"""Tests for test intent overlay."""

import os
import sys

import pytest


@pytest.fixture
def qapp():
    """Return a QApplication for intent overlay widget tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    yield app
    app.processEvents()


def _close_overlay_if_valid(overlay, app) -> None:
    """Close a Qt overlay unless WA_DeleteOnClose already destroyed it."""
    shiboken6 = pytest.importorskip("shiboken6", reason="shiboken6 not installed")
    if shiboken6.isValid(overlay):
        overlay.close()
        app.processEvents()


def test_intent_overlay_tools_tab_uses_execution_snapshot_and_t_shortcut(qapp, monkeypatch):
    """Tools are a sibling view whose statuses come from the execution layer."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "browser", "key": "2", "label": "Browser", "state": "on"}],
        tool_snapshot={
            "execution": "Codex / gpt-test",
            "count": 2,
            "items": [
                {
                    "name": "Read files",
                    "status": "ready",
                    "description": "Read workspace files.",
                    "tools": ["read_file"],
                    "toggleable": True,
                },
                {
                    "name": "Run commands",
                    "status": "ask",
                    "description": "May request approval.",
                    "tools": ["run_command"],
                    "toggleable": True,
                },
                {"name": "Network", "status": "unavailable"},
            ],
        },
    )
    try:
        overlay.show()
        qapp.processEvents()

        assert overlay._active_panel == "context"
        assert overlay.tool_snapshot()["count"] == 2
        assert [item["status"] for item in overlay.tool_snapshot()["items"]] == [
            "ready",
            "ask",
            "unavailable",
        ]

        QTest.keyClick(overlay, Qt.Key.Key_T)
        qapp.processEvents()

        assert overlay._active_panel == "tools"
        assert overlay._context_preview_height() == 0

        first_tool_rect, _index = overlay._tool_row_rects[0]
        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=first_tool_rect.center())
        qapp.processEvents()
        assert overlay.tool_snapshot()["count"] == 1
        assert overlay.tool_choices()[0] == {
            "id": "Read files",
            "tools": ["read_file"],
            "enabled": False,
            "toggleable": True,
        }

        # Numeric source shortcuts retain their original behavior and return
        # to Context so the changed state is visible.
        QTest.keyClick(overlay, Qt.Key.Key_2)
        qapp.processEvents()
        assert overlay._active_panel == "context"
        assert overlay.context_choices()[0]["state"] == "off"
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_intent_overlay_does_not_steal_configured_t_intent(qapp, monkeypatch):
    """A caller-owned T command wins when the optional Tools shortcut conflicts."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{
        "intents": [{"key": "t", "label": "Translate", "hint": "", "prompt": "Translate this."}],
        "custom_key": "s",
    }]
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    chosen: list[tuple[str, str]] = []
    overlay = intent_overlay.IntentOverlay(caller_idx=0, tool_snapshot={"count": 1, "items": []})
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    try:
        overlay.show()
        qapp.processEvents()
        assert overlay._tools_shortcut_available() is False

        QTest.keyClick(overlay, Qt.Key.Key_T)
        QTest.qWait(120)
        qapp.processEvents()

        assert chosen == [("T", "Translate this.")]
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_intent_tool_inventory_groups_real_schemas_and_file_approval(tmp_path):
    from runtime.supervisor.tool_inventory import build_openwand_inventory

    snapshot = build_openwand_inventory(
        provider="anthropic",
        model="claude-test",
        allowed_tools=[
            "list_files", "read_file", "create_file", "edit_file", "write_file",
            "web_search", "retrieve_website", "mcp_slack_search",
        ],
        file_access_mode="ask",
        tool_descriptions={"mcp_slack_search": "[MCP:slack] Search messages."},
        file_roots=[str(tmp_path)],
    )

    by_name = {item["name"]: item for item in snapshot["items"]}
    assert snapshot["execution"] == "anthropic / claude-test"
    assert snapshot["count"] == 8
    assert by_name["Read files"]["status"] == "ready"
    assert by_name["Read files"]["toggleable"] is True
    assert by_name["Read files"]["tools"] == ["list_files", "read_file"]
    assert by_name["Edit files"]["status"] == "ask"
    assert by_name["Web search"]["count"] == 2
    assert by_name["Connected apps"]["count"] == 1


def test_intent_tool_inventory_marks_known_unsupported_route_unavailable(tmp_path):
    from runtime.supervisor.tool_inventory import build_openwand_inventory

    snapshot = build_openwand_inventory(
        provider="copilot",
        model="gpt-test",
        allowed_tools=["read_file", "web_search"],
        file_access_mode="read",
        file_roots=[str(tmp_path)],
    )

    assert snapshot["count"] == 0
    assert {item["status"] for item in snapshot["items"]} == {"unavailable"}
    assert not any(item["toggleable"] for item in snapshot["items"])


def test_intent_tool_inventory_reflects_native_harness_policies():
    from runtime.supervisor.tool_inventory import build_harness_inventory

    ask = build_harness_inventory(
        execution_mode="codex",
        model="gpt-test",
        approval_mode="ask",
    )
    assert {item["name"]: item["status"] for item in ask["items"]} == {
        "Read files": "ready",
        "Edit files": "ask",
        "Run commands": "ask",
        "Network": "ask",
    }
    assert ask["count"] == 4
    assert not any(item["toggleable"] for item in ask["items"])

    read_only = build_harness_inventory(
        execution_mode="codex",
        model="gpt-test",
        approval_mode="read_only",
    )
    assert read_only["count"] == 1
    assert [item["status"] for item in read_only["items"]] == [
        "ready", "unavailable", "unavailable", "unavailable",
    ]

    claude = build_harness_inventory(
        execution_mode="claude",
        model="claude-test",
        approval_mode="full_access",
    )
    assert claude["count"] == 3
    assert [item["name"] for item in claude["items"]] == [
        "Read files", "Find files", "Search files",
    ]


def test_intent_tool_choices_only_remove_selected_tools_for_one_prompt():
    from runtime.supervisor.flows import FlowController
    from runtime.supervisor.tool_modes import allowed_model_tools

    caller = {
        "tools": {"memory_save": "on"},
        "file_access": "ask",
    }
    updated = FlowController._apply_intent_tool_choices(
        caller,
        [
            {
                "tools": ["read_file", "list_files"],
                "enabled": False,
                "toggleable": True,
            },
            {
                "tools": ["memory_save"],
                "enabled": True,
                "toggleable": True,
            },
            {
                "tools": ["cannot_be_disabled_from_ui"],
                "enabled": False,
                "toggleable": False,
            },
        ],
    )

    assert updated is not caller
    assert updated["tools"] == {
        "memory_save": "on",
        "read_file": "off",
        "list_files": "off",
    }
    assert caller["tools"] == {"memory_save": "on"}
    assert "read_file" not in allowed_model_tools(updated)
    assert "list_files" not in allowed_model_tools(updated)
    assert "edit_file" in allowed_model_tools(updated)


def test_addon_intent_rows_render_and_run_from_the_visible_picker(qapp, monkeypatch):
    """A contributed prompt row is visible and emits its exact action on keypress."""
    from types import SimpleNamespace

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    import config
    import core.addon_manager as addon_manager
    import ui.intent_overlay as intent_overlay

    manager = SimpleNamespace(
        get_intents=lambda caller_idx: [
            {
                "addon_id": "demo",
                "id": "research",
                "label": "Research with addon",
                "hint": "Addon: demo",
                "key": "x",
                "prompt": "Research the current selection with the add-on.",
                "caller": str(caller_idx),
            }
        ]
    )
    monkeypatch.setattr(addon_manager, "get_manager", lambda: manager)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    chosen = []
    overlay.intent_chosen.connect(lambda prompt, label: chosen.append((prompt, label)))
    try:
        overlay.show()
        overlay.activateWindow()
        overlay.setFocus()
        qapp.processEvents()
        addon_row = next(row for row in overlay._rows if row["label"] == "Research with addon")
        assert addon_row == {
            "glyph": "X",
            "label": "Research with addon",
            "hint": "Addon: demo",
            "prompt": "Research the current selection with the add-on.",
            "is_custom": False,
            "routing": {
                "mode": "addon",
                "source": "addon",
                "addon_id": "demo",
                "action_id": "research",
                "callback": False,
            },
            "access": [],
            "access_colour": "",
        }

        QTest.keyClick(overlay, Qt.Key.Key_X)
        QTest.qWait(120)
        qapp.processEvents()
        assert chosen == [(
            "X",
            "Research the current selection with the add-on.",
        )]
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_provider_suggestions_precede_but_preserve_configured_and_custom_rows(qapp):
    """The captured app can tailor shortcuts without replacing user configuration."""
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{
        "intents": [{
            "key": "f",
            "label": "Configured fix",
            "hint": "Existing behavior",
            "prompt": "Use my configured fix prompt.",
        }],
        "custom_key": "s",
        "custom_label": "Freeform",
    }]
    provider = {
        "id": "vscode",
        "app": "vscode",
        "display_name": "VS Code",
        "suggested_intents": [{
            "id": "vscode.fix_selection",
            "label": "Fix selected code",
            "hint": "Preview and apply a focused code change",
            "prompt": "Fix the selected code.",
            "preferred_key": "F",
            "mode": "action",
            "capability_type": "vscode.replace_selection@1",
            "planning_tool": "vscode_plan_replace_selection",
        }],
    }
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay.update_action_provider(provider)
        assert [row["label"] for row in overlay._rows] == [
            "Fix selected code",
            "Configured fix",
            "Freeform",
        ]
        assert overlay._rows[0]["appearance"] == "app_action"
        assert "appearance" not in overlay._rows[1]
        assert overlay._rows[0]["glyph"] != "F"
        assert overlay._rows[1]["glyph"] == "F"
        assert overlay._rows[1]["routing"] == {"mode": "answer", "source": "configured"}
        assert overlay._rows[-1]["routing"] == {"mode": "answer", "source": "custom"}
        overlay._selection_pending_idx = 0
        overlay._fire(0)
        assert overlay.selected_intent_routing() == {
            "mode": "action",
            "source": "provider",
            "suggestion_id": "vscode.fix_selection",
            "capability_type": "vscode.replace_selection@1",
            "planning_tool": "vscode_plan_replace_selection",
            "provider_id": "vscode",
            "app": "vscode",
        }
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_rewrite_custom_prompt_keeps_rewrite_routing(qapp):
    """The explicit Rewrite & Paste caller remains an action surface."""
    import ui.intent_overlay as intent_overlay

    overlay = intent_overlay.IntentOverlay(caller_idx=1)
    try:
        custom = next(row for row in overlay._rows if row.get("is_custom"))
        assert custom["routing"] == {"mode": "legacy", "source": "custom"}
    finally:
        _close_overlay_if_valid(overlay, qapp)


def test_hidden_provider_primitive_stays_out_of_picker_rows(qapp):
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    provider = {
        "id": "excel",
        "app": "excel",
        "display_name": "Microsoft Excel",
        "suggested_intents": [
            {
                "id": "add_chart",
                "label": "Create a chart",
                "hint": "Planner primitive",
                "prompt": "Create a chart.",
                "preferred_key": "c",
                "mode": "action",
                "capability_type": "excel.add_chart@1",
                "planning_tool": "excel_plan_add_chart",
                "show_in_picker": False,
            },
            {
                "id": "find_outliers",
                "label": "Find outliers in this data",
                "hint": "Explain unusual rows",
                "prompt": "Find outliers.",
                "preferred_key": "u",
                "mode": "answer",
                "show_in_picker": True,
            },
        ],
    }
    overlay = intent_overlay.IntentOverlay(caller_idx=0, action_provider=provider)
    try:
        assert [row["label"] for row in overlay._rows] == [
            "Find outliers in this data",
            "Custom prompt",
        ]
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_writer_app_action_is_golden_and_precedes_normal_actions():
    """Text-app provider actions keep the same highlighted, top-of-list treatment."""
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{
        "paste_back": False,
        "intents": [{
            "key": "w",
            "label": "Normal writing action",
            "hint": "Configured action",
            "prompt": "Improve this writing.",
        }],
        "custom_key": "s",
    }]
    try:
        rows = intent_overlay._build_rows(0, [{
            "id": "writer.summarize_document",
            "label": "Summarize this document",
            "hint": "Use the active Writer document",
            "prompt": "Summarize this document.",
            "preferred_key": "d",
            "mode": "answer",
        }])

        assert [row["label"] for row in rows] == [
            "Summarize this document",
            "Normal writing action",
            "Custom prompt",
        ]
        assert rows[0]["appearance"] == "app_action"
        assert all(row.get("appearance") != "app_action" for row in rows[1:])
        palette = intent_overlay._theme_palette()
        assert intent_overlay._intent_label_color(
            rows[0], palette, available=True
        ) == palette["app_action"]
        assert intent_overlay._intent_label_color(
            rows[1], palette, available=True
        ) == palette["label"]
    finally:
        config.CALLER_ROWS[:] = old_rows


def test_rewrite_picker_ignores_app_action_provider(qapp):
    """Rewrite & Paste never exposes app actions intended for the General caller."""
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{
        "paste_back": True,
        "intents": [{
            "key": "w",
            "label": "Fix grammar",
            "hint": "Correct the selection",
            "prompt": "Fix the grammar.",
        }],
        "custom_key": "s",
    }]
    provider = {
        "id": "vscode",
        "app": "vscode",
        "suggested_intents": [{
            "id": "vscode.fix_selection",
            "label": "Fix selected code",
            "hint": "Preview and apply a focused code change",
            "prompt": "Fix the selected code.",
            "preferred_key": "F",
            "mode": "action",
            "capability_type": "vscode.replace_selection@1",
            "planning_tool": "vscode_plan_replace_selection",
        }],
    }
    overlay = intent_overlay.IntentOverlay(caller_idx=0, action_provider=provider)
    try:
        assert [row["label"] for row in overlay._rows] == ["Fix grammar", "Custom prompt"]
        configured = next(row for row in overlay._rows if row["label"] == "Fix grammar")
        assert configured["routing"] == {"mode": "legacy", "source": "configured"}
        assert overlay._action_provider == {}

        overlay.update_action_provider(provider)

        assert [row["label"] for row in overlay._rows] == ["Fix grammar", "Custom prompt"]
        assert overlay._action_provider == {}
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_unavailable_provider_action_is_visible_but_cannot_fire(qapp):
    """Missing account bridges are explained in-place instead of exposing a fake action."""
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    provider = {
        "id": "gmail",
        "app": "email",
        "display_name": "Gmail",
        "suggested_intents": [{
            "id": "gmail.create_draft",
            "label": "Create email draft",
            "hint": "Preview recipients, subject, and body",
            "prompt": "Create a draft email.",
            "mode": "action",
            "capability_type": "email.create_draft@1",
            "planning_tool": "email_plan_create_draft",
            "available": False,
            "unavailable_reason": "Connect this account to OpenWand first",
        }],
    }
    overlay = intent_overlay.IntentOverlay(caller_idx=0, action_provider=provider)
    chosen = []
    overlay.intent_chosen.connect(lambda *value: chosen.append(value))
    try:
        row = overlay._rows[0]
        assert row["label"] == "Create email draft"
        assert row["hint"] == "Connect this account to OpenWand first"
        assert row["available"] is False

        overlay._select(0, drop_trigger_key=False)
        qapp.processEvents()
        assert chosen == []
        assert overlay._selection_pending_idx is None
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_ui_host_emits_provider_routing_with_chosen_intent(qapp, monkeypatch):
    """Provider routing survives the visible picker and UI event boundary."""
    import ui.intent_overlay as intent_overlay
    from runtime.workers.ui_host import QtProtocolHost

    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)

    events = []
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._intent = None
    host._active_project_id = "general"
    host._intent_conversation_options = lambda: []
    host._intent_project_options = lambda: [{"id": "general", "name": "General"}]
    host._intent_active_project_id = lambda: "general"
    host._intent_conversation_namespace_label = lambda: ""
    host._apply_intent_project_choice = lambda value: value
    host._apply_intent_conversation_choice = lambda value: value
    host.emit = lambda event, payload: events.append((event, payload))

    provider = {
        "id": "libreoffice_calc",
        "app": "libreoffice_calc",
        "display_name": "LibreOffice Calc",
        "suggested_intents": [{
            "id": "calc.add_chart",
            "label": "Create a bar chart",
            "hint": "Build a reviewed chart from the selected cells",
            "prompt": "Create a vertical bar chart from the selected cells.",
            "preferred_key": "C",
            "mode": "action",
            "capability_type": "calc.add_chart@1",
            "planning_tool": "calc_plan_add_chart",
        }],
    }
    try:
        host._show_intent(
            caller_idx=0,
            action_provider=provider,
            tool_snapshot={
                "count": 1,
                "items": [{
                    "id": "web",
                    "name": "Web search",
                    "status": "ready",
                    "tools": ["web_search"],
                    "toggleable": True,
                }],
            },
        )
        host._intent._tool_items[0]["enabled"] = False
        host._intent._selection_pending_idx = 0
        host._intent._fire(0)
        qapp.processEvents()

        event, payload = next(item for item in events if item[0] == "ui.intent.chosen")
        assert event == "ui.intent.chosen"
        assert payload["custom"] == "Create a vertical bar chart from the selected cells."
        assert payload["intent_routing"] == {
            "mode": "action",
            "source": "provider",
            "suggestion_id": "calc.add_chart",
            "capability_type": "calc.add_chart@1",
            "planning_tool": "calc_plan_add_chart",
            "provider_id": "libreoffice_calc",
            "app": "libreoffice_calc",
        }
        assert payload["tool_choices"] == [{
            "id": "web",
            "tools": ["web_search"],
            "enabled": False,
            "toggleable": True,
        }]
    finally:
        if host._intent is not None:
            _close_overlay_if_valid(host._intent, qapp)


def test_custom_prompt_wraps_and_grows_vertically(qapp, monkeypatch):
    """Long freeform prompts wrap into additional visible editor lines."""
    from PySide6.QtCore import Qt

    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay.show()
        overlay._enter_custom_mode(drop_trigger_key=False)
        qapp.processEvents()
        initial_overlay_h = overlay.height()

        overlay._input_line.setText("wrapped prompt " * 80)
        qapp.processEvents()
        overlay._resize_prompt_input()

        assert overlay._input_line.height() > intent_overlay._INPUT_MIN_H
        assert overlay.height() > initial_overlay_h
        assert overlay._input_line.horizontalScrollBar().maximum() == 0
        assert overlay._input_line.height() <= overlay._prompt_input_max_height()
        assert (
            overlay._input_line.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_custom_prompt_single_line_has_vertical_room(qapp, monkeypatch):
    """The prompt editor leaves room for complete glyphs and descenders."""
    from PySide6.QtGui import QFontMetrics

    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay.show()
        overlay._enter_custom_mode(drop_trigger_key=False)
        overlay._input_line.setText("Summarize this. glyphs: gypq")
        qapp.processEvents()

        font_height = QFontMetrics(overlay._input_line.font()).lineSpacing()
        assert overlay._input_line.document().documentMargin() == 0
        assert overlay._input_line.viewport().height() >= font_height + 8
        cursor = overlay._input_line.cursorRect()
        content_top = overlay._input_line.viewport().geometry().top() + cursor.top()
        content_bottom = content_top + cursor.height()
        assert abs(content_top - (overlay._input_line.height() - content_bottom)) <= 1
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_custom_prompt_scrolls_only_after_overlay_fills_screen(qapp, monkeypatch):
    """The editor uses available screen height before showing a scrollbar."""
    from PySide6.QtCore import QRect, Qt

    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._screen_geometry = QRect(0, 0, 800, 360)
        overlay.show()
        overlay._enter_custom_mode(drop_trigger_key=False)
        overlay._input_line.setText("very long wrapped prompt " * 300)
        qapp.processEvents()
        overlay._resize_prompt_input()

        assert overlay.height() <= 360 - intent_overlay._SCREEN_MARGIN
        assert overlay._input_line.height() == overlay._prompt_input_max_height()
        assert (
            overlay._input_line.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        assert overlay._input_line.verticalScrollBar().maximum() > 0
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_custom_prompt_enter_submits_and_shift_enter_adds_line(qapp, monkeypatch):
    """Enter sends the prompt while Shift+Enter remains available for newlines."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    chosen: list[tuple[str, str]] = []
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    try:
        overlay.show()
        overlay._enter_custom_mode(drop_trigger_key=False)
        overlay._input_line.setText("first line")
        overlay._input_line.moveCursor(overlay._input_line.textCursor().MoveOperation.End)
        QTest.keyClick(
            overlay._input_line,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.ShiftModifier,
        )
        QTest.keyClicks(overlay._input_line, "second line")
        assert overlay._input_line.text() == "first line\nsecond line"

        QTest.keyClick(overlay._input_line, Qt.Key.Key_Return)
        qapp.processEvents()

        assert chosen == [("S", "first line\nsecond line")]
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


