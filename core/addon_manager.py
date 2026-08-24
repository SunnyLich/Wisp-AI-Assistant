"""Addon lifecycle management for OpenWand.

Addons are folders with an ``addon.toml`` manifest. Each enabled addon runs in
its own subprocess host so hook crashes and long-running code do not execute in
the brain process.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import addon_runtime, addon_store
from core.action_files.addons import AddonActionFile, load_addon_actions
from core.action_files.contracts import LoadIssue
from core.action_files.edit import update_toml_values
from core.system.paths import ADDONS_DIR, BUNDLED_ADDONS_DIR, REPO_ROOT

log = logging.getLogger("openwand.addons")

_HOST_TIMEOUT_SECONDS = 2.0
_DEFAULT_BUNDLED_ADDONS = (
    "mcp_bridge",
    "ui_lab",
    "virtual_workspace",
)

# These shipped in older builds but are now part of the core product (or no
# longer supported).  Ignore an already-seeded copy in the user's addon folder
# so an upgrade cannot silently bring the old UI/actions back.
_RETIRED_ADDON_IDS = {"formatted-replies"}


def _terminal(event: str) -> None:
    """Handle terminal for addon manager."""
    try:
        print(f"[addon] {event}", file=sys.stderr, flush=True)
    except Exception:
        pass


@dataclass(frozen=True)
class AddonManifest:
    """Model addon manifest."""
    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    homepage: str = ""
    entry: str = "__init__.py"
    api_version: str = "1"
    priority: int = 100
    permissions: dict[str, Any] = field(default_factory=dict)
    dependencies: addon_runtime.AddonDependencies = field(default_factory=addon_runtime.AddonDependencies)
    events: list[str] = field(default_factory=list)
    intents: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    hotkeys: list[dict[str, Any]] = field(default_factory=list)
    settings: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    actions: tuple[AddonActionFile, ...] = ()
    action_issues: tuple[LoadIssue, ...] = ()


@dataclass
class LoadedAddon:
    """Model loaded addon."""
    id: str
    name: str
    path: Path
    manifest: AddonManifest
    host: AddonHostProcess | None = None
    enabled: bool = True
    status: str = "loaded"
    error: str = ""
    hooks: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    tray_actions: list[str] = field(default_factory=list)
    intents: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    hotkeys: list[dict[str, Any]] = field(default_factory=list)
    settings: list[dict[str, Any]] = field(default_factory=list)
    runtime_status: dict[str, Any] = field(default_factory=dict)
    runtime_python: Path | None = None


@dataclass
class AppContext:
    """Context object passed to addon lifecycle hooks."""

    signals: Any
    model_tool_registry: Any
    config: Any


class AddonHostProcess:
    """Model addon host process."""
    def __init__(self, addon: LoadedAddon, *, timeout: float = _HOST_TIMEOUT_SECONDS) -> None:
        """Initialize the addon host process instance."""
        self.addon = addon
        self.timeout = timeout
        self._lock = threading.Lock()
        self._logs_lock = threading.Lock()
        self._logs: deque[str] = deque(maxlen=200)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"addon-{addon.id}")
        self._proc: subprocess.Popen[str] | None = None
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn the out-of-process addon host (no-op if already running)."""
        if self._proc and self._proc.poll() is None:
            return
        cmd = [
            str(self.addon.runtime_python or sys.executable),
            "-m",
            "core.addon_host",
            "--id",
            self.addon.id,
            "--folder",
            str(self.addon.path),
            "--entry",
            self.addon.manifest.entry,
            "--store",
            str(addon_store.store_path()),
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        self._proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if self._proc.stderr is not None:
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(self._proc.stderr,),
                name=f"addon-{self.addon.id}-stderr",
                daemon=True,
            )
            self._stderr_thread.start()

    def stop(self) -> None:
        """Run the addon's on_shutdown hook, then terminate (or kill) the host process."""
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                self.call("on_shutdown", {}, timeout=1.0)
            except Exception:
                pass
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    def restart(self) -> None:
        """Handle restart for addon host process."""
        self.stop()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"addon-{self.addon.id}")
        self.start()

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        """Invoke an addon RPC method on the host, killing it if it exceeds the timeout."""
        self.start()
        future = self._executor.submit(self._raw_call, method, params or {})
        try:
            return future.result(timeout=self.timeout if timeout is None else timeout)
        except TimeoutError as exc:
            self._kill_for_timeout(method)
            raise TimeoutError(f"addon {self.addon.id} timed out during {method}") from exc

    def _raw_call(self, method: str, params: dict[str, Any]) -> Any:
        """Handle raw call for addon host process."""
        with self._lock:
            proc = self._proc
            if proc is None or proc.stdin is None or proc.stdout is None or proc.poll() is not None:
                raise RuntimeError(f"addon host is not running: {self.addon.id}")
            req_id = uuid.uuid4().hex
            proc.stdin.write(json.dumps({"id": req_id, "method": method, "params": params}, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            while True:
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError(f"addon host exited: {self.addon.id}")
                reply = json.loads(line)
                if reply.get("id") not in {req_id, None}:
                    continue
                if reply.get("error"):
                    raise RuntimeError(str(reply["error"]).strip())
                return reply.get("result")

    def _kill_for_timeout(self, method: str) -> None:
        """Handle kill for timeout for addon host process."""
        proc = self._proc
        if proc and proc.poll() is None:
            message = f"{self.addon.id} timed out in {method}; killing host"
            self._append_log(message)
            _terminal(message)
            proc.kill()
        self._proc = None

    def log_text(self) -> str:
        """Log text."""
        with self._logs_lock:
            return "\n".join(self._logs)

    def _read_stderr(self, stream: Any) -> None:
        """Read stderr."""
        try:
            for line in stream:
                self._append_log(str(line).rstrip("\r\n"))
        except Exception:
            pass

    def _append_log(self, line: str) -> None:
        """Append log."""
        line = line.strip()
        if not line:
            return
        with self._logs_lock:
            self._logs.append(line)


class AddonManager:
    """Coordinate addon manager behavior."""
    def __init__(
        self,
        addons_dir: Path | None = None,
        bundled_addons_dir: Path | None = None,
        *,
        require_approval: bool | None = None,
    ):
        """Initialize the addon manager instance."""
        self._dir = addons_dir or ADDONS_DIR
        self._bundled_addons_dir = bundled_addons_dir or BUNDLED_ADDONS_DIR
        self._require_approval = (
            _same_path(self._dir, ADDONS_DIR) if require_approval is None else bool(require_approval)
        )
        self._seed_bundled_defaults = bundled_addons_dir is not None or (
            not str(os.environ.get("OPENWAND_ADDONS_DIR") or "").strip()
            and _same_path(self._dir, ADDONS_DIR)
        )
        self._mods: list[LoadedAddon] = []  # compatibility name used by callers/tests
        self._tool_registry: Any = None

    def load_all(self) -> None:
        """Load all."""
        self.shutdown_hosts()
        self._mods = []
        if not self._dir.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
        self._seed_default_addons()
        for child in sorted(p for p in self._dir.iterdir() if p.is_dir()):
            self._load_addon(child)

    def _seed_default_addons(self) -> None:
        """Copy bundled default addons into the writable addon folder when absent."""
        if not self._seed_bundled_defaults:
            return
        bundled_root = self._bundled_addons_dir
        if not bundled_root.exists():
            return
        for folder_name in _DEFAULT_BUNDLED_ADDONS:
            source = bundled_root / folder_name
            target = self._dir / folder_name
            if not source.is_dir() or target.exists():
                continue
            try:
                if source.resolve() == target.resolve():
                    continue
                shutil.copytree(source, target)
                _terminal(f"seeded default addon {folder_name}")
            except Exception:
                log.error("[addons] Failed to seed bundled addon %s:\n%s", folder_name, traceback.format_exc())

    def _load_addon(self, folder: Path) -> None:
        """Load addon."""
        try:
            manifest = load_manifest(folder)
            if manifest.id in _RETIRED_ADDON_IDS:
                _terminal(f"ignored retired addon {manifest.id}")
                return
            enabled = addon_store.is_enabled(manifest.id, True)
            addon = LoadedAddon(
                id=manifest.id,
                name=manifest.name,
                path=folder,
                manifest=manifest,
                enabled=enabled,
            )
            if enabled:
                self._activate_addon(addon)
            else:
                addon.status = "disabled"
            self._mods.append(addon)
            _terminal(f"loaded {manifest.id} enabled={enabled}")
        except Exception:
            fallback_id = _valid_id(folder.name)
            self._mods.append(
                LoadedAddon(
                    id=fallback_id,
                    name=folder.name,
                    path=folder,
                    manifest=AddonManifest(id=fallback_id, name=folder.name),
                    enabled=False,
                    status="error",
                    error=traceback.format_exc(),
                )
            )
            log.error("[addons] Failed to load addon at %s:\n%s", folder, traceback.format_exc())
            _terminal(f"failed to load {folder.name}")

    def _is_bundled_addon(self, folder: Path) -> bool:
        """Return true only when a default addon's shipped code matches the bundle."""
        if folder.name not in _DEFAULT_BUNDLED_ADDONS:
            return False
        source = self._bundled_addons_dir / folder.name
        if not source.is_dir():
            return False
        try:
            if source.resolve() == folder.resolve():
                return True
        except OSError:
            return False
        source_files = _bundled_code_files(source)
        target_files = _bundled_code_files(folder)
        if source_files.keys() != target_files.keys():
            return False
        return all(source_files[name] == target_files[name] for name in source_files)

    def on_startup(self, app_context: AppContext) -> None:
        """Handle startup events."""
        self._tool_registry = app_context.model_tool_registry
        for addon in self._enabled_addons():
            if addon.host is None:
                continue
            _call_host(addon, "on_startup", {"data_dir": str(_data_dir(addon.id))}, timeout=3.0)
            self._register_tools(addon)
        self.dispatch_event("app.startup", {})

    def on_shutdown(self) -> None:
        """Handle shutdown events."""
        self.dispatch_event("app.shutdown", {})
        self.shutdown_hosts()

    def shutdown_hosts(self) -> None:
        """Handle shutdown hosts for addon manager."""
        for addon in getattr(self, "_mods", []):
            if addon.host is not None:
                addon.host.stop()
                addon.host = None

    def before_query(self, prompt: str, context_snapshot: str) -> tuple[str, str]:
        """Handle before query for addon manager."""
        for addon in self._enabled_addons():
            if addon.host is None:
                continue
            query_perm = str(addon.manifest.permissions.get("query") or "none").lower()
            if query_perm not in {"read", "modify"}:
                continue
            result = _call_host(
                addon,
                "before_query",
                {"prompt": prompt, "context": context_snapshot},
            )
            if isinstance(result, dict):
                if query_perm == "modify":
                    prompt = str(result.get("prompt", prompt))
                    context_snapshot = str(result.get("context", context_snapshot))
        return prompt, context_snapshot

    def after_response(self, response_text: str) -> None:
        """Handle after response for addon manager."""
        for addon in self._enabled_addons():
            response_perm = str(addon.manifest.permissions.get("response") or "none").lower()
            if addon.host is not None and response_perm in {"read", "modify"}:
                _call_host(addon, "after_response", {"text": response_text})
        self.dispatch_event("response.after", {"text": response_text})

    def transform_response_text(self, payload: dict[str, Any] | None = None) -> str:
        """Let explicitly permitted addons replace assistant response text."""
        request = _safe_response_transform_payload(payload or {})
        text = request.get("text", "")
        if not text:
            return text
        for addon in self._enabled_addons():
            response_perm = str(addon.manifest.permissions.get("response") or "none").lower()
            if addon.host is None or response_perm != "modify" or not _action_surface_enabled(
                addon, "response_transform"
            ):
                continue
            result = _call_host(addon, "transform_response_text", request, timeout=3.0)
            replacement: str | None = None
            if isinstance(result, str):
                replacement = result
            elif isinstance(result, dict) and "text" in result:
                replacement = str(result.get("text") or "")
            if replacement is not None:
                text = replacement
                request["text"] = text
        return text

    def dispatch_event(self, event: str, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Dispatch event."""
        results: list[dict[str, Any]] = []
        event = str(event or "").strip()
        if not event:
            return results
        for addon in self._enabled_addons():
            if addon.host is None or event not in addon.manifest.events:
                continue
            result = _call_host(addon, "on_event", {"event": event, "payload": payload or {}})
            if isinstance(result, dict):
                results.append({"addon_id": addon.id, **result})
            elif isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        results.append({"addon_id": addon.id, **item})
        return results

    def get_tray_actions(self) -> list[dict[str, Any]]:
        """Return tray actions."""
        actions: list[dict[str, Any]] = []
        for addon in self._enabled_addons():
            if not _has_ui_permission(addon, "tray"):
                continue
            labels = addon.tray_actions
            if addon.host is not None:
                labels = _safe_list(_call_host(addon, "get_tray_actions"))
                addon.tray_actions = labels
            for label in labels:
                actions.append({"label": label, "callback": _make_action_callback(addon, label)})
        return actions

    def run_tray_action(self, name: str, label: str) -> dict[str, Any]:
        """Run tray action."""
        addon = self._find(name)
        if addon is None or addon.host is None or not addon.enabled:
            raise ValueError(f"Addon not loaded: {name}")
        if not _has_ui_permission(addon, "tray"):
            raise PermissionError(f"Addon is missing ui tray permission: {name}")
        result = _call_host(addon, "run_tray_action", {"label": label}, timeout=5.0)
        return _safe_tray_action_result(result)

    def get_intents(self, caller_idx: int | None = None) -> list[dict[str, Any]]:
        """Return intents."""
        intents: list[dict[str, Any]] = []
        for addon in self._enabled_addons():
            if not _has_ui_permission(addon, "intents"):
                continue
            for intent in addon.intents:
                if not isinstance(intent, dict):
                    continue
                normalized = _normalize_intent(addon.id, intent)
                if normalized is None:
                    continue
                target = normalized.get("caller")
                if caller_idx is not None and target not in {"", "all", str(caller_idx)}:
                    continue
                intents.append(normalized)
        return intents

    def run_intent(self, name: str, intent_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run intent."""
        addon = self._find(name)
        if addon is None or addon.host is None or not addon.enabled:
            raise ValueError(f"Addon not loaded: {name}")
        if not _has_ui_permission(addon, "intents"):
            raise PermissionError(f"Addon is missing ui intents permission: {name}")
        action = _find_action(addon, "intent", intent_id)
        if action is not None and not action.enabled:
            raise PermissionError(f"Addon intent is disabled: {name} / {intent_id}")
        handler = action.handler if action is not None else intent_id
        result = _call_host(addon, "run_intent", {"id": handler, "payload": payload or {}}, timeout=8.0)
        return result if isinstance(result, dict) else {}

    def get_hotkeys(self) -> list[dict[str, Any]]:
        """Return hotkeys."""
        hotkeys: list[dict[str, Any]] = []
        for addon in self._enabled_addons():
            if not _has_permission(addon, "hotkeys"):
                continue
            for item in addon.hotkeys:
                if isinstance(item, dict):
                    hotkeys.append({"addon_id": addon.id, **item})
        return hotkeys

    def run_hotkey(self, name: str, hotkey_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run hotkey."""
        addon = self._find(name)
        if addon is None or addon.host is None or not addon.enabled:
            raise ValueError(f"Addon not loaded: {name}")
        if not _has_permission(addon, "hotkeys"):
            raise PermissionError(f"Addon is missing hotkeys permission: {name}")
        for item in addon.hotkeys:
            if str(item.get("id") or "") == hotkey_id and str(item.get("prompt") or "").strip():
                return {"prompt": str(item.get("prompt") or "").strip()}
        result = _call_host(addon, "run_hotkey", {"id": hotkey_id, "payload": payload or {}}, timeout=8.0)
        return result if isinstance(result, dict) else {}

    def get_notifications(self) -> list[dict[str, Any]]:
        """Return notifications."""
        notifications: list[dict[str, Any]] = []
        for addon in self._enabled_addons():
            if not _has_ui_permission(addon, "notifications"):
                continue
            for item in addon.notifications:
                if isinstance(item, dict):
                    notifications.append({"addon_id": addon.id, **item})
        return notifications

    def get_text_annotations(self, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return sanitized display-only annotations from permitted addons."""
        request = _safe_text_annotation_payload(payload or {})
        text = request.get("text", "")
        annotations: list[dict[str, Any]] = []
        if not text:
            return annotations
        from ui.text_annotations import MAX_ANNOTATIONS, normalize_range_annotations

        remaining = MAX_ANNOTATIONS
        for addon in self._enabled_addons():
            if remaining <= 0:
                break
            if addon.host is None or not _has_text_annotation_permission(addon):
                continue
            result = _call_host(addon, "get_text_annotations", request, timeout=2.0)
            raw_items = result if isinstance(result, list) else []
            normalized = normalize_range_annotations(raw_items, text, surface=request.get("surface", "chat"), limit=remaining)
            for annotation in normalized:
                item = dict(annotation.__dict__)
                item["source"] = f"addon:{addon.id}"
                item["message_id"] = item.get("message_id") or request.get("message_id", "")
                item["conversation_id"] = item.get("conversation_id") or request.get("conversation_id", "")
                annotations.append(item)
            remaining = MAX_ANNOTATIONS - len(annotations)
        return annotations

    def get_text_context_actions(self, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return sanitized text context-menu actions from permitted addons."""
        request = _safe_text_context_action_payload(payload or {})
        if not request.get("selected_text", ""):
            return []
        actions: list[dict[str, Any]] = []
        for addon in self._enabled_addons():
            if len(actions) >= 12:
                break
            if addon.host is None or not _has_text_context_menu_permission(addon):
                continue
            result = _call_host(addon, "get_text_context_actions", request, timeout=2.0)
            for item in _safe_list(result):
                if len(actions) >= 12:
                    break
                normalized = _safe_text_context_action(addon.id, item)
                if normalized is not None:
                    actions.append(normalized)
        return actions

    def get_message_actions(self, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return safe actions addons may place beside a chat message."""
        request = _safe_message_action_payload(payload or {}, include_text=False)
        actions: list[dict[str, Any]] = []
        for addon in self._enabled_addons():
            if len(actions) >= 12:
                break
            if addon.host is None or not _has_ui_permission(addon, "message_actions"):
                continue
            result = _call_host(addon, "get_message_actions", request, timeout=2.0)
            for item in _safe_list(result):
                normalized = _safe_message_action(addon.id, item)
                if normalized is not None:
                    declaration = _matching_action(addon, "message_action", normalized["id"])
                    declarations = _actions_of_kind(addon, "message_action")
                    if declarations and (declaration is None or not declaration.enabled):
                        continue
                    if declaration is not None:
                        normalized["id"] = declaration.id
                        normalized["label"] = declaration.label
                        normalized["access"] = [entry.value for entry in declaration.access]
                        normalized["access_colour"] = declaration.colour
                        normalized["action_file"] = declaration.to_dict()
                    actions.append(normalized)
                if len(actions) >= 12:
                    break
        return actions

    def run_message_action(
        self,
        addon_id: str,
        action_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a permitted addon action for one concrete chat message."""
        addon = self._find(addon_id)
        if addon is None or addon.host is None or not addon.enabled:
            raise ValueError(f"Addon not loaded: {addon_id}")
        if not _has_ui_permission(addon, "message_actions"):
            raise PermissionError(f"Addon is missing ui message_actions permission: {addon_id}")
        action = _find_action(addon, "message_action", action_id)
        if action is not None and not action.enabled:
            raise PermissionError(f"Addon message action is disabled: {addon_id} / {action_id}")
        handler = action.handler if action is not None else action_id
        request = _safe_message_action_payload(payload or {}, include_text=True)
        result = _call_host(
            addon,
            "run_message_action",
            {"action_id": str(handler or "")[:80], "payload": request},
            timeout=5.0,
        )
        return _safe_message_action_result(result)

    def resume_message_action(
        self,
        addon_id: str,
        action_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resume a message action after the host completes its requested work."""
        addon = self._find(addon_id)
        if addon is None or addon.host is None or not addon.enabled:
            raise ValueError(f"Addon not loaded: {addon_id}")
        if not _has_ui_permission(addon, "message_actions"):
            raise PermissionError(f"Addon is missing ui message_actions permission: {addon_id}")
        action = _find_action(addon, "message_action", action_id)
        if action is not None and not action.enabled:
            raise PermissionError(f"Addon message action is disabled: {addon_id} / {action_id}")
        handler = action.handler if action is not None else action_id
        result = _call_host(
            addon,
            "resume_message_action",
            {
                "action_id": str(handler or "")[:80],
                "payload": _safe_message_action_resume_payload(payload or {}),
            },
            timeout=5.0,
        )
        return _safe_message_action_result(result)

    def mod_names(self) -> list[str]:
        """Handle mod names for addon manager."""
        return [m.name for m in self._mods]

    def is_enabled(self, name: str) -> bool:
        """Return whether enabled is true."""
        addon = self._find(name)
        return bool(addon and addon.enabled)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Set enabled."""
        addon = self._find(name)
        if addon is None:
            return False
        enabled = bool(enabled)
        if addon.enabled == enabled:
            return enabled
        addon_store.set_enabled(addon.id, enabled)
        addon.enabled = enabled
        if self._tool_registry is not None:
            self._tool_registry.unregister_source(f"addon:{addon.id}")
        if enabled:
            self._activate_addon(addon)
            if self._tool_registry is not None:
                self._register_tools(addon)
                if addon.host is not None:
                    _call_host(addon, "on_startup", {"data_dir": str(_data_dir(addon.id))}, timeout=3.0)
        elif addon.host is not None:
            addon.host.stop()
            addon.host = None
            addon.status = "disabled"
            addon.hooks = []
            addon.tray_actions = []
            addon.tools = []
            addon.intents = []
            addon.notifications = []
            addon.hotkeys = []
            addon.settings = []
        return enabled

    def repair_environment(self, name: str) -> dict[str, Any]:
        """Handle repair environment for addon manager."""
        addon = self._find(name)
        if addon is None:
            raise ValueError(f"Addon not loaded: {name}")
        if self._access_status(addon).get("needs_approval"):
            raise PermissionError(f"Review this addon's access before installing its components: {name}")
        if addon.host is not None:
            addon.host.stop()
            addon.host = None
        try:
            addon.runtime_status = addon_runtime.provision_environment(
                addon.id,
                addon.manifest.dependencies,
                force=True,
            )
            addon.runtime_status = self._runtime_status(addon)
            addon.error = ""
        except Exception as exc:
            addon.runtime_status = addon_runtime.environment_status(addon.id, addon.manifest.dependencies)
            addon.runtime_status["error"] = str(exc)
            addon.error = f"Dependency environment failed: {exc}"
            addon.status = "needs_dependencies"
            return addon.runtime_status
        if addon.enabled:
            self._activate_addon(addon)
            if self._tool_registry is not None:
                self._tool_registry.unregister_source(f"addon:{addon.id}")
                if addon.host is not None:
                    _call_host(addon, "on_startup", {"data_dir": str(_data_dir(addon.id))}, timeout=3.0)
                self._register_tools(addon)
        return addon.runtime_status

    def approve_addon(self, name: str) -> dict[str, Any]:
        """Accept the addon's current access claims and activate it."""
        addon = self._find(name)
        if addon is None:
            raise ValueError(f"Addon not loaded: {name}")
        claims = addon_access_claims(addon.manifest)
        addon_store.set_approved_access(
            addon.id,
            [claim["id"] for claim in claims],
            author=addon.manifest.author,
        )
        if addon.enabled and addon.manifest.dependencies.has_dependencies:
            status = addon_runtime.environment_status(addon.id, addon.manifest.dependencies)
            if not status.get("ready"):
                try:
                    addon.runtime_status = addon_runtime.provision_environment(
                        addon.id,
                        addon.manifest.dependencies,
                    )
                except Exception as exc:
                    addon.runtime_status = addon_runtime.environment_status(
                        addon.id,
                        addon.manifest.dependencies,
                    )
                    addon.runtime_status["error"] = str(exc)
                    addon.error = f"Addon components failed to install: {exc}"
                    addon.status = "needs_dependencies"
                    return self.payload(addon, refresh_host=False, resolve_dynamic_options=False)
        if addon.enabled:
            self._activate_addon(addon)
            if self._tool_registry is not None:
                self._tool_registry.unregister_source(f"addon:{addon.id}")
                if addon.host is not None:
                    _call_host(addon, "on_startup", {"data_dir": str(_data_dir(addon.id))}, timeout=3.0)
                self._register_tools(addon)
        return self.payload(addon, refresh_host=False, resolve_dynamic_options=False)

    def get_settings(
        self,
        name: str,
        *,
        refresh_host: bool = True,
        resolve_dynamic_options: bool = True,
    ) -> list[dict[str, Any]]:
        """Return settings."""
        addon = self._find(name)
        if addon is None:
            return []
        descriptors: list[dict[str, Any]] = []
        descriptors.extend(dict(s) for s in addon.manifest.settings if isinstance(s, dict))
        if addon.enabled and addon.host is not None and _has_ui_permission(addon, "settings"):
            if refresh_host:
                addon.settings = [
                    dict(s)
                    for s in _safe_list(_call_host(addon, "get_settings"))
                    if isinstance(s, dict)
                ]
            descriptors.extend(dict(s) for s in addon.settings if isinstance(s, dict))
        out: list[dict[str, Any]] = []
        for item in descriptors:
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            if resolve_dynamic_options and str(item.get("type") or "").strip().lower() == "ollama_model":
                try:
                    from core.llm_clients.client import safe_list_models

                    models, error = safe_list_models("ollama")
                except Exception as exc:
                    models, error = [], f"{type(exc).__name__}: {exc}"
                if models:
                    item["type"] = "choice"
                    item["options"] = list(dict.fromkeys(str(model) for model in models if str(model)))
                    item["default"] = str(item.get("default") or item["options"][0])
                else:
                    item["type"] = "text"
                    if error:
                        original_help = str(item.get("help") or "").strip()
                        item["help"] = f"{original_help} Ollama: {error}".strip()
            item["value"] = addon_store.get_setting(addon.id, key, item.get("default"))
            out.append(item)
        return out

    def set_setting(self, name: str, key: str, value: Any) -> None:
        """Set setting."""
        addon = self._find(name)
        if addon is None or not str(key).strip():
            return
        addon_store.set_setting(addon.id, str(key).strip(), value)

    def set_action_enabled(self, name: str, action_id: str, enabled: bool) -> bool:
        """Persist one addon action's enabled flag and apply it live."""
        addon = self._find(name)
        if addon is None:
            return False
        action = next((item for item in addon.manifest.actions if item.id == action_id), None)
        if action is None:
            return False
        update_toml_values(Path(action.path), {"enabled": bool(enabled)})
        addon.manifest = load_manifest(addon.path)
        if self._tool_registry is not None:
            self._tool_registry.unregister_source(f"addon:{addon.id}")
        if addon.enabled:
            self._activate_addon(addon)
            if addon.host is not None:
                _call_host(addon, "on_startup", {"data_dir": str(_data_dir(addon.id))}, timeout=3.0)
            if self._tool_registry is not None:
                self._register_tools(addon)
        return bool(enabled)

    def summaries(
        self,
        *,
        refresh_host: bool = True,
        resolve_dynamic_options: bool = True,
    ) -> list[dict[str, Any]]:
        """Handle summaries for addon manager."""
        return [
            self.payload(
                addon,
                refresh_host=refresh_host,
                resolve_dynamic_options=resolve_dynamic_options,
            )
            for addon in self._mods
        ]

    def model_tool_names(self) -> list[str]:
        """Return enabled addon model-tool names without building UI payloads."""
        return [item["name"] for item in self.model_tool_payloads()]

    def model_tool_payloads(self) -> list[dict[str, Any]]:
        """Return enabled addon model-tool payloads without building UI payloads."""
        names: list[str] = []
        payloads: list[dict[str, Any]] = []
        for addon in self._enabled_addons():
            for item in addon.tools:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
                    payload = {
                        "name": name,
                        "description": str(item.get("description") or name),
                    }
                    for key in ("label", "access", "access_colour", "action_file"):
                        if key in item:
                            payload[key] = item[key]
                    payloads.append(payload)
        return payloads

    def payload(
        self,
        addon: LoadedAddon,
        *,
        refresh_host: bool = True,
        resolve_dynamic_options: bool = True,
    ) -> dict[str, Any]:
        """Handle payload for addon manager."""
        tray_actions = list(addon.tray_actions)
        if refresh_host and addon.host is not None and _has_ui_permission(addon, "tray"):
            tray_actions = _safe_list(_call_host(addon, "get_tray_actions"))
            addon.tray_actions = tray_actions
        return {
            "id": addon.id,
            "name": addon.name,
            "author": addon.manifest.author,
            "homepage": addon.manifest.homepage,
            "path": str(addon.path),
            "status": addon.status,
            "enabled": bool(addon.enabled),
            "hooks": list(addon.hooks),
            "tray_actions": tray_actions,
            "tools": [str(t.get("name") or "") for t in addon.tools if isinstance(t, dict)],
            "actions": [action.to_dict() for action in addon.manifest.actions],
            "action_issues": [issue.to_dict() for issue in addon.manifest.action_issues],
            "intents": list(addon.intents),
            "notifications": list(addon.notifications),
            "hotkeys": list(addon.hotkeys),
            "settings": self.get_settings(
                addon.id,
                refresh_host=refresh_host,
                resolve_dynamic_options=resolve_dynamic_options,
            ),
            "permissions": addon.manifest.permissions,
            "dependencies": {
                "python": addon.manifest.dependencies.python,
                "packages": list(addon.manifest.dependencies.packages),
            },
            "runtime": dict(addon.runtime_status or self._runtime_status(addon)),
            "approval": self._access_status(addon),
            "disk": _addon_disk_usage(addon),
            "description": addon.manifest.description,
            "error": addon.error,
            "logs": addon.host.log_text() if addon.host is not None else "",
        }

    def _register_tools(self, addon: LoadedAddon) -> None:
        """Handle register tools for addon manager."""
        if self._tool_registry is None:
            return
        from core.tool_registry import ToolSpec

        for item in addon.tools:
            name = str(item.get("name") or "").strip()
            if not name or not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
                continue
            spec = ToolSpec(
                name=name,
                description=str(item.get("description") or name),
                input_schema=item.get("input_schema") or {"type": "object", "properties": {}, "required": []},
                executor=_make_tool_executor(addon, name) if item.get("has_executor") else None,
                source=f"addon:{addon.id}",
            )
            self._tool_registry.register_builtin(spec)

    def _enabled_addons(self) -> list[LoadedAddon]:
        """Handle enabled addons for addon manager."""
        return sorted(
            [addon for addon in self._mods if addon.enabled and addon.status != "error"],
            key=lambda addon: (addon.manifest.priority, addon.id),
        )

    def _find(self, name: str) -> LoadedAddon | None:
        """Return the loaded addon matching *name* by id or name, else None."""
        for addon in self._mods:
            if addon.id == name or addon.name == name:
                return addon
        return None

    def _activate_addon(self, addon: LoadedAddon) -> bool:
        """Handle activate addon for addon manager."""
        if addon.host is not None:
            addon.host.stop()
            addon.host = None
        addon.runtime_status = self._runtime_status(addon)
        addon.runtime_python = None
        access_status = self._access_status(addon)
        if access_status.get("needs_approval"):
            addon.status = "needs_approval"
            addon.error = "Review this addon's author and requested access before activation."
            addon.host = None
            addon.hooks = []
            addon.tray_actions = []
            addon.tools = []
            addon.intents = []
            addon.notifications = []
            addon.hotkeys = []
            addon.settings = []
            return False
        if addon.manifest.dependencies.has_dependencies:
            if not addon.runtime_status.get("ready"):
                addon.status = "needs_dependencies"
                addon.error = str(addon.runtime_status.get("error") or "Dependency environment is not ready.")
                addon.host = None
                addon.hooks = []
                addon.tray_actions = []
                addon.tools = []
                addon.intents = []
                addon.notifications = []
                addon.hotkeys = []
                addon.settings = []
                return False
            addon.runtime_python = Path(str(addon.runtime_status.get("python") or ""))

        addon.error = ""
        addon.host = AddonHostProcess(addon)
        addon.hooks = _safe_list(_call_host(addon, "hooks", {}, timeout=3.0))
        addon.tray_actions = (
            _safe_list(_call_host(addon, "get_tray_actions"))
            if _has_ui_permission(addon, "tray")
            else []
        )
        addon.tools = (
            _apply_tool_catalog(addon, _safe_tool_specs(_call_host(addon, "get_tools")))
            if _has_permission(addon, "tools")
            else []
        )
        addon.intents = _safe_intents(addon, _call_host(addon, "get_intents")) if _has_ui_permission(addon, "intents") else []
        addon.notifications = (
            _safe_notifications(addon, _call_host(addon, "get_notifications"))
            if _has_ui_permission(addon, "notifications")
            else []
        )
        addon.hotkeys = _safe_hotkeys(addon, _call_host(addon, "get_hotkeys")) if _has_permission(addon, "hotkeys") else []
        addon.settings = (
            [
                dict(item)
                for item in _safe_list(_call_host(addon, "get_settings"))
                if isinstance(item, dict)
            ]
            if _has_ui_permission(addon, "settings")
            else []
        )
        if addon.error:
            addon.status = "error"
            if addon.host is not None:
                addon.host.stop()
                addon.host = None
            return False
        addon.status = "loaded"
        return True

    def _runtime_status(self, addon: LoadedAddon) -> dict[str, Any]:
        """Handle runtime status for addon manager."""
        return addon_runtime.environment_status(addon.id, addon.manifest.dependencies)

    def _access_status(self, addon: LoadedAddon) -> dict[str, Any]:
        """Return current and newly requested access without importing addon code."""
        claims = addon_access_claims(addon.manifest)
        approved = addon_store.approved_access_ids(addon.id)
        current_ids = {claim["id"] for claim in claims}
        needs_review = self._require_approval and not self._is_bundled_addon(addon.path)
        new_ids = current_ids - approved if needs_review else set()
        return {
            "approved": not new_ids,
            "needs_approval": bool(new_ids),
            "access": claims,
            "new_access": [claim for claim in claims if claim["id"] in new_ids],
            "full_trust": True,
        }


_PERMISSION_LABELS = {
    "query:read": "Read prompts and gathered context",
    "query:modify": "Read and change prompts or gathered context",
    "response:read": "Read AI responses",
    "response:modify": "Read and change AI responses",
    "tools": "Add tools the AI can call",
    "llm": "Send requests through your configured AI services",
    "hotkeys": "Register global hotkeys",
    "ui:tray": "Add controls to the system tray",
    "ui:settings": "Add settings to OpenWand",
    "ui:intents": "Add commands to the prompt menu",
    "ui:notifications": "Show notifications",
    "ui:text_annotations": "Add labels to displayed text",
    "ui:text_context_menu": "Add actions to text menus",
    "ui:message_actions": "Add actions to AI messages",
    "ui:message_presentations": "Change how AI messages are presented",
    "action:text": "Use text supplied to an addon action",
    "action:files": "Access files through an addon action",
    "action:internet": "Access the internet through an addon action",
    "action:programs": "Control programs through an addon action",
}


def addon_access_claims(manifest: AddonManifest) -> list[dict[str, str]]:
    """Build stable, human-readable access claims from manifest-only data."""
    claim_ids: set[str] = {"full_code"}
    permissions = manifest.permissions
    for key, value in permissions.items():
        permission = str(key).strip().casefold()
        if not permission or value is False or value is None or str(value).strip().casefold() in {"", "none", "false"}:
            continue
        if permission in {"query", "response"}:
            mode = str(value).strip().casefold()
            if mode in {"read", "modify"}:
                claim_ids.add(f"{permission}:{mode}")
            continue
        if permission == "ui":
            if isinstance(value, list):
                claim_ids.update(f"ui:{str(item).strip().casefold()}" for item in value if str(item).strip())
            elif bool(value):
                claim_ids.add("ui:all")
            continue
        if bool(value):
            claim_ids.add(permission)
    for action in manifest.actions:
        claim_ids.update(f"action:{item.value}" for item in action.access)

    claims: list[dict[str, str]] = []
    for claim_id in sorted(claim_ids):
        if claim_id == "full_code":
            label = "Run addon code with your user account's access"
        else:
            label = _PERMISSION_LABELS.get(claim_id, _humanize_access_id(claim_id))
        claims.append({"id": claim_id, "label": label})
    return claims


def _humanize_access_id(claim_id: str) -> str:
    """Turn an uncommon manifest claim into a readable fallback label."""
    return claim_id.replace(":", " ").replace("_", " ").strip().capitalize()


def _addon_disk_usage(addon: LoadedAddon) -> dict[str, Any]:
    """Return current on-disk size, including the private environment when present."""
    addon_bytes = _directory_size(addon.path)
    environment = addon_runtime.env_path(addon.id)
    environment_bytes = _directory_size(environment) if environment.exists() else 0
    total = addon_bytes + environment_bytes
    dependencies_pending = bool(
        addon.manifest.dependencies.has_dependencies
        and not addon_runtime.environment_status(addon.id, addon.manifest.dependencies).get("ready")
    )
    return {
        "addon_bytes": addon_bytes,
        "environment_bytes": environment_bytes,
        "total_bytes": total,
        "label": _format_bytes(total),
        "dependencies_pending": dependencies_pending,
    }


def _directory_size(path: Path) -> int:
    """Best-effort recursive directory size without following symlinks."""
    total = 0
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
            for name in files:
                target = Path(root) / name
                if target.is_symlink():
                    continue
                try:
                    total += target.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _bundled_code_files(path: Path) -> dict[str, bytes]:
    """Read the Python/TOML surface used to identify an untouched bundled addon."""
    files: dict[str, bytes] = {}
    try:
        for target in sorted(path.rglob("*")):
            if not target.is_file() or target.is_symlink() or target.suffix.casefold() not in {".py", ".toml"}:
                continue
            files[target.relative_to(path).as_posix()] = target.read_bytes()
    except OSError:
        return {}
    return files


def _format_bytes(value: int) -> str:
    """Format byte counts for the addon review UI."""
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def load_manifest(folder: Path) -> AddonManifest:
    """Load manifest."""
    path = _first_existing(folder / "addon.toml", folder / "plugin.toml")
    if path is None:
        legacy = folder / "__init__.py"
        if legacy.exists():
            addon_id = _valid_id(folder.name)
            return AddonManifest(id=addon_id, name=folder.name, entry="__init__.py")
        raise FileNotFoundError("missing addon.toml")
    data = _load_toml(path)
    plugin = data.get("addon") or data.get("plugin") or {}
    if not isinstance(plugin, dict):
        plugin = {}
    addon_id = _valid_id(str(plugin.get("id") or folder.name))
    raw_settings = data.get("settings") or []
    if isinstance(raw_settings, dict):
        raw_settings = [
            {"key": key, **value} if isinstance(value, dict) else {"key": key, "default": value}
            for key, value in raw_settings.items()
        ]
    permissions = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
    raw_events = data.get("events") or permissions.get("events")
    actions, action_issues = load_addon_actions(folder)
    manifest = AddonManifest(
        id=addon_id,
        name=str(plugin.get("name") or folder.name),
        version=str(plugin.get("version") or "0.0.0"),
        description=str(plugin.get("description") or ""),
        author=str(plugin.get("author") or plugin.get("publisher") or "").strip(),
        homepage=str(plugin.get("homepage") or plugin.get("website") or "").strip(),
        entry=str(plugin.get("entry") or "__init__.py"),
        api_version=str(plugin.get("api_version") or "1"),
        priority=_safe_int(plugin.get("priority"), 100),
        permissions=permissions,
        dependencies=addon_runtime.dependencies_from_manifest(data.get("dependencies")),
        events=[str(item).strip() for item in _safe_list(raw_events) if str(item).strip()],
        intents=_safe_tool_specs(data.get("intents")),
        notifications=_safe_tool_specs(data.get("notifications")),
        hotkeys=_safe_tool_specs(data.get("hotkeys")),
        settings=raw_settings if isinstance(raw_settings, list) else [],
        tools=data.get("tools") if isinstance(data.get("tools"), list) else [],
        actions=actions,
        action_issues=action_issues,
    )
    if manifest.api_version != "1":
        raise ValueError(f"unsupported addon API version: {manifest.api_version}")
    return manifest


def addon_setting(addon_id: str, key: str, default: Any = None) -> Any:
    """Return a persisted setting for an addon."""
    return addon_store.get_setting(_valid_id(addon_id), key, default)


def init(addons_dir: Path | None = None) -> AddonManager:
    """Handle init for addon manager."""
    global _manager
    manager = AddonManager(addons_dir or ADDONS_DIR)
    manager.load_all()
    _manager = manager
    return manager


def get_manager() -> AddonManager:
    """Return manager."""
    if _manager is None:
        raise RuntimeError("AddonManager not initialised yet; call init() first.")
    return _manager


def _make_tool_executor(addon: LoadedAddon, name: str):
    """Create tool executor."""
    def _executor(inputs: dict[str, Any]) -> str:
        """Handle executor for addon manager."""
        if addon.host is None:
            addon.host = AddonHostProcess(addon)
        result = _call_host(addon, "execute_tool", {"name": name, "inputs": inputs or {}}, timeout=8.0)
        return "" if result is None else str(result)

    return _executor


def _make_action_callback(addon: LoadedAddon, label: str):
    """Create action callback."""
    def _callback() -> None:
        """Handle callback for addon manager."""
        if addon.host is None:
            addon.host = AddonHostProcess(addon)
        _call_host(addon, "run_tray_action", {"label": label}, timeout=5.0)

    return _callback


def _call_host(addon: LoadedAddon, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
    """Call host."""
    if addon.host is None:
        return None
    try:
        return addon.host.call(method, params or {}, timeout=timeout)
    except Exception as exc:
        addon.error = f"{type(exc).__name__}: {exc}"
        log.error("[addons] %s.%s failed:\n%s", addon.id, method, traceback.format_exc())
        return None


def _safe_list(value: Any) -> list[Any]:
    """Handle safe list for addon manager."""
    return value if isinstance(value, list) else []


def _safe_tool_specs(value: Any) -> list[dict[str, Any]]:
    """Handle safe tool specs for addon manager."""
    return [item for item in _safe_list(value) if isinstance(item, dict)]


def _safe_int(value: Any, default: int) -> int:
    """Handle safe int for addon manager."""
    try:
        return int(value)
    except Exception:
        return default


def _has_permission(addon: LoadedAddon, key: str) -> bool:
    """Return whether permission is available."""
    return bool(addon.manifest.permissions.get(key))


def _has_ui_permission(addon: LoadedAddon, feature: str) -> bool:
    """Return whether ui permission is available."""
    ui = addon.manifest.permissions.get("ui")
    if isinstance(ui, list):
        return feature in {str(item) for item in ui}
    return ui is True or str(ui).lower() == "true"


def _has_text_annotation_permission(addon: LoadedAddon) -> bool:
    """Return whether an addon explicitly opted into visible text annotations."""
    ui = addon.manifest.permissions.get("ui")
    return isinstance(ui, list) and "text_annotations" in {str(item) for item in ui}


def _has_text_context_menu_permission(addon: LoadedAddon) -> bool:
    """Return whether an addon explicitly opted into text context-menu actions."""
    ui = addon.manifest.permissions.get("ui")
    return isinstance(ui, list) and "text_context_menu" in {str(item) for item in ui}


def _safe_tray_action_result(value: Any) -> dict[str, Any]:
    """Allow tray callbacks to request only bounded UI-owned effects."""
    from urllib.parse import urlsplit

    out: dict[str, Any] = {}
    if not isinstance(value, dict):
        return out
    message = str(value.get("message") or "").replace("\x00", "").strip()[:240]
    if message:
        out["message"] = message
    raw_url = str(value.get("virtual_workspace_url") or "").strip()
    if not raw_url or len(raw_url) > 4096 or "\x00" in raw_url or "\r" in raw_url or "\n" in raw_url:
        return out
    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError:
        return out
    if (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and parsed.username is None
        and parsed.password is None
        and port is not None
        and 1 <= port <= 65535
    ):
        out["virtual_workspace_url"] = raw_url
    return out


def _safe_message_action(addon_id: str, item: Any) -> dict[str, Any] | None:
    """Normalize one unobtrusive action shown beneath assistant messages."""
    if not isinstance(item, dict):
        return None
    action_id = str(item.get("id") or "").replace("\x00", "").strip()[:80]
    label = str(item.get("label") or action_id).replace("\x00", "").strip()[:40]
    role = str(item.get("role") or "assistant").strip().lower()
    if not action_id or not label or role not in {"assistant", "user", "all"}:
        return None
    return {
        "addon_id": addon_id,
        "id": action_id,
        "label": label,
        "role": role,
        "presentation": bool(item.get("presentation")),
        "auto": bool(item.get("auto")),
        "provider": str(item.get("provider") or "").replace("\x00", "").strip()[:80],
        "model": str(item.get("model") or "").replace("\x00", "").strip()[:200],
    }


def _safe_message_action_payload(payload: dict[str, Any], *, include_text: bool) -> dict[str, Any]:
    """Limit message metadata crossing into an addon subprocess."""
    allowed = {
        "surface",
        "role",
        "message_id",
        "conversation_id",
        "user_prompt",
        "presentation_status",
    }
    if include_text:
        allowed.add("text")
    out = {key: str(payload.get(key) or "").replace("\x00", "") for key in allowed}
    out["text"] = out.get("text", "")[:120_000]
    out["user_prompt"] = out.get("user_prompt", "")[:24_000]
    return out


def _safe_message_action_resume_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Limit host-operation results returned to an addon action."""
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    safe_state: dict[str, Any] = {}
    for key, value in list(state.items())[:24]:
        clean_key = str(key or "").replace("\x00", "").strip()[:80]
        if not clean_key:
            continue
        if isinstance(value, bool) or value is None:
            safe_state[clean_key] = value
        elif isinstance(value, (int, float)):
            safe_state[clean_key] = value
        else:
            safe_state[clean_key] = str(value or "").replace("\x00", "")[:180_000]
    return {
        "operation": str(payload.get("operation") or "").strip()[:40],
        "text": str(payload.get("text") or "").replace("\x00", "")[:180_000],
        "state": safe_state,
        "input_tokens_estimate": max(0, _safe_int(payload.get("input_tokens_estimate"), 0)),
        "output_tokens_estimate": max(0, _safe_int(payload.get("output_tokens_estimate"), 0)),
        "route": str(payload.get("route") or "").replace("\x00", "").strip()[:40],
        "provider": str(payload.get("provider") or "").replace("\x00", "").strip()[:80],
        "model": str(payload.get("model") or "").replace("\x00", "").strip()[:200],
    }


def _safe_message_action_result(value: Any) -> dict[str, Any]:
    """Allow only host operations, status, and restricted-HTML presentations."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    status = str(value.get("status") or "").replace("\x00", "").strip()[:240]
    if status:
        out["status"] = status
    error_detail = str(value.get("error_detail") or "").replace("\x00", "").strip()[:1000]
    if error_detail:
        out["error_detail"] = error_detail
    provider = str(value.get("provider") or "").replace("\x00", "").strip()[:80]
    model = str(value.get("model") or "").replace("\x00", "").strip()[:200]
    if provider:
        out["provider"] = provider
    if model:
        out["model"] = model
    operation = value.get("llm")
    if isinstance(operation, dict):
        prompt = str(operation.get("prompt") or "").replace("\x00", "").strip()[:180_000]
        if prompt:
            route = str(operation.get("route") or "llm").strip().lower()
            if route not in {"llm", "chat", "chatgpt-mini", "chatgpt-nano", "ollama-local"}:
                route = "llm"
            out["llm"] = {
                "prompt": prompt,
                "max_tokens": max(1, min(_safe_int(operation.get("max_tokens"), 2048), 4096)),
                "temperature": operation.get("temperature"),
                "route": route,
            }
            operation_model = str(operation.get("model") or "").replace("\x00", "").strip()[:200]
            if operation_model:
                out["llm"]["model"] = operation_model
            state = value.get("state") if isinstance(value.get("state"), dict) else {}
            out["state"] = _safe_message_action_resume_payload({"state": state})["state"]
            return out
    presentation = value.get("presentation")
    if isinstance(presentation, dict):
        fragment = str(presentation.get("html") or "").replace("\x00", "")[:180_000]
        if fragment:
            out["presentation"] = {
                "format": "restricted_html",
                "html": fragment,
                "label": str(presentation.get("label") or "Formatted").replace("\x00", "").strip()[:40],
                "status": str(presentation.get("status") or status or "Formatted").replace("\x00", "").strip()[:120],
            }
    usage = value.get("token_usage")
    if isinstance(usage, dict):
        out["token_usage"] = {
            "formatting_input_estimate": max(0, _safe_int(usage.get("formatting_input_estimate"), 0)),
            "formatting_output_estimate": max(0, _safe_int(usage.get("formatting_output_estimate"), 0)),
            "verification_input_estimate": max(0, _safe_int(usage.get("verification_input_estimate"), 0)),
            "verification_output_estimate": max(0, _safe_int(usage.get("verification_output_estimate"), 0)),
        }
    return out


def _safe_text_annotation_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Return the visible text metadata an annotation addon may inspect."""
    allowed = {
        "text",
        "surface",
        "role",
        "message_id",
        "conversation_id",
    }
    return {key: str(payload.get(key) or "") for key in allowed}


def _safe_text_context_action_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Return selected text metadata a context-menu addon may inspect."""
    allowed = {
        "selected_text",
        "text",
        "surface",
        "role",
        "message_id",
        "conversation_id",
    }
    out = {key: str(payload.get(key) or "") for key in allowed}
    out["selected_text"] = out["selected_text"][:4000]
    out["text"] = out["text"][:12000]
    return out


def _safe_text_context_action(addon_id: str, item: Any) -> dict[str, Any] | None:
    """Return one safe text context-menu action."""
    if not isinstance(item, dict):
        return None
    label = str(item.get("label") or "").replace("\x00", "").strip()[:80]
    action = str(item.get("action") or "copy").replace("\x00", "").strip().lower()
    if not label or action not in {"copy", "label_editor", "delete_label"}:
        return None
    text = str(item.get("text") or "").replace("\x00", "")[:12000]
    if action == "copy" and not text:
        return None
    match = str(item.get("match") or "").replace("\x00", "").strip()[:160]
    if action in {"label_editor", "delete_label"} and not match:
        return None
    out = {
        "addon_id": addon_id,
        "id": str(item.get("id") or label).replace("\x00", "").strip()[:80],
        "label": label,
        "action": action,
        "text": text,
    }
    if match:
        out["match"] = match
    return out


def _safe_response_transform_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Return assistant text metadata a response-modifying addon may inspect."""
    allowed = {
        "text",
        "surface",
        "role",
        "message_id",
        "conversation_id",
    }
    return {key: str(payload.get(key) or "") for key in allowed}


def _normalize_intent(addon_id: str, item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize intent."""
    label = str(item.get("label") or item.get("name") or "").strip()
    prompt = str(item.get("prompt") or item.get("template") or "").strip()
    callback = bool(item.get("callback") or item.get("id"))
    if not label or (not prompt and not callback):
        return None
    intent_id = str(item.get("id") or _valid_id(label))
    result: dict[str, Any] = {
        "id": intent_id,
        "addon_id": addon_id,
        "key": str(item.get("key") or "").strip(),
        "label": label,
        "hint": str(item.get("hint") or item.get("description") or "").strip(),
        "prompt": prompt,
        "caller": str(item.get("caller") or "all").strip() or "all",
        "callback": callback and not prompt,
    }
    for key in ("access", "access_colour", "action_file"):
        if key in item:
            result[key] = item[key]
    return result


def _safe_intents(addon: LoadedAddon, dynamic: Any) -> list[dict[str, Any]]:
    """Handle safe intents for addon manager."""
    items = [*addon.manifest.intents, *_safe_tool_specs(dynamic)]
    out: list[dict[str, Any]] = []
    for item in items:
        normalized = _normalize_intent(addon.id, item)
        if normalized is not None:
            out.append(normalized)
    for action in _actions_of_kind(addon, "intent"):
        matches = [item for item in out if str(item.get("id") or "") == action.handler]
        if not action.enabled:
            out = [item for item in out if item not in matches]
            continue
        source = matches[0] if matches else {}
        declared = {
            "id": action.id,
            "label": action.label,
            "hint": action.hint or str(source.get("hint") or ""),
            "prompt": action.prompt or str(source.get("prompt") or ""),
            "caller": action.caller,
            "key": action.key or str(source.get("key") or ""),
            "callback": not bool(action.prompt) and bool(source.get("callback")),
            "access": [entry.value for entry in action.access],
            "access_colour": action.colour,
            "action_file": action.to_dict(),
        }
        out = [item for item in out if item not in matches]
        normalized = _normalize_intent(addon.id, declared)
        if normalized is not None:
            out.append(normalized)
    return out


def _actions_of_kind(addon: LoadedAddon, kind: str) -> tuple[AddonActionFile, ...]:
    """Return declarations of one kind in file order."""
    return tuple(action for action in addon.manifest.actions if action.kind == kind)


def _find_action(addon: LoadedAddon, kind: str, action_id: str) -> AddonActionFile | None:
    """Find a declaration by its public id."""
    return next(
        (action for action in _actions_of_kind(addon, kind) if action.id == action_id),
        None,
    )


def _matching_action(addon: LoadedAddon, kind: str, handler: str) -> AddonActionFile | None:
    """Find a declaration that owns one runtime handler."""
    return next(
        (
            action
            for action in _actions_of_kind(addon, kind)
            if action.handler == handler or action.id == handler
        ),
        None,
    )


def _action_surface_enabled(addon: LoadedAddon, kind: str) -> bool:
    """Return a declared surface state, falling back to legacy availability."""
    actions = _actions_of_kind(addon, kind)
    return not actions or any(action.enabled for action in actions)


def _apply_tool_catalog(addon: LoadedAddon, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decorate/filter runtime tool schemas using the data-only catalogue."""
    declarations = _actions_of_kind(addon, "tool")
    providers = _actions_of_kind(addon, "tool_provider")
    if not declarations and not providers:
        return tools
    out: list[dict[str, Any]] = []
    for raw in tools:
        name = str(raw.get("name") or "").strip()
        declaration = _matching_action(addon, "tool", name)
        owner = declaration or next((item for item in providers if item.enabled), None)
        if declaration is not None and not declaration.enabled:
            continue
        if declaration is None and providers and owner is None:
            continue
        if declaration is None and declarations and not providers:
            # Explicit tool declarations make the catalogue authoritative.
            continue
        item = dict(raw)
        if owner is not None:
            item["label"] = owner.label if declaration is not None else name
            item["description"] = owner.hint or str(item.get("description") or name)
            item["access"] = [entry.value for entry in owner.access]
            item["access_colour"] = owner.colour
            item["action_file"] = owner.to_dict()
        out.append(item)
    return out


def _safe_notifications(addon: LoadedAddon, dynamic: Any) -> list[dict[str, Any]]:
    """Handle safe notifications for addon manager."""
    out: list[dict[str, Any]] = []
    for item in [*addon.manifest.notifications, *_safe_tool_specs(dynamic)]:
        title = str(item.get("title") or addon.name).strip()
        message = str(item.get("message") or item.get("body") or "").strip()
        if message:
            out.append({"title": title, "message": message})
    return out


def _safe_hotkeys(addon: LoadedAddon, dynamic: Any) -> list[dict[str, Any]]:
    """Handle safe hotkeys for addon manager."""
    out: list[dict[str, Any]] = []
    for item in [*addon.manifest.hotkeys, *_safe_tool_specs(dynamic)]:
        combo = str(item.get("hotkey") or item.get("combo") or "").strip()
        label = str(item.get("label") or item.get("id") or combo).strip()
        if not combo or not label:
            continue
        hotkey_id = str(item.get("id") or _valid_id(label))
        out.append({
            "id": hotkey_id,
            "label": label,
            "hotkey": combo,
            "prompt": str(item.get("prompt") or "").strip(),
            "intent_id": str(item.get("intent_id") or "").strip(),
            "callback": bool(item.get("callback") or not item.get("prompt")),
        })
    return out


def _data_dir(addon_id: str) -> Path:
    """Handle data dir for addon manager."""
    return REPO_ROOT / "addon_data" / addon_id


def _valid_id(value: str) -> str:
    """Handle valid id for addon manager."""
    value = re.sub(r"[^a-zA-Z0-9-]+", "-", value.strip().lower()).strip("-")
    return value or "addon"


def _first_existing(*paths: Path) -> Path | None:
    """Handle first existing for addon manager."""
    for path in paths:
        if path.exists():
            return path
    return None


def _same_path(left: Path, right: Path) -> bool:
    """Return whether two paths point at the same filesystem location."""
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return left == right


def _load_toml(path: Path) -> dict[str, Any]:
    """Load toml."""
    text = _read_manifest_text(path)
    try:
        import tomllib

        return tomllib.loads(text)
    except ModuleNotFoundError:
        from core.tool_registry import _load_simple_toml

        return _load_simple_toml(text)


def _read_manifest_text(path: Path) -> str:
    """Read addon manifests, accepting common Windows-encoded punctuation."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


_manager: AddonManager | None = None
