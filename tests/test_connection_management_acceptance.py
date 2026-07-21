"""Real Settings workflows for provider connections and model discovery."""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.workflow,
    pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed"),
]


def _new_dialog(monkeypatch: pytest.MonkeyPatch, *, env: dict[str, str] | None = None):
    from ui.settings_panel import dialog as settings_dialog

    persisted = env if env is not None else {}
    monkeypatch.setattr(settings_dialog.SettingsDialog, "_schedule_open_status_refresh", lambda _self: None)
    monkeypatch.setattr(settings_dialog, "_read_env", lambda: dict(persisted))
    dialog = settings_dialog.SettingsDialog()
    dialog.show()
    dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("Connections"))
    return dialog


def _close(dialog, app) -> None:
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def _remove_loaded_rows(dialog) -> None:
    dialog._loading_values = True
    try:
        for row in list(dialog._api_key_rows):
            dialog._remove_api_key_row(row)
    finally:
        dialog._loading_values = False


def _trigger_real_menu_action(app, button, action_text: str) -> None:
    """Open a real Qt popup, trigger one visible action, and close its event loop."""

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMenu

    def choose_active_action() -> None:
        menu = QApplication.activePopupWidget()
        assert isinstance(menu, QMenu)
        try:
            action = next(action for action in menu.actions() if action.text() == action_text)
            action.trigger()
        finally:
            menu.close()

    QTimer.singleShot(0, choose_active_action)
    button.click()
    app.processEvents()


def _install_save_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    persisted: dict[str, str],
) -> dict[str, str]:
    """Keep the real Save button/state machine while isolating external effects."""

    import config
    from core import secret_store, tts
    from core.llm_clients import client as llm
    from core.system import autostart
    from ui.settings_panel import dialog as settings_dialog
    from ui.shared import theme

    secrets: dict[str, str] = {}

    def write_env(values: dict[str, str], remove_keys: set[str] | None = None) -> None:
        for key in remove_keys or set():
            persisted.pop(key, None)
        persisted.update({key: str(value) for key, value in values.items()})

    monkeypatch.setattr(settings_dialog, "_read_env", lambda: dict(persisted))
    monkeypatch.setattr(settings_dialog, "_write_env", write_env)
    monkeypatch.setattr(config, "reload", lambda: None)
    monkeypatch.setattr(llm, "reset_clients", lambda: None)
    monkeypatch.setattr(tts, "reset_connections", lambda: None)
    monkeypatch.setattr(theme, "apply_app_theme", lambda: None)
    monkeypatch.setattr(autostart, "sync_start_on_login", lambda _enabled: None)
    monkeypatch.setattr(secret_store, "migrate_env_secrets", lambda _env: None)
    monkeypatch.setattr(secret_store, "set_secret", lambda name, value: secrets.__setitem__(name, value))
    monkeypatch.setattr(secret_store, "delete_secret", lambda name: secrets.pop(name, None))
    monkeypatch.setattr(secret_store, "has_secret", lambda name: bool(secrets.get(name)))
    monkeypatch.setattr(secret_store, "get_secret", lambda name: secrets.get(name, ""))
    monkeypatch.setattr(secret_store, "get_keychain_secret", lambda name: secrets.get(name, ""))
    monkeypatch.setattr(
        settings_dialog.SettingsDialog,
        "_capability_warnings_for_values",
        lambda _self, _values: ([], {}),
    )
    return secrets


