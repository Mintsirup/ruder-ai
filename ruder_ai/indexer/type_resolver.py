"""Type Resolver."""

from __future__ import annotations

import re
from collections import defaultdict

from ruder_ai.indexer.models import (
    ProjectIndex,
    Symbol,
)


class TypeResolver:
    """
    타입 이름을 실제 Symbol로 연결한다.

    예)
        Player -> org.bukkit.entity.Player

        Bukkit -> org.bukkit.Bukkit

        JavaPlugin -> org.bukkit.plugin.java.JavaPlugin
    """

    def __init__(self):

        self.type_map: dict[
            str,
            list[Symbol],
        ] = defaultdict(list)

        self.import_map: dict[
            str,
            str,
        ] = {}

        # (lowercased name, original name) in declaration order, deduplicated
        # on the lowercased form. The case-insensitive fallback has to stay an
        # ordered scan - a substring hit on an earlier name beats an exact hit
        # on a later one - but the old code recomputed ``name.lower()`` for
        # every symbol on every query token, which on a 1,300-symbol project is
        # tens of thousands of throwaway string allocations per plan.
        self._lower_pairs: list[tuple[str, str]] = []

    def build(
        self,
        index: ProjectIndex,
    ) -> None:

        self.type_map.clear()
        self.import_map.clear()
        self._lower_pairs = []

        for symbol in index.symbols:

            self.type_map[
                symbol.name
            ].append(
                symbol
            )

            if symbol.package:

                full = (
                    f"{symbol.package}."
                    f"{symbol.name}"
                )

                self.import_map[
                    full
                ] = symbol.name

        if hasattr(
            index,
            "imports",
        ):

            self.import_map.update(
                index.imports
            )

        # first-wins on the lowercased form, preserving type_map's insertion
        # order - a later name that differs only by case can never win anyway
        seen_lower: set[str] = set()
        for name in self.type_map:
            lower = name.lower()
            if lower in seen_lower:
                continue
            seen_lower.add(lower)
            self._lower_pairs.append((lower, name))

    def resolve(
        self,
        index: ProjectIndex,
        token: str,
    ) -> str | None:

        token = token.strip()

        if not token:
            return None

        # -------------------------
        # Exact Symbol
        # -------------------------

        if token in self.type_map:
            return token

        # -------------------------
        # Fully Qualified Name
        # -------------------------

        if token in self.import_map:
            return self.import_map[
                token
            ]

        # -------------------------
        # package.Class
        # -------------------------

        short = token.split(
            "."
        )[-1]

        if short in self.type_map:
            return short
        # -------------------------
        # Generic 제거
        # -------------------------

        token = re.sub(
            r"<.*?>",
            "",
            token,
        )

        if token in self.type_map:
            return token

        # -------------------------
        # Array 제거
        # -------------------------

        token = token.replace(
            "[]",
            "",
        )

        if token in self.type_map:
            return token

        # -------------------------
        # Nullable / Optional
        # -------------------------

        token = token.replace(
            "?",
            "",
        )

        token = token.strip()

        if token in self.type_map:
            return token

        # -------------------------
        # CamelCase 일부 일치
        # -------------------------

        lower = token.lower()

        for lowered, name in self._lower_pairs:

            if lower in lowered:
                return name

        # -------------------------
        # import 마지막 이름 비교
        # -------------------------

        # Hoisted out of the loop: the old code rebuilt "." + token once per
        # import in the project.
        suffix = "." + token

        for full, short in (
            self.import_map.items()
        ):

            if full.endswith(suffix):
                return short

        return None

    def resolve_symbol(
        self,
        index: ProjectIndex,
        token: str,
    ) -> Symbol | None:
        """
        Symbol 하나를 반환한다.
        """

        resolved = self.resolve(
            index,
            token,
        )

        if resolved is None:
            return None

        symbols = self.type_map.get(
            resolved,
            [],
        )

        if not symbols:
            return None

        return symbols[0]

