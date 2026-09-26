"""Project Symbol Indexer."""

from __future__ import annotations

import ast
from pathlib import Path

from ruder_ai.indexer.file_cache import CACHE as FILE_CACHE
from ruder_ai.indexer.models import (
    FileInfo,
    ProjectIndex,
    ProjectInfo,
    Symbol,
)
from ruder_ai.semantic.cache import SemanticCache


#: AST nodes whose children can *contain* an import or a definition.
#: Everything else is an expression or a leaf, and descending into it can never
#: reveal an ``Import``/``ClassDef``/``FunctionDef`` - it is where the bulk of
#: the nodes in a real module live, which is what made ``ast.walk`` expensive.
_STATEMENT_CONTAINERS = (
    ast.Module,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Match,
    # ``except``/``finally``/``else`` bodies are statement lists hanging off
    # ``Try``, not children of it, so the handler needs its own descent.
    ast.ExceptHandler,
)

#: ``TryStar`` (``except*``) only exists from Python 3.11.
_STATEMENT_CONTAINERS = _STATEMENT_CONTAINERS + (
    getattr(ast, "TryStar", ast.Try),
)


#: The node types the symbol indexer actually consumes.
_DEFINITION_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
)


def walk_definitions(tree: ast.AST):
    """Yield every import/class/function node without visiting expressions.

    ``ast.walk`` visits every node in a module - in a real file the vast
    majority of nodes live inside expressions (arguments, bodies, dicts,
    comprehensions), and none of them can be an import or a definition. This
    yields the same set of definitions while only descending through the node
    types that can actually *contain* a statement.
    """
    for child in ast.iter_child_nodes(tree):
        # A class/function is both a definition worth reporting *and* a
        # container worth descending into, so the two checks are independent.
        if isinstance(child, _DEFINITION_NODES):
            yield child
        if isinstance(child, ast.Match):
            # ``match_case`` is not an ``ast.AST`` subclass, so
            # ``iter_child_nodes`` silently drops every case body.
            for case in child.cases:
                for sub in case.body:
                    if isinstance(sub, _DEFINITION_NODES):
                        yield sub
                    if isinstance(sub, _STATEMENT_CONTAINERS):
                        yield from walk_definitions(sub)
        elif isinstance(child, _STATEMENT_CONTAINERS):
            yield from walk_definitions(child)


