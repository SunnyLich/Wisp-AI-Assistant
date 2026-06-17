"""Tests for test screenshot capability."""

import unittest

from core.llm_clients.client import (
    screenshot_capability_warnings,
    subscription_auth_warnings,
    tool_capability_warnings,
)


def warn(modes, **cfg):
    """Verify warn behavior."""
    base = dict(llm_provider="", llm_model="", vision_provider="", vision_model="")
    base.update(cfg)
    return screenshot_capability_warnings(modes, **base)


class ScreenshotCapabilityWarnings(unittest.TestCase):
    """Test case for screenshot capability warnings behavior."""
    def test_all_off_is_silent(self):
        """Verify all off is silent behavior."""
        self.assertEqual(warn(["off", "off"]), [])

    def test_auto_without_vision_model_warns(self):
        """Verify auto without vision model warns behavior."""
        w = warn(["auto"], llm_provider="anthropic", llm_model="claude-opus-4-5",
                 vision_provider="", vision_model="")
        self.assertEqual(len(w), 1)
        self.assertIn("Image model", w[0])

    def test_auto_with_vision_capable_model_is_silent(self):
        """Verify auto with vision capable model is silent behavior."""
        self.assertEqual(
            warn(["auto"], vision_provider="anthropic", vision_model="claude-opus-4-5"),
            [],
        )

    def test_auto_with_text_only_vision_model_warns(self):
        """Verify auto with text only vision model warns behavior."""
        w = warn(["auto"], vision_provider="groq", vision_model="llama-3.3-70b")
        self.assertEqual(len(w), 1)
        self.assertIn("may not read screenshots", w[0])

    def test_model_mode_anthropic_is_silent(self):
        """Verify model mode anthropic is silent behavior."""
        self.assertEqual(
            warn(["model"], llm_provider="anthropic", llm_model="claude-sonnet-4-5"),
            [],
        )

    def test_model_mode_copilot_warns_unsupported(self):
        """Verify model mode copilot warns unsupported behavior."""
        w = warn(["model"], llm_provider="copilot", llm_model="gpt-4o")
        self.assertEqual(len(w), 1)
        self.assertIn("do not work", w[0])

    def test_model_mode_openai_vision_capable_main_model_is_silent(self):
        """Verify model mode openai vision capable main model is silent behavior."""
        self.assertEqual(
            warn(["model"], llm_provider="openai", llm_model="gpt-4o"),
            [],
        )

    def test_model_mode_groq_text_only_warns(self):
        """Verify model mode groq text only warns behavior."""
        w = warn(["model"], llm_provider="groq", llm_model="llama-3.3-70b")
        self.assertEqual(len(w), 1)
        self.assertIn("may not read screenshots", w[0])

    def test_model_mode_groq_with_same_provider_vision_model_is_silent(self):
        """Verify model mode groq with same provider vision model is silent behavior."""
        self.assertEqual(
            warn(["model"], llm_provider="groq", llm_model="llama-3.3-70b",
                 vision_provider="groq", vision_model="llama-3.2-90b-vision"),
            [],
        )

    def test_legacy_and_mixed_modes_dedupe(self):
        # Two callers both on auto with a bad vision model → still one warning.
        """Verify legacy and mixed modes dedupe behavior."""
        w = warn(["auto", "auto"], vision_provider="groq", vision_model="llama-3.3-70b")
        self.assertEqual(len(w), 1)


class ToolCapabilityWarnings(unittest.TestCase):
    """Test case for tool capability warnings behavior."""
    def test_tools_off_is_silent(self):
        """Verify tools off is silent behavior."""
        self.assertEqual(tool_capability_warnings(False, llm_provider="chatgpt"), [])

    def test_tools_on_chatgpt_warns(self):
        """Verify tools on chatgpt warns behavior."""
        w = tool_capability_warnings(True, llm_provider="chatgpt")
        self.assertEqual(len(w), 1)
        self.assertIn("ChatGPT", w[0])

    def test_tools_on_anthropic_is_silent(self):
        """Verify tools on anthropic is silent behavior."""
        self.assertEqual(tool_capability_warnings(True, llm_provider="anthropic"), [])

    def test_tools_on_groq_is_silent(self):
        """Verify tools on groq is silent behavior."""
        self.assertEqual(tool_capability_warnings(True, llm_provider="groq"), [])


class SubscriptionAuthWarnings(unittest.TestCase):
    """Test case for subscription auth warnings behavior."""
    def test_api_key_providers_are_silent(self):
        """Verify api key providers are silent behavior."""
        self.assertEqual(
            subscription_auth_warnings(llm_provider="anthropic", vision_provider="openai"),
            [],
        )

    def test_chatgpt_vision_warns(self):
        """Verify chatgpt vision warns behavior."""
        w = subscription_auth_warnings(vision_provider="chatgpt")
        self.assertEqual(len(w), 1)
        self.assertIn("Image model", w[0])

    def test_copilot_main_warns(self):
        """Verify copilot main warns behavior."""
        w = subscription_auth_warnings(llm_provider="copilot")
        self.assertEqual(len(w), 1)
        self.assertIn("Chat model", w[0])

    def test_both_roles_warn_separately(self):
        """Verify both roles warn separately behavior."""
        w = subscription_auth_warnings(llm_provider="chatgpt", vision_provider="copilot")
        self.assertEqual(len(w), 2)


if __name__ == "__main__":
    unittest.main()
