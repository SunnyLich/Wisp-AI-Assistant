"""Conservative, detached Wisp uninstaller for source and packaged builds."""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from core.system.paths import USER_DATA_DIR


class UninstallError(RuntimeError):
    """Raised when a safe uninstall plan cannot be created or launched."""


_WISP_MODEL_CACHE_NAMES = {
    "models--hexgrad--Kokoro-82M",
    "models--Systran--faster-whisper-tiny",
    "models--Systran--faster-whisper-base",
    "models--Systran--faster-whisper-small",
    "models--Systran--faster-whisper-medium",
    "models--Systran--faster-whisper-large-v3",
}
_KEYRING_SERVICE = "python-ai-overlay"
_CHATGPT_KEYRING_CHUNKS = tuple(f"chatgpt-oauth-chunk-{index}" for index in range(32))
_KEYRING_ACCOUNTS = (
    "__wisp_secrets__",
    "chatgpt-oauth",
    "github-oauth",
    "github-copilot-token",
) + _CHATGPT_KEYRING_CHUNKS


@dataclass(frozen=True)
class UninstallPlan:
    """Validated paths owned by one complete Wisp uninstall."""

    platform: str
    source_checkout: bool
    app_root: Path
    user_data_root: Path
    targets: tuple[Path, ...]


@dataclass(frozen=True)
class UninstallLaunch:
    """Detached helper artifacts created for an uninstall."""

    script_path: Path
    failure_log_path: Path


def _absolute(path: Path | str) -> Path:
    return Path(path).expanduser().absolute()


def _assert_safe_root(path: Path, *, label: str, home: Path) -> None:
    if not path.is_absolute():
        raise UninstallError(f"{label} is not absolute: {path}")
    if path == Path(path.anchor):
        raise UninstallError(f"Refusing to uninstall a filesystem root: {path}")
    if path == home:
        raise UninstallError(f"Refusing to uninstall the user home directory: {path}")


def _packaged_root(executable: Path, platform: str) -> Path:
    executable = executable.resolve()
    if platform == "darwin":
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent
        raise UninstallError(f"Packaged macOS Wisp is not inside an app bundle: {executable}")
    return executable.parent


