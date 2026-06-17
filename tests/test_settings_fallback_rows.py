"""Tests for test settings fallback rows."""

import unittest

from ui.settings_panel.helpers import parse_fallback_rows


class SettingsFallbackRowsTests(unittest.TestCase):
    """Test case for settings fallback rows tests behavior."""
    def test_parse_fallback_rows_accepts_lines_and_semicolons(self):
        """Verify parse fallback rows accepts lines and semicolons behavior."""
        self.assertEqual(
            parse_fallback_rows("chatgpt:gpt-5.5\nanthropic:claude-sonnet-4-5; openai:gpt-4o"),
            [
                ("chatgpt", "gpt-5.5"),
                ("anthropic", "claude-sonnet-4-5"),
                ("openai", "gpt-4o"),
            ],
        )


if __name__ == "__main__":
    unittest.main()

