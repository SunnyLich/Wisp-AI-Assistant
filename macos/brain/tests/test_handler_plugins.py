from __future__ import annotations

import sys
import types

from wisp_brain import handlers


def test_plugins_list_handler_registered():
    assert "brain.plugins.list" in handlers.HANDLERS


def test_plugins_list_returns_discovered_plugin_folder(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    example = plugin_dir / "example"
    example.mkdir(parents=True)
    (example / "__init__.py").write_text(
        "def before_query(prompt, context):\n"
        "    return prompt, context\n\n"
        "def get_tools():\n"
        "    return []\n",
        encoding="utf-8",
    )

    import core.system.paths as paths

    monkeypatch.setattr(paths, "PLUGINS_DIR", plugin_dir)

    result = handlers.HANDLERS["brain.plugins.list"]()

    assert result["plugins_dir"] == str(plugin_dir)
    assert result["plugins"] == [
        {
            "name": "example",
            "path": str(example),
            "status": "discovered",
            "hooks": ["before_query", "get_tools"],
            "tray_actions": [],
            "tools": [],
            "error": "",
        }
    ]


def test_plugins_list_prefers_loaded_manager(monkeypatch, tmp_path):
    module = types.ModuleType("plugins.loaded")
    module.__file__ = str(tmp_path / "plugins" / "loaded" / "__init__.py")

    def get_tray_actions():
        return [{"label": "Do Thing", "callback": object()}]

    def get_tools():
        return [{"name": "loaded_tool"}]

    module.get_tray_actions = get_tray_actions
    module.get_tools = get_tools
    mod = types.SimpleNamespace(name="loaded", module=module)
    manager = types.SimpleNamespace(_mods=[mod])
    fake_plugin_manager = types.ModuleType("core.plugin_manager")
    fake_plugin_manager.get_manager = lambda: manager
    monkeypatch.setitem(sys.modules, "core.plugin_manager", fake_plugin_manager)

    import core.system.paths as paths

    monkeypatch.setattr(paths, "PLUGINS_DIR", tmp_path / "plugins")

    result = handlers.HANDLERS["brain.plugins.list"]()

    assert result["plugins"] == [
        {
            "name": "loaded",
            "path": str(tmp_path / "plugins" / "loaded"),
            "status": "loaded",
            "hooks": ["get_tools", "get_tray_actions"],
            "tray_actions": ["Do Thing"],
            "tools": ["loaded_tool"],
            "error": "",
        }
    ]
