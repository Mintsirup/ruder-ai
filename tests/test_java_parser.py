"""JavaParser 단위 테스트.

이번에 강화된 기능을 검증한다:
- class/interface 의 extends, implements 파싱
- 메서드 바로 위 어노테이션 (@EventHandler, @Override 등) 인식
- Bukkit/Spigot 스타일 리스너(Listener 구현 + @EventHandler) 감지
- 기존에 있던 modifier 정규식 버그
  (`public|private|...|static\\s+` 처럼 `\\s+` 가 마지막
  대안에만 걸려 "public class Foo" 같은 흔한 선언도
  매칭되지 않던 문제) 가 실제로 고쳐졌는지.
"""

from __future__ import annotations

from pathlib import Path

from ruder_ai.indexer.java_parser import JavaParser
from ruder_ai.indexer.models import FileInfo


LISTENER_SOURCE = """
package com.tokyowar.listener;

import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerJoinEvent;

public class JoinListener implements Listener {

    private String welcomeMessage;

    @EventHandler
    public void onPlayerJoin(PlayerJoinEvent event) {
        event.getPlayer().sendMessage(welcomeMessage);
    }

    @Override
    public String toString() {
        return "JoinListener";
    }
}
"""

INHERITANCE_SOURCE = """
package com.tokyowar.core;

public class TeamCore extends BaseCore implements Listener, Saveable {

    public static final int MAX_MEMBERS = 10;
}
"""


def _parse(text: str, tmp_path: Path, filename: str):

    java_file = tmp_path / filename
    java_file.write_text(text, encoding="utf-8")

    file_info = FileInfo(
        relative_path=filename,
        absolute_path=java_file,
        extension=".java",
        size=len(text),
    )

    return JavaParser().parse(file_info)


def test_public_class_is_detected(tmp_path):
    """modifier 정규식 버그 회귀 테스트:

    'public class Foo' 처럼 흔한 선언이 과거에는 매칭되지
    않았다. 지금은 정상적으로 class 심볼이 나와야 한다.
    """

    symbols = _parse(
        LISTENER_SOURCE, tmp_path, "JoinListener.java"
    )

    classes = [s for s in symbols if s.kind == "class"]

    assert len(classes) == 1
    assert classes[0].name == "JoinListener"


def test_implements_listener_detected(tmp_path):

    symbols = _parse(
        LISTENER_SOURCE, tmp_path, "JoinListener.java"
    )

    cls = next(s for s in symbols if s.kind == "class")

    assert cls.implements == ["Listener"]
    assert cls.is_listener is True


def test_event_handler_method_marked_as_listener(tmp_path):

    symbols = _parse(
        LISTENER_SOURCE, tmp_path, "JoinListener.java"
    )

    handler = next(
        s for s in symbols if s.name == "onPlayerJoin"
    )

    assert handler.kind == "listener_method"
    assert handler.is_listener is True
    assert "EventHandler" in handler.annotations


def test_non_handler_method_not_marked_as_listener(tmp_path):

    symbols = _parse(
        LISTENER_SOURCE, tmp_path, "JoinListener.java"
    )

    to_string = next(
        s for s in symbols if s.name == "toString"
    )

    assert to_string.kind == "method"
    assert to_string.is_listener is False
    assert to_string.annotations == ["Override"]


def test_extends_and_multiple_implements(tmp_path):

    symbols = _parse(
        INHERITANCE_SOURCE, tmp_path, "TeamCore.java"
    )

    cls = next(s for s in symbols if s.kind == "class")

    assert cls.extends == "BaseCore"
    assert cls.implements == ["Listener", "Saveable"]
    assert cls.is_listener is True


def test_field_still_parsed_alongside_new_features(
    tmp_path,
):

    symbols = _parse(
        INHERITANCE_SOURCE, tmp_path, "TeamCore.java"
    )

    field = next(
        s for s in symbols if s.name == "MAX_MEMBERS"
    )

    assert field.kind == "field"
    assert field.datatype == "int"
