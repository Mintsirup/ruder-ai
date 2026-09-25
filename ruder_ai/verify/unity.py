from __future__ import annotations

import os
from pathlib import Path

from ruder_ai.core.platform import PLATFORM
from .models import CheckResult


def find_unity_executable() -> str | None:
    for key in ("RUDER_AI_UNITY_EDITOR", "UNITY_EDITOR", "UNITY_EXE"):
        value = os.environ.get(key)
        if value and Path(value).exists():
            return str(Path(value).resolve())
    for name in ("Unity", "Unity.exe", "unity", "unity-editor"):
        found = PLATFORM.which(name)
        if found:
            return found
    return None


def is_unity_project(workspace: Path) -> bool:
    return (workspace / "Assets").is_dir() and (workspace / "ProjectSettings" / "ProjectVersion.txt").is_file()


async def run_unity_batchmode_compile(workspace: Path, timeout: int = 900) -> CheckResult:
    from .runners import _run

    if not is_unity_project(workspace):
        return CheckResult(
            tool="unity-batchmode",
            command="unity -batchmode -quit -projectPath <workspace>",
            passed=False,
            skipped=True,
            skip_reason="Unity 프로젝트 구조가 아닙니다 (Assets + ProjectSettings/ProjectVersion.txt 필요).",
        )

    editor = find_unity_executable()
    if not editor:
        return CheckResult(
            tool="unity-batchmode",
            command="<UnityEditor> -batchmode -quit -projectPath <workspace> -logFile -",
            passed=False,
            skipped=True,
            skip_reason="Unity Editor 실행 파일을 찾을 수 없습니다. RUDER_AI_UNITY_EDITOR를 지정하세요.",
        )

    args = [
        editor,
        "-batchmode",
        "-quit",
        "-nographics",
        "-projectPath",
        str(workspace),
        "-logFile",
        "-",
    ]
    return await _run("unity-batchmode", args, workspace, timeout)
