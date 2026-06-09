"""wisp-native worker: platform permissions, hotkeys, context, capture, clipboard."""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from macos_py.bootstrap import repo_root
from macos_py.service_host import run_host

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
_emit: Callable[[str, Any, Any], None] | None = None
_hotkeys = None


class _HotkeyHelper:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._ready = threading.Event()
        self._status: dict[str, Any] = {
            "started": False,
            "backend": "carbon-helper",
            "reason": "not started",
        }

    def start(self) -> dict[str, Any]:
        self._stop_stale_helpers()
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("WISP_REPO_ROOT", str(repo_root()))
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "macos_py.workers.hotkey_helper"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(repo_root()),
            env=env,
            bufsize=0,
        )
        threading.Thread(target=self._stdout_loop, daemon=True).start()
        threading.Thread(target=self._stderr_loop, daemon=True).start()
        if not self._ready.wait(timeout=5.0):
            self._status = {
                "started": False,
                "backend": "carbon-helper",
                "reason": "helper did not report readiness",
            }
            self.stop()
        return dict(self._status)

    def _stop_stale_helpers(self) -> None:
        if not IS_MAC:
            return
        try:
            subprocess.run(
                ["/usr/bin/pkill", "-f", "macos_py.workers.hotkey_helper"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            )
        except Exception:
            pass

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)

    def _stdout_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(f"[hotkeys] helper stdout: {line}", file=sys.stderr)
                continue
            if "status" in msg:
                self._status = msg
                self._ready.set()
                continue
            if msg.get("event") == "native.hotkey":
                _event("native.hotkey", msg.get("data") or {})
        if not self._ready.is_set():
            self._status = {
                "started": False,
                "backend": "carbon-helper",
                "reason": "helper exited before readiness",
            }
            self._ready.set()

    def _stderr_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                print(f"[hotkeys] helper: {line}", file=sys.stderr)


class _DirectHotkeys:
    """Windows/Linux hotkeys using the shared core listener in this worker."""

    def __init__(self) -> None:
        self.listener = None
        self._status: dict[str, Any] = {
            "started": False,
            "backend": "core-hotkeys",
            "reason": "not started",
        }

    def start(self) -> dict[str, Any]:
        try:
            import config
            from core.hotkeys import HotkeyListener

            def emit_hotkey(kind: str, **extra: Any) -> None:
                _event("native.hotkey", {"kind": kind, **extra})

            caller_count = len(getattr(config, "CALLER_ROWS", []))
            callers = [
                (lambda idx=idx: emit_hotkey("caller", index=idx))
                for idx in range(caller_count)
            ]
            self.listener = HotkeyListener(
                on_callers=callers,
                on_add_context=lambda: emit_hotkey("add_context"),
                on_clear_context=lambda: emit_hotkey("clear_context"),
                on_snip=lambda: emit_hotkey("snip"),
                on_voice_start=lambda: emit_hotkey("voice_start"),
                on_voice_stop=lambda: emit_hotkey("voice_stop"),
            )
            started = bool(self.listener.start())
            status = self.listener.status() if hasattr(self.listener, "status") else {}
            self._status = {
                "started": started,
                "backend": "core-hotkeys",
                **status,
            }
            if not started:
                self.stop()
                self._status.setdefault("reason", "no hotkeys registered")
        except Exception as exc:  # noqa: BLE001 - report hotkey backend failures
            self.stop()
            self._status = {
                "started": False,
                "backend": "core-hotkeys",
                "error": f"{type(exc).__name__}: {exc}",
            }
        return dict(self._status)

    def stop(self) -> None:
        listener = self.listener
        self.listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass


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
        try:
            from core.platform_utils import (
                get_foreground_window,
                get_window_pid,
                get_window_title,
            )

            wid = int(get_foreground_window() or 0)
            return {
                "name": get_window_title(wid),
                "bundle_id": "",
                "pid": int(get_window_pid(wid) or 0),
                "window_id": wid,
            }
        except Exception:
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
    if IS_MAC:
        try:
            import AppKit  # type: ignore

            pb = AppKit.NSPasteboard.generalPasteboard()
            value = pb.stringForType_(AppKit.NSPasteboardTypeString)
            return str(value) if value is not None else None
        except Exception:
            return None
    try:
        from core.capture import get_clipboard_text

        return get_clipboard_text()
    except Exception:
        return None


