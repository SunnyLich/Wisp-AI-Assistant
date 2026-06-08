"""Pure-Python macOS multi-process Wisp target.

This package is a parallel macOS runtime. It intentionally does not import
``main.py``: Windows/Linux keep using the existing single-process Qt entrypoint,
while macOS Python workers are split by responsibility.
"""

from __future__ import annotations

VERSION = "0.1.0"

