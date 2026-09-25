"""Reference Index."""

from __future__ import annotations

import re
from collections import defaultdict

from .models import ProjectIndex


class ReferenceIndex:
    """
    심볼이 어디에서 사용되는지 저장한다.
    """

    def __init__(self):

        self.references = defaultdict(list)

    def build(
        self,
        index: ProjectIndex,
    ):

        self.references.clear()

        workspace = index.workspace

        symbol_names = {
            symbol.name
            for symbol in index.symbols
        }

        pattern = re.compile(
            r"[A-Za-z_][A-Za-z0-9_]*"
        )

        for file in index.files:

            absolute = (
                workspace
                / file.relative_path
            )

            try:

                lines = absolute.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines()

            except Exception:
                continue

            for line_no, line in enumerate(
                lines,
                start=1,
            ):

                for token in pattern.findall(line):

                    if token not in symbol_names:
                        continue

                    self.references[token].append(
                        (
                            file.relative_path,
                            line_no,
                        )
                    )
                    
    def find_references(
        self,
        symbol: str,
        limit: int = 30,
    ) -> list[tuple[str, int]]:
        """
        심볼이 사용된 위치를 반환한다.

        Returns:
            [
                ("src/Main.java", 25),
                ("src/GameCommand.java", 61),
            ]
        """

        refs = self.references.get(symbol)

        if refs is None:
            return []

        return refs[:limit]
