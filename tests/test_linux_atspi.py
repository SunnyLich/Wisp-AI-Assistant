"""Tests for the Linux AT-SPI2 browser URL reader and its context wiring."""

from __future__ import annotations

from core import context_fetcher
from core.platform import linux_atspi


def test_page_title_strips_browser_suffix():
    """Verify the window-title suffix strip keeps only the page title."""
    assert linux_atspi._page_title("Example Domain — Mozilla Firefox") == "Example Domain"
    assert linux_atspi._page_title("Docs - Google Chrome") == "Docs"
    assert linux_atspi._page_title("Plain title") == "Plain title"
    assert linux_atspi._page_title("") == ""


def test_usable_url_filters_internal_pages():
    """Verify only fetchable URLs survive the filter."""
    assert linux_atspi._usable_url("https://example.test/page")
    assert linux_atspi._usable_url("file:///home/user/doc.html")
    assert not linux_atspi._usable_url("about:blank")
    assert not linux_atspi._usable_url("chrome://settings")
    assert not linux_atspi._usable_url("")


def test_choose_document_url_prefers_title_match():
    """Verify the visible tab wins over background-tab documents."""
    docs = [
        ("Background Tab", "https://example.test/background"),
        ("Example Domain", "https://example.test/visible"),
    ]
    chosen = linux_atspi._choose_document_url(docs, "Example Domain — Mozilla Firefox")
    assert chosen == "https://example.test/visible"


def test_choose_document_url_falls_back_to_first_usable():
    """Verify unusable URLs are skipped and the first fetchable one wins."""
    docs = [
        ("New Tab", "about:blank"),
        ("Somewhere", "https://example.test/first"),
        ("Elsewhere", "https://example.test/second"),
    ]
    assert linux_atspi._choose_document_url(docs, "No Match Title") == "https://example.test/first"
    assert linux_atspi._choose_document_url([("New Tab", "about:blank")], "x") == ""


def test_collect_document_urls_stops_at_documents(monkeypatch):
    """Verify the BFS finds documents and never descends into page content."""
    roles = {
        ("app", "/frame"): 23,  # frame
        ("app", "/panel"): 39,  # panel
        ("app", "/doc"): 95,  # document web
    }
    children = {
        ("app", "/frame"): [("app", "/panel")],
        ("app", "/panel"): [("app", "/doc")],
    }
    descended_into_doc: list[tuple[str, str]] = []

    monkeypatch.setattr(linux_atspi, "_get_role", lambda _c, dest, path: roles.get((dest, path), -1))

    def fake_children(_conn, dest, path):
        """Return the fake tree's children, flagging illegal document reads."""
        if (dest, path) == ("app", "/doc"):
            descended_into_doc.append((dest, path))
        return children.get((dest, path), [])

    monkeypatch.setattr(linux_atspi, "_get_children", fake_children)
    monkeypatch.setattr(linux_atspi, "_get_name", lambda _c, _d, _p: "Example Domain")
    monkeypatch.setattr(linux_atspi, "_document_url", lambda _c, _d, _p: "https://example.test/page")

    budget = linux_atspi._WalkBudget(deadline=linux_atspi.time.monotonic() + 5.0)
    docs = linux_atspi._collect_document_urls(object(), ("app", "/frame"), budget)

    assert docs == [("Example Domain", "https://example.test/page")]
    assert descended_into_doc == []


def test_get_browser_tab_url_end_to_end_selection(monkeypatch):
    """Verify the walk wires apps -> frames -> documents -> chosen URL."""
    monkeypatch.setattr(linux_atspi, "_IS_LINUX", True)
    monkeypatch.setattr(linux_atspi, "_open_a11y_connection", lambda: object())
    monkeypatch.setattr(
        linux_atspi,
        "_candidate_apps",
        lambda _conn, pid, process_name: [("app", "/root")] if pid == 4242 else [],
    )
    monkeypatch.setattr(
        linux_atspi,
        "_ordered_frames",
        lambda _conn, _app, _title: [("app", "/frame")],
    )
    monkeypatch.setattr(
        linux_atspi,
        "_collect_document_urls",
        lambda _conn, _frame, _budget: [("Example Domain", "https://example.test/page")],
    )

    url = linux_atspi.get_browser_tab_url(
        pid=4242, window_title="Example Domain — Mozilla Firefox", process_name="firefox"
    )
    assert url == "https://example.test/page"

    assert linux_atspi.get_browser_tab_url(pid=1, window_title="", process_name="") == ""


def test_get_browser_tab_url_off_linux_is_noop(monkeypatch):
    """Verify non-Linux platforms never touch the bus."""
    monkeypatch.setattr(linux_atspi, "_IS_LINUX", False)
    monkeypatch.setattr(
        linux_atspi,
        "_open_a11y_connection",
        lambda: (_ for _ in ()).throw(AssertionError("bus opened off-Linux")),
    )
    assert linux_atspi.get_browser_tab_url(pid=123) == ""


def test_selected_text_for_node_reads_all_live_ranges(monkeypatch):
    """AT-SPI text selections are resolved from ranges without clipboard use."""
    def fake_call(_conn, _dest, _path, iface, method, signature=None, body=()):
        assert iface == linux_atspi._IFACE_TEXT
        if method == "GetNSelections":
            return (2,)
        if method == "GetSelection":
            return ((2, 7) if body == (0,) else (10, 15))
        if method == "GetText":
            return ("first" if body == (2, 7) else "second",)
        raise AssertionError(method)

    monkeypatch.setattr(linux_atspi, "_call", fake_call)
    assert linux_atspi._selected_text_for_node(object(), "app", "/field") == "first\nsecond"


