"""wisp-brain worker wrapper for the pure-Python target."""

from __future__ import annotations

import sys

from runtime import VERSION
from runtime.boundaries import boundary_status
from runtime.bootstrap import configure_paths


def main() -> int:
    """Handle main for runtime workers brain host."""
    configure_paths(include_brain=True)
    from wisp_brain import handlers

    def brain_ping(value=None):
        """Handle brain ping for runtime workers brain host."""
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
