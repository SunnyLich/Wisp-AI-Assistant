"""Test addon: open an editor with a Wisp greeting on standalone 'hi'."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

_HI_WORD = re.compile(r"(?<![A-Za-z0-9_])hi(?![A-Za-z0-9_])", re.IGNORECASE)
_GREETING = "hi from wisp"


def before_query(prompt: str, context: str) -> tuple[str, str]:
    """React only when 'hi' appears as its own word."""
    if not _HI_WORD.search(prompt or ""):
        return prompt, context

    _open_editor_with_text(_GREETING)
    return prompt, context


def _open_editor_with_text(text: str) -> None:
    """Launch a platform editor with *text* in the opened document."""
    try:
        note_path = Path(tempfile.gettempdir()) / "wisp-hi-from-wisp.txt"
        note_path.write_text(text, encoding="utf-8")
        command = _editor_command(note_path)
        if not command:
            return
        subprocess.Popen(  # noqa: S603 - intentional local test addon action.
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        pass


def _editor_command(note_path: Path) -> list[str]:
    """Return an editor/open command for the current platform."""
    if sys.platform == "win32":
        return ["notepad.exe", str(note_path)]
    if sys.platform.startswith("linux"):
        for env_name in ("VISUAL", "EDITOR"):
            command = _split_command(os.environ.get(env_name, ""))
            if command and _command_exists(command[0]):
                return [*command, str(note_path)]
        for command in ("xdg-open", "gedit", "kate", "kwrite", "xed", "mousepad"):
            if shutil.which(command):
                return [command, str(note_path)]
    return []


def _split_command(value: str) -> list[str]:
    """Split an editor command while tolerating malformed env values."""
    value = (value or "").strip()
    if not value:
        return []
    try:
        return shlex.split(value)
    except ValueError:
        return [value]


def _command_exists(command: str) -> bool:
    """Return whether *command* can be launched."""
    if not command:
        return False
    path = Path(command).expanduser()
    return path.exists() or shutil.which(command) is not None
