"""Path bootstrap helpers for the pure-Python macOS worker processes."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root, honoring bundled/dev overrides."""
    env_root = os.environ.get("WISP_REPO_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def brain_dir() -> Path:
    """Return the existing macOS brain package directory."""
    return repo_root() / "macos" / "brain"


def configure_paths(*, include_brain: bool = False) -> Path:
    """Make shared repo modules importable and return the repo root."""
    root = repo_root()
    paths = [root]
    if include_brain:
        paths.insert(0, brain_dir())
    for path in reversed(paths):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
    return root

