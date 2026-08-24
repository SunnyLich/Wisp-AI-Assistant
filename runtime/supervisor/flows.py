"""Product flow controller for the pure-Python worker target."""

from __future__ import annotations

import base64
import itertools
import logging
import os
import queue
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from core.actions.progress import ActionProgress, ActionProgressStage, ActionProgressUpdate
from core.actions.telemetry import ActionTrace
from core.attachment_source import DOCUMENT_SUFFIXES
from core.system.env_utils import mcp_server_id_from_tool, mcp_server_override_key
from runtime.supervisor import flow_context, flow_estimates, flow_utils, tool_inventory, tool_modes
from runtime.supervisor.runtime_log import RuntimeEventLog, normalize_severity
from ui.i18n import t

log = logging.getLogger("openwand.runtime.flows")
_INTERACTIVE_LLM_TIMEOUT_SECONDS = 120.0
_INTERACTIVE_LLM_TOOL_TIMEOUT_SECONDS = 300.0
_SLOW_RESPONSE_NOTICE_SECONDS = 3.0
_ACTION_PROGRESS_HEADS_UP_SECONDS = 4.0
_TTS_SEGMENT_MIN_CHARS = 60
_TTS_SEGMENT_MAX_CHARS = 520
_READ_ALOUD_MIN_WORDS = 50
_READ_ALOUD_MAX_WORDS = 110
_SPEECH_WARMUP_NOTICE_INTERVAL_SECONDS = 5.0
_UNDO_EDIT_WINDOW_SECONDS = 30.0
_READ_ALOUD_PAUSE_RE = re.compile(r"[.!?;:][\"')\]}]*$")
_USE_SHARED_REPLY_PARSER = object()
_BROWSER_APP_NAMES = {
    "browser",
    "chrome",
    "chrome.exe",
    "google chrome",
    "firefox",
    "firefox.exe",
    "safari",
    "brave",
    "brave browser",
    "brave.exe",
    "msedge.exe",
    "microsoft edge",
    "opera",
    "vivaldi",
}
_SELECTED_PATH_TEXT_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml",
    ".yml", ".html", ".htm", ".css", ".xml", ".sh", ".bat", ".ps1",
    ".c", ".cpp", ".h", ".java", ".rs", ".go", ".rb", ".php", ".sql",
    ".toml", ".ini", ".cfg", ".conf", ".log",
}
_SELECTED_PATH_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"}
_SELECTED_PATH_DOCUMENT_EXTS = DOCUMENT_SUFFIXES
_SELECTED_PATH_TEXT_BYTES = 51_200
_AUDIO_CONFIG_KEYS = {
    "TTS_PROVIDER",
    "TTS_SPEAK_REPLIES",
    "CARTESIA_VOICE_ID",
    "ELEVENLABS_VOICE_ID",
    "ELEVENLABS_MODEL",
    "OPENAI_TTS_VOICE",
    "OPENAI_TTS_MODEL",
    "TTS_CUSTOM_BASE_URL",
    "TTS_CUSTOM_VOICE",
    "TTS_CUSTOM_MODEL",
    "TTS_CUSTOM_SAMPLE_RATE",
    "GPT_SOVITS_URL",
    "GPT_SOVITS_REF_AUDIO_PATH",
    "GPT_SOVITS_PROMPT_TEXT",
    "GPT_SOVITS_PROMPT_LANG",
    "GPT_SOVITS_TEXT_LANG",
    "GPT_SOVITS_SAMPLE_RATE",
    "GPT_SOVITS_TEXT_SPLIT_METHOD",
    "GPT_SOVITS_BATCH_SIZE",
    "GPT_SOVITS_SPEED_FACTOR",
    "GPT_SOVITS_SEED",
    "GPT_SOVITS_TIMEOUT_SECONDS",
    "KOKORO_VOICE",
    "KOKORO_LANG_CODE",
    "KOKORO_DEVICE",
    "KOKORO_SPEED",
    "KOKORO_SAMPLE_RATE",
    "KOKORO_SPLIT_PATTERN",
    "TTS_VOLUME",
    "TTS_READ_ALOUD_MIN_WORDS",
    "TTS_READ_ALOUD_MAX_WORDS",
    "STT_PROVIDER",
    "STT_MODEL",
    "STT_COMPUTE_TYPE",
    "STT_LANGUAGE",
    "STT_BEAM_SIZE",
    "STT_DEVICE",
    "STT_CLOUDFLARE_ACCOUNT_ID",
    "STT_CLOUDFLARE_MODEL",
    "STT_CLOUDFLARE_TIMEOUT_SECONDS",
    "STT_CLOUDFLARE_FALLBACK_LOCAL",
    "CLOUDFLARE_API_TOKEN",
    "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS",
    "STT_BACKGROUND_CHUNK_STEP_SECONDS",
    "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS",
    "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS",
    "HOTKEY_VOICE",
    "HOTKEY_VOICE_2",
    "HOTKEY_VOICE_ENABLED",
    "HOTKEY_DICTATE",
    "HOTKEY_DICTATE_2",
    "HOTKEY_DICTATE_ENABLED",
    "LIVE_VOICE_PROVIDER",
    "LIVE_VOICE_MODEL",
    "LIVE_VOICE_VOICE_NAME",
    "LIVE_VOICE_HALF_DUPLEX",
    "LIVE_VOICE_SYSTEM_PROMPT",
}
_PRIVACY_CONFIG_KEYS = {
    "PRIVACY_MODE",
    "PRIVACY_AI_ENABLED",
    "TRUST_PRIVACY_MODE",
}
_HARNESS_CONFIG_KEYS = {
    "CHAT_EXECUTION_MODE",
    "OPENWAND_CLAUDE_CLI",
    "OPENWAND_CLAUDE_SYSTEM_PROMPT",
    "OPENWAND_CLAUDE_WORKSPACE",
    "OPENWAND_CODEX_CLI",
    "OPENWAND_CODEX_HOME",
    "OPENWAND_CODEX_SYSTEM_PROMPT",
    "OPENWAND_CODEX_WORKSPACE",
}


def _configured_harness_workspace(provider: str) -> str:
    """Return an existing provider-specific workspace, or automatic mode."""
    import config

    key = "OPENWAND_CLAUDE_WORKSPACE" if provider == "claude" else "OPENWAND_CODEX_WORKSPACE"
    raw = str(getattr(config, key, "") or "").strip()
    if not raw:
        return ""
    try:
        path = Path(raw).expanduser().resolve()
    except OSError:
        return ""
    return str(path) if path.is_dir() else ""


def _is_transient_local_tts_warmup_error(text: str) -> bool:
    """Return True when local TTS is merely busy importing/warming."""
    lowered = " ".join(str(text or "").lower().split())
    if not lowered or "local speech is ready" not in lowered:
        return False
    if "still warming" not in lowered and "warming up" not in lowered:
        return False
    speech_terms = ("kokoro", "local tts", "local voice", "tts")
    return any(term in lowered for term in speech_terms)


_file_context_text = flow_context.file_context_text
_normalized_tool_context = flow_context.normalized_tool_context
_all_context_off_policy = flow_context.all_context_off_policy
_normalized_context_policy = flow_context.normalized_context_policy
json_safe_dumps = flow_utils.json_safe_dumps


class WorkerLike(Protocol):
    """Model worker like."""
    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        wait: bool = True,
    ) -> Any:
        """Call a method on the worker and return its result."""
        ...

    def on_event(self, event: str, handler) -> None:
        """Handle event events."""
        ...

    def call_with_events(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        on_event: Callable[[str, Any, Any], None],
        on_started: Callable[[Any], None] | None = None,
    ) -> Any:
        """Call with events."""
        ...


@dataclass
class PendingInvocation:
    """Model pending invocation."""
    caller_idx: int = 0
    caller: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    # Provider identity and picker suggestions derived from the active app
    # captured before the overlay is constructed.
    action_provider_context: dict[str, Any] = field(default_factory=dict)
    screenshot_b64: str | None = None
    screenshot_tool_b64: str | None = None
    intent_target_pid: int = 0
    paste_target_pid: int = 0
    is_snip: bool = False
    # (item_id, source_id) pairs removed via the intent picker's per-row X.
    removed_context_sources: set = field(default_factory=set)
    # Per-prompt tool switches selected in the intent overlay.
    tool_choices: list[dict[str, Any]] = field(default_factory=list)
    context_ready: threading.Event = field(default_factory=threading.Event)
    invoked_at_unix_ns: int = 0
    initial_context_at_unix_ns: int = 0
    intent_shown_at_unix_ns: int = 0
    context_ready_at_unix_ns: int = 0


@dataclass
class RewriteAnnotationRequest:
    """One app-attached Rewrite proposal from capture through acceptance."""

    annotation_id: str
    pending: PendingInvocation
    session_key: str
    display_number: int = 1
    comment: str = ""
    include_document: bool = False
    replacement_text: str = ""
    stream_id: Any = None
    state: str = "composing"
    copy_only: bool = False
    structured_target: dict[str, Any] = field(default_factory=dict)
    structured_plan: Any = None


_REWRITE_NUMBER_RESERVED_STATES = {"composing", "held", "queued", "processing"}


@dataclass
class UndoableEdit:
    """The most recent successful paste-back that OpenWand can safely undo."""

    original_text: str
    replacement_text: str
    target_pid: int
    focus_token: int
    created_at: float = field(default_factory=time.monotonic)


class _TtsSegmentBuffer:
    """Collect streamed reply text into stable TTS-sized segments."""

    def __init__(
        self,
        *,
        min_chars: int = _TTS_SEGMENT_MIN_CHARS,
        max_chars: int = _TTS_SEGMENT_MAX_CHARS,
    ) -> None:
        self._buffer = ""
        self._min_chars = min_chars
        self._max_chars = max_chars

    def feed(self, text: str) -> list[str]:
        """Add text and return completed speakable segments."""
        if not text:
            return []
        self._buffer += text
        segments: list[str] = []
        while True:
            boundary = self._boundary()
            if boundary is None:
                break
            segment = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:].lstrip()
            if segment:
                segments.append(segment)
        return segments

    def finish(self) -> list[str]:
        """Return any remaining text as the final segment."""
        segment = self._buffer.strip()
        self._buffer = ""
        return [segment] if segment else []

    def _boundary(self) -> int | None:
        """Find the next stable sentence/paragraph/length boundary."""
        text = self._buffer
        paragraph_at = text.find("\n\n")
        if paragraph_at >= self._min_chars // 2:
            return paragraph_at + 2
        for idx, char in enumerate(text):
            if char not in ".!?":
                continue
            boundary = idx + 1
            if boundary < self._min_chars:
                continue
            if boundary == len(text) or text[boundary].isspace():
                return boundary
        if len(text) >= self._max_chars:
            split_at = max(
                text.rfind(" ", self._min_chars, self._max_chars),
                text.rfind("\n", self._min_chars, self._max_chars),
            )
            return split_at if split_at > 0 else self._max_chars
        return None


class FlowController:
    """Wire native/UI events into brain/audio/native product workflows."""

    def __init__(
        self,
        *,
        native: WorkerLike,
        ui: WorkerLike,
        brain: WorkerLike,
        audio: WorkerLike,
        run_async: bool = True,
        runtime_log: RuntimeEventLog | None = None,
    ) -> None:
        """Initialize the flow controller instance."""
        self.native = native
        self.ui = ui
        self.brain = brain
        self.audio = audio
        self.run_async = run_async
        self.runtime_log = runtime_log if runtime_log is not None else RuntimeEventLog()
        self.runtime_log.set_publisher(self._publish_runtime_events)
        self._lock = threading.RLock()
        self._pending: PendingInvocation | None = None
        self._rewrite_annotations: dict[str, RewriteAnnotationRequest] = {}
        self._rewrite_app_sessions: dict[str, dict[str, Any]] = {}
        self._rewrite_anchor_refreshing: set[str] = set()
        self._voice_context: dict[str, Any] = {}
        self._voice_screenshot_b64: str | None = None
        self._voice_active = False
        self._voice_state = "idle"
        # Dictation push-to-talk (paste transcript into the focused field).
        self._dictate_state = "idle"
        # Live voice conversation (toggle hotkey, Gemini Live in the audio worker).
        self._live_voice_state = "idle"  # idle | starting | active | stopping
        # One "ready" bubble notice per session, on the first listening state.
        self._live_voice_ready_notified = False
        self._dictate_target_pid = 0
        self._dictate_focus_token = 0
        self._generation = itertools.count(1)
        self._current_generation = 0
        self._context_buffer: list[str] = []
        self._drop_context_items: list[dict[str, Any]] = []
        self._pending_context_capture: dict[str, Any] | None = None
        self._last_reply = ""
        self._last_undoable_edit: UndoableEdit | None = None
        self._last_privacy_report: dict[str, Any] = {}
        self._addon_tray_actions_snapshot: tuple[tuple[str, str], ...] = ()
        self._active_agent_stream_id: Any = None
        self._background_task_watchers: set[str] = set()
        self._active_reply_stream_id: Any = None
        self._active_reply_stream_generation = 0
        self._reply_thought_parser = None
        self._tts_lock = threading.RLock()
        self._tts_generation = 0
        self._tts_queue: queue.Queue[str | None] | None = None
        self._tts_sequence_active = False
        self._reply_bubble_cancelled_generation = 0
        self._config_mtime = self._current_config_mtime()
        # Speech readiness notices are timed here, outside the audio process.
        # Native STT/TTS initialization can hold that process's GIL, but should
        # never freeze the user-visible elapsed timer.
        self._speech_warmup_lock = threading.RLock()
        self._speech_warmup_notice_lock = threading.Lock()
        self._speech_warmup_generation = 0
        self._speech_warmup_stop = threading.Event()
        self._speech_warmup_shutdown = False
        self._speech_warmup_started_at = 0.0
        self._speech_warmup_provider = ""
        self._speech_warmup_id = ""
        self._speech_warmup_states: dict[str, dict[str, Any]] = {}

    # -- lifecycle -----------------------------------------------------

    def start(self, *, prewarm: bool = True) -> None:
        """Wire app flows and show the UI, optionally starting background prewarms."""
        self.native.on_event("native.hotkey", self._on_native_hotkey)
        self.ui.on_event("ui.summon_caller", self._on_summon_caller)
        self.ui.on_event("ui.request_snip", self._on_request_snip)
        self.ui.on_event("ui.intent.chosen", self._on_intent_chosen)
        self.ui.on_event("ui.intent.cancelled", self._on_intent_cancelled)
        self.ui.on_event("ui.intent.snip.requested", self._on_intent_snip_requested)
        self.ui.on_event("ui.intent.snip.region", self._on_intent_snip_region)
        self.ui.on_event("ui.intent.snip.cancelled", self._on_intent_snip_cancelled)
        self.ui.on_event("ui.intent.selection.requested", self._on_intent_selection_requested)
        self.ui.on_event("ui.intent.context.remove", self._on_intent_context_remove)
        self.ui.on_event("ui.intent.context.reenabled", self._on_intent_context_reenabled)
        self.ui.on_event("ui.rewrite.annotation.submitted", self._on_rewrite_annotation_submitted)
        self.ui.on_event("ui.rewrite.annotation.held", self._on_rewrite_annotation_held)
        self.ui.on_event("ui.rewrite.send_all", self._on_rewrite_send_all)
        self.ui.on_event("ui.rewrite.annotation.cancelled", self._on_rewrite_annotation_cancelled)
        self.ui.on_event("ui.rewrite.annotation.accepted", self._on_rewrite_annotation_accepted)
        self.ui.on_event("ui.rewrite.annotation.declined", self._on_rewrite_annotation_declined)
        self.ui.on_event("ui.rewrite.annotation.revision_requested", self._on_rewrite_annotation_revision_requested)
        self.ui.on_event("ui.rewrite.annotation.anchor_refresh_requested", self._on_rewrite_anchor_refresh_requested)
        self.ui.on_event("ui.chat.snip.region", self._on_chat_snip_region)
        self.ui.on_event("ui.chat.snip.cancelled", self._on_chat_snip_cancelled)
        self.ui.on_event("ui.chat.selection.requested", self._on_chat_selection_requested)
        self.ui.on_event("ui.snip.region", self._on_snip_region)
        self.ui.on_event("ui.snip.cancelled", self._on_snip_cancelled)
        self.ui.on_event("ui.context.dropped", self._on_context_dropped)
        self.ui.on_event("ui.context.remove", self._on_context_remove)
        self.ui.on_event("ui.chat.request", self._on_chat_request)
        self.ui.on_event("ui.chat.context_preview", self._on_chat_context_preview)
        self.ui.on_event("ui.chat.message_actions.requested", self._on_chat_message_actions_requested)
        self.ui.on_event("ui.chat.message_action.requested", self._on_chat_message_action_requested)
        self.ui.on_event("ui.memory.open_requested", self._on_memory_open_requested)
        self.ui.on_event("ui.memory.add", self._on_memory_add)
        self.ui.on_event("ui.memory.update", self._on_memory_update)
        self.ui.on_event("ui.memory.delete", self._on_memory_delete)
        self.ui.on_event("ui.settings.open_requested", self._on_settings_open_requested)
        self.ui.on_event("ui.addons.open_requested", self._on_addons_open_requested)
        self.ui.on_event("ui.runtime_status.open_requested", self._on_runtime_status_open_requested)
        self.ui.on_event("ui.runtime_status.opened", self._on_runtime_status_opened)
        self.ui.on_event("ui.runtime_status.closed", self._on_runtime_status_closed)
        self.ui.on_event("ui.log.event", self._on_ui_log_event)
        self.ui.on_event("ui.addons.run_action", self._on_addons_run_action)
        self.ui.on_event("ui.addons.set_enabled", self._on_addons_set_enabled)
        self.ui.on_event("ui.addons.set_action_enabled", self._on_addons_set_action_enabled)
        self.ui.on_event("ui.addons.set_setting", self._on_addons_set_setting)
        self.ui.on_event("ui.addons.approve", self._on_addons_approve)
        self.ui.on_event("ui.addons.repair_environment", self._on_addons_repair_environment)
        self.ui.on_event("ui.addons.install_archive", self._on_addons_install_archive)
        self.ui.on_event("ui.addons.install_folder", self._on_addons_install_folder)
        self.ui.on_event("ui.agent.task_requested", self._on_agent_task_requested)
        self.ui.on_event("ui.agent.history_requested", self._on_agent_history_requested)
        self.ui.on_event("ui.agent.run_requested", self._on_agent_run_requested)
        self.ui.on_event("ui.agent.cancel_requested", self._on_agent_cancel_requested)
        self.ui.on_event("ui.agent.pause_requested", self._on_agent_pause_requested)
        self.ui.on_event("ui.agent.resume_requested", self._on_agent_resume_requested)
        self.ui.on_event("ui.agent.nudge", self._on_agent_nudge)
        self.ui.on_event("ui.agent.permissions", self._on_agent_permissions)
        self.ui.on_event("ui.agent.approval.respond", self._on_agent_approval_respond)
        self.ui.on_event("ui.agent.history.refresh", self._on_agent_history_refresh)
        self.ui.on_event("ui.agent.history.read", self._on_agent_history_read)
        self.ui.on_event("ui.agent.history.retry", self._on_agent_history_retry)
        self.ui.on_event("ui.agent.history.continue", self._on_agent_history_continue)
        self.ui.on_event("ui.settings.applied", self._on_settings_applied)
        self.ui.on_event("ui.health.requested", self._on_health_requested)
        self.ui.on_event("ui.bubble.speed", self._on_bubble_speed)
        self.ui.on_event("ui.bubble.stop", self._on_bubble_stop)
        self.ui.on_event("ui.rewrite.undo", self._on_rewrite_undo)
        self.brain.on_event("reply.chunk", self._on_reply_chunk)
        self.brain.on_event("reply.done", self._on_reply_done)
        self.brain.on_event("agent.log", self._forward_agent_event("ui.agent.log"))
        self.brain.on_event("agent.trace", self._forward_agent_event("ui.agent.trace"))
        self.brain.on_event("agent.done", self._forward_agent_event("ui.agent.done"))
        self.brain.on_event("agent.approval.request", self._on_agent_approval_request)
        self.brain.on_event("addons.changed", self._on_addons_changed)
        self.audio.on_event("audio.warmup.started", self._on_audio_warmup_started)
        self.audio.on_event("audio.warmup.progress", self._on_audio_warmup_progress)
        self.audio.on_event("audio.warmup.done", self._on_audio_warmup_done)
        self.audio.on_event("audio.playback.started", self._on_audio_playback_started)
        self.audio.on_event("audio.playback.amplitude", self._on_audio_playback_amplitude)
        self.audio.on_event("audio.playback.done", self._on_audio_playback_done)
        self.audio.on_event("audio.live.state", self._on_audio_live_state)
        self.audio.on_event("audio.live.amplitude", self._on_audio_playback_amplitude)
        self.audio.on_event("audio.live.transcript", self._on_audio_live_transcript)
        self.audio.on_event("audio.live.error", self._on_audio_live_error)
        self.audio.on_event("audio.live.ended", self._on_audio_live_ended)
        # A live voice session dies with the audio worker; clean up the toggle
        # state so the hotkey works again after the worker restarts. Guarded:
        # test FakeWorkers don't implement on_exit.
        audio_on_exit = getattr(self.audio, "on_exit", None)
        if callable(audio_on_exit):
            audio_on_exit(self._on_audio_worker_exit)
        addon_snapshot = self._safe_call(
            self.brain,
            "brain.addons.ready",
            {"timeout_seconds": 3.0},
            timeout=5.0,
        )
        if isinstance(addon_snapshot, dict) and not addon_snapshot.get("ready", True):
            error = str(addon_snapshot.get("error") or "").strip()
            if error:
                log.warning("addon startup failed: %s", error)
            else:
                log.info("addons are still loading; the tray will update when they are ready")
        usable_addon_snapshot = (
            addon_snapshot
            if isinstance(addon_snapshot, dict) and "addons" in addon_snapshot
            else {"addons": []}
        )
        addon_tray_actions = self._load_addon_tray_actions(usable_addon_snapshot)
        self._addon_tray_actions_snapshot = self._addon_action_key(addon_tray_actions)
        self.ui.call(
            "ui.show_overlay",
            {"addon_tray_actions": addon_tray_actions},
            timeout=30.0,
        )
        if prewarm:
            try:
                self.ui.call("ui.prewarm_intent", timeout=30.0, wait=False)
            except Exception:
                log.exception("intent prewarm did not start")
            self._prewarm_privacy()
            self._prewarm_harness()
            default_caller = self._caller(0) or _all_context_off_policy()
            self._prewarm_llm_prefix(default_caller, route_kind="query")
            self._prewarm_llm_prefix(default_caller, route_kind="chat")
            try:
                self.audio.call("audio.prewarm", timeout=30.0, wait=False)
            except Exception:
                log.exception("audio prewarm did not start")
        # Surface results that detached installers (staged applies, model
        # downloads) wrote while OpenWand was closed, right at startup.
        try:
            self.runtime_log.ingest_installer_statuses()
        except Exception:  # noqa: BLE001 - installer status files are best-effort
            log.exception("could not ingest installer status files")

    def stop(self) -> None:
        """Stop supervisor-owned background activity before worker teardown."""
        self.runtime_log.disable_publishing()
        with self._speech_warmup_lock:
            self._speech_warmup_shutdown = True
            self._speech_warmup_stop.set()
            self._speech_warmup_generation += 1

    def start_hotkeys(self) -> dict[str, Any]:
        """Start hotkeys."""
        addon_hotkeys = self._addon_hotkeys()
        result = self.native.call("native.hotkeys.start", {"addon_hotkeys": addon_hotkeys}, timeout=10.0) or {}
        if not isinstance(result, dict):
            result = {"started": False, "reason": "unexpected native response"}
        if not result.get("started"):
            reason = str(result.get("reason") or result.get("error") or "unknown error")
            log.warning("native hotkeys did not start: %s", reason)
            self._notice("Global hotkeys did not start. Click the OpenWand icon to summon it.", severity="warning")
        self._show_addon_notifications()
        return result

    # -- event handlers ------------------------------------------------

    def _on_native_hotkey(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle native hotkey events."""
        kind = (data or {}).get("kind")
        if self._settings_dialog_is_open():
            log.info("hotkey ignored while Settings is open: kind=%s", kind)
            return
        if kind == "caller":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.begin_caller, int((data or {}).get("index") or 0), time.time_ns())
        elif kind == "snip":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.begin_snip)
        elif kind == "add_context":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.add_context)
        elif kind == "clear_context":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.clear_context)
        elif kind == "read_selection_aloud":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.read_selection_aloud)
        elif kind == "voice_start":
            if self._claim_voice_start():
                log.info("hotkey received: kind=%s", kind)
                self._schedule(self.voice_start)
        elif kind == "voice_stop":
            if self._claim_voice_stop():
                log.info("hotkey received: kind=%s", kind)
                self._schedule(self.voice_stop)
        elif kind == "voice_live":
            action = self._claim_live_voice_toggle()
            if action == "start":
                log.info("hotkey received: kind=%s action=start", kind)
                self._schedule(self.live_voice_start)
            elif action == "stop":
                log.info("hotkey received: kind=%s action=stop", kind)
                self._schedule(self.live_voice_stop)
        elif kind == "dictate_start":
            if self._claim_dictate_start():
                log.info("hotkey received: kind=%s", kind)
                self._schedule(self.dictate_start)
        elif kind == "dictate_stop":
            if self._claim_dictate_stop():
                log.info("hotkey received: kind=%s", kind)
                self._schedule(self.dictate_stop)
        elif kind == "addon":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.addon_run_hotkey, data or {})

    def _on_summon_caller(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle summon caller events."""
        self._schedule(self.begin_caller, int((data or {}).get("caller_idx") or 0), time.time_ns())

    def _on_request_snip(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle request snip events."""
        self._schedule(self.begin_snip)

    def _on_intent_chosen(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle intent chosen events."""
        prompt = str((data or {}).get("custom") or (data or {}).get("prompt") or "").strip()
        choices = list((data or {}).get("context_choices") or [])
        tool_choices = list((data or {}).get("tool_choices") or [])
        routing = (
            dict((data or {}).get("intent_routing") or {})
            if isinstance((data or {}).get("intent_routing"), dict)
            else {}
        )
        conversation_choice = (
            dict((data or {}).get("conversation_choice") or {})
            if isinstance((data or {}).get("conversation_choice"), dict)
            else {}
        )
        self._schedule(
            self.intent_chosen,
            prompt,
            choices,
            routing,
            conversation_choice,
            tool_choices,
        )

    def _on_intent_cancelled(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle intent cancelled events."""
        with self._lock:
            pending_capture = dict(self._pending_context_capture or {})
        if pending_capture.get("surface") == "intent":
            return
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is not None:
            pending.context_ready.set()
        self._new_generation()
        self._set_idle()

    def _on_rewrite_undo(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Restore the text replaced by the most recent OpenWand rewrite."""
        self._schedule(self.undo_last_openwand_edit)

    def _on_rewrite_annotation_submitted(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Generate a delayed-edit proposal from one Rewrite comment popup."""
        payload = data or {}
        self._schedule(
            self.submit_rewrite_annotation,
            str(payload.get("annotation_id") or ""),
            str(payload.get("comment") or ""),
            bool(payload.get("include_document")),
        )

    def _on_rewrite_annotation_held(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Save one comment without starting a model request."""
        payload = data or {}
        self._schedule(
            self.hold_rewrite_annotation,
            str(payload.get("annotation_id") or ""),
            str(payload.get("comment") or ""),
            bool(payload.get("include_document")),
        )

    def _on_rewrite_send_all(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Dispatch all held comments while preserving per-app conversations."""
        self._schedule(self.send_all_rewrite_annotations)

    def _on_rewrite_annotation_cancelled(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Cancel an in-flight proposal and discard its popup state."""
        self._schedule(self.cancel_rewrite_annotation, str((data or {}).get("annotation_id") or ""))

    def _on_rewrite_annotation_accepted(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Immediately apply or copy an accepted delayed-edit proposal."""
        self._schedule(self.accept_rewrite_annotation, str((data or {}).get("annotation_id") or ""))

    def _on_rewrite_annotation_declined(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Forget a proposal the user declined."""
        self._schedule(self.decline_rewrite_annotation, str((data or {}).get("annotation_id") or ""))

    def _on_rewrite_annotation_revision_requested(
        self,
        data: dict[str, Any],
        _req_id: Any = None,
    ) -> None:
        """Regenerate one proposal using follow-up feedback from its popup."""
        payload = data or {}
        self._schedule(
            self.revise_rewrite_annotation,
            str(payload.get("annotation_id") or ""),
            str(payload.get("prompt") or ""),
        )

    def _on_rewrite_anchor_refresh_requested(
        self,
        data: dict[str, Any],
        _req_id: Any = None,
    ) -> None:
        """Refresh a cached exact range after its source document scrolls."""
        key = str((data or {}).get("annotation_id") or "")
        if not key:
            return
        with self._lock:
            if key not in self._rewrite_annotations or key in self._rewrite_anchor_refreshing:
                return
            self._rewrite_anchor_refreshing.add(key)
        self._schedule(self.refresh_rewrite_annotation_anchor, key)

    def _on_intent_snip_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle screenshot-chip snip requests from an open intent picker."""
        choices = list((data or {}).get("context_choices") or [])
        tool_choices = list((data or {}).get("tool_choices") or [])
        custom_text = str((data or {}).get("custom_text") or "")
        self._schedule(self.intent_snip_requested, choices, custom_text, tool_choices)

    def _on_intent_snip_region(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle a selected screenshot-chip snip region."""
        self._schedule(self.intent_snip_region_selected, data or {})

    def _on_intent_snip_cancelled(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle cancellation of a screenshot-chip snip."""
        self._schedule(self.intent_snip_cancelled)

    def _on_intent_selection_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle selection capture requests from an open intent picker."""
        choices = list((data or {}).get("context_choices") or [])
        tool_choices = list((data or {}).get("tool_choices") or [])
        custom_text = str((data or {}).get("custom_text") or "")
        self._schedule(self.intent_selection_capture_requested, choices, custom_text, tool_choices)

    def _on_intent_context_remove(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle per-row context removals from an open intent picker."""
        item_id = str((data or {}).get("id") or "")
        source_id = str((data or {}).get("source_id") or "")
        if item_id:
            self._schedule(self.intent_context_source_removed, item_id, source_id)

    def _on_intent_context_reenabled(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle context groups toggled back on after per-row removals."""
        item_id = str((data or {}).get("id") or "")
        choices = list((data or {}).get("context_choices") or [])
        if item_id:
            self._schedule(self.intent_context_source_reenabled, item_id, choices)

    def _on_chat_snip_region(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle a selected chat screenshot snip region."""
        self._schedule(self.chat_snip_region_selected, data or {})

    def _on_chat_snip_cancelled(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle cancellation of a chat screenshot snip."""
        self._schedule(self.chat_snip_cancelled)

    def _on_chat_selection_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle selection capture requests from the chat window."""
        self._schedule(self.chat_selection_capture_requested)

    def _on_snip_region(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle snip region events."""
        self._schedule(self.snip_region_selected, data or {})

    def _on_snip_cancelled(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle snip cancelled events."""
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is not None:
            pending.context_ready.set()
        self._new_generation()
        self._set_idle()

    def _on_context_dropped(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle context dropped events."""
        # Keep clipboard/drop ordering deterministic: a user can paste and hit
        # Enter immediately, and the following intent-chosen event must see the
        # newly attached context.
        self.context_items_dropped(list((data or {}).get("items") or []))

    def _on_context_remove(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle context remove events."""
        self._schedule(self.remove_context_item, int((data or {}).get("index") or 0))

    def _on_chat_request(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle chat request events."""
        self._schedule(self.chat_request, data or {})

    def _on_chat_context_preview(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle chat context preview events."""
        self._schedule(self.chat_context_preview, data or {})

    def _on_chat_message_actions_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Refresh addon message actions when Chat opens or addons change."""
        self._schedule(self.chat_message_actions, data or {})

    def _on_chat_message_action_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Run one user-requested addon action away from the UI thread."""
        self._schedule(self.addon_run_message_action, data or {})

    def _on_memory_open_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle memory open requested events."""
        self._schedule(self.open_memory)

    def _on_memory_add(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle memory add events."""
        self._schedule(self.memory_add, data or {})

    def _on_memory_update(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle memory update events."""
        self._schedule(self.memory_update, data or {})

    def _on_memory_delete(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle memory delete events."""
        self._schedule(self.memory_delete, data or {})

    def _on_settings_open_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle settings open requested events."""
        self._schedule(self.open_settings, str((data or {}).get("initial_page") or ""))

    def _on_addons_open_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons open requested events."""
        self._schedule(self.open_addons, str((data or {}).get("addon_id") or ""))

    def _on_runtime_status_open_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle runtime status open requested events."""
        self._schedule(self.open_runtime_status)

    def _on_runtime_status_opened(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Start live-publishing runtime events while the window is open."""
        self.runtime_log.enable_publishing()

    def _on_runtime_status_closed(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Stop live-publishing runtime events once the window closes."""
        self.runtime_log.disable_publishing()

    def _on_ui_log_event(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Record a structured log event reported by the UI worker."""
        payload = data if isinstance(data, dict) else {}
        title = str(payload.get("title") or "").strip()
        if not title:
            return
        self.runtime_log.append(
            str(payload.get("source") or "ui").strip()[:24] or "ui",
            normalize_severity(str(payload.get("severity") or "")),
            title,
            detail=str(payload.get("detail") or ""),
        )

    def _on_addons_run_action(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons run action events."""
        self._schedule(self.addon_run_action, data or {})

    def _on_addons_set_enabled(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons set enabled events."""
        self._schedule(self.addon_set_enabled, data or {})

    def _on_addons_set_action_enabled(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addon action toggle events."""
        self._schedule(self.addon_set_action_enabled, data or {})

    def _on_addons_set_setting(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons set setting events."""
        self._schedule(self.addon_set_setting, data or {})

    def _on_addons_repair_environment(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons repair environment events."""
        self._schedule(self.addon_repair_environment, data or {})

    def _on_addons_approve(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addon trust and access approval events."""
        self._schedule(self.addon_approve, data or {})

    def _on_addons_install_archive(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons install archive events."""
        self._schedule(self.addon_install_archive, data or {})

    def _on_addons_install_folder(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons install folder events."""
        self._schedule(self.addon_install_folder, data or {})

    def _on_addons_changed(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Apply a brain-published addon snapshot to the already-visible tray."""
        self._schedule(self._apply_addon_change, data or {})

    def _on_agent_task_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent task requested events."""
        self._schedule(self.open_agent_task)

    def _on_agent_history_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent history requested events."""
        self._schedule(self.open_agent_history)

    def _on_agent_run_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent run requested events."""
        self._schedule(self.run_agent_task, dict((data or {}).get("spec") or {}))

    def _on_agent_cancel_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent cancel requested events."""
        self._schedule(self.cancel_agent_task)

    def _on_agent_pause_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent pause requested events."""
        self._schedule(self.control_agent_task, {"action": "pause"})

    def _on_agent_resume_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent resume requested events."""
        self._schedule(self.control_agent_task, {"action": "resume"})

    def _on_agent_nudge(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent nudge events."""
        payload = data or {}
        self._schedule(
            self.control_agent_task,
            {
                "action": "nudge",
                "target_agent": str(payload.get("target_agent") or payload.get("to") or "ALL"),
                "message": str(payload.get("message") or ""),
            },
        )

    def _on_agent_permissions(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle live agent permission updates."""
        self._schedule(
            self.control_agent_task,
            {"action": "permissions", "permission_modes": dict((data or {}).get("permission_modes") or {})},
        )

    def _on_agent_approval_respond(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent approval respond events."""
        self._schedule(self.respond_agent_approval, data or {})

    def _on_agent_history_refresh(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent history refresh events."""
        self._schedule(self.open_agent_history)

    def _on_agent_history_read(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent history read events."""
        self._schedule(self.read_agent_history, str((data or {}).get("run_dir") or ""))

    def _on_agent_history_retry(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent history retry events."""
        self._schedule(self.retry_agent_history, str((data or {}).get("run_dir") or ""))

    def _on_agent_history_continue(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent history continue events."""
        self._schedule(self.continue_agent_history, str((data or {}).get("run_dir") or ""))

    def _on_settings_applied(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle settings applied events."""
        changed_keys = None
        if isinstance(data, dict) and "changed_keys" in data:
            changed_keys = [str(key) for key in (data.get("changed_keys") or [])]
        self._schedule(self.reload_settings, changed_keys)

    def _on_audio_warmup_started(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Start one supervisor-owned, consistently timed speech notice."""
        items = {str(item) for item in ((data or {}).get("items") or []) if str(item) in {"stt", "tts"}}
        if not items:
            return
        now = time.monotonic()
        with self._speech_warmup_lock:
            if self._speech_warmup_shutdown:
                return
            self._speech_warmup_stop.set()
            self._speech_warmup_stop = threading.Event()
            stop_event = self._speech_warmup_stop
            self._speech_warmup_generation += 1
            generation = self._speech_warmup_generation
            self._speech_warmup_started_at = now
            self._speech_warmup_provider = str((data or {}).get("provider") or "").strip().lower()
            self._speech_warmup_id = str((data or {}).get("warmup_id") or "")
            self._speech_warmup_states = {
                item: {"status": "waiting", "started_at": None, "detail": ""}
                for item in sorted(items)
            }
        self._show_speech_warmup_notice(generation)
        threading.Thread(
            target=self._speech_warmup_notice_loop,
            args=(generation, stop_event),
            daemon=True,
            name="speech-warmup-notice",
        ).start()

    def _on_audio_warmup_progress(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Track per-component progress; one supervisor timer renders it."""
        item = str((data or {}).get("item") or "")
        status = str((data or {}).get("status") or "")
        if item not in {"stt", "tts"} or not status:
            return
        warmup_id = str((data or {}).get("warmup_id") or "")
        now = time.monotonic()
        terminal = False
        with self._speech_warmup_lock:
            if warmup_id and self._speech_warmup_id and warmup_id != self._speech_warmup_id:
                return
            warmup_active = (
                self._speech_warmup_started_at > 0
                and not self._speech_warmup_stop.is_set()
                and not self._speech_warmup_shutdown
            )
            if not warmup_active:
                return
            generation = self._speech_warmup_generation
            state = self._speech_warmup_states.setdefault(
                item,
                {"status": "waiting", "started_at": None, "detail": ""},
            )
            if status == "started" or status.startswith("preparing "):
                state["status"] = "warming"
                state["started_at"] = state.get("started_at") or now
                state["detail"] = ""
            elif status == "ok":
                state["status"] = "ready"
                state["detail"] = ""
                terminal = True
            elif status == "skipped":
                state["status"] = "skipped"
                state["detail"] = ""
                terminal = True
            elif status == "stopped":
                state["status"] = "stopped"
                state["detail"] = ""
                terminal = True
            elif status.startswith("error:"):
                state["status"] = "deferred" if item == "tts" and _is_transient_local_tts_warmup_error(status) else "failed"
                state["detail"] = status.removeprefix("error:").strip()
                terminal = True
        if terminal and warmup_active:
            self._show_speech_warmup_notice(generation)

    def _on_audio_warmup_done(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Stop the timer and replace it with a complete per-item result."""
        items = {str(item) for item in ((data or {}).get("items") or []) if str(item) in {"stt", "tts"}}
        if not items:
            return
        provider = str((data or {}).get("provider") or "").strip().lower()
        result = (data or {}).get("result") if isinstance((data or {}).get("result"), dict) else {}
        warmup_id = str((data or {}).get("warmup_id") or "")
        with self._speech_warmup_lock:
            if warmup_id and self._speech_warmup_id and warmup_id != self._speech_warmup_id:
                return
            self._speech_warmup_stop.set()
            self._speech_warmup_generation += 1
            generation = self._speech_warmup_generation
            self._speech_warmup_provider = provider or self._speech_warmup_provider
            for item in items:
                raw_status = str(result.get(item) or ("ok" if not result else "skipped"))
                state = self._speech_warmup_states.setdefault(
                    item,
                    {"status": "waiting", "started_at": None, "detail": ""},
                )
                if raw_status == "ok":
                    state["status"] = "ready"
                    state["detail"] = ""
                elif raw_status == "skipped":
                    state["status"] = "skipped"
                    state["detail"] = ""
                elif raw_status == "stopped":
                    state["status"] = "stopped"
                    state["detail"] = ""
                elif raw_status.startswith("error:"):
                    transient = item == "tts" and _is_transient_local_tts_warmup_error(raw_status)
                    state["status"] = "deferred" if transient else "failed"
                    state["detail"] = raw_status.removeprefix("error:").strip()
            states = {name: dict(state) for name, state in self._speech_warmup_states.items()}
        has_failure = any(state.get("status") == "failed" for state in states.values())
        has_deferred = any(state.get("status") == "deferred" for state in states.values())
        if has_failure:
            heading = "Speech warm-up failed."
        elif has_deferred:
            heading = "Speech warm-up finished; one service will retry when needed."
        else:
            heading = "Speech services are ready."
        lines = [heading, *self._speech_warmup_state_lines(states, provider=provider, final=True)]
        params: dict[str, Any] = {
            "text": "\n".join(lines),
            "timeout_ms": 8000 if has_failure or has_deferred else 6000,
            "key": "audio-warmup",
        }
        if has_failure:
            params["severity"] = "error"
        elif has_deferred:
            params["severity"] = "warning"
        self._send_speech_warmup_notice(params, generation)

    def _speech_warmup_notice_loop(self, generation: int, stop_event: threading.Event) -> None:
        """Refresh one keyed notice on a fixed cadence outside the audio worker."""
        while not stop_event.wait(_SPEECH_WARMUP_NOTICE_INTERVAL_SECONDS):
            if not self._show_speech_warmup_notice(generation):
                return

    def _show_speech_warmup_notice(self, generation: int) -> bool:
        """Send one current timer frame, ordered against terminal frames."""
        with self._speech_warmup_notice_lock:
            with self._speech_warmup_lock:
                if (
                    generation != self._speech_warmup_generation
                    or self._speech_warmup_stop.is_set()
                    or self._speech_warmup_shutdown
                    or not self._speech_warmup_states
                ):
                    return False
                states = {
                    name: dict(state)
                    for name, state in self._speech_warmup_states.items()
                }
                provider = self._speech_warmup_provider
                elapsed = max(0, int(time.monotonic() - self._speech_warmup_started_at))
            text = "\n".join(
                [
                    f"Preparing speech services - {self._speech_elapsed_text(elapsed)} elapsed.",
                    *self._speech_warmup_state_lines(states, provider=provider, final=False),
                ]
            )
            # This is cosmetic and periodic. Do not make lifecycle/event threads
            # wait for the UI process on any operating system.
            self._fire(
                self.ui,
                "ui.reply.notice",
                {"text": text, "timeout_ms": 0, "key": "audio-warmup"},
            )
            return True

    def _send_speech_warmup_notice(
        self,
        params: dict[str, Any],
        generation: int,
    ) -> bool:
        """Order a terminal notice after timers and reject obsolete results."""
        with self._speech_warmup_notice_lock:
            with self._speech_warmup_lock:
                if generation != self._speech_warmup_generation:
                    return False
            self._fire(self.ui, "ui.reply.notice", params)
            return True

    @staticmethod
    def _speech_elapsed_text(elapsed_seconds: int) -> str:
        minutes, seconds = divmod(max(0, int(elapsed_seconds)), 60)
        return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"

    @staticmethod
    def _speech_item_label(item: str, provider: str) -> str:
        if item == "stt":
            return "STT (speech recognition)"
        if provider == "kokoro":
            return "TTS (Kokoro local voice)"
        if provider == "cartesia":
            return "TTS (Cartesia connection)"
        return f"TTS ({provider})" if provider and provider != "none" else "TTS"

    def _speech_warmup_state_lines(
        self,
        states: dict[str, dict[str, Any]],
        *,
        provider: str,
        final: bool,
    ) -> list[str]:
        now = time.monotonic()
        lines: list[str] = []
        for item in ("stt", "tts"):
            if item not in states:
                continue
            state = states[item]
            status = str(state.get("status") or "waiting")
            label = self._speech_item_label(item, provider)
            if status == "warming":
                started_at = float(state.get("started_at") or self._speech_warmup_started_at or now)
                value = f"warming up ({self._speech_elapsed_text(int(now - started_at))})"
            elif status == "ready":
                value = "ready"
            elif status == "skipped":
                value = "not needed"
            elif status == "deferred":
                value = "will retry when first used"
            elif status == "failed":
                detail = str(state.get("detail") or "unknown error")
                value = f"failed - {detail}"
            elif status == "stopped":
                value = "stopped"
            else:
                value = "waiting to start" if not final else "not completed"
            lines.append(f"{label}: {value}")
        return lines

    def _on_bubble_speed(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle bubble speed events."""
        self._safe_call(
            self.audio,
            "audio.speed_boost",
            {"enabled": bool((data or {}).get("enabled"))},
            timeout=5.0,
        )

    def _on_bubble_stop(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Stop the visible reply bubble and any speech for the current answer."""
        self._schedule(self.stop_reply_bubble)

    def _on_audio_playback_started(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle audio playback started events."""
        self._safe_call(self.ui, "ui.overlay.state", {"state": "speaking"}, timeout=30.0)
        self._safe_call(self.ui, "ui.reply.start_reveal", timeout=30.0)

    def _on_audio_playback_amplitude(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Forward the PCM level without blocking audio playback on the UI."""
        self._fire(
            self.ui,
            "ui.overlay.amplitude",
            {"amplitude": float((data or {}).get("amplitude") or 0.0)},
        )

    def _on_audio_playback_done(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle audio playback done events."""
        if self._tts_sequence_is_active():
            return
        self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
        self._set_idle()

    def _on_audio_live_state(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Drive the overlay doll from live voice session state."""
        state = str((data or {}).get("state") or "")
        overlay = {
            "connecting": "thinking",
            "listening": "listening",
            "speaking": "speaking",
        }.get(state)
        if overlay and self._live_voice_busy():
            self._fire(self.ui, "ui.overlay.state", {"state": overlay})
        if state == "listening" and self._live_voice_busy() and not self._live_voice_ready_notified:
            # First listening state = the Gemini websocket is connected and the
            # mic is streaming; tell the user the conversation is actually live.
            self._live_voice_ready_notified = True
            self._fire(self.ui, "ui.live_voice.ready", {})

    def _on_audio_live_transcript(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle live voice transcript events."""
        self._live_transcript_sink(data or {})

    def _live_transcript_sink(self, payload: dict[str, Any]) -> None:
        """Forward one live transcript fragment to the bubble captions."""
        role = str(payload.get("role") or "")
        text = str(payload.get("text") or "")
        if role in ("user", "assistant") and text:
            self._fire(self.ui, "ui.live_voice.transcript", {"role": role, "text": text})

    def _on_audio_live_error(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle advisory live voice errors (session keeps running or is ending)."""
        code = str((data or {}).get("code") or "")
        message = str((data or {}).get("message") or "")
        if code == "expiring":
            self._notice(t("Live voice session will end soon (server time limit)."))
            return
        self._notice(
            f"{t('Live voice error')}: {self._friendly_error(message or code or 'unknown error')}",
            severity="warning",
        )

    def _on_audio_live_ended(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle the (exactly-once) end of a live voice session."""
        if not self._live_voice_busy():
            return  # already cleaned up by live_voice_stop or worker-exit handling
        reason = str((data or {}).get("reason") or "")
        self._mark_live_voice_idle()
        self._fire(self.ui, "ui.live_voice.session", {"active": False})
        self._set_idle()
        if reason == "server_closed":
            self._notice(t("Live voice session ended (server time limit). Press the hotkey to start again."))

    def _on_audio_worker_exit(self, _returncode: int | None = None) -> None:
        """The audio worker died; finish any state owned by that process."""
        with self._speech_warmup_lock:
            warmup_active = (
                self._speech_warmup_started_at > 0
                and not self._speech_warmup_stop.is_set()
            )
            if warmup_active:
                self._speech_warmup_stop.set()
                self._speech_warmup_generation += 1
                generation = self._speech_warmup_generation
                self._speech_warmup_id = f"interrupted:{self._speech_warmup_id}"
                for state in self._speech_warmup_states.values():
                    if state.get("status") in {"waiting", "warming"}:
                        state["status"] = "stopped"
                states = {
                    name: dict(state)
                    for name, state in self._speech_warmup_states.items()
                }
                provider = self._speech_warmup_provider
            else:
                states = {}
                provider = ""
                generation = self._speech_warmup_generation
        if warmup_active:
            self._send_speech_warmup_notice(
                {
                    "text": "\n".join(
                        [
                            "Speech warm-up was interrupted because the audio service restarted.",
                            *self._speech_warmup_state_lines(
                                states,
                                provider=provider,
                                final=True,
                            ),
                        ]
                    ),
                    "timeout_ms": 8000,
                    "key": "audio-warmup",
                    "severity": "warning",
                },
                generation,
            )
        if not self._live_voice_busy():
            return
        self._mark_live_voice_idle()
        self._fire(self.ui, "ui.live_voice.session", {"active": False})
        self._set_idle()
        self._notice(t("Live voice stopped because the audio worker restarted."), severity="warning")

    def _on_reply_chunk(
        self,
        data: dict[str, Any],
        _req_id: Any = None,
        *,
        thought_parser: Any = _USE_SHARED_REPLY_PARSER,
    ) -> list[tuple[str, bool, bool]]:
        """Handle reply chunk events."""
        text = str((data or {}).get("text") or "")
        if not text:
            return []
        is_progress = bool((data or {}).get("is_progress"))
        payload_is_thought = bool((data or {}).get("is_thought"))
        annotations = list((data or {}).get("annotations") or []) if isinstance(data, dict) else []
        if payload_is_thought:
            self._safe_call(
                self.ui,
                "ui.reply.chunk",
                {
                    "text": text,
                    "is_thought": True,
                    "is_progress": is_progress,
                },
                timeout=30.0,
            )
            return [(text, True, is_progress)]
        parser = self._reply_thought_parser if thought_parser is _USE_SHARED_REPLY_PARSER else thought_parser
        if parser is None:
            self._safe_call(
                self.ui,
                "ui.reply.chunk",
                {"text": text, "is_progress": is_progress, "annotations": annotations},
                timeout=30.0,
            )
            return [(text, False, is_progress)]
        segments = list(parser.feed(text))
        passthrough_annotations = annotations if len(segments) == 1 and segments[0] == (text, False) else []
        for segment, is_thought in segments:
            if segment:
                chunk_payload: dict[str, Any] = {
                    "text": segment,
                    "is_thought": bool(is_thought),
                    "is_progress": is_progress,
                }
                if passthrough_annotations and not is_thought:
                    chunk_payload["annotations"] = passthrough_annotations
                self._safe_call(
                    self.ui,
                    "ui.reply.chunk",
                    chunk_payload,
                    timeout=30.0,
                )
        return [(segment, bool(is_thought), is_progress) for segment, is_thought in segments if segment]

    def _replace_reply_text(self, text: str) -> None:
        """Replace streamed reply chunks with the final assistant text."""
        if not text:
            return
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        try:
            from core.assistant_text import ThoughtStreamParser

            parser = ThoughtStreamParser()
        except Exception:
            self._safe_call(self.ui, "ui.reply.chunk", {"text": text}, timeout=30.0)
            return
        for segment, is_thought in list(parser.feed(text)) + list(parser.finish()):
            if segment:
                self._safe_call(
                    self.ui,
                    "ui.reply.chunk",
                    {"text": segment, "is_thought": bool(is_thought)},
                    timeout=30.0,
                )

    def _on_reply_done(
        self,
        data: dict[str, Any],
        _req_id: Any = None,
        *,
        thought_parser: Any = _USE_SHARED_REPLY_PARSER,
    ) -> None:
        """Handle reply done events."""
        use_shared_parser = thought_parser is _USE_SHARED_REPLY_PARSER
        parser = self._reply_thought_parser if use_shared_parser else thought_parser
        if parser is not None:
            for segment, is_thought in parser.finish():
                if segment:
                    self._safe_call(
                        self.ui,
                        "ui.reply.chunk",
                        {"text": segment, "is_thought": bool(is_thought)},
                        timeout=30.0,
                    )
            if use_shared_parser:
                self._reply_thought_parser = None
        text = str((data or {}).get("text") or "")
        if text:
            self._last_reply = text
        # flush=False: the LLM finished streaming but no audio will pace the
        # bubble (this path only runs with TTS off) - let the WPM reveal drain
        # at BUBBLE_REVEAL_WPM instead of slamming the full reply in at once.
        self._safe_call(self.ui, "ui.reply.done", {"flush": False}, timeout=30.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "idle"}, timeout=30.0)

    def _forward_agent_event(self, event_name: str):
        """Handle forward agent event for flow controller."""
        def forward(data: Any, _req_id: Any = None) -> None:
            """Handle forward for flow controller."""
            log.debug("%s: %s", event_name, data)
            self._safe_call(self.ui, event_name, {"data": data}, timeout=30.0)

        return forward

    def _on_agent_approval_request(self, data: Any, _req_id: Any = None) -> None:
        """Handle agent approval request events."""
        action = ""
        detail = ""
        if isinstance(data, dict):
            action = str(data.get("action") or "approval")
            detail = str(data.get("detail") or data.get("reason") or "")
        text = f"Agent needs permission: {action}"
        if detail:
            text += f"\n{detail}"
        self._safe_call(
            self.ui,
            "ui.agent.notify_approval",
            {"text": text, "resolved": False, "data": data},
            timeout=30.0,
        )

    # -- public product actions ---------------------------------------

    @staticmethod
    def _action_provider_picker_context(context: dict[str, Any] | None) -> dict[str, Any]:
        """Detect the app action provider from the hotkey-time context snapshot."""
        try:
            from core.actions.providers import detected_picker_context

            return detected_picker_context(context)
        except Exception:
            log.exception("action provider detection failed")
            return {}

    def begin_caller(self, caller_idx: int = 0, invoked_at_unix_ns: int = 0) -> None:
        """Handle begin caller for flow controller."""
        import time

        t0 = time.monotonic()
        invoked_at_unix_ns = int(invoked_at_unix_ns or time.time_ns())
        self._reload_supervisor_config_if_changed()
        caller = self._caller(caller_idx)
        self._log_caller_runtime(caller_idx, caller)
        if caller.get("paste_back"):
            self.begin_rewrite_annotation(caller_idx, caller)
            return
        self._prewarm_llm_prefix(caller, route_kind="query")
        generation = self._new_generation()
        # Silence any in-progress speech, but don't block the picker waiting for
        # it - audio.stop just flips a flag in the audio worker.
        self._fire(self.audio, "audio.stop")
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})
        # Capture the foreground app, selection, and selected paths before any
        # OpenWand top-level window is shown. Even a nominally non-activating
        # Windows popup can change the focused accessibility element or make a
        # synthetic Copy land in OpenWand instead of the source app.
        initial_context: dict[str, Any] = {}
        try:
            initial_context = self._context_snapshot(
                caller,
                include_browser=False,
                include_selected_paths=True,
                preview_context_sources=True,
                dedupe_selection=True,
            )
        except Exception:
            # Context capture remains best-effort: a denied native permission
            # must not prevent the caller picker from opening.
            log.exception("pre-picker context snapshot failed")
            initial_context = {}
        if not self._is_current(generation):
            return

        # A requested desktop capture also happens before the picker so the
        # screenshot cannot include OpenWand itself. Slow browser/document
        # content remains deferred and is prefetched after the picker appears.
        screenshot_b64 = None
        screenshot_tool_b64 = None
        t_shot0 = time.monotonic()
        if caller.get("context_screenshot") == "auto":
            screenshot_b64 = self._capture_fullscreen_b64()
        elif self._screenshot_tool_allowed(caller):
            screenshot_tool_b64 = self._capture_model_tool_b64()
        t_shot = time.monotonic()
        if not self._is_current(generation):
            return
        pending = PendingInvocation(
            caller_idx=caller_idx,
            caller=caller,
            context=initial_context,
            action_provider_context={},
            screenshot_b64=screenshot_b64,
            screenshot_tool_b64=screenshot_tool_b64,
            invoked_at_unix_ns=invoked_at_unix_ns,
            initial_context_at_unix_ns=time.time_ns(),
        )
        target_id = self._intent_target_id(initial_context)
        pending.intent_target_pid = target_id
        pending.paste_target_pid = target_id if pending.caller.get("paste_back") else 0
        with self._lock:
            self._pending = pending
        # The source-app snapshot is now stable. Keep the initial shell inert
        # only until its app-action provider and deferred metadata are ready.
        self._safe_call(
            self.ui,
            "ui.show_intent",
            {
                "caller_idx": caller_idx,
                "target_hwnd": target_id,
                "context_items": self._intent_context_items(pending),
                "tool_snapshot": self._intent_tool_snapshot(
                    pending.caller,
                    pending.tool_choices,
                ),
                "action_provider": pending.action_provider_context,
                "defer_focus": True,
            },
            timeout=30.0,
        )
        pending.intent_shown_at_unix_ns = time.time_ns()
        t_show = time.monotonic()
        self._schedule(self._collect_initial_intent_context, pending, generation, t0, t_show)
        log.info(
            "caller %d picker shell shown screenshot=%.2fs total=%.2fs",
            caller_idx, t_shot - t_shot0, t_show - t0,
        )

    def begin_rewrite_annotation(self, caller_idx: int, caller: dict[str, Any]) -> None:
        """Capture a real selection and open the replacement Rewrite composer."""
        self._fire(self.audio, "audio.stop")
        try:
            context = self._context_snapshot(
                caller,
                include_browser=False,
                include_selected_paths=False,
                preview_context_sources=False,
            )
        except Exception as exc:  # noqa: BLE001 - selection capture is best-effort
            log.exception("rewrite annotation selection capture failed")
            self._notice(f"Could not read selected text: {self._friendly_error(exc)}", severity="error")
            return
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        log.info(
            "rewrite selection captured: process=%r hwnd=%s chars=%d focus_token=%s",
            active_app.get("process_name"),
            active_app.get("window_id") or 0,
            len(str(context.get("selected_text") or "")),
            context.get("focus_token") or 0,
        )
        try:
            structured_target: dict[str, Any] = {}
            probes = (
                ("code-editor", self._capture_vscode_rewrite_target),
                ("spreadsheet", self._capture_spreadsheet_rewrite_target),
                ("word", self._capture_word_rewrite_target),
                ("powerpoint", self._capture_powerpoint_rewrite_target),
                ("libreoffice", self._capture_libreoffice_rewrite_target),
                ("browser", self._capture_browser_rewrite_target),
            )
            for probe_name, probe in probes:
                probe_started = time.monotonic()
                log.info("rewrite target probe started: %s", probe_name)
                structured_target = probe(context, active_app) or {}
                log.info(
                    "rewrite target probe finished: %s matched=%s elapsed=%.3fs",
                    probe_name,
                    bool(structured_target),
                    time.monotonic() - probe_started,
                )
                if structured_target:
                    break
        except Exception as exc:  # noqa: BLE001 - app adapters fail at a user-visible boundary
            log.warning("structured Rewrite target capture failed: %s", exc)
            self._notice(f"OpenWand couldn't safely read that app selection: {exc}", severity="warning")
            return
        if structured_target:
            context["selected_text"] = str(structured_target["grid_text"])
            if isinstance(structured_target.get("selection_rect"), dict):
                context["selection_rect"] = dict(structured_target["selection_rect"])
        if str(context.get("platform") or "").startswith("win"):
            app_native_rect = (
                dict(structured_target.get("selection_rect") or {})
                if isinstance(structured_target.get("selection_rect"), dict)
                else {}
            )
            anchor = self._safe_call(
                self.native,
                "native.selection.anchor.resolve",
                {
                    "focus_token": int(context.get("focus_token") or 0),
                    "source_window_id": int(active_app.get("window_id") or 0),
                    "app_native_rect": app_native_rect,
                    "allow_mouse": True,
                    "refresh": False,
                },
                timeout=2.0,
            )
            if isinstance(anchor, dict) and anchor.get("ok") and anchor.get("visible"):
                context["selection_rect"] = dict(anchor.get("selection_rect") or {})
                context["selection_anchor_source"] = str(anchor.get("source") or "")
                log.info(
                    "rewrite anchor resolved: source=%s rect=%r",
                    context["selection_anchor_source"],
                    context["selection_rect"],
                )
        selected = str(context.get("selected_text") or "")
        if not selected.strip():
            self._notice("No selected text to rewrite.", severity="warning")
            return

        target_id = self._intent_target_id(context)
        annotation_id = uuid.uuid4().hex
        pending = PendingInvocation(
            caller_idx=int(caller_idx),
            caller=dict(caller),
            context=context,
            paste_target_pid=target_id,
            intent_target_pid=target_id,
        )
        session_key = self._rewrite_annotation_session_key(context)
        with self._lock:
            display_number = self._next_rewrite_display_number()
        platform = str(context.get("platform") or "")
        exact_target = bool(structured_target or context.get("focus_token"))
        # Windows and macOS may edit only a captured native range. Merely
        # knowing the source window is not enough: after the user clicks away,
        # an ordinary paste would land at the new caret instead of replacing
        # the text that Rewrite originally captured.
        safe_in_place = exact_target or bool(
            target_id and not platform.startswith(("win", "darwin"))
        )
        request = RewriteAnnotationRequest(
            annotation_id=annotation_id,
            pending=pending,
            session_key=session_key,
            display_number=display_number,
            copy_only=not safe_in_place,
            structured_target=structured_target,
        )
        with self._lock:
            self._rewrite_annotations[annotation_id] = request
            self._rewrite_app_sessions.setdefault(session_key, {"document_text": "", "turns": []})
        log.info(
            "rewrite composer handoff: annotation=%s display=%d source_hwnd=%s chars=%d",
            annotation_id,
            display_number,
            active_app.get("window_id") or 0,
            len(selected),
        )
        result = self._safe_call(
            self.ui,
            "ui.rewrite.annotation.show",
            {
                "annotation_id": annotation_id,
                "display_number": display_number,
                "selected_text": selected,
                "source_window_id": int(active_app.get("window_id") or 0),
                "source_pid": int(active_app.get("pid") or 0),
                "source_label": str(active_app.get("name") or active_app.get("process_name") or ""),
                "selection_rect": (
                    dict(context.get("selection_rect") or {})
                    if isinstance(context.get("selection_rect"), dict)
                    else {}
                ),
            },
            timeout=30.0,
        )
        log.info("rewrite composer result: annotation=%s result=%r", annotation_id, result)
        if (
            isinstance(result, dict)
            and result.get("shown") is False
            and result.get("created") is not True
        ):
            with self._lock:
                self._rewrite_annotations.pop(annotation_id, None)
            self._release_rewrite_annotation_anchor(request)

    @staticmethod
    def _rewrite_annotation_session_key(context: dict[str, Any]) -> str:
        """Return the per-app conversation key used by concurrent Rewrite popups."""
        active = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        platform = str(context.get("platform") or "")
        pid = int(active.get("pid") or 0)
        process = str(active.get("process_name") or active.get("name") or "app").strip().casefold()
        return f"{platform}:{pid}:{process}"

    def _next_rewrite_display_number(
        self,
        *,
        excluding: RewriteAnnotationRequest | None = None,
    ) -> int:
        """Return the smallest number still needed by unfinished generation work."""
        used_numbers = {
            max(1, int(item.display_number or 1))
            for item in self._rewrite_annotations.values()
            if item is not excluding and item.state in _REWRITE_NUMBER_RESERVED_STATES
        }
        preferred = max(1, int(excluding.display_number or 1)) if excluding else 0
        if preferred and preferred not in used_numbers:
            return preferred
        return next(number for number in itertools.count(1) if number not in used_numbers)

    def _capture_vscode_rewrite_target(
        self,
        context: dict[str, Any],
        active_app: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind a supported saved editor selection to exact file offsets and hash."""
        from core.actions.adapters.vscode import VSCodeSnapshot, code_editor_name, is_code_editor_app

        if not is_code_editor_app(active_app):
            return {}
        selected_text = str(context.get("selected_text") or "")
        if not selected_text.strip():
            return {}
        response = self._safe_call(
            self.native,
            "native.action.vscode.snapshot",
            {"active_app": active_app, "selected_text": selected_text},
            timeout=12.0,
        ) or {}
        payload = response.get("snapshot") if isinstance(response, dict) else None
        if not bool(response.get("ok")) or not isinstance(payload, dict):
            # Unsaved/unidentifiable editors still get the ordinary proposal
            # path; Accept will safely degrade to Copy if Monaco rejects it.
            log.info("code editor exact Rewrite target unavailable: %s", response.get("error"))
            return {}
        payload = dict(payload)
        payload.setdefault("editor_name", code_editor_name(active_app))
        snapshot = VSCodeSnapshot.from_selection(payload)
        return {
            "kind": "vscode_saved_selection",
            "snapshot": snapshot,
            "grid_text": snapshot.selected_text,
            "prompt_context": (
                f"[Exact saved {snapshot.editor_name} target: {snapshot.display_name}; "
                f"characters {snapshot.selection_start}:{snapshot.selection_end}]"
            ),
        }

    def _capture_spreadsheet_rewrite_target(
        self,
        context: dict[str, Any],
        active_app: dict[str, Any],
    ) -> dict[str, Any]:
        """Capture a typed cell range when Rewrite starts in Excel or Calc."""
        from core.actions.adapters.excel import ExcelRuntimeProvider
        from core.rewrite_spreadsheets import spreadsheet_grid_text

        if ExcelRuntimeProvider.detects(context):
            provider = ExcelRuntimeProvider()
            snapshot = provider.snapshot(context)
            if not bool(snapshot.formula_capture_complete):
                raise RuntimeError("Excel did not expose complete formula identity for the selected range.")
            grid_text = spreadsheet_grid_text(snapshot.values, snapshot.formulas)
            return {
                "kind": "excel_cells",
                "snapshot": snapshot,
                "grid_text": grid_text,
                "rows": snapshot.row_count,
                "columns": snapshot.column_count,
                "prompt_context": (
                    f"[Exact Excel target: {snapshot.workbook_name} / "
                    f"{snapshot.worksheet_name}!{snapshot.selection_address}]\n{grid_text}"
                ),
            }

        from core.actions.adapters.calc import CalcSnapshot, is_calc_app

        if not is_calc_app(active_app):
            return {}
        response = self._safe_call(
            self.native,
            "native.action.calc.snapshot",
            {"active_app": active_app},
            timeout=15.0,
        ) or {}
        selection = response.get("selection") if isinstance(response, dict) else None
        if not bool(response.get("ok")) or not isinstance(selection, dict):
            raise RuntimeError(str(response.get("error") or "Calc returned no selected range."))
        snapshot = CalcSnapshot.from_selection(selection)
        grid_text = spreadsheet_grid_text(snapshot.typed_values, snapshot.formulas)
        return {
            "kind": "calc_cells",
            "snapshot": snapshot,
            "grid_text": grid_text,
            "rows": snapshot.row_count,
            "columns": snapshot.column_count,
            "prompt_context": (
                f"[Exact LibreOffice Calc target: {snapshot.document_title} / "
                f"{snapshot.selection_address}]\n{grid_text}"
            ),
        }

    @staticmethod
    def _capture_word_rewrite_target(
        context: dict[str, Any],
        active_app: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind a desktop Word selection to an exact native COM Range."""
        from core.rewrite_office import WordRewriteClient, is_word_desktop_app

        if not is_word_desktop_app(active_app):
            return {}
        try:
            snapshot = WordRewriteClient().inspect_selection(active_app)
        except ValueError:
            # A Word table/object boundary is not a safe generic paste target.
            # Keep it blocked until object-aware Rewrite can bind that object.
            raise
        except Exception as exc:  # noqa: BLE001 - generic Rewrite remains useful
            log.info("Word exact Rewrite target unavailable; using focus-safe fallback: %s", exc)
            return {}
        return {
            "kind": "word_text_range",
            "snapshot": snapshot,
            "grid_text": snapshot.selected_text,
            "prompt_context": (
                f"[Exact Microsoft Word target: {snapshot.document_name}; "
                f"characters {snapshot.start}:{snapshot.end}]"
            ),
        }

    @staticmethod
    def _capture_powerpoint_rewrite_target(
        context: dict[str, Any],
        active_app: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind a desktop PowerPoint selection to one slide shape text range."""
        from core.actions.adapters.presentation import is_powerpoint_desktop_app
        from core.rewrite_office import PowerPointRewriteClient

        if not is_powerpoint_desktop_app(active_app):
            return {}
        try:
            snapshot = PowerPointRewriteClient().inspect_selection(active_app)
        except ValueError:
            # Images, grouped objects, and ambiguous multi-shape selections
            # must never silently degrade into a focus-based text paste.
            raise
        except Exception as exc:  # noqa: BLE001 - generic Rewrite remains useful
            log.info("PowerPoint exact Rewrite target unavailable; using focus-safe fallback: %s", exc)
            return {}
        return {
            "kind": "powerpoint_text_range",
            "snapshot": snapshot,
            "grid_text": snapshot.selected_text,
            "prompt_context": (
                f"[Exact Microsoft PowerPoint target: {snapshot.presentation_name}; "
                f"slide {snapshot.slide_id}, shape {snapshot.shape_id}, "
                f"characters {snapshot.start}:{snapshot.start + snapshot.length}]"
            ),
        }

    def _capture_libreoffice_rewrite_target(
        self,
        context: dict[str, Any],
        active_app: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind Writer or Impress selected text to a serializable UNO container."""
        from core.rewrite_libreoffice import (
            LibreOfficeRewriteSnapshot,
            libreoffice_rewrite_surface,
        )

        surface = libreoffice_rewrite_surface(active_app)
        if not surface:
            return {}
        response = self._safe_call(
            self.native,
            "native.action.libreoffice.rewrite_snapshot",
            {
                "active_app": active_app,
                "selected_text": str(context.get("selected_text") or ""),
            },
            timeout=15.0,
        ) or {}
        payload = response.get("snapshot") if isinstance(response, dict) else None
        if not bool(response.get("ok")) or not isinstance(payload, dict):
            raise RuntimeError(
                str(response.get("error") or f"LibreOffice {surface.title()} returned no exact selection.")
            )
        snapshot = LibreOfficeRewriteSnapshot.from_dict(payload)
        target_label = "Writer text container" if surface == "writer" else "Impress slide shape"
        return {
            "kind": "libreoffice_text_range",
            "snapshot": snapshot,
            "grid_text": snapshot.selected_text,
            "prompt_context": (
                f"[Exact LibreOffice {surface.title()} target: {snapshot.document_title}; "
                f"{target_label}, characters {snapshot.start}:{snapshot.start + snapshot.length}]"
            ),
        }

    def _capture_browser_rewrite_target(
        self,
        context: dict[str, Any],
        active_app: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind editable DOM text when the tab belongs to OpenWand's managed browser."""
        from core.actions.adapters.browser import is_browser_app
        from core.rewrite_browser import BrowserRewriteSnapshot

        if not is_browser_app(active_app):
            return {}
        response = self._safe_call(
            self.native,
            "native.action.browser.rewrite_snapshot",
            {"active_app": active_app},
            timeout=8.0,
        ) or {}
        payload = response.get("snapshot") if isinstance(response, dict) else None
        if not bool(response.get("ok")) or not isinstance(payload, dict):
            log.info("managed-browser exact Rewrite unavailable: %s", response.get("error"))
            return {}
        snapshot = BrowserRewriteSnapshot.from_dict(payload)
        return {
            "kind": "browser_text_range",
            "snapshot": snapshot,
            "grid_text": snapshot.selected_text,
            "prompt_context": (
                f"[Exact managed browser target: {snapshot.title}; "
                f"editable characters {snapshot.start}:{snapshot.end}]"
            ),
        }

    @staticmethod
    def _build_structured_rewrite_plan(target: dict[str, Any], replacement: str) -> Any:
        """Bind a model proposal to an exact app-owned target."""
        snapshot = target.get("snapshot")
        kind = str(target.get("kind") or "")
        if kind == "vscode_saved_selection":
            from core.actions.adapters.vscode import build_replace_selection_plan

            return build_replace_selection_plan(
                snapshot,
                replacement,
                summary="Rewrite the selected code",
            )
        if kind == "word_text_range":
            from core.rewrite_office import build_word_rewrite_plan

            return build_word_rewrite_plan(snapshot, replacement)
        if kind == "powerpoint_text_range":
            from core.rewrite_office import build_powerpoint_rewrite_plan

            return build_powerpoint_rewrite_plan(snapshot, replacement)
        if kind == "libreoffice_text_range":
            from core.rewrite_libreoffice import build_libreoffice_rewrite_plan

            return build_libreoffice_rewrite_plan(snapshot, replacement)
        if kind == "browser_text_range":
            from core.rewrite_browser import build_browser_rewrite_plan

            return build_browser_rewrite_plan(snapshot, replacement)
        from core.rewrite_spreadsheets import spreadsheet_rewrite_changes

        if kind == "excel_cells":
            from core.actions.adapters.excel.plans import build_cleanup_plan

            changes = spreadsheet_rewrite_changes(
                snapshot.values,
                snapshot.formulas,
                replacement,
                allow_boolean_values=True,
            )
            return build_cleanup_plan(snapshot, changes)
        if kind == "calc_cells":
            from core.actions.adapters.calc.plans import build_cleanup_plan

            changes = spreadsheet_rewrite_changes(
                snapshot.typed_values,
                snapshot.formulas,
                replacement,
                allow_boolean_values=False,
            )
            return build_cleanup_plan(snapshot, changes)
        raise ValueError("The app-owned Rewrite target is unavailable.")

    def _apply_structured_rewrite(self, request: RewriteAnnotationRequest) -> bool:
        """Apply and verify one accepted app-owned target plan."""
        plan = request.structured_plan
        kind = str(request.structured_target.get("kind") or "")
        if plan is None:
            return False
        try:
            if kind == "vscode_saved_selection":
                response = self._safe_call(
                    self.native,
                    "native.action.vscode.apply",
                    {
                        "plan": plan.to_dict(),
                        "confirmed": True,
                        "idempotency_key": f"rewrite:{request.annotation_id}",
                    },
                    timeout=15.0,
                ) or {}
                result = response.get("result") if isinstance(response, dict) else {}
                return bool(response.get("ok") and isinstance(result, dict) and result.get("status") == "applied")
            if kind == "excel_cells":
                from core.actions.adapters.excel import ExcelActionAdapter

                result = ExcelActionAdapter().execute(
                    plan,
                    confirmed=True,
                    idempotency_key=f"rewrite:{request.annotation_id}",
                )
                return bool(result.status == "applied" and result.verification)
            if kind == "calc_cells":
                response = self._safe_call(
                    self.native,
                    "native.action.calc.apply",
                    {
                        "plan": plan.to_dict(),
                        "confirmed": True,
                        "idempotency_key": f"rewrite:{request.annotation_id}",
                    },
                    timeout=15.0,
                ) or {}
                result = response.get("result") if isinstance(response, dict) else {}
                return bool(response.get("ok") and isinstance(result, dict) and result.get("status") == "applied")
            if kind == "word_text_range":
                from core.rewrite_office import WordRewriteClient

                return WordRewriteClient().apply(plan)
            if kind == "powerpoint_text_range":
                from core.rewrite_office import PowerPointRewriteClient

                return PowerPointRewriteClient().apply(plan)
            if kind == "libreoffice_text_range":
                response = self._safe_call(
                    self.native,
                    "native.action.libreoffice.rewrite_apply",
                    {"plan": plan.to_dict()},
                    timeout=15.0,
                ) or {}
                result = response.get("result") if isinstance(response, dict) else {}
                return bool(
                    response.get("ok")
                    and isinstance(result, dict)
                    and result.get("status") == "applied"
                    and result.get("verification")
                )
            if kind == "browser_text_range":
                response = self._safe_call(
                    self.native,
                    "native.action.browser.rewrite_apply",
                    {"plan": plan.to_dict()},
                    timeout=12.0,
                ) or {}
                result = response.get("result") if isinstance(response, dict) else {}
                return bool(
                    response.get("ok")
                    and isinstance(result, dict)
                    and result.get("status") == "applied"
                    and result.get("verification")
                )
        except Exception:
            log.exception("structured app Rewrite apply failed")
        return False

    def submit_rewrite_annotation(
        self,
        annotation_id: str,
        comment: str,
        include_document: bool,
        *,
        allow_revision: bool = False,
    ) -> None:
        """Generate one proposal without editing the captured application."""
        key = str(annotation_id or "")
        clean_comment = str(comment or "").strip()
        with self._lock:
            request = self._rewrite_annotations.get(key)
            if request is None or not clean_comment:
                return
            allowed_states = {"composing", "failed", "queued"}
            if allow_revision:
                allowed_states.add("proposal")
            if request.state not in allowed_states:
                return
            request.display_number = self._next_rewrite_display_number(excluding=request)
            request.comment = clean_comment
            request.include_document = bool(include_document)
            request.state = "processing"
            request.stream_id = None
            display_number = request.display_number
        self._publish_rewrite_held_count()
        self._fire(
            self.ui,
            "ui.rewrite.annotation.processing",
            {"annotation_id": key, "display_number": display_number},
        )

        session = self._rewrite_app_sessions.setdefault(
            request.session_key,
            {"document_text": "", "turns": []},
        )
        if include_document and not str(session.get("document_text") or "").strip():
            try:
                session["document_text"] = self._fetch_active_document_text(request.pending.context)
            except Exception:
                log.exception("rewrite annotation document context capture failed")
        document_text = str(session.get("document_text") or "").strip()
        prior_turns = list(session.get("turns") or [])[-6:]
        context_parts: list[str] = []
        if document_text:
            context_parts.append(f"[Active document]\n{document_text}")
        if prior_turns:
            rendered_turns = []
            for turn in prior_turns:
                if not isinstance(turn, dict):
                    continue
                rendered_turns.append(
                    "Instruction: {instruction}\nProposal: {proposal}".format(
                        instruction=str(turn.get("instruction") or ""),
                        proposal=str(turn.get("proposal") or ""),
                    )
                )
            if rendered_turns:
                context_parts.append("[Earlier Rewrite proposals in this app conversation]\n" + "\n\n".join(rendered_turns))
        if request.structured_target:
            context_parts.append(str(request.structured_target.get("prompt_context") or ""))
        rewrite_context = "\n\n".join(context_parts)
        structured_requirement = ""
        structured_kind = str(request.structured_target.get("kind") or "")
        if structured_kind in {"excel_cells", "calc_cells"}:
            rows = int(request.structured_target.get("rows") or 0)
            columns = int(request.structured_target.get("columns") or 0)
            structured_requirement = (
                f" The target is a {rows}-row by {columns}-column spreadsheet range. "
                "Return only the complete replacement range as plain tab-separated values, with exactly "
                "the same row and column counts. Preserve formulas with a leading '=' and do not use a "
                "Markdown code fence."
            )
        elif structured_kind == "vscode_saved_selection":
            editor_label = str(
                getattr(request.structured_target.get("snapshot"), "editor_name", "Code editor")
                or "Code editor"
            )
            structured_requirement = (
                f" The target is an exact saved {editor_label} selection. Return only the replacement code, "
                "preserving indentation and line endings where appropriate, without a Markdown code fence."
            )
        elif structured_kind == "word_text_range":
            structured_requirement = (
                " The target is an exact Microsoft Word text range. Return only the replacement text, "
                "without commentary or a Markdown code fence."
            )
        elif structured_kind == "powerpoint_text_range":
            structured_requirement = (
                " The target is an exact Microsoft PowerPoint shape text range. Return only the "
                "replacement text, without commentary or a Markdown code fence."
            )
        elif structured_kind == "libreoffice_text_range":
            surface = str(getattr(request.structured_target.get("snapshot"), "surface", "document"))
            structured_requirement = (
                f" The target is an exact LibreOffice {surface.title()} text range. Return only the "
                "replacement text, without commentary or a Markdown code fence."
            )
        elif structured_kind == "browser_text_range":
            structured_requirement = (
                " The target is an exact editable text range in a OpenWand-managed browser tab. "
                "Return only the replacement text, without commentary or a Markdown code fence."
            )
        delegated_prompt = (
            f"{clean_comment}\n\n"
            "Execution requirement: delegate this edit to a sub-agent when the active model/runtime "
            "supports sub-agents. OpenWand is issuing this edit as a separate managed call when native "
            "sub-agents are unavailable. Return only the proposed replacement through the rewrite tool."
            f"{structured_requirement}"
        )

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            if event == "privacy.review.request":
                self._handle_privacy_review_request(payload)

        def on_started(req_id: Any) -> None:
            with self._lock:
                current = self._rewrite_annotations.get(key)
                if current is request:
                    request.stream_id = req_id
                    return
            self._safe_call(self.brain, "brain.cancel", {"target": req_id}, timeout=5.0)

        try:
            result = self._brain_call_with_events(
                "brain.rewrite",
                {
                    "selected_text": str(request.pending.context.get("selected_text") or ""),
                    "intent_prompt": delegated_prompt,
                    "rewrite_context": rewrite_context,
                    "privacy_session_id": f"rewrite-annotation:{key}",
                },
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=on_event,
                on_started=on_started,
            )
        except Exception as exc:  # noqa: BLE001 - retain the user's popup for retry
            log.exception("rewrite annotation generation failed")
            with self._lock:
                current = self._rewrite_annotations.get(key)
            if current is request:
                request.state = "failed"
                request.stream_id = None
                self._safe_call(
                    self.ui,
                    "ui.rewrite.annotation.failure",
                    {"annotation_id": key, "message": f"Rewrite failed: {self._friendly_error(exc)}"},
                    timeout=30.0,
                )
            return

        with self._lock:
            current = self._rewrite_annotations.get(key)
        if current is not request:
            return
        request.stream_id = None
        raw_replacement = str((result or {}).get("text") or "")
        replacement = (
            raw_replacement.strip("\r\n")
            if request.structured_target
            else raw_replacement.strip()
        )
        if not replacement:
            request.state = "failed"
            self._safe_call(
                self.ui,
                "ui.rewrite.annotation.failure",
                {"annotation_id": key, "message": "The model returned no replacement. You can retry."},
                timeout=30.0,
            )
            return
        if request.structured_target:
            try:
                request.structured_plan = self._build_structured_rewrite_plan(
                    request.structured_target,
                    replacement,
                )
            except Exception as exc:  # noqa: BLE001 - malformed proposals remain retryable
                request.state = "failed"
                self._safe_call(
                    self.ui,
                    "ui.rewrite.annotation.failure",
                    {"annotation_id": key, "message": f"Rewrite proposal was not safe to apply: {exc}"},
                    timeout=30.0,
                )
                return
        request.replacement_text = replacement
        request.state = "proposal"
        session.setdefault("turns", []).append(
            {"instruction": clean_comment, "proposal": replacement}
        )
        session["turns"] = list(session.get("turns") or [])[-12:]
        self._safe_call(
            self.ui,
            "ui.rewrite.annotation.proposal",
            {
                "annotation_id": key,
                "replacement_text": replacement,
                "copy_only": request.copy_only,
            },
            timeout=30.0,
        )

    def hold_rewrite_annotation(
        self,
        annotation_id: str,
        comment: str,
        include_document: bool,
    ) -> None:
        """Stash one complete comment without spending a model call."""
        key = str(annotation_id or "")
        clean_comment = str(comment or "").strip()
        with self._lock:
            request = self._rewrite_annotations.get(key)
            if request is None or not clean_comment or request.state == "processing":
                return
            request.comment = clean_comment
            request.include_document = bool(include_document)
            request.state = "held"
            request.stream_id = None
        self._publish_rewrite_held_count()

    def send_all_rewrite_annotations(self) -> None:
        """Send every held comment, reusing only the conversation for its own app."""
        with self._lock:
            held = [
                request
                for request in self._rewrite_annotations.values()
                if request.state == "held" and request.comment.strip()
            ]
            for request in held:
                request.state = "queued"
        self._publish_rewrite_held_count()
        for request in held:
            self.submit_rewrite_annotation(
                request.annotation_id,
                request.comment,
                request.include_document,
            )

    def _publish_rewrite_held_count(self) -> None:
        with self._lock:
            count = sum(
                request.state == "held"
                for request in self._rewrite_annotations.values()
            )
        self._fire(self.ui, "ui.rewrite.held_count", {"count": int(count)})

    def refresh_rewrite_annotation_anchor(self, annotation_id: str) -> None:
        """Re-query one cached range so its popup follows document scrolling."""
        key = str(annotation_id or "")
        try:
            with self._lock:
                request = self._rewrite_annotations.get(key)
            if request is None:
                return
            context = request.pending.context
            active_app = (
                context.get("active_app")
                if isinstance(context.get("active_app"), dict)
                else {}
            )
            anchor = self._safe_call(
                self.native,
                "native.selection.anchor.resolve",
                {
                    "focus_token": int(context.get("focus_token") or 0),
                    "source_window_id": int(active_app.get("window_id") or 0),
                    "allow_mouse": False,
                    "refresh": True,
                },
                timeout=2.0,
            )
            if not isinstance(anchor, dict) or not anchor.get("ok"):
                return
            visible = bool(anchor.get("visible"))
            rect = dict(anchor.get("selection_rect") or {}) if visible else {}
            if visible:
                context["selection_rect"] = rect
                context["selection_anchor_source"] = str(anchor.get("source") or "")
            self._fire(
                self.ui,
                "ui.rewrite.annotation.anchor",
                {
                    "annotation_id": key,
                    "selection_rect": rect,
                    "visible": visible,
                    "source": str(anchor.get("source") or ""),
                },
            )
        finally:
            with self._lock:
                self._rewrite_anchor_refreshing.discard(key)

    def _release_rewrite_annotation_anchor(self, request: RewriteAnnotationRequest) -> None:
        token = int(request.pending.context.get("focus_token") or 0)
        if token:
            self._fire(
                self.native,
                "native.selection.anchor.release",
                {"focus_token": token},
            )

    def cancel_rewrite_annotation(self, annotation_id: str) -> None:
        """Cancel the correct managed model call and clear its state."""
        key = str(annotation_id or "")
        with self._lock:
            request = self._rewrite_annotations.pop(key, None)
        if request is not None and request.stream_id is not None:
            self._safe_call(self.brain, "brain.cancel", {"target": request.stream_id}, timeout=5.0)
        if request is not None:
            self._release_rewrite_annotation_anchor(request)
        self._publish_rewrite_held_count()

    def decline_rewrite_annotation(self, annotation_id: str) -> None:
        """Discard a composed or completed proposal."""
        key = str(annotation_id or "")
        with self._lock:
            request = self._rewrite_annotations.pop(key, None)
        if request is not None:
            self._release_rewrite_annotation_anchor(request)
        self._publish_rewrite_held_count()

    def revise_rewrite_annotation(self, annotation_id: str, feedback: str) -> None:
        """Regenerate a proposal from its original target and follow-up feedback."""
        key = str(annotation_id or "")
        with self._lock:
            request = self._rewrite_annotations.get(key)
        clean_feedback = str(feedback or "").strip()
        if request is None or not clean_feedback:
            return
        revision_prompt = (
            f"Original instruction: {request.comment}\n"
            f"Previous proposal: {request.replacement_text}\n"
            f"Follow-up instruction: {clean_feedback}"
        )
        self.submit_rewrite_annotation(
            key,
            revision_prompt,
            request.include_document,
            allow_revision=True,
        )

    def accept_rewrite_annotation(self, annotation_id: str) -> None:
        """Immediately apply the captured proposal, falling back to Copy safely."""
        key = str(annotation_id or "")
        with self._lock:
            request = self._rewrite_annotations.get(key)
            if request is None or request.state != "proposal" or not request.replacement_text:
                return
            # Claim this proposal before leaving the lock. A double click or
            # duplicate IPC event must never apply the same rewrite twice.
            request.state = "applying"
        if request.structured_target:
            applied = self._apply_structured_rewrite(request)
            if applied:
                with self._lock:
                    self._rewrite_annotations.pop(key, None)
                self._fire(self.ui, "ui.rewrite.annotation.remove", {"annotation_id": key})
                return
            request.copy_only = True
            request.state = "proposal"
            self._safe_call(
                self.ui,
                "ui.rewrite.annotation.proposal",
                {
                    "annotation_id": key,
                    "replacement_text": request.replacement_text,
                    "copy_only": True,
                },
                timeout=30.0,
            )
            self._notice(
                "OpenWand could not safely update that app selection. The proposal is still available to copy.",
                severity="warning",
            )
            return
        if request.copy_only:
            copied = self.native.call(
                "native.clipboard.set",
                {"text": request.replacement_text},
                timeout=30.0,
            ) or {}
            if isinstance(copied, dict) and copied.get("ok"):
                with self._lock:
                    self._rewrite_annotations.pop(key, None)
                self._fire(self.ui, "ui.rewrite.annotation.remove", {"annotation_id": key})
            else:
                request.state = "proposal"
            return

        context = request.pending.context
        paste = self.native.call(
            "native.paste_text",
            {
                "text": request.replacement_text,
                "target_pid": int(request.pending.paste_target_pid or 0),
                "focus_token": int(context.get("focus_token") or 0),
                "restore_clipboard": True,
            },
            timeout=30.0,
        ) or {}
        if isinstance(paste, dict) and paste.get("ok"):
            with self._lock:
                self._rewrite_annotations.pop(key, None)
                self._last_undoable_edit = UndoableEdit(
                    original_text=str(context.get("selected_text") or ""),
                    replacement_text=request.replacement_text,
                    target_pid=int(request.pending.paste_target_pid or 0),
                    focus_token=int(context.get("focus_token") or 0),
                )
            self._fire(self.ui, "ui.rewrite.annotation.remove", {"annotation_id": key})
            return

        # Preserve the proposal and offer a safe manual path instead of applying
        # to whichever control happens to be focused now.
        request.copy_only = True
        request.state = "proposal"
        self._safe_call(
            self.ui,
            "ui.rewrite.annotation.proposal",
            {
                "annotation_id": key,
                "replacement_text": request.replacement_text,
                "copy_only": True,
            },
            timeout=30.0,
        )
        self._notice("OpenWand could not safely edit this selection in place. Use Copy instead.", severity="warning")

    def begin_snip(self) -> None:
        """Handle begin snip for flow controller."""
        self._new_generation()
        # Show the selector FIRST; it must never wait on audio teardown or
        # cosmetic UI state. Stopping audio and the "listening" animation are
        # fired afterwards without blocking (mirrors begin_caller). Previously the
        # blocking audio.stop call delayed the overlay once the audio worker was
        # busy - fast on the first snip, slow on later ones.
        t0 = time.monotonic()
        self.ui.call("ui.show_snip", timeout=30.0)
        log.info("snip: ui.show_snip round-trip %.2fs", time.monotonic() - t0)
        self._fire(self.audio, "audio.stop")
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})

    def stop_reply_bubble(self) -> None:
        """Cancel the active answer, hide its bubble, and stop its speech."""
        with self._lock:
            generation = self._current_generation
            self._reply_bubble_cancelled_generation = generation
            target = (
                self._active_reply_stream_id
                if self._active_reply_stream_generation == generation
                else None
            )
        if target is not None:
            self._safe_call(
                self.brain,
                "brain.cancel",
                {"target": target},
                timeout=5.0,
            )
        self._cancel_tts_sequence(generation)
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "idle"}, timeout=30.0)

    def snip_region_selected(self, region: dict[str, Any]) -> None:
        """Handle snip region selected for flow controller."""
        result = self.native.call("native.capture.region", {"region": region}, timeout=30.0)
        path = result.get("path") if isinstance(result, dict) else ""
        screenshot_b64 = self._file_b64(path) if path else None
        caller = self._snip_caller()
        with self._lock:
            pending = PendingInvocation(
                caller_idx=0,
                caller=caller,
                context=self._context_snapshot(caller, dedupe_selection=True),
                screenshot_b64=screenshot_b64,
                is_snip=True,
            )
            self._pending = pending
        self.ui.call(
            "ui.show_intent",
            {
                "caller_idx": 0,
                "context_items": self._intent_context_items(pending) if pending else [],
                "tool_snapshot": self._intent_tool_snapshot(pending.caller) if pending else {},
            },
            timeout=30.0,
        )

    def intent_snip_requested(
        self,
        context_choices: list[dict[str, Any]] | None = None,
        custom_text: str = "",
        tool_choices: list[dict[str, Any]] | None = None,
    ) -> None:
        """Mark the current intent as waiting for a user-selected screenshot."""
        with self._lock:
            pending = self._pending
            if pending is None:
                return
            pending.caller = self._apply_intent_context_choices(
                pending.caller,
                context_choices or [],
            )
            pending.tool_choices = [
                dict(item)
                for item in (tool_choices or [])
                if isinstance(item, dict)
            ]
            pending.caller["context_screenshot"] = "auto"
            pending.caller["_context_screenshot_enabled"] = True
            has_screenshot = bool(pending.screenshot_b64)
            pending.caller["_context_screenshot_requires_snip"] = not has_screenshot
            if not has_screenshot:
                pending.screenshot_b64 = None
            pending.screenshot_tool_b64 = None
            self._pending = pending
            self._pending_context_capture = {
                "surface": "intent",
                "source": "screenshot",
                "custom_text": str(custom_text or ""),
            }

    def intent_snip_region_selected(self, region: dict[str, Any]) -> None:
        """Attach a selected snip to the current pending intent."""
        result = self.native.call("native.capture.region", {"region": region}, timeout=30.0)
        path = result.get("path") if isinstance(result, dict) else ""
        screenshot_b64 = self._file_b64(path) if path else None
        with self._lock:
            pending = self._pending
            if pending is None:
                return
            capture = dict(self._pending_context_capture or {})
            custom_text = str(capture.get("custom_text") or "")
            if capture.get("source") == "screenshot":
                self._pending_context_capture = None
            pending.screenshot_b64 = screenshot_b64
            pending.screenshot_tool_b64 = None
            pending.caller["context_screenshot"] = "auto" if screenshot_b64 else "off"
            pending.caller["_context_screenshot_enabled"] = bool(screenshot_b64)
            pending.caller["_context_screenshot_requires_snip"] = False
            self._pending = pending
        context_items = self._intent_context_items(pending)
        for item in context_items:
            if item.get("id") == "screenshot":
                if screenshot_b64:
                    item["touched"] = True
                else:
                    item["force_state"] = True
        self._restore_intent_after_context_capture(pending, custom_text, context_items)

    def intent_snip_cancelled(self) -> None:
        """Return the screenshot chip to Off after a cancelled intent snip."""
        with self._lock:
            pending = self._pending
            if pending is None:
                return
            capture = dict(self._pending_context_capture or {})
            custom_text = str(capture.get("custom_text") or "")
            if capture.get("source") == "screenshot":
                self._pending_context_capture = None
            pending.screenshot_b64 = None
            pending.screenshot_tool_b64 = None
            pending.caller["context_screenshot"] = "off"
            pending.caller["_context_screenshot_enabled"] = False
            pending.caller["_context_screenshot_requires_snip"] = False
            self._pending = pending
        context_items = self._intent_context_items(pending)
        for item in context_items:
            if item.get("id") == "screenshot":
                item["force_state"] = True
        self._restore_intent_after_context_capture(pending, custom_text, context_items)

    def intent_selection_capture_requested(
        self,
        context_choices: list[dict[str, Any]] | None = None,
        custom_text: str = "",
        tool_choices: list[dict[str, Any]] | None = None,
    ) -> None:
        """Capture selected text or paths for intent after the next user selection."""
        with self._lock:
            pending = self._pending
            if pending is None:
                return
            pending.caller = self._apply_intent_context_choices(
                pending.caller,
                context_choices or [],
            )
            pending.tool_choices = [
                dict(item)
                for item in (tool_choices or [])
                if isinstance(item, dict)
            ]
            pending.caller["_context_selection_enabled"] = False
            self._pending = pending
            self._pending_context_capture = {
                "surface": "intent",
                "source": "selection",
                "custom_text": str(custom_text or ""),
            }
            capture = dict(self._pending_context_capture)
        self._notice("Select text or files/folders.")
        self._complete_selection_after_user_selects(capture)

    def chat_selection_capture_requested(self) -> None:
        """Capture selected text or paths for chat after the next user selection."""
        with self._lock:
            self._pending_context_capture = {"surface": "chat", "source": "selection"}
            capture = dict(self._pending_context_capture)
        self._notice("Select text or files/folders.")
        self._complete_selection_after_user_selects(capture)

    def intent_context_source_removed(self, item_id: str, source_id: str) -> None:
        """Drop one removed context row from the pending invocation.

        Item-level rows (selection, clipboard, ...) are handled inside the
        overlay by switching the chip off; only per-source rows need the
        supervisor so the removed document block also leaves the prompt.
        """
        with self._lock:
            pending = self._pending
        if pending is None:
            return
        if item_id == "attachments" and source_id.startswith("dropped:"):
            try:
                index = int(source_id.partition(":")[2])
            except ValueError:
                index = -1
            if 0 <= index < len(self._drop_context_items):
                self._drop_context_items.pop(index)
            self._fire(
                self.ui,
                "ui.intent.context_items",
                {"context_items": self._intent_context_items(pending)},
            )
            return
        pending.removed_context_sources.add((str(item_id), str(source_id)))
        context = pending.context if isinstance(pending.context, dict) else {}
        if item_id == "ambient":
            context.setdefault("_active_document_text_full", str(context.get("active_document_text") or ""))
            context.setdefault(
                "_active_document_sources_full",
                [
                    dict(item)
                    for item in (context.get("active_document_sources") or [])
                    if isinstance(item, dict)
                ],
            )
            removed = {
                sid for iid, sid in pending.removed_context_sources if iid == "ambient" and sid
            }
            sources = [
                item
                for item in (context.get("active_document_sources") or [])
                if isinstance(item, dict)
                and " ".join(str(item.get("label") or "").split()) not in removed
            ]
            context["active_document_sources"] = sources
            if context.get("active_document_text"):
                context["active_document_text"] = self._strip_removed_document_sources(
                    str(context.get("active_document_text") or ""), removed
                )
            if not sources:
                # The last app document row was removed: disable App context for
                # this invocation so the top chip switches off with the list.
                pending.caller["_context_ambient_enabled"] = False
        self._fire(
            self.ui,
            "ui.intent.context_items",
            {"context_items": self._intent_context_items(pending)},
        )

    def intent_context_source_reenabled(
        self,
        item_id: str,
        context_choices: list[dict[str, Any]] | None = None,
    ) -> None:
        """Restore a context group that was emptied by per-row removals."""
        with self._lock:
            pending = self._pending
        if pending is None:
            return

        item_id = str(item_id or "")
        pending.caller = self._apply_intent_context_choices(
            pending.caller,
            context_choices or [],
        )
        if item_id == "ambient":
            pending.caller["_context_ambient_enabled"] = True
            pending.removed_context_sources = {
                pair for pair in pending.removed_context_sources if pair[0] != "ambient"
            }
            context = pending.context if isinstance(pending.context, dict) else {}
            full_text = str(context.get("_active_document_text_full") or "")
            full_sources = [
                dict(item)
                for item in (context.get("_active_document_sources_full") or [])
                if isinstance(item, dict)
            ]
            if full_text or full_sources:
                context["active_document_text"] = full_text
                context["active_document_sources"] = full_sources
            else:
                context.pop("active_document_text", None)
                context.pop("active_document_sources", None)
                text = self._fetch_active_document_text(context)
                if text:
                    context["active_document_text"] = text
            pending.context = context
        elif item_id == "browser":
            pending.removed_context_sources = {
                pair for pair in pending.removed_context_sources if pair[0] != "browser"
            }

        with self._lock:
            if self._pending is pending:
                self._pending = pending
        self._fire(
            self.ui,
            "ui.intent.context_items",
            {"context_items": self._intent_context_items(pending)},
        )

    @staticmethod
    def _strip_removed_document_sources(text: str, removed_labels: set[str]) -> str:
        """Drop labelled document blocks the user removed in the intent picker."""
        raw = str(text or "")
        if not raw or not removed_labels:
            return raw
        matches = list(re.finditer(r"(?m)^\[([^\]\n]{1,160})\]\n", raw))
        if not matches:
            return raw
        kept: list[str] = []
        prefix = raw[: matches[0].start()].strip()
        if prefix:
            kept.append(prefix)
        for idx, match in enumerate(matches):
            label = " ".join(match.group(1).split()).strip()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            if label in removed_labels:
                continue
            kept.append(raw[match.start():end].strip())
        return "\n\n".join(part for part in kept if part).strip()

    def _complete_selection_after_user_selects(self, capture: dict[str, Any]) -> None:
        """Capture Selection automatically after the user finishes selecting."""
        try:
            context = self.native.call(
                "native.context.await_selection",
                {
                    "timeout": 30.0,
                    "settle_ms": 100,
                    "include_clipboard": True,
                    "include_selected_paths": True,
                },
                timeout=35.0,
            ) or {}
        except Exception:
            log.exception("interactive selection capture failed")
            context = {}
        if not context:
            context = self._context_snapshot({"context_clipboard": True}, include_selected_paths=True)
        with self._lock:
            if self._pending_context_capture != capture:
                return
        paths = self._selected_paths_from_context(context)
        selected_text = str(context.get("selected_text") or "").strip()
        clipboard_text = str(context.get("clipboard_text") or "").strip()
        text = selected_text or ("" if paths else clipboard_text)
        self._complete_selection_capture(text, capture, paths)

    def chat_snip_region_selected(self, region: dict[str, Any]) -> None:
        """Attach a selected snip image to the chat composer."""
        result = self.native.call("native.capture.region", {"region": region}, timeout=30.0)
        path = result.get("path") if isinstance(result, dict) else ""
        screenshot_b64 = self._file_b64(path) if path else None
        if not screenshot_b64:
            self.chat_snip_cancelled()
            return
        self._safe_call(
            self.ui,
            "ui.chat.capture_context",
            {
                "name": "Screenshot",
                "content": screenshot_b64,
                "item_type": "image",
                "source": "screenshot",
            },
            timeout=30.0,
        )
        self._notice("Screenshot captured.")

    def chat_snip_cancelled(self) -> None:
        """Return the chat Screenshot chip to Off after a cancelled snip."""
        self._safe_call(
            self.ui,
            "ui.chat.capture_cancelled",
            {"source": "screenshot"},
            timeout=30.0,
        )

    def intent_chosen(
        self,
        prompt: str,
        context_choices: list[dict[str, Any]] | None = None,
        intent_routing: dict[str, Any] | None = None,
        conversation_choice: dict[str, Any] | None = None,
        tool_choices: list[dict[str, Any]] | None = None,
    ) -> None:
        """Handle intent chosen for flow controller."""
        import config

        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            pending = PendingInvocation(caller_idx=0, caller=self._caller(0), context=self._context_snapshot({}))
            pending.context_ready.set()
        elif not pending.context_ready.is_set():
            pending.context_ready.wait(timeout=3.0)
        choices = context_choices or []
        if (
            bool(getattr(config, "CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY", False))
            and str((conversation_choice or {}).get("mode") or "").strip().lower() == "continue"
        ):
            choices = [
                (
                    {**item, "state": "off", "default_state": "off"}
                    if not item.get("locked") and not item.get("touched")
                    else dict(item)
                )
                for item in choices
            ]
        pending.caller = self._apply_intent_context_choices(pending.caller, choices)
        effective_tool_choices = tool_choices if tool_choices is not None else pending.tool_choices
        pending.caller = self._apply_intent_tool_choices(
            pending.caller,
            effective_tool_choices or [],
        )
        context = pending.context if isinstance(pending.context, dict) else {}
        if (
            str(context.get("platform") or "").strip().lower().startswith("linux")
            and not any(str(item.get("id") or "") == "selection" for item in choices)
        ):
            pending.caller["_context_selection_enabled"] = False
        if (
            pending.caller.get("_context_selection_enabled")
            and not str(context.get("selected_text") or "").strip()
            and str(context.get("stale_selected_text") or "").strip()
        ):
            # The user toggled the off-by-default Selection chip back on:
            # attach the earlier (stale) selection it was offering.
            context["selected_text"] = str(context.get("stale_selected_text") or "")
        if not prompt:
            prompt = "What is this?"
        app_selection = context.get("app_selection") if isinstance(context.get("app_selection"), dict) else {}
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        browser_app = dict(active_app)
        if context.get("browser_url"):
            browser_app["browser_url"] = str(context.get("browser_url") or "")
        selected_text = str(context.get("selected_text") or "")
        routing = self._validated_intent_routing(pending, intent_routing)
        if routing.get("mode") == "invalid":
            self._notice("OpenWand could not verify the selected app action. Nothing was changed.", severity="warning")
            self._set_idle()
        elif routing.get("mode") == "file":
            self._run_action_file(pending, prompt, routing)
        elif routing.get("mode") == "addon":
            self._run_addon_intent(pending, prompt, routing)
        elif routing.get("mode") == "action":
            self._dispatch_provider_action(
                pending,
                prompt,
                routing,
                active_app=active_app,
                browser_app=browser_app,
                app_selection=app_selection,
                selected_text=selected_text,
            )
        elif routing.get("mode") == "answer":
            if not self._attach_provider_answer_context(
                pending,
                routing,
                active_app=active_app,
            ):
                self._set_idle()
                return
            caller = dict(pending.caller)
            caller["paste_back"] = False
            pending.caller = caller
            self._query(prompt, pending)
        elif self._is_calc_chart_action(prompt, app_selection):
            self._run_calc_chart_action(pending, app_selection)
        elif self._is_browser_form_action(prompt, browser_app):
            self._run_browser_form_action(pending, prompt, browser_app)
        elif self._is_vscode_fix_action(prompt, active_app, selected_text):
            self._run_vscode_fix_action(pending, prompt, active_app, selected_text)
        elif app_selection.get("app") == "libreoffice_calc" and pending.caller.get("paste_back"):
            # A structured cell range is never ordinary text paste-back. Until a
            # specific safe action owns the request, answer without touching Calc.
            caller = dict(pending.caller)
            caller["paste_back"] = False
            pending.caller = caller
            self._query(prompt, pending)
        elif pending.caller.get("paste_back") and self._is_local_file_request(prompt):
            caller = dict(pending.caller)
            caller["paste_back"] = False
            if tool_modes.local_file_access_mode(caller) in {"off", "read"}:
                caller["file_access"] = "ask"
            pending.caller = caller
            self._query(prompt, pending)
        elif pending.caller.get("paste_back"):
            self._rewrite_and_paste(prompt, pending)
        else:
            self._query(prompt, pending)

    @staticmethod
    def _validated_intent_routing(
        pending: PendingInvocation,
        value: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Resolve UI routing against the provider detected before the picker opened."""
        raw = value if isinstance(value, dict) else {}
        mode = str(raw.get("mode") or "legacy").strip().lower()
        if mode == "legacy":
            return {"mode": "legacy"}
        source = str(raw.get("source") or "")
        # Older UI workers labeled the freeform row "auto". Treat that legacy
        # payload as a direct answer too: a custom prompt never opts into an app
        # mutation merely because OpenWand detected a supported application.
        if mode == "auto" and source == "custom":
            return {"mode": "answer", "source": "custom"}
        if mode == "answer" and source in {"configured", "custom"}:
            return {"mode": "answer", "source": source}
        if mode == "file" and str(raw.get("source") or "") == "configured":
            action_name = str(raw.get("action_name") or "")
            caller_folder = str(raw.get("caller_folder") or "")
            if caller_folder != str(pending.caller.get("folder") or ""):
                return {"mode": "invalid"}
            intents = pending.caller.get("intents")
            trusted = next(
                (
                    item.get("action_file")
                    for item in intents
                    if isinstance(item, dict)
                    and isinstance(item.get("action_file"), dict)
                    and str(item["action_file"].get("name") or "") == action_name
                ),
                None,
            ) if isinstance(intents, list) else None
            if not isinstance(trusted, dict) or not bool(trusted.get("has_code")):
                return {"mode": "invalid"}
            return {"mode": "file", "source": "configured", "action_file": dict(trusted)}
        if mode == "addon" and str(raw.get("source") or "") == "addon":
            addon_id = str(raw.get("addon_id") or "").strip()
            action_id = str(raw.get("action_id") or "").strip()
            if not addon_id or not action_id:
                return {"mode": "invalid"}
            return {
                "mode": "addon",
                "source": "addon",
                "addon_id": addon_id,
                "action_id": action_id,
                "callback": bool(raw.get("callback")),
            }

        provider = pending.action_provider_context
        if not isinstance(provider, dict) or not provider:
            return {"mode": "invalid"}
        if str(raw.get("provider_id") or "") != str(provider.get("id") or ""):
            return {"mode": "invalid"}
        if str(raw.get("app") or "") != str(provider.get("app") or ""):
            return {"mode": "invalid"}
        suggestion_id = str(raw.get("suggestion_id") or "")
        suggestions = provider.get("suggested_intents")
        trusted = next(
            (
                item
                for item in suggestions if isinstance(item, dict)
                and str(item.get("id") or "") == suggestion_id
            ),
            None,
        ) if isinstance(suggestions, list) else None
        if not isinstance(trusted, dict):
            return {"mode": "invalid"}
        if not bool(trusted.get("available", True)):
            return {"mode": "invalid"}
        trusted_mode = str(trusted.get("mode") or "").strip().lower()
        if mode != trusted_mode or mode not in {"action", "answer", "file"}:
            return {"mode": "invalid"}
        for routing_field in ("capability_type", "planning_tool"):
            if str(raw.get(routing_field) or "") != str(trusted.get(routing_field) or ""):
                return {"mode": "invalid"}
        result: dict[str, Any] = {
            "mode": trusted_mode,
            "source": "provider",
            "provider_id": str(provider.get("id") or ""),
            "app": str(provider.get("app") or ""),
            "suggestion_id": suggestion_id,
            "capability_type": str(trusted.get("capability_type") or ""),
            "planning_tool": str(trusted.get("planning_tool") or ""),
        }
        if trusted_mode == "file":
            action_file = trusted.get("action_file")
            if not isinstance(action_file, dict) or not bool(action_file.get("has_code")):
                return {"mode": "invalid"}
            result["action_file"] = dict(action_file)
        return result

    def _attach_provider_answer_context(
        self,
        pending: PendingInvocation,
        routing: dict[str, Any],
        *,
        active_app: dict[str, Any],
    ) -> bool:
        """Attach the exact selected cells required by spreadsheet answer actions."""
        if str(routing.get("source") or "") != "provider":
            return True
        provider_id = str(routing.get("provider_id") or "")
        document_providers = {
            "word_desktop": "Word",
            "libreoffice_writer": "Writer",
            "powerpoint_desktop": "PowerPoint",
            "libreoffice_impress": "Impress",
        }
        if provider_id in document_providers:
            context = pending.context if isinstance(pending.context, dict) else {}
            # Provider rows describe the document that was active at hotkey
            # time. Never reuse ambient context that may contain several open
            # documents: re-read only that exact captured window.
            document_text = self._fetch_active_document_text(context, active_only=True)
            if document_text:
                context["active_document_text"] = document_text
                context["_active_document_text_full"] = document_text
            else:
                context.pop("active_document_text", None)
                context.pop("_active_document_text_full", None)
            selected_text = str(context.get("selected_text") or "").strip()
            if not document_text and not selected_text:
                app_name = document_providers[provider_id]
                self._notice(
                    f"OpenWand couldn't read the active {app_name} document. "
                    f"Return to {app_name}, select some text, and try again.",
                    severity="warning",
                )
                return False
            pending.context = context
            caller = dict(pending.caller)
            caller["context_ambient"] = True
            caller["context_documents_mode"] = "auto"
            caller["paste_back"] = False
            pending.caller = caller
            return True
        browser_document_providers = {
            "powerpoint_web": "PowerPoint for the web",
            "google_slides": "Google Slides",
            "google_docs": "Google Docs",
        }
        if provider_id in browser_document_providers:
            context = pending.context if isinstance(pending.context, dict) else {}
            browser_content = str(context.get("browser_content") or "").strip()
            if not browser_content:
                browser = self._fetch_browser_content_for_context(context)
                if browser.get("browser_url"):
                    context["browser_url"] = browser["browser_url"]
                browser_content = str(browser.get("browser_content") or "").strip()
                if browser_content:
                    context["browser_content"] = browser_content
            selected_text = str(context.get("selected_text") or "").strip()
            if not browser_content and not selected_text:
                product = browser_document_providers[provider_id]
                self._notice(
                    f"OpenWand couldn't read the active {product} slide text. "
                    "Select the relevant slide text and try again.",
                    severity="warning",
                )
                return False
            pending.context = context
            caller = dict(pending.caller)
            caller["context_browser_mode"] = "auto"
            caller["paste_back"] = False
            pending.caller = caller
            return True
        if provider_id not in {"excel", "libreoffice_calc"}:
            return True

        try:
            if provider_id == "excel":
                from core.actions.adapters.excel import ExcelRuntimeProvider

                provider = ExcelRuntimeProvider()
                selection = provider.answer_context(provider.snapshot({"active_app": active_app}))
            else:
                response = self.native.call(
                    "native.action.calc.snapshot",
                    {"active_app": active_app},
                    timeout=12.0,
                ) or {}
                selection = response.get("selection") if isinstance(response, dict) else None
                if not bool(response.get("ok")) or not isinstance(selection, dict) or not selection:
                    raise RuntimeError(str(response.get("error") or "Calc returned no selected cells."))
                selection = dict(selection)
                selection["selected_text"] = self._calc_answer_selection_text(selection)
        except Exception as exc:  # noqa: BLE001 - optional app readers fail at a user-visible boundary
            app_name = "Excel" if provider_id == "excel" else "Calc"
            log.warning("could not attach %s answer context: %s", app_name, exc)
            self._notice(
                f"OpenWand couldn't read the selected {app_name} cells. "
                f"Return to {app_name}, select the range, and try again.",
                severity="warning",
            )
            return False

        selected_text = str(selection.get("selected_text") or "").strip()
        if not selected_text:
            self._notice(
                "OpenWand couldn't read any selected spreadsheet cells. Select a non-empty range and try again.",
                severity="warning",
            )
            return False
        context = pending.context if isinstance(pending.context, dict) else {}
        context["app_selection"] = selection
        context["selected_text"] = selected_text
        context["app_selection_deferred"] = False
        pending.context = context
        caller = dict(pending.caller)
        # Choosing a provider action carrying Text access is the user's explicit
        # request to attach that app-owned selection, even when the generic
        # Selection chip was unavailable while the overlay held focus.
        caller["_context_selection_enabled"] = True
        caller["paste_back"] = False
        pending.caller = caller
        return True

    @staticmethod
    def _calc_answer_selection_text(selection: dict[str, Any]) -> str:
        """Render bounded Calc values and formulas without treating formulas as display text."""
        values = selection.get("values") if isinstance(selection.get("values"), list | tuple) else ()
        formulas = selection.get("formulas") if isinstance(selection.get("formulas"), list | tuple) else ()
        displayed = str(selection.get("selected_text") or "").strip()
        if not displayed:
            displayed = "\n".join(
                "\t".join(str(cell) for cell in row)
                for row in values
                if isinstance(row, list | tuple)
            )
        formula_lines = [
            "\t".join(
                str(cell) if str(cell).startswith("=") else ""
                for cell in row
            )
            for row in formulas
            if isinstance(row, list | tuple)
        ]
        text = (
            "[LibreOffice Calc selected cells]\n"
            f"Range: {selection.get('range') or 'unknown'}\n"
            f"Rows: {selection.get('rows') or len(values)}; "
            f"Columns: {selection.get('columns') or (len(values[0]) if values else 0)}\n"
            "Displayed values (tab-separated):\n"
            f"{displayed}\n"
            "Formulas at the same positions (blank means the cell is not a formula):\n"
            + "\n".join(formula_lines)
        ).strip()
        limit = 20_000
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "\n[Selection context truncated to 20,000 characters.]"

    def _run_addon_intent(
        self,
        pending: PendingInvocation,
        prompt: str,
        routing: dict[str, Any],
    ) -> None:
        """Run a declared addon callback or submit its prompt normally."""
        if not bool(routing.get("callback")):
            self._query(prompt, pending)
            return
        result = self._safe_call(
            self.brain,
            "brain.addons.run_intent",
            {
                "addon_id": str(routing.get("addon_id") or ""),
                "action_id": str(routing.get("action_id") or ""),
                "payload": {
                    "caller_idx": pending.caller_idx,
                    "context": pending.context if isinstance(pending.context, dict) else {},
                },
            },
            timeout=60.0,
        )
        if isinstance(result, dict) and str(result.get("prompt") or "").strip():
            self._query(str(result["prompt"]).strip(), pending)
            return
        message = str(result.get("message") or "Addon action finished.") if isinstance(result, dict) else "Addon action finished."
        self._notice(message)
        self._set_idle()

    def _dispatch_provider_action(
        self,
        pending: PendingInvocation,
        prompt: str,
        routing: dict[str, str],
        *,
        active_app: dict[str, Any],
        browser_app: dict[str, Any],
        app_selection: dict[str, Any],
        selected_text: str,
    ) -> None:
        """Run one server-verified provider capability without keyword guessing."""
        capability_type = str(routing.get("capability_type") or "")
        planning_tool = str(routing.get("planning_tool") or "")
        if capability_type == "browser.fill_form":
            self._run_browser_form_action(
                pending,
                prompt,
                browser_app,
                planning_tool=planning_tool,
            )
            return
        if capability_type == "vscode.replace_selection@1":
            self._run_vscode_fix_action(
                pending,
                prompt,
                active_app,
                selected_text,
                planning_tool=planning_tool,
            )
            return
        if capability_type in {
            "calc.add_chart@1",
            "calc.clean_range@1",
            "calc.format_table@1",
            "calc.sort_range@1",
        }:
            self._run_calc_chart_action(
                pending,
                app_selection,
                prompt=prompt,
                planning_tool=planning_tool,
                capability_type=capability_type,
            )
            return
        if capability_type in {
            "excel.add_chart@1",
            "excel.clean_range@1",
            "excel.create_table@1",
            "excel.sort_range@1",
        }:
            self._run_excel_action(
                pending,
                prompt,
                planning_tool=planning_tool,
                capability_type=capability_type,
                provider_id=str(routing.get("provider_id") or ""),
            )
            return
        if capability_type.startswith("presentation."):
            self._run_powerpoint_action(
                pending,
                prompt,
                planning_tool=planning_tool,
                capability_type=capability_type,
                provider_id=str(routing.get("provider_id") or ""),
            )
            return
        self._notice("This app action is not available in the current OpenWand build.", severity="warning")
        self._set_idle()

    def _run_powerpoint_action(
        self,
        pending: PendingInvocation,
        prompt: str,
        *,
        planning_tool: str,
        capability_type: str,
        provider_id: str,
    ) -> None:
        """Run PowerPoint through the shared preview-first ActionRunner."""
        from core.actions.adapters.presentation import PowerPointDesktopRuntimeProvider

        self._run_typed_desktop_action(
            pending,
            prompt,
            planning_tool=planning_tool,
            capability_type=capability_type,
            provider_id=provider_id,
            runtime_provider=PowerPointDesktopRuntimeProvider(),
            product_name="PowerPoint",
            trace_app="presentation",
        )

    def _run_excel_action(
        self,
        pending: PendingInvocation,
        prompt: str,
        *,
        planning_tool: str,
        capability_type: str,
        provider_id: str,
    ) -> None:
        """Run Excel through the shared preview-first ActionRunner."""
        from core.actions.adapters.excel import ExcelRuntimeProvider

        self._run_typed_desktop_action(
            pending,
            prompt,
            planning_tool=planning_tool,
            capability_type=capability_type,
            provider_id=provider_id,
            runtime_provider=ExcelRuntimeProvider(),
            product_name="Excel",
            trace_app="excel",
        )

    def _run_typed_desktop_action(
        self,
        pending: PendingInvocation,
        prompt: str,
        *,
        planning_tool: str,
        capability_type: str,
        provider_id: str,
        runtime_provider: Any,
        product_name: str,
        trace_app: str,
    ) -> None:
        """Run one desktop provider through the common preview-first boundary."""
        from core.actions.runner import ActionRunner, ActionRuntimeProviderRegistry, PlannedToolCall

        trace = ActionTrace(
            capability_type,
            app=trace_app,
            started_unix_ns=pending.invoked_at_unix_ns,
        )
        gen = self._new_generation()
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        self._safe_call(self.ui, "ui.reply.thinking", timeout=30.0)

        def publish(update: ActionProgressUpdate) -> None:
            self._safe_call(self.ui, "ui.action.progress", update.to_dict(), timeout=30.0)

        def record(update: ActionProgressUpdate) -> None:
            trace.mark(
                "progress_updated",
                progress_stage=update.stage,
                progress_sequence=update.sequence,
                terminal=update.terminal,
            )

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            if event == "privacy.review.request":
                self._handle_privacy_review_request(payload)

        def plan_with_model(**kwargs: Any) -> PlannedToolCall:
            result = self._brain_reply_call_with_events(
                "brain.action.plan",
                {
                    "planning_tool_name": kwargs["tool_name"],
                    "planning_tool_description": kwargs["tool_description"],
                    "input_schema": kwargs["input_schema"],
                    "user_prompt": kwargs["user_prompt"],
                    "app_context": kwargs["app_context"],
                },
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=on_event,
                generation=gen,
            )
            if not isinstance(result, dict):
                return PlannedToolCall(tool_name="", arguments={})
            arguments = result.get("arguments")
            return PlannedToolCall(
                tool_name=str(result.get("tool_name") or ""),
                arguments=dict(arguments) if isinstance(arguments, dict) else {},
                visible_text=str(result.get("visible_text") or ""),
            )

        def approve(preview: Any) -> bool:
            self._set_idle()
            decision = self._safe_call(
                self.ui,
                "ui.action.preview.request",
                {
                    "plan_id": preview.plan_id,
                    "title": preview.title,
                    "summary": preview.summary,
                    "html": preview.html,
                    "details": list(preview.details),
                    "warnings": list(preview.warnings),
                },
                timeout=300.0,
            )
            approved = isinstance(decision, dict) and bool(decision.get("approved"))
            if approved:
                self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
            return approved

        runner = ActionRunner(
            ActionRuntimeProviderRegistry((runtime_provider,)),
            planner=plan_with_model,
            approver=approve,
            progress_sink=publish,
            telemetry_sink=record,
            planning_warning_seconds=_ACTION_PROGRESS_HEADS_UP_SECONDS,
        )
        try:
            outcome = runner.run(
                context=pending.context if isinstance(pending.context, dict) else {},
                user_prompt=prompt,
                capability_type=capability_type,
                planning_tool_name=planning_tool,
                provider_id=provider_id,
                idempotency_key=f"{pending.invoked_at_unix_ns}:{capability_type}",
            )
        except Exception as exc:  # noqa: BLE001 - runner owns mutation rollback
            trace.finish("failed", error_type=type(exc).__name__)
            if self._is_current(gen):
                self._notice(
                    f"OpenWand couldn't apply the {product_name} action: {self._friendly_error(exc)}",
                    severity="warning",
                )
                self._set_idle()
            return
        if not self._is_current(gen):
            self._set_idle()
            return
        if outcome.status == "cancelled":
            trace.finish("cancelled", failure_stage="preview_decision")
            self._notice(f"{product_name} action cancelled. Nothing was changed.")
        else:
            result = outcome.result
            trace.finish("applied", result_status=result.status if result is not None else "applied")
            self._status_notice(
                result.message if result is not None else f"{product_name} action applied and verified."
            )
        self._set_idle()

    def _run_action_file(
        self,
        pending: PendingInvocation,
        prompt: str,
        routing: dict[str, Any],
    ) -> None:
        """Confirm, isolate, and run one trusted code-backed action file."""
        from html import escape

        from core.action_files.execution import action_from_dict, run_action_script

        raw_action = routing.get("action_file")
        if not isinstance(raw_action, dict):
            self._notice("OpenWand could not verify the selected action file. Nothing ran.", severity="warning")
            self._set_idle()
            return
        action = action_from_dict(raw_action)
        access = ", ".join(item.value.title() for item in action.access) or "No access declared"
        self._set_idle()
        decision = self._safe_call(
            self.ui,
            "ui.action.preview.request",
            {
                "plan_id": f"action-file:{action.name}:{pending.invoked_at_unix_ns}",
                "title": f"Run {action.label}?",
                "summary": action.hint or "This action can run code on your computer.",
                "html": (
                    '<div class="action-focus-preview">'
                    f"<h2>{escape(action.label)}</h2>"
                    f"<p>{escape(action.hint or 'This action can run code on your computer.')}</p>"
                    f"<p><strong>Declared access:</strong> {escape(access)}</p>"
                    "<p>Review the action file before approving code you do not trust.</p>"
                    "</div>"
                ),
                "details": [{"type": "action_file", "label": action.label, "path": action.path}],
                "warnings": ["Action-file access is self-declared and is not sandboxed."],
            },
            timeout=300.0,
        )
        if not (isinstance(decision, dict) and bool(decision.get("approved"))):
            self._notice(f"{action.label} cancelled. Nothing ran.")
            self._set_idle()
            return

        gen = self._new_generation()
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        self._safe_call(self.ui, "ui.reply.thinking", timeout=30.0)

        def model_response(model_prompt: str) -> str:
            def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
                if event == "privacy.review.request":
                    self._handle_privacy_review_request(payload)

            result = self._brain_reply_call_with_events(
                "brain.query",
                self._brain_query_params(model_prompt, pending),
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=on_event,
                generation=gen,
            )
            return str((result or {}).get("text") or "") if isinstance(result, dict) else ""

        try:
            if action.run_script_first:
                first = run_action_script(action, context=pending.context, prompt=prompt)
                if first.prompt:
                    reply = model_response(first.prompt)
                    result = run_action_script(
                        action,
                        context=pending.context,
                        prompt=first.prompt,
                        model_response=reply,
                    )
                    output = result.output or reply
                else:
                    result = first
                    output = first.output
            else:
                reply = model_response(prompt or action.prompt)
                result = run_action_script(
                    action,
                    context=pending.context,
                    prompt=prompt or action.prompt,
                    model_response=reply,
                )
                output = result.output or reply
        except Exception as exc:  # noqa: BLE001 - isolated failure is user-facing
            log.exception("action file failed: %s", action.path)
            if self._is_current(gen):
                self._notice(
                    f"OpenWand couldn't run {action.label}: {self._friendly_error(exc)}",
                    severity="warning",
                )
                self._set_idle()
            return

        if not self._is_current(gen):
            return
        paste_back = result.paste_back
        if paste_back is None:
            paste_back = action.paste_back
        if paste_back is None:
            paste_back = bool(pending.caller.get("paste_back"))
        if paste_back and output:
            paste = self.native.call(
                "native.paste_text",
                {
                    "text": output,
                    "target_pid": pending.paste_target_pid,
                    "focus_token": int(pending.context.get("focus_token") or 0),
                    "restore_clipboard": True,
                },
                timeout=30.0,
            )
            if not (isinstance(paste, dict) and paste.get("ok")):
                self._notice("The action finished, but OpenWand could not paste its output.", severity="warning")
        elif output:
            self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
            self._safe_call(self.ui, "ui.reply.chunk", {"text": output}, timeout=30.0)
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
        else:
            self._status_notice(f"{action.label} completed.")
        self._set_idle()

    @staticmethod
    def _is_browser_form_action(prompt: str, active_app: dict[str, Any]) -> bool:
        """Recognize an explicit request to fill existing fields in a managed browser."""
        try:
            from core.actions.adapters.browser import is_browser_app

            if not is_browser_app(active_app):
                return False
        except Exception:
            return False
        text = " ".join(str(prompt or "").casefold().split())
        if not text or len(text) > 1_000:
            return False
        verbs = {
            "fill", "fill in", "fill out", "complete", "enter", "populate", "set", "type",
            "填寫", "填入", "填表", "填写", "輸入", "输入",
        }
        objects = {
            "form", "field", "fields", "details", "information", "application", "survey",
            "表單", "表格", "欄位", "字段", "資料", "信息",
        }
        return any(verb in text for verb in verbs) and (
            any(noun in text for noun in objects) or " with " in text or ":" in text
        )

    def _run_browser_form_action(
        self,
        pending: PendingInvocation,
        prompt: str,
        active_app: dict[str, Any],
        *,
        planning_tool: str = "browser_plan_fill_form",
    ) -> None:
        """Plan, preview, fill, and verify safe fields without submitting the page."""
        trace = ActionTrace(
            "browser.fill_form",
            app="browser",
            started_unix_ns=pending.invoked_at_unix_ns,
        )
        progress = self._new_action_progress("browser.fill_form", app="browser", trace=trace)
        gen = self._new_generation()
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        self._safe_call(self.ui, "ui.reply.thinking", timeout=30.0)
        progress.advance(
            ActionProgressStage.READING,
            "Reading safe editable fields from the current browser page...",
        )
        trace.mark("snapshot_requested")
        snapshot_response = self._safe_call(
            self.native,
            "native.action.browser.form_snapshot",
            {"active_app": active_app},
            timeout=5.0,
        )
        snapshot_value = (
            snapshot_response.get("snapshot")
            if isinstance(snapshot_response, dict) and isinstance(snapshot_response.get("snapshot"), dict)
            else {}
        )
        if not isinstance(snapshot_response, dict) or not snapshot_response.get("ok") or not snapshot_value:
            error = str((snapshot_response or {}).get("error") or "The current page could not be inspected.")
            progress.advance(ActionProgressStage.FAILED, "The browser page is not ready for a safe action.")
            trace.finish("failed", failure_stage="snapshot", error_type=error.split(":", 1)[0][:80])
            self._notice(
                "OpenWand could not inspect this page through its private browser API. "
                "Reopen Chrome through OpenWand control and try again.",
                severity="warning",
                technical_detail=error,
            )
            self._set_idle()
            return
        try:
            from core.actions.adapters.browser import (
                BrowserActionAdapter,
                BrowserFormSnapshot,
                browser_capabilities,
                build_fill_form_plan,
            )

            snapshot = BrowserFormSnapshot.from_dict(snapshot_value)
        except Exception as exc:  # noqa: BLE001 - malformed native state must never reach Apply
            progress.advance(ActionProgressStage.FAILED, "The browser fields did not pass safety checks.")
            trace.finish("failed", failure_stage="snapshot_validation", error_type=type(exc).__name__)
            self._notice(f"OpenWand couldn't prepare this browser action: {exc}", severity="warning")
            self._set_idle()
            return

        progress.advance(
            ActionProgressStage.PLANNING,
            f"Found {len(snapshot.fields)} safe field(s). Drafting the exact values to fill...",
        )
        model_finished = threading.Event()
        heads_up = self._start_action_progress_heads_up(
            gen,
            model_finished,
            progress,
            ActionProgressStage.PLANNING,
            "The model is still matching your request to the page fields; this may take a few more seconds.",
        )

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            if event == "privacy.review.request":
                self._handle_privacy_review_request(payload)

        model_instruction = (
            f"{prompt}\n\n"
            "Fill the forced planning tool with exact field assignments. Use only field_id values present in the "
            "snapshot. Omit fields the user did not ask to change. Select values must exactly match one provided "
            "option. Never submit, click, navigate, or propose any operation outside this tool."
        )
        try:
            trace.mark("model_requested", field_count=len(snapshot.fields))
            result = self._brain_reply_call_with_events(
                "brain.action.plan",
                {
                    "planning_tool_name": planning_tool,
                    "planning_tool_description": (
                        "Plan exact values for the current safe browser fields. This tool can only fill reviewed "
                        "fields and cannot submit, click, or navigate."
                    ),
                    "input_schema": browser_capabilities()[0].input_schema,
                    "user_prompt": model_instruction,
                    "app_context": snapshot.model_context(),
                },
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=on_event,
                generation=gen,
            )
        except Exception as exc:  # noqa: BLE001 - no page mutation occurred
            progress.advance(ActionProgressStage.FAILED, "The form values could not be drafted.")
            trace.finish("failed", failure_stage="model", error_type=type(exc).__name__)
            if self._is_current(gen):
                self._notice(f"Browser form action failed: {self._friendly_error(exc)}", severity="error")
                self._set_idle()
            return
        finally:
            model_finished.set()
            heads_up.cancel()
        if not self._is_current(gen):
            progress.advance(ActionProgressStage.CANCELLED, "This browser action was replaced by a newer request.")
            trace.finish("superseded", failure_stage="after_model")
            self._set_idle()
            return

        progress.advance(
            ActionProgressStage.VALIDATING,
            "Draft received. Checking every field, value, and page boundary...",
        )
        try:
            if not isinstance(result, dict) or str(result.get("tool_name") or "") != planning_tool:
                raise ValueError("The model did not return the required browser planning tool.")
            planned_arguments = (
                result.get("arguments") if isinstance(result, dict) and isinstance(result.get("arguments"), dict)
                else {}
            )
            assignments = planned_arguments.get("assignments")
            if not isinstance(assignments, list):
                raise ValueError("The model did not return a form assignment list.")
            summary = str((result or {}).get("visible_text") or "").strip()
            plan = build_fill_form_plan(
                snapshot,
                assignments,
                summary=summary or f"Fill {len(assignments)} field(s) on {snapshot.title}",
            )
            progress.advance(
                ActionProgressStage.PREPARING_PREVIEW,
                "Safety checks passed. Building the exact field-by-field preview...",
            )
            preview = BrowserActionAdapter().render_preview(plan, snapshot)
        except Exception as exc:  # noqa: BLE001 - invalid model output never reaches the page
            progress.advance(ActionProgressStage.FAILED, "The proposed values could not form a safe browser action.")
            trace.finish("failed", failure_stage="preview_build", error_type=type(exc).__name__)
            self._notice(f"OpenWand couldn't build a safe form preview: {exc}", severity="warning")
            self._set_idle()
            return

        progress.advance(
            ActionProgressStage.AWAITING_APPROVAL,
            preview.summary,
        )
        self._set_idle()
        decision = self._safe_call(
            self.ui,
            "ui.action.preview.request",
            {
                "plan_id": preview.plan_id,
                "title": preview.title,
                "summary": preview.summary,
                "html": preview.html,
                "details": list(preview.details),
                "warnings": list(preview.warnings),
            },
            timeout=300.0,
        )
        if not isinstance(decision, dict) or not decision.get("approved"):
            progress.advance(ActionProgressStage.CANCELLED, "Browser form action cancelled. Nothing was changed.")
            trace.finish("cancelled", failure_stage="preview_decision")
            self._notice("Browser form action cancelled. Nothing was changed.")
            self._set_idle()
            return

        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        progress.advance(
            ActionProgressStage.APPLYING,
            "Rechecking the page, filling the reviewed fields, and verifying every value...",
        )
        response = self._safe_call(
            self.native,
            "native.action.browser.form_apply",
            {
                "plan": plan.to_dict(),
                "confirmed": True,
                "idempotency_key": f"{plan.plan_id}:apply",
            },
            timeout=15.0,
        )
        if isinstance(response, dict) and response.get("ok"):
            applied = response.get("result") if isinstance(response.get("result"), dict) else {}
            progress.advance(ActionProgressStage.COMPLETE, "Reviewed browser fields filled and verified.")
            trace.finish("applied", result_status=str(applied.get("status") or "applied"))
            self._status_notice(str(applied.get("message") or "Filled the browser form without submitting it."))
            self._set_idle()
            return
        error = str((response or {}).get("error") or "The browser did not verify the reviewed field values.")
        progress.advance(ActionProgressStage.FAILED, "The reviewed browser action could not be verified.")
        trace.finish("failed", failure_stage="apply", error_type=error.split(":", 1)[0][:80])
        self._notice(f"OpenWand couldn't fill the browser form: {error}", severity="warning")
        self._set_idle()

    @staticmethod
    def _is_calc_chart_action(prompt: str, app_selection: dict[str, Any]) -> bool:
        """Recognize the narrow local fast path OpenWand can execute exactly."""
        if app_selection.get("app") != "libreoffice_calc":
            return False
        text = " ".join(str(prompt or "").casefold().split())
        if not text or len(text) > 220:
            return False
        unsupported = {
            "pie", "line chart", "scatter", "area chart", "donut", "doughnut",
            "\u9905\u5716", "\u997c\u56fe", "\u6298\u7dda", "\u6298\u7ebf", "\u6563\u9ede", "\u6563\u70b9",
        }
        if any(token in text for token in unsupported):
            return False
        nouns = {"chart", "graph", "plot", "\u5716\u8868", "\u56fe\u8868", "\u5716\u5f62", "\u56fe\u5f62"}
        verbs = {
            "create", "add", "insert", "make", "draw", "plot", "generate", "build", "chart", "graph",
            "\u5efa\u7acb", "\u65b0\u589e", "\u63d2\u5165", "\u88fd\u4f5c", "\u521b\u5efa", "\u6dfb\u52a0",
        }
        return any(token in text for token in nouns) and any(token in text for token in verbs)

    def _run_calc_chart_action(
        self,
        pending: PendingInvocation,
        selection: dict[str, Any],
        *,
        prompt: str = "Create a vertical bar chart from the selected cells.",
        planning_tool: str = "calc_plan_add_chart",
        capability_type: str = "calc.add_chart@1",
    ) -> None:
        """Preview, confirm, execute, and report one bounded Calc action."""
        action_key = capability_type.removesuffix("@1")
        trace = ActionTrace(
            action_key,
            app="libreoffice_calc",
            started_unix_ns=pending.invoked_at_unix_ns,
        )
        progress = self._new_action_progress(action_key, app="libreoffice_calc", trace=trace)
        gen = self._new_generation()
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        self._safe_call(self.ui, "ui.reply.thinking", timeout=30.0)
        progress.advance(
            ActionProgressStage.TARGETING,
            "Checking the active Calc sheet and action connection...",
        )
        connection_finished = threading.Event()
        connection_heads_up = self._start_action_progress_heads_up(
            gen,
            connection_finished,
            progress,
            ActionProgressStage.TARGETING,
            "Calc is still starting its private action connection; OpenWand is waiting without taking focus.",
        )
        try:
            status = self._safe_call(self.native, "native.action.calc.status", {}, timeout=25.0)
        finally:
            connection_finished.set()
            connection_heads_up.cancel()
        if isinstance(status, dict) and status.get("available") is False:
            reason = str(status.get("reason") or "")
            progress.advance(ActionProgressStage.FAILED, "Calc is not ready for a background action.")
            trace.finish("failed", failure_stage="availability", error_type=reason[:80])
            if reason == "bridge_pending_restart":
                self._notice(
                    "OpenWand installed its Calc action connection, but this LibreOffice process was already running "
                    "before that one-time integration update. Reopen LibreOffice once to load it. After that, "
                    "Calc and OpenWand can be opened in either order.",
                    severity="warning",
                )
            else:
                self._notice(
                    "Focusless Calc actions are not available in this session. OpenWand did not change the spreadsheet.",
                    severity="warning",
                )
            self._set_idle()
            return
        progress.advance(
            ActionProgressStage.READING,
            "Reading the selected Calc range and checking its current values...",
        )
        active_app = (
            pending.context.get("active_app")
            if isinstance(pending.context, dict)
            and isinstance(pending.context.get("active_app"), dict)
            else {}
        )
        app_result = self._safe_call(
            self.native,
            "native.action.calc.snapshot",
            {"active_app": active_app},
            timeout=12.0,
        ) or {}
        selection = (
            app_result.get("selection")
            if isinstance(app_result, dict) and isinstance(app_result.get("selection"), dict)
            else {}
        )
        if not selection:
            error = str(
                app_result.get("error")
                if isinstance(app_result, dict)
                else ""
            ).strip() or "Calc did not return a readable selected range."
            progress.advance(ActionProgressStage.FAILED, "The selected Calc range could not be read.")
            trace.finish("failed", failure_stage="snapshot", error_type=error.split(":", 1)[0][:80])
            self._notice(
                f"OpenWand couldn't read the selected Calc cells: {error}",
                severity="warning",
            )
            self._set_idle()
            return
        if isinstance(pending.context, dict):
            pending.context["app_selection"] = selection
            pending.context["selected_text"] = str(selection.get("selected_text") or "")
            pending.context["app_selection_deferred"] = False
        try:
            from core.actions.adapters.calc import (
                CalcActionAdapter,
                CalcSnapshot,
                build_chart_plan,
                build_cleanup_plan,
                build_format_table_plan,
                build_sort_range_plan,
            )

            snapshot = CalcSnapshot.from_selection(selection)
            if capability_type == "calc.add_chart@1":
                planning_description = (
                    "Plan one vertical bar chart title for the captured Calc selection. The range and chart kind "
                    "are fixed by OpenWand and this tool cannot edit cells or create other objects."
                )
                planning_schema = {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "maxLength": 120,
                            "description": "A concise title for the vertical bar chart.",
                        },
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                }
                planning_progress = "Selection checked. Drafting the exact chart title and operation..."
            elif capability_type == "calc.format_table@1":
                planning_description = (
                    "Plan the registered clean-table formatting operation for the captured Calc selection. "
                    "Choose whether the first selected row is a header. The operation cannot change cell contents."
                )
                planning_schema = {
                    "type": "object",
                    "properties": {
                        "has_header": {
                            "type": "boolean",
                            "description": "True only when the first selected row contains column headings.",
                        },
                    },
                    "required": ["has_header"],
                    "additionalProperties": False,
                }
                planning_progress = "Selection checked. Planning the exact formatting-only changes..."
            elif capability_type == "calc.sort_range@1":
                headers = [str(value).strip() for value in snapshot.values[0] if str(value).strip()]
                unique_headers = [header for header in headers if headers.count(header) == 1]
                if not unique_headers:
                    raise ValueError("Sorting requires at least one unique, non-empty header in the first selected row.")
                planning_description = (
                    "Plan one row sort for the captured Calc selection. Choose one exact available header and a "
                    "direction. OpenWand keeps the header fixed and always moves complete rows together."
                )
                planning_schema = {
                    "type": "object",
                    "properties": {
                        "column_header": {
                            "type": "string",
                            "enum": unique_headers,
                            "description": "The exact selected header to sort by.",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["ascending", "descending"],
                        },
                    },
                    "required": ["column_header", "direction"],
                    "additionalProperties": False,
                }
                planning_progress = "Selection checked. Choosing the exact sort column and direction..."
            elif capability_type == "calc.clean_range@1":
                planning_description = (
                    "Propose only concrete, unambiguous cleanup replacements inside the captured Calc range. "
                    "Use zero-based row and column offsets. Preserve formulas unless a formula replacement is "
                    "explicitly necessary; replacing a formula with a value requires replace_formula=true. "
                    "Return between 1 and 32 changes and leave ambiguous cells unchanged."
                )
                planning_schema = {
                    "type": "object",
                    "properties": {
                        "changes": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 32,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "row_offset": {"type": "integer", "minimum": 0},
                                    "column_offset": {"type": "integer", "minimum": 0},
                                    "after_kind": {"type": "string", "enum": ["value", "formula"]},
                                    "after_value": {},
                                    "replace_formula": {"type": "boolean"},
                                },
                                "required": ["row_offset", "column_offset", "after_kind", "after_value"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["changes"],
                    "additionalProperties": False,
                }
                planning_progress = "Selection checked. Drafting exact cell-by-cell cleanup replacements..."
            else:
                raise ValueError("This Calc operation is not registered.")
            progress.advance(
                ActionProgressStage.PLANNING,
                planning_progress,
            )
            model_finished = threading.Event()
            heads_up = self._start_action_progress_heads_up(
                gen,
                model_finished,
                progress,
                ActionProgressStage.PLANNING,
                "The model is still preparing the Calc operation; this may take a few more seconds.",
            )

            def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
                if event == "privacy.review.request":
                    self._handle_privacy_review_request(payload)

            try:
                result = self._brain_reply_call_with_events(
                    "brain.action.plan",
                    {
                        "planning_tool_name": planning_tool,
                        "planning_tool_description": planning_description,
                        "input_schema": planning_schema,
                        "user_prompt": prompt,
                        "app_context": {
                            "document_title": snapshot.document_title,
                            "selection_address": snapshot.selection_address,
                            "row_count": snapshot.row_count,
                            "column_count": snapshot.column_count,
                            "values": [list(row) for row in snapshot.values],
                            "typed_values": [list(row) for row in snapshot.typed_values],
                            "formulas": [list(row) for row in snapshot.formulas],
                        },
                    },
                    timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                    on_event=on_event,
                    generation=gen,
                )
            finally:
                model_finished.set()
                heads_up.cancel()
            if not isinstance(result, dict) or str(result.get("tool_name") or "") != planning_tool:
                raise ValueError("The model did not return the required Calc planning tool.")
            planned_arguments = (
                result.get("arguments") if isinstance(result, dict) and isinstance(result.get("arguments"), dict)
                else {}
            )
            if capability_type == "calc.add_chart@1":
                chart_title = str(planned_arguments.get("title") or "").strip()
                if not chart_title:
                    raise ValueError("The model did not return a chart title.")
                plan = build_chart_plan(snapshot, title=chart_title)
            elif capability_type == "calc.format_table@1":
                if not isinstance(planned_arguments.get("has_header"), bool):
                    raise ValueError("The model did not identify whether the selection has a header row.")
                plan = build_format_table_plan(snapshot, has_header=bool(planned_arguments["has_header"]))
            elif capability_type == "calc.sort_range@1":
                plan = build_sort_range_plan(
                    snapshot,
                    column_label=str(planned_arguments.get("column_header") or ""),
                    direction=str(planned_arguments.get("direction") or ""),
                )
            else:
                changes = planned_arguments.get("changes")
                if not isinstance(changes, list):
                    raise ValueError("The model did not return structured Calc cleanup changes.")
                plan = build_cleanup_plan(snapshot, changes)
            progress.advance(
                ActionProgressStage.VALIDATING,
                "Selection checked. Validating the exact Calc operation...",
            )
            progress.advance(
                ActionProgressStage.PREPARING_PREVIEW,
                "Building the exact preview from the validated range...",
            )
            preview = CalcActionAdapter().render_preview(plan, snapshot)
        except Exception as exc:  # noqa: BLE001 - keep malformed app state away from paste-back
            progress.advance(ActionProgressStage.FAILED, "The Calc action preview could not be prepared.")
            trace.finish("failed", failure_stage="preview_build", error_type=type(exc).__name__)
            self._notice(f"OpenWand couldn't prepare the Calc action preview: {exc}", severity="warning")
            self._set_idle()
            return

        progress.advance(
            ActionProgressStage.AWAITING_APPROVAL,
            preview.summary,
        )
        decision = self._safe_call(
            self.ui,
            "ui.action.preview.request",
            {
                "plan_id": preview.plan_id,
                "title": preview.title,
                "summary": preview.summary,
                "html": preview.html,
                "details": list(preview.details),
                "warnings": list(preview.warnings),
            },
            timeout=300.0,
        )
        if not isinstance(decision, dict) or not decision.get("approved"):
            progress.advance(ActionProgressStage.CANCELLED, "Calc action cancelled. Nothing was changed.")
            trace.finish("cancelled", failure_stage="preview_decision")
            self._notice("Calc action cancelled. Nothing was changed.")
            self._set_idle()
            return

        self._notice("Applying the reviewed action in Calc...")
        progress.advance(
            ActionProgressStage.APPLYING,
            "Rechecking the range, applying the reviewed action, and verifying the result...",
        )
        response = self._safe_call(
            self.native,
            "native.action.calc.apply",
            {
                "plan": plan.to_dict(),
                "confirmed": True,
                "idempotency_key": f"{plan.plan_id}:apply",
            },
            timeout=15.0,
        )
        if isinstance(response, dict) and response.get("ok"):
            result = response.get("result") if isinstance(response.get("result"), dict) else {}
            progress.advance(ActionProgressStage.COMPLETE, "Calc action applied and verified.")
            trace.finish("applied", result_status=str(result.get("status") or "applied"))
            self._status_notice(str(result.get("message") or "Calc action applied and verified."))
            self._set_idle()
            return
        error = str((response or {}).get("error") or "Calc did not confirm the change.")
        progress.advance(ActionProgressStage.FAILED, "Calc could not verify the reviewed action.")
        trace.finish("failed", failure_stage="apply", error_type=error.split(":", 1)[0][:80])
        self._notice(f"OpenWand couldn't apply the Calc action: {error}", severity="warning")
        self._set_idle()

    @staticmethod
    def _is_vscode_fix_action(
        prompt: str,
        active_app: dict[str, Any],
        selected_text: str,
    ) -> bool:
        """Recognize an explicit selected-code mutation in a VS Code-like editor."""
        try:
            from core.actions.adapters.vscode import is_code_editor_app

            if not is_code_editor_app(active_app):
                return False
        except Exception:
            return False
        text = " ".join(str(prompt or "").casefold().split())
        if not text or len(text) > 500:
            return False
        action_words = {
            "fix", "debug", "repair", "refactor", "optimize", "implement", "change",
            "improve", "correct", "solve", "patch", "rewrite", "edit", "write", "create",
            "make", "update",
            "add error handling",
            "修復", "修正", "除錯", "重構", "改善", "實作", "修改", "解决", "修复",
        }
        return any(word in text for word in action_words)

    def _run_vscode_fix_action(
        self,
        pending: PendingInvocation,
        prompt: str,
        active_app: dict[str, Any],
        selected_text: str,
        *,
        planning_tool: str = "vscode_plan_replace_selection",
    ) -> None:
        """Plan, preview, and apply one fingerprint-checked selected-code fix."""
        from core.actions.adapters.vscode import code_editor_name

        editor_label = code_editor_name(active_app)
        trace = ActionTrace(
            "vscode.code_change",
            app="vscode",
            started_unix_ns=pending.invoked_at_unix_ns,
        )
        for stage, when in (
            ("initial_context_captured", pending.initial_context_at_unix_ns),
            ("intent_presented", pending.intent_shown_at_unix_ns),
            ("context_ready", pending.context_ready_at_unix_ns),
        ):
            if when:
                trace.mark_at(stage, when)
        progress = self._new_action_progress("vscode.code_change", app="vscode", trace=trace)
        trace.mark(
            "intent_received",
            prompt_chars=len(prompt),
            captured_selection_chars=len(selected_text),
        )
        gen = self._new_generation()
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        self._safe_call(self.ui, "ui.reply.thinking", timeout=30.0)
        progress.advance(
            ActionProgressStage.READING,
            f"Reading the active saved {editor_label} file and exact selected range...",
        )

        if self._is_vscode_untitled_tab(active_app):
            self._run_vscode_untitled_action(
                pending,
                prompt,
                active_app,
                selected_text,
                trace=trace,
                progress=progress,
                generation=gen,
                planning_tool=planning_tool,
            )
            return

        trace.mark("snapshot_requested")
        snapshot_response = self._safe_call(
            self.native,
            "native.action.vscode.snapshot",
            {"active_app": active_app, "selected_text": selected_text},
            timeout=4.0,
        )
        trace.mark(
            "snapshot_returned",
            ok=bool(isinstance(snapshot_response, dict) and snapshot_response.get("ok")),
            native_timing=(snapshot_response or {}).get("timing", {})
            if isinstance(snapshot_response, dict)
            else {},
        )
        snapshot_value = (
            snapshot_response.get("snapshot")
            if isinstance(snapshot_response, dict) and isinstance(snapshot_response.get("snapshot"), dict)
            else {}
        )
        if not isinstance(snapshot_response, dict) or not snapshot_response.get("ok") or not snapshot_value:
            error = str((snapshot_response or {}).get("error") or "The active saved file could not be read.")
            if self._is_vscode_save_required_error(error):
                progress.advance(
                    ActionProgressStage.FAILED,
                    f"This {editor_label} tab must be saved before OpenWand can change it safely.",
                )
                trace.finish("failed", failure_stage="save_required", error_type="unsaved_editor")
                self._notice(
                    "Save this tab once, then press Ctrl+Shift+Q again. "
                    "OpenWand did not change anything.\n\n"
                    "Recommendation: Press Ctrl+S to choose a filename, then run the same request again.",
                    severity="warning",
                )
                self._set_idle()
                return
            progress.advance(ActionProgressStage.FAILED, "The active saved file could not be read safely.")
            trace.finish("failed", failure_stage="snapshot", error_type=error.split(":", 1)[0][:80])
            self._notice(f"OpenWand couldn't prepare the {editor_label} action: {error}", severity="warning")
            self._set_idle()
            return

        try:
            from core.actions.adapters.vscode import (
                VSCodeActionAdapter,
                VSCodeSnapshot,
                build_replace_file_plan,
                build_replace_selection_plan,
            )

            snapshot = VSCodeSnapshot.from_selection(snapshot_value)
        except Exception as exc:  # noqa: BLE001 - malformed native state must not reach the model
            progress.advance(ActionProgressStage.FAILED, "The saved-file target did not pass safety checks.")
            trace.finish("failed", failure_stage="snapshot_validation", error_type=type(exc).__name__)
            self._notice(f"OpenWand couldn't prepare the {editor_label} action: {exc}", severity="warning")
            self._set_idle()
            return

        trace.mark(
            "snapshot_validated",
            target_kind="whole_file" if snapshot.is_whole_file else "selection",
            document_chars=len(snapshot.text),
            selected_chars=len(snapshot.selected_text),
        )
        progress.advance(
            ActionProgressStage.PLANNING,
            (
                "Target checked. Drafting the exact contents for the new file..."
                if snapshot.is_whole_file
                else "Target checked. Reviewing the selected code and drafting the exact change..."
            ),
        )
        model_finished = threading.Event()
        model_heads_up_timer = self._start_action_progress_heads_up(
            gen,
            model_finished,
            progress,
            ActionProgressStage.PLANNING,
            "The model is still drafting the exact code change; this may take a few more seconds.",
        )
        first_model_activity = threading.Event()

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            if event in {"rewrite.first_activity", "reply.chunk", "reply.done"} and not first_model_activity.is_set():
                first_model_activity.set()
                trace.mark("model_first_activity", event=event)
            if event == "reply.chunk":
                # Model summaries are not execution state. Keep the public
                # action line deterministic while recording first activity.
                return
            elif event == "reply.done":
                return
            elif event == "privacy.review.request":
                trace.mark("privacy_review_requested")
                self._handle_privacy_review_request(payload)
            elif event == "rewrite.telemetry":
                trace.mark(
                    "model_worker_telemetry",
                    timing=payload if isinstance(payload, dict) else {},
                )

        context_radius = 2_500
        context_start = max(0, snapshot.selection_start - context_radius)
        context_end = min(len(snapshot.text), snapshot.selection_end + context_radius)
        if snapshot.is_whole_file:
            model_selected_text = f"[The active saved {editor_label} file is currently empty.]"
            rewrite_context = f"Active saved {editor_label} file: {snapshot.file_path}\nCurrent content: empty"
            model_instruction = (
                f"{prompt}\n\n"
                f"You are filling an empty saved {editor_label} file. Return the complete new file content, "
                "not the bracketed placeholder. In assistant_response, briefly state what you created."
            )
        else:
            model_selected_text = snapshot.selected_text
            rewrite_context = (
                f"Active saved file: {snapshot.file_path}\n"
                f"Selected character range: {snapshot.selection_start}:{snapshot.selection_end}\n\n"
                "Surrounding saved code:\n"
                f"{snapshot.text[context_start:context_end]}"
            )
            model_instruction = (
                f"{prompt}\n\n"
                f"You are proposing a change to selected code in {editor_label}. Return the complete replacement "
                "for the selected block only, preserving valid indentation. In assistant_response, briefly "
                "state the issue you found and how this replacement fixes it. Do not modify code outside "
                "the selected block."
            )
        try:
            _local_progress = (
                "Planning the new file and drafting its exact contents…"
                if snapshot.is_whole_file
                else "Reviewing the selected code and drafting the exact change…"
            )
            trace.mark("local_progress_presented")
            trace.mark("model_requested")
            result = self._brain_reply_call_with_events(
                "brain.action.plan",
                {
                    "planning_tool_name": planning_tool,
                    "planning_tool_description": (
                        f"Plan the exact replacement text for the captured {editor_label} target. The tool cannot edit "
                        "outside that target or execute commands."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "replacement_text": {
                                "type": "string",
                                "description": "The complete replacement for only the captured editor target.",
                            },
                        },
                        "required": ["replacement_text"],
                        "additionalProperties": False,
                    },
                    "user_prompt": model_instruction,
                    "app_context": {
                        "selected_text": model_selected_text,
                        "context": rewrite_context,
                    },
                },
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=on_event,
                generation=gen,
            )
        except Exception as exc:  # noqa: BLE001 - surface route failures without touching the file
            log.exception("code editor action planning failed")
            progress.advance(ActionProgressStage.FAILED, "The code change could not be drafted.")
            trace.finish("failed", failure_stage="model", error_type=type(exc).__name__)
            if self._is_current(gen):
                self._notice(f"{editor_label} fix failed: {self._friendly_error(exc)}", severity="error")
                self._set_idle()
            return
        finally:
            model_finished.set()
            model_heads_up_timer.cancel()
        if not self._is_current(gen):
            progress.advance(ActionProgressStage.CANCELLED, "This code action was replaced by a newer request.")
            trace.finish("superseded", failure_stage="after_model")
            self._set_idle()
            return
        if not isinstance(result, dict) or str(result.get("tool_name") or "") != planning_tool:
            progress.advance(ActionProgressStage.FAILED, "The model did not return the required code planning tool.")
            trace.finish("failed", failure_stage="model_contract", error_type="wrong_planning_tool")
            self._notice("OpenWand couldn't build a safe code plan because the required tool was not returned.", severity="warning")
            self._set_idle()
            return
        planned_arguments = (
            result.get("arguments") if isinstance(result, dict) and isinstance(result.get("arguments"), dict)
            else {}
        )
        replacement = str(planned_arguments.get("replacement_text") or "")
        model_summary = str((result or {}).get("visible_text") or "").strip()
        trace.mark(
            "model_completed",
            replacement_chars=len(replacement),
            summary_chars=len(model_summary),
        )
        progress.advance(
            ActionProgressStage.VALIDATING,
            "Draft received. Checking its file boundary, selected range, and operation schema...",
        )
        try:
            plan = (
                build_replace_file_plan(
                    snapshot,
                    replacement,
                    summary=model_summary or f"Proposed content for {snapshot.display_name}",
                )
                if snapshot.is_whole_file
                else build_replace_selection_plan(
                    snapshot,
                    replacement,
                    summary=model_summary or f"Proposed fix for {Path(snapshot.file_path).name}",
                )
            )
            progress.advance(
                ActionProgressStage.PREPARING_PREVIEW,
                "Safety checks passed. Building the exact saved-file diff preview...",
            )
            preview = VSCodeActionAdapter().render_preview(plan, snapshot)
        except Exception as exc:  # noqa: BLE001 - invalid model output never reaches Apply
            progress.advance(ActionProgressStage.FAILED, "The proposed code could not form a safe diff.")
            trace.finish("failed", failure_stage="preview_build", error_type=type(exc).__name__)
            self._notice(f"OpenWand couldn't build a safe code diff: {exc}", severity="warning")
            self._set_idle()
            return

        progress.advance(
            ActionProgressStage.AWAITING_APPROVAL,
            preview.summary,
        )
        self._set_idle()
        trace.mark("preview_requested", operation_count=len(plan.operations))
        decision = self._safe_call(
            self.ui,
            "ui.action.preview.request",
            {
                "plan_id": preview.plan_id,
                "title": preview.title,
                "summary": preview.summary,
                "html": preview.html,
                "details": list(preview.details),
                "warnings": list(preview.warnings),
            },
            timeout=300.0,
        )
        if isinstance(decision, dict):
            show_called_at = int(
                decision.get("show_called_at_unix_ns")
                or decision.get("presented_at_unix_ns")
                or 0
            )
            topmost_at = int(decision.get("topmost_at_unix_ns") or 0)
            decided_at = int(decision.get("decided_at_unix_ns") or 0)
            if show_called_at:
                trace.mark_at("preview_show_called", show_called_at)
            if topmost_at:
                trace.mark_at("preview_raised_topmost", topmost_at)
            if decided_at:
                trace.mark_at(
                    "preview_decided",
                    decided_at,
                    approved=bool(decision.get("approved")),
                    decision_wait_ms=decision.get("decision_wait_ms"),
                )
            else:
                trace.mark("preview_decided", approved=bool(decision.get("approved")))
        if not isinstance(decision, dict) or not decision.get("approved"):
            progress.advance(ActionProgressStage.CANCELLED, "Code change cancelled. Nothing was changed.")
            trace.finish("cancelled", failure_stage="preview_decision")
            self._notice(f"{editor_label} change cancelled. Nothing was changed.")
            self._set_idle()
            return

        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        progress.advance(
            ActionProgressStage.APPLYING,
            "Rechecking the saved file, applying the reviewed change, and verifying the result...",
        )
        trace.mark("apply_requested")
        response = self._safe_call(
            self.native,
            "native.action.vscode.apply",
            {
                "plan": plan.to_dict(),
                "confirmed": True,
                "idempotency_key": f"{plan.plan_id}:apply",
            },
            timeout=15.0,
        )
        trace.mark(
            "apply_returned",
            ok=bool(isinstance(response, dict) and response.get("ok")),
            native_timing=(response or {}).get("timing", {}) if isinstance(response, dict) else {},
        )
        if isinstance(response, dict) and response.get("ok"):
            applied = response.get("result") if isinstance(response.get("result"), dict) else {}
            progress.advance(ActionProgressStage.COMPLETE, "Reviewed code change applied and verified.")
            trace.finish(
                "applied",
                result_status=str(applied.get("status") or "applied"),
                created_count=len(applied.get("created") or []),
                verification_count=len(applied.get("verification") or []),
            )
            self._status_notice(str(applied.get("message") or f"Applied the code change in {editor_label}."))
            self._set_idle()
            return
        error = str((response or {}).get("error") or f"{editor_label} did not confirm the file change.")
        progress.advance(ActionProgressStage.FAILED, "The reviewed code change could not be verified.")
        trace.finish("failed", failure_stage="apply", error_type=error.split(":", 1)[0][:80])
        self._notice(f"OpenWand couldn't apply the code change: {error}", severity="warning")
        self._set_idle()

    def _run_vscode_untitled_action(
        self,
        pending: PendingInvocation,
        prompt: str,
        active_app: dict[str, Any],
        selected_text: str,
        *,
        trace: ActionTrace,
        progress: ActionProgress,
        generation: int,
        planning_tool: str = "vscode_plan_replace_selection",
    ) -> None:
        """Preview and write through the exact editor range captured at summon time."""
        focus_token = int(pending.context.get("focus_token") or 0)
        if not focus_token:
            progress.advance(
                ActionProgressStage.FAILED,
                "OpenWand could not capture the exact Untitled editor target safely.",
            )
            trace.finish("failed", failure_stage="focus_capture", error_type="missing_focus_token")
            self._notice(
                "Keep the caret in the Untitled editor when you press Ctrl+Shift+Q, then try again.\n\n"
                "Recommendation: Make sure the text editor itself has focus, not a panel or terminal.",
                severity="warning",
            )
            self._set_idle()
            return

        display_name = str(active_app.get("name") or "Untitled VS Code tab").strip()
        progress.advance(
            ActionProgressStage.PLANNING,
            (
                "Editor target captured. Reviewing the selected code and drafting the exact change..."
                if selected_text.strip()
                else "Editor insertion point captured. Drafting the exact new content..."
            ),
        )
        model_finished = threading.Event()
        heads_up = self._start_action_progress_heads_up(
            generation,
            model_finished,
            progress,
            ActionProgressStage.PLANNING,
            "The model is still drafting the exact code change; this may take a few more seconds.",
        )

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            if event == "privacy.review.request":
                trace.mark("privacy_review_requested")
                self._handle_privacy_review_request(payload)
            elif event == "rewrite.first_activity":
                trace.mark("model_first_activity", event=event)
            elif event == "rewrite.telemetry":
                trace.mark("model_worker_telemetry", timing=payload if isinstance(payload, dict) else {})

        model_selected_text = selected_text or "[The captured Untitled VS Code editor is currently empty.]"
        instruction = (
            f"{prompt}\n\n"
            + (
                "Return the complete replacement for the selected code only. "
                if selected_text.strip()
                else "Return the complete content to insert into the empty Untitled editor. "
            )
            + "Do not include Markdown fences. In assistant_response, briefly describe the proposed change."
        )
        try:
            trace.mark("model_requested", target_kind="selection" if selected_text.strip() else "caret")
            result = self._brain_reply_call_with_events(
                "brain.action.plan",
                {
                    "planning_tool_name": planning_tool,
                    "planning_tool_description": (
                        "Plan the exact replacement or insertion text for the captured Untitled VS Code editor "
                        "target. The tool cannot edit any other target or execute commands."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "replacement_text": {
                                "type": "string",
                                "description": "The complete replacement or insertion for the captured target.",
                            },
                        },
                        "required": ["replacement_text"],
                        "additionalProperties": False,
                    },
                    "user_prompt": instruction,
                    "app_context": {
                        "selected_text": model_selected_text,
                        "context": f"Active unsaved VS Code tab: {display_name}",
                    },
                },
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=on_event,
                generation=generation,
            )
        except Exception as exc:  # noqa: BLE001 - never touch the editor after a model failure
            progress.advance(ActionProgressStage.FAILED, "The code change could not be drafted.")
            trace.finish("failed", failure_stage="model", error_type=type(exc).__name__)
            if self._is_current(generation):
                self._notice(f"VS Code fix failed: {self._friendly_error(exc)}", severity="error")
                self._set_idle()
            return
        finally:
            model_finished.set()
            heads_up.cancel()

        if not self._is_current(generation):
            progress.advance(ActionProgressStage.CANCELLED, "This code action was replaced by a newer request.")
            trace.finish("superseded", failure_stage="after_model")
            self._set_idle()
            return

        if not isinstance(result, dict) or str(result.get("tool_name") or "") != planning_tool:
            progress.advance(ActionProgressStage.FAILED, "The model did not return the required code planning tool.")
            trace.finish("failed", failure_stage="model_contract", error_type="wrong_planning_tool")
            self._notice("OpenWand couldn't build a safe code plan because the required tool was not returned.", severity="warning")
            self._set_idle()
            return
        planned_arguments = (
            result.get("arguments") if isinstance(result, dict) and isinstance(result.get("arguments"), dict)
            else {}
        )
        replacement = str(planned_arguments.get("replacement_text") or "")
        summary = str((result or {}).get("visible_text") or "").strip()
        trace.mark("model_completed", replacement_chars=len(replacement), summary_chars=len(summary))
        progress.advance(
            ActionProgressStage.VALIDATING,
            "Draft received. Checking the captured editor target and exact replacement...",
        )
        if not replacement.strip() or len(replacement) > 24_000:
            progress.advance(ActionProgressStage.FAILED, "The proposed code could not form a safe diff.")
            trace.finish("failed", failure_stage="replacement_validation", error_type="invalid_replacement")
            self._notice("OpenWand couldn't build a safe code diff for the Untitled tab.", severity="warning")
            self._set_idle()
            return

        from core.actions.adapters.vscode import render_vscode_untitled_preview

        progress.advance(
            ActionProgressStage.PREPARING_PREVIEW,
            "Safety checks passed. Building the exact Untitled editor diff preview...",
        )
        preview = render_vscode_untitled_preview(
            replacement,
            selected_text=selected_text,
            display_name=display_name,
            summary=summary or "Proposed change for the Untitled VS Code tab",
        )
        progress.advance(
            ActionProgressStage.AWAITING_APPROVAL,
            preview.summary,
        )
        self._set_idle()
        decision = self._safe_call(
            self.ui,
            "ui.action.preview.request",
            {
                "plan_id": preview.plan_id,
                "title": preview.title,
                "summary": preview.summary,
                "html": preview.html,
                "details": list(preview.details),
                "warnings": list(preview.warnings),
            },
            timeout=300.0,
        )
        if not isinstance(decision, dict) or not decision.get("approved"):
            progress.advance(ActionProgressStage.CANCELLED, "Code change cancelled. Nothing was changed.")
            trace.finish("cancelled", failure_stage="preview_decision")
            self._notice("VS Code change cancelled. Nothing was changed.")
            self._set_idle()
            return

        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        progress.advance(
            ActionProgressStage.APPLYING,
            "Writing the reviewed change to the captured Untitled editor target...",
        )
        paste = self._safe_call(
            self.native,
            "native.action.vscode.live_apply",
            {
                "text": replacement,
                "active_app": active_app,
                "editor_point": pending.context.get("editor_point") or {},
                "confirmed": True,
            },
            timeout=30.0,
        )
        if isinstance(paste, dict) and paste.get("ok"):
            progress.advance(ActionProgressStage.COMPLETE, "Reviewed code change written to the Untitled tab.")
            trace.finish("applied", result_status="vscode_live_api_verified")
            self._set_idle()
            return

        error = str((paste or {}).get("error") or "The captured editor target was no longer available.")
        progress.advance(ActionProgressStage.FAILED, "OpenWand could not write to the captured Untitled editor target.")
        trace.finish("failed", failure_stage="paste_back", error_type=error.split(":", 1)[0][:80])
        self._notice(
            "OpenWand could not reach this live VS Code editor through its private API bridge.\n\n"
            "Recommendation: Reopen VS Code through OpenWand control, keep the caret in that tab, and try again.",
            severity="warning",
            technical_detail=error,
        )
        self._set_idle()

    @staticmethod
    def _is_vscode_untitled_tab(active_app: dict[str, Any]) -> bool:
        """Return whether the captured editor title identifies a VS Code Untitled buffer."""
        title = " ".join(str(active_app.get("name") or "").casefold().split())
        return bool(re.search(r"(?:^|\s|[\-—])untitled(?:[- ]?\d+)?(?:\s|$|[\-—])", title))

    @staticmethod
    def _is_vscode_save_required_error(error: str) -> bool:
        """Recognize native failures that the user can resolve with one save."""
        text = " ".join(str(error or "").casefold().split())
        return any(
            marker in text
            for marker in (
                "tab is unsaved",
                "save it once",
                "save the active vs code file",
                "before asking openwand to change it",
            )
        )

    def add_context(self) -> None:
        """Add context."""
        with self._lock:
            pending_capture = dict(self._pending_context_capture or {})
        context = self._context_snapshot(
            {"context_clipboard": True},
            include_selected_paths=pending_capture.get("source") == "selection",
        )
        paths = self._selected_paths_from_context(context)
        selected_text = str(context.get("selected_text") or "").strip()
        clipboard_text = str(context.get("clipboard_text") or "").strip()
        text = selected_text or ("" if paths else clipboard_text)
        if pending_capture.get("source") == "selection":
            self._complete_selection_capture(text, pending_capture, paths)
            return
        if not text:
            self._notice("No selected text or clipboard text to add.")
            return
        # Show the added context as a removable badge to the right of the icon,
        # exactly like a dropped file -- not as a speech-bubble notice. Routing
        # it through _drop_context_items keeps the badge's X-to-remove indexing
        # consistent with remove_context_item.
        name = "Selection"
        self._drop_context_items.append({"name": name, "content": text, "type": "text"})
        self._fire(self.ui, "ui.context.add_item", {"name": name, "item_type": "text"})

    def _complete_selection_capture(
        self,
        text: str,
        capture: dict[str, Any],
        paths: list[str] | None = None,
    ) -> None:
        """Complete a pending interactive selection capture."""
        surface = str(capture.get("surface") or "")
        selected_paths = self._selected_paths_from_context({"selected_paths": paths or []})
        if not text and not selected_paths:
            with self._lock:
                if self._pending_context_capture == capture:
                    self._pending_context_capture = None
            if surface == "intent":
                self._restore_intent_after_selection_capture("", str(capture.get("custom_text") or ""))
            elif surface == "chat":
                self._safe_call(
                    self.ui,
                    "ui.chat.capture_cancelled",
                    {"source": "selection"},
                    timeout=30.0,
                )
            self._notice("No selected text, clipboard text, or selected files found.")
            return

        with self._lock:
            self._pending_context_capture = None
        if surface == "intent":
            self._restore_intent_after_selection_capture(
                text,
                str(capture.get("custom_text") or ""),
                selected_paths,
            )
        elif surface == "chat":
            payload = {
                "name": "Selection",
                "content": text,
                "item_type": "text",
                "source": "selection",
            }
            if selected_paths:
                payload["paths"] = selected_paths
            self._safe_call(self.ui, "ui.chat.capture_context", payload, timeout=30.0)
        else:
            if text:
                self._drop_context_items.append({"name": "Selection", "content": text, "type": "text"})
                self._fire(self.ui, "ui.context.add_item", {"name": "Selection", "item_type": "text"})
            else:
                for item in self._path_context_items(selected_paths):
                    self._drop_context_items.append(item)
                    self._fire(
                        self.ui,
                        "ui.context.add_item",
                        {
                            "name": str(item.get("name") or "Selection"),
                            "item_type": str(item.get("type") or "file"),
                        },
                    )
        self._notice("Selection captured.")

    def _restore_intent_after_selection_capture(
        self,
        text: str,
        custom_text: str = "",
        paths: list[str] | None = None,
    ) -> None:
        """Restore the intent picker after an out-of-band selection capture."""
        selected_paths = self._selected_paths_from_context({"selected_paths": paths or []})
        with self._lock:
            pending = self._pending
            if pending is None:
                return
            if text:
                pending.context["selected_text"] = text
            else:
                pending.context.pop("selected_text", None)
            if selected_paths:
                pending.context["selected_paths"] = selected_paths
            else:
                pending.context.pop("selected_paths", None)
            if text or selected_paths:
                pending.caller["_context_selection_enabled"] = True
            else:
                pending.caller["_context_selection_enabled"] = False
            self._pending = pending
        context_items = self._intent_context_items(pending)
        if text or selected_paths:
            for item in context_items:
                if item.get("id") == "selection":
                    item["touched"] = True
        self._safe_call(
            self.ui,
            "ui.show_intent",
            {
                "caller_idx": pending.caller_idx,
                "target_hwnd": pending.intent_target_pid,
                "context_items": context_items,
                "tool_snapshot": self._intent_tool_snapshot(
                    pending.caller,
                    pending.tool_choices,
                ),
                "initial_custom_text": custom_text,
                "focus_overlay": True,
                "action_provider": pending.action_provider_context,
            },
            timeout=30.0,
        )

    def _restore_intent_after_context_capture(
        self,
        pending: PendingInvocation,
        custom_text: str = "",
        context_items: list[dict[str, Any]] | None = None,
    ) -> None:
        """Reopen the intent picker after an interactive context capture."""
        self._safe_call(
            self.ui,
            "ui.show_intent",
            {
                "caller_idx": pending.caller_idx,
                "target_hwnd": pending.intent_target_pid,
                "context_items": context_items or self._intent_context_items(pending),
                "tool_snapshot": self._intent_tool_snapshot(
                    pending.caller,
                    pending.tool_choices,
                ),
                "initial_custom_text": str(custom_text or ""),
                "focus_overlay": True,
                "action_provider": pending.action_provider_context,
            },
            timeout=30.0,
        )

    def read_selection_aloud(self) -> None:
        """Speak the currently selected text without sending it to a model."""
        if self._live_voice_busy():
            self._notice(t("Stop the live voice conversation first."))
            return
        if not self._tts_enabled():
            self._notice(t("TTS is off. Choose a voice provider in Settings first."))
            return
        try:
            context = self._context_snapshot({"context_clipboard": False})
        except Exception as exc:  # noqa: BLE001 - keep tray action user-facing
            log.exception("read selection aloud failed to capture context")
            self._notice(f"{t('Could not read selected text')}: {self._friendly_error(exc)}", severity="error")
            return
        text = str(context.get("selected_text") or "").strip()
        if not text:
            self._notice(t("No selected text to read aloud."))
            return

        gen = self._new_generation()
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        self._safe_call(
            self.ui,
            "ui.reply.labeled_text",
            {
                "label": t("Preparing speech"),
                "text": text,
                "timeout_ms": 0,
                "cancel_on_close": True,
            },
            timeout=30.0,
        )
        if not self._read_aloud_text(text, generation=gen) and not self._reply_bubble_cancelled(gen):
            self._notice(t("Could not read selected text aloud."))

    def clear_context(self) -> None:
        """Clear context."""
        self._context_buffer.clear()
        self._drop_context_items.clear()
        with self._lock:
            self._pending_context_capture = None
        # The panel visibly empties (ui.context.clear), so no bubble notice.
        self._safe_call(self.ui, "ui.context.clear", timeout=30.0)

    def context_items_dropped(self, items: list[dict[str, Any]]) -> None:
        """Handle context items dropped for flow controller."""
        cleaned: list[dict[str, Any]] = []
        for raw in items:
            item = self._normalize_context_item(raw)
            if item.get("type") == "document_path":
                expanded = self._path_context_items([item.get("content")])
                cleaned.extend(expanded or [item])
            else:
                cleaned.append(item)
        self._drop_context_items.extend(cleaned)
        with self._lock:
            pending = self._pending
        if pending is not None:
            self._fire(
                self.ui,
                "ui.intent.context_items",
                {"context_items": self._intent_context_items(pending)},
            )

    def remove_context_item(self, index: int) -> None:
        """Remove context item."""
        if 0 <= index < len(self._drop_context_items):
            self._drop_context_items.pop(index)

    def voice_start(self) -> None:
        """Handle voice start for flow controller."""
        # Acknowledge the keypress instantly with the listening icon, before any
        # config reload or setup work, so holding the hotkey gives immediate
        # visual feedback rather than waiting on the steps below.
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})
        self._reload_supervisor_config_if_changed()
        self._ensure_voice_start_claimed()
        self._new_generation()
        caller = self._voice_caller()
        self._voice_context = {}
        self._voice_screenshot_b64 = None
        self._fire(self.audio, "audio.stop")
        try:
            record_result = self.audio.call("audio.record.start", timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            log.exception("voice record start failed")
            self._notice(f"Couldn't start recording: {self._friendly_error(exc)}", severity="error")
            self._mark_voice_failed()
            self._set_idle()
            return
        if isinstance(record_result, dict) and record_result.get("recording") is False:
            error = str(record_result.get("error") or "").strip()
            if error:
                log.warning("voice record start unavailable: %s", error)
                self._notice(f"Couldn't start recording: {self._friendly_error(error)}", severity="error")
            self._mark_voice_failed()
            self._set_idle()
            return
        if not self._mark_voice_recording():
            return
        self._fire(self.ui, "ui.reply.listening")
        # include_browser=False keeps a slow page fetch off the record-start
        # path; _brain_query_params fetches it lazily at query time instead.
        self._voice_context = self._context_snapshot(caller, include_browser=False)
        # Capture AFTER recording starts so the screenshot overlaps the speech
        # instead of delaying the record start.
        if caller.get("context_screenshot") == "auto":
            self._voice_screenshot_b64 = self._capture_fullscreen_b64()

    def voice_stop(self) -> None:
        """Handle voice stop for flow controller."""
        if not self._ensure_voice_stop_claimed():
            return
        self._fire(self.ui, "ui.overlay.state", {"state": "thinking"})
        # The first transcription after launch blocks on the (slow) model load /
        # warmup. Tell the user that's what's happening instead of leaving them
        # staring at the generic "thinking" dots wondering why it's slow.
        if self._stt_warming():
            self._fire(self.ui, "ui.reply.notice",
                       {"text": "Warming up speech model - the first transcription is slower..."})
        else:
            self._fire(self.ui, "ui.reply.thinking")
        try:
            result = self.audio.call("audio.record.stop_transcribe", timeout=180.0)
            text = str((result or {}).get("text") or "").strip()
            if not text:
                # Empty transcript = the clip was too short/quiet or had no speech
                # (a too-brief F8 tap is the usual cause). Tell the user how to
                # hold the key instead of silently resetting and leaving them
                # wondering why nothing happened.
                self._notice("Didn't catch any speech. Hold the key down while you speak, then release.")
                self._set_idle()
                return
            text = self._confirm_voice_transcript(text, purpose="voice")
            if not text:
                self._fire(self.ui, "ui.reply.reset")
                self._set_idle()
                return
            pending = PendingInvocation(
                caller_idx=0,
                caller=self._voice_caller(),
                context=self._voice_context,
                action_provider_context=self._action_provider_picker_context(self._voice_context),
                screenshot_b64=self._voice_screenshot_b64,
            )
            self._voice_screenshot_b64 = None
            if self._voice_review_transcript_enabled():
                pending.context_ready.set()
                self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
                with self._lock:
                    self._pending = pending
                self._safe_call(
                    self.ui,
                    "ui.show_intent",
                    {
                        "caller_idx": 0,
                        "target_hwnd": 0,
                        "context_items": self._intent_context_items(pending),
                        "tool_snapshot": self._intent_tool_snapshot(pending.caller),
                        "initial_custom_text": text,
                        "focus_overlay": True,
                        "action_provider": pending.action_provider_context,
                    },
                    timeout=30.0,
                )
                self._set_idle()
                return
            self._safe_call(self.ui, "ui.reply.transcript", {"text": text}, timeout=30.0)
            self._mark_voice_idle()
            self._query(text, pending, preserve_reply_bubble=True)
        finally:
            self._mark_voice_idle()

    def live_voice_start(self) -> None:
        """Begin a hands-free live voice conversation (toggle hotkey)."""
        # Acknowledge the keypress instantly; "thinking" covers the connect.
        self._fire(self.ui, "ui.overlay.state", {"state": "thinking"})
        self._live_voice_ready_notified = False
        self._reload_supervisor_config_if_changed()
        with self._lock:
            recorder_busy = self._voice_state != "idle" or self._dictate_state != "idle"
        if recorder_busy:
            self._notice(t("Finish the current voice recording first."))
            self._mark_live_voice_idle()
            self._set_idle()
            return
        self._fire(self.audio, "audio.stop")
        try:
            result = self.audio.call("audio.live.start", timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            log.exception("live voice start failed")
            self._notice(f"{t('Could not start live voice')}: {self._friendly_error(exc)}", severity="error")
            self._mark_live_voice_idle()
            self._set_idle()
            return
        result = result if isinstance(result, dict) else {}
        if not result.get("started"):
            error = str(result.get("error") or "")
            if error == "already_active":
                # The worker still runs a session (e.g. a lost stop); adopt it.
                # It is mid-conversation, so no "ready" notice on its next
                # speaking -> listening flip.
                self._live_voice_ready_notified = True
                self._mark_live_voice_active()
                self._fire(self.ui, "ui.live_voice.session", {"active": True})
                return
            if error == "missing_key":
                self._notice(t("Live voice needs a Google API key. Add one in Settings."))
            elif error == "disabled":
                self._notice(t("Live conversation is disabled in Settings."))
            elif error == "missing_package":
                self._notice(t("Live voice support is not installed. Install it in Settings > TTS / Voice."))
            elif error == "mic_busy":
                self._notice(t("Finish the current voice recording first."))
            elif error == "unsupported_provider":
                self._notice(t("Live voice currently supports Gemini Live through the Google provider."))
            else:
                self._notice(f"{t('Could not start live voice')}: {error or 'unknown error'}", severity="error")
            self._mark_live_voice_idle()
            self._set_idle()
            return
        self._mark_live_voice_active()
        self._fire(self.ui, "ui.live_voice.session", {"active": True})
        log.info("live voice session started: model=%s", result.get("model"))

    def live_voice_stop(self) -> None:
        """End the live voice conversation (second toggle press)."""
        self._safe_call(self.audio, "audio.live.stop", timeout=10.0)
        self._mark_live_voice_idle()
        self._fire(self.ui, "ui.live_voice.session", {"active": False})
        self._set_idle()

    def dictate_start(self) -> None:
        """Push-to-talk dictation: capture the focused text field (so the result
        can be pasted back in place), then start recording."""
        self._reload_supervisor_config_if_changed()
        # Capture focus now, while the user's app is still frontmost. paste_back=True
        # makes the snapshot grab the focused text element / window handle.
        context = self._context_snapshot(
            {"paste_back": True, "context_clipboard": False}, include_browser=False
        )
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        if str(context.get("platform") or "") == "darwin":
            self._dictate_target_pid = int(active_app.get("pid") or 0)
        else:
            self._dictate_target_pid = int(active_app.get("window_id") or active_app.get("pid") or 0)
        self._dictate_focus_token = int(context.get("focus_token") or 0)
        self._fire(self.audio, "audio.stop")
        try:
            record_result = self.audio.call("audio.record.start", timeout=20.0)
        except Exception as exc:  # noqa: BLE001 - surface mic/worker failure in the UI
            log.exception("dictation record start failed")
            self._notice(f"Couldn't start dictation: {self._friendly_error(exc)}", severity="error")
            self._mark_dictate_failed()
            self._set_idle()
            return
        if isinstance(record_result, dict) and record_result.get("recording") is False:
            error = str(record_result.get("error") or "").strip()
            if error:
                log.warning("dictation record start unavailable: %s", error)
                self._notice(f"Couldn't start dictation: {self._friendly_error(error)}", severity="error")
            self._mark_dictate_failed()
            self._set_idle()
            return
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})
        self._fire(self.ui, "ui.reply.listening")

    def dictate_stop(self) -> None:
        """Stop dictation, transcribe, optionally LLM-clean, and paste into the
        text field that was focused when recording started."""
        self._fire(self.ui, "ui.reply.reset")
        try:
            try:
                result = self.audio.call("audio.record.stop_transcribe", timeout=180.0)
            except Exception as exc:  # noqa: BLE001 - surface transcribe failure in the UI
                log.exception("dictation transcribe failed")
                self._notice(f"Dictation failed: {self._friendly_error(exc)}", severity="error")
                self._set_idle()
                return
            text = str((result or {}).get("text") or "").strip()
            if not text:
                self._notice("Didn't catch any speech. Hold the key down while you speak, then release.")
                self._set_idle()
                return
            text = self._confirm_voice_transcript(text, purpose="dictation")
            if not text:
                self._set_idle()
                return
            import config
            if str(getattr(config, "DICTATE_MODE", "raw")).lower() == "llm":
                text = self._dictation_cleanup(text)
            self._paste_dictation(text)
        finally:
            self._mark_dictate_idle()

    def _dictation_cleanup(self, text: str) -> str:
        """Run the raw transcript through the LLM for punctuation/cleanup. Any
        failure falls back to the raw text so dictation always pastes something."""
        try:
            result = self._brain_call_with_events(
                "brain.rewrite",
                {
                    "selected_text": text,
                    "intent_prompt": (
                        "This is a raw speech-to-text dictation. Fix punctuation, "
                        "capitalization, and obvious transcription slips, and remove "
                        "filler words. Output ONLY the cleaned text, nothing else."
                    ),
                },
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=lambda *_a, **_k: None,
            )
            return str((result or {}).get("text") or "").strip() or text
        except Exception:  # noqa: BLE001 - never block a paste on cleanup
            log.exception("dictation LLM cleanup failed; pasting raw transcript")
            return text

    @staticmethod
    def _voice_transcript_candidates(text: str) -> list[str]:
        """Return cheap transcript candidates without rerunning STT."""
        raw = " ".join(str(text or "").split())
        if not raw:
            return []
        candidates = [raw]
        polished = raw[:1].upper() + raw[1:]
        if polished and polished[-1] not in ".!?":
            polished += "."
        if polished not in candidates:
            candidates.append(polished)
        command_like = raw.rstrip(".!?")
        if command_like.lower().startswith(("can you ", "please ")):
            command_like = command_like[:1].upper() + command_like[1:]
            if command_like not in candidates:
                candidates.append(command_like)
        return candidates[:3]

    def _confirm_voice_transcript(self, text: str, *, purpose: str) -> str:
        """Optionally ask the user to choose/edit the transcript before use."""
        import config

        if not bool(getattr(config, "VOICE_TRANSCRIPT_CONFIRM", False)):
            return text
        result = self._safe_call(
            self.ui,
            "ui.voice.candidates",
            {
                "text": text,
                "candidates": self._voice_transcript_candidates(text),
                "purpose": purpose,
            },
            timeout=300.0,
        ) or {}
        if not isinstance(result, dict) or not result.get("accepted"):
            return ""
        return str(result.get("text") or "").strip()

    def _paste_dictation(self, text: str) -> None:
        """Paste dictation."""
        paste = self.native.call(
            "native.paste_text",
            {
                "text": text,
                "target_pid": self._dictate_target_pid,
                "focus_token": self._dictate_focus_token,
            },
            timeout=30.0,
        )
        paste = paste if isinstance(paste, dict) else {}
        log.info("dictation paste: target_pid=%s result=%s", self._dictate_target_pid, paste)
        self._set_idle()
        if paste.get("ok"):
            return  # silent success - the pasted text is the confirmation
        if paste.get("clipboard_ok"):
            self._native_notify(
                "OpenWand - dictation on clipboard",
                f"Couldn't focus the field. Press {self._paste_shortcut()} to paste.",
            )
        else:
            log.error("dictation paste failed: %s", paste.get("error") or paste)
            self._native_notify("OpenWand - dictation failed", "Couldn't paste the text. See native.stderr.log.")

    def reload_settings(self, changed_keys: list[str] | None = None) -> None:
        """Handle reload settings for flow controller."""
        import config

        config.reload()
        self._config_mtime = self._current_config_mtime()
        log.info("supervisor config reloaded")
        self._safe_call(
            self.brain,
            "brain.config.reload",
            {"changed_keys": list(changed_keys) if changed_keys is not None else None},
            timeout=30.0,
        )
        privacy_changed = changed_keys is None or any(
            key in _PRIVACY_CONFIG_KEYS for key in changed_keys
        )
        if privacy_changed:
            self._prewarm_privacy()
        harness_changed = changed_keys is None or any(
            key in _HARNESS_CONFIG_KEYS for key in changed_keys
        )
        if harness_changed:
            self._prewarm_harness()
        default_caller = self._caller(0) or _all_context_off_policy()
        self._prewarm_llm_prefix(default_caller, route_kind="query")
        self._prewarm_llm_prefix(default_caller, route_kind="chat")
        # The audio worker owns the live TTS path and is long-lived, so it must
        # reload config + drop cached TTS connections here - prewarm alone leaves
        # the old provider/voice in effect until restart.
        audio_changed = changed_keys is None or any(key in _AUDIO_CONFIG_KEYS for key in changed_keys)
        if audio_changed:
            self._safe_call(self.audio, "audio.config.reload", timeout=30.0)
        else:
            log.info("audio config reload skipped; changed settings did not affect audio")
        # The native worker is a separate long-lived process and owns global
        # registrations. Replace hotkeys in one native call so Apply cannot
        # leave an old listener referenced between stop/start requests.
        result = self._safe_call(
            self.native,
            "native.hotkeys.reload",
            {"addon_hotkeys": self._addon_hotkeys()},
            timeout=10.0,
        ) or {}
        if isinstance(result, dict) and not result.get("started"):
            self._notice("Global hotkeys did not start. Click the OpenWand icon to summon it.", severity="warning")

    def _prewarm_privacy(self) -> None:
        """Start best-effort Advanced Privacy warmup in the brain worker."""
        try:
            self.brain.call("brain.privacy.prewarm", timeout=600.0, wait=False)
        except Exception:
            log.exception("advanced privacy prewarm did not start")

    def _prewarm_harness(self) -> None:
        """Start the selected reusable local agent harness in the brain worker."""
        try:
            self.brain.call("brain.harness.prewarm", timeout=120.0, wait=False)
        except Exception:
            log.exception("agent harness prewarm did not start")

    def _prewarm_llm_prefix(self, caller: dict[str, Any], *, route_kind: str) -> None:
        """Queue the caller's stable OpenWand prefix while the UI is idle."""
        try:
            allowed_tools, pinned_tools, file_access_mode = self._chat_tool_policy(caller)
            allow_screenshot_tool = False
            if route_kind == "query":
                allow_screenshot_tool = self._screenshot_tool_allowed(caller)
            elif self._screenshot_tool_allowed(caller) and "capture_screen" not in allowed_tools:
                # Mirror the current chat request policy exactly.  The history
                # route treats this as an allowed general tool rather than the
                # one-shot route's dedicated screenshot-tool flag.
                allowed_tools.append("capture_screen")
                if "capture_screen" not in pinned_tools:
                    pinned_tools.append("capture_screen")
            self.brain.call(
                "brain.llm.prefix.prewarm",
                {
                    "route_kind": route_kind,
                    "allowed_tools": allowed_tools,
                    "pinned_tools": pinned_tools,
                    "file_access_mode": file_access_mode,
                    "allow_screenshot_tool": allow_screenshot_tool,
                    "browser_retrieval": (
                        route_kind == "chat"
                        and self._context_mode(caller, "browser") == "model"
                    ),
                },
                timeout=30.0,
                wait=False,
            )
        except Exception:
            log.exception("Ollama static-prefix prewarm did not start")

    def _on_health_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        from core.setup_check import run_setup_check

        rows = list(run_setup_check())
        self._safe_call(self.ui, "ui.health.show", {"rows": rows, "title": "Setup check"}, timeout=5.0)
        warnings = [row for row in rows if row.get("status") in {"warn", "fail"}]
        self.runtime_log.append(
            "health",
            "info",
            f"Setup check ran: {len(rows)} check(s), {len(warnings)} issue(s).",
        )
        for row in warnings:
            self.runtime_log.append(
                "health",
                "error" if row.get("status") == "fail" else "warning",
                f"Setup check - {row.get('name')}: {row.get('message')}",
                detail=str(row.get("recommendation") or ""),
            )
        if warnings:
            first = warnings[0]
            self._safe_call(
                self.ui,
                "ui.reply.notice",
                {
                    "text": f"Health issue: {first.get('name')}: {first.get('message')}",
                    "timeout_ms": 8000,
                    "severity": "warning",
                },
                timeout=30.0,
            )

    def chat_request(self, data: dict[str, Any]) -> None:
        """Handle chat request for flow controller."""
        import config

        self._reload_supervisor_config_if_changed()
        request_id = str(data.get("request_id") or "")
        messages = data.get("messages") or []
        if not request_id:
            return

        done_seen = False
        done_payload: dict[str, Any] = {}
        user_text = self._latest_message_text(messages, role="user")
        user_annotations = self._chat_text_annotations(user_text, role="user")

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            """Handle event events."""
            nonlocal done_seen, done_payload
            if event == "reply.chunk":
                self._safe_call(
                    self.ui,
                    "ui.chat.chunk",
                    {
                        "request_id": request_id,
                        "text": str((payload or {}).get("text") or ""),
                        "is_progress": bool((payload or {}).get("is_progress")),
                        "is_thought": bool((payload or {}).get("is_thought")),
                    },
                    timeout=30.0,
                )
            elif event == "reply.done":
                done_seen = True
                done_payload = dict(payload or {}) if isinstance(payload, dict) else {}
                self._emit_file_context_progress(
                    list((payload or {}).get("file_context") or []),
                    chat_request_id=request_id,
                    include_bubble=False,
                )
            elif event == "live_file.activity":
                self._safe_call(
                    self.ui,
                    "ui.chat.chunk",
                    {
                        "request_id": request_id,
                        "local_work": dict(payload or {}),
                    },
                    timeout=30.0,
                )
            elif event == "harness.activity":
                self._safe_call(
                    self.ui,
                    "ui.chat.chunk",
                    {
                        "request_id": request_id,
                        "harness_activity": dict(payload or {}),
                    },
                    timeout=30.0,
                )
            elif event == "model_tool.ui.request":
                self._handle_model_tool_ui_request(payload)
            elif event == "live_file.approval.request":
                self._handle_live_file_approval_request(payload)
            elif event == "privacy.review.request":
                self._handle_privacy_review_request(payload)
            elif event == "background_task.started":
                self._watch_model_background_task(
                    dict(payload or {}),
                    conversation_id=str(data.get("conversation_id") or ""),
                )

        try:
            caller_idx = int(data.get("caller_idx", 0) or 0)
        except (TypeError, ValueError):
            caller_idx = 0
        supplied_policy = _normalized_context_policy(data.get("context_policy"))
        caller = supplied_policy or self._caller(caller_idx) or _all_context_off_policy()
        allowed_tools, pinned_tools, file_access_mode = self._chat_tool_policy(caller)
        stored_tool_context = _normalized_tool_context(data.get("tool_context"))
        if stored_tool_context and not supplied_policy:
            allowed_tools = list(stored_tool_context.get("allowed_tools") or allowed_tools)
            pinned_tools = list(stored_tool_context.get("pinned_tools") or pinned_tools)
            file_access_mode = str(stored_tool_context.get("file_access_mode") or file_access_mode)
        if self._screenshot_tool_allowed(caller) and "capture_screen" not in allowed_tools:
            allowed_tools.append("capture_screen")
            if "capture_screen" not in pinned_tools:
                pinned_tools.append("capture_screen")
        tool_context = {
            "allowed_tools": list(allowed_tools),
            "pinned_tools": list(pinned_tools),
            "file_access_mode": file_access_mode,
        }
        context_parts = self._chat_context_parts(caller)
        messages = self._messages_with_chat_context(messages, caller, context_parts)
        context_snippets: list[dict[str, str]] = []
        for label, _block, preview_source in context_parts:
            preview = self._context_preview_text(preview_source)
            if preview:
                context_snippets.append({"label": label, "preview": preview})
        chat_params: dict[str, Any] = {
            "messages": messages,
            "privacy_session_id": str(data.get("conversation_id") or request_id),
            "memory_enabled": self._context_mode(caller, "memory") == "on",
            "use_tools": bool(allowed_tools),
            "allowed_tools": allowed_tools,
            "pinned_tools": pinned_tools,
            "file_access_mode": file_access_mode,
        }
        harness_mode = str(getattr(config, "CHAT_EXECUTION_MODE", "openwand") or "openwand").strip().lower()
        if harness_mode not in {"openwand", "codex", "claude"}:
            harness_mode = "openwand"
        chat_params["harness_provider"] = harness_mode
        conversation_owner = str(
            getattr(config, "CHAT_CONVERSATION_OWNER", "openwand") or "openwand"
        ).strip().lower()
        chat_params["conversation_owner"] = conversation_owner if conversation_owner in {"openwand", "agent"} else "openwand"
        harness_sessions = data.get("harness_sessions") if isinstance(data.get("harness_sessions"), dict) else {}
        if harness_mode in {"codex", "claude"}:
            selected_session = harness_sessions.get(harness_mode)
            if isinstance(selected_session, dict):
                chat_params["harness_session"] = dict(selected_session)
            chat_params["harness_cwd"] = (
                _configured_harness_workspace(harness_mode)
                or str(data.get("harness_cwd") or "")
            )
        try:
            hist = self._safe_call(self.ui, "ui.chat.active_history", {}, timeout=10.0)
            if isinstance(hist, dict):
                chat_params["memory_project"] = hist.get("project_id")
        except Exception:
            log.exception("failed to fetch active project for chat")

        try:
            result = self._brain_call_with_events(
                "brain.chat",
                chat_params,
                timeout=self._interactive_llm_timeout_seconds(chat_params),
                on_event=on_event,
            )
            final_payload = done_payload if done_seen else (result if isinstance(result, dict) else {})
            text = str((final_payload or {}).get("text") or "")
            file_context = list((final_payload or {}).get("file_context") or [])
            assistant_attachments = list((final_payload or {}).get("attachments") or [])
            harness = dict((final_payload or {}).get("harness") or {})
            privacy_report = (final_payload or {}).get("privacy_report")
            self._last_privacy_report = privacy_report if isinstance(privacy_report, dict) else {}
            annotations = self._chat_text_annotations(text, role="assistant")
            if not done_seen:
                self._emit_file_context_progress(
                    file_context,
                    chat_request_id=request_id,
                    include_bubble=False,
                )
                if text:
                    self._safe_call(
                        self.ui,
                        "ui.chat.chunk",
                        {"request_id": request_id, "text": text},
                        timeout=30.0,
                    )
            self._safe_call(
                self.ui,
                "ui.chat.done",
                {
                    "request_id": request_id,
                    "text": text,
                    "file_context": file_context,
                    "tool_context": tool_context,
                    "context_snippets": context_snippets,
                    "annotations": annotations,
                    "user_annotations": user_annotations,
                    "display_segments": list((final_payload or {}).get("display_segments") or []),
                    "assistant_attachments": assistant_attachments,
                    "harness": harness,
                },
                timeout=30.0,
            )
            if (
                isinstance(privacy_report, dict)
                and privacy_report.get("count")
                and not privacy_report.get("reviewed")
            ):
                self._safe_call(
                    self.ui,
                    "ui.privacy.report",
                    {"report": privacy_report, "title": t("Privacy Report")},
                    timeout=30.0,
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("chat request failed")
            self._safe_call(
                self.ui,
                "ui.chat.error",
                {"request_id": request_id, "error": f"{type(exc).__name__}: {exc}"},
                timeout=30.0,
            )

    def chat_context_preview(self, data: dict[str, Any]) -> None:
        """Refresh chat-window context chip token estimates before send."""
        self._reload_supervisor_config_if_changed()
        preview_id = str(data.get("preview_id") or "")
        if not preview_id:
            return
        try:
            caller_idx = int(data.get("caller_idx", 0) or 0)
        except (TypeError, ValueError):
            caller_idx = 0
        caller = _normalized_context_policy(data.get("context_policy")) or self._caller(caller_idx) or _all_context_off_policy()
        self._prewarm_llm_prefix(caller, route_kind="chat")
        try:
            context = self._context_snapshot(
                caller,
                include_browser=False,
                preview_context_sources=True,
            )
        except Exception:
            log.exception("chat context preview snapshot failed")
            context = {}
        pending = PendingInvocation(caller_idx=caller_idx, caller=caller, context=context)
        self._safe_call(
            self.ui,
            "ui.chat.context_preview",
            {
                "preview_id": preview_id,
                "context_items": self._intent_context_items(pending),
            },
            timeout=30.0,
        )
        changed = False
        if self._effective_document_mode(caller) in {"auto", "model"} and not context.get("active_document_text"):
            text = self._fetch_active_document_text(context)
            if text:
                context["active_document_text"] = text
                changed = True
        preview_browser_pages = self._browser_pages_from_context(context)
        if self._context_mode(caller, "browser") == "auto" and (
            not preview_browser_pages
            or any(not str(page.get("content") or "").strip() for page in preview_browser_pages)
        ):
            browser = self._fetch_browser_content_for_context(context)
            if browser.get("browser_url") and not context.get("browser_url"):
                context["browser_url"] = browser["browser_url"]
                changed = True
            if browser.get("browser_content"):
                context["browser_content"] = browser["browser_content"]
                changed = True
            if self._browser_pages_from_context(context):
                changed = True
        if changed:
            pending.context = context
            self._safe_call(
                self.ui,
                "ui.chat.context_preview",
                {
                    "preview_id": preview_id,
                    "context_items": self._intent_context_items(pending),
                },
                timeout=30.0,
            )

    def open_memory(self) -> None:
        """Open memory."""
        result = self._safe_call(self.brain, "brain.memory.list", timeout=30.0) or {}
        facts = result.get("facts") if isinstance(result, dict) else []
        self._safe_call(self.ui, "ui.show_memory", {"facts": facts or []}, timeout=30.0)

    def memory_add(self, data: dict[str, Any]) -> None:
        """Handle memory add for flow controller."""
        self._safe_call(
            self.brain,
            "brain.memory.add",
            {
                "text": str(data.get("text") or ""),
                "category": data.get("category"),
                "project": data.get("project"),
            },
            timeout=30.0,
        )

    def memory_update(self, data: dict[str, Any]) -> None:
        """Handle memory update for flow controller."""
        self._safe_call(
            self.brain,
            "brain.memory.update",
            {
                "fact_id": str(data.get("id") or data.get("fact_id") or ""),
                "text": str(data.get("text") or ""),
                "category": data.get("category"),
                "project": data.get("project"),
            },
            timeout=30.0,
        )

    def memory_delete(self, data: dict[str, Any]) -> None:
        """Handle memory delete for flow controller."""
        self._safe_call(
            self.brain,
            "brain.memory.delete",
            {"fact_id": str(data.get("id") or data.get("fact_id") or "")},
            timeout=30.0,
        )

    def open_addons(self, addon_id: str = "") -> None:
        """Open addons."""
        result = self._safe_call(self.brain, "brain.addons.list", timeout=30.0) or {}
        if not isinstance(result, dict):
            result = {}
        self._safe_call(
            self.ui,
            "ui.show_addons",
            {
                "addons": result.get("addons") or [],
                "addons_dir": str(result.get("addons_dir") or ""),
            },
            timeout=30.0,
        )
        if addon_id:
            addon = next(
                (
                    item
                    for item in result.get("addons") or []
                    if isinstance(item, dict) and str(item.get("id") or "") == addon_id
                ),
                None,
            )
            if addon is not None:
                self._safe_call(
                    self.ui,
                    "ui.show_addon_settings",
                    {"addon": addon},
                    timeout=30.0,
                )

    def _load_addon_tray_actions(
        self,
        snapshot: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Build enabled tray actions from a snapshot or an explicit refresh."""
        result = snapshot
        if result is None:
            result = self._safe_call(self.brain, "brain.addons.list", timeout=30.0) or {}
        rows = result.get("addons") if isinstance(result, dict) else []
        actions: list[dict[str, str]] = []
        for addon in rows or []:
            if not isinstance(addon, dict) or not bool(addon.get("enabled", True)):
                continue
            addon_id = str(addon.get("id") or addon.get("name") or "").strip()
            for raw_label in addon.get("tray_actions") or []:
                label = str(raw_label or "").strip()
                if addon_id and label:
                    actions.append({"addon_id": addon_id, "label": label})
        return actions

    @staticmethod
    def _addon_action_key(actions: list[dict[str, str]]) -> tuple[tuple[str, str], ...]:
        """Return a deterministic identity for one tray-action snapshot."""
        return tuple(
            (str(item.get("addon_id") or ""), str(item.get("label") or ""))
            for item in actions
            if str(item.get("addon_id") or "") and str(item.get("label") or "")
        )

    def _publish_addon_tray_actions(self, actions: list[dict[str, str]]) -> bool:
        """Rebuild the native tray only when the authoritative action set changed."""
        key = self._addon_action_key(actions)
        if key == self._addon_tray_actions_snapshot:
            return False
        self._addon_tray_actions_snapshot = key
        self._safe_call(
            self.ui,
            "ui.addons.tray_actions",
            {"actions": actions},
            timeout=30.0,
        )
        return True

    def _apply_addon_change(self, snapshot: dict[str, Any]) -> None:
        """Apply a pushed enabled/disabled/installed addon snapshot immediately."""
        try:
            self.brain.call("brain.llm.prefix.invalidate", timeout=30.0, wait=False)
        except Exception:
            log.exception("Ollama static-prefix invalidation did not start")
        default_caller = self._caller(0) or _all_context_off_policy()
        self._prewarm_llm_prefix(default_caller, route_kind="query")
        self._prewarm_llm_prefix(default_caller, route_kind="chat")
        actions = self._load_addon_tray_actions(snapshot)
        changed = self._publish_addon_tray_actions(actions)
        if changed:
            log.info(
                "addon tray actions updated: reason=%s addon=%s actions=%s",
                str(snapshot.get("reason") or "changed"),
                str(snapshot.get("addon_id") or ""),
                len(actions),
            )

    def refresh_addon_tray_actions(self) -> None:
        """Mirror enabled addon actions into the existing native OpenWand tray."""
        self._publish_addon_tray_actions(self._load_addon_tray_actions())

    def open_settings(self, initial_page: str = "") -> None:
        """Open settings with live addon model tools from the brain process."""
        self._safe_call(
            self.ui,
            "ui.show_settings",
            {
                "extra_tools": self._addon_model_tool_payloads(),
                "initial_page": initial_page or None,
            },
            timeout=30.0,
        )

    def _settings_dialog_is_open(self) -> bool:
        """Return whether Settings is visible in the UI worker."""
        result = self._safe_call(self.ui, "ui.settings.is_open", timeout=2.0) or {}
        return bool(isinstance(result, dict) and result.get("open"))

    def _worker_status_row(self, name: str, worker: WorkerLike) -> dict[str, Any]:
        """Build one worker status row without making an IPC round trip."""
        spec = getattr(worker, "spec", None)
        stderr_tail = ""
        tail_fn = getattr(worker, "stderr_tail", None)
        if callable(tail_fn):
            try:
                stderr_tail = str(tail_fn(30) or "")
            except Exception:
                stderr_tail = ""
        alive_fn = getattr(worker, "alive", None)
        try:
            alive = bool(alive_fn()) if callable(alive_fn) else False
        except Exception:
            alive = False
        return {
            "name": name,
            "pid": getattr(worker, "pid", None),
            "alive": alive,
            "module": str(getattr(spec, "module", "") or ""),
            "stderr_tail": stderr_tail,
        }

    def open_runtime_status(self) -> None:
        """Open a live diagnostics view for packaged/no-console runs."""
        workers = [
            self._worker_status_row("native", self.native),
            self._worker_status_row("ui", self.ui),
            self._worker_status_row("brain", self.brain),
            self._worker_status_row("audio", self.audio),
        ]
        # Fold in results that detached installer processes left on disk, then
        # send the full aggregated event backlog; the window streams updates
        # afterwards via ui.runtime_status.append.
        try:
            self.runtime_log.ingest_installer_statuses()
        except Exception:  # noqa: BLE001 - installer status files are best-effort
            log.exception("could not ingest installer status files")
        self._safe_call(
            self.ui,
            "ui.runtime_status.show",
            {
                "workers": workers,
                "log_dir": os.environ.get("OPENWAND_RUN_LOG_DIR", ""),
                "events": self.runtime_log.snapshot(),
            },
            timeout=30.0,
        )

    def addon_run_action(self, data: dict[str, Any]) -> None:
        """Handle addon run action for flow controller."""
        addon_id = str(data.get("addon_id") or "")
        result = self._safe_call(
            self.brain,
            "brain.addons.run_action",
            {
                "addon_id": addon_id,
                "label": str(data.get("label") or ""),
            },
            timeout=60.0,
        )
        message = "Addon action finished."
        if isinstance(result, dict) and result.get("message"):
            message = str(result["message"])
        workspace_opened = False
        if isinstance(result, dict) and result.get("virtual_workspace_url"):
            opened = self._safe_call(
                self.ui,
                "ui.show_virtual_workspace",
                {"endpoint": str(result["virtual_workspace_url"])},
                timeout=10.0,
            )
            if not (isinstance(opened, dict) and opened.get("shown")):
                message = "The workspace started, but its native window could not be shown."
            else:
                workspace_opened = True
        if not workspace_opened:
            self._notice(message)
        self.refresh_addon_tray_actions()

    def _handle_model_tool_ui_request(self, payload: Any) -> None:
        """Apply a bounded UI request emitted by a successfully executed model tool."""
        request = payload if isinstance(payload, dict) else {}
        if str(request.get("action") or "") != "show_virtual_workspace":
            return
        endpoint = str(request.get("endpoint") or "")
        opened = self._safe_call(
            self.ui,
            "ui.show_virtual_workspace",
            {"endpoint": endpoint, "activate": False},
            timeout=10.0,
        )
        if not (isinstance(opened, dict) and opened.get("shown")):
            self._notice(
                "The virtual workspace started, but its window could not be shown.",
                severity="warning",
            )
        self.refresh_addon_tray_actions()

    def chat_message_actions(self, data: dict[str, Any] | None = None) -> None:
        """Send enabled addon message actions to the open Chat window."""
        result = self._safe_call(
            self.brain,
            "brain.addons.message_actions",
            {"payload": {"surface": "chat", "role": "assistant", **(data or {})}},
            timeout=30.0,
        )
        actions = result.get("actions") if isinstance(result, dict) else []
        self._safe_call(
            self.ui,
            "ui.chat.message_actions",
            {"actions": actions or []},
            timeout=30.0,
        )

    def addon_run_message_action(self, data: dict[str, Any]) -> None:
        """Run a formatted-message workflow and return it to the canonical turn."""
        addon_id = str(data.get("addon_id") or "")
        action_id = str(data.get("action_id") or "")
        result = self._safe_call(
            self.brain,
            "brain.addons.run_message_action",
            {
                "addon_id": addon_id,
                "action_id": action_id,
                "payload": {
                    key: data.get(key)
                    for key in (
                        "surface", "role", "message_id", "conversation_id",
                        "text", "user_prompt", "presentation_status",
                    )
                },
            },
            timeout=300.0,
        )
        if not isinstance(result, dict):
            result = {"status": "Formatting failed. Original kept."}
        self._safe_call(
            self.ui,
            "ui.chat.message_action_result",
            {
                "conversation_id": str(data.get("conversation_id") or ""),
                "message_id": str(data.get("message_id") or ""),
                "addon_id": addon_id,
                "action_id": action_id,
                "result": result,
            },
            timeout=30.0,
        )

    def addon_set_enabled(self, data: dict[str, Any]) -> None:
        """Handle addon set enabled for flow controller."""
        addon_id = str(data.get("addon_id") or "")
        if not addon_id:
            return
        self._safe_call(
            self.brain,
            "brain.addons.set_enabled",
            {"addon_id": addon_id, "enabled": bool(data.get("enabled"))},
            timeout=30.0,
        )
        self.chat_message_actions()
        self.refresh_addon_tray_actions()
        self.open_addons()  # refresh the dialog so it reflects the new state

    def addon_set_action_enabled(self, data: dict[str, Any]) -> None:
        """Persist one addon action-file toggle and refresh exposed surfaces."""
        addon_id = str(data.get("addon_id") or "")
        action_id = str(data.get("action_id") or "")
        if not addon_id or not action_id:
            return
        self._safe_call(
            self.brain,
            "brain.addons.set_action_enabled",
            {
                "addon_id": addon_id,
                "action_id": action_id,
                "enabled": bool(data.get("enabled")),
            },
            timeout=30.0,
        )
        self.chat_message_actions()
        self.open_addons()

    def addon_set_setting(self, data: dict[str, Any]) -> None:
        """Handle addon set setting for flow controller."""
        addon_id = str(data.get("addon_id") or "")
        key = str(data.get("key") or "")
        if not addon_id or not key:
            return
        self._safe_call(
            self.brain,
            "brain.addons.set_setting",
            {"addon_id": addon_id, "key": key, "value": data.get("value")},
            timeout=30.0,
        )
        self.chat_message_actions()

    def addon_repair_environment(self, data: dict[str, Any]) -> None:
        """Handle addon repair environment for flow controller."""
        addon_id = str(data.get("addon_id") or "")
        if not addon_id:
            return
        result = self._safe_call(
            self.brain,
            "brain.addons.repair_environment",
            {"addon_id": addon_id},
            timeout=600.0,
        )
        message = "Addon dependency environment repaired."
        if isinstance(result, dict) and not result.get("ready", True):
            message = str(result.get("error") or "Addon dependency environment is not ready.")
        self._notice(message)
        self.refresh_addon_tray_actions()
        self.open_addons()

    def addon_approve(self, data: dict[str, Any]) -> None:
        """Approve an addon's current access and refresh exposed surfaces."""
        addon_id = str(data.get("addon_id") or "")
        if not addon_id:
            return
        result = self._safe_call(
            self.brain,
            "brain.addons.approve",
            {"addon_id": addon_id},
            timeout=600.0,
        )
        message = "Addon approved and ready."
        if isinstance(result, dict) and str(result.get("status") or "") != "loaded":
            message = str(result.get("error") or "Addon approved, but it is not ready yet.")
        self._notice(message)
        self.chat_message_actions()
        self.refresh_addon_tray_actions()
        self.open_addons()

    def addon_install_archive(self, data: dict[str, Any]) -> None:
        """Handle addon install archive for flow controller."""
        path = str(data.get("path") or "")
        if not path:
            return
        result = self._safe_call(
            self.brain,
            "brain.addons.install_archive",
            {"path": path},
            timeout=120.0,
        )
        message = "Addon archive installed."
        if isinstance(result, dict) and result.get("id"):
            message = f"Installed addon: {result['id']}"
        self._notice(message)
        self.refresh_addon_tray_actions()
        self.open_addons()

    def addon_install_folder(self, data: dict[str, Any]) -> None:
        """Handle addon install folder for flow controller."""
        path = str(data.get("path") or "")
        if not path:
            return
        result = self._safe_call(
            self.brain,
            "brain.addons.install_folder",
            {"path": path},
            timeout=120.0,
        )
        message = "Addon folder installed."
        if isinstance(result, dict) and result.get("id"):
            message = f"Installed addon: {result['id']}"
        self._notice(message)
        self.refresh_addon_tray_actions()
        self.open_addons()

    def addon_run_hotkey(self, data: dict[str, Any]) -> None:
        """Handle addon run hotkey for flow controller."""
        addon_id = str(data.get("addon_id") or "")
        hotkey_id = str(data.get("hotkey_id") or "")
        if not addon_id or not hotkey_id:
            return
        result = self._safe_call(
            self.brain,
            "brain.addons.run_hotkey",
            {"addon_id": addon_id, "hotkey_id": hotkey_id},
            timeout=60.0,
        )
        if isinstance(result, dict):
            prompt = str(result.get("prompt") or "").strip()
            if prompt:
                self.intent_chosen(prompt)
                return
            notify = result.get("notify")
            if isinstance(notify, dict):
                notify_result = self._safe_call(
                    self.native,
                    "native.notify",
                    {
                        "title": str(notify.get("title") or "OpenWand"),
                        "message": str(notify.get("message") or ""),
                    },
                    timeout=10.0,
                )
                if not (isinstance(notify_result, dict) and notify_result.get("ok")):
                    self._notice(str(notify.get("message") or "Addon notification."))
                return
            llm = result.get("llm")
            if isinstance(llm, dict):
                llm_result = self._safe_call(
                    self.brain,
                    "brain.addons.llm_call",
                    {
                        "addon_id": addon_id,
                        "prompt": str(llm.get("prompt") or ""),
                        "max_tokens": int(llm.get("max_tokens") or 512),
                    },
                    timeout=120.0,
                )
                if isinstance(llm_result, dict) and llm_result.get("text"):
                    self._notice(str(llm_result["text"]))
                return
            message = str(result.get("message") or "").strip()
            if message:
                self._notice(message)

    def _addon_hotkeys(self) -> list[dict[str, Any]]:
        """Handle addon hotkeys for flow controller."""
        result = self._safe_call(self.brain, "brain.addons.list", timeout=30.0) or {}
        if not isinstance(result, dict):
            return []
        out: list[dict[str, Any]] = []
        for addon in result.get("addons") or []:
            if not isinstance(addon, dict):
                continue
            addon_id = str(addon.get("id") or addon.get("name") or "")
            for item in addon.get("hotkeys") or []:
                if not isinstance(item, dict):
                    continue
                combo = str(item.get("hotkey") or "")
                hotkey_id = str(item.get("id") or "")
                if addon_id and combo and hotkey_id:
                    out.append({"addon_id": addon_id, "id": hotkey_id, "hotkey": combo})
        return out

    def _show_addon_notifications(self) -> None:
        """Show addon notifications."""
        result = self._safe_call(self.brain, "brain.addons.list", timeout=30.0) or {}
        if not isinstance(result, dict):
            return
        for addon in result.get("addons") or []:
            if not isinstance(addon, dict):
                continue
            for item in addon.get("notifications") or []:
                if not isinstance(item, dict):
                    continue
                message = str(item.get("message") or "")
                if not message:
                    continue
                notify_result = self._safe_call(
                    self.native,
                    "native.notify",
                    {
                        "title": str(item.get("title") or addon.get("name") or "OpenWand"),
                        "message": message,
                    },
                    timeout=10.0,
                )
                if not (isinstance(notify_result, dict) and notify_result.get("ok")):
                    self._notice(message)

    def open_agent_task(self, spec: dict[str, Any] | None = None) -> None:
        """Open agent task."""
        params = {"spec": spec} if isinstance(spec, dict) and spec else {}
        self._safe_call(self.ui, "ui.show_agent_task", params, timeout=30.0)

    def open_agent_history(self) -> None:
        """Open agent history."""
        result = self._safe_call(
            self.brain,
            "brain.agent.history.list",
            {"limit": 100},
            timeout=30.0,
        ) or {}
        if not isinstance(result, dict):
            result = {}
        self._safe_call(
            self.ui,
            "ui.show_agent_history",
            {
                "runs_root": str(result.get("runs_root") or ""),
                "runs": list(result.get("runs") or []),
            },
            timeout=30.0,
        )

    def run_agent_task(self, spec: dict[str, Any]) -> None:
        """Run agent task."""
        if not isinstance(spec, dict) or not spec:
            self._notice("Agent Team spec was empty.")
            return

        timeout = max(600.0, float(spec.get("max_runtime_minutes") or 60) * 60.0 + 120.0)
        done_seen = False
        stream_id: Any = None

        def on_started(req_id: Any) -> None:
            """Handle started events."""
            nonlocal stream_id
            stream_id = req_id
            with self._lock:
                self._active_agent_stream_id = req_id

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            """Handle event events."""
            nonlocal done_seen
            params = payload if isinstance(payload, dict) else {"data": payload}
            if event == "agent.log":
                self._safe_call(self.ui, "ui.agent.log", params, timeout=30.0)
            elif event == "agent.trace":
                self._safe_call(self.ui, "ui.agent.trace", params, timeout=30.0)
            elif event == "agent.approval.request":
                result = self._safe_call(self.ui, "ui.agent.approval.request", params, timeout=30.0) or {}
                accepted = bool(result.get("accepted")) if isinstance(result, dict) else False
                approval_id = str(params.get("approval_id") or "").strip()
                if approval_id and not accepted:
                    self._notice("Agent approval could not be shown; declining the request.")
                    self._safe_call(
                        self.brain,
                        "brain.agent.approval.respond",
                        {"approval_id": approval_id, "approved": False},
                        timeout=30.0,
                    )
            elif event == "agent.done":
                done_seen = True
                self._safe_call(self.ui, "ui.agent.done", params, timeout=30.0)

        try:
            result = self._brain_call_with_events(
                "brain.agent.run",
                {"spec": spec},
                timeout=timeout,
                on_event=on_event,
                on_started=on_started,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("agent task failed")
            self._safe_call(
                self.ui,
                "ui.agent.done",
                {"error": f"{type(exc).__name__}: {exc}"},
                timeout=30.0,
            )
            return
        finally:
            with self._lock:
                if self._active_agent_stream_id == stream_id:
                    self._active_agent_stream_id = None

        if not done_seen and isinstance(result, dict):
            self._safe_call(self.ui, "ui.agent.done", result, timeout=30.0)

    def cancel_agent_task(self) -> None:
        """Cancel agent task."""
        with self._lock:
            target = self._active_agent_stream_id
        if target is None:
            self._notice("No Agent Team is running.")
            return
        result = self._safe_call(self.brain, "brain.cancel", {"target": target}, timeout=10.0) or {}
        if isinstance(result, dict) and result.get("cancelled"):
            self._notice("Agent Team cancellation requested.")
        else:
            self._notice("Agent Team was not running.")

    def control_agent_task(self, data: dict[str, Any]) -> None:
        """Send a cooperative control command to the active agent task."""
        with self._lock:
            target = self._active_agent_stream_id
        if target is None:
            self._notice("No Agent Team is running.")
            return
        payload = dict(data or {})
        payload["target"] = target
        result = self._safe_call(self.brain, "brain.agent.control", payload, timeout=30.0) or {}
        if isinstance(result, dict) and result.get("message"):
            self._notice(str(result["message"]))

    def respond_agent_approval(self, data: dict[str, Any]) -> None:
        """Handle respond agent approval for flow controller."""
        approval_id = str(data.get("approval_id") or "").strip()
        if not approval_id:
            self._notice("Agent approval response was missing an id.")
            return
        result = self._safe_call(
            self.brain,
            "brain.agent.approval.respond",
            {"approval_id": approval_id, "approved": bool(data.get("approved", False))},
            timeout=30.0,
        ) or {}
        if isinstance(result, dict) and result.get("message"):
            self._notice(str(result["message"]))

    def read_agent_history(self, run_dir: str) -> None:
        """Read agent history."""
        if not run_dir:
            self._safe_call(
                self.ui,
                "ui.agent.history.detail",
                {"error": "run_dir is required"},
                timeout=30.0,
            )
            return
        try:
            result = self.brain.call(
                "brain.agent.history.read",
                {"run_dir": run_dir},
                timeout=30.0,
            ) or {}
        except Exception as exc:  # noqa: BLE001
            log.exception("agent history read failed")
            result = {"run_dir": run_dir, "error": f"{type(exc).__name__}: {exc}"}
        self._safe_call(self.ui, "ui.agent.history.detail", result, timeout=30.0)

    def retry_agent_history(self, run_dir: str) -> None:
        """Handle retry agent history for flow controller."""
        self._open_agent_spec_from_history("brain.agent.history.retry_spec", run_dir)

    def continue_agent_history(self, run_dir: str) -> None:
        """Handle continue agent history for flow controller."""
        self._open_agent_spec_from_history("brain.agent.history.continue_spec", run_dir)

    def _open_agent_spec_from_history(self, method: str, run_dir: str) -> None:
        """Open agent spec from history."""
        if not run_dir:
            self._notice("Choose an agent run first.")
            return
        result = self._safe_call(self.brain, method, {"run_dir": run_dir}, timeout=30.0) or {}
        spec = result.get("spec") if isinstance(result, dict) else None
        if isinstance(spec, dict) and spec:
            self.open_agent_task(spec)
        else:
            self._notice("Could not load that Agent Team spec.")

    # -- core flows -----------------------------------------------------

    def _new_action_progress(
        self,
        action_id: str,
        *,
        app: str,
        trace: ActionTrace | None = None,
    ) -> ActionProgress:
        """Create one monotonic action progress stream backed by the reply bubble."""

        def publish(update: ActionProgressUpdate) -> None:
            self._safe_call(self.ui, "ui.action.progress", update.to_dict(), timeout=30.0)

        def record(update: ActionProgressUpdate) -> None:
            if trace is not None:
                trace.mark(
                    "progress_updated",
                    progress_stage=update.stage,
                    progress_sequence=update.sequence,
                    terminal=update.terminal,
                )

        return ActionProgress(action_id, app=app, sink=publish, telemetry=record)

    def _start_action_progress_heads_up(
        self,
        generation: int,
        finished: threading.Event,
        progress: ActionProgress,
        stage: ActionProgressStage,
        text: str,
    ) -> threading.Timer:
        """Refresh a long-running stage with an honest four-second heads-up."""

        def show_heads_up() -> None:
            if finished.is_set() or not self._is_current(generation):
                return
            try:
                progress.advance(stage, text)
            except (RuntimeError, ValueError):
                # Completion or another stage won the timer race.
                return

        timer = threading.Timer(_ACTION_PROGRESS_HEADS_UP_SECONDS, show_heads_up)
        timer.daemon = True
        timer.start()
        return timer

    def _start_slow_response_notice(
        self,
        generation: int,
        activity_seen: threading.Event,
        text: str,
    ) -> threading.Timer:
        """Show one honest progress update only when useful output is late."""
        def show_notice() -> None:
            if activity_seen.is_set() or not self._is_current(generation):
                return
            self._on_reply_chunk(
                {"text": text, "is_progress": True},
                thought_parser=None,
            )

        timer = threading.Timer(_SLOW_RESPONSE_NOTICE_SECONDS, show_notice)
        timer.daemon = True
        timer.start()
        return timer

    def _query(
        self,
        prompt: str,
        pending: PendingInvocation,
        *,
        preserve_reply_bubble: bool = False,
    ) -> None:
        """Run a prompt through the pipeline: stop audio, show 'thinking', stream the reply."""
        import config

        self._reload_supervisor_config_if_changed()
        query_started = time.monotonic()
        gen = self._new_generation()
        auto_open_chat = bool(getattr(config, "CHAT_OPEN_ON_PROMPT", False))
        suppress_reply_bubble = bool(
            auto_open_chat
            and getattr(config, "CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE", False)
        )
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        if not preserve_reply_bubble:
            self._safe_call(
                self.ui,
                "ui.reply.presentation",
                {"suppress_bubble": suppress_reply_bubble},
                timeout=30.0,
            )
            self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
            self._safe_call(self.ui, "ui.reply.thinking", timeout=30.0)
        if auto_open_chat:
            self._safe_call(
                self.ui,
                "ui.show_chat",
                {"new": False},
                timeout=30.0,
            )
        response_activity = threading.Event()
        slow_notice_timer = self._start_slow_response_notice(
            gen,
            response_activity,
            t("This is taking a little longer. I'm still working on it."),
        )
        try:
            params = self._brain_query_params(prompt, pending)
        except Exception:
            response_activity.set()
            slow_notice_timer.cancel()
            raise
        harness_mode = str(getattr(config, "CHAT_EXECUTION_MODE", "openwand") or "openwand").strip().lower()
        if harness_mode not in {"openwand", "codex", "claude"}:
            harness_mode = "openwand"
        params["harness_provider"] = harness_mode
        conversation_owner = str(
            getattr(config, "CHAT_CONVERSATION_OWNER", "openwand") or "openwand"
        ).strip().lower()
        params["conversation_owner"] = conversation_owner if conversation_owner in {"openwand", "agent"} else "openwand"
        configured_harness_workspace = (
            _configured_harness_workspace(harness_mode)
            if harness_mode in {"codex", "claude"}
            else ""
        )
        if configured_harness_workspace:
            params["harness_cwd"] = configured_harness_workspace
        self._discard_unused_pending_context(pending, params)
        log.info(
            "query context ready in %.2fs prompt_chars=%d ambient_chars=%d "
            "selected_chars=%d screenshot=%s screenshot_tool=%s tools=%s",
            time.monotonic() - query_started,
            len(prompt or ""),
            len(str(params.get("ambient_text") or "")),
            len(str(params.get("selected") or "")),
            bool(params.get("screenshot_b64")),
            params.get("screenshot_tool_b64") is not None,
            bool(params.get("use_tools")),
        )
        summary = params.pop("_ui_context_summary", [])
        # Persist the exact context that accompanied this user turn.  Selected
        # text is sent to the model in its own payload field, so folding only
        # ambient_text into Chat made the most important source invisible in
        # the transcript even though the model had received it.
        selected_context = str(params.get("selected") or "").strip()
        ambient_context = str(params.get("ambient_text") or "").strip()
        chat_context = "\n\n".join(
            part
            for part in (
                f"[Selected text]\n{selected_context}" if selected_context else "",
                ambient_context,
            )
            if part
        )
        if summary:
            self._safe_call(self.ui, "ui.context.summary", {"items": summary}, timeout=30.0)

        done_seen = False
        first_chunk_seen = False
        reply_parser_finished = False
        streamed_reply_parts: list[str] = []
        tts_segmenter = _TtsSegmentBuffer() if self._tts_replies_enabled() else None
        early_chat_index: int | None = None
        try:
            from core.assistant_text import ThoughtStreamParser

            reply_thought_parser = ThoughtStreamParser()
        except Exception:
            reply_thought_parser = None

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            """Handle event events."""
            nonlocal done_seen, first_chunk_seen, reply_parser_finished
            if event == "reply.chunk":
                response_activity.set()
                slow_notice_timer.cancel()
                if not self._is_current(gen):
                    return
                if not first_chunk_seen:
                    first_chunk_seen = True
                    log.info("query first reply chunk after %.2fs", time.monotonic() - query_started)
                if not bool((payload or {}).get("is_progress")) and not bool((payload or {}).get("is_thought")):
                    streamed_reply_parts.append(str((payload or {}).get("text") or ""))
                if early_chat_index is not None:
                    self._safe_call(
                        self.ui,
                        "ui.chat.chunk",
                        {
                            "conversation_index": early_chat_index,
                            "text": str((payload or {}).get("text") or ""),
                            "is_progress": bool((payload or {}).get("is_progress")),
                            "is_thought": bool((payload or {}).get("is_thought")),
                        },
                        timeout=30.0,
                    )
                if self._reply_bubble_cancelled(gen):
                    return
                for segment, is_thought, is_progress in self._on_reply_chunk(
                    payload,
                    thought_parser=reply_thought_parser,
                ):
                    if tts_segmenter is not None and is_progress and not is_thought:
                        self._queue_tts_segment(gen, segment)
                    elif tts_segmenter is not None and not is_thought:
                        for tts_segment in tts_segmenter.feed(segment):
                            self._queue_tts_segment(gen, tts_segment)
            elif event == "reply.done":
                response_activity.set()
                slow_notice_timer.cancel()
                if not self._is_current(gen):
                    return
                done_seen = True
                self._emit_file_context_progress(
                    list((payload or {}).get("file_context") or []),
                    conversation_index=early_chat_index,
                )
                generated_attachments = list((payload or {}).get("attachments") or [])
                if generated_attachments and not self._reply_bubble_cancelled(gen):
                    self._safe_call(
                        self.ui,
                        "ui.reply.image",
                        {"attachments": generated_attachments},
                        timeout=30.0,
                    )
                text_done = str((payload or {}).get("text") or "")
                if text_done:
                    self._last_reply = text_done
                if not (self._tts_replies_enabled() and text_done):
                    self._on_reply_done(payload, thought_parser=reply_thought_parser)
                    reply_parser_finished = True
            elif event == "live_file.activity":
                if early_chat_index is not None:
                    self._safe_call(
                        self.ui,
                        "ui.chat.chunk",
                        {
                            "conversation_index": early_chat_index,
                            "local_work": dict(payload or {}),
                        },
                        timeout=30.0,
                    )
            elif event == "harness.activity":
                if early_chat_index is not None:
                    self._safe_call(
                        self.ui,
                        "ui.chat.chunk",
                        {
                            "conversation_index": early_chat_index,
                            "harness_activity": dict(payload or {}),
                        },
                        timeout=30.0,
                    )
            elif event == "model_tool.ui.request":
                self._handle_model_tool_ui_request(payload)
            elif event == "live_file.approval.request":
                self._handle_live_file_approval_request(payload)
            elif event == "privacy.review.request":
                self._handle_privacy_review_request(payload)

        # Continue the conversation selected in the chat window: replay its prior
        # turns so the model has full context. ui_host is the source of truth for
        # the active conversation (empty on a fresh start -> new conversation).
        try:
            hist = self._safe_call(self.ui, "ui.chat.active_history", {}, timeout=10.0)
            if isinstance(hist, dict):
                if hist.get("conversation_id"):
                    params["privacy_session_id"] = str(hist["conversation_id"])
                if hist.get("history"):
                    params["history"] = hist["history"]
                prior_context = str(hist.get("context") or "").strip()
                if prior_context:
                    base = str(params.get("ambient_text") or "")
                    block = f"[Conversation Context]\n{prior_context}"
                    params["ambient_text"] = (base + "\n\n" + block).strip() if base else block
                file_ctx = _file_context_text(list(hist.get("file_context") or []))
                if file_ctx:
                    base = str(params.get("ambient_text") or "")
                    params["ambient_text"] = (base + "\n\n" + file_ctx).strip() if base else file_ctx
                # Scope memory (retrieval + saves) to the conversation's project.
                params["memory_project"] = hist.get("project_id")
                harness_sessions = hist.get("harness_sessions") if isinstance(hist.get("harness_sessions"), dict) else {}
                if harness_mode in {"codex", "claude"}:
                    selected_session = harness_sessions.get(harness_mode)
                    if isinstance(selected_session, dict):
                        params["harness_session"] = dict(selected_session)
                    params["harness_cwd"] = (
                        configured_harness_workspace
                        or str(hist.get("harness_cwd") or "")
                    )
        except Exception:
            log.exception("failed to fetch active conversation history")

        context_policy = params.pop("context_policy", {})
        user_annotations = self._chat_text_annotations(prompt, role="user")
        try:
            begin_result = self._safe_call(
                self.ui,
                "ui.chat.begin_conversation",
                {
                    "user": prompt,
                    "context": chat_context,
                    "image_base64": params.get("screenshot_b64"),
                    "context_policy": context_policy,
                    "user_annotations": user_annotations,
                },
                timeout=30.0,
            )
            if isinstance(begin_result, dict) and begin_result.get("started"):
                if begin_result.get("conversation_id"):
                    params["privacy_session_id"] = str(begin_result["conversation_id"])
                raw_idx = begin_result.get("conversation_index")
                if isinstance(raw_idx, int):
                    early_chat_index = raw_idx
        except Exception:
            log.exception("failed to begin chat conversation before query")
        try:
            log.info("query brain call started")
            result = self._brain_reply_call_with_events(
                "brain.query",
                params,
                timeout=self._interactive_llm_timeout_seconds(params),
                on_event=on_event,
                generation=gen,
            )
        except Exception as exc:  # noqa: BLE001 - surface route/config failures in the UI
            response_activity.set()
            slow_notice_timer.cancel()
            log.exception("brain query failed after %.2fs", time.monotonic() - query_started)
            if not self._is_current(gen):
                if early_chat_index is not None:
                    self._safe_call(
                        self.ui,
                        "ui.chat.done",
                        {"conversation_index": early_chat_index, "text": ""},
                        timeout=30.0,
                    )
                return
            if early_chat_index is not None:
                self._safe_call(
                    self.ui,
                    "ui.chat.done",
                    {"conversation_index": early_chat_index, "text": ""},
                    timeout=30.0,
                )
            self._notice(f"LLM request failed: {self._friendly_error(exc)}", severity="error")
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            self._set_idle()
            return
        response_activity.set()
        slow_notice_timer.cancel()
        log.info("query brain call finished after %.2fs", time.monotonic() - query_started)
        if not self._is_current(gen):
            if early_chat_index is not None:
                self._safe_call(
                    self.ui,
                    "ui.chat.done",
                    {"conversation_index": early_chat_index, "text": ""},
                    timeout=30.0,
                )
            return
        if reply_thought_parser is not None and not reply_parser_finished:
            for segment, is_thought in reply_thought_parser.finish():
                if segment:
                    self._safe_call(
                        self.ui,
                        "ui.reply.chunk",
                        {"text": segment, "is_thought": bool(is_thought)},
                        timeout=30.0,
                    )
                    if tts_segmenter is not None and not is_thought:
                        for tts_segment in tts_segmenter.feed(segment):
                            self._queue_tts_segment(gen, tts_segment)
        text = str((result or {}).get("text") or "")
        file_context = list((result or {}).get("file_context") or [])
        assistant_attachments = list((result or {}).get("attachments") or [])
        display_segments = list((result or {}).get("display_segments") or [])
        harness = dict((result or {}).get("harness") or {})
        if not done_seen:
            self._emit_file_context_progress(file_context, conversation_index=early_chat_index)
        privacy_report = (result or {}).get("privacy_report") if isinstance(result, dict) else None
        self._last_privacy_report = privacy_report if isinstance(privacy_report, dict) else {}
        self._last_reply = text
        assistant_annotations = self._chat_text_annotations(text, role="assistant")
        bubble_cancelled = self._reply_bubble_cancelled(gen)
        if assistant_attachments and not done_seen and not bubble_cancelled:
            self._safe_call(
                self.ui,
                "ui.reply.image",
                {"attachments": assistant_attachments},
                timeout=30.0,
            )
        if early_chat_index is not None:
            self._safe_call(
                self.ui,
                "ui.chat.done",
                {
                    "conversation_index": early_chat_index,
                    "text": text,
                    "file_context": file_context,
                    "tool_context": _normalized_tool_context(
                        {
                            "allowed_tools": params.get("allowed_tools") or [],
                            "pinned_tools": params.get("pinned_tools") or [],
                            "file_access_mode": params.get("file_access_mode") or "",
                        }
                    ),
                    "annotations": assistant_annotations,
                    "display_segments": display_segments,
                    "assistant_attachments": assistant_attachments,
                    "harness": harness,
                },
                timeout=30.0,
            )
        if (
            text
            and not bubble_cancelled
            and not suppress_reply_bubble
            and "".join(streamed_reply_parts) != text
        ):
            self._replace_reply_text(text)
        if tts_segmenter is not None and not bubble_cancelled:
            for tts_segment in tts_segmenter.finish():
                self._queue_tts_segment(gen, tts_segment)
            self._finish_tts_sequence(gen)
        if text or assistant_attachments:
            tool_context = _normalized_tool_context(
                {
                    "allowed_tools": params.get("allowed_tools") or [],
                    "pinned_tools": params.get("pinned_tools") or [],
                    "file_access_mode": params.get("file_access_mode") or "",
                }
            )
            self._safe_call(
                self.ui,
                "ui.chat.add_conversation",
                {
                    "user": prompt,
                    "assistant": text,
                    "assistant_attachments": assistant_attachments,
                    "context": chat_context,
                    "image_base64": params.get("screenshot_b64"),
                    "file_context": file_context,
                    "tool_context": tool_context,
                    "context_policy": context_policy,
                    "user_annotations": user_annotations if early_chat_index is None else [],
                    "assistant_annotations": assistant_annotations,
                    "display_segments": display_segments,
                    "harness": harness,
                    "append_user": early_chat_index is None,
                    "conversation_index": early_chat_index,
                },
                timeout=30.0,
            )
        privacy_count = int((privacy_report or {}).get("count") or 0) if isinstance(privacy_report, dict) else 0
        if privacy_count and not bool((privacy_report or {}).get("reviewed")):
            self._safe_call(
                self.ui,
                "ui.context.summary",
                {
                    "items": [
                        {
                            "label": t("Privacy: {count} redacted").format(count=privacy_count),
                            "type": "privacy",
                        }
                    ]
                },
                timeout=30.0,
            )
            self._safe_call(
                self.ui,
                "ui.privacy.report",
                {"report": privacy_report, "title": t("Privacy Report")},
                timeout=30.0,
            )
        if bubble_cancelled:
            self._set_idle()
        elif self._is_current(gen) and text and self._tts_replies_enabled():
            if not self._tts_sequence_is_active():
                self._speak_text(text, generation=gen)
        elif text and "".join(streamed_reply_parts) != text:
            self._safe_call(self.ui, "ui.reply.done", {"flush": False}, timeout=30.0)
            self._set_idle()
        elif not done_seen:
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            self._set_idle()

    def _rewrite_and_paste(self, prompt: str, pending: PendingInvocation) -> None:
        """Handle rewrite and paste for flow controller."""
        gen = self._new_generation()
        selected = str(pending.context.get("selected_text") or "").strip()
        if not selected:
            self._notice("No selected text to rewrite.")
            self._set_idle()
            return
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        self._safe_call(self.ui, "ui.reply.thinking", timeout=30.0)
        response_activity = threading.Event()
        slow_notice_timer = self._start_slow_response_notice(
            gen,
            response_activity,
            t("This is taking a little longer. I'm still preparing the rewrite."),
        )
        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            """Handle event events."""
            if event == "reply.chunk":
                response_activity.set()
                slow_notice_timer.cancel()
                if self._is_current(gen):
                    self._on_reply_chunk(payload, thought_parser=None)
            elif event == "reply.done":
                response_activity.set()
                slow_notice_timer.cancel()
            elif event == "privacy.review.request":
                self._handle_privacy_review_request(payload)

        try:
            query_params = self._brain_query_params(prompt, pending)
            rewrite_context = self._rewrite_context_from_query_params(query_params)
            log.info(
                "rewrite request context: prompt_chars=%d selected_chars=%d "
                "source_chars=%d source_labels=%r",
                len(prompt or ""),
                len(selected),
                len(rewrite_context),
                self._rewrite_source_labels(rewrite_context),
            )
            result = self._brain_reply_call_with_events(
                "brain.rewrite",
                {
                    "selected_text": selected,
                    "intent_prompt": prompt,
                    "rewrite_context": rewrite_context,
                },
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=on_event,
                generation=gen,
            )
        except Exception as exc:  # noqa: BLE001 - surface route/config failures in the UI
            log.exception("brain rewrite failed")
            if not self._is_current(gen):
                return
            self._notice(f"Rewrite failed: {self._friendly_error(exc)}", severity="error")
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            self._set_idle()
            return
        finally:
            response_activity.set()
            slow_notice_timer.cancel()
        text = str((result or {}).get("text") or "").strip()
        visible_text = str((result or {}).get("visible_text") or "").strip()
        privacy_report = (result or {}).get("privacy_report") if isinstance(result, dict) else None
        self._last_privacy_report = privacy_report if isinstance(privacy_report, dict) else {}
        if (
            isinstance(privacy_report, dict)
            and privacy_report.get("count")
            and not privacy_report.get("reviewed")
        ):
            self._safe_call(
                self.ui,
                "ui.privacy.report",
                {"report": privacy_report, "title": t("Privacy Report")},
                timeout=30.0,
            )
        if not visible_text and text:
            visible_text = t("Replacement pasted.")
        log.info("rewrite result: text_chars=%d visible_chars=%d", len(text), len(visible_text))
        bubble_cancelled = self._reply_bubble_cancelled(gen)
        if text and self._is_current(gen) and not bubble_cancelled:
            chat_context = "\n\n".join(
                part
                for part in (
                    f"[Selected text]\n{selected}",
                    rewrite_context,
                )
                if str(part or "").strip()
            )
            paste = self.native.call(
                "native.paste_text",
                {
                    "text": text,
                    "target_pid": pending.paste_target_pid,
                    "focus_token": int(pending.context.get("focus_token") or 0),
                    "restore_clipboard": True,
                },
                timeout=30.0,
            )
            paste = paste if isinstance(paste, dict) else {}
            log.info(
                "rewrite paste-back: target_pid=%s result=%s",
                pending.paste_target_pid, paste,
            )
            # Rewrite status must NOT land in the reply bubble (it would clobber the
            # streamed rewrite text). Success is silent - the pasted text in the
            # user's app is the confirmation. Only problems raise a system
            # notification, which needs user action / awareness.
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            if paste.get("ok"):
                if paste.get("clipboard_ok") and paste.get("clipboard_restored") is False:
                    self._native_notify(
                        t("OpenWand pasted the rewrite"),
                        t("The text was replaced, but OpenWand couldn't restore your previous clipboard."),
                    )
                with self._lock:
                    self._last_undoable_edit = UndoableEdit(
                        original_text=selected,
                        replacement_text=text,
                        target_pid=int(pending.paste_target_pid or 0),
                        focus_token=int(pending.context.get("focus_token") or 0),
                    )
                self._safe_call(
                    self.ui,
                    "ui.reply.undo_ready",
                    {
                        "text": visible_text,
                        "timeout_ms": int(_UNDO_EDIT_WINDOW_SECONDS * 1000),
                    },
                    timeout=30.0,
                )
            elif paste.get("clipboard_ok"):
                app = str(paste.get("app_name") or "").strip()
                where = f" into {app}" if app else ""
                log.warning(
                    "rewrite paste-back could not confirm focus%s (frontmost=%s); "
                    "clipboard_restored=%s",
                    where,
                    paste.get("frontmost_pid"),
                    paste.get("clipboard_restored"),
                )
                self._native_notify(
                    t("OpenWand rewrite could not paste"),
                    t("Couldn't replace the selected text. Your clipboard was restored."),
                )
            else:
                log.error("rewrite paste-back failed: %s", paste.get("error") or paste)
                self._native_notify(
                    t("OpenWand - rewrite failed"),
                    t("Couldn't paste the rewrite. See native.stderr.log."),
                )
            self._safe_call(
                self.ui,
                    "ui.chat.add_conversation",
                    {
                        "user": prompt,
                        "assistant": visible_text,
                        "context": chat_context,
                        "context_policy": query_params.get("context_policy") or {},
                        "user_annotations": self._chat_text_annotations(prompt, role="user"),
                        "assistant_annotations": self._chat_text_annotations(visible_text, role="assistant"),
                    },
                timeout=30.0,
            )
        elif self._is_current(gen) and not bubble_cancelled:
            log.warning("rewrite returned empty text; paste-back skipped")
            self._native_notify(
                t("OpenWand rewrite returned nothing"),
                t("The model returned no replacement text."),
            )
        self._set_idle()

    def undo_last_openwand_edit(self) -> None:
        """Undo the most recent confirmed rewrite, or copy its original text."""
        with self._lock:
            edit = self._last_undoable_edit
            self._last_undoable_edit = None
        if edit is None:
            self._undo_result_notice("There is no recent OpenWand edit to undo.", severity="warning")
            return
        if time.monotonic() - edit.created_at > _UNDO_EDIT_WINDOW_SECONDS:
            copied = self._safe_call(
                self.native,
                "native.clipboard.set",
                {"text": edit.original_text},
                timeout=5.0,
            ) or {}
            if isinstance(copied, dict) and copied.get("ok"):
                self._undo_result_notice(
                    "The undo window expired. Original text copied to clipboard.",
                    severity="warning",
                )
            else:
                self._undo_result_notice(
                    "The undo window expired and the original text could not be copied.",
                    severity="error",
                )
            return
        try:
            result = self.native.call(
                "native.undo_edit",
                {
                    "target_pid": edit.target_pid,
                    "focus_token": edit.focus_token,
                    "original_text": edit.original_text,
                    "replacement_text": edit.replacement_text,
                },
                timeout=10.0,
            )
        except Exception as exc:  # noqa: BLE001 - keep the original recoverable
            log.exception("native undo failed")
            result = {"ok": False, "clipboard_ok": False, "error": str(exc)}
        result = result if isinstance(result, dict) else {}
        if result.get("ok"):
            self._undo_result_notice("Last OpenWand edit undone.")
        elif result.get("clipboard_ok"):
            self._undo_result_notice(
                "Couldn't safely undo in the app. Original text copied to clipboard.",
                severity="warning",
            )
        else:
            self._undo_result_notice(
                "Couldn't undo the last OpenWand edit or copy the original text.",
                severity="error",
            )

    def _undo_result_notice(self, text: str, *, severity: str = "") -> None:
        """Show an undo outcome without attaching generic error advice."""
        severity_name = str(severity or "").strip().lower()
        self.runtime_log.append(
            "assistant",
            normalize_severity(severity_name) if severity_name else "info",
            text,
        )
        payload: dict[str, Any] = {
            "text": text,
            "timeout_ms": 6000,
            "log_mirrored": True,
        }
        if severity_name:
            payload["severity"] = severity_name
        self._safe_call(self.ui, "ui.reply.notice", payload, timeout=30.0)

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _rewrite_context_from_query_params(params: dict[str, Any]) -> str:
        """Render the shared Ctrl+Q context payload as source-only rewrite context."""
        parts: list[str] = []
        context_priority = str(params.get("context_priority") or "").strip()
        if context_priority:
            parts.append(f"[Context priority]\nPrioritize {context_priority} when sources disagree.")
        ambient_text = str(params.get("ambient_text") or "").strip()
        if ambient_text:
            parts.append(ambient_text)
        active_document_text = FlowController._rewrite_source_document_text(
            str(params.get("active_document_text") or ""),
            str(params.get("selected") or ""),
        ).strip()
        if active_document_text:
            label = " ".join(str(params.get("active_document_label") or "").split()).strip()
            if label:
                parts.append(
                    f"--- BEGIN ACTIVE DOCUMENT: {label} ---\n"
                    f"{active_document_text}\n"
                    f"--- END ACTIVE DOCUMENT: {label} ---"
                )
            else:
                parts.append(f"[Active document]\n{active_document_text}")
        return "\n\n".join(part for part in parts if str(part or "").strip())

    @staticmethod
    def _rewrite_source_document_text(active_document_text: str, selected_text: str) -> str:
        """Drop target-selection document blocks when other document sources exist."""
        raw = str(active_document_text or "").strip()
        selected = FlowController._rewrite_match_text(selected_text)
        if not raw or not selected:
            return raw
        matches = list(re.finditer(r"(?m)^\[([^\]\n]{1,160})\]\n", raw))
        if len(matches) <= 1:
            return raw

        kept: list[str] = []
        removed = 0
        for idx, match in enumerate(matches):
            label = match.group(1).strip()
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            body = raw[start:end].strip()
            body_match = FlowController._rewrite_match_text(body)
            is_target = bool(body_match) and (selected in body_match or body_match in selected)
            if is_target:
                removed += 1
                continue
            if label and body:
                kept.append(f"[{label}]\n{body}")
        if removed and kept:
            return "\n\n".join(kept)
        return raw

    @staticmethod
    def _rewrite_match_text(text: str) -> str:
        """Normalize text for target/source block matching."""
        return " ".join(str(text or "").split()).casefold()

    @staticmethod
    def _rewrite_source_labels(text: str) -> list[str]:
        """Return source block labels for rewrite diagnostics without logging content."""
        labels: list[str] = []
        for pattern in (
            r"(?m)^--- BEGIN [^-:\n]+: (.{1,160}?) ---$",
            r"(?m)^\[([^\]\n]{1,160})\]$",
        ):
            for match in re.finditer(pattern, str(text or "")):
                label = " ".join(match.group(1).split()).strip()
                if label and label not in labels:
                    labels.append(label)
        return labels[:8]

    @staticmethod
    def _paste_shortcut() -> str:
        """Paste shortcut."""
        return flow_utils.paste_shortcut()

    @staticmethod
    def _is_local_file_request(prompt: str) -> bool:
        """Return True when a paste-back prompt is really asking for disk edits."""
        return flow_utils.is_local_file_request(prompt)

    def _native_notify(self, title: str, message: str) -> None:
        """Best-effort system notification (keeps status out of the reply bubble)."""
        try:
            self.native.call("native.notify", {"title": title, "message": message}, timeout=10.0)
        except Exception:
            log.exception("native.notify failed")

    def _schedule(self, fn, *args) -> None:
        """Run *fn* on a daemon thread, or inline when async is disabled."""
        if not self.run_async:
            fn(*args)
            return
        threading.Thread(target=self._guarded, args=(fn, args), daemon=True).start()

    def _guarded(self, fn, args) -> None:
        """Handle guarded for flow controller."""
        try:
            fn(*args)
        except Exception:
            log.exception("flow %s failed", getattr(fn, "__name__", fn))
            self._set_idle()

    def _safe_call(self, worker: WorkerLike, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> Any:
        """Handle safe call for flow controller."""
        try:
            return worker.call(method, params or {}, timeout=timeout)
        except Exception as exc:
            if self._is_expected_worker_exit(method, exc):
                log.debug("worker unavailable for best-effort call %s: %s", method, exc)
                return None
            log.exception("worker call failed: %s", method)
            return None

    def _publish_runtime_events(self, events: list[dict[str, Any]]) -> None:
        """Push a batch of new runtime events to the open Runtime Status window.

        Deliberately raw (no _fire, no logging): a failure here must not write
        new log records, or a dead UI worker would feed the log from its own
        publish failures. Raising lets the event log disable publishing.
        """
        alive = getattr(self.ui, "alive", None)
        if callable(alive) and not alive():
            # WorkerClient.call would respawn the worker; a cosmetic log push
            # must never be the thing that restarts a dead UI process.
            raise RuntimeError("ui worker is not running")
        self.ui.call("ui.runtime_status.append", {"events": events}, wait=False)

    def _fire(self, worker: WorkerLike, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a fire-and-forget request - the response is not awaited.

        For cosmetic / side-effect calls (e.g. doll animation state, stopping
        speech) that must never sit on the critical path. A slow or wedged worker
        then can't delay the thing the user is actually waiting for."""
        try:
            worker.call(method, params or {}, wait=False)
        except Exception as exc:
            if self._is_expected_worker_exit(method, exc):
                log.debug("worker unavailable for best-effort fire %s: %s", method, exc)
                return
            log.exception("worker fire failed: %s", method)

    @staticmethod
    def _is_expected_worker_exit(method: str, exc: Exception) -> bool:
        """Return True for best-effort UI calls racing a normal UI shutdown."""
        if not method.startswith("ui."):
            return False
        message = str(exc).lower()
        return (
            "worker exited" in message
            or "is not running" in message
            or "broken pipe" in message
            or "write failed" in message
        )

    def _brain_call_with_events(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        on_event: Callable[[str, Any, Any], None],
        on_started: Callable[[Any], None] | None = None,
    ) -> Any:
        """Handle brain call with events for flow controller."""
        call_with_events = getattr(self.brain, "call_with_events", None)
        if callable(call_with_events):
            return call_with_events(
                method,
                params,
                timeout=timeout,
                on_event=on_event,
                on_started=on_started,
            )
        return self.brain.call(method, params, timeout=timeout)

    def _brain_reply_call_with_events(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        on_event: Callable[[str, Any, Any], None],
        generation: int,
    ) -> Any:
        """Track a user-visible reply stream so the bubble Stop button can cancel it."""
        stream_id: Any = None

        def on_started(req_id: Any) -> None:
            nonlocal stream_id
            stream_id = req_id
            with self._lock:
                if generation == self._current_generation:
                    self._active_reply_stream_id = req_id
                    self._active_reply_stream_generation = generation
                    stale = False
                else:
                    stale = True
            if stale:
                # This request was superseded while its worker was starting.
                # WorkerClient publishes ids after writing the request, and the
                # brain host accepts queued cancels, so this cannot miss the race.
                self._safe_call(
                    self.brain,
                    "brain.cancel",
                    {"target": req_id},
                    timeout=5.0,
                )

        try:
            return self._brain_call_with_events(
                method,
                params,
                timeout=timeout,
                on_event=on_event,
                on_started=on_started,
            )
        finally:
            with self._lock:
                if self._active_reply_stream_id == stream_id:
                    self._active_reply_stream_id = None
                    self._active_reply_stream_generation = 0

    def _new_generation(self) -> int:
        """Start a user action and cancel any reply that it supersedes."""
        with self._lock:
            previous_target = self._active_reply_stream_id
            previous_generation = self._active_reply_stream_generation
            self._current_generation = next(self._generation)
            generation = self._current_generation
            if previous_target is not None:
                self._reply_bubble_cancelled_generation = previous_generation
        if previous_target is not None:
            self._safe_call(
                self.brain,
                "brain.cancel",
                {"target": previous_target},
                timeout=5.0,
            )
        return generation

    def _is_current(self, generation: int) -> bool:
        """Return whether current is true."""
        with self._lock:
            return generation == self._current_generation

    def _reply_bubble_cancelled(self, generation: int) -> bool:
        """Return whether bubble/TTS output was muted for this generation."""
        with self._lock:
            return generation == self._reply_bubble_cancelled_generation

    def _claim_voice_start(self) -> bool:
        """Handle claim voice start for flow controller."""
        with self._lock:
            # All three speech entry points share one recorder/microphone.  The
            # inverse guard already exists in _claim_dictate_start; keep this
            # side symmetrical so a rapid F9 press cannot steal an active F8
            # dictation recording.
            if (
                self._voice_state != "idle"
                or self._dictate_state != "idle"
                or self._live_voice_state != "idle"
            ):
                return False
            self._voice_state = "starting"
            self._voice_active = True
            return True

    def _ensure_voice_start_claimed(self) -> None:
        """Ensure voice start claimed."""
        with self._lock:
            if self._voice_state == "idle":
                self._voice_state = "starting"
                self._voice_active = True

    def _mark_voice_recording(self) -> bool:
        """Handle mark voice recording for flow controller."""
        with self._lock:
            if self._voice_state == "starting":
                self._voice_state = "recording"
                return True
            return self._voice_state == "recording"

    def _claim_voice_stop(self) -> bool:
        """Handle claim voice stop for flow controller."""
        with self._lock:
            if self._voice_state == "idle":
                return False
            if self._voice_state == "failed":
                self._voice_state = "idle"
                self._voice_active = False
                return False
            self._voice_state = "stopping"
            self._voice_active = False
            return True

    def _ensure_voice_stop_claimed(self) -> bool:
        """Ensure voice stop claimed."""
        with self._lock:
            if self._voice_state == "idle":
                return False
            if self._voice_state == "failed":
                self._voice_state = "idle"
                self._voice_active = False
                return False
            self._voice_state = "stopping"
            self._voice_active = False
            return True

    def _mark_voice_failed(self) -> None:
        """Keep a failed press claimed until its key-up event arrives."""
        with self._lock:
            self._voice_state = "failed"
            self._voice_active = True

    def _mark_voice_idle(self) -> None:
        """Handle mark voice idle for flow controller."""
        with self._lock:
            self._voice_state = "idle"
            self._voice_active = False

    def _claim_dictate_start(self) -> bool:
        """Handle claim dictate start for flow controller."""
        with self._lock:
            # Mutually exclusive with voice push-to-talk and the live voice
            # conversation (one shared recorder/mic).
            if (
                self._dictate_state != "idle"
                or self._voice_state != "idle"
                or self._live_voice_state != "idle"
            ):
                return False
            self._dictate_state = "recording"
            return True

    def _claim_dictate_stop(self) -> bool:
        """Handle claim dictate stop for flow controller."""
        with self._lock:
            if self._dictate_state == "failed":
                self._dictate_state = "idle"
                return False
            if self._dictate_state != "recording":
                return False
            self._dictate_state = "stopping"
            return True

    def _mark_dictate_failed(self) -> None:
        """Keep a failed dictation press claimed until its key-up event arrives."""
        with self._lock:
            self._dictate_state = "failed"

    def _mark_dictate_idle(self) -> None:
        """Handle mark dictate idle for flow controller."""
        with self._lock:
            self._dictate_state = "idle"

    def _claim_live_voice_toggle(self) -> str | None:
        """Resolve one toggle-hotkey press: "start", "stop", or None.

        None while a start/stop is already in flight, so hammering the key
        can't stack transitions."""
        with self._lock:
            if self._live_voice_state == "idle":
                self._live_voice_state = "starting"
                return "start"
            if self._live_voice_state == "active":
                self._live_voice_state = "stopping"
                return "stop"
            return None

    def _mark_live_voice_active(self) -> None:
        """Handle mark live voice active for flow controller."""
        with self._lock:
            if self._live_voice_state == "starting":
                self._live_voice_state = "active"

    def _mark_live_voice_idle(self) -> None:
        """Handle mark live voice idle for flow controller."""
        with self._lock:
            self._live_voice_state = "idle"

    def _live_voice_busy(self) -> bool:
        """Handle live voice busy for flow controller."""
        with self._lock:
            return self._live_voice_state != "idle"

    def _set_idle(self) -> None:
        # Fire-and-forget. This runs inline on the worker event-reader thread
        # (from _on_intent_cancelled / _on_snip_cancelled). A BLOCKING ui.call
        # here waits for a response that only that same reader thread can read ->
        # 30s self-deadlock that also stalls every other UI call queued behind it
        # (e.g. the next snip). The idle animation is cosmetic, so never wait --
        # mirrors the non-blocking "listening" state fired in begin_caller/snip.
        """Set idle."""
        self._fire(self.ui, "ui.overlay.state", {"state": "idle"})

    def _notice(self, text: str, *, severity: str = "", technical_detail: str = "") -> None:
        """Show a transient warning/status bubble that dismisses itself.

        These are advisory ("didn't catch that", "couldn't start recording", …),
        so they auto-hide after a few seconds instead of lingering — long enough
        to read, short enough not to nag after an accidental tap.

        Every notice is also recorded in the runtime event log: the first line
        stays visible in Runtime Status while the recommendation and any
        technical detail (e.g. a traceback) collapse behind it.
        """
        from core.error_recommendations import format_error

        formatted = format_error(text)
        # The event log gets the technical detail (tracebacks, raw provider
        # errors); the transient bubble only shows the friendly text.
        logged = format_error(text, technical_detail=technical_detail) if technical_detail else formatted
        lines = logged.splitlines()
        self.runtime_log.append(
            "assistant",
            normalize_severity(severity) if severity else "warning",
            lines[0] if lines else logged,
            detail="\n".join(lines[1:]).strip(),
        )
        # log_mirrored tells the UI host not to report this notice back via
        # ui.log.event — the richer supervisor-side record above already exists.
        payload = {"text": formatted, "timeout_ms": 6000, "log_mirrored": True}
        severity_name = str(severity or "").strip().lower()
        if severity_name:
            payload["severity"] = severity_name
        self._safe_call(self.ui, "ui.reply.notice", payload, timeout=30.0)

    def _status_notice(self, text: str) -> None:
        """Show a successful status without attaching error-recovery advice."""
        from core.privacy_redaction import redact_text

        clean = redact_text(str(text or "").strip())
        if not clean:
            return
        self.runtime_log.append("assistant", "info", clean, detail="")
        self._safe_call(
            self.ui,
            "ui.reply.notice",
            {"text": clean, "timeout_ms": 6000, "log_mirrored": True},
            timeout=30.0,
        )

    def _handle_live_file_approval_request(self, payload: Any) -> None:
        """Ask the UI to approve a live model file edit, then answer the brain."""
        if not isinstance(payload, dict):
            return
        approval_id = str(payload.get("approval_id") or "")
        if not approval_id:
            return
        result = self._safe_call(
            self.ui,
            "ui.live_file.approval.request",
            payload,
            timeout=600.0,
        ) or {}
        approved = bool(result.get("approved")) if isinstance(result, dict) else False
        feedback = str(result.get("feedback") or "").strip() if isinstance(result, dict) else ""
        response = dict(result) if isinstance(result, dict) else {}
        response.pop("approved", None)
        response.pop("feedback", None)
        response.pop("surface", None)
        response_params = {
            "approval_id": approval_id,
            "approved": approved,
            "feedback": feedback,
        }
        if response:
            response_params["response"] = response
        self._fire(
            self.brain,
            "brain.live_file.approval.respond",
            response_params,
        )

    def _handle_privacy_review_request(self, payload: Any) -> None:
        """Show the pre-send privacy sheet and resolve the blocked brain request."""
        if not isinstance(payload, dict):
            return
        approval_id = str(payload.get("approval_id") or "")
        if not approval_id:
            return
        result = self._safe_call(
            self.ui,
            "ui.privacy.review.request",
            payload,
            timeout=600.0,
        ) or {}
        decision = str(result.get("decision") or "").strip().lower() if isinstance(result, dict) else ""
        if decision not in {"redacted", "full", "cancel"}:
            decision = "redacted" if isinstance(result, dict) and result.get("approved") else "cancel"
        self._fire(
            self.brain,
            "brain.privacy.review.respond",
            {
                "approval_id": approval_id,
                "approved": decision in {"redacted", "full"},
                "decision": decision,
            },
        )

    def _stt_warming(self) -> bool:
        """True when the STT model isn't loaded/warmed yet, so the next transcribe
        will block on the slow first load. Fast and best-effort: any failure or
        timeout is treated as 'ready' so this never adds latency to the voice path."""
        try:
            res = self.audio.call("audio.stt.is_ready", timeout=3.0) or {}
            return not bool(res.get("ready", True))
        except Exception:
            return False

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        """Handle friendly error for flow controller."""
        return flow_utils.friendly_error(exc)

    def _caller(self, caller_idx: int) -> dict[str, Any]:
        """Handle caller for flow controller."""
        import config
        from core.action_files.store import configured_caller_rows

        rows = configured_caller_rows(config)
        if 0 <= caller_idx < len(rows):
            return config.effective_caller(dict(rows[caller_idx]))
        return {}

    def _voice_caller(self) -> dict[str, Any]:
        """Context/tool config for push-to-talk; falls back to caller 1's row."""
        import config

        voice = getattr(config, "VOICE_CALLER", None)
        if isinstance(voice, dict) and voice:
            return config.effective_caller(dict(voice))
        return self._caller(0)

    def _voice_review_transcript_enabled(self) -> bool:
        """Return whether F9 should review the transcript in the intent picker."""
        import config

        return bool(getattr(config, "VOICE_REVIEW_TRANSCRIPT", False))

    def _snip_caller(self) -> dict[str, Any]:
        """Context/tool config for region snips, while reusing caller 1's prompts."""
        import config

        caller = self._caller(0)
        snip = getattr(config, "SNIP_CALLER", None)
        if isinstance(snip, dict) and snip:
            caller.update(config.effective_caller(dict(snip)))
        else:
            caller.update(
                {
                    "context_ambient": self._config_value("SNIP_CONTEXT_AMBIENT", True),
                    "context_documents": self._config_value("SNIP_CONTEXT_DOCUMENTS", True),
                    "context_tools": self._config_value("SNIP_CONTEXT_TOOLS", False),
                }
            )
        caller.update({"context_screenshot": "off", "paste_back": False})
        return caller

    @staticmethod
    def _current_config_mtime() -> float | None:
        """Handle current config mtime for flow controller."""
        try:
            import config

            env_file = Path(getattr(config, "_ENV_FILE", ""))
            return env_file.stat().st_mtime
        except (OSError, TypeError, ValueError):
            return None

    def _reload_supervisor_config_if_changed(self) -> None:
        """Handle reload supervisor config if changed for flow controller."""
        current_mtime = self._current_config_mtime()
        if current_mtime is None or current_mtime == self._config_mtime:
            return
        import config

        config.reload()
        self._config_mtime = current_mtime
        log.info("supervisor config reloaded after .env change")

    def _config_value(self, name: str, default: Any) -> Any:
        """Handle config value for flow controller."""
        import config

        return getattr(config, name, default)

    @staticmethod
    def _interactive_llm_timeout_seconds(params: dict[str, Any]) -> float:
        """Return a longer timeout when the model has live tools available."""
        return (
            _INTERACTIVE_LLM_TOOL_TIMEOUT_SECONDS
            if bool((params or {}).get("use_tools"))
            else _INTERACTIVE_LLM_TIMEOUT_SECONDS
        )

    def _log_caller_runtime(self, caller_idx: int, caller: dict[str, Any]) -> None:
        """Log caller runtime."""
        try:
            import config

            log.info(
                "caller %d config\n"
                "  label=%r hotkey=%r paste_back=%s\n"
                "  context: app=%s docs=%s browser=%s memory=%s screenshot=%r clipboard=%s\n"
                "  runtime: cwd=%r\n"
                "           config=%r\n"
                "           env=%r",
                caller_idx,
                caller.get("label"),
                caller.get("hotkey"),
                caller.get("paste_back"),
                caller.get("context_ambient"),
                self._context_mode(caller, "documents"),
                self._context_mode(caller, "browser"),
                self._context_mode(caller, "memory"),
                caller.get("context_screenshot"),
                caller.get("context_clipboard"),
                str(Path.cwd()),
                str(getattr(config, "__file__", "") or ""),
                str(getattr(config, "_ENV_FILE", "") or ""),
            )
        except Exception:
            log.exception("caller runtime logging failed")

    def _context_snapshot(
        self,
        caller: dict[str, Any],
        *,
        include_browser: bool = True,
        include_selected_paths: bool = False,
        preview_context_sources: bool = False,
        dedupe_selection: bool = False,
    ) -> dict[str, Any]:
        # The browser-page fetch is a ~2-3s network read (requests.get). Keep it
        # OFF the hotkey -> picker path (include_browser=False) and fetch it lazily
        # at query time instead, where it overlaps the LLM round-trip. The URL and
        # window handle ARE captured now, while the browser is still foreground -
        # by query time the picker has stolen focus and re-detection would fail.
        """Handle context snapshot for flow controller."""
        browser_auto = self._context_mode(caller, "browser") == "auto"
        documents_auto = self._effective_document_mode(caller) == "auto"
        snapshot = self.native.call(
            "native.context.snapshot",
            {
                "include_clipboard": bool(caller.get("context_clipboard", False))
                or preview_context_sources,
                "include_selection": True,
                "include_selected_paths": bool(include_selected_paths),
                # Native Wayland accessibility content must be captured before
                # the picker/overlay changes focus. Other platforms ignore this.
                "include_active_window_text": documents_auto or browser_auto,
                "include_browser_content": include_browser and browser_auto,
                "include_browser_url": browser_auto or preview_context_sources,
                # Paste-back callers capture the focused text element so the rewrite
                # can be written back in place (AX) without refocusing the app.
                "capture_focus": bool(caller.get("paste_back")),
                # Intent-picker captures suppress re-serving the exact same X11
                # PRIMARY acquisition they already auto-filled once (stale after
                # the user deselects); other flows keep plain reads.
                "selection_dedupe_key": "intent" if dedupe_selection else "",
            },
            timeout=30.0,
        ) or {}
        active_app = snapshot.get("active_app") if isinstance(snapshot.get("active_app"), dict) else {}
        debug = snapshot.get("debug") if isinstance(snapshot.get("debug"), dict) else {}
        runtime_debug = debug.get("runtime") if isinstance(debug.get("runtime"), dict) else {}
        window_debug = debug.get("window") if isinstance(debug.get("window"), dict) else {}
        browser_window = debug.get("browser_window") if isinstance(debug.get("browser_window"), dict) else {}
        log.info(
            "context snapshot\n"
            "  active: title=%r process=%r pid=%s hwnd=%s\n"
            "  foreground: raw=(hwnd=%s pid=%s process=%r title=%r)\n"
            "              chosen=(hwnd=%s pid=%s process=%r title=%r corrected=%s)\n"
            "  browser: url=%s hwnd=%s chars=%d\n"
            "  runtime: cwd=%r repo=%r exe=%r\n"
            "           config=%r env=%r",
            active_app.get("name"),
            active_app.get("process_name"),
            active_app.get("pid"),
            active_app.get("window_id") or active_app.get("pid") or 0,
            window_debug.get("raw_hwnd"),
            window_debug.get("raw_pid"),
            window_debug.get("raw_process"),
            window_debug.get("raw_title"),
            window_debug.get("chosen_hwnd"),
            window_debug.get("chosen_pid"),
            window_debug.get("chosen_process"),
            window_debug.get("chosen_title"),
            window_debug.get("corrected"),
            snapshot.get("browser_url") or "",
            snapshot.get("browser_hwnd") or 0,
            len(str(snapshot.get("browser_content") or "")),
            runtime_debug.get("cwd"),
            runtime_debug.get("repo_root"),
            runtime_debug.get("executable"),
            runtime_debug.get("config_file"),
            runtime_debug.get("env_file"),
        )
        if browser_window:
            log.info(
                "context browser window\n"
                "  hwnd=%s pid=%s process=%r\n"
                "  title=%r\n"
                "  url=%r",
                browser_window.get("hwnd"),
                browser_window.get("pid"),
                browser_window.get("process_name"),
                browser_window.get("title"),
                browser_window.get("url"),
            )
        if snapshot.get("browser_error"):
            log.warning("context browser error: %s", snapshot.get("browser_error"))
        return snapshot

    def _fetch_browser_snapshot(self) -> dict[str, Any]:
        """Fetch just the active browser tab's URL + page content - the deferred,
        slow part of the snapshot. Active-app only; no selection/clipboard."""
        return self.native.call(
            "native.context.snapshot",
            {
                "include_clipboard": False,
                "include_selection": False,
                "include_browser_content": True,
            },
            timeout=30.0,
        ) or {}

    def _intent_target_id(self, context: dict[str, Any]) -> int:
        """Return the hotkey-time target id used for paste-back and placement."""
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        if str(context.get("platform") or "") == "darwin":
            return int(active_app.get("pid") or 0)
        return int(active_app.get("window_id") or active_app.get("pid") or 0)

    def _collect_initial_intent_context(
        self,
        pending: PendingInvocation,
        generation: int,
        started_at: float,
        shown_at: float,
    ) -> None:
        """Finalize pre-picker context and start deferred source prefetches."""
        try:
            if not self._is_current(generation):
                return
            t_ctx0 = time.monotonic()
            context = pending.context if isinstance(pending.context, dict) and pending.context else {}
            if not context:
                try:
                    context = self._context_snapshot(
                        pending.caller,
                        include_browser=False,
                        include_selected_paths=True,
                        preview_context_sources=True,
                        dedupe_selection=True,
                    )
                except Exception:
                    # The pre-picker capture is deliberately best-effort. Keep
                    # the post-picker retry under the same contract so a denied
                    # permission or dead native source cannot abort the intent.
                    log.exception("post-picker context snapshot failed")
                    context = {}
            # Do not invoke Calc's Copy command while the intent picker is a
            # visible popup: even a background UIA invocation can activate Calc
            # and dismiss the picker. The selected range is captured only after
            # the user chooses a Calc action and the picker has closed normally.
            t_ctx = time.monotonic()
            if not self._is_current(generation):
                return
            screenshot_b64 = pending.screenshot_b64
            screenshot_tool_b64 = pending.screenshot_tool_b64
            t_shot = time.monotonic()
            if not self._is_current(generation):
                return
            target_id = self._intent_target_id(context)
            pending.context = context
            pending.action_provider_context = self._action_provider_picker_context(context)
            provider_suggestions = pending.action_provider_context.get("suggested_intents")
            log.info(
                "caller %d action provider id=%r app=%r suggestions=%d",
                pending.caller_idx,
                pending.action_provider_context.get("id"),
                pending.action_provider_context.get("app"),
                len(provider_suggestions) if isinstance(provider_suggestions, list) else 0,
            )
            pending.screenshot_b64 = screenshot_b64
            pending.screenshot_tool_b64 = screenshot_tool_b64
            pending.intent_target_pid = target_id
            pending.paste_target_pid = target_id if pending.caller.get("paste_back") else 0
            pending.initial_context_at_unix_ns = time.time_ns()
            pending.context_ready_at_unix_ns = time.time_ns()
            with self._lock:
                if self._pending is pending:
                    self._pending = pending
            if self._is_current(generation):
                self._fire(
                    self.ui,
                    "ui.intent.context_items",
                    {"context_items": self._intent_context_items(pending)},
                )
                self._fire(
                    self.ui,
                    "ui.intent.action_provider",
                    {"action_provider": pending.action_provider_context},
                )
            log.info(
                "caller %d context ready after show=%.2fs context=%.2fs screenshot=%.2fs total=%.2fs",
                pending.caller_idx,
                t_ctx - shown_at,
                t_ctx - t_ctx0,
                t_shot - t_ctx,
                t_shot - started_at,
            )
            pending.context_ready.set()
            if self._is_current(generation):
                self._safe_call(self.ui, "ui.intent.activate", timeout=30.0)
            self._prefetch_intent_context(pending, generation)
        finally:
            pending.context_ready.set()

    def _active_document_window(self, context: dict[str, Any]) -> dict[str, Any]:
        """Build the active-window payload used by active document extraction."""
        document_window = context.get("document_window") if isinstance(context.get("document_window"), dict) else {}
        if document_window:
            return {
                "title": document_window.get("title") or "",
                "process_name": document_window.get("process_name") or "",
                "pid": document_window.get("pid") or 0,
                "window_id": document_window.get("window_id") or 0,
            }
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        debug = context.get("debug") if isinstance(context.get("debug"), dict) else {}
        window_debug = debug.get("window") if isinstance(debug.get("window"), dict) else {}
        return {
            "title": window_debug.get("chosen_title") or window_debug.get("raw_title") or active_app.get("name") or "",
            "process_name": (
                window_debug.get("chosen_process")
                or window_debug.get("raw_process")
                or active_app.get("process_name")
                or active_app.get("name")
                or ""
            ),
            "pid": active_app.get("pid") or window_debug.get("chosen_pid") or window_debug.get("raw_pid") or 0,
            "window_id": active_app.get("window_id") or window_debug.get("chosen_hwnd") or window_debug.get("raw_hwnd") or 0,
        }

    def _fetch_active_document_text(
        self,
        context: dict[str, Any],
        *,
        active_only: bool = False,
    ) -> str:
        """Fetch active document text for preview and query reuse."""
        accessibility_text = str(context.get("active_window_text") or "").strip()
        if accessibility_text and not self._is_browser_active_context(context):
            context["active_document_sources"] = [
                {
                    "label": self._active_document_label(context),
                    "preview": self._context_preview_text(accessibility_text),
                }
            ]
            return accessibility_text
        result = self._safe_call(
            self.brain,
            "brain.context.active_document",
            {
                "active_window": self._active_document_window(context),
                "active_only": bool(active_only),
            },
            timeout=15.0,
        ) or {}
        text = str(result.get("text") or "") if isinstance(result, dict) else ""
        doc_debug = result.get("debug") if isinstance(result, dict) else None
        if isinstance(context, dict):
            context["active_document_sources"] = self._active_document_source_previews(text, doc_debug)
        sources = context.get("active_document_sources") if isinstance(context, dict) else []
        source_labels = [
            str(item.get("label") or "")
            for item in (sources or [])
            if isinstance(item, dict) and str(item.get("label") or "")
        ]
        candidate_lines: list[str] = []
        if isinstance(doc_debug, dict):
            for item in (doc_debug.get("window_candidates") or [])[:12]:
                if not isinstance(item, dict):
                    continue
                candidate_lines.append(
                    "    - {label!r} process={process!r} hwnd={hwnd} chars={chars} accepted={accepted} method={method!r}".format(
                        label=item.get("label") or item.get("title") or "",
                        process=item.get("process_name") or "",
                        hwnd=item.get("hwnd") or 0,
                        chars=item.get("chars") or 0,
                        accepted=bool(item.get("accepted")),
                        method=item.get("method") or "",
                    )
                )
        candidate_text = "\n".join(candidate_lines) if candidate_lines else "    - none"
        log.info(
            "active document context\n"
            "  chars=%d sources=%r error=%r\n"
            "  paths=%r path_chars=%s\n"
            "  window_labels=%r window_chars=%s\n"
            "  window_candidates:\n%s",
            len(text),
            source_labels,
            result.get("error") if isinstance(result, dict) else None,
            doc_debug.get("paths") if isinstance(doc_debug, dict) else [],
            doc_debug.get("path_chars") if isinstance(doc_debug, dict) else 0,
            doc_debug.get("window_labels") if isinstance(doc_debug, dict) else [],
            doc_debug.get("window_chars") if isinstance(doc_debug, dict) else 0,
            candidate_text,
        )
        return text

    @staticmethod
    def _context_source_app_name(process_name: str, title: str = "") -> str:
        """Return a concise product name for one captured document source."""
        process = " ".join(str(process_name or "").split()).strip()
        lowered = process.casefold()
        title_lower = str(title or "").casefold()
        candidates = f"{lowered} {title_lower}"
        mappings = (
            (("code - insiders", "visual studio code - insiders"), "VS Code Insiders"),
            (("cursor",), "Cursor"),
            (("windsurf",), "Windsurf"),
            (("visual studio code", "code.exe", " code "), "VS Code"),
            (("google chrome", "chrome.exe"), "Google Chrome"),
            (("microsoft edge", "msedge.exe"), "Microsoft Edge"),
            (("brave",), "Brave"),
            (("firefox",), "Firefox"),
            (("libreoffice calc", "soffice", "scalc"), "LibreOffice Calc"),
            (("excel",), "Microsoft Excel"),
            (("winword", "microsoft word"), "Microsoft Word"),
            (("powerpnt", "powerpoint"), "Microsoft PowerPoint"),
            (("notepad",), "Notepad"),
        )
        padded = f" {candidates} "
        for needles, display_name in mappings:
            if any(needle in padded for needle in needles):
                return display_name
        if not process:
            return ""
        fallback = re.sub(r"\.(?:exe|bin|app)$", "", process, flags=re.IGNORECASE)
        return fallback.replace("_", " ").replace("-", " ").strip()

    def _active_document_source_previews(self, text: str, debug: Any) -> list[dict[str, str]]:
        """Split active-document text into labelled preview rows for the overlay."""
        raw = str(text or "").strip()
        if not raw:
            return []
        labels: list[str] = []
        if isinstance(debug, dict):
            labels = [
                str(label or "").strip()
                for label in (debug.get("window_labels") or [])
                if str(label or "").strip()
            ]
            if not labels:
                labels = [
                    Path(str(path or "")).name
                    for path in (debug.get("paths") or [])
                    if str(path or "").strip()
                ]
        sources: list[dict[str, str]] = []
        window_candidates = (
            [item for item in (debug.get("window_candidates") or []) if isinstance(item, dict)]
            if isinstance(debug, dict)
            else []
        )
        active_window = debug.get("active_window") if isinstance(debug, dict) else {}
        active_window = active_window if isinstance(active_window, dict) else {}
        seen_sources: set[tuple[str, str, str]] = set()
        matches = list(re.finditer(r"(?m)^\[([^\]\n]{1,160})\]\n", raw))
        for idx, match in enumerate(matches):
            label = " ".join(match.group(1).split()).strip()
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            preview = self._context_preview_text(raw[start:end])
            normalized_label = label.casefold()
            candidate = next(
                (
                    item for item in window_candidates
                    if " ".join(str(item.get("label") or item.get("title") or "").split()).casefold()
                    == normalized_label
                ),
                {},
            )
            process_name = str(candidate.get("process_name") or "")
            source_title = str(candidate.get("title") or "")
            if not process_name and idx == 0:
                process_name = str(active_window.get("process_name") or "")
                source_title = str(active_window.get("title") or "")
            app_name = self._context_source_app_name(process_name, source_title)
            if label and preview:
                source_key = (app_name.casefold(), normalized_label, preview.casefold())
                if source_key in seen_sources:
                    continue
                seen_sources.add(source_key)
                source = {"label": label, "preview": preview}
                if app_name:
                    source["app"] = app_name
                sources.append(source)
        if sources:
            return sources[:5]
        label = labels[0] if labels else self._active_document_label({})
        app_name = self._context_source_app_name(
            str(active_window.get("process_name") or ""),
            str(active_window.get("title") or ""),
        )
        source = {"label": label, "preview": self._context_preview_text(raw)}
        if app_name:
            source["app"] = app_name
        return [source]

    def _active_document_label(self, context: dict[str, Any]) -> str:
        """Return a human-readable source label for active document context."""
        active_window = self._active_document_window(context)
        process = " ".join(str(active_window.get("process_name") or "").split()).strip()
        title = " ".join(str(active_window.get("title") or "").split()).strip()
        if process and title and title != process:
            return f"{process} - {title}"
        return title or process or "Active document"

    def _active_document_context_label(self, context: dict[str, Any]) -> str:
        """Return the prompt boundary label for active-document context."""
        sources = [
            item
            for item in (context.get("active_document_sources") or [])
            if isinstance(item, dict) and str(item.get("preview") or "").strip()
        ]
        if len(sources) > 1:
            return "Open app documents"
        return self._active_document_label(context)

    @staticmethod
    def _browser_page_id(page: dict[str, Any]) -> str:
        """Return a stable picker/removal id for one captured browser window."""
        explicit = str(page.get("id") or "").strip()
        if explicit:
            return explicit
        hwnd = int(page.get("hwnd") or 0)
        if hwnd:
            return f"browser:{hwnd}"
        app = str(page.get("app") or page.get("process_name") or "").strip().casefold()
        url = str(page.get("url") or "").strip()
        return f"browser:{app or url}"

    @classmethod
    def _browser_pages_from_context(cls, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize plural browser pages, synthesizing the legacy singular page."""
        raw_pages = context.get("browser_pages")
        pages = [dict(page) for page in raw_pages or [] if isinstance(page, dict)]
        if not pages and any(
            context.get(key)
            for key in ("browser_url", "browser_hwnd", "browser_app", "browser_content")
        ):
            pages = [
                {
                    "title": "",
                    "process_name": str(context.get("browser_app") or ""),
                    "app": str(context.get("browser_app") or ""),
                    "url": str(context.get("browser_url") or ""),
                    "hwnd": int(context.get("browser_hwnd") or 0),
                    "content": str(context.get("browser_content") or ""),
                }
            ]
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in pages:
            page["title"] = str(page.get("title") or "").strip()
            page["process_name"] = str(page.get("process_name") or page.get("app") or "").strip()
            page["app"] = str(page.get("app") or page.get("process_name") or "").strip()
            page["url"] = str(page.get("url") or "").strip()
            page["hwnd"] = int(page.get("hwnd") or 0)
            page["content"] = str(page.get("content") or "")
            page["id"] = cls._browser_page_id(page)
            if not page["id"] or page["id"] in seen:
                continue
            seen.add(page["id"])
            normalized.append(page)
        return normalized

    @classmethod
    def _store_browser_pages(
        cls,
        context: dict[str, Any],
        pages: list[dict[str, Any]],
    ) -> None:
        """Store plural pages and keep legacy singular fields on the first page."""
        normalized = cls._browser_pages_from_context({"browser_pages": pages})
        context["browser_pages"] = normalized
        if not normalized:
            context["browser_url"] = ""
            context["browser_hwnd"] = 0
            context["browser_app"] = ""
            context["browser_content"] = ""
            return
        primary = normalized[0]
        context["browser_url"] = primary.get("url") or ""
        context["browser_hwnd"] = int(primary.get("hwnd") or 0)
        context["browser_app"] = primary.get("app") or primary.get("process_name") or ""
        context["browser_content"] = primary.get("content") or ""

    @staticmethod
    def _browser_app_label(page: dict[str, Any]) -> str:
        """Return a short user-facing browser name for picker and chat sources."""
        raw = str(page.get("app") or page.get("process_name") or "Browser").strip()
        key = raw.casefold().removesuffix(".exe")
        return {
            "chrome": "Chrome",
            "google chrome": "Chrome",
            "msedge": "Edge",
            "microsoft edge": "Edge",
            "firefox": "Firefox",
            "brave": "Brave",
            "brave browser": "Brave",
            "vivaldi": "Vivaldi",
            "opera": "Opera",
            "safari": "Safari",
        }.get(key, raw or "Browser")

    @classmethod
    def _browser_page_label(cls, page: dict[str, Any]) -> str:
        """Return the page/window label beneath the browser application name."""
        title = " ".join(str(page.get("title") or "").split()).strip()
        if title:
            return title
        url = str(page.get("url") or "").strip()
        return url or "Active page"

    @classmethod
    def _browser_page_prompt_block(
        cls,
        page: dict[str, Any],
        *,
        priority: str = "",
    ) -> str:
        """Render one browser window as a bounded, independently labelled block."""
        source_label = f"{cls._browser_app_label(page)}: {cls._browser_page_label(page)}"
        bits = [f"--- BEGIN BROWSER PAGE: {source_label} ---"]
        if priority:
            bits.append(f"Priority: {priority}")
        url = str(page.get("url") or "").strip()
        content = str(page.get("content") or "").strip()
        if url:
            bits.append(f"URL: {url}")
        if content:
            bits.append(content)
        bits.append(f"--- END BROWSER PAGE: {source_label} ---")
        return "\n".join(bits)

    def _fetch_browser_content_for_context(self, context: dict[str, Any]) -> dict[str, Any]:
        """Fetch every visible browser page captured at hotkey time."""
        pages = self._browser_pages_from_context(context)
        accessibility_text = str(context.get("active_window_text") or "").strip()
        if not pages:
            fetched = self._fetch_browser_snapshot()
            pages = self._browser_pages_from_context(fetched)
            log.info(
                "browser context fallback snapshot pages=%d chars=%d error=%r",
                len(pages),
                sum(len(str(page.get("content") or "")) for page in pages),
                fetched.get("browser_error"),
            )
        else:
            for page in pages:
                if str(page.get("content") or "").strip():
                    continue
                result = self._safe_call(
                    self.native,
                    "native.context.browser_content",
                    {
                        "url": page.get("url") or "",
                        "hwnd": int(page.get("hwnd") or 0),
                        "app": page.get("app") or page.get("process_name") or "",
                    },
                    timeout=30.0,
                ) or {}
                page["url"] = str(result.get("url") or page.get("url") or "").strip()
                page["content"] = str(result.get("content") or "").strip()
                log.info(
                    "browser context source id=%r url=%r hwnd=%s chars=%d error=%r",
                    page.get("id"),
                    page.get("url"),
                    page.get("hwnd") or 0,
                    len(page["content"]),
                    result.get("error") if isinstance(result, dict) else None,
                )
        if (
            pages
            and not any(str(page.get("content") or "").strip() for page in pages)
            and accessibility_text
            and self._is_browser_active_context(context)
        ):
            pages[0]["content"] = accessibility_text
        self._store_browser_pages(context, pages)
        primary = pages[0] if pages else {}
        return {
            "browser_url": str(primary.get("url") or ""),
            "browser_content": str(primary.get("content") or ""),
        }

    def _prefetch_intent_context(self, pending: PendingInvocation, generation: int) -> None:
        """Fetch slow context while the intent overlay is open, then refresh chips."""
        if not self._is_current(generation):
            return
        changed = False
        context = pending.context if isinstance(pending.context, dict) else {}
        if not context.get("active_document_text"):
            text = self._fetch_active_document_text(context)
            if not self._is_current(generation):
                return
            if text:
                context["active_document_text"] = text
                changed = True
        browser_requested = self._context_mode(pending.caller, "browser") == "auto"
        browser_pages = self._browser_pages_from_context(context)
        if browser_requested and (
            not browser_pages
            or not all(str(page.get("content") or "").strip() for page in browser_pages)
        ):
            browser = self._fetch_browser_content_for_context(context)
            if not self._is_current(generation):
                return
            if browser.get("browser_url") and not context.get("browser_url"):
                context["browser_url"] = browser["browser_url"]
                changed = True
            if browser.get("browser_content"):
                context["browser_content"] = browser["browser_content"]
                changed = True
            if self._browser_pages_from_context(context):
                changed = True
        if not changed or not self._is_current(generation):
            return
        with self._lock:
            if self._pending is pending:
                self._pending.context = context
        self._fire(
            self.ui,
            "ui.intent.context_items",
            {"context_items": self._intent_context_items(pending)},
        )

    def _brain_query_params(self, prompt: str, pending: PendingInvocation) -> dict[str, Any]:
        """Handle brain query params for flow controller."""
        caller = pending.caller
        context = pending.context or {}
        ambient_parts: list[str] = []
        buffered_items, drop_items = self._consume_context_extras()
        if caller.get("_context_selection_enabled", True):
            drop_items.extend(self._path_context_items(context.get("selected_paths")))
        screenshot_b64 = pending.screenshot_b64
        screenshot_tool_b64: str | None = pending.screenshot_tool_b64
        if caller.get("_context_screenshot_enabled") is False:
            screenshot_b64 = None
            screenshot_tool_b64 = None
        elif (
            not screenshot_b64
            and str(caller.get("context_screenshot") or "").strip().lower() == "auto"
            and not caller.get("_context_screenshot_requires_snip")
        ):
            screenshot_b64 = self._capture_fullscreen_b64()
        allow_screenshot_tool = self._screenshot_tool_allowed(caller)
        if allow_screenshot_tool and screenshot_tool_b64 is None:
            screenshot_tool_b64 = self._capture_model_tool_b64()
        allowed_tools = self._allowed_model_tools(caller)
        pinned_tools = self._pinned_model_tools(caller)
        frontload_tools = self._frontloaded_model_tools(caller)
        memory_mode = self._context_mode(caller, "memory")
        documents_mode = self._effective_document_mode(caller)
        selected_text = (
            str(context.get("selected_text") or "")
            if caller.get("_context_selection_enabled", True)
            else ""
        )
        include_active_document = documents_mode == "auto"
        active_document_text = str(context.get("active_document_text") or "") if include_active_document else ""
        if include_active_document:
            active_document_text = active_document_text or self._fetch_active_document_text(context)
            removed_doc_labels = {
                sid for iid, sid in pending.removed_context_sources if iid == "ambient" and sid
            }
            if removed_doc_labels and active_document_text:
                # Rows removed via the picker's X buttons must also leave the
                # prompt; the document text is (re)fetched at submit time.
                active_document_text = self._strip_removed_document_sources(
                    active_document_text, removed_doc_labels
                )
        if caller.get("context_ambient", True):
            active_app = context.get("active_app")
            if isinstance(active_app, dict) and active_app.get("name"):
                ambient_parts.append(f"[App]\nActive app: {active_app.get('name')}")
        if caller.get("context_clipboard") and context.get("clipboard_text"):
            ambient_parts.append(f"[Clipboard]\n{context.get('clipboard_text')}")
        if self._context_mode(caller, "browser") == "auto":
            browser_pages = self._browser_pages_from_context(context)
            if not browser_pages or any(
                not str(page.get("content") or "").strip() for page in browser_pages
            ):
                # URL + window handle (Windows) or browser app name (macOS) were
                # captured at hotkey time; fetch every captured browser window
                # without requiring it to be foreground.
                self._fetch_browser_content_for_context(context)
                browser_pages = self._browser_pages_from_context(context)
            removed_browser_ids = {
                sid for iid, sid in pending.removed_context_sources if iid == "browser" and sid
            }
            browser_pages = [
                page
                for page in browser_pages
                if str(page.get("id") or "") not in removed_browser_ids
            ]
            self._store_browser_pages(context, browser_pages)
            browser_blocks = [
                self._browser_page_prompt_block(
                    page,
                    priority=(
                        "primary"
                        if (
                            not selected_text.strip()
                            and index == 0
                            and self._is_browser_active_context(context)
                        )
                        else "supporting"
                    ),
                )
                for index, page in enumerate(browser_pages)
                if page.get("url") or page.get("content") or page.get("app")
            ]
            if browser_blocks:
                ambient_parts.append("[Browser/Web]\n" + "\n\n".join(browser_blocks))
        if buffered_items:
            ambient_parts.append("[Buffered context]\n" + "\n\n".join(buffered_items))
        if drop_items:
            drop_text_parts = []
            for item in drop_items:
                item_type = str(item.get("type") or "text")
                content = item.get("content")
                if item_type == "image" and not screenshot_b64:
                    screenshot_b64 = self._image_content_b64(content)
                    continue
                name = " ".join(str(item.get("name") or "Context").split()).strip() or "Context"
                drop_text_parts.append(
                    f"--- BEGIN DROPPED CONTEXT: {name} ({item_type}) ---\n"
                    f"{self._content_to_text(content)}\n"
                    f"--- END DROPPED CONTEXT: {name} ({item_type}) ---"
                )
            if drop_text_parts:
                ambient_parts.append("[Dropped context]\n" + "\n\n".join(drop_text_parts))
        ambient_text = "\n\n".join(ambient_parts)
        context_priority = (
            "Selection"
            if selected_text.strip()
            else self._context_priority_source(
                context,
                ambient_text,
                active_document_text,
            )
        )
        summary = self._context_summary_badges(
            selected=selected_text,
            screenshot_b64=screenshot_b64,
            buffered_items=buffered_items,
            drop_items=drop_items,
            clipboard_text=str(context.get("clipboard_text") or "") if caller.get("context_clipboard") else "",
            ambient_text=ambient_text,
            active_document_text=active_document_text,
        )
        return {
            "intent_prompt": prompt,
            "selected": selected_text,
            "screenshot_b64": screenshot_b64,
            "ambient_text": ambient_text,
            "memory_enabled": memory_mode == "on",
            "use_tools": bool(allowed_tools),
            "allowed_tools": allowed_tools,
            "pinned_tools": pinned_tools,
            "frontload_tools": frontload_tools,
            "file_access_mode": tool_modes.local_file_access_mode(caller),
            "allow_screenshot_tool": allow_screenshot_tool,
            "screenshot_tool_b64": screenshot_tool_b64,
            "include_active_document": include_active_document and not active_document_text,
            "active_document_text": active_document_text,
            "active_document_label": self._active_document_context_label(context) if include_active_document else "",
            "context_priority": context_priority,
            "_ui_context_summary": summary,
            "context_policy": _normalized_context_policy(caller),
        }

    @staticmethod
    def _discard_unused_pending_context(
        pending: PendingInvocation,
        params: dict[str, Any],
    ) -> None:
        """Drop gathered context that was left out of the final request payload.

        This is best-effort transient cleanup, not secure memory erasure. The
        provider-bound payload in ``params`` keeps the selected context; this
        removes unselected preview/capture values from the pending request state
        before the provider call starts.
        """
        context = pending.context if isinstance(pending.context, dict) else {}
        ambient = str(params.get("ambient_text") or "")

        selected_used = bool(params.get("selected"))
        clipboard_used = "[Clipboard]" in ambient
        browser_used = "[Browser/Web]" in ambient
        active_document_used = bool(params.get("active_document_text")) or (
            "--- BEGIN ACTIVE DOCUMENT:" in ambient or "[Active document]" in ambient
        )
        app_used = "[App]" in ambient or active_document_used

        if not selected_used:
            context.pop("selected_text", None)
        if not clipboard_used:
            context.pop("clipboard_text", None)
        if not browser_used:
            for key in (
                "browser_url",
                "browser_content",
                "browser_app",
                "browser_hwnd",
                "browser_window",
                "browser_error",
                "browser_pages",
            ):
                context.pop(key, None)
        if not active_document_used:
            for key in (
                "active_document_text",
                "active_document_sources",
                "active_window_text",
                "document_window",
            ):
                context.pop(key, None)
        if not app_used:
            context.pop("active_app", None)

        # Debug snapshots can contain window titles/process metadata that are
        # useful for local diagnostics but are never needed after payload build.
        context.pop("debug", None)

        if not params.get("screenshot_b64"):
            pending.screenshot_b64 = None
        if not params.get("screenshot_tool_b64"):
            pending.screenshot_tool_b64 = None

    @staticmethod
    def _is_browser_active_context(context: dict[str, Any]) -> bool:
        """Return whether browser active context is true."""
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        candidates = [
            active_app.get("process_name"),
            active_app.get("name"),
            context.get("browser_app"),
        ]
        names = {str(name or "").strip().lower() for name in candidates if str(name or "").strip()}
        if names & _BROWSER_APP_NAMES:
            return True
        try:
            from core.context_fetcher import _BROWSER_PROCS

            if names & set(_BROWSER_PROCS):
                return True
        except Exception:
            pass
        return False

    @classmethod
    def _context_priority_source(
        cls,
        context: dict[str, Any],
        ambient_text: str,
        active_document_text: str,
    ) -> str:
        """Handle context priority source for flow controller."""
        if not active_document_text or "[Browser/Web]" not in (ambient_text or ""):
            return ""
        return "Browser/Web" if cls._is_browser_active_context(context) else "Active document"

    @staticmethod
    def _context_mode(caller: dict[str, Any], name: str) -> str:
        """Handle context mode for flow controller."""
        return tool_modes.context_mode(caller, name)

    def _effective_document_mode(self, caller: dict[str, Any]) -> str:
        """Treat enabled App context as active document context."""
        mode = self._context_mode(caller, "documents")
        if mode == "off" and bool(caller.get("context_ambient", False)):
            return "auto"
        return mode

    def _allowed_model_tools(self, caller: dict[str, Any]) -> list[str]:
        """Handle allowed model tools for flow controller."""
        allowed = tool_modes.allowed_model_tools(caller)
        overrides = tool_modes.tool_overrides(caller)
        for item in self._addon_model_tool_payloads():
            name = item["name"]
            server_id = mcp_server_id_from_tool(name, item.get("description", ""))
            group_mode = (
                overrides.get(mcp_server_override_key(server_id))
                if server_id
                else None
            )
            mode = overrides.get(name, group_mode or "on")
            if mode == "off":
                continue
            if name not in allowed:
                allowed.append(name)
        return allowed

    def _intent_tool_snapshot(
        self,
        caller: dict[str, Any],
        choices: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Return the capability inventory for the exact route used after submit."""
        import config

        execution_mode = str(
            getattr(config, "CHAT_EXECUTION_MODE", "openwand") or "openwand"
        ).strip().lower()
        if execution_mode in {"codex", "claude"}:
            prefix = "OPENWAND_CODEX" if execution_mode == "codex" else "OPENWAND_CLAUDE"
            snapshot = tool_inventory.build_harness_inventory(
                execution_mode=execution_mode,
                model=str(getattr(config, f"{prefix}_MODEL", "") or ""),
                approval_mode=str(
                    getattr(config, f"{prefix}_APPROVAL_MODE", "ask") or "ask"
                ),
            )
            return snapshot

        addon_tools = self._addon_model_tool_payloads()
        descriptions = {
            str(item.get("name") or ""): str(item.get("description") or "")
            for item in addon_tools
        }
        # Avoid a second brain round-trip: _allowed_model_tools only adds the
        # same enabled addon payloads gathered immediately above.
        allowed = tool_modes.allowed_model_tools(caller)
        overrides = tool_modes.tool_overrides(caller)
        for item in addon_tools:
            name = str(item.get("name") or "")
            server_id = mcp_server_id_from_tool(name, item.get("description", ""))
            group_mode = overrides.get(mcp_server_override_key(server_id)) if server_id else None
            if name and overrides.get(name, group_mode or "on") != "off" and name not in allowed:
                allowed.append(name)
        snapshot = tool_inventory.build_openwand_inventory(
            provider=str(getattr(config, "CHAT_LLM_PROVIDER", "") or ""),
            model=str(getattr(config, "CHAT_LLM_MODEL", "") or ""),
            allowed_tools=allowed,
            file_access_mode=tool_modes.local_file_access_mode(caller),
            tool_descriptions=descriptions,
            file_roots=[str(root) for root in (getattr(config, "TOOL_FILE_ROOTS", []) or [])],
        )
        disabled_tools = {
            str(name)
            for item in (choices or [])
            if (
                isinstance(item, dict)
                and bool(item.get("toggleable"))
                and not bool(item.get("enabled", True))
            )
            for name in (item.get("tools") or [])
            if str(name)
        }
        if disabled_tools:
            for item in snapshot.get("items") or []:
                if set(item.get("tools") or []) & disabled_tools:
                    item["enabled"] = False
            snapshot["count"] = sum(
                int(item.get("count") or 1)
                for item in snapshot.get("items") or []
                if item.get("enabled") and item.get("status") != "unavailable"
            )
        return snapshot

    def _pinned_model_tools(self, caller: dict[str, Any]) -> list[str]:
        """Tools explicitly pinned by caller policy.

        Context dropdowns in "model" mode mean "offer the tool schema and let
        the model decide whether to call it." The allow-list uses dotted source
        grants like ``get_context.browser``, but the actual schema is named
        ``get_context``, so pin the schema name here.
        """
        return tool_modes.pinned_model_tools(caller)

    def _addon_model_tools(self) -> list[str]:
        """Return enabled addon tool names from the brain-owned addon registry."""
        return [item["name"] for item in self._addon_model_tool_payloads()]

    def _addon_model_tool_payloads(self) -> list[dict[str, str]]:
        """Return enabled addon tool payloads from the brain-owned addon registry."""
        try:
            result = self._safe_call(self.brain, "brain.addons.tools", timeout=3.0) or {}
        except Exception:
            return []
        tools = result.get("tools") if isinstance(result, dict) else []
        payloads: list[dict[str, str]] = []
        for item in tools or []:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                description = str(item.get("description") or name)
            else:
                name = str(item or "").strip()
                description = name
            if name and name not in {tool["name"] for tool in payloads}:
                payloads.append({"name": name, "description": description})
        return payloads

    def _watch_model_background_task(
        self,
        payload: dict[str, Any],
        *,
        conversation_id: str,
    ) -> None:
        """Deliver a detached model-delegated task back to its originating chat."""
        import json

        from core.system.paths import AGENT_RUNS_DIR

        state_text = str(payload.get("state_path") or "").strip()
        job_id = str(payload.get("job_id") or "").strip()
        if not state_text or not job_id or not conversation_id:
            return
        try:
            state_path = Path(state_text).expanduser().resolve()
            jobs_root = (Path(AGENT_RUNS_DIR) / "background_jobs").resolve()
        except Exception:
            return
        if state_path.parent != jobs_root or state_path.suffix.lower() != ".json":
            log.warning("ignored background task state outside the jobs root: %s", state_path)
            return
        key = str(state_path)
        with self._lock:
            if key in self._background_task_watchers:
                return
            self._background_task_watchers.add(key)

        def watch() -> None:
            deadline = time.monotonic() + (65 * 60)
            state: dict[str, Any] = {}
            try:
                while time.monotonic() < deadline:
                    try:
                        loaded = json.loads(state_path.read_text(encoding="utf-8"))
                        state = loaded if isinstance(loaded, dict) else {}
                    except (OSError, ValueError, json.JSONDecodeError):
                        state = {}
                    if str(state.get("status") or "") in {"completed", "failed", "cancelled"}:
                        break
                    time.sleep(0.5)
                else:
                    state = {
                        **state,
                        "status": "failed",
                        "error": "The background task did not finish before its monitoring deadline.",
                    }

                status = str(state.get("status") or "failed")
                text = str(state.get("error") or "").strip()
                final_path = Path(str(state.get("final_path") or ""))
                if status == "completed" and final_path.is_file():
                    text = final_path.read_text(encoding="utf-8", errors="replace").strip()
                if not text:
                    text = str(state.get("last_log") or "The background task finished without a report.")
                result = self._safe_call(
                    self.ui,
                    "ui.chat.background_result",
                    {
                        "conversation_id": conversation_id,
                        "job_id": job_id,
                        "status": status,
                        "title": str(state.get("title") or payload.get("title") or "Background task"),
                        "text": text[:50000],
                        "run_dir": str(state.get("run_dir") or ""),
                    },
                    timeout=30.0,
                ) or {}
                if isinstance(result, dict) and (result.get("appended") or result.get("duplicate")):
                    state["delivered_at"] = time.time()
                    temporary = state_path.with_name(f".{state_path.name}.delivered.tmp")
                    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
                    temporary.replace(state_path)
            except Exception:  # noqa: BLE001 - watcher failure must not affect foreground chat
                log.exception("background task watcher failed for %s", job_id)
            finally:
                with self._lock:
                    self._background_task_watchers.discard(key)

        threading.Thread(
            target=watch,
            name=f"openwand-background-task-{job_id}",
            daemon=True,
        ).start()

    def _chat_text_annotations(self, text: str, *, role: str) -> list[dict[str, Any]]:
        """Return display-only chat annotations from the brain-owned addon manager."""
        text = str(text or "")
        if not text.strip():
            return []
        payload = {
            "text": text,
            "surface": "chat",
            "role": str(role or ""),
        }
        try:
            result = self._safe_call(
                self.brain,
                "brain.addons.text_annotations",
                {"payload": payload},
                timeout=3.0,
            ) or {}
        except Exception:
            return []
        annotations = result.get("annotations") if isinstance(result, dict) else []
        if not isinstance(annotations, list):
            return []
        return [item for item in annotations if isinstance(item, dict)]

    @staticmethod
    def _latest_message_text(messages: list, *, role: str) -> str:
        """Return the newest message content matching role."""
        target = str(role or "").lower()
        for item in reversed(messages or []):
            if not isinstance(item, dict):
                continue
            if str(item.get("role") or "").lower() == target:
                return str(item.get("content") or "")
        return ""

    def _chat_tool_policy(self, caller: dict[str, Any]) -> tuple[list[str], list[str], str]:
        """Return chat tool grants from the visible chat/caller policy."""
        allowed = self._allowed_model_tools(caller)
        pinned = self._pinned_model_tools(caller)
        file_access_mode = tool_modes.local_file_access_mode(caller)
        return allowed, pinned, file_access_mode

    def _messages_with_chat_context(
        self,
        messages: list,
        caller: dict[str, Any],
        parts: list[tuple[str, str, str]] | None = None,
    ) -> list:
        """Attach selected chat context to the volatile latest user turn."""
        if parts is None:
            parts = self._chat_context_parts(caller)
        context_text = "\n\n".join(block for _label, block, _src in parts if block.strip())
        if not context_text:
            return messages
        out = [dict(m) for m in messages]
        from core.llm_clients.messages import build_contextual_user_text

        for msg in reversed(out):
            if str(msg.get("role") or "").lower() == "user":
                msg["content"] = build_contextual_user_text(
                    str(msg.get("content") or ""),
                    ambient_context=f"[Current Chat Context]\n{context_text}",
                )
                return out
        return out

    def _chat_context_text(self, caller: dict[str, Any]) -> str:
        """Joined prompt text for the frontloaded chat context (model-facing)."""
        parts = self._chat_context_parts(caller)
        return "\n\n".join(block for _label, block, _src in parts if block.strip())

    def _chat_context_parts(self, caller: dict[str, Any]) -> list[tuple[str, str, str]]:
        """Fetch frontloaded chat context as ``(label, prompt_block, preview_source)``.

        ``prompt_block`` is injected verbatim into the model prompt. ``label`` and
        ``preview_source`` feed the display-only per-source snippets shown under the
        user's turn in the chat transcript; those snippets are never sent to the
        model.
        """
        wants_documents = self._effective_document_mode(caller) == "auto"
        wants_browser = self._context_mode(caller, "browser") == "auto"
        wants_clipboard = bool(caller.get("context_clipboard"))
        wants_ambient = bool(caller.get("context_ambient"))
        wants_selection = bool(caller.get("_context_selection_enabled", False))
        if not any((wants_documents, wants_browser, wants_clipboard, wants_ambient, wants_selection)):
            return []

        try:
            context = self._context_snapshot(caller, include_browser=False, preview_context_sources=wants_browser)
        except Exception:
            log.exception("chat context snapshot failed")
            context = {}

        parts: list[tuple[str, str, str]] = []
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        if wants_ambient and active_app.get("name"):
            body = f"Active app: {active_app.get('name')}"
            parts.append(("App", f"[App]\n{body}", body))

        if wants_selection:
            selected = str(context.get("selected_text") or "").strip()
            if selected:
                parts.append(("Selection", f"[Selection]\n{selected}", selected))

        if wants_clipboard:
            clipboard = str(context.get("clipboard_text") or "").strip()
            if clipboard:
                parts.append(("Clipboard", f"[Clipboard]\n{clipboard}", clipboard))

        if wants_documents:
            active_document_text = str(context.get("active_document_text") or "").strip()
            if not active_document_text:
                active_document_text = self._fetch_active_document_text(context)
            if active_document_text:
                label = self._active_document_context_label(context)
                block = (
                    f"--- BEGIN ACTIVE DOCUMENT: {label} ---\n"
                    f"{active_document_text}\n"
                    f"--- END ACTIVE DOCUMENT: {label} ---"
                )
                parts.append((f"Document: {label}", block, active_document_text))

        if wants_browser:
            self._fetch_browser_content_for_context(context)
            for page in self._browser_pages_from_context(context):
                if not (page.get("url") or page.get("content")):
                    continue
                source_label = f"{self._browser_app_label(page)}: {self._browser_page_label(page)}"
                block = self._browser_page_prompt_block(page)
                preview_source = str(page.get("content") or page.get("url") or "")
                parts.append((f"Browser/Web: {source_label}", f"[Browser/Web]\n{block}", preview_source))

        return parts

    def _screenshot_tool_allowed(self, caller: dict[str, Any]) -> bool:
        """Whether capture_screen is exposed: the Screenshot dropdown's "model"
        mode, overridable per-tool from the Allowed Tools list (auto-capture
        stays dropdown-governed)."""
        return tool_modes.screenshot_tool_allowed(caller)

    @staticmethod
    def _tool_overrides(caller: dict[str, Any]) -> dict[str, str]:
        """Handle tool overrides for flow controller."""
        return tool_modes.tool_overrides(caller)

    def _frontloaded_model_tools(self, caller: dict[str, Any]) -> list[str]:
        """Handle frontloaded model tools for flow controller."""
        return tool_modes.frontloaded_model_tools(caller)

    @staticmethod
    def _estimate_context_tokens(text: str) -> int:
        """Fast token estimate for context preview chips."""
        return flow_estimates.estimate_context_tokens(text)

    @classmethod
    def _token_label(cls, text: str) -> str:
        """Return a compact token estimate label."""
        return flow_estimates.token_label(text)

    @staticmethod
    def _deferred_token_label() -> str:
        """Return the token label for context fetched after the picker."""
        return flow_estimates.deferred_token_label()

    @staticmethod
    def _image_size_from_b64(data: str | None) -> tuple[int, int] | None:
        """Best-effort PNG/JPEG dimension read for screenshot token estimates."""
        return flow_estimates.image_size_from_b64(data)

    @classmethod
    def _image_size_token_label(cls, size: tuple[int, int] | None) -> str:
        """Return a rough token estimate for an image of known dimensions."""
        return flow_estimates.image_size_token_label(size)

    @classmethod
    def _image_token_label(cls, data: str | None) -> str:
        """Return a rough token estimate for image input."""
        return flow_estimates.image_token_label(data)

    @classmethod
    def _image_token_count(cls, data: str | None) -> int | None:
        """Return a rough token count for image input."""
        return flow_estimates.image_token_count(data)

    @classmethod
    def _screen_token_label(cls, context: dict[str, Any]) -> str:
        """Return screenshot token estimate from screen metadata."""
        return flow_estimates.screen_token_label(context)

    def _intent_context_keys(self) -> str:
        """Return eight unique overlay-local context toggle keys."""
        raw = str(self._config_value("INTENT_CONTEXT_TOGGLE_KEYS", "12345678") or "12345678")
        keys: list[str] = []
        for ch in raw + "12345678":
            if ch.isspace() or ch in keys:
                continue
            keys.append(ch)
            if len(keys) >= 8:
                break
        return "".join(keys)

    @staticmethod
    def _mode_to_context_state(mode: str) -> str:
        """Map stored context mode to overlay state."""
        mode = (mode or "").strip().lower()
        if mode in {"auto", "on"}:
            return "on"
        if mode == "model":
            return "auto"
        return "off"

    @staticmethod
    def _file_access_to_context_state(mode: str) -> str:
        """Map local file access mode to overlay state."""
        return "off" if (mode or "").strip().lower() == "off" else "auto"

    @staticmethod
    def _context_warning(
        tokens: int,
        *,
        available: bool = True,
        deferred: bool = False,
        deferred_warning: str = "",
    ) -> str:
        """Return the warning text shown when hovering a context warning sign."""
        if deferred:
            return deferred_warning or "This context may be fetched or used after you send the prompt, so this token cost is not known yet."
        if tokens >= 1500:
            return "This context source is large and may cost noticeable input tokens."
        return ""

    @staticmethod
    def _redaction_count(text: str) -> int:
        """Return detected sensitive item count for preview-only privacy badges."""
        if not str(text or "").strip():
            return 0
        try:
            import config
            if not bool(getattr(config, "TRUST_PRIVACY_MODE", True)):
                return 0
            from core.privacy_redaction import redact_with_report

            _redacted, report = redact_with_report(str(text), source="preview")
            return int(report.get("count") or 0)
        except Exception:
            return 0

    @staticmethod
    def _context_preview_text(text: str, limit: int = 180) -> str:
        """Return a compact, privacy-safe snippet for context previews."""
        flat = " ".join(str(text or "").split())
        if not flat:
            return ""
        try:
            import config
            if bool(getattr(config, "TRUST_PRIVACY_MODE", True)):
                from core.privacy_redaction import redact_with_report

                flat, _report = redact_with_report(flat, source="preview")
                flat = " ".join(str(flat or "").split())
        except Exception:
            pass
        if len(flat) <= limit:
            return flat
        return flat[: max(0, limit - 3)].rstrip() + "..."

    def _file_context_progress_texts(self, file_context: list[dict[str, Any]] | None) -> list[str]:
        """Return one-line, display-only summaries for local file tool use."""
        texts: list[str] = []
        seen: set[tuple[str, str, bool]] = set()
        for raw in file_context or []:
            if not isinstance(raw, dict):
                continue
            tool = str(raw.get("tool") or "").strip()
            path = str(raw.get("relative_path") or raw.get("path") or "").strip()
            ok = bool(raw.get("ok"))
            if not tool or not path:
                continue
            key = (tool, path, ok)
            if key in seen:
                continue
            seen.add(key)
            if not ok:
                texts.append(t("Tool failed: {tool}: {path}").format(tool=tool, path=path))
            elif tool == "read_file":
                texts.append(t("Read file: {path}").format(path=path))
            elif tool == "list_files":
                texts.append(t("Listed files: {path}").format(path=path))
            else:
                texts.append(t("Used {tool}: {path}").format(tool=tool, path=path))
        return texts

    def _emit_file_context_progress(
        self,
        file_context: list[dict[str, Any]] | None,
        *,
        chat_request_id: str = "",
        conversation_index: int | None = None,
        include_bubble: bool = True,
    ) -> None:
        """Display local-file tool summaries without adding them to reply text."""
        for text in self._file_context_progress_texts(file_context):
            payload = {"text": text, "is_progress": True, "is_thought": True}
            if include_bubble:
                self._safe_call(self.ui, "ui.reply.chunk", payload, timeout=30.0)
            if chat_request_id:
                self._safe_call(
                    self.ui,
                    "ui.chat.chunk",
                    {"request_id": chat_request_id, **payload},
                    timeout=30.0,
                )
            elif conversation_index is not None:
                self._safe_call(
                    self.ui,
                    "ui.chat.chunk",
                    {"conversation_index": conversation_index, **payload},
                    timeout=30.0,
                )

    @staticmethod
    def _with_privacy_warning(warning: str, redactions: int) -> str:
        """Append detected-and-censored privacy detail to a context warning."""
        if redactions <= 0:
            return warning
        privacy = t("Privacy: {count} item(s) detected and censored.").format(count=redactions)
        return f"{warning}\n\n{privacy}" if warning else privacy

    def _intent_context_items(self, pending: PendingInvocation | None) -> list[dict[str, Any]]:
        """Build context preview chips for the intent overlay."""
        keys = self._intent_context_keys()
        caller = pending.caller if pending else {}
        context = pending.context if pending else {}
        context_loading = bool(
            pending is not None
            and not pending.context_ready.is_set()
            and not context
        )
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        document_window = context.get("document_window") if isinstance(context.get("document_window"), dict) else {}
        active_document_text = str(context.get("active_document_text") or "")
        removed_sources = pending.removed_context_sources if pending else set()
        removed_app_labels = {sid for iid, sid in removed_sources if iid == "ambient" and sid}
        app_source_previews = [
            dict(item)
            for item in (context.get("active_document_sources") or [])
            if isinstance(item, dict)
            and str(item.get("preview") or "").strip()
            and " ".join(str(item.get("label") or "").split()) not in removed_app_labels
        ]
        active_text = " ".join(
            str(part)
            for part in (
                active_app.get("name"),
                active_app.get("process_name"),
                active_app.get("title"),
                document_window.get("process_name"),
                document_window.get("title"),
                active_document_text,
            )
            if part
        )
        app_available = bool(active_text)
        document_state = self._mode_to_context_state(self._effective_document_mode(caller))
        app_on = bool(caller.get("context_ambient", True)) and app_available
        app_state = "on" if app_on or (document_state == "on" and app_available) else ("auto" if document_state == "auto" and app_available else "off")
        if caller.get("_context_ambient_enabled") is False:
            # Every app document row was removed via the picker's X buttons.
            app_state = "off"
        elif context_loading and (caller.get("context_ambient", True) or document_state != "off"):
            app_state = "auto" if document_state == "auto" else "on"
        app_deferred = bool(
            app_state != "off"
            and (
                context_loading
                or (document_state in {"on", "auto"} and app_available and not active_document_text)
            )
        )

        removed_browser_ids = {
            sid for iid, sid in removed_sources if iid == "browser" and sid
        }
        browser_pages = [
            page
            for page in self._browser_pages_from_context(context)
            if str(page.get("id") or "") not in removed_browser_ids
        ]
        browser_text = "\n\n".join(
            "\n".join(
                str(part)
                for part in (page.get("url"), page.get("content"))
                if part
            )
            for page in browser_pages
        ).strip()
        browser_available = bool(
            browser_pages
            or browser_text
            or context.get("browser_hwnd")
            or context.get("browser_app")
        )
        browser_state = self._mode_to_context_state(self._context_mode(caller, "browser"))
        browser_tokens = self._estimate_context_tokens(browser_text)
        browser_requested = browser_state != "off"
        browser_deferred = bool(
            browser_requested
            and (
                context_loading
                or not browser_pages
                or any(not str(page.get("content") or "").strip() for page in browser_pages)
            )
        )

        selected_text = str(context.get("selected_text") or "")
        selected_paths = self._selected_paths_from_context(context)
        selected_path_items = self._path_context_items(selected_paths)
        selected_path_parts: list[str] = []
        for item in selected_path_items:
            name = str(item.get("name") or "Selected file")
            if str(item.get("type") or "") == "image":
                selected_path_parts.append(f"[Image file]\n{name}")
            else:
                selected_path_parts.append(f"[{name}]\n{self._content_to_text(item.get('content'))}".strip())
        selected_path_text = "\n\n".join(part for part in selected_path_parts if part.strip())
        selected_context_text = "\n\n".join(
            part for part in (selected_text, selected_path_text) if part.strip()
        )
        platform_name = str(context.get("platform") or "").strip().lower()
        linux_selection_off_by_default = platform_name.startswith("linux")
        # A selection this picker surface already auto-filled once: offered
        # off-by-default so a cleared highlight never rides along silently,
        # while one toggle re-attaches it after an accidental close.
        stale_selected_text = (
            "" if selected_context_text else str(context.get("stale_selected_text") or "")
        )
        clipboard_text = str(context.get("clipboard_text") or "")
        github_mode = self._context_mode(caller, "github")
        memory_mode = self._context_mode(caller, "memory")
        file_mode = tool_modes.local_file_access_mode(caller)
        screenshot_mode = str(caller.get("context_screenshot") or "off").strip().lower()
        screenshot_preview = (pending.screenshot_b64 or pending.screenshot_tool_b64) if pending else None
        has_screenshot = bool(screenshot_preview)
        app_redactions = self._redaction_count(active_text)
        browser_redactions = self._redaction_count(browser_text)
        selected_redactions = self._redaction_count(selected_context_text or stale_selected_text)
        clipboard_redactions = self._redaction_count(clipboard_text)
        app_preview = self._context_preview_text(active_document_text or active_text)
        if app_source_previews:
            app_preview = str(app_source_previews[0].get("preview") or app_preview)
        browser_sources = [
            {
                "id": str(page.get("id") or ""),
                "app": self._browser_app_label(page),
                "label": self._browser_page_label(page),
                "preview": self._context_preview_text(
                    str(page.get("content") or page.get("url") or "Browser page text is loading.")
                ),
            }
            for page in browser_pages
        ]
        browser_preview = str(browser_sources[0].get("preview") or "") if browser_sources else ""
        if not browser_preview and browser_requested and browser_available:
            browser_preview = self._context_preview_text(
                context.get("browser_url")
                or context.get("browser_app")
                or "Browser page text may be fetched after you send the prompt."
            )
        selected_preview = self._context_preview_text(selected_context_text or stale_selected_text)
        clipboard_preview = self._context_preview_text(clipboard_text)
        selected_state = (
            "off"
            if linux_selection_off_by_default
            else ("on" if selected_context_text else "off")
        )
        if selected_context_text and linux_selection_off_by_default:
            selected_warning = (
                "Selection captured from the last focused app but not attached. "
                "Toggle Selection on to attach it."
            )
        elif selected_context_text:
            selected_warning = self._context_warning(
                self._estimate_context_tokens(selected_context_text),
                available=True,
            )
        elif stale_selected_text:
            selected_warning = (
                "Earlier selection available but not attached (it may no "
                "longer be highlighted). Toggle Selection on to attach it."
            )
        else:
            selected_warning = ""

        screenshot_state = "on" if (screenshot_mode == "auto" or (pending and pending.screenshot_b64)) else (
            "auto" if screenshot_mode == "model" else "off"
        )
        # A screenshot chip now represents a user-selected snip. Before the
        # crop exists its dimensions—and therefore its cost—are unknown. Using
        # the monitor dimensions here made an off chip look like a full-screen
        # capture and was wrong for region snips.
        screenshot_tokens = (
            self._image_token_label(screenshot_preview)
            if has_screenshot
            else self._deferred_token_label()
        )
        screenshot_token_count = (
            self._image_token_count(screenshot_preview) if has_screenshot else None
        )

        attachment_sources: list[dict[str, str]] = []
        attachment_text: list[str] = []
        attachment_images: list[str] = []
        for index, raw in enumerate(self._drop_context_items):
            item = self._normalize_context_item(raw)
            name = " ".join(str(item.get("name") or "Attachment").split()) or "Attachment"
            item_type = str(item.get("type") or "text")
            content = self._content_to_text(item.get("content"))
            if item_type == "image":
                preview = f"Image: {name}"
                if content:
                    attachment_images.append(content)
            else:
                preview = self._context_preview_text(content) or f"Attached file: {name}"
                if content:
                    attachment_text.append(content)
            attachment_sources.append(
                {
                    "id": f"dropped:{index}",
                    "label": name,
                    "preview": preview,
                }
            )

        attachment_tokens = ""
        attachment_token_count: int | None = None
        if attachment_images:
            attachment_tokens = (
                self._image_token_label(attachment_images[0])
                if len(attachment_sources) == 1
                else self._deferred_token_label()
            )
            if len(attachment_sources) == 1:
                attachment_token_count = self._image_token_count(attachment_images[0])
        elif attachment_text:
            joined_attachment_text = "\n\n".join(attachment_text)
            attachment_tokens = self._token_label(joined_attachment_text)
            attachment_token_count = self._estimate_context_tokens(joined_attachment_text)

        context_items = [
            {
                "id": "ambient",
                "key": keys[0],
                "label": "App",
                "state": app_state,
                "tokens": self._token_label(active_text),
                "token_count": self._estimate_context_tokens(active_text),
                "preview": app_preview,
                "sources": app_source_previews,
                "privacy_count": app_redactions,
                "warning": self._with_privacy_warning(
                    self._context_warning(
                        self._estimate_context_tokens(active_text),
                        available=app_available,
                        deferred=app_deferred,
                        deferred_warning="Active app or document context may be fetched after you send the prompt, so this token cost is not known yet.",
                    ) if app_state != "off" else "",
                    app_redactions,
                ),
            },
            {
                "id": "browser",
                "key": keys[1],
                "label": "Browser/Web",
                "state": browser_state if browser_requested else "off",
                "tokens": (
                    self._deferred_token_label()
                    if browser_deferred and not browser_text
                    else self._token_label(browser_text)
                ),
                "token_count": browser_tokens if browser_text else None,
                "preview": browser_preview,
                "sources": browser_sources,
                "privacy_count": browser_redactions,
                "warning": self._with_privacy_warning(
                    self._context_warning(
                        browser_tokens,
                        available=browser_available,
                        deferred=browser_deferred,
                        deferred_warning="Browser page text may be fetched after you send the prompt, so this token cost is not known yet.",
                    ) if browser_state != "off" else "",
                    browser_redactions,
                ),
            },
            {
                "id": "selection",
                "key": keys[2],
                "label": "Selection",
                "available": True,
                "state": selected_state,
                "stale": bool(stale_selected_text),
                "capture_on_enable": not linux_selection_off_by_default,
                "tokens": (
                    self._token_label(selected_context_text or stale_selected_text)
                    if (selected_context_text or stale_selected_text)
                    else ""
                ),
                "token_count": self._estimate_context_tokens(
                    selected_context_text or stale_selected_text
                ),
                "preview": selected_preview,
                "privacy_count": selected_redactions,
                "warning": self._with_privacy_warning(
                    selected_warning,
                    selected_redactions,
                ),
            },
            {
                "id": "clipboard",
                "key": keys[3],
                "label": "Clipboard",
                "state": "on" if caller.get("context_clipboard") and clipboard_text else "off",
                "tokens": self._token_label(clipboard_text),
                "token_count": self._estimate_context_tokens(clipboard_text),
                "preview": clipboard_preview,
                "privacy_count": clipboard_redactions,
                "warning": self._with_privacy_warning(
                    self._context_warning(
                        self._estimate_context_tokens(clipboard_text),
                        available=bool(clipboard_text),
                    ) if caller.get("context_clipboard") else "",
                    clipboard_redactions,
                ),
            },
            {
                "id": "screenshot",
                "key": keys[4],
                "label": "Screenshot",
                "state": screenshot_state,
                "tokens": screenshot_tokens,
                "token_count": screenshot_token_count,
                "warning": "",
            },
            {
                "id": "github",
                "key": keys[5],
                "label": "Git/GitHub",
                "state": self._mode_to_context_state(github_mode),
                "tokens": self._deferred_token_label() if github_mode != "off" else "0 tok",
                "warning": self._context_warning(0, deferred=True) if github_mode != "off" else "",
            },
            {
                "id": "memory",
                "key": keys[6],
                "label": "Memory",
                "state": self._mode_to_context_state(memory_mode),
                "tokens": self._deferred_token_label() if memory_mode != "off" else "0 tok",
                "warning": "Memory tokens are estimated after the prompt is known." if memory_mode != "off" else "",
            },
            {
                "id": "files",
                "key": keys[7],
                "label": "Files",
                "state": self._file_access_to_context_state(file_mode),
                "tokens": "",
                "warning": "",
            },
        ]
        if attachment_sources:
            attachment_privacy_count = self._redaction_count("\n\n".join(attachment_text))
            context_items.append(
                {
                    "id": "attachments",
                    "key": "",
                    "label": "Attachments",
                    "state": "on",
                    "default_state": "on",
                    "locked": True,
                    "tokens": attachment_tokens,
                    "token_count": attachment_token_count,
                    "preview": attachment_sources[0]["preview"],
                    "sources": attachment_sources,
                    "privacy_count": attachment_privacy_count,
                    "warning": self._with_privacy_warning("", attachment_privacy_count),
                }
            )
        return context_items

    @staticmethod
    def _apply_intent_context_choices(
        caller: dict[str, Any],
        choices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Apply per-prompt context chip choices to a caller policy copy."""
        updated = dict(caller or {})
        for item in choices or []:
            source = str(item.get("id") or "")
            state = str(item.get("state") or "off").lower()
            if source == "ambient":
                updated["context_ambient"] = state != "off"
                default_state = str(item.get("default_state") or state).lower()
                touched = bool(item.get("touched")) or state != default_state
                if state == "off":
                    updated["context_documents_mode"] = "off"
                elif touched:
                    updated["context_documents_mode"] = "model" if state == "auto" else "auto"
            elif source == "browser":
                updated["context_browser_mode"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
            elif source == "selection":
                updated["_context_selection_enabled"] = state != "off"
            elif source == "clipboard":
                updated["context_clipboard"] = state != "off"
            elif source == "screenshot":
                updated["context_screenshot"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
                updated["_context_screenshot_enabled"] = state != "off"
            elif source == "github":
                updated["context_github_mode"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
            elif source == "memory":
                updated["context_memory_mode"] = "off" if state == "off" else ("model" if state == "auto" else "on")
            elif source == "files":
                if state == "off":
                    updated["file_access"] = "off"
                elif tool_modes.local_file_access_mode(updated) == "off":
                    updated["file_access"] = "ask"
        return updated

    @staticmethod
    def _apply_intent_tool_choices(
        caller: dict[str, Any],
        choices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Apply only restrictive per-prompt tool switches to a caller copy."""
        updated = dict(caller or {})
        overrides = dict(updated.get("tools") or {})
        for item in choices or []:
            if not isinstance(item, dict) or not bool(item.get("toggleable")):
                continue
            if bool(item.get("enabled", True)):
                continue
            for raw_name in item.get("tools") or []:
                name = str(raw_name or "").strip()
                if name:
                    overrides[name] = "off"
        updated["tools"] = overrides
        return updated

    def _consume_context_extras(self) -> tuple[list[str], list[dict[str, Any]]]:
        """Handle consume context extras for flow controller."""
        buffered = list(self._context_buffer)
        dropped = list(self._drop_context_items)
        self._context_buffer.clear()
        self._drop_context_items.clear()
        if dropped:
            self._safe_call(self.ui, "ui.context.clear", timeout=30.0)
        return buffered, dropped

    @staticmethod
    def _normalize_context_item(item: Any) -> dict[str, Any]:
        """Normalize context item."""
        if isinstance(item, dict):
            return {
                "name": str(item.get("name") or item.get("label") or "Context"),
                "content": item.get("content", ""),
                "type": str(item.get("type") or item.get("item_type") or "text"),
            }
        return {"name": "Context", "content": str(item), "type": "text"}

    @staticmethod
    def _content_to_text(content: Any) -> str:
        """Handle content to text for flow controller."""
        if content is None:
            return ""
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        if isinstance(content, (dict, list, tuple)):
            return json_safe_dumps(content)
        return str(content)

    def _image_content_b64(self, content: Any) -> str | None:
        """Handle image content b64 for flow controller."""
        if isinstance(content, str):
            try:
                p = Path(content).expanduser()
                if p.exists():
                    return self._file_b64(p)
            except (OSError, ValueError):
                pass
            cleaned = content.strip()
            if cleaned:
                return cleaned
        return None

    @staticmethod
    def _short(text: str, n: int = 24) -> str:
        """Handle short for flow controller."""
        return flow_utils.short(text, n)

    @staticmethod
    def _selected_paths_from_context(context: dict[str, Any]) -> list[str]:
        """Return normalized selected file/folder paths from a native snapshot."""
        raw_paths = context.get("selected_paths") if isinstance(context, dict) else []
        if not isinstance(raw_paths, list):
            return []
        seen: set[str] = set()
        paths: list[str] = []
        for raw in raw_paths:
            path = str(raw or "").strip()
            if not path:
                continue
            try:
                key = os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))
            except Exception:
                key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
        return paths

    @staticmethod
    def _path_context_items(paths: Any) -> list[dict[str, Any]]:
        """Build dropped-context items for explicitly selected file/folder paths."""
        selected_paths = FlowController._selected_paths_from_context({"selected_paths": paths or []})
        items: list[dict[str, Any]] = []
        for raw_path in selected_paths:
            path = Path(raw_path).expanduser()
            path_text = str(path)
            name = path.name or path_text
            ext = path.suffix.lower()
            if path.is_dir():
                items.append({"name": name, "content": f"[Folder: {path_text}]", "type": "file"})
                continue
            if ext in _SELECTED_PATH_IMAGE_EXTS:
                try:
                    content = base64.b64encode(path.read_bytes()).decode("ascii")
                    items.append({"name": name, "content": content, "type": "image"})
                except OSError:
                    items.append({"name": name, "content": f"[Image file: {path_text}]", "type": "file"})
                continue
            if ext in _SELECTED_PATH_TEXT_EXTS or ext == "":
                try:
                    content = path.read_bytes()[:_SELECTED_PATH_TEXT_BYTES].decode("utf-8", errors="replace")
                    items.append({"name": name, "content": content, "type": "text"})
                except OSError:
                    items.append({"name": name, "content": f"[File: {path_text}]", "type": "file"})
                continue
            if ext in _SELECTED_PATH_DOCUMENT_EXTS:
                try:
                    from core.llm_clients.client import read_document_file

                    content = str(read_document_file(path_text) or "").strip()
                except Exception:
                    content = ""
                if content:
                    items.append({"name": name, "content": content, "type": "text"})
                else:
                    items.append({"name": name, "content": f"[File: {path_text}]", "type": "file"})
                continue
            items.append({"name": name, "content": f"[File: {path_text}]", "type": "file"})
        return items

    def _context_summary_badges(
        self,
        *,
        selected: str,
        screenshot_b64: str | None,
        buffered_items: list[str],
        drop_items: list[dict[str, Any]],
        clipboard_text: str,
        ambient_text: str,
        active_document_text: str,
    ) -> list[dict[str, str]]:
        """Handle context summary badges for flow controller."""
        items: list[dict[str, str]] = []

        def add_source(label: str, item_type: str) -> None:
            if not any(item.get("label") == label for item in items):
                items.append({"label": label, "type": item_type})

        if screenshot_b64:
            add_source("Screenshot", "image")
        if selected:
            add_source("Selection", "text")
        for item in drop_items:
            items.append(
                {
                    "label": self._short(str(item.get("name") or "Context"), 24),
                    "type": "image" if item.get("type") == "image" else "file",
                }
            )
        for buffered in buffered_items:
            items.append({"label": self._short(buffered, 24), "type": "text"})
        if clipboard_text:
            add_source("Clipboard", "text")
        if active_document_text:
            add_source("App", "file")
        if "[Browser/Web]" in (ambient_text or ""):
            add_source("Browser/Web", "file")
        ambient_without_browser = (ambient_text or "").replace("[Browser/Web]", "").strip()
        if ambient_without_browser:
            add_source("App", "file")
        return items[:8]

    def _capture_fullscreen_b64(self) -> str | None:
        """Handle capture fullscreen b64 for flow controller."""
        started = time.monotonic()
        try:
            result = self.native.call("native.capture.fullscreen", timeout=30.0)
        except Exception:
            log.exception("auto screenshot capture failed after %.2fs", time.monotonic() - started)
            return None
        path = result.get("path") if isinstance(result, dict) else ""
        image_b64 = self._file_b64(path) if path else None
        log.info(
            "auto screenshot capture ok=%s path=%r size=%s b64_chars=%d error=%r after %.2fs",
            result.get("ok") if isinstance(result, dict) else None,
            path,
            result.get("size") if isinstance(result, dict) else None,
            len(image_b64 or ""),
            result.get("error") if isinstance(result, dict) else None,
            time.monotonic() - started,
        )
        return image_b64

    def _capture_model_tool_b64(self) -> str:
        """Handle capture model tool b64 for flow controller."""
        started = time.monotonic()
        try:
            result = self.native.call("native.capture.fullscreen", timeout=8.0)
        except Exception:
            log.exception("model screenshot pre-capture failed after %.2fs", time.monotonic() - started)
            return ""
        path = result.get("path") if isinstance(result, dict) else ""
        image_b64 = self._file_b64(path) or ""
        log.info(
            "model screenshot pre-capture %s path=%r size=%s error=%r after %.2fs",
            "succeeded" if image_b64 else "returned empty",
            path,
            result.get("size") if isinstance(result, dict) else None,
            result.get("error") if isinstance(result, dict) else None,
            time.monotonic() - started,
        )
        return image_b64

    @staticmethod
    def _file_b64(path: str | Path | None) -> str | None:
        """Handle file b64 for flow controller."""
        return flow_estimates.file_b64(path)

    def _tts_enabled(self) -> bool:
        """Handle TTS enabled for flow controller."""
        import config

        return str(getattr(config, "TTS_PROVIDER", "none")).strip().lower() != "none"

    def _tts_replies_enabled(self) -> bool:
        """Return whether assistant replies should be spoken automatically."""
        import config

        if self._live_voice_busy():
            return False  # the live conversation owns the speaker; don't talk over it
        return self._tts_enabled() and bool(getattr(config, "TTS_SPEAK_REPLIES", False))

    def _tts_sequence_is_active(self) -> bool:
        """Return whether a segmented TTS queue owns playback state."""
        with self._tts_lock:
            return self._tts_sequence_active

    def _cancel_tts_sequence(self, generation: int) -> None:
        """Cancel queued TTS segments for one generation."""
        with self._tts_lock:
            q = self._tts_queue if self._tts_generation == generation else None
            if q is not None:
                self._tts_queue = None
                self._tts_sequence_active = False
        if q is not None:
            q.put(None)

    def _ensure_tts_sequence(self, generation: int) -> queue.Queue[str | None]:
        """Create or return the segmented TTS queue for this generation."""
        if self._reply_bubble_cancelled(generation):
            raise RuntimeError("reply bubble output is muted for this generation")
        with self._tts_lock:
            if self._tts_queue is not None and self._tts_generation == generation:
                return self._tts_queue
            q: queue.Queue[str | None] = queue.Queue()
            self._tts_generation = generation
            self._tts_queue = q
            self._tts_sequence_active = True
            threading.Thread(target=self._tts_sequence_worker, args=(generation, q), daemon=True).start()
            return q

    def _queue_tts_segment(self, generation: int, text: str) -> None:
        """Queue one completed reply segment for TTS playback."""
        segment = " ".join((text or "").split())
        if not segment or not self._is_current(generation) or self._reply_bubble_cancelled(generation):
            return
        try:
            q = self._ensure_tts_sequence(generation)
        except RuntimeError:
            return
        self._safe_call(self.ui, "ui.reply.track_speech", timeout=30.0)
        q.put(segment)

    def _finish_tts_sequence(self, generation: int) -> None:
        """Close the segmented TTS queue for this generation."""
        if self._reply_bubble_cancelled(generation):
            return
        with self._tts_lock:
            q = self._tts_queue if self._tts_generation == generation else None
        if q is not None:
            q.put(None)

    def _tts_sequence_worker(self, generation: int, q: queue.Queue[str | None]) -> None:
        """Synthesize and play queued TTS segments sequentially."""
        try:
            while self._is_current(generation) and not self._reply_bubble_cancelled(generation):
                segment = q.get()
                if segment is None:
                    break
                if not self._is_current(generation) or self._reply_bubble_cancelled(generation):
                    break
                played = self._speak_text(segment, generation=generation, wait_for_playback=True)
                if not played or not self._is_current(generation) or self._reply_bubble_cancelled(generation):
                    break
        finally:
            with self._tts_lock:
                if self._tts_queue is q:
                    self._tts_queue = None
                    self._tts_sequence_active = False
            if self._is_current(generation) and not self._reply_bubble_cancelled(generation):
                self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
                self._set_idle()

    def _begin_manual_tts_sequence(self, generation: int) -> None:
        """Mark playback as owned by a manual TTS flow."""
        with self._tts_lock:
            self._tts_generation = generation
            self._tts_queue = None
            self._tts_sequence_active = True

    def _end_manual_tts_sequence(self, generation: int) -> None:
        """Release manual TTS playback ownership."""
        with self._tts_lock:
            if self._tts_generation == generation and self._tts_queue is None:
                self._tts_sequence_active = False

    @staticmethod
    def _read_aloud_chunks(text: str) -> list[str]:
        """Split read-aloud text into responsive TTS chunks."""
        import config

        min_words = max(1, int(getattr(config, "TTS_READ_ALOUD_MIN_WORDS", _READ_ALOUD_MIN_WORDS)))
        max_words = max(min_words, int(getattr(config, "TTS_READ_ALOUD_MAX_WORDS", _READ_ALOUD_MAX_WORDS)))
        words = re.findall(r"\S+", text or "")
        if not words:
            return []
        chunks: list[str] = []
        current: list[str] = []
        for word in words:
            current.append(word)
            word_count = len(current)
            should_split = (
                word_count >= min_words
                and _READ_ALOUD_PAUSE_RE.search(word) is not None
            ) or word_count >= max_words
            if should_split:
                chunks.append(" ".join(current))
                current = []
        if current:
            chunks.append(" ".join(current))
        return chunks

    def _read_aloud_text(self, text: str, *, generation: int) -> bool:
        """Read selected text with one synthesized chunk buffered ahead."""
        chunks = self._read_aloud_chunks(text)
        if not chunks or not self._is_current(generation) or self._reply_bubble_cancelled(generation):
            return False
        if len(chunks) == 1:
            played = False
            reported_error = False
            self._begin_manual_tts_sequence(generation)
            try:
                try:
                    result = self.audio.call("audio.tts.synthesize", {"text": chunks[0]}, timeout=180.0)
                except Exception as exc:  # noqa: BLE001 - keep read-aloud user-facing
                    if "warming up" in str(exc).lower():
                        self._notice(
                            "TTS (local voice) is still warming up. "
                            "Wait for the speech status notice to show TTS ready."
                        )
                        reported_error = True
                    else:
                        log.exception("read selection aloud synthesis failed")
                    return reported_error
                path = result.get("path") if isinstance(result, dict) else ""
                if not path:
                    log.error("read selection aloud synthesis returned no path: %r", result)
                    return False
                if not self._is_current(generation) or self._reply_bubble_cancelled(generation):
                    return False
                self._safe_call(self.ui, "ui.reply.reading", {"text": text}, timeout=30.0)
                self._safe_call(self.ui, "ui.overlay.state", {"state": "speaking"}, timeout=30.0)
                play_result = self.audio.call("audio.play_file", {"path": path}, timeout=180.0)
                played = not (isinstance(play_result, dict) and play_result.get("stopped"))
                return played
            except Exception:
                log.exception("read selection aloud playback failed")
                return played
            finally:
                self._end_manual_tts_sequence(generation)
                if self._is_current(generation) and not self._reply_bubble_cancelled(generation):
                    self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
                    self._set_idle()

        synth_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
        stop_synth = threading.Event()

        def put_synth_result(item: dict[str, Any] | None) -> bool:
            while not stop_synth.is_set():
                try:
                    synth_queue.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    continue
            return False

        def synthesize_ahead() -> None:
            try:
                for chunk in chunks:
                    if (
                        stop_synth.is_set()
                        or not self._is_current(generation)
                        or self._reply_bubble_cancelled(generation)
                    ):
                        break
                    try:
                        result = self.audio.call("audio.tts.synthesize", {"text": chunk}, timeout=180.0)
                    except Exception as exc:  # noqa: BLE001 - surface playback failure below
                        put_synth_result({"error": exc})
                        return
                    if not put_synth_result({"chunk": chunk, "result": result}):
                        return
            finally:
                put_synth_result(None)

        self._begin_manual_tts_sequence(generation)
        threading.Thread(target=synthesize_ahead, daemon=True).start()
        played_any = False
        reported_error = False
        try:
            while self._is_current(generation) and not self._reply_bubble_cancelled(generation):
                try:
                    item = synth_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is None:
                    break
                error = item.get("error")
                if error is not None:
                    if "warming up" in str(error).lower():
                        self._notice(
                            "TTS (local voice) is still warming up. "
                            "Wait for the speech status notice to show TTS ready."
                        )
                        reported_error = True
                        break
                    log.error(
                        "read selection aloud synthesis failed",
                        exc_info=(type(error), error, error.__traceback__),
                    )
                    break
                result = item.get("result")
                path = result.get("path") if isinstance(result, dict) else ""
                if not path:
                    break
                if not self._is_current(generation) or self._reply_bubble_cancelled(generation):
                    break
                if not played_any:
                    self._safe_call(self.ui, "ui.reply.reading", {"text": text}, timeout=30.0)
                self._safe_call(self.ui, "ui.overlay.state", {"state": "speaking"}, timeout=30.0)
                play_result = self.audio.call("audio.play_file", {"path": path}, timeout=180.0)
                if isinstance(play_result, dict) and play_result.get("stopped"):
                    break
                played_any = True
            return played_any or reported_error
        except Exception:
            log.exception("read selection aloud playback failed")
            return played_any
        finally:
            stop_synth.set()
            self._end_manual_tts_sequence(generation)
            if self._is_current(generation) and not self._reply_bubble_cancelled(generation):
                self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
                self._set_idle()

    def _speak_text(
        self,
        text: str,
        *,
        generation: int | None = None,
        wait_for_playback: bool = False,
    ) -> bool:
        """Handle speak text for flow controller."""
        if generation is not None and (
            not self._is_current(generation) or self._reply_bubble_cancelled(generation)
        ):
            return False
        try:
            result = self.audio.call("audio.tts.synthesize", {"text": text}, timeout=180.0)
            path = result.get("path") if isinstance(result, dict) else ""
            if path:
                if generation is not None and (
                    not self._is_current(generation) or self._reply_bubble_cancelled(generation)
                ):
                    return False
                self._safe_call(self.ui, "ui.overlay.state", {"state": "speaking"}, timeout=30.0)
                # Buffer Cartesia word timestamps in the bubble *before* playback
                # starts. start_word_reveal - fired by the audio.playback.started
                # event below - drains them anchored to the real audio clock, so
                # the word highlight tracks the spoken voice instead of the
                # normal bubble reveal speed. Do NOT call ui.reply.start_reveal here: it would
                # anchor the reveal to synth-completion (before audio is audible)
                # and the playback-started reveal would then cancel it.
                wts = result.get("word_timestamps") if isinstance(result, dict) else None
                if isinstance(wts, dict) and wts.get("words") and not wts.get("estimated"):
                    self._safe_call(
                        self.ui,
                        "ui.reply.schedule_words",
                        {"words": wts.get("words"), "start_ms": wts.get("start_ms")},
                        timeout=30.0,
                    )
                if wait_for_playback:
                    play_result = self.audio.call("audio.play_file", {"path": path}, timeout=180.0)
                    if isinstance(play_result, dict) and play_result.get("stopped"):
                        return False
                else:
                    self.audio.call("audio.play_file", {"path": path}, wait=False)
                return True
            else:
                if not wait_for_playback:
                    self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
                    self._set_idle()
                return False
        except Exception:
            log.exception("audio playback failed")
            if not wait_for_playback:
                self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
                self._set_idle()
            return False
