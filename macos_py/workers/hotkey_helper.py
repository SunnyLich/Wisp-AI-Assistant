"""Standalone Carbon hotkey event loop for the Mac Python target.

The native worker is a stdin/stdout service and cannot block its main thread in
Carbon's event loop. This helper owns that loop in a tiny child process and
streams hotkey events back to the native worker as newline-delimited JSON.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from typing import Any

from macos_py.bootstrap import configure_paths
from macos_py import protocol


def _protect_stdout():
    real_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real_out


def _run_carbon_loop(stop: threading.Event) -> None:
    import ctypes
    import ctypes.util

    carbon = ctypes.CDLL(ctypes.util.find_library("Carbon") or "Carbon")
    run_current = carbon.RunCurrentEventLoop
    run_current.argtypes = [ctypes.c_double]
    run_current.restype = ctypes.c_int32
    while not stop.is_set():
        run_current(0.25)


def main() -> int:
    configure_paths()
    out = _protect_stdout()
    write_lock = threading.Lock()
    stop = threading.Event()

    def send(obj: dict[str, Any]) -> None:
        with write_lock:
            protocol.write_message(out, obj)

    def emit_hotkey(kind: str, **extra: Any) -> None:
        data = {"kind": kind, **extra}
        send({"event": "native.hotkey", "data": data})

    def request_stop(_signum=None, _frame=None) -> None:
        stop.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            signal.signal(sig, request_stop)

    try:
        import config
        from core.hotkeys import HotkeyListener

        caller_count = len(getattr(config, "CALLER_ROWS", []))
        callers = [
            (lambda idx=idx: emit_hotkey("caller", index=idx))
            for idx in range(caller_count)
        ]
        listener = HotkeyListener(
            on_callers=callers,
            on_add_context=lambda: emit_hotkey("add_context"),
            on_clear_context=lambda: emit_hotkey("clear_context"),
            on_snip=lambda: emit_hotkey("snip"),
            on_voice_start=lambda: emit_hotkey("voice_start"),
            on_voice_stop=lambda: emit_hotkey("voice_stop"),
        )
        started = bool(listener.start())
        send(
            {
                "status": "started" if started else "failed",
                "started": started,
                "backend": "carbon-helper",
            }
        )
        if not started:
            return 1
        _run_carbon_loop(stop)
        listener.stop()
        return 0
    except Exception as exc:  # noqa: BLE001 - report startup failure to parent
        import traceback

        traceback.print_exc()
        send(
            {
                "status": "failed",
                "started": False,
                "backend": "carbon-helper",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