@pytest.mark.parametrize("clipboard_kind", ["image", "file"])
def test_custom_prompt_paste_attaches_non_text_clipboard_context(
    qapp,
    monkeypatch,
    tmp_path,
    clipboard_kind,
):
    """Ctrl+V attaches copied images/files without inserting paths in the prompt."""
    from PySide6.QtCore import QEvent, QMimeData, Qt, QUrl
    from PySide6.QtGui import QImage, QKeyEvent

    import config
    import ui.intent_overlay as intent_overlay

    mime = QMimeData()
    if clipboard_kind == "image":
        image = QImage(3, 2, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.magenta)
        mime.setImageData(image)
        mime.setText("https://example.test/source-image")
    else:
        path = tmp_path / "copied.txt"
        path.write_text("copied file body", encoding="utf-8")
        mime.setUrls([QUrl.fromLocalFile(str(path))])

    class Clipboard:
        @staticmethod
        def mimeData():
            return mime

    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay.QApplication, "clipboard", staticmethod(Clipboard))
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    pasted: list[list[tuple[str, str, str]]] = []
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.context_items_pasted.connect(lambda items: pasted.append(list(items)))
    try:
        overlay.show()
        overlay._enter_custom_mode(drop_trigger_key=False)
        overlay._input_line.setText("describe this")
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier,
            "v",
        )

        assert overlay.eventFilter(overlay._input_line, event) is True
        assert overlay._input_line.text() == "describe this"
        assert len(pasted) == 1
        assert pasted[0][0][2] == clipboard_kind.replace("file", "text")
        if clipboard_kind == "image":
            assert pasted[0][0][0] == "Pasted image"
        else:
            assert pasted[0][0][0] == "copied.txt"
            assert pasted[0][0][1] == "copied file body"
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_context_preview_entries_expand_item_sources(monkeypatch):
    """Verify one App chip can show multiple detected source previews."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[
            {
                "id": "ambient",
                "key": "1",
                "label": "App",
                "state": "on",
                "sources": [
                    {"app": "Notepad", "label": "Notes.txt", "preview": "notepad body"},
                    {"app": "VS Code", "label": "demo.py", "preview": "VS Code paragraph"},
                ],
            }
        ],
    )
    try:
        assert overlay._context_preview_entries() == [
            ("Notepad: Notes.txt", "notepad body", "ambient", "Notes.txt"),
            ("VS Code: demo.py", "VS Code paragraph", "ambient", "demo.py"),
        ]
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_prompt_input_grabs_keyboard_on_windows(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    grabs: list[QWidget] = []
    releases: list[QWidget] = []
    force_foreground_calls: list[bool] = []

    def grab_keyboard(self):
        grabs.append(self)

    def release_keyboard(self):
        releases.append(self)

    monkeypatch.setattr(intent_overlay, "_IS_WIN", True)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(
        intent_overlay.IntentOverlay,
        "_win_force_foreground",
        lambda self: force_foreground_calls.append(True),
    )
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._enter_custom_mode()

        assert overlay._input_line.isHidden() is False
        assert force_foreground_calls == [True]
        assert grabs == [overlay._input_line]
        assert overlay._input_grabbed_keyboard is True

        overlay._unhook()

        assert releases == [overlay._input_line]
        assert overlay._input_grabbed_keyboard is False
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_key_debug_avoids_prompt_text(monkeypatch, capsys):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_DEBUG_KEYS", True)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._custom_mode = True
        overlay._input_line.show()
        overlay._debug_key(
            "test",
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_A,
                Qt.KeyboardModifier.NoModifier,
                "a",
            ),
        )

        err = capsys.readouterr().err
        assert "[openwand-intent]" in err
        assert "text=printable-len:1" in err
        assert " a " not in err
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_auto_custom_prompt_keeps_first_typed_key(monkeypatch):
    """Verify blank custom key starts typing mode without dropping first input."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": ""}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._enter_auto_custom_mode()

        assert overlay._custom_mode is True
        assert overlay._input_line.isHidden() is False
        assert overlay._drop_next_keypress is False
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_prefilled_custom_prompt_keeps_context_keys(monkeypatch):
    """A prefilled voice prompt should not steal context toggle keys."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    chosen: list[tuple[str, str]] = []
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "memory", "key": "1", "label": "Memory", "state": "off"}],
        initial_custom_text="voice prompt",
        focus_overlay=True,
    )
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    try:
        overlay._enter_prefilled_custom_mode()

        assert overlay._prefilled_custom_mode is True
        assert overlay._custom_mode is False
        assert overlay._input_line.text() == "voice prompt"
        assert overlay._input_line.isHidden() is False

        overlay.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier, "1")
        )
        assert overlay.context_choices()[0]["state"] == "on"

        overlay.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        )
        assert chosen == [("S", "voice prompt")]
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_cancel_if_focus_leaves(monkeypatch):
    """Verify clicking away cancels a pending custom prompt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    cancelled: list[bool] = []
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.cancelled.connect(lambda: cancelled.append(True))
    try:
        overlay.show()
        overlay._enter_custom_mode()
        monkeypatch.setattr(
            intent_overlay.QApplication,
            "focusWidget",
            staticmethod(lambda: overlay._input_line),
        )

        overlay._cancel_if_focus_left()

        assert cancelled == []
        assert overlay._handled is False

        monkeypatch.setattr(
            intent_overlay.QApplication,
            "focusWidget",
            staticmethod(lambda: None),
        )

        overlay._cancel_if_focus_left()

        assert cancelled == [True]
        assert overlay._handled is True
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_close_emits_cancelled_once(qapp):
    """Verify lifecycle closes report cancellation to reset overlay state."""
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    cancelled: list[bool] = []
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.cancelled.connect(lambda: cancelled.append(True))
    try:
        overlay.show()
        overlay.close()

        assert cancelled == [True]
        assert overlay._handled is True

        assert overlay._cancel_if_unhandled() is False
        qapp.processEvents()

        assert cancelled == [True]
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_escape_restores_hotkey_source_window_before_next_invocation(qapp, monkeypatch):
    """Escape returns focus so a repeated hotkey still detects the source app."""
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    restored: list[int] = []
    cancelled: list[bool] = []
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    monkeypatch.setattr(intent_overlay, "_restore_foreground_window", restored.append)
    overlay = intent_overlay.IntentOverlay(caller_idx=0, target_hwnd=777)
    overlay.cancelled.connect(lambda: cancelled.append(True))
    try:
        overlay._on_raw_key("escape")

        assert cancelled == [True]
        assert restored == [777]
        assert overlay._handled is True
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_intent_text_and_popup_disappear_together_after_choose_or_cancel(qapp, monkeypatch):
    """The picker shell must close when its visible choice text is resolved."""
    import shiboken6

    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [
        {
            "intents": [
                {
                    "key": "f",
                    "label": "Fix selected code",
                    "prompt": "Fix the selected code.",
                }
            ],
            "custom_key": "s",
        }
    ]
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)

    chosen: list[tuple[str, str]] = []
    picker = intent_overlay.IntentOverlay(caller_idx=0)
    picker.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    try:
        picker.show()
        qapp.processEvents()
        assert picker.isVisible()
        assert picker._rows[0]["label"] == "Fix selected code"

        picker._selection_pending_idx = 0
        picker._fire(0)
        qapp.processEvents()
        assert chosen == [("F", "Fix the selected code.")]
        assert not shiboken6.isValid(picker) or not picker.isVisible()
    finally:
        _close_overlay_if_valid(picker, qapp)

    cancelled: list[bool] = []
    picker = intent_overlay.IntentOverlay(caller_idx=0)
    picker.cancelled.connect(lambda: cancelled.append(True))
    try:
        picker.show()
        qapp.processEvents()
        assert picker.isVisible()
        assert any(row["label"] == "Fix selected code" for row in picker._rows)

        picker._cancel()
        qapp.processEvents()
        assert cancelled == [True]
        assert not shiboken6.isValid(picker) or not picker.isVisible()
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(picker, qapp)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_pending_selection_close_cancels_not_chosen(qapp, monkeypatch):
    """Verify a vanished picker cancels if the highlighted row was not emitted yet."""
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    callbacks = []
    chosen: list[tuple[str, str]] = []
    cancelled: list[bool] = []
    config.CALLER_ROWS[:] = [
        {
            "intents": [{"key": "w", "label": "What?", "prompt": "What is this?"}],
            "custom_key": "s",
        }
    ]
    monkeypatch.setattr(
        intent_overlay.QTimer,
        "singleShot",
        staticmethod(lambda _delay, callback: callbacks.append(callback)),
    )
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    overlay.cancelled.connect(lambda: cancelled.append(True))
    try:
        overlay._select(0)
        overlay.close()
        qapp.processEvents()

        assert cancelled == [True]
        assert chosen == []

        assert callbacks
        callbacks[0]()

        assert cancelled == [True]
        assert chosen == []
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_translates_default_custom_prompt_label(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    i18n.set_language(app=app)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s", "custom_label": ""}]
    try:
        row = intent_overlay._build_rows(0)[-1]

        assert row["label"] == i18n.t("Custom prompt")
        assert row["label"] != "Custom prompt"
    finally:
        config.CALLER_ROWS[:] = old_rows
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_translates_builtin_labels_but_preserves_runtime_prompt():
    """Built-in overlay copy follows the app language without changing model input."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay
    from core.prompt_i18n import caller_intent_template
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    old_language = getattr(config, "APP_LANGUAGE", "")
    english = caller_intent_template(0, 0, "English")
    traditional = caller_intent_template(0, 0, "zh-Hant")
    config.APP_LANGUAGE = "zh-Hant"
    i18n.set_language(app=app)
    config.CALLER_ROWS[:] = [{"intents": [english], "custom_key": "s", "custom_label": ""}]
    try:
        row = intent_overlay._build_rows(0)[0]

        assert row["label"] == traditional["label"]
        assert row["hint"] == traditional["hint"]
        assert row["prompt"] == english["prompt"]
    finally:
        config.CALLER_ROWS[:] = old_rows
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


def test_intent_overlay_preserves_custom_prompt_label():
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s", "custom_label": "Freeform"}]
    try:
        row = intent_overlay._build_rows(0)[-1]

        assert row["label"] == "Freeform"
    finally:
        config.CALLER_ROWS[:] = old_rows


def test_intent_overlay_context_palette_uses_theme_settings():
    """Verify context chip colors derive from the active settings theme."""
    import config
    import ui.intent_overlay as intent_overlay

    old_values = {
        "THEME_MODE": getattr(config, "THEME_MODE", "system"),
        "THEME_DARK_BG": getattr(config, "THEME_DARK_BG", "#1c1e26"),
        "THEME_DARK_SURFACE": getattr(config, "THEME_DARK_SURFACE", "#17181d"),
        "THEME_DARK_TEXT": getattr(config, "THEME_DARK_TEXT", "#e8e8f0"),
        "THEME_DARK_ACCENT": getattr(config, "THEME_DARK_ACCENT", "#8b87ff"),
    }
    try:
        config.THEME_MODE = "dark"
        config.THEME_DARK_BG = "#101820"
        config.THEME_DARK_SURFACE = "#203040"
        config.THEME_DARK_TEXT = "#f0ead6"
        config.THEME_DARK_ACCENT = "#ff3366"

        palette = intent_overlay._theme_palette()

        assert palette["ctx_on"].name().lower() == "#ff3366"
        assert palette["ctx_text"].name().lower() == "#f0ead6"
        assert palette["badge_bg"].name().lower() == "#203040"
        assert palette["bg"].name().lower() == "#101820"
        assert palette["app_action"].name().lower() == "#e0b03e"
    finally:
        for key, value in old_values.items():
            setattr(config, key, value)


def test_intent_overlay_custom_prompt_input_uses_theme_settings():
    """Verify custom prompt input does not stay hard-coded dark in light mode."""
    import config
    import ui.intent_overlay as intent_overlay

    old_values = {
        "THEME_MODE": getattr(config, "THEME_MODE", "system"),
        "THEME_LIGHT_BG": getattr(config, "THEME_LIGHT_BG", "#f2f2f7"),
        "THEME_LIGHT_SURFACE": getattr(config, "THEME_LIGHT_SURFACE", "#ffffff"),
        "THEME_LIGHT_TEXT": getattr(config, "THEME_LIGHT_TEXT", "#1c1c1e"),
        "THEME_LIGHT_ACCENT": getattr(config, "THEME_LIGHT_ACCENT", "#5856d6"),
    }
    try:
        config.THEME_MODE = "light"
        config.THEME_LIGHT_BG = "#eeeeee"
        config.THEME_LIGHT_SURFACE = "#fafafa"
        config.THEME_LIGHT_TEXT = "#111111"
        config.THEME_LIGHT_ACCENT = "#2255aa"

        style = intent_overlay._input_line_stylesheet().lower()

        assert "#fafafa" in style
        assert "#111111" in style
        assert "#2255aa" in style
        assert "#2a2a38" not in style
        assert "#eeeef8" not in style
    finally:
        for key, value in old_values.items():
            setattr(config, key, value)


def test_context_preview_text_is_redacted_and_trimmed(monkeypatch):
    """Verify context preview snippets are compact and privacy-safe."""
    import config
    from runtime.supervisor.flows import FlowController

    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True, raising=False)
    preview = FlowController._context_preview_text(
        "OpenAI key sk-" + ("a" * 24) + " should not be visible " + ("x" * 240),
        limit=80,
    )

    assert "[API_KEY]" in preview
    assert "sk-" not in preview
    assert len(preview) <= 80


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_fallback_context_tokens_are_unknown():
    """Verify fallback context chips do not pretend unknown estimates are zero."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    overlay = intent_overlay.IntentOverlay(context_items=None)
    try:
        choices = {item["id"]: item for item in overlay.context_choices()}
        assert choices["browser"]["tokens"] == "? tok"
        assert choices["screenshot"]["tokens"] == "? tok"
        assert choices["files"]["tokens"] == ""
    finally:
        overlay.close()
        app.processEvents()


def test_context_total_uses_exact_count_instead_of_rounded_chip_label():
    """The large total must not turn every ~1.1k source into exactly 1,100."""
    from ui.intent_overlay import _context_token_count

    assert _context_token_count({"tokens": "~1.1k tok", "token_count": 1073}) == 1073
    assert _context_token_count({"tokens": "~1.1k tok"}) == 1100


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_fits_files_context_chip(qapp):
    """Verify all eight context chips fit and Files is hit-testable."""
    from PySide6.QtCore import QPoint

    from ui import intent_overlay
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(context_items=None)
    try:
        choices = overlay.context_choices()
        assert [item["id"] for item in choices] == [
            "ambient",
            "browser",
            "selection",
            "clipboard",
            "screenshot",
            "github",
            "memory",
            "files",
        ]
        assert overlay._context_chip_width() <= intent_overlay._CTX_CHIP_W
        top = intent_overlay._PAD_V + (
            intent_overlay._CONV_H if overlay._show_conversation_selector else 0
        ) + intent_overlay._CTX_TOP
        rects = overlay._context_chip_rects(top)
        assert len(rects) == 8
        assert rects[-1][0]["id"] == "files"
        assert rects[-1][1].right() <= intent_overlay._W - intent_overlay._PAD_H

        center = rects[-1][1].center()
        assert overlay._context_item_at(QPoint(center.x(), center.y()))["id"] == "files"
    finally:
        overlay.close()
        qapp.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_bottom_context_previews_resize(qapp):
    """Verify enabled context previews appear below intent rows and resize."""
    from ui import intent_overlay
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {"id": "ambient", "key": "1", "label": "App", "state": "on", "preview": "This is app context"},
            {"id": "browser", "key": "2", "label": "Browser/Web", "state": "on", "preview": "This is browser context"},
            {"id": "clipboard", "key": "4", "label": "Clipboard", "state": "off", "preview": "Hidden clipboard"},
        ]
    )
    try:
        assert overlay._context_preview_entries() == [
            ("App", "This is app context", "ambient", ""),
            ("Browser/Web", "This is browser context", "browser", ""),
        ]
        assert overlay._context_preview_height() == (
            intent_overlay._CTX_PREVIEW_TOP + intent_overlay._CTX_PREVIEW_LINE_H * 2
        )
        initial_h = overlay.height()

        overlay.update_context_items([
            {"id": "ambient", "key": "1", "label": "App", "state": "off", "preview": ""},
            {"id": "browser", "key": "2", "label": "Browser/Web", "state": "on", "preview": "Browser"},
            {"id": "selection", "key": "3", "label": "Selection", "state": "on", "preview": "Selection"},
            {"id": "clipboard", "key": "4", "label": "Clipboard", "state": "on", "preview": "Clipboard"},
            {"id": "memory", "key": "6", "label": "Memory", "state": "auto", "preview": "Memory"},
        ])

        assert overlay._context_preview_entries() == [
            ("Browser/Web", "Browser", "browser", ""),
            ("Selection", "Selection", "selection", ""),
            ("Clipboard", "Clipboard", "clipboard", ""),
        ]
        expanded_h = initial_h + intent_overlay._CTX_PREVIEW_LINE_H
        assert overlay.height() == expanded_h

        assert overlay._cycle_context_key("2") is True
        assert overlay._context_preview_entries() == [
            ("Selection", "Selection", "selection", ""),
            ("Clipboard", "Clipboard", "clipboard", ""),
            ("Memory", "Memory", "memory", ""),
        ]
        assert overlay.height() == expanded_h
    finally:
        overlay.close()
        qapp.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_cycles_context_chip(monkeypatch):
    """Verify numeric context chips cycle independently of intent rows."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "browser", "key": "2", "label": "Browser", "state": "on"}],
    )
    try:
        overlay.update_context_items(
            [{"id": "browser", "key": "2", "label": "Browser", "state": "auto", "tokens": "? tok"}]
        )
        assert overlay.context_choices()[0]["state"] == "auto"
        assert overlay.context_choices()[0]["touched"] is False

        assert overlay._cycle_context_key("2") is True
        assert overlay.context_choices()[0]["state"] == "off"
        assert overlay.context_choices()[0]["touched"] is True

        overlay.update_context_items(
            [{"id": "browser", "key": "2", "label": "Browser", "state": "auto", "tokens": "? tok"}]
        )
        assert overlay.context_choices()[0]["state"] == "off"
        assert overlay.context_choices()[0]["touched"] is True
        assert intent_overlay._context_chip_token_text(overlay.context_choices()[0]) == "? tok"

        overlay.show()
        app.processEvents()
        QTest.mouseClick(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=QPoint(
                intent_overlay._PAD_H + intent_overlay._CTX_CHIP_W // 2,
                intent_overlay._PAD_V
                + intent_overlay._CONV_H
                + intent_overlay._CTX_TOP
                + intent_overlay._CTX_CHIP_H // 2,
            ),
        )
        app.processEvents()
        assert overlay.context_choices()[0]["state"] == "on"

        assert overlay._cycle_context_key("2") is True
        assert overlay.context_choices()[0]["state"] == "off"
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_screenshot_chip_requests_snip_when_enabled_from_off(qapp):
    """Verify turning Screenshot on asks for a snip instead of silently capturing."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[{"id": "screenshot", "key": "5", "label": "Screenshot", "state": "off"}]
    )
    requested: list[bool] = []
    overlay.screenshot_snip_requested.connect(lambda: requested.append(True))
    try:
        assert overlay._cycle_context_key("5") is True
        qapp.processEvents()

        assert overlay.context_choices()[0]["state"] == "on"
        assert requested == [True]

        assert overlay._cycle_context_key("5") is True
        qapp.processEvents()

        assert overlay.context_choices()[0]["state"] == "off"
        assert requested == [True]
    finally:
        overlay.close()
        qapp.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_context_item_force_state_overrides_user_touched_choice(qapp):
    """Verify a cancelled snip can force Screenshot back off."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[{"id": "screenshot", "key": "5", "label": "Screenshot", "state": "off"}]
    )
    try:
        assert overlay._cycle_context_key("5") is True
        assert overlay.context_choices()[0]["state"] == "on"
        assert overlay.context_choices()[0]["touched"] is True

        overlay.update_context_items([
            {
                "id": "screenshot",
                "key": "5",
                "label": "Screenshot",
                "state": "off",
                "force_state": True,
            }
        ])

        assert overlay.context_choices()[0]["state"] == "off"
        assert overlay.context_choices()[0]["touched"] is False
    finally:
        overlay.close()
        qapp.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_conversation_choice_toggles_new_and_continue():
    """Verify the intent overlay exposes the selected conversation mode."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        conversation_options=[
            {"index": 1, "title": "Latest chat", "selected": True},
            {"index": 0, "title": "Older chat"},
        ],
    )
    try:
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}
        assert overlay.conversation_choice_touched() is False

        overlay._toggle_conversation_mode()
        assert overlay.conversation_choice() == {"mode": "new"}
        assert overlay.conversation_choice_touched() is True

        overlay._set_conversation_choice(0)
        assert overlay.conversation_choice() == {"mode": "continue", "index": 0}
        assert overlay.conversation_choice_touched() is True
    finally:
        overlay.close()
        app.processEvents()


