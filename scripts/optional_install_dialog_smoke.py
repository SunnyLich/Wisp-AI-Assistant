"""Launch the optional installer dialog prototype with a harmless worker."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.optional_install_dialog import OptionalInstallDialog, optional_install_mock_command  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("success", "failure", "slow", "unicode"), default="success")
    parser.add_argument("--lines", type=int, default=8)
    parser.add_argument("--delay", type=float, default=0.08)
    parser.add_argument("--auto-close", action="store_true", help="Close the dialog automatically after the worker exits.")
    args = parser.parse_args()

    _app = QApplication.instance() or QApplication(sys.argv)
    log_path = ROOT / "build_logs" / "optional_install_dialog_smoke.log"
    try:
        log_path.unlink(missing_ok=True)
    except OSError:
        pass
    dialog = OptionalInstallDialog(
        title="Optional Installer Prototype",
        subtitle="This smoke test uses the same Qt subprocess path as the planned STT/TTS installer window.",
        command=optional_install_mock_command(mode=args.mode, lines=args.lines, delay=args.delay),
        cwd=ROOT,
        log_path=log_path,
        auto_start=True,
    )
    if args.auto_close:
        dialog.install_finished.connect(lambda _code: dialog.accept())
    dialog.exec()
    return int(dialog.exit_code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
