"""Run optional speech package installs in a standalone terminal."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# How long an apply helper watches for Wisp to close. A missed window no
# longer discards the staged download; the supervisor re-arms the plan on the
# next launch, so this only bounds how long one helper process lingers.
STAGED_APPLY_WAIT_SECONDS = 24 * 60 * 60.0
# A staged apply that keeps failing is retried at later restarts, but only
# this many times before the staging is discarded for good.
STAGED_APPLY_MAX_ATTEMPTS = 3
STAGED_FILE_RETRY_SECONDS = 12.0
STAGED_FILE_RETRY_INTERVAL_SECONDS = 0.4
_LAST_INSTALL_FAILURE_DETAIL = ""

_DISK_FULL_MARKERS = (
    "os error 112",
    "no space left on device",
    "not enough space on the disk",
    "there is not enough space on the disk",
    "disk full",
)
_DISK_FULL_GUIDANCE = (
    "Not enough free disk space while extracting the downloaded packages. "
    "Free at least 15 GB on the drive containing the uv cache and Wisp optional packages, then retry. "
    "For a much smaller download, select CPU for Kokoro instead of Auto or GPU."
)
_INSTALLER_DECORATION_PREFIX = re.compile(r"^[\s?\ufffd×╰├│─▶└┌┬┐]+(?=[A-Za-z`(])")


def _normalize_installer_output_line(line: str) -> str:
    """Convert package-manager tree glyphs that commonly mojibake to ASCII."""
    text = str(line).strip()
    if _INSTALLER_DECORATION_PREFIX.match(text):
        return _INSTALLER_DECORATION_PREFIX.sub("-> ", text, count=1)
    return text


def _safe_console_print(line: str) -> None:
    """Print installer progress without crashing on legacy Windows code pages."""
    text = str(line)
    try:
        print(text, flush=True)
        return
    except UnicodeEncodeError:
        stream = sys.stdout
        encoding = getattr(stream, "encoding", None) or "utf-8"
        # Preserve unsupported characters as explicit Unicode escapes instead
        # of turning an entire localized diagnostic into meaningless ?????.
        safe = text.encode(encoding, errors="backslashreplace").decode(encoding, errors="strict")
        try:
            print(safe, flush=True)
        except Exception:
            pass


def _plan_app_language(plan: dict[str, Any]) -> str:
    return str(plan.get("app_language") or os.environ.get("APP_LANGUAGE") or "").strip()


def _installer_env_for_plan(plan: dict[str, Any]) -> dict[str, str]:
    from core import optional_deps

    env = optional_deps.pip_install_env()
    language = _plan_app_language(plan)
    if language:
        env["APP_LANGUAGE"] = language
    return env


def _apply_plan_language(plan: dict[str, Any]) -> None:
    language = _plan_app_language(plan)
    if not language:
        return
    os.environ["APP_LANGUAGE"] = language
    try:
        import config

        config.APP_LANGUAGE = language
    except Exception:
        pass


def _log(handle, prefix: str, message: str) -> None:
    line = f"{prefix} {message}"
    _safe_console_print(line)
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


def _plan_status_metadata(plan: dict[str, Any]) -> dict[str, object]:
    """Return the contract fields that make persisted results non-stale."""
    metadata: dict[str, object] = {}
    contract = str(plan.get("install_contract") or "").strip()
    app_version = str(plan.get("app_version") or "").strip()
    if contract:
        metadata["install_contract"] = contract
    if app_version:
        metadata["app_version"] = app_version
    return metadata


def _status_extra(plan: dict[str, Any], **extra: object) -> dict[str, object]:
    return {**_plan_status_metadata(plan), **extra}


def _format_spec_status_message(status: dict[str, object]) -> str:
    message = str(status.get("message") or "").strip()
    if message:
        return message
    display_name = str(status.get("display_name") or "Optional package")
    return f"{display_name} package files do not match this Wisp release."


def _spec_key_for_display_name(display_name: str) -> str:
    key = display_name.strip().lower().replace(" ", "-")
    return "live_voice" if key == "live-voice" else key


def _load_plan(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("installer plan must be a JSON object")
    return data


def _write_plan(path: Path, plan: dict[str, Any]) -> None:
    """Persist plan updates for apply helpers and later re-arming."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


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


