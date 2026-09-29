"""UI language selection for the Studio and the settings window.

The chrome was hardcoded Korean, so running the English or Japanese model
still produced a Korean window. These tests pin the catalogue's contract
(complete, fallback-safe) and the config round-trip, rather than the pixels —
the widget wiring itself is covered out-of-process by _studio_gui_check.py.
"""
from pathlib import Path

import pytest

from ruder_ai.core.config import DEFAULT_CONFIG, ConfigManager
from ruder_ai.tui import i18n
from ruder_ai.tui.i18n import (
    CATALOG,
    DEFAULT_LANGUAGE,
    LANGUAGE_NAMES,
    LANGUAGES,
    get_language,
    language_choices,
    normalize_language,
    set_language,
    t,
)


@pytest.fixture(autouse=True)
def _restore_language():
    """Keep the process-wide language from leaking between tests."""
    previous = get_language()
    yield
    set_language(previous)


def test_every_language_has_a_display_name():
    assert set(LANGUAGE_NAMES) == set(LANGUAGES)
    for code in LANGUAGES:
        assert LANGUAGE_NAMES[code].strip(), code


def test_catalog_is_complete_for_every_language():
    """A half-translated language must not silently fall back at runtime."""
    missing = {
        key: [lang for lang in LANGUAGES if lang not in entry]
        for key, entry in CATALOG.items()
        if any(lang not in entry for lang in LANGUAGES)
    }
    assert not missing, missing


def test_catalog_entries_have_no_empty_strings():
    empty = [
        (key, lang)
        for key, entry in CATALOG.items()
        for lang, text in entry.items()
        if not str(text).strip()
    ]
    assert not empty, empty


def test_korean_is_the_fallback_language():
    assert DEFAULT_LANGUAGE == "ko"
    # A language missing a key falls back to the Korean source string, not to
    # a raw identifier.
    assert DEFAULT_LANGUAGE in CATALOG["settings.save"]


def test_each_language_renders_distinctly():
    set_language("ja")
    assert t("settings.save") == "設定を保存"
    set_language("zh")
    assert t("settings.save") == "保存设置"
    set_language("en")
    assert t("settings.save") == "Save Configuration"


def test_set_language_normalizes_and_reports():
    assert set_language("en-US") == "en"
    assert set_language("ZH_cn") == "zh"
    # Unknown values fall back rather than leaving the GUI untranslated.
    assert set_language("klingon") == DEFAULT_LANGUAGE
    assert set_language(None) == DEFAULT_LANGUAGE
    assert set_language(123) == DEFAULT_LANGUAGE


def test_normalize_language_is_pure():
    assert normalize_language("JA") == "ja"
    assert normalize_language("") == DEFAULT_LANGUAGE
    assert normalize_language("  fr  ") == DEFAULT_LANGUAGE


def test_unknown_key_returns_the_key_instead_of_raising():
    assert t("no.such.key") == "no.such.key"


def test_placeholders_are_formatted_only_when_supplied():
    assert t("msg.delete_question") == "항목을 삭제하시겠습니까?"
    assert "{" not in t("msg.delete_question")


def test_language_choices_are_ordered_for_a_picker():
    choices = language_choices()
    assert [code for code, _ in choices] == list(LANGUAGES)
    assert [name for _, name in choices] == [LANGUAGE_NAMES[c] for c in LANGUAGES]


def test_ui_language_default_is_declared_and_persisted(tmp_path):
    assert DEFAULT_CONFIG["ui_language"] == DEFAULT_LANGUAGE

    mgr = ConfigManager(config_path=tmp_path / ".ruder_ai_config.json")
    assert mgr.get("ui_language") == DEFAULT_LANGUAGE

    # A config written before the key existed still yields a usable language.
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"model_name": "m"}', encoding="utf-8")
    assert ConfigManager(config_path=legacy).get("ui_language") == DEFAULT_LANGUAGE


def test_ui_language_survives_a_partial_update(tmp_path):
    """Saving the dialog must not drop the language, like other tuning keys."""
    mgr = ConfigManager(config_path=tmp_path / ".ruder_ai_config.json")
    mgr.update({"ui_language": "ja"})
    mgr.update({"model_name": "ruder-ai-jp"})

    assert mgr.get("ui_language") == "ja"
    assert mgr.get("model_name") == "ruder-ai-jp"


def test_corrupt_ui_language_value_falls_back(tmp_path):
    path = tmp_path / ".ruder_ai_config.json"
    path.write_text('{"ui_language": 42}', encoding="utf-8")

    mgr = ConfigManager(config_path=path)
    assert normalize_language(mgr.get("ui_language")) == DEFAULT_LANGUAGE


def test_gui_modules_use_the_catalogue_instead_of_hardcoded_korean():
    """Guard against new UI strings being added as bare Korean literals."""
    tui = Path(__file__).resolve().parent.parent / "ruder_ai" / "tui"
    import re

    # Korean allowed only inside comments/docstrings would be hard to separate;
    # instead assert the widget labels and dialogs route through t().
    pattern = re.compile(
        r'(?:text|title)\s*=\s*"[^"]*[\uac00-\ud7af][^"]*"'
        r'|(?:showerror|showwarning|showinfo|askyesno|askyesnocancel)\s*\(\s*"[^"]*[\uac00-\ud7af]',
    )
    for name in ("app_gui.py", "config_gui.py"):
        source = (tui / name).read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in source.splitlines()
            if pattern.search(line)
        ]
        assert not offenders, f"{name}: {offenders}"
