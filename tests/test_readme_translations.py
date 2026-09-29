"""Keep the four README translations structurally identical.

Translations drift the moment nobody notices: a new section lands in the
English file, a command block is edited in one language and not the others,
or a switcher link points at a file that was renamed. None of that breaks the
build — it just leaves readers of one language quietly reading a stale
document, which is exactly the failure these tests exist to make loud.

What is compared:

* the section outline (heading depth and order, ignoring heading text)
* fenced code blocks — byte-identical, except for translated ``#`` comments
* inline code spans, since commands and identifiers must not be reworded
* the language switcher, which must exist in all four and link to all four

Heading *text* is deliberately not compared: translating "Turn Intent" is the
point of the exercise. ``Translations`` is the one exception — it is
navigation rather than prose, so it must appear in every file.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SOURCE = "README.md"
TRANSLATIONS = ("README.ko.md", "README.ja.md", "README.zh.md")
ALL_FILES = (SOURCE, *TRANSLATIONS)
VERSION_FILE_NAME = "VERSION"

#: Blocks that legitimately differ: a ``#`` comment in a shell snippet is
#: user-facing prose, so it is translated along with everything else.
COMMENT_PREFIXES = ("#",)

#: Endonym shown for each file in the switcher.
DISPLAY_NAME = {
    "README.md": "English",
    "README.ko.md": "한국어",
    "README.ja.md": "日本語",
    "README.zh.md": "简体中文",
}


def _read(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def _outline(text: str) -> list[int]:
    """Heading depths in document order, ignoring fenced code blocks.

    A ``## CLI 엔트리포인트`` line inside the survey sample output is part of
    the program's output, not of the document, so fences are skipped.
    """
    depths: list[int] = []
    in_fence = False
    for line in text.split("\n"):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6}) ", line)
        if match:
            depths.append(len(match.group(1)))
    return depths


def _fences_balanced(text: str, name: str) -> None:
    assert text.count("```") % 2 == 0, f"{name}: unbalanced code fence"


def _code_blocks(text: str) -> list[str]:
    return re.findall(r"```[^\n]*\n(.*?)```", text, re.S)


def _strip_comment_only_differences(block: str) -> str:
    """Drop trailing ``# ...`` comments so translated comments compare equal.

    Only whole-line comments and trailing comments on shell commands are
    removed; command names, flags, paths and arguments are left intact, so a
    translation that rewrites ``--model`` into something else still fails.
    """
    lines = []
    for line in block.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(COMMENT_PREFIXES):
            continue
        # Trailing comment on a command line, honouring quotes so a '#'
        # inside a path or argument is not mistaken for a comment.
        if " #" in line:
            head, _, tail = line.partition(" #")
            if tail and not head.rstrip().endswith(("'", '"', "`")):
                lines.append(head)
                continue
        lines.append(line)
    return "\n".join(lines)


def _inline_code(text: str) -> list[str]:
    """Inline code spans, with fenced blocks removed."""
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.findall(r"`([^`\n]+)`", body)


@pytest.fixture(scope="module")
def docs() -> dict[str, str]:
    return {name: _read(name) for name in ALL_FILES}


# --- existence and switcher ------------------------------------------------


@pytest.mark.parametrize("name", ALL_FILES)
def test_every_readme_exists(name):
    path = REPO / name
    assert path.is_file(), f"{name} is missing"
    assert path.stat().st_size > 0, f"{name} is empty"


@pytest.mark.parametrize("name", ALL_FILES)
def test_every_readme_links_to_every_other_readme(docs, name):
    """A language you cannot reach from any page is effectively unpublished.

    Checked in the switcher specifically rather than anywhere in the file: the
    Translations table repeats the same links, so a search over the whole
    document would keep passing after the switcher entry was broken.
    """
    body = docs[name]
    switcher = next(
        (l for l in body.split("\n")[:8] if all(f"]({t})" in l for t in ALL_FILES)),
        None,
    )
    assert switcher is not None, f"{name}: no single switcher line found"
    for target in ALL_FILES:
        assert f"]({target})" in switcher, (
            f"{name}: switcher does not link to {target}"
        )
        # A link whose label and target disagree is worse than no link.
        labelled = re.findall(
            r"\[([^\]]+)\]\((README[^)]*)\)", switcher
        )
        for label, href in labelled:
            assert href in ALL_FILES, f"{name}: switcher points at {href}"


@pytest.mark.parametrize("name", ALL_FILES)
def test_switcher_marks_the_current_language(docs, name):
    """Exactly one entry is marked, and it is this file.

    The English file is the source, so it has no bold to mark — every other
    language bolds its own link and that is the whole convention.
    """
    head = "\n".join(docs[name].split("\n")[:8])
    line = next(
        (l for l in head.split("\n") if all(f"]({t})" in l for t in ALL_FILES)),
        None,
    )
    assert line is not None, f"{name}: no single switcher line found"

    own = DISPLAY_NAME[name]
    # Count the label-to-file pairs that are emphasised, e.g. **[日本語](...)**
    pairs = re.findall(
        r"(\*\*)?\[([^\]]+)\]\((README[^)]+)\)(?(1)\*\*)", line
    )
    assert pairs, f"{name}: could not parse switcher: {line!r}"
    for bold, label, target in pairs:
        assert label == DISPLAY_NAME[target], (
            f"{name}: {target} is labelled {label!r}, "
            f"expected {DISPLAY_NAME[target]!r}"
        )
    marked = [target for bold, _, target in pairs if bold]
    if name == SOURCE:
        assert not marked, f"{name}: the source file should mark nothing"
    else:
        assert marked == [name], f"{name}: marked {marked}, expected [{name}]"


# --- structural parity -----------------------------------------------------


@pytest.mark.parametrize("name", ALL_FILES)
def test_code_fences_are_balanced(docs, name):
    _fences_balanced(docs[name], name)


def test_section_outline_matches_the_english_source(docs):
    expected = _outline(docs[SOURCE])
    for name in TRANSLATIONS:
        actual = _outline(docs[name])
        assert actual == expected, (
            f"{name} section outline differs from {SOURCE}: "
            f"{len(actual)} vs {len(expected)} headings"
        )
        # A same-length but reordered outline means a section moved, which the
        # depth comparison above would miss.
        if actual == expected:
            assert _outline_order_signature(docs[name]) == _outline_order_signature(
                docs[SOURCE]
            ), f"{name}: sections are in a different order than {SOURCE}"


def _outline_order_signature(text: str) -> list[tuple[int, str]]:
    """(depth, position-within-parent) for each heading, ignoring its text."""
    signature: list[tuple[int, int]] = []
    counters: dict[int, int] = {}
    for depth in _outline(text):
        counters[depth] = counters.get(depth, -1) + 1
        for deeper in [d for d in counters if d > depth]:
            counters[deeper] = -1
        signature.append((depth, counters[depth]))
    return signature


def test_translations_section_exists_in_every_language(docs):
    """Navigation, not prose — it must not be the one English-only section.

    Matched as an exact heading, not a prefix: ``## 翻訳情報`` was passing
    before because the check only asked whether a heading *started* with
    ``## 翻訳``.
    """
    expected = {
        "README.md": "## Translations",
        "README.ko.md": "## 번역",
        "README.ja.md": "## 翻訳",
        "README.zh.md": "## 翻译",
    }
    for name in ALL_FILES:
        headings = [
            l.strip() for l in docs[name].split("\n") if re.match(r"^## ", l)
        ]
        assert expected[name] in headings, (
            f"{name}: expected a {expected[name]!r} section, "
            f"found {[h for h in headings if h.startswith('##')][-4:]}"
        )


def test_every_language_has_exactly_one_h1(docs):
    for name in ALL_FILES:
        h1s = [l for l in docs[name].split("\n") if l.startswith("# ")]
        assert len(h1s) == 1, f"{name}: {len(h1s)} top-level headings"


def test_code_block_count_matches(docs):
    """Same examples, same order — a dropped block means a dropped section."""
    expected = len(_code_blocks(docs[SOURCE]))
    for name in TRANSLATIONS:
        assert len(_code_blocks(docs[name])) == expected, (
            f"{name}: {len(_code_blocks(docs[name]))} code blocks, "
            f"{SOURCE} has {expected}"
        )


@pytest.mark.parametrize("name", TRANSLATIONS)
def test_code_blocks_are_verbatim_apart_from_comments(docs, name):
    """Commands, paths, flags and numbers must not be reworded."""
    english = _code_blocks(docs[SOURCE])
    translated = _code_blocks(docs[name])
    offenders = [
        i for i, (a, b) in enumerate(zip(english, translated))
        if _strip_comment_only_differences(a) != _strip_comment_only_differences(b)
    ]
    assert not offenders, (
        f"{name}: code blocks {offenders} differ beyond translated comments"
    )


@pytest.mark.parametrize("name", TRANSLATIONS)
def test_inline_code_spans_match(docs, name):
    """`ToolExecutor`, `ruder-ai-ko` and friends are not translatable.

    Compared as a multiset rather than a subset: an earlier version only
    checked that each English span appeared *somewhere*, so replacing the one
    genuine ``ToolExecutor`` with a translated word still passed as long as
    three other mentions were left alone.

    A translation may mention a span *more* often than the source — some
    languages need to name the owning module where English does not — so only
    a shortfall is an error.
    """
    expected = Counter(_inline_code(docs[SOURCE]))
    actual = Counter(_inline_code(docs[name]))
    missing = expected - actual
    assert not missing, (
        f"{name}: inline code lost or reworded vs {SOURCE}: {dict(list(missing.items())[:8])}"
    )


# --- facts that must not drift -------------------------------------------


def test_version_mentioned_in_the_docs_matches_the_version_file(docs):
    """Every version-looking number in a doc must be the current release.

    Checked as a set rather than a membership test: the release appears three
    times per file, so asserting only that it occurs *somewhere* let a stale
    number introduced in one other place through.

    Scoped to the release marker (``v7.5.9`` / ``7.5.9 代理流水线``) rather
    than every dotted number, which would also match the ``0.1.0`` packaging
    version the Provenance section deliberately contrasts it with, and the
    ``127.0.0.1`` Ollama host.
    """
    version = (REPO / VERSION_FILE_NAME).read_text(encoding="utf-8").strip()
    release = version.split("-")[0]
    for name in ALL_FILES:
        body = docs[name]
        marked = set(re.findall(r"\bv(\d+\.\d+\.\d+)\b", body))
        assert marked == {release}, (
            f"{name}: release markers {sorted(marked)}, "
            f"but {VERSION_FILE_NAME} says {release}"
        )
        # The full version string, including its suffix, is quoted verbatim in
        # the `ruder-ai where` sample output.
        if name == SOURCE or "실행 위치" in body or "执行位置" in body \
                or "実行位置" in body:
            assert version in body, f"{name}: does not quote {version!r}"


def test_documented_model_names_exist_as_modelfiles(docs):
    """`ruder-ai-zh` in the docs but no such Modelfile is a broken promise."""
    modelfiles = {p.name for p in (REPO / "Modelfiles").iterdir()}
    for name in ALL_FILES:
        for model in set(re.findall(r"ruder-ai-[a-z]{2}", docs[name])):
            assert model in modelfiles, (
                f"{name} documents {model}, which has no Modelfiles/{model}"
            )


def test_cli_commands_documented_actually_exist(docs):
    """Every `ruder-ai <verb>` in the docs must be a real command."""
    source = _read("ruder_ai/main.py")
    for name in ALL_FILES:
        for verb in set(re.findall(r"ruder-ai ([a-z][a-z-]+)", docs[name])):
            assert f"def {verb}(" in source, (
                f"{name} documents 'ruder-ai {verb}', "
                f"which is not a command in ruder_ai/main.py"
            )
