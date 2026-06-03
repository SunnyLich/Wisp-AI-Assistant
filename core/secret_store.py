"""Small OS-keychain wrapper for provider API keys."""
from __future__ import annotations

import os
import json
import logging
import threading
from pathlib import Path

log = logging.getLogger("wisp.secrets")

# Cache keychain reads. On macOS, keyring goes through the Security framework
# (find_generic_password); calling it repeatedly from worker threads — e.g. the
# per-query route logging on the TTS/LLM threads — has been observed to segfault
# when it races other native framework work (CoreAudio). config.reload() reads
# every key at startup on the main thread, which warms this cache, so subsequent
# lookups never touch the Security framework off the main thread.
_keychain_cache: dict[str, str] = {}
_keychain_cache_lock = threading.Lock()

_KEYRING_SERVICE = "python-ai-overlay"
_META_FILE = Path(__file__).parent.parent / "private" / ".secret_status.json"

API_KEY_NAMES = (
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "CARTESIA_API_KEY",
    "ELEVENLABS_API_KEY",
    "CUSTOM_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "XAI_API_KEY",
    "TOGETHER_API_KEY",
    "CEREBRAS_API_KEY",
)


class KeychainError(RuntimeError):
    """Raised when an OS-keychain write cannot be completed or verified."""


def _account(name: str) -> str:
    return name.lower()


def get_secret(name: str) -> str:
    """Return a secret from the OS keychain, falling back to env during migration."""
    value = get_keychain_secret(name)
    if value:
        return value
    return os.getenv(name, "")


def get_keychain_secret(name: str) -> str:
    """Return a secret only from the OS keychain (cached after first read)."""
    with _keychain_cache_lock:
        if name in _keychain_cache:
            return _keychain_cache[name]

    value = ""
    try:
        import keyring  # type: ignore

        result = keyring.get_password(_KEYRING_SERVICE, _account(name))
        if result:
            value = result
    except Exception:
        pass

    with _keychain_cache_lock:
        _keychain_cache[name] = value
    return value


def has_secret(name: str) -> bool:
    return bool(get_secret(name)) or configured_marker(name)


def secret_source(name: str) -> str:
    """Return where a secret would be read from, without returning the secret."""
    if get_keychain_secret(name):
        return "keychain"
    if os.getenv(name, ""):
        return "env"
    return "none"


def set_secret(name: str, value: str) -> None:
    """Write a secret to the OS keychain and verify it landed.

    The "configured" marker is only set after a successful read-back, so a
    silently-dropped write can no longer leave a stale marker without a stored
    value. Raises KeychainError (after logging) on any failure.
    """
    try:
        import keyring  # type: ignore
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller + log
        log.error("Cannot save %s: OS keychain support (keyring) is unavailable: %s", name, exc)
        raise KeychainError(
            "OS keychain support (the 'keyring' package) is not available, "
            f"so {name} could not be saved."
        ) from exc

    try:
        keyring.set_password(_KEYRING_SERVICE, _account(name), value)
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller + log
        log.error("Failed writing %s to OS keychain: %s", name, exc)
        raise KeychainError(f"Could not write {name} to the OS keychain: {exc}") from exc

    # Drop the cached value so the read-back below (and future reads) see the
    # freshly written secret rather than a stale cache entry.
    with _keychain_cache_lock:
        _keychain_cache.pop(name, None)

    # Read back to confirm the value actually persisted before trusting it.
    if get_keychain_secret(name) != value:
        log.error("Verification failed for %s: keychain read-back did not match the value written", name)
        set_configured_marker(name, False)
        raise KeychainError(
            f"{name} did not persist to the OS keychain (verification read-back failed)."
        )

    set_configured_marker(name, True)
    log.info("Saved %s to OS keychain", name)


def delete_secret(name: str) -> None:
    try:
        import keyring  # type: ignore

        keyring.delete_password(_KEYRING_SERVICE, _account(name))
    except Exception:
        pass
    set_configured_marker(name, False)


def migrate_env_secrets(env: dict[str, str]) -> list[str]:
    """
    Copy any existing .env API keys into the OS keychain.
    Returns the names that were migrated.
    """
    migrated: list[str] = []
    for name in API_KEY_NAMES:
        value = (env.get(name) or "").strip()
        if not value:
            continue
        if get_keychain_secret(name):
            continue
        set_secret(name, value)
        migrated.append(name)
    return migrated


def configured_marker(name: str) -> bool:
    return bool(_read_meta().get(name))


def set_configured_marker(name: str, configured: bool) -> None:
    data = _read_meta()
    if configured:
        data[name] = True
    else:
        data.pop(name, None)
    _META_FILE.parent.mkdir(parents=True, exist_ok=True)
    _META_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_meta() -> dict:
    try:
        return json.loads(_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
