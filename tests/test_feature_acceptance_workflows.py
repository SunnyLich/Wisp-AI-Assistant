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
    monkeypatch.setattr(SettingsDialog, "_schedule_open_status_refresh", lambda _self: None)
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


def test_shortcut_settings_search_toggle_record_clear_and_cancel_are_real_ui_actions(
    qapp,
    isolated_settings,
    monkeypatch,
):
    """Exercise every shortcut-row control through the actual Settings widgets."""

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from ui.settings_panel import hotkey_capture
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(hotkey_capture._WindowsKeyCaptureHook, "start", lambda _self: False)
    monkeypatch.setattr(hotkey_capture._WindowsKeyCaptureHook, "stop", lambda _self: None)
    driver = QtUserDriver(qapp)
    dialog = SettingsDialog()
    try:
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("Keybinds"))
        dialog.resize(900, 760)
        dialog.show()
        driver.pump()

        search = dialog._shortcut_search
        driver.replace_text(search, "focused field")
        visible_titles = {
            entry["title"]
            for entry in dialog._shortcut_rows
            if not entry["widget"].isHidden()
        }
        assert visible_titles == {"Hold to dictate"}

        driver.replace_text(search, "context buffer")
        visible_titles = {
            entry["title"]
            for entry in dialog._shortcut_rows
            if not entry["widget"].isHidden()
        }
        assert visible_titles == {"Add selection as context", "Clear context"}
        driver.replace_text(search, "")

        controls = [
            (block["enabled"], block["hotkey"], block["hotkey_2"])
            for block in dialog._caller_blocks
        ]
        controls.extend(
            (
                dialog._fields[f"{key}_ENABLED"],
                dialog._fields[key],
                dialog._fields[f"{key}_2"],
            )
            for key in (
                "HOTKEY_SNIP",
                "HOTKEY_VOICE",
                "HOTKEY_DICTATE",
                "HOTKEY_VOICE_LIVE",
                "HOTKEY_READ_SELECTION_ALOUD",
                "HOTKEY_ADD_CONTEXT",
                "HOTKEY_CLEAR_CONTEXT",
            )
        )
        assert len(controls) == len(dialog._shortcut_rows)
        assert all(enabled.isChecked() for enabled, _primary, _secondary in controls)

        # Toggle every row off and back on. At each step, only that row's two
        # capture fields may change enabled state.
        for index, (enabled, primary, secondary) in enumerate(controls):
            others_before = [other.isChecked() for pos, (other, _p, _s) in enumerate(controls) if pos != index]
            driver.click(enabled)
            assert not enabled.isChecked()
            assert not primary.isEnabled() and not secondary.isEnabled()
            assert [other.isChecked() for pos, (other, _p, _s) in enumerate(controls) if pos != index] == others_before
            driver.click(enabled)
            assert enabled.isChecked()
            assert primary.isEnabled() and secondary.isEnabled()

        edit = dialog._fields["HOTKEY_CLEAR_CONTEXT_2"]
        driver.click(edit)
        QTest.keyClick(
            edit,
            Qt.Key.Key_K,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
        )
        QTest.qWait(120)
        driver.pump()
        assert edit.text() == "ctrl+alt+k"
        assert edit._recording is False

        # Escape is the explicit clear gesture while recording.
        driver.click(edit)
        QTest.keyClick(edit, Qt.Key.Key_Escape)
        driver.pump()
        assert edit.text() == ""
        assert edit._recording is False

        # Leaving the field is cancel: it restores the previous assignment.
        edit.setText("ctrl+alt+k")
        driver.click(edit)
        assert edit._recording is True
        driver.click(search)
        assert edit.text() == "ctrl+alt+k"
        assert edit._recording is False
    finally:
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_intent_shortcut_editor_mutations_policies_tools_and_reopen_are_one_real_workflow(
    qapp,
    isolated_settings,
    monkeypatch,
):
    """Build and persist a caller while exercising every connected editor control."""

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
    from ui.settings_panel.dialog import SettingsDialog
    from ui.settings_panel.tool_access import ToolAccessDialog

    env_path, _settings_env, _theme_calls = isolated_settings
    driver = QtUserDriver(qapp)
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((str(title), str(message)))
        or QMessageBox.StandardButton.Ok,
    )

    def button_with_text(widget, text: str) -> QPushButton:
        return next(button for button in widget.findChildren(QPushButton) if button.text() == text)

    def open_detail(block) -> None:
        if block["detail"].isHidden():
            entry = next(
                row for row in dialog._shortcut_rows if row["detail"] is block["detail"]
            )
            driver.click(button_with_text(entry["widget"], "Customize"))
        assert not block["detail"].isHidden()

    def choose_tool(block, tool: str, mode: str) -> None:
        open_detail(block)
        tool_button = next(
            button
            for button in block["detail"].findChildren(QPushButton)
            if button.text().startswith("Allowed tools")
        )
        modal_checks: list[bool] = []

        def interact() -> None:
            modal = QApplication.activeModalWidget()
            modal_checks.append(isinstance(modal, ToolAccessDialog))
            if not isinstance(modal, ToolAccessDialog):
                return
            combo = modal._combos[tool]
            combo.setCurrentIndex(combo.findData(mode))
            modal.accept()

        QTimer.singleShot(0, interact)
        driver.click(tool_button)
        assert modal_checks == [True]
        assert block["tool_overrides"][tool] == mode

    dialog = SettingsDialog()
    reopened = None
    try:
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("Keybinds"))
        dialog.resize(1000, 820)
        dialog.show()
        driver.pump()
        _unique_shortcuts(dialog)
        original_count = len(dialog._caller_blocks)

        add_button = next(
            button for button in dialog.findChildren(QPushButton) if button.text() == "Add intent shortcut"
        )
        driver.click(add_button)
        assert len(dialog._caller_blocks) == original_count + 1
        block = dialog._caller_blocks[-1]
        open_detail(block)

        driver.replace_text(block["label"], "Research")
        block["hotkey"].setText("ctrl+alt+r")
        block["hotkey_2"].setText("ctrl+shift+alt+r")
        assert block["title_label"].text() == "Research"

        add_choice = button_with_text(block["detail"], "Add choice")
        driver.click(add_choice)
        first = block["intent_rows"][0]
        driver.replace_text(first["key"], "r")
        driver.replace_text(first["label"], "Research selection")
        driver.replace_text(first["prompt"], "Research this selection and summarize the findings.")

        # Edit the choice, add a second one, then remove only the second.
        driver.replace_text(first["key"], "g")
        driver.replace_text(first["label"], "Gather sources")
        driver.replace_text(first["prompt"], "Gather reliable sources for this selection.")
        driver.click(add_choice)
        second = block["intent_rows"][1]
        driver.replace_text(second["key"], "x")
        driver.replace_text(second["label"], "Temporary")
        driver.replace_text(second["prompt"], "Temporary prompt")
        driver.click(button_with_text(second["widget"], "X"))
        assert block["intent_rows"] == [first]

        driver.replace_text(block["custom_key"], "c")
        driver.replace_text(block["custom_label"], "Custom research")
        driver.click(block["paste_back"])
        assert block["paste_back"].isChecked()

        driver.select_combo_data(block["context_documents_mode"], "auto")
        driver.select_combo_data(block["context_clipboard"], "true")
        driver.select_combo_data(block["context_browser_mode"], "model")
        driver.select_combo_data(block["context_github_mode"], "model")
        driver.select_combo_data(block["context_memory_mode"], "on")
        driver.select_combo_data(block["context_screenshot"], "auto")
        driver.select_combo_data(block["file_access"], "read")
        choose_tool(block, "read_file", "off")
        choose_tool(dialog._voice_block, "edit_file", "on")
        choose_tool(dialog._snip_block, "list_files", "on")

        driver.select_combo_data(dialog._fields["DICTATE_MODE"], "raw")
        driver.select_combo_data(dialog._fields["DICTATE_MODE"], "llm")

        # A duplicate primary/secondary assignment must be rejected visibly.
        block["hotkey_2"].setText(dialog._caller_blocks[0]["hotkey"].text())
        assert dialog._do_save() is False
        assert warnings and warnings[-1][0] == "Duplicate keys"
        block["hotkey_2"].setText("ctrl+shift+alt+r")

        # Add and remove another caller through its visible controls; Research
        # must remain intact and in the same position.
        driver.click(add_button)
        temporary = dialog._caller_blocks[-1]
        open_detail(temporary)
        driver.click(button_with_text(temporary["detail"], "Remove intent shortcut"))
        driver.pump()
        assert len(dialog._caller_blocks) == original_count + 1
        assert dialog._caller_blocks[-1] is block

        apply_button = dialog.findChild(QPushButton, "settingsApplyButton")
        driver.click(apply_button)
        assert "CALLER_3_LABEL=Research" in env_path.read_text(encoding="utf-8")

        dialog.close()
        dialog.deleteLater()
        driver.pump()
        dialog = None
        reopened = SettingsDialog()
        saved = reopened._caller_blocks[-1]
        assert saved["label"].text() == "Research"
        assert saved["hotkey"].text() == "ctrl+alt+r"
        assert saved["hotkey_2"].text() == "ctrl+shift+alt+r"
        assert saved["paste_back"].isChecked()
        assert len(saved["intent_rows"]) == 1
        assert saved["intent_rows"][0]["key"].text() == "g"
        assert saved["intent_rows"][0]["label"].text() == "Gather sources"
        assert saved["intent_rows"][0]["prompt"].toPlainText() == "Gather reliable sources for this selection."
        assert saved["custom_key"].text() == "c"
        assert saved["custom_label"].text() == "Custom research"
        assert saved["context_documents_mode"].currentData() == "auto"
        assert saved["context_browser_mode"].currentData() == "model"
        assert saved["tool_overrides"]["read_file"] == "off"
        assert reopened._voice_block["tool_overrides"]["edit_file"] == "on"
        assert reopened._snip_block["tool_overrides"]["list_files"] == "on"
        assert reopened._fields["DICTATE_MODE"].currentData() == "llm"
    finally:
        if reopened is not None:
            reopened.close()
            reopened.deleteLater()
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        driver.pump()


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


