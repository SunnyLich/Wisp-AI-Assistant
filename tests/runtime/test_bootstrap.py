from __future__ import annotations

import io
import sys
import threading
from types import SimpleNamespace


def test_thread_excepthook_accepts_python_312_traceback_attr(monkeypatch):
    """Crash diagnostics should use threading.ExceptHookArgs.exc_traceback."""
    from runtime import bootstrap

    original_thread_hook = threading.excepthook
    original_main_hook = sys.excepthook
    monkeypatch.setattr(bootstrap, "_crash_diagnostics_installed", False)
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    try:
        bootstrap.install_crash_diagnostics()
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            args = SimpleNamespace(
                exc_type=type(exc),
                exc_value=exc,
                exc_traceback=exc.__traceback__,
                thread=threading.current_thread(),
            )
            threading.excepthook(args)
    finally:
        threading.excepthook = original_thread_hook
        sys.excepthook = original_main_hook
        bootstrap._crash_diagnostics_installed = False

    output = stream.getvalue()
    assert "[crash] unhandled exception in thread" in output
    assert "RuntimeError: boom" in output
