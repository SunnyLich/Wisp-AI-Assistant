"""Tests for the supervisor FlowController's query, voice, and snip flows."""

from __future__ import annotations

import base64
import queue
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import config
from core.actions.progress import ActionProgressStage
from runtime.supervisor import flow_estimates, tool_modes
from runtime.supervisor import flows as flows_module
from runtime.supervisor.flows import FlowController, PendingInvocation


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
    old_tts_speak_replies = getattr(config, "TTS_SPEAK_REPLIES", False)
    # Missing fields must resolve to product defaults, not to the developer's
    # currently selected profile. Explicit values in a scenario still win.
    config.CALLER_ROWS[:] = [
        {"context_memory_mode": "off", **row}
        for row in rows
    ]
    config.TTS_PROVIDER = "none"
    config.TTS_SPEAK_REPLIES = False
    try:
        yield
    finally:
        config.CALLER_ROWS[:] = old_rows
        config.TTS_PROVIDER = old_tts
        config.TTS_SPEAK_REPLIES = old_tts_speak_replies


@contextmanager
def voice_config(row: dict[str, Any]):
    old_row = dict(getattr(config, "VOICE_CALLER", {}))
    old_tts = getattr(config, "TTS_PROVIDER", "none")
    old_tts_speak_replies = getattr(config, "TTS_SPEAK_REPLIES", False)
    old_voice_review_transcript = getattr(config, "VOICE_REVIEW_TRANSCRIPT", False)
    config.VOICE_CALLER.clear()
    config.VOICE_CALLER.update({"context_memory_mode": "off", **row})
    config.TTS_PROVIDER = "none"
    config.TTS_SPEAK_REPLIES = False
    config.VOICE_REVIEW_TRANSCRIPT = False
    try:
        yield
    finally:
        config.VOICE_CALLER.clear()
        config.VOICE_CALLER.update(old_row)
        config.TTS_PROVIDER = old_tts
        config.TTS_SPEAK_REPLIES = old_tts_speak_replies
        config.VOICE_REVIEW_TRANSCRIPT = old_voice_review_transcript


@contextmanager
def snip_config(row: dict[str, Any]):
    """Temporarily set the region-snip caller context."""
    old_row = dict(getattr(config, "SNIP_CALLER", {}))
    old_tts = getattr(config, "TTS_PROVIDER", "none")
    old_tts_speak_replies = getattr(config, "TTS_SPEAK_REPLIES", False)
    config.SNIP_CALLER.clear()
    config.SNIP_CALLER.update({"context_memory_mode": "off", **row})
    config.TTS_PROVIDER = "none"
    config.TTS_SPEAK_REPLIES = False
    try:
        yield
    finally:
        config.SNIP_CALLER.clear()
        config.SNIP_CALLER.update(old_row)
        config.TTS_PROVIDER = old_tts
        config.TTS_SPEAK_REPLIES = old_tts_speak_replies


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


@pytest.mark.parametrize("event", ["audio.playback.amplitude", "audio.live.amplitude"])
def test_audio_amplitude_drives_overlay_without_blocking(event: str):
    """Both generated and live speech use the same normalized visual meter."""
    _flow, _native, ui, _brain, audio = make_flow()

    audio.emit(event, {"amplitude": 0.64})

    call = ui.last_call("ui.overlay.amplitude")
    assert call["params"] == {"amplitude": 0.64}
    assert call["wait"] is False


def test_slow_response_notice_appears_only_after_the_deadline(monkeypatch):
    flow, _native, ui, _brain, _audio = make_flow()
    monkeypatch.setattr(flows_module, "_SLOW_RESPONSE_NOTICE_SECONDS", 0.01)
    activity = threading.Event()

    timer = flow._start_slow_response_notice(  # noqa: SLF001 - contract-level flow test
        flow._current_generation,  # noqa: SLF001
        activity,
        "Still working.",
    )
    timer.join(timeout=1.0)

    chunks = ui.calls_for("ui.reply.chunk")
    assert chunks[-1]["params"]["text"] == "Still working."
    assert chunks[-1]["params"]["is_progress"] is True


def test_fast_response_cancels_slow_notice(monkeypatch):
    flow, _native, ui, _brain, _audio = make_flow()
    monkeypatch.setattr(flows_module, "_SLOW_RESPONSE_NOTICE_SECONDS", 0.01)
    activity = threading.Event()
    activity.set()

    timer = flow._start_slow_response_notice(  # noqa: SLF001 - contract-level flow test
        flow._current_generation,  # noqa: SLF001
        activity,
        "Must not appear.",
    )
    timer.join(timeout=1.0)

    assert not ui.calls_for("ui.reply.chunk")


def test_long_action_stage_gets_an_exact_heads_up(monkeypatch):
    flow, _native, ui, _brain, _audio = make_flow()
    monkeypatch.setattr(flows_module, "_ACTION_PROGRESS_HEADS_UP_SECONDS", 0.01)
    finished = threading.Event()
    progress = flow._new_action_progress("vscode.code_change", app="vscode")  # noqa: SLF001
    progress.advance(ActionProgressStage.PLANNING, "Drafting the exact change...")

    timer = flow._start_action_progress_heads_up(  # noqa: SLF001
        flow._current_generation,  # noqa: SLF001
        finished,
        progress,
        ActionProgressStage.PLANNING,
        "The model is still drafting; this may take a few more seconds.",
    )
    timer.join(timeout=1.0)

    calls = ui.calls_for("ui.action.progress")
    assert [call["params"]["stage"] for call in calls] == ["planning", "planning"]
    assert calls[-1]["params"]["text"].startswith("The model is still drafting")


def test_start_can_wire_ui_without_unrelated_background_prewarms():
    """Shell acceptance can use real event wiring without loading speech models."""
    native = FakeWorker()
    ui = FakeWorker()
    brain = FakeWorker()
    audio = FakeWorker()
    flow = FlowController(
        native=native,
        ui=ui,
        brain=brain,
        audio=audio,
        run_async=False,
    )

    flow.start(prewarm=False)

    assert ui.calls_for("ui.show_overlay")
    assert not ui.calls_for("ui.prewarm_intent")
    assert not brain.calls_for("brain.privacy.prewarm")
    assert not brain.calls_for("brain.harness.prewarm")
    assert not audio.calls_for("audio.prewarm")
    assert ui.events["ui.memory.open_requested"]


def test_start_loads_addon_actions_before_showing_first_tray_menu():
    """Addons that load inside the startup grace period appear in the first menu."""
    order: list[str] = []
    brain = FakeWorker(
        {
            "brain.addons.ready": lambda _params: order.append("load_addons") or {
                "ready": True,
                "addons": [
                    {
                        "id": "virtual-workspace",
                        "enabled": True,
                        "tray_actions": ["Open Virtual Workspace"],
                    }
                ]
            }
        }
    )

    def show_overlay(params: dict[str, Any]) -> dict[str, Any]:
        order.append("show_overlay")
        assert params["addon_tray_actions"] == [
            {
                "addon_id": "virtual-workspace",
                "label": "Open Virtual Workspace",
            }
        ]
        return {"shown": True}

    ui = FakeWorker({"ui.show_overlay": show_overlay})
    flow = FlowController(
        native=FakeWorker(),
        ui=ui,
        brain=brain,
        audio=FakeWorker(),
        run_async=False,
    )

    flow.start(prewarm=False)

    assert order == ["load_addons", "show_overlay"]
    assert not brain.calls_for("brain.addons.list")
    assert not ui.calls_for("ui.addons.tray_actions")


def test_live_addon_snapshots_rebuild_existing_tray_and_deduplicate():
    flow, _native, ui, brain, _audio = make_flow()
    baseline = len(ui.calls_for("ui.addons.tray_actions"))
    loaded = {
        "reason": "enabled",
        "addon_id": "virtual-workspace",
        "addons": [{
            "id": "virtual-workspace",
            "enabled": True,
            "tray_actions": ["Open Virtual Workspace"],
        }],
    }

    brain.emit("addons.changed", loaded)

    assert ui.last_call("ui.addons.tray_actions")["params"]["actions"] == [{
        "addon_id": "virtual-workspace",
        "label": "Open Virtual Workspace",
    }]
    assert len(ui.calls_for("ui.addons.tray_actions")) == baseline + 1

    brain.emit("addons.changed", loaded)
    assert len(ui.calls_for("ui.addons.tray_actions")) == baseline + 1

    brain.emit("addons.changed", {
        "reason": "disabled",
        "addon_id": "virtual-workspace",
        "addons": [{
            "id": "virtual-workspace",
            "enabled": False,
            "tray_actions": [],
        }],
    })
    assert ui.last_call("ui.addons.tray_actions")["params"]["actions"] == []
    assert len(ui.calls_for("ui.addons.tray_actions")) == baseline + 2


def test_start_does_not_fall_back_to_slow_addon_listing_when_loading_continues():
    brain = FakeWorker(
        {
            "brain.addons.ready": lambda _params: {
                "ready": False,
                "error": "",
                "addons": [],
            },
            "brain.addons.list": lambda _params: pytest.fail(
                "startup must not use the full addon-manager listing"
            ),
        }
    )
    ui = FakeWorker()
    flow = FlowController(
        native=FakeWorker(),
        ui=ui,
        brain=brain,
        audio=FakeWorker(),
        run_async=False,
    )

    flow.start(prewarm=False)

    ready_call = brain.last_call("brain.addons.ready")
    assert ready_call["params"] == {"timeout_seconds": 3.0}
    assert ready_call["timeout"] == 5.0
    assert ui.last_call("ui.show_overlay")["params"]["addon_tray_actions"] == []
    assert not brain.calls_for("brain.addons.list")

    brain.emit(
        "addons.changed",
        {
            "reason": "loaded",
            "addons": [{
                "id": "virtual-workspace",
                "enabled": True,
                "tray_actions": ["Open Virtual Workspace"],
            }],
        },
    )

    assert ui.last_call("ui.addons.tray_actions")["params"]["actions"] == [{
        "addon_id": "virtual-workspace",
        "label": "Open Virtual Workspace",
    }]


def test_safe_call_quiets_ui_worker_exit(caplog):
    """Late best-effort UI calls during shutdown should not log an ERROR traceback."""
    flow, *_ = make_flow()

    class ExitedUi:
        def call(self, _method, _params=None, *, timeout=30.0, wait=True):
            raise RuntimeError("worker exited")

    with caplog.at_level("ERROR", logger="openwand.runtime.flows"):
        result = flow._safe_call(ExitedUi(), "ui.reply.notice", {"text": "closing"}, timeout=1.0)

    assert result is None
    assert "worker call failed" not in caplog.text


def test_safe_call_still_logs_real_ui_failures(caplog):
    """Non-shutdown UI failures should remain visible."""
    flow, *_ = make_flow()

    class BrokenUi:
        def call(self, _method, _params=None, *, timeout=30.0, wait=True):
            raise RuntimeError("render failed")

    with caplog.at_level("ERROR", logger="openwand.runtime.flows"):
        result = flow._safe_call(BrokenUi(), "ui.reply.notice", {"text": "hello"}, timeout=1.0)

    assert result is None
    assert "worker call failed: ui.reply.notice" in caplog.text


def test_privacy_review_is_resolved_through_the_local_ui():
    """A blocked brain request receives the user's review decision."""
    ui = FakeWorker({"ui.privacy.review.request": lambda _params: {"approved": True}})
    flow, _native, ui, brain, _audio = make_flow(ui=ui)

    payload = {"approval_id": "privacy-1", "count": 1, "scrubbed_preview": "[EMAIL_1]"}
    flow._handle_privacy_review_request(payload)

    assert ui.last_call("ui.privacy.review.request")["params"] == payload
    response = brain.last_call("brain.privacy.review.respond")
    assert response["params"] == {
        "approval_id": "privacy-1",
        "approved": True,
        "decision": "redacted",
    }
    assert response["wait"] is False


def test_privacy_review_can_authorize_the_full_unredacted_message():
    """The explicit full-send decision must reach the blocked brain request."""
    ui = FakeWorker(
        {"ui.privacy.review.request": lambda _params: {"approved": True, "decision": "full"}}
    )
    flow, _native, ui, brain, _audio = make_flow(ui=ui)

    flow._handle_privacy_review_request(
        {"approval_id": "privacy-full", "count": 1, "scrubbed_preview": "[PERSON_1]"}
    )

    assert brain.last_call("brain.privacy.review.respond")["params"] == {
        "approval_id": "privacy-full",
        "approved": True,
        "decision": "full",
    }


def context_handler(
    selected: str = "selected",
    clipboard: str = "",
    pid: int = 42,
    focus_token: int = 0,
    selected_paths: list[str] | None = None,
):
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        result = {
            "selected_text": selected,
            "clipboard_text": clipboard,
            "active_app": {"name": "Notes", "pid": pid, "bundle_id": "com.apple.Notes"},
        }
        if params.get("include_selected_paths"):
            result["selected_paths"] = list(selected_paths or [])
        # Mirror the native worker: a paste-back caller asks to capture the
        # focused element and gets a token back for AX in-place write.
        if params.get("capture_focus"):
            result["focus_token"] = focus_token
        return result

    return handler


def browser_context_handler(selected: str = "selected"):
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        result = {
            "selected_text": selected,
            "clipboard_text": "",
            "active_app": {"name": "Browser", "pid": 42, "bundle_id": "com.browser"},
        }
        # The page fetch is deferred off the picker path: begin_caller's snapshot
        # asks without it, and the query-time fetch asks with it.
        if params.get("include_browser_content"):
            result["browser_url"] = "https://example.test/page"
            result["browser_content"] = "Example page text"
        return result

    return handler


@pytest.mark.parametrize(
    ("active_app", "provider_id", "suggestion_id"),
    [
        (
            {"name": "Contact - Google Chrome", "process_name": "chrome.exe", "pid": 42},
            "browser",
            "browser.fill_form",
        ),
        (
            {"name": "demo.py - Visual Studio Code", "process_name": "Code.exe", "pid": 42},
            "vscode",
            "vscode.fix_selection",
        ),
        (
            {"name": "demo.py – project – PyCharm", "process_name": "pycharm64.exe", "pid": 43},
            "code_editors",
            "code_editor.fix_selection",
        ),
        (
            {"name": "Budget.ods - LibreOffice Calc", "process_name": "soffice.bin", "pid": 42},
            "libreoffice_calc",
            "calc.add_chart",
        ),
    ],
)
def test_caller_detects_action_provider_from_pre_picker_context(
    active_app: dict[str, Any],
    provider_id: str,
    suggestion_id: str,
) -> None:
    """The General caller derives app suggestions from the hotkey-time app snapshot."""
    order: list[str] = []

    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        order.append("snapshot")
        return {"active_app": active_app, "selected_text": "", "clipboard_text": ""}

    def show_intent(params: dict[str, Any]) -> dict[str, Any]:
        order.append("show_intent")
        assert params["action_provider"] == {}
        assert params["defer_focus"] is True
        return {}

    native = FakeWorker({"native.context.snapshot": snapshot})
    ui = FakeWorker({"ui.show_intent": show_intent})
    with caller_config([{"context_clipboard": False}]):
        flow, _native, _ui, _brain, _audio = make_flow(native=native, ui=ui)
        flow.begin_caller(0)

    provider = ui.last_call("ui.intent.action_provider")["params"]["action_provider"]
    assert provider["id"] == provider_id
    assert provider["suggested_intents"][0]["id"] == suggestion_id
    assert provider["suggested_intents"][0]["mode"] == "action"
    assert provider["suggested_intents"][0]["planning_tool"]
    assert order[:2] == ["snapshot", "show_intent"]


def test_unsupported_app_keeps_generic_intent_picker_fallback() -> None:
    """Apps without an action provider still receive the ordinary caller picker."""
    native = FakeWorker({
        "native.context.snapshot": lambda _params: {
            "active_app": {"name": "Notes", "process_name": "notes.exe", "pid": 42},
            "selected_text": "hello",
            "clipboard_text": "",
        }
    })
    with caller_config([{"context_clipboard": False}]):
        flow, _native, ui, _brain, _audio = make_flow(native=native)
        flow.begin_caller(0)

    assert ui.last_call("ui.show_intent")["params"]["action_provider"] == {}
    assert ui.last_call("ui.intent.action_provider")["params"]["action_provider"] == {}


def test_wayland_accessibility_text_supplies_active_document_without_brain_read():
    """Hotkey-time AT-SPI text is reused as generic app-document context."""
    flow, _native, _ui, brain, _audio = make_flow()
    context = {
        "active_app": {"name": "Untitled - Kate", "process_name": "kate"},
        "active_window_text": "unselected Wayland editor content",
    }

    assert flow._fetch_active_document_text(context) == "unselected Wayland editor content"
    assert context["active_document_sources"][0]["label"] == "kate - Untitled - Kate"
    assert not brain.calls_for("brain.context.active_document")


def test_wayland_accessibility_text_falls_back_for_browser_content():
    """Captured browser accessibility text survives the overlay taking focus."""
    native = FakeWorker({"native.context.browser_content": lambda _params: {"content": ""}})
    flow, native, _ui, _brain, _audio = make_flow(native=native)
    context = {
        "active_app": {"name": "Example - Firefox", "process_name": "firefox"},
        "browser_url": "https://example.test/private",
        "active_window_text": "signed-in page content",
    }

    assert flow._fetch_browser_content_for_context(context) == {
        "browser_url": "https://example.test/private",
        "browser_content": "signed-in page content",
    }
    assert native.last_call("native.context.browser_content")["params"]["url"] == "https://example.test/private"


def query_stream(reply: str = "reply"):
    def handler(_params: dict[str, Any], on_event) -> dict[str, Any]:
        on_event("reply.chunk", {"text": reply}, 1)
        on_event("reply.done", {"text": reply}, 1)
        return {"text": reply}

    return handler


def rewrite_stream(replacement: str = "replacement", visible: str = ""):
    def handler(_params: dict[str, Any], on_event) -> dict[str, Any]:
        if visible:
            on_event("reply.chunk", {"text": visible}, 1)
        on_event("reply.done", {"text": replacement, "visible_text": visible}, 1)
        return {"text": replacement, "visible_text": visible}

    return handler


def action_plan_stream(arguments: dict[str, Any], visible: str = "Prepared the action for review."):
    """Return one forced planning-tool result through the fake brain stream."""
    def handler(params: dict[str, Any], on_event) -> dict[str, Any]:
        result = {
            "tool_name": str(params.get("planning_tool_name") or ""),
            "arguments": dict(arguments),
            "visible_text": visible,
        }
        if visible:
            on_event("reply.chunk", {"text": visible, "is_progress": True}, 1)
        on_event("reply.done", result, 1)
        return result

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
        _flow, native, ui, brain, audio = make_flow(native=native)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})

    assert ui.calls_for("ui.show_overlay")
    assert ui.last_call("ui.prewarm_intent")["wait"] is False
    assert brain.last_call("brain.privacy.prewarm")["wait"] is False
    assert brain.last_call("brain.harness.prewarm")["wait"] is False
    prefix = brain.last_call("brain.llm.prefix.prewarm")
    assert prefix["wait"] is False
    assert prefix["params"]["route_kind"] == "query"
    assert audio.last_call("audio.prewarm")["wait"] is False
    assert native.last_call("native.context.snapshot")["params"]["include_selection"] is True
    assert native.last_call("native.context.snapshot")["params"]["selection_dedupe_key"] == "intent"
    assert ui.last_call("ui.show_intent")["params"]["caller_idx"] == 0
    assert not ui.calls_for("ui.reply.listening")


def test_default_discrete_shortcuts_use_production_callbacks_and_runtime_event_path(monkeypatch):
    """A configured chord's real listener callback must reach its user flow."""

    from core import hotkeys as core_hotkeys
    from runtime.workers import native_host

    rows = [
        {
            "enabled": True,
            "hotkey": config._caller_default_hotkey(0),
            "hotkey_2": "",
            "paste_back": False,
            "context_ambient": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        },
        {
            "enabled": True,
            "hotkey": config._caller_default_hotkey(1),
            "hotkey_2": "",
            "paste_back": True,
            "context_ambient": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        },
    ]
    monkeypatch.setattr(config, "CALLER_ROWS", rows)
    for name, value in {
        "HOTKEY_ADD_CONTEXT": "alt+q",
        "HOTKEY_ADD_CONTEXT_2": "",
        "HOTKEY_CLEAR_CONTEXT": "alt+w",
        "HOTKEY_CLEAR_CONTEXT_2": "",
        "HOTKEY_SNIP": "ctrl+alt+q",
        "HOTKEY_SNIP_2": "",
        "HOTKEY_READ_SELECTION_ALOUD": "f7",
        "HOTKEY_READ_SELECTION_ALOUD_2": "",
        "HOTKEY_VOICE_LIVE": "shift+f9",
        "HOTKEY_VOICE_LIVE_2": "",
        "HOTKEY_VOICE": "f9",
        "HOTKEY_VOICE_2": "",
        "HOTKEY_DICTATE": "f8",
        "HOTKEY_DICTATE_2": "",
        "TTS_PROVIDER": "kokoro",
    }.items():
        monkeypatch.setattr(config, name, value, raising=False)

    native = FakeWorker({"native.context.snapshot": context_handler(selected="shortcut selection")})
    audio = FakeWorker(
        {
            "audio.tts.synthesize": lambda _params: {"path": "spoken.wav"},
            "audio.play_file": lambda _params: {"played": True},
            "audio.live.start": lambda _params: {"started": True, "model": "gemini-test"},
        }
    )
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)
    native_host.set_event_sink(lambda name, data, _req_id: native.emit(name, data))
    monkeypatch.setattr(core_hotkeys.HotkeyListener, "start", lambda _self: True)
    monkeypatch.setattr(
        core_hotkeys.HotkeyListener,
        "status",
        lambda self: {"started": True, "registered": len(self._hotkey_defs)},
    )
    backend = native_host._DirectHotkeys()
    try:
        assert backend.start()["started"] is True
        listener = backend.listener
        callbacks = {combo: callback for combo, callback in listener._hotkey_defs}

        callbacks[config._caller_default_hotkey(0)]()
        assert ui.last_call("ui.show_intent")["params"]["caller_idx"] == 0
        ui.emit("ui.intent.cancelled", {})

        callbacks[config._caller_default_hotkey(1)]()
        rewrite = ui.last_call("ui.rewrite.annotation.show")["params"]
        assert rewrite["selected_text"] == "shortcut selection"
        ui.emit("ui.rewrite.annotation.declined", {"annotation_id": rewrite["annotation_id"]})

        callbacks["ctrl+alt+q"]()
        assert ui.calls_for("ui.show_snip")
        ui.emit("ui.snip.cancelled", {})

        callbacks["alt+q"]()
        assert ui.last_call("ui.context.add_item")["params"] == {
            "name": "Selection",
            "item_type": "text",
        }
        callbacks["alt+w"]()
        assert ui.calls_for("ui.context.clear")

        callbacks["f7"]()
        assert audio.last_call("audio.tts.synthesize")["params"]["text"] == "shortcut selection"

        callbacks["shift+f9"]()
        assert audio.calls_for("audio.live.start")

        # Hold-to-talk bindings use the same production listener but are kept
        # as press/release callbacks rather than discrete hotkey definitions.
        assert listener._voice_hotkeys == ("f9",)
        assert listener._dictate_hotkeys == ("f8",)
    finally:
        backend.stop()
        native_host.set_event_sink(lambda _name, _data, _req_id: None)


def test_caller_hotkey_is_ignored_while_settings_is_open():
    """Remapping a registered caller hotkey should not summon the intent overlay."""
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
    ui = FakeWorker({"ui.settings.is_open": lambda _params: {"open": True}})
    with caller_config(rows):
        _flow, native, ui, _brain, _audio = make_flow(native=native, ui=ui)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})

    assert not native.calls_for("native.context.snapshot")
    assert not ui.calls_for("ui.show_intent")


def test_audio_warmup_events_surface_user_notices():
    """Verify local audio warmup start and finish are visible to the user."""
    flow, _native, ui, _brain, audio = make_flow()

    audio.emit("audio.warmup.started", {"items": ["stt", "tts"], "provider": "kokoro"})
    audio.emit("audio.warmup.progress", {"item": "stt", "status": "started", "items": ["stt", "tts"]})
    audio.emit("audio.warmup.progress", {"item": "stt", "status": "ok", "items": ["stt", "tts"]})
    audio.emit("audio.warmup.progress", {"item": "tts", "status": "started", "items": ["stt", "tts"]})
    audio.emit(
        "audio.warmup.done",
        {"items": ["stt", "tts"], "provider": "kokoro", "ok": True, "result": {"stt": "ok", "tts": "ok"}},
    )

    notices = [call["params"]["text"] for call in ui.calls_for("ui.reply.notice")]
    assert len(notices) == 3
    assert notices[0].startswith("Preparing speech services - 0s elapsed.")
    assert "STT (speech recognition): waiting to start" in notices[0]
    assert "TTS (Kokoro local voice): waiting to start" in notices[0]
    assert "STT (speech recognition): ready" in notices[1]
    assert notices[2] == (
        "Speech services are ready.\n"
        "STT (speech recognition): ready\n"
        "TTS (Kokoro local voice): ready"
    )
    assert all(call["params"]["key"] == "audio-warmup" for call in ui.calls_for("ui.reply.notice"))


def test_audio_warmup_done_does_not_announce_skipped_tts():
    """Skipped optional TTS should not be reported as warmed or ready."""
    _flow, _native, ui, _brain, audio = make_flow()

    audio.emit(
        "audio.warmup.done",
        {
            "items": ["stt", "tts"],
            "provider": "kokoro",
            "ok": True,
            "result": {"stt": "ok", "tts": "skipped"},
        },
    )

    assert ui.last_call("ui.reply.notice")["params"]["text"] == (
        "Speech services are ready.\n"
        "STT (speech recognition): ready\n"
        "TTS (Kokoro local voice): not needed"
    )


def test_audio_warmup_done_uses_remote_tts_wording():
    """Remote/API TTS prewarm should not be described as a local voice install."""
    _flow, _native, ui, _brain, audio = make_flow()

    audio.emit(
        "audio.warmup.done",
        {
            "items": ["stt", "tts"],
            "provider": "cartesia",
            "ok": True,
            "result": {"stt": "ok", "tts": "ok"},
        },
    )

    assert ui.last_call("ui.reply.notice")["params"]["text"] == (
        "Speech services are ready.\n"
        "STT (speech recognition): ready\n"
        "TTS (Cartesia connection): ready"
    )


def test_audio_warmup_failure_surfaces_user_notice():
    """Verify local audio warmup failures are visible to the user."""
    _flow, _native, ui, _brain, audio = make_flow()

    audio.emit(
        "audio.warmup.done",
        {
            "items": ["tts"],
            "provider": "kokoro",
            "ok": False,
            "result": {"tts": "error: RuntimeError: missing model"},
        },
    )

    notice = ui.last_call("ui.reply.notice")
    assert notice["params"]["text"] == (
        "Speech warm-up failed.\n"
        "TTS (Kokoro local voice): failed - RuntimeError: missing model"
    )
    assert notice["params"]["severity"] == "error"
    assert notice["params"]["key"] == "audio-warmup"


def test_transient_kokoro_warmup_failure_finishes_persistent_notice_as_deferred():
    """A transient TTS result must replace, not strand, the persistent timer."""
    _flow, _native, ui, _brain, audio = make_flow()
    status = (
        "error: RuntimeError: Kokoro is still warming up. "
        "Current stage: importing kokoro.KPipeline (17s). Try again when local speech is ready."
    )

    audio.emit("audio.warmup.progress", {"item": "tts", "status": status, "items": ["tts"]})
    audio.emit(
        "audio.warmup.done",
        {
            "items": ["tts"],
            "provider": "kokoro",
            "ok": False,
            "result": {"tts": status},
        },
    )

    notice = ui.last_call("ui.reply.notice")
    assert "one service will retry when needed" in notice["params"]["text"]
    assert "TTS (Kokoro local voice): will retry when first used" in notice["params"]["text"]
    assert notice["params"]["severity"] == "warning"
    assert notice["params"]["key"] == "audio-warmup"


def test_audio_warmup_timer_runs_in_supervisor_when_audio_sends_no_progress(monkeypatch):
    """The bubble timer keeps moving even while the audio worker is blocked."""
    from runtime.supervisor import flows as flows_module

    monkeypatch.setattr(flows_module, "_SPEECH_WARMUP_NOTICE_INTERVAL_SECONDS", 0.01)
    _flow, _native, ui, _brain, audio = make_flow()

    audio.emit("audio.warmup.started", {"items": ["stt", "tts"], "provider": "kokoro"})
    deadline = time.monotonic() + 0.3
    while len(ui.calls_for("ui.reply.notice")) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    audio.emit(
        "audio.warmup.done",
        {"items": ["stt", "tts"], "provider": "kokoro", "result": {"stt": "ok", "tts": "ok"}},
    )

    timer_notices = [
        call["params"]["text"]
        for call in ui.calls_for("ui.reply.notice")
        if call["params"]["text"].startswith("Preparing speech services")
    ]
    assert len(timer_notices) >= 3
    assert all("STT (speech recognition):" in text for text in timer_notices)
    assert all("TTS (Kokoro local voice):" in text for text in timer_notices)


def test_stale_audio_warmup_events_cannot_overwrite_newer_notice():
    """An older startup/config warmup cannot finish a newer keyed timer."""
    _flow, _native, ui, _brain, audio = make_flow()

    audio.emit(
        "audio.warmup.started",
        {"items": ["stt"], "provider": "none", "warmup_id": "new"},
    )
    audio.emit(
        "audio.warmup.progress",
        {"item": "stt", "status": "ok", "items": ["stt"], "warmup_id": "old"},
    )
    audio.emit(
        "audio.warmup.done",
        {"items": ["stt"], "provider": "none", "warmup_id": "old", "result": {"stt": "ok"}},
    )

    assert len(ui.calls_for("ui.reply.notice")) == 1
    assert "waiting to start" in ui.last_call("ui.reply.notice")["params"]["text"]

    audio.emit(
        "audio.warmup.done",
        {"items": ["stt"], "provider": "none", "warmup_id": "new", "result": {"stt": "ok"}},
    )
    assert ui.last_call("ui.reply.notice")["params"]["text"].endswith(
        "STT (speech recognition): ready"
    )


def test_audio_worker_exit_finishes_persistent_warmup_notice():
    """A dead audio worker must not leave a permanent, frozen warm-up timer."""
    flow, _native, ui, _brain, audio = make_flow()

    audio.emit(
        "audio.warmup.started",
        {
            "items": ["stt", "tts"],
            "provider": "kokoro",
            "warmup_id": "startup:1",
        },
    )
    audio.emit(
        "audio.warmup.progress",
        {
            "item": "stt",
            "status": "started",
            "warmup_id": "startup:1",
        },
    )
    flow._on_audio_worker_exit(1)

    notice = ui.last_call("ui.reply.notice")["params"]
    assert notice["text"] == (
        "Speech warm-up was interrupted because the audio service restarted.\n"
        "STT (speech recognition): stopped\n"
        "TTS (Kokoro local voice): stopped"
    )
    assert notice["key"] == "audio-warmup"
    assert notice["severity"] == "warning"
    assert notice["timeout_ms"] == 8000

    # A queued completion event from the dead worker cannot replace the warning.
    audio.emit(
        "audio.warmup.done",
        {
            "items": ["stt", "tts"],
            "provider": "kokoro",
            "warmup_id": "startup:1",
            "result": {"stt": "ok", "tts": "ok"},
        },
    )
    assert ui.last_call("ui.reply.notice")["params"] == notice


def test_audio_warmup_terminal_notice_wins_timer_race():
    """A timer frame already being sent cannot overwrite the final result."""
    flow, _native, ui, _brain, audio = make_flow()
    audio.emit(
        "audio.warmup.started",
        {"items": ["stt"], "provider": "none", "warmup_id": "startup:race"},
    )
    generation = flow._speech_warmup_generation
    timer_entered = threading.Event()
    release_timer = threading.Event()
    original_fire = flow._fire

    def blocking_fire(worker, method, params=None):
        timer_entered.set()
        assert release_timer.wait(1.0)
        original_fire(worker, method, params)

    flow._fire = blocking_fire  # type: ignore[method-assign]
    timer_thread = threading.Thread(
        target=flow._show_speech_warmup_notice,
        args=(generation,),
    )
    timer_thread.start()
    assert timer_entered.wait(1.0)

    done_thread = threading.Thread(
        target=audio.emit,
        args=(
            "audio.warmup.done",
            {
                "items": ["stt"],
                "provider": "none",
                "warmup_id": "startup:race",
                "result": {"stt": "ok"},
            },
        ),
    )
    done_thread.start()
    deadline = time.monotonic() + 1.0
    while flow._speech_warmup_generation == generation and time.monotonic() < deadline:
        time.sleep(0.005)
    release_timer.set()
    timer_thread.join(1.0)
    done_thread.join(1.0)

    assert not timer_thread.is_alive()
    assert not done_thread.is_alive()
    assert ui.last_call("ui.reply.notice")["params"]["text"] == (
        "Speech services are ready.\nSTT (speech recognition): ready"
    )
    assert ui.last_call("ui.reply.notice")["params"]["timeout_ms"] == 6000


