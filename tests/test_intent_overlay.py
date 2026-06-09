import os
import sys

import pytest


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_prompt_input_grabs_keyboard_on_windows(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLineEdit

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    grabs: list[QLineEdit] = []
    releases: list[QLineEdit] = []

    def grab_keyboard(self):
        grabs.append(self)

    def release_keyboard(self):
        releases.append(self)

    monkeypatch.setattr(intent_overlay, "_IS_WIN", True)
    monkeypatch.setattr(QLineEdit, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QLineEdit, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._enter_custom_mode()

        assert overlay._input_line.isHidden() is False
        assert grabs == [overlay._input_line]
        assert overlay._input_grabbed_keyboard is True

        overlay._unhook()

        assert releases == [overlay._input_line]
        assert overlay._input_grabbed_keyboard is False
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_key_debug_avoids_prompt_text(monkeypatch, capsys):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_DEBUG_KEYS", True)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._custom_mode = True
        overlay._input_line.show()
        overlay._debug_key(
            "test",
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_A,
                Qt.KeyboardModifier.NoModifier,
                "a",
            ),
        )

        err = capsys.readouterr().err
        assert "[wisp-intent]" in err
        assert "text=printable-len:1" in err
        assert " a " not in err
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()
