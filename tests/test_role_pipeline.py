import pytest

from ruder_ai.agents.orchestrator import AgentOrchestrator
from ruder_ai.agents.role_tester import TesterAgent
from ruder_ai.core.goal_planner import Goal, Plan, PlanTask


class FakeSkill:
    async def execute(self, **kwargs):
        return {"status": "success", "summary": "build passed"}


class FakeRegistry:
    def list_skills(self):
        return {"write_file": "write", "verify_project": "verify"}

    def get_skill(self, name):
        return FakeSkill()


class FakeExecutor:
    def __init__(self):
        self._active_role = None
        self._current_task = ""
        self.last_changed_files = ["Assets/Test.cs"]
        self.last_verification_summary = ""
        self.last_verification_result = {}

    async def _execute_tool(self, tool_name, kwargs):
        assert self._active_role == "Tester"
        assert tool_name == "verify_project"
        return {"status": "success", "summary": "PASS", "ran_tools": ["dotnet"]}


@pytest.mark.asyncio
async def test_tester_is_independent_and_sets_real_result():
    executor = FakeExecutor()
    agent = TesterAgent()
    passed, text, result = await agent.verify(executor, task="verify")
    assert passed is True
    assert text == "PASS"
    assert result["status"] == "success"
    assert executor._active_role is None
    assert executor.last_verification_summary == "PASS"


def test_coder_plan_defers_verification():
    plan = Plan(
        goal=Goal("x"),
        tasks=[
            PlanTask(1, "edit", "write_file"),
            PlanTask(2, "verify", "verify_project"),
        ],
    )
    filtered = AgentOrchestrator._coder_plan(plan)
    assert [t.tool for t in filtered.tasks] == ["write_file"]
    assert plan.tasks[1].tool == "verify_project"
