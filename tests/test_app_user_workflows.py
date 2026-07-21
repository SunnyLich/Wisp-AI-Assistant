"""Broad user-visible workflow tests for Wisp.

These tests intentionally exercise product functions through the same seams a
user touches: project/chat state, context choices, prompt assembly, UI widgets,
memory scope, file/tool permissions, and route/stream contracts. External
boundaries stay fake unless a test is explicitly opt-in elsewhere.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.workflow


@pytest.fixture(autouse=True)
def _use_builtin_privacy_in_offline_workflows(monkeypatch: pytest.MonkeyPatch):
    """Keep deterministic workflow tests independent from a user's installed AI model."""
    import config

    monkeypatch.setattr(config, "CHAT_EXECUTION_MODE", "wisp", raising=False)
    monkeypatch.setattr(config, "CHAT_CONVERSATION_OWNER", "wisp", raising=False)
    monkeypatch.setattr(config, "PRIVACY_MODE", "builtin", raising=False)
    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True, raising=False)
    monkeypatch.setattr(config, "PRIVACY_AI_ENABLED", False, raising=False)


@dataclass(frozen=True)
class IsolatedAppState:
    root: Path
    chats: Path
    attachments: Path
    memory: Path


def _is_relative_to(path: Path, root: Path) -> bool:
    path = path.resolve()
    root = root.resolve()
    return path == root or root in path.parents


@pytest.fixture
def isolated_app_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IsolatedAppState:
    """Patch app stores to temp paths so workflow tests cannot touch user data."""
    from core.conversation_store import store as conversation_store
    from core.memory_store import store as memory_store

    root = tmp_path / "wisp-user-state"
    chats = root / "chats"
    attachments = chats / "attachments"
    memory = root / "memory"

    monkeypatch.setattr(conversation_store, "CHATS_DIR", chats)
    monkeypatch.setattr(conversation_store, "CHAT_ATTACHMENTS_DIR", attachments)
    monkeypatch.setattr(conversation_store, "PROJECTS_FILE", chats / "projects.json")
    monkeypatch.setattr(conversation_store, "CONVERSATIONS_FILE", chats / "conversations.json")

    monkeypatch.setattr(memory_store, "_MEMORY_DIR", str(memory))
    monkeypatch.setattr(memory_store, "_FALLBACK_PATH", str(memory / "facts_fallback.json"))
    monkeypatch.setattr(memory_store, "_manager", None)
    monkeypatch.setattr(memory_store.config, "MEMORY_AUTO_CONSOLIDATE", False, raising=False)
    memory_store.set_active_project(None)

    state = IsolatedAppState(root=root, chats=chats, attachments=attachments, memory=memory)
    for path in (state.chats, state.attachments, state.memory):
        assert _is_relative_to(path, state.root)
    yield state
    memory_store.set_active_project(None)
    monkeypatch.setattr(memory_store, "_manager", None)


@pytest.fixture
def qapp():
    """Return an offscreen QApplication, or skip when PySide6 is unavailable."""
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["wisp-workflow-tests"])


def _pump_until(app: Any, predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def _ensure_brain_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    brain_dir = repo_root / "runtime" / "brain"
    if str(brain_dir) not in sys.path:
        sys.path.insert(0, str(brain_dir))


def _recording_stream_context(req_id: Any = "workflow"):
    _ensure_brain_path()
    from wisp_brain.handlers import StreamContext

    events: list[tuple[str, Any]] = []
    ctx = StreamContext(lambda event, data, _rid: events.append((event, data)), req_id)
    return events, ctx


def _minimal_agent_spec(scope: Path) -> dict[str, Any]:
    return {
        "title": "Workflow Agent",
        "objective": "Inspect the sample project and report what exists.",
        "scope_folder": str(scope),
        "sandbox_mode": "workspace-write: scope folder only",
        "approval_policy": "ask before escalation",
        "provider": "fake",
        "model": "workflow-fake",
        "reasoning_effort": "low",
        "max_runtime_minutes": 1,
        "max_turns": 1,
        "allow_shell": False,
        "allow_network": False,
        "allow_git": False,
        "allow_file_create": False,
        "allow_file_edit": False,
        "allow_file_delete": False,
        "required_context": "Use read-only inspection only.",
        "completion_criteria": "A final report exists.",
        "agents": [
            {
                "name": "Builder",
                "role": "Implementer",
                "provider": "same as task",
                "model": "same as task",
                "responsibility": "Read the scope and summarize it.",
            }
        ],
    }


def test_workflow_state_isolation_guard_and_memory_project_scope(
    isolated_app_state: IsolatedAppState,
    runtime_state_guard,
):
    """A real project/conversation/memory workflow stays scoped to temp state."""
    from core.conversation_store import store as conversations
    from core.memory_store import store as memory

    runtime_state_guard.validate_json_under(isolated_app_state.root)

    project_a = conversations.add_project("Aurora")
    project_b = conversations.add_project("Borealis")
    conv = {
        "id": "conv-aurora",
        "project_id": project_a["id"],
        "messages": [
            {"role": "user", "content": "Remember that this project codename is Aurora Nebula."},
            {"role": "assistant", "content": "Stored."},
        ],
        "context": "[Conversation Context]\nProject test conversation.",
    }
    conversations.save_conversations([conv])

    assert _is_relative_to(conversations.PROJECTS_FILE, isolated_app_state.root)
    assert _is_relative_to(conversations.CONVERSATIONS_FILE, isolated_app_state.root)
    assert conversations.load_conversations()[0]["project_id"] == project_a["id"]

    memory.set_active_project(project_a["id"])
    manager = memory.get_manager()
    saved = manager.save_memory("This project codename is Aurora Nebula.")
    assert saved["ok"] is True
    assert saved["scope"] == "project"
    assert saved["project"] == project_a["id"]

    saved_general = manager.save_memory("The user prefers compact workflow reports.", scope="general")
    assert saved_general["ok"] is True
    rejected = manager.save_memory("api_key sk-proj-abcdefghijklmnopqrstuvwxyz1234567890")  # secret-scan: allow
    assert rejected["ok"] is False

    aurora_memory = manager.retrieve_relevant("what do you remember", project_id=project_a["id"])
    other_memory = manager.retrieve_relevant("what do you remember", project_id=project_b["id"])
    assert "Aurora Nebula" in aurora_memory
    assert "Aurora Nebula" not in other_memory
    assert "compact workflow reports" in aurora_memory
    assert "compact workflow reports" in other_memory

    facts_path = Path(memory._FALLBACK_PATH)
    assert _is_relative_to(facts_path, isolated_app_state.root)
    assert facts_path.exists()


def test_brain_query_workflow_assembles_context_and_redacts_secrets(
    isolated_app_state: IsolatedAppState,
    monkeypatch: pytest.MonkeyPatch,
):
    """The normal query handler proves selected/doc/memory context reaches the model."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    from core.memory_store import store as memory

    memory.set_active_project("project-1")
    memory.get_manager().add_fact_manual(
        "The project marker phrase is workflow quartz.",
        "project_context",
        project="project-1",
    )

    events, ctx = _recording_stream_context("workflow-query")
    from wisp_brain import handlers

    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="Use all context to answer.",
        selected="Selected traceback text",
        ambient_text="[Browser/Web]\nCurrent browser article.",
        active_document_text="Open document notes with password=supersecret",
        include_active_document=True,
        context_priority="Browser/Web",
        memory_enabled=True,
        memory_project="project-1",
        use_tools=False,
    )

    text = result["text"]
    assert "[fake-llm]" in text
    assert "Selected traceback text" in text
    assert "[Browser/Web]" in text
    assert "[Context priority]\nPrioritize Browser/Web" in text
    assert "password=supersecret" in text
    assert result["privacy_report"]["categories"]["credential"] == 1
    assert any(event == "reply.chunk" for event, _data in events)
    assert any(event == "reply.done" and data.get("text") == text for event, data in events)
    done = next(data for event, data in events if event == "reply.done")
    assert done["privacy_report"]["count"] >= 1


def test_stream_cancel_stops_query_and_returns_partial_result(
    monkeypatch: pytest.MonkeyPatch,
):
    """Cancelling a stream leaves a partial done payload instead of hanging stale UI."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    _ensure_brain_path()
    from wisp_brain import handlers

    events: list[tuple[str, Any]] = []
    ctx: Any = None

    def emit_and_cancel(event: str, data: Any, req_id: Any) -> None:
        events.append((event, data))
        assert req_id == "cancel-flow"
        if event == "reply.chunk":
            ctx.cancelled = True

    ctx = handlers.StreamContext(emit_and_cancel, "cancel-flow")
    result = handlers.brain_query(
        ctx,
        intent_prompt="Cancel after the first streamed token",
        selected="This selected text should not all stream.",
        memory_enabled=False,
    )

    chunks = [payload["text"] for event, payload in events if event == "reply.chunk"]
    assert len(chunks) == 1
    assert result["text"] == chunks[0]
    assert events[-1][0] == "reply.done"
    assert events[-1][1]["text"] == chunks[0]


def test_intent_overlay_project_conversation_and_context_chip_workflow(qapp, monkeypatch: pytest.MonkeyPatch):
    """Intent overlay exposes project/chat choice and stable context chip state."""
    monkeypatch.setattr(sys, "platform", "darwin")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMenu
    import config
    from runtime.supervisor.flows import FlowController
    from ui.intent_overlay import IntentOverlay

    monkeypatch.setattr(config, "INTENT_CONTEXT_TOGGLE_KEYS", "12345678", raising=False)
    screenshot_tokens = FlowController._screen_token_label({"screen_size": {"width": 1440, "height": 900}})
    assert screenshot_tokens not in {"", "0 tok", "? tok"}

    context_items = [
        {"id": "ambient", "key": "1", "label": "App", "state": "on", "tokens": "~4 tok"},
        {"id": "browser", "key": "2", "label": "Browser/Web", "state": "on", "tokens": "~8 tok"},
        {"id": "selection", "key": "3", "label": "Selection", "state": "on", "tokens": "~5 tok"},
        {"id": "clipboard", "key": "4", "label": "Clipboard", "state": "off", "tokens": "? tok"},
        {"id": "screenshot", "key": "5", "label": "Screenshot", "state": "auto", "tokens": screenshot_tokens},
        {"id": "github", "key": "6", "label": "Git/GitHub", "state": "auto", "tokens": "? tok"},
        {"id": "memory", "key": "7", "label": "Memory", "state": "auto", "tokens": "? tok"},
        {"id": "files", "key": "8", "label": "Files", "state": "auto", "tokens": ""},
    ]
    overlay = IntentOverlay(
        context_items=context_items,
        project_options=[{"id": "p1", "name": "Project One"}, {"id": "p2", "name": "Project Two"}],
        conversation_options=[
            {"index": 0, "title": "P1 old", "project_id": "p1", "selected": True},
            {"index": 2, "title": "P2 old", "project_id": "p2"},
        ],
        active_project_id="p1",
    )

    chosen: list[tuple[str, str]] = []
    cancelled: list[bool] = []
    menus = []
    monkeypatch.setattr(QMenu, "popup", lambda menu, _pos: menus.append(menu))
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    overlay.cancelled.connect(lambda: cancelled.append(True))
    try:
        overlay.show()
        qapp.processEvents()
        assert overlay.project_choice() == {"mode": "existing", "project_id": "p1"}
        assert overlay.conversation_choice() == {"mode": "continue", "index": 0}
        assert {item["id"] for item in overlay.context_choices()} == {
            "ambient", "browser", "selection", "clipboard", "screenshot", "github", "memory", "files"
        }

        QTest.keyClick(overlay, Qt.Key.Key_3)
        qapp.processEvents()
        selection = next(item for item in overlay.context_choices() if item["id"] == "selection")
        assert selection["state"] == "off"
        assert selection["touched"] is True

        overlay.update_context_items([
            {**item, "tokens": "~99 tok" if item["id"] == "selection" else item["tokens"]}
            for item in context_items
        ])
        selection = next(item for item in overlay.context_choices() if item["id"] == "selection")
        assert selection["state"] == "off"
        assert selection["tokens"] == "~99 tok"

        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=overlay._project_rect.center())
        project_menu = menus[-1]
        next(action for action in project_menu.actions() if action.text() == "Project Two").trigger()
        assert overlay.project_choice() == {"mode": "existing", "project_id": "p2"}
        assert overlay.conversation_choice() == {"mode": "new"}
        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=overlay._conversation_mode_rect.center())
        assert overlay.conversation_choice() == {"mode": "continue", "index": 2}
        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=overlay._conversation_list_rect.center())
        conversation_menu = menus[-1]
        next(action for action in conversation_menu.actions() if action.data() == 2).trigger()
        assert overlay.conversation_choice() == {"mode": "continue", "index": 2}

        overlay._input_line.setText("Please answer with the selected project context.")
        overlay._fire_custom()
        assert chosen and chosen[-1][1] == "Please answer with the selected project context."
        assert cancelled == []
    finally:
        overlay.close()
        overlay.deleteLater()
        qapp.processEvents()


