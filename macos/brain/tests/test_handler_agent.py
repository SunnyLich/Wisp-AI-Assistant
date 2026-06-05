"""Unit tests for the ``brain.agent.run`` handler.

The agent loop is driven by a scripted model callback (no provider/network), so
these exercise the *real* ``core.agent`` runner end-to-end: spec deserialization,
the run-loop, log/trace event streaming, and the final-report result. Two seams
are covered -- the single-turn ``WISP_BRAIN_FAKE_LLM`` fast path and an explicit
``WISP_BRAIN_AGENT_TEST_SCRIPT`` of model turns.
"""
from __future__ import annotations

import json
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
