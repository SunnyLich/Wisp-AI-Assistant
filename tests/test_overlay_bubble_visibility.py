"""Tests for test overlay bubble visibility."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest


pytestmark = pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")


def test_bubble_chunk_restores_hidden_icon(monkeypatch):
    """Verify bubble chunk restores hidden icon behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.overlay import IconOverlay, OverlaySignals

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda self: None)
    signals = OverlaySignals()
    overlay = IconOverlay(signals)

    try:
        overlay._icon_label.hide()
        app.processEvents()

        signals.bubble_chunk.emit("hello", False)
        app.processEvents()

        assert overlay._icon_label.isVisible()
    finally:
        overlay._bubble.clear()
        overlay._icon_label.close()
        overlay.close()
        app.processEvents()
