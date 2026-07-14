"""Read native-Wayland context through AT-SPI2 (Linux's UIA equivalent).

Wayland has no global selected-text or active-window API.  This walks the
desktop accessibility bus the same way a screen reader would: find the active
window, read selected text from its focused tree, and locate browser document
nodes to read the DocURL attribute Firefox and Chromium publish there.

Everything is best-effort: any D-Bus hiccup, missing tree, or timeout returns
"" so context capture never blocks on accessibility plumbing. Browsers only
build their accessibility tree when assistive tech is present, so the a11y
bus IsEnabled flag is switched on once per process before the first walk;
a browser started before that flag flips may need a page reload (Chromium)
before its tree appears.
"""
from __future__ import annotations

import logging
import sys
import time

_log = logging.getLogger("wisp.linux_atspi")

_IS_LINUX = sys.platform.startswith("linux")

_A11Y_BUS_NAME = "org.a11y.Bus"
_A11Y_BUS_PATH = "/org/a11y/bus"
_REGISTRY_NAME = "org.a11y.atspi.Registry"
_ROOT_PATH = "/org/a11y/atspi/accessible/root"
_IFACE_ACCESSIBLE = "org.a11y.atspi.Accessible"
_IFACE_DOCUMENT = "org.a11y.atspi.Document"
_IFACE_TEXT = "org.a11y.atspi.Text"
_IFACE_PROPERTIES = "org.freedesktop.DBus.Properties"

# atspi-constants.h role numbers (stable protocol values).
_ROLE_DOCUMENT_FRAME = 82
_ROLE_DOCUMENT_WEB = 95
_DOCUMENT_ROLES = {_ROLE_DOCUMENT_FRAME, _ROLE_DOCUMENT_WEB}

_CALL_TIMEOUT = 2.0
_WALK_DEADLINE = 4.0
_MAX_NODES = 300
_MAX_DEPTH = 10
_MAX_CHILDREN_PER_NODE = 64
_SELECTION_MAX_NODES = 800
_SELECTION_MAX_CHARS = 100_000
_WINDOW_TEXT_MAX_CHARS = 30_000
_WINDOW_TEXT_DEADLINE = 1.5
_STATE_ACTIVE = 1
_STATE_FOCUSED = 12
_STATE_SHOWING = 25

# Substrings that identify a browser accessible-application by name when the
# pid match fails (Flatpak routes the a11y connection through a proxy, so the
# connection pid is the proxy's, not the browser's).
_BROWSER_NAME_HINTS = ("firefox", "chrom", "brave", "opera", "vivaldi", "edge")

_a11y_conn = None
_a11y_enabled_sent = False
_unavailable_until = 0.0
_UNAVAILABLE_RETRY_S = 30.0


class _WalkBudget:
    """Node/time budget so a huge accessible tree cannot stall capture."""

    def __init__(self, deadline: float, max_nodes: int = _MAX_NODES) -> None:
        """Initialize the walk budget."""
        self.deadline = deadline
        self.nodes_left = max_nodes

    def spend_node(self) -> bool:
        """Consume one node from the budget; False when exhausted."""
        if self.nodes_left <= 0 or time.monotonic() >= self.deadline:
            return False
        self.nodes_left -= 1
        return True


def _open_session_connection():
    """Open a session-bus connection (raises on failure)."""
    from jeepney.io.blocking import open_dbus_connection

    return open_dbus_connection(bus="SESSION")


