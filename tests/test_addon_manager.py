"""Tests for process-hosted addons and the plugin compatibility facade."""
from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
import time
import zipfile
from pathlib import Path

import pytest

import config
import core.addon_manager as am
import core.addon_manager as pm
import core.addon_runtime as addon_runtime
import core.addon_store as addon_store
from core.addon_distribution import install_addon_archive, install_addon_folder
from core.tool_registry import ToolRegistry

_ADDON_SRC = """
import os
import sys
from core.addon_manager import addon_setting

def before_query(prompt, context):
    print("before-query-log", file=sys.stderr, flush=True)
    return prompt + "!" + str(os.getpid()), context + "|addon"

def after_response(text):
    print("after-response:" + text, file=sys.stderr, flush=True)

def on_event(event, payload):
    print("event-log:" + event, file=sys.stderr, flush=True)
    return {"event": event, "seen": sorted((payload or {}).keys())}

def get_tools():
    return [{
        "name": "demo_tool",
        "description": "demo",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "executor": lambda inputs: "ok:" + str(os.getpid()),
    }]

def get_tray_actions():
    return [{"label": "Act", "callback": lambda: {
        "message": "acted",
        "virtual_workspace_url": "http://127.0.0.1:8765/?token=test",
    }}]

def get_settings():
    return [{"key": "greeting", "label": "Greeting", "type": "text", "default": "hi"}]

def get_intents():
    return [{"id": "dynamic", "label": "Dynamic", "key": "d", "prompt": "Dynamic prompt"}]

def get_notifications():
    return [{"title": "Demo", "message": "Loaded"}]

def get_hotkeys():
    return [{"id": "dynamic-hotkey", "label": "Dynamic hotkey", "hotkey": "ctrl+alt+d", "callback": lambda payload: {"message": "hotkey ok"}}]
"""


