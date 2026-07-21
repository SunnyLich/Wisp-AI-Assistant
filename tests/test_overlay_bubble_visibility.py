"""Tests for test overlay bubble visibility."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")


def test_post_submit_surfaces_show_without_taking_focus(monkeypatch):
    """Thinking UI shown after intent submission must remain non-activating."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ui.overlay import IconOverlay, OverlaySignals

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda self: None)
    overlay = IconOverlay(OverlaySignals())

    try:
        show_without_activating = Qt.WidgetAttribute.WA_ShowWithoutActivating
        assert overlay.testAttribute(show_without_activating)
        assert overlay._icon_label.testAttribute(show_without_activating)
        assert overlay._provider_badge.testAttribute(show_without_activating)
        assert overlay._bubble.testAttribute(show_without_activating)
    finally:
        overlay._bubble.clear()
        overlay._provider_badge.close()
        overlay._icon_label.close()
        overlay.close()
        app.processEvents()


@pytest.mark.parametrize(("mode", "text"), [("codex", "CHATGPT ⚙"), ("claude", "CLAUDE ⚙")])
def test_external_harness_badge_sits_under_icon(monkeypatch, mode: str, text: str):
    """The selected live harness is always visible directly below Wisp's icon."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.overlay import IconOverlay, OverlaySignals

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda self: None)
    opened = []
    monkeypatch.setattr(IconOverlay, "_open_harness_controls", lambda self: opened.append(mode))
    monkeypatch.setattr(config, "CHAT_EXECUTION_MODE", mode, raising=False)
    overlay = IconOverlay(OverlaySignals())

    try:
        app.processEvents()
        assert overlay._provider_badge.text() == text
        assert overlay._provider_badge.width() > overlay._provider_badge.sizeHint().width()
        assert overlay._provider_badge.isVisible() == overlay._icon_label.isVisible()
        assert overlay._provider_badge.y() >= overlay._icon_label.y() + config.ICON_SIZE
        assert "controls" in overlay._provider_badge.toolTip().lower()
        badge_style = overlay._provider_badge.styleSheet()
        assert "background: transparent" in badge_style
        assert "border: none" in badge_style
        assert "border-radius" not in badge_style
        overlay._provider_badge.click()
        assert opened == [mode]

        overlay._icon_label.move(120, 140)
        overlay._on_icon_dragged(overlay._icon_label.pos())
        assert overlay._provider_badge.y() == 140 + config.ICON_SIZE + 2
    finally:
        overlay._bubble.clear()
        overlay._icon_label.close()
        overlay.close()
        app.processEvents()


def test_harness_controls_report_only_their_changed_keys(monkeypatch):
    """Saving provider controls must not look like a full settings/audio reload."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.overlay import IconOverlay, OverlaySignals

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda self: None)
    monkeypatch.setattr(config, "reload", lambda: None)
    signals = OverlaySignals()
    payloads: list[dict] = []
    signals.settings_applied.connect(payloads.append)
    overlay = IconOverlay(signals)

    try:
        keys = ["WISP_CODEX_MODEL", "WISP_CODEX_FAST_MODE"]
        overlay._on_harness_controls_applied(keys)
        app.processEvents()

        assert payloads == [{"changed_keys": keys, "source": "harness_controls"}]
    finally:
        overlay._bubble.clear()
        overlay._icon_label.close()
        overlay.close()
        app.processEvents()


def test_bubble_chunk_restores_hidden_icon(monkeypatch):
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


def test_default_icon_position_reserves_context_panel_space(monkeypatch):
    """Verify default icon placement leaves room for right-side context badges."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.drop_zone import context_panel_reserved_width
    from ui.overlay import IconOverlay, OverlaySignals

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda self: None)
    monkeypatch.setattr(config, "ICON_SIZE", 60, raising=False)
    signals = OverlaySignals()
    overlay = IconOverlay(signals)

    try:
        screen = QApplication.primaryScreen().availableGeometry()
        pos = overlay._icon_label.pos()
        right_edge = pos.x() + config.ICON_SIZE + context_panel_reserved_width(config.ICON_SIZE)

        assert right_edge <= screen.x() + screen.width() - 20
    finally:
        overlay._bubble.clear()
        overlay._icon_label.close()
        overlay.close()
        app.processEvents()


def test_tray_menu_keeps_icon_surface_as_parent_after_rebuild(monkeypatch):
    """Keep the native Wayland popup attached to the visible icon surface."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ui.overlay import IconOverlay, OverlaySignals

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda self: None)
    overlay = IconOverlay(OverlaySignals())

    try:
        assert overlay._tray_menu.parentWidget() is overlay._icon_label
        assert overlay._tray_menu.windowType() == Qt.WindowType.Popup

        overlay.apply_settings()
        assert overlay._tray_menu.parentWidget() is overlay._icon_label
        assert overlay._tray_menu.windowType() == Qt.WindowType.Popup
    finally:
        overlay._bubble.clear()
        overlay._icon_label.close()
        overlay.close()
        app.processEvents()


