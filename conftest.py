"""Repository-wide pytest classification and host-safety enforcement."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent
_CATALOG = _ROOT / "tests" / "catalog" / "test_map.json"
_BEHAVIOUR_MARKERS = ("internal", "user_path")
_EXECUTION_MARKERS = ("github_safe", "isolated_host")
_PLATFORM_MARKERS = ("windows", "linux", "macos")


def _current_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _catalogue() -> dict[str, dict[str, object]]:
    try:
        data = json.loads(_CATALOG.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise pytest.UsageError(
            "The test catalogue is missing or invalid. "
            "Run `python scripts/test_map.py` before pytest."
        ) from exc
    return {str(entry["path"]): entry for entry in data.get("entries", [])}


def _relative_test_path(item: pytest.Item) -> str:
    return Path(str(item.path)).resolve().relative_to(_ROOT).as_posix()


def _present_markers(item: pytest.Item, names: tuple[str, ...]) -> list[str]:
    return [name for name in names if item.get_closest_marker(name) is not None]


def pytest_configure(config: pytest.Config) -> None:
    descriptions = {
        "internal": "tests an internal contract rather than a complete user action",
        "user_path": "drives a feature through the same production path a user triggers",
        "github_safe": "safe for an ordinary GitHub-hosted runner",
        "isolated_host": "requires a disposable VM or dedicated isolated test host",
        "windows": "scheduled on Windows",
        "linux": "scheduled on Linux",
        "macos": "scheduled on macOS",
    }
    for marker, description in descriptions.items():
        config.addinivalue_line("markers", f"{marker}: {description}")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Apply exactly two required axes and enforce safe execution routing."""

    del config
    if os.environ.get("WISP_TEST_MAP_GENERATING") == "1":
        return

    entries = _catalogue()
    errors: list[str] = []
    platform = _current_platform()
    isolated = os.environ.get("WISP_ISOLATED_TEST_HOST") == "1"

    for item in items:
        try:
            path = _relative_test_path(item)
        except ValueError:
            errors.append(f"test is outside the repository: {item.nodeid}")
            continue
        entry = entries.get(path)
        if entry is None:
            errors.append(
                f"unclassified test file {path}; run `python scripts/test_map.py` "
                "and review its catalogue entry"
            )
            continue

        expected_behaviour = str(entry.get("behaviour", ""))
        expected_execution = str(entry.get("execution", ""))
        expected_platforms = tuple(str(value) for value in entry.get("platforms", []))
        expected_schedule = str(entry.get("schedule", ""))

        before_behaviour = _present_markers(item, _BEHAVIOUR_MARKERS)
        before_execution = _present_markers(item, _EXECUTION_MARKERS)
        if before_behaviour and before_behaviour != [expected_behaviour]:
            errors.append(
                f"{item.nodeid}: behaviour marker {before_behaviour!r} conflicts "
                f"with catalogue value {expected_behaviour!r}"
            )
        if before_execution and before_execution != [expected_execution]:
            errors.append(
                f"{item.nodeid}: execution marker {before_execution!r} conflicts "
                f"with catalogue value {expected_execution!r}"
            )

        item.add_marker(getattr(pytest.mark, expected_behaviour))
        item.add_marker(getattr(pytest.mark, expected_execution))
        for target in expected_platforms:
            item.add_marker(getattr(pytest.mark, target))

        after_behaviour = _present_markers(item, _BEHAVIOUR_MARKERS)
        after_execution = _present_markers(item, _EXECUTION_MARKERS)
        if len(after_behaviour) != 1 or len(after_execution) != 1:
            errors.append(
                f"{item.nodeid}: expected exactly one behaviour and one execution "
                f"marker, got {after_behaviour!r} and {after_execution!r}"
            )

        if platform not in expected_platforms:
            item.add_marker(
                pytest.mark.skip(
                    reason=f"scheduled for {', '.join(expected_platforms)}, not {platform}"
                )
            )
        if expected_execution == "isolated_host" and not isolated:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "isolated-host safety guard: run only on a disposable VM or "
                        "dedicated host with WISP_ISOLATED_TEST_HOST=1"
                    )
                )
            )
        if expected_execution == "github_safe" and expected_schedule != "github-actions":
            errors.append(f"{path}: github_safe test has no GitHub Actions schedule")
        if expected_execution == "isolated_host" and expected_schedule != "isolated-host":
            errors.append(f"{path}: isolated_host pytest file has no isolated-host schedule")

    if errors:
        preview = "\n- ".join(errors[:25])
        remainder = len(errors) - 25
        suffix = f"\n- ... and {remainder} more" if remainder > 0 else ""
        raise pytest.UsageError(f"Test classification errors:\n- {preview}{suffix}")
