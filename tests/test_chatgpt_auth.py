"""Tests for ChatGPT OAuth callback and credential contracts."""

import json
import logging
import os
import socket
import threading
import time
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


def test_token_file_fallback_refresh_rotation_and_clear(tmp_path, monkeypatch):
    """Fallback storage preserves rotated refresh state and clears completely."""
    token_file = tmp_path / "private" / "tokens.json"
    monkeypatch.setattr(chatgpt_auth, "_TOKEN_FILE", token_file)
    monkeypatch.setattr(chatgpt_auth, "_keyring_get", lambda: None)
    monkeypatch.setattr(chatgpt_auth, "_keyring_set", lambda _value: False)
    monkeypatch.setattr(chatgpt_auth, "_keyring_delete", lambda: None)
    expired = {
        "access": "expired-access",
        "refresh": "rotating-refresh",
        "expires": int((time.time() - 60) * 1000),
        "account_id": "account-1",
    }
    chatgpt_auth.save_tokens(expired)
    assert json.loads(token_file.read_text(encoding="utf-8")) == expired

    monkeypatch.setattr(
        chatgpt_auth,
        "_do_refresh",
        lambda refresh: {
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh" if refresh == "rotating-refresh" else "",
            "expires_in": 3600,
        },
    )
    assert chatgpt_auth.get_valid_access_token() == "fresh-access"
    stored = json.loads(token_file.read_text(encoding="utf-8"))
    assert stored["refresh"] == "fresh-refresh"
    assert stored["account_id"] == "account-1"

    chatgpt_auth.clear_tokens()
    assert not token_file.exists()
    assert chatgpt_auth.get_tokens() is None


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
    payload = json.dumps({"access": "disposable-contract-token"})
    try:
        assert chatgpt_auth._keyring_set(payload) is True
        assert chatgpt_auth._keyring_get() == payload
        assert not chatgpt_auth._TOKEN_FILE.exists()
    finally:
        chatgpt_auth._keyring_delete()
    assert chatgpt_auth._keyring_get() is None
