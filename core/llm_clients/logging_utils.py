"""Structured logging helpers for LLM client code."""
from __future__ import annotations

import logging
from typing import Any

LOGGER_NAME = "wisp.llm"


def get_logger() -> logging.Logger:
    """Return logger."""
    return logging.getLogger(LOGGER_NAME)


def log_event(
    event: str,
    message: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Log an LLM event with stable extra fields for searchable runtime logs."""
    get_logger().log(level, message, extra={"event": event, **fields})

def log_context(
    reason: str,
    text: str,
    max_line: int = 120,
    max_lines: int = 12,
    max_chars: int = 1200,
) -> None:
    """Log a compact preview of a context block for debugging."""

    def _trim(line: str) -> str:
        """Handle trim for LLM clients client."""
        return line if len(line) <= max_line else line[:max_line] + "-¦"

    lines = [_trim(line) for line in text.splitlines() if line.strip()]
    truncated = False

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True

    body = "\n  ".join(lines) if lines else "[empty]"
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "-¦"
        truncated = True
    if truncated and body != "[empty]":
        body += "\n  [preview truncated]"

    log_event(
        "llm.context_preview",
        f"Context preview for {reason}:\n  {body}",
        reason=reason,
        preview=body,
        truncated=truncated,
    )
