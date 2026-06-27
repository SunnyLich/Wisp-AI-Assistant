"""Small pure utility helpers for supervisor flows."""
from __future__ import annotations

import json
import re
import sys
from typing import Any

_LOCAL_FILE_ACTION_RE = re.compile(
    r"\b(?:append|change|create|edit|fix|modify|patch|replace|save|update|write)\b",
    re.IGNORECASE,
)
_LOCAL_FILE_TARGET_RE = re.compile(
    r"\b(?:file|folder|local\s+file|path|project|workspace)\b",
    re.IGNORECASE,
)
_LOCAL_FILE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s]+|[^\s]+\.(?:cfg|css|csv|html|ini|js|json|log|md|py|toml|ts|txt|xml|yaml|yml))",
    re.IGNORECASE,
)


def friendly_error(exc: Exception) -> str:
    """Return a concise exception message for user-facing notices."""
    text = str(exc).strip() or type(exc).__name__
    for prefix in ("ValueError: ", "RuntimeError: "):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text


def paste_shortcut() -> str:
    """Return the platform paste shortcut label."""
    return "Cmd+V" if sys.platform == "darwin" else "Ctrl+V"


def is_local_file_request(prompt: str) -> bool:
    """Return True when a paste-back prompt is really asking for disk edits."""
    text = str(prompt or "")
    if not _LOCAL_FILE_ACTION_RE.search(text):
        return False
    return bool(_LOCAL_FILE_TARGET_RE.search(text) or _LOCAL_FILE_PATH_RE.search(text))


def short(text: str, n: int = 24) -> str:
    """Return a single-line shortened label."""
    flat = " ".join((text or "").split())
    return (flat[: n - 1] + "...") if len(flat) > n else flat


def json_safe_dumps(value: Any) -> str:
    """Serialize a JSON-like value, falling back to str for unsupported objects."""
    try:
        return json.dumps(value, ensure_ascii=True)
    except TypeError:
        return str(value)
