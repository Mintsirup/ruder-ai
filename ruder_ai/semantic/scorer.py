"""Semantic Scorer."""

from __future__ import annotations


class SemanticScorer:

    EXACT = 30
    PARTIAL = 10

    def score(
        self,
        query_tokens: set[str],
        target_tokens: set[str],
    ) -> int:

        score = 0

        for token in query_tokens:

            if token in target_tokens:
                score += self.EXACT
                continue

            for target in target_tokens:

                if token in target:
                    score += self.PARTIAL

                elif target in token:
                    score += self.PARTIAL

        return score
