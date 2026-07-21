"""Traceability checks between the function inventory and runtime workflows."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.generate_workflow_manifest import build_manifest
from scripts.workflow_manifest import load_inventory, load_manifest, validate_manifest

pytestmark = pytest.mark.workflow

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "APP_FUNCTION_INVENTORY.md"
MANIFEST = ROOT / "tests" / "workflows" / "manifest.json"


def test_inventory_parser_preserves_every_function_and_failure_reference():
    functions = load_inventory(INVENTORY)

    assert len(functions) == 472
    assert sum(len(item.failure_refs) for item in functions) == 3296
    assert functions[0].function_id == "F001"
    assert functions[-1].function_id == "F472"
    assert len({item.name for item in functions}) == len(functions)


def test_runtime_workflow_manifest_references_real_inventory_entries_and_tests():
    summary = validate_manifest(root=ROOT, manifest_path=MANIFEST)

    assert summary["inventory_functions"] == 472
    assert summary["failure_references"] == 3296
    assert summary["mapped_functions"] == 472
    assert summary["enforce_complete"] is True
    assert summary["missing_functions"] == []
    assert sum(summary["mapping_statuses"].values()) == 472


def test_expanded_workflow_manifest_is_reproducible_from_inventory_and_tests():
    generated = build_manifest(ROOT, MANIFEST)

    assert generated == load_manifest(MANIFEST)
