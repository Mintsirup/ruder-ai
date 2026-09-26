"""Headless tests for the Studio editor features.

The tokenizer and the search engine are pure modules, so the bulk of the
highlighting and find/replace contract is covered without a display. One live
tkinter test then checks the widget wiring.
"""
import ast
import os
import sys
from pathlib import Path

import pytest

from ruder_ai.tui import find_replace as fr
from ruder_ai.tui import syntax


# --- language detection -------------------------------------------------

@pytest.mark.parametrize(
    "name,expected",
    [
        ("a.py", "python"),
        ("a.pyi", "python"),
        ("a.tsx", "clike"),
        ("a.cs", "clike"),
        ("a.go", "clike"),
        ("a.rs", "clike"),
        ("a.json", "json"),
        ("a.yaml", "yaml"),
        ("a.yml", "yaml"),
        ("a.md", "markdown"),
        ("a.sh", "shell"),
        ("Dockerfile", "shell"),
        ("a.unknownext", None),
    ],
)
def test_detect_language(name, expected):
    assert syntax.detect_language(name) == expected


# --- tokenizer: the cases that break naive highlighters ------------------

def kinds(src, lang):
    return [(t.kind, src[t.start:t.end]) for t in syntax.tokenize(src, lang)]


def test_python_keyword_inside_string_stays_string():
    got = kinds('x = "class return def"\n', "python")
    assert ("keyword", "class") not in got
    assert ("string", '"class return def"') in got


def test_quote_inside_comment_does_not_terminate_it():
    got = kinds('# it"s fine\ny = 1\n', "python")
    assert ("comment", '# it"s fine') in got
    assert ("string", '"s fine\ny = 1\n') not in got


def test_triple_quoted_string_swallows_keywords():
    got = kinds('"""\ndef not_a_keyword\n"""\nreal = 1\n', "python")
    assert ("string", '"""\ndef not_a_keyword\n"""') in got
    assert ("keyword", "not_a_keyword") not in got


def test_python_prefixes_are_part_of_the_string_token():
    texts = [t for k, t in kinds('a = f"x{y}"\nb = rb"z"\n', "python") if k == "string"]
    assert 'f"x{y}"' in texts
    assert "rb" in "".join(texts)


def test_csharp_verbatim_and_js_template_strings():
    got = kinds('var a = `t${x}`;\nstring b = @"C:\\p";\n', "clike")
    texts = [t for k, t in got if k == "string"]
    assert "`t${x}`" in texts
    assert any(t.startswith('@"') for t in texts), texts


def test_c_block_comment_absorbs_quote_characters():
    got = kinds("/* it's \"tricky\" */ int x = 1;\n", "clike")
    assert ("comment", "/* it's \"tricky\" */") in got
    assert ("keyword", "int") in got


def test_json_distinguishes_keys_from_string_values():
    got = kinds('{"a": 1, "b": "x"}\n', "json")
    assert ("key", '"a"') in got
    assert ("key", '"b"') in got
    assert ("string", '"x"') in got
    assert ("number", "1") in got


def test_python_keywords_and_builtins_are_distinguished():
    got = kinds("import os\nprint(1)\n", "python")
    assert ("keyword", "import") in got
    assert ("builtin", "print") in got
    assert ("keyword", "print") not in got


def test_decorators_and_numbers():
    got = kinds("@deco\nx = 0xFF\n", "python")
    assert ("decorator", "@deco") in got
    assert ("number", "0xFF") in got


def test_markdown_headings_and_code_spans():
    got = kinds("# Title\n\n`code` here\n", "markdown")
    assert ("heading", "# Title") in got
    assert any(k == "string" and t == "`code`" for k, t in got)


def test_unknown_language_yields_no_tokens():
    assert syntax.tokenize("anything at all", None) == []
    assert syntax.tokenize("anything at all", "not-a-language") == []


def test_empty_input_yields_no_tokens():
    assert syntax.tokenize("", "python") == []