def test_first_prompt_only_context_defaults_follow_conversation_mode(qapp, monkeypatch):
    """Continuations start Off, while New chat restores untouched caller defaults."""
    import config
    from ui.intent_overlay import IntentOverlay

    monkeypatch.setattr(config, "CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY", True, raising=False)
    overlay = IntentOverlay(
        context_items=[
            {"id": "ambient", "key": "1", "label": "App", "state": "on"},
            {"id": "browser", "key": "2", "label": "Browser", "state": "auto"},
            {"id": "attachments", "key": "", "label": "Attachments", "state": "on", "locked": True},
        ],
        conversation_options=[{"index": 1, "title": "Latest chat", "selected": True}],
    )
    try:
        states = {item["id"]: item["state"] for item in overlay.context_choices()}
        assert states == {"ambient": "off", "browser": "off", "attachments": "on"}

        overlay._cycle_context_key("1")
        assert next(item for item in overlay.context_choices() if item["id"] == "ambient")["state"] == "on"

        overlay._toggle_conversation_mode()
        states = {item["id"]: item["state"] for item in overlay.context_choices()}
        assert states == {"ambient": "on", "browser": "auto", "attachments": "on"}

        overlay._set_conversation_choice(1)
        states = {item["id"]: item["state"] for item in overlay.context_choices()}
        assert states == {"ambient": "on", "browser": "off", "attachments": "on"}
    finally:
        _close_overlay_if_valid(overlay, qapp)


