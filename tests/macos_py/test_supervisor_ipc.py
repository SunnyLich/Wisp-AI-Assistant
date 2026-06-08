from __future__ import annotations

import importlib.util
import os
import time

import pytest

from macos_py.supervisor.ipc import WorkerClient, WorkerError, WorkerSpec


def _worker(module: str, role: str, name: str | None = None, env: dict[str, str] | None = None) -> WorkerClient:
    merged_env = {"WISP_BRAIN_FAKE_LLM": "1", **(env or {})}
    return WorkerClient(WorkerSpec(name or role, module, role, env=merged_env))


def test_native_worker_ping_and_boundary_status():
    worker = _worker("macos_py.workers.native_host", "native")
    try:
        result = worker.call("ping", {"value": "hello"}, timeout=10)
        assert result["pong"] is True
        assert result["value"] == "hello"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_audio_worker_ping_does_not_import_audio_stack():
    worker = _worker("macos_py.workers.audio_host", "audio")
    try:
        result = worker.call("audio.ping", timeout=10)
        assert result["role"] == "audio"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_brain_worker_exposes_boundary_status_without_ui_or_native_imports():
    worker = _worker("macos_py.workers.brain_host", "brain")
    try:
        result = worker.call("brain.ping", timeout=20)
        assert result["role"] == "brain"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_unknown_method_reports_error_without_killing_worker():
    worker = _worker("macos_py.workers.native_host", "native")
    try:
        with pytest.raises(WorkerError):
            worker.call("native.nope", timeout=10)
        result = worker.call("ping", timeout=10)
        assert result["pong"] is True
    finally:
        worker.shutdown()


def test_worker_respawns_after_process_death():
    worker = _worker("macos_py.workers.native_host", "native")
    try:
        first = worker.call("ping", timeout=10)
        assert worker._proc is not None
        worker._proc.kill()
        worker._proc.wait(timeout=5)
        second = worker.call("ping", timeout=10)
        assert second["pong"] is True
        assert second["pid"] != first["pid"]
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_emits_ready_event_and_passes_boundary():
    seen = []
    worker = _worker(
        "macos_py.workers.ui_host",
        "ui",
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    worker.on_event("ui.ready", lambda data, req_id: seen.append(data))
    try:
        result = worker.call("ui.ping", timeout=30)
        deadline = time.time() + 5
        while not seen and time.time() < deadline:
            time.sleep(0.05)
        assert seen
        assert result["role"] == "ui"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()

