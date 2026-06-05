# Wisp — native macOS shell + Python brain sidecar

This directory is the from-scratch macOS rewrite described in
[`../MACOS_NATIVE_PLAN.md`](../MACOS_NATIVE_PLAN.md) (repo root, gitignored).
**Hybrid Option A**: a native Swift/AppKit app owns every OS-bound API on the
main thread; the existing OS-agnostic Python `core/` runs as a headless sidecar
over a newline-delimited JSON seam. Existing normal Qt product windows
(Settings, Chat, Memory, Plugin Manager, Agent) are reused through a separate
Qt UI host so the macOS app can recover UI parity without reintroducing the old
tray/overlay/hotkey/audio crash surface.

Parity with the Windows/Qt app is tracked in
[`../docs/MACOS_PARITY.md`](../docs/MACOS_PARITY.md). The current native UI is a
functional skeleton; the overlay loads the existing Wisp doll state art when
launched from the repo and only falls back to a circle if those assets are
unavailable.

```
macos/
├── brain/                     # Python sidecar (runs the existing core/ brain)
│   ├── wisp_brain/            #   protocol.py · handlers.py · host.py
│   └── tests/test_brain_host.py
└── Sources/Wisp/             # Swift app (compiles on macOS only)
    ├── Bridge/               #   Protocol · BrainClient · QtUIBridge
    ├── App/                  #   main · AppDelegate
    ├── Overlay/              #   OverlayPanel (NSPanel + SwiftUI)
    └── Tray/                 #   StatusItem (NSStatusItem)
macos/ui_host/                 # Python/Qt host for reusable product windows
```

## Status — Phase 1 (skeleton + brain handshake)

