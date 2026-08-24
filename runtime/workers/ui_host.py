"""openwand-ui worker: the only process allowed to own PySide6 widgets."""

from __future__ import annotations

import hashlib
import html
import itertools
import json
import logging
import math
import os
import queue
import re
import sys
import threading
import time
import traceback
from collections.abc import MutableMapping
from datetime import UTC
from pathlib import Path
from typing import Any

from runtime import VERSION, protocol
from runtime.bootstrap import configure_paths
from runtime.boundaries import boundary_status
from ui.action_i18n import localize_action_preview_html, translate_action_text
from ui.agent.activity_i18n import (
    translate_agent_activity_text,
    translate_agent_health_badge,
    translate_agent_health_detail,
    translate_agent_log_line,
    translate_agent_name,
    translate_agent_role,
    translate_agent_status,
)
from ui.i18n import localize_widget_tree, t
from ui.shared.theme import is_dark_mode, theme_colors

log = logging.getLogger("openwand.ui_host")

_CHAT_AUTO_ELABORATE_MAX_CHARS = 500


def _configure_linux_ui_platform(
    environment: MutableMapping[str, str] | None = None,
    *,
    platform: str | None = None,
) -> str:
    """Choose a placement-capable Qt backend for the floating Linux overlay.

    A Wayland client cannot position its own top-level windows, while OpenWand's
    icon, speech bubble, and context panel are deliberately positioned overlay
    windows. When XWayland is present, Qt's XCB backend provides the required
    placement contract. The native capture worker remains a separate process
    and continues to use Wayland/portal APIs.
    """
    env = environment if environment is not None else os.environ
    selected = str(env.get("QT_QPA_PLATFORM") or "").strip()
    if selected:
        return selected
    if not (platform or sys.platform).startswith("linux"):
        return ""

    preference = str(env.get("OPENWAND_UI_PLATFORM") or "auto").strip().lower()
    if preference in {"wayland", "native-wayland"}:
        return ""
    if preference in {"xcb", "x11", "xwayland"}:
        env["QT_QPA_PLATFORM"] = "xcb"
        return "xcb"
    if preference != "auto":
        return ""
    if env.get("WAYLAND_DISPLAY") and env.get("DISPLAY"):
        env["QT_QPA_PLATFORM"] = "xcb"
        return "xcb"
    return ""

_CONTEXT_SOURCE_LABELS = {
    "App",
    "Browser/Web",
    "Selection",
    "Clipboard",
    "Screenshot",
    "Git/GitHub",
    "Memory",
    "Files",
    "Attachments",
    "Context",
}

_STATUS_LABELS = {
    "pass": "PASS",
    "ok": "OK",
    "warn": "WARN",
    "fail": "FAIL",
}

_PRIVACY_CATEGORY_LABELS = {
    "account_number": "Account number",
    "address": "Address",
    "api_key": "API key",
    "bearer_token": "Bearer token",
    "card_number": "Card number",
    "credential": "Credential",
    "custom": "Custom pattern",
    "date": "Date",
    "drivers_license": "Driver's license",
    "email": "Email",
    "iban": "IBAN",
    "person": "Person",
    "phone": "Phone",
    "passport": "Passport",
    "private_key": "Private key",
    "secret": "Secret",
    "ssn": "SSN",
    "url": "URL",
    "url_credential": "URL credential",
}

_PRIVACY_SOURCE_LABELS = {
    "active_document": "Active document",
    "ambient": "App",
    "buffered_context": "Context",
    "clipboard": "Clipboard",
    "context": "Context",
    "instruction": "Instruction",
    "memory": "Memory",
    "prompt": "Prompt",
    "rewrite_text": "Selected text",
    "selection": "Selection",
}


def _context_display_label(label: str) -> str:
    """Translate built-in context source labels while preserving user labels."""
    text = str(label or "Context")
    return t(text) if text in _CONTEXT_SOURCE_LABELS else text


def _localized_context_items(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Translate built-in context item labels while preserving custom metadata."""
    localized: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        label = next_item.get("label")
        if label is None:
            label = next_item.get("name")
        next_item["label"] = _context_display_label(str(label or "Context"))
        localized.append(next_item)
    return localized


def _privacy_category_label(category: str) -> str:
    """Translate privacy report category keys into user-facing labels."""
    text = str(category or "Sensitive data").strip()
    label = _PRIVACY_CATEGORY_LABELS.get(text.lower(), text or "Sensitive data")
    return t(label)


def _privacy_source_label(source: str) -> str:
    """Translate privacy report source keys while preserving file/user labels."""
    text = str(source or "Context").strip()
    lowered = text.lower()
    if lowered.startswith("document:"):
        return f"{t('Document')}: {text.split(':', 1)[1].strip()}"
    if lowered.startswith("dropped:"):
        return f"{t('Dropped file')}: {text.split(':', 1)[1].strip()}"
    if lowered.startswith("history:"):
        return t("History")
    if lowered.startswith("message:"):
        return t("Chat message")
    if lowered.startswith("tool:"):
        return t("Tool result")
    if lowered in _PRIVACY_SOURCE_LABELS:
        return t(_PRIVACY_SOURCE_LABELS[lowered])
    return _context_display_label(text)


def _mac_status_text(status: str) -> str:
    """Handle mac status text for runtime workers UI host."""
    return translate_agent_status(status)


def _status_label(status: str) -> str:
    """Translate setup/health status labels."""
    return t(_STATUS_LABELS.get(str(status or "").strip().lower(), "WARN"))


def _runtime_event_headline(event: dict[str, Any]) -> str:
    """Render the always-visible first line of one runtime event."""
    ts = float(event.get("ts") or 0)
    stamp = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "--:--:--"
    severity = str(event.get("severity") or "info")
    marker = {"error": " ERROR", "warning": " WARN"}.get(severity, "")
    count = int(event.get("count") or 1)
    suffix = f" (x{count})" if count > 1 else ""
    return f"[{stamp}] [{event.get('source') or 'app'}]{marker} {event.get('title') or ''}{suffix}"


def _open_folder(path: str) -> None:
    """Open a folder in the OS file browser."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def _translate_health_value(value: str) -> str:
    """Translate known health value atoms while preserving provider/model names."""
    text = str(value or "")
    if text in {"None", "unavailable", "authorized", "denied", "not_determined", "restricted"}:
        return t(text)
    return text


def _translate_health_text(text: str) -> str:
    """Translate health rows, including dynamic provider/model values."""
    value = str(text or "")
    if "\n" in value:
        return "\n".join(_translate_health_text(part) for part in value.splitlines())

    dynamic_patterns: tuple[tuple[str, str], ...] = (
        (r"^LLM route configured: (?P<route>.+)\.$", "LLM route configured: {route}."),
        (r"^LLM route incomplete: (?P<route>.+)\.$", "LLM route incomplete: {route}."),
        (r"^TTS provider configured: (?P<provider>.+)\.$", "TTS provider configured: {provider}."),
        (
            r"^STT model configured: (?P<model>.+), but STT verification failed: (?P<error>.+)$",
            "STT model configured: {model}, but STT verification failed: {error}",
        ),
        (
            r"^STT packages and runtime are verified for (?P<model>.+); the model loads on first use\.$",
            "STT packages and runtime are verified for {model}; the model loads on first use.",
        ),
        (r"^STT model configured: (?P<model>.+)\.$", "STT model configured: {model}."),
        (r"^(?P<count>\d+) hotkeys configured\.$", "{count} hotkeys configured."),
        (r"^(?P<label>.+) worker responded\.$", "{label} worker responded."),
        (r"^(?P<label>.+) worker did not respond\.$", "{label} worker did not respond."),
        (r"^Accessibility permission: (?P<value>.+)\.$", "Accessibility permission: {value}."),
        (r"^Screen recording permission: (?P<value>.+)\.$", "Screen recording permission: {value}."),
        (r"^Microphone permission: (?P<value>.+)\.$", "Microphone permission: {value}."),
        (r"^TTS synthesis responded with (?P<provider>.+)\.$", "TTS synthesis responded with {provider}."),
        (r"^LLM test failed: (?P<message>.+)$", "LLM test failed: {message}"),
        (r"^LLM route uses (?P<provider>.+) but you are not logged in\.$", "LLM route uses {provider} but you are not logged in."),
    )
    for pattern, template in dynamic_patterns:
        match = re.match(pattern, value)
        if match:
            groups = match.groupdict()
            if "message" in groups:
                groups["message"] = _translate_health_text(groups["message"])
            if "error" in groups:
                groups["error"] = _translate_health_text(groups["error"])
            if "value" in groups:
                groups["value"] = _translate_health_value(groups["value"])
            return t(template).format(**groups)
    cuda_failure = re.fullmatch(
        r"Windows CUDA runtime is incomplete or unloadable(?:: (?P<files>.+))?",
        value,
    )
    if cuda_failure:
        files = cuda_failure.group("files")
        if files:
            return t("Windows CUDA runtime is incomplete or unloadable: {files}").format(
                files=files
            )
        return t("Windows CUDA runtime is incomplete or unloadable")
    return t(value)


# Bubble/notice lines whose prefix is fixed but whose tail is a dynamic error
# detail. Translate the prefix via the catalog and keep the detail verbatim, the
# same way _translate_health_text handles dynamic health rows.
_NOTICE_DYNAMIC_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"^Health issue: (?P<name>[^:]+): (?P<message>.+)$",
        "Health issue: {name}: {message}",
    ),
    (r"^LLM request failed: (?P<message>.+)$", "LLM request failed: {message}"),
    (r"^Rewrite failed: (?P<message>.+)$", "Rewrite failed: {message}"),
    (
        r"^OpenWand couldn't safely read that app selection: (?P<error>.+)$",
        "OpenWand couldn't safely read that app selection: {error}",
    ),
    (
        r"^Rewrite proposal was not safe to apply: (?P<error>.+)$",
        "Rewrite proposal was not safe to apply: {error}",
    ),
    (r"^Dictation failed: (?P<message>.+)$", "Dictation failed: {message}"),
    (r"^Couldn't start recording: (?P<message>.+)$", "Couldn't start recording: {message}"),
    (r"^Couldn't start dictation: (?P<message>.+)$", "Couldn't start dictation: {message}"),
    (r"^Could not read selected text: (?P<message>.+)$", "Could not read selected text: {message}"),
    (r"^Preparing local voice\.\.\. (?P<detail>.+)$", "Preparing local voice... {detail}"),
    (r"^Local speech warmup failed: (?P<message>.+)$", "Local speech warmup failed: {message}"),
    (r"^Running: (?P<detail>.+)$", "Running: {detail}"),
    (r"^ChatGPT started (?P<action>[^:]+): (?P<detail>.+)$", "ChatGPT started {action}: {detail}"),
    (r"^ChatGPT started (?P<action>.+)$", "ChatGPT started {action}"),
    (r"^ChatGPT (?P<action>[^:]+): (?P<status>.+)$", "ChatGPT {action}: {status}"),
    (r"^Claude started (?P<action>.+)$", "Claude started {action}"),
    (r"^Claude action: (?P<detail>.+)$", "Claude action: {detail}"),
    (r"^Claude event: (?P<detail>.+)$", "Claude event: {detail}"),
)


_SPEECH_NOTICE_LINE_RE = re.compile(
    r"^(?P<label>STT \(speech recognition\)|TTS(?: \((?P<tts_label>[^)]+)\))?): "
    r"(?P<status>.+)$"
)


def _translate_speech_elapsed(value: str) -> str:
    """Translate the compact supervisor duration while preserving its numbers."""
    match = re.fullmatch(r"(?:(?P<minutes>\d+)m )?(?P<seconds>\d+)s", value)
    if not match:
        return value
    minutes = match.group("minutes")
    seconds = match.group("seconds")
    if minutes is not None:
        return t("{minutes}m {seconds}s").format(minutes=minutes, seconds=seconds)
    return t("{seconds}s").format(seconds=seconds)


def _translate_speech_notice_line(line: str) -> str | None:
    """Translate a structured STT/TTS status line without translating diagnostics."""
    elapsed_match = re.match(
        r"^Preparing speech services - (?P<elapsed>.+) elapsed\.$",
        line,
    )
    if elapsed_match:
        return t("Preparing speech services - {elapsed} elapsed.").format(
            elapsed=_translate_speech_elapsed(elapsed_match.group("elapsed"))
        )

    match = _SPEECH_NOTICE_LINE_RE.match(line)
    if not match:
        return None
    label = match.group("label")
    tts_label = match.group("tts_label")
    if label == "STT (speech recognition)":
        translated_label = t("STT (speech recognition)")
    elif label == "TTS":
        translated_label = t("TTS")
    elif tts_label == "Kokoro local voice":
        translated_label = t("TTS (Kokoro local voice)")
    elif tts_label == "Cartesia connection":
        translated_label = t("TTS (Cartesia connection)")
    else:
        translated_label = t("TTS ({provider})").format(provider=tts_label or "")

    status = match.group("status")
    warming_match = re.match(r"^warming up \((?P<elapsed>.+)\)$", status)
    failure_match = re.match(r"^failed - (?P<message>.+)$", status)
    if warming_match:
        translated_status = t("warming up ({elapsed})").format(
            elapsed=_translate_speech_elapsed(warming_match.group("elapsed"))
        )
    elif failure_match:
        message = failure_match.group("message")
        if message == "unknown error":
            message = t("unknown error")
        translated_status = t("failed - {message}").format(
            message=message
        )
    else:
        translated_status = t(status)
    return t("{label}: {status}").format(
        label=translated_label,
        status=translated_status,
    )


def _translate_notice_line(line: str) -> str:
    """Translate one bubble/notice line: fixed prefix, dynamic error template, or
    a plain catalog lookup."""
    speech_line = _translate_speech_notice_line(line)
    if speech_line is not None:
        return speech_line
    for prefix in ("Installed addon: ", "Technical detail: "):
        if line.startswith(prefix):
            return t(prefix) + line[len(prefix):]
    for pattern, template in _NOTICE_DYNAMIC_PATTERNS:
        match = re.match(pattern, line)
        if match:
            groups = match.groupdict()
            if groups.get("status"):
                groups["status"] = t(groups["status"])
            if groups.get("name"):
                groups["name"] = _translate_health_text(groups["name"])
            if groups.get("message"):
                groups["message"] = _translate_health_text(groups["message"])
            return t(template).format(**groups)
    translated = translate_action_text(line)
    return translated if translated != line else t(line)


def _translate_notice_text(text: str) -> str:
    """Translate known system notice/bubble lines without touching arbitrary output."""
    lines = str(text or "").splitlines()
    if not lines:
        return t(str(text or ""))
    return "\n".join(_translate_notice_line(line) if line else line for line in lines)


def _is_speech_status_notice(text: str) -> bool:
    """Return True for non-error STT/TTS warmup/readiness notices."""
    lowered = " ".join(str(text or "").lower().split())
    if not lowered or "failed" in lowered or "error:" in lowered:
        return False
    status_terms = (
        "ready",
        "warming up",
        "warmup",
        "warm-up",
        "still warming",
        "preparing",
        "waiting to start",
        "will retry",
        "not needed",
        "stopped",
        "interrupted",
    )
    speech_terms = (
        "tts",
        "stt",
        "speech recognition",
        "speech model",
        "local speech",
        "local voice",
    )
    return any(term in lowered for term in status_terms) and any(
        term in lowered for term in speech_terms
    )


def _is_transient_local_tts_warmup_notice(text: str) -> bool:
    """Return True when Kokoro/TTS is busy importing, not actually broken."""
    lowered = " ".join(str(text or "").lower().split())
    if not lowered or "local speech is ready" not in lowered:
        return False
    if "still warming" not in lowered and "warming up" not in lowered:
        return False
    speech_terms = ("kokoro", "local tts", "local voice", "tts")
    return any(term in lowered for term in speech_terms)


def _bubble_has_active_prompt_reply(bubble: Any) -> bool:
    """Whether replacing the bubble would interrupt an active prompt reply."""
    if bubble is None:
        return False
    active = bool(
        getattr(bubble, "_thinking", False)
        or getattr(bubble, "_transcript_preview", False)
        or (
            int(getattr(bubble, "_reply_chunk_count", 0) or 0) > 0
            and bool(getattr(bubble, "_full_text", ""))
        )
    )
    if not active:
        return False
    is_visible = getattr(bubble, "isVisible", None)
    if callable(is_visible):
        try:
            return bool(is_visible())
        except Exception:
            return True
    return True


def _live_file_approval_summary(params: dict[str, Any]) -> tuple[str, str, str]:
    """Return action, path, and display text for live file approvals."""
    details = params.get("details") if isinstance(params.get("details"), dict) else {}
    action = str(params.get("action") or "file edit")
    path = str(params.get("path") or details.get("path") or "").strip()
    diff = str(params.get("diff") or details.get("diff") or "").strip()
    plus = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    minus = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    lines = [
        t("OpenWand wants permission to modify a local file."),
        t("Why: Files is set to ask before write, so OpenWand needs approval before changing disk."),
    ]
    if action:
        lines.append(f"{t('Tool:')} {action}")
    if path:
        lines.append(f"{t('Target:')} {path}")
    if "old_chars" in details or "new_chars" in details:
        lines.append(
            t("Change: replace {old} chars with {new} chars").format(
                old=int(details.get("old_chars") or 0),
                new=int(details.get("new_chars") or 0),
            )
        )
    elif "chars" in details:
        template = "Change: overwrite file with {chars} chars" if details.get("exists") else "Change: create file with {chars} chars"
        lines.append(t(template).format(chars=int(details.get("chars") or 0)))
    if diff:
        lines.append(t("Diff: +{added} -{removed} lines").format(added=plus, removed=minus))
    lines.append(t("Approve this file change?"))
    return action, path, "\n".join(lines)


def _ui_log_dir() -> Path:
    """Handle ui log dir for runtime workers UI host."""
    configured = os.environ.get("OPENWAND_RUN_LOG_DIR")
    if configured:
        root = Path(configured)
    else:
        root = configure_paths() / "build_logs" / "ui_runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _format_thread_stacks() -> str:
    """Format thread stacks."""
    frames = sys._current_frames()
    parts: list[str] = []
    for thread in threading.enumerate():
        parts.append(f"\n--- thread={thread.name} ident={thread.ident} daemon={thread.daemon} ---\n")
        frame = frames.get(thread.ident or -1)
        if frame is None:
            parts.append("(no frame)\n")
            continue
        parts.extend(traceback.format_stack(frame))
    return "".join(parts)


class QtFreezeWatchdog:
    """Background detector for a blocked Qt event loop.

    A QTimer updates the heartbeat on the main thread. If the heartbeat stops,
    the watchdog thread writes thread stacks to disk, which gives freeze reports
    something more useful than "it hung."
    """

    def __init__(self, app, status_fn) -> None:
        """Initialize the qt freeze watchdog instance."""
        from PySide6.QtCore import QTimer

        self._status_fn = status_fn
        self._threshold = float(os.environ.get("OPENWAND_UI_FREEZE_THRESHOLD_SECONDS", "2.5"))
        self._interval = float(os.environ.get("OPENWAND_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS", "0.5"))
        self._cooldown = max(self._threshold, float(os.environ.get("OPENWAND_UI_FREEZE_LOG_COOLDOWN_SECONDS", "10.0")))
        self._last_beat = time.monotonic()
        self._last_log = 0.0
        self._grace_until = 0.0
        self._last_drain_ticks = -1
        self._drain_progress = time.monotonic()
        self._last_ipc_log = 0.0
        self._stop = threading.Event()
        self._timer = QTimer()
        self._timer.setInterval(max(50, int(self._interval * 1000)))
        self._timer.timeout.connect(self.beat)
        self._timer.start()
        app.aboutToQuit.connect(self.stop)
        self._thread = threading.Thread(target=self._run, name="openwand-ui-freeze-watchdog", daemon=True)
        self._thread.start()

    def beat(self) -> None:
        """Handle beat for qt freeze watchdog."""
        self._last_beat = time.monotonic()

    def expect_slow(self, seconds: float) -> None:
        """Grant a one-off grace window for a deliberately slow main-thread op.

        A handler calls this immediately before a known-heavy *synchronous* build
        (e.g. first construction of the chat window). The background thread reads
        ``_grace_until`` while the main thread is blocked, so an expected cold-start
        stall is not mistaken for a real event-loop freeze. Genuine freezes outside
        a grace window still trip at the normal threshold.
        """
        self._grace_until = time.monotonic() + max(0.0, float(seconds))

    def stop(self) -> None:
        """Stop the freeze-watchdog timer and its background thread."""
        self._stop.set()
        self._timer.stop()

    def _run(self) -> None:
        """Background loop: detect when the Qt event loop stalls and report it."""
        while not self._stop.wait(self._interval):
            now = time.monotonic()
            stalled_for = now - self._last_beat
            status = self._status_fn() or {}

            # Track whether the IPC pump (_drain) is still advancing.
            ticks = int(status.get("drain_ticks", 0) or 0)
            if ticks != self._last_drain_ticks:
                self._last_drain_ticks = ticks
                self._drain_progress = now

            # Case 1: the Qt event loop itself is frozen (heartbeat stopped).
            if stalled_for >= self._threshold:
                if now < self._grace_until:
                    # A handler announced an expected slow operation (e.g. building
                    # the chat window). Hold off until the grace window elapses.
                    continue
                if now - self._last_log >= self._cooldown:
                    self._last_log = now
                    self._write_report("ui_freeze", "event_loop_frozen", stalled_for, status)
                continue

            # Case 2 (the blind spot): the event loop is alive but the IPC pump
            # has stopped draining while requests are queued - exactly the state
            # a 'ui.* timed out' with no freeze log points to.
            drain_stalled = now - self._drain_progress
            if (
                int(status.get("queue_depth", 0) or 0) > 0
                and drain_stalled >= self._threshold
                and now - self._last_ipc_log >= self._cooldown
            ):
                self._last_ipc_log = now
                self._write_report("ui_ipc_stall", "ipc_pump_stalled", drain_stalled, status)

    def _write_report(self, prefix: str, kind: str, stalled_for: float, status: dict) -> None:
        """Write report."""
        try:
            log_dir = _ui_log_dir()
            path = log_dir / f"{prefix}_{time.strftime('%Y%m%d-%H%M%S')}.log"
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            body = [
                f"time={time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                f"kind={kind}\n",
                f"pid={os.getpid()}\n",
                f"stalled_for_seconds={stalled_for:.3f}\n",
                f"active_method={status.get('method') or ''}\n",
                f"active_for_seconds={status.get('active_for_seconds') or 0:.3f}\n",
                f"queue_depth={status.get('queue_depth') or 0}\n",
                f"drain_ticks={status.get('drain_ticks') or 0}\n",
                "\nThread stacks:\n",
                _format_thread_stacks(),
            ]
            tmp_path.write_text("".join(body), encoding="utf-8")
            tmp_path.replace(path)
            print(f"[openwand-ui] watchdog wrote {path}", file=sys.stderr, flush=True)
        except Exception:
            log.exception("failed writing UI watchdog log")


class MemoryProxy:
    """Small UI-side cache that forwards memory mutations to the supervisor."""

    def __init__(self, emit_fn) -> None:
        """Initialize the memory proxy instance."""
        self._emit = emit_fn
        self._facts: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    def replace_facts(self, facts: list[dict[str, Any]] | None) -> None:
        """Handle replace facts for memory proxy."""
        self._facts = [dict(f) for f in (facts or [])]

    def get_all_facts(self) -> list[dict[str, Any]]:
        """Return all facts."""
        return [dict(f) for f in self._facts]

    def add_fact_manual(
        self, text: str, category: str = "general", project: str | None = None
    ) -> None:
        """Add fact manual."""
        project = (project or "").strip()
        if project:
            category = "project_context"
        fact = {
            "id": f"pending-{next(self._ids)}",
            "text": text,
            "category": category or "general",
            "source": "manual",
            "project": project,
        }
        self._facts.append(fact)
        self._emit("ui.memory.add", {"text": text, "category": category, "project": project})

    def update_fact(
        self,
        fact_id: str,
        text: str,
        category: str | None = None,
        project: str | None = None,
    ) -> None:
        """Update fact."""
        payload = {"id": fact_id, "text": text, "category": category}
        if project is not None:
            project = project.strip()
            payload["project"] = project
            category = "project_context" if project else "general"
            payload["category"] = category
        for fact in self._facts:
            if str(fact.get("id")) == str(fact_id):
                fact["text"] = text
                if category is not None:
                    fact["category"] = category
                if project is not None:
                    fact["project"] = project
                break
        self._emit("ui.memory.update", payload)

    def delete_fact(self, fact_id: str) -> None:
        """Delete fact."""
        self._facts = [f for f in self._facts if str(f.get("id")) != str(fact_id)]
        self._emit("ui.memory.delete", {"id": fact_id})


def _make_fit_graphics_view(QGraphicsView, Qt):
    """Create fit graphics view."""
    class _MacFitGraphicsView(QGraphicsView):
        """Model mac fit graphics view."""
        def __init__(self, scene):
            """Initialize the mac fit graphics view instance."""
            super().__init__(scene)
            self._zoom_factor = 1.0
            self._min_zoom = 0.45
            self._max_zoom = 3.0
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

        def fit_scene(self) -> None:
            """Handle fit scene for mac fit graphics view."""
            rect = self.scene().sceneRect() if self.scene() else None
            if rect is not None and not rect.isEmpty():
                self.resetTransform()
                self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
                if self._zoom_factor != 1.0:
                    self.scale(self._zoom_factor, self._zoom_factor)

        def wheelEvent(self, event):  # noqa: N802
            """Zoom the meeting room with the mouse wheel."""
            delta = event.angleDelta().y()
            if delta == 0:
                super().wheelEvent(event)
                return
            step = 1.15 if delta > 0 else 1 / 1.15
            self._zoom_factor = max(self._min_zoom, min(self._max_zoom, self._zoom_factor * step))
            self.fit_scene()
            event.accept()

        def resizeEvent(self, event):  # noqa: N802
            """Resize event."""
            super().resizeEvent(event)
            self.fit_scene()

        def showEvent(self, event):  # noqa: N802
            """Show event."""
            super().showEvent(event)
            self.fit_scene()

    return _MacFitGraphicsView


def _make_live_agent_item(
    QGraphicsEllipseItem,
    QGraphicsItemGroup,
    QGraphicsRectItem,
    QGraphicsTextItem,
    QBrush,
    QColor,
    QFont,
    QPen,
    QPointF,
    Qt,
    SpeakingRippleGraphicsItem,
    doll_assets_dir,
):
    """Create live agent item."""
    class _MacLiveAgentItem(QGraphicsItemGroup):
        """Model mac live agent item."""
        WIDTH = 220
        HEIGHT = 150
        TEXT_WIDTH = 196
        ACTIVITY_VFX_SIZE = 250
        GRIP = 16
        MIN_SCALE = 0.6
        MAX_SCALE = 2.2
        _CLICK_SLOP = 5

        def __init__(
            self,
            index: int,
            click_callback,
            x: float,
            y: float,
            name: str,
            role: str,
            status: str,
            objective: str,
            health: str,
            active: bool,
            selected: bool,
            scale: float = 1.0,
            on_geometry_change=None,
        ):
            """Initialize the mac live agent item instance."""
            super().__init__()
            self._index = index
            self._click_callback = click_callback
            self._on_geometry_change = on_geometry_change
            self._scale_factor = scale
            self._resizing = False
            self._press_scene = QPointF()
            self.setAcceptHoverEvents(True)
            self.setFlag(QGraphicsItemGroup.GraphicsItemFlag.ItemIsMovable, True)
            self.setFlag(QGraphicsItemGroup.GraphicsItemFlag.ItemSendsGeometryChanges, True)
            self.setZValue(5 if active or selected else 3)

            colors = theme_colors()
            border = colors["accent"] if active else colors["border"]
            fill = colors["accent_soft"] if active else colors["surface"]
            if selected:
                border = colors["accent_fill"]
                fill = colors["accent_strong"]

            self.activity_vfx = None
            if active:
                self.activity_vfx = SpeakingRippleGraphicsItem(self.ACTIVITY_VFX_SIZE)
                self.activity_vfx.set_vfx_sources(str(doll_assets_dir / "vfx"))
                self.activity_vfx.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self.activity_vfx.setPos(
                    (self.WIDTH - self.ACTIVITY_VFX_SIZE) / 2,
                    (self.HEIGHT - self.ACTIVITY_VFX_SIZE) / 2,
                )
                self.activity_vfx.setZValue(-2)
                self.activity_vfx.start()
                self.addToGroup(self.activity_vfx)

            shadow = QGraphicsEllipseItem(4, 6, self.WIDTH, self.HEIGHT)
            shadow.setBrush(QBrush(QColor(0, 0, 0, 64)))
            shadow.setPen(QPen(Qt.PenStyle.NoPen))
            shadow.setZValue(-1)
            self.addToGroup(shadow)

            node = QGraphicsRectItem(0, 0, self.WIDTH, self.HEIGHT)
            node.setBrush(QBrush(QColor(fill)))
            node.setPen(QPen(QColor(border), 2.2 if active or selected else 1.3))
            self.addToGroup(node)

            name_item = QGraphicsTextItem(name)
            name_item.setDefaultTextColor(QColor(colors["text"]))
            name_item.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            name_item.setTextWidth(self.TEXT_WIDTH)
            name_item.setPos(12, 10)
            name_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.addToGroup(name_item)

            role_item = QGraphicsTextItem(role)
            role_item.setDefaultTextColor(QColor(colors["text_dim"]))
            role_item.setFont(QFont("Segoe UI", 8))
            role_item.setTextWidth(self.TEXT_WIDTH)
            role_item.setPos(12, 32)
            role_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.addToGroup(role_item)

            status_item = QGraphicsTextItem(_mac_status_text(status or "Waiting"))
            status_item.setDefaultTextColor(QColor(colors["accent"] if active else colors["label"]))
            status_item.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold if active else QFont.Weight.Normal))
            status_item.setTextWidth(self.TEXT_WIDTH)
            status_item.setPos(12, 54)
            status_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.addToGroup(status_item)

            objective_item = QGraphicsTextItem(objective or t("No current objective"))
            objective_item.setDefaultTextColor(QColor(colors["label"]))
            objective_item.setFont(QFont("Segoe UI", 7))
            objective_item.setTextWidth(self.TEXT_WIDTH)
            objective_item.setPos(12, 78)
            objective_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.addToGroup(objective_item)

            health_item = QGraphicsTextItem(health)
            health_item.setDefaultTextColor(QColor(colors["text_dim"]))
            health_item.setFont(QFont("Segoe UI", 7))
            health_item.setTextWidth(self.TEXT_WIDTH)
            health_item.setPos(12, 122)
            health_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.addToGroup(health_item)

            grip = QGraphicsRectItem(self.WIDTH - self.GRIP, self.HEIGHT - self.GRIP, self.GRIP, self.GRIP)
            grip.setBrush(QBrush(QColor(border)))
            grip.setPen(QPen(QColor(colors["on_accent"]), 1))
            grip.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.addToGroup(grip)

            self.setScale(scale)
            self.setPos(x, y)

        def hoverEnterEvent(self, event):  # noqa: N802
            """Handle hover enter event for mac live agent item."""
            self.setOpacity(0.88)
            super().hoverEnterEvent(event)

        def hoverLeaveEvent(self, event):  # noqa: N802
            """Handle hover leave event for mac live agent item."""
            self.setOpacity(1.0)
            super().hoverLeaveEvent(event)

        def _in_grip(self, event) -> bool:
            """Handle in grip for mac live agent item."""
            pos = event.pos()
            return pos.x() >= self.WIDTH - self.GRIP and pos.y() >= self.HEIGHT - self.GRIP

        def _emit_geometry(self) -> None:
            """Emit geometry."""
            if self._on_geometry_change is not None:
                pos = self.pos()
                self._on_geometry_change(self._index, pos.x(), pos.y(), self._scale_factor)

        def mousePressEvent(self, event):  # noqa: N802
            """Handle mouse press event for mac live agent item."""
            self._press_scene = event.scenePos()
            if self._in_grip(event):
                self._resizing = True
                event.accept()
                return
            self._resizing = False
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event):  # noqa: N802
            """Handle mouse move event for mac live agent item."""
            if self._resizing:
                origin = self.scenePos()
                dx = event.scenePos().x() - origin.x()
                dy = event.scenePos().y() - origin.y()
                raw = max(dx / self.WIDTH, dy / self.HEIGHT)
                self._scale_factor = max(self.MIN_SCALE, min(self.MAX_SCALE, raw))
                self.setScale(self._scale_factor)
                event.accept()
                return
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):  # noqa: N802
            """Handle mouse release event for mac live agent item."""
            if self._resizing:
                self._resizing = False
                event.accept()
                self._emit_geometry()
                return
            super().mouseReleaseEvent(event)
            moved = (event.scenePos() - self._press_scene).manhattanLength() >= self._CLICK_SLOP
            event.accept()
            if moved:
                self._emit_geometry()
            else:
                self._click_callback(self._index)

    return _MacLiveAgentItem