def test_add_alias_search_filter_and_expand_every_connection_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual Add connection modal and list tools work for the full catalog."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QListWidget, QPushButton

    from ui.settings_panel import dialog as settings_dialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = _new_dialog(monkeypatch)
    try:
        _remove_loaded_rows(dialog)
        add_button = next(
            button
            for button in dialog.findChildren(QPushButton)
            if button.text() == "+ Add connection"
        )

        for index, provider in enumerate(settings_dialog._CONNECTION_PROVIDER_IDS):
            def choose_provider(provider_id=provider) -> None:
                modal = QApplication.activeModalWidget()
                assert modal is not None
                catalog = modal.findChild(QListWidget, "settingsProviderCatalog")
                assert catalog is not None
                search = next(
                    field
                    for field in modal.findChildren(QLineEdit)
                    if field.placeholderText() == "Search providers..."
                )
                search.setText(provider_id)
                item = next(
                    catalog.item(row)
                    for row in range(catalog.count())
                    if catalog.item(row).data(Qt.ItemDataRole.UserRole) == provider_id
                )
                assert not item.isHidden()
                catalog.setCurrentItem(item)
                box = modal.findChild(QDialogButtonBox)
                assert box is not None
                box.button(QDialogButtonBox.StandardButton.Ok).click()

            QTimer.singleShot(0, choose_provider)
            add_button.click()
            assert len(dialog._api_key_rows) == index + 1
            row = dialog._api_key_rows[-1]
            assert row["provider"].currentData() == provider
            assert row["alias"].text() == ""
            row["alias"].setText(f"alias-{provider}")
            assert row["alias"].text() == f"alias-{provider}"

        rows = list(dialog._api_key_rows)
        dialog._connections_expanded = False
        dialog._refresh_connection_rows_filter()
        assert sum(not row["widget"].isHidden() for row in rows) == 6
        assert dialog._connections_show_more_btn.isVisible()
        dialog._connections_show_more_btn.click()
        assert all(not row["widget"].isHidden() for row in rows)
        dialog._connections_show_more_btn.click()
        assert sum(not row["widget"].isHidden() for row in rows) == 6

        dialog._connections_expanded = True
        dialog._refresh_connection_rows_filter()
        local_providers = {"ollama", "custom"}
        queries = (
            "",
            "no-such-connection",
            *settings_dialog._CONNECTION_PROVIDER_IDS,
            *(f"alias-{provider}" for provider in settings_dialog._CONNECTION_PROVIDER_IDS),
        )
        for mode in ("all", "cloud", "local"):
            dialog._connections_filter.setCurrentIndex(dialog._connections_filter.findData(mode))
            for query in queries:
                dialog._connections_search.setText(query)
                app.processEvents()
                expected = []
                for candidate in rows:
                    candidate_provider = candidate["provider"].currentData()
                    haystack = (
                        f"{candidate_provider} "
                        f"{settings_dialog._PROVIDER_LABELS[candidate_provider]} "
                        f"{candidate['alias'].text()}"
                    ).lower()
                    matches_text = not query or query in haystack
                    matches_mode = (
                        mode == "all"
                        or (mode == "local" and candidate_provider in local_providers)
                        or (mode == "cloud" and candidate_provider not in local_providers)
                    )
                    if matches_text and matches_mode:
                        expected.append(candidate)
                assert [
                    candidate for candidate in rows if not candidate["widget"].isHidden()
                ] == expected

        dialog._connections_filter.setCurrentIndex(dialog._connections_filter.findData("all"))
        for expanded in (False, True):
            dialog._connections_expanded = expanded
            dialog._connections_search.setText("alias-openai")
            dialog._refresh_connection_rows_filter()
            app.processEvents()
            assert [candidate for candidate in rows if not candidate["widget"].isHidden()] == [
                rows[settings_dialog._CONNECTION_PROVIDER_IDS.index("openai")]
            ]
            assert dialog._connections_show_more_btn.isHidden()

        dialog._connections_search.clear()
        dialog._connections_filter.setCurrentIndex(dialog._connections_filter.findData("local"))
        for expanded in (False, True):
            dialog._connections_expanded = expanded
            dialog._refresh_connection_rows_filter()
            app.processEvents()
            assert {
                candidate["provider"].currentData()
                for candidate in rows
                if not candidate["widget"].isHidden()
            } == local_providers
            assert dialog._connections_show_more_btn.isHidden()
    finally:
        _close(dialog, app)


