"""Live GUI checks, run as a subprocess by the test suite.

Why a subprocess: pytest replaces ``sys.stdout`` with a capture object, and on
Windows the *second* ``tkinter.Tk()`` in a process then dies with
"Can't find a usable init.tcl" under capture while working fine with ``-s``.
Requiring ``pytest -s`` for the whole suite would cost the other 270 tests
their captured output, so the two windowed checks run out-of-process instead.

Not collected by pytest (leading underscore, no ``test_`` prefix). Invoked as::

    python tests/_studio_gui_check.py <mode> <workspace-dir>

Exit code 0 means every assertion passed; output carries the diagnostics.
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEMO_PY = (
    '"""Tiny demo module."""\n'
    "import os\n"
    "from pathlib import Path as P\n\n"
    "MAX_RETRIES = 3\n\n\n"
    "@lru_cache(maxsize=None)\n"
    "def load(path: P) -> dict:\n"
    '    raw = path.read_text(encoding="utf-8")\n'
    "    return {k: str(v) for k, v in json.loads(raw).items()}\n\n\n"
    "class Loader:\n"
    "    def run(self) -> None:\n"
    "        for i in range(MAX_RETRIES):\n"
    "            try:\n"
    '                load(P(".") / "config.json")\n'
    "            except FileNotFoundError:\n"
    "                continue\n"
)


def _tagged(widget, tag):
    ranges = widget.tag_ranges(tag)
    return [widget.get(a, b) for a, b in zip(ranges[0::2], ranges[1::2])]


def check_boot(workspace: Path) -> None:
    from ruder_ai.tui.app_gui import RuderAIVSCodeApp

    app = RuderAIVSCodeApp()
    try:
        app.update()
        assert app.agent is not None, app.agent_error
        print("agent      :", app.agent.model_name, app.agent.ollama_host)

        # Terminal session must be live, whichever backend won.
        assert app.shell_session is not None
        assert app.shell_session.proc is not None
        print("terminal   :", type(app.shell_session).__name__)

        # Round trip through the real queue -> after() -> widget path.
        app.shell_session.send(b"echo STUDIO_TERMINAL_OK\n")
        deadline = time.time() + 10
        found = False
        while time.time() < deadline and not found:
            app.update()
            time.sleep(0.05)
            found = "STUDIO_TERMINAL_OK" in app.term_display.get("1.0", "end")
        assert found, "terminal round trip failed"
        print("round trip : OK")

        # Escape sequences must never reach the widget.
        app._append_terminal_text("\x1b[32mgreen\x1b[0m ok\x1b]0;title\x07\n")
        body = app.term_display.get("1.0", "end")
        assert "green ok" in body, body
        assert "0;title" not in body, "OSC leaked into the terminal"
        print("ansi strip : OK")

        # Lazy tree: only the top level exists until a folder is expanded.
        root = app.tree.get_children("")[0]
        labels = [app.tree.item(i, "text") for i in app.tree.get_children(root)]
        assert any("sub" in n for n in labels), labels
        assert not any("leaf.txt" in n for n in labels), "tree was not lazy"
        sub = next(i for i in app.tree.get_children(root) if "sub" in app.tree.item(i, "text"))
        assert [app.tree.item(i, "text") for i in app.tree.get_children(sub)] == ["…"]
        app.tree.selection_set(sub)
        app._on_tree_open(None)
        deeper = [app.tree.item(i, "text") for i in app.tree.get_children(sub)]
        assert any("leaf.txt" in n for n in deeper), deeper
        print("lazy tree  : OK")

        # Config must reach the agent.
        app.config_mgr.update({
            "ollama_base_url": "http://10.0.0.9:11434",
            "temperature": 0.42,
        })
        app._create_agent()
        assert app.agent.ollama_host == "http://10.0.0.9:11434", app.agent.ollama_host
        assert app.agent.settings.temperature == 0.42
        print("config     : OK")
    finally:
        if app.shell_session is not None:
            try:
                app.shell_session.close()
            except Exception:
                pass
        app.withdraw()


def check_editor(workspace: Path) -> None:
    from ruder_ai.tui.app_gui import FIND_TAG, MATCH_TAG, RuderAIVSCodeApp

    source = workspace / "sample.py"
    source.write_text(
        'import os\n\n@deco\ndef greet(name="w"):\n    # note\n    print(42)\n',
        encoding="utf-8",
    )

    app = RuderAIVSCodeApp()
    try:
        app.update()
        app._open_file_in_editor(source)
        app.update()
        widget = app._current_editor()
        assert widget is not None

        widget._highlighter.cancel()
        widget._highlighter._run()
        app.update()

        print("keyword    :", _tagged(widget, "keyword"))
        print("comment    :", _tagged(widget, "comment"))
        print("string     :", _tagged(widget, "string"))
        print("number     :", _tagged(widget, "number"))
        print("decorator  :", _tagged(widget, "decorator"))
        print("builtin    :", _tagged(widget, "builtin"))

        assert "import" in _tagged(widget, "keyword")
        assert "def" in _tagged(widget, "keyword")
        assert _tagged(widget, "comment") == ["# note"]
        assert '"w"' in _tagged(widget, "string")
        assert "42" in _tagged(widget, "number")
        assert _tagged(widget, "decorator") == ["@deco"]
        assert "print" in _tagged(widget, "builtin")
        print("highlight  : OK")

        app._open_find()
        app.update()
        dialog = app.find_dialog
        assert dialog is not None and dialog.winfo_exists()
        dialog.find_var.set("greet")
        assert dialog.refresh() == 1
        app.update()
        assert widget.tag_ranges(MATCH_TAG), "matches not painted"
        dialog.step(1)
        app.update()
        assert widget.tag_ranges(FIND_TAG), "current hit not painted"
        print("find       : OK")

        dialog.replace_var.set("salute")
        dialog.replace_current()
        app.update()
        body = widget.get("1.0", "end-1c")
        assert "def salute(" in body and "greet" not in body, body
        assert app._is_dirty(widget), "replacement did not mark the buffer dirty"
        print("replace    : OK")

        dialog.find_var.set("print")
        dialog.replace_var.set("log")
        dialog.word_var.set(True)
        dialog.replace_everything()
        app.update()
        assert "    log(42)" in widget.get("1.0", "end-1c")
        print("replace all: OK")

        dialog.regex_var.set(True)
        dialog.find_var.set("[unclosed")
        assert dialog.refresh() == 0
        assert "정규식" in dialog.status_var.get(), dialog.status_var.get()
        print("bad regex  : OK")
        dialog.regex_var.set(False)

        # Whole-word must find punctuation needles; the old \b approach could
        # never match "//" in a comment.
        widget.delete("1.0", "end-1c")
        widget.insert("1.0", 'import os  # note\n// divider\nvalue = 1\n')
        app._on_text_modified(widget)
        dialog.word_var.set(True)
        dialog.case_var.set(True)
        dialog.find_var.set("//")
        assert dialog.refresh() == 1, "whole-word // search failed"
        app.update()
        assert "Ln 2" in dialog.status_var.get(), dialog.status_var.get()
        print("punct word : OK ->", dialog.status_var.get())

        # Typo-tolerant fallback, and it must be flagged as approximate.
        # "vaule" is a transposition of "value" - distance 2, above zero, so
        # only the fuzzy path can find it.
        dialog.find_var.set("vaule")
        assert dialog.refresh() == 0, "exact search should not match a typo"
        dialog.fuzzy_var.set(True)
        assert dialog.refresh() == 1, "fuzzy fallback found nothing"
        assert dialog._fuzzy_hit is True
        assert "≈" in dialog.status_var.get(), dialog.status_var.get()
        print("fuzzy      : OK ->", dialog.status_var.get())
        dialog.fuzzy_var.set(False)
        dialog.word_var.set(False)
        dialog.case_var.set(False)

        dialog.close()
        app.find_dialog = None
        app.update()
        assert not widget.tag_ranges(MATCH_TAG), "hit tags survived close"
        print("close      : OK")

        widget.insert("end", "# tail\n")
        app.update()
        widget._highlighter.cancel()
        widget._highlighter._run()
        app.update()
        assert "# tail" in _tagged(widget, "comment")
        print("re-highlite: OK")
    finally:
        app.find_dialog = None
        if app.shell_session is not None:
            try:
                app.shell_session.close()
            except Exception:
                pass
        app.withdraw()


CHECKS = {"boot": check_boot, "editor": check_editor}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "boot"
    workspace = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp())
    workspace.mkdir(parents=True, exist_ok=True)

    if mode == "boot":
        (workspace / "sub").mkdir(exist_ok=True)
        (workspace / "sub" / "leaf.txt").write_text("hi", encoding="utf-8")
        (workspace / "top.py").write_text("x = 1\n", encoding="utf-8")

    original_cwd = Path.cwd()
    # The Studio resolves its workspace through ConfigManager, which is
    # CWD-relative, so run from inside the throwaway workspace.
    os.chdir(workspace)
    try:
        CHECKS[mode](workspace)
        print(f"GUI CHECK OK ({mode})")
        return 0
    except Exception:
        import traceback

        traceback.print_exc()
        return 1
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
