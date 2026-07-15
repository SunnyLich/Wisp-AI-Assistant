"""Unified deterministic harness for chat tool-loop behavior."""
from __future__ import annotations

import html
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from core.llm_clients.chat_tool_loop import (
    ChatLoopModel,
    ChatModelTurn,
    ChatToolLoop,
    ChatToolRequest,
    WispObservation,
    WispToolCall,
    WispToolResult,
)


@dataclass(frozen=True)
class ChatScenario:
    """One behavior scenario to run through the unified chat flow."""

    name: str
    prompt: str
    tools: list[str]
    expected_relevant_tools: list[str] = field(default_factory=list)
    expected_change_tools: list[str] = field(default_factory=list)
    expected_verification_tools: list[str] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatFlowTrace:
    """Comparable trace for one scenario run."""

    flow: str
    scenario: str
    prompt: str
    tools_offered: list[str]
    tool_calls: list[WispToolCall]
    observations: list[WispObservation]
    final_text: str
    final_status: str
    progress_chunks: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatFlowRun:
    """Unified harness result for one scenario."""

    scenario: str
    score: dict[str, Any]
    trace: ChatFlowTrace


@dataclass(frozen=True)
class ChatFlowHarnessReport:
    """Full unified harness report for all scenarios."""

    generated_at: str
    runs: list[ChatFlowRun]

    @property
    def summary(self) -> dict[str, Any]:
        """Return compact aggregate counts for the harness run."""
        totals = {
            "scenarios": len(self.runs),
            "relevant_tool_called": 0,
            "answered_actual_request": 0,
            "recovered_after_failed_tool": 0,
            "completion_gate_missed": 0,
            "verification_attempted": 0,
        }
        for run in self.runs:
            if run.score.get("relevant_tool_called"):
                totals["relevant_tool_called"] += 1
            if run.score.get("answered_actual_request"):
                totals["answered_actual_request"] += 1
            if run.score.get("recovered_after_failed_tool"):
                totals["recovered_after_failed_tool"] += 1
            if run.score.get("completion_gate_missed"):
                totals["completion_gate_missed"] += 1
            if run.score.get("verification_attempted"):
                totals["verification_attempted"] += 1
        return totals


class ChatFlowRunner(Protocol):
    """Runner interface used by the unified harness."""

    name: str

    def run(self, scenario: ChatScenario) -> ChatFlowTrace:
        """Run one scenario and return a normalized trace."""


@dataclass(frozen=True)
class ScriptedModelStep:
    """One deterministic fake-model step for harness tests and dry runs."""

    tool_calls: list[WispToolCall] = field(default_factory=list)
    final: str = ""
    status: str = "continue"
    progress: str = ""


ToolFixtureQueue = list[WispToolResult | str]
ToolFixtureMap = dict[str, ToolFixtureQueue]
ScenarioFixtures = dict[str, ToolFixtureQueue | ToolFixtureMap]


class FakeToolExecutor:
    """Deterministic tool executor with per-tool or argument-aware fixture results."""

    def __init__(self, fixtures: ScenarioFixtures | None = None):
        """Initialize fake executor fixtures."""
        self._fixtures: ScenarioFixtures = {}
        for name, results in (fixtures or {}).items():
            if isinstance(results, dict):
                self._fixtures[name] = {key: list(value) for key, value in results.items()}
            else:
                self._fixtures[name] = list(results)

    def execute(self, call: WispToolCall) -> WispToolResult:
        """Execute one fake tool call."""
        fixture = self._fixtures.get(call.name)
        if isinstance(fixture, dict):
            key = _fixture_key(call)
            queue = fixture.get(key)
            if queue is None:
                queue = fixture.get("*") or []
                key = "*"
            value = queue.pop(0) if queue else f"{call.name} completed."
            fixture[key] = queue
        else:
            queue = fixture or []
            value = queue.pop(0) if queue else f"{call.name} completed."
            self._fixtures[call.name] = queue
        if isinstance(value, WispToolResult):
            return replace(value, call_id=call.id, name=call.name)
        return WispToolResult(
            call_id=call.id,
            name=call.name,
            ok=True,
            content=str(value),
        )


