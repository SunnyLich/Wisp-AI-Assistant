"""Tests for Qt-free chat reply rendering helpers."""

from __future__ import annotations

from ui.chat_rendering import _assistant_text_to_html


def test_assistant_html_preserves_markdown_blocks():
    """Verify assistant replies render paragraphs, lists, and code blocks."""
    rendered = _assistant_text_to_html(
        "First line\nSecond line\n\n- one\n- two\n\n```py\nprint('hi')\n```"
    )

    assert "First line<br>Second line" in rendered
    assert "<ul>" in rendered
    assert "<li>one</li>" in rendered
    assert "<pre><code>print(&#x27;hi&#x27;)</code></pre>" in rendered


def test_assistant_html_keeps_inline_code_literal():
    """Verify inline markdown does not style text inside code spans."""
    rendered = _assistant_text_to_html("Use `**literal**` and **bold**")

    assert "<code>**literal**</code>" in rendered
    assert "<strong>bold</strong>" in rendered
