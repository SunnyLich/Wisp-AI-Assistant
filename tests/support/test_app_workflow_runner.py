"""Tests for the app workflow test runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

from scripts import pytest_temp_cleanup, run_app_workflow_tests


def test_documented_direct_script_entry_point_loads_cleanup_module():
    """Running the script by path must work when the repo root is not on sys.path."""

    script = run_app_workflow_tests.__file__
    completed = subprocess.run(
        [sys.executable, script, "--help"],
        cwd=os.path.dirname(os.path.dirname(script)),
        env={**os.environ, pytest_temp_cleanup.KEEP_TEMP_ENV: "1"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr


def test_strict_log_scan_flags_runtime_diagnostics(tmp_path):
    """Workflow runner fails logs that pytest alone can treat as passed."""
    log = tmp_path / "pytest.log"
    log.write_text(
        "27 passed\n"
        'Could not parse stylesheet of object QPushButton(name = "chatContextChip_browser")\n'
        "[crash] unhandled exception in thread provider-reader:\n"
        "Fatal Python error: finalizing\n",
        encoding="utf-8",
    )

    issues = run_app_workflow_tests._strict_log_issues(log)

    assert "Could not parse stylesheet" in issues
    assert "[crash] unhandled" in issues
    assert "Fatal Python error" in issues


def test_strict_log_scan_allows_expected_workflow_noise(tmp_path):
    """Intentional workflow logs, such as crash-log tests, do not fail the run."""
    log = tmp_path / "pytest.log"
    log.write_text(
        "Wrote Wisp crash log: /tmp/build_logs/wisp_crash_20260621\n"
        "This plugin does not support raise()\n"
        "27 passed\n",
        encoding="utf-8",
    )

    assert run_app_workflow_tests._strict_log_issues(log) == []


def test_pytest_command_enables_startup_faulthandler():
    """Pytest is launched with faulthandler before test imports run."""
    cmd = run_app_workflow_tests._pytest_cmd("/venv/bin/python", "-q")

    assert cmd[:4] == ["/venv/bin/python", "-X", "faulthandler", "-m"]
    assert cmd[4:] == ["pytest", "-q"]


def test_workflow_runner_covers_core_user_workflows():
    """The workflow entrypoint keeps the main user-visible suites together."""
    expected = {
        "tests/test_app_user_workflows.py",
        "tests/test_profile_user_workflows.py",
        "tests/catalog/test_workflow_manifest.py",
        "tests/runtime/test_flows.py",
        "tests/test_error_recommendations.py",
        "tests/test_i18n_catalog_sources.py",
        "tests/test_overlay_bubble_visibility.py",
        "tests/test_query_pipeline.py",
        "tests/test_settings_dialog_controls.py",
        "tests/test_setup_check.py",
    }
    assert expected <= set(run_app_workflow_tests.WORKFLOW_TESTS)


def test_workflow_runner_includes_every_manifest_referenced_file():
    """Trace, acceptance, and interaction mappings execute through the master."""

    root = run_app_workflow_tests._repo_root()
    mapped = set(run_app_workflow_tests._manifest_workflow_test_files(root))
    selected = set(run_app_workflow_tests._workflow_test_files(root))

    assert len(mapped) >= 60
    assert mapped - set(run_app_workflow_tests.APP_ARCHITECTURE_TESTS) <= selected
    assert "tests/test_optional_install_acceptance.py" in selected
    assert "tests/test_diagnostics_acceptance.py" in selected


def test_workflow_runner_executes_every_direct_failure_evidence_node():
    """Direct failure coverage cannot exist only as documentation metadata."""

    root = run_app_workflow_tests._repo_root()
    nodes = run_app_workflow_tests._failure_evidence_nodes(root)
    manifest = json.loads(
        (root / "tests" / "workflows" / "failure_coverage.json").read_text(
            encoding="utf-8"
        )
    )
    expected = {
        node_id.replace("\\", "/")
        for case in manifest["failure_cases"]
        for node_id in case["evidence_node_ids"]
    }

    assert set(nodes) == expected
    assert len(nodes) >= 82
    assert any(node.startswith("tests/integration/brain/") for node in nodes)


def test_workflow_runner_reports_honest_feature_acceptance_counts():
    root = run_app_workflow_tests._repo_root()
    counts = run_app_workflow_tests._feature_acceptance_counts(root)

    assert counts["total"] == 472
    assert counts["accepted"] == counts["total"]
    assert counts["dependency_audited"] == counts["total"]
    assert counts["accepted_interactions"] == 197
    assert counts["accepted_interactions"] == counts["declared_interactions"]
    assert counts["complete"] is True


def test_pytest_preflight_reports_missing_project_venv(tmp_path, monkeypatch):
    """Missing pytest is explained as setup/venv work, not a raw import crash."""
    monkeypatch.setattr(
        run_app_workflow_tests.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="ModuleNotFoundError: pytest"),
    )

    message = run_app_workflow_tests._pytest_preflight_message("/usr/bin/python3", tmp_path, {})

    assert "No project virtualenv was found" in message
    assert "setup_dev" in message
    assert "ModuleNotFoundError: pytest" in message


def test_pytest_preflight_reports_incomplete_project_venv(tmp_path, monkeypatch):
    """An existing .venv without pytest gets a precise setup hint."""
    venv_python = tmp_path / ".venv" / (
        "Scripts/python.exe" if run_app_workflow_tests.os.name == "nt" else "bin/python"
    )
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        run_app_workflow_tests.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="No module named pytest"),
    )

    message = run_app_workflow_tests._pytest_preflight_message(str(venv_python), tmp_path, {})

    assert "pytest is not installed" in message
    assert "setup_dev" in message


def test_setup_command_points_at_platform_dev_setup(tmp_path):
    """The auto-setup command uses the repo's setup_dev entrypoint."""
    cmd = run_app_workflow_tests._setup_command_args(tmp_path)

    assert any("setup_dev" in part for part in cmd)


