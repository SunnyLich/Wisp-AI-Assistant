"""Central runtime event log aggregating every user-visible log surface.

The supervisor owns one :class:`RuntimeEventLog`. Everything that used to be
visible in only one place — worker stderr, bubble notices, installer status
files, setup-check results, supervisor log records — is appended here as a
structured event and rendered by the Runtime Status window:

    {"seq": 12, "ts": 1752e9, "source": "brain", "severity": "error",
     "title": "brain encountered an error: ValueError: bad model",
     "detail": "Traceback (most recent call last): ...", "count": 1}

``title`` is the always-visible first line; ``detail`` holds the lines that
stay collapsed until the user expands the entry.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

_FLUSH_THREAD_NAME = "wisp-runtime-log-flush"
_MAX_EVENTS = 500
_MAX_TITLE_CHARS = 500
_MAX_DETAIL_CHARS = 8000
_COALESCE_WINDOW_SECONDS = 10.0
_FLUSH_INTERVAL_SECONDS = 0.5
# A held (complete) traceback block waits this long for a chained
# "During handling of ..." continuation before it becomes an event.
_TRACEBACK_HOLD_SECONDS = 1.0
# An unterminated traceback block is force-finalized after this much silence.
_TRACEBACK_IDLE_SECONDS = 3.0

_SEVERITIES = ("info", "warning", "error")

_TRACEBACK_CHAIN_MARKERS = (
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
)

# Tokens that mark a single stderr line as more severe than plain output.
_LINE_ERROR_TOKENS = ("[error]", " error ", "error:", "critical:", "fatal:", "exception:", "[critical]")
_LINE_WARNING_TOKENS = ("[warning]", " warning ", "warning:", "[warn]", "warn:")


def normalize_severity(value: str) -> str:
    """Map arbitrary severity labels onto info/warning/error."""
    name = str(value or "").strip().lower()
    if name in {"error", "err", "critical", "fatal"}:
        return "error"
    if name in {"warning", "warn"}:
        return "warning"
    return "info"


def _line_severity(line: str) -> str:
    """Guess the severity of one plain stderr line."""
    lowered = f" {line.lower()} "
    if any(token in lowered for token in _LINE_ERROR_TOKENS):
        return "error"
    if any(token in lowered for token in _LINE_WARNING_TOKENS):
        return "warning"
    return "info"


class _StderrFolder:
    """Fold one worker's stderr lines, grouping tracebacks into single events.

    A multi-line Python traceback becomes one error event whose title is
    "<worker> encountered an error: <exception line>" and whose detail is the
    full traceback, so the Runtime Status window can collapse it behind the
    headline the way a user expects.
    """

    def __init__(self, log: RuntimeEventLog, source: str) -> None:
        self._log = log
        self._source = source
        self._lock = threading.Lock()
        self._buffer: list[str] = []
        # idle -> collecting (inside a traceback) -> holding (saw the
        # exception line; waiting briefly for a chained traceback marker).
        self._state = "idle"
        self._last_line_at = 0.0

    def __call__(self, line: str) -> None:
        """Consume one stderr line (called from the worker's stderr thread)."""
        text = str(line or "").rstrip()
        if not text.strip():
            return
        with self._lock:
            self._last_line_at = time.monotonic()
            if self._state == "holding":
                if text.strip() in _TRACEBACK_CHAIN_MARKERS:
                    self._buffer.append(text)
                    self._state = "collecting"
                    return
                self._finalize_locked()
            if self._state == "collecting":
                self._buffer.append(text)
                if not text.startswith((" ", "\t")) and not text.startswith("Traceback ("):
                    self._state = "holding"
                self._log._schedule_flush()
                return
            if text.startswith("Traceback (most recent call last):"):
                self._buffer = [text]
                self._state = "collecting"
                self._log._schedule_flush()
                return
        self._log.append(self._source, _line_severity(text), text)

    def flush_idle(self, now: float | None = None) -> None:
        """Finalize buffered blocks that stopped growing."""
        current = time.monotonic() if now is None else now
        with self._lock:
            if not self._buffer:
                return
            idle = current - self._last_line_at
            if self._state == "holding" and idle >= _TRACEBACK_HOLD_SECONDS:
                self._finalize_locked()
            elif self._state == "collecting" and idle >= _TRACEBACK_IDLE_SECONDS:
                self._finalize_locked()

    def has_pending(self) -> bool:
        """Return whether a buffered block is still waiting to finalize.

        Deliberately lockless: this is an advisory read used to decide whether
        the flush timer needs to re-arm. Callers that race a concurrent line
        are safe because that line's own ``__call__`` re-arms the timer.
        """
        return bool(self._buffer)

    def _finalize_locked(self) -> None:
        """Emit the buffered traceback block as one grouped error event."""
        buffer, self._buffer = self._buffer, []
        self._state = "idle"
        if not buffer:
            return
        headline = buffer[-1].strip()
        for candidate in reversed(buffer):
            text = candidate.strip()
            if text and not text.startswith(("File ", "Traceback (")) and text not in _TRACEBACK_CHAIN_MARKERS:
                headline = text
                break
        title = f"{self._source} encountered an error: {headline}"
        self._log.append(self._source, "error", title, detail="\n".join(buffer))


class RuntimeEventLog:
    """Thread-safe bounded log of runtime events with optional live publishing."""

    def __init__(self, *, max_events: int = _MAX_EVENTS) -> None:
        """Initialize the runtime event log instance."""
        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._seq = 0
        self._folders: dict[str, _StderrFolder] = {}
        self._publisher: Callable[[list[dict[str, Any]]], None] | None = None
        self._publish_enabled = False
        self._last_published_seq = 0
        self._flush_timer: threading.Timer | None = None
        self._closed = False
        self._ingested_installer_statuses: dict[str, float] = {}

    # -- ingestion -----------------------------------------------------

    def append(self, source: str, severity: str, title: str, detail: str = "") -> dict[str, Any]:
        """Append one event; identical unpublished repeats bump a counter."""
        clean_title = " ".join(str(title or "").split()) or "(empty)"
        if len(clean_title) > _MAX_TITLE_CHARS:
            clean_title = clean_title[: _MAX_TITLE_CHARS - 3] + "..."
        clean_detail = str(detail or "").strip()
        if len(clean_detail) > _MAX_DETAIL_CHARS:
            clean_detail = clean_detail[-_MAX_DETAIL_CHARS:]
        now = time.time()
        with self._lock:
            last = self._events[-1] if self._events else None
            if (
                last is not None
                and last["seq"] > self._last_published_seq
                and last["source"] == str(source)
                and last["severity"] == normalize_severity(severity)
                and last["title"] == clean_title
                and last["detail"] == clean_detail
                and now - last["ts"] <= _COALESCE_WINDOW_SECONDS
            ):
                last["count"] += 1
                last["ts"] = now
                event = last
            else:
                self._seq += 1
                event = {
                    "seq": self._seq,
                    "ts": now,
                    "source": str(source or "app"),
                    "severity": normalize_severity(severity),
                    "title": clean_title,
                    "detail": clean_detail,
                    "count": 1,
                }
                self._events.append(event)
        self._schedule_flush()
        return event

    def stderr_sink(self, source: str) -> Callable[[str], None]:
        """Return a line sink that folds *source*'s stderr into events."""
        with self._lock:
            folder = self._folders.get(source)
            if folder is None:
                folder = _StderrFolder(self, source)
                self._folders[source] = folder
        return folder

    def ingest_installer_statuses(self) -> int:
        """Pull new optional-installer results into the event log.

        Detached installers (speech packages, privacy model, staged applies)
        only write ``*.status.json`` and ``*-install.log`` files. Scanning them
        on demand puts "only in the installer" outcomes into Runtime Status.
        """
        ingested = 0
        for status_path in self._installer_status_files():
            key = str(status_path)
            try:
                mtime = status_path.stat().st_mtime
            except OSError:
                continue
            if self._ingested_installer_statuses.get(key) == mtime:
                continue
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - unreadable status is not an event
                continue
            if not isinstance(data, dict):
                continue
            self._ingested_installer_statuses[key] = mtime
            name = status_path.name.replace(".status.json", "").replace("-install", "")
            message = str(data.get("message") or "").strip() or "(no installer message)"
            ok = data.get("ok")
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(data.get("updated_at") or mtime)))
            if ok is False:
                detail = f"Status file: {status_path}\nFinished: {when}"
                log_tail = self._installer_log_tail(status_path)
                if log_tail:
                    detail += f"\nInstaller log tail:\n{log_tail}"
                self.append("installer", "error", f"{name} install failed: {message}", detail=detail)
            elif ok is True:
                self.append("installer", "info", f"{name} install finished: {message}", detail=f"Finished: {when}")
            else:
                self.append("installer", "info", f"{name} install in progress: {message}")
            ingested += 1
        return ingested

    @staticmethod
    def _installer_status_files() -> list[Path]:
        """Return every known installer status file, newest first."""
        roots: list[Path] = []
        run_log_dir = os.environ.get("WISP_RUN_LOG_DIR")
        if run_log_dir:
            roots.append(Path(run_log_dir).expanduser() / "installers")
        try:
            from core import optional_deps

            roots.append(optional_deps.OPTIONAL_PACKAGES_DIR.parent / "installers")
        except Exception:  # noqa: BLE001 - optional_deps may be unavailable in tests
            pass
        try:
            from core.privacy_model import model_dir

            roots.append(model_dir().parent / "installers")
        except Exception:  # noqa: BLE001 - privacy model dir is optional
            pass
        seen: set[str] = set()
        files: list[Path] = []
        for root in roots:
            try:
                candidates = list(root.glob("*.status.json"))
            except OSError:
                continue
            for path in candidates:
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                files.append(path)
        try:
            files.sort(key=lambda path: path.stat().st_mtime)
        except OSError:
            pass
        return files

    @staticmethod
    def _installer_log_tail(status_path: Path, max_lines: int = 40) -> str:
        """Return the tail of the installer log beside a status file."""
        log_path = status_path.with_name(status_path.name.replace(".status.json", ".log"))
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-max_lines:])

    # -- reading -------------------------------------------------------

    def snapshot(self) -> list[dict[str, Any]]:
        """Return every retained event (oldest first), flushing idle blocks."""
        for folder in list(self._folders.values()):
            folder.flush_idle()
        with self._lock:
            return [dict(event) for event in self._events]

    # -- live publishing -----------------------------------------------

    def set_publisher(self, publisher: Callable[[list[dict[str, Any]]], None] | None) -> None:
        """Set the callback that receives batches of new events."""
        with self._lock:
            self._publisher = publisher

    def enable_publishing(self) -> None:
        """Start pushing new events to the publisher (Runtime Status opened)."""
        with self._lock:
            self._publish_enabled = self._publisher is not None
            # Everything up to now travels in the full snapshot; live batches
            # must only carry events that arrive after the window opened.
            self._last_published_seq = self._seq
        self._schedule_flush()

    def disable_publishing(self) -> None:
        """Stop pushing events (Runtime Status closed or unreachable)."""
        with self._lock:
            self._publish_enabled = False

    def close(self) -> None:
        """Cancel pending flush work at shutdown."""
        with self._lock:
            self._closed = True
            timer = self._flush_timer
            self._flush_timer = None
        if timer is not None:
            timer.cancel()

    def _schedule_flush(self) -> None:
        """Arm the batch timer when there is pending work."""
        with self._lock:
            if self._closed or self._flush_timer is not None:
                return
            has_publish_work = self._publish_enabled and self._seq > self._last_published_seq
            # has_pending() is lockless, so no folder lock is taken while the
            # log lock is held (folders take folder-then-log, never reversed).
            has_folder_work = any(folder.has_pending() for folder in self._folders.values())
            if not has_publish_work and not has_folder_work:
                return
            timer = threading.Timer(_FLUSH_INTERVAL_SECONDS, self._flush)
            timer.name = _FLUSH_THREAD_NAME
            timer.daemon = True
            self._flush_timer = timer
        timer.start()

    def _flush(self) -> None:
        """Finalize idle stderr blocks and publish new events in one batch."""
        with self._lock:
            self._flush_timer = None
        for folder in list(self._folders.values()):
            folder.flush_idle()
        with self._lock:
            publisher = self._publisher if self._publish_enabled else None
            batch = [dict(event) for event in self._events if event["seq"] > self._last_published_seq]
            if publisher is not None and batch:
                self._last_published_seq = batch[-1]["seq"]
        if publisher is not None and batch:
            try:
                publisher(batch)
            except Exception:  # noqa: BLE001 - a dead window must not wedge logging
                self.disable_publishing()
        self._schedule_flush()


class RuntimeLogHandler(logging.Handler):
    """Mirror supervisor logging records into the runtime event log."""

    def __init__(self, runtime_log: RuntimeEventLog, *, level: int = logging.INFO) -> None:
        """Initialize the runtime log handler instance."""
        super().__init__(level=level)
        self._runtime_log = runtime_log

    def emit(self, record: logging.LogRecord) -> None:
        """Append one logging record as a runtime event."""
        if threading.current_thread().name == _FLUSH_THREAD_NAME:
            return  # never let publish-path logging feed back into the log
        if record.name == "wisp.worker_stderr":
            return  # stderr lines already arrive via on_stderr_line listeners
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - malformed log calls must not raise
            message = str(record.msg)
        lines = message.splitlines() or [""]
        detail_parts = lines[1:]
        if record.exc_info and record.exc_info[0] is not None:
            detail_parts.append("".join(traceback.format_exception(*record.exc_info)).rstrip())
        severity = "error" if record.levelno >= logging.ERROR else (
            "warning" if record.levelno >= logging.WARNING else "info"
        )
        try:
            self._runtime_log.append("supervisor", severity, lines[0], detail="\n".join(detail_parts))
        except Exception:  # noqa: BLE001 - logging must never raise
            pass
