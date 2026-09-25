"""Project Scanner."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ruder_ai.semantic.cache import SemanticCache
from .models import FileInfo


class ProjectScanner:
    """
    프로젝트를 스캔한다.

    - 파일 목록 생성
    - Inverted Index 생성
    """

    IGNORE_DIRS = {
        ".git",
        ".idea",
        ".vscode",
        "__pycache__",
        "build",
        "out",
        "target",
        ".gradle",
        ".forgecache",
    }

    def __init__(self, workspace: str | Path):

        self.workspace = Path(workspace).resolve()

        self.inverted_index: dict[str, set[str]] = defaultdict(set)

        self.semantic = SemanticCache()

    def scan(self) -> list[FileInfo]:

        files: list[FileInfo] = []

        self.inverted_index.clear()

        for path in self.workspace.rglob("*"):

            if not path.is_file():
                continue

            if any(
                part in self.IGNORE_DIRS
                for part in path.parts
            ):
                continue

            relative = str(
                path.relative_to(self.workspace)
            )

            info = FileInfo(
                absolute_path=path,
                relative_path=relative,
                extension=path.suffix.lower(),
                size=path.stat().st_size,
            )

            info.semantic_tokens = self.semantic.build(
                path.stem
            )

            files.append(info)

            self._index_file(path, relative)

        files.sort(
            key=lambda f: f.relative_path
        )

        return files

    def _index_file(
        self,
        file: Path,
        relative: str,
    ) -> None:

        try:

            text = file.read_text(
                encoding="utf-8",
                errors="ignore",
            )

        except Exception:
            return

        tokens = self._tokenize(text)

        for token in tokens:

            self.inverted_index[token].add(
                relative
            )

    def search(
        self,
        keyword: str,
    ) -> list[str]:

        return sorted(
            self.inverted_index.get(
                keyword.lower(),
                set(),
            )
        )

    def _tokenize(
        self,
        text: str,
    ) -> list[str]:

        words = re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*|[가-힣]+",
            text,
        )

        result = []

        for word in words:

            original = word

            result.append(original.lower())

            # CamelCase
            parts = re.findall(
                r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)",
                original,
            )

            result.extend(
                p.lower()
                for p in parts
            )

            lower = original.lower()

            if "_" in lower:
                result.extend(
                    x
                    for x in lower.split("_")
                    if x
                )

        return list(set(result))