def test_real_intent_overlay_paste_shortcut_attaches_clipboard_file(qapp, tmp_path):
    """Ctrl+V with file MIME emits context instead of typing a filesystem path."""

    from PySide6.QtCore import QMimeData, Qt, QUrl
    from PySide6.QtTest import QTest
    from ui.intent_overlay import IntentOverlay

    path = tmp_path / "clipboard-notes.txt"
    path.write_text("clipboard file context", encoding="utf-8")
    clipboard = qapp.clipboard()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    clipboard.setMimeData(mime)
    pasted: list[list[tuple[str, str, str]]] = []
    overlay = IntentOverlay(context_items=[])
    overlay.context_items_pasted.connect(pasted.append)
    try:
        overlay.show()
        overlay.activateWindow()
        overlay.setFocus()
        qapp.processEvents()
        QTest.keyClick(overlay, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()

        assert pasted == [[("clipboard-notes.txt", "clipboard file context", "text")]]
        assert str(path) not in overlay.current_custom_text()
    finally:
        clipboard.clear()
        overlay.close()
        overlay.deleteLater()
        qapp.processEvents()


def test_chat_window_context_preview_send_and_history_workflow(qapp, isolated_app_state: IsolatedAppState):
    """Chat chips, previews, attachments, streaming, and history mutate one conversation."""
    from core.conversation_store import store as conversation_store
    from ui.chat_window import ChatWindow

    attachment = isolated_app_state.root / "attached.txt"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_text("attached file body", encoding="utf-8")
    conversations = [
        {
            "id": "conv-a",
            "project_id": "p1",
            "messages": [{"role": "user", "content": "Initial user turn"}],
            "context": "[Current Chat Context]\nLegacy context block",
            "context_policy": {},
        }
    ]
    preview_requests: list[dict] = []
    send_calls: list[dict] = []
    persisted: list[bool] = []

    def send_fn(messages, *, context_policy):
        send_calls.append({"messages": messages, "context_policy": dict(context_policy)})
        yield {
            "type": "metadata",
            "file_context": [
                {
                    "tool": "read_file",
                    "path": str(attachment),
                    "relative_path": "attached.txt",
                    "root": str(isolated_app_state.root),
                    "ok": True,
                    "message": "read attached.txt",
                }
            ],
            "tool_context": {"allowed_tools": ["read_file"], "file_access_mode": "read"},
        }
        yield "Workflow "
        yield "answer."

    window = ChatWindow(
        conversations,
        send_fn,
        projects=[{"id": "general", "name": "General"}, {"id": "p1", "name": "Project One"}],
        active_project_id="p1",
        persist_fn=lambda: persisted.append(True),
        on_context_preview=preview_requests.append,
    )
    try:
        window.show()
        qapp.processEvents()
        _pump_until(qapp, lambda: bool(preview_requests))
        preview_requests.clear()

        assert set(window._context_controls) == {
            "ambient", "browser", "selection", "clipboard", "screenshot", "github", "memory", "files"
        }
        window._set_context_policy_state("browser", "on")
        window._set_context_policy_state("selection", "on")
        window._set_context_policy_state("memory", "auto")
        window._set_context_policy_state("files", "read")
        assert preview_requests
        preview_id = preview_requests[-1]["preview_id"]
        old_selection_tokens = window._context_control_tokens["selection"]
        window.update_context_preview("stale-preview", [{"id": "selection", "tokens": "~500 tok"}])
        assert window._context_control_tokens["selection"] == old_selection_tokens
        window.update_context_preview(
            preview_id,
            [
                {"id": "selection", "tokens": "~7 tok", "warning": ""},
                {"id": "memory", "tokens": "? tok", "warning": "Memory tokens are estimated after prompt."},
            ],
        )
        assert window._context_control_tokens["selection"] == "~7 tok"
        assert "Memory tokens" in window._context_controls["memory"].toolTip()

        conversations[0]["context_policy"]["context_browser_mode"] = "auto"
        window._pending_attachment_context = "Attached context from drop zone."
        window._pending_attachment_labels = ["attached.txt"]
        window._pending_attachments = [
            conversation_store.external_file_attachment(str(attachment), kind="text", source="external_path")
        ]
        window._refresh_attachment_label()
        qapp.processEvents()
        assert window._attachment_label is not None and window._attachment_label.isVisible()

        window._send("Question with current chat context")
        _pump_until(qapp, lambda: not window._streaming and len(conversations[0]["messages"]) >= 3)

        assert send_calls
        call = send_calls[0]
        assert call["context_policy"]["context_browser_mode"] == "auto"
        assert call["context_policy"]["context_memory_mode"] == "model"
        assert call["context_policy"]["file_access"] == "read"
        assert call["messages"][0]["role"] == "system"
        assert "Legacy context block" in call["messages"][0]["content"]
        assert "[Attached context for this message]" in call["messages"][-1]["content"]

        assert conversations[0]["messages"][-2]["role"] == "user"
        assert "Attached context from drop zone" in conversations[0]["messages"][-2]["context"]
        assert conversations[0]["messages"][-1]["role"] == "assistant"
        assert conversations[0]["messages"][-1]["content"] == "Workflow answer."
        assert conversations[0]["file_context"][0]["relative_path"] == "attached.txt"
        assert conversations[0]["tool_context"]["allowed_tools"] == ["read_file"]
        assert persisted

        conversations.append(
            {
                "id": "conv-b",
                "project_id": "p1",
                "messages": [{"role": "user", "content": "Externally added"}],
            }
        )
        window.ingest_new_conversations()
        assert window._stack.count() == 2
        window._toggle_pin(0)
        assert conversations[0]["pinned"] is True
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_chat_real_new_send_newline_continue_and_history_controls(qapp):
    """Real chat controls create, switch, and continue conversations without mixing input."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from ui.chat_window import ChatWindow

    conversations = [
        {
            "id": "older",
            "project_id": "p1",
            "title": "Older project chat",
            "messages": [{"role": "user", "content": "old question"}],
            "context_policy": {},
        },
        {
            "id": "newer",
            "project_id": "general",
            "title": "Newer general chat",
            "messages": [{"role": "user", "content": "new question"}],
            "context_policy": {},
        },
    ]
    calls = []
    project_changes = []

    def send_fn(messages, *, context_policy):
        calls.append({"messages": [dict(item) for item in messages], "policy": dict(context_policy)})
        yield "answer"

    window = ChatWindow(
        conversations,
        send_fn,
        projects=[{"id": "general", "name": "General"}, {"id": "p1", "name": "Project One"}],
        active_idx=1,
        on_project_change=project_changes.append,
    )
    try:
        window.show()
        window.activateWindow()
        qapp.processEvents()
        assert window.isVisible() and window._active_idx == 1
        assert any("Project One" in label.text() for label in window.findChildren(type(window._past_notice)))

        older_button = next(button for idx, button in window._sidebar_btns if idx == 0)
        QTest.mouseClick(older_button, Qt.MouseButton.LeftButton)
        assert window._active_idx == 0

        window._project_combo.setFocus()
        QTest.keyClick(window._project_combo, Qt.Key.Key_Down)
        assert window._project_combo.currentData() == "p1"
        assert project_changes == ["p1"]
        QTest.mouseClick(window._new_chat_btn, Qt.MouseButton.LeftButton)
        assert len(conversations) == 3 and window._active_idx == 2
        assert conversations[2]["project_id"] == "p1"
        QTest.keyClick(window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert len(conversations) == 4 and window._active_idx == 3

        window._input.setFocus()
        QTest.keyClicks(window._input, "line one")
        QTest.keyClick(window._input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClicks(window._input, "line two")
        assert window._input.toPlainText() == "line one\nline two"
        assert calls == []
        QTest.keyClick(window._input, Qt.Key.Key_Return)
        _pump_until(qapp, lambda: not window._streaming and len(conversations[3]["messages"]) == 2)
        assert conversations[3]["messages"][0]["content"] == "line one\nline two"
        assert conversations[3]["messages"][1]["content"] == "answer"

        older_button = next(button for idx, button in window._sidebar_btns if idx == 0)
        QTest.mouseClick(older_button, Qt.MouseButton.LeftButton)
        window._input.setFocus()
        QTest.keyClicks(window._input, "follow up")
        QTest.keyClick(window._input, Qt.Key.Key_Return)
        _pump_until(qapp, lambda: not window._streaming and len(conversations[0]["messages"]) == 3)
        assert [item["content"] for item in calls[-1]["messages"] if item["role"] == "user"] == [
            "old question",
            "follow up",
        ]
        assert window._active_idx == 0
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_chat_real_project_and_conversation_options_workflow(qapp, monkeypatch, tmp_path):
    """Real project selector and conversation menus mutate and persist the shown history."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QInputDialog, QLabel, QMenu, QMessageBox, QPushButton

    from ui import chat_window as chat_window_mod
    from ui.chat_window import ChatWindow

    conversations = [
        {
            "id": "first",
            "project_id": "p1",
            "title": "First chat",
            "messages": [{"role": "user", "content": "first question"}],
            "context_policy": {},
        },
        {
            "id": "second",
            "project_id": "general",
            "title": "Second chat",
            "messages": [{"role": "user", "content": "second question"}],
            "context_policy": {},
        },
    ]
    projects = [{"id": "general", "name": "General"}, {"id": "p1", "name": "Project One"}]
    persisted = []
    project_changes = []
    created_names = []
    revealed = []
    menus = []
    submenus = []
    text_answers = iter([("Created Project", True), ("Renamed chat", True)])
    record = tmp_path / "chats" / "conversations.json"
    record.parent.mkdir(parents=True)
    record.write_text("[]", encoding="utf-8")

    def create_project(name):
        created_names.append(name)
        return {"id": "p2", "name": name}

    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: next(text_answers))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMenu, "popup", lambda menu, _pos: menus.append(menu))
    original_add_menu = QMenu.addMenu

    def capture_submenu(menu, *args):
        submenu = original_add_menu(menu, *args)
        submenus.append(submenu)
        return submenu

    monkeypatch.setattr(QMenu, "addMenu", capture_submenu)
    monkeypatch.setattr(chat_window_mod._conversation_store, "CONVERSATIONS_FILE", record)
    monkeypatch.setattr(chat_window_mod._file_browser, "reveal_path", revealed.append)

    window = ChatWindow(
        conversations,
        lambda _messages, **_kwargs: iter(()),
        projects=projects,
        on_new_project=create_project,
        on_project_change=project_changes.append,
        persist_fn=lambda: persisted.append(True),
    )

    def open_options(conversation_id):
        index = next(i for i, conv in enumerate(conversations) if conv["id"] == conversation_id)
        title_button = next(button for real_idx, button in window._sidebar_btns if real_idx == index)
        row = title_button.parentWidget()
        menu_button = next(button for button in row.findChildren(QPushButton) if button is not title_button)
        QTest.mouseClick(menu_button, Qt.MouseButton.LeftButton)
        assert menus and window._conversation_menu is menus[-1]
        return menus[-1]

    def action(menu, label):
        return next(item for item in menu.actions() if item.text() == label)

    try:
        window.show()
        qapp.processEvents()
        labels = {label.text().strip() for label in window.findChildren(QLabel)}
        assert "Project One" in labels
        grouped = window._grouped_sidebar_indices()
        assert {project_id: indices for project_id, _name, indices in grouped} == {
            "general": [1],
            "p1": [0],
        }

        QTest.mouseClick(window._new_chat_btn, Qt.MouseButton.LeftButton)
        assert conversations[-1]["project_id"] == "general"
        window._project_combo.setFocus()
        QTest.keyClick(window._project_combo, Qt.Key.Key_End)
        qapp.processEvents()
        assert created_names == ["Created Project"]
        assert project_changes == ["p2"]
        assert window._project_combo.currentData() == "p2"
        QTest.mouseClick(window._new_chat_btn, Qt.MouseButton.LeftButton)
        assert conversations[-1]["project_id"] == "p2"

        menu = open_options("first")
        action(menu, "Pin").trigger()
        assert conversations[0]["pinned"] is True
        menu = open_options("first")
        action(menu, "Unpin").trigger()
        assert conversations[0]["pinned"] is False

        menu = open_options("first")
        action(menu, "Rename").trigger()
        assert conversations[0]["title_override"] == "Renamed chat"
        assert any("Renamed chat" in button.toolTip() for _idx, button in window._sidebar_btns)

        menu = open_options("first")
        project_menu = next(submenu for submenu in reversed(submenus) if submenu.title() == "Add to project")
        next(item for item in project_menu.actions() if item.text() == "Created Project").trigger()
        assert conversations[0]["project_id"] == "p2"
        regrouped = {
            project_id: indices for project_id, _name, indices in window._grouped_sidebar_indices()
        }
        assert 0 in regrouped["p2"] and 0 not in regrouped.get("p1", [])

        menu = open_options("first")
        action(menu, "Browse conversation files").trigger()
        assert revealed == [record]

        menu = open_options("first")
        action(menu, "Delete").trigger()
        assert all(conv["id"] != "first" for conv in conversations)
        assert len(persisted) == 6
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("project_state", ["general", "existing", "new"])
@pytest.mark.parametrize("entry", ["button", "shortcut"])
def test_chat_project_by_new_entry_matrix(qapp, monkeypatch, project_state, entry):
    """Every project kind survives both user routes that create a new chat."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QInputDialog

    from ui.chat_window import ChatWindow

    conversations = [
        {"id": "seed", "project_id": "general", "messages": [{"role": "user", "content": "seed"}]}
    ]
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Matrix Project", True))
    window = ChatWindow(
        conversations,
        lambda _messages, **_kwargs: iter(()),
        projects=[{"id": "general", "name": "General"}, {"id": "p1", "name": "Existing"}],
        on_new_project=lambda _name: {"id": "p2", "name": "Matrix Project"},
    )
    try:
        window.show()
        window.activateWindow()
        qapp.processEvents()
        expected = "general"
        if project_state == "existing":
            window._project_combo.setFocus()
            QTest.keyClick(window._project_combo, Qt.Key.Key_Down)
            expected = "p1"
        elif project_state == "new":
            window._project_combo.setFocus()
            QTest.keyClick(window._project_combo, Qt.Key.Key_End)
            expected = "p2"
        if entry == "button":
            QTest.mouseClick(window._new_chat_btn, Qt.MouseButton.LeftButton)
        else:
            QTest.keyClick(window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert conversations[-1]["project_id"] == expected
        assert window._active_idx == len(conversations) - 1
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_chat_real_context_chip_matrix_is_per_conversation_and_reaches_send(qapp, monkeypatch):
    """Every context-chip choice travels through its real menu and active chat request."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMenu

    from ui.chat_window import ChatWindow

    conversations = [
        {"id": "one", "messages": [], "context_policy": {}},
        {"id": "two", "messages": [], "context_policy": {}},
    ]
    menus = []
    captures = []
    previews = []
    sends = []
    persisted = []
    monkeypatch.setattr(QMenu, "popup", lambda menu, _pos: menus.append(menu))

    def send_fn(messages, *, context_policy):
        sends.append({"messages": [dict(item) for item in messages], "policy": dict(context_policy)})
        yield "context matrix answer"

    window = ChatWindow(
        conversations,
        send_fn,
        active_idx=0,
        persist_fn=lambda: persisted.append(True),
        on_context_capture=captures.append,
        on_context_preview=previews.append,
    )
    try:
        window.show()
        window.activateWindow()
        qapp.processEvents()

        expected_final = {}
        for source, chip in window._context_controls.items():
            for value, _label in window._context_control_options[source]:
                preview_count = len(previews)
                QTest.mouseClick(chip, Qt.MouseButton.LeftButton)
                menu = menus[-1]
                action = next(item for item in menu.actions() if item.data() == value)
                previous = str(chip.property("context_state"))
                action.trigger()
                qapp.processEvents()
                if source in {"selection", "screenshot"} and previous == "off" and value == "on":
                    assert captures[-1]["source"] == source
                    assert str(chip.property("context_state")) == "off"
                    assert window.cancel_context_capture(source)["cancelled"] is True
                    assert str(chip.property("context_state")) == "off"
                    QTest.mouseClick(chip, Qt.MouseButton.LeftButton)
                    next(item for item in menus[-1].actions() if item.data() == "on").trigger()
                    assert captures[-1]["source"] == source
                    attached = window.attach_captured_context(
                        name=f"{source}.txt",
                        content=f"captured {source}",
                        item_type="text",
                        source=source,
                    )
                    assert attached["attached"] is True
                assert str(chip.property("context_state")) == value, source
                assert len(previews) > preview_count
                assert previews[-1]["context_policy"] == conversations[0]["context_policy"]
            expected_final[source] = str(chip.property("context_state"))

        second_button = next(button for index, button in window._sidebar_btns if index == 1)
        QTest.mouseClick(second_button, Qt.MouseButton.LeftButton)
        assert window._active_idx == 1
        second_states = {
            source: str(chip.property("context_state"))
            for source, chip in window._context_controls.items()
        }
        assert second_states != expected_final
        assert conversations[1]["context_policy"] != conversations[0]["context_policy"]

        first_button = next(button for index, button in window._sidebar_btns if index == 0)
        QTest.mouseClick(first_button, Qt.MouseButton.LeftButton)
        assert window._active_idx == 0
        assert {
            source: str(chip.property("context_state"))
            for source, chip in window._context_controls.items()
        } == expected_final

        window._input.setFocus()
        QTest.keyClicks(window._input, "use every selected context")
        QTest.keyClick(window._input, Qt.Key.Key_Return)
        _pump_until(qapp, lambda: not window._streaming and bool(sends))
        assert sends[0]["policy"] == conversations[0]["context_policy"]
        assert sends[0]["messages"][-1]["role"] == "user"
        assert "captured selection" in sends[0]["messages"][-1]["content"]
        assert "captured screenshot" in sends[0]["messages"][-1]["content"]
        assert persisted
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_chat_real_picker_and_drop_events_attach_text_and_image(
    qapp, monkeypatch, tmp_path, isolated_app_state: IsolatedAppState
):
    """Picker clicks and Qt drag/drop events feed one visible message and its thumbnail."""
    from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog, QLabel

    from core.conversation_store import store as conversation_store
    from ui.chat_window import ChatWindow

    note = tmp_path / "picked-note.txt"
    note.write_text("picked through the real file chooser", encoding="utf-8")
    picker_image = tmp_path / "picked-image.png"
    dropped_note = tmp_path / "dropped-note.txt"
    dropped_note.write_text("dropped through the real Qt event", encoding="utf-8")
    dropped_image = tmp_path / "dropped-image.png"
    file_image = QImage(3, 2, QImage.Format.Format_RGB32)
    file_image.fill(QColor("blue"))
    assert file_image.save(str(picker_image)) and file_image.save(str(dropped_image))
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        lambda *_args, **_kwargs: ([str(note), str(picker_image)], "Supported files"),
    )
    conversations = [{"id": "attachments", "messages": [], "context_policy": {}}]
    sends = []

    def send_fn(messages, *, context_policy):
        sends.append([dict(item) for item in messages])
        yield "attachment answer"

    window = ChatWindow(conversations, send_fn)
    try:
        window.show()
        window.activateWindow()
        qapp.processEvents()

        QTest.mouseClick(window._attach_btn, Qt.MouseButton.LeftButton)
        assert note.name in window._pending_attachment_labels
        assert picker_image.name in window._pending_attachment_labels
        assert window._attachment_label is not None and window._attachment_label.isVisible()

        image = QImage(3, 2, QImage.Format.Format_RGB32)
        image.fill(QColor("red"))
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(dropped_note)), QUrl.fromLocalFile(str(dropped_image))])
        mime.setImageData(image)
        drag = QDragEnterEvent(
            QPoint(12, 12),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(window, drag)
        assert drag.isAccepted()
        drop = QDropEvent(
            QPointF(12, 12),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(window, drop)
        assert drop.isAccepted()
        assert dropped_note.name in window._pending_attachment_labels
        assert dropped_image.name in window._pending_attachment_labels

        window._input.setFocus()
        QTest.keyClicks(window._input, "inspect both attachments")
        QTest.keyClick(window._input, Qt.Key.Key_Return)
        _pump_until(qapp, lambda: not window._streaming and len(conversations[0]["messages"]) == 2)

        user_message = conversations[0]["messages"][0]
        external_paths = {
            item["path"] for item in user_message["attachments"] if item["source"] == "external_path"
        }
        assert external_paths == {str(note), str(picker_image), str(dropped_note), str(dropped_image)}
        image_refs = [item for item in user_message["attachments"] if item["kind"] == "image"]
        assert image_refs and all(conversation_store.attachment_image_base64(item) for item in image_refs)
        assert note.name in user_message["context"]
        assert sends and "picked through the real file chooser" in sends[0][-1]["content"]
        assert "dropped through the real Qt event" in sends[0][-1]["content"]
        thumbnails = [label.pixmap() for label in window.findChildren(QLabel) if label.pixmap() is not None]
        assert any(not pixmap.isNull() for pixmap in thumbnails)
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_chat_real_message_actions_and_zoom_shortcuts(qapp, monkeypatch):
    """Message option buttons route copy/UI-Lab/branch/rewind and keyboard/wheel zoom."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QContextMenuEvent, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QPushButton

    import config
    from ui.chat_window import ChatWindow, _MessageTextView

    conversations = [
        {
            "id": "actions",
            "messages": [
                {"role": "user", "content": "first message"},
                {"role": "assistant", "content": "select this phrase"},
                {"role": "user", "content": "third message"},
            ],
            "context_policy": {},
        }
    ]
    menus = []
    edited = []
    deleted = []
    saved_scales = []
    persisted = []
    monkeypatch.setattr(QMenu, "popup", lambda menu, _pos: menus.append(menu))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(config, "set_chat_font_scale", saved_scales.append)
    monkeypatch.setattr(
        ChatWindow,
        "_ui_lab_context_actions",
        lambda *_args: [
            {"label": "Edit UI label", "action": "label_editor", "match": "select"},
            {"label": "Delete UI label", "action": "delete_label", "match": "select"},
        ],
    )
    monkeypatch.setattr(ChatWindow, "_edit_ui_lab_label", lambda _self, text, index: edited.append((text, index)))
    monkeypatch.setattr(ChatWindow, "_delete_ui_lab_label", lambda _self, text, index: deleted.append((text, index)))

    window = ChatWindow(
        conversations,
        lambda _messages, **_kwargs: iter(()),
        persist_fn=lambda: persisted.append(True),
    )

    def message_buttons():
        return [
            button
            for button in window._stack.currentWidget().findChildren(QPushButton)
            if button.accessibleName() == "Message options"
        ]

    def action(menu, label):
        return next(item for item in menu.actions() if item.text() == label)

    try:
        window.show()
        window.activateWindow()
        qapp.processEvents()
        views = window.findChildren(_MessageTextView)
        cursor = views[1].document().find("select")
        assert not cursor.isNull()
        views[1].setTextCursor(cursor)

        context_event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            views[1].rect().center(),
            views[1].mapToGlobal(views[1].rect().center()),
        )
        QApplication.sendEvent(views[1], context_event)
        menu = menus[-1]
        action(menu, "Copy selected text").trigger()
        action(menu, "Edit UI label").trigger()
        action(menu, "Delete UI label").trigger()
        assert QApplication.clipboard().text() == "select"
        assert edited == [("select", 0)] and deleted == [("select", 0)]

        action(menu, "Branch from here").trigger()
        qapp.processEvents()
        assert len(conversations) == 2 and window._active_idx == 1
        assert len(conversations[1]["messages"]) == 2

        QTest.mouseClick(message_buttons()[0], Qt.MouseButton.LeftButton)
        action(menus[-1], "Rewind current chat to here").trigger()
        qapp.processEvents()
        assert len(conversations[1]["messages"]) == 1
        assert persisted

        initial_scale = window._font_scale
        QTest.keyClick(window, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert window._font_scale == pytest.approx(initial_scale + 0.1)
        QTest.keyClick(window, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert window._font_scale == pytest.approx(initial_scale)

        wheel = QWheelEvent(
            QPointF(10, 10),
            QPointF(window.mapToGlobal(QPoint(10, 10))),
            QPoint(),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(window, wheel)
        assert wheel.isAccepted() and window._font_scale == pytest.approx(initial_scale + 0.1)
        QTest.keyClick(window, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert window._font_scale == pytest.approx(1.0)
        window._font_scale_save_timer.timeout.emit()
        assert saved_scales[-1] == pytest.approx(1.0)
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("message_index", [0, 1], ids=["first", "middle"])
@pytest.mark.parametrize("operation", ["copy", "branch", "rewind"])
def test_chat_message_position_action_matrix(qapp, monkeypatch, message_index, operation):
    """Copy, branch, and rewind target both the first and a middle retained message."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QPushButton

    from ui.chat_window import ChatWindow, _MessageTextView

    conversations = [{
        "id": "position-matrix",
        "messages": [
            {"role": "user", "content": "first target"},
            {"role": "assistant", "content": "middle target"},
            {"role": "user", "content": "last target"},
        ],
        "context_policy": {},
    }]
    menus = []
    monkeypatch.setattr(QMenu, "popup", lambda menu, _pos: menus.append(menu))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(ChatWindow, "_ui_lab_context_actions", lambda *_args: [])
    window = ChatWindow(conversations, lambda _messages, **_kwargs: iter(()))
    try:
        window.show()
        window.activateWindow()
        qapp.processEvents()
        active_page = window._stack.currentWidget()
        views = active_page.findChildren(_MessageTextView)
        buttons = [
            button for button in active_page.findChildren(QPushButton)
            if button.accessibleName() == "Message options"
        ]
        if operation == "copy":
            word = "first" if message_index == 0 else "middle"
            cursor = views[message_index].document().find(word)
            views[message_index].setTextCursor(cursor)
            event = QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse,
                views[message_index].rect().center(),
                views[message_index].mapToGlobal(views[message_index].rect().center()),
            )
            QApplication.sendEvent(views[message_index], event)
            next(item for item in menus[-1].actions() if item.text() == "Copy selected text").trigger()
            assert QApplication.clipboard().text() == word
        else:
            QTest.mouseClick(buttons[message_index], Qt.MouseButton.LeftButton)
            label = "Branch from here" if operation == "branch" else "Rewind current chat to here"
            next(item for item in menus[-1].actions() if item.text() == label).trigger()
            qapp.processEvents()
            target = conversations[-1] if operation == "branch" else conversations[0]
            assert len(target["messages"]) == message_index + 1
            assert len(conversations) == (2 if operation == "branch" else 1)
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_chat_tool_loop_trace_toggle_reaches_visible_history(qapp, monkeypatch):
    """The real tool-loop toggle controls trace chunks and Chat persists them as activity."""
    from types import SimpleNamespace

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    import config
    from core.llm_clients import client as llm
    from ui.chat_window import ChatWindow

    monkeypatch.delenv("CHAT_TOOL_TRACE_UI", raising=False)

    class Responses:
        def create(self, **_kwargs):
            return SimpleNamespace(id="response", output_text="trace answer", output=[])

    def raw_stream(enabled):
        monkeypatch.setattr(config, "CHAT_TOOL_TRACE_UI", enabled, raising=False)
        return list(
            llm._stream_codex(
                "inspect the workspace",
                "gpt-test",
                SimpleNamespace(responses=Responses()),
                use_tools=True,
                allowed_tools=["read_file"],
                pinned_tools=["read_file"],
            )
        )

    disabled = raw_stream(False)
    assert [getattr(chunk, "kind", "answer") for chunk in disabled] == ["answer"]
    enabled = raw_stream(True)
    assert [getattr(chunk, "kind", "answer") for chunk in enabled] == [
        "progress", "progress", "answer"
    ]
    assert "Tool loop:" in str(enabled[0]) and "Tool calls:" in str(enabled[1])

    conversations = [{"id": "trace", "messages": [], "context_policy": {}}]

    def send_fn(_messages, **_kwargs):
        for chunk in raw_stream(True):
            kind = getattr(chunk, "kind", "answer")
            if kind == "progress":
                yield {"type": "chunk", "text": str(chunk), "is_progress": True, "is_thought": True}
            else:
                yield str(chunk)

    window = ChatWindow(conversations, send_fn)
    try:
        window.show()
        window.activateWindow()
        qapp.processEvents()
        window._input.setFocus()
        QTest.keyClicks(window._input, "show trace")
        QTest.keyClick(window._input, Qt.Key.Key_Return)
        _pump_until(qapp, lambda: not window._streaming and len(conversations[0]["messages"]) == 2)

        saved = conversations[0]["messages"][1]
        assert saved["content"] == "trace answer"
        assert "Tool loop:" in saved["display_content"]
        assert "Tool calls:" in saved["display_content"]
        assert [segment["is_thought"] for segment in saved["display_segments"]] == [True, False]
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "enabled"])
@pytest.mark.parametrize("opening_state", ["short", "long", "user_last", "already_used", "force_new"])
def test_chat_auto_elaborate_opening_matrix(qapp, monkeypatch, enabled, opening_state):
    """Opening real Chat auto-elaborates only one eligible short assistant reply."""
    import hashlib

    from PySide6.QtTest import QTest

    import config
    from core.conversation_store import store as conversation_store
    from runtime.workers.ui_host import QtProtocolHost

    answer = "A short answer." if opening_state != "long" else ("L" * 501)
    role = "user" if opening_state == "user_last" else "assistant"
    latest = {"id": "latest-answer", "role": role, "content": answer, "created_at": "now"}
    conversation = {"id": "auto-elaborate", "messages": [latest], "context_policy": {}}
    if opening_state == "already_used":
        marker_source = "|".join((latest["id"], latest["created_at"], answer))
        conversation["auto_elaborated_message"] = hashlib.sha256(
            marker_source.encode("utf-8")
        ).hexdigest()
        conversation["auto_elaborate_turn_count"] = 1

    monkeypatch.setattr(config, "CHAT_AUTO_ELABORATE", enabled, raising=False)
    monkeypatch.setattr(config, "CHAT_ELABORATE_PROMPT", "Please expand this answer.", raising=False)
    monkeypatch.setattr(
        conversation_store,
        "load_projects",
        lambda: [{"id": "general", "name": "General"}],
    )
    sends = []
    persisted = []

    def send_fn(messages, **_kwargs):
        sends.append([dict(item) for item in messages])
        yield "A substantially expanded answer."

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._chat = None
    host._all_conversations = [conversation]
    host._active_conversation_idx = 0
    host._active_project_id = "general"
    host._watchdog = None
    host._make_chat_send_fn = lambda: send_fn
    host._persist_conversations = lambda: persisted.append(True)
    host._set_active_project = lambda project_id: setattr(host, "_active_project_id", project_id)
    host._create_project = lambda name: {"id": name.lower(), "name": name}
    host._set_active_conversation = lambda idx: setattr(host, "_active_conversation_idx", idx)
    host._chat_context_capture_requested = lambda _payload: None
    host.emit = lambda *_args, **_kwargs: None

    chat = None
    try:
        result = host._show_chat(force_new=opening_state == "force_new")
        chat = host._chat
        assert result == {"shown": True, "reused": False}
        QTest.qWait(250)
        qapp.processEvents()
        expected_send = enabled and opening_state == "short"
        assert bool(sends) is expected_send
        if expected_send:
            assert sends[0][-1]["content"] == "Please expand this answer."
            assert conversation["auto_elaborated_message"]
            assert persisted
            chat.hide()
            host._show_chat(force_new=False)
            QTest.qWait(150)
            qapp.processEvents()
            assert len(sends) == 1
    finally:
        if chat is not None:
            chat.close()
            chat.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize(
    "prompt",
    ["Please elaborate on that.", "Explain the tradeoffs and give one concrete example."],
    ids=["localized_default", "custom"],
)
def test_chat_auto_elaborate_prompt_reaches_real_send(qapp, monkeypatch, prompt):
    """Default and custom elaborate prompts reach the same real Chat send path exactly."""
    from PySide6.QtTest import QTest

    import config
    from core.conversation_store import store as conversation_store
    from runtime.workers.ui_host import QtProtocolHost

    conversation = {
        "id": "prompt-choice",
        "messages": [{"id": "answer", "role": "assistant", "content": "Short."}],
        "context_policy": {},
    }
    monkeypatch.setattr(config, "CHAT_AUTO_ELABORATE", True, raising=False)
    monkeypatch.setattr(config, "CHAT_ELABORATE_PROMPT", prompt, raising=False)
    monkeypatch.setattr(
        conversation_store,
        "load_projects",
        lambda: [{"id": "general", "name": "General"}],
    )
    sends = []

    def send_fn(messages, **_kwargs):
        sends.append([dict(item) for item in messages])
        yield "Expanded."

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._chat = None
    host._all_conversations = [conversation]
    host._active_conversation_idx = 0
    host._active_project_id = "general"
    host._watchdog = None
    host._make_chat_send_fn = lambda: send_fn
    host._persist_conversations = lambda: None
    host._set_active_project = lambda value: setattr(host, "_active_project_id", value)
    host._create_project = lambda name: {"id": name.lower(), "name": name}
    host._set_active_conversation = lambda idx: setattr(host, "_active_conversation_idx", idx)
    host._chat_context_capture_requested = lambda _payload: None
    host.emit = lambda *_args, **_kwargs: None
    chat = None
    try:
        host._show_chat(force_new=False)
        chat = host._chat
        QTest.qWait(250)
        qapp.processEvents()
        assert sends and sends[0][-1]["content"] == prompt
    finally:
        if chat is not None:
            chat.close()
            chat.deleteLater()
        qapp.processEvents()


