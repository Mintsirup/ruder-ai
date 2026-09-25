from pathlib import Path

from ruder_ai.core.tool_resolver import ToolResolver
from ruder_ai.skills import SkillRegistry


def test_resolves_basename_and_injects_workspace(tmp_path):
    target = tmp_path / "Assets" / "Scripts" / "PlayerController.cs"
    target.parent.mkdir(parents=True)
    target.write_text("class PlayerController {}\n", encoding="utf-8")
    registry = SkillRegistry()
    resolver = ToolResolver(registry, tmp_path)
    result = resolver.resolve("read_file", {}, description="PlayerController.cs 파일을 읽습니다.")
    assert result.ok
    assert result.kwargs["file_path"] == "Assets/Scripts/PlayerController.cs"
    assert result.kwargs["workspace_path"] == str(tmp_path)


def test_rejects_unknown_tool(tmp_path):
    resolver = ToolResolver(SkillRegistry(), tmp_path)
    result = resolver.resolve("nope", {})
    assert not result.ok
    assert "알 수 없는 Tool" in result.message