class MacAgentRunDialog:
    """Protocol-backed agent run window for the pure-Python target."""

    def __init__(self, host: QtProtocolHost, spec: dict[str, Any]) -> None:
        """Initialize the mac agent run dialog instance."""
        from PySide6.QtCore import QPointF, Qt, QTimer, QUrl
        from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QPainterPath, QPen, QTextCursor
        from PySide6.QtWidgets import (
            QApplication,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QFrame,
            QGraphicsEllipseItem,
            QGraphicsItemGroup,
            QGraphicsRectItem,
            QGraphicsScene,
            QGraphicsTextItem,
            QGraphicsView,
            QHBoxLayout,
            QLabel,
            QMessageBox,
            QPushButton,
            QSplitter,
            QTabWidget,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )

        from core.system.paths import DOLL_ASSETS_DIR
        from ui.openwand_icon import SpeakingRippleGraphicsItem

        self._host = host
        self._spec = dict(spec or {})
        self._paused = False
        self._run_dir = ""
        self._pending_approval_id = ""
        self._QApplication = QApplication
        self._QDesktopServices = QDesktopServices
        self._QTextCursor = QTextCursor
        self._QTimer = QTimer
        self._QUrl = QUrl
        self._QBrush = QBrush
        self._QComboBox = QComboBox
        self._QDialog = QDialog
        self._QDialogButtonBox = QDialogButtonBox
        self._QFormLayout = QFormLayout
        self._QVBoxLayout = QVBoxLayout
        self._QColor = QColor
        self._QFont = QFont
        self._QMessageBox = QMessageBox
        self._QPainterPath = QPainterPath
        self._QPen = QPen
        self._Qt = Qt
        self._LiveAgentItem = _make_live_agent_item(
            QGraphicsEllipseItem,
            QGraphicsItemGroup,
            QGraphicsRectItem,
            QGraphicsTextItem,
            QBrush,
            QColor,
            QFont,
            QPen,
            QPointF,
            Qt,
            SpeakingRippleGraphicsItem,
            DOLL_ASSETS_DIR,
        )
        fit_graphics_view = _make_fit_graphics_view(QGraphicsView, Qt)
        self._agent_roles = self._agent_roles_from_spec(self._spec)
        self._agent_names = list(self._agent_roles) or ["Solo"]
        self._active_agent = self._agent_names[0]
        self._selected_agent = self._active_agent
        self._meeting_messages: list[dict[str, str]] = []
        self._agent_layout: dict[str, dict[str, float]] = {}
        self._agent_states = {
            name: {
                "role": self._agent_roles.get(name, "Agent"),
                "status": "Waiting",
                "thought": "",
                "objective": "",
                "tool": "",
                "health": {"calls": 0, "total_latency": 0.0, "invalid_json": 0, "repairs": 0, "fallbacks": 0},
                "history": [],
            }
            for name in self._agent_names
        }

        title = str(self._spec.get("title") or "Agent Team")
        self.dialog = QDialog()
        self.dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.dialog.setWindowTitle(f"{t('Agent Team')} - {title}")
        self.dialog.setMinimumSize(1100, 700)
        from ui.shared.window_utils import enable_standard_window_controls
        enable_standard_window_controls(self.dialog)

        root = QVBoxLayout(self.dialog)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title_label = QLabel(f"<b>{title}</b>")
        title_label.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(title_label)

        self.completion_banner = QFrame()
        self.completion_banner.setObjectName("agentCompletionBanner")
        banner_layout = QVBoxLayout(self.completion_banner)
        banner_layout.setContentsMargins(14, 10, 14, 10)
        banner_layout.setSpacing(2)
        self.completion_banner_title = QLabel(t("Agent Team Running"))
        self.completion_banner_title.setObjectName("agentCompletionBannerTitle")
        self.completion_banner_detail = QLabel(t("The run is still working."))
        self.completion_banner_detail.setWordWrap(True)
        banner_layout.addWidget(self.completion_banner_title)
        banner_layout.addWidget(self.completion_banner_detail)
        self.completion_banner.hide()
        root.addWidget(self.completion_banner)

        colors = theme_colors()
        self.approval_panel = QFrame()
        self.approval_panel.setStyleSheet(
            f"QFrame {{ background: {colors['accent_soft']}; border: 1px solid {colors['accent']}; border-radius: 6px; }}"
            f"QLabel {{ color: {colors['text']}; background: transparent; font-weight: 600; }}"
        )
        approval_row = QHBoxLayout(self.approval_panel)
        approval_row.setContentsMargins(10, 8, 10, 8)
        self.approval_label = QLabel()
        self.approval_label.setWordWrap(True)
        approve_btn = QPushButton(t("Approve"))
        decline_btn = QPushButton(t("Decline"))
        approve_btn.clicked.connect(lambda: self._resolve_approval(True))
        decline_btn.clicked.connect(lambda: self._resolve_approval(False))
        approval_row.addWidget(self.approval_label, 1)
        approval_row.addWidget(approve_btn)
        approval_row.addWidget(decline_btn)
        self.approval_panel.hide()
        root.addWidget(self.approval_panel)

        self.tabs = QTabWidget()
        self.meeting_scene = QGraphicsScene(self.dialog)
        self.meeting_view = fit_graphics_view(self.meeting_scene)
        self.meeting_view.setMinimumSize(280, 220)
        self.meeting_view.setStyleSheet(
            f"QGraphicsView {{ background: {colors['bg']}; border: 1px solid {colors['border']}; }}"
        )
        meeting_splitter = QSplitter(Qt.Orientation.Horizontal)
        meeting_splitter.setChildrenCollapsible(False)

        meeting_panel = QWidget()
        meeting_layout = QVBoxLayout(meeting_panel)
        meeting_layout.setContentsMargins(0, 0, 0, 0)
        meeting_layout.setSpacing(6)
        meeting_header = QHBoxLayout()
        meeting_header.addWidget(QLabel(t("Meeting")))
        meeting_header.addStretch()
        self.reset_layout_btn = QPushButton(t("Reset Layout"))
        self.reset_layout_btn.setToolTip(t("Restore every agent card to its default position and size"))
        self.reset_layout_btn.clicked.connect(self._reset_agent_layout)
        meeting_header.addWidget(self.reset_layout_btn)
        meeting_layout.addLayout(meeting_header)
        meeting_layout.addWidget(self.meeting_view, 1)

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(6)
        detail_layout.addWidget(QLabel(t("Agent Detail")))
        self.agent_summary_view = QTextEdit()
        self.agent_summary_view.setReadOnly(True)
        self.agent_summary_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.agent_summary_view.setMaximumHeight(250)
        self.agent_summary_view.setMinimumWidth(240)
        self.agent_detail_view = self.agent_summary_view
        detail_layout.addWidget(self.agent_summary_view, 1)
        detail_layout.addWidget(QLabel(t("Recent Activity")))
        self.agent_activity_view = QTextEdit()
        self.agent_activity_view.setReadOnly(True)
        self.agent_activity_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        detail_layout.addWidget(self.agent_activity_view, 1)

        board_panel = QWidget()
        board_layout = QVBoxLayout(board_panel)
        board_layout.setContentsMargins(0, 0, 0, 0)
        board_layout.setSpacing(6)
        board_layout.addWidget(QLabel(t("Shared Board")))
        self.shared_board_view = QTextEdit()
        self.shared_board_view.setReadOnly(True)
        self.shared_board_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        board_layout.addWidget(self.shared_board_view, 1)

        meeting_splitter.addWidget(meeting_panel)
        meeting_splitter.addWidget(board_panel)
        meeting_splitter.addWidget(detail_panel)
        meeting_splitter.setStretchFactor(0, 4)
        meeting_splitter.setStretchFactor(1, 2)
        meeting_splitter.setStretchFactor(2, 4)
        meeting_splitter.setSizes([460, 240, 460])

        self.log_view = QTextEdit()
        self.trace_view = QTextEdit()
        self.final_view = QTextEdit()
        for view in (
            self.agent_detail_view,
            self.agent_activity_view,
            self.shared_board_view,
            self.log_view,
            self.trace_view,
            self.final_view,
        ):
            view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.trace_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.final_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.tabs.addTab(meeting_splitter, t("Meeting Room"))
        self.tabs.addTab(self.log_view, t("Live Log"))
        self.tabs.addTab(self.trace_view, t("Model Trace"))
        self.tabs.addTab(self.final_view, t("Final Report"))
        root.addWidget(self.tabs, 1)
        self._refresh_meeting_room()

        row = QHBoxLayout()
        self.status_label = QLabel(t("Running..."))
        self.open_result_btn = QPushButton(t("Open Run Folder"))
        self.open_result_btn.setEnabled(False)
        self.open_result_btn.clicked.connect(self._open_result_folder)
        self.open_scope_btn = QPushButton(t("Open Scope Folder"))
        self.open_scope_btn.clicked.connect(self._open_scope_folder)
        self.retry_btn = QPushButton(t("Retry"))
        self.retry_btn.setEnabled(False)
        self.retry_btn.clicked.connect(self._retry_run)
        self.continue_btn = QPushButton(t("Continue"))
        self.continue_btn.setEnabled(False)
        self.continue_btn.clicked.connect(self._continue_run)
        self.nudge_btn = QPushButton(t("Nudge Agent"))
        self.nudge_btn.clicked.connect(self._send_manual_nudge)
        self.permissions_btn = QPushButton(t("Permissions"))
        self.permissions_btn.clicked.connect(self._edit_live_permissions)
        self.pause_btn = QPushButton(t("Pause After Turn"))
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.cancel_btn = QPushButton(t("Cancel"))
        self.cancel_btn.clicked.connect(self._cancel_run)
        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(self.dialog.close)
        row.addWidget(self.status_label)
        row.addStretch()
        row.addWidget(self.open_result_btn)
        row.addWidget(self.open_scope_btn)
        row.addWidget(self.retry_btn)
        row.addWidget(self.continue_btn)
        row.addWidget(self.nudge_btn)
        row.addWidget(self.permissions_btn)
        row.addWidget(self.pause_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(close_btn)
        root.addLayout(row)
        localize_widget_tree(self.dialog)

    def show(self) -> None:
        """Show, raise, and focus the agent-run dialog."""
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def close(self) -> None:
        """Close the agent-run dialog."""
        self.dialog.close()

    def append_log(self, data: dict[str, Any]) -> None:
        """Append log."""
        payload = self._payload(data)
        line = str(payload.get("line") or payload.get("text") or payload.get("data") or "")
        if line:
            self._update_live_meeting(line)
            self._append_text(self.log_view, translate_agent_log_line(line))
            if not self._paused:
                self.status_label.setText(t("Running..."))
            self._refresh_meeting_room()

    def append_trace(self, data: dict[str, Any]) -> None:
        """Append trace."""
        payload = self._payload(data)
        entry = str(payload.get("entry") or payload.get("text") or payload.get("data") or "")
        if entry:
            self._append_text(self.trace_view, entry)

    def finish(self, data: dict[str, Any]) -> None:
        """Handle finish for mac agent run dialog."""
        payload = self._payload(data)
        self._run_dir = str(payload.get("run_dir") or self._run_dir or "")
        final_text = str(payload.get("final") or "")
        error_text = str(payload.get("error") or "")
        if final_text:
            self.final_view.setPlainText(
                t(final_text) if final_text == "Agent Team was cancelled by the user." else final_text
            )
        elif error_text:
            self.final_view.setPlainText(error_text)
        elif payload.get("cancelled"):
            self.final_view.setPlainText(t("Agent Team cancelled."))
        else:
            self.final_view.setPlainText(t("(no final report)"))

        if error_text:
            self.status_label.setText(t("Failed"))
            self._show_completion_banner(t("Agent Team Failed"), error_text, "failed")
            self._set_agent_status(self._active_agent, "Failed", error_text)
        elif payload.get("cancelled"):
            self.status_label.setText(t("Cancelled"))
            self._show_completion_banner(t("Agent Team Cancelled"), t("Agent Team cancelled."), "cancelled")
            self._set_agent_status(self._active_agent, "Cancelled", "Agent Team cancelled.")
        else:
            self.status_label.setText(t("Finished"))
            self._show_completion_banner(
                t("Agent Team Finished"),
                f"{t('Final report is ready. Log:')} {self._run_dir}" if self._run_dir else t("Final report is ready."),
                "success",
            )
            for name in self._agent_names:
                status = str(self._agent_states.get(name, {}).get("status") or "")
                if status not in {"Done", "Failed", "Cancelled"}:
                    self._set_agent_status(name, "Finished", "Agent run finished.")
        self.approval_panel.hide()
        self.cancel_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.nudge_btn.setEnabled(False)
        self.permissions_btn.setEnabled(False)
        self.open_result_btn.setEnabled(bool(self._run_dir))
        self.retry_btn.setEnabled(True)
        self.continue_btn.setEnabled(bool(self._run_dir))
        self.tabs.setCurrentWidget(self.final_view)
        self._refresh_meeting_room()
        self.show()
        self._QApplication.alert(self.dialog, 0)
        notice_title = t("Agent Team Failed") if error_text else t("Agent Team Cancelled") if payload.get("cancelled") else t("Agent Team Finished")
        if hasattr(self._host, "_agent_notify_approval"):
            self._host._agent_notify_approval(notice_title, True, {"run_dir": self._run_dir})

    def _show_completion_banner(self, title: str, detail: str, kind: str) -> None:
        """Show a prominent completion banner at the top of the run window."""
        dark_styles = {
            "success": ("#173124", "#42b883", "#bce8cf"),
            "failed": ("#351d1b", "#c4553d", "#f0c0b6"),
            "cancelled": ("#22262b", "#5f574f", "#b8b4ac"),
        }
        light_styles = {
            "success": ("#e3ecdf", "#557a4c", "#31472d"),
            "failed": ("#f0ddd5", "#a3391c", "#6e2816"),
            "cancelled": ("#e7e2d5", "#d1c9b7", "#6c6659"),
        }
        styles = dark_styles if is_dark_mode() else light_styles
        bg, border, text = styles.get(kind, styles["success"])
        self.completion_banner.setStyleSheet(
            f"QFrame#agentCompletionBanner {{ background: {bg}; border: 3px solid {border}; border-radius: 8px; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            "QLabel#agentCompletionBannerTitle { font-size: 22px; font-weight: 800; }"
        )
        self.completion_banner_title.setText(title)
        self.completion_banner_detail.setText(detail)
        self.completion_banner.show()

    def request_approval(self, data: dict[str, Any]) -> None:
        """Handle request approval for mac agent run dialog."""
        payload = self._payload(data)
        self._pending_approval_id = str(payload.get("approval_id") or "")
        action = str(payload.get("action") or "approval")
        details = payload.get("details")
        detail_text = ""
        if isinstance(details, dict):
            detail_text = ", ".join(f"{key}={value}" for key, value in details.items())
        if not detail_text:
            detail_text = str(payload.get("detail") or payload.get("reason") or "")
        text = f"{t('Permission needed')}: {action}"
        if detail_text:
            text += f"\n{detail_text}"
        self.approval_label.setText(text)
        self.approval_panel.show()
        self.status_label.setText(t("Permission needed"))
        self._set_agent_status(self._active_agent, "Needs approval", text)
        self._refresh_meeting_room()
        self._host._agent_notify_approval(
            text,
            resolved=False,
            data=payload,
        )
        self.dialog.raise_()
        self._QApplication.alert(self.dialog, 0)

    @staticmethod
    def _payload(data: dict[str, Any]) -> dict[str, Any]:
        """Handle payload for mac agent run dialog."""
        if isinstance(data, dict) and isinstance(data.get("data"), dict) and len(data) == 1:
            return data["data"]
        return data if isinstance(data, dict) else {"data": data}

    def _append_text(self, view, text: str) -> None:
        """Append text."""
        if not text:
            return
        cursor = view.textCursor()
        cursor.movePosition(self._QTextCursor.MoveOperation.End)
        if view.toPlainText():
            cursor.insertText("\n")
        cursor.insertText(text.rstrip("\n"))
        bar = view.verticalScrollBar()
        bar.setValue(bar.maximum())

    @staticmethod
    def _scroll_snapshot(view) -> tuple[int, bool]:
        """Capture scroll position and whether the view was following the bottom."""
        bar = view.verticalScrollBar()
        value = bar.value()
        return value, value >= bar.maximum() - 4

    @classmethod
    def _set_plain_text_preserving_scroll(cls, view, text: str) -> None:
        """Set text without snapping a user-scrolled view back to the top."""
        old_value, was_at_bottom = cls._scroll_snapshot(view)
        view.setPlainText(text)
        bar = view.verticalScrollBar()
        if was_at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(old_value, bar.maximum()))

    @staticmethod
    def _agent_roles_from_spec(spec: dict[str, Any]) -> dict[str, str]:
        """Handle agent roles from spec for mac agent run dialog."""
        roles: dict[str, str] = {}
        agents = spec.get("agents") if isinstance(spec, dict) else None
        if isinstance(agents, list):
            for idx, agent in enumerate(agents):
                if isinstance(agent, dict):
                    name = str(agent.get("name") or f"Agent {idx + 1}").strip()
                    role = str(agent.get("role") or "Agent").strip()
                else:
                    name = str(getattr(agent, "name", "") or f"Agent {idx + 1}").strip()
                    role = str(getattr(agent, "role", "") or "Agent").strip()
                if name:
                    roles[name] = role or "Agent"
        return roles

    def _update_live_meeting(self, line: str) -> None:
        """Update live meeting."""
        from ui.agent.log_parser import parse_live_log_event

        event = parse_live_log_event(line)
        body = event.body
        if event.kind in {"agent_turn", "agent_read_only_turn"}:
            name = event.agent
            self._ensure_agent_state(name)
            self._active_agent = name
            if self._selected_agent not in self._agent_states:
                self._selected_agent = name
            status = "Read-only briefing" if event.kind == "agent_read_only_turn" else "Thinking"
            self._set_agent_status(name, status, body)
            return
        if body.startswith("parallel read-only briefing started"):
            for name in self._agent_names:
                self._set_agent_status(name, "Joining briefing", body)
            return
        if body.startswith("parallel read-only briefing finished"):
            for name in self._agent_names:
                current = str(self._agent_states[name].get("status") or "")
                if current in {"Joining briefing", "Read-only briefing", "Calling model"}:
                    self._set_agent_status(name, "Briefed", body)
            return
        if body.startswith("requesting LLM tool response"):
            self._set_agent_status(self._active_agent, "Calling model", body)
            return
        if body.startswith("model call still waiting after "):
            elapsed = body.split(" after ", 1)[1].split(" via ", 1)[0]
            self._set_agent_status(self._active_agent, f"Waiting {elapsed}", body)
            return
        if body.startswith("model first token after "):
            elapsed = body.split(" after ", 1)[1].split(" via ", 1)[0]
            self._set_agent_status(self._active_agent, f"Receiving response ({elapsed})", body)
            return
        if body.startswith("model streaming response: "):
            self._set_agent_status(self._active_agent, "Receiving response", body)
            return
        if body.startswith("model response still streaming after "):
            self._set_agent_status(self._active_agent, "Still receiving response", body)
            return
        if body.startswith("LLM call failed: "):
            self._agent_health(self._active_agent)["fallbacks"] += 1
            self._set_agent_status(self._active_agent, "Model error; retrying", body)
            return
        if body.startswith("routing by latest directed message: "):
            route = body[len("routing by latest directed message: "):].strip()
            target = route.split(" -> ", 1)[1] if " -> " in route else route
            self._set_agent_status(self._active_agent, f"Handing off to {target}", body)
            return
        if body.startswith("routing by explicit next_agent: "):
            route = body[len("routing by explicit next_agent: "):].split(" (", 1)[0].strip()
            target = route.split(" -> ", 1)[1] if " -> " in route else route
            self._set_agent_status(self._active_agent, f"Explicit handoff to {target}", body)
            return
        if body.startswith("prompt prepared for "):
            summary = body.split(": ", 1)[1] if ": " in body else body
            self._set_agent_status(self._active_agent, f"Prompt {summary}", body)
            return
        if body.startswith("model response received") or body.startswith("model callback response received"):
            self._record_model_latency(self._active_agent, body)
            self._set_agent_status(self._active_agent, "Parsing response", body)
            return
        if body.startswith("file payload in JSON response"):
            self._append_agent_history(self._active_agent, body)
            return
        if body.startswith("agent response parse failed"):
            self._agent_health(self._active_agent)["invalid_json"] += 1
            self._set_agent_status(self._active_agent, "Repairing response", body)
            return
        if body.startswith("requesting JSON repair"):
            self._set_agent_status(self._active_agent, "Repairing JSON", body)
            return
        if body.startswith("repaired invalid JSON locally") or body.startswith("JSON repair response received"):
            self._agent_health(self._active_agent)["repairs"] += 1
            self._set_agent_status(self._active_agent, "Parsing repaired JSON", body)
            return
        if body.startswith("using local fallback"):
            self._agent_health(self._active_agent)["fallbacks"] += 1
            self._set_agent_status(self._active_agent, "Retrying", body)
            return
        if body.startswith("agent run paused"):
            self._paused = True
            self.pause_btn.setText(t("Resume"))
            self.status_label.setText(t("Paused after current turn"))
            return
        if body.startswith("agent run resumed"):
            self._paused = False
            self.pause_btn.setText(t("Pause After Turn"))
            self.status_label.setText(t("Running..."))
            return
        if body.startswith("agent reached turn limit"):
            self._set_agent_status(self._active_agent, "Turn limit reached", body)
            return
        if body.startswith("message: ") and " -> " in body and ": " in body[9:]:
            self._record_meeting_message(body)
            return
        for name in list(self._agent_states):
            thought_prefix = f"{name} thought: "
            tool_prefix = f"{name} tool call: "
            final_prefix = f"{name} returned final response"
            if body.startswith(thought_prefix):
                thought = body[len(thought_prefix):].strip()
                self._agent_states[name]["thought"] = thought
                self._agent_states[name]["objective"] = self._objective_from_thought(thought)
                self._set_agent_status(name, "Thinking", "Thought: " + thought)
                return
            if body.startswith(tool_prefix):
                tool = body[len(tool_prefix):].strip()
                self._agent_states[name]["tool"] = tool
                self._set_agent_status(name, f"Using {tool}", body)
                return
            if body.startswith(final_prefix):
                self._set_agent_status(name, "Done", body)
                return
        if body.startswith("tool "):
            self._append_agent_history(self._active_agent, body)

    def _record_meeting_message(self, body: str) -> None:
        """Record meeting message."""
        try:
            payload = body[len("message: "):]
            route, message = payload.split(": ", 1)
            source, target = route.split(" -> ", 1)
        except ValueError:
            return
        item = {
            "from": source.strip(),
            "to": target.strip(),
            "message": message.strip(),
        }
        if any(
            existing.get("from") == item["from"]
            and existing.get("to") == item["to"]
            and existing.get("message") == item["message"]
            for existing in self._meeting_messages[-6:]
        ):
            return
        self._meeting_messages.append(item)
        del self._meeting_messages[:-12]
        if item["from"] in self._agent_states:
            self._append_agent_history(item["from"], f"Told {item['to']}: {item['message']}")
        if item["to"] in self._agent_states:
            self._append_agent_history(item["to"], f"Heard from {item['from']}: {item['message']}")

    def _ensure_agent_state(self, name: str) -> None:
        """Ensure agent state."""
        name = (name or "Agent").strip() or "Agent"
        if name in self._agent_states:
            return
        self._agent_names.append(name)
        self._agent_roles.setdefault(name, "Agent")
        self._agent_states[name] = {
            "role": self._agent_roles.get(name, "Agent"),
            "status": "Waiting",
            "thought": "",
            "objective": "",
            "tool": "",
            "health": {"calls": 0, "total_latency": 0.0, "invalid_json": 0, "repairs": 0, "fallbacks": 0},
            "history": [],
        }

    def _set_agent_status(self, name: str, status: str, event: str) -> None:
        """Set agent status."""
        self._ensure_agent_state(name)
        self._agent_states[name]["status"] = status
        self._append_agent_history(name, event)

    def _agent_health(self, name: str) -> dict:
        """Handle agent health for mac agent run dialog."""
        self._ensure_agent_state(name)
        return self._agent_states[name].setdefault(
            "health",
            {"calls": 0, "total_latency": 0.0, "invalid_json": 0, "repairs": 0, "fallbacks": 0},
        )

    def _record_model_latency(self, name: str, body: str) -> None:
        """Record model latency."""
        marker = " received in "
        if marker not in body:
            return
        try:
            seconds = float(body.split(marker, 1)[1].split("s", 1)[0])
        except ValueError:
            return
        health = self._agent_health(name)
        health["calls"] = int(health.get("calls", 0)) + 1
        health["total_latency"] = float(health.get("total_latency", 0.0)) + seconds

    @staticmethod
    def _objective_from_thought(thought: str) -> str:
        """Handle objective from thought for mac agent run dialog."""
        clean = " ".join(thought.split())
        return MacAgentRunDialog._shorten(clean, 120)

    def _append_agent_history(self, name: str, event: str) -> None:
        """Append agent history."""
        self._ensure_agent_state(name)
        history = self._agent_states[name]["history"]
        history.append(event)
        del history[:-40]

    def _refresh_meeting_room(self) -> None:
        """Refresh meeting room."""
        self._refresh_shared_board()
        self._draw_live_meeting()

    def _draw_live_meeting(self) -> None:
        """Handle draw live meeting for mac agent run dialog."""
        colors = theme_colors()
        self.meeting_scene.clear()
        self.meeting_scene.setSceneRect(0, 0, 1080, 560)

        bg = self._QPainterPath()
        bg.addRoundedRect(10, 10, 1060, 540, 16, 16)
        self.meeting_scene.addPath(
            bg,
            self._QPen(self._QColor(colors["border"]), 1),
            self._QBrush(self._QColor(colors["bg"])),
        )

        table = self._QPainterPath()
        table.addRoundedRect(445, 230, 190, 100, 24, 24)
        self.meeting_scene.addPath(
            table,
            self._QPen(self._QColor(colors["accent"]), 1.5),
            self._QBrush(self._QColor(colors["raised"])),
        )

        title = self.meeting_scene.addText(t("Agent Meeting"), self._QFont("Segoe UI", 11, self._QFont.Weight.DemiBold))
        title.setDefaultTextColor(self._QColor(colors["accent"]))
        title.setTextWidth(150)
        title.setPos(465, 267)
        title.setAcceptedMouseButtons(self._Qt.MouseButton.NoButton)

        positions = self._live_agent_positions(len(self._agent_names))
        centers: dict[str, tuple[float, float]] = {}
        live_item = self._LiveAgentItem
        for idx, name in enumerate(self._agent_names):
            state = self._agent_states.get(name, {})
            x, y = positions[idx]
            override = self._agent_layout.get(name) or {}
            x = float(override.get("x", x))
            y = float(override.get("y", y))
            scale = float(override.get("scale", 1.0))
            centers[name] = (
                x + live_item.WIDTH * scale / 2,
                y + live_item.HEIGHT * scale / 2,
            )
            item = live_item(
                idx,
                self._select_live_agent,
                x,
                y,
                translate_agent_name(name),
                translate_agent_role(str(state.get("role") or "Agent")),
                translate_agent_status(str(state.get("status") or "Waiting")),
                self._shorten(str(state.get("objective") or ""), 96),
                self._health_badge(name),
                name == self._active_agent,
                name == self._selected_agent,
                scale=scale,
                on_geometry_change=self._on_agent_geometry_change,
            )
            self.meeting_scene.addItem(item)

        self._draw_last_message_arrow(centers)
        self.meeting_view.fit_scene()
        self._refresh_agent_detail()

    def _draw_last_message_arrow(self, centers: dict[str, tuple[float, float]]) -> None:
        """Handle draw last message arrow for mac agent run dialog."""
        if not self._meeting_messages:
            return
        item = self._meeting_messages[-1]
        source = item.get("from", "")
        target = item.get("to", "")
        if source not in centers:
            return
        if target.upper() == "ALL":
            for name in self._agent_names:
                if name != source and name in centers:
                    self._draw_live_arrow(centers[source], centers[name])
            return
        if target not in centers:
            return
        self._draw_live_arrow(centers[source], centers[target])

    def _draw_live_arrow(self, source: tuple[float, float], target: tuple[float, float]) -> None:
        """Handle draw live arrow for mac agent run dialog."""
        sx, sy = source
        tx, ty = target
        sx_edge, sy_edge, tx_edge, ty_edge = self._live_edge_points(sx, sy, tx, ty)
        accent = self._QColor(theme_colors()["accent_fill"])
        pen = self._QPen(accent, 2.3)
        self.meeting_scene.addLine(sx_edge, sy_edge, tx_edge, ty_edge, pen)
        dx, dy = tx_edge - sx_edge, ty_edge - sy_edge
        length = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        size = 11
        bx = tx_edge - ux * size
        by = ty_edge - uy * size
        path = self._QPainterPath()
        path.moveTo(tx_edge, ty_edge)
        path.lineTo(bx + px * size * 0.55, by + py * size * 0.55)
        path.lineTo(bx - px * size * 0.55, by - py * size * 0.55)
        path.closeSubpath()
        self.meeting_scene.addPath(path, self._QPen(accent), self._QBrush(accent))

    def _live_edge_points(self, sx: float, sy: float, tx: float, ty: float) -> tuple[float, float, float, float]:
        """Handle live edge points for mac agent run dialog."""
        dx, dy = tx - sx, ty - sy
        length = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        live_item = self._LiveAgentItem
        half_w, half_h = live_item.WIDTH / 2, live_item.HEIGHT / 2
        border_offset = min(
            half_w / max(abs(ux), 0.001),
            half_h / max(abs(uy), 0.001),
        )
        gap = 10.0
        return (
            sx + ux * (border_offset + gap),
            sy + uy * (border_offset + gap),
            tx - ux * (border_offset + gap),
            ty - uy * (border_offset + gap),
        )

    def _live_agent_positions(self, count: int) -> list[tuple[float, float]]:
        """Handle live agent positions for mac agent run dialog."""
        if count <= 0:
            return []
        live_item = self._LiveAgentItem
        cx, cy = 540, 280
        rx, ry = 390, 200
        return [
            (
                cx + math.cos(-math.pi / 2 + 2 * math.pi * idx / count) * rx - live_item.WIDTH / 2,
                cy + math.sin(-math.pi / 2 + 2 * math.pi * idx / count) * ry - live_item.HEIGHT / 2,
            )
            for idx in range(count)
        ]

    def _select_live_agent(self, index: int) -> None:
        """Handle select live agent for mac agent run dialog."""
        if 0 <= index < len(self._agent_names):
            self._selected_agent = self._agent_names[index]
            self._draw_live_meeting()

    def _on_agent_geometry_change(self, index: int, x: float, y: float, scale: float) -> None:
        """Handle agent geometry change events."""
        if not (0 <= index < len(self._agent_names)):
            return
        rect = self.meeting_scene.sceneRect()
        live_item = self._LiveAgentItem
        width = live_item.WIDTH * scale
        height = live_item.HEIGHT * scale
        x = max(rect.left(), min(x, rect.right() - width))
        y = max(rect.top(), min(y, rect.bottom() - height))
        self._agent_layout[self._agent_names[index]] = {"x": x, "y": y, "scale": scale}
        self._QTimer.singleShot(0, self._draw_live_meeting)

    def _reset_agent_layout(self) -> None:
        """Reset agent layout."""
        if not self._agent_layout:
            return
        self._agent_layout.clear()
        self._draw_live_meeting()

    def _refresh_agent_detail(self) -> None:
        """Refresh agent detail."""
        state = self._agent_states.get(self._selected_agent)
        if not state:
            self.agent_detail_view.clear()
            self.agent_activity_view.clear()
            return
        health = self._health_detail(self._selected_agent)
        detail = (
            f"<h3>{html.escape(translate_agent_name(self._selected_agent))}</h3>"
            f"<p><b>{html.escape(t('Role:'))}</b> {html.escape(translate_agent_role(str(state.get('role') or 'Agent')))}<br>"
            f"<b>{html.escape(t('Status:'))}</b> {html.escape(_mac_status_text(str(state.get('status') or 'Waiting')))}<br>"
            f"<b>{html.escape(t('Last tool:'))}</b> {html.escape(str(state.get('tool') or t('None')))}</p>"
            f"<p><b>{html.escape(t('Current objective'))}</b><br>"
            f"{html.escape(str(state.get('objective') or t('No current objective.')))}</p>"
            f"<p><b>{html.escape(t('Model health'))}</b><br>{html.escape(health)}</p>"
            f"<p><b>{html.escape(t('Latest thought'))}</b><br>"
            f"{html.escape(str(state.get('thought') or t('No thought yet.')))}</p>"
        )
        self.agent_detail_view.setHtml(detail)
        history = state.get("history") or []
        self._set_plain_text_preserving_scroll(
            self.agent_activity_view,
            "\n".join(f"- {translate_agent_activity_text(item)}" for item in history[-18:])
            or f"- {t('No activity yet.')}",
        )

    def _refresh_shared_board(self) -> None:
        """Refresh shared board."""
        if not self._meeting_messages:
            self._set_plain_text_preserving_scroll(self.shared_board_view, t("No messages yet."))
            return
        lines: list[str] = []
        for item in self._meeting_messages:
            lines.append(f"{translate_agent_name(item['from'])} -> {translate_agent_name(item['to'])}")
            lines.append(translate_agent_activity_text(item["message"]))
            lines.append("")
        self._set_plain_text_preserving_scroll(self.shared_board_view, "\n".join(lines).strip())

    def _health_badge(self, name: str) -> str:
        """Handle health badge for mac agent run dialog."""
        return translate_agent_health_badge(self._agent_health(name))

    def _health_detail(self, name: str) -> str:
        """Handle health detail for mac agent run dialog."""
        return translate_agent_health_detail(self._agent_health(name))

    @staticmethod
    def _shorten(text: str, max_chars: int) -> str:
        """Handle shorten for mac agent run dialog."""
        clean = " ".join(text.split())
        if len(clean) <= max_chars:
            return clean
        return clean[: max(0, max_chars - 1)].rstrip() + "..."

    def _resolve_approval(self, approved: bool) -> None:
        """Handle resolve approval for mac agent run dialog."""
        if not self._pending_approval_id:
            return
        approval_id = self._pending_approval_id
        self._pending_approval_id = ""
        self.approval_panel.hide()
        self._host.emit(
            "ui.agent.approval.respond",
            {"approval_id": approval_id, "approved": bool(approved)},
        )

    def _toggle_pause(self) -> None:
        """Pause after the current turn, or resume a paused run."""
        if self._paused:
            self._host.emit("ui.agent.resume_requested", {})
            self._paused = False
            self.pause_btn.setText(t("Pause After Turn"))
            self.status_label.setText(t("Running..."))
        else:
            self._host.emit("ui.agent.pause_requested", {})
            self._paused = True
            self.pause_btn.setText(t("Resume"))
            self.status_label.setText(t("Will pause after current turn"))

    def _send_manual_nudge(self) -> None:
        """Queue a user message for the running agent task."""
        from ui.agent.task_window import AgentNudgeDialog

        dialog = AgentNudgeDialog(self._agent_names + ["ALL"], parent=self.dialog)
        if dialog.exec() != self._QDialog.DialogCode.Accepted or not dialog.nudge:
            return
        target = str(dialog.nudge.get("to") or "ALL")
        message = str(dialog.nudge.get("message") or "").strip()
        if not message:
            return
        self._host.emit("ui.agent.nudge", {"target_agent": target, "message": message})
        self.status_label.setText(f"{t('Nudge queued for')} {target}")
        self._record_meeting_message(f"message: User -> {target}: {message.replace(chr(10), ' ')}")
        self._refresh_meeting_room()

    def _edit_live_permissions(self) -> None:
        """Edit permission modes for the active run."""
        dialog = self._QDialog(self.dialog)
        dialog.setWindowTitle(t("Permission Modes"))
        layout = self._QVBoxLayout(dialog)
        form = self._QFormLayout()
        mode_options = ("auto", "ask permission", "never permit")
        fields = {
            "shell": ("shell_permission_mode", t("Shell")),
            "network": ("network_permission_mode", t("Network")),
            "git": ("git_permission_mode", t("Git")),
            "file_create": ("file_create_permission_mode", t("Create files")),
            "file_edit": ("file_edit_permission_mode", t("Edit files")),
            "file_delete": ("file_delete_permission_mode", t("Delete files")),
        }
        combos: dict[str, Any] = {}
        for category, (spec_key, label) in fields.items():
            combo = self._QComboBox()
            for option in mode_options:
                combo.addItem(t(option), option)
            current = str(self._spec.get(spec_key) or "never permit").strip().lower()
            idx = combo.findData(current)
            combo.setCurrentIndex(max(0, idx))
            combos[category] = combo
            form.addRow(label, combo)
        layout.addLayout(form)
        buttons = self._QDialogButtonBox(
            self._QDialogButtonBox.StandardButton.Cancel | self._QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        localize_widget_tree(dialog)
        if dialog.exec() != self._QDialog.DialogCode.Accepted:
            return
        modes = {category: str(combo.currentData() or "") for category, combo in combos.items()}
        for category, mode in modes.items():
            spec_key = fields[category][0]
            self._spec[spec_key] = mode
            allow_key = {
                "shell": "allow_shell",
                "network": "allow_network",
                "git": "allow_git",
                "file_create": "allow_file_create",
                "file_edit": "allow_file_edit",
                "file_delete": "allow_file_delete",
            }[category]
            self._spec[allow_key] = mode not in {"never", "never permit", "deny"}
        self._host.emit("ui.agent.permissions", {"permission_modes": modes})
        self.status_label.setText(t("Permission changes queued"))

    def _cancel_run(self) -> None:
        """Cancel run."""
        self.status_label.setText(t("Cancelling..."))
        self.cancel_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.nudge_btn.setEnabled(False)
        self.permissions_btn.setEnabled(False)
        self._host.emit("ui.agent.cancel_requested", {})

    def _retry_run(self) -> None:
        """Handle retry run for mac agent run dialog."""
        self._host._start_agent_run(self._spec)

    def _continue_run(self) -> None:
        """Handle continue run for mac agent run dialog."""
        if self._run_dir:
            self._host.emit("ui.agent.history.continue", {"run_dir": self._run_dir})

    def _open_result_folder(self) -> None:
        """Open result folder."""
        if self._run_dir:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(self._run_dir))

    def _open_scope_folder(self) -> None:
        """Open scope folder."""
        scope = str(self._spec.get("scope_folder") or "")
        if scope:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(scope))


