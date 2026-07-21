"""Tests for ChatGPT OAuth callback and credential contracts."""

import json
import logging
import os
import socket
import threading
import urllib.request
import uuid
import webbrowser
from urllib.parse import parse_qs, urlparse

import pytest

from core.auth import chatgpt as chatgpt_auth


def test_oauth_success_page_uses_wisp_copy(monkeypatch):
    """Verify the browser callback success page uses the branded Wisp message."""
    monkeypatch.setattr(chatgpt_auth, "_app_icon_data_uri", lambda: "data:image/x-icon;base64,icon")

    html = chatgpt_auth._html_success()

    assert "Wisp - Authorization Complete" in html
    assert 'alt="Wisp"' in html
    assert "Authorization completed successfully." in html
    assert "You can close this window and return to Wisp." in html
    assert "Authorization Successful" not in html
    assert "return to the app" not in html


def test_oauth_error_page_escapes_error_message(monkeypatch):
    """Verify OAuth errors cannot inject markup into the callback page."""
    monkeypatch.setattr(chatgpt_auth, "_app_icon_data_uri", lambda: "")

    html = chatgpt_auth._html_error('<script>alert("x")</script>')

    assert "Authorization failed." in html
    assert "Return to Wisp and try signing in again." in html
    assert "&lt;script&gt;" in html
    assert '<script>alert("x")</script>' not in html


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_browser_oauth_reports_when_system_browser_cannot_open(monkeypatch):
    """A false/failed browser launch ends the flow instead of waiting five minutes."""
    errors: list[str] = []
    finished = threading.Event()

    class FakeServer:
        def __init__(self, *_args, **_kwargs):
            self.closed = False

        def server_close(self):
            self.closed = True

    monkeypatch.setattr(chatgpt_auth, "HTTPServer", FakeServer)
    monkeypatch.setattr(webbrowser, "open", lambda _url: False)
    chatgpt_auth.start_browser_login(
        lambda _tokens: pytest.fail("browser failure must not authenticate"),
        lambda message: (errors.append(message), finished.set()),
    )
    assert finished.wait(5)
    assert errors == ["The browser cannot open the ChatGPT sign-in page."]


