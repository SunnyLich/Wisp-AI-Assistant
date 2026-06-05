"""Shared pytest setup for the wisp_brain sidecar tests.

Puts the brain package dir (``macos/brain``), the repo root (so ``core`` and
``config`` import), and this ``tests`` dir (so the integration test can import
the ``BrainSidecar`` harness from ``test_brain_host``) on ``sys.path``. This runs
before collection, so individual test files don't each have to re-derive paths.

It also exposes a couple of tiny helpers reused across the per-handler tests:
``record_ctx`` (a real ``StreamContext`` whose events are captured in a list) and
``call_stream`` (invoke a streaming handler and return its result + events).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_BRAIN_DIR = _TESTS_DIR.parent              # macos/brain
_REPO_ROOT = _TESTS_DIR.parents[2]          # repo root (has core/, config.py)

for _p in (str(_BRAIN_DIR), str(_REPO_ROOT), str(_TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def record_ctx():
    """Factory for a StreamContext that records emitted (event, data) pairs.

    Usage::

        events, ctx = record_ctx()
        handlers.HANDLERS["brain.echo"](ctx, text="a b")
        assert ("reply.done", {"text": "a b"}) in events
    """
    from wisp_brain.handlers import StreamContext

    def _make(req_id: Any = 1):
        events: list[tuple[str, Any]] = []
        ctx = StreamContext(lambda event, data, _rid: events.append((event, data)), req_id)
        return events, ctx

    return _make
