"""Tool Retry 단위 테스트.

TODO.md P2 "Executor > Tool Retry" 항목 검증:
`status == "error"`(예외/timeout 등 인프라성 일시적 실패)만
`_execute_tool`이 재시도하고, `status == "failed"`(예:
verify_project의 빌드/테스트 실패)는 재시도하지 않고 그대로 반환하는지,
재시도를 다 써도 계속 실패하면 무한루프 없이 마지막 실패를 반환하는지,
`verify_project`는 override(기본 재시도 0회)가 실제로 적용되는지,
생성자로 넘긴 override가 반영되는지를 확인한다.

Tool Timeout 기본값(120초)과 섞이지 않도록 timeout이 걸리지 않게
짧게 끝나는 가짜 Tool만 사용한다.
"""

from __future__ import annotations

import asyncio

from ruder_ai.core.executor import ToolExecutor


class FlakySkill:
    """`fail_times`번은 status=error를 반환하다가 이후에는 성공하는 가짜 Tool."""

    description = "일시적으로 실패하다 성공하는 가짜 Tool (테스트 전용)"

    def __init__(self, name: str, fail_times: int, error_type: str | None = None):
        self.name = name
        self.fail_times = fail_times
        self.error_type = error_type
        self.call_count = 0

    async def execute(self, **kwargs):
        self.call_count += 1
        if self.call_count <= self.fail_times:
            result = {"status": "error", "message": "일시적인 오류"}
            if self.error_type:
                result["error_type"] = self.error_type
            return result
        return {"status": "success", "message": "완료"}


class AlwaysFailSkill:
    description = "항상 실패하는 가짜 Tool (테스트 전용)"

    def __init__(self, name: str):
        self.name = name
        self.call_count = 0

    async def execute(self, **kwargs):
        self.call_count += 1
        return {"status": "error", "message": "영구적인 오류"}


class AlwaysFailedStatusSkill:
    """status='failed'(빌드/테스트 실패류)를 항상 반환하는 가짜 Tool."""

    description = "항상 failed 상태를 반환하는 가짜 Tool (테스트 전용)"

    def __init__(self, name: str):
        self.name = name
        self.call_count = 0

    async def execute(self, **kwargs):
        self.call_count += 1
        return {"status": "failed", "summary": "빌드 실패"}


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


def _executor(registry, **overrides) -> ToolExecutor:
    return ToolExecutor(
        llm=FakeLLM(),
        skill_registry=registry,
        workspace_path=".",
        tool_timeout=5.0,
        tool_retry_backoff=0.01,  # 테스트가 느려지지 않도록 짧게
        **overrides,
    )


def test_transient_failure_succeeds_after_retry():
    skill = FlakySkill("flaky", fail_times=2)
    registry = FakeSkillRegistry({"flaky": skill})

    executor = _executor(registry, tool_retries=2)

    result = run(executor._execute_tool("flaky", {}))

    assert result["status"] == "success"
    # 최초 1회 + 재시도 2회 중 2번째 재시도(=3번째 시도)에서 성공.
    assert skill.call_count == 3


def test_exhausted_retries_returns_last_failure_without_infinite_loop():
    skill = AlwaysFailSkill("always_fail")
    registry = FakeSkillRegistry({"always_fail": skill})

    executor = _executor(registry, tool_retries=2)

    result = run(executor._execute_tool("always_fail", {}))

    assert result["status"] == "error"
    # 최초 1회 + 재시도 2회 = 총 3회에서 멈춰야 한다 (무한루프 없음).
    assert skill.call_count == 3


def test_status_failed_is_not_retried():
    skill = AlwaysFailedStatusSkill("verify_like")
    registry = FakeSkillRegistry({"verify_like": skill})

    executor = _executor(registry, tool_retries=2)

    result = run(executor._execute_tool("verify_like", {}))

    assert result["status"] == "failed"
    # status=failed는 재시도 대상이 아니므로 정확히 1번만 호출된다.
    assert skill.call_count == 1


def test_verify_project_override_defaults_to_zero_retries():
    skill = AlwaysFailSkill("verify_project")
    registry = FakeSkillRegistry({"verify_project": skill})

    # 생성자에 tool_retries=2를 줘도, verify_project는 클래스 기본
    # TOOL_RETRY_OVERRIDES에 의해 재시도 0회로 고정되어야 한다.
    executor = _executor(registry, tool_retries=2)

    result = run(executor._execute_tool("verify_project", {}))

    assert result["status"] == "error"
    assert skill.call_count == 1


def test_constructor_retry_override_applies():
    skill = AlwaysFailSkill("custom_tool")
    registry = FakeSkillRegistry({"custom_tool": skill})

    executor = _executor(
        registry,
        tool_retries=0,
        tool_retry_overrides={"custom_tool": 3},
    )

    result = run(executor._execute_tool("custom_tool", {}))

    assert result["status"] == "error"
    # 기본은 0회지만, 이 Tool만 override로 3회 재시도 -> 총 4회 호출.
    assert skill.call_count == 4
