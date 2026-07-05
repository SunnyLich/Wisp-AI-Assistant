"""Application icon and desktop identity helpers."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from core.system.paths import ASSETS_DIR, REPO_ROOT

APP_ID = "app.wisp.desktop"
LINUX_DESKTOP_FILE_NAME = "wisp"
APP_NAME = "Wisp"

log = logging.getLogger("wisp.app_icon")


def _asset_path(name: str) -> Path | None:
    """Return an existing icon asset path from the bundled assets directory."""
    path = ASSETS_DIR / name
    return path if path.exists() else None


def app_icon_path(platform: str | None = None) -> Path | None:
    """Return the best icon file for the current platform."""
    platform = platform or sys.platform
    if platform == "win32":
        preferred = ("app.ico", "app.png")
    elif platform == "darwin":
        preferred = ("app.icns", "app.png", "app.ico")
    else:
        preferred = ("app.png", "app.ico")
    for name in preferred:
        if path := _asset_path(name):
            return path
    return None


def set_windows_app_user_model_id(app_id: str = APP_ID, platform: str | None = None) -> bool:
    """Set the Windows taskbar identity for the current process."""
    if (platform or sys.platform) != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:
        log.exception("Failed to set Windows AppUserModelID")
        return False


def _desktop_exec_quote(value: str) -> str:
    """Quote one Exec argument per the Desktop Entry specification."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("$", "\\$")
        .replace('"', '\\"')
    )
    return f'"{escaped}"'


def _linux_desktop_entry_content() -> str:
    """Build the wisp.desktop payload for the current install location."""
    if getattr(sys, "frozen", False):
        exec_line = _desktop_exec_quote(sys.executable)
    else:
        launcher = REPO_ROOT / "Start Wisp.sh"
        if launcher.exists():
            exec_line = f"bash {_desktop_exec_quote(str(launcher))}"
        else:
            exec_line = (
                f"{_desktop_exec_quote(sys.executable)} "
                f"{_desktop_exec_quote(str(REPO_ROOT / 'main.py'))}"
            )
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "Comment=On-screen AI assistant overlay",
        f"Exec={exec_line}",
        "Terminal=false",
        "Categories=Utility;",
        f"StartupWMClass={LINUX_DESKTOP_FILE_NAME}",
    ]
    icon = app_icon_path("linux")
    if icon is not None:
        lines.insert(5, f"Icon={icon}")
    return "\n".join(lines) + "\n"


def ensure_linux_desktop_entry(platform: str | None = None) -> Path | None:
    """Install or refresh the user-level wisp.desktop launcher entry.

    Qt announces LINUX_DESKTOP_FILE_NAME as the app id on Linux; without a
    matching .desktop file the desktop portal logs "App info not found for
    'wisp'" and the taskbar cannot resolve Wisp's name or icon.
    """
    if not (platform or sys.platform).startswith("linux"):
        return None
    try:
        data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        target = data_home / "applications" / f"{LINUX_DESKTOP_FILE_NAME}.desktop"
        content = _linux_desktop_entry_content()
        try:
            if target.read_text(encoding="utf-8") == content:
                return target
        except OSError:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target
    except Exception:
        log.exception("Failed to install the Linux desktop entry")
        return None


def install_app_icon(app: Any, platform: str | None = None) -> Path | None:
    """Apply Wisp's app metadata and icon to a Qt application object."""
    platform = platform or sys.platform
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    if platform.startswith("linux") and hasattr(app, "setDesktopFileName"):
        app.setDesktopFileName(LINUX_DESKTOP_FILE_NAME)

    icon_path = app_icon_path(platform)
    if icon_path is None:
        return None

    try:
        from PySide6.QtGui import QIcon

        icon = QIcon(str(icon_path))
        if not icon.isNull():
            app.setWindowIcon(icon)
    except Exception:
        log.exception("Failed to apply Wisp application icon")
    return icon_path