def _enable_accessibility(session) -> None:
    """Flip the a11y-bus IsEnabled flag so browsers build their trees.

    Screen readers do the same; Firefox reacts immediately, Chromium picks it
    up for new page loads. Best-effort - failures are logged and ignored.
    """
    global _a11y_enabled_sent
    if _a11y_enabled_sent:
        return
    try:
        from jeepney import DBusAddress, new_method_call

        addr = DBusAddress(_A11Y_BUS_PATH, bus_name=_A11Y_BUS_NAME, interface=_IFACE_PROPERTIES)
        msg = new_method_call(addr, "Set", "ssv", ("org.a11y.Status", "IsEnabled", ("b", True)))
        session.send_and_get_reply(msg, timeout=_CALL_TIMEOUT)
        _a11y_enabled_sent = True
        _log.info("a11y IsEnabled flag set on org.a11y.Status")
    except Exception as exc:  # noqa: BLE001 - flag is an optimization, not a requirement
        _log.info("could not set a11y IsEnabled flag: %s", exc)


def _open_a11y_connection():
    """Return a (cached) connection to the accessibility bus, or None."""
    global _a11y_conn, _unavailable_until
    if _a11y_conn is not None:
        return _a11y_conn
    if time.monotonic() < _unavailable_until:
        return None
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection

        session = _open_session_connection()
        try:
            _enable_accessibility(session)
            addr = DBusAddress(_A11Y_BUS_PATH, bus_name=_A11Y_BUS_NAME, interface="org.a11y.Bus")
            reply = session.send_and_get_reply(new_method_call(addr, "GetAddress"), timeout=_CALL_TIMEOUT)
            address = str(reply.body[0] or "")
        finally:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - close failures don't matter
                pass
        if not address:
            raise RuntimeError("empty a11y bus address")
        _a11y_conn = open_dbus_connection(address)
        return _a11y_conn
    except Exception as exc:  # noqa: BLE001 - no a11y bus means no URL, not a crash
        _log.info("accessibility bus unavailable: %s", exc)
        _unavailable_until = time.monotonic() + _UNAVAILABLE_RETRY_S
        return None


def _reset_connection() -> None:
    """Drop the cached a11y connection after an I/O failure."""
    global _a11y_conn
    if _a11y_conn is not None:
        try:
            _a11y_conn.close()
        except Exception:  # noqa: BLE001 - already broken
            pass
        _a11y_conn = None


def _call(conn, dest: str, path: str, iface: str, method: str, sig: str | None = None, body: tuple = ()):
    """Send one method call and return the reply body tuple (raises on error)."""
    from jeepney import DBusAddress, new_method_call

    addr = DBusAddress(path, bus_name=dest, interface=iface)
    msg = new_method_call(addr, method, sig, body) if sig else new_method_call(addr, method)
    reply = conn.send_and_get_reply(msg, timeout=_CALL_TIMEOUT)
    return reply.body


def _get_children(conn, dest: str, path: str) -> list[tuple[str, str]]:
    """Return (bus name, object path) child references of an accessible."""
    body = _call(conn, dest, path, _IFACE_ACCESSIBLE, "GetChildren")
    refs = []
    for item in (body[0] if body else []) or []:
        try:
            name, obj_path = str(item[0]), str(item[1])
        except Exception:  # noqa: BLE001 - skip malformed refs
            continue
        if obj_path and obj_path != "/org/a11y/atspi/null":
            refs.append((name, obj_path))
    return refs[:_MAX_CHILDREN_PER_NODE]


def _get_role(conn, dest: str, path: str) -> int:
    """Return the AT-SPI role number of an accessible (-1 on failure)."""
    try:
        body = _call(conn, dest, path, _IFACE_ACCESSIBLE, "GetRole")
        return int(body[0])
    except Exception:  # noqa: BLE001 - treat unreadable nodes as roleless
        return -1


def _get_name(conn, dest: str, path: str) -> str:
    """Return the accessible's Name property ("" on failure)."""
    try:
        body = _call(
            conn, dest, path, _IFACE_PROPERTIES, "Get", "ss", (_IFACE_ACCESSIBLE, "Name")
        )
        return str(_variant_value(body[0]) or "")
    except Exception:  # noqa: BLE001 - nameless is fine
        return ""


