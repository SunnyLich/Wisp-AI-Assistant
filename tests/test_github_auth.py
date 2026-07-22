"""Tests for test github auth."""

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config
from core.auth import github as github_auth


class GithubAuthTests(unittest.TestCase):
    def test_configured_client_id_prefers_user_override(self):
        with patch.object(config, "GITHUB_CLIENT_ID", "custom"), patch.object(
            config, "GITHUB_DEFAULT_CLIENT_ID", "default"
        ), patch.object(github_auth, "_BUNDLED_CLIENT_ID", "bundled"):
            self.assertEqual(github_auth.configured_client_id(), "custom")

    def test_configured_client_id_falls_back_to_bundled(self):
        with patch.object(config, "GITHUB_CLIENT_ID", ""), patch.object(
            config, "GITHUB_DEFAULT_CLIENT_ID", ""
        ), patch.object(github_auth, "_BUNDLED_CLIENT_ID", "bundled"):
            self.assertEqual(github_auth.configured_client_id(), "bundled")

    def test_post_form_returns_github_oauth_error_body(self):
        """Verify post form preserves GitHub OAuth error responses."""
        class FakeResponse:
            status_code = 400

            def json(self):
                return {
                    "error": "incorrect_client_credentials",
                    "error_description": "Bad credentials",
                }

            def raise_for_status(self):
                raise AssertionError("raise_for_status should not hide OAuth errors")

        posted = {}

        def fake_post(_url, data, headers, timeout):
            posted.update({"data": data, "headers": headers, "timeout": timeout})
            return FakeResponse()

        fake_requests = SimpleNamespace(post=fake_post)
        with patch.dict("sys.modules", {"requests": fake_requests}):
            data = github_auth._post_form(
                "https://github.com/login/device/code",
                {"client_id": "bad-client", "scope": ""},
            )

        self.assertEqual(data["error"], "incorrect_client_credentials")
        self.assertEqual(posted["data"], {"client_id": "bad-client"})

    def test_oauth_error_message_adds_client_id_hint(self):
        """Verify OAuth error messages include actionable client ID guidance."""
        message = github_auth._oauth_error_message({
            "error": "incorrect_client_credentials",
            "error_description": "Bad credentials",
        })

        self.assertIn("Bad credentials", message)
        self.assertIn("OAuth client ID", message)


def test_github_device_flow_pending_then_persists_validated_user(monkeypatch):
    """Device login polls pending state, validates the user, and saves the token."""
    responses = iter(
        [
            {
                "verification_uri": "https://github.com/login/device",
                "user_code": "ABCD-EFGH",
                "device_code": "device-secret",
                "expires_in": 900,
                "interval": 1,
            },
            {"error": "authorization_pending"},
            {"access_token": "github-access", "token_type": "bearer", "scope": "repo"},
        ]
    )
    codes = []
    successes = []
    errors = []
    saved = []
    finished = threading.Event()

    monkeypatch.setattr(github_auth, "configured_client_id", lambda: "client-id")
    monkeypatch.setattr(github_auth, "_post_form", lambda _url, _params: next(responses))
    monkeypatch.setattr(
        github_auth,
        "_get_json",
        lambda _url, token: {"login": "octo-user", "id": 42, "name": "Octo"}
        if token == "github-access"
        else {},
    )
    monkeypatch.setattr(github_auth, "save_tokens", lambda tokens: saved.append(dict(tokens)))
    monkeypatch.setattr(github_auth.time, "sleep", lambda _seconds: None)

    github_auth.start_device_login(
        lambda url, code: codes.append((url, code)),
        lambda tokens: (successes.append(tokens), finished.set()),
        lambda error: (errors.append(error), finished.set()),
    )
    assert finished.wait(5)

    assert errors == []
    assert codes == [("https://github.com/login/device", "ABCD-EFGH")]
    assert successes == saved
    assert saved[0]["access"] == "github-access"
    assert saved[0]["user"] == {"login": "octo-user", "id": 42, "name": "Octo"}


