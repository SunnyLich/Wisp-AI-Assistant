"""Tests for the MCP bridge addon."""

from __future__ import annotations

import sys
import textwrap

import pytest

from addons.mcp_bridge import MCPStdioClient


def test_mcp_bridge_tolerates_cp1252_bytes_in_server_output(tmp_path):
    """Verify MCP bridge tolerates cp1252 bytes in server output."""
    server = tmp_path / "legacy_mcp_server.py"
    server.write_text(
        textwrap.dedent(
            """
            import json
            import sys

            while True:
                line = sys.stdin.buffer.readline()
                if not line:
                    break
                msg = json.loads(line.decode("utf-8"))
                if "id" not in msg:
                    continue
                method = msg.get("method")
                if method == "initialize":
                    payload = {"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {}}}
                    sys.stdout.buffer.write(json.dumps(payload).encode("utf-8") + b"\\n")
                elif method == "tools/list":
                    raw = (
                        b'{"jsonrpc":"2.0","id":'
                        + str(msg["id"]).encode("ascii")
                        + b',"result":{"tools":[{"name":"legacy","description":"old\\x97new",'
                        + b'"inputSchema":{"type":"object","properties":{}}}]}}\\n'
                    )
                    sys.stdout.buffer.write(raw)
                else:
                    payload = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
                    sys.stdout.buffer.write(json.dumps(payload).encode("utf-8") + b"\\n")
                sys.stdout.buffer.flush()
            """
        ).strip(),
        encoding="utf-8",
    )

    client = MCPStdioClient("legacy", sys.executable, [str(server)], timeout=2)
    try:
        client.start()
        tools = client.list_tools()
    finally:
        client.stop()

    assert tools[0]["name"] == "legacy"
    assert tools[0]["description"].startswith("old")
    assert tools[0]["description"].endswith("new")


def test_mcp_server_unavailable_and_protocol_errors_are_controlled(monkeypatch):
    """The bridge distinguishes an absent process from a JSON-RPC error response."""
    client = MCPStdioClient("offline", "missing-command", timeout=0.01)
    with pytest.raises(RuntimeError, match="not running"):
        client._send({"jsonrpc": "2.0", "method": "tools/list"})

    client._alive = True
    client._responses[1] = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32601, "message": "protocol method unavailable"},
    }
    monkeypatch.setattr(client, "_send", lambda _payload: None)
    with pytest.raises(RuntimeError, match="protocol method unavailable"):
        client._request("tools/list", {})
