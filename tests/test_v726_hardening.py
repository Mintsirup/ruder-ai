from pathlib import Path

from ruder_ai.indexer.detector import ProjectDetector
from ruder_ai.indexer.models import FileInfo
from ruder_ai.core.executor import ToolExecutor


def test_unity_detected_from_asset_paths():
    files = [
        FileInfo(relative_path="Assets/Scripts/PlayerController.cs", absolute_path=Path("Assets/Scripts/PlayerController.cs"), extension=".cs", size=1),
        FileInfo(relative_path="ProjectSettings/ProjectVersion.txt", absolute_path=Path("ProjectSettings/ProjectVersion.txt"), extension=".txt", size=1),
    ]
    project = ProjectDetector().detect(".", files)
    assert project.language == "C#"
    assert project.framework == "Unity"


def test_explicit_file_path_can_be_recovered_from_task():
    value = ToolExecutor._extract_explicit_file_paths(
        "PlayerController.cs의 실제 컴파일 오류를 수정해줘"
    )
    assert value == ["PlayerController.cs"]
