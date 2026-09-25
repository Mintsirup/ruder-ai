"""Reflection Prompt 개선 단위 테스트.

TODO.md P2 "Reflection > Reflection Prompt 개선" 항목 검증:
`core/reflection.py::Reflector._file_contents_block`이
- 실제 변경된 파일 내용을 프롬프트에 포함하는지
- REVIEW_FILE_CONTENT_BUDGET을 넘으면 head+tail로 압축하는지
- 존재하지 않는(삭제된) 파일은 예외 없이 안내 문구로 처리하는지
확인한다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ruder_ai.core.reflection import Reflector


class FakeLLM:
    """critic 프롬프트(messages)를 그대로 기록만 하는 가짜 LLM."""

    def __init__(self, response: str = "OK"):
        self.response = response
        self.last_messages: list[dict[str, str]] | None = None

    async def chat(self, messages):
        self.last_messages = messages
        return self.response


def run(coro):
    return asyncio.run(coro)


def test_file_contents_included_in_prompt(tmp_path: Path):
    target = tmp_path / "target.py"
    target.write_text(
        "def hello():\n    return 'this is a very specific marker'\n",
        encoding="utf-8",
    )

    llm = FakeLLM("OK")
    reflector = Reflector(llm=llm, workspace_path=tmp_path)

    run(
        reflector.review(
            task="hello 함수를 추가해줘",
            final_answer="hello 함수를 추가했습니다.",
            changed_files=["target.py"],
        )
    )

    assert llm.last_messages is not None
    user_content = llm.last_messages[1]["content"]

    # 파일명뿐 아니라 실제 파일 내용(마커 문자열)까지 프롬프트에
    # 포함되어야 한다.
    assert "target.py" in user_content
    assert "this is a very specific marker" in user_content


def test_file_contents_compressed_when_over_budget(tmp_path: Path):
    # REVIEW_FILE_CONTENT_BUDGET(3000 토큰)을 확실히 넘도록 큰 파일을 만든다.
    # ASCII 기준 4자당 1토큰 근사이므로 20000자 정도면 충분히 넘는다.
    head_marker = "HEAD_MARKER_" + ("a" * 50)
    tail_marker = "TAIL_MARKER_" + ("z" * 50)
    middle = "x" * 20000

    big_file = tmp_path / "big.py"
    big_file.write_text(
        f"{head_marker}\n{middle}\n{tail_marker}\n",
        encoding="utf-8",
    )

    llm = FakeLLM("OK")
    reflector = Reflector(llm=llm, workspace_path=tmp_path)

    run(
        reflector.review(
            task="big.py를 수정해줘",
            final_answer="수정했습니다.",
            changed_files=["big.py"],
        )
    )

    user_content = llm.last_messages[1]["content"]

    assert "생략: 예산 초과로 압축됨" in user_content
    # head/tail 압축 방식이므로 프롬프트 전체 길이는 원본보다 훨씬 짧아야 한다.
    assert len(user_content) < len(middle)


def test_missing_file_reported_without_crashing(tmp_path: Path):
    llm = FakeLLM("OK")
    reflector = Reflector(llm=llm, workspace_path=tmp_path)

    result = run(
        reflector.review(
            task="deleted.py를 정리해줘",
            final_answer="삭제했습니다.",
            changed_files=["deleted.py"],
        )
    )

    # 존재하지 않는 파일이어도 예외 없이 review가 끝나야 한다.
    assert result.passed is True

    user_content = llm.last_messages[1]["content"]
    assert "deleted.py" in user_content
    assert "삭제됨" in user_content or "존재하지 않음" in user_content
