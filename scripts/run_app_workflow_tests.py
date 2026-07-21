"""Run Wisp user-workflow tests from one entry point.

Default run:
    python scripts/run_app_workflow_tests.py

Live GPT 5.5 run:
    python scripts/run_app_workflow_tests.py --real-gpt55

Real host/native smoke run:
    python scripts/run_app_workflow_tests.py --real-host
    python scripts/run_app_workflow_tests.py --real-host-interactive

Pass extra pytest args after ``--``:
    python scripts/run_app_workflow_tests.py -- -vv -s
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

WORKFLOW_TESTS = (
    "tests/test_app_user_workflows.py",
    "tests/test_profile_user_workflows.py",
    "tests/test_workflow_manifest.py",
    "tests/test_feature_acceptance_manifest.py",
    "tests/test_feature_acceptance_workflows.py",
    "tests/runtime/test_flows.py",
    "tests/test_error_recommendations.py",
    "tests/test_i18n_catalog_sources.py",
    "tests/test_overlay_bubble_visibility.py",
    "tests/test_query_pipeline.py",
    "tests/test_settings_dialog_controls.py",
    "tests/test_setup_check.py",
    "tests/test_real_gpt55_integration.py",
    "tests/test_real_host_native_smoke.py",
)
APP_ARCHITECTURE_TESTS = (
    "tests/runtime/test_supervisor_ipc.py",
)
REAL_HOST_TESTS = ("tests/test_real_host_native_smoke.py",)
LOG_ROOT_NAME = "build_logs"
LATEST_LOG_POINTER = "latest_app_workflow_tests.txt"
LATEST_FAILURE_POINTER = "latest_app_workflow_tests_failure.txt"
STRICT_LOG_PATTERNS = (
    "Could not parse stylesheet",
    "[crash] unhandled",
    "Fatal Python error",
    "Segmentation fault",
    "Abort trap",
    "SIGTRAP",
)
FAILURE_TAIL_LINES = 80


def _manifest_workflow_test_files(root: Path) -> tuple[str, ...]:
    """Return every test file referenced by trace and acceptance manifests."""

    files: set[str] = set()
    manifest_records = (
        ("manifest.json", "workflows"),
        ("feature_acceptance.json", "records"),
        ("feature_interactions.json", "interactions"),
    )
    for filename, record_key in manifest_records:
        manifest_path = root / "tests" / "workflows" / filename
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        files.update(
            str(node_id).split("::", 1)[0].replace("\\", "/")
            for record in manifest.get(record_key, [])
            for node_id in record.get("test_node_ids", [])
            if "::" in str(node_id)
        )
    return tuple(sorted(files))


def _workflow_test_files(root: Path) -> tuple[str, ...]:
    """Merge the legacy master list with every manifest-referenced test file."""

    architecture = set(APP_ARCHITECTURE_TESTS)
    return tuple(
        path
        for path in dict.fromkeys((*WORKFLOW_TESTS, *_manifest_workflow_test_files(root)))
        if path not in architecture
    )


def _failure_evidence_nodes(root: Path) -> tuple[str, ...]:
    """Return every direct asserting node in the failure-coverage manifest."""

    manifest_path = root / "tests" / "workflows" / "failure_coverage.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ()
    return tuple(
        sorted(
            {
                str(node_id).replace("\\", "/")
                for record in manifest.get("failure_cases", [])
                for node_id in record.get("evidence_node_ids", [])
            }
        )
    )


def _feature_acceptance_counts(root: Path) -> dict[str, int | bool]:
    """Return honest positive feature and interaction counts for summaries."""

    acceptance_path = root / "tests" / "workflows" / "feature_acceptance.json"
    interactions_path = root / "tests" / "workflows" / "feature_interactions.json"
    try:
        acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        acceptance = {"records": []}
    try:
        interactions = json.loads(interactions_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        interactions = {"interactions": []}
    records = acceptance.get("records", [])
    accepted = sum(1 for row in records if row.get("acceptance_status") == "real_entry_accepted")
    dependency_audited = sum(1 for row in records if row.get("dependency_status") == "audited")
    return {
        "total": len(records),
        "accepted": accepted,
        "component_only": sum(1 for row in records if row.get("acceptance_status") == "component_only"),
        "candidates": sum(1 for row in records if row.get("acceptance_status") == "candidate_needs_audit"),
        "untested": sum(1 for row in records if row.get("acceptance_status") == "untested"),
        "dependency_audited": dependency_audited,
        "accepted_interactions": sum(1 for row in interactions.get("interactions", []) if row.get("status") == "accepted"),
        "complete": len(records) == 472 and accepted == 472 and dependency_audited == 472,
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name)


def _make_log_dir(root: Path) -> Path:
    log_root = root / LOG_ROOT_NAME
    log_dir = log_root / f"app_workflow_tests_{_timestamp()}_{os.getpid()}"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_root / LATEST_LOG_POINTER).write_text(str(log_dir), encoding="utf-8")
    return log_dir


def _preferred_python(root: Path) -> str:
    if os.name == "nt":
        candidate = root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = root / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _setup_command() -> str:
    """Return the platform setup command for a missing/incomplete test venv."""
    if os.name == "nt":
        return r".\scripts\setup_dev.ps1"
    return "bash scripts/setup_dev.sh"


def _setup_command_args(root: Path) -> list[str]:
    """Return a subprocess command that provisions the developer environment."""
    if os.name == "nt":
        return [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts" / "setup_dev.ps1"),
        ]
    return ["bash", str(root / "scripts" / "setup_dev.sh")]


def _pytest_preflight_message(python: str, root: Path, env: dict[str, str]) -> str:
    """Return a user-facing error when the selected Python cannot import pytest."""
    try:
        proc = subprocess.run(
            [python, "-c", "import pytest"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - preflight should explain any launch failure
        proc = None
        detail = f"{type(exc).__name__}: {exc}"
    else:
        if proc.returncode == 0:
            return ""
        detail = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()

    venv_python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not venv_python.exists():
        reason = "No project virtualenv was found at .venv."
    elif Path(python) == venv_python:
        reason = "The project .venv exists, but pytest is not installed in it."
    else:
        reason = "The runner fell back to the current Python because the project .venv was not usable."
    return (
        "pytest is not available for the Python selected by the workflow runner.\n"
        f"Selected Python: {python}\n"
        f"{reason}\n"
        f"Import error: {detail}\n\n"
        f"Run setup first:\n  {_setup_command()}\n\n"
        "Then rerun the workflow tests."
    )


def _normalize_pytest_args(raw: list[str]) -> list[str]:
    if raw and raw[0] == "--":
        return raw[1:]
    return raw


def _with_default_basetemp(args: list[str], root: Path) -> list[str]:
    if any(arg == "--basetemp" or arg.startswith("--basetemp=") for arg in args):
        return args
    suffix = f"{os.getpid()}_{int(time.time() * 1000)}"
    basetemp = root / ".tmp_pytest" / f"app_workflows_{suffix}"
    basetemp.parent.mkdir(parents=True, exist_ok=True)
    return [*args, "--basetemp", str(basetemp)]


def _with_cache_disabled(args: list[str]) -> list[str]:
    if "cacheprovider" in " ".join(args):
        return args
    return ["-p", "no:cacheprovider", *args]


def _with_named_basetemp(args: list[str], root: Path, name: str) -> list[str]:
    """Add a stable per-subprocess basetemp unless the caller supplied one."""
    if any(arg == "--basetemp" or arg.startswith("--basetemp=") for arg in args):
        return args
    basetemp = root / ".tmp_pytest" / _safe_name(name)
    basetemp.parent.mkdir(parents=True, exist_ok=True)
    return [*args, "--basetemp", str(basetemp)]


def _all_test_files(root: Path) -> list[str]:
    """Return pytest test files for isolated full-suite execution."""
    test_root = root / "tests"
    return sorted(
        path.relative_to(root).as_posix()
        for path in test_root.rglob("test*.py")
        if path.is_file()
    )


def _write_failure_pointer(root: Path, log_path: Path) -> None:
    log_root = root / LOG_ROOT_NAME
    log_root.mkdir(parents=True, exist_ok=True)
    (log_root / LATEST_FAILURE_POINTER).write_text(str(log_path), encoding="utf-8")


def _run_logged(name: str, cmd: list[str], *, root: Path, env: dict[str, str], log_dir: Path) -> tuple[int, Path]:
    log_path = log_dir / f"{_safe_name(name)}.log"
    print("Running:", " ".join(cmd), flush=True)
    print("Log:", log_path, flush=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"name={name}\n")
        log.write(f"cwd={root}\n")
        log.write(f"command={' '.join(cmd)}\n\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log.write(line)
        status = proc.wait()
        log.write(f"\nexit_code={status}\n")
    return status, log_path


def _pytest_cmd(python: str, *args: str) -> list[str]:
    """Build a pytest command with startup faulthandler enabled."""
    return [python, "-X", "faulthandler", "-m", "pytest", *args]


def _describe_exit_status(status: int) -> str:
    """Return a human-readable process exit status."""
    if status == 0:
        return "0"
    signum = -status if status < 0 else status
    if signum in {getattr(signal, "SIGSEGV", object()), 11}:
        return f"{status} (native crash: SIGSEGV/segmentation fault)"
    if signum in {getattr(signal, "SIGABRT", object()), 6}:
        return f"{status} (native abort: SIGABRT)"
    if signum in {getattr(signal, "SIGTRAP", object()), 5}:
        return f"{status} (native trap: SIGTRAP)"
    return str(status)


def _log_tail(log_path: Path, *, max_lines: int = FAILURE_TAIL_LINES) -> str:
    """Return the last lines of a log file for terminal failure output."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"(could not read log tail: {exc})"
    return "\n".join(lines[-max_lines:])