def test_wayland_icon_uses_compositor_move_and_press_menu(monkeypatch):
    """Wayland requires the press serial for both a drag and an xdg popup."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtWidgets import QApplication

    import ui.overlay as overlay_module
    from ui.overlay import IconOverlay, OverlaySignals

    class MouseEvent:
        def __init__(self, event_type, button, point, buttons=Qt.MouseButton.NoButton):
            self._event_type = event_type
            self._button = button
            self._point = QPointF(point)
            self._buttons = buttons

        def type(self):
            return self._event_type

        def button(self):
            return self._button

        def globalPosition(self):
            return self._point

        def position(self):
            return self._point

        def buttons(self):
            return self._buttons

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda self: None)
    monkeypatch.setattr(overlay_module, "is_wayland", lambda: True)
    moved = []
    monkeypatch.setattr(
        overlay_module,
        "start_wayland_system_move",
        lambda widget: moved.append(widget) or True,
    )
    overlay = IconOverlay(OverlaySignals())
    menu_positions = []
    monkeypatch.setattr(overlay, "_popup_tray_menu", menu_positions.append)

    try:
        press = MouseEvent(QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton, QPoint(20, 20))
        assert overlay.eventFilter(overlay._icon_label, press) is True
        assert moved == [overlay._icon_label]

        right_press = MouseEvent(QEvent.Type.MouseButtonPress, Qt.MouseButton.RightButton, QPoint(25, 30))
        assert overlay.eventFilter(overlay._icon_label, right_press) is True
        assert menu_positions == [QPoint(25, 30)]
    finally:
        overlay._bubble.clear()
        overlay._icon_label.close()
        overlay.close()
        app.processEvents()


def test_tray_surface_failure_matrix_rebuilds_and_dispatches_without_blocking(monkeypatch):
    """Tray actions survive absent native support/menu callbacks/UI consumers."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import time

    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from ui.overlay import IconOverlay, OverlaySignals

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setenv("WISP_MACOS_PY_UI_HOST", "1")
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda self: None)
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    monkeypatch.setattr(QSystemTrayIcon, "show", lambda _self: None)
    quit_calls: list[bool] = []
    monkeypatch.setattr(QApplication, "quit", lambda: quit_calls.append(True))
    signals = OverlaySignals()
    dispatched: list[str] = []
    signals.show_last_chat.connect(lambda: dispatched.append("chat"))
    signals.show_memory_viewer.connect(lambda: dispatched.append("memory"))
    signals.show_settings.connect(lambda: dispatched.append("settings"))
    overlay = IconOverlay(signals)

    def action(label: str):
        return next(item for item in overlay._tray_menu.actions() if item.text() == label)

    try:
        assert overlay._tray is not None
        assert overlay._tray_menu is not None
        action("Last chat").trigger()
        action("Memory").trigger()
        action("Settings").trigger()
        action("Quit").trigger()
        assert dispatched == ["chat", "memory", "settings"]
        assert quit_calls == [True]

        # Rebuilding replaces a missing/destroyed native tray/menu pair and
        # recreates every action callback.
        old_menu = overlay._tray_menu
        old_menu.deleteLater()
        overlay._build_tray()
        app.processEvents()
        assert overlay._tray_menu is not old_menu
        assert {item.text() for item in overlay._tray_menu.actions()} >= {
            "Last chat", "Memory", "Settings", "Quit"
        }

        # With no UI target/callback connected, emitting an action is still an
        # immediate, in-band operation rather than a worker-response wait.
        signals.show_last_chat.disconnect()
        signals.show_memory_viewer.disconnect()
        signals.show_settings.disconnect()
        started = time.perf_counter()
        action("Last chat").trigger()
        action("Memory").trigger()
        action("Settings").trigger()
        assert time.perf_counter() - started < 0.25
    finally:
        overlay._bubble.clear()
        overlay._icon_label.close()
        overlay.close()
        app.processEvents()


@pytest.mark.workflow
def test_tray_menu_omits_health_status(monkeypatch):
    """Verify the right-click tray menu does not expose Health Status."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.i18n import t
    from ui.overlay import IconOverlay, OverlaySignals

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda self: None)
    signals = OverlaySignals()
    overlay = IconOverlay(signals)

    try:
        labels = {action.text() for action in overlay._tray_menu.actions()}
        assert t("Health Status") not in labels
    finally:
        overlay._bubble.clear()
        overlay._icon_label.close()
        overlay.close()
        app.processEvents()


def test_tray_menu_rebuilds_after_language_change(monkeypatch):
    """Verify settings apply rebuilds tray actions in the active language."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n
    from ui.i18n import t
    from ui.overlay import IconOverlay, OverlaySignals

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda self: None)
    monkeypatch.setattr(config, "APP_LANGUAGE", "zh-Hant", raising=False)
    i18n.set_language(app=app)
    signals = OverlaySignals()
    overlay = IconOverlay(signals)

    try:
        labels = {action.text() for action in overlay._tray_menu.actions()}
        settings_label = t("Settings")
        assert settings_label in labels

        monkeypatch.setattr(config, "APP_LANGUAGE", "en", raising=False)
        overlay.apply_settings()
        app.processEvents()

        labels = {action.text() for action in overlay._tray_menu.actions()}
        assert "Settings" in labels
        assert settings_label not in labels
    finally:
        monkeypatch.setattr(config, "APP_LANGUAGE", old_language, raising=False)
        i18n.set_language(app=app)
        overlay._bubble.clear()
        overlay._icon_label.close()
        overlay.close()
        app.processEvents()
