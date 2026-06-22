"""Tests for user-facing error recommendations."""

import pytest

from core.error_recommendations import format_error, recommendation_for


pytestmark = pytest.mark.workflow


def test_recommendation_for_known_error_classes():
    """Common failure classes produce direct next steps."""
    assert "Settings" in recommendation_for("missing API key")
    assert "microphone" in recommendation_for("STT microphone failure").lower()
    assert "hotkey" in recommendation_for("RegisterHotKey failed").lower()


def test_format_error_appends_recommendation_and_redacts_detail():
    """Formatted errors keep recommendations after messages and hide secrets."""
    text = format_error(
        "Provider failed because API key is invalid.",
        technical_detail="api_key = sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
    )

    assert "Provider failed" in text
    assert "Recommendation:" in text
    assert "Technical detail:" in text
    assert "[API_KEY]" in text
    assert "sk-proj-" not in text
