from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ruder_ai.core.checkpoint import CheckpointStore


@dataclass(frozen=True, slots=True)
class AutonomyBudget:
    max_cycles: int = 12
    max_wall_time_seconds: float = 1800.0
    max_failures: int = 3
    max_file_changes: int = 50


class AutonomyController:
    """Bounded, checkpointed outer loop around the role pipeline."""

    def __init__(self, workspace, *, budget: AutonomyBudget | None = None) -> None:
        self.budget = budget or AutonomyBudget()
        self.checkpoints = CheckpointStore(workspace)

    @staticmethod
    def should_enable(prompt: str, mode: str) -> bool:
        text = str(prompt or "").lower()
        if str(mode or "").lower() == "autonomous":
            return True
        markers = (
            "자율적으로", "자율 작업", "끝까지 해줘", "끝까지 작업", "계속 진행",
            "알아서 진행", "완료될 때까지", "until complete", "autonomously", "resume",
        )
        return any(marker in text for marker in markers)

    async def run(self, runner, *, prompt: str, resume: bool = False) -> str:
        started = time.monotonic()
        state = self.checkpoints.load() if resume else None
        if state and state.get("request") != prompt:
            state = None

        cycle_start = int(state.get("cycle", 0)) if state else 0
        failures = int(state.get("failures", 0)) if state else 0
        changed_files = set(state.get("changed_files", []) if state else [])
        previous_response = str(state.get("last_response", "") if state else "")
        recovery_prompt = str(state.get("recovery_prompt", "") if state else "")

        for cycle in range(cycle_start, self.budget.max_cycles):
            if time.monotonic() - started >= self.budget.max_wall_time_seconds:
                return self._limit_response("wall_time", cycle, changed_files, previous_response)
            if failures >= self.budget.max_failures:
                return self._limit_response("failures", cycle, changed_files, previous_response)
            if len(changed_files) >= self.budget.max_file_changes:
                return self._limit_response("file_changes", cycle, changed_files, previous_response)

            cycle_prompt = recovery_prompt or prompt
            response = await runner(prompt=cycle_prompt, max_cycles=1)
            actual_changed = set(getattr(runner, "last_changed_files", []) or [])
            changed_files.update(actual_changed)
            previous_response = response or ""

            response_text = str(response or "")
            failure_markers = (
                "[검증 실패]", "[Tester 검증 실패]", "[Reviewer 검증 실패]",
                "[구조화된 실행 결과]\n검증 상태: FAIL",
                "csharp-static: FAIL", "pytest: FAIL", "검증 상태: FAIL",
                "⚠️ 계획 실행 중 오류",
            )
            success = (
                not response_text.startswith("⚠️")
                and not any(marker in response_text for marker in failure_markers)
            )
            if success:
                self.checkpoints.clear()
                return response

            failures += 1
            recovery_prompt = (
                f"{prompt}\n\n"
                "[자율 복구 사이클] 이전 사이클이 실패했습니다. "
                "실패 내용을 사실로 취급하고 원인을 다시 확인한 뒤, "
                "필요한 코드를 실제로 수정하고 검증까지 계속 진행하세요. "
                "단순히 실패를 보고하고 종료하지 마세요.\n\n"
                f"이전 실행 결과:\n{previous_response[:8000]}"
            )
            self.checkpoints.save({
                "schema_version": 2,
                "request": prompt,
                "cycle": cycle + 1,
                "failures": failures,
                "changed_files": sorted(changed_files),
                "last_response": previous_response[:4000],
                "recovery_prompt": recovery_prompt[:12000],
                "status": "paused",
            })

        return self._limit_response("cycles", self.budget.max_cycles, changed_files, previous_response)

    @staticmethod
    def _limit_response(reason: str, cycle: int, changed_files: set[str], previous_response: str) -> str:
        reason_text = {
            "wall_time": "작업 시간 예산",
            "failures": "연속 실패 예산",
            "file_changes": "변경 파일 예산",
            "cycles": "자율 실행 횟수 예산",
        }[reason]
        return (
            f"⚠️ 자율 작업이 {reason_text}에 도달해 일시 중단되었습니다. "
            "checkpoint가 저장되어 이어서 재개할 수 있습니다.\n"
            f"현재 cycle={cycle}, 변경 파일={len(changed_files)}개.\n"
            + (f"마지막 결과:\n{previous_response}" if previous_response else "")
        )
