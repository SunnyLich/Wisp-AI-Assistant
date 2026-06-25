from __future__ import annotations

import json

from core.llm_clients import client


def test_planned_chunking_emits_visible_parts_from_hidden_plan(monkeypatch):
    calls: list[str] = []
    plan = {
        "answer_goal": "explain the idea",
        "chunk_goals": ["open", "tradeoffs", "close"],
        "finish_style": "decisive",
    }
    responses = [
        json.dumps(plan),
        "First stable part.",
        "Second stable part.",
        "Final stable part.",
    ]

    def fake_stream_response(prompt: str, **_kwargs):
        calls.append(prompt)
        yield responses.pop(0)

    monkeypatch.setattr(client, "stream_response", fake_stream_response)

    chunks = list(
        client.stream_planned_chunk_response(
            "Should we fake streaming with small turns?",
            chunks=3,
        )
    )

    assert chunks == ["First stable part.", " Second stable part.", " Final stable part."]
    assert len(calls) == 4
    assert "Hidden answer plan" in calls[1]
    assert "Previously visible answer" in calls[2]


def test_planned_chunking_falls_back_when_planner_is_invalid(monkeypatch):
    def fake_stream_response(*_args, **_kwargs):
        yield "not json"

    monkeypatch.setattr(client, "stream_response", fake_stream_response)

    chunks = list(
        client.stream_planned_chunk_response(
            "Explain planned chunking.",
            fallback_stream=lambda: iter(["fallback answer"]),
        )
    )

    assert chunks == ["fallback answer"]


def test_planned_chunking_finishes_when_later_chunk_is_empty(monkeypatch):
    plan = {
        "answer_goal": "explain the idea",
        "chunk_goals": ["open", "tradeoffs", "close"],
        "finish_style": "decisive",
    }
    responses = [json.dumps(plan), "First part.", "", "Finished from the first part."]

    def fake_stream_response(*_args, **_kwargs):
        yield responses.pop(0)

    monkeypatch.setattr(client, "stream_response", fake_stream_response)

    chunks = list(client.stream_planned_chunk_response("Explain planned chunking.", chunks=3))

    assert chunks == ["First part.", " Finished from the first part."]


def test_planned_chunking_streams_visible_part_tokens(monkeypatch):
    plan = {
        "answer_goal": "explain the idea",
        "chunk_goals": ["open", "tradeoffs"],
        "finish_style": "decisive",
    }
    responses = [
        [json.dumps(plan)],
        ["First ", "part."],
        ["Second ", "part."],
    ]

    def fake_stream_response(*_args, **_kwargs):
        yield from responses.pop(0)

    monkeypatch.setattr(client, "stream_response", fake_stream_response)

    chunks = list(client.stream_planned_chunk_response("Explain planned chunking.", chunks=2))

    assert chunks == ["First ", "part.", " Second ", "part."]
