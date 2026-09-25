"""Typed runtime settings for RuderAI.

The agent is used both from the CLI and from the Project AI bridge.  Keeping
runtime tuning in one small dataclass avoids the previous situation where
model, context, timeout and context-builder budgets were scattered across
multiple constructors and hard-coded constants.
"""
from __future__ import annotations

from dataclasses import dataclass
import os


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _str_env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


@dataclass(frozen=True, slots=True)
class RuderAISettings:
    model: str = "ruder-ai-agent:latest"
    ollama_host: str = "http://127.0.0.1:11434"
    temperature: float = 0.1
    num_ctx: int = 16384
    max_tokens: int = 3072
    timeout: float = 300.0
    context_token_budget: int = 4352
    max_files: int = 5
    max_snippets_per_file: int = 5
    max_steps: int = 5
    max_replans: int = 2
    max_reflections: int = 2
    enable_reflection: bool = True
    autonomy_enabled: bool = True
    autonomy_max_cycles: int = 12
    autonomy_max_wall_time: float = 1800.0
    autonomy_max_failures: int = 3
    autonomy_max_file_changes: int = 50

    @classmethod
    def from_env(cls) -> "RuderAISettings":
        # dataclass(slots=True)의 클래스 속성은 기본값이 아니라
        # member_descriptor가 되므로 `cls.model` 같은 접근을 기본값으로
        # 사용하면 안 된다. 실제 기본값을 명시해 인스턴스를 생성한다.
        return cls(
            model=_str_env("RUDER_AI_MODEL", "ruder-ai-agent:latest"),
            ollama_host=_str_env(
                "RUDER_AI_OLLAMA_HOST", "http://127.0.0.1:11434"
            ),
            temperature=_float_env("RUDER_AI_TEMPERATURE", 0.1, 0.0),
            num_ctx=_int_env("RUDER_AI_NUM_CTX", 16384, 1024),
            max_tokens=_int_env("RUDER_AI_MAX_TOKENS", 3072, 1),
            timeout=_float_env("RUDER_AI_TIMEOUT", 300.0, 1.0),
            context_token_budget=_int_env(
                "RUDER_AI_CONTEXT_TOKEN_BUDGET", 4352, 256
            ),
            max_files=_int_env("RUDER_AI_MAX_FILES", 5, 1),
            max_snippets_per_file=_int_env(
                "RUDER_AI_MAX_SNIPPETS_PER_FILE", 5, 1
            ),
            max_steps=_int_env("RUDER_AI_MAX_STEPS", 5, 1),
            max_replans=_int_env("RUDER_AI_MAX_REPLANS", 2, 0),
            max_reflections=_int_env("RUDER_AI_MAX_REFLECTIONS", 2, 0),
            enable_reflection=os.getenv("RUDER_AI_ENABLE_REFLECTION", "true").lower()
            in {"1", "true", "yes", "on"},
            autonomy_enabled=os.getenv("RUDER_AI_AUTONOMY_ENABLED", "true").lower()
            in {"1", "true", "yes", "on"},
            autonomy_max_cycles=_int_env("RUDER_AI_AUTONOMY_MAX_CYCLES", 12, 1),
            autonomy_max_wall_time=_float_env("RUDER_AI_AUTONOMY_MAX_WALL_TIME", 1800.0, 1.0),
            autonomy_max_failures=_int_env("RUDER_AI_AUTONOMY_MAX_FAILURES", 3, 1),
            autonomy_max_file_changes=_int_env("RUDER_AI_AUTONOMY_MAX_FILE_CHANGES", 50, 1),
        )