def _iter_install_output(stream) -> Any:
    """Yield installer output records, treating CR progress updates as records.

    pip/uv can redraw download progress with carriage returns instead of
    newline-terminated lines. Reading by record keeps normal line output
    unchanged while letting those redraws reach the visible installer log.
    """
    read = getattr(stream, "read", None)
    if not callable(read):
        for raw_line in stream:
            line = str(raw_line).strip()
            if line:
                yield line
        return

    pending: list[str] = []
    while True:
        chunk = read(1)
        if chunk in ("", b""):
            break
        if isinstance(chunk, bytes):
            text = chunk.decode("utf-8", errors="replace")
        else:
            text = str(chunk)
        for char in text:
            if char in "\r\n":
                line = "".join(pending).strip()
                pending.clear()
                if line:
                    yield line
            else:
                pending.append(char)
    line = "".join(pending).strip()
    if line:
        yield line


def _install_failure_detail(output_lines: list[str]) -> str:
    """Turn known low-level package-manager failures into actionable advice."""
    output = "\n".join(output_lines).lower()
    if any(marker in output for marker in _DISK_FULL_MARKERS):
        return _DISK_FULL_GUIDANCE
    return ""


def _run_install_command(
    log,
    prefix: str,
    packages: list[str],
    *,
    reinstall: bool = False,
    target_dir: Path | None = None,
) -> int:
    """Run one optional package install command, streaming output to the terminal."""
    global _LAST_INSTALL_FAILURE_DETAIL

    from core import optional_deps

    _LAST_INSTALL_FAILURE_DETAIL = ""

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
    output_tail: list[str] = []
    for line in _iter_install_output(process.stdout):
        line = _normalize_installer_output_line(line)
        if not line:
            continue
        output_tail.append(line)
        if len(output_tail) > 200:
            del output_tail[:-200]
        _log(log, prefix, line)
    returncode = process.wait()
    if returncode != 0:
        _LAST_INSTALL_FAILURE_DETAIL = _install_failure_detail(output_tail)
        if _LAST_INSTALL_FAILURE_DETAIL:
            _log(log, prefix, f"ERROR: {_LAST_INSTALL_FAILURE_DETAIL}")
        _log(log, prefix, f"Failed with exit code {returncode}.")
    return int(returncode or 0)


def _run_install_phase(*args, **kwargs) -> tuple[int, str]:
    """Run an install command and return its exit code plus classified failure."""
    global _LAST_INSTALL_FAILURE_DETAIL

    _LAST_INSTALL_FAILURE_DETAIL = ""
    returncode = _run_install_command(*args, **kwargs)
    return returncode, _LAST_INSTALL_FAILURE_DETAIL


