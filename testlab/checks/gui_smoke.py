"""Offscreen GUI smoke: real windows construct, show, and screenshot.

Thin lab wrapper around the repo's existing GUI smoke pass
(``scripts/run_personal_os_tests.py --_gui-smoke``): settings dialog, agent
windows, intent overlay, chat window, and speech bubble are built for real and
verified visible under the offscreen Qt platform. Screenshots land in
testlab/.artifacts/gui_smoke/.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _lab

_lab.bootstrap()


def main() -> int:
    shots = _lab.scratch_dir("gui_smoke")
    proc = subprocess.run(
        [
            sys.executable,
            str(_lab.REPO_ROOT / "scripts" / "run_personal_os_tests.py"),
            "--_gui-smoke",
            "--mode",
            "offscreen",
            "--screenshot-dir",
            str(shots),
        ],
        cwd=_lab.REPO_ROOT,
        env=_lab.child_env(offscreen_ui=True),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    for line in (proc.stdout or "").splitlines():
        _lab.log(f"[gui] {line}")
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-800:]
        return _lab.finish(_lab.FAIL, f"GUI smoke exited {proc.returncode}: {tail}")
    captured = sorted(p.name for p in shots.glob("*.png"))
    return _lab.finish(
        _lab.PASS,
        f"{len(captured)} windows rendered offscreen ({', '.join(captured)})",
        screenshots=len(captured),
    )


if __name__ == "__main__":
    raise SystemExit(main())
