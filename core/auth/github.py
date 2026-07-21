"""
core/github_auth.py - GitHub OAuth device authentication.

Tokens are stored only in the OS keychain via keyring.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
from collections.abc import Callable

import config
from core.system.native_locks import keychain_lock

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"

_KEYRING_SERVICE = "python-ai-overlay"
_KEYRING_ACCOUNT = "github-oauth"
_TOKEN_FILE = pathlib.Path(__file__).parent.parent / "private" / ".github_tokens.json"

# Public OAuth app client ID for this desktop app. GitHub device flow does not
# require a client secret, so this is safe to bundle once the OAuth app exists.
_BUNDLED_CLIENT_ID = "Ov23lir59v9aESWj9PYV"


class OAuthTokenStorageError(RuntimeError):
    """Raised when reusable OAuth tokens cannot be stored securely."""


def configured_client_id() -> str:
    """Return the bundled GitHub OAuth client ID, with optional user override."""
    return (
        getattr(config, "GITHUB_CLIENT_ID", "").strip()
        or getattr(config, "GITHUB_DEFAULT_CLIENT_ID", "").strip()
        or _BUNDLED_CLIENT_ID
    )


def has_configured_client_id() -> bool:
    """Return whether configured client id is available."""
    return bool(configured_client_id())


def _keyring_get() -> str | None:
    """Handle keyring get for auth github."""
    try:
        with keychain_lock():
            import keyring  # type: ignore
            return keyring.get_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
    except Exception:
        return None


def _keyring_set(value: str) -> bool:
    """Handle keyring set for auth github."""
    try:
        with keychain_lock():
            import keyring  # type: ignore
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, value)
        return True
    except Exception:
        return False


def _keyring_delete() -> None:
    """Handle keyring delete for auth github."""
    try:
        with keychain_lock():
            import keyring  # type: ignore
            keyring.delete_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
    except Exception:
        pass


def get_tokens() -> dict | None:
    """Return tokens."""
    raw = _keyring_get()
    if not raw and _TOKEN_FILE.exists():
        # Migrate the plaintext fallback used by older Wisp versions. Always
        # remove the file afterwards so keychain failure cannot preserve a
        # reusable OAuth credential on disk.
        try:
            legacy_raw = _TOKEN_FILE.read_text(encoding="utf-8")
            if legacy_raw and _keyring_set(legacy_raw) and _keyring_get() == legacy_raw:
                raw = legacy_raw
        finally:
            try:
                _TOKEN_FILE.unlink(missing_ok=True)
            except Exception:
                pass
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return None


def save_tokens(tokens: dict) -> None:
    """Save tokens in the OS keychain, or fail without storing them."""
    serialized = json.dumps(tokens)
    if not _keyring_set(serialized) or _keyring_get() != serialized:
        raise OAuthTokenStorageError(
            "GitHub sign-in succeeded, but its OAuth token could not be saved "
            "to the OS keychain. The token was not stored. Check your system "
            "keychain and try signing in again."
        )


def clear_tokens() -> None:
    """Clear tokens."""
    _keyring_delete()
    try:
        _TOKEN_FILE.unlink(missing_ok=True)
    except Exception:
        pass


_TOKEN_WARN_DAYS = 365


def get_valid_access_token() -> str | None:
    """Return valid access token."""
    tokens = get_tokens()
    if not tokens:
        return None
    token = tokens.get("access")
    if token and "saved_at" in tokens:
        age_days = (time.time() * 1000 - tokens["saved_at"]) / (1000 * 86400)
        if age_days > _TOKEN_WARN_DAYS:
            print(
                f"[github_auth] Warning: stored token is {age_days:.0f} days old "
                "and may have expired. Re-authenticate if API calls fail."
            )
    return token


def get_user_login() -> str | None:
    """Return user login."""
    tokens = get_tokens()
    user = tokens.get("user") if tokens else None
    return user.get("login") if isinstance(user, dict) else None


def _post_form(url: str, params: dict) -> dict:
    """Handle post form for auth github."""
    import requests  # type: ignore

    resp = requests.post(
        url,
        data={k: v for k, v in params.items() if v not in (None, "")},
        headers={
            "Accept": "application/json",
            "User-Agent": "python-ai-overlay",
        },
        timeout=30,
    )
    try:
        data = resp.json()
    except Exception:
        data = None
    if resp.status_code >= 400:
        if isinstance(data, dict) and data.get("error"):
            return data
        resp.raise_for_status()
    if isinstance(data, dict):
        return data
    resp.raise_for_status()
    return {}


def _oauth_error_message(data: dict) -> str:
    """Return a readable GitHub OAuth error message."""
    error = str(data.get("error") or "GitHub OAuth error")
    description = str(data.get("error_description") or "").strip()
    if error == "incorrect_client_credentials":
        hint = (
            "GitHub rejected the OAuth client ID. Check GITHUB_CLIENT_ID, or "
            "verify that the bundled OAuth app still exists."
        )
    elif error == "device_flow_disabled":
        hint = (
            "Device flow is disabled for this OAuth app. Enable device flow in "
            "the app's GitHub settings or use a client ID with device flow enabled."
        )
    else:
        hint = ""
    parts = [description or error]
    if hint and hint not in parts[0]:
        parts.append(hint)
    return " ".join(parts)


def _get_json(url: str, token: str) -> dict:
    """Return json."""
    import requests  # type: ignore

    resp = requests.get(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "python-ai-overlay",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _tokens_from_raw(raw: dict) -> dict:
    """Handle tokens from raw for auth github."""
    access = raw["access_token"]
    user = _get_json(_USER_URL, access)
    return {
        "access": access,
        "token_type": raw.get("token_type", "bearer"),
        "scope": raw.get("scope", ""),
        "user": {
            "login": user.get("login"),
            "id": user.get("id"),
            "name": user.get("name"),
        },
        "saved_at": int(time.time() * 1000),
    }


def start_device_login(
    on_code: Callable[[str, str], None],
    on_success: Callable[[dict], None],
    on_error: Callable[[str], None],
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    """
    Start GitHub OAuth device flow in a background daemon thread.

    The GitHub OAuth app must have device flow enabled. The app client ID is
    bundled by default, with GITHUB_CLIENT_ID as an optional override.
    """

    def _run() -> None:
        """Drive the GitHub device OAuth flow on the background thread."""
        cancelled = is_cancelled or (lambda: False)
        if cancelled():
            return
        client_id = configured_client_id()
        if not client_id:
            on_error(
                "This build does not include a GitHub OAuth app client ID yet. "
                "Register one once for Wisp and bundle its public client ID."
            )
            return

        try:
            device = _post_form(
                _DEVICE_CODE_URL,
                {
                    "client_id": client_id,
                    "scope": getattr(config, "GITHUB_OAUTH_SCOPES", "").strip(),
                },
            )
        except Exception as exc:
            on_error(f"Device auth initiation failed: {exc}")
            return

        if "error" in device:
            on_error(_oauth_error_message(device))
            return

        verification_uri = device["verification_uri"]
        user_code = device["user_code"]
        device_code = device["device_code"]
        expires_at = time.time() + int(device.get("expires_in", 900))
        interval = max(int(device.get("interval", 5)), 1)

        if cancelled():
            return
        on_code(verification_uri, user_code)

        while time.time() < expires_at:
            if cancelled():
                return
            time.sleep(interval)
            if cancelled():
                return
            try:
                data = _post_form(
                    _ACCESS_TOKEN_URL,
                    {
                        "client_id": client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
            except Exception as exc:
                on_error(f"Token polling failed: {exc}")
                return

            error = data.get("error")
            if not error:
                try:
                    tokens = _tokens_from_raw(data)
                    if cancelled():
                        return
                    save_tokens(tokens)
                    on_success(tokens)
                except Exception as exc:
                    on_error(f"Token validation failed: {exc}")
                return
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval = int(data.get("interval", interval + 5))
                continue
            on_error(_oauth_error_message(data))
            return

        on_error("GitHub device code expired. Start sign-in again.")

    threading.Thread(target=_run, daemon=True, name="github-oauth-device").start()
