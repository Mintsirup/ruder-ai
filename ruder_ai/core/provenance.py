"""Which checkout is actually running?

A whole work session was lost to this: edits went into one RUDER-AI checkout
while an editable install made the agent import a *different* copy, so every
change appeared to do nothing. Nothing warned about it - the agent ran fine,
it just ran the wrong code.

Two cheap signals catch it at startup:

* Where ``ruder_ai`` is actually imported from (the editable install's target).
* Whether that checkout is the same one the user is standing in.

The recorded origin also turns "which version am I running?" from a guess into
a lookup, and gives the agent a factual answer when asked about its own
identity.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

#: Written next to the package so a running install can name itself.
ORIGIN_FILENAME = ".ruder_ai_origin.json"

#: Per-user cache of "this checkout was healthy last time we looked".
PROVENANCE_CACHE = Path.home() / ".ruder_ai" / "provenance.json"


@dataclass(frozen=True, slots=True)
class Provenance:
    #: The directory ``ruder_ai`` was imported from.
    import_root: str
    #: The checkout that contains it (parent of the package directory).
    checkout: str
    #: The directory the user invoked the command from, when known.
    cwd: str
    #: The workspace the agent is configured to modify.
    workspace: str
    version: str
    python: str

    @property
    def edited_and_running_match(self) -> bool:
        return _same_path(self.checkout, self.cwd)

    def describe(self) -> str:
        lines = [
            f"RUDER-AI 실행 위치 : {self.checkout}",
            f"import 경로       : {self.import_root}",
            f"현재 작업 디렉터리 : {self.cwd}",
            f"대상 workspace    : {self.workspace}",
            f"버전              : {self.version}",
        ]
        if not self.edited_and_running_match:
            lines.append("")
            lines.append(
                "⚠️  경고: 실행 중인 코드와 작업 디렉터리가 서로 다른 "
                "복사본입니다. 이 디렉터리의 수정이 반영되지 않을 수 "
                "있습니다. (pip install -e . 가 가리키는 곳을 확인하세요.)"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)


def _same_path(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return os.path.normcase(a) == os.path.normcase(b)


def _read_version() -> str:
    """The version a user would quote.

    ``ruder_ai.__version__`` is the packaging version (0.1.0) and
    ``AIAgent.__version__`` is a different string again; the ``VERSION`` file
    at the checkout root is what the README and the Modelfiles refer to, so it
    wins when present.
    """
    try:
        import ruder_ai

        package_dir = Path(ruder_ai.__file__).resolve().parent
    except Exception:  # pragma: no cover
        return "unknown"

    for candidate in (
        package_dir.parent / "VERSION",
        package_dir / "VERSION",
    ):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            continue

    return str(getattr(ruder_ai, "__version__", "unknown"))


def detect(workspace: str | Path | None = None) -> Provenance:
    """Work out where this process is really running from."""
    try:
        import ruder_ai

        module_file = getattr(ruder_ai, "__file__", None)
    except Exception:  # pragma: no cover - ruder_ai always imports
        module_file = None

    if module_file:
        import_root = str(Path(module_file).resolve().parent)
        # .../<checkout>/ruder_ai -> .../<checkout>
        checkout = str(Path(import_root).parent)
    else:  # pragma: no cover
        import_root = "<unknown>"
        checkout = "<unknown>"

    return Provenance(
        import_root=import_root,
        checkout=checkout,
        cwd=str(Path.cwd()),
        workspace=str(Path(workspace).resolve()) if workspace else "",
        version=_read_version(),
        python=sys.executable,
    )


# --------------------------------------------------------------------------
# divergence detection
# --------------------------------------------------------------------------

#: Files whose content identifies a checkout. A mismatch on any of them means
#: the two trees are different code, not just different scratch files.
_FINGERPRINT_FILES = (
    "VERSION",
    "ruder_ai/main.py",
    "ruder_ai/core/executor.py",
    "ruder_ai/core/agent.py",
    "ruder_ai/indexer/scanner.py",
    "ruder_ai/tui/app_gui.py",
)

#: Never compared: local state, caches and generated files.
_IGNORED_DIRS = {
    "__pycache__", ".venv", "venv", ".git", "build", "dist", "node_modules",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
}


def fingerprint(checkout: str | Path) -> dict[str, str]:
    """Cheap content hash of the files that identify a checkout."""
    import hashlib

    root = Path(checkout)
    digests: dict[str, str] = {}
    for relative in _FINGERPRINT_FILES:
        path = root / relative
        if not path.is_file():
            continue
        try:
            digests[relative] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()[:16]
        except OSError:
            continue
    return digests


def _load_cache() -> dict:
    try:
        return json.loads(PROVENANCE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _store_cache(data: dict) -> None:
    try:
        PROVENANCE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PROVENANCE_CACHE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def detect_divergence() -> list[str]:
    """Compare this checkout against every other one seen on this machine.

    Returns human-readable warnings; empty when everything agrees. Only
    reports a divergence when two checkouts disagree on a fingerprint file, so
    a stale-but-untouched copy never produces noise.
    """
    here = detect()
    if not here.checkout or here.checkout == "<unknown>":
        return []

    warnings: list[str] = []
    mine = fingerprint(here.checkout)
    if not mine:
        return warnings

    cache = _load_cache()
    known = cache.setdefault("checkouts", {})

    for other, record in list(known.items()):
        if _same_path(other, here.checkout):
            known[other] = {"fingerprint": mine, "version": here.version}
            continue
        if not Path(other).is_dir():
            del known[other]
            continue
        theirs = record.get("fingerprint") or {}
        differing = sorted(
            name
            for name in set(mine) & set(theirs)
            if mine[name] != theirs[name]
        )
        if not differing:
            continue
        if _same_path(here.cwd, other):
            warnings.append(
                f"현재 작업 디렉터리({here.cwd})는 실행 중인 코드와 다릅니다. "
                f"이 디렉터리에 편집해도 반영되지 않습니다.\n"
                f"   실행 중: {here.checkout}\n"
                f"   다른 코드와 다른 파일: {', '.join(differing)}"
            )
        else:
            warnings.append(
                f"다른 RUDER-AI 복사본의 코드가 다릅니다: {other}\n"
                f"   다른 파일: {', '.join(differing)}"
            )

    known[here.checkout] = {"fingerprint": mine, "version": here.version}
    _store_cache(cache)
    return warnings


def write_origin(workspace: str | Path | None = None) -> None:
    """Record this checkout inside its own tree, for cross-run comparison."""
    try:
        info = detect(workspace)
        target = Path(info.checkout)
        if not target.is_dir() or target == Path(target.anchor):
            return
        (target / ORIGIN_FILENAME).write_text(
            json.dumps(info.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
