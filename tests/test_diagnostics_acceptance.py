from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

pytestmark = pytest.mark.workflow


def _prepare_settings(monkeypatch, *, repo_checkout: bool, version: str = "1.2.3"):
    """Build Settings while neutralizing unrelated background host probes."""

    from core import updater
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(SettingsDialog, "_schedule_open_status_refresh", lambda _self: None)
    monkeypatch.setattr(updater, "is_repo_checkout", lambda: repo_checkout)
    monkeypatch.setattr(updater, "current_version", lambda: version)
    return SettingsDialog()


def _show_about(dialog, driver) -> None:
    dialog.show()
    driver.pump()
    driver.select_list_row(dialog._settings_nav, dialog._tab_base_names.index("About"))


def _dispose_dialog(dialog, qapp) -> None:
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize(
    ("state", "expected_mode", "expected_status"),
    [
        ("available", "download", "Version 1.3.0 is available."),
        ("no_platform_asset", "check", "no windows-x64 build was published"),
        ("up_to_date", "check", "Wisp is up to date."),
        ("error", "check", "Update check failed: release service unavailable"),
    ],
)
def test_packaged_update_check_real_button_result_matrix(
    qapp,
    monkeypatch,
    runtime_state_guard,
    state: str,
    expected_mode: str,
    expected_status: str,
):
    """Every GitHub-release check result drives the production Settings state machine."""

    del runtime_state_guard
    from core import updater
    from scripts.runtime_test_harness import QtUserDriver

    asset = updater.UpdateAsset("windows-x64", "Wisp.zip", "https://example.invalid/Wisp.zip")
    entered = threading.Event()
    release = threading.Event()

    def check_for_updates():
        entered.set()
        assert release.wait(5), "test did not release update-check worker"
        if state == "error":
            raise ConnectionError("release service unavailable")
        if state == "available":
            return updater.UpdateCheckResult("1.2.3", "1.3.0", True, asset)
        if state == "no_platform_asset":
            return updater.UpdateCheckResult("1.2.3", "1.3.0", True, None)
        return updater.UpdateCheckResult("1.2.3", "1.2.3", False, None)

    monkeypatch.setattr(updater, "check_for_updates", check_for_updates)
    monkeypatch.setattr(updater, "normalized_platform_key", lambda: "windows-x64")
    dialog = _prepare_settings(monkeypatch, repo_checkout=False)
    driver = QtUserDriver(qapp, timeout=8.0)
    try:
        _show_about(dialog, driver)
        assert dialog._update_current_lbl.text() == "Current version: 1.2.3"
        driver.click(dialog._update_btn)
        driver.wait(entered.is_set, "release check worker to start")

        assert dialog._update_running is True
        assert not dialog._update_btn.isEnabled()
        assert dialog._update_btn.text() == "Checking..."
        assert dialog._update_status_lbl.text() == "Checking for updates..."

        release.set()
        driver.wait(lambda: not dialog._update_running, "release check result to reach Settings")
        assert dialog._update_mode == expected_mode
        assert expected_status in dialog._update_status_lbl.text()
        assert dialog._update_btn.isEnabled()
    finally:
        release.set()
        _dispose_dialog(dialog, qapp)


