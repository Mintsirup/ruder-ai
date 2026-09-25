"""Patch Generator/Applier 단위 테스트.

TODO.md P1 "Patch Generator" 항목 검증: 수정/신규 생성/삭제 diff 생성
및 적용, rollback으로 원복, 이미 적용된 patch를 다시 적용했을 때
검증 단계에서 실패 처리되는지까지 확인한다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ruder_ai.skills.patch_ops import (
    ApplyPatchSkill,
    GenerateDiffSkill,
    RollbackPatchSkill,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "existing.txt").write_text("hello\nworld\n", encoding="utf-8")
    return tmp_path


def test_modify_apply_and_rollback(workspace: Path):
    gen = GenerateDiffSkill()
    app = ApplyPatchSkill()
    roll = RollbackPatchSkill()

    async def scenario():
        gen_result = await gen.execute(
            file_path="existing.txt",
            new_content="hello\nWORLD\n",
            workspace_path=str(workspace),
        )
        assert gen_result["status"] == "success"
        diff = gen_result["diff"]
        assert diff

        apply_result = await app.execute(diff=diff, workspace_path=str(workspace))
        assert apply_result["status"] == "success"
        assert (workspace / "existing.txt").read_text() == "hello\nWORLD\n"

        rollback_result = await roll.execute(diff=diff, workspace_path=str(workspace))
        assert rollback_result["status"] == "success"
        assert (workspace / "existing.txt").read_text() == "hello\nworld\n"

    asyncio.run(scenario())


def test_create_new_file(workspace: Path):
    gen = GenerateDiffSkill()
    app = ApplyPatchSkill()

    async def scenario():
        gen_result = await gen.execute(
            file_path="newone.txt",
            new_content="abc\n",
            workspace_path=str(workspace),
        )
        assert gen_result["status"] == "success"

        apply_result = await app.execute(
            diff=gen_result["diff"], workspace_path=str(workspace)
        )
        assert apply_result["status"] == "success"
        assert (workspace / "newone.txt").read_text() == "abc\n"

    asyncio.run(scenario())


def test_delete_file(workspace: Path):
    gen = GenerateDiffSkill()
    app = ApplyPatchSkill()

    async def scenario():
        gen_result = await gen.execute(
            file_path="existing.txt",
            delete=True,
            workspace_path=str(workspace),
        )
        assert gen_result["status"] == "success"

        apply_result = await app.execute(
            diff=gen_result["diff"], workspace_path=str(workspace)
        )
        assert apply_result["status"] == "success"
        assert not (workspace / "existing.txt").exists()

    asyncio.run(scenario())


def test_duplicate_apply_fails_without_touching_files(workspace: Path):
    gen = GenerateDiffSkill()
    app = ApplyPatchSkill()

    async def scenario():
        gen_result = await gen.execute(
            file_path="existing.txt",
            new_content="hello\nWORLD2\n",
            workspace_path=str(workspace),
        )
        diff = gen_result["diff"]

        first = await app.execute(diff=diff, workspace_path=str(workspace))
        assert first["status"] == "success"

        content_after_first = (workspace / "existing.txt").read_text()

        second = await app.execute(diff=diff, workspace_path=str(workspace))
        assert second["status"] == "error"

        # 검증 실패 시 파일이 그대로여야 한다 (건드리지 않음).
        assert (workspace / "existing.txt").read_text() == content_after_first

    asyncio.run(scenario())


def test_works_outside_git_repository(tmp_path: Path):
    """`.git` 저장소가 아닌 일반 디렉토리에서도 동작해야 한다."""

    assert not (tmp_path / ".git").exists()

    (tmp_path / "plain.txt").write_text("a\nb\n", encoding="utf-8")

    gen = GenerateDiffSkill()
    app = ApplyPatchSkill()

    async def scenario():
        gen_result = await gen.execute(
            file_path="plain.txt",
            new_content="a\nB\n",
            workspace_path=str(tmp_path),
        )
        apply_result = await app.execute(
            diff=gen_result["diff"], workspace_path=str(tmp_path)
        )
        assert apply_result["status"] == "success"
        assert (tmp_path / "plain.txt").read_text() == "a\nB\n"

    asyncio.run(scenario())
