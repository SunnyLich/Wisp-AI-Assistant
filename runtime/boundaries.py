"""Import-boundary checks for the pure-Python runtime worker split."""

from __future__ import annotations

import sys
from typing import Iterable

ROLE_FORBIDDEN_PREFIXES: dict[str, tuple[str, ...]] = {
    "ui": (
        "AppKit",
        "ApplicationServices",
        "CoreFoundation",
        "CoreGraphics",
        "Quartz",
        "objc",
        "pyobjc",
        "pynput",
        "sounddevice",
        "soundfile",
        "faster_whisper",
        "torch",
        "onnxruntime",
        "ctranslate2",
    ),
    "native": (
        "PySide6",
        "sounddevice",
        "soundfile",
        "faster_whisper",
        "torch",
        "onnxruntime",
        "ctranslate2",
    ),
    "brain": (
        "PySide6",
        "AppKit",
        "ApplicationServices",
        "CoreGraphics",
        "Quartz",
        "objc",
        "pyobjc",
        "sounddevice",
    ),
    "audio": (
        "PySide6",
        "AppKit",
        "ApplicationServices",
        "CoreGraphics",
        "Quartz",
        "objc",
        "pyobjc",
    ),
    "supervisor": (
        "PySide6",
        "AppKit",
        "ApplicationServices",
        "CoreGraphics",
        "Quartz",
        "objc",
        "pyobjc",
        "sounddevice",
        "faster_whisper",
        "torch",
        "onnxruntime",
    ),
}


def loaded_forbidden(role: str, modules: Iterable[str] | None = None) -> list[str]:
    """Return loaded module names forbidden for *role*."""
    prefixes = ROLE_FORBIDDEN_PREFIXES.get(role, ())
    names = list(sys.modules if modules is None else modules)
    found: list[str] = []
    for name in names:
        if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes):
            found.append(name)
    return sorted(set(found))


def boundary_status(role: str) -> dict[str, object]:
    """Handle boundary status for runtime boundaries."""
    forbidden = loaded_forbidden(role)
    return {
        "role": role,
        "ok": not forbidden,
        "forbidden_loaded": forbidden,
        "forbidden_prefixes": list(ROLE_FORBIDDEN_PREFIXES.get(role, ())),
    }
