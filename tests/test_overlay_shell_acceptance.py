"""Real-entry acceptance for the floating icon and tray shell."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.workflow


def _close_overlay(overlay, qapp) -> None:
    overlay._bubble.clear()
    overlay._context_panel.close()
    overlay._provider_badge.close()
    overlay._icon_label.close()
    overlay.close()
    qapp.processEvents()


def test_icon_states_drag_autohide_and_real_tray_visibility_toggle(qapp, monkeypatch) -> None:
    """Drive the production icon signal, mouse, timer, and tray QAction paths."""
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt

    import config
    import ui.overlay as overlay_module
    from ui.overlay import IconOverlay, OverlaySignals

    class MouseEvent:
        def __init__(self, event_type, point, *, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton):
            self._event_type = event_type
            self._point = QPointF(point)
            self._button = button
            self._buttons = buttons

        def type(self):
            return self._event_type

        def globalPosition(self):
            return self._point

        def position(self):
            return self._point

        def button(self):
            return self._button

        def buttons(self):
            return self._buttons

    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda _self: None)
    monkeypatch.setattr(overlay_module, "is_wayland", lambda: False)
    monkeypatch.setattr(overlay_module, "start_wayland_system_move", lambda _widget: False)
    monkeypatch.setattr(config, "ICON_AUTO_HIDE", False)
    signals = OverlaySignals()
    overlay = IconOverlay(signals)
    try:
        for auto_hide in (False, True):
            monkeypatch.setattr(config, "ICON_AUTO_HIDE", auto_hide)
            for state in ("idle", "listening", "thinking", "speaking"):
                overlay._icon_label.hide() if auto_hide else overlay._icon_label.show()
                signals.set_state.emit(state)
                qapp.processEvents()
                assert overlay._current_state == state
                assert state in overlay._state_icons
                assert overlay._icon_label.pixmap() is not None
                assert overlay._icon_label.isVisible() is (not auto_hide or state != "idle")

        monkeypatch.setattr(config, "ICON_AUTO_HIDE", False)

        original = overlay._icon_label.pos()
        provider_original = overlay._provider_badge.pos()
        panel_original = overlay._context_panel.pos()
        press_global = original + QPoint(12, 12)
        destination = press_global + QPoint(90, -45)
        assert overlay.eventFilter(
            overlay._icon_label,
            MouseEvent(QEvent.Type.MouseButtonPress, press_global, button=Qt.MouseButton.LeftButton),
        )
        assert overlay.eventFilter(
            overlay._icon_label,
            MouseEvent(QEvent.Type.MouseMove, destination, buttons=Qt.MouseButton.LeftButton),
        )
        assert overlay.eventFilter(
            overlay._icon_label,
            MouseEvent(QEvent.Type.MouseButtonRelease, destination, button=Qt.MouseButton.LeftButton),
        )
        assert overlay._icon_label.pos() == original + QPoint(90, -45)
        assert overlay._provider_badge.pos() != provider_original
        assert overlay._context_panel.pos() != panel_original

        toggle = overlay._icon_toggle_action
        assert overlay._icon_label.isVisible()
        toggle.trigger()
        qapp.processEvents()
        assert not overlay._icon_label.isVisible()
        overlay._sync_icon_toggle_text()
        assert toggle.text() == "Show icon"
        toggle.trigger()
        qapp.processEvents()
        assert overlay._icon_label.isVisible()

        monkeypatch.setattr(config, "ICON_AUTO_HIDE", True)
        signals.set_state.emit("listening")
        qapp.processEvents()
        assert overlay._icon_label.isVisible()
        signals.hide_icon.emit()
        assert overlay._icon_hide_timer.isActive()
        overlay._on_icon_hide_timeout()
        assert not overlay._icon_label.isVisible()
    finally:
        _close_overlay(overlay, qapp)


def _wait_snapshot(ui, key: str, *, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    snapshot = {}
    while time.monotonic() < deadline:
        snapshot = ui.call("ui.debug.shell.snapshot", timeout=5)
        if snapshot.get(key) is True:
            return snapshot
        time.sleep(0.05)
    pytest.fail(f"UI shell did not reach {key}: {snapshot}")


@pytest.mark.parametrize(
    ("execution_mode", "provider_title"),
    (("codex", "ChatGPT"), ("claude", "Claude")),
)
def test_real_worker_tray_and_provider_actions_open_every_target_then_quit(
    tmp_path: Path,
    execution_mode: str,
    provider_title: str,
) -> None:
    """Trigger actual UI-worker QActions and observe every production target window."""
    from runtime.supervisor.flows import FlowController
    from runtime.supervisor.ipc import WispSupervisor, default_specs

    (tmp_path / ".env").write_text(
        f"WISP_ONBOARDING_COMPLETE=True\nCHAT_EXECUTION_MODE={execution_mode}\nTTS_PROVIDER=none\n",
        encoding="utf-8",
    )
    shared_env = {
        "PYTHONPATH": os.pathsep.join(
            [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / "runtime" / "brain")]
        ),
        "QT_QPA_PLATFORM": "offscreen",
        "CHAT_EXECUTION_MODE": execution_mode,
        "WISP_ADDONS_DIR": str(tmp_path / "addons"),
        "WISP_BRAIN_FAKE_LLM": "1",
        "WISP_REPO_ROOT": str(tmp_path),
        "WISP_UI_DEBUG_METHODS": "1",
        "WISP_ONBOARDING_COMPLETE": "True",
    }
    specs = default_specs()
    for spec in specs.values():
        spec.env = {**spec.env, **shared_env}
    supervisor = WispSupervisor(specs)
    flow = FlowController(
        native=supervisor.workers["native"],
        ui=supervisor.workers["ui"],
        brain=supervisor.workers["brain"],
        audio=supervisor.workers["audio"],
    )
    ui = supervisor.workers["ui"]
    try:
        supervisor.start_all()
        flow.start()

        for label, visible_key in (
            ("Last chat", "chat_visible"),
            ("Memory", "memory_visible"),
            ("Addon Manager", "addons_visible"),
            ("Settings", "settings_visible"),
            ("Runtime Status", "runtime_status_visible"),
        ):
            result = ui.call("ui.debug.tray.trigger", {"label": label}, timeout=10)
            assert result == {"triggered": True, "label": label}
            _wait_snapshot(ui, visible_key)

        assert ui.call("ui.debug.provider_badge.click", timeout=10) == {
            "clicked": True,
            "provider": execution_mode,
        }
        provider = _wait_snapshot(ui, "provider_controls_visible")
        assert any(provider_title in title for title in provider["visible_window_titles"])

        assert ui.call("ui.debug.tray.trigger", {"label": "Quit"}, timeout=10)["triggered"] is True
        deadline = time.monotonic() + 10
        while ui.alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not ui.alive()
    finally:
        flow.stop()
        supervisor.shutdown()
