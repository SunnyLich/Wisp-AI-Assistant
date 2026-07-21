"""
core/chatgpt_auth.py — ChatGPT Pro/Plus OAuth authentication.

Implements PKCE browser flow and device-code flow against OpenAI's auth
endpoints, mirroring the approach used by opencode (MIT licence,
https://github.com/anomalyco/opencode).

Tokens are stored in the OS keychain via keyring — never in plaintext files.

Typical usage
-------------
# Browser flow (opens system browser):
chatgpt_auth.start_browser_login(on_success=..., on_error=...)

# Headless / device-code flow:
chatgpt_auth.start_device_login(on_code=..., on_success=..., on_error=...)

# Obtain a valid (auto-refreshed) access token:
token = chatgpt_auth.get_valid_access_token()   # None if not logged in

# Check login status:
info = chatgpt_auth.get_tokens()   # dict or None
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import secrets
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from core.system.native_locks import keychain_lock

log = logging.getLogger("wisp.chatgpt_auth")

# ---------------------------------------------------------------------------
# Constants (sourced from opencode's built-in Codex plugin, MIT)
# ---------------------------------------------------------------------------

_CLIENT_ID    = "app_EMoamEEZ73f0CkXaXp7hrann"
_ISSUER       = "https://auth.openai.com"
_OAUTH_PORT   = 1455
_REDIRECT_URI = f"http://localhost:{_OAUTH_PORT}/auth/callback"

_KEYRING_SERVICE = "python-ai-overlay"
_KEYRING_ACCOUNT = "chatgpt-oauth"
_KEYRING_CHUNK_SIZE = 900
_KEYRING_MAX_CHUNKS = 32
_USE_CHUNKED_KEYRING = sys.platform == "win32"
_CHUNK_MANIFEST_KEY = "_wisp_oauth_chunks"

# Keep reads, rewrites, and deletes consecutive inside this process. Windows
# Credential Manager exposes individual credential operations, not a batch API.
_token_storage_lock = threading.RLock()

# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _generate_code_verifier() -> str:
    """Handle generate code verifier for auth chatgpt."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def _generate_code_challenge(verifier: str) -> str:
    """Handle generate code challenge for auth chatgpt."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _generate_state() -> str:
    """Handle generate state for auth chatgpt."""
    return base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# Token storage — OS keychain only
# ---------------------------------------------------------------------------

_TOKEN_FILE = Path(__file__).parent.parent / "private" / ".chatgpt_tokens.json"
_APP_ICON_FILE = Path(__file__).resolve().parents[2] / "assets" / "app.ico"


class OAuthTokenStorageError(RuntimeError):
    """Raised when reusable OAuth tokens cannot be stored securely."""


def _app_icon_data_uri() -> str:
    """Return the bundled Wisp app icon as an embeddable data URI."""
    try:
        return "data:image/x-icon;base64," + base64.b64encode(_APP_ICON_FILE.read_bytes()).decode("ascii")
    except Exception:
        return ""


def _keyring_get(account: str = _KEYRING_ACCOUNT) -> str | None:
    """Handle keyring get for auth chatgpt."""
    try:
        with keychain_lock():
            import keyring  # type: ignore
            return keyring.get_password(_KEYRING_SERVICE, account)
    except Exception:
        return None


def _keyring_set(value: str, account: str = _KEYRING_ACCOUNT) -> bool:
    """Handle keyring set for auth chatgpt."""
    try:
        with keychain_lock():
            import keyring  # type: ignore
            keyring.set_password(_KEYRING_SERVICE, account, value)
        return True
    except Exception as exc:
        log.error("Could not write ChatGPT OAuth credential %s: %s", account, exc)
        return False


def _keyring_delete(account: str = _KEYRING_ACCOUNT) -> None:
    """Handle keyring delete for auth chatgpt."""
    try:
        with keychain_lock():
            import keyring  # type: ignore
            keyring.delete_password(_KEYRING_SERVICE, account)
    except Exception:
        pass


def _chunk_account(index: int) -> str:
    """Return the keyring account used for one OAuth payload chunk."""
    return f"{_KEYRING_ACCOUNT}-chunk-{index}"


def _chunk_manifest(raw: str | None) -> dict | None:
    """Parse and validate a chunk manifest stored in the primary account."""
    if not raw:
        return None
    try:
        manifest = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get(_CHUNK_MANIFEST_KEY) != 1:
        return None
    count = manifest.get("count")
    length = manifest.get("length")
    digest = manifest.get("sha256")
    if not isinstance(count, int) or not 1 <= count <= _KEYRING_MAX_CHUNKS:
        return None
    if not isinstance(length, int) or length < 1 or not isinstance(digest, str):
        return None
    return manifest


def _read_keyring_payload() -> str | None:
    """Read either the legacy single-item payload or the current chunked form."""
    primary = _keyring_get()
    manifest = _chunk_manifest(primary)
    if manifest is None:
        return primary

    parts: list[str] = []
    for index in range(manifest["count"]):
        chunk = _keyring_get(_chunk_account(index))
        if chunk is None:
            log.warning("ChatGPT OAuth keychain chunk %d is missing", index)
            return None
        parts.append(chunk)

    payload = "".join(parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if len(payload) != manifest["length"] or digest != manifest["sha256"]:
        log.warning("ChatGPT OAuth keychain chunks failed verification")
        return None
    return payload


def _write_keyring_payload(payload: str) -> bool:
    """Write and verify an OAuth payload, chunking it on Windows."""
    if not _USE_CHUNKED_KEYRING:
        return _keyring_set(payload) and _keyring_get() == payload

    chunks = [payload[start : start + _KEYRING_CHUNK_SIZE] for start in range(0, len(payload), _KEYRING_CHUNK_SIZE)]
    if not chunks or len(chunks) > _KEYRING_MAX_CHUNKS:
        log.error("ChatGPT OAuth payload requires too many keychain chunks: %d", len(chunks))
        return False

    previous_manifest = _chunk_manifest(_keyring_get())
    previous_count = previous_manifest["count"] if previous_manifest else 0

    for index, chunk in enumerate(chunks):
        account = _chunk_account(index)
        if not _keyring_set(chunk, account) or _keyring_get(account) != chunk:
            return False

    manifest = json.dumps(
        {
            _CHUNK_MANIFEST_KEY: 1,
            "count": len(chunks),
            "length": len(payload),
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        },
        separators=(",", ":"),
    )
    if not _keyring_set(manifest) or _keyring_get() != manifest:
        return False

    for index in range(len(chunks), previous_count):
        _keyring_delete(_chunk_account(index))
    return _read_keyring_payload() == payload


def _delete_keyring_payload() -> None:
    """Delete the primary OAuth entry and every possible Windows chunk."""
    _keyring_delete()
    if _USE_CHUNKED_KEYRING:
        for index in range(_KEYRING_MAX_CHUNKS):
            _keyring_delete(_chunk_account(index))


def _get_tokens_unlocked() -> dict | None:
    """Return stored OAuth tokens dict or None if the user is not logged in."""
    raw = _read_keyring_payload()
    if not raw and _TOKEN_FILE.exists():
        # Versions before 0.10.2 could fall back to this plaintext file. Migrate
        # it once when the keychain is available, and remove it either way so a
        # failed keychain can never leave reusable credentials on disk.
        try:
            legacy_raw = _TOKEN_FILE.read_text(encoding="utf-8")
            if legacy_raw and _write_keyring_payload(legacy_raw):
                raw = legacy_raw
            elif legacy_raw:
                log.error("Discarded legacy plaintext ChatGPT OAuth tokens because the OS keychain is unavailable")
        except Exception as exc:
            log.warning("Could not migrate legacy ChatGPT OAuth tokens: %s", exc)
        finally:
            try:
                _TOKEN_FILE.unlink(missing_ok=True)
            except Exception as exc:
                log.warning("Could not remove legacy plaintext ChatGPT OAuth token file: %s", exc)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return None


def get_tokens() -> dict | None:
    """Return stored OAuth tokens while keeping chunk operations consecutive."""
    with _token_storage_lock:
        return _get_tokens_unlocked()


def _save_tokens_unlocked(tokens: dict) -> None:
    """Persist tokens in the OS keychain, or fail without storing them."""
    serialised = json.dumps(tokens, separators=(",", ":"))
    if not _write_keyring_payload(serialised):
        raise OAuthTokenStorageError(
            "ChatGPT sign-in succeeded, but its OAuth tokens could not be saved "
            "to the OS keychain. The tokens were not stored. Check your system "
            "keychain and try signing in again."
        )


def save_tokens(tokens: dict) -> None:
    """Persist tokens while keeping chunk operations consecutive."""
    with _token_storage_lock:
        _save_tokens_unlocked(tokens)


def _clear_tokens_unlocked() -> None:
    """Remove stored tokens."""
    _delete_keyring_payload()
    try:
        _TOKEN_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def clear_tokens() -> None:
    """Remove all stored token chunks and legacy fallback data."""
    with _token_storage_lock:
        _clear_tokens_unlocked()


# ---------------------------------------------------------------------------
# Token exchange / refresh
# ---------------------------------------------------------------------------

def _post_form(url: str, params: dict) -> dict:
    """Handle post form for auth chatgpt."""
    import urllib.request
    body = urlencode(params).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _post_json(url: str, payload: dict) -> dict:
    """Handle post json for auth chatgpt."""
    import urllib.request
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _exchange_code(code: str, verifier: str, redirect_uri: str) -> dict:
    """Handle exchange code for auth chatgpt."""
    return _post_form(f"{_ISSUER}/oauth/token", {
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  redirect_uri,
        "client_id":     _CLIENT_ID,
        "code_verifier": verifier,
    })


def _do_refresh(refresh_token: str) -> dict:
    """Handle do refresh for auth chatgpt."""
    return _post_form(f"{_ISSUER}/oauth/token", {
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
        "client_id":     _CLIENT_ID,
    })


def _parse_account_id(raw: dict) -> str | None:
    """Extract the ChatGPT account ID from JWT claims in id_token or access_token."""
    for key in ("id_token", "access_token"):
        token = raw.get(key, "")
        parts = token.split(".")
        if len(parts) != 3:
            continue
        try:
            padding = "=" * (4 - len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
            account_id = (
                claims.get("chatgpt_account_id")
                or (claims.get("https://api.openai.com/auth") or {}).get("chatgpt_account_id")
                or ((claims.get("organizations") or [{}])[0]).get("id")
            )
            if account_id:
                return account_id
        except Exception:
            pass
    return None


def _tokens_from_raw(raw: dict, existing: dict | None = None) -> dict:
    """Build the internal token dict from a raw token endpoint response."""
    return {
        "access":     raw["access_token"],
        "refresh":    raw.get("refresh_token") or (existing or {}).get("refresh", ""),
        "expires":    int(time.time() * 1000) + raw.get("expires_in", 3600) * 1000,
        "account_id": _parse_account_id(raw) or (existing or {}).get("account_id"),
    }


# ---------------------------------------------------------------------------
# Public token accessors
# ---------------------------------------------------------------------------

def _needs_refresh(tokens: dict) -> bool:
    """True if the access token expires within 60 seconds."""
    return tokens.get("expires", 0) < (time.time() + 60) * 1000


# Serializes token refresh. ChatGPT OAuth uses rotating, single-use refresh
# tokens with reuse detection: if two callers refresh with the same refresh
# token at once, OpenAI treats the second as a reuse and invalidates the whole
# credential — forcing a fresh sign-in. The lock ensures only one refresh runs;
# others wait and reuse the freshly-rotated token instead of spending it twice.
_refresh_lock = threading.Lock()


def get_valid_access_token() -> str | None:
    """
    Return a valid access token, transparently refreshing if it is near expiry.
    Returns None if the user is not logged in.
    """
    tokens = get_tokens()
    if not tokens:
        return None
    if not _needs_refresh(tokens):
        return tokens.get("access")

    with _refresh_lock:
        # Re-read under the lock: another caller may have refreshed while we
        # waited, in which case we use their rotated token rather than spending
        # our now-stale refresh token a second time.
        tokens = get_tokens() or tokens
        if not _needs_refresh(tokens):
            return tokens.get("access")
        try:
            raw = _do_refresh(tokens["refresh"])
            tokens = _tokens_from_raw(raw, existing=tokens)
            save_tokens(tokens)
        except Exception as exc:
            print(f"[chatgpt_auth] Token refresh failed: {exc}")
            return None

    return tokens.get("access")


def get_account_id() -> str | None:
    """Return the stored ChatGPT account ID, or None."""
    tokens = get_tokens()
    return tokens.get("account_id") if tokens else None


# ---------------------------------------------------------------------------
# Browser-based PKCE flow
# ---------------------------------------------------------------------------

def start_browser_login(
    on_success: Callable[[dict], None],
    on_error: Callable[[str], None],
) -> None:
    """
    Start the browser-based PKCE OAuth flow in a background daemon thread.

    The system browser is opened to OpenAI's authorize endpoint.
    A temporary HTTP server on port 1455 catches the redirect callback.
    ``on_success(tokens)`` / ``on_error(message)`` are called from that thread.
    """
    def _run() -> None:
        """Drive the browser PKCE OAuth flow on the background thread."""
        import webbrowser

        verifier  = _generate_code_verifier()
        challenge = _generate_code_challenge(verifier)
        state     = _generate_state()

        auth_params = urlencode({
            "response_type":            "code",
            "client_id":                _CLIENT_ID,
            "redirect_uri":             _REDIRECT_URI,
            "scope":                    "openid profile email offline_access",
            "code_challenge":           challenge,
            "code_challenge_method":    "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state":                    state,
            "originator":               "opencode",
        })
        auth_url = f"{_ISSUER}/oauth/authorize?{auth_params}"

        result: list = []      # filled by the request handler
        done   = threading.Event()

        class _Handler(BaseHTTPRequestHandler):
            """Model handler."""
            def log_message(self, *args):   # silence access log
                """Log message."""
                pass

            def do_GET(self):              # noqa: N802
                """Handle do g e t for handler."""
                parsed = urlparse(self.path)
                if parsed.path != "/auth/callback":
                    self.send_response(404)
                    self.end_headers()
                    return

                qs          = parse_qs(parsed.query)
                code        = (qs.get("code")  or [None])[0]
                got_state   = (qs.get("state") or [None])[0]
                error       = (qs.get("error") or [None])[0]
                error_desc  = (qs.get("error_description") or [None])[0]

                # Diagnostics: record which params came back so a failed sign-in
                # is debuggable. Never log the authorization code value itself.
                present = sorted(qs.keys())
                log.info("ChatGPT OAuth callback received; params=%s", present)

                if error:
                    msg = error_desc or error
                    log.warning("ChatGPT OAuth callback returned error: %s", msg)
                    result.append(("error", msg))
                    self._send_html(_html_error(msg))
                elif not code:
                    # No code and no error: the authorize step never produced one.
                    # Common for managed / SSO / ChatGPT Edu accounts that aren't
                    # entitled to the personal Codex login this flow uses.
                    log.warning("ChatGPT OAuth callback missing code; params=%s", present)
                    result.append((
                        "error",
                        "Sign-in did not return an authorization code. This usually "
                        "means the account isn't eligible for the ChatGPT login "
                        "(managed, SSO, or ChatGPT Edu/Team accounts often aren't) — "
                        "try a personal ChatGPT Plus/Pro account, or use an API key.",
                    ))
                    self._send_html(_html_error("Sign-in did not return an authorization code."))
                elif got_state != state:
                    # A code came back but the state doesn't match ours — typically an
                    # organization/workspace selection step rewrote the redirect.
                    log.warning(
                        "ChatGPT OAuth state mismatch (expected %s…, got %s…)",
                        state[:6], (got_state or "")[:6],
                    )
                    result.append((
                        "error",
                        "Sign-in state did not match — the login likely went through "
                        "an organization/SSO step. Try again and, if prompted, pick "
                        "your personal workspace rather than an institutional one.",
                    ))
                    self._send_html(_html_error("OAuth state mismatch"))
                else:
                    result.append(("code", code))
                    self._send_html(_html_success())
                done.set()

            def _send_html(self, html: str) -> None:
                """Send html."""
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            server = HTTPServer(("localhost", _OAUTH_PORT), _Handler)
        except OSError as exc:
            on_error(f"Cannot bind to port {_OAUTH_PORT}: {exc}")
            return

        try:
            opened = bool(webbrowser.open(auth_url))
        except Exception as exc:
            server.server_close()
            on_error(f"The browser cannot open: {type(exc).__name__}: {exc}")
            return
        if not opened:
            server.server_close()
            on_error("The browser cannot open the ChatGPT sign-in page.")
            return
        server.timeout = 300   # 5-minute timeout
        deadline = time.time() + 300
        while not done.is_set() and time.time() < deadline:
            server.handle_request()
        server.server_close()

        if not result:
            on_error("OAuth flow timed out — no callback received within 5 minutes")
            return

        kind, value = result[0]
        if kind == "error":
            on_error(value)
            return

        try:
            raw    = _exchange_code(value, verifier, _REDIRECT_URI)
            tokens = _tokens_from_raw(raw)
            save_tokens(tokens)
            on_success(tokens)
        except Exception as exc:
            on_error(f"Token exchange failed: {exc}")

    threading.Thread(target=_run, daemon=True, name="chatgpt-oauth-browser").start()


# ---------------------------------------------------------------------------
# Device-code flow (headless / no-browser)
# ---------------------------------------------------------------------------

def start_device_login(
    on_code:    Callable[[str, str], None],   # (verify_url, user_code)
    on_success: Callable[[dict], None],
    on_error:   Callable[[str], None],
) -> None:
    """
    Start the device-code OAuth flow in a background daemon thread.

    ``on_code(url, user_code)`` is called first so the caller can show the
    user where to go and what code to enter.  The thread then polls until
    the user authorises, then calls ``on_success`` or ``on_error``.
    """
    _POLLING_SAFETY_MS = 3000  # extra margin on top of the server-supplied interval

    def _run() -> None:
        """Drive the device-code OAuth flow (poll until authorised) on the background thread."""
        try:
            device = _post_json(
                f"{_ISSUER}/api/accounts/deviceauth/usercode",
                {"client_id": _CLIENT_ID},
            )
        except Exception as exc:
            on_error(f"Device auth initiation failed: {exc}")
            return

        on_code(f"{_ISSUER}/codex/device", device["user_code"])

        interval_ms = max(int(device.get("interval", 5)), 1) * 1000 + _POLLING_SAFETY_MS

        while True:
            time.sleep(interval_ms / 1000)
            try:
                data = _post_json(
                    f"{_ISSUER}/api/accounts/deviceauth/token",
                    {
                        "device_auth_id": device["device_auth_id"],
                        "user_code":      device["user_code"],
                    },
                )
                # Server responded 200 — exchange the returned auth code
                raw = _exchange_code(
                    data["authorization_code"],
                    data["code_verifier"],
                    f"{_ISSUER}/deviceauth/callback",
                )
                tokens = _tokens_from_raw(raw)
                save_tokens(tokens)
                on_success(tokens)
                return
            except OAuthTokenStorageError as exc:
                on_error(str(exc))
                return
            except Exception:
                # 403/404 == still pending; keep polling
                pass

    threading.Thread(target=_run, daemon=True, name="chatgpt-oauth-device").start()


# ---------------------------------------------------------------------------
# HTML templates for the redirect page
# ---------------------------------------------------------------------------

_HTML_SHELL = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  color-scheme: light;
  --wisp-blue: #2563eb;
  --wisp-blue-soft: #dbeafe;
  --wisp-ink: #0f172a;
  --wisp-muted: #475569;
  --wisp-page: #f8fbff;
  --wisp-white: #ffffff;
}}
* {{ box-sizing: border-box; }}
body {{
  min-height: 100vh;
  margin: 0;
  display: grid;
  place-items: center;
  background:
    radial-gradient(circle at 50% 0%, rgba(37, 99, 235, 0.10), transparent 34rem),
    var(--wisp-page);
  color: var(--wisp-ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
main {{
  width: min(92vw, 27rem);
  padding: 2.75rem 2rem;
  text-align: center;
}}
.icon {{
  width: 4.5rem;
  height: 4.5rem;
  object-fit: contain;
  margin-bottom: 1.5rem;
  filter: drop-shadow(0 1rem 1.5rem rgba(37, 99, 235, 0.18));
}}
.icon-fallback {{
  width: 4.5rem;
  height: 4.5rem;
  margin: 0 auto 1.5rem;
  display: grid;
  place-items: center;
  border-radius: 1.25rem;
  background: var(--wisp-blue);
  color: var(--wisp-white);
  font-size: 2rem;
  font-weight: 750;
  box-shadow: 0 1rem 1.5rem rgba(37, 99, 235, 0.18);
}}
h1 {{
  margin: 0;
  color: var(--wisp-ink);
  font-size: clamp(1.35rem, 5vw, 1.75rem);
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: 0;
}}
p {{
  margin: 0.75rem 0 0;
  color: var(--wisp-muted);
  font-size: 1rem;
  line-height: 1.55;
}}
.error {{
  display: inline-block;
  max-width: 100%;
  margin-top: 1rem;
  padding: 0.65rem 0.85rem;
  border: 1px solid #bfdbfe;
  border-radius: 0.5rem;
  background: var(--wisp-white);
  color: #1d4ed8;
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
  font-size: 0.9rem;
}}
</style>
</head>
<body>
<main>
{icon}
{content}
</main>
{script}
</body>
</html>"""


def _html_icon() -> str:
    icon_uri = _app_icon_data_uri()
    if icon_uri:
        return f'<img class="icon" src="{icon_uri}" alt="Wisp">'
    return '<div class="icon-fallback" aria-label="Wisp">W</div>'


def _html_success() -> str:
    """Return the branded OAuth success page."""
    return _HTML_SHELL.format(
        title="Wisp - Authorization Complete",
        icon=_html_icon(),
        content=(
            "<h1>Authorization completed successfully.</h1>\n"
            "<p>You can close this window and return to Wisp.</p>"
        ),
        script="<script>setTimeout(() => window.close(), 2000)</script>",
    )


def _html_error(error: str) -> str:
    """Return the branded OAuth error page."""
    return _HTML_SHELL.format(
        title="Wisp - Authorization Failed",
        icon=_html_icon(),
        content=(
            "<h1>Authorization failed.</h1>\n"
            "<p>Return to Wisp and try signing in again.</p>\n"
            f'<div class="error">{html.escape(error)}</div>'
        ),
        script="",
    )
