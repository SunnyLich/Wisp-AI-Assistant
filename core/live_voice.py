"""Live voice conversation session (Gemini Live API) for the audio worker.

One ``LiveVoiceSession`` is one hands-free conversation: the mic streams to
Gemini's Live websocket, returned PCM plays immediately, and the server's VAD
interrupts playback when the user talks over it (barge-in). The session runs
its own asyncio loop on a private daemon thread so it can live inside the
audio worker's threaded request/dispatch host; every external touchpoint
(``start`` / ``request_stop`` / ``join`` / ``is_active`` / ``state``) is
thread-safe and returns quickly.

Session output flows through ``emit(name, payload)`` with short event names —
``state`` / ``transcript`` / ``error`` / ``ended`` — which the audio worker
forwards upstream as ``audio.live.*`` IPC events.

``google-genai`` is an optional install and ``sounddevice`` opens real audio
devices, so neither is imported at module scope: the default ``connector``
and ``stream_factory`` import them lazily, and tests inject fakes to run the
whole session without either.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core import optional_deps

log = logging.getLogger("wisp.live_voice")

# Live API audio contract (validated by prototypes/voice_live).
SEND_RATE = 16000       # input:  16 kHz PCM16 mono
RECV_RATE = 24000       # output: 24 kHz PCM16 mono
BLOCK_MS = 20
SEND_BLOCK = SEND_RATE * BLOCK_MS // 1000
RECV_BLOCK = RECV_RATE * BLOCK_MS // 1000
HANGOVER_S = 0.3        # half-duplex: mic stays muted this long after playback

DEFAULT_LIVE_MODEL = "gemini-3.1-flash-live-preview"

EmitFn = Callable[[str, dict], None]


def genai_available() -> bool:
    """Return whether google-genai is importable (optional layer or base env).

    ``optional_deps.is_importable`` only sees top-level modules in the optional
    directory; google-genai lives at ``google/genai`` inside the shared
    ``google`` namespace package, so probe with a full find_spec instead.
    """
    optional_deps.add_optional_packages_to_path()
    try:
        return importlib.util.find_spec("google.genai") is not None
    except Exception:
        return False


class Playback:
    """Thread-safe PCM buffer drained by the speaker callback.

    ``clear()`` is the barge-in: dropping queued audio the instant the server
    reports an interruption is what makes talking over Wisp feel responsive.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._last_active = 0.0

    def feed(self, pcm: bytes) -> None:
        with self._lock:
            self._buf.extend(pcm)
            self._last_active = time.monotonic()

    def clear(self) -> int:
        with self._lock:
            dropped = len(self._buf)
            del self._buf[:]
            return dropped

    def pull(self, nbytes: int) -> bytes:
        with self._lock:
            chunk = bytes(self._buf[:nbytes])
            del self._buf[:len(chunk)]
            if chunk:
                self._last_active = time.monotonic()
        return chunk.ljust(nbytes, b"\x00")

    def active(self) -> bool:
        with self._lock:
            if self._buf:
                return True
            return (time.monotonic() - self._last_active) < HANGOVER_S


@dataclass(frozen=True)
class LiveVoiceConfig:
    """Immutable per-session settings snapshot."""
    api_key: str
    model: str = DEFAULT_LIVE_MODEL
    voice_name: str = ""
    system_prompt: str = ""
    half_duplex: bool = False
    mic_queue_max: int = 50


def _default_connector(cfg: LiveVoiceConfig) -> Any:
    """Return the real Gemini Live async context manager for this config."""
    optional_deps.add_optional_packages_to_path()
    from google import genai
    from google.genai import types

    live_config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription={},
        output_audio_transcription={},
    )
    if cfg.system_prompt:
        live_config.system_instruction = types.Content(
            parts=[types.Part(text=cfg.system_prompt)], role="user"
        )
    if cfg.voice_name:
        live_config.speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=cfg.voice_name
                )
            )
        )
    client = genai.Client(api_key=cfg.api_key)
    return client.aio.live.connect(model=cfg.model, config=live_config)


def _default_stream_factory(
    mic_callback: Callable, speaker_callback: Callable
) -> tuple[Any, Any]:
    """Return (mic, speaker) sounddevice streams; both are context managers."""
    import sounddevice as sd

    mic = sd.RawInputStream(
        samplerate=SEND_RATE, blocksize=SEND_BLOCK, channels=1,
        dtype="int16", callback=mic_callback,
    )
    speaker = sd.RawOutputStream(
        samplerate=RECV_RATE, blocksize=RECV_BLOCK, channels=1,
        dtype="int16", callback=speaker_callback,
    )
    return mic, speaker


