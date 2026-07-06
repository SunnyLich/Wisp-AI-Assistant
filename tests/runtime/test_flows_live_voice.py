"""Supervisor flow tests for the live voice conversation toggle."""

from __future__ import annotations

import config
from tests.runtime.test_flows import FakeWorker, make_flow


def live_audio_worker(result: dict | None = None) -> FakeWorker:
    return FakeWorker(
        handlers={
            "audio.live.start": lambda _params: result or {"started": True, "model": "gemini-test"},
        }
    )


def notices(ui: FakeWorker) -> list[str]:
    return [str(call["params"].get("text") or "") for call in ui.calls_for("ui.reply.notice")]


def overlay_states(ui: FakeWorker) -> list[str]:
    return [str(call["params"].get("state") or "") for call in ui.calls_for("ui.overlay.state")]


def test_toggle_hotkey_starts_then_stops_session():
    flow, native, ui, _brain, audio = make_flow(audio=live_audio_worker())

    native.emit("native.hotkey", {"kind": "voice_live"})

    assert audio.calls_for("audio.live.start")
    assert "thinking" in overlay_states(ui)
    assert ui.last_call("ui.live_voice.session")["params"] == {"active": True}
    assert flow._live_voice_state == "active"

    native.emit("native.hotkey", {"kind": "voice_live"})

    assert audio.calls_for("audio.live.stop")
    assert ui.last_call("ui.live_voice.session")["params"] == {"active": False}
    assert flow._live_voice_state == "idle"


def test_toggle_ignored_while_transition_in_flight():
    flow, native, _ui, _brain, audio = make_flow(audio=live_audio_worker())

    flow._live_voice_state = "starting"
    native.emit("native.hotkey", {"kind": "voice_live"})
    flow._live_voice_state = "stopping"
    native.emit("native.hotkey", {"kind": "voice_live"})

    assert audio.calls_for("audio.live.start") == []
    assert audio.calls_for("audio.live.stop") == []


def test_start_refused_while_voice_recording():
    flow, native, ui, _brain, audio = make_flow(audio=live_audio_worker())

    flow._voice_state = "recording"
    native.emit("native.hotkey", {"kind": "voice_live"})

    assert audio.calls_for("audio.live.start") == []
    assert any("voice recording" in text for text in notices(ui))
    assert flow._live_voice_state == "idle"


def test_live_voice_blocks_voice_and_dictation_claims():
    flow, *_ = make_flow(audio=live_audio_worker())

    flow._live_voice_state = "active"
    assert flow._claim_voice_start() is False
    assert flow._claim_dictate_start() is False

    flow._live_voice_state = "idle"
    assert flow._claim_voice_start() is True


def test_start_error_notices():
    for error, fragment in (
        ("missing_key", "Google API key"),
        ("missing_package", "not installed"),
        ("mic_busy", "voice recording"),
        ("boom", "Could not start live voice"),
    ):
        flow, native, ui, _brain, audio = make_flow(
            audio=live_audio_worker({"started": False, "error": error})
        )
        native.emit("native.hotkey", {"kind": "voice_live"})

        assert any(fragment in text for text in notices(ui)), (error, notices(ui))
        assert flow._live_voice_state == "idle"
        assert ui.calls_for("ui.live_voice.session") == []


def test_start_adopts_already_active_worker_session():
    flow, native, ui, _brain, _audio = make_flow(
        audio=live_audio_worker({"started": False, "error": "already_active"})
    )

    native.emit("native.hotkey", {"kind": "voice_live"})

    assert flow._live_voice_state == "active"
    assert ui.last_call("ui.live_voice.session")["params"] == {"active": True}
    assert notices(ui) == []


def test_live_state_events_drive_overlay():
    flow, native, ui, _brain, audio = make_flow(audio=live_audio_worker())
    native.emit("native.hotkey", {"kind": "voice_live"})

    audio.emit("audio.live.state", {"state": "speaking"})
    audio.emit("audio.live.state", {"state": "listening"})

    states = overlay_states(ui)
    assert "speaking" in states
    assert states[-1] == "listening"


def test_live_state_events_ignored_when_idle():
    flow, _native, ui, _brain, audio = make_flow(audio=live_audio_worker())

    audio.emit("audio.live.state", {"state": "speaking"})

    assert overlay_states(ui) == []


def test_transcript_events_forward_to_bubble():
    flow, native, ui, _brain, audio = make_flow(audio=live_audio_worker())
    native.emit("native.hotkey", {"kind": "voice_live"})

    audio.emit("audio.live.transcript", {"role": "user", "text": "hello "})
    audio.emit("audio.live.transcript", {"role": "assistant", "text": "hi!"})
    audio.emit("audio.live.transcript", {"role": "other", "text": "nope"})
    audio.emit("audio.live.transcript", {"role": "user", "text": ""})

    forwarded = [call["params"] for call in ui.calls_for("ui.live_voice.transcript")]
    assert forwarded == [
        {"role": "user", "text": "hello "},
        {"role": "assistant", "text": "hi!"},
    ]


def test_server_closed_ends_session_with_notice():
    flow, native, ui, _brain, audio = make_flow(audio=live_audio_worker())
    native.emit("native.hotkey", {"kind": "voice_live"})

    audio.emit("audio.live.ended", {"reason": "server_closed"})

    assert flow._live_voice_state == "idle"
    assert ui.last_call("ui.live_voice.session")["params"] == {"active": False}
    assert any("time limit" in text for text in notices(ui))


def test_user_stop_ended_event_is_silent_and_not_doubled():
    flow, native, ui, _brain, audio = make_flow(audio=live_audio_worker())
    native.emit("native.hotkey", {"kind": "voice_live"})
    native.emit("native.hotkey", {"kind": "voice_live"})  # stop
    session_calls = len(ui.calls_for("ui.live_voice.session"))

    audio.emit("audio.live.ended", {"reason": "user"})  # worker's echo of the stop

    assert notices(ui) == []
    assert len(ui.calls_for("ui.live_voice.session")) == session_calls


def test_expiring_error_is_advisory():
    flow, native, ui, _brain, audio = make_flow(audio=live_audio_worker())
    native.emit("native.hotkey", {"kind": "voice_live"})

    audio.emit("audio.live.error", {"code": "expiring", "message": ""})

    assert any("end soon" in text for text in notices(ui))
    assert flow._live_voice_state == "active"


def test_audio_worker_exit_cleans_up_live_session():
    flow, native, ui, _brain, _audio = make_flow(audio=live_audio_worker())
    native.emit("native.hotkey", {"kind": "voice_live"})

    flow._on_audio_worker_exit(1)

    assert flow._live_voice_state == "idle"
    assert ui.last_call("ui.live_voice.session")["params"] == {"active": False}
    assert any("audio worker restarted" in text for text in notices(ui))

    # no-op when nothing is live
    ui.calls.clear()
    flow._on_audio_worker_exit(1)
    assert ui.calls == []


def test_reply_tts_suppressed_while_live(monkeypatch):
    flow, native, _ui, _brain, _audio = make_flow(audio=live_audio_worker())
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(config, "TTS_SPEAK_REPLIES", True, raising=False)

    assert flow._tts_replies_enabled() is True
    native.emit("native.hotkey", {"kind": "voice_live"})
    assert flow._tts_replies_enabled() is False


def test_read_selection_aloud_refuses_while_live():
    flow, native, ui, _brain, _audio = make_flow(audio=live_audio_worker())
    native.emit("native.hotkey", {"kind": "voice_live"})

    flow.read_selection_aloud()

    assert any("live voice conversation" in text for text in notices(ui))
