"""Regression tests for the v7.5.2 follow-up fixes.

* ``PlatformAdapter.command_args`` must not normalise the caller's path
  spelling (``/tmp/gradlew`` was being rewritten to ``\\tmp\\gradlew`` on
  Windows, silently pointing subprocess at a different program).
* ``AutoVerifier._plan_checks`` must not let a stray ``.py`` file drag the
  Python verification path into a project of another language.
* The CLI must survive a non-UTF-8 Windows console code page (cp949), which
  used to crash ``ruder-ai --help`` with UnicodeEncodeError.
"""
import sys
from types import SimpleNamespace

from ruder_ai.core.platform import PlatformAdapter
from ruder_ai.main import _force_utf8_console
from ruder_ai.verify.verifier import AutoVerifier


def test_posix_style_path_is_not_rewritten_on_windows():
    adapter = PlatformAdapter(
        name="windows",
        is_windows=True,
        executable_suffixes=(".exe", ".cmd", ".bat"),
        shell_executable="cmd.exe",
    )
    argv = adapter.command_args("/tmp/gradlew", ["build"])
    assert argv == ["/tmp/gradlew", "build"]


def test_batch_wrapper_still_routes_through_cmd():
    adapter = PlatformAdapter(
        name="windows",
        is_windows=True,
        executable_suffixes=(".exe", ".cmd", ".bat"),
        shell_executable="cmd.exe",
    )
    argv = adapter.command_args("C:/project/gradlew.bat", ["build"])
    assert argv[:4] == ["cmd.exe", "/d", "/c", "C:/project/gradlew.bat"]


def test_python_metadata_detects_real_python_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert AutoVerifier._looks_like_python_project(tmp_path) is True


def test_stray_py_file_is_not_a_python_project(tmp_path):
    (tmp_path / "music_player.py").write_text("x = 1\n", encoding="utf-8")
    assert AutoVerifier._looks_like_python_project(tmp_path) is False


def test_python_targets_skipped_for_csharp_unity_project(tmp_path):
    verifier = AutoVerifier()
    project = SimpleNamespace(language="C#", build_system="", framework="Unity")
    (tmp_path / "music_player.py").write_text("x = 1\n", encoding="utf-8")
    assert verifier._plan_checks(project, tmp_path, ["music_player.py"]) == []


def test_python_targets_still_run_for_mislabelled_python_project(tmp_path):
    verifier = AutoVerifier()
    project = SimpleNamespace(language="C#", build_system="", framework="")
    (tmp_path / "requirements.txt").write_text("rich\n", encoding="utf-8")
    checks = verifier._plan_checks(project, tmp_path, ["music_player.py"])
    assert checks
    for check in checks:
        close = getattr(check, "close", None)
        if close is not None:
            close()


def test_force_utf8_console_is_safe_and_idempotent():
    _force_utf8_console()
    _force_utf8_console()
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        encoding = getattr(stream, "encoding", "") or ""
        assert encoding.lower().replace("-", "") in {"utf8", "cp65001"}
