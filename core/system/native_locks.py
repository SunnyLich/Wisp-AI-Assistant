"""
core/system/native_locks.py - Serialize macOS-only native-init hazards.

On macOS, constructing an HTTP/SDK client can build an SSL context, which reads
the system trust store through the Security framework. Keychain operations via
keyring also enter the Security framework. Those native initialization paths are
not reliably thread-safe when multiple workers hit them at the same time.

`native_init_lock()` is one process-wide lock shared across SDK client
construction and keychain access on macOS. Everywhere the hazard does not exist
(Windows/Linux) it is a no-op context manager, so callers can wrap native-boundary
initialization unconditionally.
"""
from __future__ import annotations

import contextlib
import sys
import threading

_IS_MAC = sys.platform == "darwin"

# Shared across TTS, LLM client builders, and keychain access. Held only while a
# native object is being created/read, never during normal streaming.
_native_init_lock = threading.Lock()

# Backwards-compatible name used by tests and older call sites.
_ssl_init_lock = _native_init_lock


def native_init_lock():
    """Serialize macOS native initialization; no-op elsewhere."""
    if _IS_MAC:
        return _native_init_lock
    return contextlib.nullcontext()


def ssl_init_lock():
    """Serialize SSL/SDK client construction on macOS; no-op elsewhere.

    Returns a context manager. Use as ``with ssl_init_lock(): client = SDK(...)``.
    """
    return native_init_lock()


def keychain_lock():
    """Serialize keyring/Security-framework calls on macOS; no-op elsewhere."""
    return native_init_lock()
