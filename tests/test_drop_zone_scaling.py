"""Tests for the icon-adjacent context panel sizing."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")


def test_context_panel_scales_with_icon_size():
    """Verify the context panel and badges scale from the active icon size."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from ui.drop_zone import ContextPanel

    app = QApplication.instance() or QApplication(sys.argv)
    panel = ContextPanel()

    try:
        panel.reposition(QPoint(10, 20), 80)
        panel.add_item("example.txt", "text")
        app.processEvents()

        assert panel.width() == 172
        assert panel.height() == 64
        assert panel._badges[0].width() == 172
        assert panel._badges[0].height() == 28
        assert panel.pos() == QPoint(95, 28)

        panel.reposition(QPoint(10, 20), 160)
        app.processEvents()

        assert panel.width() == 344
        assert panel.height() == 128
        assert panel._badges[0].width() == 344
        assert panel._badges[0].height() == 56
        assert panel.pos() == QPoint(180, 36)
    finally:
        panel.close()
        app.processEvents()