def test_every_app_and_assistant_language_pair_runs_every_localized_builtin_intent(qapp):
    """Exercise all 6 x 12 language states through all six real picker actions."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    import config
    from core.prompt_i18n import caller_intent_template, localize_intent_if_default
    from ui import i18n
    from ui.i18n import LANGUAGE_OPTIONS
    from ui.intent_overlay import IntentOverlay
    from ui.settings_panel import dialog as settings_dialog

    driver = QtUserDriver(qapp, timeout=2.0)
    original_rows = list(config.CALLER_ROWS)
    original_app_language = getattr(config, "APP_LANGUAGE", "")
    original_assistant_language = getattr(config, "ASSISTANT_LANGUAGE", "")
    app_languages = [value for _label, value in LANGUAGE_OPTIONS]
    assistant_languages = [
        value for _label, value in settings_dialog._ASSISTANT_LANGUAGE_OPTIONS
    ]
    exercised = []

    try:
        for app_language in app_languages:
            config.APP_LANGUAGE = app_language
            i18n.set_language(app=qapp)
            display_language = i18n.current_language()
            for assistant_language in assistant_languages:
                config.ASSISTANT_LANGUAGE = assistant_language
                config.CALLER_ROWS[:] = [
                    {
                        "intents": [
                            localize_intent_if_default(
                                caller_idx,
                                intent_idx,
                                caller_intent_template(caller_idx, intent_idx, "English"),
                                assistant_language,
                            )
                            for intent_idx in range(3)
                        ],
                        "custom_key": "s",
                        "custom_label": "",
                    }
                    for caller_idx in range(2)
                ]

                for caller_idx in range(2):
                    for intent_idx in range(3):
                        expected_display = caller_intent_template(
                            caller_idx,
                            intent_idx,
                            display_language,
                        )
                        expected_prompt = caller_intent_template(
                            caller_idx,
                            intent_idx,
                            assistant_language,
                        )["prompt"]
                        overlay = IntentOverlay(caller_idx=caller_idx)
                        chosen = []
                        overlay.intent_chosen.connect(
                            lambda key, prompt: chosen.append((key, prompt))
                        )
                        try:
                            overlay.show()
                            driver.pump()
                            row = overlay._rows[intent_idx]
                            assert row["label"] == expected_display["label"]
                            assert row["hint"] == expected_display["hint"]
                            assert row["prompt"] == expected_prompt
                            QTest.mouseClick(
                                overlay,
                                Qt.MouseButton.LeftButton,
                                pos=overlay._row_rects[intent_idx].center(),
                            )
                            driver.wait(
                                lambda: bool(chosen),
                                f"localized intent {app_language}/{assistant_language}/"
                                f"{caller_idx}/{intent_idx}",
                            )
                            assert chosen == [(row["glyph"], expected_prompt)]
                            exercised.append(
                                (app_language, assistant_language, caller_idx, intent_idx)
                            )
                        finally:
                            try:
                                overlay.close()
                                overlay.deleteLater()
                            except RuntimeError:
                                pass
                            driver.pump()
    finally:
        config.CALLER_ROWS[:] = original_rows
        config.APP_LANGUAGE = original_app_language
        config.ASSISTANT_LANGUAGE = original_assistant_language
        i18n.set_language(app=qapp)

    assert len(exercised) == len(app_languages) * len(assistant_languages) * 2 * 3


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


def test_conversation_engine_and_owner_settings_drive_runtime_dispatch_matrix(
    qapp,
    live_runtime_settings,
    monkeypatch,
):
    """Save every valid engine/owner pairing, then use it for a real brain turn."""

    from PySide6.QtWidgets import QPushButton

    import config
    from core import harness_clients
    from core.harness_clients.base import HarnessResult
    from ui.settings_panel.dialog import SettingsDialog, _set

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "runtime" / "brain"))
    from wisp_brain import handlers

    driver = QtUserDriver(qapp, timeout=2.0)
    harness_calls = []

    def fake_harness(provider, prompt, **kwargs):
        harness_calls.append((provider, prompt, dict(kwargs)))
        return HarnessResult(provider, f"{provider} answer", "new-session", "/repo")

    monkeypatch.setattr(harness_clients, "run_harness", fake_harness)
    monkeypatch.setattr(
        handlers,
        "_stream_chat_reply",
        lambda *_args, **_kwargs: iter(["wisp answer"]),
    )

    scenarios = [
        ("wisp", "wisp", "wisp"),
        ("wisp", "agent", "wisp"),
        ("codex", "wisp", "wisp"),
        ("codex", "agent", "agent"),
        ("claude", "wisp", "wisp"),
        ("claude", "agent", "agent"),
    ]
    for index, (provider, requested_owner, effective_owner) in enumerate(scenarios):
        dialog = SettingsDialog()
        try:
            _unique_shortcuts(dialog)
            _set(dialog._fields["CHAT_EXECUTION_MODE"], provider)
            driver.pump()
            owner_field = dialog._fields["CHAT_CONVERSATION_OWNER"]
            assert owner_field.isEnabled() is (provider != "wisp")
            if owner_field.isEnabled():
                _set(owner_field, requested_owner)
            else:
                assert requested_owner in {"wisp", "agent"}
                assert owner_field.currentData() == "wisp"
            dialog._fields["SYSTEM_PROMPT_UTILITY"].setPlainText(
                f"Wisp runtime prompt {index}."
            )
            driver.pump()

            save = dialog.findChild(QPushButton, "settingsApplyButton")
            assert save is not None and save.isEnabled()
            driver.click(save)

            assert config.CHAT_EXECUTION_MODE == provider
            assert config.CHAT_CONVERSATION_OWNER == effective_owner
            assert f"Wisp runtime prompt {index}." in config.get_system_prompt()

            # Keep this workflow offline after the real Settings reload. The
            # selected Wisp route/agent adapter and ownership logic remain real.
            config.TRUST_PRIVACY_MODE = False
            harness_calls.clear()
            events = []
            ctx = handlers.StreamContext(
                lambda event, data, request_id: events.append((event, data, request_id)),
                269,
            )
            result = handlers.HANDLERS["brain.chat"](
                ctx,
                messages=[
                    {"role": "user", "content": "Earlier question"},
                    {"role": "assistant", "content": "Earlier answer"},
                    {"role": "user", "content": "Continue now"},
                ],
                memory_enabled=False,
                harness_session={
                    "provider": provider,
                    "session_id": "old-session",
                    "cwd": "/repo",
                },
            )

            if provider == "wisp":
                assert harness_calls == []
                assert result["text"] == "wisp answer"
                assert "harness" not in result
            else:
                assert len(harness_calls) == 1
                selected, prompt, kwargs = harness_calls[0]
                assert selected == provider
                assert kwargs["session_id"] == (
                    "old-session" if effective_owner == "agent" else ""
                )
                assert ("Earlier question" in prompt) is (effective_owner == "wisp")
                assert prompt.endswith("Continue now")
                assert result["text"] == f"{provider} answer"
                assert result["harness"]["conversation_owner"] == effective_owner
                assert result["harness"]["session_id"] == (
                    "new-session" if effective_owner == "agent" else ""
                )
                assert result["harness"]["clear_session"] is (effective_owner == "wisp")
            assert [
                data["text"] for event, data, _request_id in events if event == "reply.done"
            ] == [result["text"]]
        finally:
            dialog.close()
            dialog.deleteLater()
            driver.pump()


def test_tts_volume_speed_and_read_aloud_chunk_settings_drive_audio_runtime(
    qapp,
    live_runtime_settings,
    monkeypatch,
):
    """Save playback controls, then consume them in the audio and read-aloud paths."""

    import sys
    import types

    import numpy as np
    from PySide6.QtWidgets import QPushButton

    import config
    from core import audio_state
    from runtime.supervisor.flows import FlowController
    from runtime.workers import audio_host
    from ui.settings_panel.dialog import SettingsDialog

    driver = QtUserDriver(qapp, timeout=2.0)
    dialog = SettingsDialog()
    try:
        _unique_shortcuts(dialog)
        dialog._fields["TTS_VOLUME"].setValue(25)
        dialog._fields["TTS_PLAYBACK_RATE"].setText("1.25")
        dialog._fields["TTS_HOLD_PLAYBACK_RATE"].setText("1.80")
        dialog._fields["TTS_READ_ALOUD_MIN_WORDS"].setText("2")
        dialog._fields["TTS_READ_ALOUD_MAX_WORDS"].setText("4")
        driver.pump()

        save = dialog.findChild(QPushButton, "settingsApplyButton")
        assert save is not None and save.isEnabled()
        driver.click(save)

        assert config.TTS_VOLUME == 0.25
        assert config.TTS_PLAYBACK_RATE == 1.25
        assert config.TTS_HOLD_PLAYBACK_RATE == 1.80
        assert config.TTS_READ_ALOUD_MIN_WORDS == 2
        assert config.TTS_READ_ALOUD_MAX_WORDS == 4
        assert FlowController._read_aloud_chunks(
            "one two. three four five six seven"
        ) == ["one two.", "three four five six", "seven"]
        assert FlowController._read_aloud_chunks("one.") == ["one."]
        assert FlowController._read_aloud_chunks("one two three four") == [
            "one two three four"
        ]

        audio_state.set_tts_speed_boost(False)
        assert audio_host._current_tts_rate() == 1.25
        audio_host.audio_speed_boost(True)
        assert audio_host._current_tts_rate() == 1.80
        audio_host.audio_speed_boost(False)

        source = np.array([0.8, 0.4, -0.4, -0.8], dtype=np.float32)
        writes = []

        class FakeOutputStream:
            def __init__(self, *, samplerate, channels, dtype):
                assert samplerate == 22_050
                assert channels == 1
                assert dtype == "float32"

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def write(self, data):
                writes.append(np.array(data, copy=True))

        monkeypatch.setitem(
            sys.modules,
            "sounddevice",
            types.SimpleNamespace(OutputStream=FakeOutputStream, stop=lambda: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "soundfile",
            types.SimpleNamespace(read=lambda _path, dtype="float32": (source, 22_050)),
        )

        for volume in (0.0, 0.25, 1.0, 1.5):
            config.TTS_VOLUME = volume
            writes.clear()
            assert audio_host.play_file("saved-volume.wav") == {
                "played": True,
                "stopped": False,
            }
            scaled = np.clip(source * volume, -1.0, 1.0).astype("float32")
            expected = audio_host._speed_adjust_float_audio(scaled, 1.25)
            assert np.allclose(np.concatenate(writes), expected)
    finally:
        audio_state.set_tts_speed_boost(False)
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_visible_test_tts_button_forwards_every_provider_configuration(
    qapp,
    live_runtime_settings,
    monkeypatch,
):
    """Click Test TTS for every provider and each Kokoro device choice."""

    from PySide6.QtWidgets import QPushButton

    import config
    from core import tts
    from ui.settings_panel.dialog import SettingsDialog, _set

    driver = QtUserDriver(qapp, timeout=2.0)
    dialog = SettingsDialog()
    calls = []

    def fake_test_connection(provider, **kwargs):
        calls.append((provider, dict(kwargs)))
        return True, f"TTS route OK: {provider}"

    monkeypatch.setattr(tts, "test_connection", fake_test_connection)
    try:
        dialog.show()
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("TTS / Voice"))
        dialog._show_voice_feature("tts")
        driver.pump()
        _unique_shortcuts(dialog)
        dialog._fields["CARTESIA_API_KEY"].setText("test-cartesia-key")
        dialog._fields["CARTESIA_VOICE_ID"].setText("cartesia-voice")
        dialog._fields["ELEVENLABS_API_KEY"].setText("test-elevenlabs-key")
        dialog._fields["ELEVENLABS_VOICE_ID"].setText("elevenlabs-voice")
        dialog._fields["ELEVENLABS_MODEL"].setText("elevenlabs-model")
        dialog._fields["OPENAI_TTS_VOICE"].setText("nova")
        dialog._fields["OPENAI_TTS_MODEL"].setText("gpt-4o-mini-tts")
        dialog._fields["TTS_CUSTOM_BASE_URL"].setText("https://speech.example/v1")
        dialog._fields["TTS_CUSTOM_API_KEY"].setText("test-custom-speech-key")
        dialog._fields["TTS_CUSTOM_VOICE"].setText("custom-voice")
        dialog._fields["TTS_CUSTOM_MODEL"].setText("custom-model")
        dialog._fields["TTS_CUSTOM_SAMPLE_RATE"].setText("16000")
        dialog._fields["GPT_SOVITS_URL"].setText("http://127.0.0.1:9880")
        dialog._fields["GPT_SOVITS_REF_AUDIO_PATH"].setText("C:/voices/reference.wav")
        dialog._fields["GPT_SOVITS_PROMPT_TEXT"].setText("reference transcript")
        _set(dialog._fields["GPT_SOVITS_PROMPT_LANG"], "en")
        _set(dialog._fields["GPT_SOVITS_TEXT_LANG"], "zh")
        dialog._fields["GPT_SOVITS_SAMPLE_RATE"].setText("32000")
        dialog._fields["KOKORO_VOICE"].setText("af_heart")
        dialog._fields["KOKORO_LANG_CODE"].setText("a")
        dialog._fields["KOKORO_SPEED"].setText("1.10")
        dialog._fields["KOKORO_SAMPLE_RATE"].setText("24000")
        openai_row = dialog._add_api_key_row("openai", alias="Speech")
        openai_row["key"].setText("test-openai-speech-key")

        scenarios = [
            ("cartesia", None),
            ("elevenlabs", None),
            ("openai", None),
            ("openai_compatible", None),
            ("gpt_sovits", None),
            ("kokoro", "auto"),
            ("kokoro", "cpu"),
            ("kokoro", "cuda"),
        ]
        test_button = dialog._tts_test_row.findChild(QPushButton)
        assert test_button is not None and test_button.text() == "Test TTS"
        for provider, device in scenarios:
            _set(dialog._fields["TTS_PROVIDER"], provider)
            if device is not None:
                _set(dialog._fields["KOKORO_DEVICE"], device)
            driver.pump()
            assert test_button.isVisible()
            expected_count = len(calls) + 1
            driver.click(test_button)
            driver.wait(lambda: len(calls) >= expected_count, f"{provider}/{device} TTS call")
            driver.wait(
                lambda: dialog._tts_test_status_lbl.text() == f"TTS route OK: {provider}",
                f"{provider}/{device} TTS result",
            )

        by_state = {(provider, kwargs.get("kokoro_device")): kwargs for provider, kwargs in calls}
        assert by_state[("cartesia", "auto")]["cartesia_api_key"] == "test-cartesia-key"
        assert by_state[("cartesia", "auto")]["cartesia_voice_id"] == "cartesia-voice"
        assert by_state[("elevenlabs", "auto")]["elevenlabs_api_key"] == "test-elevenlabs-key"
        assert by_state[("elevenlabs", "auto")]["elevenlabs_voice_id"] == "elevenlabs-voice"
        assert by_state[("elevenlabs", "auto")]["elevenlabs_model"] == "elevenlabs-model"
        assert by_state[("openai", "auto")]["openai_api_key"] == "test-openai-speech-key"
        assert by_state[("openai", "auto")]["openai_voice"] == "nova"
        assert by_state[("openai", "auto")]["openai_model"] == "gpt-4o-mini-tts"
        assert by_state[("openai_compatible", "auto")]["custom_base_url"] == "https://speech.example/v1"
        assert by_state[("openai_compatible", "auto")]["custom_voice"] == "custom-voice"
        assert by_state[("openai_compatible", "auto")]["custom_model"] == "custom-model"
        assert by_state[("gpt_sovits", "auto")]["gpt_sovits_ref_audio_path"] == "C:/voices/reference.wav"
        assert by_state[("gpt_sovits", "auto")]["gpt_sovits_prompt_text"] == "reference transcript"
        assert by_state[("gpt_sovits", "auto")]["gpt_sovits_text_lang"] == "zh"
        for device in ("auto", "cpu", "cuda"):
            assert by_state[("kokoro", device)]["kokoro_voice"] == "af_heart"
            assert by_state[("kokoro", device)]["kokoro_lang_code"] == "a"

        save = dialog.findChild(QPushButton, "settingsApplyButton")
        assert save is not None and save.isEnabled()
        driver.click(save)
        assert config.TTS_PROVIDER == "kokoro"
        assert config.TTS_CUSTOM_SAMPLE_RATE == 16000
        assert config.GPT_SOVITS_SAMPLE_RATE == 32000
        assert config.KOKORO_DEVICE == "cuda"
        assert config.KOKORO_SPEED == 1.10
        assert config.KOKORO_SAMPLE_RATE == 24000
    finally:
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_visible_speech_install_and_kokoro_asset_actions_reach_runtime_boundaries(
    qapp,
    live_runtime_settings,
    monkeypatch,
):
    """Click the real provider install, repair, and update controls."""

    from PySide6.QtWidgets import QMessageBox

    from core import optional_deps, tts, tts_assets
    from ui.settings_panel.dialog import SettingsDialog, _set

    driver = QtUserDriver(qapp, timeout=2.0)
    installs: list[dict[str, object]] = []
    asset_calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(SettingsDialog, "_refresh_tts_optional_install_status", lambda _self: None)
    monkeypatch.setattr(SettingsDialog, "_elevenlabs_installed", lambda _self: False)
    monkeypatch.setattr(
        SettingsDialog,
        "_kokoro_install_snapshot",
        lambda _self: {
            "installed": False,
            "needs_gpu": False,
            "needs_repair": False,
            "mode": "cpu",
        },
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_install_optional_tts_package",
        lambda _self, **kwargs: installs.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        tts,
        "prepare_kokoro_assets",
        lambda voice: asset_calls.append(("repair", voice)) or {"voice": "voice.pt"},
    )
    monkeypatch.setattr(
        tts_assets,
        "apply_update",
        lambda manifest, revision, *, voices: asset_calls.append(
            ("update", (manifest, revision, tuple(voices)))
        ),
    )
    monkeypatch.setattr(tts, "reset_connections", lambda: asset_calls.append(("reset", None)))
    dialog = SettingsDialog()

    try:
        dialog.show()
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("TTS / Voice"))
        dialog._show_voice_feature("tts")
        driver.pump()

        _set(dialog._fields["TTS_PROVIDER"], "elevenlabs")
        dialog._apply_elevenlabs_install_status(False)
        driver.pump()
        assert dialog._elevenlabs_install_btn.isVisible()
        assert dialog._elevenlabs_install_btn.text() == "Install ElevenLabs"
        driver.click(dialog._elevenlabs_install_btn)
        assert len(installs) == 1
        elevenlabs_plan = installs[-1]
        assert elevenlabs_plan["packages"] == [optional_deps.ELEVENLABS_PACKAGE]
        assert elevenlabs_plan["reinstall"] is False
        assert elevenlabs_plan["external_plan_extra"]["settings_updates"] == {
            "TTS_PROVIDER": "elevenlabs",
            "WISP_TTS_PREFERENCE": "cloud",
        }
        assert dialog._fields["TTS_PROVIDER"].currentData() == "elevenlabs"

        _set(dialog._fields["TTS_PROVIDER"], "kokoro")
        _set(dialog._fields["KOKORO_DEVICE"], "cpu")
        dialog._fields["KOKORO_VOICE"].setText("af_heart")
        dialog._apply_kokoro_install_status(
            installed=False,
            mode="cpu",
            torch_status={},
            needs_gpu=False,
        )
        driver.pump()
        assert dialog._kokoro_install_btn.isVisible()
        assert dialog._kokoro_install_btn.text() == "Install Kokoro"
        driver.click(dialog._kokoro_install_btn)
        assert len(installs) == 2
        kokoro_plan = installs[-1]
        assert kokoro_plan["packages"] == optional_deps.kokoro_install_packages("cpu")
        assert kokoro_plan["pre_install_packages"] == []
        assert kokoro_plan["reinstall"] is False
        assert kokoro_plan["external_plan_extra"]["settings_updates"] == {
            "TTS_PROVIDER": "kokoro",
            "WISP_TTS_PREFERENCE": "local",
            "KOKORO_VOICE": "af_heart",
            "KOKORO_LANG_CODE": "a",
            "KOKORO_DEVICE": "cpu",
        }

        dialog._apply_kokoro_assets_status(
            installed=True,
            assets={"state": "damaged", "problems": ["model hash mismatch"]},
        )
        driver.pump()
        assert dialog._kokoro_assets_btn.isVisible()
        assert dialog._kokoro_assets_btn.text() == "Repair voice files"
        driver.click(dialog._kokoro_assets_btn)
        driver.wait(lambda: ("repair", "af_heart") in asset_calls, "Kokoro repair")
        driver.wait(
            lambda: dialog._kokoro_install_status_lbl.text() == "Kokoro voice files repaired.",
            "Kokoro repair result",
        )
        assert dialog._kokoro_assets_btn.isEnabled()

        dialog._apply_kokoro_assets_status(
            installed=True,
            assets={"state": "ready", "update_revision": "revision-2"},
        )
        driver.pump()
        assert dialog._kokoro_assets_btn.text() == "Update voice model"
        driver.click(dialog._kokoro_assets_btn)
        driver.wait(
            lambda: any(kind == "update" for kind, _value in asset_calls),
            "Kokoro model update",
        )
        driver.wait(
            lambda: dialog._kokoro_install_status_lbl.text() == "Kokoro voice model updated.",
            "Kokoro update result",
        )
        update = next(value for kind, value in asset_calls if kind == "update")
        assert update[0] is tts_assets.KOKORO
        assert update[1:] == ("revision-2", ("af_heart",))
        assert asset_calls[-1] == ("reset", None)
        assert dialog._kokoro_assets_btn.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_visible_stt_install_button_covers_every_model_device_compute_language_and_beam(
    qapp,
    live_runtime_settings,
    monkeypatch,
):
    """Drive every STT selector state through the visible install action."""

    from itertools import product

    from PySide6.QtWidgets import QMessageBox

    from core import optional_deps
    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog, _set

    plans: list[dict[str, object]] = []
    monkeypatch.setattr(SettingsDialog, "_refresh_stt_active_backend", lambda _self: None)
    monkeypatch.setattr(SettingsDialog, "_refresh_tts_optional_install_status", lambda _self: None)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_install_optional_tts_package",
        lambda _self, **kwargs: plans.append(dict(kwargs)),
    )
    driver = QtUserDriver(qapp, timeout=2.0)
    dialog = SettingsDialog()
    try:
        dialog.show()
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("TTS / Voice"))
        dialog._show_voice_feature("stt")
        driver.pump()
        assert dialog._stt_download_btn.isVisible()
        assert dialog._stt_download_btn.text() == "Install STT"

        model_values = [
            dialog._fields["STT_MODEL"].itemData(index)
            for index in range(dialog._fields["STT_MODEL"].count())
        ]
        device_values = [
            dialog._fields["STT_DEVICE"].itemData(index)
            for index in range(dialog._fields["STT_DEVICE"].count())
        ]
        compute_values = [
            dialog._fields["STT_COMPUTE_TYPE"].itemData(index)
            for index in range(dialog._fields["STT_COMPUTE_TYPE"].count())
        ]
        beam_values = [
            dialog._fields["STT_BEAM_SIZE"].itemData(index)
            for index in range(dialog._fields["STT_BEAM_SIZE"].count())
        ]
        for model in model_values:
            _set(dialog._fields["STT_MODEL"], model)
            driver.pump()
            assert (dialog._fields["STT_LANGUAGE"].findData("yue") >= 0) is (
                model == "large-v3"
            )

        cases: set[tuple[str, str, str, str, str]] = set()
        cases.update((model, "cpu", "int8", "", "5") for model in model_values)
        cases.update(
            ("base", device, compute, "en", "3")
            for device, compute in product(device_values, compute_values)
        )
        cases.update(
            ("large-v3", "cpu", "int8", language, "1")
            for _label, language in dialog_mod._STT_LANGUAGE_OPTIONS
        )
        cases.update(("base", "cpu", "int8", "en", beam) for beam in beam_values)

        cases = {
            case
            for case in cases
            if case[3] != "yue" or case[0] == "large-v3"
        }
        expected_cases = set(cases)
        for model, device, compute, language, beam in sorted(cases):
            _set(dialog._fields["STT_MODEL"], model)
            _set(dialog._fields["STT_DEVICE"], device)
            _set(dialog._fields["STT_COMPUTE_TYPE"], compute)
            _set(dialog._fields["STT_LANGUAGE"], language)
            _set(dialog._fields["STT_BEAM_SIZE"], beam)
            driver.pump()
            driver.click(dialog._stt_download_btn)

        observed_cases = set()
        for plan in plans:
            updates = plan["external_plan_extra"]["settings_updates"]
            case = (
                plan["external_plan_extra"]["stt_model"],
                plan["external_plan_extra"]["stt_device"],
                plan["external_plan_extra"]["stt_compute_type"],
                updates["STT_LANGUAGE"],
                updates["STT_BEAM_SIZE"],
            )
            observed_cases.add(case)
            assert plan["packages"] == optional_deps.stt_install_packages(case[1])
            assert plan["remove_artifacts"] == optional_deps.stt_remove_artifacts()
            assert updates["WISP_STT_PREFERENCE"] == "local"
        assert observed_cases == expected_cases
        assert {case[0] for case in observed_cases} == set(model_values)
        assert {case[1] for case in observed_cases} == set(device_values)
        assert {case[2] for case in observed_cases} == set(compute_values)
        assert {case[3] for case in observed_cases} == {
            value for _label, value in dialog_mod._STT_LANGUAGE_OPTIONS
        }
        assert {case[4] for case in observed_cases} == set(beam_values)
    finally:
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_stt_runtime_consumes_every_model_device_compute_language_and_beam(
    monkeypatch,
):
    """Run the production STT loader and transcriber across every saved state."""

    from itertools import product
    import sys
    import types

    import numpy as np

    import config
    from core import optional_deps, stt_device
    from core.macos_helper import handlers
    from ui.settings_panel import dialog as dialog_mod

    constructor_calls: list[tuple[str, str, str]] = []

    class FakeWhisperModel:
        def __init__(self, model, *, device, compute_type):
            constructor_calls.append((model, device, compute_type))

        def transcribe(self, _audio, **_kwargs):
            return [], None

    fake_faster_whisper = types.ModuleType("faster_whisper")
    fake_faster_whisper.WhisperModel = FakeWhisperModel
    fake_ctranslate2 = types.SimpleNamespace(
        __version__="test",
        get_cuda_device_count=lambda: 1,
        get_supported_compute_types=lambda _device, _index: {
            "int8",
            "int8_float16",
            "float16",
            "float32",
        },
    )
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_faster_whisper)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ctranslate2)
    monkeypatch.setattr(optional_deps, "require_optional_package_runtime", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        stt_device,
        "windows_cuda_runtime_status",
        lambda: {"checked": True, "valid": True, "errors": {}},
    )

    old_model = handlers._model
    old_ready = handlers._model_ready
    try:
        models = [value for _label, value, _translation in dialog_mod._STT_MODEL_OPTIONS]
        devices = [value for _label, value in dialog_mod._STT_DEVICE_OPTIONS]
        computes = [value for _label, value in dialog_mod._STT_COMPUTE_OPTIONS]
        for model, device, compute in product(models, devices, computes):
            monkeypatch.setattr(config, "STT_MODEL", model)
            monkeypatch.setattr(config, "STT_DEVICE", device)
            monkeypatch.setattr(config, "STT_COMPUTE_TYPE", compute)
            handlers._model = None
            handlers._model_ready = False
            handlers._get_model()
            effective_device = "cpu" if device == "cpu" else "cuda"
            effective_compute = (
                "int8"
                if effective_device == "cpu" and compute in {"float16", "int8_float16"}
                else compute
            )
            assert constructor_calls[-1] == (model, effective_device, effective_compute)
            assert handlers._model_ready is True

        transcript_calls: list[dict[str, object]] = []

        class FakeTranscriber:
            def transcribe(self, _audio, **kwargs):
                transcript_calls.append(dict(kwargs))
                return [types.SimpleNamespace(text=" exact transcript ")], None

        monkeypatch.setattr(handlers, "_get_model", lambda: FakeTranscriber())
        languages = [value for _label, value in dialog_mod._STT_LANGUAGE_OPTIONS]
        beams = [int(value) for _label, value in dialog_mod._STT_BEAM_OPTIONS]
        audio = np.ones(int(0.5 * handlers._SAMPLE_RATE), dtype="float32")
        for language, beam in product(languages, beams):
            monkeypatch.setattr(config, "STT_LANGUAGE", language)
            monkeypatch.setattr(config, "STT_BEAM_SIZE", beam)
            assert handlers._transcribe_audio(audio, label="acceptance") == "exact transcript"
            assert transcript_calls[-1] == {
                "beam_size": beam,
                "language": language or None,
                "vad_filter": True,
            }
    finally:
        handlers._model = old_model
        handlers._model_ready = old_ready


def test_saved_background_stt_chunk_settings_drive_live_overlapping_windows(
    qapp,
    live_runtime_settings,
    monkeypatch,
):
    """Save custom chunk timing and run the live background-window scheduler."""

    import threading

    import numpy as np
    from PySide6.QtWidgets import QPushButton

    import config
    from core.macos_helper import handlers
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(SettingsDialog, "_refresh_stt_active_backend", lambda _self: None)
    driver = QtUserDriver(qapp, timeout=2.0)
    dialog = SettingsDialog()
    calls: list[tuple[str, int]] = []
    stop_event = threading.Event()
    old_chunks = list(handlers._chunks)
    old_recording = handlers._recording
    old_results = list(handlers._stt_bg_results)
    try:
        _unique_shortcuts(dialog)
        for key, value in {
            "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS": "20",
            "STT_BACKGROUND_CHUNK_STEP_SECONDS": "7",
            "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS": "3",
            "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS": "2",
        }.items():
            driver.replace_text(dialog._fields[key], value)
        save = dialog.findChild(QPushButton, "settingsApplyButton")
        assert save is not None and save.isEnabled()
        driver.click(save)
        assert config.STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS == 20.0
        assert config.STT_BACKGROUND_CHUNK_STEP_SECONDS == 7.0
        assert config.STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS == 3.0
        assert config.STT_BACKGROUND_CHUNK_OVERLAP_SECONDS == 2.0

        handlers._recording = True
        handlers._stt_bg_results.clear()
        handlers._chunks[:] = [
            np.ones((int(31.0 * handlers._SAMPLE_RATE), 1), dtype="float32")
        ]

        def fake_transcribe(audio, *, label):
            calls.append((label, len(audio)))
            if len(calls) == 2:
                stop_event.set()
            return f"chunk {len(calls)}"

        monkeypatch.setattr(handlers, "_transcribe_audio", fake_transcribe)
        handlers._stt_background_worker(stop_event)
        sample_rate = handlers._SAMPLE_RATE
        assert calls == [
            ("background 0.0-17.0s", int(17.0 * sample_rate)),
            ("background 15.0-24.0s", int(9.0 * sample_rate)),
        ]
        assert [item["text"] for item in handlers._stt_bg_results] == ["chunk 1", "chunk 2"]
    finally:
        handlers._chunks[:] = old_chunks
        handlers._recording = old_recording
        handlers._stt_bg_results[:] = old_results
        dialog.close()
        dialog.deleteLater()
        driver.pump()


def test_visible_live_voice_install_and_saved_session_configuration_reach_audio_runtime(
    qapp,
    live_runtime_settings,
    monkeypatch,
):
    """Install live voice, save both duplex modes, and start the audio session."""

    from itertools import product

    from PySide6.QtWidgets import QMessageBox, QPushButton

    import config
    from core import live_voice, optional_deps
    from core.macos_helper import handlers as stt_handlers
    from runtime.workers import audio_host
    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog, _set

    installs: list[dict[str, object]] = []
    sessions = []
    events: list[tuple[str, dict]] = []

    class FakeLiveSession:
        def __init__(self, cfg, emit):
            self.cfg = cfg
            self.emit = emit
            self.started = False
            self._active = True
            self.stop_reasons = []
            sessions.append(self)

        def start(self):
            self.started = True

        def request_stop(self, reason="user"):
            self.stop_reasons.append(reason)

        def join(self, _timeout=None):
            self._active = False
            return True

        @property
        def is_active(self):
            return self._active

        @property
        def state(self):
            return "listening" if self._active else "idle"

    monkeypatch.setattr(SettingsDialog, "_refresh_live_voice_install_status", lambda _self: None)
    monkeypatch.setattr(SettingsDialog, "_refresh_tts_optional_install_status", lambda _self: None)
    monkeypatch.setattr(SettingsDialog, "_live_voice_installed", lambda _self: False)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_install_optional_tts_package",
        lambda _self, **kwargs: installs.append(dict(kwargs)),
    )
    monkeypatch.setattr(live_voice, "genai_available", lambda: True)
    monkeypatch.setattr(live_voice, "LiveVoiceSession", FakeLiveSession)
    monkeypatch.setattr(stt_handlers, "stt_is_recording", lambda: False)
    audio_host._live_session = None
    audio_host.set_event_sink(lambda name, data, _request_id: events.append((name, dict(data))))

    driver = QtUserDriver(qapp, timeout=2.0)
    dialog = SettingsDialog()

    def set_model(value: str) -> None:
        row = dialog._live_voice_model_row
        if value == "custom-live-model":
            driver.select_combo_data(row["model_combo"], dialog_mod._CUSTOM_MODEL_SENTINEL)
            driver.replace_text(row["model_edit"], value)
        else:
            driver.select_combo_data(row["model_combo"], value)

    def set_voice(value: str) -> None:
        row = dialog._live_voice_voice_row
        if value == "CustomVoice":
            driver.select_combo_data(row["model_combo"], dialog_mod._CUSTOM_MODEL_SENTINEL)
            driver.replace_text(row["model_edit"], value)
        else:
            driver.select_combo_data(row["model_combo"], value)

    try:
        dialog.show()
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("TTS / Voice"))
        dialog._show_voice_feature("live")
        driver.pump()
        assert dialog._live_voice_install_btn.isVisible()
        assert dialog._live_voice_install_btn.text() == "Install live voice"
        driver.click(dialog._live_voice_install_btn)
        assert len(installs) == 1
        assert installs[0]["packages"] == [optional_deps.GOOGLE_GENAI_PACKAGE]
        assert installs[0]["reinstall"] is False

        models = [value for _label, value in dialog_mod._LIVE_VOICE_MODEL_OPTIONS]
        models.append("custom-live-model")
        voices = [value for _label, value in dialog_mod._LIVE_VOICE_VOICE_OPTIONS]
        voices.append("CustomVoice")
        observed = set()
        for model, voice, half_duplex in product(models, voices, (False, True)):
            set_model(model)
            set_voice(voice)
            if dialog._fields["LIVE_VOICE_HALF_DUPLEX"].isChecked() != half_duplex:
                driver.click(dialog._fields["LIVE_VOICE_HALF_DUPLEX"])
            observed.add(
                (
                    dialog._live_voice_model_value(),
                    dialog._live_voice_voice_value(),
                    dialog._fields["LIVE_VOICE_HALF_DUPLEX"].isChecked(),
                )
            )
        assert observed == set(product(models, voices, (False, True)))

        monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-google-live-key")
        monkeypatch.setattr(config, "LIVE_VOICE_PROVIDER", "google")
        for model, voice, half_duplex in sorted(observed):
            monkeypatch.setattr(config, "LIVE_VOICE_MODEL", model)
            monkeypatch.setattr(config, "LIVE_VOICE_VOICE_NAME", voice)
            monkeypatch.setattr(config, "LIVE_VOICE_HALF_DUPLEX", half_duplex)
            assert audio_host.audio_live_start() == {"started": True, "model": model}
            session = sessions[-1]
            assert (session.cfg.model, session.cfg.voice_name, session.cfg.half_duplex) == (
                model,
                voice,
                half_duplex,
            )
            assert audio_host.audio_live_stop() == {"stopped": True}

        _unique_shortcuts(dialog)
        runtime_cases = [
            (models[0], "Puck", False),
            ("custom-live-model", "CustomVoice", True),
        ]
        for model, voice, half_duplex in runtime_cases:
            set_model(model)
            set_voice(voice)
            if dialog._fields["LIVE_VOICE_HALF_DUPLEX"].isChecked() != half_duplex:
                driver.click(dialog._fields["LIVE_VOICE_HALF_DUPLEX"])
            save = dialog.findChild(QPushButton, "settingsApplyButton")
            assert save is not None and save.isEnabled()
            driver.click(save)
            monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-google-live-key")
            result = audio_host.audio_live_start()
            assert result == {"started": True, "model": model}
            session = sessions[-1]
            assert session.started is True
            assert session.cfg.api_key == "test-google-live-key"
            assert session.cfg.model == model
            assert session.cfg.voice_name == voice
            assert session.cfg.half_duplex is half_duplex
            session.emit("transcript", {"role": "user", "text": "hello"})
            session.emit("transcript", {"role": "assistant", "text": "hi"})
            assert events[-2:] == [
                ("audio.live.transcript", {"role": "user", "text": "hello"}),
                ("audio.live.transcript", {"role": "assistant", "text": "hi"}),
            ]
            assert audio_host.audio_live_stop() == {"stopped": True}
            assert session.stop_reasons == ["user"]
    finally:
        audio_host.audio_live_stop()
        audio_host.set_event_sink(lambda *_args: None)
        audio_host._live_session = None
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
