"""wisp-audio worker: microphone, STT, TTS synthesis, and playback."""

from __future__ import annotations

import os
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from runtime.service_host import run_host

_emit: Callable[[str, Any, Any], None] | None = None
_playback_stop = threading.Event()
_live_lock = threading.Lock()
_live_session: Any = None  # core.live_voice.LiveVoiceSession | None (lazy import)
_config_prewarm_lock = threading.Lock()
_config_prewarm_generation = 0
_warmup_lock = threading.Lock()
_local_tts_warming = False
_local_tts_ready = False
_local_tts_error = ""
_local_tts_warmup_started_at: float | None = None
_LOCAL_TTS_WARMUP_STALE_SECONDS = 3600.0
_LOCAL_TTS_WARMUP_PROGRESS_SECONDS = 5.0
_PLAYBACK_CHUNK_FRAMES = 2048


def _local_tts_provider(provider: str) -> bool:
    """Return whether provider has an in-process local model warmup."""
    return provider == "kokoro"


def _prewarm_tts_provider(provider: str) -> bool:
    """Return whether provider has useful startup TTS work."""
    if provider == "cartesia":
        return True
    if provider == "kokoro":
        return _kokoro_prewarm_available()
    return False


def _kokoro_prewarm_available() -> bool:
    """Return whether Kokoro can be warmed in this process."""
    try:
        from core import tts

        return tts.kokoro_installed()
    except Exception:
        return False


def _serialize_audio_warmup(provider: str) -> bool:
    """Return whether local warmups should avoid concurrent native GPU init."""
    if provider != "kokoro":
        return False
    if not _kokoro_prewarm_available():
        return False
    try:
        import config

        kokoro_device = str(getattr(config, "KOKORO_DEVICE", "auto") or "auto").strip().lower()
        stt_device = str(getattr(config, "STT_DEVICE", "auto") or "auto").strip().lower()
    except Exception:
        return True
    return kokoro_device != "cpu" or stt_device != "cpu"


def _warmup_items(provider: str) -> list[str]:
    """Return local audio components that should warm up in this process."""
    items = ["stt"]
    if _prewarm_tts_provider(provider):
        items.append("tts")
    return items


def _set_local_tts_warmup(*, warming: bool, ready: bool | None = None, error: str = "") -> None:
    """Track whether local TTS is warming or ready."""
    global _local_tts_warming, _local_tts_ready, _local_tts_error, _local_tts_warmup_started_at
    with _warmup_lock:
        _local_tts_warming = warming
        _local_tts_warmup_started_at = time.monotonic() if warming else None
        if ready is not None:
            _local_tts_ready = ready
        _local_tts_error = error


def _local_tts_is_warming() -> bool:
    """Return whether a local TTS warmup is currently holding the model."""
    global _local_tts_warming, _local_tts_error, _local_tts_warmup_started_at
    with _warmup_lock:
        if not _local_tts_warming or _local_tts_ready:
            return False
        started_at = _local_tts_warmup_started_at
        if started_at is None:
            return True
        age = time.monotonic() - started_at
        if age <= _LOCAL_TTS_WARMUP_STALE_SECONDS:
            return True
        _local_tts_warming = False
        _local_tts_warmup_started_at = None
        _local_tts_error = f"Local TTS warmup exceeded {_LOCAL_TTS_WARMUP_STALE_SECONDS:.0f}s; synthesis will retry directly."
    print(f"[audio] {_local_tts_error}", flush=True)
    return False


