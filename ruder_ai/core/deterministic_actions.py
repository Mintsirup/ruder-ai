from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from ruder_ai.core.file_resolver import FileResolver
from ruder_ai.verify.csharp_static import run_csharp_static_check


class DeterministicActionResolver:
    """Map unambiguous user intents to direct, non-LLM actions."""

    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.files = FileResolver(self.workspace)

    @staticmethod
    def classify(prompt: str) -> str | None:
        text = str(prompt or '').lower()

        # Action-oriented requests must reach the normal Planner/Executor
        # pipeline. Deterministic inspection is reserved for genuinely
        # read-only questions.
        # Pure read-only environment queries keep their deterministic path.
        environment_markers = ('현재 python 실행 환경', 'python 실행 환경', '파이썬 실행 환경', '프로젝트 경로', 'working directory', 'python executable')
        mutation_markers = ('만들', '생성', '추가', '수정', '변경', '고쳐', '실행해', '실행해서', '설치', '구성', '구축', '가상환경', '.venv', 'requirements.txt', '빌드', '테스트를 추가', '기능', '삭제', '리팩터링', 'rename', 'remove', 'create', 'add ', 'modify', 'change', 'fix', 'run ', 'install ')
        if any(x in text for x in environment_markers) and not any(x in text for x in mutation_markers):
            if any(x in text for x in ('확인', '알려', '보여', 'check', 'show')):
                return 'environment_check'

        action_markers = (
            '만들', '생성', '추가', '수정', '변경', '고쳐', '실행해', '실행해서', '설치', '구성', '구축', '프로젝트', '가상환경',
            '.venv', 'venv', 'requirements.txt', '빌드', '테스트를 추가',
            '기능', '삭제', '리팩터링', 'rename', 'remove', 'create',
            'add ', 'modify', 'change', 'fix', 'run ', 'install ',
        )
        has_action = any(x in text for x in action_markers)
        explicit_shell = any(x in text for x in (
            '셸 명령', 'shell command', 'python --version', 'pip --version',
        ))

        if explicit_shell or has_action:
            return None

        if any(x in text for x in ('현재 python 실행 환경', 'python 실행 환경', '파이썬 실행 환경', '프로젝트 경로', 'working directory', 'python executable')):
            return 'environment_check'
        if any(x in text for x in ('git 상태', 'git status', '변경사항 확인', '변경 사항 확인')):
            return 'git_status'
        if any(x in text for x in ('파일을 찾아', '파일 찾', 'file discovery', '파일 위치 확인')):
            return 'file_discovery'
        if '.cs' in text and any(x in text for x in ('컴파일 오류', 'c# 검사', 'csharp', '정적 검사', 'compile error')) and not any(x in text for x in ('수정', '고쳐', 'fix', 'repair', '해결')):
            return 'csharp_check'
        if ('.py' in text or 'python' in text or '파이썬' in text) and any(x in text for x in ('테스트', '검증', 'pytest', 'lint', '검사')):
            return 'python_check'
        return None

    async def execute(self, prompt: str) -> dict[str, Any] | None:
        action = self.classify(prompt)
        if action is None:
            return None
        if action == 'environment_check':
            return self.environment_check()
        if action == 'git_status':
            return self.git_status()
        if action == 'file_discovery':
            return self.file_discovery(prompt)
        if action == 'csharp_check':
            return await self.csharp_check(prompt)
        if action == 'python_check':
            return self.python_check(prompt)
        return None

    def environment_check(self) -> dict[str, Any]:
        tools = {name: shutil.which(name) for name in ('git', 'dotnet', 'node', 'npm')}
        return {
            'action': 'environment_check',
            'status': 'success',
            'os': platform.system(),
            'python': str(Path(sys.executable).resolve()),
            'python_version': sys.version.split()[0],
            'cwd': str(Path.cwd().resolve()),
            'workspace': str(self.workspace),
            'tools': {k: v for k, v in tools.items() if v},
        }

    def file_discovery(self, prompt: str) -> dict[str, Any]:
        names = self._mentioned_files(prompt)
        results = []
        for requested in names:
            resolved = self.files.resolve(requested)
            results.append({'requested': requested, 'found': bool(resolved), 'path': resolved})
        return {'action': 'file_discovery', 'status': 'success', 'files': results}

    async def csharp_check(self, prompt: str) -> dict[str, Any]:
        targets = self._mentioned_files(prompt, extensions={'.cs'})
        resolved = [self.files.resolve(x) or x for x in targets]
        report = await run_csharp_static_check(self.workspace, target_files=resolved or None)
        return {
            'action': 'csharp_check',
            'status': 'success' if report.passed else 'failed',
            'target_files': resolved,
            'result': report.log_text(),
            'skipped': report.skipped,
        }

    def python_check(self, prompt: str) -> dict[str, Any]:
        import py_compile
        targets = [self.files.resolve(x) for x in self._mentioned_files(prompt, extensions={'.py'})]
        targets = [x for x in targets if x]
        if not targets:
            targets = [p.relative_to(self.workspace).as_posix() for p in self.workspace.rglob('*.py') if '.git' not in p.parts and '__pycache__' not in p.parts][:32]
        failures = []
        for rel in targets:
            path = self.workspace / rel
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                failures.append(f'{rel}: {exc.msg}')
            except OSError as exc:
                failures.append(f'{rel}: {exc}')
        return {
            'action': 'python_check',
            'status': 'failed' if failures else 'success',
            'target_files': targets,
            'message': '\n'.join(failures) if failures else f'Python syntax check passed ({len(targets)} files)',
        }

    def git_status(self) -> dict[str, Any]:
        import subprocess
        try:
            completed = subprocess.run(['git', 'status', '--short', '--branch'], cwd=self.workspace, capture_output=True, text=True, timeout=30, check=False)
            return {'action': 'git_status', 'status': 'success' if completed.returncode == 0 else 'failed', 'result': completed.stdout.strip(), 'stderr': completed.stderr.strip(), 'returncode': completed.returncode}
        except (OSError, subprocess.SubprocessError) as exc:
            return {'action': 'git_status', 'status': 'failed', 'error': str(exc)}

    @staticmethod
    def _mentioned_files(prompt: str, extensions: set[str] | None = None) -> list[str]:
        import re
        matches = re.findall(r'(?<![A-Za-z0-9_./\\-])[A-Za-z0-9_.\\/-]+\.[A-Za-z0-9]+(?![A-Za-z0-9_./\\-])', prompt or '', flags=re.IGNORECASE)
        out = []
        for item in matches:
            ext = Path(item).suffix.lower()
            if extensions and ext not in extensions:
                continue
            item = item.replace('\\', '/')
            if item not in out:
                out.append(item)
        return out[:16]
