"""Release manifest checks and update downloads for Wisp."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
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


def _quoted_ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quoted_sh(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def install_root() -> Path:
    """Return the packaged app root that should be replaced by an update."""
    if not getattr(sys, "frozen", False):
        raise UpdateError("Automatic update apply is only available in packaged Wisp builds.")

    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent
    return executable.parent


def _archive_root_name(path: Path) -> str:
    lower = path.name.lower()
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return "Wisp"
    if lower.endswith(".zip"):
        try:
            with zipfile.ZipFile(path) as archive:
                roots = {
                    part
                    for name in archive.namelist()
                    if (part := name.strip("/").split("/", 1)[0])
                }
        except zipfile.BadZipFile as exc:
            raise UpdateError(f"Update archive is not a valid zip file: {exc}") from exc
        if len(roots) == 1:
            return next(iter(roots))
    return "Wisp.app" if sys.platform == "darwin" else "Wisp"


def _write_windows_apply_script(update_path: Path, root: Path, restart_target: Path, pid: int) -> Path:
    script_path = UPDATE_DOWNLOAD_DIR / f"apply-wisp-update-{pid}.ps1"
    archive_root = _archive_root_name(update_path)
    script = f"""$ErrorActionPreference = "Stop"
$pidToWait = {pid}
$archive = {_quoted_ps(str(update_path))}
$installRoot = {_quoted_ps(str(root))}
$restartTarget = {_quoted_ps(str(restart_target))}
$archiveRootName = {_quoted_ps(archive_root)}
$workRoot = Join-Path (Split-Path -LiteralPath $archive -Parent) ("apply-" + [guid]::NewGuid().ToString())
$extractRoot = Join-Path $workRoot "extract"
$backupRoot = "$installRoot.previous-update"

function Restore-Backup {{
    if ((Test-Path -LiteralPath $backupRoot) -and -not (Test-Path -LiteralPath $installRoot)) {{
        Rename-Item -LiteralPath $backupRoot -NewName (Split-Path -Leaf $installRoot)
    }}
}}

try {{
    New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
    try {{ Wait-Process -Id $pidToWait -Timeout 90 -ErrorAction SilentlyContinue }} catch {{ }}
    Expand-Archive -LiteralPath $archive -DestinationPath $extractRoot -Force
    $candidate = Join-Path $extractRoot $archiveRootName
    if (-not (Test-Path -LiteralPath $candidate)) {{
        $dirs = @(Get-ChildItem -LiteralPath $extractRoot -Directory)
        if ($dirs.Count -eq 1) {{
            $candidate = $dirs[0].FullName
        }} else {{
            throw "Could not find the extracted Wisp app folder."
        }}
    }}
    if (Test-Path -LiteralPath $backupRoot) {{
        Remove-Item -LiteralPath $backupRoot -Recurse -Force
    }}
    Rename-Item -LiteralPath $installRoot -NewName (Split-Path -Leaf $backupRoot)
    Move-Item -LiteralPath $candidate -Destination $installRoot
    Start-Process -FilePath $restartTarget -WorkingDirectory (Split-Path -LiteralPath $restartTarget -Parent)
    Start-Sleep -Seconds 5
    Remove-Item -LiteralPath $backupRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
}} catch {{
    Restore-Backup
    $log = Join-Path (Split-Path -LiteralPath $archive -Parent) "apply-update-error.log"
    $_ | Out-String | Set-Content -LiteralPath $log
    exit 1
}}
"""
    script_path.write_text(script, encoding="utf-8")
    return script_path


def _write_posix_apply_script(update_path: Path, root: Path, restart_target: Path, pid: int) -> Path:
    script_path = UPDATE_DOWNLOAD_DIR / f"apply-wisp-update-{pid}.sh"
    archive_root = _archive_root_name(update_path)
    opener = f"open {_quoted_sh(str(root))}" if sys.platform == "darwin" else f"{_quoted_sh(str(restart_target))} >/dev/null 2>&1 &"
    if update_path.name.lower().endswith((".tar.gz", ".tgz")):
        extract_command = 'tar -xzf "$archive" -C "$extract_root"'
    else:
        extract_command = 'if command -v ditto >/dev/null 2>&1; then ditto -x -k "$archive" "$extract_root"; else unzip -q "$archive" -d "$extract_root"; fi'
    script = f"""#!/bin/sh
set -eu
pid_to_wait={pid}
archive={_quoted_sh(str(update_path))}
install_root={_quoted_sh(str(root))}
restart_target={_quoted_sh(str(restart_target))}
archive_root_name={_quoted_sh(archive_root)}
work_root="$(dirname "$archive")/apply-$(date +%s)-$$"
extract_root="$work_root/extract"
backup_root="$install_root.previous-update"

restore_backup() {{
    if [ -d "$backup_root" ] && [ ! -e "$install_root" ]; then
        mv "$backup_root" "$install_root"
    fi
}}

trap 'rc=$?; if [ "$rc" -ne 0 ]; then restore_backup; echo "Wisp update apply failed." > "$(dirname "$archive")/apply-update-error.log"; fi; exit "$rc"' EXIT
mkdir -p "$extract_root"
while kill -0 "$pid_to_wait" 2>/dev/null; do
    sleep 1
done
{extract_command}
candidate="$extract_root/$archive_root_name"
if [ ! -e "$candidate" ]; then
    candidate="$(find "$extract_root" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
fi
if [ -z "$candidate" ] || [ ! -e "$candidate" ]; then
    echo "Could not find the extracted Wisp app folder." > "$(dirname "$archive")/apply-update-error.log"
    exit 1
fi
rm -rf "$backup_root"
mv "$install_root" "$backup_root"
mv "$candidate" "$install_root"
{opener}
sleep 5
rm -rf "$backup_root" "$work_root"
"""
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o700)
    return script_path


def apply_update(update_path: Path, pid: int | None = None) -> Path:
    """Start a detached helper that applies an update after Wisp exits."""
    update_path = Path(update_path).resolve()
    if not update_path.exists():
        raise UpdateError("Downloaded update file is no longer available.")
    root = install_root()
    restart_target = Path(sys.executable).resolve()
    wait_pid = pid or os.getpid()
    UPDATE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        script_path = _write_windows_apply_script(update_path, root, restart_target, wait_pid)
        creationflags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creationflags |= subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    else:
        script_path = _write_posix_apply_script(update_path, root, restart_target, wait_pid)
        subprocess.Popen(
            [str(script_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    return script_path
