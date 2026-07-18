"""Tests for the first-run profile setup choices."""
from __future__ import annotations

import os

from ui.onboarding import (
    clean_profile_name,
    local_speech_install_request,
    persisted_profile_setup_values,
    personal_profile_values,
    profile_values,
    should_show_onboarding,
)


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


def test_local_speech_choices_build_one_combined_real_installer_request():
    from core import optional_deps

    request = local_speech_install_request(
        tts_preference="local",
        stt_preference="local",
        settings={"TTS_PROVIDER": "kokoro", "STT_MODEL": "base", "STT_DEVICE": "auto"},
    )

    assert request is not None
    assert request["display_name"] == "Local speech"
    assert optional_deps.KOKORO_PACKAGE in request["packages"]
    assert optional_deps.STT_PACKAGE in request["packages"]
    assert optional_deps.KOKORO_CUDA_TORCH_PACKAGE in request["pre_install_packages"]
    extra = request["external_plan_extra"]
    assert extra["post_install"] == "speech_prepare"
    assert extra["settings_updates"]["TTS_PROVIDER"] == "kokoro"
    assert extra["settings_updates"]["STT_MODEL"] == "base"


def test_no_local_speech_choice_skips_the_installer():
    assert local_speech_install_request(
        tts_preference="none",
        stt_preference="cloud",
        settings={},
    ) is None


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


def test_first_setup_creates_and_activates_a_real_named_profile():
    setup = profile_values(
        name="Ada Lovelace",
        setup_mode="advanced",
        provider="anthropic",
        model="claude-test",
    )

    values = personal_profile_values(
        name="Ada Lovelace",
        setup_values=setup,
        existing_env={},
    )

    assert values["PROFILE_COUNT"] == "1"
    assert values["PROFILE_1_ID"] == "ada-lovelace"
    assert values["PROFILE_1_LABEL"] == "Ada Lovelace"
    assert values["PROFILE_1_LLM_PROVIDER"] == "anthropic"
    assert values["PROFILE_1_LLM_MODEL"] == "claude-test"
    assert values["ACTIVE_PROFILE"] == "ada-lovelace"
    assert values["SETTINGS_PROFILE"] == "ada-lovelace"


def test_setup_appends_named_profile_without_replacing_existing_profiles():
    existing = {
        "PROFILE_COUNT": "1",
        "PROFILE_1_ID": "work",
        "PROFILE_1_LABEL": "Work",
        "PROFILE_1_LLM_PROVIDER": "openai",
        "WISP_PROFILE_NAME": "Old User",
    }

    values = personal_profile_values(
        name="Pat",
        setup_values=profile_values(name="Pat", setup_mode="simple"),
        existing_env=existing,
    )

    assert values["PROFILE_COUNT"] == "2"
    assert values["PROFILE_2_ID"] == "pat"
    assert values["PROFILE_2_LABEL"] == "Pat"
    assert "PROFILE_1_ID" not in values


def test_setup_with_an_existing_same_name_creates_a_unique_profile():
    existing = {
        "PROFILE_COUNT": "1",
        "PROFILE_1_ID": "pat",
        "PROFILE_1_LABEL": "Pat",
        "PROFILE_1_LLM_PROVIDER": "openai",
        "PROFILE_1_LLM_MODEL": "gpt-existing",
        "ACTIVE_PROFILE": "pat",
        "SETTINGS_PROFILE": "pat",
    }

    values = personal_profile_values(
        name="Pat",
        setup_values=profile_values(
            name="Pat",
            setup_mode="advanced",
            provider="anthropic",
            model="claude-new",
        ),
        existing_env=existing,
    )

    assert values["PROFILE_COUNT"] == "2"
    assert values["PROFILE_2_ID"] == "pat-2"
    assert values["PROFILE_2_LABEL"] == "Pat"
    assert values["PROFILE_2_LLM_PROVIDER"] == "anthropic"
    assert values["PROFILE_2_LLM_MODEL"] == "claude-new"
    assert not any(key.startswith("PROFILE_1_") for key in values)


def test_rerunning_setup_appends_without_replacing_the_active_personal_profile():
    existing = {
        "PROFILE_COUNT": "2",
        "PROFILE_1_ID": "work",
        "PROFILE_1_LABEL": "Work",
        "PROFILE_2_ID": "ada",
        "PROFILE_2_LABEL": "Ada",
        "PROFILE_2_LLM_PROVIDER": "openai",
        "PROFILE_2_LLM_MODEL": "gpt-existing",
        "PROFILE_2_CONTEXT_BROWSER_MODE": "model",
        "WISP_PROFILE_NAME": "Ada",
        "ACTIVE_PROFILE": "ada",
        "SETTINGS_PROFILE": "ada",
    }

    values = personal_profile_values(
        name="Ada Lovelace",
        setup_values=profile_values(name="Ada Lovelace", setup_mode="simple"),
        existing_env=existing,
    )

    assert values["PROFILE_COUNT"] == "3"
    assert values["PROFILE_3_ID"] == "ada-lovelace"
    assert values["PROFILE_3_LABEL"] == "Ada Lovelace"
    assert values["PROFILE_3_LLM_PROVIDER"] == "openai"
    assert values["PROFILE_3_LLM_MODEL"] == "gpt-existing"
    assert values["PROFILE_3_CONTEXT_BROWSER_MODE"] == "model"
    assert values["ACTIVE_PROFILE"] == "ada-lovelace"
    assert not any(key.startswith("PROFILE_2_") for key in values)


