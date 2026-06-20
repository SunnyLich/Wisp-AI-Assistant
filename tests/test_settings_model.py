"""Tests for test settings model."""

from __future__ import annotations

import os
from unittest.mock import patch

import config
from core.settings_model import AppSettings


def _snapshot_config_globals() -> dict[str, object]:
    """Return a shallow snapshot of config globals mutated by config.reload()."""
    snapshot: dict[str, object] = {}
    for name, value in vars(config).items():
        if not name.isupper():
            continue
        if isinstance(value, list):
            snapshot[name] = list(value)
        elif isinstance(value, dict):
            snapshot[name] = dict(value)
        else:
            snapshot[name] = value
    return snapshot


def _restore_config_globals(snapshot: dict[str, object]) -> None:
    """Restore config globals after a reload-based settings test."""
    for name, value in snapshot.items():
        current = getattr(config, name, None)
        if isinstance(current, list) and isinstance(value, list):
            current[:] = value
        elif isinstance(current, dict) and isinstance(value, dict):
            current.clear()
            current.update(value)
        else:
            setattr(config, name, value)


def test_get_settings_returns_typed_snapshot():
    """Verify get settings returns typed snapshot behavior."""
    previous_config = _snapshot_config_globals()
    try:
        with patch("config.load_dotenv"), patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "anthropic",
                "LLM_MODEL": "claude-test",
                "CALLER_COUNT": "1",
                "CALLER_1_LABEL": "Typed",
                "VOICE_CONTEXT_MEMORY_MODE": "model",
                "BUBBLE_WIDTH": "420",
                "BUBBLE_FONT_SIZE": "14",
                "BUBBLE_SCROLL_ENABLED": "false",
                "BUBBLE_SCROLL_SNAP_DELAY_MS": "1800",
                "TRUST_PRIVACY_MODE": "true",
            },
            clear=False,
        ):
            config.reload()

        settings = config.get_settings()

        assert isinstance(settings, AppSettings)
        assert settings.llm.provider == "anthropic"
        assert settings.llm.model == "claude-test"
        assert settings.ui.bubble_width == 420
        assert settings.ui.bubble_font_size == 14
        assert settings.ui.bubble_scroll_enabled is False
        assert settings.ui.bubble_scroll_snap_delay_ms == 1800
        assert settings.privacy.trust_privacy_mode is True
        assert settings.callers.callers[0]["label"] == "Typed"
        assert settings.callers.voice["context_memory_mode"] == "model"
    finally:
        _restore_config_globals(previous_config)


def test_trust_privacy_mode_can_be_disabled():
    """Verify trust privacy mode can be disabled behavior."""
    previous_config = _snapshot_config_globals()
    try:
        with patch("config.load_dotenv"), patch.dict(
            os.environ,
            {
                "TRUST_PRIVACY_MODE": "false",
            },
            clear=True,
        ):
            config.reload()

        assert config.TRUST_PRIVACY_MODE is False
        assert config.get_settings().privacy.trust_privacy_mode is False
    finally:
        _restore_config_globals(previous_config)


def test_trust_privacy_mode_defaults_on():
    """Verify trust privacy mode defaults on behavior."""
    previous_config = _snapshot_config_globals()
    try:
        with patch("config.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            config.reload()

        assert config.TRUST_PRIVACY_MODE is True
        assert config.get_settings().privacy.trust_privacy_mode is True
    finally:
        _restore_config_globals(previous_config)
