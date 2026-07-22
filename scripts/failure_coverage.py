"""Parse and validate function-specific failure-test evidence."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

try:
    from scripts.workflow_manifest import load_inventory, load_manifest
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from workflow_manifest import load_inventory, load_manifest


@dataclass(frozen=True)
class FailureReference:
    reference: int
    function_id: str
    function: str
    cause: str
    evidence_ids: tuple[str, ...]


_CHECKED_CAUSE = re.compile(
    r"^- \[x\] \[(?P<reference>\d+)\] (?P<cause>.+?)(?P<evidence> (?:\[T\d+\])+)$",
    re.IGNORECASE,
)
_UNCHECKED_CAUSE = re.compile(r"^- \[(?P<reference>\d+)\] (?P<cause>.+)$")
_EVIDENCE_LINE = re.compile(
    r"^- \[(?P<evidence>T\d+)\] \[(?P<label>.+?)\]\((?P<path>.+?)\)::`(?P<test>.+?)`$"
)


def _plain_name(value: str) -> str:
    return value.replace("**", "").strip()


def load_failure_references(path: Path) -> list[FailureReference]:
    """Read all numbered causes and their explicit T-evidence IDs."""

    text = path.read_text(encoding="utf-8")
    catalogue = text.split("## Differentiated failure reference catalogue", 1)[1]
    catalogue = catalogue.split("## Failure-test evidence", 1)[0]
    inventory = load_inventory(path)
    by_name = {_plain_name(item.name): item for item in inventory}
    current = None
    references: list[FailureReference] = []
    for line in catalogue.splitlines():
        if line.startswith("### "):
            current = by_name.get(_plain_name(line[4:]))
            continue
        match = _CHECKED_CAUSE.match(line)
        evidence: tuple[str, ...] = ()
        if match is not None:
            evidence = tuple(re.findall(r"T\d+", match.group("evidence")))
        else:
            match = _UNCHECKED_CAUSE.match(line)
        if match is None:
            continue
        if current is None:
            raise ValueError(f"failure reference has no inventory function: {line}")
        references.append(
            FailureReference(
                reference=int(match.group("reference")),
                function_id=current.function_id,
                function=current.name,
                cause=match.group("cause"),
                evidence_ids=evidence,
            )
        )
    return references


def load_evidence_nodes(path: Path) -> dict[str, str]:
    """Return T-evidence IDs mapped to repository-relative pytest node IDs."""

    text = path.read_text(encoding="utf-8")
    evidence = text.split("## Failure-test evidence", 1)[1]
    result: dict[str, str] = {}
    for line in evidence.splitlines():
        match = _EVIDENCE_LINE.match(line)
        if match is None:
            continue
        linked = match.group("path").replace("\\", "/")
        while linked.startswith("../"):
            linked = linked[3:]
        result[match.group("evidence")] = f"{linked}::{match.group('test')}"
    return result


@cache
def _ast_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def pytest_node_exists(root: Path, node_id: str) -> bool:
    """Check a top-level or class-qualified pytest node against its AST."""

    parts = node_id.split("::")
    if len(parts) < 2:
        return False
    path = root / parts[0]
    if not path.is_file():
        return False
    body: list[ast.stmt] = list(_ast_tree(path).body)
    for name in parts[1:]:
        match = next(
            (
                node
                for node in body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ),
            None,
        )
        if match is None:
            return False
        body = list(match.body) if isinstance(match, ast.ClassDef) else []
    return True


def load_failure_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_failure_manifest(*, root: Path, manifest_path: Path) -> dict[str, Any]:
    """Validate all 3,296 reference records and direct evidence nodes."""

    manifest = load_failure_manifest(manifest_path)
    inventory_path = root / str(manifest["inventory_source"])
    source_refs = load_failure_references(inventory_path)
    source_by_ref = {item.reference: item for item in source_refs}
    source_pairs = {(item.function_id, item.cause) for item in source_refs}
    function_manifest = load_manifest(root / str(manifest["function_manifest"]))
    mapped_functions = {
        record["function_id"]: record for record in function_manifest.get("workflows", [])
    }
    errors: list[str] = []
    shared_boundary_path = root / str(
        manifest.get(
            "shared_failure_boundaries",
            "tests/workflows/shared_failure_boundaries.json",
        )
    )
    try:
        shared_manifest = json.loads(shared_boundary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"cannot load shared failure boundaries: {exc}")
        shared_manifest = {"boundaries": []}
    shared_pairs: dict[tuple[str, str], str] = {}
    boundary_ids: set[str] = set()
    for boundary in shared_manifest.get("boundaries", []):
        boundary_id = str(boundary.get("id") or "").strip()
        if not boundary_id:
            errors.append("shared failure boundary has no ID")
            continue
        if boundary_id in boundary_ids:
            errors.append(f"duplicate shared failure boundary ID: {boundary_id}")
        boundary_ids.add(boundary_id)
        if not str(boundary.get("rationale") or "").strip():
            errors.append(f"shared failure boundary has no rationale: {boundary_id}")
        function_ids = [str(value) for value in boundary.get("function_ids", [])]
        evidence_by_cause = boundary.get("evidence_by_cause", {})
        if not function_ids or not evidence_by_cause:
            errors.append(f"shared failure boundary is empty: {boundary_id}")
        for cause, node_ids in evidence_by_cause.items():
            if not node_ids:
                errors.append(
                    f"shared failure boundary cause has no evidence: {boundary_id}: {cause}"
                )
            for node_id in node_ids:
                if not pytest_node_exists(root, str(node_id)):
                    errors.append(
                        f"missing shared-boundary evidence node: {boundary_id}: {node_id}"
                    )
            for function_id in function_ids:
                pair = (function_id, str(cause))
                if pair not in source_pairs:
                    errors.append(
                        f"shared failure boundary pair is absent from inventory: "
                        f"{boundary_id}: {function_id}: {cause}"
                    )
                previous = shared_pairs.get(pair)
                if previous is not None:
                    errors.append(
                        f"shared failure boundary pair is duplicated: "
                        f"{previous}/{boundary_id}: {function_id}: {cause}"
                    )
                shared_pairs[pair] = boundary_id
    seen: set[int] = set()
    verified = 0
    for record in manifest.get("failure_cases", []):
        reference = int(record.get("reference", 0))
        source = source_by_ref.get(reference)
        if source is None:
            errors.append(f"unknown failure reference: {reference}")
            continue
        if reference in seen:
            errors.append(f"duplicate failure reference: {reference}")
        seen.add(reference)
        if record.get("function_id") != source.function_id:
            errors.append(f"function ID mismatch for [{reference}]")
        if record.get("function") != source.function:
            errors.append(f"function text mismatch for [{reference}]")
        if record.get("cause") != source.cause:
            errors.append(f"cause text mismatch for [{reference}]")
        expected_boundary = shared_pairs.get((source.function_id, source.cause), "")
        if str(record.get("shared_boundary") or "") != expected_boundary:
            errors.append(f"shared failure boundary mismatch for [{reference}]")
        if source.function_id not in mapped_functions:
            errors.append(f"function is absent from workflow manifest for [{reference}]")
        evidence_nodes = list(record.get("evidence_node_ids", []))
        status = record.get("status")
        if status == "verified":
            verified += 1
            if not evidence_nodes:
                errors.append(f"verified reference has no evidence: [{reference}]")
        elif status != "uncovered":
            errors.append(f"invalid failure status for [{reference}]: {status}")
        for node_id in evidence_nodes:
            if not pytest_node_exists(root, str(node_id)):
                errors.append(f"missing evidence node for [{reference}]: {node_id}")
    missing = sorted(set(source_by_ref) - seen)
    if missing:
        errors.append(f"{len(missing)} failure references have no manifest record")
    uncovered = len(source_refs) - verified
    if manifest.get("enforce_complete") and uncovered:
        errors.append(f"{uncovered} failure references lack direct executable evidence")
    if errors:
        raise AssertionError("Failure coverage manifest is invalid:\n- " + "\n- ".join(errors))
    return {
        "failure_references": len(source_refs),
        "verified_references": verified,
        "uncovered_references": uncovered,
        "unique_causes": len({item.cause for item in source_refs}),
        "enforce_complete": bool(manifest.get("enforce_complete")),
    }
