"""Prompt assembly: task instructions, kwargs schema, web-fetch grounding."""

from __future__ import annotations

import inspect
import json
import re
from typing import Any


class PromptsMixin:
    """Prompt assembly: task instructions, kwargs schema, web-fetch grounding."""

    _TOOL_SPECIFIC_HINTS: dict[str, str] = {
        "execute_code": "이 Tool은 Python 코드 전용입니다. pip/npm/mvn/gradle/javac 같은 셸 명령을 넣지 마세요. 그런 명령은 execute_shell을 사용하세요.",
        "execute_shell": "OS 셸 명령만 실행하세요. Python 소스는 execute_code를 사용하세요.",
        "web_search": (
            "query에는 실제 검색 대상(제품명/게임명 등 구체적인 "
            "주제)이 들어가야 합니다. 지금 이 Task를 만든 사용자 "
            "요청이 짧거나 대명사만 있어서 주제가 안 보이면, 위 "
            "대화 기록에서 실제로 이야기하고 있던 주제를 찾아 "
            "query에 반드시 포함하세요. 예: 이전에 '마인크래프트'를 "
            "이야기하다가 사용자가 '그거 최신 버전 찾아줘'라고만 "
            "물었다면 query는 '몇 버전'이 아니라 '마인크래프트 최신 "
            "버전'이어야 합니다."
        ),
        "web_fetch": (
            "url은 지어내지 말고, 이 대화에 이미 나온 web_search Tool "
            "결과(results 목록의 url 필드) 중 질문과 가장 관련성 높은 "
            "것 하나를 그대로 골라 쓰세요."
        ),
    }



    _KNOWN_REQUIRED_OVERRIDES: dict[str, list[str]] = {
        "write_file": ["file_path", "content"],
        "patch_file": ["file_path", "old_str", "new_str"],
        "read_file": ["file_path"],
        "backup_file": ["file_path"],
        "delete_file": ["file_path"],
        "append_file": ["file_path", "content"],
        "move_file": ["file_path", "new_path"],
        "git_checkout": ["file_path"],
        "web_search": ["query"],
        "web_fetch": ["url"],
        "verify_project": [],
    }


    def _task_instruction(self, pt) -> str:
        """Task 하나를 실행하라는 지시 메시지를 만든다.

        Tool 이름은 Plan이 이미 정했으므로, LLM에게는 그 Tool의
        kwargs만 채우도록 요구한다 (Executor/LLM이 다른 Tool을
        선택하지 못하게 형식으로 제약).
        """

        if pt.tool is None:
            return (
                f"[Task {pt.order}] {pt.description}\n\n"
                "이 Task는 Tool 호출이 필요 없습니다. 사용자에게 직접"
                "말하듯이 답하세요 (예: 인사면 인사로 답하고, 질문이면 "
                "질문에 답하세요). '사용자는 ~라고 했습니다', '사용자가 "
                "~를 원합니다' 같은 3인칭 상황 서술/보고체로 쓰지 마세요"
                " — 그건 답변이 아니라 요약입니다. JSON 블록도 쓰지 "
                "마세요.\n"
                "중요: 이 Task는 실제로 어떤 파일/디렉터리도 만들거나 "
                "옮기거나 바꾸지 않습니다 (Tool을 호출하지 않으니까요). "
                "'~폴더를 만들었습니다', '~파일을 옮겼습니다'처럼 실제로 "
                "하지 않은 작업을 했다고 말하지 마세요 — 그건 거짓 "
                "보고입니다. 파일/디렉터리 작업이 필요한 요청이라면, "
                "지금 이 Task에서는 그걸 할 수 없다는 것과 그 이유를 "
                "사실대로 답하세요."
            )

        schema_hint = self._kwargs_schema_hint(pt.tool)
        tool_hint = self._TOOL_SPECIFIC_HINTS.get(pt.tool, "")
        scope_hint = (f"\n현재 활성 프로젝트 범위: {self._active_project_root}. 이 범위 밖의 파일을 수정하지 마세요." if self._active_project_root and pt.tool in self.FILE_MUTATING_TOOLS else "")

        return (
            f"[Task {pt.order}] {pt.description}\n\n"
            f"이 Task는 반드시 '{pt.tool}' Tool을 사용합니다. 다른 "
            "Tool을 선택하지 말고, 이 Tool의 kwargs만 채워서 아래 "
            "형식의 JSON 블록 하나로 응답하세요. 현재 Task의 설명과 실제 Tool 결과가 충돌하면 실제 Tool 결과를 우선하세요. 이미 성공한 이전 작업을 되돌리는 경로/파일을 추측하지 마세요.\n\n"
            "```json\n"
            "{\n"
            f'  "tool": "{pt.tool}",\n'
            '  "kwargs": { ... }\n'
            "}\n"
            "```\n"
            f"{schema_hint}"
            + (f"\n{tool_hint}" if tool_hint else "")
            + scope_hint
        )

    def _kwargs_schema_hint(self, tool_name: str) -> str:
        """Tool의 execute() 시그니처에서 kwargs 필드명/필수 여부를 뽑아
        Task 지시문에 붙일 힌트 문자열을 만든다.

        Tool 설명(description)만으로는 정확한 파라미터 이름을 모델이
        추측해야 하는데, 특히 작은 모델(gemma2:9b 등)은 file_path 같은
        필수 필드를 아예 빼먹거나 빈 값으로 보내는 경우가 실제로
        있었다 (write_file이 "file_path가 필요합니다"로 반복 실패한
        사례). Skill 코드가 이미 알고 있는 정확한 필드명을 프롬프트에
        직접 박아 넣어, 모델이 추측하지 않게 한다.
        """

        if self.skill_registry is None:
            return ""

        try:
            skill = self.skill_registry.get_skill(tool_name)
        except Exception:
            skill = None

        if skill is None:
            return ""

        try:
            sig = inspect.signature(skill.execute)
        except (TypeError, ValueError):
            return ""

        all_names: list[str] = []

        for name, param in sig.parameters.items():

            if name in ("self", "kwargs", "workspace_path", "project"):
                continue

            if param.kind in (
                inspect.Parameter.VAR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            ):
                continue

            all_names.append(name)

        if not all_names:
            return ""

        override = self._KNOWN_REQUIRED_OVERRIDES.get(tool_name)

        if override is not None:
            required = [n for n in override if n in all_names]
            optional = [n for n in all_names if n not in required]
        else:
            # 알려진 Tool이 아니면 기존처럼 시그니처의 기본값 유무로
            # 판단한다 (이 경우 file_path류 Optional 필드는 놓칠 수
            # 있지만, 최소한 아무 힌트도 없는 것보다는 낫다).
            required = [
                n for n in all_names
                if sig.parameters[n].default is inspect._empty
            ]
            optional = [n for n in all_names if n not in required]

        lines = [f"'{tool_name}' kwargs 필드:"]

        if required:
            lines.append(
                "- 필수: " + ", ".join(required)
                + " (반드시 값을 채우세요, 비워두면 실행이 실패합니다)"
            )

        if optional:
            lines.append("- 선택: " + ", ".join(optional))

        return "\n".join(lines)

    def _parse_kwargs_for(
        self,
        tool_name: str,
        response: str,
    ) -> dict[str, Any]:
        """응답에서 kwargs만 뽑아낸다. tool 이름은 Plan이 고정하므로,
        응답이 다른 Tool 이름을 반환해도 무시하고 kwargs만 사용한다
        (Executor가 Plan을 벗어난 Tool을 실행하지 않도록 하기 위함)."""

        parsed = self._parse_tool(response)

        if parsed is None:
            return {}

        called_tool = parsed.get("tool")

        if called_tool and called_tool != tool_name:

            print(
                f"⚠️ Task는 '{tool_name}' Tool을 지정했지만 응답은 "
                f"'{called_tool}'을 반환해 무시하고 계획된 Tool을 "
                "사용합니다."
            )

        kwargs = dict(parsed.get("kwargs") or {})
        kwargs = self._recover_file_path_from_task(tool_name, kwargs)

        # Common short aliases emitted by small local models. Keep the Tool
        # schema canonical while accepting these harmless aliases at the
        # Executor boundary so a format-only retry is not wasted.
        if tool_name in {"patch_file", "preview_patch"}:
            if "old" in kwargs and "old_str" not in kwargs:
                kwargs["old_str"] = kwargs.pop("old")
            if "new" in kwargs and "new_str" not in kwargs:
                kwargs["new_str"] = kwargs.pop("new")

        required = self._KNOWN_REQUIRED_OVERRIDES.get(tool_name)
        if required:
            missing = [k for k in required if k not in kwargs]
            empty = [
                k for k in required
                if k in kwargs and not kwargs.get(k)
            ]
            if missing or empty:
                detail_parts = []
                if missing:
                    detail_parts.append(f"누락된 키: {missing}")
                if empty:
                    detail_parts.append(f"값이 비어있는 키: {empty}")
                print(
                    f"⚠️ '{tool_name}' 필수 필드 문제 "
                    f"(필요: {required}, {', '.join(detail_parts)}, "
                    f"받은 kwargs 키: {list(kwargs.keys())})"
                )

        return kwargs

    @staticmethod
    def _format_for_log(value: Any, max_len: int = 800) -> str:
        """콘솔 로그용으로 kwargs/result를 한 줄 JSON 문자열로 요약한다.

        web_search/web_fetch 디버깅 때 "Task N: tool_name"만 찍히고
        실제 결과(성공 여부, 반환된 검색 결과, 에러 메시지)는 어디에도
        안 남아서 원인 파악이 막혔던 적이 있었다 — 이후로는 kwargs와
        결과를 항상 함께 찍는다. 너무 길면(특히 web_fetch 본문) 잘라서
        콘솔이 도배되지 않게 한다.
        """

        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)

        if len(text) > max_len:
            text = text[:max_len] + f"... (총 {len(text)}자, 이하 생략)"

        return text

    def _enforce_web_fetch_url(
        self,
        kwargs: dict[str, Any],
        search_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """web_fetch의 url이 실제 web_search 결과 안에 있는 것인지
        검증하고, 아니면(지어낸 URL이면) 코드가 직접 가장 관련성 높은
        후보로 덮어쓴다.

        "이전 web_search 결과에서 골라 써라"를 프롬프트로만 지시했을
        때, 작은 로컬 모델이 검색 결과에 없는 URL을 그냥 지어내는
        경우가 실제로 있었다(재현됨). URL 선택은 사실 여부가 걸린
        부분이라 모델의 자유 판단에 맡기지 않고 여기서 결정론적으로
        고른다.

        유효성 검사는 이번 턴 검색 결과(search_results)뿐 아니라
        self._session_search_results(세션 전체, 턴이 바뀌어도 유지)
        까지 함께 본다 — 그렇지 않으면 사용자가 이전 턴 맥락을 이어
        받아 짧게 되물었을 때, 모델이 대화 기록을 보고 이전 턴의
        올바른 URL을 정확히 재사용해도 "이번 턴엔 검색된 적 없다"는
        이유로 지어낸 URL 취급을 받아 엉뚱하게 덮어써지는 문제가
        실제로 있었다 (재현됨).
        """

        # 세션 전체에서 실제로 검색된 적 있는 URL 전체 집합 —
        # 여기 있으면 이번 턴 검색 결과에 없어도 "지어낸 URL"이
        # 아니므로 손대지 않는다.
        session_urls: set[str] = set()
        for item in self._session_search_results:
            if item.get("tool") != "web_search":
                continue
            for c in (item.get("result") or {}).get("results") or []:
                if c.get("url"):
                    session_urls.add(c["url"])

        proposed_url = (kwargs or {}).get("url")

        if proposed_url and proposed_url in session_urls:
            # 이전 턴을 포함해 세션 어딘가에서 실제로 검색된 URL이면
            # 모델의 선택(대화 맥락을 이어받은 선택일 수 있음)을
            # 존중한다.
            return kwargs

        # 지금부터는 "이번 턴 검색 결과 중 가장 관련성 높은 것으로
        # 강제 교체" 로직 — 후보는 이번 턴 것을 우선, 없으면 세션에서
        # 가장 최근 web_search 결과로 대체한다.
        candidates: list[dict[str, str]] = []
        query_tokens: set[str] = set()

        for item in search_results or self._session_search_results:
            if item.get("tool") != "web_search":
                continue

            result = item.get("result") or {}
            results_list = result.get("results") or []

            if not results_list:
                continue

            # 가장 최근 web_search 결과를 기준으로 삼는다.
            candidates = results_list
            query = (item.get("kwargs") or {}).get("query", "")
            query_tokens = {
                tok for tok in re.split(r"\s+", query) if len(tok) > 1
            }

        if not candidates:
            # 참고할 web_search 결과가 없으면 모델이 준 kwargs를
            # 그대로 둔다 (강제할 근거 자체가 없음).
            return kwargs

        def _score(candidate: dict[str, str]) -> int:
            text = f"{candidate.get('title', '')} {candidate.get('snippet', '')}"
            return sum(1 for tok in query_tokens if tok in text)

        best = max(candidates, key=_score) if candidates else None
        best_url = best.get("url") if best else None

        if not best_url:
            return kwargs

        new_kwargs = dict(kwargs or {})

        if proposed_url and proposed_url != best_url:
            print(
                f"   ⚠️ web_fetch가 검색 결과에 없는 URL을 지어내서 "
                f"코드에서 자동으로 교체했습니다: {proposed_url} -> "
                f"{best_url}"
            )

        new_kwargs["url"] = best_url

        return new_kwargs

    @staticmethod
    def _completed_call_key(
        tool_name: str,
        kwargs: dict[str, Any],
    ) -> tuple[str, str] | None:
        """(tool, kwargs)를 완료 여부 판정용 캐시 키로 정규화한다.

        kwargs를 JSON으로 직렬화할 수 없는 경우(드묾)에는 None을 반환해
        캐싱 대상에서 제외한다 — 판단 불가능한 걸 억지로 캐싱해서 잘못
        스킵하는 것보다, 그런 경우엔 그냥 항상 재실행하는 편이 안전하다.
        """

        try:
            kwargs_repr = json.dumps(
                kwargs, sort_keys=True, ensure_ascii=False, default=str,
            )
        except Exception:
            return None

        return (tool_name, kwargs_repr)
