"""STT round-trip: known speech in, matching transcript out. No microphone.

Proves dictation actually works, not just that the model loads: synthesize a
known English sentence with Kokoro (cached in .artifacts after the first run,
so later runs skip the TTS model entirely), resample to the 16 kHz mono float32
the app's recorder produces, and feed it through the REAL active transcription
path (``core.macos_helper.handlers._transcribe_audio`` - the same code live
dictation uses on every OS). Passes when most of the sentence comes back.

Also runs the mic-free noise selftest first (model load + inference sanity).

The reference is English and the transcribe call pins language=en for this
process only - the check tests "does STT function", not the user's language
setting (which is reported in extras instead). A hand-recorded fallback can be
dropped at testlab/assets/reference_speech.wav (16 kHz mono) with its words in
reference_speech.txt.
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _lab

_lab.bootstrap()

SENTENCE = "The quick brown fox jumps over the lazy dog."
EXPECTED_WORDS = {"quick", "brown", "fox", "jumps", "over", "lazy", "dog"}
MATCH_RATIO = 0.6
TARGET_RATE = 16_000

ASSETS_DIR = _lab.TESTLAB_DIR / "assets"
CACHE_WAV = _lab.ARTIFACTS_DIR / "stt_ref" / "reference_speech.wav"


def _save_wav(path: Path, audio, rate: int) -> None:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)


def _load_wav(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    audio = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    return audio, rate


def _resample(audio, rate: int, target: int):
    import numpy as np

    if rate == target:
        return audio
    duration = len(audio) / float(rate)
    n_target = int(duration * target)
    src_t = np.linspace(0.0, duration, num=len(audio), endpoint=False)
    dst_t = np.linspace(0.0, duration, num=n_target, endpoint=False)
    return np.interp(dst_t, src_t, audio).astype("float32")


def _reference_audio(watch: _lab.Stopwatch):
    """Return (audio16k, source_label, expected_words) or (None, reason, set())."""
    manual_wav = ASSETS_DIR / "reference_speech.wav"
    manual_txt = ASSETS_DIR / "reference_speech.txt"
    if manual_wav.is_file():
        audio, rate = _load_wav(manual_wav)
        words = EXPECTED_WORDS
        if manual_txt.is_file():
            words = {w.strip(".,!?").lower() for w in manual_txt.read_text(encoding="utf-8").split()}
            words = {w for w in words if len(w) > 2}
        _lab.log(f"using hand-recorded reference {manual_wav} ({rate} Hz)")
        return _resample(audio, rate, TARGET_RATE), "manual recording", words

    if CACHE_WAV.is_file():
        audio, rate = _load_wav(CACHE_WAV)
        _lab.log(f"using cached Kokoro reference from a previous run ({CACHE_WAV})")
        return _resample(audio, rate, TARGET_RATE), "cached kokoro", EXPECTED_WORDS

    import config
    from core import tts

    try:
        if not tts.kokoro_installed():
            return None, "no reference speech: Kokoro not installed and no assets/reference_speech.wav", set()
    except Exception as exc:  # noqa: BLE001
        return None, f"kokoro probe failed: {exc}", set()

    # English voice on CPU regardless of user settings: this synth only exists
    # to produce a deterministic reference clip (in-process config only).
    config.TTS_PROVIDER = "kokoro"
    config.KOKORO_VOICE = "af_heart"
    config.KOKORO_LANG_CODE = "a"
    config.KOKORO_DEVICE = "cpu"
    _lab.log("synthesizing reference sentence with Kokoro (first run only, model load takes a minute)...")
    import numpy as np

    chunks = list(tts.stream_audio_from_chunks([SENTENCE]))
    if not chunks:
        return None, "kokoro produced no audio for the reference sentence", set()
    rate, _channels, dtype = tts.playback_format("kokoro")
    if dtype == "int16":
        audio = np.frombuffer(b"".join(chunks), dtype="<i2").astype("float32") / 32768.0
    else:
        audio = np.frombuffer(b"".join(chunks), dtype=np.float32)
    _lab.log(f"kokoro reference ready after {watch.lap()}s ({len(audio) / rate:.2f}s @ {rate} Hz)")
    audio16 = _resample(audio, rate, TARGET_RATE)
    _save_wav(CACHE_WAV, audio16, TARGET_RATE)
    _lab.log(f"cached reference at {CACHE_WAV}")
    return audio16, "kokoro synth", EXPECTED_WORDS


def main() -> int:
    import config

    model_name = str(getattr(config, "STT_MODEL", "") or "").strip()
    if not model_name:
        return _lab.finish(_lab.SKIP, "STT_MODEL is empty (dictation off) - nothing to test")

    from core import optional_deps

    status = optional_deps.stt_runtime_import_status_fast()
    if not (status.get("installed") and status.get("valid")):
        return _lab.finish(
            _lab.SKIP,
            f"faster-whisper not importable ({status.get('error') or status}) - install_stt covers installs",
        )
    _lab.log(f"faster-whisper {status.get('version')} at {status.get('origin')}")

    watch = _lab.Stopwatch()
    audio, source, expected = _reference_audio(watch)
    if audio is None:
        return _lab.finish(_lab.SKIP, source)
    _lab.log(f"reference audio: {len(audio) / TARGET_RATE:.2f}s from {source}")

    from core.macos_helper import handlers

    configured_language = str(getattr(config, "STT_LANGUAGE", "") or "auto")
    config.STT_LANGUAGE = "en"  # in-process only; reference speech is English

    _lab.log(f"loading whisper model '{model_name}' (device={getattr(config, 'STT_DEVICE', 'auto')})...")
    selftest = handlers.stt_selftest(seconds=0.5)
    load_seconds = watch.lap()
    _lab.log(f"noise selftest after {load_seconds}s: {selftest}")
    if not selftest.get("ok"):
        return _lab.finish(_lab.FAIL, f"whisper model failed to load/transcribe: {selftest}")

    transcript = handlers._transcribe_audio(audio, label="testlab-roundtrip")
    total_seconds = watch.lap()
    _lab.log(f"transcript: {transcript!r}")

    words = {w.strip(".,!?。，").lower() for w in transcript.split()}
    hits = expected & words
    ratio = len(hits) / max(1, len(expected))
    _lab.log(f"matched {len(hits)}/{len(expected)} expected words: {sorted(hits)}")
    if not transcript.strip():
        return _lab.finish(_lab.FAIL, "empty transcript for known speech (silence-gate or model failure)")
    if ratio < MATCH_RATIO:
        return _lab.finish(
            _lab.FAIL,
            f"transcript matched only {ratio:.0%} of the sentence: {transcript!r}",
        )
    return _lab.finish(
        _lab.PASS,
        f"'{model_name}' transcribed {ratio:.0%} of the reference ({source}) in {total_seconds}s",
        model=model_name,
        source=source,
        transcript=transcript,
        match_ratio=round(ratio, 2),
        configured_language=configured_language,
        seconds=total_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
