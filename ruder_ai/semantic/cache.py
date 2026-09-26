"""Semantic Token Cache."""

from __future__ import annotations

from collections import OrderedDict

from .engine import SemanticEngine

#: Bound on the memo. Symbol names repeat heavily in a real project
#: (``__init__``, ``run``, ``toString``, and one entry per file for the file's
#: own relative path), so a small cache absorbs most of the work.
_MAX_ENTRIES = 8192


class SemanticCache:

    def __init__(self):

        self.engine = SemanticEngine()
        self._entries: OrderedDict[str, frozenset[str]] = OrderedDict()

    def build(
        self,
        symbol_name: str,
    ) -> set[str]:
        """Expand ``symbol_name`` into its semantic token set.

        The class was named "cache" but held no state: the scanner calls this
        once per file, the symbol indexer once per file *and* once per symbol,
        and the planner calls ``expand_tokens`` on every prompt. Re-tokenizing
        the same string hundreds of times per project build is pure waste, and
        it is deterministic, so it memoizes.

        A fresh ``set`` is handed out on every call so a caller that mutates
        the result cannot corrupt the memo.
        """
        cached = self._entries.get(symbol_name)
        if cached is None:
            cached = frozenset(
                self.engine.expand(symbol_name)
            )
            self._entries[symbol_name] = cached
            if len(self._entries) > _MAX_ENTRIES:
                self._entries.popitem(last=False)
        return set(cached)

    def clear(self) -> None:
        self._entries.clear()
