"""Local Codex and Claude Code conversation pull plus guarded append sync."""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from core.conversation_store.store import GENERAL_PROJECT_ID
from core.system.paths import CHATS_DIR

_CODEX_ID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f-]{27,})", re.IGNORECASE)
_CLAUDE_NOISE_BLOCK_RE = re.compile(
    r"<(system-reminder|local-command-caveat|command-name|command-message|"
    r"local-command-stdout|ide_opened_file|task-notification)>.*?</\1>\s*",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class SyncReport:
    """Counts from one external-history pull."""

    imported: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        """Return the number of Wisp conversations changed by the pull."""
        return self.imported + self.updated


@dataclass
class PushReport:
    """Result of appending Wisp-only turns to one external transcript."""

    provider: str
    pushed: int
    backup_path: Path | None = None


@dataclass
class ExportReport:
    """Result of exporting a Wisp-native conversation as a new provider session."""

    provider: str
    session_id: str
    path: Path
    exported: int


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_codex_home() -> Path:
    configured = str(os.environ.get("CODEX_HOME") or "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _default_claude_home() -> Path:
    configured = str(os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def _io_path(path: Path) -> Path:
    """Return an extended Windows path when a transcript exceeds MAX_PATH."""
    absolute = Path(path).expanduser().absolute()
    text = str(absolute)
    if os.name != "nt" or text.startswith("\\\\?\\") or len(text) < 240:
        return absolute
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text.lstrip("\\"))
    return Path("\\\\?\\" + text)


def _read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with _io_path(path).open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def _text(value: object) -> str:
    return str(value or "").strip()


def _append_message(messages: list[dict], role: str, text: str, timestamp: str, message_id: str) -> None:
    content = _text(text)
    if role not in {"user", "assistant"} or not content:
        return
    if messages and messages[-1].get("role") == role:
        previous = _text(messages[-1].get("content"))
        if content != previous:
            messages[-1]["content"] = f"{previous}\n\n{content}" if previous else content
        return
    messages.append(
        {
            "id": message_id or str(uuid.uuid4()),
            "role": role,
            "content": content,
            "created_at": timestamp or _now_iso(),
        }
    )


def _derived_title(messages: list[dict], fallback: str) -> str:
    for message in messages:
        if message.get("role") == "user" and _text(message.get("content")):
            compact = " ".join(_text(message.get("content")).split())
            return compact[:72] + ("…" if len(compact) > 72 else "")
    return fallback


def _file_signature(path: Path) -> str:
    stat = _io_path(path).stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _file_timestamp(path: Path) -> str:
    return datetime.fromtimestamp(_io_path(path).stat().st_mtime, UTC).isoformat()


def _conversation(
    *,
    provider: str,
    session_id: str,
    path: Path,
    title: str,
    messages: list[dict],
    cwd: str = "",
) -> dict:
    fallback_time = _file_timestamp(path)
    created_at = _text(messages[0].get("created_at")) if messages else fallback_time
    updated_at = _text(messages[-1].get("created_at")) if messages else fallback_time
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"wisp:{provider}:{session_id}")),
        "project_id": GENERAL_PROJECT_ID,
        "title": title or _derived_title(messages, f"{provider.title()} conversation"),
        "title_override": "",
        "pinned": False,
        "messages": messages,
        "context": "",
        "file_context": [],
        "tool_context": {},
        "context_policy": {},
        "created_at": created_at or fallback_time,
        "updated_at": updated_at or fallback_time,
        "external_source": {
            "provider": provider,
            "session_id": session_id,
            "path": str(path),
            "signature": _file_signature(path),
            "message_count": len(messages),
            "cwd": cwd,
            "source_updated_at": fallback_time,
            "synced_at": _now_iso(),
        },
    }


def parse_codex_session(path: Path) -> dict | None:
    """Convert one Codex session transcript into a Wisp conversation."""
    records = _read_jsonl(path)
    session_id = ""
    cwd = ""
    messages: list[dict] = []
    for index, record in enumerate(records):
        outer_type = _text(record.get("type"))
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        timestamp = _text(record.get("timestamp") or payload.get("timestamp"))
        if outer_type == "session_meta":
            session_id = _text(payload.get("id") or payload.get("session_id")) or session_id
            cwd = _text(payload.get("cwd")) or cwd
            continue
        if outer_type != "event_msg":
            continue
        event_type = _text(payload.get("type"))
        if event_type == "user_message":
            _append_message(
                messages,
                "user",
                _text(payload.get("message")),
                timestamp,
                f"codex-{session_id or path.stem}-{index}",
            )
        elif event_type == "agent_message" and _text(payload.get("phase")) == "final_answer":
            _append_message(
                messages,
                "assistant",
                _text(payload.get("message")),
                timestamp,
                f"codex-{session_id or path.stem}-{index}",
            )
    if not session_id:
        match = _CODEX_ID_RE.search(path.stem)
        session_id = match.group(1) if match else path.stem
    if not messages:
        return None
    return _conversation(
        provider="codex",
        session_id=session_id,
        path=path,
        title=_derived_title(messages, "ChatGPT conversation"),
        messages=messages,
        cwd=cwd,
    )