def test_get_selected_text_walks_accessibility_tree(monkeypatch):
    """The native Wayland selection walk returns the selected descendant."""
    monkeypatch.setattr(linux_atspi, "_IS_LINUX", True)
    monkeypatch.setattr(linux_atspi, "_open_a11y_connection", lambda: object())
    children = {
        (linux_atspi._REGISTRY_NAME, linux_atspi._ROOT_PATH): [("app", "/app")],
        ("app", "/app"): [("app", "/field")],
    }
    monkeypatch.setattr(
        linux_atspi,
        "_get_children",
        lambda _conn, dest, path: children.get((dest, path), []),
    )
    monkeypatch.setattr(
        linux_atspi,
        "_selected_text_for_node",
        lambda _conn, _dest, path: "native selection" if path == "/field" else "",
    )
    monkeypatch.setattr(
        linux_atspi,
        "_node_states",
        lambda _conn, _dest, path: {linux_atspi._STATE_FOCUSED} if path == "/field" else set(),
    )
    assert linux_atspi.get_selected_text() == "native selection"


def test_get_focused_context_returns_native_app_and_browser(monkeypatch):
    """Focused Wayland app metadata and document URL come from one AT-SPI tree."""
    monkeypatch.setattr(linux_atspi, "_IS_LINUX", True)
    monkeypatch.setattr(linux_atspi, "_open_a11y_connection", lambda: object())
    children = {
        (linux_atspi._REGISTRY_NAME, linux_atspi._ROOT_PATH): [("app", "/app")],
        ("app", "/app"): [("app", "/frame")],
        ("app", "/frame"): [("app", "/field")],
    }
    names = {"/app": "Firefox", "/frame": "Example — Firefox", "/field": "Page"}
    monkeypatch.setattr(linux_atspi, "_get_children", lambda _c, d, p: children.get((d, p), []))
    monkeypatch.setattr(linux_atspi, "_get_name", lambda _c, _d, p: names.get(p, ""))
    monkeypatch.setattr(
        linux_atspi,
        "_node_states",
        lambda _c, _d, p: {linux_atspi._STATE_FOCUSED} if p == "/field" else set(),
    )
    monkeypatch.setattr(
        linux_atspi,
        "_collect_document_urls",
        lambda _c, _frame, _budget: [("Example", "https://example.test/page")],
    )
    monkeypatch.setattr(linux_atspi, "_connection_pid", lambda _c, _d, _cache: 4242)

    context = linux_atspi.get_focused_context()
    assert context["app_name"] == "Firefox"
    assert context["window_title"] == "Example — Firefox"
    assert context["pid"] == 4242
    assert context["browser_url"] == "https://example.test/page"


def test_browser_content_linux_resolves_url_via_atspi(monkeypatch):
    """Verify the Linux page read resolves the URL, fetches it, and keeps it."""
    monkeypatch.setattr(context_fetcher, "_IS_WIN", False)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", False)

    fetched: list[str] = []
    monkeypatch.setattr(
        context_fetcher,
        "_linux_browser_tab_url",
        lambda _win: "https://example.test/atspi-resolved",
    )

    def fake_fetch(url, max_chars):
        """Record the fetched URL and return canned page text."""
        fetched.append(url)
        return "page text from the network"

    monkeypatch.setattr(context_fetcher, "_fetch_browser_content", fake_fetch)
    context_fetcher._browser_cache.pop("https://example.test/atspi-resolved", None)

    win = context_fetcher.WindowInfo(title="Example — Firefox", pid=77, hwnd=555)
    content = context_fetcher._browser_content_linux(win, max_chars=4000)

    assert content == "page text from the network"
    assert fetched == ["https://example.test/atspi-resolved"]
    assert win.url == "https://example.test/atspi-resolved"


def test_browser_content_linux_without_url_stays_empty(monkeypatch):
    """Verify no AT-SPI URL means no network fetch and empty content."""
    monkeypatch.setattr(context_fetcher, "_IS_WIN", False)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", False)
    monkeypatch.setattr(context_fetcher, "_linux_browser_tab_url", lambda _win: "")
    monkeypatch.setattr(
        context_fetcher,
        "_fetch_browser_content",
        lambda _url, _max: (_ for _ in ()).throw(AssertionError("fetched without a URL")),
    )

    win = context_fetcher.WindowInfo(title="Example — Firefox", pid=77, hwnd=555)
    assert context_fetcher._browser_content_linux(win, max_chars=4000) == ""


def test_linux_browser_tab_url_resolves_pid_and_title_from_hwnd(monkeypatch):
    """Verify the deferred path (hwnd only) recovers pid/title before the walk."""
    monkeypatch.setattr(context_fetcher, "_IS_WIN", False)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", False)

    import core.platform_utils as platform_utils

    monkeypatch.setattr(platform_utils, "get_window_pid", lambda wid: 4242 if wid == 555 else 0)
    monkeypatch.setattr(
        platform_utils, "get_window_title", lambda wid: "Example — Firefox" if wid == 555 else ""
    )

    calls: list[dict] = []

    def fake_walk(pid=0, window_title="", process_name=""):
        """Capture the resolved arguments for assertion."""
        calls.append({"pid": pid, "window_title": window_title, "process_name": process_name})
        return "https://example.test/from-hwnd"

    monkeypatch.setattr(linux_atspi, "get_browser_tab_url", fake_walk)

    win = context_fetcher.WindowInfo(hwnd=555)
    assert context_fetcher._linux_browser_tab_url(win) == "https://example.test/from-hwnd"
    assert calls == [{"pid": 4242, "window_title": "Example — Firefox", "process_name": ""}]
