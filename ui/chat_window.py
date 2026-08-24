"""
ui/chat_window.py - Multi-turn chat window with conversation history sidebar.

Left sidebar lists all past conversations; clicking one selects it so you can
continue that thread.

Send message: Enter (Shift+Enter for newline).
"""
from __future__ import annotations

import html
import inspect
import json
import os
import re
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QEventLoop, QMimeData, QObject, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import config
from core import addon_store
from core.assistant_text import ThoughtStreamParser, merge_segment_iterables
from core.conversation_store import store as _conversation_store
from core.conversation_store.external_sync import (
    apply_external_conversations,
    discover_external_conversations,
    export_conversation_as_new_session,
    external_conversations_since,
    external_project_path,
    load_external_sync_state,
    set_external_auto_sync,
)
from core.conversation_store.store import GENERAL_PROJECT_ID as _GENERAL_PROJECT_ID
from core.system import file_browser as _file_browser
from core.system.paths import CHATS_DIR
from runtime.supervisor import tool_modes
from ui.chat_rendering import (
    _assistant_segments_to_html,
    _assistant_text_to_html,
    _contains_markdown_table,
    _user_text_to_html,
)
from ui.i18n import t
from ui.shared.activity_spinner import ActivitySpinner
from ui.shared.theme import show_tooltip_text
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen
from ui.text_annotations import TextAnnotation, annotation_tooltip_anchor, normalize_range_annotations

_W          = 840
_H          = 640
_BG         = "#16181b"
_SIDEBAR_BG = "#1c1f23"
_TITLE_BG   = "#1c1f23"
_USER_BG    = "#504329"
_AI_BG      = "#1c1f23"
_BORDER     = "#30353b"
_TEXT       = "#e9e6e0"
_HINT       = "#8b8a86"
_ACCENT     = "#d8a145"
_SEL_BG     = "#403724"
_ACCENT_BG_10 = "#29271f"
_ACCENT_BG_12 = "#2c2920"
_ACCENT_BG_18 = "#342d22"
_ACCENT_BG_28 = "#433725"
_ACCENT_BG_32 = "#493a26"
_ACCENT_BG_46 = "#61492a"
_ACCENT_BG_60 = "#79582f"
_WHITE_BG_8 = "#1c1f23"
_WHITE_BG_10 = "#2b2d2f"
_WHITE_BG_12 = "#333538"
_PROJECT_HEADER_BG = "#1c1f23"
# Derived accents used on top of the accent colour (text/bg over accent buttons,
# disabled states). Seeded dark; refreshed from the app theme on each open.
_ON_ACCENT = "#16181b"
_ACCENT_HOVER = "#e6b45c"
_DISABLED_BG = "#444444"
_DISABLED_TEXT = "#666666"
_REVERT_DELAY_MS = 3000   # how long bold words stay highlighted after TTS finishes
_CHAT_RENDER_CHAR_LIMIT = 24_000
_CHAT_FOLLOW_BOTTOM_THRESHOLD_PX = 24
_CONTEXT_TOOLTIP_CHAR_LIMIT = 4_000
_ATTACHMENT_CONTEXT_CHAR_LIMIT = 40_000
_SAFE_LOCAL_PREVIEW_SUFFIXES = frozenset(
    {
        ".bmp", ".csv", ".doc", ".docx", ".gif", ".htm", ".html", ".jpeg", ".jpg",
        ".md", ".mkv", ".mov", ".mp3", ".mp4", ".odp", ".ods", ".odt", ".pdf",
        ".png", ".ppt", ".pptx", ".rtf", ".svg", ".txt", ".wav", ".webm", ".webp",
        ".xls", ".xlsx",
    }
)
_SIDEBAR_MENU_W = 32
_SIDEBAR_FADE_W = 34
_SIDEBAR_GENERAL_GROUP_GAP = 8
_CHAT_SCROLLBAR_WIDTH = 18
_CHAT_SCROLLBAR_HANDLE_MIN_HEIGHT = 52
_CHAT_WHEEL_STEP = 72
_CHAT_AUTOSCROLL_DEAD_ZONE = 12
_CHAT_AUTOSCROLL_INTERVAL_MS = 16
_FORMATTED_REPLIES_ADDON_ID = "formatted-replies"
# History rows built before the window is first painted. Anything past this is
# filled in straight after the first frame, so a long history cannot keep the
# window off screen. Comfortably more than one screenful at any usable height.
_SIDEBAR_INITIAL_ROWS = 25
_EXTERNAL_AUTO_SYNC_INTERVAL_MS = 60_000
_CHAT_WINDOW_STATE_FILE = CHATS_DIR / "chat_window_state.json"
_CHAT_WINDOW_GEOMETRY_SAVE_DELAY_MS = 350


def _external_provider_display_name(provider: object) -> str:
    """Return the local history provider name without implying a web-chat link."""
    key = str(provider or "").strip().lower()
    if key == "codex":
        return "Codex"
    if key == "claude":
        return "Claude Code"
    return key.title()


def _conversation_source_status(conversation: dict | None) -> tuple[str, str]:
    """Return a truthful short label and explanation for one conversation source."""
    conversation = conversation if isinstance(conversation, dict) else {}
    source = conversation.get("external_source")
    if not isinstance(source, dict):
        return (
            t("Local OpenWand"),
            t("Stored locally by OpenWand. It is not linked to an external chat thread."),
        )
    provider = _external_provider_display_name(source.get("provider")) or t("external history")
    exported = str(source.get("origin") or "").strip().lower() == "exported"
    label = (
        t("Exported to {provider} · pull-only").format(provider=provider)
        if exported
        else t("Imported from {provider} · pull-only").format(provider=provider)
    )
    detail = t(
        "This is a local transcript relationship, not a ChatGPT or Claude web conversation. "
        "OpenWand can pull later transcript changes, but new OpenWand turns are not written back automatically."
    )
    return label, detail


def _mix_hex(a: str, b: str, t: float) -> str:
    """Blend two hex colours: t=0 → a, t=1 → b."""
    ca, cb = QColor(a), QColor(b)
    return (
        f"#{round(ca.red() * (1 - t) + cb.red() * t):02x}"
        f"{round(ca.green() * (1 - t) + cb.green() * t):02x}"
        f"{round(ca.blue() * (1 - t) + cb.blue() * t):02x}"
    )


def _formatted_reply_chat_colors(dark: bool) -> dict[str, str]:
    """Keep formatted replies inside the application's active amber palette."""
    from ui.shared.theme import theme_colors

    return theme_colors(dark)


def _refresh_chat_palette(formatted_replies_enabled: bool = False) -> None:
    """Re-derive Chat colours from either the host or addon-owned palette.

    The chat window predates the shared light/dark theme and was written with a
    fixed dark palette spread across ~150 inline stylesheet f-strings. Rather
    than thread a palette object through all of them, we recompute those
    module-level colour names whenever the addon UI mode changes. Formatted
    replies share the host palette so enabling the addon never turns Chat into
    a visually separate application.
    """
    global _BG, _SIDEBAR_BG, _TITLE_BG, _USER_BG, _AI_BG, _BORDER, _TEXT, _HINT
    global _ACCENT, _SEL_BG, _PROJECT_HEADER_BG
    global _ACCENT_BG_10, _ACCENT_BG_12, _ACCENT_BG_18, _ACCENT_BG_28
    global _ACCENT_BG_32, _ACCENT_BG_46, _ACCENT_BG_60
    global _WHITE_BG_8, _WHITE_BG_10, _WHITE_BG_12
    global _ON_ACCENT, _ACCENT_HOVER, _DISABLED_BG, _DISABLED_TEXT
    try:
        from ui.shared.theme import is_dark_mode, theme_colors
        c = theme_colors()
        if formatted_replies_enabled:
            c = _formatted_reply_chat_colors(is_dark_mode())
    except Exception:
        return
    bg, surface, text, accent = c["bg"], c["surface"], c["text"], c["accent"]
    _BG = bg
    _SIDEBAR_BG = surface
    _TITLE_BG = surface
    _PROJECT_HEADER_BG = surface
    _AI_BG = c["card"]
    _BORDER = c["border"]
    _TEXT = text
    _HINT = c["text_dim"]
    _ACCENT = accent
    _ON_ACCENT = c["on_accent"]
    _ACCENT_HOVER = c["accent_hover"]
    _USER_BG = _mix_hex(bg, accent, 0.30)
    _SEL_BG = _mix_hex(bg, accent, 0.22)
    _ACCENT_BG_10 = _mix_hex(bg, accent, 0.10)
    _ACCENT_BG_12 = _mix_hex(bg, accent, 0.12)
    _ACCENT_BG_18 = _mix_hex(bg, accent, 0.18)
    _ACCENT_BG_28 = _mix_hex(bg, accent, 0.28)
    _ACCENT_BG_32 = _mix_hex(bg, accent, 0.32)
    _ACCENT_BG_46 = _mix_hex(bg, accent, 0.46)
    _ACCENT_BG_60 = _mix_hex(bg, accent, 0.60)
    _WHITE_BG_8 = surface
    _WHITE_BG_10 = _mix_hex(bg, text, 0.10)
    _WHITE_BG_12 = _mix_hex(bg, text, 0.14)
    _DISABLED_BG = _mix_hex(bg, text, 0.16)
    _DISABLED_TEXT = c["text_dim"]
    try:
        from ui.chat_rendering import set_render_palette
        set_render_palette(
            code_bg=_mix_hex(bg, text, 0.08),
            code_inline_bg=_mix_hex(bg, text, 0.16),
            thought=c["text_dim"],
            table_header_bg=_mix_hex(bg, accent, 0.24),
            table_row_bg=c["card"],
            table_alt_bg=_mix_hex(c["card"], accent, 0.06),
            table_border=c["border"],
            table_text=text,
            table_accent=accent,
        )
    except Exception:
        pass