def test_exit_status_describes_native_crash_codes():
    """Native crash exit codes are translated in summaries."""
    assert "SIGSEGV" in run_app_workflow_tests._describe_exit_status(11)
    assert "SIGSEGV" in run_app_workflow_tests._describe_exit_status(-11)
    assert "SIGABRT" in run_app_workflow_tests._describe_exit_status(6)


def test_log_tail_returns_last_lines(tmp_path):
    """Failure output includes the useful end of the pytest log."""
    log = tmp_path / "pytest.log"
    log.write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")

    tail = run_app_workflow_tests._log_tail(log, max_lines=3)

    assert tail == "line 97\nline 98\nline 99"


def test_all_test_files_returns_sorted_test_modules(tmp_path):
    """Isolated all-tests mode discovers test files deterministically."""
    tests_dir = tmp_path / "tests"
    nested = tests_dir / "runtime"
    nested.mkdir(parents=True)
    (tests_dir / "test_b.py").write_text("", encoding="utf-8")
    (tests_dir / "helper.py").write_text("", encoding="utf-8")
    (nested / "test_a.py").write_text("", encoding="utf-8")

    assert run_app_workflow_tests._all_test_files(tmp_path) == [
        "tests/runtime/test_a.py",
        "tests/test_b.py",
    ]


def test_named_basetemp_is_per_subprocess(tmp_path):
    """Isolated subprocesses get separate temp roots unless one is supplied."""
    args = run_app_workflow_tests._with_named_basetemp(["-q"], tmp_path, "tests/foo.py")

    assert args[:1] == ["-q"]
    assert args[-2] == "--basetemp"
    assert args[-1].endswith("tests_foo_py")
    assert run_app_workflow_tests._with_named_basetemp(
        ["--basetemp", "custom"], tmp_path, "tests/foo.py"
    ) == ["--basetemp", "custom"]


