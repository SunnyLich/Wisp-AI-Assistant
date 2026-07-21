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
        technical_detail="api_key = sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",  # secret-scan: allow
    )

    assert "Provider failed" in text
    assert "Recommendation:" in text
    assert "Technical detail:" in text
    assert "[API_KEY]" in text
    assert "sk-proj-" not in text


def test_request_failure_matrix_has_actionable_recommendations():
    """Every request-lifecycle failure in the inventory has a next action."""
    cases = (
        ("Required user input is empty.", "enter a prompt"),
        ("Required context is empty.", "context source"),
        ("The configured route fails because the API key is missing.", "Settings"),
        ("The network request fails.", "network/provider"),
        ("The request is cancelled.", "start the request again"),
        ("The result cannot be rendered.", "reopen the conversation"),
        ("The result cannot be pasted into the target application.", "target field"),
    )
    for message, expected in cases:
        rendered = format_error(message)
        assert "Recommendation:" in rendered
        assert expected.lower() in rendered.lower()


def test_worker_recovery_failure_matrix_always_preserves_diagnostic_action():
    """Unresponsive workers and sparse or unknown errors retain a recovery path."""
    worker = format_error(
        "The worker is unresponsive.",
        technical_detail="brain heartbeat exceeded 12 seconds in request req-42",
    )
    assert "restart Wisp" in worker
    assert "Runtime Status" in worker
    assert "brain heartbeat exceeded 12 seconds" in worker

    sparse = format_error("Worker failed.")
    assert "Recommendation:" in sparse
    assert "crash report" in sparse

    unknown = recommendation_for("unclassified runtime fault zeta-17")
    assert "Runtime Status" in unknown
    assert "recent logs" in unknown
