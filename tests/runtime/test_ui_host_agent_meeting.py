"""Tests for macos py test ui host agent meeting."""

from __future__ import annotations

import importlib.util
import os

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")


class _Host:
    """Test case for host behavior."""
    def __init__(self) -> None:
        """Initialize the host instance."""
        self.events: list[tuple[str, dict]] = []
        self.notices: list[tuple[str, bool, dict | None]] = []

    def emit(self, event: str, data: dict) -> None:
        """Verify emit behavior."""
        self.events.append((event, data))

    def _agent_notify_approval(
        self,
        text: str = "",
        resolved: bool = False,
        data: dict | None = None,
    ) -> dict:
        """Verify agent notify approval behavior."""
        self.notices.append((text, resolved, data))
        return {"notified": True}


def _qapp():
    """Verify qapp behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_mac_ui_agent_run_dialog_recreates_meeting_room(tmp_path):
    """Verify mac ui agent run dialog recreates meeting room behavior."""
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