def test_chat_external_transcript_pull_button_and_count_matrix(qapp, monkeypatch, tmp_path):
    """The real Pull button imports/updates both namespaces and reports every count state."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox

    from core.conversation_store import external_sync
    from ui import chat_window as chat_window_mod
    from ui.chat_window import ChatWindow

    codex_home = tmp_path / ".codex"
    claude_home = tmp_path / ".claude"
    codex_path = codex_home / "sessions" / "shared.jsonl"
    claude_path = claude_home / "projects" / "repo" / "shared.jsonl"

    def write_jsonl(path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")

    codex_records = [
        {"type": "session_meta", "payload": {"id": "shared", "cwd": str(tmp_path)}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "Codex question"}},
    ]
    claude_records = [
        {
            "type": "user",
            "uuid": "u1",
            "parentUuid": None,
            "sessionId": "shared",
            "cwd": str(tmp_path),
            "message": {"role": "user", "content": "Claude question"},
        }
    ]
    write_jsonl(codex_path, codex_records)
    write_jsonl(claude_path, claude_records)
    monkeypatch.setattr(
        chat_window_mod,
        "discover_external_conversations",
        lambda: external_sync.discover_external_conversations(
            codex_home=codex_home,
            claude_home=claude_home,
        ),
    )
    information = []
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, text: information.append((title, text)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, text: warnings.append((title, text)),
    )
    conversations = []
    persisted = []
    window = ChatWindow(
        conversations,
        lambda _messages, **_kwargs: iter(()),
        persist_fn=lambda: persisted.append(True),
    )

    def pull_and_wait():
        previous = len(information)
        QTest.mouseClick(window._external_sync_btn, Qt.MouseButton.LeftButton)
        _pump_until(
            qapp,
            lambda: window._external_sync_btn.isEnabled() and len(information) == previous + 1,
        )
        return information[-1][1]

    try:
        window.show()
        qapp.processEvents()
        first = pull_and_wait()
        assert "Imported 2, updated 0, unchanged 0." in first
        assert {
            (conv["external_source"]["provider"], conv["external_source"]["session_id"])
            for conv in conversations
        } == {("codex", "shared"), ("claude", "shared")}
        assert len({conv["id"] for conv in conversations}) == 2
        assert any("ChatGPT" in button.toolTip() for _idx, button in window._sidebar_btns)
        assert any("Claude" in button.toolTip() for _idx, button in window._sidebar_btns)

        codex_records.append(
            {
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "phase": "final_answer",
                    "message": "Codex answer",
                },
            }
        )
        write_jsonl(codex_path, codex_records)
        second = pull_and_wait()
        assert "Imported 0, updated 1, unchanged 1." in second
        codex_conv = next(
            conv for conv in conversations if conv["external_source"]["provider"] == "codex"
        )
        assert [message["content"] for message in codex_conv["messages"]] == [
            "Codex question", "Codex answer"
        ]

        claude_records.append(
            {
                "type": "assistant",
                "uuid": "a1",
                "parentUuid": "u1",
                "sessionId": "shared",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Claude answer"}]},
            }
        )
        write_jsonl(claude_path, claude_records)
        third = pull_and_wait()
        assert "Imported 0, updated 1, unchanged 1." in third
        claude_conv = next(
            conv for conv in conversations if conv["external_source"]["provider"] == "claude"
        )
        assert [message["content"] for message in claude_conv["messages"]] == [
            "Claude question", "Claude answer"
        ]

        fourth = pull_and_wait()
        assert "Imported 0, updated 0, unchanged 2." in fourth
        assert len(persisted) == 3
        assert warnings == []
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("confirmed", [False, True], ids=["declined", "confirmed"])
def test_chat_real_external_push_provider_by_confirmation_matrix(
    qapp, monkeypatch, tmp_path, provider, confirmed
):
    """Both provider transcript formats are changed only after the real confirmation action."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMenu, QMessageBox, QPushButton

    from core.conversation_store import external_sync
    from ui import chat_window as chat_window_mod
    from ui.chat_window import ChatWindow

    root = tmp_path / f".{provider}"
    if provider == "codex":
        path = root / "sessions" / "push.jsonl"
        records = [
            {"type": "session_meta", "payload": {"id": "push", "cwd": str(tmp_path)}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Original"}},
        ]
        parser = external_sync.parse_codex_session
    else:
        path = root / "projects" / "repo" / "push.jsonl"
        records = [{
            "type": "user",
            "uuid": "u1",
            "parentUuid": None,
            "sessionId": "push",
            "cwd": str(tmp_path),
            "message": {"role": "user", "content": "Original"},
        }]
        parser = external_sync.parse_claude_session
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")
    conversation = parser(path)
    assert conversation is not None
    conversation["messages"].append({"role": "assistant", "content": "Wisp follow-up"})
    original = path.read_bytes()
    backups = tmp_path / "backups"
    menus = []
    information = []
    monkeypatch.setattr(QMenu, "popup", lambda menu, _pos: menus.append(menu))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: (
            QMessageBox.StandardButton.Yes if confirmed else QMessageBox.StandardButton.No
        ),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, text: information.append((title, text)),
    )
    monkeypatch.setattr(
        chat_window_mod,
        "push_conversation_to_source",
        lambda conv: external_sync.push_conversation_to_source(
            conv,
            backup_dir=backups,
            source_root=root,
        ),
    )
    persisted = []
    window = ChatWindow(
        [conversation],
        lambda _messages, **_kwargs: iter(()),
        persist_fn=lambda: persisted.append(True),
    )
    try:
        window.show()
        qapp.processEvents()
        title_button = window._sidebar_btns[0][1]
        row = title_button.parentWidget()
        menu_button = next(
            button for button in row.findChildren(QPushButton) if button is not title_button
        )
        QTest.mouseClick(menu_button, Qt.MouseButton.LeftButton)
        provider_label = "ChatGPT" if provider == "codex" else "Claude"
        push_action = next(
            action for action in menus[-1].actions()
            if action.text() == f"Push Wisp turns to {provider_label}"
        )
        assert push_action.isEnabled()
        push_action.trigger()
        qapp.processEvents()

        if confirmed:
            reparsed = parser(path)
            assert reparsed is not None
            assert [message["content"] for message in reparsed["messages"]] == [
                "Original", "Wisp follow-up"
            ]
            assert list(backups.glob("*"))
            assert persisted == [True]
            assert information
        else:
            assert path.read_bytes() == original
            assert not backups.exists()
            assert persisted == []
            assert information == []
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("confirmed", [False, True], ids=["declined", "confirmed"])
def test_chat_real_external_export_provider_by_confirmation_matrix(
    qapp, monkeypatch, tmp_path, provider, confirmed
):
    """Both export choices create a new transcript only after their real confirmation action."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMenu, QMessageBox, QPushButton

    from core.conversation_store import external_sync
    from ui import chat_window as chat_window_mod
    from ui.chat_window import ChatWindow

    codex_home = tmp_path / ".codex"
    claude_home = tmp_path / ".claude"
    conversation = {
        "id": "wisp-native",
        "messages": [
            {"role": "user", "content": "Original question"},
            {"role": "assistant", "content": "Wisp answer"},
        ],
        "context_policy": {},
    }
    menus = []
    submenus = []
    information = []
    monkeypatch.setattr(QMenu, "popup", lambda menu, _pos: menus.append(menu))
    original_add_menu = QMenu.addMenu

    def capture_submenu(menu, *args):
        submenu = original_add_menu(menu, *args)
        submenus.append(submenu)
        return submenu

    monkeypatch.setattr(QMenu, "addMenu", capture_submenu)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: (
            QMessageBox.StandardButton.Yes if confirmed else QMessageBox.StandardButton.No
        ),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, text: information.append((title, text)),
    )
    monkeypatch.setattr(
        chat_window_mod,
        "export_conversation_as_new_session",
        lambda conv, provider_key, cwd: external_sync.export_conversation_as_new_session(
            conv,
            provider_key,
            cwd=cwd,
            codex_home=codex_home,
            claude_home=claude_home,
        ),
    )
    persisted = []
    window = ChatWindow(
        [conversation],
        lambda _messages, **_kwargs: iter(()),
        persist_fn=lambda: persisted.append(True),
    )
    try:
        window.show()
        qapp.processEvents()
        title_button = window._sidebar_btns[0][1]
        row = title_button.parentWidget()
        menu_button = next(
            button for button in row.findChildren(QPushButton) if button is not title_button
        )
        QTest.mouseClick(menu_button, Qt.MouseButton.LeftButton)
        export_menu = next(menu for menu in submenus if menu.title() == "Export as new conversation")
        label = "ChatGPT" if provider == "codex" else "Claude"
        next(action for action in export_menu.actions() if action.text() == label).trigger()
        qapp.processEvents()

        root = codex_home / "sessions" if provider == "codex" else claude_home / "projects"
        exported = list(root.rglob("*.jsonl")) if root.exists() else []
        if confirmed:
            assert len(exported) == 1
            assert conversation["external_source"]["provider"] == provider
            assert persisted == [True]
            assert information
        else:
            assert exported == []
            assert "external_source" not in conversation
            assert persisted == []
            assert information == []
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_chat_attachment_extracts_persists_and_reopens(qapp, isolated_app_state: IsolatedAppState):
    """An actual attached file reaches the model and survives a fresh UI instance."""
    from core.conversation_store import store as conversation_store
    from ui.chat_window import ChatWindow

    attachment = isolated_app_state.root / "drop-contract.txt"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_text("real attachment contract body", encoding="utf-8")
    conversations = [
        {
            "id": "attachment-contract",
            "project_id": conversation_store.GENERAL_PROJECT_ID,
            "messages": [],
            "context_policy": {},
        }
    ]
    model_calls = []

    def send_fn(messages, *, context_policy):
        model_calls.append({"messages": messages, "context_policy": context_policy})
        yield "Attachment persisted."

    def persist():
        conversation_store.save_conversations(conversations)

    window = ChatWindow(conversations, send_fn, persist_fn=persist)
    reopened = None
    try:
        window.show()
        qapp.processEvents()
        assert window._add_attachment_paths([str(attachment)]) is True
        assert window._pending_attachment_labels == [attachment.name]
        assert window._pending_attachments[0]["path"] == str(attachment)

        window._send("Summarize the real attachment")
        _pump_until(qapp, lambda: not window._streaming and len(conversations[0]["messages"]) == 2)

        assert model_calls
        user_payload = model_calls[0]["messages"][-1]
        assert "real attachment contract body" in user_payload["content"]
        assert "[Attached context for this message]" in user_payload["content"]

        loaded = conversation_store.load_conversations()
        assert len(loaded) == 1
        saved_user = loaded[0]["messages"][0]
        assert saved_user["attachments"][0]["path"] == str(attachment)
        assert saved_user["attachments"][0]["source"] == "external_path"
        assert loaded[0]["messages"][1]["content"] == "Attachment persisted."

        window.close()
        qapp.processEvents()

        reopened = ChatWindow(
            conversation_store.load_conversations(),
            lambda _messages, **_kwargs: iter(()),
            persist_fn=lambda: None,
        )
        reopened.show()
        qapp.processEvents()
        assert reopened._stack.count() == 1
        assert reopened._conversations[0]["messages"][0]["attachments"][0]["name"] == attachment.name
        reopened._switch(0)
        assert reopened._active_idx == 0
    finally:
        try:
            window.close()
            window.deleteLater()
        except RuntimeError:
            pass
        if reopened is not None:
            reopened.close()
            reopened.deleteLater()
        qapp.processEvents()


def test_chat_file_approval_panel_resolves_from_real_button_click(qapp):
    """A visible file-write approval is resolved by the actual chat control."""
    from PySide6.QtWidgets import QFrame, QLabel, QPushButton

    from ui.chat_window import ChatWindow

    decisions = []
    window = ChatWindow(
        [{"id": "approval-chat", "messages": [], "context_policy": {}}],
        lambda _messages, **_kwargs: iter(()),
    )
    try:
        window.show()
        qapp.processEvents()
        result = window.request_live_file_approval(
            {
                "approval_id": "approval-1",
                "action": "edit_file",
                "path": "notes/important.txt",
                "diff": "--- a/notes/important.txt\n+++ b/notes/important.txt\n-old\n+new\n",
                "_on_decision": decisions.append,
            }
        )

        assert result == {"approved": False, "feedback": "", "shown": True}
        panel = window.findChild(QFrame, "liveFileApprovalPanel")
        assert panel is not None
        visible_text = "\n".join(label.text() for label in panel.findChildren(QLabel))
        assert "Approve this file change?" in visible_text
        assert "notes/important.txt" in visible_text
        assert "Diff: +1 -1 lines" in visible_text

        approve = next(button for button in panel.findChildren(QPushButton) if button.text() == "Approve")
        approve.click()
        qapp.processEvents()

        assert decisions == [{"approved": True, "feedback": "", "shown": True}]
        assert not panel.isVisible()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_settings_real_apply_click_persists_and_reopens(qapp, tmp_path: Path, monkeypatch):
    """Editing and clicking Apply writes a real env file that a fresh dialog reloads."""
    from PySide6.QtWidgets import QPushButton

    import config
    from core import tts
    from core.llm_clients import client as llm_client
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel import env as settings_env
    from ui.settings_panel.dialog import SettingsDialog, _get, _set
    from ui.shared import theme

    env_path = tmp_path / "settings-contract.env"
    env_path.write_text(
        "TTS_PROVIDER=none\nBUBBLE_WIDTH=340\nCHAT_ELABORATE_PROMPT=Before apply\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    # Saving is real; these are unrelated live side effects that should not
    # touch credentials, model connections, audio devices, or the global theme.
    monkeypatch.setattr(SettingsDialog, "_save_api_keys_to_keychain", lambda _self: True)
    monkeypatch.setattr(config, "reload", lambda: None)
    monkeypatch.setattr(llm_client, "reset_clients", lambda: None)
    monkeypatch.setattr(tts, "reset_connections", lambda: None)
    monkeypatch.setattr(theme, "apply_app_theme", lambda: None)

    applied = []
    dialog = SettingsDialog(on_apply=applied.append)
    reopened = None
    try:
        # Keep validation independent from any hotkeys configured on the
        # developer's machine while exercising the real settings serializer.
        for index, block in enumerate(dialog._caller_blocks, 1):
            block["hotkey"].setText(f"ctrl+alt+{index}")
        for index, key in enumerate(
            ("HOTKEY_ADD_CONTEXT", "HOTKEY_CLEAR_CONTEXT", "HOTKEY_SNIP", "HOTKEY_VOICE", "HOTKEY_DICTATE"),
            1,
        ):
            dialog._fields[key].setText(f"ctrl+shift+alt+{index}")

        dialog._fields["BUBBLE_WIDTH"].setText("612")
        dialog._fields["WISP_PLANNED_CHUNKING"].setChecked(True)
        dialog._fields["WISP_PLANNED_CHUNKING_CHUNKS"].setText("4")
        dialog._fields["WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS"].setText("120")
        _set(dialog._fields["CHAT_REASONING_EFFORT"], "medium")
        dialog._fields["CHAT_AUTO_ELABORATE"].setChecked(True)
        dialog._fields["CHAT_ELABORATE_PROMPT"].setText("Persisted through the real Apply button")
        qapp.processEvents()
        apply_button = dialog.findChild(QPushButton, "settingsApplyButton")
        assert apply_button is not None and apply_button.isEnabled()

        apply_button.click()
        qapp.processEvents()

        saved = settings_env.read_settings_env()
        assert saved["BUBBLE_WIDTH"] == "612"
        assert saved["WISP_PLANNED_CHUNKING"] == "True"
        assert saved["WISP_PLANNED_CHUNKING_CHUNKS"] == "4"
        assert saved["WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS"] == "120"
        assert saved["CHAT_REASONING_EFFORT"] == "medium"
        assert saved["CHAT_AUTO_ELABORATE"] == "True"
        assert saved["CHAT_ELABORATE_PROMPT"] == "Persisted through the real Apply button"
        assert applied and {
            "BUBBLE_WIDTH",
            "WISP_PLANNED_CHUNKING",
            "WISP_PLANNED_CHUNKING_CHUNKS",
            "WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS",
            "CHAT_REASONING_EFFORT",
            "CHAT_AUTO_ELABORATE",
            "CHAT_ELABORATE_PROMPT",
        } <= set(applied[-1]["changed_keys"])
        assert dialog.isVisible() is False or dialog.result() == 0
        assert not apply_button.isEnabled()

        reopened = SettingsDialog()
        assert _get(reopened._fields["BUBBLE_WIDTH"]) == "612"
        assert reopened._fields["WISP_PLANNED_CHUNKING"].isChecked()
        assert _get(reopened._fields["WISP_PLANNED_CHUNKING_CHUNKS"]) == "4"
        assert _get(reopened._fields["WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS"]) == "120"
        assert _get(reopened._fields["CHAT_REASONING_EFFORT"]) == "medium"
        assert reopened._fields["CHAT_AUTO_ELABORATE"].isChecked()
        assert _get(reopened._fields["CHAT_ELABORATE_PROMPT"]) == "Persisted through the real Apply button"
    finally:
        dialog.close()
        dialog.deleteLater()
        if reopened is not None:
            reopened.close()
            reopened.deleteLater()
        qapp.processEvents()


def test_context_buffer_drop_priority_and_privacy_workflow():
    """Context order, dropped documents, image promotion, and privacy are visible."""
    from core.query_pipeline import ContextInputs, GenerationCounter, build_context

    counter = GenerationCounter()
    first = counter.next()
    second = counter.next()
    assert not counter.is_current(first)
    assert counter.is_current(second)

    out = build_context(
        ContextInputs(
            intent_prompt="Summarize the current task.",
            selected="Selected user text",
            screenshot_b64=None,
            ambient_text="[Browser/Web]\nBrowser page",
            buffered_items=["Buffered Alt+Q context"],
            drop_items=[
                ("screen.png", "BASE64PNG", "image"),
                ("notes.md", "/tmp/notes.md", "document_path"),
                ("raw.txt", "raw dropped text", "text"),
            ],
            clipboard_text="Bearer abcdefghijklmnopqrstuvwxyz1234567890",
            active_document_text="active doc password=supersecret",
            priority_context="Browser/Web",
        ),
        read_document_file=lambda path: f"document body from {path}",
    )

    assert out.screenshot_b64 == "BASE64PNG"
    assert out.user_message == "Summarize the current task."
    assert "[Context priority]\nPrioritize Browser/Web" in out.ambient_ctx
    assert out.ambient_ctx.index("Buffered Alt+Q context") < out.ambient_ctx.index("--- BEGIN DOCUMENT: notes.md ---")
    assert out.ambient_ctx.index("--- BEGIN DOCUMENT: notes.md ---") < out.ambient_ctx.index("raw dropped text")
    assert out.ambient_ctx.index("raw dropped text") < out.ambient_ctx.index("Selected user text")
    assert "[BEARER_TOKEN]" in out.ambient_ctx
    assert "[REDACTED_CREDENTIAL]" in out.ambient_ctx
    assert "supersecret" not in out.ambient_ctx


def test_chat_history_projects_and_corruption_recovery_workflow(
    isolated_app_state: IsolatedAppState,
):
    """Chat/project persistence handles user actions and bad disk state."""
    from core.conversation_store import store as conversations

    conversations.PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    conversations.PROJECTS_FILE.write_text("{not valid json", encoding="utf-8")
    conversations.CONVERSATIONS_FILE.write_text("{not valid json", encoding="utf-8")

    projects_after_corruption = conversations.load_projects()
    assert projects_after_corruption[0]["id"] == conversations.GENERAL_PROJECT_ID
    assert conversations.load_conversations() == []

    project = conversations.add_project("Workflow History")
    assert conversations.add_project("workflow history")["id"] == project["id"]
    conversations.save_conversations(
        [
            {
                "id": "empty-placeholder",
                "project_id": project["id"],
                "messages": [{"role": "user", "content": "   "}],
            },
            {
                "id": "real-chat",
                "project_id": project["id"],
                "title": "",
                "pinned": True,
                "messages": [
                    {
                        "role": "user",
                        "content": "Summarize this attachment",
                        "image_base64": "base64-should-not-persist",
                        "attachments": [{"type": "image", "path": "shot.png", "mime": "image/png"}],
                    },
                    {"role": "assistant", "content": "Done"},
                ],
                "context_policy": {"memory": "on", "screenshot": "model"},
                "file_context": [{"path": "notes.txt", "text": "hello"}],
                "tool_context": {"read_file": "on"},
            },
        ]
    )

    loaded = conversations.load_conversations()
    assert [conv["id"] for conv in loaded] == ["real-chat"]
    saved_message = loaded[0]["messages"][0]
    assert "image_base64" not in saved_message
    assert saved_message["attachments"][0]["kind"] == "file"
    assert saved_message["attachments"][0]["mime"] == "image/png"
    assert loaded[0]["pinned"] is True
    assert loaded[0]["context_policy"] == {"memory": "on", "screenshot": "model"}

    assert conversations.delete_project(project["id"]) is True
    reassigned = conversations.load_conversations()[0]
    assert reassigned["project_id"] == conversations.GENERAL_PROJECT_ID
    assert conversations.delete_project(conversations.GENERAL_PROJECT_ID) is False
    assert _is_relative_to(conversations.CONVERSATIONS_FILE, isolated_app_state.root)


def test_bubble_settings_and_platform_popup_styles_workflow(qapp, monkeypatch: pytest.MonkeyPatch):
    """Bubble width and font size change independently; popup styles stay opaque."""
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QToolTip

    import config
    from ui.bubble import SpeechBubble
    from ui.intent_overlay import IntentOverlay
    from ui.shared.theme import apply_app_theme, theme_colors

    monkeypatch.setattr(config, "BUBBLE_WIDTH", 320, raising=False)
    monkeypatch.setattr(config, "BUBBLE_LINES", 2, raising=False)
    monkeypatch.setattr(config, "BUBBLE_FONT_SIZE", 10, raising=False)
    monkeypatch.setattr(config, "ICON_SIZE", 80, raising=False)
    monkeypatch.setattr(config, "BUBBLE_COLOR", "#1c1c24dc", raising=False)
    monkeypatch.setattr(config, "BUBBLE_TEXT_COLOR", "#eeeeee", raising=False)
    monkeypatch.setattr(config, "BUBBLE_READ_WORD_COLOR", "#4da3ff", raising=False)
    monkeypatch.setattr(config, "THEME_MODE", "dark", raising=False)

    apply_app_theme(qapp)
    bubble = SpeechBubble()
    try:
        assert bubble._bubble_w == 320
        assert bubble._font.pointSize() == 10
        initial_height = bubble.height()

        monkeypatch.setattr(config, "BUBBLE_WIDTH", 520, raising=False)
        bubble.apply_config()
        assert bubble._bubble_w == 520
        assert bubble._font.pointSize() == 10

        monkeypatch.setattr(config, "BUBBLE_FONT_SIZE", 16, raising=False)
        bubble.apply_config()
        assert bubble._bubble_w == 520
        assert bubble._font.pointSize() == 16
        assert bubble.height() > initial_height

        app_style = qapp.styleSheet()
        assert "QComboBox QAbstractItemView" in app_style
        assert "background-color:" in app_style
        overlay = IntentOverlay()
        try:
            assert "background: transparent" not in overlay._menu_style()
        finally:
            overlay.close()
            overlay.deleteLater()

        monkeypatch.setattr(config, "THEME_MODE", "light", raising=False)
        apply_app_theme(qapp)
        colors = theme_colors(False)
        tooltip_palette = QToolTip.palette()
        assert tooltip_palette.color(QPalette.ColorRole.ToolTipBase).name() == QColor(colors["tooltip_bg"]).name()
        assert tooltip_palette.color(QPalette.ColorRole.ToolTipText).name() == QColor(colors["text"]).name()
    finally:
        bubble.close()
        bubble.deleteLater()
        qapp.processEvents()


def test_tool_file_permission_and_approval_workflow(tmp_path: Path):
    """Agent file tools enforce roots, blocked globs, approvals, and diffs."""
    from core.agent.runtime import AgentPermissions, PermissionDenied, ScopeViolation
    from core.agent.toolbox import AgentToolbox
    from core.agent.workspace import ScopedWorkspace

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "public.txt").write_text("old text", encoding="utf-8")
    (root / "private").mkdir()
    (root / "private" / "secret.txt").write_text("secret", encoding="utf-8")

    ws = ScopedWorkspace(root, blocked_globs=["private/*"])
    assert ws.list_files() == ["public.txt"]
    with pytest.raises(ScopeViolation):
        ws.read_text("../outside.txt")
    with pytest.raises(ScopeViolation):
        ws.read_text("private/secret.txt")

    approval_requests: list[dict] = []

    def deny_once(request: dict) -> bool:
        approval_requests.append(request)
        return False

    tools = AgentToolbox(
        ws,
        AgentPermissions(allow_file_edit=True, allow_file_create=True),
        approval_callback=deny_once,
        require_approval=True,
        permission_modes={"file_edit": "ask permission"},
    )
    with pytest.raises(PermissionDenied, match="declined"):
        tools.write_file("public.txt", "new text")
    assert approval_requests[0]["action"] == "write_file"
    assert "-old text" in approval_requests[0]["diff"]
    assert "+new text" in approval_requests[0]["diff"]
    assert (root / "public.txt").read_text(encoding="utf-8") == "old text"

    tools = AgentToolbox(
        ws,
        AgentPermissions(allow_file_edit=True, allow_file_create=True),
        approval_callback=lambda _request: {"approved": False, "feedback": "Keep the old phrasing."},
        require_approval=True,
        permission_modes={"file_edit": "ask permission"},
    )
    with pytest.raises(PermissionDenied, match="Keep the old phrasing"):
        tools.write_file("public.txt", "new text")

    approvals: list[dict] = []
    tools = AgentToolbox(
        ws,
        AgentPermissions(allow_file_edit=True, allow_file_create=True, allow_shell=False),
        approval_callback=lambda request: approvals.append(request) or True,
        require_approval=True,
        permission_modes={"file_edit": "ask permission"},
    )
    result = tools.patch_file("public.txt", "old", "approved")
    assert result.ok is True
    assert (root / "public.txt").read_text(encoding="utf-8") == "approved text"
    assert approvals and approvals[0]["action"] == "patch_file"

    with pytest.raises(PermissionDenied):
        tools.run_command(["python", "-m", "py_compile", "public.txt"])


def test_all_live_local_file_tools_complete_real_operations(tmp_path: Path):
    """Every granted local-file model tool produces its intended disk result."""

    import config
    from core.llm_clients import client as llm_client
    from core.tools.local_files import LOCAL_FILE_TOOLS

    root = tmp_path / "live-file-tools"
    root.mkdir()
    seed = root / "seed.txt"
    seed.write_text("alpha beta", encoding="utf-8")
    old_roots = list(getattr(config, "TOOL_FILE_ROOTS", []))
    old_blocked = list(getattr(config, "TOOL_FILE_BLOCKED_GLOBS", []))
    old_mode = getattr(config, "TOOL_FILE_MODE", "never")
    allowed = sorted(LOCAL_FILE_TOOLS)
    try:
        config.TOOL_FILE_ROOTS = [str(root)]
        config.TOOL_FILE_BLOCKED_GLOBS = []
        config.TOOL_FILE_MODE = "auto"
        llm_client.set_live_file_access_mode("auto")

        listed = llm_client._execute_model_tool(
            "list_files", {"folder": str(root)}, allowed_tools=allowed
        )
        assert "seed.txt" in listed
        assert "alpha beta" in llm_client._execute_model_tool(
            "read_file", {"path": str(seed)}, allowed_tools=allowed
        )

        created = root / "created.txt"
        created_result = llm_client._execute_model_tool(
            "create_file",
            {"path": str(created), "content": "created once"},
            allowed_tools=allowed,
        )
        assert "created.txt" in created_result
        assert created.read_text(encoding="utf-8") == "created once"

        edited_result = llm_client._execute_model_tool(
            "edit_file",
            {"path": str(seed), "old": "beta", "new": "gamma"},
            allowed_tools=allowed,
        )
        assert "Edited" in edited_result
        assert seed.read_text(encoding="utf-8") == "alpha gamma"

        written_result = llm_client._execute_model_tool(
            "write_file",
            {"path": str(created), "content": "overwritten"},
            allowed_tools=allowed,
        )
        assert written_result
        assert created.read_text(encoding="utf-8") == "overwritten"
    finally:
        llm_client.set_live_file_access_mode(None)
        config.TOOL_FILE_ROOTS = old_roots
        config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
        config.TOOL_FILE_MODE = old_mode


@pytest.mark.parametrize(
    ("access_mode", "tool_name"),
    [
        (mode, tool)
        for mode in ("off", "read", "ask", "auto")
        for tool in ("list_files", "read_file", "create_file", "edit_file", "write_file")
    ],
)
def test_every_file_access_mode_controls_every_live_file_tool(
    tmp_path: Path,
    access_mode: str,
    tool_name: str,
):
    """Execute the complete four-mode by five-tool behavior matrix."""

    import config
    from core.llm_clients import client as llm_client
    from core.tools.local_files import file_tools_for_access

    root = tmp_path / "file-mode-matrix"
    root.mkdir()
    seed = root / "seed.txt"
    seed.write_text("alpha beta", encoding="utf-8")
    created = root / "created.txt"
    inputs = {
        "list_files": {"folder": str(root)},
        "read_file": {"path": str(seed)},
        "create_file": {"path": str(created), "content": "created"},
        "edit_file": {"path": str(seed), "old": "beta", "new": "gamma"},
        "write_file": {"path": str(seed), "content": "overwritten"},
    }
    allowed = file_tools_for_access(access_mode)
    approvals: list[dict[str, Any]] = []
    old_roots = list(getattr(config, "TOOL_FILE_ROOTS", []))
    old_blocked = list(getattr(config, "TOOL_FILE_BLOCKED_GLOBS", []))
    old_mode = getattr(config, "TOOL_FILE_MODE", "never")
    try:
        config.TOOL_FILE_ROOTS = [str(root)]
        config.TOOL_FILE_BLOCKED_GLOBS = []
        config.TOOL_FILE_MODE = access_mode
        llm_client.set_live_file_access_mode(access_mode)
        llm_client.set_file_edit_approval_callback(
            lambda request: approvals.append(request) or True
        )

        result = llm_client._execute_model_tool(
            tool_name,
            inputs[tool_name],
            allowed_tools=allowed,
        )

        granted = tool_name in allowed
        if not granted:
            assert "disabled" in result.lower()
            assert seed.read_text(encoding="utf-8") == "alpha beta"
            assert not created.exists()
            assert approvals == []
        elif tool_name == "list_files":
            assert "seed.txt" in result
        elif tool_name == "read_file":
            assert "alpha beta" in result
        elif tool_name == "create_file":
            assert created.read_text(encoding="utf-8") == "created"
        elif tool_name == "edit_file":
            assert seed.read_text(encoding="utf-8") == "alpha gamma"
        else:
            assert seed.read_text(encoding="utf-8") == "overwritten"

        if granted and tool_name in {"create_file", "edit_file", "write_file"}:
            if access_mode == "ask":
                assert approvals and approvals[0]["action"] == tool_name
            else:
                assert access_mode == "auto"
                assert approvals == []
    finally:
        llm_client.set_live_file_access_mode(None)
        llm_client.set_file_edit_approval_callback(None)
        config.TOOL_FILE_ROOTS = old_roots
        config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
        config.TOOL_FILE_MODE = old_mode


def test_provider_fallback_cooldown_capability_and_auth_redaction_workflow(
    monkeypatch: pytest.MonkeyPatch,
):
    """Route fallback, cooldown, capability warnings, and auth status are user-safe."""
    from core.auth import chatgpt as chatgpt_auth
    from core.auth import copilot_auth
    from core.auth import github as github_auth
    from core.llm_clients import client as llm_client

    _ensure_brain_path()
    from wisp_brain import handlers

    events: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        llm_client,
        "log_event",
        lambda name, message, **data: events.append((name, message, data)),
    )
    llm_client._route_cooldowns.clear()

    calls: list[tuple[str, str]] = []

    def fallback_factory(provider: str, model: str):
        calls.append((provider, model))
        if model == "primary":
            raise RuntimeError("429 rate limit from provider")
        yield "fallback answer"

    chunks = list(
        llm_client._stream_with_fallbacks(
            "query",
            [("openai", "primary"), ("openai", "fallback")],
            fallback_factory,
        )
    )
    assert chunks == ["fallback answer"]
    assert calls == [("openai", "primary"), ("openai", "fallback")]
    assert llm_client._is_route_cooling("openai", "primary") is True
    assert any(name == "llm.route_transient_fallback" for name, _message, _data in events)

    calls.clear()
    chunks = list(
        llm_client._stream_with_fallbacks(
            "query",
            [("openai", "primary"), ("openai", "fallback")],
            fallback_factory,
        )
    )
    assert chunks == ["fallback answer"]
    assert calls == [("openai", "fallback")]

    warnings = llm_client.screenshot_capability_warnings(
        ["model", "auto"],
        llm_provider="chatgpt",
        llm_model="gpt-5.5",
        vision_provider="copilot",
        vision_model="copilot-chat",
    )
    assert any("Let model decide" in warning for warning in warnings)
    assert any("Copilot cannot read screenshots" in warning for warning in warnings)
    assert llm_client.tool_capability_warnings(True, llm_provider="chatgpt")
    assert llm_client.subscription_auth_warnings(llm_provider="chatgpt", vision_provider="copilot")

    monkeypatch.setattr(
        chatgpt_auth,
        "get_tokens",
        lambda: {"access_token": "secret-chatgpt-token", "account_id": "acct-visible"},
    )
    monkeypatch.setattr(
        github_auth,
        "get_tokens",
        lambda: {"access_token": "secret-github-token", "user": {"login": "octo-user"}},
    )
    monkeypatch.setattr(
        copilot_auth,
        "token_status",
        lambda: (True, "Stored in OS keychain. Token format OK."),
    )
    status = handlers.brain_auth_status()
    rendered = repr(status)
    assert "acct-visible" in rendered
    assert "octo-user" in rendered
    assert "secret-chatgpt-token" not in rendered
    assert "secret-github-token" not in rendered


def test_selected_primary_and_all_configured_fallback_states_run_through_real_brain_query(
    monkeypatch: pytest.MonkeyPatch,
):
    """Enter at brain.query and exercise success, failure, empty, and cooldown routing."""
    import config

    from core.llm_clients import client as llm_client

    _ensure_brain_path()
    from wisp_brain import handlers

    monkeypatch.delenv("WISP_BRAIN_FAKE_LLM", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "primary")
    monkeypatch.setattr(
        config,
        "LLM_FALLBACKS",
        "openai:fallback-one\nopenai:fallback-two",
    )
    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", False)
    monkeypatch.setattr(config, "PLANNED_CHUNKING", False)

    def run_query(behaviors):
        calls = []
        events = []

        def fake_route(provider, model, user_message, *_args, **_kwargs):
            calls.append((provider, model, user_message))
            behavior = behaviors[(provider, model)]
            if isinstance(behavior, Exception):
                raise behavior
            yield from behavior

        monkeypatch.setattr(llm_client, "_stream_single_response_route", fake_route)
        ctx = handlers.StreamContext(
            lambda event, data, req_id: events.append((event, data, req_id)),
            71,
        )
        result = handlers.brain_query(
            ctx,
            intent_prompt="route this request",
            memory_enabled=False,
            harness_provider="wisp",
            conversation_owner="wisp",
        )
        return calls, events, result

    scenarios = [
        (
            {
                ("openai", "primary"): ["primary ", "answer"],
                ("openai", "fallback-one"): ["unused"],
                ("openai", "fallback-two"): ["unused"],
            },
            [("openai", "primary", "route this request")],
            "primary answer",
        ),
        (
            {
                ("openai", "primary"): RuntimeError("429 rate limit"),
                ("openai", "fallback-one"): ["fallback one answer"],
                ("openai", "fallback-two"): ["unused"],
            },
            [
                ("openai", "primary", "route this request"),
                ("openai", "fallback-one", "route this request"),
            ],
            "fallback one answer",
        ),
        (
            {
                ("openai", "primary"): [],
                ("openai", "fallback-one"): [],
                ("openai", "fallback-two"): ["fallback two answer"],
            },
            [
                ("openai", "primary", "route this request"),
                ("openai", "fallback-one", "route this request"),
                ("openai", "fallback-two", "route this request"),
            ],
            "fallback two answer",
        ),
    ]
    for behaviors, expected_calls, expected_text in scenarios:
        llm_client._route_cooldowns.clear()
        calls, events, result = run_query(behaviors)
        assert calls == expected_calls
        assert result["text"] == expected_text
        assert [data for event, data, _req_id in events if event == "reply.done"] == [result]

    llm_client._route_cooldowns.clear()
    cooldown_behaviors = {
        ("openai", "primary"): RuntimeError("429 rate limit"),
        ("openai", "fallback-one"): ["cooled fallback"],
        ("openai", "fallback-two"): ["unused"],
    }
    first_calls, _events, first = run_query(cooldown_behaviors)
    second_calls, _events, second = run_query(cooldown_behaviors)
    assert first["text"] == second["text"] == "cooled fallback"
    assert [model for _provider, model, _prompt in first_calls] == ["primary", "fallback-one"]
    assert [model for _provider, model, _prompt in second_calls] == ["fallback-one"]
    llm_client._route_cooldowns.clear()


def test_supervisor_rewrite_snip_voice_and_dictation_workflow(tmp_path: Path):
    """Supervisor-level user paths cover paste-back, screenshots, voice, and dictation."""
    from tests.runtime.test_flows import (
        FakeWorker,
        caller_config,
        context_handler,
        make_flow,
        query_stream,
        voice_config,
    )

    rewrite_rows = [
        {
            "paste_back": True,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="bad grammar", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.rewrite": query_stream("good grammar")})
    with caller_config(rewrite_rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "Fix grammar"})

    assert native.last_call("native.context.snapshot")["params"]["capture_focus"] is True
    assert brain.last_call("brain.rewrite")["params"]["selected_text"] == "bad grammar"
    paste = native.last_call("native.paste_text")["params"]
    assert paste["text"] == "good grammar"
    assert paste["target_pid"] == 777
    assert paste["focus_token"] == 9
    assert paste["restore_clipboard"] is True
    assert not ui.calls_for("ui.reply.notice")

    image_path = tmp_path / "snip.png"
    image_bytes = b"workflow-snip-image"
    image_path.write_bytes(image_bytes)
    snip_rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.capture.region": lambda _params: {"ok": True, "path": str(image_path)},
            "native.context.snapshot": context_handler(selected=""),
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("vision reply")})
    with caller_config(snip_rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "snip"})
        ui.emit("ui.snip.region", {"x": 1, "y": 2, "width": 30, "height": 40})
        ui.emit("ui.intent.chosen", {"custom": "What is in this image?"})

    assert ui.calls_for("ui.show_snip")
    assert native.last_call("native.capture.region")["params"]["region"]["height"] == 40
    assert brain.last_call("brain.query")["params"]["screenshot_b64"] == (
        __import__("base64").b64encode(image_bytes).decode("ascii")
    )

    voice_row = {
        "label": "Voice",
        "paste_back": False,
        "context_ambient": True,
        "context_clipboard": False,
        "context_documents_mode": "off",
        "context_browser_mode": "model",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "tools": {"alpha": "on", "beta": "model"},
    }
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "voice prompt"}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("voice reply")})
    with voice_config(voice_row):
        _flow, native, ui, brain, audio = make_flow(native=native, audio=audio, brain=brain)
        native.emit("native.hotkey", {"kind": "voice_start"})
        native.emit("native.hotkey", {"kind": "voice_stop"})

    assert audio.calls_for("audio.record.start")
    assert ui.last_call("ui.reply.transcript")["params"]["text"] == "voice prompt"
    voice_query = brain.last_call("brain.query")["params"]
    assert voice_query["intent_prompt"] == "voice prompt"
    assert voice_query["memory_enabled"] is False
    assert set(voice_query["allowed_tools"]) >= {"web_search", "get_context.browser", "alpha", "beta"}

    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "dictated words"}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)
    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_stop"})

    dictation_snapshot = native.calls_for("native.context.snapshot")[0]["params"]
    dictation_paste = native.last_call("native.paste_text")["params"]
    assert dictation_snapshot["capture_focus"] is True
    assert dictation_paste["text"] == "dictated words"
    assert dictation_paste["target_pid"] == 777
    assert dictation_paste["focus_token"] == 9
    assert not _brain.calls_for("brain.query")


def test_context_disabled_sources_preview_and_os_native_contract_workflow(
    monkeypatch: pytest.MonkeyPatch,
):
    """Disabled context does not leak, previews still estimate, and native fakes degrade."""
    from tests.runtime.test_flows import FakeWorker, caller_config, context_handler, make_flow, query_stream

    all_off_policy = {
        "context_ambient": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "context_clipboard": False,
        "file_access": "off",
        "tools": {},
    }
    native = FakeWorker({"native.context.snapshot": context_handler(selected="selected chat bubble")})
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})
    _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)

    ui.emit(
        "ui.chat.request",
        {
            "request_id": "chat-1",
            "messages": [{"role": "user", "content": "can you see hidden context?"}],
            "context_policy": all_off_policy,
        },
    )
    chat_params = brain.last_call("brain.chat")["params"]
    assert not native.calls_for("native.context.snapshot")
    assert "selected chat bubble" not in repr(chat_params["messages"])
    assert chat_params["memory_enabled"] is False
    assert chat_params["allowed_tools"] == []

    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "selected chat text",
                "clipboard_text": "clipboard chat text",
                "active_app": {"name": "Preview App", "pid": 42, "bundle_id": "com.preview"},
                "screen_size": {"width": 1920, "height": 1080},
            },
        }
    )
    _flow, native, ui, _brain, _audio = make_flow(native=native)
    ui.emit("ui.chat.context_preview", {"preview_id": "preview-all-off", "context_policy": all_off_policy})
    chips = {item["id"]: item for item in ui.last_call("ui.chat.context_preview")["params"]["context_items"]}
    assert chips["screenshot"]["state"] == "off"
    assert chips["screenshot"]["tokens"] == "~1.1k tok"
    assert chips["selection"]["tokens"].startswith("~")
    assert chips["clipboard"]["tokens"].startswith("~")
    assert not native.calls_for("native.capture.fullscreen")

    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "model",
            "context_browser_mode": "model",
            "context_github_mode": "auto",
            "context_memory_mode": "off",
            "context_tools": True,
            "context_screenshot": "model",
            "context_clipboard": False,
            "tools": {
                "web_search": "off",
                "get_context": "off",
                "retrieve_website": "off",
                "git_status": "off",
                "capture_screen": "off",
            },
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="picked")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("reply")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "go"})
    query = brain.last_call("brain.query")["params"]
    assert query["allowed_tools"] == []
    assert query["use_tools"] is False
    assert query["frontload_tools"] == ["git_diff"]
    assert query["allow_screenshot_tool"] is False
    assert not native.calls_for("native.capture.fullscreen")

    from core import context_fetcher
    from runtime.workers import native_host

    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "IS_MAC", True)
    monkeypatch.setattr(context_fetcher, "_IS_WIN", False)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", True)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "TextEdit", "pid": 101, "bundle_id": "com.apple.TextEdit"},
    )
    monkeypatch.setattr(native_host, "selected_text", lambda **_kwargs: "")
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": ""})
    monkeypatch.setattr(
        "core.platform.macos_native.list_document_windows",
        lambda: [
            {"process_name": "TextEdit", "pid": 101, "frontmost": True, "title": "Notes.txt"},
            {"process_name": "Safari", "pid": 303, "frontmost": False, "title": "Example Page"},
        ],
    )
    monkeypatch.setattr(context_fetcher, "_mac_browser_url", lambda _app: "https://example.test/page")
    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )
    assert snapshot["document_window"]["title"] == "Notes.txt"
    assert snapshot["browser_app"] == "Safari"
    assert snapshot["browser_url"] == "https://example.test/page"


def test_prompt_tools_and_memory_scheduler_settings_workflow(
    isolated_app_state: IsolatedAppState,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Prompt, tool visibility, and memory scheduling settings change runtime behavior."""
    import config
    from core.memory_store import store as memory_store
    from core.tool_registry import ToolRegistry, ToolSpec

    monkeypatch.setattr(config, "SYSTEM_PROMPT_UTILITY", "Base workflow system prompt.", raising=False)
    monkeypatch.setattr(config, "ASSISTANT_LANGUAGE", "Spanish", raising=False)
    prompt = config.get_system_prompt()
    assert "Base workflow system prompt." in prompt
    assert "Respond in Spanish" in prompt

    registry = ToolRegistry(plugin_dir=tmp_path / "no-tools")
    registry.register_builtin(
        ToolSpec(
            name="workflow_tool",
            description="Workflow demo tool",
            input_schema={"type": "object", "properties": {}},
            executor=lambda _inputs: "ok",
        )
    )
    assert "workflow_tool" in {
        schema["name"] for schema in registry.filtered_schemas("hello there", include_server_tools=False)
    }
    reloaded = ToolRegistry(plugin_dir=tmp_path / "no-tools")
    reloaded.register_builtin(
        ToolSpec(
            name="workflow_tool",
            description="Workflow demo tool",
            input_schema={"type": "object", "properties": {}},
            executor=lambda _inputs: "ok",
        )
    )
    assert "workflow_tool" in {
        schema["name"] for schema in reloaded.filtered_schemas("anything now", include_server_tools=False)
    }

    timers: list[Any] = []

    class FakeTimer:
        def __init__(self, interval: float, callback):
            self.interval = interval
            self.callback = callback
            self.daemon = False
            self.started = False
            self.cancelled = False
            timers.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(memory_store.threading, "Timer", FakeTimer)
    monkeypatch.setattr(
        memory_store.macos_safety,
        "memory_background_llm_enabled",
        lambda: True,
    )
    monkeypatch.setattr(memory_store.config, "MEMORY_AUTO_CONSOLIDATE", False, raising=False)
    monkeypatch.setattr(memory_store.config, "MEMORY_CONSOLIDATION_INTERVAL", 7, raising=False)
    manager = memory_store.MemoryManager()
    assert timers == []

    monkeypatch.setattr(memory_store.config, "MEMORY_AUTO_CONSOLIDATE", True, raising=False)
    manager._sync_consolidation_timer()
    assert timers[-1].interval == 7 * 60
    assert timers[-1].started is True
    assert timers[-1].daemon is True

    monkeypatch.setattr(memory_store.config, "MEMORY_AUTO_CONSOLIDATE", False, raising=False)
    manager._sync_consolidation_timer()
    assert timers[-1].cancelled is True
    assert manager._consolidation_timer is None
    assert _is_relative_to(isolated_app_state.memory, isolated_app_state.root)


