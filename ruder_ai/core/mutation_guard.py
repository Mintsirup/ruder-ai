"""Deterministic guards for LLM-driven file mutations.

The LLM is allowed to decide *how* to perform a requested edit, but it must
not silently invent a symbol that the user asked to modify.  This module
contains small, dependency-free checks that run immediately before a
mutating file tool executes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]{1,80}(?![A-Za-z0-9_])")

# Words that look like identifiers in natural-language requests but are not
# useful as code-symbol targets.
_IDENTIFIER_STOPWORDS = {
    "the", "this", "that", "file", "files", "code", "class", "method",
    "function", "field", "value", "default", "change", "changes", "modify",
    "update", "fix", "make", "create", "add", "remove", "delete", "from",
    "to", "true", "false", "null", "return", "public", "private", "protected",
    "static", "final", "float", "double", "int", "string", "bool", "boolean",
    "void", "var", "const", "using", "namespace", "new", "set", "get",
    "request", "task", "playercontroller", "players", "unity", "assets",
}

_CREATE_PATTERNS = (
    "만들", "생성", "추가", "새로", "create", "add", "implement", "작성", "구현",
    "introduce", "도입",
)

_MODIFY_PATTERNS = (
    "수정", "변경", "고쳐", "바꿔", "리팩토링", "fix", "modify", "change", "update",
)

_DELETE_PATTERNS = (
    "삭제", "지워", "제거", "delete", "remove",
)


@dataclass(frozen=True, slots=True)
class MutationGuardResult:
    allowed: bool
    message: str = ""
    symbol: str | None = None


class MutationGuard:
    """Validate whether a generated mutation is consistent with the request."""

    @staticmethod
    def task_allows_new_symbols(task: str) -> bool:
        lowered = (task or "").lower()
        if any(pattern in lowered for pattern in _CREATE_PATTERNS):
            return True
        # Explicit full-file replacement is a stronger signal than a casual
        # modification request.  Keep this conservative: "rewrite" alone is
        # not treated as creation because it may still mean a refactor.
        return any(token in lowered for token in ("전체 파일을 새로", "전체를 다시 작성", "rewrite the file"))

    @staticmethod
    def task_targets_symbol(task: str, symbol: str) -> bool:
        if not task or not symbol:
            return False
        return re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", task) is not None

    @staticmethod
    def candidate_symbols(task: str) -> list[str]:
        found: list[str] = []
        for token in _IDENTIFIER_RE.findall(task or ""):
            lowered = token.lower()
            if lowered in _IDENTIFIER_STOPWORDS:
                continue
            if token.isdigit() or len(token) < 3:
                continue
            # Prefer tokens that look like actual code symbols.
            if not ("_" in token or any(ch.isupper() for ch in token)):
                continue
            if token not in found:
                found.append(token)
        return found

    def validate_text_mutation(
        self,
        *,
        task: str,
        original: str | None,
        generated: str,
        operation: str,
        file_path: str,
    ) -> MutationGuardResult:
        """Reject a modification that introduces a specifically requested
        symbol which did not exist in the original file, unless the request
        explicitly asked to create/add/implement it.
        """
        if not original or not generated or self.task_allows_new_symbols(task):
            return MutationGuardResult(True)

        # Only reason about explicit symbol-like names from the user's task.
        # This avoids blocking ordinary refactors that introduce harmless local
        # variables not mentioned by the user.
        for symbol in self.candidate_symbols(task):
            if not self.task_targets_symbol(task, symbol):
                continue
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", original):
                continue
            if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", generated):
                continue

            return MutationGuardResult(
                allowed=False,
                symbol=symbol,
                message=(
                    f"요청에서 수정 대상으로 지정한 심볼 '{symbol}'이 "
                    f"원본 파일 '{file_path}'에 존재하지 않습니다. "
                    f"'{operation}'으로 새 심볼을 만들어내지 않았습니다. "
                    "새 심볼 생성이 목적이라면 명시적으로 '추가/생성/구현'을 요청하세요."
                ),
            )

        return MutationGuardResult(True)
