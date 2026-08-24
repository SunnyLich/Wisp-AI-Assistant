"""Build the truthful per-prompt tool inventory shown by the intent picker."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.system.env_utils import mcp_server_id_from_tool

_FILE_READ = {"list_files", "read_file"}
_FILE_WRITE = {"create_file", "edit_file", "write_file"}
_GROUPS: tuple[tuple[str, str, set[str]], ...] = (
    ("Read files", "Read files in the configured project roots.", _FILE_READ),
    ("Edit files", "Create or change files in the configured project roots.", _FILE_WRITE),
    ("Web search", "Search and retrieve websites.", {"web_search", "retrieve_website"}),
    ("Browser", "Read browser context when it is requested.", {"get_context.browser"}),
    ("Documents", "Read document context when it is requested.", {"get_context.documents"}),
    ("Git", "Inspect repository status and changes.", {"git_status", "git_diff"}),
    ("GitHub", "Read configured GitHub repositories and issues.", {"github_repo", "github_issue"}),
    ("Memory", "Search or save OpenWand memory.", {"memory_search", "memory_save"}),
    ("Screen", "Capture the screen when the model requests it.", {"capture_screen"}),
    (
        "Background tasks",
        "Start and inspect scoped background work.",
        {"delegate_background_task", "background_task_status"},
    ),
)


def _pretty_name(name: str) -> str:
    """Return a compact human label for an ungrouped tool name."""
    text = str(name or "").replace("-", " ").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "Tool"


def _item(
    name: str,
    *,
    status: str,
    count: int = 1,
    description: str = "",
    tools: set[str] | None = None,
    toggleable: bool = False,
) -> dict[str, Any]:
    normalized = str(status or "unavailable").strip().lower()
    if normalized not in {"ready", "ask", "unavailable"}:
        normalized = "unavailable"
    return {
        "id": str(name or "tool").strip().lower().replace(" ", "_"),
        "name": str(name or "Tool"),
        "status": normalized,
        "count": max(1, int(count or 1)),
        "description": str(description or ""),
        "tools": sorted(str(value) for value in (tools or set()) if str(value)),
        "enabled": normalized != "unavailable",
        "toggleable": bool(toggleable and normalized != "unavailable"),
    }


def _normalize_schema_names(names: list[str]) -> set[str]:
    """Collapse source-specific get_context grants into the real schema name."""
    normalized = {str(name or "").strip() for name in names if str(name or "").strip()}
    if {"get_context.browser", "get_context.documents"} & normalized:
        normalized.add("get_context")
    return normalized


def _has_configured_root(file_roots: list[str]) -> bool:
    """Return whether at least one configured file root currently exists."""
    for root in file_roots:
        try:
            if Path(str(root)).expanduser().is_dir():
                return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def build_openwand_inventory(
    *,
    provider: str,
    model: str,
    allowed_tools: list[str],
    file_access_mode: str,
    tool_descriptions: dict[str, str] | None = None,
    file_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Describe schemas OpenWand will offer on the selected chat route.

    ``Ready`` means the schema is offered by the configured route. It does not
    promise that a network service or account will succeed at call time.
    """
    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    names = _normalize_schema_names(allowed_tools or [])
    descriptions = {
        str(key): str(value or "") for key, value in (tool_descriptions or {}).items()
    }
    roots_ready = _has_configured_root(file_roots or [])
    route_supports_tools = provider != "copilot"
    items: list[dict[str, Any]] = []
    consumed: set[str] = set()

    for label, description, members in _GROUPS:
        present = names & members
        if not present:
            continue
        consumed.update(present)
        status = "ready"
        if not route_supports_tools:
            status = "unavailable"
        elif present & (_FILE_READ | _FILE_WRITE) and not roots_ready:
            status = "unavailable"
            description = "No local file root is configured."
        elif present & _FILE_WRITE and str(file_access_mode or "").lower() == "ask":
            status = "ask"
        items.append(
            _item(
                label,
                status=status,
                count=len(present),
                description=description,
                tools=present,
                toggleable=route_supports_tools and status != "unavailable",
            )
        )

    mcp_groups: dict[str, set[str]] = {}
    for name in sorted(names - consumed - {"get_context"}):
        server_id = mcp_server_id_from_tool(name, descriptions.get(name, ""))
        if server_id:
            mcp_groups.setdefault(server_id, set()).add(name)
            consumed.add(name)
    if mcp_groups:
        count = sum(len(group) for group in mcp_groups.values())
        server_count = len(mcp_groups)
        description = (
            f"{count} tool{'s' if count != 1 else ''} from "
            f"{server_count} connected app{'s' if server_count != 1 else ''}."
        )
        items.append(
            _item(
                "Connected apps",
                status="ready" if route_supports_tools else "unavailable",
                count=count,
                description=description,
                tools=set().union(*mcp_groups.values()),
                toggleable=route_supports_tools,
            )
        )

    for name in sorted(names - consumed - {"get_context"}):
        items.append(
            _item(
                _pretty_name(name),
                status="ready" if route_supports_tools else "unavailable",
                description=descriptions.get(name, ""),
                tools={name},
                toggleable=route_supports_tools,
            )
        )

    available_count = sum(
        int(item["count"]) for item in items if item["status"] != "unavailable"
    )
    route = " / ".join(part for part in (provider or "OpenWand", model) if part)
    return {
        "execution": route or "OpenWand",
        "count": available_count,
        "items": items,
        "note": "Switches apply to this prompt only; Ask still requires approval.",
    }


def build_harness_inventory(
    *,
    execution_mode: str,
    model: str,
    approval_mode: str,
) -> dict[str, Any]:
    """Describe native tools explicitly configured by a local agent harness."""
    mode = str(execution_mode or "").strip().lower()
    approval = str(approval_mode or "ask").strip().lower()
    model = str(model or "").strip()
    if mode == "claude":
        items = [
            _item("Read files", status="ready", description="Claude Read tool."),
            _item("Find files", status="ready", description="Claude Glob tool."),
            _item("Search files", status="ready", description="Claude Grep tool."),
        ]
    else:
        read_only = approval == "read_only"
        asks = approval == "ask"
        full_access = approval == "full_access"
        items = [
            _item("Read files", status="ready", description="Read access in the selected workspace."),
            _item(
                "Edit files",
                status="unavailable" if read_only else ("ask" if asks else "ready"),
                description="Workspace changes follow the selected Codex approval policy.",
            ),
            _item(
                "Run commands",
                status="unavailable" if read_only else ("ask" if asks else "ready"),
                description="Commands run under the selected Codex sandbox.",
            ),
            _item(
                "Network",
                status="ready" if full_access else ("ask" if asks else "unavailable"),
                description="Network access requires full access or an approved escalation.",
            ),
        ]
    count = sum(1 for item in items if item["status"] != "unavailable")
    label = "Claude" if mode == "claude" else "Codex"
    execution = " / ".join(part for part in (label, model) if part)
    return {
        "execution": execution,
        "count": count,
        "items": items,
        "note": "Native harness tools are locked to the active sandbox and approval mode.",
    }
