"""Tests for supervisor app startup dispatch and runtime log modes."""

import os
from pathlib import Path

import pytest

from runtime.supervisor import app as supervisor_app


@pytest.fixture(autouse=True)
def _isolate_optional_install_maintenance(monkeypatch):
    """App-main tests must not maintain the developer's real package layer."""
    from scripts import optional_tts_installer

    monkeypatch.setattr(optional_tts_installer, "resume_pending_staged_applies", lambda: 0)
    monkeypatch.setattr(optional_tts_installer, "cleanup_stale_optional_package_swaps", lambda: ([], {}))


def test_optional_install_startup_maintenance_resumes_before_cleanup(monkeypatch):
    """Supervisor startup resumes owned apply plans before pruning stale swaps."""
    from scripts import optional_tts_installer

    events = []
    monkeypatch.setattr(optional_tts_installer, "resume_pending_staged_applies", lambda: events.append("resume") or 0)
    monkeypatch.setattr(
        optional_tts_installer,
        "cleanup_stale_optional_package_swaps",
        lambda: events.append("cleanup") or ([], {}),
    )

    supervisor_app._resume_staged_optional_installs()

    assert events == ["resume", "cleanup"]


def test_dispatch_module_mode_runs_requested_worker_module(monkeypatch):
    calls = []
    monkeypatch.setattr(supervisor_app.sys, "argv", ["Wisp.exe", "-m", "runtime.workers.audio_host", "--flag"])
    monkeypatch.setattr(supervisor_app.runpy, "run_module", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(SystemExit) as exc:
        supervisor_app._dispatch_module_mode()

    assert exc.value.code == 0
    assert calls == [
        (
            ("runtime.workers.audio_host",),
            {"run_name": "__main__", "alter_sys": True},
        )
    ]
    assert supervisor_app.sys.argv == ["runtime.workers.audio_host", "--flag"]


def test_runtime_log_mode_defaults_to_crash(tmp_path, monkeypatch):
    """Verify normal runs do not create debug runtime logs by default."""
    monkeypatch.delenv("WISP_RUNTIME_LOG_MODE", raising=False)
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)

    assert supervisor_app._runtime_log_mode() == "crash"


def test_runtime_log_mode_debug_env_enables_debug_logs(monkeypatch):
    """Verify debug launchers opt in to persistent runtime logs."""
    monkeypatch.setenv("WISP_RUNTIME_LOG_MODE", "debug")
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)

    assert supervisor_app._runtime_log_mode() == "debug"


def test_runtime_log_mode_frozen_defaults_to_debug_logs(monkeypatch):
    """Verify packaged no-console builds keep persistent runtime logs by default."""
    monkeypatch.delenv("WISP_RUNTIME_LOG_MODE", raising=False)
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.setattr(supervisor_app.sys, "frozen", True, raising=False)

    assert supervisor_app._runtime_log_mode() == "debug"


def test_runtime_log_mode_explicit_crash_overrides_frozen_default(monkeypatch):
    """Verify explicit crash logging mode still works for packaged builds."""
    monkeypatch.setenv("WISP_RUNTIME_LOG_MODE", "crash")
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.setattr(supervisor_app.sys, "frozen", True, raising=False)

    assert supervisor_app._runtime_log_mode() == "crash"


def test_prepare_run_log_dir_sets_env_and_latest_pointer(tmp_path, monkeypatch):
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.setattr(supervisor_app, "repo_root", lambda: tmp_path)

    log_dir = supervisor_app._prepare_run_log_dir()

    assert log_dir.is_dir()
    assert log_dir.parent == tmp_path / "build_logs"
    assert Path(supervisor_app.os.environ["WISP_RUN_LOG_DIR"]) == log_dir
    assert (tmp_path / "build_logs" / "latest_wisp_runtime.txt").read_text(encoding="utf-8") == str(log_dir)


def test_prepare_crash_log_dir_does_not_enable_worker_logs(tmp_path, monkeypatch):
    """Verify crash-only log dirs do not turn on worker stderr file logging."""
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.setattr(supervisor_app, "repo_root", lambda: tmp_path)

    log_dir = supervisor_app._prepare_run_log_dir(reason="crash", expose_to_workers=False)

    assert log_dir.is_dir()
    assert log_dir.name.startswith("wisp_crash_")
    assert "WISP_RUN_LOG_DIR" not in supervisor_app.os.environ
    assert (tmp_path / "build_logs" / "latest_wisp_runtime.txt").read_text(encoding="utf-8") == str(log_dir)


