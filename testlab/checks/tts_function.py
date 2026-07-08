"""Real TTS synthesis (and playback) through the real audio worker.

Simulates what happens when Wisp speaks a reply: spawn the actual
``runtime.workers.audio_host`` subprocess, prewarm it like the supervisor does
at startup, synthesize a sentence with the user's configured provider, then
verify the WAV is real speech-like audio (non-silent, sane duration) and play
it through the real output device (``--no-play`` for silent machines/CI).

Provider choice: the configured TTS_PROVIDER; if TTS is off but Kokoro is
installed, Kokoro is tested anyway (that is the feature the user would enable).
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _lab

_lab.bootstrap()

TEXT = "Wisp test lab is checking speech output."
MIN_SECONDS = 0.4
MIN_PEAK = 0.02  # int16 fraction; silence-detector threshold


def _wav_stats(path: Path) -> dict:
    """Duration + peak amplitude of a 16-bit mono WAV without numpy."""
    import array

    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        frames = wf.getnframes()
        raw = wf.readframes(frames)
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    peak = max((abs(s) for s in samples), default=0) / 32768.0
    return {"seconds": round(frames / float(rate or 1), 2), "peak": round(peak, 4), "rate": rate}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-play", action="store_true", help="skip real device playback")
    args = parser.parse_args()

    import config
    from core import tts
    from runtime.supervisor.ipc import WorkerClient, WorkerSpec

    configured = str(getattr(config, "TTS_PROVIDER", "none") or "none").strip().lower()
    kokoro_ok = False
    try:
        kokoro_ok = bool(tts.kokoro_installed())
    except Exception as exc:  # noqa: BLE001
        _lab.log(f"kokoro_installed probe failed: {type(exc).__name__}: {exc}")
    provider = configured
    env_extra: dict[str, str] = {}
    if configured in ("", "none"):
        if not kokoro_ok:
            return _lab.finish(
                _lab.SKIP,
                "TTS_PROVIDER=none and Kokoro is not installed - nothing to test "
                "(install_tts covers the install path)",
            )
        provider = "kokoro"
        env_extra["TTS_PROVIDER"] = "kokoro"
        _lab.log("TTS is off in settings; testing installed Kokoro anyway")
    _lab.log(f"provider under test: {provider} (configured: {configured}, kokoro_installed: {kokoro_ok})")

    isolated_root = _lab.isolated_repo_root("tts_function")
    worker = WorkerClient(
        WorkerSpec(
            "lab-audio",
            "runtime.workers.audio_host",
            "audio",
            env=_lab.env_overrides(
                isolated_root=isolated_root,
                extra={"WISP_MACOS_ENABLE_AUDIO": "1", **env_extra},
            ),
        )
    )
    warmup_events: list[str] = []
    watch = _lab.Stopwatch()
    try:
        ping = worker.call("audio.ping", {"value": "lab"}, timeout=60.0)
        _lab.log(f"audio worker up after {watch.lap()}s: pid={ping.get('pid') if isinstance(ping, dict) else ping}")

        worker.on_event("audio.warmup.progress", lambda data, _rid: warmup_events.append(str(data)))
        prewarm = worker.call("audio.prewarm", {}, timeout=420.0)
        _lab.log(f"audio.prewarm after {watch.lap()}s: {prewarm}")
        prewarm_errors = [
            f"{key}={value}"
            for key, value in (prewarm or {}).items()
            if isinstance(value, str) and value.startswith("error:")
        ]
        if prewarm_errors:
            return _lab.finish(_lab.FAIL, "audio prewarm reported errors: " + "; ".join(prewarm_errors))

        synth = worker.call("audio.tts.synthesize", {"text": TEXT}, timeout=300.0)
        elapsed = watch.lap()
        _lab.log(f"audio.tts.synthesize after {elapsed}s: {synth}")
        if not isinstance(synth, dict) or int(synth.get("bytes") or 0) <= 0:
            return _lab.finish(_lab.FAIL, f"synthesize returned no audio bytes: {synth!r}")

        wav_path = Path(str(synth.get("path") or ""))
        if not wav_path.is_file():
            return _lab.finish(_lab.FAIL, f"synthesize reported a missing WAV: {wav_path}")
        stats = _wav_stats(wav_path)
        _lab.log(f"wav stats: {stats}")
        if stats["seconds"] < MIN_SECONDS:
            return _lab.finish(_lab.FAIL, f"audio too short for the sentence: {stats['seconds']}s")
        if stats["peak"] < MIN_PEAK:
            return _lab.finish(
                _lab.FAIL,
                f"audio is silence (peak {stats['peak']}) - the classic 'TTS OK but plays silence' failure",
            )

        played = "skipped (--no-play)"
        if not args.no_play:
            playback_events: list[str] = []
            worker.on_event("audio.playback.started", lambda data, _rid: playback_events.append("started"))
            worker.on_event("audio.playback.done", lambda data, _rid: playback_events.append("done"))
            try:
                play = worker.call(
                    "audio.play_file",
                    {"path": str(wav_path)},
                    timeout=stats["seconds"] + 60.0,
                )
                _lab.log(f"audio.play_file: {play} events={playback_events}")
                if not (isinstance(play, dict) and play.get("played")):
                    return _lab.finish(_lab.FAIL, f"playback did not complete: {play!r}")
                if "started" not in playback_events or "done" not in playback_events:
                    return _lab.finish(
                        _lab.FAIL,
                        f"playback events missing (got {playback_events}) - UI would never unfreeze",
                    )
                played = "real device"
            except Exception as exc:  # noqa: BLE001
                message = str(exc).lower()
                if "output device" in message or "no default" in message or "portaudio" in message:
                    played = f"skipped (no output device: {exc})"
                    _lab.log(played)
                else:
                    raise

        return _lab.finish(
            _lab.PASS,
            f"{provider} synthesized {stats['seconds']}s of audio (peak {stats['peak']}), playback: {played}",
            provider=provider,
            wav_seconds=stats["seconds"],
            peak=stats["peak"],
            synth_seconds=elapsed,
            playback=played,
            warmup_progress=len(warmup_events),
        )
    finally:
        worker.shutdown()
        _lab.log("audio worker shut down")


if __name__ == "__main__":
    raise SystemExit(main())