def _validate_source_root(root: Path) -> None:
    required = (
        root / "pyproject.toml",
        root / "runtime" / "supervisor" / "app.py",
        root / "core" / "system" / "paths.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise UninstallError("Refusing to remove an unrecognized source directory: " + ", ".join(missing))


def _huggingface_hub_root(environ: Mapping[str, str], home: Path) -> Path:
    if value := str(environ.get("HF_HUB_CACHE") or "").strip():
        return _absolute(value)
    if value := str(environ.get("HF_HOME") or "").strip():
        return _absolute(Path(value) / "hub")
    if value := str(environ.get("XDG_CACHE_HOME") or "").strip():
        return _absolute(Path(value) / "huggingface" / "hub")
    return _absolute(home / ".cache" / "huggingface" / "hub")


def _model_cache_targets(hub_root: Path) -> list[Path]:
    """Return exact Wisp speech repositories without touching shared cache data."""
    targets: list[Path] = []
    for name in sorted(_WISP_MODEL_CACHE_NAMES):
        repo = hub_root / name
        if repo.exists() or repo.is_symlink():
            targets.append(repo)
        lock_repo = hub_root / ".locks" / name
        if lock_repo.exists() or lock_repo.is_symlink():
            targets.append(lock_repo)
    return targets


def _integration_targets(platform: str, home: Path, environ: Mapping[str, str]) -> list[Path]:
    if platform == "darwin":
        return [home / "Library" / "LaunchAgents" / "com.wisp.launcher.plist"]
    if platform.startswith("linux"):
        config_home = _absolute(environ.get("XDG_CONFIG_HOME") or home / ".config")
        data_home = _absolute(environ.get("XDG_DATA_HOME") or home / ".local" / "share")
        return [
            config_home / "autostart" / "wisp.desktop",
            data_home / "applications" / "wisp.desktop",
        ]
    return []


def _collapse_targets(paths: list[Path], *, platform: str) -> tuple[Path, ...]:
    """Deduplicate targets and omit children already covered by an owned root."""
    unique: dict[str, Path] = {}
    for raw in paths:
        path = _absolute(raw)
        key = str(path).casefold() if platform == "win32" else str(path)
        unique[key] = path
    collapsed: list[Path] = []
    for candidate in sorted(unique.values(), key=lambda item: (len(item.parts), str(item))):
        if any(candidate == parent or candidate.is_relative_to(parent) for parent in collapsed):
            continue
        collapsed.append(candidate)
    return tuple(collapsed)


def build_uninstall_plan(
    *,
    platform: str | None = None,
    frozen: bool | None = None,
    executable: Path | str | None = None,
    source_root: Path | str | None = None,
    user_data_root: Path | str | None = None,
    optional_packages_root: Path | str | None = None,
    home: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> UninstallPlan:
    """Build a deletion plan containing only paths Wisp owns."""
    from core import optional_deps, updater

    platform = platform or sys.platform
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    executable_path = Path(executable or sys.executable)
    home_path = _absolute(home or Path.home())
    data_root = _absolute(user_data_root or USER_DATA_DIR)
    _assert_safe_root(data_root, label="Wisp user-data root", home=home_path)
    if data_root.name.casefold() != "wisp":
        raise UninstallError(f"Refusing to remove an unexpected Wisp user-data directory: {data_root}")

    if frozen:
        app_root = _packaged_root(executable_path, platform)
        executable_resolved = executable_path.resolve()
        if not executable_resolved.is_relative_to(app_root.resolve()):
            raise UninstallError(f"Packaged executable is outside its install root: {executable_resolved}")
    else:
        app_root = _absolute(source_root or updater.source_checkout_root())
        _validate_source_root(app_root)
    _assert_safe_root(app_root, label="Wisp app root", home=home_path)

    env = dict(os.environ if environ is None else environ)
    hub_root = _huggingface_hub_root(env, home_path)
    optional_root = _absolute(optional_packages_root or optional_deps.OPTIONAL_PACKAGES_DIR)
    _assert_safe_root(optional_root, label="Wisp optional-package root", home=home_path)
    if not optional_root.is_relative_to(data_root) and optional_root.name != "python_packages":
        raise UninstallError(f"Refusing to remove an unexpected optional-package directory: {optional_root}")
    targets = [app_root, data_root, optional_root]
    targets.extend(_model_cache_targets(hub_root))
    targets.extend(_integration_targets(platform, home_path, env))
    if frozen:
        targets.append(app_root.with_name(f"{app_root.name}.previous-update"))

    return UninstallPlan(
        platform=platform,
        source_checkout=not frozen,
        app_root=app_root,
        user_data_root=data_root,
        targets=_collapse_targets(targets, platform=platform),
    )


def remove_wisp_keychain_entries() -> list[str]:
    """Delete every keychain account Wisp creates, including legacy API-key items."""
    from core import secret_store
    from core.system.native_locks import keychain_lock

    accounts = [*_KEYRING_ACCOUNTS, *(name.lower() for name in secret_store.API_KEY_NAMES)]
    failures: list[str] = []
    try:
        import keyring  # type: ignore
    except Exception as exc:  # No keyring means Wisp could only have used local fallback files.
        return [f"keyring unavailable: {exc}"] if any(secret_store.configured_marker(name) for name in secret_store.API_KEY_NAMES) else []

    with keychain_lock():
        for account in accounts:
            try:
                if keyring.get_password(_KEYRING_SERVICE, account) is not None:
                    keyring.delete_password(_KEYRING_SERVICE, account)
            except keyring.errors.PasswordDeleteError:
                continue
            except Exception as exc:  # noqa: BLE001 - all failures are shown before uninstall proceeds
                failures.append(f"{account}: {type(exc).__name__}: {exc}")
    secret_store.refresh_cache()
    return failures


def _quoted_ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quoted_sh(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def render_windows_uninstall_script(plan: UninstallPlan, *, wait_pid: int, log_path: Path) -> str:
    """Render the detached Windows remover using literal, prevalidated targets."""
    target_lines = "\n".join(f"    {_quoted_ps(str(path))}" for path in plan.targets)
    return f'''$ErrorActionPreference = "Continue"
$waitPid = {int(wait_pid)}
$logPath = {_quoted_ps(str(log_path))}
$helperRoot = Split-Path -LiteralPath $PSCommandPath -Parent
$targets = @(
{target_lines}
)
$failures = New-Object System.Collections.Generic.List[string]
function Write-UninstallLog([string]$Message) {{
    Add-Content -LiteralPath $logPath -Value $Message -Encoding UTF8
}}
while (Get-Process -Id $waitPid -ErrorAction SilentlyContinue) {{ Start-Sleep -Milliseconds 500 }}
Start-Sleep -Seconds 1
try {{
    Remove-ItemProperty -LiteralPath 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' -Name 'Wisp' -ErrorAction SilentlyContinue
}} catch {{ Write-UninstallLog ("Could not remove Wisp login entry: " + $_.Exception.Message) }}
foreach ($target in $targets) {{
    $removed = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {{
        if (-not (Test-Path -LiteralPath $target)) {{ $removed = $true; break }}
        try {{
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
        }} catch {{
            Start-Sleep -Milliseconds 500
        }}
    }}
    if (-not $removed -and -not (Test-Path -LiteralPath $target)) {{ $removed = $true }}
    if (-not $removed) {{
        $failures.Add($target)
        Write-UninstallLog ("Could not remove: " + $target)
    }}
}}
if ($failures.Count -eq 0) {{ Remove-Item -LiteralPath $logPath -Force -ErrorAction SilentlyContinue }}
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
if ($failures.Count -eq 0) {{ Remove-Item -LiteralPath $helperRoot -Force -ErrorAction SilentlyContinue }}
'''


def render_posix_uninstall_script(plan: UninstallPlan, *, wait_pid: int, log_path: Path) -> str:
    """Render the detached macOS/Linux remover using literal targets."""
    targets = " ".join(_quoted_sh(str(path)) for path in plan.targets)
    return f'''#!/bin/sh
wait_pid={int(wait_pid)}
log_path={_quoted_sh(str(log_path))}
helper_dir=$(dirname -- "$0")
while kill -0 "$wait_pid" 2>/dev/null; do sleep 1; done
sleep 1
failed=0
for target in {targets}; do
    if [ -e "$target" ] || [ -L "$target" ]; then
        if ! rm -rf -- "$target" 2>>"$log_path"; then
            printf '%s\n' "Could not remove: $target" >>"$log_path"
            failed=1
        fi
    fi
done
if [ "$failed" -eq 0 ]; then rm -f -- "$log_path"; fi
rm -f -- "$0"
if [ "$failed" -eq 0 ]; then rmdir -- "$helper_dir" 2>/dev/null || true; fi
'''


def launch_uninstaller(plan: UninstallPlan, *, wait_pid: int | None = None) -> UninstallLaunch:
    """Remove Wisp credentials and launch a native helper that survives self-removal."""
    from core import updater

    credential_failures = remove_wisp_keychain_entries()
    if credential_failures:
        raise UninstallError("Could not remove Wisp credentials: " + "; ".join(credential_failures))

    helper_root = Path(tempfile.gettempdir()) / f"wisp-uninstall-{uuid.uuid4().hex}"
    helper_created = False
    try:
        helper_root.mkdir(parents=True, exist_ok=False)
        helper_created = True
        log_path = helper_root / "uninstall-failures.log"
        pid = updater.wisp_wait_pid(wait_pid)
        if plan.platform == "win32":
            script_path = helper_root / "uninstall-wisp.ps1"
            script_path.write_text(
                render_windows_uninstall_script(plan, wait_pid=pid, log_path=log_path),
                encoding="utf-8",
            )
            command = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ]
        else:
            script_path = helper_root / "uninstall-wisp.sh"
            script_path.write_text(
                render_posix_uninstall_script(plan, wait_pid=pid, log_path=log_path),
                encoding="utf-8",
            )
            script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
            command = [str(script_path)]
        updater.launch_detached_helper(command, cwd=helper_root)
        return UninstallLaunch(script_path=script_path, failure_log_path=log_path)
    except Exception:
        # A helper that never launched cannot remove itself.  Do not leave its
        # script/log directory behind when setup, permission, or process launch
        # fails.  The original exception remains the actionable failure.
        if helper_created:
            shutil.rmtree(helper_root, ignore_errors=True)
        raise
