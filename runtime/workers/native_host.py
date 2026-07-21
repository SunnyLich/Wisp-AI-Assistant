"""wisp-native worker: platform permissions, hotkeys, context, capture, clipboard."""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
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
        if "WISP_DATA_ROOT" not in env and "WISP_REPO_ROOT" not in env:
            env["WISP_DATA_ROOT"] = str(data_root())
        if addon_hotkeys:
            env["WISP_ADDON_HOTKEYS"] = json.dumps(addon_hotkeys)
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

            caller_count = len(getattr(config, "CALLER_ROWS", []))
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


def _win_is_wisp_ui_window(hwnd: int) -> bool:
    """Handle win is wisp ui window for runtime workers native host."""
    pid = _win_window_pid(hwnd)
    title = _win_window_title(hwnd).strip().lower()
    proc = _win_process_name(pid).strip().lower()
    if pid == os.getpid():
        return True
    if proc == "wisp.exe":
        return True
    if proc in {"python.exe", "pythonw.exe"} and title in {"wisp", "wisp settings", "wisp memory"}:
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
        if _win_is_wisp_ui_window(hwnd):
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
    """Return True when an X11 window's pid belongs to Wisp's own process tree."""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    own = {int(os.getpid())}
    try:
        supervisor_pid = int(os.environ.get("WISP_SUPERVISOR_PID") or 0)
    except ValueError:
        supervisor_pid = 0
    if supervisor_pid > 0:
        own.add(supervisor_pid)
    if pid in own:
        return True
    try:
        import psutil

        proc = psutil.Process(pid)
        if str(proc.name() or "").strip().lower() == "wisp":
            return True
        ancestors = {int(parent.pid) for parent in proc.parents()}
    except Exception:
        return False
    return bool(own & ancestors)


