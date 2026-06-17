"""End-to-end integration: drive the real ``wisp_brain.host`` subprocess through a
full session, the way the the supervisor client would in real use.

This is the "try them together" pass. It spawns the actual brain worker process and
exercises every handler over the real newline-JSON transport -- liveness,
streaming reply, a context-bearing query, TTS file synthesis, STT short-clip
handling, a scoped agent run, mid-stream cancel, and clean shutdown -- all
offline via the ``WISP_BRAIN_FAKE_LLM`` seam so it needs no API keys, models, or
audio devices and never touches the user's real memory store.

The ``BrainSidecar`` host harness is reused from ``test_brain_host`` so this test
talks to the brain worker exactly like the worker handshake test does.
"""
from __future__ import annotations

import importlib.util
import threading
import wave
from pathlib import Path
from typing import Any

import pytest

from test_brain_host import BrainSidecar

_HAS_SOUNDFILE = importlib.util.find_spec("soundfile") is not None


@pytest.fixture
def brain_worker(tmp_path, monkeypatch):
    # Offline, deterministic brain; artifacts confined to the test's tmp dir.
    """Verify brain worker behavior."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(tmp_path))
    s = BrainSidecar()
    try:
        yield s
    finally:
        s.shutdown()


def _chunks(events: list[dict[str, Any]]) -> list[str]:
    """Verify chunks behavior."""
    return [e["data"]["text"] for e in events if e["event"] == "reply.chunk"]


def test_full_session_like_real_use(brain_worker, tmp_path):
    """Verify full session like real use behavior."""
    s = brain_worker

    # 1. Liveness handshake (the menubar's brain-status probe).
    _, ping = s.call("ping", {"value": "wake"})
    assert ping["pong"] is True and ping["value"] == "wake"

    # 2. Streaming echo (the overlay's dependency-free smoke path).
    echo_rid, echo = s.call("brain.echo", {"text": "the brain seam works"})
    assert echo["text"] == "the brain seam works"
    assert "".join(_chunks(s.events_for(echo_rid))) == "the brain seam works"

    # 3. Real query path with selected + ambient context, streamed back.
    q_rid, query = s.call(
        "brain.query",
        {
            "intent_prompt": "explain this code",
            "selected": "def add(a, b): return a + b",
            "ambient_text": "VSCode - scratch.py",
            "memory_context": "(none)",
        },
        timeout=30,
    )
    assert query["text"].startswith("[fake-llm]")
    assert "explain this code" in query["text"]
    assert "def add(a, b)" in query["text"]
    assert _chunks(s.events_for(q_rid)), "query should stream reply.chunk events"

    # 4. TTS synthesis returns a real, playable WAV file on disk (path over IPC).
    _, tts = s.call("brain.tts.synthesize", {"text": "all systems go"}, timeout=30)
    tts_path = Path(tts["path"])
    assert tts_path.is_file() and tts["bytes"] > 0
    with wave.open(str(tts_path), "rb") as wf:
        assert wf.getnchannels() == 1 and wf.getsampwidth() == 2

    # 5. STT: a short silent clip is handled without loading a model.
    if _HAS_SOUNDFILE:
        clip = tmp_path / "blip.wav"
        with wave.open(str(clip), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16_000)
            wf.writeframes(b"\x00\x00" * 1600)  # 0.1s < 0.25s gate
        _, stt = s.call("brain.transcribe", {"pcm_path": str(clip)}, timeout=60)
        assert stt["reason"] == "too_short"

    # 6. Scoped agent run streams progress and returns a final report.
    scope = tmp_path / "agent_scope"
    scope.mkdir()
    agent_rid, agent = s.call(
        "brain.agent.run",
        {"spec": {"title": "smoke", "objective": "noop", "scope_folder": str(scope),
                  "allow_git": False, "allow_shell": False},
         "log_root": str(tmp_path / "agent_runs")},
        timeout=60,
    )
    assert "Fake agent run complete." in agent["final"]
    assert agent["error"] == ""
    agent_events = s.events_for(agent_rid)
    assert any(e["event"] == "agent.log" for e in agent_events)
    assert any(e["event"] == "agent.done" for e in agent_events)
    assert Path(agent["run_dir"], "final.md").is_file()

    # 7. A final ping proves the brain worker stayed responsive through all of it.
    _, ping2 = s.call("ping")
    assert ping2["pong"] is True


def test_cancel_stops_a_long_stream(brain_worker):
    """Verify cancel stops a long stream behavior."""
    s = brain_worker
    rid = next(s._ids)
    ev = threading.Event()
    s._pending[rid] = {"event": ev, "resp": None}
    with s._write_lock:
        from wisp_brain import protocol

        protocol.write_message(
            s._proc.stdin,
            {"id": rid, "method": "brain.echo",
             "params": {"text": " ".join(str(i) for i in range(60)), "delay": 0.02}},
        )
    threading.Event().wait(0.1)
    s.call("brain.cancel", {"target": rid})
    ev.wait(5)
    chunks = [e for e in s.events_for(rid) if e["event"] == "reply.chunk"]
    assert len(chunks) < 60, "cancel should cut the stream short"


def test_unknown_method_is_an_error(brain_worker):
    """Verify unknown method is an error behavior."""
    with pytest.raises(RuntimeError):
        brain_worker.call("does.not.exist", timeout=5)


