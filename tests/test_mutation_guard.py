from pathlib import Path

import pytest

from ruder_ai.core.mutation_guard import MutationGuard
from ruder_ai.skills.file_ops import PatchFileSkill, WriteFileSkill


@pytest.mark.asyncio
async def test_modify_missing_named_symbol_is_rejected(tmp_path: Path):
    path = tmp_path / "PlayerController.cs"
    original = "public class PlayerController {\n    float moveSpeed = 5f;\n}\n"
    path.write_text(original, encoding="utf-8")

    result = await PatchFileSkill().execute(
        file_path="PlayerController.cs",
        old_str="float moveSpeed = 5f;",
        new_str="float superJumpForce = 20f;\n    float moveSpeed = 5f;",
        workspace_path=str(tmp_path),
        __task="PlayerController의 superJumpForce를 20으로 바꿔줘",
    )

    assert result["status"] == "error"
    assert result["guard"] == "missing_target_symbol"
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_explicit_add_allows_new_named_symbol(tmp_path: Path):
    path = tmp_path / "PlayerController.cs"
    original = "public class PlayerController {\n    float moveSpeed = 5f;\n}\n"
    path.write_text(original, encoding="utf-8")

    result = await PatchFileSkill().execute(
        file_path="PlayerController.cs",
        old_str="float moveSpeed = 5f;",
        new_str="float moveSpeed = 5f;\n    float superJumpForce = 20f;",
        workspace_path=str(tmp_path),
        __task="PlayerController에 superJumpForce를 20으로 추가해줘",
    )

    assert result["status"] == "success"
    assert "superJumpForce" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_modify_missing_file_is_rejected(tmp_path: Path):
    result = await WriteFileSkill().execute(
        file_path="Missing.cs",
        content="public class Missing {}",
        workspace_path=str(tmp_path),
        __task="Missing.cs를 수정해줘",
    )
    assert result["status"] == "error"
    assert result["guard"] == "missing_target_file"
    assert not (tmp_path / "Missing.cs").exists()


def test_candidate_symbol_detection():
    guard = MutationGuard()
    assert "superJumpForce" in guard.candidate_symbols(
        "PlayerController의 superJumpForce를 20으로 바꿔줘"
    )
    assert guard.task_allows_new_symbols("superJumpForce를 추가해줘")
    assert not guard.task_allows_new_symbols("superJumpForce를 바꿔줘")