def _print_failure_tail(log_path: Path) -> None:
    """Print a compact failure tail so users do not need to open summary.txt."""
    print(f"Last {FAILURE_TAIL_LINES} log lines:", flush=True)
    tail = _log_tail(log_path)
    if tail:
        print(tail, flush=True)
    else:
        print("(log is empty)", flush=True)


def _strict_log_issues(log_path: Path) -> list[str]:
    """Return serious runtime diagnostics that should fail a workflow run."""
    issues: list[str] = []
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"could not read log for strict scan: {exc}"]
    for pattern in STRICT_LOG_PATTERNS:
        if pattern in text:
            issues.append(pattern)
    return issues


def _append_log_issues(summary_lines: list[str], name: str, log_path: Path) -> list[str]:
    issues = _strict_log_issues(log_path)
    if issues:
        summary_lines.append(f"{name}.strict_log_issues={', '.join(issues)}")
        print(
            f"Strict log scan failed for {name}: {', '.join(issues)}",
            flush=True,
        )
        _write_failure_pointer(_repo_root(), log_path)
    return issues


def _run_all_tests_isolated(
    *,
    python: str,
    root: Path,
    env: dict[str, str],
    log_dir: Path,
    extra: list[str],
    summary_lines: list[str],
    fail_fast: bool = False,
) -> tuple[int, Path]:
    """Run every test file in a separate process to isolate native crashes."""
    test_files = _all_test_files(root)
    aggregate_log = log_dir / "pytest-main.log"
    failures: list[tuple[str, int, Path]] = []
    aggregate_lines = [
        "name=pytest-main",
        f"cwd={root}",
        "mode=isolated-per-file",
        f"fail_fast={str(bool(fail_fast)).lower()}",
        f"test_file_count={len(test_files)}",
        "",
    ]
    summary_lines.append(f"pytest-main.isolated_files={len(test_files)}")
    summary_lines.append(f"pytest-main.fail_fast={str(bool(fail_fast)).lower()}")
    for index, test_file in enumerate(test_files, start=1):
        print(f"Isolated pytest file {index}/{len(test_files)}: {test_file}", flush=True)
        run_name = f"pytest-main-{index:03d}-{test_file}"
        per_extra = _with_named_basetemp(
            extra,
            root,
            f"app_workflows_{os.getpid()}_{index:03d}_{test_file}",
        )
        cmd = _pytest_cmd(python, test_file, *per_extra)
        status, log_path = _run_logged(run_name, cmd, root=root, env=env, log_dir=log_dir)
        issues = _append_log_issues(summary_lines, run_name, log_path)
        if status == 0 and issues:
            status = 1
        aggregate_lines.append(
            f"{run_name}.exit_code={_describe_exit_status(status)}"
        )
        aggregate_lines.append(f"{run_name}.log={log_path}")
        summary_lines.append(f"{run_name}.exit_code={_describe_exit_status(status)}")
        summary_lines.append(f"{run_name}.log={log_path}")
        if status != 0:
            failures.append((run_name, status, log_path))
            if fail_fast:
                aggregate_lines.append("failed_file_count=1")
                aggregate_lines.append(f"failed_file={run_name}")
                aggregate_lines.append(f"failed_file_exit_code={_describe_exit_status(status)}")
                aggregate_lines.append(f"failed_file_log={log_path}")
                aggregate_lines.append("error_log_count=1")
                aggregate_lines.append(f"error_log.1={log_path}")
                summary_lines.append("pytest-main.failed_file_count=1")
                summary_lines.append(f"pytest-main.failed_file={run_name}")
                summary_lines.append(f"pytest-main.failed_file_exit_code={_describe_exit_status(status)}")
                summary_lines.append(f"pytest-main.failed_file_log={log_path}")
                summary_lines.append("pytest-main.error_log_count=1")
                summary_lines.append(f"pytest-main.error_log.1={log_path}")
                aggregate_lines.append(f"failure_log={log_path}")
                aggregate_lines.append(f"exit_code={status}")
                aggregate_log.write_text("\n".join(aggregate_lines) + "\n", encoding="utf-8")
                return status, log_path
    if failures:
        _first_name, first_status, first_log = failures[0]
        aggregate_lines.append(f"failed_file_count={len(failures)}")
        aggregate_lines.append(f"error_log_count={len(failures)}")
        summary_lines.append(f"pytest-main.failed_file_count={len(failures)}")
        summary_lines.append(f"pytest-main.error_log_count={len(failures)}")
        for index, (name, status, log_path) in enumerate(failures, start=1):
            aggregate_lines.append(f"failed_file={name}")
            aggregate_lines.append(f"failed_file_exit_code={_describe_exit_status(status)}")
            aggregate_lines.append(f"failed_file_log={log_path}")
            aggregate_lines.append(f"error_log.{index}={log_path}")
            summary_lines.append(f"pytest-main.failed_file={name}")
            summary_lines.append(f"pytest-main.failed_file_exit_code={_describe_exit_status(status)}")
            summary_lines.append(f"pytest-main.failed_file_log={log_path}")
            summary_lines.append(f"pytest-main.error_log.{index}={log_path}")
        aggregate_lines.append(f"failure_log={first_log}")
        aggregate_lines.append(f"exit_code={first_status}")
        aggregate_log.write_text("\n".join(aggregate_lines) + "\n", encoding="utf-8")
        return first_status, first_log
    aggregate_lines.append("failed_file_count=0")
    aggregate_lines.append("error_log_count=0")
    aggregate_lines.append("exit_code=0")
    summary_lines.append("pytest-main.failed_file_count=0")
    summary_lines.append("pytest-main.error_log_count=0")
    aggregate_log.write_text("\n".join(aggregate_lines) + "\n", encoding="utf-8")
    return 0, aggregate_log


