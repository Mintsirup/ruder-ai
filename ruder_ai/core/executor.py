"""RuderAI Tool Executor."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect
import json
import re
import uuid
from pathlib import Path
from typing import Any

from ruder_ai.core.goal_planner import Goal
from ruder_ai.core.goal_planner import Plan as GoalPlan
from ruder_ai.core.ipc import LaunchCoreClient
from ruder_ai.agents.permissions import check_tool_permission
from ruder_ai.core.tool_resolver import ToolResolver
from ruder_ai.core.file_resolver import FileResolver
from ruder_ai.core.failure_policy import FailurePolicy
from ruder_ai.core.execution import ExecutionContext, ExecutionResult, ExecutionTaskRecord
from ruder_ai.core.telemetry import (
    END_OF_TOKEN,
    END_OF_TOKEN_EVENT,
    JsonlExecutionLogger,
    MetricsRegistry,
)
from ruder_ai.core.turn_intent import (
    MUTATION_REFUSED_MESSAGE,
    classify_turn,
)
from ruder_ai.core.checkpoint import FileSnapshotStore
import time

from ruder_ai.core import executor_constants
from ruder_ai.core.executor_guards import GuardsMixin
from ruder_ai.core.executor_errors import ErrorsMixin
from ruder_ai.core.executor_tracking import TrackingMixin
from ruder_ai.core.executor_prompts import PromptsMixin


class ToolExecutor(GuardsMixin, ErrorsMixin, TrackingMixin, PromptsMixin):
    """LLM Tool Calling Executor."""


    TOOL_PATTERN = re.compile(
        r"```json\s*(\{.*?\})\s*```",
        re.DOTALL,
    )

    # TOOL_PATTERN이 실패했을 때(모델이 ```json 태그를 빼먹거나 코드펜스
    # 자체를 안 쓰는 경우) 마지막으로 시도하는 완화된 폴백 패턴들.
    # 1) 태그 없는 코드펜스, 2) 응답에 등장하는 첫 번째 { ... } 블록
    # (non-greedy이므로 중첩 객체가 있으면 실패할 수 있지만, 완전히
    # 못 뽑는 것보다는 낫다).
    _TOOL_PATTERN_FALLBACKS = (
        re.compile(r"```\s*(\{.*?\})\s*```", re.DOTALL),
        re.compile(r"(\{.*\})", re.DOTALL),
    )

    # 변경된 것으로 취급해 인덱스를 갱신 + reflection 대상에 넣는 tool들
    FILE_MUTATING_TOOLS = {
        "write_file",
        "append_file",
        "patch_file",
        "delete_file",
        "move_file",
        "apply_patch",
    }

    # Planner가 "파일 검색" 같은 추상적인 Task를 잘못 해석해 참조용
    # 문서(README/TASK_INDEX/RUNBOOK 등)를 직접 고쳐버리는 사고가
    # 실제로 있었다. 이런 reference-only 파일은 기본적으로 read-only로
    # 취급한다 — 단, 사용자가 원래 요청(task 원문)에서 그 파일을
    # 명시적으로 언급했다면(즉 정말로 그 파일을 고쳐달라고 한 경우)
    # 예외로 허용한다. 파일 basename 기준 대소문자 무시 비교.
    PROTECTED_FILES = {
        "readme.md",
        "task_index.md",
        "runbook.md",
        "todo.md",
        "license",
        "license.md",
        "changelog.md",
    }

    # 위 kwargs 키 중 하나라도 있으면 그 값이 대상 파일 경로다.
    _PATH_KWARG_NAMES = ("file_path", "path")

    # Tool Timeout: 대부분의 Tool(예: git_ops처럼 자체 timeout이 없어
    # 네트워크/프롬프트 대기 등으로 무한정 멈출 수 있는 것들)을 위한
    # 안전망 기본값(초).
    DEFAULT_TOOL_TIMEOUT: float | None = 120.0

    # verify_project처럼 이미 내부적으로 각 빌드/테스트 명령마다 자체
    # timeout(최대 900초, `verify/runners.py` 참고)을 관리하는 Tool은
    # 여기서 굳이 더 짧게 한 번 더 자르면 오히려 정상적인 빌드가
    # 중간에 잘릴 수 있다. 그런 Tool은 여기서 예외로 두고(값 None =
    # Executor 레벨 timeout 없음) 내부 관리에 맡긴다.
    TOOL_TIMEOUT_OVERRIDES: dict[str, float | None] = {
        "verify_project": None,
    }

    # Tool Retry: Tool 실행이 인프라성 실패("error" — 예외, timeout
    # 등)로 끝났을 때, 재계획(replan)까지 가기 전에 같은 kwargs로
    # 몇 번 더 시도해볼지. 재시도 사이에는 backoff만큼 대기한다
    # (시도 횟수에 비례해 선형 증가).
    DEFAULT_TOOL_RETRIES: int = 2
    TOOL_RETRY_BACKOFF_SECONDS: float = 0.5

    # verify_project는 한 번 실행에 최대 900초까지 걸릴 수 있어
    # (`verify/runners.py`), 인프라성 실패라도 맹목적으로 재시도하면
    # 비용이 너무 크다. 그런 경우는 재시도 없이 바로 Retry Loop의
    # replan(더 나은 판단이 필요한 실패)으로 넘기는 게 낫다.
    TOOL_RETRY_OVERRIDES: dict[str, int] = {
        "verify_project": 0,
    }

    # Tool Error Classification: status == "error" 결과를 대략적인
    # 유형으로 분류한다. 목적은 두 가지 —
    # 1) Tool Retry가 재시도해도 결과가 바뀌지 않을 결정적 에러(파일
    #    없음/권한 없음/잘못된 kwargs)에 재시도 예산을 낭비하지 않게
    #    하고,
    # 2) Planner에게 재계획을 요청할 때(_format_task_failure) 더
    #    구체적인 실패 맥락(오류 유형)을 제공하기 위함이다.
    # 실제 값은 executor_constants에 있고(분리된 errors mixin의
    # @staticmethod 분류기들이 self 없이 읽을 수 있도록), 여기서는
    # ToolExecutor.ERROR_TYPE_* 공개 API를 유지하기 위해 재노출한다.
    ERROR_TYPE_TIMEOUT = executor_constants.ERROR_TYPE_TIMEOUT
    ERROR_TYPE_NOT_FOUND = executor_constants.ERROR_TYPE_NOT_FOUND
    ERROR_TYPE_PERMISSION = executor_constants.ERROR_TYPE_PERMISSION
    ERROR_TYPE_VALIDATION = executor_constants.ERROR_TYPE_VALIDATION
    ERROR_TYPE_NETWORK = executor_constants.ERROR_TYPE_NETWORK
    ERROR_TYPE_UNKNOWN = executor_constants.ERROR_TYPE_UNKNOWN
    NON_RETRYABLE_ERROR_TYPES = executor_constants.NON_RETRYABLE_ERROR_TYPES

    def __init__(
        self,
        llm,
        skill_registry,
        workspace_path,
        max_steps: int = 5,
        agent=None,
        reflector=None,
        launchcore: LaunchCoreClient | None = None,
        goal_planner=None,
        max_replans: int = 2,
        memory=None,
        tool_timeout: float | None = DEFAULT_TOOL_TIMEOUT,
        tool_timeout_overrides: dict[str, float | None] | None = None,
        tool_retries: int = DEFAULT_TOOL_RETRIES,
        tool_retry_overrides: dict[str, int] | None = None,
        tool_retry_backoff: float = TOOL_RETRY_BACKOFF_SECONDS,
    ):
        self.llm = llm
        self.skill_registry = skill_registry
        self.workspace_path = workspace_path
        self.max_steps = max_steps
        self.agent = agent
        self.reflector = reflector
        self.launchcore = launchcore or LaunchCoreClient()

        # Plan 실행 중 Tool이 실패했을 때 Planner에게 재계획을 요청하기
        # 위한 참조. None이면 실패 시 재계획 없이 바로 실패를 반환한다.
        self.goal_planner = goal_planner
        self.max_replans = max_replans

        # MemoryManager(core/memory.py). 주어지면 Task 실행 과정을
        # Scratchpad에 기록하고, 작업 완료 시 최근 작업 기록에 남긴다.
        # None이면 기존과 동일하게 동작한다 (하위 호환).
        self.memory = memory

        # Tool Timeout: 모든 Tool 실행에 적용할 기본 제한 시간(초).
        # None이면 Executor 레벨 timeout을 아예 걸지 않는다.
        self.tool_timeout = tool_timeout

        # Tool별 개별 제한(위 기본값을 덮어씀). 클래스 기본
        # TOOL_TIMEOUT_OVERRIDES를 먼저 깔고, 생성자로 받은 값으로
        # 덮어써서 호출자가 특정 Tool만 조정할 수 있게 한다.
        self.tool_timeout_overrides: dict[str, float | None] = dict(
            self.TOOL_TIMEOUT_OVERRIDES
        )
        if tool_timeout_overrides:
            self.tool_timeout_overrides.update(tool_timeout_overrides)

        # Tool Retry: 인프라성 실패("error")를 몇 번 더 재시도할지와,
        # 재시도 사이 대기 시간(초). Tool별 override는 timeout과
        # 동일한 방식으로 클래스 기본값 위에 덮어쓴다.
        self.tool_retries = tool_retries

        # 세션(대화) 전체 동안 유지되는 web_search/web_fetch 결과.
        # run()의 로컬 search_results(턴마다 새로 []로 초기화됨)와는
        # 별개 — _enforce_web_fetch_url이 "이 URL이 실제로 검색된
        # 적 있는지" 판단할 때 이번 턴 검색 결과만 보면, 사용자가
        # 이전 턴 맥락을 이어받아 짧게 되물었을 때(예: "그거 다시
        # 검색해서 몇 버전인지 알려줘") 모델이 대화 기록을 보고 이전
        # 턴의 올바른 URL을 정확히 재사용해도, 이번 턴엔 그 URL이
        # 아직 한 번도 검색된 적 없어서 "지어낸 URL"로 오판하고 엉뚱한
        # URL로 덮어쓰는 문제가 실제로 있었다(재현됨). 세션 전체 기록을
        # 봐야 이런 오판을 막을 수 있다. 세션이 아주 길어질 경우를
        # 대비해 최근 N개만 유지한다.
        self._session_search_results: list[dict[str, Any]] = []
        self.tool_retry_backoff = tool_retry_backoff
        self.tool_retry_overrides: dict[str, int] = dict(
            self.TOOL_RETRY_OVERRIDES
        )
        if tool_retry_overrides:
            self.tool_retry_overrides.update(tool_retry_overrides)

        # PROTECTED_FILES guard용 — run() 호출 전에 _execute_tool을
        # 직접 부르는 테스트 등에서도 AttributeError 없이 동작하도록
        # 빈 문자열로 초기화해둔다 (run()이 실제 task로 덮어씀).
        self._current_task: str = ""
        self._last_failure_context: str = ""
        self.last_changed_files: list[str] = []
        self.last_verification_summary: str = ""
        self.last_verification_result: dict[str, Any] = {}
        self.execution_context = ExecutionContext(workspace=str(workspace_path))
        self.last_execution_result = ExecutionResult(success=False)
        self.telemetry = JsonlExecutionLogger(workspace_path)
        self.metrics = MetricsRegistry()
        self.snapshot_store = FileSnapshotStore(workspace_path)
        self._last_mutation_snapshot: dict[str, Any] | None = None
        # 자유 루프(_run_step_loop)의 현재 스텝 레코드. 동일 인스턴스를
        # 여러 코루틴이 절대 동시에 실행하지 않으므로(역할 파이프라인은
        # 순차 호출) 인스턴스 필드로 충분하다.
        self._current_step_record: ExecutionTaskRecord | None = None
        # 직전 Tool 실행 결과(_refresh_index가 성공 여부를 판단할 때 사용).
        self._last_tool_result: dict[str, Any] = {}
        # Role-based Tool authorization. None keeps legacy direct-Executor
        # callers unrestricted; role agents pass their explicit role name.
        self._active_role: str | None = None
        self._active_project_root: str | None = None
        self.tool_resolver = ToolResolver(
            skill_registry, workspace_path,
            project=getattr(getattr(agent, "project_index", None), "project", None),
        )

    async def run(
        self,
        messages: list[dict[str, str]],
        task: str | None = None,
        plan=None,
        role: str | None = None,
    ) -> str:
        """대화를 실행하고 최종 답변을 반환한다.

        plan(core.goal_planner.Plan)이 주어지면 첫 라운드는 Plan의 Task를
        순서대로 실행한다 (Executor는 Task 순서/구성을 바꾸지 않는다).

        리플렉션(자기 검증)이 실패하면:
        - self.goal_planner가 있으면, Auto Verify 실패와 동일한 경로로
          그 피드백을 `GoalPlanner.replan()`에 넘겨 새 Plan을 받고,
          그 Plan을 다시 `_run_plan`으로 실행한다 (최대 max_reflections회).
          즉 Reflection 결과도 Auto Verify 실패 로그와 마찬가지로
          Planner의 입력이 된다.
        - self.goal_planner가 없으면 기존과 동일하게, 피드백을 대화에
          주입하고 자유 Tool 호출 루프(_run_step_loop)로 이어서
          진행한다 (하위 호환).
        plan이 없으면 기존과 동일하게 자유 Tool 호출 루프로 시작한다.
        """

        # PROTECTED_FILES guard가 "사용자가 이 파일을 명시적으로
        # 언급했는지" 판단할 때 쓸 원본 요청 텍스트. _execute_tool은
        # run()보다 훨씬 아래(Task 실행 시점)에서 호출되므로 인스턴스에
        # 저장해둔다.
        self._current_task = task or ""
        self._last_failure_context = ""
        # If the user explicitly names a file in another project, that explicit
        # target takes precedence over the previous session scope. This keeps the
        # scope guard safe without making it sticky across intentional project switches.
        explicit_targets = self._extract_explicit_file_paths(self._current_task)
        for explicit in explicit_targets:
            inferred = self._infer_project_root(explicit)
            if inferred:
                self._active_project_root = inferred
                break
        self.last_changed_files = []
        self.last_verification_summary = ""
        self.last_verification_result = {}
        self._last_tool_result = {}
        self.execution_context = ExecutionContext(request=self._current_task, workspace=str(self.workspace_path), role=role)
        self.last_execution_result = ExecutionResult(success=False)
        self._current_step_record = None
        self.metrics.task_start()
        self.telemetry.log("task_start", request=self._current_task, role=role)

        # 새 작업 시작: Working Memory/Scratchpad를 초기화한다.
        # (memory가 없으면 아무 일도 하지 않음 — 하위 호환)
        self.execution_context.role = role
        self.execution_context.request = task or ""
        if self.memory is not None:
            self.memory.start_task(task or "")

        # reflector가 없으면 기존과 동일하게 동작 (하위 호환)
        max_reflections = (
            self.reflector.max_reflections
            if self.reflector
            else 0
        )

        reflection_attempt = 0

        # task 전체(재검증 라운드를 포함해서) 동안 변경된 파일을 누적한다.
        # 재검증 라운드에서 도구를 다시 호출하지 않아도, 이전 라운드에서
        # 변경한 파일 목록은 계속 reflection 대상으로 남아야 하기 때문.
        changed_files: list[str] = []

        # web_search/web_fetch 등 정보 검색 Tool의 실제 결과를 task 전체
        # 동안 누적한다. changed_files와 같은 이유 — 검색 결과가 여러 턴
        # 전 대화에 묻혀 있으면, 작은 로컬 모델은 _finalize 시점에 그걸
        # 놓치고 시스템 프롬프트의 grounding 규칙과 무관하게 학습 지식
        # (오래된 버전 번호 등)으로 답을 채우는 경우가 실제로 있었다
        # (재현됨: web_search+web_fetch 둘 다 성공적으로 실행됐는데도
        # 최종 답변은 여전히 오래된 "1.20.1"을 사실처럼 말함). changed_files
        # 처럼 _finalize에서 다시 명시적으로 원문을 보여준다.
        search_results: list[dict[str, Any]] = []

        # Plan.warnings(알 수 없는 Tool 무시 등)는 지금까지 콘솔에만
        # 찍히고 최종 답변 LLM에게는 전달되지 않았다. 그 결과 mkdir류
        # 환각 Tool이 걸려서 description에 "move_file로 대신하세요" 같은
        # 안내가 붙어도, 그 문구가 몇 턴 전 대화 속에 묻혀버려 작은
        # 모델이 최종 답변에서 놓치는 경우가 있었다. task 전체(재계획/
        # 리플렉션 라운드 포함) 동안 누적해서 _finalize에 명시적으로
        # 전달한다.
        warnings: list[str] = list(plan.warnings) if plan is not None else []

        # task 전체(재계획/리플렉션 라운드 포함) 동안, 이미 성공적으로
        # 실행 완료된 (tool, kwargs) 조합을 누적한다. 재계획으로 받은
        # 새 Plan에 예전과 완전히 동일한 Task가 다시 들어있으면
        # (Planner가 "이전 Task를 그대로 재사용"했거나, 완료 상태를
        # 못 알아챈 경우 모두 대비), Executor가 이를 재실행하지 않고
        # 건너뛴다 — 1-5(전체 재실행으로 인한 시간 낭비) 안전장치.
        # dict 값은 사람이 읽을 결과 요약(성공 로그 재사용용).
        completed_calls: dict[tuple[str, str], str] = {}

        use_plan = plan is not None

        while True:

            if use_plan:

                response = await self._run_plan(
                    messages,
                    task or "",
                    plan,
                    changed_files,
                    warnings,
                    search_results,
                    completed_calls,
                )

                # Plan은 한 번만 실행한다 — 이후 라운드는 자유 루프.
                use_plan = False

            else:

                response = await self._run_step_loop(
                    messages,
                    changed_files,
                )

            is_step_limit_warning = response.startswith(
                "⚠️ 최대 실행 단계"
            )

            is_plan_failure = response.startswith(
                "⚠️ 계획 실행 중 오류"
            )

            if (
                self.reflector is None
                or is_step_limit_warning
                or is_plan_failure
                or reflection_attempt >= max_reflections
            ):
                self.last_changed_files = list(changed_files)
                self._record_task_memory(
                    task=task or "",
                    plan=plan,
                    response=response,
                    changed_files=changed_files,
                    success=not (
                        is_step_limit_warning or is_plan_failure
                    ),
                )
                return response

            print(
                f"\n🔎 자기 검증 중... "
                f"(변경된 파일 {len(changed_files)}개)"
            )

            verdict = await self.reflector.review(
                task=task or "",
                final_answer=response,
                changed_files=changed_files,
            )

            if verdict.passed:
                self.last_changed_files = list(changed_files)
                self._record_task_memory(
                    task=task or "",
                    plan=plan,
                    response=response,
                    changed_files=changed_files,
                    success=True,
                )
                return response

            reflection_attempt += 1

            print(
                f"\n♻️ 자기 검증 실패 "
                f"({reflection_attempt}/{max_reflections}): "
                f"{verdict.feedback}"
            )

            messages.append(
                {
                    "role": "assistant",
                    "content": response,
                }
            )

            if self.goal_planner is not None:

                # Reflection 실패도 Auto Verify 실패와 동일하게 Planner의
                # 입력으로 넘긴다 — Executor가 임의로 "계속 해보세요"라고
                # 지시하는 대신, Planner가 리뷰 피드백을 반영한 새 Plan을
                # 세우게 한다.
                plan = await self._replan_from_reflection(
                    task=task or "",
                    plan=plan,
                    feedback=verdict.feedback,
                )

                plan = self._post_process_plan(plan)

                warnings.extend(plan.warnings)

                self._note_scratchpad(
                    f"[리플렉션 재계획 {reflection_attempt}/"
                    f"{max_reflections}] {verdict.feedback}"
                )

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[자기 검증 리뷰 결과 -> 재계획]\n\n"
                            f"{verdict.feedback}\n\n"
                            "위 피드백을 반영해 Planner가 새 계획을 "
                            f"세웠습니다.\n새 Goal: {plan.goal.text}"
                        ),
                    }
                )

                use_plan = True

            else:

                # goal_planner가 없으면 기존과 동일하게 동작 (하위 호환):
                # 피드백을 대화에 주입하고 자유 Tool 호출 루프로 계속한다.
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[자기 검증 리뷰 결과]\n\n"
                            f"{verdict.feedback}\n\n"
                            "위 피드백을 반영해서 작업을 계속하세요. "
                            "필요하면 Tool을 다시 호출하세요."
                        ),
                    }
                )

    async def _replan_from_reflection(
        self,
        task: str,
        plan,
        feedback: str,
    ):
        """Reflection 실패 피드백을 받아 GoalPlanner.replan()으로 새
        Plan을 받는다.

        직전에 실행된 Plan이 없는 경우(예: 애초에 자유 루프로 시작한
        작업)에도 GoalPlanner.replan()은 이전 Plan을 요구하므로, Task가
        없는 빈 Plan을 자리표시자로 만들어 넘긴다.
        """

        previous_plan = (
            plan
            if plan is not None
            else GoalPlan(goal=Goal(text=task), tasks=[])
        )

        context_summary = self._rebuild_context_summary(
            task, feedback,
        )

        return await self.goal_planner.replan(
            prompt=task,
            previous_plan=previous_plan,
            failure_log=feedback,
            context_summary=context_summary,
        )

    async def _run_step_loop(
        self,
        messages: list[dict[str, str]],
        changed_files: list[str],
    ) -> str:
        """Tool 호출이 없는 응답이 나올 때까지 실행하고 최종 응답을 반환한다.

        도구 호출로 변경된 파일 경로는 (호출자가 넘겨준) changed_files에
        계속 누적해서 기록한다.
        """

        for step in range(1, self.max_steps + 1):

            response = await self._chat_next(messages)

            tool_call = self._parse_tool(response)

            # Tool 호출이 없으면 종료
            if tool_call is None:
                return response

            step_record = self.execution_context.record_task(
                step,
                str(tool_name := tool_call.get("tool") or ""),
                dict(tool_call.get("kwargs") or {}),
            )
            self._current_step_record = step_record

            tool_name = tool_call.get("tool")

            if not tool_name:
                return (
                    "⚠️ Tool JSON에는 "
                    "'tool' 필드가 반드시 필요합니다."
                )

            kwargs = tool_call.get("kwargs", {})

            print(f"\n⚙️ Step {step}: {tool_name}")
            print(f"   kwargs: {self._format_for_log(kwargs)}")

            call_id = uuid.uuid4().hex[:8]

            await self.launchcore.tool_start(
                call_id,
                tool_name,
            )

            result = await self._execute_tool(
                tool_name,
                kwargs,
            )
            self._last_tool_result = result

            tool_success = self._is_tool_result_success(result)
            if self._current_step_record is not None and self._current_step_record.tool == tool_name:
                self._current_step_record.status = "succeeded" if tool_success else "failed"
                self._current_step_record.message = str(result.get("message", "") or result.get("summary", ""))
            if tool_success:
                self._last_failure_context = ""
                if self.execution_context.last_error:
                    self.execution_context.last_error = {}
            else:
                self._set_failure_context(tool_name, result)

            if not tool_success:
                # 자유 루프에서도 Tool 실패는 구조화된 실행 결과에 남겨야
                # build_execution_result가 성공으로 오판하지 않는다
                # (Plan 루프와 동일한 기록 규칙).
                self.execution_context.last_error = {
                    "tool": tool_name,
                    "message": str(result.get("message", "") or result),
                    "error_type": str(result.get("error_type", "") or ""),
                }

            await self.launchcore.tool_end(
                call_id,
                tool_success,
            )

            self._refresh_index(
                tool_name,
                kwargs,
            )

            self._track_changed_file(
                tool_name,
                kwargs,
                result,
                changed_files,
            )

            self._note_scratchpad(
                f"[Step {step}] {tool_name} 실행 "
                f"({'성공' if tool_success else '실패'})"
            )

            messages.append(
                {
                    "role": "assistant",
                    "content": response,
                }
            )

            messages.append(
                {
                    "role": "user",
                    "content": self._tool_result_message(
                        tool_name,
                        result,
                    ),
                }
            )

        return (
            f"⚠️ 최대 실행 단계({self.max_steps})에 도달했습니다."
        )

    async def _chat_next(self, messages: list[dict[str, str]]) -> str:
        """다음 LLM 호출에 직전 Tool 실패를 명시적으로 주입한다."""
        if not self._last_failure_context:
            return await self.llm.chat(messages)

        augmented = list(messages)
        augmented.append(
            {
                "role": "user",
                "content": (
                    "[이전 실행 실패 정보]\n"
                    f"{self._last_failure_context}\n\n"
                    "이 실패를 해결하는 방향으로 이번 작업을 계속하세요. "
                    "이미 성공한 작업은 불필요하게 반복하지 마세요."
                ),
            }
        )
        return await self.llm.chat(augmented)

    def _normalize_plan_for_workspace(self, plan):
        """Apply workspace-sensitive plan normalization on every plan path.

        This intentionally lives in Executor too: immediate Tool-failure
        replans do not pass through AgentOrchestrator._normalize_plan_for_workspace.
        """
        if plan is None:
            return plan
        workspace = getattr(self, "workspace_path", None)
        if not workspace:
            return plan
        root = Path(workspace).resolve()
        if (root / ".git").exists():
            return plan
        copied = deepcopy(plan)
        replaced = False
        for task in copied.tasks:
            if task.tool in {"git_status", "git_diff"}:
                original = task.tool
                task.tool = "list_directory"
                task.description = (
                    f"Git 저장소가 아닌 작업공간이므로 {original} 대신 "
                    "list_directory로 실제 파일/프로젝트 상태를 확인합니다."
                )
                task.kwargs = {}
                replaced = True
        if replaced:
            copied.warnings.append(
                "현재 workspace에 .git이 없어 git 관련 상태 확인을 list_directory로 정규화했습니다."
            )
        return copied

    def _normalize_project_layout_plan(self, plan):
        """Drop nonsensical project-root moves before Tool execution.

        A Planner sometimes creates ``move_file(project_dir, workspace_root)``
        after already creating the project directly inside the workspace. On
        Windows this becomes WinError 183 and can trigger a long replan storm.
        """
        if plan is None or not self.workspace_path:
            return plan
        root = Path(self.workspace_path).resolve()
        copied = deepcopy(plan)
        kept = []
        removed = False
        for task in copied.tasks:
            if task.tool != "move_file":
                kept.append(task)
                continue
            kw = getattr(task, "kwargs", {}) or {}
            src = kw.get("file_path") or kw.get("path")
            dst = kw.get("new_path") or kw.get("destination")
            try:
                src_path = (root / str(src)).resolve() if src else None
                dst_path = (root / str(dst)).resolve() if dst else None
            except Exception:
                src_path = dst_path = None
            if (src_path is not None and dst_path is not None
                    and src_path.is_dir() and dst_path == root):
                removed = True
                continue
            kept.append(task)
        if removed:
            copied.tasks = kept
            copied.warnings.append(
                "이미 workspace 내부에 생성된 프로젝트 디렉터리를 workspace 루트로 이동하는 잘못된 move_file Task를 제거했습니다."
            )
        for i, task in enumerate(copied.tasks, 1):
            task.order = i
        return copied

    def _post_process_plan(self, plan):
        """Run all common plan normalizers for both initial plans and replans."""
        if plan is None:
            return plan

        agent = self.agent
        fixer = getattr(agent, "_auto_fix_mkdir_hallucination", None) if agent is not None else None
        if fixer is not None:
            try:
                plan = fixer(plan, getattr(agent, "project_index", None))
            except Exception:
                pass

        plan = self._normalize_plan_for_workspace(plan)
        return self._normalize_project_layout_plan(plan)

    async def _run_plan(
        self,
        messages: list[dict[str, str]],
        task: str,
        plan,
        changed_files: list[str],
        warnings: list[str] | None = None,
        search_results: list[dict[str, Any]] | None = None,
        completed_calls: dict[tuple[str, str], str] | None = None,
    ) -> str:
        """Plan(core.goal_planner.Plan)의 Task를 순서대로 실행한다.

        - Task 순서는 Plan이 정한 그대로 실행하고, Executor가 임의로
          순서를 바꾸거나 Task를 추가/삭제하지 않는다 (계획 변경 금지).
        - 각 Task에서는 Tool 실행 결과(또는 자유 응답)만 수집해서
          대화에 쌓는다.
        - Tool 실행이 실패하면 Planner에게 재계획(replan)을 요청하고,
          새로 받은 Plan으로 이어서 실행한다 (최대 self.max_replans 회).
        - 재계획을 받아도 이전에 실패한 Plan과 Tool sequence가 완전히
          똑같으면(같은 실수를 그대로 반복하는 것이므로), LLM에게 다시
          한번 실행할 기회를 주지 않고 코드가 즉시 실패로 종료한다
          (동일 계획 반복 감지 — `_is_repeated_tool_sequence`).
        """

        current_plan = plan
        replans = 0
        warnings = warnings if warnings is not None else []
        search_results = (
            search_results if search_results is not None else []
        )
        completed_calls = (
            completed_calls if completed_calls is not None else {}
        )

        while True:

            failure = await self._execute_tasks(
                messages,
                current_plan,
                changed_files,
                search_results,
                completed_calls,
            )

            if failure is None:
                break

            error_type = self._extract_failure_type(failure)
            # _execute_tool() already owns per-Tool retries. Reaching this
            # point means those retries are exhausted, so FailurePolicy here
            # decides replan/stop rather than attempting a duplicate retry loop.
            decision = FailurePolicy.decide(
                error_type=error_type,
                attempt=1,
                max_retries=1,
                max_replans=self.max_replans,
                replans=replans,
            )
            if (
                self.goal_planner is None
                or decision.action == "stop"
            ):
                return (
                    "⚠️ 계획 실행 중 오류가 발생했습니다 "
                    f"(재계획 {replans}/{self.max_replans}회 시도):\n"
                    f"{failure}"
                )

            replans += 1

            print(
                f"\n♻️ Task 실패로 재계획 요청 "
                f"({replans}/{self.max_replans}): {failure}"
            )

            self._note_scratchpad(
                f"[재계획 {replans}/{self.max_replans}] "
                f"실패 사유: {failure}"
            )

            # Retry Loop: 실패 로그(빌드/테스트 에러 등)에는 원래 프롬프트로는
            # 찾지 못했던 파일/심볼 단서(스택 트레이스 경로, 에러 메시지 속
            # 클래스명 등)가 들어있는 경우가 많다. 그대로 재계획만 하지 않고,
            # 실패 로그까지 포함해 컨텍스트(관련 파일/심볼)를 다시 검색한다.
            context_summary = self._rebuild_context_summary(
                task, failure,
            )

            previous_sequence = current_plan.tool_sequence()

            new_plan = await self.goal_planner.replan(
                prompt=task,
                previous_plan=current_plan,
                failure_log=failure,
                context_summary=context_summary,
            )

            new_plan = self._post_process_plan(new_plan)

            if self._is_repeated_tool_sequence(
                previous_sequence, new_plan.tool_sequence(),
            ):
                warnings.extend(new_plan.warnings)

                # 동일 계획이 반복됐다고 해서 원래 실패가 아직도 유효하다는
                # 보장은 없다 — 이전 라운드 안에서 다른 Task(예: patch_file)가
                # 이미 문제를 고쳤는데 verify_project가 그 뒤 무관한 이유로
                # 실패했을 수도 있다. 그런데도 여기서 예전 `failure` 문자열을
                # 그대로 최종 보고에 써버리면, 실제로는 해결된 문제를 "아직도
                # 실패 중"이라고 잘못 보고하게 된다. 종료를 확정하기 전에
                # verify_project를 한 번 더 직접 재실행해 최신 상태를 확인한다.
                # 단, 재검증이 의미를 가지려면 직전 라운드에 실제 파일 변경이
                # 있었어야 한다 — 변경이 없었다면(예: Plan이 verify_project
                # 하나뿐이었던 경우) 몇 번 다시 돌려도 같은 결과가 나오므로
                # 최대 900초까지 걸릴 수 있는 verify_project를 낭비 없이
                # 건너뛴다 (재확인 불가(None) → 기존 failure 그대로 사용).
                fresh_failure = await self._recheck_failure_still_present(
                    failure, changed_files=changed_files,
                )
                if fresh_failure is False:
                    self._note_scratchpad(
                        f"[반복 계획 감지 {replans}/{self.max_replans}] "
                        "재확인 결과 원래 실패가 이미 해결되어 성공으로 종료"
                    )
                    return await self._finalize(
                        messages, changed_files, warnings, search_results,
                    )

                self._note_scratchpad(
                    f"[반복 계획 감지 {replans}/{self.max_replans}] "
                    "재계획해도 Tool sequence가 이전과 동일해 "
                    "deterministic recovery로 종료"
                )

                return self._format_repeated_plan_failure(
                    replans, self.max_replans, previous_sequence,
                    fresh_failure if isinstance(fresh_failure, str) else failure,
                )

            current_plan = new_plan

            warnings.extend(current_plan.warnings)

            messages.append(
                {
                    "role": "user",
                    "content": (
                        "[재계획]\n\n"
                        "이전 Task 실행이 실패해서 Planner가 새 계획을 "
                        f"세웠습니다.\n새 Goal: {current_plan.goal.text}"
                    ),
                }
            )

        return await self._finalize(
            messages, changed_files, warnings, search_results,
        )

    async def _recheck_failure_still_present(
        self, failure: str, changed_files: list[str] | None = None,
    ) -> str | bool | None:
        """동일 계획 반복으로 중단하기 직전, verify_project를 한 번 더
        직접 실행해 원래 실패가 여전히 재현되는지 확인한다.

        반환값:
        - False: verify_project를 재실행했고 이제는 통과함 (원래 문제가
          이미 해결됨). 호출부는 실패 대신 성공으로 마무리해야 한다.
        - str: verify_project를 재실행했고 여전히 실패함. 최신 실패
          메시지를 반환하므로, 오래된(stale) `failure` 대신 이 값을
          최종 보고에 사용한다.
        - None: verify_project 자체를 (권한/스킬 부재 등으로) 재실행할
          수 없었거나, 재확인의 근거가 될 파일 변경이 없어 건너뛴다.
          이 경우 기존 동작대로 원래 `failure`를 그대로 쓴다 (재확인이
          불가능하다고 해서 실패를 성공으로 둔갑시키지 않는다).
        """

        if not changed_files:
            # 직전 라운드에서 바뀐 파일이 없다면 검증 결과도 바뀌지 않았다.
            return None

        skill = self.skill_registry.get_skill("verify_project")
        if skill is None:
            return None

        try:
            result = await self._execute_tool("verify_project", {})
        except Exception:
            return None

        if not isinstance(result, dict):
            return None

        if self._is_tool_result_success(result):
            return False

        if isinstance(result, dict):
            parts = [p for p in (result.get("summary"), result.get("failure_log")) if p]
            message = "\n".join(parts) if parts else result.get("message", str(result))
            error_type = result.get("error_type")
            if error_type:
                message = f"[오류 유형: {error_type}] {message}"
        else:
            message = str(result)

        return f"[재확인] 'verify_project' 재실행 결과 여전히 실패: {message}"

    async def _execute_tasks(
        self,
        messages: list[dict[str, str]],
        plan,
        changed_files: list[str],
        search_results: list[dict[str, Any]] | None = None,
        completed_calls: dict[tuple[str, str], str] | None = None,
    ) -> str | None:
        """Plan의 Task를 순서대로 실행한다.

        모두 성공하면 None을, 하나라도 실패하면 실패 로그 문자열을
        반환한다 (반환 즉시 나머지 Task 실행은 중단한다).

        completed_calls가 주어지면, 이전 라운드(재계획/리플렉션)에서
        이미 성공한 것과 (tool, kwargs)가 완전히 동일한 Task는 다시
        실행하지 않고 건너뛴다 — Planner가 "이전 Task 재사용"을
        선택했거나 완료 상태를 놓친 경우에도, 이미 끝난 작업이
        중복 실행되어 시간을 낭비하지 않도록 하는 코드 레벨 안전장치
        (1-5: 자기검증/재계획 실패 시 Task 1부터 전체 재실행되던 문제).
        """

        search_results = (
            search_results if search_results is not None else []
        )
        completed_calls = (
            completed_calls if completed_calls is not None else {}
        )

        for pt in plan.tasks:
            task_record = self.execution_context.record_task(
                int(getattr(pt, "order", len(self.execution_context.task_records) + 1)),
                str(getattr(pt, "tool", "") or ""),
                dict(getattr(pt, "kwargs", {}) or {}),
            )
            # Verification is a separate pipeline stage. GoalPlanner may add
            # verify_project automatically, but while the Coder role is active
            # we defer it to TesterAgent instead of treating it as a permission
            # failure or allowing the Coder to self-certify its own work.
            if self._active_role == "Coder" and pt.tool == "verify_project":
                pt.status = "deferred"
                task_record.status = "deferred"
                self._note_scratchpad(
                    f"[Task {pt.order}] verify_project -> TesterAgent에 위임"
                )
                continue

            if pt.tool is None:
                # 순수 판단/설명 Task -> Tool 실행 없이 다음 Task로.
                pt.status = "done"
                task_record.status = "skipped"
                self._note_scratchpad(
                    f"[Task {pt.order}] {pt.description} (Tool 없음)"
                )
                continue

            # 결정론적 PlanTask는 Tool kwargs를 코드가 이미 알고 있다.
            # 이 경우 작은 모델에게 다시 인자를 생성하게 하지 않아
            # file_path/old_str/new_str 환각과 형식 누락을 제거한다.
            deterministic_kwargs = dict(getattr(pt, "kwargs", {}) or {})
            if deterministic_kwargs:
                kwargs = deterministic_kwargs
                response = ""
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": self._task_instruction(pt),
                    }
                )

                response = await self._chat_next(messages)

                messages.append(
                    {
                        "role": "assistant",
                        "content": response,
                    }
                )

                kwargs = self._parse_kwargs_for(pt.tool, response)
            kwargs = self._recover_file_path_from_task(pt.tool, kwargs, pt.description)
            resolved = self.tool_resolver.resolve(
                pt.tool, kwargs, description=pt.description
            )
            if not resolved.ok:
                pt.status = "failed"
                task_record.status = "failed"
                task_record.message = resolved.message
                return self._format_task_failure(
                    pt, {"status": "error", "error_type": "validation", "message": resolved.message}
                )
            kwargs = resolved.kwargs

            # 소형 모델이 첫 응답에서 Tool JSON을 누락하거나 필수
            # kwargs를 빠뜨리는 경우가 있다. 그대로 빈 kwargs로 실행하면
            # 결정적 실패 후 불필요한 전체 재계획으로 빠질 수 있으므로,
            # 계획된 Tool을 바꾸지 않고 한 번만 형식을 교정한다.
            required = self._KNOWN_REQUIRED_OVERRIDES.get(pt.tool) or []
            if (not deterministic_kwargs) and required and not all(kwargs.get(key) for key in required):
                messages.append({
                    "role": "user",
                    "content": (
                        f"[Tool JSON 형식 재시도] 이전 응답에서 '{pt.tool}'의 "
                        f"필수 kwargs가 누락되었습니다. 이 Task의 Tool은 "
                        f"'{pt.tool}'로 고정되어 있습니다. 다른 설명 없이 "
                        "반드시 올바른 JSON 하나만 반환하세요.\n\n"
                        f"필수 필드: {', '.join(required)}"
                    ),
                })
                retry_response = await self._chat_next(messages)
                messages.append({
                    "role": "assistant",
                    "content": retry_response,
                })
                retry_kwargs = self._parse_kwargs_for(pt.tool, retry_response)
                if all(retry_kwargs.get(key) for key in required):
                    kwargs = retry_kwargs
                else:
                    msg = (
                        f"'{pt.tool}' Tool의 필수 kwargs를 두 번의 응답에서 모두 얻지 못했습니다. "
                        f"필수 필드: {', '.join(required)}"
                    )
                    self._note_scratchpad(f"[Task {pt.order}] {pt.tool} kwargs 재시도 실패")
                    pt.status = "failed"
                    task_record.status = "failed"
                    task_record.message = msg
                    failure = {"status": "error", "error_type": "validation", "message": msg}
                    self._set_failure_context(pt.tool, failure)
                    return self._format_task_failure(pt, failure)

            if pt.tool == "web_fetch":
                kwargs = self._enforce_web_fetch_url(
                    kwargs, search_results,
                )

            call_key = self._completed_call_key(pt.tool, kwargs)

            if call_key is not None and call_key in completed_calls:
                # 동일 Tool + 동일 kwargs가 이전 라운드에서 이미 성공함
                # -> 재실행하지 않고 그 결과를 그대로 재사용.
                pt.status = "done"
                task_record.status = "skipped"
                task_record.message = "이전 라운드에서 동일한 입력으로 성공 완료"
                print(
                    f"\n⏭️  Task {pt.order}: {pt.tool} "
                    "(이전에 이미 완료 — 재실행 건너뜀)"
                )
                self._note_scratchpad(
                    f"[Task {pt.order}] {pt.description} "
                    "(이전에 완료됨 — 건너뜀)"
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[Tool 실행 건너뜀]\n\n"
                            f"Tool: {pt.tool}\n\n"
                            "이 Task는 이전 라운드에서 이미 동일한 "
                            "입력으로 성공적으로 실행되었습니다. "
                            "재실행하지 않습니다.\n"
                            f"이전 결과: {completed_calls[call_key]}\n\n"
                            "다음 Task로 넘어가세요."
                        ),
                    }
                )
                continue

            print(f"\n⚙️ Task {pt.order}: {pt.tool}")
            print(f"   kwargs: {self._format_for_log(kwargs)}")

            call_id = uuid.uuid4().hex[:8]

            await self.launchcore.tool_start(
                call_id,
                pt.tool,
            )

            result = await self._execute_tool(
                pt.tool,
                kwargs,
            )
            self._last_tool_result = result

            if pt.tool == "move_file" and self._is_tool_result_success(result):
                src = kwargs.get("file_path") or kwargs.get("path")
                dst = kwargs.get("new_path") or kwargs.get("destination")
                if src and dst:
                    repaired_refs = self._repair_moved_file_references(str(src), str(dst))
                    if repaired_refs:
                        result = dict(result)
                        result["reference_repairs"] = repaired_refs
                        result["message"] = str(result.get("message", "move_file 완료")) + f"; 참조 경로 {len(repaired_refs)}개 자동 보정"
                        for ref in repaired_refs:
                            if ref not in changed_files:
                                changed_files.append(ref)
                            self.execution_context.add_changed_file(ref)

            tool_success = self._is_tool_result_success(result)
            if tool_success:
                self._last_failure_context = ""
            else:
                self._set_failure_context(pt.tool, result)

            print(
                f"   결과 ({'성공' if tool_success else '실패'}): "
                f"{self._format_for_log(result)}"
            )

            await self.launchcore.tool_end(
                call_id,
                tool_success,
            )

            self._refresh_index(
                pt.tool,
                kwargs,
            )

            self._track_changed_file(
                pt.tool,
                kwargs,
                result,
                changed_files,
            )

            self._track_search_result(
                pt.tool,
                kwargs,
                result,
                search_results,
            )

            self._note_scratchpad(
                f"[Task {pt.order}] {pt.description} -> {pt.tool} "
                f"({'성공' if tool_success else '실패'})"
            )

            messages.append(
                {
                    "role": "user",
                    "content": self._tool_result_message(
                        pt.tool,
                        result,
                    ),
                }
            )

            if not tool_success:
                pt.status = "failed"
                task_record.status = "failed"
                task_record.message = str(result.get("message", "") or result.get("summary", ""))
                self.execution_context.last_error = {
                    "tool": pt.tool,
                    "message": task_record.message,
                    "error_type": str(result.get("error_type", "") or ""),
                }
                return self._format_task_failure(pt, result)

            pt.status = "done"
            task_record.status = "succeeded"
            task_record.message = str(result.get("message", "") or result.get("summary", ""))
            # 재계획/재시도로 이후 Task가 성공하면 이전 실패는 "마지막
            # 상태"가 아니다. 최종 ExecutionResult가 성공을 실패로
            # 뒤집지 않도록 성공 시점에 last_error를 비운다.
            if self.execution_context.last_error:
                self.execution_context.last_error = {}

            if call_key is not None:
                completed_calls[call_key] = self._format_for_log(result)

        return None

    async def _finalize(
        self,
        messages: list[dict[str, str]],
        changed_files: list[str] | None = None,
        warnings: list[str] | None = None,
        search_results: list[dict[str, Any]] | None = None,
    ) -> str:
        """모든 Task 실행이 끝난 뒤, Tool 호출 없이 최종 답변만 받는다."""

        # 실제로 생성/수정된 파일 목록을 명시적으로 알려준다. 이게
        # 없으면 작은 모델이 "무엇을 실제로 했는지"를 매번 다시
        # 추측하게 되어, 이미 만든 파일(plugin.yml 등)을 다시
        # "이런 파일입니다"라고 개념 설명만 반복하거나, 사용자가
        # "OO도 포함해줘"라고 재요청해도 실제로 뭐가 바뀌었는지 반영
        # 못 하고 이전과 거의 같은 답을 재활용하는 문제가 실제로
        # 있었다.
        if changed_files:
            files_line = (
                "이번 작업으로 실제 생성/수정된 파일: "
                + ", ".join(changed_files)
            )
        else:
            files_line = "이번 작업으로 파일 변경은 없었습니다."

        # Plan.warnings(예: 존재하지 않는 Tool을 지어내서 무시된 경우)를
        # 지금까지는 콘솔에만 찍고 최종 답변 LLM에게는 넘기지 않았다.
        # 그 결과 mkdir류 환각 Tool이 걸려 description에 대안(예:
        # move_file)이 붙어도, 몇 턴 전 대화 속에 묻혀 최종 답변이
        # 그걸 놓치고 "할 수 없다"고만 말하고 끝나는 경우가 있었다.
        # 여기서 명시적으로 다시 짚어줘서 최종 답변이 실패 사실과
        # (있다면) 대안까지 정확히 반영하게 한다.
        if warnings:
            dedup_warnings = list(dict.fromkeys(warnings))
            warnings_block = (
                "\n\n이번 작업 중 아래 경고가 있었습니다 (Planner가 "
                "존재하지 않는 Tool을 요청했거나 계획대로 실행되지 "
                "않은 부분입니다):\n"
                + "\n".join(f"- {w}" for w in dedup_warnings)
                + "\n위 경고와 관련된 작업은 실제로 실행되지 않았을 "
                "수 있습니다. 최종 답변에서 이 사실을 숨기지 말고, "
                "경고 문구 안에 대안(예: move_file 등)이 언급되어 "
                "있다면 그 대안을 사용자에게 함께 안내하세요."
            )
        else:
            warnings_block = ""

        # 검색/웹 조회 결과 원문을 최종 답변 요청 바로 앞에 다시
        # 명시적으로 보여준다. 시스템 프롬프트의 grounding 규칙만으로는
        # (특히 여러 Task를 거쳐 대화가 길어졌을 때) 작은 로컬 모델이
        # 몇 턴 전 결과를 놓치고 학습 지식으로 답을 채우는 경우가
        # 실제로 있었다. 여기서 원문을 그대로 다시 붙여서, "지금 막
        # 본 내용"으로 만든다.
        if search_results:

            blocks = []

            for item in search_results:

                try:
                    result_text = json.dumps(
                        item["result"], ensure_ascii=False, default=str,
                    )
                except Exception:
                    result_text = str(item.get("result"))

                blocks.append(
                    f"[{item['tool']} 결과]\n"
                    f"요청: {json.dumps(item.get('kwargs') or {}, ensure_ascii=False)}\n"
                    f"{result_text}"
                )

            search_results_block = (
                "\n\n# 검색/웹 조회 원문 (최종 답변은 반드시 이 안의 "
                "내용에만 근거하세요)\n"
                + "\n\n".join(blocks)
                + "\n\n위 원문에 없는 사실(버전 번호, 날짜, 가격, 이름 "
                "등)은 당신의 학습 지식에서 나온 것이므로 최종 답변에 "
                "쓰지 마세요. 위 원문이 질문에 명확히 답하지 못한다면 "
                "'검색 결과에서 정확한 답을 찾지 못했습니다' 등으로 "
                "정직하게 말하고, 관련성 높아 보이는 링크가 있다면 "
                "함께 안내하세요. 절대 원문에 없는 숫자/날짜를 "
                "확실한 사실인 것처럼 말하지 마세요."
            )
        else:
            search_results_block = ""

        messages.append(
            {
                "role": "user",
                "content": (
                    "모든 Task 실행이 끝났습니다. 지금까지의 결과를 "
                    "바탕으로, 사용자에게 직접 말하듯이 최종 답변을 "
                    "한국어 텍스트로 작성하세요 (JSON Tool 호출 블록 "
                    "금지). '사용자는 ~라고 했습니다' 같은 3인칭 "
                    "상황 서술/보고체는 쓰지 마세요 — 사용자가 지금 "
                    "읽을 답변이니, 실제로 사용자에게 하는 말처럼 "
                    "쓰세요.\n\n"
                    f"{files_line}\n"
                    "위 목록에 있는 파일만 '만들었다/수정했다'고 "
                    "말하세요. 목록에 없는 파일(예: 사용자가 방금 "
                    "요청했지만 이번 Task에 없었던 파일)은 아직 "
                    "만들어지지 않았다는 걸 분명히 밝히세요 — 이미 "
                    "있는 것처럼 얼버무리지 마세요."
                    f"{warnings_block}"
                    f"{search_results_block}"
                ),
            }
        )

        response = await self.llm.chat(messages)

        context = getattr(self, "execution_context", None)
        metrics_snapshot = self.metrics.snapshot()
        if context is not None:
            task_statuses = [str(item.get("status", "")) for item in context.as_dict().get("tasks", [])]
            execution_success = not any(status == "failed" for status in task_statuses)
            if context.verification and context.verification.get("success") is False:
                execution_success = str(context.verification.get("verification_status", "")).lower() == "not_run"
            if context.requirement_satisfied is False:
                execution_success = False
            self.last_execution_result = ExecutionResult(
                success=execution_success,
                changed_files=list(changed_files or []),
                verification=dict(getattr(context, "verification", {}) or {}),
                tasks=context.as_dict().get("tasks", []),
                warnings=list(warnings or []),
                error=dict(getattr(context, "last_error", {}) or {}),
                metrics=metrics_snapshot,
                requirement_satisfied=getattr(context, "requirement_satisfied", None),
                result_state=("FAILED" if not execution_success else getattr(context, "result_state", "PASSED")),
            )
        else:
            self.last_execution_result = ExecutionResult(
                success=True,
                changed_files=list(changed_files or []),
                warnings=list(warnings or []),
                metrics=metrics_snapshot,
            )

        messages.append(
            {
                "role": "assistant",
                "content": response,
            }
        )

        return response

    def build_execution_result(self, success: bool | None = None) -> ExecutionResult:
        """Build an LLM-independent structured result from the current execution context."""
        ctx = self.execution_context
        if success is None:
            success = not bool(ctx.last_error) and (
                not ctx.verification
                or bool(ctx.verification.get("success", False))
                or str(ctx.verification.get("verification_status", "")).lower() == "not_run"
            )
            if ctx.requirement_satisfied is False:
                success = False
        if ctx.requirement_satisfied is False:
            state = "FAILED"
        elif ctx.result_state in {"NO_CHANGE", "ALREADY_SATISFIED", "NOT_APPLICABLE"}:
            state = ctx.result_state
        else:
            state = "PASSED" if success else "FAILED"
        result = ExecutionResult(
            success=bool(success),
            changed_files=list(self.execution_context.changed_files),
            verification=dict(self.execution_context.verification),
            tasks=[
                {
                    "order": r.order,
                    "tool": r.tool,
                    "status": r.status,
                    "message": r.message,
                }
                for r in self.execution_context.task_records
            ],
            warnings=list(self.execution_context.warnings),
            error=dict(self.execution_context.last_error),
            metrics=self.metrics.snapshot(),
            requirement_satisfied=self.execution_context.requirement_satisfied,
            result_state=state,
        )
        self.last_execution_result = result
        self.metrics.task_end(result.success)
        self.telemetry.log("task_end", success=result.success, changed_files=result.changed_files, verification=result.verification, metrics=result.metrics)
        # Explicit terminator in the JSONL log: a run that dies mid-task is
        # otherwise indistinguishable from one that finished, and every
        # consumer has to guess from a missing task_end.
        self.telemetry.log(END_OF_TOKEN_EVENT, marker=END_OF_TOKEN, task=self._current_task)
        return result

    async def _execute_tool(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:

        permission = check_tool_permission(self._active_role, tool_name)
        if not permission.allowed:
            return {
                "status": "error",
                "error_type": self.ERROR_TYPE_PERMISSION,
                "message": permission.message,
            }

        skill = self.skill_registry.get_skill(
            tool_name
        )

        if skill is None:
            return {
                "status": "error",
                "error_type": self.ERROR_TYPE_VALIDATION,
                "message": f"Unknown tool: {tool_name}",
            }

        scope_guard = self._active_scope_guard(tool_name, kwargs)
        if scope_guard is not None:
            return scope_guard

        guard_result = self._check_protected_files(tool_name, kwargs)
        if guard_result is not None:
            return guard_result

        turn_guard = self._check_turn_intent(tool_name)
        if turn_guard is not None:
            return turn_guard

        # A common LLM planning error is to apply patch_file and then immediately
        # call preview_patch with the same old_str. At that point old_str no longer
        # exists, so preview_patch used to turn an already-successful mutation into
        # a false task failure. If the replacement is already present, the preview
        # is stale rather than an actual project error; report a successful no-op.
        # A stale apply_patch immediately after a successful patch_file/preview_patch
        # is a planner artifact, not a real mutation failure. If no diff remains,
        # treat the apply as a successful no-op instead of poisoning the whole cycle.
        if tool_name == "apply_patch" and not (kwargs.get("diff") or kwargs.get("patch")):
            return {
                "status": "success",
                "message": "적용할 diff가 없어 no-op으로 처리했습니다.",
                "changed": False,
                "stale_apply": True,
            }

        if tool_name == "preview_patch" and kwargs.get("file_path") and kwargs.get("old_str"):
            try:
                target = Path(self.workspace_path) / str(kwargs["file_path"])
                current = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
                old_str = str(kwargs.get("old_str") or "")
                new_str = str(kwargs.get("new_str") or "")
                if old_str and old_str not in current and new_str and new_str in current:
                    return {
                        "status": "success",
                        "message": (
                            "요청한 patch의 새 내용이 이미 파일에 적용되어 있어 "
                            "preview_patch를 다시 생성할 변경 사항이 없습니다."
                        ),
                        "diff": "",
                        "changed": False,
                        "stale_preview": True,
                    }
            except OSError:
                pass

        semantic_guard = self._check_mutation_semantics(tool_name, kwargs)
        if semantic_guard is not None:
            return semantic_guard

        if tool_name == "patch_file":
            recovered = self._patch_file_local_recovery(kwargs)
            if recovered is not None:
                if recovered.get("__already_applied"):
                    return {
                        "status": "success",
                        "message": "요청한 patch 내용이 이미 적용되어 있어 no-op으로 처리했습니다.",
                        "changed": False,
                        "already_applied": True,
                    }
                kwargs = {k: v for k, v in recovered.items() if not str(k).startswith("__") }

        if tool_name in self.FILE_MUTATING_TOOLS:
            mutation_path = (kwargs.get("file_path") or kwargs.get("path") or kwargs.get("new_path") or kwargs.get("destination"))
            if mutation_path:
                self._last_mutation_snapshot = self.snapshot_store.snapshot([str(mutation_path)])
            else:
                self._last_mutation_snapshot = None

        if tool_name == "patch_file" and kwargs.get("file_path") and kwargs.get("old_str"):
            try:
                target = Path(self.workspace_path) / str(kwargs["file_path"])
                if target.is_file():
                    current = target.read_text(encoding="utf-8", errors="replace")
                    old = str(kwargs["old_str"])
                    if old not in current:
                        # Fuzzy recovery: tolerate CRLF/LF differences, trailing
                        # whitespace, and indentation/spacing drift while still
                        # requiring exactly one match. This avoids blindly patching
                        # the wrong occurrence.
                        def canonical(value: str) -> str:
                            return re.sub(r"\s+", " ", value.replace("\r\n", "\n").replace("\r", "\n")).strip()

                        nold = canonical(old)
                        if nold:
                            parts = [re.escape(x) for x in re.split(r"\s+", nold) if x]
                            fuzzy_pattern = r"\s+".join(parts)
                            matches = list(re.finditer(fuzzy_pattern, current, flags=re.DOTALL))
                            if len(matches) == 1:
                                kwargs["old_str"] = matches[0].group(0)
                                print("⚙️ patch_file old_str을 공백/들여쓰기 차이까지 허용해 유일 매치로 복구합니다.")
            except OSError:
                pass

        if tool_name == "move_file" and kwargs.get("file_path") and (kwargs.get("new_path") or kwargs.get("destination")):
            try:
                root = Path(self.workspace_path).resolve()
                src = (root / str(kwargs.get("file_path"))).resolve()
                dst = (root / str(kwargs.get("new_path") or kwargs.get("destination"))).resolve()
                if src.is_dir() and dst == root:
                    return {
                        "status": "error",
                        "error_type": self.ERROR_TYPE_VALIDATION,
                        "message": "이미 workspace 안에 있는 프로젝트 디렉터리를 workspace 루트로 이동하는 작업은 허용하지 않습니다.",
                    }
            except Exception:
                pass

        # File mutation skills need the original user request to distinguish
        # "modify an existing symbol" from "create a new symbol".  The model
        # does not get to choose whether a missing requested symbol may be
        # invented; the deterministic MutationGuard decides that.
        if tool_name in self.FILE_MUTATING_TOOLS and self._current_task:
            kwargs = dict(kwargs)
            kwargs.setdefault("__task", self._current_task)

        sig = inspect.signature(
            skill.execute
        )

        if (
            "workspace_path" in sig.parameters
            and "workspace_path" not in kwargs
        ):
            kwargs["workspace_path"] = (
                self.workspace_path
            )

        if (
            "project" in sig.parameters
            and self.agent is not None
        ):
            # project는 workspace_path와 마찬가지로 시스템이 자동
            # 주입하는 값이라 LLM은 채울 필요가 없다 (_kwargs_schema_hint
            # 에서도 제외됨). 그런데도 LLM이 문자열("Python", "Gradle"
            # 등)을 추측해서 kwargs["project"]에 채워 넣는 경우가 있었고,
            # 예전 코드는 "project" not in kwargs 조건 때문에 그 잘못된
            # 값을 그대로 두고 넘어가서 verify_project 등에서
            # "'str' object has no attribute 'build_system'"으로
            # 크래시했다. 이제는 LLM이 뭘 채웠든 project_index의 실제
            # project 객체로 항상 덮어써서, 잘못된 추측값이 절대
            # 그대로 전달되지 않게 한다.
            #
            # project_index가 없으면(최초 실행 실패, refresh_file 도중
            # 예외로 invalidate_index()된 경우 등) verify_project 같은
            # Tool이 project=None을 받아 조용히 실패하고, Replan을 여러
            # 번 반복해도 같은 이유로 계속 실패하는 문제가 있었다.
            # kwargs를 채우기 직전에 인덱스가 비어 있으면 여기서 즉시
            # 재생성을 시도해서, Tool 실행 시점에는 project가 준비돼
            # 있도록 보장한다 (실패해도 아래에서 기존과 동일하게
            # project 없이 넘어간다 — Tool 쪽 자체 방어는 그대로 유지).
            if self.agent.project_index is None:
                try:
                    self.agent.rebuild_index()
                except Exception as e:
                    print(
                        "⚠️ project_index 재생성 실패 "
                        f"(project 없이 '{tool_name}' 계속 진행): {e}"
                    )

            if self.agent.project_index is not None:
                kwargs["project"] = (
                    self.agent.project_index.project
                )

        # Search 계열 Skill(search_symbol/search_reference/
        # semantic_search)이 요구하는 인덱스 객체들도 workspace_path와
        # 같은 방식으로 자동 주입한다 — Planner/LLM은 kwargs로 검색어만
        # 채우면 되고, 인덱스를 어디서 가져오는지는 신경 쓸 필요가 없다.
        _AGENT_INDEX_ATTRS = (
            "project_index",
            "reference_index",
            "semantic_file_index",
        )

        for attr in _AGENT_INDEX_ATTRS:

            if attr not in sig.parameters or self.agent is None:
                continue
            supplied = kwargs.get(attr)
            # Preserve explicit non-primitive objects supplied by trusted
            # programmatic callers/tests, but replace obvious LLM hallucinations
            # such as integers/strings/dicts that can never be index objects.
            # This fixes the real failure mode ("'int' object has no attribute
            # symbols") without breaking the documented kwargs precedence.
            if attr not in kwargs or isinstance(supplied, (str, int, float, bool, list, tuple, dict)):
                kwargs[attr] = getattr(self.agent, attr, None)

        timeout = self._timeout_for(tool_name)
        retries = self._retries_for(tool_name)

        # Tool Retry: 총 시도 횟수 = 최초 1회 + retries.
        # status == "error"(예외, timeout 등 인프라성 실패)만
        # 재시도한다 — status == "failed"(예: verify_project의
        # 빌드/테스트 실패)는 코드를 고치지 않는 한 다시 실행해도
        # 똑같이 실패하므로 재시도가 아니라 Retry Loop의 replan으로
        # 보내는 게 맞다 (_is_retryable_failure 참고).
        last_result: dict[str, Any] | None = None

        for attempt in range(retries + 1):

            result = await self._execute_tool_once(
                tool_name, skill, kwargs, timeout,
            )

            if (
                self._is_retryable_failure(result)
                and attempt < retries
            ):
                last_result = result
                wait = self.tool_retry_backoff * (attempt + 1)

                print(
                    f"🔁 Tool '{tool_name}' 실행 실패 "
                    f"({attempt + 1}/{retries + 1}번째 시도): "
                    f"{result.get('message', '')} -> {wait:.1f}초 "
                    "후 재시도"
                )

                await asyncio.sleep(wait)
                continue

            if attempt > 0 and self._is_tool_result_success(result):
                print(
                    f"✅ Tool '{tool_name}' 재시도 후 성공"
                    f"({attempt + 1}번째 시도)"
                )

            return result

        # (도달하지 않음: 위 for 루프는 항상 return으로 종료된다)
        return last_result

    async def _execute_tool_once(
        self,
        tool_name: str,
        skill,
        kwargs: dict[str, Any],
        timeout: float | None,
    ) -> dict[str, Any]:
        """Tool을 정확히 한 번 실행한다 (재시도 없음)."""

        start = time.perf_counter()
        try:

            result = skill.execute(**kwargs)

            if inspect.isawaitable(result):

                if timeout is None:
                    result = await result
                else:
                    try:
                        result = await asyncio.wait_for(
                            result, timeout=timeout,
                        )
                    except asyncio.TimeoutError:
                        result = {
                            "status": "error",
                            "error_type": self.ERROR_TYPE_TIMEOUT,
                            "message": (
                                f"Tool '{tool_name}' 실행이 "
                                f"{timeout}초 안에 끝나지 않아 "
                                "타임아웃 처리했습니다."
                            ),
                        }
                        latency_ms = (time.perf_counter() - start) * 1000
                        self.metrics.tool_end(tool_name, result, latency_ms)
                        self.telemetry.log("tool_end", tool=tool_name, status=result["status"], error_type=result["error_type"], latency_ms=latency_ms)
                        return result

            if result is None:
                result = {
                    "status": "success",
                    "message": "done",
                }

            # Tool 자체가 예외 없이 status=error dict를 반환한 경우
            # (예: git_ops/patch_ops의 자체 검증 실패)에도 분류를
            # 붙여서, Tool Retry가 결정적 실패를 걸러낼 수 있게 한다.
            result = self._ensure_error_classified(result)
            latency_ms = (time.perf_counter() - start) * 1000
            self.metrics.tool_end(tool_name, result, latency_ms)
            self.telemetry.log(
                "tool_end", tool=tool_name, status=result.get("status"),
                error_type=result.get("error_type", ""), latency_ms=latency_ms,
            )
            if result.get("status") in ("error", "failed") and tool_name in self.FILE_MUTATING_TOOLS and self._last_mutation_snapshot:
                restored = self.snapshot_store.restore(self._last_mutation_snapshot)
                if restored:
                    result.setdefault("rollback", {"restored_files": restored, "reason": "mutation failed"})
            return result

        except Exception as e:

            result = {
                "status": "error",
                "error_type": self._classify_error(e, str(e)),
                "message": str(e),
            }
            latency_ms = (time.perf_counter() - start) * 1000
            self.metrics.tool_end(tool_name, result, latency_ms)
            self.telemetry.log(
                "tool_end", tool=tool_name, status="error",
                error_type=result["error_type"], latency_ms=latency_ms,
            )
            if tool_name in self.FILE_MUTATING_TOOLS and self._last_mutation_snapshot:
                restored = self.snapshot_store.restore(self._last_mutation_snapshot)
                if restored:
                    result["rollback"] = {"restored_files": restored, "reason": "mutation raised exception"}
            return result

    @staticmethod
    def _extract_json_objects(text: str) -> list[str]:
        """Extract complete JSON object spans without relying on regex nesting.

        Regex like ``\\{.*?\\}`` stops at the first closing brace, so a valid
        tool payload containing a nested ``kwargs`` object can be truncated and
        rejected by ``json.loads``. Use JSONDecoder.raw_decode over every opening
        brace instead; this correctly handles nested objects, arrays, escaped
        strings, and code-fenced JSON.
        """
        decoder = json.JSONDecoder()
        found: list[str] = []
        source = str(text or "")
        for index, char in enumerate(source):
            if char != "{":
                continue
            try:
                _, end = decoder.raw_decode(source[index:])
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            candidate = source[index:index + end]
            if candidate not in found:
                found.append(candidate)
        return found

    def _parse_tool(
        self,
        response: str,
    ) -> dict[str, Any] | None:
        """Parse a tool payload from normal or fenced JSON model output."""
        candidates: list[str] = []

        # Prefer explicit JSON code fences, but do not require them.
        for pattern in (self.TOOL_PATTERN, *self._TOOL_PATTERN_FALLBACKS[:1]):
            match = pattern.search(response or "")
            if match:
                candidates.append(match.group(1))
                break

        # Always add structurally complete JSON objects as a fallback.
        candidates.extend(
            item for item in self._extract_json_objects(response or "")
            if item not in candidates
        )

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

            if not isinstance(payload, dict):
                continue

            kwargs = payload.get("kwargs")
            if kwargs is None:
                kwargs = payload.get("args")
            if kwargs is None:
                kwargs = {}
            if not isinstance(kwargs, dict):
                continue

            return {
                "tool": payload.get("tool"),
                "kwargs": kwargs,
            }

        preview = (response or "").strip().replace("\n", " ")[:500]
        print(
            "⚠️ LLM 응답에서 완전한 kwargs JSON을 찾지 못했습니다. "
            f"응답 미리보기: {preview!r}"
        )
        return None

    def _tool_result_message(
        self,
        tool_name: str,
        result: Any,
    ) -> str:

        try:

            result_text = json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        except Exception:

            result_text = str(result)

        return (
            "[Tool Execution Result]\n\n"
            f"Tool: {tool_name}\n\n"
            f"{result_text}\n\n"
            "결과를 확인한 뒤 "
            "필요하면 다음 Tool을 호출하세요.\n"
            "모든 작업이 끝났다면 "
            "최종 답변만 출력하세요."
        )