def test_flow_stop_cancels_speech_timer_and_rejects_new_warmups(monkeypatch):
    """Application shutdown leaves no timer producing UI work."""
    from runtime.supervisor import flows as flows_module

    monkeypatch.setattr(flows_module, "_SPEECH_WARMUP_NOTICE_INTERVAL_SECONDS", 0.01)
    flow, _native, ui, _brain, audio = make_flow()
    audio.emit("audio.warmup.started", {"items": ["stt"], "provider": "none"})
    deadline = time.monotonic() + 0.3
    while len(ui.calls_for("ui.reply.notice")) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    flow.stop()
    call_count = len(ui.calls_for("ui.reply.notice"))
    time.sleep(0.05)
    audio.emit("audio.warmup.started", {"items": ["tts"], "provider": "kokoro"})

    assert len(ui.calls_for("ui.reply.notice")) == call_count


def test_late_audio_progress_after_done_cannot_reopen_timer_notice():
    """Out-of-order progress from warm-up threads is ignored after completion."""
    _flow, _native, ui, _brain, audio = make_flow()
    payload = {"items": ["stt"], "provider": "none", "warmup_id": "startup:late"}
    audio.emit("audio.warmup.started", payload)
    audio.emit(
        "audio.warmup.done",
        {**payload, "result": {"stt": "ok"}},
    )
    final_notice = ui.last_call("ui.reply.notice")["params"]

    audio.emit(
        "audio.warmup.progress",
        {**payload, "item": "stt", "status": "started"},
    )

    assert ui.last_call("ui.reply.notice")["params"] == final_notice


def test_caller_hotkey_captures_selection_before_intent_steals_focus():
    """Showing the picker must not happen until source-app selection is captured."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": True,
        }
    ]
    order: list[str] = []
    selection_available = True

    def snapshot(params: dict[str, Any]) -> dict[str, Any]:
        """Simulate selection disappearing as soon as the picker is shown."""
        order.append("snapshot")
        assert params["include_selection"] is True
        assert params["include_clipboard"] is True
        assert params["include_selected_paths"] is True
        return {
            "selected_text": "selected before picker" if selection_available else "",
            "clipboard_text": "original clipboard",
            "active_app": {"name": "Codex", "pid": 42, "window_id": 777, "bundle_id": ""},
        }

    def show_intent(params: dict[str, Any]) -> dict[str, Any]:
        """Model the real Windows boundary that invalidated later UIA reads."""
        nonlocal selection_available
        assert params["defer_focus"] is True
        assert params["target_hwnd"] == 777
        order.append("show_intent")
        selection_available = False
        return {}

    def activate_intent(_params: dict[str, Any]) -> dict[str, Any]:
        order.append("activate_intent")
        return {}

    native = FakeWorker({"native.context.snapshot": snapshot})
    ui = FakeWorker({"ui.show_intent": show_intent, "ui.intent.activate": activate_intent})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native, ui=ui)
        _flow.begin_caller(0)

    assert order == ["snapshot", "show_intent", "activate_intent"]
    chips = {
        item["id"]: item
        for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
    }
    assert chips["selection"]["state"] == "on"
    assert chips["selection"]["tokens"].startswith("~")


def test_calc_selection_is_not_collected_while_intent_picker_is_open() -> None:
    """Calc must not invoke Copy while the popup-style intent picker is visible."""
    order: list[str] = []
    active_app = {
        "name": "Budget.ods — LibreOffice Calc",
        "process_name": "soffice.bin",
        "pid": 42,
        "window_id": 777,
    }

    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        order.append("snapshot")
        return {
            "platform": "win32",
            "active_app": active_app,
            "selected_text": "",
            "clipboard_text": "",
            "app_selection_deferred": True,
        }

    def show_intent(_params: dict[str, Any]) -> dict[str, Any]:
        order.append("show_intent")
        return {}

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot,
            "native.context.app_selection": lambda _params: pytest.fail(
                "Calc selection capture must wait until an action is chosen"
            ),
        }
    )
    ui = FakeWorker({"ui.show_intent": show_intent})
    with caller_config([{"context_clipboard": False}]):
        flow, _native, ui, _brain, _audio = make_flow(native=native, ui=ui)
        flow.begin_caller(0)

    assert order == ["snapshot", "show_intent"]
    chips = {
        item["id"]: item
        for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
    }
    assert chips["selection"]["state"] == "off"
    assert flow._pending is not None  # noqa: SLF001 - contract-level flow state
    assert flow._pending.context["app_selection_deferred"] is True  # noqa: SLF001


def test_calc_chart_request_forces_planner_then_applies_without_rewrite() -> None:
    """A Calc chart is planned through a forced tool, never pasted into cells."""
    active_app = {
        "name": "Budget.ods — LibreOffice Calc",
        "process_name": "soffice.bin",
        "pid": 42,
        "window_id": 777,
    }
    selection = {
        "app": "libreoffice_calc",
        "document_title": active_app["name"],
        "window_id": 777,
        "pid": 42,
        "range": "A1:B3",
        "rows": 3,
        "columns": 2,
        "values": (("Month", "Revenue"), ("Jan", "12"), ("Feb", "20")),
        "selected_text": "Month\tRevenue\nJan\t12\nFeb\t20",
        "fingerprint": "test-fingerprint",
    }
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "platform": "win32",
                "active_app": active_app,
                "selected_text": "",
                "clipboard_text": "",
                "app_selection_deferred": True,
            },
            "native.action.calc.snapshot": lambda _params: {
                "ok": True,
                "selection": selection,
                "error": "",
            },
            "native.action.calc.apply": lambda params: {
                "ok": True,
                "result": {
                    "plan_id": params["plan"]["plan_id"],
                    "status": "applied",
                    "message": "Created a vertical bar chart from A1:B3.",
                },
                "error": "",
            },
        }
    )
    ui = FakeWorker(
        {
            "ui.show_intent": lambda _params: {},
            "ui.action.preview.request": lambda _params: {"approved": True},
        }
    )
    brain = FakeWorker(
        stream_handlers={"brain.action.plan": action_plan_stream({"title": "Revenue by month"})}
    )
    with caller_config([{"paste_back": False, "context_clipboard": False}]):
        flow, native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        flow.begin_caller(0)
        provider = ui.last_call("ui.intent.action_provider")["params"]["action_provider"]
        suggestion = provider["suggested_intents"][0]
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "create a graph",
                "intent_routing": {
                    "mode": suggestion["mode"],
                    "source": "provider",
                    "suggestion_id": suggestion["id"],
                    "provider_id": provider["id"],
                    "app": provider["app"],
                    "capability_type": suggestion["capability_type"],
                    "planning_tool": suggestion["planning_tool"],
                },
            },
        )

    preview = ui.last_call("ui.action.preview.request")["params"]
    assert preview["plan_id"]
    assert "A1:B3" in preview["html"]
    applied = native.last_call("native.action.calc.apply")["params"]
    assert applied["confirmed"] is True
    assert applied["plan"]["target"]["locator"]["window_id"] == "777"
    assert not native.calls_for("native.paste_text")
    assert not brain.calls_for("brain.rewrite")
    assert not brain.calls_for("brain.query")
    planner = brain.last_call("brain.action.plan")["params"]
    assert planner["planning_tool_name"] == "calc_plan_add_chart"
    assert planner["input_schema"]["required"] == ["title"]
    assert [
        call["params"]["stage"] for call in ui.calls_for("ui.action.progress")
    ] == [
        "targeting",
        "reading",
        "planning",
        "validating",
        "preparing_preview",
        "awaiting_approval",
        "applying",
        "complete",
    ]


@pytest.mark.parametrize(
    ("capability_type", "planning_tool", "arguments", "operation_type", "preview_text"),
    [
        (
            "calc.format_table@1",
            "calc_plan_format_table",
            {"has_header": True},
            "calc.format_table@1",
            "Keep every cell value",
        ),
        (
            "calc.sort_range@1",
            "calc_plan_sort_range",
            {"column_header": "Revenue", "direction": "descending"},
            "calc.sort_range@1",
            "Proposed order",
        ),
        (
            "calc.clean_range@1",
            "calc_plan_clean_range",
            {
                "changes": [{
                    "row_offset": 1,
                    "column_offset": 0,
                    "after_kind": "value",
                    "after_value": "January",
                }],
            },
            "calc.clean_range@1",
            "Every other selected value and formula stays unchanged",
        ),
    ],
)
def test_calc_non_chart_actions_use_forced_typed_planners(
    capability_type: str,
    planning_tool: str,
    arguments: dict[str, Any],
    operation_type: str,
    preview_text: str,
) -> None:
    active_app = {
        "name": "Budget.ods — LibreOffice Calc",
        "process_name": "soffice.bin",
        "pid": 42,
        "window_id": 777,
    }
    selection = {
        "app": "libreoffice_calc",
        "document_title": active_app["name"],
        "window_id": 777,
        "pid": 42,
        "range": "A1:B3",
        "values": (("Month", "Revenue"), ("Jan", "12"), ("Feb", "20")),
        "typed_values": (("Month", "Revenue"), ("Jan", 12.0), ("Feb", 20.0)),
        "formulas": (("Month", "Revenue"), ("Jan", "12"), ("Feb", "20")),
        "selected_text": "Month\tRevenue\nJan\t12\nFeb\t20",
        "fingerprint": "test-fingerprint",
    }
    native = FakeWorker({
        "native.action.calc.snapshot": lambda _params: {"ok": True, "selection": selection, "error": ""},
        "native.action.calc.apply": lambda params: {
            "ok": True,
            "result": {"status": "applied", "message": "Applied", "plan_id": params["plan"]["plan_id"]},
            "error": "",
        },
    })
    ui = FakeWorker({"ui.action.preview.request": lambda _params: {"approved": True}})
    brain = FakeWorker(stream_handlers={"brain.action.plan": action_plan_stream(arguments)})
    flow, native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
    pending = PendingInvocation(
        caller_idx=0,
        caller={"paste_back": True},
        context={"active_app": active_app},
    )

    flow._run_calc_chart_action(  # noqa: SLF001 - exercise the shared Calc action boundary
        pending,
        {},
        prompt="Do the selected Calc action",
        planning_tool=planning_tool,
        capability_type=capability_type,
    )

    planner = brain.last_call("brain.action.plan")["params"]
    assert planner["planning_tool_name"] == planning_tool
    preview = ui.last_call("ui.action.preview.request")["params"]
    assert preview_text in preview["html"]
    applied = native.last_call("native.action.calc.apply")["params"]
    assert applied["plan"]["operations"][0]["type"] == operation_type


def test_excel_capability_dispatches_to_the_excel_runtime(monkeypatch) -> None:
    flow, _native, _ui, _brain, _audio = make_flow()
    pending = PendingInvocation(caller_idx=0, caller={}, context={})
    captured: dict[str, Any] = {}

    def run_excel(*args: Any, **kwargs: Any) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(flow, "_run_excel_action", run_excel)
    flow._dispatch_provider_action(  # noqa: SLF001 - exercise typed dispatch boundary
        pending,
        "Create a chart",
        {
            "capability_type": "excel.add_chart@1",
            "planning_tool": "excel_plan_add_chart",
            "provider_id": "excel",
        },
        active_app={"process_name": "excel.exe"},
        browser_app={},
        app_selection={},
        selected_text="",
    )

    assert captured["args"] == (pending, "Create a chart")
    assert captured["kwargs"] == {
        "planning_tool": "excel_plan_add_chart",
        "capability_type": "excel.add_chart@1",
        "provider_id": "excel",
    }


def test_excel_cleanup_dispatches_to_the_preview_first_runtime(monkeypatch) -> None:
    flow, _native, _ui, _brain, _audio = make_flow()
    pending = PendingInvocation(caller_idx=0, caller={}, context={})
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        flow,
        "_run_excel_action",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs),
    )
    flow._dispatch_provider_action(  # noqa: SLF001 - typed cleanup dispatch boundary
        pending,
        "Clean up this export",
        {
            "capability_type": "excel.clean_range@1",
            "planning_tool": "excel_plan_clean_range",
            "provider_id": "excel",
        },
        active_app={"process_name": "excel.exe"},
        browser_app={},
        app_selection={},
        selected_text="",
    )

    assert captured == {
        "args": (pending, "Clean up this export"),
        "kwargs": {
            "planning_tool": "excel_plan_clean_range",
            "capability_type": "excel.clean_range@1",
            "provider_id": "excel",
        },
    }


def test_excel_answer_action_attaches_structured_selected_cells(monkeypatch) -> None:
    class FakeExcelProvider:
        def snapshot(self, context: dict[str, Any]) -> object:
            assert context["active_app"]["process_name"] == "excel.exe"
            return object()

        @staticmethod
        def answer_context(_snapshot: object) -> dict[str, Any]:
            return {
                "app": "excel",
                "selection_address": "B2",
                "formula_context": {
                    "status": "single_cell_formula",
                    "cells": [{"address": "B2", "formula": "=A2*2", "displayed_value": 12}],
                },
                "selected_text": "[Excel selected cells]\nB2 formula: =A2*2; displayed value: 12",
            }

    monkeypatch.setattr("core.actions.adapters.excel.ExcelRuntimeProvider", FakeExcelProvider)
    flow, _native, _ui, _brain, _audio = make_flow()
    pending = PendingInvocation(caller_idx=0, caller={"paste_back": True}, context={})

    assert flow._attach_provider_answer_context(  # noqa: SLF001 - answer-context boundary
        pending,
        {"source": "provider", "provider_id": "excel", "suggestion_id": "explain_formula"},
        active_app={"process_name": "excel.exe"},
    )
    assert pending.context["app_selection"]["formula_context"]["status"] == "single_cell_formula"
    assert "=A2*2" in pending.context["selected_text"]
    assert pending.caller["_context_selection_enabled"] is True
    assert pending.caller["paste_back"] is False


def test_calc_answer_action_attaches_values_and_formula_grid() -> None:
    active_app = {
        "name": "Budget.ods — LibreOffice Calc",
        "process_name": "soffice.bin",
        "pid": 42,
        "window_id": 777,
    }
    native = FakeWorker({
        "native.action.calc.snapshot": lambda params: {
            "ok": params["active_app"] == active_app,
            "selection": {
                "app": "libreoffice_calc",
                "range": "A1:B2",
                "rows": 2,
                "columns": 2,
                "values": [["Amount", "Tax"], [10, 2]],
                "formulas": [["Amount", "Tax"], [10, "=A2*0.2"]],
                "selected_text": "Amount\tTax\n10\t2",
                "fingerprint": "calc-answer-fingerprint",
            },
            "error": "",
        },
    })
    flow, native, _ui, _brain, _audio = make_flow(native=native)
    pending = PendingInvocation(caller_idx=0, caller={"paste_back": True}, context={})

    assert flow._attach_provider_answer_context(  # noqa: SLF001 - answer-context boundary
        pending,
        {"source": "provider", "provider_id": "libreoffice_calc", "suggestion_id": "explain_formula"},
        active_app=active_app,
    )
    assert "Amount\tTax" in pending.context["selected_text"]
    assert "=A2*0.2" in pending.context["selected_text"]
    assert pending.context["app_selection_deferred"] is False
    assert native.last_call("native.action.calc.snapshot")["params"]["active_app"] == active_app


@pytest.mark.parametrize(
    "provider_id",
    ["word_desktop", "libreoffice_writer", "powerpoint_desktop", "libreoffice_impress"],
)
def test_document_answer_action_attaches_the_active_document(monkeypatch, provider_id: str) -> None:
    flow, _native, _ui, _brain, _audio = make_flow()
    pending = PendingInvocation(
        caller_idx=0,
        caller={"context_ambient": False, "context_documents_mode": "off", "paste_back": True},
        context={
            "active_app": {"process_name": "WINWORD.EXE"},
            "active_document_text": "Wrong text from several open documents",
        },
    )
    reads: list[bool] = []

    def fetch(_context: dict[str, Any], *, active_only: bool = False) -> str:
        reads.append(active_only)
        return "Heading\nDocument body"

    monkeypatch.setattr(flow, "_fetch_active_document_text", fetch)

    assert flow._attach_provider_answer_context(  # noqa: SLF001 - document answer-context boundary
        pending,
        {"source": "provider", "provider_id": provider_id, "suggestion_id": "summarize_document"},
        active_app=pending.context["active_app"],
    )
    assert reads == [True]
    assert pending.context["active_document_text"] == "Heading\nDocument body"
    assert pending.caller["context_ambient"] is True
    assert pending.caller["context_documents_mode"] == "auto"
    assert pending.caller["paste_back"] is False


@pytest.mark.parametrize("provider_id", ["powerpoint_web", "google_slides", "google_docs"])
def test_web_document_answer_action_attaches_browser_content(monkeypatch, provider_id: str) -> None:
    flow, _native, _ui, _brain, _audio = make_flow()
    pending = PendingInvocation(
        caller_idx=0,
        caller={"context_browser_mode": "off", "paste_back": True},
        context={"browser_url": "https://slides.example/deck", "browser_hwnd": 777},
    )
    monkeypatch.setattr(
        flow,
        "_fetch_browser_content_for_context",
        lambda _context: {
            "browser_url": "https://slides.example/deck",
            "browser_content": "Slide 1: Launch plan\nSlide 2: Risks",
        },
    )

    assert flow._attach_provider_answer_context(  # noqa: SLF001 - web deck context boundary
        pending,
        {"source": "provider", "provider_id": provider_id, "suggestion_id": "summarize_deck"},
        active_app={"process_name": "chrome.exe"},
    )
    assert "Launch plan" in pending.context["browser_content"]
    assert pending.caller["context_browser_mode"] == "auto"
    assert pending.caller["paste_back"] is False


def test_calc_selection_failure_after_action_choice_does_not_mutate() -> None:
    """A failed deferred Calc read warns after choice and never plans or applies."""
    active_app = {
        "name": "Budget.ods — LibreOffice Calc",
        "process_name": "soffice.bin",
        "pid": 42,
        "window_id": 777,
    }
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "platform": "win32",
                "active_app": active_app,
                "selected_text": "",
                "clipboard_text": "",
                "app_selection_deferred": True,
            },
            "native.action.calc.snapshot": lambda _params: {
                "ok": False,
                "selection": {},
                "error": "RuntimeError: LibreOffice could not snapshot the selected range.",
            },
        }
    )
    ui = FakeWorker({"ui.show_intent": lambda _params: {}})
    with caller_config([{"paste_back": False, "context_clipboard": False}]):
        flow, native, ui, brain, _audio = make_flow(native=native, ui=ui)
        flow.begin_caller(0)
        assert not native.calls_for("native.action.calc.snapshot")
        provider = ui.last_call("ui.intent.action_provider")["params"]["action_provider"]
        suggestion = provider["suggested_intents"][0]
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "create a graph",
                "intent_routing": {
                    "mode": suggestion["mode"],
                    "source": "provider",
                    "suggestion_id": suggestion["id"],
                    "provider_id": provider["id"],
                    "app": provider["app"],
                    "capability_type": suggestion["capability_type"],
                    "planning_tool": suggestion["planning_tool"],
                },
            },
        )

    assert native.calls_for("native.action.calc.snapshot")
    assert "couldn't read the selected Calc cells" in ui.last_call("ui.reply.notice")["params"]["text"]
    assert not brain.calls_for("brain.action.plan")
    assert not native.calls_for("native.action.calc.apply")


def test_unrecognized_calc_request_never_pastes_over_cells() -> None:
    pending = PendingInvocation(
        caller_idx=0,
        caller={"paste_back": True},
        context={
            "app_selection": {
                "app": "libreoffice_calc",
                "range": "A1:B3",
                "selected_text": "Month\tRevenue",
            }
        },
    )
    pending.context_ready.set()
    flow, native, _ui, brain, _audio = make_flow(
        brain=FakeWorker(stream_handlers={"brain.query": query_stream("Here is an explanation.")})
    )
    flow._pending = pending  # noqa: SLF001 - direct safety-boundary setup

    flow.intent_chosen("explain these numbers")

    assert brain.calls_for("brain.query")
    assert not brain.calls_for("brain.rewrite")
    assert not native.calls_for("native.paste_text")


def test_first_prompt_only_setting_suppresses_untouched_context_on_continue(monkeypatch) -> None:
    """The supervisor enforces continuation defaults even if an older UI sends caller defaults."""
    import config

    monkeypatch.setattr(config, "CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY", True, raising=False)
    pending = PendingInvocation(
        caller_idx=0,
        caller={
            "paste_back": False,
            "context_ambient": True,
            "context_clipboard": True,
            "context_browser_mode": "auto",
            "context_memory_mode": "on",
            "file_access": "read",
        },
        context={},
    )
    pending.context_ready.set()
    flow, _native, _ui, _brain, _audio = make_flow()
    flow._pending = pending  # noqa: SLF001 - direct context-policy boundary setup
    captured = []
    flow._query = lambda prompt, invocation: captured.append((prompt, invocation))  # type: ignore[method-assign]  # noqa: SLF001

    flow.intent_chosen(
        "continue without fresh context",
        context_choices=[
            {"id": "ambient", "state": "on", "default_state": "on", "touched": False},
            {"id": "clipboard", "state": "on", "default_state": "on", "touched": False},
            {"id": "browser", "state": "on", "default_state": "on", "touched": False},
            {"id": "memory", "state": "on", "default_state": "on", "touched": False},
            {"id": "files", "state": "on", "default_state": "on", "touched": False},
            {"id": "selection", "state": "on", "default_state": "off", "touched": True},
        ],
        conversation_choice={"mode": "continue", "index": 0},
    )

    assert captured == [("continue without fresh context", pending)]
    assert pending.caller["context_ambient"] is False
    assert pending.caller["context_clipboard"] is False
    assert pending.caller["context_browser_mode"] == "off"
    assert pending.caller["context_memory_mode"] == "off"
    assert pending.caller["file_access"] == "off"
    assert pending.caller["_context_selection_enabled"] is True


def test_addon_prompt_intent_uses_normal_query_path() -> None:
    pending = PendingInvocation(caller_idx=0, caller={"paste_back": False}, context={})
    pending.context_ready.set()
    flow, _native, _ui, _brain, _audio = make_flow()
    flow._pending = pending  # noqa: SLF001 - direct routing-boundary setup
    captured = []
    flow._query = lambda prompt, invocation: captured.append((prompt, invocation))  # type: ignore[method-assign]  # noqa: SLF001

    flow.intent_chosen(
        "Declared addon prompt",
        intent_routing={
            "mode": "addon",
            "source": "addon",
            "addon_id": "demo",
            "action_id": "prompt-action",
            "callback": False,
        },
    )

    assert captured == [("Declared addon prompt", pending)]


def test_addon_callback_intent_crosses_brain_host_boundary() -> None:
    pending = PendingInvocation(caller_idx=2, caller={"paste_back": False}, context={"selected_text": "hello"})
    pending.context_ready.set()
    brain = FakeWorker({"brain.addons.run_intent": lambda params: {"message": f"ran {params['action_id']}"}})
    flow, _native, ui, brain, _audio = make_flow(brain=brain)
    flow._pending = pending  # noqa: SLF001 - direct routing-boundary setup

    flow.intent_chosen(
        "",
        intent_routing={
            "mode": "addon",
            "source": "addon",
            "addon_id": "demo",
            "action_id": "callback-action",
            "callback": True,
        },
    )

    call = brain.last_call("brain.addons.run_intent")["params"]
    assert call["addon_id"] == "demo"
    assert call["action_id"] == "callback-action"
    assert call["payload"]["caller_idx"] == 2
    assert ui.last_call("ui.reply.notice")["params"]["text"].startswith("ran callback-action")


def test_every_available_shipped_app_capability_has_a_supervisor_dispatch_branch() -> None:
    """An available catalogue row must not load successfully and then fall through at execution."""
    from core.action_files import load_catalog

    catalog = load_catalog(Path(__file__).parents[2] / "assets" / "callers")
    actions = [
        bound.action
        for app in catalog.apps
        for bound in app.actions
        if bound.action.available and bound.action.capability
    ]
    flow = FlowController.__new__(FlowController)
    branch_calls: list[str] = []
    flow._run_browser_form_action = lambda *_args, **_kwargs: branch_calls.append("browser")  # type: ignore[method-assign]  # noqa: SLF001
    flow._run_vscode_fix_action = lambda *_args, **_kwargs: branch_calls.append("vscode")  # type: ignore[method-assign]  # noqa: SLF001
    flow._run_calc_chart_action = lambda *_args, **_kwargs: branch_calls.append("calc")  # type: ignore[method-assign]  # noqa: SLF001
    flow._run_excel_action = lambda *_args, **_kwargs: branch_calls.append("excel")  # type: ignore[method-assign]  # noqa: SLF001
    flow._run_powerpoint_action = lambda *_args, **_kwargs: branch_calls.append("presentation")  # type: ignore[method-assign]  # noqa: SLF001
    flow._notice = lambda *_args, **_kwargs: branch_calls.append("unsupported")  # type: ignore[method-assign]  # noqa: SLF001
    flow._set_idle = lambda: None  # type: ignore[method-assign]  # noqa: SLF001
    pending = PendingInvocation(caller_idx=0, caller={}, context={})

    expected = {
        "browser.fill_form": "browser",
        "vscode.replace_selection@1": "vscode",
            "calc.add_chart@1": "calc",
            "calc.clean_range@1": "calc",
            "calc.format_table@1": "calc",
            "calc.sort_range@1": "calc",
            "excel.add_chart@1": "excel",
            "excel.clean_range@1": "excel",
            "excel.create_table@1": "excel",
        "excel.sort_range@1": "excel",
        "presentation.create_slide@1": "presentation",
        "presentation.restyle_slide@1": "presentation",
        "presentation.upsert_speaker_notes@1": "presentation",
    }
    assert {action.capability for action in actions} == set(expected)
    for action in actions:
        before = len(branch_calls)
        flow._dispatch_provider_action(  # noqa: SLF001 - production dispatch boundary
            pending,
            action.prompt,
            {
                "provider_id": "catalogue-test",
                "capability_type": action.capability,
                "planning_tool": action.planner,
            },
            active_app={},
            browser_app={},
            app_selection={},
            selected_text="",
        )
        assert branch_calls[before:] == [expected[action.capability]], action.path


def test_vscode_fix_request_uses_model_diff_preview_then_safe_apply() -> None:
    """A selected-code fix is an action plan, not direct keyboard paste-back."""
    selected = "def add_one(value):\n    return value"
    fixed = "def add_one(value):\n    return value + 1"
    active_app = {
        "name": "demo.py - project - Visual Studio Code",
        "process_name": "Code.exe",
        "pid": 42,
        "window_id": 777,
    }
    snapshot = {
        "app": "vscode",
        "file_path": "C:\\project\\demo.py",
        "display_name": "demo.py",
        "window_id": 777,
        "pid": 42,
        "text": f"# demo\n{selected}\n",
        "selected_text": selected,
        "selection_start": 7,
        "selection_end": 7 + len(selected),
        "fingerprint": "file-fingerprint",
        "selection_fingerprint": __import__("hashlib").sha256(selected.encode()).hexdigest(),
        "has_utf8_bom": False,
    }
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "platform": "win32",
                "active_app": active_app,
                "selected_text": selected,
                "clipboard_text": "",
                "app_selection_deferred": False,
            },
            "native.action.vscode.snapshot": lambda _params: {
                "ok": True,
                "snapshot": snapshot,
                "error": "",
            },
            "native.action.vscode.apply": lambda params: {
                "ok": True,
                "result": {
                    "plan_id": params["plan"]["plan_id"],
                    "status": "applied",
                    "message": "Updated demo.py.",
                },
                "error": "",
            },
        }
    )
    ui = FakeWorker(
        {
            "ui.show_intent": lambda _params: {},
            "ui.action.preview.request": lambda _params: {"approved": True},
        }
    )
    brain = FakeWorker(
        stream_handlers={
            "brain.action.plan": action_plan_stream(
                {"replacement_text": fixed},
                "The function returned the input unchanged; the replacement increments it.",
            )
        }
    )

    with caller_config([{"paste_back": False, "context_clipboard": False}]):
        flow, native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "fix this bug"})

    model_call = brain.last_call("brain.action.plan")["params"]
    assert model_call["planning_tool_name"] == "vscode_plan_replace_selection"
    assert model_call["app_context"]["selected_text"] == selected
    assert "state the issue" in model_call["user_prompt"]
    preview = ui.last_call("ui.action.preview.request")["params"]
    assert "return value + 1" in preview["html"]
    applied = native.last_call("native.action.vscode.apply")["params"]
    assert applied["confirmed"] is True
    assert applied["plan"]["operations"][0]["type"] == "vscode.replace_selection@1"
    assert applied["plan"]["operations"][0]["args"]["replacement_text"] == fixed
    assert not native.calls_for("native.paste_text")
    assert ui.last_call("ui.overlay.state")["params"]["state"] == "idle"
    progress_calls = ui.calls_for("ui.action.progress")
    assert [call["params"]["stage"] for call in progress_calls] == [
        "reading",
        "planning",
        "validating",
        "preparing_preview",
        "awaiting_approval",
        "applying",
        "complete",
    ]
    assert all(call["params"]["action_id"] == "vscode.code_change" for call in progress_calls)


def test_vscode_untitled_tab_previews_then_writes_to_captured_editor_target() -> None:
    """An Untitled tab uses the hotkey-time editor target after approval."""
    active_app = {
        "name": "Untitled-1 - Visual Studio Code",
        "process_name": "Code.exe",
        "pid": 42,
        "window_id": 777,
    }
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "platform": "win32",
                "active_app": active_app,
                "selected_text": "",
                "clipboard_text": "",
                "app_selection_deferred": False,
                "focus_token": 9,
                "editor_point": {"x": 320.0, "y": 180.0},
            },
            "native.action.vscode.snapshot": lambda _params: pytest.fail("Untitled must not use saved-file IO"),
            "native.action.vscode.live_apply": lambda _params: {
                "ok": True,
                "method": "vscode-devtools",
                "confirmed": True,
                "text_verified": True,
            },
        }
    )
    ui = FakeWorker(
        {
            "ui.show_intent": lambda _params: {},
            "ui.action.preview.request": lambda _params: {"approved": True},
        }
    )
    brain = FakeWorker(
        stream_handlers={
            "brain.action.plan": action_plan_stream(
                {"replacement_text": 'def greet(name):\n    return f"Hello, {name}!"\n\nprint(greet("OpenWand"))'},
                "Created a small Python greeting example.",
            )
        }
    )

    with caller_config([{"paste_back": False, "context_clipboard": False}]):
        flow, native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "create a small Python example"})

    assert brain.last_call("brain.action.plan")["params"]["planning_tool_name"] == "vscode_plan_replace_selection"
    assert not native.calls_for("native.action.vscode.apply")
    preview = ui.last_call("ui.action.preview.request")["params"]
    assert preview["title"] == "Apply code to Untitled tab"
    assert "def greet" in preview["html"]
    paste = native.last_call("native.action.vscode.live_apply")["params"]
    assert paste["active_app"] == active_app
    assert paste["editor_point"] == {"x": 320.0, "y": 180.0}
    assert paste["confirmed"] is True
    assert paste["text"].startswith("def greet")
    assert [call["params"]["stage"] for call in ui.calls_for("ui.action.progress")] == [
        "reading",
        "planning",
        "validating",
        "preparing_preview",
        "awaiting_approval",
        "applying",
        "complete",
    ]
    assert ui.last_call("ui.overlay.state")["params"]["state"] == "idle"


def test_browser_form_action_uses_model_preview_and_verified_api_apply() -> None:
    """A Chrome form-fill request never falls back to keyboard paste or submission."""
    from core.actions.adapters.browser import BrowserField, BrowserFormSnapshot

    fields = (
        BrowserField("field_1", "#name", "Name", "text", "", "Full name", True),
        BrowserField("field_2", "#email", "Email", "email", "", "name@example.com", True),
    )
    snapshot = BrowserFormSnapshot(
        title="Contact form",
        url="https://example.test/contact",
        target_id="page-1",
        fields=fields,
        fingerprint=BrowserFormSnapshot.compute_fingerprint("https://example.test/contact", fields),
    )
    active_app = {
        "name": "Contact form - Google Chrome",
        "process_name": "chrome.exe",
        "pid": 42,
        "window_id": 777,
    }
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "platform": "win32",
                "active_app": active_app,
                "browser_url": snapshot.url,
                "selected_text": "",
                "clipboard_text": "",
                "app_selection_deferred": False,
            },
            "native.action.browser.form_snapshot": lambda _params: {
                "ok": True,
                "snapshot": snapshot.to_dict(),
            },
            "native.action.browser.form_apply": lambda _params: {
                "ok": True,
                "result": {
                    "status": "applied",
                    "message": "Filled and verified 2 fields without submitting the form.",
                },
            },
        }
    )
    ui = FakeWorker(
        {
            "ui.show_intent": lambda _params: {},
            "ui.action.preview.request": lambda _params: {"approved": True},
        }
    )
    brain = FakeWorker(
        stream_handlers={
            "brain.action.plan": action_plan_stream(
                {"assignments": [
                    {"field_id": "field_1", "value": "Sunny"},
                    {"field_id": "field_2", "value": "sunny@example.test"},
                ]},
                "Fill the two requested contact fields.",
            )
        }
    )

    with caller_config([{"paste_back": False, "context_clipboard": False}]):
        flow, native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        flow.begin_caller(0)
        provider = ui.last_call("ui.intent.action_provider")["params"]["action_provider"]
        suggestion = provider["suggested_intents"][0]
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "Fill this form with name Sunny and email sunny@example.test",
                "intent_routing": {
                    "mode": suggestion["mode"],
                    "source": "provider",
                    "suggestion_id": suggestion["id"],
                    "provider_id": provider["id"],
                    "app": provider["app"],
                    "capability_type": suggestion["capability_type"],
                    "planning_tool": suggestion["planning_tool"],
                },
            },
        )

    assert brain.calls_for("brain.action.plan"), {"native": native.calls, "ui": ui.calls}
    model = brain.last_call("brain.action.plan")["params"]
    assert model["planning_tool_name"] == "browser_plan_fill_form"
    assert any(field["field_id"] == "field_1" for field in model["app_context"]["fields"])
    preview = ui.last_call("ui.action.preview.request")["params"]
    assert "Sunny" in preview["html"]
    assert "Will not submit" not in preview["html"]
    applied = native.last_call("native.action.browser.form_apply")["params"]
    assert applied["confirmed"] is True
    assert len(applied["plan"]["operations"]) == 2
    assert not native.calls_for("native.paste_text")
    assert [call["params"]["stage"] for call in ui.calls_for("ui.action.progress")] == [
        "reading",
        "planning",
        "validating",
        "preparing_preview",
        "awaiting_approval",
        "applying",
        "complete",
    ]


def test_supported_app_custom_prompt_answers_without_action_disposition() -> None:
    """Freeform prompts stay separate from the supported app-action rows."""
    from core.actions.adapters.browser import BrowserField, BrowserFormSnapshot

    fields = (BrowserField("field_1", "#name", "Name", "text", "", "Full name", True),)
    snapshot = BrowserFormSnapshot(
        title="Contact form",
        url="https://example.test/contact",
        target_id="page-1",
        fields=fields,
        fingerprint=BrowserFormSnapshot.compute_fingerprint("https://example.test/contact", fields),
    )
    active_app = {
        "name": "Contact form - Google Chrome",
        "process_name": "chrome.exe",
        "pid": 42,
        "window_id": 777,
    }
    native = FakeWorker({
        "native.context.snapshot": lambda _params: {
            "platform": "win32",
            "active_app": active_app,
            "browser_url": snapshot.url,
            "selected_text": "Sunny",
            "clipboard_text": "",
        },
        "native.action.browser.form_snapshot": lambda _params: {"ok": True, "snapshot": snapshot.to_dict()},
        "native.action.browser.form_apply": lambda _params: {
            "ok": True,
            "result": {"status": "applied", "message": "Filled and verified one field."},
        },
    })
    ui = FakeWorker({"ui.show_intent": lambda _params: {}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("That looks like a contact-form name.")})
    with caller_config([{"paste_back": False, "context_clipboard": False}]):
        flow, native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        flow.begin_caller(0)
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "Use my selected name in this form",
                "intent_routing": {"mode": "answer", "source": "custom"},
            },
        )

    assert brain.last_call("brain.query")["params"]["intent_prompt"] == "Use my selected name in this form"
    assert not brain.calls_for("brain.action.plan")
    assert not native.calls_for("native.action.browser.form_snapshot")
    assert not native.calls_for("native.action.browser.form_apply")
    assert not native.calls_for("native.paste_text")


def test_legacy_custom_auto_routing_also_answers_without_action_disposition() -> None:
    """An older UI worker's custom/auto payload must not revive action guessing."""
    active_app = {
        "name": "Contact form - Google Chrome",
        "process_name": "chrome.exe",
        "pid": 42,
        "window_id": 777,
    }
    native = FakeWorker({
        "native.context.snapshot": lambda _params: {
            "platform": "win32",
            "active_app": active_app,
            "browser_url": "https://example.test/contact",
            "selected_text": "",
            "clipboard_text": "",
        },
    })
    ui = FakeWorker({"ui.show_intent": lambda _params: {}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("This page asks for contact information.")})
    with caller_config([{"paste_back": False, "context_clipboard": False}]):
        flow, native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        flow.begin_caller(0)
        ui.emit(
            "ui.intent.chosen",
            {"custom": "What is this page asking for?", "intent_routing": {"mode": "auto", "source": "custom"}},
        )

    assert brain.last_call("brain.query")["params"]["intent_prompt"] == "What is this page asking for?"
    assert not brain.calls_for("brain.action.plan")
    assert not native.calls_for("native.action.browser.form_snapshot")
    assert not native.calls_for("native.action.browser.form_apply")
    assert not native.calls_for("native.paste_text")
