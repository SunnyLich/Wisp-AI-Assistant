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
def test_notice_bubble_close_is_dismiss_only():
    """Informational notices should not route their close button to backend stop."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()
    stop_calls = []
    bubble.set_stop_callback(lambda: stop_calls.append("stop"))

    def close_release() -> QMouseEvent:
        point = QPointF(bubble._close_rect().center())
        return QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            point,
            point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

    try:
        bubble.show_notice("Warming up local voice...", timeout_ms=0)
        assert bubble._close_cancels is False
        bubble._close_pressed = True
        bubble.mouseReleaseEvent(close_release())
        assert stop_calls == []

        bubble.show_reading("read this aloud")
        assert bubble._close_cancels is True
        bubble._close_pressed = True
        bubble.mouseReleaseEvent(close_release())
        assert stop_calls == ["stop"]

        bubble.show_notice("Another notice", timeout_ms=0)
        bubble.append_chunk("Actual reply")
        assert bubble._close_cancels is True
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_fast_forward_button_controls_speed_boost():
    """Holding the fast-forward button should toggle bubble speed boost."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()
    speed_events: list[bool] = []
    bubble.set_speed_callback(speed_events.append)

    try:
        bubble.append_chunk("hello world")
        app.processEvents()

        assert bubble._fast_forward_enabled() is True
        pos = bubble._fast_forward_rect().center()

        QTest.mousePress(bubble, Qt.MouseButton.LeftButton, pos=pos)
        assert bubble._speed_boosting is True
        assert speed_events == [True]

        QTest.mouseRelease(bubble, Qt.MouseButton.LeftButton, pos=pos)
        assert bubble._speed_boosting is False
        assert speed_events == [True, False]
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_holding_bubble_body_no_longer_controls_speed_boost():
    """The bubble body should remain available for drag/click instead of speed control."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()
    speed_events: list[bool] = []
    bubble.set_speed_callback(speed_events.append)

    try:
        bubble.append_chunk("hello world")
        app.processEvents()

        pos = QPoint(24, 24)
        assert not bubble._fast_forward_rect().contains(pos)
        assert not bubble._close_rect().contains(pos)

        QTest.mousePress(bubble, Qt.MouseButton.LeftButton, pos=pos)
        QTest.mouseRelease(bubble, Qt.MouseButton.LeftButton, pos=pos)

        assert bubble._speed_boosting is False
        assert speed_events == []
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_bubble_document_view_is_selectable_and_avoids_controls():
    """The full document widget should live only inside the bubble text viewport."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        bubble.append_chunk("one two three four five six seven eight")
        app.processEvents()

        view = bubble._text_view
        assert view.isVisible()
        assert view.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
        assert "one two three" in view.toPlainText()
        assert "\n".join(bubble._lines).strip() in view.toPlainText()
        assert not view.geometry().intersects(bubble._close_rect())
        assert not view.geometry().intersects(bubble._fast_forward_rect())

        bubble.show_notice("Choose an action", timeout_ms=0, actions=[("Open", lambda: None)])
        app.processEvents()

        assert view.isVisible()
        assert bubble._action_rects
        assert not view.geometry().intersects(bubble._action_rects[0])
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_document_view_keeps_legacy_bubble_text_width():
    """Selectable document styling must not change the bubble text wrapping budget."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble, _CLOSE_SIZE, _PAD

    app = QApplication.instance() or QApplication(sys.argv)
    old_width = getattr(config, "BUBBLE_WIDTH", 340)
    config.BUBBLE_WIDTH = 420
    bubble = SpeechBubble()

    try:
        assert bubble._text_w == config.BUBBLE_WIDTH - _PAD * 2 - _CLOSE_SIZE
        bubble.append_chunk("style changes should not change the text model")

        assert bubble._full_text == "style changes should not change the text model"
        assert bubble._visible_plain_text() == "\n".join(bubble._lines).strip()
        assert not bubble._text_view.geometry().intersects(bubble._close_rect())
        assert not bubble._text_view.geometry().intersects(bubble._fast_forward_rect())
    finally:
        config.BUBBLE_WIDTH = old_width
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_bubble_document_view_renders_addon_reply_annotations():
    """Addon reply annotations should render safe HTML-style tag/style attributes."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        bubble.append_chunk(
            "bubble chat",
            annotations=[
                {
                    "start": 0,
                    "end": 6,
                    "tag": "mark",
                    "style": "background-color:#12abef; position:absolute",
                    "tooltip": "Addon picked this style",
                    "id": "custom-reply-style",
                }
            ],
        )
        app.processEvents()

        html = bubble._text_view.toHtml()
        assert "#12abef" in html
        assert "position:absolute" not in html
        assert any(item.tooltip == "Addon picked this style" for item in bubble._text_view_tooltips)
        assert "bubble chat" in bubble._text_view.toPlainText()
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_document_view_keeps_full_text_and_selection_on_update():
    """The selectable bubble text should behave like a live document viewport."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QTextCursor
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
        bubble.append_chunk("one two three four five six seven eight")
        app.processEvents()

        visible_text = "\n".join(bubble._lines).strip()
        document_text = bubble._text_view.toPlainText()

        assert visible_text
        assert visible_text in document_text
        assert "one two three" in document_text
        assert document_text != visible_text

        doc = bubble._text_view.document()
        cursor = QTextCursor(doc)
        start = document_text.index("two")
        cursor.setPosition(start)
        cursor.setPosition(start + len("two three"), QTextCursor.MoveMode.KeepAnchor)
        bubble._text_view.setTextCursor(cursor)

        bubble._advance_highlight()
        app.processEvents()

        selected = bubble._text_view.textCursor().selectedText().replace("\u2029", "\n")
        assert selected == "two three"
        assert "one two three" in bubble._text_view.toPlainText()
    finally:
        config.BUBBLE_LINES = old_lines
        config.BUBBLE_WIDTH = old_width
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_bubble_text_view_click_opens_chat_without_speed_boost():
    """Simple text-area clicks should keep old open-chat behavior without speeding up."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()
    opened: list[bool] = []
    speed_events: list[bool] = []
    bubble.set_click_callback(lambda: opened.append(True))
    bubble.set_speed_callback(speed_events.append)

    try:
        bubble.append_chunk("click this text")
        app.processEvents()

        QTest.mouseClick(bubble._text_view, Qt.MouseButton.LeftButton, pos=bubble._text_view.rect().center())

        assert opened == [True]
        assert speed_events == []
        assert bubble._speed_boosting is False
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_document_view_wheel_moves_bubble_viewport():
    """Wheel events over document text should move the bubble viewport, not replace the document."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    class _Delta:
        def __init__(self, value: int):
            self._value = value

        def y(self) -> int:
            return self._value

    class _WheelEvent:
        def __init__(self, value: int):
            self.accepted = False
            self._value = value

        def angleDelta(self) -> _Delta:
            return _Delta(self._value)

        def pixelDelta(self) -> _Delta:
            return _Delta(0)

        def accept(self) -> None:
            self.accepted = True

    app = QApplication.instance() or QApplication(sys.argv)
    old_lines = getattr(config, "BUBBLE_LINES", 3)
    old_scroll = getattr(config, "BUBBLE_SCROLL_ENABLED", True)
    old_snap = getattr(config, "BUBBLE_SCROLL_SNAP_ENABLED", True)
    config.BUBBLE_LINES = 2
    config.BUBBLE_SCROLL_ENABLED = True
    config.BUBBLE_SCROLL_SNAP_ENABLED = False
    bubble = SpeechBubble()

    try:
        bubble._all_line_segments = [
            [(word, False, idx, False, True)]
            for idx, word in enumerate(["one", "two", "three", "four"])
        ]
        bubble._revealed_count = 1
        bubble._apply_visible_lines()
        before_document = bubble._text_view.toPlainText()

        event = _WheelEvent(-120)
        bubble._text_view.wheelEvent(event)

        assert event.accepted is True
        assert bubble._lines == ["two", "three"]
        assert bubble._text_view.toPlainText() == before_document
    finally:
        config.BUBBLE_LINES = old_lines
        config.BUBBLE_SCROLL_ENABLED = old_scroll
        config.BUBBLE_SCROLL_SNAP_ENABLED = old_snap
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_reading_prefix_is_not_counted_as_spoken_highlight():
    """The read-aloud label should display without becoming spoken/highlighted text."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = ""
    bubble = SpeechBubble()
    highlights: list[tuple[str, int, bool]] = []
    bubble.set_highlight_callback(lambda text, count, finished: highlights.append((text, count, finished)))

    try:
        bubble.show_reading("read this aloud")

        assert bubble._full_text == "Reading: read this aloud"
        assert bubble._pending_words == ["read", "this", "aloud"]
        assert bubble._revealed_count == 0

        bubble.start_word_reveal()
        bubble._advance_highlight()

        highlighted_words = [
            word
            for line in bubble._all_line_segments
            for word, _bold, reply_idx, is_thought, _space_before in line
            if (
                not is_thought
                and reply_idx is not None
                and reply_idx >= bubble._highlight_index_offset
                and reply_idx < bubble._highlight_index_offset + bubble._revealed_count
            )
        ]

        assert bubble._full_text == "Reading: read this aloud"
        assert highlights[-1] == ("read this aloud", 1, False)
        assert "Reading:" not in highlighted_words
        assert highlighted_words == ["read"]
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
        r"`C:\Users\TestUser\Documents\ExampleProject\model_files`"
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
def test_pending_speech_stops_following_latest_stream_text():
    """Verify queued TTS anchors the bubble before audio playback starts."""
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
        bubble.append_chunk("six seven eight nine ten eleven ")
        assert "eleven" in " ".join(bubble._lines)

        bubble.start_speech_tracking()
        assert "one" in " ".join(bubble._lines)

        bubble.append_chunk("twelve thirteen fourteen fifteen")
        visible = " ".join(bubble._lines)
        assert "one" in visible
        assert "fifteen" not in visible
    finally:
        config.BUBBLE_LINES = old_lines
        config.BUBBLE_WIDTH = old_width
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_bubble_preserves_explicit_newlines_and_blank_lines():
    """Verify model paragraphs remain visible as separate bubble lines."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_lines = getattr(config, "BUBBLE_LINES", 3)
    old_width = getattr(config, "BUBBLE_WIDTH", 340)
    config.BUBBLE_LINES = 5
    config.BUBBLE_WIDTH = 520
    bubble = SpeechBubble()

    try:
        bubble.append_chunk("First paragraph.\nSecond line.\n\nNew paragraph.")

        assert bubble._lines[:4] == [
            "First paragraph.",
            "Second line.",
            "",
            "New paragraph.",
        ]
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
        r"C:\Users\TestUser\Documents\ExampleProject\model_files"
    )

    assert len(units) > 1
    assert any(unit.endswith("\\") for unit in units)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_cjk_reply_stream_reveals_character_by_character():
    """Verify Chinese replies do not highlight an entire sentence as one word."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        bubble.append_chunk("你好世界")
        bubble._advance_highlight()
        bubble._rewrap()

        reply_indexes = [
            reply_idx
            for line in bubble._all_line_segments
            for _word, _bold, reply_idx, is_thought, _space_before in line
            if not is_thought and reply_idx is not None
        ]

        assert bubble._pending_words == ["你", "好", "世", "界"]
        assert bubble._full_text == "你好世界"
        assert bubble._revealed_count == 1
        assert reply_indexes == [0, 1, 2, 3]
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_cjk_timestamp_words_are_split_for_reveal():
    """Verify provider timestamps that group Chinese text are split locally."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        bubble._audio_started = True
        bubble._timestamp_mode = True
        bubble._audio_elapsed.start()

        bubble._advance_highlight("你好")

        assert bubble._pending_words == ["你", "好"]
        assert bubble._full_text == "你好"
        assert bubble._revealed_count == 1
    finally:
        bubble.deleteLater()
        app.processEvents()


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


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_manual_wheel_scroll_repaints_immediately():
    """Verify wheel scrolling does not wait for the next speech/highlight tick to repaint."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    class _Delta:
        def __init__(self, value: int):
            self._value = value

        def y(self) -> int:
            return self._value

    class _WheelEvent:
        def __init__(self, value: int):
            self.accepted = False
            self._value = value

        def angleDelta(self) -> _Delta:
            return _Delta(self._value)

        def pixelDelta(self) -> _Delta:
            return _Delta(0)

        def accept(self) -> None:
            self.accepted = True

    class _TestBubble(SpeechBubble):
        def __init__(self):
            self.update_count = 0
            super().__init__()

        def update(self, *args, **kwargs):  # noqa: N802 - Qt override
            self.update_count += 1
            super().update(*args, **kwargs)

    app = QApplication.instance() or QApplication(sys.argv)
    old_lines = getattr(config, "BUBBLE_LINES", 3)
    old_scroll = getattr(config, "BUBBLE_SCROLL_ENABLED", True)
    old_snap = getattr(config, "BUBBLE_SCROLL_SNAP_ENABLED", True)
    config.BUBBLE_LINES = 2
    config.BUBBLE_SCROLL_ENABLED = True
    config.BUBBLE_SCROLL_SNAP_ENABLED = False
    bubble = _TestBubble()

    try:
        bubble._all_line_segments = [
            [(word, False, idx, False, True)]
            for idx, word in enumerate(["one", "two", "three", "four"])
        ]
        bubble._revealed_count = 1
        bubble._apply_visible_lines()
        before_updates = bubble.update_count

        event = _WheelEvent(-120)
        bubble.wheelEvent(event)

        assert event.accepted is True
        assert bubble._lines == ["two", "three"]
        assert bubble.update_count == before_updates + 1
    finally:
        config.BUBBLE_LINES = old_lines
        config.BUBBLE_SCROLL_ENABLED = old_scroll
        config.BUBBLE_SCROLL_SNAP_ENABLED = old_snap
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_transient_bubble_clicks_do_not_open_chat():
    """Verify recording/thinking bubbles are not chat-open click targets."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()
    opened: list[bool] = []
    bubble.set_click_callback(lambda: opened.append(True))

    try:
        bubble.show_listening("Recording - release to send")
        app.processEvents()
        QTest.mouseClick(bubble, Qt.MouseButton.LeftButton, pos=bubble.rect().center())
        assert opened == []

        bubble.start_thinking()
        app.processEvents()
        QTest.mouseClick(bubble, Qt.MouseButton.LeftButton, pos=bubble.rect().center())
        assert opened == []

        bubble.append_chunk("Done.")
        app.processEvents()
        QTest.mouseClick(bubble, Qt.MouseButton.LeftButton, pos=bubble.rect().center())
        assert opened == [True]
    finally:
        bubble.deleteLater()
        app.processEvents()
