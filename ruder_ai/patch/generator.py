"""Unified diff 생성기.

TODO.md의 "Patch Generator" 항목 구현.

difflib만 사용해서 `git apply`가 그대로 소비할 수 있는 unified diff
텍스트를 만든다. 세 가지 경우를 모두 지원한다:

- 파일 수정: 기존 내용(old_content)과 새 내용(new_content)이 모두 있음.
- 신규 생성: old_content가 None (원본 없음 -> `--- /dev/null`).
- 삭제: new_content가 None (결과 없음 -> `+++ /dev/null`).

이 클래스는 파일 IO를 하지 않는다 — 문자열만 받아서 diff 문자열을
반환한다. 실제로 디스크의 파일을 읽거나 diff를 적용하는 것은
`PatchApplier`/Tool(skills/patch_ops.py)의 몫이다.
"""

from __future__ import annotations

import difflib


class PatchGenerator:
    """old/new 텍스트로부터 `git apply` 호환 unified diff를 만든다."""

    def generate(
        self,
        file_path: str,
        old_content: str | None,
        new_content: str | None,
    ) -> str:
        """`file_path` 하나에 대한 unified diff 텍스트를 생성한다.

        - old_content, new_content 둘 다 값이 있으면: 수정 diff
        - old_content가 None이면: 신규 생성 diff (`--- /dev/null`)
        - new_content가 None이면: 삭제 diff (`+++ /dev/null`)
        - 둘 다 None이거나 내용이 동일하면: 빈 문자열(변경 없음)
        """

        if old_content is None and new_content is None:
            return ""

        if old_content == new_content:
            return ""

        old_lines = (
            old_content.splitlines(keepends=True)
            if old_content is not None
            else []
        )
        new_lines = (
            new_content.splitlines(keepends=True)
            if new_content is not None
            else []
        )

        from_file = (
            "/dev/null" if old_content is None else f"a/{file_path}"
        )
        to_file = (
            "/dev/null" if new_content is None else f"b/{file_path}"
        )

        diff_lines = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=from_file,
            tofile=to_file,
        )

        text = "".join(diff_lines)

        if not text:
            return ""

        if not text.endswith("\n"):
            text += "\n"

        return text
