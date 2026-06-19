"""Unit tests for the ``brain.chat`` handler."""
from __future__ import annotations

import threading

import pytest

from wisp_brain import handlers


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Verify offline behavior."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")


def _chunks(events):
    """Verify chunks behavior."""
    return [data["text"] for event, data in events if event == "reply.chunk"]


def test_chat_is_registered_as_streaming():
    """Verify chat is registered as streaming behavior."""
    assert "brain.chat" in handlers.HANDLERS
    assert "brain.chat" in handlers.STREAMING


def test_chat_streams_and_reassembles_last_user_turn(record_ctx):
    """Verify chat streams and reassembles last user turn behavior."""
    events, ctx = record_ctx(req_id=11)
    result = handlers.HANDLERS["brain.chat"](
        ctx,
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "continue please"},
        ],
        memory_context="(none)",
    )

    assert result["text"].startswith("[fake-chat]")
    assert "continue please" in result["text"]
    assert "".join(_chunks(events)) == result["text"]
    done = [data for event, data in events if event == "reply.done"]
    assert done == [{"text": result["text"]}]


def test_chat_requires_a_user_turn(record_ctx):
    """Verify chat requires a user turn behavior."""
    _events, ctx = record_ctx()
    with pytest.raises(ValueError, match="at least one user"):
        handlers.HANDLERS["brain.chat"](ctx, messages=[], memory_context="(none)")


def test_chat_normalizes_messages_and_forwards_memory_context(record_ctx, monkeypatch):
    """Verify chat normalizes messages and forwards memory context behavior."""
    captured = {}

    def fake_stream(
        messages,
        memory_context,
        *,
        use_tools=False,
        allowed_tools=None,
        pinned_tools=None,
        ctx=None,
        file_access_mode="",
        file_context=None,
    ):
        """Verify fake stream behavior."""
        captured["messages"] = messages
        captured["memory_context"] = memory_context
        captured["use_tools"] = use_tools
        captured["allowed_tools"] = allowed_tools
        captured["pinned_tools"] = pinned_tools
        captured["ctx"] = ctx
        captured["file_access_mode"] = file_access_mode
        captured["file_context"] = file_context
        yield "ok"

    monkeypatch.setattr(handlers, "_stream_chat_reply", fake_stream)
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.chat"](
        ctx,
        messages=[
            {"role": "system", "content": "Be brief."},
            {"role": "ignored", "content": "drop me"},
            {"role": "user", "content": "  tell me more  "},
            {"role": "assistant", "content": ""},
        ],
        memory_context="[Memory]\n- one fact",
        use_tools=True,
        allowed_tools=["read_file"],
        pinned_tools=["read_file"],
        file_access_mode="read",
    )

    assert result["text"] == "ok"
    assert "".join(_chunks(events)) == "ok"
    assert captured == {
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "tell me more"},
        ],
        "memory_context": "[Memory]\n- one fact",
        "use_tools": True,
        "allowed_tools": ["read_file"],
        "pinned_tools": ["read_file"],
        "ctx": ctx,
        "file_access_mode": "read",
        "file_context": [],
    }


def test_chat_emits_progress_chunks_without_saving_them(record_ctx, monkeypatch):
    """Verify chat progress narration streams live but is excluded from final text."""
    class ProgressChunk(str):
        """String-like progress chunk."""
        kind = "progress"

    def fake_stream(*_args, **_kwargs):
        """Emit progress then final answer."""
        yield ProgressChunk("Checking the file first.")
        yield "Done."

    monkeypatch.setattr(handlers, "_stream_chat_reply", fake_stream)
    events, ctx = record_ctx()

    result = handlers.HANDLERS["brain.chat"](
        ctx,
        messages=[{"role": "user", "content": "edit file"}],
        memory_context="(none)",
    )

    assert result["text"] == "Done."
    assert _chunks(events) == ["Checking the file first.", "Done."]
    progress = [data for event, data in events if event == "reply.chunk" and data.get("is_progress")]
    assert progress == [{"text": "Checking the file first.", "is_progress": True}]
    assert [data for event, data in events if event == "reply.done"] == [{"text": "Done."}]


def test_chat_memory_disabled_skips_retrieval(record_ctx, monkeypatch):
    """Verify chat memory disabled skips retrieval behavior."""
    from core.memory_store import store

    class FailingManager:
        """Coordinate failing manager behavior."""
        def retrieve_relevant(self, _query):
            """Verify retrieve relevant behavior."""
            raise AssertionError("memory should not be fetched")

    monkeypatch.setattr(store, "get_manager", lambda: FailingManager())

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.chat"](
        ctx,
        messages=[{"role": "user", "content": "what do you know about me"}],
        memory_enabled=False,
    )

    assert result["text"].startswith("[fake-chat]")
    assert "".join(_chunks(events)) == result["text"]


def test_chat_reloads_config_before_live_file_tools(record_ctx, monkeypatch):
    """Verify chat refreshes TOOL_FILE_ROOTS before live local-file tools run."""
    import config

    reloads = []
    monkeypatch.setattr(config, "reload", lambda: reloads.append(True))

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.chat"](
        ctx,
        messages=[{"role": "user", "content": "create hello.py"}],
        memory_enabled=False,
        use_tools=True,
        allowed_tools=["create_file"],
        file_access_mode="ask",
    )

    assert result["text"].startswith("[fake-chat]")
    assert "".join(_chunks(events)) == result["text"]
    assert reloads == [True]


def test_chat_precancelled_yields_empty(record_ctx):
    """Verify chat precancelled yields empty behavior."""
    events, ctx = record_ctx()
    ctx.cancelled = True
    result = handlers.HANDLERS["brain.chat"](
        ctx,
        messages=[{"role": "user", "content": "anything"}],
        memory_context="(none)",
    )

    assert result["text"] == ""
    assert _chunks(events) == []


def test_live_file_approval_callback_emits_request_and_accepts_response(record_ctx):
    """Verify live file approval callback waits for the matching response."""
    events, ctx = record_ctx()
    approved = {"value": False}

    def wait_for_approval():
        """Run the blocking approval callback in a worker thread."""
        approved["value"] = handlers._live_file_approval_callback(ctx)({
            "action": "edit_file",
            "path": "note.txt",
        })

    thread = threading.Thread(target=wait_for_approval, daemon=True)
    thread.start()
    try:
        request = None
        for _ in range(50):
            requests = [data for event, data in events if event == "live_file.approval.request"]
            if requests:
                request = requests[0]
                break
            threading.Event().wait(0.02)
        assert request is not None
        assert request["action"] == "edit_file"

        result = handlers.HANDLERS["brain.live_file.approval.respond"](
            approval_id=request["approval_id"],
            approved=True,
        )
        assert result == {"ok": True, "approved": True}
    finally:
        ctx.cancelled = True
        thread.join(timeout=2.0)

    assert approved["value"] is True
