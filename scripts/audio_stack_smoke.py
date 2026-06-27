"""Progressive Kokoro/Whisper audio stack smoke test.

Runs each rung in a subprocess with a timeout so a native-library hang is
reported at the rung where it happens instead of freezing the whole diagnostic.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_ROOT = REPO_ROOT / "build_logs" / "audio-stack-smoke"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _json_default(value: Any) -> str:
    return str(value)


def _print_json(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=_json_default), flush=True)


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("WISP_RUN_LOG_DIR", str(LOG_ROOT))
    env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) if not existing_pythonpath else str(REPO_ROOT) + os.pathsep + existing_pythonpath
    return env


def _run_child(case: str, timeout: float) -> dict[str, Any]:
    env = _base_env()
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--case", case],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return {
        "case": case,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "seconds": round(time.monotonic() - started, 2),
        "stdout": proc.stdout.strip(),
        "stderr_tail": proc.stderr.strip()[-4000:],
    }


def _child_case_env_probe() -> None:
    import config

    result: dict[str, Any] = {
        "tts_provider": getattr(config, "TTS_PROVIDER", ""),
        "kokoro_voice": getattr(config, "KOKORO_VOICE", ""),
        "kokoro_lang": getattr(config, "KOKORO_LANG_CODE", ""),
        "kokoro_device": getattr(config, "KOKORO_DEVICE", ""),
        "stt_model": getattr(config, "STT_MODEL", ""),
        "stt_device": getattr(config, "STT_DEVICE", ""),
        "stt_compute_type": getattr(config, "STT_COMPUTE_TYPE", ""),
    }
    try:
        from core import tts

        result["kokoro_installed"] = tts.kokoro_installed()
    except Exception as exc:  # noqa: BLE001
        result["kokoro_installed_error"] = f"{type(exc).__name__}: {exc}"
    try:
        import faster_whisper  # type: ignore

        result["faster_whisper"] = getattr(faster_whisper, "__version__", "installed")
    except Exception as exc:  # noqa: BLE001
        result["faster_whisper_error"] = f"{type(exc).__name__}: {exc}"
    try:
        import torch  # type: ignore

        result["torch"] = getattr(torch, "__version__", "unknown")
        result["torch_cuda_available"] = bool(torch.cuda.is_available())
        result["torch_cuda_name"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else ""
    except Exception as exc:  # noqa: BLE001
        result["torch_error"] = f"{type(exc).__name__}: {exc}"
    try:
        from ctranslate2 import get_cuda_device_count  # type: ignore

        result["ctranslate2_cuda_devices"] = int(get_cuda_device_count())
    except Exception as exc:  # noqa: BLE001
        result["ctranslate2_error"] = f"{type(exc).__name__}: {exc}"
    _print_json(result)


def _kokoro_test(device: str) -> None:
    import config
    from core import tts

    config.TTS_PROVIDER = "kokoro"
    ok, message = tts.test_connection(
        "kokoro",
        kokoro_voice=getattr(config, "KOKORO_VOICE", "af_heart"),
        kokoro_lang_code=getattr(config, "KOKORO_LANG_CODE", "a"),
        kokoro_device=device,
    )
    result = {"ok": ok, "message": message, "device": device}
    _print_json(result)
    if not ok:
        raise RuntimeError(message)


def _child_case_kokoro_direct_cpu() -> None:
    _kokoro_test("cpu")


def _child_case_kokoro_direct_current() -> None:
    import config

    _kokoro_test(getattr(config, "KOKORO_DEVICE", "auto") or "auto")


def _child_case_kokoro_audio_host_cpu() -> None:
    import config
    from runtime.workers import audio_host

    config.TTS_PROVIDER = "kokoro"
    config.KOKORO_DEVICE = "cpu"
    audio_host._set_local_tts_warmup(warming=False, ready=False)
    result = audio_host.tts_synthesize("hello from wisp audio smoke")
    _print_json(result)
    if int(result.get("bytes") or 0) <= 0:
        raise RuntimeError("audio_host.tts_synthesize returned no audio bytes")


def _child_case_whisper_selftest() -> None:
    from core.macos_helper import handlers

    handlers.stt_reset_model()
    result = handlers.stt_selftest(seconds=0.5)
    _print_json(result)


def _child_case_audio_host_prewarm_cpu() -> None:
    import config
    from runtime.workers import audio_host

    config.TTS_PROVIDER = "kokoro"
    config.KOKORO_DEVICE = "cpu"
    events: list[tuple[str, Any]] = []
    audio_host.set_event_sink(lambda name, data, _req_id: events.append((name, data)))
    result = audio_host.audio_prewarm()
    _print_json({"result": result, "events": events})
    errors = [value for value in result.values() if str(value).startswith("error:")]
    if errors:
        raise RuntimeError("; ".join(str(value) for value in errors))


def _reader_thread(stream, out_queue: queue.Queue[tuple[str, str]], name: str) -> None:
    for line in iter(stream.readline, ""):
        out_queue.put((name, line.rstrip()))


def _call_worker(proc: subprocess.Popen[str], out_queue: queue.Queue[tuple[str, str]], req_id: int, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps({"id": req_id, "method": method, "params": params}) + "\n")
    proc.stdin.flush()
    deadline = time.monotonic() + timeout
    events: list[dict[str, Any]] = []
    stderr: list[str] = []
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"audio worker exited with code {proc.returncode}; stderr={stderr[-20:]}")
        try:
            source, line = out_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if source == "stderr":
            stderr.append(line)
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("event"):
            events.append(message)
            continue
        if message.get("id") == req_id:
            message["_events"] = events
            message["_stderr_tail"] = stderr[-20:]
            return message
    raise TimeoutError(f"timed out waiting for worker method {method!r}; stderr={stderr[-20:]}; events={events[-10:]}")


def _spawn_audio_worker(env_updates: dict[str, str] | None = None) -> tuple[subprocess.Popen[str], queue.Queue[tuple[str, str]]]:
    env = _base_env()
    if env_updates:
        env.update(env_updates)
    proc = subprocess.Popen(
        [sys.executable, "-m", "runtime.workers.audio_host"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    out_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    assert proc.stdout is not None and proc.stderr is not None
    threading.Thread(target=_reader_thread, args=(proc.stdout, out_queue, "stdout"), daemon=True).start()
    threading.Thread(target=_reader_thread, args=(proc.stderr, out_queue, "stderr"), daemon=True).start()
    return proc, out_queue


def _shutdown_worker(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None and proc.stdin is not None:
        try:
            proc.stdin.write(json.dumps({"id": 999, "method": "__shutdown__", "params": {}}) + "\n")
            proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _child_case_audio_worker_ipc_basic() -> None:
    proc, out_queue = _spawn_audio_worker()
    try:
        ping = _call_worker(proc, out_queue, 1, "ping", {}, 15.0)
        speed = _call_worker(proc, out_queue, 2, "audio.speed_boost", {"enabled": True}, 15.0)
        _print_json({"ping": ping, "speed_boost": speed})
        if not ping.get("ok") or not speed.get("ok"):
            raise RuntimeError("basic worker IPC call failed")
    finally:
        _shutdown_worker(proc)


def _child_case_audio_worker_ipc_tts_none() -> None:
    proc, out_queue = _spawn_audio_worker({"TTS_PROVIDER": "none"})
    try:
        ping = _call_worker(proc, out_queue, 1, "ping", {}, 15.0)
        synth = _call_worker(
            proc,
            out_queue,
            2,
            "audio.tts.synthesize",
            {"text": "hello from disabled tts worker smoke"},
            30.0,
        )
        _print_json({"ping": ping, "synthesize": synth})
        if not synth.get("ok"):
            raise RuntimeError(str(synth.get("error") or "worker disabled synth failed"))
    finally:
        _shutdown_worker(proc)


def _child_case_audio_worker_ipc_tts_cpu() -> None:
    proc, out_queue = _spawn_audio_worker({"TTS_PROVIDER": "kokoro", "KOKORO_DEVICE": "cpu"})
    try:
        ping = _call_worker(proc, out_queue, 1, "ping", {}, 15.0)
        synth = _call_worker(
            proc,
            out_queue,
            2,
            "audio.tts.synthesize",
            {"text": "hello from wisp worker smoke"},
            120.0,
        )
        _print_json({"ping": ping, "synthesize": synth})
        if not synth.get("ok"):
            raise RuntimeError(str(synth.get("error") or "worker synth failed"))
        result = synth.get("result") or {}
        if int(result.get("bytes") or 0) <= 0:
            raise RuntimeError("worker synth returned no audio bytes")
    finally:
        _shutdown_worker(proc)


CASES: dict[str, Callable[[], None]] = {
    "env_probe": _child_case_env_probe,
    "kokoro_direct_cpu": _child_case_kokoro_direct_cpu,
    "kokoro_direct_current": _child_case_kokoro_direct_current,
    "kokoro_audio_host_cpu": _child_case_kokoro_audio_host_cpu,
    "whisper_selftest": _child_case_whisper_selftest,
    "audio_host_prewarm_cpu": _child_case_audio_host_prewarm_cpu,
    "audio_worker_ipc_basic": _child_case_audio_worker_ipc_basic,
    "audio_worker_ipc_tts_none": _child_case_audio_worker_ipc_tts_none,
    "audio_worker_ipc_tts_cpu": _child_case_audio_worker_ipc_tts_cpu,
}


def _run_ladder(timeout: float) -> int:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    plan = [
        ("env_probe", True),
        ("kokoro_direct_cpu", True),
        ("kokoro_direct_current", True),
        ("whisper_selftest", True),
        ("kokoro_audio_host_cpu", False),
        ("audio_host_prewarm_cpu", False),
        ("audio_worker_ipc_basic", False),
        ("audio_worker_ipc_tts_none", False),
        ("audio_worker_ipc_tts_cpu", False),
    ]
    results: list[dict[str, Any]] = []
    passed: dict[str, bool] = {}
    for case, always_run in plan:
        dependencies_ok = passed.get("kokoro_direct_cpu", False) and passed.get("whisper_selftest", False)
        if not always_run and not dependencies_ok:
            result = {"case": case, "ok": False, "skipped": True, "reason": "requires kokoro_direct_cpu and whisper_selftest"}
        else:
            try:
                result = _run_child(case, timeout)
            except subprocess.TimeoutExpired as exc:
                result = {
                    "case": case,
                    "ok": False,
                    "timeout": True,
                    "seconds": timeout,
                    "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
                    "stderr_tail": ((exc.stderr or "").strip()[-4000:] if isinstance(exc.stderr, str) else ""),
                }
        passed[case] = bool(result.get("ok"))
        results.append(result)
        status = "PASS" if result.get("ok") else ("SKIP" if result.get("skipped") else "FAIL")
        print(f"[{status}] {case} ({result.get('seconds', '-') }s)", flush=True)
        if result.get("stdout"):
            print(result["stdout"], flush=True)
        if result.get("stderr_tail"):
            print("--- stderr tail ---", flush=True)
            print(result["stderr_tail"], flush=True)
    summary_path = LOG_ROOT / "summary.json"
    summary_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nSummary: {summary_path}", flush=True)
    return 0 if all(result.get("ok") or result.get("skipped") for result in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(CASES))
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.case:
        CASES[args.case]()
        return 0
    return _run_ladder(args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
