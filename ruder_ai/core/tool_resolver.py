from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from ruder_ai.core.file_resolver import FileResolver


@dataclass(slots=True)
class ToolResolution:
    ok: bool
    kwargs: dict[str, Any]
    message: str = ""


class ToolResolver:
    """PlanTask/LLM kwargs를 Executor가 실행 가능한 형태로 정규화한다."""

    FILE_KEYS = ("file_path", "path", "target_path", "source_path", "file")

    def __init__(self, skill_registry, workspace, project=None):
        self.registry = skill_registry
        self.file_resolver = FileResolver(workspace)
        self.workspace = str(workspace)
        self.project = project

    def resolve(self, tool_name: str, kwargs: dict[str, Any] | None, *, description: str = "") -> ToolResolution:
        data = dict(kwargs or {})
        skill = self.registry.get_skill(tool_name)
        if skill is None:
            return ToolResolution(False, data, f"알 수 없는 Tool: {tool_name}")

        # aliases → canonical path
        for key in self.FILE_KEYS:
            if key in data and data[key]:
                resolved = self.file_resolver.resolve(str(data[key]))
                if resolved:
                    data["file_path"] = resolved
                    if key != "file_path":
                        data.pop(key, None)
                break

        # description에서 유일한 파일명을 찾아서 file_path를 보정.
        if "file_path" not in data:
            import re
            candidates = re.findall(r"(?:[A-Za-z0-9_.\\/-]+/)?[A-Za-z0-9_.-]+\.(?:cs|py|js|ts|java|kt|json|yaml|yml|md|txt)", description)
            for candidate in candidates:
                resolved = self.file_resolver.resolve(candidate)
                if resolved:
                    data["file_path"] = resolved
                    break

        if "workspace_path" in self._parameter_names(skill):
            data.setdefault("workspace_path", self.workspace)
        if "project" in self._parameter_names(skill) and self.project is not None:
            data.setdefault("project", self.project)

        missing = self.required_parameters(skill, data)
        if missing:
            return ToolResolution(False, data, f"필수 kwargs 누락: {', '.join(missing)}")
        return ToolResolution(True, data)

    @staticmethod
    def _parameter_names(skill) -> set[str]:
        try:
            return set(inspect.signature(skill.execute).parameters)
        except (TypeError, ValueError):
            return set()

    @classmethod
    def required_parameters(cls, skill, kwargs: dict[str, Any]) -> list[str]:
        try:
            signature = inspect.signature(skill.execute)
        except (TypeError, ValueError):
            return []
        missing = []
        for name, param in signature.parameters.items():
            if name in {"self", "kwargs", "workspace_path", "project"}:
                continue
            if param.default is inspect.Parameter.empty and name not in kwargs:
                missing.append(name)
        return missing
