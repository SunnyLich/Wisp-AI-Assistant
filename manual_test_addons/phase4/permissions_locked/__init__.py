"""Package marker and exports for manual test addons phase4 permissions locked."""

def before_query(prompt, context):
    """Support package marker and exports for manual test addons phase4 permissions locked for before query."""
    return prompt, context


def get_tray_actions():
    """Return tray actions."""
    return [{"label": "SHOULD NOT APPEAR", "callback": lambda: None}]


def get_settings():
    """Return settings."""
    return [{"key": "blocked", "label": "SHOULD NOT APPEAR", "type": "text", "default": "blocked"}]


def get_intents():
    """Return intents."""
    return [{"id": "blocked-intent", "label": "SHOULD NOT APPEAR", "prompt": "blocked"}]


def get_notifications():
    """Return notifications."""
    return [{"title": "SHOULD NOT APPEAR", "message": "blocked"}]


def get_hotkeys():
    """Return hotkeys."""
    return [{"id": "blocked-hotkey", "label": "SHOULD NOT APPEAR", "hotkey": "ctrl+alt+shift+l"}]


def get_tools():
    """Return tools."""
    return [
        {
            "name": "phase4_blocked_tool",
            "description": "SHOULD NOT APPEAR",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "executor": lambda inputs: "blocked",
        }
    ]

