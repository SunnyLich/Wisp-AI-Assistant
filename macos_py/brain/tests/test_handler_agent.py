"""Unit tests for the ``brain.agent.run`` handler.

The agent loop is driven by a scripted model callback (no provider/network), so
these exercise the *real* ``core.agent`` runner end-to-end: spec deserialization,
the run-loop, log/trace event streaming, and the final-report result. Two seams
are covered -- the single-turn ``WISP_BRAIN_FAKE_LLM`` fast path and an explicit
``WISP_BRAIN_AGENT_TEST_SCRIPT`` of model turns.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from wisp_brain import handlers


def _events_of(events, name):
    return [data for event, data in events if event == name]


def _noop_spec(scope, *, title, objective):
    """A minimal task spec for a noop fake run.

    git and shell are disabled so the runner never shells out (a noop agent needs
    neither), keeping the test hermetic and fast -- otherwise the final diff
    artifact step would invoke ``git status`` in a non-repo temp dir.
    """
    return {
        "title": title,
        "objective": objective,
        "scope_folder": str(scope),
        "allow_git": False,
        "allow_shell": False,
    }


def test_agent_is_registered_as_streaming():
    assert "brain.agent.run" in handlers.HANDLERS
    assert "brain.agent.run" in handlers.STREAMING
    assert "brain.agent.history.list" in handlers.HANDLERS
    assert "brain.agent.history.read" in handlers.HANDLERS
    assert "brain.agent.history.retry_spec" in handlers.HANDLERS
    assert "brain.agent.history.continue_spec" in handlers.HANDLERS
    assert "brain.agent.last_spec.read" in handlers.HANDLERS
    assert "brain.agent.last_spec.write" in handlers.HANDLERS
    assert "brain.agent.approval.respond" in handlers.HANDLERS


def test_agent_requires_spec_dict():
    events = []
    from wisp_brain.handlers import StreamContext

    ctx = StreamContext(lambda e, d, r: events.append((e, d)), 1)
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.agent.run"](ctx, spec=None)


def test_agent_fake_llm_completes_in_one_turn(record_ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    events, ctx = record_ctx()
    spec = _noop_spec(tmp_path, title="smoke", objective="do nothing")
    result = handlers.HANDLERS["brain.agent.run"](
        ctx, spec=spec, log_root=str(tmp_path / "runs")
    )

    assert "Fake agent run complete." in result["final"]
    assert result["error"] == ""
    assert result["cancelled"] is False

    run_dir = Path(result["run_dir"])
    assert run_dir.is_dir()
    assert (run_dir / "final.md").read_text(encoding="utf-8") == result["final"]

    # Progress streamed as agent.log, and a terminal agent.done with the result.
    assert _events_of(events, "agent.log"), "expected streamed agent.log lines"
    assert _events_of(events, "agent.done") == [result]
    last_task = tmp_path / "runs" / "last_task.json"
    assert json.loads(last_task.read_text(encoding="utf-8"))["title"] == "smoke"


def test_agent_runs_a_scripted_model(record_ctx, tmp_path, monkeypatch):
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            [{"thought": "plan", "final": "Scripted final report.", "tool_calls": []}]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WISP_BRAIN_AGENT_TEST_SCRIPT", str(script))

    events, ctx = record_ctx()
    spec = _noop_spec(tmp_path, title="scripted", objective="report")
    result = handlers.HANDLERS["brain.agent.run"](
        ctx, spec=spec, log_root=str(tmp_path / "runs")
    )

    assert result["final"] == "Scripted final report."
    assert result["error"] == ""
    assert _events_of(events, "agent.done") == [result]


def test_agent_history_lists_recent_runs(tmp_path):
    root = tmp_path / "runs"
    old = root / "20260101-010101-old"
    new = root / "20260102-010101-new"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "task.json").write_text(json.dumps({"title": "Old", "objective": "Earlier"}), encoding="utf-8")
    (old / "final.md").write_text("Done old", encoding="utf-8")
    (new / "task.json").write_text(json.dumps({"title": "New", "objective": "Later"}), encoding="utf-8")
    (new / "error.txt").write_text("boom", encoding="utf-8")

    result = handlers.HANDLERS["brain.agent.history.list"](log_root=str(root))

    assert result["runs_root"] == str(root)
    assert [run["title"] for run in result["runs"]] == ["New", "Old"]
    assert result["runs"][0]["status"] == "failed"
    assert result["runs"][1]["status"] == "complete"


def test_agent_history_lists_previous_runtime_runs_after_restart(tmp_path, monkeypatch):
    current_runtime = tmp_path / "build_logs" / "wisp_runtime_20260102-010101"
    old_root = tmp_path / "build_logs" / "wisp_runtime_20260101-010101" / "agent-runs"
    cancelled = old_root / "20260101-020202-cancelled"
    cancelled.mkdir(parents=True)
    (cancelled / "task.json").write_text(
        json.dumps({"title": "Cancelled", "objective": "Stopped before restart"}),
        encoding="utf-8",
    )
    (cancelled / "run.log").write_text("[00:00:00] agent run cancelled", encoding="utf-8")

    import core.system.paths as system_paths

    monkeypatch.setattr(handlers, "_runtime_output_dir", lambda: current_runtime)
    monkeypatch.setattr(system_paths, "AGENT_RUNS_DIR", tmp_path / "persistent" / "agent_runs")

    result = handlers.HANDLERS["brain.agent.history.list"]()

    by_title = {run["title"]: run for run in result["runs"]}
    assert "Cancelled" in by_title
    assert by_title["Cancelled"]["status"] == "cancelled"
    assert str(old_root) in result["runs_roots"]


def test_agent_history_reads_run_artifacts(tmp_path):
    run = tmp_path / "runs" / "20260101-010101-demo"
    run.mkdir(parents=True)
    (run / "task.json").write_text(json.dumps({"title": "Demo", "objective": "Inspect"}), encoding="utf-8")
    (run / "final.md").write_text("Final report", encoding="utf-8")
    (run / "run.log").write_text("[00:00:00] agent run finished", encoding="utf-8")
    (run / "diff.patch").write_text("diff --git a/a b/a", encoding="utf-8")

    result = handlers.HANDLERS["brain.agent.history.read"](run_dir=str(run))

    assert result["title"] == "Demo"
    assert result["objective"] == "Inspect"
    assert result["status"] == "complete"
    assert result["final"] == "Final report"
    assert "agent run finished" in result["run_log"]
    assert result["diff_patch"].startswith("diff --git")


def test_agent_history_retry_and_continue_specs(tmp_path):
    run = tmp_path / "runs" / "20260101-010101-demo"
    run.mkdir(parents=True)
    (run / "task.json").write_text(
        json.dumps({
            "title": "Demo",
            "objective": "Inspect",
            "scope_folder": str(tmp_path),
            "allow_git": False,
            "allow_shell": False,
        }),
        encoding="utf-8",
    )
    (run / "final.md").write_text("Final report", encoding="utf-8")
    (run / "run.log").write_text("[00:00:00] agent turn 1/1: Agent", encoding="utf-8")

    retry = handlers.HANDLERS["brain.agent.history.retry_spec"](run_dir=str(run))["spec"]
    continued = handlers.HANDLERS["brain.agent.history.continue_spec"](run_dir=str(run))["spec"]

    assert retry["title"] == "Demo"
    assert retry["objective"] == "Inspect"
    assert continued["title"] == "Continue: Demo"
    assert "Continuing from previous agent run" in continued["required_context"]
    assert "Previous final report" in continued["required_context"]


def test_agent_last_spec_write_and_read_round_trips(tmp_path):
    spec = _noop_spec(tmp_path, title="Last Task", objective="Copy this task")

    written = handlers.HANDLERS["brain.agent.last_spec.write"](spec=spec, log_root=str(tmp_path / "runs"))
    read = handlers.HANDLERS["brain.agent.last_spec.read"](log_root=str(tmp_path / "runs"))

    assert written["ok"] is True
    assert Path(written["path"]).name == "last_task.json"
    assert written["spec"]["title"] == "Last Task"
    assert read["spec"]["title"] == "Last Task"
    assert read["spec"]["objective"] == "Copy this task"


def test_agent_last_spec_read_falls_back_to_newest_run_task(tmp_path):
    root = tmp_path / "runs"
    old = root / "20260101-010101-old"
    new = root / "20260102-010101-new"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "task.json").write_text(json.dumps(_noop_spec(tmp_path, title="Old", objective="Earlier")), encoding="utf-8")
    (new / "task.json").write_text(json.dumps(_noop_spec(tmp_path, title="New", objective="Later")), encoding="utf-8")
    os.utime(old / "task.json", (1, 1))
    os.utime(new / "task.json", (2, 2))

    result = handlers.HANDLERS["brain.agent.last_spec.read"](log_root=str(root))

    assert result["spec"]["title"] == "New"
    assert result["spec"]["objective"] == "Later"


def test_agent_last_spec_read_scans_previous_runtime_roots(tmp_path, monkeypatch):
    current_runtime = tmp_path / "build_logs" / "wisp_runtime_20260102-010101"
    old_root = tmp_path / "build_logs" / "wisp_runtime_20260101-010101" / "agent-runs"
    previous = old_root / "20260101-020202-previous"
    previous.mkdir(parents=True)
    (previous / "task.json").write_text(
        json.dumps(_noop_spec(tmp_path, title="Previous", objective="Restore me")),
        encoding="utf-8",
    )

    import core.system.paths as system_paths

    monkeypatch.setattr(handlers, "_runtime_output_dir", lambda: current_runtime)
    monkeypatch.setattr(system_paths, "AGENT_RUNS_DIR", tmp_path / "persistent" / "agent_runs")

    result = handlers.HANDLERS["brain.agent.last_spec.read"]()

    assert result["spec"]["title"] == "Previous"
    assert result["spec"]["objective"] == "Restore me"


def test_agent_approval_callback_emits_request_and_accepts_response(record_ctx):
    events, ctx = record_ctx()
    approved: dict[str, bool] = {}

    def wait_for_approval():
        approved["value"] = handlers._agent_approval_callback(ctx)({
            "action": "write_file",
            "details": {"path": "note.txt", "chars": 5},
        })

    thread = threading.Thread(target=wait_for_approval)
    thread.start()
    for _ in range(50):
        if _events_of(events, "agent.approval.request"):
            break
        time.sleep(0.02)

    requests = _events_of(events, "agent.approval.request")
    assert requests
    assert requests[0]["action"] == "write_file"
    assert requests[0]["details"]["path"] == "note.txt"

    response = handlers.HANDLERS["brain.agent.approval.respond"](
        approval_id=requests[0]["approval_id"],
        approved=True,
    )
    thread.join(timeout=2)

    assert response == {"ok": True, "approved": True}
    assert not thread.is_alive()
    assert approved["value"] is True


def test_agent_run_approval_request_can_be_approved(record_ctx, tmp_path, monkeypatch):
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            [
                {
                    "thought": "Create note.",
                    "status": "continue",
                    "tool_calls": [
                        {
                            "tool": "create_file",
                            "args": {"path": "note.txt", "content": "hello"},
                        }
                    ],
                    "final": None,
                },
                {"thought": "Done.", "tool_calls": [], "final": "Created note."},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WISP_BRAIN_AGENT_TEST_SCRIPT", str(script))

    events = []

    def emit(event, data, _req_id):
        events.append((event, data))
        if event == "agent.approval.request":
            handlers.HANDLERS["brain.agent.approval.respond"](
                approval_id=data["approval_id"],
                approved=True,
            )

    from wisp_brain.handlers import StreamContext

    ctx = StreamContext(emit, 1)
    spec = _noop_spec(tmp_path, title="approval run", objective="create a note")
    spec.update(
        {
            "parallel_read_only_briefing": False,
            "allow_file_create": True,
            "file_create_permission_mode": "ask permission",
            "agents": [{"name": "Builder", "role": "Implementer"}],
        }
    )

    result = handlers.HANDLERS["brain.agent.run"](
        ctx, spec=spec, log_root=str(tmp_path / "runs")
    )

    assert result["error"] == ""
    assert result["final"] == "Created note."
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello"
    approvals = _events_of(events, "agent.approval.request")
    assert approvals
    assert approvals[0]["action"] == "create_file"
