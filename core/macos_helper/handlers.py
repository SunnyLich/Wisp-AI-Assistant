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
import re
import threading
from typing import Any, Callable

# Stray library prints are redirected to stderr by host.py, but keep HF quiet too.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

# Set by host.py so handlers can push unsolicited events to the parent.
_emit_event: Callable[[dict[str, Any]], None] | None = None


def set_event_sink(fn: Callable[[dict[str, Any]], None]) -> None:
    """Set event sink."""
    global _emit_event
    _emit_event = fn


def _emit(event: str, data: Any = None) -> None:
    """Forward an event dict to the registered event sink, if any."""
    if _emit_event is not None:
        _emit_event({"event": event, "data": data})


def _log(msg: str) -> None:
    """Print a helper log line to stderr."""
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
_STT_BG_FIRST_TRIGGER_SECONDS = 15.0
_STT_BG_STEP_SECONDS = 10.0
_STT_BG_LIVE_DELAY_SECONDS = 4.5
_STT_BG_OVERLAP_SECONDS = 1.0
_STT_BG_POLL_SECONDS = 0.2

_model = None
_model_ready = False  # True once the model is loaded AND warmed (first clip fast)
_model_lock = threading.Lock()
_recording_lock = threading.RLock()
_stream = None
_recording = False
_chunks: list = []
_chunks_lock = threading.Lock()
_stt_bg_thread: threading.Thread | None = None
_stt_bg_stop: threading.Event | None = None
_stt_bg_results: list[dict[str, Any]] = []
_stt_bg_lock = threading.Lock()


def _stt_bg_seconds(name: str, default: float, *, minimum: float = 0.0) -> float:
    """Return configurable background STT timing in seconds."""
    try:
        import config

        value = float(getattr(config, name, default))
    except Exception:
        value = default
    return max(minimum, value)


def _stt_bg_first_trigger_seconds() -> float:
    """Return the first background STT trigger time."""
    return _stt_bg_seconds("STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS", _STT_BG_FIRST_TRIGGER_SECONDS, minimum=1.0)


def _stt_bg_step_seconds() -> float:
    """Return the background STT cadence."""
    return _stt_bg_seconds("STT_BACKGROUND_CHUNK_STEP_SECONDS", _STT_BG_STEP_SECONDS, minimum=1.0)


def _stt_bg_live_delay_seconds() -> float:
    """Return how far behind live audio background STT should stay."""
    return _stt_bg_seconds("STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS", _STT_BG_LIVE_DELAY_SECONDS)


def _stt_bg_overlap_seconds() -> float:
    """Return overlap between background STT windows."""
    return _stt_bg_seconds("STT_BACKGROUND_CHUNK_OVERLAP_SECONDS", _STT_BG_OVERLAP_SECONDS)


def _get_model():
    """Return model."""
    global _model, _model_ready
    with _model_lock:
        if _model is None:
            import config
            from faster_whisper import WhisperModel
            from core.stt_device import resolve_device, resolve_compute_type, build_model
            device = resolve_device(config.STT_DEVICE, log=_log)
            compute_type = resolve_compute_type(device, config.STT_COMPUTE_TYPE, log=_log)
            _model, device, compute_type = build_model(
                WhisperModel, config.STT_MODEL, device, compute_type, log=_log
            )
            _model_ready = True
            _log(f"Whisper model '{config.STT_MODEL}' loaded on {device} ({compute_type}).")
    return _model


def stt_prewarm(wait: bool = False) -> None:
    """Load the model in a background thread; return immediately so the request
    loop is not blocked by the (slow) first model load."""
    if wait:
        _get_model()
        return None

    def _worker() -> None:
        """Handle worker for macos helper handlers."""
        try:
            _get_model()
        except Exception as exc:  # noqa: BLE001 — best effort, logged
            _log(f"prewarm skipped: {exc}")
    threading.Thread(target=_worker, daemon=True).start()
    return None


def stt_reset_model() -> None:
    """Drop the cached Whisper model after STT settings change."""
    global _model, _model_ready
    with _model_lock:
        _model = None
        _model_ready = False
    _log("Whisper model cache reset")
    return None


def stt_is_ready() -> dict[str, Any]:
    """Non-blocking readiness check: True once the model is loaded and warmed.

    Reads a flag only (never the model lock), so it answers instantly even while
    prewarm is still loading on its background thread — letting the GUI show a
    "warming up" indicator instead of a silent slow first transcription."""
    return {"ready": _model_ready}


