"""An analysis request must produce an analysis.

A real session asked "이 워크스페이스에 있는 모든 프로젝트를 분석해" and got
back "이번 작업에서는 파일 변경이 없었습니다" - a report about file changes
to someone who never asked for a change. The agent read three files and
concluded that the answer was "nothing".

Two things were broken, and neither was the typo in the prompt:

* ``wants_survey`` matched particle-free markers against text that always
  contains particles. Natural Korean puts josa between the noun and the verb -
  ``프로젝트`` + ``를`` + `` `` + ``분석해`` - so "프로젝트 분석" missed by one
  character. Even the correctly typed request was unroutable.
* A broad "what is this project?" had no deterministic answer at all, so it
  fell through the same hole.

These tests pin the routing: an understanding request about the project is
answered from the index, and a request to change something is not.
"""

from __future__ import annotations

import pytest

from ruder_ai.core.deterministic_actions import DeterministicActionResolver
from ruder_ai.core.survey import (
    build_overview,
    format_overview,
    wants_project_summary,
    wants_survey,
)
from ruder_ai.indexer.detector import ProjectDetector
from ruder_ai.indexer.scanner import ProjectScanner
from ruder_ai.indexer.symbol_indexer import SymbolIndexer


# ======================================================================
# particles
# ======================================================================

def test_particles_are_stripped_before_marker_matching():
    from ruder_ai.core.survey import _normalize

    # The noun/verb pair that used to be one character apart.
    assert "프로젝트 분석" in _normalize("이 워크스페이스에 있는 모든 프로젝트를 분석해")
    assert "프로젝트 분석" in _normalize("프로젝트를 분석해줘")
    assert "모듈 설명" in _normalize("모든 모듈을 설명해줘")


def test_a_leading_particle_is_not_stripped():
    """``이`` here is a subject marker, not part of a word."""
    from ruder_ai.core.survey import _normalize

    assert _normalize("이 워크스페이스에 있는").startswith("이 ")


def test_a_particle_inside_a_word_is_not_stripped():
    """Otherwise ``이슈`` would become ``슈`` and ``로그인`` would become ``로그``."""
    from ruder_ai.core.survey import _normalize

    assert "이슈" in _normalize("이슈 트래커")
    assert "사이" in _normalize("사이언스")
    assert "로그인" in _normalize("로그인이")
    assert "코드베이스" in _normalize("코드베이스를 분석해줘")


def test_a_trailing_syllable_is_still_stripped_but_that_is_harmless():
    """A trailing jamo is indistinguishable from a particle without a
    morphological analyser: ``하이`` normalises to ``하``. It is confined to
    survey routing, where neither spelling is a marker, and the greeting
    classifier - which *does* care - does not use this normaliser.
    """
    from ruder_ai.core.survey import _normalize

    assert _normalize("하이") == "하"
    # No marker contains that shape, so routing is unaffected.
    assert wants_survey("하이") is False
    assert wants_project_summary("하이") is False


def test_greeting_detection_does_not_use_the_particle_normaliser():
    """The normaliser is a survey-routing concern only. If it ever leaked
    into turn-intent, ``하이`` would stop being a greeting."""
    from ruder_ai.core import survey as survey_mod
    from ruder_ai.core import turn_intent as turn_intent_mod

    assert survey_mod._PARTICLE_RE is not turn_intent_mod._LATIN_GREETING_RE
    assert turn_intent_mod.classify_turn("하이").kind.value == "conversational"


# ======================================================================
# routing: survey
# ======================================================================

SURVEY_REQUESTS = [
    "이 프로젝트 일일히 분석해서 파일마다 기능 일일히 말해줘",
    "이 워크스페이스에 있는 모든 프로젝트를 분석해",
    "모든 모듈을 하나씩 설명해줘",
    "analyze every module",
    "explain every file in this codebase",
    "이 프로젝트 전체 구조 파악해줘",
    "이 코드베이스 전체를 분석해줘",
    "모든 파일이 뭘 하는지 알려줘",
]

SUMMARY_REQUESTS = [
    "이 프로젝트를 분석해줘",
    "프로젝트를 분석해줘",
    "이 프로젝트가 뭐 하는 곳이야?",
    "이 프로젝트는 어떤 구성이야?",
    "이 프로젝트는 어떤 일을 하는지 설명해줘",
    "analyze this codebase",
    "what is this project?",
    "이 저장소는 어떤 프로그램이야?",
]

#: "설명해줘" is the verb of both tiers, so on its own it must not pull a
#: broad question into a file detail.
SOFT_VERBS_ALONE = [
    "이 프로젝트 설명해줘",
    "이 코드베이스를 설명해줘",
]

ACTION_REQUESTS = [
    "이 프로젝트를 분석해서 README를 고쳐줘",
    "로그인 버그 고쳐줘",
    "이슈 트래커는 어디 있어?",
    "테스트 돌려줘",
    "전체 테스트 돌려줘",
    "인덱스를 최적화해줘",
    "how do I fix the code",
    "안녕",
    "",
]

#: "Have a look at settings.py" asks about one file, so it is answered from
#: the index rather than by a plan that changes nothing. Naming a file makes
#: it a question, not work - the executor's turn-intent gate is what stops it
#: becoming a mutation.
DETAIL_REQUESTS = [
    "settings.py 좀 봐줘",
    "executor.py 는 어떤 일을 해?",
]


