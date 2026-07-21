"""Pure-Python multi-process Wisp target.

This package owns the worker-supervisor runtime used by the shared Python app.
It intentionally keeps UI, native input/capture, brain, and audio imports split
by responsibility.
"""

from __future__ import annotations

VERSION = "0.10.2"
