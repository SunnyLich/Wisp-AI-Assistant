"""Protocol verification for the macOS Qt UI host (the Swift <-> Qt seam).

Spawns ``wisp_qt_ui_host.py`` headless (``QT_QPA_PLATFORM=offscreen``) and drives
its newline-delimited JSON protocol over stdin/stdout, the same contract the
Swift ``QtUIBridge`` speaks. Like the brain-host test, it runs on any OS: it only
needs PySide6 in the spawned interpreter, no LLM stack, models, or API keys.

The point is to prove the transport (startup ``ui.ready``, per-command
``ui.ok``/``ui.error``, error isolation, clean shutdown) before any Swift is
compiled -- and to guard the portable stdin reader, since QSocketNotifier on a
pipe fd silently no-ops on Windows.

Run directly:   python macos/ui_host/tests/test_qt_ui_host.py
Run via pytest: pytest macos/ui_host/tests/test_qt_ui_host.py
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HOST = _REPO_ROOT / "macos" / "ui_host" / "wisp_qt_ui_host.py"

# Generous because the child imports PySide6 and applies the Qt theme on startup.
_READY_TIMEOUT = 40.0
_CMD_TIMEOUT = 30.0


class UIHost:
    """Test stand-in for the Swift QtUIBridge: spawn the host headless, send
    commands, and collect the ``ui.*`` events it writes back."""

    def __init__(self) -> None:
        env = {
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONUNBUFFERED": "1",
            "WISP_QT_UI_HOST": "1",
            "WISP_REPO_ROOT": str(_REPO_ROOT),
        }
        self._proc = subprocess.Popen(
            [sys.executable, str(_HOST)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_REPO_ROOT),
            env=env,
            bufsize=0,
        )
        self._ready = threading.Event()
        self._results: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._write_lock = threading.Lock()
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        out = self._proc.stdout
        assert out is not None
        for raw in iter(out.readline, b""):
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # the host logs to stderr; stray stdout is ignored
            event = msg.get("event")
            if event == "ui.ready":
                self._ready.set()
            elif event in ("ui.ok", "ui.error"):
                self._results.put(msg)

    def wait_ready(self, timeout: float = _READY_TIMEOUT) -> None:
        if not self._ready.wait(timeout):
            raise TimeoutError("host never emitted ui.ready")

    def send(self, method: str, params: dict | None = None) -> None:
        line = json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
        with self._write_lock:
            assert self._proc.stdin is not None
            self._proc.stdin.write(line)
            self._proc.stdin.flush()

    def next_result(self, timeout: float = _CMD_TIMEOUT) -> dict[str, Any]:
        try:
            return self._results.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("no ui.ok/ui.error from host") from exc

    def command(self, method: str, params: dict | None = None) -> dict[str, Any]:
        self.send(method, params)
        return self.next_result()

    def shutdown(self) -> int:
        try:
            self.send("__shutdown__")
            return self._proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self._proc.kill()
            return self._proc.wait(timeout=5)


def test_emits_ready_on_startup():
    host = UIHost()
    try:
        host.wait_ready()
    finally:
        host.shutdown()


def test_unknown_method_errors_without_killing_host():
    host = UIHost()
    try:
        host.wait_ready()
        bad = host.command("nope.not.a.method")
        assert bad["event"] == "ui.error"
        assert bad.get("error")
        # The host must survive a bad command and keep serving the next one.
        ok = host.command("ui.reload_config")
        assert ok["event"] == "ui.ok", ok
    finally:
        host.shutdown()


def test_reload_config_acknowledges():
    host = UIHost()
    try:
        host.wait_ready()
        res = host.command("ui.reload_config")
        assert res["event"] == "ui.ok", res
        assert res.get("method") == "ui.reload_config"
    finally:
        host.shutdown()


# Windows that construct quickly and reliably headless (<1s each, measured).
# Settings, Chat, and Memory are deliberately excluded: their construction blocks
# the host's single main thread for tens of seconds (Settings enumerates audio
# devices / model lists; Chat pulls heavy view deps; Memory cold-loads the
# chromadb vector store), which would hang this test. That a slow window can
# freeze the single-threaded UI host is a real risk worth its own coverage, but
# this protocol test should stay fast and deterministic.
_LIGHTWEIGHT_WINDOWS = (
    "ui.show_plugin_manager",
    "ui.show_agent_task",
    "ui.show_agent_history",
)


def test_lightweight_product_windows_open():
    host = UIHost()
    try:
        host.wait_ready()
        for method in _LIGHTWEIGHT_WINDOWS:
            res = host.command(method)
            assert res["event"] == "ui.ok", f"{method}: {res}"
            assert res.get("method") == method
        # Re-issue one to prove the window is reusable and the host stays healthy
        # after constructing several windows in one session.
        again = host.command(_LIGHTWEIGHT_WINDOWS[0])
        assert again["event"] == "ui.ok", again
    finally:
        host.shutdown()


def test_shutdown_exits_cleanly():
    host = UIHost()
    host.wait_ready()
    code = host.shutdown()
    assert code == 0, f"expected clean exit, got {code}"


def _run_directly() -> int:
    tests = [
        test_emits_ready_on_startup,
        test_unknown_method_errors_without_killing_host,
        test_reload_config_acknowledges,
        test_lightweight_product_windows_open,
        test_shutdown_exits_cleanly,
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
