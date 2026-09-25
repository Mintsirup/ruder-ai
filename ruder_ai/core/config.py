"""Configuration Manager for RuderAI."""
import json
from pathlib import Path
from typing import Any, Dict

CONFIG_FILE_PATH = Path.cwd() / ".ruder_ai_config.json"

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

class ConfigManager:
    def __init__(self, config_path: Path = CONFIG_FILE_PATH):
        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            self.save_config(DEFAULT_CONFIG)
            return DEFAULT_CONFIG.copy()
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                # 누락된 기본 키 보장
                for k, v in DEFAULT_CONFIG.items():
                    loaded.setdefault(k, v)
                return loaded
        except Exception:
            return DEFAULT_CONFIG.copy()

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
