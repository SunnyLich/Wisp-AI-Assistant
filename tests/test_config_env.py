import os
import unittest
from unittest.mock import patch

import config


class ConfigEnvTests(unittest.TestCase):
    def test_reload_parses_icon_size_and_bool_aliases(self):
        previous = {
            "ICON_SIZE": config.ICON_SIZE,
            "DARK_MODE": config.DARK_MODE,
            "ICON_AUTO_HIDE": config.ICON_AUTO_HIDE,
            "SNIP_CONTEXT_DOCUMENTS": config.SNIP_CONTEXT_DOCUMENTS,
        }
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "ICON_SIZE": "96",
                    "DARK_MODE": "true",
                    "ICON_AUTO_HIDE": "yes",
                    "SNIP_CONTEXT_DOCUMENTS": "off",
                },
                clear=False,
            ):
                config.reload()

            self.assertEqual(config.ICON_SIZE, 96)
            self.assertTrue(config.DARK_MODE)
            self.assertTrue(config.ICON_AUTO_HIDE)
            self.assertFalse(config.SNIP_CONTEXT_DOCUMENTS)
        finally:
            for name, value in previous.items():
                setattr(config, name, value)

    def test_legacy_doll_keys_still_honored(self):
        """Old DOLL_* env keys remain valid via back-compat fallback."""
        previous = {
            "ICON_SIZE": config.ICON_SIZE,
            "ICON_AUTO_HIDE": config.ICON_AUTO_HIDE,
        }
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "DOLL_SIZE": "72",
                    "DOLL_AUTO_HIDE": "false",
                },
                clear=False,
            ):
                # Ensure the new keys are absent so the fallback path is exercised.
                os.environ.pop("ICON_SIZE", None)
                os.environ.pop("ICON_AUTO_HIDE", None)
                config.reload()

            self.assertEqual(config.ICON_SIZE, 72)
            self.assertFalse(config.ICON_AUTO_HIDE)
        finally:
            for name, value in previous.items():
                setattr(config, name, value)

    def test_new_icon_keys_win_over_legacy(self):
        """When both new and legacy keys are set, the new ICON_* key takes precedence."""
        previous = {"ICON_AUTO_HIDE": config.ICON_AUTO_HIDE}
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "ICON_AUTO_HIDE": "false",
                    "DOLL_AUTO_HIDE": "true",
                },
                clear=False,
            ):
                config.reload()

            self.assertFalse(config.ICON_AUTO_HIDE)
        finally:
            for name, value in previous.items():
                setattr(config, name, value)


if __name__ == "__main__":
    unittest.main()
