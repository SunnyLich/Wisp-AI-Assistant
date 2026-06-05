from __future__ import annotations

import sys
import types

from wisp_brain import handlers


def test_config_reload_handler_registered():
    assert "brain.config.reload" in handlers.HANDLERS


def test_config_reload_calls_config_reload(monkeypatch):
    calls: list[str] = []

    fake_config = types.ModuleType("config")
    fake_config.LLM_PROVIDER = "openai"
    fake_config.LLM_MODEL = "gpt-5.4"
    fake_config.TTS_PROVIDER = "none"

    def reload() -> None:
        calls.append("reload")
        fake_config.LLM_PROVIDER = "anthropic"
        fake_config.LLM_MODEL = "claude-sonnet-4-5"
        fake_config.TTS_PROVIDER = "cartesia"

    fake_config.reload = reload
    monkeypatch.setitem(sys.modules, "config", fake_config)

    result = handlers.HANDLERS["brain.config.reload"]()

    assert calls == ["reload"]
    assert result == {
        "ok": True,
        "llm_provider": "anthropic",
        "llm_model": "claude-sonnet-4-5",
        "tts_provider": "cartesia",
    }
