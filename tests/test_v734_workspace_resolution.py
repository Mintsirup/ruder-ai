from pathlib import Path

from ruder_ai.agents.explorer import ExplorerAgent
from ruder_ai.indexer.models import FileInfo, ProjectIndex, ProjectInfo
from ruder_ai.verify.csharp_static import run_csharp_static_check


def _index(tmp: Path):
    path = tmp / "Assets" / "Scripts" / "PlayerController.cs"
    path.parent.mkdir(parents=True)
    path.write_text(
        "using UnityEngine;\npublic class PlayerController : MonoBehaviour {\n    private float superJumpForce = 25f;\n    void Update() { velocity.y = jumpForce; }\n}\n",
        encoding="utf-8",
    )
    return ProjectIndex(
        workspace=tmp,
        project=ProjectInfo(tmp, "C#", "Unknown", "Unity"),
        files=[FileInfo(path.relative_to(tmp).as_posix(), path, ".cs", path.stat().st_size)],
        symbols=[],
    )


def test_explorer_resolves_explicit_nested_basename(tmp_path: Path):
    index = _index(tmp_path)
    evidence = ExplorerAgent(workspace_path=tmp_path).collect_evidence(
        index, "PlayerController.cs의 오류를 찾아줘"
    )
    assert "Assets/Scripts/PlayerController.cs" in evidence.files
    assert "Assets/Scripts/PlayerController.cs" in evidence.snapshots
    assert "jumpForce" in evidence.snapshots["Assets/Scripts/PlayerController.cs"]


def test_csharp_static_resolves_nested_basename(tmp_path: Path):
    _index(tmp_path)
    import asyncio
    report = asyncio.run(
        run_csharp_static_check(tmp_path, target_files=["PlayerController.cs"])
    )
    assert report.passed is False
    assert report.skipped is False
    assert "jumpForce" in report.log_text()


def test_csharp_static_missing_target_is_failure_not_skip(tmp_path: Path):
    import asyncio
    report = asyncio.run(
        run_csharp_static_check(tmp_path, target_files=["NotExistController.cs"])
    )
    assert report.passed is False
    assert report.skipped is False
    assert report.returncode == 2
