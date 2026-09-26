"""RuderAI Studio — Code-OSS style desktop GUI, built on stdlib tkinter.

The Studio used to be a PyQt6 application.  PyQt6 is a ~60 MB binary wheel that
had to be present on every machine that wanted the GUI, and the Qt dependency
bought nothing here: the shell session layer, the config plumbing and the agent
worker are all toolkit-agnostic.  Everything below is standard library.

What changed with the toolkit swap, deliberately:

* The file tree now loads **lazily**.  The old implementation walked the entire
  tree recursively on startup *and* again after every AI response, which froze
  the window for seconds on a large repository.  Directories are populated the
  first time they are expanded.
* Drag-and-drop file moving is gone — tkinter has no native DnD.  The context
  menu gained an explicit "이동" action instead, which is both more reliable
  and harder to trigger by accident.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import queue
import shutil
import sys
import threading
import traceback
from pathlib import Path
from typing import Callable

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from ruder_ai.core.agent import AIAgent
from ruder_ai.core.config import ConfigManager, settings_from_config
from ruder_ai.core.telemetry import END_OF_TOKEN
from ruder_ai.tui import find_replace
from ruder_ai.tui.find_replace import Match
from ruder_ai.tui.shell_session import PipeReaderThread, start_shell, strip_ansi
from ruder_ai.tui.syntax import detect_language, tokenize

# Directories the tree hides.  Kept in step with the agent's own file resolver so
# the explorer and the tools agree on what exists.
HIDDEN_DIRS = {".git", "__pycache__", "node_modules", "bin", "obj", "Library", ".venv", "venv"}

DARK = {
    "bg": "#1e1e1e",
    "panel": "#252526",
    "chrome": "#333333",
    "bar": "#2d2d2d",
    "text": "#cccccc",
    "bright": "#ffffff",
    "muted": "#858585",
    "accent": "#007acc",
    "terminal_bg": "#0e0e0e",
    "terminal_fg": "#00ff66",
}

MONO = ("Consolas", 10)
UI_FONT = ("Segoe UI", 10)

# Syntax colours, roughly the VS Code Dark+ palette.
SYNTAX_COLORS = {
    "comment": "#6A9955",
    "string": "#CE9178",
    "number": "#B5CEA8",
    "keyword": "#569CD6",
    "builtin": "#DCDCAA",
    "decorator": "#D7BA7D",
    "constant": "#4FC1FF",
    "type": "#4EC9B0",
    "key": "#9CDCFE",
    "heading": "#569CD6",
    "markup": "#D7BA7D",
}

FIND_TAG = "find_hit"
FIND_CURRENT_TAG = "find_current"
MATCH_TAG = "match_all"

# Highlighting rewrites every tag in the buffer, which is O(file). Debounce so
# typing stays responsive, and give up on files too big to colour cheaply.
HIGHLIGHT_DEBOUNCE_MS = 250
HIGHLIGHT_MAX_CHARS = 250_000


def _configure_dark(root: tk.Misc) -> None:
    """Apply a Code-OSS-ish dark theme. Best effort: never fatal."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=DARK["bg"], foreground=DARK["text"], font=UI_FONT)
    style.configure("TFrame", background=DARK["bg"])
    style.configure("Panel.TFrame", background=DARK["panel"])
    style.configure("Bar.TFrame", background=DARK["bar"])
    style.configure("Chrome.TFrame", background=DARK["chrome"])
    style.configure("TLabel", background=DARK["bg"], foreground=DARK["text"])
    style.configure("Muted.TLabel", background=DARK["bar"], foreground=DARK["muted"], font=UI_FONT)
    style.configure("TButton", background="#3c3c3c", foreground="#ffffff", borderwidth=0, padding=(8, 4))
    style.map("TButton", background=[("active", "#4a4a4a")])
    style.configure("Accent.TButton", background=DARK["accent"], foreground="#ffffff", padding=(8, 4))
    style.map("Accent.TButton", background=[("active", "#0098ff")])
    style.configure("TNotebook", background=DARK["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", background="#2d2d2d", foreground=DARK["muted"], padding=(10, 5))
    style.map("TNotebook.Tab", background=[("selected", DARK["bg"]), ("active", "#3c3c3c")])
    style.configure("Treeview", background=DARK["panel"], fieldbackground=DARK["panel"], foreground=DARK["text"])
    style.configure("Treeview.Item", background=DARK["panel"], foreground=DARK["text"])
    style.map("Treeview.Item", background=[("selected", "#37373d")], foreground=[("selected", "#ffffff")])
    style.configure("TPanedWindow", background=DARK["bg"])
    style.configure("Vertical.TPanedWindow", background=DARK["bg"])


class AIWorker(threading.Thread):
    """Runs one agent task off the UI thread and delivers the result."""

    def __init__(self, agent, prompt: str, on_done: Callable[[str], None]):
        super().__init__(daemon=True)
        self.agent = agent
        self.prompt = prompt
        self.on_done = on_done

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                response = loop.run_until_complete(self.agent.process_task(self.prompt))
                result = str(response) if response else "응답이 비어있습니다."
            finally:
                loop.close()
        except Exception:
            result = f"⚠️ 에러 발생:\n{traceback.format_exc()}"
        self.on_done(result)


class EnvDialog(tk.Toplevel):
    """환경변수 추가/수정 대화상자"""

    def __init__(self, parent, config_mgr: ConfigManager):
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.title("환경변수 설정")
        self.configure(background=DARK["bg"])
        self.geometry("480x360")

        ttk.Label(
            self,
            text="환경변수 설정 (형식: KEY=VALUE / 한 줄에 하나씩)",
            background=DARK["bg"],
            foreground=DARK["bright"],
            font=("Segoe UI", 10, "bold"),
        ).pack(padx=10, pady=(10, 4), anchor="w")

        current = self.config_mgr.get("env_vars", {}) or {}
        body = "\n".join(f"{k}={v}" for k, v in current.items())

        text_frame = ttk.Frame(self)
        text_frame.pack(fill="both", expand=True, padx=10, pady=6)
        self.text = tk.Text(
            text_frame,
            wrap="none",
            bg=DARK["bg"],
            fg=DARK["text"],
            insertbackground=DARK["bright"],
            font=MONO,
            relief="flat",
            highlightthickness=1,
            highlightbackground=DARK["bar"],
        )
        ybar = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=ybar.set)
        self.text.pack(side="left", fill="both", expand=True)
        ybar.pack(side="right", fill="y")
        self.text.insert("1.0", body)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(buttons, text="취소", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="💾 저장", style="Accent.TButton", command=self._save).pack(
            side="right", padx=(0, 6)
        )

    def _save(self) -> None:
        envs: dict[str, str] = {}
        for line in self.text.get("1.0", "end").strip().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if key:
                    envs[key] = value
                    os.environ[key] = value

        self.config_mgr.set("env_vars", envs)
        messagebox.showinfo("성공", "환경변수가 성공적으로 저장되었습니다.", parent=self)
        self.destroy()


