"""Regression tests for the four user-visible behaviours added in v7.5.7.

Each of these exists because a real session failed in a specific way, and
each one is a behaviour a user can now depend on:

* a greeting must never reach the filesystem
  (``안녕`` produced a ``write_file`` for a ``hello_handler`` module),
* a "describe every file" request must be complete, and must say how
  complete it is (2 of 190 files were described, silently),
* the agent must be able to say which checkout it is actually running,
  because edits went to one clone while another was imported,
* the hot paths must stay measurable, so a regression has something to
  compare against.

Nothing here asserts a wall-clock number. Timing belongs to
``test_v755_performance.py`` as structural facts; this file asserts the
*decisions*, which are the part that silently breaks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ruder_ai.core import provenance as provenance_mod
from ruder_ai.core import survey as survey_mod
from ruder_ai.core.bench import Measurement, format_report
from ruder_ai.core.bench import run as bench_run
from ruder_ai.core.deterministic_actions import DeterministicActionResolver
from ruder_ai.core.executor import ToolExecutor
from ruder_ai.core.provenance import (
    Provenance,
    detect,
    detect_divergence,
    fingerprint,
    write_origin,
)
from ruder_ai.core.survey import build_survey, format_survey, wants_survey
from ruder_ai.core.turn_intent import (
    MUTATION_REFUSED_MESSAGE,
    TurnIntent,
    TurnKind,
    classify_turn,
)
from ruder_ai.indexer.detector import ProjectDetector
from ruder_ai.indexer.scanner import ProjectScanner
from ruder_ai.indexer.symbol_indexer import SymbolIndexer
from ruder_ai.skills import SkillRegistry


# ======================================================================
# fixtures
# ======================================================================

CORE_MODULE = '''\
"""Authentication helpers for the demo project."""

import json
import hmac
from .store import USERS


class AuthManager:
    def login(self, name: str) -> bool:
        return name in USERS

    def token(self, name: str) -> str:
        return hmac.new(b"k", name.encode(), "sha256").hexdigest()
'''

TEST_MODULE = '''\
"""Tests for the auth helpers."""

from .core import AuthManager


def test_login():
    assert AuthManager().login("a") is False
'''

README = "# demo\n\nA tiny project.\n"


def _write_tree(root: Path) -> Path:
    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "core" / "auth.py").write_text(CORE_MODULE, encoding="utf-8")
    (root / "core" / "__init__.py").write_text('"""Package."""\n', encoding="utf-8")
    (root / "tests" / "test_auth.py").write_text(TEST_MODULE, encoding="utf-8")
    (root / "README.md").write_text(README, encoding="utf-8")
    (root / "main.py").write_text('"""CLI entry point."""\n', encoding="utf-8")
    return root


def _build_index(root: Path):
    scanner = ProjectScanner(root)
    files = scanner.scan()
    return SymbolIndexer().build(
        root, files, ProjectDetector().detect(root, files),
        dict(scanner.inverted_index),
    )


@pytest.fixture
def tree(tmp_path):
    return _write_tree(tmp_path)


# ======================================================================
# turn intent: a greeting is not a task
# ======================================================================

CONVERSATIONAL = [
    "안녕",
    "안녕하세요",
    "하이",
    "hi",
    "Hello there!",
    "hey",
    "thanks!",
    "고마워",
    "감사합니다",
    "ㅋㅋ 재밌네",
]

QUESTIONS = [
    "이거 왜 이렇게 짜여 있어?",
    "이 프로젝트가 뭐 하는 곳이야?",
    "which module handles login?",
    "how does the scanner work?",
    "what is FileResolver used for?",
]

ACTIONS = [
    "hello.py 만들어줘",
    "executor.py 좀 고쳐줘",
    "인증을 고쳐서 다시 만들어줘",
    "Add a regression test for the login path",
    "refactor the indexer",
    "테스트 돌려줘",
    "delete the dead helper",
    "main.py",
    "runner.py 좀 봐줘",
    "Please remove unused imports in app_gui.py",
]


@pytest.mark.parametrize("prompt", CONVERSATIONAL)
def test_greetings_are_conversational(prompt):
    intent = classify_turn(prompt)
    assert intent.kind is TurnKind.CONVERSATIONAL, (prompt, intent.signal)
    assert intent.may_mutate is False


@pytest.mark.parametrize("prompt", QUESTIONS)
def test_questions_are_answerable_but_not_actionable(prompt):
    intent = classify_turn(prompt)
    assert intent.kind is TurnKind.QUESTION, (prompt, intent.signal)
    assert intent.may_mutate is False


@pytest.mark.parametrize("prompt", ACTIONS)
def test_actionable_turns_may_mutate(prompt):
    intent = classify_turn(prompt)
    assert intent.kind is TurnKind.ACTION, (prompt, intent.signal)
    assert intent.may_mutate is True


def test_empty_prompt_is_conversational():
    for value in ("", "   ", "\n\n"):
        assert classify_turn(value).kind is TurnKind.CONVERSATIONAL


def test_latin_greeting_uses_word_boundaries():
    """``hi`` must not fire inside ``this``/``which``/``him``.

    Substring matching on a two-letter word is the classic way a greeting
    filter starts rejecting real work, so the boundary is pinned here.
    """
    for prompt in (
        "this module is broken",
        "which file owns the cache?",
        "him and the settings differ",
        "thin wrapper around the client",
    ):
        intent = classify_turn(prompt)
        assert not intent.signal.startswith("greeting:"), (prompt, intent)


def test_korean_greeting_still_matches_inside_a_sentence():
    assert classify_turn("수고했어 안녕").kind is TurnKind.CONVERSATIONAL


def test_intent_reports_the_signal_that_decided_it():
    assert classify_turn("안녕").signal.startswith("greeting:")
    assert classify_turn("main.py").signal == "target"
    assert classify_turn("").signal == "empty"


def test_turn_intent_is_immutable():
    intent = TurnIntent(TurnKind.ACTION, "why", "action:test")
    with pytest.raises(Exception):
        intent.kind = TurnKind.QUESTION  # type: ignore[misc]


def test_refusal_message_names_the_way_out():
    """A refusal that does not say what to do next reads as a bug."""
    assert MUTATION_REFUSED_MESSAGE
    assert "만들어줘" in MUTATION_REFUSED_MESSAGE
    assert "고쳐줘" in MUTATION_REFUSED_MESSAGE


# ======================================================================
# executor: the guard is the authority
# ======================================================================

def _executor(workspace: Path) -> ToolExecutor:
    return ToolExecutor(
        llm=None,
        skill_registry=SkillRegistry(),
        workspace_path=str(workspace),
    )


def test_every_mutating_tool_is_guarded():
    """A tool added to the mutating set inherits the guard for free."""
    expected = {
        "write_file", "append_file", "patch_file",
        "delete_file", "move_file", "apply_patch",
    }
    assert expected <= ToolExecutor.FILE_MUTATING_TOOLS


def test_greeting_blocks_every_mutating_tool(tmp_path):
    executor = _executor(tmp_path)
    executor._current_task = "안녕"
    for tool in sorted(ToolExecutor.FILE_MUTATING_TOOLS):
        guard = executor._check_turn_intent(tool)
        assert guard is not None, tool
        assert guard["status"] == "error"
        assert guard["turn_kind"] == "conversational"


def test_question_blocks_mutation_but_not_reads(tmp_path):
    executor = _executor(tmp_path)
    executor._current_task = "이 프로젝트가 뭐 하는 곳이야?"
    assert executor._check_turn_intent("write_file")["turn_kind"] == "question"
    assert executor._check_turn_intent("read_file") is None
    assert executor._check_turn_intent("search_files") is None


def test_action_allows_mutation(tmp_path):
    executor = _executor(tmp_path)
    executor._current_task = "hello.py 만들어줘"
    assert executor._check_turn_intent("write_file") is None


@pytest.mark.asyncio
async def test_greeting_cannot_create_a_file_through_the_executor(tmp_path):
    """The end-to-end shape of the original incident."""
    executor = _executor(tmp_path)
    executor._current_task = "안녕"

    result = await executor._execute_tool(
        "write_file",
        {"file_path": str(tmp_path / "hello_handler.py"), "content": "print('hi')\n"},
    )

    assert result["status"] == "error"
    assert result["turn_kind"] == "conversational"
    assert not (tmp_path / "hello_handler.py").exists()
    assert not (tmp_path / "ruder_ai" / "core" / "hello_handler.py").exists()


@pytest.mark.asyncio
async def test_greeting_still_allows_reading_a_file(tmp_path):
    """Refusing to mutate must not make the agent deaf."""
    _write_tree(tmp_path)
    executor = _executor(tmp_path)
    executor._current_task = "안녕. 이 프로젝트 뭐 하는 곳이야?"

    result = await executor._execute_tool(
        "read_file", {"file_path": str(tmp_path / "main.py")}
    )

    assert result["status"] != "error", result
    assert "CLI entry point" in str(result.get("content", ""))


# ======================================================================
# agent: a greeting costs one LLM call
# ======================================================================

@pytest.mark.asyncio
async def test_greeting_short_circuits_the_pipeline(tree, monkeypatch):
    from ruder_ai.core.agent import AIAgent

    agent = AIAgent(workspace_path=str(tree))
    calls: list[list[dict]] = []

    async def fake_chat(messages, *args, **kwargs):
        calls.append(messages)
        return "안녕하세요! 무엇을 도와드릴까요?"

    monkeypatch.setattr(agent, "_llm_chat", fake_chat)

    answer = await agent.process_task("안녕")

    assert "무엇을 도와드릴까요" in answer
    assert len(calls) == 1, "a greeting must not enter the multi-role pipeline"
    assert [m["role"] for m in calls[0]] == ["system", "user"]
    # No index was built: nothing to plan against was needed.
    assert agent.project_index is None
    assert sorted(p.name for p in tree.rglob("*.py")) == [
        "__init__.py", "auth.py", "main.py", "test_auth.py",
    ]


@pytest.mark.asyncio
async def test_conversational_greeting_survives_a_dead_model(tree, monkeypatch):
    """A model failure on a greeting must not read as a failed task."""
    from ruder_ai.core.agent import AIAgent

    agent = AIAgent(workspace_path=str(tree))

    async def boom(messages, *args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(agent, "_llm_chat", boom)

    answer = await agent.process_task("안녕")

    assert "connection refused" in answer
    assert "무엇을 도와드릴까요" in answer


# ======================================================================
# survey: complete by construction, and honest about coverage
# ======================================================================

SURVEY_PROMPTS = [
    "이 프로젝트 일일히 분석해서 파일마다 기능 일일히 말해줘",
    "이 프로젝트 전체 구조 파악해줘",
    "모든 파일이 뭘 하는지 알려줘",
    "explain every file in this codebase",
    "이 코드베이스 전체를 분석해줘",
]

NOT_SURVEY_PROMPTS = [
    "",
    "안녕",
    "auth.py의 로그인 로직 고쳐줘",
    "이 프로젝트 구조 파악하고 README를 고쳐줘",
    "main.py 좀 봐줘",
    "fix the bug in executor.py",
]


@pytest.mark.parametrize("prompt", SURVEY_PROMPTS)
def test_wants_survey_accepts_whole_project_requests(prompt):
    assert wants_survey(prompt) is True


@pytest.mark.parametrize("prompt", NOT_SURVEY_PROMPTS)
def test_wants_survey_refuses_when_the_user_wants_a_change(prompt):
    assert wants_survey(prompt) is False


def test_survey_covers_every_indexed_file(tree):
    index = _build_index(tree)
    survey = build_survey(index)

    assert survey.covered == len(index.files)
    assert survey.covered == survey.total
    assert survey.unreadable == []
    assert {record.path for record in survey.files} == {
        file.relative_path for file in index.files
    }


def test_survey_counts_an_unreadable_file_in_the_denominator(tree):
    """Coverage is ``covered/total``; a file it could not read is not hidden."""
    index = _build_index(tree)
    victim = tree / "core" / "auth.py"
    victim.unlink()

    survey = build_survey(index)

    assert survey.unreadable == ["core/auth.py"]
    assert survey.total == len(index.files) + 1
    assert survey.covered == len(index.files)
    assert survey.covered < survey.total
    assert [r for r in survey.files if r.path == "core/auth.py"][0].summary == (
        "(파일을 읽을 수 없음)"
    )


def test_survey_summaries_come_from_the_files_own_docstrings(tree):
    survey = build_survey(_build_index(tree))
    by_path = {record.path: record for record in survey.files}

    assert by_path["core/auth.py"].summary.startswith("Authentication helpers")
    assert "AuthManager" in by_path["core/auth.py"].symbols
    assert "core" in by_path["core/auth.py"].imports or any(
        "store" in item for item in by_path["core/auth.py"].imports
    )
    assert by_path["tests/test_auth.py"].summary.startswith("Tests for the auth")
    assert "test_login" in by_path["tests/test_auth.py"].symbols


def test_survey_classifies_files_by_role(tree):
    survey = build_survey(_build_index(tree))
    by_path = {record.path: record for record in survey.files}

    assert by_path["main.py"].role == "CLI 엔트리포인트"
    assert by_path["README.md"].role == "문서"
    assert by_path["core/__init__.py"].role == "패키지 초기화"
    assert by_path["core/auth.py"].role == "핵심 실행 로직"
    assert by_path["tests/test_auth.py"].role == "테스트"


def test_a_packages_own_main_is_the_entrypoint(tmp_path):
    """``<pkg>/main.py`` is the entrypoint; it must not land in "기타"."""
    (tmp_path / "ruder_ai").mkdir()
    (tmp_path / "ruder_ai" / "__init__.py").write_text('"""Package."""\n', encoding="utf-8")
    (tmp_path / "ruder_ai" / "main.py").write_text('"""Entry."""\n', encoding="utf-8")
    # A directory that is *not* a package keeps the generic classification.
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "main.py").write_text('"""Helper."""\n', encoding="utf-8")

    by_path = {r.path: r.role for r in build_survey(_build_index(tmp_path)).files}

    assert by_path["ruder_ai/main.py"] == "CLI 엔트리포인트"
    assert by_path["scripts/main.py"] == "기타"


def test_survey_without_a_docstring_says_so(tree):
    (tree / "core" / "auth.py").write_text("def login():\n    return 1\n", encoding="utf-8")
    survey = build_survey(_build_index(tree))
    record = [r for r in survey.files if r.path == "core/auth.py"][0]
    assert record.summary == "(문서 주석 없음)"
    assert "login" in record.symbols


def test_survey_never_invents_symbols_for_unparsable_python(tree):
    (tree / "core" / "auth.py").write_text("def broken(:\n", encoding="utf-8")
    survey = build_survey(_build_index(tree))
    record = [r for r in survey.files if r.path == "core/auth.py"][0]
    # An unparsable file falls back to the index, never to a guess.
    assert record.lines == 2


def test_survey_is_deterministic(tree):
    index = _build_index(tree)
    first, second = build_survey(index), build_survey(index)
    assert [(r.path, r.role, r.summary) for r in first.files] == [
        (r.path, r.role, r.summary) for r in second.files
    ]
    # Sorted by role then path, so the report does not reshuffle between runs.
    keys = [(r.role, r.path) for r in first.files]
    assert keys == sorted(keys)


def test_format_survey_states_full_coverage(tree):
    index = _build_index(tree)
    survey = build_survey(index)
    text = format_survey(survey)
    assert f"**{len(index.files)}/{len(index.files)}**" in text
    assert "100%" in text
    for record in survey.files:
        assert record.path in text


def test_format_survey_admits_partial_coverage(tree):
    index = _build_index(tree)
    (tree / "core" / "auth.py").unlink()
    text = format_survey(build_survey(index))

    assert "일부 판독 불가" in text
    assert "읽을 수 없어 설명을 생략한 파일" in text
    assert "core/auth.py" in text


def test_format_survey_can_skip_role_grouping(tree):
    text = format_survey(build_survey(_build_index(tree)), group_by_role=False)
    assert "## 테스트" not in text
    assert "### `core/auth.py`" in text


def test_root_documents_and_config_are_recognised(tmp_path):
    """The extension lookup carries its dot; without it these were "기타"."""
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")

    by_path = {r.path: r.role for r in build_survey(_build_index(tmp_path)).files}

    assert by_path["README.md"] == "문서"
    assert by_path["pyproject.toml"] == "프로젝트 설정"
    assert by_path["config.yaml"] == "프로젝트 설정"
    assert by_path["LICENSE"] == "루트 파일"
    assert by_path[".gitignore"] == "프로젝트 설정"


def test_a_dotless_name_is_not_treated_as_an_extension(tmp_path):
    """``rpartition`` puts the whole name last when there is no dot."""
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n", encoding="utf-8")
    survey = build_survey(_build_index(tmp_path))
    record = [r for r in survey.files if r.path == "Makefile"][0]
    assert record.role == "루트 파일"
    assert record.extension == ""


# ======================================================================
# deterministic action routing
# ======================================================================

def test_survey_wins_over_the_legacy_marker_filter():
    """'기능' used to be an action marker and stole the survey request."""
    prompt = "이 프로젝트 일일히 분석해서 파일마다 기능 일일히 말해줘"
    assert "기능" in prompt
    assert DeterministicActionResolver.classify(prompt) == "project_survey"


def test_a_change_request_still_reaches_the_pipeline():
    assert DeterministicActionResolver.classify(
        "이 프로젝트 전체 구조 파악하고 README를 고쳐줘"
    ) is None


@pytest.mark.asyncio
async def test_survey_action_reports_coverage(tree):
    actions = DeterministicActionResolver(str(tree))
    result = await actions.project_survey("이 프로젝트 일일히 분석해서 말해줘")

    assert result["action"] == "project_survey"
    assert result["status"] == "success"
    assert result["covered"] == result["total"] > 0
    assert result["unreadable"] == []
    assert result["roles"]
    assert "### `" in result["report"]


@pytest.mark.asyncio
async def test_survey_action_reuses_the_injected_index(tree):
    """Handing the index over keeps the answer to a single scan."""
    actions = DeterministicActionResolver(str(tree))
    index = _build_index(tree)
    actions.agent_index = index

    result = await actions.project_survey("모든 파일 설명해줘")

    assert result["covered"] == len(index.files)


@pytest.mark.asyncio
async def test_survey_action_falls_back_to_scanning_without_an_index(tree):
    actions = DeterministicActionResolver(str(tree))
    assert actions.agent_index is None
    result = await actions.project_survey("모든 파일 설명해줘")
    assert result["covered"] == result["total"] > 0


# ======================================================================
# provenance: which checkout is actually running
# ======================================================================

def _fake_checkout(root: Path, version: str = "9.9.9", body: str = "a") -> Path:
    (root / "ruder_ai" / "core").mkdir(parents=True, exist_ok=True)
    (root / "ruder_ai" / "tui").mkdir(parents=True, exist_ok=True)
    (root / "ruder_ai" / "indexer").mkdir(parents=True, exist_ok=True)
    (root / "VERSION").write_text(version, encoding="utf-8")
    for relative, text in (
        ("ruder_ai/main.py", body),
        ("ruder_ai/core/executor.py", body),
        ("ruder_ai/core/agent.py", body),
        ("ruder_ai/indexer/scanner.py", body),
        ("ruder_ai/tui/app_gui.py", body),
    ):
        (root / relative).write_text(text, encoding="utf-8")
    return root


def test_detect_reports_the_live_import_root():
    info = detect()
    assert info.import_root.endswith("ruder_ai")
    assert Path(info.checkout) == Path(info.import_root).parent
    assert Path(info.cwd) == Path.cwd()
    assert info.python
    assert info.version


def test_detect_prefers_the_VERSION_file_over_the_package_version():
    """The version a user would quote is the VERSION file, not 0.1.0."""
    info = detect()
    version_file = Path(info.checkout) / "VERSION"
    if version_file.is_file():
        assert info.version == version_file.read_text(encoding="utf-8").strip()
    else:  # pragma: no cover - only when installed without the checkout root
        assert info.version


def test_describe_warns_only_on_a_split_checkout(tmp_path):
    checkout = _fake_checkout(tmp_path / "checkout")
    same = Provenance(
        import_root=str(checkout / "ruder_ai"),
        checkout=str(checkout),
        cwd=str(checkout),
        workspace=str(checkout),
        version="9.9.9",
        python="python",
    )
    other = Provenance(
        import_root=str(checkout / "ruder_ai"),
        checkout=str(checkout),
        cwd=str(tmp_path / "elsewhere"),
        workspace=str(checkout),
        version="9.9.9",
        python="python",
    )

    assert same.edited_and_running_match is True
    assert "⚠️" not in same.describe()
    assert "다른" in other.describe() or "다릅니다" in other.describe()
    assert other.edited_and_running_match is False
    assert "실행 위치" in other.describe()


def test_fingerprint_is_content_addressed(tmp_path):
    left = _fake_checkout(tmp_path / "left", body="one")
    right = _fake_checkout(tmp_path / "right", body="one")
    drifted = _fake_checkout(tmp_path / "drifted", body="two")

    assert fingerprint(left) == fingerprint(right)
    assert set(fingerprint(left)) == set(provenance_mod._FINGERPRINT_FILES)
    differing = sorted(
        name
        for name in fingerprint(left)
        if fingerprint(left)[name] != fingerprint(drifted)[name]
    )
    assert "ruder_ai/core/agent.py" in differing


def test_fingerprint_ignores_a_checkout_without_fingerprint_files(tmp_path):
    (tmp_path / "empty").mkdir()
    assert fingerprint(tmp_path / "empty") == {}


# ----------------------------------------------------------------------
# divergence
# ----------------------------------------------------------------------

@pytest.fixture
def two_checkouts(tmp_path, monkeypatch):
    running = _fake_checkout(tmp_path / "running", version="7.5.7", body="running")
    other = _fake_checkout(tmp_path / "other", version="7.5.2", body="other")
    cache = tmp_path / "provenance.json"
    monkeypatch.setattr(provenance_mod, "PROVENANCE_CACHE", cache)

    def pretend(workspace=None):
        return Provenance(
            import_root=str(running / "ruder_ai"),
            checkout=str(running),
            cwd=str(running if workspace is None else workspace),
            workspace=str(running),
            version="7.5.7",
            python="python",
        )

    monkeypatch.setattr(provenance_mod, "detect", pretend)
    return running, other, cache


def test_first_look_registers_the_checkout_without_warning(two_checkouts):
    running, _, cache = two_checkouts
    assert detect_divergence() == []
    stored = json.loads(cache.read_text(encoding="utf-8"))
    assert str(running) in stored["checkouts"]
    assert stored["checkouts"][str(running)]["version"] == "7.5.7"


def test_identical_clone_does_not_warn(two_checkouts):
    running, _, cache = two_checkouts
    detect_divergence()
    twin = _fake_checkout(
        running.parent / "twin", version="7.5.7", body="running"
    )
    stored = json.loads(cache.read_text(encoding="utf-8"))
    stored["checkouts"][str(twin)] = {
        "fingerprint": fingerprint(twin),
        "version": "7.5.7",
    }
    cache.write_text(json.dumps(stored), encoding="utf-8")

    assert detect_divergence() == []


def test_divergent_clone_warns_and_names_the_files(two_checkouts):
    running, other, cache = two_checkouts
    stored = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {
        "checkouts": {}
    }
    stored["checkouts"][str(other)] = {
        "fingerprint": fingerprint(other),
        "version": "7.5.2",
    }
    cache.write_text(json.dumps(stored), encoding="utf-8")

    warnings = detect_divergence()

    assert len(warnings) == 1
    assert "다른 RUDER-AI 복사본의 코드가 다릅니다" in warnings[0]
    assert "ruder_ai/core/agent.py" in warnings[0]


def test_editing_the_wrong_checkout_is_called_out_explicitly(two_checkouts):
    """The exact failure: edits land somewhere the agent never imports."""
    running, other, cache = two_checkouts
    other.mkdir(parents=True, exist_ok=True)
    stored = {"checkouts": {str(other): {
        "fingerprint": fingerprint(other), "version": "7.5.2",
    }}}
    cache.write_text(json.dumps(stored), encoding="utf-8")

    provenance_mod.detect = lambda workspace=None: Provenance(
        import_root=str(running / "ruder_ai"),
        checkout=str(running),
        cwd=str(other),
        workspace=str(running),
        version="7.5.7",
        python="python",
    )
    try:
        warnings = detect_divergence()
    finally:
        pass

    assert len(warnings) == 1
    assert "현재 작업 디렉터리" in warnings[0]
    assert "반영되지 않습니다" in warnings[0]
    assert "실행 중" in warnings[0]


def test_a_vanished_clone_is_pruned_silently(two_checkouts):
    running, _, cache = two_checkouts
    detect_divergence()
    stored = json.loads(cache.read_text(encoding="utf-8"))
    stored["checkouts"][str(running.parent / "deleted")] = {
        "fingerprint": {"VERSION": "0" * 16},
        "version": "0.0.0",
    }
    cache.write_text(json.dumps(stored), encoding="utf-8")

    assert detect_divergence() == []
    after = json.loads(cache.read_text(encoding="utf-8"))
    assert "deleted" not in after["checkouts"]


def test_a_corrupt_cache_is_ignored_not_fatal(two_checkouts):
    _, _, cache = two_checkouts
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{not json", encoding="utf-8")
    assert detect_divergence() == []


def test_write_origin_records_the_checkout(tmp_path, monkeypatch):
    running = _fake_checkout(tmp_path / "running")
    monkeypatch.setattr(
        provenance_mod,
        "detect",
        lambda workspace=None: Provenance(
            import_root=str(running / "ruder_ai"),
            checkout=str(running),
            cwd=str(running),
            workspace=str(running),
            version="7.5.7",
            python="python",
        ),
    )
    write_origin()
    recorded = json.loads(
        (running / provenance_mod.ORIGIN_FILENAME).read_text(encoding="utf-8")
    )
    assert recorded["checkout"] == str(running)
    assert recorded["version"] == "7.5.7"


# ======================================================================
# bench: the hot paths stay measurable
# ======================================================================

def test_bench_measures_every_hot_path(tree):
    result = bench_run(tree, reps=1)

    names = [m.name for m in result["measurements"]]
    for expected in (
        "ProjectScanner.scan",
        "index build (cold cache)",
        "index build (warm cache)",
        "TaskPlanner.plan",
        "ContextBuilder.build",
        "ReferenceIndex.build",
        "refresh_file (1 edit)",
    ):
        assert expected in names, names

    for measurement in result["measurements"]:
        assert isinstance(measurement, Measurement)
        assert measurement.best_ms >= 0.0
        assert measurement.median_ms >= 0.0
        assert measurement.best_ms <= measurement.median_ms + 1e-6

    facts = result["facts"]
    assert facts["files"] > 0
    assert facts["symbols"] > 0
    assert facts["index_survived_refresh"] is True
    assert facts["edit_refresh"].endswith(".py")


def test_bench_leaves_the_caller_tree_untouched(tree):
    before = {p: p.read_bytes() for p in tree.rglob("*.py")}
    bench_run(tree, reps=1)
    after = {p: p.read_bytes() for p in tree.rglob("*.py")}
    assert before == after


def test_bench_rejects_a_non_project_directory(tmp_path):
    with pytest.raises(ValueError):
        bench_run(tmp_path, reps=1)


def test_bench_says_so_when_there_is_nothing_to_edit(tmp_path):
    """A skipped stage must be visible, not a silently missing number."""
    (tmp_path / "ruder_ai").mkdir()
    (tmp_path / "ruder_ai" / "__init__.py").unlink(missing_ok=True)
    result = bench_run(tmp_path, reps=1)
    report = format_report(result)
    assert result["facts"]["edit_refresh"] is None
    assert "건너뜁니다" in report
    assert "refresh_file (1 edit)" not in report


def test_bench_report_states_the_project_and_a_total(tree):
    report = format_report(bench_run(tree, reps=1))
    assert "RUDER-AI 성능 측정" in report
    assert "프로젝트:" in report
    assert "합계 (best)" in report
    assert "refresh_file 후 인덱스 유지: True" in report


def test_measurement_line_pads_to_the_widest_name():
    m = Measurement("scan", 1.5, 2.5)
    assert m.line(12) == "scan          best=    1.50 ms   median=    2.50 ms"


# ======================================================================
# CLI surface
# ======================================================================

def test_the_new_commands_are_registered():
    from typer.testing import CliRunner

    from ruder_ai.main import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for command in ("bench", "where", "survey", "start", "gui"):
        assert command in result.output
