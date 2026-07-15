"""Install Wisp's optional local AI privacy detector and runtime."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path


def _print(message: str) -> None:
    print(message, flush=True)


def _write_status(path: Path | None, *, ok: bool | None, message: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ok": ok, "message": message, "updated_at": time.time()}
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def install(status_path: Path | None = None) -> int:
    # These must be set before importing huggingface_hub. The Wisp installer
    # provides its own progress and error reporting, so Hub warnings/progress
    # would only duplicate or obscure the useful status lines.
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    from core import optional_deps
    from core.privacy_model import (
        MODEL_FILES,
        MODEL_REPO,
        RUNTIME_PACKAGES,
        model_dir,
        model_status,
        runtime_dir,
    )

    _write_status(status_path, ok=None, message="Preparing advanced privacy detection.")
    _print("Preparing the optional local privacy runtime.")
    optional_deps.ensure_pip_available()
    target = model_dir()
    target.mkdir(parents=True, exist_ok=True)
    command = optional_deps.pip_install_command(
        list(RUNTIME_PACKAGES),
        target_dir=runtime_dir(target),
    )
    completed = subprocess.run(command, check=False, env=optional_deps.pip_install_env())
    if completed.returncode != 0:
        message = f"Local privacy runtime installation failed with exit code {completed.returncode}."
        _write_status(status_path, ok=False, message=message)
        _print(message)
        return completed.returncode or 1

    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:  # noqa: BLE001
        message = f"Hugging Face downloader is unavailable: {type(exc).__name__}: {exc}"
        _write_status(status_path, ok=False, message=message)
        _print(message)
        return 1

    _write_status(status_path, ok=None, message="Downloading the advanced privacy model (about 2.8 GB).")
    _print("Downloading OpenAI Privacy Filter (about 2.8 GB). This may take a while.")
    try:
        snapshot_download(
            repo_id=MODEL_REPO,
            local_dir=str(target),
            allow_patterns=list(MODEL_FILES),
        )
    except Exception as exc:  # noqa: BLE001
        message = f"Privacy model download failed: {type(exc).__name__}: {exc}"
        _write_status(status_path, ok=False, message=message)
        _print(message)
        return 1

    status = model_status(target)
    if not status["valid"]:
        message = "Privacy model verification failed; missing: " + ", ".join(status["missing"])
        _write_status(status_path, ok=False, message=message)
        _print(message)
        return 1
    message = "Advanced privacy detection installed and verified."
    _write_status(status_path, ok=True, message=message)
    _print(message)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-path", default="")
    args = parser.parse_args()
    return install(Path(args.status_path) if args.status_path else None)


if __name__ == "__main__":
    raise SystemExit(main())
