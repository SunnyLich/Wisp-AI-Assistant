"""
wisp_brain.handlers — methods that execute INSIDE the brain sidecar.

Each entry in ``HANDLERS`` maps a protocol ``method`` to a callable. Methods in
``STREAMING`` receive a ``StreamContext`` as their first positional argument and
may push ``reply.chunk``-style events (tagged with the request id) before they
return their final result; everything else is a plain unary call whose return
value becomes the response ``result``.

Heavy / OS-agnostic brain modules (``core.query_pipeline``,
``core.llm_clients.client``, faster-whisper, ...) are imported LAZILY inside the
handlers, never at module import, so the sidecar boots and can answer ``ping`` on
any platform with no API keys or models present. That is what lets this file be
tested from Windows/CI without the LLM stack.
"""
from __future__ import annotations

import os
import time
import wave
from pathlib import Path
from typing import Any, Callable

# Keep optional-dependency chatter off the protocol channel's stderr mirror.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

HANDLERS: dict[str, Callable[..., Any]] = {}
STREAMING: set[str] = set()
_STT_MODEL = None


class StreamContext:
    """Passed to streaming handlers; ``emit`` tags events with the request id so
    the host can route partial output back to the originating call."""

    __slots__ = ("_emit", "req_id", "cancelled")

    def __init__(self, emit: Callable[[str, Any, Any], None], req_id: Any) -> None:
        self._emit = emit          # (event_name, data, req_id) -> None
        self.req_id = req_id
        self.cancelled = False

    def emit(self, event: str, data: Any = None) -> None:
        self._emit(event, data, self.req_id)


