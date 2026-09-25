"""Auto Verify: 프로젝트 타입에 맞는 빌드/테스트/린트를 자동으로 실행."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from ruder_ai.indexer.models import ProjectInfo
from ruder_ai.core.file_resolver import FileResolver

from . import runners
from . import csharp_static
from . import unity
from .models import CheckResult, VerifyReport

_PROJECT_MARKERS = (
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "settings.gradle.kts", "package.json", "pyproject.toml", "requirements.txt",
    "setup.py", "go.mod", "Cargo.toml",
)

def _nearest_project_root(workspace: Path, target_files: list[str] | None) -> Path:
    if not target_files:
        return workspace
    roots: list[Path] = []
    for value in target_files:
        path = (workspace / str(value).replace("\\", "/")).resolve()
        if workspace not in path.parents and path != workspace:
            continue
        current = path if path.is_dir() else path.parent
        while current != workspace and workspace in current.parents:
            if any((current / marker).is_file() for marker in _PROJECT_MARKERS):
                roots.append(current)
                break
            if (current / "src").is_dir() and current.parent != workspace:
                roots.append(current)
                break
            current = current.parent
    if not roots:
        return workspace
    # If all targets share a nested project root, use the deepest one.
    candidate = roots[0]
    for root in roots[1:]:
        if str(root).startswith(str(candidate) + "/"):
            candidate = root
        elif str(candidate).startswith(str(root) + "/"):
            pass
        else:
            return workspace
    return candidate


class AutoVerifier:
    """ProjectInfo(언어/빌드시스템)를 보고 적용 가능한 검증만 골라 실행한다."""

    async def verify(
        self,
        project: ProjectInfo,
        workspace: Path | str,
        target_files: list[str] | None = None,
        scenario_entry: str | None = None,
        scenario_input: str | None = None,
        scenario_expected: list[str] | str | None = None,
    ) -> VerifyReport:
        workspace = Path(workspace).resolve()
        resolver = FileResolver(workspace)

        normalized_targets = self._normalize_target_files(workspace, target_files, resolver)
        verify_root = _nearest_project_root(workspace, normalized_targets)
        if verify_root != workspace:
            from ruder_ai.indexer.scanner import ProjectScanner
            from ruder_ai.indexer.detector import ProjectDetector
            nested_scanner = ProjectScanner(verify_root)
            nested_files = nested_scanner.scan()
            project = ProjectDetector().detect(verify_root, nested_files)
            normalized_targets = [
                str((workspace / rel).resolve().relative_to(verify_root)).replace("\\", "/")
                for rel in (normalized_targets or [])
                if (workspace / rel).resolve().is_relative_to(verify_root)
            ]
            workspace = verify_root
        checks = self._plan_checks(project, workspace, normalized_targets)
        if scenario_entry and scenario_input is not None:
            checks.append(
                runners.run_python_scenario_test(
                    workspace,
                    scenario_entry,
                    scenario_input,
                    expected_outputs=scenario_expected,
                )
            )

        results: list[CheckResult] = []

        for check in checks:
            result = await check
            results.append(result)

        return VerifyReport(results=results)

    def _plan_checks(
        self,
        project: ProjectInfo,
        workspace: Path,
        target_files: list[str] | None = None,
    ) -> list:
        checks = []

        build_system = project.build_system
        language = project.language

        if build_system == "Gradle":
            wrapper = runners.PLATFORM.wrapper_script(workspace, "gradlew")
            if wrapper is not None or shutil.which("gradle") is not None:
                checks.append(runners.run_gradle_build(workspace))
            else:
                java_files = [
                    str(p.relative_to(workspace)).replace("\\", "/")
                    for p in workspace.rglob("*.java")
                    if p.is_file() and not any(part in {".git", "build", "target", "out"} for part in p.parts)
                ]
                if java_files:
                    checks.append(runners.run_javac(workspace, java_files))
            if self._uses_spotless(workspace, gradle=True) and (wrapper is not None or shutil.which("gradle") is not None):
                checks.append(runners.run_spotless_gradle(workspace))

        elif build_system == "Maven":
            checks.append(runners.run_maven_build(workspace))
            if self._uses_spotless(workspace, gradle=False):
                checks.append(runners.run_spotless_maven(workspace))

        elif build_system == "Cargo":
            checks.append(runners.run_cargo_check(workspace))
            checks.append(runners.run_cargo_test(workspace))
            checks.append(runners.run_cargo_clippy(workspace))

        elif build_system == "Go":
            checks.append(runners.run_go_vet(workspace))
            checks.append(runners.run_go_test(workspace))

        python_targets = [f for f in (target_files or []) if Path(f).suffix.lower() == ".py"]
        python_targeted = bool(python_targets)
        if language == "Python" or build_system == "Python" or python_targeted:
            if not target_files or python_targeted:
                if self._has_python_tests(workspace):
                    checks.append(runners.run_pytest(workspace))
                elif python_targets:
                    # A target-specific compile check still works for a plain
                    # Python project with no pyproject/requirements metadata.
                    # This prevents verifier status from becoming NOT_RUN just
                    # because the project is a tiny src/ layout.
                    checks.append(runners.run_python_compile(workspace, python_targets))
                elif language == "Python" or build_system == "Python":
                    py_files = [
                        str(p.relative_to(workspace)).replace("\\", "/")
                        for p in workspace.rglob("*.py")
                        if p.is_file() and not any(part in {".git", ".venv", "__pycache__"} for part in p.parts)
                    ]
                    if py_files:
                        checks.append(runners.run_python_compile(workspace, py_files[:64]))
                if self._has_flake8_config(workspace):
                    checks.append(runners.run_flake8(workspace))
                if self._has_mypy_config(workspace):
                    checks.append(runners.run_mypy(workspace))

                # 구문 검사(python_compile)만으로는 NameError/AttributeError처럼
                # import 누락이나 미구현 메서드로 인한 런타임 오류를 절대
                # 잡아내지 못한다. pytest 스위트가 없는 소규모 프로젝트일수록
                # (게임/스크립트 등) 이런 문제가 "구문 검사 PASS"인 채로
                # 조용히 넘어가기 쉬우므로, 엔트리 포인트를 찾을 수 있으면
                # 실제로 한 번 기동해보는 스모크 테스트를 추가한다.
                if not self._has_python_tests(workspace):
                    entry_point = self._find_python_entry_point(workspace)
                    if entry_point:
                        checks.append(runners.run_python_smoke_test(workspace, entry_point))

        if language == "Java" and build_system == "Unknown":
            java_files = [
                str(p.relative_to(workspace)).replace("\\", "/")
                for p in workspace.rglob("*.java")
                if p.is_file() and not any(part in {".git", "build", "target", "out"} for part in p.parts)
            ]
            if target_files:
                targeted_java = [f for f in target_files if Path(f).suffix.lower() == ".java"]
                if targeted_java:
                    java_files = targeted_java
            if java_files:
                checks.append(runners.run_javac(workspace, java_files))

        # Unity projects: when the current workspace is a real Unity project,
        # run the source-level C# check and, when an Editor is configured, the
        # real Unity batchmode compilation check.
        # When the Tester is validating a concrete mutation, restrict this
        # check to the changed/targeted files so an unrelated pre-existing
        # error cannot hijack the current task's repair loop.
        # Targeted .cs verification is always applicable when the current
        # task explicitly changed or requested a C# file. This avoids a
        # project-detector mismatch (or a missing Unity framework marker)
        # silently turning the C# verifier into SKIP.
        if target_files:
            if self._targets_include_language(target_files, {".cs"}):
                checks.append(
                    csharp_static.run_csharp_static_check(
                        workspace, target_files=target_files,
                    )
                )
        elif language == "C#" and str(getattr(project, "framework", "") or "").lower() == "unity":
            checks.append(
                csharp_static.run_csharp_static_check(
                    workspace, target_files=target_files,
                )
            )
            if unity.is_unity_project(workspace):
                checks.append(unity.run_unity_batchmode_compile(workspace))
        elif unity.is_unity_project(workspace):
            # Detector mismatch must not hide Unity verification.
            if not target_files or self._targets_include_language(target_files, {".cs"}):
                checks.append(
                    csharp_static.run_csharp_static_check(
                        workspace, target_files=target_files,
                    )
                )
                checks.append(unity.run_unity_batchmode_compile(workspace))

        if build_system == "NPM":
            if self._has_eslint_config(workspace):
                checks.append(runners.run_eslint(workspace))
            if self._has_npm_test_script(workspace):
                checks.append(runners.run_npm_test(workspace))

        return checks

    @staticmethod
    def _normalize_target_files(workspace: Path, target_files: list[str] | None, resolver: FileResolver) -> list[str] | None:
        if not target_files:
            return None
        out: list[str] = []
        for value in target_files:
            if not value:
                continue
            resolved = resolver.resolve(str(value))
            candidate = resolved or str(value).replace("\\", "/").lstrip("/")
            if candidate not in out:
                out.append(candidate)
        return out or None

    @staticmethod
    def _targets_include_language(target_files: list[str], extensions: set[str]) -> bool:
        return any(Path(name).suffix.lower() in extensions for name in target_files)

    @staticmethod
    def _uses_spotless(workspace: Path, gradle: bool) -> bool:
        candidates = (
            ["build.gradle", "build.gradle.kts"]
            if gradle else ["pom.xml"]
        )
        for name in candidates:
            path = workspace / name
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "spotless" in text.lower():
                return True
        return False

    @staticmethod
    def _find_python_entry_point(workspace: Path) -> str | None:
        """실행 가능한 진입점 스크립트를 관례적인 위치에서 찾는다.

        `src/main.py` 같은 일반적인 레이아웃을 우선하고, 없으면 워크스페이스
        루트의 `main.py`를 본다. 못 찾으면 None을 반환해 스모크 테스트를
        건너뛴다 (엔트리 포인트를 추측해서 엉뚱한 파일을 실행하지 않기 위함).
        """
        candidates = (
            "src/main.py",
            "main.py",
            "src/app.py",
            "app.py",
        )
        for rel in candidates:
            if (workspace / rel).is_file():
                return rel
        return None

    @staticmethod
    def _has_python_tests(workspace: Path) -> bool:
        if (workspace / "tests").is_dir():
            return True
        if (workspace / "test").is_dir():
            return True
        for pattern in ("test_*.py", "*_test.py"):
            if next(workspace.rglob(pattern), None) is not None:
                return True
        return False

    @staticmethod
    def _has_flake8_config(workspace: Path) -> bool:
        for name in (".flake8", "tox.ini", "setup.cfg"):
            path = workspace / name
            if path.exists():
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if name == ".flake8" or "[flake8]" in text:
                    return True
        return False

    @staticmethod
    def _has_mypy_config(workspace: Path) -> bool:
        for name in ("mypy.ini", ".mypy.ini", "setup.cfg"):
            path = workspace / name
            if path.exists():
                if name != "setup.cfg":
                    return True
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if "[mypy]" in text:
                    return True
        pyproject = workspace / "pyproject.toml"
        if pyproject.exists():
            try:
                text = pyproject.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return False
            if "[tool.mypy]" in text:
                return True
        return False

    @staticmethod
    def _has_npm_test_script(workspace: Path) -> bool:
        package_json = workspace / "package.json"
        if not package_json.exists():
            return False
        try:
            text = package_json.read_text(encoding="utf-8", errors="ignore")
            data = json.loads(text)
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict):
            return False
        scripts = data.get("scripts")
        if not isinstance(scripts, dict):
            return False
        test_script = scripts.get("test")
        if not test_script or not isinstance(test_script, str):
            return False
        # `npm init`이 만드는 기본 placeholder는 항상 실패하도록
        # 되어 있어("Error: no test specified") 실제 테스트가 있다고
        # 볼 수 없다 — 이 경우는 건너뛴다.
        if "no test specified" in test_script.lower():
            return False
        return True

    @staticmethod
    def _has_eslint_config(workspace: Path) -> bool:
        names = (
            ".eslintrc", ".eslintrc.js", ".eslintrc.cjs",
            ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml",
        )
        for name in names:
            if (workspace / name).exists():
                return True
        package_json = workspace / "package.json"
        if package_json.exists():
            try:
                text = package_json.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return False
            if '"eslintConfig"' in text:
                return True
        return False