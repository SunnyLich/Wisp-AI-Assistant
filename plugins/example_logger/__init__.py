"""
example_logger — example Wisp mod.

Demonstrates every available hook. Copy this folder, rename it, and
delete the hooks you don't need.

SECURITY: Mods run in-process with full Python access.
Only install mods from sources you trust.
"""
from __future__ import annotations
import logging

log = logging.getLogger("wisp.mod.example_logger")

_ctx = None


def on_startup(app_context) -> None:
    global _ctx
    _ctx = app_context
    log.info("example_logger started. LLM provider = %s", app_context.config.LLM_PROVIDER)


def on_shutdown() -> None:
    log.info("example_logger shutdown.")


def before_query(prompt: str, context: str) -> tuple[str, str]:
    log.debug("example_logger before_query: %r", prompt[:80])
    return prompt, context


def after_response(text: str) -> None:
    log.debug("example_logger after_response: %r", text[:80])


def get_tray_actions() -> list[dict]:
    return [
        {"label": "Example mod action", "callback": _on_tray_click},
    ]


def _on_tray_click() -> None:
    # Runs in the headless brain worker — fine for side effects / automation,
    # but there's no Qt here (app_context.signals is None), so it can't open UI.
    log.info("example_logger tray action clicked.")


def get_tools() -> list[dict]:
    return [
        {
            "name": "example_echo",
            "description": "Echo back whatever text is passed to it. Example mod tool.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to echo back."}
                },
                "required": ["text"],
            },
            "executor": lambda inputs: inputs.get("text", ""),
        }
    ]