def test_brain_memory_crud_and_project_scope_workflow(
    isolated_app_state: IsolatedAppState,
    monkeypatch: pytest.MonkeyPatch,
):
    """The Memory UI backend can add, search, edit, delete, and scope facts."""
    _ensure_brain_path()
    from wisp_brain import handlers

    from core.memory_store import store as memory_store

    memory_store.set_active_project("alpha-project")
    project_add = handlers.brain_memory_add(
        text="The alpha project launch phrase is silver comet.",
        scope="project",
    )
    general_add = handlers.brain_memory_add(
        text="The global preferred editor is Nova.",
        category="preference",
    )

    assert project_add["ok"] is True
    assert project_add["project"] == "alpha-project"
    assert general_add["ok"] is True
    listed = handlers.brain_memory_list()["facts"]
    assert {item["text"] for item in listed} >= {
        "The alpha project launch phrase is silver comet.",
        "The global preferred editor is Nova.",
    }

    project_fact = next(item for item in listed if "silver comet" in item["text"])
    search = handlers.brain_memory_search(query="What is the launch phrase?", top_k=5)
    assert "silver comet" in search["text"]

    updated = handlers.brain_memory_update(
        fact_id=project_fact["id"],
        text="The alpha project launch phrase is blue comet.",
        project="alpha-project",
    )
    assert updated["ok"] is True
    assert "blue comet" in handlers.brain_memory_search(query="launch phrase", top_k=5)["text"]

    deleted = handlers.brain_memory_delete(fact_id=project_fact["id"])
    assert deleted["ok"] is True
    after_delete = handlers.brain_memory_list()["facts"]
    assert all(item["id"] != project_fact["id"] for item in after_delete)
    assert _is_relative_to(isolated_app_state.memory, isolated_app_state.root)

    memory_store.set_active_project(None)
    monkeypatch.setattr(memory_store, "_manager", None)


