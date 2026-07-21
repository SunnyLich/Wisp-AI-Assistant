"""Tests for the Wisp Context Server (the MCP server side of mcp_bridge).

Protocol behavior is tested through a real subprocess (dogfooded through the
bridge's own MCPStdioClient); capture wiring is tested in-process with
monkeypatching, since CI is headless and must never perform a real capture,
keystroke, or screenshot.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image

from addons.mcp_bridge import MCPStdioClient, client_config_snippet, context_server
from core.context_fetcher import WindowInfo
from core.system import clipboard_lock

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER = REPO_ROOT / "addons" / "mcp_bridge" / "context_server.py"

EXPECTED_TOOLS = {
    "get_selected_text",
    "get_clipboard",
    "get_active_window",
    "read_browser_page",
    "take_screen_snip",
}

_ENABLED_ENV = {"WISP_CONTEXT_SERVER_ENABLED": "1"}


def _make_client(name: str = "ctx", cwd: str | None = None) -> MCPStdioClient:
    """Dogfood harness: Wisp's own MCP client pointed at the context server."""
    return MCPStdioClient(
        name, sys.executable, [str(SERVER)], env=_ENABLED_ENV, cwd=cwd, timeout=30
    )


# ---------------------------------------------------------------------------
# Subprocess protocol tests
# ---------------------------------------------------------------------------

def test_handshake_and_tool_discovery():
    """The server initializes and advertises exactly the five context tools."""
    client = _make_client()
    try:
        client.start()
        tools = client.list_tools()
    finally:
        client.stop()

    by_name = {tool["name"]: tool for tool in tools}
    assert set(by_name) == EXPECTED_TOOLS
    for tool in tools:
        assert re.fullmatch(r"[A-Za-z0-9_-]+", tool["name"])
        assert tool["description"].strip()
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties", {}), dict)


def test_serves_from_foreign_cwd(tmp_path):
    """The sys.path bootstrap works when launched from an unrelated directory,
    the way Claude Desktop launches servers."""
    client = _make_client("ctx-cwd", cwd=str(tmp_path))
    try:
        client.start()
        tools = client.list_tools()
    finally:
        client.stop()
    assert {tool["name"] for tool in tools} == EXPECTED_TOOLS


def test_unknown_tool_returns_iserror_result():
    """Calling a nonexistent tool yields an isError tool result, not a crash."""
    client = _make_client("ctx-unknown")
    try:
        client.start()
        result = client.call_tool("no_such_tool", {})
    finally:
        client.stop()
    assert result.startswith("[tool error]")
    assert "no_such_tool" in result


def test_unknown_method_and_garbage_input():
    """Garbage lines are ignored and unknown methods answer -32601; the server
    keeps serving afterwards."""
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, **_ENABLED_ENV},
    )
    try:
        proc.stdin.write("this is not json\n")
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}},
        }) + "\n")
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "bogus/method", "params": {},
        }) + "\n")
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {},
        }) + "\n")
        proc.stdin.close()

        replies = {msg["id"]: msg for msg in map(json.loads, proc.stdout)}
    finally:
        proc.stdout.close()
        assert proc.wait(timeout=30) == 0

    assert replies[1]["result"]["serverInfo"]["name"] == "wisp-context-server"
    assert replies[2]["error"]["code"] == -32601
    assert {tool["name"] for tool in replies[3]["result"]["tools"]} == EXPECTED_TOOLS


