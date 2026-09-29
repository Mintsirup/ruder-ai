"""Semantic Normalizer."""

from __future__ import annotations


class SemanticNormalizer:
    """토큰을 정규화한다."""

    def normalize(self, token: str) -> str:

        token = token.lower()

        # 복수형 제거
        if token.endswith("ies"):
            return token[:-3] + "y"

        if token.endswith("es"):
            return token[:-2]

        if token.endswith("s") and len(token) > 3:
            return token[:-1]

        return token
