from __future__ import annotations

from pathlib import Path
from typing import Iterable


class FileResolver:
    """단일 규칙으로 workspace 내 파일을 안전하게 해석한다."""

    IGNORE_PARTS = {".git", "Library", "Temp", "obj", "bin", "__pycache__"}

    def __init__(self, workspace):
        self.root = Path(workspace).resolve()

    def resolve(self, requested: str) -> str | None:
        if not requested:
            return None
        raw = str(requested).strip().strip('"\'')
        candidate = (self.root / raw.replace("\\", "/")).resolve()
        if self._inside_root(candidate) and candidate.is_file():
            return candidate.relative_to(self.root).as_posix()

        name = Path(raw).name
        matches = [
            p for p in self.root.rglob(name)
            if p.is_file() and not self._ignored(p)
        ]
        if len(matches) == 1:
            return matches[0].relative_to(self.root).as_posix()
        return None

    def resolve_many(self, requested: Iterable[str]) -> list[str]:
        result: list[str] = []
        for item in requested:
            resolved = self.resolve(item)
            if resolved and resolved not in result:
                result.append(resolved)
        return result

    def exists(self, requested: str) -> bool:
        return self.resolve(requested) is not None

    def _inside_root(self, path: Path) -> bool:
        return path == self.root or self.root in path.parents

    def _ignored(self, path: Path) -> bool:
        return any(part in self.IGNORE_PARTS for part in path.parts)
