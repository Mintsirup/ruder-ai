"""Conservative static checks for C# source when a real project compiler is unavailable."""
from __future__ import annotations

import re
from pathlib import Path

from .models import CheckResult
from ruder_ai.core.file_resolver import FileResolver


_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_FIELD = re.compile(
    r"(?:public|private|protected|internal|static|readonly|const|volatile|sealed|new|virtual|override|\s)+"
    r"(?:[A-Za-z_][A-Za-z0-9_<>,.?\[\]]*\s+)+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;)")
_LOCAL = re.compile(
    r"(?:^|[;{}])\s*(?:const\s+)?(?:[A-Za-z_][A-Za-z0-9_<>,.?\[\]]*\s+)"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;)", re.MULTILINE)
_PARAM = re.compile(r"\((?P<body>[^)]*)\)")

_KEYWORDS = {
    "if", "else", "for", "foreach", "while", "do", "switch", "case", "default",
    "return", "new", "true", "false", "null", "this", "base", "typeof", "sizeof",
    "nameof", "in", "out", "ref", "is", "as", "var", "void", "bool", "byte", "char",
    "decimal", "double", "float", "int", "long", "object", "sbyte", "short", "string",
    "uint", "ulong", "ushort", "dynamic", "async", "await", "get", "set", "add", "remove",
    "yield", "try", "catch", "finally", "throw", "lock", "using", "namespace", "class",
    "struct", "interface", "enum", "public", "private", "protected", "internal", "static",
    "readonly", "const", "sealed", "partial", "abstract", "virtual", "override", "extern",
}


def _declared_names(text: str) -> set[str]:
    names = set(m.group("name") for m in _FIELD.finditer(text))
    names.update(m.group("name") for m in _LOCAL.finditer(text))
    for match in _PARAM.finditer(text):
        for piece in match.group("body").split(","):
            piece = piece.strip()
            if not piece:
                continue
            tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", piece)
            if len(tokens) >= 2:
                names.add(tokens[-1])
    # Common Unity members inherited from MonoBehaviour / Component.
    names.update({"transform", "gameObject", "enabled", "name", "tag"})
    return names


def _likely_missing_identifiers(text: str, declared: set[str]) -> list[str]:
    # Conservative: collect identifiers from expressions where an undeclared
    # local/field is plausible. Avoid treating every type/API name as an error.
    suspects: set[str] = set()
    patterns = (
        r"=\s*(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:[;,)\]}]|$)",
        r"(?<![.\w])([A-Za-z_][A-Za-z0-9_]*)\s*(?:\+\+|--)",
        r"(?:return|\()\s*(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)\s*[;,)]",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            name = match.group(1)
            if name in declared or name in _KEYWORDS:
                continue
            suspects.add(name)

    likely: list[str] = []
    for name in sorted(suspects):
        low = name.lower()
        if low.endswith((
            "force", "speed", "velocity", "distance", "amount", "count",
            "value", "health", "score", "timer",
        )):
            likely.append(name)
    return likely[:20]


async def run_csharp_static_check(
    workspace: Path,
    timeout: int = 30,
    target_files: list[str] | None = None,
) -> CheckResult:
    workspace = Path(workspace).resolve()
    resolver = FileResolver(workspace)
    if target_files:
        files = []
        for rel in target_files:
            normalized = str(rel).replace("\\", "/")
            resolved = resolver.resolve(normalized)
            if not resolved or Path(resolved).suffix.lower() != ".cs":
                continue
            path = (workspace / resolved).resolve()
            if path.is_file() and workspace in path.parents:
                files.append(path)

        if not files:
            return CheckResult(
                tool="csharp-static",
                command="static-csharp-check",
                passed=False,
                returncode=2,
                stderr=f"대상 C# 파일을 찾을 수 없습니다: {', '.join(map(str, target_files))}",
            )
    else:
        # 주의: p.parts(절대 경로)로 필터링하면 Windows의 사용자 temp
        # 디렉터리(..\Local\Temp\..) 아래 전부가 걸려 어떤 파일도 검사
        # 대상이 되지 않는다. workspace 기준 상대 parts로 판단한다.
        files = [
            p for p in workspace.rglob("*.cs")
            if not any(
                part in {".git", "Library", "Temp"}
                for part in p.relative_to(workspace).parts[:-1]
            )
        ]
        if not files:
            return CheckResult(
                tool="csharp-static",
                command="static-csharp-check",
                passed=False,
                skipped=True,
                skip_reason="검사할 C# 소스 파일이 없습니다.",
            )

    issues: list[str] = []
    for path in files[:200]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        declared = _declared_names(text)
        missing = _likely_missing_identifiers(text, declared)
        # Only emit a deterministic issue when a missing identifier has a
        # close declared sibling (e.g. jumpForce vs superJumpForce).
        for name in missing:
            siblings = [d for d in declared if d.lower().endswith(name.lower()) or name.lower().endswith(d.lower())]
            if siblings:
                issues.append(
                    f"{path.relative_to(workspace)}: '{name}'가 선언되지 않았습니다. "
                    f"유사 선언: {', '.join(sorted(siblings)[:3])}"
                )

    if issues:
        return CheckResult(
            tool="csharp-static",
            command="static-csharp-check",
            passed=False,
            returncode=1,
            stderr="\n".join(issues),
        )

    return CheckResult(
        tool="csharp-static",
        command="static-csharp-check",
        passed=True,
        returncode=0,
        stdout=f"C# 정적 검사 통과 ({len(files)}개 파일 검사)" + (" [대상 파일 한정]" if target_files else ""),
    )
