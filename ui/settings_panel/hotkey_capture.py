"""Hotkey capture widget used by the settings dialog."""
from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLineEdit

from ui.i18n import t

_QT_KEY_NAMES: dict[int, str] = {
    Qt.Key.Key_Space.value: "space",
    Qt.Key.Key_Tab.value: "tab",
    Qt.Key.Key_Return.value: "enter",
    Qt.Key.Key_Enter.value: "enter",
    Qt.Key.Key_Escape.value: "escape",
    Qt.Key.Key_Backspace.value: "backspace",
    Qt.Key.Key_Delete.value: "delete",
    Qt.Key.Key_Insert.value: "insert",
    Qt.Key.Key_Home.value: "home",
    Qt.Key.Key_End.value: "end",
    Qt.Key.Key_PageUp.value: "pageup",
    Qt.Key.Key_PageDown.value: "pagedown",
    Qt.Key.Key_Left.value: "left",
    Qt.Key.Key_Right.value: "right",
    Qt.Key.Key_Up.value: "up",
    Qt.Key.Key_Down.value: "down",
    **{Qt.Key[f"Key_F{i}"].value: f"f{i}" for i in range(1, 25)},
}

_MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")
_MODIFIER_NAMES = {
    Qt.Key.Key_Control.value: "ctrl",
    Qt.Key.Key_Alt.value: "alt",
    Qt.Key.Key_Shift.value: "shift",
    Qt.Key.Key_Meta.value: "win",
    Qt.Key.Key_AltGr.value: "alt",
}
for _qt_name in ("Key_Super_L", "Key_Super_R", "Key_Hyper_L", "Key_Hyper_R"):
    _qt_key = getattr(Qt.Key, _qt_name, None)
    if _qt_key is not None:
        _MODIFIER_NAMES[int(_qt_key.value)] = "win"
_COMMIT_DEBOUNCE_MS = 80
_HOTKEY_MODIFIER_TOKENS = {"ctrl", "control", "alt", "shift", "win", "cmd", "command", "option"}
_HOTKEY_ACTION_MODIFIER_TOKENS = {"ctrl", "control", "alt", "win", "cmd", "command", "option"}


def _hotkey_parts(hotkey_str: str) -> list[str]:
    """Return normalized hotkey tokens from a combo string."""
    return [part.strip().lower() for part in (hotkey_str or "").split("+") if part.strip()]


def _is_f_key(token: str) -> bool:
    """Return whether token names an F-key accepted for global shortcuts."""
    if not (token.startswith("f") and token[1:].isdigit()):
        return False
    try:
        return 1 <= int(token[1:]) <= 24
    except ValueError:
        return False


def _is_safe_global_hotkey(hotkey_str: str) -> bool:
    """Return True when a combo avoids stealing ordinary unmodified typing keys."""
    parts = _hotkey_parts(hotkey_str)
    if not parts:
        return False
    has_action_modifier = any(part in _HOTKEY_ACTION_MODIFIER_TOKENS for part in parts)
    key_parts = [part for part in parts if part not in _HOTKEY_MODIFIER_TOKENS]
    if not key_parts:
        return False
    return has_action_modifier or any(_is_f_key(part) for part in key_parts)


def _qt_key_name(key: int) -> str | None:
    """Return the hotkey token for a Qt key code."""
    key_name = _QT_KEY_NAMES.get(key)
    if key_name is not None:
        return key_name
    ch = chr(key).lower() if 0x20 < key <= 0x7E else ""
    return ch if ch else None


def _event_key_int(event) -> int | None:
    """Return a stable integer key code from a Qt key event."""
    try:
        return int(event.key())
    except Exception:
        key = getattr(event, "key", lambda: None)()
        value = getattr(key, "value", None)
        try:
            return int(value)
        except Exception:
            return None


