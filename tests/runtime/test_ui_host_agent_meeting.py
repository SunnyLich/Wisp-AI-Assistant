"""Tests for the UI host's agent meeting room views and controls."""

from __future__ import annotations

import importlib.util
import os

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")


class _Host:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.notices: list[tuple[str, bool, dict | None]] = []

    def emit(self, event: str, data: dict) -> None:
        self.events.append((event, data))

    def _agent_notify_approval(
        self,
        text: str = "",
        resolved: bool = False,
        data: dict | None = None,
    ) -> dict:
        self.notices.append((text, resolved, data))
        return {"notified": True}


def _qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_mac_ui_agent_run_dialog_recreates_meeting_room(tmp_path):
    app = _qapp()
    import config
    from ui import i18n

    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "en"
    i18n.set_language(app=app)
    from runtime.workers.ui_host import MacAgentRunDialog

    dialog = MacAgentRunDialog(
        _Host(),
        {
            "title": "Demo task",
            "scope_folder": str(tmp_path),
            "agents": [
                {"name": "Planner", "role": "Coordinator"},
                {"name": "Builder", "role": "Implementer"},
            ],
        },
    )
    try:
        assert dialog.tabs.tabText(0) == "Meeting Room"
        cards = [item for item in dialog.meeting_scene.items() if isinstance(item, dialog._LiveAgentItem)]
        assert len(cards) == 2
        assert not dialog.meeting_scene.sceneRect().isEmpty()

        dialog.append_log({"line": "[00:00:00] agent turn 1: Planner"})
        dialog.append_log({"line": "[00:00:01] Planner thought: I need to inspect the UI worker"})
        dialog.append_log({"line": "[00:00:02] message: Planner -> Builder: Please restore the meeting room"})
        dialog.append_log({"line": "[00:00:03] agent turn 2: Builder"})
        dialog.append_log({"line": "[00:00:04] Builder tool call: patch_file"})

        assert dialog._active_agent == "Builder"
        assert len([item for item in dialog.meeting_scene.items() if isinstance(item, dialog._LiveAgentItem)]) == 2
        assert any(item.__class__.__name__.endswith("PathItem") for item in dialog.meeting_scene.items())
        assert "Planner -> Builder" in dialog.shared_board_view.toPlainText()
        assert "Please restore the meeting room" in dialog.shared_board_view.toPlainText()

        dialog._select_live_agent(1)
        detail = dialog.agent_detail_view.toPlainText()
        assert "Builder" in detail
        assert "Using patch_file" in detail
        assert "patch_file" in detail
    finally:
        dialog.close()
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


def test_mac_ui_agent_meeting_view_wheel_zoom_survives_redraw(tmp_path):
    """Verify mac UI meeting room supports mouse-wheel zoom."""
    _qapp()
    from runtime.workers.ui_host import MacAgentRunDialog

    class _Delta:
        def __init__(self, value: int) -> None:
            self._value = value

        def y(self) -> int:
            return self._value

    class _Wheel:
        def __init__(self, value: int) -> None:
            self._value = value
            self.accepted = False

        def angleDelta(self) -> _Delta:
            return _Delta(self._value)

        def accept(self) -> None:
            self.accepted = True

    dialog = MacAgentRunDialog(
        _Host(),
        {
            "title": "Zoom task",
            "scope_folder": str(tmp_path),
            "agents": [
                {"name": "Planner", "role": "Coordinator"},
                {"name": "Builder", "role": "Implementer"},
            ],
        },
    )
    try:
        view = dialog.meeting_view
        fitted_scale = view.transform().m11()
        wheel = _Wheel(120)

        view.wheelEvent(wheel)
        zoomed_scale = view.transform().m11()
        dialog.append_log({"line": "[00:00:00] agent turn 1: Planner"})

        assert wheel.accepted
        assert zoomed_scale > fitted_scale
        assert view.transform().m11() == pytest.approx(zoomed_scale)
    finally:
        dialog.close()


def test_mac_ui_agent_controls_and_shared_board_preserve_scroll(tmp_path):
    """Verify live controls emit events and shared board refresh preserves scroll."""
    app = _qapp()
    from runtime.workers.ui_host import MacAgentRunDialog

    host = _Host()
    dialog = MacAgentRunDialog(
        host,
        {
            "title": "Control task",
            "scope_folder": str(tmp_path),
            "agents": [
                {"name": "Planner", "role": "Coordinator"},
                {"name": "Builder", "role": "Implementer"},
            ],
        },
    )
    try:
        assert dialog._paused is False
        dialog._toggle_pause()
        assert host.events[-1] == ("ui.agent.pause_requested", {})
        assert dialog._paused is True
        dialog._toggle_pause()
        assert host.events[-1] == ("ui.agent.resume_requested", {})
        assert dialog._paused is False

        for idx in range(24):
            dialog._record_meeting_message(f"message: Planner -> Builder: update {idx}")
        dialog._refresh_shared_board()
        app.processEvents()
        bar = dialog.shared_board_view.verticalScrollBar()
        bar.setValue(max(0, bar.maximum() // 2))
        old_value = bar.value()
        dialog._record_meeting_message("message: Builder -> Planner: final update")
        dialog._refresh_shared_board()
        app.processEvents()

        assert dialog.shared_board_view.verticalScrollBar().value() == old_value
    finally:
        dialog.close()


def test_mac_ui_agent_finish_shows_prominent_banner(tmp_path):
    """Verify protocol-backed auto-agent runs show an obvious finished state."""
    _qapp()
    from runtime.workers.ui_host import MacAgentRunDialog

    host = _Host()
    dialog = MacAgentRunDialog(
        host,
        {
            "title": "Finish task",
            "scope_folder": str(tmp_path),
            "agents": [
                {"name": "Planner", "role": "Coordinator"},
                {"name": "Builder", "role": "Implementer"},
            ],
        },
    )
    try:
        dialog.finish({"run_dir": str(tmp_path / "run"), "final": "All done."})

        assert dialog.completion_banner.isHidden() is False
        assert dialog.completion_banner_title.text() == "Agent Task Finished"
        assert "Final report is ready" in dialog.completion_banner_detail.text()
        assert dialog.tabs.currentWidget() is dialog.final_view
        assert dialog.final_view.toPlainText() == "All done."
        assert host.notices
        assert host.notices[-1][0] == "Agent Task Finished"
        assert host.notices[-1][1] is True
    finally:
        dialog.close()
