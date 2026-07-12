"""Tests for test github auth."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import config
from core.auth import github as github_auth


class GithubAuthTests(unittest.TestCase):
    """Test case for github auth tests behavior."""
    def test_configured_client_id_prefers_user_override(self):
        """Verify configured client id prefers user override behavior."""
        with patch.object(config, "GITHUB_CLIENT_ID", "custom"), patch.object(
            config, "GITHUB_DEFAULT_CLIENT_ID", "default"
        ), patch.object(github_auth, "_BUNDLED_CLIENT_ID", "bundled"):
            self.assertEqual(github_auth.configured_client_id(), "custom")

    def test_configured_client_id_falls_back_to_bundled(self):
        """Verify configured client id falls back to bundled behavior."""
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


def test_github_token_file_fallback_and_logout_clear(tmp_path, monkeypatch):
    """GitHub fallback persistence is readable and logout clears every copy."""
    token_file = tmp_path / "private" / "github.json"
    tokens = {"access": "github-contract-token", "user": {"login": "octo-user"}, "saved_at": 1}
    monkeypatch.setattr(github_auth, "_TOKEN_FILE", token_file)
    monkeypatch.setattr(github_auth, "_keyring_get", lambda: None)
    monkeypatch.setattr(github_auth, "_keyring_set", lambda _value: False)
    monkeypatch.setattr(github_auth, "_keyring_delete", lambda: None)

    github_auth.save_tokens(tokens)
    assert github_auth.get_tokens() == tokens
    assert github_auth.get_user_login() == "octo-user"
    github_auth.clear_tokens()
    assert github_auth.get_tokens() is None
    assert not token_file.exists()


if __name__ == "__main__":
    unittest.main()
