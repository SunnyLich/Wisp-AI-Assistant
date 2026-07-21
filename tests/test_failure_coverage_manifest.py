"""Audit every numbered app failure against direct executable evidence."""

from __future__ import annotations

from pathlib import Path

from scripts.failure_coverage import validate_failure_manifest
from scripts.generate_failure_coverage import build_failure_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "workflows" / "failure_coverage.json"


def test_failure_manifest_tracks_every_reference_without_inflating_coverage():
    summary = validate_failure_manifest(root=ROOT, manifest_path=MANIFEST)

    assert summary == {
        "failure_references": 3296,
        "verified_references": 3296,
        "uncovered_references": 0,
        "unique_causes": 361,
        "enforce_complete": True,
    }


def test_failure_manifest_is_reproducible_from_inventory_and_evidence():
    import json

    assert build_failure_manifest(ROOT) == json.loads(MANIFEST.read_text(encoding="utf-8"))
