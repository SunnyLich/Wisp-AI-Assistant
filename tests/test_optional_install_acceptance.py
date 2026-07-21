from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

pytestmark = pytest.mark.workflow


def _stop_and_dispose(dialog, qapp) -> None:
    """Leave no installer process or top-level widget behind after an outcome."""

    from PySide6.QtCore import QProcess

    process = dialog._process
    if process is not None and process.state() != QProcess.ProcessState.NotRunning:
        process.kill()
        process.waitForFinished(3000)
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_optional_installer_real_ui_terminal_outcome_matrix(
    qapp,
    tmp_path: Path,
    monkeypatch,
    runtime_state_guard,
    outcome: str,
):
    """Drive the production dialog through every terminal state and control."""

    del runtime_state_guard

    from PySide6.QtCore import QProcess

    import ui.optional_install_dialog as install_ui
    from scripts.runtime_test_harness import QtUserDriver
    from ui.optional_install_dialog import OptionalInstallDialog, optional_install_mock_command

    driver = QtUserDriver(qapp, timeout=8.0)
    log_path = tmp_path / f"{outcome}-install.log"
    status_path = tmp_path / f"{outcome}-install.status.json"
    status_payload: dict[str, object] = {"ok": None, "restart_apply": False}
    mode = "success" if outcome == "cancel" else outcome
    lines = 80 if outcome == "cancel" else 8
    delay = 0.05

    if outcome == "success":
        status_payload["restart_apply"] = True
    elif outcome == "failure":
        status_payload.update(
            ok=False,
            message="Resolver failed after retaining the optional-install status.",
        )
    status_path.write_text(json.dumps(status_payload), encoding="utf-8")

    restarted: list[bool] = []
    opened_folders: list[str] = []
    monkeypatch.setattr(
        OptionalInstallDialog,
        "_restart_app_now",
        staticmethod(lambda: restarted.append(True)),
    )
    monkeypatch.setattr(
        install_ui.QDesktopServices,
        "openUrl",
        lambda url: opened_folders.append(url.toLocalFile()) or True,
    )

    dialog = OptionalInstallDialog(
        title=f"{outcome.title()} optional install",
        command=optional_install_mock_command(mode=mode, lines=lines, delay=delay),
        cwd=Path.cwd(),
        log_path=log_path,
        status_path=status_path,
        auto_start=True,
    )
    try:
        dialog.show()
        driver.wait(
            lambda: dialog._process is not None
            and dialog._process.state() == QProcess.ProcessState.Running,
            f"{outcome} installer process to start",
        )
        driver.wait(
            lambda: "installing package chunk 1/" in dialog.log_text(),
            f"{outcome} installer live output",
        )

        # These assertions happen while the real child process is still alive.
        assert dialog._elapsed.text().startswith("Elapsed ")
        assert dialog._cancel_btn.isEnabled()
        assert not dialog._close_btn.isEnabled()
        assert log_path.exists()

        if outcome == "cancel":
            driver.click(dialog._cancel_btn)
            assert dialog._cancel_requested is True
            driver.wait(lambda: dialog.exit_code is not None, "cancelled installer to finish")
            assert dialog.exit_code != 0
            assert "Installer cancelled." in dialog._status.text()
            retained_marker = "Installer cancelled."
        else:
            driver.wait(lambda: dialog.exit_code is not None, f"{outcome} installer to finish")
            if outcome == "success":
                assert dialog.exit_code == 0
                assert "Installer completed successfully." in dialog._status.text()
                retained_marker = "verification complete"
            else:
                assert dialog.exit_code == 7
                assert dialog._status.text() == status_payload["message"]
                retained_marker = str(status_payload["message"])

        # Terminal state changes all related controls.  The restart branch is
        # intentionally exclusive to a successful staged install.
        assert not dialog._cancel_btn.isEnabled()
        assert dialog._close_btn.isEnabled()
        assert dialog._copy_btn.isEnabled()
        assert dialog._open_log_btn.isEnabled()
        assert dialog._restart_btn.isVisible() is (outcome == "success")
        retained_log = log_path.read_text(encoding="utf-8")
        assert retained_marker in retained_log
        assert "installing package chunk 1/" in retained_log

        driver.click(dialog._copy_btn)
        assert qapp.clipboard().text() == dialog.log_text()

        driver.click(dialog._open_log_btn)
        assert len(opened_folders) == 1
        assert Path(opened_folders[0]).resolve() == log_path.parent.resolve()

        if outcome == "success":
            driver.click(dialog._restart_btn)
            assert restarted == [True]
        else:
            assert restarted == []

        driver.click(dialog._close_btn)
        driver.wait(lambda: not dialog.isVisible(), f"{outcome} installer dialog to close")
    finally:
        _stop_and_dispose(dialog, qapp)
