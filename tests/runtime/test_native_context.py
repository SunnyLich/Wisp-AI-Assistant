"""Tests for native-worker paste and context-snapshot platform behavior."""

from __future__ import annotations

import sys

import pytest

from core import capture, context_fetcher
from core.platform import linux_atspi
from runtime.workers import native_host


def test_windows_selection_anchor_fallback_order(monkeypatch):
    app_rect = {"left": 10, "top": 20, "width": 30, "height": 12}
    uia_rect = {"left": 40, "top": 50, "width": 60, "height": 14}
    caret_rect = {"left": 70, "top": 80, "width": 2, "height": 18}
    mouse_rect = {"left": 90, "top": 100, "width": 2, "height": 20}
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_focus_cache",
        {"token": 9, "kind": "win-uia", "range": object()},
    )
    monkeypatch.setattr(native_host, "_win_uia_selection_screen_rect", lambda _range: uia_rect)
    monkeypatch.setattr(native_host, "_win_caret_screen_rect", lambda _hwnd=0: caret_rect)
    monkeypatch.setattr(native_host, "_win_cursor_screen_rect", lambda: mouse_rect)

    native = native_host.selection_anchor_resolve(
        focus_token=9,
        app_native_rect=app_rect,
    )
    assert native["source"] == "app-native"

    uia = native_host.selection_anchor_resolve(focus_token=9, app_native_rect={})
    assert uia["source"] == "uia"

    caret = native_host.selection_anchor_resolve(focus_token=0, allow_mouse=True)
    assert caret["source"] == "os-caret"

    monkeypatch.setattr(native_host, "_win_caret_screen_rect", lambda _hwnd=0: {})
    mouse = native_host.selection_anchor_resolve(focus_token=0, allow_mouse=True)
    assert mouse["source"] == "mouse"


def test_windows_scrolled_exact_range_hides_without_random_fallback(monkeypatch):
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_focus_cache",
        {"token": 12, "kind": "win-uia", "range": object()},
    )
    monkeypatch.setattr(native_host, "_win_uia_selection_screen_rect", lambda _range: {})
    monkeypatch.setattr(
        native_host,
        "_win_caret_screen_rect",
        lambda _hwnd=0: pytest.fail("scroll refresh must not use OpenWand's current caret"),
    )
    monkeypatch.setattr(
        native_host,
        "_win_cursor_screen_rect",
        lambda: pytest.fail("scroll refresh must not use the current mouse"),
    )

    result = native_host.selection_anchor_resolve(
        focus_token=12,
        refresh=True,
        allow_mouse=False,
    )

    assert result == {
        "ok": True,
        "visible": False,
        "source": "uia",
        "selection_rect": {},
    }


def test_two_annotations_keep_their_own_screen_positions(monkeypatch):
    range_a = object()
    range_b = object()
    rect_a = {"left": 100, "top": 200, "width": 80, "height": 18}
    rect_b = {"left": 500, "top": 600, "width": 90, "height": 18}
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_focus_cache",
        {"token": 202, "kind": "win-uia", "range": range_b},
    )
    monkeypatch.setattr(
        native_host,
        "_focus_anchors",
        {
            101: {"token": 101, "kind": "win-uia", "range": range_a},
            202: {"token": 202, "kind": "win-uia", "range": range_b},
        },
    )
    monkeypatch.setattr(
        native_host,
        "_win_uia_selection_screen_rect",
        lambda text_range: rect_a if text_range is range_a else rect_b,
    )

    annotation_a = native_host.selection_anchor_resolve(focus_token=101, allow_mouse=False)
    annotation_b = native_host.selection_anchor_resolve(focus_token=202, allow_mouse=False)

    assert annotation_a["selection_rect"] == {
        **rect_a,
        "endpoint_x": 180.0,
        "endpoint_y": 209.0,
    }
    assert annotation_b["selection_rect"] == {
        **rect_b,
        "endpoint_x": 590.0,
        "endpoint_y": 609.0,
    }


def test_windows_classic_richedit_uses_cached_uia_range_for_geometry(monkeypatch):
    geometry_range = object()
    expected = {"left": 380, "top": 260, "width": 90, "height": 20}
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_focus_cache",
        {
            "token": 14,
            "kind": "win-edit",
            "class_name": "RICHEDIT50W",
            "geometry_range": geometry_range,
        },
    )
    monkeypatch.setattr(
        native_host,
        "_win_uia_selection_screen_rect",
        lambda value: expected if value is geometry_range else {},
    )
    monkeypatch.setattr(
        native_host,
        "_win_edit_selection_screen_rect",
        lambda *_args, **_kwargs: pytest.fail("cross-process RichEdit POINT must not be trusted"),
    )

    result = native_host.selection_anchor_resolve(focus_token=14, refresh=True)

    assert result["source"] == "uia"
    assert result["selection_rect"] == {
        **expected,
        "endpoint_x": 470.0,
        "endpoint_y": 270.0,
    }


def test_windows_scroll_visibility_uses_editor_viewport_not_whole_app(monkeypatch):
    expected = {"left": 140, "top": 90, "width": 80, "height": 20}
    validated_against: list[int] = []
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_focus_cache",
        {
            "token": 15,
            "kind": "win-edit",
            "class_name": "EDIT",
            "input_hwnd": 222,
            "selection_end": 4,
            "document_text": "text",
        },
    )
    monkeypatch.setattr(
        native_host,
        "_win_edit_selection_screen_rect",
        lambda *_args, **_kwargs: expected,
    )

    def validate(value, *, source_window_id=0):
        validated_against.append(source_window_id)
        # The rectangle could still overlap the top-level app (111), but it is
        # outside the actual editor viewport (222) and therefore not visible.
        return {} if source_window_id == 222 else value

    monkeypatch.setattr(native_host, "_win_valid_selection_anchor", validate)

    result = native_host.selection_anchor_resolve(
        focus_token=15,
        source_window_id=111,
        refresh=True,
    )

    assert result["visible"] is False
    assert result["source"] == "native-edit"
    assert validated_against == [222]


def test_windows_native_edit_anchor_endpoint_is_not_shifted_by_caret_width(monkeypatch):
    rect = {"left": 410.0, "top": 260.0, "width": 2.0, "height": 20.0}
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_focus_cache",
        {
            "token": 16,
            "kind": "win-edit",
            "class_name": "EDIT",
            "input_hwnd": 222,
            "selection_end": 8,
            "document_text": "selected",
        },
    )
    monkeypatch.setattr(
        native_host,
        "_win_edit_selection_screen_rect",
        lambda *_args, **_kwargs: rect,
    )
    monkeypatch.setattr(
        native_host,
        "_win_valid_selection_anchor",
        lambda value, **_kwargs: dict(value or {}),
    )

    result = native_host.selection_anchor_resolve(
        focus_token=16,
        refresh=True,
    )

    assert result["source"] == "native-edit"
    assert result["selection_rect"]["endpoint_x"] == 410.0
    assert result["selection_rect"]["endpoint_y"] == 270.0


def test_windows_uia_selection_uses_last_visible_screen_rectangle(monkeypatch):
    """Multi-line selections anchor the popup beside the selection's final line."""
    monkeypatch.setattr(native_host, "IS_WIN", True)

    class FakeRange:
        @staticmethod
        def GetBoundingRectangles():
            return [100.0, 120.0, 180.0, 20.0, 100.0, 140.0, 90.0, 20.0]

    assert native_host._win_uia_selection_screen_rect(FakeRange()) == {
        "left": 100.0,
        "top": 140.0,
        "width": 90.0,
        "height": 20.0,
    }


