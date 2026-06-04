"""
core.macos_helper — out-of-process isolation for crash-prone native work.

On macOS, several native libraries (sounddevice/PortAudio → CoreAudio, faster-
whisper/torch, mss/Quartz) are fragile when they share a process with Qt's Cocoa
run loop. This package runs that work in a separate "native worker" subprocess so
that:

  1. A segfault in the worker kills the worker, not the Qt GUI (the parent
     supervises and restarts it), and
  2. The worker's own CoreAudio / run-loop machinery runs on *its* main thread,
     never contending with Qt's.

Layout:
  - protocol.py   newline-delimited JSON framing shared by both sides.
  - host.py       the worker entry point: ``python -m core.macos_helper.host``.
  - handlers.py   methods that run *inside* the worker (STT now; TTS/audio next).
  - client.py     the parent-side supervisor + request/response transport.

The whole package is gated behind ``WISP_MACOS_HELPER=1`` (see ``is_enabled``)
and is a no-op everywhere it is not enabled, so the in-process paths remain the
default until the worker is proven on the Mac.
"""
from __future__ import annotations

import os
import sys


def is_enabled() -> bool:
    """True when the macOS helper subprocess should be used instead of in-process
    native work. macOS-only and opt-in via ``WISP_MACOS_HELPER=1``."""
    return sys.platform == "darwin" and os.environ.get("WISP_MACOS_HELPER") == "1"
