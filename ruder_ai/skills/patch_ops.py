"""Unified Diff Patch Generator/Applier skill for RuderAI Agent."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ruder_ai.patch import PatchApplier, PatchGenerator
from ruder_ai.skills.base import BaseSkill
from ruder_ai.skills.file_ops import _resolve_path


class GenerateDiffSkill(BaseSkill):
    name = "generate_diff"
    description = (
        "파일 하나에 대한 unified diff(@@)를 생성합니다. new_content를 주면 "
        "파일을 그 내용으로 수정/신규 생성하는 diff를, delete=true로 주면 "
        "파일을 삭제하는 diff를 만듭니다. 아직 파일을 실제로 바꾸지는 "
        "않습니다 — 실제 적용은 apply_patch Tool을 사용하세요."
    )

    def __init__(self) -> None:
        self._generator = PatchGenerator()

    async def execute(
        self,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        new_content: Optional[str] = None,
        delete: bool = False,
        workspace_path: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:

        target = file_path or path

        if not target:
            return {"status": "error", "message": "file_path가 필요합니다."}

        if not delete and new_content is None:
            return {
                "status": "error",
                "message": (
                    "new_content가 필요합니다 (파일을 삭제하려면 "
                    "delete=true를 사용하세요)."
                ),
            }

        try:
            file_path_obj = _resolve_path(target, workspace_path)
        except Exception as e:
            return {"status": "error", "message": str(e)}

        old_content: Optional[str] = None

        if file_path_obj.exists():
            if not file_path_obj.is_file():
                return {
                    "status": "error",
                    "message": f"파일이 아닙니다: {target}",
                }
            try:
                old_content = file_path_obj.read_text(
                    encoding="utf-8", errors="replace"
                )
            except Exception as e:
                return {"status": "error", "message": str(e)}

        new_content_value = None if delete else new_content

        if old_content is None and delete:
            return {
                "status": "error",
                "message": f"삭제할 파일이 존재하지 않습니다: {target}",
            }

        diff_text = self._generator.generate(
            file_path=target,
            old_content=old_content,
            new_content=new_content_value,
        )

        if not diff_text:
            return {
                "status": "success",
                "message": "변경 사항이 없어 diff를 생성하지 않았습니다.",
                "diff": "",
            }

        return {
            "status": "success",
            "message": f"{target}에 대한 diff 생성 완료",
            "diff": diff_text,
        }


class ApplyPatchSkill(BaseSkill):
    name = "apply_patch"
    description = (
        "generate_diff로 만든 unified diff를 워크스페이스에 실제로 "
        "적용합니다(`git apply` 기반). 적용 전 `--check`로 먼저 검증하고, "
        "검증에 실패하면 파일을 건드리지 않고 실패를 반환합니다."
    )

    def __init__(self) -> None:
        self._applier = PatchApplier()

    async def execute(
        self,
        diff: Optional[str] = None,
        patch: Optional[str] = None,
        workspace_path: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:

        diff_text = diff or patch

        if not diff_text:
            return {"status": "error", "message": "diff가 필요합니다."}

        result = await self._applier.apply(
            diff_text=diff_text,
            workspace=workspace_path or ".",
        )

        if not result.success:
            return {
                "status": "error",
                "message": result.message or "patch 적용에 실패했습니다.",
                "failure_log": result.log_text(),
            }

        return {
            "status": "success",
            "message": "patch 적용 완료",
        }


class RollbackPatchSkill(BaseSkill):
    name = "rollback_patch"
    description = (
        "apply_patch로 이미 적용한 unified diff를 그대로 넘기면 "
        "`git apply --reverse`로 역적용해서 변경 전 상태로 되돌립니다. "
        "역적용 전에도 사전 검증을 거칩니다."
    )

    def __init__(self) -> None:
        self._applier = PatchApplier()

    async def execute(
        self,
        diff: Optional[str] = None,
        patch: Optional[str] = None,
        workspace_path: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:

        diff_text = diff or patch

        if not diff_text:
            return {"status": "error", "message": "diff가 필요합니다."}

        result = await self._applier.rollback(
            diff_text=diff_text,
            workspace=workspace_path or ".",
        )

        if not result.success:
            return {
                "status": "error",
                "message": result.message or "rollback에 실패했습니다.",
                "failure_log": result.log_text(),
            }

        return {
            "status": "success",
            "message": "rollback 완료",
        }
