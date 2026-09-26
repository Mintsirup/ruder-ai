"""Source-only sync between a RUDER-AI clone and a test copy.

Copies tracked source files and nothing else. Never deletes a destination
file, so anything that exists only in the test copy (agent-written scratch
files, benchmark results, local state) survives untouched.

Local state that must not be disturbed:
    .venv/  .ruder_ai_config.json  .ruder_ai_memory.json  .ruder_ai_logs/
    *.egg-info/  __pycache__/  .git/  build/

Usage:
    python sync.py <src> <dst>          # report + copy
    python sync.py <src> <dst> --dry    # report only
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

#: Top-level entries that belong to the clone, not the source tree.
SKIP_DIRS = {
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "build",
    "dist",
}

#: Never delete these, even if they only exist in the destination.
PRESERVE = {
    ".ruder_ai_config.json",
    ".ruder_ai_memory.json",
    ".gitignore",
    "VERSION",
}

#: File suffixes considered source.
SOURCE_SUFFIXES = {".py", ".md", ".toml", ".ini", ".cfg", ".json", ".txt"}


def _skip_dir(name: str) -> bool:
    return (
        name in SKIP_DIRS
        or name.endswith(".egg-info")
        or (name.startswith(".") and name not in {".github"})
    )


def collect(src: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(_skip_dir(part) for part in rel.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in SOURCE_SUFFIXES or path.name in PRESERVE:
            files.append(rel)
    return files


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]).resolve(), Path(argv[2]).resolve()
    dry = "--dry" in argv[3:]

    if not (src / "ruder_ai" / "__init__.py").is_file():
        print(f"error: {src} does not look like a RUDER-AI checkout", file=sys.stderr)
        return 2

    wanted = collect(src)
    added, updated, same = [], [], []
    for rel in wanted:
        target = dst / rel
        if not target.exists():
            added.append(rel)
        elif target.read_bytes() != (src / rel).read_bytes():
            updated.append(rel)
        else:
            same.append(rel)

    print(f"source files : {len(wanted)}")
    print(f"  new        : {len(added)}")
    print(f"  changed    : {len(updated)}")
    print(f"  identical  : {len(same)}")
    if added:
        print("\nnew:")
        for rel in added:
            print(f"  + {rel.as_posix()}")
    if updated:
        print("\nchanged:")
        for rel in updated:
            print(f"  M {rel.as_posix()}")

    # Destination-only source files are reported, never removed.
    for rel in collect(dst):
        if not (src / rel).exists():
            print(f"  kept (dst only): {rel.as_posix()}")

    if dry:
        print("\n-- dry run, nothing written --")
        return 0

    for rel in added + updated:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)

    # Stale bytecode for a module whose source changed shape can only cause
    # confusion; Python invalidates on mtime, but a clean slate is cheaper
    # than reasoning about it.
    for cache in dst.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)

    print(f"\ncopied {len(added) + len(updated)} file(s); cleared __pycache__")
    print("state left alone: .venv, *.egg-info, .ruder_ai_*, *.dst-only files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
