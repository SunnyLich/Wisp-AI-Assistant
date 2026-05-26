"""
core/stt.py — Local speech-to-text via faster-whisper.

Push-to-talk flow:
  start_recording()      → opens sounddevice InputStream, accumulates float32 PCM
  stop_and_transcribe()  → closes stream, runs faster-whisper, returns text
  prewarm()              → loads model in background (avoids cold start on first use)

Requires: faster-whisper  (pip install faster-whisper)
Model is lazy-loaded on first use, or eagerly via prewarm().
"""
from __future__ import annotations

import os
import threading
import numpy as np
import sounddevice as sd
import config

# Suppress noisy HuggingFace Hub warnings before any faster-whisper import
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

SAMPLE_RATE = 16_000   # Whisper expects 16 kHz mono

_model      = None
_model_lock = threading.Lock()

_stream: sd.InputStream | None = None
_recording  = False
_chunks: list[np.ndarray] = []
_chunks_lock = threading.Lock()


# ------------------------------------------------------------------
# Model loading
# ------------------------------------------------------------------

def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(
                config.STT_MODEL,
                device="cpu",
                compute_type=config.STT_COMPUTE_TYPE,
            )
            print(f"[stt] Model '{config.STT_MODEL}' loaded.")
    return _model


def prewarm():
    """Load the Whisper model in a background thread to avoid cold start on first use."""
    def _worker() -> None:
        try:
            _get_model()
        except Exception as exc:
            print(f"[stt] prewarm skipped: {exc}")

    threading.Thread(target=_worker, daemon=True).start()


# ------------------------------------------------------------------
# Recording
# ------------------------------------------------------------------

def start_recording():
    """Open the microphone and start buffering audio. Call from any thread."""
    global _stream, _recording
    _recording = True
    with _chunks_lock:
        _chunks.clear()
    _stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=1024,
        callback=_audio_callback,
    )
    _stream.start()
    print("[stt] Recording started.")


def _audio_callback(indata: np.ndarray, frames: int, time, status):
    if _recording:
        with _chunks_lock:
            _chunks.append(indata.copy())


# ------------------------------------------------------------------
# Transcription
# ------------------------------------------------------------------

def stop_and_transcribe() -> str:
    """
    Stop recording and run transcription synchronously.
    Returns the transcribed string, or "" if nothing meaningful was captured.
    Blocks for ~200–600 ms depending on clip length and model size.
    """
    global _stream, _recording
    _recording = False
    if _stream is not None:
        try:
            _stream.stop()
            _stream.close()
        except Exception:
            pass
        _stream = None

    print("[stt] Recording stopped. Transcribing…")

    with _chunks_lock:
        chunks = list(_chunks)
        _chunks.clear()

    if not chunks:
        return ""

    audio = np.concatenate(chunks, axis=0).flatten()

    # Skip accidental taps shorter than 0.3 s
    if len(audio) < SAMPLE_RATE * 0.3:
        return ""

    model = _get_model()
    segments, _info = model.transcribe(
        audio,
        beam_size=1,
        language=config.STT_LANGUAGE or None,  # None = auto-detect
        vad_filter=True,                        # skip silent regions
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    print(f"[stt] Transcribed: {text!r}")
    return text