def test_browser_oauth_rejects_mismatched_state_without_leaking_code(monkeypatch, caplog):
    """The real localhost callback rejects CSRF state before token exchange."""
    port = _free_local_port()
    opened = []
    browser_ready = threading.Event()
    errors = []
    finished = threading.Event()

    monkeypatch.setattr(chatgpt_auth, "_OAUTH_PORT", port)
    monkeypatch.setattr(chatgpt_auth, "_REDIRECT_URI", f"http://localhost:{port}/auth/callback")
    monkeypatch.setattr(chatgpt_auth, "_generate_state", lambda: "expected-state")
    monkeypatch.setattr(chatgpt_auth, "_generate_code_verifier", lambda: "contract-verifier")
    monkeypatch.setattr(
        chatgpt_auth,
        "_exchange_code",
        lambda *_args: pytest.fail("state mismatch must not exchange the authorization code"),
    )

    def open_browser(url):
        opened.append(url)
        browser_ready.set()
        return True

    monkeypatch.setattr(webbrowser, "open", open_browser)
    with caplog.at_level(logging.INFO, logger="wisp.chatgpt_auth"):
        chatgpt_auth.start_browser_login(
            lambda _tokens: pytest.fail("state mismatch must not authenticate"),
            lambda message: (errors.append(message), finished.set()),
        )
        assert browser_ready.wait(5)
        secret_code = "authorization-code-must-not-be-logged"
        with urllib.request.urlopen(
            f"http://localhost:{port}/auth/callback?code={secret_code}&state=wrong-state",
            timeout=5,
        ) as response:
            body = response.read().decode("utf-8")
        assert finished.wait(5)

    assert "OAuth state mismatch" in body
    assert errors and "state did not match" in errors[0]
    assert secret_code not in caplog.text
    query = parse_qs(urlparse(opened[0]).query)
    assert query["state"] == ["expected-state"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [chatgpt_auth._generate_code_challenge("contract-verifier")]


def test_browser_oauth_valid_callback_uses_pkce_and_persists_tokens(monkeypatch):
    """A valid localhost callback exchanges with its verifier and saves tokens."""
    port = _free_local_port()
    opened = []
    browser_ready = threading.Event()
    finished = threading.Event()
    exchanges = []
    saved = []
    successes = []

    monkeypatch.setattr(chatgpt_auth, "_OAUTH_PORT", port)
    monkeypatch.setattr(chatgpt_auth, "_REDIRECT_URI", f"http://localhost:{port}/auth/callback")
    monkeypatch.setattr(chatgpt_auth, "_generate_state", lambda: "valid-state")
    monkeypatch.setattr(chatgpt_auth, "_generate_code_verifier", lambda: "valid-verifier")
    monkeypatch.setattr(
        webbrowser,
        "open",
        lambda url: (opened.append(url), browser_ready.set(), True)[-1],
    )

    def exchange(code, verifier, redirect_uri):
        exchanges.append((code, verifier, redirect_uri))
        return {"access_token": "access-value", "refresh_token": "refresh-value", "expires_in": 3600}

    monkeypatch.setattr(chatgpt_auth, "_exchange_code", exchange)
    monkeypatch.setattr(chatgpt_auth, "save_tokens", lambda tokens: saved.append(dict(tokens)))
    chatgpt_auth.start_browser_login(
        lambda tokens: (successes.append(tokens), finished.set()),
        lambda error: pytest.fail(f"unexpected OAuth error: {error}"),
    )
    assert browser_ready.wait(5)
    with urllib.request.urlopen(
        f"http://localhost:{port}/auth/callback?code=valid-code&state=valid-state",
        timeout=5,
    ) as response:
        assert response.status == 200
    assert finished.wait(5)

    assert exchanges == [("valid-code", "valid-verifier", f"http://localhost:{port}/auth/callback")]
    assert successes == saved
    assert saved[0]["access"] == "access-value"
    assert saved[0]["refresh"] == "refresh-value"
    query = parse_qs(urlparse(opened[0]).query)
    assert query["code_challenge"] == [chatgpt_auth._generate_code_challenge("valid-verifier")]


def test_token_storage_fails_closed_when_keyring_is_unavailable(tmp_path, monkeypatch):
    """Reusable OAuth tokens are never written to a plaintext fallback file."""
    token_file = tmp_path / "private" / "tokens.json"
    monkeypatch.setattr(chatgpt_auth, "_TOKEN_FILE", token_file)
    monkeypatch.setattr(chatgpt_auth, "_USE_CHUNKED_KEYRING", True)
    monkeypatch.setattr(chatgpt_auth, "_keyring_get", lambda _account=chatgpt_auth._KEYRING_ACCOUNT: None)
    monkeypatch.setattr(chatgpt_auth, "_keyring_set", lambda _value, _account=chatgpt_auth._KEYRING_ACCOUNT: False)
    monkeypatch.setattr(chatgpt_auth, "_keyring_delete", lambda _account=chatgpt_auth._KEYRING_ACCOUNT: None)

    with pytest.raises(chatgpt_auth.OAuthTokenStorageError, match="not stored"):
        chatgpt_auth.save_tokens({"access": "old", "refresh": "refresh-old", "expires": 1})
    assert not token_file.exists()


def test_legacy_plaintext_tokens_migrate_to_keyring_and_are_removed(tmp_path, monkeypatch):
    token_file = tmp_path / "private" / "tokens.json"
    token_file.parent.mkdir(parents=True)
    tokens = {"access": "old", "refresh": "refresh-old", "expires": 1}
    token_file.write_text(json.dumps(tokens), encoding="utf-8")
    stored: dict[str, str] = {}
    monkeypatch.setattr(chatgpt_auth, "_TOKEN_FILE", token_file)
    monkeypatch.setattr(chatgpt_auth, "_USE_CHUNKED_KEYRING", True)
    monkeypatch.setattr(
        chatgpt_auth,
        "_keyring_get",
        lambda account=chatgpt_auth._KEYRING_ACCOUNT: stored.get(account),
    )
    monkeypatch.setattr(
        chatgpt_auth,
        "_keyring_set",
        lambda value, account=chatgpt_auth._KEYRING_ACCOUNT: stored.__setitem__(account, value) or True,
    )

    assert chatgpt_auth.get_tokens() == tokens
    assert not token_file.exists()


def test_chunked_token_roundtrip_rewrite_and_clear(tmp_path, monkeypatch):
    """Large OAuth payloads are chunked, rewritten, reassembled, and fully removed."""
    stored: dict[str, str] = {}
    deleted: list[str] = []
    monkeypatch.setattr(chatgpt_auth, "_TOKEN_FILE", tmp_path / "must-not-exist.json")
    monkeypatch.setattr(chatgpt_auth, "_USE_CHUNKED_KEYRING", True)
    monkeypatch.setattr(
        chatgpt_auth,
        "_keyring_get",
        lambda account=chatgpt_auth._KEYRING_ACCOUNT: stored.get(account),
    )
    monkeypatch.setattr(
        chatgpt_auth,
        "_keyring_set",
        lambda value, account=chatgpt_auth._KEYRING_ACCOUNT: stored.__setitem__(account, value) or True,
    )

    def delete(account=chatgpt_auth._KEYRING_ACCOUNT):
        deleted.append(account)
        stored.pop(account, None)

    monkeypatch.setattr(chatgpt_auth, "_keyring_delete", delete)

    large = {
        "access": "access-" + "a" * 2400,
        "refresh": "refresh-" + "r" * 1200,
        "expires": 123456789,
        "account_id": "account-1",
    }
    chatgpt_auth.save_tokens(large)
    first_manifest = json.loads(stored[chatgpt_auth._KEYRING_ACCOUNT])
    assert first_manifest[chatgpt_auth._CHUNK_MANIFEST_KEY] == 1
    assert first_manifest["count"] >= 4
    assert chatgpt_auth.get_tokens() == large

    smaller = {
        "access": "new-access-" + "b" * 1000,
        "refresh": "new-refresh",
        "expires": 987654321,
        "account_id": "account-1",
    }
    chatgpt_auth.save_tokens(smaller)
    second_manifest = json.loads(stored[chatgpt_auth._KEYRING_ACCOUNT])
    assert second_manifest["count"] < first_manifest["count"]
    assert chatgpt_auth.get_tokens() == smaller
    for index in range(second_manifest["count"], first_manifest["count"]):
        assert chatgpt_auth._chunk_account(index) not in stored

    chatgpt_auth.clear_tokens()
    assert chatgpt_auth.get_tokens() is None
    assert chatgpt_auth._KEYRING_ACCOUNT not in stored
    assert all(not account.startswith(f"{chatgpt_auth._KEYRING_ACCOUNT}-chunk-") for account in stored)
    assert chatgpt_auth._chunk_account(0) in deleted


def test_chunked_refresh_rewrites_rotating_credentials(tmp_path, monkeypatch):
    """Automatic refresh replaces a large expired chunk set with the rotated tokens."""
    stored: dict[str, str] = {}
    monkeypatch.setattr(chatgpt_auth, "_TOKEN_FILE", tmp_path / "must-not-exist.json")
    monkeypatch.setattr(chatgpt_auth, "_USE_CHUNKED_KEYRING", True)
    monkeypatch.setattr(
        chatgpt_auth,
        "_keyring_get",
        lambda account=chatgpt_auth._KEYRING_ACCOUNT: stored.get(account),
    )
    monkeypatch.setattr(
        chatgpt_auth,
        "_keyring_set",
        lambda value, account=chatgpt_auth._KEYRING_ACCOUNT: stored.__setitem__(account, value) or True,
    )
    monkeypatch.setattr(
        chatgpt_auth,
        "_keyring_delete",
        lambda account=chatgpt_auth._KEYRING_ACCOUNT: stored.pop(account, None),
    )

    expired = {
        "access": "expired-access-" + "a" * 2400,
        "refresh": "rotating-refresh-" + "r" * 1200,
        "expires": 1,
        "account_id": "account-1",
    }
    chatgpt_auth.save_tokens(expired)
    old_count = json.loads(stored[chatgpt_auth._KEYRING_ACCOUNT])["count"]

    def refresh(refresh_token):
        assert refresh_token == expired["refresh"]
        return {
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "expires_in": 3600,
        }

    monkeypatch.setattr(chatgpt_auth, "_do_refresh", refresh)

    assert chatgpt_auth.get_valid_access_token() == "fresh-access"
    refreshed = chatgpt_auth.get_tokens()
    assert refreshed["refresh"] == "fresh-refresh"
    assert refreshed["account_id"] == "account-1"
    new_count = json.loads(stored[chatgpt_auth._KEYRING_ACCOUNT])["count"]
    assert new_count < old_count
    for index in range(new_count, old_count):
        assert chatgpt_auth._chunk_account(index) not in stored


def test_device_login_reports_secure_storage_failure(monkeypatch):
    """A completed device login must not poll forever when the keychain rejects tokens."""
    responses = iter(
        [
            {"device_auth_id": "device", "user_code": "CODE", "interval": 1},
            {"authorization_code": "auth-code", "code_verifier": "verifier"},
        ]
    )
    errors: list[str] = []

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(chatgpt_auth.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(chatgpt_auth.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(chatgpt_auth, "_post_json", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(
        chatgpt_auth,
        "_exchange_code",
        lambda *_args: {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
    )
    monkeypatch.setattr(
        chatgpt_auth,
        "save_tokens",
        lambda _tokens: (_ for _ in ()).throw(chatgpt_auth.OAuthTokenStorageError("keychain unavailable")),
    )

    chatgpt_auth.start_device_login(
        lambda _url, _code: None,
        lambda _tokens: pytest.fail("storage failure must not authenticate"),
        errors.append,
    )

    assert errors == ["keychain unavailable"]


@pytest.mark.real_host
@pytest.mark.skipif(
    os.environ.get("WISP_RUN_REAL_KEYRING_TESTS") != "1",
    reason="set WISP_RUN_REAL_KEYRING_TESTS=1 to use the real OS credential store",
)
def test_real_os_keyring_roundtrip_uses_disposable_account(tmp_path, monkeypatch):
    """The active OS keyring can store, retrieve, and clear a disposable token."""
    account = f"chatgpt-oauth-contract-{uuid.uuid4()}"
    monkeypatch.setattr(chatgpt_auth, "_KEYRING_ACCOUNT", account)
    monkeypatch.setattr(chatgpt_auth, "_TOKEN_FILE", tmp_path / "must-not-exist.json")
    tokens = {
        "access": "disposable-contract-token-" + "a" * 2400,
        "refresh": "disposable-refresh-token-" + "r" * 1200,
        "expires": 123456789,
        "account_id": "disposable-account",
    }
    try:
        chatgpt_auth.save_tokens(tokens)
        assert chatgpt_auth.get_tokens() == tokens
        assert not chatgpt_auth._TOKEN_FILE.exists()
    finally:
        chatgpt_auth.clear_tokens()
    assert chatgpt_auth.get_tokens() is None
