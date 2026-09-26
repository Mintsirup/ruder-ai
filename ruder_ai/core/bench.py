"""Measured benchmark for the paths that run on every task.

Timing is only meaningful if it is repeatable, so every stage here is warmed
before it is timed and reported as the best of N rather than the mean - a
single GC pause otherwise dominates the result on a small workspace.

The numbers are what a task actually pays for: indexing, planning, context
assembly, and the per-edit refresh that runs after every change the agent
makes.
"""

from __future__ import annotations

import gc
import shutil
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

#: A representative natural-language prompt in the product's primary language.
DEFAULT_PROMPT = (
    "사용자 로그인 실패가 계속 발생합니다. AuthenticationManager에서 "
    "토큰 검증 로직과 에러 처리 코드를 찾아서 고쳐주세요."
)


@dataclass(frozen=True, slots=True)
class Measurement:
    name: str
    best_ms: float
    median_ms: float

    def line(self, width: int) -> str:
        return (
            f"{self.name:<{width}}  best={self.best_ms:8.2f} ms"
            f"   median={self.median_ms:8.2f} ms"
        )


def _time(fn, reps: int) -> Measurement:
    fn()  # warm
    samples: list[float] = []
    for _ in range(reps):
        gc.collect()
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return Measurement("", min(samples), statistics.median(samples))


def run(
    workspace: str | Path,
    *,
    prompt: str = DEFAULT_PROMPT,
    reps: int = 3,
) -> dict:
    """Measure every hot path for ``workspace``."""
    from ruder_ai.context.builder import ContextBuilder
    from ruder_ai.core.agent import AIAgent
    from ruder_ai.core.planner import TaskPlanner
    from ruder_ai.indexer.call_graph import CallGraph
    from ruder_ai.indexer.detector import ProjectDetector
    from ruder_ai.indexer.file_cache import CACHE
    from ruder_ai.indexer.reference_index import ReferenceIndex
    from ruder_ai.indexer.scanner import ProjectScanner
    from ruder_ai.indexer.semantic_file_index import SemanticFileIndex
    from ruder_ai.indexer.symbol_indexer import SymbolIndexer
    from ruder_ai.indexer.type_resolver import TypeResolver

    root = Path(workspace).resolve()
    if not (root / "ruder_ai").is_dir() and not any(root.glob("*.py")):
        raise ValueError(f"not a project directory: {root}")

    results: list[Measurement] = []
    facts: dict = {}

    def measure(name: str, fn) -> None:
        m = _time(fn, reps)
        m = Measurement(name, m.best_ms, m.median_ms)
        results.append(m)

    def cold_index():
        CACHE.clear()
        scanner = ProjectScanner(root)
        files = scanner.scan()
        return SymbolIndexer().build(
            root, files, ProjectDetector().detect(root, files),
            dict(scanner.inverted_index),
        )

    def warm_index():
        scanner = ProjectScanner(root)
        files = scanner.scan()
        return SymbolIndexer().build(
            root, files, ProjectDetector().detect(root, files),
            dict(scanner.inverted_index),
        )

    measure("ProjectScanner.scan", lambda: ProjectScanner(root).scan())
    measure("index build (cold cache)", cold_index)
    measure("index build (warm cache)", warm_index)

    index = warm_index()
    facts["files"] = len(index.files)
    facts["symbols"] = len(index.symbols)
    facts["imports"] = len(index.imports)
    facts["tokens"] = len(index.inverted_index)

    def make_planner():
        planner = TaskPlanner(
            CallGraph(), ReferenceIndex(), SemanticFileIndex(), TypeResolver()
        )
        planner.call_graph.build(index)
        planner.reference_index.build(index)
        planner.semantic_file_index.build(index)
        planner.type_resolver.build(index)
        return planner

    planner = make_planner()
    measure("TaskPlanner.plan", lambda: planner.plan(prompt, index))
    plan = planner.plan(prompt, index)
    builder = ContextBuilder(root)
    measure("ContextBuilder.build", lambda: builder.build(plan, index))
    measure("ReferenceIndex.build", lambda: ReferenceIndex().build(index))

    def signature():
        AIAgent(workspace_path=str(root))._workspace_fs_signature()

    try:
        measure("workspace signature", signature)
    except Exception as exc:  # noqa: BLE001
        facts["signature_error"] = str(exc)

    # Per-edit refresh, in a throwaway copy so the caller's tree is untouched.
    scratch = Path(tempfile.mkdtemp(prefix="_ruder_bench_"))
    try:
        for name in ("ruder_ai", "tests", "benchmarks"):
            source = root / name
            if source.is_dir():
                shutil.copytree(source, scratch / name, dirs_exist_ok=True)
        if not any(scratch.iterdir()):
            for item in list(root.glob("*.py")):
                shutil.copy2(item, scratch / item.name)
        if not any(scratch.iterdir()):
            facts["edit_refresh"] = None
        else:
            agent = AIAgent(workspace_path=str(scratch))
            agent._build_project_index(force_refresh=True)
            # The biggest module is the one a real edit would land in, and
            # picking by size means a small project still gets the stage
            # measured instead of silently losing its most important number.
            candidates = sorted(
                scratch.rglob("*.py"),
                key=lambda p: p.stat().st_size,
                reverse=True,
            )
            editable = candidates[0] if candidates else None
            if editable is None:
                facts["edit_refresh"] = None
            else:
                target = editable
                relative = target.relative_to(scratch).as_posix()
                original = target.read_text(encoding="utf-8")

                def edit():
                    # A comment keeps the size change out of the way: we are
                    # timing the index update, not the write.
                    target.write_text(
                        original + "\n# bench\n", encoding="utf-8"
                    )
                    agent.refresh_file(relative)

                measure("refresh_file (1 edit)", edit)
                facts["edit_refresh"] = relative
                facts["index_survived_refresh"] = agent.project_index is not None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    facts["cache"] = CACHE.stats
    return {"measurements": results, "facts": facts}


def format_report(result: dict) -> str:
    measurements: list[Measurement] = result["measurements"]
    facts = result["facts"]
    width = max(len(m.name) for m in measurements)

    lines = [
        "RUDER-AI 성능 측정",
        "=" * (width + 44),
    ]
    if "files" in facts:
        lines.append(
            f"프로젝트: {facts['files']} 파일 / {facts['symbols']} 심볼 / "
            f"{facts['imports']} import / {facts['tokens']} 토큰"
        )
    if facts.get("cache"):
        lines.append(f"파생 데이터 캐시: {facts['cache']}")
    lines.append("")

    for m in measurements:
        lines.append(m.line(width))

    total = sum(m.best_ms for m in measurements)
    lines.append("-" * (width + 44))
    lines.append(f"{'합계 (best)':<{width}}  {total:8.2f} ms")

    extras = [
        f"refresh_file 후 인덱스 유지: {facts['index_survived_refresh']}"
        for _ in (0,)
        if facts.get("index_survived_refresh") is not None
    ]
    if facts.get("edit_refresh"):
        extras.insert(0, f"편집 대상: {facts['edit_refresh']}")
    if facts.get("signature_error"):
        extras.append(f"signature 측정 실패: {facts['signature_error']}")
    if "edit_refresh" in facts and facts["edit_refresh"] is None:
        extras.append(
            "편집 반영 측정을 건너뜁니다: 측정 대상 .py 파일이 없습니다."
        )
    if extras:
        lines.append("")
        lines.extend(extras)

    return "\n".join(lines) + "\n"
