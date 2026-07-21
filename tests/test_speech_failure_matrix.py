"""Executable fault probes for the shared speech runtime boundary."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import stt, tts, tts_assets


def test_tts_provider_auth_network_and_model_failures_are_controlled():
    ok, message = tts.test_connection("openai", openai_api_key="")
    assert ok is False
    assert "OPENAI_API_KEY" in message

    failures = (
        ConnectionError("provider network request failed"),
        RuntimeError("selected model is unsupported"),
    )
    for failure in failures:
        with patch("core.tts._stream_openai", side_effect=failure):
            ok, message = tts.test_connection(
                "openai",
                openai_api_key="test-key",
                openai_model="test-model",
            )
        assert ok is False
        assert str(failure) in message


def test_microphone_permission_failure_resets_recording_state(monkeypatch):
    class DeniedInputStream:
        def __init__(self, **_kwargs):
            raise PermissionError("microphone permission denied")

    monkeypatch.setattr(stt.macos_helper, "is_enabled", lambda: False)
    monkeypatch.setattr(stt.macos_safety, "audio_enabled", lambda: True)
    monkeypatch.setitem(
        __import__("sys").modules,
        "sounddevice",
        SimpleNamespace(InputStream=DeniedInputStream),
    )
    stt._stream = None
    stt._recording = False

    with pytest.raises(PermissionError, match="microphone permission denied"):
        stt.start_recording()

    assert stt._recording is False
    assert stt._stream is None
    assert stt._chunks == []


def test_missing_speech_model_assets_are_classified_without_network(monkeypatch):
    monkeypatch.setattr(tts_assets, "resolve_local", lambda _manifest, _filename: None)

    status = tts_assets.verify(tts_assets.KOKORO, voices=["af_heart"])

    assert status.state == "not_installed"
    assert status.problems
    assert status.missing_voices == ["af_heart"]


def test_damaged_speech_model_assets_are_classified_without_network(
    tmp_path, monkeypatch
):
    damaged = tmp_path / "damaged.bin"
    damaged.write_bytes(b"x")
    monkeypatch.setattr(
        tts_assets,
        "resolve_local",
        lambda _manifest, _filename: str(damaged),
    )

    status = tts_assets.verify(tts_assets.KOKORO)

    assert status.state == "damaged"
    assert any("expected" in problem for problem in status.problems)
