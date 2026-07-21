"""Real-entry acceptance workflows for native desktop integration.

The operating system itself is the deterministic boundary in these tests.  The
native worker, platform routing, hotkey callback wiring, selection routing,
clipboard policy, and paste state machine remain production code.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from runtime.workers import native_host

pytestmark = pytest.mark.workflow


class _ImmediateThread:
    """Run short deterministic helper-reader threads inline."""

    def __init__(self, target, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


@pytest.mark.parametrize(
    ("platform", "backend_name"),
    (("win32", "_Win32Impl"), ("linux", "_PynputImpl")),
)
def test_native_hotkey_direct_platform_runtime_event_matrix(
    platform, backend_name, monkeypatch
):
    """Windows/Linux registration feeds real native-worker events and stops."""
    import config
    from core import hotkeys

    for name in (
        "HOTKEY_ADD_CONTEXT",
        "HOTKEY_ADD_CONTEXT_2",
        "HOTKEY_CLEAR_CONTEXT",
        "HOTKEY_CLEAR_CONTEXT_2",
        "HOTKEY_SNIP",
        "HOTKEY_SNIP_2",
        "HOTKEY_READ_SELECTION_ALOUD",
        "HOTKEY_READ_SELECTION_ALOUD_2",
        "HOTKEY_VOICE",
        "HOTKEY_VOICE_2",
        "HOTKEY_DICTATE",
        "HOTKEY_DICTATE_2",
        "HOTKEY_VOICE_LIVE",
        "HOTKEY_VOICE_LIVE_2",
    ):
        monkeypatch.setattr(config, name, "", raising=False)
    monkeypatch.setattr(
        config,
        "CALLER_ROWS",
        [{"enabled": True, "hotkey": "ctrl+alt+q", "hotkey_2": ""}],
        raising=False,
    )
    monkeypatch.setattr(hotkeys, "_IS_WIN", platform == "win32")
    monkeypatch.setattr(hotkeys, "_IS_MAC", False)

    backend = getattr(hotkeys, backend_name)
    lifecycle: list[str] = []

    def start(self):
        lifecycle.append(f"start:{type(self).__name__}")
        return True

    def status(self):
        return {
            "started": True,
            "registered": len(self._hotkey_defs),
            "requested": len(self._hotkey_defs),
            "reason": "OS registration boundary accepted all bindings",
        }

    def stop(_self):
        lifecycle.append(f"stop:{backend.__name__}")

    # RegisterHotKey/pynput are the external OS boundary. Backend selection,
    # binding construction, event conversion, lifetime, and worker state are real.
    monkeypatch.setattr(backend, "start", start)
    monkeypatch.setattr(backend, "status", status, raising=False)
    monkeypatch.setattr(backend, "stop", stop)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "_hotkeys", None)

    events: list[tuple[str, dict]] = []
    native_host.set_event_sink(
        lambda name, data, _request_id: events.append((name, data))
    )
    try:
        result = native_host.hotkeys_start(
            addon_hotkeys=[
                {
                    "hotkey": "ctrl+alt+h",
                    "addon_id": "acceptance.addon",
                    "id": "run",
                }
            ]
        )
        assert result["ok"] is True
        assert result["registered"] == result["requested"] == 2

        listener = native_host._hotkeys.listener
        assert type(listener._impl).__name__ == backend_name
        callbacks = dict(listener._hotkey_defs)
        callbacks["ctrl+alt+q"]()
        callbacks["ctrl+alt+h"]()

        assert events == [
            ("native.hotkey", {"kind": "caller", "index": 0}),
            (
                "native.hotkey",
                {
                    "kind": "addon",
                    "addon_id": "acceptance.addon",
                    "hotkey_id": "run",
                },
            ),
        ]
        stopped = native_host.hotkeys_stop()
        assert stopped == {"stopped": True, "ok": True}
        assert lifecycle == [f"start:{backend_name}", f"stop:{backend_name}"]
    finally:
        native_host.hotkeys_stop()
        native_host.set_event_sink(lambda _name, _data, _request_id: None)


def test_native_hotkey_macos_helper_runtime_event_and_cleanup(monkeypatch):
    """The macOS helper status/event pipe reaches the native runtime entry."""
    messages = b"\n".join(
        (
            json.dumps(
                {
                    "status": "started",
                    "started": True,
                    "backend": "carbon-helper",
                    "registered": 1,
                    "requested": 1,
                }
            ).encode(),
            json.dumps(
                {
                    "event": "native.hotkey",
                    "data": {
                        "kind": "addon",
                        "addon_id": "acceptance.addon",
                        "hotkey_id": "run",
                    },
                }
            ).encode(),
            b"",
        )
    )

    class Proc:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(messages)
            self.stderr = io.BytesIO()
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            raise AssertionError("responsive helper should not need a kill")

    proc = Proc()
    stale_cleanup: list[list[str]] = []
    monkeypatch.setattr(native_host, "IS_MAC", True)
    monkeypatch.setattr(native_host, "_hotkeys", None)
    monkeypatch.setattr(native_host.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(native_host.subprocess, "Popen", lambda *_a, **_k: proc)
    monkeypatch.setattr(
        native_host.subprocess,
        "run",
        lambda command, **_kwargs: stale_cleanup.append(command)
        or SimpleNamespace(returncode=0),
    )

    events: list[tuple[str, dict]] = []
    native_host.set_event_sink(
        lambda name, data, _request_id: events.append((name, data))
    )
    try:
        result = native_host.hotkeys_start(
            addon_hotkeys=[
                {
                    "hotkey": "cmd+shift+h",
                    "addon_id": "acceptance.addon",
                    "id": "run",
                }
            ]
        )
        assert result["ok"] is True
        assert result["backend"] == "carbon-helper"
        assert events == [
            (
                "native.hotkey",
                {
                    "kind": "addon",
                    "addon_id": "acceptance.addon",
                    "hotkey_id": "run",
                },
            )
        ]
        assert stale_cleanup == [
            ["/usr/bin/pkill", "-f", "runtime.workers.hotkey_helper"]
        ]
        assert native_host.hotkeys_stop()["ok"] is True
        assert proc.terminated is True
    finally:
        native_host.hotkeys_stop()
        native_host.set_event_sink(lambda _name, _data, _request_id: None)


@pytest.mark.parametrize(
    ("route", "expected"),
    (
        ("macos_ax", "macOS AX selection"),
        ("linux_wayland_atspi", "Wayland AT-SPI selection"),
        ("linux_x11_primary", "X11 PRIMARY selection"),
        ("windows_uia", "Windows UIA selection"),
    ),
)
def test_native_selected_text_platform_route_matrix(route, expected, monkeypatch):
    """Every supported accessibility route returns through native.selected_text."""
    from core import capture
    from core.platform import linux_atspi

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(native_host, "IS_MAC", route == "macos_ax")
    monkeypatch.setattr(native_host, "IS_WIN", route == "windows_uia")

    kwargs = {"allow_clipboard_fallback": False}
    if route == "macos_ax":
        monkeypatch.setattr(native_host, "_ax_selected_text", lambda: expected)
    elif route == "linux_wayland_atspi":
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setattr(linux_atspi, "get_selected_text", lambda: expected)
        kwargs["require_active_owner"] = True
        kwargs["active_pid"] = 410
    elif route == "linux_x11_primary":
        monkeypatch.setattr(
            capture,
            "_get_primary_selection_linux",
            lambda **_kwargs: expected,
        )
        kwargs["require_active_owner"] = True
        kwargs["active_pid"] = 411
    else:
        monkeypatch.setattr(capture, "_get_selected_text_uia", lambda: expected)

    assert native_host.selected_text(**kwargs) == expected


@pytest.mark.parametrize(
    "method",
    ("linux_clipboard", "windows_uia", "macos_ax", "macos_clipboard"),
)
@pytest.mark.parametrize("restore_clipboard", (False, True))
def test_native_paste_method_by_clipboard_restore_matrix(
    method, restore_clipboard, monkeypatch
):
    """All native paste methods obey both clipboard-retention policies."""
    from core import platform_utils
    from core.platform import macos_native

    original = "clipboard before paste"
    clipboard_writes: list[str] = []
    clipboard_reads: list[str] = []
    focus_events: list[object] = []
    key_events: list[str] = []

    def clipboard_get():
        clipboard_reads.append(original)
        return {"text": original, "ok": True}

    def clipboard_set(text=""):
        clipboard_writes.append(text)
        return {"ok": True}

    monkeypatch.setattr(native_host, "clipboard_get", clipboard_get)
    monkeypatch.setattr(native_host, "clipboard_set", clipboard_set)
    monkeypatch.setattr(native_host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        platform_utils,
        "set_foreground_window",
        lambda target: focus_events.append(target),
    )
    monkeypatch.setattr(
        platform_utils, "send_keys", lambda combo: key_events.append(combo)
    )
    monkeypatch.setattr(native_host, "IS_MAC", method.startswith("macos"))
    monkeypatch.setattr(native_host, "IS_WIN", method == "windows_uia")

    focus_token = 0
    if method == "windows_uia":
        class Element:
            def SetFocus(self):
                focus_events.append("uia-focus")

        class TextRange:
            def Select(self):
                focus_events.append("uia-select")

        focus_token = 91
        monkeypatch.setattr(
            native_host,
            "_focus_cache",
            {
                "token": focus_token,
                "kind": "win-uia",
                "element": Element(),
                "range": TextRange(),
            },
        )
    elif method == "macos_ax":
        focus_token = 92
        monkeypatch.setattr(
            native_host,
            "_ax_apply_selected_text",
            lambda token, text: focus_events.append(("ax", token, text))
            or {"ok": True},
        )
    elif method == "macos_clipboard":
        monkeypatch.setattr(
            native_host,
            "_activate_pid",
            lambda pid: focus_events.append(pid)
            or {
                "confirmed": True,
                "frontmost_pid": pid,
                "app_name": "Target Editor",
                "error": "",
            },
        )
        monkeypatch.setattr(
            macos_native,
            "set_clipboard_text",
            lambda text: clipboard_writes.append(text) or True,
        )
        monkeypatch.setattr(
            macos_native,
            "send_key_combo",
            lambda combo: key_events.append(combo) or True,
        )

    result = native_host.paste_text(
        "generated reply",
        target_pid=733,
        focus_token=focus_token,
        restore_clipboard=restore_clipboard,
    )

    assert result["ok"] is True
    if method == "macos_ax":
        assert result["method"] == "ax"
        assert result["keystroke_sent"] is False
        assert clipboard_reads == []
        assert clipboard_writes == []
        assert focus_events == [("ax", 92, "generated reply")]
        return

    assert result["keystroke_sent"] is True
    assert "generated reply" in clipboard_writes
    if restore_clipboard:
        assert clipboard_reads == [original]
        assert clipboard_writes[-1] == original
        assert result["clipboard_restored"] is True
    else:
        assert clipboard_reads == []
        assert clipboard_writes[-1] == "generated reply"
        assert result["clipboard_restored"] is False