class LiveVoiceSession:
    """One live conversation on a private asyncio-on-a-daemon-thread loop.

    ``ended {reason}`` is emitted exactly once, from the thread's final block:
    ``user``/``config_reload`` when stop was requested, ``error`` when the
    session died, ``server_closed`` when the server ended it (~15 min cap).
    """

    def __init__(
        self,
        cfg: LiveVoiceConfig,
        emit: EmitFn,
        *,
        connector: Callable[[LiveVoiceConfig], Any] | None = None,
        stream_factory: Callable[..., tuple[Any, Any]] | None = None,
    ) -> None:
        self._cfg = cfg
        self._emit_raw = emit
        self._connector = connector or _default_connector
        self._stream_factory = stream_factory or _default_stream_factory
        self._playback = Playback()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_async: asyncio.Event | None = None
        self._stop_requested = threading.Event()
        self._stop_reason = "user"
        self._state = "idle"
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public, thread-safe surface (called from IPC handler threads).
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("live voice session already started")
        self._thread = threading.Thread(
            target=self._thread_main, name="audio-live-session", daemon=True
        )
        self._thread.start()

    def request_stop(self, reason: str = "user") -> None:
        """Ask the session to end; idempotent, first reason wins."""
        if not self._stop_requested.is_set():
            self._stop_reason = reason
            self._stop_requested.set()
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._set_stop_async)
            except RuntimeError:
                pass  # loop already closed; the thread is exiting anyway

    def join(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    @property
    def cfg(self) -> LiveVoiceConfig:
        return self._cfg

    @property
    def is_active(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    # ------------------------------------------------------------------
    # Session internals (daemon thread + its asyncio loop).
    # ------------------------------------------------------------------
    def _set_stop_async(self) -> None:
        if self._stop_async is not None:
            self._stop_async.set()

    def _emit(self, name: str, payload: dict) -> None:
        if name == "state":
            with self._state_lock:
                self._state = str(payload.get("state", ""))
        try:
            self._emit_raw(name, payload)
        except Exception:
            log.exception("live voice emit failed: %s", name)

    def _thread_main(self) -> None:
        reason = "server_closed"
        try:
            asyncio.run(self._run())
        except Exception as exc:
            log.exception("live voice session failed")
            self._emit("error", {"code": "session_failed", "message": str(exc)})
            reason = "error"
        finally:
            if self._stop_requested.is_set():
                reason = self._stop_reason
            with self._state_lock:
                self._state = "idle"
            self._emit("ended", {"reason": reason})

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_async = asyncio.Event()
        if self._stop_requested.is_set():
            return
        mic_queue: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=self._cfg.mic_queue_max
        )
        self._emit("state", {"state": "connecting"})
        async with self._connector(self._cfg) as session:
            if self._stop_requested.is_set():
                return
            mic, speaker = self._stream_factory(
                self._make_mic_callback(mic_queue),
                self._make_speaker_callback(),
            )
            with mic, speaker:
                self._emit("state", {"state": "listening"})
                tasks = [
                    asyncio.create_task(self._sender(session, mic_queue)),
                    asyncio.create_task(self._receiver(session)),
                    asyncio.create_task(self._state_watcher()),
                    asyncio.create_task(self._stop_async.wait()),
                ]
                try:
                    done, _pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                if not self._stop_requested.is_set():
                    for task in done:
                        if task.cancelled():
                            continue
                        exc = task.exception()
                        if exc is not None:
                            raise exc

    def _make_mic_callback(self, mic_queue: asyncio.Queue) -> Callable:
        def on_mic(indata, frames, time_info, status) -> None:
            data = bytes(indata)
            loop = self._loop

            def push() -> None:
                if mic_queue.full():  # network stall: drop oldest, stay realtime
                    try:
                        mic_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                mic_queue.put_nowait(data)

            if loop is not None:
                try:
                    loop.call_soon_threadsafe(push)
                except RuntimeError:
                    pass  # loop shut down mid-callback

        return on_mic

    def _make_speaker_callback(self) -> Callable:
        playback = self._playback

        def on_speaker(outdata, frames, time_info, status) -> None:
            outdata[:] = playback.pull(len(outdata))

        return on_speaker

    async def _sender(self, session: Any, mic_queue: asyncio.Queue) -> None:
        mime = f"audio/pcm;rate={SEND_RATE}"
        half_duplex = self._cfg.half_duplex
        playback = self._playback
        while True:
            chunk = await mic_queue.get()
            if half_duplex and playback.active():
                continue  # mic is "muted" while Wisp is talking
            # Plain dict is a valid BlobDict for the real SDK and keeps this
            # module import-free of google-genai for injected fake sessions.
            await session.send_realtime_input(
                audio={"data": chunk, "mime_type": mime}
            )

    async def _receiver(self, session: Any) -> None:
        playback = self._playback
        while True:
            try:
                turn = session.receive()
                async for msg in turn:
                    self._handle_server_message(msg, playback)
            except Exception as exc:
                if type(exc).__name__ == "ConnectionClosedOK":
                    return  # server ended the session politely (~15 min cap)
                raise

    def _handle_server_message(self, msg: Any, playback: Playback) -> None:
        data = getattr(msg, "data", None)
        if data:
            playback.feed(data)
        if getattr(msg, "go_away", None) is not None:
            self._emit("error", {"code": "expiring", "message": ""})
        content = getattr(msg, "server_content", None)
        if content is None:
            return
        if getattr(content, "interrupted", None):
            playback.clear()
            self._emit("state", {"state": "listening"})
        for attr, role in (
            ("input_transcription", "user"),
            ("output_transcription", "assistant"),
        ):
            transcription = getattr(content, attr, None)
            text = getattr(transcription, "text", "") if transcription else ""
            if text:
                self._emit("transcript", {"role": role, "text": text})

    async def _state_watcher(self) -> None:
        """Flip speaking/listening off playback activity (drives the overlay)."""
        playback = self._playback
        speaking = False
        while True:
            await asyncio.sleep(0.1)
            active = playback.active()
            if active and not speaking:
                speaking = True
                self._emit("state", {"state": "speaking"})
            elif not active and speaking:
                speaking = False
                self._emit("state", {"state": "listening"})
