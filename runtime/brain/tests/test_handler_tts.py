"""Unit tests for the ``brain.tts.synthesize`` handler.

The handler's job is to turn text into a standard mono int16 WAV *file* whose
path crosses IPC (the audio worker plays it). We verify the offline seam, the empty-text
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
    """Verify run log dir behavior."""
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(tmp_path))


def _inject_fake_tts(monkeypatch, *, provider, chunks):
    """Install a fake config.TTS_PROVIDER and a fake core.tts module."""
    import config

    monkeypatch.setattr(config, "TTS_PROVIDER", provider, raising=False)

    fake_tts = types.ModuleType("core.tts")
    fake_tts.stream_audio = lambda text: iter(chunks)
    fake_tts.SAMPLE_RATE = 22_050
    fake_tts._EL_SAMPLE_RATE = 44_100

    def fake_playback_format(prov):
        # Mirror core.tts.playback_format: elevenlabs streams int16, the
        # float32 providers (cartesia) need the convert-to-int16 branch.
        """Verify fake playback format behavior."""
        if prov == "elevenlabs":
            return fake_tts._EL_SAMPLE_RATE, 1, "int16"
        return fake_tts.SAMPLE_RATE, 1, "float32"

    fake_tts.playback_format = fake_playback_format
    monkeypatch.setitem(sys.modules, "core.tts", fake_tts)


def _read_wav(path):
    """Verify read wav behavior."""
    with wave.open(str(path), "rb") as wf:
        return wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()


def test_tts_is_registered_unary():
    """Verify tts is registered unary behavior."""
    assert "brain.tts.synthesize" in handlers.HANDLERS
    assert "brain.tts.synthesize" not in handlers.STREAMING


def test_tts_test_is_registered_unary():
    """Verify tts test is registered unary behavior."""
    assert "brain.tts.test" in handlers.HANDLERS
    assert "brain.tts.test" not in handlers.STREAMING


def test_tts_requires_text():
    """Verify tts requires text behavior."""
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.tts.synthesize"](text="   ")


def test_tts_offline_seam_writes_silent_wav(monkeypatch):
    """Verify tts offline seam writes silent wav behavior."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    result = handlers.HANDLERS["brain.tts.synthesize"](text="hello there")
    assert result["provider"] == "fake"
    assert result["bytes"] > 0
    channels, width, rate, frames = _read_wav(result["path"])
    assert (channels, width, rate) == (1, 2, 22_050)
    assert frames > 0


def test_tts_test_offline_seam(monkeypatch):
    """Verify tts test offline seam behavior."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    result = handlers.HANDLERS["brain.tts.test"](provider="cartesia")
    assert result == {"ok": True, "message": "TTS route OK: cartesia", "provider": "cartesia"}


def test_tts_test_forwards_provider_and_voice(monkeypatch):
    """Verify tts test forwards provider and voice behavior."""
    import config

    captured = {}
    fake_tts = types.ModuleType("core.tts")

    def fake_test_connection(provider, *, cartesia_voice_id=None):
        """Verify fake connection behavior."""
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
    """Verify tts none provider writes empty wav behavior."""
    _inject_fake_tts(monkeypatch, provider="none", chunks=[])
    result = handlers.HANDLERS["brain.tts.synthesize"](text="hello")
    assert result["provider"] == "none"
    assert result["bytes"] == 0
    channels, width, rate, frames = _read_wav(result["path"])
    assert (channels, width) == (1, 2)
    assert frames == 0


def test_tts_float_provider_writes_pcm(monkeypatch):
    """Verify tts float provider writes pcm behavior."""
    samples = np.zeros(2048, dtype=np.float32).tobytes()
    _inject_fake_tts(monkeypatch, provider="cartesia", chunks=[samples])
    result = handlers.HANDLERS["brain.tts.synthesize"](text="speak this")
    assert result["provider"] == "cartesia"
    assert result["sample_rate"] == 22_050
    assert result["bytes"] > 0
    _, width, rate, frames = _read_wav(result["path"])
    assert width == 2 and rate == 22_050 and frames > 0


def test_tts_elevenlabs_uses_el_sample_rate(monkeypatch):
    """Verify tts elevenlabs uses el sample rate behavior."""
    pcm16 = (b"\x00\x00" * 1024)
    _inject_fake_tts(monkeypatch, provider="elevenlabs", chunks=[pcm16])
    result = handlers.HANDLERS["brain.tts.synthesize"](text="speak this")
    assert result["provider"] == "elevenlabs"
    assert result["sample_rate"] == 44_100
    assert result["bytes"] == len(pcm16)

