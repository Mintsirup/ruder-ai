"""Project Scanner."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path


from ruder_ai.semantic.cache import SemanticCache
from .file_cache import CACHE as FILE_CACHE
from .models import FileInfo

# Compiled once at import. Passing these through re.findall() made the
# interpreter re-resolve the pattern from its internal cache on every call,
# which showed up as ~25k cache lookups per scan.
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[가-힣]+")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)")

#: Bytes. Reading a multi-hundred-megabyte bundle/minified asset into memory
#: just to pull a few tokens out of it used to be enough to stall a scan for
#: minutes; the file is still listed, it is just not tokenized.
MAX_TOKENIZE_BYTES = 2_000_000


class ProjectScanner:
    """
    프로젝트를 스캔한다.

    - 파일 목록 생성
    - Inverted Index 생성
    """

    # Directories pruned from the walk entirely. ``node_modules``/``.venv``
    # were already skipped by FileResolver, the orchestrator, the executor
    # and the Studio tree; the scanner was the one place that still walked
    # into them, which is where most of the wasted stat() calls went.
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
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
    }

    def __init__(self, workspace: str | Path):

        self.workspace = Path(workspace).resolve()

        self.inverted_index: dict[str, set[str]] = defaultdict(set)

        self.semantic = SemanticCache()

    def scan(self) -> list[FileInfo]:

        files: list[FileInfo] = []

        self.inverted_index.clear()

        # os.scandir with in-place pruning instead of rglob("*"). The old
        # loop asked the filesystem about *every* entry under .git, build/ and
        # out/ and only then threw the answer away; now those subtrees are
        # never opened at all.
        #
        # The relative path is assembled from the names we already have
        # instead of Path.relative_to().as_posix(). The codebase convention is
        # posix ('a/b/c.py'); building the string directly keeps that
        # guaranteed on Windows without allocating a Path per file.
        ignore = self.IGNORE_DIRS
        semantic = self.semantic.build
        append = files.append

        def walk(directory: str, prefix: str) -> None:
            try:
                entries = list(os.scandir(directory))
            except OSError:
                return

            for entry in entries:
                try:
                    if entry.is_dir():
                        if entry.name in ignore:
                            continue
                        walk(entry.path, prefix + entry.name + "/")
                        continue
                    if not entry.is_file():
                        continue
                    # One syscall for both the file test and the size. The old
                    # path paid is_file() + stat() for every entry.
                    st = entry.stat()
                except OSError:
                    continue
                size = st.st_size
                key = (entry.path, size, st.st_mtime_ns)

                name = entry.name
                # Mirrors PurePath exactly: the last dot only separates a stem
                # from a suffix when it is neither the first nor the last
                # character. That is what makes ".gitignore" a suffix-less stem
                # and ".env.local" a stem of ".env" with a ".local" suffix.
                dot = name.rfind(".")
                if 0 < dot < len(name) - 1:
                    stem = name[:dot]
                    extension = name[dot:].lower()
                else:
                    stem = name
                    extension = ""

                info = FileInfo(
                    absolute_path=Path(entry.path),
                    relative_path=prefix + name,
                    extension=extension,
                    size=size,
                )

                info.semantic_tokens = semantic(
                    stem
                )

                append(info)

                if size <= MAX_TOKENIZE_BYTES:
                    self._index_file(
                        entry.path,
                        info.relative_path,
                        key,
                    )

        walk(str(self.workspace), "")

        files.sort(
            key=lambda f: f.relative_path
        )

        return files

    def _index_file(
        self,
        file: str | Path,
        relative: str,
        cache_key: tuple[str, int, int] | None = None,
    ) -> None:

        if cache_key is None:
            cache_key = FILE_CACHE.key(Path(file))
        if cache_key is None:
            return

        def compute() -> list[str]:
            text = FILE_CACHE.text(cache_key, None, "ignore")
            if text is None:
                return []
            return self._tokenize(text)

        tokens = FILE_CACHE.tokens(
            cache_key,
            compute,
        )
        if not tokens:
            return

        inverted = self.inverted_index
        for token in tokens:

            bucket = inverted.get(token)
            if bucket is None:
                inverted[token] = {relative}
            else:
                bucket.add(relative)

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

        words = _WORD_RE.findall(text)

        # The result was always de-duplicated by a set before being returned,
        # so accumulate straight into one instead of building a list first.
        result: set[str] = set()
        add = result.add

        for word in words:

            lower = word.lower()
            add(lower)

            # CamelCase split. Almost every token in real source is already
            # lowercase and the regex engine cannot know that without running,
            # so the explicit check skips ~95% of the calls. ``islower()``
            # answers it without allocating a second lowered copy; a token that
            # is already all-lowercase can only split on "_", and the branch
            # below already covers that (including the leading underscore of
            # ``_private``).
            if not word.islower():
                for part in _CAMEL_RE.findall(word):
                    add(part.lower())

            if "_" in lower:
                for piece in lower.split("_"):
                    if piece:
                        add(piece)

        return list(result)
