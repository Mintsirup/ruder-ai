"""Dual Memory System: Short-term (Scratchpad/Working Memory) &
Long-term (Project Memory + 최근 작업 기록).

TODO.md의 "Memory" 항목 구현:
- MemoryManager 연결: agent.py/goal_planner.py/executor.py에서 이 클래스를
  실제로 사용한다 (이 파일 자체는 데이터 구조와 영속화만 담당).
- Project Memory: 프로젝트 단위로 영구 저장되는 key-value 저장소.
- Working Memory: 현재 진행 중인 작업(task) 하나에 대한 상태
  (task 시작 시 초기화되는 휘발성 상태).
- Scratchpad: 작업 진행 중 쌓이는 단기 추론/실행 메모.
- 최근 작업 기록: 완료된 작업들의 이력 (Goal, 변경 파일, 성공 여부 등).

Project Memory와 최근 작업 기록은 workspace 안의 JSON 파일에 저장되어
프로세스가 재시작돼도 유지된다 (`core/config.py::ConfigManager`가 쓰는
"workspace 루트에 JSON 파일 하나" 방식과 동일한 패턴). Scratchpad/Working
Memory는 작업 단위로만 의미가 있는 휘발성 상태라 디스크에 저장하지 않는다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MEMORY_FILENAME = ".ruder_ai_memory.json"
DEFAULT_MAX_HISTORY = 20
MEMORY_SCHEMA_VERSION = 2


@dataclass(slots=True)
class TaskRecord:
    """완료된 작업 1건에 대한 기록 (최근 작업 기록)."""

    task: str
    goal: str = ""
    result_summary: str = ""
    changed_files: list[str] = field(default_factory=list)
    success: bool = True
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "goal": self.goal,
            "result_summary": self.result_summary,
            "changed_files": self.changed_files,
            "success": self.success,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskRecord":
        return cls(
            task=str(data.get("task", "")),
            goal=str(data.get("goal", "")),
            result_summary=str(data.get("result_summary", "")),
            changed_files=list(data.get("changed_files") or []),
            success=bool(data.get("success", True)),
            timestamp=float(data.get("timestamp") or time.time()),
        )


class MemoryManager:
    """Scratchpad + Working Memory(휘발성) + Project Memory/최근 작업
    기록(영구 저장)을 관리한다."""

    def __init__(
        self,
        workspace_path: str | Path = ".",
        max_history: int = DEFAULT_MAX_HISTORY,
        memory_filename: str = DEFAULT_MEMORY_FILENAME,
    ):
        self.workspace_path = Path(workspace_path)
        self.max_history = max_history
        self._memory_path = self.workspace_path / memory_filename
        self._memory_path.parent.mkdir(parents=True, exist_ok=True)

        # -------------------------
        # Scratchpad / Working Memory (task 단위, 휘발성)
        # -------------------------
        self.scratchpad: list[dict[str, Any]] = []
        self.working_memory: dict[str, Any] = {}

        # -------------------------
        # Project Memory / 최근 작업 기록 (영구 저장)
        # -------------------------
        self.long_term_memory: dict[str, Any] = {}
        self.task_history: list[TaskRecord] = []

        self._load()

    # ------------------------------------------------------------------
    # Scratchpad (단기 추론/실행 메모)
    # ------------------------------------------------------------------

    def add_scratchpad_entry(self, entry: str) -> None:
        """Adds a short-term reasoning step to the Scratchpad."""
        self.scratchpad.append(
            {"content": entry, "timestamp": time.time()}
        )

    def clear_scratchpad(self) -> None:
        """Clears current Scratchpad notes."""
        self.scratchpad.clear()

    def scratchpad_summary(self) -> str:
        """Scratchpad 내용을 프롬프트에 넣을 수 있는 문자열로 요약한다."""
        if not self.scratchpad:
            return ""
        return "\n".join(
            f"- {entry['content']}" for entry in self.scratchpad
        )

    # ------------------------------------------------------------------
    # Working Memory (현재 진행 중인 작업의 상태, task 단위 휘발성)
    # ------------------------------------------------------------------

    def start_task(self, task: str) -> None:
        """새 작업을 시작할 때 Working Memory/Scratchpad를 초기화한다."""
        self.working_memory = {"task": task, "changed_files": []}
        self.clear_scratchpad()

    def set_working(self, key: str, value: Any) -> None:
        self.working_memory[key] = value

    def get_working(self, key: str, default: Any = None) -> Any:
        return self.working_memory.get(key, default)

    # ------------------------------------------------------------------
    # Project Memory (영구, 프로젝트 단위 key-value)
    # ------------------------------------------------------------------

    def save_long_term_memory(self, key: str, value: Any) -> None:
        """Saves persistent project context info."""
        self.long_term_memory[key] = value
        self._save()

    def get_long_term_memory(self, key: str) -> Any:
        """Retrieves persistent project context info."""
        return self.long_term_memory.get(key)

    # ------------------------------------------------------------------
    # 최근 작업 기록 (Recent Task History)
    # ------------------------------------------------------------------

    def record_task(
        self,
        task: str,
        goal: str = "",
        result_summary: str = "",
        changed_files: list[str] | None = None,
        success: bool = True,
    ) -> None:
        """작업 완료 후 결과를 최근 작업 기록에 추가하고 즉시 저장한다."""

        normalized_task = " ".join(str(task or "").split())
        normalized_goal = " ".join(str(goal or "").split())
        normalized_files = sorted({str(item).replace("\\", "/") for item in (changed_files or []) if item})

        # 최근 동일 작업을 바로 다시 기록하지 않는다. 이는 bridge 재시작이나
        # reflection 재시도에서 메모리가 같은 성공/실패 기록으로 오염되는
        # 것을 막는다. 결과가 달라졌다면 별도 기록을 허용한다.
        if self.task_history:
            latest = self.task_history[-1]
            if (
                " ".join(latest.task.split()) == normalized_task
                and sorted(set(latest.changed_files)) == normalized_files
                and latest.success == bool(success)
                and latest.result_summary[:500] == str(result_summary or "")[:500]
            ):
                return

        record = TaskRecord(
            task=normalized_task,
            goal=normalized_goal,
            result_summary=" ".join(str(result_summary or "").split())[:500],
            changed_files=normalized_files,
            success=bool(success),
        )

        self.task_history.append(record)

        if len(self.task_history) > self.max_history:
            self.task_history = self.task_history[-self.max_history:]

        self._save()

    def recent_history_summary(self, limit: int = 5) -> str:
        """최근 작업 기록을 Planner 프롬프트에 넣을 수 있는 문자열로 요약."""

        if not self.task_history:
            return ""

        recent = self.task_history[-limit:]
        lines = []

        for record in reversed(recent):
            status = "성공" if record.success else "실패"
            files = ", ".join(record.changed_files) or "-"
            lines.append(
                f"- [{status}] {record.goal or record.task} "
                f"(변경 파일: {files})"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence (Project Memory + 최근 작업 기록만 디스크에 저장)
    # ------------------------------------------------------------------

    def _load(self) -> None:

        if not self._memory_path.exists():
            return

        try:
            with open(self._memory_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            # 손상된 메모리 파일은 무시하고 빈 상태로 시작한다.
            return

        self.long_term_memory = dict(
            data.get("long_term_memory") or {}
        )

        self.task_history = [
            TaskRecord.from_dict(item)
            for item in (data.get("task_history") or [])
            if isinstance(item, dict)
        ]

    def _save(self) -> None:

        try:
            payload = {
                "schema_version": MEMORY_SCHEMA_VERSION,
                "long_term_memory": self.long_term_memory,
                "task_history": [
                    record.to_dict() for record in self.task_history
                ],
            }

            with open(self._memory_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

        except Exception:
            # 메모리 저장 실패는 작업 자체를 실패시키지 않는다.
            pass