def test_windows_backward_selection_keeps_hotkey_time_caret_endpoint(monkeypatch):
    """The bubble follows the active start, not document order, after focus moves."""
    monkeypatch.setattr(native_host, "IS_WIN", True)

    class FakeRange:
        @staticmethod
        def GetBoundingRectangles():
            return [100.0, 120.0, 80.0, 20.0, 100.0, 140.0, 160.0, 20.0]

    text_range = FakeRange()
    affinity = native_host._win_uia_anchor_affinity(
        text_range,
        caret_rect={"left": 100.0, "top": 120.0, "width": 2.0, "height": 20.0},
        pointer_rect={},
    )
    assert affinity == "start"

    monkeypatch.setattr(
        native_host,
        "_focus_cache",
        {
            "token": 81,
            "kind": "win-uia",
            "range": text_range,
            "anchor_affinity": affinity,
        },
    )
    result = native_host.selection_anchor_resolve(focus_token=81, allow_mouse=False)

    assert result["source"] == "uia"
    assert result["selection_rect"] == {
        "left": 100.0,
        "top": 120.0,
        "width": 80.0,
        "height": 20.0,
        "endpoint_x": 100.0,
        "endpoint_y": 130.0,
    }


def test_windows_unrelated_pointer_cannot_reverse_keyboard_selection(monkeypatch):
    """Mouse fallback is accepted only when it is visibly at a range endpoint."""
    monkeypatch.setattr(native_host, "IS_WIN", True)

    class FakeRange:
        @staticmethod
        def GetBoundingRectangles():
            return [100.0, 120.0, 180.0, 20.0]

    assert native_host._win_uia_anchor_affinity(
        FakeRange(),
        caret_rect={},
        pointer_rect={"left": 900.0, "top": 700.0, "width": 2.0, "height": 20.0},
    ) == ""


def test_windows_bidirectional_selection_keeps_left_edge_on_document_end(monkeypatch):
    """The visual caret edge is independent from document start/end ordering."""
    monkeypatch.setattr(native_host, "IS_WIN", True)

    class FakeRange:
        @staticmethod
        def GetBoundingRectangles():
            return [300.0, 120.0, 80.0, 20.0, 200.0, 140.0, 160.0, 20.0]

    text_range = FakeRange()
    affinity, edge = native_host._win_uia_anchor_details(
        text_range,
        caret_rect={"left": 200.0, "top": 140.0, "width": 2.0, "height": 20.0},
        pointer_rect={},
    )
    assert (affinity, edge) == ("end", "left")
    monkeypatch.setattr(
        native_host,
        "_focus_cache",
        {
            "token": 82,
            "kind": "win-uia",
            "range": text_range,
            "anchor_affinity": affinity,
            "anchor_edge": edge,
        },
    )

    result = native_host.selection_anchor_resolve(focus_token=82, allow_mouse=False)

    assert result["selection_rect"]["top"] == 140.0
    assert result["selection_rect"]["endpoint_x"] == 200.0
    assert result["selection_rect"]["endpoint_y"] == 150.0


def test_windows_uia_selection_rejects_clipped_viewport_sliver(monkeypatch):
    """An offscreen Monaco range can appear as a 2 px strip at the editor edge."""
    monkeypatch.setattr(native_host, "IS_WIN", True)

    class FakeRange:
        @staticmethod
        def GetBoundingRectangles():
            return [216.0, 172.0, 200.0, 2.0]

    assert native_host._win_uia_selection_screen_rect(FakeRange()) == {}


def test_capture_worker_failure_matrix_is_in_band(tmp_path, monkeypatch):
    """Capture permission, backend, stale-target, and empty-region faults stay controlled."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    calls: list[dict | None] = []

    def fail_capture(region=None):
        calls.append(region)
        raise PermissionError("screen-recording permission missing")

    monkeypatch.setattr(capture, "get_screen_snippet", fail_capture)
    fullscreen = native_host.capture_fullscreen(str(tmp_path / "permission.png"))
    region = native_host.capture_region(
        str(tmp_path / "region.png"),
        {"left": 10, "top": 20, "width": 30, "height": 40},
    )
    assert fullscreen["ok"] is False
    assert region["ok"] is False
    assert "PermissionError" in fullscreen["error"]
    assert "PermissionError" in region["error"]

    calls.clear()
    for invalid in (
        None,
        {},
        {"left": 0, "top": 0, "width": 0, "height": 10},
        {"left": 0, "top": 0, "width": "bad", "height": 10},
    ):
        result = native_host.capture_region(str(tmp_path / "empty.png"), invalid)
        assert result["ok"] is False
        assert "empty or invalid" in result["error"]
    assert calls == []

    def vanished_target(_region=None):
        raise OSError("target window disappeared")

    monkeypatch.setattr(capture, "get_screen_snippet", vanished_target)
    vanished = native_host.capture_region(
        str(tmp_path / "vanished.png"),
        {"left": 10, "top": 20, "width": 30, "height": 40},
    )
    assert vanished["ok"] is False
    assert "target window disappeared" in vanished["error"]


def test_paste_text_can_restore_clipboard(monkeypatch):
    """Verify paste-back can avoid leaving generated text on the clipboard."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    clipboard_sets: list[str] = []
    keys: list[str] = []
    focused: list[int] = []

    import core.platform_utils as platform_utils

    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": "original clipboard"})
    monkeypatch.setattr(native_host, "clipboard_set", lambda text="": clipboard_sets.append(text) or {"ok": True})
    monkeypatch.setattr(platform_utils, "set_foreground_window", lambda wid: focused.append(wid))
    monkeypatch.setattr(platform_utils, "get_foreground_window", lambda: 777)
    monkeypatch.setattr(platform_utils, "send_keys", lambda combo: keys.append(combo))
    monkeypatch.setattr(native_host.time, "sleep", lambda _seconds: None)

    result = native_host.paste_text("model reply", target_pid=777, restore_clipboard=True)

    assert result["ok"] is True, result
    assert result["clipboard_restored"] is True
    assert focused == [777]
    assert keys == [platform_utils.PASTE_COMBO]
    assert clipboard_sets == ["model reply", "original clipboard"]


def test_undo_edit_refocuses_original_window_before_sending_undo(monkeypatch):
    """Undo is sent only after the captured target is confirmed foreground."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", True)
    focused: list[int] = []
    keys: list[str] = []

    import core.platform_utils as platform_utils

    monkeypatch.setattr(platform_utils, "set_foreground_window", focused.append)
    monkeypatch.setattr(platform_utils, "get_foreground_window", lambda: 7007)
    monkeypatch.setattr(platform_utils, "send_keys", keys.append)
    monkeypatch.setattr(native_host, "_win_window_for_pid", lambda pid: 7007 if pid == 777 else 0)
    monkeypatch.setattr(native_host, "_win_window_pid", lambda hwnd: 777 if hwnd == 7007 else 0)
    monkeypatch.setattr(native_host, "_focus_cached_edit_target", lambda token: token == 9)
    monkeypatch.setattr(native_host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(native_host, "clipboard_set", lambda _text="": pytest.fail("fallback used"))

    result = native_host.undo_edit(
        target_pid=777,
        focus_token=9,
        original_text="before",
        replacement_text="after",
    )

    assert result == {"ok": True, "method": "app-undo", "clipboard_ok": False}
    assert focused == [7007]
    assert keys == ["ctrl+z"]


def test_undo_edit_copies_original_when_target_cannot_be_confirmed(monkeypatch):
    """A focus race must never send undo to the wrong app."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", True)
    copied: list[str] = []

    import core.platform_utils as platform_utils

    monkeypatch.setattr(platform_utils, "set_foreground_window", lambda _wid: None)
    monkeypatch.setattr(platform_utils, "get_foreground_window", lambda: 9009)
    monkeypatch.setattr(platform_utils, "send_keys", lambda _combo: pytest.fail("undo sent to wrong app"))
    monkeypatch.setattr(native_host, "_win_window_for_pid", lambda _pid: 7007)
    monkeypatch.setattr(native_host, "_win_window_pid", lambda hwnd: 999 if hwnd == 9009 else 777)
    monkeypatch.setattr(native_host, "_focus_cached_edit_target", lambda _token: True)
    monkeypatch.setattr(native_host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        native_host,
        "clipboard_set",
        lambda text="": copied.append(text) or {"ok": True},
    )

    result = native_host.undo_edit(
        target_pid=777,
        focus_token=9,
        original_text="before",
        replacement_text="after",
    )

    assert result["ok"] is False
    assert result["clipboard_ok"] is True
    assert result["method"] == "clipboard-fallback"
    assert copied == ["before"]


