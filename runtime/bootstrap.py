"""Path bootstrap helpers for the pure-Python worker processes."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

_console_ctrl_suppressed = False


def suppress_console_ctrl_c() -> None:
    """Ignore console Ctrl+C (CTRL_C_EVENT) on Windows. Best-effort, idempotent.

    Wisp synthesizes Ctrl+C to copy the selected text (the clipboard fallback in
    ``core.capture``). When Wisp is launched from a console — e.g. double-clicking
    ``Start Wisp.bat`` — that injected Ctrl+C is delivered to the whole console
    process group as a CTRL_C_EVENT, which Python raises as KeyboardInterrupt and
    which kills the worker/supervisor processes, closing the app. Suppressing the
    handler stops the synthetic (and a stray real) Ctrl+C from tearing the app
    down; Wisp is quit via its UI/tray, not Ctrl+C. No-op off Windows.
    """
    global _console_ctrl_suppressed
    if _console_ctrl_suppressed or sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
        _console_ctrl_suppressed = True
    except Exception:
        pass


_crash_diagnostics_installed = False


def install_crash_diagnostics() -> None:
    """Enable native + Python crash diagnostics for this process. Idempotent.

    Installs diagnostics for the worker model, where each process's stderr is
    already captured to a per-worker ``.stderr.log`` by the supervisor:

    * faulthandler dumps an all-thread traceback on a fatal native signal
      (SIGSEGV/SIGABRT/SIGBUS/SIGILL/SIGTRAP). The worker split exists to contain
      crash-prone native code, so capturing *where* a worker died is the whole
      point — without this the supervisor only sees the worker vanish.
    * threading/sys excepthooks print otherwise-unhandled Python exceptions (from
      worker background threads or the main thread) to stderr so they reach the
      log instead of disappearing.

    Output goes to stderr (fd 2), which the supervisor mirrors to the log.
    Best-effort; never raises.
    """
    global _crash_diagnostics_installed
    if _crash_diagnostics_installed:
        return
    _crash_diagnostics_installed = True

    try:
        import faulthandler
        import signal as _signal

        faulthandler.enable(all_threads=True)
        for _name in ("SIGSEGV", "SIGABRT", "SIGBUS", "SIGILL", "SIGTRAP"):
            _sig = getattr(_signal, _name, None)
            if _sig is None:
                continue
            try:
                faulthandler.register(_sig, all_threads=True, chain=True)
            except (ValueError, OSError, RuntimeError):
                pass
    except Exception:
        pass

    import traceback as _traceback

    def _thread_hook(args) -> None:
        """Handle thread hook for runtime bootstrap."""
        if args.exc_type in (SystemExit, KeyboardInterrupt):
            return
        name = args.thread.name if args.thread else "<unknown>"
        exc_traceback = getattr(args, "exc_traceback", None) or getattr(args, "exc_tb", None)
        sys.stderr.write(
            f"[crash] unhandled exception in thread {name}:\n"
            + "".join(_traceback.format_exception(args.exc_type, args.exc_value, exc_traceback))
        )
        sys.stderr.flush()

    def _main_hook(exc_type, exc_value, exc_tb) -> None:
        """Handle main hook for runtime bootstrap."""
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        sys.stderr.write(
            "[crash] unhandled main-thread exception:\n"
            + "".join(_traceback.format_exception(exc_type, exc_value, exc_tb))
        )
        sys.stderr.flush()

    try:
        threading.excepthook = _thread_hook
        sys.excepthook = _main_hook
    except Exception:
        pass


def repo_root() -> Path:
    """Return the repository root, honoring bundled/dev overrides."""
    env_root = os.environ.get("WISP_REPO_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def data_root() -> Path:
    """Return the writable Wisp data root used by shared core modules."""
    try:
        from core.system.paths import REPO_ROOT

        return Path(REPO_ROOT)
    except Exception:
        return repo_root()


def brain_dir() -> Path:
    """Return the pure-Python brain package directory."""
    return repo_root() / "runtime" / "brain"


def configure_worker_logging() -> None:
    """Route worker app logs to stderr so the supervisor can aggregate them.

    Workers historically never configured logging, so their INFO records (for
    example the "bubble notice: ..." mirrors) were dropped by Python's
    lastResort WARNING-level handler and never reached the supervisor's
    runtime event log. Root stays at WARNING to keep third-party noise out of
    Runtime Status; wisp.* loggers emit INFO.
    """
    import logging

    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    logging.getLogger("wisp").setLevel(logging.INFO)


def configure_paths(*, include_brain: bool = False) -> Path:
    """Make shared repo modules importable and return the repo root."""
    # Every worker process starts here — guard them all against the synthetic
    # copy-Ctrl+C that would otherwise kill them when launched from a console,
    # and capture native/Python crash stacks into their per-worker log.
    suppress_console_ctrl_c()
    install_crash_diagnostics()
    configure_worker_logging()
    root = repo_root()
    paths = [root]
    if include_brain:
        paths.insert(0, brain_dir())
    for path in reversed(paths):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
    try:
        from core import optional_deps

        optional_deps.add_optional_packages_to_path()
    except Exception:
        pass
    return root
