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
    macOS:   posts CGEvents via Quartz with fixed virtual keycodes. We must NOT
             use pynput.keyboard.Controller here: its char→keycode translation
             calls main-thread-only HIToolbox APIs (UCKeyTranslate /
             keycode_context), and invoking that from the hotkey/worker thread
             while the global CGEventTap run loop is live trace-traps (SIGTRAP).
    Linux:   uses pynput.keyboard.Controller via Xlib — no root required.
    """
    if IS_WIN:
        import keyboard  # type: ignore
        keyboard.send(combo)
    elif IS_MAC:
        _send_keys_macos(combo)
    else:
        _send_keys_pynput(combo)


# macOS ANSI virtual keycodes (kVK_*) — fixed layout-independent codes so we
# never invoke HIToolbox keyboard-layout translation off the main thread.
_MAC_VK: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33,
    "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
    ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51,
    "backspace": 51, "escape": 53, "esc": 53, "home": 115, "end": 119,
    "pageup": 116, "pagedown": 121, "left": 123, "right": 124, "down": 125,
    "up": 126,
    **{f"f{n}": vk for n, vk in {
        1: 122, 2: 120, 3: 99, 4: 118, 5: 96, 6: 97, 7: 98, 8: 100,
        9: 101, 10: 109, 11: 103, 12: 111,
    }.items()},
}

_MAC_MOD_FLAGS: dict[str, int] = {
    "cmd": 0x100000, "win": 0x100000, "command": 0x100000,  # kCGEventFlagMaskCommand
    "ctrl": 0x40000, "control": 0x40000,                    # kCGEventFlagMaskControl
    "shift": 0x20000,                                       # kCGEventFlagMaskShift
    "alt": 0x80000, "option": 0x80000,                      # kCGEventFlagMaskAlternate
}


def _send_keys_macos(combo: str) -> None:
    import Quartz  # type: ignore

    flags = 0
    keycode: int | None = None
    for token in combo.lower().split("+"):
        token = token.strip()
        if token in _MAC_MOD_FLAGS:
            flags |= _MAC_MOD_FLAGS[token]
        elif token in _MAC_VK:
            keycode = _MAC_VK[token]
    if keycode is None:
        return

    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    down = Quartz.CGEventCreateKeyboardEvent(src, keycode, True)
    Quartz.CGEventSetFlags(down, flags)
    up = Quartz.CGEventCreateKeyboardEvent(src, keycode, False)
    Quartz.CGEventSetFlags(up, flags)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


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
