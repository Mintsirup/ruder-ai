from ruder_ai.core.executor import ToolExecutor
from ruder_ai.agents.orchestrator import AgentOrchestrator
from ruder_ai.agents.explorer import ExplorerEvidence, EvidenceFact


def test_backup_file_has_required_path_override():
    assert "file_path" in ToolExecutor._KNOWN_REQUIRED_OVERRIDES["backup_file"]


def test_explicit_absent_symbol_preflight_blocks_mutation():
    ev = ExplorerEvidence(files=["Assets/Scripts/PlayerController.cs"])
    ev.facts.append(EvidenceFact(kind="symbol", subject="nonexistentSpeed", exists=False, details="인덱스에서 심볼을 찾지 못함"))
    hit = AgentOrchestrator._explicit_nonexistent_symbol(
        "PlayerController.cs의 nonexistentSpeed 변수 값을 20으로 변경해줘", ev
    )
    assert hit is not None
    assert hit[0] == "nonexistentSpeed"


def test_deterministic_csharp_repair_plan_is_minimal():
    result = {
        "status": "failed",
        "summary": "[csharp-static] FAILED",
        "failure_log": "Assets/Scripts/PlayerController.cs: 'jumpForce'가 선언되지 않았습니다. 유사 선언: superJumpForce",
    }
    plan = AgentOrchestrator._deterministic_csharp_repair_plan(result)
    assert plan is not None
    assert [t.tool for t in plan.tasks] == ["read_file", "patch_file"]
    assert "jumpForce" in plan.tasks[1].description
    assert "superJumpForce" in plan.tasks[1].description
