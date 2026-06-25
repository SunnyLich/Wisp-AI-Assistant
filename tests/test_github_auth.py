"""Tests for test github auth."""

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


if __name__ == "__main__":
    unittest.main()
