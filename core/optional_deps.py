"""Runtime-installable optional Python dependencies."""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import site
import sys
from pathlib import Path

from core.system.paths import REPO_ROOT


OPTIONAL_PACKAGES_DIR = REPO_ROOT / "python_packages"


def _is_frozen() -> bool:
    """Return whether Wisp is running from a packaged executable."""
    return bool(getattr(sys, "frozen", False))


def _find_uv() -> str:
    """Find a uv executable usable for packaged optional dependency installs."""
    suffix = ".exe" if sys.platform == "win32" else ""
    candidates: list[Path] = []
    bundle_root = Path(getattr(sys, "_MEIPASS", REPO_ROOT))
    candidates.extend([
        bundle_root / "bin" / f"uv{suffix}",
        bundle_root / "uv" / f"uv{suffix}",
        bundle_root / f"uv{suffix}",
    ])
    if _is_frozen():
        exe_root = Path(sys.executable).resolve().parent
        candidates.extend([
            exe_root / "bin" / f"uv{suffix}",
            exe_root / "_internal" / "bin" / f"uv{suffix}",
            exe_root / f"uv{suffix}",
        ])
    candidates.extend([
        REPO_ROOT / "bin" / f"uv{suffix}",
        REPO_ROOT / "tools" / f"uv{suffix}",
    ])
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("uv") or ""


def add_optional_packages_to_path() -> None:
    """Make user-installed optional packages importable in the current process."""
    path = str(OPTIONAL_PACKAGES_DIR)
    if path in sys.path:
        return
    try:
        OPTIONAL_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        site.addsitedir(path)
    except Exception:
        sys.path.insert(0, path)


def is_importable(module_name: str) -> bool:
    """Return whether an optional dependency module can be imported."""
    add_optional_packages_to_path()
    importlib.invalidate_caches()
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def pip_install_command(packages: list[str]) -> list[str]:
    """Return a command that installs packages into Wisp's optional dir."""
    if _is_frozen():
        uv = _find_uv()
        if not uv:
            raise RuntimeError(
                "Packaged Wisp installs optional packages with uv, but uv was not bundled. "
                "Place uv.exe at bin\\uv.exe or tools\\uv.exe before building, then rebuild Wisp."
            )
        return [
            uv,
            "pip",
            "install",
            "--color",
            "never",
            "--link-mode",
            "copy",
            "--python-version",
            f"{sys.version_info.major}.{sys.version_info.minor}",
            "--target",
            str(OPTIONAL_PACKAGES_DIR),
            *packages,
        ]
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--progress-bar=raw",
        "--target",
        str(OPTIONAL_PACKAGES_DIR),
        *packages,
    ]


def pip_install_env() -> dict[str, str]:
    """Return an environment for optional dependency installs."""
    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    return env
