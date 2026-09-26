"""`git apply` 기반 diff 적용기.

TODO.md의 "Git Apply 지원 / Patch 검증 / Rollback" 항목 구현.

`git apply`는 `.git` 저장소가 아닌 일반 디렉토리에서도 동작한다
(git 저장소 여부와 무관하게, 워킹 디렉토리 기준으로 patch(1) 처럼
동작). 그래서 이 클래스는 workspace가 git 저장소인지 확인하지 않고
그냥 `git apply`를 서브프로세스로 실행한다.

적용/역적용 전에는 항상 `--check`로 먼저 검증하고, 검증에 실패하면
파일을 전혀 건드리지 않고 바로 실패를 반환한다.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from .models import PatchResult
from ruder_ai.core.platform import PLATFORM


class PatchApplier:
    """`git apply`를 사용해 unified diff를 검증/적용/역적용한다."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    async def check(
        self,
        diff_text: str,
        workspace: Path | str,
        reverse: bool = False,
    ) -> PatchResult:
        """`git apply --check`로 실제 적용 전 사전 검증만 한다."""

        args = ["--check"]

        if reverse:
            args.append("--reverse")

        return await self._run_git_apply(diff_text, workspace, args)

    async def apply(
        self,
        diff_text: str,
        workspace: Path | str,
    ) -> PatchResult:
        """diff를 검증 후 실제로 적용한다.

        검증(`--check`)에 실패하면 파일을 건드리지 않고 바로 실패를
        반환한다.
        """

        check_result = await self.check(diff_text, workspace)

        if not check_result.success:
            return PatchResult(
                success=False,
                message="Patch 검증 실패 (적용 전 --check 단계): 파일을 건드리지 않았습니다.",
                command=check_result.command,
                stdout=check_result.stdout,
                stderr=check_result.stderr,
                returncode=check_result.returncode,
            )

        return await self._run_git_apply(diff_text, workspace, [])

    async def rollback(
        self,
        diff_text: str,
        workspace: Path | str,
    ) -> PatchResult:
        """이미 적용된 diff를 `git apply --reverse`로 역적용해 원복한다.

        역적용 전에도 `--reverse --check`로 먼저 사전 검증한다 (예:
        diff가 실제로 적용된 상태가 아니면 역적용도 실패해야 한다).
        """

        check_result = await self.check(diff_text, workspace, reverse=True)

        if not check_result.success:
            return PatchResult(
                success=False,
                message="Rollback 검증 실패 (--reverse --check 단계): 파일을 건드리지 않았습니다.",
                command=check_result.command,
                stdout=check_result.stdout,
                stderr=check_result.stderr,
                returncode=check_result.returncode,
            )

        return await self._run_git_apply(diff_text, workspace, ["--reverse"])

    async def _run_git_apply(
        self,
        diff_text: str,
        workspace: Path | str,
        extra_args: list[str],
    ) -> PatchResult:

        workspace = Path(workspace)

        git_executable = PLATFORM.which("git")

        if git_executable is None:
            return PatchResult(
                success=False,
                message="git 실행 파일을 찾을 수 없습니다.",
            )

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".patch",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(diff_text)
            patch_path = tmp.name

        args = [
            git_executable,
            "apply",
            *extra_args,
            patch_path,
        ]

        command = " ".join(["git", "apply", *extra_args, "<patch>"])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=PLATFORM.subprocess_env(),
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return PatchResult(
                    success=False,
                    message=f"제한 시간({self.timeout}초)을 초과했습니다.",
                    command=command,
                )

            returncode = proc.returncode

            return PatchResult(
                success=(returncode == 0),
                message="" if returncode == 0 else "git apply 실패",
                command=command,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                returncode=returncode,
            )

        except Exception as e:
            return PatchResult(
                success=False,
                message=str(e),
                command=command,
            )

        finally:
            Path(patch_path).unlink(missing_ok=True)
