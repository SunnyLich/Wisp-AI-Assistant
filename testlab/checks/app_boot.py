"""Real app boot + crash watch.

Rung 1: spawn the real four-worker stack (native/ui/brain/audio) exactly as the
supervisor does, ping every worker, check process-boundary isolation, then shut
down and assert every worker exits cleanly (exit code 0 - a nonzero/killed
worker at shutdown is the classic leaked-process / hang class of bug).

Rung 2: launch the REAL app entrypoint (``python -m runtime.supervisor.app``)
with an offscreen UI and an isolated data root, watch its supervisor log until
all workers are up, keep it alive a few seconds, scan for tracebacks/crash
logs, then stop it (SIGTERM on POSIX asserts a clean exit 0; on Windows the
tree is killed after the health checks pass). If another Wisp instance holds
the single-instance lock the rung is skipped with a note.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _lab

_lab.bootstrap()

BOOT_MARKERS = ("starting wisp-native", "starting wisp-ui", "starting wisp-brain", "starting wisp-audio")
WORKER_LOGS = ("wisp-native.stderr.log", "wisp-ui.stderr.log", "wisp-brain.stderr.log", "wisp-audio.stderr.log")
BOOT_TIMEOUT = 120.0
STEADY_SECONDS = 8.0


def _rung1_worker_stack() -> tuple[bool, str]:
    from runtime.supervisor.ipc import WispSupervisor, default_specs

    isolated_root = _lab.isolated_repo_root("app_boot_workers")
    specs = default_specs()
    for spec in specs.values():
        spec.env.update(
            _lab.env_overrides(
                isolated_root=isolated_root,
                offscreen_ui=(spec.role == "ui"),
                extra=dict(spec.env),
            )
        )
    supervisor = WispSupervisor(specs)
    exit_codes: dict[str, int | None] = {}
    watch = _lab.Stopwatch()
    try:
        pings = supervisor.start_all()
        _lab.log(f"all workers pinged after {watch.lap()}s")
        for name, ping in pings.items():
            boundary = (ping or {}).get("boundary") if isinstance(ping, dict) else None
            ok = bool(isinstance(ping, dict) and ping.get("pong"))
            boundary_ok = bool(isinstance(boundary, dict) and boundary.get("ok"))
            loaded = (boundary or {}).get("forbidden_loaded") if isinstance(boundary, dict) else None
            _lab.log(f"  {name}: pong={ok} boundary_ok={boundary_ok} pid={ping.get('pid') if isinstance(ping, dict) else '?'}")
            if not ok:
                return False, f"worker {name} did not answer ping: {ping!r}"
            if not boundary_ok:
                return False, f"worker {name} violated its import boundary (loaded: {loaded})"
        for name, worker in supervisor.workers.items():
            worker.on_exit(lambda code, n=name: exit_codes.setdefault(n, code))
    finally:
        supervisor.shutdown()
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and len(exit_codes) < len(supervisor.workers):
        time.sleep(0.2)
    _lab.log(f"shutdown exit codes after {watch.lap()}s: {exit_codes}")
    missing = [name for name in supervisor.workers if name not in exit_codes]
    if missing:
        return False, f"no exit observed for workers {missing} within 20s of shutdown"
    dirty = {name: code for name, code in exit_codes.items() if code != 0}
    if dirty:
        return False, f"workers exited uncleanly at shutdown: {dirty}"
    return True, f"4 workers up + clean shutdown in {watch.lap()}s"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _isolate_single_instance(env: dict[str, str]) -> str:
    """Sandbox the user-data dir (single-instance lock) so the lab app can boot
    beside a running Wisp. The real optional-packages dir is pinned so STT/TTS
    still count as installed inside the sandbox.
    """
    from core import optional_deps

    env["WISP_OPTIONAL_PACKAGES_DIR"] = str(optional_deps.OPTIONAL_PACKAGES_DIR)
    sandbox = _lab.scratch_dir("app_boot_userdata")
    if sys.platform == "win32":
        env["APPDATA"] = str(sandbox)
        return "sandboxed APPDATA"
    elif sys.platform == "darwin":
        # HOME redirection would move the keychain/HF-cache world; not worth it.
        return "no lock sandbox on macOS (skips if Wisp is running)"
    else:
        env["XDG_CONFIG_HOME"] = str(sandbox)
        return "sandboxed XDG_CONFIG_HOME"


def _rung2_real_app() -> tuple[bool, str]:
    isolated_root = _lab.isolated_repo_root("app_boot_process")
    log_dir = _lab.scratch_dir("app_boot_process_logs")
    env = _lab.child_env(
        isolated_root=isolated_root,
        offscreen_ui=True,
        extra={
            "WISP_RUN_LOG_DIR": str(log_dir),
            "WISP_RUNTIME_LOG_MODE": "debug",
        },
    )
    _lab.log(f"single-instance isolation: {_isolate_single_instance(env)}")
    supervisor_log = log_dir / "supervisor.log"
    popen_kwargs: dict = {}
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [sys.executable, "-m", "runtime.supervisor.app"],
        cwd=_lab.REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **popen_kwargs,
    )
    _lab.log(f"real app started (pid {proc.pid}); watching {supervisor_log}")

    def _kill() -> None:
        if proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            else:
                import signal

                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass

    try:
        deadline = time.monotonic() + BOOT_TIMEOUT
        booted = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                if proc.returncode == 2:
                    return True, "skipped: another Wisp instance is running (single-instance lock)"
                text = _read_text(supervisor_log)
                return False, f"app exited during boot with code {proc.returncode}; log tail: {text[-500:]}"
            text = _read_text(supervisor_log)
            if all(marker in text for marker in BOOT_MARKERS) and all(
                (log_dir / name).exists() for name in WORKER_LOGS
            ):
                booted = True
                break
            time.sleep(1.0)
        if not booted:
            return False, f"app did not finish booting within {int(BOOT_TIMEOUT)}s; log tail: {_read_text(supervisor_log)[-500:]}"
        boot_text = _read_text(supervisor_log)
        _lab.log("all four workers spawned; holding steady-state watch")
        time.sleep(STEADY_SECONDS)
        if proc.poll() is not None:
            return False, f"app died in steady state with code {proc.returncode}"
        text = _read_text(supervisor_log)
        if "Traceback" in text:
            snippet = text[text.index("Traceback"):][:400]
            return False, f"supervisor log has a traceback: {snippet}"
        crash_dirs = list((isolated_root / "build_logs").glob("wisp_crash_*")) if (isolated_root / "build_logs").exists() else []
        if crash_dirs:
            return False, f"crash logs were written during boot: {[d.name for d in crash_dirs]}"
        hotkeys_note = "hotkeys registered"
        if "native hotkeys did not start" in text:
            hotkeys_note = "hotkeys unavailable (likely held by a running Wisp/other app) - non-fatal"
            _lab.log(hotkeys_note)

        if sys.platform == "win32":
            _kill()
            return True, f"real app booted 4 workers and stayed healthy; {hotkeys_note}; (hard-stopped: Windows has no external clean-quit signal)"
        import signal

        proc.send_signal(signal.SIGTERM)
        try:
            code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill()
            return False, "app ignored SIGTERM for 30s (shutdown hang)"
        if code != 0:
            return False, f"app exited with code {code} after SIGTERM (expected clean 0)"
        return True, f"real app booted 4 workers, stayed healthy, exited cleanly on SIGTERM; {hotkeys_note}"
    finally:
        _kill()


def main() -> int:
    ok1, detail1 = _rung1_worker_stack()
    _lab.log(f"rung 1 (worker stack): {'ok' if ok1 else 'FAIL'} - {detail1}")
    if not ok1:
        return _lab.finish(_lab.FAIL, f"worker stack: {detail1}")
    ok2, detail2 = _rung2_real_app()
    _lab.log(f"rung 2 (real app process): {'ok' if ok2 else 'FAIL'} - {detail2}")
    if not ok2:
        return _lab.finish(_lab.FAIL, f"real app process: {detail2}")
    return _lab.finish(_lab.PASS, f"workers: {detail1} | app: {detail2}")


if __name__ == "__main__":
    raise SystemExit(main())
