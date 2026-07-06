"""Run optional speech package installs in a standalone terminal."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
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


def _write_status(path: Path | None, *, ok: bool | None, message: str) -> None:
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


def _run_install_command(log, prefix: str, packages: list[str], *, reinstall: bool = False) -> int:
    """Run one optional package install command, streaming output to the terminal."""
    from core import optional_deps

    try:
        optional_deps.ensure_pip_available()
        command = optional_deps.pip_install_command(packages, reinstall=reinstall)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="Path to the installer plan JSON.")
    args = parser.parse_args()

    from core import optional_deps

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
        _write_status(status_path, ok=None, message=f"{display_name} install is running.")
        _remove_artifacts(log, prefix, remove_artifacts)
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

        optional_deps.add_optional_packages_to_path()
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
                    message = f"Kokoro installed, but runtime verification failed: {detail}"
                    _log(log, prefix, message)
                    _write_status(status_path, ok=False, message=message)
                    return 1
                torch_status = optional_deps.kokoro_torch_status()
                if torch_status.get("error") or torch_status.get("valid") is False:
                    detail = str(torch_status.get("error") or "Torch verification failed.")
                    message = f"Kokoro installed, but Torch verification failed: {detail}"
                    _log(log, prefix, message)
                    _write_status(status_path, ok=False, message=message)
                    return 1
                if require_gpu and not torch_status.get("cuda_available"):
                    message = "Kokoro installed, but CUDA Torch verification failed."
                    _log(log, prefix, message)
                    _write_status(status_path, ok=False, message=message)
                    return 1
            except Exception as exc:
                message = (
                    "Kokoro installed, but local voice preparation failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                _log(log, prefix, message)
                _write_status(status_path, ok=False, message=message)
                return 1
        elif post_install == "stt_prepare":
            model = str(plan.get("stt_model") or "base")
            device = str(plan.get("stt_device") or "auto")
            compute_type = str(plan.get("stt_compute_type") or "int8")
            _write_status(status_path, ok=None, message=f"Installing STT: downloading or loading Whisper model {model}.")
            _log(log, prefix, f"Downloading or loading STT model {model} on {device} ({compute_type}).")
            status = optional_deps.stt_model_status_subprocess(model, device, compute_type)
            if status.get("error") or status.get("valid") is False:
                detail = str(status.get("error") or "STT model verification failed.")
                message = f"STT installed, but model verification failed: {detail}"
                _log(log, prefix, message)
                _write_status(status_path, ok=False, message=message)
                return 1
            resolved = f"{status.get('model') or model} on {status.get('device') or device} ({status.get('compute') or compute_type})"
            message = f"STT installed and model ready: {resolved}."
            _log(log, prefix, message)
            _write_status(status_path, ok=True, message=message)
            return 0

        _log(log, prefix, "Completed successfully.")
        _write_status(status_path, ok=True, message=f"{display_name} installed successfully.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
