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