def handler(name: str, *, streaming: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        HANDLERS[name] = fn
        if streaming:
            STREAMING.add(name)
        return fn
    return deco


def _log(msg: str) -> None:
    print(f"[brain] {msg}", flush=True)  # -> stderr (host redirects fd 1 to fd 2)


def _runtime_output_dir() -> Path:
    """Directory for large sidecar artifacts returned by path over IPC."""
    run_log_dir = os.getenv("WISP_RUN_LOG_DIR")
    if run_log_dir:
        out = Path(run_log_dir)
    else:
        import tempfile
        out = Path(tempfile.gettempdir()) / "wisp-brain"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# Diagnostics (no heavy imports -- always available)
# ---------------------------------------------------------------------------

@handler("ping")
def ping(value: Any = None) -> dict[str, Any]:
    """Liveness / round-trip check. Echoes *value* and reports the sidecar pid."""
    return {"pong": True, "value": value, "pid": os.getpid()}


@handler("brain.echo", streaming=True)
def brain_echo(ctx: StreamContext, text: str = "", chunk_size: int = 1, delay: float = 0.0) -> dict[str, Any]:
    """Stream *text* back word-by-word as ``reply.chunk`` events, then return the
    whole string. Pure-Python, no models or network -- this is the streaming
    handshake the Phase-1 test exercises to prove event correlation works."""
    words = text.split(" ") if text else []
    sent: list[str] = []
    for i in range(0, len(words), max(1, chunk_size)):
        if ctx.cancelled:
            break
        piece = " ".join(words[i:i + max(1, chunk_size)])
        if i + max(1, chunk_size) < len(words):
            piece += " "
        sent.append(piece)
        ctx.emit("reply.chunk", {"text": piece})
        if delay:
            time.sleep(delay)
    full = "".join(sent)
    ctx.emit("reply.done", {"text": full})
    return {"text": full}


# ---------------------------------------------------------------------------
# Audio model endpoints -- Swift owns CoreAudio; Python only reads/writes paths.
# ---------------------------------------------------------------------------

@handler("brain.transcribe")
def brain_transcribe(pcm_path: str = "", language: str | None = None) -> dict[str, Any]:
    """Transcribe a WAV/audio file recorded by Swift.

    This deliberately does NOT import ``core.stt`` because that module still owns
    legacy sounddevice recording. The native shell has already captured audio;
    the sidecar only loads faster-whisper and transcribes a normalized numpy
    array. Large PCM never crosses IPC, only ``pcm_path``.
    """
    if not pcm_path:
        raise ValueError("pcm_path is required")

    import numpy as np
    import soundfile as sf
    import config

    data, sample_rate = sf.read(pcm_path, dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    if sample_rate != 16_000:
        try:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(int(sample_rate), 16_000)
            data = resample_poly(data, 16_000 // g, int(sample_rate) // g).astype("float32")
        except Exception:
            # Linear fallback keeps the handler usable if scipy is missing.
            duration = len(data) / float(sample_rate)
            src_x = np.linspace(0.0, duration, num=len(data), endpoint=False)
            dst_len = max(1, int(duration * 16_000))
            dst_x = np.linspace(0.0, duration, num=dst_len, endpoint=False)
            data = np.interp(dst_x, src_x, data).astype("float32")

    if len(data) < 16_000 * 0.25:
        return {"text": "", "duration": len(data) / 16_000, "reason": "too_short"}

    global _STT_MODEL
    if _STT_MODEL is None:
        from faster_whisper import WhisperModel
        _STT_MODEL = WhisperModel(
            config.STT_MODEL,
            device="cpu",
            compute_type=config.STT_COMPUTE_TYPE,
        )
        _log(f"loaded STT model {config.STT_MODEL!r}")
    model = _STT_MODEL
    segments, _info = model.transcribe(
        data,
        beam_size=1,
        language=language or config.STT_LANGUAGE or None,
        vad_filter=True,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    _log(f"transcribed {pcm_path!r}: {text!r}")
    return {"text": text, "duration": len(data) / 16_000}


@handler("brain.tts.synthesize")
def brain_tts_synthesize(text: str = "", voice: str | None = None) -> dict[str, Any]:
    """Synthesize text to a standard int16 WAV file for Swift playback."""
    if not text.strip():
        raise ValueError("text is required")

    import numpy as np
    import config
    from core import tts

    chunks = list(tts.stream_audio(text))
    out_path = _runtime_output_dir() / f"tts-{int(time.time() * 1000)}.wav"

    provider = config.TTS_PROVIDER.lower()
    if provider == "none" or not chunks:
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22_050)
            wf.writeframes(b"")
        return {"path": str(out_path), "sample_rate": 22_050, "bytes": 0, "provider": provider}

    if provider == "elevenlabs":
        sample_rate = tts._EL_SAMPLE_RATE
        pcm_i16 = b"".join(chunks)
    else:
        sample_rate = tts.SAMPLE_RATE
        audio_f32 = np.frombuffer(b"".join(chunks), dtype=np.float32)
        audio_f32 = np.nan_to_num(audio_f32, copy=False)
        audio_f32 = np.clip(audio_f32, -1.0, 1.0)
        pcm_i16 = (audio_f32 * 32767.0).astype("<i2").tobytes()

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16)

    _log(f"tts synthesized {len(pcm_i16)} bytes -> {out_path}")
    return {"path": str(out_path), "sample_rate": sample_rate, "bytes": len(pcm_i16), "provider": provider}


# ---------------------------------------------------------------------------
# Real query path -- wired to the existing pipeline, exercised on the Mac / online.
# Imports are lazy so this module still loads with no LLM deps/keys present.
# ---------------------------------------------------------------------------

@handler("brain.query", streaming=True)
def brain_query(
    ctx: StreamContext,
    intent_prompt: str = "",
    selected: str | None = None,
    screenshot_b64: str | None = None,
    ambient_text: str = "",
    memory_context: str = "",
    use_tools: bool = False,
) -> dict[str, Any]:
    """Assemble context and stream an LLM reply, mirroring App._query_and_speak.

    Reuses the OS-agnostic brain verbatim: ``core.query_pipeline.build_context``
    for precedence rules and ``core.llm_clients.client.stream_response`` for the
    token stream. Each chunk becomes a ``reply.chunk`` event tagged with this
    request's id; the full text is the final response result.
    """
    from core.query_pipeline import ContextInputs, build_context
    from core.llm_clients.client import stream_response

    if not memory_context:
        try:
            from core.memory_store import store
            memory_context = store.get_manager().retrieve_relevant(intent_prompt) or ""
        except Exception as exc:  # memory should not block answering
            _log(f"memory retrieval skipped: {type(exc).__name__}: {exc}")

    built = build_context(
        ContextInputs(
            intent_prompt=intent_prompt,
            selected=selected,
            screenshot_b64=screenshot_b64,
            ambient_text=ambient_text,
        )
    )

    parts: list[str] = []
    for chunk in stream_response(
        built.user_message,
        image_base64=built.screenshot_b64,
        ambient_context=built.ambient_ctx,
        memory_context=memory_context,
        use_tools=use_tools,
    ):
        if ctx.cancelled:
            break
        parts.append(chunk)
        ctx.emit("reply.chunk", {"text": chunk})

    full = "".join(parts)
    ctx.emit("reply.done", {"text": full})
    return {"text": full}


@handler("brain.memory.add")
def brain_memory_add(text: str = "", category: str | None = None) -> dict[str, Any]:
    """Add a durable memory fact through the existing memory store."""
    fact = text.strip()
    if not fact:
        raise ValueError("text is required")

    from core.memory_store import store

    manager = store.get_manager()
    if category:
        manager.add_fact_manual(fact, category)
        used_category = category
    else:
        manager.add_explicit_fact(fact)
        used_category = "auto"
    return {"ok": True, "category": used_category, "text": fact}


@handler("brain.memory.search")
def brain_memory_search(query: str = "", top_k: int | None = None) -> dict[str, Any]:
    """Return the same memory block injected into LLM context."""
    if not query.strip():
        raise ValueError("query is required")

    from core.memory_store import store

    text = store.get_manager().retrieve_relevant(query, top_k=top_k) or ""
    return {"text": text}


__all__ = ["HANDLERS", "STREAMING", "StreamContext", "handler"]
