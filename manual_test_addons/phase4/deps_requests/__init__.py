from __future__ import annotations

import requests


def before_query(prompt: str, context: str) -> tuple[str, str]:
    marker = f"[phase4-deps-requests requests={requests.__version__}]"
    return f"{prompt}\n\n{marker}", context


def after_response(text: str) -> None:
    print("phase4-deps-requests after_response length", len(text), flush=True)


def get_tray_actions() -> list[dict]:
    return [{"label": "Dependency addon loaded", "callback": lambda: None}]


def get_settings() -> list[dict]:
    return [
        {
            "key": "enabled_note",
            "label": "Enabled note",
            "type": "text",
            "default": "requests import works",
        }
    ]

