"""Tests for test settings dialog controls."""

import json
import os
import sys
import threading
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _default_settings_tests_to_english():
    """Keep settings UI assertions independent from the developer's local language."""
    import config
    from ui import i18n

    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "en"
    i18n.set_language("en")
    try:
        yield
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(old_language or None)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_disconnect_clicked_handlers_ignores_missing_connections_warning():
    """Disconnecting an unconnected PySide signal should stay quiet."""
    import warnings

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import _disconnect_clicked_handlers

    app = QApplication.instance() or QApplication(sys.argv)
    button = QPushButton("Install")

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _disconnect_clicked_handlers(button)

        assert not [
            warning
            for warning in caught
            if issubclass(warning.category, RuntimeWarning)
            and "Failed to disconnect" in str(warning.message)
        ]
    finally:
        button.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_combo_ignores_wheel_when_popup_closed():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import _NoScrollCombo

    class FakeWheelEvent:
        def __init__(self) -> None:
            self.ignored = False

        def ignore(self) -> None:
            self.ignored = True

    app = QApplication.instance() or QApplication(sys.argv)
    combo = _NoScrollCombo()
    event = FakeWheelEvent()
    try:
        combo.addItems(["one", "two"])
        combo.setFocus()

        combo.wheelEvent(event)

        assert event.ignored is True
    finally:
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_general_setting_reveals_optional_auto_open_bubble_choice():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    try:
        auto_open = dialog._fields["CHAT_OPEN_ON_PROMPT"]
        hide_bubble = dialog._fields["CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE"]

        auto_open.setChecked(False)
        assert hide_bubble.isHidden()
        assert not hide_bubble.isEnabled()

        auto_open.setChecked(True)
        assert not hide_bubble.isHidden()
        assert hide_bubble.isEnabled()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_memory_tab_does_not_show_stored_facts():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._env = {}
    tab = SettingsDialog._tab_memory(dialog)

    try:
        labels = {label.text() for label in tab.findChildren(QLabel)}
        combined = "\n".join(labels)
        assert "Stored Facts" not in combined
        assert "Stored facts" not in combined
    finally:
        tab.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_app_settings_starts_with_conversation_harness_selector():
    """The first App card includes route selection and local agent login."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._env = {}
    tab = SettingsDialog._tab_app(dialog)

    try:
        first_card = tab.widget().layout().itemAt(0).widget()
        header = first_card.findChild(QLabel, "sectionHeader")
        assert header is not None
        assert header.text() == t("Run conversations with").upper()
        combo = dialog._fields["CHAT_EXECUTION_MODE"]
        assert [combo.itemData(index) for index in range(combo.count())] == ["openwand", "codex", "claude"]
        owner = dialog._fields["CHAT_CONVERSATION_OWNER"]
        assert [owner.itemData(index) for index in range(owner.count())] == ["openwand", "agent"]
        assert owner.isEnabled() is False
        assert dialog._harness_auth_widget.isHidden() is True
        combo.setCurrentIndex(combo.findData("codex"))
        assert owner.isEnabled() is True
        assert owner.itemText(1) == t("ChatGPT")
        assert owner.currentData() == "agent"
        assert dialog._harness_auth_widget.isHidden() is False
        assert dialog._harness_auth_title_lbl.text() == t("ChatGPT login")
        assert dialog._harness_login_btn.objectName() == "settingsHarnessLoginButton"
        assert dialog._harness_logout_btn.objectName() == "settingsHarnessLogoutButton"
        owner.setCurrentIndex(owner.findData("openwand"))
        combo.setCurrentIndex(combo.findData("claude"))
        assert owner.currentData() == "openwand"
        assert dialog._harness_auth_title_lbl.text() == t("Claude Agent login")
    finally:
        tab.deleteLater()
        app.processEvents()


@pytest.mark.workflow
@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_exposes_setup_check_button():
    """Verify Settings exposes the reusable setup check entry point."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    calls = []
    dialog = SettingsDialog(on_setup_check=lambda: calls.append("setup"))
    try:
        button = dialog.findChild(QPushButton, "settingsSetupCheckButton")
        assert button is not None
        assert button.text() == t("Run setup check")
        button.click()
        assert calls == ["setup"]
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tts_voice_tab_exposes_stt_settings():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_tts(dialog)
    tab.show()
    app.processEvents()

    try:
        assert {"STT_MODEL", "STT_COMPUTE_TYPE", "STT_LANGUAGE"} <= set(dialog._fields)
        labels = {label.text() for label in tab.findChildren(QLabel)}
        assert not any("Chunking controls for read-aloud TTS" in label for label in labels)
        assert not any("Hands-free conversation with Gemini Live" in label for label in labels)
        assert f"{t('Whisper model')}  ⓘ" in labels
        assert f"{t('Compute type')}  ⓘ" in labels
        assert f"{t('Speech language')}  ⓘ" in labels
        stt_model = dialog._fields["STT_MODEL"]
        assert not stt_model.isEditable()
        model_values = {
            stt_model.itemData(i)
            for i in range(stt_model.count())
        }
        assert {"tiny", "base", "small", "medium", "large-v3"} <= model_values
        model_labels = {stt_model.itemText(i) for i in range(stt_model.count())}
        assert "base" in model_labels
        assert "small" in model_labels
        stt_compute = dialog._fields["STT_COMPUTE_TYPE"]
        compute_labels = {stt_compute.itemText(i) for i in range(stt_compute.count())}
        assert "int8_float16" in compute_labels
        assert "float16" in compute_labels
        language_values = {
            dialog._fields["STT_LANGUAGE"].itemData(i)
            for i in range(dialog._fields["STT_LANGUAGE"].count())
        }
        assert {"", "en", "zh", "yue", "es", "fr", "ja"} <= language_values
        language_labels = {
            dialog._fields["STT_LANGUAGE"].itemText(i)
            for i in range(dialog._fields["STT_LANGUAGE"].count())
        }
        assert "Chinese (Mandarin)" in language_labels
        assert "Cantonese" in language_labels
        assert "Chinese (Mandarin / Cantonese)" not in language_labels
        assert dialog._fields["STT_LANGUAGE"].itemData(
            dialog._fields["STT_LANGUAGE"].findText("Chinese (Mandarin)")
        ) == "zh"
        assert dialog._fields["STT_LANGUAGE"].itemData(
            dialog._fields["STT_LANGUAGE"].findText("Cantonese")
        ) == "yue"
        for key in (
            "TTS_READ_ALOUD_MIN_WORDS",
            "TTS_READ_ALOUD_MAX_WORDS",
            "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS",
            "STT_BACKGROUND_CHUNK_STEP_SECONDS",
            "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS",
            "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS",
        ):
            assert key in dialog._fields
        buttons = [button for button in tab.findChildren(QPushButton) if button.text() == t("Advanced settings")]
        assert buttons
        advanced_button = buttons[0]
        assert advanced_button.isCheckable()
        assert not advanced_button.isChecked()
        advanced_button.setChecked(True)
        assert dialog._fields["TTS_READ_ALOUD_MIN_WORDS"].isVisible()
        assert dialog._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"].isVisible()
    finally:
        tab.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tts_voice_tab_switches_to_cloudflare_stt_controls():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_tts(dialog)
    tab.show()

    try:
        provider = dialog._fields["STT_PROVIDER"]
        provider.setCurrentIndex(provider.findData("cloudflare"))
        app.processEvents()

        assert not dialog._fields["STT_CLOUDFLARE_ACCOUNT_ID"].isHidden()
        assert not dialog._fields["CLOUDFLARE_API_TOKEN"].isHidden()
        assert dialog._fields["STT_MODEL"].isHidden()
        assert dialog._stt_download_btn.text() == "Test Cloudflare STT"
    finally:
        tab.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tts_voice_tab_can_disable_stt_without_leaving_model_controls_active():
    """The explicit disabled provider hides every model-specific STT control."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_tts(dialog)
    tab.show()

    try:
        provider = dialog._fields["STT_PROVIDER"]
        provider.setCurrentIndex(provider.findData("none"))
        SettingsDialog._update_stt_provider_fields(dialog)
        app.processEvents()

        assert provider.currentText() == "None"
        assert dialog._fields["STT_MODEL"].isHidden()
        assert dialog._fields["STT_LANGUAGE"].isHidden()
        assert dialog._fields["STT_BEAM_SIZE"].isHidden()
        assert dialog._fields["STT_CLOUDFLARE_ACCOUNT_ID"].isHidden()
        assert dialog._stt_download_btn.isHidden()
        assert dialog._stt_active_lbl.isHidden()
        assert dialog._stt_active_lbl.text() == ""

        provider.setCurrentIndex(provider.findData("local"))
        SettingsDialog._update_stt_provider_fields(dialog)
        assert not dialog._stt_active_lbl.isHidden()
        assert not dialog._stt_download_btn.isHidden()
    finally:
        tab.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tts_voice_tab_can_disable_live_conversation_and_only_lists_supported_choices():
    """Live Conversation offers Disabled/Google only and hides session controls when off."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_tts(dialog)
    tab.show()

    try:
        provider = dialog._fields["LIVE_VOICE_PROVIDER"]
        assert [provider.itemData(index) for index in range(provider.count())] == ["none", "google"]

        provider.setCurrentIndex(provider.findData("none"))
        SettingsDialog._update_live_voice_provider_fields(dialog)
        app.processEvents()

        assert provider.currentText() == "None"
        assert all(widget.isHidden() for widget in dialog._live_voice_enabled_widgets)
        assert dialog._live_voice_install_btn.isHidden()
        assert dialog._live_voice_key_note_lbl.isHidden()
        assert dialog._live_voice_key_note_lbl.text() == ""

        provider.setCurrentIndex(provider.findData("google"))
        SettingsDialog._update_live_voice_provider_fields(dialog)
        assert not dialog._live_voice_key_note_lbl.isHidden()
        assert not dialog._live_voice_install_btn.isHidden()
    finally:
        tab.deleteLater()
        app.processEvents()

