"""Base types for role-separated agents."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from ruder_ai.core.file_resolver import FileResolver

@dataclass(frozen=True, slots=True)
class AgentRole:
    name: str
    description: str
    can_mutate: bool = False
    can_execute: bool = False

class BaseRoleAgent:
    role = AgentRole("base", "기본 역할")

    def __init__(self, llm=None, workspace_path: str | Path | None = None):
        self.llm = llm
        self.workspace_path = Path(workspace_path or ".").resolve()
        self.file_resolver = FileResolver(self.workspace_path)

    def system_prefix(self) -> str:
        return (
            f"당신은 RuderAI의 {self.role.name} Agent입니다.\n"
            f"역할: {self.role.description}\n"
            "다른 Agent의 책임을 대신하지 말고 자신의 역할만 수행하세요.\n"
        )
