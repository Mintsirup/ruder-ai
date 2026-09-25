"""Web search skill for RuderAI Agent."""
from __future__ import annotations

import re
import html
import urllib.parse
from typing import Any, Dict

import httpx

from ruder_ai.skills.base import BaseSkill


class WebSearchSkill(BaseSkill):
    name = "web_search"
    description = "웹에서 최신 정보를 검색하여 관련 결과 목록을 가져옵니다."

    # DuckDuckGo HTML(비-JS) 결과 페이지 파싱용 패턴.
    # result__a: 결과 링크(제목 포함), result__snippet: 요약 텍스트.
    _RESULT_LINK_RE = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    _SNIPPET_RE = re.compile(
        r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    _TAG_RE = re.compile(r"<[^>]+>")

    def _clean(self, raw: str) -> str:
        """HTML 태그 제거 + 엔티티 디코딩."""
        return html.unescape(self._TAG_RE.sub("", raw)).strip()

    def _unwrap_ddg_redirect(self, href: str) -> str:
        """DDG HTML 결과의 //duckduckgo.com/l/?uddg=... 리다이렉트 링크를 실제 URL로 변환."""
        if "uddg=" in href:
            parsed = urllib.parse.urlparse(href if href.startswith("http") else f"https:{href}")
            qs = urllib.parse.parse_qs(parsed.query)
            target = qs.get("uddg", [None])[0]
            if target:
                return urllib.parse.unquote(target)
        return href

    async def execute(self, query: str, max_results: int = 5, **kwargs) -> Dict[str, Any]:
        """DuckDuckGo HTML(비-JS) 검색 결과 페이지를 스크래핑한다.

        api.duckduckgo.com(Instant Answer API)은 즉답형 요약만 제공하고
        일반 웹 검색 결과는 주지 않아 대부분의 쿼리에서 빈 결과가 나온다.
        따라서 실제 검색 결과를 얻기 위해 JS 없는 HTML 결과 페이지를 사용한다.
        """
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Safari/537.36"
                    ),
                },
                follow_redirects=True,
            ) as client:
                response = await client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query},
                )
                response.raise_for_status()
                body = response.text

            links = self._RESULT_LINK_RE.findall(body)
            snippets = self._SNIPPET_RE.findall(body)

            results = []
            for i, (href, title_raw) in enumerate(links[:max_results]):
                title = self._clean(title_raw)
                url = self._unwrap_ddg_redirect(href)
                snippet = self._clean(snippets[i]) if i < len(snippets) else ""

                if not title or not url:
                    continue

                results.append(
                    {
                        "title": title,
                        "snippet": snippet,
                        "url": url,
                    }
                )

            return {
                "status": "success",
                "query": query,
                "results": results if results else "검색 결과가 없습니다.",
            }
        except httpx.HTTPStatusError as e:
            return {
                "status": "error",
                "message": f"웹 검색 실패 (HTTP {e.response.status_code}): {e}",
            }
        except Exception as e:
            return {"status": "error", "message": f"웹 검색 실패: {str(e)}"}