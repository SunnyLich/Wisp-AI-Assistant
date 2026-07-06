"""Run optional speech package installs in a standalone terminal."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _log(handle, prefix: str, message: str) -> None:
    line = f"{prefix} {message}"
    print(line, flush=True)
    try:
        handle.write(line + "\n")
        handle.flush()
    except Exception:
        pass


def _write_status(
    path: Path | None,
    *,
    ok: bool | None,
    message: str,
    extra: dict[str, object] | None = None,
) -> None:
    """Persist installer status for Settings to show after the terminal exits."""
    if path is None:
        return
    try:
        import time

        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ok": ok,
            "message": str(message or ""),
            "updated_at": time.time(),
        }
        if extra:
            payload.update(extra)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _load_plan(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("installer plan must be a JSON object")
    return data


def _package_list(plan: dict[str, Any], key: str) -> list[str]:
    """Return a validated package list from an installer plan."""
    packages = plan.get(key)
    if packages is None:
        return []
    if not isinstance(packages, list) or not all(isinstance(item, str) for item in packages):
        raise ValueError(f"installer plan {key} must be a list of strings")
    return packages


def _remove_artifacts(log, prefix: str, patterns: list[str]) -> None:
    """Remove optional-package artifacts before reinstalling a repaired package."""
    if not patterns:
        return
    from core import optional_deps

    _log(log, prefix, "Removing previous optional package artifacts before install.")
    removed = optional_deps.remove_optional_package_artifacts(patterns)
    if removed:
        _log(log, prefix, f"Removed: {', '.join(sorted(removed))}")
    else:
        _log(log, prefix, "No previous artifacts found.")


def _remove_duplicate_dist_infos(log, prefix: str) -> None:
    """Remove mixed optional-package trees before running a target install."""
    from core import optional_deps

    removed = optional_deps.remove_duplicate_optional_package_artifacts()
    if removed:
        _log(
            log,
            prefix,
            "Removed stale duplicate optional package artifacts: "
            f"{', '.join(sorted(removed))}",
        )


def _remove_stale_install_artifacts(log, prefix: str, packages: list[str]) -> None:
    """Remove package trees that do not match exact install specs."""
    from core import optional_deps

    removed = optional_deps.remove_stale_optional_package_artifacts(packages)
    if removed:
        _log(
            log,
            prefix,
            "Removed stale optional package artifacts before install: "
            f"{', '.join(sorted(removed))}",
        )


def _warn_duplicate_dist_infos(log, prefix: str) -> None:
    """Log duplicate optional package metadata left after installation."""
    from core import optional_deps

    duplicates = optional_deps.duplicate_optional_dist_infos()
    if not duplicates:
        return
    detail = "; ".join(
        f"{package}: {', '.join(names)}"
        for package, names in sorted(duplicates.items())
    )
    _log(log, prefix, f"Warning: duplicate optional package metadata found: {detail}")


def _run_install_command(
    log,
    prefix: str,
    packages: list[str],
    *,
    reinstall: bool = False,
    target_dir: Path | None = None,
) -> int:
    """Run one optional package install command, streaming output to the terminal."""
    from core import optional_deps

    try:
        optional_deps.ensure_pip_available()
        if target_dir is None:
            command = optional_deps.pip_install_command(packages, reinstall=reinstall)
        else:
            command = optional_deps.pip_install_command(packages, reinstall=reinstall, target_dir=target_dir)
    except Exception as exc:
        _log(log, prefix, f"Failed to prepare installer: {type(exc).__name__}: {exc}")
        return 1
    _log(log, prefix, f"Running: {' '.join(command)}")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=optional_deps.pip_install_env(),
        )
    except Exception as exc:
        _log(log, prefix, f"Failed to start installer: {type(exc).__name__}: {exc}")
        return 1
    _log(log, prefix, f"installer started with pid {process.pid}")
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.strip()
        if line:
            _log(log, prefix, line)
    returncode = process.wait()
    if returncode != 0:
        _log(log, prefix, f"Failed with exit code {returncode}.")
    return int(returncode or 0)


def _post_install_result(log, prefix: str, plan: dict[str, Any], status_path: Path | None) -> tuple[bool, str]:
    from core import optional_deps

    optional_deps.add_optional_packages_to_path()
    display_name = str(plan.get("display_name") or "Optional package")
    post_install = str(plan.get("post_install") or "")
    if post_install == "kokoro_prepare":
        voice = str(plan.get("kokoro_voice") or "af_heart")
        require_gpu = bool(plan.get("kokoro_require_gpu"))
        try:
            from core import tts

            _log(log, prefix, f"Preparing Kokoro model and voice assets for {voice}.")
            paths = tts.prepare_kokoro_assets(voice=voice)
            for name, path in sorted(paths.items()):
                _log(log, prefix, f"Prepared {name}: {path}")
            runtime_status = optional_deps.kokoro_runtime_import_status()
            if runtime_status.get("error") or runtime_status.get("valid") is False:
                detail = str(runtime_status.get("error") or "Kokoro runtime import failed.")
                return False, f"Kokoro installed, but runtime verification failed: {detail}"
            torch_status = optional_deps.kokoro_torch_status()
            if torch_status.get("error") or torch_status.get("valid") is False:
                detail = str(torch_status.get("error") or "Torch verification failed.")
                return False, f"Kokoro installed, but Torch verification failed: {detail}"
            if require_gpu and not torch_status.get("cuda_available"):
                return False, "Kokoro installed, but CUDA Torch verification failed."
        except Exception as exc:
            return False, f"Kokoro installed, but local voice preparation failed: {type(exc).__name__}: {exc}"
    elif post_install == "stt_prepare":
        model = str(plan.get("stt_model") or "base")
        device = str(plan.get("stt_device") or "auto")
        compute_type = str(plan.get("stt_compute_type") or "int8")
        _write_status(status_path, ok=None, message=f"Installing STT: downloading or loading Whisper model {model}.")
        _log(log, prefix, f"Downloading or loading STT model {model} on {device} ({compute_type}).")
        status = optional_deps.stt_model_status_subprocess(model, device, compute_type)
        if status.get("error") or status.get("valid") is False:
            detail = str(status.get("error") or "STT model verification failed.")
            return False, f"STT installed, but model verification failed: {detail}"
        resolved = f"{status.get('model') or model} on {status.get('device') or device} ({status.get('compute') or compute_type})"
        return True, f"STT installed and model ready: {resolved}."

    return True, f"{display_name} installed successfully."


def _path_from_plan(plan: dict[str, Any], key: str) -> Path:
    value = str(plan.get(key) or "").strip()
    if not value:
        raise ValueError(f"installer plan is missing {key}")
    return Path(value).expanduser().resolve()


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _remove_active_packages_replaced_by_stage(staging: Path, target: Path) -> list[str]:
    from core import optional_deps

    groups: dict[str, list[Path]] = {}
    for staged in staging.iterdir():
        if staged.is_dir() and staged.name.endswith(".dist-info"):
            package, _version = optional_deps._dist_info_metadata(staged)  # type: ignore[attr-defined]
            canonical = optional_deps._canonical_package_name(package)  # type: ignore[attr-defined]
            if canonical:
                groups.setdefault(canonical, [])
    if not groups or not target.exists():
        return []
    for active in target.iterdir():
        if active.is_dir() and active.name.endswith(".dist-info"):
            package, _version = optional_deps._dist_info_metadata(active)  # type: ignore[attr-defined]
            canonical = optional_deps._canonical_package_name(package)  # type: ignore[attr-defined]
            if canonical in groups:
                groups[canonical].append(active)
    removed: list[str] = []
    for package, dist_infos in groups.items():
        for path in optional_deps._optional_package_artifact_paths(target, package, dist_infos):  # type: ignore[attr-defined]
            if path.exists():
                _remove_path(path)
                removed.append(path.name)
    return sorted(set(removed))


def _apply_staging(staging: Path, target: Path, log, prefix: str) -> None:
    if not staging.is_dir():
        raise RuntimeError(f"Staged package directory is missing: {staging}")
    target.mkdir(parents=True, exist_ok=True)
    removed = _remove_active_packages_replaced_by_stage(staging, target)
    if removed:
        _log(log, prefix, f"Removed active package artifacts before staged apply: {', '.join(removed)}")
    moved: list[str] = []
    for child in sorted(staging.iterdir(), key=lambda p: p.name.lower()):
        destination = target / child.name
        if destination.exists():
            _remove_path(destination)
        shutil.move(str(child), str(destination))
        moved.append(child.name)
    _log(log, prefix, f"Applied staged package files: {', '.join(moved) if moved else '(none)'}")


def _restart_wisp(log, prefix: str) -> None:
    from core import updater

    command, cwd = updater.app_restart_command()
    _log(log, prefix, f"Reopening Wisp: {' '.join(command)}")
    updater.launch_detached_helper(command, cwd=cwd)


def _run_staged_apply(plan_path: Path) -> int:
    from core import updater

    plan = _load_plan(plan_path)
    display_name = str(plan.get("display_name") or "Optional package")
    prefix = f"[{display_name.lower()} install]"
    log_path = _path_from_plan(plan, "log_path")
    status_path = Path(str(plan.get("status_path") or "")).expanduser() if plan.get("status_path") else None
    staging_path = _path_from_plan(plan, "staging_path")
    wisp_closed = False
    try:
        with log_path.open("a", encoding="utf-8") as log:
            _write_status(status_path, ok=None, message=f"{display_name} staged install is waiting for Wisp to close.")
            _log(log, prefix, "Waiting for Wisp to exit before applying staged packages.")
            updater.wait_for_wisp_exit(int(plan.get("wait_pid") or 0), timeout=300.0)
            wisp_closed = True
            _write_status(status_path, ok=None, message=f"{display_name} staged install is replacing package files.")
            _apply_staging(staging_path, _path_from_plan(plan, "target_path"), log, prefix)
            ok, message = _post_install_result(log, prefix, plan, status_path)
            _log(log, prefix, message)
            _write_status(status_path, ok=ok, message=message)
            _restart_wisp(log, prefix)
            if ok:
                _write_status(status_path, ok=True, message=f"{message} Wisp is reopening.")
            return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001 - helper failures must be visible in status/logs
        message = f"{display_name} staged install failed: {type(exc).__name__}: {exc}"
        with log_path.open("a", encoding="utf-8") as log:
            _log(log, prefix, message)
            _write_status(status_path, ok=False, message=message)
            if wisp_closed:
                try:
                    _restart_wisp(log, prefix)
                except Exception as restart_exc:  # noqa: BLE001
                    _log(log, prefix, f"Failed to reopen Wisp: {type(restart_exc).__name__}: {restart_exc}")
        return 1
    finally:
        shutil.rmtree(staging_path, ignore_errors=True)


def _slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return text or "optional-package"


def _launch_staged_apply(plan_path: Path) -> None:
    from core import optional_deps, updater

    updater.launch_detached_helper(
        [sys.executable, "-m", "runtime.workers.optional_speech_installer", "--apply-plan", str(plan_path)],
        cwd=ROOT,
        env=optional_deps.pip_install_env(),
    )


def _run_staged_restart_install(
    *,
    plan: dict[str, Any],
    display_name: str,
    log_path: Path,
    status_path: Path | None,
    prefix: str,
    pre_install_packages: list[str],
    packages: list[str],
    reinstall: bool,
) -> int:
    from core import optional_deps, updater

    slug = _slug(display_name)
    staging_root = optional_deps.OPTIONAL_PACKAGES_DIR.parent / "_staged_installs"
    staging_path = staging_root / f"{slug}-{int(time.time())}-{os.getpid()}"
    if staging_path.exists():
        shutil.rmtree(staging_path)
    staging_path.mkdir(parents=True, exist_ok=True)
    _write_status(status_path, ok=None, message=f"{display_name} install is staging packages before restart.")

    with log_path.open("a", encoding="utf-8") as log:
        _log(log, prefix, f"Installing into staging folder: {staging_path}")
        if pre_install_packages:
            _log(log, prefix, "Installing CUDA Torch into staging before Kokoro packages.")
            returncode = _run_install_command(
                log,
                prefix,
                pre_install_packages,
                reinstall=True,
                target_dir=staging_path,
            )
            if returncode != 0:
                _write_status(status_path, ok=False, message=f"{display_name} install failed during staged CUDA Torch install.")
                return returncode
        if packages:
            returncode = _run_install_command(
                log,
                prefix,
                packages,
                reinstall=reinstall,
                target_dir=staging_path,
            )
            if returncode != 0:
                _write_status(status_path, ok=False, message=f"{display_name} install failed during staged package install.")
                return returncode

        apply_plan_path = log_path.with_suffix(".apply-plan.json")
        apply_plan = {
            **plan,
            "staging_path": str(staging_path),
            "target_path": str(optional_deps.OPTIONAL_PACKAGES_DIR),
            "wait_pid": updater.wisp_wait_pid(),
            "log_path": str(log_path),
            "status_path": str(status_path) if status_path else "",
        }
        apply_plan_path.write_text(json.dumps(apply_plan, indent=2, sort_keys=True), encoding="utf-8")
        _log(log, prefix, "Staged packages downloaded. Wisp will close so locked package files can be replaced.")
        _launch_staged_apply(apply_plan_path)
        _write_status(
            status_path,
            ok=None,
            message=(
                f"{display_name} packages are staged. Wisp will close, replace locked files, "
                "verify the install, and reopen."
            ),
            extra={"restart_apply": True},
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", help="Path to the installer plan JSON.")
    parser.add_argument("--apply-plan", help="Path to a staged apply plan JSON.")
    args = parser.parse_args()

    from core import optional_deps

    if args.apply_plan:
        return _run_staged_apply(Path(args.apply_plan).expanduser().resolve())
    if not args.plan:
        parser.error("--plan is required unless --apply-plan is used")

    plan_path = Path(args.plan).expanduser().resolve()
    plan = _load_plan(plan_path)
    display_name = str(plan.get("display_name") or "Optional package")
    remove_artifacts = _package_list(plan, "remove_artifacts")
    pre_install_packages = _package_list(plan, "pre_install_packages")
    packages = _package_list(plan, "packages")
    reinstall = bool(plan.get("reinstall"))
    if not packages and not pre_install_packages:
        raise ValueError("installer plan packages or pre_install_packages must be a non-empty list of strings")
    raw_log_path = str(plan.get("log_path") or "").strip()
    log_path = Path(raw_log_path).expanduser() if raw_log_path else Path(plan_path).with_suffix(".log")
    raw_status_path = str(plan.get("status_path") or "").strip()
    status_path = Path(raw_status_path).expanduser() if raw_status_path else None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"[{display_name.lower()} install]"

    with log_path.open("a", encoding="utf-8") as log:
        if bool(plan.get("restart_apply")) and sys.platform == "win32":
            return _run_staged_restart_install(
                plan=plan,
                display_name=display_name,
                log_path=log_path,
                status_path=status_path,
                prefix=prefix,
                pre_install_packages=pre_install_packages,
                packages=packages,
                reinstall=reinstall,
            )
        _write_status(status_path, ok=None, message=f"{display_name} install is running.")
        _remove_artifacts(log, prefix, remove_artifacts)
        _remove_stale_install_artifacts(log, prefix, [*pre_install_packages, *packages])
        _remove_duplicate_dist_infos(log, prefix)
        if pre_install_packages:
            _log(log, prefix, "Installing CUDA Torch before Kokoro packages.")
            returncode = _run_install_command(log, prefix, pre_install_packages, reinstall=True)
            if returncode != 0:
                _write_status(status_path, ok=False, message=f"{display_name} install failed during CUDA Torch install.")
                return returncode
        if packages:
            returncode = _run_install_command(log, prefix, packages, reinstall=reinstall)
            if returncode != 0:
                _write_status(status_path, ok=False, message=f"{display_name} install failed during package install.")
                return returncode

        _warn_duplicate_dist_infos(log, prefix)
        ok, message = _post_install_result(log, prefix, plan, status_path)
        _log(log, prefix, message if not ok else "Completed successfully.")
        _write_status(status_path, ok=ok, message=message)
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
