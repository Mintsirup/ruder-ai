"""Reflector 단위 테스트.

기존 tests/*.py 들과 달리, 실제 pytest 컨벤션(def test_...(): assert ...)을
따르고, 네트워크(Ollama)나 실제 workspace 스캔에 의존하지 않는다.
LLM 호출은 FakeLLM으로 대체해서 결정적으로 검증한다.
"""

from __future__ import annotations

import asyncio

from ruder_ai.core.executor import ToolExecutor
from ruder_ai.core.reflection import Reflector


class FakeLLM:
    """실제 Ollama 대신 미리 정해진 응답을 순서대로 반환하는 가짜 LLM."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if not self._responses:
            raise AssertionError("FakeLLM: 예상보다 많은 chat() 호출이 발생했습니다.")
        return self._responses.pop(0)


class FakeSkill:
    """write_file을 흉내내는 가짜 스킬. 항상 성공을 반환한다."""

    name = "write_file"

    async def execute(self, **kwargs):
        return {"status": "success", "message": "written"}


class FakeSkillRegistry:
    def __init__(self, skill):
        self._skill = skill

    def get_skill(self, name):
        if name == self._skill.name:
            return self._skill
        return None


def run(coro):
    """asyncio 테스트 헬퍼 (pytest-asyncio 의존성 없이 실행)."""
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# Reflector 단독 테스트
# ----------------------------------------------------------------------

def test_review_passes_when_llm_says_ok(tmp_path):
    good_file = tmp_path / "ok.py"
    good_file.write_text("def f():\n    return 1\n", encoding="utf-8")

    reflector = Reflector(
        llm=FakeLLM(["OK"]),
        workspace_path=tmp_path,
    )

    result = run(
        reflector.review(
            task="f 함수를 추가해줘",
            final_answer="f 함수를 추가했습니다.",
            changed_files=["ok.py"],
        )
    )

    assert result.passed is True


def test_review_fails_when_llm_flags_issue(tmp_path):
    good_file = tmp_path / "ok.py"
    good_file.write_text("def f():\n    return 1\n", encoding="utf-8")

    reflector = Reflector(
        llm=FakeLLM(["ISSUE: 요청한 두 번째 함수 g가 빠졌습니다."]),
        workspace_path=tmp_path,
    )

    result = run(
        reflector.review(
            task="f, g 함수를 추가해줘",
            final_answer="f 함수를 추가했습니다.",
            changed_files=["ok.py"],
        )
    )

    assert result.passed is False
    assert "g" in result.feedback


def test_review_catches_syntax_error_without_calling_llm(tmp_path):
    broken_file = tmp_path / "broken.py"
    broken_file.write_text("def f(:\n    pass\n", encoding="utf-8")

    llm = FakeLLM([])  # LLM이 호출되면 AssertionError로 실패해야 함

    reflector = Reflector(
        llm=llm,
        workspace_path=tmp_path,
    )

    result = run(
        reflector.review(
            task="f 함수를 고쳐줘",
            final_answer="수정했습니다.",
            changed_files=["broken.py"],
        )
    )

    assert result.passed is False
    assert "broken.py" in result.feedback
    assert llm.calls == []  # 구문 오류는 LLM 호출 없이 바로 걸러져야 함


def test_review_skips_llm_when_no_files_changed(tmp_path):
    llm = FakeLLM([])

    reflector = Reflector(
        llm=llm,
        workspace_path=tmp_path,
    )

    result = run(
        reflector.review(
            task="이 프로젝트 구조 설명해줘",
            final_answer="이 프로젝트는 ...",
            changed_files=[],
        )
    )

    assert result.passed is True
    assert llm.calls == []


# ----------------------------------------------------------------------
# Executor + Reflector 통합 테스트
# ----------------------------------------------------------------------

def test_executor_retries_once_after_failed_reflection(tmp_path):
    target = tmp_path / "target.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")

    # LLM 호출 순서:
    # 1) write_file 도구 호출 (JSON) -> 파일 씀
    # 2) 최종 답변 (도구 호출 없음) -> reflector가 ISSUE 판정
    # 3) 재시도 최종 답변 -> reflector가 OK 판정
    agent_llm = FakeLLM(
        [
            '```json\n{"tool": "write_file", "kwargs": '
            '{"file_path": "target.py", "content": "x"}}\n```',
            "f 함수를 수정했습니다.",
            "g 함수까지 추가해서 수정했습니다.",
        ]
    )
    reflector_llm = FakeLLM(["ISSUE: g 함수가 빠졌습니다.", "OK"])

    reflector = Reflector(
        llm=reflector_llm,
        workspace_path=tmp_path,
        max_reflections=2,
    )

    executor = ToolExecutor(
        llm=agent_llm,
        skill_registry=FakeSkillRegistry(FakeSkill()),
        workspace_path=str(tmp_path),
        max_steps=5,
        reflector=reflector,
    )

    messages = [{"role": "user", "content": "f, g 함수를 추가해줘"}]

    final = run(
        executor.run(messages, task="f, g 함수를 추가해줘")
    )

    assert final == "g 함수까지 추가해서 수정했습니다."
    assert len(reflector_llm.calls) == 2  # 실패 1번 + 통과 1번


def test_executor_gives_up_after_max_reflections(tmp_path):
    target = tmp_path / "target.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")

    agent_llm = FakeLLM(
        [
            '```json\n{"tool": "write_file", "kwargs": '
            '{"file_path": "target.py", "content": "x"}}\n```',
            "1차 답변",  # reflection에서 ISSUE 판정 -> 재시도 소진
            "2차 답변",  # max_reflections(1) 소진했으므로 이 답변이 그대로 반환됨
        ]
    )
    # max_reflections=1이므로 reflector는 정확히 1번만 호출되어야 한다.
    reflector_llm = FakeLLM(["ISSUE: 여전히 부족합니다."])

    reflector = Reflector(
        llm=reflector_llm,
        workspace_path=tmp_path,
        max_reflections=1,
    )

    executor = ToolExecutor(
        llm=agent_llm,
        skill_registry=FakeSkillRegistry(FakeSkill()),
        workspace_path=str(tmp_path),
        max_steps=5,
        reflector=reflector,
    )

    messages = [{"role": "user", "content": "질문"}]

    final = run(executor.run(messages, task="질문"))

    assert final == "2차 답변"
    assert len(reflector_llm.calls) == 1


def test_executor_without_reflector_behaves_as_before(tmp_path):
    """reflector=None이면 기존 동작(하위 호환)을 그대로 유지해야 한다."""

    agent_llm = FakeLLM(["그냥 답변"])

    executor = ToolExecutor(
        llm=agent_llm,
        skill_registry=FakeSkillRegistry(FakeSkill()),
        workspace_path=str(tmp_path),
        max_steps=5,
        reflector=None,
    )

    messages = [{"role": "user", "content": "질문"}]

    final = run(executor.run(messages, task="질문"))

    assert final == "그냥 답변"
