"""Safe text annotation helpers for chat and bubble rendering."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

MAX_ANNOTATIONS = 256
MAX_KEYWORD_RULES = 64
MAX_TOOLTIP_CHARS = 240
MAX_STYLE_CHARS = 360
_ALLOWED_TAGS = {"span", "mark", "u", "code", "strong", "b", "em", "i", "s", "small", "sub", "sup"}
_ALLOWED_STYLE_PROPS = {
    "background",
    "background-color",
    "border",
    "border-bottom",
    "border-color",
    "border-radius",
    "border-style",
    "border-width",
    "color",
    "font-family",
    "font-style",
    "font-weight",
    "padding",
    "text-decoration",
    "text-decoration-color",
    "text-decoration-line",
    "text-decoration-style",
}
_UNSAFE_STYLE_RE = re.compile(r"(?i)(?:url\s*\(|expression\s*\(|@import|javascript:|vbscript:|-moz-binding)")


@dataclass(frozen=True)
class TextAnnotation:
    """A sanitized text range that can render as a small HTML-like inline tag."""

    start: int
    end: int
    tag: str = "span"
    style: str = ""
    tooltip: str = ""
    source: str = ""
    id: str = ""
    surface: str = "chat"
    message_id: str = ""
    conversation_id: str = ""
    action: str = ""


@dataclass(frozen=True)
class KeywordRule:
    """A sanitized literal keyword rule that can be applied to live text."""

    match: str
    tag: str = "span"
    style: str = ""
    tooltip: str = ""
    source: str = ""
    id: str = ""
    surface: str = "chat"
    case_sensitive: bool = False
    whole_word: bool = False
    action: str = ""


@dataclass(frozen=True)
class AnnotatedSlice:
    """A contiguous text slice and the annotation that decorates it."""

    text: str
    annotation: TextAnnotation | None = None


def annotation_tooltip_anchor(annotation: TextAnnotation) -> str:
    """Return an opaque internal anchor for one annotation tooltip."""
    if not annotation.tooltip:
        return ""
    payload = "\x00".join((annotation.source, annotation.id, annotation.tooltip))
    token = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"wisp-annotation:{token}"


def normalize_range_annotations(
    raw_annotations: object,
    text: str,
    *,
    surface: str = "chat",
    limit: int = MAX_ANNOTATIONS,
) -> list[TextAnnotation]:
    """Return sanitized range annotations that are safe to render."""
    if raw_annotations is None:
        return []
    items: list[Any]
    if isinstance(raw_annotations, TextAnnotation):
        items = [raw_annotations]
    elif isinstance(raw_annotations, list):
        items = raw_annotations
    else:
        return []

    text_len = len(text or "")
    out: list[TextAnnotation] = []
    for raw in items:
        if len(out) >= max(0, limit):
            break
        annotation = _coerce_annotation(raw, text_len=text_len, fallback_surface=surface)
        if annotation is not None:
            out.append(annotation)
    return out


def normalize_keyword_rules(
    raw_rules: object,
    *,
    surface: str = "chat",
    limit: int = MAX_KEYWORD_RULES,
) -> list[KeywordRule]:
    """Return sanitized literal keyword rules."""
    if raw_rules is None:
        return []
    items: list[Any]
    if isinstance(raw_rules, KeywordRule):
        items = [raw_rules]
    elif isinstance(raw_rules, list):
        items = raw_rules
    else:
        return []

    out: list[KeywordRule] = []
    for raw in items:
        if len(out) >= max(0, limit):
            break
        rule = _coerce_keyword_rule(raw, fallback_surface=surface)
        if rule is not None:
            out.append(rule)
    return out


def annotations_from_keyword_rules(
    text: str,
    raw_rules: object,
    *,
    surface: str = "chat",
    limit: int = MAX_ANNOTATIONS,
) -> list[TextAnnotation]:
    """Expand literal keyword rules into range annotations for *text*."""
    source_text = str(text or "")
    annotations: list[TextAnnotation] = []
    for rule in normalize_keyword_rules(raw_rules, surface=surface):
        haystack = source_text if rule.case_sensitive else source_text.lower()
        needle = rule.match if rule.case_sensitive else rule.match.lower()
        start = 0
        while len(annotations) < max(0, limit):
            idx = haystack.find(needle, start)
            if idx < 0:
                break
            end = idx + len(rule.match)
            start = max(end, idx + 1)
            if rule.whole_word and not _has_word_boundaries(source_text, idx, end):
                continue
            annotations.append(
                TextAnnotation(
                    start=idx,
                    end=end,
                    tag=rule.tag,
                    style=rule.style,
                    tooltip=rule.tooltip,
                    source=rule.source,
                    id=rule.id,
                    surface=rule.surface,
                    action=rule.action,
                )
            )
        if len(annotations) >= max(0, limit):
            break
    return annotations


def annotations_for_subrange(
    annotations: list[TextAnnotation],
    start: int,
    end: int,
) -> list[TextAnnotation]:
    """Return annotations clipped and re-based to a text subrange."""
    if end <= start:
        return []
    clipped: list[TextAnnotation] = []
    for annotation in annotations:
        left = max(annotation.start, start)
        right = min(annotation.end, end)
        if right <= left:
            continue
        clipped.append(
            TextAnnotation(
                start=left - start,
                end=right - start,
                tag=annotation.tag,
                style=annotation.style,
                tooltip=annotation.tooltip,
                source=annotation.source,
                id=annotation.id,
                surface=annotation.surface,
                message_id=annotation.message_id,
                conversation_id=annotation.conversation_id,
                action=annotation.action,
            )
        )
    return clipped


def compose_annotated_slices(text: str, annotations: list[TextAnnotation]) -> list[AnnotatedSlice]:
    """Split text into non-overlapping slices, applying deterministic annotation precedence."""
    source_text = str(text or "")
    if not source_text:
        return []
    if not annotations:
        return [AnnotatedSlice(source_text)]

    ordered = sorted(annotations, key=lambda item: (item.start, -(item.end - item.start), item.source, item.id))
    slices: list[AnnotatedSlice] = []
    cursor = 0
    for annotation in ordered:
        start = max(0, min(len(source_text), annotation.start))
        end = max(0, min(len(source_text), annotation.end))
        if end <= start or start < cursor:
            continue
        if start > cursor:
            slices.append(AnnotatedSlice(source_text[cursor:start]))
        slices.append(AnnotatedSlice(source_text[start:end], annotation))
        cursor = end
    if cursor < len(source_text):
        slices.append(AnnotatedSlice(source_text[cursor:]))
    return slices


def _coerce_annotation(raw: Any, *, text_len: int, fallback_surface: str) -> TextAnnotation | None:
    if isinstance(raw, TextAnnotation):
        data = raw.__dict__
    elif isinstance(raw, dict):
        data = raw
    else:
        return None

    start = _coerce_int(data.get("start"))
    end = _coerce_int(data.get("end"))
    if start is None or end is None:
        return None
    start = max(0, min(text_len, start))
    end = max(0, min(text_len, end))
    if end <= start:
        return None

    return TextAnnotation(
        start=start,
        end=end,
        tag=_safe_tag(data.get("tag")),
        style=_safe_style(data.get("style")),
        tooltip=_safe_text(data.get("tooltip"), MAX_TOOLTIP_CHARS),
        source=_safe_text(data.get("source"), 80),
        id=_safe_text(data.get("id"), 80),
        surface=_safe_text(data.get("surface"), 24) or fallback_surface,
        message_id=_safe_text(data.get("message_id"), 120),
        conversation_id=_safe_text(data.get("conversation_id"), 120),
        action=_safe_text(data.get("action"), 80),
    )


def _coerce_keyword_rule(raw: Any, *, fallback_surface: str) -> KeywordRule | None:
    if isinstance(raw, KeywordRule):
        data = raw.__dict__
    elif isinstance(raw, dict):
        data = raw
    else:
        return None

    match = _safe_text(data.get("match"), 120)
    if not match:
        return None
    return KeywordRule(
        match=match,
        tag=_safe_tag(data.get("tag")),
        style=_safe_style(data.get("style")),
        tooltip=_safe_text(data.get("tooltip"), MAX_TOOLTIP_CHARS),
        source=_safe_text(data.get("source"), 80),
        id=_safe_text(data.get("id"), 80),
        surface=_safe_text(data.get("surface"), 24) or fallback_surface,
        case_sensitive=bool(data.get("case_sensitive")),
        whole_word=bool(data.get("whole_word")),
        action=_safe_text(data.get("action"), 80),
    )


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _safe_tag(value: object) -> str:
    tag = str(value or "span").replace("\x00", "").strip().lower()
    return tag if tag in _ALLOWED_TAGS else "span"


def _safe_style(value: object) -> str:
    raw = _safe_text(value, MAX_STYLE_CHARS)
    if not raw:
        return ""
    declarations: list[str] = []
    for part in raw.split(";"):
        if ":" not in part:
            continue
        prop, val = part.split(":", 1)
        prop = prop.strip().lower()
        val = val.strip()
        if not prop or not val or prop not in _ALLOWED_STYLE_PROPS:
            continue
        if any(ch in val for ch in {"<", ">", '"', "'", "\\", "`", "{", "}"}):
            continue
        if _UNSAFE_STYLE_RE.search(val):
            continue
        declarations.append(f"{prop}:{val}")
    return "; ".join(declarations)


def _safe_text(value: object, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[: max(0, limit)]


def _has_word_boundaries(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not _is_word_char(before) and not _is_word_char(after)


def _is_word_char(value: str) -> bool:
    return bool(value) and (value.isalnum() or value == "_")