def _make_manager(tmp_path: Path, monkeypatch) -> tuple[am.AddonManager, Path]:
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "demo"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "demo"
            name = "demo"
            entry = "__init__.py"

            [permissions]
            query = "modify"
            response = "read"
            tools = true
            hotkeys = true
            ui = ["tray", "settings", "intents", "notifications"]
            events = ["demo.event"]

            [[intents]]
            id = "static"
            label = "Static"
            key = "s"
            prompt = "Static prompt"

            [[notifications]]
            title = "Static"
            message = "Ready"

            [[hotkeys]]
            id = "static-hotkey"
            label = "Static hotkey"
            hotkey = "ctrl+alt+s"
            prompt = "Static hotkey prompt"
            """
        ).strip(),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text(textwrap.dedent(_ADDON_SRC).strip(), encoding="utf-8")

    store_path = tmp_path / "addons.json"
    monkeypatch.setattr(addon_store, "_STORE_PATH", store_path)
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", store_path)

    manager = am.AddonManager(addons_dir)
    manager.load_all()
    return manager, store_path


def test_addon_hooks_and_tools_run_in_host_process(tmp_path, monkeypatch):
    manager, _store_path = _make_manager(tmp_path, monkeypatch)
    registry = ToolRegistry(plugin_dir=Path("does-not-exist"))
    manager.on_startup(am.AppContext(signals=None, model_tool_registry=registry, config=config))

    host_pid = manager.before_query("hi", "")[0].removeprefix("hi!")
    assert host_pid and int(host_pid) != os.getpid()
    assert manager.before_query("hi", "")[1] == "|addon"
    assert manager.get_tray_actions()[0]["label"] == "Act"
    assert manager.run_tray_action("demo", "Act") == {
        "message": "acted",
        "virtual_workspace_url": "http://127.0.0.1:8765/?token=test",
    }
    assert am._safe_tray_action_result({"virtual_workspace_url": "https://example.com/"}) == {}
    assert "demo_tool" in {s["name"] for s in registry.schemas()}

    tool_result = registry.execute("demo_tool", {})
    assert tool_result.startswith("ok:")
    assert int(tool_result.removeprefix("ok:")) != os.getpid()

    manager.after_response("complete reply")
    logs = ""
    for _ in range(20):
        logs = str(manager.summaries()[0].get("logs") or "")
        if "after-response:complete reply" in logs:
            break
        time.sleep(0.05)
    assert "after-response:complete reply" in logs

    manager.on_shutdown()


def test_addon_events_intents_and_notifications(tmp_path, monkeypatch):
    manager, _store_path = _make_manager(tmp_path, monkeypatch)

    intents = manager.get_intents(caller_idx=0)
    assert {item["id"] for item in intents} == {"static", "dynamic"}
    assert manager.get_notifications() == [
        {"addon_id": "demo", "title": "Static", "message": "Ready"},
        {"addon_id": "demo", "title": "Demo", "message": "Loaded"},
    ]

    result = manager.dispatch_event("demo.event", {"answer": 42})
    assert result == [{"addon_id": "demo", "event": "demo.event", "seen": ["answer"]}]
    assert {item["id"] for item in manager.get_hotkeys()} == {"static-hotkey", "dynamic-hotkey"}
    assert manager.run_hotkey("demo", "static-hotkey") == {"prompt": "Static hotkey prompt"}
    assert manager.run_hotkey("demo", "dynamic-hotkey") == {"message": "hotkey ok"}
    manager.on_shutdown()


def test_action_files_catalogue_addon_surfaces_and_preserve_host_isolation(tmp_path, monkeypatch):
    """Action TOMLs label/filter host surfaces without becoming executors."""
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "catalogued"
    actions_dir = addon_dir / "actions"
    actions_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "catalogued"
            name = "Catalogued"
            entry = "__init__.py"

            [permissions]
            response = "modify"
            tools = true
            ui = ["intents", "message_actions"]
            """
        ).strip(),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text(
        textwrap.dedent(
            """
            def get_tools():
                return [
                    {"name": "kept_tool", "description": "runtime", "executor": lambda inputs: "kept"},
                    {"name": "hidden_tool", "description": "runtime", "executor": lambda inputs: "hidden"},
                ]

            def get_intents():
                return [{"id": "host-intent", "label": "Host", "callback": lambda payload: {"message": "ran"}}]

            def get_message_actions(payload):
                return [{"id": "host-message", "label": "Host message"}]

            def run_message_action(action_id, payload):
                return {"status": action_id}

            def transform_response_text(payload):
                return {"text": "changed:" + payload["text"]}
            """
        ).strip(),
        encoding="utf-8",
    )
    (actions_dir / "tool.toml").write_text(
        'id = "public-tool"\nkind = "tool"\nhandler = "kept_tool"\n'
        'label = "Kept tool"\nhint = "Declared description"\naccess = ["files"]\n',
        encoding="utf-8",
    )
    (actions_dir / "intent.toml").write_text(
        'id = "public-intent"\nkind = "intent"\nhandler = "host-intent"\n'
        'label = "Declared intent"\nkey = "z"\naccess = ["internet"]\n',
        encoding="utf-8",
    )
    (actions_dir / "message.toml").write_text(
        'id = "public-message"\nkind = "message_action"\nhandler = "host-message"\n'
        'label = "Declared message"\naccess = ["text"]\n',
        encoding="utf-8",
    )
    transform_path = actions_dir / "transform.toml"
    transform_path.write_text(
        '# retain me\nid = "transform"\nkind = "response_transform"\n'
        'label = "Response transform"\naccess = ["text"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    assert [item["name"] for item in manager.model_tool_payloads()] == ["kept_tool"]
    assert manager.model_tool_payloads()[0]["description"] == "Declared description"
    assert manager.model_tool_payloads()[0]["access"] == ["files"]
    assert manager.get_intents()[0] == {
        "id": "public-intent",
        "addon_id": "catalogued",
        "key": "z",
        "label": "Declared intent",
        "hint": "",
        "prompt": "",
        "caller": "all",
        "callback": True,
        "access": ["internet"],
        "access_colour": "amber",
        "action_file": manager.get_intents()[0]["action_file"],
    }
    assert manager.run_intent("catalogued", "public-intent") == {"message": "ran"}
    assert manager.get_message_actions({"role": "assistant"})[0]["label"] == "Declared message"
    assert manager.run_message_action("catalogued", "public-message") == {"status": "host-message"}
    assert manager.transform_response_text({"text": "reply"}) == "changed:reply"
    assert {item["kind"] for item in manager.summaries()[0]["actions"]} == {
        "intent", "message_action", "response_transform", "tool",
    }

    assert manager.set_action_enabled("catalogued", "transform", False) is False
    assert "# retain me" in transform_path.read_text(encoding="utf-8")
    assert "enabled = false" in transform_path.read_text(encoding="utf-8")
    assert manager.transform_response_text({"text": "reply"}) == "reply"
    manager.on_shutdown()


def test_bundled_addon_action_catalogues_load_without_issues():
    """Every shipped phase-two declaration is readable without addon imports."""
    root = Path(__file__).parents[1] / "addons"
    expected = {
        "ui_lab": {"intent", "response_transform", "tool"},
        "virtual_workspace": {"tool"},
        "mcp_bridge": {"tool_provider"},
    }
    for folder_name, kinds in expected.items():
        manifest = am.load_manifest(root / folder_name)
        assert manifest.action_issues == ()
        assert {action.kind for action in manifest.actions} == kinds
        assert all(Path(action.path).suffix == ".toml" for action in manifest.actions)


def test_text_annotation_hook_requires_explicit_permission_and_sanitizes(tmp_path, monkeypatch):
    """Verify text annotation addons are permission-gated and sanitized."""
    addons_dir = tmp_path / "addons"
    addons_dir.mkdir()
    specs = {
        "allowed": 'ui = ["text_annotations"]',
        "blocked": 'ui = ["settings"]',
        "broad": "ui = true",
    }
    for addon_id, ui_permission in specs.items():
        folder = addons_dir / addon_id
        folder.mkdir()
        (folder / "addon.toml").write_text(
            textwrap.dedent(
                f"""
                [addon]
                id = "{addon_id}"
                name = "{addon_id}"
                entry = "__init__.py"

                [permissions]
                {ui_permission}
                """
            ).strip(),
            encoding="utf-8",
        )
        (folder / "__init__.py").write_text(
            textwrap.dedent(
                """
                def get_text_annotations(payload):
                    assert "context" not in payload
                    return [
                        {"start": 0, "end": 4, "tag": "mark", "style": "background-color:#00ffaa", "tooltip": "visible"},
                        {"start": 5, "end": 9, "tag": "script", "style": "position:absolute", "source": "addon:fake"},
                        {"start": 999, "end": 1000},
                    ]
                """
            ).strip(),
            encoding="utf-8",
        )
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    annotations = manager.get_text_annotations(
        {
            "text": "CUDA test",
            "context": "secret hidden context",
            "message_id": "m1",
            "conversation_id": "c1",
            "surface": "chat",
            "role": "assistant",
        }
    )

    assert len(annotations) == 2
    assert {item["source"] for item in annotations} == {"addon:allowed"}
    assert annotations[0]["tag"] == "mark"
    assert annotations[0]["style"] == "background-color:#00ffaa"
    assert annotations[1]["tag"] == "span"
    assert annotations[1]["style"] == ""
    assert all(item["message_id"] == "m1" for item in annotations)
    assert all(item["conversation_id"] == "c1" for item in annotations)
    manager.on_shutdown()


def test_text_context_actions_require_permission_and_sanitize(tmp_path, monkeypatch):
    """Verify selected-text menu actions are opt-in and client-side safe."""
    addons_dir = tmp_path / "addons"
    addons_dir.mkdir()
    specs = {
        "allowed": 'ui = ["text_context_menu"]',
        "blocked": 'ui = ["text_annotations"]',
        "broad": "ui = true",
    }
    for addon_id, ui_permission in specs.items():
        folder = addons_dir / addon_id
        folder.mkdir()
        (folder / "addon.toml").write_text(
            textwrap.dedent(
                f"""
                [addon]
                id = "{addon_id}"
                name = "{addon_id}"
                entry = "__init__.py"

                [permissions]
                {ui_permission}
                """
            ).strip(),
            encoding="utf-8",
        )
        (folder / "__init__.py").write_text(
            textwrap.dedent(
                """
                def get_text_context_actions(payload):
                    assert "context" not in payload
                    return [
                        {"label": "Copy note", "action": "copy", "text": "note: " + payload["selected_text"]},
                        {"label": "Edit note", "action": "label_editor", "match": payload["selected_text"]},
                        {"label": "Delete note", "action": "delete_label", "match": payload["selected_text"]},
                        {"label": "Broken edit", "action": "label_editor"},
                        {"label": "Run code", "action": "execute", "text": "bad"},
                        {"label": "", "action": "copy", "text": "bad"},
                    ]
                """
            ).strip(),
            encoding="utf-8",
        )
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    actions = manager.get_text_context_actions(
        {
            "selected_text": "bubble words",
            "text": "full bubble words",
            "context": "secret hidden context",
            "surface": "reply",
            "role": "assistant",
        }
    )

    assert actions == [
        {
            "addon_id": "allowed",
            "id": "Copy note",
            "label": "Copy note",
            "action": "copy",
            "text": "note: bubble words",
        },
        {
            "addon_id": "allowed",
            "id": "Edit note",
            "label": "Edit note",
            "action": "label_editor",
            "text": "",
            "match": "bubble words",
        },
        {
            "addon_id": "allowed",
            "id": "Delete note",
            "label": "Delete note",
            "action": "delete_label",
            "text": "",
            "match": "bubble words",
        }
    ]
    assert manager.get_text_context_actions({"text": "full only", "surface": "reply"}) == []
    manager.on_shutdown()


def test_response_transform_hook_requires_modify_permission_and_chains(tmp_path, monkeypatch):
    """Verify response text transforms are opt-in and use sanitized payloads."""
    addons_dir = tmp_path / "addons"
    addons_dir.mkdir()
    specs = {
        "first": 'response = "modify"',
        "blocked": 'response = "read"',
        "second": 'response = "modify"',
    }
    for addon_id, response_permission in specs.items():
        folder = addons_dir / addon_id
        folder.mkdir()
        (folder / "addon.toml").write_text(
            textwrap.dedent(
                f"""
                [addon]
                id = "{addon_id}"
                name = "{addon_id}"
                entry = "__init__.py"

                [permissions]
                {response_permission}
                """
            ).strip(),
            encoding="utf-8",
        )
        (folder / "__init__.py").write_text(
            textwrap.dedent(
                f"""
                def transform_response_text(payload):
                    assert "context" not in payload
                    assert payload["surface"] == "chat"
                    assert payload["role"] == "assistant"
                    return {addon_id!r} + ":" + payload["text"]
                """
            ).strip(),
            encoding="utf-8",
        )
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    result = manager.transform_response_text(
        {
            "text": "hello",
            "context": "secret hidden context",
            "surface": "chat",
            "role": "assistant",
        }
    )

    assert result == "second:first:hello"
    manager.on_shutdown()


def test_ui_lab_addon_exercises_saved_label_surfaces(tmp_path, monkeypatch):
    """The bundled UI Lab addon should apply saved labels to chat and bubble text."""
    addons_dir = tmp_path / "addons"
    addons_dir.mkdir()
    shutil.copytree(Path("addons/ui_lab"), addons_dir / "ui_lab")
    repo_data = tmp_path / "repo-data"
    labels_dir = repo_data / "addon_data" / "ui-lab"
    labels_dir.mkdir(parents=True)
    labels_path = labels_dir / "labels.json"
    monkeypatch.setenv("OPENWAND_REPO_ROOT", str(repo_data))
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    assert (
        manager.get_text_annotations(
            {
                "text": "Try bubble chat style select right-click code in OpenWand.",
                "message_id": "m-ui-lab",
                "conversation_id": "c-ui-lab",
                "surface": "chat",
                "role": "assistant",
            }
        )
        == []
    )

    labels_path.write_text(
        json.dumps(
            {
                "labels": [
                    {
                        "match": "OpenWand",
                        "tooltip": "Assistant name",
                        "style": "font-weight:700; text-decoration:underline; background:url(x)",
                    },
                    {
                        "match": "bubble label",
                        "tooltip": "Phrase label",
                        "style": "font-style:italic; color:#b8b8ff",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    sample_text = "OpenWand uses a bubble label. openwands should not match. OPENWAND can match again."
    annotations = manager.get_text_annotations(
        {
            "text": sample_text,
            "message_id": "m-ui-lab",
            "conversation_id": "c-ui-lab",
            "surface": "chat",
            "role": "assistant",
        }
    )

    assert len(annotations) == 3
    assert {item["source"] for item in annotations} == {"addon:ui-lab"}
    assert all(item["message_id"] == "m-ui-lab" for item in annotations)
    assert all(item["tag"] == "span" for item in annotations)
    by_match = {sample_text[item["start"]: item["end"]]: item for item in annotations}
    assert {"OpenWand", "bubble label", "OPENWAND"} == set(by_match)
    assert by_match["OpenWand"]["start"] == 0
    assert by_match["OpenWand"]["end"] == len("OpenWand")
    assert by_match["OpenWand"]["tooltip"] == "Assistant name"
    assert by_match["OpenWand"]["style"] == "font-weight:700; text-decoration:underline"
    assert by_match["bubble label"]["style"] == "font-style:italic; color:#b8b8ff"

    unicode_text = "“quoted”—OpenWand"
    unicode_annotations = manager.get_text_annotations(
        {"text": unicode_text, "surface": "chat", "role": "assistant"}
    )
    openwand_annotation = next(
        item for item in unicode_annotations if item.get("id") == "ui-lab-label-openwand"
    )
    assert unicode_text[openwand_annotation["start"]:openwand_annotation["end"]] == "OpenWand"

    setting_keys = {item["key"] for item in manager.get_settings("ui-lab")}
    assert {"enabled", "annotate_user_messages", "rewrite_enabled", "rewrite_prefix"} <= setting_keys

    assert (
        manager.get_text_annotations(
            {
                "text": "OpenWand uses a bubble label.",
                "surface": "chat",
                "role": "user",
            }
        )
        == []
    )
    manager.set_setting("ui-lab", "annotate_user_messages", "true")
    assert manager.get_text_annotations(
        {
            "text": "OpenWand uses a bubble label.",
            "surface": "chat",
            "role": "user",
        }
    )

    reply_annotations = manager.get_text_annotations(
        {
            "text": "OpenWand uses a bubble label.",
            "surface": "reply",
            "role": "assistant",
        }
    )
    reply_ids = {item["id"] for item in reply_annotations}
    assert {"ui-lab-label-openwand", "ui-lab-label-bubble-label"} == reply_ids
    assert {item["surface"] for item in reply_annotations} == {"reply"}

    context_actions = manager.get_text_context_actions(
        {
            "selected_text": "OpenWand",
            "text": "OpenWand uses a bubble label.",
            "surface": "reply",
            "role": "assistant",
        }
    )
    assert context_actions == [
        {
            "addon_id": "ui-lab",
            "id": "ui-lab-edit-label",
            "label": "Edit label",
            "action": "label_editor",
            "text": "",
            "match": "OpenWand",
        },
        {
            "addon_id": "ui-lab",
            "id": "ui-lab-delete-label",
            "label": "Delete label",
            "action": "delete_label",
            "text": "",
            "match": "OpenWand",
        },
    ]
    assert manager.get_text_context_actions({"selected_text": "new phrase", "surface": "chat"}) == [
        {
            "addon_id": "ui-lab",
            "id": "ui-lab-edit-label",
            "label": "Add label",
            "action": "label_editor",
            "text": "",
            "match": "new phrase",
        },
    ]

    manager.set_setting("ui-lab", "enabled", "false")
    assert manager.get_text_annotations({"text": "OpenWand", "surface": "chat"}) == []
    assert "labels" not in (tmp_path / "addons.json").read_text(encoding="utf-8")

    assert manager.transform_response_text({"text": "reply", "surface": "chat", "role": "assistant"}) == "reply"
    manager.set_setting("ui-lab", "rewrite_enabled", "true")
    assert (
        manager.transform_response_text({"text": "reply", "surface": "chat", "role": "assistant"})
        == "[UI Lab] reply"
    )
    manager.on_shutdown()


def test_ui_lab_labels_migrate_from_legacy_addon_settings(tmp_path, monkeypatch):
    """Legacy UI Lab label settings should move into addon-owned data storage."""
    addons_dir = tmp_path / "addons"
    addons_dir.mkdir()
    shutil.copytree(Path("addons/ui_lab"), addons_dir / "ui_lab")
    repo_data = tmp_path / "repo-data"
    monkeypatch.setenv("OPENWAND_REPO_ROOT", str(repo_data))
    store_path = tmp_path / "addons.json"
    monkeypatch.setattr(addon_store, "_STORE_PATH", store_path)
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", store_path)
    addon_store.set_setting(
        "ui-lab",
        "labels",
        [{"match": "OpenWand", "tooltip": "Migrated label", "style": "text-decoration:underline"}],
    )

    manager = am.AddonManager(addons_dir)
    manager.load_all()
    annotations = manager.get_text_annotations({"text": "OpenWand", "surface": "chat", "role": "assistant"})

    assert annotations and annotations[0]["tooltip"] == "Migrated label"
    labels_path = repo_data / "addon_data" / "ui-lab" / "labels.json"
    assert labels_path.exists()
    assert "Migrated label" in labels_path.read_text(encoding="utf-8")
    assert "labels" not in store_path.read_text(encoding="utf-8")
    manager.on_shutdown()


def test_addon_enable_and_settings_round_trip(tmp_path, monkeypatch):
    manager, store_path = _make_manager(tmp_path, monkeypatch)
    registry = ToolRegistry(plugin_dir=Path("does-not-exist"))
    manager.on_startup(am.AppContext(signals=None, model_tool_registry=registry, config=config))

    settings = manager.get_settings("demo")
    assert settings == [
        {"key": "greeting", "label": "Greeting", "type": "text", "default": "hi", "value": "hi"}
    ]
    assert pm.addon_setting("demo", "greeting", "fallback") == "fallback"

    manager.set_setting("demo", "greeting", "hello")
    assert pm.addon_setting("demo", "greeting") == "hello"
    assert manager.get_settings("demo")[0]["value"] == "hello"
    settings_path = store_path.parent / "addon_data" / "demo" / "settings.json"
    assert "hello" in settings_path.read_text(encoding="utf-8")
    assert "hello" not in store_path.read_text(encoding="utf-8")

    assert manager.before_query("prompt", "context")[0].startswith("prompt!")
    assert manager.get_tray_actions()[0]["label"] == "Act"
    assert {item["id"] for item in manager.get_intents()} == {"static", "dynamic"}
    assert {item["id"] for item in manager.get_hotkeys()} == {"static-hotkey", "dynamic-hotkey"}
    assert [item["title"] for item in manager.get_notifications()] == ["Static", "Demo"]

    manager.set_enabled("demo", False)
    assert not manager.is_enabled("demo")
    assert "demo_tool" not in {s["name"] for s in registry.schemas()}
    assert manager.before_query("prompt", "context") == ("prompt", "context")
    assert manager.get_tray_actions() == []
    assert manager.get_intents() == []
    assert manager.get_hotkeys() == []
    assert manager.get_notifications() == []

    manager.set_enabled("demo", True)
    assert manager.is_enabled("demo")
    assert "demo_tool" in {s["name"] for s in registry.schemas()}
    assert manager.before_query("prompt", "context")[0].startswith("prompt!")
    assert manager.get_tray_actions()[0]["label"] == "Act"
    assert {item["id"] for item in manager.get_intents()} == {"static", "dynamic"}
    assert {item["id"] for item in manager.get_hotkeys()} == {"static-hotkey", "dynamic-hotkey"}
    assert [item["title"] for item in manager.get_notifications()] == ["Static", "Demo"]
    manager.on_shutdown()


def test_addon_stderr_is_exposed_in_summary_logs(tmp_path, monkeypatch):
    manager, _store_path = _make_manager(tmp_path, monkeypatch)

    manager.before_query("hi", "")
    logs = ""
    for _ in range(20):
        logs = str(manager.summaries()[0].get("logs") or "")
        if "before-query-log" in logs:
            break
        time.sleep(0.05)

    assert "before-query-log" in logs
    manager.on_shutdown()


def test_addon_with_dependencies_waits_for_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(addon_runtime, "ADDON_ENVS_DIR", tmp_path / "addon_envs")
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "needs_deps"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "needs-deps"
            name = "needs_deps"
            entry = "__init__.py"

            [dependencies]
            python = ">=3.11"
            packages = ["requests>=2.31"]
            """
        ).strip(),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text("def before_query(prompt, context):\n    return prompt, context\n", encoding="utf-8")
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    summary = manager.summaries()[0]
    assert summary["status"] == "needs_dependencies"
    assert summary["runtime"]["tier"] == "2"
    assert summary["runtime"]["ready"] is False
    assert summary["approval"]["needs_approval"] is False
    assert summary["dependencies"]["packages"] == ["requests>=2.31"]
    assert manager.before_query("hi", "") == ("hi", "")


def test_addon_access_review_uses_author_and_blocks_code_until_approved(tmp_path, monkeypatch):
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "reviewed"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "reviewed"
            name = "Reviewed"
            author = "Example Publisher"
            homepage = "https://example.test/addon"

            [permissions]
            query = "read"
            tools = true
            """
        ).strip(),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text("def hooks():\n    return ['before_query']\n", encoding="utf-8")
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir, require_approval=True)
    manager.load_all()
    pending = manager.summaries()[0]

    assert pending["author"] == "Example Publisher"
    assert pending["homepage"] == "https://example.test/addon"
    assert pending["status"] == "needs_approval"
    assert pending["approval"]["needs_approval"] is True
    assert pending["hooks"] == []
    assert {item["id"] for item in pending["approval"]["new_access"]} == {
        "full_code",
        "query:read",
        "tools",
    }

    approved = manager.approve_addon("reviewed")

    assert approved["status"] == "loaded"
    assert approved["approval"]["needs_approval"] is False
    assert addon_store.approved_access_ids("reviewed") == {"full_code", "query:read", "tools"}
    manager.shutdown_hosts()


def test_addon_update_only_reprompts_when_access_expands(tmp_path, monkeypatch):
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "updated"
    addon_dir.mkdir(parents=True)
    manifest = addon_dir / "addon.toml"
    manifest.write_text(
        '[addon]\nid = "updated"\nauthor = "Example Publisher"\n\n[permissions]\nresponse = "read"\n',
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")
    manager = am.AddonManager(addons_dir, require_approval=True)
    manager.load_all()
    manager.approve_addon("updated")

    manager.load_all()
    assert manager.summaries()[0]["status"] == "loaded"

    manifest.write_text(
        '[addon]\nid = "updated"\nauthor = "Example Publisher"\n\n'
        '[permissions]\nresponse = "read"\nllm = true\n',
        encoding="utf-8",
    )
    manager.load_all()
    expanded = manager.summaries()[0]

    assert expanded["status"] == "needs_approval"
    assert [item["id"] for item in expanded["approval"]["new_access"]] == ["llm"]
    manager.shutdown_hosts()


def test_addon_manifest_accepts_cp1252_punctuation(tmp_path, monkeypatch):
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "legacy"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_bytes(
        b'[addon]\nid = "legacy"\nname = "Legacy"\ndescription = "old\x97new"\n'
    )
    (addon_dir / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    summary = manager.summaries()[0]
    assert summary["status"] == "loaded"
    assert summary["description"].startswith("old")
    assert summary["description"].endswith("new")
    assert ord(summary["description"][3]) == 0x2014


def test_manager_seeds_bundled_default_addons_when_missing(tmp_path, monkeypatch):
    """Verify bundled default addons are copied into the writable addon folder."""
    bundled_root = tmp_path / "bundle" / "addons"
    bundled_addon = bundled_root / "mcp_bridge"
    bundled_addon.mkdir(parents=True)
    (bundled_addon / "addon.toml").write_text("[addon]\nid = 'mcp-bridge'\nname = 'MCP Bridge'\n", encoding="utf-8")
    (bundled_addon / "__init__.py").write_text("", encoding="utf-8")
    (bundled_addon / "servers.json").write_text('{"servers": []}', encoding="utf-8")
    ui_lab_addon = bundled_root / "ui_lab"
    ui_lab_addon.mkdir(parents=True)
    (ui_lab_addon / "addon.toml").write_text("[addon]\nid = 'ui-lab'\nname = 'UI Lab'\n", encoding="utf-8")
    (ui_lab_addon / "__init__.py").write_text("", encoding="utf-8")

    store_path = tmp_path / "addons.json"
    monkeypatch.setattr(addon_store, "_STORE_PATH", store_path)
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", store_path)
    addon_store.set_enabled("mcp-bridge", False)

    addons_dir = tmp_path / "runtime" / "addons"
    manager = am.AddonManager(addons_dir, bundled_addons_dir=bundled_root)
    manager.load_all()

    assert (addons_dir / "mcp_bridge" / "addon.toml").exists()
    assert (addons_dir / "mcp_bridge" / "servers.json").read_text(encoding="utf-8") == '{"servers": []}'
    assert (addons_dir / "ui_lab" / "addon.toml").exists()
    summaries = {item["id"]: item for item in manager.summaries()}
    assert summaries["mcp-bridge"]["enabled"] is False
    assert summaries["ui-lab"]["enabled"] is True
    assert addon_store.approved_access_ids("mcp-bridge") == set()
    assert addon_store.approved_access_ids("ui-lab") == set()


def test_manager_does_not_overwrite_existing_default_addon(tmp_path, monkeypatch):
    """Verify seeded default addons preserve existing user configuration."""
    bundled_root = tmp_path / "bundle" / "addons"
    bundled_addon = bundled_root / "mcp_bridge"
    bundled_addon.mkdir(parents=True)
    (bundled_addon / "addon.toml").write_text("[addon]\nid = 'mcp-bridge'\nname = 'MCP Bridge'\n", encoding="utf-8")
    (bundled_addon / "__init__.py").write_text("", encoding="utf-8")
    (bundled_addon / "servers.json").write_text('{"servers": []}', encoding="utf-8")

    addons_dir = tmp_path / "runtime" / "addons"
    existing_addon = addons_dir / "mcp_bridge"
    existing_addon.mkdir(parents=True)
    (existing_addon / "addon.toml").write_text("[addon]\nid = 'mcp-bridge'\nname = 'MCP Bridge'\n", encoding="utf-8")
    (existing_addon / "__init__.py").write_text("", encoding="utf-8")
    (existing_addon / "servers.json").write_text('{"servers": [{"name": "custom"}]}', encoding="utf-8")

    store_path = tmp_path / "addons.json"
    monkeypatch.setattr(addon_store, "_STORE_PATH", store_path)
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", store_path)
    addon_store.set_enabled("mcp-bridge", False)

    manager = am.AddonManager(addons_dir, bundled_addons_dir=bundled_root)
    manager.load_all()

    assert (existing_addon / "servers.json").read_text(encoding="utf-8") == '{"servers": [{"name": "custom"}]}'


def test_approved_addon_with_missing_environment_needs_install(tmp_path, monkeypatch):
    monkeypatch.setattr(addon_runtime, "ADDON_ENVS_DIR", tmp_path / "addon_envs")
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "needs_deps"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "needs-deps"
            name = "needs_deps"
            entry = "__init__.py"

            [dependencies]
            packages = ["requests>=2.31"]
            """
        ).strip(),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text("", encoding="utf-8")
    store_path = tmp_path / "addons.json"
    monkeypatch.setattr(addon_store, "_STORE_PATH", store_path)
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", store_path)
    deps = addon_runtime.dependencies_from_manifest({"packages": ["requests>=2.31"]})
    addon_store.set_approved_dependency_hash("needs-deps", addon_runtime.dependency_hash(deps))

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    summary = manager.summaries()[0]
    assert summary["status"] == "needs_dependencies"
    assert summary["approval"]["needs_approval"] is False
    assert summary["runtime"]["ready"] is False


def test_addon_repair_environment_uses_ready_runtime(tmp_path, monkeypatch):
    deps = addon_runtime.AddonDependencies(python=">=3.11", packages=["demo-pkg"])
    monkeypatch.setattr(addon_runtime, "ADDON_ENVS_DIR", tmp_path / "addon_envs")
    env_dir = addon_runtime.env_path("demo")
    env_dir.mkdir(parents=True)
    addon_runtime.python_path(env_dir).parent.mkdir(parents=True, exist_ok=True)
    addon_runtime.python_path(env_dir).write_text("", encoding="utf-8")
    addon_runtime._write_marker(env_dir, deps)

    status = addon_runtime.environment_status("demo", deps)
    assert status["ready"] is True
    assert status["tier"] == "2"
    assert status["python"] == str(addon_runtime.python_path(env_dir))


def test_repair_environment_does_not_create_a_package_approval(tmp_path, monkeypatch):
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "needs_deps"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "needs-deps"
            name = "needs_deps"
            entry = "__init__.py"

            [dependencies]
            packages = ["demo-pkg"]
            """
        ).strip(),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text("", encoding="utf-8")
    store_path = tmp_path / "addons.json"
    monkeypatch.setattr(addon_store, "_STORE_PATH", store_path)
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", store_path)

    def fake_provision(addon_id, deps, *, force=False):
        return {
            "tier": "2",
            "ready": True,
            "python": sys.executable,
            "env_path": str(tmp_path / "env"),
            "packages": list(deps.packages),
            "python_requirement": deps.python,
            "hash": addon_runtime.dependency_hash(deps),
            "error": "",
        }

    monkeypatch.setattr(addon_runtime, "provision_environment", fake_provision)
    manager = am.AddonManager(addons_dir)
    manager.load_all()

    manager.repair_environment("needs-deps")

    assert addon_store.approved_dependency_hash("needs-deps") == ""


def test_install_addon_archive_rejects_path_traversal(tmp_path):
    archive = tmp_path / "bad.openwand"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "nope")

    with pytest.raises(ValueError, match="unsafe"):
        install_addon_archive(archive, tmp_path / "addons")


def test_install_addon_archive_extracts_single_addon(tmp_path):
    archive = tmp_path / "demo.openwand"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("demo/addon.toml", "[addon]\nid = 'demo'\nname = 'Demo'\n")
        zf.writestr("demo/__init__.py", "")

    result = install_addon_archive(archive, tmp_path / "addons")

    assert result["id"] == "demo"
    assert (tmp_path / "addons" / "demo" / "addon.toml").exists()


def test_install_addon_archive_accepts_legacy_wisp_suffix(tmp_path):
    archive = tmp_path / "legacy.wisp"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("demo/addon.toml", "[addon]\nid = 'demo'\nname = 'Demo'\n")
        zf.writestr("demo/__init__.py", "")

    result = install_addon_archive(archive, tmp_path / "addons")

    assert result["id"] == "demo"


def test_install_addon_folder_copies_single_addon(tmp_path):
    source = tmp_path / "source" / "demo"
    source.mkdir(parents=True)
    (source / "addon.toml").write_text("[addon]\nid = 'demo'\nname = 'Demo'\n", encoding="utf-8")
    (source / "__init__.py").write_text("", encoding="utf-8")

    result = install_addon_folder(source, tmp_path / "addons")

    assert result["id"] == "demo"
    assert (tmp_path / "addons" / "demo" / "addon.toml").exists()


def test_missing_permissions_deny_surfaces(tmp_path, monkeypatch):
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "locked"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        "[addon]\nid = 'locked'\nname = 'locked'\nentry = '__init__.py'\n",
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text(textwrap.dedent(_ADDON_SRC).strip(), encoding="utf-8")
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()
    registry = ToolRegistry(plugin_dir=Path("does-not-exist"))
    manager.on_startup(am.AppContext(signals=None, model_tool_registry=registry, config=config))

    assert manager.before_query("hi", "") == ("hi", "")
    assert manager.get_tray_actions() == []
    assert manager.get_settings("locked") == []
    assert registry.schemas(include_server_tools=False) == []
    manager.on_shutdown()


def test_invalid_incompatible_and_crashed_addons_fail_closed(tmp_path, monkeypatch):
    """Bad manifests/API versions and a dead isolated host never report loaded."""
    addons_dir = tmp_path / "addons"
    addons_dir.mkdir()

    invalid = addons_dir / "invalid"
    invalid.mkdir()
    (invalid / "addon.toml").write_text("[addon\nid = broken", encoding="utf-8")

    incompatible = addons_dir / "incompatible"
    incompatible.mkdir()
    (incompatible / "addon.toml").write_text(
        '[addon]\nid = "incompatible"\napi_version = "999"\n',
        encoding="utf-8",
    )
    (incompatible / "__init__.py").write_text("", encoding="utf-8")

    crashed = addons_dir / "crashed"
    crashed.mkdir()
    (crashed / "addon.toml").write_text(
        '[addon]\nid = "crashed"\napi_version = "1"\n',
        encoding="utf-8",
    )
    (crashed / "__init__.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(
        am.AddonHostProcess,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("isolated host crashed")),
    )

    manager = am.AddonManager(addons_dir)
    manager.load_all()
    try:
        by_id = {addon.id: addon for addon in manager._mods}
        assert by_id["invalid"].status == "error"
        assert "toml" in by_id["invalid"].error.lower()
        assert by_id["incompatible"].status == "error"
        assert "unsupported addon API version" in by_id["incompatible"].error
        assert by_id["crashed"].status == "error"
        assert "isolated host crashed" in by_id["crashed"].error
        assert by_id["crashed"].host is None
    finally:
        manager.on_shutdown()
