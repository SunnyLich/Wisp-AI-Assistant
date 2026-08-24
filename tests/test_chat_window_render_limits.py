"""Tests for test chat window render limits."""

import base64
import os
import sys
import time

import pytest

from core import addon_store

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
except ImportError as exc:
    PYSIDE6_AVAILABLE = False
    pytest.skip(f"PySide6 Qt libraries unavailable: {exc}", allow_module_level=True)
else:
    PYSIDE6_AVAILABLE = True

from ui.chat_rendering import _assistant_text_to_html
from ui.chat_window import (
    _CHAT_RENDER_CHAR_LIMIT,
    _SIDEBAR_GENERAL_GROUP_GAP,
    ChatWindow,
    _chat_model_messages,
    _context_not_anchored_to_messages,
    _file_context_text,
    _format_conversation_datetime,
    _latest_tool_context_from_messages,
    _merge_file_context_from_messages,
    _merged_annotations,
    _message_timestamp_text,
    _MessageTextView,
    _truncate_for_display,
    _truncate_segments_for_display,
)
from ui.text_annotations import annotation_tooltip_anchor, normalize_range_annotations


def test_truncate_for_display_caps_large_text():
    text = "x" * (_CHAT_RENDER_CHAR_LIMIT + 50)

    result = _truncate_for_display(text, _CHAT_RENDER_CHAR_LIMIT, "chat display")

    assert len(result) < len(text)
    assert "chat display truncated" in result
    assert "50 chars hidden" in result


def test_truncate_segments_preserves_visible_prefix_and_adds_marker():
    segments = [
        ("thought:" + "a" * 20, True),
        ("reply:" + "b" * 20, False),
    ]

    result = _truncate_segments_for_display(segments, limit=24)

    assert result[0] == ("thought:" + "a" * 16, True)
    assert result[1][1] is False
    assert "chat display truncated" in result[1][0]


def test_conversation_rename_failure_matrix_rejects_or_rolls_back(monkeypatch):
    """Rename validation and persistence faults never leave a false in-memory title."""
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv)
    window = ChatWindow.__new__(ChatWindow)
    original = {
        "title_override": "Original",
        "messages": [{"role": "user", "content": "first"}],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    other = {
        "title_override": "Existing",
        "messages": [{"role": "user", "content": "second"}],
    }
    window._conversations = [original, other]
    warnings = []
    rebuilds = []
    monkeypatch.setattr(ChatWindow, "_rebuild_sidebar", lambda self: rebuilds.append(True))
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args: warnings.append(str(_args[-1])) or QMessageBox.StandardButton.Ok,
    )

    for proposed, expected in (
        ("   ", "empty"),
        ("bad\nname", "invalid"),
        ("existing", "already uses"),
    ):
        monkeypatch.setattr(QInputDialog, "getText", lambda *_args, proposed=proposed, **_kwargs: (proposed, True))
        window._persist_fn = lambda: pytest.fail("invalid name reached persistence")
        ChatWindow._rename_conversation(window, 0)
        assert original["title_override"] == "Original"
        assert expected in warnings[-1].lower()

    storage_failures = (
        PermissionError("backing store is read-only"),
        BlockingIOError("backing store is locked"),
        ValueError("backing store is corrupt"),
        OSError("write was interrupted"),
    )
    for failure in storage_failures:
        monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Renamed", True))

        def fail_persist(failure=failure):
            raise failure

        window._persist_fn = fail_persist
        ChatWindow._rename_conversation(window, 0)
        assert original["title_override"] == "Original"
        assert str(failure) in warnings[-1]

    assert len(rebuilds) == len(storage_failures) * 2
    app.processEvents()


def test_conversation_delete_failure_matrix_preserves_history(monkeypatch):
    """Missing, locked, cancelled, and storage-failed deletes preserve history."""
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv)
    window = ChatWindow.__new__(ChatWindow)
    conversation = {"id": "keep-me", "messages": []}
    window._conversations = [conversation]
    window._active_idx = 0
    window._streaming = False
    warnings = []
    questions = {"answer": QMessageBox.StandardButton.Yes}
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: questions["answer"],
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args: warnings.append(str(_args[-1])) or QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        ChatWindow,
        "_rebuild_stack",
        lambda self: pytest.fail("failed delete rebuilt the conversation stack"),
    )
    monkeypatch.setattr(
        ChatWindow,
        "_rebuild_sidebar",
        lambda self: pytest.fail("failed delete rebuilt the conversation sidebar"),
    )

    window._persist_fn = lambda: pytest.fail("missing target reached persistence")
    ChatWindow._delete_conversation(window, 4)
    assert window._conversations == [conversation]

    window._streaming = True
    window._persist_fn = lambda: pytest.fail("locked target reached persistence")
    ChatWindow._delete_conversation(window, 0)
    assert window._conversations == [conversation]
    window._streaming = False

    questions["answer"] = QMessageBox.StandardButton.No
    window._persist_fn = lambda: pytest.fail("cancelled delete reached persistence")
    ChatWindow._delete_conversation(window, 0)
    assert window._conversations == [conversation]
    questions["answer"] = QMessageBox.StandardButton.Yes

    storage_failures = (
        PermissionError("required elevation is denied"),
        PermissionError("storage access is denied"),
        BlockingIOError("another process is using the files"),
        OSError("cleanup only partly completes"),
    )
    for failure in storage_failures:
        def fail_persist(failure=failure):
            raise failure

        window._persist_fn = fail_persist
        ChatWindow._delete_conversation(window, 0)
        assert window._conversations == [conversation]
        assert window._active_idx == 0
        assert str(failure) in warnings[-1]

    app.processEvents()


def test_delete_all_conversations_confirms_clears_and_persists(monkeypatch):
    """Bulk deletion is explicit, clears the live list, and refreshes the empty UI."""
    from PySide6.QtWidgets import QMessageBox

    window = ChatWindow.__new__(ChatWindow)
    window._conversations = [
        {"id": "one", "messages": []},
        {"id": "two", "messages": []},
    ]
    window._active_idx = 1
    window._streaming = False
    persisted_snapshots = []
    window._persist_fn = lambda: persisted_snapshots.append(list(window._conversations))
    rebuilt = []
    window._rebuild_stack = lambda: rebuilt.append("stack")
    window._rebuild_sidebar = lambda: rebuilt.append("sidebar")

    class InputFrame:
        enabled = True

        def setEnabled(self, enabled):
            self.enabled = enabled

    window._input_frame = InputFrame()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    ChatWindow._delete_all_conversations(window)

    assert window._conversations == []
    assert window._active_idx == 0
    assert persisted_snapshots == [[]]
    assert rebuilt == ["stack", "sidebar"]
    assert window._input_frame.enabled is False


