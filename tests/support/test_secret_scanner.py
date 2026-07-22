"""Tests for the dependency-free repository secret scanner."""
from __future__ import annotations

from scripts.scan_secrets import ALLOW_MARKER, scan_text


def test_scanner_detects_provider_keys_without_returning_secret_value():
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"  # secret-scan: allow
    findings = scan_text(f'OPENAI_API_KEY = "{secret}"', path="config.py")

    assert {item.rule for item in findings} >= {"openai-key", "credential-assignment"}
    assert all(secret not in repr(item) for item in findings)


def test_scanner_detects_generic_credentials():
    findings = scan_text('password = "correct-horse-battery-staple"', path="settings.py")  # secret-scan: allow

    assert [item.rule for item in findings] == ["credential-assignment"]


def test_scanner_allows_explicit_synthetic_fixture_marker():
    text = f'API_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"  # {ALLOW_MARKER}'  # secret-scan: allow

    assert scan_text(text, path="tests/test_fixture.py") == []