def test_connection_save_keychain_remove_last_and_cancel_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Save stores secrets; only saved removal of the last provider row clears them."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel import dialog as settings_dialog

    app = QApplication.instance() or QApplication(sys.argv)
    persisted: dict[str, str] = {}
    secrets = _install_save_boundaries(monkeypatch, persisted)
    dialog = _new_dialog(monkeypatch, env=persisted)
    try:
        _remove_loaded_rows(dialog)
        first = dialog._add_api_key_row("openai", alias="Primary")
        first["key"].setText("openai-acceptance-key")
        dialog._fields["CUSTOM_BASE_URL"].setText("http://localhost:1234/v1")
        dialog._fields["CUSTOM_API_KEY"].setText("custom-acceptance-key")
        dialog._refresh_dirty_state()
        assert dialog._apply_btn.isEnabled()
        dialog._apply_btn.click()
        app.processEvents()

        assert secrets == {
            "OPENAI_API_KEY": "openai-acceptance-key",
            "CUSTOM_API_KEY": "custom-acceptance-key",
        }
        assert first["key"].text() == ""
        assert dialog._fields["CUSTOM_API_KEY"].text() == ""
        assert persisted["CUSTOM_BASE_URL"] == "http://localhost:1234/v1"
        assert persisted["WISP_CONNECTION_ALIAS_OPENAI"] == "Primary"

        sibling = dialog._add_api_key_row("openai", alias="Sibling")
        remove_first = next(
            button for button in first["widget"].findChildren(QPushButton) if button.text() == "✕"
        )
        remove_first.click()
        dialog._refresh_dirty_state()
        dialog._apply_btn.click()
        app.processEvents()
        assert secrets["OPENAI_API_KEY"] == "openai-acceptance-key"

        remove_last = next(
            button for button in sibling["widget"].findChildren(QPushButton) if button.text() == "✕"
        )
        remove_last.click()
        assert secrets["OPENAI_API_KEY"] == "openai-acceptance-key"
        dialog._refresh_dirty_state()
        dialog._apply_btn.click()
        app.processEvents()
        assert "OPENAI_API_KEY" not in secrets
        assert "WISP_CONNECTION_ALIAS_OPENAI" not in persisted
        dialog._load_values()
        assert all(row["provider"].currentData() != "openai" for row in dialog._api_key_rows)

        secrets["OPENAI_API_KEY"] = "clear-on-provider-change"
        changed = dialog._add_api_key_row("openai", alias="Changed")
        changed["provider"].setCurrentIndex(changed["provider"].findData("anthropic"))
        changed["key"].setText("anthropic-after-change")
        dialog._refresh_dirty_state()
        dialog._apply_btn.click()
        app.processEvents()
        assert "OPENAI_API_KEY" not in secrets
        assert secrets["ANTHROPIC_API_KEY"] == "anthropic-after-change"

        secrets["OPENAI_API_KEY"] = "keep-after-change-back"
        returning = dialog._add_api_key_row("openai", alias="Returning")
        returning["provider"].setCurrentIndex(returning["provider"].findData("anthropic"))
        returning["provider"].setCurrentIndex(returning["provider"].findData("openai"))
        dialog._refresh_dirty_state()
        dialog._apply_btn.click()
        app.processEvents()
        assert secrets["OPENAI_API_KEY"] == "keep-after-change-back"

        secrets["OPENAI_API_KEY"] = "keep-on-cancel"
        cancel_remove = next(
            button for button in returning["widget"].findChildren(QPushButton) if button.text() == "✕"
        )
        cancel_remove.click()
        cancel = dialog.findChild(QPushButton, "settingsCancelButton")
        assert cancel is not None
        cancel.click()
        assert secrets["OPENAI_API_KEY"] == "keep-on-cancel"
    finally:
        _close(dialog, app)


