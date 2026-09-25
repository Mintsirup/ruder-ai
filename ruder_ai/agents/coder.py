"""Code execution agent."""
from __future__ import annotations
from .base import AgentRole, BaseRoleAgent

class CoderAgent(BaseRoleAgent):
    role = AgentRole("Coder", "계획된 변경을 실제 코드에 적용", True, True)

    async def execute(self, executor, messages, task: str, plan) -> str:
        return await executor.run(messages, task=task, plan=plan, role=self.role.name)
