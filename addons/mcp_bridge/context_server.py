"""Wisp Context Server — an MCP stdio server exposing live desktop context.

This is the *server* side of MCP (the addon's __init__.py is the client side):
an MCP client such as Claude Desktop or Cursor launches this script as a
subprocess and gets tools for reading the user's selection, clipboard, active
window, browser page, and screen through Wisp's capture machinery. The running
Wisp app is not involved — this process imports core.* directly.

Speaks JSON-RPC 2.0, one message per line: stdout carries protocol messages
and nothing else, all logging goes to stderr (MCP clients surface server
stderr in their logs, so the startup self-check below is the first place to
look when a tool misbehaves).

Launch with Wisp's own interpreter — the capture stack needs Wisp's installed
dependencies. The addon writes a ready-to-paste client config snippet
(claude_config_snippet.json, also logged at addon startup) so the paths never
have to be typed by hand.
"""
from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

_IS_WIN = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"

# --- sys.path bootstrap ------------------------------------------------------
# MCP clients launch this script from their own working directory, so `import
# core...` only works if the Wisp root (the folder containing core/) is put on
# sys.path explicitly. The addon lives at <root>/addons/mcp_bridge, but walk
# upward instead of hardcoding the depth so a relocated addon folder still
# finds a root when one exists above it.

