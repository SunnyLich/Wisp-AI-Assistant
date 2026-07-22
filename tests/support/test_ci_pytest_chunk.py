"""Contracts for the complete GitHub-safe pytest chunk runner."""

from __future__ import annotations

from pathlib import Path

from scripts import run_ci_pytest_chunk


def test_file_discovery_uses_the_single_canonical_tests_root(tmp_path: Path) -> None:
    canonical = tmp_path / "tests" / "integration" / "test_a.py"
    old_brain_location = tmp_path / "runtime" / "brain" / "tests" / "test_old.py"
    canonical.parent.mkdir(parents=True)
    old_brain_location.parent.mkdir(parents=True)
    canonical.write_text("def test_a(): pass\n", encoding="utf-8")
    old_brain_location.write_text("def test_old(): pass\n", encoding="utf-8")

    assert run_ci_pytest_chunk._test_files(tmp_path) == [canonical]


def test_github_runner_selects_only_github_safe_tests(tmp_path: Path) -> None:
    test_file = tmp_path / "tests" / "test_example.py"
    command = run_ci_pytest_chunk._pytest_command(
        tmp_path, [test_file], tmp_path / ".pytest-tmp"
    )

    assert any(
        command[index : index + 2] == ["-m", "github_safe"]
        for index in range(len(command) - 1)
    )
    assert "-k" not in command


def test_chunks_partition_every_file_once() -> None:
    files = [Path(f"test_{index}.py") for index in range(11)]
    chunks = [
        run_ci_pytest_chunk._chunk_files(files, chunk_index, 4)
        for chunk_index in range(1, 5)
    ]

    flattened = [path for chunk in chunks for path in chunk]
    assert len(flattened) == len(files)
    assert set(flattened) == set(files)
    assert not any(
        set(left) & set(right)
        for left in chunks
        for right in chunks
        if left is not right
    )
