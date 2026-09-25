"""Semantic File Index."""

from __future__ import annotations

from collections import defaultdict

from ruder_ai.indexer.models import (
    ProjectIndex,
)
from ruder_ai.semantic.scorer import (
    SemanticScorer,
)


class SemanticFileIndex:
    """
    파일 단위 Semantic Index.

    semantic token을 이용하여
    관련 파일을 빠르게 찾는다.
    """

    def __init__(self):

        self.index: dict[
            str,
            set[str],
        ] = defaultdict(set)

        self.file_tokens: dict[
            str,
            set[str],
        ] = {}

        self.scorer = SemanticScorer()

    def build(
        self,
        project: ProjectIndex,
    ) -> None:

        self.index.clear()

        self.file_tokens.clear()

        for file in project.files:

            tokens = set(
                file.semantic_tokens
            )

            self.file_tokens[
                file.relative_path
            ] = tokens

            for token in tokens:

                self.index[token].add(
                    file.relative_path
                )
    def search(
        self,
        query_tokens: set[str],
    ) -> dict[str, int]:
        """
        Semantic 검색.

        반환:
            {
                "src/.../PlayerManager.java": 87,
                ...
            }
        """

        candidates: set[str] = set()

        for token in query_tokens:

            candidates.update(
                self.index.get(
                    token,
                    set(),
                )
            )

        scores: dict[
            str,
            int,
        ] = {}

        for file in candidates:

            score = self.scorer.score(
                query_tokens,
                self.file_tokens.get(
                    file,
                    set(),
                ),
            )

            if score > 0:

                scores[file] = score

        return dict(
            sorted(
                scores.items(),
                key=lambda x: x[1],
                reverse=True,
            )
        )

    def update(
        self,
        file: str,
        tokens: set[str],
    ) -> None:
        """
        파일 하나만 다시 인덱싱한다.
        """

        old = self.file_tokens.get(
            file,
            set(),
        )

        for token in old:

            bucket = self.index.get(
                token
            )

            if bucket:

                bucket.discard(file)

                if not bucket:

                    del self.index[
                        token
                    ]

        self.file_tokens[file] = set(
            tokens
        )

        for token in tokens:

            self.index[token].add(
                file
            )