def test_brain_rewrite_chat_tts_and_route_setting_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Settings test buttons and text workflows work offline without spending tokens."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    monkeypatch.setenv("WISP_RUNTIME_OUTPUT_DIR", str(tmp_path / "runtime-output"))
    _ensure_brain_path()
    from wisp_brain import handlers

    rewrite_events, rewrite_ctx = _recording_stream_context("rewrite")
    rewrite = handlers.brain_rewrite(
        rewrite_ctx,
        selected_text="this sentence need polish",
        intent_prompt="Make it concise",
    )
    assert rewrite["text"].startswith("[fake-rewrite]")
    assert any(event == "reply.done" for event, _payload in rewrite_events)

    chat_events, chat_ctx = _recording_stream_context("chat")
    chat = handlers.brain_chat(
        chat_ctx,
        messages=[
            {"role": "system", "content": "Keep replies tiny."},
            {"role": "user", "content": "What is my last message?"},
        ],
        memory_enabled=False,
    )
    assert "[fake-chat] What is my last message?" in chat["text"]
    assert any(event == "reply.chunk" for event, _payload in chat_events)

    route = handlers.brain_llm_test(
        provider="chatgpt",
        model="gpt-5.5",
        fallbacks="openai:gpt-4.1-mini",
        route_name="Chat",
    )
    assert route["ok"] is True
    assert [item["label"] for item in route["routes"]] == ["Primary", "Fallback 1"]
    assert route["routes"][0]["model"] == "gpt-5.5"

    tts_test = handlers.brain_tts_test(provider="none")
    assert tts_test["ok"] is True
    assert tts_test["provider"] == "none"
    tts = handlers.brain_tts_synthesize(text="short test")
    assert Path(tts["path"]).exists()
    assert tts["provider"] == "fake"


