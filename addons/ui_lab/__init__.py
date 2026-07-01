"""UI Lab addon for exercising chat and bubble extension behavior."""

from __future__ import annotations

from core.addon_manager import addon_setting


_TRIGGERS = {
    "bubble": ("highlight", "#4da3ff", "Bubble surface annotation"),
    "chat": ("underline", "#00ffaa", "Chat surface annotation"),
    "style": ("tag", "#ffd166", "Style tag annotation"),
    "select": ("highlight", "#d77cff", "Selection-related annotation"),
    "right-click": ("underline", "#ff8a65", "Context-menu annotation"),
    "code": ("tag", "#8bd17c", "Inline code annotation"),
}


def on_startup(_app_context):
    return {"ok": True}


def after_response(_text: str):
    return None


def get_tray_actions() -> list[dict]:
    return [{"label": "UI Lab: no-op", "callback": lambda: {"ok": True}}]


def get_settings() -> list[dict]:
    return [
        {
            "key": "enabled",
            "label": "Annotate chat trigger words",
            "type": "bool",
            "default": True,
        },
        {
            "key": "extra_word",
            "label": "Extra highlighted word",
            "type": "text",
            "default": "Wisp",
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
    """Highlight known words in chat messages using display-only annotations."""
    if not _truthy(addon_setting("ui-lab", "enabled", True)):
        return []
    text = str((payload or {}).get("text") or "")
    surface = str((payload or {}).get("surface") or "chat")
    if surface != "chat":
        return []

    annotations: list[dict] = []
    lowered = text.lower()
    for word, (kind, color, tooltip) in _TRIGGERS.items():
        _append_matches(
            annotations,
            haystack=lowered,
            needle=word.lower(),
            kind=kind,
            color=color,
            tooltip=tooltip,
            annotation_id=f"ui-lab-{word}",
        )

    extra = str(addon_setting("ui-lab", "extra_word", "Wisp") or "").strip()
    if extra:
        _append_matches(
            annotations,
            haystack=lowered,
            needle=extra.lower(),
            kind="highlight",
            color="#b8b8ff",
            tooltip="Configured UI Lab extra word",
            annotation_id="ui-lab-extra",
        )
    return annotations[:32]


def _append_matches(
    annotations: list[dict],
    *,
    haystack: str,
    needle: str,
    kind: str,
    color: str,
    tooltip: str,
    annotation_id: str,
) -> None:
    if not needle:
        return
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx < 0:
            return
        end = idx + len(needle)
        annotations.append(
            {
                "start": idx,
                "end": end,
                "kind": kind,
                "color": color,
                "tooltip": tooltip,
                "id": annotation_id,
                "surface": "chat",
            }
        )
        start = end


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}