def _variant_value(value):
    """Unwrap a D-Bus variant returned by jeepney when present."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        return value[1]
    return value


def _selected_text_for_node(conn, dest: str, path: str) -> str:
    """Return active Text-interface selections for one accessible node."""
    try:
        body = _call(conn, dest, path, _IFACE_TEXT, "GetNSelections")
        count = max(0, min(32, int(body[0])))
    except Exception:
        return ""
    parts: list[str] = []
    remaining = _SELECTION_MAX_CHARS
    for index in range(count):
        try:
            bounds = _call(conn, dest, path, _IFACE_TEXT, "GetSelection", "i", (index,))
            start, end = int(bounds[0]), int(bounds[1])
            if start < 0 or end <= start:
                continue
            end = min(end, start + remaining)
            text = str(_call(conn, dest, path, _IFACE_TEXT, "GetText", "ii", (start, end))[0] or "")
        except Exception:
            continue
        text = text.strip()
        if text:
            parts.append(text)
            remaining -= len(text)
            if remaining <= 0:
                break
    return "\n".join(parts)


def _text_for_node(conn, dest: str, path: str, max_chars: int) -> str:
    """Read up to ``max_chars`` of one accessible node's Text interface."""
    try:
        body = _call(conn, dest, path, _IFACE_TEXT, "GetCharacterCount")
        count = int(_variant_value(body[0]) or 0)
        if count <= 0:
            return ""
        end = min(count, max(1, int(max_chars)))
        text = _call(conn, dest, path, _IFACE_TEXT, "GetText", "ii", (0, end))[0]
        return str(text or "").strip()
    except Exception:
        return ""


def _node_states(conn, dest: str, path: str) -> set[int]:
    """Return the AT-SPI state enumeration values held by one node.

    AT-SPI serializes its state set as 32-bit bitset words.  A focused state
    (12), for example, is bit 12 of the first word, not the integer ``12`` in
    the response array.
    """
    try:
        body = _call(conn, dest, path, _IFACE_ACCESSIBLE, "GetState")
        words = [int(value) for value in (body[0] if body else [])]
        states: set[int] = set()
        for word_index, word in enumerate(words):
            for bit in range(32):
                if word & (1 << bit):
                    states.add(word_index * 32 + bit)
        return states
    except Exception:
        return set()


def get_focused_context() -> dict[str, object]:
    """Return focused native-Wayland application/window metadata via AT-SPI2."""
    if not _IS_LINUX:
        return {}
    conn = _open_a11y_connection()
    if conn is None:
        return {}
    deadline = time.monotonic() + _WALK_DEADLINE
    try:
        focused = _focused_window(conn, deadline)
        if focused is None:
            return {}
        app_ref, frame_ref, focused_ref = focused
        pid_cache: dict[str, int] = {}
        app_name = _get_name(conn, app_ref[0], app_ref[1])
        window_title = _get_name(conn, frame_ref[0], frame_ref[1])
        docs = _collect_document_urls(conn, frame_ref, _WalkBudget(deadline, max_nodes=300))
        return {
            "app_name": app_name,
            "process_name": app_name,
            "pid": _connection_pid(conn, app_ref[0], pid_cache),
            "window_title": window_title,
            "focused_name": _get_name(conn, focused_ref[0], focused_ref[1]),
            "browser_url": _choose_document_url(docs, window_title),
        }
    except Exception as exc:
        _log.info("AT-SPI focused-context walk failed: %s", exc)
        _reset_connection()
        return {}


