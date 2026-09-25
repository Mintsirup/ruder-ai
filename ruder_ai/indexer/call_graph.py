"""Call Graph Builder."""

from __future__ import annotations

import re
from collections import defaultdict

from .models import ProjectIndex


class CallGraph:
    """
    함수 호출 관계를 저장한다.

    startGame
      ↓
    resetPlayer
      ↓
    updateScoreboard
    """

    def __init__(self):
        self.calls = defaultdict(set)
        self.called_by = defaultdict(set)

    def build(
        self,
        index: ProjectIndex,
    ) -> None:

        self.calls.clear()
        self.called_by.clear()

        workspace = index.workspace

        for file in index.files:

            if file.extension != ".java":
                continue

            absolute = workspace / file.relative_path

            try:
                text = absolute.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except Exception:
                continue

            current_method = None

            for line in text.splitlines():

                method = re.search(
                    r"(?:public|private|protected|static|\s)+.*?([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                    line,
                )

                if method:
                    current_method = method.group(1)
                    continue

                if current_method is None:
                    continue

                for call in re.findall(
                    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                    line,
                ):
                    self.calls[current_method].add(call)
                    self.called_by[call].add(current_method)

    def find_related(
        self,
        symbol: str,
        depth: int = 1,
    ) -> list[str]:
        """
        호출 관계를 따라 관련 메서드를 찾는다.
        """

        visited = set()
        result = set()

        def dfs(node: str, remain: int):
            if remain < 0:
                return

            if node in visited:
                return

            visited.add(node)

            for nxt in self.calls.get(node, ()):
                result.add(nxt)
                dfs(nxt, remain - 1)

        dfs(symbol, depth)

        return sorted(result)

    def find_callers(
        self,
        symbol: str,
        depth: int = 1,
    ) -> list[str]:
        """
        해당 메서드를 호출하는 메서드들을 찾는다.
        """

        visited = set()
        result = set()

        def dfs(node: str, remain: int):
            if remain < 0:
                return

            if node in visited:
                return

            visited.add(node)

            for caller in self.called_by.get(node, ()):
                result.add(caller)
                dfs(caller, remain - 1)

        dfs(symbol, depth)

        return sorted(result)
