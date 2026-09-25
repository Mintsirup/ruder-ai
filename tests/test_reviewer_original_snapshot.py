from pathlib import Path

from ruder_ai.agents.explorer import ExplorerAgent
from ruder_ai.agents.reviewer import ReviewerAgent
from ruder_ai.indexer.models import FileInfo, ProjectIndex, ProjectInfo, Symbol


class FakeLLM:
    def __init__(self):
        self.messages = []

    async def chat(self, messages):
        self.messages.append(messages)
        return "OK"


def test_explorer_keeps_pre_mutation_snapshot(tmp_path: Path):
    path = tmp_path / "Assets" / "Scripts" / "PlayerController.cs"
    path.parent.mkdir(parents=True)
    path.write_text("velocity.y = jumpForce;\n", encoding="utf-8")
    index = ProjectIndex(
        workspace=tmp_path,
        project=ProjectInfo(tmp_path, "C#", "Unknown", "Unity"),
        files=[FileInfo("Assets/Scripts/PlayerController.cs", path, ".cs", path.stat().st_size)],
        symbols=[Symbol("jumpForce", "field", "Assets/Scripts/PlayerController.cs", 1)],
    )
    evidence = ExplorerAgent(workspace_path=tmp_path).collect_evidence(
        index, "PlayerController.cs의 오류를 찾아서 수정해줘"
    )
    assert "jumpForce" in evidence.snapshots["Assets/Scripts/PlayerController.cs"]


def test_reviewer_receives_before_and_after_contents(tmp_path: Path):
    path = tmp_path / "PlayerController.cs"
    path.write_text("velocity.y = superJumpForce;\n", encoding="utf-8")
    llm = FakeLLM()
    reviewer = ReviewerAgent(llm, tmp_path)
    import asyncio
    ok, feedback = asyncio.run(reviewer.review(
        ["PlayerController.cs"],
        "PlayerController.cs의 실제 컴파일 오류를 수정해줘",
        "수정했습니다.",
        "symbol: jumpForce = 없음",
        ["PlayerController.cs"],
        {"PlayerController.cs": "velocity.y = jumpForce;\n"},
    ))
    assert ok is True
    joined = "\n".join(m[1]["content"] for m in llm.messages if len(m) > 1)
    assert "jumpForce" in joined
    assert "superJumpForce" in joined
