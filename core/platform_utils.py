"""
core/platform_utils.py — Thin cross-platform helpers for keyboard injection
and window-focus management.

Windows: uses ctypes Win32 APIs.
Linux:   uses pynput.keyboard.Controller (X11) for key injection and
         python-xlib + ewmh for window management (both pip-installable;
         no system packages required).
macOS:   uses pynput.keyboard.Controller for key injection and
         Quartz + AppKit (pyobjc) for window enumeration/focus. Requires the
         user to grant Accessibility (key injection / focus) and Screen
         Recording (window titles) permissions in System Settings.
"""
from __future__ import annotations

import sys

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Clipboard shortcuts differ by platform: macOS uses Cmd, everything else Ctrl.
# Used by text capture (synthesised copy) and paste-back.
COPY_COMBO  = "cmd+c" if IS_MAC else "ctrl+c"
PASTE_COMBO = "cmd+v" if IS_MAC else "ctrl+v"


# ---------------------------------------------------------------------------
# Key injection
# ---------------------------------------------------------------------------

def send_keys(combo: str) -> None:
    """
    Inject a key combo (e.g. "ctrl+c", "ctrl+v") into the focused window.

    Windows: delegates to the *keyboard* library (pywin32-based injection).
    Linux:   uses pynput.keyboard.Controller via Xlib — no root required.
    """
    if IS_WIN:
        import keyboard  # type: ignore
        keyboard.send(combo)
    else:
        _send_keys_pynput(combo)


def _send_keys_pynput(combo: str) -> None:
    from pynput.keyboard import Controller, Key, KeyCode  # type: ignore

    _KEY_MAP = {
        "ctrl":      Key.ctrl,
        "control":   Key.ctrl,
        "alt":       Key.alt,
        "shift":     Key.shift,
        "win":       Key.cmd,
        "cmd":       Key.cmd,
        "enter":     Key.enter,
        "return":    Key.enter,
        "tab":       Key.tab,
        "space":     Key.space,
        "backspace": Key.backspace,
        "delete":    Key.delete,
        "esc":       Key.esc,
        "escape":    Key.esc,
        "up":        Key.up,
        "down":      Key.down,
        "left":      Key.left,
        "right":     Key.right,
        "home":      Key.home,
        "end":       Key.end,
        "pageup":    Key.page_up,
        "pagedown":  Key.page_down,
        **{f"f{i}": getattr(Key, f"f{i}") for i in range(1, 13)},
    }

    ctrl = Controller()
    keys = []
    for token in combo.lower().split("+"):
        token = token.strip()
        if token in _KEY_MAP:
            keys.append(_KEY_MAP[token])
        elif len(token) == 1:
            keys.append(KeyCode.from_char(token))

    for k in keys:
        ctrl.press(k)
    for k in reversed(keys):
        ctrl.release(k)


# ---------------------------------------------------------------------------
# Active window / window ID
# ---------------------------------------------------------------------------

def get_foreground_window() -> int:
    """
    Return an opaque integer identifying the current foreground window.
    Windows: Win32 HWND.
    Linux:   X11 window ID via python-xlib/ewmh (0 if unavailable).
    macOS:   CoreGraphics window number of the frontmost app (0 if unavailable).
    """
    if IS_WIN:
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow()
    elif IS_MAC:
        return _mac_active_window()
    return _xlib_active_window()


def set_foreground_window(wid: int) -> None:
    """
    Bring *wid* to the foreground.
    Windows: SetForegroundWindow.
    Linux:   ewmh setActiveWindow + display flush (no-op if unavailable).
    macOS:   activates the owning application via NSRunningApplication.
    """
    if not wid:
        return
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(wid)
    elif IS_MAC:
        _mac_focus_window(wid)
    else:
        _xlib_focus_window(wid)


# ---------------------------------------------------------------------------
# X11 window helpers (Linux only) — backed by python-xlib + ewmh
# ---------------------------------------------------------------------------

_ewmh_instance = None


