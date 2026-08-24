"""Live integration tests for OpenWand's Codex and Claude conversation harnesses.

These tests intentionally spend provider tokens and invoke the production
adapters without mocks. They are disabled during the ordinary test suite and
run only when OPENWAND_RUN_REAL_HARNESS_TESTS=1 is set explicitly. No CI job sets
that variable; run this file manually (e.g. before a release).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.harness_clients.base import HarnessEvent, run_harness

_RUN_ENV = "OPENWAND_RUN_REAL_HARNESS_TESTS"

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
    monkeypatch.setattr(config, "OPENWAND_CODEX_APPROVAL_MODE", "read_only", raising=False)
    monkeypatch.setattr(config, "OPENWAND_CODEX_FAST_MODE", False, raising=False)
    monkeypatch.setattr(config, "OPENWAND_CODEX_REASONING_EFFORT", "low", raising=False)
    monkeypatch.setattr(config, "OPENWAND_CODEX_REASONING_SUMMARY", "none", raising=False)
    monkeypatch.setattr(config, "OPENWAND_CODEX_SYSTEM_PROMPT", instructions, raising=False)
    monkeypatch.setattr(config, "OPENWAND_CLAUDE_APPROVAL_MODE", "read_only", raising=False)
    monkeypatch.setattr(config, "OPENWAND_CLAUDE_FAST_MODE", False, raising=False)
    monkeypatch.setattr(config, "OPENWAND_CLAUDE_REASONING_EFFORT", "low", raising=False)
    monkeypatch.setattr(config, "OPENWAND_CLAUDE_REASONING_SUMMARY", "none", raising=False)
    monkeypatch.setattr(config, "OPENWAND_CLAUDE_SYSTEM_PROMPT", instructions, raising=False)


def _assert_real_resumable_conversation(provider: str, workspace: Path) -> None:
    marker = f"openwand-live-{provider}-saffron-7319"
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


def test_real_codex_reports_native_skills_and_mcp_inventory(tmp_path: Path) -> None:
    """Exercise app-server discovery through the same profile used by Codex CLI."""
    events: list[HarnessEvent] = []
    run_harness(
        "codex",
        "Reply with only READY.",
        cwd=tmp_path,
        on_event=events.append,
        approval_callback=lambda _request: False,
    )

    snapshots = [
        event.attachment
        for event in events
        if event.kind == "activity"
        and isinstance(event.attachment, dict)
        and event.attachment.get("type") == "capabilities"
    ]
    assert snapshots
    assert isinstance(snapshots[-1].get("skills"), list)
    assert isinstance(snapshots[-1].get("mcp_servers"), list)


@pytest.mark.skipif(
    os.getenv("OPENWAND_RUN_REAL_SUBAGENT_TESTS") != "1",
    reason="set OPENWAND_RUN_REAL_SUBAGENT_TESTS=1 for the live multi-agent check",
)
def test_real_codex_streams_subagent_assignment_and_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the installed Codex can delegate and OpenWand exposes the child work."""
    import config

    monkeypatch.setattr(
        config,
        "OPENWAND_CODEX_SYSTEM_PROMPT",
        "Spawn exactly one subagent to answer the user's tiny question, wait for it, then reply READY.",
        raising=False,
    )
    events: list[HarnessEvent] = []
    run_harness(
        "codex",
        "Ask one subagent to determine whether 2 + 2 equals 4.",
        cwd=tmp_path,
        on_event=events.append,
        approval_callback=lambda _request: False,
    )

    subagent_events = [
        event for event in events
        if event.kind == "activity"
        and isinstance(event.attachment, dict)
        and event.attachment.get("type") == "subagent"
    ]
    assert subagent_events
    assert any(event.attachment.get("prompt") for event in subagent_events)


def test_real_claude_harness_resumes_a_live_conversation(tmp_path: Path) -> None:
    """Call the real Claude Agent SDK twice and verify session continuity."""
    _assert_real_resumable_conversation("claude", tmp_path)
