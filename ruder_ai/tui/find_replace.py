"""Pure find/replace matching for the Studio editor.

No tkinter here: the dialog is a thin shell over this module, which keeps the
awkward parts testable without a display.

What this gets right that a naive ``re`` wrapper does not
--------------------------------------------------------
* **Whole-word matching that is not ``\\b``.** ``\\b`` can never be satisfied by a
  needle that ends in punctuation, or that is pure punctuation, when it sits
  between two non-word characters: searching for ``//`` in ``// comment`` with
  "whole word" enabled returns nothing, and the same for ``**``, ``;`` or a
  regex like ``나\\.``. Boundaries are decided here with an explicit
  Unicode-aware character test instead, so CJK behaves predictably too.
* **Regex search is multiline by default.** In an editor buffer ``^import``
  means "every line starting with import", which requires ``re.MULTILINE``.
* **A typo-tolerant fallback.** When a literal search finds nothing, word
  tokens within a small edit distance are offered, so ``gat`` finds ``greet``.
* **Stepping is O(1).** ``find_next_in`` walks a precomputed match list, so
  holding F3 down does not rescan the buffer for every repeat.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchOptions:
    """Everything that changes how a needle is matched."""

    case_sensitive: bool = True
    whole_word: bool = False
    use_regex: bool = False
    #: ``^``/``$`` anchor to line boundaries. Correct for a multi-line buffer.
    multiline: bool = True
    #: Fall back to a typo-tolerant search when the exact one finds nothing.
    fuzzy: bool = False
    #: How many characters may differ in a fuzzy hit.
    max_fuzzy_distance: int = 2
    #: Stop after this many matches. ``None`` means "all".
    limit: int | None = None

    def regex_flags(self) -> int:
        flags = 0
        if not self.case_sensitive:
            flags |= re.IGNORECASE
        if self.multiline:
            flags |= re.MULTILINE
        return flags


DEFAULT_OPTIONS = SearchOptions()


def options_from(**kwargs) -> SearchOptions:
    """Build options from loose keyword arguments, ignoring unknown keys.

    Lets callers pass a UI state dict straight through without a KeyError on
    every new checkbox.
    """
    known = {f: getattr(DEFAULT_OPTIONS, f) for f in DEFAULT_OPTIONS.__dataclass_fields__}
    known.update({k: v for k, v in kwargs.items() if k in known})
    return SearchOptions(**known)


# ---------------------------------------------------------------------------
# Word boundaries
# ---------------------------------------------------------------------------


def is_word_char(char: str) -> bool:
    """True for characters that should block a whole-word match.

    ``str.isalnum`` is Unicode-aware, so Hangul, Kana and Han all count as
    word characters — which is exactly what ``\\b`` fails to express.
    """
    return char.isalnum() or char == "_"


def has_word_boundaries(text: str, start: int, end: int) -> bool:
    """True when [start, end) is not glued to surrounding word characters."""
    if start > 0 and is_word_char(text[start - 1]):
        return False
    if end < len(text) and is_word_char(text[end]):
        return False
    return True


# ---------------------------------------------------------------------------
# Pattern compilation with a bounded cache
# ---------------------------------------------------------------------------

_PATTERN_CACHE: "OrderedDict[tuple, re.Pattern | None]" = OrderedDict()
_PATTERN_CACHE_MAX = 128


@dataclass(frozen=True)
class Match:
    start: int
    end: int

    @property
    def text_span(self) -> tuple[int, int]:
        return self.start, self.end

    def __len__(self) -> int:  # pragma: no cover - convenience
        return self.end - self.start


def compile_pattern(
    needle: str,
    *,
    case_sensitive: bool = True,
    whole_word: bool = False,
    use_regex: bool = False,
    multiline: bool = True,
) -> re.Pattern | None:
    """Build the search regex, or None when the options cannot be honoured.

    ``whole_word`` is accepted for call-site compatibility but deliberately
    *not* baked into the regex: ``\\b`` is wrong for CJK and for patterns that
    end in punctuation. The check is applied by :func:`find_all` instead.
    """
    if not needle:
        return None

    key = (needle, bool(case_sensitive), bool(use_regex), bool(multiline))
    if key in _PATTERN_CACHE:
        _PATTERN_CACHE.move_to_end(key)
        return _PATTERN_CACHE[key]

    body = needle if use_regex else re.escape(needle)
    flags = 0
    if not case_sensitive:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.MULTILINE

    try:
        compiled: re.Pattern | None = re.compile(body, flags)
    except re.error:
        compiled = None

    _PATTERN_CACHE[key] = compiled
    if len(_PATTERN_CACHE) > _PATTERN_CACHE_MAX:
        _PATTERN_CACHE.popitem(last=False)
    return compiled


def clear_pattern_cache() -> None:
    _PATTERN_CACHE.clear()


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


def _literal_matches(text: str, needle: str, opts: SearchOptions) -> list[Match]:
    pattern = compile_pattern(
        needle,
        case_sensitive=opts.case_sensitive,
        use_regex=opts.use_regex,
        multiline=opts.multiline,
    )
    if pattern is None:
        return []

    out: list[Match] = []
    for m in pattern.finditer(text):
        if m.start() == m.end():
            # A zero-length match (e.g. "a*") cannot be selected or replaced
            # and would make the caller's navigation loop spin forever.
            continue
        if opts.whole_word and not has_word_boundaries(text, m.start(), m.end()):
            continue
        out.append(Match(m.start(), m.end()))
        if opts.limit is not None and len(out) >= opts.limit:
            break
    return out


_WORD_TOKEN = re.compile(r"\w+", re.UNICODE)

#: Upper bound on tokens examined by a fuzzy scan, so a generated or minified
#: file cannot turn a keystroke into a multi-second stall.
_FUZZY_MAX_TOKENS = 200_000


def _edit_distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, or ``limit + 1`` once the budget is blown.

    Bails out of a row as soon as the cheapest cell exceeds the budget, which
    keeps the scan linear-ish in practice instead of O(len(a) * len(b)) for
    every token in the buffer.
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        row_min = i
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            current.append(value)
            if value < row_min:
                row_min = value
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1] if previous[-1] <= limit else limit + 1


def _fuzzy_matches(text: str, needle: str, opts: SearchOptions) -> list[Match]:
    """Word tokens within ``max_fuzzy_distance`` edits of the needle.

    Only ever used for literal search: fuzzy-matching a user regex would be
    meaningless, so regex mode never reaches here.
    """
    if opts.use_regex or not needle:
        return []
    target = needle if opts.case_sensitive else needle.lower()
    budget = max(0, opts.max_fuzzy_distance)

    found: list[tuple[int, int, int]] = []  # (distance, start, end)
    for index, m in enumerate(_WORD_TOKEN.finditer(text)):
        if index >= _FUZZY_MAX_TOKENS:
            break
        token = m.group(0)
        probe = token if opts.case_sensitive else token.lower()
        if abs(len(probe) - len(target)) > budget:
            continue
        distance = _edit_distance(probe, target, budget)
        if distance <= budget:
            found.append((distance, m.start(), m.end()))

    # Nearest first, then earliest: matches what a reader expects highlighted.
    found.sort(key=lambda item: (item[0], item[1]))
    out = [Match(start, end) for _, start, end in found]
    if opts.limit is not None:
        out = out[: opts.limit]
    return out


def find_all(text: str, needle: str, options: SearchOptions | None = None, **kwargs) -> list[Match]:
    """Every non-overlapping match, left to right.

    When the exact search finds nothing and ``fuzzy`` is on, typo-tolerant
    word matches are returned instead.
    """
    opts = options or options_from(**kwargs)
    if not needle:
        return []

    matches = _literal_matches(text, needle, opts)
    if matches or not opts.fuzzy:
        return matches
    return _fuzzy_matches(text, needle, opts)


def find_next_in(
    matches: Sequence[Match],
    from_pos: int,
    *,
    wrap: bool = True,
    backward: bool = False,
) -> Match | None:
    """Step through a precomputed match list in O(1)."""
    if not matches:
        return None
    if backward:
        for match in reversed(matches):
            if match.start < from_pos:
                return match
    else:
        for match in matches:
            if match.start >= from_pos:
                return match
    return matches[0] if wrap else None


def find_next(
    text: str,
    needle: str,
    from_pos: int,
    *,
    wrap: bool = True,
    backward: bool = False,
    **kwargs,
) -> Match | None:
    """Convenience wrapper: find matches then step. Prefer
    :func:`find_all` + :func:`find_next_in` when stepping repeatedly."""
    return find_next_in(
        find_all(text, needle, **kwargs), from_pos, wrap=wrap, backward=backward
    )


def count_matches(text: str, needle: str, **kwargs) -> int:
    return len(find_all(text, needle, **kwargs))


def is_valid_pattern(needle: str, *, use_regex: bool = False, **flags) -> bool:
    """True when *needle* is usable as a pattern (for live UI validation)."""
    if not needle:
        return False
    if not use_regex:
        return True
    return compile_pattern(needle, use_regex=True, **flags) is not None


# ---------------------------------------------------------------------------
# Replacement
# ---------------------------------------------------------------------------


def expand_replacement(replacement: str, use_regex: bool) -> str:
    r"""Prepare a replacement for :func:`re.sub`.

    With regular expressions off the user's text is literal, so backslashes are
    doubled — otherwise ``path\to\file`` silently becomes ``pathtofile`` via
    ``\t``, and a Windows path is exactly the case that gets typed here.
    """
    if use_regex:
        return replacement
    return replacement.replace("\\", "\\\\")


def replace_all(
    text: str,
    needle: str,
    replacement: str,
    *,
    case_sensitive: bool = True,
    whole_word: bool = False,
    use_regex: bool = False,
    **rest,
) -> tuple[str, int]:
    """Replace every match. Returns ``(new_text, replacements_made)``.

    Whole-word filtering is done by hand rather than with ``\\b`` for the same
    reason :func:`find_all` does it, so the two can never disagree.
    """
    opts = options_from(
        case_sensitive=case_sensitive,
        whole_word=whole_word,
        use_regex=use_regex,
        **rest,
    )
    if not needle:
        return text, 0

    pattern = compile_pattern(
        needle,
        case_sensitive=opts.case_sensitive,
        use_regex=opts.use_regex,
        multiline=opts.multiline,
    )
    if pattern is None:
        return text, 0

    body = expand_replacement(replacement, opts.use_regex)
    if not opts.whole_word:
        new_text, count = pattern.subn(body, text)
        return new_text, count

    pieces: list[str] = []
    cursor = 0
    count = 0
    for m in pattern.finditer(text):
        if m.start() == m.end():
            continue
        if not has_word_boundaries(text, m.start(), m.end()):
            continue
        pieces.append(text[cursor:m.start()])
        pieces.append(m.expand(body))
        cursor = m.end()
        count += 1
    if not count:
        return text, 0
    pieces.append(text[cursor:])
    return "".join(pieces), count


def replace_one(text: str, match: Match, replacement: str, *, use_regex: bool = False) -> str:
    """Replace a single already-located match (used by "Replace" once)."""
    return text[: match.start] + expand_replacement(replacement, use_regex) + text[match.end :]


# ---------------------------------------------------------------------------
# Position reporting
# ---------------------------------------------------------------------------


def line_column(text: str, offset: int) -> tuple[int, int]:
    """1-based ``(line, column)`` for a character offset."""
    if offset <= 0:
        return 1, 1
    offset = min(offset, len(text))
    line = text.count("\n", 0, offset) + 1
    last_break = text.rfind("\n", 0, offset)
    column = offset - last_break
    return line, column


def describe(text: str, offset: int) -> str:
    """Human-readable ``Ln x, Col y`` for a status bar."""
    line, column = line_column(text, offset)
    return f"Ln {line}, Col {column}"
