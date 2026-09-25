"""Web fetch skill for RuderAI Agent.

web_search가 제목+스니펫만 주는 것과 달리, 이 Skill은 검색 결과의 URL을
실제로 열어 본문 텍스트를 가져온다. "검색 -> 상위 결과 본문 읽기 -> 답변"
2단계 흐름의 두 번째 단계를 담당한다 (web_search 단독으로는 스니펫이
짧고 부정확해서 최신 버전/날짜 같은 사실 질문에 답하기엔 정보가 부족한
경우가 많았다).
"""
from __future__ import annotations

import re
import html
from typing import Any, Dict

import httpx

from ruder_ai.skills.base import BaseSkill
from ruder_ai.context.token_budget import truncate_to_budget


class WebFetchSkill(BaseSkill):
    name = "web_fetch"
    description = (
        "web_search로 찾은 URL의 실제 페이지를 열어 본문 텍스트를 "
        "가져옵니다. 검색 스니펫만으로 답이 불확실할 때, 상위 결과 "
        "URL 하나를 이걸로 읽고 그 내용에 근거해 답하세요."
    )

    # <script>/<style> 내부는 본문과 무관한 코드/CSS라 통째로 제거.
    # 그 외 태그는 텍스트만 남기고 벗겨낸다.
    _SCRIPT_STYLE_RE = re.compile(
        r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL,
    )
    _TAG_RE = re.compile(r"<[^>]+>")
    _WHITESPACE_RE = re.compile(r"[ \t]+")
    _BLANK_LINES_RE = re.compile(r"\n{3,}")

    # 나무위키 등 위키 페이지에서 실제 검색 결과 응답에도 매번 그대로
    # 반복되는 것으로 확인된 안내/네비게이션 문구들. 실제 web_fetch 결과를
    # 확인해 보니 "최신 버전이 뭔지" 같은 사실 정보가 문서 뒷부분에 있는
    # 경우가 많은데, 이런 잡음이 앞부분 예산을 잡아먹어서 정작 중요한
    # 내용이 truncate 단계에서 잘려나가는 문제가 있었다. 본문 추출 단계
    # 에서 미리 제거해 budget을 실제 내용에 쓰게 한다.
    _WIKI_BOILERPLATE_RES = [
        re.compile(r"최근 수정 시각:\s*[\d\-: ]+"),
        re.compile(
            r"IP 우회 수단\(프록시 서버, VPN, Tor 등\)이나 "
            r"IDC 대역 IP로 접속하셨습니다\.[^\n]*"
        ),
        re.compile(r"\(VPN이나 iCloud의 비공개 릴레이[^\n]*\)"),
        re.compile(
            r"잘못된 IDC 대역 차단이라고 생각하시는 경우[^\n]*"
        ),
        re.compile(r"닫기\s*토론\s*역사"),
        re.compile(r"최근 변경\s*최근 토론\s*특수 기능"),
    ]

    # 문자 기반 근사치(4자당 1토큰) * DEFAULT_MAX_TOKENS 만큼만 남긴다.
    # ContextBuilder의 DEFAULT_TOKEN_BUDGET(6000)보다 작게 잡아서, 본문
    # 하나가 나머지 대화(시스템 프롬프트, 이전 Task 결과 등)를 밀어내지
    # 않도록 한다. 1500 -> 2500으로 올렸다: 실제 위키 문서(예: 버전 목록)
    # 에서 1500 토큰(약 6000자)로는 "최신 버전"이 언급되는 문서 뒷부분
    # 까지 충분히 안 남는 경우가 있었다 (Modelfile num_ctx가 16384라
    # 여유는 있음).
    DEFAULT_MAX_TOKENS = 2500

    # head/tail 분배 시 뒤쪽에 줄 비중. 시간순으로 정리된 문서(버전
    # 목록/업데이트 이력 등)는 "최신" 내용이 뒷부분에 있을 가능성이
    # 높아서, 앞쪽 목차/안내문보다 뒤쪽에 더 비중을 준다. 그래도 페이지
    # 도입부(무엇에 대한 문서인지)가 아예 없으면 맥락을 잃으니 head도
    # 일부는 남긴다.
    TAIL_BIAS = 0.7

    def _clean(self, raw_html: str) -> str:
        """HTML에서 스크립트/스타일을 제거하고 태그를 벗겨 본문 텍스트만 남긴다."""

        no_script = self._SCRIPT_STYLE_RE.sub(" ", raw_html)
        text = self._TAG_RE.sub("\n", no_script)
        text = html.unescape(text)

        for pattern in self._WIKI_BOILERPLATE_RES:
            text = pattern.sub(" ", text)

        text = self._WHITESPACE_RE.sub(" ", text)
        text = self._BLANK_LINES_RE.sub("\n\n", text)

        return "\n".join(
            line.strip() for line in text.splitlines() if line.strip()
        )

    async def execute(
        self,
        url: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        **kwargs,
    ) -> Dict[str, Any]:

        if not url or not url.strip():
            return {
                "status": "error",
                "error_type": "validation",
                "message": "url이 필요합니다.",
            }

        url = url.strip()

        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Safari/537.36"
                    ),
                },
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                body = response.text

            content = self._clean(body)

            if not content:
                return {
                    "status": "error",
                    "error_type": "not_found",
                    "message": f"'{url}' 페이지에서 텍스트 내용을 추출하지 못했습니다.",
                }

            truncated = truncate_to_budget(
                content, max_tokens, tail_bias=self.TAIL_BIAS,
            )

            return {
                "status": "success",
                "url": url,
                "content": truncated,
                "truncated": truncated != content,
            }

        except httpx.HTTPStatusError as e:
            return {
                "status": "error",
                "error_type": "not_found",
                "message": f"페이지 가져오기 실패 (HTTP {e.response.status_code}): {url}",
            }
        except Exception as e:
            return {
                "status": "error",
                "error_type": "network",
                "message": f"페이지 가져오기 실패: {str(e)}",
            }