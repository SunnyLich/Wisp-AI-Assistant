"""Tests for macos py brain test handler addons."""

from __future__ import annotations

import sys
import types

from wisp_brain import handlers


def test_addons_list_handler_registered():
    """Verify addons list handler registered behavior."""
    assert "brain.addons.list" in handlers.HANDLERS
    assert "brain.addons.run_action" in handlers.HANDLERS
    assert "brain.addons.repair_environment" in handlers.HANDLERS
    assert "brain.addons.install_archive" in handlers.HANDLERS
    assert "brain.addons.install_folder" in handlers.HANDLERS
    assert "brain.addons.run_hotkey" in handlers.HANDLERS
    assert "brain.addons.llm_call" in handlers.HANDLERS


def test_addons_list_returns_discovered_addon_folder(tmp_path, monkeypatch):
    """Verify addons list returns discovered addon folder behavior."""
    addon_dir = tmp_path / "addons"
    example = addon_dir / "example"
    example.mkdir(parents=True)
    (example / "addon.toml").write_text(
        "[addon]\nid = 'example'\nname = 'Example'\nentry = '__init__.py'\n",
        encoding="utf-8",
    )
    (example / "__init__.py").write_text(
        "def before_query(prompt, context):\n"
        "    return prompt, context\n\n"
        "def get_tools():\n"
        "    return []\n",
        encoding="utf-8",
    )

    import core.addon_manager as addon_manager
    import core.system.paths as paths

    monkeypatch.setattr(paths, "ADDONS_DIR", addon_dir)
    monkeypatch.setattr(addon_manager, "_manager", None)

    result = handlers.HANDLERS["brain.addons.list"]()

    assert result["addons_dir"] == str(addon_dir)
    assert result["addons"][0]["id"] == "example"
    assert result["addons"][0]["name"] == "Example"
    assert result["addons"][0]["hooks"] == ["before_query", "get_tools"]

    addon_manager.get_manager().on_shutdown()


def test_addons_list_creates_missing_addons_folder(tmp_path, monkeypatch):
    """Addon Manager creates the install location on first open."""
    addon_dir = tmp_path / "addons"

    import core.addon_manager as addon_manager
    import core.system.paths as paths

    monkeypatch.setattr(paths, "ADDONS_DIR", addon_dir)
    monkeypatch.setattr(addon_manager, "_manager", None)

    result = handlers.HANDLERS["brain.addons.list"]()

    assert result["addons_dir"] == str(addon_dir)
    assert result["addons"] == []
    assert addon_dir.is_dir()


def test_addons_list_initializes_shared_manager_and_action_can_run(tmp_path, monkeypatch):
    """Verify addons list initializes shared manager and action can run behavior."""
    addon_dir = tmp_path / "addons"
    example = addon_dir / "native_action"
    marker = tmp_path / "ran.txt"
    example.mkdir(parents=True)
    (example / "addon.toml").write_text(
        "[addon]\nid = 'native-action'\nname = 'native_action'\nentry = '__init__.py'\n\n"
        "[permissions]\nui = ['tray']\n",
        encoding="utf-8",
    )
    (example / "__init__.py").write_text(
        "from pathlib import Path\n\n"
        "def _run():\n"
        f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n\n"
        "def get_tray_actions():\n"
        "    return [{'label': 'Do Native Thing', 'callback': _run}]\n",
        encoding="utf-8",
    )

    import core.addon_manager as addon_manager
    import core.system.paths as paths

    monkeypatch.setattr(paths, "ADDONS_DIR", addon_dir)
    monkeypatch.setattr(addon_manager, "_manager", None)

    result = handlers.HANDLERS["brain.addons.list"]()

    assert result["addons"][0]["id"] == "native-action"
    assert result["addons"][0]["tray_actions"] == ["Do Native Thing"]

    action_result = handlers.HANDLERS["brain.addons.run_action"](
        addon_id="native-action",
        label="Do Native Thing",
    )

    assert action_result == {
        "ok": True,
        "message": "Ran addon action: native-action / Do Native Thing",
    }
    assert marker.read_text(encoding="utf-8") == "ran"
    addon_manager.get_manager().on_shutdown()