class TerminalView(tk.Text):
    """Read-only terminal surface fed by a :mod:`shell_session` backend."""

    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            wrap="none",
            bg=DARK["terminal_bg"],
            fg=DARK["terminal_fg"],
            insertbackground=DARK["terminal_fg"],
            font=MONO,
            relief="flat",
            highlightthickness=0,
            state="disabled",
            **kwargs,
        )
        self.session = None

    def append(self, text: str) -> None:
        self.configure(state="normal")
        self.insert("end", text)
        self.see("end")
        self.configure(state="disabled")


class _PipeReader(PipeReaderThread):
    """Backwards-compatible alias for the previous private name."""


def _install_editor_tags(widget: tk.Text) -> None:
    """Configure syntax, find-hit and match-all tags on an editor buffer."""
    for kind, color in SYNTAX_COLORS.items():
        widget.tag_configure(kind, foreground=color)
    widget.tag_configure(MATCH_TAG, background="#3a3d41")
    widget.tag_configure(FIND_TAG, background="#6e5a00")
    widget.tag_configure(
        FIND_CURRENT_TAG, background="#d7ba7d", foreground="#1e1e1e"
    )
    # Search hits must win over the other tags regardless of insertion order.
    widget.tag_raise(FIND_TAG)
    widget.tag_raise(FIND_CURRENT_TAG)