def test_caller_hotkey_captures_selected_file_before_intent_steals_focus(tmp_path):
    """Verify Explorer/Finder-selected files are captured before the picker focuses."""
    picked = tmp_path / "already-selected.md"
    picked.write_text("already selected file body", encoding="utf-8")
    rows = [
        {
            "paste_back": False,
            "context_ambient": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
            "file_access": "off",
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(
                selected="",
                clipboard="stale clipboard",
                selected_paths=[str(picked)],
            )
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("done")})
    with caller_config(rows):
        flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"prompt": "what is selected?", "context_choices": []})

    snapshot = native.calls_for("native.context.snapshot")[0]["params"]
    assert snapshot["include_selected_paths"] is True
    shown = ui.calls_for("ui.intent.context_items")[-1]["params"]
    chips = {item["id"]: item for item in shown["context_items"]}
    assert chips["selection"]["state"] == "on"
    assert "already-selected.md" in chips["selection"]["preview"]
    assert chips["selection"]["tokens"].startswith("~")
    params = brain.last_call("brain.query")["params"]
    assert params["file_access_mode"] == "off"
    assert "already selected file body" in params["ambient_text"]
    assert "stale clipboard" not in params["ambient_text"]


def test_intent_stale_selection_offers_off_by_default_chip():
    """Verify an already-served X11 selection arrives as an off, stale chip."""
    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_text": "",
            "stale_selected_text": "earlier words",
            "clipboard_text": "",
            "active_app": {"name": "Notes", "pid": 42},
        }

    native = FakeWorker({"native.context.snapshot": snapshot})
    with caller_config([{}]):
        flow, native, ui, _brain, _audio = make_flow(native=native)
        flow.begin_caller(0)

    shown = ui.calls_for("ui.intent.context_items")[-1]["params"]
    chips = {item["id"]: item for item in shown["context_items"]}
    assert chips["selection"]["state"] == "off"
    assert chips["selection"]["stale"] is True
    assert "earlier words" in chips["selection"]["preview"]
    assert chips["selection"]["tokens"].startswith("~")
    assert "not attached" in chips["selection"]["warning"]


def test_intent_stale_selection_attaches_when_chip_toggled_on():
    """Verify toggling the stale Selection chip on attaches the earlier text."""
    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_text": "",
            "stale_selected_text": "earlier words",
            "clipboard_text": "",
            "active_app": {"name": "Notes", "pid": 42},
        }

    native = FakeWorker({"native.context.snapshot": snapshot})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("done")})
    with caller_config([{}]):
        flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        ui.emit(
            "ui.intent.chosen",
            {
                "prompt": "use it",
                "context_choices": [{"id": "selection", "state": "on", "touched": True}],
            },
        )

    params = brain.last_call("brain.query")["params"]
    assert params["selected"] == "earlier words"


def test_intent_stale_selection_stays_detached_without_toggle():
    """Verify an untouched stale Selection chip sends no selection text."""
    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_text": "",
            "stale_selected_text": "earlier words",
            "clipboard_text": "",
            "active_app": {"name": "Notes", "pid": 42},
        }

    native = FakeWorker({"native.context.snapshot": snapshot})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("done")})
    with caller_config([{}]):
        flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        ui.emit(
            "ui.intent.chosen",
            {
                "prompt": "no selection",
                "context_choices": [{"id": "selection", "state": "off", "touched": False}],
            },
        )

    params = brain.last_call("brain.query")["params"]
    assert params["selected"] == ""
    assert "earlier words" not in str(params)


def test_linux_intent_selection_context_is_off_by_default():
    """Verify Linux offers the detected selection without attaching it by default."""
    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "platform": "linux",
            "selected_text": "last selected words",
            "clipboard_text": "",
            "active_app": {"name": "Editor", "pid": 42},
        }

    native = FakeWorker({"native.context.snapshot": snapshot})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("done")})
    with caller_config([{}]):
        flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        shown = ui.calls_for("ui.intent.context_items")[-1]["params"]
        chips = {item["id"]: item for item in shown["context_items"]}
        assert chips["selection"]["state"] == "off"
        assert chips["selection"]["capture_on_enable"] is False
        assert chips["selection"]["preview"] == "last selected words"
        assert chips["selection"]["tokens"].startswith("~")

        ui.emit("ui.intent.chosen", {"prompt": "do not use selection"})

    params = brain.last_call("brain.query")["params"]
    assert params["selected"] == ""
    assert "last selected words" not in str(params)


def test_linux_intent_selection_toggle_attaches_existing_selection():
    """Verify Linux Selection On uses the already detected text, not a new capture."""
    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "platform": "linux",
            "selected_text": "last selected words",
            "clipboard_text": "",
            "active_app": {"name": "Editor", "pid": 42},
        }

    native = FakeWorker({"native.context.snapshot": snapshot})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("done")})
    with caller_config([{}]):
        flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        ui.emit(
            "ui.intent.chosen",
            {
                "prompt": "use selection",
                "context_choices": [{"id": "selection", "state": "on", "touched": True}],
            },
        )

    params = brain.last_call("brain.query")["params"]
    assert params["selected"] == "last selected words"


def test_intent_context_source_removal_filters_items_and_disables_empty_group():
    """Verify per-row X removals drop sources and switch an emptied App chip off."""
    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Notes", "pid": 42},
            "active_document_text": "[Doc A]\nalpha body\n[Doc B]\nbeta body",
            "active_document_sources": [
                {"label": "Doc A", "preview": "alpha body"},
                {"label": "Doc B", "preview": "beta body"},
            ],
        }

    native = FakeWorker({"native.context.snapshot": snapshot})
    rows = [{"context_ambient": True, "context_documents": True}]
    with caller_config(rows):
        flow, native, ui, _brain, _audio = make_flow(native=native)
        flow.begin_caller(0)
        ui.emit("ui.intent.context.remove", {"id": "ambient", "source_id": "Doc A"})

        shown = ui.calls_for("ui.intent.context_items")[-1]["params"]
        chips = {item["id"]: item for item in shown["context_items"]}
        assert [s["label"] for s in chips["ambient"]["sources"]] == ["Doc B"]
        assert chips["ambient"]["state"] != "off"

        pending = flow._pending
        assert pending is not None
        assert "alpha body" not in str(pending.context.get("active_document_text"))
        assert "beta body" in str(pending.context.get("active_document_text"))

        ui.emit("ui.intent.context.remove", {"id": "ambient", "source_id": "Doc B"})
        shown = ui.calls_for("ui.intent.context_items")[-1]["params"]
        chips = {item["id"]: item for item in shown["context_items"]}
        assert chips["ambient"]["sources"] == []
        assert chips["ambient"]["state"] == "off"
        assert chips["ambient"]["tokens"].startswith("~")


def test_pasted_context_appears_in_intent_and_can_be_removed():
    """Intent paste attachments are visible, queued for send, and removable."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    with caller_config([{}]):
        flow, _native, ui, _brain, _audio = make_flow(native=native)
        flow.begin_caller(0)
        ui.emit(
            "ui.context.dropped",
            {
                "items": [
                    {"name": "pasted.png", "content": "aW1hZ2U=", "type": "image"},
                    {"name": "notes.txt", "content": "pasted notes", "type": "text"},
                ]
            },
        )

        shown = ui.last_call("ui.intent.context_items")["params"]
        chips = {item["id"]: item for item in shown["context_items"]}
        assert chips["attachments"]["state"] == "on"
        assert chips["attachments"]["locked"] is True
        assert [source["label"] for source in chips["attachments"]["sources"]] == [
            "pasted.png",
            "notes.txt",
        ]
        assert len(flow._drop_context_items) == 2

        ui.emit(
            "ui.intent.context.remove",
            {"id": "attachments", "source_id": "dropped:0"},
        )

        shown = ui.last_call("ui.intent.context_items")["params"]
        chips = {item["id"]: item for item in shown["context_items"]}
        assert [source["label"] for source in chips["attachments"]["sources"]] == [
            "notes.txt"
        ]
        assert [item["name"] for item in flow._drop_context_items] == ["notes.txt"]


def test_pasted_intent_image_reaches_query_as_vision_context():
    """A clipboard bitmap attached in the picker is sent through screenshot vision."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("described")})
    with caller_config([{}]):
        flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        ui.emit(
            "ui.context.dropped",
            {"items": [{"name": "Pasted image", "content": "aW1hZ2U=", "type": "image"}]},
        )
        ui.emit("ui.intent.chosen", {"custom": "Describe this image"})

    params = brain.last_call("brain.query")["params"]
    assert params["screenshot_b64"] == "aW1hZ2U="


def test_intent_app_context_reenable_restores_removed_document_sources():
    """Verify App recaptures all document rows after an emptied group is re-enabled."""
    active_doc = "[Doc A]\nalpha body\n\n[Doc B]\nbeta body\n\n[Doc C]\ngamma body"

    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": active_doc}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)

        shown = ui.last_call("ui.intent.context_items")["params"]
        chips = {item["id"]: item for item in shown["context_items"]}
        original_tokens = chips["ambient"]["tokens"]
        assert [source["label"] for source in chips["ambient"]["sources"]] == ["Doc A", "Doc B", "Doc C"]
        assert original_tokens.startswith("~")

        for label in ("Doc A", "Doc B", "Doc C"):
            ui.emit("ui.intent.context.remove", {"id": "ambient", "source_id": label})

        emptied = ui.last_call("ui.intent.context_items")["params"]
        emptied_chips = {item["id"]: item for item in emptied["context_items"]}
        assert emptied_chips["ambient"]["state"] == "off"
        assert emptied_chips["ambient"]["sources"] == []
        assert emptied_chips["ambient"]["tokens"].startswith("~")

        ui.emit(
            "ui.intent.context.reenabled",
            {
                "id": "ambient",
                "context_choices": [
                    {"id": "ambient", "state": "on", "default_state": "off", "touched": True}
                ],
            },
        )

        restored = ui.last_call("ui.intent.context_items")["params"]
        restored_chips = {item["id"]: item for item in restored["context_items"]}
        assert restored_chips["ambient"]["state"] == "on"
        assert restored_chips["ambient"]["tokens"] == original_tokens
        assert [source["label"] for source in restored_chips["ambient"]["sources"]] == [
            "Doc A",
            "Doc B",
            "Doc C",
        ]

        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "Use all docs",
                "context_choices": [
                    {"id": "ambient", "state": "on", "default_state": "off", "touched": True}
                ],
            },
        )

    query = brain.last_call("brain.query")["params"]
    assert "[Doc A]\nalpha body" in query["active_document_text"]
    assert "[Doc B]\nbeta body" in query["active_document_text"]
    assert "[Doc C]\ngamma body" in query["active_document_text"]


def test_strip_removed_document_sources_drops_only_matching_blocks():
    """Verify removed labels drop their blocks and everything else survives."""
    text = "[Doc A]\nalpha body\n[Doc B]\nbeta body"
    assert FlowController._strip_removed_document_sources(text, {"Doc A"}) == "[Doc B]\nbeta body"
    assert FlowController._strip_removed_document_sources(text, set()) == text
    assert FlowController._strip_removed_document_sources("plain text", {"Doc A"}) == "plain text"
    assert (
        FlowController._strip_removed_document_sources(text, {"Doc A", "Doc B"}) == ""
    )


def test_intent_selection_chip_can_start_capture_without_selected_text():
    """Verify empty selection can still be toggled to start capture."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native)
        _flow.begin_caller(0)

    chips = {
        item["id"]: item
        for item in ui.last_call("ui.show_intent")["params"]["context_items"]
    }
    assert chips["selection"]["available"] is True
    assert chips["selection"]["state"] == "off"
    assert chips["selection"]["tokens"] == ""


def test_intent_context_estimates_known_text_and_marks_unknown_deferred_sources():
    """Verify known context text is estimated while truly unknown sources stay deferred."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_memory_mode": "on",
            "context_screenshot": "model",
            "context_clipboard": True,
            "file_access": "ask",
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="selected", clipboard="clip text")})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native)
        _flow.begin_caller(0)

    chips = {
        item["id"]: item
        for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
    }

    assert chips["ambient"]["tokens"].startswith("~")
    assert "not known yet" in chips["ambient"]["warning"]
    assert chips["selection"]["tokens"] != "? tok"
    assert chips["clipboard"]["tokens"] != "? tok"
    assert chips["screenshot"]["tokens"] == "? tok"
    assert chips["memory"]["tokens"] == "? tok"
    assert chips["files"]["tokens"] == ""
    assert chips["files"]["warning"] == ""


def test_intent_screenshot_cost_is_unknown_until_snip_exists():
    """A region snip must not inherit the dimensions of the whole monitor."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": False,
            "context_documents_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
            "file_access": "off",
        }
    ]

    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        result = context_handler(selected="")(_params)
        result["screen_size"] = {"width": 1920, "height": 1080}
        return result

    native = FakeWorker({"native.context.snapshot": snapshot})
    with caller_config(rows):
        _flow, native, ui, _brain, _audio = make_flow(native=native)
        _flow.begin_caller(0)

    screenshot_chip = next(
        item
        for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
        if item["id"] == "screenshot"
    )
    assert screenshot_chip["state"] == "off"
    assert screenshot_chip["tokens"] == "? tok"
    assert screenshot_chip["token_count"] is None
    assert screenshot_chip["warning"] == ""
    assert not native.calls_for("native.capture.fullscreen")


def test_image_context_estimate_uses_actual_cropped_dimensions():
    """A captured region is priced from its image header, not monitor metadata."""
    png_header = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + (300).to_bytes(4, "big")
        + (200).to_bytes(4, "big")
    )
    image_b64 = base64.b64encode(png_header).decode("ascii")

    assert FlowController._image_token_count(image_b64) == 255
    assert FlowController._image_token_label(image_b64) == "~255 tok"


def test_intent_off_context_sources_keep_available_estimates():
    """Verify off context chips still show estimates when metadata is available."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
            "file_access": "off",
        }
    ]

    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        result = context_handler(selected="", clipboard="clipboard text")(_params)
        result["browser_url"] = "https://example.test/page"
        result["browser_content"] = "Browser page estimate text."
        result["screen_size"] = {"width": 1920, "height": 1080}
        return result

    native = FakeWorker({"native.context.snapshot": snapshot})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native)
        _flow.begin_caller(0)

    chips = {
        item["id"]: item
        for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
    }
    assert chips["ambient"]["state"] == "off"
    assert chips["ambient"]["tokens"].startswith("~")
    assert chips["browser"]["state"] == "off"
    assert chips["browser"]["tokens"].startswith("~")
    assert chips["clipboard"]["state"] == "off"
    assert chips["clipboard"]["tokens"].startswith("~")
    assert chips["screenshot"]["state"] == "off"
    assert chips["screenshot"]["tokens"] == "? tok"
    assert chips["github"]["state"] == "off"
    assert chips["github"]["tokens"] == "0 tok"
    assert chips["memory"]["state"] == "off"
    assert chips["memory"]["tokens"] == "0 tok"
    assert chips["files"]["state"] == "off"
    assert chips["files"]["tokens"] == ""


def test_begin_caller_reloads_supervisor_config_when_env_changed(monkeypatch):
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
            "intents": [{"key": "w", "label": "Ask", "prompt": "Ask"}],
        }
    ]
    updated_rows = [{**rows[0], "context_tools": True, "context_screenshot": "model"}]
    mtimes = iter([1.0, 2.0])
    reload_calls: list[str] = []
    monkeypatch.setattr(FlowController, "_current_config_mtime", staticmethod(lambda: next(mtimes)))

    def reload_config() -> None:
        reload_calls.append("reload")
        config.CALLER_ROWS[:] = updated_rows

    monkeypatch.setattr(config, "reload", reload_config)

    with caller_config(rows):
        flow, _native, ui, _brain, _audio = make_flow(
            native=FakeWorker({"native.context.snapshot": context_handler(selected="")})
        )
        flow.begin_caller(0)

    assert reload_calls == ["reload"]
    assert ui.last_call("ui.show_intent")["params"]["caller_idx"] == 0
    assert flow._pending is not None
    assert flow._pending.caller["context_tools"] is True
    assert flow._pending.caller["context_screenshot"] == "model"


def test_bubble_speed_event_forwards_to_audio_worker():
    _flow, _native, ui, _brain, audio = make_flow()

    ui.emit("ui.bubble.speed", {"enabled": True})

    assert audio.last_call("audio.speed_boost")["params"] == {"enabled": True}


def test_bubble_stop_event_hides_bubble_and_cancels_current_tts_queue():
    """Verify bubble stop mutes visible output for the current reply."""
    flow, _native, ui, _brain, audio = make_flow()
    generation = flow._new_generation()
    q: queue.Queue[str | None] = queue.Queue()
    with flow._tts_lock:
        flow._tts_generation = generation
        flow._tts_queue = q
        flow._tts_sequence_active = True

    ui.emit("ui.bubble.stop", {})

    assert flow._reply_bubble_cancelled(generation)
    assert audio.last_call("audio.stop")
    assert ui.last_call("ui.reply.reset")
    assert ui.last_call("ui.overlay.state")["params"] == {"state": "idle"}
    assert q.get_nowait() is None
    flow._queue_tts_segment(generation, "do not speak")
    assert not audio.calls_for("audio.tts.synthesize")


def test_bubble_stop_event_cancels_the_active_model_request_and_prevents_late_output():
    """The real UI stop event must cancel the request, not merely hide its output."""
    stream_running = threading.Event()
    cancel_received = threading.Event()
    cancel_targets: list[int] = []

    def query_stream(_params, on_event):
        on_event("reply.chunk", {"text": "partial "}, 1)
        stream_running.set()
        assert cancel_received.wait(5), "bubble Stop never reached brain.cancel"
        on_event("reply.done", {"text": "partial "}, 1)
        return {"text": "partial "}

    def cancel_stream(params):
        cancel_targets.append(int(params["target"]))
        cancel_received.set()
        return {"cancelled": True}

    brain = FakeWorker(
        handlers={"brain.cancel": cancel_stream},
        stream_handlers={"brain.query": query_stream},
    )
    flow, _native, ui, brain, audio = make_flow(brain=brain)
    pending = PendingInvocation(
        caller_idx=0,
        caller={"paste_back": False, "context_memory_mode": "off"},
        context={},
    )
    pending.context_ready.set()
    with flow._lock:
        flow._pending = pending

    query_thread = threading.Thread(
        target=lambda: ui.emit("ui.intent.chosen", {"prompt": "answer slowly"}),
        name="test-reply-query",
    )
    query_thread.start()
    assert stream_running.wait(5), "query stream did not start"

    ui.emit("ui.bubble.stop", {})
    query_thread.join(timeout=5)

    assert not query_thread.is_alive(), "cancelled query did not return"
    assert len(cancel_targets) == 1 and cancel_targets[0] > 0
    assert brain.last_call("brain.cancel")["params"] == {"target": cancel_targets[0]}
    assert flow._active_reply_stream_id is None
    assert ui.last_call("ui.reply.reset")
    assert ui.last_call("ui.overlay.state")["params"] == {"state": "idle"}
    assert audio.last_call("audio.stop")
    assert not ui.calls_for("ui.reply.replace"), "cancelled answer resurfaced after Stop"


def test_rapid_second_intent_cancels_first_and_never_mixes_late_chunks_or_parsers():
    """A newer user input owns the UI even when the older stream returns late data."""
    first_started = threading.Event()
    release_first = threading.Event()
    cancel_targets: list[int] = []

    def query_stream(params, on_event):
        prompt = str(params.get("intent_prompt") or "")
        if prompt == "first request":
            on_event("reply.chunk", {"text": "<think>old private thought"}, 1)
            first_started.set()
            assert release_first.wait(5), "new input never cancelled the old request"
            # Deliberately misbehave like a slow provider and send data after cancel.
            on_event("reply.chunk", {"text": "</think> OLD LATE ANSWER"}, 1)
            on_event("reply.done", {"text": "OLD FINAL"}, 1)
            return {"text": "OLD FINAL"}
        assert prompt == "second request"
        on_event("reply.chunk", {"text": "NEW ANSWER"}, 2)
        on_event("reply.done", {"text": "NEW ANSWER"}, 2)
        return {"text": "NEW ANSWER"}

    def cancel_stream(params):
        cancel_targets.append(int(params["target"]))
        release_first.set()
        return {"cancelled": True}

    brain = FakeWorker(
        handlers={"brain.cancel": cancel_stream},
        stream_handlers={"brain.query": query_stream},
    )
    flow, _native, ui, brain, _audio = make_flow(brain=brain)

    def choose(prompt: str) -> None:
        pending = PendingInvocation(
            caller_idx=0,
            caller={"paste_back": False, "context_memory_mode": "off"},
            context={},
        )
        pending.context_ready.set()
        with flow._lock:
            flow._pending = pending
        ui.emit("ui.intent.chosen", {"prompt": prompt})

    first_thread = threading.Thread(target=choose, args=("first request",), name="first-intent")
    first_thread.start()
    assert first_started.wait(5), "first request did not start"
    ui_cutoff = len(ui.calls)

    choose("second request")
    first_thread.join(timeout=5)

    assert not first_thread.is_alive(), "superseded request did not finish"
    assert len(cancel_targets) == 1
    late_ui_calls = ui.calls[ui_cutoff:]
    late_reply_chunks = [
        call["params"]
        for call in late_ui_calls
        if call["method"] == "ui.reply.chunk"
    ]
    assert late_reply_chunks == [
        {"text": "NEW ANSWER", "is_thought": False, "is_progress": False}
    ]
    assert all("OLD" not in str(call["params"]) for call in late_ui_calls)
    conversations = ui.calls_for("ui.chat.add_conversation")
    assert [call["params"]["assistant"] for call in conversations] == ["NEW ANSWER"]
    assert flow._last_reply == "NEW ANSWER"
    assert flow._active_reply_stream_id is None


def test_query_flow_streams_reply_and_adds_chat_conversation_with_context():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_tools": True,
            "context_browser_mode": "model",
            "context_github_mode": "model",
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
    assert "context_policy" not in query
    assert query["intent_prompt"] == "Explain this"
    assert query["selected"] == "selected"
    assert query["use_tools"] is True
    assert "web_search" in query["allowed_tools"]
    assert "github_repo" in query["allowed_tools"]
    assert "[App]\nActive app: Notes" in query["ambient_text"]
    assert "[Clipboard]" in query["ambient_text"]
    assert "[Buffered context]" in query["ambient_text"]
    assert "[Dropped context]" in query["ambient_text"]
    chunks = [c["params"] for c in ui.calls_for("ui.reply.chunk")]
    assert not any(c.get("is_progress") for c in chunks)
    assert [c["text"] for c in chunks] == ["hello"]
    assert ui.calls_for("ui.reply.done")
    chat_params = ui.last_call("ui.chat.add_conversation")["params"]
    assert chat_params["assistant"] == "hello"
    assert chat_params["context_policy"]["context_clipboard"] is True
    assert chat_params["context_policy"]["context_documents_mode"] == "auto"
    assert ui.calls_for("ui.context.summary")
    summary_labels = [item["label"] for item in ui.last_call("ui.context.summary")["params"]["items"]]
    assert "Selection" in summary_labels
    assert "Clipboard" in summary_labels
    assert "App" in summary_labels
    assert not any(label.startswith(("Selection -", "Clipboard -")) for label in summary_labels)
    assert ui.calls_for("ui.context.clear")


def test_query_arms_chat_only_reply_presentation_when_enabled(monkeypatch):
    rows = [{"paste_back": False, "context_ambient": False}]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("hello")})
    with caller_config(rows):
        flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        monkeypatch.setattr(config, "CHAT_OPEN_ON_PROMPT", True, raising=False)
        monkeypatch.setattr(config, "CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE", True, raising=False)
        flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Explain this"})

    assert ui.last_call("ui.reply.presentation")["params"] == {"suppress_bubble": True}
    assert ui.last_call("ui.show_chat")["params"] == {"new": False}


def test_query_flow_persists_image_only_assistant_result():
    """An overlay image generation creates a chat turn even without reply text."""
    rows = [{"paste_back": False, "context_ambient": True, "context_screenshot": "off"}]
    attachment = {
        "kind": "image",
        "source": "codex_image_generation",
        "path": "/repo/generated.png",
        "name": "generated.png",
    }

    def image_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        on_event(
            "reply.chunk",
            {"text": "Image generated.", "is_progress": True, "is_thought": False},
            1,
        )
        payload = {"text": "", "attachments": [attachment]}
        on_event("reply.done", payload, 1)
        return payload

    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": image_stream})
    with caller_config(rows):
        flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Generate a test image"})

    chat_params = ui.last_call("ui.chat.add_conversation")["params"]
    assert chat_params["assistant"] == ""
    assert chat_params["assistant_attachments"] == [attachment]
    assert ui.last_call("ui.reply.chunk")["params"] == {
        "text": "Image generated.",
        "is_progress": True,
        "is_thought": False,
    }
    assert ui.last_call("ui.reply.image")["params"] == {"attachments": [attachment]}


