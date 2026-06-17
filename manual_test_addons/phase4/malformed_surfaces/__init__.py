"""Package marker and exports for manual test addons phase4 malformed surfaces."""

def get_intents():
    """Return intents."""
    return [
        None,
        "not a dict",
        {"label": "", "prompt": ""},
        {"id": "dynamic-missing-prompt", "label": "Dynamic Missing Prompt"},
    ]


def get_notifications():
    """Return notifications."""
    return [
        None,
        "not a dict",
        {"title": "No message"},
        {"message": ""},
    ]


def get_hotkeys():
    """Return hotkeys."""
    return [
        None,
        "not a dict",
        {"id": "missing-hotkey", "label": "Missing Hotkey"},
        {"id": "blank-hotkey", "label": "Blank Hotkey", "hotkey": ""},
    ]