def test_setup_profile_keeps_existing_default_model_settings_untouched():
    existing = {
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "gpt-existing",
        "VISION_LLM_PROVIDER": "google",
        "VISION_LLM_MODEL": "gemini-existing",
        "ACTIVE_PROFILE": "default",
        "SETTINGS_PROFILE": "default",
    }
    setup = profile_values(
        name="Pat",
        setup_mode="advanced",
        provider="anthropic",
        model="claude-new",
        theme_mode="dark",
    )

    values = persisted_profile_setup_values(
        name="Pat",
        setup_values=setup,
        existing_env=existing,
    )

    assert "LLM_PROVIDER" not in values
    assert "LLM_MODEL" not in values
    assert "VISION_LLM_PROVIDER" not in values
    assert "VISION_LLM_MODEL" not in values
    assert values["PROFILE_1_LLM_PROVIDER"] == "anthropic"
    assert values["PROFILE_1_LLM_MODEL"] == "claude-new"
    assert values["PROFILE_1_VISION_LLM_PROVIDER"] == "google"
    assert values["PROFILE_1_VISION_LLM_MODEL"] == "gemini-existing"
    assert values["THEME_MODE"] == "dark"


def test_wizard_finish_persists_the_named_profile_contract(tmp_path, monkeypatch):
    """The real Finish action writes the custom profile, not only WISP_PROFILE_NAME."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.onboarding import OnboardingWizard
    from ui.settings_panel import env as settings_env

    monkeypatch.setattr(settings_env, "ENV_PATH", tmp_path / ".env")
    qapp = QApplication.instance() or QApplication([])
    wizard = OnboardingWizard()
    try:
        wizard._name.setText("Barry")
        wizard._finish()

        saved = settings_env.read_settings_env()
        assert saved["WISP_PROFILE_NAME"] == "Barry"
        assert saved["PROFILE_COUNT"] == "1"
        assert saved["PROFILE_1_ID"] == "barry"
        assert saved["PROFILE_1_LABEL"] == "Barry"
        assert saved["ACTIVE_PROFILE"] == "barry"
        assert saved["SETTINGS_PROFILE"] == "barry"
    finally:
        wizard.close()
        qapp.processEvents()


def test_wizard_finish_launches_selected_local_speech_installer(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui import onboarding
    from ui.settings_panel import env as settings_env

    monkeypatch.setattr(settings_env, "ENV_PATH", tmp_path / ".env")
    launched: list[dict[str, object]] = []
    monkeypatch.setattr(onboarding, "launch_local_speech_installer", launched.append)
    qapp = QApplication.instance() or QApplication([])
    wizard = onboarding.OnboardingWizard()
    try:
        wizard._name.setText("Voice User")
        wizard._tts.setCurrentIndex(wizard._tts.findData("local"))
        wizard._stt.setCurrentIndex(wizard._stt.findData("local"))
        wizard._finish()
        qapp.processEvents()

        assert len(launched) == 1
        assert launched[0]["display_name"] == "Local speech"
        assert launched[0]["external_plan_extra"]["post_install"] == "speech_prepare"
    finally:
        wizard.close()
        qapp.processEvents()


def test_wizard_finish_preserves_existing_setup_and_appends_profile(tmp_path, monkeypatch):
    """The real Finish action must not mutate the active/default setup in place."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.onboarding import OnboardingWizard
    from ui.settings_panel import env as settings_env

    monkeypatch.setattr(settings_env, "ENV_PATH", tmp_path / ".env")
    settings_env.write_settings_env(
        {
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": "gpt-default-existing",
            "PROFILE_COUNT": "1",
            "PROFILE_1_ID": "work",
            "PROFILE_1_LABEL": "Work",
            "PROFILE_1_LLM_PROVIDER": "google",
            "PROFILE_1_LLM_MODEL": "gemini-work-existing",
            "PROFILE_1_CONTEXT_BROWSER_MODE": "model",
            "ACTIVE_PROFILE": "work",
            "SETTINGS_PROFILE": "work",
        }
    )
    qapp = QApplication.instance() or QApplication([])
    wizard = OnboardingWizard()
    try:
        wizard._name.setText("Barry")
        wizard._advanced_mode.setChecked(True)
        wizard._provider.setCurrentIndex(wizard._provider.findData("anthropic"))
        wizard._provider_model.setCurrentText("claude-new")
        wizard._finish()

        saved = settings_env.read_settings_env()
        assert saved["LLM_PROVIDER"] == "openai"
        assert saved["LLM_MODEL"] == "gpt-default-existing"
        assert saved["PROFILE_COUNT"] == "2"
        assert saved["PROFILE_1_ID"] == "work"
        assert saved["PROFILE_1_LLM_PROVIDER"] == "google"
        assert saved["PROFILE_1_LLM_MODEL"] == "gemini-work-existing"
        assert saved["PROFILE_2_ID"] == "barry"
        assert saved["PROFILE_2_LABEL"] == "Barry"
        assert saved["PROFILE_2_LLM_PROVIDER"] == "anthropic"
        assert saved["PROFILE_2_LLM_MODEL"] == "claude-new"
        assert saved["PROFILE_2_CONTEXT_BROWSER_MODE"] == "model"
        assert saved["ACTIVE_PROFILE"] == "barry"
        assert saved["SETTINGS_PROFILE"] == "barry"
    finally:
        wizard.close()
        qapp.processEvents()


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
