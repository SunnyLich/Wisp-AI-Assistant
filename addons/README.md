# Addons

Addons extend Wisp with query hooks, response observers, tray actions, settings,
and model-callable tools.

Each addon lives in its own folder under `addons/` and declares an `addon.toml`
manifest:

```text
addons/
  my-addon/
    addon.toml
    __init__.py
```

In a portable packaged build, Wisp creates this `addons/` folder next to
`Wisp.exe` when that location is writable. If the executable is installed in a
read-only location, use **Addon Manager -> Open addons folder** to open the
fallback user-writable addon directory. You can also install a `.wisp`/`.zip`
archive or unpacked addon folder from the Addon Manager.

Addons run in a dedicated Python subprocess, one process per addon. That means a
crash, import failure, or slow hook is isolated from the brain worker and from
other addons.

## Manifest

```toml
[addon]
id = "my-addon"
name = "My Addon"
version = "1.0.0"
description = "Adds one small behavior to Wisp."
entry = "__init__.py"
api_version = "1"

[permissions]
query = "modify"
response = "read"
tools = true
ui = ["tray", "settings"]
hotkeys = true
llm = true

[dependencies]
python = ">=3.11"
packages = ["requests>=2.31", "beautifulsoup4"]

events = ["app.startup", "response.after"]

[[intents]]
id = "summarize-selection"
key = "z"
label = "Addon summary"
hint = "Ask using this addon's prompt"
prompt = "Summarize the current selection with project context."

[[notifications]]
title = "My Addon"
message = "My Addon loaded."

[[hotkeys]]
id = "quick-summary"
label = "Quick addon summary"
hotkey = "ctrl+alt+z"
prompt = "Summarize the current context using this addon's workflow."
```

Missing permissions are denied. For example, an addon without `tools = true`
will not register model-callable tools, and an addon without `ui = ["tray"]`
will not expose tray actions. `response = "read"` allows observation through
`after_response`; `response = "modify"` is required to replace assistant text
through `transform_response_text`.

`[dependencies]` is optional. Addons without it run from Wisp's own Python
runtime. Addons that declare dependencies get a dedicated virtual environment
under `addon_envs/<addon-id>/`; the Addon Manager shows the required packages
and provides an Install/Repair action. Wisp records approval for the exact
dependency hash, so an addon update that changes packages must be approved
again before it runs. Wisp uses `uv` when available, falling back to
`python -m venv` in source checkouts.

Packaged builds should ship the `uv` binary at `bin/uv` or `bin/uv.exe` inside
the app bundle/folder. The PyInstaller specs collect `bin/uv*` or `tools/uv*`
from the repo when present.

## Phase 4 Surfaces

Addons can subscribe to app events with `events = [...]` and implement:

```python
def on_event(event: str, payload: dict):
    return {"ok": True}
```

Supported event names currently include:

- `app.startup`
- `app.shutdown`
- `response.after`

Prompt intents declared with `[[intents]]` appear in the normal intent picker
when the addon has `ui = ["intents"]`. Notifications declared with
`[[notifications]]` are exposed through the addon manager payload for UI/native
surfaces that want to display them, with a Wisp notice fallback where native
toasts are unavailable.

Bubble text that needs a UI-only label should use the host/UI event
`ui.reply.labeled_text` with `{"label": "...", "text": "..."}`. Wisp renders it
as `Label: text`, but the label is display chrome: it is excluded from reply
text, read-position counts, and TTS word highlighting. Built-in read-aloud uses
the same convention for `Reading: ...`.

Addon hotkeys declared with `[[hotkeys]]` require `hotkeys = true`. A hotkey can
return a prompt directly from the manifest, or dynamic `get_hotkeys()` callbacks
can return dictionaries such as `{"prompt": "..."}`, `{"message": "..."}`,
`{"notify": {"title": "...", "message": "..."}}`, or
`{"llm": {"prompt": "...", "max_tokens": 512}}`. LLM actions require
`llm = true` and are capped by Wisp before provider credentials are used.

Distribution is supported with `.zip` or `.wisp` archives containing exactly one
addon folder or a manifest at the archive root. The Addon Manager can also
install from an unpacked addon folder.

## Hooks

All hooks are optional:

```python
def on_startup(app_context):
    # app_context.config is the live config module.
    # app_context.data_dir is a per-addon writable directory.
    pass

def on_shutdown():
    pass

def before_query(prompt: str, context: str) -> tuple[str, str]:
    return prompt, context

def after_response(text: str):
    pass

def transform_response_text(payload: dict) -> dict:
    return {"text": payload.get("text", "")}

def get_text_annotations(payload: dict) -> list[dict]:
    return [{
        "start": 0,
        "end": 5,
        "tag": "mark",
        "style": "background-color:#ffd166; color:#111111",
        "tooltip": "Addon-provided tooltip",
    }]

def get_text_context_actions(payload: dict) -> list[dict]:
    return [{
        "label": "Copy tagged selection",
        "action": "copy",
        "text": "[addon] " + payload.get("selected_text", ""),
    }]

def get_tray_actions() -> list[dict]:
    return [{"label": "Run thing", "callback": run_thing}]

def get_settings() -> list[dict]:
    return [{"key": "prefix", "label": "Prefix", "type": "text", "default": "[my-addon]"}]

def get_tools() -> list[dict]:
    return [{
        "name": "my_tool",
        "description": "Does something useful.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "executor": lambda inputs: "ok",
    }]
```

`transform_response_text` receives the final assistant text plus display
metadata such as `surface` (`"reply"` for the floating bubble, `"chat"` for
chat), `role`, `message_id`, and `conversation_id`. It only runs for addons
with `[permissions] response = "modify"`. Returning a string or
`{"text": "..."}` replaces the final assistant text used by the bubble/chat
document and by later annotations. Wisp buffers normal assistant chunks until
this final text is ready; live progress/thought chunks may still be shown, but
the raw pre-transform answer text is not sent to the bubble/chat first.

`get_text_annotations` receives the same visible text metadata and returns safe
range tags for chat and floating-bubble text. `tag` is an HTML-like inline tag
name such as `span`, `mark`, `u`, `code`, `strong`, or `em`; unknown tags fall
back to `span`. `style` is sanitized inline CSS, and `tooltip` is rendered as a
normal HTML `title` attribute. Raw HTML from addons is never injected.

`get_text_context_actions` receives selected-text metadata from chat and the
floating bubble, then returns safe right-click menu actions. Addons must request
`ui = ["text_context_menu"]`. Supported actions include `{"action": "copy",
"text": "..."}` for clipboard text and UI Lab's local `label_editor` /
`delete_label` actions with a `match` value for editing saved text labels.

Read settings with:

```python
from core.addon_manager import addon_setting

value = addon_setting("my-addon", "prefix", "[my-addon]")
```
