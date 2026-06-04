"""
core/system/native_locks.py — Serialize macOS-only native-init hazards.

On macOS, constructing an HTTP/SDK client builds an SSL context, which calls
ssl.create_default_context() → reads the system trust store through the Security
framework. Doing that from two threads at once segfaults — the same
Security-framework family as the cached keychain reads and the AppKit/CoreAudio
main-thread rule (see core.system.main_thread). A single query already runs the
LLM stream and the TTS stream on separate threads, so two cold clients can build
their SSL contexts concurrently on the very first request and crash.

`ssl_init_lock()` is one process-wide lock shared across ALL SDK client
construction (TTS + LLM) so no two SSL contexts are ever built at the same time
on macOS. Everywhere the hazard does not exist (Windows/Linux) it is a no-op
context manager, so callers can wrap construction unconditionally with zero cost.
"""
from __future__ import annotations

import contextlib
import sys
import threading

_IS_MAC = sys.platform == "darwin"

# Shared across TTS and every LLM client builder. Held only while a client is
# being constructed (first use / prewarm), never during streaming.
_ssl_init_lock = threading.Lock()


def ssl_init_lock():
    """Serialize SSL/SDK client construction on macOS; no-op elsewhere.

    Returns a context manager. Use as ``with ssl_init_lock(): client = SDK(...)``.
    """
    if _IS_MAC:
        return _ssl_init_lock
    return contextlib.nullcontext()