def test_paste_text_uses_windows_uia_focus_token(monkeypatch):
    """Verify Windows paste-back anchors to the captured selection token."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", True)
    applied: list[dict[str, object]] = []

    def apply(token: int, text: str, *, paste_combo: str = "", restore_clipboard: bool = False):
        applied.append(
            {
                "token": token,
                "text": text,
                "paste_combo": paste_combo,
                "restore_clipboard": restore_clipboard,
            }
        )
        return {"ok": True, "method": "uia-range", "confirmed": True}

    import core.platform_utils as platform_utils

    monkeypatch.setattr(native_host, "_win_uia_apply_selected_text", apply)
    monkeypatch.setattr(platform_utils, "set_foreground_window", lambda _wid: pytest.fail("unanchored focus used"))
    monkeypatch.setattr(platform_utils, "send_keys", lambda _combo: pytest.fail("unanchored paste used"))

    result = native_host.paste_text(
        "model reply",
        target_pid=777,
        focus_token=9,
        restore_clipboard=True,
    )

    assert result["ok"] is True
    assert result["method"] == "uia-range"
    assert applied == [
        {
            "token": 9,
            "text": "model reply",
            "paste_combo": "",
            "restore_clipboard": True,
        }
    ]


def test_windows_focus_capture_keeps_a_collapsed_editor_caret(monkeypatch):
    """A caret-only editor target is cacheable for later Untitled insertion."""
    import sys
    import types

    fake_uiac = types.ModuleType("comtypes.gen.UIAutomationClient")
    fake_uiac.IUIAutomation = object
    fake_uiac.IUIAutomationTextPattern = object
    fake_uiac.TextPatternRangeEndpoint_Start = 0
    fake_uiac.TextPatternRangeEndpoint_End = 1

    class FakeRange:
        def CompareEndpoints(self, *_args):
            return 0

    class FakeSelections:
        Length = 1

        def GetElement(self, _index):
            return FakeRange()

    class FakeTextPattern:
        def GetSelection(self):
            return FakeSelections()

    class FakeRawPattern:
        def QueryInterface(self, _interface):
            return FakeTextPattern()

    class FakeElement:
        def GetCurrentPattern(self, _pattern_id):
            return FakeRawPattern()

    class FakeUia:
        def GetFocusedElement(self):
            return FakeElement()

    fake_client = types.ModuleType("comtypes.client")
    fake_client.GetModule = lambda _name: None
    fake_client.CreateObject = lambda *_args, **_kwargs: FakeUia()
    fake_comtypes = types.ModuleType("comtypes")
    fake_comtypes.__path__ = []
    fake_comtypes.client = fake_client
    fake_gen = types.ModuleType("comtypes.gen")
    fake_gen.__path__ = []
    fake_gen.UIAutomationClient = fake_uiac

    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "_focus_seq", 0)
    native_host._focus_cache.clear()
    monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)
    monkeypatch.setitem(sys.modules, "comtypes.client", fake_client)
    monkeypatch.setitem(sys.modules, "comtypes.gen", fake_gen)
    monkeypatch.setitem(sys.modules, "comtypes.gen.UIAutomationClient", fake_uiac)

    token = native_host._win_uia_capture_focus()

    assert token == 1
    assert native_host._focus_cache["range"].CompareEndpoints(0, None, 1) == 0
    assert native_host._focus_cache["collapsed"] is True


def test_windows_background_text_units_preserve_unicode_and_normalize_newlines():
    units = native_host._win_background_text_units("A\n\U0001f642")

    assert units == [0x0041, 0x000D, 0xD83D, 0xDE42]


def test_windows_uia_range_context_binds_exact_prefix_selection_and_suffix(monkeypatch):
    class Range:
        def __init__(self, text: str, start: int, end: int):
            self.text = text
            self.start = start
            self.end = end

        def Clone(self):
            return Range(self.text, self.start, self.end)

        def MoveEndpointByRange(self, endpoint, other, other_endpoint):
            value = other.start if other_endpoint == 0 else other.end
            if endpoint == 0:
                self.start = value
            else:
                self.end = value

        def GetText(self, _limit):
            return self.text[self.start : self.end]

    class Pattern:
        DocumentRange = Range("A rough sentence.", 0, 17)

    monkeypatch.setattr(native_host, "IS_WIN", True)
    context = native_host._win_uia_range_context(Pattern(), Range("A rough sentence.", 2, 7))

    assert context == {
        "range_context_bound": True,
        "selected_text": "rough",
        "document_prefix": "A ",
        "document_suffix": " sentence.",
        "document_text": "A rough sentence.",
    }


def test_context_snapshot_retries_focus_binding_after_browser_copy_exposes_selection(monkeypatch):
    tokens = iter((0, 0, 17))
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Page - Google Chrome", "process_name": "chrome.exe", "pid": 8},
    )
    monkeypatch.setattr(native_host, "_screen_size", lambda: {"width": 1920, "height": 1080})
    monkeypatch.setattr(native_host, "_capture_focus", lambda: next(tokens))
    monkeypatch.setattr(
        native_host,
        "_selected_text_and_stale",
        lambda **_kwargs: ("rough", ""),
    )
    monkeypatch.setattr(native_host, "_win_cursor_screen_rect", lambda: {})

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=True,
        capture_focus=True,
    )

    assert snapshot["selected_text"] == "rough"
    assert snapshot["focus_token"] == 17


def test_windows_wordpad_capture_prefers_exact_native_edit_range(monkeypatch):
    snapshot = {
        "input_hwnd": 501,
        "root_hwnd": 500,
        "target_pid": 77,
        "class_name": "RICHEDIT50W",
        "selection_start": 2,
        "selection_end": 7,
        "selected_text": "rough",
        "document_prefix": "A ",
        "document_suffix": " sentence.",
        "document_text": "A rough sentence.",
        "range_context_bound": True,
    }
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "_focus_seq", 40)
    monkeypatch.setattr(native_host, "_focus_cache", {})
    monkeypatch.setattr(native_host, "_win_edit_control_snapshot", lambda: dict(snapshot))
    monkeypatch.setattr(
        native_host,
        "_win_uia_capture_focus",
        lambda: pytest.fail("WordPad must use its exact RichEdit range before UIA"),
    )

    token = native_host._capture_focus()

    assert token == 41
    assert native_host._focus_cache["kind"] == "win-edit"
    assert native_host._focus_cache["class_name"] == "RICHEDIT50W"
    assert native_host._focus_cache["selected_text"] == "rough"


def test_windows_modern_notepad_d2d_control_uses_verified_uia_path() -> None:
    assert native_host._win_direct_edit_class_supported("RICHEDIT50W") is True
    assert native_host._win_direct_edit_class_supported("RichEdit20W") is True
    assert native_host._win_direct_edit_class_supported("Edit") is True
    assert native_host._win_direct_edit_class_supported("RichEditD2DPT") is False


def test_windows_wordpad_rich_edit_replaces_and_verifies_without_focus_or_clipboard(monkeypatch):
    import ctypes as real_ctypes
    import types

    state = {"document": "A rough sentence.", "replaced": False}
    cached = {
        "token": 41,
        "kind": "win-edit",
        "input_hwnd": 501,
        "root_hwnd": 500,
        "target_pid": 77,
        "class_name": "RICHEDIT50W",
        "selection_start": 2,
        "selection_end": 7,
        "selected_text": "rough",
        "document_prefix": "A ",
        "document_suffix": " sentence.",
        "document_text": "A rough sentence.",
        "range_context_bound": True,
    }

    def snapshot(_hwnd=0, *, require_selection=True):
        del require_selection
        if not state["replaced"]:
            return dict(cached)
        return {
            **cached,
            "selection_start": 7,
            "selection_end": 7,
            "selected_text": "",
            "document_prefix": "A clear",
            "document_suffix": " sentence.",
            "document_text": state["document"],
        }

    class User32:
        @staticmethod
        def GetForegroundWindow():
            return 900

        @staticmethod
        def SendMessageW(_hwnd, message, _wparam, lparam):
            if message == native_host._EM_REPLACESEL:
                state["document"] = f"A {lparam.value} sentence."
                state["replaced"] = True
            return 1

    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "_focus_cache", cached)
    monkeypatch.setattr(native_host, "_win_edit_control_snapshot", snapshot)
    monkeypatch.setattr(
        native_host,
        "ctypes",
        types.SimpleNamespace(
            windll=types.SimpleNamespace(user32=User32()),
            c_wchar_p=real_ctypes.c_wchar_p,
        ),
    )

    result = native_host.paste_text(
        "clear",
        target_pid=77,
        focus_token=41,
        restore_clipboard=True,
    )

    assert result["ok"] is True, result
    assert result["method"] == "win-richedit"
    assert result["text_verified"] is True
    assert result["clipboard_restored"] is True
    assert result["foreground_unchanged"] is True
    assert state["document"] == "A clear sentence."


def test_windows_wordpad_reselects_captured_range_after_user_clicks_away(monkeypatch):
    import ctypes as real_ctypes
    import types

    state = {"document": "A rough sentence.", "replaced": False}
    cached = {
        "token": 42,
        "kind": "win-edit",
        "input_hwnd": 501,
        "root_hwnd": 500,
        "target_pid": 77,
        "class_name": "RICHEDIT50W",
        "selection_start": 2,
        "selection_end": 7,
        "selected_text": "rough",
        "document_prefix": "A ",
        "document_suffix": " sentence.",
        "document_text": "A rough sentence.",
        "range_context_bound": True,
    }
    current = {
        **cached,
        # The document is unchanged, but the user clicked at its end while the
        # model was working. Rewrite must use the captured offsets, not this
        # new caret.
        "selection_start": 17,
        "selection_end": 17,
        "selected_text": "",
        "document_prefix": "A rough sentence.",
        "document_suffix": "",
    }
    selected_messages: list[tuple[int, int]] = []

    def snapshot(_hwnd=0, *, require_selection=True):
        del require_selection
        if not state["replaced"]:
            return dict(current)
        return {**current, "document_text": state["document"]}

    class User32:
        @staticmethod
        def GetForegroundWindow():
            return 900

        @staticmethod
        def SendMessageW(_hwnd, message, wparam, lparam):
            if message == native_host._EM_SETSEL:
                selected_messages.append((int(wparam), int(lparam)))
            elif message == native_host._EM_REPLACESEL:
                state["document"] = f"A {lparam.value} sentence."
                state["replaced"] = True
            return 1

    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "_focus_cache", cached)
    monkeypatch.setattr(native_host, "_win_edit_control_snapshot", snapshot)
    monkeypatch.setattr(
        native_host,
        "ctypes",
        types.SimpleNamespace(
            windll=types.SimpleNamespace(user32=User32()),
            c_wchar_p=real_ctypes.c_wchar_p,
        ),
    )

    result = native_host.paste_text("clear", target_pid=77, focus_token=42)

    assert result["ok"] is True, result
    assert selected_messages == [(2, 7)]
    assert state["document"] == "A clear sentence."


def test_windows_uia_browser_fallback_focuses_pastes_verifies_and_restores(monkeypatch):
    import types

    comtypes_module = types.ModuleType("comtypes")
    comtypes_gen_module = types.ModuleType("comtypes.gen")
    uiac_module = types.ModuleType("comtypes.gen.UIAutomationClient")
    uiac_module.IUIAutomationTextPattern = object()
    comtypes_module.gen = comtypes_gen_module
    comtypes_gen_module.UIAutomationClient = uiac_module
    monkeypatch.setitem(sys.modules, "comtypes", comtypes_module)
    monkeypatch.setitem(sys.modules, "comtypes.gen", comtypes_gen_module)
    monkeypatch.setitem(sys.modules, "comtypes.gen.UIAutomationClient", uiac_module)

    state = {"document": "A rough sentence.", "foreground": 800, "clipboard": "keep me"}
    focus_events: list[int | str] = []

    class DocumentRange:
        def GetText(self, _limit):
            return state["document"]

    class Pattern:
        def __init__(self):
            self.DocumentRange = DocumentRange()

    class RawPattern:
        def QueryInterface(self, _interface):
            return Pattern()

    class Element:
        def GetCurrentPattern(self, _pattern_id):
            return RawPattern()

        def SetFocus(self):
            focus_events.append("element")

    class TextRange:
        def GetText(self, _limit):
            return "rough"

        def Select(self):
            focus_events.append("selection")

    class User32:
        @staticmethod
        def GetForegroundWindow():
            return state["foreground"]

    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(
        native_host,
        "ctypes",
        types.SimpleNamespace(windll=types.SimpleNamespace(user32=User32())),
    )
    monkeypatch.setattr(
        native_host,
        "_focus_cache",
        {
            "token": 41,
            "kind": "win-uia",
            "element": Element(),
            "range": TextRange(),
            "root_hwnd": 700,
            "target_pid": 71,
            "range_context_bound": True,
            "selected_text": "rough",
            "document_prefix": "A ",
            "document_suffix": " sentence.",
        },
    )
    monkeypatch.setattr(
        native_host,
        "_win_post_text_to_cached_target",
        lambda *_args, **_kwargs: pytest.fail("partial WM_CHAR replacement must never run"),
    )

    def restore_foreground(hwnd: int) -> bool:
        state["foreground"] = hwnd
        focus_events.append(hwnd)
        return True

    monkeypatch.setattr(native_host, "_win_restore_foreground", restore_foreground)
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": state["clipboard"]})

    def clipboard_set(text=""):
        state["clipboard"] = text
        return {"ok": True}

    monkeypatch.setattr(native_host, "clipboard_set", clipboard_set)
    monkeypatch.setattr(native_host.time, "sleep", lambda _seconds: None)
    import core.platform_utils as platform_utils

    monkeypatch.setattr(
        platform_utils,
        "send_keys",
        lambda combo: state.update(document="A two words sentence.") if combo == "ctrl+v" else None,
    )

    result = native_host._win_uia_apply_selected_text(
        41,
        "two words",
        paste_combo="ctrl+v",
        restore_clipboard=True,
    )

    assert result["ok"] is True, result
    assert result["method"] == "win-uia-foreground-paste"
    assert result["text_verified"] is True
    assert result["clipboard_restored"] is True
    assert result["focus_restored"] is True
    assert state == {"document": "A two words sentence.", "foreground": 800, "clipboard": "keep me"}
    assert focus_events == ["selection", "element", 700, 800]


def test_paste_text_refuses_windows_unanchored_fallback_when_focus_token_fails(monkeypatch):
    """Verify failed anchored paste-back does not paste into the current caret."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", True)

    import core.platform_utils as platform_utils

    monkeypatch.setattr(
        native_host,
        "_win_uia_apply_selected_text",
        lambda *_args, **_kwargs: {"ok": False, "method": "uia-range", "error": "stale range"},
    )
    monkeypatch.setattr(platform_utils, "set_foreground_window", lambda _wid: pytest.fail("unanchored focus used"))
    monkeypatch.setattr(platform_utils, "send_keys", lambda _combo: pytest.fail("unanchored paste used"))
    monkeypatch.setattr(native_host, "clipboard_set", lambda _text="": pytest.fail("unanchored clipboard used"))

    result = native_host.paste_text("model reply", target_pid=777, focus_token=9)

    assert result["ok"] is False
    assert result["method"] == "uia-range"
    assert result["error"] == "stale range"
    assert result["confirmed"] is False
    assert result["keystroke_sent"] is False


