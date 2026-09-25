"""git_status/git_diff/git_checkout 전용 Tool 단위 테스트.

TODO.md P3 "Git" 항목 검증:
- git_status가 변경된 파일을 보여주는지.
- git_diff가 특정 파일/전체 diff 모두 되는지.
- git_checkout이 실제로 마지막 커밋 내용으로 파일을 복원하는지.
- git_checkout을 file_path 없이 호출하면 에러로 막히는지.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from ruder_ai.skills.git_ops import (
    GitCheckoutSkill,
    GitDiffSkill,
    GitStatusSkill,
)


def _run(coro):
    return asyncio.run(coro)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    _git("init", cwd=tmp_path)
    _git("config", "user.email", "test@example.com", cwd=tmp_path)
    _git("config", "user.name", "Test", cwd=tmp_path)

    (tmp_path / "existing.txt").write_text("hello\nworld\n", encoding="utf-8")
    _git("add", "existing.txt", cwd=tmp_path)
    _git("commit", "-m", "initial", cwd=tmp_path)

    return tmp_path


def test_git_status_shows_changed_files(git_repo: Path):
    (git_repo / "existing.txt").write_text("hello\nWORLD\n", encoding="utf-8")
    (git_repo / "new_file.txt").write_text("new\n", encoding="utf-8")

    skill = GitStatusSkill()
    result = _run(skill.execute(workspace_path=str(git_repo)))

    assert result["status"] == "success"
    assert "existing.txt" in result["stdout"]
    assert "new_file.txt" in result["stdout"]


def test_git_diff_specific_file(git_repo: Path):
    (git_repo / "existing.txt").write_text("hello\nWORLD\n", encoding="utf-8")

    skill = GitDiffSkill()
    result = _run(
        skill.execute(file_path="existing.txt", workspace_path=str(git_repo))
    )

    assert result["status"] == "success"
    assert "existing.txt" in result["stdout"]
    assert "WORLD" in result["stdout"]


def test_git_diff_all_files(git_repo: Path):
    (git_repo / "existing.txt").write_text("hello\nWORLD\n", encoding="utf-8")

    skill = GitDiffSkill()
    result = _run(skill.execute(workspace_path=str(git_repo)))

    assert result["status"] == "success"
    assert "existing.txt" in result["stdout"]


def test_git_checkout_restores_last_commit(git_repo: Path):
    (git_repo / "existing.txt").write_text("broken\n", encoding="utf-8")
    assert (git_repo / "existing.txt").read_text() == "broken\n"

    skill = GitCheckoutSkill()
    result = _run(
        skill.execute(file_path="existing.txt", workspace_path=str(git_repo))
    )

    assert result["status"] == "success"
    assert (git_repo / "existing.txt").read_text() == "hello\nworld\n"


def test_git_checkout_requires_file_path(git_repo: Path):
    skill = GitCheckoutSkill()
    result = _run(skill.execute(workspace_path=str(git_repo)))

    assert result["status"] == "error"
    assert "file_path" in result["message"]
