"""Typed snapshot objects for runtime configuration.

The app historically exposes settings as module-level names in ``config.py``.
Those globals stay for compatibility, while new code can accept an AppSettings
object to make dependencies explicit and easier to test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _copy_rows(value: Any) -> tuple[dict[str, Any], ...]:
    """Copy rows."""
    if not isinstance(value, list):
        return ()
    return tuple(dict(row) for row in value if isinstance(row, dict))


def _copy_dict(value: Any) -> dict[str, Any]:
    """Copy dict."""
    return dict(value) if isinstance(value, dict) else {}


def _copy_bool(value: Any, default: bool = False) -> bool:
    """Copy bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class ModelSettings:
    """Store model settings configuration data."""
    provider: str
    model: str
    fallbacks: str = ""


@dataclass(frozen=True)
class ContextBudgets:
    """Model context budgets."""
    browser_max_chars: int
    ambient_document_max_chars: int
    tool_document_max_chars: int


@dataclass(frozen=True)
class UiSettings:
    """Store ui settings configuration data."""
    app_language: str
    assistant_language: str
    bubble_width: int
    bubble_lines: int
    icon_size: int
    bubble_hide_delay_ms: int
    bubble_scroll_enabled: bool
    bubble_scroll_snap_enabled: bool
    bubble_scroll_snap_delay_ms: int


@dataclass(frozen=True)
class AudioSettings:
    """Store audio settings configuration data."""
    tts_provider: str
    tts_playback_rate: float
    tts_hold_playback_rate: float
    stt_model: str
    stt_device: str
    stt_language: str
    stt_beam_size: int


@dataclass(frozen=True)
class MemorySettings:
    """Store memory settings configuration data."""
    model: ModelSettings
    auto_consolidate: bool
    top_k: int
    stm_token_budget: int


@dataclass(frozen=True)
class PrivacySettings:
    """Store trust and privacy settings configuration data."""
    trust_privacy_mode: bool


@dataclass(frozen=True)
class CallerSettings:
    """Store caller settings configuration data."""
    callers: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    voice: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppSettings:
    """Store app settings configuration data."""
    llm: ModelSettings
    chat_llm: ModelSettings
    vision_llm: ModelSettings
    ui: UiSettings
    audio: AudioSettings
    memory: MemorySettings
    privacy: PrivacySettings
    callers: CallerSettings
    context: ContextBudgets
    tool_plugin_dir: str
    tool_git_root: str
    tool_file_roots: tuple[str, ...]
    tool_file_mode: str
    tool_file_blocked_globs: tuple[str, ...]
    system_prompt_utility: str

    @classmethod
    def from_config(cls, values: dict[str, Any]) -> AppSettings:
        """Handle from config for app settings."""
        return cls(
            llm=ModelSettings(
                provider=str(values.get("LLM_PROVIDER", "")),
                model=str(values.get("LLM_MODEL", "")),
                fallbacks=str(values.get("LLM_FALLBACKS", "")),
            ),
            chat_llm=ModelSettings(
                provider=str(values.get("CHAT_LLM_PROVIDER", "")),
                model=str(values.get("CHAT_LLM_MODEL", "")),
                fallbacks=str(values.get("CHAT_LLM_FALLBACKS", "")),
            ),
            vision_llm=ModelSettings(
                provider=str(values.get("VISION_LLM_PROVIDER", "")),
                model=str(values.get("VISION_LLM_MODEL", "")),
                fallbacks=str(values.get("VISION_LLM_FALLBACKS", "")),
            ),
            ui=UiSettings(
                app_language=str(values.get("APP_LANGUAGE", "")),
                assistant_language=str(values.get("ASSISTANT_LANGUAGE", "")),
                bubble_width=int(values.get("BUBBLE_WIDTH", 0)),
                bubble_lines=int(values.get("BUBBLE_LINES", 0)),
                icon_size=int(values.get("ICON_SIZE", 0)),
                bubble_hide_delay_ms=int(values.get("BUBBLE_HIDE_DELAY_MS", 0)),
                bubble_scroll_enabled=_copy_bool(values.get("BUBBLE_SCROLL_ENABLED"), True),
                bubble_scroll_snap_enabled=_copy_bool(values.get("BUBBLE_SCROLL_SNAP_ENABLED"), True),
                bubble_scroll_snap_delay_ms=int(values.get("BUBBLE_SCROLL_SNAP_DELAY_MS", 0)),
            ),
            audio=AudioSettings(
                tts_provider=str(values.get("TTS_PROVIDER", "")),
                tts_playback_rate=float(values.get("TTS_PLAYBACK_RATE", 1.0)),
                tts_hold_playback_rate=float(values.get("TTS_HOLD_PLAYBACK_RATE", 1.0)),
                stt_model=str(values.get("STT_MODEL", "")),
                stt_device=str(values.get("STT_DEVICE", "")),
                stt_language=str(values.get("STT_LANGUAGE", "")),
                stt_beam_size=int(values.get("STT_BEAM_SIZE", 0)),
            ),
            memory=MemorySettings(
                model=ModelSettings(
                    provider=str(values.get("MEMORY_LLM_PROVIDER", "")),
                    model=str(values.get("MEMORY_LLM_MODEL", "")),
                    fallbacks=str(values.get("MEMORY_LLM_FALLBACKS", "")),
                ),
                auto_consolidate=bool(values.get("MEMORY_AUTO_CONSOLIDATE", False)),
                top_k=int(values.get("MEMORY_TOP_K", 0)),
                stm_token_budget=int(values.get("MEMORY_STM_TOKEN_BUDGET", 0)),
            ),
            privacy=PrivacySettings(
                trust_privacy_mode=_copy_bool(values.get("TRUST_PRIVACY_MODE"), True),
            ),
            callers=CallerSettings(
                callers=_copy_rows(values.get("CALLER_ROWS")),
                voice=_copy_dict(values.get("VOICE_CALLER")),
            ),
            context=ContextBudgets(
                browser_max_chars=int(values.get("CONTEXT_BROWSER_MAX_CHARS", 0)),
                ambient_document_max_chars=int(values.get("CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS", 0)),
                tool_document_max_chars=int(values.get("CONTEXT_TOOL_DOCUMENT_MAX_CHARS", 0)),
            ),
            tool_plugin_dir=str(values.get("TOOL_PLUGIN_DIR", "")),
            tool_git_root=str(values.get("TOOL_GIT_ROOT", "")),
            tool_file_roots=tuple(str(v) for v in values.get("TOOL_FILE_ROOTS", ()) or ()),
            tool_file_mode=str(values.get("TOOL_FILE_MODE", "never")),
            tool_file_blocked_globs=tuple(
                str(v) for v in values.get("TOOL_FILE_BLOCKED_GLOBS", ()) or ()
            ),
            system_prompt_utility=str(values.get("SYSTEM_PROMPT_UTILITY", "")),
        )
