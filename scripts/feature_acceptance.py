"""Build and validate honest real-entry feature acceptance coverage.

The older workflow manifest is a traceability/candidate index.  It deliberately
does not prove that a user-visible function works.  This module adds the
separate acceptance gate used for that claim.
"""

from __future__ import annotations

import ast
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from scripts.workflow_manifest import load_inventory, load_manifest
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from workflow_manifest import load_inventory, load_manifest

ACCEPTED = "real_entry_accepted"
COMPONENT_ONLY = "component_only"
CANDIDATE = "candidate_needs_audit"
UNTESTED = "untested"
VALID_STATUSES = {ACCEPTED, COMPONENT_ONLY, CANDIDATE, UNTESTED}


@lru_cache(maxsize=None)
def _test_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def _node_exists(root: Path, node_id: str) -> bool:
    parts = node_id.split("::")
    if len(parts) < 2:
        return False
    relative, test_name = parts[0], parts[-1]
    path = root / relative
    return path.is_file() and test_name in _test_functions(path)


def build_acceptance_manifest(root: Path) -> dict[str, Any]:
    inventory = load_inventory(root / "docs" / "APP_FUNCTION_INVENTORY.md")
    trace = load_manifest(root / "tests" / "workflows" / "manifest.json")
    trace_by_id = {str(row["function_id"]): row for row in trace["workflows"]}
    overrides_path = root / "tests" / "workflows" / "feature_acceptance_overrides.json"
    overrides = load_manifest(overrides_path)
    override_by_id = {str(row["function_id"]): row for row in overrides["overrides"]}

    records: list[dict[str, Any]] = []
    for item in inventory:
        source = trace_by_id[item.function_id]
        override = override_by_id.get(item.function_id)
        if override is not None:
            status = str(override["acceptance_status"])
            node_ids = list(override.get("test_node_ids", []))
            entry = str(override.get("production_entry_point", ""))
            assertions = list(override.get("success_assertions", []))
            note = str(override.get("audit_note", ""))
            dependency_status = str(override.get("dependency_status", "pending"))
            interaction_ids = list(override.get("interaction_ids", []))
        else:
            # A direct name match is only a candidate.  A section-level match is
            # not retained as evidence because it often points at another feature.
            direct = source.get("mapping_status") in {"direct", "verified"}
            status = CANDIDATE if direct else UNTESTED
            node_ids = list(source.get("test_node_ids", [])) if direct else []
            entry = ""
            assertions = []
            note = "Candidate generated from the trace manifest; requires a human code-path audit."
            dependency_status = "pending"
            interaction_ids = []
        records.append(
            {
                "function_id": item.function_id,
                "function": item.name,
                "acceptance_status": status,
                "production_entry_point": entry,
                "test_node_ids": node_ids,
                "success_assertions": assertions,
                "dependency_status": dependency_status,
                "interaction_ids": interaction_ids,
                "audit_note": note,
            }
        )
    return {
        "schema_version": 1,
        "inventory_source": "docs/APP_FUNCTION_INVENTORY.md",
        "completion_requires": {
            "accepted_functions": 472,
            "dependency_audited_functions": 472,
            "accepted_interactions": "all declared interactions",
        },
        "records": records,
    }


