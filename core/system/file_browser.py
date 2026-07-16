"""Open files and folders in the platform's native file browser."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def reveal_path(path: str | Path, *, platform: str | None = None):
    """Reveal *path* in Explorer/Finder, or open its folder on Linux."""
    target = Path(path).expanduser().resolve()
    current_platform = platform or sys.platform
    is_file = target.is_file()

    if current_platform == "win32":
        command = (
            ["explorer.exe", f"/select,{target}"]
            if is_file
            else ["explorer.exe", str(target)]
        )
    elif current_platform == "darwin":
        command = ["open", "-R", str(target)] if is_file else ["open", str(target)]
    else:
        command = ["xdg-open", str(target.parent if is_file else target)]
    return subprocess.Popen(command)
