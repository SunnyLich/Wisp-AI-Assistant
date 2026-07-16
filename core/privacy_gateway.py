"""Fail-closed privacy gateway for cloud-bound text."""
from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from core.privacy_redaction import SensitiveEntity, detect_sensitive_entities, safe_preview


class PrivacyFilterError(RuntimeError):
    """A model request was blocked by the privacy gateway."""


class PrivacyReviewCanceled(PrivacyFilterError):
    """The user canceled the pre-send privacy review."""


class PrivacyLeakDetected(PrivacyFilterError):
    """Sensitive text remained after scrubbing."""


_PREFIXES = {
    "person": "PERSON",
    "email": "EMAIL",
    "phone": "PHONE",
    "url": "URL",
    "address": "ADDR",
    "date": "DATE",
    "account_number": "ACCT",
    "passport": "PASSPORT",
    "drivers_license": "LICENSE",
    "card_number": "CARD",
    "ssn": "SSN",
    "iban": "IBAN",
    "api_key": "SECRET",
    "bearer_token": "SECRET",
    "credential": "SECRET",
    "private_key": "SECRET",
    "url_credential": "SECRET",
    "secret": "SECRET",
    "custom": "CUSTOM",
}


def _merge_entities(
    builtin: Iterable[SensitiveEntity],
    model: Iterable[SensitiveEntity],
) -> list[SensitiveEntity]:
    """Merge detectors once per span, preferring the model on overlap."""
    accepted: list[SensitiveEntity] = []
    for entity in (*model, *builtin):
        if any(entity.start < prior.end and entity.end > prior.start for prior in accepted):
            continue
        accepted.append(entity)
    return sorted(accepted, key=lambda item: item.start)


def _detect_entities(
    text: str,
    *,
    source: str = "",
    ai_enabled: bool = False,
) -> list[SensitiveEntity]:
    """Run the invariant built-in detector and optionally merge local AI hits."""
    builtin = detect_sensitive_entities(text, source=source)
    if not ai_enabled or not text:
        return builtin

    from core.privacy_model import detect_with_model

    model = detect_with_model(text, source=source)
    return _merge_entities(builtin, model)


