"""Provider-neutral entry point for live agent harnesses."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

HarnessEventKind = Literal["reply", "thought", "progress", "status", "image", "activity"]
EventCallback = Callable[["HarnessEvent"], None]
ApprovalCallback = Callable[[dict[str, Any]], bool | dict[str, Any]]


@dataclass(frozen=True)
class HarnessEvent:
    """One normalized live event emitted by an external harness."""

    kind: HarnessEventKind
    text: str
    attachment: dict[str, Any] | None = None


@dataclass(frozen=True)
class HarnessResult:
    """Completed harness turn and its resumable provider session."""

    provider: str
    text: str
    session_id: str
    cwd: str
    backend: str = ""
    attachments: tuple[dict[str, Any], ...] = ()
    workspace_changes: dict[str, Any] | None = None


def normalized_cwd(value: str | Path | None) -> Path:
    """Return an existing working directory without asking the user to choose one."""
    candidate = Path(value).expanduser() if value else Path.cwd()
    if candidate.is_file():
        candidate = candidate.parent
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = Path.cwd().resolve()
    return resolved if resolved.is_dir() else Path.cwd().resolve()


def emit(callback: EventCallback | None, kind: HarnessEventKind, text: object) -> None:
    """Emit a non-empty normalized event."""
    value = str(text or "")
    if value and callback is not None:
        callback(HarnessEvent(kind=kind, text=value))


def approval_allowed(result: bool | dict[str, Any]) -> bool:
    """Normalize OpenWand approval callback results."""
    if isinstance(result, dict):
        return bool(result.get("approved"))
    return bool(result)


def run_harness(
    provider: str,
    prompt: str,
    *,
    model: str | None = None,
    session_id: str = "",
    cwd: str | Path | None = None,
    on_event: EventCallback | None = None,
    approval_callback: ApprovalCallback | None = None,
    images: Sequence[str] = (),
) -> HarnessResult:
    """Run one turn through the selected local agent harness.

    ``images`` are local files attached to the turn's input; only the codex
    harness ingests them today, and callers own the files' cleanup.
    """
    selected = str(provider or "").strip().lower()
    if selected == "codex":
        from core.harness_clients.codex import run_codex

        return run_codex(
            prompt,
            model=model,
            session_id=session_id,
            cwd=cwd,
            on_event=on_event,
            approval_callback=approval_callback,
            images=images,
        )
    if selected == "claude":
        from core.harness_clients.claude import run_claude

        return run_claude(
            prompt,
            model=model,
            session_id=session_id,
            cwd=cwd,
            on_event=on_event,
            approval_callback=approval_callback,
        )
    raise ValueError(f"unsupported harness provider: {provider!r}")