def _fixture_key(call: WispToolCall) -> str:
    """Return a stable fixture key for a tool call."""
    if "path" in call.arguments:
        path = str(call.arguments.get("path") or "").replace("\\", "/")
        return f"path={path}"
    if "folder" in call.arguments:
        folder = str(call.arguments.get("folder") or "").replace("\\", "/")
        return f"folder={folder}"
    return json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)


class ScriptedChatLoopModel(ChatLoopModel):
    """Scripted model adapter consumed by the provider-neutral loop."""

    def __init__(self, steps: list[ScriptedModelStep]):
        """Initialize the scripted model."""
        self._steps = list(steps)

    def next_turn(
        self,
        _request: ChatToolRequest,
        _observations: list[WispObservation],
        _tool_calls: list[WispToolCall],
    ) -> ChatModelTurn:
        """Return the next scripted model turn."""
        if not self._steps:
            return ChatModelTurn(final_text="Scripted model had no more steps.", status="script_exhausted")
        step = self._steps.pop(0)
        return ChatModelTurn(
            tool_calls=step.tool_calls,
            final_text=step.final,
            status=step.status,
            progress=step.progress,
        )


class UnifiedScriptedChatFlowRunner:
    """Harness runner that exercises the real provider-neutral ChatToolLoop."""

    def __init__(
        self,
        name: str,
        steps_by_scenario: dict[str, list[ScriptedModelStep]],
        *,
        fixtures_by_scenario: dict[str, ScenarioFixtures] | None = None,
        loop: ChatToolLoop | None = None,
    ):
        """Initialize loop-backed scripted runner."""
        self.name = name
        self._steps_by_scenario = steps_by_scenario
        self._fixtures_by_scenario = fixtures_by_scenario or {}
        self._loop = loop or ChatToolLoop()

    def run(self, scenario: ChatScenario) -> ChatFlowTrace:
        """Run a scenario through the neutral chat tool loop."""
        request = ChatToolRequest(
            messages=[{"role": "user", "content": scenario.prompt}],
            system_prompt="",
            model_route={"provider": "scripted", "model": self.name},
            tools=[{"name": name} for name in scenario.tools],
            allowed_tools=list(scenario.tools),
            pinned_tools=[],
            permissions=dict(scenario.permissions),
            budgets={},
            ambient_context=str(scenario.context.get("ambient_context") or ""),
            memory_context=str(scenario.context.get("memory_context") or ""),
            screenshot_b64=scenario.context.get("screenshot_b64"),
        )
        final = self._loop.run(
            request,
            ScriptedChatLoopModel(self._steps_by_scenario.get(scenario.name) or []),
            FakeToolExecutor(self._fixtures_by_scenario.get(scenario.name)),
        )
        return ChatFlowTrace(
            flow=self.name,
            scenario=scenario.name,
            prompt=scenario.prompt,
            tools_offered=list(scenario.tools),
            tool_calls=final.tool_calls,
            observations=final.observations,
            final_text=final.text,
            final_status=final.status,
            progress_chunks=list(final.metadata.get("progress_chunks") or []),
            metadata=final.metadata,
        )


