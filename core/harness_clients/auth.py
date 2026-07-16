"""Authentication helpers for the local Codex and Claude harness CLIs."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

from core.harness_clients.claude import _claude_executable
from core.harness_clients.codex import (
    _codex_environment,
    _codex_environment_overrides,
    _codex_executable,
)


@dataclass(frozen=True)
class HarnessLoginStatus:
    """One harness CLI's availability and account sign-in state."""

    provider: str
    available: bool
    logged_in: bool | None
    message: str
    executable: str = ""


def _provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    if value not in {"codex", "claude"}:
        raise ValueError(f"Unsupported harness provider: {provider}")
    return value


def harness_executable(provider: str) -> str:
    """Return the executable used by the selected harness runtime."""
    return _codex_executable() if _provider(provider) == "codex" else _claude_executable()


def harness_login_command(provider: str) -> list[str]:
    """Return the interactive account sign-in command for a harness."""
    provider = _provider(provider)
    executable = harness_executable(provider)
    if provider == "codex":
        return [executable, "login"]
    return [executable, "auth", "login"]


def harness_logout_command(provider: str) -> list[str]:
    """Return the non-interactive account sign-out command for a harness."""
    provider = _provider(provider)
    executable = harness_executable(provider)
    if provider == "codex":
        return [executable, "logout"]
    return [executable, "auth", "logout"]


def harness_login_environment(provider: str) -> dict[str, str]:
    """Return environment overrides for an interactive harness login."""
    return _codex_environment_overrides() if _provider(provider) == "codex" else {}


def _status_command(provider: str, executable: str) -> list[str]:
    if provider == "codex":
        return [executable, "login", "status"]
    return [executable, "auth", "status", "--json"]


def _run(
    command: list[str],
    *,
    timeout: float,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=creationflags,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return " ".join((result.stdout or result.stderr or "").split()).strip()


def harness_login_status(provider: str, *, timeout: float = 5.0) -> HarnessLoginStatus:
    """Read a harness login state without opening an interactive terminal."""
    provider = _provider(provider)
    try:
        executable = harness_executable(provider)
    except Exception as exc:  # noqa: BLE001 - normalized for the Settings UI
        return HarnessLoginStatus(provider, False, None, str(exc))

    try:
        command = _status_command(provider, executable)
        if provider == "codex":
            result = _run(command, timeout=timeout, environment=_codex_environment())
        else:
            result = _run(command, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return HarnessLoginStatus(
            provider,
            True,
            None,
            f"Could not read login status: {exc}",
            executable,
        )

    output = _output(result)
    if provider == "codex":
        logged_out = "not logged" in output.casefold()
        if result.returncode == 0 and not logged_out:
            return HarnessLoginStatus(provider, True, True, output or "Logged in", executable)
        if logged_out:
            return HarnessLoginStatus(provider, True, False, "Not logged in", executable)
        return HarnessLoginStatus(
            provider,
            True,
            None,
            output or f"Login status failed with exit code {result.returncode}",
            executable,
        )

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict) and isinstance(payload.get("loggedIn"), bool):
        logged_in = bool(payload["loggedIn"])
        if not logged_in:
            return HarnessLoginStatus(provider, True, False, "Not logged in", executable)
        method = str(payload.get("authMethod") or "").strip()
        suffix = f" using {method}" if method and method != "none" else ""
        return HarnessLoginStatus(provider, True, True, f"Logged in{suffix}", executable)
    return HarnessLoginStatus(
        provider,
        True,
        None,
        output or f"Login status failed with exit code {result.returncode}",
        executable,
    )


def logout_harness(provider: str, *, timeout: float = 10.0) -> HarnessLoginStatus:
    """Sign out of a harness account, then return its updated status."""
    provider = _provider(provider)
    try:
        command = harness_logout_command(provider)
        if provider == "codex":
            result = _run(command, timeout=timeout, environment=_codex_environment())
        else:
            result = _run(command, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - normalized for the Settings UI
        return HarnessLoginStatus(provider, False, None, f"Sign out failed: {exc}")
    if result.returncode != 0:
        return HarnessLoginStatus(
            provider,
            True,
            None,
            _output(result) or f"Sign out failed with exit code {result.returncode}",
            command[0],
        )
    return harness_login_status(provider, timeout=timeout)
