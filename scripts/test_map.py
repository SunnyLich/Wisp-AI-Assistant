"""Build and validate the authoritative Wisp test catalogue.

Every pytest file is classified on two independent axes:

* behaviour: ``internal`` or ``user_path``
* execution: ``github_safe`` or ``isolated_host``

The four resulting categories are GI, GU, II, and IU.  Scope and platform are
separate routing metadata; they do not replace either required axis.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
BEHAVIOURS = ("internal", "user_path")
EXECUTIONS = ("github_safe", "isolated_host")
SCOPES = (
    "unit",
    "component",
    "integration",
    "ui",
    "e2e",
    "native",
    "support",
    "catalog",
)
PLATFORMS = ("windows", "linux", "macos")

CATALOG_RELATIVE = Path("tests/catalog/test_map.json")
REPORT_RELATIVE = Path("tests/TEST_MAP.md")

_ISOLATED_FILENAMES = {
    "test_real_gpt55_integration.py",
    "test_real_harness_integration.py",
    "test_real_host_native_smoke.py",
}

_SUPPORT_FILENAMES = {
    "test_app_workflow_runner.py",
    "test_build_scripts.py",
    "test_dependency_locks.py",
    "test_docs_setup_guidance.py",
    "test_gitattributes.py",
    "test_pip_recover_install.py",
    "test_pytest_temp_cleanup.py",
    "test_python_version_check.py",
    "test_release_manifest.py",
    "test_runtime_test_harness.py",
    "test_safe.py",
    "test_secret_scanner.py",
    "test_setup_scripts.py",
    "test_test_map.py",
    "test_testlab_install_plans.py",
    "test_version_metadata.py",
}

_UNIT_FILENAMES = {
    "test_assistant_text.py",
    "test_env_utils.py",
    "test_llm_history.py",
    "test_memory_commands.py",
    "test_page_context_extraction.py",
    "test_rewrite_tool_call.py",
    "test_settings_model.py",
    "test_text_annotations.py",
    "test_theme.py",
    "test_tool_modes.py",
    "test_tool_registry.py",
    "test_unsupported_params.py",
}

_UI_FRAGMENTS = (
    "app_icon",
    "bubble",
    "chat_rendering",
    "chat_window",
    "dialog",
    "drop_zone",
    "intent_overlay",
    "privacy_review_ui",
    "settings_fallback_rows",
    "snip_overlay",
    "visual_regression",
    "window_chrome",
)

_INTEGRATION_FRAGMENTS = (
    "addon_manager",
    "addon_runtime",
    "agent_runner",
    "audio_stream",
    "auth",
    "conversation_store",
    "external_conversation_sync",
    "harness_clients",
    "live_voice",
    "llm_fallbacks",
    "local_file_security",
    "mcp_",
    "memory_quality",
    "optional_deps",
    "privacy_gateway",
    "privacy_model",
    "provider",
    "query_pipeline",
    "secret_store",
    "shutdown_lifecycle",
    "speech_failure_matrix",
    "ssl_init_concurrency",
    "tts_",
    "unified_chat_harness",
    "uninstaller",
    "updater",
)

_NATIVE_FRAGMENTS = (
    "autostart",
    "capture",
    "context_fetcher_linux",
    "context_fetcher_macos",
    "context_fetcher_windows",
    "file_browser",
    "hotkey",
    "linux_atspi",
    "macos",
    "native_context",
    "platform_",
    "screenshot",
)

_USER_PATH_FRAGMENTS = (
    "acceptance",
    "agent_task_workflows",
    "app_user_workflows",
    "profile_user_workflows",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def discover_test_files(root: Path) -> list[tuple[str, str]]:
    """Return every pytest file plus intentionally manual test utility."""

    discovered: list[tuple[str, str]] = []
    for base in (root / "tests",):
        if not base.is_dir():
            continue
        discovered.extend(
            (_relative(path, root), "pytest")
            for path in base.rglob("test_*.py")
            if path.is_file()
        )
    tools = root / "tools"
    if tools.is_dir():
        discovered.extend(
            (_relative(path, root), "manual")
            for path in tools.glob("test_*.py")
            if path.is_file()
        )
    return sorted(set(discovered))


def category_for(behaviour: str, execution: str) -> str:
    first = "G" if execution == "github_safe" else "I"
    second = "I" if behaviour == "internal" else "U"
    return first + second


def _platforms_for(path: str) -> list[str]:
    name = Path(path).name.lower()
    if "native_platform_acceptance" in name or "autostart" in name:
        return list(PLATFORMS)
    if "macos" in name or "platform_macos" in name:
        return ["macos"]
    if "linux" in name or "atspi" in name:
        return ["linux"]
    if "win32" in name or "windows" in name:
        return ["windows"]
    return list(PLATFORMS)


def _scope_for(path: str) -> str:
    normalized = path.lower()
    name = Path(path).name.lower()
    for scope in SCOPES:
        if normalized.startswith(f"tests/{scope}/"):
            return scope
    if "manifest" in name or normalized.startswith("tests/catalog/"):
        return "catalog"
    if name in _SUPPORT_FILENAMES:
        return "support"
    if any(fragment in name for fragment in _USER_PATH_FRAGMENTS):
        return "e2e"
    if normalized.startswith("tests/runtime/") or normalized.startswith(
        "tests/integration/brain/"
    ):
        return "integration"
    if any(fragment in name for fragment in _NATIVE_FRAGMENTS):
        return "native"
    if any(fragment in name for fragment in _UI_FRAGMENTS):
        return "ui"
    if any(fragment in name for fragment in _INTEGRATION_FRAGMENTS):
        return "integration"
    if name in _UNIT_FILENAMES:
        return "unit"
    return "component"


def _behaviour_for(path: str, scope: str) -> str:
    name = Path(path).name.lower()
    if name == "test_real_host_native_smoke.py":
        return "user_path"
    if scope in {"ui", "e2e"}:
        return "user_path"
    return "internal"


def suggest_entry(path: str, kind: str) -> dict[str, Any]:
    """Suggest an initial explicit classification for a newly discovered file."""

    name = Path(path).name.lower()
    scope = _scope_for(path)
    execution = (
        "isolated_host"
        if kind == "manual" or name in _ISOLATED_FILENAMES
        else "github_safe"
    )
    behaviour = _behaviour_for(path, scope)
    return {
        "path": path,
        "kind": kind,
        "scope": scope,
        "behaviour": behaviour,
        "execution": execution,
        "category": category_for(behaviour, execution),
        "platforms": _platforms_for(path),
        "schedule": (
            "isolated-host-manual"
            if kind == "manual"
            else "isolated-host"
            if execution == "isolated_host"
            else "github-actions"
        ),
        "node_count": 0,
    }


def load_catalog(root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    path = root / CATALOG_RELATIVE
    return json.loads(path.read_text(encoding="utf-8"))


def entries_by_path(root: Path | None = None) -> dict[str, dict[str, Any]]:
    return {
        str(entry["path"]): entry
        for entry in load_catalog(root).get("entries", [])
    }


def _collect_node_counts(root: Path) -> dict[str, int]:
    env = dict(os.environ)
    env["WISP_TEST_MAP_GENERATING"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "faulthandler",
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "pytest collection failed while generating the test map:\n"
            + completed.stdout
            + completed.stderr
        )
    counts: Counter[str] = Counter()
    for line in completed.stdout.splitlines():
        if "::" not in line:
            continue
        test_path = line.split("::", 1)[0].replace("\\", "/")
        counts[test_path] += 1
    return dict(counts)


def _manual_definition_count(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )


def validate_catalog(data: dict[str, Any], root: Path | None = None) -> list[str]:
    root = root or repo_root()
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    discovered = dict(discover_test_files(root))
    entries = data.get("entries", [])
    by_path: dict[str, dict[str, Any]] = {}
    for entry in entries:
        path = str(entry.get("path", ""))
        if not path:
            errors.append("entry has no path")
            continue
        if path in by_path:
            errors.append(f"duplicate entry: {path}")
        by_path[path] = entry
        behaviour = str(entry.get("behaviour", ""))
        execution = str(entry.get("execution", ""))
        scope = str(entry.get("scope", ""))
        kind = str(entry.get("kind", ""))
        platforms = entry.get("platforms", [])
        if behaviour not in BEHAVIOURS:
            errors.append(f"{path}: invalid behaviour {behaviour!r}")
        if execution not in EXECUTIONS:
            errors.append(f"{path}: invalid execution {execution!r}")
        if scope not in SCOPES:
            errors.append(f"{path}: invalid scope {scope!r}")
        if kind not in {"pytest", "manual"}:
            errors.append(f"{path}: invalid kind {kind!r}")
        if entry.get("category") != category_for(behaviour, execution):
            errors.append(f"{path}: category does not match its two axes")
        if not entry.get("schedule"):
            errors.append(f"{path}: test is unscheduled")
        if not platforms or any(value not in PLATFORMS for value in platforms):
            errors.append(f"{path}: invalid platform list {platforms!r}")
    missing = sorted(set(discovered) - set(by_path))
    extra = sorted(set(by_path) - set(discovered))
    errors.extend(f"unclassified test file: {path}" for path in missing)
    errors.extend(f"catalogue references missing test file: {path}" for path in extra)
    for path in sorted(set(discovered) & set(by_path)):
        if discovered[path] != by_path[path].get("kind"):
            errors.append(f"{path}: kind differs from discovery")
    return errors


def refreshed_catalog(root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    existing: dict[str, dict[str, Any]] = {}
    catalog_path = root / CATALOG_RELATIVE
    if catalog_path.is_file():
        existing = entries_by_path(root)
    node_counts = _collect_node_counts(root)
    entries: list[dict[str, Any]] = []
    for path, kind in discover_test_files(root):
        entry = dict(existing.get(path, suggest_entry(path, kind)))
        entry["path"] = path
        entry["kind"] = kind
        entry["category"] = category_for(
            str(entry["behaviour"]), str(entry["execution"])
        )
        entry["node_count"] = (
            node_counts.get(path, 0)
            if kind == "pytest"
            else _manual_definition_count(root / path)
        )
        entries.append(entry)
    return {
        "schema_version": SCHEMA_VERSION,
        "required_axes": {
            "behaviour": list(BEHAVIOURS),
            "execution": list(EXECUTIONS),
        },
        "scopes": list(SCOPES),
        "entries": entries,
    }


def render_report(data: dict[str, Any]) -> str:
    entries = list(data["entries"])
    pytest_entries = [entry for entry in entries if entry["kind"] == "pytest"]
    manual_entries = [entry for entry in entries if entry["kind"] == "manual"]
    category_files = Counter(entry["category"] for entry in entries)
    category_nodes = Counter()
    scope_files = Counter(entry["scope"] for entry in entries)
    scope_nodes = Counter()
    for entry in entries:
        category_nodes[entry["category"]] += int(entry["node_count"])
        scope_nodes[entry["scope"]] += int(entry["node_count"])

    lines = [
        "# Wisp Test Map",
        "",
        "Generated by `python scripts/test_map.py`. Do not edit this report by hand.",
        "",
        "Every pytest test receives exactly one behaviour marker (`internal` or `user_path`) and exactly one execution marker (`github_safe` or `isolated_host`) from `tests/catalog/test_map.json`.",
        "",
        f"- Pytest files: **{len(pytest_entries)}**",
        f"- Collected pytest nodes: **{sum(int(entry['node_count']) for entry in pytest_entries)}**",
        f"- Manual diagnostic files: **{len(manual_entries)}**",
        "- Unclassified files: **0**",
        "- Unscheduled files: **0**",
        "",
        "## Execution categories",
        "",
        "| Category | Meaning | Files | Test nodes |",
        "|---|---|---:|---:|",
    ]
    meanings = {
        "GI": "GitHub-safe internal",
        "GU": "GitHub-safe user path",
        "II": "Isolated-host internal",
        "IU": "Isolated-host user path",
    }
    for category in ("GI", "GU", "II", "IU"):
        lines.append(
            f"| {category} | {meanings[category]} | {category_files[category]} | {category_nodes[category]} |"
        )

    lines.extend(
        [
            "",
            "## Physical scopes",
            "",
            "| Scope | Files | Test nodes |",
            "|---|---:|---:|",
        ]
    )
    for scope in SCOPES:
        lines.append(f"| {scope} | {scope_files[scope]} | {scope_nodes[scope]} |")

    lines.extend(
        [
            "",
            "## File catalogue",
            "",
            "| Path | Kind | Scope | Category | Platforms | Schedule | Nodes |",
            "|---|---|---|---|---|---|---:|",
        ]
    )
    for entry in entries:
        lines.append(
            "| `{path}` | {kind} | {scope} | {category} | {platforms} | {schedule} | {nodes} |".format(
                path=entry["path"],
                kind=entry["kind"],
                scope=entry["scope"],
                category=entry["category"],
                platforms=", ".join(entry["platforms"]),
                schedule=entry["schedule"],
                nodes=entry["node_count"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def _serialize(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_catalog(root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    data = refreshed_catalog(root)
    errors = validate_catalog(data, root)
    if errors:
        raise AssertionError("Invalid test map:\n- " + "\n- ".join(errors))
    catalog_path = root / CATALOG_RELATIVE
    report_path = root / REPORT_RELATIVE
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(_serialize(data), encoding="utf-8")
    report_path.write_text(render_report(data), encoding="utf-8")
    return data


def check_catalog(root: Path | None = None) -> list[str]:
    root = root or repo_root()
    try:
        current = load_catalog(root)
    except (OSError, ValueError, TypeError) as exc:
        return [f"cannot load {CATALOG_RELATIVE.as_posix()}: {exc}"]
    errors = validate_catalog(current, root)
    refreshed = refreshed_catalog(root)
    if current != refreshed:
        errors.append("test_map.json is stale; run python scripts/test_map.py")
    report_path = root / REPORT_RELATIVE
    expected_report = render_report(current)
    if not report_path.is_file() or report_path.read_text(encoding="utf-8") != expected_report:
        errors.append("TEST_MAP.md is stale; run python scripts/test_map.py")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the test map is incomplete or stale.")
    args = parser.parse_args(argv)
    root = repo_root()
    if args.check:
        errors = check_catalog(root)
        if errors:
            print("Test map check failed:\n- " + "\n- ".join(errors), file=sys.stderr)
            return 1
        data = load_catalog(root)
        print(
            f"Test map is current: {len(data['entries'])} files, "
            f"{sum(int(entry['node_count']) for entry in data['entries'] if entry['kind'] == 'pytest')} pytest nodes."
        )
        return 0
    data = write_catalog(root)
    print(
        f"Wrote {CATALOG_RELATIVE.as_posix()} and {REPORT_RELATIVE.as_posix()} "
        f"for {len(data['entries'])} files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
