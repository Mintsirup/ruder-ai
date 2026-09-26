"""Headless regression tests for the GUI / TUI fixes.

None of these construct a ``QApplication``; they exercise the pure logic and
the shell-session layer that previously had no coverage at all.
"""
import ast
import os
import sys
import time
import tkinter as tk
from pathlib import Path

import pytest

from ruder_ai.core.config import ConfigManager, settings_from_config
from ruder_ai.tui import shell_session


# --- ConfigManager.update(): the settings dialog no longer wipes config ---

def test_update_preserves_keys_the_dialog_does_not_know_about(tmp_path):
    cfg = ConfigManager(tmp_path / "c.json")
    cfg.set("env_vars", {"OLLAMA_KEEP_ALIVE": "5m"})
    cfg.set("max_steps", 9)

    cfg.update({"model_name": "ruder-ai-ko", "temperature": 0.7})

    reloaded = ConfigManager(tmp_path / "c.json")
    assert reloaded.get("model_name") == "ruder-ai-ko"
    assert reloaded.get("temperature") == 0.7
    # The bug: saving from the config GUI dropped these.
    assert reloaded.get("env_vars") == {"OLLAMA_KEEP_ALIVE": "5m"}
    assert reloaded.get("max_steps") == 9


def test_update_does_not_mutate_defaults(tmp_path):
    cfg = ConfigManager(tmp_path / "fresh.json")
    assert set(cfg.config) >= {"ollama_base_url", "temperature", "max_steps"}
    cfg.update({"model_name": "x"})
    assert cfg.config["model_name"] == "x"


# --- settings_from_config: GUI and CLI must resolve identically ---

def test_settings_from_config_reads_every_tuning_key(tmp_path):
    cfg = ConfigManager(tmp_path / "c.json")
    cfg.update({
        "model_name": "ruder-ai-ko",
        "ollama_base_url": "http://192.168.0.5:11434",
        "temperature": 0.42,
        "num_ctx": 8192,
        "max_tokens": 1024,
        "context_token_budget": 2048,
        "max_steps": 11,
        "max_replans": 4,
        "max_reflections": 3,
        "enable_reflection": False,
        "timeout": 42.0,
    })

    s = settings_from_config(cfg)
    assert s.model == "ruder-ai-ko"
    # The GUI used to hardcode 127.0.0.1 and ignore this entirely.
    assert s.ollama_host == "http://192.168.0.5:11434"
    assert s.temperature == 0.42
    assert s.num_ctx == 8192
    assert s.max_tokens == 1024
    assert s.context_token_budget == 2048
    assert s.max_steps == 11
    assert s.max_replans == 4
    assert s.max_reflections == 3
    assert s.enable_reflection is False
    assert s.timeout == 42.0


def test_settings_from_config_survives_corrupt_values(tmp_path):
    cfg = ConfigManager(tmp_path / "c.json")
    cfg.update({"temperature": "hot", "max_steps": "many", "ollama_base_url": ""})
    s = settings_from_config(cfg)
    assert s.temperature == 0.1        # RuderAISettings default
    assert s.max_steps == 5
    assert s.ollama_host == "http://127.0.0.1:11434"


def test_config_gui_temperature_default_matches_agent_default():
    from ruder_ai.core.config import DEFAULT_CONFIG
    from ruder_ai.core.settings import RuderAISettings

    # config_gui used to seed 0.3 while the agent ran at 0.1.
    assert DEFAULT_CONFIG["temperature"] == RuderAISettings().temperature


# --- ANSI stripping ---

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("\x1b[31mred\x1b[0m", "red"),
        ("\x1b[1;32mbold green\x1b[m", "bold green"),
        ("plain", "plain"),
        ("\x1b]0;title\x07after", "after"),   # OSC
    ],
)
def test_strip_ansi(raw, expected):
    assert shell_session.strip_ansi(raw) == expected


# --- Backend selection ---

IS_WINDOWS = os.name == "nt"


def test_backend_prefers_a_real_tty():
    """Windows should reach for ConPTY; POSIX for a PTY."""
    session = shell_session.make_shell()
    name = type(session).__name__
    if IS_WINDOWS:
        assert name in {"WindowsConPTYSession", "WindowsPipeSession"}
        if shell_session.conpty_available():
            assert name == "WindowsConPTYSession"
    else:
        assert name == "PosixPtySession"


def test_start_shell_always_yields_a_usable_session(tmp_path):
    """ConPTY may be present but unusable; start_shell must degrade, not fail."""
    session, note = shell_session.start_shell(
        cwd=str(tmp_path), env=os.environ.copy()
    )
    try:
        assert session.proc is not None
        if IS_WINDOWS and isinstance(session, shell_session.WindowsPipeSession):
            # A fallback must always explain itself so the UI can say why.
            assert note and "ConPTY" in note
    finally:
        session.reader_finished.set()
        session.close()