def test_query_flow_streams_reply_into_open_chat_conversation():
    """Verify overlay query chunks are mirrored into the open chat window."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_memory_mode": "off",
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    ui = FakeWorker({"ui.chat.begin_conversation": lambda _params: {"started": True, "conversation_index": 2}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("hello")})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Explain this"})

    chat_chunks = [call["params"] for call in ui.calls_for("ui.chat.chunk")]
    assert chat_chunks == [
        {"conversation_index": 2, "text": "hello", "is_progress": False, "is_thought": False},
    ]
    assert ui.calls_for("ui.chat.done")[-1]["params"]["conversation_index"] == 2
    assert ui.calls_for("ui.chat.done")[-1]["params"]["text"] == "hello"
    assert ui.last_call("ui.chat.add_conversation")["params"]["conversation_index"] == 2
    assert ui.last_call("ui.chat.add_conversation")["params"]["assistant"] == "hello"


def test_query_bubble_splits_exposed_thought_segments():
    """Verify speech bubble receives exposed model thought segments separately."""
    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        reply = "<thought>checking files</thought>Created it."
        on_event("reply.chunk", {"text": reply[:13]}, 1)
        on_event("reply.chunk", {"text": reply[13:]}, 1)
        on_event("reply.done", {"text": reply}, 1)
        return {"text": reply}

    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": stream})
    with caller_config([{"paste_back": False, "context_ambient": True}]):
        flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "create a file"})

    chunks = [call["params"] for call in ui.calls_for("ui.reply.chunk")]
    assert "".join(chunk["text"] for chunk in chunks if chunk.get("is_thought")) == "checking files"
    assert "".join(chunk["text"] for chunk in chunks if not chunk.get("is_thought") and not chunk.get("is_progress")) == "Created it."


def test_query_bubble_forwards_reply_text_annotations():
    """Verify final reply-surface annotations reach the floating bubble chunk."""
    annotation = {"start": 0, "end": 6, "tag": "mark", "style": "background-color:#4da3ff", "id": "reply-mark"}

    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        on_event("reply.chunk", {"text": "bubble text", "annotations": [annotation]}, 1)
        on_event("reply.done", {"text": "bubble text"}, 1)
        return {"text": "bubble text"}

    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": stream})
    with caller_config([{"paste_back": False, "context_ambient": True}]):
        flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "answer"})

    chunks = [call["params"] for call in ui.calls_for("ui.reply.chunk")]
    assert chunks[-1]["text"] == "bubble text"
    assert chunks[-1]["annotations"] == [annotation]


def test_add_context_shows_panel_badge_not_bubble():
    native = FakeWorker({"native.context.snapshot": context_handler(selected="hello world selection")})
    with caller_config([{}]):
        flow, native, ui, brain, _audio = make_flow(native=native)
        flow.add_context()
    add_calls = ui.calls_for("ui.context.add_item")
    assert add_calls, "added context should surface as a right-of-icon badge"
    assert add_calls[-1]["params"]["name"] == "Selection"
    assert add_calls[-1]["params"]["item_type"] == "text"
    assert not ui.calls_for("ui.reply.notice"), "added context must not go to the bubble"
    assert len(flow._drop_context_items) == 1
    assert flow._drop_context_items[0]["content"] == "hello world selection"


def test_add_context_without_text_falls_back_to_notice():
    native = FakeWorker({"native.context.snapshot": context_handler(selected="", clipboard="")})
    with caller_config([{}]):
        flow, native, ui, brain, _audio = make_flow(native=native)
        flow.add_context()
    assert not ui.calls_for("ui.context.add_item")
    assert ui.last_call("ui.reply.notice")["params"]["text"].startswith("No selected")


def test_read_selection_aloud_speaks_selected_text_without_model(monkeypatch):
    """Verify read-selection-aloud uses selected text and local TTS only."""
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    native = FakeWorker({"native.context.snapshot": context_handler(selected="read this out loud")})
    audio = FakeWorker(
        {
            "audio.tts.synthesize": lambda params: {
                "path": "selection.wav",
                "provider": "fake",
                "word_timestamps": {
                    "words": str(params.get("text") or "").split(),
                    "start_ms": [0, 100, 200, 300],
                    "estimated": False,
                },
            },
            "audio.play_file": lambda params: {"played": True, "path": params.get("path")},
            "audio.stop": lambda _params: {"stopped": True},
        }
    )
    flow, native, ui, brain, audio = make_flow(native=native, audio=audio)

    flow.read_selection_aloud()

    assert not brain.calls_for("brain.query")
    snapshot = native.last_call("native.context.snapshot")["params"]
    assert snapshot["include_selection"] is True
    assert snapshot["include_clipboard"] is False
    assert audio.last_call("audio.tts.synthesize")["params"]["text"] == "read this out loud"
    assert audio.last_call("audio.play_file")["params"]["path"] == "selection.wav"
    assert ui.last_call("ui.reply.reading")["params"]["text"] == "read this out loud"
    assert any(
        call["params"].get("state") == "speaking"
        for call in ui.calls_for("ui.overlay.state")
    )
    assert ui.calls_for("ui.reply.done")


def test_read_selection_aloud_single_word_failure_finishes_reading_bubble(monkeypatch):
    """A failed short read-aloud should not leave the static Reading bubble stuck."""
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    native = FakeWorker({"native.context.snapshot": context_handler(selected="word")})
    audio = FakeWorker(
        {
            "audio.tts.synthesize": lambda _params: {"provider": "fake"},
            "audio.stop": lambda _params: {"stopped": True},
        }
    )
    flow, _native, ui, brain, audio = make_flow(native=native, audio=audio)

    flow.read_selection_aloud()

    assert not brain.calls_for("brain.query")
    assert audio.last_call("audio.tts.synthesize")["params"]["text"] == "word"
    assert not audio.calls_for("audio.play_file")
    assert not ui.calls_for("ui.reply.reading")
    assert ui.calls_for("ui.reply.labeled_text")[-1]["params"]["label"] == "Preparing speech"
    assert ui.calls_for("ui.reply.done")
    notice = ui.last_call("ui.reply.notice")["params"]["text"]
    assert notice.startswith("Could not read selected text aloud.")


def test_closing_read_aloud_bubble_stops_tts_without_failure_notice(monkeypatch):
    """Closing the reading bubble is an intentional stop, not a TTS failure."""
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    native = FakeWorker({"native.context.snapshot": context_handler(selected="stop reading this")})
    holder: dict[str, FlowController] = {}

    def stop_during_playback(_params):
        holder["flow"].stop_reply_bubble()
        return {"played": False, "stopped": True}

    audio = FakeWorker(
        {
            "audio.tts.synthesize": lambda _params: {"path": "selection.wav", "provider": "fake"},
            "audio.play_file": stop_during_playback,
            "audio.stop": lambda _params: {"stopped": True},
        }
    )
    flow, _native, ui, _brain, _audio = make_flow(native=native, audio=audio)
    holder["flow"] = flow

    flow.read_selection_aloud()

    assert audio.calls_for("audio.stop")
    assert ui.calls_for("ui.reply.reset")
    assert not ui.calls_for("ui.reply.notice")


def test_read_selection_aloud_synthesizes_next_chunk_while_first_plays(monkeypatch):
    """Verify long read-aloud selections synthesize one chunk ahead."""
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    first_chunk = " ".join(f"alpha{i}" for i in range(49)) + " alpha49."
    second_chunk = " ".join(f"beta{i}" for i in range(59)) + " beta59."
    native = FakeWorker({"native.context.snapshot": context_handler(selected=f"{first_chunk} {second_chunk}")})
    second_synth_started = threading.Event()
    lock = threading.Lock()
    synth_count = {"value": 0}
    synth_texts: list[str] = []

    def synth_handler(params):
        with lock:
            synth_count["value"] += 1
            count = synth_count["value"]
            synth_texts.append(str(params.get("text") or ""))
        if count == 2:
            second_synth_started.set()
        return {"path": f"chunk-{count}.wav", "provider": "fake"}

    def play_handler(params):
        path = str(params.get("path") or "")
        if path == "chunk-1.wav":
            assert second_synth_started.wait(1.0), "second chunk should synthesize while first plays"
        return {"played": True, "path": path}

    audio = FakeWorker(
        {
            "audio.tts.synthesize": synth_handler,
            "audio.play_file": play_handler,
            "audio.stop": lambda _params: {"stopped": True},
        }
    )
    flow, _native, _ui, brain, audio = make_flow(native=native, audio=audio)

    flow.read_selection_aloud()

    assert not brain.calls_for("brain.query")
    assert synth_texts == [first_chunk, second_chunk]
    assert [call["params"]["path"] for call in audio.calls_for("audio.play_file")] == [
        "chunk-1.wav",
        "chunk-2.wav",
    ]


def test_read_selection_aloud_without_selection_shows_notice(monkeypatch):
    """Verify read-selection-aloud tells the user when nothing is selected."""
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    native = FakeWorker({"native.context.snapshot": context_handler(selected="", clipboard="clipboard")})
    flow, _native, ui, brain, audio = make_flow(native=native)

    flow.read_selection_aloud()

    assert not brain.calls_for("brain.query")
    assert not audio.calls_for("audio.tts.synthesize")
    assert ui.last_call("ui.reply.notice")["params"]["text"] == (
        "No selected text to read aloud.\n\n"
        "Recommendation: select the source text or enable a context source, then retry."
    )


def test_read_selection_aloud_selection_failure_matrix_is_controlled(monkeypatch):
    """Accessibility selection faults stop before synthesis and show an error."""
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    faults = (
        RuntimeError("focus moved"),
        RuntimeError("target control does not expose accessible text"),
        PermissionError("OS permission is missing"),
        RuntimeError("target application is unsupported"),
        NotImplementedError("platform backend is unsupported"),
    )
    for fault in faults:
        native = FakeWorker(
            {
                "native.context.snapshot": lambda _params, fault=fault: (
                    _ for _ in ()
                ).throw(fault)
            }
        )
        flow, _native, ui, brain, audio = make_flow(native=native)
        flow.read_selection_aloud()
        assert not brain.calls_for("brain.query")
        assert not audio.calls_for("audio.tts.synthesize")
        notice = ui.last_call("ui.reply.notice")["params"]
        assert notice["severity"] == "error"
        assert "Could not read selected text" in notice["text"]


def test_read_selection_aloud_native_hotkey_routes_to_tts(monkeypatch):
    """Verify the configurable native hotkey invokes read-selection-aloud."""
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    native = FakeWorker({"native.context.snapshot": context_handler(selected="read by hotkey")})
    audio = FakeWorker(
        {
            "audio.tts.synthesize": lambda _params: {"path": "selection.wav", "provider": "fake"},
            "audio.play_file": lambda _params: {"played": True},
            "audio.stop": lambda _params: {"stopped": True},
        }
    )
    _flow, native, _ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "read_selection_aloud"})

    assert audio.last_call("audio.tts.synthesize")["params"]["text"] == "read by hotkey"


def test_clear_context_empties_panel_without_bubble():
    snapshots = iter(
        (
            context_handler(selected="some text")({}),
            context_handler(selected="")({}),
        )
    )
    native = FakeWorker({"native.context.snapshot": lambda _params: next(snapshots)})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("after clear")})
    with caller_config([{}]):
        flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.add_context()
        flow.clear_context()
        flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "verify clear"})
    assert ui.calls_for("ui.context.clear"), "clear should empty the panel"
    assert not ui.calls_for("ui.reply.notice"), "clear must not go to the bubble"
    assert flow._drop_context_items == []
    query = brain.last_call("brain.query")["params"]
    assert query["selected"] == ""
    assert "some text" not in query["ambient_text"]


def test_clear_context_failure_matrix_always_clears_local_state():
    """Missing state and UI/storage-style cleanup faults cannot retain context."""
    failures = (
        FileNotFoundError("target required by this function is missing"),
        PermissionError("target required by this function is locked"),
        PermissionError("required elevation is denied"),
        PermissionError("storage access is denied"),
        OSError("another process is using the files"),
        OSError("cleanup only partly completes"),
    )

    # The clear action itself is the user's confirmation.  It must also be safe
    # when there is nothing to remove.
    empty_flow, _native, _ui, _brain, _audio = make_flow()
    empty_flow.clear_context()
    assert empty_flow._context_buffer == []
    assert empty_flow._drop_context_items == []

    for failure in failures:
        def fail_clear(_params, error=failure):
            raise error

        ui = FakeWorker({"ui.context.clear": fail_clear})
        flow, _native, _ui, _brain, _audio = make_flow(ui=ui)
        flow._context_buffer.extend([{"type": "text", "content": "selected"}])
        flow._drop_context_items.extend([{"type": "text", "content": "dropped"}])
        flow._pending_context_capture = {"caller": 0}

        flow.clear_context()

        assert flow._context_buffer == []
        assert flow._drop_context_items == []
        assert flow._pending_context_capture is None


def test_context_modes_map_to_auto_documents_and_allowed_tools():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "model",
            "context_browser_mode": "off",
            "context_github_mode": "model",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler()})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        ui.emit("ui.intent.chosen", {"custom": "Use context"})

    query = brain.last_call("brain.query")["params"]
    assert query["include_active_document"] is False
    assert query["use_tools"] is True
    # memory defaults to "off", so memory_save is not offered.
    assert query["allowed_tools"] == ["get_context.documents", "git_status", "git_diff", "github_repo", "github_issue"]
    assert query["pinned_tools"] == ["get_context", "git_status", "git_diff", "github_repo", "github_issue"]
    assert query["frontload_tools"] == []


def test_context_tool_off_overrides_suppress_context_mode_grants():
    """Verify explicit off overrides suppress named context-mode tools."""
    caller = {
        "context_documents_mode": "model",
        "context_browser_mode": "model",
        "context_github_mode": "model",
        "context_memory_mode": "model",
        "context_screenshot": "model",
        "tools": {
            "get_context": "off",
            "web_search": "off",
            "git_status": "off",
            "memory_search": "off",
            "capture_screen": "off",
            "my_tool": "on",
        },
    }

    assert tool_modes.tool_overrides(caller) == {
        "get_context": "off",
        "web_search": "off",
        "git_status": "off",
        "memory_search": "off",
        "capture_screen": "off",
        "my_tool": "on",
    }
    allowed = tool_modes.allowed_model_tools(caller)
    assert "get_context.documents" not in allowed
    assert "get_context.browser" not in allowed
    assert "web_search" not in allowed
    assert "git_status" not in allowed
    assert "git_diff" in allowed
    assert "memory_search" not in allowed
    assert "memory_save" in allowed
    assert tool_modes.screenshot_tool_allowed(caller) is False


def test_context_policy_failure_contract_fails_closed_at_runtime_boundary():
    """Exercise every shared context-policy cause through runtime-owned boundaries."""
    from core.query_pipeline import (
        MAX_CAPTURED_CONTEXT_CHARS,
        ContextInputs,
        build_context,
    )

    disabled = {
        "context_ambient": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "context_clipboard": False,
        "file_access": "off",
        "tools": {},
    }
    flow, native, _ui, _brain, _audio = make_flow()
    assert flow._allowed_model_tools(disabled) == []
    assert flow._screenshot_tool_allowed(disabled) is False
    assert not native.calls_for("native.context.snapshot")

    for failure in (
        RuntimeError("context source unavailable"),
        PermissionError("context capture permission missing"),
    ):
        def failed_snapshot(_params, failure=failure):
            raise failure

        native = FakeWorker({"native.context.snapshot": failed_snapshot})
        with caller_config([disabled]):
            flow, _native, ui, _brain, _audio = make_flow(native=native)
            flow.begin_caller(0)
        assert flow._pending is not None
        assert not any(flow._pending.context.values())
        assert ui.calls_for("ui.show_intent")

    native = FakeWorker({"native.context.snapshot": lambda _params: {}})
    with caller_config([disabled]):
        flow, _native, ui, _brain, _audio = make_flow(native=native)
        flow.begin_caller(0)
    assert flow._pending is not None
    assert not any(flow._pending.context.values())
    assert ui.calls_for("ui.show_intent")

    stale_snapshot = {
        "selected_text": "",
        "stale_selected_text": "selection from an earlier capture",
        "clipboard_text": "",
        "active_app": {"name": "Editor", "pid": 42},
        "platform": "linux",
    }
    native = FakeWorker({"native.context.snapshot": lambda _params: dict(stale_snapshot)})
    with caller_config([disabled]):
        flow, _native, ui, _brain, _audio = make_flow(native=native)
        flow.begin_caller(0)
    selection = next(
        item
        for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
        if item["id"] == "selection"
    )
    assert selection["state"] == "off"
    assert selection["stale"] is True

    invalid_policy = {
        **disabled,
        "context_documents_mode": "corrupt",
        "context_browser_mode": {"not": "a mode"},
        "context_github_mode": 17,
        "tools": {"web_search": "unknown", "": "on"},
    }
    assert tool_modes.context_mode(invalid_policy, "documents") == "off"
    assert tool_modes.context_mode(invalid_policy, "browser") == "off"
    assert tool_modes.context_mode(invalid_policy, "github") == "off"
    assert tool_modes.tool_overrides(invalid_policy) == {}
    assert tool_modes.allowed_model_tools(invalid_policy) == []

    built = build_context(
        ContextInputs(
            intent_prompt="summarize",
            ambient_text="a" * MAX_CAPTURED_CONTEXT_CHARS,
            clipboard_text="b" * MAX_CAPTURED_CONTEXT_CHARS,
            selected="c" * MAX_CAPTURED_CONTEXT_CHARS,
            trust_privacy_mode=False,
        )
    )
    assert len(built.ambient_ctx) <= MAX_CAPTURED_CONTEXT_CHARS
    assert built.ambient_ctx.endswith("[captured context truncated at safety limit]")


def test_document_model_mode_preview_does_not_inject_active_document():
    """Verify model-mode document preview does not frontload document text."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "model",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "DOC PREVIEW"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Use document only if needed"})

    query = brain.last_call("brain.query")["params"]
    assert brain.calls_for("brain.context.active_document")
    assert query["active_document_text"] == ""
    assert query["include_active_document"] is False
    assert query["allowed_tools"] == ["get_context.documents"]
    summary_labels = [item["label"] for item in ui.last_call("ui.context.summary")["params"]["items"]]
    assert "App" in summary_labels
    assert "Active document" not in summary_labels


def test_context_modes_map_on_browser_and_git_to_frontloaded_context():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "auto",
            "context_github_mode": "auto",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": browser_context_handler()})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, _native, _ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        _ui.emit("ui.intent.chosen", {"custom": "Use context"})

    query = brain.last_call("brain.query")["params"]
    # memory defaults to "off" and browser/git context is frontloaded rather than
    # offered as tools, so no model tools are offered here.
    assert query["use_tools"] is False
    assert query["allowed_tools"] == []
    assert query["frontload_tools"] == ["git_status", "git_diff"]
    assert "[Browser/Web]" in query["ambient_text"]
    assert "https://example.test/page" in query["ambient_text"]
    assert "Example page text" in query["ambient_text"]


def test_query_with_tools_uses_longer_brain_timeout():
    """Tool-enabled overlay queries get extra time before supervisor timeout."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "on",
            "context_screenshot": "off",
            "context_clipboard": False,
            "file_access": "ask",
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "hello"})

    query = brain.last_call("brain.query")["params"]
    assert query["use_tools"] is True
    assert query["file_access_mode"] == "ask"
    assert brain.last_call("brain.query")["timeout"] == 300.0
    assert not any(call["params"].get("is_progress") for call in ui.calls_for("ui.reply.chunk"))
    assert set(query["allowed_tools"]) >= {"list_files", "read_file", "create_file", "edit_file", "write_file"}


def test_query_begins_chat_conversation_before_tool_enabled_brain_call():
    """Verify overlay prompts are saved before long tool waits or approval prompts."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
            "file_access": "ask",
        }
    ]
    native = FakeWorker(
        {"native.context.snapshot": context_handler(selected="article passage")}
    )
    events: list[str] = []

    def begin_chat(params: dict[str, Any]) -> dict[str, Any]:
        events.append("begin")
        assert params["user"] == "edit file"
        assert params["context"].startswith("[Selected text]\narticle passage")
        return {"started": True, "conversation_index": 2}

    def stream(_params: dict[str, Any], _on_event) -> dict[str, Any]:
        events.append("brain")
        return {"text": "done"}

    brain = FakeWorker(stream_handlers={"brain.query": stream})
    ui = FakeWorker(handlers={"ui.chat.begin_conversation": begin_chat})
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "edit file"})

    assert events == ["begin", "brain"]
    final_chat = ui.last_call("ui.chat.add_conversation")["params"]
    assert final_chat["assistant"] == "done"
    assert final_chat["append_user"] is False
    assert final_chat["conversation_index"] == 2


def test_browser_url_captured_at_hotkey_time_fetches_content_by_handle(monkeypatch):
    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True)
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        result = {
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Browser", "pid": 42, "bundle_id": "com.browser"},
        }
        # Mirrors the native worker: URL + window handle are grabbed at hotkey
        # time while the browser is still foreground; the page text is not.
        if params.get("include_browser_url"):
            result["browser_url"] = "https://example.test/page"
            result["browser_hwnd"] = 777
        return result

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda params: {
                "url": params.get("url"),
                "content": f"Window text via hwnd {params.get('hwnd')}",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        browser_chip = next(
            item
            for item in ui.calls_for("ui.intent.context_items")[0]["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert browser_chip["state"] == "on"
        assert browser_chip["tokens"].startswith("~")
        assert "not known yet" in browser_chip["warning"]
        updated_browser_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert updated_browser_chip["state"] == "on"
        assert updated_browser_chip["tokens"].startswith("~")
        assert updated_browser_chip["warning"] == "Privacy: 1 item(s) detected and censored."
        ui.emit("ui.intent.chosen", {"custom": "What is this page?"})

    fetch = native.last_call("native.context.browser_content")["params"]
    assert fetch == {"url": "https://example.test/page", "hwnd": 777, "app": ""}
    assert len(native.calls_for("native.context.browser_content")) == 1
    # No focus-race re-detection: the snapshot ran exactly once, at hotkey time.
    assert len(native.calls_for("native.context.snapshot")) == 1
    ambient = brain.last_call("brain.query")["params"]["ambient_text"]
    assert "https://example.test/page" in ambient
    assert "Window text via hwnd 777" in ambient


def test_browser_hwnd_without_url_fetches_content_by_handle():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    native = FakeWorker(
        {
            "native.context.snapshot": lambda params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {"name": "Calc", "pid": 42, "window_id": 111, "bundle_id": ""},
                "browser_url": "",
                "browser_hwnd": 777 if params.get("include_browser_url") else 0,
            },
            "native.context.browser_content": lambda params: {
                "url": params.get("url"),
                "content": f"Rendered browser text {params.get('hwnd')}",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        browser_chip = next(
            item
            for item in ui.calls_for("ui.intent.context_items")[0]["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert browser_chip["state"] == "on"
        assert browser_chip["tokens"] == "? tok"
        assert "not known yet" in browser_chip["warning"]
        updated_browser_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert updated_browser_chip["state"] == "on"
        assert updated_browser_chip["tokens"].startswith("~")
        assert updated_browser_chip["warning"] == ""
        ui.emit("ui.intent.chosen", {"custom": "What is the page?"})

    assert native.last_call("native.context.browser_content")["params"] == {"url": "", "hwnd": 777, "app": ""}
    assert len(native.calls_for("native.context.browser_content")) == 1
    assert "Rendered browser text 777" in brain.last_call("brain.query")["params"]["ambient_text"]


def test_background_chrome_and_edge_are_separate_removable_browser_sources():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        pages = [
            {
                "id": "browser:701",
                "title": "Guide.pdf - Google Chrome",
                "process_name": "chrome.exe",
                "app": "chrome.exe",
                "url": "file:///C:/Docs/Guide.pdf",
                "hwnd": 701,
                "content": "",
            },
            {
                "id": "browser:702",
                "title": "Project site - Microsoft Edge",
                "process_name": "msedge.exe",
                "app": "msedge.exe",
                "url": "https://example.test/project",
                "hwnd": 702,
                "content": "",
            },
        ]
        return {
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Notepad", "pid": 42, "window_id": 111},
            "browser_pages": pages if params.get("include_browser_url") else [],
            "browser_url": pages[0]["url"] if params.get("include_browser_url") else "",
            "browser_hwnd": 701 if params.get("include_browser_url") else 0,
        }

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda params: {
                "url": params.get("url"),
                "content": (
                    "Chrome PDF preparation guide"
                    if params.get("hwnd") == 701
                    else "Edge project website"
                ),
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        browser_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert [source["app"] for source in browser_chip["sources"]] == ["Chrome", "Edge"]
        assert len(native.calls_for("native.context.browser_content")) == 2

        ui.emit("ui.intent.context.remove", {"id": "browser", "source_id": "browser:701"})
        filtered_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert [source["app"] for source in filtered_chip["sources"]] == ["Edge"]
        ui.emit("ui.intent.chosen", {"custom": "Use the browser context"})

    ambient = brain.last_call("brain.query")["params"]["ambient_text"]
    assert "Edge project website" in ambient
    assert "Chrome PDF preparation guide" not in ambient
    assert "BEGIN BROWSER PAGE: Edge" in ambient


def test_browser_app_captured_at_hotkey_time_fetches_text_via_applescript():
    """macOS path: the browser app name + URL are grabbed at hotkey time, then
    the page text is read by app (AppleScript) - no read-by-handle on macOS."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        result = {
            "platform": "darwin",
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Safari", "pid": 42, "bundle_id": "com.apple.Safari"},
        }
        # Mirrors the macOS worker: URL + browser app are grabbed at hotkey time;
        # the page text is deferred (no window handle on macOS).
        if params.get("include_browser_url"):
            result["browser_url"] = "https://example.test/page"
            result["browser_app"] = "Safari"
        return result

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda params: {
                "url": params.get("url"),
                "content": f"Page text via {params.get('app')}",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "What is this page?"})

    fetch = native.last_call("native.context.browser_content")["params"]
    assert fetch == {"url": "https://example.test/page", "hwnd": 0, "app": "Safari"}
    # No focus-race re-detection: the snapshot ran exactly once, at hotkey time.
    assert len(native.calls_for("native.context.snapshot")) == 1
    ambient = brain.last_call("brain.query")["params"]["ambient_text"]
    assert "https://example.test/page" in ambient
    assert "Page text via Safari" in ambient


def test_macos_begin_caller_captures_safari_before_intent_overlay(monkeypatch):
    """macOS path: capture the browser target before the picker steals focus."""
    monkeypatch.setattr(sys, "platform", "darwin")
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    order: list[str] = []

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        order.append("snapshot")
        return {
            "platform": "darwin",
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Safari", "pid": 42, "bundle_id": "com.apple.Safari"},
            "browser_app": "Safari" if params.get("include_browser_url") else "",
        }

    def show_intent(_params: dict[str, Any]) -> dict[str, Any]:
        order.append("show")
        return {"shown": True}

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda params: {
                "url": "https://example.test/safari",
                "content": f"Deferred page text via {params.get('app')}",
            },
        }
    )
    ui = FakeWorker({"ui.show_intent": show_intent})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        _flow.begin_caller(0)
        first_browser_chip = next(
            item
            for item in ui.last_call("ui.show_intent")["params"]["context_items"]
            if item["id"] == "browser"
        )
        updated_browser_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "browser"
        )
        ui.emit("ui.intent.chosen", {"custom": "What is this page?"})

    assert order[:2] == ["snapshot", "show"]
    assert len(native.calls_for("native.context.snapshot")) == 1
    assert first_browser_chip["tokens"] == "? tok"
    assert updated_browser_chip["tokens"].startswith("~")
    assert native.last_call("native.context.browser_content")["params"] == {
        "url": "",
        "hwnd": 0,
        "app": "Safari",
    }
    ambient = brain.last_call("brain.query")["params"]["ambient_text"]
    assert "[App]\nActive app: Safari" in ambient
    assert "https://example.test/safari" in ambient
    assert "Deferred page text via Safari" in ambient


def test_macos_intent_enabled_browser_uses_pre_picker_safari(monkeypatch):
    """Browser/Web can be turned on per prompt after Safari lost focus."""
    monkeypatch.setattr(sys, "platform", "darwin")
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": lambda params: {
                "platform": "darwin",
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {"name": "Safari", "pid": 42, "bundle_id": "com.apple.Safari"},
                "browser_app": "Safari" if params.get("include_browser_url") else "",
            },
            "native.context.browser_content": lambda params: {
                "url": "https://example.test/enabled",
                "content": f"Enabled page text via {params.get('app')}",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "Use this page",
                "context_choices": [{"id": "browser", "state": "on"}],
            },
        )

    assert native.last_call("native.context.browser_content")["params"] == {
        "url": "",
        "hwnd": 0,
        "app": "Safari",
    }
    ambient = brain.last_call("brain.query")["params"]["ambient_text"]
    assert "https://example.test/enabled" in ambient
    assert "Enabled page text via Safari" in ambient


def test_intent_enabled_browser_fetches_from_hotkey_time_target_when_setting_off():
    """Verify a per-prompt Browser/Web toggle can use the original foreground tab."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        result = {
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Browser", "pid": 42, "window_id": 777, "bundle_id": ""},
        }
        if params.get("include_browser_url"):
            result["browser_url"] = "https://example.test/off-by-default"
            result["browser_hwnd"] = 777
        return result

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda params: {
                "url": params.get("url"),
                "content": f"Deferred page text {params.get('hwnd')}",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        browser_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert browser_chip["state"] == "off"
        assert browser_chip["tokens"].startswith("~")
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "Use the page",
                "context_choices": [{"id": "browser", "state": "on"}],
            },
        )

    snapshot_params = native.calls_for("native.context.snapshot")[0]["params"]
    assert snapshot_params["include_browser_url"] is True
    assert snapshot_params["include_browser_content"] is False
    assert len(native.calls_for("native.context.snapshot")) == 1
    assert len(native.calls_for("native.context.browser_content")) == 1
    assert native.last_call("native.context.browser_content")["params"] == {
        "url": "https://example.test/off-by-default",
        "hwnd": 777,
        "app": "",
    }
    ambient = brain.last_call("brain.query")["params"]["ambient_text"]
    assert "https://example.test/off-by-default" in ambient
    assert "Deferred page text 777" in ambient


def test_intent_enabled_clipboard_uses_hotkey_time_clipboard_when_setting_off():
    """Verify Clipboard can be enabled per prompt even when disabled in settings."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_text": "",
            "clipboard_text": "clipboard from hotkey time" if params.get("include_clipboard") else "",
            "active_app": {"name": "Notes", "pid": 42, "bundle_id": "com.apple.Notes"},
        }

    native = FakeWorker({"native.context.snapshot": snapshot_handler})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        clipboard_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "clipboard"
        )
        assert clipboard_chip["state"] == "off"
        assert clipboard_chip["tokens"].startswith("~")
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "Use clipboard",
                "context_choices": [{"id": "clipboard", "state": "on"}],
            },
        )

    assert native.calls_for("native.context.snapshot")[0]["params"]["include_clipboard"] is True
    ambient = brain.last_call("brain.query")["params"]["ambient_text"]
    assert "[Clipboard]" in ambient
    assert "clipboard from hotkey time" in ambient


def test_intent_enabled_app_fetches_active_document_when_setting_off():
    """Verify the App chip can enable document context for one prompt."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "DOC TEXT"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        app_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "ambient"
        )
        assert app_chip["state"] == "off"
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "Use open document",
                "context_choices": [
                    {"id": "ambient", "state": "on", "default_state": "off", "touched": True},
                ],
            },
        )

    query = brain.last_call("brain.query")["params"]
    assert query["active_document_text"] == "DOC TEXT"
    assert query["active_document_label"] == "Notes"
    assert query["include_active_document"] is False
    assert {"label": "App", "type": "file"} in ui.last_call("ui.context.summary")["params"]["items"]


def test_intent_app_on_estimates_and_sends_active_document_when_documents_off():
    """Verify an enabled App chip includes active document text in its estimate."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "notepad body " * 40}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        app_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "ambient"
        )
        assert app_chip["state"] == "on"
        assert app_chip["tokens"].startswith("~")
        assert app_chip["tokens"] != "~4 tok"
        ui.emit("ui.intent.chosen", {"custom": "Use the open notepad"})

    query = brain.last_call("brain.query")["params"]
    assert "notepad body" in query["active_document_text"]
    assert query["active_document_label"] == "Notes"
    assert query["include_active_document"] is False


def test_intent_app_preview_lists_multiple_active_document_sources():
    """Verify App context preview exposes each detected document source."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(
        handlers={
            "brain.context.active_document": lambda _params: {
                "text": "[Notepad]\nnotepad body\n\n[demo.py]\nVS Code paragraph",
                "debug": {"window_labels": ["Notepad", "demo.py"]},
            }
        },
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)

    app_chip = next(
        item
        for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
        if item["id"] == "ambient"
    )
    assert app_chip["sources"] == [
        {"label": "Notepad", "preview": "notepad body"},
        {"label": "demo.py", "preview": "VS Code paragraph"},
    ]
    ui.emit("ui.intent.chosen", {"custom": "Use VS Code"})
    query = brain.last_call("brain.query")["params"]
    assert query["active_document_label"] == "Open app documents"
    assert "[Notepad]\nnotepad body" in query["active_document_text"]
    assert "[demo.py]\nVS Code paragraph" in query["active_document_text"]


def test_active_document_previews_carry_application_names_for_visible_labels() -> None:
    """Context rows identify the owning application instead of App 1/App 2."""
    flow, _native, _ui, _brain, _audio = make_flow()

    sources = flow._active_document_source_previews(  # noqa: SLF001 - focused formatting contract
        "[demo.py]\ndef greet(): return 'hi'\n\n[Contact form]\nName Email Country",
        {
            "window_candidates": [
                {
                    "label": "demo.py",
                    "title": "demo.py - Visual Studio Code",
                    "process_name": "Code.exe",
                    "accepted": True,
                },
                {
                    "label": "Contact form",
                    "title": "Contact form - Google Chrome",
                    "process_name": "chrome.exe",
                    "accepted": True,
                },
            ],
        },
    )

    assert sources == [
        {"label": "demo.py", "preview": "def greet(): return 'hi'", "app": "VS Code"},
        {"label": "Contact form", "preview": "Name Email Country", "app": "Google Chrome"},
    ]


def test_context_priority_marks_browser_when_browser_was_active():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        result = {
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Browser", "pid": 42, "bundle_id": "com.browser"},
        }
        if params.get("include_browser_url"):
            result["browser_url"] = "https://example.test/page"
            result["browser_hwnd"] = 777
        return result

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda _params: {
                "url": "https://example.test/page",
                "content": "Browser text",
            },
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "DOC TEXT"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Use both"})

    query = brain.last_call("brain.query")["params"]
    assert query["context_priority"] == "Browser/Web"


def test_context_priority_marks_document_when_browser_was_background_context():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        result = {
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Notes", "pid": 42, "bundle_id": "com.apple.Notes"},
        }
        if params.get("include_browser_url"):
            result["browser_url"] = "https://example.test/page"
            result["browser_hwnd"] = 777
        return result

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda _params: {
                "url": "https://example.test/page",
                "content": "Browser text",
            },
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "DOC TEXT"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Use both"})

    query = brain.last_call("brain.query")["params"]
    assert query["context_priority"] == "Active document"


def test_selected_context_is_primary_and_browser_is_supporting_context():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        result = {
            "selected_text": "the exact selected target",
            "clipboard_text": "",
            "active_app": {"name": "Browser", "pid": 42, "bundle_id": "com.browser"},
        }
        if params.get("include_browser_url"):
            result["browser_url"] = "https://example.test/reference"
            result["browser_hwnd"] = 777
        return result

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda _params: {
                "url": "https://example.test/reference",
                "content": "Supporting browser reference",
            },
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "SUPPORTING DOC"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Explain this"})

    query = brain.last_call("brain.query")["params"]
    assert query["selected"] == "the exact selected target"
    assert query["context_priority"] == "Selection"
    assert "Priority: supporting" in query["ambient_text"]
    assert "Priority: primary" not in query["ambient_text"]


def test_active_document_auto_fetches_before_query_and_summary():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "DOC TEXT"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        updated_app_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "ambient"
        )
        assert updated_app_chip["tokens"].startswith("~")
        assert updated_app_chip["warning"] == ""
        ui.emit("ui.intent.chosen", {"custom": "Use open docs"})

    query = brain.last_call("brain.query")["params"]
    assert query["active_document_text"] == "DOC TEXT"
    assert query["active_document_label"] == "Notes"
    assert query["include_active_document"] is False
    assert len(brain.calls_for("brain.context.active_document")) == 1
    summary = ui.last_call("ui.context.summary")["params"]["items"]
    assert {"label": "App", "type": "file"} in summary


def test_active_document_request_includes_hotkey_time_window():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {
                    "name": "Untitled 1 \u2014 LibreOffice Calc",
                    "pid": 202,
                    "window_id": 222,
                    "bundle_id": "",
                },
                "debug": {
                    "window": {
                        "chosen_process": "soffice.bin",
                        "chosen_title": "Untitled 1 \u2014 LibreOffice Calc",
                        "chosen_pid": 202,
                        "chosen_hwnd": 222,
                    }
                },
            }
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "CALC CELLS"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Read the sheet"})

    params = brain.last_call("brain.context.active_document")["params"]
    assert params["active_window"] == {
        "title": "Untitled 1 \u2014 LibreOffice Calc",
        "process_name": "soffice.bin",
        "pid": 202,
        "window_id": 222,
    }
    query = brain.last_call("brain.query")["params"]
    assert query["active_document_text"] == "CALC CELLS"
    assert query["active_document_label"] == "soffice.bin - Untitled 1 \u2014 LibreOffice Calc"


