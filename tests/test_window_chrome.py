"""Window chrome coverage for top-level app surfaces."""

from __future__ import annotations

import gc
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QPushButton, QWidget


def test_text_popup_uses_standard_window_controls():
    """The full-reply popup should have the same titlebar buttons as app dialogs."""
    from ui.popup import TextPopup

    app = QApplication.instance() or QApplication(sys.argv)
    popup = TextPopup("hello")
    try:
        popup.show()
        app.processEvents()

        titlebar = popup.findChild(QWidget, "wispTitleBar")
        buttons = popup.findChildren(QPushButton)

        assert titlebar is not None
        assert {button.property("winbtn") for button in buttons} >= {"min", "max", "close"}
    finally:
        popup.close()
        popup.deleteLater()
        app.processEvents()


def test_window_chrome_survives_change_events_after_gc():
    """Window chrome event filter should keep its Python state alive."""
    from ui.shared.framed import install_window_chrome

    app = QApplication.instance() or QApplication(sys.argv)
    window = QWidget()
    try:
        install_window_chrome(window)
        assert getattr(window, "_wisp_window_chrome", None) is not None

        gc.collect()
        window.show()
        app.processEvents()
        for event_type in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.Resize,
        ):
            app.sendEvent(window, QEvent(event_type))
        app.processEvents()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_shared_qt_surface_geometry_destruction_and_wm_rejection_contract(monkeypatch):
    """Top-level surfaces recover stale geometry and contain window-manager refusal."""
    from core import platform_utils
    from ui.popup import TextPopup
    from ui.shared import window_utils

    app = QApplication.instance() or QApplication(sys.argv)
    screen = QApplication.primaryScreen().availableGeometry()
    popup = TextPopup("runtime surface")
    replacement = None
    try:
        popup.resize(screen.width() * 2, screen.height() * 2)
        popup.move(screen.right() + 5000, screen.bottom() + 5000)
        window_utils.fit_window_to_screen(
            popup,
            preferred_width=screen.width() * 2,
            preferred_height=screen.height() * 2,
        )
        assert screen.contains(popup.frameGeometry().topLeft())
        assert popup.width() <= screen.width()
        assert popup.height() <= screen.height()

        popup.close()
        popup.deleteLater()
        app.processEvents()
        replacement = TextPopup("replacement after destroyed widget")
        replacement.show()
        app.processEvents()
        assert replacement.isVisible()

        class RejectingHandle:
            def startSystemMove(self):
                raise RuntimeError("window manager rejects the behavior")

        class RejectingWindow:
            def windowHandle(self):
                return RejectingHandle()

        monkeypatch.setattr(window_utils, "is_wayland", lambda: True)
        assert window_utils.start_wayland_system_move(RejectingWindow()) is False

        class RejectingNativeWidget:
            def winId(self):
                raise RuntimeError("native window was destroyed")

        monkeypatch.setattr(platform_utils, "IS_MAC", True)
        monkeypatch.setattr(platform_utils, "_qt_platform_is_cocoa", lambda: True)
        platform_utils.keep_overlay_visible_across_apps(RejectingNativeWidget())
    finally:
        if replacement is not None:
            replacement.close()
            replacement.deleteLater()
        popup.close()
        popup.deleteLater()
        app.processEvents()