def validate_acceptance_manifest(*, root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    inventory = load_inventory(root / str(manifest["inventory_source"]))
    by_id = {item.function_id: item for item in inventory}
    interactions = load_manifest(root / "tests" / "workflows" / "feature_interactions.json")
    interaction_ids = {str(row["interaction_id"]) for row in interactions["interactions"]}
    errors: list[str] = []
    seen: set[str] = set()

    for record in manifest.get("records", []):
        function_id = str(record.get("function_id", ""))
        item = by_id.get(function_id)
        if item is None:
            errors.append(f"unknown function ID: {function_id}")
            continue
        if function_id in seen:
            errors.append(f"duplicate function ID: {function_id}")
        seen.add(function_id)
        if record.get("function") != item.name:
            errors.append(f"inventory text differs for {function_id}")
        status = str(record.get("acceptance_status", ""))
        if status not in VALID_STATUSES:
            errors.append(f"invalid acceptance status for {function_id}: {status}")
        nodes = [str(value) for value in record.get("test_node_ids", [])]
        for node in nodes:
            if not _node_exists(root, node):
                errors.append(f"missing test node for {function_id}: {node}")
        if status == ACCEPTED:
            if not nodes:
                errors.append(f"accepted function has no test node: {function_id}")
            if not str(record.get("production_entry_point", "")).strip():
                errors.append(f"accepted function has no production entry point: {function_id}")
            if not record.get("success_assertions"):
                errors.append(f"accepted function has no success assertion: {function_id}")
        for interaction_id in record.get("interaction_ids", []):
            if interaction_id not in interaction_ids:
                errors.append(f"unknown interaction {interaction_id} on {function_id}")

    missing = sorted(set(by_id) - seen)
    if missing:
        errors.append(f"{len(missing)} inventory functions missing from acceptance manifest")
    if errors:
        raise AssertionError("Feature acceptance manifest is invalid:\n- " + "\n- ".join(errors))

    counts = {
        status: sum(1 for row in manifest["records"] if row["acceptance_status"] == status)
        for status in sorted(VALID_STATUSES)
    }
    dependency_audited = sum(
        1 for row in manifest["records"] if row.get("dependency_status") == "audited"
    )
    complete = counts[ACCEPTED] == len(inventory) and dependency_audited == len(inventory)
    return {
        "inventory_functions": len(inventory),
        "status_counts": counts,
        "dependency_audited_functions": dependency_audited,
        "complete": complete,
    }


def validate_interactions(*, root: Path, manifest_path: Path) -> dict[str, Any]:
    data = load_manifest(manifest_path)
    inventory = {item.function_id for item in load_inventory(root / "docs" / "APP_FUNCTION_INVENTORY.md")}
    acceptance_path = root / "tests" / "workflows" / "feature_acceptance.json"
    acceptance = load_manifest(acceptance_path)
    accepted_functions = {
        str(row["function_id"])
        for row in acceptance.get("records", [])
        if row.get("acceptance_status") == ACCEPTED
    }
    errors: list[str] = []
    accepted = 0
    for row in data.get("interactions", []):
        interaction_id = str(row.get("interaction_id", ""))
        sources = [str(value) for value in row.get("source_function_ids", [])]
        targets = [str(value) for value in row.get("target_function_ids", [])]
        if not sources:
            sources = [str(row.get("source_function_id", ""))]
        if not targets:
            targets = [str(row.get("target_function_id", ""))]
        if any(value not in inventory for value in (*sources, *targets)):
            errors.append(f"{interaction_id} references an unknown function")
        source_states = [str(value) for value in row.get("source_states", [])]
        target_states = [str(value) for value in row.get("target_states", [])]
        expected = {(a, b) for a in source_states for b in target_states}
        actual = {
            (str(case.get("source_state")), str(case.get("target_state")))
            for case in row.get("cases", [])
        }
        if expected != actual:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            errors.append(f"{interaction_id} state matrix differs; missing={missing}, extra={extra}")
        nodes = [str(value) for value in row.get("test_node_ids", [])]
        for node in nodes:
            if not _node_exists(root, node):
                errors.append(f"missing interaction test node for {interaction_id}: {node}")
        if row.get("status") == "accepted":
            accepted += 1
            missing_acceptance = sorted((set(sources) | set(targets)) - accepted_functions)
            if missing_acceptance:
                errors.append(
                    f"accepted interaction {interaction_id} has unaccepted endpoint(s): {missing_acceptance}"
                )
            if not nodes:
                errors.append(f"accepted interaction has no test node: {interaction_id}")
            if any(not case.get("expected_result") for case in row.get("cases", [])):
                errors.append(f"accepted interaction has a case without an expected result: {interaction_id}")
    if errors:
        raise AssertionError("Feature interaction manifest is invalid:\n- " + "\n- ".join(errors))
    return {"declared_interactions": len(data.get("interactions", [])), "accepted_interactions": accepted}
