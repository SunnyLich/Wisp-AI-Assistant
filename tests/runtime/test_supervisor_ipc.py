"""Tests for macos py test supervisor ipc."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import logging
import os
import sys
import textwrap
import threading
import time
import wave
from pathlib import Path

import pytest

from runtime.supervisor.ipc import WispSupervisor, WorkerClient, WorkerError, WorkerSpec, default_specs

pytestmark = pytest.mark.workflow


def _worker(module: str, role: str, name: str | None = None, env: dict[str, str] | None = None) -> WorkerClient:
    """Verify worker behavior."""
    merged_env = {"WISP_BRAIN_FAKE_LLM": "1", **(env or {})}
    return WorkerClient(WorkerSpec(name or role, module, role, env=merged_env))


def _is_macos_offscreen_qt() -> bool:
    """Return whether this test is using macOS' headless Qt backend."""
    return sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM", "offscreen") == "offscreen"


def _app_supervisor(tmp_path) -> WispSupervisor:
    """Create the same worker process set the app supervisor starts."""
    specs = default_specs()
    for name, spec in specs.items():
        spec.env = {
            **spec.env,
            "WISP_BRAIN_FAKE_LLM": "1",
            "WISP_RUN_LOG_DIR": str(tmp_path),
        }
        if name == "ui":
            spec.env["QT_QPA_PLATFORM"] = "offscreen"
            spec.env["WISP_UI_FREEZE_THRESHOLD_SECONDS"] = "2.5"
            spec.env["WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS"] = "0.25"
    return WispSupervisor(specs)


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_wisp_supervisor_starts_real_app_worker_process_set(tmp_path):
    """The app architecture starts UI/native/brain/audio as separate workers."""
    supervisor = _app_supervisor(tmp_path)
    try:
        results = supervisor.start_all()

        assert set(results) == {"native", "ui", "brain", "audio"}
        pids = {result["pid"] for result in results.values()}
        assert len(pids) == len(results)
        for name, result in results.items():
            assert result["pong"] is True
            assert result["role"] == name
            assert result["boundary"]["ok"] is True
            assert result["boundary"]["forbidden_loaded"] == []

        assert supervisor.call("ui", "ui.ping", timeout=10)["pong"] is True
        if not _is_macos_offscreen_qt():
            # The real macOS app uses the Cocoa backend. The headless offscreen
            # backend can exit when top-level UI surfaces are opened, which is a
            # test harness limitation rather than the app architecture contract
            # this smoke test is meant to cover.
            assert supervisor.call("ui", "ui.show_chat", {"new": True}, timeout=30) == {
                "shown": True,
                "reused": False,
            }
            assert supervisor.call("ui", "ui.show_settings", timeout=10) == {"queued": True}
        assert supervisor.call("ui", "ui.ping", timeout=10)["pong"] is True

        events = []
        reply = supervisor.workers["brain"].call_with_events(
            "brain.query",
            {
                "intent_prompt": "architecture smoke prompt",
                "ambient_text": "architecture smoke context",
                "memory_enabled": False,
            },
            timeout=30,
            on_event=lambda event, data, req_id: events.append((event, data, req_id)),
        )
        assert "[fake-llm]" in reply["text"]
        assert "architecture smoke prompt" in reply["text"]
        assert any(event == "reply.chunk" for event, _data, _req_id in events)

        assert not list(tmp_path.glob("ui_freeze_*.log"))
    finally:
        supervisor.shutdown()