@pytest.mark.parametrize("prompt", SURVEY_REQUESTS)
def test_completeness_requests_are_surveys(prompt):
    assert wants_survey(prompt) is True
    assert wants_project_summary(prompt) is False
    assert DeterministicActionResolver.classify(prompt) == "project_survey"


@pytest.mark.parametrize("prompt", SUMMARY_REQUESTS)
def test_broad_questions_are_summaries(prompt):
    assert wants_survey(prompt) is False
    assert wants_project_summary(prompt) is True
    assert DeterministicActionResolver.classify(prompt) == "project_summary"


@pytest.mark.parametrize("prompt", SOFT_VERBS_ALONE)
def test_a_soft_verb_alone_stays_a_summary(prompt):
    """Routing it to a file detail would answer a question nobody asked."""
    assert DeterministicActionResolver.classify(prompt) == "project_summary"


@pytest.mark.parametrize("prompt", ACTION_REQUESTS)
def test_work_requests_are_left_to_the_pipeline(prompt):
    assert wants_survey(prompt) is False
    assert wants_project_summary(prompt) is False
    assert DeterministicActionResolver.classify(prompt) is None


@pytest.mark.parametrize("prompt", DETAIL_REQUESTS)
def test_naming_a_file_makes_it_a_question(prompt):
    assert DeterministicActionResolver.classify(prompt) == "project_detail"


def test_survey_wins_over_summary_when_asked_for_everything():
    prompt = "이 프로젝트 전체를 분석해서 파일마다 기능 말해줘"
    assert DeterministicActionResolver.classify(prompt) == "project_survey"


def test_an_edit_request_is_never_described_instead_of_done():
    """The refusal list is what keeps "analyse it and fix it" from turning
    into a description the user did not ask for."""
    for prompt in (
        "이 프로젝트를 분석하고 고쳐줘",
        "analyze the project and fix the bug",
        "모든 파일을 분석해서 오류를 고쳐줘",
    ):
        assert DeterministicActionResolver.classify(prompt) is None, prompt


def test_a_missing_particle_does_not_disable_routing():
    """The session that motivated this had a typo; the correctly typed
    request must still route, so the fix is not a typo workaround."""
    assert (
        DeterministicActionResolver.classify(
            "이 워크스페이스에 있는 모든 프로젝트를 분석해"
        )
        == "project_survey"
    )


# ======================================================================
# routing: the summary action
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
    (tmp_path / "core" / "main.py").write_text(
        '"""CLI entry point."""\n\n\ndef main():\n    return 0\n'
        + "x = 1\n" * 40,
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_a.py").write_text('"""Tests."""\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    return tmp_path


@pytest.mark.asyncio
async def test_summary_action_reports_the_whole_shape(tree):
    resolver = DeterministicActionResolver(str(tree))
    result = await resolver.project_summary("이 프로젝트가 뭐 하는 곳이야?")

    assert result["action"] == "project_summary"
    assert result["status"] == "success"
    assert result["covered"] == result["total"] > 0
    assert result["files"] == 4
    assert result["symbols"] > 0
    assert result["roles"]["테스트"] == 1


@pytest.mark.asyncio
async def test_summary_reuses_the_injected_index(tree):
    resolver = DeterministicActionResolver(str(tree))
    index = _build_index(tree)
    resolver.agent_index = index
    result = await resolver.project_summary("프로젝트 소개해줘")
    assert result["covered"] == len(index.files)


def test_overview_names_the_entrypoint_and_the_biggest_module(tree):
    overview = build_overview(_build_index(tree))
    text = format_overview(overview)

    assert [r.path for r in overview.entrypoints] == ["core/main.py"]
    assert "`core/main.py`" in text
    assert "CLI 엔트리포인트" in text
    assert "가장 큰 모듈" in text
    # The follow-up is named, so the short answer is not a dead end.
    assert "일일히" in text


def test_overview_states_its_coverage(tree):
    overview = build_overview(_build_index(tree))
    assert f"**{overview.covered}/{overview.total}**" in format_overview(overview)
    assert overview.covered == overview.total
    assert overview.line_count > 0


def test_overview_reports_partial_coverage_honestly(tree):
    index = _build_index(tree)
    (tree / "core" / "main.py").unlink()
    overview = build_overview(index)
    text = format_overview(overview)

    assert overview.covered < overview.total
    assert "일부 판독 불가" in text


def test_overview_is_deterministic(tree):
    index = _build_index(tree)
    first = format_overview(build_overview(index))
    second = format_overview(build_overview(index))
    assert first == second


def test_overview_handles_a_project_with_no_entrypoint(tmp_path):
    (tmp_path / "a.py").write_text('"""A."""\n', encoding="utf-8")
    overview = build_overview(_build_index(tmp_path))
    assert overview.entrypoints == []
    assert "진입점" not in format_overview(overview)


def test_the_agent_formats_the_summary_header(tree):
    from ruder_ai.core.agent import AIAgent

    text = AIAgent._format_deterministic_result({
        "action": "project_summary",
        "status": "success",
        "files": 4,
        "covered": 4,
        "total": 4,
        "report": "BODY",
    })
    assert text.startswith("[project_summary] SUCCESS (4개 파일, 4/4 설명됨)")
    assert text.endswith("BODY")
