"""Unit tests for the off-console CGEventTap hotkey matching logic.

These cover the pure parse/match helpers (no Quartz, no macOS required), which
are what decide whether a keystroke counts as a configured hotkey.
"""
from __future__ import annotations

from types import SimpleNamespace

from runtime.workers import hotkey_helper

# Minimal keycode map (subset of core.hotkeys.MACOS_VIRTUAL_KEYCODES).
VK = {"q": 12, "a": 0, "1": 18, "f1": 122, "f9": 101}

SHIFT = 0x00020000
CTRL = 0x00040000
ALT = 0x00080000
CMD = 0x00100000
FN = 0x00800000  # rides along on F-keys; must be ignored when matching


def test_parse_combo_rejects_bare_key():
    # A bare printable key would steal ordinary typing -> rejected.
    """Verify parse combo rejects bare key behavior."""
    assert hotkey_helper._parse_combo_to_tap("q", VK) is None


def test_parse_combo_ctrl_q():
    """Verify parse combo ctrl q behavior."""
    assert hotkey_helper._parse_combo_to_tap("ctrl+q", VK) == (12, CTRL)


def test_parse_combo_cmd_shift_1():
    """Verify parse combo cmd shift 1 behavior."""
    assert hotkey_helper._parse_combo_to_tap("cmd+shift+1", VK) == (18, CMD | SHIFT)


def test_match_exact_modifier():
    """Verify match exact modifier behavior."""
    table = hotkey_helper._build_tap_table([("ctrl+q", "caller", {"index": 0})], VK)
    # ctrl+q pressed -> match.
    assert hotkey_helper._match_tap_event(12, CTRL, table) == ("caller", {"index": 0})
    # ctrl+shift+q pressed -> NOT a match (extra modifier).
    assert hotkey_helper._match_tap_event(12, CTRL | SHIFT, table) is None
    # q alone -> no match.
    assert hotkey_helper._match_tap_event(12, 0, table) is None
    # different key with ctrl -> no match.
    assert hotkey_helper._match_tap_event(0, CTRL, table) is None


def test_match_ignores_fn_and_capslock_bits():
    """Verify match ignores fn and capslock bits behavior."""
    table = hotkey_helper._build_tap_table([("shift+f1", "snip", {})], VK)
    # Real F-key events carry the Fn bit; matching must ignore it.
    assert hotkey_helper._match_tap_event(122, SHIFT | FN, table) == ("snip", {})


def test_voice_key_parses_and_matches_as_start():
    # Push-to-talk default is a bare F-key (allowed; not a typing key).
    """Verify voice key parses and matches as start behavior."""
    parsed = hotkey_helper._parse_combo_to_tap("f9", VK)
    assert parsed == (101, 0)
    # main() appends the voice key to the table as a "voice_start" entry.
    keycode, modmask = parsed
    table = [(keycode, modmask, "voice_start", {})]
    # Key-down of f9 (Fn bit rides along, must be ignored) -> voice_start.
    assert hotkey_helper._match_tap_event(101, FN, table) == ("voice_start", {})


def test_build_table_skips_unparseable():
    """Verify build table skips unparseable behavior."""
    specs = [("ctrl+q", "caller", {"index": 0}), ("", "snip", {}), ("boguskey", "x", {})]
    table = hotkey_helper._build_tap_table(specs, VK)
    assert table == [(12, CTRL, "caller", {"index": 0})]


def test_specs_from_config():
    """Verify specs from config behavior."""
    config = SimpleNamespace(
        CALLER_ROWS=[
            {"hotkey": "ctrl+q", "hotkey_2": "ctrl+shift+q", "enabled": True},
            {"hotkey": "ctrl+x", "hotkey_2": "ctrl+shift+x", "enabled": False},
            {"hotkey": "cmd+shift+1", "hotkey_2": "", "enabled": True},
        ],
        HOTKEY_ADD_CONTEXT="ctrl+a",
        HOTKEY_ADD_CONTEXT_2="ctrl+shift+a",
        HOTKEY_CLEAR_CONTEXT="",
        HOTKEY_CLEAR_CONTEXT_2="",
        HOTKEY_SNIP="cmd+shift+s",
        HOTKEY_SNIP_2="cmd+alt+s",
        HOTKEY_READ_SELECTION_ALOUD="ctrl+alt+r",
        HOTKEY_READ_SELECTION_ALOUD_2="ctrl+shift+r",
        HOTKEY_VOICE_LIVE="shift+f9",
        HOTKEY_VOICE_LIVE_2="shift+f10",
    )
    specs = hotkey_helper._hotkey_specs_from_config(config)
    assert ("ctrl+q", "caller", {"index": 0}) in specs
    assert ("ctrl+shift+q", "caller", {"index": 0}) in specs
    assert ("cmd+shift+1", "caller", {"index": 2}) in specs
    assert all(extra != {"index": 1} for _, kind, extra in specs if kind == "caller")
    assert ("ctrl+a", "add_context", {}) in specs
    assert ("ctrl+shift+a", "add_context", {}) in specs
    assert ("cmd+shift+s", "snip", {}) in specs
    assert ("cmd+alt+s", "snip", {}) in specs
    assert ("ctrl+alt+r", "read_selection_aloud", {}) in specs
    assert ("ctrl+shift+r", "read_selection_aloud", {}) in specs
    assert ("shift+f10", "voice_live", {}) in specs
    # Empty caller hotkey and empty HOTKEY_CLEAR_CONTEXT are skipped.
    assert all(combo for combo, _, _ in specs)
    assert all(kind != "clear_context" for _, kind, _ in specs)
