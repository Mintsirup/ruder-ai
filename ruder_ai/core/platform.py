"""Cross-platform command and path helpers for RuderAI.

The agent core should not care whether it runs on Windows or POSIX.  This
module centralizes OS-specific executable lookup, virtualenv Python paths and
wrapper-script invocation (``*.bat``/``*.cmd`` vs native executables).
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class PlatformAdapter:
    """Small OS abstraction used by command-running skills."""

    name: str
    is_windows: bool
    executable_suffixes: tuple[str, ...]
    shell_executable: str | None

    @classmethod
    def detect(cls) -> "PlatformAdapter":
        is_windows = sys.platform.startswith("win")
        return cls(
            name="windows" if is_windows else "posix",
            is_windows=is_windows,
            executable_suffixes=(".exe", ".cmd", ".bat") if is_windows else ("",),
            shell_executable=(os.environ.get("COMSPEC") or "cmd.exe") if is_windows else (os.environ.get("SHELL") or "/bin/sh"),
        )

    @property
    def path_env_key(self) -> str:
        return "PATH"

    def which(self, command: str) -> str | None:
        """Resolve a command using the host OS PATH."""
        return shutil.which(command)

    def venv_root(self, workspace: Path | str) -> Path:
        """Return the standard project-local virtual environment path."""
        return Path(workspace).resolve() / ".venv"

    def venv_python(self, workspace: Path | str) -> Path:
        """Return the project-local virtualenv Python executable path."""
        root = self.venv_root(workspace)
        return root / ("Scripts" if self.is_windows else "bin") / (
            "python.exe" if self.is_windows else "python"
        )

    def venv_activate(self, workspace: Path | str) -> Path:
        """Return the virtualenv activation script path."""
        root = self.venv_root(workspace)
        return root / ("Scripts\activate.bat" if self.is_windows else "bin/activate")

    def python_executable(self, venv_root: Path | str | None = None) -> str:
        """Return the best Python executable for the current platform."""
        if venv_root is not None:
            root = Path(venv_root)
            candidate = root / ("Scripts" if self.is_windows else "bin") / (
                "python.exe" if self.is_windows else "python"
            )
            if candidate.exists():
                return str(candidate)
        return sys.executable

    def wrapper_script(self, workspace: Path, base_name: str) -> Path | None:
        """Find a project-local command wrapper for *base_name*."""
        names = (
            (f"{base_name}.bat", f"{base_name}.cmd", base_name)
            if self.is_windows
            else (base_name,)
        )
        for name in names:
            candidate = workspace / name
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def command_args(self, executable: Path | str, args: Sequence[str] = ()) -> list[str]:
        """Build subprocess argv, handling Windows batch/cmd scripts safely."""
        exe = Path(executable)
        if self.is_windows and exe.suffix.lower() in {".bat", ".cmd"}:
            return [self.shell_executable or "cmd.exe", "/d", "/c", str(exe), *map(str, args)]
        return [str(exe), *map(str, args)]

    def describe(self) -> dict[str, str | bool]:
        return {
            "name": self.name,
            "is_windows": self.is_windows,
            "shell": self.shell_executable or "",
        }


PLATFORM = PlatformAdapter.detect()
