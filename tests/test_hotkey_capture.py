"""Tests for hotkey capture widgets."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_hotkey_capture_escape_clears_binding(monkeypatch):
    """Verify Escape clears a binding while recording instead of becoming one."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    import ui.settings_panel.hotkey_capture as hotkey_capture
    from ui.settings_panel.hotkey_capture import HotkeyCaptureEdit

    monkeypatch.setattr(hotkey_capture._WindowsKeyCaptureHook, "start", lambda self: False)
    monkeypatch.setattr(hotkey_capture._WindowsKeyCaptureHook, "stop", lambda self: None)

    app = QApplication.instance() or QApplication(sys.argv)
    edit = HotkeyCaptureEdit()
    try:
        edit.setText("ctrl+alt+q")
        edit._start_recording()

        edit.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Escape,
                Qt.KeyboardModifier.NoModifier,
            )
        )

        assert edit.text() == ""
        assert edit._recording is False
    finally:
        edit.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_hotkey_capture_accumulates_simultaneous_copilot_chord(monkeypatch):
    """Verify Copilot-style chords are captured even when F23 arrives before modifiers."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.settings_panel.hotkey_capture as hotkey_capture
    from ui.settings_panel.hotkey_capture import HotkeyCaptureEdit

    monkeypatch.setattr(hotkey_capture._WindowsKeyCaptureHook, "start", lambda self: False)
    monkeypatch.setattr(hotkey_capture._WindowsKeyCaptureHook, "stop", lambda self: None)

    app = QApplication.instance() or QApplication(sys.argv)
    edit = HotkeyCaptureEdit()
    try:
        edit._start_recording()

        edit._handle_captured_token("f23", True)
        edit._handle_captured_token("shift", True)
        edit._handle_captured_token("win", True)
        edit._handle_captured_token("f23", False)
        edit._handle_captured_token("shift", False)
        edit._handle_captured_token("win", False)
        edit._commit_pending()

        assert edit.text() == "shift+win+f23"
        assert edit._recording is False
    finally:
        edit.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_hotkey_capture_records_linux_default_caller_chord(monkeypatch):
    """Verify the Qt capture path records the Linux default caller chord."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    import ui.settings_panel.hotkey_capture as hotkey_capture
    from ui.settings_panel.hotkey_capture import HotkeyCaptureEdit

    monkeypatch.setattr(hotkey_capture._WindowsKeyCaptureHook, "start", lambda self: False)
    monkeypatch.setattr(hotkey_capture._WindowsKeyCaptureHook, "stop", lambda self: None)

    app = QApplication.instance() or QApplication(sys.argv)
    edit = HotkeyCaptureEdit()
    ctrl_alt = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    try:
        edit._start_recording()

        edit.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier))
        edit.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Alt, ctrl_alt))
        edit.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, ctrl_alt))
        edit.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, ctrl_alt))
        edit.keyReleaseEvent(
            QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Alt, Qt.KeyboardModifier.ControlModifier)
        )
        edit.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Control, Qt.KeyboardModifier.NoModifier))
        edit._commit_pending()

        assert edit.text() == "ctrl+alt+space"
        assert edit._recording is False
    finally:
        edit.deleteLater()
        app.processEvents()


def test_hotkey_capture_does_not_import_global_hotkey_backend():
    """The settings UI should not import global hotkey listener code to validate edits."""
    source = (ROOT / "ui" / "settings_panel" / "hotkey_capture.py").read_text(encoding="utf-8")

    assert "core.hotkeys" not in source


def test_windows_vk_f23_maps_to_hotkey_token():
    """Verify the Windows Copilot key action code maps to F23."""
    from ui.settings_panel.hotkey_capture import _win_vk_name

    assert _win_vk_name(0x86) == "f23"
