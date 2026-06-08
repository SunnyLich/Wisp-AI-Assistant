"""wisp-audio worker: microphone, STT, TTS synthesis, and playback."""

from __future__ import annotations

import os
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable

from macos_py.service_host import run_host

_emit: Callable[[str, Any, Any], None] | None = None
_playback_stop = threading.Event()


def set_event_sink(fn: Callable[[str, Any, Any], None]) -> None:
    global _emit
    _emit = fn


def _event(name: str, data: Any = None) -> None:
    if _emit is not None:
        _emit(name, data, None)


def _output_dir() -> Path:
    root = os.environ.get("WISP_RUN_LOG_DIR")
    if root:
        out = Path(root).expanduser() / "audio"
    else:
        import tempfile

        out = Path(tempfile.gettempdir()) / "wisp-audio"
    out.mkdir(parents=True, exist_ok=True)
    return out


def audio_prewarm() -> dict[str, Any]:
    """Warm audio/STT/TTS dependencies in the audio process only."""
    result: dict[str, Any] = {"stt": "skipped", "tts": "skipped"}
    try:
        from core.macos_helper import handlers as stt_handlers

        stt_handlers.stt_prewarm()
        result["stt"] = "started"
    except Exception as exc:  # noqa: BLE001
        result["stt"] = f"error: {type(exc).__name__}: {exc}"
    try:
        from core import tts

        tts.prewarm()
        result["tts"] = "ok"
    except Exception as exc:  # noqa: BLE001
        result["tts"] = f"error: {type(exc).__name__}: {exc}"
    return result


def record_start() -> dict[str, Any]:
    from core.macos_helper import handlers as stt_handlers

    stt_handlers.stt_start_recording()
    _event("audio.recording.started", {})
    return {"recording": True}


def record_stop_transcribe() -> dict[str, Any]:
    from core.macos_helper import handlers as stt_handlers

    text = stt_handlers.stt_stop_and_transcribe()
    _event("audio.transcribed", {"text": text})
    return {"text": text}


def _write_empty_wav(path: Path, *, sample_rate: int = 22_050) -> dict[str, Any]:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"")
    return {"path": str(path), "sample_rate": sample_rate, "bytes": 0, "provider": "none"}


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

    import numpy as np
    import config
    from core import tts

    chunks = list(tts.stream_audio(text))
    path = _output_dir() / f"tts-{int(time.time() * 1000)}.wav"
    provider = config.TTS_PROVIDER.lower()
    if provider == "none" or not chunks:
        return _write_empty_wav(path)

    if provider == "elevenlabs":
        sample_rate = tts._EL_SAMPLE_RATE
        pcm_i16 = b"".join(chunks)
    else:
        sample_rate = tts.SAMPLE_RATE
        audio_f32 = np.frombuffer(b"".join(chunks), dtype=np.float32)
        audio_f32 = np.nan_to_num(audio_f32)
        audio_f32 = np.clip(audio_f32, -1.0, 1.0)
        pcm_i16 = (audio_f32 * 32767.0).astype("<i2").tobytes()

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16)
    return {"path": str(path), "sample_rate": sample_rate, "bytes": len(pcm_i16), "provider": provider}


def play_file(path: str = "") -> dict[str, Any]:
    """Play a WAV file through sounddevice. Returns after playback completes."""
    if not path:
        raise ValueError("path is required")
    _playback_stop.clear()
    import sounddevice as sd
    import soundfile as sf

    data, sample_rate = sf.read(path, dtype="float32")
    _event("audio.playback.started", {"path": path})
    sd.play(data, sample_rate)
    while not _playback_stop.is_set():
        try:
            sd.wait()
            break
        except KeyboardInterrupt:
            _playback_stop.set()
    if _playback_stop.is_set():
        sd.stop()
    _event("audio.playback.done", {"path": path, "stopped": _playback_stop.is_set()})
    return {"played": True, "stopped": _playback_stop.is_set()}


def audio_stop() -> dict[str, Any]:
    _playback_stop.set()
    try:
        import sounddevice as sd

        sd.stop()
    except Exception:
        pass
    return {"stopped": True}


HANDLERS = {
    "audio.prewarm": audio_prewarm,
    "audio.record.start": record_start,
    "audio.record.stop_transcribe": record_stop_transcribe,
    "audio.tts.synthesize": tts_synthesize,
    "audio.play_file": play_file,
    "audio.stop": audio_stop,
}


def main() -> int:
    return run_host(role="audio", handlers=HANDLERS, event_sink_setter=set_event_sink)


if __name__ == "__main__":
    raise SystemExit(main())

