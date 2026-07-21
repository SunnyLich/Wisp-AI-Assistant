"""Create a bounded, redacted diagnostic bundle that users can review and share."""
from __future__ import annotations

import json
import platform
import re
import sys
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from core.privacy_redaction import redact_text
from core.system.paths import USER_DATA_DIR

_MAX_LOG_FILES = 24
_MAX_LOG_BYTES = 512 * 1024
_LOG_SUFFIXES = {".log", ".crash", ".txt"}


def _safe_version() -> str:
    try:
        from core.updater import current_version

        return current_version()
    except Exception:
        return "unknown"


def _candidate_logs(log_root: Path) -> list[Path]:
    if not log_root.is_dir():
        return []
    candidates: list[Path] = []
    try:
        for path in log_root.rglob("*"):
            try:
                if path.is_file() and path.suffix.casefold() in _LOG_SUFFIXES:
                    candidates.append(path)
            except OSError:
                continue
    except OSError:
        return []
    candidates.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    return candidates[:_MAX_LOG_FILES]


def _read_tail(path: Path) -> tuple[str, bool]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        truncated = size > _MAX_LOG_BYTES
        if truncated:
            handle.seek(-_MAX_LOG_BYTES, 2)
        raw = handle.read(_MAX_LOG_BYTES)
    return raw.decode("utf-8", errors="replace"), truncated


def _path_replacements(repo_root: Path) -> list[tuple[str, str]]:
    values = (
        (str(repo_root.resolve()), "[WISP_DATA]"),
        (str(USER_DATA_DIR.resolve()), "[USER_DATA]"),
        (str(Path.home().resolve()), "[HOME]"),
    )
    replacements: list[tuple[str, str]] = []
    for raw, label in values:
        if not raw:
            continue
        replacements.append((raw, label))
        replacements.append((raw.replace("\\", "/"), label))
    return sorted(set(replacements), key=lambda item: len(item[0]), reverse=True)


def _sanitize_text(text: str, replacements: Iterable[tuple[str, str]]) -> str:
    sanitized = str(text or "")
    for raw, label in replacements:
        sanitized = re.sub(re.escape(raw), label, sanitized, flags=re.IGNORECASE)
    return redact_text(sanitized)


def create_crash_report(
    *,
    output_dir: Path | None = None,
    log_root: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Create and return a ZIP containing safe metadata and recent redacted logs."""
    created = (now or datetime.now(UTC)).astimezone(UTC)
    destination = Path(output_dir or (USER_DATA_DIR / "crash_reports"))
    destination.mkdir(parents=True, exist_ok=True)
    root = Path(log_root or (USER_DATA_DIR / "build_logs"))
    if log_root is None and not root.exists():
        # Development checkouts keep runtime logs beside the repository rather
        # than in the stable per-user data folder.
        from runtime.bootstrap import repo_root

        root = repo_root() / "build_logs"

    stamp = created.strftime("%Y%m%d-%H%M%S-%f")
    report_path = destination / f"wisp-crash-report-{stamp}.zip"
    replacements = _path_replacements(root.parent)
    included: list[dict[str, object]] = []
    failures: list[str] = []

    with zipfile.ZipFile(report_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for index, source in enumerate(_candidate_logs(root), start=1):
            try:
                text, truncated = _read_tail(source)
                safe_text = _sanitize_text(text, replacements)
                relative = source.relative_to(root).as_posix()
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", relative).strip("_") or f"log-{index}.txt"
                archive_name = f"logs/{index:02d}-{safe_name}"
                if truncated:
                    safe_text = "[Only the final 512 KiB of this log is included.]\n" + safe_text
                archive.writestr(archive_name, safe_text)
                included.append(
                    {
                        "archive_path": archive_name,
                        "source_name": source.name,
                        "truncated": truncated,
                    }
                )
            except Exception as exc:
                failures.append(f"{source.name}: {type(exc).__name__}")

        metadata = {
            "format": 1,
            "created_utc": created.isoformat(),
            "wisp_version": _safe_version(),
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "frozen": bool(getattr(sys, "frozen", False)),
            },
            "included_logs": included,
            "collection_failures": failures,
            "privacy": (
                "Log text was passed through Wisp's deterministic privacy redactor and local paths were replaced. "
                "Chats, memory databases, settings files, environment variables, and keychain data are not collected. "
                "Review this archive before sharing because diagnostic messages can still contain user-provided text."
            ),
        }
        archive.writestr("report.json", json.dumps(metadata, indent=2, ensure_ascii=False))

    return report_path
