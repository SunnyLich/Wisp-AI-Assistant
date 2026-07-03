"""Tests for the Linux native-worker hotkey backend."""

from __future__ import annotations


def test_linux_hotkey_listener_uses_existing_pynput_global_backend(monkeypatch):
    import config
    import core.hotkeys as hotkeys

    monkeypatch.setattr(hotkeys, "_IS_WIN", False)
    monkeypatch.setattr(hotkeys, "_IS_MAC", False)
    monkeypatch.setattr(config, "CALLER_ROWS", [{"hotkey": "ctrl+alt+space"}], raising=False)
    monkeypatch.setattr(config, "HOTKEY_ADD_CONTEXT", "", raising=False)
    monkeypatch.setattr(config, "HOTKEY_CLEAR_CONTEXT", "", raising=False)
    monkeypatch.setattr(config, "HOTKEY_SNIP", "", raising=False)
    monkeypatch.setattr(config, "HOTKEY_READ_SELECTION_ALOUD", "", raising=False)
    monkeypatch.setattr(config, "HOTKEY_VOICE", "", raising=False)
    monkeypatch.setattr(config, "HOTKEY_DICTATE", "", raising=False)

    listener = hotkeys.HotkeyListener(on_callers=[lambda: None])

    assert isinstance(listener._impl, hotkeys._PynputImpl)


def test_linux_push_to_talk_uses_existing_pynput_listener(monkeypatch):
    import config
    import core.hotkeys as hotkeys

    calls: list[str] = []
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
    monkeypatch.setattr(listener, "_start_voice_listener", lambda: calls.append("start-ptt"))

    assert listener.start() is True
    assert calls == ["start-ptt"]


def test_pynput_hotkey_conversion_keeps_linux_global_chords_safe(monkeypatch):
    import core.hotkeys as hotkeys

    monkeypatch.setattr(hotkeys, "_IS_MAC", False)

    assert hotkeys._to_pynput_hotkey("ctrl+alt+q") == "<ctrl>+<alt>+q"
    assert hotkeys._to_pynput_hotkey("f9") == "<f9>"
    assert hotkeys._to_pynput_hotkey("q") is None