def test_isolated_all_tests_continues_after_failing_files(tmp_path, monkeypatch):
    """macOS isolated mode reports every failing file instead of stopping early."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    calls: list[str] = []

    monkeypatch.setattr(
        run_app_workflow_tests,
        "_all_test_files",
        lambda _root: ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"],
    )

    def fake_run_logged(name, _cmd, *, root, env, log_dir):
        calls.append(name)
        log_path = log_dir / f"{run_app_workflow_tests._safe_name(name)}.log"
        log_path.write_text("pytest output\n", encoding="utf-8")
        return (1 if name.endswith("test_b.py") or name.endswith("test_c.py") else 0), log_path

    monkeypatch.setattr(run_app_workflow_tests, "_run_logged", fake_run_logged)

    summary_lines: list[str] = []
    status, failure_log = run_app_workflow_tests._run_all_tests_isolated(
        python="/venv/bin/python",
        root=tmp_path,
        env={},
        log_dir=log_dir,
        extra=["-q"],
        summary_lines=summary_lines,
    )

    assert status == 1
    assert len(calls) == 3
    assert failure_log.name.endswith("tests_test_b_py.log")
    aggregate = (log_dir / "pytest-main.log").read_text(encoding="utf-8")
    assert "failed_file_count=2" in aggregate
    assert "error_log_count=2" in aggregate
    assert "error_log.1=" in aggregate
    assert "error_log.2=" in aggregate
    assert "tests_test_b_py" in aggregate
    assert "tests_test_c_py" in aggregate
    assert "pytest-main.failed_file_count=2" in summary_lines
    assert "pytest-main.error_log_count=2" in summary_lines
    assert any(line.startswith("pytest-main.error_log.1=") for line in summary_lines)
    assert any(line.startswith("pytest-main.error_log.2=") for line in summary_lines)


def test_isolated_all_tests_fail_fast_stops_at_first_failure(tmp_path, monkeypatch):
    """The explicit fail-fast option preserves the old stop-at-first-file behavior."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    calls: list[str] = []

    monkeypatch.setattr(
        run_app_workflow_tests,
        "_all_test_files",
        lambda _root: ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"],
    )

    def fake_run_logged(name, _cmd, *, root, env, log_dir):
        calls.append(name)
        log_path = log_dir / f"{run_app_workflow_tests._safe_name(name)}.log"
        log_path.write_text("pytest output\n", encoding="utf-8")
        return (1 if name.endswith("test_b.py") else 0), log_path

    monkeypatch.setattr(run_app_workflow_tests, "_run_logged", fake_run_logged)

    summary_lines: list[str] = []
    status, failure_log = run_app_workflow_tests._run_all_tests_isolated(
        python="/venv/bin/python",
        root=tmp_path,
        env={},
        log_dir=log_dir,
        extra=["-q"],
        summary_lines=summary_lines,
        fail_fast=True,
    )

    assert status == 1
    assert len(calls) == 2
    assert failure_log.name.endswith("tests_test_b_py.log")
    aggregate = (log_dir / "pytest-main.log").read_text(encoding="utf-8")
    assert "failed_file_count=1" in aggregate
    assert "error_log_count=1" in aggregate
    assert "error_log.1=" in aggregate
    assert "pytest-main.failed_file_count=1" in summary_lines
    assert "pytest-main.error_log_count=1" in summary_lines
    assert any(line.startswith("pytest-main.error_log.1=") for line in summary_lines)


def test_main_collects_stale_temp_before_and_current_runner_temp_after(monkeypatch):
    """The public master entry point always performs both cleanup passes."""

    calls: list[int | None] = []
    monkeypatch.setattr(run_app_workflow_tests, "_main", lambda _argv: 7)
    monkeypatch.setattr(
        pytest_temp_cleanup,
        "cleanup_stale_owned_basetemps",
        lambda _root, runner_pid=None: calls.append(runner_pid) or [],
    )

    assert run_app_workflow_tests.main(["--example"]) == 7
    assert calls == [None, os.getpid()]