def test_paste_text_refuses_unconfirmed_windows_target_without_focus_token(monkeypatch):
    """A rejected SetForegroundWindow call must never become a false success."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", True)

    import core.platform_utils as platform_utils

    monkeypatch.setattr(platform_utils, "set_foreground_window", lambda _wid: None)
    monkeypatch.setattr(platform_utils, "get_foreground_window", lambda: 9009)
    monkeypatch.setattr(native_host, "_win_window_pid", lambda hwnd: {7007: 777, 9009: 999}.get(hwnd, 0))
    monkeypatch.setattr(platform_utils, "send_keys", lambda _combo: pytest.fail("paste sent to wrong app"))
    monkeypatch.setattr(native_host, "clipboard_set", lambda _text="": pytest.fail("clipboard overwritten"))
    monkeypatch.setattr(native_host.time, "sleep", lambda _seconds: None)

    result = native_host.paste_text("model reply", target_pid=7007, focus_token=0)

    assert result["ok"] is False
    assert result["confirmed"] is False
    assert result["keystroke_sent"] is False
    assert result["frontmost_pid"] == 999
    assert result["error"] == "target window could not be confirmed"


def test_paste_failure_matrix_is_controlled_at_native_boundary(monkeypatch):
    """Clipboard, focus, synthetic-input, overwrite, and AX faults stay in band."""
    import core.platform_utils as platform_utils

    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": "original"})
    monkeypatch.setattr(platform_utils, "set_foreground_window", lambda _wid: None)
    monkeypatch.setattr(platform_utils, "get_foreground_window", lambda: 777)

    sent: list[str] = []
    monkeypatch.setattr(platform_utils, "send_keys", sent.append)
    monkeypatch.setattr(native_host, "clipboard_set", lambda _text="": {"ok": False})
    locked = native_host.paste_text("reply", target_pid=777)
    assert locked["ok"] is False
    assert locked["clipboard_ok"] is False
    assert sent == []

    monkeypatch.setattr(native_host, "clipboard_set", lambda _text="": {"ok": True})

    def focus_moved(_wid):
        raise OSError("focus changed before completion")

    monkeypatch.setattr(platform_utils, "set_foreground_window", focus_moved)
    moved = native_host.paste_text("reply", target_pid=777)
    assert moved["ok"] is False
    assert "focus changed" in moved["error"]

    monkeypatch.setattr(platform_utils, "set_foreground_window", lambda _wid: None)

    def synthetic_blocked(_combo):
        raise PermissionError("target blocks synthetic input")

    monkeypatch.setattr(platform_utils, "send_keys", synthetic_blocked)
    blocked = native_host.paste_text("reply", target_pid=777)
    assert blocked["ok"] is False
    assert "target blocks synthetic input" in blocked["error"]

    monkeypatch.setattr(platform_utils, "send_keys", lambda _combo: None)
    writes = iter(({"ok": True}, {"ok": False}))
    monkeypatch.setattr(native_host, "clipboard_set", lambda _text="": next(writes))
    overwritten = native_host.paste_text("reply", target_pid=777, restore_clipboard=True)
    assert overwritten["ok"] is True
    assert overwritten["clipboard_restored"] is False

    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_win_uia_apply_selected_text",
        lambda *_args, **_kwargs: {
            "ok": False,
            "method": "uia-range",
            "error": "accessibility permission missing",
        },
    )
    inaccessible = native_host.paste_text("reply", target_pid=777, focus_token=9)
    assert inaccessible["ok"] is False
    assert inaccessible["confirmed"] is False
    assert inaccessible["keystroke_sent"] is False
    assert "accessibility permission missing" in inaccessible["error"]


def test_context_snapshot_explorer_selection_uses_paths_not_text(monkeypatch):
    """Verify Explorer selection capture uses file paths directly."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Explorer", "pid": 42, "window_id": 777, "bundle_id": ""},
    )
    monkeypatch.setattr(native_host, "_screen_size", lambda: {"width": 0, "height": 0})
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": "previous clipboard"})
    monkeypatch.setattr(native_host, "selected_paths", lambda: [r"C:\Users\sunny\Desktop\note.txt"])
    calls: list[tuple[bool, int | None, bool]] = []

    def fake_selected_text(
        *,
        allow_clipboard_fallback: bool = True,
        active_pid: int | None = None,
        require_active_owner: bool = False,
        selection_dedupe_key: str = "",
    ) -> tuple[str, str]:
        calls.append((allow_clipboard_fallback, active_pid, require_active_owner))
        return "", ""

    monkeypatch.setattr(native_host, "_selected_text_and_stale", fake_selected_text)

    snapshot = native_host.context_snapshot(
        include_clipboard=True,
        include_selection=True,
        include_selected_paths=True,
    )

    assert calls == []
    assert snapshot["selected_paths"] == [r"C:\Users\sunny\Desktop\note.txt"]
    assert snapshot["selected_text"] == ""
    assert snapshot["clipboard_text"] == "previous clipboard"


