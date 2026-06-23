"""Sensitive text redaction with optional user-visible reports."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RedactionPattern:
    """One sensitive-data pattern and its replacement label."""

    category: str
    pattern: re.Pattern[str]
    replacement: str


REDACTION_PATTERNS: tuple[RedactionPattern, ...] = (
    RedactionPattern("card_number", re.compile(r"\b(?:\d[ \-]?){13,19}\b"), "[CARD_NUMBER]"),
    RedactionPattern("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
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
    RedactionPattern("api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}\b"), "[API_KEY]"),
    RedactionPattern("api_key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b"), "[API_KEY]"),
    RedactionPattern(
        "api_key",
        re.compile(r"\b(?:gh[opusr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{20,})\b"),
        "[API_KEY]",
    ),
    RedactionPattern("api_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "[API_KEY]"),
    RedactionPattern("api_key", re.compile(r"\bxox(?:[abprs]|o)-[A-Za-z0-9-]{20,}\b"), "[API_KEY]"),
    RedactionPattern("api_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"), "[API_KEY]"),
    RedactionPattern("api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), "[API_KEY]"),
    RedactionPattern("api_key", re.compile(r"\bnpm_[A-Za-z0-9]{36,}\b"), "[API_KEY]"),
    RedactionPattern(
        "bearer_token",
        re.compile(
            r"\b(?:mfa\.[A-Za-z0-9_-]{20,}|"
            r"[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,})\b"
        ),
        "[BEARER_TOKEN]",
    ),
    RedactionPattern(
        "bearer_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "[BEARER_TOKEN]",
    ),
    RedactionPattern(
        "bearer_token",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.~+/=]{20,}"),
        "[BEARER_TOKEN]",
    ),
    RedactionPattern(
        "api_key",
        re.compile(
            r"(?i)(?:token|api[_\-]?key|access[_\-]?key|secret[_\-]?key|client[_\-]?secret)"
            r"[\s]*[:=][\s]*(?:"
            r"[\'\"][A-Za-z0-9\-_./+]{20,}[\'\"]"
            r"|"
            r"[A-Za-z0-9\-_./+]{32,}(?=$|[\s,#;\]}])"
            r")"
        ),
        "[API_KEY]",
    ),
    RedactionPattern(
        "credential",
        re.compile(r"(?i)(?:password|passwd|pwd|secret)\s*[:=]\s*\S+"),
        "[REDACTED_CREDENTIAL]",
    ),
)


def safe_preview(value: str, *, prefix: int = 3, suffix: int = 4) -> str:
    """Return a non-sensitive preview of a redacted value."""
    flat = " ".join(str(value or "").split())
    if not flat:
        return ""
    if len(flat) <= prefix + suffix + 3:
        return flat[:1] + "..." + flat[-1:]
    return f"{flat[:prefix]}...{flat[-suffix:]}"


def redact_text(text: str) -> str:
    """Replace sensitive-looking values with stable tags."""
    redacted = str(text or "")
    for item in REDACTION_PATTERNS:
        redacted = item.pattern.sub(item.replacement, redacted)
    return redacted


def redact_with_report(text: str, *, source: str = "") -> tuple[str, dict[str, Any]]:
    """Redact text and return a report of detected sensitive categories."""
    hits: list[dict[str, str]] = []
    redacted = str(text or "")
    for item in REDACTION_PATTERNS:
        matches = list(item.pattern.finditer(redacted))
        if not matches:
            continue
        for match in matches:
            hits.append(
                {
                    "source": source,
                    "category": item.category,
                    "replacement": item.replacement,
                    "preview": safe_preview(match.group(0)),
                }
            )
        redacted = item.pattern.sub(item.replacement, redacted)
    categories: dict[str, int] = {}
    for hit in hits:
        categories[hit["category"]] = categories.get(hit["category"], 0) + 1
    return redacted, {"source": source, "count": len(hits), "items": hits, "categories": categories}


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
