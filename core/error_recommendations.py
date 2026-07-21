"""User-facing error messages with actionable recommendations."""
from __future__ import annotations

from core.privacy_redaction import redact_text

_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("required user input is empty", "no input provided", "prompt is empty"),
        "Recommendation: enter a prompt or select text, then try again.",
    ),
    (
        ("required context is empty", "no selected text", "context is empty"),
        "Recommendation: select the source text or enable a context source, then retry.",
    ),
    (
        ("request is cancelled", "request canceled", "request cancelled"),
        "Recommendation: start the request again when you are ready.",
    ),
    (
        ("cannot be rendered", "render failed", "render data is invalid"),
        "Recommendation: reopen the conversation and retry; restart Wisp if the display remains unavailable.",
    ),
    (
        ("cannot be pasted", "paste into the target", "paste failed"),
        "Recommendation: return focus to the target field and retry, or paste the preserved clipboard text manually.",
    ),
    (
        ("api key", "missing key", "no api key", "unauthorized", "401"),
        "Recommendation: add or refresh the provider API key in Settings, then run Setup Check.",
    ),
    (
        ("invalid model", "model_not_found", "unknown model", "404"),
        "Recommendation: choose a supported model in Settings or refresh the provider model list.",
    ),
    (
        ("rate limit", "rate-limit", "429", "too many requests", "overloaded", "quota"),
        "Recommendation: wait a minute and retry, or check the provider's usage limits and billing.",
    ),
    (
        ("insufficient credit", "billing", "payment required", "402"),
        "Recommendation: check the provider account's billing/credits, then retry.",
    ),
    (
        ("ssl", "certificate", "cert verify"),
        "Recommendation: check the system clock, proxy, or VPN; corporate proxies often break TLS certificates.",
    ),
    (
        ("no space left", "disk full", "errno 28", "not enough space"),
        "Recommendation: free up disk space, then retry the operation.",
    ),
    (
        ("permission denied", "access is denied", "errno 13", "winerror 5"),
        "Recommendation: check file/folder permissions, or close another program locking the file, then retry.",
    ),
    (
        ("no module named", "modulenotfounderror", "importerror", "dll load failed"),
        "Recommendation: the package install looks incomplete - reinstall it from Settings, then restart Wisp.",
    ),
    (
        ("cuda", "cudnn", "cublas", "vram", "gpu"),
        "Recommendation: update the GPU driver or reinstall the CUDA build, or switch the device to CPU in Settings.",
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
    (
        ("worker is unresponsive", "worker stopped responding", "worker heartbeat"),
        "Recommendation: restart Wisp, then open Runtime Status and inspect the worker log if it happens again.",
    ),
)

_DEFAULT_RECOMMENDATION = (
    "Recommendation: retry once, then open Runtime Status and create a crash report "
    "with recent logs if the failure continues."
)


def recommendation_for(message: str) -> str:
    """Return the best recommendation for a user-facing error message."""
    lower = str(message or "").lower()
    for needles, recommendation in _RULES:
        if any(needle in lower for needle in needles):
            return recommendation
    return _DEFAULT_RECOMMENDATION


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
