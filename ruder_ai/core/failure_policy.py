from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FailureDecision:
    action: str  # retry | replan | stop
    retryable: bool
    reason: str


class FailurePolicy:
    """Single place for execution failure classification and recovery policy."""

    RETRYABLE = {"timeout", "network", "unknown"}
    REPLAN_REQUIRED = {"not_found", "permission", "validation", "failed"}

    @classmethod
    def decide(cls, *, error_type: str | None, attempt: int, max_retries: int, max_replans: int, replans: int) -> FailureDecision:
        kind = str(error_type or "unknown").lower()
        if kind in cls.RETRYABLE and attempt < max_retries:
            return FailureDecision("retry", True, f"일시적 오류({kind}) 재시도")
        if replans < max_replans:
            return FailureDecision("replan", kind in cls.RETRYABLE, f"재계획 필요({kind})")
        return FailureDecision("stop", False, f"복구 한도 초과({kind})")