def test_prepare_run_log_dir_respects_existing_env(tmp_path, monkeypatch):
    configured = tmp_path / "custom-logs"
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(configured))

    log_dir = supervisor_app._prepare_run_log_dir()

    assert log_dir == configured
    assert configured.is_dir()


def test_prune_runtime_logs_removes_wisp_logs_older_than_retention(tmp_path):
    """Verify runtime log pruning removes only expired Wisp log artifacts."""
    log_root = tmp_path / "build_logs"
    old_runtime = log_root / "wisp_runtime_20260101-010101"
    old_crash = log_root / "wisp_crash_20260101-010101"
    fresh_runtime = log_root / "wisp_runtime_20260108-010101"
    unrelated = log_root / "app_workflow_tests_20260101"
    ui_log = log_root / "ui_runtime" / "ui_freeze_20260101-010101.log"
    for path in (old_runtime, old_crash, fresh_runtime, unrelated, ui_log.parent):
        path.mkdir(parents=True, exist_ok=True)
    for path in (old_runtime, old_crash, fresh_runtime, unrelated):
        (path / "sample.log").write_text("log\n", encoding="utf-8")
    ui_log.write_text("ui log\n", encoding="utf-8")

    now = 1_800_000_000.0
    cutoff = now - (supervisor_app.RUNTIME_LOG_RETENTION_DAYS * 24 * 60 * 60)
    old_time = cutoff - 1
    for path in (old_runtime, old_crash, unrelated, ui_log):
        os.utime(path, (old_time, old_time))
    os.utime(fresh_runtime, (cutoff, cutoff))

    assert supervisor_app._prune_runtime_logs(log_root, now=now) == 3
    assert not old_runtime.exists()
    assert not old_crash.exists()
    assert not ui_log.exists()
    assert fresh_runtime.exists()
    assert unrelated.exists()


def test_prepare_run_log_dir_prunes_expired_runtime_logs(tmp_path, monkeypatch):
    """Verify automatic run log setup prunes old Wisp runtime logs."""
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.setattr(supervisor_app, "repo_root", lambda: tmp_path)
    old_runtime = tmp_path / "build_logs" / "wisp_runtime_20260101-010101"
    old_runtime.mkdir(parents=True)
    os.utime(old_runtime, (0, 0))

    log_dir = supervisor_app._prepare_run_log_dir()

    assert log_dir.exists()
    assert not old_runtime.exists()


def test_main_exits_when_single_instance_lock_is_held(tmp_path, monkeypatch):
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.delenv("WISP_RUNTIME_LOG_MODE", raising=False)
    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: False)

    class ShouldNotStart:
        def __init__(self):
            raise AssertionError("workers should not start for duplicate instance")

    monkeypatch.setattr(supervisor_app, "WispSupervisor", ShouldNotStart)

    assert supervisor_app.main() == 2


def test_main_exits_when_ui_worker_exits(tmp_path, monkeypatch):
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.delenv("WISP_RUNTIME_LOG_MODE", raising=False)
    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: True)
    instances = []

    class FakeWorker:
        def __init__(self):
            self.exit_handlers = []

        def on_exit(self, handler):
            self.exit_handlers.append(handler)

        def on_event(self, _event, _handler):
            pass

        def call(self, _method, _params=None, *, timeout=30.0, wait=True):
            return {"started": True}

    class FakeSupervisor:
        def __init__(self):
            self.workers = {
                "native": FakeWorker(),
                "ui": FakeWorker(),
                "brain": FakeWorker(),
                "audio": FakeWorker(),
            }
            self.shutdown_called = False
            instances.append(self)

        def start_all(self):
            return {}

        def shutdown(self):
            self.shutdown_called = True

    class FakeFlowController:
        def __init__(self, *, native, ui, brain, audio, **_kwargs):
            self.ui = ui

        def start(self):
            for handler in list(self.ui.exit_handlers):
                handler(0)

        def start_hotkeys(self):
            return {"started": True}

    monkeypatch.setattr(supervisor_app, "WispSupervisor", FakeSupervisor)
    monkeypatch.setattr(supervisor_app, "FlowController", FakeFlowController)

    assert supervisor_app.main() == 0
    assert instances
    assert instances[0].shutdown_called is True
    assert not (tmp_path / "build_logs").exists()