def _run_pytest_phase(
    name: str,
    cmd: list[str],
    *,
    root: Path,
    env: dict[str, str],
    log_dir: Path,
    summary_lines: list[str],
) -> tuple[int, Path]:
    """Run one pytest phase and apply strict runtime log diagnostics."""
    status, log_path = _run_logged(name, cmd, root=root, env=env, log_dir=log_dir)
    issues = _append_log_issues(summary_lines, name, log_path)
    if status == 0 and issues:
        status = 1
    summary_lines.extend(
        [
            f"{name}.exit_code={_describe_exit_status(status)}",
            f"{name}.log={log_path}",
        ]
    )
    return status, log_path


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Wisp app user-workflow test suite.",
    )
    parser.add_argument(
        "--real-gpt55",
        action="store_true",
        help="Enable the opt-in real GPT 5.5 workflow test. This can spend tokens.",
    )
    parser.add_argument(
        "--real-host",
        action="store_true",
        help="Enable opt-in tests that touch the real desktop, clipboard, screenshot, and tray APIs.",
    )
    parser.add_argument(
        "--real-host-interactive",
        action="store_true",
        help="Also enable real host tests that synthesize input into focused test windows.",
    )
    parser.add_argument(
        "--all-tests",
        action="store_true",
        help="Run the full pytest suite instead of only workflow-marked tests.",
    )
    parser.add_argument(
        "--single-process",
        action="store_true",
        help="Run --all-tests in one pytest process instead of macOS-safe per-file subprocesses.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop macOS isolated --all-tests at the first failing test file.",
    )
    parser.add_argument(
        "--no-auto-setup",
        action="store_true",
        help="Do not run scripts/setup_dev.* automatically when pytest is missing.",
    )
    parser.add_argument(
        "--require-complete-acceptance",
        action="store_true",
        help="Fail unless all 472 real-entry features and dependency audits are complete.",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Extra pytest arguments, optionally after --.",
    )
    args = parser.parse_args(argv)

    root = _repo_root()
    log_dir = _make_log_dir(root)
    summary_path = log_dir / "summary.txt"
    print("Test logs:", log_dir, flush=True)
    base_env = os.environ.copy()
    real_host = args.real_host or args.real_host_interactive
    env = base_env.copy()
    env.setdefault("PYTHONFAULTHANDLER", "1")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if args.real_gpt55:
        env["WISP_RUN_REAL_GPT55_TESTS"] = "1"

    extra = _with_cache_disabled(_normalize_pytest_args(args.pytest_args))
    python = _preferred_python(root)
    summary_lines = [
        "Wisp app workflow test run",
        f"started={_dt.datetime.now().isoformat(timespec='seconds')}",
        f"cwd={root}",
        f"python={python}",
        f"args={' '.join(argv if argv is not None else sys.argv[1:])}",
        "",
    ]
    acceptance_counts = _feature_acceptance_counts(root)
    summary_lines.extend(
        [
            f"feature_acceptance.accepted={acceptance_counts['accepted']}/{acceptance_counts['total']}",
            f"feature_acceptance.component_only={acceptance_counts['component_only']}",
            f"feature_acceptance.candidates={acceptance_counts['candidates']}",
            f"feature_acceptance.untested={acceptance_counts['untested']}",
            f"feature_acceptance.dependency_audited={acceptance_counts['dependency_audited']}/{acceptance_counts['total']}",
            f"feature_acceptance.accepted_interactions={acceptance_counts['accepted_interactions']}",
            f"feature_acceptance.complete={str(bool(acceptance_counts['complete'])).lower()}",
            "",
        ]
    )
    if args.require_complete_acceptance and not acceptance_counts["complete"]:
        message = (
            "Real-entry feature acceptance is incomplete: "
            f"{acceptance_counts['accepted']}/{acceptance_counts['total']} functions and "
            f"{acceptance_counts['dependency_audited']}/{acceptance_counts['total']} dependency audits."
        )
        incomplete_log = log_dir / "feature-acceptance-incomplete.log"
        incomplete_log.write_text(message + "\n", encoding="utf-8")
        summary_lines.extend(["final_exit_code=1", f"failure_log={incomplete_log}"])
        _write_failure_pointer(root, incomplete_log)
        summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        print(message, flush=True)
        print("Summary:", summary_path, flush=True)
        return 1
    preflight_message = _pytest_preflight_message(python, root, env)
    if preflight_message and not args.no_auto_setup:
        print("pytest is not available; running developer setup first.", flush=True)
        setup_status, setup_log = _run_logged(
            "setup-dev",
            _setup_command_args(root),
            root=root,
            env=base_env,
            log_dir=log_dir,
        )
        summary_lines.extend(
            [
                f"setup-dev.exit_code={_describe_exit_status(setup_status)}",
                f"setup-dev.log={setup_log}",
            ]
        )
        if setup_status == 0:
            python = _preferred_python(root)
            summary_lines[3] = f"python={python}"
            preflight_message = _pytest_preflight_message(python, root, env)
        else:
            preflight_message = (
                "Developer setup failed, so pytest is still unavailable.\n"
                f"Setup log: {setup_log}\n\n"
                f"Original pytest preflight:\n{preflight_message}"
            )

    if preflight_message:
        preflight_log = log_dir / "pytest-preflight.log"
        preflight_log.write_text(preflight_message + "\n", encoding="utf-8")
        summary_lines.extend(
            [
                "pytest-preflight.exit_code=1",
                f"pytest-preflight.log={preflight_log}",
                "final_exit_code=1",
                f"failure_log={preflight_log}",
            ]
        )
        _write_failure_pointer(root, preflight_log)
        summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        print(preflight_message, flush=True)
        print("Summary:", summary_path, flush=True)
        print("Failure log:", preflight_log, flush=True)
        return 1

    isolate_all_tests = args.all_tests and sys.platform == "darwin" and not args.single_process
    if args.all_tests:
        if isolate_all_tests:
            print(
                "Mode: full pytest suite (--all-tests, isolated per test file on macOS, continuing after failures).",
                flush=True,
            )
            if args.fail_fast:
                print("Fail-fast enabled; stopping at the first failing test file.", flush=True)
        else:
            print("Mode: full pytest suite (--all-tests).", flush=True)
            extra = _with_default_basetemp(extra, root)
            cmd = _pytest_cmd(python, *extra)
    else:
        workflow_test_files = _workflow_test_files(root)
        failure_evidence_nodes = _failure_evidence_nodes(root)
        marker = "workflow and not real_host" if real_host else "workflow"
        print(
            "Mode: app workflow suite "
            f"({len(APP_ARCHITECTURE_TESTS)} app-architecture file + "
            f"{len(failure_evidence_nodes)} direct failure-evidence nodes + "
            f"{len(workflow_test_files)} workflow entry files; use --all-tests for the full pytest suite).",
            flush=True,
        )
        app_arch_extra = _with_named_basetemp(extra, root, f"app_architecture_{os.getpid()}")
        app_arch_cmd = _pytest_cmd(
            python,
            "-m",
            "workflow and not real_host",
            *APP_ARCHITECTURE_TESTS,
            *app_arch_extra,
        )
        status, main_log = _run_pytest_phase(
            "pytest-app-architecture",
            app_arch_cmd,
            root=root,
            env=env,
            log_dir=log_dir,
            summary_lines=summary_lines,
        )
        if status != 0:
            summary_lines.append(f"final_exit_code={_describe_exit_status(status)}")
            summary_lines.append(f"failure_log={main_log}")
            _write_failure_pointer(root, main_log)
            summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
            print("Summary:", summary_path, flush=True)
            print("Failure log:", main_log, flush=True)
            _print_failure_tail(main_log)
            return status
        failure_extra = _with_named_basetemp(extra, root, f"failure_evidence_{os.getpid()}")
        failure_cmd = _pytest_cmd(python, *failure_evidence_nodes, *failure_extra)
        status, failure_log = _run_pytest_phase(
            "pytest-failure-evidence",
            failure_cmd,
            root=root,
            env=env,
            log_dir=log_dir,
            summary_lines=summary_lines,
        )
        if status != 0:
            summary_lines.append(f"final_exit_code={_describe_exit_status(status)}")
            summary_lines.append(f"failure_log={failure_log}")
            _write_failure_pointer(root, failure_log)
            summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
            print("Summary:", summary_path, flush=True)
            print("Failure log:", failure_log, flush=True)
            _print_failure_tail(failure_log)
            return status
        workflow_extra = _with_named_basetemp(extra, root, f"workflow_{os.getpid()}")
        cmd = _pytest_cmd(python, "-m", marker, *workflow_test_files, *workflow_extra)
    if args.real_gpt55:
        print("Real GPT 5.5 workflow test enabled; this may spend tokens.", flush=True)
    if real_host:
        print("Real host/native tests enabled; the second pytest process may touch clipboard, screenshots, tray, and visible windows.", flush=True)
        if sys.platform == "darwin":
            print(
                "macOS grants privacy permissions to the launcher running pytest "
                "(Terminal, Codex, or Python), not automatically to the packaged Wisp app.",
                flush=True,
            )
    if args.real_host_interactive:
        print("Interactive real host tests enabled; keep the machine idle while keyboard/paste checks run.", flush=True)
    if isolate_all_tests:
        status, main_log = _run_all_tests_isolated(
            python=python,
            root=root,
            env=env,
            log_dir=log_dir,
            extra=extra,
            summary_lines=summary_lines,
            fail_fast=args.fail_fast,
        )
    else:
        status, main_log = _run_pytest_phase(
            "pytest-main",
            cmd,
            root=root,
            env=env,
            log_dir=log_dir,
            summary_lines=summary_lines,
        )
    if status != 0 or not real_host:
        summary_lines.append(f"final_exit_code={_describe_exit_status(status)}")
        if status != 0:
            summary_lines.append(f"failure_log={main_log}")
            _write_failure_pointer(root, main_log)
        summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        print("Summary:", summary_path, flush=True)
        if status != 0:
            print("Failure log:", main_log, flush=True)
            _print_failure_tail(main_log)
        return status

    host_env = base_env.copy()
    host_env.setdefault("PYTHONFAULTHANDLER", "1")
    host_env.pop("QT_QPA_PLATFORM", None)
    host_env["WISP_RUN_REAL_HOST_TESTS"] = "1"
    if args.real_host_interactive:
        host_env["WISP_RUN_REAL_HOST_INTERACTIVE_TESTS"] = "1"
    host_extra = _with_cache_disabled(
        _with_default_basetemp(_normalize_pytest_args(args.pytest_args), root)
    )
    host_cmd = _pytest_cmd(python, "-m", "real_host", *REAL_HOST_TESTS, *host_extra)
    host_status, host_log = _run_logged("pytest-real-host", host_cmd, root=root, env=host_env, log_dir=log_dir)
    host_issues = _append_log_issues(summary_lines, "pytest-real-host", host_log)
    if host_status == 0 and host_issues:
        host_status = 1
    summary_lines.extend(
        [
            f"pytest-real-host.exit_code={_describe_exit_status(host_status)}",
            f"pytest-real-host.log={host_log}",
            f"final_exit_code={_describe_exit_status(host_status)}",
        ]
    )
    if host_status != 0:
        summary_lines.append(f"failure_log={host_log}")
        _write_failure_pointer(root, host_log)
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print("Summary:", summary_path, flush=True)
    if host_status != 0:
        print("Failure log:", host_log, flush=True)
        _print_failure_tail(host_log)
    return host_status


def main(argv: list[str] | None = None) -> int:
    """Run the suite and collect dead/current-run repository temp trees."""

    if __package__:
        from scripts.pytest_temp_cleanup import cleanup_stale_owned_basetemps
    else:
        # ``python scripts/run_app_workflow_tests.py`` places only ``scripts``
        # on sys.path.  Keep the documented direct entry point equivalent to
        # ``python -m scripts.run_app_workflow_tests``.
        from pytest_temp_cleanup import cleanup_stale_owned_basetemps

    root = _repo_root()
    cleanup_stale_owned_basetemps(root)
    try:
        return _main(argv)
    finally:
        cleanup_stale_owned_basetemps(root, runner_pid=os.getpid())


if __name__ == "__main__":
    raise SystemExit(main())
