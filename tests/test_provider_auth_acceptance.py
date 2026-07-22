"""Real Settings UI acceptance for ChatGPT, GitHub, and Copilot authentication."""

from __future__ import annotations

import importlib.util
import os
import sys
import webbrowser

import pytest

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed"),
]


def _new_settings_dialog(monkeypatch: pytest.MonkeyPatch):
    """Construct real Settings without starting unrelated status threads."""

    from ui.settings_panel import dialog as settings_dialog

    monkeypatch.setattr(settings_dialog.SettingsDialog, "_schedule_open_status_refresh", lambda _self: None)
    return settings_dialog.SettingsDialog()


def _close_settings(dialog, app) -> None:
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_chatgpt_settings_login_status_and_logout_real_button_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sign in, status refresh, error display, and sign out use the visible buttons."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core.auth import chatgpt as chatgpt_auth

    app = QApplication.instance() or QApplication(sys.argv)
    store: dict[str, object] = {"tokens": None}
    starts: list[str] = []
    clears: list[str] = []
    monkeypatch.setattr(chatgpt_auth, "get_tokens", lambda: store["tokens"])
    monkeypatch.setattr(
        chatgpt_auth,
        "start_browser_login",
        lambda on_success, _on_error: (
            starts.append("browser"),
            store.__setitem__("tokens", {"account_id": "acct-123456789"}),
            on_success(store["tokens"]),
        ),
    )
    monkeypatch.setattr(
        chatgpt_auth,
        "clear_tokens",
        lambda: (clears.append("chatgpt"), store.__setitem__("tokens", None)),
    )
    dialog = _new_settings_dialog(monkeypatch)
    try:
        dialog.show()
        app.processEvents()
        dialog._refresh_chatgpt_status()
        assert dialog._chatgpt_status_lbl.text() == "Not logged in"

        dialog._cgpt_login_btn.click()
        app.processEvents()
        assert starts == ["browser"]
        assert dialog._auth_poll_timer.isActive()
        dialog._auth_poll_tick()
        assert not dialog._auth_poll_timer.isActive()
        assert "acct-123" in dialog._chatgpt_status_lbl.text()
        assert "#80c080" in dialog._chatgpt_status_lbl.styleSheet()

        with monkeypatch.context() as status_error:
            status_error.setattr(
                chatgpt_auth,
                "get_tokens",
                lambda: (_ for _ in ()).throw(RuntimeError("credential store locked")),
            )
            dialog._refresh_chatgpt_status()
            assert "credential store locked" in dialog._chatgpt_status_lbl.text()
            assert "#c04040" in dialog._chatgpt_status_lbl.styleSheet()

        dialog._cgpt_logout_btn.click()
        app.processEvents()
        assert clears == ["chatgpt"]
        assert store["tokens"] is None
        assert dialog._chatgpt_status_lbl.text() == "Not logged in"
    finally:
        _close_settings(dialog, app)


