"""User-facing error messages with actionable recommendations."""
from __future__ import annotations

from core.privacy_redaction import redact_text


_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("api key", "missing key", "no api key", "unauthorized", "401"),
        "Recommendation: add or refresh the provider API key in Settings, then run Setup Check.",
    ),
    (
        ("invalid model", "model_not_found", "unknown model", "404"),
        "Recommendation: choose a supported model in Settings or refresh the provider model list.",
    ),
    (
        ("timeout", "timed out", "network", "connection", "dns", "temporarily unavailable"),
        "Recommendation: check your network/provider status, then retry or switch to a fallback model.",
    ),
    (
        ("tts", "no audio", "audio.tts", "synthesize"),
        "Recommendation: check the TTS provider, voice, and API key, then play a sample in Setup Check.",
    ),
    (
        ("microphone", "recording", "dictation", "stt", "speech", "transcribe"),
        "Recommendation: check microphone permission/input device, then run the speech check in Settings.",
    ),
    (
        ("hotkey", "registerhotkey", "shortcut"),
        "Recommendation: choose a different hotkey in Settings or close the app currently using it.",
    ),
    (
        ("screenshot", "screen recording", "capture", "snip"),
        "Recommendation: grant screenshot/screen-recording permission, then rerun Setup Check.",
    ),
    (
        ("addon", "plugin"),
        "Recommendation: open Addon Manager, inspect the addon diagnostics, then repair or disable it.",
    ),
)


def recommendation_for(message: str) -> str:
    """Return the best recommendation for a user-facing error message."""
    lower = str(message or "").lower()
    for needles, recommendation in _RULES:
        if any(needle in lower for needle in needles):
            return recommendation
    return ""


def format_error(message: str, *, technical_detail: str = "") -> str:
    """Return a redacted message followed by a recommendation when known."""
    base = redact_text(str(message or "").strip())
    detail = redact_text(str(technical_detail or "").strip())
    if "Recommendation:" in base:
        parts = [base]
        if detail and detail != base:
            parts.append(f"Technical detail: {detail}")
        return "\n\n".join(parts)
    recommendation = recommendation_for(f"{base}\n{detail}")
    parts = [part for part in (base, recommendation) if part]
    if detail and detail != base:
        parts.append(f"Technical detail: {detail}")
    return "\n\n".join(parts)
