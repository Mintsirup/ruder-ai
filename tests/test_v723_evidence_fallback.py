from pathlib import Path

from ruder_ai.agents.explorer import ExplorerAgent
from ruder_ai.core.goal_planner import GoalPlanner
from ruder_ai.indexer.models import FileInfo, ProjectIndex, ProjectInfo, Symbol


class BadLLM:
    async def chat(self, messages):
        return "not json"


class Registry:
    def list_skills(self):
        return {
            "list_directory": "list",
            "read_file": "read",
            "patch_file": "patch",
            "verify_project": "verify",
        }


def make_index(tmp_path: Path):
    player = tmp_path / "Assets" / "Scripts" / "PlayerController.cs"
    player.parent.mkdir(parents=True)
    player.write_text(
        "private float superJumpForce = 25f;\n"
        "velocity.y = jumpForce;\n"
        "grounded = false;\n",
        encoding="utf-8",
    )
    files = [
        FileInfo("Assets/Scripts/PlayerController.cs", player, ".cs", player.stat().st_size)
    ]
    symbols = [
        Symbol("superJumpForce", "field", "Assets/Scripts/PlayerController.cs", 1),
    ]
    return ProjectIndex(
        workspace=tmp_path,
        project=ProjectInfo(tmp_path, "csharp", "unity"),
        files=files,
        symbols=symbols,
    )


def test_explorer_evidence_distinguishes_missing_symbol_and_text(tmp_path):
    index = make_index(tmp_path)
    explorer = ExplorerAgent(workspace_path=tmp_path)
    evidence = explorer.collect_evidence(
        index,
        "PlayerController.cs의 jumpForce와 fal을 확인해줘",
    )
    text = evidence.as_text()
    assert "symbol: jumpForce = 없음" in text
    assert "source_text: fal = 없음" in text


def test_fallback_plan_preserves_mutation_and_verification_intent():
    planner = GoalPlanner(BadLLM(), Registry())
    plan = planner._fallback_plan(
        "PlayerController.cs의 컴파일 오류를 찾아서 수정하고 검증해줘",
        warning="parse failed",
    )
    tools = [task.tool for task in plan.tasks]
    assert tools[0] == "read_file"
    assert "patch_file" in tools
    assert "verify_project" in tools
    assert tools.count("verify_project") == 1
