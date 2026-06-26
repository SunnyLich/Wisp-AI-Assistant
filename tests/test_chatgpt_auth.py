"""Tests for ChatGPT OAuth callback pages."""

from core.auth import chatgpt as chatgpt_auth


def test_oauth_success_page_uses_wisp_copy(monkeypatch):
    """Verify the browser callback success page uses the branded Wisp message."""
    monkeypatch.setattr(chatgpt_auth, "_app_icon_data_uri", lambda: "data:image/x-icon;base64,icon")

    html = chatgpt_auth._html_success()

    assert "Wisp - Authorization Complete" in html
    assert 'alt="Wisp"' in html
    assert "Authorization completed successfully." in html
    assert "You can close this window and return to Wisp." in html
    assert "Authorization Successful" not in html
    assert "return to the app" not in html


def test_oauth_error_page_escapes_error_message(monkeypatch):
    """Verify OAuth errors cannot inject markup into the callback page."""
    monkeypatch.setattr(chatgpt_auth, "_app_icon_data_uri", lambda: "")

    html = chatgpt_auth._html_error('<script>alert("x")</script>')

    assert "Authorization failed." in html
    assert "Return to Wisp and try signing in again." in html
    assert "&lt;script&gt;" in html
    assert '<script>alert("x")</script>' not in html
