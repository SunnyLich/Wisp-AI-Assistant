# Pure-Python macOS Architecture Notes

This file records how the pure-Python macOS target uses older macOS code as
evidence. Old patches are not automatically ported. Each subsystem must classify
old behavior before implementation.

## Classification Rules

| Classification | Meaning | Action |
|---|---|---|
| Keep as invariant | Product/platform rule that remains true with the new process split | Implement directly and test |
| Replace with clean structure | Workaround caused by the old mixed Qt/PyObjC/audio/ML process | Do not port; solve through ownership boundaries |
| Keep only as fallback | Useful when primary native code cannot satisfy a real Mac requirement | Isolate behind the responsible worker |
| Do not port | Patch only existed to survive a broken structure | Leave behind |

## Subsystem Ledger

| Subsystem | Old references | Classification | Pure-Python decision |
|---|---|---|---|
| Protocol stdout | `core.macos_helper.protocol`, `macos/brain/wisp_brain/protocol.py` | Keep as invariant | All workers use newline-delimited JSON and reserve stdout for protocol messages. |
| Worker isolation | `core.macos_helper.client`, `core.macos_helper.host`, `macos/brain/wisp_brain/host.py` | Keep as invariant | Supervisor owns worker lifecycle, timeouts, stderr capture, and crash recovery. |
| UI main thread | `main.py`, `macos/ui_host/wisp_qt_ui_host.py` | Keep as invariant | `wisp-ui` is the only PySide process; all widget work happens on the Qt main thread. |
| `run_on_main` escape hatch | `core/system/main_thread.py` | Replace with clean structure | Native/audio work leaves the UI process, so UI should not need native main-thread escapes. |
| macOS safe-mode feature blocks | `core/system/macos_safety.py` | Replace with clean structure | Native/audio/ML packages are separated by process, so production behavior should not be disabled by default just because it is macOS. |
| Hotkeys | `core/hotkeys.py` | Keep only as fallback | Current `wisp-native` reuses the isolated old backend first; replace with a purpose-built PyObjC/Carbon implementation after live validation. |
| Clipboard and pasteback | `core/capture.py`, `core/platform/macos_native.py` | Keep as invariant plus fallback | Preserve clipboard and focus safety. AppKit pasteboard is primary; CLI/AppleScript fallbacks stay isolated in `wisp-native`. |
| Screen capture | `core/capture.py`, `core/platform/macos_native.py` | Keep only as fallback | `screencapture` is retained in `wisp-native`; primary ScreenCaptureKit/PyObjC implementation should replace it when validated. |
| Audio/STT | `core/audio.py`, `core/stt.py`, `core/macos_helper/handlers.py` | Keep as invariant | `sounddevice`, `faster_whisper`, `torch`, and related native packages are allowed only in `wisp-audio`. |
| Brain behavior | `macos/brain/wisp_brain`, `core/query_pipeline.py` | Keep as invariant | Reuse the existing headless brain sidecar; do not import UI/native/audio stacks. |
| Product flows | `main.py` hotkey, query, rewrite, snip, voice, context, and settings methods | Keep as invariant; replace structure | `macos_py.supervisor.flows.FlowController` owns routing: hotkey -> intent -> native context -> brain stream -> UI reply -> optional audio, plus rewrite/pasteback, snip, voice, chat, memory, and settings reload. |
| Drag/drop and buffered context | `main.py`, `ui/overlay.py`, `ui/drop_zone.py` | Keep as invariant | Dropped/buffered context is collected by UI/native, consumed once by the supervisor, summarized in UI, and sent to `wisp-brain` as prompt context. |
| Chat window LLM calls | `ui/chat_window.py`, `main.py` | Replace with clean structure | `wisp-ui` keeps the existing chat widget, but its `send_fn` emits `ui.chat.request`; supervisor streams `brain.chat` chunks back through UI methods. |
| Memory viewer | `ui/memory_viewer.py`, `core/memory_store` | Replace with clean structure | `wisp-ui` uses a small cache/proxy for the existing viewer. Open/add/update/delete events are routed to `wisp-brain` memory handlers. |
| Agent task/history | `ui/agent/task_window.py`, `core.agent`, `macos/brain/wisp_brain` | Replace with clean structure | Task collection reuses the shared Qt spec dialog, but run/history/cancel/approval behavior is protocol-backed: `wisp-ui` emits task/history intents, the supervisor streams `brain.agent.*`, and only `wisp-brain` runs `core.agent`. |
| Plugin manager | `ui/plugin_manager.py`, `core.plugin_manager`, `macos/brain/wisp_brain` | Replace with clean structure | Query-time plugin hooks and plugin-manager list/action behavior run in `wisp-brain`; the macOS Python UI shows a protocol-fed plugin dialog instead of importing `core.plugin_manager`. |
| Existing Swift path | `macos/Sources/Wisp` | Parallel target | Read for parity only. The pure-Python target does not link or require Swift. |

## Current Implementation Status

- `macos_py.supervisor` provides worker lifecycle and ID-correlated IPC.
- `macos_py.workers.native_host` exposes permissions, hotkey start/stop, context, capture, clipboard, pasteback, and settings-open methods.
- `macos_py.workers.ui_host` owns PySide UI windows and response surfaces through protocol methods.
- `macos_py.workers.brain_host` wraps the existing headless brain host.
- `macos_py.workers.audio_host` owns audio/STT/TTS imports and playback.
- `macos_py.supervisor.flows` wires feature flows across workers and contains
  regression coverage for caller/query, rewrite/pasteback, snip, voice, chat,
  memory, dropped context, settings reload, and agent task/history routing.

## Live-Mac Follow-Up

The first implementation is intentionally testable off-Mac. These pieces still
need live macOS validation before release:

- TCC prompts and permission state transitions.
- Hotkey delivery from the isolated native worker.
- Screen region capture backend choice.
- Fullscreen Spaces, multi-monitor overlay placement, and sleep/wake recovery.
- Developer ID signing, notarization, and stable TCC identity.
