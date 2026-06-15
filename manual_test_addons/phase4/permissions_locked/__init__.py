def before_query(prompt, context):
    return prompt, context


def get_tray_actions():
    return [{"label": "SHOULD NOT APPEAR", "callback": lambda: None}]


def get_settings():
    return [{"key": "blocked", "label": "SHOULD NOT APPEAR", "type": "text", "default": "blocked"}]


def get_intents():
    return [{"id": "blocked-intent", "label": "SHOULD NOT APPEAR", "prompt": "blocked"}]


def get_notifications():
    return [{"title": "SHOULD NOT APPEAR", "message": "blocked"}]


def get_hotkeys():
    return [{"id": "blocked-hotkey", "label": "SHOULD NOT APPEAR", "hotkey": "ctrl+alt+shift+l"}]


def get_tools():
    return [
        {
            "name": "phase4_blocked_tool",
            "description": "SHOULD NOT APPEAR",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "executor": lambda inputs: "blocked",
        }
    ]

