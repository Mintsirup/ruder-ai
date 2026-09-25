from __future__ import annotations

import json
from pathlib import Path

from ruder_ai.core.memory import MemoryManager
from ruder_ai.core.telemetry import JsonlExecutionLogger, MetricsRegistry


def test_jsonl_execution_logger_writes_event(tmp_path: Path):
    logger = JsonlExecutionLogger(tmp_path)
    logger.log("tool_end", tool="read_file", latency_ms=12.5, result={"status": "success"})
    files = list((tmp_path / ".ruder_ai_logs").glob("executions-*.jsonl"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert data["event"] == "tool_end"
    assert data["tool"] == "read_file"


def test_metrics_registry_tracks_tool_latency():
    metrics = MetricsRegistry()
    metrics.task_start()
    metrics.tool_end("read_file", {"status": "success"}, 12.0)
    metrics.tool_end("read_file", {"status": "error", "error_type": "not_found"}, 8.0)
    metrics.task_end(False)
    snapshot = metrics.snapshot()
    assert snapshot["tasks"]["failures"] == 1
    assert snapshot["tools"]["read_file"]["calls"] == 2
    assert snapshot["tools"]["read_file"]["average_latency_ms"] == 10.0


def test_memory_deduplicates_identical_latest_record(tmp_path: Path):
    memory = MemoryManager(tmp_path)
    kwargs = dict(task="fix PlayerController", goal="fix jump", result_summary="PASS", changed_files=["Assets\\Scripts\\PlayerController.cs"], success=True)
    memory.record_task(**kwargs)
    memory.record_task(**kwargs)
    assert len(memory.task_history) == 1
