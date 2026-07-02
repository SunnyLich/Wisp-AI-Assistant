"""Persistent addon enablement and settings storage."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from core.system.paths import REPO_ROOT

_STORE_PATH = REPO_ROOT / "addons.json"


def store_path() -> Path:
    """Handle store path for addon store."""
    override = os.getenv("WISP_ADDON_STORE")
    return Path(override) if override else _STORE_PATH


def _read() -> dict[str, Any]:
    """Load the addon store JSON, returning an empty store on any error."""
    path = store_path()
    if not path.exists():
        return {"addons": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"addons": {}}
    return data if isinstance(data, dict) else {"addons": {}}


def _write(data: dict[str, Any]) -> None:
    """Atomically write the addon store JSON via a temp file + replace."""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _addon(data: dict[str, Any], addon_id: str) -> dict[str, Any]:
    """Handle addon for addon store."""
    addons = data.setdefault("addons", {})
    if not isinstance(addons, dict):
        data["addons"] = addons = {}
    item = addons.setdefault(addon_id, {})
    if not isinstance(item, dict):
        addons[addon_id] = item = {}
    return item


def is_enabled(addon_id: str, default: bool = True) -> bool:
    """Return whether enabled is true."""
    item = _read().get("addons", {}).get(addon_id, {})
    if isinstance(item, dict) and "enabled" in item:
        return bool(item.get("enabled"))
    return default


def set_enabled(addon_id: str, enabled: bool) -> None:
    """Set enabled."""
    data = _read()
    _addon(data, addon_id)["enabled"] = bool(enabled)
    _write(data)


def get_setting(addon_id: str, key: str, default: Any = None) -> Any:
    """Return setting."""
    item = _read().get("addons", {}).get(addon_id, {})
    settings = item.get("settings", {}) if isinstance(item, dict) else {}
    if isinstance(settings, dict) and key in settings:
        return settings[key]
    return default


def set_setting(addon_id: str, key: str, value: Any) -> None:
    """Set setting."""
    data = _read()
    item = _addon(data, addon_id)
    settings = item.setdefault("settings", {})
    if not isinstance(settings, dict):
        item["settings"] = settings = {}
    settings[str(key)] = value
    _write(data)


def delete_setting(addon_id: str, key: str) -> None:
    """Delete one addon setting if present."""
    data = _read()
    item = data.get("addons", {}).get(addon_id, {})
    settings = item.get("settings", {}) if isinstance(item, dict) else {}
    if not isinstance(settings, dict) or key not in settings:
        return
    del settings[key]
    if not settings:
        item.pop("settings", None)
    _write(data)


def approved_dependency_hash(addon_id: str) -> str:
    """Handle approved dependency hash for addon store."""
    item = _read().get("addons", {}).get(addon_id, {})
    if not isinstance(item, dict):
        return ""
    return str(item.get("approved_dependency_hash") or "")


def set_approved_dependency_hash(addon_id: str, dependency_hash: str) -> None:
    """Set approved dependency hash."""
    data = _read()
    _addon(data, addon_id)["approved_dependency_hash"] = str(dependency_hash)
    _write(data)


def record_llm_call(addon_id: str, *, limit: int = 5, window_seconds: int = 3600) -> tuple[bool, int]:
    """Record llm call."""
    now = time.time()
    data = _read()
    item = _addon(data, addon_id)
    calls = item.setdefault("llm_calls", [])
    if not isinstance(calls, list):
        item["llm_calls"] = calls = []
    cutoff = now - max(1, int(window_seconds))
    calls[:] = [float(ts) for ts in calls if _is_recent_timestamp(ts, cutoff)]
    if len(calls) >= max(0, int(limit)):
        _write(data)
        return False, max(0, int(limit)) - len(calls)
    calls.append(now)
    _write(data)
    return True, max(0, int(limit)) - len(calls)


def _is_recent_timestamp(value: Any, cutoff: float) -> bool:
    """Return whether recent timestamp is true."""
    try:
        return float(value) >= cutoff
    except Exception:
        return False
