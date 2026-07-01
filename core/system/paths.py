"""Canonical filesystem locations for the app."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_NAME = "Wisp"


def _user_data_dir() -> Path:
    """
    Return a platform-appropriate, user-writable directory for settings and
    data that must survive rebuilds and updates.

    Linux:   $XDG_CONFIG_HOME/wisp  (~/.config/wisp)
    Windows: %APPDATA%\\Wisp
    macOS:   ~/Library/Application Support/Wisp
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / _APP_NAME
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_NAME
    else:
        xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        return xdg / _APP_NAME.lower()


def _bundle_root() -> Path:
    """
    Return the root of read-only bundled files (assets).
    In a PyInstaller onedir build this is sys._MEIPASS (_internal/).
    In a dev run it is the repo root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def _executable_root() -> Path | None:
    """Return the app executable folder for packaged builds."""
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    return executable.parent if executable.name else None


def _writable_dir(path: Path) -> bool:
    """Return whether *path* can be created and written to."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".wisp-write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _repo_root() -> Path:
    """Return the repo root in dev mode; user data dir when frozen."""
    override = os.environ.get("WISP_REPO_ROOT")
    if override:
        d = Path(override).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d
    if getattr(sys, "frozen", False):
        d = _user_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d
    return Path(__file__).resolve().parents[2]


def _addons_dir() -> Path:
    """
    Return the user-installable addon folder.

    Packaged zip/onedir builds are portable by default when their executable
    folder is writable, so addons live next to Wisp.exe. Installed/read-only
    locations fall back to the normal user data root.
    """
    override = os.environ.get("WISP_ADDONS_DIR")
    if override:
        d = Path(override).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d
    exe_root = _executable_root()
    if exe_root is not None:
        portable_addons = exe_root / "addons"
        if _writable_dir(portable_addons):
            return portable_addons
    return REPO_ROOT / "addons"


# ---------------------------------------------------------------------------
# Public paths
# ---------------------------------------------------------------------------

REPO_ROOT = _repo_root()          # user-writable: .env, memory, addons
_BUNDLE = _bundle_root()           # read-only bundled files
USER_DATA_DIR = _user_data_dir()   # stable across repo/dev and packaged launches

ASSETS_DIR       = _BUNDLE / "assets"
DOLL_ASSETS_DIR  = ASSETS_DIR / "doll"
BUNDLED_ADDONS_DIR = _BUNDLE / "addons"
UPDATE_DOWNLOAD_DIR = _user_data_dir() / "updates"

# Single-instance lock. Lives in the user-data dir (not the repo / bundle) so a
# dev run (`python -m runtime.supervisor.app`) and an installed build contend for the *same* lock —
# only one Wisp can be active at a time, regardless of how it was launched.
SINGLE_INSTANCE_LOCK = _user_data_dir() / "wisp.lock"

MEMORY_DIR        = REPO_ROOT / "memory"
AGENT_RUNS_DIR    = MEMORY_DIR / "agent_runs"
# Persisted chat history + project definitions (user-writable, gitignored).
CHATS_DIR         = REPO_ROOT / "chats"
PROJECTS_FILE     = CHATS_DIR / "projects.json"
CONVERSATIONS_FILE = CHATS_DIR / "conversations.json"
CHAT_ATTACHMENTS_DIR = CHATS_DIR / "attachments"
TOOLS_INSTALLED_DIR = REPO_ROOT / "tools" / "installed"
MODEL_TOOLS_DIR     = REPO_ROOT / "model_tools"
MODEL_FILE_ACCESS_DIR = REPO_ROOT / "model_files"
ADDONS_DIR          = _addons_dir()
