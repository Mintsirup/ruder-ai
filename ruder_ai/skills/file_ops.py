"""File operation skills for RuderAI Agent."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

from ruder_ai.patch import PatchGenerator
from ruder_ai.core.mutation_guard import MutationGuard
from ruder_ai.skills.base import BaseSkill


class ReadFileSkill(BaseSkill):
    name = "read_file"
    description = "지정한 경로의 파일 내용을 읽습니다."

    async def execute(
        self,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        workspace_path: Optional[str] = None,
        **kwargs,
    ):
        target = file_path or path
        if not target:
            return {"status": "error", "message": "file_path가 필요합니다."}

        try:
            file_path_obj, corrected = _resolve_existing_file_path(target, workspace_path)

            if not file_path_obj.exists():
                return {"status": "error", "message": f"파일이 존재하지 않습니다: {target}"}

            if not file_path_obj.is_file():
                return {"status": "error", "message": f"파일이 아닙니다: {target}"}

            content = file_path_obj.read_text(encoding="utf-8", errors="replace")

            result = {"status": "success", "content": content, "file_path": str(file_path_obj)}
            if corrected:
                result["message"] = (
                    f"요청 경로 '{target}'를 실제 파일 '{corrected}'로 자동 보정했습니다."
                )
                result["corrected_from"] = target
                result["corrected_to"] = corrected
            return result

        except Exception as e:
            return {"status": "error", "message": str(e)}


class WriteFileSkill(BaseSkill):
    name = "write_file"
    description = (
        "지정한 경로에 파일 내용을 생성하거나 덮어씁니다. 파일 전체를 새로 "
        "쓰므로 큰 파일의 일부만 고칠 때는 patch_file을 우선 사용하세요. "
        "임시 파일에 먼저 쓰고 원자적으로 교체하므로 도중에 실패해도 원본이 "
        "깨지지 않습니다. 기존 파일 내용을 실제로 바꾸는 경우에는 덮어쓰기 "
        "전에 자동으로 백업을 남기고 결과의 backup_path로 알려줍니다 "
        "(restore_backup으로 되돌릴 수 있음)."
    )

    async def execute(
        self,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        content: str = "",
        workspace_path: Optional[str] = None,
        **kwargs,
    ):
        target = file_path or path
        if not target:
            return {"status": "error", "message": "file_path가 필요합니다."}

        try:
            file_path_obj = _resolve_path(target, workspace_path)

            task = str(kwargs.get("__task", ""))
            if not file_path_obj.exists() and task and not MutationGuard.task_allows_new_symbols(task):
                lowered = task.lower()
                modify_words = (
                    "수정", "변경", "고쳐", "바꿔", "리팩토링",
                    "fix", "modify", "change", "update",
                )
                if any(word in lowered for word in modify_words):
                    return {
                        "status": "error",
                        "error_type": "validation",
                        "message": (
                            f"요청한 수정 대상 파일 '{target}'이 존재하지 않습니다. "
                            "존재하지 않는 파일을 임의로 생성하지 않습니다. "
                            "새 파일 생성이 목적이면 명시적으로 생성/추가를 요청하세요."
                        ),
                        "guard": "missing_target_file",
                    }

            file_path_obj.parent.mkdir(parents=True, exist_ok=True)

            backup_path_str = None
            if file_path_obj.exists() and file_path_obj.is_file():
                existing = file_path_obj.read_text(
                    encoding="utf-8", errors="replace"
                )
                guard = MutationGuard().validate_text_mutation(
                    task=str(kwargs.get("__task", "")),
                    original=existing,
                    generated=content,
                    operation="write_file",
                    file_path=str(file_path_obj),
                )
                if not guard.allowed:
                    return {
                        "status": "error",
                        "error_type": "validation",
                        "message": guard.message,
                        "guard": "missing_target_symbol",
                        "symbol": guard.symbol,
                    }
                if existing != content:
                    backup_path_str = _create_backup(file_path_obj, workspace_path)

            _atomic_write_text(file_path_obj, content)

            result = {"status": "success", "message": f"{target} 작성 완료"}
            if backup_path_str:
                result["backup_path"] = backup_path_str
            return result

        except Exception as e:
            return {"status": "error", "message": str(e)}


class CreateDirectorySkill(BaseSkill):
    name = "create_directory"
    description = (
        "지정한 경로에 빈 디렉터리를 생성합니다 (필요한 상위 경로도 함께 "
        "생성). 이미 존재하면 그대로 성공 처리합니다. 'mkdir', 'make_dir', "
        "'create_folder' 등으로도 불리는 동일한 작업입니다."
    )

    async def execute(
        self,
        dir_path: Optional[str] = None,
        path: Optional[str] = None,
        file_path: Optional[str] = None,
        workspace_path: Optional[str] = None,
        **kwargs,
    ):
        target = dir_path or path or file_path
        if not target:
            return {"status": "error", "message": "dir_path가 필요합니다."}

        try:
            dir_path_obj = _resolve_path(target, workspace_path)
            dir_path_obj.mkdir(parents=True, exist_ok=True)

            return {"status": "success", "message": f"{target} 디렉터리 생성 완료"}

        except Exception as e:
            return {"status": "error", "message": str(e)}


class PatchFileSkill(BaseSkill):
    name = "patch_file"
    description = (
        "파일 안의 old_str 텍스트를 new_str로 치환합니다. old_str은 파일 안에서 "
        "정확히 한 번만 일치해야 합니다. 파일 전체를 다시 쓰지 않고 필요한 부분만 "
        "바꾸므로, 큰 파일을 일부만 수정할 때는 write_file 대신 이 도구를 사용하세요. "
        "실제로 적용하기 전에 결과를 먼저 보고 싶다면 preview_patch를 먼저 "
        "사용하세요. 원자적 교체로 쓰고, 적용 전 자동으로 백업을 남깁니다 "
        "(결과의 backup_path, restore_backup으로 복원 가능)."
    )

    async def execute(
        self,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        old_str: str = "",
        new_str: str = "",
        workspace_path: Optional[str] = None,
        **kwargs,
    ):
        target = file_path or path
        if not target:
            return {"status": "error", "message": "file_path가 필요합니다."}

        try:
            file_path_obj, corrected = _resolve_existing_file_path(target, workspace_path)

            if not file_path_obj.exists():
                return {"status": "error", "message": f"파일이 존재하지 않습니다: {target}"}

            original = file_path_obj.read_text(encoding="utf-8", errors="replace")

            updated, error = _replace_unique(original, old_str, new_str)
            if error:
                return {"status": "error", "message": error}

            guard = MutationGuard().validate_text_mutation(
                task=str(kwargs.get("__task", "")),
                original=original,
                generated=updated,
                operation="patch_file",
                file_path=str(file_path_obj),
            )
            if not guard.allowed:
                return {
                    "status": "error",
                    "error_type": "validation",
                    "message": guard.message,
                    "guard": "missing_target_symbol",
                    "symbol": guard.symbol,
                }

            if updated == original:
                # old_str이 new_str과 같거나, 치환 결과가 우연히 원본과
                # 동일한 경우. 파일은 실제로 바뀌지 않았으므로 이를
                # "패치 완료"(success)로 보고하면 안 된다 — 상위
                # Orchestrator/Reflector가 실제로 수정이 반영됐다고
                # 오판해 검증을 건너뛰게 된다.
                result = {
                    "status": "no_change",
                    "message": (
                        f"{target}: old_str과 new_str이 동일하거나 치환 "
                        "결과가 원본과 같아 파일이 변경되지 않았습니다."
                    ),
                    "file_path": str(file_path_obj),
                }
                if corrected:
                    result["corrected_from"] = target
                    result["corrected_to"] = corrected
                return result

            backup_path_str = _create_backup(file_path_obj, workspace_path)

            _atomic_write_text(file_path_obj, updated)

            result = {
                "status": "success",
                "message": f"{target} 패치 완료 (1개 위치 치환)",
                "file_path": str(file_path_obj),
            }
            if corrected:
                result["message"] = (
                    f"요청 경로 '{target}'를 실제 파일 '{corrected}'로 자동 보정한 뒤 "
                    "패치했습니다."
                )
                result["corrected_from"] = target
                result["corrected_to"] = corrected
            if backup_path_str:
                result["backup_path"] = backup_path_str
            return result

        except Exception as e:
            return {"status": "error", "message": str(e)}


class AppendFileSkill(BaseSkill):
    name = "append_file"
    description = "지정한 경로의 파일 끝에 내용을 추가합니다. 파일이 없으면 새로 생성합니다."

    async def execute(
        self,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        content: str = "",
        workspace_path: Optional[str] = None,
        **kwargs,
    ):
        target = file_path or path
        if not target:
            return {"status": "error", "message": "file_path가 필요합니다."}

        try:
            file_path_obj = _resolve_path(target, workspace_path)
            file_path_obj.parent.mkdir(parents=True, exist_ok=True)

            with file_path_obj.open("a", encoding="utf-8") as f:
                f.write(content)

            return {"status": "success", "message": f"{target}에 {len(content)}자 추가 완료"}

        except Exception as e:
            return {"status": "error", "message": str(e)}


class DeleteFileSkill(BaseSkill):
    name = "delete_file"
    description = (
        "지정한 경로의 파일을 삭제합니다. 삭제 전에 자동으로 백업을 남기고 "
        "결과의 backup_path로 알려줍니다 (restore_backup으로 복원 가능)."
    )

    async def execute(
        self,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        workspace_path: Optional[str] = None,
        **kwargs,
    ):
        target = file_path or path
        if not target:
            return {"status": "error", "message": "file_path가 필요합니다."}

        try:
            file_path_obj = _resolve_path(target, workspace_path)

            if not file_path_obj.exists():
                return {"status": "error", "message": f"파일이 존재하지 않습니다: {target}"}

            if not file_path_obj.is_file():
                return {
                    "status": "error",
                    "message": f"파일이 아닙니다(디렉토리는 삭제할 수 없습니다): {target}",
                }

            backup_path_str = _create_backup(file_path_obj, workspace_path)

            file_path_obj.unlink()

            return {
                "status": "success",
                "message": f"{target} 삭제 완료",
                "backup_path": backup_path_str,
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}


class PreviewPatchSkill(BaseSkill):
    name = "preview_patch"
    description = (
        "write_file/patch_file/delete_file을 실제로 실행하기 전에 결과가 "
        "어떻게 바뀔지 unified diff로 미리 보여줍니다. 디스크는 전혀 "
        "건드리지 않습니다 (백업도 남기지 않음). new_content를 주면 "
        "write_file 스타일 미리보기를, old_str/new_str을 주면 patch_file "
        "스타일 미리보기를, delete=true를 주면 delete_file 미리보기를 "
        "생성합니다."
    )

    def __init__(self) -> None:
        self._generator = PatchGenerator()
        self._mutation_guard = MutationGuard()

    async def execute(
        self,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        new_content: Optional[str] = None,
        old_str: Optional[str] = None,
        new_str: Optional[str] = None,
        delete: bool = False,
        workspace_path: Optional[str] = None,
        **kwargs,
    ):
        target = file_path or path
        if not target:
            return {"status": "error", "message": "file_path가 필요합니다."}

        try:
            file_path_obj, corrected = _resolve_existing_file_path(target, workspace_path)
        except Exception as e:
            return {"status": "error", "message": str(e)}

        old_content: Optional[str] = None
        if file_path_obj.exists():
            if not file_path_obj.is_file():
                return {"status": "error", "message": f"파일이 아닙니다: {target}"}
            old_content = file_path_obj.read_text(encoding="utf-8", errors="replace")

        if delete:
            if old_content is None:
                return {
                    "status": "error",
                    "message": f"삭제할 파일이 존재하지 않습니다: {target}",
                }
            new_content_value: Optional[str] = None
        elif new_content is not None:
            new_content_value = new_content
        elif old_str:
            if old_content is None:
                return {"status": "error", "message": f"파일이 존재하지 않습니다: {target}"}
            new_content_value, error = _replace_unique(
                old_content, old_str, new_str or ""
            )
            if error:
                return {"status": "error", "message": error}
        else:
            return {
                "status": "error",
                "message": (
                    "new_content, old_str/new_str, delete=true 중 하나가 "
                    "필요합니다."
                ),
            }

        effective_target = (
            corrected
            if corrected
            else target
        )
        diff_text = self._generator.generate(
            file_path=effective_target, old_content=old_content, new_content=new_content_value
        )

        if not diff_text:
            return {
                "status": "success",
                "message": "변경 사항이 없어 미리보기가 비어 있습니다.",
                "diff": "",
                "changed": False,
            }

        return {
            "status": "success",
            "message": f"{target}에 대한 미리보기 생성 완료 (디스크는 변경되지 않았습니다)",
            "diff": diff_text,
            "changed": True,
        }


class BackupFileSkill(BaseSkill):
    name = "backup_file"
    description = (
        "지정한 파일의 현재 내용을 워크스페이스의 .ruder_ai_backups 디렉터리에 "
        "복사해 백업을 남깁니다. write_file/patch_file/delete_file은 내용이 "
        "바뀔 때 이미 자동으로 백업을 남기므로, 이 Tool은 그 자동 백업 시점과 "
        "무관하게 지금 상태를 명시적으로 남기고 싶을 때 사용하세요."
    )

    async def execute(
        self,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        workspace_path: Optional[str] = None,
        **kwargs,
    ):
        target = file_path or path
        if not target:
            return {"status": "error", "message": "file_path가 필요합니다."}

        try:
            file_path_obj = _resolve_path(target, workspace_path)

            if not file_path_obj.exists():
                return {"status": "error", "message": f"파일이 존재하지 않습니다: {target}"}

            if not file_path_obj.is_file():
                return {"status": "error", "message": f"파일이 아닙니다: {target}"}

            backup_path_str = _create_backup(file_path_obj, workspace_path)

            return {
                "status": "success",
                "message": f"{target} 백업 완료",
                "backup_path": backup_path_str,
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}


class RestoreBackupSkill(BaseSkill):
    name = "restore_backup"
    description = (
        "backup_file/write_file/patch_file/delete_file이 남긴 backup_path를 "
        "그대로 받아 file_path에 복원합니다. backup_path는 반드시 워크스페이스의 "
        ".ruder_ai_backups 디렉터리 안에 있어야 하며, 그 밖의 경로는 거부됩니다."
    )

    async def execute(
        self,
        backup_path: Optional[str] = None,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        workspace_path: Optional[str] = None,
        **kwargs,
    ):
        target = file_path or path
        if not target:
            return {"status": "error", "message": "file_path가 필요합니다."}

        if not backup_path:
            return {"status": "error", "message": "backup_path가 필요합니다."}

        try:
            file_path_obj = _resolve_path(target, workspace_path)
        except Exception as e:
            return {"status": "error", "message": str(e)}

        try:
            backup_root = _backup_root(file_path_obj, workspace_path).resolve()

            backup_obj = Path(backup_path)
            if not backup_obj.is_absolute():
                if workspace_path:
                    backup_obj = (Path(workspace_path).resolve() / backup_path).resolve()
                else:
                    backup_obj = (backup_root / backup_path).resolve()
            else:
                backup_obj = backup_obj.resolve()

            if not backup_obj.is_relative_to(backup_root):
                return {
                    "status": "error",
                    "message": ".ruder_ai_backups 디렉터리 밖의 backup_path는 사용할 수 없습니다.",
                }

            if not backup_obj.exists() or not backup_obj.is_file():
                return {
                    "status": "error",
                    "message": f"백업 파일을 찾을 수 없습니다: {backup_path}",
                }

            content = backup_obj.read_text(encoding="utf-8", errors="replace")
            file_path_obj.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(file_path_obj, content)

            return {
                "status": "success",
                "message": f"{target}을(를) {backup_path}에서 복원 완료",
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}


class MoveFileSkill(BaseSkill):
    name = "move_file"
    description = "파일을 새 경로로 이동하거나 이름을 변경합니다."

    async def execute(
        self,
        file_path: Optional[str] = None,
        path: Optional[str] = None,
        new_path: Optional[str] = None,
        destination: Optional[str] = None,
        workspace_path: Optional[str] = None,
        **kwargs,
    ):
        target = file_path or path
        dest = new_path or destination

        if not target or not dest:
            return {"status": "error", "message": "file_path와 new_path가 모두 필요합니다."}

        try:
            src_obj = _resolve_path(target, workspace_path)
            dest_obj = _resolve_path(dest, workspace_path)

            if not src_obj.exists():
                return {"status": "error", "message": f"파일이 존재하지 않습니다: {target}"}

            dest_obj.parent.mkdir(parents=True, exist_ok=True)
            src_obj.rename(dest_obj)

            return {"status": "success", "message": f"{target} -> {dest} 이동 완료"}

        except Exception as e:
            return {"status": "error", "message": str(e)}


def _resolve_path(target: str, workspace_path: Optional[str]) -> Path:
    if workspace_path:
        workspace = Path(workspace_path).resolve()
        file_path = (workspace / target).resolve()

        if not file_path.is_relative_to(workspace):
            raise ValueError("workspace 밖의 파일은 접근할 수 없습니다.")

        return file_path

    return Path(target).resolve()


def _resolve_existing_file_path(
    target: str, workspace_path: Optional[str]
) -> tuple[Path, Optional[str]]:
    """Resolve a requested file path, correcting a unique extension hallucination.

    LLMs occasionally preserve the right basename but invent the wrong extension
    (for example ``PlayerController.json`` when the real file is
    ``Assets/Scripts/PlayerController.cs``).  For existing-file operations only,
    search the workspace for an exact basename/stem match when the requested path
    does not exist.  The correction is applied only when exactly one candidate is
    found, so we never guess between multiple files.
    """
    resolved = _resolve_path(target, workspace_path)
    if resolved.exists():
        return resolved, None

    if not workspace_path:
        return resolved, None

    workspace = Path(workspace_path).resolve()
    stem = resolved.stem.lower()
    if not stem:
        return resolved, None

    # Keep traversal bounded and deterministic.
    ignored = {
        ".git", "Library", "Temp", "Logs", "obj", "Build", "Builds",
        "UserSettings", "node_modules", ".vs", "__pycache__",
        ".ruder_ai_backups",
    }
    candidates: list[Path] = []

    try:
        for candidate in workspace.rglob("*"):
            if not candidate.is_file():
                continue
            try:
                rel_parts = candidate.relative_to(workspace).parts
            except ValueError:
                continue
            if any(part in ignored or part.startswith(".") for part in rel_parts):
                continue
            if candidate.stem.lower() != stem:
                continue
            candidates.append(candidate.resolve())
            if len(candidates) > 1:
                break
    except OSError:
        return resolved, None

    if len(candidates) != 1:
        return resolved, None

    corrected = candidates[0]
    relative = corrected.relative_to(workspace).as_posix()
    return corrected, relative


def _replace_unique(
    original: str, old_str: str, new_str: str
) -> Tuple[Optional[str], Optional[str]]:
    """`original` 안에서 `old_str`이 정확히 한 번만 일치하는지 확인하고

    치환한 결과를 돌려준다. patch_file과 preview_patch가 동일한 판정
    로직을 공유하도록 분리했다. 반환값은 (결과, 에러메시지) 튜플이며
    성공 시 에러메시지는 None, 실패 시 결과는 None이다.
    """

    if not old_str:
        return None, "old_str이 필요합니다. (전체 파일을 쓰려면 write_file을 사용하세요)"

    count = original.count(old_str)

    if count == 0:
        return None, (
            "old_str이 파일 안에서 발견되지 않았습니다. 공백/들여쓰기까지 "
            "정확히 일치해야 합니다."
        )

    if count > 1:
        return None, (
            f"old_str이 파일 안에서 {count}번 발견됩니다. 정확히 한 번만 "
            "일치하도록 old_str 범위를 넓혀주세요."
        )

    return original.replace(old_str, new_str, 1), None


def _atomic_write_text(file_path_obj: Path, content: str) -> None:
    """임시 파일에 먼저 쓰고 `os.replace`로 원자적으로 교체한다.

    쓰기 도중 프로세스가 죽거나 예외가 나도 원본 파일은 손상되지 않고
    이전 상태(또는 없음) 그대로 남는다. 같은 디렉터리에 임시 파일을
    만들어야 `os.replace`가 같은 파일시스템 안에서 원자적으로 동작함이
    보장된다.
    """

    parent = file_path_obj.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(parent), prefix=f".{file_path_obj.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, file_path_obj)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _backup_root(file_path_obj: Path, workspace_path: Optional[str]) -> Path:
    """이 파일의 백업이 저장될 디렉터리(생성은 하지 않음)를 계산한다.

    workspace_path가 있으면 워크스페이스 루트의 `.ruder_ai_backups`를
    공용으로 쓰고, 없으면(워크스페이스 밖 절대경로 등) 파일이 있는
    디렉터리 바로 아래에 `.ruder_ai_backups`를 둔다.
    """

    if workspace_path:
        return Path(workspace_path).resolve() / ".ruder_ai_backups"

    return file_path_obj.parent / ".ruder_ai_backups"


def _create_backup(file_path_obj: Path, workspace_path: Optional[str]) -> str:
    """`file_path_obj`의 현재 내용을 백업 디렉터리에 복사하고 경로를

    돌려준다. workspace_path가 있으면 그 워크스페이스 기준 상대경로
    문자열을, 없으면 절대경로 문자열을 돌려준다 — restore_backup이 같은
    규칙으로 다시 해석한다.
    """

    backup_root = _backup_root(file_path_obj, workspace_path)
    backup_root.mkdir(parents=True, exist_ok=True)

    if workspace_path:
        try:
            rel = file_path_obj.resolve().relative_to(
                Path(workspace_path).resolve()
            )
            flat_name = str(rel).replace("/", "__").replace("\\", "__")
        except ValueError:
            flat_name = file_path_obj.name
    else:
        flat_name = file_path_obj.name

    timestamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
    backup_file = backup_root / f"{flat_name}.{timestamp}.bak"

    shutil.copy2(file_path_obj, backup_file)

    if workspace_path:
        try:
            return str(
                backup_file.resolve().relative_to(Path(workspace_path).resolve())
            )
        except ValueError:
            pass

    return str(backup_file)
