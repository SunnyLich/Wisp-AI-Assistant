"""Translation helpers for rendered auto-agent activity text."""

from __future__ import annotations

import re
from typing import Any

from ui.i18n import t

DEFAULT_AGENT_NAMES = {"Coordinator", "Builder", "Reviewer"}
AGENT_ROLES = {"Coordinator", "Planner", "Implementer", "Reviewer", "Tester", "Researcher", "Agent"}
STATUS_WORDS = {"waiting", "blocked", "done", "continue", "ready_for_review", "complete"}
MODE_WORDS = {"delta", "full", "read-only full"}


def translate_agent_name(name: str) -> str:
    """Translate built-in agent names while preserving user-defined names."""
    text = str(name or "").strip()
    return t(text) if text in DEFAULT_AGENT_NAMES else text


def translate_agent_role(role: str) -> str:
    """Translate built-in agent roles while preserving custom roles."""
    text = str(role or "").strip()
    return t(text) if text in AGENT_ROLES else text


def _translate_status_word(value: str) -> str:
    text = str(value or "").strip()
    return t(text) if text in STATUS_WORDS else text


def _translate_mode(value: str) -> str:
    text = str(value or "").strip()
    return t(text) if text in MODE_WORDS else text


def translate_agent_status(status: str) -> str:
    """Translate live meeting status strings, including dynamic suffixes."""
    text = str(status or "")
    patterns: tuple[tuple[str, str], ...] = (
        (r"^Waiting (?P<elapsed>.+)$", "Waiting {elapsed}"),
        (r"^Receiving response \((?P<elapsed>.+)\)$", "Receiving response ({elapsed})"),
        (r"^Handing off to (?P<agent>.+)$", "Handing off to {agent}"),
        (r"^Explicit handoff to (?P<agent>.+)$", "Explicit handoff to {agent}"),
        (r"^Prompt (?P<summary>.+)$", "Prompt {summary}"),
        (r"^Using (?P<tool>.+)$", "Using {tool}"),
    )
    for pattern, template in patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        groups = match.groupdict()
        if "agent" in groups:
            groups["agent"] = translate_agent_name(groups["agent"])
        return t(template).format(**groups)
    return t(text)


def translate_agent_health_badge(health: dict[str, Any]) -> str:
    """Translate the compact health badge shown on agent cards."""
    calls = int(health.get("calls", 0))
    avg = "-" if not calls else f"{float(health.get('total_latency', 0.0)) / calls:.1f}s"
    return t("avg {avg} | invalid {invalid} | repair {repairs} | fallback {fallbacks}").format(
        avg=avg,
        invalid=int(health.get("invalid_json", 0)),
        repairs=int(health.get("repairs", 0)),
        fallbacks=int(health.get("fallbacks", 0)),
    )


def translate_agent_health_detail(health: dict[str, Any]) -> str:
    """Translate the expanded health summary in the agent detail panel."""
    calls = int(health.get("calls", 0))
    avg = 0.0 if not calls else float(health.get("total_latency", 0.0)) / calls
    return t(
        "calls {calls}, average latency {avg}s, invalid JSON {invalid}, repairs {repairs}, fallbacks {fallbacks}"
    ).format(
        calls=calls,
        avg=f"{avg:.1f}",
        invalid=int(health.get("invalid_json", 0)),
        repairs=int(health.get("repairs", 0)),
        fallbacks=int(health.get("fallbacks", 0)),
    )


def translate_agent_activity_text(text: str) -> str:
    """Translate known agent run activity/log lines while preserving free-form content."""
    value = str(text or "")
    if not value:
        return value
    if "\n" in value:
        return "\n".join(translate_agent_activity_text(part) for part in value.splitlines())

    exact = {
        "Message cannot be empty.": "Message cannot be empty.",
        "No messages yet.": "No messages yet.",
        "Agent task cancelled.": "Agent task cancelled.",
        "Agent run finished.": "Agent run finished.",
    }
    if value in exact:
        return t(exact[value])

    patterns: tuple[tuple[str, str], ...] = (
        (r"^Told (?P<target>[^:]+): (?P<message>.*)$", "Told {target}: {message}"),
        (r"^Heard from (?P<source>[^:]+): (?P<message>.*)$", "Heard from {source}: {message}"),
        (r"^Thought: (?P<message>.*)$", "Thought: {message}"),
        (r"^thought: (?P<message>.*)$", "thought: {message}"),
        (r"^Handoff \((?P<status>[^)]+)\): (?P<reason>.*)$", "Handoff ({status}): {reason}"),
        (r"^(?P<agent>.+) returned final response$", "{agent} returned final response"),
        (r"^returned final response$", "returned final response"),
        (r"^agent turn (?P<turn>[^:]+): (?P<agent>.+)$", "agent turn {turn}: {agent}"),
        (r"^agent read-only turn: (?P<agent>.+)$", "agent read-only turn: {agent}"),
        (r"^prompt prepared for (?P<agent>.+): (?P<chars>\d+) chars \((?P<mode>.+)\)$", "prompt prepared for {agent}: {chars} chars ({mode})"),
        (r"^requesting LLM tool response via (?P<route>.+)$", "requesting LLM tool response via {route}"),
        (r"^model call still waiting after (?P<elapsed>.+) via (?P<route>.+)$", "model call still waiting after {elapsed} via {route}"),
        (r"^model first token after (?P<elapsed>.+) via (?P<route>.+)$", "model first token after {elapsed} via {route}"),
        (r"^model response received in (?P<elapsed>.+)s \((?P<chars>\d+) chars\)$", "model response received in {elapsed}s ({chars} chars)"),
        (r"^model callback response received in (?P<elapsed>.+)s \((?P<chars>\d+) chars\)$", "model callback response received in {elapsed}s ({chars} chars)"),
        (r"^tool (?P<tool>\S+) failed: (?P<message>.*)$", "tool {tool} failed: {message}"),
        (r"^tool (?P<tool>\S+): exit (?P<code>-?\d+): (?P<message>.*)$", "tool {tool}: exit {code}: {message}"),
        (r"^tool call: (?P<tool>.+)$", "tool call: {tool}"),
        (r"^(?P<agent>.+) tool call: (?P<tool>.+)$", "{agent} tool call: {tool}"),
    )
    for pattern, template in patterns:
        match = re.match(pattern, value)
        if not match:
            continue
        groups = match.groupdict()
        for key in ("agent", "source", "target"):
            if key in groups:
                groups[key] = translate_agent_name(groups[key])
        if "status" in groups:
            groups["status"] = _translate_status_word(groups["status"])
        if "mode" in groups:
            groups["mode"] = _translate_mode(groups["mode"])
        if "message" in groups:
            groups["message"] = translate_agent_activity_text(groups["message"])
        return t(template).format(**groups)
    return t(value)


def translate_agent_log_line(line: str) -> str:
    """Translate a plain-text log line, preserving the timestamp prefix."""
    text = str(line or "")
    if text.startswith("[") and "] " in text:
        stamp, body = text.split("] ", 1)
        return f"{stamp}] {translate_agent_activity_text(body)}"
    return translate_agent_activity_text(text)
