"""Positive real-entry workflows promoted into feature acceptance coverage."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.runtime_test_harness import QtUserDriver

pytestmark = pytest.mark.workflow


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Give Settings a real isolated file while neutralizing unrelated host effects."""

    import config
    from core import tts
    from core.llm_clients import client as llm_client
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel import env as settings_env
    from ui.settings_panel.dialog import SettingsDialog
    from ui.shared import theme

    env_path = tmp_path / "acceptance-settings.env"
    env_path.write_text(
        "TTS_PROVIDER=none\nTHEME_MODE=system\nBUBBLE_WIDTH=340\n",
        encoding="utf-8",
    )
    theme_calls: list[bool] = []
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    monkeypatch.setattr(SettingsDialog, "_save_api_keys_to_keychain", lambda _self: True)
    monkeypatch.setattr(SettingsDialog, "_capability_warnings_for_values", lambda _self, _values: ([], {}))
    monkeypatch.setattr(SettingsDialog, "_set_warning_markers", lambda _self, _warnings: None)
    monkeypatch.setattr(config, "reload", lambda: None)
    monkeypatch.setattr(llm_client, "reset_clients", lambda: None)
    monkeypatch.setattr(tts, "reset_connections", lambda: None)
    monkeypatch.setattr(theme, "apply_app_theme", lambda *_args, **_kwargs: theme_calls.append(True))
    return env_path, settings_env, theme_calls


