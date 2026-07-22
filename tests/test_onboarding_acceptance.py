"""Real first-run and onboarding acceptance workflows."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts.runtime_test_harness import QtUserDriver

pytestmark = pytest.mark.workflow


@dataclass
class OnboardingHarness:
    app: Any
    driver: QtUserDriver
    env_path: Path
    onboarding: Any
    settings_env: Any
    config: Any
    secrets: list[tuple[str, str]]
    installers: list[dict[str, object]]
    language_previews: list[str]
    theme_previews: list[str]

    def reset_saved_state(self) -> None:
        self.env_path.unlink(missing_ok=True)

    def saved(self) -> dict[str, str]:
        return self.settings_env.read_settings_env()

    def new_wizard(self, on_complete=None):
        wizard = self.onboarding.OnboardingWizard(on_complete=on_complete)
        wizard.show()
        self.driver.pump()
        return wizard


@pytest.fixture
def onboarding_harness(tmp_path, monkeypatch):
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from core import secret_store
    from ui import i18n, onboarding
    from ui.settings_panel import env as settings_env
    from ui.shared import theme

    app = QApplication.instance() or QApplication(["wisp-onboarding-acceptance"])
    env_path = tmp_path / "first-run.env"
    secrets: list[tuple[str, str]] = []
    installers: list[dict[str, object]] = []
    language_previews: list[str] = []
    theme_previews: list[str] = []
    original = {
        "APP_LANGUAGE": getattr(config, "APP_LANGUAGE", ""),
        "THEME_MODE": getattr(config, "THEME_MODE", "system"),
        "DARK_MODE": getattr(config, "DARK_MODE", False),
    }
    original_env_file = config._ENV_FILE
    original_loaded_keys = set(config._LOADED_DOTENV_KEYS)
    original_process_env = dict(os.environ)

    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    config._ENV_FILE = env_path
    config._LOADED_DOTENV_KEYS = set(original_loaded_keys)
    monkeypatch.setattr(
        secret_store,
        "set_secret",
        lambda name, value: secrets.append((name, value)),
    )
    monkeypatch.setattr(onboarding, "launch_local_speech_installer", installers.append)
    monkeypatch.setattr(
        i18n,
        "set_language",
        lambda language=None, app=None: language_previews.append(
            str(language if language is not None else config.APP_LANGUAGE)
        ),
    )
    monkeypatch.setattr(
        theme,
        "apply_app_theme",
        lambda _app=None: theme_previews.append(str(config.THEME_MODE)),
    )

    harness = OnboardingHarness(
        app=app,
        driver=QtUserDriver(app, timeout=3.0),
        env_path=env_path,
        onboarding=onboarding,
        settings_env=settings_env,
        config=config,
        secrets=secrets,
        installers=installers,
        language_previews=language_previews,
        theme_previews=theme_previews,
    )
    try:
        yield harness
    finally:
        os.environ.clear()
        os.environ.update(original_process_env)
        config._ENV_FILE = original_env_file
        config._LOADED_DOTENV_KEYS = original_loaded_keys
        config._load_config()
        for key, value in original.items():
            setattr(config, key, value)


def _select(combo, value, driver: QtUserDriver) -> None:
    driver.select_combo_data(combo, value)


def _dispose_wizard(wizard, driver: QtUserDriver) -> None:
    """Close a live wizard; Finish may already have deleted its C++ object."""
    try:
        wizard.close()
        wizard.deleteLater()
    except RuntimeError:
        pass
    driver.pump()


def _open_profile_page(
    harness: OnboardingHarness,
    wizard,
    *,
    advanced: bool,
    name: str,
) -> None:
    """Use Continue controls until the provider/OAuth branch begins."""
    driver = harness.driver
    assert wizard._pages.currentIndex() == 0
    driver.click(wizard._next)
    assert wizard._pages.currentIndex() == 1
    driver.click(wizard._advanced_mode if advanced else wizard._simple_mode)
    driver.click(wizard._next)
    assert wizard._pages.currentIndex() == 2
    driver.replace_text(wizard._name, name)
    driver.click(wizard._next)
    assert wizard._pages.currentIndex() == (3 if advanced else 4)


def _finish_from_provider_or_oauth(
    harness: OnboardingHarness,
    wizard,
    *,
    advanced: bool,
    tts: str = "none",
    stt: str = "none",
    open_chat: bool = False,
) -> str:
    """Finish the remaining visible pages and return trial guidance."""
    driver = harness.driver
    if advanced:
        assert wizard._pages.currentIndex() == 3
        driver.click(wizard._next)
    assert wizard._pages.currentIndex() == 4
    driver.click(wizard._next)
    assert wizard._pages.currentIndex() == 5
    _select(wizard._tts, tts, driver)
    _select(wizard._stt, stt, driver)
    driver.click(wizard._next)
    assert wizard._pages.currentIndex() == 6
    guidance = wizard._trial_steps.text()
    wizard._open_chat.setChecked(open_chat)
    driver.click(wizard._next)
    driver.pump()
    return guidance


def test_first_overlay_launch_runs_real_wizard_applies_profile_and_opens_chat(
    onboarding_harness,
    monkeypatch,
):
    """Fresh UI-host startup reaches setup, installer scheduling, and first chat."""
    from core.setup_check import run_setup_check
    from runtime.workers import ui_host

    harness = onboarding_harness
    host = object.__new__(ui_host.QtProtocolHost)
    host._onboarding = None
    applied: list[dict[str, str]] = []
    chats: list[bool] = []
    monkeypatch.setattr(host, "_settings_applied", applied.append)
    monkeypatch.setattr(
        host,
        "_show_chat",
        lambda *, force_new=False: chats.append(bool(force_new)) or {"shown": True},
    )

    assert not harness.env_path.exists()
    host._show_onboarding_if_needed()
    harness.driver.wait(
        lambda: host._onboarding is not None and host._onboarding.isVisible(),
        "fresh-install onboarding wizard",
    )
    wizard = host._onboarding
    try:
        _select(wizard._app_language, "fr", harness.driver)
        _select(wizard._assistant_language, "French", harness.driver)
        _select(wizard._theme_mode, "dark", harness.driver)

        harness.driver.click(wizard._next)
        harness.driver.click(wizard._advanced_mode)
        harness.driver.click(wizard._next)
        assert wizard._pages.currentIndex() == 2
        harness.driver.click(wizard._back)
        assert wizard._pages.currentIndex() == 1
        harness.driver.click(wizard._next)
        harness.driver.replace_text(wizard._name, "First Runtime User")
        harness.driver.click(wizard._next)

        _select(wizard._provider, "custom", harness.driver)
        harness.driver.replace_text(wizard._provider_model, "first-run-model")
        harness.driver.replace_text(
            wizard._custom_base_url,
            "http://127.0.0.1:1234/v1",
        )
        harness.driver.replace_text(wizard._provider_key, "first-run-key")

        guidance = _finish_from_provider_or_oauth(
            harness,
            wizard,
            advanced=True,
            tts="local",
            stt="local",
            open_chat=True,
        )
        harness.driver.wait(lambda: chats == [True], "first chat after onboarding")

        saved = harness.saved()
        assert saved["WISP_ONBOARDING_COMPLETE"] == "True"
        assert saved["APP_LANGUAGE"] == "fr"
        assert saved["ASSISTANT_LANGUAGE"] == "French"
        assert saved["THEME_MODE"] == "dark"
        assert saved["PROFILE_1_LLM_PROVIDER"] == "custom"
        assert saved["PROFILE_1_LLM_MODEL"] == "first-run-model"
        assert saved["CUSTOM_BASE_URL"] == "http://127.0.0.1:1234/v1"
        assert harness.secrets == [("CUSTOM_API_KEY", "first-run-key")]
        assert applied == [{"source": "onboarding"}]
        assert len(harness.installers) == 1
        assert harness.installers[0]["display_name"] == "Local speech"
        assert harness.installers[0]["external_plan_extra"]["post_install"] == "speech_prepare"
        assert "Ctrl+Q" in guidance if sys.platform == "win32" else "Ctrl+Alt+Space" in guidance

        # The same automatic entry must not reopen after completion.
        harness.driver.wait(lambda: host._onboarding is None, "wizard teardown")
        host._show_onboarding_if_needed()
        harness.driver.pump()
        assert host._onboarding is None

        rows = run_setup_check()
        assert rows and all({"name", "status", "message"} <= set(row) for row in rows)
    finally:
        try:
            wizard.close()
            wizard.deleteLater()
        except RuntimeError:
            pass
        harness.driver.pump()


def test_onboarding_language_assistant_and_theme_choice_matrix(onboarding_harness):
    """Every offered language and theme value survives the real Finish action."""
    from core.prompt_i18n import assistant_language_instruction

    harness = onboarding_harness
    app_languages = [value for _label, value in harness.onboarding.LANGUAGE_OPTIONS]
    assistant_languages = [
        "",
        "match_user",
        "English",
        "Chinese",
        "Chinese (Traditional)",
        "Spanish",
        "French",
        "German",
        "Japanese",
        "Korean",
        "Portuguese",
        "Hindi",
    ]

    dimensions = (
        ("app_language", app_languages),
        ("assistant_language", assistant_languages),
        ("theme", ["system", "light", "dark"]),
    )
    for dimension, values in dimensions:
        for index, value in enumerate(values):
            harness.reset_saved_state()
            applied: list[bool] = []

            def apply_to_runtime(
                open_chat: bool,
                applied_results: list[bool] = applied,
            ) -> None:
                harness.config.reload()
                applied_results.append(bool(open_chat))

            wizard = harness.new_wizard(on_complete=apply_to_runtime)
            try:
                if dimension == "app_language":
                    _select(wizard._app_language, value, harness.driver)
                elif dimension == "assistant_language":
                    _select(wizard._assistant_language, value, harness.driver)
                else:
                    _select(wizard._theme_mode, value, harness.driver)
                _open_profile_page(
                    harness,
                    wizard,
                    advanced=False,
                    name=f"Choice {dimension} {index}",
                )
                guidance = _finish_from_provider_or_oauth(
                    harness,
                    wizard,
                    advanced=False,
                )
                saved = harness.saved()
                key = {
                    "app_language": "APP_LANGUAGE",
                    "assistant_language": "ASSISTANT_LANGUAGE",
                    "theme": "THEME_MODE",
                }[dimension]
                assert saved[key] == value
                assert applied == [False]
                assert getattr(harness.config, key) == value
                if dimension == "assistant_language":
                    instruction = assistant_language_instruction(value)
                    if instruction:
                        assert instruction in harness.config.get_system_prompt()
                assert guidance.strip()
            finally:
                _dispose_wizard(wizard, harness.driver)

    # System-default values are already selected when the widget opens, so
    # they persist without emitting a change-preview signal.
    assert set(app_languages) - {""} <= set(harness.language_previews)
    assert {"light", "dark"} <= set(harness.theme_previews)


def test_onboarding_every_provider_model_endpoint_and_key_matrix(onboarding_harness):
    """Every provider offered by setup persists its editable route and secret."""
    harness = onboarding_harness
    providers = list(harness.onboarding._PROVIDER_DEFAULTS)
    for index, provider in enumerate(providers):
        harness.reset_saved_state()
        harness.secrets.clear()
        wizard = harness.new_wizard()
        try:
            _open_profile_page(
                harness,
                wizard,
                advanced=True,
                name=f"Provider {index}",
            )
            _select(wizard._provider, provider, harness.driver)
            model = f"acceptance-{provider}-model"
            harness.driver.replace_text(wizard._provider_model, model)
            if provider == "custom":
                harness.driver.replace_text(
                    wizard._custom_base_url,
                    "https://provider.example.test/v1",
                )
            if provider != "ollama":
                harness.driver.replace_text(
                    wizard._provider_key,
                    f"{provider}-acceptance-key",
                )
            _finish_from_provider_or_oauth(harness, wizard, advanced=True)

            saved = harness.saved()
            assert saved["PROFILE_1_LLM_PROVIDER"] == provider
            assert saved["PROFILE_1_LLM_MODEL"] == model
            if provider == "custom":
                assert saved["CUSTOM_BASE_URL"] == "https://provider.example.test/v1"
            if provider == "ollama":
                assert harness.secrets == []
            else:
                assert harness.secrets == [
                    (
                        harness.onboarding._PROVIDER_SECRET_NAMES[provider],
                        f"{provider}-acceptance-key",
                    )
                ]
        finally:
            _dispose_wizard(wizard, harness.driver)


@pytest.mark.parametrize(
    ("explicit_provider", "oauth_connected", "expected_provider"),
    (
        ("anthropic", True, "anthropic"),
        ("", True, "chatgpt"),
        ("", False, "openai"),
    ),
)
def test_onboarding_provider_vs_chatgpt_signin_precedence_matrix(
    onboarding_harness,
    monkeypatch,
    explicit_provider,
    oauth_connected,
    expected_provider,
):
    """Explicit provider, successful ChatGPT sign-in, and neither stay distinct."""
    from core.auth import chatgpt as chatgpt_auth

    harness = onboarding_harness
    started: list[bool] = []
    tokens = {"access_token": "stored-by-auth-boundary"} if oauth_connected else None

    def start_browser_login(on_success, _on_error):
        started.append(True)
        if tokens:
            on_success(tokens)

    monkeypatch.setattr(chatgpt_auth, "start_browser_login", start_browser_login)
    monkeypatch.setattr(chatgpt_auth, "get_tokens", lambda: tokens)
    wizard = harness.new_wizard()
    try:
        advanced = bool(explicit_provider)
        _open_profile_page(
            harness,
            wizard,
            advanced=advanced,
            name="OAuth precedence",
        )
        if explicit_provider:
            _select(wizard._provider, explicit_provider, harness.driver)
            harness.driver.click(wizard._next)
        assert wizard._pages.currentIndex() == 4
        if oauth_connected:
            harness.driver.click(wizard._oauth_button)
            wizard._poll_oauth()
            harness.driver.pump()
            assert wizard._oauth_button.text() == "Connected"
        harness.driver.click(wizard._next)
        _select(wizard._tts, "none", harness.driver)
        _select(wizard._stt, "none", harness.driver)
        harness.driver.click(wizard._next)
        harness.driver.click(wizard._next)

        saved = harness.saved()
        assert saved["PROFILE_1_LLM_PROVIDER"] == expected_provider
        assert bool(started) is oauth_connected
    finally:
        _dispose_wizard(wizard, harness.driver)


@pytest.mark.parametrize("tts", ("none", "local", "cloud"))
@pytest.mark.parametrize("stt", ("none", "local", "cloud"))
def test_onboarding_tts_by_stt_full_choice_matrix(
    onboarding_harness,
    tts,
    stt,
):
    """All 3 x 3 voice preference combinations persist and schedule correctly."""
    harness = onboarding_harness
    completed: list[bool] = []
    wizard = harness.new_wizard(on_complete=completed.append)
    try:
        _open_profile_page(
            harness,
            wizard,
            advanced=False,
            name=f"Speech {tts} {stt}",
        )
        _finish_from_provider_or_oauth(
            harness,
            wizard,
            advanced=False,
            tts=tts,
            stt=stt,
            open_chat=False,
        )
        saved = harness.saved()
        assert saved["WISP_TTS_PREFERENCE"] == tts
        assert saved["WISP_STT_PREFERENCE"] == stt
        assert completed == [False]

        local_tts = tts == "local"
        local_stt = stt == "local"
        if not local_tts and not local_stt:
            assert harness.installers == []
        else:
            assert len(harness.installers) == 1
            expected = (
                "speech_prepare"
                if local_tts and local_stt
                else "kokoro_prepare"
                if local_tts
                else "stt_prepare"
            )
            assert harness.installers[0]["external_plan_extra"]["post_install"] == expected
    finally:
        _dispose_wizard(wizard, harness.driver)


@pytest.mark.parametrize("platform", ("win32", "darwin", "linux"))
@pytest.mark.parametrize("open_chat", (False, True))
def test_onboarding_trial_guidance_and_open_chat_decision_matrix(
    onboarding_harness,
    monkeypatch,
    platform,
    open_chat,
):
    """Platform shortcut guidance and the first-chat checkbox are independent."""
    harness = onboarding_harness
    monkeypatch.setattr(harness.onboarding.sys, "platform", platform)
    completed: list[bool] = []
    wizard = harness.new_wizard(on_complete=completed.append)
    try:
        _open_profile_page(
            harness,
            wizard,
            advanced=False,
            name=f"Guidance {platform} {open_chat}",
        )
        guidance = _finish_from_provider_or_oauth(
            harness,
            wizard,
            advanced=False,
            open_chat=open_chat,
        )
        expected_hotkey = "Ctrl+Q" if platform == "win32" else "Ctrl+Alt+Space"
        assert expected_hotkey in guidance
        assert "F9" in guidance and "F7" in guidance
        assert completed == [open_chat]
    finally:
        _dispose_wizard(wizard, harness.driver)