| Piece | State |
|---|---|
| Python sidecar (`wisp_brain`) + JSON seam | **Done & tested** (`test_brain_host.py`, 5/5, runs on any OS) |
| Swift `BrainClient` / `Protocol` / overlay / tray | **Scaffolded** — needs a Mac to compile/run |
| Embedded-Python bundling + signing/notarization | Not started (Phase 1's riskiest part) |

Phase checkboxes live in `../MACOS_NATIVE_PLAN.md` §6.

## The protocol (Swift ⇄ Python)

Newline-delimited JSON, one object per line. Defined by
[`brain/wisp_brain/protocol.py`](brain/wisp_brain/protocol.py) and mirrored in
[`Sources/Wisp/Bridge/Protocol.swift`](Sources/Wisp/Bridge/Protocol.swift).

```
request   {"id": Int, "method": String, "params": {...}}        host  → brain
response  {"id": Int, "ok": Bool, "result": <any> | "error": String}  brain → host
event     {"event": String, "id": Int?, "data": <any>}          brain → host
```

Streaming methods (`brain.query`, `brain.echo`) emit `reply.chunk` events tagged
with the originating request `id`, then return the full text as the response
`result`. `brain.cancel {"target": id}` cooperatively stops a stream.
**Invariant:** large binary (PCM audio) never crosses this channel — only paths do.

Implemented methods: `ping`, `brain.echo` (streaming demo), `brain.query`
(streaming, wired to `core.query_pipeline` + `core.llm_clients.client`),
`brain.transcribe` (Swift-recorded audio path → faster-whisper text),
`brain.tts.synthesize` (text → WAV path for Swift playback), `brain.cancel`,
`brain.memory.add`, `brain.memory.search`, `__shutdown__`.

## Run the verified part now (any OS)

```bash
cd macos/brain
python tests/test_brain_host.py          # or: pytest tests/test_brain_host.py
```

This spawns `python -m wisp_brain.host` and exercises ping, id-tagged streaming,
concurrency, cancel, and error propagation — the whole transport, no LLM keys or
models needed.

## Run the Swift app (on a Mac)

**One-command Mac handoff:** double-click `Start Wisp.command` in the repo root.
It bootstraps `.venv` from `requirements-macos.lock` when needed, validates the
Python brain sidecar and Swift package, creates a dev `Wisp.app` bundle with
macOS privacy usage strings, writes logs, then launches the native Swift app.

**Fast validation pass without launching:** from the repo root, run:

```bash
bash scripts/macos_phase1_validate.sh
```

That runs the Python sidecar transport test, `swift test`, and `swift build`.
Every run writes timestamped logs under `build_logs/macos_phase1_<timestamp>/`
including `environment.log`, `python-brain-sidecar.log`, `swift-test.log`, and
`swift-build.log`; the dev app wrapper is summarized in `dev-app-bundle.log`.
At exit, the launcher also writes `build_logs/wisp-macos-logs_<timestamp>.zip`;
send that zip for debugging instead of converting logs to `.docx`.
If it passes, launch the live menubar/overlay handshake with:

```bash
bash scripts/macos_phase1_validate.sh --run
```

`--run` also writes `wisp-app-run.log`. If the app crashes hard, the same log
folder includes `recent_diagnostic_reports.txt` with matching macOS crash report
names from `~/Library/Logs/DiagnosticReports`.

Once Wisp launches, test these in order:

1. Menubar `✦` shows `Brain: ok (...)`.
2. Menubar `Show Prompt` opens the native prompt panel.
3. Prompt `Echo` mode streams a response without API keys.
4. Menubar `Run Echo Smoke` does the same from the tray.
5. Copy text in any app, select text in the frontmost app, then run menubar
   `Context Snapshot`. It should report active app, clipboard, selected text
   when Accessibility is trusted, and the focused window title when available.
6. Run menubar `Permission Snapshot` and confirm Accessibility, Screen
   Recording, and Microphone states are visible.
7. Run menubar `Capture Screen Smoke`. If macOS asks for Screen Recording,
   grant it in System Settings, rerun the command, then verify the saved
   `screen-capture-*.png` in the same `build_logs/macos_phase1_<timestamp>/`
   folder.
8. Menubar `Open Run Logs` opens the current log/artifact folder in Finder.
9. Click the floating overlay; it should open the prompt.
10. Menubar `Toggle Overlay` hides/shows the floating panel.
11. Menubar `Settings`, `New Chat`, `Last Chat`, `Memory`, `Plugin Manager`,
    `Start Agent Task`, and `Agent Task History` should open the existing Qt
    product windows from the separate UI host.
12. Press `Ctrl-Option-Space`. If macOS asks for Accessibility permission, grant
   it in System Settings, then use `Retry Hotkey Permission`.
13. Prompt `Query` mode exercises the real `brain.query` path once `.env` and
    the Python dependencies are ready; it includes selected text and ambient
    active-app/clipboard context when available.
14. Prompt `Query+Screen` captures the main display, saves the PNG beside the
    logs, attaches it to `brain.query`, and streams the model response.
15. Menubar `Start Voice Query`, speak, then `Stop Voice Query`. Swift records
    a WAV, Python transcribes it with faster-whisper, and the transcript feeds
    Query mode.
16. Menubar `Speak Last Response` synthesizes the prompt response to a WAV in
    the log folder and plays it natively with AVFoundation.
17. Menubar `Remember Prompt` stores the prompt text in the existing memory
    store; `Search Memory` retrieves relevant facts for the prompt text.

`Start Wisp (Mac Native).command` is kept as an alias to `Start Wisp.command`.

**Manual:**

```bash
cd macos
# Dev: point at this checkout instead of a bundled runtime.
export WISP_BRAIN_PYTHON=$(which python3)
export WISP_BRAIN_DIR="$PWD/brain"
export WISP_REPO_ROOT="$PWD/.."      # so the sidecar can import `core`
../build/WispNative/Wisp.app/Contents/MacOS/Wisp  # after validation builds it
swift test                            # protocol framing tests
```

If double-click is blocked ("cannot be opened"), run once:
`chmod +x "Start Wisp.command" "Start Wisp (Mac Native).command"` (the git index
already marks both executable, so a fresh clone/pull should not need this).

`brain.query` additionally needs the `core/` runtime deps (see repo
`requirements.txt`) and provider API keys/`.env`; `ping`/`brain.echo` do not.

## Next

- **Phase 1 finish:** embed python-build-standalone, bundle `wisp_brain` + `core`,
  wrap in an Xcode app target, and prove deep-signing + notarization early
  (plan §7–§9).
- **Phase 2:** the overlay/tray skeleton here graduates to the real doll states.
- **Phase 3+:** CGEvent hotkey tap → intent picker → `brain.query`; then capture,
  audio, context, hardening.
