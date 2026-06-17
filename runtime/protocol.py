"""Newline-delimited JSON protocol used by all pure-Python runtime workers."""

from __future__ import annotations

from typing import Any

from core.macos_helper.protocol import read_message, write_message


def make_request(req_id: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create request."""
    return {"id": req_id, "method": method, "params": params or {}}


def make_response(req_id: Any, ok: bool, *, result: Any = None, error: str | None = None) -> dict[str, Any]:
    """Create response."""
    msg: dict[str, Any] = {"id": req_id, "ok": ok}
    if ok:
        msg["result"] = result
    else:
        msg["error"] = error or "unknown error"
    return msg


def make_event(event: str, *, data: Any = None, req_id: Any = None) -> dict[str, Any]:
    """Create event."""
    return {"event": event, "id": req_id, "data": data}
