"""Bridge UI-side log surfaces into the supervisor's runtime event log.

The UI worker process hosts surfaces (Settings install status lines, dialogs)
whose messages used to exist only on screen. The UI host registers an emitter
at startup; any UI code can then call :func:`log_event` and the message shows
up in the Runtime Status window. Without an emitter (standalone dialogs,
tests) calls are silent no-ops.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_emitter: Callable[[dict[str, Any]], None] | None = None


def set_emitter(emitter: Callable[[dict[str, Any]], None] | None) -> None:
    """Register the callable that forwards events to the supervisor."""
    global _emitter
    _emitter = emitter


def log_event(source: str, severity: str, title: str, detail: str = "") -> None:
    """Report one log event (best-effort; never raises)."""
    emitter = _emitter
    if emitter is None or not str(title or "").strip():
        return
    try:
        emitter(
            {
                "source": str(source or "ui"),
                "severity": str(severity or "info"),
                "title": str(title),
                "detail": str(detail or ""),
            }
        )
    except Exception:  # noqa: BLE001 - logging must never break the UI
        pass
