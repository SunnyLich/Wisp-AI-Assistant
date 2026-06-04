"""
core/filler_bake.py — synthesise short filler clips ("Hmm...", "Let me think...")
in the user's chosen TTS voice and cache them on disk for play_filler().

Wired into the Settings -> Apply path so the user gets voice-matched filler
the next time they trigger the overlay, without any UI prompt.

Cache layout::

    USER_FILLER_AUDIO_DIR/
        .voice                       # text file; current voice id (Cartesia)
        wisp_filler_hmm.wav
        wisp_filler_let_me_think.wav
        ...

When the voice id in `.voice` no longer matches the configured voice, the
existing files are deleted and re-baked so the timbre stays consistent.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Iterable

import config
from core.system.native_locks import ssl_init_lock


# Short, neutral-content phrases. Kept under ~1s of speech (matches
# config.FILLER_MAX_DURATION_MS) and free of named entities so they fit any
# context. Edit cautiously: changing this list invalidates the cache silently.
FILLER_PHRASES: tuple[str, ...] = (
    "Hmm...",
    "Let me think...",
    "I wonder...",
    "Let's see...",
    "One moment...",
    "Okay...",
    "Just a second...",
    "Right...",
)

_FILE_PREFIX = "wisp_filler_"   # also used as the "ours to delete" marker
_VOICE_MARKER = ".voice"

_bake_lock = threading.Lock()


def _slug(phrase: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", phrase.lower()).strip("_")
    return s or "clip"


def _filename(phrase: str) -> str:
    return f"{_FILE_PREFIX}{_slug(phrase)}.wav"


def _read_voice_marker(dir_path: str) -> str:
    path = os.path.join(dir_path, _VOICE_MARKER)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_voice_marker(dir_path: str, voice_id: str) -> None:
    path = os.path.join(dir_path, _VOICE_MARKER)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(voice_id)
    except OSError:
        pass


def _clear_existing(dir_path: str) -> None:
    """Remove only our own previously-baked clips; leave anything else alone."""
    try:
        for name in os.listdir(dir_path):
            if name.startswith(_FILE_PREFIX) and name.lower().endswith(".wav"):
                try:
                    os.remove(os.path.join(dir_path, name))
                except OSError:
                    pass
    except OSError:
        pass


def _synthesise_cartesia(text: str, voice_id: str, api_key: str) -> bytes:
    """Call Cartesia synchronously and return raw pcm_f32le bytes at 44100 Hz.

    Uses a fresh WebSocket connection so this doesn't interfere with the
    long-lived singleton used for live TTS playback.
    """
    from cartesia import Cartesia  # type: ignore
    from core import tts as tts_module

    with ssl_init_lock():
        client = Cartesia(api_key=api_key)
        ws_manager = client.tts.websocket_connect()
        ws = ws_manager.__enter__()
    try:
        ctx = ws.context(
            model_id="sonic-3",
            voice={"mode": "id", "id": voice_id},
            output_format={
                "container": "raw",
                "encoding": "pcm_f32le",
                "sample_rate": tts_module.SAMPLE_RATE,
            },
            language="en",
            add_timestamps=False,
        )
        ctx.push(text)
        ctx.no_more_inputs()
        out = bytearray()
        for response in ctx.receive():
            if response.type == "chunk" and response.audio:
                out.extend(response.audio)
        return bytes(out)
    finally:
        try:
            ws_manager.__exit__(None, None, None)
        except Exception:
            pass


def _write_wav(pcm: bytes, out_path: str) -> None:
    """Persist pcm_f32le mono @ 44100Hz as a 16-bit WAV (smaller, universally
    decodable). soundfile handles the format conversion."""
    import numpy as np
    import soundfile as sf
    from core import tts as tts_module

    samples = np.frombuffer(pcm, dtype=np.float32)
    if samples.size == 0:
        raise RuntimeError("empty audio from TTS")
    # Trim long trailing silence so clips stay snappy. Threshold derived from
    # peak amplitude; if the whole clip is quiet (which shouldn't happen),
    # leave it alone.
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0.01:
        thresh = peak * 0.02
        nonzero = np.where(np.abs(samples) > thresh)[0]
        if nonzero.size:
            end = int(nonzero[-1]) + int(tts_module.SAMPLE_RATE * 0.15)  # +150ms tail
            samples = samples[: min(end, samples.size)]
    sf.write(out_path, samples, tts_module.SAMPLE_RATE, subtype="PCM_16")


def _is_current(dir_path: str, voice_id: str, phrases: Iterable[str]) -> bool:
    """All expected clips exist and the voice marker matches."""
    if _read_voice_marker(dir_path) != voice_id:
        return False
    for p in phrases:
        if not os.path.isfile(os.path.join(dir_path, _filename(p))):
            return False
    return True


def bake_filler_clips(
    *,
    phrases: Iterable[str] | None = None,
    force: bool = False,
) -> tuple[int, int]:
    """Bake any missing filler clips. Returns (baked, skipped).

    Silent: never raises, never prints user-visible UI, swallows API errors.
    Designed for fire-and-forget invocation from a background thread.

    `force=True` re-bakes everything regardless of the on-disk cache.
    """
    provider = (config.TTS_PROVIDER or "").lower().strip()
    if provider != "cartesia":
        # Only Cartesia is wired for now; ElevenLabs would need its own branch.
        return (0, 0)

    voice_id = (config.CARTESIA_VOICE_ID or "").strip()
    api_key = (config.CARTESIA_API_KEY or "").strip()
    if not voice_id or not api_key:
        return (0, 0)

    out_dir = config.USER_FILLER_AUDIO_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError:
        return (0, 0)

    phrases = tuple(phrases) if phrases is not None else FILLER_PHRASES

    # Serialise concurrent bakes (e.g. two rapid Apply clicks) so we don't
    # double-spend API credits.
    if not _bake_lock.acquire(blocking=False):
        return (0, 0)
    try:
        if not force and _is_current(out_dir, voice_id, phrases):
            return (0, len(phrases))

        if force or _read_voice_marker(out_dir) != voice_id:
            _clear_existing(out_dir)

        baked = skipped = 0
        for phrase in phrases:
            out_path = os.path.join(out_dir, _filename(phrase))
            if not force and os.path.isfile(out_path):
                skipped += 1
                continue
            try:
                pcm = _synthesise_cartesia(phrase, voice_id, api_key)
                _write_wav(pcm, out_path)
                baked += 1
            except Exception:
                # Silent: leave the missing clip alone, try again next Apply.
                continue

        if baked:
            _write_voice_marker(out_dir, voice_id)
            # Refresh the in-memory cache so the next play_filler() call sees
            # the new clips without restarting the app.
            try:
                from core import audio
                audio.prewarm_filler()
            except Exception:
                pass
        return (baked, skipped)
    finally:
        _bake_lock.release()


def bake_in_background(*, force: bool = False) -> None:
    """Fire-and-forget bake on a daemon thread. Safe to call from the UI."""
    threading.Thread(
        target=bake_filler_clips,
        kwargs={"force": force},
        daemon=True,
        name="filler-bake",
    ).start()