def test_every_custom_endpoint_menu_action_updates_real_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every Endpoints menu action applies its URL, model hint, and key hint."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel import dialog as settings_dialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = _new_dialog(monkeypatch)
    try:
        model_row = dialog._model_section_rows["LLM"][0]
        model_row["api_key_combo"].setCurrentIndex(model_row["api_key_combo"].findData("custom"))
        endpoint_button = next(
            button for button in dialog.findChildren(QPushButton) if button.text() == "Endpoints ▾"
        )
        for name, url, model_hint, api_key_hint in settings_dialog.SettingsDialog._CUSTOM_ENDPOINTS:
            dialog._fields["CUSTOM_BASE_URL"].clear()
            dialog._fields["CUSTOM_API_KEY"].clear()
            _trigger_real_menu_action(app, endpoint_button, name)
            assert dialog._fields["CUSTOM_BASE_URL"].text() == url
            assert model_hint in model_row["model_edit"].placeholderText()
            assert dialog._fields["CUSTOM_API_KEY"].text() == api_key_hint
    finally:
        _close(dialog, app)


def _wait_until(app, predicate, *, timeout: float = 5.0) -> None:
    """Process queued Qt events until a background workflow reaches its boundary."""

    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


def test_model_refresh_and_manual_name_every_provider_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every selectable provider can refresh models and retain an exact manual name."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core.auth import copilot_auth
    from core.llm_clients import client as llm
    from ui.settings_panel import dialog as settings_dialog

    app = QApplication.instance() or QApplication(sys.argv)
    calls: list[tuple[str, str, str]] = []
    expected_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(copilot_auth, "get_token", lambda: "copilot-key")
    monkeypatch.setattr(settings_dialog.secret_store, "get_keychain_secret", lambda name: f"stored-{name}")

    def safe_list_models(provider: str, *, api_key: str = "", base_url: str = ""):
        calls.append((provider, api_key, base_url))
        return [f"{provider}-live-a", f"{provider}-live-b"], ""

    monkeypatch.setattr(llm, "safe_list_models", safe_list_models)
    dialog = _new_dialog(monkeypatch)
    try:
        _remove_loaded_rows(dialog)
        dialog._fields["CUSTOM_BASE_URL"].setText("https://custom.example/v1")
        dialog._fields["CUSTOM_API_KEY"].setText("custom-key")
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("LLM"))
        app.processEvents()
        row = dialog._model_section_rows["LLM"][0]
        providers = (*settings_dialog._CONNECTION_PROVIDER_IDS, "chatgpt")
        for provider in providers:
            connection = (
                dialog._add_api_key_row(provider)
                if provider in settings_dialog._CONNECTION_PROVIDER_IDS
                else None
            )
            dialog._fill_credential_combo(row["api_key_combo"], provider)
            row["api_key_combo"].setCurrentIndex(row["api_key_combo"].findData(provider))

            credential_cases: list[tuple[str, str]]
            if provider in settings_dialog._PROVIDER_KEY_NAMES and provider != "custom":
                assert connection is not None
                key_name = settings_dialog._PROVIDER_KEY_NAMES[provider]
                connection["key"].setText(f"typed-{provider}")
                credential_cases = [
                    (f"typed-{provider}", ""),
                    (f"stored-{key_name}", ""),
                ]
            elif provider == "custom":
                credential_cases = [
                    ("custom-key", "https://custom.example/v1"),
                    ("stored-CUSTOM_API_KEY", "https://custom.example/v1"),
                ]
            elif provider == "copilot":
                assert connection is not None
                connection["key"].setText("typed-copilot")
                credential_cases = [("typed-copilot", ""), ("copilot-key", "")]
            else:
                credential_cases = [("", "")]

            for case_index, (expected_key, expected_url) in enumerate(credential_cases):
                if case_index == 1:
                    if provider == "custom":
                        dialog._fields["CUSTOM_API_KEY"].clear()
                    elif connection is not None:
                        connection["key"].clear()
                expected_calls.append((provider, expected_key, expected_url))
                row["refresh_btn"].click()
                _wait_until(app, row["refresh_btn"].isEnabled)
                assert calls[-1] == expected_calls[-1]
                assert [
                    row["model_combo"].itemData(index)
                    for index in range(row["model_combo"].count())
                ] == [
                    f"{provider}-live-a",
                    f"{provider}-live-b",
                    settings_dialog._CUSTOM_MODEL_SENTINEL,
                ]
                assert row["refresh_btn"].toolTip() == "Live: 2 models"

            row["model_combo"].setCurrentIndex(
                row["model_combo"].findData(settings_dialog._CUSTOM_MODEL_SENTINEL)
            )
            manual = f"exact/{provider}-manual-model"
            row["model_edit"].setText(manual)
            assert row["model_edit"].isVisible()
            assert dialog._model_value(row) == manual

        assert calls == expected_calls

        monkeypatch.setattr(llm, "safe_list_models", lambda *_args, **_kwargs: ([], "provider offline"))
        row["refresh_btn"].click()
        _wait_until(app, row["refresh_btn"].isEnabled)
        assert "provider offline" in row["refresh_btn"].toolTip()
        assert dialog._model_value(row) == manual
    finally:
        _close(dialog, app)