class SymbolIndexer:
    """
    프로젝트의 Symbol을 추출하여
    ProjectIndex를 생성한다.
    """

    def __init__(self):

        self.semantic = SemanticCache()

        # relative posix path -> set of ``index.imports`` keys that file
        # declared. Needed so ``update_file`` can tell "this file is the only
        # one that imports X" (safe to drop) from "many files import X".
        self._imports_by_file: dict[str, set[str]] = {}

    def build(
        self,
        workspace: Path,
        files: list[FileInfo],
        project: ProjectInfo,
        inverted_index: dict[str, set[str]],
    ) -> ProjectIndex:

        index = ProjectIndex(
            workspace=workspace,
            project=project,
            files=files,
            inverted_index=inverted_index,
        )

        index.types = {}
        index.imports = {}

        self._imports_by_file = {}

        for file in files:

            path = workspace / file.relative_path

            file.semantic_tokens = self.semantic.build(
                file.relative_path
            )

            seen = set(index.imports)

            try:

                if file.extension == ".py":

                    self._index_python(
                        index,
                        path,
                        file.relative_path,
                    )

                elif file.extension == ".java":

                    self._index_java(
                        index,
                        path,
                        file.relative_path,
                    )

            except Exception:
                continue

            # ``imports`` is last-writer-wins, so a file that imports nothing
            # still has to be recorded as contributing nothing - otherwise
            # ``update_file`` cannot tell whether it was the only importer.
            if len(index.imports) != len(seen):
                self._imports_by_file[file.relative_path] = {
                    dotted
                    for dotted in index.imports
                    if dotted not in seen
                }
            else:
                self._imports_by_file[file.relative_path] = set()

        return index

    def _index_python(
        self,
        index: ProjectIndex,
        path: Path,
        relative_path: str,
    ) -> None:

        key = FILE_CACHE.key(path)

        def parse() -> ast.AST:
            text = FILE_CACHE.text(key, None, "ignore")
            if text is None:
                raise OSError(f"unreadable: {path}")
            return ast.parse(text)

        if key is None:
            tree = parse()
        else:
            # ast.parse -> compile() was ~25% of a full index rebuild, and the
            # index is rebuilt after every edit. Keyed on the same
            # (path, size, mtime_ns) triple the workspace signature uses.
            tree = FILE_CACHE.tree(key, parse)

        package = ""

        for node in walk_definitions(tree):

            # -------------------------
            # import
            # -------------------------

            if isinstance(
                node,
                ast.Import,
            ):

                for alias in node.names:

                    short = alias.asname or alias.name.split(".")[-1]

                    index.imports[
                        alias.name
                    ] = short

            elif isinstance(
                node,
                ast.ImportFrom,
            ):

                if node.module is None:
                    continue

                for alias in node.names:

                    full = (
                        f"{node.module}."
                        f"{alias.name}"
                    )

                    short = (
                        alias.asname
                        or alias.name
                    )

                    index.imports[
                        full
                    ] = short

            # -------------------------
            # class
            # -------------------------

            elif isinstance(
                node,
                ast.ClassDef,
            ):

                symbol = Symbol(
                    name=node.name,
                    kind="class",
                    file=relative_path,
                    line=node.lineno,
                    package=package,
                )

                symbol.semantic_tokens = (
                    self.semantic.build(
                        node.name
                    )
                )

                index.symbols.append(
                    symbol
                )

                index.types[
                    node.name
                ] = relative_path

            # -------------------------
            # function
            # -------------------------

            elif isinstance(
                node,
                ast.FunctionDef,
            ):

                symbol = Symbol(
                    name=node.name,
                    kind="function",
                    file=relative_path,
                    line=node.lineno,
                    package=package,
                )

                symbol.semantic_tokens = (
                    self.semantic.build(
                        node.name
                    )
                )

                index.symbols.append(
                    symbol
                )

            elif isinstance(
                node,
                ast.AsyncFunctionDef,
            ):

                symbol = Symbol(
                    name=node.name,
                    kind="async_function",
                    file=relative_path,
                    line=node.lineno,
                    package=package,
                )

                symbol.semantic_tokens = (
                    self.semantic.build(
                        node.name
                    )
                )

                index.symbols.append(
                    symbol
                )

    def _index_java(
        self,
        index: ProjectIndex,
        path: Path,
        relative_path: str,
    ) -> None:

        source = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        package = ""

        for line in source.splitlines():

            line = line.strip()

            if line.startswith(
                "package "
            ):

                package = (
                    line[8:]
                    .rstrip(";")
                    .strip()
                )

                break
        for line_no, line in enumerate(
            source.splitlines(),
            start=1,
        ):

            stripped = line.strip()

            # -------------------------
            # import
            # -------------------------

            if stripped.startswith(
                "import "
            ):

                full = (
                    stripped[7:]
                    .rstrip(";")
                    .strip()
                )

                short = full.split(".")[-1]

                index.imports[
                    full
                ] = short

                continue

            # -------------------------
            # class / interface / enum / record
            # -------------------------

            for keyword in (
                "class",
                "interface",
                "enum",
                "record",
            ):

                if f" {keyword} " not in stripped:
                    continue

                name = (
                    stripped.split(
                        keyword,
                        1,
                    )[1]
                    .strip()
                    .split()[0]
                    .split("<")[0]
                )

                symbol = Symbol(
                    name=name,
                    kind=keyword,
                    file=relative_path,
                    line=line_no,
                    package=package,
                )

                symbol.semantic_tokens = (
                    self.semantic.build(
                        name
                    )
                )

                index.symbols.append(
                    symbol
                )

                index.types[
                    name
                ] = relative_path

                break

            # -------------------------
            # method
            # -------------------------

            if (
                "(" not in stripped
                or ")" not in stripped
                or stripped.endswith(";")
            ):
                continue

            if any(
                stripped.startswith(prefix)
                for prefix in (
                    "if",
                    "for",
                    "while",
                    "switch",
                    "catch",
                    "return",
                    "new ",
                )
            ):
                continue

            try:

                before = stripped.split(
                    "(",
                    1,
                )[0].strip()

                name = before.split()[-1]

            except Exception:
                continue

            if not name.isidentifier():
                continue

            symbol = Symbol(
                name=name,
                kind="method",
                file=relative_path,
                line=line_no,
                package=package,
            )

            symbol.semantic_tokens = self.semantic.build(
                name
            )

            index.symbols.append(
                symbol
            )

    # ------------------------------------------------------------------
    # Incremental update
    # ------------------------------------------------------------------
    def update_file(
        self,
        index: ProjectIndex,
        path: Path,
    ) -> bool:
        """Re-index a single file into an existing ``ProjectIndex``.

        ``AIAgent.refresh_file`` has always called this method, but it did not
        exist: every "incremental" refresh raised ``AttributeError``, the
        caller swallowed it and set ``project_index = None`` - silently
        turning a one-file update into a full workspace rescan and re-parse on
        the next request.

        Returns ``True`` when the set of symbol *names* declared by this file
        changed. The reference index can only be updated incrementally while
        the global name universe is stable: adding or removing a name changes
        which tokens other files are interested in, so the caller has to
        rebuild it in that case.
        """
        workspace = Path(index.workspace)
        path = Path(path)
        try:
            relative = path.resolve().relative_to(workspace.resolve()).as_posix()
        except ValueError:
            # Not under the workspace: nothing to key on, ask for a full rebuild.
            raise

        file = next(
            (f for f in index.files if f.relative_path == relative),
            None,
        )
        if file is None:
            # Never indexed (newly created file, or one outside the scan). The
            # caller falls back to a full rebuild.
            return False

        names_before = {
            symbol.name
            for symbol in index.symbols
        }

        if not path.is_file():
            # The file was deleted. Leaving its symbols behind would keep a
            # ghost definition queryable for the rest of the session.
            index.symbols = [
                symbol
                for symbol in index.symbols
                if symbol.file != relative
            ]
            index.files = [
                f
                for f in index.files
                if f.relative_path != relative
            ]
            for name, location in list(index.types.items()):
                if location == relative and not any(
                    s.name == name
                    for s in index.symbols
                ):
                    del index.types[name]
            self._forget_unique_entries(index, relative)
            # Deleting a file can remove a name from the universe, so every
            # derived index has to be re-derived.
            return True

        # The position this file's symbols occupy is part of the observable
        # state (callers slice the first N), so remember it and re-insert in
        # place instead of appending at the end.
        first = None
        kept: list[Symbol] = []
        for symbol in index.symbols:
            if symbol.file == relative:
                if first is None:
                    first = len(kept)
            else:
                kept.append(symbol)
        if first is None:
            first = len(kept)
        index.symbols = kept

        # ``types``/``imports`` are global name -> location maps that every file
        # writes into, so an entry can only be dropped when this file was its
        # only contributor.
        for name, location in list(index.types.items()):
            if location != relative:
                continue
            if not any(s.name == name for s in index.symbols):
                del index.types[name]

        self._forget_unique_entries(index, relative)

        try:
            # ``build`` derives file tokens from the relative path, not the
            # stem - match it exactly or an incremental refresh and a full
            # rebuild would disagree.
            file.semantic_tokens = self.semantic.build(
                relative
            )
            # The scanner records the size; context assembly uses it to decide
            # whether a file is worth reading, so it has to track edits too.
            file.size = path.stat().st_size
        except Exception:
            pass

        seen_imports = set(index.imports)
        before = len(index.symbols)
        types_before = dict(index.types)
        try:
            if file.extension == ".py":
                self._index_python(index, path, relative)
            elif file.extension == ".java":
                self._index_java(index, path, relative)
        except Exception:
            # Leave the index exactly as it was rather than half-updated.
            del index.symbols[before:]
            index.types.clear()
            index.types.update(types_before)
            return

        fresh = index.symbols[before:]
        del index.symbols[before:]
        index.symbols[first:first] = fresh

        self._imports_by_file[relative] = {
            dotted
            for dotted in index.imports
            if dotted not in seen_imports
        }

        return names_before != {
            symbol.name
            for symbol in index.symbols
        }

    def _forget_unique_entries(
        self,
        index: ProjectIndex,
        relative: str,
    ) -> None:
        """Drop the ``index.imports`` entries only this file declared.

        ``imports`` is a single flat map written by every file in scan order,
        so an incremental update has to know the per-file contributions to tell
        "safe to drop" from "another file still provides this".
        """
        dropped = self._imports_by_file.pop(relative, None)
        if not dropped:
            return

        still_imported = {
            dotted
            for other, dotted_set in self._imports_by_file.items()
            for dotted in dropped
            if dotted in dotted_set
        }
        for dotted in dropped:
            if dotted not in still_imported:
                index.imports.pop(dotted, None)