def _post_install_result(log, prefix: str, plan: dict[str, Any], status_path: Path | None) -> tuple[bool, str]:
    from core import optional_deps

    optional_deps.add_optional_packages_to_path()
    display_name = str(plan.get("display_name") or "Optional package")
    post_install = str(plan.get("post_install") or "")
    spec_key = str(plan.get("spec_key") or _spec_key_for_display_name(display_name))
    spec_device = plan.get("kokoro_install_device")
    if spec_device is None and post_install == "kokoro_prepare":
        spec_device = "cuda" if bool(plan.get("kokoro_require_gpu")) else "cpu"
    if spec_device is None and post_install == "stt_prepare":
        spec_device = str(plan.get("stt_device") or "auto")
    try:
        spec_status = optional_deps.optional_package_spec_status(spec_key, device=str(spec_device) if spec_device is not None else None)
    except Exception:
        spec_status = {}
    if spec_status and spec_status.get("valid") is not True:
        detail = _format_spec_status_message(spec_status)
        return False, f"{display_name} package install failed: {detail}"
    if post_install == "kokoro_prepare":
        voice = str(plan.get("kokoro_voice") or "af_heart")
        require_gpu = bool(plan.get("kokoro_require_gpu"))
        try:
            from core import tts

            _write_status(status_path, ok=None, message="Kokoro package installed; preparing local voice assets.")
            _log(log, prefix, f"Preparing Kokoro model and voice assets for {voice}.")
            paths = tts.prepare_kokoro_assets(voice=voice)
            for name, path in sorted(paths.items()):
                _log(log, prefix, f"Prepared {name}: {path}")
            _write_status(status_path, ok=None, message="Kokoro package installed; verifying runtime import.")
            runtime_status = optional_deps.kokoro_runtime_import_status()
            if runtime_status.get("error") or runtime_status.get("valid") is False:
                detail = str(runtime_status.get("error") or "Kokoro runtime import failed.")
                return False, f"Kokoro installed, but runtime verification failed: {detail}"
            _write_status(status_path, ok=None, message="Kokoro package installed; verifying Torch.")
            torch_status = optional_deps.kokoro_torch_status()
            if torch_status.get("error") or torch_status.get("valid") is False:
                detail = str(torch_status.get("error") or "Torch verification failed.")
                return False, f"Kokoro installed, but Torch verification failed: {detail}"
            if require_gpu and not torch_status.get("cuda_available"):
                detail = optional_deps.kokoro_cuda_failure_detail(torch_status)
                return False, f"Kokoro installed, but CUDA Torch verification failed: {detail}"
        except Exception as exc:
            return False, f"Kokoro package installed, but voice asset preparation failed: {type(exc).__name__}: {exc}"
    elif post_install == "stt_prepare":
        model = str(plan.get("stt_model") or "base")
        device = str(plan.get("stt_device") or "auto")
        compute_type = str(plan.get("stt_compute_type") or "int8")
        _write_status(
            status_path,
            ok=None,
            message=f"STT package installed; downloading or loading Whisper model {model}.",
            extra={"progress_percent": 75},
        )
        _log(log, prefix, f"Downloading or loading STT model {model} on {device} ({compute_type}).")

        def _stt_model_progress(elapsed_seconds: int) -> None:
            minutes, seconds = divmod(max(0, int(elapsed_seconds)), 60)
            elapsed = f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"
            message = (
                f"STT model {model} is still downloading or loading after {elapsed}. "
                "The first download can be large; Wisp will report an error if verification times out."
            )
            _log(log, prefix, message)
            _write_status(status_path, ok=None, message=message, extra={"progress_percent": 75})

        status = optional_deps.stt_model_status_subprocess(
            model,
            device,
            compute_type,
            progress=_stt_model_progress,
        )
        for diagnostic in status.get("diagnostics") or []:
            _log(log, prefix, f"STT diagnostic: {diagnostic}")
        if status.get("error") or status.get("valid") is False:
            detail = str(status.get("error") or "STT model verification failed.")
            return False, f"STT package installed, but model download/load failed: {detail}"
        resolved = f"{status.get('model') or model} on {status.get('device') or device} ({status.get('compute') or compute_type})"
        effective_device = str(status.get("device") or device)
        effective_compute = str(status.get("compute") or compute_type)
        if effective_device != device or effective_compute != compute_type:
            return True, (
                f"STT installed and model ready: {resolved}. Requested {device} ({compute_type}); "
                f"runtime verification selected {effective_device} ({effective_compute})."
            )
        return True, f"STT installed and model ready: {resolved}."

    return True, f"{display_name} installed successfully."


def _path_from_plan(plan: dict[str, Any], key: str) -> Path:
    value = str(plan.get(key) or "").strip()
    if not value:
        raise ValueError(f"installer plan is missing {key}")
    return Path(value).expanduser().resolve()


def _remove_path(path: Path) -> None:
    def _remove() -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    _retry_file_operation(_remove)


