"""Pure-Python macOS app supervisor entrypoint."""

from __future__ import annotations

import logging
import signal
import sys
import threading

from macos_py.supervisor.ipc import WispSupervisor


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
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
        # Start passive native listeners after all workers are healthy. UI/brain
        # product routing will be layered on this supervisor seam.
        try:
            supervisor.call("native", "native.hotkeys.start", timeout=10.0)
        except Exception:
            logging.exception("native hotkeys did not start")
        stop.wait()
    finally:
        supervisor.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

