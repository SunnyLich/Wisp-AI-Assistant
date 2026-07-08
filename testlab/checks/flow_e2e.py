"""Simulated user, end to end: hotkey -> intent -> real LLM reply -> spoken TTS.

Drives the REAL FlowController against the REAL brain and audio worker
processes (real LLM route with the user's key; real Kokoro/cloud TTS through
the speakers) plus a scripted UI worker standing in for the windows - so every
assertion is made at the exact seam the real UI reads from.

Two user actions are simulated the way the app itself delivers them:
  1. caller hotkey -> intent picker shown -> intent chosen -> streamed reply
     chunks -> reply done -> reply spoken through the real audio device
  2. a chat window message -> streamed chat chunks -> chat done

By default the native worker is scripted too (canned selection/context): the
real one fires a synthetic Ctrl+C at whatever window YOU have focused, which a
background run must not do. Pass --real-desktop for an attended run that uses
the real native worker end to end.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _lab

_lab.bootstrap()

INTENT_PROMPT = "Reply with one short sentence saying the Wisp test lab hotkey flow works."
CHAT_PROMPT = "Reply with one short sentence saying the Wisp test lab chat flow works."

LAB_CALLER_ROW = {
    "name": "Testlab",
    "paste_back": False,
    "context_ambient": False,
    "context_documents_mode": "off",
    "context_browser_mode": "off",
    "context_memory_mode": "off",
    "context_screenshot": "off",
    "context_clipboard": False,
    "context_tools": False,
}


class ScriptedWorker:
    """Thread-safe stand-in for one worker: records calls, replays responses,
    lets the lab emit the events the real worker would emit."""

    def __init__(self, name: str, responses: dict[str, Any] | None = None) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._responses = dict(responses or {})
        self._handlers: dict[str, list[Callable[[Any, Any], None]]] = {}
        self.calls: list[dict[str, Any]] = []
        self._call_seen = threading.Condition(self._lock)

    # -- WorkerLike surface used by FlowController ----------------------
    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0, wait: bool = True) -> Any:
        record = {"method": method, "params": params or {}, "wait": wait}
        with self._call_seen:
            self.calls.append(record)
            self._call_seen.notify_all()
        response = self._responses.get(method, {})
        return response(params or {}) if callable(response) else response

    def call_with_events(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0, on_event=None, on_started=None) -> Any:
        return self.call(method, params)

    def on_event(self, event: str, handler: Callable[[Any, Any], None]) -> None:
        with self._lock:
            self._handlers.setdefault(event, []).append(handler)

    # -- lab controls ----------------------------------------------------
    def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            handlers = list(self._handlers.get(event, []))
        for handler in handlers:
            handler(data or {}, None)

    def calls_for(self, method: str) -> list[dict[str, Any]]:
        with self._lock:
            return [c for c in self.calls if c["method"] == method]

    def wait_for_call(
        self,
        method: str,
        timeout: float,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        with self._call_seen:
            while True:
                found = [
                    c
                    for c in self.calls
                    if c["method"] == method and (predicate is None or predicate(c["params"]))
                ]
                if found:
                    return found[-1]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._call_seen.wait(timeout=min(remaining, 0.5))


def _canned_context(params: dict[str, Any]) -> dict[str, Any]:
    result = {
        "selected_text": "def add(a, b): return a + b",
        "clipboard_text": "",
        "active_app": {"name": "Testlab Editor", "pid": 4242, "bundle_id": "dev.wisp.testlab"},
    }
    if params.get("include_selected_paths"):
        result["selected_paths"] = []
    if params.get("capture_focus"):
        result["focus_token"] = 0
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--real-desktop",
        action="store_true",
        help="use the real native worker (captures real selection/foreground app; attended runs only)",
    )
    parser.add_argument("--no-play", action="store_true", help="skip the spoken-reply (TTS) leg")
    args = parser.parse_args()

    import config
    from runtime.supervisor.flows import FlowController
    from runtime.supervisor.ipc import WorkerClient, WorkerSpec

    # Optional lab-only route pin (e.g. a free AI Studio model) - see llm_query.
    lab_provider = os.environ.get("WISP_TESTLAB_LLM_PROVIDER", "").strip()
    lab_model = os.environ.get("WISP_TESTLAB_LLM_MODEL", "").strip()
    provider = lab_provider or str(getattr(config, "LLM_PROVIDER", "") or "").strip()
    if not provider:
        return _lab.finish(_lab.SKIP, "no LLM provider configured")
    tts_provider = str(getattr(config, "TTS_PROVIDER", "none") or "none").strip().lower()
    tts_expected = tts_provider != "none" and not args.no_play

    # In-process config for this run: the lab caller row, and spoken replies on
    # so the TTS leg runs (their .env values are untouched on disk).
    config.CALLER_ROWS[:] = [dict(LAB_CALLER_ROW)]
    config.TTS_SPEAK_REPLIES = bool(tts_expected)

    isolated_root = _lab.isolated_repo_root("flow_e2e")
    route_env: dict[str, str] = {}
    if lab_provider and lab_model:
        # .env never overrides existing process env, so this pins the brain
        # worker's route (both the overlay and chat paths).
        route_env = {
            "LLM_PROVIDER": lab_provider,
            "LLM_MODEL": lab_model,
            "CHAT_LLM_PROVIDER": lab_provider,
            "CHAT_LLM_MODEL": lab_model,
        }
    overrides = _lab.env_overrides(isolated_root=isolated_root, extra=route_env)
    brain = WorkerClient(WorkerSpec("lab-brain", "runtime.workers.brain_host", "brain", env=overrides))
    audio = WorkerClient(
        WorkerSpec(
            "lab-audio",
            "runtime.workers.audio_host",
            "audio",
            env={**overrides, "WISP_MACOS_ENABLE_AUDIO": "1"},
        )
    )
    native: Any
    if args.real_desktop:
        native = WorkerClient(WorkerSpec("lab-native", "runtime.workers.native_host", "native", env=overrides))
        native_kind = "real native worker (--real-desktop)"
    else:
        native = ScriptedWorker(
            "native",
            {
                "native.context.snapshot": _canned_context,
                "native.hotkeys.start": {"started": True},
            },
        )
        native_kind = "scripted native (canned selection; --real-desktop for the real one)"
    ui = ScriptedWorker(
        "ui",
        {
            "ui.chat.begin_conversation": {"started": True, "conversation_index": 0},
        },
    )
    _lab.log(
        f"llm={provider}/{lab_model or getattr(config, 'LLM_MODEL', '')} "
        f"tts={tts_provider} native={native_kind}"
    )

    playback = {"started": threading.Event(), "done": threading.Event()}
    watch = _lab.Stopwatch()
    try:
        brain.call("brain.ping", {}, timeout=90.0)
        audio.call("audio.ping", {}, timeout=60.0)
        audio.on_event("audio.playback.started", lambda _d, _r: playback["started"].set())
        audio.on_event("audio.playback.done", lambda _d, _r: playback["done"].set())
        if tts_expected:
            _lab.log("prewarming audio worker (local TTS load)...")
            audio.call("audio.prewarm", {}, timeout=420.0)
        _lab.log(f"workers ready after {watch.lap()}s")

        flows = FlowController(native=native, ui=ui, brain=brain, audio=audio, run_async=True)
        flows.start()

        # ---- scenario 1: caller hotkey -> intent -> reply -> speech ----
        flows._on_native_hotkey({"kind": "caller", "index": 0})
        shown = ui.wait_for_call("ui.show_intent", timeout=60.0)
        if shown is None:
            return _lab.finish(_lab.FAIL, "intent picker was never shown after the caller hotkey")
        _lab.log(f"intent picker shown after {watch.lap()}s (caller_idx={shown['params'].get('caller_idx')})")

        ui.emit("ui.intent.chosen", {"custom": INTENT_PROMPT, "context_choices": []})
        done = ui.wait_for_call("ui.reply.done", timeout=300.0)
        if done is None:
            return _lab.finish(_lab.FAIL, "no ui.reply.done after choosing an intent (reply flow stalled)")
        chunks = [
            str(c["params"].get("text") or "")
            for c in ui.calls_for("ui.reply.chunk")
            if not c["params"].get("is_thought")
        ]
        reply_text = "".join(chunks).strip()
        _lab.log(f"hotkey reply after {watch.lap()}s: {len(chunks)} chunks: {ascii(reply_text[:160])}")
        if not reply_text:
            return _lab.finish(_lab.FAIL, "reply finished but no visible reply chunks reached the UI seam")

        tts_note = "tts: off"
        if tts_expected:
            if not playback["started"].wait(timeout=180.0):
                return _lab.finish(_lab.FAIL, "reply was never spoken: no audio.playback.started within 180s")
            if not playback["done"].wait(timeout=300.0):
                return _lab.finish(_lab.FAIL, "speech started but never finished (playback hang)")
            tts_note = f"spoken via {tts_provider}"
            _lab.log(f"reply spoken through the real device after {watch.lap()}s")

        # ---- scenario 2: chat message -> chat done ----------------------
        # The overlay flow above also emits ui.chat.done (for the transcript
        # window), so scenario 2 must match on its own request_id or it reads
        # scenario 1's stale call and races teardown against the live request.
        chat_request_id = "lab-chat-1"
        ui.emit(
            "ui.chat.request",
            {"request_id": chat_request_id, "messages": [{"role": "user", "content": CHAT_PROMPT}]},
        )
        chat_done = ui.wait_for_call(
            "ui.chat.done",
            timeout=300.0,
            predicate=lambda p: str(p.get("request_id") or "") == chat_request_id,
        )
        if chat_done is None:
            return _lab.finish(_lab.FAIL, "no ui.chat.done for the lab chat request within 300s")
        chat_chunks = [
            str(c["params"].get("text") or "")
            for c in ui.calls_for("ui.chat.chunk")
            if not c["params"].get("is_thought")
            and str(c["params"].get("request_id") or "") == chat_request_id
        ]
        chat_text = (str(chat_done["params"].get("text") or "") or "".join(chat_chunks)).strip()
        _lab.log(f"chat reply after {watch.lap()}s: {ascii(chat_text[:160])}")
        if not chat_text:
            return _lab.finish(_lab.FAIL, "chat finished but returned no text")

        return _lab.finish(
            _lab.PASS,
            f"hotkey+intent reply ({len(reply_text)} chars, {tts_note}) and chat reply "
            f"({len(chat_text)} chars) via real workers in {watch.lap()}s",
            reply_chars=len(reply_text),
            chat_chars=len(chat_text),
            tts=tts_note,
            native=native_kind,
            seconds=watch.lap(),
        )
    finally:
        for worker in (brain, audio, native):
            shutdown = getattr(worker, "shutdown", None)
            if callable(shutdown):
                shutdown()
        _lab.log("workers shut down")


if __name__ == "__main__":
    raise SystemExit(main())