def test_context_snapshot_linux_file_manager_selection_uses_paths_not_text(monkeypatch):
    """Verify Linux file manager selection capture uses file paths directly."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Files", "process_name": "nautilus", "pid": 42, "window_id": 777},
    )
    monkeypatch.setattr(native_host, "_screen_size", lambda: {"width": 0, "height": 0})
    monkeypatch.setattr(native_host, "selected_paths", lambda: ["/home/sunny/repo/note.txt"])
    monkeypatch.setattr(
        native_host,
        "_selected_text_and_stale",
        lambda **_kwargs: pytest.fail("file managers should not read PRIMARY text"),
    )

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=True,
        include_selected_paths=True,
    )

    assert snapshot["selected_paths"] == ["/home/sunny/repo/note.txt"]
    assert snapshot["selected_text"] == ""


def test_linux_selected_paths_reads_uri_list(monkeypatch):
    """Verify Linux selected file capture parses file-manager URI clipboard data."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": "previous clipboard"})
    restored: list[str] = []
    monkeypatch.setattr(native_host, "clipboard_set", lambda text="": restored.append(text) or {"ok": True})
    monkeypatch.setattr(native_host.time, "sleep", lambda _seconds: None)

    import core.platform_utils as platform_utils

    copied: list[str] = []
    monkeypatch.setattr(platform_utils, "send_keys", lambda combo: copied.append(combo))

    class Result:
        returncode = 0
        stdout = "copy\nfile:///home/sunny/repo/hello%20there.md\n# ignored\nfile:///tmp/notes.txt\n"

    seen_commands: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        seen_commands.append(list(cmd))
        return Result()

    monkeypatch.setattr(native_host.subprocess, "run", fake_run)

    assert native_host.selected_paths() == [
        "/home/sunny/repo/hello there.md",
        "/tmp/notes.txt",
    ]
    assert copied == [platform_utils.COPY_COMBO]
    assert restored == ["previous clipboard"]
    assert seen_commands[0][4] == "x-special/gnome-copied-files"


