"""Semantic aliases."""

from __future__ import annotations

import json
from pathlib import Path

_ALIAS_FILE = (
    Path(__file__).parent
    / "aliases.json"
)

try:

    ALIASES: dict[str, list[str]] = json.loads(
        _ALIAS_FILE.read_text(
            encoding="utf-8",
        )
    )

except Exception:

    ALIASES = {}
