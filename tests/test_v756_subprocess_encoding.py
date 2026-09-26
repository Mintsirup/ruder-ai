"""Child-process output must round-trip regardless of the host code page.

On Windows a child's stdout is encoded with the ANSI code page (cp949 on a
Korean system) whenever it is a pipe rather than a console. Every one of these
skills decodes what it captures as UTF-8, so without an explicit fix the model
reads its own Korean output back as mojibake and concludes the code it just
wrote is broken.

The probe runs in a subprocess with ``PYTHONIOENCODING``/``PYTHONUTF8``
removed, so the tests fail on a machine that happens to have them exported -
which is exactly the situation that hid the bug.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

KOREAN = "안녕하세요! 무엇을 도와드릴까요?"

PROBE = r'''
import asyncio, json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, {repo!r})

from ruder_ai.core.platform import PLATFORM
from ruder_ai.skills.code_exec import ExecuteCodeSkill, ExecuteShellSkill
from ruder_ai.skills.git_ops import _run_git

KOREAN = {korean!r}


def make_repo(root: Path) -> None:
    """A real git repo whose commit subject is Korean."""
    def git(*args):
        subprocess.run(
            ["git", *args], cwd=root, check=True,
            capture_output=True, encoding="utf-8", errors="replace",
            env=PLATFORM.subprocess_env(),
        )

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "tester")
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", KOREAN)


async def main():
    out = {{}}
    ws = tempfile.mkdtemp(prefix="_enc_")

    r = await ExecuteCodeSkill().execute(
        code="print({korean!r})", workspace_path=ws)
    out["execute_code"] = r.get("stdout", "")

    r = await ExecuteCodeSkill().execute(
        code="import sys; sys.stderr.write({korean!r})", workspace_path=ws)
    out["execute_code_stderr"] = r.get("stderr", "")

    r = await ExecuteShellSkill().execute(
        command="python -c \"print({korean!r})\"", workspace_path=ws)
    out["execute_shell"] = r.get("stdout", "")

    repo = tempfile.mkdtemp(prefix="_encgit_")
    make_repo(Path(repo))
    r = await _run_git("log --oneline", repo)
    out["git"] = (r or {{}}).get("stdout", "")

    out["env"] = {{
        "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING", ""),
        "PYTHONUTF8": os.environ.get("PYTHONUTF8", ""),
    }}
    # Write to a file rather than stdout: the probe itself is running with a
    # cp949 pipe, so its own print would be the one thing still mojibake.
    Path({out_path!r}).write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")


asyncio.run(main())
'''


@pytest.fixture(scope="module")
def probe(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("encoding")
    script = tmp / "probe.py"
    result = tmp / "result.json"
    script.write_text(
        PROBE.format(repo=str(REPO), korean=KOREAN, out_path=str(result)),
        encoding="utf-8",
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONIOENCODING", "PYTHONUTF8")
    }
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(result.read_text(encoding="utf-8"))


def test_probe_actually_ran_without_a_utf8_override(probe):
    """Guard the guard: the probe must not inherit a UTF-8 environment."""
    assert probe["env"]["PYTHONIOENCODING"] == ""
    assert probe["env"]["PYTHONUTF8"] == ""


def test_execute_code_stdout_round_trips_korean(probe):
    assert KOREAN in probe["execute_code"], probe["execute_code"]


def test_execute_code_stderr_round_trips_korean(probe):
    assert KOREAN in probe["execute_code_stderr"], probe["execute_code_stderr"]


def test_execute_shell_stdout_round_trips_korean(probe):
    assert KOREAN in probe["execute_shell"], probe["execute_shell"]


def test_git_output_round_trips_a_korean_commit_subject(probe):
    assert KOREAN in probe["git"], probe["git"]


def test_subprocess_env_forces_utf8_and_layers_the_base():
    from ruder_ai.core.platform import PLATFORM

    env = PLATFORM.subprocess_env({"PATH": "/custom"})
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["PATH"] == "/custom", "an existing environment must survive"
    # Never mutate the caller's dict.
    base = {"PATH": "/custom"}
    PLATFORM.subprocess_env(base)
    assert base == {"PATH": "/custom"}


def test_subprocess_env_without_a_base_inherits_the_process():
    from ruder_ai.core.platform import PLATFORM

    env = PLATFORM.subprocess_env()
    assert env["PYTHONIOENCODING"] == "utf-8"
    for key, value in os.environ.items():
        assert env.get(key) == value, key


def test_console_fixup_exports_the_setting_to_children(monkeypatch):
    """`ruder-ai` entry point must propagate UTF-8 to everything it spawns."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    import importlib

    import ruder_ai.main as main_mod

    main_mod._force_utf8_console()
    assert os.environ["PYTHONIOENCODING"] == "utf-8"
    assert os.environ["PYTHONUTF8"] == "1"
    importlib.reload(main_mod)