def test_context_snapshot_text_app_selection_uses_text_not_paths(monkeypatch):
    """Verify non-shell selection capture uses selected text directly."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Untitled - Notepad", "process_name": "notepad.exe", "pid": 42, "window_id": 777},
    )
    monkeypatch.setattr(native_host, "_screen_size", lambda: {"width": 0, "height": 0})
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": "clipboard text"})
    monkeypatch.setattr(native_host, "selected_paths", lambda: pytest.fail("text app should not read paths"))
    calls: list[tuple[bool, int | None, bool]] = []

    def fake_selected_text(
        *,
        allow_clipboard_fallback: bool = True,
        active_pid: int | None = None,
        require_active_owner: bool = False,
        selection_dedupe_key: str = "",
    ) -> tuple[str, str]:
        calls.append((allow_clipboard_fallback, active_pid, require_active_owner))
        return "selected text", ""

    monkeypatch.setattr(native_host, "_selected_text_and_stale", fake_selected_text)

    snapshot = native_host.context_snapshot(
        include_clipboard=True,
        include_selection=True,
        include_selected_paths=True,
    )

    assert calls == [(True, 42, True)]
    assert snapshot["selected_text"] == "selected text"
    assert snapshot["selected_paths"] == []
    assert snapshot["clipboard_text"] == "clipboard text"


def test_context_snapshot_skips_text_focus_capture_for_calc(monkeypatch):
    """Calc cells use the structured action API and must not enter UIA paste-back."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {
            "name": "Budget.ods — LibreOffice Calc",
            "process_name": "soffice.bin",
            "pid": 42,
            "window_id": 777,
        },
    )
    monkeypatch.setattr(native_host, "_screen_size", lambda: {"width": 0, "height": 0})
    monkeypatch.setattr(native_host, "_capture_focus", lambda: pytest.fail("Calc must not capture text focus"))

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=True,
        capture_focus=True,
    )

    assert snapshot["focus_token"] == 0
    assert snapshot["app_selection_deferred"] is True


def test_context_snapshot_wayland_includes_active_window_accessible_text(monkeypatch):
    """Wayland can attach unselected active-window text without clipboard reads."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    monkeypatch.setattr(native_host, "_active_app", lambda: {"name": "Editor", "pid": 42})
    monkeypatch.setattr(native_host, "_screen_size", lambda: {"width": 0, "height": 0})
    monkeypatch.setattr(linux_atspi, "get_active_window_text", lambda: "unselected editor content")

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_active_window_text=True,
    )

    assert snapshot["active_window_text"] == "unselected editor content"


def test_context_snapshot_does_not_read_active_window_text_off_wayland(monkeypatch):
    """The accessibility-content path remains exclusive to Wayland sessions."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(native_host, "_active_app", lambda: {"name": "Editor", "pid": 42})
    monkeypatch.setattr(native_host, "_screen_size", lambda: {"width": 0, "height": 0})
    monkeypatch.setattr(
        linux_atspi,
        "get_active_window_text",
        lambda: pytest.fail("AT-SPI content read attempted off Wayland"),
    )

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_active_window_text=True,
    )

    assert snapshot["active_window_text"] == ""


def test_await_selection_context_disables_active_owner_requirement(monkeypatch):
    """Verify explicit Selection capture can use the interactive selection gesture."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Editor", "process_name": "editor", "pid": 42, "window_id": 777},
    )
    monkeypatch.setattr(native_host, "_screen_size", lambda: {"width": 0, "height": 0})
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": ""})
    monkeypatch.setattr(native_host, "selected_paths", lambda: [])
    calls: list[tuple[bool, int | None, bool]] = []

    def fake_selected_text(
        *,
        allow_clipboard_fallback: bool = True,
        active_pid: int | None = None,
        require_active_owner: bool = False,
        selection_dedupe_key: str = "",
    ) -> tuple[str, str]:
        calls.append((allow_clipboard_fallback, active_pid, require_active_owner))
        return "picked text", ""

    monkeypatch.setattr(native_host, "_selected_text_and_stale", fake_selected_text)

    snapshot = native_host.await_selection_context(
        timeout=0.5,
        settle_ms=0,
        include_clipboard=False,
        include_selected_paths=False,
    )

    assert calls == [(True, 42, False)]
    assert snapshot["selected_text"] == "picked text"


def test_context_snapshot_forwards_selection_dedupe_key(monkeypatch):
    """Verify the intent surface's dedupe key reaches the selection reader."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Editor", "process_name": "editor", "pid": 42, "window_id": 777},
    )
    monkeypatch.setattr(native_host, "_screen_size", lambda: {"width": 0, "height": 0})
    seen_keys: list[str] = []

    def fake_selected_text(*, selection_dedupe_key: str = "", **_kwargs) -> tuple[str, str]:
        seen_keys.append(selection_dedupe_key)
        return "", ""

    monkeypatch.setattr(native_host, "_selected_text_and_stale", fake_selected_text)

    native_host.context_snapshot(
        include_clipboard=False, include_selection=True, selection_dedupe_key="intent"
    )
    native_host.context_snapshot(include_clipboard=False, include_selection=True)

    assert seen_keys == ["intent", ""]


def test_context_snapshot_surfaces_stale_selection_for_intent(monkeypatch):
    """Verify a repeated PRIMARY acquisition reaches the snapshot as stale text."""
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Editor", "process_name": "editor", "pid": 42, "window_id": 777},
    )
    monkeypatch.setattr(native_host, "_screen_size", lambda: {"width": 0, "height": 0})
    monkeypatch.setattr(
        native_host,
        "_selected_text_and_stale",
        lambda **_kwargs: ("", "earlier text"),
    )

    snapshot = native_host.context_snapshot(
        include_clipboard=False, include_selection=True, selection_dedupe_key="intent"
    )

    assert snapshot["selected_text"] == ""
    assert snapshot["stale_selected_text"] == "earlier text"


def test_selected_text_dedupes_repeated_x11_primary_acquisition(monkeypatch):
    """Verify one PRIMARY acquisition auto-fills once, then turns stale."""
    import core.capture as capture

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "_AUTOFILLED_PRIMARY_SELECTIONS", {})
    monkeypatch.setattr(capture, "_get_primary_selection_linux", lambda **_kw: "picked text")
    monkeypatch.setattr(
        capture,
        "_get_selected_text_clipboard",
        lambda: pytest.fail("stale capture must not synthesize Ctrl+C"),
    )
    identities = [(777, 100, "digest"), (777, 100, "digest"), (777, 200, "digest")]
    monkeypatch.setattr(native_host, "_primary_selection_identity", lambda _text: identities.pop(0))

    kwargs = {"active_pid": 42, "require_active_owner": True, "selection_dedupe_key": "intent"}
    assert native_host._selected_text_and_stale(**kwargs) == ("picked text", "")
    assert native_host._selected_text_and_stale(**kwargs) == ("", "picked text")
    assert native_host._selected_text_and_stale(**kwargs) == ("picked text", "")


def test_selected_text_dedupe_fails_open_without_identity(monkeypatch):
    import core.capture as capture

    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "_AUTOFILLED_PRIMARY_SELECTIONS", {})
    monkeypatch.setattr(capture, "_get_primary_selection_linux", lambda **_kw: "picked text")
    monkeypatch.setattr(native_host, "_primary_selection_identity", lambda _text: None)

    kwargs = {"active_pid": 42, "require_active_owner": True, "selection_dedupe_key": "intent"}
    assert native_host._selected_text_and_stale(**kwargs) == ("picked text", "")
    assert native_host._selected_text_and_stale(**kwargs) == ("picked text", "")
    assert native_host._AUTOFILLED_PRIMARY_SELECTIONS == {}


