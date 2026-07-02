"""Persistent user labels for the UI Lab addon."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.system.paths import REPO_ROOT


ADDON_ID = "ui-lab"
SETTING_KEY = "labels"
DATA_FILENAME = "labels.json"
MAX_LABELS = 128
MAX_MATCH_CHARS = 160
MAX_TOOLTIP_CHARS = 240
DEFAULT_STYLE = "text-decoration:underline"
_data_dir = REPO_ROOT / "addon_data" / ADDON_ID
_labels_path = _data_dir / DATA_FILENAME


def configure_data_dir(path: str | Path) -> None:
    """Set the addon-owned data directory used for saved label rules."""
    global _data_dir, _labels_path
    _data_dir = Path(path)
    _labels_path = _data_dir / DATA_FILENAME
    _migrate_legacy_settings()


def load_rules() -> list[dict[str, str]]:
    """Return sanitized UI Lab label rules."""
    _migrate_legacy_settings()
    raw = _read_rules()
    if not isinstance(raw, list):
        return []
    return _sanitize_rules(raw)


def save_rules(rules: list[dict[str, Any]]) -> None:
    """Persist sanitized UI Lab label rules."""
    _write_rules(_sanitize_rules(rules))


def labels_path() -> Path:
    """Return the current UI Lab labels file path."""
    return _labels_path


def _sanitize_rules(rules: list[Any]) -> list[dict[str, str]]:
    clean: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in rules:
        if len(clean) >= MAX_LABELS:
            break
        rule = _sanitize_rule(item)
        if rule is None:
            continue
        key = rule["match"].casefold()
        if key in seen:
            continue
        seen.add(key)
        clean.append(rule)
    return clean


def _read_rules() -> list[Any]:
    try:
        if not _labels_path.exists():
            return []
        data = json.loads(_labels_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        rules = data.get("labels", [])
        return rules if isinstance(rules, list) else []
    return data if isinstance(data, list) else []


def _write_rules(rules: list[dict[str, str]]) -> None:
    _labels_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _labels_path.with_suffix(_labels_path.suffix + ".tmp")
    tmp.write_text(json.dumps({"labels": rules}, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(_labels_path)


def _migrate_legacy_settings() -> None:
    if _labels_path.exists():
        return
    try:
        from core import addon_store

        legacy = addon_store.get_setting(ADDON_ID, SETTING_KEY, None)
    except Exception:
        return
    if not isinstance(legacy, list):
        return
    clean = _sanitize_rules(legacy)
    if clean:
        _write_rules(clean)
    try:
        addon_store.delete_setting(ADDON_ID, SETTING_KEY)
    except Exception:
        pass


def find_rule(match: str) -> dict[str, str] | None:
    """Return the saved rule for a selected word or phrase."""
    key = _clean_match(match).casefold()
    if not key:
        return None
    for rule in load_rules():
        if rule["match"].casefold() == key:
            return rule
    return None


def upsert_rule(match: str, *, tooltip: str, style: str) -> dict[str, str] | None:
    """Create or replace one label rule."""
    clean_match = _clean_match(match)
    if not clean_match:
        return None
    rule = {
        "match": clean_match,
        "tooltip": _clean_text(tooltip, MAX_TOOLTIP_CHARS),
        "tag": "span",
        "style": _clean_text(style, 360),
    }
    rules = [item for item in load_rules() if item["match"].casefold() != clean_match.casefold()]
    rules.append(rule)
    save_rules(rules)
    return rule


def delete_rule(match: str) -> bool:
    """Delete one label rule."""
    key = _clean_match(match).casefold()
    if not key:
        return False
    rules = load_rules()
    kept = [item for item in rules if item["match"].casefold() != key]
    if len(kept) == len(rules):
        return False
    save_rules(kept)
    return True


def annotations_for_text(text: str, *, surface: str = "chat") -> list[dict[str, str | int]]:
    """Return range annotations for all saved labels in text."""
    source = str(text or "")
    if not source:
        return []
    annotations: list[dict[str, str | int]] = []
    for rule in load_rules():
        for start, end in _match_ranges(source, rule["match"]):
            annotations.append(
                {
                    "start": start,
                    "end": end,
                    "tag": rule.get("tag", "span"),
                    "style": rule.get("style", ""),
                    "tooltip": rule.get("tooltip", ""),
                    "id": "ui-lab-label-" + _rule_id(rule["match"]),
                    "surface": surface,
                }
            )
            if len(annotations) >= 128:
                return annotations
    return annotations


def _match_ranges(text: str, match: str) -> list[tuple[int, int]]:
    """Find case-insensitive exact word/phrase occurrences."""
    needle = _clean_match(match)
    if not needle:
        return []
    flags = re.IGNORECASE
    escaped = re.escape(needle)
    if re.fullmatch(r"\w+", needle):
        pattern = re.compile(rf"(?<!\w){escaped}(?!\w)", flags)
    else:
        pattern = re.compile(escaped, flags)
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


def _sanitize_rule(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    match = _clean_match(item.get("match", ""))
    if not match:
        return None
    return {
        "match": match,
        "tooltip": _clean_text(item.get("tooltip", ""), MAX_TOOLTIP_CHARS),
        "tag": "span",
        "style": _clean_text(item.get("style", ""), 360),
    }


def _clean_match(value: object) -> str:
    text = _clean_text(value, MAX_MATCH_CHARS)
    return re.sub(r"\s+", " ", text).strip()


def _clean_text(value: object, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[: max(0, limit)]


def _rule_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:48] or "label"
