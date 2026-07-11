"""Tests for the first-run profile setup choices."""
from __future__ import annotations

import os

from ui.onboarding import clean_profile_name, profile_values, should_show_onboarding


def test_onboarding_only_autostarts_for_a_fresh_installation():
    assert should_show_onboarding({}, env_file_exists=False) is True
    assert should_show_onboarding({}, env_file_exists=True) is False
    assert should_show_onboarding({"WISP_ONBOARDING_COMPLETE": "false"}, env_file_exists=True) is True
    assert should_show_onboarding({"WISP_ONBOARDING_COMPLETE": "true"}, env_file_exists=False) is False


def test_simple_profile_uses_oauth_and_local_speech_defaults():
    values = profile_values(
        name="  Ada   Lovelace ",
        setup_mode="simple",
        oauth_connected=True,
        tts_preference="local",
        stt_preference="local",
    )

    assert values["WISP_PROFILE_NAME"] == "Ada Lovelace"
    assert values["LLM_PROVIDER"] == "chatgpt"
    assert values["TTS_PROVIDER"] == "kokoro"
    assert values["STT_MODEL"] == "base"


def test_advanced_profile_keeps_cloud_speech_as_a_preference():
    values = profile_values(
        name="Pat",
        setup_mode="advanced",
        provider="anthropic",
        model="custom-claude",
        tts_preference="cloud",
        stt_preference="cloud",
    )

    assert values["LLM_PROVIDER"] == "anthropic"
    assert values["LLM_MODEL"] == "custom-claude"
    assert values["WISP_TTS_PREFERENCE"] == "cloud"
    assert values["WISP_STT_PREFERENCE"] == "cloud"
    assert "TTS_PROVIDER" not in values


def test_advanced_custom_route_persists_entered_model_endpoint_and_language():
    values = profile_values(
        name="Pat",
        setup_mode="advanced",
        provider="custom",
        model="local-model",
        custom_base_url="http://localhost:1234/v1",
        app_language="fr",
        assistant_language="French",
        theme_mode="dark",
    )

    assert values["LLM_PROVIDER"] == "custom"
    assert values["LLM_MODEL"] == "local-model"
    assert values["CUSTOM_BASE_URL"] == "http://localhost:1234/v1"
    assert values["APP_LANGUAGE"] == "fr"
    assert values["ASSISTANT_LANGUAGE"] == "French"
    assert values["THEME_MODE"] == "dark"


def test_profile_name_is_compacted_and_limited():
    assert clean_profile_name("  A\n\tB  ") == "A B"
    assert len(clean_profile_name("x" * 100)) == 80


def test_wizard_continue_is_the_default_action():
    """Enter in the name field must not activate the Back button."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.onboarding import OnboardingWizard

    qapp = QApplication.instance() or QApplication([])
    wizard = OnboardingWizard()
    try:
        assert wizard._back.autoDefault() is False
        assert wizard._next.isDefault() is True
    finally:
        wizard.close()
        qapp.processEvents()


def test_wizard_uses_the_shared_custom_window_chrome():
    """The first-run wizard uses Wisp's themed title bar and controls."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.onboarding import OnboardingWizard

    qapp = QApplication.instance() or QApplication([])
    wizard = OnboardingWizard()
    try:
        assert getattr(wizard, "_wisp_window_chrome", None) is not None
    finally:
        wizard.close()
        qapp.processEvents()
