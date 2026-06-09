from __future__ import annotations

import io
import subprocess
import threading
from types import SimpleNamespace

from macos_py.workers import hotkey_helper, native_host


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
    assert run_calls[0][0] == ["/usr/bin/pkill", "-f", "macos_py.workers.hotkey_helper"]
    assert popen_calls
    assert popen_calls[0][1]["stdin"] is subprocess.PIPE


def test_hotkey_helper_stops_when_parent_pipe_closes(monkeypatch):
    class _FakeStdin:
        buffer = io.BytesIO()

    stop = threading.Event()
    monkeypatch.setattr(hotkey_helper.sys, "stdin", _FakeStdin())

    hotkey_helper._stop_on_parent_pipe_close(stop)

    assert stop.is_set()
