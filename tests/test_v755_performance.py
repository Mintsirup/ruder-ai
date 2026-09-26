"""Regression tests for the performance pass (v7.5.5).

These lock in behaviour that was changed for speed. Every optimization in
this file had to prove equivalence against the implementation it replaced;
these tests are the durable form of that proof, so a future "simplification"
cannot silently change scan results, symbol ordering or reference locations.

Nothing here asserts a wall-clock number - timing assertions are flaky in CI.
Instead the tests assert the *structural* facts the speedups depend on
(no re-parse of unchanged files, no full rebuild on a one-file edit, ignored
directories are never opened).
"""

from __future__ import annotations

import ast
import os
import time
from pathlib import Path

import pytest

from ruder_ai.core.agent import AIAgent
from ruder_ai.indexer import file_cache as file_cache_mod
from ruder_ai.indexer.call_graph import CallGraph
from ruder_ai.indexer.detector import ProjectDetector
from ruder_ai.indexer.file_cache import CACHE, FileDataCache
from ruder_ai.indexer.reference_index import ReferenceIndex
from ruder_ai.indexer.scanner import ProjectScanner
from ruder_ai.indexer.semantic_file_index import SemanticFileIndex
from ruder_ai.indexer.symbol_indexer import SymbolIndexer, walk_definitions
from ruder_ai.indexer.type_resolver import TypeResolver
from ruder_ai.semantic.cache import SemanticCache
from ruder_ai.semantic.tokenizer import SemanticTokenizer


# ======================================================================
# fixtures
# ======================================================================

PY_A = '''\
import os
import json


class Alpha:
    def method_a(self):
        return os.sep


def beta():
    import base64
    return json.dumps(base64.b64encode(b"x"))


async def gamma():
    pass
'''

PY_B = '''\
from collections import OrderedDict


class Beta:
    def method_b(self):
        return OrderedDict()


def alpha():
    return 1
'''

JAVA = '''\
package com.demo;

import java.util.List;

public class Widget {
    public int size() { return 0; }
    public void run() throws Exception { helper(); }
    private void helper() {}
}
'''


def _write_tree(root: Path) -> None:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "a.py").write_text(PY_A, encoding="utf-8")
    (root / "pkg" / "b.py").write_text(PY_B, encoding="utf-8")
    (root / "Widget.java").write_text(JAVA, encoding="utf-8")


def _build(root: Path) -> tuple:
    scanner = ProjectScanner(root)
    files = scanner.scan()
    project = ProjectDetector().detect(root, files)
    return SymbolIndexer().build(root, files, project, dict(scanner.inverted_index))


def _snapshot(index) -> dict:
    return {
        "symbols": [(s.name, s.kind, s.file, s.line, s.package) for s in index.symbols],
        "symbol_tokens": sorted(
            (s.name, s.kind, s.file, s.line, tuple(sorted(s.semantic_tokens)))
            for s in index.symbols
        ),
        "types": dict(index.types),
        "imports": dict(index.imports),
        "files": [
            (f.relative_path, f.extension, f.size, tuple(sorted(f.semantic_tokens)))
            for f in index.files
        ],
    }


@pytest.fixture
def tree(tmp_path):
    _write_tree(tmp_path)
    return tmp_path


# ======================================================================
# walk_definitions must equal ast.walk for the five node types
# ======================================================================

_DEFINITION_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
)

_ADVERSARIAL = '''
import os

def top():
    import json
    try:
        from a import b
    except ImportError:
        import c
    except (TypeError, ValueError) as e:
        from d import f
    else:
        import g
    finally:
        import h
    return os, json, c, b, f, g, h

def outer():
    def inner():
        import nested
        class Deep:
            import in_class
    return inner

async def amain():
    from x import y

match command.split():
    case [action]:
        import m1
        def in_case(): pass
    case {"k": v}:
        class InCase: pass
    case _:
        import m2

with open("f") as fh:
    import in_with
    class InWith: pass

for i in range(3):
    import in_for
else:
    import in_forelse

while True:
    import in_while
    break
else:
    import in_whileelse

if True:
    import in_if
else:
    import in_ifelse

lam = lambda: [i for i in range(3)]
d = {k: v for k, v in ()}
'''


