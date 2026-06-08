from __future__ import annotations

import base64
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import config

from macos_py.supervisor.flows import FlowController


class FakeWorker:
    def __init__(
        self,
        handlers: dict[str, Any] | None = None,
        stream_handlers: dict[str, Any] | None = None,
    ) -> None:
        self.handlers = handlers or {}
        self.stream_handlers = stream_handlers or {}
        self.calls: list[dict[str, Any]] = []
        self.events: dict[str, list[Any]] = {}

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        wait: bool = True,
    ) -> Any:
        payload = params or {}
        self.calls.append({"method": method, "params": payload, "timeout": timeout, "wait": wait})
        handler = self.handlers.get(method)
        if handler is None:
            return {}
        return handler(payload)

    def call_with_events(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        on_event,
        on_started=None,
    ) -> Any:
        payload = params or {}
        request_id = len(self.calls) + 1
        self.calls.append(
            {"method": method, "params": payload, "timeout": timeout, "wait": True, "stream": True}
        )
        if on_started is not None:
            on_started(request_id)
        handler = self.stream_handlers.get(method)
        if handler is not None:
            return handler(payload, on_event)
        return self.handlers.get(method, lambda _params: {})(payload)

    def on_event(self, event: str, handler) -> None:
        self.events.setdefault(event, []).append(handler)

    def emit(self, event: str, data: Any = None) -> None:
        for handler in list(self.events.get(event, [])):
            handler(data or {}, None)

    def calls_for(self, method: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["method"] == method]

    def last_call(self, method: str) -> dict[str, Any]:
        calls = self.calls_for(method)
        assert calls, f"expected call {method!r}"
        return calls[-1]


@contextmanager
def caller_config(rows: list[dict[str, Any]]):
    old_rows = list(config.CALLER_ROWS)
    old_tts = getattr(config, "TTS_PROVIDER", "none")
    config.CALLER_ROWS[:] = rows
    config.TTS_PROVIDER = "none"
    try:
        yield
    finally:
        config.CALLER_ROWS[:] = old_rows
        config.TTS_PROVIDER = old_tts


def make_flow(
    *,
    native: FakeWorker | None = None,
    ui: FakeWorker | None = None,
    brain: FakeWorker | None = None,
    audio: FakeWorker | None = None,
) -> tuple[FlowController, FakeWorker, FakeWorker, FakeWorker, FakeWorker]:
    native = native or FakeWorker()
    ui = ui or FakeWorker()
    brain = brain or FakeWorker()
    audio = audio or FakeWorker()
    flow = FlowController(native=native, ui=ui, brain=brain, audio=audio, run_async=False)
    flow.start()
    return flow, native, ui, brain, audio


def context_handler(selected: str = "selected", clipboard: str = "", pid: int = 42):
    def handler(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_text": selected,
            "clipboard_text": clipboard,
            "active_app": {"name": "Notes", "pid": pid, "bundle_id": "com.apple.Notes"},
        }

    return handler


def query_stream(reply: str = "reply"):
    def handler(_params: dict[str, Any], on_event) -> dict[str, Any]:
        on_event("reply.chunk", {"text": reply[:2]}, 1)
        on_event("reply.chunk", {"text": reply[2:]}, 1)
        on_event("reply.done", {"text": reply}, 1)
        return {"text": reply}

    return handler


def test_caller_hotkey_collects_context_and_shows_intent():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": True,
            "context_tools": True,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler()})
    with caller_config(rows):
        _flow, native, ui, _brain, audio = make_flow(native=native)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})

    assert ui.calls_for("ui.show_overlay")
    assert audio.last_call("audio.prewarm")["wait"] is False
    assert native.last_call("native.context.snapshot")["params"]["include_selection"] is True
    assert ui.last_call("ui.show_intent")["params"]["caller_idx"] == 0


def test_query_flow_streams_reply_and_adds_chat_conversation_with_context():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": True,
            "context_tools": True,
            "context_screenshot": "off",
            "context_clipboard": True,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(clipboard="clip text")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("hello")})
    with caller_config(rows):
        flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow._context_buffer.append("buffered text")
        ui.emit("ui.context.dropped", {"items": [{"name": "notes.txt", "content": "drop text", "type": "text"}]})
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "Explain this"})

    query = brain.last_call("brain.query")["params"]
    assert query["intent_prompt"] == "Explain this"
    assert query["selected"] == "selected"
    assert query["use_tools"] is True
    assert "Active app: Notes" in query["ambient_text"]
    assert "Clipboard:" in query["ambient_text"]
    assert "Buffered context:" in query["ambient_text"]
    assert "Dropped context:" in query["ambient_text"]
    assert [c["params"]["text"] for c in ui.calls_for("ui.reply.chunk")] == ["he", "llo"]
    assert ui.calls_for("ui.reply.done")
    assert ui.last_call("ui.chat.add_conversation")["params"]["assistant"] == "hello"
    assert ui.calls_for("ui.context.summary")
    assert ui.calls_for("ui.context.clear")


