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
def test_final_only_reply_starts_visible_at_beginning():
    """Verify all-at-once replies show the beginning before reveal advances."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_lines = getattr(config, "BUBBLE_LINES", 3)
    old_width = getattr(config, "BUBBLE_WIDTH", 340)
    config.BUBBLE_LINES = 2
    config.BUBBLE_WIDTH = 520
    bubble = SpeechBubble()
    text = (
        "I don't have the previous file-operation details in this chat, so I can't safely make a change yet. "
        "Please resend the requested change and the target file path under: "
        r"`C:\Users\sunny\Documents\GitHub-CodeBase\Python-AI-assistant-overlay\model_files`"
    )

    try:
        bubble.append_chunk(text)

        visible = " ".join(bubble._lines)
        assert "previous file-operation" in visible
        assert "model_files" not in visible
    finally:
        config.BUBBLE_LINES = old_lines
        config.BUBBLE_WIDTH = old_width
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_multi_chunk_reply_follows_latest_text_before_first_highlight():
    """Verify early chunks can keep the newest text visible before reading starts."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_lines = getattr(config, "BUBBLE_LINES", 3)
    old_width = getattr(config, "BUBBLE_WIDTH", 340)
    config.BUBBLE_LINES = 1
    config.BUBBLE_WIDTH = 260
    bubble = SpeechBubble()

    try:
        bubble.append_chunk("one two three four five ")
        assert bubble._revealed_count == 0

        bubble.append_chunk("six seven eight nine ten eleven")

        visible = " ".join(bubble._lines)
        assert "eleven" in visible
        assert "one two" not in visible
    finally:
        config.BUBBLE_LINES = old_lines
        config.BUBBLE_WIDTH = old_width
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_multi_chunk_reply_follows_read_highlight_once_reveal_starts():
    """Verify later chunks do not pull the bubble away from the read position."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_lines = getattr(config, "BUBBLE_LINES", 3)
    old_width = getattr(config, "BUBBLE_WIDTH", 340)
    config.BUBBLE_LINES = 1
    config.BUBBLE_WIDTH = 260
    bubble = SpeechBubble()

    try:
        bubble.append_chunk("one two three four five ")
        bubble._revealed_count = 1
        bubble._rewrap()
        assert "one" in " ".join(bubble._lines)

        bubble.append_chunk("six seven eight nine ten eleven")

        visible = " ".join(bubble._lines)
        assert "one" in visible
        assert "eleven" not in visible
    finally:
        config.BUBBLE_LINES = old_lines
        config.BUBBLE_WIDTH = old_width
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_windows_paths_are_breakable_bubble_units():
    """Verify path-like words can wrap instead of clipping as one giant word."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ui.bubble import SpeechBubble

    units = SpeechBubble._wrap_units(
        r"C:\Users\sunny\Documents\GitHub-CodeBase\Python-AI-assistant-overlay\model_files"
    )

    assert len(units) > 1
    assert any(unit.endswith("\\") for unit in units)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_bubble_font_size_applies_without_changing_width():
    """Verify bubble text size can change independently from bubble width."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_font_size = getattr(config, "BUBBLE_FONT_SIZE", 10)
    old_width = getattr(config, "BUBBLE_WIDTH", 340)
    config.BUBBLE_FONT_SIZE = 10
    config.BUBBLE_WIDTH = 420
    bubble = SpeechBubble()

    try:
        original_line_h = bubble._line_h
        assert bubble._font.pointSize() == 10

        config.BUBBLE_FONT_SIZE = 16
        bubble.apply_config()

        assert bubble._font.pointSize() == 16
        assert bubble._bold_font.pointSize() == 16
        assert bubble._line_h > original_line_h
        assert bubble._bubble_w == 420
    finally:
        config.BUBBLE_FONT_SIZE = old_font_size
        config.BUBBLE_WIDTH = old_width
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
