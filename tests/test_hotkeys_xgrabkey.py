"""Tests for the Linux XGrabKey hotkey backend."""

from __future__ import annotations

import types


def test_linux_hotkey_listener_uses_xgrabkey_for_global_and_ptt(monkeypatch):
    import config
    import core.hotkeys as hotkeys

    monkeypatch.setattr(hotkeys, "_IS_WIN", False)
    monkeypatch.setattr(hotkeys, "_IS_MAC", False)
    monkeypatch.setattr(config, "CALLER_ROWS", [{"hotkey": "ctrl+alt+space"}], raising=False)
    monkeypatch.setattr(config, "HOTKEY_ADD_CONTEXT", "", raising=False)
    monkeypatch.setattr(config, "HOTKEY_CLEAR_CONTEXT", "", raising=False)
    monkeypatch.setattr(config, "HOTKEY_SNIP", "", raising=False)
    monkeypatch.setattr(config, "HOTKEY_READ_SELECTION_ALOUD", "", raising=False)
    monkeypatch.setattr(config, "HOTKEY_VOICE", "f9", raising=False)
    monkeypatch.setattr(config, "HOTKEY_DICTATE", "f10", raising=False)

    listener = hotkeys.HotkeyListener(
        on_callers=[lambda: None],
        on_voice_start=lambda: None,
        on_voice_stop=lambda: None,
        on_dictate_start=lambda: None,
        on_dictate_stop=lambda: None,
    )
    monkeypatch.setattr(listener._impl, "start", lambda: True)
    monkeypatch.setattr(listener, "_start_voice_listener", lambda: (_ for _ in ()).throw(AssertionError))

    assert isinstance(listener._impl, hotkeys._XGrabKeyImpl)
    assert listener.start() is True
    assert listener._voice_listener is None


def test_xgrabkey_parser_maps_configured_chord(monkeypatch):
    import core.hotkeys as hotkeys

    fake_x = types.SimpleNamespace(
        ControlMask=0x04,
        ShiftMask=0x01,
        Mod1Mask=0x08,
        Mod4Mask=0x40,
    )
    fake_xk = types.SimpleNamespace(
        string_to_keysym=lambda name: {
            "space": 65,
            "F9": 75,
            "Return": 36,
        }.get(name, 0)
    )
    fake_xlib = types.SimpleNamespace(X=fake_x, XK=fake_xk)
    monkeypatch.setitem(__import__("sys").modules, "Xlib", fake_xlib)

    impl = hotkeys._XGrabKeyImpl([])
    impl._display = types.SimpleNamespace(keysym_to_keycode=lambda keysym: keysym + 8)

    assert impl._parse_hotkey_x11("ctrl+alt+space") == (73, fake_x.ControlMask | fake_x.Mod1Mask)
    assert impl._parse_hotkey_x11("win+f9") == (83, fake_x.Mod4Mask)
    assert impl._parse_hotkey_x11("space") is None
