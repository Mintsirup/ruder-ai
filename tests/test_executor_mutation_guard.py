import pytest

from ruder_ai.core.executor import ToolExecutor
from ruder_ai.skills import SkillRegistry


@pytest.mark.asyncio
async def test_apply_patch_rejects_missing_requested_symbol(tmp_path):
    path = tmp_path / "PlayerController.cs"
    path.write_text("public class PlayerController {\n    float moveSpeed = 7f;\n}\n", encoding="utf-8")

    executor = ToolExecutor(
        llm=None,
        skill_registry=SkillRegistry(),
        workspace_path=str(tmp_path),
    )
    executor._current_task = "PlayerController의 superJumpForce를 20으로 바꿔줘"

    result = await executor._execute_tool(
        "apply_patch",
        {
            "diff": "--- a/PlayerController.cs\n+++ b/PlayerController.cs\n@@ -1,3 +1,4 @@\n public class PlayerController {\n     float moveSpeed = 7f;\n+    float superJumpForce = 20f;\n }\n"
        },
    )

    assert result["status"] == "error"
    assert result["guard"] == "missing_target_symbol"
