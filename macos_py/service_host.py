"""Generic stdin/stdout JSON worker host for the pure-Python worker target."""

from __future__ import annotations

import inspect
import os
import sys
import threading
from collections.abc import Callable
from typing import Any

from macos_py import VERSION
from macos_py.boundaries import boundary_status
from macos_py.bootstrap import configure_paths
from macos_py import protocol

Handler = Callable[..., Any]
EventSinkSetter = Callable[[Callable[[str, Any, Any], None]], None]


def _protect_stdout():
    """Reserve original stdout for protocol messages and redirect prints to stderr."""
    real_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real_out


def _call_handler(fn: Handler, params: dict[str, Any]) -> Any:
    sig = inspect.signature(fn)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return fn(**params)
    accepted = {
        key: value
        for key, value in params.items()
        if key in sig.parameters
    }
    return fn(**accepted)


def run_host(
    *,
    role: str,
    handlers: dict[str, Handler],
    threaded: bool = False,
    event_sink_setter: EventSinkSetter | None = None,
    include_brain_path: bool = False,
) -> int:
    """Run a worker host until stdin closes or ``__shutdown__`` is requested."""
    root = configure_paths(include_brain=include_brain_path)
    real_out = _protect_stdout()
    stdin = sys.stdin.buffer
    write_lock = threading.Lock()
    shutting_down = threading.Event()

    def send(obj: dict[str, Any]) -> None:
        with write_lock:
            try:
                protocol.write_message(real_out, obj)
            except (BrokenPipeError, OSError, ValueError):
                shutting_down.set()

    def emit(event: str, data: Any = None, req_id: Any = None) -> None:
        send(protocol.make_event(event, data=data, req_id=req_id))

    if event_sink_setter is not None:
        event_sink_setter(emit)

    def ping(value: Any = None) -> dict[str, Any]:
        return {
            "pong": True,
            "value": value,
            "pid": os.getpid(),
            "role": role,
            "version": VERSION,
            "cwd": os.getcwd(),
            "repo_root": str(root),
            "boundary": boundary_status(role),
        }

    all_handlers = dict(handlers)
    all_handlers.setdefault("ping", ping)
    all_handlers.setdefault(f"{role}.ping", ping)
    all_handlers.setdefault("boundary.status", lambda: boundary_status(role))

    def respond(req_id: Any, ok: bool, *, result: Any = None, error: str | None = None) -> None:
        send(protocol.make_response(req_id, ok, result=result, error=error))

    def dispatch(req: dict[str, Any]) -> None:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        if not isinstance(params, dict):
            respond(req_id, False, error="params must be an object")
            return
        fn = all_handlers.get(str(method))
        if fn is None:
            respond(req_id, False, error=f"unknown method: {method!r}")
            return
        try:
            result = _call_handler(fn, params)
            respond(req_id, True, result=result)
        except Exception as exc:  # noqa: BLE001 - report over protocol
            import traceback

            traceback.print_exc()
            respond(req_id, False, error=f"{type(exc).__name__}: {exc}")

    while not shutting_down.is_set():
        req = protocol.read_message(stdin)
        if req is None:
            break
        if req.get("method") == "__shutdown__":
            respond(req.get("id"), True, result=None)
            break
        if threaded:
            threading.Thread(target=dispatch, args=(req,), daemon=True).start()
        else:
            dispatch(req)
    return 0
