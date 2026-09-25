"""Project exploration agent with deterministic evidence collection."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .base import AgentRole, BaseRoleAgent


@dataclass(slots=True)
class EvidenceFact:
    kind: str
    subject: str
    exists: bool
    files: list[str] = field(default_factory=list)
    details: str = ""


@dataclass(slots=True)
class ExplorerEvidence:
    """Deterministic facts collected before LLM planning."""

    files: list[str] = field(default_factory=list)
    facts: list[EvidenceFact] = field(default_factory=list)
    snapshots: dict[str, str] = field(default_factory=dict)
    relations: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        lines = ["# Explorer 사실 근거", f"파일 후보: {len(self.files)}개"]
        for fact in self.facts:
            state = "존재" if fact.exists else "없음"
            suffix = f" — {fact.details}" if fact.details else ""
            where = f" [{', '.join(fact.files)}]" if fact.files else ""
            lines.append(f"- {fact.kind}: {fact.subject} = {state}{where}{suffix}")
        if self.relations:
            lines.append("# Explorer 관계 근거")
            lines.extend(f"- {item}" for item in self.relations)
        return "\n".join(lines)


class ExplorerAgent(BaseRoleAgent):
    role = AgentRole("Explorer", "프로젝트 구조·파일·심볼·참조 관계를 사실 기반으로 분석", False, False)

    def build_context(self, project_index, planner, context_builder, prompt: str):
        context_plan = planner.plan(prompt=prompt, index=project_index)
        context = context_builder.build(context_plan, project_index)
        return context_plan, context

    def collect_evidence(self, project_index, prompt: str, context_plan=None) -> ExplorerEvidence:
        """Read-only deterministic evidence used by later agents.

        The Explorer does not ask the LLM whether a symbol/text exists; it
        derives that fact directly from the index and source files.
        """
        files = []
        for item in (getattr(context_plan, "files", None) or []):
            if item not in files:
                files.append(item)

        # Never rely solely on semantic ranking for explicitly named files.
        # Resolve basenames directly against the current filesystem so a stale
        # or incomplete index cannot turn an existing file into a false "missing".
        for requested in self._mentioned_files(prompt):
            resolved = self.file_resolver.resolve(requested)
            if resolved and resolved not in files:
                files.append(resolved)

        if not files:
            files = [f.relative_path for f in getattr(project_index, "files", [])[:8]]

        evidence = ExplorerEvidence(files=files)

        # 파일 존재/내용 여부에 대한 결정론적 사실을 명시적으로 기록한다.
        # `list_directory`/파일시스템으로 존재가 확인된 파일을, 이후 단계의
        # LLM이 "내용이 없다"거나 "존재하지 않는다"고 임의로 재해석하는
        # 사고가 실제로 있었다 — 인덱스가 오래됐거나 파일 크기가 작을 때
        # 특히 그렇다. Tool 결과값(파일시스템)을 기준으로 존재 여부를
        # 여기서 고정해두면, 프롬프트에 이 사실이 명시적으로 들어가
        # LLM이 그 판단을 뒤집을 여지를 줄인다.
        for rel in files[:12]:
            path = Path(project_index.workspace) / rel
            exists = path.is_file()
            if not exists:
                evidence.facts.append(
                    EvidenceFact(
                        kind="file_existence",
                        subject=rel,
                        exists=False,
                        details="파일시스템에 실제로 존재하지 않음 (list_directory/인덱스 결과가 오래되었을 수 있음)",
                    )
                )
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = None
            if size == 0:
                detail = "파일은 존재하지만 내용이 0바이트(진짜 빈 파일)"
            elif size is not None:
                detail = f"파일 존재 확인됨 ({size}바이트) — 내용이 없다고 판단하려면 실제로 읽은 스냅샷을 근거로 해야 함"
            else:
                detail = "파일 존재 확인됨 (크기 조회 실패)"
            evidence.facts.append(
                EvidenceFact(
                    kind="file_existence",
                    subject=rel,
                    exists=True,
                    details=detail,
                )
            )

        # Preserve pre-mutation source snapshots. Reviewer must compare
        # before/after state; otherwise a correctly fixed error disappears
        # from the current file and gets misclassified as a hallucinated
        # error.
        for rel in files[:12]:
            path = Path(project_index.workspace) / rel
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(text) > 16000:
                text = text[:8000] + "\n...\n" + text[-8000:]
            evidence.snapshots[rel] = text

        # Deterministic relationship facts for explicitly named files.
        # Distinguish direct code references from shared runtime state.
        if len(files) >= 2:
            named = {Path(rel).name.lower(): rel for rel in files}
            rel_paths = list(files)[:8]
            for i, left in enumerate(rel_paths):
                for right in rel_paths[i + 1:]:
                    ltext = evidence.snapshots.get(left, "")
                    rtext = evidence.snapshots.get(right, "")
                    lclass = re.search(r"class\s+(\w+)", ltext)
                    rclass = re.search(r"class\s+(\w+)", rtext)
                    if lclass and rclass:
                        lc, rc = lclass.group(1), rclass.group(1)
                        direct = bool(re.search(rf"\b{re.escape(rc)}\b", ltext)) or bool(re.search(rf"\b{re.escape(lc)}\b", rtext))
                        if direct:
                            evidence.relations.append(f"직접 코드 참조: {left} ↔ {right}")
                    # Common Unity runtime relation: one script writes its
                    # own Transform and the other reads a Transform target.
                    def writes_transform(text: str) -> bool:
                        return bool(re.search(r"\btransform\.position\s*=|transform\.position\s*\+=|transform\.Translate\s*\(", text))
                    def reads_target_transform(text: str) -> bool:
                        return bool(re.search(r"\btarget\.position\b|\btarget\.rotation\b", text))
                    if writes_transform(ltext) and reads_target_transform(rtext):
                        evidence.relations.append(f"런타임 Transform 의존: {right}가 {left}의 Transform 위치를 읽을 수 있음")
                    if writes_transform(rtext) and reads_target_transform(ltext):
                        evidence.relations.append(f"런타임 Transform 의존: {left}가 {right}의 Transform 위치를 읽을 수 있음")

        candidates = self._mentioned_identifiers(prompt)
        symbol_by_name: dict[str, list] = {}
        for symbol in getattr(project_index, "symbols", []):
            symbol_by_name.setdefault(symbol.name, []).append(symbol)

        for name in candidates:
            symbols = symbol_by_name.get(name, [])
            locations = sorted({s.file for s in symbols})
            evidence.facts.append(
                EvidenceFact(
                    kind="symbol",
                    subject=name,
                    exists=bool(symbols),
                    files=locations,
                    details=(
                        ", ".join(sorted({f"{s.kind}@{s.line}" for s in symbols}))
                        if symbols else "인덱스에서 심볼을 찾지 못함"
                    ),
                )
            )

        # Exact source-text probes catch claims about literals/typos such as
        # `fal` that are not symbols and therefore cannot be found in the
        # symbol index. Only inspect the small set of context files.
        for token in candidates:
            matches = []
            for rel in files[:12]:
                path = Path(project_index.workspace) / rel
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text):
                    matches.append(rel)
            if not matches:
                evidence.facts.append(
                    EvidenceFact(
                        kind="source_text",
                        subject=token,
                        exists=False,
                        details="선정된 실제 파일 내용에서 정확히 발견되지 않음",
                    )
                )

        return evidence


    @staticmethod
    def _mentioned_files(prompt: str) -> list[str]:
        matches = re.findall(
            r"(?<![A-Za-z0-9_./\\-])(?:[A-Za-z0-9_.\\/-]+\.(?:cs|py|js|ts|tsx|jsx|java|kt|kts|rs|go|cpp|cc|c|h|hpp|json|yaml|yml|toml|xml|md|txt))(?![A-Za-z0-9_./\\-])",
            prompt or "",
            flags=re.IGNORECASE,
        )
        out = []
        for item in matches:
            item = item.replace("\\", "/")
            if item not in out:
                out.append(item)
        return out[:8]

    @staticmethod
    def _mentioned_identifiers(prompt: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", prompt or "")
        result = []
        keywords = {"cs", "file", "fix", "false", "true", "code", "unity"}
        for token in tokens:
            if token.lower() in keywords or len(token) < 2:
                continue
            if token not in result:
                result.append(token)
        return result[:24]
