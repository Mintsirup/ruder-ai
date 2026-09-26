"""Per-file derived data (inverted-index tokens, Python AST) keyed on mtime.

The agent rebuilds the project index often - after every edit, and again
whenever ``invalidate_index`` is called - and each rebuild used to re-read,
re-tokenize and re-parse every file in the workspace. Reading is cheap;
``ast.parse`` and regex tokenizing are not, and together they were ~85% of a
rebuild.

The invalidation key is ``(path, size, mtime_ns)``, exactly what
``AIAgent._workspace_fs_signature`` already uses to decide whether a rebuild is
needed at all. The cache therefore cannot hand back anything staler than the
index it feeds: if the signature says the file changed, so does this key.

Entries are bounded twice - by count and by the total source size they were
derived from - so a huge monorepo evicts instead of growing without limit.
"""

from __future__ import annotations

import ast
from collections import OrderedDict
from pathlib import Path

#: Never hold more than this many files' derived data.
MAX_ENTRIES = 4096

#: ...nor more than this much source size worth of derived data.
MAX_SOURCE_BYTES = 128 * 1024 * 1024


class _Entry:
    __slots__ = ("raw", "readable", "tokens", "tree", "size")

    def __init__(self, size: int):
        #: Decoded once and shared. The raw *bytes* are cached rather than a
        #: decoded string because the three passes disagree on the decode
        #: error policy (``errors="ignore"`` for the inverted index,
        #: ``"replace"`` for references) and those really do produce different
        #: tokens: "abc\xffdef" is one identifier under "ignore" and two under
        #: "replace".
        self.raw: bytes | None = None
        self.readable: bool | None = None
        self.tokens: tuple[str, ...] | None = None
        self.tree: ast.AST | None = None
        self.size = size


class FileDataCache:
    """Memoize the expensive per-file derivations, keyed on mtime + size."""

    def __init__(self) -> None:
        self._entries: "OrderedDict[tuple[str, int, int], _Entry]" = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------------
    @staticmethod
    def key(path: Path) -> tuple[str, int, int] | None:
        """Return the cache key for ``path``, or ``None`` if it cannot be stat-ed."""
        try:
            st = Path(path).stat()
        except OSError:
            return None
        return (str(path), st.st_size, st.st_mtime_ns)

    def _entry(self, key: tuple[str, int, int]) -> _Entry:
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
            self.hits += 1
            return entry
        self.misses += 1
        entry = _Entry(key[1])
        self._entries[key] = entry
        self._bytes += entry.size
        self._evict()
        return entry

    def _evict(self) -> None:
        while (
            len(self._entries) > MAX_ENTRIES
            or self._bytes > MAX_SOURCE_BYTES
        ) and self._entries:
            _, dropped = self._entries.popitem(last=False)
            self._bytes -= dropped.size

    # ------------------------------------------------------------------
    def tokens(self, key: tuple[str, int, int], compute) -> tuple[str, ...]:
        entry = self._entry(key)
        if entry.tokens is None:
            entry.tokens = tuple(compute())
        return entry.tokens

    def tree(self, key: tuple[str, int, int], compute):
        entry = self._entry(key)
        if entry.tree is None:
            entry.tree = compute()
        return entry.tree

    def text(self, key: tuple[str, int, int], compute, errors: str) -> str | None:
        """Decoded file contents, shared between the three index passes.

        A full build reads every file up to three times: once to tokenize for
        the inverted index, once to parse, once more to find references. The
        raw bytes are kept in the same bounded entry and each caller decodes
        with its own error policy.
        """
        entry = self._entry(key)
        if entry.readable is None:
            try:
                entry.raw = Path(key[0]).read_bytes()
            except OSError:
                entry.raw = None
            entry.readable = entry.raw is not None
        if not entry.readable:
            return None
        assert entry.raw is not None
        return entry.raw.decode("utf-8", errors)

    def invalidate(self, path: Path) -> None:
        """Forget a single file, e.g. right after writing it.

        Not strictly required - a write changes size and/or mtime, which is
        the key - but it keeps the cache from holding an entry for a file that
        was just replaced, and matters on filesystems with a coarse mtime.
        """
        prefix = str(path)
        for key in [k for k in self._entries if k[0] == prefix]:
            dropped = self._entries.pop(key)
            self._bytes -= dropped.size

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0

    @property
    def stats(self) -> str:
        return (
            f"{len(self._entries)} entries, {self._bytes / 1048576:.1f} MiB, "
            f"{self.hits} hits / {self.misses} misses"
        )


#: Process-wide. The indexer and the scanner are separate, long-lived objects
#: that both derive data from the same files; sharing is the point.
CACHE = FileDataCache()
