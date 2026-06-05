"""Unit tests for the ``brain.query`` handler.

These run fully offline via the ``WISP_BRAIN_FAKE_LLM`` seam: the real
``core.query_pipeline.build_context`` still assembles the prompt (so context
precedence is exercised for real), but the token stream is a deterministic echo
instead of a provider call. That lets us assert streaming, reassembly, that the
caller's inputs reached the model, and cooperative cancel -- with no API key.
"""
from __future__ import annotations

import pytest

from wisp_brain import handlers


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")


def _chunks(events):
    return [data["text"] for event, data in events if event == "reply.chunk"]


def test_query_is_registered_as_streaming():
    assert "brain.query" in handlers.HANDLERS
    assert "brain.query" in handlers.STREAMING


def test_query_streams_and_reassembles(record_ctx):
    events, ctx = record_ctx(req_id=7)
    result = handlers.HANDLERS["brain.query"](
        ctx, intent_prompt="hello world", memory_context="(none)"
    )
    assert result["text"].startswith("[fake-llm]")
    assert "".join(_chunks(events)) == result["text"]
    done = [data for event, data in events if event == "reply.done"]
    assert done == [{"text": result["text"]}]


def test_query_includes_intent_and_selected_in_prompt(record_ctx):
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="summarize this function",
        selected="def add(a, b): return a + b",
        ambient_text="VSCode - main.py",
        memory_context="(none)",
    )
    # The fake reply echoes the assembled user_message, so the inputs that
    # build_context folded in must be visible in the streamed answer.
    assert "summarize this function" in result["text"]
    assert "def add(a, b)" in result["text"]


def test_query_forwards_screenshot_tool_policy(record_ctx, monkeypatch):
    captured = {}

    def fake_stream(_built, _memory_context, use_tools, allow_screenshot_tool):
        captured["use_tools"] = use_tools
        captured["allow_screenshot_tool"] = allow_screenshot_tool
        yield "ok"

    monkeypatch.setattr(handlers, "_stream_query_reply", fake_stream)
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="look if needed",
        memory_context="(none)",
        use_tools=True,
        allow_screenshot_tool=True,
    )

    assert result["text"] == "ok"
    assert "".join(_chunks(events)) == "ok"
    assert captured == {"use_tools": True, "allow_screenshot_tool": True}


def test_query_precancelled_yields_empty(record_ctx):
    events, ctx = record_ctx()
    ctx.cancelled = True
    result = handlers.HANDLERS["brain.query"](
        ctx, intent_prompt="anything", memory_context="(none)"
    )
    assert result["text"] == ""
    assert _chunks(events) == []
