"""User-visible profile workflows spanning setup, Settings, disk, and reloads.

These tests deliberately avoid asserting implementation details such as which
helper method owns a state transition. They interact with the wizard buttons,
the Settings profile menu, editable model controls, and Save changes, then
verify what a user sees after reopening the app surface.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts.runtime_test_harness import QtUserDriver

pytestmark = pytest.mark.workflow


@dataclass
class ProfileWorkflow:
    """Isolated app state used by the profile workflow scenarios."""

    env_path: Path
    app: Any
    config: Any
    onboarding: Any
    settings_dialog: Any
    settings_env: Any

    def saved(self) -> dict[str, str]:
        return self.settings_env.read_settings_env()

    def reload_runtime(self) -> None:
        self.config.reload()

    def finish_wizard(
        self,
        *,
        name: str,
        provider: str = "anthropic",
        model: str = "claude-user-choice",
        open_chat: bool = False,
    ) -> dict[str, str]:
        """Complete every visible advanced-setup page using its real buttons."""
        completed: list[bool] = []
        wizard = self.onboarding.OnboardingWizard(on_complete=completed.append)

        assert wizard._pages.currentIndex() == 0
        wizard._next.click()
        assert wizard._pages.currentIndex() == 1

        wizard._advanced_mode.click()
        wizard._next.click()
        assert wizard._pages.currentIndex() == 2
        assert wizard._next.isEnabled() is False

        wizard._name.setText(name)
        assert wizard._next.isEnabled() is True
        wizard._next.click()
        assert wizard._pages.currentIndex() == 3

        provider_index = wizard._provider.findData(provider)
        assert provider_index >= 0
        wizard._provider.setCurrentIndex(provider_index)
        wizard._provider_model.setCurrentText(model)
        wizard._next.click()
        assert wizard._pages.currentIndex() == 4

        wizard._next.click()
        assert wizard._pages.currentIndex() == 5
        wizard._next.click()
        assert wizard._pages.currentIndex() == 6

        wizard._open_chat.setChecked(open_chat)
        assert wizard._next.text() == "Finish setup"
        wizard._next.click()

        assert completed == [open_chat]
        saved = self.saved()
        self.app.processEvents()
        return saved

    def open_settings(self):
        dialog = self.settings_dialog.SettingsDialog()
        dialog._save_api_keys_to_keychain = lambda: True
        dialog._capability_warnings_for_values = lambda _vals: ([], {})
        dialog._set_warning_markers = lambda _warnings: None
        return dialog

    def choose_profile(self, dialog, label: str) -> None:
        menu = dialog._profiles_btn.menu()
        action = next((item for item in menu.actions() if item.text() == label), None)
        assert action is not None, [item.text() for item in menu.actions()]
        action.trigger()
        self.app.processEvents()

    def save_changes(self, dialog) -> None:
        assert dialog._apply_btn.isEnabled() is True
        dialog._apply_btn.click()
        self.app.processEvents()
        assert dialog._apply_btn.isEnabled() is False

    def close_settings(self, dialog) -> None:
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()


@pytest.fixture
def profile_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProfileWorkflow:
    """Run profile flows against one real, isolated .env and live config reload."""
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from core import secret_store, tts
    from core.llm_clients import client as llm_client
    from core.system import autostart
    from ui import onboarding
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel import env as settings_env
    from ui.shared import theme

    env_path = tmp_path / "profile-workflow.env"
    app = QApplication.instance() or QApplication(["wisp-profile-workflows"])

    config_snapshot: dict[str, object] = {}
    for name, value in vars(config).items():
        if not name.isupper():
            continue
        if isinstance(value, list):
            config_snapshot[name] = list(value)
        elif isinstance(value, dict):
            config_snapshot[name] = dict(value)
        else:
            config_snapshot[name] = value
    original_loaded_keys = set(config._LOADED_DOTENV_KEYS)
    original_environ = dict(os.environ)
    managed_keys = set(original_loaded_keys)

    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(config, "_ENV_FILE", env_path)
    monkeypatch.setattr(secret_store, "refresh_cache", lambda: None)
    monkeypatch.setattr(secret_store, "has_secret", lambda _name: False)
    monkeypatch.setattr(autostart, "sync_start_on_login", lambda _enabled: None)
    monkeypatch.setattr(llm_client, "reset_clients", lambda: None)
    monkeypatch.setattr(tts, "reset_connections", lambda: None)
    monkeypatch.setattr(theme, "apply_app_theme", lambda *_args, **_kwargs: None)

    def _reload_isolated_env() -> None:
        current = settings_env.read_settings_env()
        managed_keys.update(current)
        for key in managed_keys:
            if key in current:
                os.environ[key] = current[key]
            else:
                os.environ.pop(key, None)
        config._LOADED_DOTENV_KEYS = set(current)

    monkeypatch.setattr(config, "_reload_dotenv", _reload_isolated_env)
    _reload_isolated_env()
    config._load_config()

    workflow = ProfileWorkflow(
        env_path=env_path,
        app=app,
        config=config,
        onboarding=onboarding,
        settings_dialog=settings_dialog,
        settings_env=settings_env,
    )
    try:
        yield workflow
    finally:
        for key in managed_keys:
            if key in original_environ:
                os.environ[key] = original_environ[key]
            else:
                os.environ.pop(key, None)
        for name, value in config_snapshot.items():
            current = getattr(config, name, None)
            if isinstance(current, list) and isinstance(value, list):
                current[:] = value
            elif isinstance(current, dict) and isinstance(value, dict):
                current.clear()
                current.update(value)
            else:
                setattr(config, name, value)
        config._LOADED_DOTENV_KEYS = original_loaded_keys


def _main_model(dialog) -> str:
    row = dialog._model_section_rows["LLM"][0]
    return dialog._model_value(row)


def _replace_main_model(dialog, model: str) -> None:
    """Type into the same custom-model editor exposed by the Settings UI."""
    row = dialog._model_section_rows["LLM"][0]
    combo = row["model_combo"]
    custom_index = combo.findData("__custom__")
    assert custom_index >= 0
    combo.setCurrentIndex(custom_index)
    row["model_edit"].setText(model)


def test_first_run_wizard_profile_is_active_in_runtime_and_visible_in_settings(profile_workflow: ProfileWorkflow):
    """A first-time user's wizard choices reach both runtime and Settings."""
    assert profile_workflow.onboarding.should_show_onboarding({}, env_file_exists=False) is True

    saved = profile_workflow.finish_wizard(
        name="Ada Lovelace",
        provider="anthropic",
        model="claude-first-run",
        open_chat=True,
    )

    assert saved["WISP_ONBOARDING_COMPLETE"] == "True"
    assert saved["PROFILE_COUNT"] == "1"
    assert saved["PROFILE_1_ID"] == "ada-lovelace"
    assert saved["PROFILE_1_LABEL"] == "Ada Lovelace"
    assert saved["PROFILE_1_LLM_PROVIDER"] == "anthropic"
    assert saved["PROFILE_1_LLM_MODEL"] == "claude-first-run"
    assert saved["ACTIVE_PROFILE"] == "ada-lovelace"
    assert saved["SETTINGS_PROFILE"] == "ada-lovelace"

    profile_workflow.reload_runtime()
    assert profile_workflow.config.ACTIVE_PROFILE == "ada-lovelace"
    assert profile_workflow.config.LLM_PROVIDER == "anthropic"
    assert profile_workflow.config.LLM_MODEL == "claude-first-run"

    dialog = profile_workflow.open_settings()
    try:
        assert dialog._profiles_btn.text() == "Ada Lovelace"
        assert _main_model(dialog) == "claude-first-run"
        assert dialog._apply_btn.isEnabled() is False
    finally:
        profile_workflow.close_settings(dialog)