class MacAgentHistoryDialog:
    """Protocol-backed agent history browser for the pure-Python target."""

    def __init__(self, host: QtProtocolHost) -> None:
        """Initialize the mac agent history dialog instance."""
        from PySide6.QtCore import Qt, QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import (
            QDialog,
            QHBoxLayout,
            QListWidget,
            QListWidgetItem,
            QPushButton,
            QSplitter,
            QTabWidget,
            QTextEdit,
            QVBoxLayout,
        )

        self._host = host
        self._QDesktopServices = QDesktopServices
        self._QListWidgetItem = QListWidgetItem
        self._Qt = Qt
        self._QUrl = QUrl
        self._loading = False
        self._current_run_dir = ""
        self._runs_root = ""

        self.dialog = QDialog()
        self.dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.dialog.setWindowTitle(t("Agent Team History"))
        self.dialog.setMinimumSize(960, 620)
        from ui.shared.window_utils import enable_standard_window_controls
        enable_standard_window_controls(self.dialog)

        root = QVBoxLayout(self.dialog)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.run_list = QListWidget()
        self.run_list.currentItemChanged.connect(self._selected_run_changed)
        splitter.addWidget(self.run_list)

        self.tabs = QTabWidget()
        self.summary_view = QTextEdit()
        self.log_view = QTextEdit()
        self.trace_view = QTextEdit()
        self.diff_view = QTextEdit()
        for view in (self.summary_view, self.log_view, self.trace_view, self.diff_view):
            view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.trace_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.diff_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.tabs.addTab(self.summary_view, t("Summary"))
        self.tabs.addTab(self.log_view, t("Run Log"))
        self.tabs.addTab(self.trace_view, t("Model Trace"))
        self.tabs.addTab(self.diff_view, t("Diff"))
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

        row = QHBoxLayout()
        refresh_btn = QPushButton(t("Refresh"))
        refresh_btn.clicked.connect(lambda: self._host.emit("ui.agent.history.refresh", {}))
        open_btn = QPushButton(t("Open Run Folder"))
        open_btn.clicked.connect(self._open_current_run)
        retry_btn = QPushButton(t("Retry"))
        retry_btn.clicked.connect(lambda: self._emit_current("ui.agent.history.retry"))
        continue_btn = QPushButton(t("Continue"))
        continue_btn.clicked.connect(lambda: self._emit_current("ui.agent.history.continue"))
        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(self.dialog.close)
        row.addStretch()
        row.addWidget(refresh_btn)
        row.addWidget(open_btn)
        row.addWidget(retry_btn)
        row.addWidget(continue_btn)
        row.addWidget(close_btn)
        root.addLayout(row)
        localize_widget_tree(self.dialog)

    def show(self) -> None:
        """Show, raise, and focus the agent-history dialog."""
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def replace_runs(self, runs: list[dict[str, Any]] | None, runs_root: str = "") -> None:
        """Handle replace runs for mac agent history dialog."""
        self._runs_root = str(runs_root or "")
        self._loading = True
        self.run_list.clear()
        for run in runs or []:
            if not isinstance(run, dict):
                continue
            run_dir = str(run.get("run_dir") or "")
            if not run_dir:
                continue
            item = self._QListWidgetItem(self._display_name(run))
            item.setData(self._Qt.ItemDataRole.UserRole, run_dir)
            self.run_list.addItem(item)
        self._loading = False
        if self.run_list.count():
            self.run_list.setCurrentRow(0)
        else:
            self._current_run_dir = ""
            self._clear_views(t("No Agent Team runs yet."))

    def update_detail(self, data: dict[str, Any]) -> None:
        """Update detail."""
        payload = data if isinstance(data, dict) else {"error": str(data)}
        if isinstance(payload.get("data"), dict) and len(payload) == 1:
            payload = payload["data"]
        run_dir = str(payload.get("run_dir") or self._current_run_dir or "")
        if run_dir:
            self._current_run_dir = run_dir
        error = str(payload.get("error") or "")
        final = str(payload.get("final") or "")
        task_json = str(payload.get("task_json") or "")
        if error and not final:
            summary = f"{t('Run folder:')}\n{run_dir}\n\n{t('Error:')}\n{error}"
        else:
            summary = (
                f"{t('Run folder:')}\n{run_dir}\n\n"
                f"{t('Final report:')}\n{final or t('(no final report)')}\n\n"
                f"{t('Task spec:')}\n{task_json or t('(missing task.json)')}"
            )
            if error:
                summary += f"\n\n{t('Error:')}\n{error}"
        self.summary_view.setPlainText(summary)
        self.log_view.setPlainText(str(payload.get("run_log") or ""))
        self.trace_view.setPlainText(str(payload.get("verbose_log") or ""))
        self.diff_view.setPlainText(str(payload.get("diff_patch") or t("(no diff artifact)")))

    def _selected_run_changed(self, current, _previous) -> None:
        """Handle selected run changed for mac agent history dialog."""
        if self._loading or current is None:
            return
        run_dir = str(current.data(self._Qt.ItemDataRole.UserRole) or "")
        self._current_run_dir = run_dir
        if run_dir:
            self._host.emit("ui.agent.history.read", {"run_dir": run_dir})

    def _emit_current(self, event: str) -> None:
        """Emit current."""
        if self._current_run_dir:
            self._host.emit(event, {"run_dir": self._current_run_dir})

    def _open_current_run(self) -> None:
        """Open current run."""
        if self._current_run_dir:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(self._current_run_dir))
        elif self._runs_root:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(self._runs_root))

    def _clear_views(self, text: str) -> None:
        """Clear views."""
        for view in (self.summary_view, self.log_view, self.trace_view, self.diff_view):
            view.setPlainText(text)

    @staticmethod
    def _display_name(run: dict[str, Any]) -> str:
        """Handle display name for mac agent history dialog."""
        status = str(run.get("status") or "unknown")
        modified = str(run.get("modified_display") or "")
        title = str(run.get("title") or run.get("id") or "Agent run")
        suffix = f" - {modified}" if modified else ""
        return f"{title} [{status}]{suffix}"


def _protect_stdout():
    """Handle protect stdout for runtime workers UI host."""
    real_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real_out


