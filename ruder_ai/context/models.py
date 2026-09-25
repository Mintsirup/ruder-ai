from __future__ import annotations

from dataclasses import dataclass, field

from ruder_ai.indexer.models import (
    ProjectInfo,
    Symbol,
)


@dataclass(slots=True)
class ContextFile:
    path: str
    content: str


@dataclass(slots=True)
class Context:
    prompt: str
    project: ProjectInfo

    symbols: list[Symbol] = field(
        default_factory=list
    )

    files: list[ContextFile] = field(
        default_factory=list
    )