def _focused_window(
    conn, deadline: float
) -> tuple[tuple[str, str], tuple[str, str], tuple[str, str]] | None:
    """Return the active app, its window, and its focused accessible node.

    A Wayland desktop can expose stale ``FOCUSED`` states for background
    controls.  Requiring an active top-level window before following its
    focused descendant prevents a background selection from becoming context.
    """
    apps = _get_children(conn, _REGISTRY_NAME, _ROOT_PATH)
    nodes_left = _SELECTION_MAX_NODES
    active_frames: list[tuple[tuple[str, str], tuple[str, str]]] = []
    for app_ref in apps:
        frames = list(_get_children(conn, app_ref[0], app_ref[1]))
        for frame_ref in frames:
            if _STATE_ACTIVE in _node_states(conn, frame_ref[0], frame_ref[1]):
                active_frames.append((app_ref, frame_ref))

    for app_ref, frame_ref in active_frames:
        queue = [(frame_ref[0], frame_ref[1])]
        seen: set[tuple[str, str]] = set()
        while queue and nodes_left > 0 and time.monotonic() < deadline:
            dest, path = queue.pop(0)
            if (dest, path) in seen:
                continue
            seen.add((dest, path))
            nodes_left -= 1
            if _STATE_FOCUSED in _node_states(conn, dest, path):
                return app_ref, frame_ref, (dest, path)
            try:
                queue.extend(_get_children(conn, dest, path))
            except Exception:
                continue
    return None


def _selected_text_in_window(conn, window_ref: tuple[str, str], deadline: float) -> str:
    """Return selection from a focused window, preferring its focused node."""
    queue = [window_ref]
    seen: set[tuple[str, str]] = set()
    focused: list[tuple[str, str]] = []
    nodes: list[tuple[str, str]] = []
    while queue and len(nodes) < _SELECTION_MAX_NODES and time.monotonic() < deadline:
        dest, path = queue.pop(0)
        if (dest, path) in seen:
            continue
        seen.add((dest, path))
        nodes.append((dest, path))
        if _STATE_FOCUSED in _node_states(conn, dest, path):
            focused.append((dest, path))
        try:
            queue.extend(_get_children(conn, dest, path))
        except Exception:
            continue

    checked: set[tuple[str, str]] = set()
    for dest, path in focused + nodes:
        if (dest, path) in checked or time.monotonic() >= deadline:
            continue
        checked.add((dest, path))
        text = _selected_text_for_node(conn, dest, path)
        if text:
            return text
    return ""


def _window_text(conn, window_ref: tuple[str, str], deadline: float, max_chars: int) -> str:
    """Return the best full-text accessible node in one active window."""
    direct = _text_for_node(conn, window_ref[0], window_ref[1], max_chars)
    if direct:
        return direct

    queue = [window_ref]
    seen: set[tuple[str, str]] = set()
    fallback: list[tuple[str, str]] = []
    best = ""
    while queue and len(seen) < _SELECTION_MAX_NODES and time.monotonic() < deadline:
        dest, path = queue.pop(0)
        if (dest, path) in seen:
            continue
        seen.add((dest, path))
        states = _node_states(conn, dest, path)
        role = _get_role(conn, dest, path)
        if role in _DOCUMENT_ROLES or _STATE_FOCUSED in states:
            text = _text_for_node(conn, dest, path, max_chars)
            if len(text) > len(best):
                best = text
        elif _STATE_SHOWING in states and (dest, path) != window_ref:
            fallback.append((dest, path))
        try:
            queue.extend(_get_children(conn, dest, path))
        except Exception:
            continue

    if best:
        return best
    for dest, path in fallback[:64]:
        if time.monotonic() >= deadline:
            break
        text = _text_for_node(conn, dest, path, max_chars)
        if len(text) > len(best):
            best = text
    return best


def get_selected_text() -> str:
    """Return selected text from the focused native Linux window via AT-SPI2.

    Wayland does not offer a global selected-text API. Search the focused
    window's accessible descendants rather than only its focused control, so
    editors and browsers that expose selection on a nearby document node work
    without falling back to clipboard or injected copy keys.
    """
    if not _IS_LINUX:
        return ""
    conn = _open_a11y_connection()
    if conn is None:
        return ""
    deadline = time.monotonic() + _WALK_DEADLINE
    try:
        focused = _focused_window(conn, deadline)
        if focused is None:
            return ""
        _app_ref, window_ref, _focused_ref = focused
        text = _selected_text_in_window(conn, window_ref, deadline)
        if text:
            _log.info("selected text via AT-SPI: %s characters", len(text))
        return text
    except Exception as exc:
        _log.info("AT-SPI selected-text walk failed: %s", exc)
        _reset_connection()
        return ""