def test_tokens_are_ordered_and_within_bounds():
    src = "def f(x):\n    return [i for i in range(10)]\n"
    tokens = syntax.tokenize(src, "python")
    assert tokens == sorted(tokens, key=lambda t: t.start)
    for token in tokens:
        assert 0 <= token.start < token.end <= len(src)


# --- find / replace engine ---------------------------------------------

TEXT = "foo bar Foo bar foobar"


def test_case_sensitivity():
    assert len(fr.find_all(TEXT, "foo")) == 2
    assert len(fr.find_all(TEXT, "foo", case_sensitive=False)) == 3


def test_whole_word_excludes_substrings():
    # Case-sensitive, so "Foo" is not a hit for "foo"; "foobar" is excluded
    # because the match is not bounded by a word edge.
    hits = fr.find_all(TEXT, "foo", whole_word=True)
    assert [TEXT[m.start:m.end] for m in hits] == ["foo"]

    insensitive = fr.find_all(TEXT, "foo", whole_word=True, case_sensitive=False)
    assert [TEXT[m.start:m.end] for m in insensitive] == ["foo", "Foo"]


def test_whole_word_with_regex():
    # The pattern itself consumes the digit, so "foo2" ends on a word edge and
    # legitimately matches; "foobar" does not.
    hits = fr.find_all("a1 foo foo2 foobar", r"foo\d?", whole_word=True, use_regex=True)
    assert [("a1 foo foo2 foobar")[m.start:m.end] for m in hits] == ["foo", "foo2"]


def test_empty_needle_and_bad_regex_return_none():
    assert fr.compile_pattern("") is None
    assert fr.compile_pattern("[unclosed", use_regex=True) is None
    assert fr.find_all(TEXT, "[unclosed", use_regex=True) == []


def test_zero_length_matches_are_skipped():
    # "a*" can match the empty string; returning those would make the caller's
    # navigation loop forever and cannot be replaced.
    assert fr.find_all("abc", "x*", use_regex=True) == []


def test_replace_all_counts_replacements():
    assert fr.replace_all("aaa", "a", "b") == ("bbb", 3)
    assert fr.replace_all("foo foobar", "foo", "X", whole_word=True) == ("X foobar", 1)


def test_literal_replacement_escapes_backslashes():
    # With regex off, "path\to\file" must not have \t turned into a tab.
    text = "path" + chr(92) + "t" + "o"
    out, _ = fr.replace_all(text, chr(92) + "t", "TAB")
    assert out == "pathTABo"


def test_regex_backreferences_work():
    assert fr.replace_all("a1 b2", r"(\w)(\d)", r"\2\1", use_regex=True) == ("1a 2b", 2)


def test_find_next_forward_backward_and_wrapping():
    first = fr.find_next(TEXT, "foo", 0, case_sensitive=False)
    assert (first.start, first.end) == (0, 3)

    wrapped = fr.find_next(TEXT, "foo", len(TEXT), case_sensitive=False)
    assert (wrapped.start, wrapped.end) == (0, 3)

    no_wrap = fr.find_next(TEXT, "foo", len(TEXT), case_sensitive=False, wrap=False)
    assert no_wrap is None

    back = fr.find_next(TEXT, "foo", len(TEXT), case_sensitive=False, backward=True)
    assert (back.start, back.end) == (16, 19)


def test_find_next_returns_none_without_matches():
    assert fr.find_next(TEXT, "absent", 0) is None


def test_count_matches():
    assert fr.count_matches(TEXT, "foo") == 2


# --- whole-word without \b ---------------------------------------------

def test_word_boundary_predicate():
    assert fr.has_word_boundaries("foo bar", 0, 3) is True
    assert fr.has_word_boundaries("foobar", 0, 3) is False
    assert fr.has_word_boundaries("foo!", 0, 3) is True
    assert fr.has_word_boundaries("_foo", 1, 4) is False


def test_is_word_char_is_unicode_aware():
    # The point of not using \b: Hangul/Kana/Han must count as word characters.
    for char in ("a", "Z", "9", "_", "가", "あ", "漢"):
        assert fr.is_word_char(char) is True, char
    for char in (" ", ".", "/", "!", "-"):
        assert fr.is_word_char(char) is False, char


