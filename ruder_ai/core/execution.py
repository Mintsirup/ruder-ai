from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ExecutionTaskRecord:
    order: int
    tool: str
    status: str = "planned"
    kwargs: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass(slots=True)
class ExecutionContext:
    """Single source of truth for one task execution session."""

    request: str = ""
    workspace: str = ""
    role: str | None = None
    changed_files: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    search_results: list[dict[str, Any]] = field(default_factory=list)
    task_records: list[ExecutionTaskRecord] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    last_error: dict[str, Any] = field(default_factory=dict)
    requirement_satisfied: bool | None = None
    result_state: str = "PENDING"

    def record_task(self, order: int, tool: str, kwargs: dict[str, Any]) -> ExecutionTaskRecord:
        record = ExecutionTaskRecord(order=order, tool=tool, kwargs=dict(kwargs))
        self.task_records.append(record)
        return record

    def add_changed_file(self, path: str) -> None:
        if path and path not in self.changed_files:
            self.changed_files.append(path)

    def as_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "workspace": self.workspace,
            "role": self.role,
            "changed_files": list(self.changed_files),
            "target_files": list(self.target_files),
            "warnings": list(self.warnings),
            "search_results": list(self.search_results),
            "tasks": [
                {
                    "order": r.order,
                    "tool": r.tool,
                    "status": r.status,
                    "kwargs": dict(r.kwargs),
                    "message": r.message,
                }
                for r in self.task_records
            ],
            "verification": dict(self.verification),
            "last_error": dict(self.last_error),
            "requirement_satisfied": self.requirement_satisfied,
            "result_state": self.result_state,
        }


@dataclass(slots=True)
class ExecutionResult:
    """Structured, LLM-independent final execution state."""

    success: bool
    changed_files: list[str] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    requirement_satisfied: bool | None = None
    result_state: str = "FAILED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "changed_files": list(self.changed_files),
            "verification": dict(self.verification),
            "tasks": list(self.tasks),
            "warnings": list(self.warnings),
            "error": dict(self.error),
            "metrics": dict(self.metrics),
            "requirement_satisfied": self.requirement_satisfied,
            "result_state": self.result_state,
        }
