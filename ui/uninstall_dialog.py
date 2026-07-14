"""Reusable confirmation flow for a complete Wisp uninstall."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from core import uninstaller
from ui.i18n import t


def run_uninstall_dialog(parent: QWidget | None = None) -> bool:
    """Confirm and launch the detached, self-removing Wisp uninstaller."""
    try:
        plan = uninstaller.build_uninstall_plan()
    except Exception as exc:  # noqa: BLE001 - safety validation must be visible
        QMessageBox.warning(
            parent,
            t("Could not start uninstaller"),
            t("Could not build a safe uninstall plan: {error}").format(error=exc),
        )
        return False

    kind = t("source checkout") if plan.source_checkout else t("release installation")
    details = t(
        "This will permanently remove:\n"
        "• Wisp's current {kind}: {app_root}\n"
        "• All Wisp settings, chats, memory, add-ons, tools, logs, updates, and optional packages: {data_root}\n"
        "• Wisp API keys and sign-in tokens from the OS keychain\n"
        "• Wisp's STT/TTS model repositories from the Hugging Face cache\n"
        "• Wisp login and desktop entries\n\n"
        "Shared uv/pip caches and unrelated Hugging Face models will not be removed."
    ).format(kind=kind, app_root=plan.app_root, data_root=plan.user_data_root)
    exact_targets = "\n".join(f"• {path}" for path in plan.targets)
    details += "\n\n" + t("Exact paths scheduled for deletion:") + "\n" + exact_targets
    if plan.source_checkout:
        details += "\n\n" + t(
            "The source checkout will be deleted, including its Git history, uncommitted changes, "
            "and every file inside it."
        )

    confirm = QMessageBox(parent)
    confirm.setIcon(QMessageBox.Icon.Critical)
    confirm.setWindowTitle(t("Uninstall Wisp?"))
    confirm.setText(t("This action cannot be undone."))
    confirm.setInformativeText(details)
    confirm.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    confirm.setDefaultButton(QMessageBox.StandardButton.No)
    if confirm.exec() != QMessageBox.StandardButton.Yes:
        return False

    try:
        uninstaller.launch_uninstaller(plan)
    except Exception as exc:  # noqa: BLE001 - keep Wisp open after a failed launch
        QMessageBox.warning(
            parent,
            t("Could not start uninstaller"),
            t("Could not start uninstaller: {error}").format(error=exc),
        )
        return False

    QMessageBox.information(
        parent,
        t("Uninstaller started"),
        t(
            "Wisp will now close. The uninstaller will remove only the listed Wisp-owned files "
            "after all Wisp processes exit."
        ),
    )
    app = QApplication.instance()
    if app is not None:
        QTimer.singleShot(0, app.quit)
    return True
