"""Whole-project survey.

"이 프로젝트 일일히 분석해서 파일마다 기능 일일히 말해줘" produced an answer
covering two files out of 190, with nothing in the output saying the other 188
had been skipped. Two things were wrong:

* There was no way to hold 190 files in a prompt, so the planner read two and
  called it done.
* Coverage was never reported, so a partial answer was indistinguishable from a
  complete one.

The project index already knows every file, its size, its symbols and its
imports. A survey built from it is complete by construction, costs no LLM
call, and cannot silently truncate - the number of files described is the
number of files in the index.

The per-file line is derived from the file's own docstring and structure
("what is this for"), not from an LLM guessing at its contents.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from ruder_ai.indexer.models import ProjectIndex

#: Directories whose name alone determines what a file is for, checked from
#: the most specific to the least.
_ROLE_BY_DIR = (
    ("tests", "테스트"),
    ("test", "테스트"),
    ("indexer", "프로젝트 인덱싱"),
    ("semantic", "의미 검색"),
    ("verify", "빌드/테스트 검증"),
    ("skills", "도구(Tool) 구현"),
    ("agents", "역할 파이전"),
    ("context", "컨텍스트 조립"),
    ("patch", "패치 적용"),
    ("tui", "터미널/데스크톱 UI"),
    ("core", "핵심 실행 로직"),
)

#: Universal filename overrides. Checked *before* the directory, because
#: ``__init__.py`` means the same thing everywhere while ``main.py`` does not.
_FILENAME_ROLE = {
    "__init__.py": "패키지 초기화",
    "__main__.py": "엔트리포인트",
    "conftest.py": "테스트 설정",
}

#: Filenames that only mean something at the top level.
_ROOT_FILENAME_ROLE = {
    "main.py": "CLI 엔트리포인트",
    "models.py": "데이터 모델",
    "settings.py": "설정",
    "config.py": "설정",
    "permissions.py": "Tool 권한 정책",
}

#: Top-level files that are configuration or documentation rather than code.
#: Keys carry the leading dot because that is how the scanner reports an
#: extension; looking them up without it silently classified every root
#: document and config file as "기타".
_ROOT_FILE_ROLE = {
    ".md": "문서",
    ".markdown": "문서",
    ".rst": "문서",
    ".txt": "문서",
    ".toml": "프로젝트 설정",
    ".cfg": "프로젝트 설정",
    ".ini": "프로젝트 설정",
    ".json": "프로젝트 설정",
    ".yml": "프로젝트 설정",
    ".yaml": "프로젝트 설정",
}

_BENCHMARK_DIR = "benchmarks"

#: "Every file" style requests. These are the ones that used to truncate.
#: The user asked for *every* file described. This is the set of requests
#: that used to truncate: 190 files did not fit in a prompt, two were read,
#: and nothing said the other 188 had been skipped.
_SURVEY_MARKERS = (
    "일일히", "하나씩", "전부 다", "전부", "모든 파일", "전체 파일", "파일마다",
    "각 파일", "전체 분석", "전체적으로 분석", "전부 분석", "전체 구조",
    "프로젝트 전체", "워크스페이스 전체", "코드베이스 전체", "전체 파일",
    "every file", "each file", "all files", "whole project", "entire project",
    "full project", "whole codebase", "codebase overview", "all of it",
)

#: Words that mean "all of it". Paired with a project noun these upgrade a
#: plain "analyze the project" into "describe every part of it", which is the
#: distinction the user draws with "모든" / "전체" / "every".
_COMPLETENESS_MARKERS = (
    "모든", "전부", "전체", "모두", "일일히", "하나씩", "각각",
    "all", "every", "each", "whole", "entire", "full", "every single",
)

#: The nouns that stand for "the whole thing".
_PROJECT_NOUNS = (
    "프로젝트", "코드베이스", "워크스페이스", "저장소", "리포지토리",
    "코드", "프로그램", "소스", "소스코드", "모듈", "컴포넌트", "클래스",
    "파일",
    "project", "codebase", "repository", "repo", "module", "component",
    "package", "class", "file",
)

#: The verbs that mean "tell me about it", as opposed to "change it".
_UNDERSTANDING_MARKERS = (
    "뭐", "무엇", "어떻", "어떤", "어떻게", "소개", "개요", "요약", "설명",
    "분석", "이해", "파악", "구성", "구조", "기능", "목적", "용도",
    "what", "which", "how", "tell me", "describe", "explain", "overview",
    "summary", "structure", "analyse", "analyze", "about",
)

#: A concrete edit target means the user wants a change, not a description.
_EDIT_MARKERS = (
    "고쳐", "고치고", "고쳐서", "수정해", "만들어", "생성해", "삭제해", "추가해",
    "변경해", "바꿔", "넣어줘", "추출해", "리팩터", "정리해", "이동해", "복사",
    "실행해", "돌려", "테스트 추가", "버그", "고장", "오류", "에러",
    "fix ", "refactor", "rewrite", "implement", "delete", "remove",
    "debug", "repair", "patch", "update",
)

#: Korean particles sit between the noun and the verb that follows it, so
#: natural text reads "프로젝트" + "를" + " 분석" while markers are written
#: particle-free. Enumerating every spelling does not scale; stripping the
#: particles does. The lookbehind keeps a particle that *starts* a word
#: ("이 워크스페이스") and the lookahead keeps one that is part of a word
#: ("이슈", "사이"), so this is a normalisation, not a guess.
#: Longer particles come first because alternation is first-match.
_PARTICLE_RE = re.compile(
    r"(?<=[가-힣A-Za-z0-9_])"
    r"(?:이라고|이라는|으로써|으로서|라고|까지|부터|만큼|처럼|보다|"
    r"이나|거나|든지|에서|에게|한테|으로|로서|이랑|과의)"
    r"|(?<=[가-힣A-Za-z0-9_])(?:을|를|이|가|은|는|의|에|와|과|도|만|로|야|랑)"
    r"(?![가-힣])"
)

_DOCSTRING_RE = re.compile(r'"""(.*?)"""', re.DOTALL)


def _normalize(prompt: str) -> str:
    """Lowercase and drop the particles that break marker adjacency."""
    return _PARTICLE_RE.sub("", str(prompt or "").strip().lower())


def _wants(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_completeness_request(text: str) -> bool:
    """A per-file marker, or an explicit "all of it" aimed at the project."""
    if _wants(text, _SURVEY_MARKERS):
        return True
    return _wants(text, _COMPLETENESS_MARKERS) and _wants(text, _PROJECT_NOUNS)


def wants_survey(prompt: str) -> bool:
    """True when the request asks for every file to be described."""
    text = _normalize(prompt)
    if not text or _wants(text, _EDIT_MARKERS):
        return False
    return _is_completeness_request(text)


def wants_project_summary(prompt: str) -> bool:
    """True for "what is this project?" - an overview, not a file-by-file tour.

    Keyed on a project noun plus an understanding verb rather than on fixed
    phrases, so the particles between them ("프로젝트가", "프로젝트는") do not
    have to be enumerated. Asking for the whole project is a separate request
    and is checked first.
    """
    text = _normalize(prompt)
    if not text or _wants(text, _EDIT_MARKERS) or _is_completeness_request(text):
        return False
    return _wants(text, _PROJECT_NOUNS) and _wants(text, _UNDERSTANDING_MARKERS)


@dataclass(slots=True)
class FileRecord:
    path: str
    extension: str
    size: int
    lines: int
    role: str
    summary: str
    symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Survey:
    workspace: Path
    project: str
    language: str
    build_system: str
    files: list[FileRecord] = field(default_factory=list)
    #: Files present in the index but unreadable on disk at survey time.
    unreadable: list[str] = field(default_factory=list)

    @property
    def covered(self) -> int:
        return len(self.files)

    @property
    def total(self) -> int:
        """Every file the index knows about - the denominator."""
        return len(self.files) + len(self.unreadable)

    def by_role(self) -> dict[str, list[FileRecord]]:
        grouped: dict[str, list[FileRecord]] = {}
        for record in self.files:
            grouped.setdefault(record.role, []).append(record)
        return grouped


def _role_for(relative: str, package_dirs: frozenset[str] = frozenset()) -> str:
    parts = relative.split("/")
    name = parts[-1]

    if name in _FILENAME_ROLE:
        return _FILENAME_ROLE[name]

    # ``<package>/main.py`` is the entrypoint of that package. Without this
    # the single most important file in the project falls through to "기타",
    # because the root-level rule below only looks at top-level files.
    if name == "main.py" and len(parts) == 2 and parts[0] in package_dirs:
        return _ROOT_FILENAME_ROLE["main.py"]

    for part in parts[:-1]:
        if part == _BENCHMARK_DIR:
            return "벤치마크"
        for prefix, role in _ROLE_BY_DIR:
            if part == prefix:
                return role

    if len(parts) == 1:
        if name in _ROOT_FILENAME_ROLE:
            return _ROOT_FILENAME_ROLE[name]
        stem, dot, extension = name.rpartition(".")
        if not dot:
            # ``"LICENSE".rpartition(".")`` is ``('', '', 'LICENSE')`` - the
            # whole name lands in the last slot, not an empty extension.
            return "루트 파일"
        if not stem:
            # A dotfile: .gitignore, .editorconfig, .pre-commit-config.yaml
            return "프로젝트 설정"
        dotted = f".{extension.lower()}"
        if dotted in _ROOT_FILE_ROLE:
            return _ROOT_FILE_ROLE[dotted]

    return "기타"


def _first_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    for stop in (". ", ".\n", "! ", "? "):
        index = text.find(stop)
        if index > 20:
            return text[: index + 1].strip()
    return text[:160].strip()


def _python_facts(source: str) -> tuple[str, list[str], list[str]]:
    """(summary, public symbols, top-level imports) for a Python module."""
    summary = ""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        match = _DOCSTRING_RE.search(source)
        return (_first_sentence(match.group(1)) if match else ""), [], []

    doc = ast.get_docstring(tree)
    if doc:
        summary = _first_sentence(doc)

    symbols: list[str] = []
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                symbols.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return summary, symbols, imports


def build_survey(
    index: ProjectIndex,
    *,
    max_symbols: int = 8,
    include_non_source: bool = True,
) -> Survey:
    """Describe every file the index knows about, from the filesystem."""
    workspace = Path(index.workspace)
    project = getattr(index, "project", None)

    records: list[FileRecord] = []
    unreadable: list[str] = []
    symbols_by_file: dict[str, list[str]] = {}
    for symbol in index.symbols:
        symbols_by_file.setdefault(symbol.file, []).append(symbol.name)

    # Top-level directories that are real packages, i.e. they ship an
    # ``__init__.py`` of their own. Derived from the index, not from a name
    # list, so a renamed or vendored package is still recognised.
    package_dirs = frozenset(
        file.relative_path.split("/", 1)[0]
        for file in index.files
        if file.relative_path.count("/") == 1
        and file.relative_path.endswith("/__init__.py")
    )

    for file in index.files:
        relative = file.relative_path
        if not include_non_source and file.extension not in (".py", ".java", ".js", ".ts", ".cs"):
            continue

        path = workspace / relative
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            unreadable.append(relative)
            records.append(
                FileRecord(
                    path=relative,
                    extension=file.extension,
                    size=file.size,
                    lines=0,
                    role=_role_for(relative, package_dirs),
                    summary="(파일을 읽을 수 없음)",
                    symbols=symbols_by_file.get(relative, [])[:max_symbols],
                )
            )
            continue

        lines = source.count("\n") + 1
        summary, symbols, imports = ("", [], [])
        if file.extension == ".py":
            summary, symbols, imports = _python_facts(source)
        elif not summary:
            match = _DOCSTRING_RE.search(source) or re.search(
                r"^\s*(?://|#)\s*(.+)$", source, re.MULTILINE
            )
            summary = _first_sentence(match.group(1)) if match else ""

        if not symbols:
            symbols = symbols_by_file.get(relative, [])

        records.append(
            FileRecord(
                path=relative,
                extension=file.extension,
                size=file.size,
                lines=lines,
                role=_role_for(relative, package_dirs),
                summary=summary or "(문서 주석 없음)",
                symbols=symbols[:max_symbols],
                imports=sorted(set(imports))[:6],
            )
        )

    records.sort(key=lambda r: (r.role, r.path))
    return Survey(
        workspace=workspace,
        project=str(getattr(project, "root", workspace)),
        language=str(getattr(project, "language", "?") or "?"),
        build_system=str(getattr(project, "build_system", "?") or "?"),
        files=records,
        unreadable=unreadable,
    )


def format_survey(
    survey: Survey,
    *,
    group_by_role: bool = True,
) -> str:
    """Render the survey, always stating coverage explicitly."""
    total_bytes = sum(r.size for r in survey.files)
    lines: list[str] = [
        f"# {survey.workspace.name} 프로젝트 전체 구성",
        "",
        f"- 작업공간: `{survey.workspace}`",
        f"- 언어/빌드: {survey.language} / {survey.build_system}",
        f"- 파일: **{survey.covered}/{survey.total}**개 "
        f"({total_bytes / 1024:.0f} KiB) "
        f"({'100%' if survey.covered == survey.total else '일부 판독 불가'})",
        "",
    ]

    if survey.unreadable:
        lines.append(f"> 읽을 수 없어 설명을 생략한 파일: {len(survey.unreadable)}개")
        for path in survey.unreadable[:10]:
            lines.append(f"> - `{path}`")
        lines.append("")

    if group_by_role:
        grouped = survey.by_role()
        for role in sorted(grouped):
            bucket = grouped[role]
            lines.append(f"## {role} ({len(bucket)}개)")
            lines.append("")
            for record in bucket:
                lines.extend(_format_record(record))
            lines.append("")
    else:
        for record in survey.files:
            lines.extend(_format_record(record))

    return "\n".join(lines).rstrip() + "\n"


def _format_record(record: FileRecord) -> list[str]:
    out = [
        f"### `{record.path}`",
        f"- {record.lines}줄 / {record.size:,} bytes / {record.extension or '확장자 없음'}",
        f"- 기능: {record.summary}",
    ]
    if record.symbols:
        out.append(f"- 주요 심볼: {', '.join(f'`{s}`' for s in record.symbols)}")
    if record.imports:
        out.append(f"- 주요 의존: {', '.join(f'`{s}`' for s in record.imports)}")
    out.append("")
    return out


# --------------------------------------------------------------------------
# overview
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Overview:
    """What kind of project this is, without describing every file."""

    workspace: Path
    project: str
    language: str
    build_system: str
    file_count: int
    symbol_count: int
    import_count: int
    line_count: int
    covered: int
    total: int
    roles: dict[str, int]
    entrypoints: list[FileRecord]
    top_level: list[tuple[str, int]]
    largest: list[FileRecord]


def build_overview(index: ProjectIndex) -> Overview:
    """A whole-project answer sized for one screen."""
    survey = build_survey(index)
    workspace = survey.workspace
    symbols_by_file: dict[str, int] = {}
    for symbol in index.symbols:
        symbols_by_file[symbol.file] = symbols_by_file.get(symbol.file, 0) + 1

    top_level: dict[str, int] = {}
    for record in survey.files:
        head = record.path.split("/")[0]
        top_level[head] = top_level.get(head, 0) + 1

    return Overview(
        workspace=workspace,
        project=survey.project,
        language=survey.language,
        build_system=survey.build_system,
        file_count=len(survey.files),
        symbol_count=len(index.symbols),
        import_count=len(index.imports),
        line_count=sum(r.lines for r in survey.files),
        covered=survey.covered,
        total=survey.total,
        roles={role: len(bucket) for role, bucket in sorted(survey.by_role().items())},
        entrypoints=[r for r in survey.files if r.role.endswith("엔트리포인트")],
        top_level=sorted(top_level.items(), key=lambda kv: (-kv[1], kv[0])),
        largest=sorted(
            (r for r in survey.files if r.lines),
            key=lambda r: -r.lines,
        )[:5],
    )


def format_overview(overview: Overview) -> str:
    """The compact answer to "what is this project?"."""
    name = Path(overview.project).name if overview.project else overview.workspace.name
    lines = [
        f"# {name} — 프로젝트 개요",
        "",
        f"- 작업공간: `{overview.workspace}`",
        f"- 언어 / 빌드: {overview.language} / {overview.build_system}",
        f"- 규모: {overview.file_count}개 파일, {overview.line_count:,}줄, "
        f"{overview.symbol_count:,}개 심볼, {overview.import_count}개 import",
        f"- 파일 설명: **{overview.covered}/{overview.total}**개 "
        f"({'100%' if overview.covered == overview.total else '일부 판독 불가'})",
        "",
    ]

    if overview.top_level:
        top = ", ".join(
            f"`{name}`({count})" for name, count in overview.top_level[:12]
        )
        lines += [f"## 최상위 구성", "", top, ""]

    if overview.roles:
        lines += ["## 파일 역할 분포", ""]
        for role, count in sorted(
            overview.roles.items(), key=lambda kv: -kv[1]
        ):
            share = count / max(1, overview.file_count)
            bar = "█" * max(1, round(share * 20))
            lines.append(f"- {role}: {count}개 {bar}")
        lines.append("")

    if overview.entrypoints:
        lines += ["## 진입점", ""]
        for record in overview.entrypoints:
            lines.append(f"- `{record.path}` — {record.summary or '(설명 없음)'}")
        lines.append("")

    if overview.largest:
        lines += ["## 가장 큰 모듈", ""]
        for record in overview.largest:
            lines.append(
                f"- `{record.path}` ({record.lines:,}줄) — "
                f"{record.summary or '(설명 없음)'}"
            )
        lines.append("")

    lines.append(
        "파일마다 상세를 원하면 '이 프로젝트 일일히 분석해서 파일마다 기능 "
        "일일히 말해줘'처럼 요청해 주세요."
    )
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------
# targeted follow-ups
# --------------------------------------------------------------------------

#: "Tell me about this one file" / "core 모듈 설명해줘". A 48 000-character
#: dump answers "describe every file" and nothing else; these are the
#: requests that made a user ask for it twice.
_FILE_DETAIL_MARKERS = (
    "자세히", "자세하게", "상세히", "자세", "깊이", "더 알아", "알려줘", "설명해",
    "뭐 하는", "뭐해", "무엇을 하는", "어떻게 동작", "동작 방식", "원리",
    "detail", "in depth", "more about", "explain", "walk me through",
)

#: Markers that imply a *particular* thing was already being discussed.
#: "설명해줘" alone does not - it is the verb of "이 프로젝트 설명해줘" as
#: well, and redirecting that to a file detail would answer a question
#: nobody asked. These narrow on their own; the softer ones above need a
#: named target to go with them.
_NARROWING_MARKERS = (
    "자세히", "자세하게", "상세히", "자세", "깊이", "더 알아",
    "detail", "in depth", "more about", "walk me through",
)

#: Roles a user can name to narrow the survey, mapped to the phrase that
#: selects them. Checking the role name is what makes "테스트 파일만" work.
_ROLE_QUERIES = {
    "테스트": ("테스트", "test", "테스트 파일", "테스트들"),
    "벤치마크": ("벤치마크", "benchmark", "벤치"),
    "CLI 엔트리포인트": ("엔트리포인트", "진입점", "entrypoint", "entry point"),
    "문서": ("문서", "document", "readme", "md 파일"),
    "프로젝트 설정": ("설정 파일", "config", "설정파일"),
}

#: Directory names a user can name to narrow by path prefix.
_DIR_HINTS = (
    "core", "agents", "indexer", "skills", "tui", "verify", "context",
    "semantic", "patch", "tests", "benchmarks", "ruder_ai",
)


def wants_repeat(prompt: str) -> bool:
    """The user is explicitly asking for the full survey again."""
    text = _normalize(prompt)
    return _wants(text, ("다시", "again", "한번 더", "전체 출력", "전부 출력"))


def file_hint(prompt: str) -> str | None:
    """A path or bare filename the user singled out, if any."""
    raw = str(prompt or "")
    # A path, an extension, or a dotted name is unambiguous. The class needs
    # an escaped backslash so a Windows-style path survives intact.
    for match in re.finditer(r"[\w./\\-]+\.[A-Za-z0-9]{1,6}\b", raw):
        return match.group(0).replace("\\", "/")
    return None


def role_hint(prompt: str) -> str | None:
    """The role the user asked about, if they named one."""
    text = _normalize(prompt)
    for role, phrases in _ROLE_QUERIES.items():
        if _wants(text, phrases):
            return role
    return None


def dir_hint(prompt: str) -> str | None:
    """A top-level directory the user named, if any."""
    text = _normalize(prompt)
    for name in _DIR_HINTS:
        if re.search(rf"(?<![가-힣a-z0-9_]){re.escape(name)}(?![가-힣a-z0-9_])", text):
            return name
    return None


def is_targeted_question(prompt: str) -> bool:
    """True when a request is about a named subset, not the whole project.

    A named file, role or directory is a stronger signal than a generic
    "what is this project?", so it outranks the overview. Asking for a change
    outranks both - "auth.py 고쳐줘" is work, not a question.
    """
    text = _normalize(prompt)
    if _wants(text, _EDIT_MARKERS):
        return False
    if file_hint(prompt) or role_hint(prompt) or dir_hint(prompt):
        return True
    return _wants(text, _NARROWING_MARKERS)


def build_detail(
    index: ProjectIndex,
    prompt: str,
    *,
    max_files: int = 40,
) -> tuple[str, FileRecord | None] | None:
    """The subset of the survey a narrowed request is asking for.

    Returns ``(report, exact_match)``. ``exact_match`` is non-None when the
    request named one specific existing file, which is the case worth
    answering in full detail rather than by role.
    """
    survey = build_survey(index)
    by_path = {record.path: record for record in survey.files}

    wanted = file_hint(prompt)
    if wanted:
        target = _resolve_relative(wanted, by_path)
        if target is not None:
            record = by_path[target]
            return _detail_for(record, survey), record
        return _not_found(survey, index), None

    role = role_hint(prompt)
    if role:
        bucket = [r for r in survey.files if r.role == role]
        return _detail_listing(role, bucket, survey, max_files), None

    directory = dir_hint(prompt)
    if directory:
        prefix = directory.rstrip("/") + "/"
        bucket = [r for r in survey.files if r.path.startswith(prefix)]
        if bucket:
            return _detail_listing(f"{directory}/ 디렉터리", bucket, survey, max_files), None
        return _not_found(survey, index), None

    return None


def _not_found(survey: Survey, index: ProjectIndex) -> str:
    """A named target that does not exist gets the shape, not another dump."""
    return (
        "지정하신 대상에 해당하는 파일을 찾지 못했습니다. "
        "전체 구성은 아래 개요에서 볼 수 있습니다.\n\n"
        + format_overview(build_overview(index))
    )


def _resolve_relative(wanted: str, by_path: dict) -> str | None:
    """Match a user-typed path against indexed paths, by suffix or basename."""
    cleaned = wanted.lstrip("./")
    if cleaned in by_path:
        return cleaned
    matches = [p for p in by_path if p.endswith("/" + cleaned)]
    if len(matches) == 1:
        return matches[0]
    base = cleaned.rsplit("/", 1)[-1]
    matches = [p for p in by_path if p.rsplit("/", 1)[-1] == base]
    return matches[0] if len(matches) == 1 else None


def _detail_for(record: FileRecord, survey: Survey) -> str:
    # _format_record already emits the "### `path`" heading; do not repeat it.
    body = _format_record(record)
    return f"[전체 {survey.covered}/{survey.total}개 중 1개]\n\n" + "\n".join(
        body
    ).rstrip() + "\n"


def _detail_listing(title: str, bucket, survey: Survey, max_files: int) -> str:
    covered = len(bucket)
    header = (
        f"# {title} — {covered}개 파일 "
        f"({survey.covered}/{survey.total} 전체 중)"
    )
    if covered == 0:
        return header + "\n\n(해당하는 파일이 없습니다.)\n"
    lines = [header, ""]
    for record in bucket[:max_files]:
        lines.append(
            f"- `{record.path}` ({record.lines:,}줄) — "
            f"{record.summary or '(설명 없음)'}"
        )
    if covered > max_files:
        lines.append("")
        lines.append(f"…외 {covered - max_files}개. 이름을 지정하면 자세히 보여드립니다.")
    return "\n".join(lines) + "\n"