class LiveResponsesUnifiedRunner:
    """Run a scenario through the provider-neutral loop using live Responses."""

    def __init__(
        self,
        name: str,
        client,
        *,
        model: str,
        instructions: str,
        tools: list[dict],
        loop: ChatToolLoop | None = None,
        fixtures_by_scenario: dict[str, ScenarioFixtures] | None = None,
    ):
        """Initialize live unified runner."""
        self.name = name
        self._client = client
        self._model = model
        self._instructions = instructions
        self._tools = tools
        self._loop = loop or ChatToolLoop()
        self._fixtures_by_scenario = fixtures_by_scenario or {}

    def run(self, scenario: ChatScenario) -> ChatFlowTrace:
        """Run one live scenario through the neutral loop."""
        from core.llm_clients.responses_chat_adapter import ResponsesChatLoopModel as RuntimeResponsesChatLoopModel

        scenario_tools = _filter_response_tools(self._tools, scenario.tools)
        request = ChatToolRequest(
            messages=[{"role": "user", "content": scenario.prompt}],
            system_prompt=self._instructions,
            model_route={"provider": "chatgpt", "model": self._model},
            tools=scenario_tools,
            allowed_tools=list(scenario.tools),
            pinned_tools=list(scenario.tools),
            permissions=dict(scenario.permissions),
            budgets={},
        )
        final = self._loop.run(
            request,
            RuntimeResponsesChatLoopModel(
                self._client,
                model=self._model,
                instructions=self._instructions,
                tools=scenario_tools,
            ),
            self._executor_for(scenario),
        )
        return ChatFlowTrace(
            flow=self.name,
            scenario=scenario.name,
            prompt=scenario.prompt,
            tools_offered=list(scenario.tools),
            tool_calls=final.tool_calls,
            observations=final.observations,
            final_text=final.text,
            final_status=final.status,
            progress_chunks=list(final.metadata.get("progress_chunks") or []),
            metadata=final.metadata,
        )

    def _executor_for(self, scenario: ChatScenario):
        """Return synthetic or live executor for this scenario."""
        fixtures = self._fixtures_by_scenario.get(scenario.name)
        if fixtures is not None:
            return FakeToolExecutor(fixtures)
        from core.llm_clients.responses_chat_adapter import LiveModelToolExecutor as RuntimeLiveModelToolExecutor

        return RuntimeLiveModelToolExecutor(allowed_tools=scenario.tools)


def run_chat_flow_harness(
    scenarios: list[ChatScenario],
    runner: ChatFlowRunner,
    *,
    parallel: bool = True,
    max_workers: int | None = None,
) -> ChatFlowHarnessReport:
    """Run scenarios through the unified flow and return metrics plus traces."""
    if parallel:
        return _run_chat_flow_harness_parallel(scenarios, runner, max_workers=max_workers)
    runs: list[ChatFlowRun] = []
    for scenario in scenarios:
        trace = _run_or_error_trace(runner, scenario)
        runs.append(ChatFlowRun(scenario=scenario.name, score=score_trace(scenario, trace), trace=trace))
    return ChatFlowHarnessReport(generated_at=datetime.now().isoformat(timespec="seconds"), runs=runs)


def _run_chat_flow_harness_parallel(
    scenarios: list[ChatScenario],
    runner: ChatFlowRunner,
    *,
    max_workers: int | None = None,
) -> ChatFlowHarnessReport:
    """Run scenarios concurrently and gather them into one report."""
    if not scenarios:
        return ChatFlowHarnessReport(generated_at=datetime.now().isoformat(timespec="seconds"), runs=[])
    workers = max_workers or min(8, max(1, len(scenarios)))
    futures = {}
    traces: dict[str, ChatFlowTrace] = {}
    started_at = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chat-flow") as executor:
        for scenario in scenarios:
            futures[executor.submit(_run_or_error_trace, runner, scenario)] = scenario.name
        for future in as_completed(futures):
            traces[futures[future]] = future.result()

    runs: list[ChatFlowRun] = []
    for scenario in scenarios:
        trace = traces[scenario.name]
        trace.metadata.setdefault("harness_parallel", True)
        trace.metadata.setdefault("harness_elapsed_seconds", round(time.monotonic() - started_at, 3))
        runs.append(ChatFlowRun(scenario=scenario.name, score=score_trace(scenario, trace), trace=trace))
    return ChatFlowHarnessReport(generated_at=datetime.now().isoformat(timespec="seconds"), runs=runs)


