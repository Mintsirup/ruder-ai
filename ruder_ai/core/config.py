"""Configuration Manager for RuderAI."""
import json
from pathlib import Path
from typing import Any, Dict

from ruder_ai.core.settings import RuderAISettings

CONFIG_FILE_NAME = ".ruder_ai_config.json"


def default_config_path() -> Path:
    """Resolve the config location against the *current* working directory.

    Resolved per call rather than at import time, so the CLI, the desktop GUI
    and the tests cannot disagree about which file they are reading depending
    on when the module happened to be first imported.
    """
    return Path.cwd() / CONFIG_FILE_NAME

DEFAULT_CONFIG: Dict[str, Any] = {
    "ollama_base_url": "http://127.0.0.1:11434",
    "model_name": "ruder-ai-agent:latest",
    "temperature": 0.1,
    "num_ctx": 16384,
    "max_tokens": 3072,
    "context_token_budget": 4352,
    "timeout": 300.0,
    "max_steps": 5,
    "max_replans": 2,
    "max_reflections": 2,
    "enable_reflection": True,
    "workspace_dir": str(Path.cwd()),
}


def default_config() -> Dict[str, Any]:
    """DEFAULT_CONFIG with ``workspace_dir`` resolved against the live CWD.

    ``DEFAULT_CONFIG["workspace_dir"]`` is frozen at import time. Using it
    directly made a freshly created config point at whatever directory
    happened to be current when the module was first imported, not the one the
    process is actually running in.
    """
    values = dict(DEFAULT_CONFIG)
    values["workspace_dir"] = str(Path.cwd())
    return values

class ConfigManager:
    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path if config_path is not None else default_config_path()
        self.config = self.load_config()

    def load_config(self) -> Dict[str, Any]:
        defaults = default_config()
        if not self.config_path.exists():
            self.save_config(defaults)
            return defaults
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                # 누락된 기본 키 보장
                for k, v in defaults.items():
                    loaded.setdefault(k, v)
                return loaded
        except Exception:
            return defaults

    def save_config(self, new_config: Dict[str, Any]):
        self.config = dict(new_config)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
            f.write("\n")
        temp_path.replace(self.config_path)

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any):
        self.config[key] = value
        self.save_config(self.config)

    def update(self, values: Dict[str, Any]) -> None:
        """Merge *values* into the config, keeping every untouched key.

        ``save_config`` replaces the whole document, so a settings dialog that
        only knows about a few fields must use this — otherwise saving a model
        name silently drops ``env_vars``, ``num_ctx``, ``max_steps`` and the
        rest of the user's configuration.
        """
        merged = dict(self.config)
        merged.update(values)
        self.save_config(merged)


def settings_from_config(config: "ConfigManager | Dict[str, Any]") -> RuderAISettings:
    """Build agent runtime settings from the persisted configuration.

    The single mapping every entry point (terminal REPL, desktop GUI) must go
    through.  Each one used to read the config keys it remembered, so the GUI
    silently ran with hardcoded defaults — most visibly it ignored
    ``ollama_base_url`` and always talked to 127.0.0.1:11434.
    """
    get = config.get if hasattr(config, "get") else (lambda key, default=None: config.get(key, default))

    def as_float(key: str, default: float) -> float:
        try:
            return float(get(key, default))
        except (TypeError, ValueError):
            return default

    def as_int(key: str, default: int) -> int:
        try:
            return int(get(key, default))
        except (TypeError, ValueError):
            return default

    defaults = RuderAISettings()
    return RuderAISettings(
        model=str(get("model_name", defaults.model) or defaults.model),
        ollama_host=str(get("ollama_base_url", defaults.ollama_host) or defaults.ollama_host),
        temperature=as_float("temperature", defaults.temperature),
        num_ctx=as_int("num_ctx", defaults.num_ctx),
        max_tokens=as_int("max_tokens", defaults.max_tokens),
        timeout=as_float("timeout", defaults.timeout),
        context_token_budget=as_int("context_token_budget", defaults.context_token_budget),
        max_steps=as_int("max_steps", defaults.max_steps),
        max_replans=as_int("max_replans", defaults.max_replans),
        max_reflections=as_int("max_reflections", defaults.max_reflections),
        enable_reflection=bool(get("enable_reflection", defaults.enable_reflection)),
    )