def test_main_shuts_down_after_nonzero_ui_exit(tmp_path, monkeypatch):
    """Verify an unexpected UI worker exit shuts down instead of restarting UI."""
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.delenv("WISP_RUNTIME_LOG_MODE", raising=False)
    monkeypatch.setattr(supervisor_app, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: True)
    instances = []

    class FakeWorker:
        """Fake worker that records restart and call activity."""
        def __init__(self):
            """Initialize the fake worker."""
            self.exit_handlers = []
            self.calls = []
            self.restart_calls = 0

        def on_exit(self, handler):
            """Store exit handler."""
            self.exit_handlers.append(handler)

        def on_event(self, _event, _handler):
            """Ignore event handlers."""
            pass

        def call(self, method, _params=None, *, timeout=30.0, wait=True):
            """Record calls and return a fake response."""
            self.calls.append({"method": method, "timeout": timeout, "wait": wait})
            return {"started": True, "pong": method == "ui.ping"}

        def restart(self):
            """Record restart requests."""
            self.restart_calls += 1

    class FakeSupervisor:
        """Fake supervisor."""
        def __init__(self):
            """Initialize fake supervisor."""
            self.workers = {
                "native": FakeWorker(),
                "ui": FakeWorker(),
                "brain": FakeWorker(),
                "audio": FakeWorker(),
            }
            self.shutdown_called = False
            instances.append(self)

        def start_all(self):
            """No-op start."""
            return {}

        def shutdown(self):
            """Record shutdown."""
            self.shutdown_called = True

    class FakeFlowController:
        """Fake flow controller that simulates a UI crash."""
        def __init__(self, *, native, ui, brain, audio, **_kwargs):
            """Initialize fake flow controller."""
            self.ui = ui

        def start(self):
            """Emit UI crash."""
            for handler in list(self.ui.exit_handlers):
                handler(9)

        def start_hotkeys(self):
            """No-op hotkeys."""
            return {"started": True}

    monkeypatch.setattr(supervisor_app, "WispSupervisor", FakeSupervisor)
    monkeypatch.setattr(supervisor_app, "FlowController", FakeFlowController)

    assert supervisor_app.main() == 0
    ui = instances[0].workers["ui"]
    assert ui.restart_calls == 0
    assert ui.calls == []
    assert instances[0].shutdown_called is True
    crash_logs = list((tmp_path / "build_logs").glob("wisp_crash_*/supervisor-crash.log"))
    assert len(crash_logs) == 1
    assert "UI worker exited with code 9" in crash_logs[0].read_text(encoding="utf-8")


def test_main_restarts_audio_worker_after_unexpected_exit(tmp_path, monkeypatch):
    """Verify an unexpected audio worker exit restarts without shutting down Wisp."""
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.delenv("WISP_RUNTIME_LOG_MODE", raising=False)
    monkeypatch.setattr(supervisor_app, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: True)
    instances = []

    class FakeWorker:
        """Fake worker that records restart and call activity."""
        def __init__(self):
            self.exit_handlers = []
            self.event_handlers = {}
            self.calls = []
            self.restart_calls = 0

        def on_exit(self, handler):
            self.exit_handlers.append(handler)

        def on_event(self, event, handler):
            self.event_handlers[event] = handler

        def call(self, method, _params=None, *, timeout=30.0, wait=True):
            self.calls.append({"method": method, "timeout": timeout, "wait": wait})
            return {"pong": method.endswith(".ping")}

        def restart(self):
            self.restart_calls += 1

    class FakeSupervisor:
        """Fake supervisor."""
        def __init__(self):
            self.workers = {
                "native": FakeWorker(),
                "ui": FakeWorker(),
                "brain": FakeWorker(),
                "audio": FakeWorker(),
            }
            self.shutdown_called = False
            instances.append(self)

        def start_all(self):
            return {}

        def shutdown(self):
            self.shutdown_called = True

    class FakeFlowController:
        """Fake flow controller that simulates audio exit, then normal UI exit."""
        def __init__(self, *, native, ui, brain, audio, **_kwargs):
            self.ui = ui
            self.audio = audio

        def start(self):
            for handler in list(self.audio.exit_handlers):
                handler(9)
            for handler in list(self.ui.exit_handlers):
                handler(0)

        def start_hotkeys(self):
            return {"started": True}

    monkeypatch.setattr(supervisor_app, "WispSupervisor", FakeSupervisor)
    monkeypatch.setattr(supervisor_app, "FlowController", FakeFlowController)

    assert supervisor_app.main() == 0
    audio = instances[0].workers["audio"]
    assert audio.restart_calls == 1
    assert [call["method"] for call in audio.calls] == ["audio.ping"]
    assert instances[0].shutdown_called is True


