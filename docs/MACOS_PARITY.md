# Wisp Windows/macOS Parity Contract

The native macOS app is not meant to become a separate product. It is a native
shell around the same Wisp brain. Keep product behavior synchronized by treating
the Python brain, config schema, and IPC protocol as shared contracts; only
OS-bound surfaces differ by platform.

## Source Of Truth

| Layer | Source of truth | Windows implementation | macOS implementation |
|---|---|---|---|
| LLM routing, prompts, tools | `core/llm_clients`, `core/query_pipeline`, `core/tool_registry` | Python direct calls | Python sidecar via `brain.query` |
| Memory | `core/memory_store` | Python direct calls | Python sidecar via `brain.memory.*` |
| Agent runtime | `core/agent` | Python direct calls | Python sidecar via future `brain.agent.*` |
| Config and secrets | `.env`, `config.py`, `core/secret_store` | Existing settings UI | Existing Qt settings via macOS UI host |
| Hotkeys | Product shortcut spec | Python/Qt/pynput path | Swift `CGEvent` tap |
| Overlay and native shell | Product UI spec in this file | Qt overlay/tray | AppKit/SwiftUI overlay + tray |
| Product windows | Existing Qt widgets | Qt Settings/Chat/Memory/Agent/Plugins | Same Qt windows in a separate UI host |
| Capture/context/audio | Capability contract below | Existing Python helpers | Swift native APIs |

Rule: Swift should not reimplement brain behavior. Swift may gather native
context, capture audio/screen, render native shell UI, and call protocol
methods. Existing normal Qt product windows may be reused on macOS, but never by
launching `main.py`; use the dedicated Qt UI host so Swift remains the single
owner of tray, overlay, hotkeys, capture, audio, and permissions.

## Capability Contract

These are the cross-platform capabilities the product expects. Each platform can
implement them differently, but the user-facing behavior should match.

| Capability | Shared brain/API contract | Windows status | macOS status |
|---|---|---:|---:|
| Launch app | `Start Wisp.bat` / `Start Wisp.command` starts Wisp | Done | In progress |
| Floating overlay | Shows Wisp state: idle/listening/thinking/speaking; opens prompt/chat | Done | Uses existing doll art when available |
| Tray/menu | Start/quit, status, settings/actions | Done | In progress; opens Qt product windows |
| Intent picker | Caller rows from `CALLER_*` config; W/A/D + custom intent | Done | Missing |
| Text prompt | Send custom prompt to `brain.query` | Done | Basic |
| Streaming reply | Render streamed chunks from model | Done | Basic |
| Selected text | Include selected text when enabled | Done | Basic AX probe |
| Clipboard context | Include clipboard when enabled | Done | Basic probe |
| Screenshot context | Include screenshot according to caller config | Done | Full-display smoke; region UI missing |
| Ambient app/window context | Active app/window/document context | Done | Basic active app/window |
| Voice query | Record mic, transcribe, query | Done | In progress |
| TTS playback | Synthesize and play response audio | Done | In progress |
| Memory add/search | `core.memory_store` | Done | Basic tray bridge + Qt Memory window |
| Settings UI | Edits same `.env` keys | Done | Qt window host wired |
| Chat window | Persistent conversation UI | Done | Qt window host wired |
| Snip overlay | Region capture UI | Done | Missing |
| Agent task window | Run/inspect agent tasks | Done | Qt window host wired |
| Plugin/tool manager | Manage tool plugins | Done | Qt window host wired |
| Packaging | Distributable app | PyInstaller path | Missing |

## Protocol Contract

Every macOS feature that needs brain behavior must use a protocol method. Add a
handler and a test before wiring UI.

| Method | Purpose | Status |
|---|---|---:|
| `ping` | Sidecar liveness | Done |
| `brain.echo` | Streaming transport smoke | Done |
| `brain.query` | Main prompt/query path | Done |
| `brain.transcribe` | Audio path to text | Done |
| `brain.tts.synthesize` | Text to WAV path | Done |
| `brain.memory.add` | Store memory fact | Done |
| `brain.memory.search` | Retrieve memory block | Done |
| `brain.agent.run` | Agent task execution | Missing |
| `settings.get` / `settings.set` | Shared config editing | Missing |
| `tools.list` / `tools.run` | Tool/plugin UI integration | Missing |

## UI Parity Rules

The native shell should be implemented in SwiftUI/AppKit, and existing Qt
product windows can be reused when they are normal top-level windows. Workflows
should match Windows:

1. The overlay is the Wisp mascot/state surface, not a generic circle.
2. The prompt must expose the same caller/intents configured in `.env`.
3. Settings must read/write the same `.env` keys as the Windows settings UI; the
   current macOS path reuses that exact Qt settings dialog.
4. Context toggles must map to the same config semantics.
5. Mac-only permission prompts should be additive, not a different product flow.

## Definition Of Done For A Shared Feature

A feature is parity-ready only when:

1. The shared brain behavior lives in `core/` or a `brain.*` sidecar handler.
2. Windows and macOS use the same config keys and prompt/query semantics.
3. There is at least one platform-neutral Python test for the brain behavior.
4. There is a macOS build/runtime checklist item in `MACOS_NATIVE_PLAN.md`.
5. The parity table above is updated from Missing/In progress to Done.

## Near-Term macOS Parity Order

1. Stabilize the hybrid Qt UI host on the Mac test machine.
2. Implement caller/intent picker from `config.CALLER_ROWS`.
3. Implement region snip overlay and screenshot context.
4. Decide whether Chat/Settings/Memory stay Qt long-term or are incrementally
   ported after parity is recovered.
5. Build signed/notarized app packaging.
