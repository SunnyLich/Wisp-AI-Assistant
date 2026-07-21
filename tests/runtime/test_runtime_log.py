"""Tests for the supervisor runtime event log aggregation."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import pytest

from runtime.supervisor.runtime_log import RuntimeEventLog, RuntimeLogHandler


@pytest.fixture()
def runtime_log():
    """A runtime event log that never scans real installer status files."""
    log = RuntimeEventLog()
    log._installer_status_files = staticmethod(lambda: [])
    yield log
    log.close()


def test_append_records_sanitized_events(runtime_log):
    """Events carry sequence, severity normalization, and trimmed titles."""
    event = runtime_log.append("brain", "CRITICAL", "  boom \n second line ", detail=" tb ")

    assert event["seq"] == 1
    assert event["source"] == "brain"
    assert event["severity"] == "error"
    assert event["title"] == "boom second line"
    assert event["detail"] == "tb"
    assert runtime_log.snapshot()[0]["count"] == 1


def test_append_coalesces_identical_unpublished_repeats(runtime_log):
    """A repeating identical event bumps its counter instead of flooding."""
    runtime_log.append("audio", "warning", "mic busy")
    runtime_log.append("audio", "warning", "mic busy")
    runtime_log.append("audio", "warning", "mic busy")

    events = runtime_log.snapshot()
    assert len(events) == 1
    assert events[0]["count"] == 3


def test_append_keeps_distinct_events_separate(runtime_log):
    """Different titles or sources stay separate entries."""
    runtime_log.append("audio", "warning", "mic busy")
    runtime_log.append("brain", "warning", "mic busy")
    runtime_log.append("brain", "warning", "other")

    assert len(runtime_log.snapshot()) == 3


def test_ring_buffer_caps_retained_events():
    """The log never grows beyond its bounded ring."""
    log = RuntimeEventLog(max_events=10)
    log._installer_status_files = staticmethod(lambda: [])
    try:
        for index in range(25):
            log.append("ui", "info", f"event {index}")
        events = log.snapshot()
        assert len(events) == 10
        assert events[-1]["title"] == "event 24"
    finally:
        log.close()


def test_stderr_sink_folds_traceback_into_single_error_event(runtime_log):
    """A multi-line traceback becomes one collapsed error package."""
    sink = runtime_log.stderr_sink("brain")
    sink("Traceback (most recent call last):")
    sink('  File "brain.py", line 10, in query')
    sink("    raise ValueError('bad model')")
    sink("ValueError: bad model")
    sink.flush_idle(now=time.monotonic() + 5)

    events = runtime_log.snapshot()
    assert len(events) == 1
    assert events[0]["severity"] == "error"
    assert events[0]["title"] == "brain encountered an error: ValueError: bad model"
    assert events[0]["detail"].splitlines()[0] == "Traceback (most recent call last):"
    assert 'line 10' in events[0]["detail"]


def test_stderr_sink_merges_chained_tracebacks(runtime_log):
    """Chained tracebacks ("During handling ...") stay one event."""
    sink = runtime_log.stderr_sink("audio")
    sink("Traceback (most recent call last):")
    sink('  File "a.py", line 1, in go')
    sink("KeyError: 'x'")
    sink("During handling of the above exception, another exception occurred:")
    sink("Traceback (most recent call last):")
    sink('  File "a.py", line 5, in go')
    sink("RuntimeError: fallback failed")
    sink.flush_idle(now=time.monotonic() + 5)

    events = runtime_log.snapshot()
    assert len(events) == 1
    assert events[0]["title"] == "audio encountered an error: RuntimeError: fallback failed"
    assert "KeyError: 'x'" in events[0]["detail"]


def test_stderr_sink_classifies_plain_lines(runtime_log):
    """Single lines map onto info/warning/error severities."""
    sink = runtime_log.stderr_sink("ui")
    sink("[WARNING] wisp.ui_host: bubble notice: mic unplugged")
    sink("[ERROR] wisp.ui_host: something broke")
    sink("plain progress line")

    severities = [event["severity"] for event in runtime_log.snapshot()]
    assert severities == ["warning", "error", "info"]


def test_publisher_receives_only_new_events_in_batches(runtime_log):
    """Snapshot carries the backlog; the publisher only gets later events."""
    batches: list[list[dict[str, Any]]] = []
    runtime_log.append("ui", "info", "before open")
    runtime_log.set_publisher(batches.append)
    runtime_log.enable_publishing()
    runtime_log.append("ui", "info", "after open")
    runtime_log.append("brain", "info", "second")
    runtime_log._flush()

    assert len(batches) == 1
    assert [event["title"] for event in batches[0]] == ["after open", "second"]

    runtime_log._flush()
    assert len(batches) == 1  # nothing new -> no extra publish


def test_publisher_failure_disables_publishing(runtime_log):
    """A dead Runtime Status window stops the stream instead of looping."""

    def _explode(_events):
        raise RuntimeError("ui gone")

    runtime_log.set_publisher(_explode)
    runtime_log.enable_publishing()
    runtime_log.append("ui", "info", "one")
    runtime_log._flush()
    runtime_log.append("ui", "info", "two")
    runtime_log._flush()

    assert runtime_log._publish_enabled is False


def test_logging_handler_captures_exceptions_with_traceback(runtime_log):
    """Supervisor log records land in the event log, tracebacks included."""
    logger = logging.getLogger("wisp.test.runtime_log_handler")
    logger.propagate = False
    logger.setLevel(logging.INFO)  # the supervisor root logger runs at INFO
    handler = RuntimeLogHandler(runtime_log)
    logger.addHandler(handler)
    try:
        try:
            raise ValueError("kaput")
        except ValueError:
            logger.exception("worker call failed: ui.show_overlay")
        logger.info("plain info line")
    finally:
        logger.removeHandler(handler)

    events = runtime_log.snapshot()
    assert events[0]["severity"] == "error"
    assert events[0]["title"] == "worker call failed: ui.show_overlay"
    assert "ValueError: kaput" in events[0]["detail"]
    assert "Traceback" in events[0]["detail"]
    assert events[1]["title"] == "plain info line"
    assert events[1]["severity"] == "info"


def test_logging_handler_skips_worker_stderr_echoes(runtime_log):
    """Worker stderr echoed via logging must not double-ingest."""
    logger = logging.getLogger("wisp.worker_stderr")
    old_propagate, old_level = logger.propagate, logger.level
    logger.propagate = False
    logger.setLevel(logging.INFO)  # ensure the record reaches the handler
    handler = RuntimeLogHandler(runtime_log)
    logger.addHandler(handler)
    try:
        logger.info("[brain] [plugin] loaded")
    finally:
        logger.removeHandler(handler)
        # This logger is shared process-wide; restore it for other tests
        # (test_supervisor_ipc asserts on its records via caplog propagation).
        logger.propagate = old_propagate
        logger.setLevel(old_level)

    assert runtime_log.snapshot() == []


def test_ingest_installer_statuses_reports_failures_with_log_tail(tmp_path, monkeypatch):
    """Detached installer outcomes become runtime events exactly once."""
    installers = tmp_path / "installers"
    installers.mkdir()
    status_path = installers / "kokoro-install.status.json"
    status_path.write_text(
        json.dumps({"ok": False, "message": "pip failed", "updated_at": time.time()}),
        encoding="utf-8",
    )
    (installers / "kokoro-install.log").write_text(
        "collecting kokoro\nerror: no matching distribution\n", encoding="utf-8"
    )

    log = RuntimeEventLog()
    monkeypatch.setattr(
        RuntimeEventLog,
        "_installer_status_files",
        staticmethod(lambda: [status_path]),
    )
    try:
        assert log.ingest_installer_statuses() == 1
        events = log.snapshot()
        assert len(events) == 1
        assert events[0]["source"] == "installer"
        assert events[0]["severity"] == "error"
        assert "kokoro install failed: pip failed" in events[0]["title"]
        assert "no matching distribution" in events[0]["detail"]

        # A second scan of the unchanged file must not duplicate the event.
        assert log.ingest_installer_statuses() == 0
        assert len(log.snapshot()) == 1
    finally:
        log.close()


def test_ingest_installer_statuses_reports_success(tmp_path, monkeypatch):
    """Successful installer statuses produce info events."""
    status_path = tmp_path / "stt-install.status.json"
    status_path.write_text(
        json.dumps({"ok": True, "message": "STT installed", "updated_at": time.time()}),
        encoding="utf-8",
    )

    log = RuntimeEventLog()
    monkeypatch.setattr(
        RuntimeEventLog,
        "_installer_status_files",
        staticmethod(lambda: [status_path]),
    )
    try:
        assert log.ingest_installer_statuses() == 1
        events = log.snapshot()
        assert events[0]["severity"] == "info"
        assert "stt install finished: STT installed" in events[0]["title"]
    finally:
        log.close()
