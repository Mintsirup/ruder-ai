"""동일 계획 반복 감지 테스트.

TODO.md P0 "동일 계획 반복 감지" 항목 검증:
Replan해도 이전에 실패한 Plan과 Tool sequence가 완전히 동일하면,
`ToolExecutor._run_plan`이 그 Plan을 다시 실행하지 않고(=LLM에게 또
기회를 주지 않고) 코드가 즉시 실패로 종료(deterministic recovery)하는지
`ToolExecutor._is_repeated_tool_sequence` 단위 검증과, GoalPlanner +
ToolExecutor를 함께 돌리는 통합 시나리오로 확인한다.
"""

from __future__ import annotations

import asyncio

from ruder_ai.core.executor import ToolExecutor
from ruder_ai.core.goal_planner import GoalPlanner


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


class AlwaysFailVerifySkill:
    """항상 동일한 이유로 실패하는 가짜 verify_project 스킬."""

    name = "verify_project"
    description = "가짜 검증 스킬 (테스트 전용)"

    def __init__(self):
        self.call_count = 0

    async def execute(self, **kwargs):
        self.call_count += 1
        return {
            "status": "failed",
            "summary": "- gradle: FAIL",
            "failure_log": "여전히 같은 컴파일 에러",
        }


class FakeSkillRegistry:
    def __init__(self, skills: dict):
        self._skills = skills

    def get_skill(self, name):
        return self._skills.get(name)

    def list_skills(self):
        return {name: s.description for name, s in self._skills.items()}


def _plan_json(tool: str, description: str, goal: str = "빌드를 통과시킨다") -> str:
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
    kwargs = (
        {"file_path": "Example.java", "content": "fixed"}
        if tool == "write_file"
        else {}
    )
    import json
    return "```json\n" + json.dumps({"tool": tool, "kwargs": kwargs}, ensure_ascii=False) + "\n```"


def test_is_repeated_tool_sequence_unit():
    # 완전히 동일하면 True
    assert ToolExecutor._is_repeated_tool_sequence(
        ["verify_project"], ["verify_project"],
    )
    # 순서/구성이 다르면 False
    assert not ToolExecutor._is_repeated_tool_sequence(
        ["verify_project"], ["write_file", "verify_project"],
    )
    # 이전 sequence가 비어 있으면(순수 판단 Task만 있던 Plan)
    # 항상 False — 빈 리스트끼리 "같다"고 오판하면 안 됨.
    assert not ToolExecutor._is_repeated_tool_sequence([], [])


def test_repeated_plan_triggers_deterministic_recovery():
    """재계획해도 Tool sequence가 완전히 같으면, 그 Plan을 다시
    실행하지 않고(LLM에게 또 기회를 주지 않고) 코드가 즉시 실패로
    종료해야 한다."""

    verify_skill = AlwaysFailVerifySkill()
    skill_registry = FakeSkillRegistry({"verify_project": verify_skill})

    llm = FakeLLM(
        [
            _plan_json("verify_project", "프로젝트를 빌드/검증한다"),
            _tool_call_json("verify_project"),  # 1차 검증 kwargs
            # replan()이 반환하는 새 Plan도 이전과 완전히 같은
            # Tool sequence(["verify_project"])다 — 여기서 멈춰야 함.
            _plan_json("verify_project", "다시 검증한다"),
        ]
    )

    goal_planner = GoalPlanner(llm=llm, skill_registry=skill_registry)

    executor = ToolExecutor(
        llm=llm,
        skill_registry=skill_registry,
        workspace_path=".",
        goal_planner=goal_planner,
        max_replans=2,
        reflector=None,
    )

    async def scenario():
        plan = await goal_planner.plan(prompt="빌드해줘")
        messages = [{"role": "user", "content": "빌드해줘"}]
        return await executor.run(messages, task="빌드해줘", plan=plan)

    response = asyncio.run(scenario())

    # verify_project는 딱 1번만 실행됐어야 한다 — 반복 계획을 다시
    # 실행하지 않았다는 뜻.
    assert verify_skill.call_count == 1

    # 재계획 예산(max_replans=2)이 남아 있었는데도, 반복 계획이
    # 감지된 시점에 더 재계획을 시도하지 않고 즉시 종료해야 한다.
    assert llm.calls == 3

    assert "동일한 Tool 순서" in response
    assert "여전히 같은 컴파일 에러" in response


def _two_task_plan_json(
    tool1: str, desc1: str, tool2: str, desc2: str,
    goal: str = "빌드를 통과시킨다",
) -> str:
    return (
        "```json\n"
        "{\n"
        f'  "goal": "{goal}",\n'
        '  "tasks": [\n'
        f'    {{"description": "{desc1}", "tool": "{tool1}"}},\n'
        f'    {{"description": "{desc2}", "tool": "{tool2}"}}\n'
        "  ]\n"
        "}\n"
        "```"
    )


def test_different_tool_sequence_replans_normally():
    """재계획 결과의 Tool sequence가 이전과 다르면(예: write_file이
    새로 추가됨) 반복 감지에 걸리지 않고 정상적으로 다시 실행되어야
    한다."""

    write_calls = {"count": 0}

    class FakeWriteSkill:
        name = "write_file"
        description = "가짜 write_file (테스트 전용)"

        async def execute(self, **kwargs):
            write_calls["count"] += 1
            return {"status": "success", "message": "written"}

    class OnceFailingVerifySkill:
        name = "verify_project"
        description = "가짜 검증 스킬 (테스트 전용)"

        def __init__(self):
            self.call_count = 0

        async def execute(self, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                return {
                    "status": "failed",
                    "summary": "- gradle: FAIL",
                    "failure_log": "최초 실패",
                }
            return {
                "status": "success",
                "summary": "- gradle: PASS",
                "failure_log": "",
            }

    verify_skill = OnceFailingVerifySkill()
    skill_registry = FakeSkillRegistry(
        {"verify_project": verify_skill, "write_file": FakeWriteSkill()}
    )

    llm = FakeLLM(
        [
            _plan_json("verify_project", "프로젝트를 빌드/검증한다"),
            _tool_call_json("verify_project"),  # 1차 검증 kwargs (실패)
            # 재계획 결과: write_file -> verify_project (이전과 다른 sequence)
            _two_task_plan_json(
                "write_file", "코드를 고친다",
                "verify_project", "다시 검증한다",
            ),
            _tool_call_json("write_file"),
            _tool_call_json("verify_project"),
            "빌드가 성공적으로 통과했습니다.",
        ]
    )

    goal_planner = GoalPlanner(llm=llm, skill_registry=skill_registry)

    executor = ToolExecutor(
        llm=llm,
        skill_registry=skill_registry,
        workspace_path=".",
        goal_planner=goal_planner,
        max_replans=2,
        reflector=None,
    )

    async def scenario():
        plan = await goal_planner.plan(prompt="빌드해줘")
        messages = [{"role": "user", "content": "빌드해줘"}]
        return await executor.run(messages, task="빌드해줘", plan=plan)

    response = asyncio.run(scenario())

    assert write_calls["count"] == 1
    assert verify_skill.call_count == 2
    assert response == "빌드가 성공적으로 통과했습니다."
    assert "동일한 Tool 순서" not in response


if __name__ == "__main__":
    test_is_repeated_tool_sequence_unit()
    test_repeated_plan_triggers_deterministic_recovery()
    test_different_tool_sequence_replans_normally()
    print("OK: repeated plan detection tests passed")