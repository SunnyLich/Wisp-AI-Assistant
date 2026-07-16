"""Exercise the Windows updater helper against a fake install directory.

This script is intentionally separate from the app. It builds a temporary
"CurrentWisp" folder, creates a fake update archive, asks ``core.updater`` to
generate and launch its normal Windows apply helper, then waits for marker files
that prove the fake install was replaced and "restarted".

Run from the repository root on Windows:

    python tools/test_windows_updater_apply.py

Nothing under a real Wisp install is touched.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import updater  # noqa: E402


def _write_fake_launcher(path: Path, marker_name: str) -> None:
    path.write_text(
        "\n".join(
            [
                "@echo off",
                "setlocal",
                f"echo restarted>\"%~dp0{marker_name}\"",
                "exit /b 0",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _wait_for(path: Path, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.25)
    return path.exists()


def _powershell_processes() -> list[str]:
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Process powershell -ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty Id",
            ],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except Exception:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Leave the temporary test directory behind for inspection.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="Seconds to wait for the fake restart marker.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory to use for the rehearsal. Defaults under build_logs/updater_rehearsals.",
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        print("This updater apply rehearsal only runs on Windows.", file=sys.stderr)
        return 2

    if args.root is None:
        temp_root = (
            ROOT
            / "build_logs"
            / "updater_rehearsals"
            / f"wisp-updater-test-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        )
    else:
        temp_root = args.root.resolve()
    if temp_root.exists():
        raise SystemExit(f"Refusing to reuse existing rehearsal directory: {temp_root}")
    temp_root.mkdir(parents=True)
    fake_install = temp_root / "CurrentWisp"
    fake_install.mkdir()
    restart_target = fake_install / "Wisp.cmd"
    _write_fake_launcher(restart_target, "restart-before-update.txt")
    (fake_install / "version.txt").write_text("old\n", encoding="utf-8")

    update_zip = temp_root / "Wisp-test-windows-x64.zip"
    with zipfile.ZipFile(update_zip, "w") as archive:
        archive.writestr("Wisp/version.txt", "new\n")
        archive.writestr(
            "Wisp/Wisp.cmd",
            "\n".join(
                [
                    "@echo off",
                    "setlocal",
                    "echo restarted>\"%~dp0restart-after-update.txt\"",
                    "exit /b 0",
                    "",
                ]
            ),
        )

    updates_dir = temp_root / "updates"
    lock_path = temp_root / "wisp.lock"
    old_frozen = getattr(sys, "frozen", None)
    old_executable = sys.executable
    old_update_dir = updater.UPDATE_DOWNLOAD_DIR
    old_lock = updater.SINGLE_INSTANCE_LOCK

    try:
        sys.frozen = True
        sys.executable = str(restart_target)
        updater.UPDATE_DOWNLOAD_DIR = updates_dir
        updater.SINGLE_INSTANCE_LOCK = lock_path

        before_powershell = set(_powershell_processes())
        script_path = updater.apply_update(update_zip, pid=999999)
        print(f"Temporary test root: {temp_root}")
        print(f"Generated helper: {script_path}")
        print("A small 'Wisp Update' window should appear briefly if UI startup works.")

        marker = fake_install / "restart-after-update.txt"
        if not _wait_for(marker, args.timeout):
            after_powershell = set(_powershell_processes())
            error_log = updates_dir / "apply-update-error.log"
            print("Updater rehearsal did not finish before the timeout.", file=sys.stderr)
            if error_log.exists():
                print(f"Error log: {error_log}", file=sys.stderr)
                print(error_log.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
            new_processes = sorted(after_powershell - before_powershell)
            if new_processes:
                print(f"PowerShell processes still present: {', '.join(new_processes)}", file=sys.stderr)
            return 1

        version = (fake_install / "version.txt").read_text(encoding="utf-8").strip()
        if version != "new":
            print(f"Fake install was not replaced correctly; version.txt is {version!r}.", file=sys.stderr)
            return 1

        print("Updater rehearsal passed: fake install was replaced and fake Wisp restarted.")
        return 0
    finally:
        sys.executable = old_executable
        updater.UPDATE_DOWNLOAD_DIR = old_update_dir
        updater.SINGLE_INSTANCE_LOCK = old_lock
        if old_frozen is None:
            try:
                delattr(sys, "frozen")
            except AttributeError:
                pass
        else:
            sys.frozen = old_frozen
        if not args.keep:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
