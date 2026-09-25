"""Directory listing and project structure inspection skill."""
from pathlib import Path
from typing import Any, Dict, Optional
from ruder_ai.skills.base import BaseSkill
from ruder_ai.skills.file_ops import _resolve_path


class ListDirectorySkill(BaseSkill):
    name = "list_directory"
    description = "지정한 디렉터리의 파일 및 폴더 목록을 조회합니다."

    async def execute(
        self,
        path: str = ".",
        workspace_path: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        # workspace_path가 있으면 그 기준으로, 없으면(하위 호환) 기존과
        # 동일하게 프로세스 cwd 기준으로 해석한다. workspace_path는
        # Executor가 자동으로 채워준다 — 이게 없으면 GUI를 어느 폴더
        # 에서 실행했는지에 따라 엉뚱한 위치(예: RuderAI 소스코드
        # 자체)를 나열하는 문제가 실제로 있었다.
        try:
            dir_path = (
                _resolve_path(path, workspace_path)
                if workspace_path
                else Path(path)
            )
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        if not dir_path.exists():
            return {"status": "error", "message": f"경로가 존재하지 않습니다: {path}"}
        if not dir_path.is_dir():
            return {"status": "error", "message": f"디렉터리가 아닙니다: {path}"}

        try:
            items = []
            for item in sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if item.name.startswith(".") or item.name == "__pycache__":
                    continue
                items.append({
                    "name": item.name,
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None
                })
            return {"status": "success", "path": str(dir_path), "items": items}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class EnvironmentInfoSkill(BaseSkill):
    name = "environment_info"
    description = "현재 RuderAI 실행 OS, Python 실행 파일, 작업공간 절대경로와 주요 도구 경로를 실제 환경에서 조회합니다."

    async def execute(self, workspace_path: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        import os
        import platform as _platform
        import shutil
        import sys

        workspace = Path(workspace_path or Path.cwd()).resolve()
        return {
            "status": "success",
            "os": _platform.system(),
            "platform": _platform.platform(),
            "python_executable": str(Path(sys.executable).resolve()),
            "python_version": sys.version.split()[0],
            "working_directory": str(Path.cwd().resolve()),
            "workspace": str(workspace),
            "git": shutil.which("git"),
            "dotnet": shutil.which("dotnet"),
            "node": shutil.which("node"),
            "npm": shutil.which("npm"),
        }
