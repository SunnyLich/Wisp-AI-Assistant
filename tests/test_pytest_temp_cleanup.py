"""Tests for repository-owned pytest basetemp cleanup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import pytest_temp_cleanup


def _config(root: Path, basetemp: Path | str | None) -> SimpleNamespace:
    """Build the minimal pytest config shape used by the cleanup plugin."""

    return SimpleNamespace(rootpath=root, option=SimpleNamespace(basetemp=basetemp))


def test_owned_basetemp_accepts_repo_pytest_temp_child(tmp_path):
    """A named child of .tmp_pytest is safe for automatic deletion."""

    target = tmp_path / ".tmp_pytest" / "focused-run"

    assert pytest_temp_cleanup._owned_basetemp(_config(tmp_path, target)) == target.resolve()


def test_owned_basetemp_accepts_ci_temp_directory(tmp_path):
    """The CI runner's root-level basetemp naming contract is also owned."""

    target = tmp_path / ".pytest-tmp-ci-chunk-2"

    assert pytest_temp_cleanup._owned_basetemp(_config(tmp_path, target)) == target.resolve()


def test_owned_basetemp_rejects_broad_or_external_paths(tmp_path):
    """Cleanup never removes the repository, temp root, or caller-owned path."""

    assert pytest_temp_cleanup._owned_basetemp(_config(tmp_path, tmp_path)) is None
    assert pytest_temp_cleanup._owned_basetemp(_config(tmp_path, tmp_path / ".tmp_pytest")) is None
    assert pytest_temp_cleanup._owned_basetemp(_config(tmp_path, tmp_path / "custom-temp")) is None
    assert pytest_temp_cleanup._owned_basetemp(_config(tmp_path, tmp_path / ".tmp_pytest-escape")) is None


def test_pytest_configure_assigns_owned_temp_to_plain_run(tmp_path):
    """Direct pytest commands avoid the shared operating-system temp root."""

    config = _config(tmp_path, None)

    pytest_temp_cleanup.pytest_configure(config)

    target = pytest_temp_cleanup._owned_basetemp(config)
    assert target is not None
    assert target.parent == (tmp_path / ".tmp_pytest").resolve()


def test_pytest_configure_preserves_caller_basetemp(tmp_path):
    """An explicit caller path remains unchanged and outside cleanup ownership."""

    config = _config(tmp_path, "custom-temp")

    pytest_temp_cleanup.pytest_configure(config)

    assert config.option.basetemp == "custom-temp"


def test_pytest_unconfigure_removes_only_current_owned_basetemp(tmp_path, monkeypatch):
    """One process cleans its own directory without touching sibling runs."""

    monkeypatch.delenv(pytest_temp_cleanup.KEEP_TEMP_ENV, raising=False)
    current = tmp_path / ".tmp_pytest" / "current"
    sibling = tmp_path / ".tmp_pytest" / "other-process"
    current.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (current / "result.txt").write_text("temporary", encoding="utf-8")
    (sibling / "result.txt").write_text("keep", encoding="utf-8")

    pytest_temp_cleanup.pytest_unconfigure(_config(tmp_path, current))

    assert not current.exists()
    assert (sibling / "result.txt").read_text(encoding="utf-8") == "keep"


def test_pytest_unconfigure_preserves_temp_when_requested(tmp_path, monkeypatch):
    """Developers can retain a failing run's files through an environment flag."""

    monkeypatch.setenv(pytest_temp_cleanup.KEEP_TEMP_ENV, "1")
    target = tmp_path / ".tmp_pytest" / "debug-run"
    target.mkdir(parents=True)
    (target / "result.txt").write_text("keep", encoding="utf-8")

    pytest_temp_cleanup.pytest_unconfigure(_config(tmp_path, target))

    assert target.exists()


def test_remove_tree_ignores_a_child_that_vanished(tmp_path, monkeypatch):
    """A concurrently removed fixture does not leave its basetemp behind."""

    target = tmp_path / "run"
    target.mkdir()

    def fake_rmtree(path, *, onexc):
        onexc(Path.unlink, str(path / "already-gone.txt"), FileNotFoundError())
        path.rmdir()

    monkeypatch.setattr(pytest_temp_cleanup.shutil, "rmtree", fake_rmtree)

    pytest_temp_cleanup._remove_tree_with_retries(target)

    assert not target.exists()


def test_stale_cleanup_removes_dead_process_tree_but_preserves_live_sibling(tmp_path, monkeypatch):
    """A later workflow run collects abandoned temp without racing an active run."""

    dead = tmp_path / ".tmp_pytest" / "pytest_1234_100"
    live = tmp_path / ".tmp_pytest" / "pytest_5678_200"
    dead.mkdir(parents=True)
    live.mkdir(parents=True)
    (dead / "leftover.txt").write_text("stale", encoding="utf-8")
    (live / "active.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(pytest_temp_cleanup, "_process_is_running", lambda pid: pid == 5678)

    removed = pytest_temp_cleanup.cleanup_stale_owned_basetemps(tmp_path)

    assert dead in removed
    assert not dead.exists()
    assert (live / "active.txt").read_text(encoding="utf-8") == "keep"


def test_stale_cleanup_removes_completed_current_runner_phases(tmp_path, monkeypatch):
    """Named master-runner phases are owned by the runner after children exit."""

    phase = tmp_path / ".tmp_pytest" / "workflow_4321"
    phase.mkdir(parents=True)
    (phase / "result.txt").write_text("temporary", encoding="utf-8")
    monkeypatch.setattr(pytest_temp_cleanup, "_process_is_running", lambda _pid: True)

    removed = pytest_temp_cleanup.cleanup_stale_owned_basetemps(tmp_path, runner_pid=4321)

    assert phase in removed
    assert not (tmp_path / ".tmp_pytest").exists()


def test_stale_cleanup_honors_debug_retention_flag(tmp_path, monkeypatch):
    """Explicit retention keeps abandoned temp available for debugging."""

    stale = tmp_path / ".tmp_pytest" / "pytest_1234_100"
    stale.mkdir(parents=True)
    monkeypatch.setenv(pytest_temp_cleanup.KEEP_TEMP_ENV, "1")
    monkeypatch.setattr(pytest_temp_cleanup, "_process_is_running", lambda _pid: False)

    assert pytest_temp_cleanup.cleanup_stale_owned_basetemps(tmp_path) == []
    assert stale.exists()
