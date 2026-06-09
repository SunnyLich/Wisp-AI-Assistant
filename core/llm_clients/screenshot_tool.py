"""Screenshot tool helpers for LLM tool loops.

The pure-Python supervisor should pass a native-worker screenshot here. The
direct capture fallback only exists for legacy callers that use the LLM client
without the supervisor boundary.
"""

from __future__ import annotations


def resolve_capture_screen_b64(provided_b64: str | None = None) -> str | None:
    """Return a base64 PNG for the capture_screen tool, or None on failure."""
    if provided_b64 is not None:
        if provided_b64:
            print("[llm] capture_screen using supervisor-provided screenshot", flush=True)
            return provided_b64
        print("[llm] capture_screen unavailable: supervisor pre-capture failed", flush=True)
        return None
    try:
        from core import capture

        return capture.image_to_base64(capture.get_screen_snippet())
    except Exception as exc:  # noqa: BLE001
        print(f"[llm] capture_screen failed: {exc}", flush=True)
        return None
