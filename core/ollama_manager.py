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
from urllib.parse import urlsplit

_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_START_TIMEOUT_SECONDS = 12.0
_PROBE_TIMEOUT_SECONDS = 0.75
_POLL_INTERVAL_SECONDS = 0.2
_start_lock = threading.Lock()


def resolve_ollama_base_url() -> str:
    """Resolve the OpenAI-compatible base URL of the Ollama server.

    Honors Ollama's own ``OLLAMA_HOST`` convention (host, host:port, or URL)
    so Wisp's requests, the readiness probe, and an auto-started ``ollama
    serve`` — which inherits the same environment — all agree on one endpoint.
    """
    raw = os.environ.get("OLLAMA_HOST", "").strip().rstrip("/")
    if not raw:
        return _DEFAULT_BASE_URL
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parts = urlsplit(raw)
        port = parts.port or 11434
    except ValueError:
        return _DEFAULT_BASE_URL
    host = parts.hostname or "localhost"
    if host in ("0.0.0.0", "::"):
        # A bind-everything server address; clients connect over loopback.
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme or 'http'}://{host}:{port}/v1"


OLLAMA_BASE_URL = resolve_ollama_base_url()


def _api_probe_url(base_url: str | None) -> str:
    """Map an OpenAI-compatible base URL onto Ollama's native tags endpoint."""
    base = (base_url or OLLAMA_BASE_URL).strip().rstrip("/")
    if "://" not in base:
        base = f"http://{base}"
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/api/tags"


def _is_local_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in ("localhost", "::1") or host.startswith("127.")


def ollama_is_running(base_url: str | None = None) -> bool:
    """Return whether the Ollama API server is accepting requests."""
    request = urllib.request.Request(_api_probe_url(base_url), method="GET")
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


def ensure_ollama_running(
    *,
    timeout_seconds: float = _START_TIMEOUT_SECONDS,
    base_url: str | None = None,
) -> bool:
    """Ensure the Ollama API is available, starting installed Ollama if needed.

    Returns ``True`` only when this call launched Ollama.  It never installs or
    terminates Ollama, so another application can keep using the shared server.
    Auto-start applies only to loopback endpoints — launching a local server
    cannot make a remote ``base_url`` reachable.
    """
    if ollama_is_running(base_url):
        return False

    if not _is_local_url(_api_probe_url(base_url)):
        raise RuntimeError(
            f"Ollama at {base_url} is not responding. Wisp only auto-starts a local "
            "Ollama server, so start or check that server, then try again."
        )

    with _start_lock:
        # A concurrent Wisp request, or the Ollama tray app, may have started it
        # while this call waited for the lock.
        if ollama_is_running(base_url):
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
            if ollama_is_running(base_url):
                return True
            time.sleep(_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        "Wisp started Ollama, but its local server did not become ready. "
        "Open Ollama once to check it, then try again."
    )
