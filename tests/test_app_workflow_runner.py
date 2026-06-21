"""Tests for the app workflow test runner."""

from __future__ import annotations

from scripts import run_app_workflow_tests


def test_strict_log_scan_flags_runtime_diagnostics(tmp_path):
    """Workflow runner fails logs that pytest alone can treat as passed."""
    log = tmp_path / "pytest.log"
    log.write_text(
        "27 passed\n"
        'Could not parse stylesheet of object QPushButton(name = "chatContextChip_browser")\n'
        "Fatal Python error: finalizing\n",
        encoding="utf-8",
    )

    issues = run_app_workflow_tests._strict_log_issues(log)

    assert "Could not parse stylesheet" in issues
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
    assert "tests_test_b_py" in aggregate
    assert "tests_test_c_py" in aggregate
    assert "pytest-main.failed_file_count=2" in summary_lines


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

    status, failure_log = run_app_workflow_tests._run_all_tests_isolated(
        python="/venv/bin/python",
        root=tmp_path,
        env={},
        log_dir=log_dir,
        extra=["-q"],
        summary_lines=[],
        fail_fast=True,
    )

    assert status == 1
    assert len(calls) == 2
    assert failure_log.name.endswith("tests_test_b_py.log")
