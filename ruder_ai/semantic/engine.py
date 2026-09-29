"""Semantic Search Engine."""

from __future__ import annotations

from .alias import ALIASES
from .normalizer import SemanticNormalizer
from .scorer import SemanticScorer
from .tokenizer import SemanticTokenizer


class SemanticEngine:

    def __init__(self):

        self.tokenizer = SemanticTokenizer()
        self.normalizer = SemanticNormalizer()
        self.scorer = SemanticScorer()

    def expand(
        self,
        text: str,
    ) -> set[str]:

        tokens = self.tokenizer.tokenize(text)

        result = set()

        for token in tokens:

            token = self.normalizer.normalize(token)

            result.add(token)

            if token in ALIASES:
                result.update(ALIASES[token])

        return result

    def similarity(
        self,
        query: str,
        target: str,
    ) -> int:

        q = self.expand(query)
        t = self.expand(target)

        return self.scorer.score(
            q,
            t,
        )

    def expand_tokens(
        self,
        text: str,
    ) -> set[str]:

        return self.expand(text)
