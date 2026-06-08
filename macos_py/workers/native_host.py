"""wisp-native worker: macOS permissions, hotkeys, context, capture, clipboard."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from macos_py.service_host import run_host

IS_MAC = sys.platform == "darwin"
_emit: Callable[[str, Any, Any], None] | None = None
_hotkeys = None


def set_event_sink(fn: Callable[[str, Any, Any], None]) -> None:
    global _emit
    _emit = fn


def _event(name: str, data: Any = None) -> None:
    if _emit is not None:
        _emit(name, data, None)


def _ax_trusted() -> bool | None:
    if not IS_MAC:
        return None
    try:
        import ctypes
        import ctypes.util

        app_services = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("ApplicationServices") or "ApplicationServices"
        )
        app_services.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(app_services.AXIsProcessTrusted())
    except Exception:
        return None


def _screen_trusted() -> bool | None:
    if not IS_MAC:
        return None
    try:
        import Quartz  # type: ignore

        fn = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        if fn is not None:
            return bool(fn())
    except Exception:
        return None
    return None


def _microphone_status() -> str:
    if not IS_MAC:
        return "unavailable"
    try:
        import AVFoundation  # type: ignore

        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio
        )
        names = {
            0: "not_determined",
            1: "restricted",
            2: "denied",
            3: "authorized",
        }
        return names.get(int(status), str(status))
    except Exception:
        return "unknown"


def permissions_snapshot() -> dict[str, Any]:
    return {
        "platform": sys.platform,
        "accessibility": _ax_trusted(),
        "screen_recording": _screen_trusted(),
        "microphone": _microphone_status(),
    }


def _active_app() -> dict[str, Any]:
    if not IS_MAC:
        return {}
    try:
        import AppKit  # type: ignore

        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return {}
        return {
            "name": str(app.localizedName() or ""),
            "bundle_id": str(app.bundleIdentifier() or ""),
            "pid": int(app.processIdentifier()),
        }
    except Exception:
        return {}


def _clipboard_text_primary() -> str | None:
    if not IS_MAC:
        return None
    try:
        import AppKit  # type: ignore

        pb = AppKit.NSPasteboard.generalPasteboard()
        value = pb.stringForType_(AppKit.NSPasteboardTypeString)
        return str(value) if value is not None else None
    except Exception:
        return None


def clipboard_get() -> dict[str, Any]:
    if IS_MAC:
        text = _clipboard_text_primary()
        if text is None:
            from core.platform import macos_native

            text = macos_native.get_clipboard_text()
    else:
        text = None
    return {"text": text or ""}


def _clipboard_set_primary(text: str) -> bool:
    if not IS_MAC:
        return False
    try:
        import AppKit  # type: ignore

        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        return bool(pb.setString_forType_(text or "", AppKit.NSPasteboardTypeString))
    except Exception:
        return False


def clipboard_set(text: str = "") -> dict[str, Any]:
    ok = _clipboard_set_primary(text)
    if not ok and IS_MAC:
        from core.platform import macos_native

        ok = macos_native.set_clipboard_text(text)
    return {"ok": bool(ok)}


def selected_text() -> str:
    if not IS_MAC:
        return ""
    # Primary AX selected-text support lands here. Until then, keep the old
    # clipboard-preserving fallback isolated in this native process.
    from core.platform import macos_native

    return macos_native.get_selected_text() or ""


def context_snapshot(include_clipboard: bool = True, include_selection: bool = True) -> dict[str, Any]:
    snapshot = {
        "platform": sys.platform,
        "active_app": _active_app(),
        "selected_text": "",
        "clipboard_text": "",
        "captured_at": time.time(),
    }
    if include_selection:
        snapshot["selected_text"] = selected_text()
    if include_clipboard:
        snapshot["clipboard_text"] = clipboard_get()["text"]
    return snapshot


def capture_fullscreen(path: str = "") -> dict[str, Any]:
    if not path:
        import tempfile

        path = str(Path(tempfile.gettempdir()) / f"wisp-capture-{int(time.time() * 1000)}.png")
    if not IS_MAC:
        return {"ok": False, "path": path, "error": "screen capture is only available on macOS"}
    from core.platform import macos_native

    ok = macos_native.capture_screen_to_file(path)
    return {"ok": ok, "path": path}


def capture_region(path: str = "", region: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path:
        import tempfile

        path = str(Path(tempfile.gettempdir()) / f"wisp-region-{int(time.time() * 1000)}.png")
    if not IS_MAC:
        return {"ok": False, "path": path, "error": "screen capture is only available on macOS"}
    from core.platform import macos_native

    ok = macos_native.capture_screen_to_file(path, region=region)
    return {"ok": ok, "path": path, "region": region}


def _activate_pid(pid: int) -> bool:
    if not IS_MAC or not pid:
        return False
    try:
        import AppKit  # type: ignore

        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        if app is None:
            return False
        opts = (
            AppKit.NSApplicationActivateIgnoringOtherApps
            | AppKit.NSApplicationActivateAllWindows
        )
        return bool(app.activateWithOptions_(opts))
    except Exception:
        return False


def paste_text(text: str = "", paste_combo: str = "cmd+v", target_pid: int = 0) -> dict[str, Any]:
    if not IS_MAC:
        return {"ok": False, "error": "pasteback is only available on macOS"}
    activated = _activate_pid(target_pid)
    if activated:
        time.sleep(0.15)
    from core.platform import macos_native

    return {"ok": macos_native.paste_text(text, paste_combo), "activated": activated}


def open_privacy_settings(pane: str = "Privacy") -> dict[str, Any]:
    if not IS_MAC:
        return {"ok": False, "error": "System Settings is only available on macOS"}
    urls = {
        "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
        "privacy": "x-apple.systempreferences:com.apple.preference.security?Privacy",
    }
    target = urls.get((pane or "privacy").strip().lower(), urls["privacy"])
    result = subprocess.run(["/usr/bin/open", target], check=False)
    return {"ok": result.returncode == 0, "url": target}


def hotkeys_start() -> dict[str, Any]:
    """Start global hotkeys in the native process.

    This deliberately keeps the legacy Carbon/pynput code out of the UI process.
    The first implementation reuses ``core.hotkeys`` as an isolated backend and
    emits protocol events back to the supervisor.
    """
    global _hotkeys
    if not IS_MAC:
        return {"started": False, "backend": "unavailable", "reason": "not macOS"}
    if _hotkeys is not None:
        return {"started": True, "backend": "existing"}
    import config
    from core.hotkeys import HotkeyListener

    caller_count = len(getattr(config, "CALLER_ROWS", []))
    callers = [
        (lambda idx=idx: _event("native.hotkey", {"kind": "caller", "index": idx}))
        for idx in range(caller_count)
    ]
    _hotkeys = HotkeyListener(
        on_callers=callers,
        on_add_context=lambda: _event("native.hotkey", {"kind": "add_context"}),
        on_clear_context=lambda: _event("native.hotkey", {"kind": "clear_context"}),
        on_snip=lambda: _event("native.hotkey", {"kind": "snip"}),
        on_voice_start=lambda: _event("native.hotkey", {"kind": "voice_start"}),
        on_voice_stop=lambda: _event("native.hotkey", {"kind": "voice_stop"}),
    )
    started = bool(_hotkeys.start())
    if not started:
        _hotkeys = None
    return {"started": started, "backend": "core.hotkeys"}


def hotkeys_stop() -> dict[str, Any]:
    global _hotkeys
    if _hotkeys is not None:
        _hotkeys.stop()
        _hotkeys = None
    return {"stopped": True}


HANDLERS = {
    "native.permissions.snapshot": permissions_snapshot,
    "native.hotkeys.start": hotkeys_start,
    "native.hotkeys.stop": hotkeys_stop,
    "native.context.snapshot": context_snapshot,
    "native.capture.fullscreen": capture_fullscreen,
    "native.capture.region": capture_region,
    "native.clipboard.get": clipboard_get,
    "native.clipboard.set": clipboard_set,
    "native.paste_text": paste_text,
    "native.open_privacy_settings": open_privacy_settings,
}


def main() -> int:
    return run_host(role="native", handlers=HANDLERS, event_sink_setter=set_event_sink)


if __name__ == "__main__":
    raise SystemExit(main())