@pytest.mark.parametrize(
    ("client_id", "scopes", "expected_client_id"),
    (("", "", "bundled-client"), ("custom-client", "repo read:user", "custom-client")),
    ids=("bundled-default", "custom-override"),
)
def test_github_settings_device_status_logout_and_override_real_button_workflow(
    monkeypatch: pytest.MonkeyPatch,
    client_id: str,
    scopes: str,
    expected_client_id: str,
) -> None:
    """Both client-ID modes flow from visible fields through device login and logout."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from core.auth import github as github_auth

    app = QApplication.instance() or QApplication(sys.argv)
    store: dict[str, object] = {"tokens": None}
    starts: list[tuple[str, str]] = []
    opened: list[str] = []
    clears: list[str] = []
    monkeypatch.setattr(config, "GITHUB_DEFAULT_CLIENT_ID", "bundled-client", raising=False)
    monkeypatch.setattr(github_auth, "has_configured_client_id", lambda: True)
    monkeypatch.setattr(github_auth, "get_tokens", lambda: store["tokens"])
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    def start_device_login(on_code, on_success, _on_error) -> None:
        starts.append((config.GITHUB_CLIENT_ID, config.GITHUB_OAUTH_SCOPES))
        on_code("https://github.com/login/device", "ABCD-1234")
        store["tokens"] = {
            "user": {"login": "octo-user"},
            "scope": config.GITHUB_OAUTH_SCOPES,
        }
        on_success(store["tokens"])

    monkeypatch.setattr(github_auth, "start_device_login", start_device_login)
    monkeypatch.setattr(
        github_auth,
        "clear_tokens",
        lambda: (clears.append("github"), store.__setitem__("tokens", None)),
    )
    dialog = _new_settings_dialog(monkeypatch)
    try:
        dialog.show()
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("Connections"))
        app.processEvents()
        dialog._fields["GITHUB_CLIENT_ID"].setText(client_id)
        dialog._fields["GITHUB_OAUTH_SCOPES"].setText(scopes)
        dialog._refresh_github_status()
        assert dialog._github_status_lbl.text() == "Not logged in"

        dialog._github_login_btn.click()
        app.processEvents()
        assert starts == [(expected_client_id, scopes)]
        dialog._github_auth_poll_tick()
        assert "ABCD-1234" in dialog._github_status_lbl.text()
        assert opened == ["https://github.com/login/device"]
        dialog._github_auth_poll_tick()
        assert not dialog._github_auth_poll_timer.isActive()
        assert "octo-user" in dialog._github_status_lbl.text()
        if scopes:
            assert scopes in dialog._github_status_lbl.text()

        with monkeypatch.context() as status_error:
            status_error.setattr(
                github_auth,
                "get_tokens",
                lambda: (_ for _ in ()).throw(RuntimeError("GitHub keychain denied")),
            )
            dialog._refresh_github_status()
            assert "GitHub keychain denied" in dialog._github_status_lbl.text()
            assert "#c04040" in dialog._github_status_lbl.styleSheet()

        dialog._github_logout_btn.click()
        app.processEvents()
        assert clears == ["github"]
        assert store["tokens"] is None
        assert dialog._github_status_lbl.text() == "Not logged in"
    finally:
        _close_settings(dialog, app)


def test_copilot_settings_connect_test_and_clear_real_button_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The newly visible Copilot controls operate on one real provider row."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core.auth import copilot_auth, copilot_client

    app = QApplication.instance() or QApplication(sys.argv)
    store: dict[str, str] = {"token": ""}
    saved: list[str] = []
    cleared: list[str] = []
    test_result: dict[str, object] = {"ok": True, "message": "Copilot connection works"}
    monkeypatch.setattr(
        copilot_auth,
        "save_token",
        lambda token: (saved.append(token), store.__setitem__("token", token)),
    )
    monkeypatch.setattr(
        copilot_auth,
        "clear_token",
        lambda: (cleared.append("copilot"), store.__setitem__("token", "")),
    )
    monkeypatch.setattr(
        copilot_auth,
        "token_status",
        lambda: (bool(store["token"]), "Stored in OS keychain." if store["token"] else "No Copilot token stored."),
    )
    monkeypatch.setattr(copilot_auth, "has_effective_token", lambda: bool(store["token"]))
    monkeypatch.setattr(
        copilot_client,
        "test_copilot_token",
        lambda: (bool(test_result["ok"]), str(test_result["message"])),
    )
    dialog = _new_settings_dialog(monkeypatch)
    try:
        dialog.show()
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("Connections"))
        app.processEvents()
        assert dialog._copilot_connect_btn.isVisible()
        assert dialog._copilot_test_btn.isVisible()
        assert dialog._copilot_clear_btn.isVisible()
        row = dialog._add_api_key_row(provider="copilot")
        row["key"].setText("github_pat_acceptance")

        dialog._copilot_connect_btn.click()
        app.processEvents()
        assert saved == ["github_pat_acceptance"]
        assert row["key"].text() == ""
        assert "Stored in OS keychain" in dialog._copilot_status_lbl.text()

        dialog._copilot_test_btn.click()
        assert dialog._copilot_status_lbl.text() == "Copilot connection works"
        assert "#80c080" in dialog._copilot_status_lbl.styleSheet()
        test_result.update(ok=False, message="Copilot rejected the token")
        dialog._copilot_test_btn.click()
        assert dialog._copilot_status_lbl.text() == "Copilot rejected the token"
        assert "#c04040" in dialog._copilot_status_lbl.styleSheet()

        dialog._copilot_clear_btn.click()
        app.processEvents()
        assert cleared == ["copilot"]
        assert store["token"] == ""
        assert "No Copilot token" in dialog._copilot_status_lbl.text()
        test_result.update(ok=False, message="No Copilot credential is configured")
        dialog._copilot_test_btn.click()
        assert dialog._copilot_status_lbl.text() == "No Copilot credential is configured"
        assert "#c04040" in dialog._copilot_status_lbl.styleSheet()
    finally:
        _close_settings(dialog, app)
