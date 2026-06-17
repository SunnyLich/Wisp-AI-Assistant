"""
Mic-free verification of the macOS native worker (core.macos_helper).

Runs entirely in a terminal — no GUI, no microphone, no voice needed — so it
works over a remote/headless Mac session. It drives the worker subprocess
through the real client and checks:

  1. the worker spawns and answers ``ping``
  2. faster-whisper/torch load + transcribe a synthetic buffer *inside the worker*
  3. the CoreAudio mic InputStream opens/closes in the worker without crashing
     (SKIPPED, not failed, if the machine has no input device)

Usage on the Mac:

    ./.venv/bin/python scripts/macos_helper_selftest.py

(The WISP_MACOS_HELPER flag is not required here — the client always spawns the
worker; the flag only governs whether core.stt delegates to it.)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.macos_helper.client import HelperClient, HelperError


def main() -> int:
    """Support command-line helper for scripts macos helper selftest for main."""
    client = HelperClient()
    checks: list[bool] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        """Record a check result and print a PASS/FAIL line."""
        checks.append(ok)
        line = f"{'PASS' if ok else 'FAIL'} {name}"
        if detail:
            line += f" — {detail}"
        print(line, flush=True)

    try:
        # 1. liveness
        try:
            p = client.call("ping", {"value": "selftest"}, timeout=15)
            record("worker spawns and answers ping",
                   isinstance(p, dict) and p.get("pong") is True,
                   f"pid={p.get('pid')}")
        except HelperError as exc:
            record("worker spawns and answers ping", False, str(exc))
            return _finish(checks)

        # 2. Whisper/torch load + transcribe in the worker — no mic, no voice
        try:
            r = client.call("stt.selftest", {"seconds": 1.0}, timeout=300)
            record("Whisper loads + transcribes in worker",
                   isinstance(r, dict) and r.get("ok") is True,
                   f"model={r.get('model')} load={r.get('load_seconds')}s "
                   f"transcribe={r.get('transcribe_seconds')}s text={r.get('text')!r}")
        except HelperError as exc:
            record("Whisper loads + transcribes in worker", False, str(exc))

        # 3. CoreAudio mic stream open/close in the worker — no segfault.
        #    Graceful SKIP (not counted) when there is no input device.
        try:
            m = client.call("stt.mic_probe", timeout=30)
            if isinstance(m, dict) and m.get("opened"):
                record("CoreAudio mic stream opens/closes in worker", True,
                       f"pid={m.get('pid')}")
            else:
                detail = (m or {}).get("error") if isinstance(m, dict) else m
                print(f"SKIP CoreAudio mic stream (no input device?): {detail}", flush=True)
        except HelperError as exc:
            record("CoreAudio mic stream opens/closes in worker", False, str(exc))
    finally:
        client.shutdown()

    return _finish(checks)


def _finish(checks: list[bool]) -> int:
    """Support command-line helper for scripts macos helper selftest for finish."""
    passed = sum(1 for c in checks if c)
    print(f"--- {passed}/{len(checks)} checks passed ---", flush=True)
    return 0 if checks and passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
