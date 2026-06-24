"""Release manifest checks and update downloads for Wisp."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from core.system.paths import UPDATE_DOWNLOAD_DIR

DEFAULT_MANIFEST_URL = (
    "https://github.com/SunnyLich/Python-AI-assistant-overlay/"
    "releases/latest/download/wisp-release-manifest.json"
)


class UpdateError(RuntimeError):
    """Raised when an update check or download cannot complete."""


@dataclass(frozen=True)
class UpdateAsset:
    """A downloadable update artifact for one platform."""

    platform_key: str
    name: str
    url: str
    sha256: str = ""
    size: int | None = None


@dataclass(frozen=True)
class UpdateCheckResult:
    """Result shown by the Settings update controls."""

    current_version: str
    latest_version: str
    update_available: bool
    asset: UpdateAsset | None
    notes_url: str = ""


def manifest_url() -> str:
    """Return the update manifest URL, allowing release-host overrides."""
    return os.environ.get("WISP_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL).strip()


def current_version() -> str:
    """Read the app version from package metadata or bundled pyproject.toml."""
    try:
        from importlib.metadata import version

        return version("wisp")
    except Exception:
        pass

    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "pyproject.toml")
    candidates.append(Path(__file__).resolve().parents[1] / "pyproject.toml")

    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', text)
        if match:
            return match.group(1)
    return "0.0.0"


def normalized_platform_key(
    sys_platform: str | None = None,
    machine: str | None = None,
) -> str:
    """Return the manifest platform key for the current host."""
    raw_platform = sys_platform or sys.platform
    raw_machine = (machine or platform.machine() or "").lower()
    if raw_machine in {"amd64", "x86_64"}:
        arch = "x64"
    elif raw_machine in {"aarch64", "arm64"}:
        arch = "arm64"
    else:
        arch = raw_machine or "unknown"

    if raw_platform == "win32":
        return f"windows-{arch}"
    if raw_platform == "darwin":
        return f"macos-{arch}"
    if raw_platform.startswith("linux"):
        return f"linux-{arch}"
    return f"{raw_platform}-{arch}"


def _version_parts(value: str) -> tuple[int, ...]:
    parts = []
    for part in re.split(r"[.+-]", value):
        if not part:
            continue
        match = re.match(r"(\d+)", part)
        parts.append(int(match.group(1)) if match else 0)
    return tuple(parts or [0])


def is_newer_version(candidate: str, current: str) -> bool:
    """Return True when candidate is newer than current."""
    left = list(_version_parts(candidate))
    right = list(_version_parts(current))
    width = max(len(left), len(right))
    left.extend([0] * (width - len(left)))
    right.extend([0] * (width - len(right)))
    return tuple(left) > tuple(right)


def parse_manifest(data: dict[str, Any], platform_key: str | None = None) -> tuple[str, str, UpdateAsset | None]:
    """Parse release manifest data into latest version, notes URL, and asset."""
    version = str(data.get("version") or "").strip()
    if not version:
        raise UpdateError("Update manifest is missing a version.")

    key = platform_key or normalized_platform_key()
    raw_assets = data.get("assets")
    if not isinstance(raw_assets, dict):
        raise UpdateError("Update manifest is missing assets.")

    raw_asset = raw_assets.get(key)
    if not isinstance(raw_asset, dict):
        return version, str(data.get("notes_url") or ""), None

    name = str(raw_asset.get("name") or "").strip()
    url = str(raw_asset.get("url") or "").strip()
    if not name or not url:
        raise UpdateError(f"Update asset for {key} is missing a name or URL.")

    size_raw = raw_asset.get("size")
    size = int(size_raw) if isinstance(size_raw, int) and size_raw >= 0 else None
    asset = UpdateAsset(
        platform_key=key,
        name=name,
        url=url,
        sha256=str(raw_asset.get("sha256") or "").lower(),
        size=size,
    )
    return version, str(data.get("notes_url") or ""), asset


def fetch_manifest(url: str | None = None, timeout: float = 15.0) -> dict[str, Any]:
    """Fetch and decode the release manifest."""
    target = url or manifest_url()
    if not target:
        raise UpdateError("Update manifest URL is not configured.")
    with urllib.request.urlopen(target, timeout=timeout) as response:
        payload = response.read()
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - surface malformed manifest details
        raise UpdateError(f"Update manifest is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UpdateError("Update manifest root must be an object.")
    return data


def check_for_updates(
    url: str | None = None,
    platform_key: str | None = None,
    installed_version: str | None = None,
) -> UpdateCheckResult:
    """Check the release manifest for a newer artifact."""
    local_version = installed_version or current_version()
    latest_version, notes_url, asset = parse_manifest(fetch_manifest(url), platform_key=platform_key)
    return UpdateCheckResult(
        current_version=local_version,
        latest_version=latest_version,
        update_available=is_newer_version(latest_version, local_version) and asset is not None,
        asset=asset,
        notes_url=notes_url,
    )


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def download_update(asset: UpdateAsset, target_dir: Path | None = None, timeout: float = 60.0) -> Path:
    """Download an update artifact and verify its SHA256 when provided."""
    destination_dir = target_dir or UPDATE_DOWNLOAD_DIR
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / asset.name
    with urllib.request.urlopen(asset.url, timeout=timeout) as response:
        with NamedTemporaryFile("wb", delete=False, dir=destination_dir, prefix=".wisp-update-", suffix=".tmp") as tmp:
            shutil.copyfileobj(response, tmp)
            tmp_path = Path(tmp.name)
    try:
        if asset.sha256:
            actual = _sha256(tmp_path)
            if actual.lower() != asset.sha256.lower():
                raise UpdateError("Downloaded update did not match the expected SHA256 hash.")
        tmp_path.replace(destination)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return destination