@pytest.mark.parametrize("caller_idx", [0, 1])
def test_space_toggles_conversation_for_intent_and_action_overlays(qapp, caller_idx):
    """Space invokes the same new/continue toggle in either overlay."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        caller_idx=caller_idx,
        conversation_options=[{"index": 1, "title": "Previous chat"}],
    )
    try:
        assert overlay.conversation_choice() == {"mode": "new"}

        overlay.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Space,
                Qt.KeyboardModifier.NoModifier,
                " ",
            )
        )
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}

        overlay.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Space,
                Qt.KeyboardModifier.NoModifier,
                " ",
            )
        )
        assert overlay.conversation_choice() == {"mode": "new"}
        assert overlay.conversation_choice_touched() is True
    finally:
        _close_overlay_if_valid(overlay, qapp)


def test_windows_raw_space_toggle_is_not_repeated_by_qt(qapp):
    """A forwarded Windows Space press toggles only once if Qt also sees it."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        conversation_options=[{"index": 1, "title": "Previous chat"}],
    )
    try:
        assert "space" in overlay._raw_shortcut_names()

        overlay._on_raw_key("space")
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}

        overlay.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Space,
                Qt.KeyboardModifier.NoModifier,
                " ",
            )
        )
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}
    finally:
        _close_overlay_if_valid(overlay, qapp)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_conversation_row_is_split_mode_and_list(monkeypatch):
    """Verify left chat segment toggles mode and right segment opens history."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMenu

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    popups: list[list[object]] = []

    def fake_popup(self, _pos):
        """Capture menu entries without showing a native popup."""
        popups.append([action.data() for action in self.actions()])
        return None

    monkeypatch.setattr(QMenu, "popup", fake_popup)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        conversation_options=[
            {"index": 1, "title": "Latest chat"},
            {"index": 0, "title": "Older chat"},
        ],
    )
    try:
        overlay.show()
        app.processEvents()
        overlay.repaint()
        app.processEvents()

        assert overlay.conversation_choice() == {"mode": "new"}
        assert not overlay._conversation_mode_rect.isNull()
        assert not overlay._conversation_list_rect.isNull()

        QTest.mouseClick(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=overlay._conversation_list_rect.center(),
        )
        app.processEvents()
        assert popups == []
        assert overlay.conversation_choice() == {"mode": "new"}

        QTest.mouseClick(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=overlay._conversation_mode_rect.center(),
        )
        app.processEvents()
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}

        QTest.mouseClick(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=overlay._conversation_list_rect.center(),
        )
        app.processEvents()
        assert popups[-1] == [1, 0]
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_changes_restart_timeout_countdown(monkeypatch):
    """Verify context and conversation changes restart the overlay timeout."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    restarts: list[bool] = []
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "browser", "key": "2", "label": "Browser", "state": "off"}],
        conversation_options=[{"index": 1, "title": "Latest chat"}],
    )
    monkeypatch.setattr(overlay, "_restart_timer", lambda: restarts.append(True))
    try:
        assert overlay._cycle_context_key("2") is True
        overlay._toggle_conversation_mode()

        assert len(restarts) == 2
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_picker_timeout_failure_matrix_is_controlled(monkeypatch):
    """Invalid, zero, stale, and modal-owned timeout states stay controlled."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)

    # Invalid runtime config must not prevent the real picker from opening.
    monkeypatch.setattr(config, "INTENT_OVERLAY_TIMEOUT_MS", "not-a-duration")
    invalid = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        assert invalid._overlay_timeout_ms == intent_overlay._AUTO_CLOSE_MS
        assert invalid._timer.isActive()

        # A state transition must retire the old timer instead of allowing it
        # to cancel a custom prompt later.
        invalid._custom_mode = True
        invalid._restart_timer()
        assert not invalid._timer.isActive()
    finally:
        invalid.close()
        app.processEvents()

    # Zero is an intentional no-timeout mode and remains inactive across use.
    monkeypatch.setattr(config, "INTENT_OVERLAY_TIMEOUT_MS", 0)
    persistent = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        assert persistent._overlay_timeout_ms == 0
        assert not persistent._timer.isActive()
        persistent._note_interaction()
        assert not persistent._timer.isActive()
    finally:
        persistent.close()
        app.processEvents()

    # A modal taking focus cannot make timeout cancellation re-entrant or emit
    # cancellation more than once.
    monkeypatch.setattr(config, "INTENT_OVERLAY_TIMEOUT_MS", 50)
    modal_owned = intent_overlay.IntentOverlay(caller_idx=0)
    modal = QDialog()
    cancelled: list[bool] = []
    modal_owned.cancelled.connect(lambda: cancelled.append(True))
    try:
        modal.setModal(True)
        modal.show()
        app.processEvents()
        modal_owned._cancel()
        modal_owned._cancel()
        assert cancelled == [True]
    finally:
        modal.close()
        _close_overlay_if_valid(modal_owned, app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_defaults_to_new_when_history_has_no_active_selection():
    """Verify loaded history does not imply continuation on app start."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        conversation_options=[
            {"index": 1, "title": "Latest chat"},
            {"index": 0, "title": "Older chat"},
        ],
    )
    try:
        assert overlay.conversation_choice() == {"mode": "new"}

        overlay._toggle_conversation_mode()
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_project_choice_filters_conversations():
    """Verify project selection scopes the intent overlay chat list."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        project_options=[
            {"id": "general", "name": "General"},
            {"id": "proj-1", "name": "Personal OS"},
        ],
        active_project_id="proj-1",
        conversation_options=[
            {"index": 2, "title": "Project chat", "project_id": "proj-1", "selected": True},
            {"index": 1, "title": "General chat", "project_id": "general"},
        ],
    )
    try:
        assert overlay.project_choice() == {"mode": "existing", "project_id": "proj-1"}
        assert overlay.conversation_choice() == {"mode": "continue", "index": 2}
        assert [item["index"] for item in overlay._filtered_conversation_options()] == [2]

        overlay._set_project_choice("general")

        assert overlay.project_choice() == {"mode": "existing", "project_id": "general"}
        assert overlay.conversation_choice() == {"mode": "new"}
        assert [item["index"] for item in overlay._filtered_conversation_options()] == [1]
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_dedupes_raw_and_qt_context_key(monkeypatch):
    """Verify a Windows raw-hook context key is not immediately toggled again by Qt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "browser", "key": "2", "label": "Browser", "state": "on"}],
    )
    try:
        overlay._on_raw_key("2")

        assert overlay.context_choices()[0]["state"] == "off"
        assert overlay._is_duplicate_qt_context_key("2") is True
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_linux_uses_qt_keys_without_pynput(monkeypatch):
    """Verify Linux overlay-local shortcuts do not start a second native listener."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import builtins

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    original_import = builtins.__import__
    grabs: list[QWidget] = []
    releases: list[QWidget] = []

    def guarded_import(name, *args, **kwargs):
        """Fail if showing the overlay tries to import pynput."""
        if name == "pynput" or name.startswith("pynput."):
            raise AssertionError("Linux intent overlay should rely on Qt key events")
        return original_import(name, *args, **kwargs)

    def grab_keyboard(self):
        """Record Qt-local keyboard grabs."""
        grabs.append(self)

    def release_keyboard(self):
        """Record Qt-local keyboard releases."""
        releases.append(self)

    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "browser", "key": "2", "label": "Browser", "state": "on"}],
    )
    try:
        overlay.show()
        app.processEvents()

        assert overlay._kb_hook is None
        assert overlay.windowFlags() & Qt.WindowType.WindowType_Mask == Qt.WindowType.Window
        assert grabs == [overlay]
        assert overlay._overlay_grabbed_keyboard is True
        QTest.keyClick(overlay, Qt.Key.Key_2)
        app.processEvents()
        assert overlay.context_choices()[0]["state"] == "off"

        overlay._unhook()

        assert releases == [overlay]
        assert overlay._overlay_grabbed_keyboard is False
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_linux_visible_picker_accepts_letter_shortcut(monkeypatch):
    """Verify Linux visible intent picker handles letter shortcuts through Qt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    chosen: list[tuple[str, str]] = []

    def grab_keyboard(self):
        """Allow Qt-local keyboard grabs without touching the host desktop."""

    def release_keyboard(self):
        """Allow Qt-local keyboard releases without touching the host desktop."""

    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [
        {
            "intents": [
                {"key": "w", "label": "Write", "hint": "", "prompt": "write this"},
            ],
            "custom_key": "s",
        }
    ]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    try:
        overlay.show()
        app.processEvents()

        QTest.keyClick(overlay, Qt.Key.Key_W)
        QTest.qWait(120)
        app.processEvents()

        assert chosen == [("W", "write this")]
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_frozen_linux_avoids_keyboard_grabs(monkeypatch):
    """Verify frozen Linux builds do not use native Qt keyboard grabs."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    chosen: list[tuple[str, str]] = []
    grabs: list[QWidget] = []
    releases: list[QWidget] = []

    def grab_keyboard(self):
        """Record unexpected Qt-native keyboard grabs."""
        grabs.append(self)

    def release_keyboard(self):
        """Record unexpected Qt-native keyboard releases."""
        releases.append(self)

    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(intent_overlay, "_IS_LINUX", True)
    monkeypatch.setattr(intent_overlay.sys, "frozen", True, raising=False)
    monkeypatch.delenv("OPENWAND_LINUX_QT_KEYBOARD_GRAB", raising=False)
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [
        {
            "intents": [
                {"key": "w", "label": "Write", "hint": "", "prompt": "write this"},
            ],
            "custom_key": "s",
        }
    ]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    try:
        overlay.show()
        app.processEvents()

        QTest.keyClick(overlay, Qt.Key.Key_W)
        QTest.qWait(120)
        app.processEvents()

        assert chosen == [("W", "write this")]
        assert grabs == []
        assert releases == []
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_frozen_linux_custom_prompt_avoids_keyboard_grabs(monkeypatch):
    """Verify frozen Linux custom prompt focus does not use native Qt grabs."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    grabs: list[QWidget] = []
    releases: list[QWidget] = []

    def grab_keyboard(self):
        """Record unexpected Qt-native keyboard grabs."""
        grabs.append(self)

    def release_keyboard(self):
        """Record unexpected Qt-native keyboard releases."""
        releases.append(self)

    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(intent_overlay, "_IS_LINUX", True)
    monkeypatch.setattr(intent_overlay.sys, "frozen", True, raising=False)
    monkeypatch.delenv("OPENWAND_LINUX_QT_KEYBOARD_GRAB", raising=False)
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay.show()
        app.processEvents()

        QTest.keyClick(overlay, Qt.Key.Key_S)
        QTest.qWait(120)
        app.processEvents()

        assert overlay._custom_mode is True
        assert overlay._input_line.isHidden() is False
        assert overlay._input_grabbed_keyboard is False
        assert grabs == []
        assert releases == []
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_linux_moves_keyboard_grab_to_custom_input(monkeypatch):
    """Verify Linux custom prompt typing gets a Qt-local keyboard grab."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    grabs: list[QWidget] = []
    releases: list[QWidget] = []

    def grab_keyboard(self):
        """Record Qt-local keyboard grabs."""
        grabs.append(self)

    def release_keyboard(self):
        """Record Qt-local keyboard releases."""
        releases.append(self)

    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._focus_overlay()
        overlay._enter_custom_mode()

        assert grabs == [overlay, overlay._input_line]
        assert releases == [overlay]
        assert overlay._overlay_grabbed_keyboard is False
        assert overlay._input_grabbed_keyboard is True

        overlay._unhook()

        assert releases == [overlay, overlay._input_line]
        assert overlay._input_grabbed_keyboard is False
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_deferred_intent_overlay_stays_inert_until_context_activation(monkeypatch):
    """The early-rendered picker must not steal focus while context is captured."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    focused: list[bool] = []
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    config.CALLER_ROWS[:] = [
        {
            "intents": [{"key": "w", "label": "Write", "hint": "", "prompt": "Write"}],
            "custom_key": "s",
        }
    ]
    overlay = intent_overlay.IntentOverlay(caller_idx=0, defer_focus=True)
    monkeypatch.setattr(overlay, "_focus_overlay", lambda: focused.append(True))
    try:
        overlay.show()
        app.processEvents()

        assert overlay.isVisible()
        assert overlay.isEnabled() is False
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is True
        assert overlay._interaction_started is False
        assert focused == []

        overlay.activate_after_context()

        assert overlay.isEnabled() is True
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is False
        assert overlay._interaction_started is True
        assert focused == [True]
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_apply_intent_context_choices_updates_caller_policy():
    """Verify overlay context choices become real per-prompt caller policy."""
    from runtime.supervisor.flows import FlowController

    caller = {
        "context_ambient": True,
        "context_documents_mode": "off",
        "context_browser_mode": "auto",
        "context_github_mode": "off",
        "context_memory_mode": "on",
        "context_screenshot": "auto",
        "file_access": "ask",
    }

    updated = FlowController._apply_intent_context_choices(
        caller,
        [
            {"id": "browser", "state": "off"},
            {"id": "selection", "state": "off"},
            {"id": "github", "state": "auto"},
            {"id": "memory", "state": "auto"},
            {"id": "files", "state": "off"},
            {"id": "ambient", "state": "on", "default_state": "off", "touched": True},
        ],
    )

    assert updated["context_documents_mode"] == "auto"
    assert updated["context_browser_mode"] == "off"
    assert updated["context_github_mode"] == "model"
    assert updated["_context_selection_enabled"] is False
    assert updated["context_memory_mode"] == "model"
    assert updated["file_access"] == "off"

    unchanged = FlowController._apply_intent_context_choices(
        caller,
        [{"id": "ambient", "state": "on", "default_state": "on"}],
    )
    assert unchanged["context_documents_mode"] == "off"


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_remove_buttons_remove_rows_and_disable_groups(qapp):
    """Verify per-row X removal drops sources and an emptied group turns off."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {
                "id": "ambient",
                "key": "1",
                "label": "App",
                "state": "on",
                "sources": [
                    {"label": "Doc A", "preview": "alpha"},
                    {"label": "Doc B", "preview": "beta"},
                ],
            },
            {"id": "clipboard", "key": "4", "label": "Clipboard", "state": "on", "preview": "Clip"},
        ]
    )
    removed: list[tuple[str, str]] = []
    overlay.context_source_removed.connect(
        lambda item_id, source_id: removed.append((item_id, source_id))
    )
    try:
        overlay._remove_context_entry("ambient", "Doc A")
        assert removed == [("ambient", "Doc A")]
        assert ("Doc B", "beta", "ambient", "Doc B") in overlay._context_preview_entries()
        choices = {c["id"]: c for c in overlay.context_choices()}
        assert choices["ambient"]["state"] == "on"

        overlay._remove_context_entry("ambient", "Doc B")
        assert removed == [("ambient", "Doc A"), ("ambient", "Doc B")]
        choices = {c["id"]: c for c in overlay.context_choices()}
        assert choices["ambient"]["state"] == "off"
        assert choices["ambient"]["touched"] is True

        overlay._remove_context_entry("clipboard", "")
        choices = {c["id"]: c for c in overlay.context_choices()}
        assert choices["clipboard"]["state"] == "off"
        assert choices["clipboard"]["touched"] is True
        assert overlay._context_preview_entries() == []
    finally:
        _close_overlay_if_valid(overlay, qapp)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_lists_chrome_and_edge_as_separate_browser_rows(qapp):
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {
                "id": "browser",
                "key": "2",
                "label": "Browser/Web",
                "state": "on",
                "sources": [
                    {
                        "id": "browser:701",
                        "app": "Chrome",
                        "label": "Guide.pdf",
                        "preview": "PDF preparation guide",
                    },
                    {
                        "id": "browser:702",
                        "app": "Edge",
                        "label": "Project site",
                        "preview": "Website project plan",
                    },
                ],
            }
        ]
    )
    try:
        assert overlay._context_preview_entries() == [
            ("Chrome: Guide.pdf", "PDF preparation guide", "browser", "browser:701"),
            ("Edge: Project site", "Website project plan", "browser", "browser:702"),
        ]
        overlay._remove_context_entry("browser", "browser:701")
        assert overlay._context_preview_entries() == [
            ("Edge: Project site", "Website project plan", "browser", "browser:702")
        ]
    finally:
        _close_overlay_if_valid(overlay, qapp)


def test_intent_overlay_stale_selection_toggle_skips_interactive_capture(qapp):
    """Verify enabling a stale Selection chip does not start a new capture."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {
                "id": "selection",
                "key": "3",
                "label": "Selection",
                "available": True,
                "state": "off",
                "stale": True,
                "tokens": "~12 tok",
                "preview": "earlier words",
            }
        ]
    )
    captures: list[str] = []
    overlay.selection_capture_requested.connect(captures.append)
    try:
        assert overlay._cycle_context_key("3") is True
        qapp.processEvents()
        selection = overlay.context_choices()[0]
        assert selection["state"] == "on"
        assert captures == []
        assert ("Selection", "earlier words", "selection", "") in overlay._context_preview_entries()
    finally:
        _close_overlay_if_valid(overlay, qapp)