def test_rewrite_flow_pastes_back_to_original_pid():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": True,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        },
        {
            "paste_back": True,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        },
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="bad grammar", pid=777),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.rewrite": query_stream("good grammar")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 1})
        ui.emit("ui.intent.chosen", {"custom": "Fix grammar"})

    rewrite = brain.last_call("brain.rewrite")["params"]
    assert rewrite["selected_text"] == "bad grammar"
    paste = native.last_call("native.paste_text")["params"]
    assert paste["text"] == "good grammar"
    assert paste["target_pid"] == 777
    assert ui.last_call("ui.reply.notice")["params"]["text"] == "Rewrite pasted."


def test_snip_region_captures_file_and_queries_with_image():
    image_bytes = b"not really a png but enough for base64"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)

    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.capture.region": lambda _params: {"ok": True, "path": str(image_path)},
            "native.context.snapshot": context_handler(selected=""),
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("vision reply")})
    try:
        with caller_config(rows):
            _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
            native.emit("native.hotkey", {"kind": "snip"})
            ui.emit("ui.snip.region", {"x": 1, "y": 2, "width": 3, "height": 4})
            ui.emit("ui.intent.chosen", {"custom": "What is in this image?"})
    finally:
        image_path.unlink(missing_ok=True)

    assert ui.calls_for("ui.show_snip")
    assert native.last_call("native.capture.region")["params"]["region"]["width"] == 3
    query = brain.last_call("brain.query")["params"]
    assert query["screenshot_b64"] == base64.b64encode(image_bytes).decode("ascii")


def test_voice_flow_records_transcribes_and_queries():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": True,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "voice prompt"}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("voice reply")})
    with caller_config(rows):
        _flow, native, _ui, brain, audio = make_flow(native=native, audio=audio, brain=brain)
        native.emit("native.hotkey", {"kind": "voice_start"})
        native.emit("native.hotkey", {"kind": "voice_stop"})

    assert audio.calls_for("audio.record.start")
    assert audio.calls_for("audio.record.stop_transcribe")
    assert brain.last_call("brain.query")["params"]["intent_prompt"] == "voice prompt"


def test_chat_request_streams_through_brain_chat():
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})
    _flow, _native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "hi"}]})

    assert brain.last_call("brain.chat")["params"]["messages"][0]["content"] == "hi"
    assert [c["params"]["text"] for c in ui.calls_for("ui.chat.chunk")] == ["ch", "at reply"]
    assert ui.last_call("ui.chat.done")["params"]["request_id"] == "chat-1"


def test_memory_events_route_to_brain_and_seed_ui_viewer():
    brain = FakeWorker({"brain.memory.list": lambda _params: {"facts": [{"id": "1", "text": "remember"}]}})
    _flow, _native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit("ui.memory.open_requested", {})
    ui.emit("ui.memory.add", {"text": "new fact", "category": "general"})
    ui.emit("ui.memory.update", {"id": "1", "text": "updated", "category": "project_context"})
    ui.emit("ui.memory.delete", {"id": "1"})

    assert ui.last_call("ui.show_memory")["params"]["facts"][0]["text"] == "remember"
    assert brain.last_call("brain.memory.add")["params"]["text"] == "new fact"
    assert brain.last_call("brain.memory.update")["params"]["fact_id"] == "1"
    assert brain.last_call("brain.memory.delete")["params"]["fact_id"] == "1"


def test_plugin_and_agent_tray_events_route_through_supervisor():
    brain = FakeWorker(
        {
            "brain.plugins.list": lambda _params: {
                "plugins_dir": "/tmp/plugins",
                "plugins": [{"name": "demo", "status": "loaded", "tray_actions": ["Run"]}],
            },
            "brain.plugins.run_action": lambda _params: {"ok": True, "message": "ran demo"},
            "brain.agent.history.list": lambda _params: {
                "runs_root": "/tmp/runs",
                "runs": [{"title": "recent", "run_dir": "/tmp/runs/1"}],
            },
        }
    )
    _flow, _native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit("ui.plugins.open_requested", {})
    ui.emit("ui.plugins.run_action", {"plugin_name": "demo", "label": "Run"})
    ui.emit("ui.agent.task_requested", {})
    ui.emit("ui.agent.history_requested", {})

    assert ui.last_call("ui.show_plugins")["params"]["plugins"][0]["name"] == "demo"
    assert brain.last_call("brain.plugins.run_action")["params"]["plugin_name"] == "demo"
    assert ui.last_call("ui.reply.notice")["params"]["text"] == "ran demo"
    assert ui.calls_for("ui.show_agent_task")
    assert ui.last_call("ui.show_agent_history")["params"]["runs"][0]["title"] == "recent"


