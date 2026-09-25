"""Auto Verify skill for RuderAI Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ruder_ai.skills.base import BaseSkill
from ruder_ai.verify import AutoVerifier


class VerifyProjectSkill(BaseSkill):
    name = "verify_project"
    description = (
        "프로젝트 타입에 맞는 빌드/테스트/린트(Gradle, Maven, pytest, "
        "flake8, mypy, spotless, eslint)를 자동으로 실행하고 결과를 반환합니다."
    )

    def __init__(self) -> None:
        self._verifier = AutoVerifier()

    async def execute(
        self,
        workspace_path: str = None,
        project=None,
        **kwargs,
    ) -> Dict[str, Any]:
        if project is None:
            return {
                "status": "error",
                "message": (
                    "프로젝트 정보(project)가 없습니다. "
                    "먼저 프로젝트 인덱싱이 필요합니다."
                ),
            }

        workspace = Path(workspace_path or ".")
        target_files = kwargs.get("target_files") or kwargs.get("file_paths")
        if isinstance(target_files, str):
            target_files = [target_files]
        if target_files is not None and not isinstance(target_files, list):
            target_files = None

        scenario_entry = kwargs.get("scenario_entry")
        scenario_input = kwargs.get("scenario_input")
        scenario_expected = kwargs.get("scenario_expected")
        if scenario_entry is not None:
            scenario_entry = str(scenario_entry).replace("\\", "/").lstrip("/")
        if scenario_input is not None:
            scenario_input = str(scenario_input)
        if scenario_expected is not None and not isinstance(scenario_expected, (list, str)):
            scenario_expected = None

        report = await self._verifier.verify(
            project,
            workspace,
            target_files=target_files,
            scenario_entry=scenario_entry,
            scenario_input=scenario_input,
            scenario_expected=scenario_expected,
        )

        return {
            "status": "success" if report.success else ("not_run" if report.status == "not_run" else "failed"),
            "verification_status": report.status,
            "verification_success": report.success,
            "summary": report.summary(),
            "ran_tools": report.ran_tools,
            "failure_log": report.failure_log(),
        }
