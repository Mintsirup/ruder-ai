"""What kind of turn is this?

A greeting is not a task. "안녕" once reached the planner, which invented a
``write_file`` for a ``hello_handler`` module - the agent modified a repository
because someone said hello. The planner already computes an ``intent`` for
retrieval purposes, but nothing consulted it before a mutation, so the model
was free to plan whatever it liked for a turn that clearly asked for nothing.

This module is the single classifier both the agent and the executor consult,
so the decision cannot drift between "what we think the user asked for" and
"what we are actually allowed to do".

The classification is deliberately biased toward *refusing to mutate*. A turn
that is misread as conversational costs the user one retry with clearer
wording; a turn that is misread as actionable silently edits their code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class TurnKind(str, Enum):
    #: "안녕", "thanks" - nothing to do but answer.
    CONVERSATIONAL = "conversational"
    #: "이거 왜 이렇게 짜여 있어?" - answerable, but never a licence to edit.
    QUESTION = "question"
    #: Everything else. The only kind that may touch a file.
    ACTION = "action"


@dataclass(frozen=True, slots=True)
class TurnIntent:
    kind: TurnKind
    #: Human-readable justification, surfaced in the refusal message.
    reason: str
    #: The signal that decided it, for telemetry and tests.
    signal: str

    @property
    def may_mutate(self) -> bool:
        return self.kind is TurnKind.ACTION


# --------------------------------------------------------------------------
# signals
# --------------------------------------------------------------------------

#: Social openers and closers. Matched as substrings of the lowercased prompt,
#: which is safe for Korean because the particles never split these words.
_GREETINGS = (
    "안녕",
    "안녕하세요",
    "반갑",
    "하이",
    "헬로",
    "hello",
    "hey",
    "good morning",
    "good evening",
    "감사합니다",
    "고맙",
    "고마워",
    "thanks",
    "thank you",
    "thx",
    "ㅎㅎ",
    "ㅋㅋ",
    "재밌",
    "재미있",
    "잘 먹",
    "수고했",
)

#: Imperative / request markers. The list is intentionally generous: every
#: entry here is a way for a user to unlock mutations, so a false positive
#: costs nothing but a slightly larger list.
_ACTIONS = (
    # create
    "만들", "생성", "추가", "작성", "생성해", "구현", "작성해", "init", "scaffold",
    "create", "add ", "generate", "write ", "implement", "build me",
    # modify
    "수정", "변경", "고쳐", "고치", "패치", "리팩터", "리팩토", "개선", "최적화",
    "정리해", "정리", "바꿔", "교체", "추출해", "넣어", "넣어줘",
    "fix", "refactor", "modify", "change", "update", "improve", "optimi",
    "optimiz", "edit ", "patch ", "tweak", "rename", "move ", "replace",
    # delete
    "삭제", "지워", "제거", "삭제해", "정리해줘",
    "delete", "remove", "drop ", "clean up", "cleanup",
    # run / verify
    "실행", "돌려", "빌드", "테스트", "검증", "확인해", "검사",
    "run", "build", "test", "verify", "check ", "execute", "compile", "install",
    # analysis that may imply acting on the result
    "분석해", "분석하고", "조사해", "찾아서", "찾아", "고쳐서", "해결",
    "analyze", "analyse", "investigate", "find ", "search", "review", "explain",
    "summarize", "summarise", "document", "refactor",
)

#: Markers that make a turn a question rather than an action.
_QUESTION_MARKS = (
    "?", "？",
    "인가요", "인가?", "일까", "나요", "랍니까",
    "어떻게", "왜", "무엇", "뭐", "얼마", "어떻",
    "무엇인지", "어떤", "있나요", "있어?",
    "what", "why", "how", "which", "where", "who", "when", "is it", "are there",
    "can you explain", "tell me about", "difference between",
)

#: Anything that looks like a concrete target: a path, an extension, a
#: qualified name, or a fenced code block. A turn that names one of these is
#: doing work even without a verb ("main.py", "config.py 좀 봐줘").
_TARGET_RE = re.compile(
    r"""
    (?:\.[A-Za-z0-9]{1,6}\b)          # a file extension
    | (?:\b\w+\s*/\s*\w+)             # a path segment
    | (?:\b\w+\.\w+\b)                # dotted / qualified name
    | (?:```|`{1,2}\w)                # inline or fenced code
    | (?:\b[A-Za-z_][A-Za-z0-9_]*\s*\()  # a call
    | (?:\bclass\s+\w+|\bdef\s+\w+|\bfunction\s+\w+|\bimport\s+\w+)
    """,
    re.VERBOSE,
)

#: Bounded length above which a turn is assumed to be substantive work.
_SUBSTANTIVE_CHARS = 160


def _normalize(prompt: str) -> str:
    return re.sub(r"\s+", " ", str(prompt or "")).strip().lower()


def _has_action(text: str) -> str | None:
    for word in _ACTIONS:
        if word in text:
            return word.strip()
    return None


#: Latin greetings need word boundaries: "hi" must not fire inside "this",
#: "him" or "which". The Korean entries are matched as plain substrings
#: because particles never split those words.
_LATIN_GREETINGS = ("hi", "hello", "hey", "thanks", "thx", "gm", "gmt")

_LATIN_GREETING_RE = re.compile(
    r"\b(?:" + "|".join(_LATIN_GREETINGS) + r")\b"
)


def _has_greeting(text: str) -> str | None:
    match = _LATIN_GREETING_RE.search(text)
    if match is not None:
        return match.group(0)
    for word in _GREETINGS:
        if word in text:
            return word.strip()
    return None


def _is_question(text: str) -> str | None:
    for mark in _QUESTION_MARKS:
        if mark in text:
            return mark.strip()
    return None


def _names_a_target(text: str, raw: str) -> bool:
    if _TARGET_RE.search(raw):
        return True
    # A bare path-ish token with no whitespace, e.g. "executor.py".
    if raw and " " not in raw and ("." in raw or "/" in raw):
        return True
    return False


def classify_turn(prompt: str) -> TurnIntent:
    """Decide whether ``prompt`` asks for work, a question, or conversation."""
    raw = str(prompt or "").strip()
    if not raw:
        return TurnIntent(
            TurnKind.CONVERSATIONAL,
            "빈 요청입니다.",
            "empty",
        )

    text = _normalize(raw)
    if not text:
        return TurnIntent(
            TurnKind.CONVERSATIONAL,
            "빈 요청입니다.",
            "empty",
        )

    action = _has_action(text)
    if action is not None:
        return TurnIntent(
            TurnKind.ACTION,
            f"작업 지시('{action}')가 있습니다.",
            f"action:{action}",
        )

    names_target = _names_a_target(text, raw)
    greeting = _has_greeting(text)
    question = _is_question(text)

    if names_target:
        return TurnIntent(
            TurnKind.ACTION,
            "구체적인 대상(파일/경로/코드)이 지정되어 있습니다.",
            "target",
        )

    if greeting is not None and question is None:
        return TurnIntent(
            TurnKind.CONVERSATIONAL,
            f"인사/잡담('{greeting}')이며 작업 지시가 없습니다.",
            f"greeting:{greeting}",
        )

    if question is not None:
        return TurnIntent(
            TurnKind.QUESTION,
            f"질문('{question}')이며 작업 지시가 없습니다.",
            f"question:{question}",
        )

    # Long, dense text with no verb is far more likely to be a specification
    # that happens to omit an imperative than a one-line pleasantry.
    if len(raw) > _SUBSTANTIVE_CHARS:
        return TurnIntent(
            TurnKind.ACTION,
            "명시적인 요청으로 간주합니다.",
            "length",
        )

    if greeting is not None:
        return TurnIntent(
            TurnKind.CONVERSATIONAL,
            f"인사/잡담('{greeting}')입니다.",
            f"greeting:{greeting}",
        )

    return TurnIntent(
        TurnKind.ACTION,
        "작업 지시로 간주합니다.",
        "default",
    )


#: Message used whenever a mutation is refused. Names the exact way out, so a
#: refused turn reads as guidance rather than as a bug.
MUTATION_REFUSED_MESSAGE = (
    "이번 요청은 작업 지시가 없는 대화/질문으로 판단되어 파일을 변경하지 "
    "않았습니다. 실제로 수정하거나 생성하려는 경우, 요청에 동작을 분명히 적어 "
    "주세요 (예: '~을 만들어줘', '~을 고쳐줘', '~ 파일을 분석하고 README를 "
    "수정해줘')."
)