def test_disabled_server_refuses_to_serve(tmp_path):
    """server_enabled off = exit before answering anything."""
    result = subprocess.run(
        [sys.executable, str(SERVER)],
        input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        env={**__import__("os").environ, "WISP_CONTEXT_SERVER_ENABLED": "0"},
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "server_enabled" in result.stderr


# ---------------------------------------------------------------------------
# In-process handler tests (no real captures)
# ---------------------------------------------------------------------------

def _text_of(blocks: list) -> str:
    """Join the text of MCP text content blocks."""
    return "\n".join(b["text"] for b in blocks if b["type"] == "text")


def test_selected_text_happy_path(monkeypatch):
    """Foreground is a normal app: full capture path runs, text comes back."""
    seen = {}

    def fake_get_selected_text(*, allow_synthetic_copy=True):
        seen["allow_synthetic_copy"] = allow_synthetic_copy
        return "hello selection"

    monkeypatch.setattr("core.capture.get_selected_text", fake_get_selected_text)
    monkeypatch.setattr(
        "core.context_fetcher.get_active_window_info",
        lambda **kw: WindowInfo(title="Notepad", pid=99999999),
    )
    monkeypatch.setattr(context_server, "_client_pids", lambda: {1})

    blocks = context_server._call_tool("get_selected_text", {})
    assert _text_of(blocks) == "hello selection"
    assert seen["allow_synthetic_copy"] is True


def test_selected_text_focus_guard_blocks_synthetic_copy(monkeypatch):
    """Foreground belongs to the MCP client: no copy keystroke is allowed and
    the guidance message comes back instead of the client's own text."""
    seen = {}

    def fake_get_selected_text(*, allow_synthetic_copy=True):
        seen["allow_synthetic_copy"] = allow_synthetic_copy
        return None

    monkeypatch.setattr("core.capture.get_selected_text", fake_get_selected_text)
    monkeypatch.setattr(
        "core.context_fetcher.get_active_window_info",
        lambda **kw: WindowInfo(title="Claude", pid=4242),
    )
    monkeypatch.setattr(context_server, "_client_pids", lambda: {4242})

    blocks = context_server._call_tool("get_selected_text", {})
    assert seen["allow_synthetic_copy"] is False
    assert _text_of(blocks) == context_server._FOCUS_GUIDANCE


def test_selected_text_empty_is_friendly(monkeypatch):
    """No selection anywhere: friendly text, not an error."""
    monkeypatch.setattr("core.capture.get_selected_text", lambda **kw: None)
    monkeypatch.setattr(
        "core.context_fetcher.get_active_window_info",
        lambda **kw: WindowInfo(title="Notepad", pid=99999999),
    )
    monkeypatch.setattr(context_server, "_client_pids", lambda: {1})

    assert _text_of(context_server._call_tool("get_selected_text", {})) == "(no selection)"


def test_selected_text_failure_matrix_is_returned_as_mcp_tool_errors(monkeypatch):
    """Selection backend faults remain in-band and cannot terminate the MCP server."""
    monkeypatch.setattr(
        "core.context_fetcher.get_active_window_info",
        lambda **kw: WindowInfo(title="Notepad", pid=99999999),
    )
    monkeypatch.setattr(context_server, "_client_pids", lambda: {1})
    faults = (
        RuntimeError("focus moved"),
        RuntimeError("target control does not expose accessible text"),
        PermissionError("OS permission is missing"),
        RuntimeError("target application is unsupported"),
        NotImplementedError("platform backend is unsupported"),
    )
    for fault in faults:
        monkeypatch.setattr(
            "core.capture.get_selected_text",
            lambda **_kw: (_ for _ in ()).throw(fault),
        )
        content, is_error = context_server._safe_call_tool("get_selected_text", {})
        assert is_error is True
        assert str(fault) in _text_of(content)


def test_selected_text_is_clipped_to_safety_limit(monkeypatch):
    """A giant desktop selection cannot become a giant MCP result."""
    monkeypatch.setattr(
        "core.capture.get_selected_text",
        lambda **kw: "x" * (context_server._MAX_TEXT_RESULT_CHARS * 3),
    )
    monkeypatch.setattr(
        "core.context_fetcher.get_active_window_info",
        lambda **kw: WindowInfo(title="Notepad", pid=99999999),
    )
    monkeypatch.setattr(context_server, "_client_pids", lambda: {1})

    text = _text_of(context_server._call_tool("get_selected_text", {}))
    assert len(text) <= context_server._MAX_TEXT_RESULT_CHARS
    assert text.endswith("[context truncated at safety limit]")


def test_clipboard_is_clipped_to_safety_limit(monkeypatch):
    """A giant clipboard value cannot become a giant MCP result."""
    monkeypatch.setattr(
        "core.capture.get_clipboard_text",
        lambda: "x" * (context_server._MAX_TEXT_RESULT_CHARS * 3),
    )
    text = _text_of(context_server._call_tool("get_clipboard", {}))
    assert len(text) <= context_server._MAX_TEXT_RESULT_CHARS
    assert text.endswith("[context truncated at safety limit]")


def test_active_window_excludes_client_pids(monkeypatch):
    """The tool passes the client's process tree as the exclusion set."""
    seen = {}

    def fake_get_active_window_info(*, exclude_pids=None):
        seen["exclude_pids"] = exclude_pids
        return WindowInfo(title="Budget.xlsx", process_name="excel.exe", pid=7)

    monkeypatch.setattr(
        "core.context_fetcher.get_active_window_info", fake_get_active_window_info
    )
    monkeypatch.setattr(context_server, "_client_pids", lambda: {123, 456})

    text = _text_of(context_server._call_tool("get_active_window", {}))
    assert seen["exclude_pids"] == {123, 456}
    assert "Budget.xlsx" in text
    assert "excel.exe" in text


def test_read_browser_page_formats_url_header(monkeypatch):
    """Browser tool combines the located window's URL with its page text."""
    monkeypatch.setattr(
        "core.context_fetcher.get_browser_window_for_context",
        lambda: WindowInfo(url="https://example.com", hwnd=11),
    )
    monkeypatch.setattr(
        "core.context_fetcher.fetch_browser_content_for_window",
        lambda url="", hwnd=0: "the page text",
    )
    text = _text_of(context_server._call_tool("read_browser_page", {}))
    assert text.startswith("url: https://example.com")
    assert text.endswith("the page text")


def test_screen_snip_returns_png_image_block(monkeypatch):
    """Snip returns an MCP image block whose payload decodes to a PNG."""
    import base64

    monkeypatch.setattr(
        "core.capture.get_screen_snippet", lambda region=None: Image.new("RGB", (10, 8))
    )
    blocks = context_server._call_tool("take_screen_snip", {})
    assert len(blocks) == 1
    assert blocks[0]["type"] == "image"
    assert blocks[0]["mimeType"] == "image/png"
    img = Image.open(io.BytesIO(base64.b64decode(blocks[0]["data"])))
    assert img.format == "PNG"
    assert img.size == (10, 8)


def test_screen_snip_downscales_large_captures(monkeypatch):
    """Oversized screenshots are downscaled to the payload cap."""
    import base64

    monkeypatch.setattr(
        "core.capture.get_screen_snippet", lambda region=None: Image.new("RGB", (4000, 2000))
    )
    blocks = context_server._call_tool("take_screen_snip", {})
    img = Image.open(io.BytesIO(base64.b64decode(blocks[0]["data"])))
    assert max(img.size) == context_server._SNIP_MAX_DIMENSION


def test_tool_exception_becomes_iserror_result(monkeypatch):
    """A raising handler answers isError through the loop and serving continues."""
    def boom(_args):
        raise RuntimeError("capture backend unavailable")

    monkeypatch.setitem(context_server._HANDLERS, "get_clipboard", boom)
    monkeypatch.setattr(context_server, "_server_enabled", lambda: True)

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "get_clipboard", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    assert context_server.main() == 0
    replies = {msg["id"]: msg for msg in map(json.loads, stdout.getvalue().splitlines())}
    assert replies[1]["result"]["isError"] is True
    assert "capture backend unavailable" in replies[1]["result"]["content"][0]["text"]
    assert replies[2]["result"]["tools"]  # loop survived the failure


def test_self_check_names_missing_dependency(monkeypatch, capsys):
    """A missing capture package is reported on stderr with the tools it affects."""
    import importlib.util

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **kw: None if name == "PIL" else real_find_spec(name, *a, **kw),
    )
    context_server._self_check()
    err = capsys.readouterr().err
    assert "PIL" in err
    assert "take_screen_snip" in err