def test_active_document_request_prefers_captured_macos_window_title():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {
                    "name": "TextEdit",
                    "pid": 202,
                    "bundle_id": "com.apple.TextEdit",
                },
                "debug": {
                    "window": {
                        "chosen_process": "TextEdit",
                        "chosen_title": "Notes.txt",
                        "chosen_pid": 202,
                    }
                },
            }
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "TXT BODY"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Read the txt"})

    params = brain.last_call("brain.context.active_document")["params"]
    assert params["active_window"] == {
        "title": "Notes.txt",
        "process_name": "TextEdit",
        "pid": 202,
        "window_id": 0,
    }
    assert brain.last_call("brain.query")["params"]["active_document_text"] == "TXT BODY"


def test_active_document_request_prefers_document_window_over_active_app():
    """Verify App/Docs uses the captured document window, not active app."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {
                    "name": "python",
                    "pid": 999,
                    "bundle_id": "",
                },
                "document_window": {
                    "title": "Notes.txt",
                    "process_name": "TextEdit",
                    "pid": 202,
                    "window_id": 0,
                },
            }
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "TXT BODY"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Read the txt"})

    params = brain.last_call("brain.context.active_document")["params"]
    assert params["active_window"] == {
        "title": "Notes.txt",
        "process_name": "TextEdit",
        "pid": 202,
        "window_id": 0,
    }


def test_no_tts_reply_done_lets_wpm_reveal_drain():
    # With TTS off, reply.done must NOT flush the bubble: the WPM reveal keeps
    # pacing the text (flush=False), instead of the full reply slamming in the
    # moment the LLM finishes streaming.
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("a fairly long reply")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "tell me"})

    done_calls = ui.calls_for("ui.reply.done")
    assert done_calls
    assert all(call["params"] == {"flush": False} for call in done_calls)


def test_configured_tts_provider_does_not_auto_speak_replies_without_opt_in():
    """Configured TTS should remain available for F7 without speaking every reply."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("a spoken-capable reply")})
    audio = FakeWorker(
        handlers={
            "audio.tts.synthesize": lambda _params: {"path": "reply.wav"},
            "audio.play_file": lambda _params: {"played": True, "stopped": False},
        }
    )
    with caller_config(rows):
        config.TTS_PROVIDER = "cartesia"
        config.TTS_SPEAK_REPLIES = False
        _flow, native, _ui, _brain, audio = make_flow(native=native, brain=brain, audio=audio)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        _ui.emit("ui.intent.chosen", {"custom": "tell me"})

    assert not audio.calls_for("audio.tts.synthesize")
    assert not audio.calls_for("audio.play_file")


def test_tts_speaks_completed_segments_before_full_reply_done():
    """Verify TTS starts after the first stable segment, before final reply.done."""
    first = "This is the first completed spoken part with enough detail to start audio now."
    second = "This is the second completed spoken part."
    first_synth_started = threading.Event()

    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit one completed segment, wait for TTS, then finish."""
        on_event("reply.chunk", {"text": first + " "}, 1)
        assert first_synth_started.wait(2.0), "first segment should synthesize before reply.done"
        on_event("reply.chunk", {"text": second}, 1)
        on_event("reply.done", {"text": f"{first} {second}"}, 1)
        return {"text": f"{first} {second}"}

    def wait_for_audio_calls(audio: FakeWorker, count: int) -> None:
        """Wait until the background TTS queue drains."""
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if len(audio.calls_for("audio.play_file")) >= count:
                return
            time.sleep(0.01)
        raise AssertionError(f"expected {count} audio.play_file calls")

    def synth(params: dict[str, Any]) -> dict[str, Any]:
        """Return a fake WAV path for a TTS segment."""
        if params["text"] == first:
            first_synth_started.set()
        return {"path": f"{params['text'][:8]}.wav", "word_timestamps": {"words": [], "start_ms": []}}

    brain = FakeWorker(stream_handlers={"brain.query": stream})
    audio = FakeWorker(
        handlers={
            "audio.tts.synthesize": synth,
            "audio.play_file": lambda _params: {"played": True, "stopped": False},
        }
    )
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    with caller_config(rows):
        config.TTS_PROVIDER = "cartesia"
        config.TTS_SPEAK_REPLIES = True
        _flow, _native, ui, _brain, audio = make_flow(native=native, brain=brain, audio=audio)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "tell me"})
        wait_for_audio_calls(audio, 2)

    synth_texts = [call["params"]["text"] for call in audio.calls_for("audio.tts.synthesize")]
    assert synth_texts == [first, second]
    assert len(audio.calls_for("audio.play_file")) == 2
    assert ui.calls_for("ui.reply.track_speech")
    assert ui.last_call("ui.chat.add_conversation")["params"]["assistant"] == f"{first} {second}"


def test_blocking_tts_schedules_word_timings_before_playback():
    """Word timings from file-based TTS should drive the bubble during playback."""
    ui = FakeWorker()

    def play_file(_params: dict[str, Any]) -> dict[str, Any]:
        assert ui.calls_for("ui.reply.schedule_words")
        return {"played": True, "stopped": False}

    audio = FakeWorker(
        handlers={
            "audio.tts.synthesize": lambda _params: {
                "path": "reply.wav",
                "word_timestamps": {"words": ["hello", "world"], "start_ms": [0, 400]},
            },
            "audio.play_file": play_file,
        }
    )
    flow, _native, ui, _brain, _audio = make_flow(ui=ui, audio=audio)

    assert flow._speak_text("hello world", wait_for_playback=True)
    assert ui.calls_for("ui.reply.schedule_words")[0]["params"] == {
        "words": ["hello", "world"],
        "start_ms": [0, 400],
    }


def test_blocking_tts_does_not_schedule_estimated_word_timings():
    """Estimated timings are not exact enough to drive per-word highlighting."""
    ui = FakeWorker()
    audio = FakeWorker(
        handlers={
            "audio.tts.synthesize": lambda _params: {
                "path": "reply.wav",
                "word_timestamps": {
                    "words": ["hello", "estimated", "world"],
                    "start_ms": [0, 300, 600],
                    "estimated": True,
                },
            },
            "audio.play_file": lambda _params: {"played": True, "stopped": False},
        }
    )
    flow, _native, ui, _brain, _audio = make_flow(ui=ui, audio=audio)

    assert flow._speak_text("hello estimated world", wait_for_playback=True)
    assert not ui.calls_for("ui.reply.schedule_words")


def test_tts_speaks_short_progress_before_final_answer():
    """Verify short progress narration speaks during tool waits."""
    progress = "Checking the file first."
    final = "Done."
    progress_synth_started = threading.Event()

    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit short progress, wait for TTS, then answer."""
        on_event("reply.chunk", {"text": progress, "is_progress": True}, 1)
        assert progress_synth_started.wait(2.0), "progress should synthesize before final answer"
        on_event("reply.chunk", {"text": final}, 1)
        on_event("reply.done", {"text": final}, 1)
        return {"text": final}

    def synth(params: dict[str, Any]) -> dict[str, Any]:
        """Return a fake WAV path for a TTS segment."""
        if params["text"] == progress:
            progress_synth_started.set()
        return {"path": f"{params['text'][:8]}.wav", "word_timestamps": {"words": [], "start_ms": []}}

    brain = FakeWorker(stream_handlers={"brain.query": stream})
    audio = FakeWorker(
        handlers={
            "audio.tts.synthesize": synth,
            "audio.play_file": lambda _params: {"played": True, "stopped": False},
        }
    )
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    with caller_config(rows):
        config.TTS_PROVIDER = "cartesia"
        config.TTS_SPEAK_REPLIES = True
        _flow, _native, ui, _brain, audio = make_flow(native=native, brain=brain, audio=audio)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "edit"})
        deadline = time.monotonic() + 2.0
        while len(audio.calls_for("audio.play_file")) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

    synth_texts = [call["params"]["text"] for call in audio.calls_for("audio.tts.synthesize")]
    assert synth_texts == [progress, final]
    assert ui.last_call("ui.chat.add_conversation")["params"]["assistant"] == final


