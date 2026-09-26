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
        append = result.append
        camel = self.CAMEL.findall

        for token in self.WORD.findall(text):

            lower = token.lower()

            append(lower)

            if "_" in lower:
                result.extend(
                    part
                    for part in lower.split("_")
                    if part
                )

            # The CAMEL pattern can only produce something the branches above
            # did not already produce when the token has an uppercase letter, a
            # digit or an underscore in it - "foo1bar" splits into foo/bar, but
            # a plain lowercase "foobar" can only match itself. Three cheap
            # predicate calls replace a regex run for the common case.
            if not (token.islower() and token.isascii() and token.isalpha()):
                for part in camel(token):
                    append(part.lower())

        return list(dict.fromkeys(result))
