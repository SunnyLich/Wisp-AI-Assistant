"""Diagnostic addon that proves the addon host system is wired up."""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

from core.addon_manager import addon_setting

_LOG = Path(__file__).with_name("healthcheck.log")


def _prefix() -> str:
    """Support package marker and exports for addons healthcheck for prefix."""
    return str(addon_setting("healthcheck", "log_prefix", "[healthcheck]"))


def _log(event: str) -> None:
    """Append a timestamped line to healthcheck.log and echo it to stderr."""
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {event}\n"
    try:
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(f"[addon:healthcheck] {_prefix()} {event}", file=sys.stderr, flush=True)


def get_settings() -> list[dict]:
    """Return settings."""
    return [
        {
            "key": "log_prefix",
            "label": "Log prefix",
            "type": "text",
            "default": "[healthcheck]",
            "help": "Text prefixed to every line this addon writes to stderr.",
        },
        {
            "key": "echo_suffix",
            "label": "Echo suffix",
            "type": "text",
            "default": "",
            "help": "Text appended to healthcheck_ping replies.",
        },
    ]


def on_startup(app_context) -> None:
    """Handle startup events."""
    provider = getattr(app_context.config, "LLM_PROVIDER", "?")
    _log(f"on_startup fired; LLM_PROVIDER={provider}, data_dir={app_context.data_dir}")


def on_shutdown() -> None:
    """Handle shutdown events."""
    _log("on_shutdown fired")


def before_query(prompt: str, context: str) -> tuple[str, str]:
    """Support package marker and exports for addons healthcheck for before query."""
    _log(f"before_query fired; prompt={prompt[:60]!r}")
    return prompt, context


def after_response(text: str) -> None:
    """Support package marker and exports for addons healthcheck for after response."""
    _log(f"after_response fired; {len(text)} chars")


def on_event(event: str, payload: dict) -> dict:
    """Handle event events."""
    _log(f"event fired; event={event}, keys={','.join(sorted((payload or {}).keys()))}")
    return {"ok": True, "event": event}


def get_tray_actions() -> list[dict]:
    """Return tray actions."""
    return []


def _on_tray_click() -> None:
    """Handle tray click events."""
    _log("tray action clicked")


def get_tools() -> list[dict]:
    """Return tools."""
    return [
        {
            "name": "healthcheck_ping",
            "description": "Diagnostic tool from the healthcheck addon. Returns pong and logs the call.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Optional note to echo back."}
                },
                "required": [],
            },
            "executor": _ping_executor,
        }
    ]


def get_hotkeys() -> list[dict]:
    """Return hotkeys."""
    return [
        {
            "id": "healthcheck-dynamic-hotkey",
            "label": "Healthcheck dynamic hotkey",
            "hotkey": "ctrl+alt+shift+h",
            "callback": _hotkey_callback,
        }
    ]


def _hotkey_callback(_payload: dict) -> dict:
    """Support package marker and exports for addons healthcheck for hotkey callback."""
    _log("dynamic hotkey fired")
    return {"message": "Healthcheck hotkey fired."}


def _ping_executor(inputs: dict) -> str:
    """Support package marker and exports for addons healthcheck for ping executor."""
    note = str((inputs or {}).get("note", "")).strip()
    suffix = str(addon_setting("healthcheck", "echo_suffix", "")).strip()
    _log(f"healthcheck_ping executed; note={note!r}")
    return "pong" + (f" - {note}" if note else "") + (f" {suffix}" if suffix else "")
