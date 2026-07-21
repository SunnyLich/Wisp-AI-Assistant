"""Tests for test bubble transcript."""

import base64
import os
import sys

import pytest


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_generated_image_is_scaled_into_the_speech_bubble(tmp_path):
    """Large generated images become bounded thumbnails instead of disappearing."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    image_path = tmp_path / "large-generated.png"
    source = QImage(1600, 1200, QImage.Format.Format_ARGB32)
    source.fill(Qt.GlobalColor.magenta)
    assert source.save(str(image_path), "PNG")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    bubble = SpeechBubble()

    try:
        base_height = bubble.height()
        bubble.show_progress("Image generated.")

        assert bubble.show_image(encoded) is True
        thumbnail = bubble._image_label.pixmap()
        assert thumbnail is not None and not thumbnail.isNull()
        assert thumbnail.width() <= bubble._text_w
        assert thumbnail.height() <= 300
        assert bubble.height() > base_height
        assert bubble._image_label.isVisible()
        assert bubble._full_text == ""
        assert bubble._can_open_chat_from_click() is True

        bubble.clear()
        assert bubble._image_label.isHidden()
        assert bubble.height() == base_height
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_transcript_preview_is_replaced_by_first_reply_chunk():
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
def test_timed_reveal_invalid_config_and_finish_reset_are_safe(monkeypatch):
    """Invalid WPM and speech completion cannot strand the fallback reveal timer."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(config, "BUBBLE_REVEAL_WPM", "not-a-number", raising=False)
    bubble = SpeechBubble()

    try:
        bubble.append_chunk("one two")
        assert bubble._current_reveal_wpm() == 170
        assert bubble._reveal_timer.interval() >= 1

        bubble._revealed_count = len(bubble._pending_words)
        bubble.finish()

        assert bubble._reveal_timer.isActive() is False
        assert bubble._reveal_mode is False
        assert bubble._timestamp_mode is False
        assert bubble._finishing is False
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_bubble_runtime_callback_timer_and_invalid_render_matrix_is_contained(monkeypatch):
    """Consumer, speech, timer, stale-state, and invalid-render faults stay in Qt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    def fail(*_args):
        raise RuntimeError("runtime callback failed")

    try:
        bubble.set_anchor_callback(fail)
        bubble.set_hide_callback(fail)
        bubble.set_highlight_callback(fail)
        bubble.set_speed_callback(fail)
        bubble.set_click_callback(fail)
        bubble.set_companion_callback(fail)
        bubble.set_stop_callback(fail)

        bubble.show()
        app.processEvents()
        bubble._emit_highlight()
        bubble._set_speed_boost(True)
        bubble.append_chunk("reply remains usable")
        bubble._text_view_clicked()
        bubble._invoke_runtime_callback("stop", bubble._stop_callback)
        bubble._system_move_active = True
        bubble.move(bubble.pos() + QPoint(1, 1))
        app.processEvents()
        bubble.hide()
        app.processEvents()

        for wrapper, target_name in (
            (bubble._on_dot_timer, "_tick_dots"),
            (bubble._on_hide_timer, "hide"),
            (bubble._on_reveal_timer, "_reveal_next_word"),
            (bubble._on_scroll_snap_timer, "_snap_scroll_to_highlight"),
        ):
            with monkeypatch.context() as scoped:
                scoped.setattr(bubble, target_name, fail)
                wrapper()

        # Invalid IPC render payloads are ignored rather than reaching string
        # methods inside a Qt callback.
        bubble.append_chunk({"invalid": "chunk"})
        bubble.show_transcript({"invalid": "transcript"})
        bubble.show_labeled_text({"bad": "label"}, {"bad": "body"})
        bubble.show_progress(["bad", "progress"])

        # A later valid operation proves the surface remains usable.
        bubble.set_anchor_callback(None)
        bubble.set_hide_callback(None)
        bubble.show_notice("still alive", timeout_ms=0)
        assert "still alive" in bubble._full_text
    finally:
        bubble.clear()
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_progress_preview_is_replaced_by_first_reasoning_summary():
    """A live Codex thought must replace the temporary working status."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        bubble.show_progress("Codex is working...")
        assert bubble._transcript_preview is True

        bubble.append_chunk("Inspecting constraints", is_thought=True)

        assert bubble._transcript_preview is False
        assert bubble._thought_text == "Inspecting constraints"
        assert "Codex is working" not in bubble._full_text
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_first_chatgpt_activity_does_not_reserve_an_empty_top_line():
    """A newline-delimited first harness activity should begin on row one."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        bubble.show_progress("Model is thinking...")
        bubble.append_chunk("\nRunning: rg\n", is_thought=True)

        assert bubble._thought_text == "Running: rg\n"
        assert bubble._lines[0] == "Running: rg"
        assert bubble._text_view.toPlainText().startswith("Running: rg")
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_thoughts_tools_and_reply_blocks_keep_stream_arrival_order():
    """Later tool activity must remain between the reply blocks around it."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_lines = getattr(config, "BUBBLE_LINES", 4)
    old_width = getattr(config, "BUBBLE_WIDTH", 340)
    config.BUBBLE_LINES = 8
    config.BUBBLE_WIDTH = 600
    bubble = SpeechBubble()

    try:
        bubble.append_chunk("Planning", is_thought=True)
        bubble.append_chunk("First answer")
        bubble.append_chunk("\nRunning: rg\n", is_thought=True)
        bubble.append_chunk("Second answer")

        assert bubble._lines == ["Planning", "First answer", "Running: rg", "Second answer"]
        assert bubble._full_text == "First answer\nSecond answer"
    finally:
        config.BUBBLE_LINES = old_lines
        config.BUBBLE_WIDTH = old_width
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_token_deltas_in_the_same_reply_block_still_join_without_forced_newlines():
    """Chronological blocks must not turn normal token streaming into one line per token."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        bubble.append_chunk("Hello")
        bubble.append_chunk(", world")

        assert bubble._full_text == "Hello, world"
        assert bubble._display_segments == [("Hello, world", False)]
        assert bubble._lines[0] == "Hello, world"
    finally:
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
def test_long_notice_expands_from_top_and_gets_more_read_time():
    """Long notices should not open scrolled to the final line."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_lines = getattr(config, "BUBBLE_LINES", 4)
    old_width = getattr(config, "BUBBLE_WIDTH", 340)
    config.BUBBLE_LINES = 2
    config.BUBBLE_WIDTH = 260
    bubble = SpeechBubble()
    text = (
        "This notice has important setup context before the warning. "
        "The middle explains what changed and why the user should care. "
        "The final sentence should not be the only visible part."
    )

    try:
        base_height = bubble._base_bubble_h()
        bubble.show_notice(text, timeout_ms=5000)

        visible = " ".join(bubble._lines)
        assert bubble._visible_line_count() > 2
        assert bubble._bubble_h > base_height
        assert "important setup context" in visible
        assert bubble._visible_start_line == 0
        assert bubble._hide_timer.interval() > 5000
    finally:
        config.BUBBLE_LINES = old_lines
        config.BUBBLE_WIDTH = old_width
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_notice_compacts_blank_paragraph_gaps():
    """Static notices should not spend bubble height on empty paragraph gaps."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        bubble.show_notice("First warning.\n\n\nSecond sentence.\n   \nThird sentence.", timeout_ms=0)

        assert bubble._full_text == "First warning.\nSecond sentence.\nThird sentence."
        assert "" not in bubble._lines
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tagged_warning_notice_does_not_auto_hide():
    """Explicit warning notices stay visible until the user dismisses them."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        bubble.show_notice("Warning: global hotkeys did not start.", severity="warning")

        assert bubble.isVisible()
        assert not bubble._hide_timer.isActive()
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
def test_fast_forward_button_uses_bubble_colors():
    """The speed control should visually belong to the bubble, not use a blue badge."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()
    try:
        bubble._bubble_color = QColor("#f4f0df")
        bubble._text_color = QColor("#1b1b1b")

        bg, border, text = bubble._fast_forward_colors(pressed=False, hovered=False)

        assert bg.name().lower() == "#f4f0df"
        assert border.name().lower() == "#1b1b1b"
        assert text.name().lower() == "#1b1b1b"
        assert bg.name().lower() != "#4da3ff"
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
    from ui.bubble import _CLOSE_SIZE, _PAD, SpeechBubble

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
def test_document_view_has_room_for_the_last_line_box():
    """The invisible document viewport must not clip the last configured row."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        expected_height = bubble._line_h * max(1, int(config.BUBBLE_LINES))
        assert bubble._text_view.height() == expected_height
        assert bubble._text_view.geometry().bottom() < bubble._bubble_h
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_clicking_bubble_permanently_disables_auto_hide_for_current_session():
    """A deliberate click should keep the current bubble open after hover ends."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        bubble.append_chunk("keep this reply open")
        bubble.finish(flush_remaining=True)
        assert bubble._hide_timer.isActive()

        QTest.mouseClick(bubble._text_view, Qt.MouseButton.LeftButton, pos=bubble._text_view.rect().center())
        assert bubble._user_engaged is True
        assert not bubble._hide_timer.isActive()

        bubble._start_hide_timer()
        assert not bubble._hide_timer.isActive()
    finally:
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_wheel_interaction_disables_auto_hide_but_hover_does_not():
    """Wheel input latches the bubble open; a hover hold remains temporary."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble

    class _Delta:
        def y(self) -> int:
            return -120

    class _WheelEvent:
        def angleDelta(self) -> _Delta:
            return _Delta()

        def pixelDelta(self) -> _Delta:
            return _Delta()

        def accept(self) -> None:
            pass

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()

    try:
        bubble._all_line_segments = [[("reply", False, 0, False, False)]]
        bubble._start_hide_timer()
        bubble._pause_auto_hide()  # hover only
        bubble._resume_auto_hide()
        assert bubble._user_engaged is False
        assert bubble._hide_timer.isActive()

        bubble.wheelEvent(_WheelEvent())
        assert bubble._user_engaged is True
        assert not bubble._hide_timer.isActive()
    finally:
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
        assert "Addon picked this style" not in html
        assert "title=" not in html
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
def test_compact_table_highlight_reaches_final_visible_word():
    """Visible table formatting must not leave trailing reply words unread."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.bubble import SpeechBubble, _css_color

    app = QApplication.instance() or QApplication(sys.argv)
    bubble = SpeechBubble()
    text = (
        "|Model|Result|\n"
        "|---|---|\n"
        "|1-bit|Usually faster/easier|\n\n"
        "On this hardware a 4B model will typically generate faster."
    )

    try:
        bubble.append_chunk(text)
        visible_indexes = [
            reply_idx
            for line in bubble._all_line_segments
            for _word, _bold, reply_idx, is_thought, _space_before in line
            if not is_thought and reply_idx is not None
        ]
        assert max(visible_indexes) + 1 > len(bubble._pending_words)

        bubble.finish(flush_remaining=True)

        accent = _css_color(bubble._read_word_color)
        rendered = bubble._line_segments_html(bubble._all_line_segments)
        assert f'<span style="color:{accent}">faster.</span>' in rendered
        assert bubble._visible_highlight_end(bubble._all_line_segments) == max(visible_indexes) + 1
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
def test_read_highlight_stays_in_middle_of_bubble_while_following():
    """The live read position should be centered instead of pinned to the bottom row."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_lines = getattr(config, "BUBBLE_LINES", 3)
    config.BUBBLE_LINES = 5
    bubble = SpeechBubble()

    try:
        words = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
        bubble._pending_words = words
        bubble._revealed_count = 6
        bubble._all_line_segments = [
            [(word, False, idx, False, True)]
            for idx, word in enumerate(words)
        ]

        bubble._apply_visible_lines()

        assert bubble._visible_start_line == 3
        assert bubble._lines == ["four", "five", "six", "seven", "eight"]
    finally:
        config.BUBBLE_LINES = old_lines
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


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_live_captions_interleave_roles_with_prefixed_lines():
    """Live voice captions label speaker turns and reveal instantly."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = ""
    bubble = SpeechBubble()

    try:
        bubble.set_live_mode(True)
        bubble.append_live_transcript("user", "hello ")
        bubble.append_live_transcript("user", "there ")
        bubble.append_live_transcript("assistant", "hi, how can ")
        bubble.append_live_transcript("assistant", "I help?")
        bubble.append_live_transcript("user", "never mind")  # barge-in

        assert bubble._full_text == (
            "You ▸ hello there \nWisp ▸ hi, how can I help?\nYou ▸ never mind"
        )
        # captions reveal instantly, not at reading WPM
        assert bubble._revealed_count == len(bubble._pending_words)
        assert not bubble._reveal_timer.isActive()
    finally:
        config.APP_LANGUAGE = old_language
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_live_ready_hint_shows_once_then_captions_follow():
    """The connected hint appears once per session, before any captions."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = ""
    bubble = SpeechBubble()
    hint = "Live voice is ready - speak anytime."

    try:
        bubble.show_live_ready()  # not live yet: ignored
        assert bubble._full_text == ""

        bubble.set_live_mode(True)
        bubble.show_live_ready()
        bubble.show_live_ready()  # duplicate event: ignored

        assert bubble._full_text == hint
        assert bubble._revealed_count == len(bubble._pending_words)

        bubble.append_live_transcript("user", "hello")
        assert bubble._full_text == f"{hint}\nYou ▸ hello"

        bubble.show_live_ready()  # captions running: ignored
        assert bubble._full_text == f"{hint}\nYou ▸ hello"

        # a new session shows the hint again
        bubble.set_live_mode(False)
        bubble.set_live_mode(True)
        bubble.show_live_ready()
        assert bubble._full_text == hint
    finally:
        config.APP_LANGUAGE = old_language
        bubble.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_live_mode_holds_auto_hide_until_deactivated():
    """The caption bubble must not fade out mid-conversation."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.bubble import SpeechBubble

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = ""
    bubble = SpeechBubble()

    try:
        bubble.set_live_mode(True)
        bubble.set_live_mode(True)  # idempotent, no double-hold
        assert bubble._auto_hide_holds == 1

        bubble.append_live_transcript("user", "hello")
        bubble._start_hide_timer()
        assert not bubble._hide_timer.isActive()

        bubble.set_live_mode(False)
        assert bubble._auto_hide_holds == 0
        assert bubble._hide_timer.isActive()  # normal hide countdown resumes
        assert bubble._revealed_count == len(bubble._pending_words)

        # a new session clears the previous conversation's captions
        bubble.set_live_mode(True)
        assert bubble._full_text == ""
        bubble.append_live_transcript("user", "again")
        assert bubble._full_text == "You ▸ again"
    finally:
        config.APP_LANGUAGE = old_language
        bubble.deleteLater()
        app.processEvents()
