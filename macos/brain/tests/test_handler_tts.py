"""Unit tests for the ``brain.tts.synthesize`` handler.

The handler's job is to turn text into a standard mono int16 WAV *file* whose
path crosses IPC (Swift plays it). We verify the offline seam, the empty-text
guard, and -- with a fake ``core.tts`` provider injected -- the real WAV-writing
branches for both float32 providers (e.g. cartesia) and elevenlabs, plus the
"none"/empty-chunks path. No network or audio device is touched.
"""
from __future__ import annotations

import sys
import types
import wave

import pytest

from wisp_brain import handlers

np = pytest.importorskip("numpy")


@pytest.fixture(autouse=True)
def _run_log_dir(tmp_path, monkeypatch):
    # Keep artifacts inside the test's tmp dir.
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(tmp_path))


def _inject_fake_tts(monkeypatch, *, provider, chunks):
    """Install a fake config.TTS_PROVIDER and a fake core.tts module."""
    import config

    monkeypatch.setattr(config, "TTS_PROVIDER", provider, raising=False)

    fake_tts = types.ModuleType("core.tts")
    fake_tts.stream_audio = lambda text: iter(chunks)
    fake_tts.SAMPLE_RATE = 22_050
    fake_tts._EL_SAMPLE_RATE = 44_100
    monkeypatch.setitem(sys.modules, "core.tts", fake_tts)


def _read_wav(path):
    with wave.open(str(path), "rb") as wf:
        return wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()


def test_tts_is_registered_unary():
    assert "brain.tts.synthesize" in handlers.HANDLERS
    assert "brain.tts.synthesize" not in handlers.STREAMING


def test_tts_test_is_registered_unary():
    assert "brain.tts.test" in handlers.HANDLERS
    assert "brain.tts.test" not in handlers.STREAMING


def test_tts_requires_text():
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.tts.synthesize"](text="   ")


def test_tts_offline_seam_writes_silent_wav(monkeypatch):
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    result = handlers.HANDLERS["brain.tts.synthesize"](text="hello there")
    assert result["provider"] == "fake"
    assert result["bytes"] > 0
    channels, width, rate, frames = _read_wav(result["path"])
    assert (channels, width, rate) == (1, 2, 22_050)
    assert frames > 0


def test_tts_test_offline_seam(monkeypatch):
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    result = handlers.HANDLERS["brain.tts.test"](provider="cartesia")
    assert result == {"ok": True, "message": "TTS route OK: cartesia", "provider": "cartesia"}


def test_tts_test_forwards_provider_and_voice(monkeypatch):
    import config

    captured = {}
    fake_tts = types.ModuleType("core.tts")

    def fake_test_connection(provider, *, cartesia_voice_id=None):
        captured["provider"] = provider
        captured["cartesia_voice_id"] = cartesia_voice_id
        return True, "ok"

    fake_tts.test_connection = fake_test_connection
    monkeypatch.setattr(config, "TTS_PROVIDER", "none", raising=False)
    monkeypatch.setitem(sys.modules, "core.tts", fake_tts)

    result = handlers.HANDLERS["brain.tts.test"](
        provider="cartesia",
        cartesia_voice_id="voice-123",
    )

    assert result == {"ok": True, "message": "ok", "provider": "cartesia"}
    assert captured == {"provider": "cartesia", "cartesia_voice_id": "voice-123"}


def test_tts_none_provider_writes_empty_wav(monkeypatch):
    _inject_fake_tts(monkeypatch, provider="none", chunks=[])
    result = handlers.HANDLERS["brain.tts.synthesize"](text="hello")
    assert result["provider"] == "none"
    assert result["bytes"] == 0
    channels, width, rate, frames = _read_wav(result["path"])
    assert (channels, width) == (1, 2)
    assert frames == 0


def test_tts_float_provider_writes_pcm(monkeypatch):
    samples = np.zeros(2048, dtype=np.float32).tobytes()
    _inject_fake_tts(monkeypatch, provider="cartesia", chunks=[samples])
    result = handlers.HANDLERS["brain.tts.synthesize"](text="speak this")
    assert result["provider"] == "cartesia"
    assert result["sample_rate"] == 22_050
    assert result["bytes"] > 0
    _, width, rate, frames = _read_wav(result["path"])
    assert width == 2 and rate == 22_050 and frames > 0


def test_tts_elevenlabs_uses_el_sample_rate(monkeypatch):
    pcm16 = (b"\x00\x00" * 1024)
    _inject_fake_tts(monkeypatch, provider="elevenlabs", chunks=[pcm16])
    result = handlers.HANDLERS["brain.tts.synthesize"](text="speak this")
    assert result["provider"] == "elevenlabs"
    assert result["sample_rate"] == 44_100
    assert result["bytes"] == len(pcm16)
