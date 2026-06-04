"""
core.macos_helper.protocol — newline-delimited JSON framing.

Both the parent (client.py) and the worker (host.py) speak this. One JSON object
per line, UTF-8, terminated by ``\\n``. Three message shapes:

  request   {"id": int, "method": str, "params": {...}}
  response  {"id": int, "ok": bool, "result": <any> | "error": str}
  event     {"event": str, "data": <any>}            # worker → parent, unsolicited

JSON keeps the foundation simple and debuggable. Large binary payloads (e.g. PCM
for the future audio stage) should NOT be base64'd through here on the hot path;
when that stage lands it gets a dedicated length-prefixed binary frame type. For
STT — where only a short transcript crosses back — JSON is plenty.
"""
from __future__ import annotations

import json
from typing import Any, BinaryIO


def write_message(stream: BinaryIO, obj: dict[str, Any]) -> None:
    """Serialize *obj* as one JSON line and flush it to *stream* (a binary file)."""
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    stream.write(data + b"\n")
    stream.flush()


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    """Read one JSON line from *stream*. Returns the decoded dict, or None on EOF.

    A line that is not valid JSON is skipped (returns the special sentinel via a
    recursive read) so a stray non-protocol write on the channel cannot wedge the
    reader — callers loop on this and treat None as "peer closed".
    """
    while True:
        line = stream.readline()
        if not line:
            return None  # EOF — peer closed the pipe
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # Not a protocol line (e.g. a library wrote to the wrong fd). Skip it.
            continue
