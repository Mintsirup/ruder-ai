"""Independent verification agent."""
from __future__ import annotations

from .base import AgentRole, BaseRoleAgent
from ruder_ai.core.file_resolver import FileResolver


class TesterAgent(BaseRoleAgent):
    """Runs verification independently of the Coder stage."""

    __test__ = False
    role = AgentRole(
        "Tester",
        "변경 결과를 빌드·테스트·정적 검증으로 독립 확인",
        False,
        True,
    )

    async def verify(
        self,
        executor,
        *,
        task: str = "",
        target_files: list[str] | None = None,
    ) -> tuple[bool, str, dict]:
        """Run verify_project directly as the Tester role.

        This intentionally bypasses the Coder's plan/execution loop: a test
        result is evidence, not another LLM-generated action plan.
        """
        previous_role = getattr(executor, "_active_role", None)
        previous_task = getattr(executor, "_current_task", "")
        try:
            executor._active_role = self.role.name
            executor._current_task = task or ""
            # Tester independently resolves the requested files instead of
            # trusting Explorer's file list. This prevents a stale/partial
            # index from turning an existing file into a false "missing".
            workspace = getattr(executor, "workspace_path", None)
            resolved_targets = (
                FileResolver(workspace).resolve_many(target_files or [])
                if workspace else list(target_files or [])
            )
            kwargs = {}
            if resolved_targets:
                kwargs["target_files"] = resolved_targets
            elif target_files:
                kwargs["target_files"] = list(target_files)
            result = await executor._execute_tool("verify_project", kwargs)
        finally:
            executor._active_role = previous_role
            executor._current_task = previous_task

        status = str(result.get("status", "error"))
        passed = status == "success"
        summary = str(result.get("summary", "") or result.get("message", ""))
        failure = str(result.get("failure_log", "") or "")

        if passed:
            text = summary or "검증 통과"
        else:
            text = summary or failure or "프로젝트 검증에 실패했습니다."
            if failure and failure not in text:
                text = f"{text}\n{failure}"

        executor.last_verification_summary = text
        executor.last_verification_result = dict(result)
        context = getattr(executor, "execution_context", None)
        if context is not None:
            context.verification = {
                "success": passed,
                "summary": text,
                "result": dict(result),
                "target_files": list(target_files or []),
            }
        return passed, text, result

    def verification_summary(self, executor) -> str:
        return getattr(executor, "last_verification_summary", "") or ""
