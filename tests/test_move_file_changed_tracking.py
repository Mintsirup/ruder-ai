"""FM-010 재현 버그 회귀 테스트.

버그: move_file 성공 시 ToolExecutor._track_changed_file이 원본 경로
(file_path/path, 이동 후 더 이상 존재하지 않음)를 changed_files에
넣었다. Reflector가 이 경로로 파일을 열려고 하면 존재하지 않아
"삭제됨 또는 존재하지 않음"으로 표시되고, LLM 리뷰어가 "원본 파일
내용이 안 보인다"며 false negative를 냈다.

수정 후: move_file은 목적지(new_path/destination) 경로를 changed_files
에 넣어야 한다 — 실제로 지금 존재하는 파일이라 Reflector가 내용을
읽어 정상적으로 검토할 수 있다.
"""

from __future__ import annotations

from ruder_ai.core.executor import ToolExecutor


def test_move_file_tracks_destination_not_source():
    changed_files: list[str] = []
    executor = ToolExecutor.__new__(ToolExecutor)  # __init__ 우회 (순수 메서드 테스트)

    executor._track_changed_file(
        "move_file",
        {"file_path": "old/loc.py", "new_path": "new/loc.py"},
        {"status": "success", "message": "old/loc.py -> new/loc.py 이동 완료"},
        changed_files,
    )

    assert changed_files == ["new/loc.py"]
    assert "old/loc.py" not in changed_files


def test_move_file_tracks_destination_alias_kwargs():
    """destination 키(new_path 대신)로 호출된 경우도 동일하게 처리."""

    changed_files: list[str] = []
    executor = ToolExecutor.__new__(ToolExecutor)

    executor._track_changed_file(
        "move_file",
        {"path": "old/loc.py", "destination": "new/loc.py"},
        {"status": "success", "message": "moved"},
        changed_files,
    )

    assert changed_files == ["new/loc.py"]


def test_move_file_failure_not_tracked():
    changed_files: list[str] = []
    executor = ToolExecutor.__new__(ToolExecutor)

    executor._track_changed_file(
        "move_file",
        {"file_path": "old/loc.py", "new_path": "new/loc.py"},
        {"status": "error", "message": "파일이 존재하지 않습니다"},
        changed_files,
    )

    assert changed_files == []


def test_other_mutating_tools_unaffected():
    """move_file 외 write_file 등은 기존과 동일하게 file_path 기준."""

    changed_files: list[str] = []
    executor = ToolExecutor.__new__(ToolExecutor)

    executor._track_changed_file(
        "write_file",
        {"file_path": "a.py", "content": "x"},
        {"status": "success"},
        changed_files,
    )

    assert changed_files == ["a.py"]