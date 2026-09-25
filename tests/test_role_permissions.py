from pathlib import Path

import pytest

from ruder_ai.agents.permissions import check_tool_permission, is_tool_allowed
from ruder_ai.core.executor import ToolExecutor


class FakeSkill:
    async def execute(self, **kwargs):
        return {"status": "success"}


class FakeRegistry:
    def __init__(self):
        self.skills = {"write_file": FakeSkill(), "read_file": FakeSkill(), "verify_project": FakeSkill()}

    def get_skill(self, name):
        return self.skills.get(name)


@pytest.mark.parametrize("role,tool,allowed", [
    ("Explorer", "read_file", True),
    ("Explorer", "write_file", False),
    ("Reviewer", "patch_file", False),
    ("Tester", "verify_project", True),
    ("Tester", "write_file", False),
    ("Memory", "read_file", False),
    ("Coder", "write_file", True),
])
def test_role_tool_policy(role, tool, allowed):
    assert is_tool_allowed(role, tool) is allowed


@pytest.mark.asyncio
async def test_executor_enforces_active_role_permission(tmp_path):
    executor = ToolExecutor(
        llm=None,
        skill_registry=FakeRegistry(),
        workspace_path=str(tmp_path),
        tool_timeout=None,
    )
    executor._active_role = "Reviewer"

    denied = await executor._execute_tool("write_file", {"file_path": "x.cs", "content": "x"})
    assert denied["status"] == "error"
    assert denied["error_type"] == executor.ERROR_TYPE_PERMISSION

    allowed = await executor._execute_tool("read_file", {"file_path": "x.cs"})
    assert allowed["status"] == "success"


def test_permission_result_is_explanatory():
    result = check_tool_permission("Explorer", "write_file")
    assert not result.allowed
    assert "권한" in result.message
