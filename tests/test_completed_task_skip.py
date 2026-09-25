"""1-5 수정 검증: 재계획 시 이미 완료된 Task를 재실행하지 않는지 확인.

버그 재현 조건이었던 상황: 자기검증(Reflection) 실패 -> Planner가
새 Plan을 짜면서 이전에 이미 성공한 Task(예: list_directory)를 새
Plan에 그대로/다시 포함시킴 -> 기존 코드는 새 Plan의 Task를 무조건
처음부터 다시 실행했음 (Task 1부터 전체 재실행).

수정 후: Executor가 (tool, kwargs)가 완전히 동일하면서 이미 성공한
Task는 LLM 호출/Tool 실행 없이 건너뛴다.
"""

from __future__ import annotations

import asyncio

from ruder_ai.core.executor import ToolExecutor
from ruder_ai.core.goal_planner import GoalPlanner
from ruder_ai.core.reflection import ReflectionResult


class FakeLLM:
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


class CountingListDirSkill:
    name = "list_directory"
    description = "가짜 list_directory 스킬 (테스트 전용)"

    def __init__(self):
        self.call_count = 0

    async def execute(self, **kwargs):
        self.call_count += 1
        return {"status": "success", "entries": ["a.py"]}


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
    def __init__(self, verdicts, max_reflections: int):
        self._verdicts = list(verdicts)
        self.max_reflections = max_reflections
        self.calls = 0

    async def review(self, task, final_answer, changed_files):
        self.calls += 1
        return self._verdicts.pop(0)


def _plan_json(tasks: list[tuple[str, str]], goal: str = "작업을 완료한다") -> str:
    task_json = ",\n".join(
        f'    {{"description": "{desc}", "tool": "{tool}"}}'
        for desc, tool in tasks
    )
    return (
        "```json\n"
        "{\n"
        f'  "goal": "{goal}",\n'
        '  "tasks": [\n'
        f"{task_json}\n"
        "  ]\n"
        "}\n"
        "```"
    )


def _tool_call_json(tool: str, kwargs: dict | None = None) -> str:
    import json as _json

    return (
        "```json\n"
        + _json.dumps({"tool": tool, "kwargs": kwargs or {}})
        + "\n```"
    )


def run(coro):
    return asyncio.run(coro)


def test_replan_skips_already_completed_identical_task():
    list_dir_skill = CountingListDirSkill()
    registry = FakeSkillRegistry(
        {"list_directory": list_dir_skill, "write_file": FakeWriteSkill()}
    )

    llm = FakeLLM(
        [
            # 1차 Plan: list_directory -> write_file
            _plan_json(
                [
                    ("디렉토리를 확인한다", "list_directory"),
                    ("파일을 작성한다", "write_file"),
                ]
            ),
            _tool_call_json("list_directory", {"path": "."}),
            _tool_call_json(
                "write_file", {"file_path": "a.py", "content": "x"}
            ),
            "1차 답변",  # reflection ISSUE -> replan
            # 재계획된 Plan: Planner가 list_directory를 다시 포함시킴
            # (완료 상태를 몰랐거나 "재사용" 선택) + write_file 수정
            _plan_json(
                [
                    ("디렉토리를 다시 확인한다", "list_directory"),
                    ("피드백을 반영해 다시 작성한다", "write_file"),
                ]
            ),
            # list_directory Task 차례: kwargs를 알아야 "이전과 동일한지"
            # 판단할 수 있으므로 LLM 호출 자체는 발생한다. kwargs가
            # 1차 라운드와 동일({"path": "."})하면 실제 Tool 실행
            # (list_dir_skill.execute)은 건너뛰어야 한다.
            _tool_call_json("list_directory", {"path": "."}),
            _tool_call_json(
                "write_file", {"file_path": "a.py", "content": "y"}
            ),
            "2차 답변",  # reflection OK
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
    # list_directory 스킬의 실제 execute()는 1차 라운드에서 딱 한 번만
    # 호출되어야 한다 - 재계획 라운드에서 동일 kwargs로 다시 포함되어도
    # 재실행되지 않아야 한다.
    assert list_dir_skill.call_count == 1