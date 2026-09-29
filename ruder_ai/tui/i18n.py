"""UI string catalogue for RuderAI Studio and the settings window.

The Studio's chrome was hardcoded Korean, so a user running ``ruder-ai-jp`` or
``ruder-ai-en`` got a Korean window around an English answer. This module holds
the user-facing strings for the four languages the project ships models for and
nothing else: no widgets, no toolkit imports, so it stays testable headless and
usable by the CLI as well as the GUI.

Usage::

    from ruder_ai.tui.i18n import set_language, t

    set_language("ja")
    title_label.configure(text=t("settings.title"))

A missing key falls back to Korean (the original source language) and then to
the key itself, so adding a new string without translating it shows readable
text instead of raising in the middle of a window build.
"""
from __future__ import annotations

from typing import Any

#: Canonical language codes, in the order they appear in the picker.
LANGUAGES: tuple[str, ...] = ("ko", "en", "ja", "zh")

#: Endonyms — a language picker should read in its own language, not in the
#: language of whoever is looking at the screen.
LANGUAGE_NAMES: dict[str, str] = {
    "ko": "한국어",
    "en": "English",
    "ja": "日本語",
    "zh": "简体中文",
}

DEFAULT_LANGUAGE = "ko"

# Each entry is {key: {lang: text}}. Korean is the source language and is also
# the fallback, so a partially translated language degrades to Korean rather
# than to a raw key.
CATALOG: dict[str, dict[str, str]] = {
    # --- settings window -------------------------------------------------
    "settings.window_title": {
        "ko": "RuderAI - 모델 및 시스템 설정",
        "en": "RuderAI - Model & System Config",
        "ja": "RuderAI - モデルとシステム設定",
        "zh": "RuderAI - 模型与系统设置",
    },
    "settings.heading": {
        "ko": "⚙️ RuderAI 설정",
        "en": "⚙️ RuderAI Settings",
        "ja": "⚙️ RuderAI 設定",
        "zh": "⚙️ RuderAI 设置",
    },
    "settings.workspace": {
        "ko": "작업 경로:",
        "en": "Workspace:",
        "ja": "ワークスペース:",
        "zh": "工作区:",
    },
    "settings.browse": {
        "ko": "📁 폴더 선택",
        "en": "📁 Browse",
        "ja": "📁 フォルダ選択",
        "zh": "📁 选择文件夹",
    },
    "settings.url": {
        "ko": "Ollama URL:",
        "en": "Ollama URL:",
        "ja": "Ollama URL:",
        "zh": "Ollama URL:",
    },
    "settings.model": {
        "ko": "모델 이름:",
        "en": "Model Name:",
        "ja": "モデル名:",
        "zh": "模型名称:",
    },
    "settings.refresh": {
        "ko": "🔄 새로고침",
        "en": "🔄 Refresh",
        "ja": "🔄 更新",
        "zh": "🔄 刷新",
    },
    "settings.temperature": {
        "ko": "Temperature:",
        "en": "Temperature:",
        "ja": "Temperature:",
        "zh": "Temperature:",
    },
    "settings.language": {
        "ko": "UI 언어:",
        "en": "UI Language:",
        "ja": "UI 言語:",
        "zh": "界面语言:",
    },
    "settings.save": {
        "ko": "Save Configuration",
        "en": "Save Configuration",
        "ja": "設定を保存",
        "zh": "保存设置",
    },
    "settings.ollama_unreachable": {
        "ko": "Ollama에 연결할 수 없습니다. URL을 확인하세요.",
        "en": "Cannot reach Ollama. Check the URL.",
        "ja": "Ollama に接続できません。URL を確認してください。",
        "zh": "无法连接到 Ollama。请检查 URL。",
    },
    "settings.error": {
        "ko": "오류",
        "en": "Error",
        "ja": "エラー",
        "zh": "错误",
    },
    "settings.success": {
        "ko": "성공",
        "en": "Success",
        "ja": "成功",
        "zh": "成功",
    },
    "settings.err_missing_dir": {
        "ko": "존재하지 않는 디렉토리입니다:",
        "en": "This directory does not exist:",
        "ja": "存在しないディレクトリです:",
        "zh": "该目录不存在:",
    },
    "settings.err_no_model": {
        "ko": "모델 이름을 입력하세요.",
        "en": "Enter a model name.",
        "ja": "モデル名を入力してください。",
        "zh": "请输入模型名称。",
    },
    "settings.err_temp_range": {
        "ko": "Temperature 값은 0.0 ~ 2.0 사이여야 합니다.",
        "en": "Temperature must be between 0.0 and 2.0.",
        "ja": "Temperature は 0.0 ~ 2.0 の間でなければなりません。",
        "zh": "Temperature 必须在 0.0 ~ 2.0 之间。",
    },
    "settings.err_temp_number": {
        "ko": "Temperature 값은 숫자여야 합니다.",
        "en": "Temperature must be a number.",
        "ja": "Temperature は数値でなければなりません。",
        "zh": "Temperature 必须是数字。",
    },
    "settings.saved": {
        "ko": "설정이 저장되었습니다!",
        "en": "Settings saved!",
        "ja": "設定を保存しました!",
        "zh": "设置已保存!",
    },
    "settings.saved_workspace": {
        "ko": "작업 경로:",
        "en": "Workspace:",
        "ja": "ワークスペース:",
        "zh": "工作区:",
    },
    # --- Studio chrome ---------------------------------------------------
    "studio.window_title": {
        "ko": "RuderAI Studio",
        "en": "RuderAI Studio",
        "ja": "RuderAI Studio",
        "zh": "RuderAI Studio",
    },
    "studio.env": {
        "ko": "⚙️ 환경변수",
        "en": "⚙️ Environment",
        "ja": "⚙️ 環境変数",
        "zh": "⚙️ 环境变量",
    },
    "studio.open_folder": {
        "ko": "📁 폴더 열기",
        "en": "📁 Open Folder",
        "ja": "📁 フォルダを開く",
        "zh": "📁 打开文件夹",
    },
    "studio.explorer": {
        "ko": "EXPLORER",
        "en": "EXPLORER",
        "ja": "エクスプローラー",
        "zh": "资源管理器",
    },
    "studio.find_replace": {
        "ko": "🔍 찾기/바꾸기 (Ctrl+F)",
        "en": "🔍 Find/Replace (Ctrl+F)",
        "ja": "🔍 検索/置換 (Ctrl+F)",
        "zh": "🔍 查找/替换 (Ctrl+F)",
    },
    "studio.save_file": {
        "ko": "💾 현재 파일 저장",
        "en": "💾 Save Current File",
        "ja": "💾 現在のファイルを保存",
        "zh": "💾 保存当前文件",
    },
    "studio.send": {
        "ko": "전송",
        "en": "Send",
        "ja": "送信",
        "zh": "发送",
    },
    "studio.ai_tab": {
        "ko": "🤖 RuderAI AI",
        "en": "🤖 RuderAI AI",
        "ja": "🤖 RuderAI AI",
        "zh": "🤖 RuderAI AI",
    },
    "studio.terminal_tab": {
        "ko": "🖥️ Terminal",
        "en": "🖥️ Terminal",
        "ja": "🖥️ ターミナル",
        "zh": "🖥️ 终端",
    },
    "studio.ready": {
        "ko": "RuderAI Studio 준비 완료. 질문을 입력하세요.",
        "en": "RuderAI Studio is ready. Ask a question.",
        "ja": "RuderAI Studio の準備ができました。質問を入力してください。",
        "zh": "RuderAI Studio 已就绪。请输入问题。",
    },
    # --- environment dialog ----------------------------------------------
    "env.title": {
        "ko": "환경변수 설정",
        "en": "Environment Variables",
        "ja": "環境変数の設定",
        "zh": "环境变量设置",
    },
    "env.hint": {
        "ko": "환경변수 설정 (형식: KEY=VALUE / 한 줄에 하나씩)",
        "en": "Environment variables (format: KEY=VALUE, one per line)",
        "ja": "環境変数 (形式: KEY=VALUE / 1行に1つ)",
        "zh": "环境变量 (格式: KEY=VALUE / 每行一个)",
    },
    "env.cancel": {
        "ko": "취소",
        "en": "Cancel",
        "ja": "キャンセル",
        "zh": "取消",
    },
    "env.save": {
        "ko": "💾 저장",
        "en": "💾 Save",
        "ja": "💾 保存",
        "zh": "💾 保存",
    },
    "env.saved": {
        "ko": "환경변수가 성공적으로 저장되었습니다.",
        "en": "Environment variables saved.",
        "ja": "環境変数を保存しました。",
        "zh": "环境变量已保存。",
    },
    # --- find / replace dialog -------------------------------------------
    "find.title": {
        "ko": "찾기 / 바꾸기",
        "en": "Find / Replace",
        "ja": "検索 / 置換",
        "zh": "查找 / 替换",
    },
    "find.find_label": {
        "ko": "찾을 문자열",
        "en": "Find",
        "ja": "検索文字列",
        "zh": "查找内容",
    },
    "find.replace_label": {
        "ko": "바꿀 문자열",
        "en": "Replace with",
        "ja": "置換文字列",
        "zh": "替换为",
    },
    "find.case": {
        "ko": "대/소문자",
        "en": "Match case",
        "ja": "大文字小文字",
        "zh": "区分大小写",
    },
    "find.word": {
        "ko": "단어 전체",
        "en": "Whole word",
        "ja": "単語全体",
        "zh": "全字匹配",
    },
    "find.regex": {
        "ko": "정규식",
        "en": "Regex",
        "ja": "正規表現",
        "zh": "正则表达式",
    },
    "find.fuzzy": {
        "ko": "오타 허용",
        "en": "Fuzzy",
        "ja": "あいまい一致",
        "zh": "容错",
    },
    "find.prev": {
        "ko": "이전",
        "en": "Previous",
        "ja": "前へ",
        "zh": "上一个",
    },
    "find.next": {
        "ko": "다음",
        "en": "Next",
        "ja": "次へ",
        "zh": "下一个",
    },
    "find.replace": {
        "ko": "바꾸기",
        "en": "Replace",
        "ja": "置換",
        "zh": "替换",
    },
    "find.replace_all": {
        "ko": "모두 바꾸기",
        "en": "Replace All",
        "ja": "すべて置換",
        "zh": "全部替换",
    },
    "find.close": {
        "ko": "닫기",
        "en": "Close",
        "ja": "閉じる",
        "zh": "关闭",
    },
    # --- shared messages --------------------------------------------------
    "msg.error": {
        "ko": "에러",
        "en": "Error",
        "ja": "エラー",
        "zh": "错误",
    },
    "msg.warning": {
        "ko": "경고",
        "en": "Warning",
        "ja": "警告",
        "zh": "警告",
    },
    "msg.success": {
        "ko": "성공",
        "en": "Success",
        "ja": "成功",
        "zh": "成功",
    },
    "msg.unreadable_file": {
        "ko": "파일을 읽을 수 없습니다:",
        "en": "Cannot read file:",
        "ja": "ファイルを読み込めません:",
        "zh": "无法读取文件:",
    },
    "msg.save_failed": {
        "ko": "파일 저장 실패:",
        "en": "Failed to save file:",
        "ja": "ファイルの保存に失敗しました:",
        "zh": "保存文件失败:",
    },
    "msg.no_tab_selected": {
        "ko": "저장할 파일 탭이 선택되어 있지 않습니다.",
        "en": "No file tab is selected to save.",
        "ja": "保存するファイルタブが選択されていません。",
        "zh": "未选择要保存的文件标签页。",
    },
    "msg.temp_tab_unsavable": {
        "ko": "저장할 수 없는 임시 탭입니다.",
        "en": "This temporary tab cannot be saved.",
        "ja": "この一時タブは保存できません。",
        "zh": "无法保存此临时标签页。",
    },
    "msg.file_saved": {
        "ko": "파일이 저장되었습니다.",
        "en": "File saved.",
        "ja": "ファイルを保存しました。",
        "zh": "文件已保存。",
    },
    "msg.name_exists": {
        "ko": "이미 존재하는 이름입니다.",
        "en": "That name already exists.",
        "ja": "その名前はすでに存在します。",
        "zh": "该名称已存在。",
    },
    "msg.create_failed": {
        "ko": "생성 실패:",
        "en": "Failed to create:",
        "ja": "作成に失敗しました:",
        "zh": "创建失败:",
    },
    "msg.delete_confirm": {
        "ko": "삭제 확인",
        "en": "Confirm delete",
        "ja": "削除の確認",
        "zh": "确认删除",
    },
    "msg.delete_question": {
        "ko": "항목을 삭제하시겠습니까?",
        "en": "Delete this item?",
        "ja": "この項目を削除しますか?",
        "zh": "要删除该项目吗?",
    },
    "msg.delete_failed": {
        "ko": "삭제 실패:",
        "en": "Failed to delete:",
        "ja": "削除に失敗しました:",
        "zh": "删除失败:",
    },
    "msg.cannot_delete_root": {
        "ko": "최상위 워크스페이스 폴더는 삭제할 수 없습니다.",
        "en": "The top-level workspace folder cannot be deleted.",
        "ja": "最上位のワークスペースフォルダは削除できません。",
        "zh": "无法删除顶层工作区文件夹。",
    },
    "msg.cannot_move_root": {
        "ko": "최상위 워크스페이스 폴더는 이동할 수 없습니다.",
        "en": "The top-level workspace folder cannot be moved.",
        "ja": "最上位のワークスペースフォルダは移動できません。",
        "zh": "无法移动顶层工作区文件夹。",
    },
    "msg.missing_folder": {
        "ko": "존재하지 않는 폴더입니다:",
        "en": "This folder does not exist:",
        "ja": "存在しないフォルダです:",
        "zh": "该文件夹不存在:",
    },
    "msg.move_into_self": {
        "ko": "자기 자신 안으로 폴더를 이동할 수 없습니다.",
        "en": "A folder cannot be moved into itself.",
        "ja": "フォルダを自分自身の中に移動することはできません。",
        "zh": "无法将文件夹移动到其自身内部。",
    },
    "msg.dest_exists": {
        "ko": "대상 이름이 이미 존재합니다.",
        "en": "That name already exists.",
        "ja": "その名前はすでに存在します。",
        "zh": "该名称已存在。",
    },
    "msg.dest_exists_question": {
        "ko": "이미 존재합니다. 덮어쓸까요?",
        "en": "already exists. Overwrite?",
        "ja": "はすでに存在します。上書きしますか?",
        "zh": "已存在。要覆盖吗?",
    },
    "msg.remove_dest_failed": {
        "ko": "기존 대상 제거 실패:",
        "en": "Failed to remove the existing target:",
        "ja": "既存の対象の削除に失敗しました:",
        "zh": "删除现有目标失败:",
    },
    "msg.move_failed": {
        "ko": "항목을 이동할 수 없습니다:",
        "en": "Cannot move this item:",
        "ja": "この項目を移動できません:",
        "zh": "无法移动该项目:",
    },
    "msg.move_failed_title": {
        "ko": "이동 실패",
        "en": "Move failed",
        "ja": "移動に失敗しました",
        "zh": "移动失败",
    },
    "msg.open_file_first": {
        "ko": "먼저 파일을 열어주세요.",
        "en": "Open a file first.",
        "ja": "先にファイルを開いてください。",
        "zh": "请先打开文件。",
    },
    "msg.unsaved_title": {
        "ko": "저장되지 않은 변경",
        "en": "Unsaved changes",
        "ja": "未保存の変更",
        "zh": "未保存的更改",
    },
    "msg.unsaved_question": {
        "ko": "저장하지 않은 변경이 있습니다. 저장하시겠습니까?",
        "en": "There are unsaved changes. Save them?",
        "ja": "未保存の変更があります。保存しますか?",
        "zh": "有未保存的更改。要保存吗?",
    },
}

