"""
core.macos_helper.client — parent-side supervisor + transport for the worker.

``HelperClient`` lazily spawns ``python -m core.macos_helper.host``, ships
requests over its stdin, and reads responses/events off its stdout on a
background reader thread. If the worker dies, the next ``call`` respawns it and
in-flight calls fail fast rather than hang.

A single process-wide instance is exposed via ``get_client()``; the per-feature
shims (``stt_client`` etc.) call through it so callers keep their existing API.
"""
from __future__ import annotations

import atexit
import itertools
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from core.macos_helper import protocol

log = logging.getLogger("wisp.macos_helper")

# Repo root = .../<root>/core/macos_helper/client.py → parents[2]. Used as cwd so
# `-m core.macos_helper.host` resolves regardless of where the app was launched.
_REPO_ROOT = Path(__file__).resolve().parents[2]


class HelperError(RuntimeError):
    """A worker call failed, timed out, or the worker is unavailable."""


class HelperClient:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._spawn_lock = threading.Lock()   # serializes spawn/shutdown
        self._write_lock = threading.Lock()    # serializes writes to the pipe
        self._ids = itertools.count(1)
        self._pending: dict[int, dict[str, Any]] = {}
        self._pending_lock = threading.Lock()
        self._event_handlers: dict[str, list[Callable[[Any], None]]] = {}
        self._shutting_down = False
        atexit.register(self.shutdown)

    # -- lifecycle ---------------------------------------------------------

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _ensure_started(self) -> None:
        if self._alive():
            return
        with self._spawn_lock:
            if self._alive() or self._shutting_down:
                return
            self._spawn()

    def _spawn(self) -> None:
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        log.info("Spawning macOS helper worker")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "core.macos_helper.host"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_REPO_ROOT),
            env=env,
            bufsize=0,
        )
        threading.Thread(target=self._read_loop, args=(self._proc,), daemon=True).start()
        threading.Thread(target=self._stderr_loop, args=(self._proc,), daemon=True).start()

    def shutdown(self) -> None:
        with self._spawn_lock:
            self._shutting_down = True
            proc = self._proc
            self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            with self._write_lock:
                if proc.stdin and not proc.stdin.closed:
                    protocol.write_message(proc.stdin, {"id": 0, "method": "__shutdown__"})
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=2.0)
        except Exception:  # noqa: BLE001
            proc.kill()

    # -- reader threads ----------------------------------------------------

    def _read_loop(self, proc: subprocess.Popen) -> None:
        stdout = proc.stdout
        assert stdout is not None
        while True:
            msg = protocol.read_message(stdout)
            if msg is None:
                break  # worker closed stdout / exited
            if msg.get("event") is not None:
                self._dispatch_event(msg["event"], msg.get("data"))
                continue
            rid = msg.get("id")
            if rid is None:
                continue
            with self._pending_lock:
                slot = self._pending.pop(rid, None)
            if slot is not None:
                slot["resp"] = msg
                slot["event"].set()
        # Worker exited: fail every in-flight call so nobody hangs.
        with self._pending_lock:
            for slot in self._pending.values():
                slot["resp"] = {"ok": False, "error": "helper worker exited"}
                slot["event"].set()
            self._pending.clear()

    def _stderr_loop(self, proc: subprocess.Popen) -> None:
        stderr = proc.stderr
        assert stderr is not None
        for raw in iter(stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                log.debug("[worker] %s", line)

    def _dispatch_event(self, event: str, data: Any) -> None:
        for handler in self._event_handlers.get(event, ()):  # snapshot via .get
            try:
                handler(data)
            except Exception:  # noqa: BLE001
                log.exception("event handler for %r failed", event)

    def on_event(self, event: str, handler: Callable[[Any], None]) -> None:
        self._event_handlers.setdefault(event, []).append(handler)

    # -- requests ----------------------------------------------------------

    def _write(self, req: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise HelperError("helper worker not running")
        with self._write_lock:
            try:
                protocol.write_message(proc.stdin, req)
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise HelperError(f"helper worker write failed: {exc}") from exc

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        wait: bool = True,
    ) -> Any:
        """Send *method* to the worker. When ``wait`` is False, fire-and-forget
        (returns None immediately). Otherwise block up to *timeout* for the
        response and return its ``result``; raise ``HelperError`` on failure."""
        self._ensure_started()
        rid = next(self._ids)
        req = {"id": rid, "method": method, "params": params or {}}

        if not wait:
            self._write(req)
            return None

        ev = threading.Event()
        slot: dict[str, Any] = {"event": ev, "resp": None}
        with self._pending_lock:
            self._pending[rid] = slot
        try:
            self._write(req)
        except HelperError:
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise

        if not ev.wait(timeout):
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise HelperError(f"helper call {method!r} timed out after {timeout:.0f}s")

        resp = slot["resp"] or {"ok": False, "error": "no response"}
        if not resp.get("ok"):
            raise HelperError(resp.get("error") or f"helper call {method!r} failed")
        return resp.get("result")


_client: HelperClient | None = None
_client_lock = threading.Lock()


def get_client() -> HelperClient:
    """Return the process-wide helper client (created on first use)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = HelperClient()
        return _client
