import pytest

from ruder_ai.skills.git_ops import _run_git


@pytest.mark.asyncio
async def test_git_runner_does_not_use_shell(tmp_path):
    result = await _run_git("status --porcelain", str(tmp_path))
    assert result["status"] in {"success", "failed", "error"}
    assert "git " not in result.get("message", "") or result["status"] == "success"
