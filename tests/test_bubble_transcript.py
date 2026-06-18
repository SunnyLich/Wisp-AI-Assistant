"""Tests for test bubble transcript."""

import os
import sys

import pytest


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_transcript_preview_is_replaced_by_first_reply_chunk():
    """Verify transcript preview is replaced by first reply chunk behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = ""
    bubble = SpeechBubble()

    try:
        bubble.show_transcript("open the settings")

        assert bubble._transcript_preview is True
        assert bubble._full_text == "Heard: open the settings"

        bubble.append_chunk("Done.")

        assert bubble._transcript_preview is False
        assert bubble._full_text == "Done."
        assert "Heard:" not in bubble._full_text
    finally:
        config.APP_LANGUAGE = old_language
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_manual_scroll_snaps_back_to_highlight_while_speaking():
    """Verify manual bubble scroll returns to the highlighted word during speech."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_lines = getattr(config, "BUBBLE_LINES", 3)
    old_snap = getattr(config, "BUBBLE_SCROLL_SNAP_ENABLED", True)
    config.BUBBLE_LINES = 2
    config.BUBBLE_SCROLL_SNAP_ENABLED = True
    bubble = SpeechBubble()

    try:
        bubble._pending_words = ["one", "two", "three", "four", "five"]
        bubble._revealed_count = 4
        bubble._all_line_segments = [
            [(word, False, idx, False, True)]
            for idx, word in enumerate(bubble._pending_words)
        ]
        bubble._manual_scroll_start = 0
        bubble._apply_visible_lines()

        assert bubble._lines == ["one", "two"]

        bubble._snap_scroll_to_highlight()

        assert bubble._manual_scroll_start is None
        assert bubble._lines == ["three", "four"]
    finally:
        config.BUBBLE_LINES = old_lines
        config.BUBBLE_SCROLL_SNAP_ENABLED = old_snap
        bubble.deleteLater()
        app.processEvents()
