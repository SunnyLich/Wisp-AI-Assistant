"""Tests for audio worker TTS synthesis and playback helpers."""

from __future__ import annotations

import shutil
import sys
import types
import wave
from pathlib import Path

import pytest

from runtime.workers import audio_host

np = pytest.importorskip("numpy")


@pytest.fixture(autouse=True)
def _run_log_dir(monkeypatch, request):
    """Keep generated audio files inside the test temp dir."""
    audio_host._audio_shutdown_requested.clear()
    root = Path.cwd() / ".pytest-audio-host" / request.node.name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(root))
    yield
    shutil.rmtree(root, ignore_errors=True)
    try:
        root.parent.rmdir()
    except OSError:
        pass
    audio_host._audio_shutdown_requested.clear()


def test_main_starts_ipc_before_loading_tts_stack(monkeypatch):
    """The supervisor ping must be available before slow local TTS imports."""
    import builtins

    calls: list[dict[str, object]] = []
    real_import = builtins.__import__

    def fake_run_host(**kwargs):
        calls.append(kwargs)
        return 0

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "config" or name == "core.tts" or (name == "core" and "tts" in fromlist):
            raise AssertionError(f"startup imported {name!r} before IPC host")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(audio_host, "run_host", fake_run_host)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert audio_host.main() == 0
    assert calls == [
        {
            "role": "audio",
            "handlers": audio_host.HANDLERS,
            "event_sink_setter": audio_host.set_event_sink,
            "shutdown_handler": audio_host.audio_shutdown,
            "threaded": True,
        }
    ]


def test_audio_shutdown_waits_for_native_warmup(monkeypatch):
    """Kokoro/Torch work must finish before the worker interpreter exits."""
    calls = []
    monkeypatch.setattr(audio_host, "audio_stop", lambda: calls.append("stop") or {"stopped": True})
    monkeypatch.setattr(audio_host, "_stop_live_session", lambda reason: calls.append(reason) or False)
    monkeypatch.setattr(
        audio_host,
        "_wait_for_warmups",
        lambda timeout: calls.append(timeout) or True,
    )

    result = audio_host.audio_shutdown()

    assert result == {"warmups_finished": True}
    assert calls == ["stop", "shutdown", audio_host._AUDIO_SHUTDOWN_WARMUP_TIMEOUT_SECONDS]


def test_record_start_reports_mic_open_failure_without_raising(monkeypatch):
    """Mic/device failures should be reported without killing the audio worker request."""
    from core.macos_helper import handlers as stt_handlers

    def fail_start():
        raise RuntimeError("PortAudio unavailable")

    monkeypatch.setattr(stt_handlers, "stt_start_recording", fail_start)

    result = audio_host.record_start()

    assert result == {"recording": False, "error": "RuntimeError: PortAudio unavailable"}


def test_tts_synthesize_uses_provider_pcm_format_for_kokoro(monkeypatch):
    """Kokoro streams int16 PCM, so the audio worker must not parse it as float32."""
    import config
    from core import tts

    samples = np.resize(np.array([0, 1200, -1200, 2400], dtype="<i2"), 24_000)
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(tts, "playback_format", lambda provider: (24_000, 1, "int16"))
    monkeypatch.setattr(
        tts,
        "stream_audio_from_chunks",
        lambda chunks, on_word_timestamps=None: iter([samples.tobytes()]),
    )

    result = audio_host.tts_synthesize("hello local voice")

    assert result["provider"] == "kokoro"
    assert result["sample_rate"] == 24_000
    assert result["bytes"] == samples.nbytes
    assert result["word_timestamps"]["words"] == []
    assert result["word_timestamps"]["estimated"] is False
    with wave.open(result["path"], "rb") as wf:
        assert wf.getframerate() == 24_000
        assert wf.readframes(wf.getnframes()) == samples.tobytes()