@pytest.mark.usefixtures("isolated_default_profile")
def test_settings_env_changes_reach_runtime_surfaces_workflow(
    tmp_path: Path,
    isolated_app_state: IsolatedAppState,
    monkeypatch: pytest.MonkeyPatch,
):
    """Saved settings reload into config and change the surfaces users touch."""
    import config
    from core.memory_store import store as memory_store

    managed_keys = {
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_FALLBACKS",
        "CHAT_LLM_PROVIDER",
        "CHAT_LLM_MODEL",
        "VISION_LLM_PROVIDER",
        "VISION_LLM_MODEL",
        "TTS_PROVIDER",
        "STT_MODEL",
        "STT_DEVICE",
        "STT_LANGUAGE",
        "STT_BEAM_SIZE",
        "BUBBLE_WIDTH",
        "BUBBLE_LINES",
        "BUBBLE_FONT_SIZE",
        "BUBBLE_HIDE_DELAY_MS",
        "INTENT_CONTEXT_TOGGLE_KEYS",
        "CALLER_COUNT",
        "CALLER_1_LABEL",
        "CALLER_1_CONTEXT_AMBIENT",
        "CALLER_1_CONTEXT_DOCUMENTS_MODE",
        "CALLER_1_CONTEXT_BROWSER_MODE",
        "CALLER_1_CONTEXT_MEMORY_MODE",
        "CALLER_1_CONTEXT_SCREENSHOT",
        "CALLER_1_FILE_ACCESS",
        "VOICE_CONTEXT_AMBIENT",
        "VOICE_CONTEXT_DOCUMENTS_MODE",
        "VOICE_CONTEXT_BROWSER_MODE",
        "VOICE_CONTEXT_MEMORY_MODE",
        "VOICE_CONTEXT_SCREENSHOT",
        "MEMORY_LLM_PROVIDER",
        "MEMORY_LLM_MODEL",
        "MEMORY_LLM_FALLBACKS",
        "MEMORY_TOP_K",
        "MEMORY_STM_TOKEN_BUDGET",
        "CONTEXT_BROWSER_MAX_CHARS",
        "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS",
        "CONTEXT_TOOL_DOCUMENT_MAX_CHARS",
        "PRIVACY_MODE",
        "TRUST_PRIVACY_MODE",
        "PRIVACY_AI_ENABLED",
    }
    original_env_file = config._ENV_FILE
    original_loaded_keys = set(config._LOADED_DOTENV_KEYS)
    original_env_values = {key: os.environ.get(key) for key in managed_keys}
    temp_env = tmp_path / ".env"
    temp_env.write_text(
        "\n".join(
            [
                "LLM_PROVIDER=chatgpt",
                "LLM_MODEL=gpt-5.5",
                "LLM_FALLBACKS=openai:gpt-4.1-mini",
                "CHAT_LLM_PROVIDER=anthropic",
                "CHAT_LLM_MODEL=claude-sonnet-4-5",
                "VISION_LLM_PROVIDER=openai",
                "VISION_LLM_MODEL=gpt-4.1-mini",
                "TTS_PROVIDER=none",
                "STT_MODEL=small",
                "STT_DEVICE=cpu",
                "STT_LANGUAGE=en",
                "STT_BEAM_SIZE=3",
                "BUBBLE_WIDTH=456",
                "BUBBLE_LINES=4",
                "BUBBLE_FONT_SIZE=19",
                "BUBBLE_HIDE_DELAY_MS=2400",
                "INTENT_CONTEXT_TOGGLE_KEYS=7654321",
                "CALLER_COUNT=1",
                "CALLER_1_LABEL=Workflow Caller",
                "CALLER_1_CONTEXT_AMBIENT=False",
                "CALLER_1_CONTEXT_DOCUMENTS_MODE=model",
                "CALLER_1_CONTEXT_BROWSER_MODE=auto",
                "CALLER_1_CONTEXT_MEMORY_MODE=off",
                "CALLER_1_CONTEXT_SCREENSHOT=model",
                "CALLER_1_FILE_ACCESS=read",
                "VOICE_CONTEXT_AMBIENT=False",
                "VOICE_CONTEXT_DOCUMENTS_MODE=off",
                "VOICE_CONTEXT_BROWSER_MODE=model",
                "VOICE_CONTEXT_MEMORY_MODE=model",
                "VOICE_CONTEXT_SCREENSHOT=auto",
                "MEMORY_LLM_PROVIDER=openai",
                "MEMORY_LLM_MODEL=gpt-4.1-mini",
                "MEMORY_LLM_FALLBACKS=chatgpt:gpt-5.5",
                "MEMORY_TOP_K=1",
                "MEMORY_STM_TOKEN_BUDGET=1234",
                "CONTEXT_BROWSER_MAX_CHARS=111",
                "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS=222",
                "CONTEXT_TOOL_DOCUMENT_MAX_CHARS=333",
                "TRUST_PRIVACY_MODE=True",
            ]
        ),
        encoding="utf-8",
    )

    try:
        for key in managed_keys:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(config, "_ENV_FILE", temp_env)
        monkeypatch.setattr(config, "_LOADED_DOTENV_KEYS", set())
        monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
        config.reload()

        settings = config.get_settings()
        assert settings.llm.model == "gpt-5.5"
        assert settings.chat_llm.provider == "chatgpt"
        assert settings.chat_llm.model == "gpt-5.5"
        assert settings.vision_llm.model == "gpt-4.1-mini"
        assert settings.ui.bubble_width == 456
        assert settings.ui.bubble_font_size == 19
        assert settings.audio.tts_provider == "none"
        assert settings.audio.stt_model == "small"
        assert settings.memory.top_k == 1
        assert settings.context.browser_max_chars == 111
        assert config.INTENT_CONTEXT_TOGGLE_KEYS == "76543218"

        caller = settings.callers.callers[0]
        assert caller["label"] == "Workflow Caller"
        assert caller["context_ambient"] is False
        assert caller["context_documents_mode"] == "model"
        assert caller["context_browser_mode"] == "auto"
        assert caller["context_memory_mode"] == "off"
        assert caller["context_screenshot"] == "model"
        assert caller["file_access"] == "read"

        voice = settings.callers.voice
        assert voice["context_ambient"] is False
        assert voice["context_browser_mode"] == "model"
        assert voice["context_memory_mode"] == "model"
        assert voice["context_screenshot"] == "auto"

        _ensure_brain_path()
        from wisp_brain import handlers

        route = handlers.brain_llm_test(
            provider=settings.memory.model.provider,
            model=settings.memory.model.model,
            fallbacks=settings.memory.model.fallbacks,
            route_name="Memory",
        )
        assert route["ok"] is True
        assert [(item["provider"], item["model"]) for item in route["routes"]] == [
            ("openai", "gpt-4.1-mini"),
            ("chatgpt", "gpt-5.5"),
        ]

        memory_store.set_active_project(None)
        memory_store.get_manager().add_fact_manual("first workflow memory fact", "general")
        memory_store.get_manager().add_fact_manual("second workflow memory fact", "general")
        search = handlers.brain_memory_search(query="workflow memory fact")
        assert search["text"].count("workflow memory fact") <= config.MEMORY_TOP_K
        assert _is_relative_to(isolated_app_state.memory, isolated_app_state.root)
    finally:
        for key, value in original_env_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config._ENV_FILE = original_env_file
        config._LOADED_DOTENV_KEYS = original_loaded_keys
        config.reload()
        memory_store.set_active_project(None)
        monkeypatch.setattr(memory_store, "_manager", None)


