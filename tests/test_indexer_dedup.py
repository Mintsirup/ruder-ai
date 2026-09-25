"""Indexer 중복 제거 단위 테스트.

TODO.md P2 "scan/index 중복 제거" 항목 검증:
- 고아 파일 `ruder_ai/indexer/indexer.py`가 실제로 삭제되어 없는지.
- 진짜 scan+index 생성 경로(ProjectScanner -> ProjectDetector ->
  SymbolIndexer)가 정상 동작하는지.
- `AIAgent._rebuild_derived_indexes`와 동일한 파생 인덱스 빌드
  시퀀스(call_graph/reference_index/semantic_file_index/type_resolver)가
  예외 없이 도는지.

`core/agent.py`는 `httpx` 의존성 때문에 네트워크 차단 환경에서 직접
import가 안 되므로, 그 개별 구성 요소를 직접 조합해 같은 시퀀스를
검증한다 (기존 다른 테스트들과 동일한 제약).
"""

from __future__ import annotations

from pathlib import Path

from ruder_ai.indexer.call_graph import CallGraph
from ruder_ai.indexer.detector import ProjectDetector
from ruder_ai.indexer.reference_index import ReferenceIndex
from ruder_ai.indexer.scanner import ProjectScanner
from ruder_ai.indexer.semantic_file_index import SemanticFileIndex
from ruder_ai.indexer.symbol_indexer import SymbolIndexer
from ruder_ai.indexer.type_resolver import TypeResolver


def _build_full_index(workspace: Path):
    """agent.py::_build_project_index와 동일한 시퀀스."""

    scanner = ProjectScanner(workspace)
    detector = ProjectDetector()
    indexer = SymbolIndexer()

    files = scanner.scan()
    inverted = scanner.inverted_index
    project = detector.detect(workspace, files)

    index = indexer.build(
        workspace=workspace,
        files=files,
        project=project,
        inverted_index=inverted,
    )

    return index


def _rebuild_derived_indexes(index):
    """agent.py::_rebuild_derived_indexes와 동일한 시퀀스."""

    call_graph = CallGraph()
    reference_index = ReferenceIndex()
    semantic_file_index = SemanticFileIndex()
    type_resolver = TypeResolver()

    call_graph.build(index)
    reference_index.build(index)
    semantic_file_index.build(index)
    type_resolver.build(index)

    return call_graph, reference_index, semantic_file_index, type_resolver


def test_orphan_indexer_file_removed():
    """`ruder_ai/indexer/indexer.py` 고아 파일이 삭제되어 없어야 한다."""

    import ruder_ai.indexer.scanner as scanner_module

    package_dir = Path(scanner_module.__file__).parent
    orphan_path = package_dir / "indexer.py"

    assert not orphan_path.exists()

    # import 자체도 실패해야 한다 (파일이 없으므로 ModuleNotFoundError).
    try:
        import importlib

        importlib.import_module("ruder_ai.indexer.indexer")
        assert False, "orphan module should not be importable"
    except ModuleNotFoundError:
        pass


def test_single_scan_index_path_builds_project_index(tmp_path: Path):
    """ProjectScanner -> ProjectDetector -> SymbolIndexer 경로가
    정상적으로 ProjectIndex를 생성해야 한다."""

    (tmp_path / "main.py").write_text(
        "def hello():\n    return 'hi'\n",
        encoding="utf-8",
    )

    index = _build_full_index(tmp_path)

    assert index.workspace == tmp_path.resolve()
    assert any(
        f.relative_path == "main.py" for f in index.files
    )
    assert any(
        symbol.name == "hello" for symbol in index.symbols
    )


def test_derived_indexes_build_without_error(tmp_path: Path):
    """call_graph/reference_index/semantic_file_index/type_resolver가
    _rebuild_derived_indexes와 같은 순서로 예외 없이 빌드돼야 한다."""

    (tmp_path / "main.py").write_text(
        "def hello():\n    return hello_helper()\n\n\n"
        "def hello_helper():\n    return 'hi'\n",
        encoding="utf-8",
    )

    index = _build_full_index(tmp_path)

    call_graph, reference_index, semantic_file_index, type_resolver = (
        _rebuild_derived_indexes(index)
    )

    # 예외 없이 끝나면 각 컴포넌트의 내부 상태가 초기화/구축돼 있어야 한다.
    assert call_graph.calls is not None
    assert reference_index.references is not None
    assert semantic_file_index.index is not None
    assert type_resolver.type_map is not None