def test_delete_all_conversations_cancel_or_save_failure_preserves_history(monkeypatch):
    """Cancelling or failing to save never loses bulk-deleted history."""
    from PySide6.QtWidgets import QMessageBox

    window = ChatWindow.__new__(ChatWindow)
    conversations = [{"id": "one", "messages": []}, {"id": "two", "messages": []}]
    window._conversations = list(conversations)
    window._active_idx = 1
    window._streaming = False
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    window._persist_fn = lambda: pytest.fail("cancelled bulk delete reached persistence")

    ChatWindow._delete_all_conversations(window)
    assert window._conversations == conversations

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    def fail_persist():
        raise PermissionError("history file is locked")

    window._persist_fn = fail_persist
    window._rebuild_stack = lambda: pytest.fail("failed bulk delete rebuilt stack")
    window._rebuild_sidebar = lambda: pytest.fail("failed bulk delete rebuilt sidebar")
    ChatWindow._delete_all_conversations(window)

    assert window._conversations == conversations
    assert window._active_idx == 1
    assert "history file is locked" in warnings[-1]


def test_merged_annotations_hides_disabled_sources_and_rebuilds_ui_lab(monkeypatch):
    """Disabled add-ons disappear and stale UI Lab ranges are never reused."""
    monkeypatch.setattr(
        addon_store,
        "is_enabled",
        lambda addon_id, default=True: addon_id == "active-addon",
    )
    monkeypatch.setattr(
        "ui.chat_window._ui_lab_label_annotations",
        lambda _text, _role: [{"start": 4, "end": 7, "source": "addon:ui-lab", "id": "fresh"}],
    )
    stored = [
        {"start": 99, "end": 102, "source": "addon:ui-lab", "id": "stale"},
        {"start": 0, "end": 3, "source": "addon:disabled-addon"},
        {"start": 0, "end": 3, "source": "addon:active-addon"},
        {"start": 0, "end": 3, "source": "builtin:test"},
    ]

    merged = _merged_annotations(stored, "say for", "assistant")

    assert {item.get("id") for item in merged if isinstance(item, dict)} == {None, "fresh"}
    assert {item.get("source") for item in merged if isinstance(item, dict)} == {
        "addon:active-addon",
        "addon:ui-lab",
        "builtin:test",
    }


