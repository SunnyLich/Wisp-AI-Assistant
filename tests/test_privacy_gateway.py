from __future__ import annotations

import json

import pytest

from core.privacy_gateway import (
    PrivacyLeakDetected,
    PrivacyReviewCanceled,
    PrivacySession,
)
from core.privacy_redaction import SensitiveEntity, detect_sensitive_entities, redact_text


def _categories(text: str) -> set[str]:
    return {entity.category for entity in detect_sensitive_entities(text)}


def test_builtin_detector_covers_structured_private_data_and_tokens():
    text = (
        "Email alex@example.com, SSN 123-45-6789, card 4111 1111 1111 1111, "
        "IBAN GB82 WEST 1234 5698 7654 32, AWS AKIAIOSFODNN7EXAMPLE, "  # secret-scan: allow
        "GitHub ghp_abcdefghijklmnopqrstuvwxyz1234567890AB, passport: X12345678, "  # secret-scan: allow
        "and driver's license number: D1234567."
    )

    assert {
        "email", "ssn", "card_number", "iban", "api_key", "passport", "drivers_license"
    } <= _categories(text)
    redacted = redact_text(text)
    for secret in (
        "alex@example.com",
        "123-45-6789",
        "4111 1111 1111 1111",
        "GB82 WEST 1234 5698 7654 32",
        "AKIAIOSFODNN7EXAMPLE",  # secret-scan: allow
    ):
        assert secret not in redacted


def test_builtin_detector_rejects_invalid_card_and_iban_candidates():
    assert "card_number" not in _categories("Reference 4111 1111 1111 1112")
    assert "iban" not in _categories("Reference GB82 WEST 1234 5698 7654 33")


def test_custom_patterns_use_their_stable_sanitized_label(monkeypatch):
    import config

    monkeypatch.setattr(
        config,
        "PRIVACY_CUSTOM_PATTERNS",
        json.dumps([{"name": "Customer ID", "pattern": r"CUST-\d{6}", "label": "customer"}]),
        raising=False,
    )
    session = PrivacySession("custom")

    scrubbed, report = session.scrub_fields({"prompt": "Open CUST-123456"})

    assert scrubbed["prompt"] == "Open [CUSTOMER_1]"
    assert report["categories"] == {"custom": 1}
    assert session.restore("Use [CUSTOMER_1]") == "Use CUST-123456"


def test_session_uses_stable_placeholders_and_restores_response():
    session = PrivacySession("conversation-1")
    first, report = session.scrub_fields({"prompt": "Email alex@example.com"})
    second, _ = session.scrub_fields({"history": "Again: alex@example.com"})

    assert first["prompt"] == "Email [EMAIL_1]"
    assert second["history"] == "Again: [EMAIL_1]"
    assert report["categories"] == {"email": 1}
    assert session.restore("Contact [EMAIL_1].") == "Contact alex@example.com."


def test_review_receives_only_scrubbed_request_and_can_cancel():
    payloads: list[dict] = []

    def cancel(payload: dict) -> bool:
        payloads.append(payload)
        return False

    with pytest.raises(PrivacyReviewCanceled):
        PrivacySession("review").scrub_fields(
            {"prompt": "Send alex@example.com"},
            review=cancel,
        )

    assert payloads
    assert "alex@example.com" not in payloads[0]["scrubbed_preview"]
    assert "[EMAIL_1]" in payloads[0]["scrubbed_preview"]


def test_review_can_send_the_original_full_message_for_one_request():
    payloads: list[dict] = []

    def send_full(payload: dict) -> str:
        payloads.append(payload)
        return "full"

    original = "Send alex@example.com without redaction"
    result, report = PrivacySession("full-send").scrub_fields(
        {"prompt": original},
        review=send_full,
    )

    assert result["prompt"] == original
    assert report["reviewed"] is True
    assert report["decision"] == "full"
    assert report["redacted"] is False
    assert "alex@example.com" not in payloads[0]["scrubbed_preview"]


def test_review_receives_the_complete_untruncated_request():
    payloads: list[dict] = []
    original = f"{'x' * 20_000} alex@example.com"

    PrivacySession("full-preview").scrub_fields(
        {"prompt": original},
        review=lambda payload: payloads.append(payload) or "redacted",
    )

    assert len(payloads) == 1
    preview = payloads[0]["scrubbed_preview"]
    assert len(preview) > 20_000
    assert preview.endswith("[EMAIL_1]")
    assert "alex@example.com" not in preview


