# macOS Native Threading

Wisp uses worker threads for hotkeys, capture, transcription, LLM streaming,
TTS streaming, model probes, OAuth flows, and memory maintenance. On macOS,
some native frameworks are not safe to touch from arbitrary workers.

Use these two rules for new code:

1. AppKit, Quartz/CoreGraphics, CoreAudio/PortAudio, and screen capture must run
   through `core.system.main_thread.run_on_main()`.
2. SDK/client construction and keychain operations that can enter OpenSSL or the
   Security framework must run through `core.system.native_locks.native_init_lock()`
   or its narrow aliases, `ssl_init_lock()` and `keychain_lock()`.

The same native-init lock is shared by LLM clients, TTS clients, model listing,
settings probes, memory summaries, online search, filler audio baking, and OAuth
keychain storage. This prevents two cold worker threads from initializing
OpenSSL/Security at the same time after a fresh install or dependency rebuild.

Known protected boundaries:

- `core.audio` and `core.stt`: sounddevice stream open/start/stop/close.
- `core.capture` and `core.context_fetcher`: screen capture uses macOS'
  out-of-process `screencapture` helper instead of in-process `mss`.
- `core.platform_utils`: simple copy/paste key combos use out-of-process
  `osascript`; window enumeration and app activation still hop to the main
  thread while they remain PyObjC-backed.
- `core.capture`, `core.context_fetcher`, and paste-back in `main`: macOS
  clipboard reads/writes use `pbpaste`/`pbcopy` instead of pyperclip.
- `core.hotkeys` and `ui.intent_overlay`: no pynput global event tap on macOS.
- `core.llm_clients.client`, `core.tts`, `core.filler_bake`,
  `core.memory_store.store`, and `core.context_fetcher`: SDK client creation.
- `core.secret_store` and `core.auth.*`: keyring/keychain access.
- `core.context_fetcher.start_fs_watcher()`: uses watchdog polling on macOS
  instead of the native FSEvents observer to avoid `_watchdog_fsevents` callback
  threads in the Qt/PyObjC process.

When adding a new provider SDK or native macOS API, wrap the smallest possible
construction/access block. Do not hold the lock for a full streaming response
unless the native library requires it.
