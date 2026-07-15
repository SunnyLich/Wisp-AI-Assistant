from __future__ import annotations

import tomllib
from pathlib import Path

from runtime import VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_version_matches_project_metadata() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]

    assert VERSION == project_version
