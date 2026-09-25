"""Memory agent."""
from __future__ import annotations
from .base import AgentRole, BaseRoleAgent

class MemoryAgent(BaseRoleAgent):
    role = AgentRole("Memory", "작업 결과와 중요한 결정·오류를 프로젝트 메모리에 기록", False, False)

    def record(self, memory, task: str, response: str, changed_files: list[str], success: bool, goal: str = "") -> None:
        if memory is None:
            return
        # Executor도 하위 호환을 위해 완료 시 Memory를 기록할 수 있으므로
        # 같은 작업/결과가 바로 앞에 있으면 중복 기록하지 않는다.
        history = getattr(memory, "task_history", None)
        if history:
            latest = history[-1]
            if (
                getattr(latest, "task", None) == task
                and getattr(latest, "changed_files", None) == list(changed_files)
                and bool(getattr(latest, "success", False)) == bool(success)
            ):
                return
        memory.record_task(
            task=task,
            goal=goal,
            result_summary=(response or "")[:500],
            changed_files=changed_files,
            success=success,
        )
