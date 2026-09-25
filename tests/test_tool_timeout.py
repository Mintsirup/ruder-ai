"""Tool Timeout 단위 테스트.

TODO.md P2 "Executor > Tool Timeout" 항목 검증:
`core/executor.py::ToolExecutor._execute_tool`이 awaitable Tool 실행을
`asyncio.wait_for`로 감싸서 제한 시간을 넘기면 `status: error,
error_type: timeout`으로 끊어내는지, `verify_project`처럼 무제한
override가 있는 Tool은 실제로 잘리지 않는지, 생성자로 받은 override가
반영되는지, Timeout이 기존 Retry Loop(GoalPlanner.replan)로 자연스럽게
이어지는지를 확인한다.

Tool Retry 기본값과 섞이지 않도록 아래 테스트들은 모두 tool_retries=0을
명시한다 (retry는 tests/test_tool_retry.py에서 별도로 검증).
"""

from __future__ import annotations

import asyncio
import time

from ruder_ai.core.executor import ToolExecutor
from ruder_ai.core.goal_planner import GoalPlanner


class SlowSkill:
    """지정한 시간만큼 대기한 뒤 성공을 반환하는 가짜 Tool."""

    description = "느린 가짜 Tool (테스트 전용)"

    def __init__(self, name: str, delay: float):
        self.name = name
        self.delay = delay
        self.call_count = 0

    async def execute(self, **kwargs):
        self.call_count += 1
        await asyncio.sleep(self.delay)
        return {"status": "success", "message": f"{self.delay}초 대기 후 완료"}


class FakeSkillRegistry:
    def __init__(self, skills: dict):
        self._skills = skills

    def get_skill(self, name):
        return self._skills.get(name)

    def list_skills(self):
        return {name: s.description for name, s in self._skills.items()}


class FakeLLM:
    def __init__(self, responses: list[str] | None = None):
        self._responses = list(responses or [])
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        if not self._responses:
            raise AssertionError(
                f"예상보다 많은 LLM 호출이 발생했습니다 (call #{self.calls})"
            )
        return self._responses.pop(0)


def run(coro):
    return asyncio.run(coro)


def test_slow_tool_times_out():
    skill = SlowSkill("slow_tool", delay=1.0)
    registry = FakeSkillRegistry({"slow_tool": skill})

    executor = ToolExecutor(
        llm=FakeLLM(),
        skill_registry=registry,
        workspace_path=".",
        tool_timeout=0.1,
        tool_retries=0,
    )

    start = time.monotonic()
    result = run(executor._execute_tool("slow_tool", {}))
    elapsed = time.monotonic() - start

    assert result["status"] == "error"
    assert result["error_type"] == "timeout"
    # 1초를 다 기다리지 않고 timeout(0.1초) 근처에서 끊겨야 한다.
    assert elapsed < 0.5


def test_fast_tool_not_affected_by_timeout():
    skill = SlowSkill("fast_tool", delay=0.0)
    registry = FakeSkillRegistry({"fast_tool": skill})

    executor = ToolExecutor(
        llm=FakeLLM(),
        skill_registry=registry,
        workspace_path=".",
        tool_timeout=0.1,
        tool_retries=0,
    )

    result = run(executor._execute_tool("fast_tool", {}))

    assert result["status"] == "success"


def test_verify_project_override_is_unlimited_by_default():
    # 기본 tool_timeout을 아주 짧게 잡아도, verify_project는 클래스 기본
    # TOOL_TIMEOUT_OVERRIDES에 의해 무제한(None)이라 잘리지 않아야 한다.
    skill = SlowSkill("verify_project", delay=0.2)
    registry = FakeSkillRegistry({"verify_project": skill})

    executor = ToolExecutor(
        llm=FakeLLM(),
        skill_registry=registry,
        workspace_path=".",
        tool_timeout=0.05,
        tool_retries=0,
    )

    result = run(executor._execute_tool("verify_project", {}))

    assert result["status"] == "success"


def test_constructor_timeout_override_applies():
    skill = SlowSkill("custom_slow", delay=1.0)
    registry = FakeSkillRegistry({"custom_slow": skill})

    executor = ToolExecutor(
        llm=FakeLLM(),
        skill_registry=registry,
        workspace_path=".",
        tool_timeout=10.0,  # 기본은 넉넉하지만
        tool_timeout_overrides={"custom_slow": 0.05},  # 이 Tool만 짧게
        tool_retries=0,
    )

    start = time.monotonic()
    result = run(executor._execute_tool("custom_slow", {}))
    elapsed = time.monotonic() - start

    assert result["status"] == "error"
    assert result["error_type"] == "timeout"
    assert elapsed < 0.5


def _plan_json(tool: str, description: str, goal: str = "작업을 완료한다") -> str:
    return (
        "```json\n"
        "{\n"
        f'  "goal": "{goal}",\n'
        '  "tasks": [\n'
        f'    {{"description": "{description}", "tool": "{tool}"}}\n'
        "  ]\n"
        "}\n"
        "```"
    )


def _tool_call_json(tool: str) -> str:
    return (
        "```json\n"
        "{\n"
        f'  "tool": "{tool}",\n'
        '  "kwargs": {}\n'
        "}\n"
        "```"
    )


def test_timeout_leads_naturally_into_replan():
    """Timeout으로 인한 status=error도 기존 Retry Loop(replan)로 자연스럽게

    이어져야 한다 — 별도의 특별 처리 없이 기존 실패 판정
    (`_is_tool_result_success`)을 그대로 재사용하기 때문.
    """

    slow = SlowSkill("slow_task", delay=1.0)
    fast = SlowSkill("fast_task", delay=0.0)
    registry = FakeSkillRegistry({"slow_task": slow, "fast_task": fast})

    llm = FakeLLM(
        [
            _plan_json("slow_task", "느린 작업을 실행한다"),
            _tool_call_json("slow_task"),  # timeout 발생
            _plan_json("fast_task", "재계획: 빠른 작업으로 대체한다"),
            _tool_call_json("fast_task"),
            "작업이 완료되었습니다.",
        ]
    )

    goal_planner = GoalPlanner(llm=llm, skill_registry=registry)

    executor = ToolExecutor(
        llm=llm,
        skill_registry=registry,
        workspace_path=".",
        goal_planner=goal_planner,
        max_replans=2,
        reflector=None,
        tool_timeout=0.1,
        tool_retries=0,
    )

    async def scenario():
        plan = await goal_planner.plan(prompt="작업해줘")
        messages = [{"role": "user", "content": "작업해줘"}]
        return await executor.run(messages, task="작업해줘", plan=plan)

    response = run(scenario())

    assert slow.call_count == 1
    assert fast.call_count == 1
    assert not response.startswith("⚠️ 계획 실행 중 오류")
    assert response == "작업이 완료되었습니다."