def test_advanced_mode_merges_detectors_once_with_model_precedence(monkeypatch):
    payloads: list[dict] = []

    def model_detector(text: str, *, source: str = ""):
        private_value = "alex@example.com"
        if private_value not in text:
            return []
        start = text.index(private_value)
        return [
            SensitiveEntity(
                "secret",
                start,
                start + len(private_value),
                private_value,
                "[SECRET]",
                source,
            )
        ]

    monkeypatch.setattr("core.privacy_model.detect_with_model", model_detector)

    scrubbed, report = PrivacySession("advanced-only").scrub_fields(
        {"prompt": "Email alex@example.com, SSN 123-45-6789"},
        ai_enabled=True,
        review=lambda payload: payloads.append(payload) or "redacted",
    )

    assert scrubbed["prompt"] == "Email [SECRET_1], SSN [SSN_1]"
    assert report["privacy_mode"] == "advanced"
    assert report["categories"] == {"secret": 1, "ssn": 1}
    assert report["count"] == 2
    assert len(payloads) == 1
    assert payloads[0]["count"] == 2
    assert [item["category"] for item in payloads[0]["items"]] == ["secret", "ssn"]


def test_post_scrub_detection_blocks_the_send(monkeypatch):
    calls = 0

    def leaking_detector(text: str, *, source: str = "", include_custom: bool = True):
        nonlocal calls
        calls += 1
        if calls == 1:
            return []
        return [SensitiveEntity("secret", 0, 4, text[:4], "[SECRET]", source)]

    monkeypatch.setattr("core.privacy_gateway.detect_sensitive_entities", leaking_detector)
    with pytest.raises(PrivacyLeakDetected):
        PrivacySession("leak").scrub_fields({"prompt": "safe-looking value"})


def test_ai_detector_failure_is_fail_closed(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("core.privacy_model.detect_with_model", unavailable)
    with pytest.raises(RuntimeError, match="model unavailable"):
        PrivacySession("ai").scrub_fields({"prompt": "hello"}, ai_enabled=True)


def test_privacy_failure_matrix_is_fail_closed(monkeypatch):
    """Exercise all shared privacy failure causes without allowing a cloud send."""
    for failure in (
        FileNotFoundError("selected privacy filter unavailable"),
        ImportError("selected privacy runtime unavailable"),
        OSError("privacy model assets missing"),
        TimeoutError("private-information detection timed out"),
    ):
        with monkeypatch.context() as scoped:
            scoped.setattr(
                "core.privacy_model.detect_with_model",
                lambda *_args, failure=failure, **_kwargs: (_ for _ in ()).throw(failure),
            )
            with pytest.raises(type(failure), match=str(failure)):
                PrivacySession(f"failure-{type(failure).__name__}").scrub_fields(
                    {"prompt": "send this"},
                    ai_enabled=True,
                )

    calls = 0

    def misclassifying_detector(text: str, *, source: str = "", include_custom: bool = True):
        nonlocal calls
        calls += 1
        if calls == 1:
            return []
        return [SensitiveEntity("secret", 0, len(text), text, "[SECRET]", source)]

    with monkeypatch.context() as scoped:
        scoped.setattr("core.privacy_gateway.detect_sensitive_entities", misclassifying_detector)
        with pytest.raises(PrivacyLeakDetected):
            PrivacySession("misclassified").scrub_fields({"prompt": "unsafe"})

    with pytest.raises(PrivacyReviewCanceled):
        PrivacySession("cancelled").scrub_fields(
            {"prompt": "alex@example.com"},
            review=lambda _payload: "cancel",
        )

    import config

    monkeypatch.setattr(
        config,
        "PRIVACY_CUSTOM_PATTERNS",
        '[{"name":"broken","pattern":"["}]',
        raising=False,
    )
    scrubbed, report = PrivacySession("invalid-config").scrub_fields(
        {"prompt": "alex@example.com"}
    )
    assert scrubbed["prompt"] == "[EMAIL_1]"
    assert report["categories"] == {"email": 1}


def test_cloud_bound_tool_results_use_the_same_session(monkeypatch):
    from core.llm_clients import client

    reports: list[dict] = []
    session = PrivacySession("tool-result")
    client.set_live_privacy_context(session, report_callback=reports.append)
    try:
        scrubbed = client._scrub_live_tool_result("read_file", "Owner: alex@example.com")
    finally:
        client.set_live_privacy_context(None)

    assert scrubbed == "Owner: [EMAIL_1]"
    assert reports[0]["sources"] == {"tool:read_file": 1}
    assert session.restore(scrubbed) == "Owner: alex@example.com"
