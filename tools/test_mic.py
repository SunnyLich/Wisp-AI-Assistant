"""
tools/test_mic.py — Verify microphone + faster-whisper work before running the full app.

Usage:
    python tools/test_mic.py
"""
import sys
import os
import numpy as np

SAMPLE_RATE = 16_000
DURATION    = 4   # seconds to record


def _require_sounddevice():
    try:
        import sounddevice as sd
    except ImportError:
        print("  ERROR: sounddevice is not installed.\n  Run: pip install sounddevice")
        sys.exit(1)
    return sd

def list_inputs():
    sd = _require_sounddevice()
    print("\n=== Audio input devices ===")
    devices = sd.query_devices()
    default_in = sd.default.device[0]
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            marker = " <-- default" if i == default_in else ""
            print(f"  [{i}] {d['name']}{marker}")
    print()

def record_and_transcribe():
    sd = _require_sounddevice()
    import time
    for i in (3, 2, 1):
        print(f"  Starting in {i}…", end="\r")
        time.sleep(1)
    print(f"Recording {DURATION}s — SPEAK NOW                    ")
    audio = sd.rec(
        int(DURATION * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    audio = audio.flatten()
    peak = np.abs(audio).max()
    rms  = float(np.sqrt(np.mean(audio ** 2)))
    print(f"  Peak: {peak:.4f}   RMS: {rms:.4f}")
    if peak < 0.001:
        print("  WARNING: audio is nearly silent — check mic permissions or default device.")
        return

    print("Transcribing…")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("  ERROR: faster-whisper is not installed.\n  Run: pip install faster-whisper")
        sys.exit(1)

    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio, beam_size=1, language="en", vad_filter=False)
    text = " ".join(s.text.strip() for s in segments).strip()
    print(f"\n  Transcribed: {text!r}")
    if not text:
        print("  (empty — try speaking louder or closer to the mic)")

if __name__ == "__main__":
    list_inputs()
    record_and_transcribe()
