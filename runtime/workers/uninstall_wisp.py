"""Standalone GUI entrypoint used by ``Uninstall Wisp.bat``."""
from __future__ import annotations

import sys


def _run_uninstaller_dialog() -> int:
    """Open the shared uninstaller only when no other Wisp process is active."""
    from PySide6.QtWidgets import QMessageBox

    from core.system import single_instance
    from ui.i18n import t
    from ui.uninstall_dialog import run_uninstall_dialog

    if not single_instance.acquire():
        QMessageBox.warning(
            None,
            t("Could not start uninstaller"),
            t("Another Wisp process is still running. Close Wisp, then run Uninstall Wisp.bat again."),
        )
        return 1
    run_uninstall_dialog(None)
    return 0


def main() -> int:
    """Create a themed Wisp application and show the complete-uninstall flow."""
    from PySide6.QtWidgets import QApplication

    from ui import i18n
    from ui.shared.app_icon import install_app_icon
    from ui.shared.theme import apply_app_theme

    app = QApplication.instance() or QApplication(sys.argv[:1])
    install_app_icon(app)
    i18n.set_language(app=app)
    apply_app_theme(app)
    return _run_uninstaller_dialog()


if __name__ == "__main__":
    raise SystemExit(main())
