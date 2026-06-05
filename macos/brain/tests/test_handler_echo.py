"""Unit tests for the dependency-free streaming ``brain.echo`` handler."""
from __future__ import annotations

from wisp_brain import handlers


def _chunks(events):
    return [data["text"] for event, data in events if event == "reply.chunk"]


def test_echo_is_registered_as_streaming():
    assert "brain.echo" in handlers.HANDLERS
    assert "brain.echo" in handlers.STREAMING


def test_echo_streams_words_then_returns_full(record_ctx):
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.echo"](ctx, text="one two three four")
    assert result["text"] == "one two three four"
    assert "".join(_chunks(events)) == "one two three four"
    # exactly one terminal reply.done carrying the full text
    done = [data for event, data in events if event == "reply.done"]
    assert done == [{"text": "one two three four"}]


def test_echo_respects_chunk_size(record_ctx):
    events, ctx = record_ctx()
    handlers.HANDLERS["brain.echo"](ctx, text="a b c d e f", chunk_size=2)
    # 6 words in groups of 2 -> 3 chunks, still reassembling exactly.
    chunks = _chunks(events)
    assert len(chunks) == 3
    assert "".join(chunks) == "a b c d e f"


def test_echo_empty_text_emits_done_only(record_ctx):
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.echo"](ctx, text="")
    assert result["text"] == ""
    assert _chunks(events) == []
    assert ("reply.done", {"text": ""}) in events


def test_echo_precancelled_emits_no_chunks(record_ctx):
    events, ctx = record_ctx()
    ctx.cancelled = True
    result = handlers.HANDLERS["brain.echo"](ctx, text="a b c d e")
    assert result["text"] == ""
    assert _chunks(events) == []
