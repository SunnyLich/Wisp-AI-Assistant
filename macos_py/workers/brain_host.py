"""wisp-brain worker wrapper.

The existing ``macos/brain/wisp_brain`` host already has the right process
shape: lazy imports, request-id events, streaming replies, and stdout
protection. This module makes it launchable as part of the pure-Python target.
"""

from __future__ import annotations

import sys

from macos_py import VERSION
from macos_py.boundaries import boundary_status
from macos_py.bootstrap import configure_paths


def main() -> int:
    configure_paths(include_brain=True)
    from wisp_brain import handlers

    def brain_ping(value=None):
        import os

        return {
            "pong": True,
            "value": value,
            "pid": os.getpid(),
            "role": "brain",
            "version": VERSION,
            "boundary": boundary_status("brain"),
        }

    handlers.HANDLERS.setdefault("brain.ping", brain_ping)
    handlers.HANDLERS.setdefault("boundary.status", lambda: boundary_status("brain"))
    from wisp_brain.host import _main

    return _main()


if __name__ == "__main__":
    sys.exit(main())
