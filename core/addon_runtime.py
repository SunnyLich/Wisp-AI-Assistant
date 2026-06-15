"""Per-addon dependency environment support."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.system.paths import REPO_ROOT


ADDON_ENVS_DIR = REPO_ROOT / "addon_envs"


@dataclass(frozen=True)
class AddonDependencies:
    python: str = ""
    packages: list[str] = field(default_factory=list)

    @property
    def has_dependencies(self) -> bool:
        return bool(self.python or self.packages)


def dependencies_from_manifest(raw: Any) -> AddonDependencies:
    if not isinstance(raw, dict):
        return AddonDependencies()
    packages = raw.get("packages") or []
    if isinstance(packages, str):
        packages = [packages]
    if not isinstance(packages, list):
        packages = []
    return AddonDependencies(
        python=str(raw.get("python") or "").strip(),
        packages=[str(item).strip() for item in packages if str(item).strip()],
    )


def env_path(addon_id: str) -> Path:
    return ADDON_ENVS_DIR / addon_id


def python_path(env_dir: Path) -> Path:
    if sys.platform == "win32":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def dependency_hash(deps: AddonDependencies) -> str:
    payload = {
        "python": deps.python,
        "packages": deps.packages,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def environment_status(addon_id: str, deps: AddonDependencies) -> dict[str, Any]:
    if not deps.has_dependencies:
        return {
            "tier": "1",
            "ready": True,
            "python": sys.executable,
            "env_path": "",
            "packages": [],
            "hash": "",
            "error": "",
        }

    root = env_path(addon_id)
    py = python_path(root)
    expected_hash = dependency_hash(deps)
    marker = _read_marker(root)
    ready = py.exists() and marker.get("hash") == expected_hash
    error = ""
    if not py.exists():
        error = "Dependency environment has not been installed yet."
    elif marker.get("hash") != expected_hash:
        error = "Dependency environment is out of date."
    return {
        "tier": "2",
        "ready": ready,
        "python": str(py),
        "env_path": str(root),
        "packages": list(deps.packages),
        "python_requirement": deps.python,
        "hash": expected_hash,
        "error": "" if ready else error,
    }


def provision_environment(addon_id: str, deps: AddonDependencies, *, force: bool = False) -> dict[str, Any]:
    if not deps.has_dependencies:
        return environment_status(addon_id, deps)

    root = env_path(addon_id)
    if force and root.exists():
        shutil.rmtree(root)

    status = environment_status(addon_id, deps)
    if status.get("ready"):
        return status

    root.parent.mkdir(parents=True, exist_ok=True)
    uv = _find_uv()
    if uv:
        python_spec = deps.python or f"{sys.version_info.major}.{sys.version_info.minor}"
        _run([uv, "venv", "--python", python_spec, str(root)])
        if deps.packages:
            _run([uv, "pip", "install", "--python", str(python_path(root)), *deps.packages])
    else:
        if getattr(sys, "frozen", False):
            raise RuntimeError("uv is required to create addon dependency environments in packaged builds.")
        _run([sys.executable, "-m", "venv", str(root)])
        if deps.packages:
            _run([str(python_path(root)), "-m", "pip", "install", *deps.packages])

    _write_marker(root, deps)
    return environment_status(addon_id, deps)


def _read_marker(root: Path) -> dict[str, Any]:
    marker = root / "addon-env.json"
    if not marker.exists():
        return {}
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_marker(root: Path, deps: AddonDependencies) -> None:
    marker = root / "addon-env.json"
    marker.write_text(
        json.dumps(
            {
                "hash": dependency_hash(deps),
                "python": deps.python,
                "packages": deps.packages,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _find_uv() -> str:
    found = shutil.which("uv")
    if found:
        return found
    suffix = ".exe" if sys.platform == "win32" else ""
    bundle_root = Path(getattr(sys, "_MEIPASS", REPO_ROOT))
    candidates = [
        bundle_root / "bin" / f"uv{suffix}",
        bundle_root / "uv" / f"uv{suffix}",
        bundle_root / f"uv{suffix}",
        Path.home() / ".local" / "bin" / f"uv{suffix}",
        Path.home() / ".cargo" / "bin" / f"uv{suffix}",
        REPO_ROOT / "bin" / f"uv{suffix}",
        REPO_ROOT / "tools" / f"uv{suffix}",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def _run(cmd: list[str]) -> None:
    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    try:
        subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "").strip()
        raise RuntimeError(output or f"command failed: {' '.join(cmd)}") from exc
