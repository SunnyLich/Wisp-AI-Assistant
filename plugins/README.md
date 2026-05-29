# Mods (Plugins)

Mods extend the **app** itself — adding tray actions, observing queries, contributing model-callable tools, and more.

> **Security:** Mods run in-process with full Python access. Only install mods from sources you trust.

---

## Creating a mod

Drop a folder under `plugins/` containing an `__init__.py`:

```text
plugins/
  my_mod/
    __init__.py
    helper.py      (optional — any extra files your mod needs)
```

All hook functions are **optional**. Define only the ones you need.

---

## Available hooks

```python
# plugins/my_mod/__init__.py

def on_startup(app_context):
    """
    Called once after the app is fully initialised.

    app_context attributes:
      .signals             — OverlaySignals  (emit Qt signals from your mod)
      .model_tool_registry — ToolRegistry    (register model-callable tools)
      .config              — config module   (read live config values)
    """

def on_shutdown():
    """Called before the app exits. Do cleanup here."""

def before_query(prompt: str, context: str) -> tuple[str, str]:
    """
    Called before every LLM query on a worker thread.
    You can inspect or modify the prompt and context.
    Must return (prompt, context) — even if unchanged.

    NOTE: Do NOT touch Qt widgets here. Use app_context.signals to emit
    Qt signals thread-safely if needed.
    """
    return prompt, context

def after_response(text: str):
    """
    Called after the LLM finishes streaming, on a worker thread.
    Useful for logging, analytics, or triggering side effects.
    """

def get_tray_actions() -> list[dict]:
    """
    Return a list of tray menu items to add.
    Each dict: {"label": str, "callback": callable}
    Callbacks run on the Qt main thread.
    """
    return [
        {"label": "My action", "callback": my_function},
    ]

def get_tools() -> list[dict]:
    """
    Contribute model-callable tools (functions the LLM can invoke during inference).
    Each dict needs:
      name         — unique identifier (letters, digits, _ -)
      description  — shown to the LLM to help it decide when to call the tool
      input_schema — JSON Schema describing the inputs
      executor     — callable(inputs: dict) -> str  (the actual implementation)
    """
    return [
        {
            "name": "my_tool",
            "description": "Does something useful.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The input query."}
                },
                "required": ["query"],
            },
            "executor": my_tool_executor,
        }
    ]

def my_tool_executor(inputs: dict) -> str:
    return f"Result for: {inputs.get('query', '')}"
```

---

## Notes

- Mods are loaded at startup in alphabetical order by folder name.
- A mod that raises an exception during loading or in any hook is skipped/logged — it will not crash the app.
- Restart the app after adding or modifying a mod.
- For simple LLM-callable scripts that don't need app access, use `model_tools/` instead.