def _warm_local_audio(
    provider: str,
    *,
    on_progress: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Warm local STT and local TTS, returning per-component status."""
    result: dict[str, Any] = {"stt": "skipped", "tts": "skipped"}
    result_lock = threading.Lock()

    def _set_result(item: str, status: str) -> None:
        with result_lock:
            result[item] = status

    def _warm_stt() -> None:
        if on_progress is not None:
            on_progress("stt", "started")
        try:
            from core.macos_helper import handlers as stt_handlers

            stt_handlers.stt_prewarm(wait=True)
            status = "ok"
        except Exception as exc:  # noqa: BLE001
            status = f"error: {type(exc).__name__}: {exc}"
        _set_result("stt", status)
        if on_progress is not None:
            on_progress("stt", status)

    def _warm_tts() -> None:
        is_local_tts = _local_tts_provider(provider)
        stop_progress = threading.Event()
        if is_local_tts:
            _set_local_tts_warmup(warming=True, ready=False)
        if on_progress is not None:
            on_progress("tts", "started")
            if is_local_tts:
                on_progress("tts", "preparing for 0s")

        def _progress_heartbeat() -> None:
            started = time.monotonic()
            while not stop_progress.wait(_LOCAL_TTS_WARMUP_PROGRESS_SECONDS):
                elapsed = int(time.monotonic() - started)
                if on_progress is not None:
                    on_progress("tts", f"preparing for {elapsed}s")

        if is_local_tts and on_progress is not None:
            threading.Thread(target=_progress_heartbeat, daemon=True, name="audio-tts-prewarm-progress").start()
        try:
            import config
            from core import tts

            if provider == "kokoro":
                message = (
                    "[audio] Kokoro warmup starting "
                    f"device={getattr(config, 'KOKORO_DEVICE', 'auto')!r} "
                    f"voice={getattr(config, 'KOKORO_VOICE', '')!r} "
                    f"lang={getattr(config, 'KOKORO_LANG_CODE', '')!r}"
                )
                print(message, flush=True)
                try:
                    from core import tts as _tts_diag

                    _tts_diag._kokoro_diag(message.removeprefix("[audio] "))
                except Exception:
                    pass

            tts.prewarm()
            status = "ok"
            if is_local_tts:
                _set_local_tts_warmup(warming=False, ready=True)
        except Exception as exc:  # noqa: BLE001
            status = f"error: {type(exc).__name__}: {exc}"
            if is_local_tts:
                _set_local_tts_warmup(warming=False, ready=False, error=status)
                try:
                    tts._kokoro_diag(f"Kokoro warmup failed: {status}")
                except Exception:
                    pass
        finally:
            stop_progress.set()
        _set_result("tts", status)
        if on_progress is not None:
            on_progress("tts", status)

    workers = [threading.Thread(target=_warm_stt, name="audio-stt-prewarm")]
    if _prewarm_tts_provider(provider):
        workers.append(threading.Thread(target=_warm_tts, name="audio-tts-prewarm"))
    else:
        _set_local_tts_warmup(warming=False, ready=False)
    if _serialize_audio_warmup(provider):
        print("[audio] serializing STT/TTS warmup to avoid concurrent GPU initialization", flush=True)
        ordered_workers = list(workers)
        if provider == "kokoro":
            ordered_workers.sort(key=lambda worker: 0 if worker.name == "audio-tts-prewarm" else 1)
        for worker in ordered_workers:
            worker.start()
            worker.join()
        return result
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    return result


def set_event_sink(fn: Callable[[str, Any, Any], None]) -> None:
    """Set event sink."""
    global _emit
    _emit = fn


def _event(name: str, data: Any = None) -> None:
    """Handle event for runtime workers audio host."""
    if _emit is not None:
        _emit(name, data, None)


def _output_dir() -> Path:
    """Handle output dir for runtime workers audio host."""
    root = os.environ.get("WISP_RUN_LOG_DIR")
    if root:
        out = Path(root).expanduser() / "audio"
    else:
        import tempfile

        out = Path(tempfile.gettempdir()) / "wisp-audio"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _prewarm_after_config_reload(generation: int, provider: str) -> None:
    """Warm STT/TTS after config reload without blocking the IPC response."""
    items = _warmup_items(provider)
    _event("audio.warmup.started", {"items": items, "provider": provider, "reason": "config_reload"})
    result = _warm_local_audio(
        provider,
        on_progress=lambda item, status: _event(
            "audio.warmup.progress",
            {"item": item, "status": status, "items": items, "provider": provider, "reason": "config_reload"},
        ),
    )
    with _config_prewarm_lock:
        stale = generation != _config_prewarm_generation
    suffix = " stale" if stale else ""
    ok = not any(str(value).startswith("error:") for value in result.values())
    _event(
        "audio.warmup.done",
        {"items": items, "provider": provider, "reason": "config_reload", "ok": ok, "result": result},
    )
    print(
        f"[audio] config reload background prewarm{suffix}: "
        f"tts_provider={provider!r} stt={result['stt']} tts={result['tts']}",
        flush=True,
    )


def audio_config_reload() -> dict[str, Any]:
    """Reload .env-backed config in the audio process after Settings → Apply.

    The audio worker is long-lived and owns the live TTS path (audio.tts.synthesize
    -> core.tts.stream_audio), so without this its config.TTS_PROVIDER and cached
    Cartesia WebSocket stay frozen at app-start values — the new provider/voice/key
    never takes effect until restart. Mirrors the desktop dialog's apply: reload
    config, drop cached TTS connections, then re-prewarm under the new settings.
    """
    global _config_prewarm_generation
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    # A running live voice session was built from the old config (model, voice,
    # API key); end it rather than keep it half-stale. Silent on the UI side.
    _stop_live_session("config_reload")
    config.reload()
    tts.reset_connections()
    stt_handlers.stt_reset_model()
    provider = config.TTS_PROVIDER.lower()
    with _config_prewarm_lock:
        _config_prewarm_generation += 1
        generation = _config_prewarm_generation
    threading.Thread(
        target=_prewarm_after_config_reload,
        args=(generation, provider),
        daemon=True,
        name="audio-config-reload-prewarm",
    ).start()
    print(f"[audio] config reloaded: tts_provider={provider!r} prewarm=background", flush=True)
    return {"ok": True, "tts_provider": provider, "prewarm": "background"}


def audio_prewarm() -> dict[str, Any]:
    """Warm audio/STT/TTS dependencies in the audio process only."""
    import config

    provider = config.TTS_PROVIDER.lower()
    items = _warmup_items(provider)
    _event("audio.warmup.started", {"items": items, "provider": provider, "reason": "startup"})
    result = _warm_local_audio(
        provider,
        on_progress=lambda item, status: _event(
            "audio.warmup.progress",
            {"item": item, "status": status, "items": items, "provider": provider, "reason": "startup"},
        ),
    )
    ok = not any(str(value).startswith("error:") for value in result.values())
    _event(
        "audio.warmup.done",
        {"items": items, "provider": provider, "reason": "startup", "ok": ok, "result": result},
    )
    return result


def record_start() -> dict[str, Any]:
    """Record start."""
    from core.macos_helper import handlers as stt_handlers

    try:
        stt_handlers.stt_start_recording()
    except Exception as exc:  # noqa: BLE001 - mic/device errors should not kill the worker request.
        return {"recording": False, "error": f"{type(exc).__name__}: {exc}"}
    _event("audio.recording.started", {})
    return {"recording": True}


def record_stop_transcribe() -> dict[str, Any]:
    """Record stop transcribe."""
    from core.macos_helper import handlers as stt_handlers

    text = stt_handlers.stt_stop_and_transcribe()
    _event("audio.transcribed", {"text": text})
    return {"text": text}


def stt_is_ready() -> dict[str, Any]:
    """Handle STT is ready for runtime workers audio host."""
    from core.macos_helper import handlers as stt_handlers

    return stt_handlers.stt_is_ready()


def _write_empty_wav(path: Path, *, sample_rate: int = 22_050) -> dict[str, Any]:
    """Write empty wav."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"")
    return {"path": str(path), "sample_rate": sample_rate, "bytes": 0, "provider": "none"}


def _current_tts_rate() -> float:
    """Return the current playback rate, including bubble fast-forward state."""
    import config
    from core import audio_state

    return audio_state.current_tts_rate(
        playback_rate=float(getattr(config, "TTS_PLAYBACK_RATE", 1.0) or 1.0),
        hold_playback_rate=float(getattr(config, "TTS_HOLD_PLAYBACK_RATE", 1.35) or 1.35),
    )


def _speed_adjust_float_audio(data, rate: float):
    """Return a copy of float audio resampled to play at the requested speed."""
    if abs(rate - 1.0) < 0.01 or getattr(data, "size", 0) < 2:
        return data
    import numpy as np

    samples = np.asarray(data, dtype=np.float32)
    mono = samples.ndim == 1
    if mono:
        samples = samples.reshape(-1, 1)
    frames = samples.shape[0]
    if frames < 2:
        return data
    out_frames = max(1, int(frames / rate))
    src_x = np.arange(frames, dtype=np.float32)
    dst_x = np.linspace(0, frames - 1, out_frames, dtype=np.float32)
    adjusted = np.empty((out_frames, samples.shape[1]), dtype=np.float32)
    for channel in range(samples.shape[1]):
        adjusted[:, channel] = np.interp(dst_x, src_x, samples[:, channel]).astype(np.float32)
    if mono:
        return adjusted[:, 0]
    return adjusted


def tts_synthesize(text: str = "", voice: str | None = None) -> dict[str, Any]:
    """Synthesize text into a WAV file and return its path."""
    if not text.strip():
        raise ValueError("text is required")
    if os.environ.get("WISP_BRAIN_FAKE_LLM"):
        path = _output_dir() / f"tts-{int(time.time() * 1000)}.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22_050)
            wf.writeframes(b"\x00\x00" * 1024)
        return {"path": str(path), "sample_rate": 22_050, "bytes": 2048, "provider": "fake"}

    import config

    provider = config.TTS_PROVIDER.lower()
    if _local_tts_provider(provider) and _local_tts_is_warming():
        raise RuntimeError("Local TTS is still warming up. Try again when local speech is ready.")
    print(f"[audio] tts.synthesize provider={provider!r} text={text[:40]!r}", flush=True)

    path = _output_dir() / f"tts-{int(time.time() * 1000)}.wav"
    if provider == "none":
        return _write_empty_wav(path)

    from core import tts

    def _synth() -> tuple[list[bytes], list[str], list[int]]:
        # Capture real provider word timestamps alongside the PCM so the UI can
        # lock the word highlight to the spoken voice. Providers without real
        # timestamps leave the lists empty; the bubble then uses normal reveal
        # speed instead of pretending to have audio-synced timings.
        """Handle synth for runtime workers audio host."""
        words: list[str] = []
        start_ms: list[int] = []

        def _collect_ts(ws: list, sms: list) -> None:
            """Handle collect ts for runtime workers audio host."""
            words.extend(ws)
            start_ms.extend(int(x) for x in sms)

        chunks = list(tts.stream_audio_from_chunks([text], on_word_timestamps=_collect_ts))
        return chunks, words, start_ms

    # The prewarmed Cartesia WebSocket can be stale/dead on the first prompt of a
    # session (idle since startup). It either raises (the socket is dropped by
    # _stream_cartesia) or returns no audio — both yield silence on the first try.
    # Retry once with a fresh connection so the first prompt isn't lost.
    expects_audio = provider not in ("none", "")
    try:
        chunks, words, start_ms = _synth()
        if expects_audio and not chunks:
            raise RuntimeError(f"{provider} returned no audio")
    except Exception as exc:  # noqa: BLE001
        if "warming up" in str(exc).lower():
            raise
        print(f"[audio] tts.synthesize first attempt failed ({type(exc).__name__}: {exc}); retrying fresh", flush=True)
        tts.reset_connections()
        try:
            chunks, words, start_ms = _synth()
        except Exception as exc2:  # noqa: BLE001 — surface as silence, not a crash
            print(f"[audio] tts.synthesize retry failed ({type(exc2).__name__}: {exc2})", flush=True)
            raise RuntimeError(f"{provider} TTS failed: {exc2}") from exc2
        if expects_audio and not chunks:
            raise RuntimeError(f"{provider} returned no audio") from exc

    if not chunks:
        return _write_empty_wav(path)

    sample_rate, _channels, dtype = tts.playback_format(provider)
    if dtype == "int16":
        pcm_i16 = b"".join(chunks)
    else:
        import numpy as np

        audio_f32 = np.frombuffer(b"".join(chunks), dtype=np.float32)
        audio_f32 = np.nan_to_num(audio_f32)
        audio_f32 = np.clip(audio_f32, -1.0, 1.0)
        pcm_i16 = (audio_f32 * 32767.0).astype("<i2").tobytes()

    word_timestamps = {"words": words, "start_ms": start_ms, "estimated": False}

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16)
    return {
        "path": str(path),
        "sample_rate": sample_rate,
        "bytes": len(pcm_i16),
        "provider": provider,
        "word_timestamps": word_timestamps,
    }