def test_selected_text_without_dedupe_key_skips_identity_lookup(monkeypatch):
    """Verify non-intent flows never pay for or record selection identity."""
    import core.capture as capture

    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "_AUTOFILLED_PRIMARY_SELECTIONS", {})
    monkeypatch.setattr(capture, "_get_primary_selection_linux", lambda **_kw: "picked text")
    monkeypatch.setattr(
        native_host,
        "_primary_selection_identity",
        lambda _text: pytest.fail("identity lookup must be skipped without a dedupe key"),
    )

    kwargs = {"active_pid": 42, "require_active_owner": True}
    assert native_host.selected_text(**kwargs) == "picked text"
    assert native_host._selected_text_and_stale(**kwargs) == ("picked text", "")


def test_selection_capture_failure_matrix_returns_no_stale_text(monkeypatch):
    """Selection absence, focus, accessibility, permission, app, and backend faults fail closed."""
    import core.capture as capture_module

    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(capture_module, "_get_selected_text_uia", lambda: "")
    assert native_host._selected_text_and_stale(
        allow_clipboard_fallback=False,
        require_active_owner=True,
    ) == ("", "")

    monkeypatch.setattr(
        capture_module,
        "_get_selected_text_uia",
        lambda: (_ for _ in ()).throw(PermissionError("OS accessibility permission missing")),
    )
    assert native_host._selected_text_and_stale(
        allow_clipboard_fallback=False,
        require_active_owner=True,
    ) == ("", "")

    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(
        capture_module,
        "_get_primary_selection_linux",
        lambda **_kwargs: (_ for _ in ()).throw(NotImplementedError("platform backend unsupported")),
    )
    assert native_host._selected_text_and_stale(
        allow_clipboard_fallback=False,
        require_active_owner=True,
    ) == ("", "")

    monkeypatch.setattr(native_host, "_active_app", lambda: {"name": "Unsupported", "pid": 99})
    monkeypatch.setattr(native_host, "_screen_size", lambda: {"width": 0, "height": 0})
    monkeypatch.setattr(native_host, "_capture_focus", lambda: 0)
    monkeypatch.setattr(native_host, "_selection_source_kind", lambda _active: "text")
    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=True,
        capture_focus=True,
    )
    assert snapshot["focus_token"] == 0
    assert snapshot["selected_text"] == ""
    assert snapshot["stale_selected_text"] == ""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native context behavior is tested on Windows")
def test_win_context_window_skips_openwand_foreground(monkeypatch):
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "_win_is_external_context_window", lambda hwnd: hwnd == 777)
    monkeypatch.setattr(native_host, "_win_find_external_context_window", lambda _hwnd: 777)
    monkeypatch.setattr(native_host, "_win_window_title", lambda hwnd: "OpenWand" if hwnd == 111 else "Chrome")

    assert native_host._win_context_window_id(111) == 777


def test_win_openwand_window_detection_uses_supervisor_process_tree(monkeypatch):
    """OpenWand-owned helper windows must not become foreground context targets."""
    monkeypatch.setenv("OPENWAND_SUPERVISOR_PID", "100")
    monkeypatch.setattr(native_host, "_win_window_pid", lambda _hwnd: 300)
    monkeypatch.setattr(native_host, "_win_window_title", lambda _hwnd: "AI Assistant Icon - OpenWand")
    monkeypatch.setattr(native_host, "_win_process_name", lambda _pid: "python.exe")

    class Process:
        def __init__(self, pid):
            assert pid == 300

        @staticmethod
        def parents():
            return [type("Parent", (), {"pid": 200})(), type("Parent", (), {"pid": 100})()]

    monkeypatch.setattr("psutil.Process", Process)

    assert native_host._win_is_own_window_pid(300) is True
    assert native_host._win_is_openwand_ui_window(999) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native context behavior is tested on Windows")
def test_context_snapshot_reads_browser_url_from_corrected_window(monkeypatch):
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(context_fetcher, "_IS_WIN", True)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", False)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Chrome", "pid": 42, "window_id": 777, "bundle_id": ""},
    )
    monkeypatch.setattr(native_host, "_selected_text_and_stale", lambda **_kwargs: ("", ""))
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": ""})

    calls: list[int] = []

    def fake_fetch_window(hwnd: int):
        calls.append(hwnd)
        return context_fetcher.WindowInfo(
            title="Example",
            process_name="chrome.exe",
            url="https://example.test/page",
            hwnd=hwnd,
        )

    monkeypatch.setattr(context_fetcher, "_fetch_window_info_win", fake_fetch_window)
    # Keep this focused-window contract independent from whatever browser
    # windows happen to be open on the hosted Windows runner. Multi-window
    # enumeration has its own deterministic test below.
    monkeypatch.setattr(context_fetcher, "_find_visible_browser_windows_win", lambda: [])

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )

    assert calls == [777]
    assert snapshot["browser_url"] == "https://example.test/page"
    assert snapshot["browser_hwnd"] == 777


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native context behavior is tested on Windows")
def test_context_snapshot_reads_background_browser_when_foreground_is_document(monkeypatch):
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(context_fetcher, "_IS_WIN", True)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", False)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Untitled 1 \u2014 LibreOffice Calc", "pid": 42, "window_id": 111, "bundle_id": ""},
    )
    monkeypatch.setattr(native_host, "_selected_text_and_stale", lambda **_kwargs: ("", ""))
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": ""})

    background_browser = context_fetcher.WindowInfo(
        title="Example - Chrome",
        process_name="chrome.exe",
        url="https://example.test/page",
        hwnd=777,
    )
    monkeypatch.setattr(
        context_fetcher,
        "get_browser_windows_for_context",
        lambda preferred_hwnd=0: [background_browser],
    )

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )

    assert snapshot["browser_url"] == "https://example.test/page"
    assert snapshot["browser_hwnd"] == 777
    assert snapshot["debug"]["browser_window"]["process_name"] == "chrome.exe"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native context behavior is tested on Windows")
def test_context_snapshot_keeps_each_visible_browser_as_a_separate_page(monkeypatch):
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(context_fetcher, "_IS_WIN", True)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", False)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Notepad", "pid": 42, "window_id": 111, "bundle_id": ""},
    )
    monkeypatch.setattr(native_host, "_selected_text_and_stale", lambda **_kwargs: ("", ""))
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": ""})
    windows = [
        context_fetcher.WindowInfo(
            title="Guide.pdf - Google Chrome",
            process_name="chrome.exe",
            url="file:///C:/Docs/Guide.pdf",
            hwnd=701,
        ),
        context_fetcher.WindowInfo(
            title="Project site - Microsoft Edge",
            process_name="msedge.exe",
            url="https://example.test/project",
            hwnd=702,
        ),
    ]
    monkeypatch.setattr(
        context_fetcher,
        "get_browser_windows_for_context",
        lambda preferred_hwnd=0: windows,
    )

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )

    assert [page["process_name"] for page in snapshot["browser_pages"]] == [
        "chrome.exe",
        "msedge.exe",
    ]
    assert [page["hwnd"] for page in snapshot["browser_pages"]] == [701, 702]
    assert snapshot["browser_url"] == "file:///C:/Docs/Guide.pdf"
    assert len(snapshot["debug"]["browser_windows"]) == 2