def get_active_window_text(max_chars: int = _WINDOW_TEXT_MAX_CHARS) -> str:
    """Return unselected text exposed by the focused native Wayland window.

    This is a bounded accessibility read, not a screenshot or clipboard
    operation.  It deliberately reads only the active window that owns focus.
    """
    if not _IS_LINUX:
        return ""
    conn = _open_a11y_connection()
    if conn is None:
        return ""
    deadline = time.monotonic() + _WINDOW_TEXT_DEADLINE
    try:
        focused = _focused_window(conn, deadline)
        if focused is None:
            return ""
        _app_ref, window_ref, _focused_ref = focused
        text = _window_text(conn, window_ref, deadline, max_chars)
        if text:
            _log.info("active-window text via AT-SPI: %s characters", len(text))
        return text
    except Exception as exc:
        _log.info("AT-SPI active-window text walk failed: %s", exc)
        _reset_connection()
        return ""


def _connection_pid(conn, unique_name: str, cache: dict[str, int]) -> int:
    """Return the process id owning a bus connection (0 on failure)."""
    if unique_name in cache:
        return cache[unique_name]
    pid = 0
    try:
        body = _call(
            conn,
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetConnectionUnixProcessID",
            "s",
            (unique_name,),
        )
        pid = int(body[0])
    except Exception:  # noqa: BLE001 - proxied/gone connections have no pid
        pid = 0
    cache[unique_name] = pid
    return pid


def _document_url(conn, dest: str, path: str) -> str:
    """Read a document accessible's URL attribute ("" when absent)."""
    attrs: dict[str, str] = {}
    try:
        body = _call(conn, dest, path, _IFACE_DOCUMENT, "GetAttributes")
        raw = body[0] if body else {}
        if isinstance(raw, dict):
            attrs = {str(k): str(v) for k, v in raw.items()}
    except Exception:  # noqa: BLE001 - fall through to the single-key read
        attrs = {}
    for key in ("DocURL", "DocUrl", "URI"):
        value = attrs.get(key, "")
        if value:
            return value
    for key in ("DocURL", "DocUrl", "URI"):
        try:
            body = _call(conn, dest, path, _IFACE_DOCUMENT, "GetAttributeValue", "s", (key,))
            value = str(body[0] or "")
            if value:
                return value
        except Exception:  # noqa: BLE001 - attribute not supported
            continue
    return ""


def _usable_url(url: str) -> bool:
    """Return True for URLs worth fetching as page context."""
    url = (url or "").strip()
    return url.startswith(("http://", "https://", "file://"))


def _page_title(window_title: str) -> str:
    """Strip the trailing " - <browser>" suffix from an X11 window title."""
    title = (window_title or "").strip()
    for sep in (" — ", " - ", " – "):
        if sep in title:
            title = title.rsplit(sep, 1)[0].strip()
            break
    return title


def _choose_document_url(docs: list[tuple[str, str]], window_title: str) -> str:
    """Pick the document whose name matches the window title, else the first.

    *docs* is [(accessible name, url)] in discovery order. Browsers can expose
    background-tab documents too, so the visible tab is identified by matching
    the document name against the page-title portion of the window title.
    """
    usable = [(name, url) for name, url in docs if _usable_url(url)]
    if not usable:
        return ""
    wanted = _page_title(window_title).lower()
    if wanted:
        for name, url in usable:
            title = (name or "").strip().lower()
            if title and (title == wanted or title in wanted or wanted in title):
                return url
    return usable[0][1]


