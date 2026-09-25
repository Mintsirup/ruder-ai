"""web_search 코드 레벨 guard 테스트.

TODO.md P0 "web_search 코드 레벨 guard" 항목 검증:
실제 벤치마크 로그에서 Planner가 "파일 검색" 같은 추상적인 Task를
web_search로 오해하는 사고가 확인됐다. 코딩 작업(Plan에 read_file/
write_file 등 로컬 Tool이 섞여 있음) + 사용자가 검색을 명시적으로
요청하지 않은 상황에서 Planner가 web_search/web_fetch Task를
끼워 넣으면, `GoalPlanner._guard_unnecessary_web_search`가 코드
레벨에서 그 Task를 제거하는지 확인한다.
"""

from __future__ import annotations

import asyncio

from ruder_ai.core.goal_planner import GoalPlanner


class FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.last_messages = None

    async def chat(self, messages):
        self.last_messages = messages
        return self.response


class FakeSkillRegistry:
    def __init__(self, names: list[str]):
        self._names = names

    def list_skills(self):
        return {name: f"{name} 설명" for name in self._names}


def _plan_json(*tasks: tuple[str, str | None], goal: str = "요청을 처리한다") -> str:
    task_items = ",\n".join(
        f'    {{"description": "{desc}", "tool": {("null" if tool is None else chr(34)+tool+chr(34))}}}'
        for desc, tool in tasks
    )
    return (
        "```json\n"
        "{\n"
        f'  "goal": "{goal}",\n'
        "  \"tasks\": [\n"
        f"{task_items}\n"
        "  ]\n"
        "}\n"
        "```"
    )


def run(coro):
    return asyncio.run(coro)


def test_removes_web_search_from_coding_plan_without_explicit_request():
    """"파일 검색" 같은 추상적인 코딩 Task를 Planner가 web_search로
    잘못 계획해도, 명시적 검색 요청이 아니고 다른 로컬 Tool이 함께
    있으면 web_search Task를 제거해야 한다."""

    skill_registry = FakeSkillRegistry(
        ["web_search", "web_fetch", "semantic_search", "read_file"]
    )
    llm = FakeLLM(
        _plan_json(
            ("특정 함수를 사용하는 파일을 찾는다", "web_search"),
            ("찾은 파일을 읽는다", "read_file"),
        )
    )

    goal_planner = GoalPlanner(llm=llm, skill_registry=skill_registry)

    plan = run(
        goal_planner.plan(prompt="특정 함수를 쓰는 파일이 어디 있는지 확인해줘")
    )

    assert "web_search" not in plan.tool_sequence()
    assert "read_file" in plan.tool_sequence()
    assert any("web_search" in w for w in plan.warnings)


def test_keeps_web_search_when_explicitly_requested():
    """사용자가 명시적으로 검색을 요청했으면(예: "검색해줘") 코딩
    Task와 섞여 있어도 web_search를 그대로 둬야 한다."""

    skill_registry = FakeSkillRegistry(
        ["web_search", "web_fetch", "read_file"]
    )
    llm = FakeLLM(
        _plan_json(
            ("최신 라이브러리 버전을 검색한다", "web_search"),
            ("코드를 수정한다", "read_file"),
        )
    )

    goal_planner = GoalPlanner(llm=llm, skill_registry=skill_registry)

    plan = run(
        goal_planner.plan(
            prompt="이 라이브러리 최신 버전 검색해서 코드에 반영해줘"
        )
    )

    assert "web_search" in plan.tool_sequence()


def test_keeps_web_search_for_pure_chat_question():
    """로컬 코딩 Tool이 전혀 없는 순수 채팅성 질문(예: 시사/버전
    질문)이면 명시적 요청이 없어도 web_search를 건드리지 않는다 —
    guard는 '코딩 작업'일 때만 적용된다."""

    skill_registry = FakeSkillRegistry(["web_search"])
    llm = FakeLLM(_plan_json(("질문에 답한다", "web_search")))

    goal_planner = GoalPlanner(llm=llm, skill_registry=skill_registry)

    plan = run(goal_planner.plan(prompt="지금 비트코인 시세 얼마야?"))

    assert "web_search" in plan.tool_sequence()


if __name__ == "__main__":
    test_removes_web_search_from_coding_plan_without_explicit_request()
    test_keeps_web_search_when_explicitly_requested()
    test_keeps_web_search_for_pure_chat_question()
    print("OK: web_search guard tests passed")