"""Tests for hotkey capture widgets."""

import os
import sys

import pytest


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_hotkey_capture_escape_clears_binding():
    """Verify Escape clears a binding while recording instead of becoming one."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.hotkey_capture import HotkeyCaptureEdit

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
