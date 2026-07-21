"""Tests for the hotkey helper subprocess lifecycle and native backend reload."""

from __future__ import annotations

import io
import subprocess
import sys
import threading
from types import SimpleNamespace

from runtime.workers import hotkey_helper, native_host


class _ImmediateThread:
    def __init__(self, target, args=(), kwargs=None, daemon=None, name=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


class _FakeProc:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(
            b'{"status":"started","started":true,"backend":"carbon-helper"}\n'
        )
        self.stderr = io.BytesIO()
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def test_macos_hotkey_helper_cleans_stale_helpers_and_uses_parent_pipe(monkeypatch):
    run_calls = []
    popen_calls = []

    def fake_run(args, **kwargs):
        run_calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    def fake_popen(args, **kwargs):
        popen_calls.append((args, kwargs))
        return _FakeProc()

    monkeypatch.setattr(native_host, "IS_MAC", True)
    monkeypatch.setattr(native_host.subprocess, "run", fake_run)
    monkeypatch.setattr(native_host.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(native_host.threading, "Thread", _ImmediateThread)

    status = native_host._HotkeyHelper().start()

    assert status["started"] is True
    assert run_calls
    assert run_calls[0][0] == ["/usr/bin/pkill", "-f", "runtime.workers.hotkey_helper"]
    assert popen_calls
    assert popen_calls[0][1]["stdin"] is subprocess.PIPE


def test_hotkey_helper_stops_when_parent_pipe_closes(monkeypatch):
    class _FakeStdin:
        buffer = io.BytesIO()

    stop = threading.Event()
    monkeypatch.setattr(hotkey_helper.sys, "stdin", _FakeStdin())

    hotkey_helper._stop_on_parent_pipe_close(stop)

    assert stop.is_set()


def test_native_hotkey_stop_forgets_backend_when_stop_fails(monkeypatch):
    """Verify a failed backend stop cannot leave stale hotkeys marked existing."""
    class BadHotkeys:
        def stop(self):
            raise RuntimeError("still busy")

    monkeypatch.setattr(native_host, "_hotkeys", BadHotkeys())

    result = native_host.hotkeys_stop()

    assert result["ok"] is False
    assert result["stopped"] is False
    assert "still busy" in result["error"]
    assert native_host._hotkeys is None


def test_native_hotkey_reload_reloads_config_and_replaces_backend(monkeypatch):
    """Verify native hotkey reload refreshes config before registering replacement keys."""
    calls: list[str] = []

    class OldHotkeys:
        def stop(self):
            calls.append("stop-old")

    class NewHotkeys:
        def start(self, addon_hotkeys=None):
            calls.append(f"start-new:{addon_hotkeys}")
            return {"started": True, "backend": "fake"}

        def stop(self):
            calls.append("stop-new")

    fake_config = SimpleNamespace(reload=lambda: calls.append("reload-config"))
    monkeypatch.setitem(sys.modules, "config", fake_config)
    monkeypatch.setattr(native_host, "_hotkeys", OldHotkeys())
    monkeypatch.setattr(native_host, "_DirectHotkeys", NewHotkeys)
    monkeypatch.setattr(native_host, "IS_MAC", False)

    result = native_host.hotkeys_reload(addon_hotkeys=[{"hotkey": "ctrl+alt+h"}])

    assert result["ok"] is True
    assert result["started"] is True
    assert result["reloaded"] is True
    assert calls == ["reload-config", "stop-old", "start-new:[{'hotkey': 'ctrl+alt+h'}]"]
    assert isinstance(native_host._hotkeys, NewHotkeys)


def test_direct_hotkey_start_reports_os_hook_rejection(monkeypatch):
    """Native hook rejection is returned as status instead of escaping the worker."""
    import core.hotkeys

    class RejectedListener:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise OSError("OS rejected the global hook")

        def stop(self):
            pass

    monkeypatch.setattr(core.hotkeys, "HotkeyListener", RejectedListener)
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(CALLER_ROWS=[]))

    status = native_host._DirectHotkeys().start()

    assert status["started"] is False
    assert "OS rejected the global hook" in status["error"]
