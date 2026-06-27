"""
core/audio.py — Audio playback engine.

Streams TTS playback by playing PCM chunks from core.tts as they arrive.
"""
from __future__ import annotations

import queue
import threading

import numpy as np

import config
from core import audio_state
from core import tts as tts_module
from core.system import macos_safety
from core.system import main_thread as _main_thread


def _macos_audio_enabled() -> bool:
    """Return True when in-process macOS audio playback is explicitly enabled."""
    return macos_safety.audio_enabled()

_SD_IMPORT_ERROR: Exception | None = None
_sd_loaded = False


def _raise_sounddevice_unavailable() -> None:
    """Handle raise sounddevice unavailable for audio."""
    raise ModuleNotFoundError("sounddevice is required for audio playback") from _SD_IMPORT_ERROR


class _MissingRawOutputStream:
    """Model missing raw output stream."""
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        """Initialize the missing raw output stream instance."""
        _raise_sounddevice_unavailable()

    def __enter__(self):
        """Enter the context manager."""
        return self

    def __exit__(self, *exc):  # noqa: ANN002, ANN003
        """Exit the context manager."""
        return False


class _MissingSoundDevice:
    """Model missing sound device."""
    RawOutputStream = _MissingRawOutputStream

    class default:
        """Model default."""
        device = (None, None)

    @staticmethod
    def play(*args, **kwargs):  # noqa: ANN002, ANN003
        """Handle play for missing sound device."""
        _raise_sounddevice_unavailable()

    @staticmethod
    def wait() -> None:
        """Handle wait for missing sound device."""
        _raise_sounddevice_unavailable()

    @staticmethod
    def stop() -> None:
        """Raise: the sounddevice library is unavailable."""
        _raise_sounddevice_unavailable()

    @staticmethod
    def query_devices():  # noqa: ANN201
        """Query devices."""
        _raise_sounddevice_unavailable()


sd = _MissingSoundDevice()


def _load_sounddevice_if_allowed():
    """Load sounddevice if allowed."""
    global sd, _SD_IMPORT_ERROR, _sd_loaded
    if _sd_loaded:
        return sd
    if not _macos_audio_enabled():
        return sd
    try:
        import sounddevice as _sd
        sd = _sd
    except (ImportError, OSError) as exc:
        _SD_IMPORT_ERROR = exc
    finally:
        _sd_loaded = True
    return sd


if _macos_audio_enabled():
    _load_sounddevice_if_allowed()

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
    """Set tts speed boost."""
    audio_state.set_tts_speed_boost(enabled)


def _current_tts_rate() -> float:
    """Handle current TTS rate for audio."""
    return audio_state.current_tts_rate(
        playback_rate=config.TTS_PLAYBACK_RATE,
        hold_playback_rate=config.TTS_HOLD_PLAYBACK_RATE,
    )


# Length of the silence flushed after the last speech chunk so the trailing
# audio isn't clipped when the output stream is stopped. ~250ms is inaudible as
# a pause but comfortably longer than a typical device output buffer.
_TTS_TAIL_SILENCE_MS = 250


def _silence_tail(dtype: str, channels: int, sample_rate: int) -> bytes:
    """Return PCM silence of _TTS_TAIL_SILENCE_MS for the given output format."""
    frames = max(1, int(sample_rate * _TTS_TAIL_SILENCE_MS / 1000))
    np_dtype = np.float32 if dtype == "float32" else np.int16
    return np.zeros(frames * channels, dtype=np_dtype).tobytes()


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


def _volume_adjust_pcm(chunk: bytes, dtype: str, volume: float) -> bytes:
    """Apply a playback volume multiplier to raw PCM bytes."""
    if not chunk:
        return chunk
    try:
        volume = max(0.0, min(2.0, float(volume)))
    except Exception:
        volume = 1.0
    if abs(volume - 1.0) < 0.01:
        return chunk
    np_dtype = np.float32 if dtype == "float32" else np.int16
    samples = np.frombuffer(chunk, dtype=np_dtype)
    if samples.size == 0:
        return chunk
    if np_dtype is np.float32:
        adjusted = np.clip(samples.astype(np.float32) * volume, -1.0, 1.0).astype(np.float32)
    else:
        adjusted = np.clip(samples.astype(np.float32) * volume, -32768, 32767).astype(np.int16)
    return adjusted.tobytes()


# ------------------------------------------------------------------
# Streaming TTS playback
# ------------------------------------------------------------------

