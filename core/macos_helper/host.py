"""
core.macos_helper.host — the native-worker subprocess entry point.

Run as::

    python -m core.macos_helper.host

Reads newline-delimited JSON requests on stdin and writes responses/events on a
*private duplicate* of stdout. fd 1 is then redirected to stderr so that any
library that prints to stdout (faster-whisper, etc.) cannot corrupt the framed
protocol channel.

Requests are handled in order on this (main) thread. STT is sequential and
stateful, and ``stt.prewarm`` returns immediately (it loads in its own thread),
so nothing long-running blocks the loop. A request named ``__shutdown__`` exits
cleanly; EOF on stdin (parent closed the pipe) also exits.
"""
from __future__ import annotations

import os
import sys
import threading


def _main() -> int:
    # Protect the protocol channel: keep a private binary handle to the *real*
    # stdout, then point fd 1 at stderr so stray prints don't land on the wire.
    """Handle main for macos helper host."""
    real_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    stdin = sys.stdin.buffer

    from core.macos_helper import protocol
    from core.macos_helper.handlers import HANDLERS, set_event_sink

    write_lock = threading.Lock()

    def emit(obj: dict) -> None:
        """Write a message to the parent over the protocol pipe (thread-safe)."""
        with write_lock:
            try:
                protocol.write_message(real_out, obj)
            except (BrokenPipeError, ValueError):
                pass  # parent went away; the read loop will hit EOF and we exit

    set_event_sink(emit)

    def respond(req_id, ok: bool, *, result=None, error=None) -> None:
        """Handle respond for macos helper host."""
        msg = {"id": req_id, "ok": ok}
        if ok:
            msg["result"] = result
        else:
            msg["error"] = error
        emit(msg)

    while True:
        req = protocol.read_message(stdin)
        if req is None:
            break  # parent closed stdin

        req_id = req.get("id")
        method = req.get("method")

        if method == "__shutdown__":
            respond(req_id, True, result=None)
            break

        fn = HANDLERS.get(method)
        if fn is None:
            respond(req_id, False, error=f"unknown method: {method!r}")
            continue

        params = req.get("params") or {}
        try:
            result = fn(**params) if params else fn()
            respond(req_id, True, result=result)
        except Exception as exc:  # noqa: BLE001 — reported to parent + stderr
            import traceback
            traceback.print_exc()
            respond(req_id, False, error=f"{type(exc).__name__}: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(_main())