def test_github_device_flow_failure_matrix_is_in_band(monkeypatch):
    """Device OAuth reports expiry, provider/network faults, and rejected scopes."""
    monkeypatch.setattr(github_auth, "configured_client_id", lambda: "client-id")
    monkeypatch.setattr(github_auth.time, "sleep", lambda _seconds: None)

    cases = (
        (ConnectionError("provider service is unavailable"), "provider service is unavailable"),
        (OSError("network access is unavailable"), "network access is unavailable"),
        ({"error": "invalid_scope", "error_description": "requested scopes are rejected"}, "requested scopes are rejected"),
        (
            {
                "verification_uri": "https://github.com/login/device",
                "user_code": "EXPIRED",
                "device_code": "expired-device",
                "expires_in": 0,
                "interval": 1,
            },
            "expired",
        ),
    )
    for response, expected in cases:
        finished = threading.Event()
        errors = []

        def post_form(_url, _params, response=response):
            if isinstance(response, BaseException):
                raise response
            return response

        monkeypatch.setattr(github_auth, "_post_form", post_form)
        github_auth.start_device_login(
            lambda _url, _code: None,
            lambda _tokens, finished=finished: finished.set(),
            lambda error, errors=errors, finished=finished: (
                errors.append(error),
                finished.set(),
            ),
        )
        assert finished.wait(5)
        assert expected in errors[-1].lower()


def test_github_device_flow_cancellation_and_manual_browser_fallback(monkeypatch):
    """The copyable device code survives browser failure, while cancellation stops polling."""
    device = {
        "verification_uri": "https://github.com/login/device",
        "user_code": "COPY-ME",
        "device_code": "device-secret",
        "expires_in": 900,
        "interval": 1,
    }
    codes = []
    cancelled = {"value": False}
    polled = []
    stopped = threading.Event()
    monkeypatch.setattr(github_auth, "configured_client_id", lambda: "client-id")

    def post_form(url, _params):
        if url == github_auth._DEVICE_CODE_URL:
            return device
        polled.append(url)
        return {"access_token": "must-not-be-stored"}

    def on_code(url, code):
        codes.append((url, code))
        cancelled["value"] = True

    def is_cancelled():
        if cancelled["value"]:
            stopped.set()
        return cancelled["value"]

    monkeypatch.setattr(github_auth, "_post_form", post_form)
    monkeypatch.setattr(github_auth.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        github_auth,
        "save_tokens",
        lambda _tokens: pytest.fail("cancelled device flow stored a token"),
    )

    github_auth.start_device_login(
        on_code,
        lambda _tokens: pytest.fail("cancelled device flow completed"),
        lambda _error: pytest.fail("cancellation should stop silently"),
        is_cancelled=is_cancelled,
    )
    assert stopped.wait(5)
    assert codes == [("https://github.com/login/device", "COPY-ME")]
    assert polled == []


def test_github_token_storage_fails_closed_when_keyring_is_unavailable(tmp_path, monkeypatch):
    """GitHub OAuth tokens are never written to a plaintext fallback file."""
    token_file = tmp_path / "private" / "github.json"
    tokens = {"access": "github-contract-token", "user": {"login": "octo-user"}, "saved_at": 1}
    monkeypatch.setattr(github_auth, "_TOKEN_FILE", token_file)
    monkeypatch.setattr(github_auth, "_keyring_get", lambda: None)
    monkeypatch.setattr(github_auth, "_keyring_set", lambda _value: False)
    monkeypatch.setattr(github_auth, "_keyring_delete", lambda: None)

    with pytest.raises(github_auth.OAuthTokenStorageError, match="not stored"):
        github_auth.save_tokens(tokens)
    assert not token_file.exists()


def test_github_legacy_plaintext_tokens_migrate_and_are_removed(tmp_path, monkeypatch):
    token_file = tmp_path / "private" / "github.json"
    token_file.parent.mkdir(parents=True)
    tokens = {"access": "github-contract-token", "user": {"login": "octo-user"}, "saved_at": 1}
    token_file.write_text(json.dumps(tokens), encoding="utf-8")
    stored: list[str] = []
    monkeypatch.setattr(github_auth, "_TOKEN_FILE", token_file)
    monkeypatch.setattr(github_auth, "_keyring_get", lambda: stored[-1] if stored else None)
    monkeypatch.setattr(github_auth, "_keyring_set", lambda value: stored.append(value) or True)

    assert github_auth.get_tokens() == tokens
    assert github_auth.get_user_login() == "octo-user"
    assert not token_file.exists()


if __name__ == "__main__":
    unittest.main()
