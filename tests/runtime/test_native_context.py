"""Tests for macos py test native context."""

from __future__ import annotations

import sys

import pytest

from core import context_fetcher
from runtime.workers import native_host


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
    monkeypatch.setattr(platform_utils, "send_keys", lambda combo: keys.append(combo))
    monkeypatch.setattr(native_host.time, "sleep", lambda _seconds: None)

    result = native_host.paste_text("model reply", target_pid=777, restore_clipboard=True)

    assert result["ok"] is True
    assert result["clipboard_restored"] is True
    assert focused == [777]
    assert keys == [platform_utils.PASTE_COMBO]
    assert clipboard_sets == ["model reply", "original clipboard"]


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
    """Verify unknown selection identity keeps the old auto-fill behavior."""
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native context behavior is tested on Windows")
def test_win_context_window_skips_wisp_foreground(monkeypatch):
    """Verify win context window skips wisp foreground behavior."""
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "_win_is_external_context_window", lambda hwnd: hwnd == 777)
    monkeypatch.setattr(native_host, "_win_find_external_context_window", lambda _hwnd: 777)
    monkeypatch.setattr(native_host, "_win_window_title", lambda hwnd: "Wisp" if hwnd == 111 else "Chrome")

    assert native_host._win_context_window_id(111) == 777


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native context behavior is tested on Windows")
def test_context_snapshot_reads_browser_url_from_corrected_window(monkeypatch):
    """Verify context snapshot reads browser url from corrected window behavior."""
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
        """Verify fake fetch window behavior."""
        calls.append(hwnd)
        return context_fetcher.WindowInfo(
            title="Example",
            process_name="chrome.exe",
            url="https://example.test/page",
            hwnd=hwnd,
        )

    monkeypatch.setattr(context_fetcher, "_fetch_window_info_win", fake_fetch_window)

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
    """Verify context snapshot reads background browser when foreground is document behavior."""
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
        "get_browser_window_for_context",
        lambda preferred_hwnd=0: background_browser,
    )

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )

    assert snapshot["browser_url"] == "https://example.test/page"
    assert snapshot["browser_hwnd"] == 777
    assert snapshot["debug"]["browser_window"]["process_name"] == "chrome.exe"


def test_linux_context_snapshot_reads_browser_target(monkeypatch):
    """Verify Linux snapshots capture Browser/Web before the overlay takes focus."""
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
        return context_fetcher.WindowInfo(
            title="Example - Firefox",
            process_name="firefox",
            pid=42,
            hwnd=0,
            url="https://example.test/linux",
        )

    monkeypatch.setattr(context_fetcher, "get_browser_window_for_context", fake_browser_window)

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )

    assert calls == [777]
    assert snapshot["browser_url"] == "https://example.test/linux"
    assert snapshot["browser_hwnd"] == 777


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
    """Verify linux active app includes real process name behavior."""
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "IS_MAC", False)

    import core.platform_utils as platform_utils

    monkeypatch.setattr(platform_utils, "get_foreground_window", lambda: 144703501)
    monkeypatch.setattr(platform_utils, "get_window_title", lambda _wid: "Summary.txt \u2014 KWrite")
    monkeypatch.setattr(platform_utils, "get_window_pid", lambda _wid: 1651464)

    class FakeProcess:
        """Test case for fake process behavior."""
        def __init__(self, pid):
            """Initialize the fake process instance."""
            self.pid = pid

        def name(self):
            """Verify name behavior."""
            return "kwrite"

    psutil = pytest.importorskip("psutil")

    monkeypatch.setattr(psutil, "Process", FakeProcess)

    active = native_host._active_app()

    assert active["name"] == "Summary.txt \u2014 KWrite"
    assert active["process_name"] == "kwrite"
    assert active["pid"] == 1651464
    assert native_host._last_context_window_debug["raw_process"] == "kwrite"


def test_linux_active_app_skips_wisp_own_window(monkeypatch):
    """Verify the Linux active-app lookup corrects past Wisp's own overlay window."""
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "IS_MAC", False)

    import core.platform_utils as platform_utils

    titles = {111: "AI Assistant Icon \u2014 Wisp", 222: "Example \u2014 Mozilla Firefox"}
    pids = {111: 999, 222: 1234}
    monkeypatch.setattr(platform_utils, "get_foreground_window", lambda: 111)
    monkeypatch.setattr(platform_utils, "get_window_title", lambda wid: titles.get(wid, ""))
    monkeypatch.setattr(platform_utils, "get_window_pid", lambda wid: pids.get(wid, 0))
    monkeypatch.setattr(platform_utils, "list_visible_windows_stacking", lambda: [111, 222, 333])
    monkeypatch.setattr(native_host, "_linux_is_own_window_pid", lambda pid: pid == 999)

    class FakeProcess:
        """Test case for fake process behavior."""
        def __init__(self, pid):
            """Initialize the fake process instance."""
            self.pid = pid

        def name(self):
            """Verify name behavior."""
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
        """Test case for fake process behavior."""
        def __init__(self, pid):
            """Initialize the fake process instance."""
            self.pid = pid

        def name(self):
            """Verify name behavior."""
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
        """Test case for fake process behavior."""
        def __init__(self, pid):
            """Initialize the fake process instance."""
            self.pid = pid

        def name(self):
            """Verify name behavior."""
            return "firefox"

    psutil = pytest.importorskip("psutil")
    monkeypatch.setattr(psutil, "Process", FakeProcess)

    win = context_fetcher.get_browser_window_for_context(77)

    assert win.hwnd == 77
    assert win.process_name == "firefox"
