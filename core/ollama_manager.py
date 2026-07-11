"""Start an already-installed Ollama server when Wisp needs it.

This deliberately does not install Ollama, pull models, or stop a process.  It
only makes the existing local Ollama provider work without asking the user to
open the Ollama app first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_API_TAGS_URL = "http://127.0.0.1:11434/api/tags"
_START_TIMEOUT_SECONDS = 12.0
_PROBE_TIMEOUT_SECONDS = 0.75
_POLL_INTERVAL_SECONDS = 0.2
_start_lock = threading.Lock()


def ollama_is_running() -> bool:
    """Return whether an Ollama API server is accepting local requests."""
    request = urllib.request.Request(OLLAMA_API_TAGS_URL, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=_PROBE_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def find_ollama_executable() -> Path | None:
    """Find a user-installed Ollama executable without requiring PATH setup."""
    configured = os.environ.get("OLLAMA_BIN", "").strip()
    candidates: list[Path] = [Path(configured).expanduser()] if configured else []

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", "")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "")
        candidates.extend(
            Path(folder) / "Ollama" / "ollama.exe"
            for folder in (local_app_data, program_files, program_files_x86)
            if folder
        )
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Ollama.app/Contents/Resources/ollama"))

    on_path = shutil.which("ollama")
    if on_path:
        candidates.append(Path(on_path))

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _start_ollama(executable: Path) -> None:
    """Launch Ollama's server with no visible console window."""
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([str(executable), "serve"], **kwargs)  # noqa: S603 -- executable is locally discovered.


def ensure_ollama_running(*, timeout_seconds: float = _START_TIMEOUT_SECONDS) -> bool:
    """Ensure the local Ollama API is available, starting installed Ollama if needed.

    Returns ``True`` only when this call launched Ollama.  It never installs or
    terminates Ollama, so another application can keep using the shared server.
    """
    if ollama_is_running():
        return False

    with _start_lock:
        # A concurrent Wisp request, or the Ollama tray app, may have started it
        # while this call waited for the lock.
        if ollama_is_running():
            return False

        executable = find_ollama_executable()
        if executable is None:
            raise RuntimeError(
                "Ollama is not running and Wisp could not find an installed Ollama application. "
                "Install Ollama, then try again."
            )
        try:
            _start_ollama(executable)
        except OSError as exc:
            raise RuntimeError(f"Wisp could not start Ollama from {executable}: {exc}") from exc

        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while time.monotonic() < deadline:
            if ollama_is_running():
                return True
            time.sleep(_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        "Wisp started Ollama, but its local server did not become ready. "
        "Open Ollama once to check it, then try again."
    )
