"""
wisp_brain.host — the brain worker entry point. Run as::

    python -m wisp_brain.host

Reads newline-delimited JSON requests on stdin and writes responses/events on a
*private duplicate* of stdout; fd 1 is then redirected to stderr so any library
that prints to stdout (faster-whisper, tokenizers, ...) cannot corrupt the framed
protocol channel.

Unlike ``core.macos_helper.host`` (strictly sequential, fine for STT), the brain
must stream long replies and stay responsive to new/cancel requests meanwhile, so
each request is dispatched on its own daemon thread. Output is serialized by a
single write lock; the host (the supervisor-side client) correlates by ``id`` and
tolerates out-of-order responses.

Lifecycle:
  - ``__shutdown__``  -> ack and exit.
  - ``brain.cancel {"target": <id>}``  -> flag the target stream's context.
  - EOF on stdin (host closed the pipe)  -> exit.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

_EXPECTED_USER_ERROR_MARKERS = (
    "all query model routes failed",
    "all chat model routes failed",
    "all rewrite model routes failed",
    "all vision model routes failed",
    "usage_limit_reached",
    "usage limit",
    "rate limit",
    "rate_limit",
    "quota",
)


def _is_expected_user_error(exc: Exception) -> bool:
    """Return true for provider/user-state failures that should not spam stderr."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    type_name = type(exc).__name__.lower()
    if "ratelimit" in type_name:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _EXPECTED_USER_ERROR_MARKERS)


def _log_handler_error(method: Any, exc: Exception) -> None:
    """Log expected provider failures compactly, but keep tracebacks for bugs."""
    if _is_expected_user_error(exc):
        print(f"[brain] {method} failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return
    import traceback
    traceback.print_exc()


def _bootstrap_path() -> None:
    """Make ``core`` (repo root) and ``wisp_brain`` (runtime/brain) importable
    regardless of cwd, so the brain worker runs the same whether launched in-repo for
    tests or from the bundled runtime inside Wisp.app."""
    here = Path(__file__).resolve()
    brain_dir = here.parents[1]   # runtime/brain
    repo_root = here.parents[3]   # repo root (has core/, config.py)
    for p in (str(brain_dir), str(repo_root)):
        if p not in sys.path:
            sys.path.insert(0, p)


def _main() -> int:
    """Handle main for runtime brain wisp brain host."""
    _bootstrap_path()

    if os.getenv("WISP_BRAIN_FAULTHANDLER"):
        import faulthandler
        faulthandler.dump_traceback_later(15, exit=False)

    # Protect the protocol channel: keep a private binary handle to the *real*
    # stdout, then point fd 1 at stderr so stray prints don't land on the wire.
    real_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    stdin = sys.stdin.buffer

    from wisp_brain import protocol
    from wisp_brain.handlers import HANDLERS, STREAMING, StreamContext

    # Pre-warm native extensions on the MAIN thread. Every request below is
    # dispatched on its own daemon thread, and some C extensions -- notably numpy
    # on Python 3.14 -- deadlock when first imported from a worker thread. Doing
    # the first import here (best-effort) makes the first real audio/query request
    # safe. It stays dependency-free: if these aren't installed the brain worker still
    # boots and answers ``ping``/``brain.echo``.
    for _prewarm in ("numpy", "soundfile"):
        try:
            __import__(_prewarm)
        except Exception:  # noqa: BLE001 -- absence is fine; ping still works
            pass

    write_lock = threading.Lock()
    # Active streaming contexts, keyed by request id, for cooperative cancel.
    active: dict[Any, StreamContext] = {}
    active_lock = threading.Lock()

    def send(obj: dict) -> None:
        """Write a message to the host over the protocol pipe (thread-safe)."""
        with write_lock:
            try:
                protocol.write_message(real_out, obj)
            except (BrokenPipeError, ValueError, OSError):
                pass  # host went away; the read loop hits EOF and we exit

    def emit_event(event: str, data, req_id) -> None:
        """Emit event."""
        send(protocol.make_event(event, data=data, req_id=req_id))

    def respond(req_id, ok: bool, *, result=None, error=None) -> None:
        """Handle respond for runtime brain wisp brain host."""
        send(protocol.make_response(req_id, ok, result=result, error=error))

    def dispatch(req: dict) -> None:
        """Route an incoming request to its handler and send the response."""
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        fn = HANDLERS.get(method)
        if fn is None:
            respond(req_id, False, error=f"unknown method: {method!r}")
            return
        try:
            if method in STREAMING:
                ctx = StreamContext(emit_event, req_id)
                with active_lock:
                    active[req_id] = ctx
                try:
                    result = fn(ctx, **params)
                finally:
                    with active_lock:
                        active.pop(req_id, None)
            else:
                result = fn(**params)
            respond(req_id, True, result=result)
        except Exception as exc:  # noqa: BLE001 -- reported to host + stderr
            _log_handler_error(method, exc)
            respond(req_id, False, error=f"{type(exc).__name__}: {exc}")

    while True:
        req = protocol.read_message(stdin)
        if req is None:
            break  # host closed stdin

        method = req.get("method")

        if method == "__shutdown__":
            respond(req.get("id"), True, result=None)
            break

        if method == "brain.cancel":
            target = (req.get("params") or {}).get("target")
            with active_lock:
                ctx = active.get(target)
            if ctx is not None:
                ctx.cancelled = True
            respond(req.get("id"), True, result={"cancelled": ctx is not None})
            continue

        # Each request runs on its own thread so a long stream doesn't block new
        # requests (or their cancels). Responses are serialized by write_lock.
        threading.Thread(target=dispatch, args=(req,), daemon=True).start()

    try:
        from wisp_brain.handlers import run_addon_shutdown
        run_addon_shutdown()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(_main())

