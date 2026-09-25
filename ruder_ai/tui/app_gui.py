"""VS Code / Code-OSS Style GUI Desktop App for RuderAI (PyQt6).
Includes Multi-tab Editor, Drag & Drop Explorer, and Interactive PTY Terminal.
"""
import sys
import os
import re
import struct
import subprocess
import asyncio
import traceback
import shutil
from pathlib import Path

# POSIX 전용 모듈 안전 체크 (Windows 지원 대비)
IS_POSIX = os.name == 'posix'
if IS_POSIX:
    import pty
    import fcntl
    import termios

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSocketNotifier
from PyQt6.QtGui import QTextCursor, QKeyEvent, QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTreeWidget, QTreeWidgetItem, QTextEdit, QLineEdit,
    QPushButton, QLabel, QTabWidget, QDialog, QPlainTextEdit, QMessageBox,
    QInputDialog, QMenu, QAbstractItemView, QFileDialog
)

from ruder_ai.core.agent import AIAgent
from ruder_ai.core.config import ConfigManager


# --- VS Code Dark Modern Style Sheet ---
QSS_STYLE = """
QMainWindow, QWidget {
    background-color: #1e1e1e;
    color: #cccccc;
    font-family: 'Segoe UI', 'Consolas', sans-serif;
    font-size: 13px;
}
QSplitter::handle {
    background-color: #2b2b2b;
}
QTreeWidget {
    background-color: #252526;
    border: none;
    color: #cccccc;
}
QTreeWidget::item:selected {
    background-color: #37373d;
    color: #ffffff;
}
QTextEdit, QPlainTextEdit, QLineEdit {
    background-color: #1e1e1e;
    color: #d4d4d4;
    border: 1px solid #2b2b2b;
    border-radius: 2px;
    padding: 4px;
    font-family: 'Consolas', monospace;
}
QLineEdit:focus, QTextEdit:focus {
    border: 1px solid #007acc;
}
QPushButton {
    background-color: #3c3c3c;
    color: #ffffff;
    border: none;
    padding: 5px 10px;
    border-radius: 2px;
}
QPushButton:hover {
    background-color: #4a4a4a;
}
QPushButton#accentBtn {
    background-color: #007acc;
}
QPushButton#accentBtn:hover {
    background-color: #0098ff;
}
QTabWidget::pane {
    border: none;
    background-color: #181818;
}
QTabBar::tab {
    background-color: #2d2d2d;
    color: #858585;
    padding: 6px 14px;
    border: none;
}
QTabBar::tab:selected {
    background-color: #181818;
    color: #ffffff;
    border-top: 2px solid #007acc;
}
QMenu {
    background-color: #252526;
    color: #cccccc;
    border: 1px solid #3c3c3c;
}
QMenu::item:selected {
    background-color: #04395e;
    color: #ffffff;
}
"""


class AIWorker(QThread):
    finished = pyqtSignal(str)

    def __init__(self, agent, prompt):
        super().__init__()
        self.agent = agent
        self.prompt = prompt

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            response = loop.run_until_complete(self.agent.process_task(self.prompt))
            loop.close()
            self.finished.emit(str(response) if response else "응답이 비어있습니다.")
        except Exception:
            self.finished.emit(f"⚠️ 에러 발생:\n{traceback.format_exc()}")


class EnvDialog(QDialog):
    """환경변수 추가/수정 팝업 대화상자"""
    def __init__(self, parent, config_mgr):
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.setWindowTitle("환경변수 설정")
        self.resize(480, 360)

        layout = QVBoxLayout(self)

        lbl = QLabel("환경변수 설정 (형식: KEY=VALUE / 한 줄에 하나씩)")
        lbl.setStyleSheet("font-weight: bold; color: #ffffff;")
        layout.addWidget(lbl)

        self.text_edit = QPlainTextEdit()
        curr_envs = self.config_mgr.get("env_vars", {})
        init_str = "\n".join([f"{k}={v}" for k, v in curr_envs.items()])
        self.text_edit.setPlainText(init_str)
        layout.addWidget(self.text_edit)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(cancel_btn)

        save_btn = QPushButton("💾 저장")
        save_btn.setObjectName("accentBtn")
        save_btn.clicked.connect(self.save_envs)
        btn_box.addWidget(save_btn)

        layout.addLayout(btn_box)

    def save_envs(self):
        raw_lines = self.text_edit.toPlainText().strip().splitlines()
        new_envs = {}
        for line in raw_lines:
            if "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k:
                    new_envs[k] = v
                    os.environ[k] = v

        self.config_mgr.set("env_vars", new_envs)
        QMessageBox.information(self, "성공", "환경변수가 성공적으로 저장되었습니다.")
        self.accept()


