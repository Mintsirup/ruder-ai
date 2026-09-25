"""RuderAI AI Agent."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ruder_ai.context.builder import ContextBuilder
from ruder_ai.context.formatter import PromptFormatter

from ruder_ai.core.executor import ToolExecutor
from ruder_ai.core.deterministic_actions import DeterministicActionResolver
from ruder_ai.core.autonomy import AutonomyBudget, AutonomyController
from ruder_ai.core.goal_planner import GoalPlanner, PlanTask
from ruder_ai.core.llm import LLMGenerationConfig, OllamaClient
from ruder_ai.core.settings import RuderAISettings
from ruder_ai.core.memory import MemoryManager
from ruder_ai.core.planner import TaskPlanner
from ruder_ai.core.reflection import Reflector
from ruder_ai.agents import (
    AgentOrchestrator,
    ExplorerAgent,
    CoderAgent,
    TesterAgent,
    ReviewerAgent,
    MemoryAgent,
)

from ruder_ai.skills import SkillRegistry

from ruder_ai.indexer.detector import ProjectDetector
from ruder_ai.indexer.scanner import ProjectScanner
from ruder_ai.indexer.symbol_indexer import SymbolIndexer

from ruder_ai.indexer.call_graph import CallGraph
from ruder_ai.indexer.reference_index import (
    ReferenceIndex,
)
from ruder_ai.indexer.semantic_file_index import (
    SemanticFileIndex,
)
from ruder_ai.indexer.type_resolver import (
    TypeResolver,
)




class _ModeAwareLLM:
    """Proxy that preserves the OllamaClient public attributes while adding mode instructions."""

    def __init__(self, inner: OllamaClient, owner: "AIAgent") -> None:
        self._inner = inner
        self._owner = owner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def chat(self, messages, system_instruction=None):
        mode = self._owner.request_mode
        mode_instruction = (
            AIAgent.INSPECT_SYSTEM_PROMPT
            if mode == "inspect"
            else AIAgent.CODING_SYSTEM_PROMPT
        )
        instruction = f"{mode_instruction}\n{system_instruction}" if system_instruction else mode_instruction
        return await self._inner.chat(messages, system_instruction=instruction)

class AIAgent:
    """RuderAI Autonomous Coding Agent."""

    __version__ = "7.5.0"

    CODING_SYSTEM_PROMPT = (
        "[MODE: STRICT_CODE] 실제 프로젝트와 Tool 결과만 근거로 사용하세요. "
        "존재하지 않는 파일, 심볼, 오류, 변경, 검증 결과를 만들어내지 마세요. "
        "수정이 필요할 때만 수정하고, 실제 실행 결과를 성공의 근거로 사용하세요. "
        "Planner/Explorer가 제공한 파일 경로와 Tool 결과를 그대로 우선하며, "
        "일치하지 않으면 추측하지 말고 실제 파일을 다시 읽으세요."
    )
    INSPECT_SYSTEM_PROMPT = (
        "[MODE: READ_ONLY_INSPECT] 분석 전용입니다. 파일을 수정, 생성, 삭제하지 마세요. "
        "실제 프로젝트의 파일 내용과 Explorer 근거만 사용하고 모르는 내용은 추측하지 마세요. "
        "코드상 직접 참조 관계와 런타임 데이터/Transform 관계를 구분해서 설명하세요. "
        "관계를 말할 때는 실제 소스에서 확인된 근거를 함께 사용하세요."
    )

    # GoalPlanner._parse_plan()이 mkdir류 환각 Tool을 tool=None으로
    # 강등시킬 때 description에 붙이는 안내 문구의 일부. 이 마커가
    # 있으면 "표준 디렉토리 구조를 만들려다 실행 가능한 Tool이 없어서
    # 못 만들었다"는 뜻이다 (goal_planner.py::_parse_plan 참고).
    _MKDIR_HONESTY_MARKER = "Tool은 존재하지 않아 실제로 실행되지 않았습니다"

    # 표준 구조로 옮길 때 건드리지 않아야 하는 빌드 설정 파일들
    # (루트에 그대로 있어야 Maven/Gradle이 정상 동작한다).
    _JAVA_BUILD_ROOT_FILES = {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradlew",
        "gradlew.bat",
    }

    def __init__(
        self,
        model_name: Optional[str] = None,
        workspace_path: Optional[str] = None,
        max_steps: int = 5,
        ollama_host: Optional[str] = None,
        enable_reflection: bool = True,
        max_reflections: int = 2,
        llm_timeout: float = OllamaClient.DEFAULT_TIMEOUT,
        num_ctx: int = 16384,
        max_tokens: int = 3072,
        context_token_budget: int = 4352,
        temperature: float = 0.1,
        max_replans: int = 2,
        settings: RuderAISettings | None = None,
    ):

        runtime = settings or RuderAISettings(
            model=model_name or "ruder-ai-agent:latest",
            ollama_host=ollama_host or "http://127.0.0.1:11434",
            temperature=temperature,
            num_ctx=num_ctx,
            max_tokens=max_tokens,
            timeout=llm_timeout,
            context_token_budget=context_token_budget,
            max_steps=max(1, int(max_steps)),
            max_replans=max(0, int(max_replans)),
            max_reflections=max(0, int(max_reflections)),
            enable_reflection=enable_reflection,
        )

        self.settings = runtime
        self.request_mode = "code"
        self.workspace_path = Path(workspace_path or ".").resolve()
        self.deterministic_actions = DeterministicActionResolver(self.workspace_path)
        self.autonomy = AutonomyController(
            self.workspace_path,
            budget=AutonomyBudget(
                max_cycles=runtime.autonomy_max_cycles,
                max_wall_time_seconds=runtime.autonomy_max_wall_time,
                max_failures=runtime.autonomy_max_failures,
                max_file_changes=runtime.autonomy_max_file_changes,
            ),
        )
        self.model_name = runtime.model
        self.ollama_host = runtime.ollama_host
        self.max_steps = runtime.max_steps

        # -------------------------
        # Project State
        # -------------------------

        self.project_index = None
        self._workspace_signature = None

        # -------------------------
        # Analysis Components
        # -------------------------

        self.scanner = ProjectScanner(
            self.workspace_path
        )

        self.detector = ProjectDetector()

        self.indexer = SymbolIndexer()

        self.call_graph = CallGraph()

        self.reference_index = (
            ReferenceIndex()
        )

        self.semantic_file_index = (
            SemanticFileIndex()
        )

        self.type_resolver = (
            TypeResolver()
        )

        # -------------------------
        # Skills
        # -------------------------

        self.skill_registry = (
            SkillRegistry()
        )

        # -------------------------
        # Planner
        # -------------------------

        self.planner = TaskPlanner(
            self.call_graph,
            self.reference_index,
            self.semantic_file_index,
            self.type_resolver,
        )

        # -------------------------
        # Context
        # -------------------------

        self.context_builder = (
            ContextBuilder(
                self.workspace_path,
                token_budget=runtime.context_token_budget,
                max_files=runtime.max_files,
                max_snippets_per_file=runtime.max_snippets_per_file,
            )
        )

        self.prompt_formatter = (
            PromptFormatter()
        )

        # -------------------------
        # LLM
        # -------------------------

        self.llm = OllamaClient(
            model=self.model_name,
            host=self.ollama_host,
            timeout=runtime.timeout,
            generation=LLMGenerationConfig(
                temperature=runtime.temperature,
                num_ctx=runtime.num_ctx,
                max_tokens=runtime.max_tokens,
            ),
        )

        # All role agents/planners share one LLM runtime, but every call receives
        # an explicit mode-specific system instruction through this proxy.
        self.raw_llm = self.llm
        self.llm = _ModeAwareLLM(self.raw_llm, self)

        # -------------------------
        # Memory
        # -------------------------
        # Scratchpad(단기 추론 메모) + Working Memory(현재 작업 상태,
        # 휘발성) + Project Memory/최근 작업 기록(workspace 안의
        # JSON 파일에 영구 저장). GoalPlanner와 Executor가 공유한다.

        self.memory = MemoryManager(
            workspace_path=str(self.workspace_path),
        )

        # -------------------------
        # Goal Planner
        # -------------------------
        # 진짜 의미의 Planner: 사용자 요청 -> Goal + 순서 있는 Task
        # 목록. `self.planner`(TaskPlanner)는 이것과 별개로, 관련
        # 심볼/파일을 찾는 컨텍스트 검색기 역할을 계속 담당한다.

        self.goal_planner = GoalPlanner(
            llm=self.llm,
            skill_registry=self.skill_registry,
            memory=self.memory,
        )

        # -------------------------
        # Reflection (자기 검증)
        # -------------------------

        self.reflector = (
            Reflector(
                llm=self.llm,
                workspace_path=str(
                    self.workspace_path
                ),
                max_reflections=runtime.max_reflections,
            )
            if runtime.enable_reflection
            else None
        )

        # -------------------------
        # Role Agents
        # -------------------------
        # 하나의 LLM runtime을 공유하되 책임을 분리한다.
        self.explorer_agent = ExplorerAgent(self.llm, self.workspace_path)
        self.coder_agent = CoderAgent(self.llm, self.workspace_path)
        self.tester_agent = TesterAgent(self.llm, self.workspace_path)
        self.reviewer_agent = ReviewerAgent(self.llm, self.workspace_path)
        self.memory_agent = MemoryAgent(self.llm, self.workspace_path)

        # -------------------------
        # Executor
        # -------------------------

        self.executor = ToolExecutor(
            llm=self.llm,
            skill_registry=self.skill_registry,
            workspace_path=str(
                self.workspace_path
            ),
            max_steps=max_steps,
            agent=self,
            reflector=self.reflector,
            goal_planner=self.goal_planner,
            memory=self.memory,
        )

        self.agent_orchestrator = AgentOrchestrator(
            explorer=self.explorer_agent,
            coder=self.coder_agent,
            tester=self.tester_agent,
            reviewer=self.reviewer_agent,
            memory_agent=self.memory_agent,
        )

    def _active_system_instruction(self) -> str:
        return (
            self.INSPECT_SYSTEM_PROMPT
            if self.request_mode == "inspect"
            else self.CODING_SYSTEM_PROMPT
        )

    async def _llm_chat(self, messages):
        return await self.llm.chat(messages, system_instruction=self._active_system_instruction())

    def _rebuild_derived_indexes(self) -> None:
        """`call_graph`/`reference_index`/`semantic_file_index`/
        `type_resolver`를 현재 `self.project_index` 기준으로 다시 만든다.

        전체 인덱스를 새로 만들 때(`_build_project_index`)와 파일 하나만
        갱신할 때(`refresh_file`) 모두 이 4개를 같은 순서로 재빌드해야
        해서 중복돼 있던 코드를 여기 하나로 모았다 (TODO.md P2
        "scan/index 중복 제거").
        """

        self.call_graph.build(self.project_index)
        self.reference_index.build(self.project_index)
        self.semantic_file_index.build(self.project_index)
        self.type_resolver.build(self.project_index)

    def _build_project_index(self, force_refresh: bool = False):
        """프로젝트 인덱스를 만들고, 작업공간이 바뀌면 자동으로 갱신한다."""
        current_signature = self._workspace_fs_signature()
        if (
            self.project_index is not None
            and not force_refresh
            and current_signature == self._workspace_signature
        ):
            return self.project_index

        files = self.scanner.scan()
        inverted = self.scanner.inverted_index
        project = self.detector.detect(self.workspace_path, files)

        self.project_index = self.indexer.build(
            workspace=self.workspace_path,
            files=files,
            project=project,
            inverted_index=inverted,
        )

        self._rebuild_derived_indexes()
        self._workspace_signature = current_signature

        return self.project_index

    def _workspace_fs_signature(self):
        """Cheap filesystem signature for cross-turn index invalidation.

        It only stats paths; source contents are not read here. This catches
        files added/removed and files modified between persistent bridge
        requests without rescanning file contents on every unchanged request.
        """
        root = self.workspace_path
        entries = []
        try:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if any(part in self.scanner.IGNORE_DIRS for part in path.parts):
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                entries.append((str(path.relative_to(root)), st.st_size, st.st_mtime_ns))
        except OSError:
            return None
        return tuple(sorted(entries))

    async def process_task(self, prompt: str, mode: str = "code", *, resume: bool = False) -> str:
        # Persistent bridge request-local mode: never let a previous request
        # leave the LLM in the wrong persona.
        self.request_mode = str(mode or "code").strip().lower()
        if self.request_mode not in {"code", "inspect", "autonomous"}:
            self.request_mode = "code"

        if self.request_mode == "code" and self.settings.autonomy_enabled and AutonomyController.should_enable(prompt, self.request_mode):
            self.request_mode = "autonomous"

        if self.request_mode == "autonomous" and not self.settings.autonomy_enabled:
            self.request_mode = "code"

        if self.request_mode == "autonomous":
            async def _run_cycle(*, prompt: str, max_cycles: int = 1):
                current_index = self._build_project_index()
                return await self.agent_orchestrator.run(
                    prompt=prompt,
                    project_index=current_index,
                    task_planner=self.planner,
                    context_builder=self.context_builder,
                    prompt_formatter=self.prompt_formatter,
                    skill_registry=self.skill_registry,
                    goal_planner=self.goal_planner,
                    executor=self.executor,
                    memory=self.memory,
                    auto_plan_fix=self._auto_fix_mkdir_hallucination,
                    max_cycles=max_cycles,
                )
            result = await self.autonomy.run(_run_cycle, prompt=prompt, resume=resume)
            return result

        # The bridge is persistent across requests. Refresh the index whenever
        # workspace files changed since the previous task.
        index = self._build_project_index()

        # Explicit file names are resolved against the live filesystem before
        # planning. This prevents a stale/semantic-only index from making a
        # real file look missing.
        explicit_files = AgentOrchestrator._explicit_file_paths(prompt)
        if explicit_files:
            missing_from_index = [
                f for f in explicit_files
                if not any(Path(item.relative_path).name.lower() == Path(f).name.lower()
                           for item in getattr(index, "files", []))
            ]
            if missing_from_index:
                index = self._build_project_index(force_refresh=True)

        # 명확한 시스템/검증 질의는 LLM Planner를 우회한다.
        if self.request_mode == "code":
            deterministic = await self.deterministic_actions.execute(prompt)
            if deterministic is not None:
                return self._format_deterministic_result(deterministic)

        # 역할 기반 파이프라인: Explorer -> Planner -> Coder/Tester -> Reviewer -> Memory
        return await self.agent_orchestrator.run(
            prompt=prompt,
            project_index=index,
            task_planner=self.planner,
            context_builder=self.context_builder,
            prompt_formatter=self.prompt_formatter,
            skill_registry=self.skill_registry,
            goal_planner=self.goal_planner,
            executor=self.executor,
            memory=self.memory,
            auto_plan_fix=self._auto_fix_mkdir_hallucination,
        )


    @staticmethod
    def _format_deterministic_result(result: dict) -> str:
        import json
        action = result.get("action", "action")
        status = result.get("status", "success")
        if action == "environment_check":
            lines = [
                f"OS: {result.get('os')}",
                f"Python: {result.get('python')}",
                f"Python 버전: {result.get('python_version')}",
                f"작업 디렉터리: {result.get('cwd')}",
                f"RuderAI workspace: {result.get('workspace')}",
            ]
            for name, path in result.get("tools", {}).items():
                lines.append(f"{name}: {path}")
            return "\n".join(lines) + "\n\n[실행 근거] deterministic environment_check"
        if action == "file_discovery":
            lines = [f"{x['requested']} → {x['path'] or '찾지 못함'}" for x in result.get('files', [])]
            return "\n".join(lines) + ""
        if action == "csharp_check":
            return f"[csharp_check] {status.upper()}\n{result.get('result','')}"
        if action == "git_status":
            return f"[git_status] {status.upper()}\n{result.get('result','')}"
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _auto_fix_mkdir_hallucination(self, plan, index):
        """mkdir류 환각 Tool로 실패한 Task를, 실제 move_file Task로
        코드에서 직접 교체한다 (`_MKDIR_HONESTY_MARKER` 참고).

        - Plan에 해당 마커가 붙은 Task가 하나도 없으면 그대로 반환.
        - Java 프로젝트가 아니거나, 옮길 만한 루트 파일이 없으면
          손대지 않고 그대로 반환 (안전한 기본값 = 아무것도 안 함).
        """

        if plan is None or index is None:
            return plan

        has_mkdir_hallucination = any(
            pt.tool is None
            and self._MKDIR_HONESTY_MARKER in pt.description
            for pt in plan.tasks
        )

        if not has_mkdir_hallucination:
            return plan

        if (getattr(index.project, "language", "") or "").lower() != "java":
            return plan

        move_tasks = self._build_java_layout_move_tasks(index)

        if not move_tasks:
            return plan

        kept_tasks = [
            pt for pt in plan.tasks
            if not (
                pt.tool is None
                and self._MKDIR_HONESTY_MARKER in pt.description
            )
        ]

        combined = kept_tasks + move_tasks

        for i, pt in enumerate(combined, start=1):
            pt.order = i

        plan.tasks = combined

        plan.warnings.append(
            "표준 디렉토리 구조 요청이 존재하지 않는 mkdir류 Tool "
            "때문에 반복 실패해서, 실제 move_file 계획을 코드에서 "
            "자동으로 대신 세웠습니다."
        )

        return plan

    def _build_java_layout_move_tasks(self, index) -> list[PlanTask]:
        """workspace 루트에 흩어진 Java 소스/리소스 파일을 표준
        Maven/Gradle 경로(src/main/java/<package>/, src/main/resources/)
        로 옮기는 move_file Task 목록을 만든다.

        이미 서브디렉토리 안에 있는 파일이나 빌드 설정 파일은
        건드리지 않는다.
        """

        tasks: list[PlanTask] = []
        seen_dest: set[str] = set()

        for f in index.files:

            rel = f.relative_path

            if "/" in rel or "\\" in rel:
                continue

            if rel in self._JAVA_BUILD_ROOT_FILES:
                continue

            if f.extension == ".java":
                package = self._resolve_java_package(index, rel)
                pkg_path = package.replace(".", "/") if package else ""
                dest = (
                    f"src/main/java/{pkg_path}/{rel}"
                    if pkg_path
                    else f"src/main/java/{rel}"
                )
            elif f.extension in (".yml", ".yaml", ".properties"):
                dest = f"src/main/resources/{rel}"
            else:
                continue

            if dest in seen_dest:
                continue

            seen_dest.add(dest)

            tasks.append(
                PlanTask(
                    order=0,
                    description=(
                        f"{rel} 파일을 {dest} 경로로 이동합니다 "
                        "(표준 Maven/Gradle 디렉토리 구조)."
                    ),
                    tool="move_file",
                )
            )

        return tasks

    def _resolve_java_package(self, index, relative_path: str) -> str:
        """Symbol 인덱스에서 해당 파일의 package를 찾는다.

        인덱스에 심볼이 없으면(예: 파싱 실패) 파일을 직접 읽어
        `package X.Y.Z;` 선언을 정규식으로 한 번 더 시도한다.
        """

        for symbol in index.symbols:
            if symbol.file == relative_path and symbol.package:
                return symbol.package

        try:
            content = (
                self.workspace_path / relative_path
            ).read_text(encoding="utf-8", errors="replace")
            match = re.search(
                r"^\s*package\s+([\w.]+)\s*;", content, re.MULTILINE,
            )
            if match:
                return match.group(1)
        except Exception:
            pass

        return ""

    def refresh_file(
        self,
        file_path: str,
    ) -> None:
        """
        변경된 파일 하나만 다시 인덱싱한다.
        """

        if self.project_index is None:
            return

        try:

            # -------------------------
            # Symbol Index
            # -------------------------

            self.indexer.update_file(
                self.project_index,
                self.workspace_path / file_path,
            )

            # -------------------------
            # Rebuild Derived Indexes
            # -------------------------

            self._rebuild_derived_indexes()

        except Exception:

            # 실패 시 다음 요청에서
            # 전체 인덱스를 다시 생성한다.

            self.project_index = None

    def invalidate_index(
        self,
    ) -> None:
        """
        전체 프로젝트 인덱스를 무효화한다.
        """

        self.project_index = None

    def rebuild_index(
        self,
    ):
        """
        프로젝트 인덱스를 강제로 다시 생성한다.
        """

        self.project_index = None

        return self._build_project_index()