def play_file(path: str = "") -> dict[str, Any]:
    """Play a WAV file through sounddevice. Returns after playback completes."""
    if not path:
        raise ValueError("path is required")
    _playback_stop.clear()
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    import config

    data, sample_rate = sf.read(path, dtype="float32")
    data = np.asarray(data, dtype=np.float32)
    volume = max(0.0, min(2.0, float(getattr(config, "TTS_VOLUME", 1.0) or 1.0)))
    if volume != 1.0:
        data = np.clip(data * volume, -1.0, 1.0).astype("float32", copy=False)
    channels = 1 if data.ndim == 1 else int(data.shape[1])
    _event("audio.playback.started", {"path": path})
    try:
        with sd.OutputStream(samplerate=sample_rate, channels=channels, dtype="float32") as stream:
            total_frames = int(data.shape[0]) if getattr(data, "ndim", 0) else 0
            offset = 0
            while offset < total_frames and not _playback_stop.is_set():
                end = min(total_frames, offset + _PLAYBACK_CHUNK_FRAMES)
                chunk = data[offset:end]
                offset = end
                stream.write(_speed_adjust_float_audio(chunk, _current_tts_rate()))
    except KeyboardInterrupt:
        _playback_stop.set()
    if _playback_stop.is_set():
        sd.stop()
    _event("audio.playback.done", {"path": path, "stopped": _playback_stop.is_set()})
    return {"played": True, "stopped": _playback_stop.is_set()}


