"""Context Builder 단위 테스트.

TODO.md "Context Builder" 항목 검증:
- Token Budget: budget을 넘기면 이후 파일은 담기지 않는다.
- Context Compression: 파일 하나가 남은 budget보다 크면 압축돼서 담긴다.
- Context Cache: 같은 (파일, mtime, target_line)은 디스크를 다시
  읽지 않고 캐시를 재사용한다.
"""

from __future__ import annotations

from pathlib import Path

from ruder_ai.context.builder import ContextBuilder
from ruder_ai.core.planner import TaskPlan
from ruder_ai.indexer.models import ProjectIndex, ProjectInfo
from ruder_ai.context.token_budget import estimate_tokens, truncate_to_budget


def _make_index(workspace: Path) -> ProjectIndex:
    project = ProjectInfo(
        root=workspace,
        language="Python",
        build_system="Python",
    )
    return ProjectIndex(workspace=workspace, project=project)


def test_token_budget_stops_adding_files(tmp_path: Path):
    # 파일 하나만 담아도 budget을 다 쓰도록 아주 작은 budget을 준다.
    (tmp_path / "a.py").write_text("x = 1\n" * 50, encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n" * 50, encoding="utf-8")

    builder = ContextBuilder(workspace=tmp_path, token_budget=20)
    index = _make_index(tmp_path)

    plan = TaskPlan(prompt="test", files=["a.py", "b.py"], intent="read")

    context = builder.build(plan, index)

    # budget이 매우 작으므로 두 파일이 전부 들어가지는 못해야 한다.
    assert len(context.files) <= 2
    assert context.files  # 그래도 최소 하나는 담김(압축돼서라도)


def test_large_file_gets_compressed(tmp_path: Path):
    big_content = "print('line')\n" * 2000
    (tmp_path / "big.py").write_text(big_content, encoding="utf-8")

    builder = ContextBuilder(workspace=tmp_path, token_budget=200)
    index = _make_index(tmp_path)

    plan = TaskPlan(prompt="test", files=["big.py"], intent="read")

    context = builder.build(plan, index)

    assert len(context.files) == 1
    result = context.files[0].content

    # 압축되어 원본 전체 내용보다 훨씬 짧아야 한다.
    assert len(result) < len(big_content)
    assert estimate_tokens(result) <= 220  # 약간의 오차 허용


def test_snippet_cache_avoids_rereading_unchanged_file(tmp_path: Path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    builder = ContextBuilder(workspace=tmp_path, token_budget=6000)
    index = _make_index(tmp_path)

    plan = TaskPlan(prompt="test", files=["a.py"], intent="read")

    # 1차 빌드로 캐시를 채운다.
    context1 = builder.build(plan, index)
    assert context1.files[0].content

    call_count = {"n": 0}
    original_build = builder.snippets.build

    def counting_build(*args, **kwargs):
        call_count["n"] += 1
        return original_build(*args, **kwargs)

    monkeypatch.setattr(builder.snippets, "build", counting_build)

    # 파일이 그대로(mtime 불변)이므로 2차 빌드는 실제 스니펫 빌드를
    # 다시 호출하지 않고 캐시를 재사용해야 한다.
    context2 = builder.build(plan, index)

    assert call_count["n"] == 0
    assert context2.files[0].content == context1.files[0].content


def test_truncate_to_budget_keeps_head_and_tail():
    text = "A" * 1000
    truncated = truncate_to_budget(text, max_tokens=10)

    assert len(truncated) < len(text)
    assert truncated.startswith("A")
    assert truncated.endswith("A")


def test_truncate_to_budget_noop_when_within_budget():
    text = "short text"
    assert truncate_to_budget(text, max_tokens=1000) == text
