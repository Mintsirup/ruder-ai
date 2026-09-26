"""Git operation skill for RuderAI Agent."""
import asyncio
import shlex
from typing import Any, Dict, Optional
from ruder_ai.skills.base import BaseSkill
from ruder_ai.core.platform import PLATFORM


async def _run_git(command: str, workspace_path: Optional[str]) -> Dict[str, Any]:
    """`git {command}`를 workspace_path에서 실행하고 결과를 dict로 반환."""

    cwd = workspace_path or "."

    try:
        parts = shlex.split(command, posix=not PLATFORM.is_windows)
    except ValueError as exc:
        return {"status": "error", "message": f"Git 인자 파싱 실패: {exc}"}

    git = PLATFORM.which("git")
    if not git:
        return {"status": "error", "message": "git 실행 파일을 찾을 수 없습니다."}

    try:
        proc = await asyncio.create_subprocess_exec(
            git, *parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            # git quotes non-ASCII paths in its output using the locale
            # code page unless told otherwise; the result is decoded as UTF-8
            # below, so a Korean filename in `git status` would come back
            # mojibake otherwise.
            env=PLATFORM.subprocess_env(),
        )
        stdout, stderr = await proc.communicate()

        # returncode != 0(예: "not a git repository")를 무조건 success로
        # 보고하면 Executor/Planner가 실패를 성공으로 착각한다. returncode
        # 기준으로 status를 결정한다.
        return {
            "status": "success" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }
    except Exception as e:
        return {"status": "error", "message": f"Git 명령어 실행 실패: {str(e)}"}


class GitSkill(BaseSkill):
    name = "git_command"
    description = "Git 명령어를 실행합니다 (예: status, diff, log, commit)."

    async def execute(self, command: str, workspace_path: str = None, **kwargs) -> Dict[str, Any]:
        """Git 명령어 안전 실행"""
        # 허용된 안전 명령어 패턴만 실행
        allowed_subcmds = ["status", "diff", "log", "branch", "add", "commit", "checkout"]
        subcmd = command.strip().split()[0] if command else ""

        if subcmd not in allowed_subcmds:
            return {
                "status": "error",
                "message": f"허용되지 않은 git 서브명령어입니다: {subcmd}. ({', '.join(allowed_subcmds)}만 허용)"
            }

        return await _run_git(command, workspace_path)


# 아래 세 개(git_status/git_diff/git_checkout)는 GitSkill(git_command)로도
# 실행 가능하지만, 그러려면 매번 정확한 command 문자열("diff --stat" 등)을
# 모델이 직접 조립해야 한다. create_directory를 도입했을 때와 같은 이유로
# — 특히 작은 로컬 모델일수록 자유 형식 문자열 조립보다 "필드 몇 개만
# 채우면 되는" 전용 Tool 쪽이 훨씬 안정적으로 성공한다 — Planner가 자주
# 쓸 법한 용도별로 전용 Tool을 따로 둔다.


class GitStatusSkill(BaseSkill):
    name = "git_status"
    description = "현재 작업 디렉터리의 git 상태(변경/추가/삭제된 파일 목록)를 확인합니다. 파라미터가 필요 없습니다."

    async def execute(self, workspace_path: str = None, **kwargs) -> Dict[str, Any]:
        return await _run_git("status --porcelain=v1", workspace_path)


class GitDiffSkill(BaseSkill):
    name = "git_diff"
    description = (
        "git이 추적 중인 변경 사항의 diff를 보여줍니다. file_path를 주면 "
        "그 파일만, 생략하면 전체 변경 사항을 보여줍니다."
    )

    async def execute(
        self,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        workspace_path: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        target = file_path or path
        command = f"diff -- {target}" if target else "diff"
        return await _run_git(command, workspace_path)


class GitCheckoutSkill(BaseSkill):
    name = "git_checkout"
    description = (
        "지정한 파일을 마지막 git 커밋 상태로 되돌립니다 (rollback 용도). "
        "file_path가 필수입니다. 커밋되지 않은 로컬 수정 사항이 사라지니 "
        "주의하세요."
    )

    async def execute(
        self,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        workspace_path: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        target = file_path or path

        if not target:
            return {"status": "error", "message": "file_path가 필요합니다."}

        return await _run_git(f"checkout -- {target}", workspace_path)
