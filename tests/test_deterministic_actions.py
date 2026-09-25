from pathlib import Path

import pytest

from ruder_ai.core.deterministic_actions import DeterministicActionResolver


def test_classify_environment():
    assert DeterministicActionResolver.classify("현재 Python 실행 환경과 프로젝트 경로를 확인해줘") == "environment_check"


def test_classify_csharp_check_but_not_repair():
    assert DeterministicActionResolver.classify("PlayerController.cs 컴파일 오류를 검사해줘") == "csharp_check"
    assert DeterministicActionResolver.classify("PlayerController.cs 컴파일 오류를 수정해줘") is None


def test_file_discovery(tmp_path):
    target = tmp_path / "Assets" / "Scripts" / "PlayerController.cs"
    target.parent.mkdir(parents=True)
    target.write_text("class PlayerController {}", encoding="utf-8")
    resolver = DeterministicActionResolver(tmp_path)
    result = resolver.file_discovery("PlayerController.cs 파일을 찾아줘")
    assert result["files"][0]["path"] == "Assets/Scripts/PlayerController.cs"


def test_python_check(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("x = 1\n", encoding="utf-8")
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    resolver = DeterministicActionResolver(tmp_path)
    result = resolver.python_check("bad.py 파이썬 문법 검사")
    assert result["status"] == "failed"
