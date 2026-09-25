from pathlib import Path
import asyncio

from ruder_ai.verify.csharp_static import run_csharp_static_check


def test_csharp_static_detects_missing_jump_force(tmp_path: Path):
    p = tmp_path / "Assets" / "Scripts" / "PlayerController.cs"
    p.parent.mkdir(parents=True)
    p.write_text(
        "private float superJumpForce = 25f;\n"
        "velocity.y = jumpForce;\n"
        "grounded = false;\n",
        encoding="utf-8",
    )
    result = asyncio.run(run_csharp_static_check(tmp_path))
    assert result.passed is False
    assert "jumpForce" in result.stderr


def test_csharp_static_passes_after_fix(tmp_path: Path):
    p = tmp_path / "Assets" / "Scripts" / "PlayerController.cs"
    p.parent.mkdir(parents=True)
    p.write_text(
        "private float superJumpForce = 25f;\n"
        "velocity.y = superJumpForce;\n"
        "grounded = false;\n",
        encoding="utf-8",
    )
    result = asyncio.run(run_csharp_static_check(tmp_path))
    assert result.passed is True
