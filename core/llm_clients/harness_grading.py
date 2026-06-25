"""Local deterministic graders for unified chat tool-loop traces."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


@dataclass(frozen=True)
class ExpectedTool:
    """Expected tool call for a trace grader."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    match: str = "contains"


@dataclass(frozen=True)
class HarnessItem:
    """One local harness grading item."""

    id: str
    prompt: str
    expected_tools: list[ExpectedTool] = field(default_factory=list)
    expected_output_contains: list[str] = field(default_factory=list)
    require_recovery: bool = False
    reject_completion_gate_miss: bool = True


def harness_spec(items: list[HarnessItem], *, name: str = "Wisp Unified Chat Tool Flow") -> dict[str, Any]:
    """Return a JSON-friendly local harness spec."""
    return {
        "name": name,
        "item_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "prompt": {"type": "string"},
                "expected_tools": {"type": "array"},
                "expected_output_contains": {"type": "array"},
            },
            "required": ["id", "prompt"],
        },
        "testing_criteria": [
            "tool names appear in order",
            "expected tool arguments are matched",
            "final answer contains required evidence",
            "required recovery follows failed tool results",
            "completion gate is not missed",
        ],
        "data": [_item_dict(item) for item in items],
    }


def sample_from_trace(trace) -> dict[str, Any]:
    """Convert a Wisp trace into local grading sample fields."""
    output_tools = []
    for call in trace.tool_calls:
        output_tools.append({
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments or {}, sort_keys=True),
            },
        })
    return {
        "output_text": trace.final_text,
        "output_tools": output_tools,
        "metadata": dict(trace.metadata or {}),
    }


def grade_trace(item: HarnessItem, trace) -> dict[str, Any]:
    """Grade one trace using local tool and answer checks."""
    sample = sample_from_trace(trace)
    tool_name_score = _grade_tool_names(item.expected_tools, sample["output_tools"])
    tool_argument_score = _grade_tool_arguments(item.expected_tools, sample["output_tools"])
    final_answer_score = _grade_output_text(item.expected_output_contains, sample["output_text"])
    recovery_score = _grade_recovery(trace) if item.require_recovery else 1.0
    gate_score = 0.0 if item.reject_completion_gate_miss and trace.metadata.get("completion_gate_missed") else 1.0
    score = (
        0.30 * tool_name_score
        + 0.30 * tool_argument_score
        + 0.25 * final_answer_score
        + 0.10 * recovery_score
        + 0.05 * gate_score
    )
    passed = score >= 0.999
    return {
        "score": round(score, 4),
        "passed": passed,
        "graders": {
            "tool_names": tool_name_score,
            "tool_arguments": tool_argument_score,
            "final_answer": final_answer_score,
            "recovery": recovery_score,
            "completion_gate": gate_score,
        },
        "sample": sample,
    }


def default_items_by_scenario() -> dict[str, HarnessItem]:
    """Return local grading items for the built-in harness scenarios."""
    return {
        "synthetic_file_context": HarnessItem(
            id="synthetic_file_context",
            prompt="In this synthetic project, what does the app use for settings storage?",
            expected_tools=[
                ExpectedTool("list_files"),
                ExpectedTool("read_file", {"path": "config.py"}),
            ],
            expected_output_contains=["settings", "settings.json"],
        ),
        "synthetic_tool_recovery": HarnessItem(
            id="synthetic_tool_recovery",
            prompt="Read notes.md and summarize it.",
            expected_tools=[
                ExpectedTool("read_file", {"path": "notes.md"}),
                ExpectedTool("list_files"),
                ExpectedTool("read_file", {"path": "docs/notes.md"}),
            ],
            expected_output_contains=["settings.json", "startup||app starts||at startup"],
            require_recovery=True,
        ),
        "needs_file_context": HarnessItem(
            id="needs_file_context",
            prompt="What does this project use for settings storage?",
            expected_tools=[
                ExpectedTool("list_files"),
                ExpectedTool("read_file"),
            ],
            expected_output_contains=["settings"],
        ),
        "edit_plus_verification": HarnessItem(
            id="edit_plus_verification",
            prompt="Fix the syntax error in app.py and verify it.",
            expected_tools=[
                ExpectedTool("read_file", {"path": "app.py"}),
                ExpectedTool("edit_file", {"path": "app.py"}),
                ExpectedTool("run_command"),
            ],
            expected_output_contains=["fixed", "verified"],
        ),
    }


def _grade_tool_names(expected: list[ExpectedTool], output_tools: list[dict[str, Any]]) -> float:
    """Return 1 when expected tool names appear in order."""
    if not expected:
        return 1.0
    index = 0
    names = [str(tool.get("function", {}).get("name") or "") for tool in output_tools]
    for expected_tool in expected:
        try:
            found = names.index(expected_tool.name, index)
        except ValueError:
            return 0.0
        index = found + 1
    return 1.0


def _grade_tool_arguments(expected: list[ExpectedTool], output_tools: list[dict[str, Any]]) -> float:
    """Return 1 when expected tool arguments match corresponding calls."""
    if not expected:
        return 1.0
    start = 0
    for expected_tool in expected:
        match_index = _find_matching_tool_with_arguments(output_tools, expected_tool, start)
        if match_index < 0:
            return 0.0
        start = match_index + 1
    return 1.0


def _grade_output_text(expected_contains: list[str], output_text: str) -> float:
    """Return 1 when all expected strings or explicit alternatives appear."""
    if not expected_contains:
        return 1.0
    text = str(output_text or "").lower()
    for needle in expected_contains:
        alternatives = [part.strip().lower() for part in str(needle).split("||") if part.strip()]
        if not alternatives or not any(alternative in text for alternative in alternatives):
            return 0.0
    return 1.0


def _grade_recovery(trace) -> float:
    """Return 1 when a successful tool result follows a failed one."""
    saw_failure = False
    for observation in trace.observations:
        for result in observation.tool_results:
            if saw_failure and result.ok:
                return 1.0
            if not result.ok:
                saw_failure = True
    return 0.0


def _find_matching_tool_with_arguments(
    output_tools: list[dict[str, Any]],
    expected_tool: ExpectedTool,
    start: int,
) -> int:
    """Find the next tool call matching the requested name and expected args."""
    for index in range(start, len(output_tools)):
        if output_tools[index].get("function", {}).get("name") != expected_tool.name:
            continue
        if not expected_tool.arguments:
            return index
        actual = _tool_arguments(output_tools[index])
        if expected_tool.match == "eq":
            if actual == expected_tool.arguments:
                return index
        elif all(actual.get(key) == value for key, value in expected_tool.arguments.items()):
            return index
    return -1


def _tool_arguments(tool: dict[str, Any]) -> dict[str, Any]:
    """Parse a tool-call argument payload."""
    raw = tool.get("function", {}).get("arguments") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _item_dict(item: HarnessItem) -> dict[str, Any]:
    """Convert a grading item to plain JSON."""
    return {
        "id": item.id,
        "prompt": item.prompt,
        "expected_tools": [
            {
                "name": tool.name,
                "arguments": tool.arguments,
                "match": tool.match,
            }
            for tool in item.expected_tools
        ],
        "expected_output_contains": item.expected_output_contains,
        "require_recovery": item.require_recovery,
        "reject_completion_gate_miss": item.reject_completion_gate_miss,
    }
