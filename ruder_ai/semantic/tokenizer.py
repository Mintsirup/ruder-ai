"""Semantic Tokenizer."""

from __future__ import annotations

import re


class SemanticTokenizer:
    """
    자연어와 코드 식별자를 공통 토큰으로 분리한다.
    """

    WORD = re.compile(
        r"[가-힣]+|[A-Za-z_][A-Za-z0-9_]*"
    )

    CAMEL = re.compile(
        r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)"
    )

    def tokenize(self, text: str) -> list[str]:

        result: list[str] = []

        for token in self.WORD.findall(text):

            lower = token.lower()

            result.append(lower)

            if "_" in lower:
                result.extend(
                    part
                    for part in lower.split("_")
                    if part
                )

            for part in self.CAMEL.findall(token):
                result.append(part.lower())

        return list(dict.fromkeys(result))