def test_conpty_backend_reports_availability():
    if not IS_WINDOWS:
        pytest.skip("ConPTY is Windows-only")
    assert isinstance(shell_session.conpty_available(), bool)


@pytest.mark.skipif(not IS_WINDOWS, reason="ConPTY is Windows-only")
def test_conpty_session_round_trip(tmp_path):
    """End-to-end ConPTY check; skipped where no console host can be allocated."""
    session = shell_session.WindowsConPTYSession()
    try:
        session.start(cwd=str(tmp_path), env=os.environ.copy(), cols=100, rows=30)
    except shell_session.ConPTYUnavailable as exc:
        pytest.skip(f"this host cannot allocate a pseudoconsole: {exc}")

    try:
        assert session.fileno() is None, "ConPTY is handle-based, not fd-based"
        assert session.proc is not None
        session.resize(120, 40)  # must not raise

        marker = "RUDER_CONPTY_PROBE"
        session.send(f"echo {marker}\r\n".encode("utf-8"))

        received = bytearray()
        deadline = time.time() + 15
        while time.time() < deadline and marker.encode() not in received:
            chunk = session.pump(4096)
            if chunk:
                received += chunk

        text = shell_session.strip_ansi(received.decode("utf-8", errors="replace"))
        assert marker in text, repr(text[:400])
    finally:
        session.reader_finished.set()
        session.close()


def test_shell_session_round_trip(tmp_path):
    """A command typed into the integrated terminal must come back out."""
    session, _ = shell_session.start_shell(
        cwd=str(tmp_path), env=os.environ.copy()
    )
    try:
        assert session.proc is not None
        marker = "RUDER_TERMINAL_PROBE"

        if IS_WINDOWS:
            session.send(f"echo {marker}\r\n".encode("utf-8"))
        else:
            session.send(f"echo {marker}\n".encode("utf-8"))

        received = bytearray()
        deadline = time.time() + 10
        while time.time() < deadline and marker.encode() not in received:
            chunk = session.pump(4096)
            if chunk:
                received += chunk
            else:
                time.sleep(0.05)

        text = shell_session.strip_ansi(received.decode("utf-8", errors="replace"))
        assert marker in text
    finally:
        session.reader_finished.set()
        session.close()


def test_close_is_idempotent(tmp_path):
    session, _ = shell_session.start_shell(
        cwd=str(tmp_path), env=os.environ.copy()
    )
    session.close()
    session.close()  # must not raise
    # Sending after close must be a no-op, not an exception.
    session.send(b"echo nope\n")
    assert session.pump() == b""


# --- GUI module: no third-party GUI toolkit ---

def _app_gui_path() -> Path:
    import ruder_ai.tui.app_gui  # noqa: F401  (ensure it is in sys.modules)

    return Path(ruder_ai.tui.app_gui.__file__)


def _imported_modules(path: Path) -> set[str]:
    """Top-level module names imported by *path*, via the AST (docstrings ignored)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_app_gui_uses_only_stdlib_tkinter():
    """The Studio must not depend on a binary GUI wheel any more."""
    imported = _imported_modules(_app_gui_path())
    assert "tkinter" in imported
    for banned in ("PyQt6", "PySide6", "PySide2", "PyQt5", "wx", "PySimpleGUI", "dearpygui"):
        assert banned not in imported, banned


def test_shell_session_has_no_gui_toolkit_dependency():
    imported = _imported_modules(Path(shell_session.__file__))
    for banned in ("PyQt6", "PySide6", "tkinter", "wx"):
        assert banned not in imported, banned


def test_app_gui_exposes_expected_surface():
    import ruder_ai.tui.app_gui as app_gui

    for attr in (
        "_create_agent",
        "_start_terminal",
        "_send_prompt",
        "_confirm_discard",
        "_append_terminal_text",
        "_move_selected_item",
    ):
        assert hasattr(app_gui.RuderAIVSCodeApp, attr), attr
    assert issubclass(app_gui.RuderAIVSCodeApp, tk.Tk)
    assert app_gui.launch_vscode_gui is not None


# --- Live tkinter window (subprocess) ---

def _run_gui_check(mode, tmp_path):
    """Run tests/_studio_gui_check.py out of process and surface its output."""
    import subprocess

    script = Path(__file__).with_name("_studio_gui_check.py")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, str(script), mode, str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=180,
    )
    if result.returncode != 0:
        if "no display" in (result.stderr or "") + (result.stdout or ""):
            pytest.skip("no display available for tkinter")
        pytest.fail(
            f"GUI check {mode!r} failed:\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def test_studio_window_boots_and_wires_everything(tmp_path):
    output = _run_gui_check("boot", tmp_path)
    for marker in ("round trip : OK", "ansi strip : OK", "lazy tree  : OK", "config     : OK"):
        assert marker in output, output
