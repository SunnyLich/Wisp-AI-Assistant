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


def test_listener_registers_both_bindings_and_skips_disabled_callers(monkeypatch):
    """Both configured bindings invoke the same action callback at runtime."""
    import config
    import core.hotkeys as hotkeys

    monkeypatch.setattr(hotkeys, "_IS_WIN", False)
    monkeypatch.setattr(hotkeys, "_IS_MAC", False)
    monkeypatch.setattr(
        config,
        "CALLER_ROWS",
        [
            {
                "hotkey": "ctrl+alt+q",
                "hotkey_2": "ctrl+alt+w",
                "enabled": True,
            },
            {
                "hotkey": "ctrl+alt+x",
                "hotkey_2": "ctrl+alt+y",
                "enabled": False,
            },
        ],
        raising=False,
    )
    values = {
        "HOTKEY_ADD_CONTEXT": "ctrl+1",
        "HOTKEY_ADD_CONTEXT_2": "ctrl+2",
        "HOTKEY_CLEAR_CONTEXT": "ctrl+3",
        "HOTKEY_CLEAR_CONTEXT_2": "ctrl+4",
        "HOTKEY_SNIP": "ctrl+5",
        "HOTKEY_SNIP_2": "ctrl+6",
        "HOTKEY_READ_SELECTION_ALOUD": "ctrl+7",
        "HOTKEY_READ_SELECTION_ALOUD_2": "ctrl+8",
        "HOTKEY_VOICE_LIVE": "shift+f9",
        "HOTKEY_VOICE_LIVE_2": "shift+f10",
        "HOTKEY_VOICE": "f9",
        "HOTKEY_VOICE_2": "f10",
        "HOTKEY_DICTATE": "f7",
        "HOTKEY_DICTATE_2": "f8",
    }
    for name, value in values.items():
        monkeypatch.setattr(config, name, value, raising=False)

    caller = lambda: None  # noqa: E731
    disabled_caller = lambda: None  # noqa: E731
    add_context = lambda: None  # noqa: E731
    snip = lambda: None  # noqa: E731
    voice_live = lambda: None  # noqa: E731
    listener = hotkeys.HotkeyListener(
        on_callers=[caller, disabled_caller],
        on_add_context=add_context,
        on_snip=snip,
        on_voice_live=voice_live,
    )

    assert ("ctrl+alt+q", caller) in listener._hotkey_defs
    assert ("ctrl+alt+w", caller) in listener._hotkey_defs
    assert all(callback is not disabled_caller for _, callback in listener._hotkey_defs)
    assert ("ctrl+1", add_context) in listener._hotkey_defs
    assert ("ctrl+2", add_context) in listener._hotkey_defs
    assert ("ctrl+5", snip) in listener._hotkey_defs
    assert ("ctrl+6", snip) in listener._hotkey_defs
    assert ("shift+f9", voice_live) in listener._hotkey_defs
    assert ("shift+f10", voice_live) in listener._hotkey_defs
    assert listener._voice_hotkeys == ("f9", "f10")
    assert listener._dictate_hotkeys == ("f7", "f8")