_current: str = DEFAULT_LANGUAGE


def normalize_language(code: Any) -> str:
    """Map anything a config file might hold onto a supported language code.

    Falls back to Korean for unknown, empty or non-string values so a typo in
    ``.ruder_ai_config.json`` cannot leave the GUI without strings.
    """
    if not isinstance(code, str):
        return DEFAULT_LANGUAGE
    lowered = code.strip().lower().replace("_", "-")
    base = lowered.split("-")[0]
    return base if base in LANGUAGES else DEFAULT_LANGUAGE


def set_language(code: Any) -> str:
    """Set the process-wide UI language. Returns the code actually applied."""
    global _current
    _current = normalize_language(code)
    return _current


def get_language() -> str:
    return _current


def t(key: str, /, **fmt: Any) -> str:
    """Translate *key*, formatting it with *fmt* when placeholders are given.

    Falls back to Korean and then to the key itself, so a partially translated
    language shows the source string instead of a raw identifier.
    """
    entry = CATALOG.get(key)
    if entry is None:
        return key
    text = entry.get(_current) or entry.get(DEFAULT_LANGUAGE) or key
    return text.format(**fmt) if fmt else text


def language_choices() -> list[tuple[str, str]]:
    """(code, endonym) pairs for a picker, in canonical order."""
    return [(code, LANGUAGE_NAMES[code]) for code in LANGUAGES]
