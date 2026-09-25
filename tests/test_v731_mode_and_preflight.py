from pathlib import Path
import asyncio

from ruder_ai.verify.csharp_static import run_csharp_static_check


def test_csharp_static_target_file_runs(tmp_path: Path):
    src = tmp_path / "Assets" / "Scripts"
    src.mkdir(parents=True)
    (src / "PlayerController.cs").write_text(
        "using UnityEngine;\n"
        "public class PlayerController : MonoBehaviour {\n"
        "private float superJumpForce = 25f;\n"
        "void Update(){ velocity.y = jumpForce; }\n"
        "}\n",
        encoding="utf-8",
    )
    result = asyncio.run(
        run_csharp_static_check(
            tmp_path,
            target_files=["Assets/Scripts/PlayerController.cs"],
        )
    )
    assert result.passed is False
    assert result.skipped is False
    assert "jumpForce" in result.stderr


def test_project_file_detector_path_indicates_unity():
    # This is a regression guard for target-file Unity projects.
    from ruder_ai.indexer.detector import ProjectDetector
    from ruder_ai.indexer.models import FileInfo
    files = [
        FileInfo("Assets/Scripts/PlayerController.cs", Path("/tmp/PlayerController.cs"), ".cs", 10),
    ]
    info = ProjectDetector().detect("/tmp", files)
    assert info.language == "C#"
    assert info.framework == "Unity"
