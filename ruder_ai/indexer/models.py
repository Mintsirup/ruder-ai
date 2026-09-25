"""Project Index Models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# -------------------------
# Scanner
# -------------------------

@dataclass(slots=True)
class FileInfo:

    relative_path: str
    absolute_path: Path
    extension: str
    size: int

    semantic_tokens: set[str] = field(
        default_factory=set
    )

    @property
    def path(self) -> Path:
        """python_parser/java_parser 등에서 쓰는 별칭.

        실제 파일을 여는 용도로는 절대경로가 필요하므로
        absolute_path 를 그대로 반환한다.
        """

        return self.absolute_path


# -------------------------
# Parser
# -------------------------

@dataclass(slots=True)
class Symbol:

    name: str
    kind: str
    file: str
    line: int
    package: str = ""

    # 클래스/메서드가 속한 상위 컨테이너 (예: 클래스 안의 메서드 -> 클래스명)
    container: str | None = None

    # 필드/메서드의 타입 또는 반환 타입
    datatype: str | None = None

    # 클래스가 extends 하는 상위 클래스 (자바 전용)
    extends: str | None = None

    # 클래스가 implements 하는 인터페이스 목록 (자바 전용)
    implements: list[str] = field(
        default_factory=list
    )

    # 메서드에 붙은 어노테이션 목록 (예: @EventHandler, @Override)
    annotations: list[str] = field(
        default_factory=list
    )

    # Bukkit/Spigot 이벤트 리스너 메서드 여부
    is_listener: bool = False

    semantic_tokens: set[str] = field(
        default_factory=set
    )


# -------------------------
# Detector
# -------------------------

@dataclass(slots=True)
class ProjectInfo:
    """프로젝트 정보."""

    root: Path

    language: str

    build_system: str

    framework: str | None = None

    java_version: str | None = None


# -------------------------
# Index
# -------------------------

@dataclass(slots=True)
class ProjectIndex:
    """프로젝트 전체 인덱스."""

    workspace: Path

    project: ProjectInfo

    files: list[FileInfo] = field(
        default_factory=list
    )

    symbols: list[Symbol] = field(
        default_factory=list
    )

    types: dict[str, str] = field(default_factory=dict)

    imports: dict[str, str] = field(default_factory=dict)

    # -------------------------
    # Inverted Index
    # -------------------------

    inverted_index: dict[
        str,
        set[str],
    ] = field(
        default_factory=dict
    )

    def search(
        self,
        keyword: str,
    ) -> list[str]:
        """
        역색인 검색
        """

        return sorted(
            self.inverted_index.get(
                keyword.lower(),
                set(),
            )
        )
