"""Auto Verify 결과 모델."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CheckResult:
    """개별 검증(빌드/테스트/린트) 1회의 결과."""

    tool: str
    """예: gradle, maven, pytest, flake8, mypy, spotless, eslint"""

    command: str
    """실제 실행한 커맨드 (로그 및 재현용)"""

    passed: bool

    returncode: int | None = None

    stdout: str = ""

    stderr: str = ""

    skipped: bool = False

    skip_reason: str = ""

    duration: float = 0.0

    def log_text(self, max_chars: int = 4000) -> str:
        """실패 로그를 Planner 재계획에 넣기 좋은 형태로 요약."""

        if self.skipped:
            return f"[{self.tool}] SKIPPED ({self.skip_reason})"

        status = "PASSED" if self.passed else "FAILED"

        combined = (self.stderr or "") + (
            ("\n" + self.stdout) if self.stdout else ""
        )
        combined = combined.strip()

        if len(combined) > max_chars:
            head = combined[: max_chars // 2]
            tail = combined[-max_chars // 2 :]
            combined = (
                f"{head}\n...(생략)...\n{tail}"
            )

        header = (
            f"[{self.tool}] {status} "
            f"(returncode={self.returncode}, cmd=`{self.command}`)"
        )

        if not combined:
            return header

        return f"{header}\n{combined}"


@dataclass(slots=True)
class VerifyReport:
    """한 번의 Auto Verify 실행 전체 결과."""

    results: list[CheckResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """실제로 검증을 실행했고, 실행된 검증이 모두 통과했는지 여부."""
        ran = [r for r in self.results if not r.skipped]
        if not ran:
            return False
        return all(r.passed for r in ran)

    @property
    def status(self) -> str:
        """Three-state verification status: passed / failed / not_run."""
        if not self.results or all(r.skipped for r in self.results):
            return "not_run"
        return "passed" if self.success else "failed"

    @property
    def failures(self) -> list[CheckResult]:
        return [
            r for r in self.results
            if not r.skipped and not r.passed
        ]

    @property
    def ran_tools(self) -> list[str]:
        return [r.tool for r in self.results if not r.skipped]

    def failure_log(self, max_chars_per_check: int = 4000) -> str:
        """Planner 재계획 입력으로 넘길 실패 로그 텍스트를 만든다."""

        failures = self.failures

        if not failures:
            return ""

        parts = [
            f.log_text(max_chars=max_chars_per_check)
            for f in failures
        ]

        return "\n\n".join(parts)

    def summary(self) -> str:
        """사람이 읽기 좋은 한 줄 요약."""

        if not self.results:
            return "적용 가능한 검증이 없어 아무것도 실행하지 않았습니다."

        lines = []
        for r in self.results:
            if r.skipped:
                lines.append(f"- {r.tool}: SKIP ({r.skip_reason})")
            elif r.passed:
                lines.append(f"- {r.tool}: PASS")
            else:
                lines.append(f"- {r.tool}: FAIL")

        return "\n".join(lines)
