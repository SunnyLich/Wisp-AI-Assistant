"""
IPC harness verification for core.macos_helper.

These exercise the parent↔worker transport using the platform-agnostic ``ping``
handler, so they run on any OS (the STT handler's native deps are imported
lazily and are never touched here). This proves the foundation — framing,
request/response correlation, process reuse, error propagation — independently
of macOS-only audio/Whisper work.

Run directly:   python tests/test_macos_helper_ipc.py
Run via pytest: pytest tests/test_macos_helper_ipc.py
"""
from __future__ import annotations

import os
import sys

# Allow `python tests/test_macos_helper_ipc.py` from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.macos_helper.client import HelperClient, HelperError


def test_ping_round_trip_and_echo():
    client = HelperClient()
    try:
        result = client.call("ping", {"value": "hello-from-parent"}, timeout=15)
        assert isinstance(result, dict)
        assert result.get("pong") is True
        assert result.get("value") == "hello-from-parent"
        assert isinstance(result.get("pid"), int) and result["pid"] > 0
    finally:
        client.shutdown()


def test_worker_process_is_reused_across_calls():
    client = HelperClient()
    try:
        first = client.call("ping", timeout=15)
        second = client.call("ping", timeout=15)
        # Same worker answered both → no respawn between calls.
        assert first["pid"] == second["pid"]
    finally:
        client.shutdown()


def test_unknown_method_raises_helper_error():
    client = HelperClient()
    try:
        raised = False
        try:
            client.call("does.not.exist", timeout=5)
        except HelperError:
            raised = True
        assert raised, "unknown method should raise HelperError"
    finally:
        client.shutdown()


def test_calls_fail_fast_after_shutdown():
    client = HelperClient()
    client.call("ping", timeout=15)   # start the worker
    client.shutdown()                  # then stop it (sets the shutting-down flag)
    # A call after shutdown must error promptly (no respawn, no hang). We pass a
    # generous timeout precisely so a hang would be obvious rather than masked.
    raised = False
    try:
        client.call("ping", timeout=15)
    except HelperError:
        raised = True
    assert raised, "call after shutdown should raise HelperError immediately"


def _run_directly() -> int:
    tests = [
        test_ping_round_trip_and_echo,
        test_worker_process_is_reused_across_calls,
        test_unknown_method_raises_helper_error,
        test_calls_fail_fast_after_shutdown,
    ]
    passed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {fn.__name__}")
            passed += 1
    print(f"--- {passed}/{len(tests)} passed ---")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run_directly())
