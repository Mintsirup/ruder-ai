"""Token Budget 추정/압축 유틸리티.

TODO.md "Context Builder" 항목 중 Token Budget / Context Compression 구현.

실제 토크나이저(tiktoken 등)를 붙이는 대신, 로컬 LLM(Ollama) 환경에서도
추가 의존성 없이 동작해야 하므로 문자 수 기반 근사치를 사용한다.
영어/코드는 대략 4자당 1토큰이라는 경험칙을 쓰고, 한글은 UTF-8/BPE
토크나이저 기준으로 한 글자가 토큰 하나에 더 가깝게 소비되는 경향이
있어 한글 비중이 높은 텍스트는 더 보수적으로 잡는다. 실제 사용 중인
LLM의 정확한 토큰 수와는 다를 수 있지만, "budget을 넘겼는지"를
판단하는 용도로는 충분한 근사치다.
"""

from __future__ import annotations

import re

_ASCII_CHARS_PER_TOKEN = 4.0
_HANGUL_CHARS_PER_TOKEN = 1.5

# Exactly the U+AC00..U+D7A3 range the old per-character loop tested, but
# scanned in C instead of one Python-level comparison per character. This
# function was ~82% of ContextBuilder.build, and ``truncate_to_budget``'s
# binary search calls it O(log n) times per snippet.
_HANGUL_RE = re.compile("[가-힣]")


def estimate_tokens(text: str) -> int:
    """텍스트의 대략적인 토큰 수를 추정한다."""

    if not text:
        return 0

    length = len(text)

    # Source code and English prompts contain no Hangul at all, so the common
    # case needs no scan.
    if text.isascii():
        return int(length / _ASCII_CHARS_PER_TOKEN) + 1

    hangul = len(_HANGUL_RE.findall(text))
    other = length - hangul

    return (
        int(
            hangul / _HANGUL_CHARS_PER_TOKEN
            + other / _ASCII_CHARS_PER_TOKEN
        )
        + 1
    )


def truncate_to_budget(
    text: str, max_tokens: int, tail_bias: float = 0.5,
) -> str:
    """텍스트를 대략적인 token budget 안으로 줄인다.

    문자 폭이 다른 언어(특히 한국어/코드)를 한 종류의 chars/token 비율로
    환산하지 않고, 후보 문자열을 실제 추정 토큰 수로 재검사하면서 길이를
    줄인다. 정확한 모델 tokenizer는 사용하지 않으므로 최종 보장은 근사치다.

    범위 이익은 "... (생략: 예산 초과로 압축됨) ..." 표시가 만드는 하한이다.
    max_tokens 이 그보다 작으면 아무리 줄여도 예산을 맞출 수 없으므로, 이
    경우 결과가 예산을 초과하는 것은 이 함수의 계약이 아니라 근사치가
    불가능하다는 뜻이다.
    """
    if max_tokens <= 0 or not text:
        return ""

    if estimate_tokens(text) <= max_tokens:
        return text

    tail_bias = min(max(tail_bias, 0.0), 1.0)
    target_chars = max(64, int(max_tokens * _ASCII_CHARS_PER_TOKEN))

    def make_candidate(chars: int) -> str:
        tail_len = int(chars * tail_bias)
        head_len = max(0, chars - tail_len)
        head = text[:head_len] if head_len else ""
        tail = text[-tail_len:] if tail_len else ""
        return f"{head}\n... (생략: 예산 초과로 압축됨) ...\n{tail}"

    candidate = make_candidate(min(target_chars, len(text)))
    # 추정치가 여전히 예산을 넘으면 이분 탐색으로 더 줄인다.
    # lo=1 (이전엔 32): 생략 표시는 한국어로 약 20토큰이라 예산이 그보다
    # 작으면 어떤 길이로 줄여도 예산을 만족시킬 수 없다. 그래도 더 줄여봐야
    # 비용이 없고, 호출자가 예산을 안 지킨 걸 숨기지 않는 편이 낫다.
    lo, hi = 1, min(target_chars, len(text))
    best = candidate
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = make_candidate(mid)
        if estimate_tokens(candidate) <= max_tokens:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1

    return best