def test_brain_addon_install_settings_actions_and_toggle_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Add-on management covers install, list, setting save, actions, hotkeys, enable state."""
    _ensure_brain_path()
    from wisp_brain import handlers

    import core.addon_manager as addon_manager
    import core.system.paths as system_paths
    from core import addon_store

    addons_dir = tmp_path / "installed-addons"
    source = tmp_path / "source-addon"
    source.mkdir()
    (source / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "workflow-demo"
            name = "workflow_demo"
            entry = "__init__.py"

            [permissions]
            query = "modify"
            response = "read"
            hotkeys = true
            ui = ["tray", "settings", "intents", "notifications"]

            [[hotkeys]]
            id = "static-hotkey"
            label = "Static hotkey"
            hotkey = "ctrl+alt+w"
            prompt = "Static workflow prompt"

            [[notifications]]
            title = "Workflow"
            message = "Loaded"
            """
        ).strip(),
        encoding="utf-8",
    )
    (source / "__init__.py").write_text(
        textwrap.dedent(
            """
            def before_query(prompt, context):
                return prompt + " from addon", context + " addon-context"

            def get_tray_actions():
                return [{"label": "Act", "callback": lambda: None}]

            def run_tray_action(label):
                return {"message": "ran " + str(label)}

            def get_settings():
                return [{"key": "greeting", "label": "Greeting", "type": "text", "default": "hi"}]

            def get_hotkeys():
                return [{"id": "dynamic-hotkey", "label": "Dynamic", "hotkey": "ctrl+alt+d", "callback": lambda payload: {"message": "hotkey ok"}}]

            def get_intents():
                return [{"id": "dynamic", "label": "Dynamic", "key": "d", "prompt": "Dynamic prompt"}]

            def get_notifications():
                return [{"title": "Runtime", "message": "Ready"}]
            """
        ).strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(system_paths, "ADDONS_DIR", addons_dir)
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(addon_manager.addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(addon_manager, "_manager", None)
    monkeypatch.setattr(handlers, "_addon_startup_done", False)

    try:
        installed = handlers.brain_addons_install_folder(path=str(source))
        assert installed["id"] == "workflow-demo"
        assert Path(installed["path"]).is_dir()
        listed = handlers.brain_addons_list()["addons"]
        addon = next(item for item in listed if item["id"] == "workflow-demo")
        assert addon["enabled"] is True
        assert "Act" in addon["tray_actions"]
        assert addon["settings"][0]["value"] == "hi"
        assert {item["id"] for item in addon["hotkeys"]} >= {"static-hotkey", "dynamic-hotkey"}

        assert handlers.brain_addons_set_setting(
            addon_id="workflow-demo",
            key="greeting",
            value="hello",
        )["ok"] is True
        settings_after_save = next(
            item for item in handlers.brain_addons_list()["addons"] if item["id"] == "workflow-demo"
        )["settings"]
        assert settings_after_save[0]["value"] == "hello"

        assert handlers.brain_addons_run_hotkey(
            addon_id="workflow-demo",
            hotkey_id="static-hotkey",
        ) == {"prompt": "Static workflow prompt"}
        dynamic_hotkey = handlers.brain_addons_run_hotkey(
            addon_id="workflow-demo",
            hotkey_id="dynamic-hotkey",
        )
        assert dynamic_hotkey["message"] == "hotkey ok"
        assert handlers.brain_addons_run_action(addon_id="workflow-demo", label="Act")["ok"] is True

        assert handlers.brain_addons_set_enabled(addon_id="workflow-demo", enabled=False)["enabled"] is False
        disabled = next(item for item in handlers.brain_addons_list()["addons"] if item["id"] == "workflow-demo")
        assert disabled["enabled"] is False
        with pytest.raises(ValueError):
            handlers.brain_addons_run_action(addon_id="workflow-demo", label="Act")

        assert handlers.brain_addons_set_enabled(addon_id="workflow-demo", enabled=True)["enabled"] is True
        enabled = next(item for item in handlers.brain_addons_list()["addons"] if item["id"] == "workflow-demo")
        assert enabled["enabled"] is True
    finally:
        try:
            addon_manager.get_manager().on_shutdown()
        except Exception:
            pass
        monkeypatch.setattr(addon_manager, "_manager", None)


def test_addon_update_remove_bad_archive_and_credentials_reset_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Add-on reload/update/remove failures and credential reset stay isolated."""
    _ensure_brain_path()
    from wisp_brain import handlers

    import config
    import core.addon_manager as addon_manager
    import core.system.paths as system_paths
    from core import addon_store, secret_store
    from core.addon_distribution import install_addon_archive, install_addon_folder

    addons_dir = tmp_path / "addons"
    monkeypatch.setattr(system_paths, "ADDONS_DIR", addons_dir)
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(addon_manager.addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(addon_manager, "_manager", None)

    source = tmp_path / "archive-source" / "archive_demo"
    source.mkdir(parents=True)
    (source / "addon.toml").write_text(
        "[addon]\nid = 'archive-demo'\nname = 'Archive Demo'\nentry = '__init__.py'\n"
        "[permissions]\nui = ['settings']\n"
        "[[settings]]\nkey = 'version'\nlabel = 'Version'\ntype = 'text'\ndefault = 'v1'\n",
        encoding="utf-8",
    )
    (source / "__init__.py").write_text("", encoding="utf-8")
    archive = tmp_path / "archive-demo.wisp"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(source / "addon.toml", "archive_demo/addon.toml")
        zf.write(source / "__init__.py", "archive_demo/__init__.py")

    unsafe = tmp_path / "unsafe.wisp"
    with zipfile.ZipFile(unsafe, "w") as zf:
        zf.writestr("../escape.txt", "nope")
    with pytest.raises(ValueError, match="unsafe"):
        install_addon_archive(unsafe, addons_dir)

    installed = handlers.brain_addons_install_archive(path=str(archive))
    assert installed["id"] == "archive-demo"
    addon = next(item for item in handlers.brain_addons_list()["addons"] if item["id"] == "archive-demo")
    assert addon["settings"][0]["value"] == "v1"
    assert handlers.brain_addons_set_setting("archive-demo", "version", "custom")["ok"] is True
    settings_path = tmp_path / "addon_data" / "archive-demo" / "settings.json"
    assert "custom" in settings_path.read_text(encoding="utf-8")
    assert "custom" not in (tmp_path / "addons.json").read_text(encoding="utf-8")

    updated = tmp_path / "updated-source" / "archive_demo"
    updated.mkdir(parents=True)
    (updated / "addon.toml").write_text(
        "[addon]\nid = 'archive-demo'\nname = 'Archive Demo'\nentry = '__init__.py'\n"
        "description = 'updated manifest'\n"
        "[permissions]\nui = ['settings']\n"
        "[[settings]]\nkey = 'version'\nlabel = 'Version'\ntype = 'text'\ndefault = 'v2'\n",
        encoding="utf-8",
    )
    (updated / "__init__.py").write_text("", encoding="utf-8")
    install_addon_folder(updated, addons_dir, replace=True)
    addon_manager.get_manager().load_all()
    updated_payload = next(item for item in handlers.brain_addons_list()["addons"] if item["id"] == "archive-demo")
    assert updated_payload["description"] == "updated manifest"
    assert updated_payload["settings"][0]["value"] == "custom"

    shutil.rmtree(addons_dir / "archive-demo")
    addon_manager.get_manager().load_all()
    assert all(item["id"] != "archive-demo" for item in handlers.brain_addons_list()["addons"])

    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=should-be-cleared\nUNRELATED_VALUE=kept\n", encoding="utf-8")
    monkeypatch.setattr(config, "_ENV_FILE", env_file)
    monkeypatch.setenv("OPENAI_API_KEY", "should-be-cleared")
    monkeypatch.setenv("UNRELATED_VALUE", "kept")
    deleted: list[str] = []
    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ["OPENAI_API_KEY", "CUSTOM_API_KEY"])
    monkeypatch.setattr(secret_store, "delete_secret", lambda name: deleted.append(name))

    from core.auth import chatgpt as chatgpt_auth
    from core.auth import copilot_auth
    from core.auth import github as github_auth

    cleared_auth: list[str] = []
    monkeypatch.setattr(chatgpt_auth, "clear_tokens", lambda: cleared_auth.append("chatgpt"))
    monkeypatch.setattr(github_auth, "clear_tokens", lambda: cleared_auth.append("github"))
    monkeypatch.setattr(copilot_auth, "clear_token", lambda: cleared_auth.append("copilot"))

    reset = handlers.brain_settings_reset_credentials()
    assert reset["ok"] is True
    assert set(deleted) == {"OPENAI_API_KEY", "CUSTOM_API_KEY"}
    assert cleared_auth == ["chatgpt", "github", "copilot"]
    assert os.environ.get("OPENAI_API_KEY") is None
    assert os.environ.get("UNRELATED_VALUE") == "kept"

    try:
        addon_manager.get_manager().on_shutdown()
    except Exception:
        pass
    monkeypatch.setattr(addon_manager, "_manager", None)


def test_agent_history_last_spec_and_approval_response_workflow(tmp_path: Path):
    """The agent task UI can save/copy-last, read run history, continue, and resolve approvals."""
    _ensure_brain_path()
    from wisp_brain import handlers

    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / "README.md").write_text("# Demo\n\nWorkflow scope.", encoding="utf-8")
    log_root = tmp_path / "agent-log-root"
    spec = _minimal_agent_spec(scope)

    saved = handlers.brain_agent_last_spec_write(spec=spec, log_root=str(log_root))
    assert saved["ok"] is True
    assert saved["spec"]["title"] == "Workflow Agent"
    assert handlers.brain_agent_last_spec_read(log_root=str(log_root))["spec"]["scope_folder"] == str(scope)

    run_dir = log_root / "20260620-010203-workflow"
    run_dir.mkdir(parents=True)
    (run_dir / "task.json").write_text(json.dumps(spec), encoding="utf-8")
    (run_dir / "run.log").write_text("agent run finished\n", encoding="utf-8")
    (run_dir / "final.md").write_text("Final report for workflow.", encoding="utf-8")
    (run_dir / "diff.patch").write_text("diff --git a/README.md b/README.md\n", encoding="utf-8")

    history = handlers.brain_agent_history_list(log_root=str(log_root), limit=5)
    assert history["runs"][0]["id"] == run_dir.name
    assert history["runs"][0]["status"] == "complete"
    read = handlers.brain_agent_history_read(run_dir=str(run_dir))
    assert "Final report for workflow." in read["final"]
    assert handlers.brain_agent_history_retry_spec(run_dir=str(run_dir))["spec"]["title"] == "Workflow Agent"
    continued = handlers.brain_agent_history_continue_spec(run_dir=str(run_dir))["spec"]
    assert continued["title"].startswith("Continue:")
    assert "Previous final report" in continued["required_context"]

    approval_id = "approval-workflow"
    event = threading.Event()
    with handlers._AGENT_APPROVALS_LOCK:
        handlers._AGENT_APPROVALS[approval_id] = {"event": event, "approved": False}
    response = handlers.brain_agent_approval_respond(approval_id=approval_id, approved=True)
    assert response == {"ok": True, "approved": True}
    assert event.wait(0.1)
    with handlers._AGENT_APPROVALS_LOCK:
        handlers._AGENT_APPROVALS.pop(approval_id, None)
    assert handlers.brain_agent_approval_respond(approval_id=approval_id, approved=True)["ok"] is False

    live_id = "live-file-workflow"
    live_event = threading.Event()
    with handlers._LIVE_FILE_APPROVALS_LOCK:
        handlers._LIVE_FILE_APPROVALS[live_id] = {"event": live_event, "approved": False}
    live_response = handlers.brain_live_file_approval_respond(approval_id=live_id, approved=False)
    assert live_response == {"ok": True, "approved": False}
    assert live_event.wait(0.1)
    with handlers._LIVE_FILE_APPROVALS_LOCK:
        handlers._LIVE_FILE_APPROVALS.pop(live_id, None)


def test_auto_agent_run_streams_logs_and_persists_artifacts_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A real brain.agent.run completes offline and produces UI-readable artifacts."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    _ensure_brain_path()
    from wisp_brain import handlers

    scope = tmp_path / "agent-scope"
    scope.mkdir()
    (scope / "README.md").write_text("# Agent Workflow\n", encoding="utf-8")
    log_root = tmp_path / "agent-runs"
    spec = _minimal_agent_spec(scope)

    events, ctx = _recording_stream_context("agent-run")
    result = handlers.brain_agent_run(ctx, spec=spec, log_root=str(log_root))

    run_dir = Path(result["run_dir"])
    assert run_dir.is_dir()
    assert result["error"] == ""
    assert "Fake agent run complete." in result["final"]
    assert (run_dir / "task.json").exists()
    assert (run_dir / "run.log").exists()
    assert (run_dir / "final.md").read_text(encoding="utf-8").strip()
    assert any(event == "agent.log" for event, _payload in events)
    assert events[-1][0] == "agent.done"
    assert events[-1][1]["run_dir"] == str(run_dir)

    history = handlers.brain_agent_history_list(log_root=str(log_root), limit=5)
    assert any(item["run_dir"] == str(run_dir) for item in history["runs"])
    continued = handlers.brain_agent_history_continue_spec(run_dir=str(run_dir))["spec"]
    assert continued["title"].startswith("Continue:")
    assert "Fake agent run complete." in continued["required_context"]


def test_launch_duplicate_crash_log_and_worker_lifecycle_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Launch behavior covers duplicate handoff, UI-worker exit, crash logs, and shutdown."""
    from runtime.supervisor import app as supervisor_app

    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.delenv("WISP_RUNTIME_LOG_MODE", raising=False)
    monkeypatch.setattr(supervisor_app, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(supervisor_app, "suppress_console_ctrl_c", lambda: None)
    monkeypatch.setattr(supervisor_app, "install_crash_diagnostics", lambda: None)
    monkeypatch.setattr(supervisor_app, "_resume_staged_optional_installs", lambda: None)

    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: False)

    class ShouldNotStart:
        def __init__(self):
            raise AssertionError("duplicate launch must not start workers")

    monkeypatch.setattr(supervisor_app, "WispSupervisor", ShouldNotStart)
    assert supervisor_app.main() == 2
    assert not (tmp_path / "build_logs").exists()

    instances: list[Any] = []

    class FakeWorker:
        def __init__(self, name: str):
            self.name = name
            self.exit_handler = None

        def on_exit(self, handler):
            self.exit_handler = handler

        def stderr_tail(self, _max_lines: int = 20) -> str:
            return f"{self.name} recent diagnostic line"

    class FakeSupervisor:
        def __init__(self):
            self.workers = {name: FakeWorker(name) for name in ("native", "ui", "brain", "audio")}
            self.shutdown_called = False
            instances.append(self)

        def start_all(self):
            self.workers["ui"].exit_handler(9)

        def shutdown(self):
            self.shutdown_called = True

    class FakeFlows:
        def __init__(self, **_workers):
            pass

        def start(self):
            pass

        def start_hotkeys(self):
            pass

    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: True)
    monkeypatch.setattr(supervisor_app, "WispSupervisor", FakeSupervisor)
    monkeypatch.setattr(supervisor_app, "FlowController", FakeFlows)

    assert supervisor_app.main() == 0
    assert instances and instances[-1].shutdown_called is True
    crash_logs = list((tmp_path / "build_logs").glob("wisp_crash_*/supervisor-crash.log"))
    assert len(crash_logs) == 1
    report = crash_logs[0].read_text(encoding="utf-8")
    assert "UI worker exited with code 9" in report
    assert "ui recent diagnostic line" in report


def test_persistence_corruption_migration_and_reset_scope_workflow(
    tmp_path: Path,
    isolated_app_state: IsolatedAppState,
    monkeypatch: pytest.MonkeyPatch,
):
    """Corrupt/legacy user data recovers while resets remove only intended app state."""
    import config
    from core.conversation_store import store as conversations
    from core.system.env_utils import read_env_file

    conversations.PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    conversations.PROJECTS_FILE.write_text("{not valid json", encoding="utf-8")
    conversations.CONVERSATIONS_FILE.write_text("{not valid json", encoding="utf-8")

    projects = conversations.load_projects()
    assert projects[0]["id"] == conversations.GENERAL_PROJECT_ID
    assert conversations.load_conversations() == []

    legacy = {
        "id": "legacy-chat",
        "project_id": "missing-project",
        "title": "Legacy",
        "messages": [
            {
                "role": "user",
                "content": "hello",
                "image_base64": "legacy-inline-image",
                "attachments": [{"path": ""}, {"path": str(tmp_path / "note.txt"), "kind": "text"}],
            }
        ],
    }
    conversations.CONVERSATIONS_FILE.write_text(json.dumps([legacy]), encoding="utf-8")
    loaded = conversations.load_conversations()
    assert loaded[0]["project_id"] == "missing-project"
    assert "image_base64" not in loaded[0]["messages"][0]
    assert loaded[0]["messages"][0]["attachments"][0]["kind"] == "text"

    _ensure_brain_path()
    from wisp_brain import handlers

    from core import secret_store
    from core.auth import chatgpt, copilot_auth, github

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890\n"  # secret-scan: allow
        "LLM_PROVIDER=chatgpt\n"
        "THEME_MODE=dark\n",
        encoding="utf-8",
    )
    original_env_file = config._ENV_FILE
    original_loaded = set(config._LOADED_DOTENV_KEYS)
    try:
        monkeypatch.setattr(config, "_ENV_FILE", env_file)
        monkeypatch.setattr(config, "_LOADED_DOTENV_KEYS", set())
        monkeypatch.setattr(secret_store, "API_KEY_NAMES", ("OPENAI_API_KEY",), raising=False)
        monkeypatch.setattr(secret_store, "delete_secret", lambda _name: None)
        monkeypatch.setattr(chatgpt, "clear_tokens", lambda: None)
        monkeypatch.setattr(github, "clear_tokens", lambda: None)
        monkeypatch.setattr(copilot_auth, "clear_token", lambda: None)
        config.reload()

        result = handlers.brain_settings_reset_credentials()
        assert result["ok"] is True
        env_values = read_env_file(env_file)
        assert "OPENAI_API_KEY" not in env_values
        assert env_values["LLM_PROVIDER"] == "chatgpt"
        assert env_values["THEME_MODE"] == "dark"
        assert _is_relative_to(isolated_app_state.chats, isolated_app_state.root)
    finally:
        config._ENV_FILE = original_env_file
        config._LOADED_DOTENV_KEYS = original_loaded
        config.reload()


