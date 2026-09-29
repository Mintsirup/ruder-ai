"""Error classification, retry policy and failure formatting."""

from __future__ import annotations

import re
from typing import Any

from ruder_ai.core import executor_constants as constants


class ErrorsMixin:
    """Error classification, retry policy and failure formatting."""

    @staticmethod
    def _is_tool_result_success(result: Any) -> bool:
        """Tool 실행 결과가 성공인지 판단한다.

        Retry Loop: `verify_project`는 실패해도 예외 없이
        `{"status": "failed", ...}`를 반환하므로(빌드/테스트 실패는
        "에러"가 아니라 "검증 결과"이기 때문), "error"뿐 아니라
        "failed"도 실패로 취급해야 재계획(replan) 경로를 탄다.
        """

        if not isinstance(result, dict):
            return True

        return result.get("status") not in ("error", "failed")

    @staticmethod
    def _format_task_failure(pt, result: Any) -> str:

        if isinstance(result, dict):

            # verify_project 실패는 summary + failure_log(빌드/테스트 실패
            # 로그)가 훨씬 유용한 재계획 입력이 되므로 우선 사용한다.
            parts = [
                part
                for part in (
                    result.get("summary"),
                    result.get("failure_log"),
                )
                if part
            ]

            message = "\n".join(parts) if parts else (
                result.get("message", str(result))
            )

            error_type = result.get("error_type")
            if error_type:
                message = f"[오류 유형: {error_type}] {message}"

        else:
            message = str(result)

        return (
            f"Task {pt.order} ('{pt.description}')에서 "
            f"'{pt.tool}' Tool 실행이 실패했습니다: {message}"
        )

    def _timeout_for(self, tool_name: str) -> float | None:
        """이 Tool에 적용할 제한 시간(초)을 반환한다. None이면 무제한."""

        if tool_name in self.tool_timeout_overrides:
            return self.tool_timeout_overrides[tool_name]

        return self.tool_timeout

    def _retries_for(self, tool_name: str) -> int:
        """이 Tool에 적용할 "최초 시도 외 추가 재시도" 횟수를 반환한다."""

        if tool_name in self.tool_retry_overrides:
            return self.tool_retry_overrides[tool_name]

        return self.tool_retries

    @staticmethod
    def _is_retryable_failure(result: Any) -> bool:
        """Tool Retry: 재시도할 가치가 있는 실패인지 판단한다.

        status == "failed"(예: verify_project의 빌드/테스트 실패)는
        코드를 고치지 않는 한 몇 번을 다시 실행해도 같은 결과이므로
        재시도 대상이 아니다 — 이런 실패는 Retry Loop의 replan으로
        보내는 게 맞다. status == "error" 중에서도, Tool Error
        Classification 결과가 NON_RETRYABLE_ERROR_TYPES(파일 없음/
        권한 없음/잘못된 kwargs 등 결정적 실패)에 해당하면 마찬가지로
        재시도하지 않는다 — 입력이 그대로인 한 몇 번을 다시 불러도
        똑같이 실패하기 때문이다. 그 외(timeout, network, unknown 등
        일시적일 수 있는 실패)만 재시도 대상으로 본다.
        """

        if not isinstance(result, dict):
            return False

        if result.get("status") != "error":
            return False

        return (
            result.get("error_type")
            not in constants.NON_RETRYABLE_ERROR_TYPES
        )

    @staticmethod
    def _classify_error(exc: Exception | None, message: str) -> str:
        """예외/에러 메시지를 보고 대략적인 에러 유형을 분류한다.

        완벽한 분류가 목표가 아니다 — 애매하면 "unknown"으로 두고
        기존처럼 재시도 대상으로 취급한다 (거짓 확신으로 재시도
        기회를 뺏지 않기 위함). 예외 타입이 있으면 그걸 우선으로,
        없으면(Tool 자체가 예외 없이 status=error dict를 반환한
        경우) 메시지 텍스트의 키워드로 추정한다.
        """

        if exc is not None:

            if isinstance(exc, FileNotFoundError):
                return constants.ERROR_TYPE_NOT_FOUND

            if isinstance(exc, PermissionError):
                return constants.ERROR_TYPE_PERMISSION

            if isinstance(exc, (TypeError, ValueError, KeyError)):
                return constants.ERROR_TYPE_VALIDATION

            if isinstance(exc, (ConnectionError, TimeoutError)):
                return constants.ERROR_TYPE_NETWORK

        text = (message or "").lower()

        not_found_kw = (
            "no such file", "not found", "does not exist",
            "존재하지 않", "찾을 수 없",
        )
        permission_kw = (
            "permission denied", "access denied", "access is denied",
            "권한이 없", "권한 거부",
        )
        network_kw = (
            "connection", "network", "dns", "unreachable",
            "timed out", "네트워크", "연결",
        )
        validation_kw = (
            "invalid", "missing required", "unexpected keyword",
            "required positional argument", "required kwargs",
            "필수 kwargs", "필수 필드", "argument required",
            "잘못된", "형식이 올바르지", "필수 필드 누락",
            "old_str", "diff가 필요",
        )
        not_repo_kw = (
            "not a git repository", "not a repository",
            "git 저장소가 아니", "git repository",
        )

        # Patch/diff-specific "not found" messages are validation errors:
        # the target file may exist; only the requested patch context is absent.
        if "old_str" in text and any(x in text for x in ("not found", "찾을 수 없")):
            return constants.ERROR_TYPE_VALIDATION

        if any(kw in text for kw in permission_kw):
            return constants.ERROR_TYPE_PERMISSION

        if any(kw in text for kw in network_kw):
            return constants.ERROR_TYPE_NETWORK

        if any(kw in text for kw in validation_kw):
            return constants.ERROR_TYPE_VALIDATION

        if any(kw in text for kw in not_found_kw):
            return constants.ERROR_TYPE_NOT_FOUND

        if any(kw in text for kw in not_repo_kw):
            return constants.ERROR_TYPE_VALIDATION

        if any(kw in text for kw in permission_kw):
            return constants.ERROR_TYPE_PERMISSION

        if any(kw in text for kw in network_kw):
            return constants.ERROR_TYPE_NETWORK

        if any(kw in text for kw in validation_kw):
            return constants.ERROR_TYPE_VALIDATION

        return constants.ERROR_TYPE_UNKNOWN

    @staticmethod
    def _ensure_error_classified(
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """status=error인데 아직 error_type이 없는 결과에 분류를
        붙인다 (timeout처럼 이미 분류를 붙여서 반환한 경로는 건드리지
        않는다)."""

        if (
            isinstance(result, dict)
            and result.get("status") == "error"
            and "error_type" not in result
        ):
            result = dict(result)
            result["error_type"] = ErrorsMixin._classify_error(
                None, str(result.get("message", "")),
            )

        return result

    @staticmethod
    def _extract_failure_type(failure: str) -> str | None:
        text = str(failure or "")
        match = re.search(r"\[오류 유형: ([^\]]+)\]", text)
        if match:
            return match.group(1).strip()
        lowered = text.lower()
        if "not a git repository" in lowered or "not a repository" in lowered:
            return constants.ERROR_TYPE_VALIDATION
        if any(x in lowered for x in ("timed out", "timeout", "time-out")):
            return constants.ERROR_TYPE_TIMEOUT
        if any(x in lowered for x in ("connection", "network", "dns", "unreachable")):
            return constants.ERROR_TYPE_NETWORK
        if any(x in lowered for x in ("permission denied", "access is denied")):
            return constants.ERROR_TYPE_PERMISSION
        if any(x in lowered for x in ("no such file", "not found", "does not exist")):
            return constants.ERROR_TYPE_NOT_FOUND
        if any(x in lowered for x in ("missing required", "required kwargs", "old_str", "diff가 필요", "필수 필드")):
            return constants.ERROR_TYPE_VALIDATION
        return constants.ERROR_TYPE_UNKNOWN

    @staticmethod
    def _is_repeated_tool_sequence(
        previous_sequence: list[str],
        new_sequence: list[str],
    ) -> bool:
        """재계획으로 받은 Plan의 Tool sequence가 방금 실패한 Plan과
        완전히 동일한지 판단한다.

        빈 sequence(Tool 없이 순수 판단 Task만 있는 Plan)끼리는 비교
        의미가 없으므로(둘 다 빈 리스트면 항상 "같다"고 오판하게 됨)
        비교 대상에서 제외한다 — 반복 감지는 실제로 Tool을 반복
        호출하는 경우에만 의미가 있다.
        """
        if not previous_sequence:
            return False
        return previous_sequence == new_sequence

    @staticmethod
    def _format_repeated_plan_failure(
        replans: int,
        max_replans: int,
        tool_sequence: list[str],
        failure: str,
    ) -> str:
        sequence_text = " → ".join(tool_sequence) if tool_sequence else "(없음)"
        return (
            "⚠️ 재계획을 요청했지만 이전과 동일한 Tool 순서"
            f"({sequence_text})가 다시 나와, 같은 실패가 반복될 것으로 "
            "판단해 더 이상 재계획을 시도하지 않고 종료합니다 "
            f"(재계획 {replans}/{max_replans}회 시점에 동일 계획 감지):\n"
            f"{failure}"
        )