def _find_wisp_root() -> Path | None:
    """Walk up from this file to the first folder containing core/__init__.py."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "core" / "__init__.py").exists():
            return candidate
    return None


_WISP_ROOT = _find_wisp_root()
if _WISP_ROOT is not None and str(_WISP_ROOT) not in sys.path:
    sys.path.insert(0, str(_WISP_ROOT))


def _log(message: str) -> None:
    """Log one line to stderr (never stdout — that's the protocol channel)."""
    print(f"[wisp-context-server] {message}", file=sys.stderr, flush=True)


# --- startup self-check ------------------------------------------------------

def _dependency_probes() -> list[tuple[str, str]]:
    """(module, which tools need it) pairs for this platform."""
    if _IS_WIN:
        return [
            ("comtypes", "get_selected_text / read_browser_page"),
            ("pyperclip", "get_clipboard / get_selected_text"),
            ("PIL", "take_screen_snip"),
            ("mss", "take_screen_snip"),
            ("psutil", "get_active_window"),
        ]
    if _IS_MAC:
        return [
            ("PIL", "take_screen_snip"),
            ("psutil", "get_active_window"),
        ]
    return [
        ("pyperclip", "get_clipboard"),
        ("PIL", "take_screen_snip"),
        ("mss", "take_screen_snip"),
        ("Xlib", "get_selected_text / get_active_window"),
        ("psutil", "get_active_window"),
    ]


def _self_check() -> None:
    """Report environment facts so a wrong-interpreter launch names itself."""
    _log(f"python {platform.python_version()} at {sys.executable}")
    if _WISP_ROOT is None:
        _log("ERROR: no Wisp root found above this script (folder containing "
             "core/); every tool call will fail. Keep context_server.py inside "
             "the Wisp installation.")
        return
    _log(f"wisp root: {_WISP_ROOT}")
    import importlib.util
    missing = [
        f"{module} (needed by {needed_by})"
        for module, needed_by in _dependency_probes()
        if importlib.util.find_spec(module) is None
    ]
    if missing:
        _log("WARNING: missing packages — launch this server with Wisp's own "
             "Python interpreter: " + "; ".join(missing))
    else:
        _log("all capture dependencies present")


def _server_enabled() -> bool:
    """Read the addon's server_enabled setting; fail open when unreadable.

    WISP_CONTEXT_SERVER_ENABLED overrides the setting when present (used by
    tests to stay independent of this machine's addon settings).
    """
    override = os.environ.get("WISP_CONTEXT_SERVER_ENABLED", "").strip().lower()
    if override:
        return override in {"1", "true", "yes", "on"}
    try:
        from core.addon_manager import addon_setting

        value = addon_setting("mcp-bridge", "server_enabled", "true")
        return str(value if value is not None else "true").strip().lower() in {
            "1", "true", "yes", "on",
        }
    except Exception:
        return True


# --- self-exclusion guard ----------------------------------------------------
# The MCP client that launched us is focused while it calls our tools, so
# "focused window" often means the client itself. Its whole app tree is
# excluded from window answers and never receives a synthetic copy combo.

# Session roots: climbing past these would put the whole desktop (every app
# under explorer/launchd) in the exclusion set, so the climb stops below them.
_CLIMB_STOP_PROCS = {
    "explorer.exe", "userinit.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "svchost.exe",  # Windows
    "launchd",                       # macOS
    "systemd", "init",               # Linux
}


def _client_pids() -> set[int]:
    """Pids of the launching MCP client's app tree, plus our own.

    The window-owning client process is usually not our direct parent: launch
    layers like a venv python trampoline or Electron helper processes sit in
    between (Claude Desktop → python shim → python is the observed chain). So
    climb the ancestor chain until the session shell and exclude the topmost
    non-shell ancestor's entire process tree.
    """
    pids = {os.getpid(), os.getppid()} - {0}
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        top = proc
        for ancestor in proc.parents():
            if ancestor.pid <= 1 or (ancestor.name() or "").lower() in _CLIMB_STOP_PROCS:
                break
            top = ancestor
            pids.add(ancestor.pid)
        pids.update(child.pid for child in top.children(recursive=True))
    except Exception:
        pass
    return pids


_FOCUS_GUIDANCE = (
    "(no selection readable — the assistant app itself has focus, so the "
    "selection in the app behind it can't be captured safely. Ask the user to "
    "copy the text, then call get_clipboard.)"
)


# --- tool implementations ----------------------------------------------------
# core.* imports stay inside the handlers: a broken capture dependency then
# degrades to a per-tool error result instead of preventing startup.

def _tool_get_selected_text(_args: dict) -> str:
    """Read the current selection, refusing the keystroke path under the guard."""
    from core import capture
    from core import context_fetcher

    foreground = context_fetcher.get_active_window_info()
    client_focused = int(foreground.pid or 0) in _client_pids()
    text = capture.get_selected_text(allow_synthetic_copy=not client_focused)
    if text:
        return text
    if client_focused:
        return _FOCUS_GUIDANCE
    return "(no selection)"


def _tool_get_clipboard(_args: dict) -> str:
    """Read the current clipboard text."""
    from core import capture

    text = capture.get_clipboard_text()
    return text or "(clipboard is empty or has no text)"


def _tool_get_active_window(_args: dict) -> str:
    """Describe the window the user is working in, skipping the client's own."""
    from core import context_fetcher

    win = context_fetcher.get_active_window_info(exclude_pids=_client_pids())
    if not (win.title or win.process_name):
        return "(no active window found)"
    lines = [f"title:   {win.title or '(untitled)'}"]
    if win.process_name:
        lines.append(f"process: {win.process_name}")
    if win.url:
        lines.append(f"url:     {win.url}")
    return "\n".join(lines)


def _tool_read_browser_page(args: dict) -> str:
    """Read the visible browser window's page text (no focus required)."""
    from core import context_fetcher

    win = context_fetcher.get_browser_window_for_context()
    if not (win.url or win.hwnd):
        return "(no visible browser window found)"
    text = context_fetcher.fetch_browser_content_for_window(
        url=win.url, hwnd=int(win.hwnd or 0)
    )
    if not text:
        return f"(browser window found ({win.url or win.title}) but its page text could not be read)"
    max_chars = int(args.get("max_chars", 0) or 0)
    if max_chars > 0:
        text = text[:max_chars]
    header = f"url: {win.url}\n\n" if win.url else ""
    return header + text


_SNIP_MAX_DIMENSION = 1568  # matches vision-model useful resolution; caps payload


def _tool_take_screen_snip(_args: dict) -> list:
    """Screenshot the primary monitor; returns an MCP image content block."""
    from core import capture

    img = capture.get_screen_snippet()
    width, height = img.size
    largest = max(width, height)
    if largest > _SNIP_MAX_DIMENSION:
        scale = _SNIP_MAX_DIMENSION / float(largest)
        img = img.resize((max(1, int(width * scale)), max(1, int(height * scale))))
    return [{
        "type": "image",
        "data": capture.image_to_base64(img),
        "mimeType": "image/png",
    }]


TOOLS = [
    {
        "name": "get_selected_text",
        "description": (
            "Read the text the user currently has highlighted on their desktop. "
            "Works best when the selection is in the app the user last used. If "
            "this returns no selection, ask the user to copy the text and call "
            "get_clipboard instead."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_clipboard",
        "description": (
            "Read the user's current clipboard text. Reliable on every platform "
            "and needs no window focus — the fallback when get_selected_text "
            "returns nothing."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_active_window",
        "description": (
            "Report the window the user is working in (title, app, URL when it "
            "is a browser), skipping the assistant's own window."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_browser_page",
        "description": (
            "Read the text of the page open in the user's visible browser "
            "window (Chrome, Edge, Firefox, Safari...), even when the browser "
            "is not focused. Returns the URL plus the page text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_chars": {
                    "type": "integer",
                    "description": "Optional cap on returned characters.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "take_screen_snip",
        "description": (
            "Take a screenshot of the user's primary monitor and return it as "
            "an image. Use when the user asks about something visible on their "
            "screen."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]

_HANDLERS = {
    "get_selected_text": _tool_get_selected_text,
    "get_clipboard": _tool_get_clipboard,
    "get_active_window": _tool_get_active_window,
    "read_browser_page": _tool_read_browser_page,
    "take_screen_snip": _tool_take_screen_snip,
}


# --- protocol loop -----------------------------------------------------------

def _send(obj: dict) -> None:
    """Write one JSON-RPC message and flush."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _ok(mid, result) -> None:
    """Send a JSON-RPC result."""
    _send({"jsonrpc": "2.0", "id": mid, "result": result})


def _err(mid, code, message) -> None:
    """Send a JSON-RPC error."""
    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def _call_tool(name: str, args: dict) -> list:
    """Run one tool; returns MCP content blocks."""
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"unknown tool: {name}")
    result = handler(args or {})
    if isinstance(result, list):
        return result
    return [{"type": "text", "text": str(result)}]


def main() -> int:
    """Answer JSON-RPC lines from stdin until EOF; single-threaded on purpose."""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8")
    _self_check()
    if not _server_enabled():
        _log("server_enabled is off in the mcp-bridge addon settings; refusing "
             "to serve. Flip it on in Wisp's Addon Manager to use this server.")
        return 2
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            _ok(mid, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "wisp-context-server", "version": "0.9.0"},
            })
        elif method == "ping":
            _ok(mid, {})
        elif method == "tools/list":
            _ok(mid, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = str(params.get("name") or "")
            args = params.get("arguments") or {}
            try:
                _ok(mid, {"content": _call_tool(name, args), "isError": False})
            except Exception as exc:
                _log(f"tool {name!r} failed: {exc}")
                _ok(mid, {
                    "content": [{"type": "text", "text": f"error: {exc}"}],
                    "isError": True,
                })
        elif mid is not None:
            _err(mid, -32601, f"unknown method: {method}")
        # notifications without a handler are ignored per JSON-RPC
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