class QtProtocolHost:
    """Model qt protocol host."""
    def __init__(self, app, out) -> None:
        """Initialize the qt protocol host instance."""
        from PySide6.QtCore import QTimer

        self._app = app
        self._out = out
        self._write_lock = threading.Lock()
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._closing = False
        self._stdin_fd = sys.stdin.fileno()

        self._overlay_signals = None
        self._overlay = None
        self._onboarding = None
        self._intent = None
        self._snip = None
        self._bubble = None
        self._rewrite_annotations: dict[str, Any] = {}
        self._rewrite_anchor_stats: dict[str, dict[str, Any]] = {}
        self._chat = None
        self._chat_message_actions_cache: list[dict[str, Any]] = []
        self._chat_message_actions_ready = False
        self._pending_chat_show_new: bool | None = None
        self._memory = None
        self._memory_viewer = None
        self._addons_dialog = None
        self._virtual_workspace_window = None
        self._addon_settings_dialogs: dict[str, Any] = {}
        self._addon_log_dialogs: dict[str, Any] = {}
        self._status_dialogs: list[Any] = []
        self._runtime_status_dialog = None
        self._runtime_status_snapshot: dict[str, Any] | None = None
        self._agent_run_dialog: MacAgentRunDialog | None = None
        self._agent_history_dialog: MacAgentHistoryDialog | None = None
        from core.conversation_store import store as conversation_store
        self._active_project_id = conversation_store.GENERAL_PROJECT_ID
        self._conversation_scope_key = self._configured_conversation_scope()
        # Conversation hotkey/voice prompts continue when the intent overlay or
        # chat window selects a target. None means the next prompt starts fresh.
        self._active_conversation_idx: int | None = None
        try:
            self._all_conversations: list[dict] = conversation_store.load_conversations()
        except Exception:
            self._all_conversations = []
        self._apply_memory_project()
        self._chat_request_ids = itertools.count(1)
        self._chat_streams: dict[str, queue.Queue[tuple[str, Any]]] = {}
        self._chat_streams_lock = threading.Lock()
        # Overlay/hotkey replies start even when the full Chat window has never
        # been constructed.  Keep their visible stream until Chat can attach to
        # it instead of dropping every chunk while that window is closed.
        self._external_reply_stream: dict[str, Any] | None = None
        self._active_dispatch_method = ""
        self._active_dispatch_started = 0.0
        self._drain_ticks = 0  # bumped each pump tick so the watchdog can tell the IPC drain apart from an event-loop freeze
        self._watchdog = QtFreezeWatchdog(app, self._watchdog_status)
        app.aboutToQuit.connect(self._on_about_to_quit)

        self._pump = QTimer()
        self._pump.setInterval(20)
        self._pump.timeout.connect(self._drain)
        self._pump.start()

        self._reader = threading.Thread(target=self._read_loop, name="openwand-ui-stdin", daemon=True)
        self._reader.start()

    def _send(self, obj: dict[str, Any]) -> None:
        """Write a message to the supervisor over the protocol pipe (thread-safe)."""
        with self._write_lock:
            protocol.write_message(self._out, obj)

    def emit(self, event: str, data: Any = None, req_id: Any = None) -> None:
        """Send an event message to the supervisor."""
        self._send(protocol.make_event(event, data=data, req_id=req_id))

    def _respond(self, req_id: Any, ok: bool, *, result: Any = None, error: str | None = None) -> None:
        """Handle respond for qt protocol host."""
        self._send(protocol.make_response(req_id, ok, result=result, error=error))

    def _read_loop(self) -> None:
        """Read stdin as raw fd chunks and queue complete protocol lines.

        os.read is used instead of BufferedReader.readline because this daemon
        thread would otherwise hold the buffer's internal lock while blocked;
        interpreter shutdown then aborts with a fatal _enter_buffered_busy
        error when finalization cannot take that lock to close stdin.
        """
        fd = self._stdin_fd
        pending = b""
        while True:
            try:
                chunk = os.read(fd, 65536)
            except (OSError, ValueError):
                chunk = b""
            if not chunk:
                if pending.strip():
                    self._lines.put(pending)
                self._lines.put(None)
                return
            pending += chunk
            while True:
                newline = pending.find(b"\n")
                if newline < 0:
                    break
                self._lines.put(pending[: newline + 1])
                pending = pending[newline + 1 :]

    def _begin_shutdown(self, reason: str, *, notify_supervisor: bool) -> None:
        """Run every shutdown path through one idempotent teardown sequence.

        The stdin reader thread is deliberately left blocked in readline():
        closing the buffered stream from this thread would block on the
        buffer's internal lock until the supervisor writes or closes the pipe,
        and closing the raw fd does not interrupt a blocked read on Linux.
        The reader is a daemon thread that only touches a queue.Queue, so it
        is safe to leave running until the process exits.
        """
        if self._closing:
            return
        self._closing = True
        if notify_supervisor:
            try:
                self.emit("ui.quit_requested", {"reason": reason})
            except Exception:
                pass
        try:
            self._pump.stop()
        except Exception:
            pass
        try:
            self._watchdog.stop()
        except Exception:
            pass
        self._teardown_windows()

    def _teardown_windows(self) -> None:
        """Close and delete all windows while Qt can still process the deletes."""
        from PySide6.QtWidgets import QApplication

        for widget in QApplication.topLevelWidgets():
            try:
                widget.close()
            except Exception:
                pass
            try:
                widget.deleteLater()
            except Exception:
                pass

    def _on_about_to_quit(self) -> None:
        """Tell the supervisor that Qt is quitting by user request."""
        self._begin_shutdown("qt_about_to_quit", notify_supervisor=True)

    def _quit_after_shutdown(self, reason: str) -> None:
        """Stop UI IPC, tear down windows, and quit the Qt event loop."""
        from PySide6.QtCore import QTimer

        self._begin_shutdown(reason, notify_supervisor=False)
        # Quit on the next loop pass so the current IPC dispatch unwinds and
        # the DeferredDelete events posted by _teardown_windows run while the
        # event loop is still alive.
        QTimer.singleShot(0, self._app.quit)

    def finalize_after_exec(self) -> None:
        """Destroy pending deleteLater objects while QApplication still exists.

        Runs after app.exec() returns; without it, widgets deleted during the
        aboutToQuit path would be destroyed by interpreter teardown in
        arbitrary order against QApplication.
        """
        from PySide6.QtCore import QCoreApplication, QEvent

        try:
            QCoreApplication.sendPostedEvents(None, int(QEvent.Type.DeferredDelete))
        except Exception:
            pass

    def _drain(self) -> None:
        """Handle drain for qt protocol host."""
        self._drain_ticks += 1  # proof the IPC pump fired this tick
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                return
            if line is None:
                self._quit_after_shutdown("stdin_eof")
                return
            if line.strip():
                self._handle_line(line)

    def _handle_line(self, raw: bytes) -> None:
        """Handle line."""
        req_id = None
        try:
            msg = json.loads(raw.decode("utf-8", errors="replace"))
            req_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "__shutdown__":
                self._respond(req_id, True, result=None)
                self._quit_after_shutdown("supervisor_shutdown")
                return
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            method_name = str(method)
            self._active_dispatch_method = method_name
            self._active_dispatch_started = time.monotonic()
            try:
                result = self._dispatch(method_name, params)
            finally:
                elapsed = time.monotonic() - self._active_dispatch_started
                slow_threshold = float(os.environ.get("OPENWAND_UI_SLOW_DISPATCH_SECONDS", "1.0"))
                if elapsed >= slow_threshold:
                    self._write_slow_dispatch_log(method_name, elapsed)
                self._active_dispatch_method = ""
                self._active_dispatch_started = 0.0
            self._respond(req_id, True, result=result)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._respond(req_id, False, error=f"{type(exc).__name__}: {exc}")

    def _watchdog_status(self) -> dict[str, Any]:
        """Handle watchdog status for qt protocol host."""
        started = self._active_dispatch_started
        return {
            "method": self._active_dispatch_method,
            "active_for_seconds": max(0.0, time.monotonic() - started) if started else 0.0,
            "drain_ticks": self._drain_ticks,
            "queue_depth": self._lines.qsize(),
        }

    def _write_slow_dispatch_log(self, method: str, elapsed: float) -> None:
        """Write slow dispatch log."""
        try:
            log_dir = _ui_log_dir()
            path = log_dir / f"ui_slow_dispatch_{time.strftime('%Y%m%d-%H%M%S')}.log"
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(
                "".join(
                    [
                        f"time={time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                        f"pid={os.getpid()}\n",
                        f"method={method}\n",
                        f"elapsed_seconds={elapsed:.3f}\n",
                        "\nThread stacks:\n",
                        _format_thread_stacks(),
                    ]
                ),
                encoding="utf-8",
            )
            tmp_path.replace(path)
            print(f"[openwand-ui] slow dispatch wrote {path}", file=sys.stderr, flush=True)
        except Exception:
            log.exception("failed writing UI slow-dispatch log")

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Route an incoming UI request method to its handler and return the result."""
        if method in {"ping", "ui.ping"}:
            return {
                "pong": True,
                "pid": os.getpid(),
                "role": "ui",
                "version": VERSION,
                "repo_root": str(configure_paths()),
                "boundary": boundary_status("ui"),
            }
        if method == "boundary.status":
            return boundary_status("ui")
        if method == "ui.debug.block_event_loop" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            seconds = max(0.0, min(10.0, float(params.get("seconds") or 0.0)))
            time.sleep(seconds)
            return {"blocked_seconds": seconds}
        if method == "ui.debug.memory.add" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            self._memory_manager().add_fact_manual(
                str(params.get("text") or ""),
                str(params.get("category") or "general"),
                str(params.get("project") or ""),
            )
            return {"emitted": True}
        if method == "ui.debug.memory.update" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            self._memory_manager().update_fact(
                str(params.get("id") or params.get("fact_id") or ""),
                str(params.get("text") or ""),
                params.get("category"),
                params.get("project"),
            )
            return {"emitted": True}
        if method == "ui.debug.memory.delete" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            self._memory_manager().delete_fact(str(params.get("id") or params.get("fact_id") or ""))
            return {"emitted": True}
        if method == "ui.debug.tray.trigger" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            from PySide6.QtCore import QTimer

            overlay = self._ensure_overlay()
            requested = str(params.get("label") or "").strip()
            action = next(
                (item for item in overlay._tray_menu.actions() if item.text().casefold() == requested.casefold()),
                None,
            )
            if action is None:
                return {
                    "triggered": False,
                    "available": [item.text() for item in overlay._tray_menu.actions() if item.text()],
                }
            label = action.text()
            QTimer.singleShot(0, action.trigger)
            return {"triggered": True, "label": label}
        if method == "ui.debug.provider_badge.click" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            overlay = self._ensure_overlay()
            overlay._provider_badge.click()
            return {"clicked": True, "provider": overlay._provider_badge_mode()}
        if method == "ui.debug.bubble.stop.click" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            from PySide6.QtCore import Qt
            from PySide6.QtTest import QTest

            bubble = self._ensure_bubble()
            close_cancels = bool(getattr(bubble, "_close_cancels", False))
            QTest.mouseClick(
                bubble,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                bubble._close_rect().center(),
            )
            return {
                "clicked": True,
                "close_cancels": close_cancels,
                "visible_after": bool(bubble.isVisible()),
            }
        if method == "ui.debug.bubble.snapshot" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            bubble = self._ensure_bubble()
            return {
                "visible": bool(bubble.isVisible()),
                "text": str(getattr(bubble, "_full_text", "") or ""),
                "close_cancels": bool(getattr(bubble, "_close_cancels", False)),
            }
        if method == "ui.debug.intent.submit" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            overlay = self._intent
            # Qt's macOS offscreen backend can keep a valid, enabled top-level
            # picker hidden even after deferred activation. Debug acceptance
            # calls should exercise that live picker instead of treating the
            # backend's visibility limitation as a missing UI surface.
            offscreen = os.environ.get("QT_QPA_PLATFORM", "").strip().casefold() == "offscreen"
            if overlay is None or (not overlay.isVisible() and not offscreen):
                popup = next(
                    (
                        item
                        for item in reversed(tuple(self._rewrite_annotations.values()))
                        if item.isVisible() and item.state == "composing"
                    ),
                    None,
                )
                if popup is None:
                    return {"submitted": False, "reason": "no_visible_intent"}
                overlay = popup
            if not overlay.isEnabled():
                return {"submitted": False, "reason": "context_capture_pending"}
            text = str(params.get("text") or "").strip()
            if not text:
                return {"submitted": False, "reason": "empty_text"}
            field = getattr(overlay, "_input_line", None)
            if field is not None:
                overlay._enter_custom_mode(drop_trigger_key=False)
                field.setPlainText(text)
                overlay._fire_custom()
            else:
                field = overlay._comment
                field.setPlainText(text)
                overlay._submit(False)
            return {"submitted": True, "text": text}
        if method == "ui.debug.rewrite.accept" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            popup = next(
                (
                    item
                    for item in reversed(tuple(self._rewrite_annotations.values()))
                    if item.isVisible() and item.state == "proposal"
                ),
                None,
            )
            if popup is None:
                return {"clicked": False, "reason": "no_visible_proposal"}
            popup._accept.click()
            return {"clicked": True}
        if method == "ui.debug.shell.close_aux_windows" and os.environ.get(
            "OPENWAND_UI_DEBUG_METHODS"
        ):
            return self._debug_close_aux_windows()
        if method == "ui.debug.shell.snapshot" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            from PySide6.QtWidgets import QApplication

            overlay = self._ensure_overlay()
            visible = [widget for widget in QApplication.topLevelWidgets() if widget.isVisible()]
            settings_dialog = next(
                (widget for widget in visible if type(widget).__name__ == "SettingsDialog"),
                None,
            )
            return {
                "overlay_state": str(getattr(overlay, "_current_state", "")),
                "icon_visible": bool(overlay._icon_label.isVisible()),
                "chat_visible": self._chat_is_visible(),
                "memory_visible": bool(self._memory_viewer is not None and self._memory_viewer.isVisible()),
                "addons_visible": bool(self._addons_dialog is not None and self._addons_dialog.isVisible()),
                "runtime_status_visible": bool(
                    self._runtime_status_dialog is not None and self._runtime_status_dialog.isVisible()
                ),
                "settings_visible": settings_dialog is not None,
                "settings_ready": bool(
                    settings_dialog is not None
                    and getattr(settings_dialog, "_values_loaded", False)
                    and not getattr(settings_dialog, "_building_deferred_pages", False)
                    and not getattr(settings_dialog, "_pending_page_builds", ())
                ),
                "provider_controls_visible": any(
                    type(widget).__name__ == "HarnessControlsDialog" for widget in visible
                ),
                "visible_window_titles": [widget.windowTitle() for widget in visible],
            }
        if method == "ui.debug.settings.action" and os.environ.get("OPENWAND_UI_DEBUG_METHODS"):
            return self._debug_settings_action(**params)
        if method == "ui.reload_config":
            return self._reload_config()
        if method == "ui.show_overlay":
            return self._show_overlay(**params)
        if method == "ui.prewarm_intent":
            return self._prewarm_intent()
        if method == "ui.show_intent":
            return self._show_intent(**params)
        if method == "ui.rewrite.annotation.show":
            return self._rewrite_annotation_show(**params)
        if method == "ui.rewrite.annotation.processing":
            return self._rewrite_annotation_processing(**params)
        if method == "ui.rewrite.annotation.proposal":
            return self._rewrite_annotation_proposal(**params)
        if method == "ui.rewrite.annotation.anchor":
            return self._rewrite_annotation_anchor(**params)
        if method == "ui.rewrite.annotation.failure":
            return self._rewrite_annotation_failure(**params)
        if method == "ui.rewrite.annotation.remove":
            return self._rewrite_annotation_remove(**params)
        if method == "ui.rewrite.held_count":
            return self._rewrite_held_count(**params)
        if method == "ui.intent.context_items":
            return self._update_intent_context_items(**params)
        if method == "ui.intent.action_provider":
            return self._update_intent_action_provider(**params)
        if method == "ui.intent.activate":
            return self._activate_intent()
        if method == "ui.show_snip":
            return self._show_snip()
        if method == "ui.overlay.state":
            return self._overlay_state(**params)
        if method == "ui.overlay.amplitude":
            return self._overlay_amplitude(**params)
        if method == "ui.reply.presentation":
            return self._reply_presentation(**params)
        if method == "ui.reply.reset":
            return self._reply_reset()
        if method == "ui.reply.thinking":
            return self._reply_thinking()
        if method == "ui.reply.listening":
            return self._reply_listening()
        if method == "ui.reply.track_speech":
            return self._reply_track_speech()
        if method == "ui.reply.start_reveal":
            return self._reply_start_reveal()
        if method == "ui.reply.schedule_words":
            return self._reply_schedule_words(**params)
        if method == "ui.reply.notice":
            return self._reply_notice(**params)
        if method == "ui.reply.transcript":
            return self._reply_transcript(**params)
        if method == "ui.reply.reading":
            return self._reply_reading(**params)
        if method == "ui.reply.labeled_text":
            return self._reply_labeled_text(**params)
        if method == "ui.voice.candidates":
            return self._voice_candidates(**params)
        if method == "ui.health.show":
            return self._health_show(**params)
        if method == "ui.privacy.report":
            return self._privacy_report(**params)
        if method == "ui.runtime_status.show":
            return self._runtime_status_show(**params)
        if method == "ui.runtime_status.append":
            return self._runtime_status_append(**params)
        if method == "ui.reply.chunk":
            return self._reply_chunk(**params)
        if method == "ui.reply.image":
            return self._reply_image(**params)
        if method == "ui.reply.done":
            return self._reply_done(**params)
        if method == "ui.reply.undo_ready":
            return self._reply_undo_ready(**params)
        if method == "ui.live_voice.session":
            return self._live_voice_session(**params)
        if method == "ui.live_voice.transcript":
            return self._live_voice_transcript(**params)
        if method == "ui.live_voice.ready":
            return self._live_voice_ready(**params)
        if method == "ui.context.clear":
            return self._context_clear()
        if method == "ui.context.add_item":
            return self._context_add_item(**params)
        if method == "ui.context.summary":
            return self._context_summary(**params)
        if method == "ui.chat.chunk":
            return self._chat_chunk(**params)
        if method == "ui.chat.done":
            return self._chat_done(**params)
        if method == "ui.chat.background_result":
            return self._chat_background_result(**params)
        if method == "ui.chat.error":
            return self._chat_error(**params)
        if method == "ui.chat.context_preview":
            return self._chat_context_preview(**params)
        if method == "ui.chat.capture_context":
            return self._chat_capture_context(**params)
        if method == "ui.chat.capture_cancelled":
            return self._chat_capture_cancelled(**params)
        if method == "ui.chat.message_actions":
            return self._chat_message_actions(**params)
        if method == "ui.chat.message_action_result":
            return self._chat_message_action_result(**params)
        if method == "ui.chat.add_conversation":
            return self._chat_add_conversation(**params)
        if method == "ui.chat.begin_conversation":
            return self._chat_begin_conversation(**params)
        if method == "ui.chat.active_history":
            return self._chat_active_history()
        if method == "ui.chat.ingest":
            return self._chat_ingest()
        if method == "ui.live_file.approval.request":
            return self._live_file_approval_request(**params)
        if method == "ui.action.preview.request":
            return self._action_preview_request(**params)
        if method == "ui.action.progress":
            return self._action_progress(**params)
        if method == "ui.privacy.review.request":
            return self._privacy_review_request(**params)
        if method == "ui.show_chat":
            return self._show_chat(force_new=bool(params.get("new", False)))
        if method == "ui.show_settings":
            return self._show_settings(**params)
        if method == "ui.settings.is_open":
            return self._settings_is_open()
        if method == "ui.show_memory":
            return self._show_memory(**params)
        if method == "ui.show_addons":
            return self._show_addons(**params)
        if method == "ui.show_addon_settings":
            return self._show_specific_addon_settings(**params)
        if method == "ui.show_addon_settings":
            return self._show_specific_addon_settings(**params)
        if method == "ui.addons.tray_actions":
            return self._set_addon_tray_actions(**params)
        if method == "ui.show_virtual_workspace":
            return self._show_virtual_workspace(**params)
        if method == "ui.show_agent_task":
            return self._show_agent_task(**params)
        if method == "ui.show_agent_history":
            return self._show_agent_history(**params)
        if method == "ui.agent.notify_approval":
            return self._agent_notify_approval(**params)
        if method == "ui.agent.log":
            return self._agent_log(**params)
        if method == "ui.agent.trace":
            return self._agent_trace(**params)
        if method == "ui.agent.done":
            return self._agent_done(**params)
        if method == "ui.agent.approval.request":
            return self._agent_approval_request(**params)
        if method == "ui.agent.history.detail":
            return self._agent_history_detail(**params)
        raise ValueError(f"unknown method: {method}")

    def _reload_config(self) -> dict[str, Any]:
        """Handle reload config for qt protocol host."""
        import config

        config.reload()
        self._conversation_scope_key = self._configured_conversation_scope()
        try:
            if self._chat is not None:
                self._chat.refresh_model_label()
        except (AttributeError, RuntimeError):
            pass
        try:
            if self._overlay is not None:
                self._overlay.apply_settings()
        except Exception:
            traceback.print_exc()
        try:
            from ui.shared.theme import apply_app_theme

            apply_app_theme(self._app)
        except Exception:
            traceback.print_exc()
        return {"ok": True}

    def _ensure_overlay(
        self,
        addon_tray_actions: list[dict[str, Any]] | None = None,
    ):
        """Ensure overlay."""
        if self._overlay is None:
            from ui.overlay import IconOverlay, OverlaySignals

            self._overlay_signals = OverlaySignals()
            self._overlay = IconOverlay(
                self._overlay_signals,
                addon_tray_actions=addon_tray_actions,
            )
            # Keep audio ownership out of the UI process. Bubble fast-forward
            # and hide/reset callbacks are routed over protocol to the supervisor.
            try:
                self._overlay._bubble.set_speed_callback(
                    lambda enabled: self.emit("ui.bubble.speed", {"enabled": bool(enabled)})
                )
            except Exception:
                traceback.print_exc()
            self._overlay_signals.bubble_speed.connect(
                lambda enabled: self.emit("ui.bubble.speed", {"enabled": bool(enabled)})
            )
            self._overlay_signals.bubble_stop_requested.connect(
                lambda: self.emit("ui.bubble.stop", {})
            )
            self._overlay_signals.summon_caller.connect(
                lambda idx: self.emit("ui.summon_caller", {"caller_idx": int(idx)})
            )
            self._overlay_signals.rewrite_send_all.connect(
                lambda: self.emit("ui.rewrite.send_all", {})
            )
            self._overlay_signals.show_snip_overlay.connect(
                lambda: self.emit("ui.request_snip", {})
            )
            self._overlay_signals.show_new_chat.connect(lambda: self._show_chat(force_new=True))
            self._overlay_signals.show_last_chat.connect(lambda: self._show_chat(force_new=False))
            self._overlay_signals.show_memory_viewer.connect(
                lambda: self.emit("ui.memory.open_requested", {})
            )
            self._overlay_signals.show_settings.connect(
                lambda: self.emit("ui.settings.open_requested", {})
            )
            self._overlay_signals.show_addon_manager.connect(
                lambda: self.emit("ui.addons.open_requested", {})
            )
            self._overlay_signals.show_runtime_status.connect(
                lambda: self.emit("ui.runtime_status.open_requested", {})
            )
            self._overlay_signals.show_agent_task.connect(
                lambda: self.emit("ui.agent.task_requested", {})
            )
            self._overlay_signals.show_agent_history.connect(
                lambda: self.emit("ui.agent.history_requested", {})
            )
            self._overlay_signals.request_setup_check.connect(
                lambda: self.emit("ui.health.requested", {"source": "settings"})
            )
            self._overlay_signals.context_items_dropped.connect(self._context_items_dropped)
            self._overlay_signals.remove_dropped_item.connect(
                lambda idx: self.emit("ui.context.remove", {"index": int(idx)})
            )
            self._overlay_signals.bubble_highlight.connect(self._bubble_highlight)
            self._overlay_signals.settings_applied.connect(self._settings_applied)
        elif addon_tray_actions is not None:
            self._overlay.set_addon_tray_actions(addon_tray_actions)
        return self._overlay

    def _set_addon_tray_actions(
        self,
        actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Publish enabled addon actions into OpenWand's native tray menu."""
        overlay = self._ensure_overlay()
        normalized = list(actions or [])
        overlay.set_addon_tray_actions(normalized)
        return {"ok": True, "count": len(normalized)}

    def _show_virtual_workspace(
        self,
        endpoint: str = "",
        activate: bool = True,
    ) -> dict[str, Any]:
        """Show the addon workspace in a real OpenWand-owned Qt window."""
        from ui.virtual_workspace_window import VirtualWorkspaceWindow, _validated_endpoint

        base_url, _token = _validated_endpoint(endpoint)
        existing = getattr(self, "_virtual_workspace_window", None)
        if existing is not None and existing.isVisible() and existing.endpoint_base == base_url:
            if activate:
                existing.raise_()
                existing.activateWindow()
            return {"shown": True, "reused": True, "native": True}
        if existing is not None:
            existing.close()

        window = VirtualWorkspaceWindow(
            endpoint,
            on_start_task=self._start_virtual_workspace_task,
            on_agent_control=self._control_virtual_workspace_agent,
        )
        self._virtual_workspace_window = window

        def _clear(_obj: Any = None, current: Any = window) -> None:
            if self._virtual_workspace_window is current:
                self._virtual_workspace_window = None

        window.destroyed.connect(_clear)
        if not activate:
            from PySide6.QtCore import Qt

            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        window.show()
        if activate:
            window.raise_()
            window.activateWindow()
        return {"shown": True, "reused": False, "native": True}

    def _start_virtual_workspace_task(self, objective: str, scope_folder: str) -> None:
        """Start a scoped task, splitting only clearly independent file work."""
        from dataclasses import asdict

        import config
        from core.agent.task_spec import agent_task_spec_from_dict
        from core.agent.workspace_parallel import split_independent_workspace_objective

        clean_objective = str(objective or "").strip()[:8_000]
        scope = str(scope_folder or "").strip()
        if not clean_objective or not scope:
            return
        first_line = clean_objective.splitlines()[0].strip()
        title = (first_line[:72] or "Virtual workspace task").rstrip(" .")
        independent_tasks = split_independent_workspace_objective(clean_objective)
        agents = []
        if independent_tasks:
            agents = [{
                "name": "Coordinator",
                "role": "Coordinator",
                "provider": "same as task",
                "model": "same as task",
                "responsibility": (
                    "Watch the independent worker results, identify any unfinished requested file, "
                    "and return the final completion summary. Do not create or edit files yourself."
                ),
            }]
            agents.extend({
                "name": f"Worker {index}",
                "role": "Implementer",
                "provider": "same as task",
                "model": "same as task",
                "responsibility": (
                    f"Complete only this independent task: {task.objective} "
                    f"Your sole writable target is {task.target_path}. Return at most one file-changing tool call."
                ),
            } for index, task in enumerate(independent_tasks, 1))
        spec = agent_task_spec_from_dict({
            "title": title,
            "objective": (
                clean_objective
                + "\n\nWork only inside the assigned OpenWand Virtual Workspace. "
                "Use only the tools listed in the agent prompt. For a new file, call "
                "create_file with a concrete relative path and the complete content. "
                "Use create_file_base64 only for short binary content. The workspace can preview "
                "text, code, Markdown, HTML, CSV/TSV, SVG and common images, and PDF files. "
                "Do not call virtual_workspace_* tools; they are not available to this runner."
            ),
            "scope_folder": scope,
            "sandbox_mode": "workspace-write: virtual workspace only",
            "approval_policy": "per-tool permissions",
            "provider": "same as app",
            "model": "",
            "reasoning_effort": str(getattr(config, "CHAT_REASONING_EFFORT", "") or "medium"),
            "max_runtime_minutes": 30,
            "max_turns": 12,
            "full_turn_max_tokens": 4096,
            "delta_turn_max_tokens": 3072,
            "pause_holds_terminal_final": False,
            "finish_on_successful_tools": True,
            "max_tool_calls_per_turn": 0,
            "allow_shell": False,
            "allow_network": False,
            "allow_git": False,
            "allow_file_create": True,
            "allow_file_edit": True,
            "allow_file_delete": False,
            "shell_permission_mode": "never permit",
            "network_permission_mode": "never permit",
            "git_permission_mode": "never permit",
            "file_create_permission_mode": "auto",
            "file_edit_permission_mode": "auto",
            "file_delete_permission_mode": "never permit",
            "required_context": (
                "The user is watching a graphical virtual desktop. Keep useful artifacts inside "
                "the assigned scope and use clear filenames so changes are visible. Use only tools "
                "listed in the agent prompt. For a new file, use create_file with a concrete path "
                "and content; short binary files may use create_file_base64. Prefer text-native "
                "formats such as HTML, Markdown, CSV, or SVG when they satisfy the request because "
                "the user can see them immediately. Never call virtual_workspace_* tools. "
                "Follow the user's exact requested filenames. Keep an explicit checklist of requested "
                "targets and never invent support files. When the user explicitly requests several "
                "independent files or multiple file tool calls in one response, include all of those "
                "file calls in that response; OpenWand will display and apply each completed call as it "
                "arrives. After successful writes, do not list or read files unless the user explicitly "
                "requested verification. A successful file tool result verifies the exact written "
                "content; do not spend another turn reading it unless the user explicitly requested "
                "separate verification."
            ),
            "completion_criteria": (
                "The requested artifacts exist inside the virtual workspace and their contents "
                "match the request. A successful create or write result is sufficient verification."
            ),
            "report_format": "Short completion summary and files created or updated",
            "parallel_read_only_briefing": False,
            "agents": agents,
            "parallel_execution": bool(independent_tasks),
            "max_parallel_agents": min(4, len(independent_tasks or ())) if independent_tasks else 1,
        })
        self._virtual_workspace_agent_active = True
        self.emit("ui.agent.run_requested", {"spec": asdict(spec)})

    def _control_virtual_workspace_agent(self, action: str) -> None:
        """Route the embedded desktop controls to the active scoped agent run."""
        action = str(action or "").strip().lower()
        if not bool(getattr(self, "_virtual_workspace_agent_active", False)):
            return
        if action == "cancel":
            self.emit("ui.agent.cancel_requested", {})
        elif action == "pause":
            self.emit("ui.agent.pause_requested", {})
        elif action == "resume":
            self.emit("ui.agent.resume_requested", {})

    def _settings_applied(self, payload: dict[str, Any] | None = None) -> None:
        """Handle settings applied for qt protocol host."""
        self._reload_config()
        self.emit("ui.settings.applied", payload or {})

    def _ensure_bubble(self):
        """Ensure bubble."""
        if self._bubble is None:
            overlay = self._ensure_overlay()
            self._bubble = getattr(overlay, "_bubble", None)
            if self._bubble is None:
                from ui.bubble import SpeechBubble

                self._bubble = SpeechBubble()
                self._bubble.set_stop_callback(lambda: self.emit("ui.bubble.stop", {}))
        return self._bubble

    def _show_overlay(
        self,
        addon_tray_actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Show overlay."""
        overlay = self._ensure_overlay(addon_tray_actions)
        overlay.show()
        overlay.raise_()
        self._show_onboarding_if_needed()
        return {"shown": True}

    def _show_onboarding_if_needed(self) -> None:
        """Queue the profile wizard after the overlay has received its first frame."""
        try:
            from PySide6.QtCore import QTimer

            from ui.onboarding import OnboardingWizard, should_show_onboarding
            from ui.settings_panel import env as settings_env

            if self._onboarding is not None:
                return
            if not should_show_onboarding(
                settings_env.read_settings_env(),
                env_file_exists=settings_env.ENV_PATH.exists(),
            ):
                return

            def show() -> None:
                if self._onboarding is not None:
                    return
                wizard = OnboardingWizard(on_complete=self._onboarding_complete)
                self._onboarding = wizard
                wizard.finished.connect(
                    lambda _result, w=wizard: setattr(self, "_onboarding", None)
                    if self._onboarding is w else None
                )
                wizard.show()
                wizard.raise_()
                wizard.activateWindow()

            QTimer.singleShot(250, show)
        except Exception:
            log.exception("could not open first-run profile wizard")

    def _onboarding_complete(self, open_chat: bool) -> None:
        """Apply saved wizard settings, then optionally open the first chat."""
        self._settings_applied({"source": "onboarding"})
        if open_chat:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, lambda: self._show_chat(force_new=True))

    def _prewarm_intent(self) -> dict[str, Any]:
        """Warm intent overlay imports and first widget construction."""
        if getattr(self, "_intent_prewarmed", False):
            return {"prewarmed": True, "cached": True}
        try:
            from ui.intent_overlay import IntentOverlay

            if sys.platform == "win32":
                try:
                    import keyboard  # type: ignore  # noqa: F401
                except Exception:
                    log.debug("keyboard prewarm failed", exc_info=True)
            warm = IntentOverlay(caller_idx=0, context_items=[])
            warm.close()
            warm.deleteLater()
            self._intent_prewarmed = True
            return {"prewarmed": True}
        except Exception as exc:
            log.debug("intent prewarm failed", exc_info=True)
            return {"prewarmed": False, "error": str(exc)}

    def _rewrite_annotation_show(
        self,
        annotation_id: str = "",
        display_number: int = 1,
        selected_text: str = "",
        source_window_id: int = 0,
        source_pid: int = 0,
        source_label: str = "",
        selection_rect: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Open a focused Rewrite comment composer for one captured selection."""
        from ui.rewrite_annotation import RewriteAnnotationPopup

        key = str(annotation_id or "").strip()
        if not key:
            return {"shown": False, "reason": "missing_annotation_id"}
        self._rewrite_annotation_remove(key)
        popup = RewriteAnnotationPopup(
            annotation_id=key,
            display_number=max(1, int(display_number or 1)),
            selected_text=str(selected_text or ""),
            source_window_id=int(source_window_id or 0),
            source_pid=int(source_pid or 0),
            source_label=str(source_label or ""),
            selection_rect=selection_rect,
        )
        self._rewrite_annotations[key] = popup
        self._rewrite_anchor_stats[key] = {
            "started_at": time.monotonic(),
            "refreshes": 0,
            "visible_samples": 0,
            "hidden_samples": 0,
            "position_changes": 0,
            "visibility_changes": 0,
            "source_counts": {},
            "last_rect": self._rewrite_anchor_rect_key(selection_rect),
            "last_visible": True,
        }

        popup.submitted.connect(
            lambda item_id, comment, include_document: self.emit(
                "ui.rewrite.annotation.submitted",
                {
                    "annotation_id": item_id,
                    "comment": comment,
                    "include_document": bool(include_document),
                },
            )
        )
        popup.held.connect(
            lambda item_id, comment, include_document: self.emit(
                "ui.rewrite.annotation.held",
                {
                    "annotation_id": item_id,
                    "comment": comment,
                    "include_document": bool(include_document),
                },
            )
        )

        def _cancel(item_id: str) -> None:
            self.emit("ui.rewrite.annotation.cancelled", {"annotation_id": item_id})
            self._rewrite_annotation_remove(item_id)

        def _decline(item_id: str) -> None:
            self.emit("ui.rewrite.annotation.declined", {"annotation_id": item_id})
            self._rewrite_annotation_remove(item_id)

        popup.cancel_requested.connect(_cancel)
        popup.declined.connect(_decline)
        popup.accept_requested.connect(
            lambda item_id, replacement: self.emit(
                "ui.rewrite.annotation.accepted",
                {"annotation_id": item_id, "replacement_text": replacement},
            )
        )
        popup.revision_requested.connect(
            lambda item_id, prompt: self.emit(
                "ui.rewrite.annotation.revision_requested",
                {"annotation_id": item_id, "prompt": prompt},
            )
        )
        popup.anchor_refresh_requested.connect(
            lambda item_id: self.emit(
                "ui.rewrite.annotation.anchor_refresh_requested",
                {"annotation_id": item_id},
            )
        )
        def _destroyed(*_args, item_id: str = key, instance=popup) -> None:
            if self._rewrite_annotations.get(item_id) is not instance:
                return
            self._rewrite_annotations.pop(item_id, None)
            self._finish_rewrite_anchor_stats(item_id, instance)

        popup.destroyed.connect(_destroyed)
        popup.show_composer()
        result = {
            "created": True,
            "shown": bool(popup.isVisible()),
            "annotation_id": key,
            "window_title": popup.windowTitle(),
            "geometry": {
                "left": popup.x(),
                "top": popup.y(),
                "width": popup.width(),
                "height": popup.height(),
            },
        }
        log.info("rewrite annotation popup show: %s", result)
        return result

    def _rewrite_annotation_processing(
        self,
        annotation_id: str = "",
        display_number: int | None = None,
    ) -> dict[str, Any]:
        """Collapse an annotation into its no-close processing balloon."""
        popup = self._rewrite_annotations.get(str(annotation_id or ""))
        if popup is None:
            return {"updated": False, "reason": "not_found"}
        popup.show_processing(display_number=display_number)
        return {
            "updated": True,
            "state": "processing",
            "display_number": popup.display_number,
        }

    def _rewrite_annotation_proposal(
        self,
        annotation_id: str = "",
        replacement_text: str = "",
        copy_only: bool = False,
    ) -> dict[str, Any]:
        """Expand an annotation with a reviewable delayed-edit proposal."""
        popup = self._rewrite_annotations.get(str(annotation_id or ""))
        if popup is None:
            return {"updated": False, "reason": "not_found"}
        popup.show_proposal(str(replacement_text or ""), copy_only=bool(copy_only))
        return {"updated": True, "state": "proposal", "copy_only": bool(copy_only)}

    def _rewrite_annotation_anchor(
        self,
        annotation_id: str = "",
        selection_rect: dict[str, float] | None = None,
        visible: bool = True,
        source: str = "",
    ) -> dict[str, Any]:
        """Move or hide a Rewrite popup when its cached source range scrolls."""
        popup = self._rewrite_annotations.get(str(annotation_id or ""))
        if popup is None:
            return {"updated": False, "reason": "not_found"}
        popup.update_selection_anchor(selection_rect, visible=bool(visible))
        result = {
            "updated": True,
            "visible": bool(visible and popup.isVisible()),
            "source": str(source or ""),
            "geometry": {
                "left": popup.x(),
                "top": popup.y(),
                "width": popup.width(),
                "height": popup.height(),
            },
        }
        stats = self._rewrite_anchor_stats.get(str(annotation_id or ""))
        if stats is not None:
            requested_visible = bool(visible)
            rect_key = self._rewrite_anchor_rect_key(selection_rect)
            stats["refreshes"] += 1
            if requested_visible:
                stats["visible_samples"] += 1
            else:
                stats["hidden_samples"] += 1
            if requested_visible != bool(stats.get("last_visible")):
                stats["visibility_changes"] += 1
            stats["last_visible"] = requested_visible
            previous_rect = stats.get("last_rect")
            if rect_key is not None:
                if previous_rect is not None and rect_key != previous_rect:
                    stats["position_changes"] += 1
                stats["last_rect"] = rect_key
            source_key = str(source or "unknown")
            source_counts = stats["source_counts"]
            source_counts[source_key] = int(source_counts.get(source_key, 0)) + 1
        return result

    def _rewrite_annotation_failure(
        self,
        annotation_id: str = "",
        message: str = "",
    ) -> dict[str, Any]:
        """Retain and reopen a failed annotation so the user can retry."""
        popup = self._rewrite_annotations.get(str(annotation_id or ""))
        if popup is None:
            return {"updated": False, "reason": "not_found"}
        popup.show_failure(_translate_notice_text(str(message or "")))
        return {"updated": True, "state": "failed"}

    def _rewrite_annotation_remove(self, annotation_id: str = "") -> dict[str, Any]:
        """Remove one annotation popup/balloon without leaving UI state behind."""
        key = str(annotation_id or "")
        popup = self._rewrite_annotations.pop(key, None)
        if popup is None:
            return {"removed": False, "reason": "not_found"}
        try:
            popup.remove()
        except RuntimeError:
            pass
        self._finish_rewrite_anchor_stats(key, popup)
        return {"removed": True, "annotation_id": key}

    @staticmethod
    def _rewrite_anchor_rect_key(
        value: dict[str, float] | None,
    ) -> tuple[float, float, float, float] | None:
        """Return a stable comparison key for one screen-space selection rect."""
        if not isinstance(value, dict):
            return None
        try:
            return tuple(
                round(float(value[name]), 3)
                for name in ("left", "top", "width", "height")
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def _finish_rewrite_anchor_stats(self, key: str, popup: Any) -> None:
        """Write one compact anchor summary after a popup's lifecycle ends."""
        stats = self._rewrite_anchor_stats.pop(str(key or ""), None)
        if stats is None:
            return
        source_counts = stats.get("source_counts") or {}
        sources = ",".join(
            f"{source}:{count}"
            for source, count in sorted(source_counts.items())
        ) or "none"
        started_at = float(stats.get("started_at") or time.monotonic())
        lifetime_ms = max(0, round((time.monotonic() - started_at) * 1000))
        log.info(
            "rewrite anchor summary: annotation=%s lifetime_ms=%d refreshes=%d "
            "visible=%d hidden=%d position_changes=%d visibility_changes=%d "
            "sources=%s final_state=%s",
            key,
            lifetime_ms,
            int(stats.get("refreshes") or 0),
            int(stats.get("visible_samples") or 0),
            int(stats.get("hidden_samples") or 0),
            int(stats.get("position_changes") or 0),
            int(stats.get("visibility_changes") or 0),
            sources,
            str(getattr(popup, "state", "unknown") or "unknown"),
        )

    def _rewrite_held_count(self, count: int = 0) -> dict[str, Any]:
        """Keep the shared Send all comments control in sync with held state."""
        overlay = self._ensure_overlay()
        normalized = max(0, int(count or 0))
        overlay.set_held_rewrite_count(normalized)
        return {"updated": True, "count": normalized}

    def _show_intent(
        self,
        caller_idx: int = 0,
        target_hwnd: int = 0,
        context_items: list[dict[str, Any]] | None = None,
        initial_custom_text: str = "",
        focus_overlay: bool = False,
        defer_focus: bool = False,
        action_provider: dict[str, Any] | None = None,
        tool_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Show intent."""
        from ui.intent_overlay import IntentOverlay

        if self._intent is not None:
            self._intent.close_without_cancel()
            self._intent = None
        self._intent = IntentOverlay(
            caller_idx=caller_idx,
            target_hwnd=target_hwnd,
            context_items=_localized_context_items(context_items),
            conversation_options=self._intent_conversation_options(),
            project_options=self._intent_project_options(),
            active_project_id=self._intent_active_project_id(),
            conversation_namespace_label=self._intent_conversation_namespace_label(),
            initial_custom_text=initial_custom_text,
            focus_overlay=focus_overlay,
            defer_focus=defer_focus,
            action_provider=action_provider,
            tool_snapshot=tool_snapshot,
        )
        def _chosen(intent: str, custom: str) -> None:
            overlay = self._intent
            project_choice = overlay.project_choice() if overlay else {"mode": "existing", "project_id": self._active_project_id}
            applied_project = self._apply_intent_project_choice(project_choice)
            conversation_choice = overlay.conversation_choice() if overlay else {"mode": "new"}
            applied_choice = self._apply_intent_conversation_choice(conversation_choice)
            self.emit(
                "ui.intent.chosen",
                {
                    "caller_idx": caller_idx,
                    "intent": intent,
                    "custom": custom,
                    "intent_routing": (
                        overlay.selected_intent_routing()
                        if overlay and hasattr(overlay, "selected_intent_routing")
                        else {"mode": "answer", "source": "custom"}
                    ),
                    "context_choices": overlay.context_choices() if overlay else [],
                    "tool_choices": overlay.tool_choices() if overlay else [],
                    "project_choice": applied_project,
                    "conversation_choice": applied_choice,
                },
            )

        self._intent.intent_chosen.connect(_chosen)
        def _request_screenshot_snip() -> None:
            overlay = self._intent
            custom_text = overlay.current_custom_text() if overlay else ""
            self.emit(
                "ui.intent.snip.requested",
                {
                    "caller_idx": caller_idx,
                    "context_choices": overlay.context_choices() if overlay else [],
                    "tool_choices": overlay.tool_choices() if overlay else [],
                    "custom_text": custom_text,
                },
            )
            if overlay is not None:
                overlay.hide_without_cancel()
            self._show_snip(event_prefix="ui.intent.snip", app_region=self._snip_app_region(target_hwnd))

        self._intent.screenshot_snip_requested.connect(_request_screenshot_snip)
        def _request_selection_capture(custom_text: str = "") -> None:
            overlay = self._intent
            self.emit(
                "ui.intent.selection.requested",
                {
                    "caller_idx": caller_idx,
                    "context_choices": overlay.context_choices() if overlay else [],
                    "tool_choices": overlay.tool_choices() if overlay else [],
                    "custom_text": str(custom_text or ""),
                },
            )
            if overlay is not None:
                overlay.hide_without_cancel()

        self._intent.selection_capture_requested.connect(_request_selection_capture)
        self._intent.context_items_pasted.connect(self._context_items_dropped)
        def _context_source_removed(item_id: str, source_id: str) -> None:
            self.emit(
                "ui.intent.context.remove",
                {
                    "caller_idx": caller_idx,
                    "id": str(item_id or ""),
                    "source_id": str(source_id or ""),
                },
            )

        self._intent.context_source_removed.connect(_context_source_removed)
        def _context_source_reenabled(item_id: str) -> None:
            overlay = self._intent
            self.emit(
                "ui.intent.context.reenabled",
                {
                    "caller_idx": caller_idx,
                    "id": str(item_id or ""),
                    "context_choices": overlay.context_choices() if overlay else [],
                },
            )

        self._intent.context_source_reenabled.connect(_context_source_reenabled)
        def _cancelled() -> None:
            self._apply_cancelled_intent_conversation_choice(self._intent)
            self.emit("ui.intent.cancelled", {"caller_idx": caller_idx})

        self._intent.cancelled.connect(_cancelled)
        self._intent.destroyed.connect(lambda: setattr(self, "_intent", None))
        self._intent.show()
        self._intent.raise_()
        if sys.platform != "win32":
            if not defer_focus:
                self._intent.activateWindow()
                self._intent.setFocus()
        return {"shown": True, "caller_idx": caller_idx, "focus_deferred": bool(defer_focus)}

    def _activate_intent(self) -> dict[str, Any]:
        """Make a visible deferred picker interactive after context capture."""
        if self._intent is None:
            return {"activated": False, "reason": "no_intent"}
        try:
            self._intent.activate_after_context()
        except RuntimeError:
            self._intent = None
            return {"activated": False, "reason": "closed"}
        return {"activated": True}

    def _apply_cancelled_intent_conversation_choice(self, overlay) -> None:
        """Preserve only explicit chat-target changes from a canceled picker."""
        if overlay is not None and overlay.conversation_choice_touched():
            self._apply_intent_conversation_choice(overlay.conversation_choice())

    def _update_intent_context_items(
        self,
        context_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Refresh context chips on the currently open intent overlay."""
        if self._intent is None:
            return {"updated": False, "reason": "no_intent"}
        try:
            self._intent.update_context_items(_localized_context_items(context_items))
        except RuntimeError:
            self._intent = None
            return {"updated": False, "reason": "closed"}
        return {"updated": True}

    def _update_intent_action_provider(
        self,
        action_provider: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Refresh app-specific actions on the currently open intent overlay."""
        if self._intent is None:
            return {"updated": False, "reason": "no_intent"}
        try:
            self._intent.update_action_provider(action_provider)
        except RuntimeError:
            self._intent = None
            return {"updated": False, "reason": "closed"}
        return {"updated": True}

    def _show_snip(
        self,
        event_prefix: str = "ui.snip",
        app_region: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Show snip."""
        from ui.snip_overlay import SnipOverlay

        # Always present a FRESH selector (mirrors _show_intent). The old reuse
        # guard returned early when self._snip was non-None, but WA_DeleteOnClose
        # defers the destroyed callback that clears it -- so after the first snip
        # closed, the stale handle made the next press a silent no-op until the
        # old object was finally collected. That was the "overlay appears much
        # later" on repeat snips. Close any existing overlay and rebuild.
        t0 = time.monotonic()
        if self._snip is not None:
            try:
                self._snip.close()
            except Exception:
                pass
            self._snip = None
        t_closed = time.monotonic()
        snip = SnipOverlay(app_region=app_region)
        t_built = time.monotonic()
        self._snip = snip
        snip.region_selected.connect(lambda region: self.emit(f"{event_prefix}.region", region))
        snip.cancelled.connect(lambda: self.emit(f"{event_prefix}.cancelled", {}))
        # Identity-guard the clear so a previous overlay's deferred destroyed
        # signal can't null out a newer one's reference.
        snip.destroyed.connect(
            lambda *_: setattr(self, "_snip", None) if self._snip is snip else None
        )
        snip.show()
        snip.raise_()
        snip.activateWindow()
        snip.focus_for_capture()
        t_shown = time.monotonic()
        print(
            f"[snip.timing] close_old={t_closed - t0:.2f}s build={t_built - t_closed:.2f}s "
            f"show={t_shown - t_built:.2f}s total={t_shown - t0:.2f}s",
            file=sys.stderr,
            flush=True,
        )
        return {"shown": True}

    def _snip_app_region(self, target_hwnd: int = 0) -> dict[str, int] | None:
        """Best-effort app/window bounds for the snip overlay App mode."""
        if sys.platform == "win32":
            return self._win_snip_app_region(target_hwnd)
        if sys.platform == "darwin":
            return self._mac_snip_app_region()
        return None

    def _win_snip_app_region(self, target_hwnd: int = 0) -> dict[str, int] | None:
        """Return a Windows mss-compatible region for a target or top external window."""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = int(target_hwnd or 0)
            if not self._win_is_capture_window(hwnd):
                hwnd = self._win_top_external_window()
            if not hwnd:
                return None
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width <= 4 or height <= 4:
                return None
            return {"left": int(rect.left), "top": int(rect.top), "width": width, "height": height}
        except Exception:
            return None

    def _win_is_capture_window(self, hwnd: int) -> bool:
        """Return whether hwnd looks like a user app window, not OpenWand chrome."""
        if sys.platform != "win32" or not hwnd:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return False
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value or 0) == os.getpid():
                return False
            length = int(user32.GetWindowTextLengthW(hwnd) or 0)
            return length > 0
        except Exception:
            return False

    def _win_top_external_window(self) -> int:
        """Return the top visible non-OpenWand window on Windows."""
        if sys.platform != "win32":
            return 0
        try:
            import ctypes

            user32 = ctypes.windll.user32
            gw_hwndnext = 2
            hwnd = int(user32.GetTopWindow(0) or 0)
            seen: set[int] = set()
            while hwnd and len(seen) < 200:
                if hwnd in seen:
                    break
                seen.add(hwnd)
                if self._win_is_capture_window(hwnd):
                    return hwnd
                hwnd = int(user32.GetWindow(hwnd, gw_hwndnext) or 0)
        except Exception:
            return 0
        return 0

    def _mac_snip_app_region(self) -> dict[str, int] | None:
        """Return the frontmost non-OpenWand macOS window bounds, if available."""
        if sys.platform != "darwin":
            return None
        if os.environ.get("OPENWAND_MACOS_UI_QUARTZ_SNIP_APP_REGION") != "1":
            return None
        try:
            import Quartz  # type: ignore

            windows = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
            ) or []
            own_pid = os.getpid()
            for row in windows:
                try:
                    if int(row.get("kCGWindowOwnerPID") or 0) == own_pid:
                        continue
                    if int(row.get("kCGWindowLayer") or 0) != 0:
                        continue
                    bounds = row.get("kCGWindowBounds") or {}
                    left = int(round(float(bounds.get("X") or 0)))
                    top = int(round(float(bounds.get("Y") or 0)))
                    width = int(round(float(bounds.get("Width") or 0)))
                    height = int(round(float(bounds.get("Height") or 0)))
                    if width > 4 and height > 4:
                        return {"left": left, "top": top, "width": width, "height": height}
                except Exception:
                    continue
        except Exception:
            return None
        return None

    def _overlay_state(self, state: str = "idle") -> dict[str, Any]:
        """Handle overlay state for qt protocol host."""
        overlay = self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.set_state.emit(state)
        if state != "idle":
            overlay.show()
            overlay.raise_()
        return {"state": state}

    def _overlay_amplitude(self, amplitude: float = 0.0) -> dict[str, Any]:
        """Deliver a normalized playback level to the animated OpenWand mark."""
        value = max(0.0, min(1.0, float(amplitude or 0.0)))
        self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.set_mouth_amp.emit(value)
        return {"amplitude": value}

    def _reply_presentation(self, suppress_bubble: bool = False) -> dict[str, Any]:
        """Arm the presentation mode consumed by the next prompt reply reset."""
        self._next_prompt_suppress_bubble = bool(suppress_bubble)
        return {"suppress_bubble": bool(suppress_bubble)}

    def _reply_reset(self) -> dict[str, Any]:
        """Handle reply reset for qt protocol host."""
        suppress = bool(getattr(self, "_next_prompt_suppress_bubble", False))
        self._next_prompt_suppress_bubble = False
        self._suppress_prompt_bubble = suppress
        if suppress:
            bubble = getattr(self, "_bubble", None)
            if bubble is not None:
                bubble.hide()
            return {"reset": True, "suppressed": True}
        bubble = self._ensure_bubble()
        bubble.clear()
        return {"reset": True}

    def _reply_thinking(self) -> dict[str, Any]:
        """Handle reply thinking for qt protocol host."""
        if bool(getattr(self, "_suppress_prompt_bubble", False)):
            return {"thinking": True, "suppressed": True}
        self._ensure_bubble().start_thinking()
        return {"thinking": True}

    def _reply_listening(self, text: str = "") -> dict[str, Any]:
        """Handle reply listening for qt protocol host."""
        self._ensure_bubble().show_listening(text or None)
        return {"listening": True}

    def _reply_track_speech(self) -> dict[str, Any]:
        """Anchor the bubble to upcoming speech before playback starts."""
        if bool(getattr(self, "_suppress_prompt_bubble", False)):
            return {"tracking": True, "suppressed": True}
        self._ensure_bubble().start_speech_tracking()
        return {"tracking": True}

    def _reply_start_reveal(self) -> dict[str, Any]:
        """Handle reply start reveal for qt protocol host."""
        if bool(getattr(self, "_suppress_prompt_bubble", False)):
            return {"started": True, "suppressed": True}
        self._ensure_bubble().start_word_reveal()
        return {"started": True}

    def _reply_schedule_words(
        self, words: list | None = None, start_ms: list | None = None
    ) -> dict[str, Any]:
        """Buffer Cartesia word timestamps so the reveal tracks the spoken voice.

        Delivered before playback starts; the bubble holds them in
        ``_pre_audio_timestamps`` until ``start_word_reveal`` (fired by the
        audio.playback.started event) drains them anchored to the audio clock.
        """
        words = list(words or [])
        start_ms = [int(x) for x in (start_ms or [])]
        if bool(getattr(self, "_suppress_prompt_bubble", False)):
            return {"scheduled": len(words), "suppressed": True}
        self._ensure_bubble().schedule_words(words, start_ms)
        return {"scheduled": len(words)}

    def _reply_notice(
        self,
        text: str = "",
        timeout_ms: int = 12000,
        key: str = "",
        severity: str = "",
        log_mirrored: bool = False,
    ) -> dict[str, Any]:
        """Handle reply notice for qt protocol host."""
        # Mirror whatever the bubble shows into the runtime log so notices and
        # errors aren't on-screen only. Log the untranslated source (collapsed to
        # one line) so log lines stay stable and greppable across UI languages.
        # Logged at the notice's severity so the supervisor's stderr ingestion
        # classifies it correctly. Skipped when the supervisor already recorded
        # this notice itself (log_mirrored) — logging it again here would
        # duplicate the runtime event.
        collapsed = " | ".join(part for part in str(text or "").splitlines() if part)
        if not log_mirrored:
            level = {"error": logging.ERROR, "warning": logging.WARNING}.get(
                str(severity or "").strip().lower(), logging.INFO
            )
            log.log(level, "bubble notice: %s", collapsed or "(empty)")
        translated = _translate_notice_text(text)
        if _is_transient_local_tts_warmup_notice(text):
            log.info("suppressed transient local TTS warmup notice: %s", collapsed or "(empty)")
            return {"shown": False, "text": translated, "reason": "transient_local_tts_warmup"}
        bubble = self._ensure_bubble()
        notice_key = str(key or "").strip()
        if notice_key and getattr(self, "_active_notice_key", "") == notice_key and not bubble.isVisible():
            log.info("suppressed dismissed keyed notice %s: %s", notice_key, collapsed or "(empty)")
            return {"shown": False, "text": translated, "reason": "dismissed", "key": notice_key}
        if _is_speech_status_notice(text) and _bubble_has_active_prompt_reply(bubble):
            log.info("suppressed speech status notice during active reply: %s", collapsed or "(empty)")
            return {"shown": False, "text": translated, "reason": "active_reply"}
        severity_name = str(severity or "").strip().lower()
        if severity_name:
            try:
                bubble.show_notice(translated, timeout_ms=timeout_ms, severity=severity_name)
            except TypeError:
                bubble.show_notice(translated, timeout_ms=timeout_ms)
        else:
            bubble.show_notice(translated, timeout_ms=timeout_ms)
        if notice_key:
            self._active_notice_key = notice_key
        else:
            self._active_notice_key = ""
        return {"shown": True, "text": translated, "key": notice_key} if notice_key else {"shown": True, "text": translated}

    def _reply_undo_ready(self, text: str = "", timeout_ms: int = 30000) -> dict[str, Any]:
        """Show a completed rewrite with a one-click undo action."""
        translated = _translate_notice_text(text)
        self._ensure_bubble().show_notice(
            translated,
            timeout_ms=max(0, int(timeout_ms or 0)),
            actions=[(t("Undo"), lambda: self.emit("ui.rewrite.undo", {}))],
        )
        return {"shown": True, "text": translated, "action": "undo"}

    def _reply_transcript(self, text: str = "") -> dict[str, Any]:
        """Handle reply transcript for qt protocol host."""
        self._ensure_bubble().show_transcript(text)
        return {"shown": bool((text or "").strip()), "text": text}

    def _reply_reading(self, text: str = "") -> dict[str, Any]:
        """Show selected text that is being read aloud."""
        self._ensure_bubble().show_reading(text)
        return {"shown": bool((text or "").strip()), "text": text}

    def _reply_labeled_text(
        self,
        label: str = "",
        text: str = "",
        timeout_ms: int = 0,
        cancel_on_close: bool = True,
    ) -> dict[str, Any]:
        """Show labeled bubble text whose label is not counted as reply content."""
        clean_text = str(text or "").strip()
        clean_label = str(label or "").strip()
        self._ensure_bubble().show_labeled_text(
            clean_label,
            clean_text,
            timeout_ms=int(timeout_ms or 0),
            cancel_on_close=bool(cancel_on_close),
        )
        return {
            "shown": bool(clean_text),
            "label": clean_label,
            "text": clean_text,
            "label_excluded_from_reply": True,
        }

    def _voice_candidates(
        self,
        text: str = "",
        candidates: list | None = None,
        purpose: str = "voice",
    ) -> dict[str, Any]:
        """Ask the user to accept/edit a voice transcript candidate."""
        from PySide6.QtWidgets import QInputDialog

        choices = [str(item).strip() for item in (candidates or []) if str(item).strip()]
        if not choices and str(text or "").strip():
            choices = [str(text).strip()]
        if not choices:
            return {"accepted": False, "text": ""}
        parent = self._ensure_overlay()
        title = "Dictation transcript" if purpose == "dictation" else "Voice transcript"
        chosen, accepted = QInputDialog.getItem(
            parent,
            title,
            "Choose or edit the transcript:",
            choices,
            0,
            True,
        )
        return {"accepted": bool(accepted), "text": str(chosen or "").strip()}

    def _reply_chunk(
        self,
        text: str = "",
        is_thought: bool = False,
        is_progress: bool = False,
        annotations: list | None = None,
    ) -> dict[str, Any]:
        """Handle reply chunk for qt protocol host."""
        if bool(getattr(self, "_suppress_prompt_bubble", False)):
            return {
                "appended": len(text or ""),
                "is_progress": bool(is_progress),
                "suppressed": True,
            }
        bubble = self._ensure_bubble()
        if is_progress and not is_thought:
            # Transient status (e.g. "Using tools...") — show it as a preview that
            # the first real reply token replaces, not appended reply content.
            bubble.show_progress(_translate_notice_text(text))
        else:
            visible_text = _translate_notice_text(text) if is_progress else text
            bubble.append_chunk(visible_text, is_thought=is_thought, annotations=annotations)
        return {"appended": len(text or ""), "is_progress": bool(is_progress)}

    def _action_progress(
        self,
        text: str = "",
        stage: str = "",
        sequence: int = 0,
        terminal: bool = False,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Replace the bubble's single truthful action-status line."""
        clean_text = _translate_notice_text(text)
        if clean_text:
            self._ensure_bubble().show_progress(clean_text)
        return {
            "shown": bool(clean_text),
            "stage": str(stage or ""),
            "sequence": int(sequence or 0),
            "terminal": bool(terminal),
        }

    def _reply_image(self, attachments: list | None = None) -> dict[str, Any]:
        """Render the first safe generated-image attachment in the speech bubble."""
        if bool(getattr(self, "_suppress_prompt_bubble", False)):
            return {"shown": False, "suppressed": True}
        from core.conversation_store import store as conversation_store

        encoded = ""
        for raw in attachments or []:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind") or "image").strip().lower()
            if kind != "image":
                continue
            data_url = str(raw.get("data_url") or "").strip()
            if data_url:
                encoded = data_url
            else:
                path = str(raw.get("path") or "").strip()
                if not path:
                    continue
                ref = conversation_store.external_file_attachment(
                    path,
                    kind="image",
                    source=str(raw.get("source") or "generated_image"),
                )
                encoded = conversation_store.attachment_image_base64(ref)
            if encoded:
                break
        shown = bool(encoded and self._ensure_bubble().show_image(encoded))
        return {"shown": shown}

    def _live_voice_session(self, active: bool = False) -> dict[str, Any]:
        """Enter/leave live voice caption mode (bubble held open while active)."""
        self._ensure_bubble().set_live_mode(bool(active))
        return {"active": bool(active)}

    def _live_voice_transcript(self, role: str = "", text: str = "") -> dict[str, Any]:
        """Append one live voice caption fragment to the bubble."""
        self._ensure_bubble().append_live_transcript(str(role or ""), str(text or ""))
        return {"appended": len(str(text or ""))}

    def _live_voice_ready(self) -> dict[str, Any]:
        """Show the connected hint in the caption bubble (session is live)."""
        self._ensure_bubble().show_live_ready()
        return {"shown": True}

    def _reply_done(self, flush: bool = True) -> dict[str, Any]:
        """Finish the reply bubble.

        flush=True reveals everything immediately (TTS playback ended, errors).
        flush=False lets a running WPM reveal drain at the configured speed -
        used when TTS is off, so BUBBLE_REVEAL_WPM is honored instead of the
        whole reply slamming in the moment the LLM finishes streaming.
        """
        if bool(getattr(self, "_suppress_prompt_bubble", False)):
            self._suppress_prompt_bubble = False
            return {"done": True, "suppressed": True}
        bubble = self._ensure_bubble()
        bubble.finish(flush_remaining=bool(flush))
        return {"done": True}

    @staticmethod
    def _context_item_payload(item: Any) -> dict[str, Any]:
        """Handle context item payload for qt protocol host."""
        if isinstance(item, dict):
            return {
                "name": str(item.get("name") or item.get("label") or "Context"),
                "content": item.get("content", ""),
                "type": str(item.get("type") or item.get("item_type") or "text"),
            }
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            return {"name": str(item[0]), "content": item[1], "type": str(item[2])}
        return {"name": "Context", "content": str(item), "type": "text"}

    def _context_items_dropped(self, items: Any) -> None:
        """Handle context items dropped for qt protocol host."""
        payload = [self._context_item_payload(item) for item in (items or [])]
        self.emit("ui.context.dropped", {"items": payload})

    def _context_clear(self) -> dict[str, Any]:
        """Handle context clear for qt protocol host."""
        overlay = self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.drop_context_cleared.emit()
        return {"cleared": True, "overlay": bool(overlay)}

    def _context_add_item(self, name: str = "", item_type: str = "text") -> dict[str, Any]:
        """Handle context add item for qt protocol host."""
        self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.add_context_item.emit(
                _context_display_label(str(name or "Context")), str(item_type or "text")
            )
        return {"added": True}

    def _context_summary(self, items: list | None = None) -> dict[str, Any]:
        """Handle context summary for qt protocol host."""
        self._ensure_overlay()
        if self._overlay_signals is not None:
            pairs = [
                (
                    _context_display_label(str(item.get("label") or item.get("name") or "Context")),
                    str(item.get("type") or "text"),
                )
                for item in (items or [])
                if isinstance(item, dict)
            ]
            self._overlay_signals.show_context_summary.emit(pairs)
        return {"shown": len(items or [])}

    def _bubble_highlight(self, text: str, revealed_count: int, finished: bool) -> None:
        """Handle bubble highlight for qt protocol host."""
        self.emit(
            "ui.bubble.highlight",
            {"text": text, "revealed_count": int(revealed_count), "finished": bool(finished)},
        )

    def _memory_manager(self):
        """Handle memory manager for qt protocol host."""
        if self._memory is None:
            self._memory = MemoryProxy(self.emit)
        return self._memory

    # ------------------------------------------------------------------
    # Projects & conversation persistence
    # ------------------------------------------------------------------

    def _persist_conversations(self) -> None:
        """Handle persist conversations for qt protocol host."""
        try:
            from core.conversation_store import store as conversation_store
            conversation_store.save_conversations(self._all_conversations)
        except Exception:
            log.exception("failed to persist conversations")

    @staticmethod
    def _configured_conversation_scope() -> str:
        """Read the independent history namespace from the active chat route."""
        import config

        mode = str(getattr(config, "CHAT_EXECUTION_MODE", "openwand") or "openwand").strip().lower()
        owner = str(
            getattr(config, "CHAT_CONVERSATION_OWNER", "openwand") or "openwand"
        ).strip().lower()
        return mode if owner == "agent" and mode in {"codex", "claude"} else "openwand"

    def _intent_conversation_scope(self) -> str:
        """Return this UI host's current independent history namespace."""
        return str(getattr(self, "_conversation_scope_key", "openwand") or "openwand")

    def _intent_conversation_namespace_label(self) -> str:
        """Return a compact visible label for provider-owned overlay history."""
        scope = self._intent_conversation_scope()
        if scope == "codex":
            return "ChatGPT / Codex"
        if scope == "claude":
            return "Claude"
        return ""

    def _conversation_matches_intent_scope(self, conv: dict[str, Any]) -> bool:
        """Return whether a conversation belongs in the active route's picker."""
        from core.conversation_store import store as conversation_store

        return conversation_store.conversation_scope(conv) == self._intent_conversation_scope()

    def _apply_memory_project(self) -> None:
        """Apply memory project."""
        try:
            from core.conversation_store import store as conversation_store
            from core.memory_store import store as memory_store
            pid = self._active_project_id
            memory_store.set_active_project(
                None if pid == conversation_store.GENERAL_PROJECT_ID else pid
            )
        except Exception:
            log.exception("failed to apply memory project scope")

    def _set_active_project(self, project_id: str | None) -> None:
        """Set active project."""
        from core.conversation_store import store as conversation_store
        self._active_project_id = project_id or conversation_store.GENERAL_PROJECT_ID
        self._apply_memory_project()

    def _create_project(self, name: str):
        """Create project."""
        try:
            from core.conversation_store import store as conversation_store
            scope = self._intent_conversation_scope()
            if scope == "openwand":
                return conversation_store.add_project(name)
            return conversation_store.add_project(name, conversation_scope=scope)
        except Exception:
            log.exception("failed to create project %r", name)
            return None

    def _set_active_conversation(self, idx) -> None:
        """Chat window selected/started a conversation -> retarget hotkey prompts."""
        previous_idx = self._active_conversation_idx
        if idx is None or (isinstance(idx, int) and 0 <= idx < len(self._all_conversations)):
            self._active_conversation_idx = idx
        if isinstance(idx, int) and 0 <= idx < len(self._all_conversations):
            conv = self._all_conversations[idx]
            if not any(
                str(message.get("content") or "").strip()
                for message in conv.get("messages", [])
                if isinstance(message, dict)
            ):
                conv["conversation_scope"] = self._intent_conversation_scope()
        if (
            isinstance(idx, int)
            and idx != previous_idx
            and self._chat is not None
            and not bool(getattr(self._chat, "_streaming", False))
        ):
            title = self._chat_notice_title(idx)
            if title:
                try:
                    self._ensure_bubble().show_notice(f"{t('Continuing')}: {title}", timeout_ms=2500)
                except Exception:
                    log.debug("failed to show chat selection notice", exc_info=True)

    def _chat_notice_title(self, idx: int, limit: int = 48) -> str:
        """Return a compact conversation title for overlay selection notices."""
        if not (0 <= idx < len(self._all_conversations)):
            return ""
        conv = self._all_conversations[idx]
        override = str(conv.get("title_override") or "").strip()
        if override:
            return override[:limit] + ("..." if len(override) > limit else "")
        for msg in conv.get("messages", []):
            if msg.get("role") == "user" and msg.get("content"):
                text = " ".join(str(msg.get("content") or "").split())
                return text[:limit] + ("..." if len(text) > limit else "")
        return t("New chat")

    def _chat_conversation_subtitle(self, idx: int) -> str:
        """Return a short display timestamp for an intent overlay chat option."""
        if not (0 <= idx < len(self._all_conversations)):
            return ""
        conv = self._all_conversations[idx]
        raw = str(conv.get("updated_at") or conv.get("created_at") or "").strip()
        if not raw:
            return ""
        return raw.replace("T", " ").split("+", 1)[0].split(".", 1)[0]

    def _intent_project_options(self) -> list[dict[str, Any]]:
        """Return project options for the intent overlay selector."""
        from core.conversation_store import store as conversation_store

        scope = self._intent_conversation_scope()
        return [
            project
            for project in conversation_store.load_projects()
            if project.get("id") == conversation_store.GENERAL_PROJECT_ID
            or conversation_store.project_scope(project) == scope
        ]

    def _intent_active_project_id(self) -> str:
        """Return the project that should be selected when the intent overlay opens."""
        from core.conversation_store import store as conversation_store

        idx = self._active_conversation_idx
        if (
            isinstance(idx, int)
            and 0 <= idx < len(self._all_conversations)
            and self._conversation_matches_intent_scope(self._all_conversations[idx])
        ):
            return self._all_conversations[idx].get("project_id") or conversation_store.GENERAL_PROJECT_ID
        valid_projects = {
            str(project.get("id") or "") for project in self._intent_project_options()
        }
        if self._active_project_id in valid_projects:
            return self._active_project_id
        return conversation_store.GENERAL_PROJECT_ID

    def _intent_conversation_options(self, limit: int = 12) -> list[dict[str, Any]]:
        """Return latest-first chat history options for the intent overlay.

        Keep ``limit`` per project so switching projects in the picker does not
        hide older project conversations behind newer chats from other projects.
        """
        if not self._all_conversations:
            return []
        from core.conversation_store import store as conversation_store

        selected_idx = (
            self._active_conversation_idx
            if isinstance(self._active_conversation_idx, int)
            and 0 <= self._active_conversation_idx < len(self._all_conversations)
            and self._conversation_matches_intent_scope(
                self._all_conversations[self._active_conversation_idx]
            )
            else None
        )
        options: list[dict[str, Any]] = []
        seen: set[int] = set()
        counts_by_project: dict[str, int] = {}
        for idx in range(len(self._all_conversations) - 1, -1, -1):
            if not self._conversation_matches_intent_scope(self._all_conversations[idx]):
                continue
            project_id = self._all_conversations[idx].get("project_id") or conversation_store.GENERAL_PROJECT_ID
            if counts_by_project.get(project_id, 0) >= limit:
                continue
            options.append(
                {
                    "index": idx,
                    "title": self._chat_notice_title(idx, limit=72) or t("Conversation"),
                    "subtitle": self._chat_conversation_subtitle(idx),
                    "project_id": project_id,
                    "selected": selected_idx is not None and idx == selected_idx,
                }
            )
            seen.add(idx)
            counts_by_project[project_id] = counts_by_project.get(project_id, 0) + 1
        if selected_idx is not None and selected_idx not in seen:
            options.append(
                {
                    "index": selected_idx,
                    "title": self._chat_notice_title(selected_idx, limit=72) or t("Conversation"),
                    "subtitle": self._chat_conversation_subtitle(selected_idx),
                    "project_id": self._all_conversations[selected_idx].get("project_id") or conversation_store.GENERAL_PROJECT_ID,
                    "selected": True,
                }
            )
        return options

    def _apply_intent_project_choice(self, choice: dict[str, Any] | None) -> dict[str, Any]:
        """Apply the intent overlay's project selection before prompting."""
        from core.conversation_store import store as conversation_store

        choice = choice or {}
        mode = str(choice.get("mode") or "").strip().lower()
        if mode == "new_project":
            name = str(choice.get("name") or "").strip()
            project = self._create_project(name) if name else None
            project_id = (
                str(project.get("id") or "")
                if isinstance(project, dict)
                else conversation_store.GENERAL_PROJECT_ID
            )
            self._set_active_project(project_id)
            return {"mode": "existing", "project_id": project_id}
        project_id = str(choice.get("project_id") or "").strip() or conversation_store.GENERAL_PROJECT_ID
        valid = {str(project.get("id") or "") for project in self._intent_project_options()}
        if project_id not in valid:
            project_id = conversation_store.GENERAL_PROJECT_ID
        self._set_active_project(project_id)
        return {"mode": "existing", "project_id": project_id}

    def _apply_intent_conversation_choice(self, choice: dict[str, Any] | None) -> dict[str, Any]:
        """Apply the intent overlay's new/continue selection before prompting."""
        choice = choice or {}
        if str(choice.get("mode") or "").strip().lower() == "new":
            self._active_conversation_idx = None
            return {"mode": "new"}
        try:
            idx = int(choice.get("index"))
        except (TypeError, ValueError):
            idx = len(self._all_conversations) - 1 if self._all_conversations else -1
        if (
            0 <= idx < len(self._all_conversations)
            and self._conversation_matches_intent_scope(self._all_conversations[idx])
        ):
            self._active_conversation_idx = idx
            return {"mode": "continue", "index": idx}
        self._active_conversation_idx = None
        return {"mode": "new"}

    def _chat_active_history(self) -> dict[str, Any]:
        """Return prior turns + memory project for the active conversation.

        The supervisor replays ``history`` to the model (full continuation) and
        uses ``project_id`` (None = global) to scope memory in the brain. When
        starting fresh, history is empty and the project is the dropdown's.
        """
        from core.conversation_store import store as conversation_store
        idx = self._active_conversation_idx
        history: list[dict] = []
        context = ""
        if (
            idx is not None
            and 0 <= idx < len(self._all_conversations)
            and self._conversation_matches_intent_scope(self._all_conversations[idx])
        ):
            conv = self._all_conversations[idx]
            conversation_id = str(conv.get("id") or "")
            project = conv.get("project_id") or conversation_store.GENERAL_PROJECT_ID
            file_context = list(conv.get("file_context") or [])
            tool_context = self._normalized_tool_context(conv.get("tool_context") or {})
            context_policy = self._normalized_context_policy(conv.get("context_policy") or {})
            harness_sessions = dict(conv.get("harness_sessions") or {})
            harness_cwd = self._conversation_harness_cwd(conv)
            context = str(conv.get("context") or "")
            for m in conv.get("messages", []):
                if (
                    m.get("role") not in ("user", "assistant")
                    or not isinstance(m.get("content"), str)
                    or not m.get("content").strip()
                ):
                    continue
                item = {"role": m.get("role"), "content": m.get("content")}
                attachments = conversation_store.normalize_attachments(m.get("attachments"))
                if attachments:
                    item["attachments"] = attachments
                history.append(item)
        else:
            conversation_id = ""
            project = self._active_project_id
            file_context = []
            tool_context = {}
            context_policy = {}
            harness_sessions = {}
            harness_cwd = str(Path.cwd())
        memory_project = None if project == conversation_store.GENERAL_PROJECT_ID else project
        return {
            "history": history,
            "conversation_id": conversation_id,
            "project_id": memory_project,
            "context": context,
            "file_context": file_context,
            "tool_context": tool_context,
            "context_policy": context_policy,
            "harness_sessions": harness_sessions,
            "harness_cwd": harness_cwd,
        }

    @staticmethod
    def _conversation_harness_cwd(conv: dict[str, Any]) -> str:
        """Infer a harness workspace from existing local context and attachments."""
        import config
        from core.conversation_store import store as conversation_store

        provider = str(getattr(config, "CHAT_EXECUTION_MODE", "openwand") or "openwand").strip().lower()
        sessions = conv.get("harness_sessions")
        if provider in {"codex", "claude"} and isinstance(sessions, dict):
            session = sessions.get(provider)
            if isinstance(session, dict):
                raw = str(session.get("cwd") or "").strip()
                if raw:
                    path = Path(raw).expanduser()
                    if path.is_dir():
                        return str(path.resolve())

        for item in conv.get("file_context", []) or []:
            if not isinstance(item, dict):
                continue
            for key in ("root", "path"):
                raw = str(item.get(key) or "").strip()
                if not raw:
                    continue
                path = Path(raw).expanduser()
                if path.is_file():
                    path = path.parent
                if path.is_dir():
                    return str(path)
        for message in reversed(conv.get("messages", []) or []):
            if not isinstance(message, dict):
                continue
            for ref in conversation_store.normalize_attachments(message.get("attachments")):
                path = conversation_store.attachment_path(ref)
                if path.is_file():
                    return str(path.parent)
                if path.is_dir():
                    return str(path)
        return str(Path.cwd())

    def _make_chat_send_fn(self):
        """Create chat send fn."""
        def send_with_memory(messages: list, context_policy: dict | None = None):
            """Send with memory."""
            request_id = f"chat-{next(self._chat_request_ids)}"
            stream: queue.Queue[tuple[str, Any]] = queue.Queue()
            streamed_text = ""
            with self._chat_streams_lock:
                self._chat_streams[request_id] = stream
            payload = {"request_id": request_id, "messages": messages}
            normalized_policy = self._normalized_context_policy(context_policy or {})
            if normalized_policy:
                payload["context_policy"] = normalized_policy
            idx = self._active_conversation_idx
            if idx is not None and 0 <= idx < len(self._all_conversations):
                active_conversation = self._all_conversations[idx]
                payload["conversation_id"] = str(active_conversation.get("id") or "")
                payload["harness_sessions"] = dict(active_conversation.get("harness_sessions") or {})
                payload["harness_cwd"] = self._conversation_harness_cwd(active_conversation)
                if not normalized_policy:
                    stored_policy = self._normalized_context_policy(
                        self._all_conversations[idx].get("context_policy") or {}
                    )
                    if stored_policy:
                        payload["context_policy"] = stored_policy
                tool_context = self._normalized_tool_context(
                    self._all_conversations[idx].get("tool_context") or {}
                )
                if tool_context:
                    payload["tool_context"] = tool_context
            self.emit("ui.chat.request", payload)
            try:
                while True:
                    kind, payload = stream.get()
                    if kind == "chunk":
                        is_thought = False
                        is_progress = False
                        local_work = {}
                        harness_activity = {}
                        if isinstance(payload, dict):
                            chunk = str(payload.get("text") or "")
                            is_thought = bool(payload.get("is_thought"))
                            is_progress = bool(payload.get("is_progress"))
                            local_work = dict(payload.get("local_work") or {})
                            harness_activity = dict(payload.get("harness_activity") or {})
                        else:
                            chunk = str(payload or "")
                        if not is_thought and not is_progress and not local_work and not harness_activity:
                            streamed_text += chunk
                            yield chunk
                        else:
                            item = {"type": "chunk", "text": chunk}
                            if is_thought:
                                item["is_thought"] = True
                            if is_progress:
                                item["is_progress"] = True
                            if local_work:
                                item["local_work"] = local_work
                            if harness_activity:
                                item["harness_activity"] = harness_activity
                            yield item
                    elif kind == "done":
                        final_text = ""
                        file_context = []
                        assistant_attachments = []
                        tool_context = {}
                        context_snippets = []
                        annotations = []
                        user_annotations = []
                        display_segments = []
                        harness = {}
                        if isinstance(payload, dict):
                            final_text = str(payload.get("text") or "")
                            file_context = list(payload.get("file_context") or [])
                            assistant_attachments = list(payload.get("assistant_attachments") or [])
                            tool_context = self._normalized_tool_context(payload.get("tool_context") or {})
                            context_snippets = list(payload.get("context_snippets") or [])
                            annotations = list(payload.get("annotations") or [])
                            user_annotations = list(payload.get("user_annotations") or [])
                            display_segments = self._normalized_display_segments(
                                payload.get("display_segments") or []
                            )
                            harness = dict(payload.get("harness") or {})
                        elif payload is not None:
                            final_text = str(payload)
                        if (
                            file_context
                            or assistant_attachments
                            or tool_context
                            or context_snippets
                            or annotations
                            or user_annotations
                            or display_segments
                            or harness
                        ):
                            metadata = {
                                "type": "metadata",
                                "file_context": file_context,
                                "tool_context": tool_context,
                                "context_snippets": context_snippets,
                                "annotations": annotations,
                                "user_annotations": user_annotations,
                            }
                            if assistant_attachments:
                                metadata["assistant_attachments"] = assistant_attachments
                            if display_segments:
                                metadata["display_segments"] = display_segments
                            if harness:
                                metadata["harness"] = harness
                            yield metadata
                        if final_text and final_text != streamed_text:
                            yield {"type": "final", "text": final_text}
                        return
                    elif kind == "error":
                        raise RuntimeError(str(payload or "chat failed"))
            finally:
                with self._chat_streams_lock:
                    self._chat_streams.pop(request_id, None)

        return send_with_memory

    def _chat_stream(self, request_id: str):
        """Handle chat stream for qt protocol host."""
        with self._chat_streams_lock:
            return self._chat_streams.get(str(request_id))

    def _chat_chunk(
        self,
        request_id: str = "",
        conversation_index: int | None = None,
        text: str = "",
        is_progress: bool = False,
        is_thought: bool = False,
        local_work: dict | None = None,
        harness_activity: dict | None = None,
    ) -> dict[str, Any]:
        """Handle chat chunk for qt protocol host."""
        stream = self._chat_stream(request_id)
        chunk_payload = {
            "text": text,
            "is_progress": bool(is_progress),
            "is_thought": bool(is_thought),
            "local_work": dict(local_work or {}),
        }
        if harness_activity:
            chunk_payload["harness_activity"] = dict(harness_activity)
        if stream is not None:
            stream.put(("chunk", chunk_payload))
        elif conversation_index is not None:
            idx = int(conversation_index)
            chunk = dict(chunk_payload)
            state = getattr(self, "_external_reply_stream", None)
            if not isinstance(state, dict) or state.get("conversation_index") != idx:
                state = {"conversation_index": idx, "chunks": [], "attached_chat_id": None, "done": False}
                self._external_reply_stream = state
            state.setdefault("chunks", []).append(chunk)
            if self._chat is not None:
                attached_now = self._restore_external_reply_stream()
                if not attached_now:
                    self._chat.external_reply_chunk(idx, chunk)
            return {"queued": True}
        return {"queued": stream is not None}

    def _chat_done(
        self,
        request_id: str = "",
        conversation_index: int | None = None,
        text: str = "",
        file_context: list | None = None,
        tool_context: dict | None = None,
        context_snippets: list | None = None,
        annotations: list | None = None,
        user_annotations: list | None = None,
        display_segments: list | None = None,
        assistant_attachments: list | None = None,
        harness: dict | None = None,
    ) -> dict[str, Any]:
        """Handle chat done for qt protocol host."""
        import uuid as _uuid

        conversation_id = "conversation"
        idx = self._active_conversation_idx
        if idx is not None and 0 <= idx < len(self._all_conversations):
            conversation_id = str(self._all_conversations[idx].get("id") or conversation_id)
        durable_attachments = self._persist_assistant_attachments(
            assistant_attachments or [],
            conversation_id=conversation_id,
            message_id=f"generated-{_uuid.uuid4()}",
        )
        stream = self._chat_stream(request_id)
        if stream is not None:
            stream.put(
                (
                    "done",
                    {
                        "text": text,
                        "file_context": list(file_context or []),
                        "tool_context": self._normalized_tool_context(tool_context or {}),
                        "context_snippets": list(context_snippets or []),
                        "annotations": list(annotations or []),
                        "user_annotations": list(user_annotations or []),
                        "display_segments": self._normalized_display_segments(display_segments or []),
                        "assistant_attachments": durable_attachments,
                        "harness": dict(harness or {}),
                    },
                )
            )
        elif conversation_index is not None:
            idx = int(conversation_index)
            state = getattr(self, "_external_reply_stream", None)
            if not isinstance(state, dict) or state.get("conversation_index") != idx:
                state = {"conversation_index": idx, "chunks": [], "attached_chat_id": None}
                self._external_reply_stream = state
            state["done"] = True
            state["final_text"] = text
            if self._chat is not None:
                self._restore_external_reply_stream()
                self._chat.finish_external_reply_stream(idx, text)
            return {"queued": True}
        return {"queued": stream is not None}

    def _chat_error(self, request_id: str = "", error: str = "") -> dict[str, Any]:
        """Handle chat error for qt protocol host."""
        log.warning("chat error (request %s): %s", request_id or "?", error)
        stream = self._chat_stream(request_id)
        if stream is not None:
            stream.put(("error", error))
        return {"queued": stream is not None}

    def _chat_background_result(
        self,
        conversation_id: str = "",
        job_id: str = "",
        status: str = "completed",
        title: str = "Background task",
        text: str = "",
        run_dir: str = "",
    ) -> dict[str, Any]:
        """Persist a detached task result in the chat that launched it."""
        import uuid as _uuid
        from datetime import datetime

        target = next(
            (
                (idx, conv)
                for idx, conv in enumerate(self._all_conversations)
                if str(conv.get("id") or "") == str(conversation_id or "")
            ),
            None,
        )
        if target is None:
            return {"appended": False, "reason": "conversation_not_found"}
        idx, conv = target
        for message in conv.get("messages") or []:
            metadata = message.get("background_task") if isinstance(message, dict) else None
            if isinstance(metadata, dict) and str(metadata.get("job_id") or "") == str(job_id or ""):
                return {"appended": False, "duplicate": True}

        failed = str(status or "").lower() != "completed"
        heading = t("Background task failed") if failed else t("Background task finished")
        body = str(text or "").strip() or t("The background task finished without a report.")
        content = f"**{heading}: {title}**\n\n{body}"
        if run_dir:
            content += f"\n\n{t('Run details')}: `{run_dir}`"
        now = datetime.now(UTC).isoformat()
        conv.setdefault("messages", []).append(
            {
                "id": str(_uuid.uuid4()),
                "role": "assistant",
                "content": content,
                "created_at": now,
                "background_task": {
                    "job_id": str(job_id or ""),
                    "status": str(status or ""),
                    "run_dir": str(run_dir or ""),
                },
            }
        )
        conv["updated_at"] = now
        self._persist_conversations()
        if self._chat_is_visible() and self._active_conversation_idx == idx:
            self._chat.sync_conversation(idx)
        return {"appended": True, "conversation_index": idx}

    def _live_file_approval_request(self, **params: Any) -> dict[str, Any]:
        """Ask the user to approve a live model file write/edit."""
        if str(params.get("kind") or "") in {"user_input", "mcp_elicitation"}:
            return self._harness_interaction_request(**params)
        action, path, text = _live_file_approval_summary(params)
        state: dict[str, Any] = {"done": False, "approved": False, "feedback": "", "surface": ""}
        loop = None
        chat_resolver = {"fn": None}
        chat_panel = {"shown": False}

        def finish(surface: str, value: bool, feedback: str = "") -> None:
            if state["done"]:
                return
            state["done"] = True
            state["approved"] = bool(value)
            state["feedback"] = str(feedback or "").strip()
            state["surface"] = surface
            if surface != "chat" and callable(chat_resolver["fn"]):
                try:
                    chat_resolver["fn"](bool(value), str(feedback or ""))
                except Exception:
                    traceback.print_exc()
            if surface == "chat":
                try:
                    bubble = self._ensure_bubble()
                    thinking = getattr(bubble, "start_thinking", None)
                    if callable(thinking):
                        thinking()
                except Exception:
                    pass
            if loop is not None:
                loop.quit()

        def show_chat_panel() -> bool:
            try:
                self._show_chat(force_new=False)
            except Exception:
                traceback.print_exc()
            if chat_panel["shown"]:
                return True
            chat = self._chat
            if chat is None or not chat.isVisible():
                return False
            request = getattr(chat, "request_live_file_approval", None)
            if not callable(request):
                return False
            request_params = dict(params)
            request_params["_on_decision"] = lambda result: finish(
                "chat",
                bool((result or {}).get("approved")) if isinstance(result, dict) else False,
                str((result or {}).get("feedback") or "") if isinstance(result, dict) else "",
            )
            request_params["_register_resolver"] = lambda resolver: chat_resolver.update({"fn": resolver})
            result = request(request_params)
            if isinstance(result, dict) and result.get("shown"):
                chat_panel["shown"] = True
                if "approved" in result and result.get("approved"):
                    finish("chat", bool(result.get("approved")), str(result.get("feedback") or ""))
                return True
            return False

        chat_shown = show_chat_panel()
        bubble_result = self._live_file_approval_bubble(
            dict(params),
            text,
            on_decision=lambda value, feedback="": finish("bubble", bool(value), str(feedback or "")),
            on_feedback=show_chat_panel,
        )
        bubble_shown = bool(bubble_result.get("shown"))
        if chat_shown or bubble_shown:
            if not state["done"]:
                from PySide6.QtCore import QEventLoop

                loop = QEventLoop()
                loop.exec()
            return {
                "approved": bool(state["approved"]),
                "feedback": str(state.get("feedback") or "").strip(),
                "surface": str(state.get("surface") or ("chat" if chat_shown else "bubble")),
            }

        if bubble_result.get("shown"):
            return {
                "approved": bool(bubble_result.get("approved")),
                "feedback": str(bubble_result.get("feedback") or "").strip(),
                "surface": "bubble",
            }

        from PySide6.QtWidgets import QMessageBox

        details = params.get("details") if isinstance(params.get("details"), dict) else {}
        diff = str(params.get("diff") or details.get("diff") or "").strip()

        box = QMessageBox(self._chat or self._overlay)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t("Approve this file change?"))
        box.setText(text)
        box.setInformativeText(t("Approve this file change?"))
        if diff:
            box.setDetailedText(diff[:20000])
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        approved = box.exec() == QMessageBox.StandardButton.Yes
        return {"approved": bool(approved), "feedback": "", "surface": "modal"}

    def _harness_interaction_request(
        self,
        kind: str = "",
        questions: list[dict[str, Any]] | None = None,
        server_name: str = "",
        mode: str = "form",
        message: str = "",
        url: str = "",
        requested_schema: dict[str, Any] | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Collect Codex request_user_input or MCP elicitation responses."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLabel,
            QLineEdit,
            QPushButton,
            QVBoxLayout,
        )

        parent = self._chat or self._overlay
        dialog = QDialog(parent)
        dialog.setModal(True)
        dialog.setMinimumWidth(520)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        fields: list[tuple[str, Any]] = []

        if kind == "user_input":
            dialog.setWindowTitle(t("ChatGPT needs your input"))
            for index, raw in enumerate(questions or []):
                question = raw if isinstance(raw, dict) else {}
                prompt = QLabel(str(question.get("question") or question.get("header") or "Question"), dialog)
                prompt.setWordWrap(True)
                root.addWidget(prompt)
                options = [item for item in question.get("options") or [] if isinstance(item, dict)]
                if options:
                    editor = QComboBox(dialog)
                    for option in options:
                        editor.addItem(str(option.get("label") or ""), str(option.get("label") or ""))
                    editor.setEditable(bool(question.get("isOther")))
                else:
                    editor = QLineEdit(dialog)
                    if bool(question.get("isSecret")):
                        editor.setEchoMode(QLineEdit.EchoMode.Password)
                editor.setObjectName(f"harnessQuestion_{question.get('id') or index}")
                root.addWidget(editor)
                fields.append((str(question.get("id") or index), editor))
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
                parent=dialog,
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            root.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return {"approved": False, "answers": {}, "surface": "harness_input"}
            answers: dict[str, dict[str, list[str]]] = {}
            for question_id, editor in fields:
                value = editor.currentText() if isinstance(editor, QComboBox) else editor.text()
                answers[question_id] = {"answers": [str(value)]}
            return {"approved": True, "answers": answers, "surface": "harness_input"}

        title = t("MCP server request")
        if server_name:
            title = t("{server} needs your input").format(server=server_name)
        dialog.setWindowTitle(title)
        if message:
            description = QLabel(message, dialog)
            description.setWordWrap(True)
            root.addWidget(description)
        if str(mode or "").lower() == "url":
            url_label = QLabel(f"<a href='{html.escape(url)}'>{html.escape(url)}</a>", dialog)
            url_label.setOpenExternalLinks(True)
            url_label.setWordWrap(True)
            root.addWidget(url_label)
            open_button = QPushButton(t("Open in browser"), dialog)
            open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
            root.addWidget(open_button)
        else:
            schema = requested_schema if isinstance(requested_schema, dict) else {}
            properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            required = {str(value) for value in schema.get("required") or []}
            form = QFormLayout()
            for name, raw in properties.items():
                spec = raw if isinstance(raw, dict) else {}
                field_type = str(spec.get("type") or "string")
                enum = list(spec.get("enum") or [])
                if enum:
                    editor = QComboBox(dialog)
                    for value in enum:
                        editor.addItem(str(value), value)
                    default = spec.get("default")
                    default_index = editor.findData(default)
                    if default_index >= 0:
                        editor.setCurrentIndex(default_index)
                elif field_type == "boolean":
                    editor = QCheckBox(dialog)
                    editor.setChecked(bool(spec.get("default", False)))
                else:
                    editor = QLineEdit(dialog)
                    if spec.get("default") is not None:
                        editor.setText(str(spec.get("default")))
                    if str(spec.get("format") or "") == "password":
                        editor.setEchoMode(QLineEdit.EchoMode.Password)
                editor.setObjectName(f"mcpElicitation_{name}")
                label = str(spec.get("title") or name) + (" *" if str(name) in required else "")
                form.addRow(label, editor)
                fields.append((str(name), editor))
            root.addLayout(form)
        buttons = QDialogButtonBox(dialog)
        accept = buttons.addButton(t("Continue"), QDialogButtonBox.ButtonRole.AcceptRole)
        decline = buttons.addButton(t("Decline"), QDialogButtonBox.ButtonRole.RejectRole)
        accept.clicked.connect(dialog.accept)
        decline.clicked.connect(dialog.reject)
        root.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return {
                "approved": False,
                "action": "decline",
                "content": None,
                "surface": "mcp_elicitation",
            }
        content: dict[str, Any] = {}
        for name, editor in fields:
            if isinstance(editor, QComboBox):
                content[name] = editor.currentData()
            elif isinstance(editor, QCheckBox):
                content[name] = editor.isChecked()
            else:
                content[name] = editor.text()
        return {
            "approved": True,
            "action": "accept",
            "content": None if str(mode or "").lower() == "url" else content,
            "_meta": None,
            "surface": "mcp_elicitation",
        }

    def _action_preview_request(
        self,
        title: str = "",
        summary: str = "",
        html: str = "",
        warnings: list[str] | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Show an exact HTML/CSS action preview and wait for Apply or Cancel."""
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import (
            QApplication,
            QDialog,
            QDialogButtonBox,
            QLabel,
            QScrollArea,
            QVBoxLayout,
            QWidget,
        )

        from ui.addon_presentations import RichPresentationView
        from ui.shared.theme import is_dark_mode, theme_colors
        from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen

        colors = theme_colors(is_dark_mode())
        light = QColor(colors["bg"]).lightness() > 128
        palette = {
            "bg": colors["bg"],
            "text": colors["text"],
            "muted": colors["text_dim"],
            "line": colors["border"],
            "accent": colors["accent"],
            "warm": "#9b5429" if light else "#f0ae72",
            "warm_soft": "#f4e4d8" if light else "#3c2b25",
            "soft": colors["accent_hint"],
            "code": "#f1f1ef" if light else "#15171c",
            "code_text": "#242424" if light else "#eff6ff",
        }

        dialog = QDialog(self._overlay)
        dialog.setWindowTitle(translate_action_text(title or "Review action"))
        dialog.setModal(True)
        dialog.setMinimumSize(760, 580)
        apply_text = "#ffffff" if light else "#15151a"
        dialog.setStyleSheet(
            f"QDialog {{ background: {colors['bg']}; color: {colors['text']}; }}"
            f"QScrollArea {{ background: transparent; border: none; }}"
            f"QDialogButtonBox {{ border-top: 1px solid {colors['border']}; padding-top: 13px; }}"
            f"QPushButton {{ min-width: 108px; padding: 9px 18px; border: 1px solid {colors['text']}; "
            f"border-radius: 4px; background: transparent; color: {colors['text']}; }}"
            f"QPushButton#actionApplyButton {{ border-color: {colors['accent']}; "
            f"background: {colors['accent']}; color: {apply_text}; font-weight: 600; }}"
        )
        enable_standard_window_controls(dialog)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        localized_html = localize_action_preview_html(html)
        content_layout.addWidget(RichPresentationView(localized_html, palette, 15, content))
        content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        warning_text = "\n".join(
            translate_action_text(str(item))
            for item in (warnings or [])
            if str(item).strip()
        )
        if warning_text:
            warning = QLabel(warning_text, dialog)
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {colors['text_dim']};")
            root.addWidget(warning)

        buttons = QDialogButtonBox(dialog)
        apply_button = buttons.addButton(t("Apply"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_button = buttons.addButton(t("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        apply_button.setObjectName("actionApplyButton")
        cancel_button.setObjectName("actionCancelButton")
        apply_button.setDefault(True)
        apply_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        root.addWidget(buttons)
        localize_widget_tree(dialog)
        fit_window_to_screen(dialog, preferred_width=820, preferred_height=680)

        show_called_at_unix_ns = 0
        topmost_at_unix_ns = 0

        def present_preview() -> None:
            """Raise the exact review surface instead of silently waiting behind another app."""
            nonlocal show_called_at_unix_ns, topmost_at_unix_ns
            show_called_at_unix_ns = time.time_ns()
            dialog.show()
            if sys.platform == "win32":
                try:
                    import ctypes
                    from ctypes import wintypes

                    user32 = ctypes.WinDLL("user32", use_last_error=True)
                    user32.SetWindowPos.argtypes = [
                        wintypes.HWND,
                        wintypes.HWND,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_int,
                        wintypes.UINT,
                    ]
                    user32.SetWindowPos.restype = wintypes.BOOL
                    user32.SetWindowPos(
                        int(dialog.winId()),
                        -1,  # HWND_TOPMOST
                        0,
                        0,
                        0,
                        0,
                        0x0001 | 0x0002 | 0x0010 | 0x0040,  # NOSIZE|NOMOVE|NOACTIVATE|SHOW
                    )
                    topmost_at_unix_ns = time.time_ns()
                except Exception:
                    log.exception("could not raise action preview topmost")
            dialog.raise_()
            dialog.activateWindow()
            QApplication.alert(dialog, 0)

        QTimer.singleShot(0, present_preview)
        approved = dialog.exec() == QDialog.DialogCode.Accepted
        decided_at_unix_ns = time.time_ns()
        return {
            "approved": bool(approved),
            "surface": "action_preview",
            "summary": str(summary or ""),
            "show_called_at_unix_ns": show_called_at_unix_ns,
            "topmost_at_unix_ns": topmost_at_unix_ns,
            "decided_at_unix_ns": decided_at_unix_ns,
            "decision_wait_ms": round(
                max(0, decided_at_unix_ns - (topmost_at_unix_ns or show_called_at_unix_ns)) / 1_000_000,
                3,
            ) if (topmost_at_unix_ns or show_called_at_unix_ns) else None,
        }

    def _live_file_approval_bubble(
        self,
        request: dict[str, Any],
        text: str,
        *,
        on_decision=None,
        on_feedback=None,
    ) -> dict[str, Any]:
        """Ask for live file approval through the overlay bubble actions."""
        state = {"done": False, "approved": False, "feedback": ""}
        loop = None

        def finish(value: bool, feedback: str = "") -> None:
            if state["done"]:
                return
            state["done"] = True
            state["approved"] = bool(value)
            state["feedback"] = str(feedback or "").strip()
            if callable(on_decision):
                on_decision(bool(value), str(feedback or ""))
            if loop is not None:
                loop.quit()

        def request_changes() -> None:
            if callable(on_feedback):
                on_feedback()
                return
            try:
                self._show_chat(force_new=False)
                chat = self._chat
                request_fn = getattr(chat, "request_live_file_approval", None) if chat is not None else None
                if callable(request_fn):
                    result = request_fn(dict(request))
                    if isinstance(result, dict) and result.get("shown"):
                        finish(bool(result.get("approved")), str(result.get("feedback") or ""))
                        return
            except Exception:
                traceback.print_exc()
            finish(False)

        overlay = self._ensure_overlay()
        notify = getattr(overlay, "notify_agent_approval", None)
        if not callable(notify):
            return {"shown": False, "approved": False}
        result = notify(
            text,
            resolved=False,
            on_approve=lambda: finish(True),
            on_feedback=request_changes,
            on_decline=lambda: finish(False),
        )
        if not isinstance(result, dict) or not result.get("actionable"):
            return {"shown": False, "approved": False}
        if not state["done"] and not callable(on_decision):
            from PySide6.QtCore import QEventLoop

            loop = QEventLoop()
            loop.exec()
        return {
            "shown": True,
            "approved": bool(state["approved"]),
            "feedback": str(state.get("feedback") or "").strip(),
        }

    def _chat_add_conversation(
        self,
        user: str = "",
        assistant: str = "",
        context: str = "",
        image_base64: str | None = None,
        attachments: list | None = None,
        file_context: list | None = None,
        tool_context: dict | None = None,
        context_policy: dict | None = None,
        user_annotations: list | None = None,
        assistant_annotations: list | None = None,
        display_segments: list | None = None,
        assistant_attachments: list | None = None,
        harness: dict | None = None,
        append_user: bool = True,
        conversation_index: int | None = None,
    ) -> dict[str, Any]:
        """Handle chat add conversation for qt protocol host."""
        import uuid as _uuid
        from datetime import datetime

        from core.conversation_store import store as conversation_store

        now = datetime.now(UTC).isoformat()
        idx = conversation_index if conversation_index is not None else self._active_conversation_idx
        conversation_scope = self._intent_conversation_scope()
        if (
            idx is not None
            and 0 <= idx < len(self._all_conversations)
            and not self._conversation_matches_intent_scope(self._all_conversations[idx])
            and not bool((harness or {}).get("clear_session"))
        ):
            # A route switch (native OpenWand -> Codex/Claude or back) must never
            # append into the other route's conversation.
            idx = None
        if idx is not None and 0 <= idx < len(self._all_conversations):
            conv_id = str(self._all_conversations[idx].get("id") or _uuid.uuid4())
            self._all_conversations[idx]["id"] = conv_id
        else:
            conv_id = str(_uuid.uuid4())
        user_msg: dict[str, Any] = {
            "id": str(_uuid.uuid4()),
            "role": "user",
            "content": user,
            "created_at": now,
        }
        normalized_attachments = conversation_store.normalize_attachments(attachments or [])
        if image_base64:
            try:
                normalized_attachments.append(
                    conversation_store.save_image_attachment(
                        image_base64,
                        conversation_id=conv_id,
                        message_id=str(user_msg["id"]),
                        source="screenshot",
                        name="screenshot.png",
                    )
                )
            except Exception:
                log.exception("failed to persist chat image attachment")
        normalized_attachments = conversation_store.normalize_attachments(normalized_attachments)
        if normalized_attachments:
            user_msg["attachments"] = normalized_attachments
        context_text = str(context or "").strip()
        if context_text:
            user_msg["context"] = context_text
        if user_annotations:
            user_msg["annotations"] = list(user_annotations)
        assistant_msg = {
            "id": str(_uuid.uuid4()),
            "role": "assistant",
            "content": assistant,
            "created_at": now,
        }
        normalized_assistant_attachments = self._persist_assistant_attachments(
            assistant_attachments or [],
            conversation_id=conv_id,
            message_id=str(assistant_msg["id"]),
        )
        if normalized_assistant_attachments:
            assistant_msg["attachments"] = normalized_assistant_attachments
        if assistant_annotations:
            assistant_msg["annotations"] = list(assistant_annotations)
        normalized_display_segments = self._normalized_display_segments(display_segments or [])
        if normalized_display_segments:
            assistant_msg["display_segments"] = normalized_display_segments
            assistant_msg["display_content"] = self._display_content_from_segments(normalized_display_segments)
        normalized_file_context = self._normalized_file_context(file_context or [])
        normalized_tool_context = self._normalized_tool_context(tool_context or {})
        harness_info = dict(harness or {})
        harness_provider = str(harness_info.get("provider") or "").strip().lower()
        harness_session_id = str(harness_info.get("session_id") or "").strip()
        clear_harness_session = (
            harness_provider in {"codex", "claude"}
            and bool(harness_info.get("clear_session"))
        )
        normalized_harness = {
            "provider": harness_provider,
            "session_id": harness_session_id,
            "cwd": str(harness_info.get("cwd") or ""),
            "updated_at": now,
        } if harness_provider in {"codex", "claude"} and harness_session_id else {}
        if normalized_file_context:
            assistant_msg["file_context"] = normalized_file_context
        if normalized_tool_context:
            assistant_msg["tool_context"] = normalized_tool_context

        if idx is not None and 0 <= idx < len(self._all_conversations):
            # Continue the active conversation (the one selected in the chat window).
            conv = self._all_conversations[idx]
            conv["conversation_scope"] = conversation_scope
            conv.setdefault("created_at", now)
            conv["updated_at"] = now
            if context_text:
                current_context = str(conv.get("context") or "").strip()
                conv["context"] = f"{current_context}\n\n---\n{context_text}" if current_context else context_text
            messages = conv.setdefault("messages", [])
            if append_user:
                messages.append(user_msg)
            if assistant or normalized_assistant_attachments:
                messages.append(assistant_msg)
            self._merge_file_context(conv, normalized_file_context)
            self._merge_tool_context(conv, normalized_tool_context)
            normalized_policy = self._normalized_context_policy(context_policy or {})
            if normalized_policy:
                conv["context_policy"] = normalized_policy
            if clear_harness_session:
                sessions = conv.get("harness_sessions")
                if isinstance(sessions, dict):
                    sessions.pop(harness_provider, None)
                    if not sessions:
                        conv.pop("harness_sessions", None)
            elif normalized_harness:
                conv.setdefault("harness_sessions", {})[harness_provider] = normalized_harness
            self._persist_conversations()
            self._finish_external_reply_stream(idx)
            if self._chat_is_visible():
                self._chat.sync_conversation(idx)
            return {"count": len(self._all_conversations), "continued": True}

        # No active conversation (fresh start) -> open a new one and make it active.
        self._all_conversations.append(
            {
                "id": conv_id,
                "project_id": self._active_project_id,
                "conversation_scope": conversation_scope,
                "messages": [
                    msg
                    for msg in (
                        user_msg if append_user else None,
                        assistant_msg if assistant or normalized_assistant_attachments else None,
                    )
                    if msg
                ],
                "context": context_text,
                "created_at": now,
                "updated_at": now,
                "file_context": normalized_file_context,
                "tool_context": normalized_tool_context,
                "context_policy": self._normalized_context_policy(context_policy or {}),
                "harness_sessions": ({harness_provider: normalized_harness} if normalized_harness else {}),
            }
        )
        self._active_conversation_idx = len(self._all_conversations) - 1
        self._persist_conversations()
        self._finish_external_reply_stream(self._active_conversation_idx)
        if self._chat_is_visible():
            self._chat.ingest_new_conversations(select_new=True)
        return {"count": len(self._all_conversations), "continued": False}

    def _chat_begin_conversation(
        self,
        user: str = "",
        context: str = "",
        image_base64: str | None = None,
        attachments: list | None = None,
        context_policy: dict | None = None,
        user_annotations: list | None = None,
    ) -> dict[str, Any]:
        """Persist a user chat turn as soon as a prompt is submitted."""
        result = self._chat_add_conversation(
            user=user,
            assistant="",
            context=context,
            image_base64=image_base64,
            attachments=attachments,
            context_policy=context_policy,
            user_annotations=user_annotations,
            append_user=True,
        )
        idx = self._active_conversation_idx
        if idx is not None:
            self._external_reply_stream = {
                "conversation_index": idx,
                "chunks": [],
                "attached_chat_id": None,
                "done": False,
            }
            if self._chat is not None:
                if not self._chat_is_visible():
                    sync = getattr(self._chat, "sync_conversation", None)
                    if callable(sync):
                        sync(idx)
                self._restore_external_reply_stream()
        return {
            "started": True,
            "conversation_index": idx,
            "conversation_id": (
                str(self._all_conversations[idx].get("id") or "")
                if idx is not None and 0 <= idx < len(self._all_conversations)
                else ""
            ),
            "continued": bool(result.get("continued")),
            "count": result.get("count"),
        }

    def _restore_external_reply_stream(self) -> bool:
        """Attach a buffered overlay reply to Chat, returning True on new attach."""
        state = getattr(self, "_external_reply_stream", None)
        chat = self._chat
        if not isinstance(state, dict) or chat is None:
            return False
        idx = state.get("conversation_index")
        if not isinstance(idx, int) or not (0 <= idx < len(self._all_conversations)):
            return False
        chat_id = id(chat)
        if state.get("attached_chat_id") == chat_id:
            return False
        chat.begin_external_reply_stream(idx)
        for chunk in list(state.get("chunks") or []):
            chat.external_reply_chunk(idx, chunk)
        state["attached_chat_id"] = chat_id
        # If completion raced with opening Chat, keep the reconstructed final
        # bubble visible until the immediately following durable sync replaces
        # it.  Calling finish here would briefly make the answer disappear.
        if state.get("done") and state.get("final_text"):
            chat._on_final_text(str(state.get("final_text") or ""))
        return True

    def _finish_external_reply_stream(self, conversation_index: int | None) -> None:
        """Remove a temporary stream once its durable assistant turn is stored."""
        state = getattr(self, "_external_reply_stream", None)
        if not isinstance(state, dict) or state.get("conversation_index") != conversation_index:
            return
        if self._chat is not None and state.get("attached_chat_id") == id(self._chat):
            self._chat.finish_external_reply_stream(int(conversation_index), "")
        self._external_reply_stream = None

    @staticmethod
    def _normalized_display_segments(items: object) -> list[dict[str, Any]]:
        """Return JSON-safe chronological thought/reply segments for local history."""
        if not isinstance(items, list):
            return []
        normalized: list[dict[str, Any]] = []
        for raw in items:
            if isinstance(raw, dict):
                text = str(raw.get("text") or "")
                is_thought = bool(raw.get("is_thought"))
            elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
                text = str(raw[0] or "")
                is_thought = bool(raw[1])
            else:
                continue
            if not text:
                continue
            if normalized and bool(normalized[-1]["is_thought"]) == is_thought:
                normalized[-1]["text"] = str(normalized[-1]["text"]) + text
            else:
                normalized.append({"text": text, "is_thought": is_thought})
        return normalized

    @staticmethod
    def _persist_assistant_attachments(
        items: object,
        *,
        conversation_id: str,
        message_id: str,
    ) -> list[dict[str, Any]]:
        """Turn generated-image payloads into durable chat attachment refs."""
        from core.conversation_store import store as conversation_store

        if not isinstance(items, list):
            return []
        refs: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            data_url = str(raw.get("data_url") or "").strip()
            path = str(raw.get("path") or "").strip()
            name = str(raw.get("name") or Path(path).name or "generated-image.png")
            source = str(raw.get("source") or "generated_image")
            try:
                if data_url:
                    ref = conversation_store.save_image_attachment(
                        data_url,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        source=source,
                        name=name,
                    )
                elif path:
                    ref = conversation_store.external_file_attachment(
                        path,
                        kind="image",
                        source=source,
                    )
                    ref["name"] = name
                else:
                    continue
            except Exception:
                log.exception("failed to persist generated image attachment")
                continue
            refs.append(ref)
        return conversation_store.normalize_attachments(refs)

    @staticmethod
    def _display_content_from_segments(items: list[dict[str, Any]]) -> str:
        """Serialize structured activity for the existing tagged history renderer."""
        return "".join(
            f"<thought>{item['text']}</thought>" if item.get("is_thought") else str(item.get("text") or "")
            for item in items
        )

    @staticmethod
    def _normalized_file_context(items: list) -> list[dict[str, Any]]:
        """Return compact persisted local-file metadata."""
        out: list[dict[str, Any]] = []
        for raw in items or []:
            if not isinstance(raw, dict):
                continue
            item = {
                "tool": str(raw.get("tool") or ""),
                "path": str(raw.get("path") or ""),
                "relative_path": str(raw.get("relative_path") or ""),
                "root": str(raw.get("root") or ""),
                "ok": bool(raw.get("ok")),
                "message": str(raw.get("message") or ""),
            }
            if item["tool"] and item["path"] and item not in out:
                out.append(item)
        return out[-20:]

    def _merge_file_context(self, conv: dict, items: list) -> None:
        """Merge local-file metadata into a conversation."""
        merged = self._normalized_file_context(list(conv.get("file_context") or []) + list(items or []))
        if merged:
            conv["file_context"] = merged

    @staticmethod
    def _normalized_tool_context(raw: dict) -> dict[str, Any]:
        """Return compact persisted tool policy metadata."""
        if not isinstance(raw, dict):
            return {}

        def _str_list(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            out: list[str] = []
            for item in value:
                text = str(item or "").strip()
                if text and text not in out:
                    out.append(text)
            return out

        mode = str(raw.get("file_access_mode") or "").strip().lower()
        if mode not in {"off", "read", "ask", "auto"}:
            mode = ""
        ctx = {
            "allowed_tools": _str_list(raw.get("allowed_tools")),
            "pinned_tools": _str_list(raw.get("pinned_tools")),
            "file_access_mode": mode,
        }
        if not ctx["allowed_tools"] and not ctx["pinned_tools"] and not ctx["file_access_mode"]:
            return {}
        return ctx

    def _merge_tool_context(self, conv: dict, raw: dict) -> None:
        """Persist the latest tool policy for a conversation."""
        ctx = self._normalized_tool_context(raw)
        if ctx:
            conv["tool_context"] = ctx

    @staticmethod
    def _normalized_context_policy(raw: dict) -> dict[str, Any]:
        """Return compact persisted chat context/tool policy metadata."""
        if not isinstance(raw, dict):
            return {}
        from core.system.env_utils import normalize_file_access_mode
        from runtime.supervisor import tool_modes

        def _mode(value: Any, default: str = "off") -> str:
            mode = str(value or default or "off").strip().lower()
            if mode == "on":
                return "auto"
            return mode if mode in {"off", "auto", "model"} else default

        tools = raw.get("tools")
        policy = {
            "context_ambient": bool(raw.get("context_ambient", False)),
            "context_documents": tool_modes.context_mode(raw, "documents") == "auto",
            "context_tools": False,
            "context_documents_mode": tool_modes.context_mode(raw, "documents"),
            "context_browser_mode": tool_modes.context_mode(raw, "browser"),
            "context_github_mode": tool_modes.context_mode(raw, "github"),
            "context_memory_mode": tool_modes.context_mode(raw, "memory"),
            "context_screenshot": _mode(raw.get("context_screenshot"), "off"),
            "context_clipboard": bool(raw.get("context_clipboard", False)),
            "_context_selection_enabled": bool(raw.get("_context_selection_enabled", False)),
            "file_access": normalize_file_access_mode(raw.get("file_access", "off")),
            "tools": dict(tools) if isinstance(tools, dict) else {},
        }
        policy["context_tools"] = any(
            policy[key] == "model"
            for key in (
                "context_documents_mode",
                "context_browser_mode",
                "context_github_mode",
                "context_memory_mode",
            )
        )
        return policy

    def _chat_ingest(self) -> dict[str, Any]:
        """Handle chat ingest for qt protocol host."""
        if self._chat_is_visible():
            self._chat.ingest_new_conversations()
            return {"ingested": True}
        return {"ingested": False}

    def _chat_is_visible(self) -> bool:
        """Return whether the chat surface is currently user-visible."""
        chat = self._chat
        if chat is None:
            return False
        try:
            return bool(chat.isVisible())
        except Exception:
            return False

    def _chat_auto_elaborate_prompt(self, *, force_new: bool = False) -> str:
        """Return and reserve the configured one-shot prompt for a short latest answer."""
        import config

        if force_new or not bool(getattr(config, "CHAT_AUTO_ELABORATE", False)):
            return ""
        if not self._all_conversations:
            return ""
        idx = self._active_conversation_idx
        if not isinstance(idx, int) or not (0 <= idx < len(self._all_conversations)):
            idx = len(self._all_conversations) - 1
        conversation = self._all_conversations[idx]
        messages = conversation.get("messages") or []
        if not messages or not isinstance(messages[-1], dict):
            return ""
        reserved_count = conversation.get("auto_elaborate_turn_count")
        if (
            isinstance(reserved_count, int)
            and reserved_count < len(messages) <= reserved_count + 2
        ):
            return ""
        latest = messages[-1]
        answer = str(latest.get("content") or "").strip()
        if str(latest.get("role") or "").lower() != "assistant":
            return ""
        if not answer or len(answer) > _CHAT_AUTO_ELABORATE_MAX_CHARS:
            return ""
        marker_source = "|".join(
            (
                str(latest.get("id") or ""),
                str(latest.get("created_at") or ""),
                answer,
            )
        )
        marker = hashlib.sha256(marker_source.encode("utf-8")).hexdigest()
        if conversation.get("auto_elaborated_message") == marker:
            return ""
        prompt = str(getattr(config, "CHAT_ELABORATE_PROMPT", "") or "").strip()
        if not prompt:
            return ""
        conversation["auto_elaborated_message"] = marker
        conversation["auto_elaborate_turn_count"] = len(messages)
        try:
            self._persist_conversations()
        except Exception as exc:  # opening Chat must survive a failed marker save
            log.warning("Could not persist auto-elaborate marker: %s", exc)
        return prompt

    def _show_chat(self, force_new: bool = False) -> dict[str, Any]:
        """Show chat."""
        from ui.chat_window import ChatWindow

        if self._chat is None and not getattr(self, "_chat_message_actions_ready", True):
            pending = getattr(self, "_pending_chat_show_new", None)
            self._pending_chat_show_new = bool(force_new or pending)
            self.emit("ui.chat.message_actions.requested", {})
            return {"shown": False, "pending_actions": True}
        if self._chat is not None:
            if force_new:
                self._chat.start_new_conversation()
            else:
                state = getattr(self, "_external_reply_stream", None)
                if not isinstance(state, dict):
                    idx = self._active_conversation_idx
                    if isinstance(idx, int) and 0 <= idx < len(self._all_conversations):
                        self._chat.sync_conversation(idx)
                auto_message = self._chat_auto_elaborate_prompt(force_new=False)
                if auto_message:
                    from PySide6.QtCore import QTimer

                    QTimer.singleShot(0, lambda: self._chat and self._chat._send(auto_message))
            self._chat.show()
            self._chat.raise_()
            self._chat.activateWindow()
            self._restore_external_reply_stream()
            self._chat.request_context_preview()
            self.emit("ui.chat.message_actions.requested", {})
            return {"shown": True, "reused": True}
        start_new = force_new or not self._all_conversations
        from core.conversation_store import store as conversation_store
        # First chat-window construction is a heavy, deliberately synchronous
        # main-thread build (cold imports + widgets + conversation/project load).
        # Warn the watchdog so a cold-start stall isn't logged as a real freeze.
        watchdog = getattr(self, "_watchdog", None)
        if watchdog is not None:
            watchdog.expect_slow(10.0)
        try:
            auto_message = self._chat_auto_elaborate_prompt(force_new=force_new)
            self._chat = ChatWindow(
                conversations=self._all_conversations,
                send_fn=self._make_chat_send_fn(),
                start_new=start_new,
                projects=conversation_store.load_projects(),
                active_project_id=self._active_project_id,
                on_project_change=self._set_active_project,
                on_new_project=self._create_project,
                persist_fn=self._persist_conversations,
                active_idx=self._active_conversation_idx,
                on_select=self._set_active_conversation,
                on_context_preview=lambda payload: self.emit("ui.chat.context_preview", payload),
                on_context_capture=self._chat_context_capture_requested,
                on_addon_message_action=lambda payload: self.emit(
                    "ui.chat.message_action.requested",
                    payload,
                ),
                on_addon_settings=lambda addon_id: self.emit(
                    "ui.addons.open_requested",
                    {"addon_id": addon_id},
                ),
                on_addon_setting_change=lambda payload: self.emit(
                    "ui.addons.set_setting",
                    dict(payload or {}),
                ),
                on_model_settings=lambda: self.emit(
                    "ui.settings.open_requested",
                    {"initial_page": "LLM"},
                ),
                addon_message_actions=list(
                    getattr(self, "_chat_message_actions_cache", []) or []
                ),
                auto_message=auto_message or None,
            )
            self._chat.destroyed.connect(lambda: setattr(self, "_chat", None))
            self._chat.show()
            self._chat.raise_()
            self._chat.activateWindow()
            self._restore_external_reply_stream()
            self.emit("ui.chat.message_actions.requested", {})
        finally:
            if watchdog is not None:
                watchdog.beat()
        return {"shown": True, "reused": False}

    def _chat_context_capture_requested(self, payload: dict[str, Any]) -> None:
        """Route chat context-chip interactive capture requests."""
        source = str((payload or {}).get("source") or "")
        if source == "screenshot":
            self.emit("ui.chat.snip.requested", dict(payload or {}))
            if self._chat is not None:
                self._chat.hide()
            self._show_snip(event_prefix="ui.chat.snip", app_region=self._snip_app_region())
            return
        if source == "selection":
            self.emit("ui.chat.selection.requested", dict(payload or {}))
            if self._chat is not None:
                self._chat.hide()

    def _chat_context_preview(
        self,
        preview_id: str = "",
        context_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Refresh chat context chip token estimates."""
        if not self._chat_is_visible():
            return {"updated": False, "reason": "no_chat"}
        self._chat.update_context_preview(str(preview_id or ""), context_items or [])
        return {"updated": True}

    def _chat_capture_context(
        self,
        name: str = "",
        content: str = "",
        item_type: str = "text",
        source: str = "",
        paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Attach interactively captured context to the visible chat composer."""
        self._show_chat(force_new=False)
        if not self._chat_is_visible():
            return {"attached": False, "reason": "no_chat"}
        result = self._chat.attach_captured_context(
            name=name,
            content=content,
            item_type=item_type,
            source=source,
            paths=paths or [],
        )
        self._chat.show()
        self._chat.raise_()
        self._chat.activateWindow()
        return result if isinstance(result, dict) else {"attached": bool(result)}

    def _chat_capture_cancelled(self, source: str = "") -> dict[str, Any]:
        """Reset a chat context chip after an interactive capture was cancelled."""
        if self._chat is not None and not self._chat_is_visible():
            self._show_chat(force_new=False)
        if not self._chat_is_visible():
            return {"cancelled": False, "reason": "no_chat"}
        result = self._chat.cancel_context_capture(source=source)
        self._chat.show()
        self._chat.raise_()
        self._chat.activateWindow()
        return result if isinstance(result, dict) else {"cancelled": bool(result)}

    def _chat_message_actions(self, actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Refresh addon actions shown beneath stored chat messages."""
        normalized = [dict(item) for item in (actions or []) if isinstance(item, dict)]
        self._chat_message_actions_cache = normalized
        self._chat_message_actions_ready = True
        pending_show = getattr(self, "_pending_chat_show_new", None)
        self._pending_chat_show_new = None
        if self._chat is None and pending_show is not None:
            result = self._show_chat(force_new=bool(pending_show))
            return {
                "updated": True,
                "count": len(normalized),
                "chat": result,
            }
        if self._chat is None:
            return {"updated": False, "reason": "no_chat"}
        self._chat.update_addon_message_actions(normalized)
        return {"updated": True, "count": len(normalized)}

    def _chat_message_action_result(self, **payload: Any) -> dict[str, Any]:
        """Apply an addon presentation result to its canonical message."""
        if self._chat is None:
            return {"updated": False, "reason": "no_chat"}
        return self._chat.apply_addon_message_action_result(**payload)

    def _show_settings(
        self,
        extra_tools: list[dict[str, Any]] | None = None,
        initial_page: str | None = None,
    ) -> dict[str, Any]:
        """Show settings."""
        from PySide6.QtCore import QTimer

        from ui.settings_panel.dialog import open_settings

        def _open() -> None:
            """Open the Settings dialog on the Qt thread."""
            watchdog = getattr(self, "_watchdog", None)
            if watchdog is not None:
                watchdog.expect_slow(10.0)
            try:
                open_settings(
                    parent=None,
                    on_apply=self._settings_applied,
                    on_setup_check=lambda: self.emit("ui.health.requested", {"source": "settings"}),
                    extra_tools=extra_tools or [],
                    initial_page=initial_page,
                )
            except Exception:
                traceback.print_exc()
            finally:
                if watchdog is not None:
                    watchdog.beat()

        QTimer.singleShot(0, _open)
        return {"queued": True}

    def _settings_is_open(self) -> dict[str, bool]:
        """Return whether the Settings dialog is currently visible."""
        try:
            from ui.settings_panel import dialog as settings_dialog

            dlg = getattr(settings_dialog, "_settings_dialog", None)
            return {"open": bool(dlg is not None and dlg.isVisible())}
        except Exception:
            return {"open": False}

    def _debug_close_aux_windows(self) -> dict[str, Any]:
        """Close acceptance-test auxiliary windows without touching the overlay shell."""
        from PySide6.QtWidgets import QApplication

        candidates: list[tuple[str, Any]] = [
            ("chat", self._chat),
            ("memory", self._memory_viewer),
            ("addons", self._addons_dialog),
            ("virtual_workspace", getattr(self, "_virtual_workspace_window", None)),
            ("runtime_status", self._runtime_status_dialog),
        ]
        try:
            from ui.settings_panel import dialog as settings_dialog

            candidates.append(("settings", getattr(settings_dialog, "_settings_dialog", None)))
        except Exception:
            pass

        overlay = self._overlay
        if overlay is not None:
            candidates.append(
                ("provider_controls", getattr(overlay, "_harness_controls_dialog", None))
            )

        # A deferred provider-controls open can replace the overlay's reference
        # before an older dialog is deleted. Close every surviving instance, but
        # never use a broad top-level-window sweep: the overlay, provider badge,
        # bubble, context panel, and tray must remain available to the test.
        candidates.extend(
            ("provider_controls", widget)
            for widget in QApplication.allWidgets()
            if type(widget).__name__ == "HarnessControlsDialog"
        )

        closed: list[str] = []
        seen: set[int] = set()
        for label, widget in candidates:
            if widget is None or id(widget) in seen:
                continue
            seen.add(id(widget))
            try:
                was_visible = bool(widget.isVisible())
                widget.close()
            except RuntimeError:
                continue
            if was_visible and label not in closed:
                closed.append(label)
        return {"closed": closed}

    def _debug_settings_action(
        self,
        action: str = "snapshot",
        profile_id: str = "",
        provider: str = "",
        model: str = "",
        value: str = "",
    ) -> dict[str, Any]:
        """Drive the real Settings dialog for opt-in process-level acceptance tests."""
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit

        from ui.settings_panel import dialog as settings_dialog

        dialog = getattr(settings_dialog, "_settings_dialog", None)
        if dialog is None or not dialog.isVisible():
            return {"ok": False, "reason": "settings_not_open"}

        def snapshot() -> dict[str, Any]:
            dialog._refresh_dirty_state()
            rows = list(getattr(dialog, "_model_section_rows", {}).get("LLM", []))
            primary = rows[0] if rows else None
            model_choices: list[str] = []
            current_provider = ""
            current_model = ""
            refresh_busy = False
            refresh_tooltip = ""
            if primary is not None:
                provider_combo = primary["api_key_combo"]
                model_combo = primary["model_combo"]
                current_provider = str(provider_combo.currentData() or "")
                current_model = str(dialog._model_value(primary) or "")
                model_choices = [
                    str(model_combo.itemData(index) or "")
                    for index in range(model_combo.count())
                    if str(model_combo.itemData(index) or "")
                    not in {"", settings_dialog._CUSTOM_MODEL_SENTINEL}
                ]
                refresh_busy = not bool(primary["refresh_btn"].isEnabled())
                refresh_tooltip = str(primary["refresh_btn"].toolTip() or "")
            connection_providers = [
                str(row["provider"].currentData() or "")
                for row in getattr(dialog, "_api_key_rows", [])
                if str(row["provider"].currentData() or "")
            ]
            bubble_width = dialog._fields.get("BUBBLE_WIDTH")
            return {
                "ok": True,
                "open": bool(dialog.isVisible()),
                "profile_id": str(dialog._selected_profile_id() or ""),
                "profile_label": str(dialog._profiles_btn.text() or ""),
                "status": str(dialog._status_lbl.text() or ""),
                "status_tooltip": str(dialog._status_lbl.toolTip() or ""),
                "save_enabled": bool(dialog._apply_btn.isEnabled()),
                "provider": current_provider,
                "model": current_model,
                "model_choices": model_choices,
                "model_refresh_busy": refresh_busy,
                "model_refresh_tooltip": refresh_tooltip,
                "connection_providers": connection_providers,
                "bubble_width": (
                    str(bubble_width.text() or "")
                    if isinstance(bubble_width, QLineEdit)
                    else ""
                ),
                "stt_beam_size": str(settings_dialog._get(dialog._fields["STT_BEAM_SIZE"]) or ""),
                "memory_top_k": str(settings_dialog._get(dialog._fields["MEMORY_TOP_K"]) or ""),
                "context_browser_max_chars": str(
                    settings_dialog._get(dialog._fields["CONTEXT_BROWSER_MAX_CHARS"]) or ""
                ),
            }

        requested = str(action or "snapshot").strip().lower()
        if requested == "snapshot":
            return snapshot()
        if requested == "select_profile":
            target = str(profile_id or "").strip()
            if not target:
                return {"ok": False, "reason": "missing_profile_id"}
            expected_label = ""
            if target in settings_dialog._PRESET_LABELS:
                expected_label = settings_dialog.t(settings_dialog._PRESET_LABELS[target])
            else:
                for _slot, saved_id, saved_label in dialog._saved_custom_profile_entries():
                    if saved_id == target:
                        expected_label = dialog._custom_profile_display_label(
                            saved_id,
                            saved_label,
                            dialog._saved_custom_profile_entries(),
                        )
                        break
            menu = dialog._profiles_btn.menu()
            available = [
                item.text()
                for item in (menu.actions() if menu is not None else [])
                if item.text()
            ]
            menu_action = next(
                (
                    item
                    for item in (menu.actions() if menu is not None else [])
                    if expected_label and item.text().casefold() == expected_label.casefold()
                ),
                None,
            )
            if menu_action is None:
                return {
                    "ok": False,
                    "reason": "profile_action_not_found",
                    "profile_id": target,
                    "available": available,
                }
            menu_action.trigger()
            QApplication.processEvents()
            return snapshot()
        if requested == "select_provider":
            rows = list(getattr(dialog, "_model_section_rows", {}).get("LLM", []))
            if not rows:
                return {"ok": False, "reason": "missing_primary_model_row"}
            combo = rows[0]["api_key_combo"]
            if not isinstance(combo, QComboBox):
                return {"ok": False, "reason": "missing_provider_combo"}
            index = combo.findData(str(provider or "").strip())
            if index < 0:
                return {"ok": False, "reason": "provider_not_available", "provider": provider}
            combo.setCurrentIndex(index)
            QApplication.processEvents()
            return snapshot()
        if requested == "select_model":
            rows = list(getattr(dialog, "_model_section_rows", {}).get("LLM", []))
            if not rows:
                return {"ok": False, "reason": "missing_primary_model_row"}
            combo = rows[0]["model_combo"]
            if not isinstance(combo, QComboBox):
                return {"ok": False, "reason": "missing_model_combo"}
            index = combo.findData(str(model or "").strip())
            if index < 0:
                result = snapshot()
                result.update({"ok": False, "reason": "model_not_available", "requested_model": model})
                return result
            combo.setCurrentIndex(index)
            QApplication.processEvents()
            return snapshot()
        if requested == "set_bubble_width":
            field = dialog._fields.get("BUBBLE_WIDTH")
            if not isinstance(field, QLineEdit):
                return {"ok": False, "reason": "missing_bubble_width"}
            field.setFocus()
            field.selectAll()
            QTest.keyClicks(field, str(value or ""))
            QApplication.processEvents()
            return snapshot()
        if requested == "save":
            before_enabled = bool(dialog._apply_btn.isEnabled())
            dialog._apply_btn.click()
            QApplication.processEvents()
            for warning_box in list(getattr(dialog, "_open_warning_boxes", [])):
                warning_box.close()
            result = snapshot()
            result["save_clicked"] = True
            result["save_was_enabled"] = before_enabled
            return result
        if requested == "close":
            dialog.close()
            QApplication.processEvents()
            return {"ok": True, "open": False}
        return {"ok": False, "reason": "unknown_action", "action": requested}

    def _format_status_rows(self, rows: list[dict[str, Any]] | None) -> str:
        """Format health/privacy rows for a compact QMessageBox."""
        lines: list[str] = []
        for row in rows or []:
            status = _status_label(str(row.get("status") or "warn"))
            name = _translate_health_text(str(row.get("name") or "Check"))
            message = _translate_health_text(str(row.get("message") or ""))
            recommendation = _translate_health_text(str(row.get("recommendation") or ""))
            block = f"{status} - {name}\n{message}"
            if recommendation:
                block += f"\n{recommendation}"
            lines.append(block)
        return "\n\n".join(lines) or t("No status details available.")

    def _show_status_dialog(self, title: str, body: str) -> None:
        """Show a non-blocking status dialog and keep it alive until closed."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox()
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        box.setWindowTitle(t(title))
        box.setText(body)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        self._status_dialogs.append(box)

        def _forget(_result: int, b=box) -> None:
            if b in self._status_dialogs:
                self._status_dialogs.remove(b)

        box.finished.connect(_forget)
        box.open()

    def _runtime_status_text(
        self,
        workers: list[dict[str, Any]] | None = None,
        log_dir: str = "",
        events: list[dict[str, Any]] | None = None,
    ) -> str:
        """Format the full runtime status (workers + events) as plain text."""
        lines = [f"OpenWand runtime status - {time.strftime('%Y-%m-%d %H:%M:%S')}"]
        if log_dir:
            lines.append(f"Log directory: {log_dir}")
        lines.append("")
        for worker in workers or []:
            name = str(worker.get("name") or "worker")
            module = str(worker.get("module") or "")
            pid = worker.get("pid")
            alive = "running" if worker.get("alive") else "stopped"
            row = f"[{name}] {alive} pid={pid or '-'}"
            if module:
                row += f" module={module}"
            lines.append(row)
        for event in events or []:
            lines.append("")
            lines.append(_runtime_event_headline(event))
            detail = str(event.get("detail") or "").strip()
            if detail:
                lines.extend(f"    {line}" for line in detail.splitlines())
        return "\n".join(lines).rstrip() or "No runtime status available."

    @staticmethod
    def _runtime_worker_summary(workers: list[dict[str, Any]] | None, log_dir: str) -> str:
        """One line per worker for the Runtime Status header."""
        lines = []
        for worker in workers or []:
            name = str(worker.get("name") or "worker")
            pid = worker.get("pid")
            alive = t("running") if worker.get("alive") else t("stopped")
            lines.append(f"[{name}] {alive}  pid={pid or '-'}")
        if log_dir:
            lines.append(f"{t('Log directory')}: {log_dir}")
        return "\n".join(lines)

    def _runtime_status_add_event(self, tree: Any, event: dict[str, Any]) -> None:
        """Append one aggregated runtime event to the diagnostics tree."""
        from PySide6.QtGui import QBrush, QColor
        from PySide6.QtWidgets import QTreeWidgetItem

        from ui.shared.theme import is_dark_mode, theme_colors

        colors = theme_colors(is_dark_mode())
        severity = str(event.get("severity") or "info")
        item = QTreeWidgetItem([_runtime_event_headline(event)])
        if severity == "error":
            item.setForeground(0, QBrush(QColor("#f2555a" if is_dark_mode() else "#c62828")))
        elif severity == "warning":
            item.setForeground(0, QBrush(QColor("#e5a50a" if is_dark_mode() else "#9a6700")))
        detail = str(event.get("detail") or "").strip()
        if detail:
            dim = QBrush(QColor(colors["text_dim"]))
            for line in detail.splitlines()[:200]:
                child = QTreeWidgetItem([line])
                child.setForeground(0, dim)
                item.addChild(child)
        tree.addTopLevelItem(item)
        # Detail lines stay collapsed until the user expands the headline row.
        item.setExpanded(False)
        while tree.topLevelItemCount() > 600:
            tree.takeTopLevelItem(0)

    @staticmethod
    def _runtime_status_selected_text(tree: Any) -> str:
        """Return selected runtime rows in display order, including collapsed details."""
        selected = {id(item) for item in tree.selectedItems()}
        lines: list[str] = []
        for top_index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(top_index)
            if id(item) in selected:
                lines.append(item.text(0))
                lines.extend(f"    {item.child(index).text(0)}" for index in range(item.childCount()))
                continue
            lines.extend(
                f"    {item.child(index).text(0)}"
                for index in range(item.childCount())
                if id(item.child(index)) in selected
            )
        return "\n".join(lines)

    def _runtime_status_copy_selected(self, tree: Any) -> None:
        """Copy selected runtime rows and their detail lines to the clipboard."""
        from PySide6.QtWidgets import QApplication

        text = self._runtime_status_selected_text(tree)
        clipboard = QApplication.clipboard()
        if text and clipboard is not None:
            clipboard.setText(text)

    def _runtime_status_show(
        self,
        workers: list[dict[str, Any]] | None = None,
        log_dir: str = "",
        events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Show or refresh the runtime status diagnostics window."""
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtGui import QAction, QKeySequence
        from PySide6.QtWidgets import (
            QDialog,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QTreeWidget,
            QVBoxLayout,
        )

        event_rows = [dict(event) for event in (events or []) if isinstance(event, dict)]
        worker_rows = [dict(worker) for worker in (workers or []) if isinstance(worker, dict)]

        def _open() -> None:
            from ui.shared.theme import is_dark_mode, theme_colors

            dialog = self._runtime_status_dialog
            tree = None
            summary = None
            if dialog is not None and dialog.isVisible():
                tree = dialog.findChild(QTreeWidget)
                summary = dialog.findChild(QLabel, "runtimeWorkerSummary")
            if dialog is None or not dialog.isVisible() or tree is None:
                colors = theme_colors(is_dark_mode())
                dialog = QDialog()
                dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
                dialog.setWindowTitle(t("Runtime Status"))
                from ui.shared.window_utils import enable_standard_window_controls
                enable_standard_window_controls(dialog)
                dialog.setStyleSheet(
                    f"""
                    QLabel#runtimeWorkerSummary, QTreeWidget {{
                        font-family: Consolas, "SF Mono", "DejaVu Sans Mono", monospace;
                        font-size: 9pt;
                    }}
                    QTreeWidget {{
                        background: {colors["surface"]};
                        border: 1px solid {colors["border"]};
                        border-radius: 8px;
                    }}
                    """
                )
                root = QVBoxLayout(dialog)
                root.setContentsMargins(16, 16, 16, 16)
                root.setSpacing(10)
                summary = QLabel()
                summary.setObjectName("runtimeWorkerSummary")
                summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                root.addWidget(summary)
                tree = QTreeWidget()
                tree.setHeaderHidden(True)
                tree.setColumnCount(1)
                tree.setRootIsDecorated(True)
                tree.setUniformRowHeights(True)
                tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
                tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                tree.setTextElideMode(Qt.TextElideMode.ElideNone)
                tree.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
                copy_selected_action = QAction(t("Copy selected"), tree)
                copy_selected_action.setObjectName("runtimeCopySelectedAction")
                copy_selected_action.setShortcut(QKeySequence.StandardKey.Copy)
                copy_selected_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                copy_selected_action.setEnabled(False)
                copy_selected_action.triggered.connect(lambda: self._runtime_status_copy_selected(tree))
                tree.addAction(copy_selected_action)
                copy_all_action = QAction(t("Copy all"), tree)
                copy_all_action.triggered.connect(self._runtime_status_copy_all)
                tree.addAction(copy_all_action)
                root.addWidget(tree, 1)
                footer = QHBoxLayout()
                refresh = QPushButton(t("Refresh"))
                refresh.clicked.connect(lambda: self.emit("ui.runtime_status.open_requested", {}))
                footer.addWidget(refresh)
                copy_selected = QPushButton(t("Copy selected"))
                copy_selected.setObjectName("runtimeCopySelectedButton")
                copy_selected.setEnabled(False)
                copy_selected.clicked.connect(lambda: self._runtime_status_copy_selected(tree))
                footer.addWidget(copy_selected)
                copy_all = QPushButton(t("Copy all"))
                copy_all.clicked.connect(lambda: self._runtime_status_copy_all())
                footer.addWidget(copy_all)

                def _update_copy_selected() -> None:
                    enabled = bool(tree.selectedItems())
                    copy_selected.setEnabled(enabled)
                    copy_selected_action.setEnabled(enabled)

                tree.itemSelectionChanged.connect(_update_copy_selected)
                if log_dir:
                    open_logs = QPushButton(t("Open log folder"))
                    open_logs.clicked.connect(lambda checked=False, path=log_dir: _open_folder(path))
                    footer.addWidget(open_logs)
                footer.addStretch()
                close = QPushButton(t("Close"))
                close.clicked.connect(dialog.close)
                footer.addWidget(close)
                root.addLayout(footer)
                dialog.resize(860, 560)

                def _forget(_obj=None) -> None:
                    self._runtime_status_dialog = None
                    try:
                        self.emit("ui.runtime_status.closed", {})
                    except Exception:  # noqa: BLE001 - closing during shutdown
                        pass

                dialog.destroyed.connect(_forget)
                self._runtime_status_dialog = dialog
                self.emit("ui.runtime_status.opened", {})
            if summary is not None:
                summary.setText(self._runtime_worker_summary(worker_rows, log_dir))
            tree.clear()
            self._runtime_status_snapshot = {"workers": worker_rows, "log_dir": log_dir, "events": list(event_rows)}
            for event in event_rows:
                self._runtime_status_add_event(tree, event)
            tree.scrollToBottom()
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

        QTimer.singleShot(0, _open)
        return {"queued": True, "count": len(worker_rows), "events": len(event_rows)}

    def _runtime_status_append(self, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Stream new runtime events into the open Runtime Status window."""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QTreeWidget

        event_rows = [dict(event) for event in (events or []) if isinstance(event, dict)]
        if not event_rows:
            return {"appended": 0}

        def _append() -> None:
            dialog = self._runtime_status_dialog
            if dialog is None or not dialog.isVisible():
                return
            tree = dialog.findChild(QTreeWidget)
            if tree is None:
                return
            scrollbar = tree.verticalScrollBar()
            pinned = scrollbar.value() >= scrollbar.maximum() - 4
            snapshot = getattr(self, "_runtime_status_snapshot", None)
            if isinstance(snapshot, dict):
                snapshot.setdefault("events", []).extend(event_rows)
                del snapshot["events"][:-600]
            for event in event_rows:
                self._runtime_status_add_event(tree, event)
            if pinned:
                tree.scrollToBottom()

        QTimer.singleShot(0, _append)
        return {"appended": len(event_rows)}

    def _runtime_status_copy_all(self) -> None:
        """Copy the full runtime status (workers + events + details) as text."""
        from PySide6.QtWidgets import QApplication

        snapshot = getattr(self, "_runtime_status_snapshot", None) or {}
        text = self._runtime_status_text(
            snapshot.get("workers") or [],
            str(snapshot.get("log_dir") or ""),
            snapshot.get("events") or [],
        )
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    def _health_show(self, rows: list[dict[str, Any]] | None = None, title: str = "") -> dict[str, Any]:
        """Show live setup/health rows in a dismissible window."""
        from PySide6.QtCore import QTimer

        body = self._format_status_rows(rows)

        def _open() -> None:
            self._show_status_dialog(title or "Setup check", body)

        QTimer.singleShot(0, _open)
        return {"queued": True, "count": len(rows or [])}

    def _privacy_report(self, report: dict[str, Any] | None = None, title: str = "") -> dict[str, Any]:
        """Show privacy redaction details using only safe previews."""
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

        report = report or {}
        count = int(report.get("count") or 0)
        items = [item for item in (report.get("items") or []) if isinstance(item, dict)]
        lines: list[str] = []
        for item in items:
            category = _privacy_category_label(str(item.get("category") or "Sensitive data"))
            source = _privacy_source_label(str(item.get("source") or "Context"))
            preview = str(item.get("preview") or item.get("replacement") or "[redacted]")
            lines.append(f"{category} - {source}: {preview}")
        body = "\n".join(lines) or t("No status details available.")

        def _open() -> None:
            dialog = QDialog()
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.setWindowTitle(t(title or "Privacy Report"))
            from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen

            enable_standard_window_controls(dialog)
            root = QVBoxLayout(dialog)
            root.setContentsMargins(18, 18, 18, 18)
            root.setSpacing(12)

            heading = QLabel(t("Privacy redaction report"))
            heading.setStyleSheet("font-size: 16px; font-weight: 600;")
            root.addWidget(heading)
            count_label = QLabel(f"{count} {t('item(s) detected and censored.')}")
            count_label.setWordWrap(True)
            root.addWidget(count_label)

            details = QTextEdit()
            details.setObjectName("privacyReportDetails")
            details.setReadOnly(True)
            details.setPlainText(body)
            root.addWidget(details, 1)

            footer = QHBoxLayout()
            footer.addStretch()
            close = QPushButton(t("Close"))
            close.clicked.connect(dialog.close)
            footer.addWidget(close)
            root.addLayout(footer)

            dialog.setMinimumSize(720, 480)
            fit_window_to_screen(dialog, preferred_width=840, preferred_height=600)
            status_dialogs = getattr(self, "_status_dialogs", None)
            if isinstance(status_dialogs, list):
                status_dialogs.append(dialog)

                def _forget(_result: int, d=dialog) -> None:
                    if d in status_dialogs:
                        status_dialogs.remove(d)

                dialog.finished.connect(_forget)
            dialog.open()

        QTimer.singleShot(0, _open)
        return {"queued": True, "count": count}

    def _privacy_review_request(
        self,
        approval_id: str = "",
        items: list[dict[str, Any]] | None = None,
        scrubbed_preview: str = "",
        count: int = 0,
        ai_enabled: bool = False,
        review_kind: str = "privacy",
        **_extra: Any,
    ) -> dict[str, Any]:
        """Show one blocking, local-only review before a model request starts."""
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QLabel,
            QTextEdit,
            QVBoxLayout,
        )

        if str(review_kind or "privacy").strip().lower() == "prompt_injection":
            dialog = QDialog()
            dialog.setWindowTitle(t("Possible prompt injection detected"))
            dialog.setModal(True)
            layout = QVBoxLayout(dialog)
            heading = QLabel(
                t(
                    "OpenWand found {count} possible prompt-injection phrase(s) in captured text."
                ).format(count=int(count or len(items or [])))
            )
            heading.setWordWrap(True)
            layout.addWidget(heading)
            explanation = QLabel(
                t(
                    "Captured content may be trying to give instructions to the model. "
                    "Review the matches, then continue or cancel the request."
                )
            )
            explanation.setWordWrap(True)
            layout.addWidget(explanation)

            match_lines: list[str] = []
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                source = _privacy_source_label(str(item.get("source") or "Context"))
                preview_text = str(item.get("preview") or "")
                match_lines.append(f"{source}: {preview_text}")
            matches = QTextEdit()
            matches.setObjectName("promptInjectionReviewMatches")
            matches.setReadOnly(True)
            matches.setPlainText("\n".join(match_lines) or t("No match details available."))
            matches.setMaximumHeight(180)
            layout.addWidget(matches)

            preview_label = QLabel(t("Captured text being sent with your request:"))
            preview_label.setWordWrap(True)
            layout.addWidget(preview_label)
            preview = QTextEdit()
            preview.setObjectName("promptInjectionReviewPreview")
            preview.setReadOnly(True)
            preview.setPlainText(str(scrubbed_preview or ""))
            layout.addWidget(preview, 1)

            decision = "cancel"

            def _choose_injection(value: str) -> None:
                nonlocal decision
                decision = value
                dialog.accept() if value == "full" else dialog.reject()

            buttons = QDialogButtonBox()
            continue_button = buttons.addButton(
                t("Continue with request"), QDialogButtonBox.ButtonRole.AcceptRole
            )
            cancel_button = buttons.addButton(
                t("Cancel send"), QDialogButtonBox.ButtonRole.RejectRole
            )
            continue_button.clicked.connect(lambda: _choose_injection("full"))
            cancel_button.clicked.connect(lambda: _choose_injection("cancel"))
            layout.addWidget(buttons)
            dialog.resize(760, 620)
            dialog.exec()
            return {
                "approval_id": approval_id,
                "approved": decision == "full",
                "decision": decision,
            }

        dialog = QDialog()
        dialog.setWindowTitle(t("Review private information"))
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        heading = QLabel(
            t("OpenWand found {count} private item(s). Review the redacted request before sending.").format(
                count=int(count or len(items or []))
            )
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)
        detector = QLabel(
            t("Detection: advanced local AI model and built-in patterns")
            if ai_enabled
            else t("Detection: built-in patterns")
        )
        detector.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(detector)
        summary_lines: list[str] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            category = _privacy_category_label(str(item.get("category") or "Sensitive data"))
            source = _privacy_source_label(str(item.get("source") or "Context"))
            preview = str(item.get("preview") or item.get("replacement") or "[redacted]")
            replacement = str(item.get("replacement") or "[redacted]")
            summary_lines.append(f"{category} - {source}: {preview} → {replacement}")
        if summary_lines:
            summary = QTextEdit()
            summary.setObjectName("privacyReviewSummary")
            summary.setReadOnly(True)
            summary.setPlainText("\n".join(summary_lines))
            summary.setMaximumHeight(150)
            layout.addWidget(summary)
        preview_label = QLabel(t("Redacted request that the model will receive:"))
        preview_label.setWordWrap(True)
        layout.addWidget(preview_label)
        preview = QTextEdit()
        preview.setObjectName("privacyReviewPreview")
        preview.setReadOnly(True)
        preview.setPlainText(str(scrubbed_preview or ""))
        layout.addWidget(preview, 1)
        warning = QLabel(t("Send full message bypasses privacy redaction for this request."))
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #d8932a;")
        layout.addWidget(warning)
        decision = "cancel"

        def _choose(value: str) -> None:
            nonlocal decision
            decision = value
            dialog.accept() if value != "cancel" else dialog.reject()

        buttons = QDialogButtonBox()
        send_button = buttons.addButton(t("Send redacted"), QDialogButtonBox.ButtonRole.AcceptRole)
        full_button = buttons.addButton(t("Send full message"), QDialogButtonBox.ButtonRole.DestructiveRole)
        cancel_button = buttons.addButton(t("Cancel send"), QDialogButtonBox.ButtonRole.RejectRole)
        send_button.clicked.connect(lambda: _choose("redacted"))
        full_button.clicked.connect(lambda: _choose("full"))
        cancel_button.clicked.connect(lambda: _choose("cancel"))
        layout.addWidget(buttons)
        dialog.resize(760, 620)
        dialog.exec()
        return {
            "approval_id": approval_id,
            "approved": decision in {"redacted", "full"},
            "decision": decision,
        }

    def _show_memory(self, facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Show memory."""
        from PySide6.QtCore import QTimer

        manager = self._memory_manager()
        if hasattr(manager, "replace_facts"):
            manager.replace_facts(facts or [])

        def _open() -> None:
            """Open (or raise) the Memory viewer on the Qt thread."""
            watchdog = getattr(self, "_watchdog", None)
            if watchdog is not None:
                watchdog.expect_slow(10.0)
            try:
                from ui.memory_viewer import MemoryViewer

                if self._memory_viewer is not None and self._memory_viewer.isVisible():
                    self._memory_viewer.raise_()
                    self._memory_viewer.activateWindow()
                    return

                viewer = MemoryViewer(manager, parent=None)
                if viewer is None:
                    print("[ui] MemoryViewer construction returned None")
                    return
                self._memory_viewer = viewer
                viewer.destroyed.connect(lambda: setattr(self, "_memory_viewer", None))
                viewer.show()
                viewer.raise_()
                viewer.activateWindow()
            except Exception:
                traceback.print_exc()
            finally:
                if watchdog is not None:
                    watchdog.beat()

        QTimer.singleShot(50, _open)
        return {"queued": True}

    def _show_addons(
        self,
        addons: list[dict[str, Any]] | None = None,
        addons_dir: str = "",
    ) -> dict[str, Any]:
        """Show addons."""
        from PySide6.QtCore import Qt, QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import (
            QDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QScrollArea,
            QVBoxLayout,
            QWidget,
        )

        # Always rebuild from the latest payload: this method is also called to
        # refresh the list after an enable/disable toggle, so reusing the open
        # dialog would show stale state.
        reused = self._addons_dialog is not None and self._addons_dialog.isVisible()
        if self._addons_dialog is not None:
            self._addons_dialog.close()
            self._addons_dialog = None

        dialog = QDialog()
        dialog.setWindowTitle(t("Addon Manager"))
        dialog.setModal(False)
        from ui.shared.window_utils import enable_standard_window_controls
        enable_standard_window_controls(dialog)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(t("Addons"))
        title.setStyleSheet("font-size: 15pt; font-weight: 700;")
        root.addWidget(title)

        subtitle = QLabel(
            t(
                "Addons are Python packages in the add-ons folder. "
                "Portable builds create this folder next to OpenWand.exe when possible."
            )
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 9pt; opacity: 0.7;")
        root.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        addon_rows = addons or []
        if not addon_rows:
            empty = QLabel(t("No addons found."))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("opacity: 0.55; font-size: 10pt;")
            inner_layout.addWidget(empty)
        else:
            for addon in addon_rows:
                inner_layout.addWidget(self._addon_card(addon))

        inner_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        if addons_dir:
            open_btn = QPushButton(t("Open addons folder"))
            def _open_addons_dir() -> None:
                Path(addons_dir).mkdir(parents=True, exist_ok=True)
                QDesktopServices.openUrl(QUrl.fromLocalFile(addons_dir))

            open_btn.clicked.connect(_open_addons_dir)
            footer.addWidget(open_btn)
            install_btn = QPushButton(t("Install archive"))
            install_btn.clicked.connect(lambda: self._install_addon_archive_dialog())
            footer.addWidget(install_btn)
            install_folder_btn = QPushButton(t("Install folder"))
            install_folder_btn.clicked.connect(lambda: self._install_addon_folder_dialog())
            footer.addWidget(install_folder_btn)
        footer.addStretch()
        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(dialog.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        dialog.resize(620, 500)
        self._addons_dialog = dialog
        self._addons_dialog.destroyed.connect(lambda: setattr(self, "_addons_dialog", None))
        localize_widget_tree(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return {"shown": True, "reused": reused}

    def _show_specific_addon_settings(
        self,
        addon: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Open one addon's settings directly from a contextual shortcut."""
        item = addon if isinstance(addon, dict) else {}
        addon_id = str(item.get("id") or "").strip()
        if not addon_id:
            return {"shown": False, "reason": "addon_not_found"}
        self._show_addon_settings_dialog(
            addon_id,
            str(item.get("name") or addon_id),
            list(item.get("settings") or []),
        )
        return {"shown": True}

    def _show_specific_addon_settings(
        self,
        addon: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Open one addon's settings directly from a contextual shortcut."""
        item = addon if isinstance(addon, dict) else {}
        addon_id = str(item.get("id") or "").strip()
        if not addon_id:
            return {"shown": False, "reason": "addon_not_found"}
        self._show_addon_settings_dialog(
            addon_id,
            str(item.get("name") or addon_id),
            list(item.get("settings") or []),
        )
        return {"shown": True}

    def _install_addon_archive_dialog(self) -> None:
        """Install addon archive dialog."""
        from PySide6.QtWidgets import QFileDialog

        archive, _selected_filter = QFileDialog.getOpenFileName(
            self._addons_dialog,
            t("Install Addon Archive"),
            "",
            t("OpenWand Addons (*.openwand *.zip)"),
        )
        if archive:
            self.emit("ui.addons.install_archive", {"path": archive})

    def _install_addon_folder_dialog(self) -> None:
        """Install addon folder dialog."""
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(
            self._addons_dialog,
            t("Install Addon Folder"),
            "",
        )
        if folder:
            self.emit("ui.addons.install_folder", {"path": folder})

    def _addon_card(self, addon: dict[str, Any]):
        """Build an addon card for the Qt protocol host."""
        from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

        card = QFrame()
        card.setObjectName("addonCard")
        card.setStyleSheet(
            "QFrame#addonCard { border: 1px solid #55555f; "
            "border-radius: 8px; padding: 2px; }"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        name_row = QHBoxLayout()
        name = str(addon.get("name") or addon.get("id") or t("Addon"))
        addon_id = str(addon.get("id") or name)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-size: 11pt; font-weight: 600;")
        name_row.addWidget(name_lbl)
        name_row.addStretch()

        settings_btn = QPushButton(t("Settings"))
        settings_btn.setToolTip(t("Open this addon's settings"))
        settings_btn.clicked.connect(
            lambda _checked=False, a=addon, aid=addon_id, n=name: self._show_addon_settings_dialog(
                aid,
                n,
                a.get("settings") or [],
            )
        )
        name_row.addWidget(settings_btn)

        logs_btn = QPushButton(t("Logs"))
        logs_btn.setToolTip(t("Open this addon's diagnostic log"))
        logs_btn.clicked.connect(
            lambda _checked=False, a=addon, aid=addon_id, n=name: self._show_addon_log_dialog(
                aid,
                n,
                str(a.get("logs") or ""),
            )
        )
        name_row.addWidget(logs_btn)

        runtime = addon.get("runtime") if isinstance(addon.get("runtime"), dict) else {}
        approval = addon.get("approval") if isinstance(addon.get("approval"), dict) else {}
        has_dependencies = str(runtime.get("tier") or "1") == "2"

        if approval.get("needs_approval"):
            approve_btn = QPushButton(t("Review access"))
            approve_btn.setToolTip(t("Review the addon author and requested access before activation"))
            approve_btn.clicked.connect(
                lambda _checked=False, a=addon: self._confirm_addon_access(a)
            )
            name_row.addWidget(approve_btn)

        if has_dependencies and not approval.get("needs_approval"):
            repair_btn = QPushButton(self._addon_runtime_action_label(runtime))
            repair_btn.setToolTip(t("Install or rebuild this addon's dependency environment"))
            repair_btn.clicked.connect(
                lambda _checked=False, aid=addon_id: self.emit(
                    "ui.addons.repair_environment", {"addon_id": aid}
                )
            )
            name_row.addWidget(repair_btn)

        enabled = bool(addon.get("enabled", True))
        enable = QCheckBox(t("Enabled"))
        enable.setChecked(enabled)
        # "discovered" (not yet loaded into a manager) addons can't be toggled live.
        enable.setEnabled(str(addon.get("status") or "") in {"loaded", "disabled", "needs_dependencies", "needs_approval"})
        enable.toggled.connect(
            lambda checked, aid=addon_id: self.emit(
                "ui.addons.set_enabled",
                {"addon_id": aid, "enabled": bool(checked)},
            )
        )
        name_row.addWidget(enable)
        layout.addLayout(name_row)

        author = str(addon.get("author") or "").strip() or t("Unknown author")
        author_lbl = QLabel(f"{t('Author:')} {author}")
        author_lbl.setStyleSheet("font-size: 8pt; opacity: 0.65;")
        layout.addWidget(author_lbl)

        path = str(addon.get("path") or "")
        if path:
            path_lbl = QLabel(path)
            path_lbl.setStyleSheet("font-size: 8pt; opacity: 0.45;")
            layout.addWidget(path_lbl)

        hooks = addon.get("hooks") or []
        tools = addon.get("tools") or []
        details = []
        if hooks:
            details.append(t("Hooks: ") + ", ".join(str(h) for h in hooks))
        if tools:
            details.append(t("Tools: ") + ", ".join(str(tool) for tool in tools))
        if details:
            detail_lbl = QLabel("\n".join(details))
            detail_lbl.setWordWrap(True)
            detail_lbl.setStyleSheet("font-size: 8pt; opacity: 0.65;")
            layout.addWidget(detail_lbl)

        actions = [item for item in (addon.get("actions") or []) if isinstance(item, dict)]
        if actions:
            actions_title = QLabel(t("Actions"))
            actions_title.setStyleSheet("font-size: 8pt; font-weight: 600; opacity: 0.75;")
            layout.addWidget(actions_title)
        for action in actions:
            action_row = QHBoxLayout()
            action_label = str(action.get("label") or action.get("id") or t("Action"))
            kind = str(action.get("kind") or "action").replace("_", " ")
            access = [str(item).title() for item in (action.get("access") or [])]
            suffix = f" · {kind}"
            if access:
                suffix += " · " + ", ".join(access)
            descriptor = QLabel(action_label + suffix)
            colour = str(action.get("colour") or "green")
            tone = {"green": "#4fb477", "amber": "#d7a93f", "red": "#d05f5f"}.get(colour, "#9aa0aa")
            descriptor.setStyleSheet(f"font-size: 8pt; color: {tone};")
            descriptor.setToolTip(str(action.get("path") or action.get("hint") or ""))
            action_row.addWidget(descriptor, 1)
            action_enabled = QCheckBox(t("Enabled"))
            action_enabled.setChecked(bool(action.get("enabled", True)))
            action_id = str(action.get("id") or "")
            action_enabled.toggled.connect(
                lambda checked, aid=addon_id, action_id=action_id: self.emit(
                    "ui.addons.set_action_enabled",
                    {"addon_id": aid, "action_id": action_id, "enabled": bool(checked)},
                )
            )
            action_row.addWidget(action_enabled)
            layout.addLayout(action_row)

        action_issues = [item for item in (addon.get("action_issues") or []) if isinstance(item, dict)]
        if action_issues:
            issue_lbl = QLabel("\n".join(str(item.get("message") or "") for item in action_issues))
            issue_lbl.setWordWrap(True)
            issue_lbl.setStyleSheet("font-size: 8pt; color: #b42318;")
            layout.addWidget(issue_lbl)

        access = [
            t(str(item.get("label") or ""))
            for item in (approval.get("access") or [])
            if isinstance(item, dict)
        ]
        if access:
            access_lbl = QLabel(t("Declared access:") + " " + "; ".join(access))
            access_lbl.setWordWrap(True)
            access_lbl.setStyleSheet("font-size: 8pt; opacity: 0.55;")
            layout.addWidget(access_lbl)

        if has_dependencies:
            dep_parts = [self._addon_runtime_summary(runtime)]
            runtime_error = str(runtime.get("error") or "")
            if runtime_error:
                dep_parts.append(runtime_error)
            dep_lbl = QLabel("\n".join(dep_parts))
            dep_lbl.setWordWrap(True)
            dep_lbl.setStyleSheet("font-size: 8pt; opacity: 0.6;")
            layout.addWidget(dep_lbl)

        error = str(addon.get("error") or "")
        if error:
            error_lbl = QLabel(error)
            error_lbl.setWordWrap(True)
            error_lbl.setStyleSheet("font-size: 8pt; color: #b42318;")
            layout.addWidget(error_lbl)
        return card

    def _confirm_addon_access(self, addon: dict[str, Any]) -> None:
        """Review addon identity and access before asking the brain to activate it."""
        from PySide6.QtWidgets import QMessageBox

        addon_id = str(addon.get("id") or "")
        display_name = str(addon.get("name") or addon_id or t("Addon"))
        author = str(addon.get("author") or "").strip() or t("Unknown author")
        approval = addon.get("approval") if isinstance(addon.get("approval"), dict) else {}
        access = [
            t(str(item.get("label") or "").strip())
            for item in (approval.get("new_access") or approval.get("access") or [])
            if isinstance(item, dict) and str(item.get("label") or "").strip()
        ]
        disk = addon.get("disk") if isinstance(addon.get("disk"), dict) else {}
        lines = [
            display_name,
            f"{t('Author:')} {author} ({t('self-declared')})",
            "",
            t("Requested access:"),
        ]
        lines.extend(f"  • {label}" for label in access)
        lines.extend(["", f"{t('Current disk use:')} {disk.get('label') or t('Unknown')}"])
        if disk.get("dependencies_pending"):
            lines.append(t("Additional components will use more disk space."))
        lines.extend([
            "",
            t("Full-code addons run with your normal user permissions. Only approve addons you trust."),
            "",
            t("Approve and activate this addon?"),
        ])
        choice = QMessageBox.question(
            self._addons_dialog,
            t("Review Addon Access"),
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self.emit("ui.addons.approve", {"addon_id": addon_id})

    @staticmethod
    def _addon_runtime_action_label(runtime: dict[str, Any]) -> str:
        """Return the dependency action label for an addon."""
        return t("Repair env") if runtime.get("ready") else t("Install env")

    @staticmethod
    def _addon_runtime_summary(runtime: dict[str, Any]) -> str:
        """Return a short dependency status summary for an addon."""
        return t("Dependency env: ready") if runtime.get("ready") else t("Dependency env: needs install")

    def _show_addon_settings_dialog(self, addon_id: str, display_name: str, settings: list):
        """Show addon settings dialog."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QScrollArea,
            QVBoxLayout,
            QWidget,
        )

        existing = self._addon_settings_dialogs.get(addon_id)
        if existing is not None and existing.isVisible():
            existing.close()

        dialog = QDialog(self._addons_dialog)
        dialog.setWindowTitle(f"{display_name} {t('Settings')}")
        dialog.setModal(False)
        from ui.shared.window_utils import enable_standard_window_controls
        enable_standard_window_controls(dialog)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(f"{display_name} {t('Settings')}")
        title.setStyleSheet("font-size: 14pt; font-weight: 700;")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        settings_box = self._addon_settings_box(addon_id, settings)
        if settings_box is None:
            empty = QLabel(t("This addon does not expose settings."))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("opacity: 0.55; font-size: 10pt;")
            inner_layout.addWidget(empty)
        else:
            inner_layout.addWidget(settings_box)
        inner_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(dialog.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        dialog.resize(500, 420)
        dialog.destroyed.connect(lambda _obj=None, key=addon_id: self._addon_settings_dialogs.pop(key, None))
        self._addon_settings_dialogs[addon_id] = dialog
        localize_widget_tree(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _show_addon_log_dialog(self, addon_id: str, display_name: str, logs: str):
        """Show addon log dialog."""
        from PySide6.QtGui import QTextCursor
        from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

        existing = self._addon_log_dialogs.get(addon_id)
        if existing is not None and existing.isVisible():
            text = existing.findChild(QTextEdit)
            if text is not None:
                text.setPlainText(logs or t("No log output yet."))
                text.moveCursor(QTextCursor.MoveOperation.End)
            existing.raise_()
            existing.activateWindow()
            return {"shown": True, "reused": True}

        dialog = QDialog(self._addons_dialog)
        dialog.setWindowTitle(f"{display_name} {t('Logs')}")
        dialog.setModal(False)
        from ui.shared.window_utils import enable_standard_window_controls
        enable_standard_window_controls(dialog)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(f"{display_name} {t('Logs')}")
        title.setStyleSheet("font-size: 14pt; font-weight: 700;")
        root.addWidget(title)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        text.setPlainText(logs or t("No log output yet."))
        text.moveCursor(QTextCursor.MoveOperation.End)
        root.addWidget(text, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(dialog.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        dialog.resize(680, 460)
        dialog.destroyed.connect(lambda _obj=None, key=addon_id: self._addon_log_dialogs.pop(key, None))
        self._addon_log_dialogs[addon_id] = dialog
        localize_widget_tree(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return {"shown": True, "reused": False}

    def _addon_settings_box(self, addon_id: str, settings: list):
        """Build the addon settings form."""
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QFormLayout,
            QFrame,
            QLineEdit,
        )

        if not settings:
            return None
        box = QFrame()
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(6)
        truthy = {"1", "true", "yes", "on"}

        def save(key, value):
            """Emit a setting-change event for this addon."""
            self.emit(
                "ui.addons.set_setting",
                {"addon_id": addon_id, "key": key, "value": value},
            )

        for s in settings:
            if not isinstance(s, dict):
                continue
            key = str(s.get("key") or "").strip()
            if not key:
                continue
            label = str(s.get("label") or key)
            stype = str(s.get("type") or "text").lower()
            value = s.get("value")
            options = s.get("options") or []

            if stype == "bool":
                w = QCheckBox()
                w.setChecked(str(value).strip().lower() in truthy)
                w.toggled.connect(
                    lambda checked, k=key: save(k, "true" if checked else "false")
                )
            elif stype == "choice" and options:
                w = QComboBox()
                opts = [str(o) for o in options]
                w.addItems(opts)
                if str(value) in opts:
                    w.setCurrentText(str(value))
                w.currentTextChanged.connect(lambda text, k=key: save(k, text))
            else:
                w = QLineEdit("" if value is None else str(value))
                if stype == "number":
                    w.setPlaceholderText(t("number"))
                w.editingFinished.connect(lambda k=key, e=w: save(k, e.text()))

            help_text = str(s.get("help") or "")
            if help_text:
                w.setToolTip(t(help_text))
            form.addRow(t(label), w)
        return box if form.rowCount() else None

    def _show_agent_task(self, spec: dict[str, Any] | None = None) -> dict[str, Any]:
        """Show agent task."""
        from core.agent.task_spec import agent_task_spec_from_dict
        from ui.agent.task_window import open_agent_task_dialog

        initial_spec = None
        if isinstance(spec, dict) and spec:
            initial_spec = agent_task_spec_from_dict(spec)

        def on_submit(task_spec) -> None:
            """Handle submit events."""
            from dataclasses import asdict

            self._start_agent_run(asdict(task_spec))

        open_agent_task_dialog(
            parent=None,
            on_submit=on_submit,
            approval_notice_callback=lambda text, resolved: self._agent_notify_approval(
                text,
                resolved=resolved,
            ),
            initial_spec=initial_spec,
        )
        return {"shown": True, "prefilled": initial_spec is not None}

    def _start_agent_run(self, spec: dict[str, Any]) -> None:
        """Start agent run."""
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.close()
        dialog = MacAgentRunDialog(self, spec)
        self._agent_run_dialog = dialog
        dialog.dialog.destroyed.connect(
            lambda _obj=None, w=dialog: setattr(self, "_agent_run_dialog", None)
            if self._agent_run_dialog is w else None
        )
        dialog.show()
        self.emit("ui.agent.run_requested", {"spec": spec})

    def _show_agent_history(
        self,
        runs: list[dict[str, Any]] | None = None,
        runs_root: str = "",
    ) -> dict[str, Any]:
        """Show agent history."""
        if self._agent_history_dialog is None:
            dialog = MacAgentHistoryDialog(self)
            self._agent_history_dialog = dialog
            dialog.dialog.destroyed.connect(
                lambda _obj=None, w=dialog: setattr(self, "_agent_history_dialog", None)
                if self._agent_history_dialog is w else None
            )
        self._agent_history_dialog.replace_runs(runs or [], runs_root)
        self._agent_history_dialog.show()
        return {"shown": True, "runs": len(runs or [])}

    def _agent_log(self, **params: Any) -> dict[str, Any]:
        """Handle agent log for qt protocol host."""
        accepted = False
        workspace = getattr(self, "_virtual_workspace_window", None)
        if (
            bool(getattr(self, "_virtual_workspace_agent_active", False))
            and workspace is not None
            and workspace.isVisible()
        ):
            workspace.append_agent_event(params)
            accepted = True
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.append_log(params)
            accepted = True
        return {"accepted": accepted}

    def _agent_trace(self, **params: Any) -> dict[str, Any]:
        """Handle agent trace for qt protocol host."""
        accepted = False
        workspace = getattr(self, "_virtual_workspace_window", None)
        if (
            bool(getattr(self, "_virtual_workspace_agent_active", False))
            and workspace is not None
            and workspace.isVisible()
        ):
            accepted = bool(workspace.append_agent_trace(params))
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.append_trace(params)
            accepted = True
        return {"accepted": accepted}

    def _agent_done(self, **params: Any) -> dict[str, Any]:
        """Handle agent done for qt protocol host."""
        accepted = False
        workspace = getattr(self, "_virtual_workspace_window", None)
        workspace_task_active = bool(getattr(self, "_virtual_workspace_agent_active", False))
        if (
            workspace_task_active
            and workspace is not None
            and workspace.isVisible()
        ):
            workspace.finish_agent_task(params)
            accepted = True
        if workspace_task_active:
            self._virtual_workspace_agent_active = False
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.finish(params)
            accepted = True
        return {"accepted": accepted}

    def _agent_approval_request(self, **params: Any) -> dict[str, Any]:
        """Handle agent approval request for qt protocol host."""
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.request_approval(params)
            return {"accepted": True}
        workspace = getattr(self, "_virtual_workspace_window", None)
        approval_id = str(params.get("approval_id") or "").strip()
        if (
            bool(getattr(self, "_virtual_workspace_agent_active", False))
            and workspace is not None
            and workspace.isVisible()
            and approval_id
        ):
            approved = workspace.request_agent_approval(params)
            self.emit(
                "ui.agent.approval.respond",
                {"approval_id": approval_id, "approved": approved},
            )
            return {"accepted": True}
        result = self._agent_notify_approval("Agent approval requested.", resolved=False, data=params)
        return {"accepted": bool(result.get("actionable"))}

    def _agent_history_detail(self, **params: Any) -> dict[str, Any]:
        """Handle agent history detail for qt protocol host."""
        if self._agent_history_dialog is not None:
            self._agent_history_dialog.update_detail(params)
            return {"accepted": True}
        return {"accepted": False}

    def _agent_notify_approval(
        self,
        text: str = "",
        resolved: bool = False,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Handle agent notify approval for qt protocol host."""
        overlay = self._ensure_overlay()
        notify = getattr(overlay, "notify_agent_approval", None)
        approval_id = str((data or {}).get("approval_id") or "").strip()

        def respond(approved: bool) -> None:
            self.emit(
                "ui.agent.approval.respond",
                {"approval_id": approval_id, "approved": bool(approved)},
            )

        kwargs: dict[str, Any] = {"resolved": bool(resolved)}
        if approval_id and not resolved:
            kwargs["on_approve"] = lambda: respond(True)
            kwargs["on_decline"] = lambda: respond(False)
        if callable(notify):
            result = notify(text or "Agent approval requested.", **kwargs)
            if isinstance(result, dict):
                return {**result, "data": data or {}}
        return {"shown": bool(callable(notify)), "actionable": False, "data": data or {}}


def main() -> int:
    """Handle main for runtime workers UI host."""
    root = configure_paths()
    os.chdir(root)
    selected_platform = _configure_linux_ui_platform()
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.screen=false")
    os.environ.setdefault("OPENWAND_MACOS_PY_UI_HOST", "1")
    real_out = _protect_stdout()

    from PySide6.QtWidgets import QApplication

    from ui.shared.app_icon import ensure_linux_desktop_entry, install_app_icon, set_windows_app_user_model_id

    set_windows_app_user_model_id()
    ensure_linux_desktop_entry()
    app = QApplication(sys.argv)
    if selected_platform:
        log.info("Qt UI platform selected: %s", selected_platform)
    install_app_icon(app)
    app.setQuitOnLastWindowClosed(False)
    try:
        from ui.i18n import install as install_i18n
        from ui.shared.theme import apply_app_theme

        apply_app_theme(app)
        install_i18n(app)
    except Exception:
        traceback.print_exc()

    host = QtProtocolHost(app, real_out)
    app._openwand_runtime_ui_host = host
    try:
        from ui.runtime_log_bridge import set_emitter

        set_emitter(lambda payload: host.emit("ui.log.event", payload))
    except Exception:
        traceback.print_exc()
    host.emit("ui.ready", {"repo": str(root)})
    exit_code = int(app.exec())
    host.finalize_after_exec()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