def test_main_does_not_restart_ui_after_user_quit_event(tmp_path, monkeypatch):
    """Verify a user-requested Qt quit is not treated as a UI crash."""
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.delenv("WISP_RUNTIME_LOG_MODE", raising=False)
    monkeypatch.setattr(supervisor_app, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: True)
    instances = []

    class FakeWorker:
        """Fake worker that records exit, event, and restart activity."""
        def __init__(self):
            """Initialize fake worker."""
            self.exit_handlers = []
            self.event_handlers = {}
            self.restart_calls = 0

        def on_exit(self, handler):
            """Store exit handler."""
            self.exit_handlers.append(handler)

        def on_event(self, event, handler):
            """Store event handler."""
            self.event_handlers[event] = handler

        def call(self, _method, _params=None, *, timeout=30.0, wait=True):
            """Return a fake response."""
            return {"started": True}

        def restart(self):
            """Record restart requests."""
            self.restart_calls += 1

    class FakeSupervisor:
        """Fake supervisor."""
        def __init__(self):
            """Initialize fake supervisor."""
            self.workers = {
                "native": FakeWorker(),
                "ui": FakeWorker(),
                "brain": FakeWorker(),
                "audio": FakeWorker(),
            }
            self.shutdown_called = False
            instances.append(self)

        def start_all(self):
            """No-op start."""
            return {}

        def shutdown(self):
            """Record shutdown."""
            self.shutdown_called = True

    class FakeFlowController:
        """Fake flow controller that simulates user quit with a nonzero UI exit."""
        def __init__(self, *, native, ui, brain, audio, **_kwargs):
            """Initialize fake flow controller."""
            self.ui = ui

        def start(self):
            """Emit user quit, then the platform-specific UI process exit."""
            self.ui.event_handlers["ui.quit_requested"]({"reason": "qt_about_to_quit"}, None)
            for handler in list(self.ui.exit_handlers):
                handler(9)

        def start_hotkeys(self):
            """No-op hotkeys."""
            return {"started": True}

    monkeypatch.setattr(supervisor_app, "WispSupervisor", FakeSupervisor)
    monkeypatch.setattr(supervisor_app, "FlowController", FakeFlowController)

    assert supervisor_app.main() == 0
    ui = instances[0].workers["ui"]
    assert ui.restart_calls == 0
    assert instances[0].shutdown_called is True
    assert not list((tmp_path / "build_logs").glob("wisp_crash_*/supervisor-crash.log"))


def test_main_writes_crash_log_when_ui_worker_exits_nonzero(tmp_path, monkeypatch):
    """Verify normal mode writes logs only after an abrupt UI worker exit."""
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.delenv("WISP_RUNTIME_LOG_MODE", raising=False)
    monkeypatch.setattr(supervisor_app, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: True)

    class FakeWorker:
        """Test worker with stderr tail support."""
        def __init__(self):
            """Initialize the fake worker."""
            self.exit_handlers = []

        def on_exit(self, handler):
            """Store exit handler."""
            self.exit_handlers.append(handler)

        def on_event(self, _event, _handler):
            """Ignore event handlers."""
            pass

        def call(self, _method, _params=None, *, timeout=30.0, wait=True):
            """Return a fake response."""
            return {"started": True}

        def stderr_tail(self, _max_lines=20):
            """Return fake stderr tail."""
            return "recent worker stderr"

    class FakeSupervisor:
        """Fake supervisor."""
        def __init__(self):
            """Initialize fake supervisor."""
            self.workers = {
                "native": FakeWorker(),
                "ui": FakeWorker(),
                "brain": FakeWorker(),
                "audio": FakeWorker(),
            }

        def start_all(self):
            """No-op start."""
            return {}

        def shutdown(self):
            """No-op shutdown."""
            return None

    class FakeFlowController:
        """Fake flow controller that simulates a UI crash."""
        def __init__(self, *, native, ui, brain, audio, **_kwargs):
            """Initialize fake flow controller."""
            self.ui = ui

        def start(self):
            """Emit non-zero UI exit."""
            for handler in list(self.ui.exit_handlers):
                handler(9)

        def start_hotkeys(self):
            """No-op hotkeys."""
            return {"started": True}

    monkeypatch.setattr(supervisor_app, "WispSupervisor", FakeSupervisor)
    monkeypatch.setattr(supervisor_app, "FlowController", FakeFlowController)

    assert supervisor_app.main() == 0
    crash_logs = list((tmp_path / "build_logs").glob("wisp_crash_*/supervisor-crash.log"))
    assert len(crash_logs) == 1
    report = crash_logs[0].read_text(encoding="utf-8")
    assert "UI worker exited with code 9" in report
    assert "recent worker stderr" in report
