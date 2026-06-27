"""Pure chat/context policy helpers for supervisor flows."""
from __future__ import annotations

from typing import Any

from runtime.supervisor import tool_modes


def file_context_text(items: list | None) -> str:
    """Build hidden follow-up context for recent local-file tools."""
    normalized: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        item = {
            "tool": str(raw.get("tool") or ""),
            "path": str(raw.get("path") or ""),
            "relative_path": str(raw.get("relative_path") or ""),
            "ok": bool(raw.get("ok")),
            "message": str(raw.get("message") or ""),
        }
        if item["tool"] and item["path"] and item not in normalized:
            normalized.append(item)
    if not normalized:
        return ""
    lines = [
        "[Conversation File Context]",
        "Recent local file tool context for this conversation. Use these exact paths when the user refers to a prior file.",
    ]
    for item in normalized[-8:]:
        status = "ok" if item.get("ok") else "failed"
        label = f"{item.get('tool')} ({status}): {item.get('path')}"
        rel = item.get("relative_path") or ""
        if rel and rel != item.get("path"):
            label += f" [relative: {rel}]"
        message = str(item.get("message") or "").strip()
        if message:
            label += f" - {message}"
        lines.append(f"- {label}")
    return "\n".join(lines)


def normalized_tool_context(raw: Any) -> dict[str, Any]:
    """Normalize persisted conversation tool grants."""
    if not isinstance(raw, dict):
        return {}

    def _str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
        return out

    mode = str(raw.get("file_access_mode") or "").strip().lower()
    if mode not in {"off", "read", "ask", "auto"}:
        mode = ""
    ctx = {
        "allowed_tools": _str_list(raw.get("allowed_tools")),
        "pinned_tools": _str_list(raw.get("pinned_tools")),
        "file_access_mode": mode,
    }
    if not ctx["allowed_tools"] and not ctx["pinned_tools"] and not ctx["file_access_mode"]:
        return {}
    return ctx


def all_context_off_policy() -> dict[str, Any]:
    """Return the explicit all-off caller policy used by chat requests."""
    return {
        "context_ambient": False,
        "context_documents": False,
        "context_tools": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "context_clipboard": False,
        "_context_selection_enabled": False,
        "file_access": "off",
        "tools": {},
    }


def normalized_context_policy(raw: Any) -> dict[str, Any]:
    """Normalize a caller-like context policy from the chat UI."""
    if not isinstance(raw, dict):
        return {}

    def _mode(value: Any, default: str = "off") -> str:
        mode = str(value or default or "off").strip().lower()
        if mode == "on":
            return "auto"
        return mode if mode in {"off", "auto", "model"} else default

    tools = raw.get("tools")
    policy = all_context_off_policy()
    policy.update(
        {
            "context_ambient": bool(raw.get("context_ambient", False)),
            "context_documents_mode": tool_modes.context_mode(raw, "documents"),
            "context_browser_mode": tool_modes.context_mode(raw, "browser"),
            "context_github_mode": tool_modes.context_mode(raw, "github"),
            "context_memory_mode": tool_modes.context_mode(raw, "memory"),
            "context_screenshot": _mode(raw.get("context_screenshot"), "off"),
            "context_clipboard": bool(raw.get("context_clipboard", False)),
            "file_access": tool_modes.local_file_access_mode(raw),
            "tools": dict(tools) if isinstance(tools, dict) else {},
        }
    )
    policy["context_documents"] = policy["context_documents_mode"] == "auto"
    policy["context_tools"] = any(
        policy[key] == "model"
        for key in (
            "context_documents_mode",
            "context_browser_mode",
            "context_github_mode",
            "context_memory_mode",
        )
    )
    policy["_context_selection_enabled"] = bool(raw.get("_context_selection_enabled", False))
    return policy