def _retry_file_operation(operation) -> None:
    deadline = time.monotonic() + STAGED_FILE_RETRY_SECONDS
    while True:
        try:
            operation()
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(STAGED_FILE_RETRY_INTERVAL_SECONDS)


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


def _move_staged_entry(source: Path, destination: Path) -> None:
    """Move one staged entry into the target, merging shared namespace dirs.

    Replacing a directory wholesale is right for regular packages (their old
    artifacts were already removed), but implicit-namespace dirs like google/
    hold members from several distributions — replacing google/ while
    installing protobuf would delete google-genai and google-auth.
    """

    def _plain_dir(path: Path) -> bool:
        return path.is_dir() and not path.is_symlink()

    if (
        _plain_dir(source)
        and _plain_dir(destination)
        and not source.name.endswith(".dist-info")
        and not (source / "__init__.py").exists()
        and not (destination / "__init__.py").exists()
    ):
        for child in sorted(source.iterdir(), key=lambda p: p.name.lower()):
            _move_staged_entry(child, destination / child.name)
        shutil.rmtree(source, ignore_errors=True)
        return
    if destination.exists():
        _remove_path(destination)
    _retry_file_operation(lambda: shutil.move(str(source), str(destination)))


def _apply_staging(staging: Path, target: Path, log, prefix: str) -> None:
    if not staging.is_dir():
        raise RuntimeError(f"Staged package directory is missing: {staging}")
    target_parent = target.parent
    target_parent.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time())}-{os.getpid()}"
    replacement = target_parent / f".{target.name}.replacement-{stamp}"
    backup = target_parent / f".{target.name}.backup-{stamp}"
    if replacement.exists():
        shutil.rmtree(replacement, ignore_errors=True)
    if target.exists():
        shutil.copytree(target, replacement, symlinks=True)
        _log(log, prefix, f"Prepared replacement package folder from active install: {replacement}")
    else:
        replacement.mkdir(parents=True, exist_ok=True)
        _log(log, prefix, f"Prepared new replacement package folder: {replacement}")
    removed = _remove_active_packages_replaced_by_stage(staging, replacement)
    if removed:
        _log(log, prefix, f"Removed active package artifacts before staged apply: {', '.join(removed)}")
    moved: list[str] = []
    for child in sorted(staging.iterdir(), key=lambda p: p.name.lower()):
        _move_staged_entry(child, replacement / child.name)
        moved.append(child.name)
    _log(log, prefix, f"Prepared staged package files: {', '.join(moved) if moved else '(none)'}")
    target_moved = False
    try:
        if target.exists():
            _retry_file_operation(lambda: target.rename(backup))
            target_moved = True
            _log(log, prefix, f"Moved active package folder aside: {backup}")
        _retry_file_operation(lambda: replacement.rename(target))
        _log(log, prefix, "Activated replacement package folder.")
    except Exception:
        if target_moved and not target.exists() and backup.exists():
            try:
                backup.rename(target)
                _log(log, prefix, "Restored previous package folder after failed apply.")
            except Exception as rollback_exc:  # noqa: BLE001
                _log(log, prefix, f"Failed to restore previous package folder: {type(rollback_exc).__name__}: {rollback_exc}")
        shutil.rmtree(replacement, ignore_errors=True)
        raise
    if backup.exists():
        try:
            _retry_file_operation(lambda: shutil.rmtree(backup))
        except OSError as exc:
            # Native speech DLLs can remain locked briefly on Windows.  Activation
            # has already succeeded, so keep the app usable and let supervisor
            # startup retry this exact Wisp-generated backup later.
            _log(
                log,
                prefix,
                f"Could not remove inactive package backup {backup}: {type(exc).__name__}: {exc}. "
                "Wisp will retry cleanup on a later startup.",
            )


