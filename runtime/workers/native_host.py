"""openwand-native worker: platform permissions, hotkeys, context, capture, clipboard."""

from __future__ import annotations

import atexit
import ctypes
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from runtime.bootstrap import data_root, repo_root
from runtime.service_host import run_host

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
_emit: Callable[[str, Any, Any], None] | None = None
_hotkeys = None
_hotkeys_lock = threading.RLock()
_last_context_window_debug: dict[str, Any] = {}


class _HotkeyHelper:
    """Model hotkey helper."""
    def __init__(self) -> None:
        """Initialize the hotkey helper instance."""
        self.proc: subprocess.Popen | None = None
        self._ready = threading.Event()
        self._status: dict[str, Any] = {
            "started": False,
            "backend": "carbon-helper",
            "reason": "not started",
        }

    def start(self, addon_hotkeys: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Spawn the hotkey-helper subprocess (after killing any stale ones)."""
        self._stop_stale_helpers()
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        if "OPENWAND_DATA_ROOT" not in env and "OPENWAND_REPO_ROOT" not in env:
            env["OPENWAND_DATA_ROOT"] = str(data_root())
        if addon_hotkeys:
            env["OPENWAND_ADDON_HOTKEYS"] = json.dumps(addon_hotkeys)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "runtime.workers.hotkey_helper"],
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
        """Stop stale helpers."""
        if not IS_MAC:
            return
        try:
            subprocess.run(
                ["/usr/bin/pkill", "-f", "runtime.workers.hotkey_helper"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            )
        except Exception:
            pass

    def stop(self) -> None:
        """Terminate the hotkey-helper subprocess."""
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
        """Handle stdout loop for hotkey helper."""
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
        """Handle stderr loop for hotkey helper."""
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
        """Initialize the direct hotkeys instance."""
        self.listener = None
        self._status: dict[str, Any] = {
            "started": False,
            "backend": "core-hotkeys",
            "reason": "not started",
        }

    def start(self, addon_hotkeys: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Start the in-process HotkeyListener directly (no helper subprocess)."""
        try:
            import config
            from core.hotkeys import HotkeyListener

            def emit_hotkey(kind: str, **extra: Any) -> None:
                """Emit hotkey."""
                _event("native.hotkey", {"kind": kind, **extra})

            from core.action_files.store import configured_caller_rows

            caller_count = len(configured_caller_rows(config))
            callers = [
                (lambda idx=idx: emit_hotkey("caller", index=idx))
                for idx in range(caller_count)
            ]
            extra_hotkeys = []
            for item in addon_hotkeys or []:
                combo = str(item.get("hotkey") or "")
                addon_id = str(item.get("addon_id") or "")
                hotkey_id = str(item.get("id") or "")
                if combo and addon_id and hotkey_id:
                    extra_hotkeys.append((
                        combo,
                        lambda aid=addon_id, hid=hotkey_id: emit_hotkey("addon", addon_id=aid, hotkey_id=hid),
                    ))
            self.listener = HotkeyListener(
                on_callers=callers,
                on_add_context=lambda: emit_hotkey("add_context"),
                on_clear_context=lambda: emit_hotkey("clear_context"),
                on_snip=lambda: emit_hotkey("snip"),
                on_read_selection_aloud=lambda: emit_hotkey("read_selection_aloud"),
                on_voice_start=lambda: emit_hotkey("voice_start"),
                on_voice_stop=lambda: emit_hotkey("voice_stop"),
                on_dictate_start=lambda: emit_hotkey("dictate_start"),
                on_dictate_stop=lambda: emit_hotkey("dictate_stop"),
                on_voice_live=lambda: emit_hotkey("voice_live"),
                extra_hotkeys=extra_hotkeys,
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
        """Stop the in-process HotkeyListener."""
        listener = self.listener
        self.listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass


def set_event_sink(fn: Callable[[str, Any, Any], None]) -> None:
    """Set event sink."""
    global _emit
    _emit = fn


def _event(name: str, data: Any = None) -> None:
    """Handle event for runtime workers native host."""
    if _emit is not None:
        _emit(name, data, None)


def _with_ok(result: dict[str, Any], success_key: str) -> dict[str, Any]:
    """Add the common native success flag while preserving specific fields."""
    out = dict(result or {})
    out["ok"] = bool(out.get(success_key))
    return out


def _ax_trusted() -> bool | None:
    """Handle ax trusted for runtime workers native host."""
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
    """Handle screen trusted for runtime workers native host."""
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
    """Handle microphone status for runtime workers native host."""
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
    """Handle permissions snapshot for runtime workers native host."""
    return {
        "platform": sys.platform,
        "accessibility": _ax_trusted(),
        "screen_recording": _screen_trusted(),
        "microphone": _microphone_status(),
    }


def _win_window_pid(hwnd: int) -> int:
    """Handle win window pid for runtime workers native host."""
    if not IS_WIN or not hwnd:
        return 0
    try:
        import ctypes

        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value or 0)
    except Exception:
        return 0


def _win_window_title(hwnd: int) -> str:
    """Handle win window title for runtime workers native host."""
    if not IS_WIN or not hwnd:
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(int(hwnd))
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(int(hwnd), buf, length + 1)
        return str(buf.value or "")
    except Exception:
        return ""


def _win_process_name(pid: int) -> str:
    """Handle win process name for runtime workers native host."""
    if not IS_WIN or pid <= 0:
        return ""
    try:
        import psutil  # type: ignore

        return str(psutil.Process(pid).name() or "")
    except Exception:
        return ""


def _win_is_own_window_pid(pid: int) -> bool:
    """Return whether *pid* belongs to this supervisor's process tree."""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    own = {int(os.getpid())}
    try:
        supervisor_pid = int(os.environ.get("OPENWAND_SUPERVISOR_PID") or 0)
    except ValueError:
        supervisor_pid = 0
    if supervisor_pid > 0:
        own.add(supervisor_pid)
    if pid in own:
        return True
    try:
        import psutil

        return any(int(parent.pid) in own for parent in psutil.Process(pid).parents())
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return False


def _win_is_openwand_ui_window(hwnd: int) -> bool:
    """Handle win is openwand ui window for runtime workers native host."""
    pid = _win_window_pid(hwnd)
    title = _win_window_title(hwnd).strip().lower()
    proc = _win_process_name(pid).strip().lower()
    if _win_is_own_window_pid(pid):
        return True
    if proc == "openwand.exe":
        return True
    if proc in {"python.exe", "pythonw.exe"} and title in {"openwand", "openwand settings", "openwand memory"}:
        return True
    return False


def _win_is_external_context_window(hwnd: int) -> bool:
    """Handle win is external context window for runtime workers native host."""
    if not IS_WIN or not hwnd:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(hwnd)
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return False
        if _win_is_openwand_ui_window(hwnd):
            return False
        return bool(_win_window_title(hwnd).strip())
    except Exception:
        return False


def _win_find_external_context_window(start_hwnd: int) -> int:
    """Handle win find external context window for runtime workers native host."""
    if not IS_WIN:
        return 0
    try:
        import ctypes

        user32 = ctypes.windll.user32
        gw_hwndnext = 2
        hwnd = user32.GetWindow(int(start_hwnd or 0), gw_hwndnext) if start_hwnd else 0
        if not hwnd:
            hwnd = user32.GetTopWindow(0)
        seen: set[int] = set()
        while hwnd and len(seen) < 200:
            hwnd_i = int(hwnd)
            if hwnd_i in seen:
                break
            seen.add(hwnd_i)
            if _win_is_external_context_window(hwnd_i):
                return hwnd_i
            hwnd = user32.GetWindow(hwnd_i, gw_hwndnext)
    except Exception:
        return 0
    return 0


def _win_context_window_id(raw_hwnd: int = 0) -> int:
    """Handle win context window id for runtime workers native host."""
    global _last_context_window_debug
    if not IS_WIN:
        _last_context_window_debug = {}
        return int(raw_hwnd or 0)
    if not raw_hwnd:
        try:
            import ctypes

            raw_hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            raw_hwnd = 0
    _last_context_window_debug = {
        "raw_hwnd": int(raw_hwnd or 0),
        "raw_title": _win_window_title(raw_hwnd),
        "raw_pid": _win_window_pid(raw_hwnd),
        "raw_process": _win_process_name(_win_window_pid(raw_hwnd)),
        "corrected": False,
        "chosen_hwnd": int(raw_hwnd or 0),
        "chosen_title": _win_window_title(raw_hwnd),
        "chosen_pid": _win_window_pid(raw_hwnd),
        "chosen_process": _win_process_name(_win_window_pid(raw_hwnd)),
    }
    if raw_hwnd and _win_is_external_context_window(raw_hwnd):
        return int(raw_hwnd)
    replacement = _win_find_external_context_window(raw_hwnd)
    if replacement:
        _last_context_window_debug.update(
            {
                "corrected": True,
                "chosen_hwnd": int(replacement),
                "chosen_title": _win_window_title(replacement),
                "chosen_pid": _win_window_pid(replacement),
                "chosen_process": _win_process_name(_win_window_pid(replacement)),
            }
        )
        print(
            "[context.snapshot] corrected foreground "
            f"raw_hwnd={raw_hwnd} raw_title={_win_window_title(raw_hwnd)!r} "
            f"-> hwnd={replacement} title={_win_window_title(replacement)!r}",
            flush=True,
        )
        return int(replacement)
    return int(raw_hwnd or 0)


def _linux_process_name(pid: int) -> str:
    """Return the process name for *pid* on Linux ("" when unavailable)."""
    if pid <= 0:
        return ""
    try:
        import psutil

        return str(psutil.Process(pid).name() or "")
    except Exception:
        return ""


def _linux_is_own_window_pid(pid: int) -> bool:
    """Return True when an X11 window's pid belongs to OpenWand's own process tree."""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    own = {int(os.getpid())}
    try:
        supervisor_pid = int(os.environ.get("OPENWAND_SUPERVISOR_PID") or 0)
    except ValueError:
        supervisor_pid = 0
    if supervisor_pid > 0:
        own.add(supervisor_pid)
    if pid in own:
        return True
    try:
        import psutil

        proc = psutil.Process(pid)
        if str(proc.name() or "").strip().lower() == "openwand":
            return True
        ancestors = {int(parent.pid) for parent in proc.parents()}
    except Exception:
        return False
    return bool(own & ancestors)


def _linux_context_window_id() -> int:
    """Return the X11 window to read context from, skipping OpenWand's own windows.

    The icon overlay can hold X11 activation while the user works elsewhere,
    so the raw _NET_ACTIVE_WINDOW may be OpenWand itself. Mirror the Windows
    correction: fall back to the topmost non-OpenWand window in stacking order.
    """
    global _last_context_window_debug
    if IS_WIN or IS_MAC:
        return 0
    _last_context_window_debug = {}
    try:
        from core.platform_utils import (
            get_foreground_window,
            get_window_pid,
            get_window_title,
            list_visible_windows_stacking,
        )

        raw_wid = int(get_foreground_window() or 0)
        raw_pid = int(get_window_pid(raw_wid) or 0) if raw_wid else 0
        raw_title = get_window_title(raw_wid) if raw_wid else ""
        raw_process = _linux_process_name(raw_pid)
        _last_context_window_debug = {
            "raw_hwnd": raw_wid,
            "raw_title": raw_title,
            "raw_pid": raw_pid,
            "raw_process": raw_process,
            "corrected": False,
            "chosen_hwnd": raw_wid,
            "chosen_title": raw_title,
            "chosen_pid": raw_pid,
            "chosen_process": raw_process,
        }
        if raw_wid and not _linux_is_own_window_pid(raw_pid):
            return raw_wid
        for candidate in list_visible_windows_stacking():
            candidate = int(candidate or 0)
            if not candidate or candidate == raw_wid:
                continue
            cand_pid = int(get_window_pid(candidate) or 0)
            if _linux_is_own_window_pid(cand_pid):
                continue
            cand_title = str(get_window_title(candidate) or "")
            if not cand_title.strip():
                continue
            _last_context_window_debug.update(
                {
                    "corrected": True,
                    "chosen_hwnd": candidate,
                    "chosen_title": cand_title,
                    "chosen_pid": cand_pid,
                    "chosen_process": _linux_process_name(cand_pid),
                }
            )
            print(
                "[context.snapshot] corrected foreground "
                f"raw_hwnd={raw_wid} raw_title={raw_title!r} "
                f"-> hwnd={candidate} title={cand_title!r}",
                flush=True,
            )
            return candidate
        return raw_wid
    except Exception:
        return 0


def _runtime_debug() -> dict[str, Any]:
    """Handle runtime debug for runtime workers native host."""
    debug = {
        "cwd": os.getcwd(),
        "repo_root": str(repo_root()),
        "executable": sys.executable,
        "platform": sys.platform,
    }
    try:
        import config

        debug["config_file"] = str(getattr(config, "__file__", "") or "")
        debug["env_file"] = str(getattr(config, "_ENV_FILE", "") or "")
    except Exception as exc:
        debug["config_error"] = f"{type(exc).__name__}: {exc}"
    return debug


def _active_app() -> dict[str, Any]:
    """Handle active app for runtime workers native host."""
    global _last_context_window_debug
    if not IS_MAC:
        try:
            if not IS_WIN and os.environ.get("WAYLAND_DISPLAY"):
                from core.platform import linux_atspi

                focused = linux_atspi.get_focused_context()
                if focused:
                    return {
                        "name": str(focused.get("window_title") or focused.get("app_name") or ""),
                        "process_name": str(focused.get("process_name") or focused.get("app_name") or ""),
                        "bundle_id": "",
                        "pid": int(focused.get("pid") or 0),
                        "window_id": 0,
                        "browser_url": str(focused.get("browser_url") or ""),
                    }
            from core.platform_utils import (
                get_window_pid,
                get_window_title,
            )

            if IS_WIN:
                wid = _win_context_window_id()
            else:
                wid = _linux_context_window_id()
            if not wid:
                from core.platform_utils import get_foreground_window

                wid = int(get_foreground_window() or 0)
            pid = int(get_window_pid(wid) or 0)
            process_name = ""
            if pid:
                try:
                    import psutil

                    process_name = str(psutil.Process(pid).name() or "")
                except Exception:
                    process_name = ""
            title = get_window_title(wid)
            if not _last_context_window_debug:
                # The per-OS context-window helpers populate this with the
                # raw-vs-chosen correction trail; only fill the plain form
                # when no helper ran (e.g. raw foreground fallback).
                _last_context_window_debug = {
                    "raw_hwnd": wid,
                    "raw_title": title,
                    "raw_pid": pid,
                    "raw_process": process_name,
                    "corrected": False,
                    "chosen_hwnd": wid,
                    "chosen_title": title,
                    "chosen_pid": pid,
                    "chosen_process": process_name,
                }
            return {
                "name": title,
                "process_name": process_name,
                "bundle_id": "",
                "pid": pid,
                "window_id": wid,
            }
        except Exception:
            return {}
    try:
        import AppKit  # type: ignore

        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return {}
        app_name = str(app.localizedName() or "")
        pid = int(app.processIdentifier())
        _last_context_window_debug = {}
        try:
            from core.platform import macos_native

            rows = macos_native.list_document_windows()
            frontmost = [
                row for row in rows
                if bool(row.get("frontmost"))
                and (
                    int(row.get("pid") or 0) == pid
                    or str(row.get("process_name") or "") == app_name
                )
            ]
            if frontmost:
                row = frontmost[0]
                title = str(row.get("title") or "")
                process_name = str(row.get("process_name") or app_name)
                row_pid = int(row.get("pid") or pid)
                _last_context_window_debug = {
                    "raw_hwnd": 0,
                    "raw_title": title,
                    "raw_pid": row_pid,
                    "raw_process": process_name,
                    "corrected": False,
                    "chosen_hwnd": 0,
                    "chosen_title": title,
                    "chosen_pid": row_pid,
                    "chosen_process": process_name,
                }
        except Exception:
            _last_context_window_debug = {}
        return {
            "name": app_name,
            "bundle_id": str(app.bundleIdentifier() or ""),
            "pid": pid,
        }
    except Exception:
        return {}


def _frontmost_document_window() -> dict[str, Any]:
    """Return the frontmost document/window independently of browser context."""
    if not IS_MAC:
        return {}
    try:
        from core.platform import macos_native

        rows = macos_native.list_document_windows()
        rows.sort(key=lambda row: (not bool(row.get("frontmost")), str(row.get("title") or "")))
        for row in rows:
            if not bool(row.get("frontmost")):
                continue
            title = str(row.get("title") or "").strip()
            process_name = str(row.get("process_name") or "").strip()
            if not title or not process_name:
                continue
            return {
                "title": title,
                "process_name": process_name,
                "pid": int(row.get("pid") or 0),
                "window_id": 0,
            }
    except Exception:
        return {}
    return {}


def _clipboard_text_primary() -> str | None:
    """Handle clipboard text primary for runtime workers native host."""
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
    """Handle clipboard get for runtime workers native host."""
    text = _clipboard_text_primary()
    if text is None and IS_MAC:
        from core.platform import macos_native

        text = macos_native.get_clipboard_text()
    return {"text": text or ""}


def _clipboard_set_primary(text: str) -> bool:
    """Handle clipboard set primary for runtime workers native host."""
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
    """Handle clipboard set for runtime workers native host."""
    ok = _clipboard_set_primary(text)
    if not ok and IS_MAC:
        from core.platform import macos_native

        ok = macos_native.set_clipboard_text(text)
    return {"ok": bool(ok)}


# Last PRIMARY acquisition auto-filled per surface (owner id, timestamp, digest).
# X11 apps keep serving a selection after the user clears the highlight, so a
# repeat of the exact same acquisition is treated as stale instead of being
# auto-filled again; re-selecting (even the same text) makes a new timestamp.
_AUTOFILLED_PRIMARY_SELECTIONS: dict[str, tuple[int, int, str]] = {}


def _primary_selection_identity(text: str) -> tuple[int, int, str] | None:
    """Return (owner id, acquisition timestamp, text digest) for X11 PRIMARY."""
    try:
        import hashlib

        from core.capture import _linux_x11_primary_selection_identity

        identity = _linux_x11_primary_selection_identity()
        if not identity:
            return None
        digest = hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()
        return int(identity[0]), int(identity[1]), digest
    except Exception:
        return None


def selected_text(
    *,
    allow_clipboard_fallback: bool = True,
    active_pid: int | None = None,
    require_active_owner: bool = False,
    selection_dedupe_key: str = "",
) -> str:
    """Handle selected text for runtime workers native host."""
    return _selected_text_and_stale(
        allow_clipboard_fallback=allow_clipboard_fallback,
        active_pid=active_pid,
        require_active_owner=require_active_owner,
        selection_dedupe_key=selection_dedupe_key,
    )[0]


_calc_selection_reader: Any = None
_calc_api_selection_reader: Any = None
_calc_action_adapter: Any = None
_vscode_selection_reader: Any = None
_vscode_action_adapter: Any = None
_vscode_extension_api_adapter: Any = None
_browser_action_adapter: Any = None
_browser_rewrite_adapter: Any = None
_calc_automation_status: dict[str, Any] = {
    "available": False,
    "managed": False,
    "reason": "not_started",
}


def calc_automation_prewarm(*, wait_for_startup: bool = False) -> dict[str, Any]:
    """Provision and detect OpenWand's persistent LibreOffice API endpoint."""
    global _calc_automation_status
    if not IS_WIN:
        _calc_automation_status = {
            "available": False,
            "managed": False,
            "reason": "windows_first",
        }
        return dict(_calc_automation_status)
    try:
        import psutil

        from core.actions.adapters.calc.bridge import (
            configure_calc_connection,
            configured_calc_connection_pipe,
        )

        libreoffice_processes = []
        command_line_pipe = ""
        for process in psutil.process_iter(["name"]):
            name = str(process.info.get("name") or "").casefold()
            if name not in {"soffice.exe", "soffice.bin", "scalc.exe"}:
                continue
            libreoffice_processes.append(process)
            try:
                command_line = " ".join(process.cmdline())
                match = re.search(r"pipe,name=(openwand_calc_[A-Za-z0-9_-]{16,80});urp", command_line)
                if match:
                    command_line_pipe = match.group(1)
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue
        executable = Path(
            os.environ.get("LIBREOFFICE_EXECUTABLE")
            or r"C:\Program Files\LibreOffice\program\soffice.exe"
        )
        libreoffice_python = Path(
            os.environ.get("LIBREOFFICE_PYTHON")
            or executable.with_name("python.exe")
        )
        helper = repo_root() / "runtime" / "helpers" / "calc_uno_action.py"
        if not executable.is_file() or not libreoffice_python.is_file() or not helper.is_file():
            _calc_automation_status = {
                "available": False,
                "managed": False,
                "reason": "libreoffice_not_found",
            }
            return dict(_calc_automation_status)

        managed_pipe = configured_calc_connection_pipe() or command_line_pipe
        configured = configure_calc_connection(managed_pipe)
        managed_pipe = str(configured.get("pipe_name") or "")
        os.environ.pop("OPENWAND_CALC_UNO_PORT", None)
        os.environ["OPENWAND_CALC_UNO_PIPE"] = managed_pipe

        if libreoffice_processes and configured.get("changed") and not command_line_pipe:
            _calc_automation_status = {
                "available": False,
                "managed": True,
                "reason": "bridge_pending_restart",
                "pipe_name": managed_pipe,
                "transport": "uno_named_pipe_persisted",
            }
            return dict(_calc_automation_status)

        connected = False
        if libreoffice_processes:
            deadline = time.monotonic() + (20.0 if wait_for_startup else 0.0)
            while True:
                completed = subprocess.run(  # noqa: S603 - fixed local executable and helper
                    [
                        str(libreoffice_python),
                        str(helper),
                        "--pipe",
                        managed_pipe,
                        "--mode",
                        "probe",
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3.0,
                    check=False,
                )
                connected = completed.returncode == 0
                if connected or time.monotonic() >= deadline:
                    break
                time.sleep(0.25)
        if libreoffice_processes and not connected:
            _calc_automation_status = {
                "available": False,
                "managed": True,
                "reason": "bridge_starting" if not wait_for_startup else "bridge_unavailable",
                "pipe_name": managed_pipe,
                "transport": "uno_named_pipe_persisted",
            }
            return dict(_calc_automation_status)
        _calc_automation_status = {
            "available": True,
            "managed": True,
            "reason": "ready" if connected else "ready_on_launch",
            "pipe_name": managed_pipe,
            "transport": "uno_named_pipe_persisted",
        }
    except Exception as exc:  # noqa: BLE001 - optional integration must not block OpenWand startup
        _calc_automation_status = {
            "available": False,
            "managed": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    return dict(_calc_automation_status)


def action_calc_status() -> dict[str, Any]:
    """Refresh whether the active LibreOffice process loaded OpenWand's endpoint."""
    return calc_automation_prewarm(wait_for_startup=True)


def _calc_api_snapshot(active_app: dict[str, Any]) -> dict[str, Any]:
    """Capture one Calc target and its values through the persisted UNO pipe."""
    global _calc_selection_reader
    from core.actions.adapters.calc import CalcSelectionReader, is_calc_app

    app = active_app if isinstance(active_app, dict) else {}
    if not is_calc_app(app):
        raise RuntimeError("The captured application is not LibreOffice Calc.")
    status = calc_automation_prewarm(wait_for_startup=True)
    if not status.get("available") or status.get("transport") != "uno_named_pipe_persisted":
        reason = str(status.get("reason") or "named_pipe_unavailable")
        raise RuntimeError(f"OpenWand's local Calc action pipe is unavailable ({reason}).")
    if _calc_selection_reader is None:
        _calc_selection_reader = CalcSelectionReader()
    target = _calc_selection_reader.inspect_target(app)
    if not target:
        raise RuntimeError("Calc did not expose the selected range.")
    expected_range = str(app.get("range") or "").replace("$", "").strip().upper()
    actual_range = str(target.get("range") or "").replace("$", "").strip().upper()
    if expected_range and actual_range != expected_range:
        raise RuntimeError("The selected Calc range changed after the preview.")

    pipe_name = str(status.get("pipe_name") or os.environ.get("OPENWAND_CALC_UNO_PIPE") or "").strip()
    libreoffice_python = Path(
        os.environ.get("LIBREOFFICE_PYTHON")
        or r"C:\Program Files\LibreOffice\program\python.exe"
    )
    helper = repo_root() / "runtime" / "helpers" / "calc_uno_action.py"
    completed = subprocess.run(  # noqa: S603 - fixed local executable and helper
        [
            str(libreoffice_python),
            str(helper),
            "--pipe",
            pipe_name,
            "--mode",
            "snapshot",
            "--title",
            str(target.get("document_title") or ""),
            "--range",
            actual_range,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8.0,
        check=False,
    )
    output = next((line for line in reversed(completed.stdout.splitlines()) if line.strip()), "")
    try:
        result = json.loads(output) if output else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("LibreOffice returned an invalid Calc snapshot.") from exc
    if completed.returncode != 0 or not isinstance(result, dict) or not result.get("ok"):
        error = str(result.get("error") if isinstance(result, dict) else "").strip()
        raise RuntimeError(error or "LibreOffice could not snapshot the selected range.")
    values = result.get("values")
    if not isinstance(values, list) or not values or not all(isinstance(row, list) for row in values):
        raise RuntimeError("LibreOffice returned invalid selected values.")
    typed_values = result.get("typed_values")
    if not isinstance(typed_values, list) or len(typed_values) != len(values) or not all(
        isinstance(row, list) and len(row) == len(values[index])
        for index, row in enumerate(typed_values)
    ):
        # Compatibility with an older helper during a rolling development restart.
        typed_values = values
    formulas = result.get("formulas")
    if not isinstance(formulas, list) or len(formulas) != len(values) or not all(
        isinstance(row, list) and len(row) == len(values[index])
        for index, row in enumerate(formulas)
    ):
        formulas = values
    fingerprint = str(result.get("fingerprint") or "").strip()
    if not fingerprint:
        raise RuntimeError("LibreOffice did not return a range fingerprint.")
    return {
        **target,
        "range": actual_range,
        "rows": len(values),
        "columns": len(values[0]) if values else 0,
        "values": values,
        "typed_values": typed_values,
        "formulas": formulas,
        "selected_text": "\n".join("\t".join(str(cell) for cell in row) for row in values),
        "fingerprint": fingerprint,
        "capture_method": "windows_uia_name_box+uno_named_pipe",
    }


class _CalcApiSelectionReader:
    """Revalidate Calc through the same typed API snapshot used by preview."""

    @staticmethod
    def inspect_selection(active_app: dict[str, Any]) -> dict[str, Any]:
        return _calc_api_snapshot(active_app)


def action_calc_snapshot(active_app: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the canonical pre-preview Calc snapshot without changing cells."""
    try:
        return {"ok": True, "selection": _calc_api_snapshot(active_app or {}), "error": ""}
    except Exception as exc:  # noqa: BLE001 - controlled IPC failure
        return {"ok": False, "selection": {}, "error": f"{type(exc).__name__}: {exc}"}


def context_app_selection(active_app: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read a captured app's structured selection after the overlay has focus."""
    app = active_app if isinstance(active_app, dict) else {}
    try:
        from core.actions.adapters.calc import CalcSelectionReader, is_calc_app

        if not is_calc_app(app):
            return {"supported": False, "selection": {}}
        global _calc_selection_reader
        if _calc_selection_reader is None:
            _calc_selection_reader = CalcSelectionReader()
        selection = _calc_selection_reader.inspect_selection(app)
        return {"supported": True, "selection": selection, "error": ""}
    except Exception as exc:  # noqa: BLE001 - optional app integration must not block the overlay
        return {
            "supported": True,
            "selection": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def action_calc_apply(
    plan: dict[str, Any] | None = None,
    confirmed: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Execute one confirmed Calc plan in the exact recorded window."""
    global _calc_action_adapter, _calc_api_selection_reader
    try:
        from core.actions.adapters.calc import CalcActionAdapter, action_plan_from_dict

        status = calc_automation_prewarm(wait_for_startup=True)
        if not status.get("available") or status.get("transport") != "uno_named_pipe_persisted":
            reason = str(status.get("reason") or "named_pipe_unavailable")
            raise RuntimeError(f"OpenWand's local Calc action pipe is unavailable ({reason}).")
        if _calc_api_selection_reader is None:
            _calc_api_selection_reader = _CalcApiSelectionReader()
        if _calc_action_adapter is None:
            _calc_action_adapter = CalcActionAdapter(reader=_calc_api_selection_reader)
        action_plan = action_plan_from_dict(plan or {})
        result = _calc_action_adapter.execute(
            action_plan,
            confirmed=bool(confirmed),
            idempotency_key=str(idempotency_key or ""),
        )
        return {"ok": True, "result": asdict(result), "error": ""}
    except Exception as exc:  # noqa: BLE001 - return a user-facing action failure over IPC
        return {"ok": False, "result": {}, "error": f"{type(exc).__name__}: {exc}"}


def _run_libreoffice_rewrite_helper(arguments: list[str], *, timeout: float) -> dict[str, Any]:
    """Run the fixed Writer/Impress UNO helper through OpenWand's persisted pipe."""
    status = calc_automation_prewarm(wait_for_startup=True)
    if not status.get("available") or status.get("transport") != "uno_named_pipe_persisted":
        reason = str(status.get("reason") or "named_pipe_unavailable")
        raise RuntimeError(f"OpenWand's local LibreOffice action pipe is unavailable ({reason}).")
    pipe_name = str(status.get("pipe_name") or os.environ.get("OPENWAND_CALC_UNO_PIPE") or "").strip()
    libreoffice_python = Path(
        os.environ.get("LIBREOFFICE_PYTHON")
        or r"C:\Program Files\LibreOffice\program\python.exe"
    )
    helper = repo_root() / "runtime" / "helpers" / "libreoffice_rewrite.py"
    if not libreoffice_python.is_file() or not helper.is_file() or not pipe_name:
        raise RuntimeError("LibreOffice's exact Rewrite runtime is unavailable.")
    completed = subprocess.run(  # noqa: S603 - fixed local executable and helper
        [str(libreoffice_python), str(helper), "--pipe", pipe_name, *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    output = next((line for line in reversed(completed.stdout.splitlines()) if line.strip()), "")
    try:
        result = json.loads(output) if output else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("LibreOffice returned invalid exact Rewrite output.") from exc
    if completed.returncode != 0 or not isinstance(result, dict) or not result.get("ok"):
        error = str(result.get("error") if isinstance(result, dict) else "").strip()
        raise RuntimeError(error or completed.stderr.strip() or "LibreOffice exact Rewrite failed.")
    return result


def action_libreoffice_rewrite_snapshot(
    active_app: dict[str, Any] | None = None,
    selected_text: str = "",
) -> dict[str, Any]:
    """Capture one exact Writer or Impress text range through UNO."""
    try:
        from core.rewrite_libreoffice import libreoffice_rewrite_surface

        app = active_app if isinstance(active_app, dict) else {}
        surface = libreoffice_rewrite_surface(app)
        if not surface:
            raise RuntimeError("The captured app is not LibreOffice Writer or Impress.")
        result = _run_libreoffice_rewrite_helper(
            [
                "--mode",
                "snapshot",
                "--surface",
                surface,
                "--title",
                str(app.get("name") or app.get("title") or ""),
                "--selected-text",
                str(selected_text or ""),
            ],
            timeout=10.0,
        )
        snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else {}
        return {"ok": bool(snapshot), "snapshot": snapshot, "error": ""}
    except Exception as exc:  # noqa: BLE001 - controlled IPC failure
        return {"ok": False, "snapshot": {}, "error": f"{type(exc).__name__}: {exc}"}


def action_libreoffice_rewrite_apply(plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply and verify one captured Writer or Impress Rewrite plan."""
    try:
        result = _run_libreoffice_rewrite_helper(
            [
                "--mode",
                "apply",
                "--plan-json",
                json.dumps(plan or {}, ensure_ascii=False, separators=(",", ":")),
            ],
            timeout=12.0,
        )
        return {"ok": True, "result": result, "error": ""}
    except Exception as exc:  # noqa: BLE001 - controlled IPC failure
        return {"ok": False, "result": {}, "error": f"{type(exc).__name__}: {exc}"}


def action_vscode_snapshot(
    active_app: dict[str, Any] | None = None,
    selected_text: str = "",
) -> dict[str, Any]:
    """Read the exact saved VS Code file containing the captured selection."""
    global _vscode_selection_reader
    started = time.perf_counter()
    try:
        from core.actions.adapters.vscode import VSCodeSelectionReader

        app = active_app if isinstance(active_app, dict) else {}
        if _vscode_selection_reader is None:
            _vscode_selection_reader = VSCodeSelectionReader()
        if str(selected_text or "").strip():
            snapshot = _vscode_selection_reader.inspect_selection(app, str(selected_text or ""))
        else:
            snapshot = _vscode_selection_reader.inspect_empty_file(app)
        return {
            "ok": True,
            "snapshot": snapshot.to_selection_dict(),
            "error": "",
            "timing": {"total_ms": round((time.perf_counter() - started) * 1000, 3)},
        }
    except Exception as exc:  # noqa: BLE001 - optional app integration must not block OpenWand
        return {
            "ok": False,
            "snapshot": {},
            "error": f"{type(exc).__name__}: {exc}",
            "timing": {"total_ms": round((time.perf_counter() - started) * 1000, 3)},
        }


def action_vscode_apply(
    plan: dict[str, Any] | None = None,
    confirmed: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Apply one confirmed VS Code plan to its fingerprint-checked saved file."""
    global _vscode_action_adapter
    started = time.perf_counter()
    try:
        from core.actions.adapters.vscode import VSCodeActionAdapter, action_plan_from_dict

        if _vscode_action_adapter is None:
            _vscode_action_adapter = VSCodeActionAdapter()
        action_plan = action_plan_from_dict(plan or {})
        result = _vscode_action_adapter.execute(
            action_plan,
            confirmed=bool(confirmed),
            idempotency_key=str(idempotency_key or ""),
        )
        return {
            "ok": True,
            "result": asdict(result),
            "error": "",
            "timing": {
                "worker_total_ms": round((time.perf_counter() - started) * 1000, 3),
                **dict(_vscode_action_adapter.last_execution_timing),
            },
        }
    except Exception as exc:  # noqa: BLE001 - return a user-facing action failure over IPC
        return {
            "ok": False,
            "result": {},
            "error": f"{type(exc).__name__}: {exc}",
            "timing": {
                "worker_total_ms": round((time.perf_counter() - started) * 1000, 3),
                **dict(getattr(_vscode_action_adapter, "last_execution_timing", {}) or {}),
            },
        }


def action_vscode_live_apply(
    text: str = "",
    active_app: dict[str, Any] | None = None,
    editor_point: dict[str, Any] | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Apply one reviewed edit to a OpenWand-managed live VS Code editor."""
    if not confirmed:
        return {
            "ok": False,
            "method": "vscode-extension-api",
            "error": "preview approval is required before editing the live VS Code buffer",
        }
    global _vscode_extension_api_adapter
    try:
        from core.actions.adapters.vscode import VSCodeExtensionAPIAdapter

        if _vscode_extension_api_adapter is None:
            _vscode_extension_api_adapter = VSCodeExtensionAPIAdapter()
        return _vscode_extension_api_adapter.apply_text(str(text or ""))
    except Exception as exc:  # noqa: BLE001 - return a controlled IPC failure
        return {
            "ok": False,
            "method": "vscode-extension-api",
            "activated": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def action_browser_form_snapshot(active_app: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read safe editable fields from one OpenWand-managed Chromium tab."""
    global _browser_action_adapter
    started = time.perf_counter()
    try:
        from core.actions.adapters.browser import BrowserActionAdapter

        if _browser_action_adapter is None:
            _browser_action_adapter = BrowserActionAdapter()
        snapshot = _browser_action_adapter.inspect_form(active_app or {})
        return {
            "ok": True,
            "snapshot": snapshot.to_dict(),
            "error": "",
            "timing": {"worker_total_ms": round((time.perf_counter() - started) * 1000, 3)},
        }
    except Exception as exc:  # noqa: BLE001 - return a controlled IPC failure
        return {
            "ok": False,
            "snapshot": {},
            "error": f"{type(exc).__name__}: {exc}",
            "timing": {"worker_total_ms": round((time.perf_counter() - started) * 1000, 3)},
        }


def action_browser_rewrite_snapshot(active_app: dict[str, Any] | None = None) -> dict[str, Any]:
    """Capture one exact editable selection in a OpenWand-managed Chromium tab."""
    global _browser_rewrite_adapter
    try:
        from core.rewrite_browser import BrowserRewriteAdapter

        if _browser_rewrite_adapter is None:
            _browser_rewrite_adapter = BrowserRewriteAdapter()
        snapshot = _browser_rewrite_adapter.inspect_selection(active_app or {})
        return {"ok": True, "snapshot": snapshot.to_dict(), "error": ""}
    except Exception as exc:  # noqa: BLE001 - controlled IPC failure
        return {"ok": False, "snapshot": {}, "error": f"{type(exc).__name__}: {exc}"}


def action_browser_rewrite_apply(plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply and verify one exact managed-browser selected-text Rewrite."""
    global _browser_rewrite_adapter
    try:
        from core.rewrite_browser import (
            BrowserRewriteAdapter,
            BrowserRewritePlan,
            BrowserRewriteSnapshot,
        )

        value = plan if isinstance(plan, dict) else {}
        snapshot = BrowserRewriteSnapshot.from_dict(dict(value.get("snapshot") or {}))
        rewrite_plan = BrowserRewritePlan(
            snapshot=snapshot,
            replacement_text=str(value.get("replacement_text") or ""),
        )
        if _browser_rewrite_adapter is None:
            _browser_rewrite_adapter = BrowserRewriteAdapter()
        applied = _browser_rewrite_adapter.apply(rewrite_plan)
        return {"ok": applied, "result": {"status": "applied", "verification": applied}, "error": ""}
    except Exception as exc:  # noqa: BLE001 - controlled IPC failure
        return {"ok": False, "result": {}, "error": f"{type(exc).__name__}: {exc}"}


def action_browser_form_apply(
    plan: dict[str, Any] | None = None,
    confirmed: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Apply and verify one reviewed browser form plan without submitting."""
    global _browser_action_adapter
    started = time.perf_counter()
    try:
        from core.actions.adapters.browser import BrowserActionAdapter, action_plan_from_dict

        if _browser_action_adapter is None:
            _browser_action_adapter = BrowserActionAdapter()
        result = _browser_action_adapter.execute(
            action_plan_from_dict(plan or {}),
            confirmed=bool(confirmed),
            idempotency_key=str(idempotency_key or ""),
        )
        return {
            "ok": True,
            "result": asdict(result),
            "error": "",
            "timing": {"worker_total_ms": round((time.perf_counter() - started) * 1000, 3)},
        }
    except Exception as exc:  # noqa: BLE001 - return a controlled IPC failure
        return {
            "ok": False,
            "result": {},
            "error": f"{type(exc).__name__}: {exc}",
            "timing": {"worker_total_ms": round((time.perf_counter() - started) * 1000, 3)},
        }


def _selected_text_and_stale(
    *,
    allow_clipboard_fallback: bool = True,
    active_pid: int | None = None,
    require_active_owner: bool = False,
    selection_dedupe_key: str = "",
    allow_copy_after_empty_uia: bool = False,
) -> tuple[str, str]:
    """Return (live selected text, stale selection already auto-filled once).

    The stale slot is only populated on Linux/X11 when a dedupe key is given
    and PRIMARY still serves the exact acquisition that key already received:
    the highlight may be long gone (X11 owners keep serving cleared
    selections), so the caller can offer it off-by-default instead of
    attaching it silently.
    """
    if IS_MAC:
        # Prefer Accessibility: reading AXSelectedText injects no keystrokes. The
        # old clipboard path synthesises Cmd+C, and System Events clearing the
        # command flag around that copy desyncs a physically-held hotkey modifier
        # (option/ctrl) -- so the next hotkey key arrives with no modifier, isn't
        # swallowed, and leaks into the app (e.g. holding the modifier, pressing
        # the add- then clear-context keys, and watching the selection get
        # replaced). Fall back to the copy only when AX can't answer (apps that
        # don't expose selection, e.g. some web/Electron views).
        ax = _ax_selected_text()
        if ax is not None:
            return ax.strip(), ""
        if not allow_clipboard_fallback:
            return "", ""
        from core.platform import macos_native

        return macos_native.get_selected_text() or "", ""
    try:
        if not IS_WIN and not IS_MAC and require_active_owner:
            if os.environ.get("WAYLAND_DISPLAY"):
                try:
                    from core.platform import linux_atspi

                    text = linux_atspi.get_selected_text().strip()
                    if text:
                        return text, ""
                except Exception:
                    pass
                if not allow_clipboard_fallback:
                    return "", ""
                from core.capture import _get_primary_selection_linux

                return (_get_primary_selection_linux() or "").strip(), ""
            from core.capture import _get_primary_selection_linux

            text = (
                _get_primary_selection_linux(
                    active_pid=active_pid,
                    require_active_owner=True,
                )
                or ""
            ).strip()
            if text and selection_dedupe_key:
                identity = _primary_selection_identity(text)
                if identity is not None:
                    if _AUTOFILLED_PRIMARY_SELECTIONS.get(selection_dedupe_key) == identity:
                        # Same acquisition this surface already auto-filled once;
                        # hand it back as stale only. Skip the Ctrl+C fallback
                        # too - it would just re-copy the same text.
                        return "", text
                    _AUTOFILLED_PRIMARY_SELECTIONS[selection_dedupe_key] = identity
            if text or not allow_clipboard_fallback:
                return text, ""
            from core.capture import _get_selected_text_clipboard

            return (_get_selected_text_clipboard() or "").strip(), ""
        if allow_clipboard_fallback:
            from core.capture import get_selected_text

            return (
                get_selected_text(
                    allow_copy_after_empty_uia=bool(allow_copy_after_empty_uia),
                )
                or "",
                "",
            )
        if IS_WIN:
            from core.capture import _get_selected_text_uia

            return (_get_selected_text_uia() or "").strip(), ""
        from core.capture import _get_primary_selection_linux

        return (
            _get_primary_selection_linux(
                active_pid=active_pid,
                require_active_owner=require_active_owner,
            )
            or ""
        ).strip(), ""
    except Exception:
        return "", ""


def _windows_clipboard_file_paths() -> list[str]:
    """Return CF_HDROP paths from the Windows clipboard, if present."""
    if not IS_WIN:
        return []
    try:
        import win32clipboard  # type: ignore

        try:
            import win32con  # type: ignore

            cf_hdrop = int(getattr(win32con, "CF_HDROP", 15))
        except Exception:
            cf_hdrop = 15
        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(cf_hdrop):
                return []
            data = win32clipboard.GetClipboardData(cf_hdrop)
        finally:
            win32clipboard.CloseClipboard()
        if isinstance(data, (list, tuple)):
            return [str(path) for path in data if str(path or "").strip()]
    except Exception:
        return []
    return []


def _mac_clipboard_file_paths() -> list[str]:
    """Return file URLs from the macOS pasteboard, if present."""
    if not IS_MAC:
        return []
    try:
        import AppKit  # type: ignore

        pb = AppKit.NSPasteboard.generalPasteboard()
        options = {AppKit.NSPasteboardURLReadingFileURLsOnlyKey: True}
        urls = pb.readObjectsForClasses_options_([AppKit.NSURL], options) or []
        paths: list[str] = []
        for url in urls:
            try:
                if bool(url.isFileURL()):
                    path = str(url.path() or "").strip()
                    if path:
                        paths.append(path)
            except Exception:
                continue
        return paths
    except Exception:
        return []


def _linux_file_uri_to_path(value: str) -> str:
    """Convert a Linux file URI or plain absolute path to a local path."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("/"):
        return text
    try:
        from urllib.parse import unquote, urlparse

        parsed = urlparse(text)
        if parsed.scheme != "file":
            return ""
        if parsed.netloc and parsed.netloc not in {"localhost", "127.0.0.1"}:
            return ""
        return unquote(parsed.path or "").strip()
    except Exception:
        return ""


def _parse_linux_uri_list(data: str) -> list[str]:
    """Parse Linux clipboard URI lists used by file managers."""
    paths: list[str] = []
    for raw_line in str(data or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line in {"copy", "cut"}:
            continue
        path = _linux_file_uri_to_path(line)
        if path:
            paths.append(path)
    return paths


def _linux_clipboard_file_paths() -> list[str]:
    """Return selected file paths from Linux clipboard MIME targets."""
    if IS_WIN or IS_MAC:
        return []

    targets = (
        "x-special/gnome-copied-files",
        "text/uri-list",
    )
    commands: list[list[str]] = []
    if os.environ.get("WAYLAND_DISPLAY"):
        for target in targets:
            commands.append(["wl-paste", "--no-newline", "--type", target])
    for target in targets:
        commands.append(["xclip", "-selection", "clipboard", "-t", target, "-o"])

    for cmd in commands:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0)
        except Exception:
            continue
        if out.returncode != 0:
            continue
        paths = _parse_linux_uri_list(out.stdout or "")
        if paths:
            return paths
    return []


def selected_paths() -> list[str]:
    """Capture selected files/folders from the foreground shell via Copy."""
    previous_text = clipboard_get().get("text", "")
    paths: list[str] = []
    try:
        from core.platform_utils import COPY_COMBO, send_keys

        send_keys(COPY_COMBO)
        deadline = time.monotonic() + (0.60 if IS_WIN else 0.35)
        while time.monotonic() < deadline:
            time.sleep(0.05)
            if IS_WIN:
                paths = _windows_clipboard_file_paths()
            elif IS_MAC:
                paths = _mac_clipboard_file_paths()
            else:
                paths = _linux_clipboard_file_paths()
            if paths:
                break
    except Exception:
        paths = []
    finally:
        try:
            clipboard_set(str(previous_text or ""))
        except Exception:
            pass

    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        normalized = str(path or "").strip()
        key = os.path.normcase(os.path.abspath(normalized)) if normalized else ""
        if normalized and key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique


def _selection_source_kind(active: dict[str, Any]) -> str:
    """Return the likely selected-context kind for the foreground app."""
    name = str((active or {}).get("name") or "").strip().lower()
    process = str((active or {}).get("process_name") or "").strip().lower()
    bundle = str((active or {}).get("bundle_id") or "").strip().lower()
    shell_names = {
        "explorer.exe",
        "explorer",
        "finder",
        "file explorer",
        "windows explorer",
        "caja",
        "dolphin",
        "io.elementary.files",
        "krusader",
        "nautilus",
        "nemo",
        "org.gnome.nautilus",
        "org.kde.dolphin",
        "pantheon-files",
        "pcmanfm",
        "pcmanfm-qt",
        "spacefm",
        "thunar",
    }
    if process in shell_names or name in shell_names or bundle == "com.apple.finder":
        return "paths"
    return "text"


def _screen_size() -> dict[str, int]:
    """Return primary-screen dimensions without capturing pixels."""
    if IS_WIN:
        try:
            import ctypes

            user32 = ctypes.windll.user32
            width = int(user32.GetSystemMetrics(0))
            height = int(user32.GetSystemMetrics(1))
            if width > 0 and height > 0:
                return {"width": width, "height": height}
        except Exception:
            pass
    try:
        import mss

        mss_factory = getattr(mss, "MSS", mss.mss)
        with mss_factory() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            width = int(monitor.get("width") or 0)
            height = int(monitor.get("height") or 0)
            if width > 0 and height > 0:
                return {"width": width, "height": height}
    except Exception:
        pass
    return {"width": 0, "height": 0}


def _browser_page_payload(window: Any, content: str = "") -> dict[str, Any]:
    """Serialize one top-level browser window as an independent context source."""
    hwnd = int(getattr(window, "hwnd", 0) or 0)
    app = str(getattr(window, "process_name", "") or "").strip()
    source_key = str(hwnd) if hwnd else app.casefold()
    return {
        "id": f"browser:{source_key}",
        "title": str(getattr(window, "title", "") or "").strip(),
        "process_name": app,
        "pid": int(getattr(window, "pid", 0) or 0),
        "hwnd": hwnd,
        "url": str(getattr(window, "url", "") or "").strip(),
        "app": app,
        "content": str(content or ""),
    }


def _apply_primary_browser_page(snapshot: dict[str, Any], pages: list[dict[str, Any]]) -> None:
    """Keep legacy singular browser fields mapped to the first captured page."""
    snapshot["browser_pages"] = pages
    if not pages:
        return
    primary = pages[0]
    snapshot["browser_url"] = str(primary.get("url") or "")
    snapshot["browser_hwnd"] = int(primary.get("hwnd") or 0)
    snapshot["browser_app"] = str(primary.get("app") or primary.get("process_name") or "")
    snapshot["browser_content"] = str(primary.get("content") or "")
    snapshot["debug"]["browser_window"] = {
        "title": primary.get("title") or "",
        "process_name": primary.get("process_name") or "",
        "pid": primary.get("pid") or 0,
        "hwnd": primary.get("hwnd") or 0,
        "url": primary.get("url") or "",
    }
    snapshot["debug"]["browser_windows"] = [
        {
            "title": page.get("title") or "",
            "process_name": page.get("process_name") or "",
            "pid": page.get("pid") or 0,
            "hwnd": page.get("hwnd") or 0,
            "url": page.get("url") or "",
        }
        for page in pages
    ]


def context_snapshot(
    include_clipboard: bool = True,
    include_selection: bool = True,
    include_selected_paths: bool = False,
    include_active_window_text: bool = False,
    include_browser_content: bool = False,
    include_browser_url: bool = False,
    capture_focus: bool = False,
    require_active_selection_owner: bool = True,
    selection_dedupe_key: str = "",
) -> dict[str, Any]:
    """Handle context snapshot for runtime workers native host."""
    t0 = time.monotonic()
    active = _active_app()
    document_window = _frontmost_document_window() if IS_MAC else {}
    t_app = time.monotonic()
    snapshot = {
        "platform": sys.platform,
        "active_app": active,
        "document_window": document_window,
        "selected_text": "",
        "stale_selected_text": "",
        "selected_paths": [],
        "clipboard_text": "",
        "active_window_text": "",
        "browser_url": "",
        "browser_hwnd": 0,
        "browser_app": "",
        "browser_content": "",
        "browser_pages": [],
        "screen_size": _screen_size(),
        "focus_token": 0,
        "captured_at": time.time(),
        "debug": {
            "runtime": _runtime_debug(),
            "window": dict(_last_context_window_debug),
        },
    }
    # Calc cells are a structured application target, not a text paste-back
    # target. Trying to cache Calc's focused cell through UIA can dereference a
    # stale COM element (and is never used by the Calc API action path).
    try:
        from core.actions.adapters.calc import is_calc_app

        structured_calc_target = is_calc_app(active)
    except Exception:
        structured_calc_target = False
    allow_copy_after_empty_uia = False
    if IS_WIN:
        try:
            from core.actions.adapters.vscode import is_vscode_app

            allow_copy_after_empty_uia = is_vscode_app(active)
        except Exception:
            allow_copy_after_empty_uia = False

    # Grab the focused text element first (before selection/clipboard work), while
    # the user's field is still focused, so a later rewrite can be written back
    # in place via Accessibility even if focus has since moved.
    if capture_focus and not structured_calc_target:
        if allow_copy_after_empty_uia:
            snapshot["focus_token"] = _capture_focus(
                source_window_id=int(active.get("window_id") or 0),
                search_window_documents=True,
            )
        else:
            snapshot["focus_token"] = _capture_focus()
        if snapshot["focus_token"] and isinstance(_focus_cache.get("editor_point"), dict):
            snapshot["editor_point"] = dict(_focus_cache["editor_point"])
        if snapshot["focus_token"] and isinstance(_focus_cache.get("selection_rect"), dict):
            snapshot["selection_rect"] = dict(_focus_cache["selection_rect"])
    sel_dt = path_dt = clip_dt = window_text_dt = br_dt = 0.0
    selection_kind = _selection_source_kind(active) if include_selected_paths else "text"
    defer_app_selection = bool(
        include_selection and selection_kind != "paths" and structured_calc_target
    )
    snapshot["app_selection_deferred"] = defer_app_selection
    if include_selection and selection_kind != "paths" and not defer_app_selection:
        _s = time.monotonic()
        resolved_vscode_selection = bool(
            allow_copy_after_empty_uia
            and snapshot["focus_token"]
            and _focus_cache.get("token") == snapshot["focus_token"]
            and _focus_cache.get("kind") == "win-uia"
            and not bool(_focus_cache.get("collapsed"))
            and str(_focus_cache.get("selected_text") or "")
        )
        if resolved_vscode_selection:
            # The same exact Monaco range now supplies both text and geometry;
            # do not synthesize Ctrl+C unless UIA failed to resolve that range.
            snapshot["selected_text"] = str(_focus_cache["selected_text"])
        else:
            selection_options: dict[str, Any] = {
                "allow_clipboard_fallback": True,
                "active_pid": int(active.get("pid") or 0),
                "require_active_owner": bool(require_active_selection_owner),
                "selection_dedupe_key": str(selection_dedupe_key or ""),
            }
            if allow_copy_after_empty_uia:
                selection_options["allow_copy_after_empty_uia"] = True
            snapshot["selected_text"], snapshot["stale_selected_text"] = (
                _selected_text_and_stale(**selection_options)
            )
        if (
            not snapshot["selected_text"]
            and snapshot["focus_token"]
            and _focus_cache.get("token") == snapshot["focus_token"]
            and _focus_cache.get("kind") == "win-edit"
        ):
            # WordPad's RichEdit control exposes its exact selected range through
            # Win32 even when Ctrl+C/clipboard selection capture is unavailable.
            snapshot["selected_text"] = str(_focus_cache.get("selected_text") or "")
        sel_dt = time.monotonic() - _s
        focus_state = _focus_anchor(int(snapshot.get("focus_token") or 0))
        vscode_focus_matches = bool(
            allow_copy_after_empty_uia
            and focus_state.get("kind") == "win-uia"
            and not bool(focus_state.get("collapsed"))
            and bool(focus_state.get("range_context_bound"))
            and str(focus_state.get("selected_text") or "") == snapshot["selected_text"]
        )
        should_retry_focus = bool(
            capture_focus
            and not structured_calc_target
            and snapshot["selected_text"]
            and (
                not snapshot["focus_token"]
                or (allow_copy_after_empty_uia and not vscode_focus_matches)
            )
        )
        if should_retry_focus:
            # Chromium can expose the copied selection a few milliseconds
            # before its renderer publishes the corresponding UIA TextRange.
            # Copying does not move focus, so one bounded retry binds the same
            # user-selected range without guessing or targeting a new control.
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                if allow_copy_after_empty_uia:
                    snapshot["focus_token"] = _capture_focus(
                        source_window_id=int(active.get("window_id") or 0),
                        search_window_documents=True,
                    )
                    focus_state = _focus_anchor(int(snapshot.get("focus_token") or 0))
                    if (
                        focus_state.get("kind") == "win-uia"
                        and not bool(focus_state.get("collapsed"))
                        and bool(focus_state.get("range_context_bound"))
                        and str(focus_state.get("selected_text") or "")
                        == snapshot["selected_text"]
                    ):
                        break
                else:
                    snapshot["focus_token"] = _capture_focus()
                    if snapshot["focus_token"]:
                        break
                time.sleep(0.05)
            focus_state = _focus_anchor(int(snapshot.get("focus_token") or 0))
            if allow_copy_after_empty_uia and not (
                focus_state.get("kind") == "win-uia"
                and not bool(focus_state.get("collapsed"))
                and bool(focus_state.get("range_context_bound"))
                and str(focus_state.get("selected_text") or "") == snapshot["selected_text"]
            ):
                snapshot["focus_token"] = 0
                focus_state = {}
            if isinstance(focus_state.get("editor_point"), dict):
                snapshot["editor_point"] = dict(focus_state["editor_point"])
            if isinstance(focus_state.get("selection_rect"), dict):
                snapshot["selection_rect"] = dict(focus_state["selection_rect"])
    if capture_focus and IS_WIN:
        anchor = selection_anchor_resolve(
            focus_token=int(snapshot.get("focus_token") or 0),
            source_window_id=int(active.get("window_id") or 0),
            allow_mouse=True,
        )
        if anchor.get("ok") and anchor.get("visible"):
            snapshot["selection_rect"] = dict(anchor.get("selection_rect") or {})
            snapshot["selection_anchor_source"] = str(anchor.get("source") or "")
        else:
            snapshot.pop("selection_rect", None)
    if include_active_window_text and not IS_WIN and not IS_MAC and os.environ.get("WAYLAND_DISPLAY"):
        _s = time.monotonic()
        try:
            from core.platform import linux_atspi

            snapshot["active_window_text"] = linux_atspi.get_active_window_text()
        except Exception:
            snapshot["active_window_text"] = ""
        window_text_dt = time.monotonic() - _s
    if include_clipboard:
        _s = time.monotonic()
        snapshot["clipboard_text"] = clipboard_get()["text"]
        clip_dt = time.monotonic() - _s
    if include_selected_paths and selection_kind == "paths":
        _s = time.monotonic()
        snapshot["selected_paths"] = selected_paths()
        path_dt = time.monotonic() - _s
    if include_browser_content or include_browser_url:
        _s = time.monotonic()
        try:
            from core.context_fetcher import (
                _BROWSER_PROCS,
                WindowInfo,
                _browser_content,
                get_browser_windows_for_context,
            )

            if not IS_WIN and not IS_MAC and os.environ.get("WAYLAND_DISPLAY"):
                active_process = str(active.get("process_name") or "").strip()
                browser_windows = []
                if active_process.lower() in _BROWSER_PROCS or active.get("browser_url"):
                    browser_windows.append(
                        WindowInfo(
                            title=str(active.get("name") or ""),
                            process_name=active_process,
                            pid=int(active.get("pid") or 0),
                            url=str(active.get("browser_url") or ""),
                        )
                    )
            else:
                active_hwnd = int(active.get("window_id") or 0) if not IS_MAC else 0
                browser_windows = get_browser_windows_for_context(active_hwnd)
            pages: list[dict[str, Any]] = []
            for browser_window in browser_windows:
                content = ""
                if include_browser_content and (browser_window.hwnd or browser_window.url or browser_window.process_name):
                    content = _browser_content(browser_window)
                pages.append(_browser_page_payload(browser_window, content))
            _apply_primary_browser_page(snapshot, pages)
        except Exception as exc:  # noqa: BLE001 - browser context should not block answering
            snapshot["browser_error"] = f"{type(exc).__name__}: {exc}"
        br_dt = time.monotonic() - _s
    print(
        f"[context.snapshot] active_app={t_app - t0:.2f}s selected={sel_dt:.2f}s paths={path_dt:.2f}s "
        f"window_text={window_text_dt:.2f}s "
        f"clipboard={clip_dt:.2f}s browser={br_dt:.2f}s total={time.monotonic() - t0:.2f}s "
        f"(app={active.get('name')!r} hwnd={active.get('window_id') or 0} "
        f"selection_kind={selection_kind} sel_len={len(snapshot['selected_text'])} "
        f"window_text_len={len(snapshot['active_window_text'])} "
        f"paths={len(snapshot['selected_paths'])} url={'y' if snapshot['browser_url'] else 'n'})",
        flush=True,
    )
    return snapshot


def await_selection_context(
    timeout: float = 30.0,
    settle_ms: int = 250,
    include_clipboard: bool = True,
    include_selected_paths: bool = True,
) -> dict[str, Any]:
    """Wait for a mouse or keyboard selection gesture to finish, then capture once."""
    deadline = time.monotonic() + max(0.5, float(timeout or 30.0))
    released = threading.Event()
    mouse_listener = None
    keyboard_listener = None
    modifiers: set[str] = set()
    saw_select_all = False
    try:
        from pynput import keyboard, mouse  # type: ignore

        def _on_click(_x, _y, _button, pressed):
            if not pressed:
                released.set()
                return False
            return None

        def _key_name(key: Any) -> str:
            try:
                return str(key.char or "").lower()
            except Exception:
                return str(key).lower()

        def _on_press(key):
            nonlocal saw_select_all
            name = _key_name(key)
            if "ctrl" in name or "cmd" in name:
                modifiers.add("mod")
            elif "shift" in name:
                modifiers.add("shift")
            elif name == "a" and "mod" in modifiers:
                saw_select_all = True
            return None

        def _on_release(key):
            nonlocal saw_select_all
            name = _key_name(key)
            if saw_select_all and (name == "a" or "ctrl" in name or "cmd" in name):
                released.set()
                return False
            if "shift" in modifiers and ("left" in name or "right" in name or "up" in name or "down" in name):
                released.set()
                return False
            if "ctrl" in name or "cmd" in name:
                modifiers.discard("mod")
            elif "shift" in name:
                modifiers.discard("shift")
            return None

        mouse_listener = mouse.Listener(on_click=_on_click)
        keyboard_listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
        mouse_listener.start()
        keyboard_listener.start()
        released.wait(max(0.0, deadline - time.monotonic()))
    except Exception:
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    finally:
        try:
            if mouse_listener is not None:
                mouse_listener.stop()
        except Exception:
            pass
        try:
            if keyboard_listener is not None:
                keyboard_listener.stop()
        except Exception:
            pass
    time.sleep(max(0, int(settle_ms or 0)) / 1000.0)
    return context_snapshot(
        include_clipboard=bool(include_clipboard),
        include_selection=True,
        include_selected_paths=bool(include_selected_paths),
        include_browser_content=False,
        include_browser_url=False,
        capture_focus=False,
        require_active_selection_owner=False,
    )


def context_browser_content(url: str = "", hwnd: int = 0, app: str = "") -> dict[str, Any]:
    """Read the page text for a browser window captured at hotkey time.

    On Windows this reads the rendered window by handle (UIA does not need
    focus), then falls back to an HTTP fetch of the URL. On macOS it asks the
    named browser app (*app*) for its active tab text via AppleScript, which
    works even though the overlay now holds focus. On Linux/X11 the tab URL
    is resolved from the captured window id via AT-SPI2 and the page text is
    an HTTP fetch of that URL. Returns {"url", "content"}.
    """
    try:
        from core.context_fetcher import WindowInfo, _browser_content

        browser_url = str(url or "")
        browser_app = str(app or "")
        if IS_MAC and browser_app and not browser_url:
            try:
                from core.context_fetcher import _mac_browser_url

                browser_url = _mac_browser_url(browser_app)
            except Exception:
                browser_url = ""

        win = WindowInfo(url=browser_url, hwnd=int(hwnd or 0), process_name=browser_app)
        content = _browser_content(win)
        return {"url": win.url, "content": content or "", "hwnd": int(hwnd or 0)}
    except Exception as exc:  # noqa: BLE001 - browser context should not block answering
        return {"url": url, "content": "", "hwnd": int(hwnd or 0), "error": f"{type(exc).__name__}: {exc}"}


def capture_fullscreen(path: str = "") -> dict[str, Any]:
    """Handle capture fullscreen for runtime workers native host."""
    if not path:
        import tempfile

        path = str(Path(tempfile.gettempdir()) / f"openwand-capture-{int(time.time() * 1000)}.png")
    if not IS_MAC:
        try:
            from core.capture import get_screen_snippet

            img = get_screen_snippet()
            img.save(path, format="PNG")
            return {
                "ok": True,
                "path": path,
                "size": os.path.getsize(path) if os.path.exists(path) else 0,
            }
        except Exception as exc:  # noqa: BLE001 - surface capture failure to caller
            return {"ok": False, "path": path, "error": f"{type(exc).__name__}: {exc}"}
    try:
        from core.platform import macos_native

        ok = macos_native.capture_screen_to_file(path)
        return {
            "ok": ok,
            "path": path,
            "size": os.path.getsize(path) if os.path.exists(path) else 0,
        }
    except Exception as exc:  # noqa: BLE001 - surface capture failure to caller
        return {"ok": False, "path": path, "error": f"{type(exc).__name__}: {exc}"}


def _normalize_region(region: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize region."""
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
    """Handle capture region for runtime workers native host."""
    if not path:
        import tempfile

        path = str(Path(tempfile.gettempdir()) / f"openwand-region-{int(time.time() * 1000)}.png")
    normalized = _normalize_region(region)
    if normalized is None:
        return {
            "ok": False,
            "path": path,
            "region": region,
            "error": "ValueError: selected capture region is empty or invalid",
        }
    if not IS_MAC:
        try:
            from core.capture import get_screen_snippet

            img = get_screen_snippet(normalized)
            img.save(path, format="PNG")
            return {"ok": True, "path": path, "region": normalized}
        except Exception as exc:  # noqa: BLE001 - surface capture failure to caller
            return {
                "ok": False,
                "path": path,
                "region": region,
                "error": f"{type(exc).__name__}: {exc}",
            }
    try:
        from core.platform import macos_native

        ok = macos_native.capture_screen_to_file(path, region=normalized)
        return {"ok": ok, "path": path, "region": normalized}
    except Exception as exc:  # noqa: BLE001 - surface capture failure to caller
        return {
            "ok": False,
            "path": path,
            "region": normalized,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _plog(event: str) -> None:
    """Paste-back diagnostics → native.stderr.log (captured by the supervisor)."""
    line = f"{time.strftime('%H:%M:%S')} [native.paste] {event}"
    print(line, file=sys.stderr, flush=True)


def _frontmost_pid() -> int:
    """pid of the app macOS currently considers frontmost (0 if unknown)."""
    if not IS_MAC:
        return 0
    try:
        import AppKit  # type: ignore

        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app is not None else 0
    except Exception:
        return 0


def _activate_pid(pid: int) -> dict[str, Any]:
    """Bring the app with `pid` to the front and confirm it actually came forward.

    `activateWithOptions_(NSApplicationActivateIgnoringOtherApps)` is deprecated on
    macOS 14+ and is frequently ignored, especially on remote/headless sessions, so
    we (1) prefer the modern no-arg `activate()` when available, (2) fall back to the
    legacy options, and (3) poll `frontmostApplication()` to verify focus landed on
    the target before the caller synthesises Cmd+V. Returns a diagnostics dict.
    """
    result: dict[str, Any] = {
        "requested_pid": int(pid or 0),
        "called": False,
        "confirmed": False,
        "app_name": "",
        "frontmost_pid": 0,
        "error": "",
    }
    if not IS_MAC or not pid:
        result["error"] = "no pid" if IS_MAC else "not macos"
        return result
    try:
        import AppKit  # type: ignore

        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        if app is None:
            result["error"] = "pid not running"
            _plog(f"activate pid={pid} -> app not running")
            return result
        result["app_name"] = str(app.localizedName() or "")
        # Prefer the non-deprecated activate() (10.15+); fall back to the legacy
        # options API if the modern selector is unavailable.
        if app.respondsToSelector_("activate"):
            app.activate()
            result["called"] = True
        else:
            opts = (
                AppKit.NSApplicationActivateIgnoringOtherApps
                | AppKit.NSApplicationActivateAllWindows
            )
            app.activateWithOptions_(opts)
            result["called"] = True
        # Activation is asynchronous; poll until the target is actually frontmost.
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            front = _frontmost_pid()
            result["frontmost_pid"] = front
            if front == int(pid):
                result["confirmed"] = True
                break
            time.sleep(0.05)
        _plog(
            f"activate pid={pid} name={result['app_name']!r} "
            f"confirmed={result['confirmed']} frontmost={result['frontmost_pid']}"
        )
        return result
    except Exception as exc:  # noqa: BLE001 - report activation failure to caller
        result["error"] = f"{type(exc).__name__}: {exc}"
        _plog(f"activate pid={pid} raised {result['error']}")
        return result


# --- Anchored paste-back focus cache ---------------------------------------
# Lets a rewrite land in the originally-focused text field/range instead of the
# caret that happens to be focused after the model replies. The cached native
# objects must stay in THIS long-lived worker process; only a small integer token
# crosses IPC.
_AX_FOCUSED_ATTR = "AXFocusedUIElement"
_AX_SELECTED_TEXT_ATTR = "AXSelectedText"
_AX_ROLE_ATTR = "AXRole"
_AX_ERROR_SUCCESS = 0  # kAXErrorSuccess
_UIA_TEXT_PATTERN_ID = 10014
_UIA_TEXT_PATTERN_RANGE_ENDPOINT_START = 0
_UIA_TEXT_PATTERN_RANGE_ENDPOINT_END = 1
_WM_GETTEXT = 0x000D
_WM_GETTEXTLENGTH = 0x000E
_EM_GETSEL = 0x00B0
_EM_SETSEL = 0x00B1
_EM_REPLACESEL = 0x00C2
_EM_POSFROMCHAR = 0x00D6
_MAX_NATIVE_EDIT_CHARS = 4_000_000

_focus_seq = 0
_focus_cache: dict[str, Any] = {}  # {"token": int, "kind": str, ...native objects}
_focus_anchors: dict[int, dict[str, Any]] = {}
_MAX_FOCUS_ANCHORS = 32


def _remember_focus_anchor() -> None:
    """Keep each captured rewrite target independent of later captures."""
    token = int(_focus_cache.get("token") or 0)
    if not token:
        return
    _focus_anchors[token] = dict(_focus_cache)
    while len(_focus_anchors) > _MAX_FOCUS_ANCHORS:
        _focus_anchors.pop(next(iter(_focus_anchors)))


def _focus_anchor(focus_token: int) -> dict[str, Any]:
    token = int(focus_token or 0)
    if token and int(_focus_cache.get("token") or 0) == token:
        return _focus_cache
    return _focus_anchors.get(token, {})


def selection_anchor_release(focus_token: int = 0) -> dict[str, Any]:
    """Release one annotation's cached native target and geometry."""
    token = int(focus_token or 0)
    released = _focus_anchors.pop(token, None) is not None
    if token and int(_focus_cache.get("token") or 0) == token:
        _focus_cache.clear()
        released = True
    return {"ok": True, "released": released}


def _capture_focus(
    *,
    source_window_id: int = 0,
    search_window_documents: bool = False,
) -> int:
    """Cache the hotkey-time text target for later paste-back."""
    if IS_MAC:
        return _ax_capture_focus()
    if IS_WIN:
        edit_token = _win_edit_capture_focus()
        if edit_token:
            return edit_token
        uia_token = _win_uia_capture_focus(
            source_window_id=source_window_id,
            search_window_documents=search_window_documents,
        )
        return uia_token
    return 0


def _win_utf16_slice(text: str, start: int, end: int) -> str:
    """Slice Windows text offsets, which are UTF-16 code units, without guessing."""
    raw = str(text or "").encode("utf-16-le", errors="surrogatepass")
    unit_count = len(raw) // 2
    lower = max(0, min(int(start), unit_count))
    upper = max(lower, min(int(end), unit_count))
    return raw[lower * 2 : upper * 2].decode("utf-16-le", errors="surrogatepass")


def _win_focused_input_hwnd() -> int:
    """Return the foreground thread's focused child control."""
    if not IS_WIN:
        return 0
    try:
        from ctypes import wintypes

        class GuiThreadInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        user32 = ctypes.windll.user32
        root_hwnd = int(user32.GetForegroundWindow() or 0)
        thread_id = int(user32.GetWindowThreadProcessId(root_hwnd, None) or 0)
        info = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
        if thread_id and user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return int(info.hwndFocus or info.hwndCaret or 0)
    except Exception:
        pass
    return 0


def _win_direct_edit_class_supported(class_name: str) -> bool:
    """Return whether cross-process Edit messages are atomic for this control."""
    folded = str(class_name or "").strip().casefold()
    if folded == "edit":
        return True
    # Classic RichEdit controls marshal EM_REPLACESEL correctly. Windows 11
    # Notepad's RichEditD2DPT looks related but can reject or partially apply
    # the same cross-process message, so it must use the verified UIA paste.
    return folded.startswith("richedit") and "d2d" not in folded


def _win_edit_control_snapshot(
    input_hwnd: int = 0,
    *,
    require_selection: bool = True,
) -> dict[str, Any]:
    """Read one standard Edit/RichEdit control and its exact selected range."""
    if not IS_WIN:
        return {}
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = int(input_hwnd or _win_focused_input_hwnd())
        if not hwnd or not user32.IsWindow(hwnd):
            return {}
        class_buffer = ctypes.create_unicode_buffer(256)
        if not user32.GetClassNameW(hwnd, class_buffer, len(class_buffer)):
            return {}
        class_name = str(class_buffer.value or "")
        if not _win_direct_edit_class_supported(class_name):
            return {}
        length = int(user32.SendMessageW(hwnd, _WM_GETTEXTLENGTH, 0, 0) or 0)
        if length < 0 or length > _MAX_NATIVE_EDIT_CHARS:
            return {}
        text_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.SendMessageW(hwnd, _WM_GETTEXT, length + 1, text_buffer)
        document_text = str(text_buffer.value or "")
        start = wintypes.DWORD()
        end = wintypes.DWORD()
        user32.SendMessageW(hwnd, _EM_GETSEL, ctypes.byref(start), ctypes.byref(end))
        selection_start = int(start.value)
        selection_end = int(end.value)
        if require_selection and selection_end <= selection_start:
            return {}
        root_hwnd = int(user32.GetAncestor(hwnd, 2) or 0) or hwnd  # GA_ROOT
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        prefix = _win_utf16_slice(document_text, 0, selection_start)
        selected_text = _win_utf16_slice(document_text, selection_start, selection_end)
        suffix_units = len(document_text.encode("utf-16-le", errors="surrogatepass")) // 2
        suffix = _win_utf16_slice(document_text, selection_end, suffix_units)
        if document_text != f"{prefix}{selected_text}{suffix}":
            return {}
        snapshot = {
            "input_hwnd": hwnd,
            "root_hwnd": root_hwnd,
            "target_pid": int(pid.value),
            "class_name": class_name,
            "selection_start": selection_start,
            "selection_end": selection_end,
            "selected_text": selected_text,
            "document_prefix": prefix,
            "document_suffix": suffix,
            "document_text": document_text,
            "range_context_bound": True,
        }
        selection_rect = _win_edit_selection_screen_rect(
            hwnd,
            class_name=class_name,
            selection_end=selection_end,
            document_units=suffix_units,
        )
        if selection_rect:
            snapshot["selection_rect"] = selection_rect
        return snapshot
    except Exception as exc:  # noqa: BLE001 - legacy controls are optional
        _plog(f"native edit snapshot raised {type(exc).__name__}: {exc}")
        return {}


def _win_edit_selection_screen_rect(
    hwnd: int,
    *,
    class_name: str,
    selection_end: int,
    document_units: int,
) -> dict[str, float]:
    """Return a screen-space anchor at a standard Edit/RichEdit selection end."""
    if not IS_WIN or not hwnd:
        return {}
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        folded_class = str(class_name or "").casefold()
        index = max(0, min(int(selection_end), max(0, int(document_units))))
        point = wintypes.POINT()
        if folded_class.startswith("richedit"):
            # RichEdit 2.0+ writes a POINT through wParam and reads the character
            # index from lParam. Unlike GetCaretPos, this does not need to attach
            # OpenWand's thread or change focus.
            user32.SendMessageW(hwnd, _EM_POSFROMCHAR, ctypes.byref(point), index)
        else:
            packed = int(user32.SendMessageW(hwnd, _EM_POSFROMCHAR, index, 0))
            if packed == -1:
                return {}
            point.x = ctypes.c_short(packed & 0xFFFF).value
            point.y = ctypes.c_short((packed >> 16) & 0xFFFF).value
        if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
            return {}
        dpi = int(user32.GetDpiForWindow(hwnd) or 96) if hasattr(user32, "GetDpiForWindow") else 96
        line_height = max(18, round(20 * max(96, dpi) / 96))
        return {
            "left": float(point.x),
            "top": float(point.y),
            "width": 2.0,
            "height": float(line_height),
        }
    except Exception as exc:  # noqa: BLE001 - placement falls back to the source window
        _plog(f"native edit selection rect unavailable: {type(exc).__name__}: {exc}")
        return {}


def _win_edit_capture_focus() -> int:
    """Bind WordPad and other standard RichEdit selections without UIA."""
    global _focus_seq
    snapshot = _win_edit_control_snapshot()
    if not snapshot:
        return 0
    _focus_seq += 1
    _focus_cache.clear()
    _focus_cache.update(snapshot)
    _focus_cache["token"] = _focus_seq
    _focus_cache["kind"] = "win-edit"
    geometry_range = _win_uia_focused_geometry_range(str(snapshot.get("selected_text") or ""))
    caret_rect = _win_caret_screen_rect(int(snapshot.get("root_hwnd") or 0))
    pointer_rect = _win_cursor_screen_rect()
    if geometry_range is not None:
        anchor_affinity, anchor_edge = _win_uia_anchor_details(
            geometry_range,
            caret_rect=caret_rect,
            pointer_rect=pointer_rect,
        )
        if anchor_affinity:
            _focus_cache["anchor_affinity"] = anchor_affinity
        if anchor_edge:
            _focus_cache["anchor_edge"] = anchor_edge
        geometry_rect = _win_uia_anchor_screen_rect(geometry_range, anchor_affinity)
        if geometry_rect:
            _focus_cache["geometry_range"] = geometry_range
            _focus_cache["selection_rect"] = geometry_rect
            _focus_cache["selection_rect_source"] = "uia"
            _focus_cache["last_selection_rect"] = dict(geometry_rect)
    elif caret_rect:
        # Standard Edit controls expose both selection offsets and the active
        # caret. Compare the endpoints once, while the source still owns focus,
        # so a backwards Shift/drag selection remains backwards later.
        document_units = len(
            str(snapshot.get("document_text") or "").encode(
                "utf-16-le", errors="surrogatepass"
            )
        ) // 2
        start_rect = _win_edit_selection_screen_rect(
            int(snapshot.get("input_hwnd") or 0),
            class_name=str(snapshot.get("class_name") or ""),
            selection_end=int(snapshot.get("selection_start") or 0),
            document_units=document_units,
        )
        end_rect = dict(snapshot.get("selection_rect") or {})
        affinity = _win_endpoint_affinity(caret_rect, start_rect, end_rect, strict=False)
        if affinity:
            _focus_cache["anchor_affinity"] = affinity
    _plog(
        f"native edit capture token={_focus_seq} class={snapshot.get('class_name')!r} "
        f"selection={snapshot.get('selection_start')}:{snapshot.get('selection_end')}"
    )
    _remember_focus_anchor()
    return _focus_seq


def _win_uia_focused_geometry_range(expected_selected_text: str = "") -> Any | None:
    """Capture a UIA range only for geometry while native Edit owns mutation."""
    if not IS_WIN:
        return None
    try:
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        import comtypes.gen.UIAutomationClient as uiac  # type: ignore

        uia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=uiac.IUIAutomation,
        )
        element = uia.GetFocusedElement()
        raw_pattern = element.GetCurrentPattern(_UIA_TEXT_PATTERN_ID)
        text_pattern = raw_pattern.QueryInterface(uiac.IUIAutomationTextPattern)
        selections = text_pattern.GetSelection()
        if selections.Length <= 0:
            return None
        text_range = selections.GetElement(0)
        if expected_selected_text:
            selected = str(text_range.GetText(-1) or "")
            if selected != str(expected_selected_text):
                return None
        return text_range
    except Exception as exc:  # noqa: BLE001 - geometry falls through to caret/mouse
        _plog(f"uia geometry range unavailable: {type(exc).__name__}: {exc}")
        return None


def _ax_capture_focus() -> int:
    """Cache the system-wide focused UI element; return a token (0 on failure)."""
    global _focus_seq
    if not IS_MAC:
        return 0
    try:
        import HIServices  # type: ignore  # pyobjc-framework-ApplicationServices

        system = HIServices.AXUIElementCreateSystemWide()
        err, focused = HIServices.AXUIElementCopyAttributeValue(
            system, _AX_FOCUSED_ATTR, None
        )
        if err != _AX_ERROR_SUCCESS or focused is None:
            _plog(f"ax capture: no focused element (err={err})")
            return 0
        _focus_seq += 1
        _focus_cache.clear()
        _focus_cache["token"] = _focus_seq
        _focus_cache["kind"] = "mac-ax"
        _focus_cache["element"] = focused
        _remember_focus_anchor()
        _plog(f"ax capture token={_focus_seq} ok")
        return _focus_seq
    except Exception as exc:  # noqa: BLE001 - AX is best-effort
        _plog(f"ax capture raised {type(exc).__name__}: {exc}")
        return 0


def _win_uia_capture_focus(
    *,
    source_window_id: int = 0,
    search_window_documents: bool = False,
) -> int:
    """Cache the focused Windows UIA text range; return a token (0 on failure)."""
    global _focus_seq
    if not IS_WIN:
        return 0
    try:
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        import comtypes.gen.UIAutomationClient as uiac  # type: ignore

        uia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=uiac.IUIAutomation,
        )
        candidate = _win_uia_selection_candidate(
            uia,
            uiac,
            source_window_id=source_window_id,
            search_window_documents=search_window_documents,
        )
        if not candidate:
            _plog("uia capture: focused element has no text selection")
            return 0
        element = candidate["element"]
        text_pattern = candidate["text_pattern"]
        text_range = candidate["range"]
        collapsed = bool(candidate["collapsed"])
        _focus_seq += 1
        _focus_cache.clear()
        _focus_cache["token"] = _focus_seq
        _focus_cache["kind"] = "win-uia"
        _focus_cache["element"] = element
        _focus_cache["range"] = text_range
        _focus_cache["collapsed"] = collapsed
        _focus_cache["selection_source"] = str(candidate["source"])
        _focus_cache["selected_text"] = str(candidate["selected_text"])
        _focus_cache.update(_win_uia_range_context(text_pattern, text_range))
        _focus_cache.update(_win_capture_background_input_target(element))
        caret_rect = _win_caret_screen_rect(
            int(_focus_cache.get("root_hwnd") or source_window_id or 0)
        )
        anchor_affinity, anchor_edge = _win_uia_anchor_details(
            text_range,
            caret_rect=caret_rect,
            pointer_rect=_win_cursor_screen_rect(),
        )
        if anchor_affinity:
            _focus_cache["anchor_affinity"] = anchor_affinity
        if anchor_edge:
            _focus_cache["anchor_edge"] = anchor_edge
        selection_rect = _win_uia_anchor_screen_rect(text_range, anchor_affinity)
        if selection_rect:
            _focus_cache["selection_rect"] = selection_rect
            _focus_cache["last_selection_rect"] = dict(selection_rect)
        editor_point = _win_uia_editor_client_point(
            text_range,
            int(_focus_cache.get("root_hwnd") or 0),
        )
        if editor_point:
            _focus_cache["editor_point"] = editor_point
        _remember_focus_anchor()
        _plog(
            f"uia capture token={_focus_seq} collapsed={collapsed} "
            f"source={candidate['source']} ok"
        )
        return _focus_seq
    except Exception as exc:  # noqa: BLE001 - UIA is best-effort
        _plog(f"uia capture raised {type(exc).__name__}: {exc}")
        return 0


def _win_uia_selection_candidate(
    uia: Any,
    uiac: Any,
    *,
    source_window_id: int = 0,
    search_window_documents: bool = False,
) -> dict[str, Any]:
    """Resolve the real selected TextRange behind Chromium/Monaco focus proxies."""
    focused = uia.GetFocusedElement()
    elements: list[tuple[str, Any]] = [("focused", focused)]
    if search_window_documents:
        # Monaco often puts UIA keyboard focus on its textarea/proxy while the
        # enclosing Document owns the visible editor selection. Prefer the
        # nearest ancestor before considering another visible split editor.
        try:
            walker = uia.RawViewWalker
            current = focused
            for _depth in range(24):
                current = walker.GetParentElement(current)
                if current is None:
                    break
                elements.append(("focused-ancestor", current))
        except Exception:
            pass
        if source_window_id:
            try:
                root = uia.ElementFromHandle(int(source_window_id))
                descendants = getattr(uiac, "TreeScope_Descendants", 4)
                keyboard_focus_property = getattr(
                    uiac,
                    "UIA_HasKeyboardFocusPropertyId",
                    30008,
                )
                focused_descendants = root.FindAll(
                    descendants,
                    uia.CreatePropertyCondition(keyboard_focus_property, True),
                )
                # An exact-window focused descendant beats GetFocusedElement:
                # Chromium can return its RootWebArea or another desktop's
                # provider while Monaco's native-edit-context owns the caret.
                window_focused = [
                    ("window-focused", focused_descendants.GetElement(index))
                    for index in range(min(int(focused_descendants.Length or 0), 16))
                ]
                elements = window_focused + elements
                control_type_property = getattr(uiac, "UIA_ControlTypePropertyId", 30003)
                document_type = getattr(uiac, "UIA_DocumentControlTypeId", 50030)
                documents = root.FindAll(
                    descendants,
                    uia.CreatePropertyCondition(control_type_property, document_type),
                )
                for index in range(min(int(documents.Length or 0), 32)):
                    document = documents.GetElement(index)
                    try:
                        if bool(document.CurrentIsOffscreen):
                            continue
                    except Exception:
                        pass
                    elements.append(("window-document", document))
            except Exception:
                pass

    start_endpoint = getattr(
        uiac,
        "TextPatternRangeEndpoint_Start",
        _UIA_TEXT_PATTERN_RANGE_ENDPOINT_START,
    )
    end_endpoint = getattr(
        uiac,
        "TextPatternRangeEndpoint_End",
        _UIA_TEXT_PATTERN_RANGE_ENDPOINT_END,
    )
    collapsed_fallback: dict[str, Any] = {}
    for source, element in elements:
        try:
            raw_pattern = element.GetCurrentPattern(_UIA_TEXT_PATTERN_ID)
            text_pattern = raw_pattern.QueryInterface(uiac.IUIAutomationTextPattern)
            selections = text_pattern.GetSelection()
        except Exception:
            continue
        for index in range(int(selections.Length or 0)):
            try:
                live_range = selections.GetElement(index)
                try:
                    # Chromium's GetSelection range keeps some geometry tied to
                    # the provider's current selection. A later user selection
                    # can therefore move or invalidate an existing bubble's
                    # anchor. Clone the endpoints while selection A is current
                    # so each annotation remains bound to its original text.
                    text_range = live_range.Clone()
                except Exception:
                    text_range = live_range
                collapsed = (
                    text_range.CompareEndpoints(start_endpoint, text_range, end_endpoint) == 0
                )
                selected_text = "" if collapsed else str(text_range.GetText(-1) or "")
            except Exception:
                continue
            candidate = {
                "element": element,
                "text_pattern": text_pattern,
                "range": text_range,
                "collapsed": collapsed,
                "selected_text": selected_text,
                "source": source,
            }
            if not collapsed:
                return candidate
            if not collapsed_fallback:
                collapsed_fallback = candidate
    return collapsed_fallback


def _win_uia_range_context(text_pattern: Any, text_range: Any) -> dict[str, Any]:
    """Bind a UIA selection to its exact editable document prefix and suffix."""
    if not IS_WIN:
        return {}
    try:
        document_range = text_pattern.DocumentRange
        selected_text = str(text_range.GetText(-1) or "")
        prefix_range = document_range.Clone()
        prefix_range.MoveEndpointByRange(
            _UIA_TEXT_PATTERN_RANGE_ENDPOINT_END,
            text_range,
            _UIA_TEXT_PATTERN_RANGE_ENDPOINT_START,
        )
        suffix_range = document_range.Clone()
        suffix_range.MoveEndpointByRange(
            _UIA_TEXT_PATTERN_RANGE_ENDPOINT_START,
            text_range,
            _UIA_TEXT_PATTERN_RANGE_ENDPOINT_END,
        )
        prefix = str(prefix_range.GetText(-1) or "")
        suffix = str(suffix_range.GetText(-1) or "")
        document_text = str(document_range.GetText(-1) or "")
        if document_text != f"{prefix}{selected_text}{suffix}":
            return {}
        return {
            "range_context_bound": True,
            "selected_text": selected_text,
            "document_prefix": prefix,
            "document_suffix": suffix,
            "document_text": document_text,
        }
    except Exception as exc:  # noqa: BLE001 - older controls may expose only selection
        _plog(f"uia range context unavailable: {type(exc).__name__}: {exc}")
        return {}


def _win_vscode_select_captured_context(
    _text_pattern: Any,
    *,
    document_prefix: str,
    selected_text: str,
) -> bool:
    """Select captured A in VS Code by its immutable line/column, not live B."""
    if not IS_WIN or not selected_text:
        return False
    try:
        import keyboard  # type: ignore

        normalized_prefix = str(document_prefix or "").replace("\r\n", "\n").replace("\r", "\n")
        normalized_selected = str(selected_text).replace("\r\n", "\n").replace("\r", "\n")
        def position(value: str) -> tuple[int, int]:
            line = value.count("\n") + 1
            column_text = value.rsplit("\n", 1)[-1]
            column = len(column_text.encode("utf-16-le", errors="surrogatepass")) // 2 + 1
            return line, column

        def go_to(value: str) -> None:
            line, column = position(value)
            keyboard.send("ctrl+g")
            time.sleep(0.18)
            keyboard.write(f"{line}:{column}", delay=0.01)
            keyboard.send("enter")
            time.sleep(0.18)

        def chord(second_key: str) -> None:
            keyboard.send("ctrl+k")
            time.sleep(0.08)
            keyboard.send(f"ctrl+{second_key}")
            time.sleep(0.18)

        clipboard_before = str(clipboard_get().get("text") or "")
        try:
            for attempt in range(3):
                go_to(normalized_prefix)
                chord("b")  # editor.action.setSelectionAnchor
                go_to(f"{normalized_prefix}{normalized_selected}")
                chord("k")  # editor.action.selectFromAnchorToCursor

                sentinel = f"openwand-selection-check-{os.getpid()}-{time.monotonic_ns()}"
                if not clipboard_set(sentinel).get("ok"):
                    return False
                keyboard.send("ctrl+c")
                deadline = time.monotonic() + 0.45
                while time.monotonic() < deadline:
                    actual = str(clipboard_get().get("text") or "")
                    normalized_actual = actual.replace("\r\n", "\n").replace("\r", "\n")
                    if normalized_actual == normalized_selected:
                        return True
                    if actual != sentinel:
                        break
                    time.sleep(0.03)
                if attempt < 2:
                    time.sleep(0.08)
            return False
        finally:
            clipboard_set(clipboard_before)
    except Exception:
        return False


def _win_uia_current_selection_matches_cached(state: dict[str, Any]) -> bool | None:
    """Return whether the provider's live selection is still this annotation's range."""
    if not IS_WIN or state.get("kind") != "win-uia":
        return None
    try:
        import comtypes.gen.UIAutomationClient as uiac  # type: ignore

        element = state.get("element")
        raw_pattern = element.GetCurrentPattern(_UIA_TEXT_PATTERN_ID)
        text_pattern = raw_pattern.QueryInterface(uiac.IUIAutomationTextPattern)
        selections = text_pattern.GetSelection()
        if int(selections.Length or 0) <= 0:
            return False
        current = selections.GetElement(0)
        cached = state.get("range")
        if cached is None:
            return None
        start_endpoint = getattr(
            uiac,
            "TextPatternRangeEndpoint_Start",
            _UIA_TEXT_PATTERN_RANGE_ENDPOINT_START,
        )
        end_endpoint = getattr(
            uiac,
            "TextPatternRangeEndpoint_End",
            _UIA_TEXT_PATTERN_RANGE_ENDPOINT_END,
        )
        return bool(
            current.CompareEndpoints(start_endpoint, cached, start_endpoint) == 0
            and current.CompareEndpoints(end_endpoint, cached, end_endpoint) == 0
        )
    except Exception:
        return None
    return None


def _win_uia_editor_client_point(text_range: Any, root_hwnd: int) -> dict[str, float]:
    """Convert the captured UIA range rectangle to a DevTools viewport point."""
    if not IS_WIN or not root_hwnd:
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        selection_rect = _win_uia_selection_screen_rect(text_range)
        if not selection_rect:
            return {}
        left = float(selection_rect["left"])
        top = float(selection_rect["top"])
        width = float(selection_rect["width"])
        height = float(selection_rect["height"])
        window_rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(root_hwnd, ctypes.byref(window_rect)):
            return {}
        return {
            "x": max(0.0, left - float(window_rect.left) + max(1.0, width * 0.5)),
            "y": max(0.0, top - float(window_rect.top) + max(1.0, height * 0.5)),
        }
    except Exception:
        return {}


def _win_uia_visible_rectangles(text_range: Any) -> list[dict[str, float]]:
    """Return visible UIA text-line rectangles in native screen coordinates."""
    if not IS_WIN:
        return []
    try:
        values = list(text_range.GetBoundingRectangles() or [])
        if len(values) < 4:
            clone = text_range.Clone()
            clone.ExpandToEnclosingUnit(0)  # TextUnit_Character
            values = list(clone.GetBoundingRectangles() or [])
        rectangles: list[dict[str, float]] = []
        for index in range(0, len(values) - 3, 4):
            left, top, width, height = (float(values[index + offset]) for offset in range(4))
            # Chromium clips an offscreen Monaco selection to a 1-2 px sliver
            # at the viewport edge. It is not a visible text line and must not
            # keep the popup pinned to the editor border while the user scrolls.
            if width > 0 and height >= 4.0:
                rectangles.append(
                    {"left": left, "top": top, "width": width, "height": height}
                )
        return rectangles
    except Exception:
        return []


def _win_uia_selection_screen_rect(text_range: Any) -> dict[str, float]:
    """Return the document-end line of a visible UIA selection."""
    rectangles = _win_uia_visible_rectangles(text_range)
    return dict(rectangles[-1]) if rectangles else {}


def _win_uia_anchor_screen_rect(text_range: Any, affinity: str = "") -> dict[str, float]:
    """Return the visible text line containing the captured active endpoint."""
    if str(affinity or "").casefold() != "start":
        # Keep the established helper as the default path. Besides retaining
        # compatibility with accessibility-provider shims, this is the normal
        # document-order endpoint when no trustworthy caret signal exists.
        return _win_uia_selection_screen_rect(text_range)
    rectangles = _win_uia_visible_rectangles(text_range)
    return dict(rectangles[0]) if rectangles else {}


def _win_endpoint_affinity(
    point_rect: dict[str, Any] | None,
    start_rect: dict[str, Any] | None,
    end_rect: dict[str, Any] | None,
    *,
    strict: bool,
) -> str:
    """Identify which selection endpoint owns a captured caret/pointer."""
    return _win_endpoint_details(
        point_rect,
        start_rect,
        end_rect,
        strict=strict,
    )[0]


def _win_endpoint_details(
    point_rect: dict[str, Any] | None,
    start_rect: dict[str, Any] | None,
    end_rect: dict[str, Any] | None,
    *,
    strict: bool,
) -> tuple[str, str]:
    """Return the active document line and visual edge nearest a caret."""
    try:
        px = float((point_rect or {}).get("left"))
        py = float((point_rect or {}).get("top")) + float(
            (point_rect or {}).get("height")
        ) / 2.0
        start_left = float((start_rect or {}).get("left"))
        start_right = start_left + float((start_rect or {}).get("width"))
        start_y = float((start_rect or {}).get("top")) + float(
            (start_rect or {}).get("height")
        ) / 2.0
        end_left = float((end_rect or {}).get("left"))
        end_right = end_left + float((end_rect or {}).get("width"))
        end_y = float((end_rect or {}).get("top")) + float(
            (end_rect or {}).get("height")
        ) / 2.0
    except (TypeError, ValueError, OverflowError):
        return "", ""
    values = (px, py, start_left, start_right, start_y, end_left, end_right, end_y)
    if not all(math.isfinite(value) for value in values):
        return "", ""
    candidates = (
        (math.hypot(px - start_left, py - start_y), "start", "left"),
        (math.hypot(px - start_right, py - start_y), "start", "right"),
        (math.hypot(px - end_left, py - end_y), "end", "left"),
        (math.hypot(px - end_right, py - end_y), "end", "right"),
    )
    distance, affinity, edge = min(candidates)
    if strict:
        line_height = max(
            4.0,
            float((start_rect or {}).get("height") or 0),
            float((end_rect or {}).get("height") or 0),
        )
        # A mouse pointer is useful only when it is visibly at an endpoint.
        # This rejects an unrelated pointer left elsewhere by keyboard users.
        if distance > max(24.0, line_height * 1.75):
            return "", ""
    return affinity, edge


def _win_uia_anchor_affinity(
    text_range: Any,
    *,
    caret_rect: dict[str, Any] | None = None,
    pointer_rect: dict[str, Any] | None = None,
) -> str:
    """Resolve forward/backward selection direction at hotkey-capture time."""
    return _win_uia_anchor_details(
        text_range,
        caret_rect=caret_rect,
        pointer_rect=pointer_rect,
    )[0]


def _win_uia_anchor_details(
    text_range: Any,
    *,
    caret_rect: dict[str, Any] | None = None,
    pointer_rect: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Resolve the captured selection's document line and visual caret edge."""
    rectangles = _win_uia_visible_rectangles(text_range)
    if not rectangles:
        return "", ""
    start_rect = rectangles[0]
    end_rect = rectangles[-1]
    details = _win_endpoint_details(
        caret_rect,
        start_rect,
        end_rect,
        strict=False,
    )
    if details[0]:
        return details
    return _win_endpoint_details(
        pointer_rect,
        start_rect,
        end_rect,
        strict=True,
    )


def _win_cursor_screen_rect() -> dict[str, float]:
    """Return a small screen-space anchor at the current Windows pointer."""
    if not IS_WIN:
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return {}
        return {
            "left": float(point.x),
            "top": float(point.y - 10),
            "width": 2.0,
            "height": 20.0,
        }
    except Exception:
        return {}


def _win_caret_screen_rect(source_window_id: int = 0) -> dict[str, float]:
    """Return the source thread's OS caret rectangle without changing focus."""
    if not IS_WIN or not source_window_id:
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        class GuiThreadInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        user32 = ctypes.windll.user32
        source_hwnd = int(source_window_id)
        if not user32.IsWindow(source_hwnd):
            return {}
        thread_id = int(user32.GetWindowThreadProcessId(source_hwnd, None) or 0)
        info = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
        if not thread_id or not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return {}
        caret_hwnd = int(info.hwndCaret or info.hwndFocus or 0)
        if not caret_hwnd:
            return {}
        caret_root = int(user32.GetAncestor(caret_hwnd, 2) or 0)  # GA_ROOT
        if caret_root and caret_root != source_hwnd:
            return {}
        point = wintypes.POINT(int(info.rcCaret.left), int(info.rcCaret.top))
        if not user32.ClientToScreen(caret_hwnd, ctypes.byref(point)):
            return {}
        return {
            "left": float(point.x),
            "top": float(point.y),
            "width": float(max(2, int(info.rcCaret.right - info.rcCaret.left))),
            "height": float(max(18, int(info.rcCaret.bottom - info.rcCaret.top))),
        }
    except Exception:
        return {}


def _win_valid_selection_anchor(
    value: dict[str, Any] | None,
    *,
    source_window_id: int = 0,
) -> dict[str, float]:
    """Validate and normalize one screen-space anchor against its source window."""
    if not IS_WIN or not isinstance(value, dict):
        return {}
    try:
        left = float(value.get("left"))
        top = float(value.get("top"))
        width = float(value.get("width"))
        height = float(value.get("height"))
    except (TypeError, ValueError, OverflowError):
        return {}
    if not all(math.isfinite(item) for item in (left, top, width, height)):
        return {}
    if width <= 0 or height <= 0 or width > 100_000 or height > 100_000:
        return {}
    if not source_window_id:
        return {"left": left, "top": top, "width": width, "height": height}
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = int(source_window_id)
        window = wintypes.RECT()
        if not user32.IsWindow(hwnd) or not user32.GetWindowRect(hwnd, ctypes.byref(window)):
            return {}
        right = left + width
        bottom = top + height
        intersects = (
            right > float(window.left)
            and left < float(window.right)
            and bottom > float(window.top)
            and top < float(window.bottom)
        )
        if not intersects:
            return {}
    except Exception:
        return {}
    return {"left": left, "top": top, "width": width, "height": height}


def selection_anchor_resolve(
    focus_token: int = 0,
    source_window_id: int = 0,
    app_native_rect: dict[str, Any] | None = None,
    allow_mouse: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    """Resolve app-native -> accessibility -> OS caret -> mouse anchor geometry."""
    if not IS_WIN:
        return {"ok": False, "visible": False, "source": "unsupported", "selection_rect": {}}
    source_hwnd = int(source_window_id or 0)
    candidates: list[tuple[str, dict[str, Any] | None, bool, int]] = [
        ("app-native", app_native_rect, True, source_hwnd),
    ]
    exact_source = ""
    exact_viewport_hwnd = source_hwnd
    token = int(focus_token or 0)
    state = _focus_anchor(token)
    if state:
        kind = str(state.get("kind") or "")
        exact_rect: dict[str, Any] | None = None
        if kind == "win-edit":
            exact_viewport_hwnd = int(state.get("input_hwnd") or source_hwnd)
            geometry_range = state.get("geometry_range")
            class_name = str(state.get("class_name") or "")
            if geometry_range is not None:
                exact_source = "uia"
                exact_rect = _win_uia_anchor_screen_rect(
                    geometry_range,
                    str(state.get("anchor_affinity") or ""),
                )
            elif not class_name.casefold().startswith("richedit"):
                # Standard Edit returns packed coordinates by value. RichEdit's
                # pointer-based EM_POSFROMCHAR does not marshal reliably across
                # processes, so never treat its zeroed POINT as valid geometry.
                exact_source = "native-edit"
                document_text = str(state.get("document_text") or "")
                document_units = len(document_text.encode("utf-16-le", errors="surrogatepass")) // 2
                exact_rect = _win_edit_selection_screen_rect(
                    int(state.get("input_hwnd") or 0),
                    class_name=class_name,
                    selection_end=int(
                        (
                            state.get("selection_start")
                            if state.get("anchor_affinity") == "start"
                            else state.get("selection_end")
                        )
                        or 0
                    ),
                    document_units=document_units,
                )
        elif kind == "win-uia":
            exact_viewport_hwnd = int(state.get("input_hwnd") or source_hwnd)
            exact_source = "uia"
            selection_matches = (
                _win_uia_current_selection_matches_cached(state) if refresh else None
            )
            if selection_matches is False:
                # Chromium only exposes bounding rectangles for its current
                # selection. Keep this annotation on selection A's last exact
                # rectangle instead of letting a later selection B move it.
                exact_rect = dict(state.get("last_selection_rect") or {})
            else:
                exact_rect = _win_uia_anchor_screen_rect(
                    state.get("range"),
                    str(state.get("anchor_affinity") or ""),
                )
                if exact_rect:
                    state["last_selection_rect"] = dict(exact_rect)
        if exact_source:
            candidates.append((exact_source, exact_rect, True, exact_viewport_hwnd))
            # During scrolling, an exact range with no on-screen rectangle means
            # it moved out of view. Never replace it with the current caret or
            # mouse, which now belong to OpenWand's popup.
            if refresh and not _win_valid_selection_anchor(
                exact_rect,
                source_window_id=exact_viewport_hwnd,
            ):
                return {
                    "ok": True,
                    "visible": False,
                    "source": exact_source,
                    "selection_rect": {},
                }
    if not refresh:
        candidates.append(("os-caret", _win_caret_screen_rect(source_hwnd), False, source_hwnd))
        if allow_mouse:
            candidates.append(("mouse", _win_cursor_screen_rect(), False, source_hwnd))
    for source, candidate, exact, viewport_hwnd in candidates:
        rect = _win_valid_selection_anchor(
            candidate,
            source_window_id=viewport_hwnd,
        )
        if rect:
            # Rectangles from text providers do not all mean the same thing.
            # UIA/app-native rectangles cover a selected text run; the cached
            # visual edge identifies its actual caret side even for backwards
            # or bidirectional text. Native Edit/caret/mouse rectangles are
            # already positioned at the endpoint. Preserve an explicit point
            # so the UI never has to infer one from rectangle width.
            rect = dict(rect)
            if source in {"native-edit", "os-caret", "mouse"} or (
                source in {"uia", "app-native"}
                and (
                    str(state.get("anchor_edge") or "") == "left"
                    or (
                        not state.get("anchor_edge")
                        and str(state.get("anchor_affinity") or "") == "start"
                    )
                )
            ):
                endpoint_x = float(rect["left"])
            else:
                endpoint_x = float(rect["left"]) + float(rect["width"])
            rect["endpoint_x"] = endpoint_x
            rect["endpoint_y"] = float(rect["top"]) + float(rect["height"]) / 2.0
            # Initial resolution is useful diagnostic context. Refreshes are
            # intentionally quiet: a visible popup polls frequently and the UI
            # worker emits one aggregate anchor summary when that popup closes.
            if not refresh:
                _plog(
                    f"selection anchor source={source} refresh={refresh} "
                    f"token={token} rect={rect}"
                )
            return {
                "ok": True,
                "visible": True,
                "source": source,
                "exact": bool(exact),
                "selection_rect": rect,
            }
    if not refresh:
        _plog(f"selection anchor unavailable refresh={refresh} token={token}")
    return {"ok": False, "visible": False, "source": "unavailable", "selection_rect": {}}


def _win_capture_background_input_target(element: Any) -> dict[str, int]:
    """Record the focused native HWND while the user is already in the target app."""
    if not IS_WIN:
        return {"input_hwnd": 0, "root_hwnd": 0, "target_pid": 0}
    try:
        import ctypes
        from ctypes import wintypes

        class GuiThreadInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        user32 = ctypes.windll.user32
        root_hwnd = int(user32.GetForegroundWindow() or 0)
        thread_id = int(user32.GetWindowThreadProcessId(root_hwnd, None) or 0)
        info = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
        input_hwnd = 0
        if thread_id and user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            input_hwnd = int(info.hwndFocus or info.hwndCaret or 0)
        if not input_hwnd:
            try:
                input_hwnd = int(element.CurrentNativeWindowHandle or 0)
            except Exception:
                input_hwnd = 0
        if not input_hwnd:
            input_hwnd = root_hwnd
        if input_hwnd:
            resolved_root = int(user32.GetAncestor(input_hwnd, 2) or 0)  # GA_ROOT
            root_hwnd = resolved_root or root_hwnd
        pid = wintypes.DWORD()
        if input_hwnd:
            user32.GetWindowThreadProcessId(input_hwnd, ctypes.byref(pid))
        return {
            "input_hwnd": int(input_hwnd),
            "root_hwnd": int(root_hwnd),
            "target_pid": int(pid.value),
        }
    except Exception as exc:  # noqa: BLE001 - capture remains best-effort
        _plog(f"uia background target capture raised {type(exc).__name__}: {exc}")
        return {"input_hwnd": 0, "root_hwnd": 0, "target_pid": 0}


def _win_background_text_units(text: str) -> list[int]:
    """Return WM_CHAR UTF-16 units, normalizing newlines to the Enter character."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = normalized.encode("utf-16-le", errors="surrogatepass")
    units = [int.from_bytes(raw[index : index + 2], "little") for index in range(0, len(raw), 2)]
    return [0x000D if unit == 0x000A else unit for unit in units]


def _win_post_text_to_cached_target(token: int, text: str) -> dict[str, Any]:
    """Post text to the captured input HWND without focus, cursor, or clipboard changes."""
    if not IS_WIN:
        return {"ok": False, "method": "win-post-message", "error": "not windows"}
    state = _focus_anchor(token)
    if not state or state.get("kind") != "win-uia":
        return {"ok": False, "method": "win-post-message", "error": "stale or missing focus token"}
    input_hwnd = int(state.get("input_hwnd") or 0)
    root_hwnd = int(state.get("root_hwnd") or 0)
    expected_pid = int(state.get("target_pid") or 0)
    if not input_hwnd:
        return {"ok": False, "method": "win-post-message", "error": "no captured background input window"}
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        if not user32.IsWindow(input_hwnd):
            return {"ok": False, "method": "win-post-message", "error": "captured input window is stale"}
        actual_root = int(user32.GetAncestor(input_hwnd, 2) or 0)  # GA_ROOT
        if root_hwnd and actual_root and actual_root != root_hwnd:
            return {"ok": False, "method": "win-post-message", "error": "captured input window changed owners"}
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(input_hwnd, ctypes.byref(pid))
        if expected_pid and int(pid.value) != expected_pid:
            return {"ok": False, "method": "win-post-message", "error": "captured input process changed"}

        foreground_before = int(user32.GetForegroundWindow() or 0)
        text_range = state.get("range")
        verification_pattern = None
        expected_after = ""
        if bool(state.get("range_context_bound")):
            try:
                import comtypes.gen.UIAutomationClient as uiac  # type: ignore

                element = state.get("element")
                raw_pattern = element.GetCurrentPattern(_UIA_TEXT_PATTERN_ID)
                verification_pattern = raw_pattern.QueryInterface(uiac.IUIAutomationTextPattern)
                prefix = str(state.get("document_prefix") or "")
                selected = str(state.get("selected_text") or "")
                suffix = str(state.get("document_suffix") or "")
                current_document = str(verification_pattern.DocumentRange.GetText(-1) or "")
                current_selection = str(text_range.GetText(-1) or "")
                if current_document != f"{prefix}{selected}{suffix}" or current_selection != selected:
                    return {
                        "ok": False,
                        "method": "win-post-message",
                        "error": "the browser text or selection changed before Rewrite was accepted",
                        "stale": True,
                    }
                expected_after = f"{prefix}{text}{suffix}"
            except Exception as exc:
                return {
                    "ok": False,
                    "method": "win-post-message",
                    "error": f"exact UIA verification unavailable: {type(exc).__name__}: {exc}",
                }
        if text_range is not None:
            # Re-anchor the captured Monaco selection. UIA Select changes the
            # target's internal selection, but unlike SetFocus it does not
            # activate the target window.
            text_range.Select()
        if int(user32.GetForegroundWindow() or 0) != foreground_before:
            return {
                "ok": False,
                "method": "win-post-message",
                "error": "target attempted to take foreground during selection",
                "foreground_unchanged": False,
            }

        units = _win_background_text_units(text)
        for unit in units:
            if not user32.PostMessageW(input_hwnd, 0x0102, unit, 1):  # WM_CHAR
                return {
                    "ok": False,
                    "method": "win-post-message",
                    "error": "target rejected background text input",
                    "foreground_unchanged": int(user32.GetForegroundWindow() or 0) == foreground_before,
                }
        # PostMessage only confirms that Windows queued the messages. Chromium
        # may intentionally discard synthetic background WM_CHAR events, so
        # verify the exact text through the already-captured accessibility
        # document before reporting success.
        delivered = not text
        if text:
            try:
                text_pattern = verification_pattern
                if text_pattern is None:
                    import comtypes.gen.UIAutomationClient as uiac  # type: ignore

                    element = state.get("element")
                    raw_pattern = element.GetCurrentPattern(_UIA_TEXT_PATTERN_ID)
                    text_pattern = raw_pattern.QueryInterface(uiac.IUIAutomationTextPattern)
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    document_text = str(text_pattern.DocumentRange.GetText(-1) or "")
                    if (expected_after and document_text == expected_after) or (
                        not expected_after and text in document_text
                    ):
                        delivered = True
                        break
                    time.sleep(0.05)
            except Exception:
                delivered = False
        foreground_after = int(user32.GetForegroundWindow() or 0)
        unchanged = foreground_after == foreground_before
        return {
            "ok": bool(unchanged and delivered),
            "method": "win-post-message",
            "activated": False,
            "confirmed": True,
            "keystroke_sent": bool(units),
            "clipboard_ok": False,
            "clipboard_restored": True,
            "foreground_unchanged": unchanged,
            "focus_restored": unchanged,
            "text_verified": delivered,
            "posted_utf16_units": len(units),
            "target_pid": int(pid.value),
            "error": (
                ""
                if unchanged and delivered
                else "target ignored background text input"
                if unchanged
                else "foreground changed while posting background text"
            ),
        }
    except Exception as exc:  # noqa: BLE001 - report the target-specific failure
        return {
            "ok": False,
            "method": "win-post-message",
            "foreground_unchanged": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _ax_selected_text() -> str | None:
    """Read the focused element's selected text via Accessibility (no keystrokes).

    Returns the selection (``""`` when a text element is focused but nothing is
    selected), or ``None`` when AX can't answer -- no focused element, or the
    element doesn't expose ``AXSelectedText`` (some web/Electron views) -- so the
    caller can fall back to the clipboard copy. Reading AX avoids synthesising
    Cmd+C, whose flag changes desync a physically-held hotkey modifier and make
    the next hotkey key leak into the foreground app.
    """
    if not IS_MAC:
        return None
    try:
        import HIServices  # type: ignore  # pyobjc-framework-ApplicationServices

        system = HIServices.AXUIElementCreateSystemWide()
        err, focused = HIServices.AXUIElementCopyAttributeValue(
            system, _AX_FOCUSED_ATTR, None
        )
        if err != _AX_ERROR_SUCCESS or focused is None:
            return None
        err, value = HIServices.AXUIElementCopyAttributeValue(
            focused, _AX_SELECTED_TEXT_ATTR, None
        )
        if err != _AX_ERROR_SUCCESS or value is None:
            return None
        return str(value)
    except Exception as exc:  # noqa: BLE001 - AX is best-effort
        _plog(f"ax selected-text raised {type(exc).__name__}: {exc}")
        return None


def _ax_apply_selected_text(token: int, text: str) -> dict[str, Any]:
    """Replace the cached element's selected text in place. Best-effort."""
    if not IS_MAC:
        return {"ok": False, "error": "not macos"}
    state = _focus_anchor(token)
    if not state or state.get("kind") not in {"mac-ax", None}:
        return {"ok": False, "error": "stale or missing focus token"}
    element = state.get("element")
    if element is None:
        return {"ok": False, "error": "no cached element"}
    try:
        import HIServices  # type: ignore

        # Confirm the element is still alive before writing to it.
        err, _role = HIServices.AXUIElementCopyAttributeValue(element, _AX_ROLE_ATTR, None)
        if err != _AX_ERROR_SUCCESS:
            return {"ok": False, "error": f"element stale (err={err})"}
        set_err = HIServices.AXUIElementSetAttributeValue(element, _AX_SELECTED_TEXT_ATTR, text)
        if set_err == _AX_ERROR_SUCCESS:
            return {"ok": True}
        return {"ok": False, "error": f"set failed (err={set_err})"}
    except Exception as exc:  # noqa: BLE001 - AX is best-effort
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _win_uia_apply_selected_text(
    token: int,
    text: str,
    *,
    paste_combo: str = "",
    restore_clipboard: bool = False,
) -> dict[str, Any]:
    """Atomically replace the cached Windows selection and verify the document.

    Posting replacement text as individual ``WM_CHAR`` messages is unsafe: an
    editor may accept only part of the sequence, leaving words at opposite ends
    of the old selection before OpenWand can detect failure. One clipboard paste is
    the only generic Windows path used here, and success requires the complete
    editable document to match the expected result.
    """
    return _win_foreground_paste_to_cached_target(
        token,
        text,
        paste_combo=paste_combo,
        restore_clipboard=restore_clipboard,
    )


def _win_edit_apply_selected_text(token: int, text: str) -> dict[str, Any]:
    """Replace a freshness-bound WordPad/RichEdit selection without taking focus."""
    method = "win-richedit"
    if not IS_WIN:
        return {"ok": False, "method": method, "error": "not windows"}
    state = _focus_anchor(token)
    if not state or state.get("kind") != "win-edit":
        return {"ok": False, "method": method, "error": "stale or missing focus token"}
    input_hwnd = int(state.get("input_hwnd") or 0)
    before = _win_edit_control_snapshot(input_hwnd)
    if not before:
        return {"ok": False, "method": method, "error": "the WordPad text control is unavailable"}
    identity_fields = ("root_hwnd", "target_pid", "class_name")
    if any(before.get(field) != state.get(field) for field in identity_fields):
        return {"ok": False, "method": method, "error": "the WordPad text control changed", "stale": True}
    if before.get("document_text") != state.get("document_text"):
        return {
            "ok": False,
            "method": method,
            "error": "the WordPad text changed before Rewrite was accepted",
            "stale": True,
        }
    original_document = str(state.get("document_text") or "")
    selection_start = int(state.get("selection_start") or 0)
    selection_end = int(state.get("selection_end") or 0)
    selected = str(state.get("selected_text") or "")
    if _win_utf16_slice(original_document, selection_start, selection_end) != selected:
        return {
            "ok": False,
            "method": method,
            "error": "the captured WordPad text range is no longer valid",
            "stale": True,
        }
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if "\r\n" in original_document:
        normalized = normalized.replace("\n", "\r\n")
    expected = (
        f"{state.get('document_prefix') or ''}"
        f"{normalized}"
        f"{state.get('document_suffix') or ''}"
    )
    foreground_before = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    try:
        user32 = ctypes.windll.user32
        user32.SendMessageW(
            input_hwnd,
            _EM_SETSEL,
            selection_start,
            selection_end,
        )
        user32.SendMessageW(input_hwnd, _EM_REPLACESEL, 1, ctypes.c_wchar_p(normalized))
        after = _win_edit_control_snapshot(input_hwnd, require_selection=False)
        verified = str(after.get("document_text") or "") == expected
        foreground_after = int(user32.GetForegroundWindow() or 0)
        return {
            "ok": verified,
            "method": method,
            "activated": False,
            "confirmed": True,
            "keystroke_sent": False,
            "clipboard_ok": False,
            "clipboard_restored": True,
            "foreground_unchanged": foreground_after == foreground_before,
            "focus_restored": foreground_after == foreground_before,
            "text_verified": verified,
            "target_pid": int(before.get("target_pid") or 0),
            "error": "" if verified else "WordPad did not expose the expected rewritten document",
        }
    except Exception as exc:  # noqa: BLE001 - retain the proposal on failure
        return {"ok": False, "method": method, "error": f"{type(exc).__name__}: {exc}"}


def _win_foreground_paste_to_cached_target(
    token: int,
    text: str,
    *,
    paste_combo: str = "",
    restore_clipboard: bool = False,
) -> dict[str, Any]:
    """Temporarily focus one freshness-bound UIA range, paste, and verify it."""
    if not IS_WIN:
        return {"ok": False, "method": "win-uia-foreground-paste", "error": "not windows"}
    state = _focus_anchor(token)
    if not state or state.get("kind") != "win-uia":
        return {
            "ok": False,
            "method": "win-uia-foreground-paste",
            "error": "stale or missing focus token",
        }
    if not bool(state.get("range_context_bound")):
        return {
            "ok": False,
            "method": "win-uia-foreground-paste",
            "error": "the selected control did not expose a freshness-bound text range",
        }
    element = state.get("element")
    text_range = state.get("range")
    root_hwnd = int(state.get("root_hwnd") or 0)
    if element is None or text_range is None or not root_hwnd:
        return {
            "ok": False,
            "method": "win-uia-foreground-paste",
            "error": "the captured browser selection is no longer available",
        }
    prefix = str(state.get("document_prefix") or "")
    selected = str(state.get("selected_text") or "")
    suffix = str(state.get("document_suffix") or "")
    expected_before = f"{prefix}{selected}{suffix}"
    expected_after = f"{prefix}{text}{suffix}"
    foreground_before = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    clipboard_before = clipboard_get().get("text", "") if restore_clipboard else ""
    clipboard_changed = False
    restored = not restore_clipboard
    focus_restored = foreground_before in {0, root_hwnd}
    result: dict[str, Any] = {"ok": False, "method": "win-uia-foreground-paste"}
    try:
        import comtypes.gen.UIAutomationClient as uiac  # type: ignore

        raw_pattern = element.GetCurrentPattern(_UIA_TEXT_PATTERN_ID)
        text_pattern = raw_pattern.QueryInterface(uiac.IUIAutomationTextPattern)
        current_document = str(text_pattern.DocumentRange.GetText(-1) or "")
        current_selection = str(text_range.GetText(-1) or "")
        if current_document != expected_before or current_selection != selected:
            result.update(
                error="the browser text or selection changed before Rewrite was accepted",
                stale=True,
            )
            return result
        process_name = _win_process_name(int(state.get("target_pid") or 0)).casefold()
        vscode_target = process_name in {
            "code.exe",
            "code - insiders.exe",
            "cursor.exe",
            "windsurf.exe",
        }
        if not vscode_target:
            text_range.Select()
        element.SetFocus()
        if not _win_restore_foreground(root_hwnd):
            result["error"] = "the original browser window could not be focused"
            return result
        if int(ctypes.windll.user32.GetForegroundWindow() or 0) != root_hwnd:
            result["error"] = "the original browser window was not confirmed"
            return result
        if vscode_target and not _win_vscode_select_captured_context(
            text_pattern,
            document_prefix=prefix,
            selected_text=selected,
        ):
            result["error"] = "VS Code could not reselect the original captured text"
            return result
        if not clipboard_set(text).get("ok"):
            result.update(error="clipboard write failed", clipboard_ok=False)
            return result
        clipboard_changed = True
        from core.platform_utils import PASTE_COMBO, send_keys

        send_keys(paste_combo or PASTE_COMBO)
        verified = False
        deadline = time.monotonic() + 1.25
        while time.monotonic() < deadline:
            if str(text_pattern.DocumentRange.GetText(-1) or "") == expected_after:
                verified = True
                break
            time.sleep(0.05)
        if not verified and vscode_target:
            # Monaco can publish the successful edit before Chromium refreshes
            # TextPattern.DocumentRange. Confirm the actual editor text at A
            # through the same selection-and-copy check used above.
            verified = _win_vscode_select_captured_context(
                text_pattern,
                document_prefix=prefix,
                selected_text=text,
            )
            if verified:
                send_keys("right")
        rolled_back = False
        if not verified:
            # Chromium can redirect a stale TextRange to its current selection.
            # Never leave that unverified mutation behind: undo it while the
            # exact target editor is still foreground and prove the original
            # document came back before returning failure.
            send_keys("ctrl+z")
            rollback_deadline = time.monotonic() + 1.25
            while time.monotonic() < rollback_deadline:
                if str(text_pattern.DocumentRange.GetText(-1) or "") == expected_before:
                    rolled_back = True
                    break
                time.sleep(0.05)
        result.update(
            ok=verified,
            activated=True,
            confirmed=True,
            keystroke_sent=True,
            clipboard_ok=True,
            text_verified=verified,
            target_pid=int(state.get("target_pid") or 0),
            rolled_back=rolled_back,
            error=(
                ""
                if verified
                else "the editor changed the wrong range and rollback was verified"
                if rolled_back
                else "the editor did not expose the expected rewritten text and rollback failed"
            ),
        )
        return result
    except Exception as exc:  # noqa: BLE001 - return a safe copy-only failure
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        if clipboard_changed and restore_clipboard:
            restored = bool(clipboard_set(clipboard_before).get("ok"))
        if foreground_before and foreground_before != root_hwnd:
            focus_restored = _win_restore_foreground(foreground_before)
        result["clipboard_restored"] = restored
        result["focus_restored"] = focus_restored
        _plog(
            "uia foreground paste "
            f"token={token} clipboard_restored={restored} focus_restored={focus_restored}"
        )


def _win_restore_foreground(hwnd: int) -> bool:
    """Best-effort restore after an anchored UIA paste temporarily activates an app."""
    if not IS_WIN or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        current = int(user32.GetForegroundWindow() or 0)
        if current == int(hwnd):
            return True
        current_thread = int(kernel32.GetCurrentThreadId() or 0)
        foreground_thread = int(user32.GetWindowThreadProcessId(current, None) or 0)
        target_thread = int(user32.GetWindowThreadProcessId(int(hwnd), None) or 0)
        attached_foreground = False
        attached_target = False
        try:
            if foreground_thread and foreground_thread != current_thread:
                attached_foreground = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
            if target_thread and target_thread not in {current_thread, foreground_thread}:
                attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))
            user32.LockSetForegroundWindow(2)  # LSFW_UNLOCK
            if user32.IsIconic(int(hwnd)):
                user32.ShowWindow(int(hwnd), 9)  # SW_RESTORE
            user32.BringWindowToTop(int(hwnd))
            user32.SetForegroundWindow(int(hwnd))
        finally:
            if attached_target:
                user32.AttachThreadInput(current_thread, target_thread, False)
            if attached_foreground:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            if int(user32.GetForegroundWindow() or 0) == int(hwnd):
                return True
            time.sleep(0.03)
        # Windows sometimes refuses the first request until this process has
        # emitted an input event. A harmless Alt tap grants that foreground
        # transition without typing into either application.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 0x0002, 0)
        user32.SetForegroundWindow(int(hwnd))
        time.sleep(0.08)
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)
    except Exception:
        return False
    return False


_PASTE_CLIPBOARD_RESTORE_DELAY_SECONDS = 0.25


def paste_text(
    text: str = "",
    paste_combo: str = "",
    target_pid: int = 0,
    focus_token: int = 0,
    restore_clipboard: bool = False,
) -> dict[str, Any]:
    """Paste text."""
    if IS_MAC:
        from core.platform import macos_native

        # Preferred path: write straight into the originally-focused text element
        # via Accessibility. No app refocus, no Cmd+V — survives the user moving
        # to another window. Falls through to activate + Cmd+V if it can't.
        if focus_token:
            ax = _ax_apply_selected_text(int(focus_token), text)
            if ax.get("ok"):
                _plog(f"paste via AX in-place token={focus_token} ok")
                return {
                    "ok": True,
                    "method": "ax",
                    "activated": True,
                    "confirmed": True,
                    "keystroke_sent": False,
                    "clipboard_ok": False,  # AX write doesn't touch the clipboard
                    "target_pid": int(target_pid or 0),
                    "app_name": "",
                    "error": "",
                }
            _plog(
                f"paste AX in-place token={focus_token} failed "
                f"({ax.get('error')}); falling back to activate+Cmd+V"
            )

        original_clipboard = clipboard_get().get("text", "") if restore_clipboard else ""
        act = _activate_pid(target_pid)
        confirmed = bool(act.get("confirmed"))
        # Settle longer when we couldn't confirm focus; the activation may still
        # be in flight.
        time.sleep(0.15 if confirmed else 0.3)
        clip_ok = macos_native.set_clipboard_text(text)
        time.sleep(0.05)  # let pbcopy propagate before Cmd+V
        sent = macos_native.send_key_combo(paste_combo or "cmd+v")
        restored = False
        if restore_clipboard and clip_ok:
            time.sleep(_PASTE_CLIPBOARD_RESTORE_DELAY_SECONDS)
            restored = bool(clipboard_set(original_clipboard).get("ok"))
        _plog(
            f"paste target_pid={target_pid} confirmed={confirmed} "
            f"clipboard={clip_ok} restored={restored} keystroke={sent} app={act.get('app_name')!r}"
        )
        return {
            "ok": bool(sent and confirmed),
            "activated": confirmed,
            "confirmed": confirmed,
            "keystroke_sent": bool(sent),
            "clipboard_ok": bool(clip_ok),
            "clipboard_restored": restored,
            "target_pid": int(target_pid or 0),
            "frontmost_pid": int(act.get("frontmost_pid") or 0),
            "app_name": act.get("app_name") or "",
            "error": act.get("error") or "",
        }
    try:
        from core.platform_utils import (
            PASTE_COMBO,
            get_foreground_window,
            send_keys,
            set_foreground_window,
        )

        if focus_token:
            state = _focus_anchor(int(focus_token))
            if state.get("kind") == "win-edit":
                uia = _win_edit_apply_selected_text(int(focus_token), text)
            else:
                uia = _win_uia_apply_selected_text(
                    int(focus_token),
                    text,
                    paste_combo=paste_combo,
                    restore_clipboard=restore_clipboard,
                )
            if uia.get("ok"):
                return uia
            _plog(
                f"paste anchored range token={focus_token} failed "
                f"({uia.get('error')}); refusing unanchored paste"
            )
            return {
                **uia,
                "ok": False,
                "activated": False,
                "confirmed": False,
                "keystroke_sent": False,
            }

        activated = False
        confirmed = False
        original_clipboard = clipboard_get().get("text", "") if restore_clipboard else ""
        if target_pid:
            set_foreground_window(int(target_pid))
            time.sleep(0.15)
            foreground_window = int(get_foreground_window() or 0)
            target_window = int(target_pid)
            # Windows callers normally pass the captured HWND. Retain PID
            # compatibility for older callers, but never infer success merely
            # because SetForegroundWindow did not raise: Windows can legally
            # reject it and return zero.
            if IS_WIN:
                target_is_window = bool(_win_window_pid(target_window))
                confirmed = bool(
                    foreground_window == target_window
                    if target_is_window
                    else _win_window_pid(foreground_window) == target_window
                )
            else:
                confirmed = foreground_window == target_window
            activated = confirmed
            if not confirmed:
                _plog(
                    f"paste target_pid={target_pid} focus confirmation FAILED "
                    f"foreground={foreground_window}"
                )
                return {
                    "ok": False,
                    "activated": False,
                    "confirmed": False,
                    "keystroke_sent": False,
                    "clipboard_ok": False,
                    "clipboard_restored": False,
                    "target_pid": int(target_pid or 0),
                    "frontmost_pid": int(_win_window_pid(foreground_window) or 0) if IS_WIN else 0,
                    "error": "target window could not be confirmed",
                }
        else:
            return {
                "ok": False,
                "activated": False,
                "confirmed": False,
                "keystroke_sent": False,
                "clipboard_ok": False,
                "clipboard_restored": False,
                "error": "missing paste target",
            }
        if not clipboard_set(text).get("ok"):
            _plog(f"paste target_pid={target_pid} clipboard write FAILED")
            return {"ok": False, "activated": activated, "clipboard_ok": False, "error": "clipboard write failed"}
        send_keys(paste_combo or PASTE_COMBO)
        restored = False
        if restore_clipboard:
            time.sleep(_PASTE_CLIPBOARD_RESTORE_DELAY_SECONDS)
            restored = bool(clipboard_set(original_clipboard).get("ok"))
        _plog(f"paste target_pid={target_pid} activated={activated} restored={restored} keystroke sent")
        return {
            "ok": True,
            "activated": activated,
            "confirmed": confirmed,
            "keystroke_sent": True,
            "clipboard_ok": True,
            "clipboard_restored": restored,
        }
    except Exception as exc:  # noqa: BLE001 - report pasteback failure to caller
        _plog(f"paste target_pid={target_pid} raised {type(exc).__name__}: {exc}")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _focus_cached_edit_target(token: int) -> bool:
    """Best-effort focus of the exact text control captured for paste-back."""
    state = _focus_anchor(token)
    if not state:
        return False
    expected_kinds = {"mac-ax"} if IS_MAC else {"win-uia", "win-edit"} if IS_WIN else set()
    if expected_kinds and state.get("kind") not in expected_kinds:
        return False
    if IS_WIN and state.get("kind") == "win-edit":
        input_hwnd = int(state.get("input_hwnd") or 0)
        root_hwnd = int(state.get("root_hwnd") or 0)
        if not input_hwnd or not ctypes.windll.user32.IsWindow(input_hwnd):
            return False
        _win_restore_foreground(root_hwnd)
        ctypes.windll.user32.SetFocus(input_hwnd)
        return True
    element = state.get("element")
    if element is None:
        return False
    try:
        if IS_MAC:
            import HIServices  # type: ignore

            return bool(
                HIServices.AXUIElementSetAttributeValue(element, "AXFocused", True)
                == _AX_ERROR_SUCCESS
            )
        if IS_WIN:
            element.SetFocus()
            return True
    except Exception:
        return False
    return False


def _win_window_for_pid(pid: int) -> int:
    """Return the topmost visible Windows HWND owned by ``pid``."""
    if not IS_WIN or not pid:
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        matches: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd: int, _lparam: int) -> bool:
            if not ctypes.windll.user32.IsWindowVisible(hwnd):
                return True
            if _win_window_pid(int(hwnd)) == int(pid):
                matches.append(int(hwnd))
                return False
            return True

        callback_ref = callback_type(callback)
        ctypes.windll.user32.EnumWindows(callback_ref, 0)
        return matches[0] if matches else 0
    except Exception:
        return 0


def undo_edit(
    target_pid: int = 0,
    focus_token: int = 0,
    original_text: str = "",
    replacement_text: str = "",
) -> dict[str, Any]:
    """Undo a recent paste-back in its original app, with clipboard fallback."""
    del replacement_text  # Reserved for future target-content verification.
    try:
        if IS_MAC:
            from core.platform import macos_native

            activated = _activate_pid(int(target_pid or 0))
            if activated.get("confirmed"):
                control_focused = bool(focus_token) and _focus_cached_edit_target(int(focus_token))
                if control_focused and macos_native.send_key_combo("cmd+z"):
                    _plog(f"undo target_pid={target_pid} via cmd+z ok")
                    return {"ok": True, "method": "app-undo", "clipboard_ok": False}
        else:
            from core.platform_utils import get_foreground_window, send_keys, set_foreground_window

            target = int(target_pid or 0)
            target_window = _win_window_for_pid(target)
            if target_window:
                set_foreground_window(target_window)
                time.sleep(0.1)
            control_focused = bool(focus_token) and _focus_cached_edit_target(int(focus_token))
            foreground_window = int(get_foreground_window() or 0)
            focused = bool(
                target
                and target_window
                and _win_window_pid(foreground_window) == target
                and control_focused
            )
            if focused:
                send_keys("ctrl+z")
                _plog(f"undo target_pid={target_pid} hwnd={target_window} via ctrl+z ok")
                return {"ok": True, "method": "app-undo", "clipboard_ok": False}
    except Exception as exc:  # noqa: BLE001 - clipboard fallback remains available
        _plog(f"undo target_pid={target_pid} raised {type(exc).__name__}: {exc}")

    copied = clipboard_set(str(original_text or ""))
    clipboard_ok = bool(isinstance(copied, dict) and copied.get("ok"))
    _plog(f"undo target_pid={target_pid} app undo unavailable clipboard={clipboard_ok}")
    return {
        "ok": False,
        "method": "clipboard-fallback" if clipboard_ok else "failed",
        "clipboard_ok": clipboard_ok,
        "error": "could not safely focus the original app",
    }


def notify(title: str = "OpenWand", message: str = "") -> dict[str, Any]:
    """Post a system notification (Notification Center) so the supervisor can
    surface paste-back status without writing into the reply bubble."""
    if IS_MAC:
        try:
            import json as _json

            script = (
                f"display notification {_json.dumps(message or '')} "
                f"with title {_json.dumps(title or 'OpenWand')}"
            )
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5.0,
                check=False,
            )
            return {"ok": result.returncode == 0}
        except Exception as exc:  # noqa: BLE001 - notification is best-effort
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    # No system-toast path wired for Windows/Linux yet; callers fall back to logs.
    return {"ok": False, "error": "unsupported platform"}


def open_privacy_settings(pane: str = "Privacy") -> dict[str, Any]:
    """Open privacy settings."""
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


def native_config_reload() -> dict[str, Any]:
    """Reload .env-backed config in the native process after Settings → Apply.

    The native worker is long-lived and owns global hotkey registration. Without
    this its in-process ``config`` (HOTKEY_*, CALLER_ROWS, context limits) stays
    frozen at app-start values, so re-registering hotkeys after a settings change
    re-binds the OLD keys — a changed hotkey only takes effect after a restart.
    Mirrors audio.config.reload / brain.config.reload.
    """
    import config
    from core.action_files.store import configured_caller_rows

    config.reload()
    print("[native] config reloaded", flush=True)
    return {
        "ok": True,
        "hotkey_voice": str(getattr(config, "HOTKEY_VOICE", "") or ""),
        "caller_count": len(configured_caller_rows(config)),
    }


def _stop_current_hotkeys() -> dict[str, Any]:
    """Stop and forget the current hotkey backend."""
    global _hotkeys
    with _hotkeys_lock:
        helper = _hotkeys
        _hotkeys = None
    if helper is None:
        return _with_ok({"stopped": True}, "stopped")
    try:
        helper.stop()
        return _with_ok({"stopped": True}, "stopped")
    except Exception as exc:  # noqa: BLE001 - never keep stale live bindings referenced
        print(f"[native] hotkey stop failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return _with_ok({
            "stopped": False,
            "error": f"{type(exc).__name__}: {exc}",
        }, "stopped")


def hotkeys_start(addon_hotkeys: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Start global hotkeys in the native process.

    Carbon hotkeys need a Carbon event loop. The native worker's main thread is
    reserved for IPC, so a tiny helper process owns that loop and streams events
    back here.
    """
    global _hotkeys
    with _hotkeys_lock:
        if _hotkeys is not None:
            return _with_ok({"started": True, "backend": "existing"}, "started")
        helper = _HotkeyHelper() if IS_MAC else _DirectHotkeys()
        result = helper.start(addon_hotkeys=addon_hotkeys or [])
        if result.get("started"):
            _hotkeys = helper
            return _with_ok(result, "started")
        try:
            helper.stop()
        finally:
            _hotkeys = None
        return _with_ok(result, "started")


def hotkeys_stop() -> dict[str, Any]:
    """Handle hotkeys stop for runtime workers native host."""
    return _stop_current_hotkeys()


def hotkeys_reload(addon_hotkeys: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Reload config and replace global hotkey registrations in one native call."""
    config_result = native_config_reload()
    stop_result = _stop_current_hotkeys()
    start_result = hotkeys_start(addon_hotkeys=addon_hotkeys or [])
    result = {
        **start_result,
        "reloaded": True,
        "config": config_result,
    }
    if not stop_result.get("stopped"):
        result["stop_error"] = stop_result.get("error") or "hotkey stop failed"
        result["ok"] = False
    return result


atexit.register(hotkeys_stop)


HANDLERS = {
    "native.permissions.snapshot": permissions_snapshot,
    "native.config.reload": native_config_reload,
    "native.hotkeys.start": hotkeys_start,
    "native.hotkeys.stop": hotkeys_stop,
    "native.hotkeys.reload": hotkeys_reload,
    "native.context.snapshot": context_snapshot,
    "native.context.app_selection": context_app_selection,
    "native.selection.anchor.resolve": selection_anchor_resolve,
    "native.selection.anchor.release": selection_anchor_release,
    "native.action.calc.status": action_calc_status,
    "native.action.calc.snapshot": action_calc_snapshot,
    "native.action.calc.apply": action_calc_apply,
    "native.action.libreoffice.rewrite_snapshot": action_libreoffice_rewrite_snapshot,
    "native.action.libreoffice.rewrite_apply": action_libreoffice_rewrite_apply,
    "native.action.vscode.snapshot": action_vscode_snapshot,
    "native.action.vscode.apply": action_vscode_apply,
    "native.action.vscode.live_apply": action_vscode_live_apply,
    "native.action.browser.form_snapshot": action_browser_form_snapshot,
    "native.action.browser.form_apply": action_browser_form_apply,
    "native.action.browser.rewrite_snapshot": action_browser_rewrite_snapshot,
    "native.action.browser.rewrite_apply": action_browser_rewrite_apply,
    "native.context.await_selection": await_selection_context,
    "native.context.browser_content": context_browser_content,
    "native.capture.fullscreen": capture_fullscreen,
    "native.capture.region": capture_region,
    "native.clipboard.get": clipboard_get,
    "native.clipboard.set": clipboard_set,
    "native.paste_text": paste_text,
    "native.undo_edit": undo_edit,
    "native.notify": notify,
    "native.open_privacy_settings": open_privacy_settings,
}


def main() -> int:
    """Handle main for runtime workers native host."""
    calc_automation_prewarm()
    return run_host(role="native", handlers=HANDLERS, event_sink_setter=set_event_sink)


if __name__ == "__main__":
    raise SystemExit(main())
