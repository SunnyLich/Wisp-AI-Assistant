"""Compatibility wrapper for settings environment helpers."""
from __future__ import annotations

from core.system.env_utils import format_env_value, read_env_file, write_env_file
from ui.settings_panel.env import ENV_PATH

__all__ = [
    "ENV_PATH",
    "format_settings_env_value",
    "read_settings_env",
    "write_settings_env",
]


def read_settings_env() -> dict[str, str]:
    """Read settings env."""
    return read_env_file(ENV_PATH)


def format_settings_env_value(value: str) -> str:
    """Format settings env value."""
    return format_env_value(value)


def write_settings_env(vals: dict[str, str], remove_keys: set[str] | None = None) -> None:
    """Write settings env."""
    write_env_file(ENV_PATH, vals, remove_keys=remove_keys)
