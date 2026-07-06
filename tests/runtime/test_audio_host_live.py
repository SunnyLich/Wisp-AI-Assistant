"""Tests for the audio worker's live voice conversation handlers."""

from __future__ import annotations

import pytest

from core import live_voice
from runtime.workers import audio_host


class FakeLiveSession:
    def __init__(self, cfg=None, emit=None, **_kwargs):
        self.cfg = cfg
        self.emit = emit
        self.started = False
        self.stop_reasons: list[str] = []
        self.join_timeouts: list[float | None] = []
        self._active = True

    def start(self) -> None:
        self.started = True

    def request_stop(self, reason: str = "user") -> None:
        self.stop_reasons.append(reason)

    def join(self, timeout=None) -> bool:
        self.join_timeouts.append(timeout)
        self._active = False
        return True

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def state(self) -> str:
        return "listening" if self._active else "idle"


@pytest.fixture(autouse=True)
def _clean_live_state():
    audio_host._live_session = None
    audio_host._playback_stop.clear()
    yield
    audio_host._live_session = None
    audio_host._playback_stop.clear()


@pytest.fixture
def startable(monkeypatch):
    """Environment where audio_live_start succeeds with a FakeLiveSession."""
    import config
    from core.macos_helper import handlers as stt_handlers

    monkeypatch.setattr(live_voice, "genai_available", lambda: True)
    monkeypatch.setattr(live_voice, "LiveVoiceSession", FakeLiveSession)
    monkeypatch.setattr(stt_handlers, "stt_is_recording", lambda: False)
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "LIVE_VOICE_MODEL", "gemini-test-live", raising=False)
    monkeypatch.setattr(config, "LIVE_VOICE_VOICE_NAME", "Puck", raising=False)
    monkeypatch.setattr(config, "LIVE_VOICE_HALF_DUPLEX", True, raising=False)
    monkeypatch.setattr(
        config, "get_live_voice_system_prompt", lambda: "live prompt", raising=False
    )


def test_start_requires_google_genai(monkeypatch):
    monkeypatch.setattr(live_voice, "genai_available", lambda: False)

    assert audio_host.audio_live_start() == {"started": False, "error": "missing_package"}


def test_start_requires_api_key(monkeypatch):
    import config

    monkeypatch.setattr(live_voice, "genai_available", lambda: True)
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "", raising=False)

    assert audio_host.audio_live_start() == {"started": False, "error": "missing_key"}


def test_start_refuses_while_hold_to_talk_recording(monkeypatch):
    import config
    from core.macos_helper import handlers as stt_handlers

    monkeypatch.setattr(live_voice, "genai_available", lambda: True)
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(stt_handlers, "stt_is_recording", lambda: True)

    assert audio_host.audio_live_start() == {"started": False, "error": "mic_busy"}


def test_start_builds_session_from_config(startable):
    result = audio_host.audio_live_start()

    assert result == {"started": True, "model": "gemini-test-live"}
    session = audio_host._live_session
    assert session.started
    assert session.cfg.api_key == "test-key"
    assert session.cfg.voice_name == "Puck"
    assert session.cfg.half_duplex is True
    assert session.cfg.system_prompt == "live prompt"
    # live session owns the speaker: any file playback loop must stop
    assert audio_host._playback_stop.is_set()


def test_start_refuses_while_already_active(startable):
    audio_host.audio_live_start()

    assert audio_host.audio_live_start() == {"started": False, "error": "already_active"}


def test_start_replaces_session_that_ended_on_its_own(startable):
    audio_host.audio_live_start()
    first = audio_host._live_session
    first._active = False  # e.g. server closed after ~15 min

    assert audio_host.audio_live_start()["started"] is True
    assert audio_host._live_session is not first


def test_session_events_are_prefixed(startable):
    events: list[tuple[str, dict]] = []
    audio_host.set_event_sink(lambda name, data, _req_id: events.append((name, data)))
    try:
        audio_host.audio_live_start()
        audio_host._live_session.emit("transcript", {"role": "user", "text": "hi"})
    finally:
        audio_host.set_event_sink(lambda *_args: None)

    assert events == [("audio.live.transcript", {"role": "user", "text": "hi"})]


def test_stop_without_session_is_a_noop():
    assert audio_host.audio_live_stop() == {"stopped": False}


def test_stop_ends_active_session(startable):
    audio_host.audio_live_start()
    session = audio_host._live_session

    assert audio_host.audio_live_stop() == {"stopped": True}
    assert session.stop_reasons == ["user"]
    assert session.join_timeouts == [3.0]
    assert audio_host._live_session is None
    # a second stop finds nothing to do
    assert audio_host.audio_live_stop() == {"stopped": False}


def test_status_reflects_session_lifecycle(startable):
    assert audio_host.audio_live_status() == {"active": False, "state": "idle", "model": ""}

    audio_host.audio_live_start()
    assert audio_host.audio_live_status() == {
        "active": True,
        "state": "listening",
        "model": "gemini-test-live",
    }

    audio_host.audio_live_stop()
    assert audio_host.audio_live_status() == {"active": False, "state": "idle", "model": ""}


def test_config_reload_stops_live_session(monkeypatch, startable):
    """Settings Apply rebuilds audio config; a stale live session must not survive it."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    audio_host.audio_live_start()
    session = audio_host._live_session

    class FakeThread:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(config, "reload", lambda: None)
    monkeypatch.setattr(config, "TTS_PROVIDER", "none", raising=False)
    monkeypatch.setattr(tts, "reset_connections", lambda: None)
    monkeypatch.setattr(stt_handlers, "stt_reset_model", lambda: None)
    monkeypatch.setattr(audio_host.threading, "Thread", FakeThread)

    result = audio_host.audio_config_reload()

    assert result["ok"] is True
    assert session.stop_reasons == ["config_reload"]
    assert audio_host._live_session is None