def test_thought_chunks_show_without_becoming_final_answer():
    """Verify structured thought chunks render early but do not pollute final text."""
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

    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit thought text before the visible answer."""
        on_event("reply.chunk", {"text": "Thinking first.", "is_thought": True}, 1)
        on_event("reply.chunk", {"text": "Answer."}, 1)
        on_event("reply.done", {"text": "Answer."}, 1)
        return {"text": "Answer."}

    brain = FakeWorker(stream_handlers={"brain.query": stream})
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Explain"})

    chunks = [call["params"] for call in ui.calls_for("ui.reply.chunk")]
    assert {"text": "Thinking first.", "is_thought": True, "is_progress": False} in chunks
    assert {"text": "Answer.", "is_thought": False, "is_progress": False} in chunks
    assert len(ui.calls_for("ui.reply.reset")) == 1
    assert ui.last_call("ui.chat.add_conversation")["params"]["assistant"] == "Answer."


def test_model_screenshot_mode_precaptures_through_native_worker():
    image_bytes = b"fake screenshot"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)

    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "model",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected=""),
            "native.capture.fullscreen": lambda _params: {"ok": True, "path": str(image_path)},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("vision reply")})
    try:
        with caller_config(rows):
            _flow, native, _ui, brain, _audio = make_flow(native=native, brain=brain)
            native.emit("native.hotkey", {"kind": "caller", "index": 0})
            _ui.emit("ui.intent.chosen", {"custom": "what can you see?"})
    finally:
        image_path.unlink(missing_ok=True)

    query = brain.last_call("brain.query")["params"]
    assert native.last_call("native.capture.fullscreen")["timeout"] == 8.0
    assert query["allow_screenshot_tool"] is True
    assert query["screenshot_tool_b64"] == base64.b64encode(image_bytes).decode("ascii")


def test_capture_worker_unavailable_does_not_escape_caller_workflow():
    """A denied or dead capture worker must leave screenshot context empty."""
    def denied(_params):
        raise PermissionError("screen-recording permission missing")

    rows = [
        {
            "paste_back": False,
            "context_screenshot": "auto",
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected=""),
            "native.capture.fullscreen": denied,
        }
    )
    with caller_config(rows):
        flow, _native, ui, _brain, _audio = make_flow(native=native)
        flow.begin_caller(0)

    assert flow._pending is not None
    assert flow._pending.screenshot_b64 is None
    assert ui.calls_for("ui.show_intent")
    assert flow._capture_model_tool_b64() == ""


def test_auto_screenshot_mode_captures_even_with_selected_text():
    image_bytes = b"selected plus screenshot"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)

    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "auto",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="some selected text"),
            "native.capture.fullscreen": lambda _params: {"ok": True, "path": str(image_path)},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("vision reply")})
    try:
        with caller_config(rows):
            _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
            native.emit("native.hotkey", {"kind": "caller", "index": 0})
            ui.emit("ui.intent.chosen", {"custom": "what can you see?"})
    finally:
        image_path.unlink(missing_ok=True)

    query = brain.last_call("brain.query")["params"]
    assert native.last_call("native.capture.fullscreen")["timeout"] == 30.0
    assert query["selected"] == "some selected text"
    assert query["screenshot_b64"] == base64.b64encode(image_bytes).decode("ascii")


def test_precaptured_screenshot_is_discarded_when_final_choice_turns_it_off():
    """Verify a captured screenshot is not sent after the chip is toggled off."""
    image_bytes = b"disabled before send"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)

    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "auto",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected=""),
            "native.capture.fullscreen": lambda _params: {"ok": True, "path": str(image_path)},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("vision reply")})
    try:
        with caller_config(rows):
            _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
            native.emit("native.hotkey", {"kind": "caller", "index": 0})
            ui.emit(
                "ui.intent.chosen",
                {
                    "custom": "ignore the screen",
                    "context_choices": [{"id": "screenshot", "state": "off", "default_state": "on", "touched": True}],
                },
            )
    finally:
        image_path.unlink(missing_ok=True)

    query = brain.last_call("brain.query")["params"]
    assert len(native.calls_for("native.capture.fullscreen")) == 1
    assert query["screenshot_b64"] is None
    assert query["screenshot_tool_b64"] is None
    assert ui.last_call("ui.chat.begin_conversation")["params"]["image_base64"] is None


def test_unused_pending_context_is_discarded_after_payload_build():
    """Verify unselected preview context is removed from transient request state."""
    pending = PendingInvocation(
        context={
            "selected_text": "preview selection",
            "clipboard_text": "preview clipboard",
            "browser_url": "https://example.test/private",
            "browser_content": "preview browser",
            "browser_app": "Browser",
            "browser_hwnd": 123,
            "active_document_text": "preview document",
            "active_document_sources": [{"label": "Doc"}],
            "document_window": {"title": "Doc"},
            "active_app": {"name": "Notes"},
            "debug": {"window": {"raw_title": "Secret title"}},
            "focus_token": 99,
        },
        screenshot_b64="SCREEN",
        screenshot_tool_b64="TOOL",
    )

    FlowController._discard_unused_pending_context(
        pending,
        {
            "selected": "",
            "ambient_text": "",
            "active_document_text": "",
            "screenshot_b64": None,
            "screenshot_tool_b64": None,
        },
    )

    for key in (
        "selected_text",
        "clipboard_text",
        "browser_url",
        "browser_content",
        "browser_app",
        "browser_hwnd",
        "active_document_text",
        "active_document_sources",
        "document_window",
        "active_app",
        "debug",
    ):
        assert key not in pending.context
    assert pending.context["focus_token"] == 99
    assert pending.screenshot_b64 is None
    assert pending.screenshot_tool_b64 is None


def test_screenshot_toggled_on_later_captures_at_send_time():
    """Verify initially disabled screenshot context is captured only after send."""
    image_bytes = b"enabled at send"
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
            "native.context.snapshot": context_handler(selected=""),
            "native.capture.fullscreen": lambda _params: {"ok": True, "path": str(image_path)},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("vision reply")})
    try:
        with caller_config(rows):
            _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
            native.emit("native.hotkey", {"kind": "caller", "index": 0})
            assert not native.calls_for("native.capture.fullscreen")
            ui.emit(
                "ui.intent.chosen",
                {
                    "custom": "look now",
                    "context_choices": [{"id": "screenshot", "state": "on", "default_state": "off", "touched": True}],
                },
            )
    finally:
        image_path.unlink(missing_ok=True)

    query = brain.last_call("brain.query")["params"]
    assert len(native.calls_for("native.capture.fullscreen")) == 1
    assert query["screenshot_b64"] == base64.b64encode(image_bytes).decode("ascii")


def test_screenshot_chip_snip_attaches_region_to_current_intent():
    """Verify Screenshot chip snips keep the active caller and attach the region image."""
    image_bytes = b"intent screenshot snip"
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
            "native.context.snapshot": context_handler(selected=""),
            "native.capture.region": lambda _params: {"ok": True, "path": str(image_path)},
            "native.capture.fullscreen": lambda _params: pytest.fail("unexpected fullscreen capture"),
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("vision reply")})
    try:
        with caller_config(rows):
            _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
            native.emit("native.hotkey", {"kind": "caller", "index": 0})
            ui.emit(
                "ui.intent.snip.requested",
                {
                    "custom_text": "look at this area",
                    "context_choices": [
                        {"id": "screenshot", "state": "on", "default_state": "off", "touched": True}
                    ],
                },
            )
            ui.emit("ui.intent.snip.region", {"left": 10, "top": 20, "width": 300, "height": 200})
            ui.emit(
                "ui.intent.chosen",
                {
                    "custom": "look at this area",
                    "context_choices": [
                        {"id": "screenshot", "state": "on", "default_state": "off", "touched": True}
                    ],
                },
            )
    finally:
        image_path.unlink(missing_ok=True)

    assert native.last_call("native.capture.region")["params"]["region"]["width"] == 300
    restored = ui.last_call("ui.show_intent")["params"]
    assert restored["initial_custom_text"] == "look at this area"
    restored_chips = {item["id"]: item for item in restored["context_items"]}
    assert restored_chips["screenshot"]["state"] == "on"
    query = brain.last_call("brain.query")["params"]
    assert query["intent_prompt"] == "look at this area"
    assert query["screenshot_b64"] == base64.b64encode(image_bytes).decode("ascii")
    assert not native.calls_for("native.capture.fullscreen")


def test_cancelled_screenshot_chip_snip_does_not_capture_fullscreen():
    """Verify backing out of a Screenshot-chip snip leaves screenshots off."""
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
            "native.context.snapshot": context_handler(selected=""),
            "native.capture.fullscreen": lambda _params: pytest.fail("unexpected fullscreen capture"),
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("reply")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit(
            "ui.intent.snip.requested",
            {
                "context_choices": [
                    {"id": "screenshot", "state": "on", "default_state": "off", "touched": True}
                ],
            },
        )
        ui.emit("ui.intent.snip.cancelled", {})
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "answer without image",
                "context_choices": [
                    {"id": "screenshot", "state": "off", "default_state": "off", "touched": False}
                ],
            },
        )

    query = brain.last_call("brain.query")["params"]
    assert query["screenshot_b64"] is None
    assert not native.calls_for("native.capture.fullscreen")
    shown = ui.last_call("ui.show_intent")["params"]
    chips = {item["id"]: item for item in shown["context_items"]}
    assert chips["screenshot"]["state"] == "off"
    assert chips["screenshot"]["force_state"] is True


def test_chat_screenshot_chip_snip_attaches_image_without_file_permission():
    """Verify chat Screenshot On attaches the snipped image to the composer."""
    image_bytes = b"chat screenshot snip"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)

    native = FakeWorker(
        {
            "native.capture.region": lambda _params: {"ok": True, "path": str(image_path)},
        }
    )
    try:
        _flow, native, ui, _brain, _audio = make_flow(native=native)
        ui.emit("ui.chat.snip.region", {"left": 4, "top": 5, "width": 80, "height": 90})
    finally:
        image_path.unlink(missing_ok=True)

    assert native.last_call("native.capture.region")["params"]["region"]["height"] == 90
    attached = ui.last_call("ui.chat.capture_context")["params"]
    assert attached["source"] == "screenshot"
    assert attached["item_type"] == "image"
    assert attached["name"] == "Screenshot"
    assert attached["content"] == base64.b64encode(image_bytes).decode("ascii")


def test_chat_selection_capture_waits_for_user_selection_without_file_permission():
    """Verify chat Selection On attaches the next selected text."""
    native = FakeWorker({"native.context.await_selection": context_handler(selected="picked text")})
    _flow, native, ui, _brain, _audio = make_flow(native=native)

    ui.emit("ui.chat.selection.requested", {})

    attached = ui.last_call("ui.chat.capture_context")["params"]
    assert attached == {
        "name": "Selection",
        "content": "picked text",
        "item_type": "text",
        "source": "selection",
    }
    capture = native.last_call("native.context.await_selection")["params"]
    assert capture["include_clipboard"] is True


def test_chat_file_tool_summary_is_progress_not_reply_text():
    """Verify file tool summaries are shown separately from assistant reply text."""
    def chat_handler(_params: dict[str, Any], on_event) -> dict[str, Any]:
        payload = {
            "text": "real answer",
            "file_context": [
                {
                    "tool": "read_file",
                    "path": r"C:\repo\notes.md",
                    "relative_path": "notes.md",
                    "ok": True,
                    "message": "120 chars",
                }
            ],
        }
        on_event("reply.done", payload, 1)
        return payload

    brain = FakeWorker(stream_handlers={"brain.chat": chat_handler})
    _flow, _native, ui, _brain, _audio = make_flow(brain=brain)

    ui.emit(
        "ui.chat.request",
        {
            "request_id": "chat-1",
            "messages": [{"role": "user", "content": "what is in notes?"}],
            "context_policy": {"file_access": "read"},
        },
    )

    chunks = [call["params"] for call in ui.calls_for("ui.chat.chunk")]
    summary = next(item for item in chunks if item.get("text") == "Read file: notes.md")
    assert summary["request_id"] == "chat-1"
    assert summary["is_progress"] is True
    assert summary["is_thought"] is True
    done = ui.last_call("ui.chat.done")["params"]
    assert done["text"] == "real answer"


def test_intent_selection_capture_restores_picker_with_selected_text():
    """Verify intent Selection On hides, captures the next selection, then restores."""
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
            "native.context.snapshot": context_handler(selected=""),
            "native.context.await_selection": context_handler(selected="intent picked text"),
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("selection answer")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit(
            "ui.intent.selection.requested",
            {
                "custom_text": "keep my draft",
                "context_choices": [
                    {"id": "selection", "state": "on", "default_state": "off", "touched": True}
                ],
            },
        )

    shown = ui.last_call("ui.show_intent")["params"]
    assert shown["initial_custom_text"] == "keep my draft"
    chips = {item["id"]: item for item in shown["context_items"]}
    assert chips["selection"]["state"] == "on"
    assert chips["selection"]["tokens"].startswith("~")
    ui.emit(
        "ui.intent.chosen",
        {
            "custom": "keep my draft",
            "context_choices": [
                {"id": "selection", "state": "on", "default_state": "off", "touched": True}
            ],
        },
    )
    query = brain.last_call("brain.query")["params"]
    assert query["intent_prompt"] == "keep my draft"
    assert query["selected"] == "intent picked text"


def test_intent_selection_capture_ignores_lifecycle_cancel_while_hidden():
    """Verify temporary hidden Selection capture does not cancel the pending intent."""
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
    ui_ref: dict[str, FakeWorker] = {}

    def await_selection(params: dict[str, Any]) -> dict[str, Any]:
        ui_ref["ui"].emit("ui.intent.cancelled", {})
        return context_handler(selected="picked after hide")(params)

    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected=""),
            "native.context.await_selection": await_selection,
        }
    )
    with caller_config(rows):
        _flow, native, ui, _brain, _audio = make_flow(native=native)
        ui_ref["ui"] = ui
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit(
            "ui.intent.selection.requested",
            {
                "custom_text": "selection draft",
                "context_choices": [
                    {"id": "selection", "state": "on", "default_state": "off", "touched": True}
                ],
            },
        )

    shown = ui.last_call("ui.show_intent")["params"]
    assert shown["initial_custom_text"] == "selection draft"
    chips = {item["id"]: item for item in shown["context_items"]}
    assert chips["selection"]["state"] == "on"
    assert chips["selection"]["preview"] == "picked after hide"


def test_chat_selection_capture_attaches_selected_paths_without_file_permission(tmp_path):
    """Verify chat Selection capture accepts selected Explorer/Finder paths."""
    picked = tmp_path / "notes.md"
    picked.write_text("selected file text", encoding="utf-8")
    native = FakeWorker(
        {
            "native.context.await_selection": context_handler(
                selected="",
                clipboard="stale clipboard time 2026-06-29T17:55:39-06:00",
                selected_paths=[str(picked)],
            )
        }
    )
    _flow, native, ui, _brain, _audio = make_flow(native=native)

    ui.emit("ui.chat.selection.requested", {})

    capture = native.last_call("native.context.await_selection")["params"]
    assert capture["include_selected_paths"] is True
    attached = ui.last_call("ui.chat.capture_context")["params"]
    assert attached["source"] == "selection"
    assert attached["paths"] == [str(picked)]
    assert attached["content"] == ""
    assert "stale clipboard" not in attached["content"]
    assert "file_access" not in attached


def test_intent_selection_capture_uses_selected_paths_without_file_permission(tmp_path):
    """Verify intent Selection capture sends selected file content as context."""
    picked = tmp_path / "notes.md"
    picked.write_text("selected file text", encoding="utf-8")
    rows = [
        {
            "paste_back": False,
            "context_ambient": False,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
            "file_access": "off",
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(
                selected="",
                clipboard="stale clipboard time 2026-06-29T17:55:39-06:00",
                selected_paths=[str(picked)],
            )
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("done")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit(
            "ui.intent.selection.requested",
            {
                "custom_text": "keep my draft",
                "context_choices": [
                    {"id": "selection", "state": "on", "default_state": "off", "touched": True}
                ],
            },
        )
        ui.emit("ui.intent.chosen", {"prompt": "summarize", "context_choices": []})

    snapshot = native.calls_for("native.context.snapshot")[-1]["params"]
    assert snapshot["include_selected_paths"] is True
    shown = ui.calls_for("ui.show_intent")[-1]["params"]
    chips = {item["id"]: item for item in shown["context_items"]}
    assert chips["selection"]["state"] == "on"
    assert "notes.md" in chips["selection"]["preview"]
    params = brain.last_call("brain.query")["params"]
    assert params["file_access_mode"] == "off"
    assert "selected file text" in params["ambient_text"]
    assert "stale clipboard" not in params["ambient_text"]


def test_caller_screenshot_precaptures_before_intent_overlay(monkeypatch):
    """Selection and desktop capture both precede the picker shell."""
    monkeypatch.setattr(sys, "platform", "linux")
    image_bytes = b"target screen"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)

    order: list[str] = []
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": True,
            "context_tools": True,
            "context_screenshot": "model",
            "context_clipboard": False,
        }
    ]

    def context(_params: dict[str, Any]) -> dict[str, Any]:
        order.append("context")
        return context_handler(selected="")(_params)

    def capture(_params: dict[str, Any]) -> dict[str, Any]:
        order.append("capture")
        return {"ok": True, "path": str(image_path)}

    def overlay_state(_params: dict[str, Any]) -> dict[str, Any]:
        order.append("overlay_state")
        return {}

    def show_intent(_params: dict[str, Any]) -> dict[str, Any]:
        order.append("show_intent")
        return {}

    def activate_intent(_params: dict[str, Any]) -> dict[str, Any]:
        order.append("activate_intent")
        return {}

    native = FakeWorker({"native.context.snapshot": context, "native.capture.fullscreen": capture})
    ui = FakeWorker(
        {
            "ui.overlay.state": overlay_state,
            "ui.show_intent": show_intent,
            "ui.intent.activate": activate_intent,
        }
    )
    try:
        with caller_config(rows):
            flow, _native, _ui, _brain, _audio = make_flow(native=native, ui=ui)
            flow.begin_caller(0)
    finally:
        image_path.unlink(missing_ok=True)

    assert order == ["overlay_state", "context", "capture", "show_intent", "activate_intent"]
    assert flow._pending is not None
    assert flow._pending.screenshot_tool_b64 == base64.b64encode(image_bytes).decode("ascii")


def test_intent_cancel_after_show_does_not_recapture_screenshot(monkeypatch):
    """Verify pre-captured screenshots are not captured again after cancellation."""
    monkeypatch.setattr(sys, "platform", "linux")
    order: list[str] = []
    image_bytes = b"target screen"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": True,
            "context_screenshot": "model",
            "context_clipboard": False,
        }
    ]

    def context(params: dict[str, Any]) -> dict[str, Any]:
        """Track initial native snapshot."""
        order.append("context")
        return context_handler(selected="")(params)

    def capture(_params: dict[str, Any]) -> dict[str, Any]:
        """Capture before the picker appears."""
        order.append("capture")
        return {"ok": True, "path": str(image_path)}

    ui = FakeWorker()
    native = FakeWorker({"native.context.snapshot": context, "native.capture.fullscreen": capture})
    try:
        with caller_config(rows):
            flow, _native, ui, _brain, _audio = make_flow(native=native, ui=ui)
            flow.begin_caller(0)
            ui.emit("ui.intent.cancelled", {})
    finally:
        image_path.unlink(missing_ok=True)

    assert order == ["context", "capture"]
    assert flow._pending is None
    assert len(native.calls_for("native.capture.fullscreen")) == 1
    assert not ui.calls_for("ui.chat.add_conversation")


def test_intent_cancel_stops_prefetch_before_browser_fetch():
    """Verify Escape cancellation prevents later prefetch stages."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    ui = FakeWorker()
    native = FakeWorker(
        {
            "native.context.snapshot": lambda params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {"name": "Browser", "pid": 42, "bundle_id": "com.browser"},
                "browser_url": "https://example.test/page" if params.get("include_browser_url") else "",
                "browser_hwnd": 777 if params.get("include_browser_url") else 0,
            },
            "native.context.browser_content": lambda _params: {"content": "should not fetch"},
        }
    )

    def active_doc(_params: dict[str, Any]) -> dict[str, Any]:
        """Cancel during active-document prefetch."""
        ui.emit("ui.intent.cancelled", {})
        return {"text": "DOC TEXT"}

    brain = FakeWorker(
        handlers={"brain.context.active_document": active_doc},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        flow, native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        flow.begin_caller(0)

    assert flow._pending is None
    assert brain.calls_for("brain.context.active_document")
    assert not native.calls_for("native.context.browser_content")
    assert not ui.calls_for("ui.chat.add_conversation")


def test_query_failure_reports_notice_and_returns_idle():
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

    def fail_query(_params: dict[str, Any], _on_event) -> dict[str, Any]:
        raise RuntimeError("ValueError: LLM route uses 'google', but its API key is not configured.")

    native = FakeWorker({"native.context.snapshot": context_handler()})
    brain = FakeWorker(stream_handlers={"brain.query": fail_query})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        ui.emit("ui.intent.chosen", {"custom": "Explain this"})

    assert ui.last_call("ui.reply.notice")["params"]["text"] == (
        "LLM request failed: LLM route uses 'google', but its API key is not configured.\n\n"
        "Recommendation: add or refresh the provider API key in Settings, then run Setup Check."
    )
    assert ui.calls_for("ui.reply.done")
    assert ui.last_call("ui.overlay.state")["params"]["state"] == "idle"


def test_builtin_intent_actions_failure_matrix_is_controlled():
    """All built-in answer actions share the same guarded request lifecycle."""
    prompts = (
        "What is this?",
        "Explain simply",
        "How do I fix this?",
        "Fix grammar",
        "Simplify",
        "Improve tone",
        "Custom prompt",
    )
    query_row = {
        "paste_back": False,
        "context_ambient": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "context_clipboard": False,
        "file_access": "off",
        "tools": {},
    }
    rewrite_row = {**query_row, "paste_back": True}

    for prompt in prompts:
        # Empty user selection and empty optional context remain a valid,
        # bounded request instead of crashing before the route boundary.
        native = FakeWorker({"native.context.snapshot": context_handler(selected="", clipboard="")})
        brain = FakeWorker(stream_handlers={"brain.query": query_stream("answer")})
        with caller_config([query_row]):
            flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
            flow.intent_chosen(prompt)
        sent = brain.last_call("brain.query")["params"]
        assert sent["intent_prompt"] == prompt
        assert sent["selected"] == ""
        assert ui.last_call("ui.overlay.state")["params"]["state"] == "idle"

        for fault in (
            RuntimeError("configured route fails"),
            ConnectionError("network request fails"),
        ):
            def fail(_params: dict[str, Any], _on_event, fault=fault):
                raise fault

            brain = FakeWorker(stream_handlers={"brain.query": fail})
            with caller_config([query_row]):
                flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
                flow.intent_chosen(prompt)
            assert str(fault) in ui.last_call("ui.reply.notice")["params"]["text"]
            assert ui.last_call("ui.overlay.state")["params"]["state"] == "idle"

        with caller_config([query_row]):
            flow, _native, ui, brain, _audio = make_flow(native=native)
            flow.begin_caller(0)
            ui.emit("ui.intent.cancelled", {})
        assert flow._pending is None
        assert not brain.calls_for("brain.query")

        def render_failure(_params: dict[str, Any]):
            raise RuntimeError("result cannot be rendered")

        ui = FakeWorker({"ui.reply.chunk": render_failure})
        brain = FakeWorker(stream_handlers={"brain.query": query_stream("rendered answer")})
        with caller_config([query_row]):
            flow, _native, ui, _brain, _audio = make_flow(native=native, ui=ui, brain=brain)
            flow.intent_chosen(prompt)
        assert ui.last_call("ui.overlay.state")["params"]["state"] == "idle"

        paste_native = FakeWorker(
            {
                "native.context.snapshot": context_handler(selected="rewrite me", pid=77, focus_token=8),
                "native.paste_text": lambda _params: {
                    "ok": False,
                    "clipboard_ok": False,
                    "error": "result cannot be pasted into target application",
                },
            }
        )
        brain = FakeWorker(
            stream_handlers={"brain.rewrite": rewrite_stream("replacement", "Replacement pasted.")}
        )
        with caller_config([rewrite_row]):
            flow, paste_native, _ui, _brain, _audio = make_flow(native=paste_native, brain=brain)
            flow.intent_chosen(prompt)
        notification = paste_native.last_call("native.notify")["params"]
        assert "rewrite failed" in notification["title"].lower()


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
            "native.context.snapshot": context_handler(selected="bad grammar", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.rewrite": rewrite_stream("good grammar", "Fixed the grammar.")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 1})
        annotation = ui.last_call("ui.rewrite.annotation.show")["params"]
        ui.emit(
            "ui.rewrite.annotation.submitted",
            {
                "annotation_id": annotation["annotation_id"],
                "comment": "Fix grammar",
                "include_document": False,
            },
        )

    # The paste-back caller asked the native worker to capture the focused element.
    snap = native.calls_for("native.context.snapshot")[0]["params"]
    assert snap["capture_focus"] is True
    rewrite = brain.last_call("brain.rewrite")["params"]
    assert rewrite["selected_text"] == "bad grammar"
    assert not native.calls_for("native.paste_text")
    assert ui.last_call("ui.rewrite.annotation.proposal")["params"]["replacement_text"] == "good grammar"

    ui.emit("ui.rewrite.annotation.accepted", {"annotation_id": annotation["annotation_id"]})

    paste = native.last_call("native.paste_text")["params"]
    assert paste["text"] == "good grammar"
    assert paste["target_pid"] == 777
    # ...and the captured token is forwarded so paste_text can do the AX write.
    assert paste["focus_token"] == 9
    assert paste["restore_clipboard"] is True
    assert ui.last_call("ui.rewrite.annotation.remove")["params"] == {
        "annotation_id": annotation["annotation_id"]
    }
    assert annotation["annotation_id"] not in _flow._rewrite_annotations
    assert not ui.calls_for("ui.reply.notice"), "rewrite status must not go in the bubble"
    assert not native.calls_for("native.notify"), "successful paste should not notify"


def test_windows_rewrite_without_exact_range_is_copy_only() -> None:
    """A source HWND must never become permission to paste at a later caret."""
    native = FakeWorker({
        "native.context.snapshot": lambda _params: {
            "platform": "win32",
            "selected_text": "bad grammar",
            "active_app": {
                "name": "Editor",
                "process_name": "editor.exe",
                "pid": 777,
                "window_id": 888,
            },
            "focus_token": 0,
        },
        "native.clipboard.set": lambda _params: {"ok": True},
    })
    brain = FakeWorker(stream_handlers={"brain.rewrite": rewrite_stream("good grammar")})

    with caller_config([{"paste_back": True, "context_clipboard": False}]):
        flow, native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        annotation = ui.last_call("ui.rewrite.annotation.show")["params"]
        ui.emit(
            "ui.rewrite.annotation.submitted",
            {
                "annotation_id": annotation["annotation_id"],
                "comment": "Fix grammar",
                "include_document": False,
            },
        )

    proposal = ui.last_call("ui.rewrite.annotation.proposal")["params"]
    assert proposal["copy_only"] is True
    ui.emit("ui.rewrite.annotation.accepted", {"annotation_id": annotation["annotation_id"]})
    assert not native.calls_for("native.paste_text")
    assert native.last_call("native.clipboard.set")["params"]["text"] == "good grammar"
    assert annotation["annotation_id"] not in flow._rewrite_annotations


def test_duplicate_rewrite_submit_while_processing_starts_one_model_call() -> None:
    started = threading.Event()
    finish = threading.Event()

    def blocked_rewrite(_params, _on_event):
        started.set()
        assert finish.wait(5)
        return {"text": "good grammar"}

    native = FakeWorker({
        "native.context.snapshot": context_handler(
            selected="bad grammar",
            pid=777,
            focus_token=9,
        ),
    })
    brain = FakeWorker(stream_handlers={"brain.rewrite": blocked_rewrite})

    with caller_config([{"paste_back": True, "context_clipboard": False}]):
        flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        annotation = ui.last_call("ui.rewrite.annotation.show")["params"]
        first = threading.Thread(
            target=flow.submit_rewrite_annotation,
            args=(annotation["annotation_id"], "Fix grammar", False),
        )
        first.start()
        assert started.wait(5)
        flow.submit_rewrite_annotation(
            annotation["annotation_id"],
            "Fix grammar",
            False,
        )
        finish.set()
        first.join(5)

    assert not first.is_alive()
    assert len(brain.calls_for("brain.rewrite")) == 1


def test_duplicate_rewrite_accept_applies_once() -> None:
    started = threading.Event()
    finish = threading.Event()

    def blocked_paste(_params):
        started.set()
        assert finish.wait(5)
        return {"ok": True}

    native = FakeWorker({
        "native.context.snapshot": context_handler(
            selected="bad grammar",
            pid=777,
            focus_token=9,
        ),
        "native.paste_text": blocked_paste,
    })
    brain = FakeWorker(stream_handlers={"brain.rewrite": rewrite_stream("good grammar")})

    with caller_config([{"paste_back": True, "context_clipboard": False}]):
        flow, native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        annotation = ui.last_call("ui.rewrite.annotation.show")["params"]
        flow.submit_rewrite_annotation(annotation["annotation_id"], "Fix grammar", False)
        first = threading.Thread(
            target=flow.accept_rewrite_annotation,
            args=(annotation["annotation_id"],),
        )
        first.start()
        assert started.wait(5)
        flow.accept_rewrite_annotation(annotation["annotation_id"])
        finish.set()
        first.join(5)

    assert not first.is_alive()
    assert len(native.calls_for("native.paste_text")) == 1


def test_rewrite_reuses_number_after_previous_proposal_finishes():
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(
                selected="rough sentence",
                pid=777,
                focus_token=9,
            ),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    brain = FakeWorker(
        stream_handlers={"brain.rewrite": rewrite_stream("clear sentence", "Rewritten.")}
    )

    with caller_config([{"paste_back": True, "context_clipboard": False}]):
        flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        first = ui.last_call("ui.rewrite.annotation.show")["params"]
        ui.emit(
            "ui.rewrite.annotation.submitted",
            {
                "annotation_id": first["annotation_id"],
                "comment": "Make it clear",
                "include_document": False,
            },
        )
        assert flow._rewrite_annotations[first["annotation_id"]].state == "proposal"

        flow.begin_caller(0)
        second = ui.last_call("ui.rewrite.annotation.show")["params"]

    assert first["display_number"] == 1
    assert second["display_number"] == 1


def test_rewrite_hold_defers_calls_and_send_all_keeps_app_conversations_separate():
    snapshots = iter(
        (
            {
                "platform": "win32",
                "selected_text": "rough one",
                "active_app": {
                    "name": "Document - WordPad",
                    "process_name": "wordpad.exe",
                    "pid": 101,
                    "window_id": 1001,
                },
                "focus_token": 11,
            },
            {
                "platform": "win32",
                "selected_text": "rough two",
                "active_app": {
                    "name": "Notes - Notepad",
                    "process_name": "notepad.exe",
                    "pid": 202,
                    "window_id": 2002,
                },
                "focus_token": 22,
            },
        )
    )
    native = FakeWorker({"native.context.snapshot": lambda _params: next(snapshots)})

    def rewrite(params: dict[str, Any], _on_event) -> dict[str, Any]:
        return {"text": str(params["selected_text"]).replace("rough", "clear")}

    brain = FakeWorker(stream_handlers={"brain.rewrite": rewrite})
    with caller_config([{"paste_back": True, "context_clipboard": False}]):
        flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        first = ui.last_call("ui.rewrite.annotation.show")["params"]
        assert first["display_number"] == 1
        ui.emit(
            "ui.rewrite.annotation.held",
            {
                "annotation_id": first["annotation_id"],
                "comment": "Fix the first selection",
                "include_document": False,
            },
        )
        assert not brain.calls_for("brain.rewrite")
        assert ui.last_call("ui.rewrite.held_count")["params"]["count"] == 1

        flow.begin_caller(0)
        second = ui.last_call("ui.rewrite.annotation.show")["params"]
        assert second["display_number"] == 2
        ui.emit(
            "ui.rewrite.annotation.held",
            {
                "annotation_id": second["annotation_id"],
                "comment": "Fix the second selection",
                "include_document": False,
            },
        )
        assert not brain.calls_for("brain.rewrite")
        assert ui.last_call("ui.rewrite.held_count")["params"]["count"] == 2

        ui.emit("ui.rewrite.send_all", {})

    calls = brain.calls_for("brain.rewrite")
    assert [call["params"]["selected_text"] for call in calls] == ["rough one", "rough two"]
    assert len(flow._rewrite_app_sessions) == 2
    assert ui.last_call("ui.rewrite.held_count")["params"]["count"] == 0
    processing_ids = {
        call["params"]["annotation_id"]
        for call in ui.calls_for("ui.rewrite.annotation.processing")
    }
    assert processing_ids == {first["annotation_id"], second["annotation_id"]}
    assert len(ui.calls_for("ui.rewrite.annotation.proposal")) == 2


def test_rewrite_popup_receives_native_selection_rectangle() -> None:
    selection_rect = {"left": 320.0, "top": 240.0, "width": 140.0, "height": 22.0}
    native = FakeWorker({
        "native.context.snapshot": lambda _params: {
            "platform": "win32",
            "selected_text": "selected words",
            "active_app": {
                "name": "Notes",
                "process_name": "notepad.exe",
                "pid": 42,
                "window_id": 777,
            },
            "focus_token": 9,
            "selection_rect": selection_rect,
        },
    })

    with caller_config([{"paste_back": True, "context_clipboard": False}]):
        flow, _native, ui, _brain, _audio = make_flow(native=native)
        flow.begin_caller(0)

    shown = ui.last_call("ui.rewrite.annotation.show")["params"]
    assert shown["selection_rect"] == selection_rect
    assert not ui.calls_for("ui.show_intent")


def test_rewrite_popup_refreshes_exact_anchor_after_document_scroll() -> None:
    initial = {"left": 320.0, "top": 240.0, "width": 140.0, "height": 22.0}
    moved = {"left": 320.0, "top": 140.0, "width": 140.0, "height": 22.0}

    def anchor(params):
        rect = moved if params.get("refresh") else initial
        return {
            "ok": True,
            "visible": True,
            "source": "uia",
            "selection_rect": rect,
        }

    native = FakeWorker({
        "native.context.snapshot": lambda _params: {
            "platform": "win32",
            "selected_text": "selected words",
            "active_app": {
                "name": "Notes",
                "process_name": "notepad.exe",
                "pid": 42,
                "window_id": 777,
            },
            "focus_token": 9,
            "selection_rect": initial,
        },
        "native.selection.anchor.resolve": anchor,
    })

    with caller_config([{"paste_back": True, "context_clipboard": False}]):
        flow, _native, ui, _brain, _audio = make_flow(native=native)
        flow.begin_caller(0)
        annotation = ui.last_call("ui.rewrite.annotation.show")["params"]
        flow.refresh_rewrite_annotation_anchor(annotation["annotation_id"])

    updated = ui.last_call("ui.rewrite.annotation.anchor")["params"]
    assert updated["selection_rect"] == moved
    assert updated["visible"] is True
    refresh = native.last_call("native.selection.anchor.resolve")["params"]
    assert refresh["refresh"] is True
    assert refresh["allow_mouse"] is False


def test_rewrite_calc_range_uses_typed_verified_apply() -> None:
    active_app = {
        "name": "Budget.ods — LibreOffice Calc",
        "process_name": "soffice.bin",
        "pid": 42,
        "window_id": 777,
    }
    selection = {
        "app": "libreoffice_calc",
        "document_title": active_app["name"],
        "window_id": 777,
        "pid": 42,
        "range": "A1:B3",
        "values": (("Month", "Revenue"), ("Jan", "12"), ("Feb", "20")),
        "typed_values": (("Month", "Revenue"), ("Jan", 12.0), ("Feb", 20.0)),
        "formulas": (("", ""), ("", ""), ("", "=SUM(B2,8)")),
        "fingerprint": "rewrite-calc-fingerprint",
    }
    native = FakeWorker({
        "native.context.snapshot": lambda _params: {
            "platform": "win32",
            "active_app": active_app,
            "selected_text": "",
            "app_selection_deferred": True,
            "selection_rect": {"left": 400, "top": 300, "width": 2, "height": 20},
        },
        "native.action.calc.snapshot": lambda _params: {
            "ok": True,
            "selection": selection,
            "error": "",
        },
        "native.action.calc.apply": lambda params: {
            "ok": True,
            "result": {
                "plan_id": params["plan"]["plan_id"],
                "status": "applied",
                "message": "Applied and verified.",
            },
            "error": "",
        },
    })
    brain = FakeWorker(
        stream_handlers={
            "brain.rewrite": rewrite_stream("Month\tRevenue\nJanuary\t12\nFeb\t=SUM(B2,8)")
        }
    )

    with caller_config([{"paste_back": True, "context_clipboard": False}]):
        flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        annotation = ui.last_call("ui.rewrite.annotation.show")["params"]
        ui.emit(
            "ui.rewrite.annotation.submitted",
            {
                "annotation_id": annotation["annotation_id"],
                "comment": "Spell out the month",
                "include_document": False,
            },
        )

    rewrite = brain.last_call("brain.rewrite")["params"]
    assert rewrite["selected_text"] == "Month\tRevenue\nJan\t12.0\nFeb\t=SUM(B2,8)"
    assert "3-row by 2-column spreadsheet range" in rewrite["intent_prompt"]
    assert "[Exact LibreOffice Calc target:" in rewrite["rewrite_context"]
    assert not native.calls_for("native.paste_text")

    ui.emit("ui.rewrite.annotation.accepted", {"annotation_id": annotation["annotation_id"]})

    applied = native.last_call("native.action.calc.apply")["params"]
    assert applied["confirmed"] is True
    assert applied["plan"]["operations"][0]["type"] == "calc.clean_range@1"
    assert applied["plan"]["operations"][0]["args"]["changes"] == ({
        "row_offset": 1,
        "column_offset": 0,
        "before_kind": "value",
        "before_value": "Jan",
        "after_kind": "value",
        "after_value": "January",
        "replace_formula": False,
    },)
    assert not native.calls_for("native.paste_text")


def test_rewrite_excel_range_builds_a_typed_verified_plan(monkeypatch) -> None:
    from core.actions.adapters.excel import ExcelRuntimeProvider, ExcelSnapshot

    active_app = {
        "name": "Budget.xlsx — Excel",
        "process_name": "excel.exe",
        "pid": 55,
        "window_id": 888,
    }
    snapshot = ExcelSnapshot(
        workbook_name="Budget.xlsx",
        workbook_path=r"C:\docs\Budget.xlsx",
        worksheet_name="Sheet1",
        selection_address="$A$1:$B$2",
        row_count=2,
        column_count=2,
        values=(("Name", "Total"), ("Old", 3.0)),
        formulas=(("", ""), ("", "=1+2")),
        formula_capture_complete=True,
        selection_row=1,
        selection_column=1,
        table_names=(),
        chart_names=(),
        fingerprint="excel-rewrite-fingerprint",
    )
    monkeypatch.setattr(ExcelRuntimeProvider, "detects", staticmethod(lambda _context: True))
    monkeypatch.setattr(ExcelRuntimeProvider, "snapshot", lambda _self, _context: snapshot)
    native = FakeWorker({
        "native.context.snapshot": lambda _params: {
            "platform": "win32",
            "active_app": active_app,
            "selected_text": "",
            "selection_rect": {"left": 500, "top": 350, "width": 2, "height": 20},
        },
    })
    brain = FakeWorker(
        stream_handlers={"brain.rewrite": rewrite_stream("Name\tTotal\nNew\t=1+2")}
    )

    with caller_config([{"paste_back": True, "context_clipboard": False}]):
        flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        applied: dict[str, Any] = {}

        def apply(request: Any) -> bool:
            applied["plan"] = request.structured_plan
            return True

        monkeypatch.setattr(flow, "_apply_structured_rewrite", apply)
        flow.begin_caller(0)
        annotation = ui.last_call("ui.rewrite.annotation.show")["params"]
        ui.emit(
            "ui.rewrite.annotation.submitted",
            {
                "annotation_id": annotation["annotation_id"],
                "comment": "Rename Old to New",
                "include_document": False,
            },
        )
        ui.emit("ui.rewrite.annotation.accepted", {"annotation_id": annotation["annotation_id"]})

    rewrite = brain.last_call("brain.rewrite")["params"]
    assert rewrite["selected_text"] == "Name\tTotal\nOld\t=1+2"
    plan = applied["plan"]
    assert plan.app == "excel"
    assert plan.operations[0].type == "excel.clean_range@1"
    assert plan.operations[0].args["changes"][0]["after_value"] == "New"
    assert not native.calls_for("native.paste_text")


def test_rewrite_vscode_saved_selection_uses_exact_file_adapter() -> None:
    selected = "def add_one(value):\n    return value"
    replacement = "def add_one(value):\n    return value + 1"
    active_app = {
        "name": "demo.py - project - Visual Studio Code",
        "process_name": "Code.exe",
        "pid": 42,
        "window_id": 777,
    }
    snapshot = {
        "app": "vscode",
        "file_path": r"C:\project\demo.py",
        "display_name": "demo.py",
        "window_id": 777,
        "pid": 42,
        "text": f"# demo\n{selected}\n",
        "selected_text": selected,
        "selection_start": 7,
        "selection_end": 7 + len(selected),
        "fingerprint": "file-fingerprint",
        "selection_fingerprint": __import__("hashlib").sha256(selected.encode()).hexdigest(),
        "has_utf8_bom": False,
    }
    native = FakeWorker({
        "native.context.snapshot": lambda _params: {
            "platform": "win32",
            "active_app": active_app,
            "selected_text": selected,
            "selection_rect": {"left": 620, "top": 410, "width": 160, "height": 20},
        },
        "native.action.vscode.snapshot": lambda _params: {
            "ok": True,
            "snapshot": snapshot,
            "error": "",
        },
        "native.action.vscode.apply": lambda params: {
            "ok": True,
            "result": {
                "plan_id": params["plan"]["plan_id"],
                "status": "applied",
                "message": "Updated demo.py.",
            },
            "error": "",
        },
    })
    brain = FakeWorker(stream_handlers={"brain.rewrite": rewrite_stream(replacement)})

    with caller_config([{"paste_back": True, "context_clipboard": False}]):
        flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        annotation = ui.last_call("ui.rewrite.annotation.show")["params"]
        ui.emit(
            "ui.rewrite.annotation.submitted",
            {
                "annotation_id": annotation["annotation_id"],
                "comment": "Fix the bug",
                "include_document": False,
            },
        )
        ui.emit("ui.rewrite.annotation.accepted", {"annotation_id": annotation["annotation_id"]})

    rewrite = brain.last_call("brain.rewrite")["params"]
    assert "exact saved VS Code selection" in rewrite["intent_prompt"]
    assert "[Exact saved VS Code target: demo.py" in rewrite["rewrite_context"]
    applied = native.last_call("native.action.vscode.apply")["params"]
    assert applied["confirmed"] is True
    assert applied["plan"]["operations"][0]["type"] == "vscode.replace_selection@1"
    assert applied["plan"]["operations"][0]["args"]["replacement_text"] == replacement
    assert not native.calls_for("native.paste_text")


def test_rewrite_other_ide_uses_the_same_exact_saved_file_boundary() -> None:
    selected = "return old_value"
    replacement = "return new_value"
    active_app = {
        "name": "main.py – project – PyCharm",
        "process_name": "pycharm64.exe",
        "pid": 52,
        "window_id": 902,
    }
    snapshot = {
        "app": "vscode",
        "file_path": r"C:\project\main.py",
        "display_name": "main.py",
        "window_id": 902,
        "pid": 52,
        "text": f"def read():\n    {selected}\n",
        "selected_text": selected,
        "selection_start": 16,
        "selection_end": 16 + len(selected),
        "fingerprint": "pycharm-file-fingerprint",
        "selection_fingerprint": __import__("hashlib").sha256(selected.encode()).hexdigest(),
        "editor_name": "PyCharm",
    }
    native = FakeWorker({
        "native.context.snapshot": lambda _params: {
            "platform": "win32",
            "active_app": active_app,
            "selected_text": selected,
            "selection_rect": {"left": 600, "top": 400, "width": 140, "height": 20},
        },
        "native.action.vscode.snapshot": lambda _params: {
            "ok": True,
            "snapshot": snapshot,
            "error": "",
        },
        "native.action.vscode.apply": lambda params: {
            "ok": True,
            "result": {"status": "applied", "plan_id": params["plan"]["plan_id"]},
            "error": "",
        },
    })
    brain = FakeWorker(stream_handlers={"brain.rewrite": rewrite_stream(replacement)})

    with caller_config([{"paste_back": True, "context_clipboard": False}]):
        flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        annotation = ui.last_call("ui.rewrite.annotation.show")["params"]
        ui.emit(
            "ui.rewrite.annotation.submitted",
            {"annotation_id": annotation["annotation_id"], "comment": "Update this", "include_document": False},
        )
        ui.emit("ui.rewrite.annotation.accepted", {"annotation_id": annotation["annotation_id"]})

    rewrite = brain.last_call("brain.rewrite")["params"]
    assert "exact saved PyCharm selection" in rewrite["intent_prompt"]
    assert "[Exact saved PyCharm target:" in rewrite["rewrite_context"]
    assert native.last_call("native.action.vscode.apply")["params"]["confirmed"] is True
    assert not native.calls_for("native.paste_text")


def test_rewrite_undo_restores_original_text_in_original_app():
    """The bubble undo action must target the exact app and selection OpenWand edited."""
    rows = [
        {
            "paste_back": True,
            "context_ambient": False,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        },
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(
                selected="bad grammar",
                pid=777,
                focus_token=9,
            ),
            "native.paste_text": lambda _params: {"ok": True, "method": "uia-range"},
            "native.undo_edit": lambda _params: {"ok": True, "method": "app-undo"},
        }
    )
    brain = FakeWorker(
        stream_handlers={"brain.rewrite": rewrite_stream("good grammar", "Fixed the grammar.")}
    )
    with caller_config(rows):
        flow, native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        annotation = ui.last_call("ui.rewrite.annotation.show")["params"]
        ui.emit(
            "ui.rewrite.annotation.submitted",
            {
                "annotation_id": annotation["annotation_id"],
                "comment": "Fix grammar",
                "include_document": False,
            },
        )
        ui.emit("ui.rewrite.annotation.accepted", {"annotation_id": annotation["annotation_id"]})

        assert not ui.calls_for("ui.reply.undo_ready")
        assert flow._last_undoable_edit is not None

        ui.emit("ui.rewrite.undo", {})

    assert native.last_call("native.undo_edit")["params"] == {
        "target_pid": 777,
        "focus_token": 9,
        "original_text": "bad grammar",
        "replacement_text": "good grammar",
    }
    assert ui.last_call("ui.reply.notice")["params"]["text"] == "Last OpenWand edit undone."
    assert flow._last_undoable_edit is None


def test_rewrite_undo_reports_clipboard_fallback():
    """If the original app cannot be focused, the original remains recoverable."""
    rows = [{"paste_back": True, "context_ambient": False, "context_screenshot": "off"}]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="before", pid=321, focus_token=4),
            "native.paste_text": lambda _params: {"ok": True},
            "native.undo_edit": lambda _params: {
                "ok": False,
                "clipboard_ok": True,
                "method": "clipboard-fallback",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.rewrite": rewrite_stream("after")})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        ui.emit("ui.intent.chosen", {"custom": "Rewrite"})
        ui.emit("ui.rewrite.undo", {})

    notice = ui.last_call("ui.reply.notice")["params"]
    assert notice["text"] == "Couldn't safely undo in the app. Original text copied to clipboard."
    assert notice["severity"] == "warning"


def test_rewrite_flow_includes_app_context_as_source():
    """Verify paste-back rewrite can use App context without changing the paste target."""
    rows = [
        {
            "paste_back": True,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        },
    ]

    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_text": "notepad text",
            "active_app": {"name": "Notepad", "pid": 777, "window_id": 777},
            "document_window": {
                "process_name": "Code",
                "title": "demo.py",
                "pid": 123,
                "window_id": 123,
            },
            "focus_token": 9,
        }

    def active_document(params: dict[str, Any]) -> dict[str, Any]:
        active_window = params["active_window"]
        assert active_window["process_name"] == "Code"
        assert active_window["title"] == "demo.py"
        return {"text": "VS Code paragraph"}

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot,
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": active_document},
        stream_handlers={"brain.rewrite": query_stream("VS Code paragraph")},
    )
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        annotation = ui.last_call("ui.rewrite.annotation.show")["params"]
        ui.emit(
            "ui.rewrite.annotation.submitted",
            {
                "annotation_id": annotation["annotation_id"],
                "comment": "Replace with the one from VS Code",
                "include_document": True,
            },
        )

    rewrite = brain.last_call("brain.rewrite")["params"]
    assert rewrite["selected_text"] == "notepad text"
    assert "[Active document]" in rewrite["rewrite_context"]
    assert "VS Code paragraph" in rewrite["rewrite_context"]
    assert not native.calls_for("native.paste_text")

    ui.emit("ui.rewrite.annotation.accepted", {"annotation_id": annotation["annotation_id"]})

    paste = native.last_call("native.paste_text")["params"]
    assert paste["target_pid"] == 777
    assert paste["text"] == "VS Code paragraph"


def test_rewrite_context_excludes_target_document_when_other_sources_exist():
    """Verify custom paste-back prompts do not use the target app as source."""
    rows = [
        {
            "paste_back": True,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        },
    ]

    def snapshot(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_text": "Yeah, no worries.\nLet's keep it.",
            "active_app": {"name": "Notepad", "pid": 777, "window_id": 777},
            "focus_token": 9,
        }

    def active_document(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "text": (
                "[Yeah, no worries. Let's just keep i]\n"
                "Yeah, no worries.\nLet's keep it.\n\n"
                "[This situation requires immediate attent - Untitled-1]\n"
                "This situation requires immediate attention."
            ),
            "debug": {
                "window_labels": [
                    "Yeah, no worries. Let's just keep i",
                    "This situation requires immediate attent - Untitled-1",
                ]
            },
        }

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot,
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": active_document},
        stream_handlers={"brain.rewrite": query_stream("This situation requires immediate attention.")},
    )
    with caller_config(rows):
        _flow, _native, _ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        _ui.emit(
            "ui.intent.chosen",
            {"custom": "Replace the selected text from the content of VS Code"},
        )

    rewrite = brain.last_call("brain.rewrite")["params"]
    assert "Yeah, no worries.\nLet's keep it." not in rewrite["rewrite_context"]
    assert "This situation requires immediate attention." in rewrite["rewrite_context"]


def test_paste_back_file_prompt_routes_to_local_file_tools():
    """Verify paste-back callers can edit files instead of clipboard-pasting."""
    rows = [
        {
            "paste_back": True,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
            "file_access": "off",
        },
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="selected text", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("edited file")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "Edit local file notes.md to say hi"})

    assert not brain.calls_for("brain.rewrite")
    assert not native.calls_for("native.paste_text")
    params = brain.last_call("brain.query")["params"]
    assert params["file_access_mode"] == "ask"
    assert set(params["allowed_tools"]) >= {"list_files", "read_file", "create_file", "edit_file", "write_file"}
    assert set(params["pinned_tools"]) >= {"list_files", "read_file", "create_file", "edit_file", "write_file"}
    assert ui.calls_for("ui.chat.add_conversation")


