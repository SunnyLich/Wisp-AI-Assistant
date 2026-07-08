"""Shared plumbing for testlab checks.

A check is a standalone script that imports this module, does its real-world
work, and finishes with exactly one ``LAB_RESULT: {json}`` line via
``finish()``. The orchestrator (testlab/run.py) parses that line; everything
else the check prints is log detail for the per-check log file.
"""
from __future__ import annotations

import faulthandler
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

TESTLAB_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTLAB_DIR.parent
ARTIFACTS_DIR = TESTLAB_DIR / ".artifacts"

RESULT_PREFIX = "LAB_RESULT: "

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


def bootstrap() -> None:
    """Prepare a check process: repo imports, faulthandler, sane cwd."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    faulthandler.enable()
    os.chdir(REPO_ROOT)
    os.environ.setdefault("PYTHONUNBUFFERED", "1")


def log(message: str) -> None:
    """Print one console-safe log line (legacy Windows code pages survive)."""
    text = str(message)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding), flush=True)


def finish(status: str, detail: str, **extra: Any) -> int:
    """Emit the machine-readable result line and return the exit code."""
    payload = {"status": status, "detail": str(detail)}
    if extra:
        payload["extra"] = extra
    log(RESULT_PREFIX + json.dumps(payload, default=str))
    return 0 if status in (PASS, SKIP) else 1


def parse_result(output: str) -> dict[str, Any] | None:
    """Find the last LAB_RESULT line in a check's combined output."""
    found: dict[str, Any] | None = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith(RESULT_PREFIX):
            try:
                found = json.loads(line[len(RESULT_PREFIX):])
            except ValueError:
                continue
    return found


def scratch_dir(tag: str, *, fresh: bool = True) -> Path:
    """Return a per-check scratch directory under testlab/.artifacts."""
    path = ARTIFACTS_DIR / tag
    if fresh and path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def isolated_repo_root(tag: str) -> Path:
    """Scratch WISP_REPO_ROOT with the user's .env copied in.

    Children pointed here keep the user's provider settings (from .env; OS
    keychain secrets are machine-level and unaffected) while memory/, chats/
    and other writes stay out of the real repo root.
    """
    root = scratch_dir(f"{tag}_repo_root")
    real_env = REPO_ROOT / ".env"
    if real_env.exists():
        shutil.copy2(real_env, root / ".env")
    return root


def env_overrides(
    *,
    isolated_root: Path | None = None,
    offscreen_ui: bool = False,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Env var overrides for children (WorkerSpec.env or subprocess env update).

    WISP_REPO_ROOT redirects data (memory/, chats/, .env) to a scratch dir, but
    in dev mode workers also resolve their *code* under it — so the real repo
    root and runtime/brain go on PYTHONPATH for imports to fall through to.
    """
    overrides: dict[str, str] = {
        "PYTHONUNBUFFERED": "1",
        "PYTHONFAULTHANDLER": "1",
    }
    path_parts = [str(REPO_ROOT), str(REPO_ROOT / "runtime" / "brain")]
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        path_parts.append(existing)
    overrides["PYTHONPATH"] = os.pathsep.join(path_parts)
    if isolated_root is not None:
        overrides["WISP_REPO_ROOT"] = str(isolated_root)
    if offscreen_ui:
        overrides["QT_QPA_PLATFORM"] = "offscreen"
    if extra:
        overrides.update(extra)
    return overrides


def child_env(
    *,
    isolated_root: Path | None = None,
    offscreen_ui: bool = False,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Full environment for subprocesses spawned directly by a check."""
    env = os.environ.copy()
    env.update(
        env_overrides(isolated_root=isolated_root, offscreen_ui=offscreen_ui, extra=extra)
    )
    return env


class Stopwatch:
    """Tiny timer for check phase reporting."""

    def __init__(self) -> None:
        self._start = time.monotonic()

    def lap(self) -> float:
        return round(time.monotonic() - self._start, 2)
