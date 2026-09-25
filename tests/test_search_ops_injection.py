"""Search 계열 Skill의 인덱스 자동 주입 단위 테스트.

TODO.md P3 "Search" 항목 검증:
- `ToolExecutor._execute_tool`이 `project_index`/`reference_index`/
  `semantic_file_index`를 `workspace_path`와 같은 방식으로
  `self.agent`에서 자동 주입하는지.
- kwargs로 이미 값을 넘긴 경우, 자동 주입이 그 값을 덮어쓰지 않는지
  (kwargs가 우선하는지).
"""

from __future__ import annotations

import asyncio
from typing import Any

from ruder_ai.core.executor import ToolExecutor


class FakeSkillRegistry:
    def __init__(self, skills: dict):
        self._skills = skills

    def get_skill(self, name):
        return self._skills.get(name)

    def list_skills(self):
        return {name: s.description for name, s in self._skills.items()}


class FakeLLM:
    async def chat(self, messages):
        raise AssertionError("이 테스트에서는 LLM이 호출되지 않아야 합니다.")


class RecordingIndexSkill:
    """전달받은 kwargs를 그대로 기록하는 가짜 Search류 Tool."""

    description = "인덱스 주입 확인용 가짜 Tool (테스트 전용)"

    def __init__(self):
        self.received: dict[str, Any] | None = None

    async def execute(
        self,
        project_index: Any = None,
        reference_index: Any = None,
        semantic_file_index: Any = None,
        query: str | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        self.received = {
            "project_index": project_index,
            "reference_index": reference_index,
            "semantic_file_index": semantic_file_index,
            "query": query,
        }
        return {"status": "success"}


class FakeAgent:
    def __init__(self, project_index, reference_index, semantic_file_index):
        self.project_index = project_index
        self.reference_index = reference_index
        self.semantic_file_index = semantic_file_index


def run(coro):
    return asyncio.run(coro)


def test_executor_injects_indexes_from_agent():
    skill = RecordingIndexSkill()
    registry = FakeSkillRegistry({"search_tool": skill})

    sentinel_project_index = object()
    sentinel_reference_index = object()
    sentinel_semantic_index = object()

    agent = FakeAgent(
        project_index=sentinel_project_index,
        reference_index=sentinel_reference_index,
        semantic_file_index=sentinel_semantic_index,
    )

    executor = ToolExecutor(
        llm=FakeLLM(),
        skill_registry=registry,
        workspace_path=".",
        agent=agent,
        tool_retries=0,
    )

    result = run(executor._execute_tool("search_tool", {"query": "hello"}))

    assert result["status"] == "success"
    assert skill.received["project_index"] is sentinel_project_index
    assert skill.received["reference_index"] is sentinel_reference_index
    assert skill.received["semantic_file_index"] is sentinel_semantic_index
    assert skill.received["query"] == "hello"


def test_kwargs_value_takes_priority_over_auto_injection():
    skill = RecordingIndexSkill()
    registry = FakeSkillRegistry({"search_tool": skill})

    sentinel_project_index = object()
    explicit_project_index = object()

    agent = FakeAgent(
        project_index=sentinel_project_index,
        reference_index=None,
        semantic_file_index=None,
    )

    executor = ToolExecutor(
        llm=FakeLLM(),
        skill_registry=registry,
        workspace_path=".",
        agent=agent,
        tool_retries=0,
    )

    result = run(
        executor._execute_tool(
            "search_tool",
            {"project_index": explicit_project_index},
        )
    )

    assert result["status"] == "success"
    # kwargs로 이미 넘긴 값은 자동 주입으로 덮어써지지 않아야 한다.
    assert skill.received["project_index"] is explicit_project_index
    assert skill.received["project_index"] is not sentinel_project_index
