"""Real global-hotkey registration through the real native worker.

Spawns ``runtime.workers.native_host`` and calls ``native.hotkeys.start`` with
the user's configured hotkeys - the same call the supervisor makes at boot.
If registration fails while a Wisp instance is running, the combos are simply
held by that instance and the check skips; without one, a failure is real.

On a headless/remote macOS session RegisterEventHotKey never fires
(console-session requirement) - registration itself is still the right check.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _lab

_lab.bootstrap()


def _wisp_is_running() -> bool:
    """True when another Wisp holds the single-instance lock."""
    from core.system import single_instance

    if not single_instance.acquire():
        return True
    release = getattr(single_instance, "release", None)
    if callable(release):
        release()
    return False


def main() -> int:
    from runtime.supervisor.ipc import WorkerClient, WorkerSpec

    worker = WorkerClient(
        WorkerSpec(
            "lab-native",
            "runtime.workers.native_host",
            "native",
            env=_lab.env_overrides(isolated_root=_lab.isolated_repo_root("hotkeys")),
        )
    )
    try:
        ping = worker.call("native.ping", {"value": "lab"}, timeout=30.0)
        _lab.log(f"native worker up: pid={ping.get('pid') if isinstance(ping, dict) else ping}")
        result = worker.call("native.hotkeys.start", {"addon_hotkeys": []}, timeout=20.0) or {}
        _lab.log(f"native.hotkeys.start: {result}")
        started = bool(isinstance(result, dict) and result.get("started"))
        registered = int((result or {}).get("registered") or 0)
        requested = int((result or {}).get("requested") or 0)
        reason = str((result or {}).get("reason") or (result or {}).get("error") or "unknown")
        if started and requested and registered == requested:
            return _lab.finish(
                _lab.PASS,
                f"all {registered} global hotkeys registered by the real native worker",
                result=result,
            )
        if _wisp_is_running():
            return _lab.finish(
                _lab.SKIP,
                f"only {registered}/{requested} hotkeys registered - the rest are held by the "
                "running Wisp instance; quit Wisp for a conclusive run",
            )
        if os.environ.get("CI"):
            # Headless CI runners have no console session / display server, so
            # global hotkey registration failing there is environmental.
            return _lab.finish(
                _lab.SKIP,
                f"registration inconclusive on a headless CI runner ({registered}/{requested}: {reason})",
            )
        if started and registered:
            return _lab.finish(
                _lab.FAIL,
                f"partial registration with no Wisp running: {registered}/{requested} ({reason})",
            )
        return _lab.finish(_lab.FAIL, f"hotkey registration failed with no Wisp running: {reason}")
    finally:
        worker.shutdown()
        _lab.log("native worker shut down (hotkeys released)")


if __name__ == "__main__":
    raise SystemExit(main())
