"""Load and validate the incremental app workflow manifest."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_INVENTORY_LINE = re.compile(
    r"^- \[[ xX]\] (?P<name>.+?) (?P<refs>(?:\[\d+\])+)$"
)
_REFERENCE = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class InventoryFunction:
    """One function and its function-specific failure references."""

    function_id: str
    name: str
    failure_refs: tuple[int, ...]


def load_inventory(path: Path) -> list[InventoryFunction]:
    """Read the authoritative inventory section before its audit catalogue."""

    functions: list[InventoryFunction] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "## Audit notes":
            break
        match = _INVENTORY_LINE.match(line)
        if match is None:
            continue
        refs = tuple(int(value) for value in _REFERENCE.findall(match.group("refs")))
        functions.append(
            InventoryFunction(
                function_id=f"F{len(functions) + 1:03d}",
                name=match.group("name"),
                failure_refs=refs,
            )
        )
    return functions


def load_manifest(path: Path) -> dict[str, Any]:
    """Read the JSON workflow mapping."""

    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _test_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def validate_manifest(*, root: Path, manifest_path: Path) -> dict[str, Any]:
    """Validate source traceability and return an incremental coverage summary."""

    manifest = load_manifest(manifest_path)
    inventory_path = root / str(manifest["inventory_source"])
    inventory = load_inventory(inventory_path)
    by_name = {item.name: item for item in inventory}
    errors: list[str] = []
    mapped: set[str] = set()
    required_fields = {
        "function_id",
        "function",
        "failure_refs",
        "test_node_ids",
        "production_entry_point",
        "scenarios",
        "platforms",
        "optional_components",
        "timeout_seconds",
        "persistent_state",
        "cleanup",
        "mapping_status",
        "source_section",
    }

    for index, record in enumerate(manifest.get("workflows", []), start=1):
        missing_fields = sorted(required_fields - set(record))
        if missing_fields:
            errors.append(f"record {index} missing fields: {', '.join(missing_fields)}")
            continue
        name = str(record["function"])
        item = by_name.get(name)
        if item is None:
            errors.append(f"unknown inventory function: {name}")
            continue
        if name in mapped:
            errors.append(f"duplicate workflow record: {name}")
        mapped.add(name)
        if record["function_id"] != item.function_id:
            errors.append(f"function ID differs from inventory for {name}")
        if record["mapping_status"] not in {"verified", "direct", "section"}:
            errors.append(f"invalid mapping status for {name}: {record['mapping_status']}")
        if not str(record["source_section"]).strip():
            errors.append(f"missing source section for {name}")
        if tuple(record["failure_refs"]) != item.failure_refs:
            errors.append(f"failure references differ from inventory for {name}")
        if not record["test_node_ids"]:
            errors.append(f"no workflow test node IDs for {name}")
        for node_id in record["test_node_ids"]:
            try:
                relative_path, test_name = str(node_id).split("::", 1)
            except ValueError:
                errors.append(f"invalid test node ID for {name}: {node_id}")
                continue
            test_path = root / relative_path
            if not test_path.is_file():
                errors.append(f"missing test file for {name}: {relative_path}")
                continue
            if test_name not in _test_functions(test_path):
                errors.append(f"missing test function for {name}: {node_id}")

    missing = [item.name for item in inventory if item.name not in mapped]
    if manifest.get("enforce_complete") and missing:
        errors.append(f"{len(missing)} inventory functions have no workflow record")
    if errors:
        raise AssertionError("Workflow manifest is invalid:\n- " + "\n- ".join(errors))
    return {
        "inventory_functions": len(inventory),
        "failure_references": sum(len(item.failure_refs) for item in inventory),
        "mapped_functions": len(mapped),
        "missing_functions": missing,
        "enforce_complete": bool(manifest.get("enforce_complete")),
        "mapping_statuses": {
            status: sum(1 for record in manifest.get("workflows", []) if record.get("mapping_status") == status)
            for status in ("verified", "direct", "section")
        },
    }