def test_linux_context_snapshot_keeps_browser_hwnd_for_deferred_read(monkeypatch):
    """Verify Linux snapshots keep the hotkey-time browser window id.

    On Linux the snapshot itself carries no URL (that is resolved later via
    AT-SPI2 by the deferred page read); the contract here is that the browser
    window id survives into browser_hwnd/debug so the deferred read has a
    target, and that a URL is passed through untouched if one ever appears.
    """
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(context_fetcher, "_IS_WIN", False)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", False)
    monkeypatch.setattr(context_fetcher, "_BROWSER_PROCS", {"firefox"})
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Example - Firefox", "process_name": "firefox", "pid": 42, "window_id": 777},
    )
    monkeypatch.setattr(native_host, "_selected_text_and_stale", lambda **_kwargs: ("", ""))
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": ""})

    calls: list[int] = []

    def fake_browser_window(preferred_hwnd: int = 0):
        calls.append(preferred_hwnd)
        # Real Linux capture: title/pid/hwnd only, no URL at snapshot time.
        return context_fetcher.WindowInfo(
            title="Example - Firefox",
            process_name="firefox",
            pid=42,
            hwnd=777,
        )

    monkeypatch.setattr(
        context_fetcher,
        "get_browser_windows_for_context",
        lambda preferred_hwnd=0: [fake_browser_window(preferred_hwnd)],
    )

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )

    assert calls == [777]
    assert snapshot["browser_url"] == ""
    assert snapshot["browser_hwnd"] == 777
    assert snapshot["debug"]["browser_window"]["process_name"] == "firefox"
    assert snapshot["debug"]["browser_window"]["hwnd"] == 777


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS native context behavior is tested on macOS")
def test_macos_context_snapshot_separates_document_and_browser(monkeypatch):
    """Verify macOS snapshots collect front document and browser independently."""
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "IS_MAC", True)
    monkeypatch.setattr(context_fetcher, "_IS_WIN", False)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", True)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "TextEdit", "pid": 101, "bundle_id": "com.apple.TextEdit"},
    )
    monkeypatch.setattr(native_host, "_selected_text_and_stale", lambda **_kwargs: ("", ""))
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": ""})

    rows = [
        {"process_name": "TextEdit", "pid": 101, "frontmost": True, "title": "Notes.txt"},
        {"process_name": "Safari", "pid": 303, "frontmost": False, "title": "Example Page"},
    ]
    monkeypatch.setattr("core.platform.macos_native.list_document_windows", lambda: rows)
    monkeypatch.setattr(context_fetcher, "_mac_browser_url", lambda _app: "https://example.test/page")

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )

    assert snapshot["document_window"] == {
        "title": "Notes.txt",
        "process_name": "TextEdit",
        "pid": 101,
        "window_id": 0,
    }
    assert snapshot["browser_app"] == "Safari"
    assert snapshot["browser_url"] == "https://example.test/page"
    assert snapshot["debug"]["browser_window"]["process_name"] == "Safari"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux native context behavior is tested on Linux")
def test_linux_active_app_includes_real_process_name(monkeypatch):
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "IS_MAC", False)

    import core.platform_utils as platform_utils

    monkeypatch.setattr(platform_utils, "get_foreground_window", lambda: 144703501)
    monkeypatch.setattr(platform_utils, "get_window_title", lambda _wid: "Summary.txt \u2014 KWrite")
    monkeypatch.setattr(platform_utils, "get_window_pid", lambda _wid: 1651464)

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "kwrite"

    psutil = pytest.importorskip("psutil")

    monkeypatch.setattr(psutil, "Process", FakeProcess)

    active = native_host._active_app()

    assert active["name"] == "Summary.txt \u2014 KWrite"
    assert active["process_name"] == "kwrite"
    assert active["pid"] == 1651464
    assert native_host._last_context_window_debug["raw_process"] == "kwrite"


def test_linux_active_app_skips_openwand_own_window(monkeypatch):
    """Verify the Linux active-app lookup corrects past OpenWand's own overlay window."""
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "IS_MAC", False)

    import core.platform_utils as platform_utils

    titles = {111: "AI Assistant Icon \u2014 OpenWand", 222: "Example \u2014 Mozilla Firefox"}
    pids = {111: 999, 222: 1234}
    monkeypatch.setattr(platform_utils, "get_foreground_window", lambda: 111)
    monkeypatch.setattr(platform_utils, "get_window_title", lambda wid: titles.get(wid, ""))
    monkeypatch.setattr(platform_utils, "get_window_pid", lambda wid: pids.get(wid, 0))
    monkeypatch.setattr(platform_utils, "list_visible_windows_stacking", lambda: [111, 222, 333])
    monkeypatch.setattr(native_host, "_linux_is_own_window_pid", lambda pid: pid == 999)

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return {999: "python", 1234: "firefox"}.get(self.pid, "")

    psutil = pytest.importorskip("psutil")
    monkeypatch.setattr(psutil, "Process", FakeProcess)

    active = native_host._active_app()

    assert active["window_id"] == 222
    assert active["name"] == "Example \u2014 Mozilla Firefox"
    assert active["process_name"] == "firefox"
    assert active["pid"] == 1234
    debug = native_host._last_context_window_debug
    assert debug["corrected"] is True
    assert debug["raw_hwnd"] == 111
    assert debug["chosen_hwnd"] == 222


def test_linux_get_browser_window_scans_stacking(monkeypatch):
    """Verify Linux browser discovery falls back to the X11 stacking order."""
    monkeypatch.setattr(context_fetcher, "_IS_WIN", False)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", False)
    monkeypatch.setattr(context_fetcher, "_BROWSER_PROCS", {"firefox"})

    import core.platform_utils as platform_utils

    titles = {11: "Files", 22: "Example \u2014 Mozilla Firefox"}
    pids = {11: 10, 22: 20}
    monkeypatch.setattr(platform_utils, "get_window_title", lambda wid: titles.get(wid, ""))
    monkeypatch.setattr(platform_utils, "get_window_pid", lambda wid: pids.get(wid, 0))
    monkeypatch.setattr(platform_utils, "list_visible_windows_stacking", lambda: [11, 22])
    monkeypatch.setattr(
        context_fetcher,
        "_fetch_active_window",
        lambda: context_fetcher.WindowInfo(title="Files", process_name="nautilus", pid=10, hwnd=11),
    )

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return {10: "nautilus", 20: "firefox"}.get(self.pid, "")

    psutil = pytest.importorskip("psutil")
    monkeypatch.setattr(psutil, "Process", FakeProcess)

    win = context_fetcher.get_browser_window_for_context(0)

    assert win.hwnd == 22
    assert win.process_name == "firefox"
    assert win.title == "Example \u2014 Mozilla Firefox"


def test_linux_get_browser_window_prefers_hotkey_window(monkeypatch):
    """Verify the hotkey-time X11 window wins over the stacking scan."""
    monkeypatch.setattr(context_fetcher, "_IS_WIN", False)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", False)
    monkeypatch.setattr(context_fetcher, "_BROWSER_PROCS", {"firefox"})

    import core.platform_utils as platform_utils

    monkeypatch.setattr(platform_utils, "get_window_title", lambda wid: "Docs \u2014 Mozilla Firefox")
    monkeypatch.setattr(platform_utils, "get_window_pid", lambda wid: 20)
    monkeypatch.setattr(
        platform_utils,
        "list_visible_windows_stacking",
        lambda: pytest.fail("stacking scan should not run when the hotkey window is a browser"),
    )
    monkeypatch.setattr(
        context_fetcher,
        "_fetch_active_window",
        lambda: pytest.fail("active-window lookup should not run when the hotkey window is a browser"),
    )

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "firefox"

    psutil = pytest.importorskip("psutil")
    monkeypatch.setattr(psutil, "Process", FakeProcess)

    win = context_fetcher.get_browser_window_for_context(77)

    assert win.hwnd == 77
    assert win.process_name == "firefox"