def test_native_worker_ping_and_boundary_status():
    """Verify native worker ping and boundary status behavior."""
    worker = _worker("runtime.workers.native_host", "native")
    try:
        result = worker.call("ping", {"value": "hello"}, timeout=10)
        assert result["pong"] is True
        assert result["value"] == "hello"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_audio_worker_ping_does_not_import_audio_stack():
    """Verify audio worker ping does not import audio stack behavior."""
    worker = _worker("runtime.workers.audio_host", "audio")
    try:
        result = worker.call("audio.ping", timeout=10)
        assert result["role"] == "audio"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_audio_worker_synthesizes_playable_wav_over_ipc(tmp_path):
    """A real audio worker returns a playable offline WAV through IPC."""
    worker = _worker(
        "runtime.workers.audio_host",
        "audio",
        env={"WISP_RUN_LOG_DIR": str(tmp_path)},
    )
    try:
        result = worker.call(
            "audio.tts.synthesize",
            {"text": "Audio worker IPC contract"},
            timeout=30,
        )

        audio_path = Path(result["path"])
        assert audio_path.is_relative_to(tmp_path / "audio")
        assert result == {
            "path": str(audio_path),
            "sample_rate": 22_050,
            "bytes": 2048,
            "provider": "fake",
        }
        with wave.open(str(audio_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 22_050
            assert wav_file.getnframes() == 1024
    finally:
        worker.shutdown()


def test_brain_worker_exposes_boundary_status_without_ui_or_native_imports():
    """Verify brain worker exposes boundary status without ui or native imports behavior."""
    worker = _worker("runtime.workers.brain_host", "brain")
    try:
        result = worker.call("brain.ping", timeout=20)
        assert result["role"] == "brain"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_brain_worker_query_accepts_allowed_tools_over_ipc():
    """A real brain worker accepts tool-policy fields over streamed IPC."""
    worker = _worker("runtime.workers.brain_host", "brain")
    events = []
    try:
        reply = worker.call_with_events(
            "brain.query",
            {
                "intent_prompt": "Summarize the test contract.",
                "memory_enabled": False,
                "use_tools": True,
                "allowed_tools": ["get_context.documents", "git_status"],
            },
            timeout=30,
            on_event=lambda event, data, req_id: events.append((event, data, req_id)),
        )

        assert reply["text"].startswith("[fake-llm]")
        assert "Summarize the test contract." in reply["text"]
        assert any(event == "reply.chunk" for event, _data, _req_id in events)
        done = [data for event, data, _req_id in events if event == "reply.done"]
        assert done == [{"text": reply["text"]}]
    finally:
        worker.shutdown()


def test_real_addon_tools_flow_from_addon_host_through_brain_policy(tmp_path):
    """Real addon discovery and MCP group policy produce the query allow-list."""
    from runtime.supervisor.flows import FlowController, PendingInvocation

    addon_dir = tmp_path / "addons" / "contract-tools"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "contract-tools"
            name = "Contract tools"
            entry = "__init__.py"

            [permissions]
            tools = true
            """
        ).strip(),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text(
        textwrap.dedent(
            """
            def get_tools():
                return [
                    {
                        "name": "mcp_contractserver_echo",
                        "description": "[MCP:contractserver] Echo back text.",
                        "input_schema": {"type": "object", "properties": {}},
                        "executor": lambda inputs: str(inputs.get("text", "")),
                    },
                    {
                        "name": "mcp_contractserver_add",
                        "description": "[MCP:contractserver] Add numbers.",
                        "input_schema": {"type": "object", "properties": {}},
                        "executor": lambda inputs: str(inputs),
                    },
                    {
                        "name": "mcp_contractserver_add",
                        "description": "[MCP:contractserver] Duplicate must be removed.",
                        "input_schema": {"type": "object", "properties": {}},
                        "executor": lambda inputs: str(inputs),
                    },
                ]
            """
        ).strip(),
        encoding="utf-8",
    )
    (tmp_path / "addons.json").write_text(
        json.dumps(
            {
                "addons": {
                    "mcp-bridge": {"enabled": False},
                    "ui-lab": {"enabled": False},
                }
            }
        ),
        encoding="utf-8",
    )

    worker = _worker(
        "runtime.workers.brain_host",
        "brain",
        env={
            "WISP_ADDONS_DIR": str(tmp_path / "addons"),
            "WISP_ADDON_STORE": str(tmp_path / "addons.json"),
            "WISP_RUN_LOG_DIR": str(tmp_path / "logs"),
        },
    )
    flow = FlowController(
        native=worker,
        ui=worker,
        brain=worker,
        audio=worker,
        run_async=False,
    )
    try:
        discovered = worker.call("brain.addons.tools", timeout=20)
        assert discovered == {
            "tools": [
                {
                    "name": "mcp_contractserver_echo",
                    "description": "[MCP:contractserver] Echo back text.",
                },
                {
                    "name": "mcp_contractserver_add",
                    "description": "[MCP:contractserver] Add numbers.",
                },
            ]
        }

        pending = PendingInvocation(
            caller={
                "context_ambient": False,
                "context_documents_mode": "off",
                "context_browser_mode": "off",
                "context_github_mode": "off",
                "context_memory_mode": "off",
                "context_screenshot": "off",
                "file_access": "off",
                "tools": {
                    "mcp_server.contractserver": "off",
                    "mcp_contractserver_add": "on",
                },
            }
        )
        params = flow._brain_query_params("Use the permitted addon tool.", pending)

        assert params["use_tools"] is True
        assert params["allowed_tools"] == ["mcp_contractserver_add"]
        assert params["pinned_tools"] == ["mcp_contractserver_add"]
    finally:
        worker.shutdown()


def test_brain_worker_query_accepts_screenshot_base64_over_ipc(tmp_path):
    """A real brain worker accepts an image payload without OS screen capture."""
    image_path = tmp_path / "one-pixel.png"
    image_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
            "lW8g6QAAAABJRU5ErkJggg=="
        )
    )
    worker = _worker("runtime.workers.brain_host", "brain")
    events = []
    try:
        reply = worker.call_with_events(
            "brain.query",
            {
                "intent_prompt": "Describe the supplied image.",
                "memory_enabled": False,
                "screenshot_b64": base64.b64encode(image_path.read_bytes()).decode("ascii"),
            },
            timeout=30,
            on_event=lambda event, data, req_id: events.append((event, data, req_id)),
        )

        assert reply["text"].startswith("[fake-llm]")
        assert "Describe the supplied image." in reply["text"]
        assert any(event == "reply.chunk" for event, _data, _req_id in events)
        done = [data for event, data, _req_id in events if event == "reply.done"]
        assert done == [{"text": reply["text"]}]
    finally:
        worker.shutdown()


def test_brain_worker_chat_streams_history_over_ipc():
    """A real brain worker normalizes chat history and streams its reply."""
    worker = _worker("runtime.workers.brain_host", "brain")
    events = []
    try:
        reply = worker.call_with_events(
            "brain.chat",
            {
                "messages": [
                    {"role": "user", "content": "Earlier question"},
                    {"role": "assistant", "content": "Earlier answer"},
                    {"role": "user", "content": "Final IPC chat prompt"},
                ],
                "memory_enabled": False,
                "use_tools": True,
                "allowed_tools": ["git_status"],
            },
            timeout=30,
            on_event=lambda event, data, req_id: events.append((event, data, req_id)),
        )

        assert reply["text"].startswith("[fake-chat]")
        assert "Final IPC chat prompt" in reply["text"]
        assert "Earlier question" not in reply["text"]
        chunks = [data["text"] for event, data, _req_id in events if event == "reply.chunk"]
        assert "".join(chunks) == reply["text"]
        done = [data for event, data, _req_id in events if event == "reply.done"]
        assert done == [reply]
    finally:
        worker.shutdown()


def test_brain_worker_rewrite_returns_replacement_over_ipc():
    """A real brain worker returns rewrite text without exposing partial text."""
    worker = _worker("runtime.workers.brain_host", "brain")
    events = []
    try:
        reply = worker.call_with_events(
            "brain.rewrite",
            {
                "selected_text": "this sentence need fixing",
                "intent_prompt": "Fix the grammar",
                "rewrite_context": "A document editor is focused.",
            },
            timeout=30,
            on_event=lambda event, data, req_id: events.append((event, data, req_id)),
        )

        assert reply == {
            "text": "[fake-rewrite] Fix the grammar: this sentence need fixing",
            "visible_text": "",
        }
        assert not [event for event, _data, _req_id in events if event == "reply.chunk"]
        done = [data for event, data, _req_id in events if event == "reply.done"]
        assert done == [reply]
    finally:
        worker.shutdown()


def test_brain_worker_cancel_stops_active_stream_over_ipc():
    """A real cancel request interrupts a concurrent real worker stream."""
    worker = _worker("runtime.workers.brain_host", "brain")
    request_started = threading.Event()
    first_chunk = threading.Event()
    request_id = []
    events = []
    result = {}
    errors = []
    words = [str(index) for index in range(80)]

    def on_event(event, data, req_id):
        events.append((event, data, req_id))
        if event == "reply.chunk":
            first_chunk.set()

    def run_stream():
        try:
            result.update(
                worker.call_with_events(
                    "brain.echo",
                    {"text": " ".join(words), "delay": 0.02},
                    timeout=10,
                    on_started=lambda req_id: (request_id.append(req_id), request_started.set()),
                    on_event=on_event,
                )
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=run_stream, name="test-brain-cancel")
    try:
        thread.start()
        assert request_started.wait(5), "stream request did not start"
        assert first_chunk.wait(5), "stream produced no chunk before cancellation"

        cancelled = worker.call("brain.cancel", {"target": request_id[0]}, timeout=5)
        thread.join(timeout=10)

        assert cancelled == {"cancelled": True}
        assert not thread.is_alive(), "cancelled stream did not finish"
        assert errors == []
        chunks = [data for event, data, _req_id in events if event == "reply.chunk"]
        assert 0 < len(chunks) < len(words)
        assert result["text"] != " ".join(words)
        assert [data for event, data, _req_id in events if event == "reply.done"] == [result]
    finally:
        worker.shutdown()
        thread.join(timeout=2)


def test_brain_worker_memory_crud_persists_over_ipc(tmp_path):
    """A real brain worker completes an isolated durable-memory lifecycle."""
    repo = Path(__file__).resolve().parents[2]
    worker = _worker(
        "runtime.workers.brain_host",
        "brain",
        env={
            "WISP_REPO_ROOT": str(tmp_path),
            "PYTHONPATH": os.pathsep.join([str(repo), str(repo / "runtime" / "brain")]),
        },
    )
    original = "The integration user prefers alpine tea."
    updated = "The integration user prefers jasmine tea."
    try:
        added = worker.call(
            "brain.memory.add",
            {"text": original, "category": "general"},
            timeout=20,
        )
        assert added == {"ok": True, "category": "general", "text": original}

        facts = worker.call("brain.memory.list", timeout=20)["facts"]
        assert len(facts) == 1
        fact_id = facts[0]["id"]
        assert facts[0]["text"] == original
        assert facts[0]["source"] == "manual"

        found = worker.call(
            "brain.memory.search",
            {"query": "Which tea does the integration user prefer?", "top_k": 3},
            timeout=20,
        )
        assert original in found["text"]

        changed = worker.call(
            "brain.memory.update",
            {"fact_id": fact_id, "text": updated, "category": "general"},
            timeout=20,
        )
        assert changed == {
            "ok": True,
            "id": fact_id,
            "text": updated,
            "category": "general",
        }
        assert worker.call("brain.memory.list", timeout=20)["facts"][0]["text"] == updated

        removed = worker.call("brain.memory.delete", {"fact_id": fact_id}, timeout=20)
        assert removed == {"ok": True, "id": fact_id}
        assert worker.call("brain.memory.list", timeout=20) == {"facts": []}
        assert (tmp_path / "memory" / "facts_fallback.json").is_file()
    finally:
        worker.shutdown()


def test_brain_worker_agent_run_streams_and_persists_over_ipc(tmp_path):
    """A real brain worker runs an offline agent and persists its final report."""
    scope = tmp_path / "agent-scope"
    scope.mkdir()
    log_root = tmp_path / "agent-runs"
    worker = _worker("runtime.workers.brain_host", "brain")
    events = []
    try:
        result = worker.call_with_events(
            "brain.agent.run",
            {
                "spec": {
                    "title": "IPC agent contract",
                    "objective": "Complete an offline no-op task",
                    "scope_folder": str(scope),
                    "allow_git": False,
                    "allow_shell": False,
                },
                "log_root": str(log_root),
            },
            timeout=60,
            on_event=lambda event, data, req_id: events.append((event, data, req_id)),
        )

        assert result["error"] == ""
        assert result["cancelled"] is False
        assert "Fake agent run complete." in result["final"]
        run_dir = Path(result["run_dir"])
        assert run_dir.is_relative_to(log_root)
        assert (run_dir / "final.md").read_text(encoding="utf-8") == result["final"]
        assert any(event == "agent.log" for event, _data, _req_id in events)
        done = [data for event, data, _req_id in events if event == "agent.done"]
        assert done == [result]
    finally:
        worker.shutdown()


def test_unknown_method_reports_error_without_killing_worker():
    """Verify unknown method reports error without killing worker behavior."""
    worker = _worker("runtime.workers.native_host", "native")
    try:
        with pytest.raises(WorkerError):
            worker.call("native.nope", timeout=10)
        result = worker.call("ping", timeout=10)
        assert result["pong"] is True
    finally:
        worker.shutdown()


def test_worker_stderr_log_file_is_created_in_run_log_dir(tmp_path):
    """Verify worker stderr log file is created in run log dir behavior."""
    worker = _worker(
        "runtime.workers.native_host",
        "native",
        env={"WISP_RUN_LOG_DIR": str(tmp_path)},
    )
    try:
        result = worker.call("ping", timeout=10)
        assert result["pong"] is True

        deadline = time.time() + 5
        log_path = tmp_path / "native.stderr.log"
        while time.time() < deadline and not log_path.exists():
            time.sleep(0.05)
        assert log_path.exists()
    finally:
        worker.shutdown()


def test_kokoro_install_worker_stderr_is_promoted_to_console_log(caplog):
    """Kokoro pip progress from the UI worker should be visible in the .bat console."""
    worker = _worker("runtime.workers.ui_host", "ui")

    class FakeProc:
        stderr = io.BytesIO(b"[kokoro install] Running: python -m pip install kokoro\n")

    with caplog.at_level(logging.INFO):
        worker._stderr_loop(FakeProc())

    assert "[ui] [kokoro install] Running: python -m pip install kokoro" in caplog.text


def test_worker_respawns_after_process_death():
    """Verify worker respawns after process death behavior."""
    worker = _worker("runtime.workers.native_host", "native")
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


def test_worker_exit_handler_fires_when_process_exits():
    """Verify worker exit handler fires when process exits behavior."""
    seen = []
    worker = _worker("runtime.workers.native_host", "native")
    worker.on_exit(lambda returncode: seen.append(returncode))
    try:
        worker.call("ping", timeout=10)
        assert worker._proc is not None
        worker._proc.kill()
        worker._proc.wait(timeout=5)

        deadline = time.time() + 5
        while not seen and time.time() < deadline:
            time.sleep(0.05)
        assert seen
        assert seen[-1] is not None
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_emits_ready_event_and_passes_boundary():
    """Verify ui worker emits ready event and passes boundary behavior."""
    seen = []
    worker = _worker(
        "runtime.workers.ui_host",
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


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_freeze_watchdog_writes_log(tmp_path):
    """Verify ui worker freeze watchdog writes log behavior."""
    worker = _worker(
        "runtime.workers.ui_host",
        "ui",
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "WISP_RUN_LOG_DIR": str(tmp_path),
            "WISP_UI_DEBUG_METHODS": "1",
            "WISP_UI_FREEZE_THRESHOLD_SECONDS": "0.1",
            "WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS": "0.05",
            "WISP_UI_FREEZE_LOG_COOLDOWN_SECONDS": "0.1",
            "WISP_UI_SLOW_DISPATCH_SECONDS": "0.1",
        },
    )
    try:
        result = worker.call("ui.debug.block_event_loop", {"seconds": 0.35}, timeout=10)
        assert result["blocked_seconds"] == 0.35

        deadline = time.time() + 5
        freeze_logs = []
        slow_logs = []
        while time.time() < deadline:
            freeze_logs = list(tmp_path.glob("ui_freeze_*.log"))
            slow_logs = list(tmp_path.glob("ui_slow_dispatch_*.log"))
            if freeze_logs and slow_logs:
                break
            time.sleep(0.05)

        assert freeze_logs
        assert slow_logs
        freeze_text = freeze_logs[0].read_text(encoding="utf-8")
        slow_text = slow_logs[0].read_text(encoding="utf-8")
        assert "active_method=ui.debug.block_event_loop" in freeze_text
        assert "Thread stacks:" in freeze_text
        assert "method=ui.debug.block_event_loop" in slow_text
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_show_settings_does_not_block_event_loop(tmp_path):
    """Verify ui worker show settings does not block event loop behavior."""
    worker = _worker(
        "runtime.workers.ui_host",
        "ui",
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "WISP_RUN_LOG_DIR": str(tmp_path),
            "WISP_UI_FREEZE_THRESHOLD_SECONDS": "2.5",
            "WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS": "0.25",
        },
    )
    try:
        started = time.perf_counter()
        result = worker.call("ui.show_settings", timeout=10)
        elapsed = time.perf_counter() - started
        assert result == {"queued": True}
        assert elapsed < 2.0

        time.sleep(0.5)
        ping = worker.call("ui.ping", timeout=10)
        assert ping["pong"] is True
        assert not list(tmp_path.glob("ui_freeze_*.log"))
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_show_memory_does_not_crash_or_block_event_loop(tmp_path):
    """Verify ui worker show memory does not crash or block event loop behavior."""
    worker = _worker(
        "runtime.workers.ui_host",
        "ui",
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "WISP_RUN_LOG_DIR": str(tmp_path),
            "WISP_UI_FREEZE_THRESHOLD_SECONDS": "2.5",
            "WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS": "0.25",
        },
    )
    try:
        started = time.perf_counter()
        result = worker.call(
            "ui.show_memory",
            {"facts": [{"id": "1", "text": "remember this", "category": "general"}]},
            timeout=10,
        )
        elapsed = time.perf_counter() - started
        assert result == {"queued": True}
        assert elapsed < 2.0

        time.sleep(0.5)
        ping = worker.call("ui.ping", timeout=10)
        assert ping["pong"] is True
        assert not list(tmp_path.glob("ui_freeze_*.log"))
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_bubble_clear_does_not_import_audio_or_freeze(tmp_path):
    """Verify ui worker bubble clear does not import audio or freeze behavior."""
    if _is_macos_offscreen_qt():
        pytest.skip("macOS offscreen Qt cannot reliably construct overlay/tray surfaces")
    worker = _worker(
        "runtime.workers.ui_host",
        "ui",
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "WISP_RUN_LOG_DIR": str(tmp_path),
            "WISP_UI_FREEZE_THRESHOLD_SECONDS": "2.5",
            "WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS": "0.25",
        },
    )
    try:
        worker.call("ui.show_overlay", timeout=30)
        worker.call("ui.reply.notice", {"text": "hello", "timeout_ms": 10}, timeout=10)
        worker.call("ui.reply.reset", timeout=10)
        ping = worker.call("ui.ping", timeout=10)

        assert ping["pong"] is True
        assert not list(tmp_path.glob("ui_freeze_*.log"))
        assert "core.audio" not in worker.stderr_tail(80)
        assert "numpy" not in worker.stderr_tail(80).lower()
    finally:
        worker.shutdown()