def test_tts_synthesize_provider_none_skips_tts_stream(monkeypatch):
    """Disabled TTS should return an empty WAV without loading the provider stack."""
    import config
    from core import tts

    monkeypatch.setattr(config, "TTS_PROVIDER", "none", raising=False)
    monkeypatch.setattr(
        tts,
        "stream_audio_from_chunks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected TTS stream")),
    )

    result = audio_host.tts_synthesize("hello with tts disabled")

    assert result["provider"] == "none"
    assert result["bytes"] == 0
    with wave.open(result["path"], "rb") as wf:
        assert wf.getnframes() == 0


def test_audio_prewarm_reports_local_warmup_events(monkeypatch):
    """Startup warmup should tell the supervisor when local audio is ready."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    events: list[tuple[str, dict]] = []
    stt_calls: list[bool] = []
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(audio_host, "_kokoro_prewarm_available", lambda: True)
    monkeypatch.setattr(stt_handlers, "stt_prewarm", lambda wait=False: stt_calls.append(wait))
    monkeypatch.setattr(tts, "prewarm", lambda: None)
    audio_host.set_event_sink(lambda name, data, _req_id: events.append((name, data)))

    result = audio_host.audio_prewarm()

    assert result == {"stt": "ok", "tts": "ok"}
    assert stt_calls == [True]
    assert events[0][0] == "audio.warmup.started"
    assert events[0][1]["items"] == ["stt", "tts"]
    assert events[0][1]["provider"] == "kokoro"
    assert events[0][1]["reason"] == "startup"
    assert events[0][1]["warmup_id"].startswith("startup:")
    assert events[-1][0] == "audio.warmup.done"
    assert events[-1][1]["ok"] is True


def test_audio_prewarm_reports_component_progress(monkeypatch):
    """Warmup progress should reveal whether STT or local TTS is still loading."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(audio_host, "_kokoro_prewarm_available", lambda: True)
    monkeypatch.setattr(stt_handlers, "stt_prewarm", lambda wait=False: None)
    monkeypatch.setattr(tts, "prewarm", lambda: None)
    audio_host.set_event_sink(lambda name, data, _req_id: events.append((name, data)))

    audio_host.audio_prewarm()

    progress = [(data["item"], data["status"]) for name, data in events if name == "audio.warmup.progress"]
    assert sorted(progress) == sorted([
        ("stt", "started"),
        ("stt", "ok"),
        ("tts", "started"),
        ("tts", "ok"),
    ])
    assert progress.index(("stt", "started")) < progress.index(("stt", "ok"))
    assert progress.index(("tts", "started")) < progress.index(("tts", "ok"))


def test_audio_prewarm_skips_missing_kokoro(monkeypatch):
    """Selecting Kokoro should not attempt TTS warmup until Kokoro is installed."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(audio_host, "_kokoro_prewarm_available", lambda: False)
    monkeypatch.setattr(stt_handlers, "stt_prewarm", lambda wait=False: None)
    monkeypatch.setattr(tts, "prewarm", lambda: (_ for _ in ()).throw(AssertionError("unexpected tts prewarm")))
    audio_host.set_event_sink(lambda name, data, _req_id: events.append((name, data)))

    result = audio_host.audio_prewarm()

    assert result == {"stt": "ok", "tts": "skipped"}
    assert events[0][0] == "audio.warmup.started"
    assert events[0][1]["items"] == ["stt"]
    assert events[0][1]["provider"] == "kokoro"
    assert events[0][1]["reason"] == "startup"
    assert events[0][1]["warmup_id"].startswith("startup:")
    assert events[-1][1]["ok"] is True


def test_audio_prewarm_warms_stt_and_tts_in_parallel(monkeypatch):
    """Local voice should start warming before STT finishes."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    calls: list[str] = []
    tts_started = audio_host.threading.Event()
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(config, "KOKORO_DEVICE", "cpu", raising=False)
    monkeypatch.setattr(config, "STT_DEVICE", "cpu", raising=False)
    monkeypatch.setattr(audio_host, "_kokoro_prewarm_available", lambda: True)

    def stt_prewarm(wait=False):
        calls.append(f"stt-start:{wait}")
        assert tts_started.wait(1.0)
        calls.append("stt-end")

    def tts_prewarm():
        calls.append("tts")
        tts_started.set()

    monkeypatch.setattr(stt_handlers, "stt_prewarm", stt_prewarm)
    monkeypatch.setattr(tts, "prewarm", tts_prewarm)

    result = audio_host.audio_prewarm()

    assert result == {"stt": "ok", "tts": "ok"}
    assert calls.index("tts") < calls.index("stt-end")


