"""Pure-Python app supervisor entrypoint."""

from __future__ import annotations

import logging
import os
import runpy
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

from core.system import single_instance
from runtime.bootstrap import (
    install_crash_diagnostics,
    repo_root,
    suppress_console_ctrl_c,
)
from runtime.supervisor.flows import FlowController
from runtime.supervisor.ipc import WispSupervisor


def _dispatch_module_mode() -> None:
    """Let a frozen supervisor executable emulate ``python -m module`` workers."""
    if len(sys.argv) >= 3 and sys.argv[1] == "-m":
        module = sys.argv[2]
        sys.argv = [module, *sys.argv[3:]]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        raise SystemExit(0)


def _runtime_log_mode() -> str:
    """Return the supervisor log mode: debug keeps logs, crash writes on failure."""
    mode = str(os.environ.get("WISP_RUNTIME_LOG_MODE") or "").strip().lower()
    if mode in {"debug", "always", "logs", "log"}:
        return "debug"
    if mode in {"crash", "off", "none", "0", "false"}:
        return "crash"
    if os.environ.get("WISP_RUN_LOG_DIR"):
        return "debug"
    if getattr(sys, "frozen", False):
        return "debug"
    return "crash"


def _prepare_run_log_dir(*, reason: str = "runtime", expose_to_workers: bool = True) -> Path:
    """Create a runtime log directory when debug logs or crash logs are needed."""
    configured = os.environ.get("WISP_RUN_LOG_DIR")
    if configured:
        path = Path(configured)
    else:
        root = repo_root()
        prefix = "wisp_runtime" if reason == "runtime" else "wisp_crash"
        path = root / "build_logs" / f"{prefix}_{time.strftime('%Y%m%d-%H%M%S')}"
        if expose_to_workers:
            os.environ["WISP_RUN_LOG_DIR"] = str(path)
        latest = root / "build_logs" / "latest_wisp_runtime.txt"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(str(path), encoding="utf-8")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _configure_logging(log_dir: Path | None) -> None:
    """Configure supervisor logging, optionally mirrored to a file."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        handlers.append(logging.FileHandler(log_dir / "supervisor.log", encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )


def _write_abrupt_log(reason: str, supervisor: WispSupervisor | None, exc_info=None) -> Path | None:
    """Best-effort crash-only log writer for normal launcher runs."""
    try:
        log_dir = _prepare_run_log_dir(reason="crash", expose_to_workers=False)
        report = log_dir / "supervisor-crash.log"
        lines = [
            f"Wisp ended abruptly: {reason}",
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        if exc_info is not None:
            lines.append("Exception:")
            lines.extend(traceback.format_exception(*exc_info))
            lines.append("")
        if supervisor is not None:
            lines.append("Worker stderr tails:")
            for name, worker in supervisor.workers.items():
                tail = worker.stderr_tail(80) if hasattr(worker, "stderr_tail") else ""
                lines.append(f"\n[{name}]")
                lines.append(tail or "(no recent stderr)")
        report.write_text("\n".join(lines), encoding="utf-8")
        return log_dir
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    # Synthetic copy-Ctrl+C (selected-text capture) reaches the whole console
    # process group; without this the supervisor's SIGINT handler would treat it
    # as a quit and tear the app down. Workers are guarded via configure_paths().
    """Handle main for runtime supervisor app."""
    suppress_console_ctrl_c()
    install_crash_diagnostics()
    log_mode = _runtime_log_mode()
    log_dir = _prepare_run_log_dir() if log_mode == "debug" else None
    _configure_logging(log_dir)
    if log_dir is not None:
        logging.info("Wisp runtime logs: %s", log_dir)
    else:
        logging.info("Wisp runtime logs are off; crash logs will be written only if startup ends abruptly.")
    if not single_instance.acquire():
        logging.warning("Another Wisp instance is already running; exiting.")
        return 2
    supervisor = WispSupervisor()
    stop = threading.Event()
    abrupt_reason = ""

    def _stop(_signum=None, _frame=None) -> None:
        """Signal handler: set the stop event to trigger shutdown."""
        stop.set()

    def _stop_when_ui_exits(returncode=None) -> None:
        """Stop when ui exits."""
        nonlocal abrupt_reason
        logging.info("UI worker exited with code %s; shutting down Wisp", returncode)
        if returncode not in (0, None):
            abrupt_reason = f"UI worker exited with code {returncode}"
        stop.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            signal.signal(sig, _stop)

    ui_worker = supervisor.workers.get("ui")
    if ui_worker is not None and hasattr(ui_worker, "on_exit"):
        ui_worker.on_exit(_stop_when_ui_exits)

    try:
        supervisor.start_all()
        flows = FlowController(
            native=supervisor.workers["native"],
            ui=supervisor.workers["ui"],
            brain=supervisor.workers["brain"],
            audio=supervisor.workers["audio"],
        )
        flows.start()
        try:
            flows.start_hotkeys()
        except Exception:
            logging.exception("native hotkeys did not start")
        stop.wait()
    except BaseException:
        if log_mode != "debug":
            crash_dir = _write_abrupt_log("supervisor exception", supervisor, sys.exc_info())
            if crash_dir is not None:
                logging.error("Wrote Wisp crash log: %s", crash_dir)
        raise
    finally:
        supervisor.shutdown()
    if abrupt_reason and log_mode != "debug":
        crash_dir = _write_abrupt_log(abrupt_reason, supervisor)
        if crash_dir is not None:
            logging.error("Wrote Wisp crash log: %s", crash_dir)
    return 0


if __name__ == "__main__":
    _dispatch_module_mode()
    raise SystemExit(main())
