"""A 48 000-character survey is not a conversation.

A real session asked for every file, got 199 sections, and then asked
essentially the same thing again and received a byte-identical 49 000
character reply. Both requests were legitimate; the second one was a user
trying to get somewhere, and the answer scrolled past them.

Two behaviours prevent that, and both are pinned here:

* A named file, role or directory is answered on its own, from the same
  index, in a few hundred characters.
* A survey is delivered once. Asking again gives the overview and the ways
  to go narrower, unless the user explicitly asked for the full thing back.
"""

from __future__ import annotations

import pytest

from ruder_ai.core.deterministic_actions import DeterministicActionResolver
from ruder_ai.core.survey import (
    build_detail,
    dir_hint,
    file_hint,
    is_targeted_question,
    role_hint,
    wants_repeat,
)
from ruder_ai.indexer.detector import ProjectDetector
from ruder_ai.indexer.scanner import ProjectScanner
from ruder_ai.indexer.symbol_indexer import SymbolIndexer


# ======================================================================
# fixtures
# ======================================================================

def _build_index(root):
    scanner = ProjectScanner(root)
    files = scanner.scan()
    return SymbolIndexer().build(
        root, files, ProjectDetector().detect(root, files),
        dict(scanner.inverted_index),
    )


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "core" / "__init__.py").write_text('"""Core."""\n', encoding="utf-8")
    (tmp_path / "core" / "executor.py").write_text(
        '"""RudderAI Tool Executor."""\n\n\ndef run():\n    return 1\n',
        encoding="utf-8",
    )
    (tmp_path / "core" / "agent.py").write_text(
        '"""AI Agent."""\n\n\ndef start():\n    return 2\n',
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_a.py").write_text(
        '"""Tests."""\n\n\ndef test_x():\n    return 3\n', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    return tmp_path


# ======================================================================
# hints
# ======================================================================

@pytest.mark.parametrize("prompt,expected", [
    ("ruder_ai/core/executor.py 는 어떤 일을 해?", "ruder_ai/core/executor.py"),
    ("executor.py 자세히 설명해줘", "executor.py"),
    ("README.md 는 뭐 하는 파일이야?", "README.md"),
    ("back\\slash.py 를 봐줘", "back/slash.py"),
])
def test_file_hint_finds_a_named_file(prompt, expected):
    assert file_hint(prompt) == expected


@pytest.mark.parametrize("prompt", ["테스트 파일만 설명해줘", "show me the tests"])
def test_role_hint(prompt):
    assert role_hint(prompt) == "테스트"


@pytest.mark.parametrize("prompt,expected", [
    ("core 디렉터리 자세히", "core"),
    ("tests 폴더 설명해줘", "tests"),
])
def test_dir_hint(prompt, expected):
    assert dir_hint(prompt) == expected


def test_a_hint_does_not_fire_on_a_substring():
    """``cores`` must not select the ``core`` directory."""
    assert dir_hint("coresomething 를 봐줘") is None
    assert dir_hint("discourse 에 대해") is None


@pytest.mark.parametrize("prompt,expected", [
    ("전체 조사 다시 보여줘", True),
    ("다시 출력해줘", True),
    ("show the full survey again", True),
    ("각 파일마다 기능 말해줘", False),
])
def test_wants_repeat(prompt, expected):
    assert wants_repeat(prompt) is expected


# ======================================================================
# routing: a named target beats the overview
# ======================================================================

TARGETED = [
    "ruder_ai/core/executor.py 는 어떤 일을 해?",
    "executor.py 자세히 설명해줘",
    "README.md 는 뭐 하는 파일이야?",
    "테스트 파일만 설명해줘",
    "core 디렉터리 자세히",
    "없는파일.py 설명해줘",
]

NOT_TARGETED = [
    # A change request is work, not a question - even with a file named.
    "auth.py 고쳐줘",
    "runner.py 를 고쳐줘",
    "runner.py 의 함수를 정리해서 옮겨줘",
    "테스트 돌려줘",
    "안녕",
]


@pytest.mark.parametrize("prompt", TARGETED)
def test_named_targets_route_to_detail(prompt):
    assert is_targeted_question(prompt) is True
    assert DeterministicActionResolver.classify(prompt) == "project_detail"


@pytest.mark.parametrize("prompt", NOT_TARGETED)
def test_a_change_request_is_never_a_detail_question(prompt):
    assert is_targeted_question(prompt) is False
    assert DeterministicActionResolver.classify(prompt) is None


def test_a_full_survey_still_outranks_a_named_file():
    assert DeterministicActionResolver.classify(
        "이 프로젝트의 모든걸 분석해"
    ) == "project_survey"
    assert DeterministicActionResolver.classify(
        "전체 파일 설명해줘, 특히 executor.py"
    ) == "project_survey"


def test_the_overview_still_answers_an_unnamed_question():
    assert DeterministicActionResolver.classify(
        "이 프로젝트가 뭐 하는 곳이야?"
    ) == "project_summary"


# ======================================================================
# the detail itself
# ======================================================================

def test_a_named_file_answers_in_full_detail(tree):
    report, exact = build_detail(_build_index(tree), "executor.py 자세히")

    assert exact is not None
    assert exact.path == "core/executor.py"
    assert "RudderAI Tool Executor" in report
    assert "`run`" in report
    # One file, not the whole project.
    assert "test_a.py" not in report
    assert len(report) < 600


def test_a_full_relative_path_resolves(tree):
    _, exact = build_detail(_build_index(tree), "core/agent.py 를 설명해줘")
    assert exact is not None and exact.path == "core/agent.py"


def test_an_unknown_file_falls_back_to_the_overview(tree):
    report, exact = build_detail(_build_index(tree), "없는파일.py 설명해줘")
    assert exact is None
    assert "찾지 못했습니다" in report
    assert "프로젝트 개요" in report


def test_an_unknown_directory_falls_back_to_the_overview(tree):
    report, exact = build_detail(_build_index(tree), "semantic 디렉터리 자세히")
    assert exact is None
    assert "찾지 못했습니다" in report


def test_a_role_hint_lists_only_that_role(tree):
    report, exact = build_detail(_build_index(tree), "테스트 파일만 설명해줘")

    assert exact is None
    assert "test_a.py" in report
    assert "executor.py" not in report
    # The header states the denominator, so a subset never reads as complete.
    assert "전체" in report


def test_a_dir_hint_lists_only_that_directory(tree):
    report, exact = build_detail(_build_index(tree), "core 디렉터리 자세히")

    assert exact is None
    assert "core/executor.py" in report
    assert "core/agent.py" in report
    assert "test_a.py" not in report


def test_a_role_with_no_members_says_so(tree):
    report, exact = build_detail(_build_index(tree), "벤치마크만 설명해줘")
    assert exact is None
    assert "없습니다" in report


def test_detail_is_deterministic(tree):
    index = _build_index(tree)
    first, _ = build_detail(index, "executor.py 설명해줘")
    second, _ = build_detail(index, "executor.py 설명해줘")
    assert first == second


@pytest.mark.asyncio
async def test_the_detail_action_reports_what_it_matched(tree):
    resolver = DeterministicActionResolver(str(tree))
    result = await resolver.project_detail("executor.py 자세히")

    assert result["action"] == "project_detail"
    assert result["matched"] == 1
    assert "RudderAI Tool Executor" in result["report"]


@pytest.mark.asyncio
async def test_an_unmatched_detail_still_answers(tree):
    resolver = DeterministicActionResolver(str(tree))
    result = await resolver.project_detail("없는파일.py 설명해줘")

    assert result["status"] == "success"
    assert "찾지 못했습니다" in result["report"]


# ======================================================================
# the survey is delivered once
# ======================================================================

@pytest.mark.asyncio
async def test_a_second_survey_does_not_repeat_the_wall(tree):
    resolver = DeterministicActionResolver(str(tree))

    first = await resolver.project_survey("이 프로젝트의 모든걸 분석해")
    second = await resolver.project_survey("각 파일마다 어떤 일을 하는지 말해봐")

    assert "repeated" not in first
    assert second.get("repeated") is True
    assert "방금 동일한 내용으로 전달했습니다" in second["report"]
    # It points at ways to go narrower rather than re-dumping every file.
    assert "좁혀서 보기 예시" in second["report"]
    assert "### " not in second["report"]


@pytest.mark.asyncio
async def test_an_explicit_repeat_gets_the_full_survey(tree):
    resolver = DeterministicActionResolver(str(tree))
    first = await resolver.project_survey("이 프로젝트의 모든걸 분석해")
    again = await resolver.project_survey("전체 조사 다시 보여줘")

    assert "repeated" not in again
    assert again["report"] == first["report"]


@pytest.mark.asyncio
async def test_the_repeat_guard_is_per_session(tree):
    a = DeterministicActionResolver(str(tree))
    b = DeterministicActionResolver(str(tree))

    await a.project_survey("이 프로젝트의 모든걸 분석해")
    await a.project_survey("각 파일마다 설명해줘")
    fresh = await b.project_survey("이 프로젝트의 모든걸 분석해")

    assert "repeated" not in fresh


@pytest.mark.asyncio
async def test_a_detail_request_does_not_poison_the_survey(tree):
    resolver = DeterministicActionResolver(str(tree))
    await resolver.project_detail("executor.py 자세히")
    survey = await resolver.project_survey("이 프로젝트의 모든걸 분석해")

    assert "repeated" not in survey
    assert "### `core/executor.py`" in survey["report"]


# ======================================================================
# formatting
# ======================================================================

def test_the_agent_labels_a_detail_answer():
    from ruder_ai.core.agent import AIAgent

    assert "[project_detail] SUCCESS (파일 1개 상세)" in (
        AIAgent._format_deterministic_result({
            "action": "project_detail",
            "status": "success",
            "matched": 1,
            "report": "BODY",
        })
    )
    assert "[project_detail] SUCCESS\n\nBODY" in (
        AIAgent._format_deterministic_result({
            "action": "project_detail",
            "status": "success",
            "report": "BODY",
        })
    )
