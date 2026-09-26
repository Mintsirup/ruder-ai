from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


class FileResolver:
    """단일 규칙으로 workspace 내 파일을 안전하게 해석한다."""

    # basename fallback 검색에서 건너뛸 디렉터리. 'Temp', 'obj', 'bin'은
    # Unity/빌드 산출물 전용 휴리스틱인데, Windows의 사용자 temp 디렉터리
    # (..\Local\Temp\..)나 일반 소스 경로(src/obj/, my-bin/ 등)까지
    # 매칭돼 테스트 임시 워크스페이스의 모든 파일이 무시되는 문제가
    # 있었다. 정확한 상위 디렉터리 경로로 판단한다.
    IGNORE_DIR_PATHS = ("Library/Temp", "Library", "obj", "bin", "__pycache__")

    def __init__(self, workspace):
        self.root = Path(workspace).resolve()
        # basename -> [workspace-relative posix paths], built in ONE walk.
        # None means "not built yet".
        self._by_name: dict[str, list[str]] | None = None

    # ------------------------------------------------------------------
    # Basename index
    # ------------------------------------------------------------------
    def invalidate(self) -> None:
        """Drop the cached basename index after the workspace changed.

        Long-lived owners (agent roles, ToolResolver, DeterministicActionResolver)
        must call this after creating, renaming or deleting files, otherwise a
        newly added file will not be found by bare basename. Most call sites
        build a throwaway resolver, for which this never matters.
        """
        self._by_name = None

    def _basename_index(self) -> dict[str, list[str]]:
        if self._by_name is not None:
            return self._by_name

        index: dict[str, list[str]] = {}
        root_str = str(self.root)

        # os.walk with in-place pruning rather than rglob("*"). Every ignored
        # subtree (.git, node_modules, Library/Temp, ...) is skipped outright
        # instead of being walked and then filtered file by file, which is the
        # difference between a fast scan and a slow one on a real repository.
        for dirpath, dirnames, filenames in os.walk(root_str):
            # os.walk/relpath use the native separator; the codebase convention
            # (and every consumer of these strings) is posix.
            relative_dir = os.path.relpath(dirpath, root_str).replace(os.sep, "/")
            if relative_dir == ".":
                relative_dir = ""

            dirnames[:] = [
                name
                for name in dirnames
                if not self._dir_ignored(relative_dir, name)
            ]

            for name in filenames:
                relative = f"{relative_dir}/{name}" if relative_dir else name
                if self._rel_ignored(relative):
                    continue
                index.setdefault(name, []).append(relative)

        self._by_name = index
        return index

    def _dir_ignored(self, relative_dir: str, name: str) -> bool:
        """True when a directory can be pruned from the walk entirely."""
        if name.startswith("."):
            return True
        candidate = f"{relative_dir}/{name}" if relative_dir else name
        if self._rel_ignored(candidate):
            return True
        return candidate.replace(os.sep, "/") in self.IGNORE_DIR_PATHS

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    def resolve(self, requested: str) -> str | None:
        if not requested:
            return None
        raw = str(requested).strip().strip('"\'')
        candidate = (self.root / raw.replace("\\", "/")).resolve()
        if self._inside_root(candidate) and candidate.is_file():
            return candidate.relative_to(self.root).as_posix()

        # Basename fallback. One shared index instead of an rglob per name:
        # resolving 50 names used to walk the tree 50 times.
        matches = self._basename_index().get(Path(raw).name, ())
        if len(matches) == 1:
            return matches[0]
        return None

    def resolve_many(self, requested: Iterable[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in requested:
            resolved = self.resolve(item)
            if resolved and resolved not in seen:
                seen.add(resolved)
                result.append(resolved)
        return result

    def exists(self, requested: str) -> bool:
        return self.resolve(requested) is not None

    def _inside_root(self, path: Path) -> bool:
        return path == self.root or self.root in path.parents

    def _ignored(self, path: Path) -> bool:
        try:
            rel_posix = path.relative_to(self.root).as_posix()
        except ValueError:
            rel_posix = path.as_posix()
        return self._rel_ignored(rel_posix)

    @staticmethod
    def _rel_ignored(rel_posix: str) -> bool:
        parts = rel_posix.split("/")
        if any(part.startswith(".") for part in parts[:-1]):
            return True
        rel_with_slash = rel_posix + "/"
        return any(
            rel_with_slash.startswith(prefix + "/")
            for prefix in FileResolver.IGNORE_DIR_PATHS
        )
