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
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any

from core.system.paths import SINGLE_INSTANCE_LOCK, UPDATE_DOWNLOAD_DIR

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


@dataclass(frozen=True)
class RepoUpdateResult:
    """Result from fast-forwarding a source checkout."""

    repo_root: Path
    before: str
    after: str
    updated: bool
    output: str = ""


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


def source_checkout_root() -> Path:
    """Return the repository root for a source checkout."""
    return Path(__file__).resolve().parents[1]


def is_repo_checkout(root: Path | None = None) -> bool:
    """Return True when Wisp is running from an editable git checkout."""
    if getattr(sys, "frozen", False):
        return False
    candidate = Path(root) if root is not None else source_checkout_root()
    return (candidate / ".git").exists()


def _run_git(root: Path, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise UpdateError("Git is not available on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("Git update timed out.") from exc


def _git_stdout(root: Path, args: list[str], timeout: float) -> str:
    completed = _run_git(root, args, timeout)
    if completed.returncode == 0:
        return str(completed.stdout or "").strip()
    message = str(completed.stderr or completed.stdout or "").strip()
    raise UpdateError(message or f"git {' '.join(args)} failed with exit code {completed.returncode}.")


@dataclass(frozen=True)
class _StatusEntry:
    status: str
    path: str


def _git_status_entries(root: Path, timeout: float) -> list[_StatusEntry]:
    """Return parsed porcelain status entries."""
    completed = _run_git(root, ["status", "--porcelain", "-z"], timeout)
    if completed.returncode != 0:
        message = str(completed.stderr or completed.stdout or "").strip()
        raise UpdateError(message or "Could not read git status before updating.")
    output = str(completed.stdout or "")
    if not output:
        return []
    raw_entries = output.split("\0")
    entries: list[_StatusEntry] = []
    index = 0
    while index < len(raw_entries):
        raw = raw_entries[index]
        index += 1
        if not raw:
            continue
        if len(raw) < 4:
            entries.append(_StatusEntry(raw[:2], ""))
            continue
        status = raw[:2]
        path = raw[3:].rstrip("\n")
        entries.append(_StatusEntry(status, path))
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            index += 1
    return entries


def _allowed_repo_update_dirty_path(path: str) -> bool:
    """Return whether a local path is user state that should survive repo pulls."""
    normalized = path.replace("\\", "/").lstrip("/")
    if not normalized or normalized.startswith("../"):
        return False
    if normalized == "addons.json":
        return True
    if normalized.startswith((".env", "addon_data/", "memory/", "chats/", "model_files/", "private/")):
        return normalized != ".env.example"
    if normalized == "addons/mcp_bridge/servers.json":
        return True
    if normalized.startswith("addons/"):
        parts = normalized.split("/")
        return len(parts) >= 2 and parts[1] not in {"mcp_bridge", "ui_lab"} and parts[1] != "README.md"
    return False


def _copy_path(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    elif src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _restore_preserved_paths(repo: Path, backup_root: Path, paths: list[str]) -> None:
    for rel in paths:
        backup_path = backup_root / rel
        target = repo / rel
        if not backup_path.exists():
            continue
        _remove_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy_path(backup_path, target)


def _preserve_user_state_for_repo_update(
    repo: Path,
    entries: list[_StatusEntry],
    backup_root: Path,
    timeout: float,
) -> list[str]:
    """Back up allowed dirty user-state paths and clear them for a git pull."""
    preserved: list[str] = []
    tracked_to_restore: list[str] = []
    for entry in entries:
        path = entry.path
        if not path or not _allowed_repo_update_dirty_path(path):
            raise UpdateError(
                "Repo update stopped because this checkout has local changes in app code. "
                "Wisp can preserve settings and addon data automatically, but code changes must be committed or stashed."
            )
        source = repo / path
        if source.exists():
            _copy_path(source, backup_root / path)
            preserved.append(path)
        if entry.status != "??":
            tracked_to_restore.append(path)

    if tracked_to_restore:
        restore = _run_git(repo, ["checkout", "--", *tracked_to_restore], timeout)
        if restore.returncode != 0:
            message = str(restore.stderr or restore.stdout or "").strip()
            raise UpdateError(message or "Could not temporarily restore tracked user-state files before updating.")

    for entry in entries:
        if entry.status == "??" and entry.path:
            _remove_path(repo / entry.path)
    return preserved


def apply_repo_update(
    root: Path | None = None,
    *,
    remote: str = "origin",
    branch: str = "main",
    timeout: float = 120.0,
) -> RepoUpdateResult:
    """Fast-forward a clean source checkout from origin/main."""
    repo = (Path(root) if root is not None else source_checkout_root()).resolve()
    if not is_repo_checkout(repo):
        raise UpdateError("Repo update is only available from a git checkout.")

    current_branch = _git_stdout(repo, ["rev-parse", "--abbrev-ref", "HEAD"], timeout)
    if current_branch == "HEAD":
        raise UpdateError("Repo update is not available while HEAD is detached.")
    if current_branch != branch:
        raise UpdateError(
            f"Repo update is only available on the {branch} branch (current branch: {current_branch})."
        )

    entries = _git_status_entries(repo, timeout)
    with TemporaryDirectory(prefix="wisp-repo-update-") as tmp:
        backup_root = Path(tmp)
        preserved = _preserve_user_state_for_repo_update(repo, entries, backup_root, timeout) if entries else []
        try:
            before = _git_stdout(repo, ["rev-parse", "HEAD"], timeout)
            pull = _run_git(repo, ["pull", "--ff-only", remote, branch], timeout)
            output = "\n".join(part for part in (str(pull.stdout or "").strip(), str(pull.stderr or "").strip()) if part)
            if pull.returncode != 0:
                raise UpdateError(output or f"git pull --ff-only {remote} {branch} failed with exit code {pull.returncode}.")
            after = _git_stdout(repo, ["rev-parse", "HEAD"], timeout)
        finally:
            if preserved:
                _restore_preserved_paths(repo, backup_root, preserved)
    return RepoUpdateResult(repo_root=repo, before=before, after=after, updated=before != after, output=output)


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
$singleInstanceLock = {_quoted_ps(str(SINGLE_INSTANCE_LOCK))}
$archiveRootName = {_quoted_ps(archive_root)}
$archiveParent = [System.IO.Path]::GetDirectoryName($archive)
$restartParent = [System.IO.Path]::GetDirectoryName($restartTarget)
$installRootLeaf = [System.IO.Path]::GetFileName($installRoot)
$workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("WispUpdate-" + [guid]::NewGuid().ToString("N"))
$extractRoot = Join-Path $workRoot "extract"
$backupRoot = "$installRoot.previous-update"
$backupRootLeaf = [System.IO.Path]::GetFileName($backupRoot)

function Restore-Backup {{
    if ((Test-Path -LiteralPath $backupRoot) -and -not (Test-Path -LiteralPath $installRoot)) {{
        Rename-Item -LiteralPath $backupRoot -NewName $installRootLeaf
    }}
}}

function Initialize-InstallerUi {{
    try {{
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $script:form = New-Object System.Windows.Forms.Form
        $script:form.Text = "Wisp Update"
        $script:form.Width = 440
        $script:form.Height = 190
        $script:form.StartPosition = "CenterScreen"
        $script:form.FormBorderStyle = "FixedDialog"
        $script:form.MaximizeBox = $false
        $script:form.MinimizeBox = $true
        $script:form.TopMost = $true
        try {{
            $script:form.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon($restartTarget)
        }} catch {{ }}

        $title = New-Object System.Windows.Forms.Label
        $title.Text = "Updating Wisp"
        $title.Font = New-Object System.Drawing.Font($title.Font.FontFamily, 12, [System.Drawing.FontStyle]::Bold)
        $title.Left = 20
        $title.Top = 18
        $title.Width = 380
        $title.Height = 26
        $script:form.Controls.Add($title)

        $script:statusLabel = New-Object System.Windows.Forms.Label
        $script:statusLabel.Text = "Preparing update..."
        $script:statusLabel.Left = 20
        $script:statusLabel.Top = 54
        $script:statusLabel.Width = 390
        $script:statusLabel.Height = 34
        $script:form.Controls.Add($script:statusLabel)

        $script:progress = New-Object System.Windows.Forms.ProgressBar
        $script:progress.Left = 20
        $script:progress.Top = 95
        $script:progress.Width = 390
        $script:progress.Height = 18
        $script:progress.Style = "Marquee"
        $script:progress.MarqueeAnimationSpeed = 35
        $script:form.Controls.Add($script:progress)

        $script:closeButton = New-Object System.Windows.Forms.Button
        $script:closeButton.Text = "Close"
        $script:closeButton.Left = 320
        $script:closeButton.Top = 122
        $script:closeButton.Width = 90
        $script:closeButton.Visible = $false
        $script:closeButton.Add_Click({{ $script:form.Close() }})
        $script:form.Controls.Add($script:closeButton)

        $script:form.Show()
        [System.Windows.Forms.Application]::DoEvents()
    }} catch {{
        $script:form = $null
    }}
}}

function Update-InstallerStatus {{
    param([string]$Message)
    try {{
        if ($script:statusLabel -ne $null) {{
            $script:statusLabel.Text = $Message
            [System.Windows.Forms.Application]::DoEvents()
        }}
    }} catch {{ }}
}}

function Finish-InstallerUi {{
    param(
        [string]$Message,
        [bool]$Failed
    )
    try {{
        if ($script:form -eq $null) {{
            return
        }}
        if ($script:progress -ne $null) {{
            $script:progress.Style = "Blocks"
            $script:progress.MarqueeAnimationSpeed = 0
            $script:progress.Value = $(if ($Failed) {{ 0 }} else {{ 100 }})
        }}
        if ($script:statusLabel -ne $null) {{
            $script:statusLabel.Text = $Message
        }}
        if ($Failed) {{
            $script:form.TopMost = $false
            $script:closeButton.Visible = $true
            $script:form.Activate()
            while ($script:form.Visible) {{
                [System.Windows.Forms.Application]::DoEvents()
                Start-Sleep -Milliseconds 100
            }}
        }} else {{
            [System.Windows.Forms.Application]::DoEvents()
            Start-Sleep -Milliseconds 500
            $script:form.Close()
        }}
    }} catch {{ }}
}}

function Test-WispLockReleased {{
    $stream = $null
    try {{
        $parent = [System.IO.Path]::GetDirectoryName($singleInstanceLock)
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {{
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }}
        $stream = [System.IO.File]::Open($singleInstanceLock, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::ReadWrite)
        $stream.Lock(0, 1)
        $stream.Unlock(0, 1)
        return $true
    }} catch {{
        return $false
    }} finally {{
        if ($stream -ne $null) {{
            $stream.Dispose()
        }}
    }}
}}

function Wait-For-WispExit {{
    Update-InstallerStatus "Waiting for Wisp to close..."
    try {{ Wait-Process -Id $pidToWait -Timeout 90 -ErrorAction SilentlyContinue }} catch {{ }}
    $deadline = (Get-Date).AddMinutes(5)
    while ((Get-Date) -lt $deadline) {{
        if (Test-WispLockReleased) {{
            Start-Sleep -Seconds 1
            return
        }}
        Start-Sleep -Seconds 1
    }}
    throw "Timed out waiting for Wisp to exit before applying the update."
}}

function Find-NewVersionHelper {{
    param([string]$CandidateRoot)
    $relativePaths = @(
        "_internal\\assets\\updater\\windows_apply_update.ps1",
        "assets\\updater\\windows_apply_update.ps1",
        "updater\\windows_apply_update.ps1"
    )
    foreach ($relativePath in $relativePaths) {{
        $helper = Join-Path $CandidateRoot $relativePath
        if (Test-Path -LiteralPath $helper -PathType Leaf) {{
            return $helper
        }}
    }}
    return $null
}}

function Invoke-NewVersionHelper {{
    param(
        [string]$Helper,
        [string]$Candidate
    )
    $delegatedHelper = Join-Path $archiveParent ("apply-new-wisp-update-" + [guid]::NewGuid().ToString() + ".ps1")
    Copy-Item -LiteralPath $Helper -Destination $delegatedHelper -Force
    Update-InstallerStatus "Starting the newer installer..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File $delegatedHelper `
        -Archive $archive `
        -InstallRoot $installRoot `
        -Candidate $Candidate `
        -RestartTarget $restartTarget `
        -BackupRoot $backupRoot `
        -WorkRoot $workRoot `
        -SingleInstanceLock $singleInstanceLock
    $exitCode = $LASTEXITCODE
    Remove-Item -LiteralPath $delegatedHelper -Force -ErrorAction SilentlyContinue
    if ($exitCode -ne 0) {{
        throw "The newer Wisp installer failed with exit code $exitCode."
    }}
}}

Initialize-InstallerUi

try {{
    Update-InstallerStatus "Preparing update files..."
    New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
    Wait-For-WispExit
    Update-InstallerStatus "Extracting the downloaded update..."
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($archive, $extractRoot)
    $candidate = Join-Path $extractRoot $archiveRootName
    if (-not (Test-Path -LiteralPath $candidate)) {{
        $dirs = @(Get-ChildItem -LiteralPath $extractRoot -Directory)
        if ($dirs.Count -eq 1) {{
            $candidate = $dirs[0].FullName
        }} else {{
            throw "Could not find the extracted Wisp app folder."
        }}
    }}
    $newVersionHelper = Find-NewVersionHelper $candidate
    if ($newVersionHelper) {{
        Invoke-NewVersionHelper -Helper $newVersionHelper -Candidate $candidate
        Finish-InstallerUi "Wisp has been updated and reopened." $false
        exit 0
    }}
    Update-InstallerStatus "Replacing the old Wisp files..."
    if (Test-Path -LiteralPath $backupRoot) {{
        Remove-Item -LiteralPath $backupRoot -Recurse -Force
    }}
    Rename-Item -LiteralPath $installRoot -NewName $backupRootLeaf
    Move-Item -LiteralPath $candidate -Destination $installRoot
    Update-InstallerStatus "Reopening Wisp..."
    Start-Process -FilePath $restartTarget -WorkingDirectory $restartParent
    Start-Sleep -Seconds 5
    Remove-Item -LiteralPath $backupRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
    Finish-InstallerUi "Wisp has been updated and reopened." $false
}} catch {{
    Restore-Backup
    $log = Join-Path $archiveParent "apply-update-error.log"
    $_ | Out-String | Set-Content -LiteralPath $log
    Finish-InstallerUi "Wisp update failed. Details were saved to $log" $true
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
single_instance_lock={_quoted_sh(str(SINGLE_INSTANCE_LOCK))}
archive_root_name={_quoted_sh(archive_root)}
work_root="$(dirname "$archive")/apply-$(date +%s)-$$"
extract_root="$work_root/extract"
backup_root="$install_root.previous-update"
error_log="$(dirname "$archive")/apply-update-error.log"
ui_pipe=""
ui_pid=""
ui_fd_open=0

restore_backup() {{
    if [ -d "$backup_root" ] && [ ! -e "$install_root" ]; then
        mv "$backup_root" "$install_root"
    fi
}}

start_installer_ui() {{
    if [ -z "${{DISPLAY:-}}" ] && [ -z "${{WAYLAND_DISPLAY:-}}" ]; then
        return 0
    fi
    if [ "$(uname -s 2>/dev/null || true)" = "Darwin" ]; then
        return 0
    fi
    if command -v zenity >/dev/null 2>&1 && command -v mkfifo >/dev/null 2>&1; then
        mkdir -p "$work_root"
        ui_pipe="$work_root/update-ui.pipe"
        rm -f "$ui_pipe"
        if mkfifo "$ui_pipe"; then
            zenity --progress --pulsate --no-cancel --auto-close --title="Wisp Update" --text="Preparing update..." < "$ui_pipe" >/dev/null 2>&1 &
            ui_pid=$!
            if exec 3<>"$ui_pipe"; then
                ui_fd_open=1
            fi
        fi
    elif command -v kdialog >/dev/null 2>&1; then
        kdialog --title "Wisp Update" --passivepopup "Updating Wisp. Wisp will reopen when the update finishes." 30 >/dev/null 2>&1 &
        ui_pid=$!
    elif command -v xmessage >/dev/null 2>&1; then
        xmessage -center -buttons "" "Updating Wisp. Wisp will reopen when the update finishes." >/dev/null 2>&1 &
        ui_pid=$!
    fi
}}

update_installer_status() {{
    if [ "$ui_fd_open" = "1" ]; then
        {{ printf '# %s\n' "$1" >&3; }} 2>/dev/null || true
    fi
}}

finish_installer_ui() {{
    message="$1"
    failed="$2"
    if [ "$ui_fd_open" = "1" ]; then
        if [ "$failed" = "1" ]; then
            {{ printf '# %s\n' "$message" >&3; }} 2>/dev/null || true
        else
            {{ printf '100\n# %s\n' "$message" >&3; }} 2>/dev/null || true
        fi
        exec 3>&- 2>/dev/null || true
        ui_fd_open=0
    fi
    if [ -n "$ui_pid" ] && kill -0 "$ui_pid" 2>/dev/null; then
        if [ "$failed" = "1" ]; then
            kill "$ui_pid" 2>/dev/null || true
        else
            sleep 0.5
        fi
    fi
    if [ "$failed" = "1" ]; then
        if command -v zenity >/dev/null 2>&1; then
            zenity --error --title="Wisp Update" --text="$message" >/dev/null 2>&1 || true
        elif command -v kdialog >/dev/null 2>&1; then
            kdialog --title "Wisp Update" --error "$message" >/dev/null 2>&1 || true
        elif command -v xmessage >/dev/null 2>&1; then
            xmessage -center "$message" >/dev/null 2>&1 || true
        fi
    fi
}}

wait_for_wisp_exit() {{
    update_installer_status "Waiting for Wisp to close..."
    while kill -0 "$pid_to_wait" 2>/dev/null; do
        sleep 1
    done
    if command -v flock >/dev/null 2>&1; then
        deadline=$(( $(date +%s) + 300 ))
        while [ "$(date +%s)" -lt "$deadline" ]; do
            mkdir -p "$(dirname "$single_instance_lock")"
            if (
                flock -n 9
            ) 9>"$single_instance_lock"; then
                sleep 1
                return 0
            fi
            sleep 1
        done
        echo "Timed out waiting for Wisp to exit before applying the update." > "$error_log"
        exit 1
    fi
}}

trap 'rc=$?; if [ "$rc" -ne 0 ]; then restore_backup; if [ ! -s "$error_log" ]; then echo "Wisp update apply failed." > "$error_log"; fi; finish_installer_ui "Wisp update failed. Details were saved to $error_log" 1; fi; exit "$rc"' EXIT
start_installer_ui
mkdir -p "$extract_root"
wait_for_wisp_exit
update_installer_status "Extracting the downloaded update..."
{extract_command}
candidate="$extract_root/$archive_root_name"
if [ ! -e "$candidate" ]; then
    candidate="$(find "$extract_root" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
fi
if [ -z "$candidate" ] || [ ! -e "$candidate" ]; then
    echo "Could not find the extracted Wisp app folder." > "$error_log"
    exit 1
fi
update_installer_status "Replacing the old Wisp files..."
rm -rf "$backup_root"
mv "$install_root" "$backup_root"
mv "$candidate" "$install_root"
update_installer_status "Reopening Wisp..."
{opener}
sleep 5
rm -rf "$backup_root" "$work_root"
finish_installer_ui "Wisp has been updated and reopened." 0
"""
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o700)
    return script_path


def _update_wait_pid(pid: int | None = None) -> int:
    """Return the process the helper should wait for before replacing files."""
    if pid is not None:
        return pid
    supervisor_pid = str(os.environ.get("WISP_SUPERVISOR_PID") or "").strip()
    if supervisor_pid.isdigit() and int(supervisor_pid) > 0:
        return int(supervisor_pid)
    return os.getpid()


def apply_update(update_path: Path, pid: int | None = None) -> Path:
    """Start a detached helper that applies an update after Wisp exits."""
    update_path = Path(update_path).resolve()
    if not update_path.exists():
        raise UpdateError("Downloaded update file is no longer available.")
    root = install_root()
    restart_target = Path(sys.executable).resolve()
    wait_pid = _update_wait_pid(pid)
    UPDATE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        script_path = _write_windows_apply_script(update_path, root, restart_target, wait_pid)
        creationflags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-STA",
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
