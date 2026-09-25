"""Context Builder.

TODO.md "Context Builder" 항목:
- MAX_FILES 튜닝: 파일 개수를 고정된 값으로 자르는 대신, Token Budget
  안에서 실제로 얼마나 들어가는지를 보고 결정한다 (`MAX_FILES`는 여전히
  상한선으로 남겨두되, budget이 먼저 소진되면 그보다 적게 포함될 수 있음).
- Context Compression: 파일 하나의 스니펫이 남은 budget보다 크면 앞/뒤만
  남기고 압축한다 (`token_budget.truncate_to_budget`).
- Token Budget: `estimate_tokens`로 전체 Context 크기를 추정해가며 파일을
  담고, budget을 넘기면 그 시점에서 중단한다.
- Context Cache: 같은 (파일, mtime, target_line) 조합은 다시 디스크를
  읽지 않고 캐시된 스니펫을 재사용한다. Retry Loop에서 실패 로그를 반영해
  컨텍스트를 다시 검색할 때(`ToolExecutor._rebuild_context_summary`)
  같은 파일을 반복해서 읽는 경우가 많아 특히 유효하다.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import time

from ruder_ai.context.models import (
    Context,
    ContextFile,
)
from ruder_ai.context.snippet_builder import SnippetBuilder
from ruder_ai.context.token_budget import (
    estimate_tokens,
    truncate_to_budget,
)
from ruder_ai.core.planner import TaskPlan
from ruder_ai.indexer.models import (
    ProjectIndex,
)


class ContextBuilder:
    """TaskPlan으로부터 LLM Context를 생성한다."""

    MAX_FILES = 5
    MAX_SNIPPETS_PER_FILE = 5

    # 전체 Context(파일 스니펫 총합)에 허용하는 대략적인 토큰 수.
    # 파일 하나가 이 budget의 절반을 넘게 요구하면 그 파일만
    # 압축(truncate_to_budget)한다.
    DEFAULT_TOKEN_BUDGET = 4352

    def __init__(
        self,
        workspace: str | Path,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        max_files: int = MAX_FILES,
        max_snippets_per_file: int = MAX_SNIPPETS_PER_FILE,
    ):

        self.workspace = Path(workspace).resolve()

        self.token_budget = max(1, int(token_budget))
        self.max_files = max(1, int(max_files))
        self.max_snippets_per_file = max(1, int(max_snippets_per_file))

        self.snippets = SnippetBuilder()

        # Context Cache: (절대경로, mtime, target_line) -> snippet 텍스트.
        # 같은 파일이 안 바뀐 채로 다시 요청되면 디스크를 다시 읽지 않는다.
        self._snippet_cache: dict[tuple[str, float, int | None], tuple[float, str]] = {}
        self.max_cache_entries = 512
        self.cache_ttl_seconds = 600.0

    def build(
        self,
        plan: TaskPlan,
        index: ProjectIndex,
    ) -> Context:

        self._cleanup_cache()

        context = Context(
            prompt=plan.prompt,
            project=index.project,
        )

        context.symbols.extend(plan.symbols)

        # -------------------------------------
        # file -> symbols
        # -------------------------------------

        symbol_map: dict[str, list] = defaultdict(list)

        for symbol in plan.symbols:
            symbol_map[symbol.file].append(symbol)

        # -------------------------------------
        # Files (Token Budget 안에서 채운다)
        # -------------------------------------

        candidates = plan.files[: self.max_files]
        remaining_budget = self.token_budget

        for relative_path in candidates:

            # budget이 이미 소진됐으면 더 이상 파일을 추가하지 않는다
            # (MAX_FILES보다 적은 수의 파일만 담기고 끝날 수 있음).
            if remaining_budget <= 0:
                break

            absolute = self.workspace / relative_path

            if not absolute.exists():
                continue

            snippets = []

            symbols = symbol_map.get(
                relative_path,
                [],
            )

            # 심볼이 없는 경우
            if not symbols:

                snippet = self._build_snippet_cached(
                    absolute,
                    None,
                )

                if snippet:
                    snippets.append(snippet)

            else:

                for symbol in symbols[: self.max_snippets_per_file]:

                    snippet = self._build_snippet_cached(
                        absolute,
                        getattr(symbol, "line", None),
                    )

                    if (
                        snippet
                        and snippet not in snippets
                    ):
                        snippets.append(snippet)

            content = "\n\n".join(snippets)

            if not content:
                continue

            # Context Compression: 이 파일 하나가 남은 budget을 넘으면
            # (여러 파일이 균등하게 들어갈 수 있도록) 남은 budget의
            # 절반까지만 쓰도록 압축한다. 이미 마지막 후보 파일이라면
            # 절반 제한이 무의미하므로 남은 budget 전체를 그대로 쓴다.
            content_tokens = estimate_tokens(content)

            if content_tokens > remaining_budget:

                is_last_candidate = (
                    relative_path == candidates[-1]
                )

                per_file_limit = (
                    remaining_budget
                    if is_last_candidate
                    else max(1, remaining_budget // 2)
                )

                content = truncate_to_budget(
                    content,
                    per_file_limit,
                )
                content_tokens = estimate_tokens(content)

            remaining_budget -= content_tokens

            context.files.append(
                ContextFile(
                    path=relative_path,
                    content=content,
                )
            )

        return context

    def clear_cache(self) -> None:
        self._snippet_cache.clear()

    def invalidate_file(self, relative_path: str) -> None:
        absolute = str((self.workspace / relative_path).resolve())
        stale = [key for key in self._snippet_cache if key[0] == absolute]
        for key in stale:
            self._snippet_cache.pop(key, None)

    def _build_snippet_cached(
        self,
        absolute: Path,
        target_line: int | None,
    ) -> str:
        """Context Cache: (경로, mtime, target_line)이 같으면 캐시를
        재사용하고, 아니면 새로 만들어 캐시에 저장한다."""

        try:
            mtime = absolute.stat().st_mtime
        except OSError:
            return ""

        cache_key = (str(absolute), mtime, target_line)

        cached = self._snippet_cache.get(cache_key)

        if cached is not None:
            created_at, cached_text = cached
            if time.monotonic() - created_at <= self.cache_ttl_seconds:
                return cached_text
            self._snippet_cache.pop(cache_key, None)

        try:
            snippet = self.snippets.build(
                absolute,
                target_line,
            )
        except Exception:
            return ""

        # 파일이 바뀌면(mtime 변경) 이전 mtime의 캐시 항목은 더 이상
        # 조회되지 않는데, 캐시가 무한정 쌓이지 않도록 같은 경로의
        # stale 항목은 명시적으로 청소한다.
        stale_keys = [
            key
            for key in self._snippet_cache
            if key[0] == str(absolute) and key[1] != mtime
        ]

        for key in stale_keys:
            del self._snippet_cache[key]

        self._snippet_cache[cache_key] = (time.monotonic(), snippet)
        self._cleanup_cache()

        return snippet

    def _cleanup_cache(self) -> None:
        now = time.monotonic()
        expired = [
            key for key, (created_at, _text) in self._snippet_cache.items()
            if now - created_at > self.cache_ttl_seconds
        ]
        for key in expired:
            self._snippet_cache.pop(key, None)

        overflow = len(self._snippet_cache) - self.max_cache_entries
        if overflow > 0:
            oldest = sorted(
                self._snippet_cache.items(),
                key=lambda item: item[1][0],
            )[:overflow]
            for key, _value in oldest:
                self._snippet_cache.pop(key, None)