def test_rewrite_does_not_treat_clipboard_only_paste_as_success():
    """Verify rewrite reports failure when selected text was not replaced."""
    rows = [
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
            "native.paste_text": lambda _params: {
                "ok": False,
                "clipboard_ok": True,
                "clipboard_restored": True,
                "confirmed": False,
                "app_name": "TextEdit",
                "frontmost_pid": 999,
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.rewrite": query_stream("good grammar")})
    with caller_config(rows):
        _flow, native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        ui.emit("ui.intent.chosen", {"custom": "Fix grammar"})

    assert not ui.calls_for("ui.reply.notice"), "fallback status must not go in the bubble"
    paste = native.last_call("native.paste_text")["params"]
    assert paste["restore_clipboard"] is True
    notify = native.last_call("native.notify")["params"]
    assert "replace the selected text" in notify["message"]


def test_rewrite_warns_when_paste_succeeds_but_clipboard_restore_fails():
    """A successful replacement must not silently discard the prior clipboard."""
    rows = [
        {
            "paste_back": True,
            "context_ambient": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="before", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {
                "ok": True,
                "clipboard_ok": True,
                "clipboard_restored": False,
                "method": "uia-range",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.rewrite": rewrite_stream("after")})
    with caller_config(rows):
        _flow, native, _ui, _brain, _audio = make_flow(native=native, brain=brain)
        _ui.emit("ui.intent.chosen", {"custom": "Rewrite"})

    notify = native.last_call("native.notify")["params"]
    assert notify["title"] == "OpenWand pasted the rewrite"
    assert "couldn't restore your previous clipboard" in notify["message"]


def test_rewrite_failure_reports_notice_and_returns_idle():
    rows = [
        {
            "paste_back": True,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        },
    ]

    def fail_rewrite(_params: dict[str, Any], _on_event) -> dict[str, Any]:
        raise RuntimeError("ValueError: LLM route uses 'google', but its API key is not configured.")

    native = FakeWorker({"native.context.snapshot": context_handler(selected="bad grammar")})
    brain = FakeWorker(stream_handlers={"brain.rewrite": fail_rewrite})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        ui.emit("ui.intent.chosen", {"custom": "Fix grammar"})

    assert ui.last_call("ui.reply.notice")["params"]["text"] == (
        "Rewrite failed: LLM route uses 'google', but its API key is not configured.\n\n"
        "Recommendation: add or refresh the provider API key in Settings, then run Setup Check."
    )
    assert ui.calls_for("ui.reply.done")
    assert ui.last_call("ui.overlay.state")["params"]["state"] == "idle"


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
    chat_params = ui.last_call("ui.chat.add_conversation")["params"]
    assert chat_params["user"] == "What is in this image?"
    assert chat_params["assistant"] == "vision reply"
    assert chat_params["image_base64"] == query["screenshot_b64"]


def test_snip_region_uses_snip_context_without_extra_screenshot_tool():
    """Verify snip has caller-style context while keeping screenshot context off."""
    image_bytes = b"snip image"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)

    rows = [
        {
            "paste_back": True,
            "context_ambient": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "model",
            "context_clipboard": False,
        }
    ]
    snip = {
        "paste_back": False,
        "context_ambient": True,
        "context_clipboard": True,
        "context_documents_mode": "off",
        "context_browser_mode": "model",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "file_access": "off",
        "tools": {},
    }
    native = FakeWorker(
        {
            "native.capture.region": lambda _params: {"ok": True, "path": str(image_path)},
            "native.context.snapshot": context_handler(selected="", clipboard="clip text"),
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("snip reply")})
    try:
        with caller_config(rows), snip_config(snip):
            _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
            ui.emit("ui.snip.region", {"x": 1, "y": 2, "width": 3, "height": 4})
            ui.emit("ui.intent.chosen", {"custom": "What is this?"})
    finally:
        image_path.unlink(missing_ok=True)

    query = brain.last_call("brain.query")["params"]
    assert query["screenshot_b64"] == base64.b64encode(image_bytes).decode("ascii")
    assert query["allow_screenshot_tool"] is False
    assert query["use_tools"] is True
    assert "web_search" in query["allowed_tools"]
    assert "[Clipboard]\nclip text" in query["ambient_text"]


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
        _flow, native, ui, brain, audio = make_flow(native=native, audio=audio, brain=brain)
        native.emit("native.hotkey", {"kind": "voice_start"})
        native.emit("native.hotkey", {"kind": "voice_stop"})

    assert audio.calls_for("audio.record.start")
    assert audio.calls_for("audio.record.stop_transcribe")
    assert ui.last_call("ui.reply.transcript")["params"]["text"] == "voice prompt"
    assert brain.last_call("brain.query")["params"]["intent_prompt"] == "voice prompt"
    chat_params = ui.last_call("ui.chat.add_conversation")["params"]
    assert chat_params["user"] == "voice prompt"
    assert chat_params["assistant"] == "voice reply"


def test_voice_review_transcript_opens_intent_overlay_before_query():
    """F9 can review the transcript and per-request context before querying."""
    voice_row = {
        "label": "Voice",
        "paste_back": False,
        "context_ambient": False,
        "context_clipboard": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "on",
        "context_screenshot": "off",
        "tools": {},
    }
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "voice prompt"}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("voice reply")})
    with voice_config(voice_row):
        config.VOICE_REVIEW_TRANSCRIPT = True
        _flow, native, ui, brain, audio = make_flow(native=native, audio=audio, brain=brain)
        native.emit("native.hotkey", {"kind": "voice_start"})
        native.emit("native.hotkey", {"kind": "voice_stop"})

        assert audio.calls_for("audio.record.stop_transcribe")
        assert not brain.calls_for("brain.query")
        show = ui.last_call("ui.show_intent")["params"]
        assert show["initial_custom_text"] == "voice prompt"
        assert show["focus_overlay"] is True
        assert any(item["id"] == "memory" and item["state"] == "on" for item in show["context_items"])

        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "voice prompt",
                "context_choices": [{"id": "memory", "state": "off", "default_state": "on", "touched": True}],
            },
        )

    query = brain.last_call("brain.query")["params"]
    assert query["intent_prompt"] == "voice prompt"
    assert query["memory_enabled"] is False


def test_voice_start_starts_recording_before_context_capture():
    audio = FakeWorker()
    native = FakeWorker()

    def snapshot(_params):
        assert audio.calls_for("audio.record.start")
        return context_handler(selected="")(_params)

    native.handlers["native.context.snapshot"] = snapshot

    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)
    native.emit("native.hotkey", {"kind": "voice_start"})

    assert audio.calls_for("audio.record.start")
    assert ui.last_call("ui.reply.listening")["params"] == {}
    assert ui.calls_for("ui.overlay.state")[0]["params"]["state"] == "listening"


def test_voice_start_does_not_show_recording_bubble_when_recording_fails():
    """Verify recording bubble only appears after the recorder actually starts."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})

    def fail_start(_params):
        """Simulate recorder startup failure."""
        raise RuntimeError("mic unavailable")

    audio = FakeWorker({"audio.record.start": fail_start})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "voice_start"})

    assert audio.calls_for("audio.record.start")
    assert not ui.calls_for("ui.reply.listening")
    assert ui.calls_for("ui.reply.notice")


def test_voice_start_does_not_show_recording_bubble_when_recorder_reports_false():
    """Verify explicit non-recording result stays quiet."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.start": lambda _params: {"recording": False}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "voice_start"})

    assert audio.calls_for("audio.record.start")
    assert not ui.calls_for("ui.reply.listening")


def test_voice_start_key_repeat_is_ignored_until_release():
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": ""}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_stop"})

    assert len(audio.calls_for("audio.record.start")) == 1
    assert len(ui.calls_for("ui.reply.listening")) == 1
    assert len(audio.calls_for("audio.record.stop_transcribe")) == 1


def test_rapid_voice_and_dictation_events_never_mix_recording_owners():
    """Whichever hold-to-talk action starts first exclusively owns the mic."""

    # Voice owns the first recording. Repeated dictation presses and their
    # release are unrelated input and must not start or stop that recording.
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "voice prompt"}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("voice reply")})
    _flow, native, _ui, brain, audio = make_flow(native=native, audio=audio, brain=brain)

    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_stop"})

    assert len(audio.calls_for("audio.record.start")) == 1
    assert not audio.calls_for("audio.record.stop_transcribe")
    native.emit("native.hotkey", {"kind": "voice_stop"})
    assert len(audio.calls_for("audio.record.stop_transcribe")) == 1
    assert brain.last_call("brain.query")["params"]["intent_prompt"] == "voice prompt"
    assert not native.calls_for("native.paste_text")

    # Dictation owns the next recording. Voice repeats and the stray voice
    # release must not stop it or turn its transcript into an assistant query.
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="", pid=888, focus_token=10),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "dictated text"}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("should not run")})
    _flow, native, _ui, brain, audio = make_flow(native=native, audio=audio, brain=brain)

    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_stop"})

    assert len(audio.calls_for("audio.record.start")) == 1
    assert not audio.calls_for("audio.record.stop_transcribe")
    native.emit("native.hotkey", {"kind": "dictate_stop"})
    assert len(audio.calls_for("audio.record.stop_transcribe")) == 1
    assert native.last_call("native.paste_text")["params"] == {
        "text": "dictated text",
        "target_pid": 888,
        "focus_token": 10,
    }
    assert not brain.calls_for("brain.query")


def test_voice_start_failure_ignores_key_repeat_until_release():
    """Verify failed voice start does not hammer the microphone on key repeat."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.start": lambda _params: {"recording": False, "error": "mic unavailable"}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_stop"})
    native.emit("native.hotkey", {"kind": "voice_start"})

    assert len(audio.calls_for("audio.record.start")) == 2
    assert not audio.calls_for("audio.record.stop_transcribe")
    assert ui.calls_for("ui.reply.notice")


def test_voice_stop_leaves_recording_bubble_before_transcribing():
    ui = FakeWorker()

    def transcribe(_params):
        assert ui.last_call("ui.overlay.state")["params"]["state"] == "thinking"
        assert ui.calls_for("ui.reply.thinking")
        return {"text": ""}

    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.stop_transcribe": transcribe})
    _flow, native, ui, _brain, audio = make_flow(native=native, ui=ui, audio=audio)

    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_stop"})

    assert audio.calls_for("audio.record.stop_transcribe")
    # An empty transcript now surfaces a "didn't catch that" notice instead of a
    # silent reset, so a too-short F8 tap gives the user feedback.
    notices = ui.calls_for("ui.reply.notice")
    assert notices
    assert "Didn't catch any speech" in notices[-1]["params"]["text"]
    assert ui.last_call("ui.overlay.state")["params"]["state"] == "idle"


def test_voice_flow_uses_voice_caller_config():
    voice_row = {
        "label": "Voice",
        "paste_back": False,
        "context_ambient": True,
        "context_clipboard": False,
        "context_documents_mode": "off",
        "context_browser_mode": "model",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "tools": {"alpha": "on", "beta": "model"},
    }
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "voice prompt"}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("voice reply")})
    with voice_config(voice_row):
        _flow, native, _ui, brain, audio = make_flow(native=native, audio=audio, brain=brain)
        native.emit("native.hotkey", {"kind": "voice_start"})
        native.emit("native.hotkey", {"kind": "voice_stop"})

    query = brain.last_call("brain.query")["params"]
    assert query["use_tools"] is True
    assert set(query["allowed_tools"]) == {"web_search", "get_context.browser", "retrieve_website", "alpha", "beta"}
    assert query["pinned_tools"] == ["web_search", "get_context", "retrieve_website", "alpha"]
    assert query["memory_enabled"] is False
    # The record-start path must not wait on the slow browser page fetch.
    snapshot = native.calls_for("native.context.snapshot")[0]["params"]
    assert snapshot["include_browser_content"] is False


def test_dictation_shows_recording_ui_after_recording_starts():
    """Verify dictation shows recording UI only after the recorder starts."""
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "hello there"}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_stop"})

    snapshot = native.calls_for("native.context.snapshot")[0]["params"]
    paste = native.last_call("native.paste_text")["params"]
    assert snapshot["capture_focus"] is True
    assert paste["text"] == "hello there"
    assert paste["target_pid"] == 777
    assert paste["focus_token"] == 9
    assert audio.calls_for("audio.record.start")
    assert audio.calls_for("audio.record.stop_transcribe")
    assert ui.calls_for("ui.overlay.state")[0]["params"]["state"] == "listening"
    assert ui.calls_for("ui.reply.listening")
    assert ui.calls_for("ui.reply.reset")
    assert [call["params"]["state"] for call in ui.calls_for("ui.overlay.state")] == [
        "listening",
        "idle",
    ]


@pytest.mark.parametrize(
    ("mode", "expected_text", "expects_cleanup"),
    (("raw", "raw spoken words", False), ("llm", "Cleaned spoken words.", True)),
)
def test_dictation_raw_and_llm_cleanup_modes_reach_the_same_paste_target(
    monkeypatch,
    mode,
    expected_text,
    expects_cleanup,
):
    """The setting changes transcript cleanup, never the captured paste target."""

    monkeypatch.setattr(config, "DICTATE_MODE", mode, raising=False)
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    audio = FakeWorker(
        {"audio.record.stop_transcribe": lambda _params: {"text": "raw spoken words"}}
    )
    brain = FakeWorker(
        stream_handlers={"brain.rewrite": rewrite_stream("Cleaned spoken words.")}
    )
    _flow, native, _ui, brain, _audio = make_flow(native=native, audio=audio, brain=brain)

    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_stop"})

    assert bool(brain.calls_for("brain.rewrite")) is expects_cleanup
    assert native.last_call("native.paste_text")["params"] == {
        "text": expected_text,
        "target_pid": 777,
        "focus_token": 9,
    }


def test_dictation_does_not_show_recording_ui_when_recording_fails():
    """Verify failed dictation start does not claim recording."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="", pid=777, focus_token=9)})

    def fail_start(_params):
        """Simulate recorder startup failure."""
        raise RuntimeError("mic unavailable")

    audio = FakeWorker({"audio.record.start": fail_start})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "dictate_start"})

    assert audio.calls_for("audio.record.start")
    assert not ui.calls_for("ui.reply.listening")
    assert not any(call["params"].get("state") == "listening" for call in ui.calls_for("ui.overlay.state"))
    assert ui.calls_for("ui.reply.notice")


def test_dictation_does_not_show_recording_ui_when_recorder_reports_false():
    """Verify explicit dictation non-recording result stays quiet."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="", pid=777, focus_token=9)})
    audio = FakeWorker({"audio.record.start": lambda _params: {"recording": False}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "dictate_start"})

    assert audio.calls_for("audio.record.start")
    assert not ui.calls_for("ui.reply.listening")
    assert not any(call["params"].get("state") == "listening" for call in ui.calls_for("ui.overlay.state"))


def test_dictation_start_failure_ignores_key_repeat_until_release():
    """Verify failed dictation start does not hammer the microphone on key repeat."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="", pid=777, focus_token=9)})
    audio = FakeWorker({"audio.record.start": lambda _params: {"recording": False, "error": "mic unavailable"}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_stop"})
    native.emit("native.hotkey", {"kind": "dictate_start"})

    assert len(audio.calls_for("audio.record.start")) == 2
    assert not audio.calls_for("audio.record.stop_transcribe")
    assert ui.calls_for("ui.reply.notice")


@pytest.mark.workflow
def test_voice_transcript_confirmation_uses_accepted_candidate(monkeypatch):
    """Voice confirmation edits the transcript before the assistant query fires."""
    monkeypatch.setattr(config, "VOICE_TRANSCRIPT_CONFIRM", True, raising=False)
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "raw voice prompt"}})
    ui = FakeWorker({"ui.voice.candidates": lambda params: {"accepted": True, "text": "edited voice prompt"}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("reply")})
    with voice_config(
        {
            "context_ambient": False,
            "context_browser_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
            "tools": {},
        }
    ):
        _flow, _native, _ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain, audio=audio)
        native.emit("native.hotkey", {"kind": "voice_start"})
        native.emit("native.hotkey", {"kind": "voice_stop"})

    candidate_call = ui.last_call("ui.voice.candidates")["params"]
    assert candidate_call["candidates"][0] == "raw voice prompt"
    assert brain.last_call("brain.query")["params"]["intent_prompt"] == "edited voice prompt"


@pytest.mark.workflow
def test_dictation_transcript_confirmation_cancel_skips_paste(monkeypatch):
    """Cancelling dictation confirmation leaves the focused field unchanged."""
    monkeypatch.setattr(config, "VOICE_TRANSCRIPT_CONFIRM", True, raising=False)
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "raw dictation"}})
    ui = FakeWorker({"ui.voice.candidates": lambda _params: {"accepted": False, "text": ""}})
    _flow, native, _ui, _brain, _audio = make_flow(native=native, ui=ui, audio=audio)

    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_stop"})

    assert ui.calls_for("ui.voice.candidates")
    assert not native.calls_for("native.paste_text")


def test_caller_tool_overrides_reach_brain_query():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
            "tools": {"my_tool": "on", "other_tool": "model"},
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="picked")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("tool reply")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "run it"})

    query = brain.last_call("brain.query")["params"]
    assert set(query["allowed_tools"]) == {"my_tool", "other_tool"}
    assert query["pinned_tools"] == ["my_tool"]
    assert query["use_tools"] is True


@pytest.mark.workflow
def test_query_privacy_report_surfaces_redacted_summary_badge_and_report():
    """A brain privacy report becomes a visible badge plus report payload."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
            "tools": {},
        }
    ]

    report = {
        "count": 2,
        "categories": {"email": 1, "api_key": 1},
        "items": [
            {"category": "email", "source": "Selection", "preview": "[email redacted]"},
            {"category": "api_key", "source": "Clipboard", "preview": "[api key redacted]"},
        ],
    }

    def query_with_privacy(_params: dict[str, Any], on_event) -> dict[str, Any]:
        on_event("reply.done", {"text": "ok", "privacy_report": report}, 1)
        return {"text": "ok", "privacy_report": report}

    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": query_with_privacy})
    with caller_config(rows):
        _flow, native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "go"})

    summaries = ui.calls_for("ui.context.summary")
    assert any(
        item.get("label") == "Privacy: 2 redacted"
        for call in summaries
        for item in call["params"].get("items", [])
    )
    privacy = ui.last_call("ui.privacy.report")["params"]
    assert privacy["report"]["count"] == 2
    assert privacy["report"]["items"][0]["preview"] == "[email redacted]"

    reviewed_report = dict(report, reviewed=True, decision="redacted")

    def query_after_review(_params: dict[str, Any], on_event) -> dict[str, Any]:
        on_event("reply.done", {"text": "ok", "privacy_report": reviewed_report}, 1)
        return {"text": "ok", "privacy_report": reviewed_report}

    reviewed_ui = FakeWorker()
    reviewed_native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    reviewed_brain = FakeWorker(stream_handlers={"brain.query": query_after_review})
    with caller_config(rows):
        _flow, reviewed_native, reviewed_ui, _brain, _audio = make_flow(
            native=reviewed_native,
            ui=reviewed_ui,
            brain=reviewed_brain,
        )
        reviewed_native.emit("native.hotkey", {"kind": "caller", "index": 0})
        reviewed_ui.emit("ui.intent.chosen", {"custom": "go"})

    assert reviewed_ui.calls_for("ui.privacy.report") == []
    assert reviewed_ui.calls_for("ui.context.summary") == []


@pytest.mark.workflow
def test_health_request_uses_fast_static_rows_and_warns(monkeypatch):
    """Health requests use the lightweight setup report instead of live probes."""
    import core.setup_check as setup_check

    static_rows = [
        {
            "name": "Config",
            "status": "warn",
            "message": "Static config check needs attention.",
            "recommendation": "",
        }
    ]
    monkeypatch.setattr(
        setup_check,
        "run_setup_check",
        lambda: static_rows,
    )

    ui = FakeWorker(
        {
            "ui.health.show": lambda _params: {"queued": True},
            "ui.reply.notice": lambda _params: {"queued": True},
        }
    )
    brain = FakeWorker({"brain.llm.test": lambda _params: {"ok": True, "message": "LLM OK"}})
    audio = FakeWorker({"audio.stt.is_ready": lambda _params: {"ready": False}})
    native = FakeWorker({"native.permissions.snapshot": lambda _params: {}})

    flow, _native, ui, _brain, _audio = make_flow(native=native, ui=ui, brain=brain, audio=audio)
    flow._last_privacy_report = {"count": 1, "categories": {"email": 1}}
    ui.emit("ui.health.requested", {})

    call = ui.last_call("ui.health.show")["params"]
    assert call["rows"] == static_rows
    assert call["title"] == "Setup check"
    assert len(ui.calls_for("ui.health.show")) == 1
    assert not brain.calls_for("brain.llm.test")
    assert not audio.calls_for("audio.stt.is_ready")
    assert not native.calls_for("native.permissions.snapshot")
    notice = ui.last_call("ui.reply.notice")["params"]
    assert "Health issue:" in notice["text"]
    assert notice["severity"] == "warning"


@pytest.mark.workflow
def test_settings_setup_check_uses_fast_static_rows(monkeypatch):
    """Settings setup check avoids live probes so the dialog appears promptly."""
    import core.setup_check as setup_check

    static_rows = [
        {
            "name": "Config",
            "status": "ok",
            "message": "Static config check passed.",
            "recommendation": "",
        }
    ]
    monkeypatch.setattr(setup_check, "run_setup_check", lambda: static_rows)

    ui = FakeWorker({"ui.health.show": lambda _params: {"queued": True}})
    brain = FakeWorker({"brain.llm.test": lambda _params: {"ok": True}})
    audio = FakeWorker({"audio.stt.is_ready": lambda _params: {"ready": False}})
    native = FakeWorker({"native.permissions.snapshot": lambda _params: {}})

    _flow, _native, ui, brain, audio = make_flow(native=native, ui=ui, brain=brain, audio=audio)
    ui.emit("ui.health.requested", {"source": "settings"})

    assert ui.last_call("ui.health.show")["params"]["rows"] == static_rows
    assert ui.last_call("ui.health.show")["params"]["title"] == "Setup check"
    assert not brain.calls_for("brain.llm.test")
    assert not audio.calls_for("audio.stt.is_ready")


def test_off_tool_override_beats_context_dropdown():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "model",
            "context_browser_mode": "model",
            "context_github_mode": "auto",
            "context_memory_mode": "off",
            "context_tools": True,
            "context_screenshot": "model",
            "context_clipboard": False,
            # web_search, get_context, and retrieve_website forced off despite browser/docs
            # granting them; git_status forced off drops it from the
            # frontload list; capture_screen off kills the screenshot tool.
            "tools": {
                "web_search": "off",
                "get_context": "off",
                "retrieve_website": "off",
                "git_status": "off",
                "capture_screen": "off",
            },
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="picked")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("reply")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "go"})

    query = brain.last_call("brain.query")["params"]
    assert query["allowed_tools"] == []
    assert query["use_tools"] is False
    assert query["frontload_tools"] == ["git_diff"]
    assert query["allow_screenshot_tool"] is False
    # capture_screen off also means no pre-captured screenshot for the tool.
    assert not native.calls_for("native.capture.fullscreen")


def test_chat_request_streams_through_brain_chat():
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})
    _flow, native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "hi"}]})

    assert brain.last_call("brain.chat")["params"]["messages"][0]["content"] == "hi"
    chunks = [c["params"] for c in ui.calls_for("ui.chat.chunk")]
    progress_chunks = [c for c in chunks if c.get("is_progress")]
    assert [c["text"] for c in progress_chunks] == []
    assert [c["text"] for c in chunks if not c.get("is_progress")] == ["chat reply"]
    done_params = ui.last_call("ui.chat.done")["params"]
    assert done_params["request_id"] == "chat-1"
    assert done_params["text"] == "chat reply"


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("configured model failed"),
        RuntimeError("configured tool failed"),
    ],
    ids=["model", "tool"],
)
def test_chat_runtime_model_and_tool_failures_end_as_controlled_errors(failure):
    """The shared chat workflow closes failed model/tool turns without a false result."""

    def fail(_params: dict[str, Any], _on_event):
        raise failure

    brain = FakeWorker(stream_handlers={"brain.chat": fail})
    _flow, _native, ui, _brain, _audio = make_flow(brain=brain)

    ui.emit(
        "ui.chat.request",
        {"request_id": "chat-failure", "messages": [{"role": "user", "content": "hi"}]},
    )

    error = ui.last_call("ui.chat.error")["params"]
    assert error["request_id"] == "chat-failure"
    assert str(failure) in error["error"]
    assert not ui.calls_for("ui.chat.done")


def test_chat_request_forwards_remote_activity_transcript_to_chat_window():
    """The final ordered provider transcript reaches local chat persistence."""
    display_segments = [
        {"text": "Inspecting\n", "is_thought": True},
        {"text": "Done", "is_thought": False},
    ]

    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        on_event("reply.chunk", {"text": "Inspecting\n", "is_thought": True}, 1)
        on_event("reply.chunk", {"text": "Done", "is_thought": False}, 1)
        payload = {"text": "Done", "display_segments": display_segments}
        on_event("reply.done", payload, 1)
        return payload

    brain = FakeWorker(stream_handlers={"brain.chat": stream})
    _flow, _native, ui, _brain, _audio = make_flow(brain=brain)

    ui.emit(
        "ui.chat.request",
        {"request_id": "chat-activity", "messages": [{"role": "user", "content": "inspect"}]},
    )

    done_params = ui.last_call("ui.chat.done")["params"]
    assert done_params["text"] == "Done"
    assert done_params["display_segments"] == display_segments


def test_chat_request_forwards_image_only_result_to_chat_window():
    """Direct chat completion includes generated media when final text is empty."""
    attachment = {
        "kind": "image",
        "source": "codex_image_generation",
        "path": "/repo/generated.png",
        "name": "generated.png",
    }

    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        payload = {"text": "", "attachments": [attachment]}
        on_event("reply.done", payload, 1)
        return payload

    brain = FakeWorker(stream_handlers={"brain.chat": stream})
    _flow, _native, ui, _brain, _audio = make_flow(brain=brain)

    ui.emit(
        "ui.chat.request",
        {
            "request_id": "chat-image",
            "messages": [{"role": "user", "content": "Generate a test image"}],
        },
    )

    done_params = ui.last_call("ui.chat.done")["params"]
    assert done_params["request_id"] == "chat-image"
    assert done_params["text"] == ""
    assert done_params["assistant_attachments"] == [attachment]


def test_chat_request_uses_selected_codex_project(monkeypatch, tmp_path):
    """The provider popup's project overrides automatic conversation context."""
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})
    _flow, _native, ui, brain, _audio = make_flow(brain=brain)
    monkeypatch.setattr(config, "CHAT_EXECUTION_MODE", "codex", raising=False)
    monkeypatch.setattr(config, "CHAT_CONVERSATION_OWNER", "agent", raising=False)
    monkeypatch.setattr(config, "OPENWAND_CODEX_WORKSPACE", str(tmp_path), raising=False)

    ui.emit(
        "ui.chat.request",
        {
            "request_id": "chat-project",
            "messages": [{"role": "user", "content": "inspect this project"}],
            "harness_cwd": "/automatic/context",
            "harness_sessions": {
                "codex": {"provider": "codex", "session_id": "old", "cwd": "/old"},
            },
        },
    )

    params = brain.last_call("brain.chat")["params"]
    assert params["harness_provider"] == "codex"
    assert params["conversation_owner"] == "agent"
    assert params["harness_cwd"] == str(tmp_path.resolve())


def test_chat_context_preview_updates_token_estimates_before_send(monkeypatch):
    """Verify chat context preview refreshes visible context token estimates."""
    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True)
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "selected chat text",
                "clipboard_text": "",
                "active_app": {"name": "Browser", "pid": 42, "bundle_id": "com.browser"},
                "browser_url": "https://example.test/page",
                "browser_hwnd": 777,
            },
            "native.context.browser_content": lambda params: {
                "url": params.get("url"),
                "content": "Browser page text for a chat preview.",
            },
        }
    )
    _flow, native, ui, brain, _audio = make_flow(native=native)

    ui.emit(
        "ui.chat.context_preview",
        {
            "preview_id": "preview-1",
            "context_policy": {
                "context_ambient": False,
                "context_documents_mode": "off",
                "context_browser_mode": "auto",
                "context_github_mode": "off",
                "context_memory_mode": "off",
                "context_screenshot": "off",
                "context_clipboard": False,
                "file_access": "off",
                "tools": {},
            },
        },
    )

    calls = ui.calls_for("ui.chat.context_preview")
    assert len(calls) == 2
    first_browser = next(item for item in calls[0]["params"]["context_items"] if item["id"] == "browser")
    assert first_browser["tokens"].startswith("~")
    updated_browser = next(item for item in calls[-1]["params"]["context_items"] if item["id"] == "browser")
    assert updated_browser["tokens"].startswith("~")
    assert updated_browser["warning"] == "Privacy: 1 item(s) detected and censored."
    assert native.last_call("native.context.browser_content")["params"] == {
        "url": "https://example.test/page",
        "hwnd": 777,
        "app": "",
    }
    prefix = brain.last_call("brain.llm.prefix.prewarm")
    assert prefix["wait"] is False
    assert prefix["params"]["route_kind"] == "chat"
    assert prefix["params"]["browser_retrieval"] is False


def test_context_estimate_failure_matrix_uses_fallbacks_and_refreshes_before_send():
    """Exercise deferred, unknown-tokenizer, capture, and stale-preview faults."""
    assert flow_estimates.deferred_token_label() == "? tok"
    # Estimates deliberately use the local heuristic, so neither a tokenizer nor
    # a provider/model identity is required for a safe preview.
    assert flow_estimates.token_label("text without model metadata").startswith("~")

    broken_native = FakeWorker(
        {"native.context.snapshot": lambda _params: (_ for _ in ()).throw(OSError("capture failed"))}
    )
    _flow, _native, broken_ui, _brain, _audio = make_flow(native=broken_native)
    broken_ui.emit(
        "ui.chat.context_preview",
        {"preview_id": "capture-failed", "context_policy": {"context_clipboard": True}},
    )
    assert broken_ui.last_call("ui.chat.context_preview")["params"]["preview_id"] == "capture-failed"

    snapshots = iter(("stale preview text", "fresh send text"))
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "",
                "clipboard_text": next(snapshots),
                "active_app": {},
            }
        }
    )
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("reply")})
    _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
    policy = {
        "context_ambient": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "context_clipboard": True,
        "file_access": "off",
        "tools": {},
    }
    ui.emit("ui.chat.context_preview", {"preview_id": "stale", "context_policy": policy})
    ui.emit(
        "ui.chat.request",
        {
            "request_id": "fresh-context",
            "messages": [{"role": "user", "content": "send"}],
            "context_policy": policy,
        },
    )
    sent = "\n".join(str(item.get("content") or "") for item in brain.last_call("brain.chat")["params"]["messages"])
    assert "fresh send text" in sent
    assert "stale preview text" not in sent


def test_chat_context_preview_treats_legacy_browser_on_as_enabled(monkeypatch):
    """Verify legacy chat Browser/Web on mode refreshes token estimates."""
    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True)
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {"name": "Browser", "pid": 42, "bundle_id": "com.browser"},
                "browser_url": "https://example.test/page",
                "browser_hwnd": 777,
            },
            "native.context.browser_content": lambda params: {
                "url": params.get("url"),
                "content": "Browser page text for a legacy on chat policy.",
            },
        }
    )
    _flow, native, ui, _brain, _audio = make_flow(native=native)

    ui.emit(
        "ui.chat.context_preview",
        {
            "preview_id": "preview-legacy-on",
            "context_policy": {
                "context_ambient": False,
                "context_documents_mode": "off",
                "context_browser_mode": "on",
                "context_github_mode": "off",
                "context_memory_mode": "off",
                "context_screenshot": "off",
                "context_clipboard": False,
                "file_access": "off",
                "tools": {},
            },
        },
    )

    calls = ui.calls_for("ui.chat.context_preview")
    assert len(calls) == 2
    first_browser = next(item for item in calls[0]["params"]["context_items"] if item["id"] == "browser")
    assert first_browser["state"] == "on"
    assert first_browser["tokens"].startswith("~")
    updated_browser = next(item for item in calls[-1]["params"]["context_items"] if item["id"] == "browser")
    assert updated_browser["state"] == "on"
    assert updated_browser["tokens"].startswith("~")
    assert updated_browser["warning"] == "Privacy: 1 item(s) detected and censored."
    assert native.last_call("native.context.browser_content")["params"] == {
        "url": "https://example.test/page",
        "hwnd": 777,
        "app": "",
    }


def test_chat_context_preview_keeps_requested_browser_on_while_detecting():
    """Browser/Web stays visibly enabled while the fallback browser scan runs."""
    native = FakeWorker(
        {
            "native.context.snapshot": lambda params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {"name": "OpenWand", "pid": 42, "bundle_id": "app.openwand"},
                "browser_url": "",
                "browser_hwnd": 0,
                "browser_content": "Detected browser page" if params.get("include_browser_content") else "",
            },
        }
    )
    _flow, native, ui, _brain, _audio = make_flow(native=native)

    ui.emit(
        "ui.chat.context_preview",
        {
            "preview_id": "preview-browser-detecting",
            "context_policy": {
                "context_ambient": False,
                "context_documents_mode": "off",
                "context_browser_mode": "auto",
                "context_github_mode": "off",
                "context_memory_mode": "off",
                "context_screenshot": "off",
                "context_clipboard": False,
                "file_access": "off",
                "tools": {},
            },
        },
    )

    calls = ui.calls_for("ui.chat.context_preview")
    assert len(calls) == 2
    first_browser = next(item for item in calls[0]["params"]["context_items"] if item["id"] == "browser")
    assert first_browser["state"] == "on"
    assert first_browser["tokens"] == "? tok"
    updated_browser = next(item for item in calls[-1]["params"]["context_items"] if item["id"] == "browser")
    assert updated_browser["state"] == "on"
    assert updated_browser["tokens"].startswith("~")