def test_packaged_update_real_button_download_apply_retry_matrix(
    qapp,
    tmp_path: Path,
    monkeypatch,
    runtime_state_guard,
):
    """Drive check, download failure/retry, apply cancel/failure/retry, and success."""

    del runtime_state_guard
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    import ui.settings_panel.dialog as settings_ui
    from core import updater
    from scripts.runtime_test_harness import QtUserDriver

    asset = updater.UpdateAsset("windows-x64", "Wisp.zip", "https://example.invalid/Wisp.zip")
    result = updater.UpdateCheckResult("1.2.3", "1.3.0", True, asset)
    monkeypatch.setattr(updater, "check_for_updates", lambda: result)

    download_entered = threading.Event()
    download_release = threading.Event()
    download_outcome = {"error": True}
    downloaded = tmp_path / "Wisp-1.3.0.zip"

    def download_update(received_asset):
        assert received_asset == asset
        download_entered.set()
        assert download_release.wait(5), "test did not release update-download worker"
        if download_outcome["error"]:
            raise OSError("download disk is full")
        downloaded.write_bytes(b"verified packaged update")
        return downloaded

    monkeypatch.setattr(updater, "download_update", download_update)
    dialog = _prepare_settings(monkeypatch, repo_checkout=False)
    driver = QtUserDriver(qapp, timeout=8.0)
    try:
        _show_about(dialog, driver)

        driver.click(dialog._update_btn)
        driver.wait(lambda: dialog._update_mode == "download", "available update state")

        # A failed download retains the selected release and exposes a retry.
        driver.click(dialog._update_btn)
        driver.wait(download_entered.is_set, "update download worker to start")
        assert dialog._update_btn.text() == "Downloading..."
        assert dialog._update_status_lbl.text() == "Downloading update..."
        download_release.set()
        driver.wait(lambda: not dialog._update_running, "download failure to reach Settings")
        assert dialog._update_mode == "download"
        assert dialog._update_btn.text() == "Download update"
        assert "download disk is full" in dialog._update_status_lbl.text()

        # Retrying the same visible button succeeds and advances to Apply.
        download_outcome["error"] = False
        download_entered.clear()
        download_release.clear()
        driver.click(dialog._update_btn)
        driver.wait(download_entered.is_set, "retried update download worker to start")
        download_release.set()
        driver.wait(lambda: dialog._update_mode == "apply", "downloaded update to become applicable")
        assert dialog._update_download_path == downloaded
        assert dialog._update_btn.text() == "Apply update"
        assert "ready to restart" in dialog._update_status_lbl.text()

        # Cancelling confirmation preserves the verified artifact and retry action.
        monkeypatch.setattr(QMessageBox, "exec", lambda _self: QMessageBox.StandardButton.Cancel)
        monkeypatch.setattr(
            updater,
            "apply_update",
            lambda *_args: (_ for _ in ()).throw(AssertionError("cancelled update was applied")),
        )
        driver.click(dialog._update_btn)
        assert dialog._update_mode == "apply"
        assert dialog._update_download_path == downloaded
        assert dialog._update_btn.isEnabled()

        # A helper-launch failure stays in-band and permits another Apply click.
        monkeypatch.setattr(QMessageBox, "exec", lambda _self: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(
            updater,
            "apply_update",
            lambda *_args: (_ for _ in ()).throw(PermissionError("update helper blocked")),
        )
        driver.click(dialog._update_btn)
        assert "update helper blocked" in dialog._update_status_lbl.text()
        assert dialog._update_btn.isEnabled()
        assert dialog._update_btn.text() == "Apply update"

        # The final retry reaches the detached apply boundary and schedules app exit.
        applied: list[Path] = []
        quit_calls: list[bool] = []
        monkeypatch.setattr(updater, "apply_update", applied.append)
        monkeypatch.setattr(
            settings_ui.QApplication,
            "instance",
            staticmethod(lambda: SimpleNamespace(quit=lambda: quit_calls.append(True))),
        )
        monkeypatch.setattr(QTimer, "singleShot", staticmethod(lambda _delay, callback: callback()))
        driver.click(dialog._update_btn)
        assert applied == [downloaded]
        assert quit_calls == [True]
        assert not dialog._update_btn.isEnabled()
        assert dialog._update_btn.text() == "Applying..."
        assert "Wisp will close now" in dialog._update_status_lbl.text()
    finally:
        download_release.set()
        _dispose_dialog(dialog, qapp)


@pytest.mark.parametrize(
    ("state", "expected_status"),
    [
        ("updated", "Repo updated. Restart Wisp"),
        ("current", "Repo is already up to date."),
        ("error", "Repo update failed: origin is unreachable"),
    ],
)
def test_repo_update_real_button_result_matrix(
    qapp,
    tmp_path: Path,
    monkeypatch,
    runtime_state_guard,
    state: str,
    expected_status: str,
):
    """Pull latest reports changed/current/error states through its real Qt worker."""

    del runtime_state_guard
    from core import updater
    from scripts.runtime_test_harness import QtUserDriver

    entered = threading.Event()
    release = threading.Event()

    def apply_repo_update():
        entered.set()
        assert release.wait(5), "test did not release repo-update worker"
        if state == "error":
            raise ConnectionError("origin is unreachable")
        return updater.RepoUpdateResult(
            repo_root=tmp_path,
            before="a" * 40,
            after=("b" if state == "updated" else "a") * 40,
            updated=state == "updated",
            output="Fast-forward" if state == "updated" else "Already up to date.",
        )

    monkeypatch.setattr(updater, "apply_repo_update", apply_repo_update)
    dialog = _prepare_settings(monkeypatch, repo_checkout=True)
    driver = QtUserDriver(qapp, timeout=8.0)
    try:
        _show_about(dialog, driver)
        assert dialog._update_mode == "repo"
        driver.click(dialog._update_btn)
        driver.wait(entered.is_set, "repo pull worker to start")
        assert dialog._update_btn.text() == "Pulling..."
        assert dialog._update_status_lbl.text() == "Pulling latest from origin/main..."
        assert not dialog._update_btn.isEnabled()

        release.set()
        driver.wait(lambda: not dialog._update_running, "repo pull result to reach Settings")
        assert expected_status in dialog._update_status_lbl.text()
        assert dialog._update_mode == "repo"
        assert dialog._update_btn.text() == "Pull latest"
        assert dialog._update_btn.isEnabled()
    finally:
        release.set()
        _dispose_dialog(dialog, qapp)


def test_crash_report_real_settings_button_creates_reveals_and_reviews_safe_zip(
    qapp,
    tmp_path: Path,
    monkeypatch,
    runtime_state_guard,
):
    """Create the actual bounded archive through Settings and inspect its contents."""

    del runtime_state_guard
    from PySide6.QtWidgets import QMessageBox

    from core import crash_report, secret_store
    from core.system import file_browser
    from scripts.runtime_test_harness import QtUserDriver
    from ui.settings_panel import env as settings_env

    data_root = tmp_path / "Wisp"
    log_root = data_root / "build_logs"
    log_root.mkdir(parents=True)
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"  # secret-scan: allow
    (log_root / "runtime.log").write_text(
        f"person@example.com api_key={secret} opened {tmp_path / 'private' / 'notes.txt'}\n",
        encoding="utf-8",
    )
    (log_root / "huge.log").write_text("discard-me\n" + ("x" * 600_000) + "\nfinal-tail", encoding="utf-8")
    excluded = {
        "chats/chat.json": "private chat",
        "memory/facts.db": "private memory",
        "settings.env": "PRIVATE_SETTING=yes",
        "keychain/secrets.json": "private keychain",
    }
    for relative, contents in excluded.items():
        path = data_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    monkeypatch.setattr(crash_report, "USER_DATA_DIR", data_root)
    dialog = _prepare_settings(monkeypatch, repo_checkout=False)
    driver = QtUserDriver(qapp, timeout=8.0)
    revealed: list[Path] = []
    messages: list[str] = []
    try:
        _show_about(dialog, driver)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("crash-report action touched excluded credential/settings storage")

        monkeypatch.setattr(secret_store, "get_secret", forbidden)
        monkeypatch.setattr(settings_env, "read_settings_env", forbidden)
        monkeypatch.setattr(file_browser, "reveal_path", revealed.append)
        monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text: messages.append(text))

        driver.click(dialog._crash_report_btn)
        assert len(revealed) == 1
        report = revealed[0]
        assert report.is_file() and report.parent == data_root / "crash_reports"
        assert messages and "Review the ZIP" in messages[0]
        assert str(report) in messages[0]
        assert report.name in dialog._crash_report_status_lbl.text()
        assert dialog._crash_report_btn.isEnabled()

        with zipfile.ZipFile(report) as archive:
            names = archive.namelist()
            text_entries = {
                name: archive.read(name).decode("utf-8")
                for name in names
            }
            metadata = json.loads(text_entries["report.json"])

        payload = "\n".join(text_entries.values())
        assert set(names) == {
            "logs/01-huge.log",
            "logs/02-runtime.log",
            "report.json",
        }
        assert text_entries["logs/01-huge.log"].startswith("[Only the final 512 KiB")
        assert "discard-me" not in payload
        assert "final-tail" in payload
        assert secret not in payload
        assert "person@example.com" not in payload
        assert "[API_KEY]" in payload and "[EMAIL]" in payload
        for contents in excluded.values():
            assert contents not in payload
        assert "Chats, memory databases, settings files, environment variables, and keychain data are not collected" in metadata["privacy"]
        assert "Review this archive before sharing" in metadata["privacy"]
    finally:
        _dispose_dialog(dialog, qapp)