def _launch_apply_status_window(
    display_name: str,
    status_path: Path | None,
    log_path: Path,
    *,
    app_language: str = "",
) -> None:
    if status_path is None:
        return
    try:
        from core import updater

        command = [
            sys.executable,
            "-m",
            "runtime.workers.optional_apply_status_window",
            "--display-name",
            display_name,
            "--status-path",
            str(status_path),
            "--log-path",
            str(log_path),
        ]
        env = None
        if app_language:
            command.extend(["--language", app_language])
            env = os.environ.copy()
            env["APP_LANGUAGE"] = app_language
        updater.launch_detached_helper(command, cwd=ROOT, env=env)
    except Exception:
        pass


def _restart_wisp(log, prefix: str) -> None:
    from core import updater

    command, cwd = updater.app_restart_command()
    _log(log, prefix, f"Reopening Wisp: {' '.join(command)}")
    updater.launch_detached_helper(command, cwd=cwd)


def _run_staged_apply(plan_path: Path) -> int:
    from core import updater

    plan = _load_plan(plan_path)
    _apply_plan_language(plan)
    display_name = str(plan.get("display_name") or "Optional package")
    prefix = f"[{display_name.lower()} install]"
    log_path = _path_from_plan(plan, "log_path")
    status_path = Path(str(plan.get("status_path") or "")).expanduser() if plan.get("status_path") else None
    staging_path = _path_from_plan(plan, "staging_path")
    reopen = bool(plan.get("reopen_after_apply", True))
    attempts = int(plan.get("apply_attempts") or 0)
    # Record this helper so a later Wisp launch does not arm a second helper
    # against the same staging (two concurrent applies would corrupt it).
    try:
        plan["helper_pid"] = os.getpid()
        _write_plan(plan_path, plan)
    except OSError:
        pass
    wisp_closed = False
    consumed = False
    try:
        with log_path.open("a", encoding="utf-8") as log:
            _write_status(
                status_path,
                ok=None,
                message=f"{display_name} staged install is waiting for Wisp to close.",
                extra=_status_extra(plan, restart_apply=True, progress_percent=5),
            )
            _log(log, prefix, "Waiting for Wisp to exit before applying staged packages.")
            try:
                updater.wait_for_wisp_exit(int(plan.get("wait_pid") or 0), timeout=STAGED_APPLY_WAIT_SECONDS)
            except updater.UpdateError:
                # Wisp never closed while this helper watched. Keep the staged
                # download and the plan; the supervisor re-arms them on the
                # next launch instead of throwing the download away.
                message = (
                    f"{display_name} packages stay staged and will be applied "
                    "the next time Wisp restarts."
                )
                _log(log, prefix, message)
                _write_status(
                    status_path,
                    ok=None,
                    message=message,
                    extra=_status_extra(plan, restart_apply=True, progress_percent=5),
                )
                return 0
            wisp_closed = True
            try:
                current_plan = _load_plan(plan_path)
            except Exception:
                current_plan = {}
            if str(current_plan.get("staging_path") or "") != str(plan.get("staging_path") or ""):
                # A newer install replaced this plan while we waited; its own
                # helper owns the apply now. Drop this stale staging quietly.
                _log(log, prefix, "Staged packages were superseded by a newer install; discarding them.")
                consumed = True
                return 0
            attempts += 1
            plan["apply_attempts"] = attempts
            _write_plan(plan_path, plan)
            _write_status(
                status_path,
                ok=None,
                message=f"{display_name} staged install is applying package files.",
                extra=_status_extra(plan, progress_percent=45),
            )
            _launch_apply_status_window(display_name, status_path, log_path, app_language=_plan_app_language(plan))
            _apply_staging(staging_path, _path_from_plan(plan, "target_path"), log, prefix)
            consumed = True
            _write_status(
                status_path,
                ok=None,
                message=f"{display_name} staged install is verifying package files.",
                extra=_status_extra(plan, progress_percent=70),
            )
            ok, message = _post_install_result(log, prefix, plan, status_path)
            _log(log, prefix, message)
            _write_status(
                status_path,
                ok=ok,
                message=message,
                extra=_status_extra(plan, progress_percent=100 if ok else 70),
            )
            plan_path.unlink(missing_ok=True)
            if reopen:
                _restart_wisp(log, prefix)
                if ok:
                    _write_status(
                        status_path,
                        ok=True,
                        message=f"{message} Wisp is reopening.",
                        extra=_status_extra(plan, progress_percent=100),
                    )
            return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001 - helper failures must be visible in status/logs
        give_up = consumed or attempts >= STAGED_APPLY_MAX_ATTEMPTS
        message = f"{display_name} staged install failed: {type(exc).__name__}: {exc}"
        extra: dict[str, object] | None = None
        if not give_up:
            message = f"{message} Wisp will retry at the next restart."
            extra = _status_extra(plan, restart_apply=True, progress_percent=70)
        else:
            extra = _status_extra(plan, progress_percent=70)
        with log_path.open("a", encoding="utf-8") as log:
            _log(log, prefix, message)
            _write_status(status_path, ok=False, message=message, extra=extra)
            if wisp_closed and reopen:
                try:
                    _restart_wisp(log, prefix)
                except Exception as restart_exc:  # noqa: BLE001
                    _log(log, prefix, f"Failed to reopen Wisp: {type(restart_exc).__name__}: {restart_exc}")
        if give_up:
            consumed = True
            plan_path.unlink(missing_ok=True)
        return 1
    finally:
        if consumed:
            shutil.rmtree(staging_path, ignore_errors=True)