def test_custom_endpoint_and_exact_manual_model_reach_real_test_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local and remote Custom presets reach the production route probe unchanged."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from core.llm_clients import client as llm
    from ui.settings_panel import dialog as settings_dialog

    app = QApplication.instance() or QApplication(sys.argv)
    client_calls: list[dict[str, str]] = []
    completion_calls: list[dict[str, object]] = []

    class FakeStream:
        def __enter__(self):
            return iter([object()])

        def __exit__(self, _exc_type, _exc, _tb) -> bool:
            return False

    class FakeCompletions:
        def create(self, **kwargs):
            completion_calls.append(dict(kwargs))
            return FakeStream() if kwargs.get("stream") else object()

    class FakeClient:
        class Chat:
            completions = FakeCompletions()

        chat = Chat()

    def openai_client(**kwargs):
        client_calls.append(dict(kwargs))
        return FakeClient()

    monkeypatch.setattr(llm.sdk_clients, "openai_client", openai_client)
    dialog = _new_dialog(monkeypatch)
    try:
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("LLM"))
        app.processEvents()
        row = dialog._model_section_rows["LLM"][0]
        for fallback in list(dialog._model_section_rows["LLM"])[1:]:
            dialog._remove_model_section_row("LLM", fallback)
        dialog._fill_credential_combo(row["api_key_combo"], "custom")
        row["api_key_combo"].setCurrentIndex(row["api_key_combo"].findData("custom"))
        row["model_combo"].setCurrentIndex(
            row["model_combo"].findData(settings_dialog._CUSTOM_MODEL_SENTINEL)
        )
        endpoint_button = next(
            button for button in dialog.findChildren(QPushButton) if button.text() == "Endpoints ▾"
        )
        test_button = next(
            button
            for button in dialog.findChildren(QPushButton)
            if button.text() == "Test Chat model"
        )

        cases = (
            ("LM Studio (local)", "http://localhost:1234/v1", "exact/local-model"),
            ("OpenRouter", "https://openrouter.ai/api/v1", "exact/remote-model"),
        )
        for index, (preset, expected_url, exact_model) in enumerate(cases, start=1):
            _trigger_real_menu_action(app, endpoint_button, preset)
            dialog._fields["CUSTOM_API_KEY"].setText(f"custom-route-key-{index}")
            row["model_edit"].setText(exact_model)
            assert dialog._model_value(row) == exact_model

            test_button.click()
            _wait_until(
                app,
                lambda: len(client_calls) == index and not dialog._running_test_tokens,
            )

            assert client_calls[-1] == {
                "api_key": f"custom-route-key-{index}",
                "base_url": expected_url,
            }
            assert completion_calls[-1]["model"] == exact_model
            assert "✓ Primary — custom /" in dialog._llm_test_status_lbl.text()
            assert exact_model in dialog._llm_test_status_lbl.text()
    finally:
        _close(dialog, app)


