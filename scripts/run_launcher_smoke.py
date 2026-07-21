"""Run a real Wisp launcher until UI/workers are ready, then audit cleanup."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

EXPECTED_WORKERS = frozenset({"native", "ui", "brain", "audio"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _packaged_executable(root: Path) -> Path:
    if sys.platform == "win32":
        return root / "dist" / "Wisp" / "Wisp.exe"
    if sys.platform == "darwin":
        return root / "dist" / "Wisp.app" / "Contents" / "MacOS" / "Wisp"
    return root / "dist" / "Wisp" / "Wisp"


def _source_command(root: Path) -> list[str]:
    if sys.platform == "win32":
        return ["cmd.exe", "/d", "/c", str(root / "Start Wisp.bat")]
    launcher = root / ("Start Wisp.command" if sys.platform == "darwin" else "Start Wisp.sh")
    return ["bash", str(launcher)]


def _process_alive(pid: int) -> bool:
    try:
        import psutil

        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except ImportError:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    except Exception:
        return False


def _stop_process_tree(process: subprocess.Popen[str]) -> None:
    try:
        import psutil

        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in children:
            child.terminate()
        parent.terminate()
        _gone, alive = psutil.wait_procs([*children, parent], timeout=5)
        for survivor in alive:
            survivor.kill()
        return
    except Exception:
        pass
    try:
        process.kill()
    except OSError:
        pass


def _validate_ready(payload: dict[str, Any], *, frozen: bool) -> list[int]:
    if payload.get("schema_version") != 1 or payload.get("ready") is not True:
        raise RuntimeError(f"invalid readiness payload: {payload!r}")
    if payload.get("frozen") is not frozen:
        raise RuntimeError(f"wrong runtime kind in readiness payload: {payload!r}")
    if payload.get("ui_overlay_shown") is not True or payload.get("flows_started") is not True:
        raise RuntimeError(f"UI/flow startup was not proven: {payload!r}")
    workers = payload.get("workers")
    if not isinstance(workers, dict) or set(workers) != EXPECTED_WORKERS:
        raise RuntimeError(f"expected all four workers, got: {workers!r}")
    pids: list[int] = []
    for name in sorted(EXPECTED_WORKERS):
        row = workers[name]
        if not isinstance(row, dict) or row.get("ping_ok") is not True:
            raise RuntimeError(f"worker {name} did not answer its real ping: {row!r}")
        pid = row.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            raise RuntimeError(f"worker {name} has no real process id: {row!r}")
        pids.append(pid)
    supervisor_pid = payload.get("supervisor_pid")
    if not isinstance(supervisor_pid, int) or supervisor_pid <= 0:
        raise RuntimeError(f"supervisor has no real process id: {payload!r}")
    if len(set([supervisor_pid, *pids])) != len(pids) + 1:
        raise RuntimeError(f"launcher did not create distinct processes: {payload!r}")
    return [supervisor_pid, *pids]


def _state_diagnostics(state: Path) -> str:
    """Return bounded runtime artifacts before the temporary state is removed."""
    sections: list[str] = []
    for path in sorted((state / "data" / "build_logs").rglob("*.log")):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sections.append(f"\n--- {path.relative_to(state)} ---\n{content[-12000:]}")
    return "".join(sections)[-30000:]


def run_launcher_smoke(
    kind: str,
    *,
    root: Path | None = None,
    executable: Path | None = None,
    source_python: Path | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Run one real launcher in isolated state and return readiness evidence."""
    root = (root or _repo_root()).resolve()
    if kind not in {"source", "packaged"}:
        raise ValueError(f"unknown launcher kind: {kind}")
    if kind == "source":
        command = _source_command(root)
        frozen = False
    else:
        executable = (executable or _packaged_executable(root)).resolve()
        if not executable.is_file():
            raise FileNotFoundError(f"packaged Wisp executable not found: {executable}")
        command = [str(executable)]
        frozen = True

    with tempfile.TemporaryDirectory(prefix=f"wisp-{kind}-smoke-") as temporary:
        state = Path(temporary)
        ready_file = state / "ready.json"
        env = os.environ.copy()
        env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "PYTHONPATH": str(root),
                "QT_QPA_PLATFORM": "offscreen",
                "WISP_ADDONS_DIR": str(state / "addons"),
                "WISP_BRAIN_FAKE_LLM": "1",
                "WISP_LAUNCH_SMOKE_DISABLE_AUTOSTART_SYNC": "1",
                "WISP_LAUNCH_SMOKE_EXIT_AFTER_READY": "1",
                "WISP_LAUNCH_SMOKE_READY_FILE": str(ready_file),
                "WISP_MACOS_SAFE_MODE": "1",
                "WISP_DATA_ROOT": str(state / "data"),
                "WISP_RUNTIME_LOG_MODE": "crash",
                "WISP_USER_DATA_DIR": str(state / "user-data"),
            }
        )
        if kind == "source":
            env["WISP_LAUNCH_PYTHON"] = str((source_python or Path(sys.executable)).resolve())
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        process = subprocess.Popen(
            command,
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            start_new_session=sys.platform != "win32",
        )
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _stop_process_tree(process)
            output, _ = process.communicate(timeout=10)
            raise RuntimeError(
                f"{kind} launcher did not reach readiness within {timeout:.0f}s\n{output[-8000:]}"
            ) from exc
        if process.returncode != 0:
            diagnostics = _state_diagnostics(state)
            raise RuntimeError(
                f"{kind} launcher exited {process.returncode}\n{output[-8000:]}{diagnostics}"
            )
        if not ready_file.is_file():
            diagnostics = _state_diagnostics(state)
            raise RuntimeError(
                f"{kind} launcher exited without readiness evidence\n{output[-8000:]}{diagnostics}"
            )
        payload = json.loads(ready_file.read_text(encoding="utf-8"))
        pids = _validate_ready(payload, frozen=frozen)
        deadline = time.monotonic() + 10.0
        survivors = [pid for pid in pids if _process_alive(pid)]
        while survivors and time.monotonic() < deadline:
            time.sleep(0.1)
            survivors = [pid for pid in survivors if _process_alive(pid)]
        if survivors:
            raise RuntimeError(f"{kind} launcher left managed processes running: {survivors}")
        payload["clean_shutdown"] = True
        payload["launcher_kind"] = kind
        return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("source", "packaged"), required=True)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--source-python", type=Path)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)
    payload = run_launcher_smoke(
        args.kind,
        executable=args.executable,
        source_python=args.source_python,
        timeout=args.timeout,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
