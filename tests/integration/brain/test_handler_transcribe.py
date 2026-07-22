"""Unit tests for the ``brain.transcribe`` handler.

Covers the contract checks and the short-audio fast path, both of which run
without faster-whisper or a model download. The actual model transcription is
validated on the Mac (it needs the STT model present); here we prove the handler
reads the audio-worker-provided WAV path, normalizes it, and rejects too-short clips
before ever loading a model.
"""
from __future__ import annotations

import wave

import pytest
from wisp_brain import handlers

# The handler reads audio via soundfile; skip cleanly where it isn't installed.
pytest.importorskip("numpy")
pytest.importorskip("soundfile")


def _write_silent_wav(path, *, sample_rate=16_000, samples=1600):
    """Verify write silent wav behavior."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * samples)


def test_transcribe_requires_pcm_path():
    """Verify transcribe requires pcm path behavior."""
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.transcribe"](pcm_path="")


def test_transcribe_short_clip_returns_too_short(tmp_path):
    """Verify transcribe short clip returns too short behavior."""
    wav = tmp_path / "blip.wav"
    _write_silent_wav(wav, sample_rate=16_000, samples=1600)  # 0.1s < 0.25s gate
    result = handlers.HANDLERS["brain.transcribe"](pcm_path=str(wav))
    assert result["text"] == ""
    assert result["reason"] == "too_short"
    assert result["duration"] == pytest.approx(0.1, abs=0.01)


def test_transcribe_resamples_non_16k_without_model(tmp_path):
    # 8 kHz, still under the 0.25s gate after resample, so no model is loaded but
    # the resample branch is exercised (1200 samples @ 8k -> ~0.15s).
    """Verify transcribe resamples non 16k without model behavior."""
    wav = tmp_path / "blip8k.wav"
    _write_silent_wav(wav, sample_rate=8_000, samples=1200)
    result = handlers.HANDLERS["brain.transcribe"](pcm_path=str(wav))
    assert result["reason"] == "too_short"


