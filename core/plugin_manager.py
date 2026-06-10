"""
core/plugin_manager.py — Mod/plugin lifecycle management for Wisp.

Mods are Python packages under plugins/<name>/__init__.py.
All hook functions are optional — a mod only needs to define the ones it uses.

SECURITY: Mods run in-process with full Python access.
Only install mods from sources you trust completely.

Available hooks (define any of these in your mod's __init__.py):

    on_startup(app_context)                        — called once after app init
    on_shutdown()                                  — called before app exits
    before_query(prompt, context) -> (prompt, ctx) — can modify before LLM call
    after_response(text)                           — called after LLM streams
    get_tray_actions() -> list[dict]               — add tray menu items
    get_tools() -> list[dict]                      — contribute model-callable tools
"""
from __future__ import annotations

import importlib.util
import logging
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("wisp.plugins")


@dataclass
class AppContext:
    """Passed to on_startup(). Store it if other hooks need app access.

    On the current worker runtime, mods run inside the headless brain process,
    so ``signals`` is ``None`` (there is no Qt there). ``model_tool_registry`` and
    ``config`` are always available.
    """
    signals: Any             # Qt signals when run in-process; None in the brain worker
    model_tool_registry: Any # ToolRegistry   — register model-callable tools
    config: Any              # config module  — read live config values


@dataclass
class _LoadedMod:
    name: str
    module: Any


class PluginManager:
    """Discovers, loads, and dispatches lifecycle hooks to all enabled mods."""

    def __init__(self, plugins_dir: Path):
        self._dir = plugins_dir
        self._mods: list[_LoadedMod] = []

    # ------------------------------------------------------------------
    # Discovery & loading
    # ------------------------------------------------------------------

    def load_all(self) -> None:
        if not self._dir.exists():
            log.debug("[mods] plugins dir %s does not exist — skipping.", self._dir)
            return
        for child in sorted(p for p in self._dir.iterdir() if p.is_dir()):
            if (child / "__init__.py").exists():
                self._load_mod(child)

    def _load_mod(self, folder: Path) -> None:
        name = folder.name
        package_name = f"plugins.{name}"
        try:
            spec = importlib.util.spec_from_file_location(
                package_name,
                folder / "__init__.py",
                submodule_search_locations=[str(folder)],
            )
            if spec is None or spec.loader is None:
                log.warning("[mods] Could not create spec for %r — skipping.", name)
                return
            module = importlib.util.module_from_spec(spec)
            sys.modules[package_name] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            self._mods.append(_LoadedMod(name=name, module=module))
            log.info("[mods] Loaded mod %r.", name)
        except Exception:
            log.error("[mods] Failed to load mod %r:\n%s", name, traceback.format_exc())

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def on_startup(self, app_context: AppContext) -> None:
        for mod in self._mods:
            _call(mod, "on_startup", app_context)
            _register_mod_tools(mod, app_context.model_tool_registry)

    def on_shutdown(self) -> None:
        for mod in self._mods:
            _call(mod, "on_shutdown")

    def before_query(self, prompt: str, context_snapshot: str) -> tuple[str, str]:
        for mod in self._mods:
            fn = getattr(mod.module, "before_query", None)
            if fn is None:
                continue
            try:
                result = fn(prompt, context_snapshot)
                if isinstance(result, tuple) and len(result) == 2:
                    prompt, context_snapshot = result
            except Exception:
                log.error("[mods] %r.before_query raised:\n%s", mod.name, traceback.format_exc())
        return prompt, context_snapshot

    def after_response(self, response_text: str) -> None:
        for mod in self._mods:
            _call(mod, "after_response", response_text)

    def get_tray_actions(self) -> list[dict]:
        actions: list[dict] = []
        for mod in self._mods:
            fn = getattr(mod.module, "get_tray_actions", None)
            if fn is None:
                continue
            try:
                items = fn()
                if isinstance(items, list):
                    actions.extend(items)
            except Exception:
                log.error("[mods] %r.get_tray_actions raised:\n%s", mod.name, traceback.format_exc())
        return actions

    def mod_names(self) -> list[str]:
        return [m.name for m in self._mods]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _call(mod: _LoadedMod, hook: str, *args: Any) -> None:
    fn = getattr(mod.module, hook, None)
    if fn is None:
        return
    try:
        fn(*args)
    except Exception:
        log.error("[mods] %r.%s raised:\n%s", mod.name, hook, traceback.format_exc())


def _register_mod_tools(mod: _LoadedMod, tool_registry: Any) -> None:
    fn = getattr(mod.module, "get_tools", None)
    if fn is None:
        return
    try:
        tools = fn()
        if not isinstance(tools, list):
            return
        from core.tool_registry import ToolSpec
        for t in tools:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name", "")).strip()
            if not name or not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
                log.warning("[mods] %r returned a tool with invalid name %r — skipping.", mod.name, name)
                continue
            spec = ToolSpec(
                name=name,
                description=str(t.get("description", name)),
                input_schema=t.get("input_schema", {"type": "object", "properties": {}, "required": []}),
                executor=t.get("executor"),
                source=f"mod:{mod.name}",
            )
            tool_registry.register_builtin(spec)
            log.info("[mods] Registered model tool %r from mod %r.", name, mod.name)
    except Exception:
        log.error("[mods] %r.get_tools raised:\n%s", mod.name, traceback.format_exc())


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_manager: PluginManager | None = None


def get_manager() -> PluginManager:
    if _manager is None:
        raise RuntimeError("PluginManager not initialised yet — call init() first.")
    return _manager


def init(plugins_dir: Path) -> PluginManager:
    global _manager
    _manager = PluginManager(plugins_dir)
    _manager.load_all()
    return _manager