class _RuntimeWorker:
    def __init__(self, name: str, *, alive: bool, pid: int | None) -> None:
        self.name = name
        self.pid = pid
        self.spec = SimpleNamespace(module=f"runtime.workers.{name}_host")
        self._alive = alive
        self.events: dict[str, Any] = {}

    def alive(self) -> bool:
        return self._alive

    def stderr_tail(self, _lines: int) -> str:
        return ""

    def on_event(self, event: str, handler) -> None:
        self.events[event] = handler

    def call(self, _method: str, _params=None, *, timeout: float = 30.0, wait: bool = True):
        del timeout, wait
        return {}

    def call_with_events(self, *_args, **_kwargs):
        return {}


class _RoutedUiWorker(_RuntimeWorker):
    def __init__(self, host) -> None:
        super().__init__("ui", alive=True, pid=202)
        self.host = host

    def call(self, method: str, params=None, *, timeout: float = 30.0, wait: bool = True):
        del timeout, wait
        if method.startswith("ui.runtime_status."):
            return self.host._dispatch(method, params or {})
        return {}


def test_tray_runtime_status_real_supervisor_to_window_workflow(
    qapp,
    tmp_path: Path,
    monkeypatch,
    runtime_state_guard,
):
    """Click the production tray action and route it through supervisor and UI host."""

    del runtime_state_guard
    from PySide6.QtWidgets import QLabel, QPushButton, QTreeWidget

    from runtime.supervisor.flows import FlowController
    from runtime.supervisor.runtime_log import RuntimeEventLog
    from runtime.workers import ui_host
    from scripts.runtime_test_harness import QtUserDriver
    from ui.overlay import IconOverlay, OverlaySignals

    monkeypatch.setenv("WISP_MACOS_PY_UI_HOST", "1")
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(tmp_path / "run-logs"))
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda _self: None)
    opened_folders: list[str] = []
    monkeypatch.setattr(ui_host, "_open_folder", opened_folders.append)

    host = ui_host.QtProtocolHost.__new__(ui_host.QtProtocolHost)
    host._runtime_status_dialog = None
    host._runtime_status_snapshot = None
    routed_ui = _RoutedUiWorker(host)
    runtime_log = RuntimeEventLog(max_events=50)
    runtime_log._installer_status_files = staticmethod(lambda: [])
    controller = FlowController(
        native=_RuntimeWorker("native", alive=True, pid=101),
        ui=routed_ui,
        brain=_RuntimeWorker("brain", alive=False, pid=None),
        audio=_RuntimeWorker("audio", alive=True, pid=303),
        run_async=False,
        runtime_log=runtime_log,
    )
    controller.start()
    emitted: list[str] = []

    def emit(event: str, data=None, req_id=None) -> None:
        emitted.append(event)
        handler = routed_ui.events.get(event)
        if handler is not None:
            handler(data or {}, req_id)

    host.emit = emit
    signals = OverlaySignals()
    signals.show_runtime_status.connect(lambda: host.emit("ui.runtime_status.open_requested", {}))
    overlay = IconOverlay(signals)
    driver = QtUserDriver(qapp, timeout=8.0)
    try:
        runtime_log.append(
            "brain",
            "error",
            "brain encountered an error: model process exited",
            detail="Traceback line\nRecommended action: restart the model worker",
        )
        action = next(item for item in overlay._tray_menu.actions() if item.text() == "Runtime Status")
        assert action.isEnabled()
        action.trigger()
        driver.pump()
        assert emitted and emitted[0] == "ui.runtime_status.open_requested"
        driver.wait(
            lambda: host._runtime_status_dialog is not None and host._runtime_status_dialog.isVisible(),
            "runtime status dialog to open from tray action",
        )

        dialog = host._runtime_status_dialog
        summary = dialog.findChild(QLabel, "runtimeWorkerSummary")
        tree = dialog.findChild(QTreeWidget)
        assert summary is not None and tree is not None
        assert "[native] running  pid=101" in summary.text()
        assert "[ui] running  pid=202" in summary.text()
        assert "[brain] stopped  pid=-" in summary.text()
        assert "[audio] running  pid=303" in summary.text()
        assert tree.topLevelItemCount() == 1
        assert "model process exited" in tree.topLevelItem(0).text(0)
        assert tree.topLevelItem(0).childCount() == 2
        assert runtime_log._publish_enabled is True
        assert "ui.runtime_status.open_requested" in emitted
        assert "ui.runtime_status.opened" in emitted

        # Once opened, new runtime events stream through the production publisher.
        runtime_log.append("audio", "warning", "microphone became unavailable", detail="device busy")
        runtime_log._flush()
        driver.wait(lambda: tree.topLevelItemCount() == 2, "new runtime event to stream into the tree")

        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        driver.click(buttons["Copy all"])
        clipboard = qapp.clipboard().text()
        assert "[native] running pid=101" in clipboard
        assert "model process exited" in clipboard
        assert "microphone became unavailable" in clipboard
        assert "    device busy" in clipboard

        driver.click(buttons["Open log folder"])
        assert opened_folders == [str(tmp_path / "run-logs")]

        driver.click(buttons["Refresh"])
        driver.wait(lambda: emitted.count("ui.runtime_status.open_requested") == 2, "runtime status refresh event")
        assert tree.topLevelItemCount() == 2

        driver.click(buttons["Close"])
        driver.wait(lambda: host._runtime_status_dialog is None, "runtime status dialog to close")
        driver.wait(lambda: runtime_log._publish_enabled is False, "runtime status publisher to stop")
        assert "ui.runtime_status.closed" in emitted
    finally:
        if host._runtime_status_dialog is not None:
            host._runtime_status_dialog.close()
            host._runtime_status_dialog.deleteLater()
        runtime_log.close()
        overlay._tray.hide()
        for widget_name in ("_bubble", "_context_panel", "_provider_badge", "_icon_label"):
            widget = getattr(overlay, widget_name, None)
            if widget is not None:
                widget.close()
                widget.deleteLater()
        overlay.close()
        overlay.deleteLater()
        qapp.processEvents()


