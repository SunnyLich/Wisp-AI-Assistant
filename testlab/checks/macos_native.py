"""macOS native crash harnesses (darwin only).

Runs the repo's real macOS crash reproducers as a lab check:
  1. scripts/macos_smoke.py            - offscreen macOS-specific smoke
  2. scripts/macos_testbot.py ssl-race - concurrent real SDK client builds
     (the SSL/Security trust-store segfault surface), 20 iterations

Both are the "real thing" harnesses: they build real clients and drive real
threads, so a regression segfaults here instead of on the user's Mac.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _lab

_lab.bootstrap()

STEPS = (
    ("macos-smoke", ["scripts/macos_smoke.py"], 240),
    ("ssl-race", ["scripts/macos_testbot.py", "ssl-race", "--iterations", "20"], 300),
)


def main() -> int:
    if sys.platform != "darwin":
        return _lab.finish(_lab.SKIP, f"darwin-only check (this is {sys.platform})")
    notes = []
    for name, argv, timeout in STEPS:
        cmd = [sys.executable, *[str(_lab.REPO_ROOT / argv[0]), *argv[1:]]]
        _lab.log(f"[{name}] {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd,
                cwd=_lab.REPO_ROOT,
                env=_lab.child_env(offscreen_ui=True),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return _lab.finish(_lab.FAIL, f"{name} hung past {timeout}s (native deadlock?)")
        for line in (proc.stdout or "").splitlines()[-40:]:
            _lab.log(f"[{name}] {line}")
        if proc.returncode != 0:
            tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-800:]
            return _lab.finish(
                _lab.FAIL,
                f"{name} exited {proc.returncode} (negative = signal/segfault): {tail}",
            )
        notes.append(f"{name} ok")
    return _lab.finish(_lab.PASS, "; ".join(notes))


if __name__ == "__main__":
    raise SystemExit(main())