def test_agent_run_request_streams_through_brain_agent_run():
    def agent_stream(params: dict[str, Any], on_event) -> dict[str, Any]:
        assert params["spec"]["title"] == "demo task"
        on_event("agent.log", {"line": "started"}, 1)
        on_event("agent.trace", {"entry": "trace"}, 1)
        on_event("agent.approval.request", {"approval_id": "abc", "action": "shell"}, 1)
        on_event("agent.done", {"run_dir": "/tmp/run", "final": "done"}, 1)
        return {"run_dir": "/tmp/run", "final": "done"}

    brain = FakeWorker(stream_handlers={"brain.agent.run": agent_stream})
    _flow, _native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit(
        "ui.agent.run_requested",
        {"spec": {"title": "demo task", "max_runtime_minutes": 1}},
    )

    assert brain.last_call("brain.agent.run")["params"]["spec"]["title"] == "demo task"
    assert ui.last_call("ui.agent.log")["params"]["line"] == "started"
    assert ui.last_call("ui.agent.trace")["params"]["entry"] == "trace"
    assert ui.last_call("ui.agent.approval.request")["params"]["approval_id"] == "abc"
    assert ui.last_call("ui.agent.done")["params"]["final"] == "done"


def test_agent_approval_and_cancel_route_to_brain():
    held_on_event = {}

    def agent_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        held_on_event["handler"] = on_event
        return {"run_dir": "/tmp/run", "cancelled": True}

    brain = FakeWorker(
        handlers={
            "brain.cancel": lambda params: {"cancelled": params.get("target") == 1},
            "brain.agent.approval.respond": lambda params: {"ok": True, "approved": params["approved"]},
        },
        stream_handlers={"brain.agent.run": agent_stream},
    )
    flow, _native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit(
        "ui.agent.run_requested",
        {"spec": {"title": "demo task", "max_runtime_minutes": 1}},
    )
    with flow._lock:
        flow._active_agent_stream_id = 1
    ui.emit("ui.agent.cancel_requested", {})
    ui.emit("ui.agent.approval.respond", {"approval_id": "abc", "approved": True})

    assert brain.last_call("brain.cancel")["params"]["target"] == 1
    assert brain.last_call("brain.agent.approval.respond")["params"] == {
        "approval_id": "abc",
        "approved": True,
    }


def test_agent_history_routes_read_retry_and_continue_specs():
    brain = FakeWorker(
        {
            "brain.agent.history.list": lambda _params: {
                "runs_root": "/tmp/runs",
                "runs": [{"title": "recent", "run_dir": "/tmp/runs/1"}],
            },
            "brain.agent.history.read": lambda params: {
                "run_dir": params["run_dir"],
                "final": "final",
                "run_log": "log",
                "verbose_log": "trace",
            },
            "brain.agent.history.retry_spec": lambda _params: {"spec": {"title": "retry"}},
            "brain.agent.history.continue_spec": lambda _params: {"spec": {"title": "continue"}},
        }
    )
    _flow, _native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit("ui.agent.history_requested", {})
    ui.emit("ui.agent.history.read", {"run_dir": "/tmp/runs/1"})
    ui.emit("ui.agent.history.retry", {"run_dir": "/tmp/runs/1"})
    ui.emit("ui.agent.history.continue", {"run_dir": "/tmp/runs/1"})

    assert ui.last_call("ui.show_agent_history")["params"]["runs_root"] == "/tmp/runs"
    assert ui.last_call("ui.agent.history.detail")["params"]["verbose_log"] == "trace"
    task_calls = ui.calls_for("ui.show_agent_task")
    assert task_calls[-2]["params"]["spec"]["title"] == "retry"
    assert task_calls[-1]["params"]["spec"]["title"] == "continue"


def test_settings_reload_refreshes_brain_audio_and_hotkeys():
    _flow, native, ui, brain, audio = make_flow()

    ui.emit("ui.settings.applied", {})

    assert brain.calls_for("brain.config.reload")
    assert audio.calls_for("audio.prewarm")
    assert native.calls_for("native.hotkeys.stop")
    assert native.calls_for("native.hotkeys.start")


def test_start_hotkeys_surfaces_failed_registration_to_user():
    native = FakeWorker(
        {"native.hotkeys.start": lambda _params: {"started": False, "reason": "Carbon unavailable"}}
    )
    flow, _native, ui, _brain, _audio = make_flow(native=native)

    result = flow.start_hotkeys()

    assert result["started"] is False
    assert ui.last_call("ui.reply.notice")["params"]["text"].startswith("Global hotkeys did not start")
