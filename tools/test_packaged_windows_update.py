"""Exercise the Windows updater helper against a real packaged Wisp folder.

The test uses the already-built ``dist/Wisp`` folder as the update payload,
creates a controlled install copy under ``build_logs/updater_rehearsals``, and
then asks ``core.updater`` to replace that copy. It never targets the user's
actual installed Wisp folder.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import updater  # noqa: E402


def _wait_for(predicate, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.5)
    return predicate()


def _archive_folder(source: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(source.parent))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--expected-version", default="0.6.4")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args()

    if sys.platform != "win32":
        print("This packaged updater rehearsal only runs on Windows.", file=sys.stderr)
        return 2

    dist = ROOT / "dist" / "Wisp"
    dist_exe = dist / "Wisp.exe"
    if not dist_exe.exists():
        print(f"Build output is missing: {dist_exe}", file=sys.stderr)
        return 2

    temp_root = (
        args.root.resolve()
        if args.root is not None
        else (
            ROOT
            / "build_logs"
            / "updater_rehearsals"
            / f"packaged-update-test-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        )
    )
    install_root = temp_root / "CurrentWisp"
    updates_dir = temp_root / "updates"
    archive_path = temp_root / "Wisp-0.6.4-windows-x64.zip"
    sentinel = install_root / "old-install-sentinel.txt"

    if temp_root.exists():
        print(f"Refusing to reuse existing rehearsal directory: {temp_root}", file=sys.stderr)
        return 2
    temp_root.mkdir(parents=True)
    try:
        shutil.copytree(dist, install_root)
        sentinel.write_text("old\n", encoding="utf-8")
        _archive_folder(dist, archive_path)

        old_frozen = getattr(sys, "frozen", None)
        old_executable = sys.executable
        old_update_dir = updater.UPDATE_DOWNLOAD_DIR
        old_lock = updater.SINGLE_INSTANCE_LOCK
        try:
            sys.frozen = True
            sys.executable = str(install_root / "Wisp.exe")
            updater.UPDATE_DOWNLOAD_DIR = updates_dir
            updater.SINGLE_INSTANCE_LOCK = temp_root / "wisp.lock"
            script_path = updater.apply_update(archive_path, pid=999999)
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

        print(f"Temporary test root: {temp_root}")
        print(f"Generated helper: {script_path}")

        def replaced() -> bool:
            return (install_root / "Wisp.exe").exists() and not sentinel.exists()

        if not _wait_for(replaced, args.timeout):
            error_log = updates_dir / "apply-update-error.log"
            print("Packaged updater rehearsal did not replace the install copy.", file=sys.stderr)
            if error_log.exists():
                print(error_log.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
            return 1

        pyproject = install_root / "_internal" / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8", errors="replace") if pyproject.exists() else ""
        expected_line = f'version = "{args.expected_version}"'
        if expected_line not in text:
            print(f"Updated install does not contain {expected_line!r} in {pyproject}.", file=sys.stderr)
            return 1

        print(f"Packaged updater rehearsal passed: install copy now contains {args.expected_version}.")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
