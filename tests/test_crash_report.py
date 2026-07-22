"""Tests for privacy-safe user-generated crash report bundles."""
from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.crash_report import create_crash_report


def test_crash_report_redacts_logs_and_excludes_unrelated_user_data(tmp_path, monkeypatch):
    import urllib.request

    from core import secret_store
    from ui.settings_panel import env as settings_env

    def forbidden(*_args, **_kwargs):
        raise AssertionError("crash report touched excluded settings/keychain/network data")

    monkeypatch.setattr(secret_store, "get_secret", forbidden)
    monkeypatch.setattr(settings_env, "read_settings_env", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    log_root = tmp_path / "build_logs"
    log_dir = log_root / "wisp_crash_20260718-120000"
    log_dir.mkdir(parents=True)
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"  # secret-scan: allow
    home_path = str(tmp_path.parent / "private-user" / "notes.txt")
    (log_dir / "supervisor-crash.log").write_text(
        f"RuntimeError for person@example.com\napi_key={secret}\nopened {home_path}\n",
        encoding="utf-8",
    )
    unrelated = tmp_path / "chats" / "conversations.json"
    unrelated.parent.mkdir()
    unrelated.write_text("private chat contents", encoding="utf-8")

    report = create_crash_report(
        output_dir=tmp_path / "reports",
        log_root=log_root,
        now=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )

    with zipfile.ZipFile(report) as archive:
        names = archive.namelist()
        payload = "\n".join(archive.read(name).decode("utf-8") for name in names)
        metadata = json.loads(archive.read("report.json"))

    assert names == ["logs/01-wisp_crash_20260718-120000_supervisor-crash.log", "report.json"]
    assert secret not in payload
    assert "person@example.com" not in payload
    assert "[API_KEY]" in payload
    assert "[EMAIL]" in payload
    assert "private chat contents" not in payload
    assert metadata["format"] == 1
    assert metadata["included_logs"][0]["truncated"] is False


def test_crash_report_bounds_each_log_tail(tmp_path):
    log_root = tmp_path / "build_logs"
    log_root.mkdir()
    (log_root / "worker.log").write_text("old-data\n" + ("x" * 600_000) + "\nfinal-line", encoding="utf-8")

    report = create_crash_report(output_dir=tmp_path / "reports", log_root=log_root)

    with zipfile.ZipFile(report) as archive:
        text = archive.read("logs/01-worker.log").decode("utf-8")
    assert "old-data" not in text
    assert "final-line" in text
    assert text.startswith("[Only the final 512 KiB")


def test_settings_crash_report_action_creates_and_reveals_bundle(tmp_path, monkeypatch):
    """Settings exposes the crash bundle and tells the user where it was saved."""
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

    from core import crash_report
    from core.system import file_browser
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(["wisp-crash-report-test"])
    report = tmp_path / "wisp-crash-report.zip"
    report.write_bytes(b"zip")
    revealed: list[Path] = []
    messages: list[str] = []
    monkeypatch.setattr(SettingsDialog, "_schedule_open_status_refresh", lambda _self: None)
    monkeypatch.setattr(crash_report, "create_crash_report", lambda: report)
    monkeypatch.setattr(file_browser, "reveal_path", revealed.append)
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, message: messages.append(message),
    )

    dialog = SettingsDialog()
    try:
        button = dialog.findChild(QPushButton, "settingsCrashReportButton")
        assert button is not None
        button.click()
        assert revealed == [report]
        assert messages and str(report) in messages[0]
        assert report.name in dialog._crash_report_status_lbl.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_metadata_diagnostic_surfaces_failure_matrix_is_controlled(monkeypatch):
    """Version/update/crash/log/status/uninstall surfaces contain artifact faults."""
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    import importlib.metadata
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QMessageBox

    from core import crash_report, updater
    from runtime.supervisor.flows import FlowController
    from runtime.supervisor.runtime_log import RuntimeEventLog
    from ui import uninstall_dialog
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(["wisp-diagnostic-fault-test"])
    monkeypatch.setattr(SettingsDialog, "_schedule_open_status_refresh", lambda _self: None)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, message: warnings.append(message))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    dialog = SettingsDialog()
    runtime_log = RuntimeEventLog(max_events=30)
    faults = (
        FileNotFoundError("a required file is unavailable"),
        ConnectionError("required network access is unavailable"),
        PermissionError("a required permission is unavailable"),
        ValueError("version metadata is invalid"),
        ValueError("verification metadata is invalid"),
        PermissionError("another process locks the target"),
        OSError("disk space is low"),
        OSError("bundling cleanup fails for this function"),
        OSError("update-apply cleanup fails"),
    )

    class BrokenWorker:
        pid = None
        spec = SimpleNamespace(module="runtime.workers.broken")

        def __init__(self, error):
            self.error = error

        def stderr_tail(self, _lines):
            raise self.error

        def alive(self):
            raise self.error

    try:
        original_exists = Path.exists
        original_read_text = Path.read_text
        for failure in faults:
            # Installed-version display falls back instead of failing startup.
            with monkeypatch.context() as scoped:
                scoped.setattr(
                    importlib.metadata,
                    "version",
                    lambda *_a, error=failure, **_k: (_ for _ in ()).throw(error),
                )

                def metadata_exists(path):
                    if path.name == "pyproject.toml":
                        return True
                    return original_exists(path)

                def metadata_read(path, *args, error=failure, **kwargs):
                    if path.name == "pyproject.toml":
                        raise error
                    return original_read_text(path, *args, **kwargs)

                scoped.setattr(Path, "exists", metadata_exists)
                scoped.setattr(Path, "read_text", metadata_read)
                assert updater.current_version() == "0.0.0"

            # Update progress/error reporting accepts every failure detail.
            dialog._set_update_status("Update failed: {error}", "error", error=failure)
            assert str(failure) in dialog._update_status_lbl.text()

            # Crash report creation/review remains in Settings and re-enables its button.
            with monkeypatch.context() as scoped:
                scoped.setattr(
                    crash_report,
                    "create_crash_report",
                    lambda error=failure: (_ for _ in ()).throw(error),
                )
                dialog._create_crash_report()
            assert dialog._crash_report_btn.isEnabled()
            assert str(failure) in warnings[-1]

            # Runtime logging sanitizes the same diagnostic detail, while worker
            # status tolerates unavailable liveness/tail metadata.
            event = runtime_log.append("runtime", "error", str(failure), detail=str(failure))
            assert event["title"] == str(failure)
            row = FlowController._worker_status_row(
                object.__new__(FlowController),
                "broken",
                BrokenWorker(failure),
            )
            assert row["alive"] is False
            assert row["stderr_tail"] == ""

            # Uninstall confirmation stops before deletion when its exact plan
            # metadata cannot be built.
            with monkeypatch.context() as scoped:
                scoped.setattr(
                    uninstall_dialog.uninstaller,
                    "build_uninstall_plan",
                    lambda error=failure: (_ for _ in ()).throw(error),
                )
                assert uninstall_dialog.run_uninstall_dialog() is False
            assert str(failure) in warnings[-1]
    finally:
        runtime_log.close()
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
