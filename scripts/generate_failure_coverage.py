"""Generate the per-reference failure-evidence manifest from the inventory."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    from scripts.failure_coverage import load_evidence_nodes, load_failure_references
    from scripts.workflow_manifest import load_manifest
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from failure_coverage import load_evidence_nodes, load_failure_references
    from workflow_manifest import load_manifest


def _family(cause: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", cause.lower()).strip("_")


def build_failure_manifest(root: Path) -> dict:
    inventory_path = root / "docs" / "APP_FUNCTION_INVENTORY.md"
    references = load_failure_references(inventory_path)
    evidence_nodes = load_evidence_nodes(inventory_path)
    override_path = root / "tests" / "workflows" / "failure_evidence_overrides.json"
    overrides = (
        json.loads(override_path.read_text(encoding="utf-8"))
        if override_path.exists()
        else {}
    )
    boundary_path = root / "tests" / "workflows" / "shared_failure_boundaries.json"
    boundary_manifest = (
        json.loads(boundary_path.read_text(encoding="utf-8"))
        if boundary_path.exists()
        else {"boundaries": []}
    )
    shared_evidence: dict[tuple[str, str], tuple[str, list[str]]] = {}
    for boundary in boundary_manifest.get("boundaries", []):
        boundary_id = str(boundary["id"])
        for function_id in boundary.get("function_ids", []):
            for cause, cause_nodes in boundary.get("evidence_by_cause", {}).items():
                key = (str(function_id), str(cause))
                if key in shared_evidence:
                    raise ValueError(
                        f"duplicate shared failure boundary for {function_id}: {cause}"
                    )
                shared_evidence[key] = (
                    boundary_id,
                    [str(node) for node in cause_nodes],
                )
    function_manifest = load_manifest(root / "tests" / "workflows" / "manifest.json")
    function_nodes = {
        record["function_id"]: list(record["test_node_ids"])
        for record in function_manifest.get("workflows", [])
    }
    cases = []
    for item in references:
        nodes = [evidence_nodes[evidence_id] for evidence_id in item.evidence_ids]
        boundary_id = ""
        boundary = shared_evidence.get((item.function_id, item.cause))
        if boundary is not None:
            boundary_id, boundary_nodes = boundary
            nodes.extend(boundary_nodes)
        nodes.extend(str(node) for node in overrides.get(str(item.reference), []))
        nodes = list(dict.fromkeys(nodes))
        cases.append(
            {
                "reference": item.reference,
                "function_id": item.function_id,
                "function": item.function,
                "cause": item.cause,
                "fault_family": _family(item.cause),
                "status": "verified" if nodes else "uncovered",
                "shared_boundary": boundary_id,
                "evidence_ids": list(item.evidence_ids),
                "evidence_node_ids": nodes,
                "function_workflow_node_ids": function_nodes[item.function_id],
            }
        )
    return {
        "schema_version": 1,
        "inventory_source": "docs/APP_FUNCTION_INVENTORY.md",
        "function_manifest": "tests/workflows/manifest.json",
        "evidence_overrides": "tests/workflows/failure_evidence_overrides.json",
        "shared_failure_boundaries": "tests/workflows/shared_failure_boundaries.json",
        "enforce_complete": True,
        "failure_cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    path = root / "tests" / "workflows" / "failure_coverage.json"
    generated = json.dumps(build_failure_manifest(root), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        return 0 if path.read_text(encoding="utf-8") == generated else 1
    path.write_text(generated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
