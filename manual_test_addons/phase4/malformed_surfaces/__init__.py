def get_intents():
    return [
        None,
        "not a dict",
        {"label": "", "prompt": ""},
        {"id": "dynamic-missing-prompt", "label": "Dynamic Missing Prompt"},
    ]


def get_notifications():
    return [
        None,
        "not a dict",
        {"title": "No message"},
        {"message": ""},
    ]


def get_hotkeys():
    return [
        None,
        "not a dict",
        {"id": "missing-hotkey", "label": "Missing Hotkey"},
        {"id": "blank-hotkey", "label": "Blank Hotkey", "hotkey": ""},
    ]