def _run_or_error_trace(runner: ChatFlowRunner, scenario: ChatScenario) -> ChatFlowTrace:
    """Run a flow, converting exceptions into traceable harness output."""
    started_at = time.monotonic()
    try:
        trace = runner.run(scenario)
    except Exception as exc:  # noqa: BLE001 - harness must record flow failures
        trace = ChatFlowTrace(
            flow=runner.name,
            scenario=scenario.name,
            prompt=scenario.prompt,
            tools_offered=list(scenario.tools),
            tool_calls=[],
            observations=[],
            final_text=f"{type(exc).__name__}: {exc}",
            final_status="runner_error",
            metadata={"error_type": type(exc).__name__, "error": str(exc)},
        )
    trace.metadata.setdefault("duration_seconds", round(time.monotonic() - started_at, 3))
    return trace


def score_trace(scenario: ChatScenario, trace: ChatFlowTrace) -> dict[str, Any]:
    """Score one trace against the scenario's behavioral checkpoints."""
    called_names = [call.name for call in trace.tool_calls]
    relevant = set(scenario.expected_relevant_tools)
    change_tools = set(scenario.expected_change_tools)
    verification_tools = set(scenario.expected_verification_tools)
    first_relevant_turn = None
    for idx, call in enumerate(trace.tool_calls, start=1):
        if call.name in relevant:
            first_relevant_turn = idx
            break
    return {
        "tool_calls_total": len(trace.tool_calls),
        "relevant_tool_called": bool(relevant and relevant.intersection(called_names)),
        "first_relevant_tool_turn": first_relevant_turn,
        "final_after_observation": bool(trace.final_text and trace.observations),
        "completion_gate_missed": bool(trace.metadata.get("completion_gate_missed")),
        "permission_boundary_reported": _permission_boundary_reported(trace),
        "verification_attempted": bool(verification_tools.intersection(called_names)),
        "made_allowed_change": _made_allowed_change(trace, change_tools),
        "relevant_tool_succeeded": _relevant_tool_succeeded(trace, relevant),
        "failed_tool_observed": _failed_tool_observed(trace),
        "recovered_after_failed_tool": _recovered_after_failed_tool(trace),
        "answered_actual_request": _answered_actual_request(scenario, trace),
        "hallucinated_context": bool(trace.metadata.get("hallucinated_context")),
        "final_status": trace.final_status,
        "final_text": trace.final_text,
    }


