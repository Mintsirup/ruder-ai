"""RuderAI Goal Planner.

TODO.md의 "Planner를 진짜 Planner로 변경" 항목 구현.

기존 `core/planner.py`의 `TaskPlanner`는 프롬프트와 관련된 심볼/파일을
점수화해서 골라주는 "컨텍스트 검색기"에 가깝다 (RAG의 retrieval 단계).
그것과는 역할이 다른, 진짜 의미의 Planner를 여기서 구현한다:

- Goal 생성: 사용자 요청을 한 문장의 목표로 정리
- Task 목록 생성: 목표를 달성하기 위한 작업을 순서대로 나열
- 실행 순서 결정: Task는 리스트 순서 = 실행 순서
- Tool은 이름만 선택: Task는 사용할 Tool의 "이름"만 가리킨다
  (실제 kwargs/코드/patch 내용은 Planner가 만들지 않는다 — Executor의 몫)
- 코드 수정 금지: Planner는 파일을 읽거나 쓰지 않고, LLM 호출로
  계획 텍스트(JSON)만 만든다
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class Goal:
    text: str


@dataclass(slots=True)
class PlanTask:
    order: int
    description: str
    tool: str | None = None
    """사용할 Tool 이름. 파일 작업이 필요 없는 순수 판단/설명 Task는 None."""

    kwargs: dict = field(default_factory=dict)
    """결정론적 Task에 사용할 정확한 Tool 인자. 비어 있으면 기존처럼 LLM이 채운다."""

    status: str = "pending"
    """실행 상태: "pending" | "done" | "failed".

    Executor._execute_tasks가 Task를 실행하면서 채운다. Planner 자신은
    항상 "pending" 상태로 새 Task를 만든다 — 이 필드는 어디까지나
    "실제로 무슨 일이 있었는지"를 기록하는 실행 결과이지, Planner가
    미리 정하는 계획의 일부가 아니다.
    """


@dataclass(slots=True)
class Plan:
    goal: Goal
    tasks: list[PlanTask] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def tool_sequence(self) -> list[str]:
        return [t.tool for t in self.tasks if t.tool]


class GoalPlanner:
    """LLM을 이용해 Goal + Task 목록(Plan)을 생성하는 진짜 Planner.

    이 클래스는 절대 파일을 읽거나 쓰지 않는다. 유일하게 하는 일은
    (1) 프롬프트를 만들고 (2) LLM을 호출하고 (3) 응답을 Plan으로
    파싱하는 것뿐이다. 실제 Tool 실행은 Executor의 책임이다.
    """

    _JSON_BLOCK = re.compile(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        re.DOTALL,
    )

    def __init__(self, llm, skill_registry, memory=None):
        self.llm = llm
        self.skill_registry = skill_registry
        self.memory = memory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def plan(
        self,
        prompt: str,
        context_summary: str = "",
    ) -> Plan:
        """사용자 요청으로부터 새 Plan을 생성한다."""
        return await self._plan_with_feedback(
            prompt=prompt,
            context_summary=context_summary,
            feedback=None,
        )

    async def replan(
        self,
        prompt: str,
        previous_plan: Plan,
        failure_log: str,
        context_summary: str = "",
    ) -> Plan:
        """실패 로그(Auto Verify 등)를 받아 Plan을 다시 세운다."""
        feedback = self._format_feedback(
            previous_plan,
            failure_log,
        )
        return await self._plan_with_feedback(
            prompt=prompt,
            context_summary=context_summary,
            feedback=feedback,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _plan_with_feedback(
        self,
        prompt: str,
        context_summary: str,
        feedback: str | None,
    ) -> Plan:

        messages = [
            {
                "role": "system",
                "content": self._system_prompt(),
            },
            {
                "role": "user",
                "content": self._user_prompt(
                    prompt=prompt,
                    context_summary=context_summary,
                    feedback=feedback,
                ),
            },
        ]

        response = await self.llm.chat(messages)

        if (
            not response
            or str(response).lstrip().startswith("[LLM_ERROR]")
        ):
            response = (
                response
                or "[LLM_ERROR] Planner가 빈 응답을 반환했습니다."
            )

        plan = self._parse_plan(prompt, response)

        # 순서가 중요하다.
        # 1) 검색 강제
        # 2) interactive 실행 차단 + scenario 검증 강제
        # 3) mutation 뒤 일반 verify 보장
        plan = self._auto_fix_missing_search(plan, prompt)
        plan = self._guard_interactive_execution(plan, prompt)
        plan = self._ensure_mutation_verification(plan)

        return self._guard_unnecessary_web_search(plan, prompt)

    def _known_tools(self) -> dict[str, str]:

        if self.skill_registry is None:
            return {}

        try:
            return dict(self.skill_registry.list_skills())
        except Exception:
            return {}

    def _system_prompt(self) -> str:

        tools = self._known_tools()

        if tools:
            tool_lines = "\n".join(
                f"- {name}: {description}"
                for name, description in tools.items()
            )
        else:
            tool_lines = "(등록된 Tool 없음)"

        return (
            "당신은 RuderAI(자율 코딩 에이전트)의 Planner입니다.\n\n"
            "당신의 유일한 역할은 사용자 요청을 Goal 하나와, 그 Goal을 "
            "달성하기 위한 순서 있는 Task 목록으로 나누는 것입니다.\n"
            "당신은 절대 코드를 직접 읽거나 쓰지 않습니다 — Tool을 "
            "호출하지 않고, 오직 계획(JSON)만 출력합니다.\n\n"

            "규칙:\n"
            "- 반드시 JSON 객체 하나만 출력하세요 (다른 설명 문장 금지).\n"
            "- \"goal\": 이 작업이 성공했을 때의 결과를 한 문장으로 요약합니다.\n"
            "- \"tasks\": 순서 있는 배열입니다. 배열의 순서가 곧 실행 순서입니다.\n"
            "- 각 task는 \"description\"(한국어로 무엇을/왜)과 "
            "\"tool\"을 가집니다. tool은 아래 목록에 있는 Tool 이름 중 "
            "정확히 하나이거나, Tool 호출이 필요 없는 순수 판단/설명 "
            "task라면 null입니다.\n"
            "- 목록에 없는 Tool 이름을 지어내지 마세요.\n"
            "- 대부분의 Task는 tool 이름만 있으면 됩니다 — kwargs, 실제 코드, "
            "diff, 파일 내용은 절대 포함하지 마세요 — 그건 각 Task를 실제로 "
            "실행할 때 Executor가 결정합니다.\n"
            "- 예외: 목표 파일 경로나 검색어처럼 Task 자체를 특정짓는 정보는 "
            "description에 파일 경로/이름/검색어를 반드시 명시하세요. "
            "Executor가 이 정보를 근거로 정확한 Tool kwargs를 결정합니다.\n"
            "- 요청을 만족하는 데 필요한 만큼만 Task를 만드세요. "
            "불필요하게 잘게 쪼개지 마세요.\n"
            "- Plan은 작업 목록이 아니라 현재 상태에서 다음에 해야 할 일입니다.\n"
            "- 파일/프로젝트를 수정하기 전에 먼저 실제 구조와 파일을 확인하세요.\n"
            "- 이미 성공한 생성/수정/이동을 다시 실행하지 마세요.\n"
            "- 파일 이동/이름 변경 이후에는 새 경로를 기준으로 하세요.\n"
            "- README.md/TODO.md/RUNBOOK.md 같은 문서는 명시적으로 요청받지 않는 한 "
            "생성하거나 수정하지 마세요.\n"

            "- 인사/잡담/일반 질문이면 Task 하나와 tool=null로 답하세요.\n"

            "- 사용자가 '검색해서', '찾아봐', '최신 버전', '지금도 그래?' "
            "등을 요청하면 web_search를 반드시 포함하세요.\n"
            "- web_search 결과만으로 구체적인 사실을 확정하기 어렵다면 "
            "web_fetch도 포함하세요.\n"

            "- 실행 명령이 필요하면 Python 코드는 execute_code를, "
            "OS 셸 명령은 execute_shell을 사용하세요.\n"

            "- 매우 중요: input() 또는 stdin을 기다리는 대화형 CLI/게임/메뉴 "
            "프로그램은 execute_code로 직접 실행하지 마세요.\n"
            "- 그런 프로그램의 실제 검증은 반드시 verify_project를 이용한 "
            "stdin 시나리오 테스트로 계획하세요.\n"
            "- 특히 '추가/조회/완료/삭제/종료'처럼 여러 사용자 입력으로 "
            "동작하는 CLI라면 반드시 실제 입력 시나리오를 검증 단계에 포함하세요.\n"

            "- 코드/파일을 만들거나 수정하는 작업이면 먼저 "
            "list_directory/read_file Task를 계획하세요.\n"

            "- web_search는 프로젝트 내부에서 해결할 수 없는 외부 정보가 "
            "필요할 때만 사용하세요.\n"

            "- 검색 결과가 부족하다고 해서 작업을 포기하지 마세요.\n"

            "- 새 프로젝트/플러그인/패키지를 만들 때 폴더만 만들고 끝내지 마세요. "
            "실제 소스/설정 파일을 생성하세요.\n"

            "- 빈 디렉터리를 만들 때는 create_directory만 사용하세요.\n\n"

            f"# 사용 가능한 Tool\n{tool_lines}\n\n"

            "# 출력 형식\n"
            "```json\n"
            "{\n"
            '  "goal": "...",\n'
            '  "tasks": [\n'
            '    {"description": "...", "tool": "read_file"},\n'
            '    {"description": "...", "tool": null}\n'
            "  ]\n"
            "}\n"
            "```"
        )

    # ------------------------------------------------------------------
    # Greeting
    # ------------------------------------------------------------------

    _GREETING_PATTERNS = (
        "안녕",
        "하이",
        "hi",
        "hello",
        "헬로",
        "ㅎㅇ",
        "반가",
        "고마워",
        "고맙",
        "감사",
        "잘가",
        "수고",
    )

    def _looks_like_greeting(self, prompt: str) -> bool:
        stripped = prompt.strip()

        if len(stripped) > 12:
            return False

        lowered = stripped.lower()

        return any(
            pat in lowered
            for pat in self._GREETING_PATTERNS
        )

    # ------------------------------------------------------------------
    # Explicit search
    # ------------------------------------------------------------------

    _EXPLICIT_SEARCH_PATTERNS = (
        "검색해",
        "검색좀",
        "찾아봐",
        "찾아줘",
        "찾아 줘",
        "웹에서",
        "인터넷에서",
        "구글",
        "google",
        "최신 버전",
        "최신버전",
        "최신 정보",
        "지금도",
        "요즘도",
        "현재 버전",
        "요즘 버전",
        "search",
    )

    def _looks_like_explicit_search_request(
        self,
        prompt: str,
    ) -> bool:
        lowered = prompt.lower()

        return any(
            pat.lower() in lowered
            for pat in self._EXPLICIT_SEARCH_PATTERNS
        )

    def _auto_fix_missing_search(
        self,
        plan: Plan,
        prompt: str,
    ) -> Plan:

        if plan is None:
            return plan

        if not self._looks_like_explicit_search_request(prompt):
            return plan

        known_tools = set(self._known_tools().keys())

        tasks = list(plan.tasks)
        added_warnings: list[str] = []

        if (
            "web_search" in known_tools
            and not any(
                t.tool == "web_search"
                for t in tasks
            )
        ):
            tasks.insert(
                0,
                PlanTask(
                    order=0,
                    description=(
                        f"'{prompt.strip()}' 요청에 답하기 위해 "
                        "웹에서 최신 정보를 검색합니다."
                    ),
                    tool="web_search",
                ),
            )

            added_warnings.append(
                "사용자가 검색을 명시적으로 요청했는데 Planner가 "
                "web_search Task를 만들지 않아 자동 추가했습니다."
            )

        has_web_search = any(
            t.tool == "web_search"
            for t in tasks
        )

        has_web_fetch = any(
            t.tool == "web_fetch"
            for t in tasks
        )

        if (
            has_web_search
            and not has_web_fetch
            and "web_fetch" in known_tools
        ):
            last_search_idx = max(
                i
                for i, t in enumerate(tasks)
                if t.tool == "web_search"
            )

            tasks.insert(
                last_search_idx + 1,
                PlanTask(
                    order=0,
                    description=(
                        "web_search 결과 중 가장 관련성 높은 URL 하나를 "
                        "열어 본문을 읽습니다."
                    ),
                    tool="web_fetch",
                ),
            )

            added_warnings.append(
                "web_search 뒤에 web_fetch Task를 자동 추가했습니다."
            )

        for i, task in enumerate(tasks, start=1):
            task.order = i

        plan.tasks = tasks
        plan.warnings.extend(added_warnings)

        return plan

    # ------------------------------------------------------------------
    # Coding tool guards
    # ------------------------------------------------------------------

    _LOCAL_CODING_TOOLS = frozenset(
        {
            "read_file",
            "list_directory",
            "write_file",
            "patch_file",
            "delete_file",
            "move_file",
            "append_file",
            "create_directory",
            "search_symbol",
            "search_reference",
            "semantic_search",
            "git_diff",
            "git_status",
            "git_checkout",
            "git_command",
            "verify_project",
            "generate_diff",
            "apply_patch",
            "rollback_patch",
            "preview_patch",
            "backup_file",
            "restore_backup",
        }
    )

    _MUTATING_TOOLS = frozenset(
        {
            "write_file",
            "patch_file",
            "append_file",
            "delete_file",
            "move_file",
            "apply_patch",
            "rollback_patch",
        }
    )

    # ------------------------------------------------------------------
    # Interactive execution guard
    # ------------------------------------------------------------------

    def _guard_interactive_execution(
        self,
        plan: Plan,
        prompt: str,
    ) -> Plan:
        """대화형 CLI/메뉴 프로그램의 실행 검증을 보정한다.

        핵심 규칙:
        1. execute_code로 interactive 프로그램을 실행하지 않는다.
        2. execute_code가 처음부터 없더라도 사용자가 실제 실행/테스트/
           검증을 요구했다면 verify_project를 반드시 추가한다.
        3. TODO CLI처럼 요구사항이 명확한 경우 결정론적인 stdin
           시나리오를 verify_project kwargs에 넣는다.
        """

        if plan is None:
            return plan

        lowered = (prompt or "").lower()

        # 실행/검증 관련 의도
        verification_words = (
            "검증",
            "테스트",
            "실행",
            "동작",
        )

        has_verification_intent = any(
            word in lowered
            for word in verification_words
        )

        # 대화형/메뉴 관련 의도
        interactive_words = (
            "cli",
            "대화형",
            "입력",
            "메뉴",
            "시나리오",
            "stdin",
            "사용자 입력",
            "실제로 실행",
            "실제 실행",
            "실행해서 검증",
            "실행하여 검증",
            "실행해 검증",
            "실제 동작",
            "실제 동작하도록",
            "기능 테스트",
            "기능을 테스트",
            "테스트까지",
        )

        has_interactive_intent = any(
            word in lowered
            for word in interactive_words
        )

        # 이미 execute_code가 있는 경우도 무조건 차단.
        has_execute_code = any(
            task.tool == "execute_code"
            for task in plan.tasks
        )

        # execute_code가 있는데 interactive 코드일 가능성이 높으면
        # verification intent 여부와 상관없이 제거 대상으로 본다.
        if not has_interactive_intent and not (
            has_execute_code and has_verification_intent
        ):
            return plan

        # ------------------------------------------------------------------
        # 실제 Python entry point 찾기
        # ------------------------------------------------------------------

        mentioned = [
            name
            for name in self._mentioned_file_names(prompt)
            if name.lower().endswith(".py")
        ]

        entry = mentioned[0] if mentioned else None

        if entry is None:
            for task in plan.tasks:
                if task.tool != "read_file":
                    continue

                candidate = re.search(
                    r"['\"]([^'\"]+\.py)['\"]",
                    task.description,
                    flags=re.IGNORECASE,
                )

                if candidate:
                    entry = candidate.group(1)
                    break

        # Python 파일 자체가 명시되어 있지 않더라도
        # execute_code는 제거할 수 있다.
        if has_execute_code and (
            has_interactive_intent
            or has_verification_intent
        ):
            plan.tasks = [
                task
                for task in plan.tasks
                if task.tool != "execute_code"
            ]

        # 진입점이 아예 없다면 일반 verify_project만 추가.
        if entry is None:
            if has_interactive_intent or has_verification_intent:
                verify_task = next(
                    (
                        task
                        for task in plan.tasks
                        if task.tool == "verify_project"
                    ),
                    None,
                )

                if verify_task is None:
                    verify_task = PlanTask(
                        order=len(plan.tasks) + 1,
                        description=(
                            "프로그램을 실제 실행 검증하여 요청된 기능이 "
                            "동작하는지 확인합니다."
                        ),
                        tool="verify_project",
                    )
                    plan.tasks.append(verify_task)

                plan.warnings.append(
                    "실행/검증 요청을 감지하여 verify_project를 "
                    "자동으로 추가했습니다."
                )

                for i, task in enumerate(
                    plan.tasks,
                    start=1,
                ):
                    task.order = i

            return plan

        # ------------------------------------------------------------------
        # verify_project 확보
        # ------------------------------------------------------------------

        verify_task = next(
            (
                task
                for task in plan.tasks
                if task.tool == "verify_project"
            ),
            None,
        )

        if verify_task is None:
            verify_task = PlanTask(
                order=len(plan.tasks) + 1,
                description=(
                    "대화형 프로그램을 실제 입력 시나리오로 실행하여 "
                    "요구사항을 검증합니다."
                ),
                tool="verify_project",
            )
            plan.tasks.append(verify_task)

        # ------------------------------------------------------------------
        # TODO CLI 시나리오
        # ------------------------------------------------------------------

        todo_tokens = (
            "추가",
            "조회",
            "목록",
            "완료",
            "삭제",
            "종료",
        )

        is_todo_cli_request = all(
            token in lowered
            for token in todo_tokens
        )

        if is_todo_cli_request:
            scenario_input = (
                "1\n"
                "테스트 할 일 A\n"
                "1\n"
                "테스트 할 일 B\n"
                "3\n"
                "1\n"
                "4\n"
                "2\n"
                "2\n"
                "4\n"
                "5\n"
            )

            scenario_expected = [
                "할 일이 추가되었습니다: 테스트 할 일 A",
                "할 일이 추가되었습니다: 테스트 할 일 B",
                "할 일이 완료되었습니다: 테스트 할 일 A",
                "1. 테스트 할 일 A [완료]",
                "2. 테스트 할 일 B [미완료]",
                "할 일이 삭제되었습니다: 테스트 할 일 B",
            ]

            verify_task.kwargs.update(
                {
                    "target_files": [entry],
                    "scenario_entry": entry,
                    "scenario_input": scenario_input,
                    "scenario_expected": scenario_expected,
                }
            )

            verify_task.description = (
                "TODO CLI를 실제 stdin 시나리오로 실행하여 "
                "추가/조회/완료/삭제/종료 기능을 모두 검증합니다."
            )

            plan.warnings.append(
                "TODO CLI 요청을 감지하여 execute_code 대신 "
                "python_scenario_test를 사용하는 verify_project로 보정했습니다."
            )

        else:
            verify_task.kwargs.setdefault(
                "target_files",
                [entry],
            )

            verify_task.kwargs.setdefault(
                "scenario_entry",
                entry,
            )

            verify_task.description = (
                "대화형 프로그램의 실행 검증을 "
                "verify_project로 수행합니다."
            )

            plan.warnings.append(
                "대화형 프로그램의 직접 execute_code 실행을 제거하고 "
                "verify_project 검증 단계로 보정했습니다."
            )

        # ------------------------------------------------------------------
        # Task 순서 정리
        # ------------------------------------------------------------------

        for index, task in enumerate(
            plan.tasks,
            start=1,
        ):
            task.order = index

        return plan

    # ------------------------------------------------------------------
    # Mutation verification
    # ------------------------------------------------------------------

    def _ensure_mutation_verification(
        self,
        plan: Plan,
    ) -> Plan:
        """파일 변경 뒤 verify_project가 없으면 자동 추가한다."""

        if plan is None:
            return plan

        known_tools = set(
            self._known_tools().keys()
        )

        if "verify_project" not in known_tools:
            return plan

        if not any(
            task.tool in self._MUTATING_TOOLS
            for task in plan.tasks
        ):
            return plan

        if any(
            task.tool == "verify_project"
            for task in plan.tasks
        ):
            return plan

        plan.tasks.append(
            PlanTask(
                order=len(plan.tasks) + 1,
                description=(
                    "실제 파일 변경이 끝났으므로 프로젝트 검증을 실행합니다."
                ),
                tool="verify_project",
            )
        )

        plan.warnings.append(
            "파일 변경 Task 뒤에 검증 단계가 없어 "
            "verify_project를 자동으로 추가했습니다."
        )

        return plan

    # ------------------------------------------------------------------
    # Web search guard
    # ------------------------------------------------------------------

    def _guard_unnecessary_web_search(
        self,
        plan: Plan,
        prompt: str,
    ) -> Plan:

        if plan is None:
            return plan

        if self._looks_like_explicit_search_request(prompt):
            return plan

        tasks = list(plan.tasks)

        removed = [
            task
            for task in tasks
            if task.tool in (
                "web_search",
                "web_fetch",
            )
        ]

        if not removed:
            return plan

        is_coding_task = any(
            task.tool in self._LOCAL_CODING_TOOLS
            for task in tasks
        )

        if not is_coding_task:
            return plan

        new_tasks = [
            task
            for task in tasks
            if task.tool not in (
                "web_search",
                "web_fetch",
            )
        ]

        if not new_tasks:
            return plan

        for i, task in enumerate(
            new_tasks,
            start=1,
        ):
            task.order = i

        plan.tasks = new_tasks

        plan.warnings.append(
            "코딩 작업에 외부 검색이 필요하지 않은 것으로 판단해 "
            f"web_search/web_fetch Task {len(removed)}개를 제거했습니다."
        )

        return plan

    # ------------------------------------------------------------------
    # mkdir normalization
    # ------------------------------------------------------------------

    _MKDIR_PATTERNS = (
        "mkdir",
        "make_dir",
        "makedir",
        "create_dir",
        "create_directory",
        "create_folder",
        "new_dir",
        "new_folder",
    )

    def _looks_like_mkdir(
        self,
        tool_name: str,
    ) -> bool:
        lowered = tool_name.strip().lower()

        return any(
            pat in lowered
            for pat in self._MKDIR_PATTERNS
        )

    # ------------------------------------------------------------------
    # User prompt
    # ------------------------------------------------------------------

    def _user_prompt(
        self,
        prompt: str,
        context_summary: str,
        feedback: str | None,
    ) -> str:

        parts = []

        if not self._looks_like_greeting(prompt):
            history = self._recent_history_summary()

            if history:
                parts.append(
                    "# 최근 작업 기록 (과거 기록 — 참고용, 절대 지금 할 일이 아님)\n"
                    "아래는 이전에 이미 끝난 작업들의 기록입니다. "
                    "지금 사용자 요청과 관련이 있을 때만 배경지식으로 참고하고, "
                    "관련 없으면 완전히 무시하세요.\n"
                    f"{history}"
                )

        if context_summary:
            parts.append(
                f"# 프로젝트 컨텍스트\n{context_summary}"
            )

        parts.append(
            f"# 사용자 요청\n{prompt}"
        )

        if feedback:
            parts.append(feedback)

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    def _recent_history_summary(self) -> str:

        if self.memory is None:
            return ""

        try:
            return self.memory.recent_history_summary()
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    _STATUS_LABEL = {
        "done": "완료",
        "failed": "실패",
        "pending": "미실행",
    }

    def _format_feedback(
        self,
        previous_plan: Plan,
        failure_log: str,
    ) -> str:

        task_lines = "\n".join(
            f"{task.order}. [{task.tool or '-'}] "
            f"({self._STATUS_LABEL.get(task.status, '미실행')}) "
            f"{task.description}"
            for task in previous_plan.tasks
        ) or "(없음)"

        done_count = sum(
            1
            for task in previous_plan.tasks
            if task.status == "done"
        )

        return (
            "# 이전 Plan (검증 실패)\n"
            f"Goal: {previous_plan.goal.text}\n"
            f"{task_lines}\n\n"
            "# Verify 실패 로그\n"
            f"{failure_log}\n\n"
            f"위 Plan을 실행했고, 그중 {done_count}개 Task는 이미 "
            "'완료'로 실제 실행되어 성공했습니다. 새 Plan에서는 "
            "완료된 Task를 다시 나열하거나 재실행하지 마세요. "
            "현재 파일 상태를 새로운 기준으로 사용하세요. "
            "실패/미실행 Task만 최소한으로 수정 또는 대체하세요."
        )

    # ------------------------------------------------------------------
    # Plan parser
    # ------------------------------------------------------------------

    def _parse_plan(
        self,
        prompt: str,
        response: str,
    ) -> Plan:

        raw = (response or "").strip()

        match = self._JSON_BLOCK.search(raw)

        payload_text = (
            match.group(1)
            if match
            else raw
        )

        try:
            data = json.loads(payload_text)
        except Exception:
            return self._fallback_plan(
                prompt,
                warning=(
                    "Planner 응답을 JSON으로 파싱하지 못해 "
                    "결정론적 복구 Plan으로 전환했습니다."
                ),
            )

        if not isinstance(data, dict):
            return self._fallback_plan(
                prompt,
                warning=(
                    "Planner 응답이 JSON 객체가 아니라 "
                    "결정론적 복구 Plan으로 전환했습니다."
                ),
            )

        goal_text = (
            str(data.get("goal") or prompt).strip()
            or prompt.strip()
        )

        raw_tasks = data.get("tasks")

        if not isinstance(raw_tasks, list) or not raw_tasks:
            return self._fallback_plan(
                prompt,
                goal_text=goal_text,
                warning=(
                    "Planner가 Task를 생성하지 않아 "
                    "결정론적 복구 Plan으로 전환했습니다."
                ),
            )

        known_tools = set(
            self._known_tools().keys()
        )

        tasks: list[PlanTask] = []
        warnings: list[str] = []

        for item in raw_tasks:

            if not isinstance(item, dict):
                continue

            description = str(
                item.get("description") or ""
            ).strip()

            if not description:
                continue

            tool = item.get("tool")

            if tool is not None:

                tool = str(tool).strip()

                if not tool or tool.lower() == "null":
                    tool = None

                elif known_tools and tool not in known_tools:

                    if self._looks_like_mkdir(tool):

                        if tool != "create_directory":
                            warnings.append(
                                f"Tool 이름 '{tool}'을(를) "
                                "'create_directory'로 통일했습니다."
                            )

                        tool = "create_directory"

                    else:
                        warnings.append(
                            f"알 수 없는 Tool '{tool}'을(를) "
                            "무시했습니다."
                        )
                        tool = None

            tasks.append(
                PlanTask(
                    order=len(tasks) + 1,
                    description=description,
                    tool=tool,
                )
            )

        if not tasks:
            benign = self._looks_like_greeting(prompt)

            return self._fallback_plan(
                prompt,
                goal_text=goal_text,
                warning=(
                    "Planner가 인사/잡담에 대해 유효한 Task를 만들지 "
                    "않아 원본 요청을 단일 Task로 처리했습니다."
                    if benign
                    else
                    "Planner가 유효한 Task를 생성하지 않아 "
                    "단일 Task로 폴백했습니다."
                ),
            )

        return Plan(
            goal=Goal(text=goal_text),
            tasks=tasks,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Fallback detection
    # ------------------------------------------------------------------

    _VERIFY_WORDS = (
        "검증",
        "테스트",
        "빌드",
        "컴파일",
        "compile",
        "verify",
        "test",
    )

    _MUTATE_WORDS = (
        "수정",
        "변경",
        "고쳐",
        "고쳐줘",
        "수리",
        "fix",
        "수정해",
        "만들",
        "생성",
        "추가",
        "삭제",
        "리팩토링",
        "refactor",
    )

    _READ_WORDS = (
        "읽",
        "분석",
        "설명",
        "찾아",
        "확인",
        "read",
        "analy",
        "review",
    )

    @staticmethod
    def _mentioned_file_names(
        prompt: str,
    ) -> list[str]:
        """사용자가 명시한 실제 파일 경로를 보수적으로 추출한다."""

        text = prompt or ""

        matches = re.findall(
            r"(?<![A-Za-z0-9_./\\-])"
            r"(?:[A-Za-z0-9_.\\/-]+\."
            r"(?:cs|py|js|ts|tsx|jsx|java|kt|kts|rs|go|cpp|cc|c|h|hpp|"
            r"json|yaml|yml|toml|xml|md|txt))"
            r"(?![A-Za-z0-9_./\\-])",
            text,
            flags=re.IGNORECASE,
        )

        out: list[str] = []

        for item in matches:
            normalized = item.replace("\\", "/")

            if normalized not in out:
                out.append(normalized)

        return out[:5]

    # ------------------------------------------------------------------
    # Fallback planner
    # ------------------------------------------------------------------

    def _fallback_plan_tools(
        self,
        prompt: str,
    ) -> list[tuple[str | None, str]]:

        known = set(
            self._known_tools().keys()
        )

        lowered = (prompt or "").lower()

        mutate = any(
            word.lower() in lowered
            for word in self._MUTATE_WORDS
        )

        verify = any(
            word.lower() in lowered
            for word in self._VERIFY_WORDS
        )

        read = (
            any(
                word.lower() in lowered
                for word in self._READ_WORDS
            )
            or mutate
        )

        mentioned_files = self._mentioned_file_names(
            prompt
        )

        steps: list[tuple[str | None, str]] = []

        if read:

            if mentioned_files and "read_file" in known:

                for file_name in mentioned_files:
                    steps.append(
                        (
                            "read_file",
                            (
                                f"사용자가 명시한 실제 파일 "
                                f"'{file_name}'의 현재 내용을 읽어 "
                                "사실을 확인합니다."
                            ),
                        )
                    )

            elif "list_directory" in known:
                steps.append(
                    (
                        "list_directory",
                        "실제 프로젝트 구조와 대상 파일을 먼저 확인합니다.",
                    )
                )

            elif "read_file" in known:
                steps.append(
                    (
                        "read_file",
                        "실제 프로젝트에서 요청 대상 파일을 확인합니다.",
                    )
                )

        if mutate:
            tool = next(
                (
                    name
                    for name in (
                        "patch_file",
                        "apply_patch",
                        "write_file",
                    )
                    if name in known
                ),
                None,
            )

            if tool:
                steps.append(
                    (
                        tool,
                        (
                            "요청된 변경을 실제 확인된 코드에만 "
                            "적용합니다. 앞서 읽은 실제 파일의 "
                            "내용만 근거로 수정하세요."
                        ),
                    )
                )

        if verify and "verify_project" in known:
            steps.append(
                (
                    "verify_project",
                    (
                        "변경 여부와 관계없이 사용자가 요청한 "
                        "컴파일·테스트·빌드 검증을 독립적으로 "
                        "실행합니다."
                    ),
                )
            )

        if not steps:
            steps.append(
                (
                    None,
                    prompt.strip()
                    or "요청을 직접 답변합니다.",
                )
            )

        return steps

    def _fallback_plan(
        self,
        prompt: str,
        goal_text: str | None = None,
        warning: str = "",
    ) -> Plan:

        tasks = [
            PlanTask(
                order=index,
                description=description,
                tool=tool,
            )
            for index, (tool, description) in enumerate(
                self._fallback_plan_tools(prompt),
                start=1,
            )
        ]

        return Plan(
            goal=Goal(
                text=(goal_text or prompt).strip()
            ),
            tasks=tasks,
            warnings=[warning] if warning else [],
        )