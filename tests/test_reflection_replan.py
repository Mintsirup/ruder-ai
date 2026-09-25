"""Reflection 실패 -> Planner 재계획 통합 테스트.

TODO.md P2 "Reflection > Reflection 결과를 Planner 입력으로 사용" 항목
검증: `ToolExecutor.run`이 Reflection(자기 검증) 실패 시 Auto Verify
실패와 동일한 경로로 `GoalPlanner.replan(prompt, previous_plan,
failure_log, context_summary)`을 호출해 새 Plan을 받고, 그 Plan을 다시
`_run_plan`으로 실행하는지 확인한다.

시나리오:
1. 검증 실패 -> replan -> 재실행 -> 검증 통과까지 정상 종료.
2. 재계획을 거듭해도 계속 실패하면 max_reflections 한도에서 무한루프
   없이 종료.

기존 `tests/test_reflection.py`(goal_planner=None 경로)의 하위 호환은
그대로 유지된다 — 여기서는 goal_planner가 있는 새 경로만 검증한다.
"""

from __future__ import annotations

import asyncio

from ruder_ai.core.executor import ToolExecutor
from ruder_ai.core.goal_planner import GoalPlanner
from ruder_ai.core.reflection import Reflector, ReflectionResult


class FakeLLM:
    """호출 순서대로 미리 정해둔 응답을 하나씩 돌려주는 가짜 LLM."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        if not self._responses:
            raise AssertionError(
                f"예상보다 많은 LLM 호출이 발생했습니다 (call #{self.calls})"
            )
        return self._responses.pop(0)


class FakeWriteSkill:
    name = "write_file"
    description = "가짜 write_file 스킬 (테스트 전용)"

    async def execute(self, **kwargs):
        return {"status": "success", "message": "written"}


class FakeSkillRegistry:
    def __init__(self, skills: dict):
        self._skills = skills

    def get_skill(self, name):
        return self._skills.get(name)

    def list_skills(self):
        return {name: s.description for name, s in self._skills.items()}


class ScriptedReflector:
    """실제 LLM 판정 대신 미리 정해둔 판정을 순서대로 반환하는 가짜 Reflector."""

    def __init__(self, verdicts: list[ReflectionResult], max_reflections: int):
        self._verdicts = list(verdicts)
        self.max_reflections = max_reflections
        self.calls = 0

    async def review(self, task, final_answer, changed_files):
        self.calls += 1
        if not self._verdicts:
            raise AssertionError(
                f"예상보다 많은 review() 호출이 발생했습니다 (call #{self.calls})"
            )
        return self._verdicts.pop(0)


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


def run(coro):
    return asyncio.run(coro)


def test_reflection_failure_triggers_replan_then_passes():
    registry = FakeSkillRegistry({"write_file": FakeWriteSkill()})

    llm = FakeLLM(
        [
            _plan_json("write_file", "파일을 작성한다"),
            _tool_call_json("write_file"),  # 1차 실행
            "1차 답변",  # _finalize -> reflection에서 ISSUE 판정
            _plan_json("write_file", "피드백을 반영해 다시 작성한다"),
            _tool_call_json("write_file"),  # replan된 2차 실행
            "2차 답변",  # _finalize -> reflection에서 OK 판정
        ]
    )

    goal_planner = GoalPlanner(llm=llm, skill_registry=registry)

    reflector = ScriptedReflector(
        verdicts=[
            ReflectionResult(passed=False, feedback="ISSUE: 여전히 부족합니다."),
            ReflectionResult(passed=True, feedback="OK"),
        ],
        max_reflections=2,
    )

    executor = ToolExecutor(
        llm=llm,
        skill_registry=registry,
        workspace_path=".",
        goal_planner=goal_planner,
        reflector=reflector,
        max_replans=2,
    )

    async def scenario():
        plan = await goal_planner.plan(prompt="작업해줘")
        messages = [{"role": "user", "content": "작업해줘"}]
        return await executor.run(messages, task="작업해줘", plan=plan)

    response = run(scenario())

    assert response == "2차 답변"
    assert reflector.calls == 2


def test_reflection_replan_gives_up_after_max_reflections():
    registry = FakeSkillRegistry({"write_file": FakeWriteSkill()})

    llm = FakeLLM(
        [
            _plan_json("write_file", "파일을 작성한다"),
            _tool_call_json("write_file"),  # 1차 실행
            "1차 답변",  # reflection ISSUE -> replan
            _plan_json("write_file", "재시도"),
            _tool_call_json("write_file"),  # replan된 2차 실행
            "2차 답변",  # max_reflections(1) 소진 -> 이 답변이 그대로 반환됨
        ]
    )

    goal_planner = GoalPlanner(llm=llm, skill_registry=registry)

    reflector = ScriptedReflector(
        verdicts=[
            ReflectionResult(passed=False, feedback="ISSUE: 여전히 부족합니다."),
        ],
        max_reflections=1,
    )

    executor = ToolExecutor(
        llm=llm,
        skill_registry=registry,
        workspace_path=".",
        goal_planner=goal_planner,
        reflector=reflector,
        max_replans=2,
    )

    async def scenario():
        plan = await goal_planner.plan(prompt="작업해줘")
        messages = [{"role": "user", "content": "작업해줘"}]
        return await executor.run(messages, task="작업해줘", plan=plan)

    response = run(scenario())

    # max_reflections(1)을 소진하면 재검증 없이 마지막 응답을 그대로
    # 반환하고, 무한루프 없이 종료해야 한다.
    assert response == "2차 답변"
    assert reflector.calls == 1
