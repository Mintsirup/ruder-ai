"""Search 스킬 단위 테스트.

TODO.md P3 "Search" 항목 검증: `search_symbol`/`search_reference`/
`semantic_search` 세 Tool이 각각 정상 검색/필수값 누락/인덱스 없음
케이스를 올바르게 처리하는지 확인한다. (Executor가 인덱스 객체를
자동 주입하는지는 tests/test_search_ops_injection.py에서 별도 검증.)
"""

from __future__ import annotations

import asyncio

from ruder_ai.indexer.models import Symbol
from ruder_ai.indexer.reference_index import ReferenceIndex
from ruder_ai.indexer.semantic_file_index import SemanticFileIndex
from ruder_ai.skills.search_ops import (
    SearchReferenceSkill,
    SearchSymbolSkill,
    SemanticSearchSkill,
)


def run(coro):
    return asyncio.run(coro)


def _project_index_stub():
    """`ProjectIndex.symbols`만 채운 최소한의 스텁 객체."""

    class Stub:
        pass

    stub = Stub()
    stub.symbols = [
        Symbol(name="PlayerManager", kind="class", file="src/PlayerManager.java", line=10),
        Symbol(name="PlayerHealth", kind="field", file="src/PlayerManager.java", line=25),
        Symbol(name="TeamManager", kind="class", file="src/TeamManager.java", line=5),
    ]
    return stub


# ---------------------------------------------------------------------------
# SearchSymbolSkill
# ---------------------------------------------------------------------------


def test_search_symbol_partial_match_and_ranking():
    skill = SearchSymbolSkill()
    project_index = _project_index_stub()

    result = run(
        skill.execute(name="Manager", project_index=project_index)
    )

    assert result["status"] == "success"
    assert result["count"] == 2
    names = [m["name"] for m in result["matches"]]
    assert "PlayerManager" in names
    assert "TeamManager" in names


def test_search_symbol_filters_by_kind():
    skill = SearchSymbolSkill()
    project_index = _project_index_stub()

    result = run(
        skill.execute(name="Player", kind="field", project_index=project_index)
    )

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["matches"][0]["name"] == "PlayerHealth"


def test_search_symbol_requires_name():
    skill = SearchSymbolSkill()

    result = run(skill.execute(project_index=_project_index_stub()))

    assert result["status"] == "error"


def test_search_symbol_missing_index_errors():
    skill = SearchSymbolSkill()

    result = run(skill.execute(name="Player", project_index=None))

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# SearchReferenceSkill
# ---------------------------------------------------------------------------


def test_search_reference_finds_usages():
    skill = SearchReferenceSkill()

    index = ReferenceIndex()
    index.references["PlayerManager"] = [
        ("src/Main.java", 12),
        ("src/GameCommand.java", 40),
    ]

    result = run(skill.execute(symbol="PlayerManager", reference_index=index))

    assert result["status"] == "success"
    assert result["count"] == 2
    assert {"file": "src/Main.java", "line": 12} in result["references"]


def test_search_reference_no_matches_reports_empty():
    skill = SearchReferenceSkill()
    index = ReferenceIndex()

    result = run(skill.execute(symbol="Nothing", reference_index=index))

    assert result["status"] == "success"
    assert result["count"] == 0
    assert result["references"] == []


def test_search_reference_requires_symbol():
    skill = SearchReferenceSkill()

    result = run(skill.execute(reference_index=ReferenceIndex()))

    assert result["status"] == "error"


def test_search_reference_missing_index_errors():
    skill = SearchReferenceSkill()

    result = run(skill.execute(symbol="PlayerManager", reference_index=None))

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# SemanticSearchSkill
# ---------------------------------------------------------------------------


def test_semantic_search_finds_relevant_file():
    skill = SemanticSearchSkill()

    index = SemanticFileIndex()
    index.file_tokens = {
        "src/PlayerManager.java": {"player", "health"},
        "src/UnrelatedThing.java": {"unrelated", "config"},
    }
    index.index = {
        "player": {"src/PlayerManager.java"},
        "health": {"src/PlayerManager.java"},
        "unrelated": {"src/UnrelatedThing.java"},
        "config": {"src/UnrelatedThing.java"},
    }

    result = run(skill.execute(query="player health", semantic_file_index=index))

    assert result["status"] == "success"
    assert result["count"] >= 1
    assert result["results"][0]["file"] == "src/PlayerManager.java"


def test_semantic_search_no_match_reports_empty():
    skill = SemanticSearchSkill()
    index = SemanticFileIndex()  # 완전히 비어있음

    result = run(skill.execute(query="zzz_no_such_token_zzz", semantic_file_index=index))

    assert result["status"] == "success"
    assert result["count"] == 0
    assert result["results"] == []


def test_semantic_search_requires_query():
    skill = SemanticSearchSkill()

    result = run(skill.execute(semantic_file_index=SemanticFileIndex()))

    assert result["status"] == "error"


def test_semantic_search_missing_index_errors():
    skill = SemanticSearchSkill()

    result = run(skill.execute(query="player", semantic_file_index=None))

    assert result["status"] == "error"