def test_switch_save_reopen_and_edit_only_the_selected_custom_profile(profile_workflow: ProfileWorkflow):
    """Custom-profile switching persists and edits do not leak into another profile."""
    profile_workflow.finish_wizard(name="Work", provider="anthropic", model="claude-work")
    profile_workflow.finish_wizard(name="Personal", provider="openai", model="gpt-personal")
    profile_workflow.reload_runtime()

    dialog = profile_workflow.open_settings()
    try:
        assert dialog._profiles_btn.text() == "Personal"
        assert _main_model(dialog) == "gpt-personal"

        profile_workflow.choose_profile(dialog, "Work")
        assert dialog._profiles_btn.text() == "Work"
        assert _main_model(dialog) == "claude-work"
        profile_workflow.save_changes(dialog)
    finally:
        profile_workflow.close_settings(dialog)

    reopened = profile_workflow.open_settings()
    try:
        assert reopened._profiles_btn.text() == "Work"
        assert _main_model(reopened) == "claude-work"

        _replace_main_model(reopened, "claude-work-edited")
        profile_workflow.app.processEvents()
        profile_workflow.save_changes(reopened)
    finally:
        profile_workflow.close_settings(reopened)

    saved = profile_workflow.saved()
    assert saved["ACTIVE_PROFILE"] == "work"
    assert saved["PROFILE_1_LLM_MODEL"] == "claude-work-edited"
    assert saved["PROFILE_2_LLM_MODEL"] == "gpt-personal"

    profile_workflow.reload_runtime()
    assert profile_workflow.config.ACTIVE_PROFILE == "work"
    assert profile_workflow.config.LLM_MODEL == "claude-work-edited"


