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

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "tests" / "workflows" / "feature_acceptance.json"
INTERACTIONS = ROOT / "tests" / "workflows" / "feature_interactions.json"


def test_feature_acceptance_manifest_is_current_and_honest():
    assert build_acceptance_manifest(ROOT) == load_manifest(ACCEPTANCE)
    summary = validate_acceptance_manifest(root=ROOT, manifest_path=ACCEPTANCE)

    assert summary["inventory_functions"] == 472
    assert sum(summary["status_counts"].values()) == 472
    accepted = summary["status_counts"]["real_entry_accepted"]
    expected_complete = accepted == 472 and summary["dependency_audited_functions"] == 472
    assert summary["complete"] is expected_complete


def test_declared_feature_interactions_cover_every_listed_state_combination():
    summary = validate_interactions(root=ROOT, manifest_path=INTERACTIONS)

    assert summary["declared_interactions"] >= 1
    assert summary["accepted_interactions"] >= 1
