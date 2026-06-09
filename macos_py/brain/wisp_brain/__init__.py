"""Headless brain worker for the pure-Python app.

The supervisor launches this package in its own process so LLM routing, memory,
agent runs, local model imports, STT, and TTS stay out of the Qt UI process.
It speaks newline-delimited JSON over stdin/stdout using the same lightweight
framing as the other Python workers.
"""

from __future__ import annotations

__all__ = ["protocol", "handlers", "host"]