class Highlighter:
    """Applies syntax tokens to a :class:`tk.Text` buffer as tags.

    Work is debounced and skipped on very large buffers; the alternative is
    re-tagging hundreds of thousands of characters on every keystroke, which
    makes the editor feel like it is chewing glass.
    """

    def __init__(self, widget: tk.Text, language: str | None):
        self.widget = widget
        self.language = language
        self._pending: str | None = None
        self._job: str | None = None

    def cancel(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
            self._job = None

    def schedule(self) -> None:
        self.cancel()
        if not self.language:
            self.clear()
            return
        try:
            if int(self.widget.index("end-1c").split(".")[0]) > 20_000:
                self.clear()
                return
        except (tk.TclError, ValueError):
            return
        self._job = self.widget.after(HIGHLIGHT_DEBOUNCE_MS, self._run)

    def clear(self) -> None:
        for kind in SYNTAX_COLORS:
            self.widget.tag_remove(kind, "1.0", "end")

    def _run(self) -> None:
        self._job = None
        if not self.language:
            return
        try:
            text = self.widget.get("1.0", "end-1c")
        except tk.TclError:
            return
        if len(text) > HIGHLIGHT_MAX_CHARS:
            self.clear()
            return

        self.clear()
        line_starts = _line_offsets(text)
        try:
            for token in tokenize(text, self.language):
                start = _index_from_offset(line_starts, token.start)
                end = _index_from_offset(line_starts, max(token.start, token.end - 1))
                self.widget.tag_add(token.kind, start, f"{end}+1c")
        except tk.TclError:
            return

    def destroy(self) -> None:
        self.cancel()


def _line_offsets(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _index_from_offset(starts: list[int], offset: int) -> str:
    """Translate a character offset into a Tk 'line.column' index."""
    low, high = 0, len(starts) - 1
    while low < high:
        mid = (low + high + 1) // 2
        if starts[mid] <= offset:
            low = mid
        else:
            high = mid - 1
    return f"{low + 1}.{offset - starts[low]}"


class FindReplaceDialog(tk.Toplevel):
    """Find / replace panel for the active editor tab."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.title("찾기 / 바꾸기")
        self.configure(background=DARK["bg"])
        self.resizable(False, False)
        self.transient(master)

        self.find_var = tk.StringVar()
        self.replace_var = tk.StringVar()
        self.case_var = tk.BooleanVar(value=False)
        self.word_var = tk.BooleanVar(value=False)
        self.regex_var = tk.BooleanVar(value=False)
        self.fuzzy_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="")
        self._matches: list[Match] = []
        self._index = -1
        self._fuzzy_hit = False
        self._refresh_job: str | None = None

        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="찾을 문자열").grid(row=0, column=0, sticky="w", pady=3)
        self.find_entry = ttk.Entry(outer, textvariable=self.find_var, width=32)
        self.find_entry.grid(row=0, column=1, columnspan=3, sticky="ew", pady=3)

        ttk.Label(outer, text="바꿀 문자열").grid(row=1, column=0, sticky="w", pady=3)
        self.replace_entry = ttk.Entry(outer, textvariable=self.replace_var, width=32)
        self.replace_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=3)

        opts = ttk.Frame(outer)
        opts.grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 2))
        ttk.Checkbutton(opts, text="대/소문자", variable=self.case_var).pack(side="left")
        ttk.Checkbutton(opts, text="단어 전체", variable=self.word_var).pack(side="left", padx=8)
        ttk.Checkbutton(opts, text="정규식", variable=self.regex_var).pack(side="left")
        ttk.Checkbutton(opts, text="오타 허용", variable=self.fuzzy_var).pack(side="left", padx=8)
        for var in (self.case_var, self.word_var, self.regex_var, self.fuzzy_var):
            var.trace_add("write", lambda *_: self.schedule_refresh())

        buttons = ttk.Frame(outer)
        buttons.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 4))
        ttk.Button(buttons, text="이전", command=lambda: self.step(-1)).pack(side="left")
        ttk.Button(buttons, text="다음", command=lambda: self.step(1)).pack(side="left", padx=4)
        ttk.Button(buttons, text="바꾸기", command=self.replace_current).pack(side="left", padx=12)
        ttk.Button(buttons, text="모두 바꾸기", style="Accent.TButton",
                   command=self.replace_everything).pack(side="left")
        ttk.Button(buttons, text="닫기", command=self.close).pack(side="right")

        ttk.Label(outer, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )

        outer.columnconfigure(1, weight=1)
        self.find_entry.focus_set()

        for widget in (self.find_entry, self.replace_entry):
            widget.bind("<Return>", lambda _e: self.step(1))
            widget.bind("<Escape>", lambda _e: self.close())
        self.bind("<Escape>", lambda _e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.find_var.trace_add("write", lambda *_: self.schedule_refresh())

    # -- helpers -----------------------------------------------------
    @property
    def target(self) -> tk.Text | None:
        return self.app._current_editor()

    def search_options(self) -> find_replace.SearchOptions:
        return find_replace.SearchOptions(
            case_sensitive=self.case_var.get(),
            whole_word=self.word_var.get(),
            use_regex=self.regex_var.get(),
            fuzzy=self.fuzzy_var.get(),
        )

    def options(self) -> dict:
        return {
            "case_sensitive": self.case_var.get(),
            "whole_word": self.word_var.get(),
            "use_regex": self.regex_var.get(),
            "fuzzy": self.fuzzy_var.get(),
        }

    def schedule_refresh(self) -> None:
        """Debounce: a full rescan per keystroke stalls on a large buffer."""
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except (tk.TclError, ValueError):
                pass
        self._refresh_job = self.after(120, self._do_refresh)

    def _do_refresh(self) -> None:
        self._refresh_job = None
        self.refresh()

    def close(self) -> None:
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except (tk.TclError, ValueError):
                pass
            self._refresh_job = None
        self._clear_tags()
        self.destroy()

    def _clear_tags(self) -> None:
        widget = self.target
        if widget is None:
            return
        for tag in (FIND_TAG, FIND_CURRENT_TAG, MATCH_TAG):
            widget.tag_remove(tag, "1.0", "end")

    def _current_text(self) -> str:
        widget = self.target
        return widget.get("1.0", "end-1c") if widget is not None else ""

    def refresh(self) -> int:
        """Recompute matches and paint them. Returns the match count."""
        widget = self.target
        self._clear_tags()
        self._matches = []
        self._index = -1
        if widget is None:
            self.status_var.set("열려 있는 파일이 없습니다.")
            return 0

        needle = self.find_var.get()
        if not needle:
            self.status_var.set("")
            return 0
        self.app._last_find = {"needle": needle}
        if not find_replace.is_valid_pattern(needle, use_regex=self.regex_var.get()):
            self.status_var.set("잘못된 정규식입니다.")
            return 0

        opts = self.search_options()
        text = self._current_text()
        # The first pass must run with fuzzy *off*, otherwise an approximate
        # hit is indistinguishable from an exact one and the status bar can
        # never show that the result is a guess.
        exact = find_replace.find_all(text, needle, dataclasses.replace(opts, fuzzy=False))
        self._fuzzy_hit = not exact and opts.fuzzy
        self._matches = exact
        if not self._matches and self._fuzzy_hit:
            self._matches = find_replace.find_all(text, needle, opts)
        if not self._matches:
            self.status_var.set("일치 항목 없음")
            return 0

        starts = _line_offsets(text)
        for match in self._matches:
            start = _index_from_offset(starts, match.start)
            end = _index_from_offset(starts, max(match.start, match.end - 1))
            widget.tag_add(MATCH_TAG, start, f"{end}+1c")

        self._index = 0
        self._select(0)
        return len(self._matches)

    def _select(self, index: int) -> None:
        widget = self.target
        if not self._matches or widget is None:
            return
        index = index % len(self._matches)
        self._index = index
        match = self._matches[index]
        text = self._current_text()
        starts = _line_offsets(text)
        start = _index_from_offset(starts, match.start)
        end = _index_from_offset(starts, max(match.start, match.end - 1))

        widget.tag_remove(FIND_TAG, "1.0", "end")
        widget.tag_add(FIND_TAG, start, f"{end}+1c")
        widget.tag_remove(FIND_CURRENT_TAG, "1.0", "end")
        widget.tag_add(FIND_CURRENT_TAG, start, f"{end}+1c")
        widget.mark_set("insert", start)
        widget.see(start)

        prefix = "≈ " if self._fuzzy_hit else ""
        self.status_var.set(
            f"{prefix}{index + 1} / {len(self._matches)}  "
            f"{find_replace.describe(text, match.start)}"
        )

    def step(self, direction: int) -> None:
        if not self._matches:
            self.refresh()
            return
        self._select(self._index + direction)

    def replace_current(self) -> None:
        widget = self.target
        if not self._matches or widget is None:
            self.refresh()
            return

        match = self._matches[self._index]
        text = self._current_text()
        starts = _line_offsets(text)
        start = _index_from_offset(starts, match.start)
        end = _index_from_offset(starts, match.end)
        replacement = find_replace.expand_replacement(self.replace_var.get(), self.regex_var.get())

        widget.mark_set("insert", start)
        widget.tag_remove(FIND_TAG, start, end)
        widget.replace(start, end, replacement)
        widget.edit_modified(True)
        self.app._on_text_modified(widget)
        self.refresh()
        if self._matches:
            self._select(self._index)

    def replace_everything(self) -> None:
        widget = self.target
        if widget is None:
            return
        needle = self.find_var.get()
        if not needle:
            return
        new_text, count = find_replace.replace_all(
            self._current_text(), needle, self.replace_var.get(), **self.options()
        )
        if not count:
            self.status_var.set("일치 항목 없음")
            return
        widget.delete("1.0", "end-1c")
        widget.insert("1.0", new_text)
        widget.edit_modified(True)
        self.app._on_text_modified(widget)
        self.refresh()
        self.status_var.set(f"{count}개 바꿈")


class RuderAIVSCodeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RuderAI Studio")
        self.geometry("1280x800")
        self.configure(background=DARK["bg"])
        _configure_dark(self)

        self.config_mgr = ConfigManager()
        self.workspace_path = Path(self.config_mgr.get("workspace_dir", Path.cwd())).resolve()
        self.opened_files: dict[str, tk.Text] = {}
        self.highlighters: dict[str, Highlighter] = {}
        self.find_dialog: FindReplaceDialog | None = None
        self._last_find: dict[str, str] = {}

        self._apply_custom_env()

        self.agent: AIAgent | None = None
        self.agent_error: str | None = None
        self.worker: AIWorker | None = None
        self._busy = False

        self.shell_session = None
        self._pipe_reader: PipeReaderThread | None = None
        self._term_queue: queue.Queue = queue.Queue()
        self._tree_items: dict[str, str] = {}
        self._suspend_tree_events = False

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        self._bind_shortcuts()
        self._load_file_tree()
        self._create_agent()
        self._start_terminal()
        self.after(30, self._drain_terminal)

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------
    def _create_agent(self) -> AIAgent | None:
        """(Re)build the agent from the persisted config. Returns None on failure."""
        try:
            self.agent = AIAgent(
                workspace_path=str(self.workspace_path),
                settings=settings_from_config(self.config_mgr),
            )
            self.agent_error = None
        except Exception as exc:
            self.agent = None
            self.agent_error = str(exc)
            traceback.print_exc()
        return self.agent

    def _apply_custom_env(self) -> None:
        custom_env = self.config_mgr.get("env_vars", {})
        if isinstance(custom_env, dict):
            for key, value in custom_env.items():
                os.environ[str(key)] = str(value)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # --- Top bar ---
        chrome = ttk.Frame(self, style="Chrome.TFrame", padding=(12, 6))
        chrome.pack(fill="x")
        self.title_label = ttk.Label(
            chrome,
            text=f"RuderAI Studio - [{self.workspace_path.name}]",
            style="Muted.TLabel",
            background=DARK["chrome"],
            foreground=DARK["bright"],
            font=("Segoe UI", 10, "bold"),
        )
        self.title_label.pack(side="left")
        ttk.Button(chrome, text="⚙️ 환경변수", command=self._open_env_dialog).pack(side="right", padx=(6, 0))
        ttk.Button(chrome, text="📁 폴더 열기", command=self._change_workspace).pack(side="right")

        # --- Main split ---
        main_split = ttk.PanedWindow(self, orient="horizontal")
        main_split.pack(fill="both", expand=True)

        sidebar = ttk.Frame(main_split, style="Panel.TFrame")
        explorer_bar = ttk.Frame(sidebar, style="Bar.TFrame", padding=(8, 4))
        explorer_bar.pack(fill="x")
        ttk.Label(explorer_bar, text="EXPLORER", style="Muted.TLabel").pack(side="left")
        ttk.Button(explorer_bar, text="📄+", width=3, command=lambda: self._create_new_item(False)).pack(side="right")
        ttk.Button(explorer_bar, text="📁+", width=3, command=lambda: self._create_new_item(True)).pack(
            side="right", padx=(4, 0)
        )
        ttk.Button(explorer_bar, text="🔄", width=3, command=self._load_file_tree).pack(
            side="right", padx=(4, 0)
        )

        self.tree = ttk.Treeview(sidebar, show="tree", selectmode="browse")
        tree_scroll = ttk.Scrollbar(sidebar, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<Double-1>", self._on_file_double_click)
        self.tree.bind("<Return>", self._on_file_double_click)
        self.tree.bind("<Button-3>", self._on_tree_context_menu)
        self.tree.bind("<Button-2>", self._on_tree_context_menu)
        main_split.add(sidebar, weight=1)

        right_split = ttk.PanedWindow(main_split, orient="vertical")

        # --- Editor ---
        editor_frame = ttk.Frame(right_split)
        editor_bar = ttk.Frame(editor_frame, style="Bar.TFrame", padding=(10, 4))
        editor_bar.pack(fill="x")
        ttk.Button(
            editor_bar, text="🔍 찾기/바꾸기 (Ctrl+F)", command=self._open_find,
        ).pack(side="right")
        ttk.Button(
            editor_bar, text="💾 현재 파일 저장", style="Accent.TButton",
            command=self._save_current_editor_file,
        ).pack(side="right", padx=(0, 6))

        self.editor_tabs = ttk.Notebook(editor_frame)
        self.editor_tabs.pack(fill="both", expand=True)
        self.editor_tabs.bind("<<NotebookTabClosed>>", self._on_tab_closed)
        right_split.add(editor_frame, weight=3)

        # --- Bottom notebook: AI + terminal ---
        self.notebook = ttk.Notebook(right_split)
        right_split.add(self.notebook, weight=2)

        self._build_ai_tab()
        self._build_terminal_tab()
        main_split.add(right_split, weight=4)
        main_split.sashpos(0, 240)

    def _build_ai_tab(self) -> None:
        ai_tab = ttk.Frame(self.notebook, padding=8)

        chat_frame = ttk.Frame(ai_tab)
        chat_frame.pack(fill="both", expand=True)
        self.chat_display = tk.Text(
            chat_frame,
            wrap="word",
            bg=DARK["bg"],
            fg=DARK["text"],
            insertbackground=DARK["bright"],
            font=UI_FONT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=DARK["bar"],
            state="disabled",
        )
        chat_scroll = ttk.Scrollbar(chat_frame, orient="vertical", command=self.chat_display.yview)
        self.chat_display.configure(yscrollcommand=chat_scroll.set)
        self.chat_display.pack(side="left", fill="both", expand=True)
        chat_scroll.pack(side="right", fill="y")

        input_bar = ttk.Frame(ai_tab)
        input_bar.pack(fill="x", pady=(8, 0))
        self.prompt_entry = ttk.Entry(input_bar, font=UI_FONT)
        self.prompt_entry.pack(side="left", fill="x", expand=True)
        self.prompt_entry.bind("<Return>", lambda _e: self._send_prompt())
        ttk.Button(input_bar, text="전송", style="Accent.TButton", command=self._send_prompt).pack(
            side="left", padx=(6, 0)
        )

        self.notebook.add(ai_tab, text="🤖 RuderAI AI")
        self._chat("🤖 RuderAI Studio 준비 완료. 질문을 입력하세요.\n" + "=" * 50 + "\n\n")

    def _build_terminal_tab(self) -> None:
        term_tab = ttk.Frame(self.notebook, padding=4)
        self.term_display = TerminalView(term_tab)
        term_scroll = ttk.Scrollbar(term_tab, orient="vertical", command=self.term_display.yview)
        self.term_display.configure(yscrollcommand=term_scroll.set)
        self.term_display.pack(side="left", fill="both", expand=True)
        term_scroll.pack(side="right", fill="y")
        self.term_display.bind("<Key>", self._on_terminal_key)
        self.notebook.add(term_tab, text="🖥️ Terminal")

    # ------------------------------------------------------------------
    # AI chat
    # ------------------------------------------------------------------
    def _chat(self, text: str) -> None:
        self.chat_display.configure(state="normal")
        self.chat_display.insert("end", text)
        self.chat_display.see("end")
        self.chat_display.configure(state="disabled")

    def _send_prompt(self) -> None:
        prompt = self.prompt_entry.get().strip()
        if not prompt:
            return

        # A second request while one is in flight would run two threads against
        # the same AIAgent, whose project_index / request_mode / autonomy budget
        # are all mutable shared state.
        if self._busy:
            self._chat("[System]\n이전 작업이 아직 진행 중입니다. 잠시만 기다려 주세요.\n\n")
            return

        if self.agent is None:
            self._chat(
                f"[System]\n⚠️ AI 에이전트를 초기화할 수 없습니다:\n{self.agent_error or '알 수 없는 오류'}\n\n"
            )
            if self._create_agent() is None:
                return

        self.prompt_entry.delete(0, "end")
        self._chat(f"[User]\n{prompt}\n\n")
        self._chat("[RuderAI AI]\n생각 중...\n\n")

        self._busy = True
        self.worker = AIWorker(self.agent, prompt, self._on_ai_response)
        self.worker.start()

    def _on_ai_response(self, text: str) -> None:
        # Called from the worker thread; marshal onto the UI thread.
        self.after(0, self._apply_ai_response, text)

    def _apply_ai_response(self, text: str) -> None:
        self._busy = False
        self._chat(f"{text}\n\n" + "-" * 50 + "\n")
        # Explicit terminator: without it a transcript cannot tell "the agent
        # finished" from "the run was cut off mid-task".
        self._chat(f"\n{END_OF_TOKEN}\n")
        self._load_file_tree()

    # ------------------------------------------------------------------
    # Integrated terminal
    # ------------------------------------------------------------------
    def _terminal_env(self) -> dict:
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        return env

    def _start_terminal(self) -> None:
        try:
            session, note = start_shell(
                cwd=str(self.workspace_path), env=self._terminal_env()
            )
        except Exception as exc:
            self.term_display.append(f"[Terminal Initialization Error] {exc}\n")
            return

        self.shell_session = session
        self.term_display.session = session
        kind = type(session).__name__
        label = "ConPTY 가상 콘솔" if "ConPTY" in kind else (
            "PTY 터미널" if "Pty" in kind else "파이프 셸"
        )
        self.term_display.append(f"[RuderAI Studio] {label}로 셸을 시작했습니다. ({kind})\n")
        if note:
            self.term_display.append(f"[안내] {note}\n")
        self.term_display.focus_set()

        if session.fileno() is None:
            # Windows (ConPTY or pipes): no fd to select() on, so a worker
            # thread drains into a queue the UI consumes on a timer.
            self._pipe_reader = PipeReaderThread(session, self._term_queue)
            self._pipe_reader.start()
        else:
            # POSIX PTY: select() from the UI thread avoids a thread entirely.
            self.after(30, self._poll_pty)

    def _drain_terminal(self) -> None:
        while True:
            try:
                data = self._term_queue.get_nowait()
            except queue.Empty:
                break
            if not data:
                self.term_display.append("\n[셸이 종료되었습니다.]\n")
                return
            self._append_terminal_text(data.decode("utf-8", errors="replace"))
        self.after(30, self._drain_terminal)

    def _poll_pty(self) -> None:
        if self.shell_session is None or self.shell_session._closed:
            return
        try:
            import select

            ready, _, _ = select.select([self.shell_session.fileno()], [], [], 0)
            if ready:
                self._append_terminal_text(
                    self.shell_session.pump().decode("utf-8", errors="replace")
                )
        except Exception:
            return
        self.after(30, self._poll_pty)

    def _append_terminal_text(self, text: str) -> None:
        """Apply a minimal line editor to raw shell output.

        Escape stripping lives here, at the single choke point every backend
        funnels through, so no caller can accidentally render raw CSI/OSC
        sequences into the terminal.
        """
        if not text:
            return
        text = strip_ansi(text)
        if not text:
            return
        widget = self.term_display
        widget.configure(state="normal")
        index = "end-1c"

        for char in text:
            if char in ("\x08", "\x7f"):
                try:
                    widget.delete(index + "-1c")
                except tk.TclError:
                    pass
            elif char == "\r":
                widget.mark_set("insert", "end-1c linestart")
            elif char in ("\x07", "\x00"):
                continue
            else:
                widget.insert(index, char)
                index = f"{index}+1c"

        widget.mark_set("insert", "end-1c")
        widget.see("end")
        widget.configure(state="disabled")

    _CTRL_MAP = {
        "c": "\x03", "d": "\x04", "z": "\x1a", "l": "\x0c",
    }
    _SPECIAL_MAP = {
        "Return": "\r", "KP_Enter": "\r", "BackSpace": "\x7f", "Tab": "\t",
        "Escape": "\x1b", "Up": "\x1b[A", "Down": "\x1b[B",
        "Right": "\x1b[C", "Left": "\x1b[D",
    }

    def _on_terminal_key(self, event) -> str | None:
        if self.shell_session is None or self.shell_session._closed:
            return None

        keysym = getattr(event, "keysym", "") or ""
        state = getattr(event, "state", 0)
        ctrl = bool(state & 0x4)  # ControlMask

        if ctrl and keysym.lower() in self._CTRL_MAP:
            self.shell_session.send(self._CTRL_MAP[keysym.lower()].encode())
            return "break"
        if ctrl and keysym.lower() == "v":
            try:
                clipboard = self.clipboard_get()
            except tk.TclError:
                clipboard = ""
            if clipboard:
                self.shell_session.send(clipboard.encode("utf-8"))
            return "break"
        if keysym in self._SPECIAL_MAP:
            self.shell_session.send(self._SPECIAL_MAP[keysym].encode())
            return "break"
        if keysym and len(keysym) == 1:
            self.shell_session.send(keysym.encode("utf-8"))
            return "break"
        return None

    # ------------------------------------------------------------------
    # File tree (lazy)
    # ------------------------------------------------------------------
    def _visible_children(self, path: Path) -> list[Path]:
        try:
            entries = list(path.iterdir())
        except (PermissionError, OSError):
            return []
        keep = []
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            if entry.is_dir() and name in HIDDEN_DIRS:
                continue
            keep.append(entry)
        keep.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
        return keep

    def _load_file_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._tree_items.clear()
        root_label = f"📂 {self.workspace_path.name}"
        root_item = self.tree.insert("", "end", text=root_label, open=True,
                                     values=(str(self.workspace_path),))
        self._tree_items[str(self.workspace_path)] = root_item
        self._populate(root_item, self.workspace_path)
        self.title_label.configure(text=f"RuderAI Studio - [{self.workspace_path.name}]")

    def _populate(self, parent_item: str, path: Path) -> None:
        self._suspend_tree_events = True
        try:
            for child in self._visible_children(path):
                is_dir = child.is_dir()
                icon = "📁" if is_dir else "📄"
                item = self.tree.insert(
                    parent_item, "end", text=f"{icon} {child.name}", values=(str(child),)
                )
                self._tree_items[str(child)] = item
                if is_dir:
                    # A placeholder makes the row expandable without paying for
                    # a recursive walk of everything underneath it.
                    if self._visible_children(child):
                        self.tree.insert(item, "end", text="…")
        finally:
            self._suspend_tree_events = False

    def _on_tree_open(self, event) -> None:
        # Prefer the selection: a programmatic open (or an arrow-key expansion)
        # can move focus without moving the selection, and the selection is what
        # the user actually aimed at.
        selection = self.tree.selection()
        item = selection[0] if selection else self.tree.focus()
        if not item:
            return
        values = self.tree.item(item, "values")
        if not values:
            return
        path = Path(values[0])
        if not path.is_dir():
            return
        children = self.tree.get_children(item)
        if len(children) == 1 and self.tree.item(children[0], "text") == "…":
            self.tree.delete(children[0])
            self._populate(item, path)

    def _on_tree_select(self, _event=None) -> None:
        return None

    def _on_tree_context_menu(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)

        menu = tk.Menu(self, tearoff=0, background="#252526", foreground="#cccccc",
                       activebackground="#04395e", activeforeground="#ffffff")
        menu.add_command(label="📄 새 파일 생성", command=lambda: self._create_new_item(False))
        menu.add_command(label="📁 새 폴더 생성", command=lambda: self._create_new_item(True))
        menu.add_separator()
        menu.add_command(label="📦 이동", command=self._move_selected_item)
        menu.add_command(label="🗑️ 삭제", command=self._delete_selected_item)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _selected_path(self) -> Path | None:
        selection = self.tree.selection()
        if not selection:
            return None
        values = self.tree.item(selection[0], "values")
        if not values:
            return None
        return Path(values[0])

    def _target_dir_from_selection(self) -> Path:
        path = self._selected_path()
        if path is None:
            return self.workspace_path
        return path if path.is_dir() else path.parent

    def _on_file_double_click(self, event=None) -> None:
        path = self._selected_path()
        if path is not None and path.is_file():
            self._open_file_in_editor(path)

    # ------------------------------------------------------------------
    # Editor tabs
    # ------------------------------------------------------------------
    def _open_file_in_editor(self, file_path: Path) -> None:
        key = str(file_path.resolve())

        if key in self.opened_files:
            self.editor_tabs.select(self.opened_files[key])
            return

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            messagebox.showerror("Error", f"파일을 읽을 수 없습니다:\n{exc}")
            return

        text = tk.Text(
            self.editor_tabs,
            wrap="none",
            bg=DARK["bg"],
            fg="#d4d4d4",
            insertbackground=DARK["bright"],
            font=MONO,
            undo=True,
            relief="flat",
            highlightthickness=0,
        )
        _install_editor_tags(text)
        text.insert("1.0", content)
        text.edit_modified(False)
        text.configure(background=DARK["bg"], foreground="#d4d4d4")

        text._file_path = file_path        # type: ignore[attr-defined]
        text._tab_label = f"📄 {file_path.name}"  # type: ignore[attr-defined]
        self.editor_tabs.add(text, text=text._tab_label)  # type: ignore[attr-defined]
        self.opened_files[key] = text
        text.bind("<<Modified>>", lambda _e, w=text: self._on_text_modified(w))
        text.bind("<KeyRelease>", lambda _e, w=text: self._on_editor_key(w))
        text.bind("<Control-f>", lambda _e: self._open_find())
        text.bind("<Control-h>", lambda _e: self._open_find(focus_replace=True))
        text.bind("<F3>", lambda _e: self._repeat_find())
        text.bind("<Shift-F3>", lambda _e: self._repeat_find(backward=True))

        highlighter = Highlighter(text, detect_language(file_path))
        text._highlighter = highlighter     # type: ignore[attr-defined]
        self.highlighters[key] = highlighter
        highlighter.schedule()

    def _on_text_modified(self, widget: tk.Text) -> None:
        if not widget.edit_modified():
            return
        widget.edit_modified(False)
        highlighter = getattr(widget, "_highlighter", None)
        if highlighter is not None:
            highlighter.schedule()
        path = getattr(widget, "_file_path", None)
        if path is None:
            return
        try:
            on_disk = Path(path).read_text(encoding="utf-8", errors="replace")
            dirty = widget.get("1.0", "end-1c") != on_disk
        except OSError:
            dirty = True
        label = getattr(widget, "_tab_label", "")
        self.editor_tabs.tab(widget, text=f"● {label}" if dirty else label)

    def _on_tab_closed(self, _event=None) -> None:
        # ttk.Notebook destroys the widget; just drop our stale bookkeeping.
        for key, widget in list(self.opened_files.items()):
            try:
                if not widget.winfo_exists():
                    highlighter = self.highlighters.pop(key, None)
                    if highlighter is not None:
                        highlighter.destroy()
                    del self.opened_files[key]
            except tk.TclError:
                highlighter = self.highlighters.pop(key, None)
                if highlighter is not None:
                    highlighter.destroy()
                del self.opened_files[key]

    def _current_editor(self) -> tk.Text | None:
        widget = self.editor_tabs.nametowidget(self.editor_tabs.select())
        return widget if isinstance(widget, tk.Text) else None

    def _is_dirty(self, widget: tk.Text) -> bool:
        path = getattr(widget, "_file_path", None)
        if path is None:
            return False
        try:
            on_disk = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return True
        return widget.get("1.0", "end-1c") != on_disk

    def _confirm_discard(self, widget: tk.Text) -> bool:
        """Return True when it is safe to drop *widget*'s contents."""
        if not self._is_dirty(widget):
            return True

        path = getattr(widget, "_file_path", None)
        name = Path(path).name if path else self.editor_tabs.tab(self.editor_tabs.index("current"))
        choice = messagebox.askyesnocancel(
            "저장하지 않은 변경사항",
            f"'{name}'에 저장하지 않은 변경사항이 있습니다.\n\n저장한 뒤 닫으시겠습니까?",
        )
        if choice is None:
            return False
        if choice:
            return self._save_editor_widget(widget)
        return True

    def _save_editor_widget(self, widget: tk.Text) -> bool:
        path = getattr(widget, "_file_path", None)
        if path is None:
            return True
        try:
            Path(path).write_text(widget.get("1.0", "end-1c"), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("에러", f"파일 저장 실패:\n{exc}")
            return False
        label = getattr(widget, "_tab_label", "")
        try:
            self.editor_tabs.tab(widget, text=label)
        except tk.TclError:
            pass
        return True

    def _save_current_editor_file(self) -> None:
        widget = self._current_editor()
        if widget is None:
            messagebox.showwarning("경고", "저장할 파일 탭이 선택되어 있지 않습니다.")
            return
        if getattr(widget, "_file_path", None) is None:
            messagebox.showwarning("경고", "저장할 수 없는 임시 탭입니다.")
            return
        if self._save_editor_widget(widget):
            messagebox.showinfo("성공", f"'{Path(widget._file_path).name}' 파일이 저장되었습니다.")

    # ------------------------------------------------------------------
    # File / folder management
    # ------------------------------------------------------------------
    def _create_new_item(self, is_folder: bool) -> None:
        target_dir = self._target_dir_from_selection()
        label = "폴더" if is_folder else "파일"
        name = simpledialog.askstring(
            f"새 {label} 생성",
            f"위치: [{target_dir.name}]\n새 {label} 이름을 입력하세요:",
            parent=self,
        )
        if not name or not name.strip():
            return

        new_path = target_dir / name.strip()
        if new_path.exists():
            messagebox.showwarning("경고", f"이미 존재하는 {label} 이름입니다.")
            return
        try:
            if is_folder:
                new_path.mkdir(parents=True, exist_ok=True)
            else:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                new_path.touch()
        except OSError as exc:
            messagebox.showerror("에러", f"{label} 생성 실패:\n{exc}")
            return

        self._load_file_tree()
        if not is_folder:
            self._open_file_in_editor(new_path)

    def _delete_selected_item(self) -> None:
        target = self._selected_path()
        if target is None or target == self.workspace_path:
            messagebox.showwarning("경고", "최상위 워크스페이스 폴더는 삭제할 수 없습니다.")
            return
        if not messagebox.askyesno("삭제 확인", f"정말로 '{target.name}' 항목을 삭제하시겠습니까?"):
            return
        try:
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        except OSError as exc:
            messagebox.showerror("에러", f"삭제 실패:\n{exc}")
            return
        self._load_file_tree()

    def _move_selected_item(self) -> None:
        """tkinter has no drag-and-drop, so moving is an explicit action."""
        source = self._selected_path()
        if source is None or source == self.workspace_path:
            messagebox.showwarning("경고", "최상위 워크스페이스 폴더는 이동할 수 없습니다.")
            return

        raw = simpledialog.askstring(
            "항목 이동",
            f"'{source.name}'을(를) 이동할 폴더 경로 입력:\n"
            f"(현재 위치 기준, 빈 줄이면 워크스페이스 루트)",
            initialvalue=str(source.parent),
            parent=self,
        )
        if raw is None:
            return
        dest_dir = Path(raw.strip()).expanduser()
        if not dest_dir.is_absolute():
            dest_dir = (source.parent / dest_dir).resolve()
        if not dest_dir.is_dir():
            messagebox.showerror("에러", f"존재하지 않는 폴더입니다:\n{dest_dir}")
            return
        if dest_dir == source.parent:
            return
        # Moving a directory into its own subtree would detach the tree from
        # its root and can recurse forever.
        try:
            if source.is_dir() and dest_dir in source.parents:
                messagebox.showerror("에러", "자기 자신 안으로 폴더를 이동할 수 없습니다.")
                return
        except OSError:
            pass

        dest = dest_dir / source.name
        if dest.exists():
            if not messagebox.askyesno("대상 존재", f"'{dest}'이(가) 이미 존재합니다. 덮어쓸까요?"):
                return
            try:
                shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
            except OSError as exc:
                messagebox.showerror("에러", f"기존 대상 제거 실패:\n{exc}")
                return

        try:
            shutil.move(str(source), str(dest))
        except OSError as exc:
            messagebox.showerror("이동 실패", f"항목을 이동할 수 없습니다:\n{exc}")
            return
        self._load_file_tree()

    def _open_env_dialog(self) -> None:
        EnvDialog(self, self.config_mgr)

    # --- Find / replace ---------------------------------------------
    def _open_find(self, *, focus_replace: bool = False) -> None:
        widget = self._current_editor()
        if widget is None:
            messagebox.showinfo("찾기", "먼저 파일을 열어주세요.")
            return

        if self.find_dialog is not None and self.find_dialog.winfo_exists():
            dialog = self.find_dialog
            dialog.lift()
        else:
            dialog = FindReplaceDialog(self, self)
            self.find_dialog = dialog

        # Seed the needle from the current selection, the way every editor does.
        try:
            seed = widget.get("sel.first", "sel.last")
        except tk.TclError:
            seed = ""
        if seed and not seed.isspace() and "\n" not in seed:
            dialog.find_var.set(seed)
        else:
            dialog.refresh()

        if focus_replace:
            dialog.replace_entry.focus_set()
            dialog.replace_entry.selection_range(0, "end")
        else:
            dialog.find_entry.focus_set()
            dialog.find_entry.selection_range(0, "end")

    def _on_editor_key(self, widget: tk.Text) -> None:
        """Re-highlight after a keystroke. Search shortcuts are bound directly."""
        highlighter = getattr(widget, "_highlighter", None)
        if highlighter is not None:
            highlighter.schedule()

    def _repeat_find(self, *, backward: bool = False) -> None:
        if self.find_dialog is not None and self.find_dialog.winfo_exists():
            self.find_dialog.step(-1 if backward else 1)

    def _bind_shortcuts(self) -> None:
        """Global fallbacks so the shortcuts work from any focused widget."""
        self.bind_all("<Control-f>", lambda _e: self._open_find())
        self.bind_all("<Control-h>", lambda _e: self._open_find(focus_replace=True))
        self.bind_all("<F3>", lambda _e: self._repeat_find())
        self.bind_all("<Shift-F3>", lambda _e: self._repeat_find(backward=True))
        self.bind_all("<Escape>", lambda _e: self._escape())

    def _change_workspace(self) -> None:
        selected = filedialog.askdirectory(initialdir=str(self.workspace_path), parent=self)
        if not selected:
            return

        self.workspace_path = Path(selected).resolve()
        self.config_mgr.set("workspace_dir", str(self.workspace_path))
        if self._create_agent() is None:
            self._chat(
                f"[System]\n⚠️ 새 작업 공간에 대한 AI 에이전트 생성에 실패했습니다:\n{self.agent_error}\n\n"
            )
        self._load_file_tree()
        if self.shell_session is not None:
            self.shell_session.send(f"cd '{self.workspace_path}'\n".encode("utf-8"))

    def _escape(self) -> str:
        """Escape closes the find panel, or cancels a selection."""
        if self.find_dialog is not None and self.find_dialog.winfo_exists():
            self.find_dialog.close()
            self.find_dialog = None
            return "break"
        widget = self._current_editor()
        if widget is not None:
            try:
                if widget.tag_ranges("sel"):
                    widget.tag_remove("sel", "1.0", "end")
                    return "break"
            except tk.TclError:
                pass
        return ""

    def on_close(self) -> None:
        for index in range(self.editor_tabs.index("end")):
            widget = self.editor_tabs.nametowidget(self.editor_tabs.tabs()[index])
            if isinstance(widget, tk.Text) and not self._confirm_discard(widget):
                return

        if self.find_dialog is not None and self.find_dialog.winfo_exists():
            self.find_dialog.destroy()
            self.find_dialog = None
        for highlighter in self.highlighters.values():
            highlighter.destroy()
        self.highlighters.clear()
        if self.shell_session is not None:
            try:
                # close() signals EOF; give the reader thread a moment to notice
                # before its window handle goes away.
                self.shell_session.close()
                self.shell_session.reader_finished.wait(1.0)
            except Exception:
                pass
            self.shell_session = None
        self.destroy()


def launch_vscode_gui() -> int:
    """Open the Studio window. Raises on a headless/unsupported system."""
    app = RuderAIVSCodeApp()
    app.mainloop()
    return 0