def _make_uninstall_plan_roots(tmp_path: Path, *, source: bool):
    """Build a production uninstall plan entirely inside pytest's temp root."""

    from core import uninstaller

    label = "source" if source else "release"
    home = tmp_path / label / "home"
    data = tmp_path / label / "data" / "Wisp"
    optional = data / "python_packages"
    hub = tmp_path / label / "hf-hub"
    model = hub / "models--hexgrad--Kokoro-82M"
    for relative in ("chats", "memory", "addons", "tools", "logs", "updates"):
        path = data / relative
        path.mkdir(parents=True, exist_ok=True)
        (path / "owned.txt").write_text(relative, encoding="utf-8")
    optional.mkdir(parents=True)
    (optional / "package.txt").write_text("optional", encoding="utf-8")
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"model")
    home.mkdir(parents=True)

    common = {
        "platform": sys.platform,
        "user_data_root": data,
        "optional_packages_root": optional,
        "home": home,
        "environ": {"HF_HUB_CACHE": str(hub)},
    }
    if source:
        app_root = tmp_path / label / "checkout" / "Wisp"
        for relative in ("pyproject.toml", "runtime/supervisor/app.py", "core/system/paths.py"):
            path = app_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# owned source\n", encoding="utf-8")
        plan = uninstaller.build_uninstall_plan(frozen=False, source_root=app_root, **common)
    else:
        if sys.platform == "darwin":
            executable = tmp_path / label / "Wisp.app" / "Contents" / "MacOS" / "Wisp"
        else:
            executable = tmp_path / label / "Wisp" / ("Wisp.exe" if sys.platform == "win32" else "wisp")
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"packaged app")
        plan = uninstaller.build_uninstall_plan(frozen=True, executable=executable, **common)
        app_root = plan.app_root
    return plan, app_root, data, model


