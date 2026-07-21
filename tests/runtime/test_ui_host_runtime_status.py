"""Tests for the Runtime Status window's event rendering helpers."""

from __future__ import annotations

import time


def test_runtime_event_headline_formats_severity_and_count() -> None:
    """Headlines carry timestamp, source, severity marker, and repeat count."""
    from runtime.workers import ui_host

    ts = time.time()
    stamp = time.strftime("%H:%M:%S", time.localtime(ts))

    error = ui_host._runtime_event_headline(
        {"ts": ts, "source": "brain", "severity": "error", "title": "brain encountered an error: boom", "count": 1}
    )
    assert error == f"[{stamp}] [brain] ERROR brain encountered an error: boom"

    warn = ui_host._runtime_event_headline(
        {"ts": ts, "source": "assistant", "severity": "warning", "title": "mic busy", "count": 3}
    )
    assert warn == f"[{stamp}] [assistant] WARN mic busy (x3)"

    info = ui_host._runtime_event_headline(
        {"ts": ts, "source": "installer", "severity": "info", "title": "Kokoro install finished."}
    )
    assert info == f"[{stamp}] [installer] Kokoro install finished."


def test_runtime_status_text_includes_workers_events_and_details() -> None:
    """The copy-all text keeps worker rows and indents collapsed detail lines."""
    from runtime.workers import ui_host

    workers = [
        {"name": "brain", "alive": True, "pid": 123, "module": "runtime.workers.brain_host"},
        {"name": "audio", "alive": False, "pid": None, "module": ""},
    ]
    events = [
        {
            "ts": time.time(),
            "source": "brain",
            "severity": "error",
            "title": "brain encountered an error: ValueError: bad",
            "detail": "Traceback (most recent call last):\nValueError: bad",
            "count": 1,
        }
    ]

    text = ui_host.QtProtocolHost._runtime_status_text(None, workers, "C:/logs", events)

    assert "[brain] running pid=123 module=runtime.workers.brain_host" in text
    assert "[audio] stopped pid=-" in text
    assert "Log directory: C:/logs" in text
    assert "brain encountered an error: ValueError: bad" in text
    assert "    Traceback (most recent call last):" in text
    assert "    ValueError: bad" in text


def test_runtime_worker_summary_translates_states(monkeypatch) -> None:
    """The header summary translates running/stopped labels."""
    from runtime.workers import ui_host

    monkeypatch.setattr(ui_host, "t", lambda text: f"tx:{text}")

    summary = ui_host.QtProtocolHost._runtime_worker_summary(
        [
            {"name": "ui", "alive": True, "pid": 7},
            {"name": "native", "alive": False, "pid": None},
        ],
        "C:/logs",
    )

    assert "[ui] tx:running  pid=7" in summary
    assert "[native] tx:stopped  pid=-" in summary
    assert "tx:Log directory: C:/logs" in summary
