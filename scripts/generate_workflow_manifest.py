"""Generate the expanded 472-function workflow manifest.

Existing hand-verified records are preserved. Remaining inventory entries are
matched only within a curated test-family pool for their product section. The
record says whether the match is hand-verified, direct by name, or a broader
section mapping so mapping completeness is not confused with direct workflow
coverage.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from scripts.workflow_manifest import InventoryFunction, load_inventory, load_manifest
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from workflow_manifest import InventoryFunction, load_inventory, load_manifest


@dataclass(frozen=True)
class SectionRule:
    start: int
    end: int
    name: str
    file_terms: tuple[str, ...]
    fallback_node: str


SECTION_RULES = (
    SectionRule(1, 30, "launch_setup_shell", ("app_user_workflows", "profile_user_workflows", "onboarding", "supervisor_ipc", "app_logging", "overlay_bubble_visibility", "autostart", "app_icon", "runtime_status", "addon_manager_ui", "harness_controls"), "tests/test_app_user_workflows.py::test_launch_duplicate_crash_log_and_worker_lifecycle_workflow"),
    SectionRule(31, 52, "ask_rewrite", ("app_user_workflows", "runtime/test_flows.py", "intent_overlay", "query_pipeline", "rewrite_tool_call", "llm_fallbacks", "error_recommendations"), "tests/runtime/test_flows.py::test_query_flow_streams_reply_and_adds_chat_conversation_with_context"),
    SectionRule(53, 81, "shortcuts", ("hotkey", "settings_dialog_controls", "runtime/test_flows.py", "config_env", "tool_modes"), "tests/test_settings_dialog_controls.py::test_settings_shortcuts_are_categorized_with_two_bindings_and_inline_details"),
    SectionRule(82, 115, "context_capture", ("context", "runtime/test_flows.py", "query_pipeline", "builtin_model_tools", "local_file_security", "tool_modes", "app_user_workflows"), "tests/test_app_user_workflows.py::test_context_buffer_drop_priority_and_privacy_workflow"),
    SectionRule(116, 125, "snip_vision", ("snip", "screenshot", "capture", "runtime/test_flows.py", "bubble_transcript", "chat_rendering"), "tests/test_snip_overlay.py::test_drag_selection_emits_scaled_region_and_closes"),
    SectionRule(126, 143, "reply_bubble", ("bubble", "overlay_bubble_visibility", "ui_host_reply", "text_annotations", "addon_manager"), "tests/test_bubble_transcript.py::test_transcript_preview_is_replaced_by_first_reply_chunk"),
    SectionRule(144, 185, "chat_projects", ("app_user_workflows", "conversation_store", "chat_", "ui_host_reply", "planned_chunking", "assistant_text", "text_annotations"), "tests/test_app_user_workflows.py::test_chat_window_context_preview_send_and_history_workflow"),
    SectionRule(186, 205, "external_conversations", ("external_conversation_sync", "harness", "unified_chat", "conversation_store", "app_user_workflows"), "tests/test_external_conversation_sync.py::test_sync_updates_in_place_and_preserves_wisp_tail"),
    SectionRule(206, 252, "provider_connections", ("chatgpt_auth", "github_auth", "copilot", "secret_store", "settings_dialog_controls", "ollama", "sdk_clients", "llm_fallbacks", "config_env"), "tests/test_settings_dialog_controls.py::test_connections_page_filters_and_paginates_large_provider_lists"),
    SectionRule(253, 271, "model_routing", ("llm_fallbacks", "settings_fallback_rows", "settings_dialog_controls", "screenshot_capability", "unsupported_params", "sdk_clients", "harness", "app_user_workflows"), "tests/test_app_user_workflows.py::test_provider_fallback_cooldown_capability_and_auth_redaction_workflow"),
    SectionRule(272, 305, "speech_voice", ("audio", "tts", "stt", "live_voice", "released_speech", "runtime/test_flows.py", "settings_dialog_controls", "optional_deps"), "tests/test_app_user_workflows.py::test_supervisor_rewrite_snip_voice_and_dictation_workflow"),
    SectionRule(306, 321, "memory", ("memory", "app_user_workflows", "settings_dialog_controls", "query_pipeline"), "tests/test_app_user_workflows.py::test_brain_memory_crud_and_project_scope_workflow"),
    SectionRule(322, 332, "privacy_secrets", ("privacy", "secret", "app_user_workflows", "settings_dialog_controls", "query_pipeline"), "tests/test_app_user_workflows.py::test_brain_query_workflow_assembles_context_and_redacts_secrets"),
    SectionRule(333, 357, "tools_permissions", ("builtin_model_tools", "tool_registry", "tool_modes", "local_file_security", "query_pipeline", "runtime/test_flows.py", "app_user_workflows", "mcp"), "tests/test_app_user_workflows.py::test_tool_file_permission_and_approval_workflow"),
    SectionRule(358, 378, "addons_mcp", ("addon", "mcp", "text_annotations", "app_user_workflows"), "tests/test_app_user_workflows.py::test_brain_addon_install_settings_actions_and_toggle_workflow"),
    SectionRule(379, 416, "agent_tasks", ("agent", "ui_host_agent_meeting", "app_user_workflows"), "tests/test_app_user_workflows.py::test_auto_agent_run_streams_logs_and_persists_artifacts_workflow"),
    SectionRule(417, 439, "settings_profiles", ("settings", "profile_user_workflows", "theme", "i18n", "app_user_workflows", "onboarding"), "tests/test_app_user_workflows.py::test_settings_real_apply_click_persists_and_reopens"),
    SectionRule(440, 446, "optional_installers", ("optional_install_dialog", "optional_deps", "pip_recover_install", "settings_dialog_controls"), "tests/test_optional_install_dialog.py::test_optional_install_dialog_streams_success_output"),
    SectionRule(447, 462, "updates_diagnostics_uninstall", ("updater", "uninstaller", "crash_report", "version_metadata", "release_manifest", "app_logging", "runtime_log", "app_user_workflows"), "tests/test_app_user_workflows.py::test_launch_duplicate_crash_log_and_worker_lifecycle_workflow"),
    SectionRule(463, 472, "platform_integration", ("capture", "context_fetcher", "hotkey", "platform_macos", "macos", "linux", "win32", "window_chrome", "file_browser", "real_host_native_smoke", "app_user_workflows"), "tests/test_app_user_workflows.py::test_context_disabled_sources_preview_and_os_native_contract_workflow"),
)

_STOP_WORDS = set(
    "a an and app are as at be before by choose configure current each enable for from in into is it its of on open or run set show the through to use using when where while wisp with".split()
)
_ALIASES = {
    "add": "create",
    "adding": "create",
    "cancelled": "cancel",
    "cancels": "cancel",
    "configuration": "settings",
    "configure": "settings",
    "configured": "settings",
    "deleting": "delete",
    "display": "show",
    "editing": "edit",
    "launch": "start",
    "loads": "load",
    "opening": "open",
    "persist": "save",
    "persists": "save",
    "refreshes": "refresh",
    "removing": "remove",
    "selected": "selection",
    "settings": "setting",
    "shut": "shutdown",
    "stops": "stop",
    "transcripts": "transcript",
    "workers": "worker",
}


def _tokens(value: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return {
        _ALIASES.get(word, word)
        for word in words
        if len(word) > 2 and word not in _STOP_WORDS
    }


def _top_level_tests(root: Path) -> list[tuple[str, str, set[str]]]:
    tests: list[tuple[str, str, set[str]]] = []
    for path in sorted((root / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        relative = path.relative_to(root).as_posix()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            label = node.name.replace("_", " ")
            tests.append((f"{relative}::{node.name}", label, _tokens(label)))
    return tests


def _rule_for(item: InventoryFunction) -> SectionRule:
    number = int(item.function_id[1:])
    return next(rule for rule in SECTION_RULES if rule.start <= number <= rule.end)


def _candidate_score(item: InventoryFunction, candidate: tuple[str, str, set[str]]) -> float:
    node_id, label, candidate_tokens = candidate
    item_tokens = _tokens(item.name)
    overlap = len(item_tokens & candidate_tokens)
    token_score = (2.0 * overlap / (len(item_tokens) + len(candidate_tokens))) if item_tokens else 0.0
    sequence_score = difflib.SequenceMatcher(None, item.name.lower(), label).ratio()
    path_score = 0.08 * len(item_tokens & _tokens(node_id.split("::", 1)[0]))
    return token_score + 0.22 * sequence_score + path_score


def _best_mapping(item: InventoryFunction, tests: list[tuple[str, str, set[str]]]) -> tuple[str, float]:
    rule = _rule_for(item)
    candidates = [
        candidate
        for candidate in tests
        if any(term in candidate[0] for term in rule.file_terms)
    ]
    if not candidates:
        return rule.fallback_node, 0.0
    score, node_id = max(
        (_candidate_score(item, candidate), candidate[0])
        for candidate in candidates
    )
    if score < 0.22:
        return rule.fallback_node, score
    return node_id, score


def _scenarios(name: str) -> list[str]:
    lowered = name.lower()
    result = ["normal", "repeat", "cleanup"]
    if any(word in lowered for word in ("cancel", "stop", "close", "quit", "delete", "remove")):
        result.append("cancel")
    if any(word in lowered for word in ("save", "store", "history", "profile", "setting", "remember", "project")):
        result.extend(["persistence", "restart"])
    if any(word in lowered for word in ("fail", "recover", "repair", "fallback", "permission", "privacy", "key")):
        result.extend(["dependency failure", "recovery"])
    return list(dict.fromkeys(result))


def build_manifest(root: Path, existing_path: Path) -> dict[str, Any]:
    inventory = load_inventory(root / "docs" / "APP_FUNCTION_INVENTORY.md")
    tests = _top_level_tests(root)
    existing = load_manifest(existing_path) if existing_path.exists() else {"workflows": []}
    verified = {
        str(record["function"]): dict(record)
        for record in existing.get("workflows", [])
        if record.get("mapping_status", "verified") == "verified"
    }
    records: list[dict[str, Any]] = []
    for item in inventory:
        rule = _rule_for(item)
        if item.name in verified:
            record = verified[item.name]
            record["function_id"] = item.function_id
            record["failure_refs"] = list(item.failure_refs)
            record["mapping_status"] = "verified"
            record["source_section"] = rule.name
            records.append(record)
            continue
        node_id, score = _best_mapping(item, tests)
        status = "direct" if score >= 0.48 else "section"
        entry_point = (
            f"Production entry point exercised by {node_id}"
            if status == "direct"
            else f"Section-level production surface represented by {node_id}"
        )
        records.append(
            {
                "function_id": item.function_id,
                "function": item.name,
                "failure_refs": list(item.failure_refs),
                "test_node_ids": [node_id],
                "production_entry_point": entry_point,
                "scenarios": _scenarios(item.name),
                "platforms": ["windows", "macos", "linux"],
                "optional_components": ["PySide6"] if any(term in node_id for term in ("ui_", "dialog", "overlay", "bubble", "window", "onboarding")) else [],
                "timeout_seconds": 120 if "supervisor_ipc" in node_id or "real_host" in node_id else 30,
                "persistent_state": ["function-specific app state"] if any(term in item.name.lower() for term in ("save", "store", "history", "profile", "setting", "memory", "project", "conversation")) else [],
                "cleanup": ["workflow-owned processes, threads, timers, and temporary paths"],
                "mapping_status": status,
                "mapping_score": round(score, 3),
                "source_section": rule.name,
            }
        )
    return {
        "schema_version": 2,
        "inventory_source": "docs/APP_FUNCTION_INVENTORY.md",
        "enforce_complete": True,
        "workflows": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the expanded manifest is stale.")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    path = root / "tests" / "workflows" / "manifest.json"
    generated = json.dumps(build_manifest(root, path), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        return 0 if path.read_text(encoding="utf-8") == generated else 1
    path.write_text(generated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
