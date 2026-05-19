"""
core/hotkeys.py — Global hotkey listener.

Uses Win32 RegisterHotKey for combo hotkeys — the OS consumes the keystroke
before it reaches any foreground window, so no stray characters are typed.
Works without administrator privileges on Windows.

Push-to-talk (press + release on a single key) uses pynput.keyboard.Listener
because RegisterHotKey only fires on key-down.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import threading
from typing import Callable, Optional

import config

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------

_user32   = ctypes.WinDLL("user32",   use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

MOD_ALT      = 0x0001
MOD_CONTROL  = 0x0002
MOD_SHIFT    = 0x0004
MOD_WIN      = 0x0008
MOD_NOREPEAT = 0x4000   # don't re-fire while the key is held

WM_HOTKEY = 0x0312
WM_QUIT   = 0x0012

_MODIFIER_FLAGS: dict[str, int] = {
    "ctrl":    MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt":     MOD_ALT,
    "shift":   MOD_SHIFT,
    "win":     MOD_WIN,
    "cmd":     MOD_WIN,
}

# F1 = 0x70 … F24 = 0x87
_VK_FN: dict[str, int] = {f"f{i}": 0x6F + i for i in range(1, 25)}

# Named non-modifier, non-F-key virtual keycodes
_VK_NAMED: dict[str, int] = {
    "space":     0x20,
    "space_bar": 0x20,  # legacy alias
    "tab":       0x09,
    "enter":     0x0D,
    "return":    0x0D,
    "backspace":  0x08,
    "delete":    0x2E,
    "insert":    0x2D,
    "home":      0x24,
    "end":       0x23,
    "pageup":    0x21,
    "pagedown":  0x22,
    "left":      0x25,
    "right":     0x27,
    "up":        0x26,
    "down":      0x28,
}


def _parse_hotkey(hotkey_str: str) -> tuple[int, int]:
    """Return (modifier_flags, vk_code) from a string like 'ctrl+alt+q'."""
    mods = MOD_NOREPEAT
    vk   = 0
    for part in hotkey_str.lower().split("+"):
        part = part.strip()
        if part in _MODIFIER_FLAGS:
            mods |= _MODIFIER_FLAGS[part]
        elif part in _VK_FN:
            vk = _VK_FN[part]
        elif part in _VK_NAMED:
            vk = _VK_NAMED[part]
        elif len(part) == 1:
            res = _user32.VkKeyScanA(ctypes.c_char(part.encode()))
            vk  = res & 0xFF
        else:
            raise ValueError(f"Unrecognised hotkey token: {part!r}")
    if vk == 0:
        raise ValueError(f"No non-modifier key found in: {hotkey_str!r}")
    return mods, vk


class HotkeyListener:
    """
    Registers global hotkeys and dispatches to callbacks.

    Usage:
        listener = HotkeyListener(on_invoke=my_callback, ...)
        listener.start()
        ...
        listener.stop()
    """

    def __init__(
        self,
        on_invoke: Callable[[], None],
        on_add_context: Callable[[], None] | None = None,
        on_clear_context: Callable[[], None] | None = None,
        on_snip: Callable[[], None] | None = None,
        on_voice_start: Callable[[], None] | None = None,
        on_voice_stop: Callable[[], None] | None = None,
    ):
        self._hotkey_defs: list[tuple[str, Callable]] = [
            (config.HOTKEY_INVOKE, on_invoke),
        ]
        if on_add_context:
            self._hotkey_defs.append((config.HOTKEY_ADD_CONTEXT, on_add_context))
        if on_clear_context:
            self._hotkey_defs.append((config.HOTKEY_CLEAR_CONTEXT, on_clear_context))
        if on_snip:
            self._hotkey_defs.append((config.HOTKEY_SNIP, on_snip))

        self._on_voice_start = on_voice_start
        self._on_voice_stop  = on_voice_stop

        self._callbacks: dict[int, Callable] = {}
        self._pump_tid   = 0
        self._pump_ready = threading.Event()
        self._pump_thread: threading.Thread | None = None
        self._voice_listener = None
        self._voice_key      = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the hotkey pump thread. Safe to call from any thread."""
        self._pump_thread = threading.Thread(
            target=self._message_pump,
            daemon=True,
            name="hotkey-pump",
        )
        self._pump_thread.start()
        self._pump_ready.wait(timeout=2.0)      # wait until thread ID is captured

        if config.HOTKEY_VOICE and (self._on_voice_start or self._on_voice_stop):
            self._start_voice_listener()

    def stop(self) -> None:
        """Unregister all hotkeys and stop background threads."""
        if self._pump_tid:
            _user32.PostThreadMessageW(self._pump_tid, WM_QUIT, 0, 0)
            self._pump_tid = 0
        if self._voice_listener:
            self._voice_listener.stop()
            self._voice_listener = None

    # ------------------------------------------------------------------
    # RegisterHotKey message pump
    # ------------------------------------------------------------------

    def _message_pump(self) -> None:
        """Runs in its own daemon thread. Registers hotkeys, pumps WM_HOTKEY."""
        self._pump_tid = _kernel32.GetCurrentThreadId()
        self._pump_ready.set()

        registered: list[int] = []
        for i, (hotkey_str, cb) in enumerate(self._hotkey_defs):
            hk_id = i + 1
            try:
                mods, vk = _parse_hotkey(hotkey_str)
                if _user32.RegisterHotKey(None, hk_id, mods, vk):
                    self._callbacks[hk_id] = cb
                    registered.append(hk_id)
                else:
                    err = ctypes.get_last_error()
                    print(f"[hotkeys] RegisterHotKey({hotkey_str!r}) failed (error {err})")
            except ValueError as exc:
                print(f"[hotkeys] Cannot parse {hotkey_str!r}: {exc}")

        msg = wintypes.MSG()
        while True:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:   # WM_QUIT or error
                break
            if msg.message == WM_HOTKEY:
                cb = self._callbacks.get(msg.wParam)
                if cb:
                    threading.Thread(target=cb, daemon=True).start()

        for hk_id in registered:
            _user32.UnregisterHotKey(None, hk_id)

    # ------------------------------------------------------------------
    # Push-to-talk voice listener (press + release)
    # ------------------------------------------------------------------

    def _start_voice_listener(self) -> None:
        from pynput import keyboard as _kb

        s   = config.HOTKEY_VOICE.lower().strip()
        key = None
        try:
            key = _kb.Key[s]
        except KeyError:
            if len(s) == 1:
                key = _kb.KeyCode.from_char(s)

        if key is None:
            print(f"[hotkeys] Voice hotkey {s!r} not recognised for push-to-talk.")
            return

        self._voice_key      = key
        self._voice_listener = _kb.Listener(
            on_press=self._voice_press,
            on_release=self._voice_release,
        )
        self._voice_listener.daemon = True
        self._voice_listener.start()

    def _voice_press(self, key) -> None:
        if key == self._voice_key and self._on_voice_start:
            self._on_voice_start()

    def _voice_release(self, key) -> None:
        if key == self._voice_key and self._on_voice_stop:
            self._on_voice_stop()