@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_speech_settings_group_tts_fields_and_use_compact_actions():
    """Speech settings keep volume first and present each speech mode consistently."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QSizePolicy

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_tts(dialog)
    tab.show()
    app.processEvents()

    try:
        content_layout = tab.widget().layout()
        card_headers = []
        for index in range(content_layout.count()):
            card = content_layout.itemAt(index).widget()
            if not isinstance(card, QFrame) or card.objectName() != "card":
                continue
            header = card.findChild(QLabel, "sectionHeader")
            if header is not None:
                card_headers.append(header.text())

        assert card_headers[:4] == [
            t("Playback").upper(),
            t("TTS").upper(),
            t("Speech to Text").upper(),
            t("Live voice conversation").upper(),
        ]
        assert t("Voice & API key").upper() not in card_headers

        def containing_card(widget):
            parent = widget.parentWidget()
            while parent is not None:
                if isinstance(parent, QFrame) and parent.objectName() == "card":
                    return parent
                parent = parent.parentWidget()
            return None

        tts_card = containing_card(dialog._fields["TTS_PROVIDER"])
        assert tts_card is not None
        assert containing_card(dialog._fields["CARTESIA_API_KEY"]) is tts_card
        assert any(
            button.text() == t("Test TTS")
            for button in tts_card.findChildren(QPushButton)
        )
        assert dialog._fields["TTS_SPEAK_REPLIES"].text() == t(
            "Read assistant replies aloud automatically"
        )
        provider_combo = dialog._fields["TTS_PROVIDER"]
        provider_combo.setCurrentIndex(provider_combo.findData("none"))
        dialog._update_tts_provider_fields()
        assert dialog._fields["TTS_SPEAK_REPLIES"].isHidden()
        assert dialog._tts_test_row.isHidden()
        assert dialog._tts_test_status_lbl.isHidden()

        provider_combo.setCurrentIndex(0)
        dialog._update_tts_provider_fields()
        assert not dialog._fields["TTS_SPEAK_REPLIES"].isHidden()
        assert not dialog._tts_test_row.isHidden()
        assert not dialog._tts_test_status_lbl.isHidden()

        compact_buttons = (
            dialog._stt_download_btn,
            dialog._elevenlabs_install_btn,
            dialog._kokoro_install_btn,
            dialog._live_voice_install_btn,
        )
        assert all(
            button.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
            for button in compact_buttons
        )
    finally:
        tab.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tts_voice_tab_does_not_import_stt_stack(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import core
    from core import optional_deps
    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_stt_module = sys.modules.pop("core.stt", None)
    old_core_stt = getattr(core, "stt", None)
    had_core_stt = hasattr(core, "stt")
    if had_core_stt:
        delattr(core, "stt")
    monkeypatch.setattr(dialog_mod, "_read_optional_install_status", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        optional_deps,
        "optional_package_spec_status",
        lambda package, *_args, **_kwargs: {"installed": package == "stt", "valid": package == "stt"},
    )
    monkeypatch.setattr(
        optional_deps,
        "stt_runtime_import_status_fast",
        lambda: {"installed": True, "valid": True, "version": "1.2.1", "origin": "fake"},
    )

    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._env = {}
    tab = None
    try:
        tab = SettingsDialog._tab_tts(dialog)

        assert "core.stt" not in sys.modules
        # Resolving the backend scans installed distributions, so the tab defers it
        # off the construction path to keep the window painting; run it directly.
        SettingsDialog._refresh_stt_active_backend(dialog)

        assert "core.stt" not in sys.modules
        assert "tiny · auto / int8" in dialog._stt_active_lbl.text()
    finally:
        if tab is not None:
            tab.deleteLater()
        if old_stt_module is not None:
            sys.modules["core.stt"] = old_stt_module
        else:
            sys.modules.pop("core.stt", None)
        if had_core_stt:
            core.stt = old_core_stt
        elif hasattr(core, "stt"):
            delattr(core, "stt")
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_open_defers_tts_install_status_checks(monkeypatch):
    """Opening Settings should not probe optional TTS packages until Voice is opened."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    calls: list[str] = []
    monkeypatch.setattr(
        SettingsDialog,
        "_optional_package_installed",
        staticmethod(lambda module_name: calls.append(module_name) or False),
    )

    dialog = SettingsDialog()
    try:
        assert calls == []
        assert dialog._kokoro_install_status_lbl.text() == ""
        assert dialog._elevenlabs_install_status_lbl.text() == ""
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_voice_tab_starts_deferred_tts_status_check(monkeypatch):
    """Selecting TTS / Voice should trigger the deferred install-status refresh."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    calls: list[str] = []
    monkeypatch.setattr(
        SettingsDialog,
        "_refresh_tts_optional_install_status",
        lambda self, **_kwargs: calls.append("refresh"),
    )

    dialog = SettingsDialog()
    try:
        assert calls == []
        dialog._tabs.setCurrentIndex(dialog._tts_tab_index)
        app.processEvents()
        # Count is not pinned: selecting the page can also finish the deferred
        # page build, which reloads values. The real method dedupes via
        # _tts_install_status_checked, so only the first call does any work.
        assert calls and set(calls) == {"refresh"}
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_voice_status_check_runs_once_per_dialog(monkeypatch):
    """Voice install-status checks should run at most once per Settings window."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog, _set

    app = QApplication.instance() or QApplication(sys.argv)
    calls: list[str] = []

    def fake_refresh(self):
        if self._tts_install_status_running or self._tts_install_status_checked:
            return
        self._tts_install_status_checked = True
        calls.append("refresh")

    monkeypatch.setattr(SettingsDialog, "_refresh_tts_optional_install_status", fake_refresh)

    dialog = SettingsDialog()
    try:
        dialog._tabs.setCurrentIndex(dialog._tts_tab_index)
        app.processEvents()
        assert calls == ["refresh"]

        dialog._tabs.setCurrentIndex(0)
        dialog._tabs.setCurrentIndex(dialog._tts_tab_index)
        _set(dialog._fields["TTS_PROVIDER"], "kokoro")
        _set(dialog._fields["KOKORO_DEVICE"], "cuda")
        app.processEvents()

        assert calls == ["refresh"]
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_optional_tts_install_launches_external_terminal(monkeypatch, tmp_path):
    """Optional TTS installs should launch the OpenWand installer dialog in source builds."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from core import optional_deps
    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    launched: dict[str, object] = {}

    class FakeSignal:
        def connect(self, callback):
            launched.setdefault("callbacks", []).append(callback)

    class FakeInstallDialog:
        def __init__(self, **kwargs):
            launched.update(kwargs)
            self.install_finished = FakeSignal()
            self.destroyed = FakeSignal()
            self.exit_code = None

        def show(self):
            launched["shown"] = True

        def raise_(self):
            launched["raised"] = True

        def activateWindow(self):
            launched["activated"] = True

    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", tmp_path / "python_packages")
    monkeypatch.setattr(dialog_mod, "OptionalInstallDialog", FakeInstallDialog)
    monkeypatch.setattr(dialog_mod.sys, "frozen", False, raising=False)
    monkeypatch.setattr(dialog_mod.sys, "platform", "linux")

    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._kokoro_install_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()

    try:
        ok = SettingsDialog._try_launch_external_optional_tts_install(
            dialog,
            test_key="kokoro_install",
            display_name="Kokoro",
            packages=["kokoro==0.9.4"],
            button_attr="_kokoro_install_btn",
            status_attr="_kokoro_install_status_lbl",
            external_plan_extra={"post_install": "kokoro_prepare", "kokoro_voice": "af_heart"},
        )

        assert ok is True
        assert launched["title"] == "OpenWand Kokoro installer"
        assert launched["shown"] is True
        assert launched["mirror_output_to_log"] is False
        command = launched["command"]
        assert isinstance(command, list)
        assert command[1].endswith("optional_tts_installer.py")
        assert command[-2] == "--plan"
        plan = Path(command[-1]).read_text(encoding="utf-8")
        assert '"kokoro==0.9.4"' in plan
        assert '"post_install": "kokoro_prepare"' in plan
        # Staged restart-apply installs are used on every platform so pip
        # never writes into the live package folder while OpenWand is running.
        assert '"restart_apply": true' in plan
        assert "OpenWand installer window" in dialog._kokoro_install_status_lbl.text()
    finally:
        dialog._kokoro_install_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_optional_tts_install_launches_external_terminal_in_frozen_build(monkeypatch, tmp_path):
    """Portable builds should launch the bundled installer worker in the OpenWand dialog."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from core import optional_deps
    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    launched: dict[str, object] = {}

    class FakeSignal:
        def connect(self, callback):
            launched.setdefault("callbacks", []).append(callback)

    class FakeInstallDialog:
        def __init__(self, **kwargs):
            launched.update(kwargs)
            self.install_finished = FakeSignal()
            self.destroyed = FakeSignal()
            self.exit_code = None

        def show(self):
            launched["shown"] = True

        def raise_(self):
            launched["raised"] = True

        def activateWindow(self):
            launched["activated"] = True

    fake_exe = tmp_path / "OpenWand.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", tmp_path / "python_packages")
    monkeypatch.setattr(dialog_mod, "OptionalInstallDialog", FakeInstallDialog)
    monkeypatch.setattr(dialog_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(dialog_mod.sys, "platform", "win32")
    monkeypatch.setattr(dialog_mod.sys, "executable", str(fake_exe))

    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._stt_download_btn = QPushButton()
    dialog._stt_active_lbl = QLabel()

    try:
        ok = SettingsDialog._try_launch_external_optional_tts_install(
            dialog,
            test_key="stt_install",
            display_name="STT",
            packages=["faster-whisper==1.2.1"],
            button_attr="_stt_download_btn",
            status_attr="_stt_active_lbl",
            external_plan_extra={"post_install": "stt_prepare", "stt_model": "base"},
        )

        assert ok is True
        assert launched["cwd"] == fake_exe.parent
        assert launched["command"][:3] == [
            str(fake_exe),
            "-m",
            "runtime.workers.optional_speech_installer",
        ]
        assert launched["shown"] is True
        assert launched["mirror_output_to_log"] is False
        assert launched["command"][-2] == "--plan"
        plan = Path(launched["command"][-1]).read_text(encoding="utf-8")
        assert '"post_install": "stt_prepare"' in plan
        assert '"stt_model": "base"' in plan
        assert '"restart_apply": true' in plan
        assert '"install_contract":' in plan
        assert '"app_version":' in plan
    finally:
        dialog._stt_download_btn.deleteLater()
        dialog._stt_active_lbl.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_optional_installer_finish_refreshes_tts_detection(monkeypatch):
    """Finishing a TTS install should invalidate cached detection and recheck."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    refreshes: list[str] = []
    monkeypatch.setattr(dialog_mod, "_read_optional_install_status", lambda *_args, **_kwargs: {"ok": True, "message": "Kokoro installed."})
    monkeypatch.setattr(SettingsDialog, "_refresh_tts_optional_install_status", lambda self: refreshes.append("tts"))

    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._kokoro_install_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()
    dialog._tts_install_status_checked = True
    dialog._tts_install_status_result = {"kokoro_installed": False}

    try:
        SettingsDialog._finish_optional_install_dialog(
            dialog,
            test_key="kokoro_install",
            display_name="Kokoro",
            button_attr="_kokoro_install_btn",
            status_attr="_kokoro_install_status_lbl",
            exit_code=0,
            dialog=object(),
        )

        assert refreshes == ["tts"]
        assert dialog._tts_install_status_checked is False
        assert dialog._tts_install_status_result is None
        assert dialog._kokoro_install_status_lbl.text() == "Kokoro installed."
    finally:
        dialog._kokoro_install_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_optional_installer_finish_refreshes_stt_detection(monkeypatch):
    """Finishing an STT install should refresh the STT backend readout."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    refreshes: list[str] = []
    monkeypatch.setattr(dialog_mod, "_read_optional_install_status", lambda *_args, **_kwargs: {"ok": True, "message": "STT installed."})
    monkeypatch.setattr(SettingsDialog, "_refresh_stt_active_backend", lambda self: refreshes.append("stt"))

    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._stt_download_btn = QPushButton()
    dialog._stt_active_lbl = QLabel()

    try:
        SettingsDialog._finish_optional_install_dialog(
            dialog,
            test_key="stt_install",
            display_name="STT",
            button_attr="_stt_download_btn",
            status_attr="_stt_active_lbl",
            exit_code=0,
            dialog=object(),
        )

        assert refreshes == ["stt"]
        assert dialog._stt_active_lbl.text() == "STT installed."
        assert dialog._stt_download_btn.text() == "Install STT"
    finally:
        dialog._stt_download_btn.deleteLater()
        dialog._stt_active_lbl.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_optional_installer_finish_prompts_restart_for_restart_apply(monkeypatch):
    """A staged Windows install should wait for an explicit restart click."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    refreshes: list[str] = []
    single_shots: list[tuple[int, object]] = []
    monkeypatch.setattr(
        dialog_mod,
        "_read_optional_install_status",
        lambda *_args, **_kwargs: {
            "ok": None,
            "message": "STT packages are staged. OpenWand will close, replace locked files, verify the install, and reopen.",
            "restart_apply": True,
        },
    )
    monkeypatch.setattr(SettingsDialog, "_refresh_stt_active_backend", lambda self: refreshes.append("stt"))
    monkeypatch.setattr(dialog_mod.QTimer, "singleShot", lambda ms, callback: single_shots.append((ms, callback)))

    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._stt_download_btn = QPushButton()
    dialog._stt_active_lbl = QLabel()

    try:
        SettingsDialog._finish_optional_install_dialog(
            dialog,
            test_key="stt_install",
            display_name="STT",
            button_attr="_stt_download_btn",
            status_attr="_stt_active_lbl",
            exit_code=0,
            dialog=object(),
        )

        assert refreshes == []
        assert dialog._stt_download_btn.isEnabled() is True
        assert dialog._stt_download_btn.text() == "Restart app now"
        assert "Click Restart app now" in dialog._stt_active_lbl.text()
        assert single_shots == []
    finally:
        dialog._stt_download_btn.deleteLater()
        dialog._stt_active_lbl.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_stt_install_uses_optional_installer(monkeypatch, tmp_path):
    """STT install should use the same optional installer flow as TTS."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QMessageBox, QPushButton

    from core import optional_deps
    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    captured: dict[str, object] = {}
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", tmp_path / "python_packages")
    monkeypatch.setattr(
        dialog_mod.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(SettingsDialog, "_install_optional_tts_package", lambda self, **kwargs: captured.update(kwargs))

    dialog = SettingsDialog.__new__(SettingsDialog)
    model = QComboBox()
    model.addItem("base", "base")
    device = QComboBox()
    device.addItem("Auto", "auto")
    compute = QComboBox()
    compute.addItem("int8", "int8")
    language = QComboBox()
    language.addItem("English", "en")
    beam = QComboBox()
    beam.addItem("5", "5")
    dialog._fields = {
        "STT_MODEL": model,
        "STT_DEVICE": device,
        "STT_COMPUTE_TYPE": compute,
        "STT_LANGUAGE": language,
        "STT_BEAM_SIZE": beam,
    }
    dialog._stt_download_btn = QPushButton()
    dialog._stt_active_lbl = QLabel()

    try:
        SettingsDialog._preload_stt_model(dialog)

        assert captured["test_key"] == "stt_install"
        assert captured["display_name"] == "STT"
        assert captured["packages"] == optional_deps.stt_install_packages("auto")
        assert captured["remove_artifacts"] == optional_deps.stt_remove_artifacts()
        assert captured["button_attr"] == "_stt_download_btn"
        assert captured["status_attr"] == "_stt_active_lbl"
        assert captured["external_plan_extra"] == {
            "post_install": "stt_prepare",
            "stt_model": "base",
            "stt_device": "auto",
            "stt_compute_type": "int8",
            "settings_updates": {
                "OPENWAND_STT_PREFERENCE": "local",
                "STT_PROVIDER": "local",
                "STT_MODEL": "base",
                "STT_DEVICE": "auto",
                "STT_COMPUTE_TYPE": "int8",
                "STT_LANGUAGE": "en",
                "STT_BEAM_SIZE": "5",
            },
        }
        assert captured["post_install_progress_detail"] == "downloading or loading Whisper model for {elapsed}"
    finally:
        for widget in (model, device, compute, language, beam, dialog._stt_download_btn, dialog._stt_active_lbl):
            widget.deleteLater()
        app.processEvents()


def test_optional_install_terminal_closes_on_windows(monkeypatch, tmp_path):
    """Windows terminal installs should auto-close on success and stay open on failure."""
    from ui.settings_panel import dialog as dialog_mod

    launched: dict[str, object] = {}
    monkeypatch.setattr(dialog_mod.sys, "platform", "win32")
    monkeypatch.setattr(dialog_mod.subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)
    monkeypatch.setattr(
        dialog_mod.subprocess,
        "Popen",
        lambda command, **kwargs: launched.update(command=command, **kwargs),
    )

    ok = dialog_mod._launch_terminal_command(
        ["python", "-m", "installer"],
        cwd=tmp_path,
        title="OpenWand installer",
    )

    assert ok is True
    assert launched["command"][:3] == ["cmd.exe", "/V:ON", "/C"]
    cmdline = launched["command"][-1]
    assert "python -m installer" in cmdline
    assert "/K" not in launched["command"]
    assert "if errorlevel 1" in cmdline
    assert "OpenWand installer failed with exit code !OPENWAND_INSTALL_EXIT!." in cmdline
    assert "pause > nul" in cmdline.lower()
    assert launched["creationflags"] == 16


def test_terminal_command_forwards_isolated_environment_on_windows(monkeypatch, tmp_path):
    """Visible Codex login terminals inherit OpenWand's private state roots."""
    from ui.settings_panel import dialog as dialog_mod

    launched: dict[str, object] = {}
    monkeypatch.setattr(dialog_mod.sys, "platform", "win32")
    monkeypatch.setattr(dialog_mod.subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)
    monkeypatch.setattr(
        dialog_mod.subprocess,
        "Popen",
        lambda command, **kwargs: launched.update(command=command, **kwargs),
    )

    ok = dialog_mod._launch_terminal_command(
        ["codex", "login"],
        cwd=tmp_path,
        title="ChatGPT sign-in",
        environment={"CODEX_HOME": "openwand-codex", "CODEX_SQLITE_HOME": "openwand-codex"},
    )

    assert ok is True
    environment = launched["env"]
    assert environment["CODEX_HOME"] == "openwand-codex"
    assert environment["CODEX_SQLITE_HOME"] == "openwand-codex"


def test_terminal_shell_command_scopes_isolated_environment(tmp_path):
    """macOS and Linux login shells scope Codex state to the launched command."""
    from ui.settings_panel import dialog as dialog_mod

    command = dialog_mod._terminal_shell_command(
        ["codex", "login"],
        tmp_path,
        "ChatGPT sign-in",
        environment={"CODEX_HOME": "/openwand/codex", "CODEX_SQLITE_HOME": "/openwand/codex"},
    )

    assert "env CODEX_HOME=/openwand/codex CODEX_SQLITE_HOME=/openwand/codex codex login" in command


def test_optional_install_terminal_auto_closes_on_macos(monkeypatch, tmp_path):
    """macOS terminal installs should close on success and pause on failure."""
    from ui.settings_panel import dialog as dialog_mod

    launched: dict[str, object] = {}
    monkeypatch.setattr(dialog_mod.sys, "platform", "darwin")
    monkeypatch.setattr(
        dialog_mod.subprocess,
        "Popen",
        lambda command, **kwargs: launched.update(command=command, **kwargs),
    )

    ok = dialog_mod._launch_terminal_command(
        ["python", "-m", "installer"],
        cwd=tmp_path,
        title="OpenWand installer",
    )

    assert ok is True
    assert launched["command"][:2] == ["/usr/bin/osascript", "-e"]
    script = launched["command"][2]
    assert "activate" in script
    assert "exit" in script
    assert "close (window of targetTab) saving no" in script
    assert "OpenWand installer failed with exit code" in script
    assert "Press Enter to close this window" in script
    assert "read -r _" in script
    assert "read -n" not in script


def test_optional_install_terminal_auto_closes_on_linux(monkeypatch, tmp_path):
    """Linux terminal installs should close on success and pause on failure."""
    from ui.settings_panel import dialog as dialog_mod

    launched: dict[str, object] = {}
    monkeypatch.setattr(dialog_mod.sys, "platform", "linux")
    monkeypatch.setattr(dialog_mod.shutil, "which", lambda name: "/usr/bin/x-terminal-emulator")

    class RunningProcess:
        def wait(self, timeout=None):
            raise dialog_mod.subprocess.TimeoutExpired(["x-terminal-emulator"], timeout)

    monkeypatch.setattr(
        dialog_mod.subprocess,
        "Popen",
        lambda command, **kwargs: launched.update(command=command, **kwargs) or RunningProcess(),
    )

    ok = dialog_mod._launch_terminal_command(
        ["python", "-m", "installer"],
        cwd=tmp_path,
        title="OpenWand installer",
    )

    assert ok is True
    command = launched["command"]
    assert command[:3] == ["x-terminal-emulator", "-e", "sh"]
    assert launched["stdin"] == dialog_mod.subprocess.DEVNULL
    assert launched["stdout"] == dialog_mod.subprocess.DEVNULL
    assert launched["stderr"] == dialog_mod.subprocess.DEVNULL
    shell_cmd = command[-1]
    assert "python -m installer" in shell_cmd
    assert "OpenWand installer failed with exit code" in shell_cmd
    assert "Press Enter to close this window" in shell_cmd
    assert "read -r -p" not in shell_cmd
    assert "read -r _" in shell_cmd


def test_local_speech_device_options_hide_cuda_on_macos(monkeypatch):
    """macOS should not offer CUDA for STT or Kokoro installs."""
    from ui.settings_panel import dialog as dialog_mod

    monkeypatch.setattr(dialog_mod.sys, "platform", "darwin")

    options = dialog_mod._local_speech_device_options()

    assert ("GPU (CUDA)", "cuda") not in options
    assert [value for _label, value in options] == ["auto", "cpu"]


def test_optional_install_terminal_falls_back_to_konsole_on_linux(monkeypatch, tmp_path):
    """Linux terminal installs should try another emulator when the first one fails."""
    from ui.settings_panel import dialog as dialog_mod

    launched: list[list[str]] = []
    monkeypatch.setattr(dialog_mod.sys, "platform", "linux")
    monkeypatch.setattr(dialog_mod.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"x-terminal-emulator", "konsole"} else None)
    monkeypatch.delenv("TERMINAL", raising=False)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "")

    class FailedProcess:
        def wait(self, timeout=None):
            return 1

    class RunningProcess:
        def wait(self, timeout=None):
            raise dialog_mod.subprocess.TimeoutExpired(["konsole"], timeout)

    def fake_popen(command, **kwargs):
        launched.append(command)
        return FailedProcess() if command[0] == "x-terminal-emulator" else RunningProcess()

    monkeypatch.setattr(dialog_mod.subprocess, "Popen", fake_popen)

    ok = dialog_mod._launch_terminal_command(
        ["python", "-m", "installer"],
        cwd=tmp_path,
        title="OpenWand installer",
    )

    assert ok is True
    assert launched[0][:2] == ["x-terminal-emulator", "-e"]
    assert launched[1][:2] == ["konsole", "--workdir"]
    assert "--title" in launched[1]
    assert "OpenWand installer" in launched[1]


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_technical_combo_options_translate_model_names_only():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.i18n import localize_widget_tree
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_tts(dialog)

    try:
        localize_widget_tree(tab)

        stt_model = dialog._fields["STT_MODEL"]
        labels = {stt_model.itemText(i) for i in range(stt_model.count())}
        assert "base\uFF08\u57FA\u790E\uFF09" in labels
        assert "small\uFF08\u5C0F\u578B\uFF09" in labels
        assert stt_model.itemData(stt_model.findText("base\uFF08\u57FA\u790E\uFF09")) == "base"

        stt_compute = dialog._fields["STT_COMPUTE_TYPE"]
        compute_labels = {stt_compute.itemText(i) for i in range(stt_compute.count())}
        assert "int8" in compute_labels
        assert "float16" in compute_labels
    finally:
        config.APP_LANGUAGE = old_language
        tab.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_context_combo_localization_preserves_app_context_labels():
    """Verify app context combo localization does not leak internal boolean roles."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    import config
    from ui import i18n
    from ui.settings_panel.context_controls import AppContextCombo

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    i18n.set_language("zh-Hant", app=app)
    host = QWidget()
    layout = QVBoxLayout(host)
    combo = AppContextCombo(False, "off")
    layout.addWidget(combo)

    try:
        i18n.localize_widget_tree(host)

        labels = [combo.itemText(i) for i in range(combo.count())]
        assert labels == ["關閉", "開啟", "開啟 + 開啟文件", "讓模型決定"]
        assert "False" not in labels
        assert "True" not in labels
        assert combo.itemData(0, int(Qt.ItemDataRole.UserRole) + 1) is False
        assert combo.itemData(1, int(Qt.ItemDataRole.UserRole) + 1) is True
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(old_language or None, app=app)
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_stt_model_dropdown_preserves_saved_custom_value():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import _NoScrollCombo, _set

    app = QApplication.instance() or QApplication(sys.argv)
    combo = _NoScrollCombo()
    combo.setProperty("allow_custom_saved_value", True)
    for model in ("tiny", "base", "small", "medium", "large-v3"):
        combo.addItem(model, model)

    try:
        _set(combo, "distil-large-v3")

        assert not combo.isEditable()
        assert combo.currentData() == "distil-large-v3"
        assert combo.findData("distil-large-v3") >= 0
    finally:
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_live_voice_voice_dropdown_supports_custom_value():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLineEdit

    from ui.settings_panel.dialog import (
        _CUSTOM_MODEL_SENTINEL,
        SettingsDialog,
        _NoScrollCombo,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    combo = _NoScrollCombo()
    edit = QLineEdit()
    dialog._fields = {"LIVE_VOICE_VOICE_NAME": combo}
    dialog._live_voice_voice_row = {
        "model_combo": combo,
        "model_edit": edit,
    }

    try:
        SettingsDialog._fill_live_voice_voice_combo(dialog, "Kore")

        assert combo.currentData() == "Kore"
        assert edit.isHidden()
        assert combo.findData(_CUSTOM_MODEL_SENTINEL) >= 0

        SettingsDialog._fill_live_voice_voice_combo(dialog, "Zephyr")

        assert combo.currentData() == _CUSTOM_MODEL_SENTINEL
        assert edit.text() == "Zephyr"
        assert not edit.isHidden()
        assert SettingsDialog._live_voice_voice_value(dialog) == "Zephyr"
    finally:
        combo.deleteLater()
        edit.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_provider_model_lists_include_current_defaults():
    """Verify the static provider model dropdowns include current common choices."""
    from ui.settings_panel.dialog import _PROVIDER_MODELS

    expected = {
        "openai": {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"},
        "chatgpt": {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"},
        "google": {"gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"},
        "anthropic": {"claude-fable-5", "claude-opus-5", "claude-sonnet-5"},
        "groq": {"openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.6-27b"},
        "deepseek": {"deepseek-v4-flash", "deepseek-v4-pro"},
        "mistral": {"mistral-medium-3-5", "mistral-small-2603"},
        "xai": {"grok-4.5", "grok-4.3"},
        "openrouter": {"openai/gpt-5.6-sol", "openai/gpt-5.6-terra", "openai/gpt-5.6-luna"},
        "vercel": {"openai/gpt-5.6-sol", "openai/gpt-5.6-terra", "openai/gpt-5.6-luna"},
        "cohere": {"command-a-plus-05-2026", "command-a-03-2025"},
        "ai21": {"jamba-large-1.7", "jamba-mini-2"},
    }

    for provider, models in expected.items():
        assert models <= set(_PROVIDER_MODELS[provider])
    assert not {
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "qwen/qwen3-32b",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
    } & set(_PROVIDER_MODELS["groq"])
    assert not {"deepseek-chat", "deepseek-reasoner"} & set(_PROVIDER_MODELS["deepseek"])
    assert _PROVIDER_MODELS["ollama"] == []


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_app_tab_exposes_assistant_language_setting():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QFrame, QLabel, QPushButton

    import config
    from ui import i18n
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "en"
    i18n.set_language(app=app)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_app(dialog)

    try:
        assert "APP_LANGUAGE" in dialog._fields
        assert "ASSISTANT_LANGUAGE" in dialog._fields
        assert "PRIVACY_MODE" in dialog._fields
        assert "PROMPT_INJECTION_PROTECTION" in dialog._fields
        assert "PROMPT_INJECTION_WARN" in dialog._fields
        assert "START_ON_LOGIN" in dialog._fields
        assert "CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY" in dialog._fields
        labels = {label.text() for label in tab.findChildren(QLabel)}
        checkboxes = {checkbox.text() for checkbox in tab.findChildren(QCheckBox)}
        assert "App language  ⓘ" in labels
        assert "Assistant language  ⓘ" in labels
        assert "Ask me before sending when private text is found" in checkboxes
        assert "Detect possible prompt injections in captured text" in checkboxes
        assert "Warn me before sending when a possible injection is found" in checkboxes
        assert {
            "Secrets and credentials",
            "Contact details",
            "Financial information",
            "Government IDs",
            "Web addresses",
        } <= checkboxes
        assert "Start OpenWand when you sign in" in checkboxes
        assert "Use caller context defaults only for new conversations" in checkboxes
        assert "Conversation history is still included" in dialog._fields[
            "CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY"
        ].toolTip()
        app_form = dialog._fields["START_ON_LOGIN"].parentWidget().layout()
        auto_hide_row, _ = app_form.getWidgetPosition(dialog._fields["ICON_AUTO_HIDE"])
        start_on_login_row, _ = app_form.getWidgetPosition(dialog._fields["START_ON_LOGIN"])
        first_prompt_context_row, _ = app_form.getWidgetPosition(
            dialog._fields["CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY"]
        )
        bubble_scroll_row, _ = app_form.getWidgetPosition(
            dialog._fields["BUBBLE_SCROLL_SNAP_ENABLED"]
        )
        assert bubble_scroll_row < auto_hide_row < start_on_login_row < first_prompt_context_row
        assert first_prompt_context_row == app_form.rowCount() - 1
        privacy_selector = dialog._fields["PRIVACY_MODE"]
        assert isinstance(privacy_selector, QComboBox)
        assert {
            privacy_selector.itemData(index)
            for index in range(privacy_selector.count())
        } == {"off", "builtin", "advanced"}
        advanced_index = privacy_selector.findData("advanced")
        advanced_tooltip = privacy_selector.itemData(
            advanced_index,
            Qt.ItemDataRole.ToolTipRole,
        )
        assert "warms it in the background" in advanced_tooltip
        assert "tens of seconds" in advanced_tooltip
        privacy_card = privacy_selector.parentWidget()
        while privacy_card is not None and privacy_card.objectName() != "card":
            privacy_card = privacy_card.parentWidget()
        assert privacy_card is not None
        privacy_help = {
            label.property("privacyHelpKey"): label
            for label in privacy_card.findChildren(QLabel)
            if label.property("privacyHelpKey")
        }
        assert set(privacy_help) == {
            "mode",
            "review",
            "prompt-injection",
            "prompt-injection-warning",
            "protected-content",
            "advanced-model",
        }
        assert privacy_help["mode"].accessibleName() == "Protection level"
        assert "Screenshots and images are not inspected" in privacy_help["mode"].toolTip()
        assert "send the original text" in privacy_help["review"].toolTip()
        assert "within the next five words" in privacy_help["prompt-injection"].toolTip()
        assert "detect silently" in privacy_help["prompt-injection-warning"].toolTip()
        assert "prompt, captured app or document context" in privacy_help["protected-content"].toolTip()
        assert "Screenshots and other images are not inspected" in privacy_help["protected-content"].toolTip()
        assert "about 2.8 GB plus its runtime" in privacy_help["advanced-model"].toolTip()
        assert "still sent to the model provider" in privacy_help["advanced-model"].toolTip()
        assert all(label.text().endswith("ⓘ") for label in privacy_help.values())
        privacy_headers = {
            label.text()
            for label in privacy_card.findChildren(QLabel)
            if label.objectName() == "sectionHeader"
        }
        assert "TEXT PRIVACY BEFORE MODEL REQUESTS" in privacy_headers
        assert "Built-in categories to hide" in privacy_headers
        cards = [frame for frame in tab.findChildren(QFrame) if frame.objectName() == "card"]
        profile_setup_button = next(
            button
            for button in tab.findChildren(QPushButton)
            if button.text() == "Run profile setup"
        )
        profile_card = profile_setup_button.parentWidget()
        while profile_card is not None and profile_card.objectName() != "card":
            profile_card = profile_card.parentWidget()
        assert profile_card is not None
        assert cards[-2:] == [privacy_card, profile_card]
        app_values = {
            dialog._fields["APP_LANGUAGE"].itemData(i)
            for i in range(dialog._fields["APP_LANGUAGE"].count())
        }
        assert {"", "en", "zh", "zh-Hant", "es", "fr"} <= app_values
        values = {
            dialog._fields["ASSISTANT_LANGUAGE"].itemData(i)
            for i in range(dialog._fields["ASSISTANT_LANGUAGE"].count())
        }
        assert {"", "match_user", "English", "Chinese", "Chinese (Traditional)", "Spanish"} <= values

        config.APP_LANGUAGE = "zh-Hant"
        i18n.set_language("zh-Hant", app=app)
        i18n.localize_widget_tree(tab)
        assert privacy_help["mode"].accessibleName() == i18n.t("Protection level")
        assert "螢幕截圖" in privacy_help["mode"].toolTip()
        assert privacy_help["advanced-model"].accessibleName() == "進階模型要求"
        translated_labels = {label.text() for label in tab.findChildren(QLabel)}
        assert "程式語言  ⓘ" in translated_labels
        assert "助手回覆語言  ⓘ" in translated_labels
        translated_app_language = next(
            label for label in tab.findChildren(QLabel) if label.accessibleName() == "程式語言"
        )
        assert translated_app_language.toolTip() == "程式菜單、對話框和控制項使用的語言。"
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)
        tab.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    ("language", "translated_fragment"),
    [
        ("es", "decenas de segundos"),
        ("fr", "plusieurs dizaines de secondes"),
        ("zh", "数十秒"),
        ("zh-Hant", "數十秒"),
    ],
)
def test_advanced_privacy_warmup_tooltip_is_translated(
    language: str,
    translated_fragment: str,
):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n

    source = (
        "Advanced privacy loads a local 2.8 GB AI model into memory and warms it in the "
        "background when OpenWand starts. Warm-up may take tens of seconds on CPU. If you send "
        "a request before it finishes, that request waits; later requests are faster. The "
        "privacy model never uploads your text."
    )
    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = language
    i18n.set_language(language, app=app)

    try:
        assert translated_fragment in i18n.t(source)
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(old_language or None, app=app)


@pytest.mark.parametrize(
    ("language", "title", "description_fragment", "status"),
    [
        ("zh", "ChatGPT 登录", "所选本地代理 CLI", "已使用 ChatGPT 登录"),
        ("zh-Hant", "ChatGPT 登入", "所選本機代理程式 CLI", "已使用 ChatGPT 登入"),
        ("fr", "Connexion à ChatGPT", "l’agent local sélectionné", "Connecté avec ChatGPT"),
        ("es", "Inicio de sesión en ChatGPT", "agente local seleccionado", "Sesión iniciada con ChatGPT"),
    ],
)
def test_harness_login_surface_is_translated(
    language: str,
    title: str,
    description_fragment: str,
    status: str,
):
    """The local-agent login card should never mix English into translated UI."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n
    from ui.settings_panel import dialog

    description = (
        "OpenWand uses the account saved by the selected local agent CLI. "
        "Sign-in opens its terminal and browser flow."
    )
    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = language
    i18n.set_language(language, app=app)

    try:
        assert i18n.t("ChatGPT login") == title
        assert description_fragment in i18n.t(description)
        assert dialog._translate_status_message("Logged in using ChatGPT") == status
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(old_language or None, app=app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_privacy_model_install_requires_explicit_confirmation(monkeypatch):
    """Canceling the confirmation must stop before installer paths are prepared."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    prompt: dict[str, object] = {}

    def reject(_parent, title, body, buttons, default_button):
        prompt.update(
            title=title,
            body=body,
            buttons=buttons,
            default_button=default_button,
        )
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", reject)
    monkeypatch.setattr(
        dialog,
        "_privacy_model_install_paths",
        lambda: pytest.fail("installer preparation ran before approval"),
    )

    SettingsDialog._install_privacy_model(dialog)

    assert "2.8 GB" in str(prompt["body"])
    assert prompt["default_button"] == QMessageBox.StandardButton.No
    assert prompt["buttons"] & QMessageBox.StandardButton.Yes
    app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_privacy_model_removal_failure_matrix_is_controlled(monkeypatch):
    """Missing, cancelled, locked, and partial privacy cleanup stay in Settings."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from core import privacy_model
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    refreshes = []
    warnings = []
    answer = {"value": QMessageBox.StandardButton.No}
    calls = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: answer["value"],
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args: warnings.append(str(_args[-1])) or QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_refresh_privacy_model_status",
        lambda self: refreshes.append(True),
    )

    monkeypatch.setattr(privacy_model, "remove_model", lambda: calls.append("remove"))
    SettingsDialog._remove_privacy_model(dialog)
    assert calls == []

    answer["value"] = QMessageBox.StandardButton.Yes
    monkeypatch.setattr(privacy_model, "remove_model", lambda: False)
    SettingsDialog._remove_privacy_model(dialog)
    assert refreshes == [True]

    faults = (
        PermissionError("A target required by this function is locked."),
        PermissionError("Required elevation is denied."),
        PermissionError("Storage access is denied."),
        BlockingIOError("Another process is using the files."),
        OSError("Cleanup only partly completes."),
    )
    for fault in faults:
        monkeypatch.setattr(
            privacy_model,
            "remove_model",
            lambda fault=fault: (_ for _ in ()).throw(fault),
        )
        SettingsDialog._remove_privacy_model(dialog)
        assert str(fault) in warnings[-1]
    app.processEvents()