def _claude_content_text(content: object, *, role: str) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict) or _text(block.get("type")) != "text":
                continue
            parts.append(_text(block.get("text")))
        text = "\n\n".join(part for part in parts if part)
    else:
        return ""
    if role == "user":
        text = _CLAUDE_NOISE_BLOCK_RE.sub("", text).strip()
        if text.startswith(("<system-reminder>", "<local-command-", "<command-")):
            return ""
    return text.strip()


def _claude_main_chain(records: list[dict]) -> list[dict]:
    """Follow Claude's parent links so rewound/abandoned branches are not imported."""
    by_id = {
        _text(record.get("uuid")): record
        for record in records
        if _text(record.get("uuid")) and not bool(record.get("isSidechain"))
    }
    candidates = [
        record
        for record in records
        if _text(record.get("type")) in {"user", "assistant"}
        and _text(record.get("uuid")) in by_id
        and not bool(record.get("isSidechain"))
    ]
    if not candidates:
        return []
    chain: list[dict] = []
    current = candidates[-1]
    seen: set[str] = set()
    while current:
        record_id = _text(current.get("uuid"))
        if not record_id or record_id in seen:
            break
        seen.add(record_id)
        chain.append(current)
        current = by_id.get(_text(current.get("parentUuid")))
    chain.reverse()
    return chain


def parse_claude_session(path: Path) -> dict | None:
    """Convert one Claude Code project transcript into a Wisp conversation."""
    records = _read_jsonl(path)
    session_id = ""
    cwd = ""
    custom_title = ""
    ai_title = ""
    for record in records:
        session_id = _text(record.get("sessionId")) or session_id
        cwd = _text(record.get("cwd")) or cwd
        record_type = _text(record.get("type"))
        if record_type == "custom-title":
            custom_title = _text(record.get("customTitle")) or custom_title
        elif record_type == "ai-title":
            ai_title = _text(record.get("aiTitle")) or ai_title
    session_id = session_id or path.stem

    messages: list[dict] = []
    for record in _claude_main_chain(records):
        role = _text(record.get("type"))
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        content = _claude_content_text(message.get("content"), role=role)
        _append_message(
            messages,
            role,
            content,
            _text(record.get("timestamp")),
            _text(record.get("uuid")) or f"claude-{session_id}-{len(messages)}",
        )
    if not messages:
        return None
    return _conversation(
        provider="claude",
        session_id=session_id,
        path=path,
        title=custom_title or ai_title or _derived_title(messages, "Claude conversation"),
        messages=messages,
        cwd=cwd,
    )


def _session_files(codex_home: Path, claude_home: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for directory in (codex_home / "sessions", codex_home / "archived_sessions"):
        if directory.is_dir():
            files.extend(("codex", path) for path in directory.rglob("*.jsonl") if path.is_file())
    projects = claude_home / "projects"
    if projects.is_dir():
        for path in projects.rglob("*.jsonl"):
            try:
                relative_parts = path.relative_to(projects).parts
            except ValueError:
                continue
            if path.is_file() and "subagents" not in relative_parts:
                files.append(("claude", path))
    return files


def _source_key(conversation: dict) -> str:
    source = conversation.get("external_source")
    if not isinstance(source, dict):
        return ""
    provider = _text(source.get("provider"))
    session_id = _text(source.get("session_id"))
    return f"{provider}:{session_id}" if provider and session_id else ""


def pending_external_push_count(conversation: dict) -> int:
    """Return the number of Wisp-only turns after the imported source prefix."""
    source = conversation.get("external_source")
    if not isinstance(source, dict):
        return 0
    imported_count = source.get("message_count", 0)
    if not isinstance(imported_count, int) or imported_count < 0:
        return 0
    messages = conversation.get("messages") if isinstance(conversation.get("messages"), list) else []
    return max(0, len(messages) - imported_count)


def _allowed_source_path(path: Path, provider: str, source_root: Path | None) -> bool:
    resolved = _io_path(path).resolve(strict=True)
    if source_root is not None:
        roots = [_io_path(source_root).resolve()]
    elif provider == "codex":
        home = _default_codex_home()
        roots = [_io_path(home / "sessions").resolve(), _io_path(home / "archived_sessions").resolve()]
    elif provider == "claude":
        roots = [_io_path(_default_claude_home() / "projects").resolve()]
    else:
        return False
    return any(resolved.is_relative_to(root) for root in roots)


def _codex_append_records(messages: list[dict]) -> list[dict]:
    records: list[dict] = []
    for message in messages:
        role = _text(message.get("role"))
        content = _text(message.get("content"))
        if role not in {"user", "assistant"} or not content:
            continue
        timestamp = _text(message.get("created_at")) or _now_iso()
        content_type = "input_text" if role == "user" else "output_text"
        records.append(
            {
                "timestamp": timestamp,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": role,
                    "content": [{"type": content_type, "text": content}],
                },
            }
        )
        payload = (
            {
                "type": "user_message",
                "message": content,
                "images": [],
                "local_images": [],
                "text_elements": [],
            }
            if role == "user"
            else {"type": "agent_message", "message": content, "phase": "final_answer"}
        )
        records.append({"timestamp": timestamp, "type": "event_msg", "payload": payload})
    return records


