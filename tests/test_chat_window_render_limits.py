import os
import sys
import time

import pytest

from ui.chat_window import (
    ChatWindow,
    _CHAT_RENDER_CHAR_LIMIT,
    _truncate_for_display,
    _truncate_segments_for_display,
)


def test_truncate_for_display_caps_large_text():
    text = "x" * (_CHAT_RENDER_CHAR_LIMIT + 50)

    result = _truncate_for_display(text, _CHAT_RENDER_CHAR_LIMIT, "chat display")

    assert len(result) < len(text)
    assert "chat display truncated" in result
    assert "50 chars hidden" in result


def test_truncate_segments_preserves_visible_prefix_and_adds_marker():
    segments = [
        ("thought:" + "a" * 20, True),
        ("reply:" + "b" * 20, False),
    ]

    result = _truncate_segments_for_display(segments, limit=24)

    assert result[0] == ("thought:" + "a" * 16, True)
    assert result[1][1] is False
    assert "chat display truncated" in result[1][0]


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_window_opens_large_last_chat_without_render_freeze():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "messages": [
                {"role": "user", "content": "what can it see?"},
                {"role": "assistant", "content": "tools help\n" + ("x" * 180_000)},
            ],
            "context": "ambient context\n" + ("y" * 180_000),
        }
    ]

    started = time.perf_counter()
    window = ChatWindow(conversations, lambda _messages: iter(()))
    elapsed = time.perf_counter() - started
    try:
        assert elapsed < 1.0
        assert window._built_pages == {0}
        page = window._stack.widget(0)
        assert getattr(page, "_msg_layout", None) is not None
    finally:
        window.close()
        app.processEvents()