def test_privacy_model_installer_reuses_per_user_huggingface_token(monkeypatch):
    """OpenWand should forward saved HF auth without ever embedding a shared token."""
    import ui.settings_panel.dialog as dialog_mod
    from core import secret_store

    monkeypatch.setattr(dialog_mod, "_optional_install_env", lambda: {"PATH": "test"})
    monkeypatch.setattr(
        secret_store,
        "get_secret",
        lambda name: "hf_user_token" if name == "HUGGINGFACE_API_KEY" else "",
    )

    env = dialog_mod._privacy_model_install_env()

    assert env["HF_TOKEN"] == "hf_user_token"
    assert env["HF_HUB_VERBOSITY"] == "error"
    assert env["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"
    assert env["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_privacy_model_installer_preserves_existing_hf_token(monkeypatch):
    """An explicit process token takes precedence over OpenWand's saved provider key."""
    import ui.settings_panel.dialog as dialog_mod
    from core import secret_store

    monkeypatch.setattr(
        dialog_mod,
        "_optional_install_env",
        lambda: {"HF_TOKEN": "hf_process_token"},
    )
    monkeypatch.setattr(secret_store, "get_secret", lambda _name: "hf_saved_token")

    env = dialog_mod._privacy_model_install_env()

    assert env["HF_TOKEN"] == "hf_process_token"


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_visible_install_status_refreshes_privacy_stt_and_tts(monkeypatch):
    """Returning to App or Voice settings should re-read live install state."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QTabWidget, QWidget

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    calls: list[str] = []
    monkeypatch.setattr(
        SettingsDialog,
        "_refresh_privacy_model_status",
        lambda self: calls.append("privacy"),
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_refresh_stt_active_backend",
        lambda self: calls.append("stt"),
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_refresh_tts_optional_install_status",
        lambda self: calls.append("tts"),
    )

    dialog = SettingsDialog.__new__(SettingsDialog)
    tabs = QTabWidget()
    dialog._tabs = tabs
    dialog._app_tab_index = tabs.addTab(QWidget(), "App")
    dialog._tts_tab_index = tabs.addTab(QWidget(), "TTS / Voice")
    dialog._privacy_model_status_lbl = QLabel()
    dialog._disposing = False
    dialog._tts_install_status_running = False
    dialog._tts_install_status_checked = True
    dialog._tts_install_status_result = {"ok": True}
    try:
        tabs.setCurrentIndex(dialog._app_tab_index)
        SettingsDialog._refresh_current_install_status(dialog, force_tts=True)
        assert calls == ["privacy"]

        calls.clear()
        tabs.setCurrentIndex(dialog._tts_tab_index)
        SettingsDialog._refresh_current_install_status(dialog, force_tts=True)
        assert calls == ["stt", "tts"]
        assert dialog._tts_install_status_checked is False
        assert dialog._tts_install_status_result is None
    finally:
        tabs.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_privacy_installer_finish_refreshes_status_immediately(monkeypatch):
    """A completed privacy installer should update its existing Settings page."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    installer = object()
    calls: list[object] = []
    monkeypatch.setattr(
        dialog,
        "_refresh_privacy_model_status",
        lambda: calls.append("refresh"),
    )
    monkeypatch.setattr(
        dialog,
        "_forget_optional_install_dialog",
        lambda value: calls.append(value),
    )
    monkeypatch.setattr(QTimer, "singleShot", lambda _delay, callback: callback())

    SettingsDialog._finish_privacy_model_install(
        dialog,
        exit_code=0,
        dialog=installer,
    )

    assert calls == ["refresh", "refresh", installer]
    app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_about_page_exposes_packaged_update_controls(monkeypatch):
    """Verify the About page exposes manual update controls."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from core import updater
    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(updater, "is_repo_checkout", lambda: False)
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    general = SettingsDialog._tab_app(dialog)
    tab = SettingsDialog._tab_about(dialog)

    try:
        update_button = tab.findChild(QPushButton, "settingsUpdateButton")
        update_status = tab.findChild(QLabel, "settingsUpdateStatusLabel")

        assert update_button is not None
        assert update_button.text() == t("Check for updates")
        assert update_status is not None
        assert update_status.text() == t("Ready to check for updates.")
    finally:
        tab.deleteLater()
        general.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_about_page_wrapped_translations_keep_content_sized_rows(monkeypatch):
    """Long translated descriptions must grow instead of overlapping the next row."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy

    import config
    from core import updater
    from ui import i18n
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(updater, "is_repo_checkout", lambda: False)
    monkeypatch.setattr(config, "APP_LANGUAGE", "zh-Hant")
    app = QApplication.instance() or QApplication(sys.argv)
    i18n.set_language("zh-Hant", app)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    general = SettingsDialog._tab_app(dialog)
    tab = SettingsDialog._tab_about(dialog)
    tab.resize(760, 520)
    tab.show()

    try:
        for _ in range(4):
            app.processEvents()
        labels = [
            label
            for label in dialog._about_crash_card.findChildren(QLabel)
            if bool(label.property("description"))
        ]
        assert len(labels) == 1
        description = labels[0]
        status = dialog._crash_report_status_lbl
        assert description.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Minimum
        assert description.height() >= description.sizeHint().height()
        assert description.geometry().bottom() < status.geometry().top()
    finally:
        tab.close()
        tab.deleteLater()
        general.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_about_page_exposes_repo_update_controls(monkeypatch):
    """Verify source checkouts expose a git fast-forward update action on About."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from core import updater
    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(updater, "is_repo_checkout", lambda: True)
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    general = SettingsDialog._tab_app(dialog)
    tab = SettingsDialog._tab_about(dialog)

    try:
        update_button = tab.findChild(QPushButton, "settingsUpdateButton")
        update_status = tab.findChild(QLabel, "settingsUpdateStatusLabel")

        assert update_button is not None
        assert update_button.text() == t("Pull latest")
        assert update_status is not None
        assert update_status.text() == t("Repo checkout: ready to pull origin/main.")
        assert dialog._update_mode == "repo"
    finally:
        tab.deleteLater()
        general.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_update_download_switches_button_to_apply(monkeypatch, tmp_path):
    """Verify a downloaded update is applied from Settings instead of just opened."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from core import updater
    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog, _UpdateSignals

    monkeypatch.setattr(updater, "is_repo_checkout", lambda: False)
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._update_signal_carriers = []
    general = SettingsDialog._tab_app(dialog)
    tab = SettingsDialog._tab_about(dialog)

    try:
        carrier = _UpdateSignals()
        dialog._update_signal_carriers.append(carrier)
        downloaded = tmp_path / "OpenWand-test-windows-x64.zip"
        downloaded.write_bytes(b"fake")

        SettingsDialog._finish_update_download(dialog, carrier, str(downloaded), "")

        update_button = tab.findChild(QPushButton, "settingsUpdateButton")
        assert update_button is not None
        assert update_button.text() == t("Apply update")
        assert dialog._update_mode == "apply"
        assert dialog._update_download_path == downloaded
    finally:
        tab.deleteLater()
        general.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_localize_widget_tree_uses_app_language(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

    import config
    from ui.i18n import localize_widget_tree

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh"
    widget = QWidget()
    layout = QVBoxLayout(widget)
    button = QPushButton("Settings")
    layout.addWidget(button)

    try:
        localize_widget_tree(widget)

        assert button.text() == "设置"
    finally:
        config.APP_LANGUAGE = old_language
        widget.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_localize_widget_tree_translates_attached_parentless_action():
    """Actions attached to a live widget remain translatable without a QObject parent."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh"
    host = QWidget()
    action = QAction("Settings")
    host.addAction(action)
    try:
        i18n.set_language(app=app)
        i18n.localize_widget_tree(host)
        assert action.text() == "\u8bbe\u7f6e"
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_action_localization_ignores_deleted_qt_wrappers():
    """A stale QAction wrapper must not be dereferenced during retranslation."""
    import shiboken6
    from PySide6.QtGui import QAction

    from ui.i18n import _translate_action

    action = QAction("Settings")
    shiboken6.delete(action)

    _translate_action(action)


def test_i18n_uses_qt_catalog_without_dynamic_matching(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh"
    try:
        i18n.set_language(app=app)

        assert i18n.t("Settings") == "\u8bbe\u7f6e"
        assert i18n.t("Selection") == "\u9009\u62e9\u5185\u5bb9"
        assert i18n.t("Clipboard") == "\u526a\u8d34\u677f"
        assert i18n.t("Files") == "\u6587\u4ef6"
        assert i18n.t("Hooks: ") == "\u4e8b\u4ef6\u6302\u94a9\uff1a"
        assert i18n.t("Demo Addon Settings") == "Demo Addon Settings"
        assert i18n.t("Hooks: startup, query") == "Hooks: startup, query"
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


def test_i18n_supports_traditional_chinese(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    import config
    from ui import i18n
    from ui.settings_panel.dialog import SettingsDialog, _translate_status_message

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    try:
        i18n.set_language(app=app)

        assert i18n.current_language() == "zh-Hant"
        assert i18n.t("Settings") == "\u8a2d\u7f6e"
        assert i18n.t("Memory") == "\u8a18\u61b6"
        assert i18n.t("Selection") == "\u9078\u53d6\u5167\u5bb9"
        assert i18n.t("Clipboard") == "\u526a\u8cbc\u7c3f"
        assert i18n.t("Files") == "\u6a94\u6848"
        assert i18n.t("Chinese (Traditional)") == "\u7e41\u9ad4\u4e2d\u6587"
        assert i18n.t("Intent context keys:") == "\u610f\u5716\u4e0a\u4e0b\u6587\u9375\uff1a"
        assert i18n.t("Timeout ms:") == "\u903e\u6642\uff08\u6beb\u79d2\uff09\uff1a"
        assert i18n.t("App Settings") == "\u7a0b\u5f0f\u8a2d\u5b9a"
        assert i18n.t("Memory Settings") == "\u8a18\u61b6\u8a2d\u5b9a"
        assert i18n.t("Unsaved changes: {pages}").format(
            pages=i18n.t("General")
        ) == "\u672a\u5132\u5b58\u7684\u8b8a\u66f4\uff1a\u901a\u7528"
        assert i18n.t("Current version: {version}").format(
            version="0.10.2"
        ) == "\u76ee\u524d\u7248\u672c\uff1a0.10.2"
        assert i18n.t("Testing...") == "\u6b63\u5728\u6e2c\u8a66..."
        pending_label = QLabel()
        SettingsDialog._set_test_pending(SettingsDialog.__new__(SettingsDialog), pending_label)
        assert pending_label.text() == "\u6b63\u5728\u6e2c\u8a66..."
        pending_label.deleteLater()
        assert _translate_status_message(
            "\u2713 Primary \u2014 chatgpt / gpt-5.5: OK"
        ) == "\u2713 \u4e3b\u8981 \u2014 chatgpt / gpt-5.5\uff1a\u901a\u904e"
        assert _translate_status_message(
            "\u2717 Fallback 2 \u2014 google / gemini-2.5-flash: "
            "Fallback 2 route uses 'google', but its API key is not configured."
        ) == (
            "\u2717 \u5f8c\u5099 2 \u2014 google / gemini-2.5-flash\uff1a"
            "\u5f8c\u5099 2\u8def\u7531\u4f7f\u7528 'google'\uff0c\u4f46\u5c1a\u672a\u8a2d\u5b9a\u5176 API \u91d1\u9470\u3002"
        )
        assert _translate_status_message(
            "\u2717 Primary \u2014 unknown / model: Unknown LLM provider: unknown"
        ) == (
            "\u2717 \u4e3b\u8981 \u2014 unknown / model\uff1a"
            "\u672a\u77e5\u7684 LLM \u63d0\u4f9b\u8005\uff1aunknown"
        )
        assert _translate_status_message(
            "Primary works. 2 fallback(s) failed (Fallback 1, Fallback 2) "
            "\u2014 fix or remove just those rows."
        ) == (
            "\u4e3b\u8981\u8def\u7531\u53ef\u7528\u30022 \u500b\u5f8c\u5099\u8def\u7531\u5931\u6557\uff08"
            "\u5f8c\u5099 1, \u5f8c\u5099 2\uff09\u2014 \u8acb\u53ea\u4fee\u6b63\u6216\u79fb\u9664\u9019\u4e9b\u5217\u3002"
        )
        assert i18n.t(
            "Larger models improve Mandarin/Cantonese speech accuracy but use more disk and CPU."
        ).startswith("\u8f03\u5927\u7684\u6a21\u578b")
        assert i18n.t("Checking STT install status...").startswith("\u6b63\u5728\u6aa2\u67e5 STT")
        assert i18n.t("Dictation (hold to type)") == "\u807d\u5beb\uff08\u6309\u4f4f\u5373\u53ef\u8f38\u5165\uff09"
        assert i18n.t("OpenAI API") == "OpenAI API"
        assert i18n.t("unavailable") == "\u7121\u6cd5\u4f7f\u7528"
        assert i18n.t("LLM route uses {provider} but you are not logged in.").format(
            provider="chatgpt"
        ) == "LLM \u8def\u7531\u4f7f\u7528 chatgpt\uff0c\u4f46\u4f60\u5c1a\u672a\u767b\u5165\u3002"
        assert i18n.t("Addon folder installed.") == "\u5916\u639b\u8cc7\u6599\u593e\u5df2\u5b89\u88dd\u3002"
        assert i18n.t(
            "Recommendation: open Addon Manager, inspect the addon diagnostics, then repair or disable it."
        ).startswith("\u5efa\u8b70\uff1a\u958b\u555f\u5916\u639b\u7ba1\u7406\u5668")
        assert i18n.t("Agent") == "\u4ee3\u7406"
        assert i18n.t("Waiting {elapsed}").format(elapsed="5s") == "\u7b49\u5f85 5s"
        assert i18n.t("tool {tool} failed: {message}").format(
            tool="send_message",
            message="\u8a0a\u606f\u4e0d\u53ef\u70ba\u7a7a\u3002",
        ) == "\u5de5\u5177 send_message \u5931\u6557\uff1a\u8a0a\u606f\u4e0d\u53ef\u70ba\u7a7a\u3002"
        assert i18n.t(
            "Fetch the full readable text of a specific web page URL on demand. "
            "Use this when the user asks about a website/page and the passive "
            "browser preview is missing, partial, stale, or not enough."
        ).startswith("\u6309\u9700\u64f7\u53d6")
        assert i18n.t("Browser/Web: ") == "\u700f\u89bd\u5668/\u7db2\u9801\uff1a"
        assert i18n.t("Browser/Web: On") == "Browser/Web: On"
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


def test_settings_status_messages_translate_nested_values(monkeypatch):
    """Verify composed settings health messages translate dynamic values."""
    from ui.settings_panel import dialog

    translations = {
        "LLM test failed: {message}": "LLM \u6e2c\u8a66\u5931\u6557\uff1a{message}",
        "LLM route uses {provider} but you are not logged in.": "LLM \u8def\u7531\u4f7f\u7528 {provider}\uff0c\u4f46\u4f60\u5c1a\u672a\u767b\u5165\u3002",
        "Microphone permission: {value}.": "\u9ea5\u514b\u98a8\u6b0a\u9650\uff1a{value}\u3002",
        "Installing Kokoro: {detail}.": "\u6b63\u5728\u5b89\u88dd Kokoro\uff1a{detail}\u3002",
        "Installing STT: {detail}.": "\u6b63\u5728\u5b89\u88dd STT\uff1a{detail}\u3002",
        "STT model configured: {model}, but STT verification failed: {error}": "\u5df2\u8a2d\u5b9a STT \u6a21\u578b\uff1a{model}\uff0c\u4f46 STT \u9a57\u8b49\u5931\u6557\uff1a{error}",
        "Windows CUDA runtime is incomplete or unloadable: {files}": "Windows CUDA \u57f7\u884c\u74b0\u5883\u4e0d\u5b8c\u6574\u6216\u7121\u6cd5\u8f09\u5165\uff1a{files}",
        "downloading packages": "\u6b63\u5728\u4e0b\u8f09\u5957\u4ef6",
        "still running for {elapsed}; no installer output for {quiet}": "\u5df2\u57f7\u884c {elapsed}\uff1b{quiet} \u6c92\u6709\u5b89\u88dd\u5668\u8f38\u51fa",
        "unavailable": "\u7121\u6cd5\u4f7f\u7528",
    }
    monkeypatch.setattr(dialog, "t", lambda text: translations.get(text, text))

    assert dialog._translate_status_message(
        "LLM test failed: LLM route uses chatgpt but you are not logged in."
    ) == "LLM \u6e2c\u8a66\u5931\u6557\uff1aLLM \u8def\u7531\u4f7f\u7528 chatgpt\uff0c\u4f46\u4f60\u5c1a\u672a\u767b\u5165\u3002"
    assert (
        dialog._translate_status_message("Microphone permission: unavailable.")
        == "\u9ea5\u514b\u98a8\u6b0a\u9650\uff1a\u7121\u6cd5\u4f7f\u7528\u3002"
    )
    assert (
        dialog._translate_status_message("Installing Kokoro: downloading packages.")
        == "\u6b63\u5728\u5b89\u88dd Kokoro\uff1a\u6b63\u5728\u4e0b\u8f09\u5957\u4ef6\u3002"
    )
    assert (
        dialog._translate_status_message("Installing Kokoro: still running for 2m 40s; no installer output for 2m 40s.")
        == "\u6b63\u5728\u5b89\u88dd Kokoro\uff1a\u5df2\u57f7\u884c 2m 40s\uff1b2m 40s \u6c92\u6709\u5b89\u88dd\u5668\u8f38\u51fa\u3002"
    )
    assert dialog._translate_status_message(
        "STT model configured: small, but STT verification failed: "
        "Windows CUDA runtime is incomplete or unloadable: cublas64_12.dll"
    ) == (
        "\u5df2\u8a2d\u5b9a STT \u6a21\u578b\uff1asmall\uff0c\u4f46 STT \u9a57\u8b49\u5931\u6557\uff1a"
        "Windows CUDA \u57f7\u884c\u74b0\u5883\u4e0d\u5b8c\u6574\u6216\u7121\u6cd5\u8f09\u5165\uff1acublas64_12.dll"
    )


def test_kokoro_install_progress_text_classifies_pip_output():
    """Verify raw pip output becomes short progress phases for the Settings UI."""
    from ui.settings_panel.dialog import (
        _kokoro_install_progress_text,
        _optional_install_elapsed_text,
        _optional_install_failure_detail,
        _optional_install_log_path,
        _optional_install_no_output_timeout_seconds,
        _optional_install_progress_text,
    )

    assert _kokoro_install_progress_text("Collecting kokoro==0.9.4") == (
        "Installing Kokoro: resolving packages."
    )
    assert _kokoro_install_progress_text("Downloading torch-2.9.0.whl") == (
        "Installing Kokoro: downloading packages."
    )
    assert _kokoro_install_progress_text("Installing collected packages: kokoro") == (
        "Installing Kokoro: installing packages."
    )
    assert _optional_install_progress_text("Collecting elevenlabs", "ElevenLabs") == (
        "Installing ElevenLabs: resolving packages."
    )
    assert _kokoro_install_progress_text("Building wheel for misaki") == (
        "Installing Kokoro: working - installer is still running."
    )
    assert _optional_install_failure_detail([
        "[notice] A new pip is available",
        "ERROR: Could not find a version that satisfies the requirement kokoro==0.9.4",
    ]) == (
        "Last installer message: ERROR: Could not find a version that satisfies the requirement kokoro==0.9.4"
    )
    log_path = _optional_install_log_path("Kokoro", Path("python_packages"))
    assert log_path.name == "kokoro-install.log"
    assert log_path.parent.name == "installers"
    assert _optional_install_elapsed_text("Kokoro", 600, 600) == (
        "Installing Kokoro: still running for 10m 00s; no installer output for 10m 00s."
    )
    assert _optional_install_no_output_timeout_seconds() == 0


def test_optional_install_plan_and_env_follow_app_language(monkeypatch, tmp_path):
    """Detached optional installers should inherit OpenWand's selected UI language."""
    import config
    from core import optional_deps, updater
    from ui.settings_panel import dialog as dialog_mod

    monkeypatch.setattr(config, "APP_LANGUAGE", "zh-Hant", raising=False)
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", tmp_path / "python_packages")
    monkeypatch.setattr(updater, "openwand_wait_pid", lambda: 1234)
    monkeypatch.setattr(dialog_mod.sys, "frozen", False, raising=False)

    command, _root, _log_path, _status_path = dialog_mod._optional_install_plan_command(
        display_name="STT",
        packages=["faster-whisper==1.2.1"],
    )

    plan = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
    assert plan["app_language"] == "zh-Hant"
    assert dialog_mod._optional_install_env()["APP_LANGUAGE"] == "zh-Hant"


def test_optional_install_no_output_timeout_is_opt_in(monkeypatch):
    """Silent optional installs should keep running unless a watchdog is configured."""
    from ui.settings_panel.dialog import _optional_install_no_output_timeout_seconds

    monkeypatch.delenv("OPENWAND_OPTIONAL_INSTALL_NO_OUTPUT_TIMEOUT_SECONDS", raising=False)
    assert _optional_install_no_output_timeout_seconds() == 0

    monkeypatch.setenv("OPENWAND_OPTIONAL_INSTALL_NO_OUTPUT_TIMEOUT_SECONDS", "1800")
    assert _optional_install_no_output_timeout_seconds() == 1800

    monkeypatch.setenv("OPENWAND_OPTIONAL_INSTALL_NO_OUTPUT_TIMEOUT_SECONDS", "not-a-number")
    assert _optional_install_no_output_timeout_seconds() == 0


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_status_explains_gpu_install_when_gpu_spec_missing(monkeypatch):
    """Kokoro status should explain the selected GPU install when the GPU package spec is missing."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

    from core import optional_deps
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(optional_deps.sys, "platform", "win32")
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._kokoro_install_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()
    combo = QComboBox()
    combo.addItem("GPU (CUDA)", "cuda")
    dialog._fields["KOKORO_DEVICE"] = combo
    monkeypatch.setattr(SettingsDialog, "_kokoro_installed", lambda self: True)
    monkeypatch.setattr(
        optional_deps,
        "optional_package_spec_status",
        lambda *_args, **_kwargs: {"installed": False, "valid": False},
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_kokoro_torch_status_fast",
        lambda self: {"cuda_available": False, "version": "2.12.1+cpu", "cuda_version": ""},
    )

    try:
        SettingsDialog._refresh_kokoro_install_status(dialog)

        assert dialog._kokoro_install_btn.isEnabled()
        assert dialog._kokoro_install_btn.text() == "Install Kokoro GPU support"
        assert dialog._kokoro_install_status_lbl.text() == (
            "Kokoro is not installed. The selected device will install GPU support and may download several GB."
        )
    finally:
        dialog._kokoro_install_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_fast_status_defers_gpu_availability_check():
    """Fast Voice-page status should not warn that Torch is CPU-only."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._kokoro_install_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()
    combo = QComboBox()
    combo.addItem("Auto (GPU if available)", "auto")
    dialog._fields["KOKORO_DEVICE"] = combo

    try:
        SettingsDialog._apply_kokoro_install_status(
            dialog,
            installed=True,
            mode="gpu",
            torch_status={"fast": True, "installed": True, "version": "2.12.1+cu128"},
            needs_gpu=False,
        )

        assert dialog._kokoro_install_btn.isEnabled()
        assert dialog._kokoro_install_btn.text() == "Reinstall Kokoro"
        assert dialog._kokoro_install_status_lbl.text() == "Kokoro is installed."
    finally:
        dialog._kokoro_install_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_status_preserves_persisted_runtime_failure():
    """Reopening Settings should keep a prior Kokoro verification failure visible."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._kokoro_install_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()
    combo = QComboBox()
    combo.addItem("CPU", "cpu")
    dialog._fields["KOKORO_DEVICE"] = combo

    try:
        SettingsDialog._apply_kokoro_install_status(
            dialog,
            installed=True,
            mode="cpu",
            torch_status={"fast": True, "installed": True, "valid": True, "version": "2.12.1+cpu"},
            needs_gpu=False,
            install_status={
                "ok": False,
                "message": "Kokoro installed, but runtime verification failed: ImportError: cmath verification failed.",
            },
        )

        assert dialog._kokoro_install_btn.isEnabled()
        assert dialog._kokoro_install_btn.text() == "Install Kokoro"
        assert "cmath verification failed" in dialog._kokoro_install_status_lbl.text()
    finally:
        dialog._kokoro_install_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_status_preserves_staged_apply_retry():
    """A failed staged apply should stay visible instead of looking uninstalled."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._kokoro_install_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()
    dialog._kokoro_assets_btn = QPushButton()
    combo = QComboBox()
    combo.addItem("CPU", "cpu")
    dialog._fields["KOKORO_DEVICE"] = combo

    try:
        SettingsDialog._apply_kokoro_install_status(
            dialog,
            installed=False,
            mode="cpu",
            torch_status={},
            needs_gpu=False,
            install_status={
                "ok": False,
                "restart_apply": True,
                "message": "Kokoro staged install failed: PermissionError: c10.dll. OpenWand will retry at the next restart.",
            },
        )

        assert dialog._kokoro_install_btn.isEnabled()
        assert dialog._kokoro_install_btn.text() == "Restart app now"
        assert "retry at the next restart" in dialog._kokoro_install_status_lbl.text()
    finally:
        dialog._kokoro_install_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        dialog._kokoro_assets_btn.deleteLater()
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_device_change_reuses_cached_status_without_rechecking(monkeypatch):
    """Changing CPU/GPU/Auto should update Kokoro UI without another background check."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._kokoro_install_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()
    dialog._tts_install_status_checked = True
    dialog._tts_install_status_result = {
        "ok": True,
        "kokoro_installed": True,
        "kokoro_torch_status": {
            "installed": True,
            "valid": True,
            "cuda_available": False,
            "version": "2.12.1+cpu",
        },
        "system_cuda_available": True,
    }
    combo = QComboBox()
    combo.addItem("CPU", "cpu")
    combo.addItem("GPU (CUDA)", "cuda")
    dialog._fields["KOKORO_DEVICE"] = combo
    refresh_calls = 0

    def _refresh_again() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    monkeypatch.setattr(SettingsDialog, "_tts_page_is_current", lambda self: True)
    monkeypatch.setattr(SettingsDialog, "_refresh_tts_optional_install_status", lambda self: _refresh_again())

    try:
        combo.setCurrentIndex(1)
        SettingsDialog._handle_kokoro_device_changed(dialog)

        assert refresh_calls == 0
        assert dialog._kokoro_install_btn.isEnabled()
        assert dialog._kokoro_install_btn.text() == "Install Kokoro GPU support"
        assert dialog._kokoro_install_status_lbl.text() == "Kokoro GPU support is not installed."
    finally:
        dialog._kokoro_install_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_device_change_to_cuda_rechecks_fast_status(monkeypatch):
    """Switching to CUDA must not reuse a metadata-only Torch status."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._kokoro_install_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()
    dialog._tts_install_status_checked = True
    dialog._tts_install_status_running = False
    dialog._tts_install_status_result = {
        "ok": True,
        "kokoro_installed": True,
        "kokoro_torch_status": {"fast": True, "installed": True, "valid": True, "version": "2.12.1+cpu"},
        "system_cuda_available": True,
    }
    combo = QComboBox()
    combo.addItem("CPU", "cpu")
    combo.addItem("GPU (CUDA)", "cuda")
    dialog._fields["KOKORO_DEVICE"] = combo
    refresh_calls = 0

    def _refresh_again() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    monkeypatch.setattr(SettingsDialog, "_tts_page_is_current", lambda self: True)
    monkeypatch.setattr(SettingsDialog, "_refresh_tts_optional_install_status", lambda self: _refresh_again())

    try:
        combo.setCurrentIndex(1)
        SettingsDialog._handle_kokoro_device_changed(dialog)

        assert refresh_calls == 1
        assert dialog._tts_install_status_checked is False
    finally:
        dialog._kokoro_install_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        combo.deleteLater()
        app.processEvents()


def test_kokoro_fast_status_does_not_request_gpu_reinstall():
    """Fast status is acceptable only when selected settings do not need CUDA."""
    from ui.settings_panel.dialog import SettingsDialog

    needs_gpu = SettingsDialog._kokoro_needs_gpu_install_from_status(
        installed=True,
        selected_device="cpu",
        torch_status={"fast": True, "installed": True, "cuda_available": False},
        system_cuda_available=True,
    )

    assert needs_gpu is False


def test_kokoro_fast_status_requests_full_gpu_install_for_cuda():
    """A metadata-only Torch check must not mark explicit CUDA as fully installed."""
    from ui.settings_panel.dialog import SettingsDialog

    needs_gpu = SettingsDialog._kokoro_needs_gpu_install_from_status(
        installed=True,
        selected_device="cuda",
        torch_status={"fast": True, "installed": True, "cuda_available": False},
        system_cuda_available=True,
    )

    assert needs_gpu is True


def test_kokoro_broken_torch_status_requests_repair():
    """A broken Torch layer should be shown as a repair, not a healthy install."""
    from ui.settings_panel.dialog import SettingsDialog

    needs_gpu = SettingsDialog._kokoro_needs_gpu_install_from_status(
        installed=True,
        selected_device="auto",
        torch_status={"fast": True, "installed": True, "valid": False, "error": "Torch install looks incomplete."},
        system_cuda_available=True,
    )

    assert needs_gpu is True


def test_kokoro_torch_timeout_does_not_request_repair():
    """A slow Torch probe should not be flattened into a broken install."""
    from ui.settings_panel.dialog import SettingsDialog

    needs_gpu = SettingsDialog._kokoro_needs_gpu_install_from_status(
        installed=True,
        selected_device="cuda",
        torch_status={"installed": True, "valid": False, "timed_out": True, "error": "torch-status subprocess timed out."},
        system_cuda_available=True,
    )

    assert needs_gpu is False


def test_kokoro_background_cuda_status_uses_subprocess(monkeypatch):
    """CUDA status refresh should avoid importing Torch in the Settings process."""
    from core import optional_deps
    from ui.settings_panel.dialog import SettingsDialog

    calls: list[str] = []
    monkeypatch.setattr(
        optional_deps,
        "kokoro_torch_status_subprocess",
        lambda: calls.append("subprocess") or {"installed": True, "valid": True, "cuda_available": False},
    )

    dialog = SettingsDialog.__new__(SettingsDialog)
    status = SettingsDialog._kokoro_torch_status(dialog)

    assert calls == ["subprocess"]
    assert status["cuda_available"] is False


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_refresh_status_does_not_run_full_torch_check(monkeypatch):
    """Manual Kokoro status refresh should not freeze the UI with Torch verification."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

    from core import optional_deps
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(optional_deps.sys, "platform", "win32")
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._kokoro_install_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()
    dialog._tts_install_status_result = None
    combo = QComboBox()
    combo.addItem("GPU (CUDA)", "cuda")
    dialog._fields["KOKORO_DEVICE"] = combo
    monkeypatch.setattr(SettingsDialog, "_kokoro_installed", lambda self: True)
    monkeypatch.setattr(
        SettingsDialog,
        "_kokoro_torch_status",
        lambda self: (_ for _ in ()).throw(AssertionError("full Torch status should not run")),
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_kokoro_torch_status_fast",
        lambda self: {"fast": True, "installed": True, "valid": True, "version": "2.12.1+cpu"},
    )

    try:
        SettingsDialog._refresh_kokoro_install_status(dialog)

        assert dialog._kokoro_install_btn.isEnabled()
        assert dialog._kokoro_install_btn.text() == "Install Kokoro GPU support"
    finally:
        dialog._kokoro_install_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_status_warns_when_torch_probe_times_out():
    """Settings should show timeout as inconclusive instead of incomplete."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._kokoro_install_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()
    combo = QComboBox()
    combo.addItem("GPU (CUDA)", "cuda")
    dialog._fields["KOKORO_DEVICE"] = combo

    try:
        SettingsDialog._apply_kokoro_install_status(
            dialog,
            installed=True,
            mode="gpu",
            torch_status={"installed": True, "valid": False, "timed_out": True, "error": "torch-status subprocess timed out after 30s."},
            needs_gpu=False,
        )

        assert dialog._kokoro_install_btn.isEnabled()
        assert dialog._kokoro_install_btn.text() == "Reinstall Kokoro"
        assert "verification is still starting" in dialog._kokoro_install_status_lbl.text()
        assert "incomplete" not in dialog._kokoro_install_status_lbl.text().lower()
    finally:
        dialog._kokoro_install_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_status_warns_when_install_is_incomplete():
    """Settings should offer reinstall when Torch import is incomplete."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._kokoro_install_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()
    combo = QComboBox()
    combo.addItem("CPU", "cpu")
    dialog._fields["KOKORO_DEVICE"] = combo

    try:
        SettingsDialog._apply_kokoro_install_status(
            dialog,
            installed=True,
            mode="cpu",
            torch_status={"installed": True, "valid": False, "error": "Torch import is incomplete."},
            needs_gpu=False,
        )

        assert dialog._kokoro_install_btn.isEnabled()
        assert dialog._kokoro_install_btn.text() == "Install Kokoro"
        assert dialog._kokoro_install_status_lbl.text() == "Kokoro install is incomplete. Reinstall Kokoro."
    finally:
        dialog._kokoro_install_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        combo.deleteLater()
        app.processEvents()


def test_kokoro_post_install_fails_when_required_gpu_is_unavailable(monkeypatch):
    """GPU Kokoro install should fail when Torch cannot see CUDA after install."""
    from core import optional_deps, tts
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(tts, "prepare_kokoro_assets", lambda voice: {"voice": "voice.pt"})
    monkeypatch.setattr(optional_deps, "kokoro_runtime_import_status_subprocess", lambda: {"valid": True})
    monkeypatch.setattr(
        optional_deps,
        "kokoro_torch_status_subprocess",
        lambda: {"installed": True, "valid": True, "version": "2.12.1+cpu", "cuda_available": False},
    )

    ok, message = SettingsDialog._prepare_kokoro_after_install(
        voice="af_heart",
        require_gpu=True,
        progress=lambda _message: None,
        write_log=lambda _message: None,
    )

    assert ok is False
    assert message == (
        "Kokoro installed, but CUDA Torch verification failed: "
        "torch 2.12.1+cpu (CPU-only build)"
    )


def test_kokoro_asset_prepare_failure_matrix_is_controlled(monkeypatch):
    """Asset download failures become a failed install result with durable detail."""
    from core import tts
    from ui.settings_panel.dialog import SettingsDialog

    faults = (
        ConnectionError("network access is unavailable"),
        RuntimeError("package source is unavailable"),
        OSError("available disk space is insufficient"),
        PermissionError("filesystem permission is insufficient"),
        RuntimeError("dependency versions conflict"),
    )
    for fault in faults:
        monkeypatch.setattr(
            tts,
            "prepare_kokoro_assets",
            lambda _voice=None, voice=None, fault=fault: (_ for _ in ()).throw(fault),
        )
        logs: list[str] = []
        ok, message = SettingsDialog._prepare_kokoro_after_install(
            voice="af_heart",
            progress=lambda _message: None,
            write_log=logs.append,
        )
        assert ok is False
        assert str(fault) in message
        assert str(fault) in "\n".join(logs)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_asset_repair_cancel_stops_before_download(monkeypatch):
    """Cancelling repair keeps the current assets and never starts a worker."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QMessageBox, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    voice = QComboBox()
    voice.addItem("Heart", "af_heart")
    dialog._fields = {"KOKORO_VOICE": voice}
    dialog._kokoro_assets_mode = "repair"
    dialog._kokoro_assets_update_revision = ""
    dialog._kokoro_assets_btn = QPushButton()
    dialog._kokoro_install_status_lbl = QLabel()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )
    monkeypatch.setattr(
        dialog,
        "_start_async_test",
        lambda *_args, **_kwargs: pytest.fail("download worker started after cancellation"),
    )
    try:
        SettingsDialog._kokoro_assets_action(dialog)
        assert dialog._kokoro_assets_btn.isEnabled()
    finally:
        voice.deleteLater()
        dialog._kokoro_assets_btn.deleteLater()
        dialog._kokoro_install_status_lbl.deleteLater()
        app.processEvents()


def test_kokoro_post_install_verifies_runtime_import(monkeypatch):
    """Kokoro install should fail if runtime imports are broken after install."""
    from core import optional_deps, tts
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(tts, "prepare_kokoro_assets", lambda voice: {"voice": "voice.pt"})
    monkeypatch.setattr(
        optional_deps,
        "kokoro_runtime_import_status_subprocess",
        lambda: {"valid": False, "error": "AttributeError: module 'regex' has no attribute 'compile'"},
    )

    ok, message = SettingsDialog._prepare_kokoro_after_install(
        voice="af_heart",
        progress=lambda _message: None,
        write_log=lambda _message: None,
    )

    assert ok is False
    assert "runtime verification failed" in message
    assert "regex" in message


def test_kokoro_post_install_uses_subprocess_verification(monkeypatch):
    """Inline post-install checks should not pin native Kokoro/Torch modules."""
    from core import optional_deps, tts
    from ui.settings_panel.dialog import SettingsDialog

    calls: list[str] = []
    monkeypatch.setattr(tts, "prepare_kokoro_assets", lambda voice: {"voice": "voice.pt"})
    monkeypatch.setattr(
        optional_deps,
        "kokoro_runtime_import_status",
        lambda: (_ for _ in ()).throw(AssertionError("direct Kokoro import should not run")),
    )
    monkeypatch.setattr(
        optional_deps,
        "kokoro_torch_status",
        lambda: (_ for _ in ()).throw(AssertionError("direct Torch import should not run")),
    )
    monkeypatch.setattr(
        optional_deps,
        "kokoro_runtime_import_status_subprocess",
        lambda: calls.append("runtime") or {"valid": True},
    )
    monkeypatch.setattr(
        optional_deps,
        "kokoro_torch_status_subprocess",
        lambda: calls.append("torch") or {"installed": True, "valid": True, "cuda_available": True},
    )

    ok, message = SettingsDialog._prepare_kokoro_after_install(
        voice="af_heart",
        require_gpu=True,
        progress=lambda _message: None,
        write_log=lambda _message: None,
    )

    assert ok is True
    assert message == "Kokoro GPU support installed and local voice is ready."
    assert calls == ["runtime", "torch"]


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
@pytest.mark.parametrize(("device_value", "expected_require_gpu"), [("cuda", True), ("auto", False)])
def test_kokoro_install_uses_gpu_packages_for_gpu_and_auto(monkeypatch, device_value, expected_require_gpu):
    """GPU and Auto should install CUDA Torch while only explicit GPU requires CUDA."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox

    from core import optional_deps
    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(optional_deps.sys, "platform", "win32")
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    device = QComboBox()
    device.addItem("Device", device_value)
    dialog._fields = {
        "TTS_PROVIDER": QComboBox(),
        "KOKORO_DEVICE": device,
        "KOKORO_VOICE": QLineEdit("af_heart"),
        "KOKORO_LANG_CODE": QLineEdit("a"),
        "KOKORO_SPEED": QLineEdit("1.0"),
        "KOKORO_SAMPLE_RATE": QLineEdit("24000"),
        "TTS_VOLUME": QLineEdit("1.0"),
    }
    dialog._fields["TTS_PROVIDER"].addItems(["none", "kokoro"])
    captured: dict[str, object] = {}
    monkeypatch.setattr(SettingsDialog, "_kokoro_installed", lambda self: False)
    monkeypatch.setattr(
        dialog_mod.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    def fake_install(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(SettingsDialog, "_install_optional_tts_package", fake_install)

    try:
        SettingsDialog._install_kokoro(dialog)

        assert captured["packages"] == optional_deps.kokoro_install_packages("cuda")
        assert captured["pre_install_packages"] == optional_deps.kokoro_torch_install_packages("cuda")
        assert captured["remove_artifacts"] == optional_deps.kokoro_remove_artifacts()
        assert "torch==2.11.0+cu128" in captured["pre_install_packages"]
        assert "torch==2.11.0+cu128" in captured["packages"]
        assert captured["external_plan_extra"]["kokoro_require_gpu"] is expected_require_gpu
        assert captured["external_plan_extra"]["settings_updates"]["TTS_PROVIDER"] == "kokoro"
        assert captured["external_plan_extra"]["settings_updates"]["KOKORO_DEVICE"] == device_value
        assert dialog._fields["TTS_PROVIDER"].currentText() == "kokoro"
        assert captured["success_message"] == "Kokoro GPU support installed and local voice is ready."
    finally:
        for widget in dialog._fields.values():
            widget.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_reinstall_click_does_not_run_full_torch_check(monkeypatch):
    """Clicking GPU support should use package metadata, not full Torch verification."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox

    from core import optional_deps
    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(optional_deps.sys, "platform", "win32")
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    device = QComboBox()
    device.addItem("GPU (CUDA)", "cuda")
    dialog._fields = {
        "KOKORO_DEVICE": device,
        "KOKORO_VOICE": QLineEdit("af_heart"),
        "KOKORO_LANG_CODE": QLineEdit("a"),
        "KOKORO_SPEED": QLineEdit("1.0"),
        "KOKORO_SAMPLE_RATE": QLineEdit("24000"),
        "TTS_VOLUME": QLineEdit("1.0"),
    }
    dialog._tts_install_status_result = None
    captured: dict[str, object] = {}
    monkeypatch.setattr(SettingsDialog, "_kokoro_installed", lambda self: True)
    monkeypatch.setattr(
        optional_deps,
        "optional_package_spec_status",
        lambda *_args, **_kwargs: {"installed": False, "valid": False},
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_kokoro_torch_status",
        lambda self: (_ for _ in ()).throw(AssertionError("full Torch status should not run")),
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_kokoro_torch_status_fast",
        lambda self: {"fast": True, "installed": True, "valid": True, "version": "2.12.1+cpu"},
    )
    monkeypatch.setattr(
        dialog_mod.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    def fake_install(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(SettingsDialog, "_install_optional_tts_package", fake_install)

    try:
        SettingsDialog._install_kokoro(dialog)

        assert captured["packages"] == optional_deps.kokoro_install_packages("cuda")
        assert captured["pre_install_packages"] == optional_deps.kokoro_torch_install_packages("cuda")
        assert captured["remove_artifacts"] == optional_deps.kokoro_remove_artifacts()
        assert captured["reinstall"] is False
        assert captured["success_message"] == "Kokoro GPU support installed and local voice is ready."
    finally:
        for widget in dialog._fields.values():
            widget.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_kokoro_reinstall_click_passes_reinstall(monkeypatch):
    """Clicking installed Kokoro reinstall should force package replacement."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox

    from core import optional_deps
    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    device = QComboBox()
    device.addItem("CPU", "cpu")
    dialog._fields = {
        "KOKORO_DEVICE": device,
        "KOKORO_VOICE": QLineEdit("af_heart"),
        "KOKORO_LANG_CODE": QLineEdit("a"),
        "KOKORO_SPEED": QLineEdit("1.0"),
        "KOKORO_SAMPLE_RATE": QLineEdit("24000"),
        "TTS_VOLUME": QLineEdit("1.0"),
    }
    dialog._tts_install_status_result = None
    captured: dict[str, object] = {}
    monkeypatch.setattr(SettingsDialog, "_kokoro_installed", lambda self: True)
    monkeypatch.setattr(
        optional_deps,
        "optional_package_spec_status",
        lambda package, *_args, **_kwargs: {"installed": package == "kokoro", "valid": package == "kokoro"},
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_kokoro_torch_status_fast",
        lambda self: {"fast": True, "installed": True, "valid": True, "version": "2.12.1+cpu"},
    )
    monkeypatch.setattr(
        dialog_mod.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    def fake_install(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(SettingsDialog, "_install_optional_tts_package", fake_install)

    try:
        SettingsDialog._install_kokoro(dialog)

        assert captured["packages"] == optional_deps.kokoro_install_packages("cpu")
        assert captured["pre_install_packages"] == []
        assert captured["remove_artifacts"] == optional_deps.kokoro_remove_artifacts()
        assert captured["reinstall"] is True
        assert captured["success_message"] == "Kokoro reinstalled and local voice is ready."
    finally:
        for widget in dialog._fields.values():
            widget.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_elevenlabs_installed_status_offers_reinstall():
    """Installed ElevenLabs should keep an enabled reinstall action."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._elevenlabs_install_btn = QPushButton()
    dialog._elevenlabs_install_status_lbl = QLabel()

    try:
        SettingsDialog._apply_elevenlabs_install_status(dialog, True)

        assert dialog._elevenlabs_install_btn.isEnabled()
        assert dialog._elevenlabs_install_btn.text() == "Reinstall ElevenLabs"
        assert dialog._elevenlabs_install_status_lbl.text() == "ElevenLabs is installed."
    finally:
        dialog._elevenlabs_install_btn.deleteLater()
        dialog._elevenlabs_install_status_lbl.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_elevenlabs_installed_files_with_broken_runtime_offer_repair():
    """Settings must not show green when the pinned SDK fails to import."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._elevenlabs_install_btn = QPushButton()
    dialog._elevenlabs_install_status_lbl = QLabel()
    try:
        SettingsDialog._apply_elevenlabs_install_status(
            dialog,
            False,
            runtime_status={"installed": True, "valid": False, "error": "ImportError: broken dependency"},
        )

        assert dialog._elevenlabs_install_btn.text() == "Reinstall ElevenLabs"
        assert "SDK cannot load" in dialog._elevenlabs_install_status_lbl.text()
        assert "broken dependency" in dialog._elevenlabs_install_status_lbl.text()
    finally:
        dialog._elevenlabs_install_btn.deleteLater()
        dialog._elevenlabs_install_status_lbl.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_stt_installed_files_with_broken_runtime_offer_repair():
    """The Voice page distinguishes STT package presence from runtime usability."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._stt_download_btn = QPushButton()
    dialog._stt_install_status_lbl = QLabel()
    try:
        SettingsDialog._apply_stt_runtime_status(
            dialog,
            {
                "stt_package_installed": True,
                "stt_runtime_status": {"installed": True, "valid": False, "error": "ImportError: ctranslate2 DLL"},
                "stt_model": "base",
                "stt_device": "cpu",
                "stt_compute": "int8",
            },
        )

        assert dialog._stt_download_btn.text() == "Reinstall STT"
        assert "faster-whisper cannot load" in dialog._stt_install_status_lbl.text()
        assert "ctranslate2" in dialog._stt_install_status_lbl.text()
    finally:
        dialog._stt_download_btn.deleteLater()
        dialog._stt_install_status_lbl.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_elevenlabs_status_preserves_staged_apply_retry():
    """ElevenLabs should show the same restart-apply state as other optional packages."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._elevenlabs_install_btn = QPushButton()
    dialog._elevenlabs_install_status_lbl = QLabel()

    try:
        SettingsDialog._apply_elevenlabs_install_status(
            dialog,
            False,
            install_status={
                "ok": False,
                "restart_apply": True,
                "message": "ElevenLabs staged install failed: PermissionError: locked file. OpenWand will retry at the next restart.",
            },
        )

        assert dialog._elevenlabs_install_btn.isEnabled()
        assert dialog._elevenlabs_install_btn.text() == "Restart app now"
        assert "retry at the next restart" in dialog._elevenlabs_install_status_lbl.text()
    finally:
        dialog._elevenlabs_install_btn.deleteLater()
        dialog._elevenlabs_install_status_lbl.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_elevenlabs_reinstall_click_passes_reinstall(monkeypatch):
    """Clicking ElevenLabs reinstall should force package replacement."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QMessageBox

    from core import optional_deps
    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    _app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    provider = QComboBox()
    provider.addItems(["none", "elevenlabs"])
    dialog._fields = {"TTS_PROVIDER": provider}
    captured: dict[str, object] = {}
    monkeypatch.setattr(SettingsDialog, "_elevenlabs_installed", lambda self: True)
    monkeypatch.setattr(
        dialog_mod.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    def fake_install(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(SettingsDialog, "_install_optional_tts_package", fake_install)

    try:
        SettingsDialog._install_elevenlabs(dialog)

        assert captured["packages"] == optional_deps.ELEVENLABS_INSTALL_PACKAGES
        assert captured["reinstall"] is True
        assert captured["external_plan_extra"]["settings_updates"]["TTS_PROVIDER"] == "elevenlabs"
        assert provider.currentText() == "elevenlabs"
        assert captured["success_message"] == "ElevenLabs reinstalled. Add your API key, then click Test TTS."
    finally:
        provider.deleteLater()
        _app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_shared_speech_optional_install_cancellation_starts_no_installer(monkeypatch):
    """Cancelling any shared optional speech install leaves packages untouched."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox

    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    monkeypatch.setattr(SettingsDialog, "_elevenlabs_installed", lambda self: False)
    monkeypatch.setattr(SettingsDialog, "_live_voice_installed", lambda self: False)
    monkeypatch.setattr(
        SettingsDialog,
        "_kokoro_install_snapshot",
        lambda self: {
            "installed": False,
            "needs_gpu": False,
            "needs_repair": False,
            "mode": "cpu",
        },
    )
    monkeypatch.setattr(
        dialog_mod.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )
    monkeypatch.setattr(
        SettingsDialog,
        "_install_optional_tts_package",
        lambda *_args, **_kwargs: pytest.fail("installer started after cancellation"),
    )

    fields = {
        "KOKORO_VOICE": QLineEdit("af_heart"),
        "KOKORO_LANG_CODE": QLineEdit("a"),
        "KOKORO_DEVICE": QLineEdit("cpu"),
        "KOKORO_SPEED": QLineEdit("1.0"),
        "KOKORO_SAMPLE_RATE": QLineEdit("24000"),
        "TTS_VOLUME": QLineEdit("1.0"),
    }
    for key, value in (
        ("STT_MODEL", "base"),
        ("STT_DEVICE", "cpu"),
        ("STT_COMPUTE_TYPE", "int8"),
        ("STT_LANGUAGE", "en"),
        ("STT_BEAM_SIZE", "5"),
    ):
        combo = QComboBox()
        combo.addItem(value, value)
        fields[key] = combo
    dialog._fields = fields

    try:
        SettingsDialog._install_elevenlabs(dialog)
        SettingsDialog._install_live_voice(dialog)
        SettingsDialog._install_kokoro(dialog)
        SettingsDialog._preload_stt_model(dialog)
        app.processEvents()
    finally:
        for widget in fields.values():
            widget.deleteLater()
        app.processEvents()


def test_i18n_translates_settings_apply_tool_warning(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from core.llm_clients.client import tool_capability_warnings
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    warning = tool_capability_warnings(True, llm_provider="chatgpt")[0]
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh"
    try:
        i18n.set_language(app=app)

        assert i18n.t("Heads up") == "\u63d0\u9192"
        assert i18n.t("Your settings were saved, but:") == "\u4f60\u7684\u8bbe\u7f6e\u5df2\u4fdd\u5b58\uff0c\u4f46\uff1a"
        translated = i18n.t(warning)
        assert translated != warning
        assert "ChatGPT" in translated
        assert "\u5de5\u5177" in translated
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_i18n_translates_stt_backend_status_messages(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    source = "Configured backend: {summary} — active backend appears after recording starts."
    try:
        for language in ("zh", "zh-Hant", "es", "fr"):
            config.APP_LANGUAGE = language
            i18n.set_language(app=app)

            translated = i18n.t(source)

            assert translated != source
            assert "{summary}" in translated
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_llm_model_routing_surface_translates_to_traditional_chinese(isolated_default_profile, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QPushButton

    import config
    from core.prompt_i18n import caller_intent_template
    from ui import i18n
    from ui.settings_panel import dialog as settings_dialog

    monkeypatch.setattr(settings_dialog, "_read_env", lambda: {})
    SettingsDialog = settings_dialog.SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    dialog = SettingsDialog()

    try:
        dialog._add_api_key_row("openai")
        dialog._add_caller_block(
            label="General",
            intents=[{"key": "w", "label": "", "prompt": ""}],
        )
        translated_caller = dialog._caller_blocks[-1]
        label_texts = {label.text() for label in dialog.findChildren(QLabel)}
        button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
        placeholder_texts = {
            edit.placeholderText()
            for edit in dialog.findChildren(QLineEdit)
            if edit.placeholderText()
        }
        all_combo_texts = {
            combo.itemText(i)
            for combo in dialog.findChildren(QComboBox)
            for i in range(combo.count())
        }
        tooltip_texts = set()
        for cls in (QLabel, QComboBox, QLineEdit, QPushButton):
            tooltip_texts.update(
                widget.toolTip()
                for widget in dialog.findChildren(cls)
                if widget.toolTip()
            )
        visible_texts = label_texts | button_texts | placeholder_texts | all_combo_texts | tooltip_texts
        combo_texts = {
            row["api_key_combo"].itemText(i)
            for rows in dialog._model_section_rows.values()
            for row in rows
            for i in range(row["api_key_combo"].count())
        }

        assert "Model routing" not in label_texts
        assert "Choose which saved credential and model powers each purpose." not in label_texts
        assert "<small><b>Provider</b></small>" not in label_texts
        assert not any("CHAT MODEL" in text for text in label_texts)
        assert "Test Chat model" not in button_texts
        assert "Codex (ChatGPT subscription) [OAuth]" not in combo_texts
        for fragment in (
            "Provider credentials",
            "Sign in or save provider API keys",
            "AUTHENTICATION",
            "Sign in opens GitHub in your browser",
            "Add the providers you use",
            "<small><b>Alias</b></small>",
            "alias (optional)",
            "Custom provider",
            "Any OpenAI-compatible endpoint",
            "Speech to Text",
            "Whisper model settings",
            "API keys are saved to the OS keychain",
            "Beam size",
            "Device",
            "+ Add Caller Hotkey",
            "Tools with <b>no keywords</b>",
        ):
            assert not any(fragment in text for text in visible_texts)

        assert any(text.endswith("\u63d0\u4f9b\u8005\u6191\u8b49") for text in label_texts)
        assert i18n.t("OAuth logins").upper() in label_texts
        assert i18n.t("ChatGPT OAuth") in label_texts
        assert i18n.t("Allows you to use ChatGPT subscription models in Model settings.") in label_texts
        assert i18n.t(
            "Allows authenticated GitHub tools to give the model repository metadata and "
            "issues/PRs as context, and lets you use Copilot models when your account has "
            "Copilot access."
        ) in label_texts
        assert "<small><b>\u5225\u540d</b></small>" in label_texts
        assert "\u5225\u540d\uff08\u9078\u586b\uff09" in placeholder_texts
        assert "\u81ea\u8a02\u63d0\u4f9b\u8005".upper() in label_texts
        custom_endpoint_note = i18n.t(
            "Choose a known OpenAI-compatible endpoint, or select Custom endpoint to enter an address. Select Custom in a model row below to use it."
        )
        assert any(custom_endpoint_note in text for text in label_texts)
        assert "\u8a9e\u97f3\u8f49\u6587\u5b57".upper() in label_texts
        assert any(text.startswith("\u88dd\u7f6e") for text in label_texts)
        assert any(text.startswith("\u675f\u5bec") for text in label_texts)
        assert "5\uff08\u5efa\u8b70\uff09" in all_combo_texts
        expected_auto_device = i18n.t(
            "Auto (CPU)" if sys.platform == "darwin" else "Auto (GPU if available)"
        )
        assert expected_auto_device in all_combo_texts
        assert any("API \u5bc6\u9470\u6703\u4fdd\u5b58\u5230\u7cfb\u7d71\u9470\u5319\u4e32" in text for text in label_texts)
        assert "\u65b0\u589e\u610f\u5716\u5feb\u901f\u9375" in button_texts
        assert "\u6a21\u578b\u8def\u7531" in label_texts
        assert "<small><b>\u63d0\u4f9b\u8005</b></small>" in label_texts
        assert any(text.endswith("\u804a\u5929\u6a21\u578b") for text in label_texts)
        assert "\u6e2c\u8a66\u804a\u5929\u6a21\u578b" in button_texts
        assert any(
            text.startswith("ChatGPT Plus/Pro\uff08OAuth \u8a02\u95b1\uff09")
            for text in combo_texts
        )
        assert translated_caller["label"].text() == i18n.t("General") == "\u901a\u7528"
        assert dialog._caller_label_value(translated_caller) == "General"
        expected_intent = caller_intent_template(0, 0, "zh-Hant")
        built_in_intent = dialog._caller_blocks[0]["intent_rows"][0]
        assert built_in_intent["label"].text() == expected_intent["label"]
        assert built_in_intent["prompt"].toPlainText() == expected_intent["prompt"]
        assert {
            i18n.t("Paste raw transcript"),
            i18n.t("Light LLM cleanup"),
        } == {
            dialog._fields["DICTATE_MODE"].itemText(index)
            for index in range(dialog._fields["DICTATE_MODE"].count())
        }
        intent_row = translated_caller["intent_rows"][0]
        assert intent_row["label"].placeholderText() == i18n.t("Label")
        assert intent_row["prompt"].placeholderText() == i18n.t("Prompt sent to LLM...")
        assert translated_caller["context_ambient"].toolTip().startswith("\u61c9\u7528\u7a0b\u5f0f\u4e0a\u4e0b\u6587")
        assert translated_caller["context_clipboard"].toolTip().startswith("\u526a\u8cbc\u7c3f")
        assert translated_caller["file_access"].toolTip().startswith("\u672c\u6a5f\u6a94\u6848")
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)
        dialog.deleteLater()
        app.processEvents()


def test_qt_catalogs_translate_exact_spanish_and_french_sources(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    try:
        config.APP_LANGUAGE = "es"
        i18n.set_language(app=app)
        assert i18n.t("Browser/Web: ") == "Navegador/Web: "
        assert i18n.t("App") == "Aplicaci\u00f3n"
        assert i18n.t("Selection") == "Selecci\u00f3n"
        assert i18n.t("Clipboard") == "Portapapeles"
        assert i18n.t("Files") == "Archivos"
        assert i18n.t("Settings") == "Configuración"

        config.APP_LANGUAGE = "fr"
        i18n.set_language(app=app)
        assert i18n.t("Browser/Web: ") == "Navigateur/Web : "
        assert i18n.t("App") == "Application"
        assert i18n.t("Selection") == "S\u00e9lection"
        assert i18n.t("Clipboard") == "Presse-papiers"
        assert i18n.t("Files") == "Fichiers"
        assert i18n.t("Settings") == "Paramètres"
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


def test_i18n_translates_auth_ui_but_keeps_technical_tokens(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh"
    try:
        i18n.set_language(app=app)

        assert i18n.t("Clear token") == "\u6e05\u9664\u4ee4\u724c"
        assert i18n.t("Not configured") == "\u672a\u914d\u7f6e"
        assert i18n.t("github_pat_\u2026 (not saved to .env)") == (
            "github_pat_\u2026\uff08\u4e0d\u4f1a\u4fdd\u5b58\u5230 .env\uff09"
        )
        assert "Copilot Requests: Read-only" in i18n.t(
            "Fine-grained PAT with Copilot Requests: Read-only. Stored in OS keychain."
        )
        assert ".env" in i18n.t("github_pat_\u2026 (not saved to .env)")
        assert "SDK" in i18n.t("Test token / SDK")
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_app_settings_surface_translates_to_traditional_chinese():
    """Verify app settings page checkboxes and labels translate to Traditional Chinese."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QAbstractButton, QApplication, QComboBox, QLabel, QLineEdit

    import config
    from ui import i18n
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    dialog = SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()

    try:
        visible_texts = {
            label.text()
            for label in dialog.findChildren(QLabel)
            if label.text()
        }
        visible_texts.update(
            button.text()
            for button in dialog.findChildren(QAbstractButton)
            if button.text()
        )
        visible_texts.update(
            edit.placeholderText()
            for edit in dialog.findChildren(QLineEdit)
            if edit.placeholderText()
        )
        visible_texts.update(
            combo.itemText(index)
            for combo in dialog.findChildren(QComboBox)
            for index in range(combo.count())
            if combo.itemText(index)
        )

        for fragment in (
            "Trust/privacy mode",
            "Built-in privacy filter",
            "Wheel-scroll text bubble",
            "Snap bubble scroll back while speaking",
            "Elaborate prompt",
            "App language",
            "Assistant language",
            "Icon size (px)",
            "Text bubble width (px)",
            "Text bubble lines",
            "Text bubble font size (pt)",
            "Uninstall OpenWand",
        ):
            assert not any(fragment in text for text in visible_texts)

        assert "\u5167\u5efa\u898f\u5247 \u2014 \u7121\u9700\u4e0b\u8f09" in visible_texts
        assert "\u6a5f\u5bc6\u8207\u767b\u5165\u6191\u8b49" in visible_texts
        assert "\u806f\u7d61\u8cc7\u6599" in visible_texts
        assert "\u5141\u8a31\u6efe\u8f2a\u6372\u52d5\u6587\u5b57\u6c23\u6ce1" in visible_texts
        assert "\u6717\u8b80\u6642\u81ea\u52d5\u6372\u56de\u76ee\u524d\u4f4d\u7f6e" in visible_texts
        assert "\u89e3\u9664\u5b89\u88dd OpenWand" in visible_texts
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_status_label_setters_localize_dynamic_text():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    import config
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    label = QLabel()
    dialog = SettingsDialog.__new__(SettingsDialog)

    try:
        SettingsDialog._set_status_label(dialog, label, None, "Not configured")
        assert label.text() == "\u672a\u8a2d\u5b9a"

        SettingsDialog._set_test_status(dialog, label, False, "Error reading status: keychain")
        assert label.text().startswith("\u8b80\u53d6\u72c0\u614b\u6642\u767c\u751f\u932f\u8aa4")
        assert "keychain" in label.text()
    finally:
        config.APP_LANGUAGE = old_language
        label.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_elaborate_prompt_field_follows_auto_elaborate_checkbox():
    """Verify elaborate prompt is only shown when auto-elaborate is enabled."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    dialog._build_deferred_pages()

    try:
        checkbox = dialog._fields["CHAT_AUTO_ELABORATE"]
        field = dialog._fields["CHAT_ELABORATE_PROMPT"]
        label = dialog._chat_elaborate_prompt_label

        checkbox.setChecked(False)
        assert field.isHidden()
        assert label is not None and label.isHidden()

        checkbox.setChecked(True)
        assert not field.isHidden()
        assert label is not None and not label.isHidden()

        checkbox.setChecked(False)
        assert field.isHidden()
        assert label is not None and label.isHidden()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_llm_tab_hides_advanced_chat_harness_controls_until_expanded():
    """Verify chat harness settings live in a closed LLM Advanced drawer."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        dialog.show()
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("LLM"))
        app.processEvents()

        for key in (
            "CHAT_TOOL_TRACE_UI",
            "OPENWAND_PLANNED_CHUNKING",
            "OPENWAND_PLANNED_CHUNKING_CHUNKS",
            "OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS",
            "CHAT_REASONING_EFFORT",
            "CHAT_AUTO_ELABORATE",
            "CHAT_ELABORATE_PROMPT",
        ):
            assert key in dialog._fields

        current_tab = dialog._tabs.currentWidget()
        buttons = [button for button in current_tab.findChildren(QPushButton) if button.text() == "Advanced settings"]
        assert buttons
        advanced_button = buttons[0]
        assert advanced_button.isCheckable()
        assert not advanced_button.isChecked()
        assert not dialog._fields["CHAT_REASONING_EFFORT"].isVisible()

        advanced_button.setChecked(True)
        dialog._fields["CHAT_AUTO_ELABORATE"].setChecked(True)
        app.processEvents()

        assert dialog._fields["CHAT_REASONING_EFFORT"].isVisible()
        info_icons = {
            label.property("_openwand_info_for"): label
            for label in current_tab.findChildren(QLabel)
            if label.property("_openwand_info_for")
        }
        checkbox_keys = (
            "CHAT_TOOL_TRACE_UI",
            "OPENWAND_PLANNED_CHUNKING",
            "CHAT_AUTO_ELABORATE",
        )
        assert set(info_icons) == set(checkbox_keys)
        for key in checkbox_keys:
            assert info_icons[key].text() == "ⓘ"
            assert info_icons[key].toolTip()
            assert info_icons[key].toolTip() == dialog._fields[key].toolTip()

        value_fields = (
            dialog._fields["OPENWAND_PLANNED_CHUNKING_CHUNKS"],
            dialog._fields["OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS"],
            dialog._fields["CHAT_REASONING_EFFORT"],
            dialog._fields["CHAT_ELABORATE_PROMPT"],
        )
        checkbox_rows = [dialog._fields[key].parentWidget() for key in checkbox_keys]
        assert min(row.y() for row in checkbox_rows) > max(field.y() for field in value_fields)
        assert [row.y() for row in checkbox_rows] == sorted(row.y() for row in checkbox_rows)
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_settings_open_requests_are_coalesced(monkeypatch):
    from ui.settings_panel import dialog as settings_dialog

    callbacks = []
    opened = []

    def fake_single_shot(_delay, callback):
        callbacks.append(callback)

    def fake_open_now(**kwargs):
        opened.append(kwargs)
        settings_dialog._settings_open_pending = False

    monkeypatch.setattr(settings_dialog.QTimer, "singleShot", fake_single_shot)
    monkeypatch.setattr(settings_dialog, "_open_settings_now", fake_open_now)
    settings_dialog._settings_open_pending = False

    try:
        settings_dialog.open_settings(parent=None, on_apply=None, initial_page="LLM")
        settings_dialog.open_settings(parent=None, on_apply=None)
        assert len(callbacks) == 1

        callbacks.pop()()
        assert opened[-1]["initial_page"] == "LLM"
        settings_dialog.open_settings(parent=None, on_apply=None)
        assert len(callbacks) == 1
    finally:
        settings_dialog._settings_open_pending = False


def test_hidden_settings_dialog_is_replaced_without_clearing_new_one(monkeypatch):
    from ui.settings_panel import dialog as settings_dialog

    class FakeSignal:
        def __init__(self) -> None:
            self._callbacks = []

        def connect(self, callback) -> None:
            self._callbacks.append(callback)

        def emit(self, obj=None) -> None:
            for callback in list(self._callbacks):
                callback(obj)

    class FakeDialog:
        """Qt dialog for fake dialog."""
        created = []

        def __init__(self, parent=None, on_apply=None, on_setup_check=None, extra_tools=None) -> None:
            self.parent = parent
            self._on_apply = on_apply
            self._on_setup_check = on_setup_check
            self._extra_tools = extra_tools or []
            self._disposing = False
            self.visible = True
            self.deleted = False
            self.destroyed = FakeSignal()
            FakeDialog.created.append(self)

        def objectName(self):
            return "fake-settings"

        def isVisible(self):
            return self.visible

        def isMinimized(self):
            return False

        def showNormal(self):
            pass

        def show(self):
            self.visible = True

        def raise_(self):
            pass

        def activateWindow(self):
            pass

        def deleteLater(self):
            self.deleted = True

    old = FakeDialog()
    old.visible = False
    new_dialogs = []

    def make_dialog(parent=None, on_apply=None, on_setup_check=None, extra_tools=None):
        dialog = FakeDialog(
            parent=parent,
            on_apply=on_apply,
            on_setup_check=on_setup_check,
            extra_tools=extra_tools,
        )
        new_dialogs.append(dialog)
        return dialog

    monkeypatch.setattr(settings_dialog, "SettingsDialog", make_dialog)
    settings_dialog._settings_dialog = old
    settings_dialog._settings_open_pending = True

    try:
        settings_dialog._open_settings_now(parent=None, on_apply=lambda: None)

        assert old.deleted is True
        assert old._disposing is True
        assert new_dialogs
        assert settings_dialog._settings_dialog is new_dialogs[-1]

        old.destroyed.emit(old)
        assert settings_dialog._settings_dialog is new_dialogs[-1]
    finally:
        settings_dialog._settings_dialog = None
        settings_dialog._settings_open_pending = False
        FakeDialog.created.clear()


def test_cancel_status_refresh_invalidates_pending_results():
    from ui.settings_panel.dialog import SettingsDialog

    class FakeTimer:
        def __init__(self) -> None:
            self.stopped = False

        def isActive(self):
            return True

        def stop(self):
            self.stopped = True

    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._status_refresh_token = 3
    dialog._status_refresh_running = True
    dialog._pending_status_results = [(3, "_github_status_lbl", None, "Checking status...")]
    dialog._pending_status_results_lock = threading.Lock()
    dialog._status_result_timer = FakeTimer()

    SettingsDialog._cancel_status_refresh(dialog)

    assert dialog._status_refresh_token == 4
    assert dialog._status_refresh_running is False
    assert dialog._pending_status_results == []
    assert dialog._status_result_timer.stopped is True


def test_cancel_async_ui_updates_stops_test_and_auth_timers():
    from ui.settings_panel.dialog import SettingsDialog

    class FakeTimer:
        def __init__(self) -> None:
            self.stopped = False

        def isActive(self):
            return True

        def stop(self):
            self.stopped = True

    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._status_refresh_token = 0
    dialog._status_refresh_running = True
    dialog._pending_status_results = [(0, "_status", None, "Checking status...")]
    dialog._pending_status_results_lock = threading.Lock()
    dialog._status_result_timer = FakeTimer()
    dialog._running_test_tokens = {("llm_test", 1)}
    dialog._latest_test_token = {"llm_test": 1}
    dialog._pending_test_results = [("llm_test", 1, True, "OK")]
    dialog._pending_test_results_lock = threading.Lock()
    dialog._pending_test_progress = [("llm_test", 1, "Testing...")]
    dialog._pending_test_progress_lock = threading.Lock()
    dialog._test_result_timer = FakeTimer()
    dialog._auth_poll_timer = FakeTimer()
    dialog._github_auth_poll_timer = FakeTimer()

    SettingsDialog._cancel_async_ui_updates(dialog)

    assert dialog._status_refresh_running is False
    assert dialog._pending_status_results == []
    assert dialog._status_result_timer.stopped is True
    assert dialog._running_test_tokens == set()
    assert dialog._latest_test_token == {}
    assert dialog._pending_test_results == []
    assert dialog._pending_test_progress == []
    assert dialog._test_result_timer.stopped is True
    assert dialog._auth_poll_timer.stopped is True
    assert dialog._github_auth_poll_timer.stopped is True


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_drain_test_progress_coalesces_timer_updates():
    """Installer heartbeat updates should replace one status line, not replay every tick."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from ui.settings_panel.dialog import SettingsDialog

    class FakeTimer:
        def __init__(self) -> None:
            self.stopped = False

        def isActive(self):
            return True

        def stop(self):
            self.stopped = True

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    label = QLabel()
    dialog._kokoro_install_status_lbl = label
    dialog._disposing = False
    dialog._pending_test_progress = [
        ("kokoro_install", 1, "Installing Kokoro: preparing local assets for 0s."),
        ("kokoro_install", 1, "Installing Kokoro: preparing local assets for 20s."),
    ]
    dialog._pending_test_progress_lock = threading.Lock()
    dialog._pending_test_results = []
    dialog._pending_test_results_lock = threading.Lock()
    dialog._latest_test_token = {"kokoro_install": 1}
    dialog._running_test_tokens = {("kokoro_install", 1)}
    dialog._test_result_timer = FakeTimer()

    try:
        SettingsDialog._drain_test_results(dialog)

        assert label.text() == "Installing Kokoro: preparing local assets for 20s."
        assert dialog._pending_test_progress == []
        assert dialog._test_result_timer.stopped is False
    finally:
        label.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_open_status_refresh_starts_independent_auth_workers(monkeypatch):
    """Verify one stuck auth provider cannot block all sign-in status labels."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from ui.settings_panel.dialog import SettingsDialog

    class FakeTimer:
        """Small QTimer stand-in for status refresh tests."""
        def __init__(self) -> None:
            self.started = False

        def isActive(self):
            return self.started

        def start(self):
            self.started = True

        def stop(self):
            self.started = False

    class FakeThread:
        """Capture worker thread names without running auth imports."""
        def __init__(self, target=None, daemon=False, name="") -> None:
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self):
            started.append(self.name)

    app = QApplication.instance() or QApplication(sys.argv)
    started: list[str] = []
    single_shots: list[tuple[int, object]] = []
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._disposing = False
    dialog._status_refresh_token = 0
    dialog._status_refresh_running = False
    dialog._pending_status_results = []
    dialog._pending_status_results_lock = threading.Lock()
    dialog._status_result_timer = FakeTimer()
    dialog._chatgpt_status_lbl = QLabel()
    dialog._github_status_lbl = QLabel()
    monkeypatch.setattr("ui.settings_panel.dialog.threading.Thread", FakeThread)
    monkeypatch.setattr(
        "ui.settings_panel.dialog.QTimer.singleShot",
        staticmethod(lambda ms, callback: single_shots.append((ms, callback))),
    )

    try:
        SettingsDialog._schedule_open_status_refresh(dialog)

        assert started == [
            "settings-status-chatgpt",
            "settings-status-github",
        ]
        assert dialog._status_refresh_running is True
        assert dialog._pending_status_attrs == {
            "_chatgpt_status_lbl",
            "_github_status_lbl",
        }
        assert dialog._chatgpt_status_lbl.text() == "Checking status..."
        assert dialog._github_status_lbl.text() == "Checking status..."
        assert single_shots
    finally:
        for label in (dialog._chatgpt_status_lbl, dialog._github_status_lbl):
            label.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_auth_status_timeout_replaces_stuck_checking_labels():
    """Verify packaged-app auth status hangs recover to a visible timeout."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from ui.settings_panel.dialog import SettingsDialog

    class FakeTimer:
        """Small QTimer stand-in for timeout tests."""
        def __init__(self) -> None:
            self.stopped = False

        def isActive(self):
            return True

        def stop(self):
            self.stopped = True

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._status_refresh_token = 7
    dialog._status_refresh_running = True
    dialog._pending_status_attrs = {"_chatgpt_status_lbl", "_github_status_lbl"}
    dialog._pending_status_results = [
        (7, "_chatgpt_status_lbl", True, "Logged in"),
        (99, "_github_status_lbl", True, "stale"),
    ]
    dialog._pending_status_results_lock = threading.Lock()
    dialog._status_result_timer = FakeTimer()
    dialog._chatgpt_status_lbl = QLabel("Checking status...")
    dialog._github_status_lbl = QLabel("Checking status...")
    try:
        SettingsDialog._expire_status_refresh(dialog, 7)

        assert dialog._chatgpt_status_lbl.text() == "Logged in"
        assert "timed out" in dialog._github_status_lbl.text()
        assert dialog._pending_status_attrs == set()
        assert dialog._status_refresh_running is False
        assert dialog._pending_status_results == [(99, "_github_status_lbl", True, "stale")]
        assert dialog._status_result_timer.stopped is True
    finally:
        dialog._chatgpt_status_lbl.deleteLater()
        dialog._github_status_lbl.deleteLater()
        app.processEvents()


def test_ui_host_skips_direct_stt_reset_thread(monkeypatch):
    from ui.settings_panel.dialog import SettingsDialog

    def fail_thread_start(*_args, **_kwargs):
        raise AssertionError("UI host must not start an STT reset thread")

    monkeypatch.setenv("OPENWAND_MACOS_PY_UI_HOST", "1")
    monkeypatch.setattr(threading, "Thread", fail_thread_start)

    SettingsDialog._reset_stt_model_in_background()


def test_reset_page_key_mapping_is_scoped():
    from ui.settings_panel.dialog import SettingsDialog

    env = {
        "LLM_PROVIDER": "anthropic",
        "GROQ_API_KEY": "secret",
        "CALLER_COUNT": "3",
        "CALLER_1_HOTKEY": "ctrl+q",
        "CALLER_2_CONTEXT_MEMORY_MODE": "model",
        "BUBBLE_WIDTH": "420",
        "BUBBLE_FONT_SIZE": "14",
        "BUBBLE_SCROLL_ENABLED": "False",
        "BUBBLE_SCROLL_SNAP_DELAY_MS": "2200",
        "MEMORY_TOP_K": "7",
        "STT_MODEL": "small",
        "APP_LANGUAGE": "zh",
        "ASSISTANT_LANGUAGE": "Chinese",
        "CHAT_TOOL_TRACE_UI": "True",
        "OPENWAND_PLANNED_CHUNKING": "True",
        "OPENWAND_PLANNED_CHUNKING_CHUNKS": "3",
        "OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS": "80",
        "CHAT_REASONING_EFFORT": "high",
        "CHAT_AUTO_ELABORATE": "True",
        "CHAT_ELABORATE_PROMPT": "Please elaborate.",
        "CUSTOM_BASE_URL": "http://localhost:1234/v1",
        "OPENWAND_CONNECTION_ALIAS_OPENAI": "Work",
    }

    assert SettingsDialog._reset_env_keys_for_page("LLM", env) >= {
        "LLM_PROVIDER",
        "CHAT_TOOL_TRACE_UI",
        "OPENWAND_PLANNED_CHUNKING",
        "OPENWAND_PLANNED_CHUNKING_CHUNKS",
        "OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS",
        "CHAT_REASONING_EFFORT",
        "CHAT_AUTO_ELABORATE",
        "CHAT_ELABORATE_PROMPT",
    }
    assert "GROQ_API_KEY" not in SettingsDialog._reset_env_keys_for_page("LLM", env)
    assert SettingsDialog._reset_env_keys_for_page("Connections", env) >= {
        "CUSTOM_BASE_URL",
        "OPENWAND_CONNECTION_ALIAS_OPENAI",
    }
    assert SettingsDialog._reset_env_keys_for_page("Keybinds", env) >= {
        "CALLER_COUNT",
        "CALLER_1_HOTKEY",
        "CALLER_2_CONTEXT_MEMORY_MODE",
        "HOTKEY_SNIP",
    }
    assert "BUBBLE_WIDTH" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "BUBBLE_FONT_SIZE" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "BUBBLE_SCROLL_ENABLED" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "BUBBLE_SCROLL_SNAP_DELAY_MS" in SettingsDialog._reset_env_keys_for_page("Advanced", env)
    assert "START_ON_LOGIN" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "APP_LANGUAGE" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "ASSISTANT_LANGUAGE" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "CHAT_AUTO_ELABORATE" not in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "MEMORY_TOP_K" in SettingsDialog._reset_env_keys_for_page("Advanced", env)
    assert "STT_MODEL" in SettingsDialog._reset_env_keys_for_page("TTS / Voice", env)
    assert SettingsDialog._reset_env_keys_for_page("Tools", env) == set()


def test_settings_tab_strip_uses_theme_background():
    from ui.settings_panel.dialog import SettingsDialog
    from ui.shared.theme import theme_colors

    colors = theme_colors(True)
    style = SettingsDialog._dialog_style(True)

    assert "QTabWidget#settingsTabs" in style
    assert "QTabWidget#settingsTabs::tab-bar" in style
    assert "QTabBar#settingsTabBar" in style
    assert "QWidget#openwandWindowContent" in style
    assert f"background: {colors['bg']};" in style
    assert "QTabBar { background: transparent" not in style
    assert "QWidget { background-color: transparent" not in style


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_tab_bar_has_explicit_painted_backing():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        bar = dialog._tabs.tabBar()
        assert dialog._tabs.objectName() == "settingsTabs"
        assert dialog._tabs.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        assert bar.objectName() == "settingsTabBar"
        assert bar.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        assert bar.drawBase() is False
        assert bar.expanding() is True
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_sidebar_uses_task_based_page_order_without_memory_page():
    """Verify the sidebar separates connections/routes and folds Memory into Advanced."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        assert dialog._tab_base_names == [
            "App",
            "Connections",
            "LLM",
            "TTS / Voice",
            "Keybinds",
            "Prompts",
            "Advanced",
            "About",
        ]
        assert [
            dialog._settings_nav.item(i).text().rstrip(" *")
            for i in range(dialog._settings_nav.count())
        ] == [
            "General",
            "Connections",
            "Model routing",
            "Voice & audio",
            "Shortcuts",
            "Prompts & context",
            "Advanced",
            "About",
        ]
        assert dialog._tabs.tabBar().isHidden()
        advanced_tab = dialog._tabs.widget(dialog._tab_base_names.index("Advanced"))
        assert advanced_tab.isAncestorOf(dialog._fields["MEMORY_TOP_K"])
        assert advanced_tab.isAncestorOf(dialog._fields["MEMORY_AUTO_CONSOLIDATE"])
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_system_prompt_page_has_separate_provider_tabs_and_real_placeholders():
    """ChatGPT and Claude prompts default to empty editable values, not fake text."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        tabs = dialog._system_prompt_tabs
        assert [tabs.tabText(index) for index in range(tabs.count())] == [
            "OpenWand",
            "ChatGPT",
            "Claude",
        ]
        chatgpt = dialog._fields["OPENWAND_CODEX_SYSTEM_PROMPT"]
        claude = dialog._fields["OPENWAND_CLAUDE_SYSTEM_PROMPT"]
        chatgpt.clear()
        claude.clear()
        assert chatgpt.toPlainText() == ""
        assert claude.toPlainText() == ""
        assert "native instructions" in chatgpt.placeholderText()
        assert "native instructions" in claude.placeholderText()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_theme_is_reapplied_after_sidebar_items_exist(monkeypatch):
    """The initial theme pass must polish navigation items, not just the empty dialog."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QListWidget

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    original = SettingsDialog._apply_dialog_theme
    item_counts = []

    def tracked_apply(dialog):
        nav = getattr(dialog, "_settings_nav", None)
        item_counts.append(nav.count() if isinstance(nav, QListWidget) else -1)
        original(dialog)

    monkeypatch.setattr(SettingsDialog, "_apply_dialog_theme", tracked_apply)
    dialog = SettingsDialog()
    try:
        assert item_counts[0] == -1
        assert item_counts[-1] == len(dialog._tab_base_names) == 8
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_sidebar_uses_uniform_explicit_row_spacing():
    """Navigation spacing must not depend on a later stylesheet repolish."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import _SETTINGS_NAV_ITEM_HEIGHT, SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    dialog.show()
    app.processEvents()
    try:
        nav = dialog._settings_nav
        rects = [nav.visualItemRect(nav.item(index)) for index in range(nav.count())]

        assert {rect.height() for rect in rects} == {_SETTINGS_NAV_ITEM_HEIGHT}
        gaps = [
            following.top() - current.bottom() - 1
            for current, following in zip(rects, rects[1:], strict=False)
        ]
        assert len(set(gaps)) == 1
        assert gaps[0] >= nav.spacing()

        dialog._apply_dialog_theme()
        app.processEvents()
        assert [nav.visualItemRect(nav.item(i)).height() for i in range(nav.count())] == [
            _SETTINGS_NAV_ITEM_HEIGHT
        ] * nav.count()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_connections_page_filters_and_paginates_large_provider_lists():
    """Large connection lists stay searchable and show six rows until expanded."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()
    try:
        for row in list(dialog._api_key_rows):
            dialog._remove_api_key_row(row)
        providers = ["openai", "anthropic", "google", "groq", "mistral", "xai", "ollama", "custom"]
        rows = [
            dialog._add_api_key_row(provider, alias=("needle" if index == 7 else f"account-{index}"))
            for index, provider in enumerate(providers)
        ]
        dialog._connections_expanded = False
        dialog._refresh_connection_rows_filter()

        assert sum(not row["widget"].isHidden() for row in rows) == 6
        assert not dialog._connections_show_more_btn.isHidden()
        assert "2" in dialog._connections_show_more_btn.text()

        dialog._connections_search.setText("needle")
        app.processEvents()
        assert [row["alias"].text() for row in rows if not row["widget"].isHidden()] == ["needle"]

        dialog._connections_search.clear()
        dialog._connections_filter.setCurrentIndex(dialog._connections_filter.findData("local"))
        app.processEvents()
        assert {
            row["provider"].currentData()
            for row in rows
            if not row["widget"].isHidden()
        } == {"custom"}
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_connection_filter_and_expand_are_provider_failure_independent(monkeypatch):
    """Local list filtering never depends on credentials or provider I/O."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core import secret_store
    from core.llm_clients import client as llm
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()
    try:
        for row in list(dialog._api_key_rows):
            dialog._remove_api_key_row(row)
        rows = [
            dialog._add_api_key_row(provider, alias=f"connection-{index}")
            for index, provider in enumerate(
                ("openai", "anthropic", "google", "groq", "mistral", "xai", "custom")
            )
        ]

        def forbidden(*_args, **_kwargs):
            raise AssertionError("provider or credential I/O reached by local filtering")

        monkeypatch.setattr(secret_store, "get_secret", forbidden)
        monkeypatch.setattr(llm, "list_models", forbidden)
        monkeypatch.setattr(llm, "test_route_connection", forbidden)

        for _fault in (
            "credentials unavailable",
            "network unavailable",
            "provider unavailable",
            "account permission missing",
            "provider API incompatible",
        ):
            dialog._connections_expanded = False
            dialog._connections_filter.setCurrentIndex(
                dialog._connections_filter.findData("cloud")
            )
            dialog._refresh_connection_rows_filter()
            assert sum(not row["widget"].isHidden() for row in rows) == 6
            dialog._toggle_connections_expanded()
            assert sum(not row["widget"].isHidden() for row in rows) == 6

        dialog._connections_filter.setCurrentIndex(
            dialog._connections_filter.findData("local")
        )
        dialog._refresh_connection_rows_filter()
        assert {
            row["provider"].currentData()
            for row in rows
            if not row["widget"].isHidden()
        } == {"custom"}
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_connection_configuration_failure_matrix_is_controlled(monkeypatch):
    """Connection add/alias/key/custom-preset/remove stays usable across boundary faults."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from core import secret_store
    from core.llm_clients import client as llm
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, message: warnings.append(message))
    try:
        for row in list(dialog._api_key_rows):
            dialog._remove_api_key_row(row)

        external_faults = (
            ValueError("value required by this function is invalid"),
            ValueError("endpoint URL is malformed"),
            ConnectionError("endpoint is offline"),
            PermissionError("account lacks permission"),
        )
        for fault in external_faults:
            def forbidden(*_args, error=fault, **_kwargs):
                raise error

            with monkeypatch.context() as scoped:
                scoped.setattr(secret_store, "get_secret", forbidden)
                scoped.setattr(llm, "list_models", forbidden)
                scoped.setattr(llm, "test_route_connection", forbidden)
                row = dialog._add_api_key_row("openai", alias="Primary")
                row["alias"].setText("Renamed connection")
                assert row["alias"].text() == "Renamed connection"
                dialog._apply_custom_preset("http://localhost:1234/v1", "local-model")
                assert dialog._fields["CUSTOM_BASE_URL"].text() == "http://localhost:1234/v1"
                dialog._remove_api_key_row(row)
                assert row not in dialog._api_key_rows

        # The credential-storage action itself contains keychain unavailability
        # and leaves the typed secret present so the user can retry.
        row = dialog._add_api_key_row("openai", alias="Keychain")
        row["key"].setText("typed-but-not-stored")
        monkeypatch.setattr(secret_store, "migrate_env_secrets", lambda _env: None)
        monkeypatch.setattr(
            secret_store,
            "set_secret",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("OS keychain is unavailable")),
        )
        assert dialog._save_api_keys_to_keychain() is False
        assert row["key"].text() == "typed-but-not-stored"
        assert "OS keychain is unavailable" in warnings[-1]

        # Masking stored secrets is a local UI operation and remains available
        # even while reads from the credential backend fail.
        dialog._set_secret_placeholder(row["key"], "stored in keychain", stored=True)
        assert row["key"].echoMode().name == "Password"
        assert "stored" in row["key"].placeholderText()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_unconfigured_model_provider_has_no_obsolete_api_key_below_hint():
    """Model provider options no longer point at the separate Connections page as 'below'."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    combo = QComboBox()

    try:
        dialog._get_api_key_display_options = lambda: [("Google AI Studio", "google")]
        dialog._credential_availability = lambda: {}
        dialog._fill_credential_combo(combo, "ollama")

        assert combo.currentData() == "ollama"
        assert combo.currentText() == t("Ollama (local)")
        assert "add an API key below" not in combo.currentText()
    finally:
        combo.deleteLater()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_ollama_is_direct_model_route_and_fetches_installed_models_automatically():
    """Ollama needs neither a fake credential row nor a manually typed model id."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import (
        _CONNECTION_PROVIDER_IDS,
        _CUSTOM_MODEL_SENTINEL,
        SettingsDialog,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    try:
        for connection in list(dialog._api_key_rows):
            dialog._remove_api_key_row(connection)
        dialog._refresh_model_api_key_combos()

        row = dialog._model_section_rows["LLM"][0]
        provider_combo = row["api_key_combo"]
        assert provider_combo.findData("ollama") >= 0
        assert "ollama" not in _CONNECTION_PROVIDER_IDS
        assert all(
            name != "Ollama (local)"
            for name, _url, _model, _key in dialog._CUSTOM_ENDPOINTS
        )

        fetches = []
        dialog._refresh_models_for_row = (
            lambda fetched_row, *, automatic=False: fetches.append((fetched_row, automatic))
        )
        non_ollama_index = next(
            index
            for index in range(provider_combo.count())
            if provider_combo.itemData(index) not in {"", "ollama"}
        )
        provider_combo.setCurrentIndex(non_ollama_index)
        provider_combo.setCurrentIndex(provider_combo.findData("ollama"))

        assert fetches[-1] == (row, True)
        assert row["model_combo"].count() == 0
        assert row["model_combo"].placeholderText() == "Finding installed Ollama models…"
        assert row["model_combo"].findData(_CUSTOM_MODEL_SENTINEL) == -1

        row["_fetch_token"] = 17
        dialog._on_models_fetched(
            row,
            ["gemma3:latest", "qwen3:8b"],
            "",
            provider="ollama",
            fetch_token=17,
        )
        model_ids = {
            row["model_combo"].itemData(index)
            for index in range(row["model_combo"].count())
        }
        assert model_ids == {"gemma3:latest", "qwen3:8b"}
        assert row["refresh_btn"].toolTip() == "Live: 2 models"

        saved_row = dialog._add_model_section_row("LLM", "ollama", "my-local-model:latest")
        assert saved_row["model_combo"].count() == 1
        assert saved_row["model_combo"].currentData() == "my-local-model:latest"
        assert saved_row["model_combo"].findData(_CUSTOM_MODEL_SENTINEL) == -1
        app.processEvents()
        assert (saved_row, True) in fetches
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_model_and_voice_workspaces_show_one_scalable_section_at_a_time():
    """Purpose and speech selectors keep large option groups mutually exclusive."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    try:
        dialog._show_model_route("VISION_LLM")
        assert not dialog._model_route_cards["VISION_LLM"].isHidden()
        assert dialog._model_route_cards["LLM"].isHidden()
        assert dialog._model_route_cards["MEMORY_LLM"].isHidden()
        assert dialog._model_route_buttons["VISION_LLM"].isChecked()

        dialog._show_voice_feature("live")
        assert not dialog._voice_feature_cards["live"].isHidden()
        assert dialog._voice_feature_cards["tts"].isHidden()
        assert dialog._voice_feature_cards["stt"].isHidden()
        assert not dialog._voice_playback_card.isHidden()
        assert dialog._voice_feature_buttons["live"].isChecked()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_model_routes_add_inline_and_drag_to_reorder():
    """Fallback rows are created inline and their drag order becomes priority order."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()
    try:
        rows = dialog._model_section_rows["LLM"]
        original_count = len(rows)

        dialog._model_route_add_buttons["LLM"].click()
        assert len(rows) == original_count + 1
        assert rows[-1]["api_key_combo"].currentData() == ""
        assert dialog._model_value(rows[-1]) == ""

        dragged_row = dialog._add_model_section_row("LLM", "ollama", "dragged-model")
        dialog._model_route_rows_containers["LLM"].rowDropped.emit(
            dragged_row["widget"], 0
        )

        assert rows[0] is dragged_row
        assert dialog._model_section_layouts["LLM"].itemAt(0).widget() is dragged_row["widget"]
        assert rows[0]["priority_lbl"].text() == "<b>1</b>"
        assert dialog._snapshot_settings()["LLM_1_MODEL"] == "dragged-model"
        if len(rows) > 1:
            assert rows[1]["priority_lbl"].text() == "2"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_provider_is_model_route_and_api_key_table_option():
    """Verify custom provider is available for both model routes and API key rows."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        api_key_row = dialog._add_api_key_row("custom")
        route_id = f"custom@{api_key_row['connection_id']}"
        options = dialog._get_api_key_display_options()
        assert route_id in {provider for _label, provider in options}

        for rows in dialog._model_section_rows.values():
            for row in rows:
                assert row["api_key_combo"].findData(route_id) >= 0

        api_key_row["key"].setText("not-a-real-key")
        assert dialog._effective_secret_value_from_provider(route_id) == "not-a-real-key"

        assert api_key_row["provider"].findData("custom") >= 0
        assert api_key_row["provider"].currentData() == "custom"
        assert "custom endpoint" in api_key_row["key"].placeholderText()
        assert not api_key_row["custom_details"].isHidden()
        assert "Test custom" not in {button.text() for button in dialog.findChildren(QPushButton)}
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_copilot_is_api_key_provider_option():
    """Verify Copilot can be entered through the API key provider list."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        api_key_row = dialog._add_api_key_row("copilot")

        assert api_key_row["provider"].findData("copilot") >= 0
        assert api_key_row["provider"].currentData() == "copilot"
        assert "github_pat_" in api_key_row["key"].placeholderText()
        button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
        assert "Connect token" not in button_texts
        assert "Test connection" not in button_texts
        assert "Clear token" not in button_texts
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_zai_is_api_key_and_model_route_option():
    """Verify native OpenAI-compatible providers are available in Settings provider lists."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    provider_ids = {
        "zai",
        "nvidia",
        "sambanova",
        "github_models",
        "huggingface",
        "chutes",
        "vercel",
        "fireworks",
        "cohere",
        "ai21",
        "nebius",
    }

    try:
        for provider in provider_ids:
            api_key_row = dialog._add_api_key_row(provider)
            assert api_key_row["provider"].findData(provider) >= 0
            assert api_key_row["provider"].currentData() == provider

        options = dialog._get_api_key_display_options()
        assert provider_ids <= {provider for _label, provider in options}

        model_values = []
        for rows in dialog._model_section_rows.values():
            for row in rows:
                for provider in provider_ids:
                    idx = row["api_key_combo"].findData(provider)
                    if idx >= 0:
                        model_values.append(row["api_key_combo"].itemData(idx))
        assert provider_ids <= set(model_values)
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_copilot_api_key_row_saves_through_copilot_auth(monkeypatch):
    """Verify Copilot API key rows use the Copilot keychain helper."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core import secret_store
    from core.auth import copilot_auth
    from ui.settings_panel.dialog import SettingsDialog

    saved_tokens = []
    monkeypatch.setattr(copilot_auth, "token_status", lambda: (False, "Not configured"))
    monkeypatch.setattr(copilot_auth, "save_token", lambda token: saved_tokens.append(token))
    monkeypatch.setattr(secret_store, "migrate_env_secrets", lambda _env: None)

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        api_key_row = dialog._add_api_key_row("copilot")
        api_key_row["key"].setText("github_pat_test")

        assert dialog._save_api_keys_to_keychain() is True

        assert saved_tokens == ["github_pat_test"]
        assert api_key_row["key"].text() == ""
        assert api_key_row["key"].placeholderText() == "stored in keychain"
        assert api_key_row["key"].property("storedSecret") is True
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_ollama_custom_preset_fills_dummy_api_key():
    """Verify Ollama custom preset fills the local dummy API key."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        dialog._fields["CUSTOM_API_KEY"].clear()
        dialog._apply_custom_preset("http://localhost:11434/v1", "llama3", "ollama")

        assert dialog._fields["CUSTOM_BASE_URL"].text() == "http://localhost:11434/v1"
        assert dialog._fields["CUSTOM_API_KEY"].text() == "ollama"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tool_warning_marks_associated_settings_headlines():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        if not dialog._caller_blocks:
            dialog._add_caller_block()
        caller = dialog._caller_blocks[0]
        browser_mode = caller["context_browser_mode"]
        browser_mode.setCurrentIndex(browser_mode.findData("model"))

        warnings, targets = dialog._capability_warnings_for_values(
            {
                "LLM_PROVIDER": "chatgpt",
                "LLM_MODEL": "gpt-5.5",
                "VISION_LLM_PROVIDER": "anthropic",
                "VISION_LLM_MODEL": "claude-sonnet-4-5",
            }
        )
        assert warnings
        assert "LLM" in targets
        assert "Global shortcuts" in targets

        dialog._set_warning_markers(targets)

        assert dialog._warning_headers["LLM"].text().startswith("\u26a0 ")
        assert dialog._warning_headers["Global shortcuts"].text().startswith("\u26a0 ")
        assert "ChatGPT" in dialog._warning_headers["LLM"].toolTip()

        dialog._set_warning_markers({})
        assert not dialog._warning_headers["LLM"].text().startswith("\u26a0 ")
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_llm_tab_groups_credentials_and_models_under_full_height_rails():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFrame, QWidget

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()

    try:
        groups = [
            widget
            for widget in dialog.findChildren(QWidget)
            if widget.objectName() == "settingsAreaGroup"
        ]
        assert len(groups) >= 2
        assert "Provider credentials" in dialog._warning_headers
        assert "Model routing" in dialog._warning_headers

        first_group_headers = {
            dialog._warning_header_base_texts.get(key)
            for key in ("Provider credentials", "OAuth logins")
        }
        assert first_group_headers == {"Provider credentials", "OAuth logins"}
        assert groups[0].findChildren(QFrame, "areaAccentLine")
        assert groups[1].findChildren(QFrame, "areaAccentLine")
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_subscription_warning_marks_provider_credentials_headline():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        warnings, targets = dialog._capability_warnings_for_values(
            {
                "LLM_PROVIDER": "chatgpt",
                "LLM_MODEL": "gpt-5.5",
                "VISION_LLM_PROVIDER": "anthropic",
                "VISION_LLM_MODEL": "claude-sonnet-4-5",
            }
        )
        assert warnings
        assert any("sign in again" in warning for warning in warnings)
        assert "Provider credentials" in targets

        dialog._set_warning_markers(targets)

        assert dialog._warning_headers["Provider credentials"].text().startswith("\u26a0 ")
        assert dialog._warning_headers["Provider credentials"].toolTip()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_each_model_tab_only_shows_its_own_subscription_warning():
    """Chat and Image tabs must not aggregate each other's route warning."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        if not dialog._caller_blocks:
            dialog._add_caller_block()
        caller_screenshot = dialog._caller_blocks[0]["context_screenshot"]
        caller_screenshot.setCurrentIndex(caller_screenshot.findData("auto"))
        voice_screenshot = dialog._voice_block["context_screenshot"]
        voice_screenshot.setCurrentIndex(voice_screenshot.findData("model"))
        warnings, targets = dialog._capability_warnings_for_values(
            {
                "LLM_PROVIDER": "chatgpt",
                "LLM_MODEL": "gpt-5.5",
                "VISION_LLM_PROVIDER": "copilot",
                "VISION_LLM_MODEL": "gpt-4.1",
            }
        )
        assert len([warning for warning in warnings if "subscription/OAuth" in warning]) == 2
        assert any("Chat model uses" in warning for warning in targets["LLM"])
        assert not any("Image model uses" in warning for warning in targets["LLM"])
        assert any("Image model uses" in warning for warning in targets["VISION_LLM"])
        assert not any("Chat model uses" in warning for warning in targets["VISION_LLM"])
        assert any("Chat model uses" in warning for warning in targets["Provider credentials"])
        assert any("Image model uses" in warning for warning in targets["Provider credentials"])
        assert any("Copilot cannot read screenshots" in warning for warning in targets["VISION_LLM"])
        assert not any("Copilot cannot read screenshots" in warning for warning in targets["LLM"])
        assert any("'Let model decide'" in warning for warning in targets["LLM"])
        assert not any("'Let model decide'" in warning for warning in targets["VISION_LLM"])

        dialog._set_warning_markers(targets)
        assert "Chat model uses" in dialog._warning_headers["LLM"].toolTip()
        assert "Image model uses" not in dialog._warning_headers["LLM"].toolTip()
        assert "Image model uses" in dialog._warning_headers["VISION_LLM"].toolTip()
        assert "Chat model uses" not in dialog._warning_headers["VISION_LLM"].toolTip()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tts_provider_timing_notice_only_for_providers_without_word_timestamps():
    """Verify TTS timing notice appears only when word sync is approximate."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from ui.i18n import t
    from ui.settings_panel.dialog import _TTS_TIMING_NOTICE, SettingsDialog, _get, _set

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()

    try:
        notice = dialog.findChild(QLabel, "ttsTimingNotice")
        assert notice is not None

        for provider in ("elevenlabs", "openai", "openai_compatible", "gpt_sovits", "kokoro"):
            _set(dialog._fields["TTS_PROVIDER"], provider)
            dialog._update_tts_provider_fields()
            assert _get(dialog._fields["TTS_PROVIDER"]) == provider
            assert not notice.isHidden()
            assert notice.text() == t(_TTS_TIMING_NOTICE)

        for provider in ("cartesia", "none"):
            _set(dialog._fields["TTS_PROVIDER"], provider)
            dialog._update_tts_provider_fields()
            assert _get(dialog._fields["TTS_PROVIDER"]) == provider
            assert notice.isHidden()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_warning_markers_refresh_from_loaded_settings(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core import settings_profiles
    from core.system.env_utils import read_env_file, write_env_file
    from ui.settings_panel import dialog as dialog_mod
    from ui.settings_panel.dialog import SettingsDialog

    env_path = tmp_path / ".env"
    profile_values = {
        "LLM_PROVIDER": "chatgpt",
        "LLM_MODEL": "gpt-5.5",
        "VISION_LLM_PROVIDER": "anthropic",
        "VISION_LLM_MODEL": "claude-sonnet-4-5",
        "CALLER_COUNT": "1",
        "CALLER_1_CONTEXT_BROWSER_MODE": "model",
        "CALLER_1_CONTEXT_DOCUMENTS_MODE": "off",
        "CALLER_1_CONTEXT_GITHUB_MODE": "off",
        "CALLER_1_CONTEXT_MEMORY_MODE": "off",
        "CALLER_1_CONTEXT_SCREENSHOT": "off",
        "VOICE_CONTEXT_BROWSER_MODE": "off",
        "VOICE_CONTEXT_DOCUMENTS_MODE": "off",
        "VOICE_CONTEXT_GITHUB_MODE": "off",
        "VOICE_CONTEXT_MEMORY_MODE": "off",
        "VOICE_CONTEXT_SCREENSHOT": "off",
    }
    write_env_file(
        env_path,
        {
            "PROFILE_COUNT": "1",
            "PROFILE_1_ID": "warning-profile",
            "PROFILE_1_LABEL": "Warning profile",
        },
    )
    settings_profiles.save_profile(
        env_path,
        "warning-profile",
        "Warning profile",
        profile_values,
    )
    writes, removals = settings_profiles.activation_write_plan(
        read_env_file(env_path),
        "warning-profile",
        profile_values,
    )
    write_env_file(env_path, writes, remove_keys=removals)
    monkeypatch.setattr(dialog_mod, "ENV_PATH", env_path)

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    dialog._build_deferred_pages()

    try:
        assert dialog._selected_profile_id() == "warning-profile"
        assert dialog._warning_headers["Provider credentials"].text().startswith("\u26a0 ")
        assert dialog._warning_headers["LLM"].text().startswith("\u26a0 ")
        # Legacy CALLER_* profile values are intentionally ignored now that
        # caller context lives in the action-file tree.
        assert not dialog._warning_headers["Global shortcuts"].text().startswith("\u26a0 ")
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_footer_uses_cancel_and_save_changes():
    """Verify the simplified footer exposes Cancel and Save changes."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        from ui.i18n import t

        save_btn = dialog.findChild(QPushButton, "settingsApplyButton")
        cancel_btn = dialog.findChild(QPushButton, "settingsCancelButton")
        assert save_btn is not None
        assert cancel_btn is not None
        assert save_btn.text() == t("Save changes")
        assert cancel_btn.text() == t("Cancel")
        assert dialog.findChild(QPushButton, "settingsConfirmButton") is None

        calls = []
        dialog._apply_settings = lambda: calls.append("saved") or True

        dialog.show()
        app.processEvents()

        # Nothing edited: skip the redundant reload and keep Settings open.
        dialog._confirm()
        assert calls == []
        assert dialog.isVisible()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_confirm_applies_when_there_are_unsaved_changes():
    """Save changes applies edits without dismissing Settings."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QCheckBox

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        dialog.show()
        app.processEvents()
        calls = []
        dialog._apply_settings = lambda: calls.append("saved") or True

        # Make a real edit so the dialog is dirty.
        box = dialog._fields["CHAT_TOOL_TRACE_UI"]
        assert isinstance(box, QCheckBox)
        box.setChecked(not box.isChecked())

        dialog._confirm()
        assert calls == ["saved"]
        assert dialog.isVisible()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_escape_does_not_close_settings_but_cancel_still_does():
    """Escape is harmless at window level; the explicit Cancel button still closes."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        dialog.show()
        app.processEvents()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        app.processEvents()
        assert dialog.isVisible()

        cancel_btn = dialog.findChild(QPushButton, "settingsCancelButton")
        assert cancel_btn is not None
        cancel_btn.click()
        app.processEvents()
        assert not dialog.isVisible()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_has_reset_page_button():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
        assert {
            "Reset Page…",
            "重置本页…",
            "重置本頁…",
            "Restablecer página…",
            "Réinitialiser la page…",
        } & button_texts
        assert {
            "Reset All…",
            "全部重置…",
            "Restablecer todo…",
            "Tout réinitialiser…",
        } & button_texts
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_search_filters_to_matching_page(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QLineEdit, QListWidget, QPushButton

    from ui.settings_panel.dialog import SettingsDialog, _FolderListEditor

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        search = dialog.findChild(QLineEdit, "settingsSearch")
        assert search is not None

        search.setText("tool_file_roots")
        app.processEvents()

        tabs = dialog._tabs
        visible_pages = {
            dialog._tab_base_names[i]
            for i in range(tabs.count())
            if tabs.isTabVisible(i)
        }
        assert visible_pages == {"Advanced"}

        allowed_roots = dialog._fields["TOOL_FILE_ROOTS"]
        assert isinstance(allowed_roots, _FolderListEditor)
        folder_list = allowed_roots.findChild(QListWidget, "settingsModelFileFolderList")
        add_folder = allowed_roots.findChild(QPushButton, "settingsModelFileFolderAdd")
        remove_folder = allowed_roots.findChild(QPushButton, "settingsModelFileFolderRemove")
        assert folder_list is not None
        assert add_folder is not None
        assert remove_folder is not None

        chosen = tmp_path / "chosen-folder"
        chosen.mkdir()
        allowed_roots.setText("")
        empty_list_height = folder_list.height()
        picker_calls = []
        monkeypatch.setattr(
            QFileDialog,
            "getExistingDirectory",
            lambda parent, caption, start, options: picker_calls.append(
                (parent, caption, start, options)
            )
            or str(chosen),
        )
        add_folder.click()
        app.processEvents()
        assert picker_calls
        assert allowed_roots.paths() == [str(chosen)]
        one_row_height = folder_list.height()
        # Both states remain a single compact row. Native styles may give a
        # populated item extra vertical padding that an empty view cannot
        # report until its first item exists.
        assert one_row_height >= empty_list_height
        assert one_row_height <= empty_list_height + folder_list.fontMetrics().height()
        add_folder.click()
        assert allowed_roots.paths() == [str(chosen)]
        folder_list.setCurrentRow(0)
        remove_folder.click()
        assert allowed_roots.paths() == []

        second = tmp_path / "second-folder"
        second.mkdir()
        allowed_roots.setText(f"{chosen}\n{second}\n{chosen}")
        assert allowed_roots.paths() == [str(chosen), str(second)]
        assert allowed_roots.text() == f"{chosen}\n{second}"
        assert folder_list.height() > one_row_height
        more_folders = [tmp_path / f"folder-{index}" for index in range(6)]
        allowed_roots.setText("\n".join(str(path) for path in more_folders))
        capped_height = folder_list.height()
        allowed_roots.setText("\n".join(str(path) for path in more_folders[:5]))
        assert folder_list.height() == capped_height
        allowed_roots.setText(f"{chosen}\n{second}")
        assert dialog._snapshot_settings()["TOOL_FILE_ROOTS"] == f"{chosen}\n{second}"
        blocked_globs = dialog._fields["TOOL_FILE_BLOCKED_GLOBS"]
        allowed_label = allowed_roots.parentWidget().layout().labelForField(allowed_roots)
        blocked_label = blocked_globs.parentWidget().layout().labelForField(blocked_globs)
        assert blocked_label.text() == "Files and folders to block  ⓘ"
        assert blocked_label.objectName() == "settingsInfoLabel"
        assert "Add one rule per line" in blocked_label.toolTip()
        assert ".env*" in blocked_label.toolTip()
        assert "**/secrets/**" in blocked_label.toolTip()
        assert "*.pem" in blocked_label.toolTip()
        info_labels = [
            label
            for label in dialog.findChildren(QLabel)
            if label.objectName() == "settingsInfoLabel"
        ]
        assert len(info_labels) >= 60
        assert all("ⓘ" in label.text() for label in info_labels)
        assert all(label.toolTip().strip() for label in info_labels)
        local_file_card = allowed_roots.parentWidget().parentWidget()
        context_card = dialog._fields["CONTEXT_BROWSER_MAX_CHARS"].parentWidget().parentWidget()
        assert local_file_card.objectName() == "card"
        assert not local_file_card.isHidden()
        assert context_card.isHidden()
        assert not allowed_roots.isHidden()
        assert not allowed_label.isHidden()
        assert blocked_globs.isHidden()
        assert any(
            label.objectName() == "sectionHeader"
            and label.text() == "MODEL FILE ACCESS"
            and not label.isHidden()
            for label in local_file_card.findChildren(QLabel)
        )

        search.setText("Model file access")
        app.processEvents()
        assert not allowed_roots.isHidden()
        assert not blocked_globs.isHidden()

        search.setText("Files and folders to block")
        app.processEvents()
        assert allowed_roots.isHidden()
        assert not blocked_globs.isHidden()

        search.setText("Advanced")
        app.processEvents()
        assert not context_card.isHidden()
        assert not allowed_roots.isHidden()
        assert not blocked_globs.isHidden()

        blocked_globs.hide()
        search.setText("tool_file_roots")
        app.processEvents()
        search.clear()
        app.processEvents()
        assert blocked_globs.isHidden()
        blocked_globs.show()
        assert all(tabs.isTabVisible(i) for i in range(tabs.count()))
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_dirty_marker_enables_apply_after_change():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        apply_btn = dialog.findChild(QPushButton, "settingsApplyButton")
        assert apply_btn is not None
        assert not apply_btn.isEnabled()

        dialog._fields["CHAT_ELABORATE_PROMPT"].setText("Please add more detail.")
        app.processEvents()

        assert apply_btn.isEnabled()
        llm_index = dialog._tab_base_names.index("LLM")
        assert dialog._tabs.tabText(llm_index).endswith("*")
        assert dialog._fields["CHAT_ELABORATE_PROMPT"].property("dirty") is True
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_apply_clears_dirty_before_showing_save_warning(monkeypatch):
    """Successful Apply should clear dirty state before non-fatal warnings appear."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

    import config
    from core import tts
    from core.llm_clients import client as llm_client
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel.dialog import SettingsDialog, _get, _set
    from ui.shared import theme

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        apply_btn = dialog.findChild(QPushButton, "settingsApplyButton")
        assert apply_btn is not None

        combo = dialog._fields["ASSISTANT_LANGUAGE"]
        new_language = "Chinese (Traditional)" if _get(combo) != "Chinese (Traditional)" else "English"
        _set(combo, new_language)
        app.processEvents()
        assert apply_btn.isEnabled()

        def fake_save():
            row = dialog._caller_blocks[0]["intent_rows"][0]
            row["label"].setText("\u9019\u662f\u4ec0\u9ebc\uff1f")
            _set(row["prompt"], "\u9019\u662f\u4ec0\u9ebc\uff1f\u8acb\u7528\u7e41\u9ad4\u4e2d\u6587\u89e3\u91cb\u3002")
            dialog._last_save_warnings = ["Non-fatal model capability warning."]
            return True

        opened = []

        def fake_open(message_box):
            opened.append({
                "dirty_keys": set(dialog._dirty_keys),
                "apply_enabled": apply_btn.isEnabled(),
                "status": dialog._status_lbl.text(),
                "text": message_box.text(),
            })

        dialog._do_save = fake_save
        monkeypatch.setattr(config, "reload", lambda: None)
        monkeypatch.setattr(llm_client, "reset_clients", lambda: None)
        monkeypatch.setattr(tts, "reset_connections", lambda: None)
        monkeypatch.setattr(theme, "apply_app_theme", lambda: None)
        monkeypatch.setattr(settings_dialog, "_read_env", lambda: {"ASSISTANT_LANGUAGE": new_language})
        monkeypatch.setattr(QMessageBox, "open", fake_open)

        assert dialog._apply_settings() is True
        app.processEvents()

        assert opened
        assert opened[0]["dirty_keys"] == set()
        assert opened[0]["apply_enabled"] is False
        assert not opened[0]["status"].startswith("Unsaved changes")
        assert dialog._dirty_keys == set()
        assert apply_btn.isEnabled() is False
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_ordinary_settings_save_skips_expensive_theme_repaint(monkeypatch):
    """A non-visual edit must not rebuild the whole app and dialog theme."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from core import tts
    from core.llm_clients import client as llm_client
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel.dialog import SettingsDialog
    from ui.shared import theme

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    dialog._build_deferred_pages()
    theme_calls: list[str] = []
    new_env = dict(dialog._env)
    new_env["CHAT_TOOL_TRACE_UI"] = (
        "False" if new_env.get("CHAT_TOOL_TRACE_UI", "False").lower() == "true" else "True"
    )

    try:
        monkeypatch.setattr(dialog, "_do_save", lambda: True)
        monkeypatch.setattr(config, "reload", lambda: None)
        monkeypatch.setattr(llm_client, "reset_clients", lambda: None)
        monkeypatch.setattr(tts, "reset_connections", lambda: None)
        monkeypatch.setattr(settings_dialog, "_read_env", lambda: dict(new_env))
        monkeypatch.setattr(theme, "apply_app_theme", lambda: theme_calls.append("app"))
        monkeypatch.setattr(dialog, "_apply_dialog_theme", lambda: theme_calls.append("dialog"))

        assert dialog._apply_settings() is True
        assert theme_calls == []
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_theme_repaint_is_limited_to_settings_consumed_by_shared_theme():
    from ui.settings_panel.dialog import _settings_change_requires_theme_repaint

    for key in (
        "THEME_MODE",
        "THEME_DARK_BG",
        "THEME_DARK_ACCENT",
        "THEME_LIGHT_SURFACE",
        "DARK_MODE",
    ):
        assert _settings_change_requires_theme_repaint({key}) is True

    assert _settings_change_requires_theme_repaint({"TTS_PROVIDER"}) is False
    assert _settings_change_requires_theme_repaint({"BUBBLE_COLOR"}) is False
    assert _settings_change_requires_theme_repaint({"ADDON_ENABLED"}) is False


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_do_save_localizes_qtextedit_prompt_fields(tmp_path, monkeypatch):
    """The real save path should localize WASD prompt QTextEdits without crashing."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QTextEdit

    from core.system import autostart
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel import env as settings_env
    from ui.settings_panel.dialog import SettingsDialog, _get, _set

    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    monkeypatch.setattr(autostart, "sync_start_on_login", lambda _enabled: None)

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        captured = {}
        monkeypatch.setattr(dialog, "_save_api_keys_to_keychain", lambda: True)
        monkeypatch.setattr(dialog, "_capability_warnings_for_values", lambda _vals: ([], {}))
        monkeypatch.setattr(dialog, "_set_warning_markers", lambda _warnings: None)
        monkeypatch.setattr(
            dialog,
            "_kokoro_installed",
            lambda: (_ for _ in ()).throw(AssertionError("saving must not require an installed TTS package")),
        )
        monkeypatch.setattr(
            dialog,
            "_elevenlabs_installed",
            lambda: (_ for _ in ()).throw(AssertionError("saving must not require an installed TTS package")),
        )
        monkeypatch.setattr(settings_dialog, "_write_env", lambda vals, remove_keys=None: captured.update(vals))

        for index, block in enumerate(dialog._caller_blocks, 1):
            block["hotkey"].setText(f"ctrl+alt+{index}")
            block["hotkey_2"].setText(f"ctrl+win+{index}")
        for index, key in enumerate(("HOTKEY_ADD_CONTEXT", "HOTKEY_CLEAR_CONTEXT", "HOTKEY_SNIP", "HOTKEY_VOICE", "HOTKEY_DICTATE"), 1):
            dialog._fields[key].setText(f"ctrl+shift+alt+{index}")
            dialog._fields[f"{key}_2"].setText(f"ctrl+shift+win+{index}")
        dialog._fields["HOTKEY_ADD_CONTEXT_ENABLED"].setChecked(False)

        _set(dialog._fields["APP_LANGUAGE"], "Chinese (Traditional)")
        _set(dialog._fields["ASSISTANT_LANGUAGE"], "Chinese (Traditional)")
        _set(dialog._fields["TTS_PROVIDER"], "kokoro")
        _set(dialog._fields["CHAT_ELABORATE_PROMPT"], "Please elaborate on that.")
        dialog._fields["OPENWAND_CODEX_SYSTEM_PROMPT"].setPlainText("ChatGPT-only rules.")
        dialog._fields["OPENWAND_CLAUDE_SYSTEM_PROMPT"].setPlainText("")
        dialog._fields["OPENWAND_PLANNED_CHUNKING"].setChecked(True)
        dialog._fields["OPENWAND_PLANNED_CHUNKING_CHUNKS"].setText("4")
        dialog._fields["OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS"].setText("120")
        dialog._fields["CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY"].setChecked(True)
        first_root = tmp_path / "model-files-one"
        second_root = tmp_path / "model-files-two"
        first_root.mkdir()
        second_root.mkdir()
        dialog._fields["TOOL_FILE_ROOTS"].setText(f"{first_root}\n{second_root}")
        dialog._add_api_key_row("openai", alias="Work")
        row = dialog._caller_blocks[0]["intent_rows"][0]
        assert isinstance(row["prompt"], QTextEdit)
        assert not row["prompt"].acceptRichText()
        row["key"].setText("w")
        row["label"].setText("What is this?")
        _set(row["prompt"], "What is this? Give me a clear, plain-English explanation in 2-3 sentences.")

        assert dialog._do_save() is True
        assert not any(key == "CALLER_COUNT" or key.startswith("CALLER_") for key in captured)
        assert (tmp_path / "callers" / "general" / "what_is_this.toml").is_file()
        assert captured["CHAT_ELABORATE_PROMPT"] == "\u8acb\u8a73\u7d30\u8aaa\u660e\u4e00\u4e0b\u3002"
        assert _get(dialog._fields["CHAT_ELABORATE_PROMPT"]) == captured["CHAT_ELABORATE_PROMPT"]
        assert captured["OPENWAND_CODEX_SYSTEM_PROMPT"] == "ChatGPT-only rules."
        assert captured["OPENWAND_CLAUDE_SYSTEM_PROMPT"] == ""
        assert captured["OPENWAND_PLANNED_CHUNKING"] == "True"
        assert captured["OPENWAND_PLANNED_CHUNKING_CHUNKS"] == "4"
        assert captured["OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS"] == "120"
        assert captured["CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY"] == "True"
        assert captured["TOOL_FILE_ROOTS"] == f"{first_root}\n{second_root}"
        assert captured["TTS_PROVIDER"] == "kokoro"
        assert captured["OPENWAND_CONNECTION_ALIAS_OPENAI"] == "Work"
        assert 'hotkey_2 = "ctrl+win+1"' in (tmp_path / "callers" / "callers.toml").read_text(encoding="utf-8")
        assert captured["HOTKEY_ADD_CONTEXT_2"] == "ctrl+shift+win+1"
        assert captured["HOTKEY_ADD_CONTEXT_ENABLED"] == "False"
        assert captured["HOTKEY_VOICE_2"] == "ctrl+shift+win+4"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_reports_secondary_shortcut_conflicts_only_when_saving(monkeypatch):
    """Editing stays quiet; Save changes reports duplicate primary/secondary keys."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        warnings: list[tuple[str, str]] = []

        def capture_warning(_parent, title, message):
            warnings.append((title, message))
            return QMessageBox.StandardButton.Ok

        monkeypatch.setattr(dialog, "_save_api_keys_to_keychain", lambda: True)
        monkeypatch.setattr(dialog, "_kokoro_installed", lambda: True)
        monkeypatch.setattr(dialog, "_elevenlabs_installed", lambda: True)
        monkeypatch.setattr(QMessageBox, "warning", capture_warning)

        duplicate = dialog._fields["HOTKEY_ADD_CONTEXT"].text()
        dialog._fields["HOTKEY_CLEAR_CONTEXT_2"].setText(duplicate)
        app.processEvents()
        assert warnings == []

        assert dialog._do_save() is False
        assert len(warnings) == 1
        assert warnings[0][0] == t("Duplicate keys")
        assert warnings[0][1] == t(
            "Two or more bindings share the same key.\nPlease resolve conflicts before saving."
        )
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_shortcut_mutation_failure_matrix_is_controlled(monkeypatch):
    """Rename/add/edit validation rejects empty, invalid, duplicate, and stale rows."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(str(message)) or QMessageBox.StandardButton.Ok,
    )

    try:
        first = dialog._caller_blocks[0]
        original_label = first["label"].text()

        first["label"].setText("")
        assert dialog._validate_caller_mutations() is False
        first["label"].setText("bad\x00name")
        assert dialog._validate_caller_mutations() is False
        first["label"].setText(original_label)

        if len(dialog._caller_blocks) < 2:
            dialog._add_caller_block(label="Second shortcut", intents=[])
        second = dialog._caller_blocks[1]
        second["label"].setText(first["label"].text())
        assert dialog._validate_caller_mutations() is False
        second["label"].setText("Second shortcut")

        dialog._add_caller_intent_row(first)
        row = first["intent_rows"][-1]
        assert dialog._validate_caller_mutations() is False
        row["key"].setText("too-long")
        row["label"].setText("New choice")
        row["prompt"].setPlainText("Do the new thing")
        assert dialog._validate_caller_mutations() is False
        row["key"].setText(first["intent_rows"][0]["key"].text())
        assert dialog._validate_caller_mutations() is False

        row["key"].setText("z")
        row["prompt"].setPlainText("bad\x00prompt")
        assert dialog._validate_caller_mutations() is False
        row["prompt"].setPlainText("Do the new thing")

        first["intent_rows"].append({"widget": object()})
        assert dialog._validate_caller_mutations() is False
        first["intent_rows"].pop()

        assert dialog._validate_caller_mutations() is True
        assert warnings
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_apply_real_save_clears_dirty_after_language_change(tmp_path, monkeypatch):
    """Changing Assistant language should save, localize prompts, and clear dirty state."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    import config
    from core import tts
    from core.llm_clients import client as llm_client
    from core.system import autostart
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel import env as settings_env
    from ui.settings_panel.dialog import SettingsDialog, _set
    from ui.shared import theme

    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    monkeypatch.setattr(autostart, "sync_start_on_login", lambda _enabled: None)

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        apply_btn = dialog.findChild(QPushButton, "settingsApplyButton")
        assert apply_btn is not None

        captured = {}
        monkeypatch.setattr(dialog, "_save_api_keys_to_keychain", lambda: True)
        monkeypatch.setattr(dialog, "_capability_warnings_for_values", lambda _vals: ([], {}))
        monkeypatch.setattr(dialog, "_set_warning_markers", lambda _warnings: None)
        monkeypatch.setattr(dialog, "_kokoro_installed", lambda: True)
        monkeypatch.setattr(dialog, "_elevenlabs_installed", lambda: True)
        monkeypatch.setattr(settings_dialog, "_write_env", lambda vals, remove_keys=None: captured.update(vals))
        monkeypatch.setattr(settings_dialog, "_read_env", lambda: dict(captured))
        monkeypatch.setattr(config, "reload", lambda: None)
        monkeypatch.setattr(llm_client, "reset_clients", lambda: None)
        monkeypatch.setattr(tts, "reset_connections", lambda: None)
        monkeypatch.setattr(theme, "apply_app_theme", lambda: None)

        for index, block in enumerate(dialog._caller_blocks, 1):
            block["hotkey"].setText(f"ctrl+alt+{index}")
        for index, key in enumerate(("HOTKEY_ADD_CONTEXT", "HOTKEY_CLEAR_CONTEXT", "HOTKEY_SNIP", "HOTKEY_VOICE", "HOTKEY_DICTATE"), 1):
            dialog._fields[key].setText(f"ctrl+shift+alt+{index}")

        _set(dialog._fields["ASSISTANT_LANGUAGE"], "Chinese (Traditional)")
        row = dialog._caller_blocks[0]["intent_rows"][0]
        row["key"].setText("w")
        row["label"].setText("What is this?")
        _set(row["prompt"], "What is this? Give me a clear, plain-English explanation in 2-3 sentences.")
        app.processEvents()

        assert apply_btn.isEnabled()
        assert dialog._apply_settings() is True
        app.processEvents()

        assert not any(key == "CALLER_COUNT" or key.startswith("CALLER_") for key in captured)
        assert (tmp_path / "callers" / "general" / "what_is_this.toml").is_file()
        assert dialog._dirty_keys == set()
        assert apply_btn.isEnabled() is False
        assert not dialog._status_lbl.text().startswith("Unsaved changes")
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_apply_reports_unexpected_save_exception(monkeypatch):
    """Confirm/Apply failures should show a visible reason instead of doing nothing."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        warnings = []

        def fail_save():
            raise RuntimeError("prompt field update failed")

        def capture_warning(_parent, title, message):
            warnings.append((title, message))
            return QMessageBox.StandardButton.Ok

        dialog._do_save = fail_save
        monkeypatch.setattr(QMessageBox, "warning", capture_warning)

        assert dialog._apply_settings() is False
        assert warnings
        assert warnings[0][0]
        assert "prompt field update failed" in warnings[0][1]
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_preset_marks_reviewable_changes_without_saving(isolated_default_profile):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog, _get, _set

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()

    try:
        apply_btn = dialog.findChild(QPushButton, "settingsApplyButton")
        assert apply_btn is not None

        llm_row = dialog._model_section_rows["LLM"][0]
        if llm_row["api_key_combo"].findData("openai") < 0:
            llm_row["api_key_combo"].addItem("OpenAI", "openai")
        _set(llm_row["api_key_combo"], "openai")
        dialog._reset_dirty_baseline()

        dialog._apply_preset("Low setup")
        app.processEvents()

        assert apply_btn.isEnabled()
        assert _get(dialog._fields["STT_MODEL"]) == "base"
        assert _get(dialog._fields["CONTEXT_BROWSER_MAX_CHARS"]) == "3000"
        assert dialog._active_preset_slug == ""
        assert dialog._pending_active_profile == "low_setup"
        assert "LLM" in dialog._tab_dirty_names
        assert dialog._tab_dirty_names <= {
            "App",
            "LLM",
            "TTS / Voice",
            "Advanced",
            "Keybinds",
        }
        assert {"Connections", "Prompts", "About"}.isdisjoint(
            dialog._tab_dirty_names
        )
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_low_setup_preset_uses_chatgpt_oauth_routes(tmp_path, monkeypatch):
    """Verify low setup preset only needs ChatGPT OAuth for model routes."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel import dialog as settings_dialog

    monkeypatch.setattr(settings_dialog, "ENV_PATH", tmp_path / ".env")
    SettingsDialog = settings_dialog.SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        dialog._apply_preset("Low setup")
        app.processEvents()

        assert dialog._active_preset_slug == ""
        assert dialog._pending_active_profile == "low_setup"
        for section in ("LLM", "VISION_LLM", "MEMORY_LLM"):
            rows = dialog._model_section_rows[section]
            assert len(rows) == 1
            assert rows[0]["api_key_combo"].currentData() == "chatgpt"
            assert dialog._model_value(rows[0]) == "gpt-5.5"

        assert dialog._voice_block["context_browser_mode"].currentData() == "off"
        assert dialog._voice_block["context_github_mode"].currentData() == "off"
        assert dialog._voice_block["context_screenshot"].currentData() == "off"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_profiles_menu_only_exposes_low_setup_builtin_preset():
    """Verify old behavior presets are not shown as built-in Settings presets."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    menu = None

    try:
        menu = dialog._build_profiles_menu(dialog)
        action_texts = [action.text() for action in menu.actions()]

        assert "Low setup" in action_texts
        for old_preset in ("Fast", "Best quality", "Private/local", "Coding assistant", "Low cost"):
            assert old_preset not in action_texts
    finally:
        if menu is not None:
            menu.deleteLater()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_builtin_profile_selection_updates_label_and_replaces_custom_runtime_profile(tmp_path, monkeypatch):
    """A built-in selection must survive the live config reload after Save changes."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    import ui.settings_panel.dialog as settings_dialog
    from core.system import autostart
    from ui.settings_panel import env as settings_env

    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join([
            "PROFILE_COUNT=1",
            "PROFILE_1_ID=a",
            "PROFILE_1_LABEL=A",
            "PROFILE_1_LLM_PROVIDER=ollama",
            "PROFILE_1_LLM_MODEL=profile-model",
            "ACTIVE_PROFILE=a",
            "SETTINGS_PROFILE=a",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    monkeypatch.setattr(autostart, "sync_start_on_login", lambda _enabled: None)

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()
    try:
        profile_btn = dialog.findChild(QPushButton, "settingsProfilesButton")
        assert profile_btn is not None
        assert profile_btn.text() == "A"

        dialog._apply_preset("Low setup")
        app.processEvents()

        assert profile_btn.text() == "Low setup"
        assert dialog._pending_active_profile == "low_setup"
        assert dialog._apply_btn.isEnabled()

        monkeypatch.setattr(dialog, "_save_api_keys_to_keychain", lambda: True)
        monkeypatch.setattr(dialog, "_capability_warnings_for_values", lambda _vals: ([], {}))
        monkeypatch.setattr(dialog, "_set_warning_markers", lambda _warnings: None)
        assert dialog._do_save() is True

        saved = settings_env.read_settings_env()
        assert saved["ACTIVE_PROFILE"] == "low_setup"
        assert saved["SETTINGS_PROFILE"] == "low_setup"
        assert "OPENWAND_SETTINGS_PRESET" not in saved
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_legacy_low_setup_is_staged_as_a_saveable_profile_without_detected_status(tmp_path, monkeypatch):
    """The old preset marker becomes a normal pending profile migration."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.settings_panel.dialog as settings_dialog
    from ui.settings_panel import env as settings_env

    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join([
            "OPENWAND_SETTINGS_PRESET=low_setup",
            "ACTIVE_PROFILE=default",
            "SETTINGS_PROFILE=default",
            "LLM_PROVIDER=chatgpt",
            "LLM_MODEL=gpt-5.5",
            "VISION_LLM_PROVIDER=chatgpt",
            "VISION_LLM_MODEL=gpt-5.5",
            "MEMORY_LLM_PROVIDER=chatgpt",
            "MEMORY_LLM_MODEL=gpt-5.5",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()
    try:
        app.processEvents()
        assert dialog._profiles_btn.text() == "Low setup"
        assert dialog._pending_active_profile == "low_setup"
        assert dialog._active_preset_slug == ""
        assert dialog._apply_btn.isEnabled()
        assert "detected" not in dialog._status_lbl.text().casefold()
        assert "selected" not in dialog._status_lbl.text().casefold()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_profile_selection_clears_builtin_profile_marker(tmp_path, monkeypatch):
    """Switching back to a custom profile must not reopen as the built-in preset."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    import ui.settings_panel.dialog as settings_dialog
    from core.system import autostart
    from ui.settings_panel import env as settings_env

    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join([
            "OPENWAND_SETTINGS_PRESET=low_setup",
            "PROFILE_COUNT=1",
            "PROFILE_1_ID=a",
            "PROFILE_1_LABEL=A",
            "PROFILE_1_LLM_PROVIDER=ollama",
            "PROFILE_1_LLM_MODEL=profile-model",
            "ACTIVE_PROFILE=default",
            "SETTINGS_PROFILE=default",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    monkeypatch.setattr(autostart, "sync_start_on_login", lambda _enabled: None)

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()
    try:
        profile_btn = dialog.findChild(QPushButton, "settingsProfilesButton")
        assert profile_btn is not None
        assert profile_btn.text() == "Low setup"

        dialog._apply_saved_profile(1)
        app.processEvents()

        assert profile_btn.text() == "A"
        assert dialog._pending_active_profile == "a"

        monkeypatch.setattr(dialog, "_save_api_keys_to_keychain", lambda: True)
        monkeypatch.setattr(dialog, "_capability_warnings_for_values", lambda _vals: ([], {}))
        monkeypatch.setattr(dialog, "_set_warning_markers", lambda _warnings: None)
        assert dialog._do_save() is True

        saved = settings_env.read_settings_env()
        assert saved["ACTIVE_PROFILE"] == "a"
        assert saved["SETTINGS_PROFILE"] == "a"
        assert "OPENWAND_SETTINGS_PRESET" not in saved
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_low_setup_profile_does_not_create_a_second_preset_override_layer():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog, _get

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        dialog._apply_preset("Low setup")
        _set_value = "7"
        dialog._fields["MEMORY_TOP_K"].setText(_set_value)
        vals = {"MEMORY_TOP_K": _get(dialog._fields["MEMORY_TOP_K"])}

        persisted = dialog._preset_values_to_persist(vals)

        assert persisted == {}
        assert dialog._selected_profile_id() == "low_setup"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_can_create_custom_profile(tmp_path, monkeypatch):
    """Verify Settings can create a real PROFILE_N custom profile."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog, QLabel, QPushButton

    import ui.settings_panel.dialog as settings_dialog
    from ui.settings_panel import env as settings_env

    env_path = tmp_path / ".env"
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Local Research", True))

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()

    try:
        profile_btn = dialog.findChild(QPushButton, "settingsProfilesButton")
        profile_label = dialog.findChild(QLabel, "settingsProfileLabel")
        assert profile_btn is not None
        assert profile_label is not None
        assert profile_label.text() == "Profile"
        assert profile_btn.text() == "Default"

        dialog._fields["CONTEXT_BROWSER_MAX_CHARS"].setText("12345")
        dialog._create_custom_profile()

        saved = settings_env.read_settings_env()
        assert saved["PROFILE_COUNT"] == "1"
        assert saved["PROFILE_1_ID"] == "local-research"
        assert saved["PROFILE_1_LABEL"] == "Local Research"
        assert saved["PROFILE_1_CONTEXT_BROWSER_MAX_CHARS"] == "12345"
        assert "ACTIVE_PROFILE" not in saved
        assert "SETTINGS_PROFILE" not in saved
        assert dialog._pending_active_profile == "local-research"
        assert profile_btn.text() == "Local Research"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_profiles_menu_shows_saved_profile_names_without_prefix(tmp_path, monkeypatch):
    """Verify saved profile menu entries are plain names."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.settings_panel.dialog as settings_dialog
    from ui.settings_panel import env as settings_env

    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join([
            "PROFILE_COUNT=2",
            "PROFILE_1_ID=new",
            "PROFILE_1_LABEL=New",
            "PROFILE_2_ID=new-2",
            "PROFILE_2_LABEL=New 2",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()
    menu = None
    try:
        menu = dialog._build_profiles_menu(dialog)
        action_texts = [action.text() for action in menu.actions() if action.text()]

        assert "New" in action_texts
        assert "New 2" in action_texts
        assert all(not text.startswith("Use saved profile") for text in action_texts)
        assert "Rename profile..." in action_texts
        assert "Delete profile..." in action_texts
    finally:
        if menu is not None:
            menu.deleteLater()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_active_profile_controls_displayed_and_saved_model(tmp_path, monkeypatch):
    """Settings must show and update the same profile model the runtime resolves."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    import ui.settings_panel.dialog as settings_dialog
    from core.system import autostart
    from ui.settings_panel import env as settings_env

    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join([
            "LLM_PROVIDER=openai",
            "LLM_MODEL=stale-global-model",
            "CONTEXT_BROWSER_MAX_CHARS=111",
            "PROFILE_COUNT=1",
            "PROFILE_1_ID=wizard-profile",
            "PROFILE_1_LABEL=Wizard Profile",
            "PROFILE_1_LLM_PROVIDER=ollama",
            "PROFILE_1_LLM_MODEL=profile-model",
            "PROFILE_1_CONTEXT_BROWSER_MAX_CHARS=222",
            "ACTIVE_PROFILE=wizard-profile",
            "SETTINGS_PROFILE=wizard-profile",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()
    try:
        profile_btn = dialog.findChild(QPushButton, "settingsProfilesButton")
        llm_row = dialog._model_section_rows["LLM"][0]
        assert profile_btn is not None
        assert profile_btn.text() == "Wizard Profile"
        assert llm_row["api_key_combo"].currentData() == "ollama"
        assert dialog._model_value(llm_row) == "profile-model"
        assert dialog._fields["CONTEXT_BROWSER_MAX_CHARS"].text() == "222"

        dialog._apply_env_values_to_ui(
            {"LLM_PROVIDER": "ollama", "LLM_MODEL": "updated-profile-model"}
        )
        captured = {}
        monkeypatch.setattr(dialog, "_save_api_keys_to_keychain", lambda: True)
        monkeypatch.setattr(dialog, "_capability_warnings_for_values", lambda _vals: ([], {}))
        monkeypatch.setattr(dialog, "_set_warning_markers", lambda _warnings: None)
        monkeypatch.setattr(autostart, "sync_start_on_login", lambda _enabled: None)
        monkeypatch.setattr(settings_dialog, "_write_env", lambda vals, remove_keys=None: captured.update(vals))

        assert dialog._do_save() is True
        assert captured["LLM_MODEL"] == "updated-profile-model"
        assert captured["PROFILE_1_LLM_MODEL"] == "updated-profile-model"
        assert captured["PROFILE_1_LLM_PROVIDER"] == "ollama"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_can_rename_and_delete_custom_profile(tmp_path, monkeypatch):
    """Verify custom profiles are manageable after creation."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

    import ui.settings_panel.dialog as settings_dialog
    from ui.settings_panel import env as settings_env

    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join([
            "PROFILE_COUNT=1",
            "PROFILE_1_ID=new",
            "PROFILE_1_LABEL=New",
            "PROFILE_1_CONTEXT_BROWSER_MAX_CHARS=111",
            "ACTIVE_PROFILE=new",
            "SETTINGS_PROFILE=new",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Renamed", True))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()
    try:
        dialog._pending_active_profile = "new"
        dialog._rename_custom_profile()
        saved = settings_env.read_settings_env()

        assert saved["PROFILE_1_ID"] == "new"
        assert saved["PROFILE_1_LABEL"] == "Renamed"
        assert saved["PROFILE_1_CONTEXT_BROWSER_MAX_CHARS"] == "111"

        dialog._delete_custom_profile()
        saved = settings_env.read_settings_env()

        assert saved["PROFILE_COUNT"] == "0"
        assert "PROFILE_1_ID" not in saved
        assert "PROFILE_1_LABEL" not in saved
        assert "PROFILE_1_CONTEXT_BROWSER_MAX_CHARS" not in saved
        assert "ACTIVE_PROFILE" not in saved
        assert "SETTINGS_PROFILE" not in saved
        assert dialog._pending_active_profile == ""
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_reset_all_settings_failure_matrix_stays_controlled(tmp_path, monkeypatch):
    """Cancel, missing files, and every cleanup fault keep the reset dialog alive."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import config
    import ui.settings_panel.dialog as settings_dialog
    from core import secret_store, tts
    from core.auth import chatgpt, copilot_auth, github
    from core.llm_clients import client as llm_client
    from ui.settings_panel import env as settings_env
    from ui.shared import theme

    env_path = tmp_path / ".env"
    env_path.write_text("THEME_MODE=dark\n", encoding="utf-8")
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ())
    monkeypatch.setattr(chatgpt, "clear_tokens", lambda: None)
    monkeypatch.setattr(github, "clear_tokens", lambda: None)
    monkeypatch.setattr(copilot_auth, "clear_token", lambda: None)
    monkeypatch.setattr(config, "reload", lambda: None)
    monkeypatch.setattr(llm_client, "reset_clients", lambda: None)
    monkeypatch.setattr(tts, "reset_connections", lambda: None)
    # _reset_all imports this helper at call time, so patch the defining module
    # instead of adding an unused attribute to settings_dialog. Reapplying the
    # global Qt stylesheet for every injected unlink fault makes the matrix test
    # unrelated whole-app windows and can flood the event queue.
    monkeypatch.setattr(theme, "apply_app_theme", lambda *_args, **_kwargs: None)
    warnings: list[str] = []
    infos: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, message: warnings.append(message))
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, message: infos.append(message))

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()
    try:
        dialog._reset_stt_model_in_background = lambda: None
        dialog._apply_dialog_theme = lambda: None
        dialog._load_values = lambda: None
        dialog._refresh_tab_labels = lambda: None
        dialog._refresh_search_index = lambda: None
        dialog._on_apply = None

        # Cancel is a strict no-op.
        monkeypatch.setattr(QMessageBox, "exec", lambda _self: QMessageBox.StandardButton.No)
        before = env_path.read_bytes()
        dialog._reset_all()
        assert env_path.read_bytes() == before

        monkeypatch.setattr(QMessageBox, "exec", lambda _self: QMessageBox.StandardButton.Yes)

        # An already-missing settings target is a successful idempotent reset.
        env_path.unlink()
        dialog._reset_all()
        assert not env_path.exists()

        failures = (
            PermissionError("target required by this function is locked"),
            PermissionError("required elevation is denied"),
            PermissionError("storage access is denied"),
            OSError("another process is using the files"),
            OSError("cleanup only partly completes"),
        )
        original_unlink = Path.unlink
        for failure in failures:
            env_path.write_text("THEME_MODE=dark\n", encoding="utf-8")

            def fail_target_unlink(path, *args, error=failure, **kwargs):
                if path == env_path:
                    raise error
                return original_unlink(path, *args, **kwargs)

            with monkeypatch.context() as scoped:
                scoped.setattr(Path, "unlink", fail_target_unlink)
                dialog._reset_all()
            assert env_path.exists()
            assert str(failure) in warnings[-1]
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_profile_delete_failure_matrix_is_transactional(tmp_path, monkeypatch):
    """Missing/cancelled/locked/partial profile deletion cannot corrupt Settings."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import ui.settings_panel.dialog as settings_dialog
    from ui.settings_panel import env as settings_env

    env_path = tmp_path / ".env"
    initial = (
        "PROFILE_COUNT=1\n"
        "PROFILE_1_ID=keep-me\n"
        "PROFILE_1_LABEL=Keep Me\n"
        "ACTIVE_PROFILE=keep-me\n"
        "SETTINGS_PROFILE=keep-me\n"
    )
    env_path.write_text(initial, encoding="utf-8")
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, message: warnings.append(message))

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()
    try:
        # A stale/missing menu target is an idempotent no-op.
        dialog._choose_saved_profile_slot = lambda *_args: None
        dialog._delete_custom_profile()
        assert env_path.read_text(encoding="utf-8") == initial

        chosen = (1, "keep-me", "Keep Me")
        dialog._choose_saved_profile_slot = lambda *_args: chosen

        # Explicit cancellation cannot touch persistence.
        monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.No)
        dialog._delete_custom_profile()
        assert env_path.read_text(encoding="utf-8") == initial

        monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
        failures = (
            PermissionError("target required by this function is locked"),
            PermissionError("required elevation is denied"),
            PermissionError("storage access is denied"),
            OSError("another process is using the files"),
            OSError("cleanup only partly completes"),
        )
        for failure in failures:
            before_env = dict(dialog._env)
            with monkeypatch.context() as scoped:
                scoped.setattr(
                    settings_dialog,
                    "_write_env",
                    lambda *_a, error=failure, **_k: (_ for _ in ()).throw(error),
                )
                dialog._delete_custom_profile()
            assert dialog._env == before_env
            assert env_path.read_text(encoding="utf-8") == initial
            assert str(failure) in warnings[-1]
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_profile_create_failure_matrix_is_controlled(tmp_path, monkeypatch):
    """Create handles empty/invalid/duplicate names and every persistence fault."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

    import ui.settings_panel.dialog as settings_dialog
    from ui.settings_panel import env as settings_env

    env_path = tmp_path / ".env"
    initial = "PROFILE_COUNT=1\nPROFILE_1_ID=existing\nPROFILE_1_LABEL=Existing\n"
    env_path.write_text(initial, encoding="utf-8")
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, message: warnings.append(message))
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()
    try:
        # Empty/cancelled input never reaches persistence.
        monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("", True))
        dialog._create_custom_profile()
        assert env_path.read_text(encoding="utf-8") == initial

        # A punctuation-only name is normalized to a valid fallback id.
        captured: dict[str, str] = {}
        monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("!!!", True))
        with monkeypatch.context() as scoped:
            scoped.setattr(settings_dialog, "_write_env", lambda vals, **_k: captured.update(vals))
            dialog._create_custom_profile()
        assert captured["PROFILE_2_ID"] == "custom"

        # Reset the in-memory model, then prove a duplicate label receives a unique id.
        dialog._env = settings_env.read_settings_env()
        captured.clear()
        monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("Existing", True))
        with monkeypatch.context() as scoped:
            scoped.setattr(settings_dialog, "_write_env", lambda vals, **_k: captured.update(vals))
            dialog._create_custom_profile()
        assert captured["PROFILE_2_ID"] == "existing-2"

        failures = (
            PermissionError("backing store is read-only"),
            PermissionError("backing store is locked"),
            ValueError("backing store is corrupt"),
            OSError("write is interrupted"),
        )
        for failure in failures:
            dialog._env = settings_env.read_settings_env()
            before_env = dict(dialog._env)
            monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("New Profile", True))
            with monkeypatch.context() as scoped:
                scoped.setattr(
                    settings_dialog,
                    "_write_env",
                    lambda *_a, error=failure, **_k: (_ for _ in ()).throw(error),
                )
                dialog._create_custom_profile()
            assert dialog._env == before_env
            assert env_path.read_text(encoding="utf-8") == initial
            assert str(failure) in warnings[-1]
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_profile_rename_failure_matrix_is_controlled(tmp_path, monkeypatch):
    """Rename rejects empty/duplicate values and contains every persistence fault."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

    import ui.settings_panel.dialog as settings_dialog
    from ui.settings_panel import env as settings_env

    env_path = tmp_path / ".env"
    initial = (
        "PROFILE_COUNT=2\n"
        "PROFILE_1_ID=first\nPROFILE_1_LABEL=First\n"
        "PROFILE_2_ID=second\nPROFILE_2_LABEL=Second\n"
    )
    env_path.write_text(initial, encoding="utf-8")
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, message: warnings.append(message))
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()
    try:
        dialog._choose_saved_profile_slot = lambda *_args: (1, "first", "First")

        monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("", True))
        dialog._rename_custom_profile()
        assert env_path.read_text(encoding="utf-8") == initial

        # Duplicate names are rejected case-insensitively before persistence.
        monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("second", True))
        dialog._rename_custom_profile()
        assert env_path.read_text(encoding="utf-8") == initial
        assert "already exists" in warnings[-1]

        failures = (
            ValueError("new value is invalid"),
            PermissionError("backing store is read-only"),
            PermissionError("backing store is locked"),
            ValueError("backing store is corrupt"),
            OSError("write is interrupted"),
        )
        for failure in failures:
            before_env = dict(dialog._env)
            monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("Renamed", True))
            with monkeypatch.context() as scoped:
                scoped.setattr(
                    settings_dialog,
                    "_write_env",
                    lambda *_a, error=failure, **_k: (_ for _ in ()).throw(error),
                )
                dialog._rename_custom_profile()
            assert dialog._env == before_env
            assert env_path.read_text(encoding="utf-8") == initial
            assert str(failure) in warnings[-1]
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_update_check_download_repo_failure_matrix_is_in_band(tmp_path, monkeypatch):
    """All updater modes surface fault classes in Settings and remain retryable."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QMessageBox

    import ui.settings_panel.dialog as settings_dialog
    from core import updater

    class ImmediateThread:
        def __init__(self, *, target, daemon, name):
            self.target = target
            assert daemon is True
            assert name.startswith("openwand-")

        def start(self):
            self.target()

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()
    monkeypatch.setattr(settings_dialog.threading, "Thread", ImmediateThread)
    asset = updater.UpdateAsset("windows-x64", "OpenWand.zip", "https://example.invalid/OpenWand.zip")
    faults = (
        ConnectionError("network access is unavailable"),
        FileNotFoundError("package source is unavailable"),
        OSError("available disk space is insufficient"),
        PermissionError("filesystem permission is insufficient"),
        updater.UpdateError("verification fails"),
        RuntimeError("dependency versions conflict"),
    )
    try:
        for failure in faults:
            for mode, method_name, action in (
                ("check", "check_for_updates", dialog._check_for_updates),
                ("repo", "apply_repo_update", dialog._pull_repo_update),
                ("download", "download_update", dialog._download_available_update),
            ):
                dialog._update_running = False
                dialog._update_mode = mode
                dialog._update_check_result = SimpleNamespace(asset=asset)
                with monkeypatch.context() as scoped:
                    scoped.setattr(
                        updater,
                        method_name,
                        lambda *_a, error=failure, **_k: (_ for _ in ()).throw(error),
                    )
                    action()
                    app.processEvents()
                assert str(failure) in dialog._update_status_lbl.text()
                assert dialog._update_running is False
                assert dialog._update_btn.isEnabled()

        # Applying is the destructive/cancellable stage. Cancellation must not
        # invoke the helper or discard the verified download.
        downloaded = tmp_path / "OpenWand.zip"
        downloaded.write_bytes(b"verified update")
        dialog._update_download_path = downloaded
        dialog._update_mode = "apply"
        monkeypatch.setattr(QMessageBox, "exec", lambda _self: QMessageBox.StandardButton.Cancel)
        monkeypatch.setattr(
            updater,
            "apply_update",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cancelled update was applied")),
        )
        dialog._apply_downloaded_update()
        assert dialog._update_download_path == downloaded
        assert "ready" in dialog._update_status_lbl.text().lower()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_advanced_tab_contains_tuning_and_memory_controls():
    """Verify settings advanced tab contains tuning and memory controls."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        advanced_tab = dialog._tabs.widget(dialog._tab_base_names.index("Advanced"))

        assert advanced_tab.isAncestorOf(dialog._fields["BUBBLE_REVEAL_WPM"])
        assert advanced_tab.isAncestorOf(dialog._fields["MEMORY_TOP_K"])
        assert advanced_tab.isAncestorOf(dialog._fields["MEMORY_STM_TOKEN_BUDGET"])
        assert advanced_tab.isAncestorOf(dialog._fields["CONTEXT_BROWSER_MAX_CHARS"])
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_caller_memory_context_block_uses_third_row_in_responsive_grid():
    """Verify Memory occupies the third row of the compact two-column grid."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QVBoxLayout, QWidget

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._caller_blocks = []
    dialog._callers_vlayout = QVBoxLayout(host)
    dialog._fields = {}

    try:
        SettingsDialog._add_caller_block(dialog, intents=[])
        frame = dialog._caller_blocks[0]["widget"]
        memory_pos = None
        for child in frame.findChildren(QWidget):
            labels = child.findChildren(QLabel)
            if not any(label.text() == t("Memory") for label in labels):
                continue
            parent = child.parentWidget()
            layout = parent.layout() if parent is not None else None
            if isinstance(layout, QGridLayout):
                idx = layout.indexOf(child)
                if idx >= 0:
                    memory_pos = layout.getItemPosition(idx)
                    break
            if memory_pos is not None:
                break

        assert memory_pos is not None
        assert memory_pos[:2] == (3, 1)
    finally:
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_caller_custom_prompt_row_lives_with_intent_rows():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog, _get

    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._caller_blocks = []
    dialog._callers_vlayout = QVBoxLayout(host)
    dialog._fields = {}

    try:
        SettingsDialog._add_caller_block(
            dialog,
            custom_key="s",
            custom_label="Freeform",
            intents=[
                {"key": "w", "label": "Ask", "prompt": "Ask"},
                {"key": "a", "label": "Explain", "prompt": "Explain"},
                {"key": "d", "label": "Fix", "prompt": "Fix"},
            ],
        )
        blk = dialog._caller_blocks[0]
        custom_row = blk["custom_key"].parentWidget()

        assert _get(blk["custom_key"]) == "s"
        assert _get(blk["custom_label"]) == "Freeform"
        assert blk["custom_prompt"].isEnabled() is False
        assert blk["custom_prompt"].text() == t("Custom prompt")
        assert blk["intents_layout"].indexOf(custom_row) == 3
    finally:
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_refresh_runs_on_background_thread(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.memory_viewer as memory_viewer
    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)
    started: list[dict] = []

    class FakeManager:
        """Coordinate fake manager behavior."""
        def get_all_facts(self):
            raise AssertionError("refresh should not run on the UI thread")

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            started.append({"target": target, "name": name, "daemon": daemon, "started": False})

        def start(self) -> None:
            started[-1]["started"] = True

    monkeypatch.setattr(memory_viewer.threading, "Thread", FakeThread)
    panel = MemoryPanel(FakeManager(), initial_facts=[])

    try:
        panel.refresh_facts()

        assert started
        assert started[0]["name"] == "openwand-memory-refresh"
        assert started[0]["daemon"] is True
        assert started[0]["started"] is True
    finally:
        panel.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_read_only_hides_mutation_controls():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.i18n import t
    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)

    class FakeManager:
        """Coordinate fake manager behavior."""
        def get_all_facts(self):
            return []

    panel = MemoryPanel(
        FakeManager(),
        initial_facts=[
            {"id": "fact-1", "text": "I prefer stable settings", "category": "general"}
        ],
        read_only=True,
    )

    try:
        assert not hasattr(panel, "_add_text")
        button_texts = {button.text() for button in panel.findChildren(QPushButton)}
        assert "Add" not in button_texts
        assert "X" not in button_texts
        assert t("Refresh") in button_texts
    finally:
        panel.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_add_runs_on_background_thread(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.memory_viewer as memory_viewer
    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)
    started: list[dict] = []

    class FakeManager:
        """Coordinate fake manager behavior."""
        def add_fact_manual(self, _text, category="general", project=None):
            raise AssertionError("add should not run on the UI thread")

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            started.append({"target": target, "name": name, "daemon": daemon, "started": False})

        def start(self) -> None:
            started[-1]["started"] = True

    monkeypatch.setattr(memory_viewer.threading, "Thread", FakeThread)
    panel = MemoryPanel(FakeManager(), initial_facts=[])

    try:
        panel._add_text.setText("I prefer fast settings")
        panel._on_add_fact()

        assert panel._add_text.text() == "I prefer fast settings"
        assert started
        assert started[0]["name"] == "openwand-memory-add"
        assert started[0]["daemon"] is True
        assert started[0]["started"] is True
    finally:
        panel.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_fact_add_failure_matrix_keeps_input_retryable(monkeypatch):
    """Rejected, duplicate, and failed manual facts keep the typed text for retry."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import ui.memory_viewer as memory_viewer
    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            assert name == "openwand-memory-add"
            assert daemon is True

        def start(self):
            self.target()

    monkeypatch.setattr(memory_viewer.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok)
    class MustNotAddEmpty:
        def add_fact_manual(self, *_args, **_kwargs):
            raise AssertionError("empty manual facts must stop before storage")

    empty_panel = MemoryPanel(MustNotAddEmpty(), initial_facts=[])
    empty_panel._on_add_fact()
    assert empty_panel._add_text.text() == ""
    empty_panel.deleteLater()

    failures = (
        False,
        ValueError("new value is invalid"),
        PermissionError("backing store is read-only"),
        BlockingIOError("backing store is locked"),
        RuntimeError("backing store is corrupt"),
        OSError("write is interrupted"),
    )
    for failure in failures:
        class FailingManager:
            def add_fact_manual(self, _text, category="general", project=None, value=failure):
                if isinstance(value, BaseException):
                    raise value
                return value

        panel = MemoryPanel(FailingManager(), initial_facts=[])
        panel._add_text.setText("I prefer concise answers")
        panel._on_add_fact()
        app.processEvents()

        assert panel._add_text.text() == "I prefer concise answers"
        panel.deleteLater()
    app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_fact_delete_failure_matrix_keeps_retryable_row(monkeypatch):
    """A fact row disappears only after its real background deletion succeeds."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox, QVBoxLayout, QWidget

    import ui.memory_viewer as memory_viewer
    from ui.memory_viewer import _FactRow

    app = QApplication.instance() or QApplication(sys.argv)

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            assert name == "openwand-memory-delete"
            assert daemon is True

        def start(self):
            self.target()

    monkeypatch.setattr(memory_viewer.threading, "Thread", ImmediateThread)
    failures = (
        PermissionError("target required by this function is locked"),
        PermissionError("required elevation is denied"),
        PermissionError("storage access is denied"),
        OSError("another process is using the files"),
        OSError("cleanup only partly completes"),
    )

    for answer, failure in (
        (QMessageBox.StandardButton.No, None),
        *((QMessageBox.StandardButton.Yes, item) for item in failures),
    ):
        parent = QWidget()
        layout = QVBoxLayout(parent)

        class FailingManager:
            def delete_fact(self, _fact_id, error=failure):
                if error is not None:
                    raise error

        row = _FactRow(
            {"id": "fact-1", "text": "Keep this fact", "category": "general"},
            FailingManager(),
            parent,
        )
        layout.addWidget(row)
        monkeypatch.setattr(QMessageBox, "question", lambda *_a, value=answer, **_k: value)

        row._on_delete()
        app.processEvents()

        assert layout.indexOf(row) >= 0
        assert row.parent() is parent
        parent.deleteLater()
    app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_fact_project_move_failure_matrix_keeps_editor_retryable(monkeypatch):
    """Moving a fact never escapes validation/store faults or destroys its editor."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    import ui.memory_viewer as memory_viewer
    from ui.memory_viewer import _FactRow

    app = QApplication.instance() or QApplication(sys.argv)

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            assert name == "openwand-memory-update"
            assert daemon is True

        def start(self):
            self.target()

    monkeypatch.setattr(memory_viewer.threading, "Thread", ImmediateThread)
    failures = (
        ValueError("new value is invalid"),
        ValueError("new value duplicates an existing value"),
        PermissionError("backing store is read-only"),
        PermissionError("backing store is locked"),
        ValueError("backing store is corrupt"),
        OSError("write is interrupted"),
    )
    for failure in failures:
        parent = QWidget()
        layout = QVBoxLayout(parent)

        class FailingManager:
            def update_fact(self, *_args, error=failure, **_kwargs):
                raise error

        row = _FactRow(
            {"id": "fact-1", "text": "Keep this fact", "category": "general"},
            FailingManager(),
            parent,
            projects=[{"id": "project-2", "name": "Project 2"}],
        )
        layout.addWidget(row)
        row._proj_combo.setCurrentIndex(row._proj_combo.findData("project-2"))
        row._save_fact()
        app.processEvents()
        assert layout.indexOf(row) >= 0
        assert row._text_edit.text() == "Keep this fact"
        parent.deleteLater()

    # Empty text is rejected before any store call or background task starts.
    parent = QWidget()
    layout = QVBoxLayout(parent)

    class MustNotUpdate:
        def update_fact(self, *_args, **_kwargs):
            raise AssertionError("empty fact must not reach persistence")

    row = _FactRow(
        {"id": "fact-1", "text": "Keep this fact", "category": "general"},
        MustNotUpdate(),
        parent,
    )
    layout.addWidget(row)
    row._text_edit.setText("   ")
    row._save_fact()
    assert layout.indexOf(row) >= 0
    parent.deleteLater()
    app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_keybinds_has_voice_block_and_tools_buttons():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        assert "HOTKEY_VOICE" in dialog._fields
        assert "HOTKEY_READ_SELECTION_ALOUD" in dialog._fields
        vb = dialog._voice_block
        assert set(vb) >= {
            "context_ambient",
            "context_documents_mode",
            "context_browser_mode",
            "context_github_mode",
            "context_memory_mode",
            "context_screenshot",
            "tool_overrides",
        }
        sb = dialog._snip_block
        assert set(sb) >= {
            "context_ambient",
            "context_documents_mode",
            "context_browser_mode",
            "context_github_mode",
            "context_memory_mode",
            "context_screenshot",
            "file_access",
            "tool_overrides",
        }
        assert sb["context_screenshot"].currentData() == "off"
        assert not sb["context_screenshot"].isEnabled()
        tools_buttons = [
            b
            for b in dialog.findChildren(QPushButton)
            if b.text() == t("Allowed tools…")
        ]
        # One per caller block plus snip and voice.
        assert len(tools_buttons) == len(dialog._caller_blocks) + 2
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_shortcuts_are_categorized_with_two_bindings_and_inline_details(isolated_default_profile):
    """The shortcut page uses categorized rows and inline per-action editors."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    # Pages after the first are built on the dialog's first paint; this test
    # inspects them directly, so materialize them up front.
    dialog._build_deferred_pages()

    try:
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("Keybinds"))
        dialog.resize(760, 736)
        dialog.show()
        app.processEvents()
        keybinds_page = dialog._page_widget(dialog._tabs.currentIndex())
        assert keybinds_page.horizontalScrollBar().maximum() == 0

        all_labels = dialog.findChildren(QLabel)
        labels = {label.text() for label in all_labels}
        section_titles = {
            label.text().removeprefix("⚠ ")
            for label in all_labels
            if label.objectName() == "shortcutSectionTitle"
        }
        assert {
            t("On"),
            t("Action"),
            t("Shortcut 1"),
            t("Shortcut 2"),
            t("Details"),
        } <= labels
        assert {
            t("Global shortcuts"),
            t("Voice shortcuts"),
            t("Context shortcuts"),
        } <= section_titles
        assert t("Application actions") not in labels
        assert not any(
            t("These actions come from app folders") in label.text()
            for label in all_labels
        )

        for key in (
            "HOTKEY_ADD_CONTEXT",
            "HOTKEY_CLEAR_CONTEXT",
            "HOTKEY_SNIP",
            "HOTKEY_READ_SELECTION_ALOUD",
            "HOTKEY_VOICE",
            "HOTKEY_DICTATE",
            "HOTKEY_VOICE_LIVE",
        ):
            assert {key, f"{key}_2", f"{key}_ENABLED"} <= dialog._fields.keys()

        caller = dialog._caller_blocks[0]
        assert {"hotkey", "hotkey_2", "enabled", "detail"} <= caller.keys()
        assert caller["detail"].isHidden()

        detailed_entries = [
            entry for entry in dialog._shortcut_rows if entry["detail"] is not None
        ]
        assert len(detailed_entries) >= 2
        first, second = detailed_entries[:2]
        first_button = next(
            button
            for button in first["widget"].findChildren(QPushButton)
            if button.text() == t("Customize")
        )
        second_button = next(
            button
            for button in second["widget"].findChildren(QPushButton)
            if button.text() == t("Customize")
        )

        first_button.click()
        app.processEvents()
        assert not first["detail"].isHidden()
        assert first_button.text() == t("Close")

        second_button.click()
        app.processEvents()
        assert first["detail"].isHidden()
        assert not second["detail"].isHidden()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_context_source_blocks_use_even_columns():
    """Verify caller context source blocks stay compact and keep even widths."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFrame,
        QLabel,
        QVBoxLayout,
        QWidget,
    )

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    host = QWidget()

    try:
        row, _controls = dialog._build_context_controls()
        layout = QVBoxLayout(host)
        layout.addWidget(row)
        host.resize(1800, 260)
        host.show()
        app.processEvents()

        frames = [
            frame
            for frame in row.findChildren(QFrame)
            if frame.objectName() == "contextSourceBlock"
        ]
        first_row_y = min(frame.y() for frame in frames)
        first_row_widths = [
            frame.width()
            for frame in frames
            if frame.y() == first_row_y
        ]

        assert len(first_row_widths) == 2
        assert max(first_row_widths) - min(first_row_widths) <= 1
        assert all(frame.minimumWidth() >= 160 for frame in frames)
        assert all(frame.minimumHeight() == 88 for frame in frames)
        assert all(not frame.findChildren(QCheckBox) for frame in frames)
        assert all(len(frame.findChildren(QComboBox)) == 1 for frame in frames)
        for frame in frames:
            title_label, key_label = frame.findChildren(QLabel)[:2]
            assert title_label.parentWidget() is key_label.parentWidget()
            assert title_label.geometry().center().y() == key_label.geometry().center().y()
    finally:
        host.deleteLater()
        dialog.deleteLater()
        app.processEvents()


def test_reset_keybinds_page_includes_voice_keys():
    from ui.settings_panel.dialog import SettingsDialog

    env = {
        "VOICE_TOOLS": "x:on",
        "VOICE_CONTEXT_BROWSER_MODE": "model",
        "CALLER_1_TOOLS": "y:model",
    }
    keys = SettingsDialog._reset_env_keys_for_page("Keybinds", env)
    assert {
        "VOICE_TOOLS",
        "VOICE_CONTEXT_BROWSER_MODE",
        "CALLER_1_TOOLS",
        "HOTKEY_VOICE",
        "HOTKEY_VOICE_2",
        "HOTKEY_VOICE_ENABLED",
        "HOTKEY_READ_SELECTION_ALOUD",
        "HOTKEY_READ_SELECTION_ALOUD_2",
        "HOTKEY_READ_SELECTION_ALOUD_ENABLED",
    } <= keys


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tool_access_dialog_round_trips_overrides():
    """Verify tool access dialog only round trips non-context tool overrides."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.tool_access import ToolAccessDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = ToolAccessDialog(
        method_label="Test",
        overrides={"github_repo": "off", "read_file": "model"},
        governed_modes={
            "Open docs": "auto",
            "Browser/Web": "off",
            "Git/GitHub": "model",
            "Memory": "model",
            "Screenshot": "off",
            "Files": "read",
        },
        extra_tools=[
            {
                "name": "mcp_example_echo",
                "description": "[MCP:example] Echo back text.",
            }
        ],
    )

    try:
        combos = dlg._combos
        assert {
            "list_files", "read_file", "create_file", "edit_file", "write_file",
            "mcp_server.example", "mcp_example_echo",
        } <= set(combos)
        assert {
            "web_search", "get_context", "retrieve_website", "git_status", "git_diff",
            "github_repo", "github_issue", "memory_search", "capture_screen",
        }.isdisjoint(combos)
        assert combos["read_file"].currentData() == "on"
        assert combos["create_file"].currentData() == "off"
        assert combos["mcp_server.example"].currentData() == "on"
        assert combos["mcp_example_echo"].currentData() == ""

        # Selectors left matching their defaults store nothing; explicit
        # deviations round-trip. Hidden context-tool overrides are dropped.
        combos["edit_file"].setCurrentIndex(combos["edit_file"].findData("on"))
        combos["mcp_server.example"].setCurrentIndex(combos["mcp_server.example"].findData("off"))
        combos["mcp_example_echo"].setCurrentIndex(combos["mcp_example_echo"].findData("on"))
        result = dlg.selected_overrides()
        assert result["edit_file"] == "on"
        assert result["mcp_server.example"] == "off"
        assert result["mcp_example_echo"] == "on"
        assert "github_repo" not in result
    finally:
        dlg.deleteLater()
        app.processEvents()


def test_bubble_hide_delay_seconds_round_trip():
    from ui.settings_panel.dialog import _ms_to_seconds_str, _seconds_str_to_ms

    assert _ms_to_seconds_str("3500", 3500) == "3.5"
    assert _ms_to_seconds_str("8000", 3500) == "8"
    assert _ms_to_seconds_str("garbage", 3500) == "3.5"
    assert _seconds_str_to_ms("3.5", 3500) == "3500"
    assert _seconds_str_to_ms("8", 3500) == "8000"
    assert _seconds_str_to_ms("0.1", 3500) == "500"  # clamped to the 0.5s floor
    assert _seconds_str_to_ms("garbage", 3500) == "3500"


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_opens_with_only_the_first_page_built():
    """Only the General page is constructed before the window can be shown."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    try:
        assert dialog._pending_page_builds, "later pages should still be pending"
        assert not dialog._values_loaded

        # The visible page is nonetheless fully populated, not blank.
        assert dialog._tabs.currentIndex() == dialog._app_tab_index
        assert dialog._fields["THEME_MODE"].currentData()
        assert "BUBBLE_WIDTH" in dict.keys(dialog._fields)

        # Painting is what fills in the rest.
        dialog.show()
        for _ in range(10):
            app.processEvents()
            if not dialog._pending_page_builds:
                break
        assert not dialog._pending_page_builds
        assert dialog._values_loaded
        assert dialog.select_page("LLM") is True
        assert dialog._tab_base_names[dialog._tabs.currentIndex()] == "LLM"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_reading_a_later_page_field_materializes_every_page():
    """Deferral stays invisible: touching any deferred state finishes the build."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)

    reference = SettingsDialog()
    reference._build_deferred_pages()
    expected = dict(reference._snapshot_settings())

    lazy = SettingsDialog()
    try:
        assert lazy._pending_page_builds
        # A field that lives on the deferred Voice page.
        assert lazy._fields["KOKORO_VOICE"] is not None
        assert not lazy._pending_page_builds
        # Same for membership tests and page-owned attributes.
        assert "HOTKEY_VOICE" in lazy._fields
        assert isinstance(lazy._voice_block, dict)
        assert lazy._model_section_rows["LLM"]

        # Values match a dialog that built everything up front, and nothing
        # reads as an unsaved edit.
        assert dict(lazy._snapshot_settings()) == expected
        assert lazy._dirty_keys == set()
        assert not lazy._apply_btn.isEnabled()
    finally:
        lazy.deleteLater()
        reference.deleteLater()
        app.processEvents()
