from pathlib import Path

from ruder_ai.agents.orchestrator import AgentOrchestrator
from ruder_ai.verify.verifier import AutoVerifier


class DummyProject:
    language = "C#"
    build_system = ""
    framework = "Unity"


def test_explicit_nonexistent_symbol_handles_korean_ranun():
    from ruder_ai.agents.explorer import ExplorerEvidence, EvidenceFact

    ev = ExplorerEvidence(files=["Assets/Scripts/PlayerController.cs"])
    ev.facts.append(
        EvidenceFact(
            kind="symbol",
            subject="nonexistentJumpForce",
            exists=False,
            details="인덱스에서 심볼을 찾지 못함",
        )
    )
    hit = AgentOrchestrator._explicit_nonexistent_symbol(
        "PlayerController.cs에서 nonexistentJumpForce라는 변수를 superJumpForce로 변경해줘",
        ev,
    )
    assert hit is not None
    assert hit[0] == "nonexistentJumpForce"


def test_explicit_file_paths_are_deterministic():
    paths = AgentOrchestrator._explicit_file_paths(
        "PlayerController.cs와 music_player.py를 수정해줘"
    )
    assert paths == ["PlayerController.cs", "music_player.py"]


def test_targeted_unity_verification_plans_only_matching_csharp_check(monkeypatch):
    calls = []

    async def fake_csharp(workspace, timeout=30, target_files=None):
        calls.append(target_files)
        return object()

    monkeypatch.setattr(
        "ruder_ai.verify.verifier.csharp_static.run_csharp_static_check",
        fake_csharp,
    )
    verifier = AutoVerifier()
    checks = verifier._plan_checks(
        DummyProject(), Path("/tmp/workspace"), ["music_player.py"]
    )
    assert calls == []
    assert checks == []


def test_targeted_unity_verification_passes_only_changed_cs(monkeypatch):
    calls = []

    async def fake_csharp(workspace, timeout=30, target_files=None):
        calls.append(target_files)
        return object()

    monkeypatch.setattr(
        "ruder_ai.verify.verifier.csharp_static.run_csharp_static_check",
        fake_csharp,
    )
    verifier = AutoVerifier()
    checks = verifier._plan_checks(
        DummyProject(), Path("/tmp/workspace"), ["Assets/Scripts/PlayerController.cs"]
    )
    assert len(checks) == 1
    assert calls == []  # async check is created but not awaited
    try:
        checks[0].close()
    except Exception:
        pass
