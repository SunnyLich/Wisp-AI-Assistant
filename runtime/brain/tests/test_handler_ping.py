"""Unit tests for the ``ping`` handler (liveness / round-trip)."""
from __future__ import annotations

import os

from wisp_brain import handlers


def test_ping_is_registered_and_not_streaming():
    """Verify ping is registered and not streaming behavior."""
    assert "ping" in handlers.HANDLERS
    assert "ping" not in handlers.STREAMING


def test_ping_echoes_value_and_reports_pid():
    """Verify ping echoes value and reports pid behavior."""
    result = handlers.HANDLERS["ping"](value={"hello": 1})
    assert result["pong"] is True
    assert result["value"] == {"hello": 1}
    assert result["pid"] == os.getpid()


def test_ping_defaults_value_to_none():
    """Verify ping defaults value to none behavior."""
    result = handlers.ping()
    assert result["pong"] is True
    assert result["value"] is None