def _get_ewmh():
    global _ewmh_instance
    if _ewmh_instance is None:
        from ewmh import EWMH  # type: ignore
        _ewmh_instance = EWMH()
    return _ewmh_instance


def _xlib_active_window() -> int:
    try:
        ew = _get_ewmh()
        win = ew.getActiveWindow()
        return win.id if win is not None else 0
    except Exception:
        return 0


def _xlib_focus_window(wid: int) -> None:
    try:
        ew = _get_ewmh()
        win = ew.display.create_resource_object("window", wid)
        ew.setActiveWindow(win)
        ew.display.flush()
    except Exception:
        pass


def get_window_title(wid: int) -> str:
    """Return the title of window *wid*, or "" if unavailable."""
    if IS_MAC:
        info = _mac_window_info(wid)
        return info.get("title", "") if info else ""
    try:
        ew = _get_ewmh()
        win = ew.display.create_resource_object("window", wid)
        title = ew.getWmName(win) or ""
        # ewmh may return bytes on some distros (legacy WM_NAME atom)
        return title.decode("utf-8", errors="replace") if isinstance(title, bytes) else title
    except Exception:
        return ""


def get_window_pid(wid: int) -> int:
    """Return the PID of window *wid*, or 0 if unavailable."""
    if IS_MAC:
        info = _mac_window_info(wid)
        return info.get("pid", 0) if info else 0
    try:
        ew = _get_ewmh()
        win = ew.display.create_resource_object("window", wid)
        return ew.getWmPid(win) or 0
    except Exception:
        return 0


def list_visible_windows() -> list[int]:
    """Return window IDs for all on-screen client windows."""
    if IS_MAC:
        return _mac_list_windows()
    try:
        ew = _get_ewmh()
        return [win.id for win in (ew.getClientList() or [])]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# macOS window helpers — backed by Quartz (CoreGraphics) + AppKit (pyobjc)
# ---------------------------------------------------------------------------

def _mac_on_screen_windows() -> list:
    """Return the list of on-screen window dicts from CoreGraphics, or []."""
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )
        opts = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
        return list(CGWindowListCopyWindowInfo(opts, kCGNullWindowID) or [])
    except Exception:
        return []


def _mac_active_window() -> int:
    """Return the CGWindowNumber of the frontmost app's primary window (0 if none)."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return 0
        front_pid = int(app.processIdentifier())
        # Layer-0 windows are normal app windows; pick the first one owned by the app.
        for w in _mac_on_screen_windows():
            if int(w.get("kCGWindowOwnerPID", -1)) == front_pid and int(w.get("kCGWindowLayer", 1)) == 0:
                return int(w.get("kCGWindowNumber", 0))
    except Exception:
        pass
    return 0


def _mac_window_info(wid: int) -> dict | None:
    """Resolve a CGWindowNumber to {'title', 'pid', 'owner'}; None if not found."""
    if not wid:
        return None
    try:
        for w in _mac_on_screen_windows():
            if int(w.get("kCGWindowNumber", -1)) == int(wid):
                # kCGWindowName needs Screen Recording permission; fall back to owner name.
                title = w.get("kCGWindowName") or w.get("kCGWindowOwnerName") or ""
                return {
                    "title": str(title),
                    "pid": int(w.get("kCGWindowOwnerPID", 0)),
                    "owner": str(w.get("kCGWindowOwnerName") or ""),
                }
    except Exception:
        pass
    return None


def _mac_focus_window(wid: int) -> None:
    """Bring the application owning window *wid* to the foreground."""
    try:
        info = _mac_window_info(wid)
        if not info or not info.get("pid"):
            return
        from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(info["pid"])
        if app is not None:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except Exception:
        pass


def _mac_list_windows() -> list[int]:
    """Return CGWindowNumbers for normal (layer-0) on-screen windows."""
    try:
        return [
            int(w.get("kCGWindowNumber", 0))
            for w in _mac_on_screen_windows()
            if int(w.get("kCGWindowLayer", 1)) == 0
        ]
    except Exception:
        return []
