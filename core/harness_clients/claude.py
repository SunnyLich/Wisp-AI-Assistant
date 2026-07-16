"""Claude Agent SDK adapter."""
from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from core.harness_clients.base import (
    ApprovalCallback,
    EventCallback,
    HarnessResult,
    approval_allowed,
    emit,
    normalized_cwd,
)


class ClaudeHarnessError(RuntimeError):
    """Raised when the Claude Agent SDK cannot complete a turn."""


def _permission_mode(approval_mode: str) -> str:
    return {
        "auto_edits": "acceptEdits",
        "full_access": "bypassPermissions",
        "read_only": "plan",
    }.get(str(approval_mode or "ask"), "default")


def _claude_executable(*, required: bool = True) -> str:
    """Return the same Claude Code CLI executable used by the Agent SDK."""
    configured = os.getenv("WISP_CLAUDE_CLI", "").strip()
    if configured:
        return configured
    try:
        import claude_agent_sdk

        cli_name = "claude.exe" if platform.system() == "Windows" else "claude"
        bundled = Path(claude_agent_sdk.__file__).resolve().parent / "_bundled" / cli_name
        if bundled.is_file():
            return str(bundled)
    except (AttributeError, ImportError, OSError, TypeError):
        pass
    executable = shutil.which("claude") or ""
    if executable:
        return executable
    if not required:
        return ""
    raise ClaudeHarnessError(
        "Claude harness is unavailable. Install claude-agent-sdk or set WISP_CLAUDE_CLI to its executable."
    )


def _tool_request(tool_name: str, input_data: dict[str, Any]) -> dict[str, Any]:
    path = str(input_data.get("file_path") or input_data.get("path") or "")
    command = str(input_data.get("command") or "")
    return {
        "action": f"Claude tool: {tool_name}",
        "path": path,
        "details": dict(input_data),
        "diff": command,
    }


