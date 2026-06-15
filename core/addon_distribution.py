"""Install addon packages from folder or zip-style archives."""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from core.system.paths import ADDONS_DIR


def install_addon_archive(archive_path: Path, addons_dir: Path | None = None, *, replace: bool = False) -> dict[str, Any]:
    archive_path = Path(archive_path)
    if archive_path.suffix.lower() not in {".zip", ".wisp"}:
        raise ValueError("addon archive must be a .zip or .wisp file")
    if not archive_path.exists():
        raise FileNotFoundError(str(archive_path))

    target_root = addons_dir or ADDONS_DIR
    target_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wisp-addon-") as tmp:
        tmp_root = Path(tmp)
        with zipfile.ZipFile(archive_path) as zf:
            _safe_extract(zf, tmp_root)
        source = _find_addon_root(tmp_root)
        addon_id = _manifest_id(source)
        target = target_root / addon_id
        if target.exists():
            if not replace:
                raise FileExistsError(f"addon already exists: {addon_id}")
            shutil.rmtree(target)
        shutil.copytree(source, target)
    return {"id": addon_id, "path": str(target)}


def install_addon_folder(folder: Path, addons_dir: Path | None = None, *, replace: bool = False) -> dict[str, Any]:
    source = _find_addon_root(Path(folder))
    addon_id = _manifest_id(source)
    target_root = addons_dir or ADDONS_DIR
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / addon_id
    if target.exists():
        if not replace:
            raise FileExistsError(f"addon already exists: {addon_id}")
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return {"id": addon_id, "path": str(target)}


def _safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    dest = destination.resolve()
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if not name or name.startswith("/") or ".." in Path(name).parts:
            raise ValueError(f"unsafe archive path: {info.filename}")
        target = (destination / name).resolve()
        if dest not in target.parents and target != dest:
            raise ValueError(f"unsafe archive path: {info.filename}")
    zf.extractall(destination)


def _find_addon_root(root: Path) -> Path:
    if (root / "addon.toml").exists() or (root / "plugin.toml").exists():
        return root
    candidates = [
        child
        for child in root.iterdir()
        if child.is_dir() and ((child / "addon.toml").exists() or (child / "plugin.toml").exists())
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise FileNotFoundError("archive does not contain exactly one addon.toml/plugin.toml")


def _manifest_id(root: Path) -> str:
    from core.addon_manager import load_manifest

    return load_manifest(root).id