def _tokens_from_qt_event(event) -> tuple[list[str], str | None]:
    """Return modifier tokens and the action token from a Qt key event."""
    mods = event.modifiers()
    modifiers: list[str] = []
    if mods & Qt.KeyboardModifier.ControlModifier:
        modifiers.append("ctrl")
    if mods & Qt.KeyboardModifier.AltModifier:
        modifiers.append("alt")
    if mods & Qt.KeyboardModifier.ShiftModifier:
        modifiers.append("shift")
    if mods & Qt.KeyboardModifier.MetaModifier:
        modifiers.append("win")

    key = _event_key_int(event)
    if key is None:
        return modifiers, None
    modifier_name = _MODIFIER_NAMES.get(key)
    if modifier_name and modifier_name not in modifiers:
        modifiers.append(modifier_name)
    action = None if modifier_name else _qt_key_name(key)
    return modifiers, action


def _win_vk_name(vk_code: int) -> str | None:
    """Return the hotkey token for a Windows virtual-key code."""
    if 0x41 <= vk_code <= 0x5A:
        return chr(vk_code).lower()
    if 0x30 <= vk_code <= 0x39:
        return chr(vk_code)
    if 0x70 <= vk_code <= 0x87:
        return f"f{vk_code - 0x6F}"
    return {
        0x08: "backspace",
        0x09: "tab",
        0x0D: "enter",
        0x10: "shift",
        0x11: "ctrl",
        0x12: "alt",
        0x1B: "escape",
        0x20: "space",
        0x21: "pageup",
        0x22: "pagedown",
        0x23: "end",
        0x24: "home",
        0x25: "left",
        0x26: "up",
        0x27: "right",
        0x28: "down",
        0x2D: "insert",
        0x2E: "delete",
        0x5B: "win",
        0x5C: "win",
        0xA0: "shift",
        0xA1: "shift",
        0xA2: "ctrl",
        0xA3: "ctrl",
        0xA4: "alt",
        0xA5: "alt",
    }.get(vk_code)


