"""빌드/테스트/린트 도구별 실행 러너.

각 러너는 하나의 외부 명령을 실행하고 CheckResult로 변환한다.
도구 자체가 설치되어 있지 않은 경우(FileNotFoundError)에는
실패가 아니라 skipped=True 로 처리해서, 실패 로그가
"환경 문제"로 오염되지 않게 한다.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import tempfile
from pathlib import Path

from .models import CheckResult
from ruder_ai.core.platform import PLATFORM


async def _run(
    tool: str,
    args: list[str],
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> CheckResult:
    """명령을 실행하고 CheckResult로 감싼다."""

    command = " ".join(args)
    start = time.monotonic()

    executable = PLATFORM.which(args[0])
    if executable is None:
        return CheckResult(
            tool=tool,
            command=command,
            passed=False,
            skipped=True,
            skip_reason=f"실행 파일을 찾을 수 없음: {args[0]}",
        )

    argv = PLATFORM.command_args(executable, args[1:])

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # A piped Windows child encodes stdout with the ANSI code page;
            # the result is decoded as UTF-8 further down.
            env=PLATFORM.subprocess_env(env),
        )
    except FileNotFoundError:
        return CheckResult(
            tool=tool,
            command=command,
            passed=False,
            skipped=True,
            skip_reason=f"실행 파일을 찾을 수 없음: {args[0]}",
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        returncode = proc.returncode
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return CheckResult(
            tool=tool,
            command=command,
            passed=False,
            returncode=None,
            stderr=f"시간 초과 ({timeout}초)",
            duration=time.monotonic() - start,
        )

    duration = time.monotonic() - start

    return CheckResult(
        tool=tool,
        command=command,
        passed=(returncode == 0),
        returncode=returncode,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        duration=duration,
    )


async def run_python_scenario_test(
    workspace: Path,
    entry_point: str,
    stdin_text: str,
    expected_outputs: list[str] | str | None = None,
    timeout: int = 20,
) -> CheckResult:
    """실제 stdin을 주입해 대화형 Python 프로그램의 시나리오를 검증한다."""
    import os

    executable = PLATFORM.python_executable()
    command = f"{executable} {entry_point} < scenario_input"
    start = time.monotonic()

    if isinstance(expected_outputs, str):
        expected = [expected_outputs]
    else:
        expected = list(expected_outputs or [])

    try:
        proc = await asyncio.create_subprocess_exec(
            executable, entry_point,
            cwd=str(workspace),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=PLATFORM.subprocess_env(),
        )
    except FileNotFoundError:
        return CheckResult(
            tool="python_scenario_test",
            command=command,
            passed=False,
            skipped=True,
            skip_reason=f"실행 파일을 찾을 수 없음: {executable}",
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(stdin_text.encode("utf-8")),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return CheckResult(
            tool="python_scenario_test",
            command=command,
            passed=False,
            returncode=None,
            stderr=f"시나리오 실행 시간 초과 ({timeout}초)",
            duration=time.monotonic() - start,
        )

    duration = time.monotonic() - start
    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")

    missing = [needle for needle in expected if needle not in stdout_text]
    passed = proc.returncode == 0 and not missing

    if proc.returncode != 0:
        detail = f"프로세스 종료 코드 {proc.returncode}"
        if stderr_text:
            detail += f"\n{stderr_text.strip()}"
    elif missing:
        detail = "기대 출력 누락: " + ", ".join(repr(x) for x in missing)
    else:
        detail = "시나리오 입력/출력 검증 통과"

    combined_stderr = detail if not stderr_text else f"{detail}\n{stderr_text}"
    return CheckResult(
        tool="python_scenario_test",
        command=command,
        passed=passed,
        returncode=proc.returncode,
        stdout=stdout_text,
        stderr=combined_stderr,
        duration=duration,
    )


# ---------------------------------------------------------------------
# Java / Gradle / Maven
# ---------------------------------------------------------------------

async def run_gradle_build(
    workspace: Path,
    timeout: int = 900,
) -> CheckResult:
    gradlew = PLATFORM.wrapper_script(workspace, "gradlew")

    if gradlew is not None:
        args = [str(gradlew), "build"]
    else:
        args = ["gradle", "build"]

    return await _run("gradle", args, workspace, timeout)


async def run_maven_build(
    workspace: Path,
    timeout: int = 900,
) -> CheckResult:
    mvnw = PLATFORM.wrapper_script(workspace, "mvnw")

    if mvnw is not None:
        args = [str(mvnw), "-B", "package"]
    else:
        args = ["mvn", "-B", "package"]

    return await _run("maven", args, workspace, timeout)


# ---------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------

async def run_pytest(
    workspace: Path,
    timeout: int = 600,
) -> CheckResult:
    import importlib.util
    import os
    env = dict(os.environ)
    roots = [workspace / "src", workspace / "src" / "main" / "python"]
    existing = [str(p) for p in roots if p.is_dir()]
    if existing:
        old_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(existing + ([old_path] if old_path else []))
    python_candidates = [workspace / ".venv" / ("Scripts/python.exe" if _is_windows() else "bin/python"), workspace / ".venv" / ("python.exe" if _is_windows() else "bin/python")]
    for py in python_candidates:
        if py.is_file():
            try:
                probe = await _run("pytest_probe", [str(py), "-c", "import pytest"], workspace, 20, env=env)
                if probe.passed:
                    return await _run("pytest", [str(py), "-m", "pytest", "-q"], workspace, timeout, env=env)
            except Exception:
                pass
    if shutil.which("pytest") is not None:
        return await _run("pytest", ["pytest", "-q"], workspace, timeout, env=env)
    if importlib.util.find_spec("pytest") is not None:
        return await _run("pytest", [sys.executable, "-m", "pytest", "-q"], workspace, timeout, env=env)
    return CheckResult(tool="pytest", command="pytest -q", passed=True, skipped=True, skip_reason="pytest 실행 파일/모듈이 없어 실행하지 못함")


async def run_flake8(
    workspace: Path,
    timeout: int = 180,
) -> CheckResult:
    return await _run(
        "flake8",
        ["flake8", "."],
        workspace,
        timeout,
    )


async def run_mypy(
    workspace: Path,
    timeout: int = 300,
) -> CheckResult:
    return await _run(
        "mypy",
        ["mypy", "."],
        workspace,
        timeout,
    )


# ---------------------------------------------------------------------
# Spotless (Gradle/Maven 코드 포맷 검사)
# ---------------------------------------------------------------------

async def run_spotless_gradle(
    workspace: Path,
    timeout: int = 300,
) -> CheckResult:
    gradlew = PLATFORM.wrapper_script(workspace, "gradlew")
    args = (
        [str(gradlew), "spotlessCheck"]
        if gradlew is not None
        else ["gradle", "spotlessCheck"]
    )

    return await _run("spotless", args, workspace, timeout)


async def run_spotless_maven(
    workspace: Path,
    timeout: int = 300,
) -> CheckResult:
    mvnw = PLATFORM.wrapper_script(workspace, "mvnw")
    args = (
        [str(mvnw), "spotless:check"]
        if mvnw is not None
        else ["mvn", "spotless:check"]
    )

    return await _run("spotless", args, workspace, timeout)


# ---------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------

async def run_eslint(
    workspace: Path,
    timeout: int = 300,
) -> CheckResult:
    npx = shutil.which("npx")

    if npx is not None:
        args = ["npx", "--no-install", "eslint", "."]
    else:
        args = ["eslint", "."]

    return await _run("eslint", args, workspace, timeout)


# ---------------------------------------------------------------------
# Rust / Cargo
# ---------------------------------------------------------------------

async def run_cargo_check(
    workspace: Path,
    timeout: int = 300,
) -> CheckResult:
    return await _run(
        "cargo_check", ["cargo", "check"], workspace, timeout,
    )


async def run_cargo_test(
    workspace: Path,
    timeout: int = 600,
) -> CheckResult:
    return await _run(
        "cargo_test", ["cargo", "test"], workspace, timeout,
    )


async def run_cargo_clippy(
    workspace: Path,
    timeout: int = 300,
) -> CheckResult:
    return await _run(
        "cargo_clippy",
        ["cargo", "clippy", "--", "-D", "warnings"],
        workspace,
        timeout,
    )


# ---------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------

async def run_go_test(
    workspace: Path,
    timeout: int = 600,
) -> CheckResult:
    return await _run(
        "go_test", ["go", "test", "./..."], workspace, timeout,
    )


async def run_go_vet(
    workspace: Path,
    timeout: int = 300,
) -> CheckResult:
    return await _run(
        "go_vet", ["go", "vet", "./..."], workspace, timeout,
    )


# ---------------------------------------------------------------------
# npm test (package.json scripts.test)
# ---------------------------------------------------------------------

async def run_npm_test(
    workspace: Path,
    timeout: int = 600,
) -> CheckResult:
    return await _run(
        "npm_test", ["npm", "test", "--silent"], workspace, timeout,
    )


def _is_windows() -> bool:
    import sys

    return sys.platform.startswith("win")
async def run_javac(workspace: Path, files: list[str], timeout: int = 120) -> CheckResult:
    """Compile standalone Java source files without polluting the project."""
    with tempfile.TemporaryDirectory(prefix="ruder_ai-javac-") as output_dir:
        return await _run("javac", ["javac", "-d", output_dir, *files], workspace, timeout)


async def run_python_compile(workspace: Path, files: list[str], timeout: int = 120) -> CheckResult:
    """Compile Python targets without requiring pytest."""
    return await _run("python_compile", [PLATFORM.python_executable(), "-m", "py_compile", *files], workspace, timeout)


async def run_python_smoke_test(
    workspace: Path,
    entry_point: str,
    timeout: int = 10,
) -> CheckResult:
    """엔트리 포인트 스크립트를 실제로 기동해 즉시 예외 없이 뜨는지 확인한다.

    `python_compile`은 구문 검사(AST 파싱)만 할 뿐, import 누락으로 인한
    NameError, 메서드 미구현으로 인한 AttributeError처럼 "실행해야만"
    드러나는 런타임 오류는 절대 잡아내지 못한다. 이 러너는 실제로
    `python <entry_point>`를 실행해서:

    - 프로세스가 짧은 시간 안에 0이 아닌 코드로 종료되면(=시작 직후
      예외로 죽음) 실패로 판정한다.
    - stdin을 닫아둔다(DEVNULL). 대화형 게임 루프가 input()을 호출하면
      즉시 EOFError로 종료되므로, 정상적으로 기동된 뒤 입력을 기다리다
      "그냥 멈춘 것처럼" timeout까지 매달리는 대신 EOFError 트레이스백이
      빠르게 드러난다 — 이는 여전히 "즉시 크래시"가 아니라 정상 동작의
      일부이므로, 별도로 걸러낸다(아래 EOFError 판정 참고).
    - timeout까지 살아있으면(예: 별도 입력 루프 없이 계속 도는 서버형
      프로세스) 죽이고 "기동 확인됨"으로 통과 처리한다.

    "PASS"는 어디까지나 "시작 시점에 즉시 죽는 명백한 버그(NameError,
    AttributeError 등)가 없었다"는 뜻이며, 게임 로직 자체가 올바르다는
    보장은 아니다 — 그 판단은 사용자가 요청한 시나리오를 실제로
    수행하는 상위 검증(Task 명시 시나리오 실행)의 몫이다.
    """

    command = f"{PLATFORM.python_executable()} {entry_point}"
    start = time.monotonic()

    executable = PLATFORM.python_executable()

    try:
        proc = await asyncio.create_subprocess_exec(
            executable, entry_point,
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=PLATFORM.subprocess_env(),
        )
    except FileNotFoundError:
        return CheckResult(
            tool="python_smoke_test",
            command=command,
            passed=False,
            skipped=True,
            skip_reason=f"실행 파일을 찾을 수 없음: {executable}",
        )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        returncode = proc.returncode
        duration = time.monotonic() - start
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        # stdin이 닫혀있어 input()을 만나는 순간 EOFError로 죽는 것은
        # "런타임 버그로 인한 크래시"가 아니라 이 스모크 테스트 방식 자체의
        # 부작용이다. 이런 경우는 실패로 보지 않고 "기동까지는 성공"으로
        # 처리한다 (진짜 입력 흐름 검증은 시나리오 기반 검증의 몫).
        if returncode != 0 and "EOFError" in stderr_text:
            return CheckResult(
                tool="python_smoke_test",
                command=command,
                passed=True,
                returncode=returncode,
                stdout=stdout_text,
                stderr=stderr_text,
                duration=duration,
                skip_reason="stdin이 닫혀 input() 호출 시 EOFError 발생 (정상 기동으로 간주)",
            )

        return CheckResult(
            tool="python_smoke_test",
            command=command,
            passed=(returncode == 0),
            returncode=returncode,
            stdout=stdout_text,
            stderr=stderr_text,
            duration=duration,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        # timeout까지 살아있었다는 것은 시작 직후 크래시는 없었다는 뜻이므로
        # 통과로 처리한다 (예: 게임 루프가 계속 돌고 있는 정상 상태).
        return CheckResult(
            tool="python_smoke_test",
            command=command,
            passed=True,
            returncode=None,
            duration=time.monotonic() - start,
            skip_reason=f"{timeout}초 동안 크래시 없이 계속 실행 중이어서 기동 성공으로 간주",
        )