def test_audio_prewarm_serializes_kokoro_cuda_warmup(monkeypatch):
    """CUDA Kokoro warmup should not race STT GPU initialization."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    calls: list[str] = []
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(config, "KOKORO_DEVICE", "cuda", raising=False)
    monkeypatch.setattr(config, "STT_DEVICE", "auto", raising=False)
    monkeypatch.setattr(audio_host, "_kokoro_prewarm_available", lambda: True)
    monkeypatch.setattr(stt_handlers, "stt_prewarm", lambda wait=False: calls.append(f"stt:{wait}"))
    monkeypatch.setattr(tts, "prewarm", lambda: calls.append("tts"))

    result = audio_host.audio_prewarm()

    assert result == {"stt": "ok", "tts": "ok"}
    assert calls == ["tts", "stt:True"]


def test_audio_prewarm_warms_cartesia_tts(monkeypatch):
    """Cartesia has a connection prewarm even though it is not a local model."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    events: list[tuple[str, dict]] = []
    calls: list[str] = []
    monkeypatch.setattr(config, "TTS_PROVIDER", "cartesia", raising=False)
    monkeypatch.setattr(stt_handlers, "stt_prewarm", lambda wait=False: calls.append(f"stt:{wait}"))
    monkeypatch.setattr(tts, "prewarm", lambda: calls.append("tts"))
    audio_host.set_event_sink(lambda name, data, _req_id: events.append((name, data)))

    result = audio_host.audio_prewarm()

    assert result == {"stt": "ok", "tts": "ok"}
    assert sorted(calls) == ["stt:True", "tts"]
    assert events[0][0] == "audio.warmup.started"
    assert events[0][1]["items"] == ["stt", "tts"]
    assert events[0][1]["provider"] == "cartesia"
    assert events[0][1]["reason"] == "startup"
    assert events[0][1]["warmup_id"].startswith("startup:")
    assert events[-1][1]["ok"] is True


def test_tts_synthesize_raises_while_local_voice_is_warming(monkeypatch):
    """A user TTS request should not sit behind a stuck local warmup forever."""
    import config

    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    audio_host._set_local_tts_warmup(warming=True, ready=False)
    try:
        with pytest.raises(RuntimeError, match="still warming up"):
            audio_host.tts_synthesize("hello")
    finally:
        audio_host._set_local_tts_warmup(warming=False, ready=False)


def test_tts_synthesize_retries_directly_after_stale_local_warmup(monkeypatch):
    """A stale worker warmup flag should not block every local TTS request forever."""
    import config
    from core import tts

    samples = np.array([0, 1200, -1200, 2400], dtype="<i2")
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(audio_host, "_LOCAL_TTS_WARMUP_STALE_SECONDS", -1.0)
    monkeypatch.setattr(tts, "playback_format", lambda provider: (24_000, 1, "int16"))
    monkeypatch.setattr(
        tts,
        "stream_audio_from_chunks",
        lambda chunks, on_word_timestamps=None: iter([samples.tobytes()]),
    )
    audio_host._set_local_tts_warmup(warming=True, ready=False)
    try:
        result = audio_host.tts_synthesize("hello")
    finally:
        audio_host._set_local_tts_warmup(warming=False, ready=False)

    assert result["provider"] == "kokoro"
    assert result["bytes"] == samples.nbytes


def test_tts_synthesize_raises_when_provider_returns_no_audio(monkeypatch):
    """Provider failures should not be converted into silent success WAVs."""
    import config
    from core import tts

    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    audio_host._set_local_tts_warmup(warming=False, ready=True)
    monkeypatch.setattr(tts, "stream_audio_from_chunks", lambda *_args, **_kwargs: iter([]))
    monkeypatch.setattr(tts, "reset_connections", lambda: None)

    with pytest.raises(RuntimeError, match="returned no audio"):
        audio_host.tts_synthesize("hello")