def test_punctuation_needle_works_with_whole_word():
    # \b can never be satisfied by "//" sitting between two non-word chars,
    # so the old implementation found nothing here.
    hits = fr.find_all("// divider", "//", whole_word=True)
    assert [("// divider")[h.start:h.end] for h in hits] == ["//"]


def test_punctuation_regex_with_whole_word():
    hits = fr.find_all("a ** bold ** b", r"\*\*", whole_word=True, use_regex=True)
    assert len(hits) == 2


def test_regex_ending_in_punctuation_with_whole_word():
    text = "안녕 나. 하세요"
    hits = fr.find_all(text, r"나\.", whole_word=True, use_regex=True)
    assert [text[h.start:h.end] for h in hits] == ["나."]


def test_korean_whole_word():
    # A space-bounded Hangul word matches; a fragment of a longer word does not.
    assert len(fr.find_all("안녕 가나 하세요", "가나", whole_word=True)) == 1
    assert fr.find_all("가나다라", "가나", whole_word=True) == []


# --- multiline regex ----------------------------------------------------

def test_regex_anchors_are_multiline_by_default():
    buffer = "import os\nimport sys\nexport x\n"
    assert [m.start for m in fr.find_all(buffer, "^import", use_regex=True)] == [0, 10]
    assert len(fr.find_all(buffer, r"^import", use_regex=True, multiline=False)) == 1


def test_regex_dollar_anchor_is_multiline():
    buffer = "a = 1;\nb = 2;\n"
    assert len(fr.find_all(buffer, r";$", use_regex=True)) == 2


# --- fuzzy --------------------------------------------------------------

def test_fuzzy_only_runs_when_exact_finds_nothing():
    text = "def greet(name):"
    assert fr.find_all(text, "gat", fuzzy=True) == []
    # An exact hit is returned as-is, never widened by fuzzy.
    assert len(fr.find_all("gat greet", "gat", fuzzy=True)) == 1


def test_fuzzy_finds_transpositions_and_typos():
    cases = [
        ("lenght", "the length here", "length"),
        ("retrun", "return 1", "return"),
        ("vaule", "value = 1", "value"),
    ]
    for needle, text, expected in cases:
        hits = fr.find_all(text, needle, fuzzy=True)
        assert [text[h.start:h.end] for h in hits] == [expected], needle


def test_fuzzy_respects_the_distance_budget():
    # gat -> greet is 3 edits, so a budget of 2 must not match it.
    assert fr.find_all("def greet(name):", "gat", fuzzy=True, max_fuzzy_distance=2) == []
    assert fr.find_all("def greet(name):", "gat", fuzzy=True, max_fuzzy_distance=3) != []


def test_fuzzy_is_case_insensitive_by_default_option():
    hits = fr.find_all("Return value", "retrun", fuzzy=True, case_sensitive=False)
    assert [("Return value")[h.start:h.end] for h in hits] == ["Return"]


def test_fuzzy_never_applies_to_regex_mode():
    # Fuzzy-matching a user regex would be meaningless, so a regex that
    # matches nothing exactly must stay empty even with fuzzy enabled.
    assert fr.find_all("value = 1", r"v.lXXX", use_regex=True, fuzzy=True) == []
    # ...while a regex that does match is still found normally.
    assert len(fr.find_all("value = 1", r"v.lue", use_regex=True, fuzzy=True)) == 1


def test_edit_distance():
    assert fr._edit_distance("abc", "abc", 5) == 0
    assert fr._edit_distance("abc", "abd", 5) == 1
    assert fr._edit_distance("abc", "", 5) == 3
    assert fr._edit_distance("gat", "greet", 5) == 3
    assert fr._edit_distance("kitten", "sitting", 5) == 3
    # Over budget reports limit+1 rather than the true distance.
    assert fr._edit_distance("kitten", "sitting", 1) == 2


# --- stepping and caching ----------------------------------------------

