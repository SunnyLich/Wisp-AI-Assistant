import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