class _WindowsKeyCaptureHook:
    """Temporary low-level keyboard hook used only while recording a keybind."""

    def __init__(self, on_key: Callable[[str, bool], None]):
        self._on_key = on_key
        self._hook = None
        self._callback_ref = None
        self._user32 = None

    def start(self) -> bool:
        """Install the hook and return whether it was installed."""
        if sys.platform != "win32" or self._hook is not None:
            return False
        try:
            import ctypes.wintypes as wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)

            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("vkCode", wintypes.DWORD),
                    ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p),
                ]

            low_level_proc = ctypes.WINFUNCTYPE(
                getattr(wintypes, "LRESULT", wintypes.LPARAM),
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            hook_handle = getattr(wintypes, "HHOOK", wintypes.HANDLE)

            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int,
                low_level_proc,
                wintypes.HINSTANCE,
                wintypes.DWORD,
            ]
            user32.SetWindowsHookExW.restype = hook_handle
            user32.CallNextHookEx.argtypes = [
                hook_handle,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.CallNextHookEx.restype = getattr(wintypes, "LRESULT", wintypes.LPARAM)
            user32.UnhookWindowsHookEx.argtypes = [hook_handle]
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL

            def _proc(n_code, w_param, l_param):
                if n_code == 0 and w_param in (0x0100, 0x0101, 0x0104, 0x0105):
                    data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    token = _win_vk_name(int(data.vkCode))
                    if token:
                        self._on_key(token, w_param in (0x0100, 0x0104))
                        return 1
                return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

            self._user32 = user32
            self._callback_ref = low_level_proc(_proc)
            self._hook = user32.SetWindowsHookExW(13, self._callback_ref, None, 0)
            if not self._hook:
                self._callback_ref = None
                self._user32 = None
                return False
            return True
        except Exception:
            self.stop()
            return False

    def stop(self) -> None:
        """Remove the hook if it is installed."""
        if self._hook is not None and self._user32 is not None:
            try:
                self._user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
        self._hook = None
        self._callback_ref = None
        self._user32 = None


class HotkeyCaptureEdit(QLineEdit):
    """Read-only field that captures a hotkey combo by interaction."""

    _IDLE_STYLE = ""
    _RECORD_STYLE = "background: #1e1e3a; color: #a0a0ff; border: 1px solid #6060cc;"

    def __init__(self, parent=None):
        """Initialize the hotkey capture edit instance."""
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText(t("Click to set..."))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._recording = False
        self._prev_text = ""
        self._seen_modifiers: set[str] = set()
        self._down_tokens: set[str] = set()
        self._action_token: str | None = None
        self._pending_combo = ""
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._commit_pending)
        self._windows_hook = _WindowsKeyCaptureHook(self._handle_captured_token)

    def mousePressEvent(self, event):  # noqa: N802
        """Handle mouse press event for hotkey capture edit."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_recording()
        super().mousePressEvent(event)

    def _start_recording(self) -> None:
        """Start recording."""
        self._recording = True
        self._prev_text = self.text()
        self._seen_modifiers.clear()
        self._down_tokens.clear()
        self._action_token = None
        self._pending_combo = ""
        self._commit_timer.stop()
        self.setText(t("Press a key combo..."))
        self.setStyleSheet(self._RECORD_STYLE)
        self.setFocus()
        self._windows_hook.start()

    def _commit(self, combo: str) -> None:
        """Handle commit for hotkey capture edit."""
        self._recording = False
        self._commit_timer.stop()
        self._windows_hook.stop()
        self.setStyleSheet(self._IDLE_STYLE)
        self.setText(combo)

    def _cancel(self) -> None:
        """Cancel the hotkey capture edit workflow."""
        self._recording = False
        self._commit_timer.stop()
        self._windows_hook.stop()
        self.setStyleSheet(self._IDLE_STYLE)
        self.setText(self._prev_text)

    def _combo_from_capture(self) -> str:
        """Build a normalized combo from all keys seen during this gesture."""
        if not self._action_token:
            return ""
        parts = [name for name in _MODIFIER_ORDER if name in self._seen_modifiers]
        parts.append(self._action_token)
        return "+".join(parts)

    def _schedule_commit_if_ready(self) -> None:
        """Schedule a commit once the key burst has settled."""
        combo = self._combo_from_capture()
        if not combo:
            return
        if not _is_safe_global_hotkey(combo):
            self._pending_combo = ""
            self._commit_timer.stop()
            self.setText(t("Add modifier or use F-key"))
            return
        self._pending_combo = combo
        self.setText(combo)
        if not self._down_tokens:
            self._commit_timer.start(_COMMIT_DEBOUNCE_MS)

    def _commit_pending(self) -> None:
        """Commit the current candidate combo."""
        if self._recording and self._pending_combo and not self._down_tokens:
            self._commit(self._pending_combo)

    def _handle_captured_token(self, token: str, is_down: bool) -> None:
        """Record one key transition from Qt or the Windows capture hook."""
        if not self._recording:
            return
        if token == "escape" and is_down:
            self._commit("")
            return
        if token in _MODIFIER_ORDER:
            if is_down:
                self._seen_modifiers.add(token)
                self._down_tokens.add(token)
            else:
                self._down_tokens.discard(token)
            self._schedule_commit_if_ready()
            return
        if is_down:
            self._action_token = token
            self._down_tokens.add(token)
        else:
            self._down_tokens.discard(token)
        self._schedule_commit_if_ready()

    def keyPressEvent(self, event):  # noqa: N802
        """Handle key press event for hotkey capture edit."""
        if not self._recording:
            super().keyPressEvent(event)
            return
        try:
            modifiers, action = _tokens_from_qt_event(event)
            for modifier in modifiers:
                self._handle_captured_token(modifier, True)
            if action:
                self._handle_captured_token(action, True)
        except Exception as exc:
            print(f"[hotkey-capture] ignored key press: {type(exc).__name__}: {exc}", flush=True)
        event.accept()

    def keyReleaseEvent(self, event):  # noqa: N802
        """Handle key release event for hotkey capture edit."""
        if self._recording:
            try:
                key = _event_key_int(event)
                modifier_name = _MODIFIER_NAMES.get(key) if key is not None else None
                if modifier_name:
                    self._handle_captured_token(modifier_name, False)
                action = None if modifier_name or key is None else _qt_key_name(key)
                if action:
                    self._handle_captured_token(action, False)
            except Exception as exc:
                print(f"[hotkey-capture] ignored key release: {type(exc).__name__}: {exc}", flush=True)
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):  # noqa: N802
        """Focus out event."""
        if self._recording:
            self._cancel()
        super().focusOutEvent(event)
