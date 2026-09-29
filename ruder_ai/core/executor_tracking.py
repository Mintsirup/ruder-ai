"""Index refresh, changed-file and search-result tracking, memory notes."""

from __future__ import annotations

import json
from typing import Any


class TrackingMixin:
    """Index refresh, changed-file and search-result tracking, memory notes."""

    SEARCH_RESULT_TOOLS = {"web_search", "web_fetch"}

    SESSION_SEARCH_RESULTS_CAP = 30

    def _refresh_index(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
    ) -> None:

        if self.agent is None:
            return

        if tool_name not in self.FILE_MUTATING_TOOLS:
            return

        # 실패한 변형은 파일을 바꾸지 않았다. 이 경로에서 파일 하나를
        # 다시 인덱싱하면 전체 파생 인덱스(콜그래프/참조/시맨틱/타입)
        # 4종을 모두 재빌드하게 된다 — 실패한 호출마다 반복되면 불필요한
        # CPU 낭비이므로 성공한 변형만 갱신한다.
        if not self._is_tool_result_success(self._last_tool_result):
            return

        path = (
            kwargs.get("file_path")
            or kwargs.get("path")
        )

        if path:

            # Drop the derived-data cache entry for the file we just wrote.
            # (path, size, mtime_ns) is the cache key, so a normal write
            # already misses it - but on a filesystem with a coarse mtime a
            # same-size rewrite inside one tick would not, and a stale AST
            # would silently keep the old symbol names alive.
            from ruder_ai.indexer.file_cache import CACHE as _FILE_CACHE

            _FILE_CACHE.invalidate(
                self.agent.workspace_path / path
            )

            self.agent.refresh_file(path)

    def _track_changed_file(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        result: Any,
        changed_files: list[str],
    ) -> None:
        """이번 run() 호출 동안 실제로 변경된 파일 경로를 누적한다.

        reflector가 없어도 비용은 리스트 append 정도라 항상 추적해둔다.
        """

        if tool_name not in self.FILE_MUTATING_TOOLS:
            return

        # tool 실행이 실패했으면 실제로 파일이 바뀌지 않았을 가능성이 높음
        if isinstance(result, dict) and result.get("status") == "error":
            return

        if tool_name == "move_file":
            # move_file은 원본(file_path/path)이 아니라 목적지
            # (new_path/destination)에 실제 파일이 존재한다. 원본 경로를
            # 넣으면 Reflector가 그 경로를 열어보려다 "삭제됨/존재하지
            # 않음"으로 판단해 false negative를 낸다 (재현: FM-010).
            path = (
                kwargs.get("new_path")
                or kwargs.get("destination")
            )
        else:
            path = (
                kwargs.get("file_path")
                or kwargs.get("path")
            )

        if path and path not in changed_files:
            changed_files.append(path)
        if path:
            inferred_root = self._infer_project_root(path)
            if inferred_root:
                self._active_project_root = inferred_root
            context = getattr(self, "execution_context", None)
            if context is not None:
                context.add_changed_file(path)

    def _track_search_result(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        result: Any,
        search_results: list[dict[str, Any]],
    ) -> None:
        """web_search/web_fetch의 성공한 원문 결과를 누적한다.

        _finalize에서 이걸 다시 명시적으로 보여줘야, 몇 턴 전 대화에
        묻힌 검색 결과를 작은 모델이 최종 답변 시점에 놓치지 않는다
        (_track_changed_file과 동일한 이유 — 위 run()의 search_results
        주석 참고).

        동시에 self._session_search_results(세션 전체 기록, 턴이
        바뀌어도 유지됨)에도 같이 쌓는다 — _enforce_web_fetch_url이
        "이 URL이 검색된 적 있는지" 판단할 때 이번 턴만 보면, 이전
        턴에서 이미 찾은 올바른 URL을 이번 턴에 재사용해도 "지어낸
        URL"로 오판하는 문제가 있었다 (재현됨).
        """

        if tool_name not in self.SEARCH_RESULT_TOOLS:
            return

        if not self._is_tool_result_success(result):
            return

        entry = {
            "tool": tool_name,
            "kwargs": kwargs,
            "result": result,
        }

        search_results.append(entry)

        self._session_search_results.append(entry)
        if len(self._session_search_results) > self.SESSION_SEARCH_RESULTS_CAP:
            self._session_search_results = (
                self._session_search_results[
                    -self.SESSION_SEARCH_RESULTS_CAP :
                ]
            )

    def _set_failure_context(self, tool_name: str, result: Any) -> None:
        try:
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception:
            text = str(result)
        self._last_failure_context = f"Tool={tool_name}\n{text[:6000]}"

    def _record_task_memory(
        self,
        task: str,
        plan,
        response: str,
        changed_files: list[str],
        success: bool,
    ) -> None:
        """작업이 끝났을 때(성공/실패 무관) 최근 작업 기록에 남긴다.

        memory가 없으면 아무 일도 하지 않는다 (하위 호환).
        """

        if self.memory is None:
            return

        goal_text = (
            plan.goal.text
            if plan is not None
            else task
        )

        try:
            self.memory.record_task(
                task=task,
                goal=goal_text,
                result_summary=(response or "")[:500],
                changed_files=changed_files,
                success=success,
            )
        except Exception:
            # 기록 실패가 작업 자체의 성공/실패에 영향을 주면 안 됨.
            pass

    def _note_scratchpad(self, entry: str) -> None:
        """memory가 있으면 Scratchpad에 한 줄 기록한다 (없으면 무시)."""

        if self.memory is None:
            return

        try:
            self.memory.add_scratchpad_entry(entry)
        except Exception:
            pass

    def _rebuild_context_summary(self, task: str, failure: str) -> str:
        """Retry Loop: 실패 로그를 반영해 컨텍스트(관련 파일/심볼)를
        다시 검색한다.

        agent(및 그 위의 TaskPlanner/ContextBuilder/PromptFormatter,
        project_index)가 없으면 컨텍스트 재생성 없이 빈 문자열을 반환한다
        (GoalPlanner.replan()은 context_summary=""여도 정상 동작한다 —
        하위 호환).
        """

        agent = self.agent

        if (
            agent is None
            or getattr(agent, "planner", None) is None
            or getattr(agent, "context_builder", None) is None
            or getattr(agent, "prompt_formatter", None) is None
        ):
            return ""

        if getattr(agent, "project_index", None) is None:
            # verify_project 실패("project 없음")로 재계획하는 경우가
            # 대표적으로 여기 걸린다 — 인덱스가 비어 있다고 바로
            # 포기하면 재계획해도 똑같은 이유로 계속 실패한다.
            try:
                agent.rebuild_index()
            except Exception:
                pass

        if getattr(agent, "project_index", None) is None:
            return ""

        try:
            query = f"{task}\n\n{failure}" if failure else task

            context_plan = agent.planner.plan(
                prompt=query,
                index=agent.project_index,
            )

            context = agent.context_builder.build(
                context_plan,
                agent.project_index,
            )

            return agent.prompt_formatter.build_user_prompt(context)

        except Exception:
            # 컨텍스트 재생성 실패가 재계획 자체를 막으면 안 된다.
            return ""