@dataclass
class PrivacySession:
    """Stable placeholder map for one conversation/session."""

    session_id: str
    original_to_placeholder: dict[str, str] = field(default_factory=dict)
    placeholder_to_original: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def placeholder_for(self, original: str, category: str, suggested: str = "") -> str:
        with self.lock:
            existing = self.original_to_placeholder.get(original)
            if existing:
                return existing
            prefix = _PREFIXES.get(category, "PRIVATE")
            if category == "custom" and suggested:
                custom_prefix = "".join(char for char in suggested.upper() if "A" <= char <= "Z")
                if custom_prefix:
                    prefix = custom_prefix
            number = self.counters.get(prefix, 0) + 1
            self.counters[prefix] = number
            placeholder = f"[{prefix}_{number}]"
            self.original_to_placeholder[original] = placeholder
            self.placeholder_to_original[placeholder] = original
            return placeholder

    def restore(self, text: str) -> str:
        value = str(text or "")
        with self.lock:
            for placeholder in sorted(self.placeholder_to_original, key=len, reverse=True):
                value = value.replace(placeholder, self.placeholder_to_original[placeholder])
        return value

    def scrub_fields(
        self,
        fields: dict[str, str],
        *,
        ai_enabled: bool = False,
        review: Callable[[dict[str, Any]], Any] | None = None,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        mode = "advanced" if ai_enabled else "builtin"
        detected: dict[str, list[SensitiveEntity]] = {}
        items: list[dict[str, str]] = []
        for source, raw in fields.items():
            text = str(raw or "")
            entities = _detect_entities(text, source=source, ai_enabled=ai_enabled)
            detected[source] = entities
            for entity in entities:
                placeholder = self.placeholder_for(entity.original, entity.category, entity.replacement)
                items.append(
                    {
                        "source": source,
                        "category": entity.category,
                        "replacement": placeholder,
                        "preview": safe_preview(entity.original),
                    }
                )

        scrubbed: dict[str, str] = {}
        for source, raw in fields.items():
            value = str(raw or "")
            for entity in sorted(detected[source], key=lambda item: item.start, reverse=True):
                placeholder = self.placeholder_for(entity.original, entity.category, entity.replacement)
                value = value[: entity.start] + placeholder + value[entity.end :]
            scrubbed[source] = value

        categories: dict[str, int] = {}
        sources: dict[str, int] = {}
        for item in items:
            categories[item["category"]] = categories.get(item["category"], 0) + 1
            sources[item["source"]] = sources.get(item["source"], 0) + 1
        report: dict[str, Any] = {
            "count": len(items),
            "items": items,
            "categories": categories,
            "sources": sources,
            "ai_enabled": bool(ai_enabled),
            "privacy_mode": mode,
            "session_id": self.session_id,
        }

        if items and review is not None:
            preview = "\n\n".join(
                f"[{source}]\n{text}" for source, text in scrubbed.items() if text
            )
            payload = dict(report)
            payload["scrubbed_preview"] = preview
            result = review(payload)
            if isinstance(result, dict):
                decision = str(result.get("decision") or "").strip().lower()
            elif isinstance(result, str):
                decision = result.strip().lower()
            else:
                decision = "redacted" if result is True else "cancel"
            if decision not in {"redacted", "full"}:
                raise PrivacyReviewCanceled("Privacy review canceled. Nothing was sent to the model.")
            report["reviewed"] = True
            report["decision"] = decision
            report["redacted"] = decision == "redacted"
            if decision == "full":
                return {name: str(value or "") for name, value in fields.items()}, report

        leaked: dict[str, int] = {}
        for value in scrubbed.values():
            remaining = _detect_entities(value, ai_enabled=ai_enabled)
            for entity in remaining:
                leaked[entity.category] = leaked.get(entity.category, 0) + 1
        if leaked:
            summary = ", ".join(f"{category}: {count}" for category, count in sorted(leaked.items()))
            raise PrivacyLeakDetected(f"Privacy filter blocked the send because sensitive text remained ({summary}).")
        return scrubbed, report


_SESSIONS: OrderedDict[str, PrivacySession] = OrderedDict()
_SESSIONS_LOCK = threading.RLock()
_MAX_SESSIONS = 100


def get_session(session_id: str) -> PrivacySession:
    key = str(session_id or "default").strip() or "default"
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(key)
        if session is None:
            session = PrivacySession(key)
            _SESSIONS[key] = session
        else:
            _SESSIONS.move_to_end(key)
        while len(_SESSIONS) > _MAX_SESSIONS:
            _SESSIONS.popitem(last=False)
        return session


def forget_session(session_id: str) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS.pop(str(session_id or "default"), None)


def privacy_enabled() -> bool:
    try:
        import config

        return bool(getattr(config, "TRUST_PRIVACY_MODE", True))
    except Exception:
        return True


def ai_detection_enabled() -> bool:
    try:
        import config

        return bool(getattr(config, "PRIVACY_AI_ENABLED", False))
    except Exception:
        return False


def configured_privacy_mode() -> str:
    """Return the effective mutually exclusive privacy mode."""
    if not privacy_enabled():
        return "off"
    return "advanced" if ai_detection_enabled() else "builtin"


def review_enabled() -> bool:
    try:
        import config

        return bool(getattr(config, "PRIVACY_REVIEW_BEFORE_SEND", True))
    except Exception:
        return True


def scrub_cloud_fields(
    fields: dict[str, str],
    *,
    session_id: str,
    review: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[PrivacySession | None, dict[str, str], dict[str, Any]]:
    """Apply the configured gate to a cloud caller outside the chat handlers.

    Background callers cannot safely open a modal review, but they still pass
    through detection, stable substitution, and the post-scrub invariant.
    Interactive callers supply their review callback directly to
    :meth:`PrivacySession.scrub_fields`.
    """
    normalized = {name: str(value or "") for name, value in fields.items()}
    if not privacy_enabled():
        return None, normalized, {}
    session = get_session(session_id)
    scrubbed, report = session.scrub_fields(
        normalized,
        ai_enabled=ai_detection_enabled(),
        review=review,
    )
    return session, scrubbed, report
