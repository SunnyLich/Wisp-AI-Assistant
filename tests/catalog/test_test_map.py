"""Regression tests for the authoritative test classification catalogue."""

from __future__ import annotations

from pathlib import Path

from scripts import test_map

ROOT = Path(__file__).resolve().parents[2]


def test_catalogue_is_complete_current_and_scheduled() -> None:
    assert test_map.check_catalog(ROOT) == []


def test_every_discovered_test_file_has_exactly_one_catalogue_entry() -> None:
    discovered = dict(test_map.discover_test_files(ROOT))
    entries = test_map.load_catalog(ROOT)["entries"]
    mapped_paths = [str(entry["path"]) for entry in entries]

    assert len(mapped_paths) == len(set(mapped_paths))
    assert set(mapped_paths) == set(discovered)


def test_every_pytest_entry_has_two_axes_and_an_execution_schedule() -> None:
    entries = test_map.load_catalog(ROOT)["entries"]
    for entry in entries:
        if entry["kind"] != "pytest":
            continue
        assert entry["behaviour"] in test_map.BEHAVIOURS
        assert entry["execution"] in test_map.EXECUTIONS
        assert entry["category"] == test_map.category_for(
            entry["behaviour"], entry["execution"]
        )
        expected_schedule = (
            "github-actions"
            if entry["execution"] == "github_safe"
            else "isolated-host"
        )
        assert entry["schedule"] == expected_schedule
