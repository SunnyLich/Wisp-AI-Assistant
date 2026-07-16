"""Unit tests for core.live_voice — no google-genai or audio devices needed.

The session accepts injected ``connector`` and ``stream_factory`` callables,
so these tests run the full asyncio-on-a-thread machinery with fakes and
drive the mic/speaker callbacks directly.
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from types import SimpleNamespace

from core.live_voice import (
    HANGOVER_S,
    LiveVoiceConfig,
    LiveVoiceSession,
    Playback,
)


class ConnectionClosedOK(Exception):
    """Name-matched stand-in for websockets' polite close."""


class EventLog:
    """Collects (name, payload) emits across threads."""

    def __init__(self) -> None:
        self._events: list[tuple[str, dict]] = []
        self._cond = threading.Condition()

    def __call__(self, name: str, payload: dict) -> None:
        with self._cond:
            self._events.append((name, payload))
            self._cond.notify_all()

    def snapshot(self) -> list[tuple[str, dict]]:
        with self._cond:
            return list(self._events)

    def wait_for(self, predicate, timeout: float = 5.0) -> tuple[str, dict]:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for event in self._events:
                    if predicate(event):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"no matching event; got {self._events!r}"
                    )
                self._cond.wait(remaining)

    def wait_for_named(self, name: str, timeout: float = 5.0) -> dict:
        return self.wait_for(lambda e: e[0] == name, timeout)[1]

    def wait_until(self, condition, timeout: float = 5.0) -> None:
        """Block until ``condition(all_events)`` is true (unlike ``wait_for``,
        which matches a single event and so can't express ordering)."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while not condition(list(self._events)):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"condition never held; got {self._events!r}"
                    )
                self._cond.wait(remaining)

    def states(self) -> list[str]:
        return [p["state"] for n, p in self.snapshot() if n == "state"]

    def count(self, name: str) -> int:
        return sum(1 for event_name, _ in self.snapshot() if event_name == name)


def spoke_then_listening(events: list[tuple[str, dict]]) -> bool:
    states = [p["state"] for n, p in events if n == "state"]
    return "speaking" in states and states[-1] == "listening"


class FakeSession:
    """Scripted Live API session: replays messages, then idles until cancelled."""

    def __init__(self, script=(), *, close_after_script: bool = False) -> None:
        self.script = list(script)
        self.close_after_script = close_after_script
        self.sent: list[bytes] = []
        self._sent_cond = threading.Condition()
        self._drained = None  # asyncio.Event, created lazily on the loop

    async def send_realtime_input(self, *, audio) -> None:
        with self._sent_cond:
            self.sent.append(audio["data"])
            self._sent_cond.notify_all()
        assert audio["mime_type"] == "audio/pcm;rate=16000"

    def wait_sent(self, count: int, timeout: float = 5.0) -> list[bytes]:
        deadline = time.monotonic() + timeout
        with self._sent_cond:
            while len(self.sent) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"only {len(self.sent)} chunks sent")
                self._sent_cond.wait(remaining)
            return list(self.sent)

    async def _receive(self):
        for msg in self.script:
            yield msg
        self.script = []
        if self.close_after_script:
            raise ConnectionClosedOK()
        await asyncio.Event().wait()  # idle until the receiver task is cancelled

    def receive(self):
        return self._receive()


class FakeConnector:
    def __init__(self, session: FakeSession, *, fail: Exception | None = None):
        self.session = session
        self.fail = fail

    def __call__(self, cfg: LiveVoiceConfig):
        connector = self

        class _Ctx:
            async def __aenter__(self):
                if connector.fail is not None:
                    raise connector.fail
                return connector.session

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


class FakeStreams:
    """Captures the mic/speaker callbacks so tests can drive them directly."""

    def __init__(self) -> None:
        self.mic_callback = None
        self.speaker_callback = None

    def __call__(self, mic_callback, speaker_callback):
        self.mic_callback = mic_callback
        self.speaker_callback = speaker_callback
        return contextlib.nullcontext(), contextlib.nullcontext()


def make_msg(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def content_msg(**kwargs) -> SimpleNamespace:
    return make_msg(server_content=SimpleNamespace(**kwargs))


def run_session(
    script=(),
    *,
    close_after_script: bool = False,
    fail: Exception | None = None,
    **cfg_overrides,
):
    cfg = LiveVoiceConfig(api_key="test-key", **cfg_overrides)
    events = EventLog()
    fake_session = FakeSession(script, close_after_script=close_after_script)
    streams = FakeStreams()
    session = LiveVoiceSession(
        cfg,
        events,
        connector=FakeConnector(fake_session, fail=fail),
        stream_factory=streams,
    )
    session.start()
    return session, events, fake_session, streams


def stop_and_join(session: LiveVoiceSession, reason: str = "user") -> None:
    session.request_stop(reason)
    assert session.join(5.0), "session thread did not exit"


def test_clean_stop_emits_single_user_ended():
    session, events, _fake, _streams = run_session()
    events.wait_for(lambda e: e == ("state", {"state": "listening"}))
    assert session.is_active
    assert session.state == "listening"
    stop_and_join(session)
    assert events.wait_for_named("ended") == {"reason": "user"}
    assert events.count("ended") == 1
    assert not session.is_active
    # second stop is a no-op, not a second event
    session.request_stop("config_reload")
    assert events.count("ended") == 1


def test_connecting_then_listening_state_order():
    session, events, _fake, _streams = run_session()
    events.wait_for(lambda e: e == ("state", {"state": "listening"}))
    states = [p["state"] for n, p in events.snapshot() if n == "state"]
    assert states[:2] == ["connecting", "listening"]
    stop_and_join(session)


def test_mic_chunks_flow_to_session():
    session, events, fake, streams = run_session()
    events.wait_for(lambda e: e == ("state", {"state": "listening"}))
    chunk = b"\x01\x02" * 160
    streams.mic_callback(chunk, 160, None, None)
    streams.mic_callback(chunk, 160, None, None)
    assert fake.wait_sent(2) == [chunk, chunk]
    stop_and_join(session)


def test_transcript_role_mapping():
    script = [
        content_msg(input_transcription=SimpleNamespace(text="hello ")),
        content_msg(output_transcription=SimpleNamespace(text="hi there")),
    ]
    session, events, _fake, _streams = run_session(script)
    events.wait_for(
        lambda e: e == ("transcript", {"role": "assistant", "text": "hi there"})
    )
    events.wait_for(
        lambda e: e == ("transcript", {"role": "user", "text": "hello "})
    )
    stop_and_join(session)


def test_barge_in_clears_playback_and_returns_to_listening():
    pcm = b"\x11\x22" * 4800  # enough to keep playback active
    script = [
        make_msg(data=pcm),
        content_msg(interrupted=True),
    ]
    session, events, _fake, streams = run_session(script)
    events.wait_for(lambda e: e == ("state", {"state": "speaking"}))
    events.wait_until(spoke_then_listening)
    # buffer was dropped: the speaker callback now gets pure silence
    out = bytearray(64)
    streams.speaker_callback(out, 32, None, None)
    assert bytes(out) == b"\x00" * 64
    stop_and_join(session)


def test_go_away_emits_expiring_advisory():
    script = [make_msg(go_away=SimpleNamespace(time_left="10s"))]
    session, events, _fake, _streams = run_session(script)
    payload = events.wait_for_named("error")
    assert payload["code"] == "expiring"
    stop_and_join(session)
    assert events.wait_for_named("ended") == {"reason": "user"}


def test_server_close_ends_with_server_closed():
    session, events, _fake, _streams = run_session(
        [content_msg(output_transcription=SimpleNamespace(text="bye"))],
        close_after_script=True,
    )
    assert events.wait_for_named("ended") == {"reason": "server_closed"}
    assert session.join(5.0)
    assert events.count("error") == 0


def test_connector_failure_emits_error_then_ended():
    session, events, _fake, _streams = run_session(
        fail=RuntimeError("bad api key")
    )
    payload = events.wait_for_named("error")
    assert payload["code"] == "session_failed"
    assert "bad api key" in payload["message"]
    assert events.wait_for_named("ended") == {"reason": "error"}
    assert session.join(5.0)


def test_half_duplex_gates_mic_while_playback_active():
    pcm = b"\x11\x22" * 2400
    session, events, fake, streams = run_session(
        [make_msg(data=pcm)], half_duplex=True
    )
    events.wait_for(lambda e: e == ("state", {"state": "speaking"}))
    chunk = b"\x01\x02" * 160
    streams.mic_callback(chunk, 160, None, None)
    # drain playback fully, then wait out the hangover
    out = bytearray(len(pcm))
    streams.speaker_callback(out, len(pcm) // 2, None, None)
    events.wait_until(spoke_then_listening)
    assert fake.sent == []  # gated chunk was dropped, not queued
    streams.mic_callback(chunk, 160, None, None)
    assert fake.wait_sent(1) == [chunk]
    stop_and_join(session)


def test_full_duplex_sends_while_playback_active():
    pcm = b"\x11\x22" * 4800
    session, events, fake, streams = run_session([make_msg(data=pcm)])
    events.wait_for(lambda e: e == ("state", {"state": "speaking"}))
    chunk = b"\x01\x02" * 160
    streams.mic_callback(chunk, 160, None, None)
    assert fake.wait_sent(1) == [chunk]
    stop_and_join(session)


def test_stop_reason_config_reload_propagates():
    session, events, _fake, _streams = run_session()
    events.wait_for(lambda e: e == ("state", {"state": "listening"}))
    stop_and_join(session, reason="config_reload")
    assert events.wait_for_named("ended") == {"reason": "config_reload"}


def test_playback_pull_pads_with_silence_and_tracks_hangover():
    playback = Playback()
    playback.feed(b"\xaa\xbb\xcc\xdd")
    assert playback.pull(2) == b"\xaa\xbb"
    assert playback.pull(4) == b"\xcc\xdd\x00\x00"
    assert playback.active()  # inside hangover window
    time.sleep(HANGOVER_S + 0.05)
    assert not playback.active()


def test_playback_clear_reports_dropped_bytes():
    playback = Playback()
    playback.feed(b"\x00" * 10)
    assert playback.clear() == 10
    assert playback.pull(4) == b"\x00\x00\x00\x00"
    assert playback.clear() == 0