def stt_is_recording() -> bool:
    """Return whether a hold-to-talk/dictation mic capture is currently open."""
    with _recording_lock:
        return _recording


def _audio_callback(indata, frames, time_info, status) -> None:
    """Handle audio callback for macos helper handlers."""
    if _recording:
        with _chunks_lock:
            _chunks.append(indata.copy())


def _sample_count(chunks: list[Any]) -> int:
    """Return total samples in captured mono chunks."""
    return sum(int(getattr(chunk, "shape", [len(chunk)])[0]) for chunk in chunks)


def _chunk_audio_slice(chunks: list[Any], start_sample: int, end_sample: int):
    """Copy one sample window out of the captured chunks."""
    import numpy as np

    if end_sample <= start_sample or not chunks:
        return np.array([], dtype="float32")
    audio = np.concatenate(chunks, axis=0).flatten()
    start = max(0, min(start_sample, len(audio)))
    end = max(start, min(end_sample, len(audio)))
    return audio[start:end].copy()


def _transcribe_audio(audio, *, label: str) -> str:
    """Normalize and transcribe one audio buffer."""
    import numpy as np
    import config
    from core.stt_postprocess import clean_transcript

    seconds = len(audio) / float(_SAMPLE_RATE)
    if len(audio) < _SAMPLE_RATE * 0.3:
        _log(f"transcribe skipped: {label} too short ({seconds:.2f}s)")
        return ""

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak < 0.01:
        _log(f"transcribe skipped: {label} input level too low (peak={peak:.4f})")
        return ""
    if peak < 0.3:
        audio = audio * (0.3 / peak)

    model = _get_model()
    language = config.STT_LANGUAGE or None
    beam_size = config.STT_BEAM_SIZE
    _log(f"transcribing {label} {seconds:.2f}s with language={language!r} beam_size={beam_size}")
    segments, _info = model.transcribe(
        audio,
        beam_size=beam_size,
        language=language,
        vad_filter=True,
    )
    raw_text = " ".join(seg.text.strip() for seg in segments).strip()
    text = clean_transcript(raw_text)
    if raw_text and not text:
        _log(f"discarded repeated-token transcript for {label}: {raw_text!r}")
    _log(f"transcribed {label}: {text!r}")
    return text


def _first_background_end_sample() -> int:
    """Return the first settled STT chunk end sample."""
    end_seconds = max(0.3, _stt_bg_first_trigger_seconds() - _stt_bg_live_delay_seconds())
    return int(end_seconds * _SAMPLE_RATE)


def _background_window_for_end(end_sample: int) -> tuple[int, int]:
    """Return the audio window for one background STT chunk end sample."""
    first_end = _first_background_end_sample()
    if end_sample <= first_end:
        return 0, end_sample
    overlap = int(_stt_bg_overlap_seconds() * _SAMPLE_RATE)
    step = int(_stt_bg_step_seconds() * _SAMPLE_RATE)
    return max(0, end_sample - step - overlap), end_sample


def _background_window_due(total_samples: int, end_sample: int) -> bool:
    """Return whether the requested background window is safely behind live audio."""
    delay = int(_stt_bg_live_delay_seconds() * _SAMPLE_RATE)
    return total_samples >= end_sample + delay


def _merge_transcript_parts(parts: list[str]) -> str:
    """Merge chunk transcripts, removing repeated words from overlap."""
    merged: list[str] = []

    def key(word: str) -> str:
        return re.sub(r"^\W+|\W+$", "", word).lower()

    for part in parts:
        words = [word for word in str(part or "").split() if word]
        if not words:
            continue
        if not merged:
            merged.extend(words)
            continue
        merged_keys = [key(word) for word in merged]
        word_keys = [key(word) for word in words]
        max_overlap = min(30, len(merged_keys), len(word_keys))
        overlap = 0
        for size in range(max_overlap, 0, -1):
            if merged_keys[-size:] == word_keys[:size]:
                overlap = size
                break
        merged.extend(words[overlap:])
    return " ".join(merged).strip()


def _snapshot_recording_window(start_sample: int, end_sample: int):
    """Copy a stable recording window while the mic continues capturing."""
    with _chunks_lock:
        return _chunk_audio_slice(list(_chunks), start_sample, end_sample)


