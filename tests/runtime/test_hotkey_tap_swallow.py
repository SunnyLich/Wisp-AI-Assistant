"""The off-console hotkey tap must SWALLOW the matched hotkey key.

Regression test for the bug where pressing the rewrite hotkey (ctrl+shift+q)
leaked the "q" keystroke to the foreground app and replaced the selected text.
The active CGEventTap consumes the matched key-down/up (returns None) so it never
reaches the app, while passing ordinary keystrokes through untouched.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from runtime.workers import hotkey_helper

VK = {"q": 12, "f9": 101}
CTRL = 0x00040000
SHIFT = 0x00020000

_F_KEYCODE = object()
_F_AUTOREPEAT = object()


class _FakeQuartz:
    """Test case for fake quartz behavior."""
    kCGEventTapDisabledByTimeout = 0xFFFFFFFE
    kCGEventTapDisabledByUserInput = -3
    kCGEventKeyDown = 10
    kCGEventKeyUp = 11
    kCGKeyboardEventKeycode = _F_KEYCODE
    kCGKeyboardEventAutorepeat = _F_AUTOREPEAT
    kCGSessionEventTap = 1
    kCGHeadInsertEventTap = 0
    kCGEventTapOptionDefault = 0
    kCGEventTapOptionListenOnly = 1
    kCFRunLoopCommonModes = object()

    def __init__(self, tap_factory=None):
        """Initialize the fake quartz instance."""
        self.captured: dict = {}
        self._tap_factory = tap_factory or (lambda option: SimpleNamespace(name="tap"))

    def CGEventMaskBit(self, bit):
        """Verify c g event mask bit behavior."""
        return 1 << bit

    def CGEventGetIntegerValueField(self, event, field):
        """Verify c g event get integer value field behavior."""
        if field is _F_KEYCODE:
            return event.keycode
        if field is _F_AUTOREPEAT:
            return event.autorepeat
        return 0

    def CGEventGetFlags(self, event):
        """Verify c g event get flags behavior."""
        return event.flags

    def CGEventTapCreate(self, loc, place, option, mask, cb, refcon):
        """Verify c g event tap create behavior."""
        self.captured.setdefault("options", []).append(option)
        self.captured["cb"] = cb
        return self._tap_factory(option)

    def CFMachPortCreateRunLoopSource(self, a, tap, b):
        """Verify c f mach port create run loop source behavior."""
        return SimpleNamespace()

    def CFRunLoopGetCurrent(self):
        """Verify c f run loop get current behavior."""
        return SimpleNamespace()

    def CFRunLoopAddSource(self, *a):
        """Verify c f run loop add source behavior."""
        pass

    def CGEventTapEnable(self, tap, on):
        """Verify c g event tap enable behavior."""
        pass


def _event(keycode, flags=0, autorepeat=0):
    """Verify event behavior."""
    return SimpleNamespace(keycode=keycode, flags=flags, autorepeat=autorepeat)


def _install(monkeypatch, fake):
    """Verify install behavior."""
    monkeypatch.setitem(sys.modules, "Quartz", fake)
    hotkey_helper._HOTKEY_TAP = None
    table = hotkey_helper._build_tap_table([("ctrl+q", "caller", {"index": 0})], VK)
    emitted: list = []
    ok = hotkey_helper._install_hotkey_tap(
        table, lambda kind, **e: emitted.append((kind, e)), voice_keycode=101
    )
    assert ok
    return fake.captured["cb"], emitted, fake


def test_active_tap_swallows_matched_keydown(monkeypatch):
    """Verify active tap swallows matched keydown behavior."""
    cb, emitted, fake = _install(monkeypatch, _FakeQuartz())
    # Prefers the active (swallowing) option first.
    assert fake.captured["options"][0] == _FakeQuartz.kCGEventTapOptionDefault
    # ctrl+q key-down -> emits and is consumed (None), so the app never sees "q".
    ret = cb(None, fake.kCGEventKeyDown, _event(12, CTRL), None)
    assert ret is None
    assert emitted == [("caller", {"index": 0})]


def test_passes_through_ordinary_keystroke(monkeypatch):
    """Verify passes through ordinary keystroke behavior."""
    cb, emitted, fake = _install(monkeypatch, _FakeQuartz())
    ev = _event(12, 0)  # bare "q", no modifiers -> not a hotkey
    ret = cb(None, fake.kCGEventKeyDown, ev, None)
    assert ret is ev  # passed through untouched
    assert emitted == []


def test_autorepeat_of_hotkey_is_swallowed_without_reemitting(monkeypatch):
    """Verify autorepeat of hotkey is swallowed without reemitting behavior."""
    cb, emitted, fake = _install(monkeypatch, _FakeQuartz())
    ret = cb(None, fake.kCGEventKeyDown, _event(12, CTRL, autorepeat=1), None)
    assert ret is None  # still consumed so it can't leak
    assert emitted == []  # but does not fire a second time


def test_keyup_of_owned_key_is_swallowed(monkeypatch):
    """Verify keyup of owned key is swallowed behavior."""
    cb, emitted, fake = _install(monkeypatch, _FakeQuartz())
    # key-up of the hotkey letter -> swallowed (no dangling key-up to the app).
    assert cb(None, fake.kCGEventKeyUp, _event(12), None) is None
    # key-up of the push-to-talk key -> emits voice_stop and is swallowed.
    assert cb(None, fake.kCGEventKeyUp, _event(101), None) is None
    assert ("voice_stop", {}) in emitted
    # key-up of an unrelated key -> passed through.
    ev = _event(2)
    assert cb(None, fake.kCGEventKeyUp, ev, None) is ev


def test_falls_back_to_listen_only_when_active_tap_unavailable(monkeypatch):
    # Active tap creation fails (e.g. Accessibility not granted); listen-only works.
    """Verify falls back to listen only when active tap unavailable behavior."""
    def factory(option):
        """Verify factory behavior."""
        if option == _FakeQuartz.kCGEventTapOptionDefault:
            return None
        return SimpleNamespace(name="listen-only-tap")

    cb, _emitted, fake = _install(monkeypatch, _FakeQuartz(tap_factory=factory))
    # It tried Default first, then fell back to ListenOnly.
    assert fake.captured["options"] == [
        _FakeQuartz.kCGEventTapOptionDefault,
        _FakeQuartz.kCGEventTapOptionListenOnly,
    ]
