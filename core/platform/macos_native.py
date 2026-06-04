"""Small out-of-process macOS helpers.

The Python UI process should avoid loading or driving fragile macOS-native
capture/key APIs whenever a system helper can do the job in a separate process.
These functions are no-ops off macOS so callers can guard cheaply.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

IS_MAC = sys.platform == "darwin"


def _run(args: list[str], *, timeout: float = 5.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def capture_screen_to_file(out_path: str | os.PathLike, region: dict | None = None) -> bool:
    """Capture a PNG using macOS' `screencapture` CLI.

    Returns True only when a non-empty file was written. `region`, when supplied,
    uses mss-style keys: left, top, width, height.
    """
    if not IS_MAC:
        return False
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["/usr/sbin/screencapture", "-x", "-t", "png"]
    if region:
        try:
            left = int(region["left"])
            top = int(region["top"])
            width = int(region["width"])
            height = int(region["height"])
            if width <= 0 or height <= 0:
                return False
            cmd.extend(["-R", f"{left},{top},{width},{height}"])
        except Exception:
            return False
    cmd.append(str(out))
    try:
        result = _run(cmd, timeout=10.0)
    except Exception:
        return False
    return result.returncode == 0 and out.exists() and out.stat().st_size > 0


_APPLESCRIPT_MODS = {
    "cmd": "command down",
    "command": "command down",
    "win": "command down",
    "ctrl": "control down",
    "control": "control down",
    "shift": "shift down",
    "alt": "option down",
    "option": "option down",
}


def send_key_combo(combo: str) -> bool:
    """Send a simple key combo through System Events in a separate process.

    This intentionally covers the combos Wisp uses for selected-text capture and
    paste-back (cmd+c/cmd+v, with optional modifiers). Unsupported combos return
    False so the caller can degrade or explicitly choose a legacy fallback.
    """
    if not IS_MAC:
        return False
    key: str | None = None
    modifiers: list[str] = []
    for raw in combo.lower().split("+"):
        token = raw.strip()
        if not token:
            continue
        if token in _APPLESCRIPT_MODS:
            mod = _APPLESCRIPT_MODS[token]
            if mod not in modifiers:
                modifiers.append(mod)
        elif len(token) == 1:
            key = token
        else:
            return False
    if not key:
        return False
    script = f'tell application "System Events" to keystroke {json.dumps(key)}'
    if modifiers:
        script += " using {" + ", ".join(modifiers) + "}"
    try:
        return _run(["/usr/bin/osascript", "-e", script], timeout=3.0).returncode == 0
    except Exception:
        return False


def get_clipboard_text() -> str | None:
    """Read the macOS text clipboard via `pbpaste`."""
    if not IS_MAC:
        return None
    try:
        result = subprocess.run(
            ["/usr/bin/pbpaste"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.0,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def set_clipboard_text(text: str) -> bool:
    """Write the macOS text clipboard via `pbcopy`."""
    if not IS_MAC:
        return False
    try:
        result = subprocess.run(
            ["/usr/bin/pbcopy"],
            input=text or "",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.0,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0


def get_selected_text(copy_combo: str = "cmd+c", *, settle_seconds: float = 0.08) -> str | None:
    """Copy the current selection, read it, then restore the previous clipboard."""
    if not IS_MAC:
        return None
    previous = get_clipboard_text()
    if not send_key_combo(copy_combo):
        return None
    time.sleep(max(0.0, settle_seconds))
    current = get_clipboard_text()
    if previous is not None:
        set_clipboard_text(previous)
    if current is None:
        return None
    text = current.strip()
    if not text or text == (previous or "").strip():
        return None
    return text


def paste_text(text: str, paste_combo: str = "cmd+v", *, settle_seconds: float = 0.05) -> bool:
    """Write text to the clipboard and paste it through System Events."""
    if not IS_MAC:
        return False
    if not set_clipboard_text(text):
        return False
    time.sleep(max(0.0, settle_seconds))
    return send_key_combo(paste_combo)
