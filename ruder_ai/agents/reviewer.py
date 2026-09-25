"""Evidence-based review agent."""
from __future__ import annotations
from .base import AgentRole, BaseRoleAgent

class ReviewerAgent(BaseRoleAgent):
    role = AgentRole("Reviewer", "원본 요청·실제 파일·변경 결과의 근거 일치 여부를 검토", False, False)

    async def review(
        self,
        changed_files: list[str],
        task: str,
        final_answer: str = "",
        evidence_text: str = "",
        context_files: list[str] | None = None,
        original_files: dict[str, str] | None = None,
    ) -> tuple[bool, str]:
        if not self.llm:
            return True, ""

        review_files = changed_files or list(context_files or [])
        files_block = self._read_changed_files(review_files)
        original_block = self._format_original_files(original_files or {})
        messages = [
            {"role": "system", "content": self.system_prefix() + self._system_prompt()},
            {"role": "user", "content": (
                f"원본 요청:\n{task}\n\n"
                f"변경 파일의 실제 현재 내용:\n{files_block}\n\n"
                f"변경 전 실제 원본 내용:\n{original_block}\n\n"
                f"실행 Agent의 최종 답변:\n{final_answer}\n\n"
                f"Explorer의 결정론적 사실 근거:\n{evidence_text or '[없음]'}\n\n"
                "중요: 오류가 수정된 경우 현재 파일에서는 해당 오류 텍스트가 사라질 수 있습니다.\n"
                "그 경우 변경 전 원본 내용과 비교해서 실제 오류였는지 판단하세요.\n"
                "변경 전/후 어디에도 근거가 없는 오류나 변경을 주장하면 ISSUE로 판정하세요.\n"
                "특정 함수명, 문자열, 로그 문구, 클래스명, 경로를 ISSUE 근거로 제시할 때는 위에 실제로 표시된 내용에서 확인된 것만 사용하세요. 표시되지 않았거나 기억/추측에 의존한 내용은 근거로 쓰지 마세요. 확실한 근거가 없으면 ISSUE 대신 OK입니다.\n"
                "반드시 첫 줄을 OK 또는 ISSUE: 로 시작하세요."
            )},
        ]
        response = (await self.llm.chat(messages) or "").strip()
        if response.upper().startswith("OK"):
            return True, ""
        if response.upper().startswith("ISSUE:"):
            return False, response.split(":", 1)[1].strip()
        return False, response or "Reviewer가 명확한 판정을 반환하지 않았습니다."

    @staticmethod
    def _format_original_files(original_files: dict[str, str]) -> str:
        if not original_files:
            return "[원본 스냅샷 없음]"
        blocks = []
        for rel, content in list(original_files.items())[:8]:
            blocks.append(f"### {rel}\n{content}")
        return "\n\n".join(blocks)

    def _read_changed_files(self, changed_files: list[str]) -> str:
        blocks = []
        for rel in changed_files[:8]:
            resolved = self.file_resolver.resolve(rel)
            path = (self.workspace_path / resolved) if resolved else (self.workspace_path / rel)
            if not path.exists():
                blocks.append(f"### {rel}\n[삭제됨 또는 존재하지 않음]")
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                blocks.append(f"### {rel}\n[읽기 실패: {exc}]")
                continue
            if len(content) > 12000:
                content = content[:6000] + "\n...\n" + content[-6000:]
            blocks.append(f"### {rel}\n{content}")
        return "\n\n".join(blocks) if blocks else "[변경 파일 없음]"

    @staticmethod
    def _system_prompt() -> str:
        return (
            "코드 변경의 사실성을 검증합니다.\n"
            "- 파일에 실제로 존재하지 않는 텍스트를 오류라고 단정하지 마세요.\n"
            "- 실행 Agent가 주장한 수정이 실제 파일 내용과 일치하는지 확인하세요.\n"
            "- 근거가 부족하면 ISSUE로 보수적으로 판정하세요."
        )
