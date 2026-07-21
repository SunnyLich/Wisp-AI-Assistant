"""Tests for FlowController integration with the runtime event log."""

from __future__ import annotations

from typing import Any

import pytest

from runtime.supervisor.flows import FlowController
from runtime.supervisor.runtime_log import RuntimeEventLog


class FakeWorker:
    """Minimal worker double recording calls and event registrations."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.events: dict[str, Any] = {}

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0, wait: bool = True) -> Any:
        self.calls.append({"method": method, "params": params or {}, "timeout": timeout, "wait": wait})
        return {}

    def call_with_events(self, method, params=None, *, timeout=30.0, on_event, on_started=None):
        return {}

    def on_event(self, event: str, handler) -> None:
        self.events[event] = handler

    def calls_for(self, method: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["method"] == method]


@pytest.fixture()
def flows():
    """A flow controller with fake workers and an isolated event log."""
    log = RuntimeEventLog()
    log._installer_status_files = staticmethod(lambda: [])
    controller = FlowController(
        native=FakeWorker(),
        ui=FakeWorker(),
        brain=FakeWorker(),
        audio=FakeWorker(),
        run_async=False,
        runtime_log=log,
    )
    yield controller
    log.close()


def test_notice_mirrors_into_runtime_log_with_detail(flows):
    """Bubble notices are recorded with severity and collapsed detail."""
    flows._notice(
        "TTS synthesize failed: boom",
        severity="error",
        technical_detail="Traceback (most recent call last):\n  File \"tts.py\", line 5\nRuntimeError: boom",
    )

    events = flows.runtime_log.snapshot()
    assert len(events) == 1
    assert events[0]["source"] == "assistant"
    assert events[0]["severity"] == "error"
    assert events[0]["title"] == "TTS synthesize failed: boom"
    assert "Recommendation:" in events[0]["detail"]
    assert "RuntimeError: boom" in events[0]["detail"]

    notice_calls = flows.ui.calls_for("ui.reply.notice")
    assert len(notice_calls) == 1
    payload = notice_calls[0]["params"]
    assert payload["log_mirrored"] is True
    # The transient bubble shows the friendly text, not the traceback.
    assert "Traceback" not in payload["text"]
    assert payload["severity"] == "error"


def test_notice_defaults_to_warning_severity(flows):
    """Plain notices land as warnings so they stand out in Runtime Status."""
    flows._notice("Didn't catch any speech.")

    events = flows.runtime_log.snapshot()
    assert events[0]["severity"] == "warning"


def test_ui_log_event_routes_into_runtime_log(flows):
    """Structured ui.log.event payloads from the UI worker are recorded."""
    flows._on_ui_log_event(
        {
            "source": "installer",
            "severity": "error",
            "title": "Kokoro install failed: pip exploded",
            "detail": "error: no matching distribution",
        }
    )
    flows._on_ui_log_event({"title": ""})  # empty titles are dropped

    events = flows.runtime_log.snapshot()
    assert len(events) == 1
    assert events[0]["source"] == "installer"
    assert events[0]["severity"] == "error"
    assert events[0]["title"] == "Kokoro install failed: pip exploded"
    assert events[0]["detail"] == "error: no matching distribution"


def test_open_runtime_status_sends_worker_rows_and_events(flows):
    """The Runtime Status window receives the aggregated event backlog."""
    flows.runtime_log.append("brain", "error", "brain encountered an error: boom", detail="tb")

    flows.open_runtime_status()

    show_calls = flows.ui.calls_for("ui.runtime_status.show")
    assert len(show_calls) == 1
    params = show_calls[0]["params"]
    assert [worker["name"] for worker in params["workers"]] == ["native", "ui", "brain", "audio"]
    assert len(params["events"]) == 1
    assert params["events"][0]["title"] == "brain encountered an error: boom"


def test_window_open_close_toggles_live_publishing(flows):
    """New events stream to the window only while it is open."""
    flows._on_runtime_status_opened({})
    flows.runtime_log.append("audio", "info", "warmup done")
    flows.runtime_log._flush()

    append_calls = flows.ui.calls_for("ui.runtime_status.append")
    assert len(append_calls) == 1
    assert append_calls[0]["wait"] is False
    assert [event["title"] for event in append_calls[0]["params"]["events"]] == ["warmup done"]

    flows._on_runtime_status_closed({})
    flows.runtime_log.append("audio", "info", "later event")
    flows.runtime_log._flush()
    assert len(flows.ui.calls_for("ui.runtime_status.append")) == 1


def test_health_results_are_recorded(flows, monkeypatch):
    """Setup-check outcomes land in the runtime event log."""
    import core.setup_check as setup_check

    rows = [
        {"status": "ok", "name": "LLM", "message": "ready", "recommendation": ""},
        {"status": "fail", "name": "Microphone", "message": "no device", "recommendation": "plug one in"},
    ]
    monkeypatch.setattr(setup_check, "run_setup_check", lambda: rows)

    flows._on_health_requested({})

    events = flows.runtime_log.snapshot()
    assert events[0]["title"] == "Setup check ran: 2 check(s), 1 issue(s)."
    assert events[1]["severity"] == "error"
    assert events[1]["title"] == "Setup check - Microphone: no device"
    assert events[1]["detail"] == "plug one in"
