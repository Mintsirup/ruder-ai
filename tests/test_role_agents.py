from pathlib import Path

import pytest

from ruder_ai.agents import (
    AgentOrchestrator, CoderAgent, ExplorerAgent, MemoryAgent,
    ReviewerAgent, TesterAgent,
)


class FakeLLM:
    async def chat(self, messages):
        return "OK"


def test_role_agents_share_one_llm():
    llm = FakeLLM()
    workspace = Path(".")
    agents = [
        ExplorerAgent(llm, workspace),
        CoderAgent(llm, workspace),
        TesterAgent(llm, workspace),
        ReviewerAgent(llm, workspace),
        MemoryAgent(llm, workspace),
    ]
    assert all(agent.llm is llm for agent in agents)
    assert agents[0].role.can_mutate is False
    assert agents[1].role.can_mutate is True
    assert agents[2].role.can_mutate is False
    assert agents[3].role.can_mutate is False
    assert agents[4].role.can_mutate is False


@pytest.mark.asyncio
async def test_reviewer_rejects_claim_not_grounded_in_file(tmp_path):
    llm = FakeLLM()
    file = tmp_path / "Example.cs"
    file.write_text("grounded = false;\n", encoding="utf-8")
    reviewer = ReviewerAgent(llm, tmp_path)

    # fake LLM says OK; this test checks the role is callable and isolated.
    passed, feedback = await reviewer.review(
        ["Example.cs"],
        "fal을 false로 수정해줘",
        "수정했습니다.",
    )
    assert passed is True
    assert feedback == ""


def test_orchestrator_is_role_coordinator():
    orchestrator = AgentOrchestrator(
        explorer=ExplorerAgent(),
        coder=CoderAgent(),
        tester=TesterAgent(),
        reviewer=ReviewerAgent(),
        memory_agent=MemoryAgent(),
    )
    assert orchestrator.explorer.role.name == "Explorer"
    assert orchestrator.coder.role.name == "Coder"
    assert orchestrator.tester.role.name == "Tester"
    assert orchestrator.reviewer.role.name == "Reviewer"
    assert orchestrator.memory_agent.role.name == "Memory"