def audio_stop() -> dict[str, Any]:
    """Handle audio stop for runtime workers audio host."""
    _playback_stop.set()
    try:
        import sounddevice as sd

        sd.stop()
    except Exception:
        pass
    return {"stopped": True}


def audio_speed_boost(enabled: bool = False) -> dict[str, Any]:
    """Handle audio speed boost for runtime workers audio host."""
    from core import audio_state

    audio_state.set_tts_speed_boost(bool(enabled))
    return {"enabled": bool(enabled)}


def _stop_live_session(reason: str) -> bool:
    """Detach and stop the live voice session, if any. Returns whether one ran."""
    global _live_session
    with _live_lock:
        session = _live_session
        _live_session = None
    if session is None or not session.is_active:
        return False
    session.request_stop(reason)
    session.join(3.0)
    return True


def audio_live_start() -> dict[str, Any]:
    """Start a hands-free live voice conversation (Gemini Live).

    Returns quickly: the session connects on its own daemon thread and reports
    progress upstream via audio.live.* events. Structured errors (not raises)
    so the supervisor can map each one to a user-facing notice.
    """
    global _live_session
    from core import live_voice
    from core.macos_helper import handlers as stt_handlers

    if not live_voice.genai_available():
        return {"started": False, "error": "missing_package"}
    import config

    api_key = str(getattr(config, "GOOGLE_API_KEY", "") or "").strip()
    if not api_key:
        return {"started": False, "error": "missing_key"}
    if stt_handlers.stt_is_recording():
        return {"started": False, "error": "mic_busy"}
    with _live_lock:
        if _live_session is not None and _live_session.is_active:
            return {"started": False, "error": "already_active"}
        _playback_stop.set()  # live session owns the speaker now
        cfg = live_voice.LiveVoiceConfig(
            api_key=api_key,
            model=str(getattr(config, "LIVE_VOICE_MODEL", "") or live_voice.DEFAULT_LIVE_MODEL),
            voice_name=str(getattr(config, "LIVE_VOICE_VOICE_NAME", "") or ""),
            system_prompt=config.get_live_voice_system_prompt(),
            half_duplex=bool(getattr(config, "LIVE_VOICE_HALF_DUPLEX", False)),
        )
        session = live_voice.LiveVoiceSession(
            cfg, lambda name, payload: _event(f"audio.live.{name}", payload)
        )
        session.start()
        _live_session = session
    return {"started": True, "model": cfg.model}