def test_every_provider_reaches_its_real_chat_route_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All 25 chat providers traverse Settings, validation, and their real adapter."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from core.auth import chatgpt as chatgpt_auth
    from core.auth import copilot_auth, copilot_client
    from core.llm_clients import client as llm
    from ui.settings_panel import dialog as settings_dialog

    app = QApplication.instance() or QApplication(sys.argv)
    openai_requests: list[tuple[dict[str, object], dict[str, object]]] = []
    response_requests: list[tuple[dict[str, object], dict[str, object]]] = []
    anthropic_requests: list[tuple[dict[str, object], dict[str, object]]] = []
    copilot_requests: list[tuple[tuple[object, ...], dict[str, object]]] = []
    ollama_readiness: list[dict[str, object]] = []

    class FakeStream:
        def __enter__(self):
            return iter([object()])

        def __exit__(self, _exc_type, _exc, _tb) -> bool:
            return False

    class FakeCompletions:
        def __init__(self, factory_kwargs: dict[str, object]) -> None:
            self._factory_kwargs = factory_kwargs

        def create(self, **kwargs):
            openai_requests.append((dict(self._factory_kwargs), dict(kwargs)))
            return FakeStream() if kwargs.get("stream") else object()

    class FakeResponses:
        def __init__(self, factory_kwargs: dict[str, object]) -> None:
            self._factory_kwargs = factory_kwargs

        def create(self, **kwargs):
            response_requests.append((dict(self._factory_kwargs), dict(kwargs)))
            return object()

    class FakeOpenAIClient:
        def __init__(self, factory_kwargs: dict[str, object]) -> None:
            self.chat = type("Chat", (), {"completions": FakeCompletions(factory_kwargs)})()
            self.responses = FakeResponses(factory_kwargs)

    class FakeAnthropicMessages:
        def __init__(self, factory_kwargs: dict[str, object]) -> None:
            self._factory_kwargs = factory_kwargs

        def create(self, **kwargs):
            anthropic_requests.append((dict(self._factory_kwargs), dict(kwargs)))
            return object()

    class FakeAnthropicClient:
        def __init__(self, factory_kwargs: dict[str, object]) -> None:
            self.messages = FakeAnthropicMessages(factory_kwargs)

    monkeypatch.setattr(
        llm.sdk_clients,
        "openai_client",
        lambda **kwargs: FakeOpenAIClient(dict(kwargs)),
    )
    monkeypatch.setattr(
        llm.sdk_clients,
        "anthropic_client",
        lambda **kwargs: FakeAnthropicClient(dict(kwargs)),
    )
    monkeypatch.setattr(llm.sdk_clients, "httpx_client", lambda **_kwargs: object())
    monkeypatch.setattr(chatgpt_auth, "get_tokens", lambda: {"access_token": "oauth", "account_id": "acct"})
    monkeypatch.setattr(copilot_auth, "get_effective_token", lambda: "copilot-token")
    monkeypatch.setattr(
        settings_dialog.secret_store,
        "get_keychain_secret",
        lambda name: f"stored-{name}",
    )
    monkeypatch.setattr(
        copilot_client,
        "ask",
        lambda *args, **kwargs: copilot_requests.append((args, dict(kwargs))) or "OK",
    )
    monkeypatch.setattr(
        llm,
        "_ensure_ollama_running",
        lambda **kwargs: ollama_readiness.append(dict(kwargs)),
    )
    llm._codex_client = None
    llm._dynamic_openai_clients.clear()

    dialog = _new_dialog(monkeypatch)
    try:
        _remove_loaded_rows(dialog)
        connection_rows: dict[str, dict] = {}
        for provider in settings_dialog._CONNECTION_PROVIDER_IDS:
            connection = dialog._add_api_key_row(provider)
            connection_rows[provider] = connection
            if provider not in {"ollama", "custom", "copilot"}:
                connection["key"].setText(f"typed-{provider}")
            elif provider == "copilot":
                connection["key"].setText("typed-copilot")
        dialog._fields["CUSTOM_BASE_URL"].setText("https://custom.runtime.example/v1")
        dialog._fields["CUSTOM_API_KEY"].setText("typed-custom")
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("LLM"))
        app.processEvents()

        row = dialog._model_section_rows["LLM"][0]
        for fallback in list(dialog._model_section_rows["LLM"])[1:]:
            dialog._remove_model_section_row("LLM", fallback)
        test_button = next(
            button
            for button in dialog.findChildren(QPushButton)
            if button.text() == "Test Chat model"
        )
        providers = (*settings_dialog._CONNECTION_PROVIDER_IDS, "chatgpt")
        before_counts = (0, 0, 0, 0)
        selected_models: dict[str, str] = {}

        for provider in providers:
            dialog._fill_credential_combo(row["api_key_combo"], provider)
            row["api_key_combo"].setCurrentIndex(row["api_key_combo"].findData(provider))
            app.processEvents()
            models = list(settings_dialog._PROVIDER_MODELS.get(provider, []))
            model = models[0] if models else f"exact/{provider}-runtime-model"
            selected_models[provider] = model
            model_index = row["model_combo"].findData(model)
            if model_index >= 0:
                row["model_combo"].setCurrentIndex(model_index)
            else:
                row["model_combo"].setCurrentIndex(
                    row["model_combo"].findData(settings_dialog._CUSTOM_MODEL_SENTINEL)
                )
                row["model_edit"].setText(model)

            if provider in settings_dialog._PROVIDER_KEY_NAMES:
                key_name = settings_dialog._PROVIDER_KEY_NAMES[provider]
                credential_cases = [
                    (f"typed-{provider}" if provider != "custom" else "typed-custom", False),
                    (f"stored-{key_name}", True),
                ]
            else:
                credential_cases = [("ollama" if provider == "ollama" else "", False)]

            for expected_key, use_stored in credential_cases:
                if use_stored:
                    if provider == "custom":
                        dialog._fields["CUSTOM_API_KEY"].clear()
                    else:
                        connection_rows[provider]["key"].clear()
                test_button.click()
                _wait_until(app, lambda: not dialog._running_test_tokens)
                assert f"✓ Primary — {provider} / {model}: OK" in dialog._llm_test_status_lbl.text()
                after_counts = (
                    len(openai_requests),
                    len(response_requests),
                    len(anthropic_requests),
                    len(copilot_requests),
                )
                assert sum(after_counts) == sum(before_counts) + 1
                before_counts = after_counts
                if provider in llm._OPENAI_COMPAT_PROVIDER_SET:
                    factory, request = openai_requests[-1]
                    assert request["model"] == model
                    assert factory["api_key"] == expected_key
                    expected_base_url = (
                        "https://custom.runtime.example/v1"
                        if provider == "custom"
                        else llm._openai_compat_base_url(provider)
                    )
                    if provider == "openai":
                        assert "base_url" not in factory
                    elif expected_base_url:
                        assert factory["base_url"] == expected_base_url
                    else:
                        assert "base_url" not in factory
                elif provider == "anthropic":
                    factory, request = anthropic_requests[-1]
                    assert factory == {"api_key": expected_key}
                    assert request["model"] == model

        assert len(openai_requests) == sum(
            2 if provider in settings_dialog._PROVIDER_KEY_NAMES else 1
            for provider in providers
            if provider in llm._OPENAI_COMPAT_PROVIDER_SET
        )

        assert [factory["api_key"] for factory, _request in anthropic_requests] == [
            "typed-anthropic",
            "stored-ANTHROPIC_API_KEY",
        ]
        assert all(
            request
            == {
                "model": selected_models["anthropic"],
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "Reply with OK."}],
            }
            for _factory, request in anthropic_requests
        )
        assert response_requests[0][0]["base_url"] == "https://chatgpt.com/backend-api/codex"
        assert response_requests[0][1]["model"] == selected_models["chatgpt"]
        assert copilot_requests == [
            (
                ("Reply with OK.", selected_models["copilot"]),
                {"system": "Return exactly OK.", "allow_tools": False},
            )
        ]
        assert ollama_readiness == [{}, {}]
    finally:
        llm._codex_client = None
        llm._dynamic_openai_clients.clear()
        _close(dialog, app)
