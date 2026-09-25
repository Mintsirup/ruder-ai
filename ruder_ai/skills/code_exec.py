"""Code execution skill for RuderAI Agent."""
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from ruder_ai.skills.base import BaseSkill
from ruder_ai.core.platform import PLATFORM


class ExecuteCodeSkill(BaseSkill):
    name = "execute_code"
    description = "파이썬 코드를 실행하고 실행 결과(stdout, stderr)를 반환합니다."

    async def execute(
        self,
        code: str,
        timeout: int = 10,
        workspace_path: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Python 코드를 임시 프로세스에서 실행

        workspace_path가 있으면 그 디렉터리를 cwd로 실행한다 (없으면
        list_directory와 같은 이유로 GUI 프로세스의 cwd를 그대로
        상속해 엉뚱한 위치에서 코드가 실행될 수 있다). workspace_path
        는 Executor가 자동으로 채워준다.
        """
        if not code or not str(code).strip():
            return {"status": "error", "error_type": "validation", "message": "code가 필요합니다."}
        first = str(code).strip().splitlines()[0].strip().lower()
        shell_prefixes = ("pip ", "pip3 ", "npm ", "npx ", "yarn ", "pnpm ", "mvn ", "mvnw ", "gradle ", "./gradlew ", "javac ", "java ", "cargo ", "go ")
        if first.startswith(shell_prefixes):
            return {
                "status": "error",
                "error_type": "validation",
                "message": "셸 명령처럼 보이는 입력입니다. execute_code는 Python 전용입니다. OS 명령은 execute_shell을 사용하세요.",
                "guard": "shell_command_in_python_tool",
            }
        try:
            workspace = Path(workspace_path).resolve() if workspace_path else None
            if workspace is not None:
                from ruder_ai.skills.code_exec import ensure_project_venv
                await ensure_project_venv(workspace)
            execution_code = str(code)
            if workspace is not None and (workspace / "src").is_dir():
                # Make common src-layout imports work for one-shot verification
                # code such as ``from game import main`` without requiring the
                # model to guess PYTHONPATH details.
                bootstrap = (
                    "import sys\n"
                    f"sys.path.insert(0, {str(workspace / 'src').__repr__()})\n"
                )
                execution_code = bootstrap + execution_code

            proc = await asyncio.create_subprocess_exec(
                PLATFORM.python_executable(workspace / ".venv" if workspace is not None else None), "-c", execution_code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path,
            )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return {"status": "error", "message": f"코드 실행 시간 초과 ({timeout}초 제한)"}

            return {
                "status": "success" if proc.returncode == 0 else "failed",
                "returncode": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace")
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

class ExecuteShellSkill(BaseSkill):
    """Execute an OS shell command in the workspace.

    Python-related commands automatically use the project-local ``.venv`` by
    prepending its Scripts/bin directory to PATH.  We intentionally do not
    call ``activate``/``deactivate``: activation is shell-stateful and is not
    needed for child processes.
    """
    name = "execute_shell"
    description = (
        "작업공간에서 운영체제 셸 명령을 실행합니다. Python 프로젝트에서는 "
        ".venv를 자동 생성하고 그 환경의 실행 파일을 PATH 우선순위로 사용합니다. "
        "전역 pip/python 대신 프로젝트 가상환경을 우선하며, activate/deactivate "
        "명령은 사용하지 않습니다."
    )

    async def execute(self, command: str, timeout: int = 120,
                      workspace_path: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        if not command or not str(command).strip():
            return {"status": "error", "error_type": "validation", "message": "command가 필요합니다."}
        workspace = Path(workspace_path).resolve() if workspace_path else None
        created_venv = False
        used_venv = False
        try:
            # Normalize common model-generated virtualenv activation wrappers.
            # Child processes already receive the project .venv through PATH, so
            # `source .venv/bin/activate` / `call .venv\Scripts\activate.bat`
            # and trailing `deactivate` are unnecessary and fail on the wrong shell.
            command_text = str(command).strip()
            command_text = _normalize_venv_shell_command(command_text, workspace)
            if not command_text:
                return {"status":"success","returncode":0,"stdout":"","stderr":"","command":"","executed_command":"","venv":bool(workspace and _looks_python_related(str(command), workspace)),"venv_created":False,"noop":True}
            env = None
            if workspace is not None and _looks_python_related(command, workspace):
                created_venv = await ensure_project_venv(workspace)
                env = _venv_environment(workspace)
                used_venv = True

            proc = await asyncio.create_subprocess_shell(
                command_text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace) if workspace else workspace_path,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=max(1, int(timeout))
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {"status": "error", "error_type": "timeout",
                        "message": f"셸 명령 실행 시간 초과 ({timeout}초 제한)",
                        "command": command_text, "venv": used_venv,
                        "venv_created": created_venv}

            return {
                "status": "success" if proc.returncode == 0 else "failed",
                "returncode": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "command": command_text,
                "executed_command": command_text,
                "venv": used_venv,
                "venv_created": created_venv,
            }
        except Exception as exc:
            return {"status": "error", "error_type": "unknown", "message": str(exc), "command": command_text if 'command_text' in locals() else str(command)}


async def ensure_project_venv(workspace: Path) -> bool:
    """Create a project-local .venv if missing. Return True when created."""
    workspace.mkdir(parents=True, exist_ok=True)
    venv = workspace / ".venv"
    py = PLATFORM.venv_python(workspace)
    if py.exists():
        return False
    # A previous interrupted venv creation may leave a corrupt/incomplete
    # .venv directory. Recreate it cleanly instead of asking Python venv to
    # merge into a broken tree (common on Windows).
    if venv.exists():
        import shutil
        try:
            shutil.rmtree(venv)
        except OSError as exc:
            raise RuntimeError(f"프로젝트 .venv 정리 실패: {exc}")
    proc = await asyncio.create_subprocess_exec(
        PLATFORM.python_executable(), "-m", "venv", str(venv),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(workspace)
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            "프로젝트 .venv 생성에 실패했습니다: " + stderr.decode("utf-8", errors="replace")
        )
    return True


def _venv_environment(workspace: Path) -> dict[str, str]:
    """Return a child-process environment that prefers the project venv."""
    import os
    env = dict(os.environ)
    venv = PLATFORM.venv_root(workspace)
    bin_dir = venv / ("Scripts" if PLATFORM.is_windows else "bin")
    env["VIRTUAL_ENV"] = str(venv)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env

def _looks_python_related(command: str, workspace: Path) -> bool:
    text = str(command).strip().lower()
    first = text.split()[0] if text.split() else ""
    python_markers = (
        ".py", "python", "py ", "py.exe", "pip", "pytest", "py_compile",
    )
    project_markers = ("requirements.txt", "pyproject.toml", "setup.py", "manage.py")
    return any(marker in text for marker in python_markers) or any((workspace / m).exists() for m in project_markers)


def _normalize_venv_shell_command(command: str, workspace: Path | None) -> str:
    """Convert activation/deactivation wrappers into a direct child command."""
    import re
    text = str(command).strip()

    # npm scripts occasionally point at a non-existent src/test.js while the
    # actual test.js lives at project root. Repair only this unambiguous case.
    if workspace is not None and re.match(r"^npm\s+test(?:\s|$)", text, re.IGNORECASE):
        try:
            import json as _json
            pkg_path = workspace / "package.json"
            root_test = workspace / "test.js"
            if pkg_path.is_file() and root_test.is_file():
                data = _json.loads(pkg_path.read_text(encoding="utf-8"))
                script = str(data.get("scripts", {}).get("test", ""))
                if "src/test.js" in script and not (workspace / "src/test.js").is_file():
                    data.setdefault("scripts", {})["test"] = script.replace("src/test.js", "test.js")
                    pkg_path.write_text(_json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        except Exception:
            pass

    # Java class execution after a source-tree javac compile needs the source
    # tree on the classpath when no separate build directory was created.
    m_java = re.match(r"^java\s+([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)\s*$", text)
    if workspace is not None and m_java and not re.search(r"(?:^|\s)-cp(?:\s|$)|(?:^|\s)-classpath(?:\s|$)", text):
        cls = m_java.group(1)
        if (workspace / "src/main/java").exists() and (workspace / "src/main/java" / (cls.replace(".", "/") + ".class")).is_file():
            text = f"java -cp src/main/java {cls}"

    # Pure activation/deactivation wrappers have no persistent child-process state.
    if re.fullmatch(r"(?:call\s+)?(?:source\s+|\.\s+)?[^&;]*(?:activate(?:\.bat|\.bash)?)", text, flags=re.IGNORECASE):
        return ""
    if re.fullmatch(r"(?:call\s+)?deactivate(?:\s+.*)?", text, flags=re.IGNORECASE):
        return ""

    # POSIX: source/. ./activate && CMD && deactivate
    m = re.match(r"^(?:source|\.)\s+[^&;]*(?:activate|activate\.bash)\s*&&\s*(.+?)\s*(?:&&\s*)?deactivate\s*$", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Windows: call ...\Scripts\activate.bat && CMD ... && call deactivate
    m = re.match(r"^call\s+[^&;]*(?:Scripts[\\/])?activate\.bat\s*&&\s*(.+?)\s*(?:&&\s*)?(?:call\s+)?deactivate(?:\s*>[^&]*)?\s*$", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Simple leading activation without a trailing deactivate.
    text = re.sub(r"^(?:source|\.|call)\s+[^&;]*(?:activate|activate\.bat)\s*&&\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*(?:&&|;)\s*(?:call\s+)?deactivate(?:\s*>[^&]*)?\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _wrap_with_venv_activation(command: str, workspace: Path) -> str:
    activate = PLATFORM.venv_activate(workspace)
    if PLATFORM.is_windows:
        # call is required for .bat files; preserve the command's exit code while
        # ensuring deactivate runs even when the command itself fails.
        escaped = str(activate).replace('"', '\"')
        return (
            f'call "{escaped}" && {command} & set "RUDER_AI_RC=%errorlevel%" & '
            f'call deactivate >nul 2>&1 & exit /b %RUDER_AI_RC%'
        )
    return (
        f'. "{activate}" && {command}; rc=$?; '
        f'deactivate >/dev/null 2>&1 || true; exit $rc'
    )
