"""Pure-Python app supervisor entrypoint."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from macos_py.supervisor.flows import FlowController
from macos_py.supervisor.ipc import WispSupervisor
from macos_py.bootstrap import (
    repo_root,
    suppress_console_ctrl_c,
    install_crash_diagnostics,
)
from core.system import single_instance


def _prepare_run_log_dir() -> Path:
    configured = os.environ.get("WISP_RUN_LOG_DIR")
    if configured:
        path = Path(configured)
    else:
        root = repo_root()
        path = root / "build_logs" / f"wisp_runtime_{time.strftime('%Y%m%d-%H%M%S')}"
        os.environ["WISP_RUN_LOG_DIR"] = str(path)
        latest = root / "build_logs" / "latest_wisp_runtime.txt"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(str(path), encoding="utf-8")
    path.mkdir(parents=True, exist_ok=True)
    return path


def main() -> int:
    # Synthetic copy-Ctrl+C (selected-text capture) reaches the whole console
    # process group; without this the supervisor's SIGINT handler would treat it
    # as a quit and tear the app down. Workers are guarded via configure_paths().
    suppress_console_ctrl_c()
    install_crash_diagnostics()
    log_dir = _prepare_run_log_dir()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "supervisor.log", encoding="utf-8"),
        ],
    )
    logging.info("Wisp runtime logs: %s", log_dir)
    if not single_instance.acquire():
        logging.warning("Another Wisp instance is already running; exiting.")
        return 2
    supervisor = WispSupervisor()
    stop = threading.Event()

    def _stop(_signum=None, _frame=None) -> None:
        stop.set()

    def _stop_when_ui_exits(returncode=None) -> None:
        logging.info("UI worker exited with code %s; shutting down Wisp", returncode)
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
    finally:
        supervisor.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