def test_intent_overlay_selection_toggle_can_disable_interactive_capture(qapp):
    """Verify Linux-style Selection chips toggle without requesting a new selection."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {
                "id": "selection",
                "key": "3",
                "label": "Selection",
                "available": True,
                "state": "off",
                "capture_on_enable": False,
                "tokens": "~12 tok",
                "preview": "last selected words",
            }
        ]
    )
    captures: list[str] = []
    overlay.selection_capture_requested.connect(captures.append)
    try:
        assert overlay._cycle_context_key("3") is True
        qapp.processEvents()
        selection = overlay.context_choices()[0]
        assert selection["state"] == "on"
        assert captures == []
        assert ("Selection", "last selected words", "selection", "") in overlay._context_preview_entries()
    finally:
        _close_overlay_if_valid(overlay, qapp)


def test_intent_overlay_previews_wrap_to_two_lines(qapp):
    """Verify long context previews paint on two lines and short ones on one."""
    from PySide6.QtGui import QFontMetrics

    from ui import intent_overlay
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {
                "id": "clipboard",
                "key": "4",
                "label": "Clipboard",
                "state": "on",
                "preview": "word " * 60,
            },
        ]
    )
    try:
        rows = overlay._context_preview_layout()
        assert len(rows) == 1
        assert len(rows[0][1]) == 2
        assert overlay._context_preview_height() == (
            intent_overlay._CTX_PREVIEW_TOP + intent_overlay._CTX_PREVIEW_LINE_H * 2
        )
        fm = QFontMetrics(overlay._preview_value_font())
        assert overlay._preview_wrap_lines(fm, "tiny", 400) == ["tiny"]
        two = overlay._preview_wrap_lines(fm, "alpha beta " * 40, 120)
        assert len(two) == 2
        assert two[0]
    finally:
        _close_overlay_if_valid(overlay, qapp)


def test_selection_context_chip_can_start_capture_when_empty(qapp):
    """Verify empty Selection metadata does not block the capture chip."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {
                "id": "selection",
                "key": "3",
                "label": "Selection",
                "available": True,
                "state": "on",
                "tokens": "~12 tok",
            }
        ]
    )
    try:
        overlay.update_context_items([
            {
                "id": "selection",
                "key": "3",
                "label": "Selection",
                "available": False,
                "state": "off",
                "tokens": "",
            }
        ])
        selection = overlay.context_choices()[0]
        assert selection["state"] == "on"
        assert selection["touched"] is False
        assert selection["tokens"] == ""

        assert overlay._cycle_context_key("3") is True
        selection = overlay.context_choices()[0]
        assert selection["state"] == "off"
        assert selection["touched"] is True
        assert overlay._cycle_context_key("3") is True
        selection = overlay.context_choices()[0]
        assert selection["state"] == "on"
        assert selection["touched"] is True
    finally:
        overlay.close()
        qapp.processEvents()