def _linux_context_window_id() -> int:
    """Return the X11 window to read context from, skipping Wisp's own windows.

    The icon overlay can hold X11 activation while the user works elsewhere,
    so the raw _NET_ACTIVE_WINDOW may be Wisp itself. Mirror the Windows
    correction: fall back to the topmost non-Wisp window in stacking order.
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


def _selected_text_and_stale(
    *,
    allow_clipboard_fallback: bool = True,
    active_pid: int | None = None,
    require_active_owner: bool = False,
    selection_dedupe_key: str = "",
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

            return get_selected_text() or "", ""
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
        "screen_size": _screen_size(),
        "focus_token": 0,
        "captured_at": time.time(),
        "debug": {
            "runtime": _runtime_debug(),
            "window": dict(_last_context_window_debug),
        },
    }
    # Grab the focused text element first (before selection/clipboard work), while
    # the user's field is still focused, so a later rewrite can be written back
    # in place via Accessibility even if focus has since moved.
    if capture_focus:
        snapshot["focus_token"] = _capture_focus()
    sel_dt = path_dt = clip_dt = window_text_dt = br_dt = 0.0
    selection_kind = _selection_source_kind(active) if include_selected_paths else "text"
    if include_selection and selection_kind != "paths":
        _s = time.monotonic()
        snapshot["selected_text"], snapshot["stale_selected_text"] = _selected_text_and_stale(
            allow_clipboard_fallback=True,
            active_pid=int(active.get("pid") or 0),
            require_active_owner=bool(require_active_selection_owner),
            selection_dedupe_key=str(selection_dedupe_key or ""),
        )
        sel_dt = time.monotonic() - _s
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
    if include_browser_content:
        _s = time.monotonic()
        try:
            from core.context_fetcher import WindowInfo, _browser_content, get_browser_window_for_context

            if not IS_WIN and not IS_MAC and os.environ.get("WAYLAND_DISPLAY"):
                browser_window = WindowInfo(
                    title=str(active.get("name") or ""),
                    process_name=str(active.get("process_name") or ""),
                    pid=int(active.get("pid") or 0),
                    url=str(active.get("browser_url") or ""),
                )
            else:
                active_hwnd = int(active.get("window_id") or 0) if IS_WIN else 0
                browser_window = get_browser_window_for_context(active_hwnd)
            snapshot["browser_url"] = getattr(browser_window, "url", "") or ""
            snapshot["browser_hwnd"] = int(getattr(browser_window, "hwnd", 0) or 0)
            snapshot["browser_content"] = _browser_content(browser_window) if browser_window.hwnd or browser_window.url else ""
            snapshot["debug"]["browser_window"] = {
                "title": getattr(browser_window, "title", ""),
                "process_name": getattr(browser_window, "process_name", ""),
                "pid": getattr(browser_window, "pid", 0),
                "hwnd": getattr(browser_window, "hwnd", 0),
                "url": getattr(browser_window, "url", ""),
            }
        except Exception as exc:  # noqa: BLE001 - browser context should not block answering
            snapshot["browser_error"] = f"{type(exc).__name__}: {exc}"
        br_dt = time.monotonic() - _s
    elif include_browser_url:
        # Cheap URL grab while the browser is still foreground (hotkey time). Each
        # OS captures what it can defer cheaply; the page text is read later.
        _s = time.monotonic()
        try:
            if IS_WIN:
                # Windows: grab the URL + window handle now; the page text is
                # fetched later by handle (UIA needs no focus, so the picker
                # stealing focus does not matter).
                from core.context_fetcher import get_browser_window_for_context

                active_hwnd = int(active.get("window_id") or 0)
                win = get_browser_window_for_context(active_hwnd)
                snapshot["debug"]["browser_window"] = {
                    "title": getattr(win, "title", ""),
                    "process_name": getattr(win, "process_name", ""),
                    "pid": getattr(win, "pid", 0),
                    "hwnd": getattr(win, "hwnd", 0),
                    "url": getattr(win, "url", ""),
                }
                if win.url:
                    snapshot["browser_url"] = win.url
                if win.hwnd:
                    snapshot["browser_hwnd"] = int(win.hwnd or 0)
            elif IS_MAC:
                # macOS: Browser/Web is independent from the active app/document.
                # Ask visible browser apps for their own front tab so a document
                # foreground can still provide browser context.
                from core.context_fetcher import get_browser_window_for_context

                win = get_browser_window_for_context(0)
                snapshot["browser_app"] = getattr(win, "process_name", "") or ""
                snapshot["browser_url"] = getattr(win, "url", "") or ""
                snapshot["debug"]["browser_window"] = {
                    "title": getattr(win, "title", ""),
                    "process_name": getattr(win, "process_name", ""),
                    "pid": getattr(win, "pid", 0),
                    "hwnd": getattr(win, "hwnd", 0),
                    "url": getattr(win, "url", ""),
                }
            else:
                if os.environ.get("WAYLAND_DISPLAY"):
                    snapshot["browser_url"] = str(active.get("browser_url") or "")
                    snapshot["browser_app"] = str(active.get("process_name") or "")
                else:
                    # Linux/X11: keep the hotkey-time browser window id.
                    from core.context_fetcher import _BROWSER_PROCS, get_browser_window_for_context

                    active_hwnd = int(active.get("window_id") or 0)
                    win = get_browser_window_for_context(active_hwnd)
                    snapshot["debug"]["browser_window"] = {
                        "title": getattr(win, "title", ""),
                        "process_name": getattr(win, "process_name", ""),
                        "pid": getattr(win, "pid", 0),
                        "hwnd": getattr(win, "hwnd", 0),
                        "url": getattr(win, "url", ""),
                    }
                    if getattr(win, "url", ""):
                        snapshot["browser_url"] = getattr(win, "url", "")
                    active_process = str(active.get("process_name") or "").strip().lower()
                    browser_hwnd = int(getattr(win, "hwnd", 0) or 0)
                    if not browser_hwnd and active_hwnd and active_process in _BROWSER_PROCS:
                        browser_hwnd = active_hwnd
                    if browser_hwnd:
                        snapshot["browser_hwnd"] = browser_hwnd
        except Exception as exc:  # noqa: BLE001 - browser context should not block the picker
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

        path = str(Path(tempfile.gettempdir()) / f"wisp-capture-{int(time.time() * 1000)}.png")
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

        path = str(Path(tempfile.gettempdir()) / f"wisp-region-{int(time.time() * 1000)}.png")
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

_focus_seq = 0
_focus_cache: dict[str, Any] = {}  # {"token": int, "kind": str, ...native objects}


def _capture_focus() -> int:
    """Cache the hotkey-time text target for later paste-back."""
    if IS_MAC:
        return _ax_capture_focus()
    if IS_WIN:
        return _win_uia_capture_focus()
    return 0


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
        _plog(f"ax capture token={_focus_seq} ok")
        return _focus_seq
    except Exception as exc:  # noqa: BLE001 - AX is best-effort
        _plog(f"ax capture raised {type(exc).__name__}: {exc}")
        return 0


def _win_uia_capture_focus() -> int:
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
        element = uia.GetFocusedElement()
        raw_pattern = element.GetCurrentPattern(_UIA_TEXT_PATTERN_ID)
        text_pattern = raw_pattern.QueryInterface(uiac.IUIAutomationTextPattern)
        selections = text_pattern.GetSelection()
        if selections.Length <= 0:
            _plog("uia capture: focused element has no text selection")
            return 0
        text_range = selections.GetElement(0)
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
        try:
            if text_range.CompareEndpoints(start_endpoint, text_range, end_endpoint) == 0:
                _plog("uia capture: text selection is collapsed")
                return 0
        except Exception:
            pass
        _focus_seq += 1
        _focus_cache.clear()
        _focus_cache["token"] = _focus_seq
        _focus_cache["kind"] = "win-uia"
        _focus_cache["element"] = element
        _focus_cache["range"] = text_range
        _plog(f"uia capture token={_focus_seq} ok")
        return _focus_seq
    except Exception as exc:  # noqa: BLE001 - UIA is best-effort
        _plog(f"uia capture raised {type(exc).__name__}: {exc}")
        return 0


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
    if not token or _focus_cache.get("token") != token or _focus_cache.get("kind") not in {"mac-ax", None}:
        return {"ok": False, "error": "stale or missing focus token"}
    element = _focus_cache.get("element")
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
    """Replace the cached Windows UIA selection range, avoiding current focus."""
    if not IS_WIN:
        return {"ok": False, "error": "not windows"}
    if not token or _focus_cache.get("token") != token or _focus_cache.get("kind") != "win-uia":
        return {"ok": False, "error": "stale or missing focus token"}
    element = _focus_cache.get("element")
    text_range = _focus_cache.get("range")
    if element is None or text_range is None:
        return {"ok": False, "error": "no cached selection range"}
    original_clipboard = clipboard_get().get("text", "") if restore_clipboard else ""
    clip_ok = False
    restored = False
    try:
        from core.platform_utils import PASTE_COMBO, send_keys

        try:
            element.SetFocus()
        except Exception:
            pass
        text_range.Select()
        time.sleep(0.05)
        clip_ok = bool(clipboard_set(text).get("ok"))
        if not clip_ok:
            return {"ok": False, "method": "uia-range", "clipboard_ok": False, "error": "clipboard write failed"}
        send_keys(paste_combo or PASTE_COMBO)
        if restore_clipboard:
            time.sleep(_PASTE_CLIPBOARD_RESTORE_DELAY_SECONDS)
            restored = bool(clipboard_set(original_clipboard).get("ok"))
        _plog(f"paste via UIA range token={token} restored={restored} ok")
        return {
            "ok": True,
            "method": "uia-range",
            "activated": True,
            "confirmed": True,
            "keystroke_sent": True,
            "clipboard_ok": True,
            "clipboard_restored": restored,
            "target_pid": 0,
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001 - UIA is best-effort
        if restore_clipboard and clip_ok:
            try:
                restored = bool(clipboard_set(original_clipboard).get("ok"))
            except Exception:
                restored = False
        return {
            "ok": False,
            "method": "uia-range",
            "clipboard_ok": bool(clip_ok),
            "clipboard_restored": restored,
            "error": f"{type(exc).__name__}: {exc}",
        }


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
        from core.platform_utils import PASTE_COMBO, send_keys, set_foreground_window

        if focus_token:
            uia = _win_uia_apply_selected_text(
                int(focus_token),
                text,
                paste_combo=paste_combo,
                restore_clipboard=restore_clipboard,
            )
            if uia.get("ok"):
                return uia
            _plog(
                f"paste UIA range token={focus_token} failed "
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
        original_clipboard = clipboard_get().get("text", "") if restore_clipboard else ""
        if target_pid:
            set_foreground_window(int(target_pid))
            activated = True
            time.sleep(0.15)
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
            "confirmed": activated,
            "keystroke_sent": True,
            "clipboard_ok": True,
            "clipboard_restored": restored,
        }
    except Exception as exc:  # noqa: BLE001 - report pasteback failure to caller
        _plog(f"paste target_pid={target_pid} raised {type(exc).__name__}: {exc}")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def notify(title: str = "Wisp", message: str = "") -> dict[str, Any]:
    """Post a system notification (Notification Center) so the supervisor can
    surface paste-back status without writing into the reply bubble."""
    if IS_MAC:
        try:
            import json as _json

            script = (
                f"display notification {_json.dumps(message or '')} "
                f"with title {_json.dumps(title or 'Wisp')}"
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

    config.reload()
    print("[native] config reloaded", flush=True)
    return {
        "ok": True,
        "hotkey_voice": str(getattr(config, "HOTKEY_VOICE", "") or ""),
        "caller_count": len(getattr(config, "CALLER_ROWS", []) or []),
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
    "native.context.await_selection": await_selection_context,
    "native.context.browser_content": context_browser_content,
    "native.capture.fullscreen": capture_fullscreen,
    "native.capture.region": capture_region,
    "native.clipboard.get": clipboard_get,
    "native.clipboard.set": clipboard_set,
    "native.paste_text": paste_text,
    "native.notify": notify,
    "native.open_privacy_settings": open_privacy_settings,
}


def main() -> int:
    """Handle main for runtime workers native host."""
    return run_host(role="native", handlers=HANDLERS, event_sink_setter=set_event_sink)


if __name__ == "__main__":
    raise SystemExit(main())