def audio_live_stop() -> dict[str, Any]:
    """Stop the live voice session; no-op when none is running."""
    return {"stopped": _stop_live_session("user")}


def audio_live_status() -> dict[str, Any]:
    """Handle audio live status for runtime workers audio host."""
    with _live_lock:
        session = _live_session
    if session is None or not session.is_active:
        return {"active": False, "state": "idle", "model": ""}
    return {"active": True, "state": session.state, "model": session.cfg.model}


HANDLERS = {
    "audio.config.reload": audio_config_reload,
    "audio.prewarm": audio_prewarm,
    "audio.record.start": record_start,
    "audio.record.stop_transcribe": record_stop_transcribe,
    "audio.stt.is_ready": stt_is_ready,
    "audio.tts.synthesize": tts_synthesize,
    "audio.play_file": play_file,
    "audio.stop": audio_stop,
    "audio.speed_boost": audio_speed_boost,
    "audio.live.start": audio_live_start,
    "audio.live.stop": audio_live_stop,
    "audio.live.status": audio_live_status,
}


def _prewarm_native_extensions() -> None:
    """Import numpy/soundfile on the MAIN thread before any request threads exist.

    Warmups and requests run on daemon threads, and numpy's OpenBLAS can
    deadlock Windows' DLL loader when its extension is first loaded from a
    worker thread (observed as `from kokoro import KPipeline` hanging forever
    in `create_module`; the brain host documents the same failure). Import
    from the optional-packages path set so this loads the same numpy Kokoro
    resolves later. Best-effort: an incomplete install must not stop the
    worker from booting and answering ping.
    """
    try:
        from core import optional_deps

        optional_deps.add_optional_packages_to_path(prepend=True)
    except Exception:  # noqa: BLE001
        pass
    for name in ("numpy", "soundfile"):
        try:
            __import__(name)
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    """Handle main for runtime workers audio host."""
    _prewarm_native_extensions()
    return run_host(role="audio", handlers=HANDLERS, event_sink_setter=set_event_sink, threaded=True)


if __name__ == "__main__":
    raise SystemExit(main())
