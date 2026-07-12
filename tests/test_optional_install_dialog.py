from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")


def _wait_for_dialog(dialog, app, timeout_ms: int = 5000) -> list[int]:
    from PySide6.QtCore import QEventLoop, QTimer

    finished: list[int] = []
    loop = QEventLoop()

    def done(code: int) -> None:
        finished.append(int(code))
        loop.quit()

    dialog.install_finished.connect(done)
    QTimer.singleShot(timeout_ms, loop.quit)
    dialog.start()
    loop.exec()
    app.processEvents()
    return finished


def test_optional_install_dialog_streams_success_output(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from ui.optional_install_dialog import OptionalInstallDialog, optional_install_mock_command

    app = QApplication.instance() or QApplication(sys.argv)
    log_path = tmp_path / "install.log"
    dialog = OptionalInstallDialog(
        title="Prototype installer",
        command=optional_install_mock_command(mode="success", lines=2, delay=0),
        cwd=Path.cwd(),
        log_path=log_path,
        auto_start=False,
    )
    try:
        finished = _wait_for_dialog(dialog, app)

        assert finished == [0]
        assert dialog.exit_code == 0
        assert "installing package chunk 2/2" in dialog.log_text()
        assert "Installer completed successfully." in dialog.log_text()
        assert "Installer completed successfully." in dialog._status.text()
        assert dialog._progress_percent.text() == "100%"
        assert not dialog._spinner.is_active()
        assert log_path.exists()
        assert "verification complete" in log_path.read_text(encoding="utf-8")
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_optional_install_dialog_reports_failure_exit_code(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from ui.optional_install_dialog import OptionalInstallDialog, optional_install_mock_command

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = OptionalInstallDialog(
        title="Prototype installer",
        command=optional_install_mock_command(mode="failure", lines=1, delay=0),
        cwd=Path.cwd(),
        log_path=tmp_path / "failure.log",
        auto_start=False,
    )
    try:
        finished = _wait_for_dialog(dialog, app)

        assert finished == [7]
        assert dialog.exit_code == 7
        assert "simulated resolver failure" in dialog.log_text()
        assert "Installer failed with exit code 7." in dialog._status.text()
        assert dialog._close_btn.isEnabled()
        assert not dialog._cancel_btn.isEnabled()
        assert dialog._spinner.text() == "×"
        assert not dialog._spinner.is_active()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_optional_install_mock_command_uses_module_worker():
    from ui.optional_install_dialog import optional_install_mock_command

    command = optional_install_mock_command(mode="unicode", lines=3, delay=0)

    assert command[:3] == [sys.executable, "-m", "runtime.workers.mock_optional_install"]
    assert "--mode" in command
    assert "unicode" in command


def test_optional_install_dialog_shows_percentage_and_spinner(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from ui.optional_install_dialog import OptionalInstallDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = OptionalInstallDialog(
        title="Percentage installer",
        command=[sys.executable, "--version"],
        cwd=Path.cwd(),
        log_path=tmp_path / "percentage.log",
        auto_start=False,
    )
    try:
        dialog._set_running(True)
        dialog._append_text("Downloading package wheel 37%\r")
        app.processEvents()

        assert dialog._progress_percent.text() == "37%"
        assert dialog._spinner.is_active()
        assert not hasattr(dialog, "_progress")
    finally:
        dialog._spinner.stop()
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_restart_apply_status_uses_percentage_and_spinner(tmp_path: Path):
    import json

    from PySide6.QtWidgets import QApplication

    from runtime.workers.optional_apply_status_window import ApplyStatusWindow

    app = QApplication.instance() or QApplication(sys.argv)
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps({"ok": None, "message": "STT staged install is applying package files.", "progress_percent": 45}),
        encoding="utf-8",
    )
    dialog = ApplyStatusWindow(display_name="STT", status_path=status_path)
    try:
        dialog._refresh()
        assert dialog._progress_percent.text() == "45%"
        assert dialog._spinner.is_active()
        assert not hasattr(dialog, "_progress")

        status_path.write_text(
            json.dumps({"ok": True, "message": "STT installed successfully.", "progress_percent": 100}),
            encoding="utf-8",
        )
        dialog._refresh()
        assert dialog._progress_percent.text() == "100%"
        assert dialog._spinner.text() == "✓"
        assert not dialog._spinner.is_active()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
