"""Task Planner."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ruder_ai.indexer.models import (
    ProjectIndex,
    Symbol,
)

from ruder_ai.semantic.engine import SemanticEngine
from ruder_ai.semantic.scorer import SemanticScorer


@dataclass(slots=True)
class TaskPlan:
    """LLM에게 전달할 작업 계획."""

    prompt: str

    symbols: list[Symbol] = field(default_factory=list)

    files: list[str] = field(default_factory=list)

    # "create" | "modify" | "delete" | "search" | "read" | "chat"
    intent: str = "chat"


class TaskPlanner:
    """사용자 요청으로부터 관련 Symbol과 파일을 선택한다."""

    MAX_SYMBOLS = 20
    MAX_FILES = 5

    # 코드 작업 의도를 나누기 위한 최소한의 키워드 휴리스틱.
    # 어느 것도 매칭되지 않고 심볼/파일 스코어도 전부 0이면
    # "chat"(인사, 잡담, 일반 질문 등)으로 간주해서
    # 프로젝트 파일을 컨텍스트에 억지로 채워 넣지 않는다.
    _CREATE_WORDS = ("만들", "생성", "추가", "create")
    _MODIFY_WORDS = ("수정", "변경", "고쳐", "리팩토링", "refactor", "fix")
    _DELETE_WORDS = ("삭제", "지워", "delete", "제거")
    _SEARCH_WORDS = ("검색", "search", "찾아")
    _READ_WORDS = ("읽", "설명", "분석", "explain", "리뷰", "review")

    def _detect_intent(self, prompt: str) -> str:

        p = prompt.lower()

        for word in self._CREATE_WORDS:
            if word in p:
                return "create"

        for word in self._MODIFY_WORDS:
            if word in p:
                return "modify"

        for word in self._DELETE_WORDS:
            if word in p:
                return "delete"

        for word in self._SEARCH_WORDS:
            if word in p:
                return "search"

        for word in self._READ_WORDS:
            if word in p:
                return "read"

        return "chat"

    def __init__(
        self,
        call_graph,
        reference_index,
        semantic_file_index,
        type_resolver,
    ):
        self.call_graph = call_graph
        self.reference_index = reference_index
        self.semantic_file_index = semantic_file_index
        self.type_resolver = type_resolver

        self.semantic = SemanticEngine()
        self.scorer = SemanticScorer()

    def _tokenize(
        self,
        text: str,
    ) -> list[str]:
        """
        자연어 + CamelCase + snake_case를 모두 토큰으로 분리한다.
        """

        tokens = re.findall(
            r"[가-힣]+|[A-Za-z_][A-Za-z0-9_]*",
            text,
        )

        result: list[str] = []

        for token in tokens:

            result.append(token.lower())

            # snake_case

            if "_" in token:

                result.extend(
                    x.lower()
                    for x in token.split("_")
                    if x
                )

            # CamelCase

            camel = re.findall(
                r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)",
                token,
            )

            result.extend(
                x.lower()
                for x in camel
            )

        return list(dict.fromkeys(result))

    def _search_inverted(
        self,
        prompt: str,
        index: ProjectIndex,
    ) -> dict[str, int]:
        """
        역색인에서 관련 파일을 검색한다.
        """

        scores: dict[str, int] = {}

        expanded = self.semantic.expand(prompt)

        for token in expanded:

            resolved = self.type_resolver.resolve(
                index,
                token,
            )

            search_tokens = [token]

            if resolved:

                if isinstance(resolved, (list, tuple, set)):
                    search_tokens.extend(resolved)
                else:
                    search_tokens.append(resolved)

            for keyword in search_tokens:

                for file in index.search(keyword):

                    scores[file] = (
                        scores.get(file, 0)
                        + len(keyword)
                    )

        return scores

    def plan(
        self,
        prompt: str,
        index: ProjectIndex,
    ) -> TaskPlan:

        plan = TaskPlan(
            prompt=prompt,
        )

        plan.intent = self._detect_intent(prompt)

        query_tokens = self.semantic.expand_tokens(
            prompt,
        )

        resolved_symbols: list[Symbol] = []

        symbol_scores: list[
            tuple[int, Symbol]
        ] = []

        file_scores: dict[
            str,
            int,
        ] = {}

        for token in query_tokens:

            symbol = self.type_resolver.resolve_symbol(
                index,
                token,
            )

            if symbol is not None:
                resolved_symbols.append(symbol)

        # -------------------------
        # Symbol Search
        # -------------------------

        for symbol in index.symbols:

            score = self.scorer.score(
                query_tokens,
                symbol.semantic_tokens,
            )

            if score <= 0:
                continue

            symbol_scores.append(
                (
                    score,
                    symbol,
                )
            )

        symbol_scores.sort(
            key=lambda x: x[0],
            reverse=True,
        )

        seen_symbols = set()

        for symbol in resolved_symbols:

            key = (
                symbol.file,
                symbol.name,
                symbol.line,
            )

            if key in seen_symbols:
                continue

            seen_symbols.add(key)

            plan.symbols.append(symbol)

            file_scores[symbol.file] = (
                file_scores.get(
                    symbol.file,
                    0,
                )
                + 100
            )

        for score, symbol in symbol_scores:

            key = (
                symbol.file,
                symbol.name,
                symbol.line,
            )

            if key in seen_symbols:
                continue

            seen_symbols.add(key)

            plan.symbols.append(symbol)

            file_scores[symbol.file] = (
                file_scores.get(
                    symbol.file,
                    0,
                )
                + score
            )

            if len(plan.symbols) >= self.MAX_SYMBOLS:
                break
        # -------------------------
        # Semantic File Search
        # -------------------------

        semantic_files = (
            self.semantic_file_index.search(
                query_tokens,
            )
        )

        for symbol in resolved_symbols:

            file_scores[symbol.file] = (
                file_scores.get(
                    symbol.file,
                    0,
                )
                + 100
            )

        for file, score in semantic_files.items():

            file_scores[file] = (
                file_scores.get(
                    file,
                    0,
                )
                + score * 20
            )

        # -------------------------
        # Inverted Index Search
        # -------------------------

        inverted_scores = self._search_inverted(
            prompt,
            index,
        )

        for file, score in inverted_scores.items():

            file_scores[file] = (
                file_scores.get(
                    file,
                    0,
                )
                + score
            )

        # -------------------------
        # Type Resolver Boost
        # -------------------------

        resolved_type_names: set[str] = set()

        for token in query_tokens:

            resolved = self.type_resolver.resolve(
                index,
                token,
            )

            if resolved:

                if isinstance(
                    resolved,
                    (list, tuple, set),
                ):

                    resolved_type_names.update(
                        resolved
                    )

                else:

                    resolved_type_names.add(
                        resolved
                    )

        if resolved_type_names:

            boosted: list[
                tuple[int, Symbol]
            ] = []

            seen = {
                (
                    s.file,
                    s.name,
                    s.line,
                )
                for s in plan.symbols
            }

            for symbol in index.symbols:

                if (
                    symbol.name
                    not in resolved_type_names
                ):
                    continue

                key = (
                    symbol.file,
                    symbol.name,
                    symbol.line,
                )

                if key in seen:
                    continue

                score = (
                    self.scorer.score(
                        query_tokens,
                        symbol.semantic_tokens,
                    )
                    + 100
                )

                boosted.append(
                    (
                        score,
                        symbol,
                    )
                )

            boosted.sort(
                key=lambda x: x[0],
                reverse=True,
            )

            for score, symbol in boosted:

                key = (
                    symbol.file,
                    symbol.name,
                    symbol.line,
                )

                seen.add(key)

                plan.symbols.append(
                    symbol
                )

                file_scores[symbol.file] = (
                    file_scores.get(
                        symbol.file,
                        0,
                    )
                    + score
                )

                if (
                    len(plan.symbols)
                    >= self.MAX_SYMBOLS
                ):
                    break

        # -------------------------
        # Sort Files
        # -------------------------

        for file, _ in sorted(
            file_scores.items(),
            key=lambda x: x[1],
            reverse=True,
        ):

            if file in plan.files:
                continue

            plan.files.append(
                file
            )

            if (
                len(plan.files)
                >= self.MAX_FILES
            ):
                break

        # -------------------------
        # Fallback
        # -------------------------
        # "chat" 의도(인사, 잡담, 코드와 무관한 질문 등)일 때는
        # 관련 없는 파일을 억지로 채워 넣지 않는다. 그렇지 않으면
        # LLM이 README 같은 아무 파일이나 "고쳐야 할 것"으로 착각해
        # 불필요한 patch_file/write_file 호출을 시도하게 된다.

        if not plan.files and plan.intent != "chat":

            for file in index.files[
                : self.MAX_FILES
            ]:

                plan.files.append(
                    file.relative_path
                )

        existing = {
            (
                s.file,
                s.name,
                s.line,
            )
            for s in plan.symbols
        }

        for symbol in plan.symbols:

            if len(plan.files) >= self.MAX_FILES:
                break

            if symbol.file not in plan.files:
                plan.files.append(symbol.file)

        # -------------------------
        # Call Graph Expansion
        # -------------------------

        for symbol in list(plan.symbols):

            related = self.call_graph.find_related(
                symbol.name,
                depth=2,
            )

            for name in related:

                for candidate in index.symbols:

                    if candidate.name != name:
                        continue

                    key = (
                        candidate.file,
                        candidate.name,
                        candidate.line,
                    )

                    if key in existing:
                        continue

                    existing.add(key)

                    plan.symbols.append(
                        candidate
                    )

                    file_scores[candidate.file] = (
                        file_scores.get(
                            candidate.file,
                            0,
                        )
                        + 50
                    )

                    if (
                        candidate.file not in plan.files
                        and len(plan.files) < self.MAX_FILES
                    ):
                        plan.files.append(candidate.file)

                    if (
                        len(plan.symbols)
                        >= self.MAX_SYMBOLS
                    ):
                        break

                if (
                    len(plan.symbols)
                    >= self.MAX_SYMBOLS
                ):
                    break

            if (
                len(plan.symbols)
                >= self.MAX_SYMBOLS
            ):
                break

        # -------------------------
        # Reverse Call Graph
        # -------------------------

        for symbol in list(plan.symbols):

            callers = self.call_graph.find_callers(
                symbol.name,
                depth=2,
            )

            for name in callers:

                for candidate in index.symbols:

                    if candidate.name != name:
                        continue

                    key = (
                        candidate.file,
                        candidate.name,
                        candidate.line,
                    )

                    if key in existing:
                        continue

                    existing.add(key)

                    plan.symbols.append(
                        candidate
                    )

                    file_scores[candidate.file] = (
                        file_scores.get(
                            candidate.file,
                            0,
                        )
                        + 40
                    )

                    if (
                        candidate.file
                        not in plan.files
                    ):
                        plan.files.append(
                            candidate.file
                        )

                    if (
                        len(plan.symbols)
                        >= self.MAX_SYMBOLS
                    ):
                        break

                if (
                    len(plan.symbols)
                    >= self.MAX_SYMBOLS
                ):
                    break

            if (
                len(plan.symbols)
                >= self.MAX_SYMBOLS
            ):
                break

        # -------------------------
        # Reference Expansion
        # -------------------------

        for symbol in list(plan.symbols):

            refs = self.reference_index.find_references(
                symbol.name,
                limit=20,
            )

            for file, weight in refs:

                file_scores[file] = (
                    file_scores.get(
                        file,
                        0,
                    )
                    + weight
                )

                if (
                    file not in plan.files
                    and len(plan.files) < self.MAX_FILES
                ):
                    plan.files.append(file)

                if (
                    len(plan.files)
                    >= self.MAX_FILES
                ):
                    break

            if (
                len(plan.files)
                >= self.MAX_FILES
            ):
                break
        # -------------------------
        # Final File Ranking
        # -------------------------

        ranked_files = sorted(
            file_scores.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        plan.files.clear()

        for file, _ in ranked_files:

            if file not in plan.files:

                plan.files.append(
                    file
                )

            if (
                len(plan.files)
                >= self.MAX_FILES
            ):
                break

        # -------------------------
        # Final Symbol Deduplication
        # -------------------------

        unique_symbols: list[
            Symbol
        ] = []

        seen = set()

        for symbol in plan.symbols:

            key = (
                symbol.file,
                symbol.name,
                symbol.line,
            )

            if key in seen:
                continue

            seen.add(key)

            unique_symbols.append(
                symbol
            )

            if (
                len(unique_symbols)
                >= self.MAX_SYMBOLS
            ):
                break

        plan.symbols = unique_symbols

        # -------------------------
        # Final File Fallback
        # -------------------------

        if not plan.files and plan.intent != "chat":

            for file in index.files[
                : self.MAX_FILES
            ]:

                plan.files.append(
                    file.relative_path
                )

        return plan
