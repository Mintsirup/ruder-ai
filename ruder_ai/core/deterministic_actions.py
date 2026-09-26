from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from ruder_ai.core.file_resolver import FileResolver
from ruder_ai.core.survey import (
    build_detail,
    build_overview,
    build_survey,
    format_overview,
    format_survey,
    is_targeted_question,
    wants_project_summary,
    wants_repeat,
    wants_survey,
)
from ruder_ai.verify.csharp_static import run_csharp_static_check


class DeterministicActionResolver:
    """Map unambiguous user intents to direct, non-LLM actions."""

    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.files = FileResolver(self.workspace)
        # Injected by AIAgent so a whole-project survey reuses the index that
        # is already built rather than scanning the workspace again.
        self.agent_index = None
        # A survey is ~48k characters; remembering that one was delivered is
        # what stops the second identical dump.
        self._survey_delivered = False

    @staticmethod
    def classify(prompt: str) -> str | None:
        text = str(prompt or '').lower()

        # A request about the shape of the whole project is answered from the
        # index, not by a plan. "일일히 분석해서 파일마다 기능 말해줘" used to
        # contain '기능' and '프로젝트', so it fell through to the mutation
        # pipeline, which read two files out of 190 and reported nothing about
        # the rest. Checked first, before any marker filter.
        if wants_survey(prompt):
            return 'project_survey'

        # "executor.py 자세히", "테스트 파일만 설명해줘". A 48 000-character
        # dump answers "describe every file" and nothing else; the follow-up
        # that gets asked after one is always narrower, so it is answered
        # narrowly. Checked before the summary because naming a file is a
        # stronger signal than asking what the project is.
        if is_targeted_question(prompt):
            return 'project_detail'

        # "이 프로젝트가 뭐 하는 곳이야?" used to fall through to the
        # mutation pipeline, which read a few files and reported "변경된
        # 파일이 없습니다" - an answer to a question nobody asked. It gets a
        # deterministic overview instead: correct, complete, and one screen.
        if wants_project_summary(prompt):
            return 'project_summary'

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
        if action == 'project_survey':
            return await self.project_survey(prompt)
        if action == 'project_detail':
            return await self.project_detail(prompt)
        if action == 'project_summary':
            return await self.project_summary(prompt)
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

    async def project_survey(self, prompt: str) -> dict[str, Any]:
        """Describe every indexed file, and state how many were covered.

        ``self.agent_index`` is injected by ``AIAgent`` so this reuses the
        already-built index instead of scanning the workspace a second time.

        A survey is ~48 000 characters. Serving the identical wall twice in a
        row in a chat pane is not an answer, it is a scroll, so a repeat
        request gets the overview plus the ways to go narrower instead -
        unless the user explicitly asked for the full thing again.
        """
        index = self.agent_index
        if index is None:
            index = self._build_index()

        survey = build_survey(index)
        roles = {
            role: len(bucket)
            for role, bucket in sorted(survey.by_role().items())
        }
        base = {
            'action': 'project_survey',
            'status': 'success',
            'covered': survey.covered,
            'total': survey.total,
            'unreadable': survey.unreadable,
            'roles': roles,
        }

        if self._survey_delivered and not wants_repeat(prompt):
            overview = build_overview(index)
            base['report'] = (
                f"전체 파일 조사는 방금 동일한 내용으로 전달했습니다 "
                f"({survey.covered}/{survey.total}개, 약 {len(format_survey(survey)):,}자).\n"
                "같은 내용을 다시 붙여넣는 대신 아래 개요와 좁혀서 보는 방법을 "
                "사용하세요. 전체 조사를 다시 출력하려면 '전체 조사 다시 보여줘' "
                "라고 요청하시면 됩니다.\n\n"
                + format_overview(overview)
                + "\n좁혀서 보기 예시:\n"
                "- `ruder_ai/core/executor.py` 는 어떤 일을 해?\n"
                "- 테스트 파일만 설명해줘\n"
                "- core 디렉터리 자세히\n"
            )
            base['repeated'] = True
            return base

        report = format_survey(survey)
        self._survey_delivered = True
        base['report'] = report
        return base

    async def project_summary(self, prompt: str) -> dict[str, Any]:
        """Answer "what is this project?" in one screen, without an LLM."""
        index = self.agent_index
        if index is None:
            index = self._build_index()

        overview = build_overview(index)
        return {
            'action': 'project_summary',
            'status': 'success',
            'covered': overview.covered,
            'total': overview.total,
            'files': overview.file_count,
            'symbols': overview.symbol_count,
            'language': overview.language,
            'build_system': overview.build_system,
            'roles': overview.roles,
            'report': format_overview(overview),
        }

    async def project_detail(self, prompt: str) -> dict[str, Any]:
        """Answer about one file, one role or one directory, not everything.

        Falls back to the overview when the narrowing hint matches nothing:
        an answer is still better than a full dump, and a full dump is what
        the user is already scrolling past.
        """
        index = self.agent_index
        if index is None:
            index = self._build_index()

        detail = build_detail(index, prompt)
        if detail is None:
            overview = build_overview(index)
            return {
                'action': 'project_detail',
                'status': 'success',
                'covered': overview.covered,
                'total': overview.total,
                'matched': 0,
                'report': format_overview(overview),
            }

        report, exact = detail
        return {
            'action': 'project_detail',
            'status': 'success',
            'covered': 1 if exact is not None else None,
            'matched': 1 if exact is not None else None,
            'report': report,
        }

    def _build_index(self):
        from ruder_ai.indexer.detector import ProjectDetector
        from ruder_ai.indexer.scanner import ProjectScanner
        from ruder_ai.indexer.symbol_indexer import SymbolIndexer

        from ruder_ai.indexer.detector import ProjectDetector
        from ruder_ai.indexer.scanner import ProjectScanner
        from ruder_ai.indexer.symbol_indexer import SymbolIndexer

        scanner = ProjectScanner(self.workspace)
        files = scanner.scan()
        return SymbolIndexer().build(
            self.workspace,
            files,
            ProjectDetector().detect(self.workspace, files),
            dict(scanner.inverted_index),
        )

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
