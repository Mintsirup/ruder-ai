from __future__ import annotations

import json
from pathlib import Path

import pytest

from ruder_ai.core.autonomy import AutonomyBudget, AutonomyController
from ruder_ai.core.checkpoint import CheckpointStore


def test_checkpoint_store_is_atomic_and_roundtrips(tmp_path: Path):
    store = CheckpointStore(tmp_path)
    state = {"request": "fix player", "cycle": 2, "changed_files": ["Assets/Scripts/Player.cs"]}
    store.save(state)
    assert json.loads(store.path.read_text(encoding="utf-8"))["cycle"] == 2
    assert store.load()["changed_files"] == ["Assets/Scripts/Player.cs"]
    store.clear()
    assert not store.path.exists()


def test_autonomy_marker_detection():
    assert AutonomyController.should_enable("자율적으로 끝까지 작업해줘", "code")
    assert AutonomyController.should_enable("anything", "autonomous")
    assert not AutonomyController.should_enable("단순히 설명해줘", "code")


@pytest.mark.asyncio
async def test_autonomy_resumes_after_failure(tmp_path: Path):
    controller = AutonomyController(
        tmp_path,
        budget=AutonomyBudget(max_cycles=4, max_wall_time_seconds=30, max_failures=3, max_file_changes=20),
    )
    calls = {"n": 0}

    class Runner:
        last_changed_files = []

        async def __call__(self, *, prompt: str, max_cycles: int = 1):
            calls["n"] += 1
            if calls["n"] == 1:
                return "⚠️ 작업 실패"
            self.last_changed_files = ["foo.py"]
            return "성공"

    runner = Runner()
    assert await controller.run(runner, prompt="continue task") == "성공"
    assert calls["n"] == 2
    assert controller.checkpoints.load() is None


@pytest.mark.asyncio
async def test_autonomy_persists_checkpoint_on_budget_stop(tmp_path: Path):
    controller = AutonomyController(
        tmp_path,
        budget=AutonomyBudget(max_cycles=2, max_wall_time_seconds=30, max_failures=2, max_file_changes=20),
    )

    class Runner:
        last_changed_files = []

        async def __call__(self, *, prompt: str, max_cycles: int = 1):
            return "⚠️ 계속 실패"

    result = await controller.run(Runner(), prompt="keep going")
    assert result.startswith("⚠️ 자율 작업")
    data = controller.checkpoints.load()
    assert data is not None
    assert data["status"] == "paused"


@pytest.mark.asyncio
async def test_autonomy_budget_preserves_changed_files(tmp_path: Path):
    controller = AutonomyController(tmp_path, budget=AutonomyBudget(max_cycles=1, max_wall_time_seconds=30, max_failures=5, max_file_changes=20))

    class Runner:
        last_changed_files = ["a.cs"]
        async def __call__(self, *, prompt: str, max_cycles: int = 1):
            return "⚠️ failed"

    result = await controller.run(Runner(), prompt="x")
    data = controller.checkpoints.load()
    assert data["changed_files"] == ["a.cs"]
    assert result.startswith("⚠️ 자율 작업")


@pytest.mark.asyncio
async def test_autonomy_passes_failure_context_into_recovery_cycle(tmp_path: Path):
    controller = AutonomyController(
        tmp_path,
        budget=AutonomyBudget(max_cycles=3, max_wall_time_seconds=30, max_failures=3, max_file_changes=20),
    )
    prompts = []

    class Runner:
        last_changed_files = []

        async def __call__(self, *, prompt: str, max_cycles: int = 1):
            prompts.append(prompt)
            if len(prompts) == 1:
                return "[Tester 검증 실패] csharp-static: FAIL jumpForce 미정의"
            self.last_changed_files = ["Assets/Scripts/PlayerController.cs"]
            return "수정 완료\n[구조화된 실행 결과]\n검증 상태: PASS"

    result = await controller.run(Runner(), prompt="PlayerController.cs 오류 수정")
    assert "수정 완료" in result
    assert len(prompts) == 2
    assert "이전 실행 결과" in prompts[1]
    assert "단순히 실패를 보고하고 종료하지 마세요" in prompts[1]


@pytest.mark.asyncio
async def test_autonomy_treats_structured_fail_as_failure(tmp_path: Path):
    controller = AutonomyController(
        tmp_path,
        budget=AutonomyBudget(max_cycles=2, max_wall_time_seconds=30, max_failures=2, max_file_changes=20),
    )
    calls = {"n": 0}

    class Runner:
        last_changed_files = []

        async def __call__(self, *, prompt: str, max_cycles: int = 1):
            calls["n"] += 1
            if calls["n"] == 1:
                return "결과\n[구조화된 실행 결과]\n검증 상태: FAIL"
            return "성공\n[구조화된 실행 결과]\n검증 상태: PASS"

    assert "성공" in await controller.run(Runner(), prompt="repair")
    assert calls["n"] == 2