def test_low_setup_switch_survives_save_reopen_and_switch_back(profile_workflow: ProfileWorkflow):
    """The built-in profile behaves like a real selectable profile across reloads."""
    profile_workflow.finish_wizard(name="Work", provider="anthropic", model="claude-work")
    profile_workflow.reload_runtime()

    dialog = profile_workflow.open_settings()
    try:
        profile_workflow.choose_profile(dialog, "Low setup")
        assert dialog._profiles_btn.text() == "Low setup"
        assert _main_model(dialog) == "gpt-5.5"
        profile_workflow.save_changes(dialog)
    finally:
        profile_workflow.close_settings(dialog)

    saved = profile_workflow.saved()
    assert saved["ACTIVE_PROFILE"] == "default"
    assert saved["SETTINGS_PROFILE"] == "default"
    assert saved["WISP_SETTINGS_PRESET"] == "low_setup"
    assert profile_workflow.config.ACTIVE_PROFILE == "default"
    assert profile_workflow.config.LLM_PROVIDER == "chatgpt"
    assert profile_workflow.config.LLM_MODEL == "gpt-5.5"

    reopened = profile_workflow.open_settings()
    try:
        assert reopened._profiles_btn.text() == "Low setup"
        profile_workflow.choose_profile(reopened, "Work")
        assert reopened._profiles_btn.text() == "Work"
        assert _main_model(reopened) == "claude-work"
        profile_workflow.save_changes(reopened)
    finally:
        profile_workflow.close_settings(reopened)

    saved = profile_workflow.saved()
    assert saved["ACTIVE_PROFILE"] == "work"
    assert saved["SETTINGS_PROFILE"] == "work"
    assert "WISP_SETTINGS_PRESET" not in saved
    assert profile_workflow.config.ACTIVE_PROFILE == "work"
    assert profile_workflow.config.LLM_MODEL == "claude-work"


def test_repeating_setup_with_the_same_name_keeps_both_profiles_selectable(profile_workflow: ProfileWorkflow):
    """Two same-name wizard profiles remain distinguishable and selectable."""
    profile_workflow.finish_wizard(name="Pat", provider="anthropic", model="claude-pat")
    profile_workflow.finish_wizard(name="Pat", provider="openai", model="gpt-pat")
    profile_workflow.reload_runtime()

    saved = profile_workflow.saved()
    assert saved["PROFILE_1_ID"] == "pat"
    assert saved["PROFILE_2_ID"] == "pat-2"

    dialog = profile_workflow.open_settings()
    try:
        labels = [action.text() for action in dialog._profiles_btn.menu().actions()]
        assert "Pat (pat)" in labels
        assert "Pat (pat-2)" in labels

        profile_workflow.choose_profile(dialog, "Pat (pat)")
        assert dialog._profiles_btn.text() == "Pat (pat)"
        assert _main_model(dialog) == "claude-pat"
        profile_workflow.choose_profile(dialog, "Pat (pat-2)")
        assert dialog._profiles_btn.text() == "Pat (pat-2)"
        assert _main_model(dialog) == "gpt-pat"
    finally:
        profile_workflow.close_settings(dialog)