def _claude_message_records(messages: list[dict], source: dict, existing: list[dict]) -> list[dict]:
    """Build Claude records linked to the end of an existing (or new) session."""
    parent_uuid = ""
    template: dict = {}
    assistant_message: dict = {}
    for record in reversed(existing):
        if not parent_uuid and _text(record.get("uuid")):
            parent_uuid = _text(record.get("uuid"))
        if not template and _text(record.get("type")) in {"user", "assistant"}:
            template = record
        if not assistant_message and _text(record.get("type")) == "assistant":
            raw_message = record.get("message")
            if isinstance(raw_message, dict):
                assistant_message = raw_message
        if parent_uuid and template and assistant_message:
            break

    session_id = (
        _text(source.get("session_id"))
        or _text(template.get("sessionId"))
        or "wisp-session"
    )
    cwd = _text(source.get("cwd") or template.get("cwd"))
    records: list[dict] = []
    for message in messages:
        role = _text(message.get("role"))
        content = _text(message.get("content"))
        if role not in {"user", "assistant"} or not content:
            continue
        record_uuid = str(uuid.uuid4())
        timestamp = _text(message.get("created_at")) or _now_iso()
        record = {
            "parentUuid": parent_uuid or None,
            "isSidechain": False,
            "type": role,
            "uuid": record_uuid,
            "timestamp": timestamp,
            "sessionId": session_id,
            "cwd": cwd,
            "userType": _text(template.get("userType")) or "external",
        }
        for key in ("version", "gitBranch", "permissionMode", "entrypoint", "promptSource"):
            if template.get(key) not in (None, ""):
                record[key] = template[key]
        if role == "user":
            record["message"] = {"role": "user", "content": content}
        else:
            usage = assistant_message.get("usage")
            record["requestId"] = f"wisp-{uuid.uuid4()}"
            record["message"] = {
                "id": f"msg_wisp_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "model": _text(assistant_message.get("model")) or "wisp-import",
                "content": [{"type": "text", "text": content}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": usage if isinstance(usage, dict) else {"input_tokens": 0, "output_tokens": 0},
            }
        records.append(record)
        parent_uuid = record_uuid
    return records


def _claude_append_records(path: Path, messages: list[dict], source: dict) -> list[dict]:
    return _claude_message_records(messages, source, _read_jsonl(path))


def _append_jsonl(path: Path, records: list[dict]) -> None:
    if not records:
        return
    needs_newline = False
    io_path = _io_path(path)
    if io_path.stat().st_size:
        with io_path.open("rb") as handle:
            handle.seek(-1, os.SEEK_END)
            needs_newline = handle.read(1) not in {b"\n", b"\r"}
    with io_path.open("a", encoding="utf-8", newline="\n") as handle:
        if needs_newline:
            handle.write("\n")
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_new_jsonl(path: Path, records: list[dict]) -> None:
    """Create one new transcript exclusively; never replace an existing session."""
    if not records:
        raise ValueError("cannot create an empty external conversation")
    io_path = _io_path(path)
    io_path.parent.mkdir(parents=True, exist_ok=True)
    with io_path.open("x", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _exportable_messages(conversation: dict) -> list[dict]:
    raw_messages = conversation.get("messages") if isinstance(conversation.get("messages"), list) else []
    return [
        message
        for message in raw_messages
        if isinstance(message, dict)
        and _text(message.get("role")) in {"user", "assistant"}
        and _text(message.get("content"))
    ]


def _conversation_export_title(conversation: dict, messages: list[dict]) -> str:
    return (
        _text(conversation.get("title_override"))
        or _text(conversation.get("title"))
        or _derived_title(messages, "Wisp conversation")
    )


def _latest_codex_cli_version(codex_home: Path) -> str:
    sessions = codex_home / "sessions"
    if not sessions.is_dir():
        return "0.0.0"
    try:
        candidates = sorted(
            (path for path in sessions.rglob("*.jsonl") if path.is_file()),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
    except OSError:
        return "0.0.0"
    for path in candidates[:10]:
        try:
            first = _read_jsonl(path)[:1]
        except OSError:
            continue
        if first and isinstance(first[0].get("payload"), dict):
            version = _text(first[0]["payload"].get("cli_version"))
            if version:
                return version
    return "0.0.0"


def _codex_new_session_records(
    messages: list[dict],
    *,
    session_id: str,
    cwd: str,
    codex_home: Path,
) -> list[dict]:
    timestamp = _now_iso()
    meta = {
        "timestamp": timestamp,
        "type": "session_meta",
        "payload": {
            "session_id": session_id,
            "id": session_id,
            "timestamp": timestamp,
            "cwd": cwd,
            "originator": "Wisp",
            "cli_version": _latest_codex_cli_version(codex_home),
            "source": "vscode",
            "thread_source": "user",
            "model_provider": "openai",
            "dynamic_tools": [],
            "history_mode": "legacy",
        },
    }
    return [meta, *_codex_append_records(messages)]


def _claude_project_slug(cwd: str) -> str:
    normalized = str(Path(cwd).expanduser().resolve())
    if re.match(r"^[A-Z]:", normalized):
        normalized = normalized[0].lower() + normalized[1:]
    return re.sub(r"[^A-Za-z0-9]", "-", normalized)


def _claude_new_session_records(
    messages: list[dict],
    *,
    session_id: str,
    cwd: str,
    title: str,
) -> list[dict]:
    source = {"session_id": session_id, "cwd": cwd}
    title_record = {
        "type": "custom-title",
        "customTitle": title,
        "sessionId": session_id,
        "timestamp": _now_iso(),
    }
    return [title_record, *_claude_message_records(messages, source, [])]


def export_conversation_as_new_session(
    conversation: dict,
    provider: str,
    *,
    cwd: Path,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> ExportReport:
    """Export a Wisp-native chat as a new Codex or Claude local session."""
    existing_source = conversation.get("external_source")
    if isinstance(existing_source, dict) and _text(existing_source.get("session_id")):
        raise ValueError("conversation is already linked to an external session")
    provider = _text(provider).lower()
    if provider not in {"codex", "claude"}:
        raise ValueError("unsupported external conversation provider")
    workspace = Path(cwd).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("workspace folder does not exist")
    messages = _exportable_messages(conversation)
    if not messages:
        raise ValueError("conversation has no messages to export")

    session_id = str(uuid.uuid4())
    title = _conversation_export_title(conversation, messages)
    if provider == "codex":
        home = codex_home or _default_codex_home()
        now = datetime.now(UTC)
        path = (
            home
            / "sessions"
            / now.strftime("%Y")
            / now.strftime("%m")
            / now.strftime("%d")
            / f"rollout-{now.strftime('%Y-%m-%dT%H-%M-%S')}-{session_id}.jsonl"
        )
        records = _codex_new_session_records(
            messages,
            session_id=session_id,
            cwd=str(workspace),
            codex_home=home,
        )
    else:
        home = claude_home or _default_claude_home()
        path = home / "projects" / _claude_project_slug(str(workspace)) / f"{session_id}.jsonl"
        records = _claude_new_session_records(
            messages,
            session_id=session_id,
            cwd=str(workspace),
            title=title,
        )

    _write_new_jsonl(path, records)
    source = {
        "provider": provider,
        "session_id": session_id,
        "path": str(path),
        "signature": _file_signature(path),
        "message_count": len(messages),
        "cwd": str(workspace),
        "source_updated_at": _file_timestamp(path),
        "synced_at": _now_iso(),
    }
    conversation["external_source"] = source
    conversation["title"] = title
    return ExportReport(
        provider=provider,
        session_id=session_id,
        path=path,
        exported=len(messages),
    )


def push_conversation_to_source(
    conversation: dict,
    *,
    backup_dir: Path | None = None,
    source_root: Path | None = None,
) -> PushReport:
    """Append Wisp-only turns to an imported transcript after making a full backup.

    This is an explicit compatibility fallback for installations where the
    provider's supported CLI/app-server cannot be launched. Earlier source
    records are never rewritten.
    """
    source = conversation.get("external_source")
    if not isinstance(source, dict):
        raise ValueError("conversation was not imported from an external history")
    provider = _text(source.get("provider")).lower()
    path = Path(_text(source.get("path"))).expanduser()
    if provider not in {"codex", "claude"} or path.suffix.lower() != ".jsonl":
        raise ValueError("unsupported external conversation source")
    if not _io_path(path).is_file() or not _allowed_source_path(path, provider, source_root):
        raise ValueError("external history path is outside the expected provider directory")

    imported_count = source.get("message_count", 0)
    if not isinstance(imported_count, int) or imported_count < 0:
        raise ValueError("external conversation metadata is invalid")
    all_messages = conversation.get("messages") if isinstance(conversation.get("messages"), list) else []
    pending = [
        message
        for message in all_messages[imported_count:]
        if isinstance(message, dict)
        and _text(message.get("role")) in {"user", "assistant"}
        and _text(message.get("content"))
    ]
    if not pending:
        return PushReport(provider=provider, pushed=0)

    backup_root = backup_dir or (CHATS_DIR / "external_history_backups" / provider)
    backup_root.mkdir(parents=True, exist_ok=True)
    safe_session = re.sub(r"[^A-Za-z0-9._-]+", "-", _text(source.get("session_id")) or path.stem)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_root / f"{safe_session}-{stamp}.jsonl.bak"
    shutil.copy2(_io_path(path), _io_path(backup_path))

    records = (
        _codex_append_records(pending)
        if provider == "codex"
        else _claude_append_records(path, pending, source)
    )
    _append_jsonl(path, records)
    source["message_count"] = imported_count + len(pending)
    source["signature"] = _file_signature(path)
    source["source_updated_at"] = _file_timestamp(path)
    source["synced_at"] = _now_iso()
    source["last_backup"] = str(backup_path)
    return PushReport(provider=provider, pushed=len(pending), backup_path=backup_path)


def sync_external_conversations(
    conversations: list[dict],
    *,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> SyncReport:
    """Pull Codex and Claude Code transcripts into ``conversations`` in place."""
    discovered, report = discover_external_conversations(
        codex_home=codex_home,
        claude_home=claude_home,
    )
    return apply_external_conversations(conversations, discovered, report=report)


def discover_external_conversations(
    *,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> tuple[list[dict], SyncReport]:
    """Read external transcripts without mutating Wisp's live conversation list."""
    report = SyncReport()
    codex_home = codex_home or _default_codex_home()
    claude_home = claude_home or _default_claude_home()
    discovered: dict[str, dict] = {}

    for provider, path in _session_files(codex_home, claude_home):
        try:
            imported = parse_codex_session(path) if provider == "codex" else parse_claude_session(path)
        except OSError as exc:
            report.errors.append(f"{provider}: {type(exc).__name__}")
            continue
        if imported is None:
            report.skipped += 1
            continue
        key = _source_key(imported)
        previous = discovered.get(key)
        previous_time = _text((previous or {}).get("external_source", {}).get("source_updated_at"))
        current_time = _text(imported.get("external_source", {}).get("source_updated_at"))
        if not previous or current_time >= previous_time:
            discovered[key] = imported

    return list(discovered.values()), report


def apply_external_conversations(
    conversations: list[dict],
    discovered: list[dict],
    *,
    report: SyncReport | None = None,
) -> SyncReport:
    """Merge previously discovered transcripts into Wisp's live list."""
    report = report or SyncReport()

    existing = {_source_key(conv): conv for conv in conversations if _source_key(conv)}
    additions: list[dict] = []
    for imported in discovered:
        key = _source_key(imported)
        if not key:
            report.skipped += 1
            continue
        current = existing.get(key)
        if current is None:
            additions.append(imported)
            report.imported += 1
            continue
        old_source = current.get("external_source") if isinstance(current.get("external_source"), dict) else {}
        new_source = imported["external_source"]
        if _text(old_source.get("signature")) == _text(new_source.get("signature")):
            report.unchanged += 1
            continue
        old_count = old_source.get("message_count", 0)
        if not isinstance(old_count, int) or old_count < 0:
            old_count = 0
        local_tail = list(current.get("messages", []))[old_count:]
        current["messages"] = [*imported["messages"], *local_tail]
        current["external_source"] = new_source
        current["created_at"] = imported["created_at"]
        current["updated_at"] = imported["updated_at"]
        if not _text(current.get("title_override")):
            current["title"] = imported["title"]
        report.updated += 1

    additions.sort(key=lambda item: _text(item.get("updated_at")))
    conversations.extend(additions)
    return report
