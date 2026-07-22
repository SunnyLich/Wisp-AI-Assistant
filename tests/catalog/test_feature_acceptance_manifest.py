"""Honest coverage gate for user-visible behavior and feature interactions."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.feature_acceptance import (
    build_acceptance_manifest,
    validate_acceptance_manifest,
    validate_interactions,
)
from scripts.workflow_manifest import load_manifest

pytestmark = pytest.mark.workflow

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = ROOT / "tests" / "workflows" / "feature_acceptance.json"
INTERACTIONS = ROOT / "tests" / "workflows" / "feature_interactions.json"


def test_feature_acceptance_manifest_is_current_and_honest():
    manifest = load_manifest(ACCEPTANCE)
    assert build_acceptance_manifest(ROOT) == manifest
    summary = validate_acceptance_manifest(root=ROOT, manifest_path=ACCEPTANCE)

    assert summary["inventory_functions"] == 472
    assert sum(summary["status_counts"].values()) == 472
    assert summary["status_counts"]["real_entry_accepted"] == 472
    assert summary["dependency_audited_functions"] == 472
    assert summary["complete"] is True
    assert all(record["interaction_ids"] for record in manifest["records"])


def test_declared_feature_interactions_cover_every_listed_state_combination():
    summary = validate_interactions(root=ROOT, manifest_path=INTERACTIONS)

    assert summary["declared_interactions"] == 197
    assert summary["accepted_interactions"] == summary["declared_interactions"]