class DragDropFileTree(QTreeWidget):
    """드래그 앤 드롭으로 파일/폴더 이동을 지원하는 커스텀 트리"""
    def __init__(self, main_app, parent=None):
        super().__init__(parent)
        self.main_app = main_app
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

    def dropEvent(self, event):
        target_item = self.itemAt(event.position().toPoint())
        selected_items = self.selectedItems()
        if not selected_items:
            return

        src_item = selected_items[0]
        src_path_str = src_item.data(0, Qt.ItemDataRole.UserRole)
        if not src_path_str:
            return

        src_path = Path(src_path_str)

        if target_item:
            target_path_str = target_item.data(0, Qt.ItemDataRole.UserRole)
            target_path = Path(target_path_str)
            if target_path.is_file():
                dest_dir = target_path.parent
            else:
                dest_dir = target_path
        else:
            dest_dir = self.main_app.workspace_path

        if src_path.parent != dest_dir:
            dest_path = dest_dir / src_path.name
            try:
                shutil.move(str(src_path), str(dest_path))
                self.main_app._load_file_tree()
            except Exception as e:
                QMessageBox.critical(self.main_app, "이동 실패", f"항목을 이동할 수 없습니다:\n{e}")

        event.accept()


class InteractiveTerminalEdit(QTextEdit):
    """키 입력을 감지하여 PTY Master FD로 직접 전송하는 대화형 터미널 에디터"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_fd = None
        self.slave_fd = None
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setStyleSheet("background-color: #0e0e0e; color: #00ff66; font-family: 'Consolas', monospace; font-size: 13px;")

    def set_fds(self, master_fd, slave_fd=None):
        self.master_fd = master_fd
        self.slave_fd = slave_fd

    def inputMethodEvent(self, event):
        """Arch/Hyprland Wayland 한글 IME 조합 완료 문자를 PTY로 전송"""
        if self.master_fd is not None and event.commitString():
            os.write(self.master_fd, event.commitString().encode("utf-8"))
        super().inputMethodEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if IS_POSIX and self.slave_fd is not None:
            font_metrics = self.fontMetrics()
            char_width = max(1, font_metrics.horizontalAdvance("W"))
            char_height = max(1, font_metrics.height())

            cols = max(10, self.viewport().width() // char_width)
            rows = max(5, self.viewport().height() // char_height)

            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.slave_fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass

    def keyPressEvent(self, event: QKeyEvent):
        if self.master_fd is None:
            super().keyPressEvent(event)
            return

        text = event.text()
        key = event.key()
        modifiers = event.modifiers()

        # Control 조합 키
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            ctrl_map = {
                Qt.Key.Key_C: b"\x03",
                Qt.Key.Key_D: b"\x04",
                Qt.Key.Key_Z: b"\x1a",
                Qt.Key.Key_L: b"\x0c",
            }
            if key in ctrl_map:
                os.write(self.master_fd, ctrl_map[key])
                return
            elif key == Qt.Key.Key_V:
                clipboard = QApplication.clipboard().text()
                if clipboard:
                    os.write(self.master_fd, clipboard.encode("utf-8"))
                return

        # 특수 제어키 처리
        key_map = {
            Qt.Key.Key_Return: b"\r",
            Qt.Key.Key_Enter: b"\r",
            Qt.Key.Key_Backspace: b"\x7f",
            Qt.Key.Key_Tab: b"\t",
            Qt.Key.Key_Escape: b"\x1b",
            Qt.Key.Key_Up: b"\x1b[A",
            Qt.Key.Key_Down: b"\x1b[B",
            Qt.Key.Key_Right: b"\x1b[C",
            Qt.Key.Key_Left: b"\x1b[D",
        }
        if key in key_map:
            os.write(self.master_fd, key_map[key])
            return

        # 일반 문자 및 IME 조합 전 문자열 전송
        if text and not event.key() == Qt.Key.Key_unknown:
            os.write(self.master_fd, text.encode("utf-8"))


class RuderAIVSCodeApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RuderAI Studio - Code OSS (PyQt6)")
        self.resize(1280, 800)

        self.config_mgr = ConfigManager()
        self.workspace_path = Path(self.config_mgr.get("workspace_dir", Path.cwd())).resolve()
        self.opened_files = {}

        self._apply_custom_env()

        try:
            self.agent = AIAgent(
                model_name=self.config_mgr.get("model_name"),
                workspace_path=str(self.workspace_path)
            )
        except Exception as e:
            self.agent = None
            print(f"[Agent Init Error] {e}")

        self.master_fd = None
        self.slave_fd = None
        self.shell_proc = None

        self._build_ui()
        self._load_file_tree()
        self._start_pty_terminal()

    def _apply_custom_env(self):
        custom_env = self.config_mgr.get("env_vars", {})
        if isinstance(custom_env, dict):
            for k, v in custom_env.items():
                os.environ[str(k)] = str(v)

    def _build_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Top Bar
        top_bar = QWidget()
        top_bar.setFixedHeight(38)
        top_bar.setStyleSheet("background-color: #333333;")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 0, 12, 0)

        self.title_label = QLabel(f"RuderAI Studio - [{self.workspace_path.name}]")
        self.title_label.setStyleSheet("font-weight: bold; color: #ffffff;")
        top_layout.addWidget(self.title_label)
        top_layout.addStretch()

        env_btn = QPushButton("⚙️ 환경변수")
        env_btn.clicked.connect(self._open_env_dialog)
        top_layout.addWidget(env_btn)

        folder_btn = QPushButton("📁 폴더 열기")
        folder_btn.clicked.connect(self._change_workspace)
        top_layout.addWidget(folder_btn)

        main_layout.addWidget(top_bar)

        # 2. Main Splitter
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(main_splitter, 1)

        # Left Explorer Panel
        sidebar_widget = QWidget()
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        explorer_bar = QWidget()
        explorer_bar.setFixedHeight(28)
        explorer_bar.setStyleSheet("background-color: #252526;")
        eb_layout = QHBoxLayout(explorer_bar)
        eb_layout.setContentsMargins(8, 0, 4, 0)

        explorer_lbl = QLabel("EXPLORER")
        explorer_lbl.setStyleSheet("color: #858585; font-weight: bold;")
        eb_layout.addWidget(explorer_lbl)
        eb_layout.addStretch()

        new_file_btn = QPushButton("📄+")
        new_file_btn.setToolTip("새 파일 생성")
        new_file_btn.setFixedSize(26, 20)
        new_file_btn.clicked.connect(lambda: self._create_new_item(is_folder=False))
        eb_layout.addWidget(new_file_btn)

        new_folder_btn = QPushButton("📁+")
        new_folder_btn.setToolTip("새 폴더 생성")
        new_folder_btn.setFixedSize(26, 20)
        new_folder_btn.clicked.connect(lambda: self._create_new_item(is_folder=True))
        eb_layout.addWidget(new_folder_btn)

        refresh_btn = QPushButton("🔄")
        refresh_btn.setToolTip("새로고침")
        refresh_btn.setFixedSize(26, 20)
        refresh_btn.clicked.connect(self._load_file_tree)
        eb_layout.addWidget(refresh_btn)

        sidebar_layout.addWidget(explorer_bar)

        self.tree = DragDropFileTree(self)
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_file_double_click)
        sidebar_layout.addWidget(self.tree)

        main_splitter.addWidget(sidebar_widget)

        # Right Splitter
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.addWidget(right_splitter)
        main_splitter.setSizes([220, 1060])

        # Editor Container
        editor_container = QWidget()
        editor_layout = QVBoxLayout(editor_container)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)

        editor_top_bar = QWidget()
        editor_top_bar.setFixedHeight(28)
        editor_top_bar.setStyleSheet("background-color: #2d2d2d;")
        etb_layout = QHBoxLayout(editor_top_bar)
        etb_layout.setContentsMargins(10, 0, 10, 0)

        etb_layout.addStretch()
        save_file_btn = QPushButton("💾 현재 파일 저장")
        save_file_btn.setObjectName("accentBtn")
        save_file_btn.clicked.connect(self._save_current_editor_file)
        etb_layout.addWidget(save_file_btn)

        editor_layout.addWidget(editor_top_bar)

        self.editor_tabs = QTabWidget()
        self.editor_tabs.setTabsClosable(True)
        self.editor_tabs.tabCloseRequested.connect(self._close_editor_tab)
        editor_layout.addWidget(self.editor_tabs)

        welcome_edit = QTextEdit()
        welcome_edit.setText(f"// Workspace: {self.workspace_path}\n// 파일 트리를 더블 클릭하여 여러 파일을 탭으로 열어보세요.")
        self.editor_tabs.addTab(welcome_edit, "welcome.txt")

        right_splitter.addWidget(editor_container)

        # Bottom Notebook Panel
        self.notebook = QTabWidget()
        right_splitter.addWidget(self.notebook)
        right_splitter.setSizes([500, 300])

        # Tab A: AI Agent
        ai_tab = QWidget()
        ai_layout = QVBoxLayout(ai_tab)
        ai_layout.setContentsMargins(8, 8, 8, 8)

        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setText("🤖 RuderAI AI 준비 완료. 질문을 입력하세요.\n" + "="*50 + "\n\n")
        ai_layout.addWidget(self.chat_display)

        ai_input_box = QHBoxLayout()
        self.prompt_entry = QLineEdit()
        self.prompt_entry.setPlaceholderText("AI에게 명령을 입력하세요 (한글 작성 지원)...")
        self.prompt_entry.returnPressed.connect(self._send_prompt)
        ai_input_box.addWidget(self.prompt_entry)

        send_btn = QPushButton("전송")
        send_btn.setObjectName("accentBtn")
        send_btn.clicked.connect(self._send_prompt)
        ai_input_box.addWidget(send_btn)

        ai_layout.addLayout(ai_input_box)
        self.notebook.addTab(ai_tab, "🤖 RuderAI AI")

        # Tab B: Interactive Terminal
        term_tab = QWidget()
        term_layout = QVBoxLayout(term_tab)
        term_layout.setContentsMargins(4, 4, 4, 4)

        self.term_display = InteractiveTerminalEdit()
        term_layout.addWidget(self.term_display)

        self.notebook.addTab(term_tab, "🖥️ Terminal")

    # --- Actions & Env ---
    def _open_env_dialog(self):
        dialog = EnvDialog(self, self.config_mgr)
        dialog.exec()

    def _send_prompt(self):
        prompt = self.prompt_entry.text().strip()
        if not prompt:
            return

        self.prompt_entry.clear()
        self.chat_display.append(f"[User]\n{prompt}\n")
        self.chat_display.append("[RuderAI AI]\n생각 중...\n")

        if self.agent is None:
            self.agent = AIAgent(
                model_name=self.config_mgr.get("model_name"),
                workspace_path=str(self.workspace_path)
            )

        self.worker = AIWorker(self.agent, prompt)
        self.worker.finished.connect(self._update_ai_response)
        self.worker.start()

    def _update_ai_response(self, text):
        self.chat_display.append(f"{text}\n\n" + "-"*50 + "\n")
        self._load_file_tree()

    # --- PTY Terminal (Real Interactive TTY Mode) ---
    def _start_pty_terminal(self):
        if not IS_POSIX:
            self.term_display.append("[Notice] PTY 터미널은 POSIX 환경(Linux/macOS)에서 지원됩니다.")
            return

        try:
            self.master_fd, self.slave_fd = pty.openpty()
            self.term_display.set_fds(self.master_fd, self.slave_fd)

            winsize = struct.pack("HHHH", 24, 80, 0, 0)
            fcntl.ioctl(self.slave_fd, termios.TIOCSWINSZ, winsize)

            shell = os.environ.get("SHELL", "/bin/zsh")
            if not os.path.exists(shell):
                shell = "/bin/bash"

            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["STARSHIP_SESSION_KEY"] = ""
            env["STARSHIP_SHELL"] = ""
            env["POWERLEVEL9K_MODE"] = "off"
            env["PROMPT"] = "%~ %# "

            self.shell_proc = subprocess.Popen(
                [shell],
                stdin=self.slave_fd,
                stdout=self.slave_fd,
                stderr=self.slave_fd,
                cwd=str(self.workspace_path),
                env=env,
                preexec_fn=os.setsid
            )

            self.notifier = QSocketNotifier(self.master_fd, QSocketNotifier.Type.Read, self)
            self.notifier.activated.connect(self._read_pty_output)
        except Exception as e:
            self.term_display.append(f"[Terminal Initialization Error] {e}")

    def _read_pty_output(self):
        ansi_cleaner = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        try:
            data = os.read(self.master_fd, 4096).decode("utf-8", errors="replace")
            if data:
                clean_text = ansi_cleaner.sub('', data)

                cursor = self.term_display.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)

                for char in clean_text:
                    if char in ('\x08', '\x7f'):
                        cursor.deletePreviousChar()
                    elif char == '\r':
                        cursor.movePosition(QTextCursor.MoveOperation.StartOfLine, QTextCursor.MoveMode.KeepAnchor)
                    else:
                        cursor.insertText(char)

                self.term_display.setTextCursor(cursor)
                self.term_display.ensureCursorVisible()
        except Exception:
            pass

    # --- Multiple File Tabs ---
    def _open_file_in_editor(self, file_path: Path):
        file_key = str(file_path.resolve())

        if file_key in self.opened_files:
            text_edit = self.opened_files[file_key]
            index = self.editor_tabs.indexOf(text_edit)
            self.editor_tabs.setCurrentIndex(index)
            return

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            new_edit = QTextEdit()
            new_edit.setText(content)

            tab_index = self.editor_tabs.addTab(new_edit, f"📄 {file_path.name}")
            self.editor_tabs.setCurrentIndex(tab_index)

            self.opened_files[file_key] = new_edit
            new_edit.setProperty("file_path", file_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"파일을 읽을 수 없습니다: {e}")

    def _close_editor_tab(self, index):
        widget = self.editor_tabs.widget(index)
        if widget:
            file_path = widget.property("file_path")
            if file_path:
                file_key = str(file_path.resolve())
                if file_key in self.opened_files:
                    del self.opened_files[file_key]
            self.editor_tabs.removeTab(index)

    def _save_current_editor_file(self):
        current_widget = self.editor_tabs.currentWidget()
        if not current_widget or not isinstance(current_widget, QTextEdit):
            QMessageBox.warning(self, "경고", "저장할 파일 탭이 선택되어 있지 않습니다.")
            return

        file_path = current_widget.property("file_path")
        if not file_path:
            QMessageBox.warning(self, "경고", "저장할 수 없는 임시 탭입니다.")
            return

        try:
            content = current_widget.toPlainText()
            file_path.write_text(content, encoding="utf-8")
            QMessageBox.information(self, "성공", f"'{file_path.name}' 파일이 저장되었습니다.")
        except Exception as e:
            QMessageBox.critical(self, "에러", f"파일 저장 실패:\n{e}")

    # --- File/Folder Management ---
    def _load_file_tree(self):
        self.tree.clear()
        root_item = QTreeWidgetItem(self.tree, [f"📂 {self.workspace_path.name}"])
        root_item.setData(0, Qt.ItemDataRole.UserRole, str(self.workspace_path))

        def add_nodes(parent_item, path: Path):
            try:
                for p in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    if p.name.startswith(".") or p.name == "__pycache__":
                        continue
                    icon = "📁" if p.is_dir() else "📄"
                    child = QTreeWidgetItem(parent_item, [f"{icon} {p.name}"])
                    child.setData(0, Qt.ItemDataRole.UserRole, str(p))
                    if p.is_dir():
                        add_nodes(child, p)
            except PermissionError:
                pass

        add_nodes(root_item, self.workspace_path)
        self.tree.expandItem(root_item)

    def _get_target_dir_from_selection(self) -> Path:
        selected_items = self.tree.selectedItems()
        if not selected_items:
            return self.workspace_path

        target_path = Path(selected_items[0].data(0, Qt.ItemDataRole.UserRole))
        if target_path.is_file():
            return target_path.parent
        return target_path

    def _create_new_item(self, is_folder: bool = False):
        target_dir = self._get_target_dir_from_selection()
        item_type = "폴더" if is_folder else "파일"

        name, ok = QInputDialog.getText(
            self,
            f"새 {item_type} 생성",
            f"위치: [{target_dir.name}]\n새 {item_type} 이름을 입력하세요:"
        )

        if ok and name.strip():
            new_path = target_dir / name.strip()
            try:
                if new_path.exists():
                    QMessageBox.warning(self, "경고", f"이미 존재하는 {item_type} 이름입니다.")
                    return

                if is_folder:
                    new_path.mkdir(parents=True, exist_ok=True)
                else:
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    new_path.touch()
                    self._open_file_in_editor(new_path)

                self._load_file_tree()
            except Exception as e:
                QMessageBox.critical(self, "에러", f"{item_type} 생성 실패:\n{e}")

    def _delete_selected_item(self):
        selected_items = self.tree.selectedItems()
        if not selected_items:
            return

        target_path = Path(selected_items[0].data(0, Qt.ItemDataRole.UserRole))
        if target_path == self.workspace_path:
            QMessageBox.warning(self, "경고", "최상위 워크스페이스 폴더는 삭제할 수 없습니다.")
            return

        reply = QMessageBox.question(
            self,
            "삭제 확인",
            f"정말로 '{target_path.name}' 항목을 삭제하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                if target_path.is_dir():
                    shutil.rmtree(target_path)
                else:
                    target_path.unlink()

                self._load_file_tree()
            except Exception as e:
                QMessageBox.critical(self, "에러", f"삭제 실패:\n{e}")

    def _show_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)

        action_new_file = QAction("📄 새 파일 생성", self)
        action_new_file.triggered.connect(lambda: self._create_new_item(is_folder=False))
        menu.addAction(action_new_file)

        action_new_folder = QAction("📁 새 폴더 생성", self)
        action_new_folder.triggered.connect(lambda: self._create_new_item(is_folder=True))
        menu.addAction(action_new_folder)

        if item:
            menu.addSeparator()
            action_delete = QAction("🗑️ 삭제", self)
            action_delete.triggered.connect(self._delete_selected_item)
            menu.addAction(action_delete)

        menu.exec(self.tree.mapToGlobal(pos))

    def _on_file_double_click(self, item, column):
        path_str = item.data(0, Qt.ItemDataRole.UserRole)
        if path_str:
            file_path = Path(path_str)
            if file_path.is_file():
                self._open_file_in_editor(file_path)

    def _change_workspace(self):
        selected = QFileDialog.getExistingDirectory(self, "작업 공간 폴더 선택", str(self.workspace_path))
        if selected:
            self.workspace_path = Path(selected).resolve()
            self.config_mgr.set("workspace_dir", str(self.workspace_path))
            self.title_label.setText(f"RuderAI Studio - [{self.workspace_path.name}]")
            self.agent = AIAgent(
                model_name=self.config_mgr.get("model_name"),
                workspace_path=str(self.workspace_path)
            )
            self._load_file_tree()
            if self.master_fd is not None:
                os.write(self.master_fd, f"cd '{self.workspace_path}'\r".encode("utf-8"))

    def closeEvent(self, event):
        """앱 종료 시 PTY FD 및 자식 프로세스 정리"""
        if hasattr(self, 'notifier') and self.notifier:
            self.notifier.setEnabled(False)

        if self.shell_proc:
            try:
                self.shell_proc.terminate()
            except Exception:
                pass

        for fd in (self.master_fd, self.slave_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass

        super().closeEvent(event)


def launch_vscode_gui():

    app = QApplication(sys.argv)
    app.setStyleSheet(QSS_STYLE)
    win = RuderAIVSCodeApp()
    win.show()
    sys.exit(app.exec())