def test_ui_lab_annotations_are_empty_when_addon_is_disabled(monkeypatch):
    """The chat renderer must honor the manager's top-level enabled state."""
    monkeypatch.setattr(addon_store, "is_enabled", lambda _addon_id, default=True: False)
    from ui.chat_window import _ui_lab_label_annotations

    assert _ui_lab_label_annotations("for", "assistant") == []


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_tooltip_anchor_stays_on_annotated_word_after_markdown_rendering():
    """Rendered Markdown structure must not shift an annotation's hover target."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    text = "1. **Make onboarding task-based.**\nInstead of primarily choosing the best for code."
    start = text.index("for code")
    raw = [
        {
            "start": start,
            "end": start + 3,
            "style": "text-decoration:underline",
            "tooltip": "This is from the addon",
            "source": "addon:test",
            "id": "label-for",
        }
    ]
    annotation = normalize_range_annotations(raw, text)[0]
    anchor = annotation_tooltip_anchor(annotation)
    view = _MessageTextView("#222222")
    try:
        view.setHtml(_assistant_text_to_html(text, annotations=raw))
        view.set_annotation_tooltips(text, raw)
        anchored_fragments: list[tuple[str, str]] = []
        block = view.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().anchorHref():
                    anchored_fragments.append((fragment.text(), fragment.charFormat().anchorHref()))
                iterator += 1
            block = block.next()

        assert anchored_fragments == [("for", anchor)]
        assert view._tooltip_for_anchor(anchor) == "This is from the addon"
        assert view._tooltip_for_anchor("") == ""
    finally:
        view.close()
        app.processEvents()


def test_conversation_datetime_formats_for_history_display():
    """Verify conversation timestamps are display metadata."""
    assert _format_conversation_datetime("2026-06-19T15:52:16+00:00")
    assert _format_conversation_datetime("2026-06-19T15:52:16")


def test_chat_model_messages_excludes_timestamp_metadata():
    """Verify model payload excludes metadata but carries user attachments."""
    messages = [
        {
            "role": "user",
            "content": "hi",
            "context": "[Attached · notes.txt]\nline one\nline two",
            "id": "m1",
            "created_at": "2026-06-19T15:52:16+00:00",
        },
        {
            "role": "assistant",
            "content": "hello",
            "id": "m2",
            "updated_at": "2026-06-19T15:52:17+00:00",
            "annotations": [{"start": 0, "end": 5, "tooltip": "display only"}],
            "file_context": [{"tool": "read_file", "path": "a.py"}],
        },
    ]

    result = _chat_model_messages(messages)

    assert result[0]["role"] == "user"
    assert result[0]["content"].startswith("hi\n\n[Attached context for this message]")
    assert "line one\nline two" in result[0]["content"]
    assert "created_at" not in result[0]
    assert result[1] == {"role": "assistant", "content": "hello"}


def test_conversation_context_skips_message_anchored_blocks():
    """Verify system context does not duplicate message-scoped attachments."""
    messages = [{"role": "user", "content": "use it", "context": "[Attached]\nattached text"}]
    context = "[Attached]\nattached text\n\n---\nAmbient context"

    assert _context_not_anchored_to_messages(context, messages) == "Ambient context"


def test_message_timestamp_formats_from_metadata():
    """Verify message timestamps are display metadata."""
    assert _message_timestamp_text(
        {"role": "user", "content": "hi", "created_at": "2026-06-19T15:52:16+00:00"}
    )


def test_file_context_text_mentions_exact_prior_path():
    """Verify file metadata can resolve later 'that file' references."""
    text = _file_context_text([
        {
            "tool": "create_file",
            "path": r"C:\repo\model_files\hello_world.py",
            "relative_path": "hello_world.py",
            "ok": True,
            "message": "Created hello_world.py.",
        }
    ])

    assert r"C:\repo\model_files\hello_world.py" in text
    assert "that file" in text


def test_hidden_context_rebuilds_from_retained_messages():
    """Verify branch/rewind metadata can be rebuilt from message-scoped metadata."""
    file_context = [
        {
            "tool": "create_file",
            "path": r"C:\repo\model_files\hello_world.py",
            "relative_path": "hello_world.py",
            "root": "",
            "ok": True,
            "message": "",
        }
    ]
    tool_context = {
        "allowed_tools": ["read_file"],
        "pinned_tools": ["read_file"],
        "file_access_mode": "ask",
    }
    messages = [
        {"role": "user", "content": "create"},
        {"role": "assistant", "content": "done", "file_context": file_context, "tool_context": tool_context},
    ]

    assert _merge_file_context_from_messages(messages) == file_context
    assert _latest_tool_context_from_messages(messages) == tool_context


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_window_is_not_always_on_top():
    """Verify chat window behaves like a normal app window."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    window = ChatWindow([{"messages": [{"role": "user", "content": "hello"}]}], lambda _messages: iter(()))
    try:
        assert not (window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    finally:
        window.close()
    app.processEvents()


def test_chat_header_inspector_shows_only_subagent_work() -> None:
    """The top-right panel is agent-only; skills live in the composer menu."""
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

    app = QApplication.instance() or QApplication(sys.argv)
    window = ChatWindow(
        [{"messages": [{"role": "user", "content": "hello"}]}],
        lambda _messages: iter(()),
    )
    try:
        activity_button = window.findChild(QPushButton, "harnessActivityButton")
        assert activity_button is not None
        window._on_chunk({
            "harness_activity": {
                "type": "capabilities",
                "skills": [{"name": "openai-docs", "description": "Official documentation", "scope": "system"}],
                "mcp_servers": [{"name": "github", "tools": {"get_file": {}}, "authStatus": "authenticated"}],
            }
        })
        window._on_chunk({
            "harness_activity": {
                "type": "subagent",
                "phase": "started",
                "item_id": "spawn-1",
                "agent_id": "child-thread-12345678",
                "status": "running",
                "prompt": "Inspect the test suite and report gaps",
                "activity_type": "collabAgentToolCall",
            }
        })

        assert activity_button.text() == "Agents 1"
        window._toggle_harness_inspector(activity_button)
        app.processEvents()
        inspector = window.findChild(QDialog, "harnessActivityInspector")
        assert inspector is not None and inspector.isVisible()
        agent_row = inspector.findChild(QPushButton, "harnessAgentRow")
        assert agent_row is not None
        assert "Inspect the test suite and report gaps" in agent_row.toolTip()
        assert inspector.findChild(QPushButton, "harnessSkillsRow") is None
        assert inspector.findChild(QPushButton, "harnessMcpRow") is None
        assert all(label.text() != "Codex capabilities" for label in inspector.findChildren(QLabel))
        inspector.close()
        window._open_composer_menu(window._composer_menu_btn)
        app.processEvents()
        assert "Skills…" in [action.text() for action in window._composer_menu.actions()]
        window._insert_skill_reference("openai-docs")
        assert window._input.toPlainText() == "$openai-docs "
    finally:
        if window._harness_inspector is not None:
            window._harness_inspector.close()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_empty_chat_hides_chat_scoped_buttons_but_keeps_import_and_sync() -> None:
    """Global history sources remain usable while inactive chat actions disappear."""
    from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton

    app = QApplication.instance() or QApplication(sys.argv)
    window = ChatWindow([], lambda _messages: iter(()))
    try:
        window.show()
        app.processEvents()
        for name in ("externalImportCodex", "externalImportClaude"):
            button = window.findChild(QPushButton, name)
            assert button is not None
            assert button.isHidden() is False
            assert button.isEnabled() is True
        for name in ("externalAutoSyncCodex", "externalAutoSyncClaude"):
            checkbox = window.findChild(QCheckBox, name)
            assert checkbox is not None
            assert checkbox.isHidden() is False
            assert checkbox.isEnabled() is True
        assert window.findChild(QPushButton, "harnessActivityButton").isHidden() is True
        assert window.findChild(QPushButton, "conversationOptionsButton").isHidden() is True
        assert window._input_frame.isHidden() is True
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_formatted_sidebar_uses_window_title_and_collapsible_sources() -> None:
    """The sidebar has no duplicate brand row and Sources acts as a disclosure."""
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    window = ChatWindow([], lambda _messages: iter(()))
    try:
        window.show()
        app.processEvents()
        assert window.windowTitle() == "OpenWand Chat"
        assert all(label.text() != "●  OpenWand" for label in window.findChildren(QLabel))

        toggle = window.findChild(QPushButton, "formattedSourcesToggle")
        container = window.findChild(QWidget, "formattedSourcesContainer")
        assert toggle is not None
        assert container is not None
        assert toggle.text() == "▾  Sources"
        assert container.isHidden() is False

        toggle.click()
        app.processEvents()
        assert toggle.text() == "▸  Sources"
        assert container.isHidden() is True
        assert window.findChild(QPushButton, "externalImportCodex").isVisible() is False

        toggle.click()
        app.processEvents()
        assert toggle.text() == "▾  Sources"
        assert container.isHidden() is False
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_deselecting_active_chat_hides_only_chat_scoped_controls() -> None:
    """No highlighted row means no composer/agent/options controls."""
    from PySide6.QtWidgets import QApplication, QPushButton

    app = QApplication.instance() or QApplication(sys.argv)
    window = ChatWindow(
        [{"messages": [{"role": "user", "content": "hello"}]}],
        lambda _messages: iter(()),
    )
    try:
        window.show()
        app.processEvents()
        assert window._input_frame.isHidden() is False
        window._selected_conversation_indices.clear()
        window._refresh_chat_scoped_controls()
        app.processEvents()
        assert window._input_frame.isHidden() is True
        assert window.findChild(QPushButton, "harnessActivityButton").isHidden() is True
        assert window.findChild(QPushButton, "conversationOptionsButton").isHidden() is True
        assert window.findChild(QPushButton, "externalImportCodex").isHidden() is False
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_conversation_scrollbar_has_a_mouse_friendly_drag_target():
    """The main transcript thumb must not collapse into a few-pixel target."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QScrollArea, QTextBrowser

    from ui.chat_window import (
        _CHAT_SCROLLBAR_HANDLE_MIN_HEIGHT,
        _CHAT_SCROLLBAR_WIDTH,
        ChatWindow,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    long_reply = "\n\n".join(f"Paragraph {index}" for index in range(120))
    window = ChatWindow(
        [{"messages": [{"role": "assistant", "content": long_reply}]}],
        lambda _messages: iter(()),
    )
    try:
        window.show()
        app.processEvents()
        pages = [
            area
            for area in window.findChildren(QScrollArea)
            if area.widget() is not None and area.widget().findChildren(QTextBrowser)
        ]
        assert len(pages) == 1
        scrollbar = pages[0].verticalScrollBar()
        assert scrollbar.width() >= _CHAT_SCROLLBAR_WIDTH
        assert f"min-height: {_CHAT_SCROLLBAR_HANDLE_MIN_HEIGHT}px" in pages[0].styleSheet()
        assert "QScrollBar::handle:vertical:hover" in pages[0].styleSheet()
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_wheel_over_reply_text_scrolls_the_outer_conversation():
    """Nested read-only reply views must not swallow ordinary wheel input."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    from ui.chat_window import ChatWindow, _MessageTextView

    app = QApplication.instance() or QApplication(sys.argv)
    long_reply = "\n\n".join(f"Paragraph {index}" for index in range(160))
    window = ChatWindow(
        [{"messages": [{"role": "assistant", "content": long_reply}]}],
        lambda _messages: iter(()),
    )
    try:
        window.show()
        app.processEvents()
        page = window._active_scroll()
        assert page is not None
        bar = page.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.maximum() // 2)
        before = bar.value()
        view = window.findChild(_MessageTextView)
        assert view is not None
        wheel = QWheelEvent(
            QPointF(10, 10),
            QPointF(view.viewport().mapToGlobal(QPoint(10, 10))),
            QPoint(),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(view.viewport(), wheel)
        assert wheel.isAccepted()
        assert bar.value() > before
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_middle_mouse_press_move_scrolls_and_release_stops():
    """A held middle press scrolls by pointer distance and stops on release."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from ui.chat_window import ChatWindow, _MessageTextView

    app = QApplication.instance() or QApplication(sys.argv)
    long_reply = "\n\n".join(f"Paragraph {index}" for index in range(160))
    window = ChatWindow(
        [{"messages": [{"role": "assistant", "content": long_reply}]}],
        lambda _messages: iter(()),
    )
    try:
        window.show()
        app.processEvents()
        page = window._active_scroll()
        assert page is not None
        bar = page.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.maximum() // 2)
        before = bar.value()
        view = window.findChild(_MessageTextView)
        assert view is not None
        target = view.viewport()
        local_start = QPointF(20, 100)
        global_start = QPointF(target.mapToGlobal(local_start.toPoint()))
        local_end = QPointF(20, 40)
        global_end = QPointF(target.mapToGlobal(local_end.toPoint()))
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            local_start,
            global_start,
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            local_end,
            global_end,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            local_end,
            global_end,
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(target, press)
        QApplication.sendEvent(target, move)
        window._tick_middle_autoscroll()
        assert bar.value() < before
        assert window._middle_autoscroll is not None
        QApplication.sendEvent(target, release)
        assert press.isAccepted() and move.isAccepted() and release.isAccepted()
        assert window._middle_autoscroll is None
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_middle_mouse_stationary_release_latches_until_next_click():
    """A click-release without movement keeps browser-style autoscroll active."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from ui.chat_window import ChatWindow, _MessageTextView

    app = QApplication.instance() or QApplication(sys.argv)
    long_reply = "\n\n".join(f"Paragraph {index}" for index in range(160))
    window = ChatWindow(
        [{"messages": [{"role": "assistant", "content": long_reply}]}],
        lambda _messages: iter(()),
    )
    try:
        window.show()
        app.processEvents()
        page = window._active_scroll()
        assert page is not None
        bar = page.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        before = bar.value()
        view = window.findChild(_MessageTextView)
        assert view is not None
        target = view.viewport()
        local_anchor = QPointF(20, 100)
        global_anchor = QPointF(target.mapToGlobal(local_anchor.toPoint()))

        def mouse_event(event_type, local, global_pos, button, buttons):
            return QMouseEvent(
                event_type,
                local,
                global_pos,
                button,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            )

        press = mouse_event(
            QEvent.Type.MouseButtonPress,
            local_anchor,
            global_anchor,
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.MiddleButton,
        )
        release = mouse_event(
            QEvent.Type.MouseButtonRelease,
            local_anchor,
            global_anchor,
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.NoButton,
        )
        QApplication.sendEvent(target, press)
        QApplication.sendEvent(target, release)
        assert window._middle_autoscroll is not None

        local_moved = QPointF(20, 40)
        global_moved = QPointF(target.mapToGlobal(local_moved.toPoint()))
        move = mouse_event(
            QEvent.Type.MouseMove,
            local_moved,
            global_moved,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
        )
        QApplication.sendEvent(target, move)
        window._tick_middle_autoscroll()
        assert bar.value() < before
        stop = mouse_event(
            QEvent.Type.MouseButtonPress,
            local_moved,
            global_moved,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
        )
        QApplication.sendEvent(target, stop)
        assert stop.isAccepted()
        assert window._middle_autoscroll is None
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_rich_chat_messages_use_the_same_ui_font_family_as_chat_controls():
    """Large Markdown headings must not fall back to QTextDocument's serif font."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.chat_window import _MessageTextView, _ui_font

    app = QApplication.instance() or QApplication(sys.argv)
    view = _MessageTextView("#000000", presentation="assistant")
    try:
        view.setHtml("<h1>Large heading</h1><p>Body text</p>")

        assert view.font().family() == _ui_font(11).family()
        assert view.document().defaultFont().family() == _ui_font(11).family()
        assert view.document().defaultFont().family() == app.font().family()
    finally:
        view.deleteLater()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_application_event_filter_ignores_non_qobject_model_items():
    """Global chat filtering must tolerate Qt model-item action events."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QStandardItem
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    window = ChatWindow([{"messages": []}], lambda _messages: iter(()))
    try:
        event = QEvent(QEvent.Type.ActionChanged)
        assert window.eventFilter(QStandardItem("provider"), event) is False
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_window_opens_large_last_chat_without_render_freeze():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "messages": [
                {"role": "user", "content": "what can it see?"},
                {"role": "assistant", "content": "tools help\n" + ("x" * 180_000)},
            ],
            "context": "ambient context\n" + ("y" * 180_000),
        }
    ]

    started = time.perf_counter()
    window = ChatWindow(conversations, lambda _messages: iter(()))
    elapsed = time.perf_counter() - started
    try:
        assert elapsed < 1.0
        assert window._built_pages == {0}
        page = window._stack.widget(0)
        assert getattr(page, "_msg_layout", None) is not None
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_sidebar_options_button_stays_visible_for_long_titles():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "messages": [
                {
                    "role": "user",
                    "content": "this is a very long conversation title " * 12,
                }
            ],
        }
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        row, title_btn = window._make_sidebar_row(0, conversations[0])
        buttons = row.findChildren(QPushButton)
        menu_btn = next(button for button in buttons if button is not title_btn)

        assert title_btn.minimumWidth() == 0
        assert "this is a very long conversation title" in title_btn.toolTip()
        assert menu_btn.width() == 32
        assert menu_btn.text() == "⋮"
        assert menu_btn.isHidden() is False
    finally:
        row.deleteLater()
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_sidebar_shows_conversation_timestamp():
    """Verify history rows include conversation date/time metadata."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "messages": [{"role": "user", "content": "hello"}],
            "updated_at": "2026-06-19T15:52:16+00:00",
        }
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        row, title_btn = window._make_sidebar_row(0, conversations[0])

        assert title_btn._subtitle
        assert title_btn._subtitle in title_btn.toolTip()
    finally:
        row.deleteLater()
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_window_open_does_not_retarget_hotkey_continuation_until_send():
    """Verify opening history visually does not make hotkeys continue it."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    selected: list[int] = []
    conversations = [
        {"messages": [{"role": "user", "content": "old"}], "project_id": "general"},
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()), on_select=selected.append)
    try:
        assert selected == []

        window._send("continue from chat composer")

        assert selected == [0]
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_sidebar_separates_project_chats_from_unheaded_general_history(monkeypatch):
    """Verify General is translated and visually separated without a heading."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    import ui.chat_window as chat_window_module

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(chat_window_module, "t", lambda text: f"T[{text}]")
    conversations = [
        {"messages": [{"role": "user", "content": "General chat 1"}], "project_id": "general"},
        {"messages": [{"role": "user", "content": "Project chat 1"}], "project_id": "project-1"},
        {"messages": [{"role": "user", "content": "General chat 2"}], "project_id": "general"},
    ]
    window = ChatWindow(
        conversations,
        lambda _messages: iter(()),
        projects=[
            {"id": "general", "name": "General"},
            {"id": "project-1", "name": "Project 1"},
        ],
    )
    try:
        groups = window._grouped_sidebar_indices()

        assert groups[0] == ("project-1", "Project 1", [1])
        assert groups[1][0:2] == ("general", "T[General]")
        assert groups[1][2] == [2, 0]
        assert window._project_combo.itemText(0) == "T[General]"

        headers = [
            label.text().strip()
            for label in window._sidebar_items.findChildren(QLabel)
            if label.text().strip() in {"Project 1", "T[General]"}
        ]
        assert headers == ["Project 1"]
        spacers = [
            item.spacerItem().sizeHint().height()
            for i in range(window._sidebar_layout.count())
            if (item := window._sidebar_layout.itemAt(i)).spacerItem() is not None
        ]
        assert _SIDEBAR_GENERAL_GROUP_GAP in spacers
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_bubble_header_shows_message_timestamp():
    """Verify each chat turn displays its own date/time metadata."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "messages": [
                {
                    "role": "user",
                    "content": "hello",
                    "created_at": "2026-06-19T15:52:16+00:00",
                }
            ],
        }
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        labels = [label.text() for label in window.findChildren(QLabel)]

        assert any("2026" in text for text in labels)
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_context_policy_controls_are_compact_menu_chips(monkeypatch):
    """Verify chat context controls render as compact chips with popup choices."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QMenu, QPushButton

    import config

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = ""
    conversations = [
        {
            "messages": [{"role": "user", "content": "hello"}],
            "context_policy": {
                "context_ambient": False,
                "context_documents_mode": "off",
                "context_browser_mode": "off",
                "context_github_mode": "off",
                "context_memory_mode": "off",
                "context_screenshot": "off",
                "context_clipboard": False,
                "file_access": "off",
                "tools": {},
            },
        }
    ]
    preview_requests = []
    window = ChatWindow(conversations, lambda _messages: iter(()), on_context_preview=preview_requests.append)
    captured = []

    def fake_popup(self, pos):
        """Capture the menu that would be opened for a context chip."""
        captured.append((self, pos, [action.data() for action in self.actions()]))
        return None

    monkeypatch.setattr(QMenu, "popup", fake_popup)
    try:
        assert set(window._context_controls) == {
            "ambient",
            "browser",
            "selection",
            "clipboard",
            "screenshot",
            "github",
            "memory",
            "files",
        }
        assert all(isinstance(control, QPushButton) for control in window._context_controls.values())
        assert not any(isinstance(control, QComboBox) for control in window._context_controls.values())

        browser_chip = window._context_controls["browser"]
        assert browser_chip.objectName() == "chatContextChip_browser"
        assert browser_chip.text().count("\n") == 2
        assert browser_chip.property("context_tokens") == "? tok"
        assert window._context_controls["selection"].property("context_state") == "off"

        window._show_context_policy_menu("browser")

        assert captured
        assert {"off", "on", "auto"} <= set(captured[0][2])

        window._set_context_policy_state("browser", "on")

        assert conversations[0]["context_policy"]["context_browser_mode"] == "auto"
        assert browser_chip.property("context_state") == "on"
        assert browser_chip.property("context_tokens") == "? tok"
        assert preview_requests
        window.update_context_preview(
            preview_requests[-1]["preview_id"],
            [{"id": "browser", "tokens": "~12 tok", "warning": ""}],
        )
        assert browser_chip.property("context_tokens") == "~12 tok"
        assert "Token estimate" in browser_chip.toolTip()

        window._set_context_policy_state("browser", "auto")

        assert "\nauto\n" in browser_chip.text()
        assert "Let model decide" not in browser_chip.text()
    finally:
        config.APP_LANGUAGE = old_language
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_context_policy_normalizes_legacy_on_modes():
    """Verify persisted on modes stay enabled in chat context chips."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = ""
    conversations = [
        {
            "messages": [{"role": "user", "content": "hello"}],
            "context_policy": {
                "context_ambient": True,
                "context_documents_mode": "on",
                "context_browser_mode": "on",
                "context_github_mode": "off",
                "context_memory_mode": "on",
                "context_screenshot": "on",
                "context_clipboard": False,
                "file_access": "off",
                "tools": {},
            },
        }
    ]
    preview_requests = []
    window = ChatWindow(conversations, lambda _messages: iter(()), on_context_preview=preview_requests.append)

    try:
        assert window._context_controls["ambient"].property("context_state") == "on"
        assert window._context_controls["browser"].property("context_state") == "on"
        assert window._context_controls["memory"].property("context_state") == "on"
        assert window._context_controls["screenshot"].property("context_state") == "on"
        assert window._context_controls["browser"].property("context_tokens") == "? tok"

        window.request_context_preview()

        assert preview_requests
        assert preview_requests[-1]["context_policy"]["context_documents_mode"] == "auto"
        assert preview_requests[-1]["context_policy"]["context_browser_mode"] == "auto"
        assert preview_requests[-1]["context_policy"]["context_screenshot"] == "auto"
    finally:
        config.APP_LANGUAGE = old_language
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_context_preview_updates_off_chips():
    """Verify chat shows context estimates even before a source is enabled."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = ""
    conversations = [
        {
            "messages": [{"role": "user", "content": "hello"}],
            "context_policy": {
                "context_ambient": False,
                "context_documents_mode": "off",
                "context_browser_mode": "off",
                "context_github_mode": "off",
                "context_memory_mode": "off",
                "context_screenshot": "off",
                "context_clipboard": False,
                "file_access": "off",
                "tools": {},
            },
        }
    ]
    preview_requests = []
    window = ChatWindow(conversations, lambda _messages: iter(()), on_context_preview=preview_requests.append)

    try:
        window.show()
        app.processEvents()
        screenshot_chip = window._context_controls["screenshot"]
        selection_chip = window._context_controls["selection"]
        assert screenshot_chip.property("context_state") == "off"
        assert screenshot_chip.property("context_tokens") == "? tok"
        assert preview_requests

        window.update_context_preview(
            preview_requests[-1]["preview_id"],
            [
                {"id": "browser", "tokens": "~12 tok", "warning": ""},
                {"id": "screenshot", "tokens": "~1.1k tok", "warning": ""},
                {"id": "selection", "tokens": "~9 tok", "warning": ""},
            ],
        )

        browser_chip = window._context_controls["browser"]
        assert browser_chip.property("context_state") == "off"
        assert browser_chip.property("context_tokens") == "~12 tok"
        window._set_context_policy_state("browser", "on")
        assert browser_chip.property("context_state") == "on"
        assert browser_chip.property("context_tokens") == "~12 tok"
        assert screenshot_chip.property("context_state") == "off"
        assert screenshot_chip.property("context_tokens") == "~1.1k tok"
        assert selection_chip.property("context_state") == "off"
        assert selection_chip.property("context_tokens") == "~9 tok"
    finally:
        config.APP_LANGUAGE = old_language
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_window_reports_initial_active_conversation():
    """Verify opening chat retargets follow-up prompts to the shown conversation."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    selected = []
    conversations = [
        {"messages": [{"role": "user", "content": "old"}]},
        {"messages": [{"role": "user", "content": "new"}]},
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()), active_idx=0, on_select=selected.append)
    try:
        assert selected == [0]
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_message_menu_can_copy_selected_text(monkeypatch):
    """Right-click message menu should expose selected-text actions when text is selected."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMenu

    import config
    from ui.chat_window import ChatWindow, _MessageTextView

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = ""
    conversations = [{"messages": [{"role": "assistant", "content": "hello world"}]}]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    captured = []

    def fake_popup(self, pos):
        """Capture the menu that would be shown."""
        captured.append((self, pos, list(self.actions())))
        return None

    monkeypatch.setattr(QMenu, "popup", fake_popup)
    monkeypatch.setattr(ChatWindow, "_ui_lab_context_actions", lambda *_args: [])
    try:
        view = window.findChild(_MessageTextView)
        assert view is not None
        cursor = view.document().find("hello")
        assert not cursor.isNull()
        view.setTextCursor(cursor)

        window._open_message_menu(0, 0, view.parentWidget())

        assert captured
        actions = [action for action in captured[0][2] if not action.isSeparator()]
        copy_action = next(action for action in actions if action.text() == "Copy selected text")
        copy_action.trigger()

        assert QApplication.clipboard().text() == "hello"
        assert [action.text() for action in actions][1:] == ["Branch from here", "Rewind current chat to here"]
    finally:
        config.APP_LANGUAGE = old_language
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_window_selection_notice_names_continued_chat():
    """Verify switching chats shows which conversation will continue."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {"messages": [{"role": "user", "content": "old topic"}]},
        {"messages": [{"role": "user", "content": "new topic"}]},
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()), active_idx=0)
    try:
        window._switch(1)

        assert window._past_notice.isHidden() is False
        assert "new topic" in window._past_notice.text()
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_sidebar_options_menu_anchors_to_button(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMenu, QPushButton

    from ui.i18n import t

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [{"messages": [{"role": "user", "content": "hello"}]}]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    captured = []

    def fake_popup(self, pos):
        captured.append(pos)
        return None

    monkeypatch.setattr(QMenu, "popup", fake_popup)
    try:
        row, title_btn = window._make_sidebar_row(0, conversations[0])
        menu_btn = next(button for button in row.findChildren(QPushButton) if button is not title_btn)

        window._open_conversation_menu(0, menu_btn)

        assert captured == [menu_btn.mapToGlobal(menu_btn.rect().bottomLeft())]
        assert t("Browse conversation files") in {
            action.text() for action in window._conversation_menu.actions()
        }
    finally:
        row.deleteLater()
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_browse_conversation_files_persists_then_reveals_record(
    monkeypatch, tmp_path
):
    """The conversation menu reveals the current persisted chat record."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui import chat_window as chat_window_mod

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [{"messages": [{"role": "user", "content": "hello"}]}]
    persisted = []
    revealed = []
    record = tmp_path / "chats" / "conversations.json"
    record.parent.mkdir()
    record.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(chat_window_mod._conversation_store, "CONVERSATIONS_FILE", record)
    monkeypatch.setattr(
        chat_window_mod._file_browser,
        "reveal_path",
        lambda path: revealed.append(path),
    )
    window = ChatWindow(
        conversations,
        lambda _messages: iter(()),
        persist_fn=lambda: persisted.append(True),
    )
    try:
        window._open_conversation_menu(0)
        browse_action = next(
            action
            for action in window._conversation_menu.actions()
            if action.text() == chat_window_mod.t("Browse conversation files")
        )
        browse_action.trigger()
        app.processEvents()

        assert persisted == [True]
        assert revealed == [record]
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_final_text_replaces_partial_stream_before_persist():
    """Verify final chat text replaces an incomplete streamed draft."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [{"messages": [{"role": "user", "content": "hi"}]}]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        window._current_ai_text = "first part"
        window._current_ai_reply_text = "first part"
        window._current_ai_segments = [("first part", False)]
        window._current_ai_parser = None

        window._on_final_text("first part plus second part")
        window._on_finished()

        saved = conversations[0]["messages"][-1]
        assert saved["role"] == "assistant"
        assert saved["content"] == "first part plus second part"
        assert saved["created_at"]
        assert saved["id"]
        assert conversations[0]["updated_at"]
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_window_streams_and_persists_remote_agent_activity_in_order():
    """ChatGPT/Claude progress should render live and remain in OpenWand history."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [{"messages": [{"role": "user", "content": "inspect"}]}]
    window = ChatWindow(conversations, lambda _messages: iter(()))

    class _Label:
        def __init__(self) -> None:
            self.html = ""

        def setHtml(self, value: str) -> None:  # noqa: N802 - Qt-compatible test double
            self.html = value

    label = _Label()
    window._current_ai_label = label  # type: ignore[assignment]
    try:
        window._on_chunk({"text": "Starting ChatGPT...", "is_progress": True})
        assert "Starting ChatGPT" in label.html

        window._on_chunk({"text": "Model is thinking...", "is_progress": True})
        assert "Model is thinking" in label.html
        assert "Starting ChatGPT" not in label.html

        window._on_chunk({"text": "Inspecting", "is_thought": True})
        window._on_chunk({"text": "First answer"})
        window._on_chunk({"text": "\nRunning: rg\n", "is_progress": True, "is_thought": True})
        window._on_chunk({"text": "Second answer"})
        window._on_metadata({
            "display_segments": [
                {"text": "Inspecting", "is_thought": True},
                {"text": "First answer", "is_thought": False},
                {"text": "\nRunning: rg\n", "is_thought": True},
                {"text": "Second answer", "is_thought": False},
            ],
            "harness": {
                "provider": "codex",
                "session_id": "thread-1",
                "cwd": "/repo",
                "workspace_changes": {
                    "source": "harness_file_events",
                    "files": [{"path": "module.py", "added": 1, "deleted": 1}],
                },
            },
        })
        window._on_finished()

        saved = conversations[0]["messages"][-1]
        assert saved["content"] == "First answer\nSecond answer"
        assert [segment["is_thought"] for segment in saved["display_segments"]] == [True, False, True, False]
        assert "Running: rg" in saved["display_content"]
        assert saved["workspace_changes"]["files"][0]["path"] == "module.py"
        assert conversations[0]["harness_sessions"]["codex"]["session_id"] == "thread-1"
    finally:
        window._current_ai_label = None
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_local_file_work_shows_link_without_auto_opening_monitor():
    """A local-file turn advertises its monitor but leaves opening it to the user."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [{"messages": [{"role": "user", "content": "edit the file"}]}]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        window.begin_external_reply_stream(0)
        window._on_chunk({
            "type": "chunk",
            "local_work": {
                "tool": "read_file",
                "relative_path": "notes.txt",
                "path": "C:/repo/notes.txt",
                "phase": "started",
            },
        })
        notice = window.findChild(QLabel, "localWorkMonitorNotice")
        dialog = window._current_local_work_dialog

        assert notice is not None
        assert "working with local files" in notice.text().lower()
        assert "#d8a145" in notice.text()
        assert "text-decoration:underline" in notice.text()
        assert dialog is not None and dialog.isVisible() is False

        notice.linkActivated.emit("openwand-local-work")
        app.processEvents()
        assert dialog.isVisible() is True
        assert "Reading: notes.txt" in dialog.activity_view.toPlainText()

        window.finish_external_reply_stream(0)
        assert dialog.status_label.text() == "Local-file work finished."
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_late_chunk_for_other_conversation_does_not_contaminate_active_stream():
    """An in-flight query's late reply must not append into a newer query's bubble.

    Reproduces the overlapping-query race: the first question is still in-flight
    (no reply yet), the user asks a second question that begins streaming, and
    then the first question's answer finally arrives. Its chunks target
    conversation 0, but the live bubble belongs to conversation 1, so they must
    be dropped.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {"messages": [{"role": "user", "content": "first question"}]},
        {"messages": [{"role": "user", "content": "second question"}]},
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        # First query reserves its bubble, then stalls (no chunks yet).
        window.begin_external_reply_stream(0)
        # Second query takes over the live stream and produces real output.
        window.begin_external_reply_stream(1)
        assert window._streaming_idx == 1
        window.external_reply_chunk(1, "answer two")
        assert window._current_ai_reply_text == "answer two"

        # The stalled first query finally replies — these must be ignored.
        window.external_reply_chunk(0, " LEAKED-ONE")
        window.finish_external_reply_stream(0, "answer one")

        assert "LEAKED" not in window._current_ai_reply_text
        assert window._current_ai_reply_text == "answer two"
        assert window._streaming_idx == 1
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_followup_injects_hidden_file_context():
    """Verify file metadata is sent as hidden system context, not message turns."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    captured = []
    conversations = [
        {
            "messages": [{"role": "user", "content": "add a comment"}],
            "file_context": [
                {
                    "tool": "create_file",
                    "path": r"C:\repo\model_files\hello_world.py",
                    "relative_path": "hello_world.py",
                    "ok": True,
                }
            ],
        }
    ]

    def send_fn(messages):
        captured.append(messages)
        yield "ok"

    window = ChatWindow(conversations, send_fn)
    try:
        window._send("edit that file")
        for _ in range(20):
            app.processEvents()
            if captured:
                break

        assert captured
        assert r"C:\repo\model_files\hello_world.py" in captured[0][0]["content"]
        assert all("file_context" not in message for message in captured[0])
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_window_persists_returned_tool_context():
    """Verify returned tool policy metadata is stored with the conversation."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [{"messages": [{"role": "user", "content": "hi"}]}]
    tool_context = {
        "allowed_tools": ["read_file", "edit_file"],
        "pinned_tools": ["read_file", "edit_file"],
        "file_access_mode": "ask",
    }
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        window._current_ai_text = "done"
        window._current_ai_reply_text = "done"
        window._on_metadata({"tool_context": tool_context})
        window._on_finished()

        assert conversations[0]["tool_context"] == tool_context
        assert conversations[0]["messages"][-1]["tool_context"] == tool_context
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_window_persists_returned_text_annotations():
    """Verify addon annotation metadata is stored with direct chat turns."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [{"messages": [{"role": "user", "content": "bubble"}]}]
    user_annotations = [{"start": 0, "end": 6, "kind": "underline"}]
    assistant_annotations = [{"start": 0, "end": 4, "kind": "highlight"}]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        window._current_user_message = conversations[0]["messages"][0]
        window._current_ai_text = "done"
        window._current_ai_reply_text = "done"
        window._on_metadata(
            {
                "user_annotations": user_annotations,
                "annotations": assistant_annotations,
            }
        )
        window._on_finished()

        assert conversations[0]["messages"][0]["annotations"] == user_annotations
        assert conversations[0]["messages"][-1]["annotations"] == assistant_annotations
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_branch_from_message_recomputes_hidden_context():
    """Verify branching keeps only retained message-scoped hidden context."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    first_file_context = [
        {
            "tool": "create_file",
            "path": r"C:\repo\first.py",
            "relative_path": "first.py",
            "root": "",
            "ok": True,
            "message": "",
        }
    ]
    later_file_context = [
        {
            "tool": "create_file",
            "path": r"C:\repo\later.py",
            "relative_path": "later.py",
            "root": "",
            "ok": True,
            "message": "",
        }
    ]
    first_tool_context = {
        "allowed_tools": ["read_file"],
        "pinned_tools": ["read_file"],
        "file_access_mode": "read",
    }
    later_tool_context = {
        "allowed_tools": ["read_file", "edit_file"],
        "pinned_tools": ["edit_file"],
        "file_access_mode": "ask",
    }
    conversations = [
        {
            "messages": [
                {"role": "user", "content": "first", "context": "[Attached]\nfirst context"},
                {
                    "role": "assistant",
                    "content": "done",
                    "file_context": first_file_context,
                    "tool_context": first_tool_context,
                },
                {"role": "user", "content": "later", "context": "[Attached]\nlater context"},
                {
                    "role": "assistant",
                    "content": "later done",
                    "file_context": later_file_context,
                    "tool_context": later_tool_context,
                },
            ],
            "context": "first context\n\n---\nlater context",
            "file_context": first_file_context + later_file_context,
            "tool_context": later_tool_context,
        }
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        window._branch_from_message(0, 1)

        assert len(conversations) == 2
        branch = conversations[1]
        assert [m["content"] for m in branch["messages"]] == ["first", "done"]
        assert branch["context"] == "[Attached]\nfirst context"
        assert branch["file_context"] == first_file_context
        assert branch["tool_context"] == first_tool_context
        assert window._active_idx == 1
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_rewind_current_chat_requires_confirmation(monkeypatch):
    """Verify destructive rewind truncates only after confirmation."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "messages": [
                {"role": "user", "content": "first", "context": "first context"},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "later", "context": "later context"},
            ],
            "context": "first context\n\n---\nlater context",
        }
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    try:
        window._rewind_to_message(0, 0)

        assert [m["content"] for m in conversations[0]["messages"]] == ["first"]
        assert conversations[0]["context"] == "first context"
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_stale_records_and_active_stream_are_rejected_without_mutation():
    """Stale selections and mid-stream actions cannot alter conversation state."""
    from copy import deepcopy

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "id": "current",
            "project_id": "general",
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "done"},
            ],
        }
    ]
    persisted = []
    window = ChatWindow(
        conversations,
        lambda _messages: iter(()),
        persist_fn=lambda: persisted.append(True),
    )
    try:
        before = deepcopy(conversations)

        # Stale conversation, project, and message records all fail closed.
        window._toggle_pin(99)
        window._assign_project(0, "deleted-project")
        window._branch_from_message(0, 99)
        window._rewind_to_message(0, 99)
        assert conversations == before
        assert persisted == []

        # A valid but currently streaming conversation is equally immutable.
        window._streaming = True
        window._streaming_idx = 0
        window._branch_from_message(0, 0)
        window._rewind_to_message(0, 0)
        window._delete_conversation(0)
        window._send("second request")
        assert conversations == before
        assert persisted == []
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_window_drop_attachments_feed_next_message_context_and_image(tmp_path, monkeypatch):
    """Verify dropped files/images attach to the next outgoing chat message."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton, QTextBrowser, QWidget

    from core.conversation_store import store as conversation_store

    chats = tmp_path / "chats"
    monkeypatch.setattr(conversation_store, "CHATS_DIR", chats)
    monkeypatch.setattr(conversation_store, "CHAT_ATTACHMENTS_DIR", chats / "attachments")
    app = QApplication.instance() or QApplication(sys.argv)
    captured = []
    conversations = [{"messages": []}]
    image_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\nshot").decode("ascii")

    def send_fn(messages):
        captured.append(messages)
        yield "ok"

    window = ChatWindow(conversations, send_fn)
    try:
        added = window._add_attachment_items([
            ("notes.txt", "remember this text", "text"),
            ("shot.png", image_b64, "image"),
        ])
        assert added is True

        window._send("use the attachment")
        for _ in range(20):
            app.processEvents()
            if captured:
                break

        assert captured
        assert "remember this text" not in captured[0][0]["content"]
        assert "remember this text" in captured[0][-1]["content"]
        assert captured[0][-1]["image_base64"] == image_b64
        user_message = conversations[0]["messages"][0]
        assert "image_base64" not in user_message
        assert user_message["attachments"][0]["path"].startswith("attachments/")
        assert "remember this text" in user_message["context"]
        assert "remember this text" not in conversations[0].get("context", "")
        disclosure = window.findChild(QWidget, "messageContextDisclosure")
        toggle = window.findChild(QPushButton, "messageContextToggle")
        context_body = window.findChild(QTextBrowser, "messageContextBody")
        assert disclosure is not None
        assert toggle is not None
        assert context_body is not None
        assert context_body.isHidden()

        toggle.click()
        app.processEvents()
        assert not context_body.isHidden()
        assert "remember this text" in context_body.toPlainText()
        assert toggle.text() == "Hide context"

        toggle.click()
        app.processEvents()
        assert context_body.isHidden()
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_assistant_image_only_bubble_renders_a_thumbnail(tmp_path):
    """Generated images render for assistants without requiring fallback text."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    image_path = tmp_path / "generated.png"
    source = QPixmap(24, 16)
    source.fill(QColor("#3b82f6"))
    assert source.save(str(image_path), "PNG")
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    window = ChatWindow([{"messages": []}], lambda _messages: iter(()))
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.addStretch()
    try:
        view = window._bubble(layout, "", "assistant", image_b64)
        wrapper = view.parentWidget()
        thumbnails = [
            label
            for label in wrapper.findChildren(QLabel)
            if label.pixmap() is not None and not label.pixmap().isNull()
        ]

        assert thumbnails
        assert view.property("openwand_has_image") is True
        assert view.isHidden()
    finally:
        window.close()
        container.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_attachment_button_path_feeds_next_message_context(tmp_path):
    """Verify file-picker attachments use the same context path as drag/drop."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    app = QApplication.instance() or QApplication(sys.argv)
    captured = []
    note = tmp_path / "note.txt"
    note.write_text("button-added context", encoding="utf-8")
    conversations = [{"messages": []}]

    def send_fn(messages):
        captured.append(messages)
        yield "ok"

    window = ChatWindow(conversations, send_fn)
    try:
        attach_btn = window.findChild(QPushButton, "chatAttachButton")
        assert attach_btn is not None
        assert attach_btn.text() == "+"

        assert window._add_attachment_paths([str(note)]) is True
        window._send("use the picked file")
        for _ in range(20):
            app.processEvents()
            if captured:
                break

        assert captured
        assert "button-added context" not in captured[0][0]["content"]
        assert "button-added context" in captured[0][-1]["content"]
        user_message = conversations[0]["messages"][0]
        assert user_message["attachments"][0]["source"] == "external_path"
        assert user_message["attachments"][0]["path"] == str(note)
        assert "button-added context" not in conversations[0].get("context", "")
        assert "button-added context" not in user_message.get("context", "")
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 not installed")
def test_chat_captured_selected_path_shows_and_feeds_next_message_context(tmp_path):
    """Verify captured file selection appears in chat and feeds the next model turn."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    captured = []
    note = tmp_path / "selected-note.txt"
    note.write_text("captured selected file context", encoding="utf-8")
    conversations = [{"messages": []}]

    def send_fn(messages):
        captured.append(messages)
        yield "ok"

    window = ChatWindow(conversations, send_fn)
    try:
        result = window.attach_captured_context(
            name="Selection",
            content="",
            item_type="text",
            source="selection",
            paths=[str(note)],
        )
        assert result["attached"] is True
        assert window._attachment_label is not None
        assert not window._attachment_label.isHidden()
        assert note.name in window._attachment_label.toolTip()

        window._send("what do you see")
        for _ in range(20):
            app.processEvents()
            if captured:
                break

        assert captured
        assert "captured selected file context" in captured[0][-1]["content"]
        user_message = conversations[0]["messages"][0]
        assert user_message["attachments"][0]["source"] == "external_path"
        assert user_message["attachments"][0]["path"] == str(note)
    finally:
        window.close()
        app.processEvents()
