"""Project Symbol Indexer."""

from __future__ import annotations

import ast
from pathlib import Path

from ruder_ai.indexer.models import (
    FileInfo,
    ProjectIndex,
    ProjectInfo,
    Symbol,
)
from ruder_ai.semantic.cache import SemanticCache


class SymbolIndexer:
    """
    프로젝트의 Symbol을 추출하여
    ProjectIndex를 생성한다.
    """

    def __init__(self):

        self.semantic = SemanticCache()

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

        for file in files:

            path = workspace / file.relative_path

            file.semantic_tokens = self.semantic.build(
                file.relative_path
            )

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

        return index

    def _index_python(
        self,
        index: ProjectIndex,
        path: Path,
        relative_path: str,
    ) -> None:

        source = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        tree = ast.parse(source)

        package = ""

        for node in ast.walk(tree):

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
