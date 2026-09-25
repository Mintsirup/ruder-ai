"""Snippet Builder."""

from __future__ import annotations

import re
from pathlib import Path


class SnippetBuilder:
    """
    필요한 코드 일부만 추출한다.

    - target line 주변만 추출
    - Java/Python 메서드 경계 탐색
    - 너무 큰 파일은 잘라낸다.
    """

    def __init__(
        self,
        context_lines: int = 30,
        max_lines: int = 180,
    ):
        self.context_lines = context_lines
        self.max_lines = max_lines

    def build(
        self,
        file: Path,
        target_line: int | None = None,
    ) -> str:

        try:
            lines = file.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()

        except Exception:
            return ""

        if not lines:
            return ""

        # 위치를 모르면 파일 앞부분만
        if target_line is None:

            return "\n".join(
                lines[: self.max_lines]
            )

        target = max(
            0,
            min(target_line - 1, len(lines) - 1),
        )

        start = self._find_block_start(
            lines,
            target,
        )

        end = self._find_block_end(
            lines,
            target,
        )

        start = max(
            0,
            start - self.context_lines,
        )

        end = min(
            len(lines),
            end + self.context_lines,
        )

        # 너무 길면 다시 줄인다.

        if end - start > self.max_lines:

            half = self.max_lines // 2

            start = max(
                0,
                target - half,
            )

            end = min(
                len(lines),
                start + self.max_lines,
            )

        return "\n".join(
            lines[start:end]
        )

    def _find_block_start(
        self,
        lines: list[str],
        target: int,
    ) -> int:

        java_pattern = re.compile(
            r"(class|interface|enum|record|public|private|protected)"
        )

        python_pattern = re.compile(
            r"^\s*(class|def)\s+"
        )

        for i in range(target, -1, -1):

            line = lines[i]

            if java_pattern.search(line):
                return i

            if python_pattern.search(line):
                return i

        return max(
            0,
            target - self.context_lines,
        )

    def _find_block_end(
        self,
        lines: list[str],
        target: int,
    ) -> int:

        braces = 0
        started = False

        for i in range(target, len(lines)):

            line = lines[i]

            braces += line.count("{")
            braces -= line.count("}")

            if "{" in line:
                started = True

            if started and braces <= 0:
                return i + 1

            # Python 함수 끝 추정

            if (
                started is False
                and i > target + 2
                and line.strip() == ""
            ):
                return i

        return min(
            len(lines),
            target + self.context_lines,
        )
