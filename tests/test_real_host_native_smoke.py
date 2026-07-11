"""Opt-in tests for real OS integration that cannot be proven with fakes.

These tests intentionally touch the host desktop. They are skipped by default
and enabled through scripts/run_app_workflow_tests.py --real-host.
"""
from __future__ import annotations

import base64
import os
import sys
import threading
import time
import uuid

import pytest

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.real_host,
    pytest.mark.skipif(
        os.environ.get("WISP_RUN_REAL_HOST_TESTS") != "1",
        reason="set WISP_RUN_REAL_HOST_TESTS=1 or use --real-host",
    ),
]


def _qapp_real_display():
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["wisp-real-host-tests"])
    platform_name = app.platformName().lower()
    if platform_name == "offscreen":
        pytest.fail("real-host tests require a real Qt platform, not QT_QPA_PLATFORM=offscreen")
    return app


def _pump_until(app, predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    app.processEvents()
    assert predicate()


def _focus_real_qt_widget(app, widget, *, timeout: float = 3.0) -> bool:
    """Try to make a real Qt widget the focused keyboard target."""
    if sys.platform == "darwin":
        from core.platform_utils import activate_self

        activate_self()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        widget.show()
        widget.raise_()
        widget.activateWindow()
        window_handle = widget.windowHandle()
        if window_handle is not None:
            window_handle.requestActivate()
        widget.setFocus()
        app.processEvents()
        if widget.hasFocus():
            return True
        time.sleep(0.05)
    app.processEvents()
    return bool(widget.hasFocus())


def test_real_host_macos_permission_snapshot_is_ready_for_native_checks():
    """macOS privacy grants are visible before tests depend on native APIs."""
    if sys.platform != "darwin":
        pytest.skip("macOS-only permission preflight")

    from runtime.workers import native_host

    snapshot = native_host.permissions_snapshot()
    missing: list[str] = []
    screen = snapshot.get("screen_recording")
    if screen is False:
        missing.append("Screen Recording")
    if os.environ.get("WISP_RUN_REAL_HOST_INTERACTIVE_TESTS") == "1":
        accessibility = snapshot.get("accessibility")
        if accessibility is False:
            missing.append("Accessibility")
    microphone = snapshot.get("microphone")
    if microphone == "denied":
        missing.append("Microphone")

    if missing:
        pytest.fail(
            "macOS permission preflight failed for the process running pytest "
            f"({sys.executable}). Missing: {', '.join(missing)}. "
            "Open System Settings > Privacy & Security and grant the permission "
            "to the launcher you used for this test, such as Terminal, Codex, or Python. "
            "A packaged Wisp.app has its own separate macOS permission entries."
        )


def test_real_host_clipboard_and_context_snapshot_roundtrip():
    """The real clipboard path feeds the same native context snapshot users send."""
    from runtime.workers import native_host

    original = native_host.clipboard_get().get("text", "")
    marker = f"wisp-real-host-clipboard-{uuid.uuid4()}"
    try:
        assert native_host.clipboard_set(marker)["ok"] is True
        deadline = time.monotonic() + 2.0
        snapshot = {}
        while time.monotonic() < deadline:
            if native_host.clipboard_get()["text"] != marker:
                native_host.clipboard_set(marker)
                time.sleep(0.1)
                continue
            snapshot = native_host.context_snapshot(
                include_clipboard=True,
                include_selection=False,
                include_browser_content=False,
                include_browser_url=False,
            )
            if snapshot.get("clipboard_text") == marker:
                break
            native_host.clipboard_set(marker)
            time.sleep(0.1)

        assert snapshot["platform"]
        assert snapshot["clipboard_text"] == marker, (
            "real clipboard changed while context_snapshot was reading it; "
            f"got {snapshot.get('clipboard_text', '')[:240]!r}"
        )
        assert isinstance(snapshot["active_app"], dict)
        assert set(snapshot["screen_size"]) == {"width", "height"}
    finally:
        native_host.clipboard_set(original)


def test_real_host_screenshot_capture_returns_pixels():
    """The actual screen-capture backend can capture non-empty pixels."""
    from core.capture import get_screen_snippet, image_to_base64

    try:
        image = get_screen_snippet()
    except Exception as exc:
        if sys.platform == "darwin":
            hint = (
                "On macOS, grant Screen Recording to the launcher running pytest "
                "(Terminal, Codex, or Python). A packaged Wisp.app has separate "
                "permission entries."
            )
        elif sys.platform == "win32":
            hint = (
                "On Windows, this usually means the test process cannot capture "
                "the current desktop session, such as a locked, secure, remote, "
                "or protected desktop."
            )
        else:
            hint = "Make sure the test is running in a real graphical desktop session."
        pytest.fail(f"real screen capture failed: {exc!r}. {hint}")
    assert image.width > 0
    assert image.height > 0
    assert image_to_base64(image)


def test_real_host_desktop_capture_flows_through_real_workers(tmp_path):
    """Actual desktop pixels cross FlowController policy and real worker IPC."""
    from runtime.supervisor.flows import FlowController, PendingInvocation
    from runtime.supervisor.ipc import WispSupervisor, default_specs

    specs = default_specs()
    for spec in specs.values():
        spec.env = {
            **spec.env,
            "WISP_ADDONS_DIR": str(tmp_path / "addons"),
            "WISP_BRAIN_FAKE_LLM": "1",
            "WISP_RUN_LOG_DIR": str(tmp_path / "logs"),
        }
    supervisor = WispSupervisor(specs)
    flow = FlowController(
        native=supervisor.workers["native"],
        ui=supervisor.workers["ui"],
        brain=supervisor.workers["brain"],
        audio=supervisor.workers["audio"],
        run_async=False,
    )
    capture_path = tmp_path / "real-desktop.png"
    events = []
    try:
        captured = supervisor.call(
            "native",
            "native.capture.fullscreen",
            {"path": str(capture_path)},
            timeout=30,
        )
        assert captured.get("ok") is True, captured
        assert capture_path.is_file()
        assert capture_path.stat().st_size > 0

        screenshot_b64 = flow._file_b64(capture_path)
        assert screenshot_b64
        assert base64.b64decode(screenshot_b64).startswith(b"\x89PNG\r\n\x1a\n")

        pending = PendingInvocation(
            caller={
                "context_ambient": False,
                "context_clipboard": False,
                "context_documents_mode": "off",
                "context_browser_mode": "off",
                "context_github_mode": "model",
                "context_memory_mode": "off",
                "context_screenshot": "auto",
                "file_access": "off",
                "tools": {"git_status": "on", "git_diff": "off"},
            },
            context={"platform": sys.platform},
            screenshot_b64=screenshot_b64,
        )
        params = flow._brain_query_params("Inspect this real desktop capture.", pending)
        assert params["screenshot_b64"] == screenshot_b64
        assert "git_status" in params["allowed_tools"]
        assert "git_diff" not in params["allowed_tools"]
        assert params["use_tools"] is True

        params.pop("_ui_context_summary", None)
        params.pop("context_policy", None)
        reply = supervisor.workers["brain"].call_with_events(
            "brain.query",
            params,
            timeout=30,
            on_event=lambda event, data, req_id: events.append((event, data, req_id)),
        )
        assert reply["text"].startswith("[fake-llm]")
        assert "Inspect this real desktop capture." in reply["text"]
        assert any(event == "reply.chunk" for event, _data, _req_id in events)
        assert [data for event, data, _req_id in events if event == "reply.done"] == [reply]
    finally:
        supervisor.shutdown()


@pytest.mark.skipif(
    os.environ.get("WISP_RUN_REAL_HOST_AUDIO_TESTS") != "1",
    reason="set WISP_RUN_REAL_HOST_AUDIO_TESTS=1 to use the real microphone",
)
def test_real_host_voice_start_captures_desktop_after_microphone_starts(tmp_path, monkeypatch):
    """Real push-to-talk start captures desktop pixels while the microphone is live."""
    import config
    from runtime.supervisor.flows import FlowController, PendingInvocation
    from runtime.supervisor.ipc import WispSupervisor, default_specs

    test_env = tmp_path / ".env"
    test_env.write_text(
        "\n".join(
            [
                "VOICE_CONTEXT_AMBIENT=false",
                "VOICE_CONTEXT_CLIPBOARD=false",
                "VOICE_CONTEXT_DOCUMENTS_MODE=off",
                "VOICE_CONTEXT_BROWSER_MODE=off",
                "VOICE_CONTEXT_GITHUB_MODE=off",
                "VOICE_CONTEXT_MEMORY_MODE=off",
                "VOICE_CONTEXT_SCREENSHOT=auto",
                "VOICE_FILE_ACCESS=off",
                "VOICE_TOOLS=capture_screen:off",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "_ENV_FILE", test_env)
    config.reload()

    specs = default_specs()
    for spec in specs.values():
        spec.env = {
            **spec.env,
            "WISP_ADDONS_DIR": str(tmp_path / "addons"),
            "WISP_ADDON_STORE": str(tmp_path / "addons.json"),
            "WISP_BRAIN_FAKE_LLM": "1",
            "WISP_RUN_LOG_DIR": str(tmp_path / "logs"),
            "TEMP": str(tmp_path),
            "TMP": str(tmp_path),
            "TMPDIR": str(tmp_path),
        }
    supervisor = WispSupervisor(specs)
    supervisor.call("ui", "ui.ping", timeout=20)
    supervisor.call("ui", "ui.overlay.state", {"state": "idle"}, timeout=20)
    flow = FlowController(
        native=supervisor.workers["native"],
        ui=supervisor.workers["ui"],
        brain=supervisor.workers["brain"],
        audio=supervisor.workers["audio"],
        run_async=False,
    )
    events = []
    try:
        assert flow._voice_caller()["context_screenshot"] == "auto"
        flow.voice_start()

        assert flow._voice_state == "recording"
        assert flow._voice_caller()["context_screenshot"] == "auto"
        screenshot_b64 = flow._voice_screenshot_b64
        if not screenshot_b64:
            pytest.fail(
                "voice-start capture returned no readable payload; "
                f"native probe={supervisor.call('native', 'native.capture.fullscreen', timeout=30)!r}"
            )
        assert base64.b64decode(screenshot_b64).startswith(b"\x89PNG\r\n\x1a\n")

        pending = PendingInvocation(
            caller=flow._voice_caller(),
            context=flow._voice_context,
            screenshot_b64=screenshot_b64,
        )
        params = flow._brain_query_params("Describe the screen captured during voice start.", pending)
        assert params["screenshot_b64"] == screenshot_b64

        params.pop("_ui_context_summary", None)
        params.pop("context_policy", None)
        reply = supervisor.workers["brain"].call_with_events(
            "brain.query",
            params,
            timeout=30,
            on_event=lambda event, data, req_id: events.append((event, data, req_id)),
        )
        assert reply["text"].startswith("[fake-llm]")
        assert any(event == "reply.chunk" for event, _data, _req_id in events)
    finally:
        supervisor.shutdown()
        monkeypatch.undo()
        config.reload()


def test_real_host_qt_screen_and_tray_capability():
    """Qt can attach to the real desktop, grab pixels, and expose tray support."""
    from PySide6.QtWidgets import QSystemTrayIcon

    app = _qapp_real_display()
    screen = app.primaryScreen()
    assert screen is not None
    capture = screen.grabWindow(0, 0, 0, 64, 64)
    assert not capture.isNull()
    assert capture.width() > 0 and capture.height() > 0

    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    if not tray_available and os.environ.get("WISP_REAL_HOST_ALLOW_NO_TRAY") != "1":
        pytest.fail("system tray is not available; set WISP_REAL_HOST_ALLOW_NO_TRAY=1 to accept this host limitation")


@pytest.mark.skipif(
    os.environ.get("WISP_RUN_REAL_HOST_INTERACTIVE_TESTS") != "1",
    reason="use --real-host-interactive to synthesize paste into a focused test window",
)
def test_real_host_interactive_paste_shortcut_targets_focused_qt_field():
    """Paste-back input injection reaches the focused test field and preserves clipboard."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTextEdit

    from core.platform_utils import PASTE_COMBO, send_keys
    from runtime.workers import native_host

    app = _qapp_real_display()
    original = native_host.clipboard_get().get("text", "")
    marker = f"wisp-real-host-paste-{uuid.uuid4()}"
    edit = QTextEdit()
    edit.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    edit.resize(420, 180)
    try:
        assert native_host.clipboard_set(marker)["ok"] is True
        edit.show()
        if not _focus_real_qt_widget(app, edit, timeout=5.0):
            if sys.platform == "darwin":
                pytest.skip(
                    "macOS did not grant keyboard focus to the pytest Qt test window. "
                    "This usually means the launcher is running in a remote/off-console "
                    "session or another app is retaining focus; paste injection cannot "
                    "be verified on this host session."
                )
            pytest.fail("real host test window did not receive keyboard focus")

        send_keys(PASTE_COMBO)
        _pump_until(app, lambda: marker in edit.toPlainText(), timeout=3.0)
    finally:
        native_host.clipboard_set(original)
        edit.close()
        edit.deleteLater()
        app.processEvents()


@pytest.mark.skipif(
    os.environ.get("WISP_RUN_REAL_HOST_INTERACTIVE_TESTS") != "1",
    reason="use --real-host-interactive to register and unregister real global hotkeys",
)
def test_real_host_interactive_hotkey_backend_starts_and_stops():
    """The real hotkey backend can register the configured keys and cleanly stop."""
    from runtime.workers import native_host

    started = native_host.hotkeys_start(addon_hotkeys=[])
    try:
        assert started.get("ok") is True
        assert started.get("started") is True
    finally:
        stopped = native_host.hotkeys_stop()
        assert stopped.get("ok") is True
        assert stopped.get("stopped") is True


@pytest.mark.skipif(
    os.environ.get("WISP_RUN_REAL_HOST_INTERACTIVE_TESTS") != "1",
    reason="use --real-host-interactive to inject a registered global hotkey",
)
def test_real_host_interactive_hotkey_reaches_native_worker_event():
    """A real registered global hotkey crosses the native worker event pipe."""
    from core.platform_utils import send_keys
    from runtime.supervisor.ipc import WorkerClient, WorkerSpec

    combo = "ctrl+alt+shift+u"
    received = []
    event_received = threading.Event()
    worker = WorkerClient(WorkerSpec("real-host-native", "runtime.workers.native_host", "native"))

    def record_hotkey(data, _req_id):
        if data == {"kind": "addon", "addon_id": "real-host-test", "hotkey_id": "roundtrip"}:
            received.append(data)
            event_received.set()

    worker.on_event("native.hotkey", record_hotkey)
    try:
        started = worker.call(
            "native.hotkeys.start",
            {
                "addon_hotkeys": [
                    {
                        "hotkey": combo,
                        "addon_id": "real-host-test",
                        "id": "roundtrip",
                    }
                ]
            },
            timeout=20,
        )
        assert started.get("ok") is True, started
        assert started.get("started") is True, started

        time.sleep(0.25)
        send_keys(combo)
        assert event_received.wait(5), (
            f"registered hotkey {combo!r} did not reach the native worker; "
            "verify the launcher has input/accessibility permission and no other Wisp instance is running"
        )
        assert received == [
            {"kind": "addon", "addon_id": "real-host-test", "hotkey_id": "roundtrip"}
        ]
    finally:
        try:
            worker.call("native.hotkeys.stop", timeout=10)
        finally:
            worker.shutdown()