@pytest.fixture
def live_runtime_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use one real Settings file for the dialog, config reload, and live widgets."""

    import config
    from core.system import autostart
    from ui.overlay import IconOverlay
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel import env as settings_env
    from ui.settings_panel.dialog import SettingsDialog

    env_path = tmp_path / "live-acceptance-settings.env"
    env_path.write_text(
        "\n".join(
            [
                "THEME_MODE=dark",
                "THEME_DARK_BG=#101218",
                "THEME_DARK_SURFACE=#171a22",
                "THEME_DARK_TEXT=#e8eaf0",
                "THEME_DARK_ACCENT=#7c83ff",
                "APP_LANGUAGE=en",
                "ASSISTANT_LANGUAGE=",
                "ICON_SIZE=60",
                "BUBBLE_WIDTH=340",
                "BUBBLE_LINES=4",
                "BUBBLE_FONT_SIZE=10",
                "BUBBLE_SCROLL_ENABLED=True",
                "BUBBLE_SCROLL_SNAP_ENABLED=True",
                "BUBBLE_COLOR=#1c1c24dc",
                "BUBBLE_TEXT_COLOR=#e6e6e6",
                "BUBBLE_READ_WORD_COLOR=#4da3ff",
                "BUBBLE_REVEAL_WPM=170",
                "BUBBLE_HOLD_REVEAL_WPM=480",
                "BUBBLE_HIDE_DELAY_MS=3500",
                "BUBBLE_SCROLL_SNAP_DELAY_MS=2500",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    original_env_file = config._ENV_FILE
    original_loaded_keys = set(config._LOADED_DOTENV_KEYS)
    original_process_env = dict(os.environ)
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    monkeypatch.setattr(SettingsDialog, "_save_api_keys_to_keychain", lambda _self: True)
    monkeypatch.setattr(SettingsDialog, "_capability_warnings_for_values", lambda _self, _values: ([], {}))
    monkeypatch.setattr(SettingsDialog, "_set_warning_markers", lambda _self, _warnings: None)
    monkeypatch.setattr(SettingsDialog, "_schedule_open_status_refresh", lambda _self: None)
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda _self: None)
    monkeypatch.setattr(autostart, "sync_start_on_login", lambda _enabled: None)

    config._ENV_FILE = env_path
    # Removing the old file's managed keys makes the isolated file authoritative.
    config._LOADED_DOTENV_KEYS = set(original_loaded_keys)
    config.reload()
    try:
        yield env_path, settings_env
    finally:
        os.environ.clear()
        os.environ.update(original_process_env)
        config._ENV_FILE = original_env_file
        config._LOADED_DOTENV_KEYS = original_loaded_keys
        config._load_config()


def _unique_shortcuts(dialog) -> None:
    """Keep save validation independent from shortcuts on the developer host."""

    for index, block in enumerate(dialog._caller_blocks, 1):
        block["hotkey"].setText(f"ctrl+alt+{index}")
        if "hotkey_2" in block:
            block["hotkey_2"].setText(f"ctrl+win+{index}")
    keys = ("HOTKEY_ADD_CONTEXT", "HOTKEY_CLEAR_CONTEXT", "HOTKEY_SNIP", "HOTKEY_VOICE", "HOTKEY_DICTATE")
    for index, key in enumerate(keys, 1):
        dialog._fields[key].setText(f"ctrl+shift+alt+{index}")
        secondary = dialog._fields.get(f"{key}_2")
        if secondary is not None:
            secondary.setText(f"ctrl+shift+win+{index}")


def test_settings_navigation_search_and_cancel_are_real_user_workflows(qapp, isolated_settings):
    """Navigate every page, filter it, then prove Cancel discards a real edit."""

    from PySide6.QtWidgets import QLineEdit, QPushButton
    from ui.settings_panel.dialog import SettingsDialog

    env_path, settings_env, _theme_calls = isolated_settings
    driver = QtUserDriver(qapp)
    dialog = SettingsDialog()
    clean_reopen = None
    reopened = None
    try:
        dialog.show()
        driver.pump()
        nav = dialog._settings_nav
        assert nav is not None
        assert nav.count() == len(dialog._tab_base_names) == 8
        for row, internal_name in enumerate(dialog._tab_base_names):
            driver.select_list_row(nav, row)
            assert dialog._tabs.currentIndex() == row
            assert dialog._current_tab_name() == internal_name

        search = dialog.findChild(QLineEdit, "settingsSearch")
        assert search is not None
        driver.replace_text(search, "tool_file_roots")
        visible = [
            dialog._tab_base_names[index]
            for index in range(dialog._tabs.count())
            if dialog._tabs.isTabVisible(index)
        ]
        assert visible == ["Advanced"]
        driver.replace_text(search, "no setting has this impossible phrase")
        assert not any(dialog._tabs.isTabVisible(index) for index in range(dialog._tabs.count()))
        search.clear()
        driver.pump()
        assert all(dialog._tabs.isTabVisible(index) for index in range(dialog._tabs.count()))

        # Clean Cancel closes without changing the file.
        original_text = env_path.read_text(encoding="utf-8")
        cancel = dialog.findChild(QPushButton, "settingsCancelButton")
        driver.click(cancel)
        assert not dialog.isVisible()
        assert env_path.read_text(encoding="utf-8") == original_text
        dialog.deleteLater()
        driver.pump()

        clean_reopen = SettingsDialog()
        clean_reopen.show()
        driver.pump()
        # Dirty Cancel must discard the pending edit as well.
        dialog = clean_reopen
        driver.replace_text(dialog._fields["BUBBLE_WIDTH"], "612")
        cancel = dialog.findChild(QPushButton, "settingsCancelButton")
        driver.click(cancel)
        assert not dialog.isVisible()
        assert settings_env.read_settings_env()["BUBBLE_WIDTH"] == "340"

        reopened = SettingsDialog()
        assert reopened._fields["BUBBLE_WIDTH"].text() == "340"
        assert env_path.read_text(encoding="utf-8").count("BUBBLE_WIDTH=") == 1
    finally:
        if reopened is not None:
            reopened.deleteLater()
        dialog.deleteLater()
        driver.pump()


@pytest.mark.parametrize("theme_mode", ["system", "light", "dark"])
def test_every_theme_mode_saves_applies_and_reopens(qapp, isolated_settings, theme_mode):
    """Every Theme choice survives the real Save changes action and reopen."""

    from PySide6.QtWidgets import QPushButton
    from ui.settings_panel.dialog import SettingsDialog

    _env_path, settings_env, theme_calls = isolated_settings
    driver = QtUserDriver(qapp)
    dialog = SettingsDialog()
    reopened = None
    try:
        dialog.show()
        driver.pump()
        _unique_shortcuts(dialog)
        driver.select_combo_data(dialog._fields["THEME_MODE"], theme_mode)
        save = dialog.findChild(QPushButton, "settingsApplyButton")
        assert save is not None and save.isEnabled()
        driver.click(save)

        assert settings_env.read_settings_env()["THEME_MODE"] == theme_mode
        assert theme_calls
        reopened = SettingsDialog()
        assert reopened._fields["THEME_MODE"].currentData() == theme_mode
    finally:
        if reopened is not None:
            reopened.deleteLater()
        dialog.deleteLater()
        driver.pump()


_APPEARANCE_PROFILES = (
    {
        "name": "compact_no_scroll_no_snap",
        "icon": "48",
        "width": "280",
        "lines": "2",
        "font": "8",
        "scroll": False,
        "snap": False,
        "normal_wpm": "120",
        "hold_wpm": "360",
        "hide_seconds": "0.5",
        "snap_seconds": "0",
        "theme": ("#111827", "#1f2937", "#f9fafb", "#22c55e"),
        "bubble": ("#102030d0", "#f1f5f9", "#ef4444"),
    },
    {
        "name": "roomy_no_scroll_with_snap",
        "icon": "96",
        "width": "640",
        "lines": "8",
        "font": "20",
        "scroll": False,
        "snap": True,
        "normal_wpm": "240",
        "hold_wpm": "720",
        "hide_seconds": "8.25",
        "snap_seconds": "4.5",
        "theme": ("#0f172a", "#1e293b", "#e2e8f0", "#38bdf8"),
        "bubble": ("#111827e0", "#f8fafc", "#f59e0b"),
    },
    {
        "name": "scroll_without_snap",
        "icon": "64",
        "width": "420",
        "lines": "5",
        "font": "12",
        "scroll": True,
        "snap": False,
        "normal_wpm": "180",
        "hold_wpm": "540",
        "hide_seconds": "3",
        "snap_seconds": "1.25",
        "theme": ("#18181b", "#27272a", "#fafafa", "#a78bfa"),
        "bubble": ("#27272add", "#fafafa", "#c084fc"),
    },
    {
        "name": "scroll_with_snap",
        "icon": "72",
        "width": "500",
        "lines": "6",
        "font": "16",
        "scroll": True,
        "snap": True,
        "normal_wpm": "300",
        "hold_wpm": "900",
        "hide_seconds": "12",
        "snap_seconds": "6",
        "theme": ("#172554", "#1e3a8a", "#eff6ff", "#60a5fa"),
        "bubble": ("#172554e8", "#eff6ff", "#93c5fd"),
    },
)


@pytest.mark.parametrize("profile", _APPEARANCE_PROFILES, ids=lambda profile: profile["name"])
def test_settings_appearance_matrix_updates_the_live_overlay(
    qapp,
    live_runtime_settings,
    profile,
):
    """Save every appearance branch and apply it to the already-running overlay."""

    from PySide6.QtWidgets import QPushButton

    import config
    from runtime.workers.ui_host import QtProtocolHost
    from ui.overlay import IconOverlay, OverlaySignals
    from ui.settings_panel.dialog import SettingsDialog

    env_path, settings_env = live_runtime_settings
    driver = QtUserDriver(qapp, timeout=2.0)
    signals = OverlaySignals()
    overlay = IconOverlay(signals)
    emitted: list[tuple[str, dict]] = []
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._app = qapp
    host._overlay = overlay
    host.emit = lambda event, data=None, req_id=None: emitted.append((event, data or {}))
    dialog = SettingsDialog(on_apply=host._settings_applied)
    try:
        overlay.show()
        signals.show_icon.emit()
        driver.pump()
        _unique_shortcuts(dialog)
        driver.select_combo_data(dialog._fields["THEME_MODE"], "dark")

        for key, value in zip(
            ("THEME_BG", "THEME_SURFACE", "THEME_TEXT", "THEME_ACCENT"),
            profile["theme"],
            strict=True,
        ):
            driver.replace_text(dialog._fields[key], value)
        for key, value in zip(
            ("BUBBLE_COLOR", "BUBBLE_TEXT_COLOR", "BUBBLE_READ_WORD_COLOR"),
            profile["bubble"],
            strict=True,
        ):
            driver.replace_text(dialog._fields[key], value)
        for key, value in (
            ("ICON_SIZE", profile["icon"]),
            ("BUBBLE_WIDTH", profile["width"]),
            ("BUBBLE_LINES", profile["lines"]),
            ("BUBBLE_FONT_SIZE", profile["font"]),
            ("BUBBLE_REVEAL_WPM", profile["normal_wpm"]),
            ("BUBBLE_HOLD_REVEAL_WPM", profile["hold_wpm"]),
            ("BUBBLE_HIDE_DELAY_S", profile["hide_seconds"]),
            ("BUBBLE_SCROLL_SNAP_DELAY_S", profile["snap_seconds"]),
        ):
            driver.replace_text(dialog._fields[key], value)
        dialog._fields["BUBBLE_SCROLL_ENABLED"].setChecked(profile["scroll"])
        dialog._fields["BUBBLE_SCROLL_SNAP_ENABLED"].setChecked(profile["snap"])
        driver.pump()

        save = dialog.findChild(QPushButton, "settingsApplyButton")
        assert save is not None and save.isEnabled()
        driver.click(save)

        saved = settings_env.read_settings_env()
        assert env_path.is_file()
        assert saved["ICON_SIZE"] == profile["icon"]
        assert saved["BUBBLE_WIDTH"] == profile["width"]
        assert saved["BUBBLE_SCROLL_ENABLED"] == str(profile["scroll"])
        assert saved["BUBBLE_SCROLL_SNAP_ENABLED"] == str(profile["snap"])
        assert config.ICON_SIZE == int(profile["icon"])
        assert config.BUBBLE_WIDTH == int(profile["width"])
        assert config.BUBBLE_LINES == int(profile["lines"])
        assert config.BUBBLE_FONT_SIZE == int(profile["font"])
        assert config.BUBBLE_SCROLL_ENABLED is profile["scroll"]
        assert config.BUBBLE_SCROLL_SNAP_ENABLED is profile["snap"]
        assert config.BUBBLE_HIDE_DELAY_MS == round(float(profile["hide_seconds"]) * 1000)
        expected_snap_ms = max(500, round(float(profile["snap_seconds"]) * 1000))
        assert config.BUBBLE_SCROLL_SNAP_DELAY_MS == expected_snap_ms
        assert (
            config.THEME_DARK_BG,
            config.THEME_DARK_SURFACE,
            config.THEME_DARK_TEXT,
            config.THEME_DARK_ACCENT,
        ) == profile["theme"]
        assert profile["theme"][0].lower() in qapp.styleSheet().lower()

        # The production UI-host callback must update widgets that already exist.
        assert emitted and emitted[-1][0] == "ui.settings.applied"
        assert overlay._icon_label.width() == int(profile["icon"])
        assert overlay._icon_label.height() == int(profile["icon"])
        assert overlay._bubble._bubble_w == int(profile["width"])
        assert overlay._bubble._font.pointSize() == int(profile["font"])
        assert overlay._bubble._bubble_color.name().lower() == profile["bubble"][0][:7].lower()
        assert overlay._bubble._bubble_color.alpha() == int(profile["bubble"][0][7:9], 16)
        assert overlay._bubble._text_color.name().lower() == profile["bubble"][1].lower()
        assert overlay._bubble._read_word_color.name().lower() == profile["bubble"][2].lower()
        assert overlay._bubble._hide_timer.interval() == max(
            500, round(float(profile["hide_seconds"]) * 1000)
        )
        assert overlay._bubble._bubble_scroll_enabled() is profile["scroll"]
        assert overlay._bubble._bubble_scroll_snap_enabled() is profile["snap"]
        assert overlay._bubble._bubble_scroll_snap_delay_ms() == expected_snap_ms
        overlay._bubble._speed_boosting = False
        assert overlay._bubble._current_reveal_wpm() == int(profile["normal_wpm"])
        overlay._bubble._speed_boosting = True
        assert overlay._bubble._current_reveal_wpm() == int(profile["hold_wpm"])

        # A normal runtime event still works after the live settings mutation.
        signals.bubble_listening.emit()
        driver.wait(lambda: overlay._bubble.isVisible(), "listening bubble after settings apply")
        assert any("Recording" in line for line in overlay._bubble._lines)
    finally:
        from PySide6.QtCore import QCoreApplication, QEvent

        dialog.close()
        dialog.deleteLater()
        overlay._bubble.clear()
        for widget in (
            overlay._bubble,
            overlay._context_panel,
            overlay._provider_badge,
            overlay._icon_label,
        ):
            widget.close()
            widget.deleteLater()
        overlay._tray.hide()
        overlay._tray.deleteLater()
        overlay.close()
        overlay.deleteLater()
        driver.pump()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        driver.pump()


def test_every_app_language_saves_and_applies_through_the_real_settings_button(
    qapp,
    live_runtime_settings,
):
    """Exercise every supported UI-language state through repeated real saves."""

    from PySide6.QtWidgets import QPushButton

    import config
    from ui.i18n import LANGUAGE_OPTIONS, current_language, t
    from ui.settings_panel.dialog import SettingsDialog

    _env_path, settings_env = live_runtime_settings
    driver = QtUserDriver(qapp, timeout=2.0)
    dialog = SettingsDialog()
    try:
        _unique_shortcuts(dialog)
        save = dialog.findChild(QPushButton, "settingsApplyButton")
        expected_values = [value for _label, value in LANGUAGE_OPTIONS]
        for value in expected_values:
            driver.select_combo_data(dialog._fields["APP_LANGUAGE"], value)
            assert save is not None and save.isEnabled()
            driver.click(save)
            assert settings_env.read_settings_env()["APP_LANGUAGE"] == value
            assert config.APP_LANGUAGE == value
            assert dialog._fields["APP_LANGUAGE"].currentData() == value
            if value:
                assert current_language() == value
            cancel = dialog.findChild(QPushButton, "settingsCancelButton")
            assert cancel is not None and cancel.text() == t("Cancel")
            # App language affects every Settings page, so exercise all 6 x 8 states.
            for row, internal_name in enumerate(dialog._tab_base_names):
                driver.select_list_row(dialog._settings_nav, row)
                assert dialog._tabs.currentIndex() == row
                assert dialog._current_tab_name() == internal_name
    finally:
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_every_assistant_language_changes_the_real_runtime_prompt(
    qapp,
    live_runtime_settings,
):
    """Save every reply-language state and verify the prompt used by the runtime."""

    from PySide6.QtWidgets import QPushButton

    import config
    from core.prompt_i18n import assistant_language_instruction
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel.dialog import SettingsDialog

    _env_path, settings_env = live_runtime_settings
    driver = QtUserDriver(qapp, timeout=2.0)
    dialog = SettingsDialog()
    try:
        _unique_shortcuts(dialog)
        save = dialog.findChild(QPushButton, "settingsApplyButton")
        values = [value for _label, value in settings_dialog._ASSISTANT_LANGUAGE_OPTIONS]
        # Start away from the fixture's empty value so every iteration is a change.
        values = values[1:] + values[:1]
        for value in values:
            driver.select_combo_data(dialog._fields["ASSISTANT_LANGUAGE"], value)
            assert save is not None and save.isEnabled()
            driver.click(save)
            assert settings_env.read_settings_env()["ASSISTANT_LANGUAGE"] == value
            assert config.ASSISTANT_LANGUAGE == value
            instruction = assistant_language_instruction(value)
            if instruction:
                assert instruction in config.get_system_prompt()
            else:
                assert "Respond in " not in config.get_system_prompt()
                assert "same language as the user's latest request" not in config.get_system_prompt()
    finally:
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_three_conversation_system_prompts_save_reload_and_remain_independent(
    qapp,
    live_runtime_settings,
):
    """Edit Wisp, ChatGPT, and Claude prompts through their real Settings fields."""

    from PySide6.QtWidgets import QPushButton

    import config
    from ui.settings_panel.dialog import SettingsDialog

    _env_path, settings_env = live_runtime_settings
    driver = QtUserDriver(qapp, timeout=2.0)
    dialog = SettingsDialog()
    reopened = None
    prompts = {
        "SYSTEM_PROMPT_UTILITY": "Wisp acceptance prompt only.",
        "WISP_CODEX_SYSTEM_PROMPT": "ChatGPT acceptance prompt only.",
        "WISP_CLAUDE_SYSTEM_PROMPT": "Claude acceptance prompt only.",
    }
    try:
        _unique_shortcuts(dialog)
        for key, value in prompts.items():
            dialog._fields[key].setPlainText(value)
        driver.pump()
        save = dialog.findChild(QPushButton, "settingsApplyButton")
        assert save is not None and save.isEnabled()
        driver.click(save)

        saved = settings_env.read_settings_env()
        assert {key: saved[key] for key in prompts} == prompts
        assert config.SYSTEM_PROMPT_UTILITY == prompts["SYSTEM_PROMPT_UTILITY"]
        assert config.WISP_CODEX_SYSTEM_PROMPT == prompts["WISP_CODEX_SYSTEM_PROMPT"]
        assert config.WISP_CLAUDE_SYSTEM_PROMPT == prompts["WISP_CLAUDE_SYSTEM_PROMPT"]

        reopened = SettingsDialog()
        for key, value in prompts.items():
            assert reopened._fields[key].toPlainText() == value
    finally:
        if reopened is not None:
            reopened.close()
            reopened.deleteLater()
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_builtin_profile_action_saves_and_reopens_as_the_active_runtime_profile(
    qapp,
    live_runtime_settings,
):
    """Trigger the real Profiles QAction, save it, and reopen its runtime values."""

    from PySide6.QtWidgets import QPushButton

    import config
    from ui.settings_panel.dialog import SettingsDialog

    _env_path, settings_env = live_runtime_settings
    driver = QtUserDriver(qapp, timeout=2.0)
    dialog = SettingsDialog()
    reopened = None
    try:
        _unique_shortcuts(dialog)
        profile_button = dialog.findChild(QPushButton, "settingsProfilesButton")
        assert profile_button is not None and profile_button.menu() is not None
        action = next(action for action in profile_button.menu().actions() if action.text() == "Low setup")
        action.trigger()
        driver.pump()
        assert profile_button.text() == "Low setup"
        assert dialog._active_preset_slug == "low_setup"

        save = dialog.findChild(QPushButton, "settingsApplyButton")
        assert save is not None and save.isEnabled()
        driver.click(save)
        saved = settings_env.read_settings_env()
        assert saved["WISP_SETTINGS_PRESET"] == "low_setup"
        assert saved["ACTIVE_PROFILE"] == "default"
        assert config.SETTINGS.chat_llm.provider == "chatgpt"

        reopened = SettingsDialog()
        assert reopened.findChild(QPushButton, "settingsProfilesButton").text() == "Low setup"
        for route in ("LLM", "VISION_LLM", "MEMORY_LLM"):
            row = reopened._model_section_rows[route][0]
            assert row["api_key_combo"].currentData() == "chatgpt"
            assert reopened._model_value(row) == "gpt-5.5"
    finally:
        if reopened is not None:
            reopened.close()
            reopened.deleteLater()
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_setup_check_button_runs_the_real_check_and_displays_its_report(
    qapp,
    live_runtime_settings,
    monkeypatch,
):
    """Use the visible setup button with the host checks isolated at their boundary."""

    from PySide6.QtWidgets import QMessageBox, QPushButton

    from core import setup_check
    from ui.settings_panel.dialog import SettingsDialog

    _env_path, _settings_env = live_runtime_settings
    rows = [
        {"name": "Provider", "status": "pass", "message": "Ready", "recommendation": ""},
        {"name": "Microphone", "status": "warn", "message": "Permission needed", "recommendation": "Grant access"},
    ]
    reports: list[tuple[str, str]] = []
    monkeypatch.setattr(setup_check, "run_setup_check", lambda: rows)
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, message: reports.append((title, message)),
    )
    driver = QtUserDriver(qapp)
    dialog = SettingsDialog()
    try:
        button = dialog.findChild(QPushButton, "settingsSetupCheckButton")
        assert button is not None
        driver.click(button)
        assert len(reports) == 1
        assert "Provider" in reports[0][1] and "Ready" in reports[0][1]
        assert "Microphone" in reports[0][1] and "Grant access" in reports[0][1]
        assert dialog.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_profile_setup_button_opens_real_wizard_and_dirty_settings_block_it(
    qapp,
    live_runtime_settings,
    monkeypatch,
):
    """Cover both clean-open and unsaved-change branches of the Settings action."""

    from PySide6.QtWidgets import QMessageBox, QPushButton

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    _env_path, _settings_env = live_runtime_settings
    notices: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, message: notices.append(message),
    )
    driver = QtUserDriver(qapp)
    dialog = SettingsDialog()
    wizard = None
    try:
        dialog.show()
        driver.pump()
        button = next(
            button
            for button in dialog.findChildren(QPushButton)
            if button.text() == t("Run profile setup")
        )
        driver.click(button)
        wizard = dialog._profile_setup_wizard
        assert wizard is not None and wizard.isVisible()
        assert wizard._pages.currentIndex() >= 0
        wizard.reject()
        driver.pump()

        driver.replace_text(dialog._fields["BUBBLE_WIDTH"], "777")
        assert dialog._dirty_keys
        driver.click(button)
        assert notices and "Save or discard" in notices[-1]
        assert dialog._profile_setup_wizard is None
    finally:
        if wizard is not None:
            try:
                wizard.close()
                wizard.deleteLater()
            except RuntimeError:
                pass
        dialog.close()
        dialog.deleteLater()
        driver.pump()


_PAGE_RESET_CASES = (
    ("App", "BUBBLE_WIDTH"),
    ("Connections", "CUSTOM_BASE_URL"),
    ("LLM", "CHAT_REASONING_EFFORT"),
    ("TTS / Voice", "TTS_VOLUME"),
    ("Keybinds", "INTENT_OVERLAY_TIMEOUT_MS"),
    ("Prompts", "SYSTEM_PROMPT_UTILITY"),
    ("Advanced", "MEMORY_TOP_K"),
    ("About", None),
)


@pytest.mark.parametrize(
    ("page_name", "removed_key"),
    _PAGE_RESET_CASES,
    ids=lambda value: str(value).replace(" / ", "_").replace(" ", "_").lower(),
)
def test_reset_page_action_is_scoped_across_every_settings_page(
    qapp,
    live_runtime_settings,
    monkeypatch,
    page_name,
    removed_key,
):
    """Reset each page through its button and prove unrelated page state survives."""

    from PySide6.QtWidgets import QMessageBox, QPushButton

    import config
    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    _env_path, settings_env = live_runtime_settings
    sentinels = {
        "BUBBLE_WIDTH": "777",
        "CUSTOM_BASE_URL": "https://example.invalid/v1",
        "CHAT_REASONING_EFFORT": "high",
        "TTS_VOLUME": "0.25",
        "INTENT_OVERLAY_TIMEOUT_MS": "9999",
        "SYSTEM_PROMPT_UTILITY": "page reset sentinel prompt",
        "MEMORY_TOP_K": "99",
    }
    settings_env.write_settings_env(sentinels)
    config.reload()
    monkeypatch.setattr(QMessageBox, "exec", lambda _self: QMessageBox.StandardButton.Yes)
    reports: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, message: reports.append(message),
    )
    driver = QtUserDriver(qapp, timeout=2.0)
    dialog = SettingsDialog()
    reset_stt_calls: list[bool] = []
    dialog._reset_stt_model_in_background = lambda: reset_stt_calls.append(True)
    try:
        row = dialog._tab_base_names.index(page_name)
        driver.select_list_row(dialog._settings_nav, row)
        reset = next(
            button
            for button in dialog.findChildren(QPushButton)
            if button.text() == t("Reset Page…")
        )
        driver.click(reset)
        saved = settings_env.read_settings_env()
        for key, value in sentinels.items():
            if key == removed_key:
                assert key not in saved
            else:
                assert saved[key] == value
        assert reports and page_name in reports[-1]
        assert bool(reset_stt_calls) is (page_name == "TTS / Voice")
        assert dialog._current_tab_name() == page_name
    finally:
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_reset_all_action_clears_isolated_settings_credentials_and_sessions(
    qapp,
    live_runtime_settings,
    monkeypatch,
):
    """Confirm the real Reset All action and verify every destructive boundary."""

    from PySide6.QtWidgets import QMessageBox, QPushButton

    import config
    from core import secret_store
    from core.auth import chatgpt, copilot_auth, github
    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    env_path, settings_env = live_runtime_settings
    settings_env.write_settings_env({"MEMORY_TOP_K": "88", "BUBBLE_WIDTH": "777"})
    config.reload()
    calls: list[str] = []
    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ("OPENAI_API_KEY", "CUSTOM_API_KEY"))
    monkeypatch.setattr(secret_store, "delete_secret", lambda name: calls.append(f"key:{name}"))
    monkeypatch.setattr(secret_store, "get_keychain_secret", lambda _name: None)
    monkeypatch.setattr(chatgpt, "clear_tokens", lambda: calls.append("oauth:chatgpt"))
    monkeypatch.setattr(github, "clear_tokens", lambda: calls.append("oauth:github"))
    monkeypatch.setattr(copilot_auth, "clear_token", lambda: calls.append("oauth:copilot"))
    monkeypatch.setattr(QMessageBox, "exec", lambda _self: QMessageBox.StandardButton.Yes)
    infos: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, message: infos.append(message),
    )
    driver = QtUserDriver(qapp, timeout=2.0)
    dialog = SettingsDialog()
    dialog._reset_stt_model_in_background = lambda: calls.append("stt:reset")
    dialog._refresh_chatgpt_status = lambda: None
    dialog._refresh_github_status = lambda: None
    dialog._refresh_copilot_status = lambda: None
    try:
        reset = next(
            button
            for button in dialog.findChildren(QPushButton)
            if button.text() == t("Reset All…")
        )
        driver.click(reset)
        assert not env_path.exists()
        assert settings_env.read_settings_env() == {}
        assert config.BUBBLE_WIDTH == 340
        assert config.MEMORY_TOP_K != 88
        assert set(calls) >= {
            "key:OPENAI_API_KEY",
            "key:CUSTOM_API_KEY",
            "oauth:chatgpt",
            "oauth:github",
            "oauth:copilot",
            "stt:reset",
        }
        assert infos and "reset" in infos[-1].lower()
        assert dialog.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_intent_submit_escape_and_timeout_modes_use_real_widget_events(qapp, monkeypatch):
    """Exercise custom submit, Escape, timed close, and zero-timeout through Qt."""

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    import config
    from ui.intent_overlay import IntentOverlay

    driver = QtUserDriver(qapp, timeout=1.0)
    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s", "custom_label": "Custom"}]
    overlays = []
    try:
        # Custom key -> typed prompt -> Enter -> production intent signal.
        monkeypatch.setattr(config, "INTENT_OVERLAY_TIMEOUT_MS", 5000)
        submit = IntentOverlay(caller_idx=0)
        overlays.append(submit)
        chosen: list[tuple[str, str]] = []
        submit.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
        submit.show()
        driver.pump()
        QTest.keyClick(submit, Qt.Key.Key_S)
        driver.pump()
        assert not submit._input_line.isHidden()
        QTest.keyClicks(submit._input_line, "Explain this actual prompt")
        QTest.keyClick(submit._input_line, Qt.Key.Key_Return)
        driver.wait(lambda: bool(chosen), "custom intent submission")
        assert chosen == [("S", "Explain this actual prompt")]

        # Escape is the real cancellation entry point.
        escape = IntentOverlay(caller_idx=0)
        overlays.append(escape)
        escaped: list[bool] = []
        escape.cancelled.connect(lambda: escaped.append(True))
        escape.show()
        driver.pump()
        QTest.keyClick(escape, Qt.Key.Key_Escape)
        driver.wait(lambda: escaped == [True], "Escape cancellation")

        # Positive timeout closes an abandoned picker.
        monkeypatch.setattr(config, "INTENT_OVERLAY_TIMEOUT_MS", 40)
        timed = IntentOverlay(caller_idx=0)
        overlays.append(timed)
        timed_out: list[bool] = []
        timed.cancelled.connect(lambda: timed_out.append(True))
        timed.show()
        driver.wait(lambda: timed_out == [True], "intent auto-close timeout")

        # Zero disables auto-close; it remains usable until the user cancels.
        monkeypatch.setattr(config, "INTENT_OVERLAY_TIMEOUT_MS", 0)
        persistent = IntentOverlay(caller_idx=0)
        overlays.append(persistent)
        persistent_cancelled: list[bool] = []
        persistent.cancelled.connect(lambda: persistent_cancelled.append(True))
        persistent.show()
        QTest.qWait(100)
        driver.pump()
        assert persistent_cancelled == []
        assert persistent.isVisible()
        QTest.keyClick(persistent, Qt.Key.Key_Escape)
        driver.wait(lambda: persistent_cancelled == [True], "manual close in zero-timeout mode")
    finally:
        config.CALLER_ROWS[:] = old_rows
        for overlay in overlays:
            try:
                overlay.close()
                overlay.deleteLater()
            except RuntimeError:
                pass
        driver.pump()
