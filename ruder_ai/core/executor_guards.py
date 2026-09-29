"""Mutation, turn-intent, protected-file and scope guards."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ruder_ai.core.mutation_guard import MutationGuard
from ruder_ai.core.turn_intent import (
    MUTATION_REFUSED_MESSAGE,
    classify_turn,
)


class GuardsMixin:
    """Mutation, turn-intent, protected-file and scope guards."""

    def _check_mutation_semantics(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Guard mutation mechanisms that can bypass PatchFileSkill.

        ``apply_patch`` can change arbitrary text without going through the
        normal ``patch_file`` skill, so the missing-target-symbol rule must
        also be enforced here.  The check is intentionally conservative:
        only symbols explicitly named in the user's request are considered.
        """
        task = self._current_task or ""
        if not task or tool_name != "apply_patch":
            return None

        from ruder_ai.core.mutation_guard import MutationGuard

        if MutationGuard.task_allows_new_symbols(task):
            return None

        diff_text = kwargs.get("diff") or kwargs.get("patch") or ""
        if not isinstance(diff_text, str) or not diff_text:
            return None

        symbols = MutationGuard.candidate_symbols(task)
        if not symbols:
            return None

        targets = self._extract_diff_target_paths(diff_text)
        if not targets:
            return None

        workspace = None
        if self.workspace_path:
            from pathlib import Path
            workspace = Path(self.workspace_path).resolve()

        if workspace is None:
            return None

        added_lines = "\n".join(
            line[1:] for line in diff_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        for symbol in symbols:
            if not MutationGuard.task_targets_symbol(task, symbol):
                continue
            if symbol not in added_lines:
                continue

            for raw_path in targets:
                path = (workspace / raw_path).resolve()
                try:
                    relative = path.relative_to(workspace).as_posix()
                except ValueError:
                    continue
                if not path.is_file():
                    continue
                try:
                    original = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if MutationGuard.task_targets_symbol(task, symbol) and symbol not in original:
                    return {
                        "status": "error",
                        "error_type": self.ERROR_TYPE_VALIDATION,
                        "message": (
                            f"요청에서 수정 대상으로 지정한 심볼 '{symbol}'이 "
                            f"원본 파일 '{relative}'에 존재하지 않습니다. "
                            "apply_patch로 새 심볼을 임의로 만들지 않았습니다."
                        ),
                        "guard": "missing_target_symbol",
                        "symbol": symbol,
                    }

        return None

    def _check_turn_intent(
        self,
        tool_name: str,
    ) -> dict[str, Any] | None:
        """Block file mutations on a turn that never asked for any.

        "안녕" once produced a ``write_file``: the planner invented a
        ``hello_handler`` module and the agent modified a repository because
        someone said hello. The model is free to plan whatever it likes, so the
        decision has to be enforced here, at the last point before a tool
        touches the filesystem - not in the planner, which the same model also
        writes.

        Read-only tools stay available: a question about the project is a
        legitimate reason to open files, just never to change them.
        """
        if tool_name not in self.FILE_MUTATING_TOOLS:
            return None

        intent = classify_turn(self._current_task)
        if intent.may_mutate:
            return None

        return {
            "status": "error",
            "error_type": self.ERROR_TYPE_VALIDATION,
            "message": MUTATION_REFUSED_MESSAGE,
            "turn_kind": intent.kind.value,
            "turn_signal": intent.signal,
            "turn_reason": intent.reason,
        }

    def _check_protected_files(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
    ) -> dict[str, Any] | None:
        """PROTECTED_FILES에 해당하는 파일을 이 Tool이 건드리려는지
        확인한다. 문제가 없으면 None, 차단해야 하면 error 결과 dict를
        반환한다.

        - write_file/patch_file/append_file/delete_file/move_file/
          generate_diff는 kwargs의 file_path(또는 path)로 대상 파일이
          직접 드러난다.
        - apply_patch는 file_path가 없고 diff 텍스트(unified diff의
          `+++ b/...` 헤더)에 대상 경로가 들어있으므로 거기서 추출한다.
        - move_file은 new_path/destination(이동 "결과" 경로)도 함께
          검사한다 — protected 파일을 다른 이름으로 옮기는 것도 원본
          파일을 사라지게 하므로 동일하게 취급한다.
        - 사용자의 원본 요청(self._current_task)에 그 파일 이름이 실제로
          언급되어 있으면 "사용자가 명시적으로 그 파일을 고쳐달라고
          했다"고 보고 허용한다.
        """

        target_paths: list[str] = []

        if tool_name == "apply_patch":
            diff_text = kwargs.get("diff") or kwargs.get("patch") or ""
            target_paths.extend(self._extract_diff_target_paths(diff_text))
        elif tool_name in (
            self.FILE_MUTATING_TOOLS | {"generate_diff", "move_file"}
        ):
            for key in self._PATH_KWARG_NAMES:
                if kwargs.get(key):
                    target_paths.append(str(kwargs[key]))
            if tool_name == "move_file":
                for key in ("new_path", "destination"):
                    if kwargs.get(key):
                        target_paths.append(str(kwargs[key]))
        else:
            return None

        task_lower = (self._current_task or "").lower()

        for raw_path in target_paths:
            basename = raw_path.replace("\\", "/").rsplit("/", 1)[-1].lower()
            if basename not in self.PROTECTED_FILES:
                continue

            # 사용자가 원래 요청에서 이 파일을 직접 언급했으면 허용.
            if basename in task_lower or raw_path.lower() in task_lower:
                continue

            return {
                "status": "error",
                "error_type": self.ERROR_TYPE_VALIDATION,
                "message": (
                    f"'{raw_path}'는 참조용 문서(README/TASK_INDEX/"
                    "RUNBOOK 등)로 보호되는 파일이라 자동으로 수정할 수 "
                    "없습니다. 사용자가 이 파일을 직접 수정해 달라고 "
                    "명시적으로 요청한 경우에만 허용됩니다."
                ),
            }

        return None

    @staticmethod
    def _extract_diff_target_paths(diff_text: str) -> list[str]:
        """unified diff 텍스트에서 대상 파일 경로들을 뽑아낸다.

        `git apply` 스타일 diff의 `+++ b/<path>`(또는 `--- a/<path>`)
        헤더를 사용한다. `/dev/null`(파일 삭제/생성의 반대쪽 헤더)은
        제외한다.
        """

        paths: list[str] = []
        for line in diff_text.splitlines():
            if not (line.startswith("+++ ") or line.startswith("--- ")):
                continue
            raw = line[4:].strip()
            if raw == "/dev/null":
                continue
            # git diff 헤더는 보통 "a/..." / "b/..." 접두사를 붙인다.
            if raw.startswith("a/") or raw.startswith("b/"):
                raw = raw[2:]
            paths.append(raw)
        return paths

    def _active_scope_guard(self, tool_name: str, kwargs: dict[str, Any]):
        active_root = getattr(self, "_active_project_root", None)
        if not active_root or tool_name not in self.FILE_MUTATING_TOOLS:
            return None
        candidate = kwargs.get("file_path") or kwargs.get("path")
        if tool_name == "move_file":
            candidate = kwargs.get("new_path") or kwargs.get("destination") or candidate
        if not candidate:
            return None
        root = Path(self.workspace_path).resolve()
        active = (root / active_root).resolve()
        target = (root / str(candidate).replace("\\", "/")).resolve()
        if target == active or active in target.parents:
            return None
        return {"status": "error", "error_type": self.ERROR_TYPE_VALIDATION,
                "message": f"활성 프로젝트 범위 밖의 파일 변경을 차단했습니다: {candidate}. 현재 활성 프로젝트: {self._active_project_root}",
                "guard": "active_project_scope"}

    def _infer_project_root(self, relative_path: str) -> str | None:
        """Infer a nested user-project root from a changed file."""
        try:
            workspace_path = getattr(self, "workspace_path", None)
            if not workspace_path:
                return None
            root = Path(workspace_path).resolve()
            path = (root / str(relative_path).replace("\\", "/")).resolve()
            if root not in path.parents and path != root:
                return None
            current = path if path.is_dir() else path.parent
            markers = {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
                       "package.json", "pyproject.toml", "requirements.txt", "setup.py", "go.mod", "Cargo.toml"}
            while current != root and root in current.parents:
                if any((current / marker).is_file() for marker in markers):
                    return current.relative_to(root).as_posix()
                if (current / "src").is_dir() and current.parent != root:
                    return current.relative_to(root).as_posix()
                current = current.parent
        except OSError:
            return None
        return None

    @staticmethod
    def _extract_explicit_file_paths(text: str) -> list[str]:
        patterns = [
            r"(?<![\w./-])([A-Za-z0-9_./\\-]+\.cs)",
            r"(?<![\w./-])([A-Za-z0-9_./\\-]+\.py)",
            r"(?<![\w./-])([A-Za-z0-9_./\\-]+\.js)",
            r"(?<![\w./-])([A-Za-z0-9_./\\-]+\.ts)",
        ]
        out: list[str] = []
        for pattern in patterns:
            for match in re.findall(pattern, text or ""):
                value = str(match).replace("\\", "/").strip(".,)")
                if value and value not in out:
                    out.append(value)
        return out[:8]

    def _recover_file_path_from_task(self, tool_name: str, kwargs: dict[str, Any], task_description: str = "") -> dict[str, Any]:
        if tool_name not in {"read_file", "patch_file", "preview_patch", "write_file", "append_file", "delete_file", "move_file", "backup_file", "restore_backup"}:
            return kwargs
        if kwargs.get("file_path") or kwargs.get("path"):
            return kwargs

        # 가장 좁은 범위인 현재 Task 설명에서 먼저 찾고, 없으면 전체
        # 사용자 요청으로 확장한다. 여러 파일이 언급된 요청에서도
        # "Task 2: CameraFollow.cs ..." 같은 설명을 정확히 따른다.
        candidates = self._extract_explicit_file_paths(task_description)
        if not candidates:
            candidates = self._extract_explicit_file_paths(getattr(self, "_current_task", ""))

        if len(candidates) == 1:
            value = candidates[0]
            # basename-only 요청은 현재 workspace에서 유일한 실제 파일로
            # 해석한다. 중복이면 추측하지 않고 원래 값을 유지한다.
            try:
                root = Path(self.workspace_path).resolve()
                direct = (root / value).resolve()
                if direct.is_file() and root in direct.parents:
                    value = direct.relative_to(root).as_posix()
                else:
                    matches = [
                        p for p in root.rglob(Path(value).name)
                        if p.is_file() and not any(x in p.parts for x in (".git", "Library", "Temp"))
                    ]
                    if len(matches) == 1:
                        value = matches[0].relative_to(root).as_posix()
            except OSError:
                pass
            kwargs = dict(kwargs)
            kwargs["file_path"] = value
            print(f"⚙️ Task의 명시 파일 경로를 '{tool_name}'에 자동 주입: {value}")
        return kwargs

    def _repair_moved_file_references(self, source: str, destination: str) -> list[str]:
        """Repair common relative JS import/require references after a move."""
        root = Path(self.workspace_path).resolve()
        src = str(source).replace("\\", "/").lstrip("./")
        dst = str(destination).replace("\\", "/").lstrip("./")
        if not src or not dst or src == dst:
            return []
        changed=[]
        old_stem = Path(src).with_suffix("").as_posix()
        new_path = root / dst
        if not new_path.is_file():
            return []
        candidates=[]
        for pth in root.rglob("*"):
            if not pth.is_file() or any(part in {".git",".venv","node_modules","__pycache__",".ruder_ai_backups"} for part in pth.parts):
                continue
            if pth.suffix.lower() not in {".js", ".cjs", ".mjs", ".ts", ".tsx", ".json"}:
                continue
            candidates.append(pth)
        import re
        for ref_file in candidates:
            try:
                text=ref_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel_new = __import__('os').path.relpath(new_path, ref_file.parent).replace('\\', '/')
            if not rel_new.startswith("."):
                rel_new = "./" + rel_new
            stem_new = rel_new.rsplit('.', 1)[0] if '.' in rel_new.rsplit('/', 1)[-1] else rel_new
            patterns=[]
            old_rel_stem=Path(__import__('os').path.relpath(root/src, ref_file.parent).replace('\\','/')).as_posix()
            if not old_rel_stem.startswith("."):
                old_rel_stem="./"+old_rel_stem
            patterns.extend([old_rel_stem, "./"+Path(src).name[:-3] if src.endswith('.js') else old_stem])
            new_text=text
            for oldref in patterns:
                if oldref and oldref != stem_new:
                    new_text=new_text.replace(f'"{oldref}"', f'"{stem_new}"').replace(f"'{oldref}'", f"'{stem_new}'")
            if new_text!=text:
                try:
                    ref_file.write_text(new_text, encoding="utf-8")
                    changed.append(ref_file.relative_to(root).as_posix())
                except OSError:
                    pass
        return changed

    def _patch_file_local_recovery(self, kwargs: dict[str, Any]) -> dict[str, Any] | None:
        """Recover deterministic patch mismatches without repeating the same call.

        Safe cases only: when the requested old text occurs multiple times and
        new_str is empty, remove one exact duplicate occurrence.  When old_str
        is stale/missing but new_str is already present, treat the mutation as
        already applied.
        """
        if not kwargs.get("file_path") or not kwargs.get("old_str"):
            return None
        try:
            path = Path(self.workspace_path) / str(kwargs["file_path"]).replace("\\", "/")
            if not path.is_file():
                return None
            current = path.read_text(encoding="utf-8", errors="replace")
            old = str(kwargs.get("old_str") or "")
            new = str(kwargs.get("new_str") or "")
            count = current.count(old)
            if count == 0 and new and new in current:
                return {**kwargs, "__already_applied": True}
            if count > 1 and new == "":
                # Exact duplicate deletion is deterministic: remove the last
                # duplicate only, preserving the first implementation.
                pos = current.rfind(old)
                updated = current[:pos] + current[pos + len(old):]
                recovered = dict(kwargs)
                recovered["old_str"] = current[pos:pos + len(old)]
                recovered["new_str"] = ""
                recovered["__recovered_patch"] = True
                return recovered
            if count > 1:
                # Make the match unique using one surrounding line on each side.
                positions=[]
                start=0
                while True:
                    idx=current.find(old,start)
                    if idx < 0: break
                    positions.append(idx); start=idx+1
                if len(positions) > 1:
                    best=positions[-1]
                    line_start=current.rfind("\n",0,best)+1
                    line_end=current.find("\n",best+len(old))
                    if line_end<0: line_end=len(current)
                    expanded=current[line_start:line_end]
                    if expanded.count(old)==1:
                        recovered=dict(kwargs)
                        recovered["old_str"]=expanded
                        recovered["new_str"]=expanded.replace(old,new,1)
                        recovered["__recovered_patch"]=True
                        return recovered
        except OSError:
            return None
        return None