def clipboard_get() -> dict[str, Any]:
    text = _clipboard_text_primary()
    if text is None and IS_MAC:
        from core.platform import macos_native

        text = macos_native.get_clipboard_text()
    return {"text": text or ""}


def _clipboard_set_primary(text: str) -> bool:
    if IS_MAC:
        try:
            import AppKit  # type: ignore

            pb = AppKit.NSPasteboard.generalPasteboard()
            pb.clearContents()
            return bool(pb.setString_forType_(text or "", AppKit.NSPasteboardTypeString))
        except Exception:
            return False
    try:
        import pyperclip

        pyperclip.copy(text or "")
        return True
    except Exception:
        return False


def clipboard_set(text: str = "") -> dict[str, Any]:
    ok = _clipboard_set_primary(text)
    if not ok and IS_MAC:
        from core.platform import macos_native

        ok = macos_native.set_clipboard_text(text)
    return {"ok": bool(ok)}


def selected_text() -> str:
    if IS_MAC:
        # Primary AX selected-text support lands here. Until then, keep the old
        # clipboard-preserving fallback isolated in this native process.
        from core.platform import macos_native

        return macos_native.get_selected_text() or ""
    try:
        from core.capture import get_selected_text

        return get_selected_text() or ""
    except Exception:
        return ""


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
        try:
            from core.capture import get_screen_snippet

            img = get_screen_snippet()
            img.save(path, format="PNG")
            return {"ok": True, "path": path}
        except Exception as exc:  # noqa: BLE001 - surface capture failure to caller
            return {"ok": False, "path": path, "error": f"{type(exc).__name__}: {exc}"}
    from core.platform import macos_native

    ok = macos_native.capture_screen_to_file(path)
    return {"ok": ok, "path": path}


def _normalize_region(region: dict[str, Any] | None) -> dict[str, Any] | None:
    if not region:
        return None
    try:
        left = int(region.get("left", region.get("x", 0)))
        top = int(region.get("top", region.get("y", 0)))
        width = int(region["width"])
        height = int(region["height"])
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return {"left": left, "top": top, "width": width, "height": height}


def capture_region(path: str = "", region: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path:
        import tempfile

        path = str(Path(tempfile.gettempdir()) / f"wisp-region-{int(time.time() * 1000)}.png")
    if not IS_MAC:
        try:
            from core.capture import get_screen_snippet

            normalized = _normalize_region(region)
            img = get_screen_snippet(normalized)
            img.save(path, format="PNG")
            return {"ok": True, "path": path, "region": normalized or region}
        except Exception as exc:  # noqa: BLE001 - surface capture failure to caller
            return {
                "ok": False,
                "path": path,
                "region": region,
                "error": f"{type(exc).__name__}: {exc}",
            }
    from core.platform import macos_native

    normalized = _normalize_region(region)
    ok = macos_native.capture_screen_to_file(path, region=normalized)
    return {"ok": ok, "path": path, "region": normalized or region}


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


def paste_text(text: str = "", paste_combo: str = "", target_pid: int = 0) -> dict[str, Any]:
    if IS_MAC:
        activated = _activate_pid(target_pid)
        if activated:
            time.sleep(0.15)
        from core.platform import macos_native

        return {"ok": macos_native.paste_text(text, paste_combo or "cmd+v"), "activated": activated}
    try:
        from core.platform_utils import PASTE_COMBO, send_keys, set_foreground_window

        activated = False
        if target_pid:
            set_foreground_window(int(target_pid))
            activated = True
            time.sleep(0.15)
        if not clipboard_set(text).get("ok"):
            return {"ok": False, "activated": activated, "error": "clipboard write failed"}
        send_keys(paste_combo or PASTE_COMBO)
        return {"ok": True, "activated": activated}
    except Exception as exc:  # noqa: BLE001 - report pasteback failure to caller
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


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

    Carbon hotkeys need a Carbon event loop. The native worker's main thread is
    reserved for IPC, so a tiny helper process owns that loop and streams events
    back here.
    """
    global _hotkeys
    if _hotkeys is not None:
        return {"started": True, "backend": "existing"}
    helper = _HotkeyHelper() if IS_MAC else _DirectHotkeys()
    result = helper.start()
    if result.get("started"):
        _hotkeys = helper
        return result
    helper.stop()
    _hotkeys = None
    return result


def hotkeys_stop() -> dict[str, Any]:
    global _hotkeys
    if _hotkeys is not None:
        _hotkeys.stop()
        _hotkeys = None
    return {"stopped": True}


atexit.register(hotkeys_stop)


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