def test_play_file_applies_tts_volume(monkeypatch):
    """The Settings volume slider applies to file-based worker playback too."""
    import config

    class FakeOutputStream:
        def __init__(self, *, samplerate, channels, dtype):
            self.samplerate = samplerate
            self.channels = channels
            self.dtype = dtype
            self.writes: list[np.ndarray] = []
            played["stream"] = self

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def write(self, data):
            self.writes.append(np.array(data, copy=True))

    played = {}
    fake_sd = types.SimpleNamespace(
        OutputStream=FakeOutputStream,
        stop=lambda: None,
    )
    fake_sf = types.SimpleNamespace(
        read=lambda path, dtype="float32": (np.array([0.8, -0.8], dtype=np.float32), 22_050)
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)
    monkeypatch.setattr(config, "TTS_VOLUME", 0.5, raising=False)

    result = audio_host.play_file("voice.wav")

    assert result == {"played": True, "stopped": False}
    assert played["stream"].samplerate == 22_050
    assert np.allclose(np.concatenate(played["stream"].writes), np.array([0.4, -0.4], dtype=np.float32))


def test_play_file_speed_boost_changes_mid_playback(monkeypatch):
    """File-based playback should honor bubble fast-forward while audio is already playing."""
    import config
    from core import audio_state

    audio_state.set_tts_speed_boost(False)
    monkeypatch.setattr(config, "TTS_VOLUME", 1.0, raising=False)
    monkeypatch.setattr(config, "TTS_PLAYBACK_RATE", 1.0, raising=False)
    monkeypatch.setattr(config, "TTS_HOLD_PLAYBACK_RATE", 2.0, raising=False)
    monkeypatch.setattr(audio_host, "_PLAYBACK_CHUNK_FRAMES", 4)
    source = np.arange(8, dtype=np.float32)
    writes: list[np.ndarray] = []

    class FakeOutputStream:
        def __init__(self, *, samplerate, channels, dtype):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def write(self, data):
            writes.append(np.array(data, copy=True))
            if len(writes) == 1:
                audio_host.audio_speed_boost(True)

    fake_sd = types.SimpleNamespace(OutputStream=FakeOutputStream, stop=lambda: None)
    fake_sf = types.SimpleNamespace(read=lambda path, dtype="float32": (source, 24_000))
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)

    try:
        result = audio_host.play_file("voice.wav")
    finally:
        audio_state.set_tts_speed_boost(False)

    assert result == {"played": True, "stopped": False}
    assert [len(chunk) for chunk in writes] == [4, 2]


def test_config_reload_schedules_prewarm_in_background(monkeypatch):
    """Settings Apply should not block while Kokoro or Whisper warms up."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    calls: list[str] = []
    threads: list[dict] = []

    class FakeThread:
        def __init__(self, *, target, args=(), daemon=False, name="") -> None:
            threads.append({"target": target, "args": args, "daemon": daemon, "name": name})

        def start(self) -> None:
            calls.append("thread.start")

    monkeypatch.setattr(config, "reload", lambda: calls.append("config.reload"))
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(tts, "reset_connections", lambda: calls.append("tts.reset_connections"))
    monkeypatch.setattr(stt_handlers, "stt_reset_model", lambda: calls.append("stt_reset_model"))
    monkeypatch.setattr(stt_handlers, "stt_prewarm", lambda: calls.append("stt_prewarm"))
    monkeypatch.setattr(tts, "prewarm", lambda: calls.append("tts.prewarm"))
    monkeypatch.setattr(audio_host.threading, "Thread", FakeThread)

    result = audio_host.audio_config_reload()

    assert result == {"ok": True, "tts_provider": "kokoro", "prewarm": "background"}
    assert calls == ["config.reload", "tts.reset_connections", "stt_reset_model", "thread.start"]
    assert threads[0]["name"] == "audio-config-reload-prewarm"
