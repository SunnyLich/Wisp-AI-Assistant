"""Tests for Codex and Claude Code local-history synchronization."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.conversation_store import external_sync
from core.conversation_store.external_sync import (
    apply_external_conversations,
    discover_external_conversations,
    export_conversation_as_new_session,
    parse_claude_session,
    parse_codex_session,
    pending_external_push_count,
    push_conversation_to_source,
    sync_external_conversations,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def test_external_path_and_format_changes_are_rejected_without_mutation(tmp_path):
    source_root = tmp_path / ".codex"
    path = source_root / "sessions" / "session.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"id": "changed"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Original"}},
        ],
    )
    conversation = parse_codex_session(path)
    assert conversation is not None
    conversation["messages"].append({"role": "assistant", "content": "Pending"})
    path.unlink()

    with pytest.raises(ValueError, match="external history path"):
        push_conversation_to_source(conversation, source_root=source_root)
    assert conversation["external_source"]["message_count"] == 1

    changed_format = source_root / "sessions" / "changed-format.jsonl"
    _write_jsonl(changed_format, [{"unexpected_provider_schema": True}])
    assert parse_codex_session(changed_format) is None


@pytest.mark.parametrize("failure_stage", ["locked", "backup"])
def test_external_locked_file_and_backup_failures_preserve_source(
    tmp_path, monkeypatch, failure_stage
):
    source_root = tmp_path / ".codex"
    path = source_root / "sessions" / "session.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"id": "safe-push"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Original"}},
        ],
    )
    original = path.read_bytes()
    conversation = parse_codex_session(path)
    assert conversation is not None
    conversation["messages"].append({"role": "assistant", "content": "Pending"})
    if failure_stage == "locked":
        monkeypatch.setattr(
            external_sync,
            "_append_jsonl",
            lambda *_args: (_ for _ in ()).throw(PermissionError("file is locked")),
        )
    else:
        monkeypatch.setattr(
            external_sync.shutil,
            "copy2",
            lambda *_args: (_ for _ in ()).throw(OSError("backup failed")),
        )

    with pytest.raises((PermissionError, OSError)):
        push_conversation_to_source(
            conversation,
            backup_dir=tmp_path / "backups",
            source_root=source_root,
        )

    assert path.read_bytes() == original
    assert conversation["external_source"]["message_count"] == 1


def test_parse_codex_uses_user_and_final_messages_only(tmp_path):
    path = tmp_path / "sessions" / "rollout-11111111-1111-1111-1111-111111111111.jsonl"
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "type": "session_meta",
                "payload": {"id": "codex-1", "cwd": "/repo"},
            },
            {
                "timestamp": "2026-01-01T00:00:01Z",
                "type": "response_item",
                "payload": {"type": "message", "role": "developer", "content": [{"text": "secret"}]},
            },
            {
                "timestamp": "2026-01-01T00:00:02Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "Fix the tests"},
            },
            {
                "timestamp": "2026-01-01T00:00:03Z",
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "commentary", "message": "Working"},
            },
            {
                "timestamp": "2026-01-01T00:00:04Z",
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "final_answer", "message": "Tests fixed"},
            },
        ],
    )

    conversation = parse_codex_session(path)

    assert conversation is not None
    assert conversation["external_source"]["session_id"] == "codex-1"
    assert conversation["external_source"]["cwd"] == "/repo"
    assert [(m["role"], m["content"]) for m in conversation["messages"]] == [
        ("user", "Fix the tests"),
        ("assistant", "Tests fixed"),
    ]


def test_parse_claude_follows_active_chain_and_ignores_tools(tmp_path):
    path = tmp_path / "projects" / "repo" / "claude-1.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "custom-title", "customTitle": "Refactor parser", "sessionId": "claude-1"},
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "sessionId": "claude-1",
                "cwd": "/repo",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "Refactor this"},
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "parentUuid": "u1",
                "timestamp": "2026-01-01T00:00:01Z",
                "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hidden"}]},
            },
            {
                "type": "assistant",
                "uuid": "a2",
                "parentUuid": "a1",
                "timestamp": "2026-01-01T00:00:02Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Done"}]},
            },
            {
                "type": "user",
                "uuid": "tool1",
                "parentUuid": "a2",
                "timestamp": "2026-01-01T00:00:03Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "private"}]},
            },
            {
                "type": "assistant",
                "uuid": "branch",
                "parentUuid": "u1",
                "timestamp": "2026-01-01T00:00:04Z",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "sidechain"}]},
            },
        ],
    )

    conversation = parse_claude_session(path)

    assert conversation is not None
    assert conversation["title"] == "Refactor parser"
    assert [(m["role"], m["content"]) for m in conversation["messages"]] == [
        ("user", "Refactor this"),
        ("assistant", "Done"),
    ]


def test_sync_updates_in_place_and_preserves_wisp_tail(tmp_path):
    codex_home = tmp_path / ".codex"
    claude_home = tmp_path / ".claude"
    path = codex_home / "sessions" / "2026" / "session.jsonl"
    records = [
        {"type": "session_meta", "timestamp": "2026-01-01T00:00:00Z", "payload": {"id": "s1"}},
        {
            "type": "event_msg",
            "timestamp": "2026-01-01T00:00:01Z",
            "payload": {"type": "user_message", "message": "Question"},
        },
    ]
    _write_jsonl(path, records)
    conversations: list[dict] = []

    first = sync_external_conversations(conversations, codex_home=codex_home, claude_home=claude_home)
    conversations[0]["messages"].append({"role": "user", "content": "Wisp follow-up"})
    records.append(
        {
            "type": "event_msg",
            "timestamp": "2026-01-01T00:00:02Z",
            "payload": {"type": "agent_message", "phase": "final_answer", "message": "Answer"},
        }
    )
    _write_jsonl(path, records)
    second = sync_external_conversations(conversations, codex_home=codex_home, claude_home=claude_home)

    assert first.imported == 1
    assert second.updated == 1
    assert len(conversations) == 1
    assert [message["content"] for message in conversations[0]["messages"]] == [
        "Question",
        "Answer",
        "Wisp follow-up",
    ]


def test_discovery_does_not_mutate_until_applied(tmp_path):
    codex_home = tmp_path / ".codex"
    claude_home = tmp_path / ".claude"
    _write_jsonl(
        codex_home / "sessions" / "session.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "s2"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Hello"}},
        ],
    )
    conversations: list[dict] = []

    discovered, report = discover_external_conversations(
        codex_home=codex_home,
        claude_home=claude_home,
    )

    assert conversations == []
    assert len(discovered) == 1
    applied = apply_external_conversations(conversations, discovered, report=report)
    assert applied.imported == 1
    assert conversations[0]["messages"][0]["content"] == "Hello"


def test_push_codex_turns_backs_up_and_round_trips(tmp_path):
    source_root = tmp_path / ".codex"
    path = source_root / "sessions" / "session.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"id": "push-codex", "cwd": "/repo"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Original"}},
        ],
    )
    conversation = parse_codex_session(path)
    assert conversation is not None
    conversation["messages"].extend(
        [
            {"role": "assistant", "content": "Wisp answer", "created_at": "2026-01-01T00:00:01Z"},
            {"role": "user", "content": "Wisp follow-up", "created_at": "2026-01-01T00:00:02Z"},
        ]
    )

    report = push_conversation_to_source(
        conversation,
        backup_dir=tmp_path / "backups",
        source_root=source_root,
    )
    reparsed = parse_codex_session(path)

    assert report.pushed == 2
    assert report.backup_path is not None and report.backup_path.is_file()
    assert pending_external_push_count(conversation) == 0
    assert reparsed is not None
    assert [message["content"] for message in reparsed["messages"]] == [
        "Original",
        "Wisp answer",
        "Wisp follow-up",
    ]


def test_push_claude_turns_preserves_parent_chain(tmp_path):
    source_root = tmp_path / ".claude"
    path = source_root / "projects" / "repo" / "claude-push.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "sessionId": "claude-push",
                "cwd": "/repo",
                "message": {"role": "user", "content": "Original"},
            }
        ],
    )
    conversation = parse_claude_session(path)
    assert conversation is not None
    conversation["messages"].append({"role": "assistant", "content": "Wisp answer"})

    report = push_conversation_to_source(
        conversation,
        backup_dir=tmp_path / "backups",
        source_root=source_root,
    )
    reparsed = parse_claude_session(path)

    assert report.pushed == 1
    assert reparsed is not None
    assert [message["content"] for message in reparsed["messages"]] == ["Original", "Wisp answer"]


def test_push_rejects_source_outside_provider_root(tmp_path):
    path = tmp_path / "outside.jsonl"
    _write_jsonl(path, [{"type": "session_meta", "payload": {"id": "outside"}}])
    conversation = {
        "messages": [{"role": "user", "content": "new"}],
        "external_source": {
            "provider": "codex",
            "session_id": "outside",
            "path": str(path),
            "message_count": 0,
        },
    }

    try:
        push_conversation_to_source(conversation, source_root=tmp_path / "allowed")
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("unsafe external source path was accepted")


def test_export_wisp_native_conversation_as_new_codex_session(tmp_path):
    conversation = {
        "title": "Native Wisp chat",
        "messages": [
            {"role": "user", "content": "Original question", "created_at": "2026-01-01T00:00:00Z"},
            {"role": "assistant", "content": "Wisp answer", "created_at": "2026-01-01T00:00:01Z"},
        ],
    }
    codex_home = tmp_path / ".codex"

    report = export_conversation_as_new_session(
        conversation,
        "codex",
        cwd=tmp_path,
        codex_home=codex_home,
    )
    reparsed = parse_codex_session(report.path)

    assert report.exported == 2
    assert report.path.is_file()
    assert report.path.is_relative_to(codex_home / "sessions")
    assert conversation["external_source"]["session_id"] == report.session_id
    assert pending_external_push_count(conversation) == 0
    assert reparsed is not None
    assert [message["content"] for message in reparsed["messages"]] == [
        "Original question",
        "Wisp answer",
    ]


def test_export_wisp_native_conversation_as_new_claude_session(tmp_path):
    conversation = {
        "title_override": "Native title",
        "messages": [
            {"role": "user", "content": "Original question"},
            {"role": "assistant", "content": "Wisp answer"},
        ],
    }
    claude_home = tmp_path / ".claude"

    report = export_conversation_as_new_session(
        conversation,
        "claude",
        cwd=Path.cwd(),
        claude_home=claude_home,
    )
    reparsed = parse_claude_session(report.path)

    assert report.exported == 2
    assert report.path.is_relative_to(claude_home / "projects")
    assert reparsed is not None
    assert reparsed["title"] == "Native title"
    assert [message["content"] for message in reparsed["messages"]] == [
        "Original question",
        "Wisp answer",
    ]
    with pytest.raises(ValueError, match="already linked"):
        export_conversation_as_new_session(
            conversation,
            "codex",
            cwd=Path.cwd(),
            codex_home=tmp_path / "other-codex",
        )


def test_export_rejects_empty_conversation(tmp_path):
    with pytest.raises(ValueError, match="no messages"):
        export_conversation_as_new_session(
            {"messages": []},
            "codex",
            cwd=tmp_path,
            codex_home=tmp_path / ".codex",
        )
