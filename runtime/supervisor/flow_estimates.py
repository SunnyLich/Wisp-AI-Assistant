"""Token, image, and file helpers for supervisor flows."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


def estimate_context_tokens(text: str) -> int:
    """Fast token estimate for context preview chips."""
    cjk = 0
    for ch in text or "":
        code = ord(ch)
        if (
            0x3040 <= code <= 0x30FF
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xAC00 <= code <= 0xD7AF
            or 0xFF00 <= code <= 0xFFEF
        ):
            cjk += 1
    return max(0, round(cjk * 0.85 + (len(text or "") - cjk) / 4))


def token_label(text: str) -> str:
    """Return a compact token estimate label."""
    tokens = estimate_context_tokens(text)
    if tokens <= 0:
        return "0 tok"
    if tokens >= 1000:
        return f"~{tokens / 1000:.1f}k tok"
    return f"~{tokens} tok"


def deferred_token_label() -> str:
    """Return the token label for context fetched after the picker."""
    return "? tok"


def image_size_from_b64(data: str | None) -> tuple[int, int] | None:
    """Best-effort PNG/JPEG dimension read for screenshot token estimates."""
    if not data:
        return None
    try:
        raw = base64.b64decode(data, validate=False)
    except Exception:
        return None
    if len(raw) >= 24 and raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    if len(raw) >= 4 and raw[:2] == b"\xff\xd8":
        idx = 2
        while idx + 9 < len(raw):
            if raw[idx] != 0xFF:
                idx += 1
                continue
            marker = raw[idx + 1]
            idx += 2
            if marker in {0xD8, 0xD9}:
                continue
            if idx + 2 > len(raw):
                break
            size = int.from_bytes(raw[idx:idx + 2], "big")
            if size < 2 or idx + size > len(raw):
                break
            if 0xC0 <= marker <= 0xC3 and idx + 7 < len(raw):
                return int.from_bytes(raw[idx + 5:idx + 7], "big"), int.from_bytes(raw[idx + 3:idx + 5], "big")
            idx += size
    return None


def image_size_token_label(size: tuple[int, int] | None) -> str:
    """Return a rough token estimate for an image of known dimensions."""
    if not size:
        return deferred_token_label()
    width, height = size
    if width <= 0 or height <= 0:
        return deferred_token_label()
    scale = min(1.0, 2048 / max(width, height))
    width = max(1, round(width * scale))
    height = max(1, round(height * scale))
    if min(width, height) > 768:
        scale = 768 / min(width, height)
        width = max(1, round(width * scale))
        height = max(1, round(height * scale))
    tiles = max(1, ((width + 511) // 512) * ((height + 511) // 512))
    tokens = 85 + 170 * tiles
    if tokens >= 1000:
        return f"~{tokens / 1000:.1f}k tok"
    return f"~{tokens} tok"


def image_token_label(data: str | None) -> str:
    """Return a rough token estimate for image input."""
    return image_size_token_label(image_size_from_b64(data))


def screen_token_label(context: dict[str, Any]) -> str:
    """Return screenshot token estimate from screen metadata."""
    raw = context.get("screen_size") if isinstance(context, dict) else {}
    if not isinstance(raw, dict):
        return deferred_token_label()
    try:
        width = int(raw.get("width") or 0)
        height = int(raw.get("height") or 0)
    except (TypeError, ValueError):
        return deferred_token_label()
    return image_size_token_label((width, height))


def file_b64(path: str | Path | None) -> str | None:
    """Return a file's base64 content, or None when no readable path exists."""
    from core.attachment_source import IMAGE_SUFFIXES, read_attachment_source

    data, _error = read_attachment_source(
        path,
        max_bytes=10 * 1024 * 1024,
        allowed_suffixes=IMAGE_SUFFIXES,
    )
    return base64.b64encode(data).decode("ascii") if data else None