def _stt_background_worker(stop_event: threading.Event) -> None:
    """Transcribe settled recording windows while capture continues."""
    next_end = _first_background_end_sample()
    step = int(_stt_bg_step_seconds() * _SAMPLE_RATE)
    while not stop_event.is_set():
        with _chunks_lock:
            total = _sample_count(_chunks)
        if not _background_window_due(total, next_end):
            stop_event.wait(_STT_BG_POLL_SECONDS)
            continue
        start, end = _background_window_for_end(next_end)
        audio = _snapshot_recording_window(start, end)
        try:
            text = _transcribe_audio(audio, label=f"background {start / _SAMPLE_RATE:.1f}-{end / _SAMPLE_RATE:.1f}s")
        except Exception as exc:  # noqa: BLE001 - final tail covers this failed window
            _log(f"background STT chunk failed: {type(exc).__name__}: {exc}")
            next_end += step
            continue
        if text:
            with _stt_bg_lock:
                _stt_bg_results.append({"start": start, "end": end, "text": text})
        next_end += step


def _start_background_stt() -> None:
    """Start background STT chunking for the current recording."""
    global _stt_bg_thread, _stt_bg_stop
    stop_event = threading.Event()
    with _stt_bg_lock:
        _stt_bg_results.clear()
    _stt_bg_stop = stop_event
    _stt_bg_thread = threading.Thread(
        target=_stt_background_worker,
        args=(stop_event,),
        daemon=True,
        name="wisp-stt-background",
    )
    _stt_bg_thread.start()


def _stop_background_stt() -> list[dict[str, Any]]:
    """Stop background STT and return completed chunk transcripts."""
    global _stt_bg_thread, _stt_bg_stop
    thread = _stt_bg_thread
    stop_event = _stt_bg_stop
    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join()
    _stt_bg_thread = None
    _stt_bg_stop = None
    with _stt_bg_lock:
        results = list(_stt_bg_results)
        _stt_bg_results.clear()
    return results


def stt_start_recording() -> None:
    """Open the mic and start buffering. The PortAudio/CoreAudio stream is opened
    on the worker's main thread (this request loop) — safe here because no Qt run
    loop owns this process."""
    global _stream, _recording
    import sounddevice as sd
    with _recording_lock:
        _stop_background_stt()
        if _stream is not None:
            try:
                _stream.stop()
                _stream.close()
            except Exception:  # noqa: BLE001
                pass
            _stream = None
        _recording = True
        with _chunks_lock:
            _chunks.clear()
        stream = None
        try:
            stream = sd.InputStream(
                samplerate=_SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=1024,
                callback=_audio_callback,
            )
            stream.start()
            _stream = stream
        except Exception:
            _recording = False
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            raise
        _start_background_stt()
    _log("recording started")
    return None


def stt_stop_and_transcribe() -> str:
    """Stop recording and transcribe synchronously. Returns "" for empty/too-short
    clips. Blocks ~200–600 ms; the parent calls this from a worker thread."""
    global _stream, _recording
    with _recording_lock:
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
        background_results = _stop_background_stt()
    if not chunks:
        _log("transcribe skipped: no audio chunks captured")
        return ""

    total_samples = _sample_count(chunks)
    if not background_results:
        return _transcribe_audio(
            _chunk_audio_slice(chunks, 0, total_samples),
            label="full clip",
        )

    background_results.sort(key=lambda item: int(item.get("start") or 0))
    last_end = max(int(item.get("end") or 0) for item in background_results)
    overlap = int(_stt_bg_overlap_seconds() * _SAMPLE_RATE)
    tail_start = max(0, last_end - overlap)
    tail_text = _transcribe_audio(
        _chunk_audio_slice(chunks, tail_start, total_samples),
        label=f"tail {tail_start / _SAMPLE_RATE:.1f}-{total_samples / _SAMPLE_RATE:.1f}s",
    )
    parts = [str(item.get("text") or "") for item in background_results]
    parts.append(tail_text)
    return _merge_transcript_parts(parts)



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
    "stt.reset_model": stt_reset_model,
    "stt.is_ready": stt_is_ready,
    "stt.start_recording": stt_start_recording,
    "stt.stop_and_transcribe": stt_stop_and_transcribe,
    "stt.selftest": stt_selftest,
    "stt.mic_probe": stt_mic_probe,
}


__all__ = ["HANDLERS", "set_event_sink"]
