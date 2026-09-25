"""Project detector."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .models import FileInfo, ProjectInfo


LANGUAGE_MAP = {
    ".java": "Java",
    ".kt": "Kotlin",
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".c": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".json": "JSON",
    ".toml": "TOML",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".xml": "XML",
    ".md": "Markdown",
}


class ProjectDetector:
    """
    파일 목록을 보고 프로젝트 정보를 추론한다.

    현재 models.py 기준으로 반드시 채워야 하는 값:
    - root
    - language
    - build_system

    선택값:
    - framework
    - java_version
    """

    def detect(
        self,
        root: str | Path,
        files: list[FileInfo],
    ) -> ProjectInfo:
        root_path = Path(root).resolve()

        names = {
            f.relative_path.split("/")[-1]
            for f in files
        }
        relative_paths = {str(f.relative_path).replace("\\", "/").lstrip("./") for f in files}

        # -----------------------
        # Build System
        # -----------------------

        build_system = "Unknown"

        if "build.gradle" in names or "build.gradle.kts" in names:
            build_system = "Gradle"
        elif "pom.xml" in names:
            build_system = "Maven"
        elif "package.json" in names:
            build_system = "NPM"
        elif "Cargo.toml" in names:
            build_system = "Cargo"
        elif "pyproject.toml" in names or "requirements.txt" in names or "setup.py" in names:
            build_system = "Python"
        elif "go.mod" in names:
            build_system = "Go"
        elif "makefile" in {n.lower() for n in names}:
            build_system = "Make"

        # -----------------------
        # Language
        # -----------------------

        counter = Counter()

        for file in files:
            language = LANGUAGE_MAP.get(file.extension.lower())
            if language:
                counter[language] += 1

        language = "Unknown"
        if counter:
            language = counter.most_common(1)[0][0]

        # build_system에 따라 보정
        if build_system == "Python":
            language = "Python"
        elif build_system == "Cargo":
            language = "Rust"
        elif build_system == "NPM" and language == "Unknown":
            language = "JavaScript"

        # -----------------------
        # Framework
        # -----------------------

        framework = None

        if "plugin.yml" in names:
            framework = "PaperMC"
            if language == "Unknown":
                language = "Java"
        elif "fabric.mod.json" in names:
            framework = "Fabric"
            if language == "Unknown":
                language = "Java"
        elif "mods.toml" in names:
            framework = "Minecraft Forge"
            if language == "Unknown":
                language = "Java"
        elif "bun.lockb" in names or "bun.lock" in names:
            framework = "Bun"
            if language == "Unknown":
                language = "TypeScript"
        elif language == "C#" and (
            "Assets" in names
            or "ProjectSettings" in names
            or any(path.startswith("Assets/") for path in relative_paths)
            or any(path.startswith("ProjectSettings/") for path in relative_paths)
        ):
            framework = "Unity"

        # -----------------------
        # Java version (best-effort)
        # -----------------------

        java_version = None
        if language == "Java":
            if "gradle.properties" in names:
                java_version = "Unknown"
            elif "pom.xml" in names:
                java_version = "Unknown"

        return ProjectInfo(
            root=root_path,
            language=language,
            build_system=build_system,
            framework=framework,
            java_version=java_version,
        )
