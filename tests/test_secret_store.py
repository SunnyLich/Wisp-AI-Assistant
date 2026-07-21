"""Tests for test secret store."""

import json
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core import secret_store


class FakeKeyring(types.SimpleNamespace):
    def __init__(self):
        super().__init__()
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        # Isolate the on-disk meta file (markers + migration flag) and reset the
        # process-wide blob cache so each case reads its mocked keychain fresh.
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        meta = patch.object(secret_store, "_META_FILE", Path(self._tmp.name) / ".secret_status.json")
        meta.start()
        self.addCleanup(meta.stop)
        secret_store._invalidate_cache()
        self.addCleanup(secret_store._invalidate_cache)

    def test_get_secret_prefers_keychain_over_env(self):
        fake = FakeKeyring()
        # Stored as the consolidated blob.
        fake.set_password("python-ai-overlay", "__wisp_secrets__",
                          json.dumps({"OPENAI_API_KEY": "keychain-value"}))
        with patch.dict(sys.modules, {"keyring": fake}), patch.dict(
            "os.environ", {"OPENAI_API_KEY": "env-value"}
        ):
            self.assertEqual(secret_store.get_secret("OPENAI_API_KEY"), "keychain-value")

    def test_get_secret_falls_back_to_env_for_migration(self):
        fake = FakeKeyring()
        with patch.dict(sys.modules, {"keyring": fake}), patch.dict(
            "os.environ", {"OPENAI_API_KEY": "env-value"}
        ):
            self.assertEqual(secret_store.get_secret("OPENAI_API_KEY"), "env-value")

    def test_legacy_per_key_items_are_migrated_into_the_blob(self):
        # An older install stored one item per key — those must still be read,
        # then folded into the consolidated blob.
        fake = FakeKeyring()
        fake.set_password("python-ai-overlay", "openai_api_key", "legacy-openai")
        fake.set_password("python-ai-overlay", "cartesia_api_key", "legacy-cartesia")
        with patch.dict(sys.modules, {"keyring": fake}):
            self.assertEqual(secret_store.get_secret("OPENAI_API_KEY"), "legacy-openai")
            self.assertEqual(secret_store.get_secret("CARTESIA_API_KEY"), "legacy-cartesia")
            # Migrated into the single consolidated item.
            blob = json.loads(fake.get_password("python-ai-overlay", "__wisp_secrets__"))
            self.assertEqual(blob["OPENAI_API_KEY"], "legacy-openai")
            self.assertEqual(blob["CARTESIA_API_KEY"], "legacy-cartesia")

    def test_legacy_key_is_recovered_when_consolidated_blob_already_exists(self):
        fake = FakeKeyring()
        fake.set_password(
            "python-ai-overlay",
            "__wisp_secrets__",
            json.dumps({"OPENAI_API_KEY": "stored-openai"}),
        )
        fake.set_password("python-ai-overlay", "google_api_key", "legacy-google")

        with patch.dict(sys.modules, {"keyring": fake}):
            secret_store.set_configured_marker(secret_store._MIGRATED_FLAG, True)
            self.assertEqual(secret_store.get_secret("GOOGLE_API_KEY"), "legacy-google")
            self.assertEqual(secret_store.secret_source("GOOGLE_API_KEY"), "keychain")

        blob = json.loads(fake.get_password("python-ai-overlay", "__wisp_secrets__"))
        self.assertEqual(blob["OPENAI_API_KEY"], "stored-openai")
        self.assertEqual(blob["GOOGLE_API_KEY"], "legacy-google")

    def test_set_secret_writes_single_consolidated_item(self):
        fake = FakeKeyring()
        with patch.dict(sys.modules, {"keyring": fake}):
            secret_store.set_secret("OPENAI_API_KEY", "sk-a")
            secret_store.set_secret("GOOGLE_API_KEY", "g-b")
        # Both keys live in one item; no per-key items were created.
        self.assertIsNone(fake.get_password("python-ai-overlay", "openai_api_key"))
        blob = json.loads(fake.get_password("python-ai-overlay", "__wisp_secrets__"))
        self.assertEqual(blob, {"OPENAI_API_KEY": "sk-a", "GOOGLE_API_KEY": "g-b"})
        with patch.dict(sys.modules, {"keyring": fake}):
            self.assertEqual(secret_store.get_secret("OPENAI_API_KEY"), "sk-a")
            self.assertEqual(secret_store.get_secret("GOOGLE_API_KEY"), "g-b")

    def test_delete_secret_removes_only_that_key(self):
        fake = FakeKeyring()
        with patch.dict(sys.modules, {"keyring": fake}):
            secret_store.set_secret("OPENAI_API_KEY", "sk-a")
            secret_store.set_secret("GOOGLE_API_KEY", "g-b")
            secret_store.delete_secret("OPENAI_API_KEY")
            self.assertEqual(secret_store.get_secret("OPENAI_API_KEY"), "")
            self.assertEqual(secret_store.get_secret("GOOGLE_API_KEY"), "g-b")

    def test_migrate_env_secrets_writes_missing_keys(self):
        fake = FakeKeyring()
        env = {"OPENAI_API_KEY": "sk-test", "GOOGLE_API_KEY": "google-test", "GROQ_API_KEY": ""}
        with patch.dict(sys.modules, {"keyring": fake}):
            migrated = secret_store.migrate_env_secrets(env)

        self.assertEqual(migrated, ["OPENAI_API_KEY", "GOOGLE_API_KEY"])
        blob = json.loads(fake.get_password("python-ai-overlay", "__wisp_secrets__"))
        self.assertEqual(blob["OPENAI_API_KEY"], "sk-test")
        self.assertEqual(blob["GOOGLE_API_KEY"], "google-test")

    def test_secret_source_reports_keychain_env_or_none(self):
        fake = FakeKeyring()
        fake.set_password("python-ai-overlay", "__wisp_secrets__",
                          json.dumps({"OPENAI_API_KEY": "keychain-value"}))
        with patch.dict(sys.modules, {"keyring": fake}), patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "env-value", "GOOGLE_API_KEY": "google-env"}, clear=False
        ):
            self.assertEqual(secret_store.secret_source("OPENAI_API_KEY"), "keychain")
            self.assertEqual(secret_store.secret_source("ANTHROPIC_API_KEY"), "env")
            self.assertEqual(secret_store.secret_source("GOOGLE_API_KEY"), "env")
            self.assertEqual(secret_store.secret_source("GROQ_API_KEY"), "none")

    def test_stale_configured_marker_does_not_count_as_secret(self):
        """Verify stale configured markers do not masquerade as stored keys."""
        fake = FakeKeyring()
        with patch.dict(sys.modules, {"keyring": fake}):
            self.assertFalse(secret_store.has_secret("OPENAI_API_KEY"))
            secret_store.set_configured_marker("OPENAI_API_KEY", True)
            self.assertFalse(secret_store.has_secret("OPENAI_API_KEY"))
            self.assertFalse(secret_store.configured_marker("OPENAI_API_KEY"))


if __name__ == "__main__":
    unittest.main()
