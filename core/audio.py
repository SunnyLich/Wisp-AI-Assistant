"""
core/audio.py — Audio playback engine.

Two responsibilities:
  1. Filler audio: instantly plays a random pre-cached WAV on hotkey press
     to mask LLM+TTS latency and provide sub-50ms acoustic feedback.
  2. Streaming TTS playback: plays PCM chunks from core.tts as they arrive.
"""
from __future__ import annotations
import os
import random
import threading
import queue
import sounddevice as sd
import soundfile as sf
import numpy as np
import config
from core import tts as tts_module

_tts_speed_boost = False
_tts_speed_lock = threading.Lock()

# Tracks the currently-playing TTS stream so stop() can abort it from any thread.
_playback_lock = threading.Lock()
_current_stop_event: threading.Event | None = None


def stop() -> None:
    """
    Immediately stop any in-progress streaming TTS playback and discard buffered
    audio. Safe to call from any thread; a no-op if nothing is playing.

    Used whenever a new generation supersedes the current one (a fresh query or
    voice capture) so the previous reply is cut off instead of talking over the
    new one.
    """
    with _playback_lock:
        ev = _current_stop_event
    if ev is not None:
        ev.set()


def set_tts_speed_boost(enabled: bool) -> None:
    """Called by the UI while the user holds the speech bubble."""
    global _tts_speed_boost
    with _tts_speed_lock:
        _tts_speed_boost = enabled


def _current_tts_rate() -> float:
    with _tts_speed_lock:
        boosted = _tts_speed_boost
    rate = config.TTS_HOLD_PLAYBACK_RATE if boosted else config.TTS_PLAYBACK_RATE
    return max(0.25, min(4.0, float(rate)))


def _speed_adjust_pcm(chunk: bytes, dtype: str, rate: float) -> bytes:
    """Change PCM playback speed by resampling chunk length before playback."""
    if abs(rate - 1.0) < 0.01 or not chunk:
        return chunk
    np_dtype = np.float32 if dtype == "float32" else np.int16
    samples = np.frombuffer(chunk, dtype=np_dtype)
    if samples.size < 2:
        return chunk
    out_len = max(1, int(samples.size / rate))
    src_x = np.arange(samples.size, dtype=np.float32)
    dst_x = np.linspace(0, samples.size - 1, out_len, dtype=np.float32)
    if np_dtype is np.float32:
        adjusted = np.interp(dst_x, src_x, samples).astype(np.float32)
    else:
        adjusted = np.interp(dst_x, src_x, samples.astype(np.float32))
        adjusted = np.clip(adjusted, -32768, 32767).astype(np.int16)
    return adjusted.tobytes()


# ------------------------------------------------------------------
# Filler audio
# ------------------------------------------------------------------

# Decoded filler clips kept in memory so the hotkey path does zero disk I/O.
_filler_clips: list[tuple[np.ndarray, int]] = []   # (samples, samplerate)
_filler_loaded = False


def prewarm_filler() -> None:
    """Decode all filler WAVs into memory once so play_filler() never touches
    disk on the latency-critical hotkey path. Safe to call repeatedly.

    Loads from both the bundled assets dir and the user-data dir (where
    TTS-baked voice-matched clips live), so a user with a Cartesia voice gets
    a mix of stock + voice-matched fillers."""
    global _filler_clips, _filler_loaded
    clips: list[tuple[np.ndarray, int]] = []
    dirs = [config.FILLER_AUDIO_DIR, getattr(config, "USER_FILLER_AUDIO_DIR", "")]
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.lower().endswith(".wav"):
                continue
            try:
                data, samplerate = sf.read(os.path.join(d, f), dtype="float32")
                clips.append((data, samplerate))
            except Exception as e:
                print(f"[audio] filler precache error for {f}: {e}")
    _filler_clips = clips
    _filler_loaded = True


def play_filler():
    """
    Play a random pre-decoded filler clip instantly (non-blocking).
    Safe to call from any thread.
    """
    if not _filler_loaded:
        prewarm_filler()
    if not _filler_clips:
        return  # no filler files available, skip silently

    data, samplerate = random.choice(_filler_clips)
    threading.Thread(target=_play_clip, args=(data, samplerate), daemon=True).start()


