"""
core.macos_helper.handlers — methods that execute INSIDE the worker process.

Each entry in ``HANDLERS`` maps a protocol ``method`` name to a callable. The
callable's return value becomes the response ``result``; raising turns into an
``error`` response. Handlers run on the worker's request-loop thread (its main
thread), in call order — which matches how the GUI drives them (prewarm, then
start_recording, then stop_and_transcribe).

Native dependencies (sounddevice, faster-whisper) are imported lazily inside the
handlers, never at module import, so the worker can boot and answer ``ping`` on
any platform (this is what lets the IPC harness be tested off-macOS).
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable

# Stray library prints are redirected to stderr by host.py, but keep HF quiet too.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

# Set by host.py so handlers can push unsolicited events to the parent.
_emit_event: Callable[[dict[str, Any]], None] | None = None


def set_event_sink(fn: Callable[[dict[str, Any]], None]) -> None:
    global _emit_event
    _emit_event = fn


def _emit(event: str, data: Any = None) -> None:
    if _emit_event is not None:
        _emit_event({"event": event, "data": data})


def _log(msg: str) -> None:
    print(f"[helper] {msg}", flush=True)  # → stderr (host redirects fd 1 to fd 2)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def ping(value: Any = None) -> dict[str, Any]:
    """Liveness/round-trip check. Echoes *value* and reports the worker pid."""
    return {"pong": True, "value": value, "pid": os.getpid()}


# ---------------------------------------------------------------------------
# STT — faster-whisper + microphone (sounddevice / CoreAudio)
# ---------------------------------------------------------------------------

_SAMPLE_RATE = 16_000  # Whisper expects 16 kHz mono

_model = None
_model_lock = threading.Lock()
_stream = None
_recording = False
_chunks: list = []
_chunks_lock = threading.Lock()


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            import config
            from faster_whisper import WhisperModel
            _model = WhisperModel(
                config.STT_MODEL,
                device="cpu",
                compute_type=config.STT_COMPUTE_TYPE,
            )
            _log(f"Whisper model '{config.STT_MODEL}' loaded.")
    return _model


def stt_prewarm() -> None:
    """Load the model in a background thread; return immediately so the request
    loop is not blocked by the (slow) first model load."""
    def _worker() -> None:
        try:
            _get_model()
        except Exception as exc:  # noqa: BLE001 — best effort, logged
            _log(f"prewarm skipped: {exc}")
    threading.Thread(target=_worker, daemon=True).start()
    return None


def _audio_callback(indata, frames, time_info, status) -> None:
    if _recording:
        with _chunks_lock:
            _chunks.append(indata.copy())


def stt_start_recording() -> None:
    """Open the mic and start buffering. The PortAudio/CoreAudio stream is opened
    on the worker's main thread (this request loop) — safe here because no Qt run
    loop owns this process."""
    global _stream, _recording
    import sounddevice as sd
    _recording = True
    with _chunks_lock:
        _chunks.clear()
    stream = sd.InputStream(
        samplerate=_SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=1024,
        callback=_audio_callback,
    )
    stream.start()
    _stream = stream
    _log("recording started")
    return None


def stt_stop_and_transcribe() -> str:
    """Stop recording and transcribe synchronously. Returns "" for empty/too-short
    clips. Blocks ~200–600 ms; the parent calls this from a worker thread."""
    global _stream, _recording
    import numpy as np
    import config
    _recording = False
    if _stream is not None:
        try:
            _stream.stop()
            _stream.close()
        except Exception:  # noqa: BLE001
            pass
        _stream = None

    with _chunks_lock:
        chunks = list(_chunks)
        _chunks.clear()
    if not chunks:
        return ""

    audio = np.concatenate(chunks, axis=0).flatten()
    if len(audio) < _SAMPLE_RATE * 0.3:  # ignore accidental sub-0.3 s taps
        return ""

    model = _get_model()
    segments, _info = model.transcribe(
        audio,
        beam_size=1,
        language=config.STT_LANGUAGE or None,
        vad_filter=True,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    _log(f"transcribed: {text!r}")
    return text


def stt_selftest(seconds: float = 1.0) -> dict[str, Any]:
    """Mic-free verification: load the model and transcribe a synthetic buffer.

    Proves faster-whisper/torch load and run *inside the worker* without any audio
    device or voice input. The transcript is expected to be empty (the buffer is
    near-silence) — we are checking the pipeline executes, not accuracy.
    """
    import time
    import numpy as np
    import config

    t0 = time.time()
    model = _get_model()
    load_seconds = time.time() - t0

    n = int(_SAMPLE_RATE * max(0.3, seconds))
    audio = (np.random.default_rng(0).standard_normal(n).astype("float32")) * 0.001

    t1 = time.time()
    segments, _info = model.transcribe(audio, beam_size=1, vad_filter=True)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return {
        "ok": True,
        "model": config.STT_MODEL,
        "text": text,
        "load_seconds": round(load_seconds, 2),
        "transcribe_seconds": round(time.time() - t1, 2),
        "pid": os.getpid(),
    }


def stt_mic_probe() -> dict[str, Any]:
    """Open then immediately close the mic InputStream to confirm CoreAudio in the
    worker does not segfault. Returns ``opened: False`` (no raise) if the machine
    has no input device, so a headless/remote Mac degrades gracefully."""
    import sounddevice as sd
    try:
        stream = sd.InputStream(
            samplerate=_SAMPLE_RATE, channels=1, dtype="float32", blocksize=1024
        )
        stream.start()
        stream.stop()
        stream.close()
        return {"opened": True, "error": None, "pid": os.getpid()}
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        return {"opened": False, "error": f"{type(exc).__name__}: {exc}", "pid": os.getpid()}


HANDLERS: dict[str, Callable[..., Any]] = {
    "ping": ping,
    "stt.prewarm": stt_prewarm,
    "stt.start_recording": stt_start_recording,
    "stt.stop_and_transcribe": stt_stop_and_transcribe,
    "stt.selftest": stt_selftest,
    "stt.mic_probe": stt_mic_probe,
}


__all__ = ["HANDLERS", "set_event_sink"]
