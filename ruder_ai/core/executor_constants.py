"""Shared Tool-failure vocabulary for the executor.

These live at module scope rather than only on ``ToolExecutor`` because
``@staticmethod`` bodies have no ``self`` to reach a class attribute through,
and the retry/classification mixin needs them. ``ToolExecutor`` imports and
re-exports every name here as a class attribute, so the existing
``ToolExecutor.ERROR_TYPE_*`` public API is unchanged.
"""

ERROR_TYPE_TIMEOUT = "timeout"
ERROR_TYPE_NOT_FOUND = "not_found"
ERROR_TYPE_PERMISSION = "permission"
ERROR_TYPE_VALIDATION = "validation"
ERROR_TYPE_NETWORK = "network"
ERROR_TYPE_UNKNOWN = "unknown"

# 이 유형들은 "결정적" 실패로 본다 — 입력을 바꾸지 않는 한 몇 번을
# 다시 실행해도 같은 결과이므로 Tool Retry 대상에서 제외한다.
# timeout/network/unknown은 일시적일 수 있어 그대로 재시도 대상.
NON_RETRYABLE_ERROR_TYPES = frozenset(
    {
        ERROR_TYPE_NOT_FOUND,
        ERROR_TYPE_PERMISSION,
        ERROR_TYPE_VALIDATION,
    }
)