# Every native PortAudio handle must be opened and torn down on the GUI main
# thread; doing it on a worker thread segfaults inside CoreAudio while Qt's
# Cocoa run loop owns the process.
# set_main_thread_runner is re-exported so existing callers register the runner
# the same way; stt.py and macOS capture paths share the same runner.
_run_on_main = _main_thread.run_on_main
set_main_thread_runner = _main_thread.set_main_thread_runner


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
    """Stream and play chunks."""
    configured_provider = config.TTS_PROVIDER.lower()
    provider = configured_provider
    if not _macos_audio_enabled():
        provider = "none"
    if provider == "none":
        # No audio device should be opened in text-only mode. On macOS, even an
        # unused PortAudio stream can enter CoreAudio and crash under Qt/Cocoa.
        if configured_provider != "none" and on_audio_start:
            try:
                on_audio_start()
            except Exception:
                pass
        for _ in text_chunks:
            pass
        if on_done:
            on_done()
        return

    sd_mod = _load_sounddevice_if_allowed()

    chunk_q: queue.Queue[bytes | None] = queue.Queue()

    # Register this playback as the current one so stop() can abort it.
    global _current_stop_event
    stop_event = threading.Event()
    with _playback_lock:
        _current_stop_event = stop_event

    # Producer: fetch TTS audio chunks
    def producer():
        """Handle producer for audio."""
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
    # Derive playback params from the configured provider so that every provider
    # (ElevenLabs 22050 Hz int16, Cartesia 44100 Hz float32, OpenAI 24000 Hz
    # int16, …) plays correctly, regardless of which was used last.
    sample_rate, channels, dtype = tts_module.playback_format(provider)

    def _open_stream():
        """Open stream."""
        s = sd_mod.RawOutputStream(samplerate=sample_rate, channels=channels, dtype=dtype)
        s.start()  # RawOutputStream's context manager normally does this on __enter__
        return s

    def _close_stream(s):
        """Close stream."""
        try:
            s.stop()
        finally:
            s.close()

    _audio_started = False
    stream = None
    try:
        print(f"[audio] opening TTS stream (rate={sample_rate}, ch={channels}, dtype={dtype})", flush=True)
        # Open on the main thread (see _run_on_main): CoreAudio + Qt's run loop
        # segfault if the stream is opened on this worker thread. Writes below
        # stay on the worker so the UI never blocks during playback.
        stream = _run_on_main(_open_stream)
        print("[audio] TTS stream opened", flush=True)
        while True:
            if stop_event.is_set():
                # abort() tears down the PortAudio stream — keep it on the main
                # thread like open/close (writes below stay on this worker).
                _run_on_main(stream.abort)  # discard buffered audio immediately
                break
            try:
                chunk = chunk_q.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                # End of speech. PortAudio's blocking write returns once a chunk
                # is queued, not once it has been played, and stop()/close() in
                # _close_stream tears the stream down as soon as the final period
                # drains — on several host APIs that clips the last fraction of a
                # second, so words trail off mid-syllable. Push a short tail of
                # silence so the real speech is fully through the device buffer
                # before the stream stops. Skip when interrupted: a superseding
                # generation wants the stream gone now, not flushed.
                if _audio_started and not stop_event.is_set():
                    try:
                        stream.write(_silence_tail(dtype, channels, sample_rate))
                    except Exception:
                        pass
                break
            if not _audio_started:
                _audio_started = True
                print(f"[audio] writing first chunk ({len(chunk)} bytes)", flush=True)
                if on_audio_start:
                    try:
                        on_audio_start()
                    except Exception:
                        pass
            adjusted_chunk = _speed_adjust_pcm(chunk, dtype, _current_tts_rate())
            adjusted_chunk = _volume_adjust_pcm(
                adjusted_chunk,
                dtype,
                getattr(config, "TTS_VOLUME", 1.0),
            )
            stream.write(adjusted_chunk)
            if on_amplitude:
                try:
                    amp_dtype = np.float32 if dtype == "float32" else np.int16
                    samples = np.frombuffer(adjusted_chunk, dtype=amp_dtype)
                    amp = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                    on_amplitude(min(amp, 1.0))
                except Exception:
                    pass
    except ModuleNotFoundError as exc:
        print(f"[audio] streaming playback unavailable: {exc}")
    finally:
        if stream is not None:
            try:
                _run_on_main(lambda: _close_stream(stream))
            except Exception:
                pass

    with _playback_lock:
        if _current_stop_event is stop_event:
            _current_stop_event = None

    # Suppress the completion callback when interrupted — the superseding
    # generation owns the UI state now.
    if on_done and not stop_event.is_set():
        on_done()
