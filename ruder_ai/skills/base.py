"""Base class for all RuderAI agent skills/tools."""
from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseSkill(ABC):
    """RuderAI 스킬 기본 클래스"""
    name: str = "base_skill"
    description: str = "기본 스킬 설명입니다."

    @abstractmethod
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """스킬 실행 로직 구현부"""
        pass

    def to_tool_schema(self) -> Dict[str, Any]:
        """Return a JSON-Schema-like Tool definition for deterministic callers and LLM function calling."""
        import inspect
        properties: Dict[str, Any] = {}
        required = []
        try:
            signature = inspect.signature(self.execute)
            for name, param in signature.parameters.items():
                if name in {"self", "kwargs", "workspace_path", "project", "project_index", "reference_index", "semantic_file_index"}:
                    continue
                annotation = str(param.annotation)
                kind = "number" if "int" in annotation or "float" in annotation else "boolean" if "bool" in annotation else "string"
                properties[name] = {"type": kind}
                if param.default is inspect.Parameter.empty:
                    required.append(name)
        except (TypeError, ValueError):
            pass
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": True},
        }
