from pathlib import Path

from ruder_ai.core.agent import AIAgent


def test_workspace_signature_changes_when_file_is_added(tmp_path: Path):
    agent = AIAgent(workspace_path=str(tmp_path), enable_reflection=False)
    first = agent._build_project_index()
    assert all(f.relative_path != "Assets/Scripts/New.cs" for f in first.files)

    p = tmp_path / "Assets" / "Scripts" / "New.cs"
    p.parent.mkdir(parents=True)
    p.write_text("public class New {}\n", encoding="utf-8")

    second = agent._build_project_index()
    assert any(f.relative_path == "Assets/Scripts/New.cs" for f in second.files)
