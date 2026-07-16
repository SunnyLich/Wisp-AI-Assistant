"""Live integration tests for Wisp's Codex and Claude conversation harnesses.

These tests intentionally spend provider tokens and invoke the production
adapters without mocks. They are disabled during the ordinary test suite and
enabled by the trusted-branch CI job with WISP_RUN_REAL_HARNESS_TESTS=1.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.harness_clients.base import HarnessEvent, run_harness

_RUN_ENV = "WISP_RUN_REAL_HARNESS_TESTS"

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.real_harness,
    pytest.mark.skipif(
        os.getenv(_RUN_ENV) != "1",
        reason=f"set {_RUN_ENV}=1 to spend real Codex and Claude provider tokens",
    ),
]


@pytest.fixture(autouse=True)
def _safe_live_harness_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep live checks read-only, deterministic, and free of tool calls."""
    import config

    instructions = (
        "You are answering a minimal automated integration test. Do not use any tools, "
        "read files, or modify the workspace. Follow the requested reply format exactly."
    )
    monkeypatch.setattr(config, "WISP_CODEX_APPROVAL_MODE", "read_only", raising=False)
    monkeypatch.setattr(config, "WISP_CODEX_FAST_MODE", False, raising=False)
    monkeypatch.setattr(config, "WISP_CODEX_REASONING_EFFORT", "low", raising=False)
    monkeypatch.setattr(config, "WISP_CODEX_REASONING_SUMMARY", "none", raising=False)
    monkeypatch.setattr(config, "WISP_CODEX_SYSTEM_PROMPT", instructions, raising=False)
    monkeypatch.setattr(config, "WISP_CLAUDE_APPROVAL_MODE", "read_only", raising=False)
    monkeypatch.setattr(config, "WISP_CLAUDE_FAST_MODE", False, raising=False)
    monkeypatch.setattr(config, "WISP_CLAUDE_REASONING_EFFORT", "low", raising=False)
    monkeypatch.setattr(config, "WISP_CLAUDE_REASONING_SUMMARY", "none", raising=False)
    monkeypatch.setattr(config, "WISP_CLAUDE_SYSTEM_PROMPT", instructions, raising=False)


def _assert_real_resumable_conversation(provider: str, workspace: Path) -> None:
    marker = f"wisp-live-{provider}-saffron-7319"
    first_events: list[HarnessEvent] = []
    first = run_harness(
        provider,
        f"Remember the exact token {marker} for my next message. Reply with only READY.",
        cwd=workspace,
        on_event=first_events.append,
        approval_callback=lambda _request: False,
    )

    assert first.provider == provider
    assert first.session_id.strip()
    assert first.backend.strip()
    assert first.text.strip()
    assert any(event.kind == "reply" and event.text for event in first_events)

    second_events: list[HarnessEvent] = []
    second = run_harness(
        provider,
        "Reply with only the exact token I asked you to remember in my previous message.",
        session_id=first.session_id,
        cwd=workspace,
        on_event=second_events.append,
        approval_callback=lambda _request: False,
    )

    assert second.session_id == first.session_id
    assert marker in second.text.strip().lower()
    assert any(event.kind == "reply" and event.text for event in second_events)


def test_real_codex_harness_resumes_a_live_conversation(tmp_path: Path) -> None:
    """Call the real Codex app-server twice and verify thread continuity."""
    _assert_real_resumable_conversation("codex", tmp_path)


def test_real_claude_harness_resumes_a_live_conversation(tmp_path: Path) -> None:
    """Call the real Claude Agent SDK twice and verify session continuity."""
    _assert_real_resumable_conversation("claude", tmp_path)