def _ui_font(point_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Return a platform-default UI font with the requested size and weight."""
    app = QApplication.instance()
    font = QFont(app.font()) if app is not None else QFont()
    font.setPointSize(point_size)
    font.setWeight(weight)
    return font


def _estimate_context_tokens(text: str) -> int:
    """Fast token estimate matching the intent overlay preview."""
    cjk = 0
    for ch in text or "":
        code = ord(ch)
        if (
            0x3040 <= code <= 0x30FF
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xAC00 <= code <= 0xD7AF
            or 0xFF00 <= code <= 0xFFEF
        ):
            cjk += 1
    return max(0, round(cjk * 0.85 + (len(text or "") - cjk) / 4))


def _token_label(text: str) -> str:
    tokens = _estimate_context_tokens(text)
    if tokens <= 0:
        return "0 tok"
    if tokens >= 1000:
        return f"~{tokens / 1000:.1f}k tok"
    return f"~{tokens} tok"


def _deferred_token_label() -> str:
    return "? tok"


def _is_concrete_token_label(value: str) -> bool:
    """Return True for a real estimate that should survive preview refreshes."""
    text = str(value or "").strip()
    return bool(text) and text not in {"0 tok", _deferred_token_label()}


def _now_iso() -> str:
    """Return current UTC time for conversation metadata."""
    return datetime.now(UTC).isoformat()


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse stored ISO timestamps and normalize them to local time."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone()


def _format_conversation_datetime(value: str | None) -> str:
    """Format a conversation timestamp for display only."""
    dt = _parse_iso_datetime(value)
    if dt is None:
        return ""
    hour = dt.strftime("%I").lstrip("0") or "0"
    return f"{dt.strftime('%b')} {dt.day}, {dt.year} {hour}:{dt.strftime('%M %p')}"


def _message_timestamp_text(msg: dict, fallback: str | None = None) -> str:
    """Return display-only timestamp for one chat turn."""
    return _format_conversation_datetime(msg.get("created_at") or msg.get("updated_at") or fallback)


def _touch_conversation(conv: dict, *, now: str | None = None) -> str:
    """Ensure created_at exists and update updated_at."""
    stamp = now or _now_iso()
    conv.setdefault("created_at", stamp)
    conv["updated_at"] = stamp
    return stamp


def _ensure_message_metadata(msg: dict, *, fallback_created_at: str | None = None) -> dict:
    """Ensure one persisted chat turn has stable display and action metadata."""
    if not isinstance(msg, dict):
        return msg
    msg.setdefault("id", str(uuid.uuid4()))
    msg.setdefault("created_at", fallback_created_at or _now_iso())
    return msg


def _ensure_conversation_metadata(conv: dict) -> None:
    """Backfill stable IDs/timestamps for older in-memory conversations."""
    stamp = conv.get("created_at") or conv.get("updated_at") or _now_iso()
    conv.setdefault("created_at", stamp)
    conv.setdefault("updated_at", stamp)
    for msg in conv.get("messages", []) or []:
        if isinstance(msg, dict):
            _ensure_message_metadata(msg, fallback_created_at=stamp)


def _message_context_text(raw: object) -> str:
    """Normalize one message-scoped hidden context value."""
    if isinstance(raw, list):
        return "\n\n---\n".join(
            str(item or "").strip()
            for item in raw
            if str(item or "").strip()
        )
    return str(raw or "").strip()


def _attachment_summary_context(attachments: object) -> str:
    """Return a compact, persisted reference summary for message attachments."""
    refs = _conversation_store.normalize_attachments(attachments)
    if not refs:
        return ""
    lines = ["[Attached files]"]
    for ref in refs:
        name = str(ref.get("name") or "Attachment")
        path = str(ref.get("path") or "")
        source = str(ref.get("source") or "")
        prefix = "managed" if source != "external_path" else "path"
        lines.append(f"- {name} ({prefix}: {path})")
    return "\n".join(lines)


def _chat_model_messages(messages: list[dict]) -> list[dict[str, str]]:
    """Return only model-relevant turn payload, with attachments anchored to users."""
    turns: list[dict[str, str]] = []
    for msg in messages:
        role = str(msg.get("role") or "").strip()
        content = msg.get("content")
        if role in {"system", "user", "assistant"} and isinstance(content, str) and content.strip():
            model_content = content
            context_parts: list[str] = []
            context_text = _message_context_text(msg.get("context")) if role == "user" else ""
            if context_text:
                context_parts.append(context_text)
            if role == "user":
                for ref in _conversation_store.normalize_attachments(msg.get("attachments")):
                    ref_context = _conversation_store.attachment_context_text(ref)
                    if ref_context:
                        context_parts.append(ref_context)
            if context_parts:
                joined_context = "\n\n".join(context_parts)
                model_content = (
                    f"{content.rstrip()}\n\n"
                    "[Attached context for this message]\n"
                    "Read and interpret this when the user refers to the attached file, document, "
                    "image, or context. Condense it to the information needed for the answer instead "
                    "of repeating the source in full unless the user explicitly asks for that.\n"
                    f"{joined_context}"
                )
            turn: dict[str, str] = {"role": role, "content": model_content}
            image = _conversation_store.first_image_base64_from_message(msg)
            if role == "user" and image:
                turn["image_base64"] = str(image)
            turns.append(turn)
    return turns


def _normalized_file_context(items: list) -> list[dict]:
    """Normalize persisted local-file tool metadata."""
    out: list[dict] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        item = {
            "tool": str(raw.get("tool") or ""),
            "path": str(raw.get("path") or ""),
            "relative_path": str(raw.get("relative_path") or ""),
            "root": str(raw.get("root") or ""),
            "ok": bool(raw.get("ok")),
            "message": str(raw.get("message") or ""),
        }
        if item["tool"] and item["path"] and item not in out:
            out.append(item)
    return out[-20:]


def _normalized_context_snippets(items: object) -> list[dict]:
    """Normalize display-only per-source context snippets for a user turn.

    These are shown under the message in the transcript and are never sent to
    the model. Each entry is ``{"label": str, "preview": str}``.
    """
    out: list[dict] = []
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, dict):
            continue
        label = " ".join(str(raw.get("label") or "").split())
        preview = " ".join(str(raw.get("preview") or "").split())
        if not preview:
            continue
        out.append({"label": label, "preview": preview})
    return out[:20]


def _merge_file_context(conv: dict, items: list) -> None:
    """Merge local-file metadata into a conversation."""
    merged = _normalized_file_context(list(conv.get("file_context") or []) + list(items or []))
    if merged:
        conv["file_context"] = merged


def _normalized_tool_context(raw: dict) -> dict:
    """Normalize persisted tool policy metadata."""
    if not isinstance(raw, dict):
        return {}

    def _str_list(value) -> list[str]:
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


def _merge_tool_context(conv: dict, raw: dict) -> None:
    """Merge tool policy metadata into a conversation."""
    ctx = _normalized_tool_context(raw)
    if ctx:
        conv["tool_context"] = ctx


def _merge_file_context_from_messages(messages: list) -> list[dict]:
    """Rebuild conversation file metadata from retained message metadata."""
    items: list = []
    for msg in messages or []:
        if isinstance(msg, dict):
            items.extend(msg.get("file_context") or [])
    return _normalized_file_context(items)


def _latest_tool_context_from_messages(messages: list) -> dict:
    """Return the latest retained tool policy metadata from assistant replies."""
    latest: dict = {}
    for msg in messages or []:
        if isinstance(msg, dict):
            ctx = _normalized_tool_context(msg.get("tool_context") or {})
            if ctx:
                latest = ctx
    return latest


def _context_from_messages(messages: list) -> str:
    """Rebuild hidden context from retained message-scoped context blocks."""
    blocks: list[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        text = _message_context_text(msg.get("context"))
        if text:
            blocks.append(text)
    return "\n\n---\n".join(blocks)


def _context_not_anchored_to_messages(context: str, messages: list) -> str:
    """Return conversation context blocks not already carried by message turns."""
    text = str(context or "").strip()
    if not text:
        return ""
    message_context = _context_from_messages(messages)
    if not message_context:
        return text
    blocks = [block.strip() for block in re.split(r"\n\s*---\s*\n", text) if block.strip()]
    missing = [block for block in blocks if block not in message_context]
    return "\n\n---\n".join(missing)


def _context_mode(value: object, default: str = "off") -> str:
    mode = str(value or default or "off").strip().lower()
    if mode == "on":
        return "auto"
    return mode if mode in {"off", "auto", "model"} else default


def _all_context_off_policy() -> dict:
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


def _default_context_policy() -> dict:
    """Default chat policy: first caller row, or no context when none exists."""
    from core.action_files.store import configured_caller_rows

    rows = configured_caller_rows(config)
    if not rows:
        return _all_context_off_policy()
    return _normalized_context_policy(rows[0])


def _normalized_context_policy(raw: dict | None) -> dict:
    """Normalize persisted chat context/tool policy metadata."""
    if not isinstance(raw, dict):
        return {}
    base = _all_context_off_policy()
    tools = raw.get("tools")
    base.update(
        {
            "context_ambient": bool(raw.get("context_ambient", base["context_ambient"])),
            "context_documents_mode": tool_modes.context_mode(raw, "documents"),
            "context_browser_mode": tool_modes.context_mode(raw, "browser"),
            "context_github_mode": tool_modes.context_mode(raw, "github"),
            "context_memory_mode": tool_modes.context_mode(raw, "memory"),
            "context_screenshot": _context_mode(raw.get("context_screenshot"), "off"),
            "context_clipboard": bool(raw.get("context_clipboard", False)),
            "file_access": tool_modes.local_file_access_mode(raw),
            "tools": dict(tools) if isinstance(tools, dict) else {},
        }
    )
    base["context_documents"] = base["context_documents_mode"] == "auto"
    base["context_tools"] = any(
        base[key] == "model"
        for key in (
            "context_documents_mode",
            "context_browser_mode",
            "context_github_mode",
            "context_memory_mode",
        )
    )
    base["_context_selection_enabled"] = bool(raw.get("_context_selection_enabled", False))
    return base


def _ensure_conversation_context_policy(conv: dict) -> dict:
    policy = _normalized_context_policy(conv.get("context_policy"))
    if not policy:
        policy = _default_context_policy()
        conv["context_policy"] = policy
    return policy


def _policy_state(policy: dict, source: str) -> str:
    if source == "ambient":
        if not policy.get("context_ambient") and tool_modes.context_mode(policy, "documents") == "off":
            return "off"
        return "auto" if tool_modes.context_mode(policy, "documents") == "model" else "on"
    if source == "browser":
        mode = tool_modes.context_mode(policy, "browser")
        return "auto" if mode == "model" else ("on" if mode == "auto" else "off")
    if source == "github":
        mode = tool_modes.context_mode(policy, "github")
        return "auto" if mode == "model" else ("on" if mode == "auto" else "off")
    if source == "selection":
        return "on" if policy.get("_context_selection_enabled", False) else "off"
    if source == "clipboard":
        return "on" if policy.get("context_clipboard") else "off"
    if source == "screenshot":
        mode = str(policy.get("context_screenshot") or "off").lower()
        return "auto" if mode == "model" else ("on" if mode == "auto" else "off")
    if source == "memory":
        mode = tool_modes.context_mode(policy, "memory")
        return "auto" if mode == "model" else ("on" if mode == "on" else "off")
    if source == "files":
        return tool_modes.local_file_access_mode(policy)
    return "off"


def _apply_policy_state(policy: dict, source: str, state: str) -> dict:
    updated = _normalized_context_policy(policy)
    state = str(state or "off").lower()
    if source == "ambient":
        updated["context_ambient"] = state != "off"
        updated["context_documents_mode"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
    elif source == "browser":
        updated["context_browser_mode"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
    elif source == "github":
        updated["context_github_mode"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
    elif source == "selection":
        updated["_context_selection_enabled"] = state != "off"
    elif source == "clipboard":
        updated["context_clipboard"] = state != "off"
    elif source == "screenshot":
        updated["context_screenshot"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
    elif source == "memory":
        updated["context_memory_mode"] = "off" if state == "off" else ("model" if state == "auto" else "on")
    elif source == "files":
        updated["file_access"] = state if state in {"off", "read", "ask", "auto"} else "off"
    updated["context_documents"] = updated["context_documents_mode"] == "auto"
    updated["context_tools"] = any(
        updated[key] == "model"
        for key in (
            "context_documents_mode",
            "context_browser_mode",
            "context_github_mode",
            "context_memory_mode",
        )
    )
    return updated


def _append_context_block(existing: str, title: str, body: str) -> str:
    """Append a labelled context block while keeping separator formatting stable."""
    text = str(body or "").strip()
    if not text:
        return str(existing or "")
    block = f"[{title}]\n{text}"
    current = str(existing or "").strip()
    return f"{current}\n\n---\n{block}" if current else block


def _file_context_text(items: list) -> str:
    """Build hidden follow-up context for recent local-file tools."""
    normalized = _normalized_file_context(items)
    if not normalized:
        return ""
    lines = [
        "Recent local file tool context for this conversation.",
        "Use these exact paths when the user refers to 'that file' or a prior file.",
    ]
    for item in normalized[-8:]:
        status = "ok" if item.get("ok") else "failed"
        path = item.get("path") or item.get("relative_path")
        rel = item.get("relative_path") or ""
        label = f"{item.get('tool')} ({status}): {path}"
        if rel and rel != path:
            label += f" [relative: {rel}]"
        message = str(item.get("message") or "").strip()
        if message:
            label += f" - {message}"
        lines.append(f"- {label}")
    return "\n".join(lines)


def _truncate_for_display(text: str, limit: int, label: str = "display") -> str:
    """Handle truncate for display for UI chat window."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    hidden = len(text) - limit
    return text[:limit].rstrip() + f"\n\n[{label} truncated; {hidden} chars hidden]"


def _ui_lab_label_annotations(text: str, role: str) -> list[dict]:
    """Return saved UI Lab label annotations for chat-window display."""
    if not addon_store.is_enabled("ui-lab", default=True):
        return []
    try:
        from addons.ui_lab import get_text_annotations

        return list(
            get_text_annotations(
                {
                    "text": text,
                    "surface": "chat",
                    "role": role,
                }
            )
            or []
        )
    except Exception:
        return []


def _merged_annotations(base: object, text: str, role: str) -> list:
    """Return enabled stored annotations plus current UI Lab label rules."""
    out: list = []
    for item in list(base or []) if isinstance(base, list) else []:
        source = str(item.get("source") or "") if isinstance(item, dict) else str(
            getattr(item, "source", "") or ""
        )
        if source == "addon:ui-lab":
            # UI Lab labels are editable rules. Rebuild them against the current
            # text so old byte-based offsets and deleted rules cannot survive.
            continue
        if source.startswith("addon:"):
            addon_id = source.removeprefix("addon:")
            if addon_id and not addon_store.is_enabled(addon_id, default=True):
                continue
        out.append(item)
    out.extend(_ui_lab_label_annotations(text, role))
    return out


def _truncate_segments_for_display(
    segments: list[tuple[str, bool]],
    limit: int = _CHAT_RENDER_CHAR_LIMIT,
) -> list[tuple[str, bool]]:
    """Handle truncate segments for display for UI chat window."""
    total = sum(len(text) for text, _is_thought in segments)
    if total <= limit:
        return segments

    remaining = limit
    visible: list[tuple[str, bool]] = []
    for text, is_thought in segments:
        if remaining <= 0:
            break
        if len(text) <= remaining:
            visible.append((text, is_thought))
            remaining -= len(text)
            continue
        visible.append((text[:remaining].rstrip(), is_thought))
        remaining = 0

    hidden = total - limit
    _merge_display_segments(
        visible,
        f"\n\n[chat display truncated; {hidden} chars hidden]",
        False,
    )
    return visible


class _StreamSignals(QObject):
    """Model stream signals."""
    chunk     = Signal(object)
    final     = Signal(str)
    metadata  = Signal(object)
    finished  = Signal()
    external_sync = Signal(object)


class ExternalConversationImportDialog(QDialog):
    """Choose provider conversations from a ChatGPT-style project browser."""

    _ITEM_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    _SCOPE_PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 2
    _DISCOVERY_ORDER_ROLE = int(Qt.ItemDataRole.UserRole) + 3

    def __init__(
        self,
        provider: str,
        discovered: list[dict],
        parent: QWidget | None = None,
        *,
        scanning: bool = False,
    ) -> None:
        super().__init__(parent)
        self._provider = str(provider or "").strip().lower()
        self._discovered = [
            item
            for item in discovered
            if str((item.get("external_source") or {}).get("provider") or "")
            .strip()
            .lower()
            == self._provider
        ]
        self._scope_items: dict[str, QTreeWidgetItem] = {}
        self._conversation_items: list[tuple[dict, QTreeWidgetItem]] = []
        self._conversation_items_by_key: dict[str, tuple[dict, QTreeWidgetItem]] = {}
        self._discovery_order_by_key = {
            self._conversation_key(conversation): index
            for index, conversation in enumerate(self._discovered)
            if self._conversation_key(conversation)
        }
        self._next_discovery_order = len(self._discovered)
        self._sort_by = "activity"
        self._sort_descending = True
        self._updating_checks = False
        self._scanning = bool(scanning)
        self._scan_error = ""
        provider_name = _external_provider_display_name(self._provider)
        self._provider_name = provider_name
        self.setWindowTitle(t("Import {provider} conversations").format(provider=provider_name))
        self.setMinimumSize(760, 560)
        enable_standard_window_controls(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        explanation = QLabel(
            t("Choose which conversations to import. Nothing is added until you confirm.")
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet(f"color: {_HINT};")
        layout.addWidget(explanation)

        tools_row = QHBoxLayout()
        tools_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("externalImportSearch")
        self.search.setPlaceholderText(t("Search conversations..."))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(0)
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search.setStyleSheet(
            f"QLineEdit {{ background: {_SIDEBAR_BG}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; border-radius: 8px; padding: 8px 10px; }}"
            f"QLineEdit:focus {{ border-color: {_ACCENT}; }}"
        )
        tools_row.addWidget(self.search, 1)
        self.sort_by = QComboBox()
        self.sort_by.setObjectName("externalImportSortBy")
        self.sort_by.setAccessibleName(t("Sort conversations by"))
        self.sort_by.addItem(t("Last activity"), "activity")
        self.sort_by.addItem(t("Date created"), "created")
        self.sort_by.addItem(t("Title"), "title")
        self.sort_by.addItem(t("Discovery order"), "discovered")
        self.sort_by.setFixedWidth(150)
        tools_row.addWidget(self.sort_by)
        self.sort_order = QComboBox()
        self.sort_order.setObjectName("externalImportSortOrder")
        self.sort_order.setAccessibleName(t("Sort order"))
        self.sort_order.setFixedWidth(125)
        self._refresh_sort_order_options()
        tools_row.addWidget(self.sort_order)
        self.select_all_button = QPushButton(t("Select all"))
        self.select_all_button.setObjectName("externalImportSelectAll")
        self.clear_button = QPushButton(t("Clear"))
        self.clear_button.setObjectName("externalImportClear")
        tools_row.addWidget(self.select_all_button)
        tools_row.addWidget(self.clear_button)
        layout.addLayout(tools_row)

        self.preview_label = QLabel()
        self.preview_label.setObjectName("externalImportPreviewCount")
        layout.addWidget(self.preview_label)
        self.browser = QTreeWidget()
        self.browser.setObjectName("externalImportBrowser")
        self.browser.setHeaderHidden(True)
        self.browser.setColumnCount(1)
        self.browser.setRootIsDecorated(True)
        self.browser.setItemsExpandable(True)
        self.browser.setIndentation(26)
        self.browser.setUniformRowHeights(False)
        self.browser.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setIconSize(QSize(18, 18))
        self.browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.browser.setStyleSheet(
            f"QTreeWidget {{ background: {_SIDEBAR_BG}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; border-radius: 10px; padding: 6px; outline: none; }}"
            "QTreeWidget::item { border-radius: 8px; padding: 4px 10px; }"
            f"QTreeWidget::item:hover {{ background: {_WHITE_BG_10}; }}"
        )
        layout.addWidget(self.browser, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.import_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.import_button.setText(t("Import"))
        self.import_button.setObjectName("externalImportConfirm")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._populate_browser()
        self.browser.itemChanged.connect(self._on_item_changed)
        self.search.textChanged.connect(self._filter_browser)
        self.sort_by.currentIndexChanged.connect(self._on_sort_changed)
        self.sort_order.currentIndexChanged.connect(self._on_sort_order_changed)
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.clear_button.clicked.connect(lambda: self._set_all_checked(False))
        self._refresh_selection_summary()

        parent_width = parent.width() if parent is not None and parent.width() > 0 else 980
        parent_height = parent.height() if parent is not None and parent.height() > 0 else 660
        fit_window_to_screen(
            self,
            preferred_width=max(900, parent_width),
            preferred_height=max(620, parent_height),
        )

    @staticmethod
    def _conversation_key(conversation: dict) -> str:
        source = conversation.get("external_source")
        if not isinstance(source, dict):
            return ""
        provider = str(source.get("provider") or "").strip().lower()
        session_id = str(source.get("session_id") or "").strip()
        return f"{provider}:{session_id}" if provider and session_id else ""

    @staticmethod
    def _activity_timestamp(conversation: dict) -> str:
        """Return the newest real activity time available for one conversation."""
        candidates = [str(conversation.get("updated_at") or "")]
        messages = conversation.get("messages")
        if isinstance(messages, list):
            candidates.extend(
                str(message.get("updated_at") or message.get("created_at") or "")
                for message in messages
                if isinstance(message, dict)
            )
        source = conversation.get("external_source")
        if isinstance(source, dict):
            candidates.append(str(source.get("source_updated_at") or ""))
        parsed = [
            (value, stamp)
            for value in candidates
            if (stamp := _parse_iso_datetime(value)) is not None
        ]
        return max(parsed, key=lambda pair: pair[1])[0] if parsed else ""

    def _populate_browser(self) -> None:
        """Build general and project groups without exposing filesystem paths."""
        general = [item for item in self._discovered if not external_project_path(item)]
        projects: dict[str, tuple[str, list[dict]]] = {}
        for conversation in self._discovered:
            path = external_project_path(conversation)
            if not path:
                continue
            key = path.casefold()
            if key not in projects:
                projects[key] = (path, [])
            projects[key][1].append(conversation)

        if general:
            self._add_scope(
                t("General conversations"),
                "",
                general,
                checked_by_default=True,
                folder=False,
            )

        self._projects_section: QTreeWidgetItem | None = None
        if projects:
            self._projects_section = QTreeWidgetItem(
                self.browser,
                [t("Projects")],
            )
            self._projects_section.setData(0, self._ITEM_KIND_ROLE, "section")
            self._projects_section.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._projects_section.setForeground(0, QColor(_HINT))
            self._projects_section.setFont(0, _ui_font(9, QFont.Weight.Bold))
            self._projects_section.setSizeHint(0, QSize(0, 34))
            self._projects_section.setExpanded(True)
            for path, conversations in projects.values():
                self._add_scope(
                    Path(path).name or t("Untitled project"),
                    path,
                    conversations,
                    checked_by_default=True,
                    folder=True,
                    parent=self._projects_section,
                )
        self.browser.expandAll()
        self._sort_all_conversations()

    def _add_scope(
        self,
        name: str,
        path: str,
        conversations: list[dict],
        *,
        checked_by_default: bool,
        folder: bool,
        parent: QTreeWidgetItem | None = None,
    ) -> None:
        target = parent if parent is not None else self.browser
        scope = QTreeWidgetItem(target, [name])
        scope.setIcon(0, self._scope_icon(folder=folder))
        scope.setData(0, self._ITEM_KIND_ROLE, "scope")
        scope.setData(0, self._SCOPE_PATH_ROLE, path)
        scope.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        scope.setFont(0, _ui_font(10, QFont.Weight.DemiBold))
        scope.setForeground(0, QColor(_TEXT))
        scope.setSizeHint(0, QSize(0, 42))
        self._scope_items[path] = scope

        for conversation in conversations:
            self._insert_conversation_item(
                scope,
                conversation,
                checked=checked_by_default,
            )
        self._update_scope_check_state(scope)
        scope.setExpanded(True)

    def _insert_conversation_item(
        self,
        scope: QTreeWidgetItem,
        conversation: dict,
        *,
        checked: bool,
        tree_index: int | None = None,
        discovery_index: int | None = None,
        discovery_order: int | None = None,
    ) -> QTreeWidgetItem:
        """Insert one streamed conversation in newest-first order."""
        title = str(conversation.get("title") or t("Untitled conversation")).strip()
        updated = _format_conversation_datetime(self._activity_timestamp(conversation))
        label = title if not updated else f"{title}\n{updated}"
        item = QTreeWidgetItem([label])
        item.setData(0, self._ITEM_KIND_ROLE, "conversation")
        key = self._conversation_key(conversation)
        if discovery_order is None:
            discovery_order = self._discovery_order_by_key.get(key)
        if discovery_order is None:
            discovery_order = self._next_discovery_order
            self._next_discovery_order += 1
        if key:
            self._discovery_order_by_key[key] = discovery_order
        item.setData(0, self._DISCOVERY_ORDER_ROLE, discovery_order)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            0,
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked,
        )
        item.setForeground(0, QColor(_TEXT))
        item.setSizeHint(0, QSize(0, 52 if updated else 42))
        item.setToolTip(0, title)
        if tree_index is None or tree_index < 0:
            scope.addChild(item)
        else:
            scope.insertChild(tree_index, item)
        pair = (conversation, item)
        if discovery_index is None or discovery_index < 0:
            self._conversation_items.append(pair)
        else:
            self._conversation_items.insert(discovery_index, pair)
        if key:
            self._conversation_items_by_key[key] = pair
        return item

    def append_conversation(self, conversation: dict) -> None:
        """Add or update one conversation while the background scan is running."""
        source = conversation.get("external_source")
        if not isinstance(source, dict) or str(source.get("provider") or "").lower() != self._provider:
            return
        key = self._conversation_key(conversation)
        previous = self._conversation_items_by_key.get(key) if key else None
        was_checked = False
        previous_discovery_index: int | None = None
        previous_tree_index: int | None = None
        previous_discovery_order: int | None = None
        previous_scope: QTreeWidgetItem | None = None
        if previous is not None:
            _previous_conversation, previous_item = previous
            was_checked = previous_item.checkState(0) == Qt.CheckState.Checked
            previous_scope = previous_item.parent()
            previous_discovery_order = int(
                previous_item.data(0, self._DISCOVERY_ORDER_ROLE) or 0
            )
            previous_discovery_index = self._conversation_items.index(previous)
            if previous_scope is not None:
                previous_tree_index = previous_scope.indexOfChild(previous_item)
                previous_scope.takeChild(previous_tree_index)
            self._conversation_items.pop(previous_discovery_index)
            self._discovered = [
                item
                for item in self._discovered
                if self._conversation_key(item) != key
            ]
        self._discovered.append(conversation)

        path = external_project_path(conversation)
        scope = self._scope_items.get(path)
        if scope is None:
            if path and self._projects_section is None:
                self._projects_section = QTreeWidgetItem(self.browser, [t("Projects")])
                self._projects_section.setData(0, self._ITEM_KIND_ROLE, "section")
                self._projects_section.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self._projects_section.setForeground(0, QColor(_HINT))
                self._projects_section.setFont(0, _ui_font(9, QFont.Weight.Bold))
                self._projects_section.setSizeHint(0, QSize(0, 34))
                self._projects_section.setExpanded(True)
            self._add_scope(
                Path(path).name or t("Untitled project") if path else t("General conversations"),
                path,
                [conversation],
                checked_by_default=True,
                folder=bool(path),
                parent=self._projects_section if path else None,
            )
            scope = self._scope_items[path]
        else:
            checked = (
                was_checked
                if previous is not None
                else scope.checkState(0) != Qt.CheckState.Unchecked
            )
            self._insert_conversation_item(
                scope,
                conversation,
                checked=checked,
                tree_index=(
                    previous_tree_index
                    if previous_scope is scope
                    else None
                ),
                discovery_index=previous_discovery_index,
                discovery_order=previous_discovery_order,
            )
            self._update_scope_check_state(scope)
        if previous_scope is not None and previous_scope is not scope:
            self._update_scope_check_state(previous_scope)
        self._sort_scope(scope)
        self._sort_project_scopes()
        self._filter_browser(self.search.text())
        self._refresh_selection_summary()

    def finish_scan(self, error: str = "") -> None:
        """Mark a streaming scan complete without moving already-visible rows."""
        self._scanning = False
        self._scan_error = str(error or "")
        self._refresh_selection_summary()

    def _refresh_sort_order_options(self) -> None:
        """Use order labels that match the selected sort field."""
        order = getattr(self, "sort_order", None)
        if order is None:
            return
        descending = bool(getattr(self, "_sort_descending", True))
        sort_by = str(getattr(self, "_sort_by", "activity"))
        labels = {
            "title": ((t("Z–A"), True), (t("A–Z"), False)),
            "discovered": ((t("Last found"), True), (t("First found"), False)),
        }.get(sort_by, ((t("Newest first"), True), (t("Oldest first"), False)))
        order.blockSignals(True)
        order.clear()
        for label, value in labels:
            order.addItem(label, value)
        order.setCurrentIndex(max(0, order.findData(descending)))
        order.blockSignals(False)

    def _on_sort_changed(self, _index: int) -> None:
        self._sort_by = str(self.sort_by.currentData() or "activity")
        self._refresh_sort_order_options()
        self._sort_all_conversations()

    def _on_sort_order_changed(self, _index: int) -> None:
        self._sort_descending = bool(self.sort_order.currentData())
        self._sort_all_conversations()

    def _conversation_sort_value(self, conversation: dict, item: QTreeWidgetItem) -> object:
        if self._sort_by == "title":
            return str(conversation.get("title") or "").casefold()
        if self._sort_by == "discovered":
            return int(item.data(0, self._DISCOVERY_ORDER_ROLE) or 0)
        timestamp = (
            str(conversation.get("created_at") or "")
            if self._sort_by == "created"
            else self._activity_timestamp(conversation)
        )
        parsed = _parse_iso_datetime(timestamp)
        return parsed.timestamp() if parsed is not None else 0.0

    def _conversation_for_item(self, wanted: QTreeWidgetItem) -> dict:
        return next(
            (conversation for conversation, item in self._conversation_items if item is wanted),
            {},
        )

    def _sort_scope(self, scope: QTreeWidgetItem) -> None:
        """Keep one scope sorted immediately as streaming results arrive."""
        children = [scope.takeChild(0) for _index in range(scope.childCount())]
        children.sort(key=lambda item: int(item.data(0, self._DISCOVERY_ORDER_ROLE) or 0))
        children.sort(
            key=lambda item: self._conversation_sort_value(
                self._conversation_for_item(item),
                item,
            ),
            reverse=self._sort_descending,
        )
        scope.addChildren(children)

    def _sort_project_scopes(self) -> None:
        section = getattr(self, "_projects_section", None)
        if section is None:
            return
        scopes = [section.takeChild(0) for _index in range(section.childCount())]
        scopes.sort(key=lambda scope: scope.text(0).casefold())
        if self._sort_by != "title":
            scopes.sort(
                key=lambda scope: (
                    self._conversation_sort_value(
                        self._conversation_for_item(scope.child(0)),
                        scope.child(0),
                    )
                    if scope.childCount()
                    else 0
                ),
                reverse=self._sort_descending,
            )
        elif self._sort_descending:
            scopes.reverse()
        section.addChildren(scopes)

    def _sort_all_conversations(self) -> None:
        for scope in self._scope_items.values():
            self._sort_scope(scope)
        self._sort_project_scopes()
        self._filter_browser(self.search.text())

    @staticmethod
    def _scope_icon(*, folder: bool) -> QIcon:
        """Draw small neutral sidebar icons without using OS file-browser artwork."""
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(_TEXT), 1.4))
        path = QPainterPath()
        if folder:
            path.moveTo(2.0, 5.0)
            path.lineTo(7.0, 5.0)
            path.lineTo(9.0, 7.0)
            path.lineTo(16.0, 7.0)
            path.lineTo(16.0, 15.0)
            path.lineTo(2.0, 15.0)
            path.closeSubpath()
            painter.drawPath(path)
        else:
            path.addRoundedRect(2.0, 3.0, 14.0, 11.0, 3.0, 3.0)
            painter.drawPath(path)
            painter.drawLine(5, 14, 4, 16)
        painter.end()
        return QIcon(pixmap)

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._updating_checks:
            return
        kind = str(item.data(0, self._ITEM_KIND_ROLE) or "")
        self._updating_checks = True
        try:
            if kind == "scope":
                state = item.checkState(0)
                if state != Qt.CheckState.PartiallyChecked:
                    for index in range(item.childCount()):
                        item.child(index).setCheckState(0, state)
            elif kind == "conversation" and item.parent() is not None:
                self._update_scope_check_state(item.parent())
        finally:
            self._updating_checks = False
        self._refresh_selection_summary()

    def _update_scope_check_state(self, scope: QTreeWidgetItem) -> None:
        states = [scope.child(index).checkState(0) for index in range(scope.childCount())]
        if states and all(state == Qt.CheckState.Checked for state in states):
            state = Qt.CheckState.Checked
        elif any(state != Qt.CheckState.Unchecked for state in states):
            state = Qt.CheckState.PartiallyChecked
        else:
            state = Qt.CheckState.Unchecked
        scope.setCheckState(0, state)

    def _set_all_checked(self, checked: bool) -> None:
        self._updating_checks = True
        try:
            state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            for _conversation, item in self._conversation_items:
                item.setCheckState(0, state)
            for scope in self._scope_items.values():
                self._update_scope_check_state(scope)
        finally:
            self._updating_checks = False
        self._refresh_selection_summary()

    def _filter_browser(self, query: str) -> None:
        terms = str(query or "").casefold().split()
        for path, scope in self._scope_items.items():
            scope_name = (Path(path).name if path else t("General conversations")).casefold()
            scope_match = bool(terms) and all(term in scope_name for term in terms)
            visible_children = 0
            for index in range(scope.childCount()):
                child = scope.child(index)
                searchable = f"{scope_name}\n{child.text(0)}".casefold()
                visible = not terms or scope_match or all(term in searchable for term in terms)
                child.setHidden(not visible)
                visible_children += int(visible)
            scope.setHidden(bool(terms) and visible_children == 0)
        section = getattr(self, "_projects_section", None)
        if section is not None:
            project_scopes = [scope for path, scope in self._scope_items.items() if path]
            section.setHidden(all(scope.isHidden() for scope in project_scopes))

    def _refresh_selection_summary(self) -> None:
        selected_count = sum(
            item.checkState(0) == Qt.CheckState.Checked
            for _conversation, item in self._conversation_items
        )
        found_count = len(self._conversation_items)
        if self._scan_error and found_count:
            text = t(
                "Scan stopped: {error} Found {found} conversation(s); {selected} selected can still be imported."
            ).format(
                error=self._scan_error,
                found=found_count,
                selected=selected_count,
            )
        elif self._scan_error:
            text = t("Scan failed: {error}").format(error=self._scan_error)
        elif self._scanning:
            text = t(
                "Scanning {provider}… Found {found} conversation(s); {selected} selected."
            ).format(
                provider=self._provider_name,
                found=found_count,
                selected=selected_count,
            )
        elif not found_count:
            text = t("No local {provider} conversations were found.").format(
                provider=self._provider_name
            )
        elif selected_count:
            text = t("Conversations to import: {count}").format(count=selected_count)
        else:
            text = t("No conversations match these choices.")
        self.preview_label.setText(text)
        self.import_button.setText(t("Import selected") if self._scanning else t("Import"))
        self.import_button.setEnabled(selected_count > 0)

    def selected_conversations(self) -> list[dict]:
        """Return checked conversations in the order discovery presented them."""
        return [
            conversation
            for conversation, item in self._conversation_items
            if item.checkState(0) == Qt.CheckState.Checked
        ]


class LocalWorkProgressDialog(QDialog):
    """On-demand monitor for local-file work started by one chat turn."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the monitor without showing it automatically."""
        super().__init__(parent)
        self.setWindowTitle(t("Local file progress"))
        self.setMinimumSize(560, 340)
        enable_standard_window_controls(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        title = QLabel(f"<b>{html.escape(t('Model work'))}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(title)
        self.status_label = QLabel(t("Working with local files…"))
        self.status_label.setObjectName("localWorkMonitorStatus")
        self.status_label.setStyleSheet(f"color: {_HINT};")
        layout.addWidget(self.status_label)
        self.activity_view = QTextBrowser()
        self.activity_view.setObjectName("localWorkMonitorActivity")
        self.activity_view.setReadOnly(True)
        self.activity_view.setOpenLinks(False)
        self.activity_view.setPlaceholderText(t("File activity will appear here."))
        self.activity_view.setStyleSheet(
            f"QTextBrowser {{ background: {_SIDEBAR_BG}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; border-radius: 8px; padding: 9px; }}"
        )
        layout.addWidget(self.activity_view, stretch=1)
        row = QHBoxLayout()
        row.addStretch()
        close_button = QPushButton(t("Close"))
        close_button.clicked.connect(self.close)
        row.addWidget(close_button)
        layout.addLayout(row)
        self._lines: list[str] = []
        fit_window_to_screen(self, preferred_width=620, preferred_height=420)

    def add_activity(self, event: dict) -> None:
        """Append one started/completed file operation."""
        tool = str(event.get("tool") or "").strip()
        path = str(event.get("relative_path") or event.get("path") or "").strip()
        phase = str(event.get("phase") or "completed").strip().lower()
        if not tool or not path:
            return
        verbs = {
            "list_files": (t("Inspecting folder"), t("Inspected folder")),
            "read_file": (t("Reading"), t("Read")),
            "create_file": (t("Creating"), t("Created")),
            "edit_file": (t("Editing"), t("Edited")),
            "write_file": (t("Writing"), t("Wrote")),
        }
        started, completed = verbs.get(tool, (t("Working on"), t("Finished")))
        if phase == "started":
            line = f"{started}: {path}"
        elif not bool(event.get("ok", True)):
            line = t("Failed: {path}").format(path=path)
        else:
            line = f"{completed}: {path}"
        if self._lines and self._lines[-1] == line:
            return
        self._lines.append(line)
        del self._lines[:-100]
        self.activity_view.setHtml(
            "<br>".join(f"• {html.escape(item)}" for item in self._lines)
        )
        bar = self.activity_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def mark_finished(self) -> None:
        """Mark this turn's monitored work complete."""
        self.status_label.setText(t("Local-file work finished."))


class _MessageTextView(QTextBrowser):
    """Model message text view."""
    _BASE_PT = 10

    def __init__(self, bg: str, scale: float = 1.0, *, presentation: str = "legacy"):
        """Initialize the message text view instance."""
        super().__init__()
        self._bg = bg
        self._scale = scale
        self._presentation = presentation
        self._tooltip_annotations: list[TextAnnotation] = []
        self._tooltips_by_anchor: dict[str, str] = {}
        self._context_menu_handler = None
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.setMouseTracking(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_font_scale(scale)
        self.textChanged.connect(self._sync_height)
        self.anchorClicked.connect(self._open_link)

    def set_annotation_tooltips(self, text: str, annotations: object) -> None:
        """Store rendered-anchor tooltips for explicit hover handling."""
        self._tooltip_annotations = [
            item for item in normalize_range_annotations(annotations, text) if item.tooltip
        ]
        self._tooltips_by_anchor = {
            annotation_tooltip_anchor(item): item.tooltip
            for item in self._tooltip_annotations
            if annotation_tooltip_anchor(item)
        }

    def set_message_context_menu_handler(self, handler) -> None:
        """Install the owning chat-window message menu callback."""
        self._context_menu_handler = handler

    def set_font_scale(self, scale: float) -> None:
        """Apply the chat text zoom multiplier to this bubble."""
        self._scale = scale
        pt = max(7, round((11 if self._presentation != "legacy" else self._BASE_PT) * scale))
        # QTextBrowser rich text has its own QTextDocument. QSS font-size alone
        # does not establish that document's family, so HTML headings can fall
        # back to a serif face while the surrounding chat controls use the Qt
        # UI font. Bind both the widget and document to that same family; code
        # spans keep their explicit monospace family from chat_rendering.py.
        ui_font = _ui_font(pt)
        self.setFont(ui_font)
        self.document().setDefaultFont(ui_font)
        if self._presentation == "assistant":
            background = "transparent"
            radius = 0
            padding = "0px"
        elif self._presentation == "user":
            background = self._bg
            radius = 16
            padding = "10px 15px"
        else:
            background = self._bg
            radius = 8
            padding = "8px 11px"
        self.setStyleSheet(
            f"QTextBrowser {{ background: {background}; color: {_TEXT}; border-radius: {radius}px;"
            f" padding: {padding}; font-size: {pt}pt; border: none; }}"
            f"QTextBrowser::selection {{ background: {_ACCENT_BG_60}; color: {_TEXT}; }}"
        )
        self._sync_height()

    def _sync_height(self):
        """Handle sync height for message text view."""
        viewport_width = self.viewport().width()
        target_width = max(1.0, viewport_width - 1.0)
        if (
            (self._presentation == "assistant" or bool(self.property("openwand_has_table")))
            and viewport_width > 0
            and abs(self.document().textWidth() - target_width) > 0.25
        ):
            # QTextDocument otherwise keeps a content-derived width, so a
            # canonical Markdown table can render narrower than the rich view
            # sitting in the exact same message column.
            # Keep the document fractionally inside the viewport. QTextDocument
            # can otherwise round an exact-width table to one device pixel wider
            # and report phantom horizontal overflow even though the scrollbar is
            # disabled and the content is visually contained.
            self.document().setTextWidth(target_width)
        doc_h = self.document().documentLayout().documentSize().height()
        margin = self.contentsMargins().top() + self.contentsMargins().bottom()
        self.setFixedHeight(max(38, int(doc_h + margin + 6)))

    def showEvent(self, event):
        """Show event."""
        super().showEvent(event)
        # Document layout hasn't run before first show — recompute height now.
        QTimer.singleShot(0, self._sync_height)

    def resizeEvent(self, event):
        """Resize event."""
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._sync_height()

    def mouseMoveEvent(self, event):  # noqa: N802
        """Show annotation tooltips over labeled text."""
        anchor = self.anchorAt(event.position().toPoint())
        tooltip = self._tooltip_for_anchor(anchor)
        if not tooltip and anchor:
            url = QUrl(anchor)
            if url.scheme().lower() in {"http", "https", "mailto", "file"}:
                tooltip = url.toDisplayString()
        if tooltip:
            show_tooltip_text(event.globalPosition().toPoint(), tooltip, self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        """Hide annotation tooltips when leaving message text."""
        QToolTip.hideText()
        super().leaveEvent(event)

    def contextMenuEvent(self, event):  # noqa: N802
        """Route right-clicks to the chat message menu."""
        if callable(self._context_menu_handler):
            self._context_menu_handler(event.pos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def wheelEvent(self, event):  # noqa: N802
        """Let the conversation page scroll instead of the individual bubble."""
        event.ignore()

    def _tooltip_for_anchor(self, anchor: str) -> str:
        """Return the tooltip associated with one rendered internal anchor."""
        return self._tooltips_by_anchor.get(str(anchor or ""), "")

    def _open_link(self, url: QUrl) -> None:
        """Open web links and safely reveal model-mentioned local files."""
        scheme = url.scheme().lower()
        if scheme in {"http", "https", "mailto"}:
            QDesktopServices.openUrl(url)
            return
        if scheme != "file":
            return
        local_path = url.toLocalFile()
        if not local_path:
            return
        try:
            target = Path(local_path)
            if target.is_file() and target.suffix.lower() in _SAFE_LOCAL_PREVIEW_SUFFIXES:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
            else:
                _file_browser.reveal_path(target)
        except (FileNotFoundError, OSError):
            return


class _ConversationTitleButton(QPushButton):
    """Paints a sidebar title with a right-edge fade under the overlaid menu."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        active: bool,
        selected: bool,
        latest: bool,
    ) -> None:
        """Initialize the conversation title button instance."""
        super().__init__("")
        self._title = title
        self._subtitle = subtitle
        self._active = active
        self._selected = selected
        self._latest = latest
        self.setCheckable(True)
        self.setChecked(selected)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setToolTip(title)
        self.setAccessibleName(title)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def set_sidebar_state(self, *, active: bool, selected: bool, latest: bool) -> None:
        """Set sidebar state."""
        self._active = active
        self._selected = selected
        self._latest = latest
        self.setChecked(selected)
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        """Paint event."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        accent = QColor(_ACCENT)
        accent.setAlpha(34)
        hover = QColor(_TEXT)
        hover.setAlpha(16)
        bg = accent if self._selected or self.isChecked() else QColor(0, 0, 0, 0)
        if self.underMouse() and not (self._selected or self.isChecked()):
            bg = hover
        if bg.alpha():
            painter.fillRect(rect, bg)
        if self._active:
            painter.fillRect(rect.left(), rect.top(), 2, rect.height(), QColor(_ACCENT))

        text_rect = rect.adjusted(10, 0, -(_SIDEBAR_MENU_W + 10), 0)
        title_font = _ui_font(9)
        subtitle_font = _ui_font(8)
        painter.setFont(title_font)
        painter.setPen(QPen(QColor(_TEXT)))

        metrics = QFontMetrics(title_font)
        available = max(0, text_rect.width() - _SIDEBAR_FADE_W)
        title = metrics.elidedText(self._title, Qt.TextElideMode.ElideRight, available)
        title_rect = text_rect.adjusted(0, 4 if self._subtitle else 0, 0, -18 if self._subtitle else 0)
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            title,
        )
        if self._subtitle:
            painter.setFont(subtitle_font)
            painter.setPen(QPen(QColor(_HINT)))
            sub_metrics = QFontMetrics(subtitle_font)
            subtitle = sub_metrics.elidedText(self._subtitle, Qt.TextElideMode.ElideRight, available)
            subtitle_rect = text_rect.adjusted(0, 24, 0, -2)
            painter.drawText(
                subtitle_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                subtitle,
            )

        if metrics.horizontalAdvance(self._title) > available:
            fade_left = max(text_rect.left(), text_rect.right() - _SIDEBAR_FADE_W)
            gradient = QLinearGradient(fade_left, 0, text_rect.right(), 0)
            fade_color = (
                QColor(_mix_hex(_SIDEBAR_BG, _ACCENT, 0.07))
                if self._selected or self.isChecked()
                else QColor(_SIDEBAR_BG)
            )
            if self.underMouse() and not (self._selected or self.isChecked()):
                fade_color = QColor(_mix_hex(_SIDEBAR_BG, _TEXT, 0.04))
            clear = QColor(fade_color)
            clear.setAlpha(0)
            gradient.setColorAt(0.0, clear)
            gradient.setColorAt(0.72, fade_color)
            gradient.setColorAt(1.0, fade_color)
            painter.fillRect(fade_left, rect.top(), text_rect.right() - fade_left + 1, rect.height(), gradient)

        painter.end()


class _ConversationSidebarRow(QWidget):
    """Sidebar row with a full-width title and an overlaid options button."""

    def __init__(
        self,
        title_btn: QPushButton,
        menu_btn: QPushButton,
        *,
        compact: bool = False,
    ) -> None:
        """Initialize the conversation sidebar row instance."""
        super().__init__()
        self.setFixedHeight(36 if compact else 52)
        self.setStyleSheet("background: transparent;")
        self._title_btn = title_btn
        self._menu_btn = menu_btn
        self._title_btn.setParent(self)
        self._menu_btn.setParent(self)
        self._layout_children()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        """Resize event."""
        super().resizeEvent(event)
        self._layout_children()

    def _layout_children(self) -> None:
        """Handle layout children for conversation sidebar row."""
        self._title_btn.setGeometry(self.rect())
        self._menu_btn.setGeometry(
            max(0, self.width() - _SIDEBAR_MENU_W - 4),
            0,
            _SIDEBAR_MENU_W,
            self.height(),
        )
        self._menu_btn.raise_()


def _merge_display_segments(segments: list[tuple[str, bool]], text: str, is_thought: bool) -> list[tuple[str, bool]]:
    """Merge display segments."""
    if not text:
        return segments
    if segments and segments[-1][1] == is_thought:
        segments[-1] = (segments[-1][0] + text, is_thought)
    else:
        segments.append((text, is_thought))
    return segments


def _normalized_display_segments(items: object) -> list[tuple[str, bool]]:
    """Normalize persisted or streamed chronological assistant segments."""
    if not isinstance(items, list):
        return []
    segments: list[tuple[str, bool]] = []
    for raw in items:
        if isinstance(raw, dict):
            text = str(raw.get("text") or "")
            is_thought = bool(raw.get("is_thought"))
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            text = str(raw[0] or "")
            is_thought = bool(raw[1])
        else:
            continue
        _merge_display_segments(segments, text, is_thought)
    return segments


def _normalized_addon_message_actions(actions: list[dict] | None) -> list[dict]:
    """Keep only safe action fields consumed by the chat surface."""
    normalized: list[dict] = []
    for item in actions or []:
        if not isinstance(item, dict):
            continue
        addon_id = str(item.get("addon_id") or "").strip()
        action_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        role = str(item.get("role") or "assistant").strip().lower()
        if addon_id and action_id and label and role in {"assistant", "user", "all"}:
            normalized.append({
                "addon_id": addon_id,
                "id": action_id,
                "label": label,
                "role": role,
                "presentation": bool(item.get("presentation")),
                "auto": bool(item.get("auto")),
                "provider": str(item.get("provider") or "").strip(),
                "model": str(item.get("model") or "").strip(),
            })
    return normalized[:12]


def _formatted_replies_ui_enabled(actions: list[dict]) -> bool:
    """Return whether the formatted-replies addon owns this Chat presentation."""
    return any(
        str(item.get("addon_id") or "") == _FORMATTED_REPLIES_ADDON_ID
        for item in actions
        if isinstance(item, dict)
    )


def _segments_to_display_content(segments: list[tuple[str, bool]]) -> str:
    """Serialize chronological activity for OpenWand's tagged history renderer."""
    return "".join(f"<thought>{text}</thought>" if is_thought else text for text, is_thought in segments)


class _PendingSidebarRows(list):
    """History button list that finishes its queued rows before being read.

    The sidebar builds only a screenful of rows up front so a long history cannot
    delay the window appearing. Anything reading the button list -- selection
    highlighting, renames, tests -- expects every conversation to be present, so a
    read flushes whatever is still queued first.
    """

    def __init__(self, window: ChatWindow) -> None:
        """Initialize the button list bound to its chat window."""
        super().__init__()
        self._window = window

    def _flush(self) -> None:
        """Build any history rows still waiting on the next frame."""
        window = self._window
        if window is not None and window.__dict__.get("_pending_sidebar_rows"):
            window._fill_pending_sidebar_rows()

    def __getitem__(self, index):
        """Return a history button, building queued rows first."""
        self._flush()
        return list.__getitem__(self, index)

    def __len__(self) -> int:
        """Report the full row count rather than the pre-fill one."""
        self._flush()
        return list.__len__(self)

    def __iter__(self):
        """Iterate every history row, including ones still queued."""
        self._flush()
        return list.__iter__(self)

    def __eq__(self, other) -> bool:
        """Compare against the complete row list."""
        self._flush()
        return list.__eq__(self, other)

    def __ne__(self, other) -> bool:
        """Compare against the complete row list."""
        self._flush()
        return list.__ne__(self, other)

    __hash__ = None  # type: ignore[assignment]


class _ComposerTextEdit(QTextEdit):
    """Multiline composer that grows naturally and routes rich paste/drop as attachments."""

    attachment_mime = Signal(object)

    def __init__(self, *, minimum_height: int, maximum_height: int, parent=None) -> None:
        super().__init__(parent)
        self._minimum_composer_height = int(minimum_height)
        self._maximum_composer_height = int(maximum_height)
        self.setMinimumHeight(self._minimum_composer_height)
        self.setMaximumHeight(self._maximum_composer_height)
        self.setAcceptDrops(True)
        self.textChanged.connect(self.sync_composer_height)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: QTimer.singleShot(0, self.sync_composer_height)
        )

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802 - Qt API
        if source is not None and (source.hasImage() or source.hasUrls()):
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - Qt API
        if source is not None and (source.hasImage() or source.hasUrls()):
            self.attachment_mime.emit(source)
            return
        super().insertFromMimeData(source)

    def sync_composer_height(self) -> None:
        """Fit visible lines up to the cap, then enable an internal scrollbar."""
        try:
            document_height = float(self.document().documentLayout().documentSize().height())
        except RuntimeError:
            return
        desired = max(self._minimum_composer_height, int(document_height + 14))
        height = min(self._maximum_composer_height, desired)
        if self.height() != height:
            self.setFixedHeight(height)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if desired > self._maximum_composer_height
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        QTimer.singleShot(0, self.sync_composer_height)


class ChatWindow(QWidget):
    """Qt window for chat window."""
    def __init__(
        self,
        conversations: list[list[dict]],
        send_fn,
        auto_message: str | None = None,
        start_new: bool = False,
        projects: list[dict] | None = None,
        active_project_id: str | None = None,
        on_project_change=None,
        on_new_project=None,
        persist_fn=None,
        active_idx: int | None = None,
        on_select=None,
        on_context_preview=None,
        on_context_capture=None,
        on_addon_message_action=None,
        on_addon_settings=None,
        on_addon_setting_change=None,
        on_model_settings=None,
        addon_message_actions: list[dict] | None = None,
        window_state_path: Path | None = None,
    ):
        """
        Args:
            conversations: Direct reference to the app's list of all past
                           conversations. Each item is a dict with keys
                           ``"messages"`` (list of role/content turns) and
                           ``"context"`` (ambient context string).
            send_fn:       Callable yielding text chunks and optional final text events.
            auto_message:  If set, automatically sent when the window opens.
            projects:      List of {"id", "name"} dicts for the project selector.
            active_project_id: Project new conversations are filed under.
            on_project_change: Callable(project_id) invoked when the user picks
                           a different project (e.g. to scope memory).
            on_new_project: Callable(name) -> project dict, creating + persisting
                           a project; returns the new project.
            persist_fn:    Callable() invoked after a reply lands to save chats.
            active_idx:    Index of the conversation to select on open (the one
                           hotkey/voice prompts currently continue).
            on_select:     Callable(idx) invoked when the user selects or starts a
                           conversation, so the app can retarget hotkey prompts.
            on_context_preview: Callable(payload) invoked to refresh token
                           estimates for visible context controls.
            on_context_capture: Callable(payload) invoked when a context chip
                           needs an interactive capture before it can turn on.
            on_addon_message_action: Callable(payload) invoked when the user asks
                           an addon to process one stored chat message.
            on_addon_settings: Callable(addon_id) invoked by a message-level
                           shortcut to that addon's settings.
            on_addon_setting_change: Callable(payload) invoked when a chat-level
                           add-on preference is changed from the composer menu.
            on_model_settings: Callable invoked by the composer footer shortcut
                to the application's model selection page.
            addon_message_actions: Enabled addon actions known before first
                           paint. Their presence selects the addon-owned chat UI.
            window_state_path: Optional geometry-state file override, primarily
                           for isolated tests and portable embeddings.
        """
        super().__init__()
        # The polished workspace is the default Chat UI.  It must not depend on
        # the retired formatted-replies addon being installed or enabled.
        self._addon_message_actions = [
            action
            for action in _normalized_addon_message_actions(addon_message_actions)
            if str(action.get("addon_id") or "") != _FORMATTED_REPLIES_ADDON_ID
        ]
        self._formatted_replies_ui_enabled = True
        self._pending_addon_ui_refresh = False
        _refresh_chat_palette(self._formatted_replies_ui_enabled)
        self._conversations = conversations  # live reference - NOT a copy
        for conv in self._conversations:
            if isinstance(conv, dict):
                _ensure_conversation_metadata(conv)
        self._send_fn = send_fn
        self._on_select = on_select
        self._on_context_preview = on_context_preview
        self._on_context_capture = on_context_capture
        self._on_addon_message_action = on_addon_message_action
        self._on_addon_settings = on_addon_settings
        self._on_addon_setting_change = on_addon_setting_change
        self._on_model_settings = on_model_settings
        self._projects = list(projects or [])
        if not any(p.get("id") == _GENERAL_PROJECT_ID for p in self._projects):
            self._projects.insert(0, {"id": _GENERAL_PROJECT_ID, "name": t("General")})
        self._active_project_id = active_project_id or _GENERAL_PROJECT_ID
        self._on_project_change = on_project_change
        self._on_new_project = on_new_project
        self._persist_fn = persist_fn
        self._streaming = False
        # The conversation index that currently owns the single _current_ai_*
        # streaming buffer. When one query is still in-flight while a newer query
        # starts streaming, the older one's late chunks target a different index;
        # they are dropped here so they can't be appended into the active bubble.
        # Their final text still lands via add_conversation.
        self._streaming_idx: int | None = None
        self._font_scale = max(0.7, min(float(getattr(config, "CHAT_FONT_SCALE", 1.0) or 1.0), 2.5))
        self._enter_sends = bool(getattr(config, "CHAT_ENTER_SEND", True))
        self._font_scale_save_timer = QTimer(self)
        self._font_scale_save_timer.setSingleShot(True)
        self._font_scale_save_timer.setInterval(600)
        self._font_scale_save_timer.timeout.connect(
            lambda: config.set_chat_font_scale(self._font_scale)
        )
        self._window_state_path = Path(window_state_path) if window_state_path else _CHAT_WINDOW_STATE_FILE
        self._window_state_enabled = bool(
            window_state_path is not None or not os.environ.get("PYTEST_CURRENT_TEST")
        )
        self._window_geometry_ready = False
        self._window_geometry_save_timer = QTimer(self)
        self._window_geometry_save_timer.setSingleShot(True)
        self._window_geometry_save_timer.setInterval(_CHAT_WINDOW_GEOMETRY_SAVE_DELAY_MS)
        self._window_geometry_save_timer.timeout.connect(self._save_window_state)
        self._current_ai_label: _MessageTextView | None = None
        self._current_ai_text = ""
        self._current_ai_reply_text = ""
        self._current_ai_segments: list[tuple[str, bool]] = []
        self._current_ai_status_text = ""
        self._current_ai_parser: ThoughtStreamParser | None = None
        self._current_ai_annotations: list[dict] = []
        self._current_ai_attachments: list[dict] = []
        self._current_file_context: list[dict] = []
        self._current_tool_context: dict = {}
        self._current_context_snippets: list[dict] = []
        self._current_harness: dict = {}
        self._harness_activity_state: dict[str, object] = {
            "skills": [],
            "agents": {},
        }
        self._harness_inspector: QDialog | None = None
        self._current_local_work_dialog: LocalWorkProgressDialog | None = None
        self._current_local_work_notice: QLabel | None = None
        self._local_work_dialogs: list[LocalWorkProgressDialog] = []
        self._current_user_message: dict | None = None
        self._pending_attachment_context = ""
        self._pending_attachment_image_b64: str | None = None
        self._pending_attachments: list[dict] = []
        self._pending_attachment_labels: list[str] = []
        self._attachment_label: QLabel | None = None
        self._attach_btn: QPushButton | None = None
        self._context_controls: dict[str, QPushButton] = {}
        self._context_control_options: dict[str, list[tuple[str, str]]] = {}
        self._context_control_labels: dict[str, str] = {}
        self._context_control_keys: dict[str, str] = {}
        self._context_control_tokens: dict[str, str] = {}
        self._context_control_warnings: dict[str, str] = {}
        self._context_controls_updating = False
        self._context_preview_id = ""
        self._conversation_menu: QMenu | None = None
        self._composer_menu: QMenu | None = None
        self._stream_follow_enabled = True
        self._stream_follow_idx: int | None = None
        self._unseen_reply_idx: int | None = None
        self._middle_autoscroll: dict[str, object] | None = None
        self._middle_autoscroll_timer = QTimer(self)
        self._middle_autoscroll_timer.setInterval(_CHAT_AUTOSCROLL_INTERVAL_MS)
        self._middle_autoscroll_timer.timeout.connect(self._tick_middle_autoscroll)
        self._external_sync_btns: dict[str, QPushButton] = {}
        self._external_sync_checkboxes: dict[str, QCheckBox] = {}
        self._external_sync_inflight: set[str] = set()
        self._external_sync_dialogs: dict[str, ExternalConversationImportDialog] = {}
        self._external_sync_reports: dict[str, object] = {}
        self._external_sync_state = load_external_sync_state()
        # History rows past the first screenful are queued here until the window
        # has painted; see _rebuild_sidebar and _fill_pending_sidebar_rows.
        self._pending_sidebar_rows: list[tuple[str, object]] = []
        self._sidebar_fill_scheduled = False
        self.setAcceptDrops(True)
        self._opened_with_explicit_active_idx = active_idx is not None
        if active_idx is not None and 0 <= active_idx < len(conversations):
            self._active_idx = active_idx
        else:
            self._active_idx = max(0, len(conversations) - 1)
        self._selected_conversation_indices: set[int] = (
            {self._active_idx} if conversations else set()
        )
        self._selection_anchor_idx: int | None = self._active_idx if conversations else None
        self._built_pages: set[int] = set()

        self._signals = _StreamSignals()
        self._signals.chunk.connect(self._on_chunk)
        self._signals.final.connect(self._on_final_text)
        self._signals.metadata.connect(self._on_metadata)
        self._signals.finished.connect(self._on_finished)
        self._signals.external_sync.connect(self._on_external_sync_finished)

        self.setWindowTitle(t("OpenWand Chat"))
        self.setWindowFlags(Qt.WindowType.Window)
        enable_standard_window_controls(self)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(f"background: {_BG}; color: {_TEXT};")
        self.setMinimumSize(_W, _H)
        self.resize(_W, _H)

        self._build_ui()
        self._external_sync_timer = QTimer(self)
        self._external_sync_timer.setInterval(_EXTERNAL_AUTO_SYNC_INTERVAL_MS)
        self._external_sync_timer.timeout.connect(self._run_external_auto_sync)
        self._external_sync_timer.start()
        QTimer.singleShot(0, self._run_external_auto_sync)
        self._restore_window_state()
        self._new_shortcut = QShortcut(QKeySequence.StandardKey.New, self)
        self._new_shortcut.activated.connect(self.start_new_conversation)
        self._history_search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._history_search_shortcut.activated.connect(self._focus_history_search)
        self._install_zoom_shortcuts()
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance()
        self._application_event_filter_installed = False
        if _app is not None:
            _app.installEventFilter(self)  # Ctrl+wheel zoom over the conversation
            self._application_event_filter_installed = True

        if start_new:
            QTimer.singleShot(0, lambda: self.start_new_conversation(auto_message=auto_message))
        elif conversations:
            if (
                self._opened_with_explicit_active_idx
                and self._on_select
                and 0 <= self._active_idx < len(self._conversations)
            ):
                self._on_select(self._active_idx)
            QTimer.singleShot(0, self.request_context_preview)
            if auto_message:
                QTimer.singleShot(120, lambda: self._send(auto_message))

    # ------------------------------------------------------------------ Build

    def _build_ui(self):
        """Build ui."""
        root = self.layout()
        if root is None:
            root = QVBoxLayout(self)
        else:
            while root.count():
                item = root.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._context_controls = {}
        self._context_control_options = {}
        self._context_control_labels = {}
        self._context_control_keys = {}
        if not self._formatted_replies_ui_enabled:
            root.addWidget(self._make_title_bar())
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(0 if self._formatted_replies_ui_enabled else 1)
        splitter.setStyleSheet(f"QSplitter::handle {{ background: {_BORDER}; }}")
        sidebar = self._make_sidebar()
        if self._formatted_replies_ui_enabled:
            sidebar.setMinimumWidth(260)
            sidebar.setMaximumWidth(260)
        splitter.addWidget(sidebar)
        splitter.addWidget(self._make_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        sidebar_width = 260 if self._formatted_replies_ui_enabled else 185
        splitter.setSizes([sidebar_width, _W - sidebar_width])
        root.addWidget(splitter, stretch=1)
        self._refresh_chat_scoped_controls()

    def _apply_addon_ui_mode(self) -> None:
        """Repaint Chat after formatted-replies gains or loses ownership."""
        if self._streaming:
            self._pending_addon_ui_refresh = True
            return
        self._pending_addon_ui_refresh = False
        input_text = self._input.toPlainText() if hasattr(self, "_input") else ""
        input_cursor = self._input.textCursor().position() if hasattr(self, "_input") else 0
        input_had_focus = bool(hasattr(self, "_input") and self._input.hasFocus())
        old_scroll_ratio: float | None = None
        if hasattr(self, "_stack"):
            current = self._stack.currentWidget()
            if isinstance(current, QScrollArea):
                bar = current.verticalScrollBar()
                old_scroll_ratio = bar.value() / max(1, bar.maximum())

        _refresh_chat_palette(self._formatted_replies_ui_enabled)
        self.setStyleSheet(f"background: {_BG}; color: {_TEXT};")
        self._built_pages.clear()
        self._build_ui()
        if input_text:
            self._input.setPlainText(input_text)
            cursor = self._input.textCursor()
            cursor.setPosition(min(input_cursor, len(input_text)))
            self._input.setTextCursor(cursor)
        self._refresh_attachment_label()
        if self._conversations:
            self._switch(self._active_idx)
        if input_had_focus:
            self._input.setFocus()
        if old_scroll_ratio is not None:
            def restore_scroll(ratio=old_scroll_ratio) -> None:
                try:
                    current = self._stack.currentWidget()
                    if isinstance(current, QScrollArea):
                        bar = current.verticalScrollBar()
                        bar.setValue(round(bar.maximum() * ratio))
                except RuntimeError:
                    return

            QTimer.singleShot(0, restore_scroll)

    def _make_title_bar(self) -> QWidget:
        """Create title bar."""
        bar = QWidget()
        bar.setObjectName("chatTitleBar")
        bar.setFixedHeight(38)
        bar.setStyleSheet(f"background: {_TITLE_BG}; border-bottom: 1px solid {_BORDER};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 0, 8, 0)
        title = QLabel(t("OpenWand Chat"))
        title.setFont(_ui_font(10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {_ACCENT}; background: transparent;")
        h.addWidget(title)
        h.addStretch()
        for provider in ("codex", "claude"):
            h.addWidget(self._make_external_auto_sync_checkbox(provider))
        return bar

    _NEW_PROJECT_SENTINEL = "__new_project__"

    def _project_display_name(self, project: dict | None, fallback: str | None = None) -> str:
        """Return a UI label for a project, translating the built-in General bucket."""
        project = project or {}
        if str(project.get("id") or "") == _GENERAL_PROJECT_ID:
            return t("General")
        name = str(project.get("name") or "").strip()
        return name or fallback or t("Project")

    def _make_new_chat_button(self) -> QPushButton:
        """Create the sidebar new-chat button."""
        new_chat = QPushButton(t("New chat"))
        new_chat.setFixedHeight(28)
        new_chat.setToolTip(t("Start a new conversation (Ctrl+N)"))
        new_chat.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT_BG_18}; color: {_ACCENT};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; font-size: 9pt;"
            " font-weight: 700; }}"
            f"QPushButton:hover {{ background: {_ACCENT_BG_28}; }}"
            f"QPushButton:disabled {{ color: {_DISABLED_TEXT}; border-color: {_WHITE_BG_10}; }}"
        )
        new_chat.clicked.connect(self.start_new_conversation)
        self._new_chat_btn = new_chat
        return new_chat

    def _make_project_selector(self) -> QWidget:
        """Dropdown that scopes new conversations (and memory) to a project."""
        combo = QComboBox()
        combo.setFixedHeight(26)
        combo.setMinimumWidth(120)
        combo.setToolTip(t("Project for new chats (memory is scoped per project)"))
        combo.setStyleSheet(
            f"QComboBox {{ background: {_ACCENT_BG_12}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; padding: 2px 8px;"
            " font-size: 9pt; }"
            f" QComboBox QAbstractItemView {{ background: {_TITLE_BG}; color: {_TEXT};"
            f" selection-background-color: {_SEL_BG}; }}"
        )
        self._project_combo = combo
        self._reload_project_combo()
        combo.currentIndexChanged.connect(self._on_project_selected)
        return combo

    def _reload_project_combo(self) -> None:
        """Handle reload project combo for chat window."""
        combo = self._project_combo
        combo.blockSignals(True)
        combo.clear()
        for proj in self._projects:
            combo.addItem(self._project_display_name(proj), proj.get("id"))
        combo.addItem(t("＋ New project…"), self._NEW_PROJECT_SENTINEL)
        idx = combo.findData(self._active_project_id)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _on_project_selected(self, _index: int) -> None:
        """Handle project selected events."""
        data = self._project_combo.currentData()
        if data == self._NEW_PROJECT_SENTINEL:
            self._create_project_interactive()
            return
        if not data or data == self._active_project_id:
            return
        self._active_project_id = data
        if self._on_project_change:
            self._on_project_change(data)

    def _create_project_interactive(self) -> None:
        """Create project interactive."""
        name, ok = QInputDialog.getText(self, t("New project"), t("Project name:"))
        name = (name or "").strip()
        if not ok or not name or self._on_new_project is None:
            # Revert the combo to the current project (user cancelled).
            self._reload_project_combo()
            return
        project = self._on_new_project(name)
        if project:
            if not any(p.get("id") == project.get("id") for p in self._projects):
                self._projects.append(project)
            self._active_project_id = project.get("id")
            if self._on_project_change:
                self._on_project_change(self._active_project_id)
        self._reload_project_combo()

    # ------------------------------------------------------------------ Sidebar

    def _make_sidebar(self) -> QWidget:
        """Create sidebar."""
        if self._formatted_replies_ui_enabled:
            return self._make_formatted_sidebar()
        sidebar = QWidget()
        sidebar.setMinimumWidth(100)
        sidebar.setStyleSheet(f"background: {_SIDEBAR_BG};")
        vl = QVBoxLayout(sidebar)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        hdr = QLabel(t("  History"))
        hdr.setFixedHeight(32)
        hdr.setStyleSheet(
            f"background: {_SIDEBAR_BG}; color: {_HINT}; font-size: 9pt;"
            f" font-weight: bold; border-bottom: 1px solid {_BORDER};"
        )
        vl.addWidget(hdr)

        controls = QWidget()
        controls.setStyleSheet(f"background: {_SIDEBAR_BG}; border-bottom: 1px solid {_BORDER};")
        controls_l = QVBoxLayout(controls)
        controls_l.setContentsMargins(8, 8, 8, 8)
        controls_l.setSpacing(6)
        controls_l.addWidget(self._make_project_selector())
        controls_l.addWidget(self._make_new_chat_button())
        for provider in ("codex", "claude"):
            controls_l.addWidget(self._make_external_sync_button(provider))
        controls_l.addWidget(self._make_delete_all_conversations_button())
        vl.addWidget(controls)

        self._history_search = QLineEdit()
        self._history_search.setFixedHeight(30)
        self._history_search.setPlaceholderText(t("Search conversations..."))
        self._history_search.setToolTip(
            t("Search titles, projects, and messages (Ctrl+K)")
        )
        self._history_search.setAccessibleName(t("Search conversations"))
        self._history_search.setClearButtonEnabled(True)
        self._history_search.setStyleSheet(
            f"QLineEdit {{ background: {_ACCENT_BG_10}; color: {_TEXT};"
            f" border: none; border-bottom: 1px solid {_BORDER};"
            " padding: 3px 9px; font-size: 9pt; }}"
            f"QLineEdit:focus {{ background: {_ACCENT_BG_18};"
            f" border-bottom: 1px solid {_ACCENT}; }}"
        )
        self._history_search.textChanged.connect(self._rebuild_sidebar)
        vl.addWidget(self._history_search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"background: {_SIDEBAR_BG};")

        self._sidebar_items = QWidget()
        self._sidebar_items.setStyleSheet(f"background: {_SIDEBAR_BG};")
        self._sidebar_layout = QVBoxLayout(self._sidebar_items)
        self._sidebar_layout.setContentsMargins(0, 4, 0, 4)
        self._sidebar_layout.setSpacing(1)
        self._sidebar_btns: list[tuple[int, QPushButton]] = _PendingSidebarRows(self)
        self._rebuild_sidebar()

        scroll.setWidget(self._sidebar_items)
        vl.addWidget(scroll, stretch=1)
        return sidebar

    def _make_formatted_sidebar(self) -> QWidget:
        """Build the ChatGPT-like navigation rail from the approved prototype."""
        sidebar = QWidget()
        sidebar.setObjectName("formattedChatSidebar")
        sidebar.setStyleSheet(
            f"QWidget#formattedChatSidebar {{ background: {_SIDEBAR_BG};"
            f" border-right: 1px solid {_BORDER}; }}"
        )
        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(4)

        new_chat = QPushButton(f"＋  {t('New chat')}")
        new_chat.setObjectName("formattedNewChat")
        new_chat.setFixedHeight(36)
        new_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        new_chat.setToolTip(t("Start a new conversation (Ctrl+N)"))
        new_chat.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_TEXT}; border: none;"
            " border-radius: 9px; text-align: left; padding: 0 10px; font-size: 10pt; }}"
            f"QPushButton:hover {{ background: {_WHITE_BG_10}; }}"
            f"QPushButton:disabled {{ color: {_DISABLED_TEXT}; }}"
        )
        new_chat.clicked.connect(self.start_new_conversation)
        self._new_chat_btn = new_chat
        outer.addWidget(new_chat)

        project_selector = self._make_project_selector()
        project_selector.setFixedHeight(34)
        project_selector.setToolTip(t("Project for new chats"))
        outer.addWidget(project_selector)

        sources_toggle = QPushButton()
        sources_toggle.setObjectName("formattedSourcesToggle")
        sources_toggle.setCheckable(True)
        sources_toggle.setChecked(bool(getattr(self, "_sources_expanded", True)))
        sources_toggle.setFixedHeight(30)
        sources_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        sources_toggle.setAccessibleName(t("Show or hide conversation sources"))
        sources_toggle.setStyleSheet(
            f"QPushButton {{ color: {_HINT}; background: transparent; border: none;"
            " border-radius: 7px; text-align: left; padding: 0 10px; font-size: 8pt; }}"
            f"QPushButton:hover {{ color: {_TEXT}; background: {_WHITE_BG_10}; }}"
        )
        outer.addWidget(sources_toggle)

        sources_container = QWidget()
        sources_container.setObjectName("formattedSourcesContainer")
        sources_layout = QVBoxLayout(sources_container)
        sources_layout.setContentsMargins(2, 0, 2, 2)
        sources_layout.setSpacing(4)
        for provider in ("codex", "claude"):
            import_button = self._make_external_sync_button(provider)
            import_button.setFixedHeight(32)
            sources_layout.addWidget(import_button)
            sources_layout.addWidget(self._make_external_auto_sync_checkbox(provider))
        outer.addWidget(sources_container)
        self._sources_toggle = sources_toggle
        self._sources_container = sources_container
        sources_toggle.toggled.connect(self._set_sources_expanded)
        self._set_sources_expanded(sources_toggle.isChecked())

        self._sidebar_search = QLineEdit()
        self._history_search = self._sidebar_search
        self._sidebar_search.setPlaceholderText(t("Search chats"))
        self._sidebar_search.setClearButtonEnabled(True)
        self._sidebar_search.setFixedHeight(36)
        self._sidebar_search.setStyleSheet(
            f"QLineEdit {{ background: {_WHITE_BG_8}; color: {_TEXT};"
            f" border: 1px solid transparent; border-radius: 9px; padding: 0 10px; }}"
            f"QLineEdit:focus {{ border-color: {_BORDER}; background: {_AI_BG}; }}"
        )
        self._sidebar_search.textChanged.connect(self._rebuild_sidebar)
        outer.addWidget(self._sidebar_search)

        history_label = QLabel(t("Chats"))
        history_label.setFixedHeight(32)
        history_label.setStyleSheet(
            f"color: {_HINT}; background: transparent; padding: 8px 10px 0 10px; font-size: 8pt;"
        )
        outer.addWidget(history_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_SIDEBAR_BG}; border: none; }}"
            "QScrollBar:vertical { width: 8px; background: transparent; }"
            f"QScrollBar::handle:vertical {{ background: {_BORDER}; border-radius: 4px; min-height: 28px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        self._sidebar_items = QWidget()
        self._sidebar_items.setStyleSheet(f"background: {_SIDEBAR_BG};")
        self._sidebar_layout = QVBoxLayout(self._sidebar_items)
        self._sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self._sidebar_layout.setSpacing(2)
        self._sidebar_btns = _PendingSidebarRows(self)
        self._rebuild_sidebar()
        scroll.setWidget(self._sidebar_items)
        outer.addWidget(scroll, stretch=1)

        return sidebar

    def _set_sources_expanded(self, expanded: bool) -> None:
        """Show or hide the global transcript-import controls."""
        self._sources_expanded = bool(expanded)
        toggle = getattr(self, "_sources_toggle", None)
        container = getattr(self, "_sources_container", None)
        if toggle is not None:
            toggle.setChecked(self._sources_expanded)
            arrow = "▾" if self._sources_expanded else "▸"
            toggle.setText(f"{arrow}  {t('Sources')}")
            toggle.setToolTip(
                t("Collapse conversation sources")
                if self._sources_expanded
                else t("Expand conversation sources")
            )
        if container is not None:
            container.setVisible(self._sources_expanded)

    def _filter_formatted_sidebar(self, query: str) -> None:
        """Filter the approved-mode history list without changing its data."""
        needle = str(query or "").strip().casefold()
        for _idx, button in self._sidebar_btns:
            row = button.parentWidget()
            title = str(button.accessibleName() or button.toolTip() or "").casefold()
            if row is not None:
                row.setVisible(not needle or needle in title)

    def _sidebar_selection_order(self) -> list[int]:
        """Return selectable conversation indices in their current visual order."""
        order: list[int] = []
        for _project_id, _project_name, indices in self._grouped_sidebar_indices():
            for idx in indices:
                if not self._conversation_matches_search(idx):
                    continue
                order.append(idx)
        return order

    def _select_sidebar_conversation(self, idx: int) -> None:
        """Apply Explorer-style click selection, then display the clicked chat."""
        if not (0 <= idx < len(self._conversations)):
            return
        modifiers = QApplication.keyboardModifiers()
        control = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        selected = {
            item
            for item in self._selected_conversation_indices
            if 0 <= item < len(self._conversations)
        }

        order = self._sidebar_selection_order()
        anchor = self._selection_anchor_idx
        if shift and anchor in order and idx in order:
            start, end = sorted((order.index(anchor), order.index(idx)))
            range_selection = set(order[start : end + 1])
            selected = selected | range_selection if control else range_selection
        elif control:
            if idx in selected:
                selected.remove(idx)
            else:
                selected.add(idx)
            self._selection_anchor_idx = idx
        else:
            selected = {idx}
            self._selection_anchor_idx = idx

        self._selected_conversation_indices = selected
        self._switch(idx, preserve_selection=True)

    def _make_external_sync_button(self, provider: str) -> QPushButton:
        """Create one provider-specific, preview-before-import button."""
        provider = str(provider or "").strip().lower()
        provider_name = _external_provider_display_name(provider)
        button = QPushButton(
            t("Import local {provider} history…").format(provider=provider_name)
        )
        button.setObjectName(f"externalImport{provider.title()}")
        button.setFixedHeight(28)
        button.setToolTip(
            t("Scan and choose which local {provider} transcript sessions to import.").format(
                provider=provider_name
            )
        )
        button.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_HINT};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; font-size: 8pt; }}"
            f"QPushButton:hover {{ background: {_WHITE_BG_10}; color: {_TEXT}; }}"
            f"QPushButton:disabled {{ color: {_DISABLED_TEXT}; }}"
        )
        button.clicked.connect(lambda _checked=False, p=provider: self._pull_external_conversations(p))
        self._external_sync_btns[provider] = button
        return button

    def _make_external_auto_sync_checkbox(self, provider: str) -> QCheckBox:
        """Create one persistent from-now-on automatic-sync switch."""
        provider = str(provider or "").strip().lower()
        provider_name = _external_provider_display_name(provider)
        checkbox = QCheckBox(
            t("Auto-import {provider}").format(provider=provider_name)
        )
        checkbox.setObjectName(f"externalAutoSync{provider.title()}")
        checkbox.setToolTip(
            t(
                "Import new or updated local {provider} conversations while chat is open. "
                "This is pull-only local transcript import, not two-way web-chat synchronization. "
                "Older conversations are not imported automatically."
            ).format(provider=provider_name)
        )
        checkbox.setAccessibleName(
            t("Automatically import local {provider} history").format(provider=provider_name)
        )
        checkbox.setStyleSheet(
            f"QCheckBox {{ color: {_HINT}; padding: 1px 7px; font-size: 8pt; }}"
            f"QCheckBox:hover {{ color: {_TEXT}; }}"
            f"QCheckBox:checked {{ color: {_ACCENT}; }}"
        )
        checkbox.setChecked(
            bool((self._external_sync_state.get(provider) or {}).get("enabled"))
        )
        checkbox.toggled.connect(
            lambda enabled, p=provider: self._set_external_auto_sync(p, enabled)
        )
        self._external_sync_checkboxes[provider] = checkbox
        return checkbox

    def _make_delete_all_conversations_button(self) -> QPushButton:
        """Create an explicit, guarded bulk-history delete control."""
        button = QPushButton(t("Delete all conversations"))
        danger_hover_bg = _mix_hex(_SIDEBAR_BG, "#be4637", 0.14)
        button.setObjectName("deleteAllConversationsButton")
        button.setFixedHeight(34 if self._formatted_replies_ui_enabled else 28)
        button.setEnabled(bool(self._conversations))
        button.setToolTip(t("Delete every OpenWand conversation after confirmation"))
        button.setAccessibleName(t("Delete all conversations"))
        if self._formatted_replies_ui_enabled:
            button.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {_HINT}; border: none;"
                " border-radius: 9px; text-align: left; padding: 0 10px; font-size: 8pt; }}"
                f"QPushButton:hover {{ background: {danger_hover_bg}; color: #ef9a8d; }}"
                f"QPushButton:disabled {{ color: {_DISABLED_TEXT}; background: transparent; }}"
            )
        else:
            button.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {_HINT};"
                f" border: 1px solid {_BORDER}; border-radius: 6px; font-size: 8pt; }}"
                f"QPushButton:hover {{ background: {danger_hover_bg}; color: #ef9a8d; }}"
                f"QPushButton:disabled {{ color: {_DISABLED_TEXT}; }}"
            )
        button.clicked.connect(self._delete_all_conversations)
        self._delete_all_conversations_btn = button
        return button

    def _pull_external_conversations(self, provider: str) -> None:
        """Scan one provider; selection and mutation happen after preview."""
        self._start_external_sync(provider, automatic=False)

    def _set_external_auto_sync(self, provider: str, enabled: bool) -> None:
        """Persist a provider switch and start its first no-backfill scan."""
        provider = str(provider or "").strip().lower()
        checkbox = self._external_sync_checkboxes.get(provider)
        try:
            self._external_sync_state = set_external_auto_sync(provider, enabled)
        except Exception as exc:
            if checkbox is not None:
                checkbox.blockSignals(True)
                checkbox.setChecked(not enabled)
                checkbox.blockSignals(False)
            QMessageBox.warning(
                self,
                t("External conversation import failed"),
                t("OpenWand could not save automatic sync: {error}").format(error=exc),
            )
            return
        if enabled:
            self._start_external_sync(provider, automatic=True)

    def _run_external_auto_sync(self) -> None:
        """Scan each enabled provider without showing import dialogs."""
        for provider in ("codex", "claude"):
            if bool((self._external_sync_state.get(provider) or {}).get("enabled")):
                self._start_external_sync(provider, automatic=True)

    def _start_external_sync(self, provider: str, *, automatic: bool) -> None:
        provider = str(provider or "").strip().lower()
        if provider not in {"codex", "claude"} or provider in self._external_sync_inflight:
            return
        self._external_sync_inflight.add(provider)
        button = self._external_sync_btns.get(provider)
        provider_name = _external_provider_display_name(provider)
        if not automatic and button is not None:
            button.setEnabled(False)
            button.setText(t("Scanning {provider}…").format(provider=provider_name))
        if not automatic:
            picker = ExternalConversationImportDialog(
                provider,
                [],
                self,
                scanning=True,
            )
            self._external_sync_dialogs[provider] = picker
            picker.accepted.connect(
                lambda p=provider, dialog=picker: self._import_external_picker(p, dialog)
            )
            picker.rejected.connect(
                lambda p=provider, dialog=picker: self._discard_external_picker(p, dialog)
            )
            # open() is non-blocking: the picker becomes visible before transcript
            # parsing starts, then receives discoveries through the Qt signal below.
            picker.open()
        since = str((self._external_sync_state.get(provider) or {}).get("since") or "")
        threading.Thread(
            target=self._external_sync_worker,
            args=(provider, automatic, since),
            daemon=True,
            name=f"{provider}-conversation-scan",
        ).start()

    def _external_sync_worker(self, provider: str, automatic: bool, since: str) -> None:
        """Read external transcript files away from the Qt UI thread."""
        try:
            def publish_discovery(conversation: dict) -> None:
                if automatic:
                    return
                try:
                    self._signals.external_sync.emit(
                        {
                            "event": "discovered",
                            "provider": provider,
                            "automatic": False,
                            "conversation": conversation,
                        }
                    )
                except RuntimeError:
                    pass

            discovered, report = discover_external_conversations(
                provider=provider,
                on_discovered=publish_discovery if not automatic else None,
            )
            if automatic:
                discovered = external_conversations_since(discovered, since)
            payload = {
                "event": "finished",
                "provider": provider,
                "automatic": automatic,
                "discovered": discovered,
                "report": report,
            }
        except Exception as exc:
            payload = {"provider": provider, "automatic": automatic, "error": str(exc)}
        try:
            self._signals.external_sync.emit(payload)
        except RuntimeError:
            pass

    def _on_external_sync_finished(self, payload: object) -> None:
        """Route streamed discoveries and merge a completed background pull."""
        result = payload if isinstance(payload, dict) else {}
        provider = str(result.get("provider") or "").strip().lower()
        if result.get("event") == "discovered":
            picker = self._external_sync_dialogs.get(provider)
            conversation = result.get("conversation")
            if picker is not None and isinstance(conversation, dict):
                picker.append_conversation(conversation)
            return
        provider_name = _external_provider_display_name(provider)
        automatic = bool(result.get("automatic"))
        button = self._external_sync_btns.get(provider)
        checkbox = self._external_sync_checkboxes.get(provider)
        try:
            if result.get("error"):
                raise RuntimeError(str(result["error"]))
            discovered = list(result.get("discovered") or [])
            if automatic:
                if checkbox is not None:
                    checkbox.setToolTip(
                        t(
                            "Import new or updated local {provider} conversations while chat is open. "
                            "Older conversations are not imported automatically."
                        ).format(provider=provider_name)
                    )
                if not discovered:
                    return
                report = apply_external_conversations(
                    self._conversations,
                    discovered,
                    report=result.get("report"),
                )
                if report.changed:
                    self._persist()
                    self._rebuild_stack()
                    self._rebuild_sidebar()
                    if self._conversations:
                        self._switch(min(self._active_idx, len(self._conversations) - 1))
                return
            picker = self._external_sync_dialogs.get(provider)
            if picker is not None:
                self._external_sync_reports[provider] = result.get("report")
                picker.finish_scan()
        except Exception as exc:
            if automatic:
                if checkbox is not None:
                    checkbox.setToolTip(
                        t("Automatic sync failed: {error}").format(error=exc)
                    )
                return
            picker = self._external_sync_dialogs.get(provider)
            if picker is not None:
                picker.finish_scan(str(exc))
            QMessageBox.warning(
                self,
                t("External conversation import failed"),
                t("OpenWand could not scan local {provider} conversations: {error}").format(
                    provider=provider_name,
                    error=exc,
                ),
            )
        finally:
            self._external_sync_inflight.discard(provider)
            if not automatic and button is not None:
                button.setText(
                    t("Import local {provider} history…").format(provider=provider_name)
                )
                button.setEnabled(True)

    def _discard_external_picker(
        self,
        provider: str,
        picker: ExternalConversationImportDialog,
    ) -> None:
        """Forget a picker the user closed while its scan may still be running."""
        if self._external_sync_dialogs.get(provider) is picker:
            self._external_sync_dialogs.pop(provider, None)
            self._external_sync_reports.pop(provider, None)
        picker.deleteLater()

    def _import_external_picker(
        self,
        provider: str,
        picker: ExternalConversationImportDialog,
    ) -> None:
        """Apply choices from a completed streaming picker."""
        if self._external_sync_dialogs.get(provider) is not picker:
            return
        self._external_sync_dialogs.pop(provider, None)
        source_report = self._external_sync_reports.pop(provider, None)
        selected = picker.selected_conversations()
        picker.deleteLater()
        if not selected:
            return
        try:
            report = apply_external_conversations(
                self._conversations,
                selected,
                report=source_report,
            )
            if report.changed:
                self._persist()
                self._rebuild_stack()
                self._rebuild_sidebar()
                if self._conversations:
                    self._switch(min(self._active_idx, len(self._conversations) - 1))
            summary = t("Imported {imported}, updated {updated}, unchanged {unchanged}.").format(
                imported=report.imported,
                updated=report.updated,
                unchanged=report.unchanged,
            )
            if report.errors:
                summary += " " + t("{count} transcript(s) could not be read.").format(
                    count=len(report.errors)
                )
            QMessageBox.information(self, t("External conversation import"), summary)
        except Exception as exc:
            QMessageBox.warning(
                self,
                t("External conversation import failed"),
                t("OpenWand could not import local {provider} conversations: {error}").format(
                    provider=_external_provider_display_name(provider),
                    error=exc,
                ),
            )

    def _rebuild_sidebar(self):
        """Handle rebuild sidebar for chat window."""
        while self._sidebar_layout.count():
            item = self._sidebar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        list.clear(self._sidebar_btns)
        self._pending_sidebar_rows = []

        if not self._conversations:
            lbl = QLabel(t("  No history yet."))
            lbl.setStyleSheet(
                f"color: {_HINT}; font-size: 9pt; padding: 8px; background: transparent;"
            )
            self._sidebar_layout.addWidget(lbl)
        else:
            ops: list[tuple[str, object]] = []
            rows_added = False
            for project_id, project_name, indices in self._grouped_sidebar_indices():
                indices = [idx for idx in indices if self._conversation_matches_search(idx)]
                if not indices:
                    continue
                if project_id != _GENERAL_PROJECT_ID:
                    ops.append(("header", project_name))
                elif rows_added:
                    ops.append(("spacing", None))
                ops.extend(("row", real_idx) for real_idx in indices)
                rows_added = True
            if not rows_added:
                lbl = QLabel(t("No matching conversations."))
                lbl.setStyleSheet(
                    f"color: {_HINT}; font-size: 9pt; padding: 8px; background: transparent;"
                )
                self._sidebar_layout.addWidget(lbl)
            else:
                # Build just enough rows to fill the visible sidebar. A long history
                # otherwise costs seconds of widget construction before the window
                # can appear at all; the rest lands right after the first frame.
                built = 0
                cut = len(ops)
                for position, op in enumerate(ops):
                    if op[0] == "row" and built >= _SIDEBAR_INITIAL_ROWS:
                        cut = position
                        break
                    self._apply_sidebar_op(op)
                    if op[0] == "row":
                        built += 1
                self._pending_sidebar_rows = ops[cut:]
        self._sidebar_layout.addStretch()
        if hasattr(self, "_delete_all_conversations_btn"):
            self._delete_all_conversations_btn.setEnabled(bool(self._conversations))
            self._delete_all_conversations_btn.setVisible(bool(self._conversations))
        if self._pending_sidebar_rows and self.isVisible():
            # Already on screen (a search, rename, or delete rebuilt the list), so
            # there is no first frame left to wait for. On the initial build the
            # fill is started from paintEvent instead.
            self._schedule_sidebar_fill()

    def _apply_sidebar_op(self, op: tuple[str, object]) -> None:
        """Append one queued history entry to the sidebar."""
        kind, value = op
        if kind == "header":
            self._sidebar_layout.addWidget(self._make_sidebar_project_header(str(value)))
        elif kind == "spacing":
            self._sidebar_layout.addSpacing(_SIDEBAR_GENERAL_GROUP_GAP)
        else:
            real_idx = int(value)  # type: ignore[arg-type]
            row, title_btn = self._make_sidebar_row(real_idx, self._conversations[real_idx])
            self._sidebar_layout.addWidget(row)
            list.append(self._sidebar_btns, (real_idx, title_btn))

    def _schedule_sidebar_fill(self) -> None:
        """Queue the remaining history rows to be built after the next frame."""
        if self._sidebar_fill_scheduled:
            return
        self._sidebar_fill_scheduled = True
        QTimer.singleShot(0, self._fill_pending_sidebar_rows)

    def _fill_pending_sidebar_rows(self) -> None:
        """Build every history row that _rebuild_sidebar left queued."""
        self._sidebar_fill_scheduled = False
        ops = self._pending_sidebar_rows
        if not ops:
            return
        self._pending_sidebar_rows = []
        # The trailing stretch has to move back below the newly appended rows.
        last = self._sidebar_layout.count() - 1
        if last >= 0 and self._sidebar_layout.itemAt(last).spacerItem() is not None:
            self._sidebar_layout.takeAt(last)
        for op in ops:
            self._apply_sidebar_op(op)
        self._sidebar_layout.addStretch()

    def _focus_history_search(self) -> None:
        """Focus the history search without changing the selected conversation."""
        search = (
            self._sidebar_search
            if self._formatted_replies_ui_enabled
            else self._history_search
        )
        search.setFocus()
        search.selectAll()

    def _history_search_terms(self) -> list[str]:
        """Return case-insensitive terms from the current history query."""
        search = getattr(self, "_history_search", None)
        if search is None:
            return []
        return str(search.text() or "").casefold().split()

    def _conversation_project_name(self, conv: dict) -> str:
        """Return the searchable display name for a conversation's project."""
        project_id = str(conv.get("project_id") or _GENERAL_PROJECT_ID)
        project = next(
            (item for item in self._projects if str(item.get("id") or "") == project_id),
            None,
        )
        return self._project_display_name(project) if project else t("General")

    def _conversation_search_messages(self, conv: dict) -> list[str]:
        """Return plain transcript text suitable for local history filtering."""
        return [
            str(message.get("content") or "")
            for message in conv.get("messages", [])
            if isinstance(message, dict) and str(message.get("content") or "").strip()
        ]

    def _conversation_matches_search(self, idx: int) -> bool:
        """Match every search term across title, project, and transcript text."""
        terms = self._history_search_terms()
        if not terms or not (0 <= idx < len(self._conversations)):
            return not terms
        conv = self._conversations[idx]
        searchable = "\n".join(
            [
                self._conversation_title(idx, conv),
                self._conversation_project_name(conv),
                *self._conversation_search_messages(conv),
            ]
        ).casefold()
        return all(term in searchable for term in terms)

    def _conversation_search_excerpt(self, conv: dict) -> str:
        """Return a compact transcript excerpt explaining a content match."""
        terms = self._history_search_terms()
        if not terms:
            return ""
        for raw in self._conversation_search_messages(conv):
            text = " ".join(raw.split())
            folded = text.casefold()
            positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
            if not positions:
                continue
            start = max(0, min(positions) - 24)
            end = min(len(text), start + 72)
            excerpt = text[start:end].strip()
            return ("…" if start else "") + excerpt + ("…" if end < len(text) else "")
        return ""

    def _grouped_sidebar_indices(self) -> list[tuple[str, str, list[int]]]:
        """Return conversation indices grouped by project for the sidebar."""
        by_project: dict[str, list[int]] = {}
        valid_projects = {str(p.get("id") or "") for p in self._projects}
        for idx in range(len(self._conversations) - 1, -1, -1):
            raw_pid = str(self._conversations[idx].get("project_id") or _GENERAL_PROJECT_ID)
            project_id = raw_pid if raw_pid in valid_projects else _GENERAL_PROJECT_ID
            by_project.setdefault(project_id, []).append(idx)

        def sort_group(indices: list[int]) -> list[int]:
            # Pinned conversations float within their project; each subgroup is newest first.
            return sorted(indices, key=lambda ix: not self._conversations[ix].get("pinned"))

        groups: list[tuple[str, str, list[int]]] = []
        for proj in self._projects:
            project_id = str(proj.get("id") or "")
            if not project_id or project_id == _GENERAL_PROJECT_ID:
                continue
            indices = sort_group(by_project.pop(project_id, []))
            if indices:
                groups.append((project_id, self._project_display_name(proj), indices))

        general = sort_group(by_project.pop(_GENERAL_PROJECT_ID, []))
        for unknown_indices in by_project.values():
            general.extend(sort_group(unknown_indices))
        if general:
            groups.append((_GENERAL_PROJECT_ID, t("General"), general))
        return groups

    def _make_sidebar_project_header(self, name: str) -> QLabel:
        """Create a compact project heading for grouped history."""
        lbl = QLabel(f"  {name}")
        lbl.setFixedHeight(28)
        lbl.setStyleSheet(
            f"QLabel {{ background: {_PROJECT_HEADER_BG}; color: {_ACCENT};"
            f" border-top: 1px solid {_BORDER}; border-bottom: 1px solid {_BORDER};"
            " font-size: 8pt; font-weight: 700; padding-left: 2px; }}"
        )
        lbl.setToolTip(name)
        return lbl

    def _conversation_title(self, idx: int, conv: dict) -> str:
        """Handle conversation title for chat window."""
        override = str(conv.get("title_override") or "").strip()
        if override:
            return override
        first_user = next((m for m in conv["messages"] if m["role"] == "user"), None)
        source = conv.get("external_source") if isinstance(conv.get("external_source"), dict) else {}
        raw = (
            conv.get("title")
            if source and conv.get("title")
            else (first_user["content"] if first_user else f"{t('Conversation')} {idx+1}")
        )
        has_image = bool(first_user and _conversation_store.first_image_base64_from_message(first_user))
        prefix = ""
        if has_image:
            prefix += f"[{t('image')}] "
        return prefix + str(raw).strip().replace("\n", " ")

    def _conversation_timestamp(self, conv: dict) -> str:
        """Return display timestamp for a conversation."""
        return _format_conversation_datetime(conv.get("updated_at") or conv.get("created_at"))

    def _make_sidebar_row(self, idx: int, conv: dict) -> tuple[QWidget, QPushButton]:
        """Create sidebar row."""
        title = self._conversation_title(idx, conv)
        if conv.get("pinned"):
            title = "📌 " + title
        if self._formatted_replies_ui_enabled:
            subtitle = self._conversation_search_excerpt(conv) if self._history_search_terms() else ""
        else:
            subtitle = self._conversation_search_excerpt(conv) or self._conversation_timestamp(conv)
        is_active = (idx == self._active_idx)
        is_selected = idx in self._selected_conversation_indices

        btn = _ConversationTitleButton(
            title,
            subtitle,
            active=is_active,
            selected=is_selected,
            latest=idx == len(self._conversations) - 1,
        )
        btn.setToolTip("\n".join(part for part in (title, subtitle) if part))
        btn.clicked.connect(lambda _checked, ix=idx: self._select_sidebar_conversation(ix))

        menu_btn = QPushButton("⋮")
        menu_btn.setFixedSize(_SIDEBAR_MENU_W, 36 if self._formatted_replies_ui_enabled else 52)
        menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        menu_btn.setToolTip(t("Conversation options"))
        menu_btn.setAccessibleName(t("Conversation options"))
        menu_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_HINT}; border: none;"
            " font-size: 16pt;"
            " font-weight: 700; padding: 0; margin: 0; }"
            f"QPushButton:hover {{ background: {_WHITE_BG_12}; color: {_TEXT}; }}"
        )
        menu_btn.clicked.connect(
            lambda _checked, ix=idx, button=menu_btn: self._open_conversation_menu(ix, button)
        )
        row = _ConversationSidebarRow(
            btn,
            menu_btn,
            compact=self._formatted_replies_ui_enabled,
        )
        return row, btn

    def _open_conversation_menu(self, idx: int, anchor: QWidget | None = None) -> None:
        """Open conversation menu."""
        if not (0 <= idx < len(self._conversations)):
            return
        if idx not in self._selected_conversation_indices:
            self._selected_conversation_indices = {idx}
            self._selection_anchor_idx = idx
        self._switch(idx, preserve_selection=True)
        selected = sorted(self._selected_conversation_indices)
        multiple = len(selected) > 1
        conv = self._conversations[idx]
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {_TITLE_BG}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; }}"
            f"QMenu::item:selected {{ background: {_SEL_BG}; }}"
        )
        all_pinned = all(self._conversations[item].get("pinned") for item in selected)
        pin_label = t("Unpin") if all_pinned else t("Pin")
        if multiple:
            pin_label = t("Unpin selected") if all_pinned else t("Pin selected")
        menu.addAction(
            pin_label,
            lambda items=selected, pinned=not all_pinned: self._set_pinned(items, pinned),
        )
        rename_action = menu.addAction(t("Rename"), lambda: self._rename_conversation(idx))
        rename_action.setEnabled(not multiple)

        project_menu = menu.addMenu(t("Add to project"))
        for proj in self._projects:
            pid = proj.get("id")
            name = self._project_display_name(proj)
            act = project_menu.addAction(
                name,
                lambda p=pid, items=selected: self._assign_project_many(items, p),
            )
            act.setCheckable(True)
            act.setChecked(
                all(
                    self._conversations[item].get("project_id", _GENERAL_PROJECT_ID) == pid
                    for item in selected
                )
            )

        browse_action = menu.addAction(
            t("Browse conversation files"),
            lambda: self._browse_conversation_files(idx),
        )
        browse_action.setEnabled(not multiple)
        source = conv.get("external_source") if isinstance(conv.get("external_source"), dict) else {}
        provider = _external_provider_display_name(source.get("provider"))
        if not provider and any(
            str(message.get("content") or "").strip()
            for message in conv.get("messages", [])
        ):
            export_menu = menu.addMenu(t("Export as new conversation"))
            export_menu.addAction(
                "Codex",
                lambda: self._export_conversation_as_new_session(idx, "codex"),
            )
            export_menu.addAction(
                "Claude",
                lambda: self._export_conversation_as_new_session(idx, "claude"),
            )
            export_menu.setEnabled(not multiple)
        delete_label = (
            t("Delete selected ({count})").format(count=len(selected))
            if multiple
            else t("Delete")
        )
        menu.addAction(delete_label, lambda items=selected: self._delete_conversations(items))
        # Drop the menu just below the ⋮ button that opened it.
        pos = (
            anchor.mapToGlobal(anchor.rect().bottomLeft())
            if anchor is not None
            else self.mapToGlobal(self.rect().center())
        )
        self._conversation_menu = menu
        menu.aboutToHide.connect(lambda: setattr(self, "_conversation_menu", None))
        menu.popup(pos)

    def _suggested_export_cwd(self, conv: dict) -> Path:
        """Return the best available workspace folder for a new provider session."""
        for item in conv.get("file_context", []) or []:
            if not isinstance(item, dict):
                continue
            for key in ("root", "path"):
                raw = str(item.get(key) or "").strip()
                if not raw:
                    continue
                path = Path(raw).expanduser()
                if path.is_file():
                    path = path.parent
                if path.is_dir():
                    return path
        for message in reversed(conv.get("messages", []) or []):
            if not isinstance(message, dict):
                continue
            for ref in _conversation_store.normalize_attachments(message.get("attachments")):
                path = _conversation_store.attachment_path(ref)
                if path.is_file():
                    return path.parent
                if path.is_dir():
                    return path
        return Path.cwd()

    def _export_conversation_as_new_session(self, idx: int, provider_key: str) -> None:
        """Create a new local Codex or Claude Code transcript from an OpenWand chat."""
        if not (0 <= idx < len(self._conversations)):
            return
        conv = self._conversations[idx]
        provider = _external_provider_display_name(provider_key)
        workspace = self._suggested_export_cwd(conv)
        prompt = t(
            "Create a new local {provider} conversation from this OpenWand history?\n\n"
            "This experimental integration writes a new transcript file and never overwrites an existing provider conversation."
        ).format(provider=provider)
        if QMessageBox.question(
            self,
            t("Experimental conversation export"),
            prompt,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            report = export_conversation_as_new_session(
                conv,
                provider_key,
                cwd=workspace,
            )
            self._persist()
            self._rebuild_sidebar()
            self._refresh_conversation_source_label()
            QMessageBox.information(
                self,
                t("Conversation exported"),
                t(
                    "Created a new {provider} conversation with {count} turn(s).\n\n"
                    "Transcript: {path}\n\n"
                    "Refresh or restart {provider} if it does not appear immediately."
                ).format(
                    provider=provider,
                    count=report.exported,
                    path=report.path,
                ),
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                t("Conversation export failed"),
                t("OpenWand could not create the external conversation: {error}").format(error=exc),
            )

    def _browse_conversation_files(self, idx: int) -> None:
        """Reveal the persisted conversation record and its adjacent attachments."""
        if not (0 <= idx < len(self._conversations)):
            return
        self._persist()
        record_path = _conversation_store.CONVERSATIONS_FILE
        try:
            if not record_path.exists():
                record_path.parent.mkdir(parents=True, exist_ok=True)
                record_path = record_path.parent
            _file_browser.reveal_path(record_path)
        except OSError as exc:
            QMessageBox.warning(
                self,
                t("Could not open conversation files"),
                t("OpenWand could not open the conversation files: {error}").format(error=exc),
            )

    def _toggle_pin(self, idx: int) -> None:
        """Handle toggle pin for chat window."""
        if not (0 <= idx < len(self._conversations)):
            return
        self._set_pinned([idx], not self._conversations[idx].get("pinned"))

    def _set_pinned(self, indices: list[int], pinned: bool) -> None:
        """Set the pinned state for one or more selected conversations."""
        valid = sorted({idx for idx in indices if 0 <= idx < len(self._conversations)})
        if not valid:
            return
        for idx in valid:
            self._conversations[idx]["pinned"] = pinned
            _touch_conversation(self._conversations[idx])
        self._rebuild_sidebar()
        self._persist()

    def _rename_conversation(self, idx: int) -> None:
        """Handle rename conversation for chat window."""
        if not (0 <= idx < len(self._conversations)):
            return
        conv = self._conversations[idx]
        current = self._conversation_title(idx, conv)
        name, ok = QInputDialog.getText(
            self, t("Rename conversation"), t("Title:"), text=current
        )
        if not ok:
            return
        name = str(name or "").strip()
        if not name:
            QMessageBox.warning(self, t("Rename conversation"), t("Conversation title cannot be empty."))
            return
        if len(name) > 200 or any(ord(char) < 32 for char in name):
            QMessageBox.warning(self, t("Rename conversation"), t("Conversation title is invalid."))
            return
        duplicate = any(
            other_idx != idx
            and self._conversation_title(other_idx, other).strip().casefold() == name.casefold()
            for other_idx, other in enumerate(self._conversations)
        )
        if duplicate:
            QMessageBox.warning(self, t("Rename conversation"), t("A conversation already uses that title."))
            return
        previous = deepcopy(conv)
        conv["title_override"] = name
        _touch_conversation(conv)
        self._rebuild_sidebar()
        if hasattr(self, "_conversation_header_label") and idx == self._active_idx:
            self._conversation_header_label.setText(self._current_conversation_header_text())
        if not self._persist():
            conv.clear()
            conv.update(previous)
            self._rebuild_sidebar()
            if hasattr(self, "_conversation_header_label") and idx == self._active_idx:
                self._conversation_header_label.setText(self._current_conversation_header_text())
            detail = str(getattr(self, "_last_persist_error", "") or t("Unknown storage error."))
            QMessageBox.warning(
                self,
                t("Rename conversation failed"),
                t("OpenWand could not save the new conversation title: {error}").format(error=detail),
            )

    def _assign_project(self, idx: int, project_id: str) -> None:
        """Handle assign project for chat window."""
        self._assign_project_many([idx], project_id)

    def _assign_project_many(self, indices: list[int], project_id: str) -> None:
        """Move one or more selected conversations into a project."""
        if project_id not in {str(project.get("id") or "") for project in self._projects}:
            return
        valid = sorted({idx for idx in indices if 0 <= idx < len(self._conversations)})
        if not valid:
            return
        for idx in valid:
            self._conversations[idx]["project_id"] = project_id
            _touch_conversation(self._conversations[idx])
        self._rebuild_sidebar()
        self._persist()

    def _delete_conversation(self, idx: int) -> None:
        """Delete conversation."""
        self._delete_conversations([idx])

    def _delete_conversations(self, indices: list[int]) -> None:
        """Delete one or more selected conversations after one confirmation."""
        valid = sorted({idx for idx in indices if 0 <= idx < len(self._conversations)})
        if not valid:
            return
        if self._streaming and self._active_idx in valid:
            return  # don't delete the conversation mid-stream
        multiple = len(valid) > 1
        prompt = (
            t("Delete these {count} conversations? This cannot be undone.").format(
                count=len(valid)
            )
            if multiple
            else t("Delete this conversation? This cannot be undone.")
        )
        if QMessageBox.question(
            self,
            t("Delete conversations") if multiple else t("Delete conversation"),
            prompt,
        ) != QMessageBox.StandardButton.Yes:
            return
        previous_conversations = list(self._conversations)
        previous_active_idx = self._active_idx
        active_conversation = (
            self._conversations[self._active_idx]
            if 0 <= self._active_idx < len(self._conversations)
            else None
        )
        for idx in reversed(valid):
            self._conversations.pop(idx)
        surviving_active_idx = next(
            (
                idx
                for idx, conversation in enumerate(self._conversations)
                if conversation is active_conversation
            ),
            None,
        )
        if surviving_active_idx is not None:
            self._active_idx = surviving_active_idx
        else:
            self._active_idx = min(previous_active_idx, max(0, len(self._conversations) - 1))
        if not self._persist():
            self._conversations.clear()
            self._conversations.extend(previous_conversations)
            self._active_idx = previous_active_idx
            detail = str(getattr(self, "_last_persist_error", "") or t("Unknown storage error."))
            QMessageBox.warning(
                self,
                t("Delete conversations failed") if multiple else t("Delete conversation failed"),
                t("OpenWand could not delete the conversation(s): {error}").format(error=detail),
            )
            return
        self._selected_conversation_indices = (
            {self._active_idx} if self._conversations else set()
        )
        self._selection_anchor_idx = self._active_idx if self._conversations else None
        self._rebuild_stack()
        self._rebuild_sidebar()
        if self._conversations:
            self._switch(min(self._active_idx, len(self._conversations) - 1))
        else:
            self._refresh_chat_scoped_controls()

    def _delete_all_conversations(self) -> None:
        """Delete all OpenWand conversations after one explicit confirmation."""
        if not self._conversations:
            return
        if self._streaming:
            QMessageBox.information(
                self,
                t("Delete all conversations"),
                t("Wait for the current reply to finish before deleting conversation history."),
            )
            return

        count = len(self._conversations)
        prompt = t(
            "Delete all {count} OpenWand conversations?\n\n"
            "This cannot be undone. Imported Codex and Claude Code source files will not be deleted."
        ).format(count=count)
        answer = QMessageBox.question(
            self,
            t("Delete all conversations"),
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        previous_conversations = list(self._conversations)
        previous_active_idx = self._active_idx
        self._conversations.clear()
        self._active_idx = 0
        if not self._persist():
            self._conversations.extend(previous_conversations)
            self._active_idx = previous_active_idx
            detail = str(getattr(self, "_last_persist_error", "") or t("Unknown storage error."))
            QMessageBox.warning(
                self,
                t("Delete all conversations failed"),
                t("OpenWand could not delete the conversations: {error}").format(error=detail),
            )
            return

        self._selected_conversation_indices = set()
        self._selection_anchor_idx = None
        self._rebuild_stack()
        self._rebuild_sidebar()
        self._refresh_chat_scoped_controls()
        if hasattr(self, "_conversation_header_label"):
            self._conversation_header_label.setText(self._current_conversation_header_text())

    def _rebuild_stack(self) -> None:
        """Tear down and rebuild all stack pages 1:1 with _conversations."""
        while self._stack.count():
            w = self._stack.widget(0)
            self._stack.removeWidget(w)
            w.deleteLater()
        self._built_pages = set()
        self._has_placeholder = not self._conversations
        if self._conversations:
            for i, conv in enumerate(self._conversations):
                if i == self._active_idx:
                    self._stack.addWidget(self._make_page(i, conv))
                else:
                    self._stack.addWidget(self._make_page_placeholder())
        else:
            ph = QLabel(t("No conversations yet.\n\nPress Ctrl+Q to ask something."))
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setStyleSheet(f"color: {_HINT}; background: {_BG};")
            self._stack.addWidget(ph)
        self._stack.setCurrentIndex(max(0, min(self._active_idx, self._stack.count() - 1)))

    def _persist(self) -> bool:
        """Handle persist for chat window."""
        self._last_persist_error = None
        if self._persist_fn:
            try:
                self._persist_fn()
            except Exception as exc:
                self._last_persist_error = exc
                return False
        return True

    def _btn_style(self, active: bool, latest: bool) -> str:
        """Handle btn style for chat window."""
        bg = _SEL_BG if active else "transparent"
        c = _TEXT
        return (
            f"QPushButton {{ background: {bg}; color: {c}; border: none;"
            f" text-align: left; padding: 6px 10px; font-size: 9pt; }}"
            f"QPushButton:hover {{ background: {_WHITE_BG_10}; }}"
            f"QPushButton:checked {{ background: {_SEL_BG}; }}"
        )

    def _switch(self, idx: int, *, preserve_selection: bool = False):
        """Handle switch for chat window."""
        if not (0 <= idx < len(self._conversations)):
            return
        self._stop_middle_autoscroll()
        self._active_idx = idx
        if not preserve_selection:
            self._selected_conversation_indices = {idx}
            self._selection_anchor_idx = idx
        if idx < self._stack.count():
            self._ensure_page_built(idx)
            self._stack.setCurrentIndex(idx)
        self._update_selected_conversation_notice(idx)
        self._refresh_chat_scoped_controls()
        for real_idx, btn in self._sidebar_btns:
            is_active = real_idx == idx
            is_selected = real_idx in self._selected_conversation_indices
            if isinstance(btn, _ConversationTitleButton):
                btn.set_sidebar_state(
                    active=is_active,
                    selected=is_selected,
                    latest=real_idx == len(self._conversations) - 1,
                )
            else:
                btn.setChecked(is_selected)
                btn.setStyleSheet(
                    self._btn_style(is_selected, real_idx == len(self._conversations) - 1)
                )
        if self._on_select and 0 <= idx < len(self._conversations):
            self._on_select(idx)
        self._refresh_context_controls()
        self.request_context_preview()
        self._update_jump_to_latest_visibility()

    def hideEvent(self, event):  # noqa: N802
        """Stop transient autoscroll whenever Chat leaves the screen."""
        self._stop_middle_autoscroll()
        super().hideEvent(event)

    def closeEvent(self, event):  # noqa: N802
        """Detach the application-wide filter before Qt deletes this window."""
        self._stop_middle_autoscroll()
        self._window_geometry_save_timer.stop()
        self._save_window_state()
        if self._application_event_filter_installed:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._application_event_filter_installed = False
        super().closeEvent(event)

    def _update_selected_conversation_notice(self, idx: int) -> None:
        """Show which conversation the composer will continue."""
        if hasattr(self, "_conversation_header_label"):
            self._conversation_header_label.setText(self._current_conversation_header_text())
        self._refresh_conversation_source_label()
        if self._formatted_replies_ui_enabled:
            self._past_notice.setVisible(False)
            return
        if not (0 <= idx < len(self._conversations)):
            self._past_notice.setVisible(False)
            return
        title = self._conversation_title(idx, self._conversations[idx])
        self._past_notice.setText(f"  {t('Continuing')}: {title}")
        self._past_notice.setToolTip(title)
        self._past_notice.setVisible(True)

    def sync_conversation(self, idx: int) -> None:
        """Rebuild and show a conversation a hotkey/voice prompt just appended to.

        Called when a prompt continued an existing thread rather than starting a
        new one, so the open window reflects the added turns and follows along.
        """
        if not (0 <= idx < len(self._conversations)):
            return
        # Force the page to rebuild with the appended turns, then show it.
        self._built_pages.discard(idx)
        self._rebuild_sidebar()
        self._switch(idx)

    def begin_external_reply_stream(self, idx: int) -> None:
        """Show a temporary assistant bubble for a hotkey/overlay reply."""
        if not (0 <= idx < len(self._conversations)):
            return
        self._ensure_page_built(idx)
        if idx != self._active_idx:
            self._switch(idx)
        layout = self._active_layout()
        if layout is None:
            return
        self._streaming = True
        self._streaming_idx = idx
        self._begin_stream_follow(idx)
        self._send_btn.setEnabled(False)
        self._new_chat_btn.setEnabled(False)
        self._current_ai_text = ""
        self._current_ai_reply_text = ""
        self._current_ai_segments = []
        self._current_ai_status_text = ""
        self._current_ai_parser = ThoughtStreamParser()
        self._current_ai_annotations = []
        self._current_ai_attachments = []
        self._current_file_context = []
        self._current_tool_context = {}
        self._current_context_snippets = []
        self._current_harness = {}
        self._current_local_work_dialog = None
        self._current_local_work_notice = None
        self._current_user_message = None
        self._current_ai_label = self._bubble(layout, "...", "assistant", created_at=_now_iso())
        self._scroll_bottom()

    def external_reply_chunk(self, idx: int, chunk: object) -> None:
        """Append one hotkey/overlay reply chunk to the temporary assistant bubble."""
        if not (0 <= idx < len(self._conversations)):
            return
        if self._current_ai_label is not None and self._streaming_idx not in (None, idx):
            # Another conversation owns the active stream; dropping this late
            # chunk keeps a stalled query's reply out of the current bubble.
            return
        if self._current_ai_label is None:
            self.begin_external_reply_stream(idx)
        self._on_chunk(chunk)

    def finish_external_reply_stream(self, idx: int, final_text: str = "") -> None:
        """Finalize and remove the temporary assistant bubble before persistence sync."""
        if not (0 <= idx < len(self._conversations)):
            return
        if self._current_ai_label is not None and self._streaming_idx not in (None, idx):
            # Not the conversation that owns the active stream — leave it intact;
            # this turn's final text is rendered by the add_conversation sync.
            return
        if final_text:
            self._on_final_text(final_text)
        if self._current_local_work_dialog is not None:
            self._current_local_work_dialog.mark_finished()
        label = self._current_ai_label
        wrapper = label.parentWidget() if label is not None else None
        self._current_ai_label = None
        self._current_ai_text = ""
        self._current_ai_reply_text = ""
        self._current_ai_segments = []
        self._current_ai_status_text = ""
        self._current_ai_parser = None
        self._current_ai_annotations = []
        self._current_ai_attachments = []
        self._current_file_context = []
        self._current_tool_context = {}
        self._current_context_snippets = []
        self._current_harness = {}
        self._current_user_message = None
        self._streaming = False
        self._streaming_idx = None
        self._update_jump_to_latest_visibility()
        self._send_btn.setEnabled(True)
        self._new_chat_btn.setEnabled(True)
        if wrapper is not None:
            wrapper.hide()
            wrapper.deleteLater()
        elif label is not None:
            label.hide()
            label.deleteLater()
        if self._pending_addon_ui_refresh:
            self._apply_addon_ui_mode()

    # ------------------------------------------------------------------ Right panel

    def _make_right_panel(self) -> QWidget:
        """Create right panel."""
        panel = QWidget()
        panel.setStyleSheet(f"background: {_BG};")
        vl = QVBoxLayout(panel)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        if self._formatted_replies_ui_enabled:
            vl.addWidget(self._make_formatted_conversation_header())

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {_BG};")
        # When no history exists yet a single placeholder widget sits at index 0;
        # _has_placeholder lets ingest_new_conversations swap it out for real pages.
        self._has_placeholder = not self._conversations
        if self._conversations:
            for i, conv in enumerate(self._conversations):
                if i == self._active_idx:
                    self._stack.addWidget(self._make_page(i, conv))
                else:
                    self._stack.addWidget(self._make_page_placeholder())
        else:
            ph = QLabel(t("No conversations yet.\n\nPress Ctrl+Q to ask something."))
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setStyleSheet(f"color: {_HINT}; background: {_BG};")
            self._stack.addWidget(ph)
        self._stack.setCurrentIndex(self._active_idx)
        vl.addWidget(self._stack, stretch=1)

        self._jump_to_latest_btn = QPushButton(t("↓  Jump to latest"))
        self._jump_to_latest_btn.setObjectName("jumpToLatestButton")
        self._jump_to_latest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._jump_to_latest_btn.setAccessibleName(t("Jump to latest reply"))
        self._jump_to_latest_btn.setToolTip(
            t("A reply is continuing below. Jump to the newest content.")
        )
        self._jump_to_latest_btn.setStyleSheet(
            f"QPushButton {{ background: {_TITLE_BG}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; border-radius: 12px;"
            " padding: 5px 12px; font-size: 8pt; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {_SEL_BG}; border-color: {_ACCENT}; }}"
        )
        self._jump_to_latest_btn.clicked.connect(self._jump_to_latest)
        self._jump_to_latest_btn.hide()
        vl.addWidget(
            self._jump_to_latest_btn,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )

        self._past_notice = QLabel(t("  Selected conversation"))
        self._past_notice.setFixedHeight(26)
        self._past_notice.setStyleSheet(
            f"background: {_ACCENT_BG_10}; color: {_HINT};"
            f" font-size: 8pt; border-top: 1px solid {_BORDER};"
        )
        self._past_notice.setVisible(False)
        if not self._formatted_replies_ui_enabled:
            vl.addWidget(self._past_notice)

        self._input_frame = self._make_input_area()
        vl.addWidget(self._input_frame)
        return panel

    def _make_formatted_conversation_header(self) -> QWidget:
        """Create the compact conversation bar used by the approved prototype."""
        bar = QWidget()
        bar.setObjectName("formattedConversationHeader")
        bar.setFixedHeight(46)
        bar.setStyleSheet(
            f"QWidget#formattedConversationHeader {{ background: {_BG};"
            f" border-bottom: 1px solid {_BORDER}; }}"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(18, 0, 14, 0)
        row.setSpacing(4)
        title = QLabel(self._current_conversation_header_text())
        title.setFont(_ui_font(10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {_TEXT}; background: transparent;")
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._conversation_header_label = title
        row.addWidget(title)

        activity_button = QPushButton(t("Agents"))
        activity_button.setObjectName("harnessActivityButton")
        activity_button.setFixedHeight(30)
        activity_button.setCursor(Qt.CursorShape.PointingHandCursor)
        activity_button.setAccessibleName(t("Agent activity"))
        activity_button.setToolTip(t("Show subagents and their current work"))
        activity_button.setStyleSheet(
            f"QPushButton {{ background: {_WHITE_BG_8}; color: {_HINT};"
            f" border: 1px solid {_BORDER}; border-radius: 9px; padding: 4px 10px;"
            " font-size: 8pt; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {_WHITE_BG_10}; color: {_TEXT}; border-color: {_ACCENT}; }}"
        )
        activity_button.clicked.connect(
            lambda _checked=False, button=activity_button: self._toggle_harness_inspector(button)
        )
        self._harness_activity_button = activity_button
        row.addWidget(activity_button)

        menu_button = QPushButton("⋮")
        menu_button.setObjectName("conversationOptionsButton")
        menu_button.setFixedSize(34, 34)
        menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        menu_button.setAccessibleName(t("Conversation options"))
        menu_button.setToolTip(t("Conversation options"))
        menu_button.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_HINT}; border: none;"
            " border-radius: 17px; padding: 0; font-size: 16pt; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {_WHITE_BG_10}; color: {_TEXT}; }}"
        )
        menu_button.clicked.connect(
            lambda _checked=False, button=menu_button: self._open_conversation_menu(
                self._active_idx,
                button,
            )
        )
        self._conversation_options_button = menu_button
        row.addWidget(menu_button)
        return bar

    def _has_highlighted_conversation(self) -> bool:
        """Return whether one valid chat is actively highlighted in history."""
        conversations = getattr(self, "_conversations", [])
        active_idx = int(getattr(self, "_active_idx", 0) or 0)
        selected = getattr(self, "_selected_conversation_indices", set())
        return (
            0 <= active_idx < len(conversations)
            and active_idx in selected
        )

    def _refresh_chat_scoped_controls(self) -> None:
        """Hide controls whose actions require an actively highlighted chat."""
        visible = self._has_highlighted_conversation()
        input_frame = getattr(self, "_input_frame", None)
        if input_frame is not None:
            set_visible = getattr(input_frame, "setVisible", None)
            if callable(set_visible):
                set_visible(visible)
            set_enabled = getattr(input_frame, "setEnabled", None)
            if callable(set_enabled):
                set_enabled(visible)
        for name in ("_harness_activity_button", "_conversation_options_button"):
            button = getattr(self, name, None)
            if button is not None:
                button.setVisible(visible)
        header = getattr(self, "_conversation_header_label", None)
        if header is not None:
            header.setText(self._current_conversation_header_text())

    def _toggle_harness_inspector(self, anchor: QWidget) -> None:
        """Open the compact top-right Codex activity panel."""
        if self._harness_inspector is not None and self._harness_inspector.isVisible():
            self._harness_inspector.close()
            return
        dialog = QDialog(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        dialog.setObjectName("harnessActivityInspector")
        dialog.setMinimumWidth(360)
        dialog.setMaximumWidth(480)
        dialog.setStyleSheet(
            f"QDialog#harnessActivityInspector {{ background: {_TITLE_BG}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; border-radius: 18px; }}"
            f"QLabel {{ background: transparent; color: {_TEXT}; }}"
            f"QPushButton {{ text-align: left; background: transparent; color: {_TEXT};"
            " border: none; border-radius: 8px; padding: 8px 10px; }}"
            f"QPushButton:hover {{ background: {_WHITE_BG_10}; }}"
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(5)
        agents = dict(self._harness_activity_state.get("agents") or {})
        heading = QLabel(t("Agent activity"))
        heading.setFont(_ui_font(9, QFont.Weight.Bold))
        heading.setStyleSheet(f"color: {_HINT};")
        layout.addWidget(heading)
        if agents:
            for agent_id, raw in agents.items():
                agent = raw if isinstance(raw, dict) else {}
                status = str(agent.get("status") or agent.get("phase") or t("Working"))
                prompt = str(agent.get("prompt") or agent.get("detail") or "").strip()
                short_id = str(agent_id)[-8:]
                label = f"●  {status}  ·  {short_id}"
                if prompt:
                    label += f"\n    {prompt[:100]}"
                button = QPushButton(label, dialog)
                button.setObjectName("harnessAgentRow")
                button.setToolTip(prompt or str(agent_id))
                button.clicked.connect(
                    lambda _checked=False, data=dict(agent), key=str(agent_id): self._show_harness_detail(
                        t("Subagent {agent}").format(agent=key[-8:]),
                        data,
                    )
                )
                layout.addWidget(button)
        else:
            empty = QLabel(t("No subagents in this turn."))
            empty.setObjectName("harnessNoAgents")
            empty.setStyleSheet(f"color: {_HINT}; padding: 8px 10px;")
            layout.addWidget(empty)
        dialog.adjustSize()
        point = anchor.mapToGlobal(anchor.rect().bottomRight())
        dialog.move(point.x() - dialog.width(), point.y() + 6)
        dialog.finished.connect(lambda _result: setattr(self, "_harness_inspector", None))
        self._harness_inspector = dialog
        dialog.show()

    def _show_harness_detail(self, title: str, data: dict) -> None:
        """Show one agent's prompt and chronological activity."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(560, 420)
        outer = QVBoxLayout(dialog)
        view = QTextBrowser(dialog)
        lines: list[str] = []
        prompt = str(data.get("prompt") or "").strip()
        if prompt:
            lines.extend([t("Assigned work"), prompt, ""])
        lines.extend([
            f"{t('Status')}: {data.get('status') or data.get('phase') or ''}",
            f"{t('Thread')}: {data.get('agent_id') or data.get('receiver_thread_ids') or ''}",
        ])
        activity = data.get("activity") if isinstance(data.get("activity"), list) else []
        if activity:
            lines.extend(["", t("Latest activity")])
            for item in activity:
                if isinstance(item, dict):
                    lines.append(
                        f"• {item.get('phase') or ''} {item.get('activity_type') or item.get('tool') or ''}: "
                        f"{item.get('detail') or item.get('status') or ''}"
                    )
        view.setPlainText("\n".join(lines))
        outer.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        dialog.exec()

    def _show_harness_list(
        self,
        title: str,
        items: list,
        key: str,
        *,
        use_skill: bool = False,
    ) -> None:
        """Show the native Codex skills or MCP inventory with descriptions."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(620, 460)
        outer = QVBoxLayout(dialog)
        if use_skill:
            hint = QLabel(t("Choose a skill to add its Codex $name marker to your message."), dialog)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {_HINT};")
            outer.addWidget(hint)
            scroll = QScrollArea(dialog)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            content = QWidget(scroll)
            rows = QVBoxLayout(content)
            rows.setContentsMargins(0, 0, 0, 0)
            rows.setSpacing(5)
            for raw in items:
                item = raw if isinstance(raw, dict) else {}
                name = str(item.get(key) or "").strip()
                if not name:
                    continue
                description = str(item.get("description") or "").strip()
                label = f"${name}"
                if description:
                    label += f"\n{description[:180]}"
                button = QPushButton(label, content)
                button.setObjectName("harnessSkillChoice")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setToolTip(description)
                button.setStyleSheet(
                    f"QPushButton {{ text-align: left; color: {_TEXT}; background: {_TITLE_BG};"
                    f" border: 1px solid {_BORDER}; border-radius: 8px; padding: 10px; }}"
                    f"QPushButton:hover {{ background: {_WHITE_BG_10}; border-color: {_ACCENT}; }}"
                )
                button.clicked.connect(
                    lambda _checked=False, skill=name, target=dialog: self._insert_skill_reference(
                        skill,
                        target,
                    )
                )
                rows.addWidget(button)
            rows.addStretch(1)
            scroll.setWidget(content)
            outer.addWidget(scroll, 1)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
            buttons.rejected.connect(dialog.reject)
            outer.addWidget(buttons)
            dialog.exec()
            return
        view = QTextBrowser(dialog)
        blocks: list[str] = []
        for raw in items:
            item = raw if isinstance(raw, dict) else {}
            name = str(item.get(key) or t("Unnamed"))
            description = str(item.get("description") or item.get("error") or "").strip()
            scope = str(item.get("scope") or item.get("authStatus") or "").strip()
            tools = item.get("tools") if isinstance(item.get("tools"), dict) else {}
            suffix = f" · {scope}" if scope else ""
            if tools:
                suffix += t(" · {count} tools").format(count=len(tools))
            blocks.append(f"{name}{suffix}\n{description}".rstrip())
        view.setPlainText("\n\n".join(blocks) if blocks else t("Nothing discovered."))
        outer.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        dialog.exec()

    def _insert_skill_reference(self, name: str, dialog: QDialog | None = None) -> None:
        """Insert the CLI-compatible ``$skill`` marker into the chat composer."""
        marker = f"${str(name or '').strip()}"
        if marker == "$":
            return
        cursor = self._input.textCursor()
        prefix = "" if not self._input.toPlainText() or cursor.position() == 0 else " "
        cursor.insertText(f"{prefix}{marker} ")
        self._input.setTextCursor(cursor)
        self._input.setFocus()
        if dialog is not None:
            dialog.accept()

    def _on_harness_activity(self, event: dict) -> None:
        """Merge capability data or subagent events into their respective UI state."""
        event_type = str(event.get("type") or "")
        if event_type == "capabilities":
            self._harness_activity_state["skills"] = list(event.get("skills") or [])
        elif event_type == "subagent":
            agents = self._harness_activity_state.setdefault("agents", {})
            if not isinstance(agents, dict):
                agents = {}
                self._harness_activity_state["agents"] = agents
            item_id = str(event.get("item_id") or "")
            agent_id = str(event.get("agent_id") or "")
            key = agent_id or item_id or f"agent-{len(agents) + 1}"
            previous = agents.pop(item_id, {}) if agent_id and item_id in agents and item_id != key else agents.get(key, {})
            merged = dict(previous) if isinstance(previous, dict) else {}
            activity = list(merged.get("activity") or [])
            activity.append(dict(event))
            merged.update(event)
            merged["agent_id"] = agent_id or str(merged.get("agent_id") or key)
            merged["activity"] = activity[-100:]
            agents[key] = merged
            for state_id, raw_state in dict(event.get("agent_states") or {}).items():
                state = raw_state if isinstance(raw_state, dict) else {}
                existing = agents.get(str(state_id), {})
                updated = dict(existing) if isinstance(existing, dict) else {}
                updated.update({"agent_id": str(state_id), **state})
                updated.setdefault("prompt", str(event.get("prompt") or ""))
                updated.setdefault("activity", list(activity))
                agents[str(state_id)] = updated
        button = getattr(self, "_harness_activity_button", None)
        if button is not None:
            count = len(dict(self._harness_activity_state.get("agents") or {}))
            button.setText(t("Agents {count}").format(count=count) if count else t("Agents"))
        inspector = self._harness_inspector
        if inspector is not None and inspector.isVisible():
            inspector.close()

    def _make_conversation_source_label(self) -> QLabel:
        """Create a compact, explicit local/imported conversation identity label."""
        conversation = (
            self._conversations[self._active_idx]
            if 0 <= self._active_idx < len(self._conversations)
            else None
        )
        text, detail = _conversation_source_status(conversation)
        label = QLabel(text)
        label.setObjectName("conversationSourceStatus")
        label.setToolTip(detail)
        label.setAccessibleName(text)
        label.setStyleSheet(
            f"color: {_HINT}; background: {_WHITE_BG_8}; border: 1px solid {_BORDER};"
            " border-radius: 8px; padding: 4px 8px; font-size: 7pt;"
        )
        self._conversation_source_label = label
        return label

    def _refresh_conversation_source_label(self) -> None:
        """Refresh source identity after switching or exporting a conversation."""
        label = getattr(self, "_conversation_source_label", None)
        if label is None:
            return
        conversation = (
            self._conversations[self._active_idx]
            if 0 <= self._active_idx < len(self._conversations)
            else None
        )
        text, detail = _conversation_source_status(conversation)
        label.setText(text)
        label.setAccessibleName(text)
        label.setToolTip(detail)

    def _current_conversation_header_text(self) -> str:
        """Return the selected title for the approved-mode top bar."""
        if self._has_highlighted_conversation():
            return self._conversation_title(self._active_idx, self._conversations[self._active_idx])
        return t("Select a chat")

    def start_new_conversation(self, auto_message: str | None = None):
        """Start new conversation."""
        if self._streaming:
            return

        search = (
            self._sidebar_search
            if self._formatted_replies_ui_enabled
            else self._history_search
        )
        if search.text():
            search.clear()

        was_empty = not self._conversations
        conv = {
            "id": str(uuid.uuid4()),
            "project_id": self._active_project_id,
            "messages": [],
            "context": "",
            "context_policy": _default_context_policy(),
        }
        _touch_conversation(conv)
        self._conversations.append(conv)

        if was_empty and self._has_placeholder:
            placeholder = self._stack.widget(0)
            self._stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self._has_placeholder = False

        idx = len(self._conversations) - 1
        self._stack.addWidget(self._make_page(idx, conv))
        self._rebuild_sidebar()
        self._switch(idx)
        self._input.setFocus()

        if auto_message:
            QTimer.singleShot(0, lambda: self._send(auto_message))

    def ingest_new_conversations(self, *, select_new: bool = False):
        """Build pages for any conversations appended to the shared list since the
        window was built (e.g. a query started via hotkey while the chat was open).

        The new tab is added to the history sidebar but NOT selected — the user
        stays on whatever tab they were reading. (Exception: if the window was
        showing the empty-history placeholder, the newest tab is shown so the
        window isn't left blank.) Pass select_new when the new chat was created
        by an external prompt that the user expects to see immediately."""
        from_placeholder = self._has_placeholder and self._conversations
        if from_placeholder:
            placeholder = self._stack.widget(0)
            self._stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self._has_placeholder = False

        # With no placeholder, stack index aligns 1:1 with _conversations.
        added = False
        for idx in range(self._stack.count(), len(self._conversations)):
            if idx == self._active_idx or from_placeholder:
                self._stack.addWidget(self._make_page(idx, self._conversations[idx]))
            else:
                self._stack.addWidget(self._make_page_placeholder())
            added = True
        if not added:
            return
        self._rebuild_sidebar()
        if from_placeholder or select_new:
            self._switch(len(self._conversations) - 1)
        else:
            self._refresh_chat_scoped_controls()

    def _make_page_placeholder(self) -> QLabel:
        """Create page placeholder."""
        ph = QLabel(t("Loading conversation..."))
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph.setStyleSheet(f"color: {_HINT}; background: {_BG};")
        return ph

    def _ensure_page_built(self, idx: int) -> None:
        """Ensure page built."""
        if idx in self._built_pages or idx < 0 or idx >= len(self._conversations):
            return
        if idx >= self._stack.count():
            return
        old = self._stack.widget(idx)
        page = self._make_page(idx, self._conversations[idx])
        self._stack.removeWidget(old)
        old.deleteLater()
        self._stack.insertWidget(idx, page)

    def _make_page(self, idx: int, conv: dict) -> QScrollArea:
        """Create page."""
        self._built_pages.add(idx)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        transcript_bar = scroll.verticalScrollBar()
        transcript_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        transcript_bar.sliderMoved.connect(
            lambda _value, target=scroll: self._on_transcript_manual_scroll(target)
        )
        transcript_bar.sliderReleased.connect(
            lambda target=scroll: self._on_transcript_manual_scroll(target)
        )
        transcript_bar.actionTriggered.connect(
            lambda _action, target=scroll: QTimer.singleShot(
                0,
                lambda: self._on_transcript_manual_scroll(target),
            )
        )
        # Keep the conversation scrollbar easy to acquire with a mouse.  The
        # previous 9 px track also had 2 px margins, leaving only a roughly
        # 5 px draggable handle on Windows.
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_BG}; border: none; }}"
            f"QScrollBar:vertical {{ width: {_CHAT_SCROLLBAR_WIDTH}px; margin: 0;"
            f" background: {_BG}; border: none; }}"
            f"QScrollBar::groove:vertical {{ background: {_BG}; border: none; }}"
            f"QScrollBar::handle:vertical {{ background: {_BORDER}; border-radius: 7px;"
            f" min-height: {_CHAT_SCROLLBAR_HANDLE_MIN_HEIGHT}px; margin: 2px; }}"
            f"QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{"
            f" background: {_HINT}; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
            " height: 0; background: none; border: none; }"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{"
            f" background: {_BG}; border: none; }}"
        )

        container = QWidget()
        container.setStyleSheet(f"background: {_BG};")
        layout = QVBoxLayout(container)
        if self._formatted_replies_ui_enabled:
            layout.setContentsMargins(24, 34, 24, 28)
            layout.setSpacing(28)
        else:
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(10)
        layout.addStretch()

        _ensure_conversation_metadata(conv)
        stamp = None if self._formatted_replies_ui_enabled else self._conversation_time_label(conv)
        hint = self._context_hint(
            _context_not_anchored_to_messages(conv.get("context", ""), conv.get("messages", []))
        )
        insert_at = 0
        if stamp is not None:
            layout.insertWidget(insert_at, stamp)
            insert_at += 1
        if hint is not None:
            layout.insertWidget(insert_at, hint)  # sits above the first message

        last_ai: _MessageTextView | None = None
        for msg_idx, msg in enumerate(conv["messages"]):
            display_text = msg.get("display_content", msg["content"])
            view = self._bubble(
                layout,
                display_text,
                msg["role"],
                _conversation_store.first_image_base64_from_message(msg),
                annotations=msg.get("annotations"),
                created_at=msg.get("created_at") or conv.get("created_at"),
                conversation_index=idx,
                message_index=msg_idx,
            )
            if msg["role"] == "user":
                msg_hint = self._message_context_hint(msg.get("context"))
                if msg_hint is not None:
                    layout.insertWidget(layout.count() - 1, msg_hint)
                snippets = self._context_snippets_widget(msg.get("context_snippets"))
                if snippets is not None:
                    layout.insertWidget(layout.count() - 1, snippets)
            if msg["role"] == "assistant":
                last_ai = view

        scroll._last_assistant_view = last_ai  # type: ignore[attr-defined]
        scroll._msg_layout = layout  # type: ignore[attr-defined]
        scroll.setWidget(container)
        def scroll_to_bottom_if_alive(s=scroll) -> None:
            """Ignore a queued scroll after its conversation widget was replaced."""
            try:
                bar = s.verticalScrollBar()
                bar.setValue(bar.maximum())
            except RuntimeError:
                return

        QTimer.singleShot(0, scroll_to_bottom_if_alive)
        return scroll

    def _context_hint(self, context: str) -> QLabel | None:
        """A small chip hinting at the context attached to this conversation
        (selected text, dropped files, ambient snapshot, ...). The full context
        is available on hover. When the document readers cut content off (they
        leave a ``[…truncated]`` marker), the chip flags it so the user knows the
        model didn't see everything. Returns None when there was no context."""
        text = (context or "").strip()
        if not text:
            return None
        truncated = "truncated]" in text  # marker left by the document/PDF readers
        preview = " ".join(text.split())  # collapse newlines/runs to one line
        if len(preview) > 160:
            preview = preview[:160].rstrip() + "…"
        body = f"{t('Context')} · {_token_label(text)} · {html.escape(preview)}"
        if truncated:
            body += f" <span style='color:#d6a04a;'>· {t('truncated')}</span>"
        lbl = QLabel(body)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        tooltip = _truncate_for_display(text, _CONTEXT_TOOLTIP_CHAR_LIMIT, "context tooltip")
        lbl.setToolTip(
            tooltip + f"\n\n[{t('context was truncated to fit the limit')}]" if truncated else tooltip
        )
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setStyleSheet(
            f"QLabel {{ background: {_ACCENT_BG_12}; color: {_HINT};"
            f" font-size: 8pt; border: 1px solid {_BORDER}; border-radius: 6px;"
            f" padding: 5px 9px; }}"
        )
        return lbl

    def _message_context_hint(self, context: object) -> QWidget | None:
        """One-click disclosure for the full context attached to a user turn."""
        text = _message_context_text(context)
        if not text:
            return None
        lines = text.splitlines()
        title = t("Attached")
        if lines:
            first = lines[0].strip()
            if first.startswith("[") and first.endswith("]"):
                title = first[1:-1].strip() or title
        truncated = "truncated]" in text
        show_label = f"{t('Show context')} · {title} · {_token_label(text)}"
        if truncated:
            show_label += f" · {t('truncated')}"

        wrapper = QWidget()
        wrapper.setObjectName("messageContextDisclosure")
        wrapper.setStyleSheet("background: transparent;")
        disclosure_layout = QVBoxLayout(wrapper)
        disclosure_layout.setContentsMargins(4, 0, 4, 0)
        disclosure_layout.setSpacing(7)

        toggle = QPushButton(show_label)
        toggle.setObjectName("messageContextToggle")
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setAccessibleName(t("Show context"))
        toggle.setToolTip(t("Show the full context attached to this message."))
        toggle.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT_BG_10}; color: {_HINT};"
            f" font-size: 8pt; border: 1px solid {_BORDER}; border-radius: 6px;"
            " padding: 6px 9px; text-align: left; }}"
            f"QPushButton:hover {{ background: {_WHITE_BG_10}; color: {_TEXT}; }}"
        )
        disclosure_layout.addWidget(toggle)

        presentation = "assistant" if self._formatted_replies_ui_enabled else "legacy"
        body = _MessageTextView(_AI_BG, self._font_scale, presentation=presentation)
        body.setObjectName("messageContextBody")
        body.setHtml(_assistant_text_to_html(text))
        body.hide()
        disclosure_layout.addWidget(body)

        def toggle_context(_checked: bool = False) -> None:
            show = body.isHidden()
            body.setVisible(show)
            toggle.setText(t("Hide context") if show else show_label)
            toggle.setAccessibleName(t("Hide context") if show else t("Show context"))
            toggle.setToolTip(
                t("Hide the attached context.")
                if show
                else t("Show the full context attached to this message.")
            )
            if show:
                QTimer.singleShot(0, body._sync_height)
            wrapper.updateGeometry()

        toggle.clicked.connect(toggle_context)
        return wrapper

    def _context_snippets_widget(self, snippets: object) -> QLabel | None:
        """Display-only, per-source context snippets shown under a user turn.

        Styled like the intent overlay's grey context preview rows. This text is
        never sent to the model — it only records what context accompanied the
        message."""
        items = _normalized_context_snippets(snippets)
        if not items:
            return None
        rows: list[str] = []
        for idx, item in enumerate(items, start=1):
            label = item.get("label") or t("Context")
            preview = item.get("preview") or ""
            if len(preview) > 160:
                preview = preview[:160].rstrip() + "…"
            rows.append(
                f"<span style='color:{_HINT};'>{idx}.</span> "
                f"<span style='color:{_TEXT};'>{html.escape(label)}</span>"
                f"<span style='color:{_HINT};'> · {html.escape(preview)}</span>"
            )
        lbl = QLabel("<br>".join(rows))
        lbl.setObjectName("messageContextSnippets")
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setToolTip(t("Context included with this message (display only - not part of the reply)."))
        lbl.setStyleSheet(
            "QLabel#messageContextSnippets {"
            f" color: {_HINT}; background: transparent; font-size: 8pt;"
            " padding: 2px 8px; margin-left: 4px; margin-right: 4px; }"
        )
        return lbl

    def _conversation_time_label(self, conv: dict) -> QLabel | None:
        """Small display-only timestamp for a conversation page."""
        created = _format_conversation_datetime(conv.get("created_at"))
        updated = _format_conversation_datetime(conv.get("updated_at"))
        if not created and not updated:
            return None
        text = created or updated
        if created and updated and updated != created:
            text = f"{created} · updated {updated}"
        lbl = QLabel(html.escape(text))
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setStyleSheet(
            f"color: {_HINT}; font-size: 8pt; padding: 2px;"
            " background-color: transparent;"
        )
        return lbl

    def _make_attachment_preview(self) -> QFrame:
        """Create the pending-attachment preview kept inside the composer block."""
        frame = QFrame()
        frame.setObjectName("pendingAttachmentPreview")
        frame.setVisible(False)
        frame.setStyleSheet(
            f"QFrame#pendingAttachmentPreview {{ background: {_WHITE_BG_8};"
            f" border: 1px solid {_BORDER}; border-radius: 9px; }}"
        )
        row = QHBoxLayout(frame)
        row.setContentsMargins(7, 5, 5, 5)
        row.setSpacing(7)

        thumbnail = QLabel()
        thumbnail.setObjectName("pendingAttachmentThumbnail")
        thumbnail.setFixedSize(44, 44)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumbnail.setStyleSheet(
            f"background: {_AI_BG}; border: none; border-radius: 6px; color: {_HINT};"
        )
        thumbnail.setVisible(False)
        row.addWidget(thumbnail)

        label = QLabel("")
        label.setObjectName("pendingAttachmentNames")
        label.setWordWrap(True)
        label.setStyleSheet(
            f"color: {_TEXT}; background: transparent; border: none; font-size: 8pt;"
        )
        row.addWidget(label, stretch=1)

        clear_button = QPushButton("×")
        clear_button.setObjectName("clearPendingAttachments")
        clear_button.setFixedSize(26, 26)
        clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_button.setToolTip(t("Remove pending attachments"))
        clear_button.setAccessibleName(t("Remove pending attachments"))
        clear_button.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_HINT}; border: none;"
            " border-radius: 13px; font-size: 14pt; }}"
            f"QPushButton:hover {{ background: {_WHITE_BG_10}; color: {_TEXT}; }}"
        )
        clear_button.clicked.connect(self._clear_pending_attachments)
        row.addWidget(clear_button)

        self._attachment_preview = frame
        self._attachment_thumbnail = thumbnail
        self._attachment_label = label
        return frame

    def _clear_pending_attachments(self) -> None:
        """Remove every unsent attachment from the composer preview."""
        self._pending_attachment_context = ""
        self._pending_attachment_image_b64 = None
        self._pending_attachments = []
        self._pending_attachment_labels = []
        self._refresh_attachment_label()
        self.request_context_preview()

    def _make_input_area(self) -> QWidget:
        """Create input area."""
        if self._formatted_replies_ui_enabled:
            return self._make_formatted_input_area()
        frame = QWidget()
        frame.setStyleSheet(f"background: {_TITLE_BG}; border-top: 1px solid {_BORDER};")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        outer.addWidget(self._make_context_policy_controls())

        outer.addWidget(self._make_attachment_preview())

        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        self._input = _ComposerTextEdit(minimum_height=62, maximum_height=180)
        self._input.attachment_mime.connect(self._add_attachments_from_mime)
        self._update_composer_send_hint()
        self._apply_input_font_scale()
        self._input.installEventFilter(self)

        self._attach_btn = QPushButton("+")
        self._attach_btn.setObjectName("chatAttachButton")
        self._attach_btn.setFixedSize(34, 46)
        self._attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._attach_btn.setToolTip(t("Add files or images as context"))
        self._attach_btn.setAccessibleName(t("Add files or images as context"))
        self._attach_btn.setStyleSheet(
            f"QPushButton {{ background-color: {_ACCENT_BG_18}; color: {_ACCENT};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; font-size: 18pt;"
            " padding: 0px; }"
            f"\nQPushButton:hover {{ background-color: {_ACCENT_BG_32}; }}"
            f"\nQPushButton:disabled {{ color: {_DISABLED_TEXT}; border: 1px solid {_WHITE_BG_10}; }}"
        )
        self._attach_btn.clicked.connect(self._choose_attachments)

        self._send_btn = QPushButton(t("Send"))
        self._send_btn.setFixedSize(64, 46)
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: {_ON_ACCENT}; border: none;"
            f" border-radius: 6px; font-size: 10pt; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {_ACCENT_HOVER}; }}"
            f"QPushButton:disabled {{ background: {_DISABLED_BG}; color: {_DISABLED_TEXT}; }}"
        )
        self._send_btn.clicked.connect(self._on_send_clicked)

        h.addWidget(self._attach_btn)
        h.addWidget(self._input)
        h.addWidget(self._send_btn)
        outer.addLayout(h)
        return frame

    def _make_formatted_input_area(self) -> QWidget:
        """Create the centered floating composer from the approved prototype."""
        footer = QWidget()
        footer.setObjectName("formattedComposerFooter")
        footer.setStyleSheet(f"QWidget#formattedComposerFooter {{ background: {_BG}; }}")
        outer = QVBoxLayout(footer)
        outer.setContentsMargins(24, 8, 24, 12)
        outer.setSpacing(0)

        card = QFrame()
        card.setObjectName("formattedComposer")
        card.setMinimumWidth(0)
        card.setMaximumWidth(768)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.setStyleSheet(
            f"QFrame#formattedComposer {{ background: {_AI_BG}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; border-radius: 18px; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 10, 8)
        card_layout.setSpacing(5)

        self._context_policy_panel = self._make_context_policy_controls()
        self._context_policy_panel.hide()
        card_layout.addWidget(self._context_policy_panel)

        self._input = _ComposerTextEdit(minimum_height=42, maximum_height=180)
        self._input.setObjectName("formattedComposerInput")
        self._input.attachment_mime.connect(self._add_attachments_from_mime)
        self._update_composer_send_hint()
        self._input.setStyleSheet(
            f"QTextEdit#formattedComposerInput {{ background: transparent; color: {_TEXT};"
            " border: none; padding: 3px 4px; font-size: 11pt; }}"
        )
        self._input.installEventFilter(self)
        card_layout.addWidget(self._input)

        card_layout.addWidget(self._make_attachment_preview())

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self._attach_btn = QPushButton("+")
        self._attach_btn.setObjectName("formattedAttachButton")
        self._attach_btn.setFixedSize(34, 34)
        self._attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._attach_btn.setToolTip(t("Add files or images as context"))
        self._attach_btn.setAccessibleName(t("Add files or images as context"))
        self._attach_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_TEXT}; border: none;"
            " border-radius: 17px; font-size: 17pt; }}"
            f"QPushButton:hover {{ background: {_WHITE_BG_10}; }}"
        )
        self._attach_btn.clicked.connect(self._choose_attachments)
        actions.addWidget(self._attach_btn)

        self._model_label = QLabel()
        self._model_label.setObjectName("chatExactModel")
        self._model_label.setStyleSheet(
            f"color: {_HINT}; background: transparent; font-size: 7pt;"
        )
        self.refresh_model_label()
        actions.addWidget(self._model_label)
        if callable(self._on_model_settings):
            change_model = QPushButton(t("Change model"))
            change_model.setObjectName("chatModelSettings")
            change_model.setFlat(True)
            change_model.setCursor(Qt.CursorShape.PointingHandCursor)
            change_model.setToolTip(t("Open model settings"))
            change_model.setStyleSheet(
                f"QPushButton {{ color: {_HINT}; background: transparent; border: none;"
                " padding: 0 4px; font-size: 7pt; }}"
                f"QPushButton:hover {{ color: {_ACCENT}; }}"
            )
            change_model.clicked.connect(lambda _checked=False: self._on_model_settings())
            actions.addWidget(change_model)

        actions.addStretch()

        composer_menu_btn = QPushButton("⋮")
        composer_menu_btn.setObjectName("formattedComposerMenuButton")
        composer_menu_btn.setFixedSize(34, 34)
        composer_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        composer_menu_btn.setAccessibleName(t("Chat options"))
        composer_menu_btn.setToolTip(t("Chat options"))
        composer_menu_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_HINT}; border: none;"
            " border-radius: 17px; font-size: 16pt; font-weight: 700; padding: 0; }}"
            f"QPushButton:hover {{ background: {_WHITE_BG_10}; color: {_TEXT}; }}"
        )
        composer_menu_btn.clicked.connect(
            lambda _checked=False, button=composer_menu_btn: self._open_composer_menu(button)
        )
        self._composer_menu_btn = composer_menu_btn
        actions.addWidget(composer_menu_btn)

        self._send_btn = QPushButton("↑")
        self._send_btn.setObjectName("formattedSendButton")
        self._send_btn.setFixedSize(34, 34)
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setAccessibleName(t("Send"))
        self._send_btn.setToolTip(t("Send"))
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background: {_TEXT}; color: {_ON_ACCENT}; border: none;"
            " border-radius: 17px; font-size: 16pt; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {_ACCENT_HOVER}; }}"
            f"QPushButton:disabled {{ background: {_DISABLED_BG}; color: {_DISABLED_TEXT}; }}"
        )
        self._send_btn.clicked.connect(self._on_send_clicked)
        actions.addWidget(self._send_btn)
        card_layout.addLayout(actions)

        composer_row = QHBoxLayout()
        composer_row.setContentsMargins(0, 0, 0, 0)
        composer_row.addStretch(1)
        composer_row.addWidget(card, 100)
        composer_row.addStretch(1)
        outer.addLayout(composer_row)
        return footer

    def _formatted_reply_action(self) -> dict | None:
        """Return the formatted-replies action that controls automatic presentation."""
        return next(
            (
                item
                for item in self._addon_message_actions
                if str(item.get("addon_id") or "") == _FORMATTED_REPLIES_ADDON_ID
                and str(item.get("id") or "") == "format-reply"
            ),
            None,
        )

    def _open_composer_menu(self, anchor: QWidget) -> None:
        """Open chat-level options from inside the bottom composer card."""
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {_TITLE_BG}; color: {_TEXT}; border: 1px solid {_BORDER}; }}"
            f"QMenu::item:selected {{ background: {_SEL_BG}; }}"
        )
        enter_send = menu.addAction(t("Enter sends message"))
        enter_send.setCheckable(True)
        enter_send.setChecked(self._enter_sends)
        enter_send.toggled.connect(self._set_enter_send)

        context_controls = menu.addAction(t("Context controls"))
        context_controls.setCheckable(True)
        context_controls.setChecked(bool(self._context_policy_panel.isVisible()))
        context_controls.toggled.connect(self._context_policy_panel.setVisible)

        skills = list(self._harness_activity_state.get("skills") or [])
        if skills:
            skills_action = menu.addAction(
                t("Skills…"),
                lambda items=list(skills): self._show_harness_list(
                    t("Codex skills"),
                    items,
                    "name",
                    use_skill=True,
                ),
            )
            skills_action.setToolTip(
                t("Choose from {count} Codex skills").format(count=len(skills))
            )

        menu.addSeparator()
        for provider in ("codex", "claude"):
            provider_name = _external_provider_display_name(provider)
            auto_import = menu.addAction(
                t("Keep {provider} imports updated").format(provider=provider_name)
            )
            auto_import.setCheckable(True)
            auto_import.setChecked(
                bool((self._external_sync_state.get(provider) or {}).get("enabled"))
            )
            auto_import.toggled.connect(
                lambda enabled, source=provider: self._set_external_auto_sync(source, enabled)
            )
        menu.addAction(
            t("Import from Codex…"),
            lambda: self._pull_external_conversations("codex"),
        )
        menu.addAction(
            t("Import from Claude Code…"),
            lambda: self._pull_external_conversations("claude"),
        )
        menu.addSeparator()
        delete_all = menu.addAction(t("Delete all chats…"), self._delete_all_conversations)
        delete_all.setEnabled(bool(self._conversations))

        self._composer_menu = menu
        menu.aboutToHide.connect(lambda: setattr(self, "_composer_menu", None))
        menu.popup(anchor.mapToGlobal(anchor.rect().topRight()))

    def _set_auto_format_enabled(self, enabled: bool) -> None:
        """Apply the quick auto-format preference immediately and persist it via the host."""
        action = self._formatted_reply_action()
        if action is None:
            return
        action["auto"] = bool(enabled)
        if callable(self._on_addon_setting_change):
            self._on_addon_setting_change(
                {
                    "addon_id": _FORMATTED_REPLIES_ADDON_ID,
                    "key": "auto_format",
                    "value": bool(enabled),
                }
            )

    def _set_enter_send(self, enabled: bool) -> None:
        """Choose between Enter-to-send and Ctrl+Enter-to-send composer behavior."""
        self._enter_sends = bool(enabled)
        config.set_chat_enter_send(self._enter_sends)
        self._update_composer_send_hint()

    def _update_composer_send_hint(self) -> None:
        """Keep the visible composer hint aligned with the active keyboard behavior."""
        if not hasattr(self, "_input"):
            return
        self._input.setPlaceholderText(
            t("Message… (Enter sends; Shift+Enter adds a line)")
            if self._enter_sends
            else t("Message… (Ctrl+Enter sends; Enter adds a line)")
        )

    def _open_addon_settings(self, addon_id: str) -> None:
        """Open an add-on's detailed settings when the runtime exposes that surface."""
        if callable(self._on_addon_settings):
            self._on_addon_settings(addon_id)

    @staticmethod
    def _configured_chat_model() -> str:
        """Return the visible Chat model route used for ordinary chat turns."""
        return str(getattr(config, "CHAT_LLM_MODEL", "") or t("Default")).strip()

    def refresh_model_label(self) -> None:
        """Keep the composer badge aligned with the route used by the next turn."""
        label = getattr(self, "_model_label", None)
        if label is None:
            return
        exact_model = self._configured_chat_model()
        label.setText(exact_model)
        label.setToolTip(
            t("Exact model used for this chat: {model}").format(model=exact_model)
        )

    def _make_context_policy_controls(self) -> QWidget:
        """Create per-conversation context/tool controls above the chat input."""
        frame = QWidget()
        frame.setStyleSheet(
            "QWidget { background-color: transparent; }"
            "QPushButton { text-align: center; }"
        )
        outer = QHBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        raw_keys = str(getattr(config, "INTENT_CONTEXT_TOGGLE_KEYS", "12345678") or "12345678")
        keys: list[str] = []
        for ch in raw_keys + "12345678":
            if ch.isspace() or ch in keys:
                continue
            keys.append(ch)
            if len(keys) == 8:
                break
        rows = [
            (
                "ambient",
                f"{keys[0]} {t('App')}",
                [("off", t("Off")), ("on", t("On")), ("auto", t("Let model decide"))],
            ),
            (
                "browser",
                f"{keys[1]} {t('Browser/Web')}",
                [("off", t("Off")), ("on", t("On")), ("auto", t("Let model decide"))],
            ),
            ("selection", f"{keys[2]} {t('Selection')}", [("off", t("Off")), ("on", t("On"))]),
            ("clipboard", f"{keys[3]} {t('Clipboard')}", [("off", t("Off")), ("on", t("On"))]),
            (
                "screenshot",
                f"{keys[4]} {t('Screenshot')}",
                [("off", t("Off")), ("on", t("On")), ("auto", t("Let model decide"))],
            ),
            (
                "github",
                f"{keys[5]} {t('Git/GitHub')}",
                [("off", t("Off")), ("on", t("On")), ("auto", t("Let model decide"))],
            ),
            (
                "memory",
                f"{keys[6]} {t('Memory')}",
                [("off", t("Off")), ("on", t("On")), ("auto", t("Let model decide"))],
            ),
            (
                "files",
                f"{keys[7]} {t('Files')}",
                [("off", t("Off")), ("read", t("Read only")), ("ask", t("Ask before write")), ("auto", t("Auto"))],
            ),
        ]
        for source, label_text, options in rows:
            key, _, label = label_text.partition(" ")
            chip = QPushButton()
            chip.setObjectName(f"chatContextChip_{source}")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFixedHeight(54)
            chip.setMinimumWidth(78)
            chip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            chip.clicked.connect(lambda _checked=False, source=source: self._show_context_policy_menu(source))
            self._context_controls[source] = chip
            self._context_control_options[source] = options
            self._context_control_labels[source] = label or label_text
            self._context_control_keys[source] = key
            outer.addWidget(chip, 1)
        self._refresh_context_controls()
        return frame

    def _refresh_context_controls(self) -> None:
        """Refresh controls from the active conversation's saved policy."""
        if not self._context_controls:
            return
        self._context_controls_updating = True
        try:
            if 0 <= self._active_idx < len(self._conversations):
                conv = self._conversations[self._active_idx]
                policy = _ensure_conversation_context_policy(conv)
            else:
                policy = _all_context_off_policy()
            for source, chip in self._context_controls.items():
                state = _policy_state(policy, source)
                self._update_context_chip(chip, source, state)
        finally:
            self._context_controls_updating = False

    def _state_label_for_context_source(self, source: str, state: str) -> str:
        """Return display label for a context chip state."""
        if state == "auto":
            return t("auto")
        for value, label in self._context_control_options.get(source, []):
            if value == state:
                return label
        return state

    def _context_chip_style(self, state: str) -> str:
        """Return the compact intent-overlay-style chip CSS."""
        color = {
            "off": "#85889a",
            "auto": "#d1b15f",
            "model": "#d1b15f",
            "on": _ACCENT,
            "read": _ACCENT,
            "ask": "#d1b15f",
        }.get(state, _ACCENT)
        background = _ACCENT_BG_32 if state == "off" else _ACCENT_BG_46
        return (
            f"QPushButton {{ background-color: {background}; color: {_TEXT};"
            f" border: 1px solid {color}; border-radius: 7px;"
            " padding: 3px;"
            " font-size: 8pt; }"
            f"\nQPushButton:hover {{ background-color: {_ACCENT_BG_60}; border: 1px solid {_ACCENT}; }}"
        )

    def _context_token_metadata(self, source: str, state: str) -> tuple[str, str]:
        """Return (token label, warning) for one chat context/tool chip."""
        if state == "off":
            if source in {"ambient", "browser", "selection", "clipboard", "screenshot"}:
                return _deferred_token_label(), ""
            return "0 tok", ""
        if source == "memory":
            return _deferred_token_label(), t("Memory tokens are estimated after the prompt is known.")
        if source in {"ambient", "browser", "github", "selection", "clipboard"}:
            return _deferred_token_label(), t("This context is fetched when you send the message, so this token cost is not known yet.")
        return _deferred_token_label(), ""

    def _update_context_chip(self, chip: QPushButton, source: str, state: str) -> None:
        """Paint one compact context chip from its current state."""
        tokens, warning = self._context_token_metadata(source, state)
        previous_tokens = self._context_control_tokens.get(source, "")
        if _is_concrete_token_label(previous_tokens) and not _is_concrete_token_label(tokens):
            tokens = previous_tokens
            warning = self._context_control_warnings.get(source, warning)
        self._set_context_chip_display(chip, source, state, tokens, warning)

    def _set_context_chip_display(
        self,
        chip: QPushButton,
        source: str,
        state: str,
        tokens: str,
        warning: str,
    ) -> None:
        """Paint one context chip using supplied token metadata."""
        key = self._context_control_keys.get(source, "")
        label = self._context_control_labels.get(source, source)
        state_label = self._state_label_for_context_source(source, state)
        self._context_control_tokens[source] = tokens
        self._context_control_warnings[source] = warning
        chip.setText(f"{key} {label}\n{state_label}\n{tokens}")
        tooltip = f"{label}: {state_label}\n{t('Token estimate')}: {tokens}"
        if warning:
            tooltip += f"\n\n{warning}"
        chip.setToolTip(tooltip)
        chip.setProperty("context_state", state)
        chip.setProperty("context_tokens", tokens)
        chip.setStyleSheet(self._context_chip_style(state))

    def request_context_preview(self) -> None:
        """Ask the supervisor to refresh visible context token estimates."""
        if self._on_context_preview is None or not (0 <= self._active_idx < len(self._conversations)):
            return
        policy = _ensure_conversation_context_policy(self._conversations[self._active_idx])
        self._context_preview_id = str(uuid.uuid4())
        self._on_context_preview(
            {
                "preview_id": self._context_preview_id,
                "caller_idx": 0,
                "context_policy": deepcopy(policy),
            }
        )

    def update_context_preview(self, preview_id: str, context_items: list[dict]) -> None:
        """Apply supervisor-provided token estimates to chat context chips."""
        if preview_id != self._context_preview_id:
            return
        by_id = {str(item.get("id") or ""): item for item in context_items or [] if isinstance(item, dict)}
        for source, chip in self._context_controls.items():
            state = str(chip.property("context_state") or "off")
            item = by_id.get(source)
            if item is None:
                continue
            tokens = str(item.get("tokens") or _deferred_token_label())
            warning = str(item.get("warning") or "")
            self._set_context_chip_display(chip, source, state, tokens, warning)

    def _show_context_policy_menu(self, source: str) -> None:
        """Open a small state list for one context chip."""
        if self._context_controls_updating:
            return
        chip = self._context_controls.get(source)
        if chip is None:
            return
        menu = QMenu(chip)
        menu.setStyleSheet(
            f"QMenu {{ background: {_TITLE_BG}; color: {_TEXT}; border: 1px solid {_BORDER}; }}"
            f"QMenu::item:selected {{ background: {_SEL_BG}; }}"
        )
        current = str(chip.property("context_state") or "off")
        for value, label in self._context_control_options.get(source, []):
            action = menu.addAction(label)
            action.setData(value)
            action.setCheckable(True)
            action.setChecked(value == current)
            action.triggered.connect(
                lambda _checked=False, source=source, value=value: self._set_context_policy_state(source, value)
            )
        menu.popup(chip.mapToGlobal(chip.rect().bottomLeft()))

    def _set_context_policy_state(self, source: str, state: str) -> None:
        """Persist one visible context/tool control change to the conversation."""
        if self._context_controls_updating or not (0 <= self._active_idx < len(self._conversations)):
            return
        chip = self._context_controls.get(source)
        if chip is None:
            return
        conv = self._conversations[self._active_idx]
        policy = _ensure_conversation_context_policy(conv)
        current = _policy_state(policy, source)
        if source in {"selection", "screenshot"} and current == "off" and state == "on":
            if callable(self._on_context_capture):
                self._on_context_capture(
                    {
                        "source": source,
                        "conversation_index": self._active_idx,
                        "context_policy": deepcopy(policy),
                    }
                )
                return
        conv["context_policy"] = _apply_policy_state(policy, source, state)
        _touch_conversation(conv)
        self._update_context_chip(chip, source, _policy_state(conv["context_policy"], source))
        self._persist()
        self.request_context_preview()

    def attach_captured_context(
        self,
        name: str = "",
        content: str = "",
        item_type: str = "text",
        source: str = "",
        paths: list[str] | None = None,
    ) -> dict:
        """Attach interactively captured context to the next outgoing chat turn."""
        if not (0 <= self._active_idx < len(self._conversations)):
            return {"attached": False, "reason": "no_conversation"}
        selected_paths = [str(path or "").strip() for path in (paths or []) if str(path or "").strip()]
        label = str(name or "Context")
        kind = str(item_type or "text")
        body = str(content or "")
        attached_any = False
        if selected_paths:
            attached_any = self._add_attachment_paths(selected_paths)
        if not body and not attached_any:
            return {"attached": False, "reason": "empty"}
        if body and kind == "image":
            if self._pending_attachment_image_b64 is None:
                self._pending_attachment_image_b64 = body
            else:
                self._pending_attachment_context = "\n\n".join(
                    part
                    for part in (
                        self._pending_attachment_context,
                        f"[Attached image: {label}]",
                    )
                    if part.strip()
            )
            if label not in self._pending_attachment_labels:
                self._pending_attachment_labels.append(label)
            attached_any = True
        elif body:
            attached_any = self._add_attachment_items([(label, body, kind)]) or attached_any

        conv = self._conversations[self._active_idx]
        policy = _ensure_conversation_context_policy(conv)
        if source == "selection":
            conv["context_policy"] = _apply_policy_state(policy, "selection", "on")
        elif source == "screenshot":
            conv["context_policy"] = _apply_policy_state(policy, "screenshot", "on")
        _touch_conversation(conv)
        self._refresh_attachment_label()
        self._refresh_context_controls()
        self._persist()
        self.request_context_preview()
        return {"attached": True}

    def cancel_context_capture(self, source: str = "") -> dict:
        """Return a chip to Off after its interactive capture was cancelled."""
        if not (0 <= self._active_idx < len(self._conversations)):
            return {"cancelled": False, "reason": "no_conversation"}
        conv = self._conversations[self._active_idx]
        policy = _ensure_conversation_context_policy(conv)
        if source in {"selection", "screenshot"}:
            conv["context_policy"] = _apply_policy_state(policy, source, "off")
            _touch_conversation(conv)
            self._refresh_context_controls()
            self._persist()
            self.request_context_preview()
        return {"cancelled": True}

    # ------------------------------------------------------------------ Bubbles

    def update_addon_message_actions(self, actions: list[dict] | None = None) -> None:
        """Install enabled actions and switch addon-owned Chat UI when needed."""
        normalized = [
            action
            for action in _normalized_addon_message_actions(actions)
            if str(action.get("addon_id") or "") != _FORMATTED_REPLIES_ADDON_ID
        ]
        actions_changed = normalized != self._addon_message_actions
        self._addon_message_actions = normalized
        self._formatted_replies_ui_enabled = True
        # The host refreshes addon discovery whenever Chat is shown.  Most of
        # those responses repeat the action list already used for first paint.
        # Replacing the active page in that case needlessly destroys and
        # recreates every rich presentation (including its WebEngine surface),
        # which makes formatted conversations flash like the window reloaded.
        if not actions_changed:
            return
        if 0 <= self._active_idx < len(self._conversations):
            self._built_pages.discard(self._active_idx)
            self._switch(self._active_idx)

    def _request_addon_message_action(
        self,
        conversation_index: int,
        message_index: int,
        addon_id: str,
        action_id: str,
    ) -> None:
        """Mark a message busy and hand its canonical text to the addon host."""
        if str(addon_id or "") == _FORMATTED_REPLIES_ADDON_ID:
            return
        if not callable(self._on_addon_message_action):
            return
        if not (0 <= conversation_index < len(self._conversations)):
            return
        conv = self._conversations[conversation_index]
        messages = conv.get("messages", [])
        if not (0 <= message_index < len(messages)):
            return
        message = messages[message_index]
        if not isinstance(message, dict) or str(message.get("role") or "") != "assistant":
            return
        user_prompt = ""
        for prior in reversed(messages[:message_index]):
            if isinstance(prior, dict) and str(prior.get("role") or "") == "user":
                user_prompt = str(prior.get("content") or "")
                break
        statuses = message.setdefault("addon_action_status", {})
        if isinstance(statuses, dict):
            statuses[addon_id] = t("Formatting…")
        payload = {
            "addon_id": addon_id,
            "action_id": action_id,
            "conversation_id": str(conv.get("id") or ""),
            "message_id": str(message.get("id") or ""),
            "surface": "chat",
            "role": "assistant",
            "text": str(message.get("content") or ""),
            "user_prompt": user_prompt,
        }
        self._refresh_addon_message_page(conversation_index)
        QTimer.singleShot(0, lambda data=payload: self._on_addon_message_action(data))

    def _refresh_addon_message_page(self, conversation_index: int) -> None:
        """Refresh one action result in place without selecting or opening a chat."""
        self._built_pages.discard(conversation_index)
        if conversation_index != self._active_idx or conversation_index >= self._stack.count():
            return

        old_page = self._stack.widget(conversation_index)
        scroll_ratio: float | None = None
        at_bottom = False
        if isinstance(old_page, QScrollArea):
            old_bar = old_page.verticalScrollBar()
            at_bottom = old_bar.value() >= max(0, old_bar.maximum() - 4)
            scroll_ratio = old_bar.value() / max(1, old_bar.maximum())

        self._ensure_page_built(conversation_index)
        self._stack.setCurrentIndex(conversation_index)

        def restore_position() -> None:
            try:
                page = self._stack.widget(conversation_index)
                if not isinstance(page, QScrollArea):
                    return
                bar = page.verticalScrollBar()
                if at_bottom:
                    bar.setValue(bar.maximum())
                elif scroll_ratio is not None:
                    bar.setValue(round(bar.maximum() * scroll_ratio))
            except RuntimeError:
                return

        QTimer.singleShot(0, restore_position)

    def apply_addon_message_action_result(
        self,
        conversation_id: str = "",
        message_id: str = "",
        addon_id: str = "",
        action_id: str = "",
        result: dict | None = None,
    ) -> dict:
        """Persist a completed addon presentation without changing canonical text."""
        if str(addon_id or "") == _FORMATTED_REPLIES_ADDON_ID:
            return {"updated": False, "reason": "retired_addon"}
        payload = result if isinstance(result, dict) else {}
        for conversation_index, conv in enumerate(self._conversations):
            if conversation_id and str(conv.get("id") or "") != str(conversation_id):
                continue
            for message_index, message in enumerate(conv.get("messages", []) or []):
                if not isinstance(message, dict):
                    continue
                if message_id and str(message.get("id") or "") != str(message_id):
                    continue
                status = str(payload.get("status") or "").strip()
                statuses = message.setdefault("addon_action_status", {})
                if isinstance(statuses, dict):
                    if status:
                        statuses[addon_id] = status
                    else:
                        statuses.pop(addon_id, None)
                errors = message.setdefault("addon_action_errors", {})
                error_detail = str(payload.get("error_detail") or "").strip()
                if isinstance(errors, dict):
                    if error_detail:
                        errors[addon_id] = error_detail[:1000]
                    else:
                        errors.pop(addon_id, None)
                    if not errors:
                        message.pop("addon_action_errors", None)
                presentation = payload.get("presentation")
                presentations = message.setdefault("addon_presentations", {})
                if isinstance(presentation, dict) and str(presentation.get("html") or "").strip():
                    presentations[addon_id] = {
                        "action_id": action_id,
                        "format": "restricted_html",
                        "html": str(presentation.get("html") or ""),
                        "label": str(presentation.get("label") or "Formatted"),
                        "status": str(presentation.get("status") or status or "Formatted"),
                        "token_usage": dict(payload.get("token_usage") or {}),
                        "provider": str(payload.get("provider") or ""),
                        "model": str(payload.get("model") or ""),
                    }
                elif isinstance(presentations, dict):
                    presentations.pop(addon_id, None)
                if isinstance(presentations, dict) and not presentations:
                    message.pop("addon_presentations", None)
                action_models = message.setdefault("addon_action_models", {})
                used_model = str(payload.get("model") or "").strip()
                used_provider = str(payload.get("provider") or "").strip()
                if isinstance(action_models, dict) and (used_model or used_provider):
                    action_models[addon_id] = {
                        "provider": used_provider,
                        "model": used_model,
                    }
                _touch_conversation(conv)
                self._persist()
                self._refresh_addon_message_page(conversation_index)
                return {
                    "updated": True,
                    "conversation_index": conversation_index,
                    "message_index": message_index,
                }
        return {"updated": False, "reason": "message_not_found"}

    def _workspace_changes_widget(
        self,
        raw: object,
        conversation_index: int,
        message_index: int,
        parent: QWidget,
    ) -> QWidget | None:
        """Build a host-owned file-change card; model text is not consulted."""
        change_set = raw if isinstance(raw, dict) else {}
        files = [item for item in change_set.get("files", []) if isinstance(item, dict)]
        if not files:
            return None
        frame = QFrame(parent)
        frame.setObjectName("workspaceChangesCard")
        frame.setStyleSheet(
            f"QFrame#workspaceChangesCard {{ background: {_WHITE_BG_8}; border: 1px solid {_BORDER};"
            " border-radius: 10px; }}"
        )
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(7)
        header = QHBoxLayout()
        title = QLabel(t("Edited {count} files").format(count=len(files)))
        title.setObjectName("workspaceChangesTitle")
        title.setStyleSheet(f"color: {_TEXT}; font-weight: 700; border: none;")
        header.addWidget(title)
        header.addStretch()
        restore = QPushButton(t("Restored") if change_set.get("restored") else t("Restore"))
        restore.setObjectName("workspaceChangesRestoreButton")
        restore.setEnabled(not bool(change_set.get("restored")))
        view = QPushButton(t("View"))
        view.setObjectName("workspaceChangesViewButton")
        for button in (restore, view):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ color: {_TEXT}; background: transparent; border: 1px solid {_BORDER};"
                " border-radius: 7px; padding: 5px 10px; }}"
                f"QPushButton:hover {{ border-color: {_ACCENT}; color: {_ACCENT}; }}"
                f"QPushButton:disabled {{ color: {_DISABLED_TEXT}; }}"
            )
            header.addWidget(button)
        outer.addLayout(header)
        additions = sum(int(item.get("added") or 0) for item in files)
        deletions = sum(int(item.get("deleted") or 0) for item in files)
        totals = QLabel(
            f'<span style="color:#38d989">+{additions}</span>&nbsp;&nbsp;'
            f'<span style="color:#ff646b">-{deletions}</span>'
        )
        totals.setObjectName("workspaceChangesTotals")
        totals.setStyleSheet("border: none;")
        outer.addWidget(totals)
        for item in files:
            row = QHBoxLayout()
            path = str(item.get("path") or item.get("absolute_path") or "")
            absolute_path = str(item.get("absolute_path") or path)
            file_button = QPushButton(path)
            file_button.setObjectName("workspaceChangedFileButton")
            file_button.setProperty("file_path", absolute_path)
            file_button.setCursor(Qt.CursorShape.PointingHandCursor)
            file_button.setToolTip(absolute_path)
            file_button.setStyleSheet(
                f"QPushButton {{ color: {_TEXT}; background: transparent; border: none;"
                " text-align: left; padding: 4px 0; text-decoration: underline; }}"
                f"QPushButton:hover {{ color: {_ACCENT}; }}"
            )
            file_button.clicked.connect(
                lambda _checked=False, value=absolute_path:
                self._open_workspace_file(value)
            )
            row.addWidget(file_button, 1)
            stats = QLabel(
                f'<span style="color:#38d989">+{int(item.get("added") or 0)}</span>&nbsp;&nbsp;'
                f'<span style="color:#ff646b">-{int(item.get("deleted") or 0)}</span>'
            )
            stats.setStyleSheet("border: none; font-variant-numeric: tabular-nums;")
            row.addWidget(stats)
            outer.addLayout(row)
        view.clicked.connect(
            lambda _checked=False, data=change_set: self._show_workspace_changes(data)
        )
        restore.clicked.connect(
            lambda _checked=False, ci=conversation_index, mi=message_index:
            self._restore_workspace_change_message(ci, mi)
        )
        return frame

    def _open_workspace_file(self, value: str) -> None:
        path = Path(str(value or "")).expanduser()
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
            return
        QMessageBox.warning(self, t("File unavailable"), t("This changed file is no longer available."))

    def _show_workspace_changes(self, change_set: dict) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("workspaceChangesDiffDialog")
        dialog.setWindowTitle(t("File changes"))
        dialog.resize(900, 620)
        layout = QVBoxLayout(dialog)
        viewer = QTextEdit(dialog)
        viewer.setObjectName("workspaceChangesDiffViewer")
        viewer.setReadOnly(True)
        diff = str(change_set.get("diff") or "").strip()
        if not diff:
            diff = "\n\n".join(
                str(item.get("diff") or "").strip()
                for item in change_set.get("files", [])
                if isinstance(item, dict) and str(item.get("diff") or "").strip()
            )
        viewer.setPlainText(diff or t("No textual diff is available for these changes."))
        layout.addWidget(viewer)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _restore_workspace_change_message(self, conversation_index: int, message_index: int) -> None:
        if not (0 <= conversation_index < len(self._conversations)):
            return
        messages = self._conversations[conversation_index].get("messages", [])
        if not (0 <= message_index < len(messages)) or not isinstance(messages[message_index], dict):
            return
        change_set = messages[message_index].get("workspace_changes")
        if not isinstance(change_set, dict) or not change_set.get("files"):
            return
        if QMessageBox.question(
            self,
            t("Restore file changes?"),
            t("Restore every file in this change card to its state before the agent changed it?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        from core.workspace_changes import restore_workspace_changes

        result = restore_workspace_changes(change_set)
        if not result.get("ok"):
            QMessageBox.warning(self, t("Could not restore files"), str(result.get("error") or ""))
            return
        _touch_conversation(self._conversations[conversation_index])
        self._persist()
        self._refresh_addon_message_page(conversation_index)
        QMessageBox.information(
            self,
            t("Files restored"),
            t("Restored {count} files.").format(count=int(result.get("restored") or 0)),
        )

    def _bubble(
        self,
        layout,
        text: str,
        role: str,
        image_b64: str | None = None,
        *,
        annotations: object = None,
        created_at: str | None = None,
        conversation_index: int | None = None,
        message_index: int | None = None,
    ) -> _MessageTextView:
        """Handle bubble for chat window."""
        bg = _USER_BG if role == 'user' else _AI_BG
        message: dict = {}
        if (
            conversation_index is not None
            and message_index is not None
            and 0 <= conversation_index < len(self._conversations)
        ):
            messages = self._conversations[conversation_index].get("messages", [])
            if 0 <= message_index < len(messages) and isinstance(messages[message_index], dict):
                message = messages[message_index]
        display_text = _truncate_for_display(text, _CHAT_RENDER_CHAR_LIMIT, "chat display")
        display_annotations = _merged_annotations(annotations, display_text, role)
        presentation_style = (
            role if self._formatted_replies_ui_enabled and role in {"user", "assistant"} else "legacy"
        )
        lbl = _MessageTextView(bg, self._font_scale, presentation=presentation_style)
        lbl.setProperty(
            "openwand_has_table",
            role == "assistant" and _contains_markdown_table(display_text),
        )
        lbl.set_annotation_tooltips(display_text, display_annotations)
        if role == "assistant":
            lbl.setHtml(_assistant_text_to_html(display_text, annotations=display_annotations))
        else:
            lbl.setHtml(_user_text_to_html(display_text, display_annotations))

        role_text = t("You" if role == "user" else "Assistant")
        stamp = _format_conversation_datetime(created_at)
        if stamp:
            role_text = f"{role_text} · {stamp}"
        role_lbl = QLabel(role_text)
        role_lbl.setStyleSheet(f"color: {_HINT}; background: transparent; font-size: 8pt;")
        role_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wrapper.setMinimumWidth(0)
        wrapper.setMaximumWidth(16777215)
        wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if conversation_index is not None and message_index is not None:
            lbl.set_message_context_menu_handler(
                lambda pos, ci=conversation_index, mi=message_index, view=lbl: self._open_message_menu(
                    ci,
                    mi,
                    view,
                    pos,
                )
            )
            wrapper.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            wrapper.customContextMenuRequested.connect(
                lambda pos, w=wrapper, ci=conversation_index, mi=message_index: self._open_message_menu(
                    ci,
                    mi,
                    w,
                    pos,
                )
            )
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(2)
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        hl.addWidget(role_lbl)
        menu_btn = None
        if conversation_index is not None and message_index is not None:
            menu_btn = QPushButton("...")
            menu_btn.setFixedSize(28, 30 if self._formatted_replies_ui_enabled else 20)
            menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            menu_btn.setToolTip(t("Message options"))
            menu_btn.setAccessibleName(t("Message options"))
            menu_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {_HINT}; border: none;"
                " font-size: 9pt; font-weight: 700; padding: 0; margin: 0; }"
                f"QPushButton:hover {{ background: {_WHITE_BG_12}; color: {_TEXT}; }}"
            )
            menu_btn.clicked.connect(
                lambda _checked=False, button=menu_btn, ci=conversation_index, mi=message_index: self._open_message_menu(
                    ci,
                    mi,
                    button,
                )
            )
            if not self._formatted_replies_ui_enabled:
                hl.addWidget(menu_btn)
        if not self._formatted_replies_ui_enabled:
            wl.addWidget(header)

        image_label = self._image_thumbnail_label(image_b64, role)
        if image_label is not None:
            wl.addWidget(image_label)
            lbl.setProperty("openwand_has_image", True)

        presentation_view = None
        presentation_addon_id = ""
        if role == "assistant" and self._formatted_replies_ui_enabled:
            presentations = message.get("addon_presentations")
            if isinstance(presentations, dict):
                for addon_id, item in presentations.items():
                    if str(addon_id or "") == _FORMATTED_REPLIES_ADDON_ID:
                        continue
                    if not isinstance(item, dict) or not str(item.get("html") or "").strip():
                        continue
                    try:
                        from ui.addon_presentations import RichPresentationView

                        light = QColor(_BG).lightness() > 128
                        palette = {
                            "bg": _BG,
                            "text": _TEXT,
                            "muted": _HINT,
                            "line": _BORDER,
                            "accent": _ACCENT,
                            "warm": "#9b5429" if light else "#f0ae72",
                            "warm_soft": "#f4e4d8" if light else "#3c2b25",
                            "soft": _ACCENT_BG_10,
                            "code": "#f1f1ef" if light else "#15171c",
                            "code_text": "#242424" if light else "#eff6ff",
                        }
                        presentation_view = RichPresentationView(
                            str(item.get("html") or ""),
                            palette,
                            max(13, round(15 * self._font_scale)),
                            wrapper,
                        )
                        presentation_addon_id = str(addon_id)
                        break
                    except Exception:
                        presentation_view = None
                        presentation_addon_id = ""

        if presentation_view is not None:
            wl.addWidget(presentation_view)
            # Keep the canonical reply in the same wrapper even while hidden.
            # Calling show() on a parentless Qt widget creates a separate
            # top-level window, which is never correct for this inline toggle.
            wl.addWidget(lbl)
            lbl.hide()
        else:
            wl.addWidget(lbl)
        if not display_text and image_label is not None:
            lbl.hide()

        if conversation_index is not None and message_index is not None:
            changes_widget = self._workspace_changes_widget(
                message.get("workspace_changes"),
                conversation_index,
                message_index,
                wrapper,
            )
            if changes_widget is not None:
                wl.addWidget(changes_widget)

        action_specs = [
            item for item in self._addon_message_actions
            if isinstance(item, dict) and str(item.get("role") or "assistant") in {role, "all"}
        ]
        enabled_addon_ids = {
            str(item.get("addon_id") or "")
            for item in action_specs
            if str(item.get("addon_id") or "")
        }
        raw_statuses = (
            message.get("addon_action_status")
            if isinstance(message.get("addon_action_status"), dict)
            else {}
        )
        statuses = {
            str(addon_id): value
            for addon_id, value in raw_statuses.items()
            if str(addon_id) in enabled_addon_ids
        }
        if (
            action_specs
            or presentation_view is not None
            or statuses
            or self._formatted_replies_ui_enabled
        ):
            action_row = QWidget()
            action_row.setStyleSheet("background: transparent;")
            actions_layout = QHBoxLayout(action_row)
            actions_layout.setContentsMargins(0, 1, 0, 0)
            actions_layout.setSpacing(7)
            busy_labels = {t("Formatting…"), t("Checking meaning…")}
            busy = any(str(value or "").strip() in busy_labels for value in statuses.values())
            if self._formatted_replies_ui_enabled and role == "assistant":
                copy_button = QPushButton(t("Copy"))
                copy_button.setFlat(True)
                copy_button.setMinimumHeight(30)
                copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
                copy_button.setToolTip(t("Copy reply"))
                copy_button.setStyleSheet(
                    f"QPushButton {{ color: {_HINT}; background: transparent; border: none;"
                    " padding: 4px 8px; font-size: 8pt; }}"
                    f"QPushButton:hover {{ color: {_TEXT}; background: {_WHITE_BG_8}; border-radius: 6px; }}"
                )
                copy_button.clicked.connect(
                    lambda _checked=False, value=display_text: QApplication.clipboard().setText(value)
                )
                actions_layout.addWidget(copy_button)
            if presentation_view is not None:
                toggle = QPushButton(t("Show original"))
                toggle.setFlat(True)
                toggle.setMinimumHeight(30)
                toggle.setCursor(Qt.CursorShape.PointingHandCursor)
                toggle.setStyleSheet(
                    f"QPushButton {{ color: {_HINT}; background: transparent; border: none;"
                    " border-radius: 7px; padding: 4px 8px; font-size: 8pt; }}"
                    f"QPushButton:hover {{ color: {_TEXT}; background: {_WHITE_BG_8}; }}"
                )

                def toggle_presentation(
                    _checked=False,
                    button=toggle,
                    original=lbl,
                    rich=presentation_view,
                ) -> None:
                    showing_rich = rich.isVisible()
                    rich.setVisible(not showing_rich)
                    original.setVisible(showing_rich)
                    button.setText(t("Show formatted") if showing_rich else t("Show original"))

                toggle.clicked.connect(toggle_presentation)
                actions_layout.addWidget(toggle)
            for spec in action_specs:
                addon_id = str(spec.get("addon_id") or "")
                action_id = str(spec.get("id") or "")
                label = t(str(spec.get("label") or "Format"))
                if addon_id == presentation_addon_id:
                    label = t("Reformat")
                button = QPushButton(label)
                button.setObjectName("addonMessageActionButton")
                button.setProperty("addon_id", addon_id)
                button.setProperty("action_id", action_id)
                button.setProperty("conversation_index", conversation_index if conversation_index is not None else -1)
                button.setProperty("message_index", message_index if message_index is not None else -1)
                button.setFlat(True)
                button.setMinimumHeight(30)
                button.setMinimumWidth(72)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                formatter_model = str(spec.get("model") or "").strip()
                button.setToolTip(
                    t("Format this reply using {model}").format(model=formatter_model)
                    if formatter_model
                    else t("Format this reply in place")
                )
                button.setStyleSheet(
                    f"QPushButton {{ color: {_ACCENT}; background: transparent; border: none;"
                    " padding: 4px 6px; font-size: 8pt; font-weight: 600; text-align: left; }}"
                    f"QPushButton:hover {{ color: {_ACCENT_HOVER}; background: transparent; border: none; }}"
                    f"QPushButton:pressed {{ color: {_ACCENT}; background: transparent; border: none; }}"
                    f"QPushButton:disabled {{ color: {_DISABLED_TEXT}; background: transparent; }}"
                )
                if conversation_index is None or message_index is None or not addon_id or not action_id:
                    button.setEnabled(False)
                elif str(statuses.get(addon_id) or "").strip() in busy_labels:
                    button.setEnabled(False)
                else:
                    button.clicked.connect(
                        lambda _checked=False, ci=conversation_index, mi=message_index, aid=addon_id, act=action_id:
                        self._request_addon_message_action(ci, mi, aid, act)
                    )
                actions_layout.addWidget(button)
            status_values = [
                t(str(value or "").strip())
                for value in statuses.values()
                if str(value or "").strip()
            ]
            raw_errors = (
                message.get("addon_action_errors")
                if isinstance(message.get("addon_action_errors"), dict)
                else {}
            )
            if busy:
                spinner = ActivitySpinner()
                spinner.setStyleSheet(f"color: {_ACCENT}; background: transparent; font-size: 10pt;")
                spinner.setToolTip(t("Formatting reply"))
                spinner.start()
                actions_layout.addWidget(spinner)
            elif status_values:
                status_label = QLabel(status_values[-1])
                status_label.setWordWrap(True)
                error_details = [
                    str(raw_errors.get(addon_id) or "").strip()
                    for addon_id in statuses
                    if str(raw_errors.get(addon_id) or "").strip()
                ]
                if error_details:
                    status_label.setToolTip(error_details[-1])
                status_label.setStyleSheet(f"color: {_HINT}; background: transparent; font-size: 8pt;")
                actions_layout.addWidget(status_label)
            actions_layout.addStretch()
            if self._formatted_replies_ui_enabled and menu_btn is not None:
                actions_layout.addWidget(menu_btn)
            wl.addWidget(action_row)
        # Both columns scale with the live conversation pane instead of using a
        # fixed pixel width. Assistant replies are centered; user prompts stay
        # flush right. The 3:14:3 and 3:7 ratios cap each column at 70%.
        column_row = QWidget()
        column_row.setObjectName(
            "assistantMessageColumnRow" if role == "assistant" else "userMessageColumnRow"
        )
        column_row.setStyleSheet("background: transparent;")
        column_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        column_layout = QHBoxLayout(column_row)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(0)
        if role == "assistant":
            column_layout.addStretch(3)
            column_layout.addWidget(wrapper, 14, Qt.AlignmentFlag.AlignTop)
            column_layout.addStretch(3)
        else:
            column_layout.addStretch(3)
            column_layout.addWidget(wrapper, 7, Qt.AlignmentFlag.AlignTop)
        layout.insertWidget(layout.count() - 1, column_row)
        return lbl

    @staticmethod
    def _image_thumbnail_label(image_b64: str | None, role: str) -> QLabel | None:
        """Build a thumbnail for either user input or assistant output images."""
        if not image_b64:
            return None
        try:
            import base64

            img_bytes = base64.b64decode(image_b64)
            pixmap = QPixmap()
            pixmap.loadFromData(img_bytes)
            if pixmap.isNull():
                return None
            thumb = pixmap.scaled(
                360 if role == "assistant" else 280,
                240 if role == "assistant" else 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            img_lbl = QLabel()
            img_lbl.setPixmap(thumb)
            background = _AI_BG if role == "assistant" else _USER_BG
            img_lbl.setStyleSheet(
                f"QLabel {{ background: {background}; border-radius: 8px; padding: 4px; }}"
            )
            img_lbl.setFixedSize(thumb.width() + 8, thumb.height() + 8)
            return img_lbl
        except Exception:
            return None

    def _insert_bubble_image(
        self,
        view: _MessageTextView,
        image_b64: str | None,
        role: str,
    ) -> None:
        """Attach a late-arriving generated image above a live text view."""
        if bool(view.property("openwand_has_image")):
            return
        image_label = self._image_thumbnail_label(image_b64, role)
        if image_label is None:
            return
        wrapper = view.parentWidget()
        layout = wrapper.layout() if wrapper is not None else None
        if layout is None:
            return
        index = layout.indexOf(view)
        layout.insertWidget(index if index >= 0 else layout.count(), image_label)
        view.setProperty("openwand_has_image", True)

    def _open_message_menu(
        self,
        conversation_index: int,
        message_index: int,
        anchor: QWidget | None = None,
        local_pos=None,
    ) -> None:
        """Open actions for one message bubble."""
        if self._streaming:
            return
        if not (0 <= conversation_index < len(self._conversations)):
            return
        messages = self._conversations[conversation_index].get("messages", [])
        if not (0 <= message_index < len(messages)):
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {_TITLE_BG}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; }}"
            f"QMenu::item:selected {{ background: {_SEL_BG}; }}"
        )
        selected_text = ""
        if anchor is not None:
            text_view = anchor if isinstance(anchor, _MessageTextView) else anchor.findChild(_MessageTextView)
            if isinstance(text_view, _MessageTextView):
                selected_text = text_view.textCursor().selectedText().replace("\u2029", "\n").strip()
        if selected_text:
            menu.addAction(
                t("Copy selected text"),
                lambda text=selected_text: QApplication.clipboard().setText(text),
            )
            for item in self._ui_lab_context_actions(selected_text, messages[message_index]):
                label = str(item.get("label") or "").strip()
                action = str(item.get("action") or "").strip()
                match = str(item.get("match") or selected_text).strip()
                if label and action == "label_editor":
                    menu.addAction(label, lambda value=match, ci=conversation_index: self._edit_ui_lab_label(value, ci))
                elif label and action == "delete_label":
                    menu.addAction(label, lambda value=match, ci=conversation_index: self._delete_ui_lab_label(value, ci))
            menu.addSeparator()
        menu.addAction(
            t("Branch from here"),
            lambda ci=conversation_index, mi=message_index: self._branch_from_message(ci, mi),
        )
        menu.addSeparator()
        menu.addAction(
            t("Rewind current chat to here"),
            lambda ci=conversation_index, mi=message_index: self._rewind_to_message(ci, mi),
        )
        if anchor is None:
            pos = self.mapToGlobal(self.rect().center())
        elif local_pos is not None:
            pos = anchor.mapToGlobal(local_pos)
        else:
            pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        menu.popup(pos)

    def _ui_lab_context_actions(self, selected_text: str, message: dict) -> list[dict]:
        """Return UI Lab label actions for a selected chat message range."""
        try:
            from addons.ui_lab import get_text_context_actions

            return list(
                get_text_context_actions(
                    {
                        "selected_text": selected_text,
                        "text": str(message.get("display_content", message.get("content", "")) or ""),
                        "surface": "chat",
                        "role": str(message.get("role") or "assistant"),
                    }
                )
                or []
            )
        except Exception:
            return []

    def _edit_ui_lab_label(self, selected_text: str, conversation_index: int) -> None:
        """Open the UI Lab label editor from the chat window."""
        try:
            from ui.ui_lab_label_editor import edit_label

            if edit_label(selected_text, self):
                self._refresh_ui_lab_labels(conversation_index)
        except Exception:
            return

    def _delete_ui_lab_label(self, selected_text: str, conversation_index: int) -> None:
        """Delete a UI Lab label from the chat window."""
        try:
            from ui.ui_lab_label_editor import delete_label

            if delete_label(selected_text, self):
                self._refresh_ui_lab_labels(conversation_index)
        except Exception:
            return

    def _refresh_ui_lab_labels(self, conversation_index: int) -> None:
        """Rebuild the current page so saved UI Lab labels apply immediately."""
        if not (0 <= conversation_index < len(self._conversations)):
            return
        self._built_pages.discard(conversation_index)
        self._switch(conversation_index)

    def _conversation_slice(self, conv: dict, message_index: int, *, new_id: bool) -> dict:
        """Copy a conversation through one message and rebuild hidden context."""
        retained = deepcopy((conv.get("messages") or [])[: message_index + 1])
        now = _now_iso()
        for msg in retained:
            if isinstance(msg, dict):
                _ensure_message_metadata(msg, fallback_created_at=conv.get("created_at") or now)
        retained_all = message_index == len(conv.get("messages", []) or []) - 1
        context = _context_from_messages(retained)
        file_context = _merge_file_context_from_messages(retained)
        tool_context = _latest_tool_context_from_messages(retained)
        if retained_all:
            context = context or str(conv.get("context") or "")
            file_context = file_context or _normalized_file_context(conv.get("file_context") or [])
            tool_context = tool_context or _normalized_tool_context(conv.get("tool_context") or {})
        sliced = {
            "id": str(uuid.uuid4()) if new_id else (conv.get("id") or str(uuid.uuid4())),
            "project_id": conv.get("project_id") or _GENERAL_PROJECT_ID,
            "messages": retained,
            "context": context,
            "file_context": file_context,
            "tool_context": tool_context,
            "context_policy": _normalized_context_policy(conv.get("context_policy") or {}),
            "created_at": conv.get("created_at") or now,
            "updated_at": now,
        }
        return sliced

    def _branch_from_message(self, conversation_index: int, message_index: int) -> None:
        """Create and select a non-destructive branch ending at a message."""
        if self._streaming or not (0 <= conversation_index < len(self._conversations)):
            return
        conv = self._conversations[conversation_index]
        if not (0 <= message_index < len(conv.get("messages", []))):
            return
        branch = self._conversation_slice(conv, message_index, new_id=True)
        self._conversations.append(branch)
        if self._has_placeholder:
            placeholder = self._stack.widget(0)
            self._stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self._has_placeholder = False
        idx = len(self._conversations) - 1
        self._stack.addWidget(self._make_page(idx, branch))
        self._rebuild_sidebar()
        self._switch(idx)
        self._persist()

    def _rewind_to_message(self, conversation_index: int, message_index: int) -> None:
        """Destructively truncate the active conversation after confirmation."""
        if self._streaming or conversation_index != self._active_idx:
            return
        if not (0 <= conversation_index < len(self._conversations)):
            return
        conv = self._conversations[conversation_index]
        messages = conv.get("messages", [])
        if not (0 <= message_index < len(messages)) or message_index == len(messages) - 1:
            return
        if QMessageBox.question(
            self,
            t("Rewind conversation"),
            t("Remove all messages after this one? This cannot be undone."),
        ) != QMessageBox.StandardButton.Yes:
            return
        sliced = self._conversation_slice(conv, message_index, new_id=False)
        conv.clear()
        conv.update(sliced)
        self._built_pages.discard(conversation_index)
        self._rebuild_sidebar()
        self._switch(conversation_index)
        self._persist()

    def _active_layout(self):
        """Handle active layout for chat window."""
        active_idx = self._active_idx
        if active_idx < 0 or active_idx >= self._stack.count():
            return None
        page = self._stack.widget(active_idx)
        return getattr(page, "_msg_layout", None)

    def _active_scroll(self) -> QScrollArea | None:
        """Handle active scroll for chat window."""
        active_idx = self._active_idx
        if active_idx < 0 or active_idx >= self._stack.count():
            return None
        page = self._stack.widget(active_idx)
        return page if isinstance(page, QScrollArea) else None

    def _is_near_transcript_bottom(self, scroll: QScrollArea | None = None) -> bool:
        """Return whether the transcript is close enough to safely keep following."""
        target = scroll or self._active_scroll()
        if target is None:
            return True
        try:
            bar = target.verticalScrollBar()
            return bar.maximum() - bar.value() <= _CHAT_FOLLOW_BOTTOM_THRESHOLD_PX
        except RuntimeError:
            return True

    def _begin_stream_follow(self, conversation_index: int) -> None:
        """Start a reply in follow mode; a later manual scroll can latch it off."""
        self._stream_follow_enabled = True
        self._stream_follow_idx = conversation_index
        if self._unseen_reply_idx == conversation_index:
            self._unseen_reply_idx = None
        self._update_jump_to_latest_visibility()

    def _on_transcript_manual_scroll(self, scroll: QScrollArea) -> None:
        """Latch streaming follow off when the reader deliberately moves upward."""
        if scroll is not self._active_scroll():
            return
        at_bottom = self._is_near_transcript_bottom(scroll)
        if at_bottom:
            if self._streaming and self._stream_follow_idx == self._active_idx:
                self._stream_follow_enabled = True
            if self._unseen_reply_idx == self._active_idx:
                self._unseen_reply_idx = None
        elif self._streaming and self._stream_follow_idx == self._active_idx:
            self._stream_follow_enabled = False
            self._unseen_reply_idx = self._active_idx
        self._update_jump_to_latest_visibility()

    def _update_jump_to_latest_visibility(self) -> None:
        """Show a compact recovery control while newer reply content is below."""
        button = getattr(self, "_jump_to_latest_btn", None)
        if button is None:
            return
        visible = (
            self._unseen_reply_idx == self._active_idx
            and not self._is_near_transcript_bottom()
        )
        button.setVisible(visible)

    def _jump_to_latest(self) -> None:
        """Resume following and reveal the newest reply content."""
        self._stream_follow_enabled = True
        self._stream_follow_idx = self._active_idx
        if self._unseen_reply_idx == self._active_idx:
            self._unseen_reply_idx = None
        self._scroll_bottom(force=True)

    def _scroll_bottom(self, *, force: bool = False):
        """Follow the newest content unless the reader intentionally scrolled away."""
        scroll = self._active_scroll()
        if scroll is None:
            return
        following_active_stream = (
            self._streaming and self._stream_follow_idx == self._active_idx
        )
        if following_active_stream and not self._stream_follow_enabled and not force:
            self._unseen_reply_idx = self._active_idx
            self._update_jump_to_latest_visibility()
            return

        def scroll_if_still_following(target=scroll, forced=force) -> None:
            try:
                if (
                    not forced
                    and self._streaming
                    and self._stream_follow_idx == self._active_idx
                    and not self._stream_follow_enabled
                ):
                    self._unseen_reply_idx = self._active_idx
                    self._update_jump_to_latest_visibility()
                    return
                bar = target.verticalScrollBar()
                bar.setValue(bar.maximum())
                if target is self._active_scroll() and self._unseen_reply_idx == self._active_idx:
                    self._unseen_reply_idx = None
                self._update_jump_to_latest_visibility()
            except RuntimeError:
                return

        QTimer.singleShot(0, scroll_if_still_following)

    # ------------------------------------------------------------------ Drops

    def dragEnterEvent(self, event):  # noqa: N802
        """Accept file/text/image drops as pending message attachments."""
        mime = event.mimeData()
        if mime and (mime.hasUrls() or mime.hasText() or mime.hasImage()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):  # noqa: N802
        """Attach dropped files, text, or images to the next chat message."""
        if self._add_attachments_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _choose_attachments(self) -> None:
        """Open a picker and attach selected files to the next outgoing turn."""
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            t("Add files or images"),
            "",
            (
                f"{t('Supported files')} (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.tiff *.tif "
                "*.txt *.md *.py *.js *.ts *.json *.yaml *.yml *.csv *.html *.css *.xml "
                "*.doc *.docx *.docm *.pdf *.xls *.xlsx *.xlsm *.xlsb "
                "*.ppt *.pps *.pot *.pptx *.pptm *.ppsx *.ppsm "
                "*.odt *.ods *.odp *.rtf *.epub);;"
                f"{t('All files')} (*)"
            ),
        )
        self._add_attachment_paths(paths)

    def _add_attachment_paths(self, paths: list[str]) -> bool:
        """Attach local files by reusing the drag/drop MIME extraction path."""
        local_paths = [str(path or "").strip() for path in paths or [] if str(path or "").strip()]
        if not local_paths:
            return False
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path in local_paths])
        return self._add_attachments_from_mime(mime)

    def _add_attachments_from_mime(self, mime) -> bool:
        """Convert dropped MIME data into next-message context/image attachments."""
        external_names: set[str] = set()
        for ref in self._attachment_refs_from_mime(mime):
            self._add_pending_attachment_ref(ref)
            external_names.add(str(ref.get("name") or ""))
        try:
            from ui.drop_zone import process_drop_mime
            raw_items = process_drop_mime(mime)
        except Exception:
            raw_items = []
        return self._add_attachment_items(raw_items, external_names=external_names) or bool(external_names)

    def _attachment_refs_from_mime(self, mime) -> list[dict]:
        """Return path-only refs for local files in a drag/drop or picker MIME payload."""
        refs: list[dict] = []
        try:
            urls = mime.urls() if mime and mime.hasUrls() else []
        except Exception:
            urls = []
        for url in urls:
            try:
                if not url.isLocalFile():
                    continue
                path = str(url.toLocalFile() or "").strip()
            except Exception:
                path = ""
            if not path:
                continue
            ref = _conversation_store.external_file_attachment(path)
            if not any(existing.get("path") == ref.get("path") for existing in refs):
                refs.append(ref)
        return refs

    def _add_pending_attachment_ref(self, ref: dict) -> None:
        """Queue one attachment reference for the next outgoing message."""
        normalized = _conversation_store.normalize_attachments([ref])
        if not normalized:
            return
        item = normalized[0]
        if not any(existing.get("path") == item.get("path") for existing in self._pending_attachments):
            self._pending_attachments.append(item)
        label = str(item.get("name") or item.get("path") or "Attachment")
        if label and label not in self._pending_attachment_labels:
            self._pending_attachment_labels.append(label)

    def _add_attachment_items(
        self,
        raw_items: list[tuple[str, str, str]],
        *,
        external_names: set[str] | None = None,
    ) -> bool:
        """Attach normalized drop-zone items to the next outgoing chat turn."""
        if not raw_items:
            return False
        external_names = external_names or set()
        image_labels: list[str] = []
        context_items: list[tuple[str, str, str]] = []
        fallback_lines: list[str] = []
        for name, content, item_type in raw_items:
            label = str(name or "Attachment")
            kind = str(item_type or "text")
            if label in external_names:
                continue
            if kind == "image" and self._pending_attachment_image_b64 is None:
                self._pending_attachment_image_b64 = str(content or "")
                image_labels.append(label)
            elif kind == "image":
                fallback_lines.append(f"[Attached image: {label}]")
            else:
                context_items.append((label, str(content or ""), kind))

        context = self._attachment_context_from_items(context_items)
        parts = [
            part
            for part in (self._pending_attachment_context, context, "\n".join(fallback_lines))
            if part.strip()
        ]
        self._pending_attachment_context = "\n\n".join(parts)
        if len(self._pending_attachment_context) > _ATTACHMENT_CONTEXT_CHAR_LIMIT:
            self._pending_attachment_context = (
                self._pending_attachment_context[:_ATTACHMENT_CONTEXT_CHAR_LIMIT].rstrip()
                + "\n[attached context truncated]"
            )

        labels = image_labels + [name for name, _content, _kind in context_items]
        for label in labels:
            if label and label not in self._pending_attachment_labels:
                self._pending_attachment_labels.append(label)
        self._refresh_attachment_label()
        return bool(labels or fallback_lines)

    def _attachment_context_from_items(self, items: list[tuple[str, str, str]]) -> str:
        """Render dropped text/document items as model-visible context."""
        if not items:
            return ""
        try:
            from core.query_pipeline import ContextInputs, build_context
            built = build_context(ContextInputs(intent_prompt="", drop_items=items))
            return str(built.ambient_ctx or "").strip()
        except Exception:
            lines = []
            for name, content, _kind in items:
                text = str(content or "").strip()
                if text:
                    lines.append(f"[{name}]\n{text}")
            return "\n\n".join(lines)

    def _refresh_attachment_label(self) -> None:
        """Update names and an image thumbnail before the pending turn is submitted."""
        if self._attachment_label is None:
            return
        preview_frame = getattr(self, "_attachment_preview", None)
        thumbnail = getattr(self, "_attachment_thumbnail", None)
        if not self._pending_attachment_labels:
            self._attachment_label.setText("")
            self._attachment_label.setToolTip("")
            if thumbnail is not None:
                thumbnail.clear()
                thumbnail.hide()
            if preview_frame is not None:
                preview_frame.hide()
            return
        names = ", ".join(self._pending_attachment_labels[:4])
        if len(self._pending_attachment_labels) > 4:
            names += f", +{len(self._pending_attachment_labels) - 4}"
        self._attachment_label.setText(f"{t('Attached')} · {html.escape(names)}")
        self._attachment_label.setToolTip("\n".join(self._pending_attachment_labels))
        self._attachment_label.show()

        image_b64 = self._pending_attachment_image_b64
        if not image_b64:
            for ref in self._pending_attachments:
                if str(ref.get("kind") or "").lower() != "image":
                    continue
                image_b64 = _conversation_store.attachment_image_base64(ref)
                if image_b64:
                    break
        if thumbnail is not None:
            preview = self._image_thumbnail_label(image_b64, "user")
            pixmap = preview.pixmap() if preview is not None else QPixmap()
            if not pixmap.isNull():
                thumbnail.setPixmap(
                    pixmap.scaled(
                        40,
                        40,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                thumbnail.show()
            else:
                thumbnail.clear()
                thumbnail.hide()
        if preview_frame is not None:
            preview_frame.show()

    def _consume_pending_attachments(self) -> tuple[str, str | None, list[str], list[dict]]:
        """Return and clear pending context/image attachments."""
        context = self._pending_attachment_context
        image = self._pending_attachment_image_b64
        labels = list(self._pending_attachment_labels)
        attachments = list(self._pending_attachments)
        self._pending_attachment_context = ""
        self._pending_attachment_image_b64 = None
        self._pending_attachments = []
        self._pending_attachment_labels = []
        self._refresh_attachment_label()
        return context, image, labels, attachments

    # ------------------------------------------------------------------ Sending

    def _on_send_clicked(self):
        """Handle send clicked events."""
        text = self._input.toPlainText().strip()
        if not text and self._pending_attachment_labels:
            text = t("Please review the attached file.")
        if text and not self._streaming:
            self._input.clear()
            self._send(text)

    def _send(self, text: str):
        """Send the chat window workflow."""
        if self._streaming or not self._conversations:
            return
        self.refresh_model_label()
        if self._on_select and 0 <= self._active_idx < len(self._conversations):
            self._on_select(self._active_idx)
        self._streaming = True
        self._streaming_idx = self._active_idx
        self._harness_activity_state["agents"] = {}
        if hasattr(self, "_harness_activity_button"):
            self._harness_activity_button.setText(t("Agents"))
        self._begin_stream_follow(self._active_idx)
        self._send_btn.setEnabled(False)
        self._new_chat_btn.setEnabled(False)

        conv = self._conversations[self._active_idx]
        _ensure_conversation_metadata(conv)
        attachment_context, attachment_image, attachment_labels, attachment_refs = self._consume_pending_attachments()
        now = _touch_conversation(conv)
        user_message = {"role": "user", "content": text, "created_at": now}
        _ensure_message_metadata(user_message, fallback_created_at=now)
        if attachment_image:
            try:
                attachment_refs.append(
                    _conversation_store.save_image_attachment(
                        attachment_image,
                        conversation_id=str(conv.get("id") or ""),
                        message_id=str(user_message.get("id") or ""),
                        source="pasted_image",
                        name=(attachment_labels[0] if attachment_labels else "image.png"),
                    )
                )
            except Exception:
                # Keep the image available for this one live request, but do not
                # allow the base64 blob into persisted conversation history.
                user_message["image_base64"] = attachment_image
        attachments = _conversation_store.normalize_attachments(attachment_refs)
        if attachments:
            user_message["attachments"] = attachments
        if attachment_context:
            label = ", ".join(attachment_labels) if attachment_labels else t("Attachments")
            attachment_context_block = f"[{t('Attached')} · {label}]\n{attachment_context.strip()}"
        else:
            attachment_context_block = ""
        attachment_summary = _attachment_summary_context(attachments)
        message_context = "\n\n".join(
            part for part in (attachment_context_block, attachment_summary) if part.strip()
        )
        if message_context:
            user_message["context"] = message_context
        context_policy = _ensure_conversation_context_policy(conv)

        layout = self._active_layout()
        if layout:
            self._bubble(
                layout,
                text,
                "user",
                _conversation_store.first_image_base64_from_message(user_message),
                created_at=now,
                conversation_index=self._active_idx,
                message_index=len(conv["messages"]),
            )
            msg_hint = self._message_context_hint(message_context)
            if msg_hint is not None:
                layout.insertWidget(layout.count() - 1, msg_hint)
        conv["messages"].append(user_message)
        self._persist()

        self._current_ai_text = ""
        self._current_ai_reply_text = ""
        self._current_ai_segments = []
        self._current_ai_status_text = ""
        self._current_ai_parser = ThoughtStreamParser()
        self._current_ai_annotations = []
        self._current_ai_attachments = []
        self._current_file_context = []
        self._current_tool_context = {}
        self._current_context_snippets = []
        self._current_harness = {}
        self._current_local_work_dialog = None
        self._current_local_work_notice = None
        self._current_user_message = user_message
        self._current_ai_label = self._bubble(layout, "...", "assistant", created_at=_now_iso()) if layout else None
        self._scroll_bottom()

        # Keep the system/tool prefix stable. Legacy conversation context and
        # message-scoped attachments ride with the volatile user turn.
        ctx = _context_not_anchored_to_messages(conv.get("context", ""), conv["messages"])
        from core.llm_clients.prompt_guidance import with_browser_retrieval_note

        sys_content = with_browser_retrieval_note(
            config.get_system_prompt(),
            tool_modes.context_mode(context_policy, "browser") == "model",
        )
        dynamic_context: list[str] = []
        if ctx:
            dynamic_context.append(f"[Conversation Context]\n{ctx}")
        file_ctx = _file_context_text(conv.get("file_context") or [])
        if file_ctx:
            dynamic_context.append(file_ctx)
        messages = [{"role": "system", "content": sys_content}] + _chat_model_messages(conv["messages"])
        if dynamic_context:
            from core.llm_clients.messages import build_contextual_user_text

            for message in reversed(messages):
                if message.get("role") == "user":
                    message["content"] = build_contextual_user_text(
                        str(message.get("content") or ""),
                        ambient_context="\n\n".join(dynamic_context),
                    )
                    break

        def _stream():
            """Stream the chat window workflow."""
            try:
                kwargs = {"context_policy": dict(context_policy)}
                try:
                    signature = inspect.signature(self._send_fn)
                    accepts_policy = (
                        "context_policy" in signature.parameters
                        or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
                    )
                except (TypeError, ValueError):
                    accepts_policy = False
                source = self._send_fn(messages, **kwargs) if accepts_policy else self._send_fn(messages)
                for item in source:
                    if isinstance(item, dict) and item.get("type") == "final":
                        self._signals.final.emit(str(item.get("text") or ""))
                    elif isinstance(item, dict) and item.get("type") == "metadata":
                        self._signals.metadata.emit(item)
                    elif isinstance(item, dict) and item.get("type") == "chunk":
                        self._signals.chunk.emit(item)
                    else:
                        self._signals.chunk.emit(str(item or ""))
            finally:
                self._signals.finished.emit()

        threading.Thread(target=_stream, daemon=True).start()

    def _on_chunk(self, chunk: object):
        """Handle chunk events."""
        if isinstance(chunk, dict):
            harness_activity = chunk.get("harness_activity")
            if isinstance(harness_activity, dict) and harness_activity:
                self._on_harness_activity(harness_activity)
                if not str(chunk.get("text") or ""):
                    return
            local_work = chunk.get("local_work")
            if isinstance(local_work, dict) and local_work:
                self._on_local_work_activity(local_work)
                if not str(chunk.get("text") or ""):
                    return
            text = str(chunk.get("text") or "")
            is_thought = bool(chunk.get("is_thought"))
            is_progress = bool(chunk.get("is_progress"))
            if is_progress and not is_thought:
                # Match the overlay bubble: startup/status notices replace each
                # other until durable thought, tool, or reply content arrives.
                self._current_ai_status_text = text.strip()
                self._render_current_ai_stream()
                self._scroll_bottom()
                return
            self._current_ai_status_text = ""
            if is_thought or is_progress:
                if not self._current_ai_segments:
                    text = text.lstrip("\r\n")
                _merge_display_segments(self._current_ai_segments, text, True)
                self._render_current_ai_stream()
                self._scroll_bottom()
                return
            chunk = text
        self._current_ai_status_text = ""
        chunk = str(chunk or "")
        if (
            self._current_ai_segments
            and self._current_ai_segments[-1][1]
            and self._current_ai_reply_text
            and not self._current_ai_reply_text.endswith(("\r", "\n"))
            and not chunk.startswith(("\r", "\n"))
        ):
            # A reply resuming after thought/tool activity is a new block. Token
            # deltas inside an uninterrupted reply still concatenate normally.
            chunk = f"\n{chunk}"
        self._current_ai_text += chunk
        if self._current_ai_parser is None:
            self._current_ai_parser = ThoughtStreamParser()
        for text, is_thought in self._current_ai_parser.feed(chunk):
            _merge_display_segments(self._current_ai_segments, text, is_thought)
            if not is_thought:
                self._current_ai_reply_text += text
        self._render_current_ai_stream()
        self._scroll_bottom()

    def _on_local_work_activity(self, event: dict) -> None:
        """Show one opt-in monitor link and update its hidden progress window."""
        dialog = self._current_local_work_dialog
        if dialog is None:
            dialog = LocalWorkProgressDialog(self)
            self._current_local_work_dialog = dialog
            self._local_work_dialogs.append(dialog)

        dialog.add_activity(event)
        if self._current_local_work_notice is not None:
            return
        layout = self._active_layout()
        if layout is None:
            return

        notice = QLabel()
        notice.setObjectName("localWorkMonitorNotice")
        notice.setTextFormat(Qt.TextFormat.RichText)
        notice.setOpenExternalLinks(False)
        notice.setWordWrap(True)
        notice.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        notice.setStyleSheet(
            f"QLabel#localWorkMonitorNotice {{ color: {_HINT}; background: transparent;"
            " padding: 2px 0 6px 0; }}"
        )
        linked_here = (
                        f"<a href='openwand-local-work' style='color:{_ACCENT}; text-decoration:underline;'>"
            f"&nbsp;{html.escape(t('here'))}&nbsp;</a>"
        )
        notice.setText(
            t("The model is working with local files — follow progress {here}.").format(
                here=linked_here,
            )
        )
        notice.linkActivated.connect(lambda _href, target=dialog: self._open_local_work_monitor(target))
        layout.insertWidget(max(0, layout.count() - 1), notice)
        self._current_local_work_notice = notice
        self._scroll_bottom()

    @staticmethod
    def _open_local_work_monitor(dialog: LocalWorkProgressDialog) -> None:
        """Open and focus the selected turn's local-work monitor."""
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _render_current_ai_stream(self) -> None:
        """Render durable chronological segments plus the replaceable live status."""
        if not self._current_ai_label:
            return
        segments = list(self._current_ai_segments)
        if self._current_ai_status_text:
            _merge_display_segments(segments, self._current_ai_status_text, True)
        self._current_ai_label.setHtml(
            _assistant_segments_to_html(_truncate_segments_for_display(segments))
        )

    def _on_final_text(self, text: str):
        """Replace the streamed draft with the final assistant text."""
        if not text or text == self._current_ai_text:
            return
        self._current_ai_status_text = ""
        self._current_ai_text = text
        self._current_ai_reply_text = ""
        self._current_ai_segments = []
        self._current_ai_parser = ThoughtStreamParser()
        for segment, is_thought in self._current_ai_parser.feed(text):
            _merge_display_segments(self._current_ai_segments, segment, is_thought)
            if not is_thought:
                self._current_ai_reply_text += segment
        flushed = self._current_ai_parser.finish()
        self._current_ai_segments = merge_segment_iterables(self._current_ai_segments, flushed)
        for segment, is_thought in flushed:
            if not is_thought:
                self._current_ai_reply_text += segment
        self._current_ai_parser = None
        if self._current_ai_label:
            self._current_ai_label.setHtml(
                _assistant_segments_to_html(_truncate_segments_for_display(self._current_ai_segments))
            )
        self._scroll_bottom()

    def _on_metadata(self, item: object):
        """Capture display-hidden metadata returned with the reply."""
        if isinstance(item, dict):
            self._current_file_context = _normalized_file_context(item.get("file_context") or [])
            self._current_tool_context = _normalized_tool_context(item.get("tool_context") or {})
            self._current_context_snippets = _normalized_context_snippets(item.get("context_snippets") or [])
            self._current_ai_annotations = list(item.get("annotations") or [])
            if "assistant_attachments" in item:
                self._current_ai_attachments = _conversation_store.normalize_attachments(
                    item.get("assistant_attachments") or []
                )
            if self._current_ai_label is not None and self._current_ai_attachments:
                image_b64 = _conversation_store.attachment_image_base64(
                    self._current_ai_attachments[0]
                )
                self._insert_bubble_image(
                    self._current_ai_label,
                    image_b64,
                    "assistant",
                )
            self._current_harness = dict(item.get("harness") or {})
            final_segments = _normalized_display_segments(item.get("display_segments") or [])
            if final_segments:
                self._current_ai_segments = final_segments
                self._render_current_ai_stream()
            if self._current_user_message is not None:
                user_annotations = list(item.get("user_annotations") or [])
                if user_annotations:
                    self._current_user_message["annotations"] = user_annotations

    def request_live_file_approval(self, request: dict) -> dict:
        """Show a file-tool approval request inline in the active chat."""
        details = request.get("details") if isinstance(request.get("details"), dict) else {}
        action = str(request.get("action") or "file edit")
        path = str(request.get("path") or details.get("path") or "").strip()
        diff = str(request.get("diff") or details.get("diff") or "").strip()
        plus = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
        minus = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
        callback = request.get("_on_decision")
        register_resolver = request.get("_register_resolver")
        title = t("Approve this file change?")
        lines = [f"<b>{html.escape(title)}</b>"]
        if action:
            lines.append(html.escape(t("Why: Files is set to ask before write, so OpenWand needs approval before changing disk.")))
            lines.append(f"{html.escape(t('Tool:'))} {html.escape(action)}")
        if path:
            lines.append(f"{html.escape(t('Target:'))} {html.escape(path)}")
        if "old_chars" in details or "new_chars" in details:
            lines.append(
                html.escape(
                    t("Change: replace {old} chars with {new} chars").format(
                        old=int(details.get("old_chars") or 0),
                        new=int(details.get("new_chars") or 0),
                    )
                )
            )
        elif "chars" in details:
            template = "Change: overwrite file with {chars} chars" if details.get("exists") else "Change: create file with {chars} chars"
            lines.append(html.escape(t(template).format(chars=int(details.get("chars") or 0))))
        if diff:
            lines.append(html.escape(t("Diff: +{added} -{removed} lines").format(added=plus, removed=minus)))
        if diff:
            preview = html.escape(diff[:1200])
            if len(diff) > 1200:
                preview += "\n..."
            lines.append(f"<pre style='white-space: pre-wrap;'>{preview}</pre>")

        layout = self._active_layout()
        if layout is None:
            return {"approved": False, "shown": False}

        frame = QFrame()
        frame.setObjectName("liveFileApprovalPanel")
        frame.setStyleSheet(
            f"QFrame#liveFileApprovalPanel {{ background: {_ACCENT_BG_18}; color: {_TEXT};"
            f" border: 1px solid {_ACCENT_BG_60}; border-radius: 6px; }}"
        )
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)
        label = QLabel("<br>".join(lines))
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(label)
        feedback_box = QTextEdit()
        feedback_box.setPlaceholderText(t("Tell OpenWand what to change before trying again."))
        feedback_box.setFixedHeight(72)
        feedback_box.setVisible(False)
        feedback_box.setStyleSheet(
            f"QTextEdit {{ background: {_USER_BG}; color: {_TEXT}; border: 1px solid {_BORDER};"
            " border-radius: 6px; padding: 6px; }}"
        )
        outer.addWidget(feedback_box)
        row = QHBoxLayout()
        row.addStretch()
        approve = QPushButton(t("Approve"))
        request_changes = QPushButton(t("Alternate option"))
        deny = QPushButton(t("Decline"))
        for btn in (approve, request_changes, deny):
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        approve.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: {_ON_ACCENT}; border: none;"
            " border-radius: 6px; padding: 4px 14px; font-weight: 700; }}"
        )
        secondary_style = (
            f"QPushButton {{ background: {_WHITE_BG_12}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; padding: 4px 14px; }}"
        )
        request_changes.setStyleSheet(secondary_style)
        deny.setStyleSheet(secondary_style)
        row.addWidget(approve)
        row.addWidget(request_changes)
        row.addWidget(deny)
        outer.addLayout(row)

        state = {"done": False, "approved": False, "feedback": ""}
        loop = QEventLoop()

        def finish(value: bool, feedback: str = "", *, notify: bool = True) -> None:
            if state["done"]:
                return
            state["done"] = True
            state["approved"] = bool(value)
            state["feedback"] = str(feedback or "").strip()
            approve.setEnabled(False)
            request_changes.setEnabled(False)
            deny.setEnabled(False)
            frame.hide()
            frame.deleteLater()
            if notify and callable(callback):
                callback(
                    {
                        "approved": bool(state["approved"]),
                        "feedback": str(state.get("feedback") or "").strip(),
                        "shown": True,
                    }
                )
            loop.quit()

        def request_change_feedback() -> None:
            if not feedback_box.isVisible():
                feedback_box.setVisible(True)
                request_changes.setText(t("Send alternate option"))
                feedback_box.setFocus()
                self._scroll_bottom()
                return
            feedback = feedback_box.toPlainText().strip()
            if not feedback:
                feedback_box.setFocus()
                return
            finish(False, feedback)

        def cancel_if_destroyed(*_args: object) -> None:
            if state["done"]:
                return
            state["done"] = True
            state["approved"] = False
            loop.quit()

        approve.clicked.connect(lambda: finish(True))
        request_changes.clicked.connect(request_change_feedback)
        deny.clicked.connect(lambda: finish(False))
        frame.destroyed.connect(cancel_if_destroyed)
        if callable(register_resolver):
            register_resolver(lambda value=False, feedback="": finish(bool(value), str(feedback or ""), notify=False))
        layout.insertWidget(layout.count() - 1, frame)
        self._scroll_bottom()
        if callable(callback):
            return {"approved": False, "feedback": "", "shown": True}
        loop.exec()
        return {
            "approved": bool(state["approved"]),
            "feedback": str(state.get("feedback") or "").strip(),
            "shown": True,
        }

    def _on_finished(self):
        """Handle finished events."""
        auto_action: tuple[int, int, str, str] | None = None
        completed_action_message: tuple[int, int] | None = None
        completed_workspace_changes = False
        pending_workspace_changes = self._current_harness.get("workspace_changes")
        has_workspace_changes = (
            isinstance(pending_workspace_changes, dict)
            and bool(pending_workspace_changes.get("files"))
        )
        self._current_ai_status_text = ""
        if self._current_local_work_dialog is not None:
            self._current_local_work_dialog.mark_finished()
        if self._current_ai_parser is not None:
            flushed = self._current_ai_parser.finish()
            self._current_ai_segments = merge_segment_iterables(self._current_ai_segments, flushed)
            for text, is_thought in flushed:
                if not is_thought:
                    self._current_ai_reply_text += text
            if self._current_ai_label:
                self._current_ai_label.setHtml(
                    _assistant_segments_to_html(_truncate_segments_for_display(self._current_ai_segments))
                )
        if (
            self._current_ai_label is not None
            and self._current_ai_attachments
            and not self._current_ai_segments
            and not self._current_ai_reply_text
        ):
            self._current_ai_label.hide()
        if self._current_context_snippets:
            if isinstance(self._current_user_message, dict):
                self._current_user_message["context_snippets"] = list(self._current_context_snippets)
            self._insert_live_context_snippets()
        if (
            (self._current_ai_reply_text or self._current_ai_attachments or has_workspace_changes)
            and self._conversations
            and 0 <= self._active_idx < len(self._conversations)
        ):
            conv = self._conversations[self._active_idx]
            stamp = _touch_conversation(conv)
            message = {"role": "assistant", "content": self._current_ai_reply_text, "created_at": stamp}
            _ensure_message_metadata(message, fallback_created_at=stamp)
            if self._current_ai_annotations:
                message["annotations"] = list(self._current_ai_annotations)
            if self._current_ai_attachments:
                message["attachments"] = list(self._current_ai_attachments)
            durable_segments = list(self._current_ai_segments)
            if durable_segments and any(is_thought for _text, is_thought in durable_segments):
                message["display_segments"] = [
                    {"text": text, "is_thought": is_thought}
                    for text, is_thought in durable_segments
                    if text
                ]
                message["display_content"] = _segments_to_display_content(durable_segments)
            elif self._current_ai_text != self._current_ai_reply_text:
                message["display_content"] = self._current_ai_text
            if self._current_file_context:
                message["file_context"] = self._current_file_context
            if self._current_tool_context:
                message["tool_context"] = self._current_tool_context
            if has_workspace_changes:
                message["workspace_changes"] = deepcopy(pending_workspace_changes)
                completed_workspace_changes = True
            conv["messages"].append(message)
            message_index = len(conv["messages"]) - 1
            completed_action_message = (self._active_idx, message_index)
            for action in self._addon_message_actions:
                if (
                    isinstance(action, dict)
                    and bool(action.get("auto"))
                    and str(action.get("role") or "assistant") in {"assistant", "all"}
                ):
                    addon_id = str(action.get("addon_id") or "")
                    action_id = str(action.get("id") or "")
                    if addon_id and action_id:
                        auto_action = (self._active_idx, message_index, addon_id, action_id)
                        break
            _merge_file_context(conv, self._current_file_context)
            _merge_tool_context(conv, self._current_tool_context)
            harness_provider = str(self._current_harness.get("provider") or "").strip().lower()
            harness_session_id = str(self._current_harness.get("session_id") or "").strip()
            if (
                harness_provider in {"codex", "claude"}
                and bool(self._current_harness.get("clear_session"))
            ):
                sessions = conv.get("harness_sessions")
                if isinstance(sessions, dict):
                    sessions.pop(harness_provider, None)
                    if not sessions:
                        conv.pop("harness_sessions", None)
            elif harness_provider in {"codex", "claude"} and harness_session_id:
                conv.setdefault("harness_sessions", {})[harness_provider] = {
                    "provider": harness_provider,
                    "session_id": harness_session_id,
                    "cwd": str(self._current_harness.get("cwd") or ""),
                    "updated_at": stamp,
                }
            if self._persist_fn:
                try:
                    self._persist_fn()
                except Exception:
                    pass
            if (
                self._current_ai_label is not None
                and self._current_ai_annotations
                and not any(is_thought for _text, is_thought in self._current_ai_segments)
            ):
                display_text = _truncate_for_display(
                    self._current_ai_reply_text,
                    _CHAT_RENDER_CHAR_LIMIT,
                    "chat display",
                )
                self._current_ai_label.setHtml(
                    _assistant_text_to_html(display_text, annotations=self._current_ai_annotations)
                )
        self._current_ai_label = None
        self._current_ai_text = ""
        self._current_ai_reply_text = ""
        self._current_ai_segments = []
        self._current_ai_status_text = ""
        self._current_ai_parser = None
        self._current_ai_annotations = []
        self._current_ai_attachments = []
        self._current_file_context = []
        self._current_tool_context = {}
        self._current_context_snippets = []
        self._current_harness = {}
        self._current_user_message = None
        self._streaming = False
        self._streaming_idx = None
        self._update_jump_to_latest_visibility()
        self._send_btn.setEnabled(True)
        self._new_chat_btn.setEnabled(True)
        if self._pending_addon_ui_refresh:
            self._apply_addon_ui_mode()
        elif completed_action_message is not None and (
            self._addon_message_actions or completed_workspace_changes
        ):
            # The streaming bubble was created before this stored message had
            # stable indices, so its Format control was necessarily disabled.
            # Rebind the finished turn immediately; reopening Chat must never
            # be required to make the last reply actionable.
            self._refresh_addon_message_page(completed_action_message[0])
        if auto_action is not None:
            QTimer.singleShot(0, lambda args=auto_action: self._request_addon_message_action(*args))

    def _insert_live_context_snippets(self) -> None:
        """Insert per-source context snippet rows just above the active reply bubble."""
        layout = self._active_layout()
        if layout is None:
            return
        anchor = self._current_ai_label.parentWidget() if self._current_ai_label else None
        idx = layout.indexOf(anchor) if anchor is not None else -1
        if idx < 0:
            # The reply bubble isn't on the active page (e.g. the user switched
            # conversations mid-stream). Skip the live insert; the snippets are
            # persisted on the user message and render on the next sync.
            return
        widget = self._context_snippets_widget(self._current_context_snippets)
        if widget is None:
            return
        layout.insertWidget(idx, widget)

    def update_live_highlight(self, reply_text: str, revealed_count: int, finished: bool):
        """Optionally mirror a read-position without wiring bubble/TTS events here."""
        if not self._conversations:
            return
        last_idx = len(self._conversations) - 1
        if last_idx >= self._stack.count():
            return  # page not built yet (ingest pending)
        page = self._stack.widget(last_idx)
        view = getattr(page, "_last_assistant_view", None)
        if view is None:
            return
        display_text = _truncate_for_display(reply_text, _CHAT_RENDER_CHAR_LIMIT, "chat display")
        if finished:
            # Flash all bold words highlighted, then revert to the normal colour.
            view.setHtml(_assistant_text_to_html(display_text, None))
            QTimer.singleShot(
                _REVERT_DELAY_MS,
                lambda v=view, s=display_text: self._revert_highlight(v, s),
            )
        else:
            view.setHtml(_assistant_text_to_html(display_text, max(0, revealed_count)))
        if last_idx == self._active_idx:
            self._scroll_bottom()

    @staticmethod
    def _revert_highlight(view: _MessageTextView, source: str):
        """Re-render a finished reply with no highlight (bold words back to normal)."""
        try:
            view.setHtml(_assistant_text_to_html(source, 0))
        except RuntimeError:
            pass  # the view (or its window) was destroyed before the timer fired

    # ------------------------------------------------------------------ Events

    def eventFilter(self, obj, event):
        """Handle event filter for chat window."""
        # This object is also installed on QApplication for Ctrl+wheel zoom.
        # PySide can forward model-item action events whose watched value is a
        # QStandardItem rather than a QObject. Passing that value to the base
        # QObject implementation raises recursively from the Qt callback.
        if not isinstance(obj, QObject):
            return False
        if event.type() == QEvent.Type.Wheel and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            # Ctrl+wheel zooms the conversation text instead of scrolling it.
            if isinstance(obj, QWidget) and (obj is self or self.isAncestorOf(obj)):
                delta = event.angleDelta().y()
                if delta:
                    self._change_font_scale(1 if delta > 0 else -1)
                return True
        if event.type() == QEvent.Type.Wheel:
            if self._middle_autoscroll is not None:
                self._stop_middle_autoscroll()
            if self._route_transcript_wheel(obj, event):
                return True
        if event.type() == QEvent.Type.MouseButtonPress and (
            event.button() == Qt.MouseButton.MiddleButton
        ):
            if self._middle_autoscroll is not None:
                self._stop_middle_autoscroll()
                event.accept()
                return True
            if self._begin_middle_autoscroll(obj, event):
                return True
        if event.type() == QEvent.Type.MouseMove and self._middle_autoscroll is not None:
            self._middle_autoscroll["pointer_y"] = event.globalPosition().y()
            anchor_y = float(self._middle_autoscroll["anchor_y"])
            if abs(event.globalPosition().y() - anchor_y) > _CHAT_AUTOSCROLL_DEAD_ZONE:
                self._middle_autoscroll["moved_to_scroll"] = True
            event.accept()
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and (
            event.button() == Qt.MouseButton.MiddleButton
        ):
            # Browser-style autoscroll is already active on press. Releasing
            # after scrolling stops it; a stationary click-release latches it.
            if self._middle_autoscroll is not None:
                self._middle_autoscroll["pointer_y"] = event.globalPosition().y()
                if bool(self._middle_autoscroll.get("moved_to_scroll")):
                    self._stop_middle_autoscroll()
                event.accept()
                return True
        if event.type() == QEvent.Type.MouseButtonPress and self._middle_autoscroll is not None:
            self._stop_middle_autoscroll()
            event.accept()
            return True
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
            and self._middle_autoscroll is not None
        ):
            self._stop_middle_autoscroll()
            event.accept()
            return True
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                modifiers = event.modifiers()
                should_send = (
                    self._enter_sends
                    and not (modifiers & Qt.KeyboardModifier.ShiftModifier)
                ) or (
                    not self._enter_sends
                    and bool(modifiers & Qt.KeyboardModifier.ControlModifier)
                )
                if should_send:
                    self._on_send_clicked()
                    return True
        return super().eventFilter(obj, event)

    def _active_transcript_for_target(self, obj: object) -> QScrollArea | None:
        """Return the active transcript when *obj* is one of its descendants."""
        if not isinstance(obj, QWidget):
            return None
        scroll = self._active_scroll()
        if scroll is None or not (obj is scroll or scroll.isAncestorOf(obj)):
            return None
        scrollbar = scroll.verticalScrollBar()
        if obj is scrollbar or scrollbar.isAncestorOf(obj):
            return None
        return scroll

    def _route_transcript_wheel(self, obj: object, event) -> bool:
        """Scroll the outer conversation when the wheel is over reply content."""
        scroll = self._active_transcript_for_target(obj)
        if scroll is None:
            return False
        pixel_delta = event.pixelDelta().y()
        angle_delta = event.angleDelta().y()
        if not pixel_delta and not angle_delta:
            return False
        delta = pixel_delta
        if not delta:
            delta = round((angle_delta / 120.0) * _CHAT_WHEEL_STEP)
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.value() - delta)
        self._on_transcript_manual_scroll(scroll)
        event.accept()
        return True

    def _begin_middle_autoscroll(self, obj: object, event) -> bool:
        """Start browser-style autoscroll immediately on middle-button press."""
        scroll = self._active_transcript_for_target(obj)
        if scroll is None or scroll.verticalScrollBar().maximum() <= 0:
            return False
        target = scroll.viewport()
        self._middle_autoscroll = {
            "scroll": scroll,
            "anchor_y": event.globalPosition().y(),
            "pointer_y": event.globalPosition().y(),
            "moved_to_scroll": False,
            "target": target,
        }
        # Use the platform-rendered cursor. A text glyph inside a handmade
        # circle varies by font and DPI and does not match native pointer UI.
        target.setCursor(Qt.CursorShape.SizeVerCursor)
        self._middle_autoscroll_timer.start()
        event.accept()
        return True

    def _tick_middle_autoscroll(self) -> None:
        """Scroll continuously according to distance from the click anchor."""
        state = self._middle_autoscroll
        if state is None:
            return
        try:
            scroll = state["scroll"]
            if not isinstance(scroll, QScrollArea):
                self._stop_middle_autoscroll()
                return
            pointer_y = float(state.get("pointer_y", QCursor.pos().y()))
            anchor_y = float(state["anchor_y"])
            distance = pointer_y - anchor_y
            if abs(distance) <= _CHAT_AUTOSCROLL_DEAD_ZONE:
                return
            excess = abs(distance) - _CHAT_AUTOSCROLL_DEAD_ZONE
            speed = min(42, max(1, round((excess / 8.0) ** 1.25)))
            if distance < 0:
                speed = -speed
            bar = scroll.verticalScrollBar()
            bar.setValue(bar.value() + speed)
            self._on_transcript_manual_scroll(scroll)
        except RuntimeError:
            self._stop_middle_autoscroll()

    def _stop_middle_autoscroll(self) -> None:
        """Stop autoscroll and restore the normal pointer."""
        state = self._middle_autoscroll
        self._middle_autoscroll = None
        self._middle_autoscroll_timer.stop()
        if state is not None:
            target = state.get("target")
            if isinstance(target, QWidget):
                try:
                    target.unsetCursor()
                except RuntimeError:
                    pass

    # ------------------------------------------------------------------ Text zoom

    def _install_zoom_shortcuts(self) -> None:
        """Bind Ctrl+±/Ctrl+0 to zoom the chat text (Ctrl+wheel also works)."""
        self._zoom_shortcuts = []
        bindings = (
            ("Ctrl++", lambda: self._change_font_scale(1)),
            ("Ctrl+=", lambda: self._change_font_scale(1)),   # + without Shift
            ("Ctrl+-", lambda: self._change_font_scale(-1)),
            ("Ctrl+0", lambda: self._set_font_scale(1.0)),    # reset to 100%
        )
        for sequence, handler in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)
            self._zoom_shortcuts.append(shortcut)

    def _change_font_scale(self, steps: int) -> None:
        """Zoom the chat text by ``steps`` increments of 10%."""
        self._set_font_scale(self._font_scale + 0.1 * steps)

    def _set_font_scale(self, value: float) -> None:
        """Set the chat text zoom multiplier, apply it, and persist it (debounced)."""
        value = max(0.7, min(round(value, 2), 2.5))
        if abs(value - self._font_scale) < 1e-3:
            return
        self._font_scale = value
        self._apply_font_scale()
        self._font_scale_save_timer.start()

    def _apply_font_scale(self) -> None:
        """Restyle every message bubble and the input box at the current zoom."""
        for view in self.findChildren(_MessageTextView):
            view.set_font_scale(self._font_scale)
        self._apply_input_font_scale()

    def _apply_input_font_scale(self) -> None:
        """Apply the current text zoom to the message composer."""
        if getattr(self, "_input", None) is None:
            return
        pt = max(7, round(10 * self._font_scale))
        self._input.setStyleSheet(
            f"QTextEdit {{ background: {_WHITE_BG_8}; border: 1px solid {_BORDER};"
            f" border-radius: 6px; color: {_TEXT}; padding: 6px 8px; font-size: {pt}pt; }}"
        )

    # ------------------------------------------------------------------ Helpers

    def _center_on_screen(self):
        """Place a first-run or invalid saved window safely on the active screen."""
        fit_window_to_screen(self, preferred_width=_W, preferred_height=_H)

    def _restore_window_state(self) -> None:
        """Restore the last normal geometry once, falling back safely across monitors."""
        state: dict = {}
        if self._window_state_enabled:
            try:
                loaded = json.loads(self._window_state_path.read_text(encoding="utf-8"))
                state = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError, TypeError):
                state = {}
        try:
            rect = QRect(
                int(state.get("x")),
                int(state.get("y")),
                int(state.get("width")),
                int(state.get("height")),
            )
        except (TypeError, ValueError):
            rect = QRect()

        screens = QApplication.screens()
        usable = (
            rect.width() >= self.minimumWidth()
            and rect.height() >= self.minimumHeight()
            and any(screen.availableGeometry().intersects(rect) for screen in screens)
        )
        if usable:
            screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
            if screen is not None:
                bounds = screen.availableGeometry()
                width = min(max(rect.width(), self.minimumWidth()), bounds.width())
                height = min(max(rect.height(), self.minimumHeight()), bounds.height())
                x = min(max(rect.x(), bounds.left()), bounds.right() - width + 1)
                y = min(max(rect.y(), bounds.top()), bounds.bottom() - height + 1)
                self.setGeometry(x, y, width, height)
        else:
            self._center_on_screen()

        self._window_geometry_ready = True
        if bool(state.get("maximized")):
            QTimer.singleShot(0, self.showMaximized)

    def _schedule_window_state_save(self) -> None:
        """Debounce move/resize persistence while the user manipulates the window."""
        if self._window_state_enabled and self._window_geometry_ready and not self.isMinimized():
            self._window_geometry_save_timer.start()

    def _save_window_state(self) -> None:
        """Persist the normal rectangle and maximized state atomically."""
        if not self._window_state_enabled or not self._window_geometry_ready:
            return
        rect = self.normalGeometry() if self.isMaximized() else self.geometry()
        state = {
            "x": rect.x(),
            "y": rect.y(),
            "width": rect.width(),
            "height": rect.height(),
            "maximized": self.isMaximized(),
        }
        temporary = self._window_state_path.with_suffix(self._window_state_path.suffix + ".tmp")
        try:
            self._window_state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.replace(temporary, self._window_state_path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def moveEvent(self, event):  # noqa: N802 - Qt API
        """Remember the user's normal window position without fighting native movement."""
        super().moveEvent(event)
        self._schedule_window_state_save()

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        """Remember natural resize, snapped, restored, and maximized geometry."""
        super().resizeEvent(event)
        self._schedule_window_state_save()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        """Fill in the rest of the history once the window has actually drawn.

        Keyed off the first paint rather than showEvent because a zero-delay timer
        started from showEvent still runs ahead of the initial frame, which would
        put the row building right back in front of the window appearing.
        """
        super().paintEvent(event)
        if self._pending_sidebar_rows:
            self._schedule_sidebar_fill()

    def showEvent(self, event):  # noqa: N802
        """Show without recentering; restored or user-moved geometry remains authoritative."""
        super().showEvent(event)
