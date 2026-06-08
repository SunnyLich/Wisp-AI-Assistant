# Wisp Code Overview

Wisp is a desktop assistant with a Python/Qt product UI on Windows and a
Swift/AppKit product UI in progress on macOS. Both surfaces share the same
configuration, model routing, local memory, tool, and agent contracts; the macOS
Swift host talks to a Python brain sidecar for OS-agnostic backend work.

## Top-Level Layout

- `main.py` wires the application together: Qt lifecycle, hotkeys, context
  buffering, memory, intent flow, chat windows, and voice/snip interactions.
- `config.py` loads runtime configuration from `.env` and keychain-backed
  secrets.
- `core/` contains service and integration code: LLM routing, audio, TTS/STT,
  capture, context fetching, memory, auth, tool discovery, and agent execution.
- `core/system/` defines canonical paths, platform setup, and environment-file
  helpers.
- `core/agent/` contains the serializable agent task contract, runner runtime
  primitives, and scoped agent execution.
- `core/auth/` contains provider authentication helpers for ChatGPT, GitHub, and
  Copilot.
- `core/llm_clients/` contains the streaming LLM client and fallback route
  parsing for LLM requests.
- `core/memory_store/` contains memory storage and explicit memory-command
  parsing.
- `ui/` contains Qt widgets and dialogs: the icon overlay, chat window,
  settings, intent picker, snip overlay, memory viewer, and agent task UI.
- `ui/agent/`, `ui/settings_panel/`, and `ui/shared/` group the largest UI
  domains and shared widget helpers.
- Root-level modules such as `core.llm`, `core.agent_runner`, `ui.settings`, and
  `ui.agent_task_mockup` are compatibility aliases for older imports.
- `tools/installed/` is the local script-tool plugin directory discovered by
  `core.tool_registry`.
- `tests/` covers parser helpers, config/settings behavior, model route
  fallback logic, tool discovery, secret storage, TTS config, and the agent
  runner.
- `macos/Sources/Wisp/` contains the Swift/AppKit macOS host, while
  `macos/brain/` contains its Python sidecar.

## Plugin Contract

Plugin authors should not write Swift to support macOS. Third-party plugins
belong in the shared Python plugin/runtime layer: Python packages under
`plugins/<name>/__init__.py` and local script tools under `tools/installed/`.
Both Windows and macOS must discover the same plugin metadata, hooks, tray
actions, and model-callable tools from that shared layer.

The native macOS app is only a generic host for this contract. Swift may render
plugin names, paths, hook names, tool names, status, and declared tray actions
from `brain.plugins.list`, then invoke those declared actions through
`brain.plugins.run_action`. Plugin-specific business logic, provider calls,
hooks, tools, and future custom plugin behavior must stay in Python/shared
contracts so a plugin does not need separate Windows and macOS implementations.

## Runtime Flow

1. On Windows, `main.App` starts Qt, registers hotkeys, initializes the overlay,
   and warms TTS/STT connections. On macOS, the Swift host starts AppKit
   surfaces and a Python brain sidecar.
2. A caller hotkey captures selected text or a screen snippet, then opens the
   intent picker.
3. The selected intent builds a prompt with optional ambient context, documents,
   tool access, memory retrieval, and screenshots.
4. `core.llm` routes the request to the configured provider with fallback
   routes where enabled.
5. Reply chunks feed the overlay/chat UI and TTS stream while memory receives
   the completed exchange.

## Quality Notes

- `core.system.env_utils` centralizes env-file IO and typed environment parsing. Prefer
  it over ad hoc `int(os.getenv(...))` and string-only boolean checks.
- Keep shared contracts and pure helpers in `core/`; keep PyQt object creation,
  signal wiring, and widget layout in `ui/`.
- The largest extraction candidates are `core/agent_runner.py`,
  `ui/agent_task_mockup.py`, `ui/settings.py`, `core/llm.py`, and
  `core/context_fetcher.py`. Split these only around stable boundaries such as data
  models, provider adapters, view components, and pure parsers.
- Keep UI modules responsible for presentation and user interaction; move pure
  parsing, formatting, and persistence helpers into `core/` or small UI helper
  modules with tests.
- Configuration changes should include a settings test and a reload test when
  the value can be changed at runtime.

## Development Checks

Run the full test suite from the repository root:

```powershell
pytest
```
