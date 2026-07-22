from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_optional_install_dialog_preserves_persisted_failure_detail(tmp_path: Path):
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    from ui.optional_install_dialog import OptionalInstallDialog

    app = QApplication.instance() or QApplication(sys.argv)
    status_path = tmp_path / "kokoro-install.status.json"
    detail = "Not enough free disk space while extracting the downloaded packages."
    status_path.write_text(json.dumps({"ok": False, "message": detail}), encoding="utf-8")
    dialog = OptionalInstallDialog(
        title="Kokoro installer",
        command=[sys.executable, "--version"],
        status_path=status_path,
        auto_start=False,
    )
    try:
        dialog._handle_finished(1, QProcess.ExitStatus.NormalExit)
        assert dialog._status.text() == detail
        assert detail in dialog.log_text()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    "detail",
    [
        "Network access is unavailable while downloading packages.",
        "Not enough free disk space while extracting packages.",
        "Filesystem permission denied while installing packages.",
        "Dependency versions conflict with the active runtime.",
        "Installer returned invalid status data.",
    ],
)
def test_optional_install_dialog_surfaces_durable_failure_classes(tmp_path: Path, detail: str):
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    from ui.optional_install_dialog import OptionalInstallDialog

    app = QApplication.instance() or QApplication(sys.argv)
    status_path = tmp_path / "install.status.json"
    status_path.write_text(json.dumps({"ok": False, "message": detail}), encoding="utf-8")
    dialog = OptionalInstallDialog(
        title="Failed installer",
        command=[sys.executable, "--version"],
        status_path=status_path,
        log_path=tmp_path / "install.log",
        auto_start=False,
    )
    try:
        dialog._handle_finished(1, QProcess.ExitStatus.NormalExit)

        assert dialog.exit_code == 1
        assert dialog._status.text() == detail
        assert detail in dialog.log_text()
        assert dialog._close_btn.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_optional_install_dialog_invalid_status_does_not_claim_restart_ready(tmp_path: Path):
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    from ui.optional_install_dialog import OptionalInstallDialog

    app = QApplication.instance() or QApplication(sys.argv)
    status_path = tmp_path / "invalid.status.json"
    status_path.write_text("{not-json", encoding="utf-8")
    dialog = OptionalInstallDialog(
        title="Invalid installer status",
        command=[sys.executable, "--version"],
        status_path=status_path,
        auto_start=False,
    )
    try:
        dialog._handle_finished(0, QProcess.ExitStatus.NormalExit)

        assert dialog.exit_code == 0
        assert dialog._restart_btn.isHidden()
        assert dialog._close_btn.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_optional_install_dialog_offers_restart_for_staged_packages(tmp_path: Path):
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    from ui.optional_install_dialog import OptionalInstallDialog

    app = QApplication.instance() or QApplication(sys.argv)
    status_path = tmp_path / "speech-install.status.json"
    status_path.write_text(
        json.dumps({"ok": None, "restart_apply": True, "message": "Local speech packages are staged."}),
        encoding="utf-8",
    )
    dialog = OptionalInstallDialog(
        title="Local speech installer",
        command=[sys.executable, "--version"],
        status_path=status_path,
        auto_start=False,
    )
    try:
        dialog._handle_finished(0, QProcess.ExitStatus.NormalExit)

        assert not dialog._restart_btn.isHidden()
        assert dialog._restart_btn.text() == "Restart app now"
        assert dialog._restart_btn.isDefault()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


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
        assert not dialog._progress_percent.isHidden()
        assert "Elapsed " in dialog._elapsed.text()
        assert dialog._spinner.is_active()
        assert not hasattr(dialog, "_progress")
    finally:
        dialog._spinner.stop()
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_optional_install_dialog_polls_phase_progress_instead_of_showing_dash_percent(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from ui.optional_install_dialog import OptionalInstallDialog

    app = QApplication.instance() or QApplication(sys.argv)
    status_path = tmp_path / "stt-install.status.json"
    status_path.write_text(
        json.dumps(
            {
                "ok": None,
                "message": "STT install is resolving and downloading locked packages.",
                "progress_percent": 10,
            }
        ),
        encoding="utf-8",
    )
    dialog = OptionalInstallDialog(
        title="STT installer",
        command=[sys.executable, "--version"],
        status_path=status_path,
        auto_start=False,
    )
    try:
        assert dialog._progress_percent.text() == ""
        assert dialog._progress_percent.isHidden()

        dialog._set_running(True)
        dialog._refresh_progress_status()
        app.processEvents()

        assert dialog._progress_percent.text() == "10%"
        assert not dialog._progress_percent.isHidden()
        assert dialog._status.text() == "STT install is resolving and downloading locked packages."
        assert "Elapsed " in dialog._elapsed.text()
        assert "—%" not in dialog._progress_percent.text()
    finally:
        dialog._set_running(False)
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_restart_apply_status_uses_percentage_and_spinner(tmp_path: Path):
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
        assert not dialog._progress_percent.isHidden()
        assert "Elapsed " in dialog._elapsed.text()
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


def test_optional_install_dialog_missing_executable_is_controlled_and_logged(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from ui.optional_install_dialog import OptionalInstallDialog

    app = QApplication.instance() or QApplication(sys.argv)
    log_path = tmp_path / "missing-process.log"
    dialog = OptionalInstallDialog(
        title="Missing installer",
        command=[str(tmp_path / "does-not-exist.exe")],
        log_path=log_path,
        auto_start=False,
    )
    try:
        finished = _wait_for_dialog(dialog, app)

        assert finished
        assert dialog.exit_code == 1
        assert dialog._close_btn.isEnabled()
        assert not dialog._cancel_btn.isEnabled()
        assert log_path.exists()
        assert "does-not-exist" in dialog.log_text()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_optional_install_dialog_cancel_terminates_then_kills_stuck_process(
    tmp_path: Path,
    monkeypatch,
):
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    import ui.optional_install_dialog as install_ui
    from ui.optional_install_dialog import OptionalInstallDialog

    app = QApplication.instance() or QApplication(sys.argv)
    calls: list[str] = []

    class StuckProcess:
        def state(self):
            return QProcess.ProcessState.Running

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

    dialog = OptionalInstallDialog(
        title="Stuck installer",
        command=[sys.executable, "--version"],
        log_path=tmp_path / "cancelled.log",
        auto_start=False,
    )
    try:
        dialog._process = StuckProcess()
        dialog._set_running(True)
        monkeypatch.setattr(
            install_ui.QTimer,
            "singleShot",
            staticmethod(lambda _delay, callback: callback()),
        )

        dialog.cancel()

        assert calls == ["terminate", "kill"]
        assert dialog._cancel_requested is True
        assert "Cancelling installer" in dialog.log_text()
        assert (tmp_path / "cancelled.log").exists()
    finally:
        dialog._process = None
        dialog._set_running(False)
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    "error",
    [
        OSError("clipboard locked"),
        RuntimeError("focus changed before completion"),
        RuntimeError("target blocks synthetic input"),
        RuntimeError("another app overwrote clipboard"),
        PermissionError("accessibility permission missing"),
    ],
)
def test_optional_install_dialog_clipboard_failure_stays_controlled(
    tmp_path: Path,
    monkeypatch,
    error: Exception,
):
    from PySide6.QtWidgets import QApplication

    import ui.optional_install_dialog as install_ui
    from ui.optional_install_dialog import OptionalInstallDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = OptionalInstallDialog(
        title="Copy installer log",
        command=[sys.executable, "--version"],
        log_path=tmp_path / "copy.log",
        auto_start=False,
    )
    fake_app = SimpleNamespace(
        clipboard=lambda: SimpleNamespace(
            setText=lambda _text: (_ for _ in ()).throw(error)
        )
    )
    try:
        dialog._append_line("installer output")
        monkeypatch.setattr(install_ui, "QApplication", fake_app)

        dialog._copy_log()

        assert type(error).__name__ in dialog._status.text()
        assert "installer output" in dialog.log_text()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_optional_install_dialog_log_folder_failures_stay_controlled(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import ui.optional_install_dialog as install_ui
    from ui.optional_install_dialog import OptionalInstallDialog

    app = QApplication.instance() or QApplication(sys.argv)
    log_path = tmp_path / "missing" / "install.log"
    dialog = OptionalInstallDialog(
        title="Open installer log",
        command=[sys.executable, "--version"],
        log_path=log_path,
        auto_start=False,
    )
    try:
        monkeypatch.setattr(install_ui.QDesktopServices, "openUrl", lambda _url: False)
        dialog._open_log_folder()
        assert log_path.parent.is_dir()
        assert "unavailable" in dialog._status.text()

        monkeypatch.setattr(
            install_ui.Path,
            "mkdir",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
        )
        dialog._open_log_folder()
        assert "PermissionError" in dialog._status.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_optional_install_dialog_rejects_close_while_process_is_running(tmp_path: Path):
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    from ui.optional_install_dialog import OptionalInstallDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = OptionalInstallDialog(
        title="Active installer",
        command=[sys.executable, "--version"],
        log_path=tmp_path / "active.log",
        auto_start=False,
    )
    event = SimpleNamespace(ignored=False, ignore=lambda: setattr(event, "ignored", True))
    dialog._process = SimpleNamespace(state=lambda: QProcess.ProcessState.Running)
    try:
        dialog.closeEvent(event)

        assert event.ignored is True
        assert "Cancel the installer" in dialog._status.text()
    finally:
        dialog._process = None
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
