"""
core/query_pipeline.py — Pure context-assembly logic for a single query.

Extracted from main.App so the precedence rules (buffered text, drag-dropped
items, clipboard, selected text, ambient snapshot, active document) can be unit
tested without Qt, hotkeys, or any I/O. The only side effect lives behind the
injectable ``read_document_file`` callable so tests can supply a fake.

GenerationCounter is the thread-safe successor to the ad-hoc _gen_id/_gen_id_lock
pair: each new query bumps the counter, and stale worker threads check
``is_current()`` before touching shared UI state.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

# Captured desktop context is supporting material, not the user's prompt.  Keep
# one bad clipboard/drop/document from turning into a model request measured in
# tens of megabytes.  Individual inputs are clipped before privacy scanning and
# the assembled context is clipped again so several individually-safe sources
# cannot exceed the per-request ceiling together.
MAX_CAPTURED_CONTEXT_CHARS = 60_000
_CONTEXT_TRUNCATION_MARKER = "\n[captured context truncated at safety limit]"
_MAX_IMAGE_BASE64_CHARS = 16_000_000  # about 12 MiB of encoded image bytes


def _clip_captured_text(text: object, limit: int = MAX_CAPTURED_CONTEXT_CHARS) -> str:
    """Bound one captured text value before redaction or prompt assembly."""
    value = str(text or "")
    if limit <= 0 or len(value) <= limit:
        return value
    keep = max(0, limit - len(_CONTEXT_TRUNCATION_MARKER))
    return value[:keep].rstrip() + _CONTEXT_TRUNCATION_MARKER


class GenerationCounter:
    """Monotonic, thread-safe generation id used to cancel superseded queries."""

    def __init__(self) -> None:
        """Initialize the generation counter instance."""
        self._value = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        """Handle next for generation counter."""
        with self._lock:
            self._value += 1
            return self._value

    @property
    def current(self) -> int:
        """Handle current for generation counter."""
        with self._lock:
            return self._value

    def is_current(self, gen_id: int) -> bool:
        """Return whether current is true."""
        with self._lock:
            return gen_id == self._value


@dataclass
class ContextInputs:
    """Everything build_context needs, with all I/O already performed by the caller."""

    intent_prompt: str
    selected: str | None = None
    screenshot_b64: str | None = None
    ambient_text: str = ""                              # pre-formatted ambient snapshot, or ""
    buffered_items: list[str] = field(default_factory=list)
    drop_items: list[tuple] = field(default_factory=list)  # (name, content, type)
    clipboard_text: str | None = None                  # already read when caller opted in
    active_document_text: str = ""                      # already read + filtered, or ""
    active_document_label: str = ""                     # app/window/file label for active document text
    priority_context: str = ""                          # e.g. "Browser/Web" or "Active document"
    trust_privacy_mode: bool = True
    defer_privacy_redaction: bool = False              # final gateway owns scrub/review


@dataclass
class BuiltContext:
    """Model built context."""
    user_message: str
    ambient_ctx: str
    screenshot_b64: str | None
    privacy_report: dict = field(default_factory=dict)


def _context_sources(ambient_text: str, all_contexts: list[str], active_document_text: str) -> set[str]:
    """Handle context sources for query pipeline."""
    sources: set[str] = set()
    if "[Browser/Web]" in (ambient_text or ""):
        sources.add("Browser/Web")
    elif ambient_text:
        sources.add("Ambient context")
    if all_contexts:
        sources.add("User-provided context")
    if active_document_text:
        sources.add("Active document")
    return sources


def _context_priority_note(priority_context: str, sources: set[str]) -> str:
    """Handle context priority note for query pipeline."""
    priority = (priority_context or "").strip()
    if priority not in sources or len(sources) < 2:
        return ""
    return (
        f"[Context priority]\nPrioritize {priority} because it was the active "
        "or last-used context when this request was captured. Use the other "
        "context as supporting context unless the user asks otherwise."
    )


def _redact_if_enabled(text: str | None, enabled: bool) -> str:
    """Apply the shared sensitive-data redactor when privacy mode is enabled."""
    if not text:
        return ""
    if not enabled:
        return str(text)
    from core.context_fetcher import _redact

    return _redact(str(text))


def _redact_with_report_if_enabled(
    text: str | None,
    enabled: bool,
    source: str,
    *,
    defer: bool = False,
) -> tuple[str, dict]:
    """Apply privacy redaction and keep a detected/censored report."""
    if not text:
        return "", {"source": source, "count": 0, "items": [], "categories": {}}
    if not enabled:
        return str(text), {"source": source, "count": 0, "items": [], "categories": {}}
    from core.privacy_redaction import redact_with_report

    redacted, report = redact_with_report(str(text), source=source)
    return (str(text) if defer else redacted), report


def build_context(
    inp: ContextInputs,
    *,
    read_document_file: Callable[[str], str] | None = None,
) -> BuiltContext:
    """Assemble the final user message, ambient context block, and vision input.

    Precedence mirrors the original App._query_and_speak logic:
      1. buffered (Alt+Q) text
      2. dropped items — first image becomes the vision input when none exists;
         document paths are read and labelled; anything else is appended verbatim
      3. clipboard text (only when the caller opted in, i.e. clipboard_text set)
      4. selected text
    The active document is appended to ambient context whenever the caller
    provided it — including alongside a screenshot, which shows pixels but not
    the document text the user explicitly enabled.
    """
    if read_document_file is None:
        from core.llm_clients.client import read_document_file as _rdf
        read_document_file = _rdf

    screenshot_b64 = inp.screenshot_b64
    privacy_enabled = bool(inp.trust_privacy_mode)
    defer_privacy = bool(inp.defer_privacy_redaction)
    reports: list[dict] = []
    context_blocks: list[str] = []
    for item in inp.buffered_items:
        redacted, report = _redact_with_report_if_enabled(
            _clip_captured_text(item), privacy_enabled, "buffered_context", defer=defer_privacy
        )
        if redacted:
            context_blocks.append(f"[Buffered context]\n{redacted}")
        reports.append(report)

    for name, content, item_type in inp.drop_items:
        label = str(name or "Dropped context").strip() or "Dropped context"
        if item_type == "image" and screenshot_b64 is None:
            encoded = str(content or "")
            if len(encoded) <= _MAX_IMAGE_BASE64_CHARS:
                screenshot_b64 = encoded  # first dropped image becomes the vision input
            else:
                context_blocks.append(f"[Image omitted: {label} exceeds the capture safety limit]")
        elif item_type == "image":
            # The request contract has one vision-image slot.  Base64 for later
            # images is not useful as text and can be enormous.
            context_blocks.append(f"[Additional image omitted from text context: {label}]")
        elif item_type == "document_path":
            doc_text = read_document_file(content)
            if doc_text:
                redacted, report = _redact_with_report_if_enabled(
                    _clip_captured_text(doc_text),
                    privacy_enabled,
                    f"document:{name}",
                    defer=defer_privacy,
                )
                if redacted:
                    context_blocks.append(
                        f"--- BEGIN DOCUMENT: {label} ---\n"
                        f"{redacted}\n"
                        f"--- END DOCUMENT: {label} ---"
                    )
                reports.append(report)
        else:
            redacted, report = _redact_with_report_if_enabled(
                _clip_captured_text(content),
                privacy_enabled,
                f"dropped:{name}",
                defer=defer_privacy,
            )
            if redacted:
                context_blocks.append(
                    f"--- BEGIN DROPPED CONTEXT: {label} ---\n"
                    f"{redacted}\n"
                    f"--- END DROPPED CONTEXT: {label} ---"
                )
            reports.append(report)

    if inp.clipboard_text:
        redacted, report = _redact_with_report_if_enabled(
            _clip_captured_text(inp.clipboard_text),
            privacy_enabled,
            "clipboard",
            defer=defer_privacy,
        )
        if redacted:
            context_blocks.append(f"[Clipboard]\n{redacted}")
        reports.append(report)

    selected, selected_report = _redact_with_report_if_enabled(
        _clip_captured_text(inp.selected), privacy_enabled, "selection", defer=defer_privacy
    )
    selected_block = f"[Selection]\n{selected}" if selected else ""
    ambient_text, ambient_report = _redact_with_report_if_enabled(
        _clip_captured_text(inp.ambient_text), privacy_enabled, "ambient", defer=defer_privacy
    )
    active_document_text, document_report = _redact_with_report_if_enabled(
        _clip_captured_text(inp.active_document_text),
        privacy_enabled,
        "active_document",
        defer=defer_privacy,
    )
    reports.extend([selected_report, ambient_report, document_report])

    all_contexts = context_blocks + ([selected_block] if selected_block else [])
    sources = _context_sources(ambient_text, all_contexts, active_document_text)

    ctx_block = "\n\n".join(all_contexts)
    active_doc_block = ""
    if active_document_text:
        label = " ".join(str(inp.active_document_label or "").split()).strip()
        if label:
            active_doc_block = (
                f"--- BEGIN ACTIVE DOCUMENT: {label} ---\n"
                f"{active_document_text}\n"
                f"--- END ACTIVE DOCUMENT: {label} ---"
            )
        else:
            active_doc_block = f"[Active document]\n{active_document_text}"
    priority_note = _context_priority_note(inp.priority_context, sources)

    # Assemble in priority order with explicit source headers. The LLM client
    # already separates this whole block from the system prompt, so keep the
    # inside readable instead of nesting generic dividers.
    ambient_ctx = "\n\n".join(
        part
        for part in (priority_note, ambient_text, ctx_block, active_doc_block)
        if part
    )
    ambient_ctx = _clip_captured_text(ambient_ctx)

    user_message, prompt_report = _redact_with_report_if_enabled(
        inp.intent_prompt, privacy_enabled, "prompt", defer=defer_privacy
    )
    reports.append(prompt_report)
    from core.privacy_redaction import merge_reports

    return BuiltContext(
        user_message=user_message,
        ambient_ctx=ambient_ctx,
        screenshot_b64=screenshot_b64,
        privacy_report=merge_reports(*reports),
    )
