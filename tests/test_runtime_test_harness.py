"""Self-tests for the reusable workflow runtime harness."""

from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

from scripts.runtime_test_harness import (
    RuntimeFailureCollector,
    RuntimeStateInspector,
    wait_until,
)


def test_failure_collector_records_main_thread_and_thread_hook_failures():
    collector = RuntimeFailureCollector(capture_qt=False, chain_hooks=False)
    original_main_hook = sys.excepthook
    original_thread_hook = threading.excepthook
    collector.install()
    try:
        main_error = RuntimeError("main callback failed")
        collector._main_hook(RuntimeError, main_error, main_error.__traceback__)
        thread_error = ValueError("reader failed")
        collector._thread_hook(
            SimpleNamespace(
                exc_type=ValueError,
                exc_value=thread_error,
                exc_traceback=thread_error.__traceback__,
                thread=SimpleNamespace(name="worker-reader"),
            )
        )
    finally:
        collector.uninstall()

    with pytest.raises(AssertionError, match="main callback failed") as error:
        collector.assert_clean()
    assert "thread exception (worker-reader)" in str(error.value)
    assert sys.excepthook is original_main_hook
    assert threading.excepthook is original_thread_hook


def test_failure_collector_records_unraisable_and_asyncio_failures():
    collector = RuntimeFailureCollector(capture_qt=False, chain_hooks=False)
    unraisable = OSError("destructor failed")
    collector._unraisable_hook(
        SimpleNamespace(
            exc_type=OSError,
            exc_value=unraisable,
            err_msg="Exception ignored in cleanup",
        )
    )

    loop = asyncio.new_event_loop()
    try:
        with collector.watch_asyncio_loop(loop):
            loop.call_exception_handler(
                {"message": "task was never retrieved", "exception": RuntimeError("async failed")}
            )
    finally:
        loop.close()

    with pytest.raises(AssertionError, match="destructor failed") as error:
        collector.assert_clean()
    assert "asyncio exception" in str(error.value)
    assert "async failed" in str(error.value)


def test_wait_until_pumps_events_and_reports_timeout():
    state = {"pumps": 0}

    wait_until(
        lambda: state["pumps"] >= 2,
        pump=lambda: state.__setitem__("pumps", state["pumps"] + 1),
        timeout=0.2,
        description="two event-loop pumps",
    )

    with pytest.raises(AssertionError, match="waiting for impossible state"):
        wait_until(lambda: False, timeout=0.01, interval=0.001, description="impossible state")


def test_state_inspector_validates_json_and_required_cleanup(tmp_path):
    valid_root = tmp_path / "valid"
    valid_root.mkdir()
    (valid_root / "state.json").write_text('{"ok": true}', encoding="utf-8")

    inspector = RuntimeStateInspector(settle_timeout=0).start()
    inspector.validate_json_under(valid_root)
    inspector.assert_clean()

    (valid_root / "broken.json").write_text("{broken", encoding="utf-8")
    leftover = tmp_path / "installer.partial"
    leftover.mkdir()
    inspector.validate_json_under(valid_root)
    inspector.require_removed(leftover)

    with pytest.raises(AssertionError, match="invalid JSON") as error:
        inspector.assert_clean()
    assert "temporary path still exists" in str(error.value)


def test_state_inspector_rejects_worker_crash_diagnostics():
    worker = SimpleNamespace(
        stderr_tail=lambda _limit: "[crash] unhandled exception in thread model-reader"
    )
    inspector = RuntimeStateInspector(settle_timeout=0).start()
    inspector.watch_worker("brain", worker)

    with pytest.raises(AssertionError, match="brain worker emitted") as error:
        inspector.assert_clean()
    assert "[crash] unhandled" in str(error.value)