def test_settings_uninstall_exact_plan_and_isolated_self_removing_helper_matrix(
    qapp,
    tmp_path: Path,
    monkeypatch,
    runtime_state_guard,
):
    """Cancel and confirm release/source plans, executing helpers only in temp trees."""

    del runtime_state_guard
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    from core import uninstaller, updater
    from scripts.runtime_test_harness import QtUserDriver

    release_plan, release_app, release_data, release_model = _make_uninstall_plan_roots(tmp_path, source=False)
    source_plan, source_app, source_data, source_model = _make_uninstall_plan_roots(tmp_path, source=True)
    assert source_plan.source_checkout is True
    assert release_plan.source_checkout is False
    assert source_app in source_plan.targets
    assert source_app not in release_plan.targets

    helper_parent = tmp_path / "uninstall-helpers"
    helper_parent.mkdir()
    monkeypatch.setattr(uninstaller.tempfile, "gettempdir", lambda: str(helper_parent))
    monkeypatch.setattr(uninstaller, "remove_wisp_keychain_entries", lambda: [])
    monkeypatch.setattr(updater, "wisp_wait_pid", lambda _pid=None: 2_147_483_647)

    helper_scripts: list[str] = []

    def run_isolated_helper(command, *, cwd):
        script_path = Path(command[-1] if sys.platform == "win32" else command[0])
        helper_scripts.append(script_path.read_text(encoding="utf-8"))
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=20, check=False)
        assert completed.returncode == 0, completed.stderr or completed.stdout
        return True

    monkeypatch.setattr(updater, "launch_detached_helper", run_isolated_helper)

    selected = {"plan": release_plan}
    monkeypatch.setattr(uninstaller, "build_uninstall_plan", lambda: selected["plan"])
    dialog = _prepare_settings(monkeypatch, repo_checkout=False)
    driver = QtUserDriver(qapp, timeout=8.0)
    answers = iter(
        [
            QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Yes,
        ]
    )
    confirmations: list[str] = []
    completion_messages: list[str] = []
    quit_schedules: list[int] = []

    def confirm(message_box):
        confirmations.append(message_box.informativeText())
        return next(answers)

    monkeypatch.setattr(QMessageBox, "exec", confirm)
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text: completion_messages.append(text))
    monkeypatch.setattr(QTimer, "singleShot", staticmethod(lambda delay, _callback: quit_schedules.append(delay)))
    try:
        _show_about(dialog, driver)

        # First confirmation is declined: no helper exists and nothing is removed.
        driver.click(dialog._uninstall_btn)
        assert helper_scripts == []
        assert release_app.exists() and release_data.exists() and release_model.exists()

        # Confirm the same packaged plan and run its real remover against temp roots.
        driver.click(dialog._uninstall_btn)
        assert len(helper_scripts) == 1
        assert not release_app.exists()
        assert not release_data.exists()
        assert not release_model.exists()

        # Source checkout is a distinct confirmed branch and includes its warning/path.
        selected["plan"] = source_plan
        driver.click(dialog._uninstall_btn)
        assert len(helper_scripts) == 2
        assert not source_app.exists()
        assert not source_data.exists()
        assert not source_model.exists()

        for index, plan in enumerate((release_plan, release_plan, source_plan)):
            text = confirmations[index]
            assert str(plan.app_root) in text
            assert str(plan.user_data_root) in text
            assert "Exact paths scheduled for deletion:" in text
            for target in plan.targets:
                assert str(target) in text
        assert "source checkout will be deleted" not in confirmations[0]
        assert "source checkout will be deleted" in confirmations[2]
        assert len(completion_messages) == 2
        assert quit_schedules == [0, 0]

        if sys.platform == "win32":
            assert all("while (Get-Process -Id $waitPid" in script for script in helper_scripts)
            assert all("Remove-Item -LiteralPath $PSCommandPath" in script for script in helper_scripts)
        else:
            assert all('while kill -0 "$wait_pid"' in script for script in helper_scripts)
            assert all('rm -f -- "$0"' in script for script in helper_scripts)
    finally:
        _dispose_dialog(dialog, qapp)
        shutil.rmtree(helper_parent, ignore_errors=True)
