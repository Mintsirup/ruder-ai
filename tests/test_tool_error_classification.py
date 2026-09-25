"""Tool Error Classification 단위 테스트.

TODO.md P2 "Executor > Tool Error Classification" 항목 검증:
`core/executor.py::ToolExecutor._classify_error`가 대표 예외 4종을
올바르게 분류하는지, Tool이 예외 없이 status=error dict를 직접
반환하는 경우 메시지 키워드로 분류하는지, 애매한 메시지는 "unknown"
으로 남아 재시도 기회를 뺏지 않는지, timeout처럼 이미 분류가 붙은
결과는 `_ensure_error_classified`가 덮어쓰지 않는지, 알 수 없는 Tool
이름은 validation으로 분류되는지를 확인한다.
"""

from __future__ import annotations

import asyncio

from ruder_ai.core.executor import ToolExecutor


class RaisingSkill:
    """execute()가 항상 지정한 예외를 던지는 가짜 Tool."""

    description = "예외를 던지는 가짜 Tool (테스트 전용)"

    def __init__(self, name: str, exc: Exception):
        self.name = name
        self._exc = exc

    async def execute(self, **kwargs):
        raise self._exc


class ErrorDictSkill:
    """예외 없이 status=error dict를 직접 반환하는 가짜 Tool."""

    description = "status=error dict를 직접 반환하는 가짜 Tool (테스트 전용)"

    def __init__(self, name: str, message: str, error_type: str | None = None):
        self.name = name
        self._message = message
        self._error_type = error_type

    async def execute(self, **kwargs):
        result = {"status": "error", "message": self._message}
        if self._error_type:
            result["error_type"] = self._error_type
        return result


class FakeSkillRegistry:
    def __init__(self, skills: dict):
        self._skills = skills

    def get_skill(self, name):
        return self._skills.get(name)

    def list_skills(self):
        return {name: s.description for name, s in self._skills.items()}


class FakeLLM:
    async def chat(self, messages):
        raise AssertionError("이 테스트에서는 LLM이 호출되지 않아야 합니다.")


def run(coro):
    return asyncio.run(coro)


def _executor(registry) -> ToolExecutor:
    return ToolExecutor(
        llm=FakeLLM(),
        skill_registry=registry,
        workspace_path=".",
        tool_timeout=5.0,
        tool_retries=0,  # 재시도는 test_tool_retry.py에서 별도 검증
    )


# ---------------------------------------------------------------------------
# 예외 타입 기반 분류 (4종) — `_classify_error`를 직접 호출한다.
#
# `TimeoutError`(3.11+에서 `asyncio.TimeoutError`와 동일 클래스)는
# `_classify_error` 안에서는 network로 분류되도록 정의돼 있지만, 실제
# 실행 경로(`_execute_tool_once`)에서는 `asyncio.wait_for`의 timeout
# 처리가 먼저 가로채 "timeout"으로 반환된다 — 그건 이 파일이 아니라
# tests/test_tool_timeout.py가 검증하는 영역이라, 여기서는 분류 함수
# 자체의 동작만 직접 확인한다.
# ---------------------------------------------------------------------------


def test_file_not_found_error_classified_as_not_found():
    error_type = ToolExecutor._classify_error(
        FileNotFoundError("no such file"), "no such file"
    )
    assert error_type == "not_found"


def test_permission_error_classified_as_permission():
    error_type = ToolExecutor._classify_error(
        PermissionError("denied"), "denied"
    )
    assert error_type == "permission"


def test_value_type_key_errors_classified_as_validation():
    for exc in (ValueError("bad"), TypeError("bad"), KeyError("bad")):
        error_type = ToolExecutor._classify_error(exc, str(exc))
        assert error_type == "validation", exc


def test_connection_timeout_errors_classified_as_network():
    for exc in (ConnectionError("down"), TimeoutError("slow")):
        error_type = ToolExecutor._classify_error(exc, str(exc))
        assert error_type == "network", exc


def test_file_not_found_error_end_to_end_through_execute_tool():
    """예외 타입 분류가 실제 `_execute_tool` 경로에도 그대로 반영되는지."""

    skill = RaisingSkill("boom", FileNotFoundError("no such file"))
    registry = FakeSkillRegistry({"boom": skill})
    executor = _executor(registry)

    result = run(executor._execute_tool("boom", {}))

    assert result["status"] == "error"
    assert result["error_type"] == "not_found"


# ---------------------------------------------------------------------------
# status=error dict의 메시지 키워드 기반 분류
# ---------------------------------------------------------------------------


def test_error_dict_message_classified_by_keyword():
    skill = ErrorDictSkill("git_like", "파일이 존재하지 않습니다: foo.txt")
    registry = FakeSkillRegistry({"git_like": skill})
    executor = _executor(registry)

    result = run(executor._execute_tool("git_like", {}))

    assert result["status"] == "error"
    assert result["error_type"] == "not_found"


def test_ambiguous_message_stays_unknown():
    skill = ErrorDictSkill("mystery", "무언가 잘못됐습니다 (원인 불명)")
    registry = FakeSkillRegistry({"mystery": skill})
    executor = _executor(registry)

    result = run(executor._execute_tool("mystery", {}))

    assert result["error_type"] == "unknown"


def test_existing_error_type_is_not_overwritten():
    # timeout처럼 Tool/Executor가 이미 error_type을 붙여 반환한 경우,
    # _ensure_error_classified가 그 값을 덮어쓰면 안 된다.
    skill = ErrorDictSkill(
        "already_classified", "이미 분류된 에러", error_type="timeout"
    )
    registry = FakeSkillRegistry({"already_classified": skill})
    executor = _executor(registry)

    result = run(executor._execute_tool("already_classified", {}))

    assert result["error_type"] == "timeout"


def test_unknown_tool_classified_as_validation():
    registry = FakeSkillRegistry({})
    executor = _executor(registry)

    result = run(executor._execute_tool("no_such_tool", {}))

    assert result["status"] == "error"
    assert result["error_type"] == "validation"