def _slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return text or "optional-package"


def _launch_staged_apply(plan_path: Path) -> None:
    from core import updater

    try:
        plan = _load_plan(plan_path)
    except Exception:
        plan = {}

    updater.launch_detached_helper(
        [sys.executable, "-m", "runtime.workers.optional_speech_installer", "--apply-plan", str(plan_path)],
        cwd=ROOT,
        env=_installer_env_for_plan(plan),
    )


def pending_apply_plan_paths() -> list[Path]:
    """Return staged apply plans recorded next to the optional install logs."""
    from core import optional_deps

    bases = [
        optional_deps.OPTIONAL_PACKAGES_DIR.parent / "installers",
        optional_deps.OPTIONAL_PACKAGES_DIR / "_logs",
    ]
    run_root = os.environ.get("WISP_RUN_LOG_DIR")
    if run_root:
        bases.append(Path(run_root).expanduser() / "installers")
    plans: list[Path] = []
    for base in bases:
        try:
            plans.extend(sorted(base.glob("*.apply-plan.json")))
        except OSError:
            continue
    return plans


def cleanup_stale_optional_package_swaps() -> tuple[list[Path], dict[Path, str]]:
    """Remove inactive package swap folders left by completed applies.

    Cleanup is deliberately deferred while any apply plan exists.  An apply
    helper does not own the app's single-instance lock, so a manually launched
    Wisp could otherwise race the helper between moving the active package
    folder aside and activating its replacement.

    Only exact names emitted by Wisp's current and legacy speech installers are
    eligible.  The active optional-package folder, staging folders, symlinks,
    and arbitrary sibling directories are never removed.
    """
    from core import optional_deps

    if pending_apply_plan_paths():
        return [], {}

    target = Path(optional_deps.OPTIONAL_PACKAGES_DIR)
    parent = target.parent
    escaped_name = re.escape(target.name)
    patterns = (
        re.compile(rf"\.{escaped_name}\.backup-\d+-\d+"),
        re.compile(rf"\.{escaped_name}\.replacement-\d+-\d+"),
        # Older releases used a visible timestamped backup directory.
        re.compile(rf"{escaped_name}\.backup-\d{{8}}-\d{{6}}"),
    )
    try:
        children = list(parent.iterdir())
    except OSError:
        return [], {}

    removed: list[Path] = []
    failed: dict[Path, str] = {}
    for child in children:
        if not any(pattern.fullmatch(child.name) for pattern in patterns):
            continue
        try:
            if child.is_symlink() or not child.is_dir():
                continue
            _retry_file_operation(lambda child=child: shutil.rmtree(child))
            removed.append(child)
        except OSError as exc:
            failed[child] = f"{type(exc).__name__}: {exc}"
    return removed, failed


