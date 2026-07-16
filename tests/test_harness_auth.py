"""Tests for Codex and Claude CLI account authentication helpers."""
from __future__ import annotations

import json
import subprocess

import pytest

from core.harness_clients import auth


def _completed(command: list[str], returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_codex_login_status_uses_cli_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "harness_executable", lambda provider: "codex-bin")
    isolated_environment = {"CODEX_HOME": "wisp-codex"}
    monkeypatch.setattr(auth, "_codex_environment", lambda: isolated_environment)
    calls = []

    def run(command, *, timeout, environment):
        calls.append((command, timeout, environment))
        return _completed(command, 0, "Logged in using ChatGPT\n")

    monkeypatch.setattr(auth, "_run", run)

    status = auth.harness_login_status("codex", timeout=3)

    assert calls == [(["codex-bin", "login", "status"], 3, isolated_environment)]
    assert status.available is True
    assert status.logged_in is True
    assert status.message == "Logged in using ChatGPT"


def test_codex_login_status_recognizes_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "harness_executable", lambda provider: "codex-bin")
    monkeypatch.setattr(auth, "_codex_environment", lambda: {"CODEX_HOME": "wisp-codex"})
    monkeypatch.setattr(
        auth,
        "_run",
        lambda command, *, timeout, environment: _completed(command, 1, stderr="Not logged in"),
    )

    status = auth.harness_login_status("codex")

    assert status.available is True
    assert status.logged_in is False
    assert status.message == "Not logged in"


@pytest.mark.parametrize(
    ("logged_in", "auth_method", "expected_message"),
    [
        (False, "none", "Not logged in"),
        (True, "claude.ai", "Logged in using claude.ai"),
    ],
)
def test_claude_login_status_parses_bundled_cli_json(
    monkeypatch: pytest.MonkeyPatch,
    logged_in: bool,
    auth_method: str,
    expected_message: str,
) -> None:
    monkeypatch.setattr(auth, "harness_executable", lambda provider: "claude-bin")
    payload = json.dumps({"loggedIn": logged_in, "authMethod": auth_method})
    monkeypatch.setattr(
        auth,
        "_run",
        lambda command, *, timeout: _completed(command, 0, payload),
    )

    status = auth.harness_login_status("claude")

    assert status.logged_in is logged_in
    assert status.message == expected_message


def test_harness_login_commands_match_each_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "harness_executable", lambda provider: f"{provider}-bin")

    assert auth.harness_login_command("codex") == ["codex-bin", "login"]
    assert auth.harness_logout_command("codex") == ["codex-bin", "logout"]
    assert auth.harness_login_command("claude") == ["claude-bin", "auth", "login"]
    assert auth.harness_logout_command("claude") == ["claude-bin", "auth", "logout"]


def test_codex_login_environment_uses_wisp_state_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"CODEX_HOME": "wisp-codex", "CODEX_SQLITE_HOME": "wisp-codex"}
    monkeypatch.setattr(auth, "_codex_environment_overrides", lambda: expected)

    assert auth.harness_login_environment("codex") == expected
    assert auth.harness_login_environment("claude") == {}
