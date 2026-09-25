"""Java source parser for RuderAI (Enhanced Version)."""

from __future__ import annotations

import re
from pathlib import Path

from .models import FileInfo, Symbol


PACKAGE_PATTERN = re.compile(
    r"^\s*package\s+([\w\.]+)\s*;",
    re.MULTILINE,
)

# 어노테이션 매칭 (괄호 및 내부 인자 포함 지원)
ANNOTATION_PATTERN = re.compile(
    r"@([A-Za-z_][A-Za-z0-9_]*)(?:\s*\((?:[^()]*|\((?:[^()]*|\([^()]*\))*\))*\))?"
)

# Bukkit/Spigot/Paper 상수
LISTENER_INTERFACE = "Listener"
EVENT_HANDLER_ANNOTATION = "EventHandler"

CLASS_PATTERN = re.compile(
    r"""
    ^
    \s*
    (?:
        (?:
            public|private|protected|abstract|final|
            sealed|non-sealed|static|strictfp
        )
        \s+
    )*
    (?P<kind>class|interface|enum|record)
    \s+
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    (?:\s*<[^>{]*>)?
    (?:\s+extends\s+(?P<extends>[A-Za-z_][A-Za-z0-9_.<>,\s]*?))?
    (?:\s+implements\s+(?P<implements>[A-Za-z_][A-Za-z0-9_.<>,\s]*?))?
    \s*[{]
    """,
    re.MULTILINE | re.VERBOSE,
)

FIELD_PATTERN = re.compile(
    r"""
    ^
    \s*
    (?:
        (?:
            public|private|protected|static|final|
            transient|volatile
        )
        \s+
    )*
    (?P<type>[A-Za-z0-9_<>\[\]\.?\s]+)
    \s+
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    \s*
    (?:=.*?)?;
    """,
    re.MULTILINE | re.VERBOSE,
)

METHOD_PATTERN = re.compile(
    r"""
    ^
    \s*
    (?:
        (?:
            public|private|protected|static|final|
            synchronized|native|abstract|default
        )
        \s+
    )*
    (?P<type>[A-Za-z0-9_<>\[\]\.?\s]+)
    \s+
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    \s*
    \(
    (?P<params>[^)]*)
    \)
    """,
    re.MULTILINE | re.VERBOSE,
)


class JavaParser:

    def parse(self, file: FileInfo) -> list[Symbol]:
        path = Path(file.path)

        try:
            raw_text = path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            return []

        # 1. 주석 및 문자열 마스킹 (파싱 오탐 방지)
        cleaned_text = self._clean_code(raw_text)

        symbols: list[Symbol] = []
        package = self._parse_package(cleaned_text)

        # 2. 클래스 파싱
        classes = self._parse_classes(
            cleaned_text,
            file.relative_path,
            package,
        )
        symbols.extend(classes)

        # 가장 대표적인 클래스 상위 컨텍스트 설정 (Class.Member 형태)
        main_class = classes[0].name if classes else None
        class_container = f"{package}.{main_class}" if package and main_class else (main_class or package)

        # 3. 필드 파싱
        symbols.extend(
            self._parse_fields(
                cleaned_text,
                file.relative_path,
                class_container,
            )
        )

        # 4. 메서드 파싱
        symbols.extend(
            self._parse_methods(
                cleaned_text,
                file.relative_path,
                class_container,
            )
        )

        return symbols

    def _clean_code(self, text: str) -> str:
        """
        줄 번호(Line Number) 인덱스를 유지하면서 주석(//, /* */) 및
        문자열 리터럴("...")을 공백으로 마스킹합니다.
        """
        def replace_keep_newlines(match: re.Match) -> str:
            s = match.group(0)
            return "".join("\n" if c == "\n" else " " for c in s)

        # 주석 및 문자열 정규식 (블록주석, 한줄주석, 텍스트블록, 일반문자열)
        pattern = r"(/\*[\s\S]*?\*/)|(//.*$)|(\"\"\"[\s\S]*?\"\"\")|(\"(?:\\.|[^\"\\])*\")"
        return re.sub(pattern, replace_keep_newlines, text, flags=re.MULTILINE)

    def _parse_package(self, text: str) -> str | None:
        m = PACKAGE_PATTERN.search(text)
        return m.group(1) if m else None

    def _line(self, text: str, index: int) -> int:
        return text.count("\n", 0, index) + 1

    def _split_types(self, raw: str | None) -> list[str]:
        """중첩 제네릭을 고려하여 인터페이스/클래스 이름을 안전하게 분리합니다."""
        if not raw:
            return []

        # 중첩 제네릭 제거 (예: Map<K, V> -> Map)
        cleaned = ""
        depth = 0
        for char in raw:
            if char == "<":
                depth += 1
            elif char == ">":
                depth -= 1
            elif depth == 0:
                cleaned += char

        return [part.strip() for part in cleaned.split(",") if part.strip()]

    def _annotations_before(self, text: str, index: int) -> list[str]:
        """선언 직전의 @Annotation 이름을 파라미터 제외 후 반환합니다."""
        prefix = text[:index]
        lines = prefix.split("\n")

        annotations: list[str] = []

        for line in reversed(lines):
            stripped = line.strip()

            if not stripped:
                continue

            matches = list(ANNOTATION_PATTERN.finditer(stripped))
            if matches:
                for m in reversed(matches):
                    annotations.append(m.group(1))
                continue

            # 어노테이션 선언 라인이 아니면 종료
            break

        annotations.reverse()
        return annotations

    def _parse_classes(self, text: str, file: str, package: str | None) -> list[Symbol]:
        result = []
        for m in CLASS_PATTERN.finditer(text):
            kind = m.group("kind")
            name = m.group("name")
            extends_raw = m.group("extends")
            implements_raw = m.group("implements")

            extends_list = self._split_types(extends_raw)
            implements_list = self._split_types(implements_raw)
            extends_name = extends_list[0] if extends_list else None

            result.append(
                Symbol(
                    name=name,
                    kind=kind,
                    file=file,
                    line=self._line(text, m.start()),
                    container=package,
                    extends=extends_name,
                    implements=implements_list,
                    is_listener=(LISTENER_INTERFACE in implements_list),
                )
            )
        return result

    def _parse_fields(self, text: str, file: str, container: str | None) -> list[Symbol]:
        result = []
        for m in FIELD_PATTERN.finditer(text):
            datatype = m.group("type").strip()
            name = m.group("name")

            # 자바 키워드 예외 처리
            if name in {"class", "return", "new", "throw"}:
                continue

            result.append(
                Symbol(
                    name=name,
                    kind="field",
                    datatype=datatype,
                    file=file,
                    line=self._line(text, m.start()),
                    container=container,
                )
            )
        return result

    def _parse_methods(self, text: str, file: str, container: str | None) -> list[Symbol]:
        result = []
        keywords = {"if", "for", "while", "switch", "catch", "return", "new", "else", "try"}

        for m in METHOD_PATTERN.finditer(text):
            datatype = m.group("type").strip()
            name = m.group("name")

            if name in keywords:
                continue

            annotations = self._annotations_before(text, m.start())
            is_listener = EVENT_HANDLER_ANNOTATION in annotations

            result.append(
                Symbol(
                    name=name,
                    kind="listener_method" if is_listener else "method",
                    datatype=datatype,
                    file=file,
                    line=self._line(text, m.start()),
                    container=container,
                    annotations=annotations,
                    is_listener=is_listener,
                )
            )
        return result