def _current_plan_contract(plan: dict[str, Any]) -> tuple[str, str]:
    """Return the install contract and app version expected by this Wisp."""
    from core import optional_deps, updater

    display_name = str(plan.get("display_name") or "Optional package")
    spec_key = str(plan.get("spec_key") or _spec_key_for_display_name(display_name))
    spec_device = (
        plan.get("kokoro_install_device")
        if spec_key == "kokoro"
        else plan.get("stt_device")
        if spec_key == "stt"
        else None
    )
    contract = optional_deps.optional_package_contract(
        spec_key,
        device=str(spec_device) if spec_device is not None else None,
    )
    return contract, updater.current_version()


def resume_pending_staged_applies() -> int:
    """Re-arm apply helpers for staged installs that were never applied.

    Called at Wisp startup. An apply helper that gave up waiting (or died with
    the machine) leaves its staging directory and apply plan behind; arming a
    fresh helper lets the staged packages land at the next shutdown instead of
    forcing the user to reinstall.
    """
    from core import updater

    resumed = 0
    for plan_path in pending_apply_plan_paths():
        try:
            plan = _load_plan(plan_path)
            staging = Path(str(plan.get("staging_path") or "")).expanduser()
            if not str(plan.get("staging_path") or "") or not staging.is_dir():
                plan_path.unlink(missing_ok=True)
                continue
            current_contract, current_app_version = _current_plan_contract(plan)
            if (
                str(plan.get("install_contract") or "") != current_contract
                or str(plan.get("app_version") or "") != current_app_version
            ):
                # Never apply packages staged by an older dependency/checker
                # contract after an application update.  That was the path by
                # which a canceled v0.9 install could unexpectedly land later.
                shutil.rmtree(staging, ignore_errors=True)
                plan_path.unlink(missing_ok=True)
                raw_status_path = str(plan.get("status_path") or "").strip()
                status_path = Path(raw_status_path).expanduser() if raw_status_path else None
                _write_status(
                    status_path,
                    ok=False,
                    message=(
                        f"{plan.get('display_name') or 'Optional package'} staged install was discarded because "
                        "Wisp or its dependency contract changed. Run the installer again."
                    ),
                    extra={
                        "install_contract": current_contract,
                        "app_version": current_app_version,
                    },
                )
                continue
            helper_pid = int(plan.get("helper_pid") or 0)
            if helper_pid and updater.process_exists(helper_pid):
                continue
            plan["wait_pid"] = updater.wisp_wait_pid()
            # A re-armed apply runs at whatever shutdown comes next, possibly
            # hours later; popping Wisp back open then would be intrusive.
            plan["reopen_after_apply"] = False
            _write_plan(plan_path, plan)
            _launch_staged_apply(plan_path)
            resumed += 1
        except Exception:
            continue
    return resumed


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
    _write_status(
        status_path,
        ok=None,
        message=f"{display_name} install is staging packages before restart.",
        extra=_status_extra(plan, progress_percent=2),
    )

    with log_path.open("a", encoding="utf-8") as log:
        _log(log, prefix, f"Installing into staging folder: {staging_path}")
        if pre_install_packages:
            _write_status(
                status_path,
                ok=None,
                message=f"{display_name} install is resolving and downloading GPU runtime packages.",
                extra=_status_extra(plan, progress_percent=5),
            )
            _log(log, prefix, "Installing CUDA Torch into staging before Kokoro packages.")
            returncode, failure_detail = _run_install_phase(
                log,
                prefix,
                pre_install_packages,
                reinstall=True,
                target_dir=staging_path,
            )
            if returncode != 0:
                _write_status(
                    status_path,
                    ok=False,
                    message=failure_detail or f"{display_name} install failed during staged CUDA Torch install.",
                    extra=_plan_status_metadata(plan),
                )
                return returncode
            _write_status(
                status_path,
                ok=None,
                message=f"{display_name} GPU runtime packages are staged.",
                extra=_status_extra(plan, progress_percent=40),
            )
        if packages:
            package_start_percent = 45 if pre_install_packages else 10
            _write_status(
                status_path,
                ok=None,
                message=f"{display_name} install is resolving and downloading locked packages.",
                extra=_status_extra(plan, progress_percent=package_start_percent),
            )
            returncode, failure_detail = _run_install_phase(
                log,
                prefix,
                packages,
                reinstall=reinstall,
                target_dir=staging_path,
            )
            if returncode != 0:
                _write_status(
                    status_path,
                    ok=False,
                    message=failure_detail or f"{display_name} install failed during staged package install.",
                    extra=_plan_status_metadata(plan),
                )
                return returncode
            _write_status(
                status_path,
                ok=None,
                message=f"{display_name} package download is complete; preparing the staged apply.",
                extra=_status_extra(plan, progress_percent=90),
            )

        apply_plan_path = log_path.with_suffix(".apply-plan.json")
        apply_plan = {
            **plan,
            "staging_path": str(staging_path),
            "target_path": str(optional_deps.OPTIONAL_PACKAGES_DIR),
            "wait_pid": int(plan.get("wait_pid") or updater.wisp_wait_pid()),
            "log_path": str(log_path),
            "status_path": str(status_path) if status_path else "",
            "reopen_after_apply": True,
        }
        _write_plan(apply_plan_path, apply_plan)
        _log(log, prefix, "Staged packages downloaded. Restart Wisp to replace locked package files.")
        _write_status(
            status_path,
            ok=None,
            message=(
                f"{display_name} packages are staged. Click Restart app now to close Wisp, "
                "replace locked files, verify the install, and reopen."
            ),
            extra=_status_extra(plan, restart_apply=True, progress_percent=100),
        )
        _launch_staged_apply(apply_plan_path)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", help="Path to the installer plan JSON.")
    parser.add_argument("--apply-plan", help="Path to a staged apply plan JSON.")
    args = parser.parse_args()

    if args.apply_plan:
        return _run_staged_apply(Path(args.apply_plan).expanduser().resolve())
    if not args.plan:
        parser.error("--plan is required unless --apply-plan is used")

    plan_path = Path(args.plan).expanduser().resolve()
    plan = _load_plan(plan_path)
    _apply_plan_language(plan)
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
        if bool(plan.get("restart_apply")):
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
        _write_status(
            status_path,
            ok=None,
            message=f"{display_name} install is running.",
            extra=_plan_status_metadata(plan),
        )
        _remove_artifacts(log, prefix, remove_artifacts)
        _remove_stale_install_artifacts(log, prefix, [*pre_install_packages, *packages])
        _remove_duplicate_dist_infos(log, prefix)
        if pre_install_packages:
            _log(log, prefix, "Installing CUDA Torch before Kokoro packages.")
            returncode, failure_detail = _run_install_phase(log, prefix, pre_install_packages, reinstall=True)
            if returncode != 0:
                _write_status(
                    status_path,
                    ok=False,
                    message=failure_detail or f"{display_name} install failed during CUDA Torch install.",
                    extra=_plan_status_metadata(plan),
                )
                return returncode
        if packages:
            returncode, failure_detail = _run_install_phase(log, prefix, packages, reinstall=reinstall)
            if returncode != 0:
                _write_status(
                    status_path,
                    ok=False,
                    message=failure_detail or f"{display_name} install failed during package install.",
                    extra=_plan_status_metadata(plan),
                )
                return returncode

        _warn_duplicate_dist_infos(log, prefix)
        ok, message = _post_install_result(log, prefix, plan, status_path)
        _log(log, prefix, message if not ok else "Completed successfully.")
        _write_status(status_path, ok=ok, message=message, extra=_plan_status_metadata(plan))
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
