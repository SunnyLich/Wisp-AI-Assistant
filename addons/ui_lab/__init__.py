"""UI Lab addon for exercising user-created text labels."""

from __future__ import annotations

from core.addon_manager import addon_setting

from . import labels


def on_startup(app_context):
    labels.configure_data_dir(app_context.data_dir)
    return {"ok": True}


def after_response(_text: str):
    return None


def transform_response_text(payload: dict) -> dict:
    """Optionally rewrite assistant text so response mutation can be tested."""
    text = str((payload or {}).get("text") or "")
    surface = str((payload or {}).get("surface") or "")
    if surface not in {"chat", "reply"}:
        return {"text": text}
    if not _truthy(addon_setting("ui-lab", "rewrite_enabled", False)):
        return {"text": text}
    prefix = str(addon_setting("ui-lab", "rewrite_prefix", "[UI Lab] ") or "")
    if not prefix or text.startswith(prefix):
        return {"text": text}
    return {"text": prefix + text}


def get_tray_actions() -> list[dict]:
    return [{"label": "UI Lab: no-op", "callback": lambda: {"ok": True}}]


def get_settings() -> list[dict]:
    return [
        {
            "key": "enabled",
            "label": "Apply saved text labels",
            "type": "bool",
            "default": True,
        },
        {
            "key": "annotate_user_messages",
            "label": "Apply labels to user messages",
            "type": "bool",
            "default": False,
        },
        {
            "key": "rewrite_enabled",
            "label": "Rewrite assistant replies",
            "type": "bool",
            "default": False,
        },
        {
            "key": "rewrite_prefix",
            "label": "Reply rewrite prefix",
            "type": "text",
            "default": "[UI Lab] ",
        },
    ]


def get_tools() -> list[dict]:
    return [
        {
            "name": "ui_lab_echo",
            "description": "Echo text so UI Lab can be tested as a harmless model tool.",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": [],
            },
            "executor": lambda inputs: "UI Lab echo: " + str((inputs or {}).get("text") or ""),
        }
    ]


def get_text_annotations(payload: dict) -> list[dict]:
    """Apply user-created labels to chat and floating reply messages."""
    if not _truthy(addon_setting("ui-lab", "enabled", True)):
        return []
    text = str((payload or {}).get("text") or "")
    surface = str((payload or {}).get("surface") or "chat")
    if surface not in {"chat", "reply"}:
        return []
    role = str((payload or {}).get("role") or "assistant")
    if role == "user" and not _truthy(addon_setting("ui-lab", "annotate_user_messages", False)):
        return []
    return labels.annotations_for_text(text, surface=surface)


def get_text_context_actions(payload: dict) -> list[dict]:
    """Return label edit/delete actions for selected text."""
    selected = str((payload or {}).get("selected_text") or "").strip()
    surface = str((payload or {}).get("surface") or "")
    if not selected or surface not in {"chat", "reply"}:
        return []
    existing = labels.find_rule(selected)
    actions = [
        {
            "id": "ui-lab-edit-label",
            "label": "Edit label" if existing else "Add label",
            "action": "label_editor",
            "match": selected,
        }
    ]
    if existing:
        actions.append(
            {
                "id": "ui-lab-delete-label",
                "label": "Delete label",
                "action": "delete_label",
                "match": selected,
            }
        )
    return actions


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}