def _collect_document_urls(conn, frame_ref: tuple[str, str], budget: _WalkBudget) -> list[tuple[str, str]]:
    """Breadth-first search a window frame for web-document URLs."""
    docs: list[tuple[str, str]] = []
    queue: list[tuple[str, str, int]] = [(frame_ref[0], frame_ref[1], 0)]
    seen: set[tuple[str, str]] = set()
    while queue:
        dest, path, depth = queue.pop(0)
        if (dest, path) in seen:
            continue
        seen.add((dest, path))
        if not budget.spend_node():
            break
        role = _get_role(conn, dest, path)
        if role in _DOCUMENT_ROLES:
            url = _document_url(conn, dest, path)
            docs.append((_get_name(conn, dest, path), url))
            continue  # tab content is huge; never descend into documents
        if depth >= _MAX_DEPTH:
            continue
        try:
            children = _get_children(conn, dest, path)
        except Exception:  # noqa: BLE001 - unreadable branch, skip it
            continue
        for child in children:
            queue.append((child[0], child[1], depth + 1))
    return docs


def _ordered_frames(conn, app_ref: tuple[str, str], window_title: str) -> list[tuple[str, str]]:
    """Return the app's window frames, best title match first."""
    try:
        frames = _get_children(conn, app_ref[0], app_ref[1])
    except Exception:  # noqa: BLE001 - app vanished mid-walk
        return []
    if len(frames) <= 1 or not (window_title or "").strip():
        return frames
    wanted = window_title.strip().lower()

    def rank(ref: tuple[str, str]) -> int:
        """Rank a frame by how well its name matches the window title."""
        name = _get_name(conn, ref[0], ref[1]).strip().lower()
        if name and name == wanted:
            return 0
        if name and (name in wanted or wanted in name):
            return 1
        return 2

    return sorted(frames, key=rank)


def _candidate_apps(conn, pid: int, process_name: str) -> list[tuple[str, str]]:
    """Return accessible applications that look like the target browser."""
    apps = _get_children(conn, _REGISTRY_NAME, _ROOT_PATH)
    pid_cache: dict[str, int] = {}
    by_pid: list[tuple[str, str]] = []
    by_name: list[tuple[str, str]] = []
    hints = [h for h in _BROWSER_NAME_HINTS if not process_name or h in process_name.lower()]
    if process_name and not hints:
        hints = [process_name.lower()]
    for ref in apps:
        if pid and _connection_pid(conn, ref[0], pid_cache) == pid:
            by_pid.append(ref)
            continue
        name = _get_name(conn, ref[0], ref[1]).lower()
        if name and any(hint in name for hint in hints):
            by_name.append(ref)
    return by_pid + by_name


def get_browser_tab_url(pid: int = 0, window_title: str = "", process_name: str = "") -> str:
    """Return the current tab URL of a running browser via AT-SPI2, or "".

    *pid* is the browser window's process id (preferred match), *window_title*
    the X11 title used to pick the right window/tab, *process_name* a fallback
    hint when the pid cannot be matched (sandboxed browsers).
    """
    if not _IS_LINUX:
        return ""
    conn = _open_a11y_connection()
    if conn is None:
        return ""
    deadline = time.monotonic() + _WALK_DEADLINE
    try:
        apps = _candidate_apps(conn, int(pid or 0), process_name or "")
        if not apps:
            _log.info(
                "no accessible browser app found (pid=%s process=%r); "
                "the browser may need a restart/reload after a11y was enabled",
                pid,
                process_name,
            )
            return ""
        budget = _WalkBudget(deadline)
        for app_ref in apps:
            for frame_ref in _ordered_frames(conn, app_ref, window_title):
                docs = _collect_document_urls(conn, frame_ref, budget)
                url = _choose_document_url(docs, window_title)
                if url:
                    _log.info("browser tab url via AT-SPI: %s", url)
                    return url
                if time.monotonic() >= deadline:
                    return ""
        return ""
    except Exception as exc:  # noqa: BLE001 - a broken walk must not break capture
        _log.info("AT-SPI browser walk failed: %s", exc)
        _reset_connection()
        return ""
