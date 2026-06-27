"""Route failover + circuit breaker for LLM providers.

A small, self-contained registry: when a provider/model route returns a
quota/5xx/no-content error it is parked ("cooling") for a cooldown window so
later turns skip straight to a fallback instead of re-probing a dead route.
Split out of core.llm_clients.client; the names are re-exported there so the
module-level cooldown state stays a single shared object.
"""
from __future__ import annotations

import threading as _threading
import time as _time

# Route circuit breaker: when a route returns 429/5xx/no-content, it is parked for this many
# seconds so subsequent turns skip straight to the fallback instead of
# re-probing an exhausted model on every reply. After it expires the route is
# tried again (some retries, not an infinite skip).
_ROUTE_COOLDOWN_SECONDS = 300.0

_route_cooldowns: dict[tuple[str, str], float] = {}

_route_cooldowns_lock = _threading.Lock()

def _route_key(provider: str, model: str) -> tuple[str, str]:
    """Handle route key for LLM clients client."""
    return ((provider or "").lower(), model or "")

def _is_route_cooling(provider: str, model: str) -> bool:
    """Return whether route cooling is true."""
    key = _route_key(provider, model)
    with _route_cooldowns_lock:
        until = _route_cooldowns.get(key)
        if until is None:
            return False
        if _time.time() >= until:
            _route_cooldowns.pop(key, None)
            return False
        return True

def _mark_route_cooling(provider: str, model: str, seconds: float = _ROUTE_COOLDOWN_SECONDS) -> None:
    """Handle mark route cooling for LLM clients client."""
    with _route_cooldowns_lock:
        _route_cooldowns[_route_key(provider, model)] = _time.time() + seconds

def _is_quota_error(exc: Exception) -> bool:
    """True for 429 / rate-limit / quota-exhausted errors worth a cooldown."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    name = type(exc).__name__.lower()
    if "ratelimit" in name:
        return True
    text = str(exc).lower()
    return "429" in text or "quota" in text or "rate limit" in text or "rate_limit" in text

def _is_transient_route_error(exc: Exception) -> bool:
    """True for provider-side temporary failures worth trying/skipping fallback."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in {429, 500, 502, 503, 504}:
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "quota",
            "rate limit",
            "rate_limit",
            "unavailable",
            "high demand",
            "temporarily",
            "try again later",
        )
    )

def _route_failure_summary(
    kind: str,
    attempts: list[tuple[str, str, Exception | str]],
    last_exc: Exception,
) -> RuntimeError:
    """Handle route failure summary for LLM clients client."""
    details = []
    for provider, model, err in attempts:
        details.append(f"{provider}/{model}: {err}")
    joined = "; ".join(details)
    return RuntimeError(f"All {kind} model routes failed. Tried {joined}")