def test_rename_and_delete_profiles_leave_a_truthful_selector(
    profile_workflow: ProfileWorkflow,
    monkeypatch: pytest.MonkeyPatch,
):
    """Profile management updates disk, menu labels, and the selected display."""
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    profile_workflow.finish_wizard(name="Work", provider="anthropic", model="claude-work")
    profile_workflow.finish_wizard(name="Personal", provider="openai", model="gpt-personal")
    profile_workflow.reload_runtime()

    dialog = profile_workflow.open_settings()
    try:
        profile_workflow.choose_profile(dialog, "Work")
        profile_workflow.save_changes(dialog)

        monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Work", True))
        monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Focused Work", True))
        profile_workflow.choose_profile(dialog, "Rename profile...")

        assert dialog._profiles_btn.text() == "Focused Work"
        assert profile_workflow.saved()["PROFILE_1_LABEL"] == "Focused Work"

        monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Personal", True))
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        profile_workflow.choose_profile(dialog, "Delete profile...")

        saved = profile_workflow.saved()
        assert saved["PROFILE_COUNT"] == "1"
        assert saved["PROFILE_1_ID"] == "work"
        assert saved["PROFILE_1_LABEL"] == "Focused Work"
        assert saved["ACTIVE_PROFILE"] == "work"

        # The remaining profile is still selected and active after deleting the
        # inactive one, so its refreshed management action can delete it directly.
        profile_workflow.choose_profile(dialog, "Delete profile...")

        saved = profile_workflow.saved()
        assert saved["PROFILE_COUNT"] == "0"
        assert "ACTIVE_PROFILE" not in saved
        assert "SETTINGS_PROFILE" not in saved
        assert dialog._profiles_btn.text() == "Default"
    finally:
        profile_workflow.close_settings(dialog)


@pytest.mark.parametrize(
    ("mode", "expected_file_tools"),
    [
        ("off", set()),
        ("read", {"list_files", "read_file"}),
        ("ask", {"list_files", "read_file", "create_file", "edit_file", "write_file"}),
        ("auto", {"list_files", "read_file", "create_file", "edit_file", "write_file"}),
    ],
)
def test_every_file_access_mode_saves_reloads_and_changes_runtime_tool_grants(
    profile_workflow: ProfileWorkflow,
    mode: str,
    expected_file_tools: set[str],
):
    """Real Settings choices must drive the corresponding runtime tool policy."""

    from core.tools.local_files import LOCAL_FILE_TOOLS
    from runtime.supervisor import tool_modes

    profile_workflow.finish_wizard(name="File Access", provider="anthropic", model="claude-files")
    if mode == "off":
        # Advanced setup defaults to Off. Persist Read first so selecting Off is
        # a real user change that exercises Save rather than a no-op.
        profile_workflow.settings_env.write_settings_env({"CALLER_1_FILE_ACCESS": "read"})
    profile_workflow.reload_runtime()
    dialog = profile_workflow.open_settings()
    driver = QtUserDriver(profile_workflow.app)
    try:
        combo = dialog._caller_blocks[0]["file_access"]
        driver.select_combo_data(combo, mode)
        profile_workflow.save_changes(dialog)
    finally:
        profile_workflow.close_settings(dialog)

    assert profile_workflow.saved()["CALLER_1_FILE_ACCESS"] == mode
    profile_workflow.reload_runtime()
    caller = profile_workflow.config.CALLER_ROWS[0]
    assert caller["file_access"] == mode
    granted = set(tool_modes.allowed_model_tools(caller)) & set(LOCAL_FILE_TOOLS)
    assert granted == expected_file_tools