def test_find_next_in_is_o1_stepping():
    matches = fr.find_all("aXaXaX", "a")
    assert [m.start for m in matches] == [0, 2, 4]
    assert fr.find_next_in(matches, 0).start == 0
    assert fr.find_next_in(matches, 1).start == 2
    assert fr.find_next_in(matches, 99).start == 0          # wraps
    assert fr.find_next_in(matches, 99, wrap=False) is None
    assert fr.find_next_in(matches, 4, backward=True).start == 2
    assert fr.find_next_in([], 0) is None


def test_pattern_cache_reuses_compiled_objects():
    fr.clear_pattern_cache()
    first = fr.compile_pattern("cache_me", use_regex=True)
    second = fr.compile_pattern("cache_me", use_regex=True)
    assert first is not None and first is second


def test_pattern_cache_is_bounded():
    fr.clear_pattern_cache()
    for i in range(400):
        fr.compile_pattern(f"needle_{i}")
    assert len(fr._PATTERN_CACHE) <= fr._PATTERN_CACHE_MAX


def test_options_from_ignores_unknown_keys():
    opts = fr.options_from(case_sensitive=False, nonsense=True)
    assert opts.case_sensitive is False
    assert opts.fuzzy is False


# --- position reporting -------------------------------------------------

def test_line_column_and_describe():
    text = "abc\ndefg\nhi"
    assert fr.line_column(text, 0) == (1, 1)
    assert fr.line_column(text, 4) == (2, 1)
    assert fr.line_column(text, 8) == (2, 5)
    assert fr.line_column(text, 10) == (3, 2)
    assert fr.describe(text, 8) == "Ln 2, Col 5"


def test_is_valid_pattern():
    assert fr.is_valid_pattern("abc") is True
    assert fr.is_valid_pattern("") is False
    assert fr.is_valid_pattern("[unclosed", use_regex=True) is False
    assert fr.is_valid_pattern(r"\d+", use_regex=True) is True


def test_replace_one():
    text = "foo bar"
    match = fr.find_all(text, "foo")[0]
    assert fr.replace_one(text, match, "baz") == "baz bar"


def test_replace_all_honours_whole_word_without_b():
    # The replacement path must agree with the search path on boundaries.
    text = "// a // b"
    assert fr.replace_all(text, "//", "X", whole_word=True) == ("X a X b", 2)


# --- no GUI-toolkit dependency in the pure modules ----------------------

@pytest.mark.parametrize("module_name", ["syntax", "find_replace"])
def test_pure_modules_do_not_import_tkinter(module_name):
    module = __import__(f"ruder_ai.tui.{module_name}", fromlist=["x"])
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.split(".")[0])
    assert "tkinter" not in imported
    assert "PyQt6" not in imported


def test_index_translation():
    from ruder_ai.tui.app_gui import _index_from_offset, _line_offsets

    text = "abc\ndefg\nhi"
    starts = _line_offsets(text)
    assert starts == [0, 4, 9]
    assert _index_from_offset(starts, 0) == "1.0"
    assert _index_from_offset(starts, 2) == "1.2"
    assert _index_from_offset(starts, 4) == "2.0"
    assert _index_from_offset(starts, 8) == "2.4"
    assert _index_from_offset(starts, 9) == "3.0"
    assert _index_from_offset(starts, 10) == "3.1"

# --- live widget wiring (subprocess) -------------------------------------

def test_studio_highlights_and_replaces(tmp_path):
    """Drive the real widgets: tags, find, replace, close.

    Runs out of process because pytest's stdout capture breaks the second
    ``tkinter.Tk()`` in a process on Windows.
    """
    import subprocess

    script = Path(__file__).with_name("_studio_gui_check.py")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, str(script), "editor", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=180,
    )
    if result.returncode != 0:
        if "no display" in (result.stderr or "") + (result.stdout or ""):
            pytest.skip("no display available for tkinter")
        pytest.fail(
            "editor GUI check failed:\n" + result.stdout + "\n" + result.stderr
        )

    for marker in (
        "highlight  : OK",
        "find       : OK",
        "replace    : OK",
        "replace all: OK",
        "bad regex  : OK",
        "punct word : OK",
        "fuzzy      : OK",
        "close      : OK",
        "re-highlite: OK",
    ):
        assert marker in result.stdout, result.stdout
