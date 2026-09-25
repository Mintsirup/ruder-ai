"""Semantic Token Cache."""

from __future__ import annotations

from .engine import SemanticEngine


class SemanticCache:

    def __init__(self):

        self.engine = SemanticEngine()

    def build(
        self,
        symbol_name: str,
    ) -> set[str]:

        return self.engine.expand(
            symbol_name
        )
