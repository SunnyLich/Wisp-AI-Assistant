"""Shared machinery for the fresh-install checks (install_stt / install_tts).

Simulates "user clicks Install in Settings on a machine without the package":
build the same installer plan Settings writes, run the REAL installer script
(``scripts/optional_tts_installer.py``) in direct mode against an empty scratch
``WISP_OPTIONAL_PACKAGES_DIR``, then verify in a clean subprocess that the
package imports from the scratch dir and real inference runs.

The user's real python_packages dir is never touched. Downloads go through the
normal pip/uv caches, so reruns are much faster than a true fresh machine.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _lab


def _dir_stats(root: Path) -> tuple[float, int]:
    total = 0
    count = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
                count += 1
            except OSError:
                continue
    return round(total / (1024 * 1024), 1), count


def _run_streamed(cmd: list[str], env: dict[str, str], label: str) -> int:
    _lab.log(f"[{label}] running: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=_lab.REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        _lab.log(f"[{label}] {line.rstrip()}")
    return proc.wait()


def _kokoro_install_plan(optional_deps, device: str) -> tuple[dict[str, object], str, str]:
    """Build the Settings-equivalent Kokoro plan and inference device."""
    selected = str(device or "auto").strip().lower()
    mode = optional_deps.kokoro_install_mode_for_device(selected)
    require_gpu = selected == "cuda"
    plan: dict[str, object] = {
        "display_name": "Kokoro",
        "packages": optional_deps.kokoro_install_packages(selected),
        "pre_install_packages": optional_deps.kokoro_torch_install_packages(selected),
        "remove_artifacts": optional_deps.kokoro_remove_artifacts(),
        "post_install": "kokoro_prepare",
        "kokoro_voice": "af_heart",
        # Auto provisions the CUDA-capable wheel but must still accept CPU
        # fallback on machines without an NVIDIA driver.
        "kokoro_require_gpu": require_gpu,
        "kokoro_install_device": "cuda" if mode == "gpu" else "cpu",
    }
    verify_device = "cuda" if require_gpu else ("cpu" if selected == "cpu" else "auto")
    return plan, verify_device, mode


def run_install_check(kind: str, *, device: str = "auto", keep: bool = False) -> int:
    """Run the full fresh-install check for ``kind`` ("stt" or "kokoro")."""
    _lab.bootstrap()
    from core import optional_deps

    scratch = _lab.scratch_dir(f"install_{kind}")
    pkgs_dir = scratch / "python_packages"
    pkgs_dir.mkdir(parents=True, exist_ok=True)
    log_path = scratch / "installer.log"
    status_path = scratch / "installer.status.json"
    env = _lab.child_env(extra={"WISP_OPTIONAL_PACKAGES_DIR": str(pkgs_dir)})

    if kind == "stt":
        plan = {
            "display_name": "STT",
            "packages": optional_deps.stt_install_packages(),
            "pre_install_packages": [],
            "remove_artifacts": optional_deps.stt_remove_artifacts(),
            "post_install": "stt_prepare",
        }
        verify_device = "auto"
    elif kind == "kokoro":
        plan, verify_device, mode = _kokoro_install_plan(optional_deps, device)
        _lab.log(f"kokoro install mode: {mode} (requested device: {device})")
    else:
        return _lab.finish(_lab.FAIL, f"unknown install kind {kind!r}")

    plan.update(
        {
            "reinstall": False,
            "restart_apply": False,  # direct mode: Wisp is not running in the scratch world
            "log_path": str(log_path),
            "status_path": str(status_path),
            "app_language": "en",
        }
    )
    plan_path = scratch / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    _lab.log(f"plan: packages={plan['packages']}")
    if plan["pre_install_packages"]:
        _lab.log(f"plan: pre_install_packages={plan['pre_install_packages']}")

    watch = _lab.Stopwatch()
    code = _run_streamed(
        [sys.executable, str(_lab.REPO_ROOT / "scripts" / "optional_tts_installer.py"), "--plan", str(plan_path)],
        env,
        "installer",
    )
    install_seconds = watch.lap()
    status: dict = {}
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    _lab.log(f"installer exit={code} after {install_seconds}s; status={status}")
    if code != 0 or not status.get("ok"):
        return _lab.finish(
            _lab.FAIL,
            f"installer failed (exit {code}): {status.get('message') or 'no status written'}",
        )
    size_mb, files = _dir_stats(pkgs_dir)
    _lab.log(f"scratch install: {size_mb} MB in {files} files")

    # Inference verification in a clean subprocess that only sees the scratch
    # dir, using the same loaders the app uses. Import origins are printed and
    # checked so a package leaking in from elsewhere fails loudly.
    if kind == "stt":
        # Mirror core.macos_helper.handlers._get_model exactly: the user's
        # configured STT_DEVICE / STT_COMPUTE_TYPE drive the load (an invented
        # compute value here once diverged from the app and false-failed).
        verify_code = textwrap.dedent(
            f"""
            import config
            from core import optional_deps
            optional_deps.add_optional_packages_to_path(prepend=True)
            import faster_whisper
            origin = faster_whisper.__file__
            print("faster_whisper origin:", origin)
            assert r"{pkgs_dir}" in origin, "faster_whisper did not come from the scratch install"
            import numpy as np
            from core.stt_device import resolve_device, resolve_compute_type, build_model
            from faster_whisper import WhisperModel
            device = resolve_device(config.STT_DEVICE, log=print)
            compute = resolve_compute_type(device, config.STT_COMPUTE_TYPE, log=print)
            model, device, compute = build_model(WhisperModel, "tiny", device, compute, log=print)
            noise = (np.random.default_rng(0).standard_normal(16000).astype("float32")) * 0.001
            segments, _info = model.transcribe(noise, beam_size=1, vad_filter=True)
            list(segments)
            print("VERIFY_OK device=%s compute=%s" % (device, compute))
            """
        )
    else:
        verify_code = textwrap.dedent(
            f"""
            import config
            from core import optional_deps
            optional_deps.add_optional_packages_to_path(prepend=True)
            import kokoro
            origin = kokoro.__file__
            print("kokoro origin:", origin)
            assert r"{pkgs_dir}" in origin, "kokoro did not come from the scratch install"
            import torch
            print("torch origin:", torch.__file__)
            assert r"{pkgs_dir}" in torch.__file__, "torch did not come from the scratch install"
            print("torch cuda available:", torch.cuda.is_available())
            from core import tts
            ok, message = tts.test_connection(
                "kokoro",
                kokoro_voice="af_heart",
                kokoro_lang_code="a",
                kokoro_device="{verify_device}",
            )
            print("test_connection:", ok, message)
            assert ok, message
            print("VERIFY_OK")
            """
        )
    code = _run_streamed([sys.executable, "-c", verify_code], env, "verify")
    verify_seconds = watch.lap() - install_seconds
    if code != 0:
        return _lab.finish(
            _lab.FAIL,
            f"install succeeded but inference verification failed (exit {code}) - see log",
        )

    if not keep:
        _lab.log("removing scratch install (pass --keep to keep it)")
        shutil.rmtree(pkgs_dir, ignore_errors=True)

    return _lab.finish(
        _lab.PASS,
        f"fresh {kind} install ok: {size_mb} MB / {files} files in {install_seconds}s, "
        f"inference verified in {round(verify_seconds, 1)}s",
        size_mb=size_mb,
        files=files,
        install_seconds=install_seconds,
        verify_seconds=round(verify_seconds, 1),
        message=str(status.get("message") or ""),
    )