def test_ui_accessibility_layout_and_model_popup_workflow(qapp, monkeypatch: pytest.MonkeyPatch):
    """Settings and auto-agent expose usable labels, focusable controls, and opaque popups."""
    from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton

    import config
    from ui.agent.task_window import AgentTaskDialog
    from ui.settings_panel.dialog import _PROVIDER_LABELS, _PROVIDER_MODELS, SettingsDialog
    from ui.shared.theme import apply_app_theme

    monkeypatch.setattr(config, "LLM_PROVIDER", "chatgpt", raising=False)
    monkeypatch.setattr(config, "LLM_MODEL", "gpt-5.5", raising=False)
    monkeypatch.setattr(config, "THEME_MODE", "dark", raising=False)

    apply_app_theme(qapp)
    assert "QComboBox QAbstractItemView" in qapp.styleSheet()
    assert "background:" in qapp.styleSheet().split("QComboBox QAbstractItemView", 1)[1]

    settings = SettingsDialog()
    agent = AgentTaskDialog()
    try:
        assert settings.findChild(QLineEdit, "settingsSearch") is not None
        assert settings.findChild(QPushButton, "settingsApplyButton") is not None
        assert settings.findChild(QPushButton, "settingsCancelButton") is not None
        assert settings._settings_nav.count() == 8
        assert "BUBBLE_FONT_SIZE" in settings._fields
        assert "CHAT_ELABORATE_PROMPT" in settings._fields

        provider_labels = {agent.provider_combo.itemText(i) for i in range(agent.provider_combo.count())}
        assert _PROVIDER_LABELS["chatgpt"] in provider_labels
        assert _PROVIDER_LABELS["openai"] in provider_labels

        chatgpt_models = _PROVIDER_MODELS["chatgpt"]
        agent_models = {agent.model_edit.itemText(i) for i in range(agent.model_edit.count())}
        assert set(chatgpt_models[: min(3, len(chatgpt_models))]) <= agent_models
        assert agent.model_edit.lineEdit().placeholderText()

        focusable = [
            widget
            for widget in [settings.findChild(QLineEdit, "settingsSearch"), agent.title_edit, agent.objective_edit]
            if widget is not None
        ]
        assert all(widget.focusPolicy() for widget in focusable)
        assert any(combo.count() > 1 for combo in agent.findChildren(QComboBox))
    finally:
        settings.deleteLater()
        agent.deleteLater()


def test_qt_widget_stylesheets_avoid_rgba_parse_warnings_workflow():
    """Qt widget stylesheets avoid color formats that macOS QSS rejects."""
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    search_roots = [root / "ui", root / "runtime" / "workers"]
    for search_root in search_roots:
        for path in sorted(search_root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "rgba(" in text:
                offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_chat_context_chip_stylesheets_use_plain_rgb_colors_workflow(qapp):
    """Chat context chips use conservative colors accepted by Qt on every OS."""
    import re

    from ui.chat_window import ChatWindow
    from ui.shared.theme import apply_app_theme

    window = None
    try:
        apply_app_theme(qapp)
        window = ChatWindow([{"messages": [], "context_policy": {}}], lambda _messages: iter(()))
        qapp.processEvents()
        for chip in window._context_controls.values():
            style = chip.styleSheet()
            assert "rgba(" not in style
            assert not re.search(r"#[0-9a-fA-F]{8}\b", style)
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
        qapp.processEvents()


def test_agent_permission_notice_and_bubble_notice_workflow(qapp, tmp_path: Path):
    """Approval and notice plumbing makes tool/file permission states visible."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from core.agent.task_spec import agent_task_spec_from_dict
    from ui.agent.task_window import AgentRunWindow
    from ui.bubble import SpeechBubble

    notices: list[tuple[str, bool]] = []
    spec = agent_task_spec_from_dict(_minimal_agent_spec(tmp_path))
    window = AgentRunWindow(spec, approval_notice_callback=lambda text, done: notices.append((text, done)))
    bubble = SpeechBubble()
    try:
        window.show()
        qapp.processEvents()
        approval_state = {"event": threading.Event(), "approved": False}
        notices.clear()
        window._show_approval({"action": "write file", "details": {"path": "README.md"}}, approval_state)
        qapp.processEvents()
        assert window.approval_panel.isVisible()
        assert "Permission needed" in window.approval_label.text()
        permission_notices = [notice for notice in notices if notice[0].startswith("Permission needed: write file")]
        assert permission_notices
        assert permission_notices[-1][1] is False

        window._finish_approval(False)
        assert approval_state["event"].wait(0.1)
        assert approval_state["approved"] is False
        assert all(notice[0] != "Permission declined." for notice in notices)

        actions: list[str] = []
        bubble.show_notice(
            "Permission needed.",
            timeout_ms=0,
            actions=[("Approve", lambda: actions.append("approve")), ("Decline", lambda: actions.append("decline"))],
        )
        assert [label for label, _callback in bubble._notice_actions] == ["Approve", "Decline"]
        assert len(bubble._action_rects) == 2
        QTest.mouseClick(bubble, Qt.MouseButton.LeftButton, pos=bubble._action_rects[0].center())
        qapp.processEvents()
        assert actions == ["approve"]
        assert bubble.isVisible()
        assert bubble._thinking is True
        assert bubble._notice_actions == []

        bubble.show_notice("Browser permission denied.", timeout_ms=20)
        assert "Browser permission denied." in bubble._full_text
        assert bubble._notice_actions == []
        _pump_until(qapp, lambda: not bubble.isVisible(), timeout=1.0)
    finally:
        window.deleteLater()
        bubble.deleteLater()