def test_client_pids_climbs_past_launch_trampolines(tmp_path):
    """The exclusion set must reach the real client app even when launch
    shims sit in between (Claude Desktop → venv python shim → python was the
    observed chain). Simulated here as: this test (grandparent) → middle
    python → inner python; the inner process must exclude both ancestors."""
    inner = tmp_path / "inner.py"
    inner.write_text(
        textwrap.dedent(
            """
            import sys
            sys.path.insert(0, sys.argv[3])
            from addons.mcp_bridge import context_server
            pids = context_server._client_pids()
            print(int(sys.argv[1]) in pids, int(sys.argv[2]) in pids)
            """
        ).strip(),
        encoding="utf-8",
    )
    middle = tmp_path / "middle.py"
    middle.write_text(
        textwrap.dedent(
            """
            import os, subprocess, sys
            out = subprocess.run(
                [sys.executable, sys.argv[1], sys.argv[2], str(os.getpid()), sys.argv[3]],
                capture_output=True, text=True, timeout=60,
            )
            sys.stderr.write(out.stderr)
            print(out.stdout.strip())
            """
        ).strip(),
        encoding="utf-8",
    )
    import os

    result = subprocess.run(
        [sys.executable, str(middle), str(inner), str(os.getpid()), str(REPO_ROOT)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    grandparent_in, parent_in = result.stdout.split()
    assert parent_in == "True"       # direct parent (the "shim")
    assert grandparent_in == "True"  # the client app above the shim


# ---------------------------------------------------------------------------
# Config snippet
# ---------------------------------------------------------------------------

def test_client_config_snippet_is_pasteable():
    """The generated snippet names this interpreter and an existing script."""
    data = json.loads(client_config_snippet())
    entry = data["mcpServers"]["wisp-context"]
    assert entry["command"] == sys.executable
    script = Path(entry["args"][0])
    assert script.is_absolute()
    assert script.exists()
    assert script.name == "context_server.py"


# ---------------------------------------------------------------------------
# Clipboard lock
# ---------------------------------------------------------------------------

_CHILD_TRY_LOCK = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[2])
    from core.system import clipboard_lock
    clipboard_lock.CLIPBOARD_LOCK_FILE = Path(sys.argv[1])
    with clipboard_lock.held(timeout=0.3) as acquired:
        print("acquired" if acquired else "unlocked")
    """
).strip()


def _run_child(script: Path, lock_file: Path) -> str:
    """Run a lock-probing child process and return its stdout."""
    result = subprocess.run(
        [sys.executable, str(script), str(lock_file), str(REPO_ROOT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_clipboard_lock_serializes_across_processes(tmp_path, monkeypatch):
    """A second process can't take the lock while we hold it, and can after."""
    lock_file = tmp_path / "clip.lock"
    child = tmp_path / "child.py"
    child.write_text(_CHILD_TRY_LOCK, encoding="utf-8")
    monkeypatch.setattr(clipboard_lock, "CLIPBOARD_LOCK_FILE", lock_file)

    with clipboard_lock.held(timeout=1.0) as acquired:
        assert acquired
        assert _run_child(child, lock_file) == "unlocked"
    assert _run_child(child, lock_file) == "acquired"


def test_clipboard_lock_freed_when_holder_dies(tmp_path, monkeypatch):
    """The OS releases the advisory lock when the holding process exits."""
    lock_file = tmp_path / "clip.lock"
    holder = tmp_path / "holder.py"
    holder.write_text(
        textwrap.dedent(
            """
            import os, sys
            from pathlib import Path
            sys.path.insert(0, sys.argv[2])
            from core.system import clipboard_lock
            fh = open(sys.argv[1], "a+")
            assert clipboard_lock._try_lock(fh)
            os._exit(0)  # exit while still holding the lock
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(clipboard_lock, "CLIPBOARD_LOCK_FILE", lock_file)

    _run_child(holder, lock_file)
    with clipboard_lock.held(timeout=1.0) as acquired:
        assert acquired


def test_clipboard_lock_fails_open_on_contention(tmp_path, monkeypatch):
    """Past the timeout the block still runs — capture never breaks on the lock."""
    lock_file = tmp_path / "clip.lock"
    monkeypatch.setattr(clipboard_lock, "CLIPBOARD_LOCK_FILE", lock_file)
    ran = False
    with clipboard_lock.held(timeout=1.0) as outer:
        assert outer
        # same-process second handle contends just like a foreign process
        with clipboard_lock.held(timeout=0.1) as inner:
            ran = True
            assert inner is False
    assert ran


# ---------------------------------------------------------------------------
# capture.get_selected_text(allow_synthetic_copy=False)
# ---------------------------------------------------------------------------

def test_allow_synthetic_copy_false_skips_clipboard_fallback(monkeypatch):
    """The keystroke fallback must never run when synthetic copy is disallowed."""
    from core import capture

    monkeypatch.setattr(capture, "_get_selected_text_uia", lambda: None)
    monkeypatch.setattr(capture, "_get_primary_selection_linux", lambda **kw: None)

    def forbidden():
        raise AssertionError("clipboard fallback must not run")

    monkeypatch.setattr(capture, "_get_selected_text_clipboard", forbidden)
    assert capture.get_selected_text(allow_synthetic_copy=False) is None


def test_allow_synthetic_copy_default_still_uses_fallback(monkeypatch):
    """Wisp's own behavior is unchanged: the fallback still runs by default."""
    from core import capture

    monkeypatch.setattr(capture, "_get_selected_text_uia", lambda: None)
    monkeypatch.setattr(capture, "_get_primary_selection_linux", lambda **kw: None)
    monkeypatch.setattr(capture, "_get_selected_text_clipboard", lambda: "fell back")
    assert capture.get_selected_text() == "fell back"
