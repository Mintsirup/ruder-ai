from pathlib import Path

from ruder_ai.core.platform import PlatformAdapter


def test_posix_python_uses_bin(tmp_path, monkeypatch):
    adapter = PlatformAdapter(
        name="posix", is_windows=False, executable_suffixes=("",), shell_executable="/bin/sh"
    )
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    py = venv / "python"
    py.write_text("")
    assert adapter.python_executable(tmp_path / ".venv") == str(py)


def test_windows_python_uses_scripts(tmp_path):
    adapter = PlatformAdapter(
        name="windows", is_windows=True, executable_suffixes=(".exe", ".cmd", ".bat"), shell_executable="cmd.exe"
    )
    venv = tmp_path / ".venv" / "Scripts"
    venv.mkdir(parents=True)
    py = venv / "python.exe"
    py.write_text("")
    assert adapter.python_executable(tmp_path / ".venv") == str(py)


def test_windows_batch_wrapper_is_invoked_through_cmd():
    adapter = PlatformAdapter(
        name="windows", is_windows=True, executable_suffixes=(".exe", ".cmd", ".bat"), shell_executable="cmd.exe"
    )
    argv = adapter.command_args(Path(r"C:\project\gradlew.bat"), ["build"])
    assert argv[:3] == ["cmd.exe", "/d", "/c"]
    assert argv[-1] == "build"


def test_posix_wrapper_is_direct():
    adapter = PlatformAdapter(
        name="posix", is_windows=False, executable_suffixes=("",), shell_executable="/bin/sh"
    )
    argv = adapter.command_args("/tmp/gradlew", ["build"])
    assert argv == ["/tmp/gradlew", "build"]