def test_chat_context_preview_estimates_off_context_sources_without_capture(monkeypatch):
    """Verify chat previews off context costs without changing send policy."""
    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True)
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "api_key = sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",  # secret-scan: allow
                "clipboard_text": "password=supersecret",
                "active_app": {"name": "Preview App", "pid": 42, "bundle_id": "com.preview"},
                "screen_size": {"width": 1920, "height": 1080},
            },
        }
    )
    _flow, native, ui, _brain, _audio = make_flow(native=native)

    ui.emit(
        "ui.chat.context_preview",
        {
            "preview_id": "preview-all-off",
            "context_policy": {
                "context_ambient": False,
                "context_documents_mode": "off",
                "context_browser_mode": "off",
                "context_github_mode": "off",
                "context_memory_mode": "off",
                "context_screenshot": "off",
                "context_clipboard": False,
                "file_access": "off",
                "tools": {},
            },
        },
    )

    calls = ui.calls_for("ui.chat.context_preview")
    assert len(calls) == 1
    chips = {
        item["id"]: item
        for item in calls[0]["params"]["context_items"]
    }
    assert chips["screenshot"]["state"] == "off"
    assert chips["screenshot"]["tokens"] == "? tok"
    assert chips["selection"]["tokens"].startswith("~")
    assert chips["clipboard"]["tokens"].startswith("~")
    assert chips["selection"]["privacy_count"] == 1
    assert "detected and censored" in chips["selection"]["warning"]
    assert chips["clipboard"]["privacy_count"] == 1
    assert chips["ambient"]["tokens"].startswith("~")
    assert not native.calls_for("native.capture.fullscreen")


def test_chat_context_preview_respects_disabled_privacy_mode(monkeypatch):
    """Privacy-off previews must not claim that content was detected or censored."""
    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", False)
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "api_key = sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",  # secret-scan: allow
                "clipboard_text": "",
                "active_app": {"name": "Preview App", "pid": 42, "bundle_id": "com.preview"},
            },
        }
    )
    _flow, _native, ui, _brain, _audio = make_flow(native=native)

    ui.emit(
        "ui.chat.context_preview",
        {
            "preview_id": "preview-privacy-off",
            "context_policy": {
                "context_ambient": False,
                "context_documents_mode": "off",
                "context_browser_mode": "off",
                "context_github_mode": "off",
                "context_memory_mode": "off",
                "context_screenshot": "off",
                "context_clipboard": False,
                "file_access": "off",
                "tools": {},
            },
        },
    )

    selection = next(
        item
        for item in ui.last_call("ui.chat.context_preview")["params"]["context_items"]
        if item["id"] == "selection"
    )
    assert selection["privacy_count"] == 0
    assert "Privacy:" not in selection["warning"]


def test_chat_request_forwards_file_context_metadata():
    """Verify chat request forwards display-hidden file metadata to the UI."""
    file_context = [
        {
            "tool": "create_file",
            "path": r"C:\repo\model_files\hello_world.py",
            "relative_path": "hello_world.py",
            "ok": True,
        }
    ]

    def chat_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit a final reply with local-file metadata."""
        on_event("reply.done", {"text": "done", "file_context": file_context}, 1)
        return {"text": "done", "file_context": file_context}

    brain = FakeWorker(stream_handlers={"brain.chat": chat_stream})
    _flow, _native, ui, _brain, _audio = make_flow(brain=brain)

    ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "create"}]})

    done_params = ui.last_call("ui.chat.done")["params"]
    assert done_params["text"] == "done"
    assert done_params["file_context"] == file_context


def test_chat_request_forwards_live_file_activity_to_monitor_link():
    """Local file work reaches chat while it is running, before reply completion."""
    def chat_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        event = {
            "tool": "read_file",
            "path": r"C:\repo\notes.txt",
            "relative_path": "notes.txt",
            "root": r"C:\repo",
        }
        on_event("live_file.activity", {**event, "phase": "started"}, 1)
        on_event("live_file.activity", {**event, "phase": "completed", "ok": True}, 1)
        on_event("reply.done", {"text": "done"}, 1)
        return {"text": "done"}

    brain = FakeWorker(stream_handlers={"brain.chat": chat_stream})
    _flow, _native, ui, _brain, _audio = make_flow(brain=brain)

    ui.emit("ui.chat.request", {
        "request_id": "chat-local-work",
        "messages": [{"role": "user", "content": "inspect notes"}],
    })

    activity = [
        call["params"]["local_work"]
        for call in ui.calls_for("ui.chat.chunk")
        if call["params"].get("local_work")
    ]
    assert [event["phase"] for event in activity] == ["started", "completed"]
    assert activity[0]["relative_path"] == "notes.txt"


def test_model_workspace_tool_event_opens_native_window_without_activation():
    """Successful model workspace work opens its native viewer without stealing focus."""
    endpoint = f"http://127.0.0.1:8765/?token={'t' * 32}"

    def chat_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        on_event("model_tool.ui.request", {
            "action": "show_virtual_workspace",
            "endpoint": endpoint,
            "tool": "virtual_workspace_write_text",
        }, 1)
        on_event("reply.done", {"text": "done"}, 1)
        return {"text": "done"}

    brain = FakeWorker(stream_handlers={"brain.chat": chat_stream})
    ui = FakeWorker({"ui.show_virtual_workspace": lambda _params: {"shown": True}})
    _flow, _native, ui, _brain, _audio = make_flow(ui=ui, brain=brain)

    ui.emit("ui.chat.request", {
        "request_id": "chat-workspace-window",
        "messages": [{"role": "user", "content": "create a workspace file"}],
    })

    assert ui.last_call("ui.show_virtual_workspace")["params"] == {
        "endpoint": endpoint,
        "activate": False,
    }
    assert not [
        call for call in ui.calls_for("ui.reply.notice")
        if "workspace" in str(call["params"].get("text") or "").lower()
    ]


def test_chat_request_forwards_addon_text_annotations():
    """Verify chat request forwards display-only addon annotations to the UI."""
    seen_roles = []

    def annotations_handler(params):
        payload = params.get("payload") or {}
        role = str(payload.get("role") or "")
        seen_roles.append(role)
        return {
            "annotations": [
                {
                    "id": f"{role}-mark",
                    "start": 0,
                    "end": min(2, len(str(payload.get("text") or ""))),
                    "tag": "mark",
                }
            ]
        }

    def chat_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        on_event("reply.done", {"text": "done"}, 1)
        return {"text": "done"}

    brain = FakeWorker(
        handlers={"brain.addons.text_annotations": annotations_handler},
        stream_handlers={"brain.chat": chat_stream},
    )
    _flow, _native, ui, _brain, _audio = make_flow(brain=brain)

    ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "hi"}]})

    done_params = ui.last_call("ui.chat.done")["params"]
    assert seen_roles == ["user", "assistant"]
    assert done_params["user_annotations"][0]["id"] == "user-mark"
    assert done_params["annotations"][0]["id"] == "assistant-mark"


def test_chat_request_reuses_conversation_tool_context():
    """Verify switched conversations keep their stored tool grants."""
    rows = [
        {
            "file_access": "off",
            "tools": {},
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
        }
    ]
    tool_context = {
        "allowed_tools": ["read_file", "edit_file"],
        "pinned_tools": ["read_file", "edit_file"],
        "file_access_mode": "ask",
    }
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})

    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(brain=brain)
        ui.emit(
            "ui.chat.request",
            {
                "request_id": "chat-1",
                "messages": [{"role": "user", "content": "edit again"}],
                "tool_context": tool_context,
            },
        )

    params = brain.last_call("brain.chat")["params"]
    assert params["allowed_tools"] == ["read_file", "edit_file"]
    assert params["pinned_tools"] == ["read_file", "edit_file"]
    assert params["file_access_mode"] == "ask"
    assert ui.last_call("ui.chat.done")["params"]["tool_context"] == tool_context


def test_chat_request_context_policy_is_absolute_over_legacy_tool_context():
    """Verify visible chat policy does not inherit older hidden tool grants."""
    rows = [
        {
            "file_access": "off",
            "tools": {},
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
        }
    ]
    tool_context = {
        "allowed_tools": ["read_file", "edit_file"],
        "pinned_tools": ["read_file", "edit_file"],
        "file_access_mode": "ask",
    }
    context_policy = {
        "context_ambient": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "context_clipboard": False,
        "file_access": "off",
        "tools": {},
    }
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})

    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(brain=brain)
        ui.emit(
            "ui.chat.request",
            {
                "request_id": "chat-1",
                "messages": [{"role": "user", "content": "edit again"}],
                "tool_context": tool_context,
                "context_policy": context_policy,
            },
        )

    params = brain.last_call("brain.chat")["params"]
    assert params["allowed_tools"] == []
    assert params["pinned_tools"] == []
    assert params["file_access_mode"] == "off"


def test_chat_request_all_off_policy_does_not_inject_selection_context():
    """Verify all-off chat policy does not silently capture selected UI text."""
    context_policy = {
        "context_ambient": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "context_clipboard": False,
        "file_access": "off",
        "tools": {},
    }
    native = FakeWorker({"native.context.snapshot": context_handler(selected="selected chat bubble")})
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})
    _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)

    ui.emit(
        "ui.chat.request",
        {
            "request_id": "chat-1",
            "messages": [{"role": "user", "content": "can you see the webpage?"}],
            "context_policy": context_policy,
        },
    )

    params = brain.last_call("brain.chat")["params"]
    assert not native.calls_for("native.context.snapshot")
    assert "selected chat bubble" not in "\n\n".join(str(msg.get("content") or "") for msg in params["messages"])


def test_chat_request_browser_on_fetches_context_from_chat_policy():
    """Verify chat Browser/Web On frontloads browser text independent of caller defaults."""
    rows = [
        {
            "file_access": "off",
            "tools": {},
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
        }
    ]
    context_policy = {
        "context_ambient": False,
        "context_documents_mode": "off",
        "context_browser_mode": "auto",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "context_clipboard": False,
        "file_access": "off",
        "tools": {},
    }
    native = FakeWorker(
        {
            "native.context.snapshot": lambda params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {"name": "Browser", "pid": 42},
                "browser_url": "https://example.test/chat" if params.get("include_browser_url") else "",
                "browser_hwnd": 777 if params.get("include_browser_url") else 0,
            },
            "native.context.browser_content": lambda params: {
                "url": params.get("url") or "",
                "content": "Fetched chat browser page",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})

    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        ui.emit(
            "ui.chat.request",
            {
                "request_id": "chat-1",
                "messages": [{"role": "user", "content": "summarize the page"}],
                "context_policy": context_policy,
            },
        )

    params = brain.last_call("brain.chat")["params"]
    assert "Fetched chat browser page" in params["messages"][0]["content"]
    assert native.last_call("native.context.browser_content")["params"] == {
        "url": "https://example.test/chat",
        "hwnd": 777,
        "app": "",
    }


def test_chat_request_keeps_file_tools_off_when_caller_files_are_off():
    """Verify chat does not grant hidden file tools when Files is off."""
    rows = [
        {
            "file_access": "off",
            "tools": {},
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
        }
    ]
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(brain=brain)
        ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "create a file"}]})

    params = brain.last_call("brain.chat")["params"]
    assert params["use_tools"] is False
    assert params["file_access_mode"] == "off"
    assert brain.last_call("brain.chat")["timeout"] == 120.0
    assert not ({"list_files", "read_file", "create_file", "edit_file", "write_file"} & set(params["allowed_tools"]))


def test_chat_request_inherits_first_caller_file_tools():
    """Verify chat requests carry file tool grants from the first caller row."""
    rows = [
        {
            "file_access": "ask",
            "tools": {},
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
        }
    ]
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(brain=brain)
        ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "edit a file"}]})

    params = brain.last_call("brain.chat")["params"]
    assert params["use_tools"] is True
    assert params["file_access_mode"] == "ask"
    assert brain.last_call("brain.chat")["timeout"] == 300.0
    assert not any(call["params"].get("is_progress") for call in ui.calls_for("ui.chat.chunk"))
    assert set(params["allowed_tools"]) >= {"list_files", "read_file", "create_file", "edit_file", "write_file"}
    assert set(params["pinned_tools"]) >= {"list_files", "read_file", "create_file", "edit_file", "write_file"}


def test_chat_live_file_approval_routes_to_ui_and_brain():
    """Verify live file approval requests are resolved back to the brain worker."""
    def chat_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit one live file approval request during chat."""
        on_event(
            "live_file.approval.request",
            {"approval_id": "file-1", "action": "edit_file", "path": "note.txt"},
            1,
        )
        on_event("reply.done", {"text": "ok"}, 1)
        return {"text": "ok"}

    brain = FakeWorker(
        handlers={"brain.live_file.approval.respond": lambda params: {"ok": True, "approved": params["approved"]}},
        stream_handlers={"brain.chat": chat_stream},
    )
    ui = FakeWorker(handlers={"ui.live_file.approval.request": lambda _params: {"approved": True}})
    _flow, _native, ui, brain, _audio = make_flow(ui=ui, brain=brain)

    ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "edit"}]})

    assert ui.last_call("ui.live_file.approval.request")["params"]["approval_id"] == "file-1"
    assert brain.last_call("brain.live_file.approval.respond")["params"] == {
        "approval_id": "file-1",
        "approved": True,
        "feedback": "",
    }
    assert brain.last_call("brain.live_file.approval.respond")["wait"] is False


def test_chat_harness_activity_routes_to_top_right_inspector():
    """Subagent and capability events stay structured instead of becoming reply text."""
    activity = {
        "type": "subagent",
        "agent_id": "child-thread",
        "status": "running",
        "prompt": "Inspect the tests",
    }

    def chat_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        on_event("harness.activity", activity, 1)
        on_event("reply.done", {"text": "ok"}, 1)
        return {"text": "ok"}

    brain = FakeWorker(stream_handlers={"brain.chat": chat_stream})
    _flow, _native, ui, _brain, _audio = make_flow(brain=brain)

    ui.emit("ui.chat.request", {"request_id": "chat-activity", "messages": [{"role": "user", "content": "go"}]})

    chunks = [call["params"] for call in ui.calls_for("ui.chat.chunk")]
    assert any(chunk.get("harness_activity") == activity for chunk in chunks)


def test_chat_codex_user_input_response_round_trips_to_brain():
    """Structured request_user_input answers survive the UI/supervisor boundary."""
    def chat_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        on_event(
            "live_file.approval.request",
            {
                "approval_id": "question-1",
                "kind": "user_input",
                "questions": [{"id": "scope", "question": "Which scope?"}],
            },
            1,
        )
        on_event("reply.done", {"text": "ok"}, 1)
        return {"text": "ok"}

    brain = FakeWorker(
        handlers={"brain.live_file.approval.respond": lambda params: params},
        stream_handlers={"brain.chat": chat_stream},
    )
    answers = {"scope": {"answers": ["Project"]}}
    ui = FakeWorker(
        handlers={
            "ui.live_file.approval.request": lambda _params: {
                "approved": True,
                "answers": answers,
                "surface": "harness_input",
            }
        }
    )
    _flow, _native, _ui, brain, _audio = make_flow(ui=ui, brain=brain)

    ui.emit("ui.chat.request", {"request_id": "chat-question", "messages": [{"role": "user", "content": "go"}]})

    response = brain.last_call("brain.live_file.approval.respond")["params"]
    assert response["approved"] is True
    assert response["response"] == {"answers": answers}


def test_icon_summon_routes_to_first_caller_like_default_hotkey():
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
    native = FakeWorker({"native.context.snapshot": context_handler(selected="icon selected")})
    with caller_config(rows):
        _flow, native, ui, _brain, _audio = make_flow(native=native)
        ui.emit("ui.summon_caller", {"caller_idx": 0})

    assert native.last_call("native.context.snapshot")["params"]["include_selection"] is True
    assert ui.last_call("ui.show_intent")["params"]["caller_idx"] == 0


def test_hotkey_followup_injects_active_chat_file_context():
    """Verify prior file metadata is replayed as hidden context for hotkey follow-ups."""
    path = r"C:\repo\model_files\hello_world.py"
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
            "context_memory_mode": "off",
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    ui = FakeWorker(
        {
            "ui.chat.active_history": lambda _params: {
                "history": [{"role": "user", "content": "create a file"}],
                "project_id": None,
                "context": "Original ambient context",
                "file_context": [
                    {
                        "tool": "create_file",
                        "path": path,
                        "relative_path": "hello_world.py",
                        "ok": True,
                    }
                ],
                "tool_context": {
                    "allowed_tools": ["read_file", "edit_file"],
                    "pinned_tools": ["read_file", "edit_file"],
                    "file_access_mode": "ask",
                },
            }
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("done")})

    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "edit that file"})

    query = brain.last_call("brain.query")["params"]
    assert path in query["ambient_text"]
    assert "Original ambient context" in query["ambient_text"]
    assert "Conversation Context" in query["ambient_text"]
    assert "Conversation File Context" in query["ambient_text"]
    assert query["allowed_tools"] == []
    assert query["pinned_tools"] == []
    assert query["file_access_mode"] == "off"


def test_hotkey_followup_keeps_current_tools_separate_from_active_chat_tools():
    """Verify chat tool grants are history context, not current hotkey tools."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "model",
            "context_browser_mode": "model",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
            "file_access": "off",
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    ui = FakeWorker(
        {
            "ui.chat.active_history": lambda _params: {
                "history": [{"role": "user", "content": "create a file"}],
                "project_id": None,
                "tool_context": {
                    "allowed_tools": ["read_file", "edit_file"],
                    "pinned_tools": ["read_file", "edit_file"],
                    "file_access_mode": "ask",
                },
            }
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("done")})

    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Use page and edit that file"})

    query = brain.last_call("brain.query")["params"]
    assert query["allowed_tools"] == ["get_context.documents", "web_search", "get_context.browser", "retrieve_website"]
    assert query["pinned_tools"] == ["get_context", "web_search", "retrieve_website"]
    assert query["file_access_mode"] == "off"
    assert query["use_tools"] is True


def test_caller_memory_modes_map_to_injected_or_model_decided_access():
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        },
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": True,
            "context_memory_mode": "model",
            "context_screenshot": "off",
            "context_clipboard": False,
        },
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_memory_mode": "on",
            "context_screenshot": "off",
            "context_clipboard": False,
        },
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="selected")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("reply")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "use no memory"})
        _flow.begin_caller(1)
        ui.emit("ui.intent.chosen", {"custom": "model may search memory"})
        _flow.begin_caller(2)
        ui.emit("ui.intent.chosen", {"custom": "use injected memory"})

    calls = brain.calls_for("brain.query")
    assert calls[0]["params"]["memory_enabled"] is False
    assert "memory_search" not in calls[0]["params"]["allowed_tools"]
    assert calls[1]["params"]["memory_enabled"] is False
    assert "memory_search" in calls[1]["params"]["allowed_tools"]
    assert calls[2]["params"]["memory_enabled"] is True
    assert "memory_search" not in calls[2]["params"]["allowed_tools"]


def test_memory_events_route_to_brain_and_seed_ui_viewer():
    brain = FakeWorker({"brain.memory.list": lambda _params: {"facts": [{"id": "1", "text": "remember"}]}})
    _flow, native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit("ui.memory.open_requested", {})
    ui.emit("ui.memory.add", {"text": "new fact", "category": "project_context", "project": "proj-1"})
    ui.emit(
        "ui.memory.update",
        {"id": "1", "text": "updated", "category": "project_context", "project": "proj-2"},
    )
    ui.emit("ui.memory.delete", {"id": "1"})

    assert ui.last_call("ui.show_memory")["params"]["facts"][0]["text"] == "remember"
    assert brain.last_call("brain.memory.add")["params"]["text"] == "new fact"
    assert brain.last_call("brain.memory.add")["params"]["project"] == "proj-1"
    assert brain.last_call("brain.memory.update")["params"]["fact_id"] == "1"
    assert brain.last_call("brain.memory.update")["params"]["project"] == "proj-2"
    assert brain.last_call("brain.memory.delete")["params"]["fact_id"] == "1"


def test_settings_open_includes_live_addon_tools():
    """Verify settings open includes live addon tool payloads."""
    brain = FakeWorker(
        {
            "brain.addons.tools": lambda _params: {
                "tools": [
                    {
                        "name": "mcp_example_echo",
                        "description": "[MCP:example] Echo back text.",
                    }
                ]
            },
        }
    )
    _flow, _native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit("ui.settings.open_requested", {})

    params = ui.last_call("ui.show_settings")["params"]
    assert params["extra_tools"] == [
        {
            "name": "mcp_example_echo",
            "description": "[MCP:example] Echo back text.",
        }
    ]
    assert params["initial_page"] is None

    ui.emit("ui.settings.open_requested", {"initial_page": "LLM"})
    assert ui.last_call("ui.show_settings")["params"]["initial_page"] == "LLM"


def test_addon_and_agent_tray_events_route_through_supervisor():
    brain = FakeWorker(
        {
            "brain.addons.ready": lambda _params: {
                "ready": True,
                "addons": [{"name": "demo", "status": "loaded", "tray_actions": ["Run"]}],
            },
            "brain.addons.list": lambda _params: {
                "addons_dir": "/tmp/addons",
                "addons": [{"name": "demo", "status": "loaded", "tray_actions": ["Run"]}],
            },
            "brain.addons.run_action": lambda _params: {
                "ok": True,
                "message": "ran demo",
                "virtual_workspace_url": "http://127.0.0.1:8765/?token=test",
            },
            "brain.addons.set_action_enabled": lambda _params: {"ok": True},
            "brain.addons.approve": lambda _params: {"status": "loaded"},
            "brain.addons.repair_environment": lambda _params: {"ready": True},
            "brain.addons.install_archive": lambda _params: {"id": "demo2"},
            "brain.addons.install_folder": lambda _params: {"id": "demo3"},
            "brain.addons.run_hotkey": lambda _params: {"prompt": "hotkey prompt"},
            "brain.query": lambda _params: {"text": "hotkey answer"},
            "brain.agent.history.list": lambda _params: {
                "runs_root": "/tmp/runs",
                "runs": [{"title": "recent", "run_dir": "/tmp/runs/1"}],
            },
        }
    )
    _flow, native, ui, brain, _audio = make_flow(brain=brain)

    assert ui.last_call("ui.show_overlay")["params"]["addon_tray_actions"] == [
        {"addon_id": "demo", "label": "Run"}
    ]

    ui.emit("ui.addons.open_requested", {})
    ui.emit("ui.addons.run_action", {"addon_id": "demo", "label": "Run"})
    ui.emit(
        "ui.addons.set_action_enabled",
        {"addon_id": "demo", "action_id": "lookup", "enabled": False},
    )
    ui.emit("ui.addons.approve", {"addon_id": "demo"})
    ui.emit("ui.addons.repair_environment", {"addon_id": "demo"})
    ui.emit("ui.addons.install_archive", {"path": "/tmp/demo.openwand"})
    ui.emit("ui.addons.install_folder", {"path": "/tmp/demo-folder"})
    native.emit("native.hotkey", {"kind": "addon", "addon_id": "demo", "hotkey_id": "hk"})
    ui.emit("ui.agent.task_requested", {})
    ui.emit("ui.agent.history_requested", {})

    assert ui.last_call("ui.show_addons")["params"]["addons"][0]["name"] == "demo"
    assert brain.last_call("brain.addons.run_action")["params"]["addon_id"] == "demo"
    assert brain.last_call("brain.addons.set_action_enabled")["params"] == {
        "addon_id": "demo",
        "action_id": "lookup",
        "enabled": False,
    }
    assert brain.last_call("brain.addons.approve")["params"]["addon_id"] == "demo"
    assert ui.last_call("ui.show_virtual_workspace")["params"]["endpoint"].startswith(
        "http://127.0.0.1:"
    )
    assert brain.last_call("brain.addons.repair_environment")["params"]["addon_id"] == "demo"
    assert brain.last_call("brain.addons.install_archive")["params"]["path"] == "/tmp/demo.openwand"
    assert brain.last_call("brain.addons.install_folder")["params"]["path"] == "/tmp/demo-folder"
    assert brain.last_call("brain.addons.run_hotkey")["params"]["hotkey_id"] == "hk"
    assert ui.calls_for("ui.reply.notice")
    assert ui.calls_for("ui.show_agent_task")
    assert ui.last_call("ui.show_agent_history")["params"]["runs"][0]["title"] == "recent"


def test_opening_virtual_workspace_does_not_show_a_redundant_speech_notice():
    brain = FakeWorker(
        {
            "brain.addons.run_action": lambda _params: {
                "ok": True,
                "message": "Virtual Workspace opened.",
                "virtual_workspace_url": "http://127.0.0.1:8765/?token=test",
            },
        }
    )
    ui = FakeWorker({"ui.show_virtual_workspace": lambda _params: {"shown": True}})
    flow, _native, ui, _brain, _audio = make_flow(ui=ui, brain=brain)
    notices_before = len(ui.calls_for("ui.reply.notice"))

    flow.addon_run_action({"addon_id": "virtual-workspace", "label": "Open Virtual Workspace"})

    assert len(ui.calls_for("ui.reply.notice")) == notices_before
    assert ui.last_call("ui.show_virtual_workspace")["params"]["endpoint"].startswith(
        "http://127.0.0.1:"
    )


def test_addon_startup_notifications_reach_the_native_desktop_boundary():
    """Enabled add-on notifications are emitted through the production supervisor route."""
    brain = FakeWorker(
        {
            "brain.addons.list": lambda _params: {
                "addons": [
                    {
                        "id": "demo",
                        "enabled": True,
                        "notifications": [
                            {"title": "Demo ready", "message": "The add-on loaded."},
                            {"title": "", "message": "ignored without a title"},
                        ],
                    },
                ]
            }
        }
    )
    flow, native, _ui, _brain, _audio = make_flow(brain=brain)

    flow._show_addon_notifications()

    assert [call["params"] for call in native.calls_for("native.notify")] == [
        {"title": "Demo ready", "message": "The add-on loaded."},
        {"title": "OpenWand", "message": "ignored without a title"},
    ]


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


def test_agent_approval_request_declines_when_ui_cannot_accept():
    """Verify unshowable approval requests do not leave the brain waiting forever."""
    def agent_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit one approval request without an active UI approval panel."""
        on_event("agent.approval.request", {"approval_id": "abc", "action": "git"}, 1)
        return {"run_dir": "/tmp/run", "final": "done"}

    brain = FakeWorker(
        handlers={"brain.agent.approval.respond": lambda params: {"ok": True, "approved": params["approved"]}},
        stream_handlers={"brain.agent.run": agent_stream},
    )
    _flow, _native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit(
        "ui.agent.run_requested",
        {"spec": {"title": "demo task", "max_runtime_minutes": 1}},
    )

    assert brain.last_call("brain.agent.approval.respond")["params"] == {
        "approval_id": "abc",
        "approved": False,
    }
    assert "could not be shown" in ui.last_call("ui.reply.notice")["params"]["text"]


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


def test_agent_pause_nudge_and_permissions_route_to_active_brain_run():
    """Verify live agent controls route to the active brain stream."""
    brain = FakeWorker(
        handlers={
            "brain.agent.control": lambda params: {"ok": True, "action": params["action"]},
        }
    )
    flow, _native, ui, brain, _audio = make_flow(brain=brain)

    with flow._lock:
        flow._active_agent_stream_id = 7
    ui.emit("ui.agent.pause_requested", {})
    ui.emit("ui.agent.resume_requested", {})
    ui.emit("ui.agent.nudge", {"target_agent": "Builder", "message": "Please inspect tests."})
    ui.emit("ui.agent.permissions", {"permission_modes": {"shell": "ask permission"}})

    calls = brain.calls_for("brain.agent.control")
    assert [call["params"]["action"] for call in calls] == ["pause", "resume", "nudge", "permissions"]
    assert all(call["params"]["target"] == 7 for call in calls)
    assert calls[2]["params"]["target_agent"] == "Builder"
    assert calls[2]["params"]["message"] == "Please inspect tests."
    assert calls[3]["params"]["permission_modes"] == {"shell": "ask permission"}


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


def test_settings_reload_refreshes_supervisor_brain_audio_and_hotkeys(monkeypatch):
    reload_calls: list[str] = []
    monkeypatch.setattr(config, "reload", lambda: reload_calls.append("supervisor"))
    _flow, native, ui, brain, audio = make_flow()

    ui.emit("ui.settings.applied", {"changed_keys": ["KOKORO_DEVICE"]})

    assert reload_calls == ["supervisor"]
    assert brain.calls_for("brain.config.reload")
    assert audio.calls_for("audio.config.reload")
    # The native worker must reload its own config and replace registrations in
    # one operation, else a changed hotkey can keep the old listener alive until
    # a second Apply.
    assert native.calls_for("native.hotkeys.reload")
    assert not native.calls_for("native.config.reload")
    assert not native.calls_for("native.hotkeys.stop")
    assert not native.calls_for("native.hotkeys.start")


def test_settings_reload_skips_audio_when_audio_settings_unchanged(monkeypatch):
    """Unrelated Settings changes should not reset and rewarm STT/TTS."""
    reload_calls: list[str] = []
    monkeypatch.setattr(config, "reload", lambda: reload_calls.append("supervisor"))
    _flow, native, ui, brain, audio = make_flow()
    privacy_prewarms = len(brain.calls_for("brain.privacy.prewarm"))
    harness_prewarms = len(brain.calls_for("brain.harness.prewarm"))

    ui.emit("ui.settings.applied", {"changed_keys": ["THEME_MODE"]})

    assert reload_calls == ["supervisor"]
    assert brain.calls_for("brain.config.reload")
    assert len(brain.calls_for("brain.privacy.prewarm")) == privacy_prewarms
    assert len(brain.calls_for("brain.harness.prewarm")) == harness_prewarms
    assert not audio.calls_for("audio.config.reload")
    assert native.calls_for("native.hotkeys.reload")


def test_settings_reload_prewarms_new_advanced_privacy_mode(monkeypatch):
    reload_calls: list[str] = []
    monkeypatch.setattr(config, "reload", lambda: reload_calls.append("supervisor"))
    _flow, _native, ui, brain, _audio = make_flow()
    privacy_prewarms = len(brain.calls_for("brain.privacy.prewarm"))

    ui.emit("ui.settings.applied", {"changed_keys": ["PRIVACY_MODE"]})

    assert reload_calls == ["supervisor"]
    assert len(brain.calls_for("brain.privacy.prewarm")) == privacy_prewarms + 1
    assert brain.last_call("brain.privacy.prewarm")["wait"] is False


def test_settings_reload_prewarms_new_codex_execution_mode(monkeypatch):
    reload_calls: list[str] = []
    monkeypatch.setattr(config, "reload", lambda: reload_calls.append("supervisor"))
    _flow, _native, ui, brain, _audio = make_flow()
    harness_prewarms = len(brain.calls_for("brain.harness.prewarm"))

    ui.emit("ui.settings.applied", {"changed_keys": ["CHAT_EXECUTION_MODE"]})

    assert reload_calls == ["supervisor"]
    assert len(brain.calls_for("brain.harness.prewarm")) == harness_prewarms + 1
    assert brain.last_call("brain.harness.prewarm")["wait"] is False


def test_start_hotkeys_surfaces_failed_registration_to_user():
    native = FakeWorker(
        {"native.hotkeys.start": lambda _params: {"started": False, "reason": "Carbon unavailable"}}
    )
    flow, _native, ui, _brain, _audio = make_flow(native=native)

    result = flow.start_hotkeys()

    assert result["started"] is False
    notice = ui.last_call("ui.reply.notice")["params"]
    assert notice["text"].startswith("Global hotkeys did not start")
    assert notice["severity"] == "warning"
def test_model_background_task_completion_returns_to_originating_chat(tmp_path, monkeypatch):
    """Detached completion is delivered by stable chat id after the foreground reply ends."""
    import json

    from core.system import paths as system_paths

    runs = tmp_path / "agent_runs"
    jobs = runs / "background_jobs"
    jobs.mkdir(parents=True)
    final = tmp_path / "final.md"
    final.write_text("Implemented the change and tests passed.", encoding="utf-8")
    state = jobs / "job-0123456789.json"
    state.write_text(
        json.dumps(
            {
                "job_id": "job-0123456789",
                "status": "completed",
                "title": "Repair export",
                "final_path": str(final),
                "run_dir": str(tmp_path / "run"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(system_paths, "AGENT_RUNS_DIR", runs)
    ui = FakeWorker({"ui.chat.background_result": lambda _params: {"appended": True}})
    flow, _native, ui, _brain, _audio = make_flow(ui=ui)

    flow._watch_model_background_task(
        {"job_id": "job-0123456789", "state_path": str(state)},
        conversation_id="conversation-origin",
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not ui.calls_for("ui.chat.background_result"):
        time.sleep(0.02)

    delivered = ui.last_call("ui.chat.background_result")["params"]
    assert delivered["conversation_id"] == "conversation-origin"
    assert delivered["job_id"] == "job-0123456789"
    assert delivered["text"] == "Implemented the change and tests passed."
    deadline = time.monotonic() + 3
    persisted = {}
    while time.monotonic() < deadline:
        persisted = json.loads(state.read_text(encoding="utf-8"))
        if persisted.get("delivered_at"):
            break
        time.sleep(0.02)
    assert persisted["delivered_at"]
