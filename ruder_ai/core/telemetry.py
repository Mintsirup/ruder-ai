from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


#: Printed at the end of every run - CLI, GUI chat and the JSONL execution
#: log. Without an explicit terminator there is no way to tell "the agent
#: finished" from "the stream was cut off mid-task" when reading a transcript
#: or tailing the log.
END_OF_TOKEN = "End Of Token"

#: The same marker as a JSONL event name, for the execution log.
END_OF_TOKEN_EVENT = "end_of_token"


def _jsonable(value: Any, limit: int = 4000) -> Any:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) > limit:
            return text[:limit] + f"...<truncated:{len(text)}>"
        return json.loads(text)
    except Exception:
        text = str(value)
        return text[:limit]


class JsonlExecutionLogger:
    """Append-only JSONL execution logger with bounded event payloads."""

    def __init__(self, workspace: str | Path, *, directory: str = ".ruder_ai_logs") -> None:
        self.workspace = Path(workspace).resolve()
        self.directory = self.workspace / directory
        self._lock = Lock()

    def _path(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return self.directory / f"executions-{stamp}.jsonl"

    def log(self, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{key: _jsonable(value) for key, value in fields.items()},
        }
        try:
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                with self._path().open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            # Telemetry must never break the agent.
            return


@dataclass(slots=True)
class ToolMetrics:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0
    last_latency_ms: float = 0.0
    last_status: str = ""
    last_error_type: str = ""

    def observe(self, result: dict[str, Any], latency_ms: float) -> None:
        self.calls += 1
        self.total_latency_ms += latency_ms
        self.last_latency_ms = latency_ms
        self.last_status = str(result.get("status", ""))
        self.last_error_type = str(result.get("error_type", "") or "")
        if result.get("status") in ("error", "failed"):
            self.failures += 1
        else:
            self.successes += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "successes": self.successes,
            "failures": self.failures,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "average_latency_ms": round(self.total_latency_ms / self.calls, 2) if self.calls else 0.0,
            "last_latency_ms": round(self.last_latency_ms, 2),
            "last_status": self.last_status,
            "last_error_type": self.last_error_type,
        }


class MetricsRegistry:
    """In-process task/tool metrics; snapshots are safe to serialize."""

    def __init__(self) -> None:
        self.tools: dict[str, ToolMetrics] = {}
        self.task_count = 0
        self.task_successes = 0
        self.task_failures = 0
        self._started = time.monotonic()

    def task_start(self) -> None:
        self.task_count += 1

    def task_end(self, success: bool) -> None:
        if success:
            self.task_successes += 1
        else:
            self.task_failures += 1

    def tool_end(self, name: str, result: dict[str, Any], latency_ms: float) -> None:
        metric = self.tools.setdefault(name, ToolMetrics())
        metric.observe(result, latency_ms)

    def snapshot(self) -> dict[str, Any]:
        return {
            "uptime_ms": round((time.monotonic() - self._started) * 1000, 2),
            "tasks": {
                "calls": self.task_count,
                "successes": self.task_successes,
                "failures": self.task_failures,
            },
            "tools": {name: metric.as_dict() for name, metric in sorted(self.tools.items())},
        }
