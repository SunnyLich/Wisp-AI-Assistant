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
from macos_py.bootstrap import repo_root
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

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            signal.signal(sig, _stop)

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