def write_harness_artifacts(
    report: ChatFlowHarnessReport,
    output_root: str | Path,
    *,
    report_title: str = "Unified Chat Flow Harness",
) -> Path:
    """Write unified harness traces and summaries to a timestamped artifact folder."""
    root = Path(output_root)
    run_dir = root / report.generated_at.replace(":", "-")
    trace_dir = run_dir / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps(report.summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    harness_scores = _harness_scores(report)
    (run_dir / "harness_scores.json").write_text(
        json.dumps(harness_scores, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "harness_spec.json").write_text(
        json.dumps(_harness_spec(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "results.json").write_text(
        json.dumps(_report_dict(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    scenario_payload = [
        {
            "scenario": run.scenario,
            "score": run.score,
        }
        for run in report.runs
    ]
    (run_dir / "scenarios.json").write_text(
        json.dumps(scenario_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    for run in report.runs:
        (trace_dir / f"{run.scenario}.json").write_text(
            json.dumps(_trace_dict(run.trace), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    (run_dir / "report.md").write_text(render_markdown_report(report, title=report_title), encoding="utf-8")
    (run_dir / "report.html").write_text(render_html_report(report, title=report_title), encoding="utf-8")
    return run_dir


def render_markdown_report(report: ChatFlowHarnessReport, *, title: str = "Unified Chat Flow Harness") -> str:
    """Render a compact human-readable harness report."""
    lines = [
        f"# {title}",
        "",
        f"Generated: {report.generated_at}",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(report.summary, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Scenarios",
        "",
    ]
    for run in report.runs:
        final = _one_line(run.score["final_text"])
        lines.extend(
            [
                f"### {run.scenario}",
                "",
                "| Checkpoint | Unified Flow |",
                "| --- | --- |",
                f"| Final status | `{run.score['final_status']}` |",
                f"| Tool calls | `{run.score['tool_calls_total']}` |",
                f"| Relevant tool called | `{run.score['relevant_tool_called']}` |",
                f"| Relevant tool succeeded | `{run.score['relevant_tool_succeeded']}` |",
                f"| Recovered after failed tool | `{run.score['recovered_after_failed_tool']}` |",
                f"| Answered actual request | `{run.score['answered_actual_request']}` |",
                f"| Verification attempted | `{run.score['verification_attempted']}` |",
                f"| Completion gate missed | `{run.score['completion_gate_missed']}` |",
                f"| Final answer excerpt | {final} |",
                "",
            ]
        )
    return "\n".join(lines)


def render_html_report(report: ChatFlowHarnessReport, *, title: str = "Unified Chat Flow Harness") -> str:
    """Render an inspectable HTML harness report."""
    scenario_cards = []
    for run in report.runs:
        scenario_cards.append(
            f"""
            <section class="scenario">
              <h2>{html.escape(run.scenario)}</h2>
              {render_html_flow_panel("Unified Flow", run.score)}
            </section>
            """
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #202124; background: #f7f7f8; }}
    h1 {{ font-size: 24px; margin: 0 0 8px; }}
    h2 {{ font-size: 18px; margin: 0 0 12px; }}
    .meta {{ color: #5f6368; margin-bottom: 20px; }}
    .summary, .scenario {{ background: #fff; border: 1px solid #dadce0; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
    .panel {{ border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; background: #fcfcfd; }}
    .panel h3 {{ font-size: 15px; margin: 0 0 10px; }}
    dl {{ display: grid; grid-template-columns: 180px 1fr; gap: 8px 10px; margin: 0; }}
    dt {{ color: #5f6368; }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    pre {{ white-space: pre-wrap; background: #f1f3f4; border-radius: 6px; padding: 10px; max-height: 220px; overflow: auto; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <div class="meta">Generated: {html.escape(report.generated_at)}</div>
  <section class="summary">
    <h2>Summary</h2>
    <pre>{html.escape(json.dumps(report.summary, indent=2, ensure_ascii=False))}</pre>
  </section>
  {''.join(scenario_cards)}
</body>
</html>
"""


def render_html_flow_panel(title: str, metrics: dict[str, Any]) -> str:
    """Render one flow panel for the HTML report."""
    fields = [
        ("Final status", metrics["final_status"]),
        ("Tool calls", metrics["tool_calls_total"]),
        ("Relevant tool called", metrics["relevant_tool_called"]),
        ("Relevant tool succeeded", metrics["relevant_tool_succeeded"]),
        ("Recovered after failure", metrics["recovered_after_failed_tool"]),
        ("Answered request", metrics["answered_actual_request"]),
        ("Verification", metrics["verification_attempted"]),
        ("Gate missed", metrics["completion_gate_missed"]),
    ]
    items = "\n".join(
        f"<dt>{html.escape(label)}</dt><dd>{html.escape(str(value))}</dd>"
        for label, value in fields
    )
    final_text = html.escape(str(metrics.get("final_text") or ""))
    return f"""
      <div class="panel">
        <h3>{html.escape(title)}</h3>
        <dl>{items}</dl>
        <h3>Final Answer</h3>
        <pre>{final_text}</pre>
      </div>
    """


def live_chatgpt_runner(
    model: str | None = None,
    *,
    synthetic_tools: bool = False,
) -> LiveResponsesUnifiedRunner:
    """Build a live unified runner for ChatGPT Responses harness runs."""
    import config
    from core.llm_clients import client as llm

    selected_model = model or config.CHAT_LLM_MODEL or config.LLM_MODEL
    allowed_tools = ["list_files", "read_file", "edit_file", "write_file", "memory_search"]
    tools = llm._get_responses_tool_schemas(
        "",
        include_general=True,
        allowed_tools=allowed_tools,
        pinned_tools=allowed_tools,
    )
    instructions = llm._with_local_file_tools_note(
        llm._with_tools_note(config.get_system_prompt(), True),
        allowed_tools,
    )
    return LiveResponsesUnifiedRunner(
        "unified-live-chatgpt",
        llm._get_chat_codex_client(),
        model=selected_model,
        instructions=instructions,
        tools=tools,
        fixtures_by_scenario=synthetic_live_fixtures() if synthetic_tools else {},
    )


def synthetic_live_scenarios() -> list[ChatScenario]:
    """Return live-model scenarios that use only synthetic tool data."""
    return [
        ChatScenario(
            name="synthetic_file_context",
            prompt=(
                "In this synthetic project, what does the app use for settings storage? "
                "Use available tools if you need context."
            ),
            tools=["list_files", "read_file"],
            expected_relevant_tools=["list_files", "read_file"],
        ),
        ChatScenario(
            name="synthetic_tool_recovery",
            prompt="Read notes.md and summarize it. Use available tools if needed.",
            tools=["read_file", "list_files"],
            expected_relevant_tools=["read_file", "list_files"],
        ),
    ]


def synthetic_live_fixtures() -> dict[str, ScenarioFixtures]:
    """Return synthetic tool outputs for safe live-model harness runs."""
    return {
        "synthetic_file_context": {
            "list_files": ["config.py\napp.py\nREADME.md"],
            "read_file": {
                "path=config.py": ["SETTINGS_STORAGE = 'json-file'\nSETTINGS_PATH = 'settings.json'\n"],
                "path=app.py": ["from config import SETTINGS_PATH, SETTINGS_STORAGE\n"],
                "path=README.md": ["Synthetic project README.\n"],
                "*": [
                    WispToolResult(
                        call_id="fixture_missing",
                        name="read_file",
                        ok=False,
                        content="File not found",
                    )
                ],
            },
        },
        "synthetic_tool_recovery": {
            "read_file": {
                "path=notes.md": [
                    WispToolResult(
                        call_id="fixture_missing",
                        name="read_file",
                        ok=False,
                        content="File not found: notes.md",
                    )
                ],
                "path=docs/notes.md": [
                    "Notes: The project stores settings in settings.json and loads them at startup.",
                ],
                "path=README.md": ["README: see docs/notes.md for project notes."],
                "*": [
                    WispToolResult(
                        call_id="fixture_missing",
                        name="read_file",
                        ok=False,
                        content="File not found",
                    )
                ],
            },
            "list_files": ["docs/notes.md\nREADME.md"],
        },
    }


def sample_harness_self_test_scenarios() -> list[ChatScenario]:
    """Return scripted scenarios used to self-test the unified harness plumbing."""
    return [
        ChatScenario(
            name="needs_file_context",
            prompt="What does this project use for settings storage?",
            tools=["list_files", "read_file"],
            expected_relevant_tools=["list_files", "read_file"],
        ),
        ChatScenario(
            name="edit_plus_verification",
            prompt="Fix the syntax error in app.py and verify it.",
            tools=["read_file", "edit_file", "run_command"],
            expected_relevant_tools=["read_file"],
            expected_change_tools=["edit_file"],
            expected_verification_tools=["run_command"],
        ),
        ChatScenario(
            name="permission_boundary",
            prompt="Delete old.log.",
            tools=[],
            expected_relevant_tools=[],
            permissions={"delete_file": "disabled"},
        ),
    ]


def _filter_response_tools(tools: list[dict], allowed_tool_names: list[str]) -> list[dict]:
    """Return Responses tool schemas allowed for one scenario."""
    allowed = set(allowed_tool_names)
    filtered = []
    for tool in tools:
        name = str(tool.get("name") or "")
        if not name and isinstance(tool.get("function"), dict):
            name = str(tool["function"].get("name") or "")
        if name in allowed:
            filtered.append(tool)
    return filtered


def _made_allowed_change(trace: ChatFlowTrace, change_tools: set[str]) -> bool:
    """Return whether a mutating expected tool succeeded."""
    if not change_tools:
        return False
    for observation in trace.observations:
        for result in observation.tool_results:
            if result.name in change_tools and result.ok:
                return True
    return False


def _relevant_tool_succeeded(trace: ChatFlowTrace, relevant_tools: set[str]) -> bool:
    """Return whether a relevant expected tool produced an ok result."""
    if not relevant_tools:
        return False
    for observation in trace.observations:
        for result in observation.tool_results:
            if result.name in relevant_tools and result.ok:
                return True
    return False


def _failed_tool_observed(trace: ChatFlowTrace) -> bool:
    """Return whether any tool result failed."""
    return any(
        not result.ok
        for observation in trace.observations
        for result in observation.tool_results
    )


def _recovered_after_failed_tool(trace: ChatFlowTrace) -> bool:
    """Return whether a later successful tool call followed a failed tool result."""
    saw_failure = False
    for observation in trace.observations:
        for result in observation.tool_results:
            if saw_failure and result.ok:
                return True
            if not result.ok:
                saw_failure = True
    return False


def _answered_actual_request(scenario: ChatScenario, trace: ChatFlowTrace) -> bool:
    """Conservative heuristic for whether the final text answered the scenario."""
    if trace.final_status == "runner_error" or not trace.final_text.strip():
        return False
    text = trace.final_text.lower()
    if scenario.name in {"synthetic_file_context", "needs_file_context"}:
        return "settings" in text and ("json" in text or "settings.json" in text or "storage" in text)
    if scenario.name == "synthetic_tool_recovery":
        return "settings.json" in text or ("settings" in text and "startup" in text)
    if scenario.expected_change_tools:
        return any(word in text for word in ("fixed", "changed", "updated", "created"))
    if _failed_tool_observed(trace) and not _recovered_after_failed_tool(trace):
        return False
    return True


def _permission_boundary_reported(trace: ChatFlowTrace) -> bool:
    """Return whether trace evidence or final text reports a permission boundary."""
    text = trace.final_text.lower()
    if "permission" in text or "disabled" in text or "not allowed" in text:
        return True
    for observation in trace.observations:
        for result in observation.tool_results:
            metadata = result.metadata or {}
            if metadata.get("permission_denied") or metadata.get("error_type") == "permission_disabled":
                return True
    return False


def _trace_dict(trace: ChatFlowTrace) -> dict[str, Any]:
    """Convert trace dataclasses to JSON-friendly dictionaries."""
    return asdict(trace)


def _report_dict(report: ChatFlowHarnessReport) -> dict[str, Any]:
    """Convert a full harness report into one consolidated JSON payload."""
    harness_scores = _harness_scores(report)
    return {
        "generated_at": report.generated_at,
        "summary": report.summary,
        "harness_scores": harness_scores,
        "runs": [
            {
                "scenario": run.scenario,
                "score": run.score,
                "harness_score": harness_scores.get(run.scenario),
                "trace": _trace_dict(run.trace),
            }
            for run in report.runs
        ],
    }


def _harness_scores(report: ChatFlowHarnessReport) -> dict[str, Any]:
    """Return local harness scores for scenarios with configured items."""
    from core.llm_clients.harness_grading import default_items_by_scenario, grade_trace

    items = default_items_by_scenario()
    scores = {}
    for run in report.runs:
        item = items.get(run.scenario)
        if item is None:
            continue
        scores[run.scenario] = grade_trace(item, run.trace)
    return scores


def _harness_spec(report: ChatFlowHarnessReport) -> dict[str, Any]:
    """Return a local harness spec for scenarios present in a report."""
    from core.llm_clients.harness_grading import default_items_by_scenario, harness_spec

    items_by_scenario = default_items_by_scenario()
    items = [
        items_by_scenario[run.scenario]
        for run in report.runs
        if run.scenario in items_by_scenario
    ]
    return harness_spec(items)


def _one_line(text: str, limit: int = 160) -> str:
    """Return escaped one-line text for Markdown tables."""
    value = " ".join(str(text or "").split())
    value = value.replace("|", "\\|")
    if len(value) > limit:
        return f"{value[:limit].rstrip()}..."
    return value or "(empty)"
