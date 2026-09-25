"""RuderAI Reflection / Self-Verification Layer.

에이전트가 "작업 완료"를 선언하기 전에 스스로 결과를 점검하는 단계.

검증은 두 단계로 이루어진다:

1. 정적 검사 (Static Check)
   이번 턴에서 수정된 파일 중 정적으로 검증 가능한 것들
   (현재는 .py 구문 검사)에 대해 실제로 파싱해보고 오류를 잡는다.
   LLM에게 다시 묻지 않고 확정적으로 판단할 수 있는 부분이라
   가장 먼저, 가장 싼 비용으로 수행한다.

2. LLM 기반 자기 비평 (Self-Critique)
   "원래 요청 vs 변경된 파일 vs 최종 답변"을 놓고 별도의 LLM 호출로
   다시 검토시킨다. 작업을 직접 수행한 것과 같은 컨텍스트(대화 이력)를
   그대로 이어서 판단하게 하면 같은 실수를 반복하기 쉬우므로,
   독립된 짧은 대화로 새로 질문한다.

Executor는 이 결과(ReflectionResult)를 보고:
- passed=True  -> 최종 답변을 그대로 사용자에게 반환
- passed=False -> feedback을 대화에 주입하고 한 번 더 시도
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from ruder_ai.context.token_budget import truncate_to_budget


@dataclass
class ReflectionResult:
    """자기 검증 1회의 결과."""

    passed: bool
    feedback: str = ""
    checked_files: list[str] = field(default_factory=list)


class Reflector:
    """작업 완료 직전에 결과를 검증하는 Reflector."""

    # 정적 구문 검사를 지원하는 확장자.
    # (Java 등은 컴파일러가 없으면 검사 불가하므로 우선 Python만 지원,
    #  이후 javac 연동 등으로 확장 가능)
    SYNTAX_CHECKABLE_EXTENSIONS = {".py"}

    # LLM 자기 비평 프롬프트에 실제로 포함할 "변경된 파일 내용" 총량의
    # 토큰 예산. 파일명/최종 답변 텍스트만으로는 근거 없는 추측이 되기
    # 쉬워서, 실제 내용을 보여주되 예산을 넘으면 head+tail로 압축한다
    # (context/token_budget.py와 동일한 접근).
    REVIEW_FILE_CONTENT_BUDGET = 2400

    def __init__(
        self,
        llm,
        workspace_path: str | Path,
        max_reflections: int = 2,
        file_content_budget: int = REVIEW_FILE_CONTENT_BUDGET,
    ):
        self.llm = llm
        self.workspace_path = Path(workspace_path)
        self.max_reflections = max(0, int(max_reflections))
        self.file_content_budget = max(256, int(file_content_budget))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def review(
        self,
        task: str,
        final_answer: str,
        changed_files: list[str],
    ) -> ReflectionResult:
        """최종 답변을 검증한다.

        정적 검사에서 문제가 발견되면 LLM 호출 없이 바로 실패로 반환한다.
        (구문 오류는 자명한 사실이라 LLM 의견을 물을 필요가 없다.)
        """

        syntax_problems = self._check_syntax(changed_files)

        if syntax_problems:

            feedback = (
                "다음 파일에서 구문 오류가 발견되었습니다. "
                "해당 파일을 다시 확인하고 수정하세요:\n"
                + "\n".join(f"- {p}" for p in syntax_problems)
            )

            return ReflectionResult(
                passed=False,
                feedback=feedback,
                checked_files=changed_files,
            )

        if not changed_files:
            # 파일 변경이 전혀 없었던 턴(단순 질의응답 등)은
            # 굳이 LLM 자기비평을 태우지 않는다.
            return ReflectionResult(passed=True, checked_files=[])

        verdict = await self._self_critique(
            task=task,
            final_answer=final_answer,
            changed_files=changed_files,
        )

        return verdict

    # ------------------------------------------------------------------
    # Static check
    # ------------------------------------------------------------------

    def _check_syntax(self, changed_files: list[str]) -> list[str]:
        """수정된 파일 중 구문 검사 가능한 것들을 검사해 문제 목록을 반환."""

        problems: list[str] = []

        for rel_path in changed_files:

            suffix = Path(rel_path).suffix

            if suffix not in self.SYNTAX_CHECKABLE_EXTENSIONS:
                continue

            full_path = self.workspace_path / rel_path

            if not full_path.exists():
                # 삭제된 파일이면 검사 대상이 아님
                continue

            try:
                source = full_path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                ast.parse(source, filename=rel_path)

            except SyntaxError as e:

                problems.append(
                    f"{rel_path} (line {e.lineno}): {e.msg}"
                )

        return problems

    # ------------------------------------------------------------------
    # LLM self-critique
    # ------------------------------------------------------------------

    async def _self_critique(
        self,
        task: str,
        final_answer: str,
        changed_files: list[str],
    ) -> ReflectionResult:

        file_contents = self._file_contents_block(changed_files)

        messages = [
            {
                "role": "system",
                "content": self._critic_system_prompt(),
            },
            {
                "role": "user",
                "content": self._critic_user_prompt(
                    task=task,
                    final_answer=final_answer,
                    changed_files=changed_files,
                    file_contents=file_contents,
                ),
            },
        ]

        response = await self.llm.chat(messages)
        response = (response or "").strip()

        if response.upper().startswith("OK"):
            return ReflectionResult(
                passed=True,
                checked_files=changed_files,
            )

        feedback = response

        if feedback.upper().startswith("ISSUE:"):
            feedback = feedback[len("ISSUE:"):].strip()

        if not feedback:
            feedback = "요청이 완전히 충족되지 않은 것 같습니다."

        return ReflectionResult(
            passed=False,
            feedback=feedback,
            checked_files=changed_files,
        )

    @staticmethod
    def _critic_system_prompt() -> str:

        return (
            "당신은 방금 작업을 마친 코딩 에이전트를 감독하는 "
            "깐깐한 시니어 리뷰어입니다.\n\n"
            "아래에 원래 요청, 이번 턴에서 변경된 파일들의 실제 내용, "
            "에이전트의 최종 답변이 주어집니다.\n"
            "반드시 '변경된 파일 내용'에 실제로 있는지 없는지를 "
            "근거로 판단하세요. 파일 내용을 읽지 않고 최종 답변의 "
            "주장만으로 판단하지 마세요.\n\n"
            "판단 기준:\n"
            "- 요청에서 명시적으로 요구한 함수/클래스/기능이 파일 "
            "내용에 실제로 존재하는가\n"
            "- 최종 답변이 '했다고 주장'만 하고 파일 내용과 모순되지는 "
            "않는가\n"
            "- 파일 내용에서 명백히 빠뜨린 부분이 보이는가\n\n"
            "주의:\n"
            "- 파일 내용 중 '(생략: 예산 초과로 압축됨)'이라고 표시된 "
            "부분은 예산 제한 때문에 보여주지 못한 것일 뿐, 실제로 "
            "빠졌다는 뜻이 아닙니다. 그 구간에 대해서는 있다/없다를 "
            "단정하지 말고, 보이는 범위 안에서만 판단하세요.\n"
            "- 파일 내용에 없는 문제를 지어내거나, 파일 내용에 있는데 "
            "없다고 우기지 마세요.\n\n"
            "충족되었다면 다른 말 없이 정확히 'OK' 한 단어만 출력하세요.\n"
            "충족되지 않았다면 'ISSUE:' 로 시작해서, 파일 내용의 어느 "
            "부분을 근거로 무엇이 부족한지 한두 문장으로 구체적으로 "
            "지적하세요.\n"
            "애매하면 관대하게 'OK'로 판단하세요 "
            "(불확실한 트집을 잡아 무한 반복시키지 마세요)."
        )

    @staticmethod
    def _critic_user_prompt(
        task: str,
        final_answer: str,
        changed_files: list[str],
        file_contents: str,
    ) -> str:

        files_list = (
            "\n".join(f"- {f}" for f in changed_files)
            or "(없음)"
        )

        return (
            f"[원래 요청]\n{task}\n\n"
            f"[이번 턴에서 변경된 파일 목록]\n{files_list}\n\n"
            f"[변경된 파일 내용]\n{file_contents}\n\n"
            f"[에이전트의 최종 답변]\n{final_answer}\n"
        )

    def _file_contents_block(self, changed_files: list[str]) -> str:
        """검증 대상 파일들의 실제 내용을 읽어 리뷰 프롬프트에 넣을
        블록을 만든다.

        파일명과 최종 답변 텍스트만으로는 LLM이 근거 없이 추측하게
        되기 쉬우므로, 실제로 무엇이 바뀌었는지 보여준다. 전체 blob은
        REVIEW_FILE_CONTENT_BUDGET 토큰 예산 안에서 head+tail로
        압축한다 (파일이 많거나 클수록 뒷부분이 잘릴 수 있음).
        """

        if not changed_files:
            return "(없음)"

        sections: list[str] = []

        for rel_path in changed_files:

            full_path = self.workspace_path / rel_path

            if not full_path.exists():
                sections.append(
                    f"### {rel_path}\n(삭제됨 또는 존재하지 않음)"
                )
                continue

            try:
                content = full_path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except Exception as e:
                sections.append(f"### {rel_path}\n(읽기 실패: {e})")
                continue

            sections.append(f"### {rel_path}\n```\n{content}\n```")

        combined = "\n\n".join(sections)

        return truncate_to_budget(
            combined,
            self.file_content_budget,
        )