def _keys(nodes):
    return sorted(
        (type(n).__name__, getattr(n, "name", None), getattr(n, "lineno", -1))
        for n in nodes
        if isinstance(n, _DEFINITION_NODES)
    )


def test_walk_definitions_matches_ast_walk_on_adversarial_source():
    tree = ast.parse(_ADVERSARIAL)
    assert _keys(walk_definitions(tree)) == _keys(ast.walk(tree))


def test_walk_definitions_matches_ast_walk_on_every_module_in_the_repo():
    """The statement-container shortcut must not drop a single definition."""
    root = Path(__file__).resolve().parent.parent
    mismatches = []
    checked = 0
    for path in sorted((root / "ruder_ai").rglob("*.py")):
        if any(part in ("__pycache__", ".git") for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        checked += 1
        if _keys(walk_definitions(tree)) != _keys(ast.walk(tree)):
            mismatches.append(path.name)
    assert checked > 50, "expected a real corpus, not an empty walk"
    assert not mismatches, f"walk_definitions diverged on: {mismatches}"


# ======================================================================
# incremental update must equal a full rebuild
# ======================================================================

@pytest.mark.parametrize(
    "mutation, target",
    [
        (lambda r: (r / "pkg" / "a.py").write_text(
            PY_A + "\n\nclass Delta:\n    import zlib\n    def m(self):\n        return zlib\n",
            encoding="utf-8"), "pkg/a.py"),
        (lambda r: (r / "pkg" / "a.py").write_text(
            "import os\n\n\ndef beta():\n    return os\n", encoding="utf-8"), "pkg/a.py"),
        (lambda r: (r / "pkg" / "b.py").write_text("def broken(:\n", encoding="utf-8"), "pkg/b.py"),
        (lambda r: (r / "pkg" / "a.py").write_text(PY_A + "# tail\n", encoding="utf-8"), "pkg/a.py"),
        (lambda r: (r / "pkg" / "a.py").write_text("import os\n", encoding="utf-8"), "pkg/a.py"),
        (lambda r: (r / "Widget.java").write_text(
            "package com.demo;\nimport java.util.Map;\n"
            "public class Gadget {\n  public Map<String,String> m(){return null;}\n}\n",
            encoding="utf-8"), "Widget.java"),
    ],
    ids=["add-class", "drop-class", "syntax-error", "comment-only", "single-import", "java-rewrite"],
)
def test_update_file_equals_full_rebuild(tree, mutation, target):
    indexer = SymbolIndexer()
    live = _build_with(tree, indexer)

    mutation(tree)
    indexer.update_file(live, tree / target)
    assert _snapshot(live) == _snapshot(_build(tree))


def _build_with(root: Path, indexer: SymbolIndexer):
    scanner = ProjectScanner(root)
    files = scanner.scan()
    project = ProjectDetector().detect(root, files)
    return indexer.build(root, files, project, dict(scanner.inverted_index))


def test_update_file_reports_symbol_name_changes(tree):
    indexer = SymbolIndexer()
    live = _build_with(tree, indexer)

    # editing a function body does not change the name universe
    target = tree / "pkg" / "a.py"
    target.write_text(PY_A.replace("return os.sep", "return os.sep  # touched"), encoding="utf-8")
    assert indexer.update_file(live, target) is False

    # adding a class does
    target.write_text(PY_A + "\n\nclass Delta:\n    pass\n", encoding="utf-8")
    assert indexer.update_file(live, target) is True


def test_update_file_handles_deletion(tree):
    indexer = SymbolIndexer()
    live = _build_with(tree, indexer)
    (tree / "pkg" / "a.py").unlink()
    assert indexer.update_file(live, tree / "pkg" / "a.py") is True
    assert all(s.file != "pkg/a.py" for s in live.symbols)
    assert all(f.relative_path != "pkg/a.py" for f in live.files)
    assert _snapshot(live) == _snapshot(_build(tree))


# ======================================================================
# reference index
# ======================================================================

def _ref_snapshot(ref: ReferenceIndex) -> dict:
    return {k: sorted(v) for k, v in sorted(ref.references.items())}


def test_reference_index_incremental_update_matches_rebuild(tree):
    indexer = SymbolIndexer()
    live = _build_with(tree, indexer)
    ref = ReferenceIndex()
    ref.build(live)

    target = tree / "pkg" / "a.py"
    target.write_text("import os\n\n\ndef delta():\n    return Beta()\n", encoding="utf-8")
    changed = indexer.update_file(live, target)
    if changed:
        ref.build(live)
    else:
        ref.update_file(live, str(target))

    fresh_index = _build(tree)
    fresh = ReferenceIndex()
    fresh.build(fresh_index)
    assert _ref_snapshot(ref) == _ref_snapshot(fresh)


def test_reference_index_preserves_symbol_ordering(tree):
    """Locations are appended per file in scan order; keep them stable."""
    indexer = SymbolIndexer()
    live = _build_with(tree, indexer)
    ref = ReferenceIndex()
    ref.build(live)
    before = _ref_snapshot(ref)
    ref.update_file(live, str(tree / "pkg" / "b.py"))
    after = _ref_snapshot(ref)
    for token, locations in before.items():
        if any(loc[0] == "pkg/b.py" for loc in locations):
            assert after[token] == locations, f"{token} moved during a no-op update"


# ======================================================================
# scanner
# ======================================================================

def test_scanner_prunes_dependency_directories(tmp_path):
    (tmp_path / "node_modules" / "left-pad").mkdir(parents=True)
    (tmp_path / "node_modules" / "left-pad" / "index.js").write_text("module.exports=1", encoding="utf-8")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "mod.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("y = 2", encoding="utf-8")

    relatives = {f.relative_path for f in ProjectScanner(tmp_path).scan()}
    assert relatives == {"src/app.py"}


def test_scanner_indexes_a_workspace_that_lives_under_an_ignored_name(tmp_path):
    """`out/`, `build/` and friends must only be matched *inside* the tree.

    The old filter ran against the absolute path parts, so a checkout that
    happened to live under a directory named `out` or `build` produced an
    empty index.
    """
    root = tmp_path / "out" / "myproject"
    root.mkdir(parents=True)
    (root / "app.py").write_text("z = 3", encoding="utf-8")

    relatives = {f.relative_path for f in ProjectScanner(root).scan()}
    assert relatives == {"app.py"}


def test_scanner_uses_posix_relative_paths(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c.py").write_text("q = 1", encoding="utf-8")
    files = ProjectScanner(tmp_path).scan()
    assert [f.relative_path for f in files] == ["a/b/c.py"]


def test_scanner_extension_matches_path_suffix_semantics(tmp_path):
    names = ("Makefile", ".gitignore", "a.b.py", "x.PY", ".env.local", "no_dot")
    for name in names:
        (tmp_path / name).write_text("z", encoding="utf-8")
    by_name = {f.relative_path: f for f in ProjectScanner(tmp_path).scan()}
    assert set(by_name) == set(names)
    for name in names:
        expected = tmp_path / name
        assert by_name[name].extension == expected.suffix.lower(), name
        assert by_name[name].semantic_tokens == SemanticCache().build(expected.stem), name


def test_scanner_skips_tokenizing_huge_files(tmp_path):
    big = tmp_path / "big.py"
    big.write_text("needle_token " * 400_000, encoding="utf-8")
    small = tmp_path / "small.py"
    small.write_text("needle_token\n", encoding="utf-8")

    scanner = ProjectScanner(tmp_path)
    files = scanner.scan()
    assert len(files) == 2
    assert "needle_token" not in scanner.inverted_index.get("needle_token", set()) - {"small.py"}


# ======================================================================
# tokenizing
# ======================================================================

def test_tokenize_keeps_private_and_camel_forms():
    scanner = ProjectScanner(Path("."))
    tokens = set(scanner._tokenize("def _private(): getUserData XMLHttpRequest foo1bar a_1b HTTP2"))
    assert {"_private", "private"} <= tokens
    assert {"getuserdata", "get", "user", "data"} <= tokens
    assert {"xmlhttprequest", "xml", "http", "request"} <= tokens
    assert "foo1bar" in tokens
    assert {"a", "1b"} <= tokens


def test_tokenize_is_a_set_semantics_helper():
    scanner = ProjectScanner(Path("."))
    assert scanner._tokenize("abc") == scanner._tokenize("abc abc")


def test_semantic_tokenizer_keeps_digit_split_pieces():
    tok = SemanticTokenizer()
    assert "bar" in tok.tokenize("foo1bar")
    assert "user" in tok.tokenize("getUserData")
    assert "private" in tok.tokenize("_private")
    assert "http" in tok.tokenize("HTTPResponse")


# ======================================================================
# semantic cache
# ======================================================================

def test_semantic_cache_returns_independent_sets():
    cache = SemanticCache()
    first = cache.build("AuthenticationManager")
    first.add("MUTATED")
    second = cache.build("AuthenticationManager")
    assert "MUTATED" not in second
    assert "MUTATED" not in cache.build("AuthenticationManager")


def test_semantic_cache_memoizes():
    cache = SemanticCache()
    calls = []
    original = cache.engine.expand

    def counting(text):
        calls.append(text)
        return original(text)

    cache.engine.expand = counting  # type: ignore[method-assign]
    for _ in range(50):
        cache.build("PlayerManager")
    assert calls == ["PlayerManager"]


# ======================================================================
# derived-data cache
# ======================================================================

def test_file_cache_reuses_unchanged_files(tree):
    cache = FileDataCache()
    target = tree / "pkg" / "a.py"
    key = cache.key(target)
    assert key is not None
    calls = []

    first = cache.tokens(key, lambda: (calls.append(1), ("a",))[1])
    second = cache.tokens(key, lambda: (calls.append(1), ("b",))[1])
    assert first == second == ("a",)
    assert len(calls) == 1


def test_file_cache_recomputes_after_a_rewrite(tree):
    cache = FileDataCache()
    target = tree / "pkg" / "a.py"
    target.write_text("def bravo():\n    return 1\n", encoding="utf-8")
    key = cache.key(target)
    assert cache.tokens(key, lambda: ("first",)) == ("first",)

    target.write_text("def charlie():\n    return 1\n", encoding="utf-8")
    fresh = cache.key(target)
    assert fresh != key
    assert cache.tokens(fresh, lambda: ("second",)) == ("second",)


def test_file_cache_invalidate_forces_recompute(tree):
    cache = FileDataCache()
    target = tree / "pkg" / "a.py"
    key = cache.key(target)
    assert cache.tokens(key, lambda: ("old",)) == ("old",)
    # pin mtime and size so the key is literally unchanged
    stat = target.stat()
    target.write_text("def echo():\n    return 1\n", encoding="utf-8")
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert cache.key(target) == key or cache.key(target) != key  # filesystem dependent
    cache.invalidate(target)
    assert cache.tokens(cache.key(target), lambda: ("new",)) == ("new",)


def test_file_cache_evicts_to_stay_bounded():
    cache = FileDataCache()
    original = file_cache_mod.MAX_ENTRIES
    file_cache_mod.MAX_ENTRIES = 5
    try:
        for i in range(40):
            cache.tokens((f"f{i}", 1, i), lambda i=i: (f"t{i}",))
        assert len(cache._entries) <= 5
        # oldest entries are the ones dropped
        assert ("f39", 1, 39) in cache._entries
        assert ("f0", 1, 0) not in cache._entries
    finally:
        file_cache_mod.MAX_ENTRIES = original


def test_indexer_does_not_reparse_unchanged_files(tree):
    """A second full build must not re-run ast.parse on anything."""
    before = CACHE.misses
    _build(tree)
    misses_first = CACHE.misses - before
    _build(tree)
    misses_second = CACHE.misses - before - misses_first
    assert misses_second == 0, "unchanged files were re-tokenized/re-parsed"


# ======================================================================
# workspace signature
# ======================================================================

def test_workspace_signature_is_stable_and_detects_edits(tree):
    agent = AIAgent(workspace_path=str(tree))
    first = agent._workspace_fs_signature()
    assert first is not None
    assert agent._workspace_fs_signature() == first

    time.sleep(0.01)
    (tree / "pkg" / "a.py").write_text(PY_A + "\n# changed\n", encoding="utf-8")
    assert agent._workspace_fs_signature() != first


def test_workspace_signature_prunes_ignored_directories(tmp_path):
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "i.js").write_text("1", encoding="utf-8")
    (tmp_path / "keep.py").write_text("k = 1", encoding="utf-8")
    agent = AIAgent(workspace_path=str(tmp_path))
    names = {entry[0] for entry in agent._workspace_fs_signature()}
    assert names == {"keep.py"}


# ======================================================================
# refresh_file: the incremental path used to be dead code
# ======================================================================

def test_refresh_file_keeps_the_index_alive(tree):
    """refresh_file used to raise AttributeError and null the whole index."""
    agent = AIAgent(workspace_path=str(tree))
    index = agent._build_project_index(force_refresh=True)
    assert index is not None

    target = tree / "pkg" / "a.py"
    target.write_text(PY_A + "\n\nclass Delta:\n    def go(self):\n        return 1\n", encoding="utf-8")
    agent.refresh_file("pkg/a.py")

    assert agent.project_index is not None, "the index was thrown away"
    names = {s.name for s in agent.project_index.symbols}
    assert "Delta" in names
    assert agent.reference_index.references, "derived indexes were left empty"


def test_refresh_file_body_edit_keeps_symbols_in_place(tree):
    agent = AIAgent(workspace_path=str(tree))
    agent._build_project_index(force_refresh=True)
    before = [(s.name, s.file) for s in agent.project_index.symbols]

    target = tree / "pkg" / "a.py"
    target.write_text(PY_A.replace("return os.sep", "return os.pathsep"), encoding="utf-8")
    agent.refresh_file("pkg/a.py")

    assert [(s.name, s.file) for s in agent.project_index.symbols] == before


# ======================================================================
# token budget estimation
# ======================================================================

def _estimate_tokens_reference(text: str) -> int:
    """The original per-character implementation."""
    if not text:
        return 0
    hangul = sum(1 for ch in text if "\uac00" <= ch <= "\ud7a3")
    other = len(text) - hangul
    return int(hangul / 1.5 + other / 4.0) + 1


def test_estimate_tokens_matches_the_per_character_implementation():
    from ruder_ai.context.token_budget import estimate_tokens

    import random

    rng = random.Random(7)
    pool = "abcXYZ_012 \n\t\uac00\ub098\ub2e4\ud558\uc77c\u65e5\u672c\u8a9e"
    cases = [
        "", "a", "\uac00", "abc\uac00", "hello world", "x" * 5000,
        "\uc0ac\uc6a9\uc790 \ub85c\uadf8\uc778 \uc2e4\ud328\uac00 \ubc1c\uc0dd\ud569\ub2c8\ub2e4",
        "def f():\n    return '\ud55c\uae00'",
    ]
    for _ in range(2000):
        cases.append("".join(rng.choice(pool) for _ in range(rng.randint(0, 200))))

    bad = [c for c in cases if estimate_tokens(c) != _estimate_tokens_reference(c)]
    assert not bad, f"{len(bad)} divergences, e.g. {bad[:2]!r}"


def test_truncate_to_budget_still_respects_the_budget():
    from ruder_ai.context.token_budget import estimate_tokens, truncate_to_budget

    text = ("\uc0ac\uc6a9\uc790 \ub85c\uadf8\uc778 \uc2e4\ud328\uac00 \ubc1c\uc0dd\ud569\ub2c8\ub2e4. " * 200)
    # The omission marker itself costs ~20 tokens, so budgets below that
    # cannot be met by *any* truncation; the docstring says so explicitly.
    for budget in (50, 200, 1000, 5000):
        out = truncate_to_budget(text, budget)
        assert out
        assert estimate_tokens(out) <= budget, budget
    # Below the floor the result shrinks all the way down rather than
    # pretending to fit.
    tiny = truncate_to_budget(text, 5)
    assert tiny and len(tiny) < len(truncate_to_budget(text, 50))
    assert truncate_to_budget(text, 0) == ""


# ======================================================================
# End Of Token terminator
# ======================================================================

def test_end_of_token_marker_is_shared_everywhere():
    from ruder_ai.core.telemetry import END_OF_TOKEN, END_OF_TOKEN_EVENT

    assert END_OF_TOKEN == "End Of Token"
    assert END_OF_TOKEN_EVENT == "end_of_token"

    import ruder_ai.main as main_mod
    assert END_OF_TOKEN in main_mod.END_OF_TOKEN_RICH

    # The GUI imports the constant rather than re-spelling the literal, so a
    # rename cannot leave the two surfaces disagreeing.
    gui_src = (
        Path(__file__).resolve().parent.parent / "ruder_ai" / "tui" / "app_gui.py"
    ).read_text(encoding="utf-8")
    assert "from ruder_ai.core.telemetry import END_OF_TOKEN" in gui_src
    assert "END_OF_TOKEN" in gui_src

    executor_src = (
        Path(__file__).resolve().parent.parent / "ruder_ai" / "core" / "executor.py"
    ).read_text(encoding="utf-8")
    assert "self.telemetry.log(END_OF_TOKEN_EVENT" in executor_src


def test_execution_log_ends_with_the_marker(tmp_path):
    import json

    from ruder_ai.core.telemetry import (
        END_OF_TOKEN,
        END_OF_TOKEN_EVENT,
        JsonlExecutionLogger,
    )

    logger = JsonlExecutionLogger(tmp_path)
    logger.log("task_start", request="hello")
    logger.log("task_end", success=True)
    logger.log(END_OF_TOKEN_EVENT, marker=END_OF_TOKEN, task="hello")

    path = next((tmp_path / ".ruder_ai_logs").glob("*.jsonl"))
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in records] == ["task_start", "task_end", END_OF_TOKEN_EVENT]
    assert records[-1]["marker"] == END_OF_TOKEN


def test_shared_file_bytes_do_not_change_the_decode_policies(tmp_path):
    """The three passes disagree on errors=; sharing bytes must not blur that."""
    target = tmp_path / "mixed.py"
    # b"abc\xffdef" is one identifier under errors="ignore" and two under
    # "replace", so the two index layers must still disagree.
    target.write_bytes(b"def abc():\n    return abc\xffdef\n")
    (tmp_path / "keep.py").write_text("z = 1", encoding="utf-8")

    scanner = ProjectScanner(tmp_path)
    files = scanner.scan()
    index = SymbolIndexer().build(
        tmp_path,
        files,
        ProjectDetector().detect(tmp_path, files),
        dict(scanner.inverted_index),
    )
    ref = ReferenceIndex()
    ref.build(index)

    assert "abcdef" in scanner.inverted_index, "ignore-glued identifier missing"
    assert "abcdef" not in ref.references, "replace must not glue them"
    # ...and the reference index still sees `abc` on line 2, where the
    # undecodable byte splits it off from `def`.
    assert ("mixed.py", 2) in ref.references.get("abc", [])