def test_addons_list_prefers_loaded_manager(monkeypatch, tmp_path):
    """Verify addons list prefers loaded manager behavior."""
    manager = types.SimpleNamespace(
        summaries=lambda: [
            {
                "id": "loaded",
                "name": "loaded",
                "path": str(tmp_path / "addons" / "loaded"),
                "status": "loaded",
                "enabled": True,
                "hooks": ["get_tools", "get_tray_actions"],
                "tray_actions": ["Do Thing"],
                "tools": ["loaded_tool"],
                "settings": [],
                "permissions": {},
                "description": "",
                "error": "",
            }
        ]
    )
    fake_addon_manager = types.ModuleType("core.addon_manager")
    fake_addon_manager.get_manager = lambda: manager
    monkeypatch.setitem(sys.modules, "core.addon_manager", fake_addon_manager)

    import core.system.paths as paths

    monkeypatch.setattr(paths, "ADDONS_DIR", tmp_path / "addons")

    result = handlers.HANDLERS["brain.addons.list"]()

    assert result["addons"] == manager.summaries()


def test_addons_run_action_invokes_loaded_tray_action(monkeypatch):
    """Verify addons run action invokes loaded tray action behavior."""
    calls: list[tuple[str, str]] = []

    class FakeManager:
        """Coordinate fake manager behavior."""
        def run_tray_action(self, name: str, label: str) -> None:
            """Verify run tray action behavior."""
            calls.append((name, label))

    fake_addon_manager = types.ModuleType("core.addon_manager")
    fake_addon_manager.get_manager = lambda: FakeManager()
    monkeypatch.setitem(sys.modules, "core.addon_manager", fake_addon_manager)

    result = handlers.HANDLERS["brain.addons.run_action"](
        addon_id="loaded",
        label="Do Thing",
    )

    assert result == {"ok": True, "message": "Ran addon action: loaded / Do Thing"}
    assert calls == [("loaded", "Do Thing")]


def test_run_addon_startup_runs_once_with_app_context(monkeypatch):
    """Verify run addon startup runs once with app context behavior."""
    import core.llm_clients.client as client

    sentinel_registry = object()
    monkeypatch.setattr(client, "get_tool_registry", lambda: sentinel_registry)

    class AppContext:
        """Represent app context behavior."""
        def __init__(self, *, signals, model_tool_registry, config):
            """Initialize the app context instance."""
            self.signals = signals
            self.model_tool_registry = model_tool_registry
            self.config = config

    class FakeManager:
        """Coordinate fake manager behavior."""
        def __init__(self):
            """Initialize the fake manager instance."""
            self.startups = []

        def on_startup(self, ctx):
            """Verify on startup behavior."""
            self.startups.append(ctx)

    manager = FakeManager()
    fake_am = types.ModuleType("core.addon_manager")
    fake_am.AppContext = AppContext
    fake_am.get_manager = lambda: manager
    fake_am.init = lambda _dir: manager
    monkeypatch.setitem(sys.modules, "core.addon_manager", fake_am)
    monkeypatch.setattr(handlers, "_addon_startup_done", False)

    handlers.run_addon_startup()
    handlers.run_addon_startup()

    assert len(manager.startups) == 1
    ctx = manager.startups[0]
    assert ctx.signals is None
    assert ctx.model_tool_registry is sentinel_registry

    import config as cfg

    assert ctx.config is cfg


def test_addons_run_action_validates_inputs():
    """Verify addons run action validates inputs behavior."""
    import pytest

    with pytest.raises(ValueError, match="addon_id"):
        handlers.HANDLERS["brain.addons.run_action"](addon_id="", label="Do Thing")
    with pytest.raises(ValueError, match="label"):
        handlers.HANDLERS["brain.addons.run_action"](addon_id="loaded", label="")


def test_addons_run_action_reports_missing_action(monkeypatch):
    """Verify addons run action reports missing action behavior."""
    class FakeManager:
        """Coordinate fake manager behavior."""
        def run_tray_action(self, name: str, label: str) -> None:
            """Verify run tray action behavior."""
            raise ValueError(f"Addon action not found: {name} / {label}")

    fake_addon_manager = types.ModuleType("core.addon_manager")
    fake_addon_manager.get_manager = lambda: FakeManager()
    monkeypatch.setitem(sys.modules, "core.addon_manager", fake_addon_manager)

    import pytest

    with pytest.raises(ValueError, match="Addon action not found"):
        handlers.HANDLERS["brain.addons.run_action"](
            addon_id="loaded",
            label="Do Thing",
        )