def _play_clip(data: np.ndarray, samplerate: int):
    try:
        sd.play(data, samplerate)
        sd.wait()
    except Exception as e:
        print(f"[audio] filler playback error: {e}")


# ------------------------------------------------------------------
# Streaming TTS playback
# ------------------------------------------------------------------

def play_tts_stream(text: str, on_done: callable | None = None):
    """
    Stream TTS for `text` and play it as chunks arrive.
    Non-blocking — runs in a daemon thread.
    """
    play_tts_stream_from_chunks(iter([text]), on_done=on_done)


def play_tts_stream_from_chunks(text_chunks, on_done: callable | None = None,
                                on_audio_start: callable | None = None,
                                on_word_timestamps: callable | None = None,
                                on_amplitude: callable | None = None):
    """
    Stream TTS from an iterable of text chunks (e.g. live LLM stream) and play.
    Non-blocking — runs in a daemon thread.

    Args:
        text_chunks: Iterable[str] of text pieces fed incrementally.
        on_done: Optional callback invoked when playback finishes.
        on_audio_start: Optional callback invoked when the first audio chunk
                        is written to the output stream (i.e. audio is audible).
        on_word_timestamps: Optional callback(words, start_ms) from Cartesia timestamps.
    """
    threading.Thread(
        target=_stream_and_play_chunks, args=(text_chunks, on_done, on_audio_start, on_word_timestamps, on_amplitude), daemon=True
    ).start()


def _stream_and_play_chunks(text_chunks, on_done: callable | None,
                            on_audio_start: callable | None,
                            on_word_timestamps: callable | None,
                            on_amplitude: callable | None):
    chunk_q: queue.Queue[bytes | None] = queue.Queue()

    # Register this playback as the current one so stop() can abort it.
    global _current_stop_event
    stop_event = threading.Event()
    with _playback_lock:
        _current_stop_event = stop_event

    # Producer: fetch TTS audio chunks
    def producer():
        try:
            for chunk in tts_module.stream_audio_from_chunks(text_chunks,
                                                              on_word_timestamps=on_word_timestamps):
                if stop_event.is_set():
                    break
                chunk_q.put(chunk)
        finally:
            chunk_q.put(None)  # sentinel

    threading.Thread(target=producer, daemon=True).start()

    # Consumer: feed chunks to sounddevice output stream
    # Derive playback params from the configured provider so that ElevenLabs
    # (22050 Hz int16) and Cartesia (44100 Hz float32) both play correctly,
    # regardless of which was used last.
    provider = config.TTS_PROVIDER.lower()
    if provider == "elevenlabs":
        sample_rate = tts_module._EL_SAMPLE_RATE
        channels    = tts_module.CHANNELS
        dtype       = tts_module._EL_DTYPE
    else:
        sample_rate = tts_module.SAMPLE_RATE
        channels    = tts_module.CHANNELS
        dtype       = tts_module.DTYPE

    _audio_started = False
    with sd.RawOutputStream(
        samplerate=sample_rate,
        channels=channels,
        dtype=dtype,
    ) as stream:
        while True:
            if stop_event.is_set():
                stream.abort()  # discard buffered audio immediately
                break
            try:
                chunk = chunk_q.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                break
            if not _audio_started:
                _audio_started = True
                if on_audio_start:
                    try:
                        on_audio_start()
                    except Exception:
                        pass
            adjusted_chunk = _speed_adjust_pcm(chunk, dtype, _current_tts_rate())
            stream.write(adjusted_chunk)
            if on_amplitude:
                try:
                    amp_dtype = np.float32 if dtype == "float32" else np.int16
                    samples = np.frombuffer(adjusted_chunk, dtype=amp_dtype)
                    amp = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                    on_amplitude(min(amp, 1.0))
                except Exception:
                    pass

    with _playback_lock:
        if _current_stop_event is stop_event:
            _current_stop_event = None

    # Suppress the completion callback when interrupted — the superseding
    # generation owns the UI state now.
    if on_done and not stop_event.is_set():
        on_done()
