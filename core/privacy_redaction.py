"""Sensitive-text detection and deterministic redaction.

The regex layer is intentionally dependency-free and always available.  It is
used both for local previews and as the final invariant protecting cloud-bound
text.  Context-aware/model detections are merged by :mod:`core.privacy_gateway`.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

Validator = Callable[[str], bool]


@dataclass(frozen=True)
class RedactionPattern:
    """One sensitive-data pattern and its replacement label."""

    category: str
    pattern: re.Pattern[str]
    replacement: str
    validator: Validator | None = None


@dataclass(frozen=True)
class SensitiveEntity:
    """One non-overlapping sensitive span in source text."""

    category: str
    start: int
    end: int
    original: str
    replacement: str
    source: str = ""


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _luhn_valid(value: str) -> bool:
    digits = _digits(value)
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        number = int(char)
        if index % 2 == parity:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def _iban_valid(value: str) -> bool:
    compact = re.sub(r"\s", "", value).upper()
    if not 15 <= len(compact) <= 34 or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    expanded = "".join(str(ord(char) - 55) if char.isalpha() else char for char in rearranged)
    remainder = 0
    for char in expanded:
        remainder = (remainder * 10 + int(char)) % 97
    return remainder == 1


def _phone_valid(value: str) -> bool:
    digits = _digits(value)
    return 10 <= len(digits) <= 15 and len(set(digits)) > 2


REDACTION_PATTERNS: tuple[RedactionPattern, ...] = (
    RedactionPattern(
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----.*?"
            r"-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[PRIVATE_KEY]",
    ),
    RedactionPattern(
        "url_credential",
        re.compile(
            r"(?i)([?&;](?:access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token|"
            r"token|api[_-]?key|key|signature|sig|auth|code)=)[^&#\s]+"
        ),
        r"\1[URL_CREDENTIAL]",
    ),
    RedactionPattern("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    RedactionPattern(
        "iban",
        re.compile(r"(?i)\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b"),
        "[IBAN]",
        _iban_valid,
    ),
    RedactionPattern(
        "card_number",
        re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
        "[CARD_NUMBER]",
        _luhn_valid,
    ),
    RedactionPattern(
        "email",
        re.compile(r"(?i)(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+\b"),
        "[EMAIL]",
    ),
    RedactionPattern(
        "url",
        re.compile(r"(?i)\bhttps?://[^\s<>\[\]{}\"']+"),
        "[URL]",
    ),
    RedactionPattern(
        "phone",
        re.compile(r"(?<![\w])(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]?)\d{3}[ .-]?\d{4}(?!\w)"),
        "[PHONE]",
        _phone_valid,
    ),
    RedactionPattern("api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"), "[API_KEY]"),
    RedactionPattern("api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), "[API_KEY]"),
    RedactionPattern(
        "api_key",
        re.compile(r"\b(?:gh[opusr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{20,})\b"),
        "[API_KEY]",
    ),
    RedactionPattern("api_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "[API_KEY]"),
    RedactionPattern("api_key", re.compile(r"\bxox(?:[abprs]|o)-[A-Za-z0-9-]{20,}\b"), "[API_KEY]"),
    RedactionPattern("api_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"), "[API_KEY]"),
    RedactionPattern("api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "[API_KEY]"),
    RedactionPattern("api_key", re.compile(r"\bnpm_[A-Za-z0-9]{36,}\b"), "[API_KEY]"),
    RedactionPattern(
        "bearer_token",
        re.compile(r"\b(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,})\b"),
        "[BEARER_TOKEN]",
    ),
    RedactionPattern(
        "bearer_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "[BEARER_TOKEN]",
    ),
    RedactionPattern(
        "bearer_token",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9-_.~+/=]{20,}"),
        "[BEARER_TOKEN]",
    ),
    RedactionPattern(
        "account_number",
        re.compile(
            r"(?i)\b(?:account|acct|routing|membership|customer)[ _-]?(?:number|no|id)?"
            r"\s*[:=#]\s*[A-Z0-9][A-Z0-9 -]{5,30}\b"
        ),
        "[ACCOUNT_NUMBER]",
    ),
    RedactionPattern(
        "passport",
        re.compile(
            r"(?i)\bpassport(?:\s+(?:number|no\.?|id))?\s*[:=#]\s*[A-Z0-9][A-Z0-9 -]{5,14}\b"
        ),
        "[PASSPORT]",
    ),
    RedactionPattern(
        "drivers_license",
        re.compile(
            r"(?i)\b(?:driver(?:'s|s)?\s+licen[cs]e|driving\s+licen[cs]e)"
            r"(?:\s+(?:number|no\.?|id))?\s*[:=#]\s*[A-Z0-9][A-Z0-9 -]{4,19}\b"
        ),
        "[DRIVERS_LICENSE]",
    ),
    RedactionPattern(
        "api_key",
        re.compile(
            r"(?i)(?:token|api[_-]?key|access[_-]?key|secret[_-]?key|client[_-]?secret)"
            r"\s*[:=]\s*(?:['\"][A-Za-z0-9_./+\-]{12,}['\"]|[A-Za-z0-9_./+\-]{20,}(?=$|[\s,#;\]}]))"
        ),
        "[API_KEY]",
    ),
    RedactionPattern(
        "credential",
        re.compile(r"(?i)(?:password|passwd|pwd|secret|passphrase)\s*[:=]\s*\S+"),
        "[REDACTED_CREDENTIAL]",
    ),
)


def _custom_patterns() -> tuple[RedactionPattern, ...]:
    """Return validated user patterns from config or the environment.

    ``PRIVACY_CUSTOM_PATTERNS`` is a JSON list of objects with ``pattern`` and
    optional ``name`` fields. Invalid or excessively large expressions are
    ignored so a stale setting cannot break outbound requests.
    """
    raw: Any = os.environ.get("PRIVACY_CUSTOM_PATTERNS", "")
    try:
        import config

        raw = getattr(config, "PRIVACY_CUSTOM_PATTERNS", raw)
    except Exception:
        pass
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return ()
    if not isinstance(raw, list):
        return ()
    patterns: list[RedactionPattern] = []
    for item in raw[:50]:
        if not isinstance(item, dict) or item.get("enabled", True) is False:
            continue
        expression = str(item.get("pattern") or "")
        if not expression or len(expression) > 512:
            continue
        label = re.sub(r"[^A-Z]", "", str(item.get("label") or item.get("name") or "CUSTOM").upper()) or "CUSTOM"
        try:
            compiled = re.compile(expression, 0 if item.get("case_sensitive", True) else re.IGNORECASE)
        except re.error:
            continue
        patterns.append(RedactionPattern("custom", compiled, f"[{label}]"))
    return tuple(patterns)


def iter_patterns(*, include_custom: bool = True) -> Iterable[RedactionPattern]:
    yield from REDACTION_PATTERNS
    if include_custom:
        yield from _custom_patterns()


def _replacement_for(pattern: RedactionPattern, match: re.Match[str]) -> str:
    try:
        return match.expand(pattern.replacement)
    except (IndexError, re.error):
        return pattern.replacement


def detect_sensitive_entities(
    text: str,
    *,
    source: str = "",
    include_custom: bool = True,
) -> list[SensitiveEntity]:
    """Return deterministic, non-overlapping sensitive spans."""
    value = str(text or "")
    candidates: list[tuple[int, SensitiveEntity]] = []
    category_priority = {
        "custom": 0,
        "private_key": 1,
        "url_credential": 2,
        "ssn": 3,
        "iban": 4,
        "card_number": 5,
        "api_key": 6,
        "bearer_token": 7,
        "credential": 8,
        "account_number": 9,
        "passport": 10,
        "drivers_license": 11,
        "email": 12,
        "url": 13,
        "phone": 14,
    }
    for order, item in enumerate(iter_patterns(include_custom=include_custom)):
        priority = category_priority.get(item.category, 20) * 1000 + order
        for match in item.pattern.finditer(value):
            original = match.group(0)
            if item.validator is not None and not item.validator(original):
                continue
            candidates.append(
                (
                    priority,
                    SensitiveEntity(
                        category=item.category,
                        start=match.start(),
                        end=match.end(),
                        original=original,
                        replacement=_replacement_for(item, match),
                        source=source,
                    ),
                )
            )
    # Pattern order is also specificity order. Prefer a known secret inside a
    # broader URL/assignment match, then discard any lower-priority overlap.
    candidates.sort(key=lambda pair: (pair[0], pair[1].start, -(pair[1].end - pair[1].start)))
    accepted: list[SensitiveEntity] = []
    for _priority, entity in candidates:
        if any(entity.start < prior.end and entity.end > prior.start for prior in accepted):
            continue
        accepted.append(entity)
    return sorted(accepted, key=lambda item: item.start)


def safe_preview(value: str, *, prefix: int = 3, suffix: int = 4) -> str:
    """Return a compact non-sensitive preview of a detected value."""
    flat = " ".join(str(value or "").split())
    if not flat:
        return ""
    if "@" in flat:
        local, _, domain = flat.partition("@")
        return f"{local[:1]}...@...{domain[-3:]}" if domain else f"{local[:1]}..."
    if len(flat) <= prefix + suffix + 3:
        return flat[:1] + "..." + flat[-1:]
    return f"{flat[:prefix]}...{flat[-suffix:]}"


def _replace_entities(text: str, entities: Iterable[SensitiveEntity]) -> str:
    redacted = str(text or "")
    for entity in sorted(entities, key=lambda item: item.start, reverse=True):
        redacted = redacted[: entity.start] + entity.replacement + redacted[entity.end :]
    return redacted


def redact_text(text: str, *, exclude_categories: Iterable[str] = ()) -> str:
    """Replace sensitive-looking values with deterministic category tags."""
    value = str(text or "")
    excluded = set(exclude_categories)
    entities = [
        entity
        for entity in detect_sensitive_entities(value)
        if entity.category not in excluded
    ]
    return _replace_entities(value, entities)


def redact_with_report(text: str, *, source: str = "") -> tuple[str, dict[str, Any]]:
    """Redact text and return safe metadata about detected categories."""
    value = str(text or "")
    entities = detect_sensitive_entities(value, source=source)
    hits = [
        {
            "source": source,
            "category": entity.category,
            "replacement": entity.replacement,
            "preview": safe_preview(entity.original),
        }
        for entity in entities
    ]
    categories: dict[str, int] = {}
    for hit in hits:
        categories[hit["category"]] = categories.get(hit["category"], 0) + 1
    return _replace_entities(value, entities), {
        "source": source,
        "count": len(hits),
        "items": hits,
        "categories": categories,
    }


def merge_reports(*reports: dict[str, Any] | None) -> dict[str, Any]:
    """Combine redaction reports from multiple context sources."""
    items: list[dict[str, str]] = []
    sources: dict[str, int] = {}
    categories: dict[str, int] = {}
    for report in reports:
        if not isinstance(report, dict):
            continue
        for raw in report.get("items") or []:
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("source") or report.get("source") or "context")
            category = str(raw.get("category") or "sensitive")
            item = {
                "source": source,
                "category": category,
                "replacement": str(raw.get("replacement") or "[REDACTED]"),
                "preview": str(raw.get("preview") or ""),
            }
            items.append(item)
            sources[source] = sources.get(source, 0) + 1
            categories[category] = categories.get(category, 0) + 1
    return {"count": len(items), "items": items, "sources": sources, "categories": categories}
