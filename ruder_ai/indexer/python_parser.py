"""Python source parser for RuderAI."""

from __future__ import annotations

import ast
from pathlib import Path

from .models import FileInfo, Symbol


class PythonParser:

    def parse(self, file: FileInfo) -> list[Symbol]:

        path = Path(file.path)

        try:
            source = path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            return []

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        symbols: list[Symbol] = []

        visitor = _Visitor(
            file=file.relative_path,
            symbols=symbols,
        )

        visitor.visit(tree)

        return symbols


class _Visitor(ast.NodeVisitor):

    def __init__(
        self,
        file: str,
        symbols: list[Symbol],
    ):
        self.file = file
        self.symbols = symbols

        self.class_stack: list[str] = []

    # ------------------------
    # Class
    # ------------------------

    def visit_ClassDef(self, node: ast.ClassDef):

        self.symbols.append(
            Symbol(
                name=node.name,
                kind="class",
                file=self.file,
                line=node.lineno,
                container=".".join(self.class_stack)
                if self.class_stack
                else None,
            )
        )

        self.class_stack.append(node.name)

        self.generic_visit(node)

        self.class_stack.pop()

    # ------------------------
    # Function
    # ------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef):

        self.symbols.append(
            Symbol(
                name=node.name,
                kind="function",
                file=self.file,
                line=node.lineno,
                container=self.class_stack[-1]
                if self.class_stack
                else None,
            )
        )

        self.generic_visit(node)

    # ------------------------
    # Async Function
    # ------------------------

    def visit_AsyncFunctionDef(
        self,
        node: ast.AsyncFunctionDef,
    ):

        self.symbols.append(
            Symbol(
                name=node.name,
                kind="async_function",
                file=self.file,
                line=node.lineno,
                container=self.class_stack[-1]
                if self.class_stack
                else None,
            )
        )

        self.generic_visit(node)

    # ------------------------
    # Variable
    # ------------------------

    def visit_Assign(self, node: ast.Assign):

        for target in node.targets:

            if isinstance(target, ast.Name):

                self.symbols.append(
                    Symbol(
                        name=target.id,
                        kind="variable",
                        file=self.file,
                        line=node.lineno,
                        container=self.class_stack[-1]
                        if self.class_stack
                        else None,
                    )
                )

        self.generic_visit(node)
