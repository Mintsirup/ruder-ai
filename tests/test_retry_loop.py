"""Retry Loop 통합 테스트.

TODO.md의 "Retry Loop" 항목 검증:
Build/Test 실패(verify_project가 status=failed 반환)
  -> Planner 재계획(GoalPlanner.replan)
  -> 재검증(verify_project 재실행, 이번엔 성공)
까지 실제로 이어지는지 GoalPlanner + ToolExecutor를 함께 실행해서 확인한다.
LLM/스킬은 전부 스크립트로 응답을 미리 정해둔 가짜(fake) 객체로 대체한다.
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


class FakeVerifySkill:
    """처음엔 실패, 두 번째부터는 성공을 반환하는 가짜 verify_project 스킬."""

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
                "failure_log": (
                    "[gradle] FAILED (returncode=1, cmd=`./gradlew build`)\n"
                    "error: cannot find symbol Attribute.MAX_HEALTH"
                ),
            }

        return {
            "status": "success",
            "summary": "- gradle: PASS",
            "failure_log": "",
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
    if tool == "write_file":
        kwargs = {"file_path": "Example.java", "content": "fixed"}
    elif tool == "patch_file":
        kwargs = {"file_path": "Example.java", "old_str": "old", "new_str": "new"}
    else:
        kwargs = {}
    import json
    return "```json\n" + json.dumps({"tool": tool, "kwargs": kwargs}, ensure_ascii=False) + "\n```"


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


class FakeWriteSkill:
    name = "write_file"
    description = "가짜 write_file (테스트 전용)"

    async def execute(self, **kwargs):
        return {"status": "success", "message": "written"}


class FakePatchSkill:
    name = "patch_file"
    description = "가짜 patch_file (테스트 전용)"

    async def execute(self, **kwargs):
        return {"status": "success", "message": "patched"}


def test_retry_loop_replans_after_verify_failure():
    verify_skill = FakeVerifySkill()
    skill_registry = FakeSkillRegistry(
        {"verify_project": verify_skill, "write_file": FakeWriteSkill()}
    )

    llm = FakeLLM(
        [
            _plan_json("verify_project", "프로젝트를 빌드/검증한다"),
            _tool_call_json("verify_project"),  # 1차 검증 kwargs
            # 실패 로그를 반영해 코드를 고치는 Task가 추가된 새 Plan
            # (Tool sequence가 이전과 달라야 "동일 계획 반복 감지"에
            # 걸리지 않고 정상적으로 재실행된다).
            _two_task_plan_json(
                "write_file", "실패 로그를 반영해 코드를 고친다",
                "verify_project", "다시 검증한다",
            ),
            _tool_call_json("write_file"),
            _tool_call_json("verify_project"),  # 2차 검증 kwargs
            "빌드가 성공적으로 통과했습니다.",  # _finalize 최종 답변
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

    # verify_project는 정확히 2번(1차 실패 + 재검증 성공) 호출되어야 한다.
    assert verify_skill.call_count == 2

    # 재계획 경로를 타지 않고 그대로 실패로 끝난 게 아니어야 한다.
    assert not response.startswith("⚠️ 계획 실행 중 오류")
    assert "동일한 Tool 순서" not in response

    # 모든 LLM 응답이 정확히 소비되어야 한다(예상 호출 횟수와 일치).
    assert llm.calls == 6
    assert response == "빌드가 성공적으로 통과했습니다."


def test_retry_loop_gives_up_after_max_replans():
    """재계획 횟수를 초과하면 무한루프 없이 실패 메시지를 반환해야
    한다.

    "동일 계획 반복 감지"(deterministic recovery)가 먼저 걸려서
    max_replans 한도까지 못 가고 조기 종료하는 것과는 다른 경로임을
    보이기 위해, 매 재계획마다 Tool sequence를 다르게(write_file ->
    patch_file 순으로 바꿔가며) 구성해서 실제로 max_replans까지 전부
    소진되는지 검증한다.
    """

    class AlwaysFailVerifySkill(FakeVerifySkill):
        async def execute(self, **kwargs):
            self.call_count += 1
            return {
                "status": "failed",
                "summary": "- gradle: FAIL",
                "failure_log": "여전히 실패",
            }

    verify_skill = AlwaysFailVerifySkill()
    skill_registry = FakeSkillRegistry(
        {
            "verify_project": verify_skill,
            "write_file": FakeWriteSkill(),
            "patch_file": FakePatchSkill(),
        }
    )

    # max_replans=2 이므로: 최초 실행 1회 + 재계획 후 실행 2회 = verify 3번.
    # 각 재계획마다 Tool sequence를 다르게(write_file/patch_file을 번갈아
    # 섞음) 만들어 "동일 계획 반복 감지"에 걸리지 않도록 한다.
    llm = FakeLLM(
        [
            _plan_json("verify_project", "검증한다"),
            _tool_call_json("verify_project"),
            _two_task_plan_json(
                "write_file", "고쳐본다", "verify_project", "다시 검증한다",
            ),
            _tool_call_json("write_file"),
            _tool_call_json("verify_project"),
            _two_task_plan_json(
                "patch_file", "다르게 고쳐본다",
                "verify_project", "또 다시 검증한다",
            ),
            _tool_call_json("patch_file"),
            _tool_call_json("verify_project"),
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

    assert verify_skill.call_count == 3
    assert response.startswith("⚠️ 계획 실행 중 오류")
    assert "동일한 Tool 순서" not in response


if __name__ == "__main__":
    test_retry_loop_replans_after_verify_failure()
    test_retry_loop_gives_up_after_max_replans()
    print("OK: retry loop tests passed")