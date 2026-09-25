from pathlib import Path

import pytest

from ruder_ai.skills.file_ops import ReadFileSkill, PatchFileSkill


@pytest.mark.asyncio
async def test_read_file_corrects_unique_wrong_extension(tmp_path: Path):
    target = tmp_path / 'Assets' / 'Scripts' / 'PlayerController.cs'
    target.parent.mkdir(parents=True)
    target.write_text('moveSpeed = 5f;\n', encoding='utf-8')

    result = await ReadFileSkill().execute(
        file_path='Assets/Scripts/PlayerController.json',
        workspace_path=str(tmp_path),
    )

    assert result['status'] == 'success'
    assert result['content'] == 'moveSpeed = 5f;\n'
    assert result['corrected_to'] == 'Assets/Scripts/PlayerController.cs'


@pytest.mark.asyncio
async def test_patch_file_corrects_unique_wrong_extension(tmp_path: Path):
    target = tmp_path / 'Assets' / 'Scripts' / 'PlayerController.cs'
    target.parent.mkdir(parents=True)
    target.write_text('moveSpeed = 5f;\n', encoding='utf-8')

    result = await PatchFileSkill().execute(
        file_path='Assets/Scripts/PlayerController.json',
        old_str='moveSpeed = 5f;',
        new_str='moveSpeed = 7f;',
        workspace_path=str(tmp_path),
    )

    assert result['status'] == 'success'
    assert '자동 보정' in result['message']
    assert target.read_text(encoding='utf-8') == 'moveSpeed = 7f;\n'
