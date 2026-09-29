#!/usr/bin/env python3
"""Run RuderBench through the real RUDER-AI AIAgent.process_task() path.

Usage (from RUDER-AI root):
  python benchmarks/run_benchmark.py --model ruder-ai-agent:latest

The runner creates an isolated workspace for every case. It never mutates the
RUDER-AI source tree; only the benchmark fixture workspaces are modified.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ruder_ai.core.agent import AIAgent
from ruder_ai.core.settings import RuderAISettings

BENCH_FILE = ROOT / "benchmarks" / "ruderbench_music_player.json"
FIXTURES = ROOT / "benchmarks" / "fixtures"
RESULTS = ROOT / "benchmarks" / "results"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run RuderBench on the real RUDER-AI agent")
    p.add_argument("--model", default=os.getenv("RUDER_AI_MODEL", "ruder-ai-agent:latest"))
    p.add_argument("--ollama-host", default=os.getenv("RUDER_AI_OLLAMA_HOST", "http://127.0.0.1:11434"))
    p.add_argument("--case", action="append", help="Run only selected case ID(s), e.g. --case MP-01")
    p.add_argument("--bench-file", type=Path, default=BENCH_FILE, help="Benchmark JSON path")
    p.add_argument("--timeout", type=float, default=float(os.getenv("RUDER_AI_TIMEOUT", "300")))
    p.add_argument("--max-steps", type=int, default=int(os.getenv("RUDER_AI_MAX_STEPS", "5")))
    p.add_argument("--max-replans", type=int, default=int(os.getenv("RUDER_AI_MAX_REPLANS", "2")))
    p.add_argument("--max-reflections", type=int, default=int(os.getenv("RUDER_AI_MAX_REFLECTIONS", "2")))
    p.add_argument("--no-reflection", action="store_true")
    p.add_argument("--keep-workspaces", action="store_true")
    p.add_argument("--output", type=Path, help="Write result JSON to this path")
    return p.parse_args()


def load_bench(path: Path = BENCH_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".ruder_ai_logs/"):
            continue
        if any(part in {"__pycache__", ".pytest_cache", ".venv", "node_modules"} for part in p.parts):
            continue
        out[rel] = sha256_file(p)
    return out


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(set(before) | set(after)) if False else sorted(
        path for path in (set(before) | set(after)) if before.get(path) != after.get(path)
    )


def source_files(root: Path) -> list[Path]:
    result = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in {".ruder_ai_logs", "__pycache__", ".pytest_cache", ".venv", "node_modules"} for part in p.parts):
            continue
        if p.suffix.lower() in {".py", ".java", ".kt", ".js", ".ts", ".cs", ".go", ".rs"}:
            result.append(p)
    return result


def combined_source_text(root: Path) -> str:
    chunks: list[str] = []
    for p in source_files(root):
        try:
            chunks.append(p.read_text(encoding="utf-8", errors="replace").lower())
        except OSError:
            pass
    return "\n".join(chunks)


def parse_latest_log(root: Path) -> list[dict[str, Any]]:
    log_dir = root / ".ruder_ai_logs"
    paths = sorted(log_dir.glob("executions-*.jsonl")) if log_dir.exists() else []
    if not paths:
        return []
    path = paths[-1]
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def event_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    tools = [e for e in events if e.get("event") == "tool_end"]
    task_ends = [e for e in events if e.get("event") == "task_end"]
    return {
        "event_count": len(events),
        "task_end_count": len(task_ends),
        "tool_calls": len(tools),
        "tools": [e.get("tool") for e in tools],
        "tool_statuses": {str(e.get("tool")): e.get("status") for e in tools[-20:]},
        "last_task_end": task_ends[-1] if task_ends else None,
    }


def score_case(case: dict[str, Any], root: Path, before: dict[str, str], after: dict[str, str], outputs: list[str], events: list[dict[str, Any]], prompt_records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    criteria = case.get("criteria", {})
    all_text = combined_source_text(root)
    all_output = "\n".join(outputs).lower()
    changed = changed_paths(before, after)
    tools = [str(e.get("tool")) for e in events if e.get("event") == "tool_end"]
    checks: dict[str, Any] = {}

    execution_results = list(prompt_records or [])
    expected_success = criteria.get("expected_execution_success")
    if expected_success is not None:
        checks["execution_success"] = all(
            bool(item.get("execution_result", {}).get("success", False)) is bool(expected_success)
            for item in execution_results
        )
    elif criteria.get("require_execution_success", True):
        checks["execution_success"] = bool(execution_results) and all(
            bool(item.get("execution_result", {}).get("success", False)) for item in execution_results
        )

    if criteria.get("source_file"):
        checks["source_file"] = any(p.suffix.lower() in {".py", ".java"} for p in source_files(root))
    if criteria.get("java_file"):
        checks["java_file"] = any(p.suffix.lower() == ".java" for p in source_files(root))
    if criteria.get("no_python_source_after_migration"):
        checks["no_python_source_after_migration"] = not any(
            p.suffix.lower() == ".py" and p.name.lower() != "__init__.py"
            for p in source_files(root)
        )
    if "codec_keywords" in criteria:
        checks["codec_keywords"] = all(x.lower() in all_text for x in criteria["codec_keywords"])
    if "seek_keywords" in criteria:
        checks["seek_keyword_count"] = sum(x.lower() in all_text for x in criteria["seek_keywords"])
        checks["seek_keywords"] = checks["seek_keyword_count"] >= 2
    if criteria.get("has_verification_record"):
        task_ends = [e for e in events if e.get("event") == "task_end"]
        checks["has_verification_record"] = bool(task_ends and isinstance(task_ends[-1].get("verification"), dict))
    if criteria.get("verification_not_falsely_successful"):
        task_ends = [e for e in events if e.get("event") == "task_end"]
        verification = task_ends[-1].get("verification") if task_ends else None
        checks["verification_not_falsely_successful"] = (
            isinstance(verification, dict)
            and "success" in verification
        )
    if criteria.get("no_source_change"):
        checks["no_source_change"] = all(
            path not in changed or Path(path).suffix.lower() not in {".py", ".java", ".kt", ".js", ".ts", ".cs", ".go", ".rs"}
            for path in changed
        )
    if criteria.get("target_changed"):
        target = criteria["target_changed"].replace("\\", "/")
        checks["target_changed"] = target in changed
    if criteria.get("uses_shell_tool"):
        checks["uses_shell_tool"] = "execute_shell" in tools
    if criteria.get("not_python_tool_for_shell"):
        shell_execs = [e for e in events if e.get("event") == "tool_end" and e.get("tool") == "execute_code"]
        checks["not_python_tool_for_shell"] = not shell_execs
    if criteria.get("tool_must_include"):
        req = criteria["tool_must_include"]
        checks["tool_must_include"] = req in tools
    if criteria.get("tool_must_exclude"):
        bad = criteria["tool_must_exclude"]
        checks["tool_must_exclude"] = bad not in tools
    if criteria.get("files_exist"):
        checks["files_exist"] = all((root / f).is_file() for f in criteria["files_exist"] if f)
    if criteria.get("files_absent"):
        checks["files_absent"] = all(not (root / f).exists() for f in criteria["files_absent"] if f)
    if criteria.get("files_any"):
        checks["files_any"] = any((root / f).is_file() for f in criteria["files_any"] if f)
    if criteria.get("source_ext_any"):
        allowed = {str(x).lower() for x in criteria["source_ext_any"]}
        checks["source_ext_any"] = any(p.suffix.lower() in allowed for p in source_files(root))
    if criteria.get("contains_all"):
        checks["contains_all"] = all(str(x).lower() in all_text for x in criteria["contains_all"])
    if criteria.get("contains_any"):
        checks["contains_any"] = any(str(x).lower() in all_text for x in criteria["contains_any"])
    if criteria.get("not_contains_all"):
        checks["not_contains_all"] = all(str(x).lower() not in all_text for x in criteria["not_contains_all"])
    if criteria.get("not_changed_prefix"):
        pref = str(criteria["not_changed_prefix"]).replace("\\", "/").rstrip("/") + "/"
        checks["not_changed_prefix"] = not any(path.replace("\\", "/").startswith(pref) for path in changed)

    passed = all(bool(v) for k, v in checks.items() if not k.endswith("_count")) if checks else False
    return {
        "passed": passed,
        "checks": checks,
        "changed_files": changed,
        "outputs": outputs,
        "event_summary": event_summary(events),
        "source_files": [p.relative_to(root).as_posix() for p in source_files(root)],
        "last_response_contains_error": "[llm_error]" in all_output,
    }


async def run_case(case: dict[str, Any], args: argparse.Namespace, run_root: Path) -> dict[str, Any]:
    case_id = case["id"]
    case_dir = run_root / case_id
    if case_dir.exists():
        shutil.rmtree(case_dir)
    fixture = case.get("setup", {}).get("fixture", "music_player")
    fixture_dir = FIXTURES / fixture
    shutil.copytree(fixture_dir, case_dir)

    before = snapshot(case_dir)
    settings = RuderAISettings(
        model=args.model,
        ollama_host=args.ollama_host,
        temperature=float(os.getenv("RUDER_AI_TEMPERATURE", "0.1")),
        num_ctx=int(os.getenv("RUDER_AI_NUM_CTX", "16384")),
        max_tokens=int(os.getenv("RUDER_AI_MAX_TOKENS", "3072")),
        timeout=args.timeout,
        context_token_budget=int(os.getenv("RUDER_AI_CONTEXT_TOKEN_BUDGET", "4352")),
        max_files=int(os.getenv("RUDER_AI_MAX_FILES", "5")),
        max_snippets_per_file=int(os.getenv("RUDER_AI_MAX_SNIPPETS_PER_FILE", "5")),
        max_steps=args.max_steps,
        max_replans=args.max_replans,
        max_reflections=args.max_reflections,
        enable_reflection=not args.no_reflection,
        autonomy_enabled=False,
    )
    agent = AIAgent(workspace_path=str(case_dir), settings=settings)

    outputs: list[str] = []
    prompts = case.get("prompts", [])
    started = time.perf_counter()
    prompt_records = []
    for prompt in prompts:
        prompt_started = time.perf_counter()
        try:
            response = await agent.process_task(prompt, mode=case.get("mode", "code"))
            status = "ok"
            error = ""
        except Exception as exc:
            response = ""
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = round((time.perf_counter() - prompt_started) * 1000, 2)
        outputs.append(str(response))
        result_obj = agent.executor.last_execution_result
        prompt_records.append({
            "prompt": prompt,
            "status": status,
            "error": error,
            "elapsed_ms": elapsed_ms,
            "response": str(response),
            "execution_result": {
                "success": bool(getattr(result_obj, "success", False)),
                "changed_files": list(getattr(result_obj, "changed_files", [])),
                "verification": copy.deepcopy(getattr(result_obj, "verification", {})),
                "tasks": copy.deepcopy(getattr(result_obj, "tasks", [])),
                "warnings": copy.deepcopy(getattr(result_obj, "warnings", [])),
                "error": copy.deepcopy(getattr(result_obj, "error", {})),
                "metrics": copy.deepcopy(getattr(result_obj, "metrics", {})),
            },
        })

    after = snapshot(case_dir)
    events = parse_latest_log(case_dir)
    score = score_case(case, case_dir, before, after, outputs, events, prompt_records)
    return {
        "id": case_id,
        "title": case.get("title", ""),
        "fixture": fixture,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "prompts": prompt_records,
        "score": score,
        "workspace": str(case_dir),
    }


async def main_async(args: argparse.Namespace) -> int:
    bench = load_bench(args.bench_file)
    wanted = set(args.case or [])
    cases = [c for c in bench["cases"] if not wanted or c["id"] in wanted]
    if wanted and len(cases) != len(wanted):
        missing = sorted(wanted - {c["id"] for c in cases})
        raise SystemExit(f"알 수 없는 case: {', '.join(missing)}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_root = RESULTS / f"workspace-{stamp}"
    run_root.mkdir(parents=True, exist_ok=True)

    result = {
        "benchmark": bench["name"],
        "version": bench["version"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "forge_root": str(ROOT),
        "model": args.model,
        "ollama_host": args.ollama_host,
        "runner": "AIAgent.process_task",
        "cases": [],
    }

    try:
        for case in cases:
            print(f"[{case['id']}] {case.get('title','')} ...", flush=True)
            case_result = await run_case(case, args, run_root)
            result["cases"].append(case_result)
            print(f"  -> {'PASS' if case_result['score']['passed'] else 'FAIL'} ({case_result['elapsed_ms']:.0f} ms)", flush=True)
    finally:
        if not args.keep_workspaces and run_root.exists():
            # Keep the JSON result, remove bulky per-case workspaces.
            shutil.rmtree(run_root, ignore_errors=True)

    passed = sum(1 for c in result["cases"] if c["score"]["passed"])
    result["summary"] = {"passed": passed, "failed": len(result["cases"]) - passed, "total": len(result["cases"])}
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    output = args.output or (RESULTS / f"benchmark-{stamp}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과: {passed}/{len(result['cases'])} PASS")
    print(f"저장: {output}")
    return 0 if passed == len(result["cases"]) else 2


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
