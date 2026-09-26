"""Reference Index."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .file_cache import CACHE as FILE_CACHE
from .models import ProjectIndex

#: One module-level pass instead of one ``findall`` per line.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _read_text(path: Path, errors: str) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors=errors)
    except Exception:
        return None


class ReferenceIndex:
    """
    심볼이 어디에서 사용되는지 저장한다.
    """

    def __init__(self):

        self.references = defaultdict(list)

        # relative posix path -> the entries this file contributed, so a single
        # file can be re-indexed without rescanning the workspace.
        self._by_file: dict[
            str,
            dict[str, list[tuple[str, int]]],
        ] = {}

    def build(
        self,
        index: ProjectIndex,
    ):

        self.references.clear()
        self._by_file.clear()

        workspace = Path(index.workspace)

        symbol_names = {
            symbol.name
            for symbol in index.symbols
        }

        for file in index.files:

            self._index_file(
                workspace / file.relative_path,
                file.relative_path,
                symbol_names,
            )

    def _index_file(
        self,
        path: Path,
        relative: str,
        symbol_names: set[str],
    ) -> None:
        # The scanner and the symbol indexer already read this file during the
        # same build; reuse the bytes instead of opening it a third time. The
        # decode policy stays "replace" - sharing the text directly would
        # change which identifiers "abc\xffdef" produces.
        key = FILE_CACHE.key(path)
        text = (
            FILE_CACHE.text(key, None, "replace")
            if key is not None
            else _read_text(path, "replace")
        )
        if text is None:
            return

        found = self._scan(
            text,
            relative,
            symbol_names,
        )

        if not found:
            return

        self._by_file[relative] = found

        references = self.references
        for token, entries in found.items():
            references[token].extend(entries)

    def _scan(
        self,
        text: str,
        relative: str,
        symbol_names: set[str],
    ) -> dict[str, list[tuple[str, int]]]:
        """Bucket the symbol names this file mentions, by line.

        Four scanning strategies were benchmarked on a real workspace
        (per-line ``findall``, whole-text ``finditer`` with incremental line
        counting, ``findall`` with a forward cursor, and ``split("\\n")``):
        per-line ``findall`` was the fastest of the four. The regex tokenizing
        is the cost here, not the per-line call overhead, so the original loop
        stays and only the *incremental* path is new.
        """
        found: dict[str, list[tuple[str, int]]] = {}
        findall = _IDENTIFIER_RE.findall

        for line_no, line in enumerate(
            text.splitlines(),
            start=1,
        ):

            for token in findall(line):

                if token not in symbol_names:
                    continue

                entries = found.get(token)
                if entries is None:
                    found[token] = [(relative, line_no)]
                else:
                    entries.append((relative, line_no))

        return found

    def update_file(
        self,
        index: ProjectIndex,
        file_path: str,
    ) -> None:
        """Re-index exactly one file instead of the whole workspace.

        ``AIAgent.refresh_file`` runs after every edit the agent makes. This
        index used to be rebuilt from scratch each time, which meant reading
        and re-tokenizing every file in the project per keystroke-level change.
        """
        workspace = Path(index.workspace)
        try:
            relative = Path(file_path).resolve().relative_to(
                workspace.resolve()
            ).as_posix()
        except ValueError:
            self.build(index)
            return

        for token, entries in self._by_file.pop(
            relative,
            {},
        ).items():
            bucket = self.references.get(token)
            if not bucket:
                continue
            remaining = [
                entry
                for entry in bucket
                if entry[0] != relative
            ]
            if remaining:
                self.references[token] = remaining
            else:
                del self.references[token]

        if not Path(file_path).is_file():
            return

        symbol_names = {
            symbol.name
            for symbol in index.symbols
        }

        found = self._scan(
            _read_text(Path(file_path), "replace") or "",
            relative,
            symbol_names,
        )

        if found:
            self._by_file[relative] = found

        for token, entries in found.items():
            self.references[token].extend(entries)

    def find_references(
        self,
        symbol: str,
        limit: int = 30,
    ) -> list[tuple[str, int]]:
        """
        심볼이 사용된 위치를 반환한다.

        Returns:
            [
                ("src/Main.java", 25),
                ("src/GameCommand.java", 61),
            ]
        """

        refs = self.references.get(symbol)

        if refs is None:
            return []

        return refs[:limit]
