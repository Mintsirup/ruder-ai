from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any


class CheckpointStore:
    """Atomic JSON checkpoint store for resumable agent work."""

    def __init__(self, workspace: str | Path, *, filename: str = ".ruder_ai_checkpoint.json") -> None:
        self.workspace = Path(workspace).resolve()
        self.path = self.workspace / filename

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(state)
        payload.setdefault("schema_version", 1)
        fd, temp_name = tempfile.mkstemp(prefix=".checkpoint-", suffix=".json", dir=str(self.path.parent))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


class FileSnapshotStore:
    """Small per-task file snapshot store used for mutation rollback."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / ".ruder_ai_checkpoints"

    def snapshot(self, paths: list[str]) -> dict[str, Any]:
        snap: dict[str, Any] = {"files": {}}
        for rel in paths:
            try:
                path = (self.workspace / str(rel).replace("\\", "/")).resolve()
                if self.workspace not in path.parents and path != self.workspace:
                    continue
                key = path.relative_to(self.workspace).as_posix()
                if path.is_file():
                    snap["files"][key] = {"exists": True, "content": path.read_text(encoding="utf-8", errors="replace")}
                else:
                    snap["files"][key] = {"exists": False}
            except OSError:
                continue
        return snap

    def restore(self, snapshot: dict[str, Any]) -> list[str]:
        restored: list[str] = []
        for rel, data in dict(snapshot.get("files") or {}).items():
            path = (self.workspace / rel).resolve()
            if self.workspace not in path.parents and path != self.workspace:
                continue
            try:
                if data.get("exists"):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(str(data.get("content", "")), encoding="utf-8")
                elif path.exists():
                    path.unlink()
                restored.append(rel)
            except OSError:
                continue
        return restored
