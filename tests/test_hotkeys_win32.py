"""Windows hotkey backend regression tests."""
from __future__ import annotations

import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hotkey backend is tested on Windows")
def test_win32_stop_waits_for_message_pump_to_unregister(monkeypatch):
    """Verify stop waits for the pump thread so replacement keybinds can register."""
    import core.hotkeys as hotkeys

    if not hasattr(hotkeys, "_Win32Impl"):
        pytest.skip("Win32 hotkey backend is only defined on Windows")

    post_calls: list[tuple[int, int, int, int]] = []
    join_calls: list[float | None] = []

    class FakeUser32:
        def PostThreadMessageW(self, tid, msg, wparam, lparam):
            post_calls.append((tid, msg, wparam, lparam))
            return 1

    class FakeThread:
        def __init__(self) -> None:
            self._alive = True

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout=None) -> None:
            join_calls.append(timeout)
            self._alive = False

    impl = hotkeys._Win32Impl([])
    impl._pump_tid = 123
    impl._pump_thread = FakeThread()
    monkeypatch.setattr(hotkeys, "_user32", FakeUser32())

    impl.stop()

    assert post_calls == [(123, hotkeys.WM_QUIT, 0, 0)]
    assert join_calls == [2.0]
    assert impl._pump_tid == 0
    assert impl._pump_thread is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hotkey backend is tested on Windows")
def test_voice_live_toggle_registers_as_plain_hotkey(monkeypatch):
    """The live voice toggle is a press-only hotkey (not push-to-talk)."""
    import config
    import core.hotkeys as hotkeys

    monkeypatch.setattr(config, "HOTKEY_VOICE_LIVE", "shift+f9", raising=False)
    on_voice_live = lambda: None  # noqa: E731

    listener = hotkeys.HotkeyListener(on_callers=[], on_voice_live=on_voice_live)

    assert ("shift+f9", on_voice_live) in listener._hotkey_defs


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hotkey backend is tested on Windows")
def test_push_to_talk_ignores_modified_presses():
    """Shift+F9 belongs to the live voice toggle, not the F9 push-to-talk key."""
    import core.hotkeys as hotkeys

    starts: list[str] = []
    stops: list[str] = []
    listener = hotkeys.HotkeyListener(on_callers=[])
    f9, shift = object(), object()
    listener._ptt_map = {f9: (lambda: starts.append("start"), lambda: stops.append("stop"))}
    listener._ptt_modifier_keys = frozenset({shift})

    # shift held: the press/release pair must not start or stop a recording
    listener._voice_press(shift)
    listener._voice_press(f9)
    listener._voice_release(f9)
    listener._voice_release(shift)
    assert starts == []
    assert stops == []

    # bare F9 push-to-talk still works
    listener._voice_press(f9)
    listener._voice_release(f9)
    assert starts == ["start"]
    assert stops == ["stop"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hotkey backend is tested on Windows")
def test_push_to_talk_survives_modifier_pressed_mid_hold():
    """A modifier pressed while F9 is already held must not swallow the release."""
    import core.hotkeys as hotkeys

    starts: list[str] = []
    stops: list[str] = []
    listener = hotkeys.HotkeyListener(on_callers=[])
    f9, shift = object(), object()
    listener._ptt_map = {f9: (lambda: starts.append("start"), lambda: stops.append("stop"))}
    listener._ptt_modifier_keys = frozenset({shift})

    listener._voice_press(f9)          # bare press starts recording
    listener._voice_press(shift)       # user grazes shift mid-hold
    listener._voice_press(f9)          # OS key auto-repeat while held
    listener._voice_release(f9)        # release must still stop the recording
    listener._voice_release(shift)

    assert starts == ["start", "start"]  # auto-repeat behavior is unchanged
    assert stops == ["stop"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hotkey backend is tested on Windows")
def test_voice_live_toggle_disabled_when_unbound(monkeypatch):
    """An empty HOTKEY_VOICE_LIVE turns the feature's hotkey off entirely."""
    import config
    import core.hotkeys as hotkeys

    monkeypatch.setattr(config, "HOTKEY_VOICE_LIVE", "", raising=False)
    on_voice_live = lambda: None  # noqa: E731

    listener = hotkeys.HotkeyListener(on_callers=[], on_voice_live=on_voice_live)

    assert on_voice_live not in [cb for _combo, cb in listener._hotkey_defs]
