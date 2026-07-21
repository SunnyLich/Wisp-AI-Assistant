"""Executable fault matrix for the shared Settings persistence/runtime boundary."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    yield app
    app.processEvents()


def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel import env as settings_env

    monkeypatch.setattr(settings_dialog, "ENV_PATH", path)
    monkeypatch.setattr(settings_env, "ENV_PATH", path)


def test_corrupt_settings_store_loads_safe_defaults(qapp, tmp_path, monkeypatch):
    """Invalid settings bytes cannot prevent the real Settings dialog from opening."""
    from ui.settings_panel import env as settings_env
    from ui.settings_panel.dialog import SettingsDialog

    env_path = tmp_path / "corrupt.env"
    env_path.write_bytes(b"APP_LANGUAGE=\xff\xfe\x80")
    _isolate_settings_env(monkeypatch, env_path)

    assert settings_env.read_settings_env() == {}
    dialog = SettingsDialog()
    try:
        assert dialog._env == {}
        assert "APP_LANGUAGE" in dialog._fields
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize(
    "failure",
    [
        ValueError("the new value is empty"),
        ValueError("the new value is invalid"),
        ValueError("the new value duplicates an existing value"),
        PermissionError("settings store is read-only"),
        PermissionError("settings store is locked"),
        ValueError("settings store is corrupt"),
        OSError("settings write is interrupted"),
        FileNotFoundError("required settings resource is missing"),
    ],
    ids=[
        "empty-value",
        "invalid-value",
        "duplicate-value",
        "read-only",
        "locked",
        "corrupt",
        "write-interrupted",
        "missing-resource",
    ],
)
def test_settings_write_and_resource_failures_are_controlled(qapp, tmp_path, monkeypatch, failure):
    """Save-time filesystem/resource faults remain controlled and do not claim success."""
    from PySide6.QtWidgets import QMessageBox

    from ui.settings_panel.dialog import SettingsDialog

    env_path = tmp_path / "settings.env"
    env_path.write_text("BUBBLE_WIDTH=340\n", encoding="utf-8")
    _isolate_settings_env(monkeypatch, env_path)
    before = env_path.read_bytes()
    warnings = []
    dialog = SettingsDialog()
    try:
        dialog._do_save = lambda: (_ for _ in ()).throw(failure)
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda _parent, title, message: warnings.append((title, message)),
        )

        assert dialog._apply_settings() is False
        assert str(failure) in warnings[0][1]
        assert env_path.read_bytes() == before
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_settings_cancel_discards_pending_change(qapp, tmp_path, monkeypatch):
    """Closing without Save leaves the persisted settings transaction unchanged."""
    from ui.settings_panel.dialog import SettingsDialog, _get

    env_path = tmp_path / "settings.env"
    env_path.write_text("BUBBLE_WIDTH=340\n", encoding="utf-8")
    _isolate_settings_env(monkeypatch, env_path)
    dialog = SettingsDialog()
    reopened = None
    try:
        dialog._fields["BUBBLE_WIDTH"].setText("612")
        qapp.processEvents()
        assert _get(dialog._fields["BUBBLE_WIDTH"]) == "612"
        dialog.reject()

        reopened = SettingsDialog()
        assert _get(reopened._fields["BUBBLE_WIDTH"]) == "340"
        assert env_path.read_text(encoding="utf-8") == "BUBBLE_WIDTH=340\n"
    finally:
        dialog.close()
        dialog.deleteLater()
        if reopened is not None:
            reopened.close()
            reopened.deleteLater()
        qapp.processEvents()


def test_settings_cancel_failure_matrix_is_transactional(qapp, tmp_path, monkeypatch):
    """Missed dirtiness, stale snapshots, previews, and active children stay safe."""
    from PySide6.QtWidgets import QMessageBox

    from ui.settings_panel.dialog import SettingsDialog

    env_path = tmp_path / "settings.env"
    env_path.write_text("BUBBLE_WIDTH=340\n", encoding="utf-8")
    _isolate_settings_env(monkeypatch, env_path)
    applied: list[object] = []
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    # Editing a previewable control does not touch the runtime before Save,
    # even if dirty tracking itself were to miss the control.
    dialog = SettingsDialog(on_apply=lambda payload=None: applied.append(payload))
    try:
        dialog.show()
        dialog._fields["BUBBLE_WIDTH"].setText("612")
        qapp.processEvents()
        assert applied == []
        dialog._dirty_keys.clear()  # inject missed dirty-state tracking
        monkeypatch.setattr(
            dialog,
            "_snapshot_settings",
            lambda: (_ for _ in ()).throw(RuntimeError("stale snapshot")),
        )
        dialog.reject()
        qapp.processEvents()
        assert not dialog.isVisible()
        assert env_path.read_text(encoding="utf-8") == "BUBBLE_WIDTH=340\n"
    finally:
        dialog.deleteLater()
        qapp.processEvents()

    class Installer:
        exit_code = None

    class Wizard:
        visible = True

        def isVisible(self):
            return self.visible

    guarded = SettingsDialog()
    installer = Installer()
    wizard = Wizard()
    try:
        guarded.show()
        guarded._optional_install_dialogs = [installer]
        guarded.reject()
        assert guarded.isVisible()
        assert "installer" in warnings[-1][1]

        installer.exit_code = 0
        guarded._profile_setup_wizard = wizard
        guarded.reject()
        assert guarded.isVisible()
        assert "setup wizard" in warnings[-1][1]

        wizard.visible = False
        guarded.reject()
        qapp.processEvents()
        assert not guarded.isVisible()
        assert env_path.read_text(encoding="utf-8") == "BUBBLE_WIDTH=340\n"
    finally:
        guarded.deleteLater()
        qapp.processEvents()


def test_settings_failed_live_reload_does_not_claim_restart_applied(qapp, tmp_path, monkeypatch):
    """A failed live reload is surfaced instead of reporting that settings took effect."""
    from PySide6.QtWidgets import QMessageBox

    import config
    from ui.settings_panel.dialog import SettingsDialog

    env_path = tmp_path / "settings.env"
    env_path.write_text("BUBBLE_WIDTH=340\n", encoding="utf-8")
    _isolate_settings_env(monkeypatch, env_path)
    warnings = []
    dialog = SettingsDialog()
    try:
        dialog._do_save = lambda: True
        monkeypatch.setattr(
            config,
            "reload",
            lambda: (_ for _ in ()).throw(RuntimeError("application restart did not occur")),
        )
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda _parent, title, message: warnings.append((title, message)),
        )

        assert dialog._apply_settings() is False
        assert "application restart did not occur" in warnings[0][1]
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_invalid_settings_values_use_safe_widget_and_runtime_fallbacks(qapp, monkeypatch):
    """Invalid generic/language/appearance/numeric values do not become active UI state."""
    from core.system.env_utils import env_int
    from ui.i18n import _normalize_language
    from ui.settings_panel.dialog import SettingsDialog, _get, _set

    dialog = SettingsDialog()
    try:
        language_before = _get(dialog._fields["APP_LANGUAGE"])
        appearance_before = _get(dialog._fields["THEME_MODE"])
        _set(dialog._fields["APP_LANGUAGE"], "not-a-language")
        _set(dialog._fields["THEME_MODE"], "not-an-appearance")

        assert _normalize_language("not-a-language") == "en"
        assert _get(dialog._fields["APP_LANGUAGE"]) == language_before
        assert _get(dialog._fields["THEME_MODE"]) == appearance_before
        monkeypatch.setenv("WISP_TEST_INVALID_NUMBER", "not-a-number")
        assert env_int("WISP_TEST_INVALID_NUMBER", 340) == 340
        assert dialog._set_value_for_env_key("NOT_A_SETTING", "bad") is False
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
