"""Language-agnostic syntax tokenizer for the Studio editor.

Deliberately pure: no tkinter, no widget, no side effects. It turns source text
into a flat list of :class:`Token` spans that the UI layer turns into text-tag
ranges. That split is what makes the highlighting rules testable without a
display.

Design notes
------------
* One combined regex per language family, scanned in a single pass with named
  groups. Tokenizing with independent per-category passes is the classic source
  of "the keyword inside a string got highlighted" and "the quote inside a
  comment closed the comment" bugs; alternation order makes the longest
  correct match win in one step.
* Group order is comment -> string -> decorator -> number -> identifier, so
  comments and strings are consumed before anything inside them can match.
* Line length is capped and total input is capped by the caller, so a
  pathological single-line minified file cannot turn into a catastrophic
  backtrack.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Token kinds, mapped to colours by the UI layer.
COMMENT = "comment"
STRING = "string"
NUMBER = "number"
KEYWORD = "keyword"
BUILTIN = "builtin"
DECORATOR = "decorator"
FUNCTION = "function"
TYPE = "type"
CONSTANT = "constant"
KEY = "key"          # JSON object keys
HEADING = "heading"  # markdown
MARKUP = "markup"    # markdown emphasis / code spans


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    kind: str


# ----------------------------------------------------------------------
# Language definitions
# ----------------------------------------------------------------------

_EXTENSIONS = {
    ".py": "python", ".pyi": "python", ".pyw": "python",
    ".js": "clike", ".mjs": "clike", ".cjs": "clike", ".jsx": "clike",
    ".ts": "clike", ".tsx": "clike", ".mts": "clike", ".cts": "clike",
    ".java": "clike", ".kt": "clike", ".kts": "clike", ".scala": "clike",
    ".c": "clike", ".h": "clike", ".cc": "clike", ".cpp": "clike",
    ".cxx": "clike", ".hpp": "clike", ".hh": "clike", ".cs": "clike",
    ".go": "clike", ".rs": "clike", ".swift": "clike", ".php": "clike",
    ".json": "json", ".jsonc": "json",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ps1": "shell",
    ".md": "markdown", ".markdown": "markdown",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "ini", ".ini": "ini",
    ".cfg": "ini", ".html": "markup", ".xml": "markup", ".css": "css",
    ".sql": "clike", ".lua": "clike",
}

_FILENAMES = {
    "Dockerfile": "shell", "Makefile": "shell", "CMakeLists.txt": "clike",
    "Rakefile": "ruby", "Gemfile": "ruby",
}

_PY_KEYWORDS = {
    "False", "None", "True", "and", "as", "assert", "async", "await", "break",
    "class", "continue", "def", "del", "elif", "else", "except", "finally",
    "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
    "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
    "match", "case", "self", "cls",
}

_PY_BUILTINS = {
    "abs", "all", "any", "bin", "bool", "bytes", "callable", "chr", "dict",
    "dir", "divmod", "enumerate", "eval", "exec", "filter", "float", "format",
    "frozenset", "getattr", "hasattr", "hash", "help", "hex", "id", "input",
    "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max",
    "min", "next", "object", "oct", "open", "ord", "pow", "print", "property",
    "range", "repr", "reversed", "round", "set", "setattr", "slice", "sorted",
    "str", "sum", "super", "tuple", "type", "vars", "zip",
    "ArithmeticError", "AttributeError", "Exception", "FileNotFoundError",
    "ImportError", "KeyError", "NotImplementedError", "OSError", "RuntimeError",
    "StopIteration", "TypeError", "ValueError", "ZeroDivisionError",
}

_CLIKE_KEYWORDS = {
    # shared
    "if", "else", "for", "while", "do", "switch", "case", "default", "break",
    "continue", "return", "goto", "try", "catch", "finally", "throw", "throws",
    "new", "delete", "typeof", "instanceof", "in", "of", "this", "self",
    "static", "const", "let", "var", "function", "class", "extends",
    "implements", "interface", "enum", "struct", "union", "typedef", "public",
    "private", "protected", "abstract", "virtual", "override", "async", "await",
    "yield", "import", "export", "from", "as", "package", "namespace", "using",
    "template", "typename", "operator", "friend", "inline", "explicit", "null",
    "true", "false", "True", "False", "None", "nil", "undefined",
    # rust / go extras
    "fn", "mut", "pub", "impl", "trait", "where", "match", "loop", "unsafe",
    "crate", "pub(crate)", "defer", "chan", "go", "select", "range", "map",
    # c/cpp
    "int", "long", "short", "char", "float", "double", "void", "signed",
    "unsigned", "bool", "size_t", "auto", "register", "volatile", "extern",
    "sizeof", "goto",
}

# Identifier-shaped heuristics shared by the C-like family.
_CLIKE_CONSTANTS = re.compile(
    r"^[A-Z][A-Z0-9_]*$|^_*[A-Z][A-Z0-9_]*_*$"
)
_TYPE_WORDS = {
    "int", "long", "short", "char", "float", "double", "void", "bool",
    "string", "str", "bytes", "byte", "uint", "ulong", "size_t", "object",
    "String", "Integer", "Boolean", "List", "Dict", "Map", "Set", "Array",
    "Number", "Any", "Optional", "var", "dict", "list", "tuple", "set",
}

# The optional prefix (f/r/b/u) and C#'s @ verbatim marker belong to the string
# token; leaving them out colours f"..." with a stray unhighlighted letter.
_PY_STRINGS = (
    r'(?:[rRbBuUfF]{1,2})?(?:"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\''
    r'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')'
)
_CLIKE_STRINGS = (
    r'(?:@@|@)?(?:"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`'
    r"|\'(?:\\.|[^\'\\\n])*\')"
)
_NUMBER = r"0[xXbBoO][0-9a-fA-F_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?[jJfFdDlLuU]*|\.\d[\d_]*(?:[eE][+-]?\d+)?"
_IDENT = r"[A-Za-z_$][A-Za-z0-9_$]*"


def _clike_pattern() -> re.Pattern:
    return re.compile(
        rf"(?P<comment>//[^\n]*|/\*[\s\S]*?\*/|#[^\n]*)"
        rf"|(?P<string>{_CLIKE_STRINGS})"
        rf"|(?P<decorator>@{_IDENT})"
        rf"|(?P<number>{_NUMBER})"
        rf"|(?P<ident>{_IDENT})",
        re.MULTILINE,
    )


def _python_pattern() -> re.Pattern:
    return re.compile(
        rf"(?P<comment>#[^\n]*)"
        rf"|(?P<string>{_PY_STRINGS})"
        rf"|(?P<decorator>@{_IDENT})"
        rf"|(?P<number>{_NUMBER})"
        rf"|(?P<ident>{_IDENT})",
        re.MULTILINE,
    )


_PATTERNS = {
    "python": _python_pattern(),
    "clike": _clike_pattern(),
}


def detect_language(path: Path | str) -> str | None:
    """Return a language id for *path*, or None when unknown."""
    p = Path(path)
    hit = _FILENAMES.get(p.name)
    if hit:
        return hit
    return _EXTENSIONS.get(p.suffix.lower())


# ----------------------------------------------------------------------
# Tokenizers
# ----------------------------------------------------------------------

def _classify_ident(name: str, kind: str) -> str:
    if kind == "python":
        if name in _PY_KEYWORDS:
            return KEYWORD
        if name in _PY_BUILTINS:
            return BUILTIN
        if name.isupper() and len(name) > 1:
            return CONSTANT
        return ""
    # clike family
    if name in _CLIKE_KEYWORDS:
        return KEYWORD
    if name in _TYPE_WORDS:
        return TYPE
    if _CLIKE_CONSTANTS.match(name):
        return CONSTANT
    return ""


def _tokenize_identifiers(
    matches: list[re.Match], out: list[Token], classify
) -> None:
    for m in matches:
        if m.lastgroup != "ident":
            continue
        kind = classify(m.group("ident"))
        if kind:
            out.append(Token(m.start("ident"), m.end("ident"), kind))


def tokenize_python(text: str) -> list[Token]:
    out: list[Token] = []
    for m in _PATTERNS["python"].finditer(text):
        kind = m.lastgroup
        if kind == "ident":
            sub = _classify_ident(m.group("ident"), "python")
            if sub:
                out.append(Token(m.start("ident"), m.end("ident"), sub))
        elif kind in {"comment", "string", "decorator", "number"}:
            out.append(Token(m.start(), m.end(), {
                "comment": COMMENT, "string": STRING,
                "decorator": DECORATOR, "number": NUMBER,
            }[kind]))
    out.sort(key=lambda t: t.start)
    return out


def tokenize_clike(text: str) -> list[Token]:
    out: list[Token] = []
    matches = list(_PATTERNS["clike"].finditer(text))
    for m in matches:
        kind = m.lastgroup
        if kind == "comment":
            out.append(Token(m.start(), m.end(), COMMENT))
        elif kind == "string":
            out.append(Token(m.start(), m.end(), STRING))
        elif kind == "decorator":
            out.append(Token(m.start(), m.end(), DECORATOR))
        elif kind == "number":
            out.append(Token(m.start(), m.end(), NUMBER))
    _tokenize_identifiers(matches, out, lambda name: _classify_ident(name, "clike"))
    out.sort(key=lambda t: t.start)
    return out


_JSON_RE = re.compile(
    rf'(?P<string>"(?:\\.|[^"\\])*")'
    rf"|(?P<number>{_NUMBER})"
    rf"|(?P<ident>\b(?:true|false|null)\b)",
    re.MULTILINE,
)


def tokenize_json(text: str) -> list[Token]:
    out: list[Token] = []
    for m in _JSON_RE.finditer(text):
        if m.lastgroup == "string":
            end = m.end()
            # A string immediately followed by ':' is an object key.
            tail = text[end:end + 1]
            out.append(Token(m.start(), end, KEY if tail == ":" else STRING))
        elif m.lastgroup == "number":
            out.append(Token(m.start(), m.end(), NUMBER))
        elif m.lastgroup == "ident":
            out.append(Token(m.start(), m.end(), CONSTANT))
    out.sort(key=lambda t: t.start)
    return out


_SHELL_RE = re.compile(
    rf"(?P<comment>#[^\n]*)"
    rf"|(?P<string>\"(?:\\.|[^\"\\])*\"|'[^']*')"
    rf"|(?P<variable>\$\{{[^}}]*\}}|\$[A-Za-z_]\w*|\$\d+)"
    rf"|(?P<keyword>\b(?:if|then|else|elif|fi|for|while|do|done|case|esac|"
    rf"function|return|export|local|readonly|set|unset|echo|source|exit)\b)",
    re.MULTILINE,
)


def tokenize_shell(text: str) -> list[Token]:
    out: list[Token] = []
    for m in _SHELL_RE.finditer(text):
        kind = {
            "comment": COMMENT, "string": STRING,
            "variable": CONSTANT, "keyword": KEYWORD,
        }[m.lastgroup]
        out.append(Token(m.start(), m.end(), kind))
    out.sort(key=lambda t: t.start)
    return out


_MD_RE = re.compile(
    r"(?P<heading>^[ \t]{0,3}#{1,6}[^\n]*)"
    r"|(?P<fence>^[ \t]{0,3}(?:```|~~~)[^\n]*)"
    r"|(?P<code>`[^`\n]+`)"
    r"|(?P<markup>\*\*[^*\n]+\*\*|__[^_\n]+__)",
    re.MULTILINE,
)


def tokenize_markdown(text: str) -> list[Token]:
    out: list[Token] = []
    for m in _MD_RE.finditer(text):
        kind = m.lastgroup
        out.append(Token(m.start(), m.end(), {
            "heading": HEADING, "fence": STRING,
            "code": STRING, "markup": MARKUP,
        }[kind]))
    out.sort(key=lambda t: t.start)
    return out


_YAML_RE = re.compile(
    rf"(?P<comment>#[^\n]*)"
    rf"|(?P<string>\"(?:\\.|[^\"\\\n])*\"|'(?:''|[^'\n])*')"
    rf"|(?P<key>^[ \t]*[A-Za-z_][\w.\-]*(?=[ \t]*:(?:[ \t]|$)))"
    rf"|(?P<number>{_NUMBER})"
    rf"|(?P<constant>\b(?:true|false|null|yes|no|on|off)\b)",
    re.MULTILINE,
)


def tokenize_yaml(text: str) -> list[Token]:
    out: list[Token] = []
    for m in _YAML_RE.finditer(text):
        kind = {
            "comment": COMMENT, "string": STRING, "key": KEY,
            "number": NUMBER, "constant": CONSTANT,
        }[m.lastgroup]
        out.append(Token(m.start(), m.end(), kind))
    out.sort(key=lambda t: t.start)
    return out


_TOKENIZERS = {
    "python": tokenize_python,
    "clike": tokenize_clike,
    "json": tokenize_json,
    "shell": tokenize_shell,
    "markdown": tokenize_markdown,
    "yaml": tokenize_yaml,
    "ruby": tokenize_clike,
    "markup": tokenize_clike,
    "ini": tokenize_shell,
    "css": tokenize_clike,
}


def tokenize(text: str, language: str | None) -> list[Token]:
    """Tokenize *text*; returns [] for unknown or empty languages."""
    if not text or not language:
        return []
    fn = _TOKENIZERS.get(language)
    return fn(text) if fn else []


def tokenize_file(path: Path | str, text: str) -> list[Token]:
    return tokenize(text, detect_language(path))
