"""Patch Generator/Applier 결과 모델."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PatchResult:
    """diff 생성/적용/검증/rollback 1회의 결과."""

    success: bool

    message: str = ""

    command: str = ""
    """실제로 실행한 커맨드 (예: `git apply --check ...`). 순수 diff
    생성처럼 커맨드를 실행하지 않은 경우는 빈 문자열."""

    stdout: str = ""

    stderr: str = ""

    returncode: int | None = None

    def log_text(self, max_chars: int = 4000) -> str:
        """실패 로그를 Planner 재계획/Tool 결과에 넣기 좋은 형태로 요약."""

        status = "OK" if self.success else "FAILED"

        combined = (self.stderr or "") + (
            ("\n" + self.stdout) if self.stdout else ""
        )
        combined = combined.strip()

        if len(combined) > max_chars:
            head = combined[: max_chars // 2]
            tail = combined[-max_chars // 2 :]
            combined = f"{head}\n...(생략)...\n{tail}"

        header = f"[patch] {status}"

        if self.command:
            header += f" (returncode={self.returncode}, cmd=`{self.command}`)"

        if self.message:
            header += f"\n{self.message}"

        if not combined:
            return header

        return f"{header}\n{combined}"