async def _run_async(
    prompt: str,
    *,
    session_id: str,
    cwd: Path,
    on_event: EventCallback | None,
    approval_callback: ApprovalCallback | None,
) -> HarnessResult:
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny
    except ImportError as exc:
        raise ClaudeHarnessError(
            "Claude harness is unavailable. Install the claude-agent-sdk package and configure ANTHROPIC_API_KEY."
        ) from exc

    try:
        import config

        model = str(getattr(config, "WISP_CLAUDE_MODEL", "") or "").strip()
        fast_mode = bool(getattr(config, "WISP_CLAUDE_FAST_MODE", False))
        approval_mode = str(getattr(config, "WISP_CLAUDE_APPROVAL_MODE", "ask") or "ask")
        effort = str(getattr(config, "WISP_CLAUDE_REASONING_EFFORT", "high") or "").strip()
        reasoning_summary = str(
            getattr(config, "WISP_CLAUDE_REASONING_SUMMARY", "summarized") or "summarized"
        )
        system_prompt = str(getattr(config, "WISP_CLAUDE_SYSTEM_PROMPT", "") or "")
    except (ImportError, AttributeError):
        model = ""
        fast_mode = False
        approval_mode = "ask"
        effort = "high"
        reasoning_summary = "summarized"
        system_prompt = ""

    async def can_use_tool(tool_name: str, input_data: dict[str, Any], _context: Any) -> Any:
        if approval_callback is None:
            return PermissionResultDeny(message="Wisp approval UI is unavailable", interrupt=False)
        allowed = approval_allowed(approval_callback(_tool_request(tool_name, input_data)))
        if allowed:
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(message="Declined in Wisp", interrupt=False)

    options_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "include_partial_messages": True,
        "include_hook_events": True,
        "permission_mode": _permission_mode(approval_mode),
        "allowed_tools": ["Read", "Glob", "Grep"],
        "can_use_tool": can_use_tool,
        "settings": json.dumps({"fastMode": fast_mode}),
    }
    cli_path = _claude_executable(required=False)
    if cli_path:
        options_kwargs["cli_path"] = cli_path
    if model:
        options_kwargs["model"] = model
    if system_prompt:
        options_kwargs["system_prompt"] = system_prompt
    if effort:
        options_kwargs["effort"] = effort
    options_kwargs["thinking"] = (
        {"type": "disabled"}
        if reasoning_summary == "none"
        else {"type": "adaptive", "display": "summarized"}
    )
    if session_id:
        options_kwargs["resume"] = session_id
    options = ClaudeAgentOptions(**options_kwargs)
    try:
        backend = f"claude-agent-sdk/{version('claude-agent-sdk')}"
    except PackageNotFoundError:
        backend = "claude-agent-sdk"

    async def prompt_stream():
        """Use SDK streaming input so its permission control channel is active."""
        yield {
            "type": "user",
            "message": {"role": "user", "content": str(prompt)},
            "parent_tool_use_id": None,
        }

    reply_parts: list[str] = []
    final_text = ""
    active_session = session_id
    streamed = False
    streamed_thinking = False
    announced_tools: set[str] = set()
    try:
        async for message in query(prompt=prompt_stream(), options=options):
            class_name = type(message).__name__
            message_session = str(getattr(message, "session_id", "") or "")
            if message_session:
                active_session = message_session
            if class_name == "SystemMessage":
                data = getattr(message, "data", {})
                if isinstance(data, dict) and getattr(message, "subtype", "") == "init":
                    active_session = str(data.get("session_id") or active_session)
                continue
            if class_name == "StreamEvent":
                event = getattr(message, "event", {})
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "content_block_start":
                    block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
                    if block.get("type") == "tool_use":
                        tool_id = str(block.get("id") or "")
                        if tool_id:
                            announced_tools.add(tool_id)
                        emit(on_event, "progress", f"Claude started {block.get('name') or 'tool'}")
                    continue
                if event.get("type") != "content_block_delta":
                    continue
                delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                delta_type = str(delta.get("type") or "")
                text = str(delta.get("text") or delta.get("thinking") or "")
                if delta_type == "text_delta" and text:
                    streamed = True
                    reply_parts.append(text)
                    emit(on_event, "reply", text)
                elif delta_type == "thinking_delta" and text:
                    streamed_thinking = True
                    emit(on_event, "thought", text)
                continue
            if class_name == "AssistantMessage":
                for block in getattr(message, "content", []) or []:
                    block_name = type(block).__name__
                    if block_name == "ToolUseBlock":
                        tool_id = str(getattr(block, "id", "") or "")
                        if tool_id not in announced_tools:
                            emit(on_event, "progress", f"Claude started {getattr(block, 'name', 'tool')}")
                        details = getattr(block, "input", {})
                        if isinstance(details, dict):
                            target = details.get("file_path") or details.get("path") or details.get("command")
                            if target:
                                emit(on_event, "progress", f"Claude action: {target}")
                    elif block_name == "ThinkingBlock" and not streamed_thinking:
                        emit(on_event, "thought", getattr(block, "thinking", ""))
                    elif block_name == "TextBlock" and not streamed:
                        text = str(getattr(block, "text", "") or "")
                        if text:
                            reply_parts.append(text)
                            emit(on_event, "reply", text)
                continue
            if class_name in {"TaskStartedMessage", "TaskProgressMessage", "TaskNotificationMessage"}:
                emit(on_event, "progress", getattr(message, "description", "") or getattr(message, "summary", ""))
                continue
            if class_name == "HookEventMessage":
                emit(on_event, "progress", f"Claude event: {getattr(message, 'hook_event_name', 'hook')}")
                continue
            if class_name == "ResultMessage":
                if bool(getattr(message, "is_error", False)):
                    raise ClaudeHarnessError(str(getattr(message, "result", "") or "Claude agent turn failed"))
                final_text = str(getattr(message, "result", "") or "")
                active_session = str(getattr(message, "session_id", "") or active_session)
    except ClaudeHarnessError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize third-party SDK errors for the UI
        raise ClaudeHarnessError(f"Claude Agent SDK failed: {exc}") from exc

    text = final_text or "".join(reply_parts)
    if not streamed and final_text and final_text != "".join(reply_parts):
        emit(on_event, "reply", final_text)
    if not active_session:
        raise ClaudeHarnessError("Claude Agent SDK did not return a session id")
    return HarnessResult(
        provider="claude",
        text=text,
        session_id=active_session,
        cwd=str(cwd),
        backend=backend,
    )


def run_claude(
    prompt: str,
    *,
    session_id: str = "",
    cwd: str | Path | None = None,
    on_event: EventCallback | None = None,
    approval_callback: ApprovalCallback | None = None,
) -> HarnessResult:
    """Run one prompt through the Claude Agent SDK."""
    workdir = normalized_cwd(cwd)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run_async(
            prompt,
            session_id=session_id,
            cwd=workdir,
            on_event=on_event,
            approval_callback=approval_callback,
        ))
    raise ClaudeHarnessError("Claude harness cannot start inside an existing asyncio event loop")
