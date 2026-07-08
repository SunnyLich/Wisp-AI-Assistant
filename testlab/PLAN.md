# Wisp Testlab — living plan

Goal: replace the manual per-release checklist ("install STT/TTS on 3 platforms,
try each feature, watch for macOS crashes") with one command per platform:

    python testlab/run.py --tier release

Everything in `testlab/` is gitignored (self-ignoring `.gitignore` with `*`), is
NOT wired into the app, and only drives the app from the outside through the
same worker IPC the real supervisor uses. Reports land in `testlab/reports/`.

## Design

- Each check is a standalone script under `testlab/checks/`. It prints human
  logs and ends with one machine line: `LAB_RESULT: {json}`.
- `run.py` executes each check in its own subprocess (native crash in a check
  cannot kill the runner), with a timeout and a per-check log file, then writes
  `report.md` + `report.json` and exits nonzero on any failure.
- Checks use the app's own seams, never mocks of the feature under test:
  real worker processes (`runtime.supervisor.ipc.WorkerClient`), real installer
  code (`core.optional_deps`), real models, real API keys (OS keychain / .env).
- Isolation: side-effecting checks run children with `WISP_REPO_ROOT` pointed
  at a scratch dir (memory/chats stay clean; the user's `.env` is copied in so
  provider settings still apply) and `WISP_OPTIONAL_PACKAGES_DIR` for installs.

## Tiers

- `smoke`   — minutes: app boots, real LLM replies, TTS synthesizes, STT
              transcribes known speech, hotkeys register, GUI widgets render.
- `release` — smoke + simulated-user end-to-end flow + macOS native harness
              (darwin only). The per-release gate.
- `deep`    — release + fresh STT/TTS installs into a scratch dir (large
              downloads; run when installer/deps code changed).

## Checks

| check         | what it proves                                                   | tier    | status |
|---------------|------------------------------------------------------------------|---------|--------|
| app_boot      | all 4 real workers spawn, ping, shut down cleanly, no crash      | smoke   | DONE (verified 2026-07-08, both rungs) |
| llm_query     | real streaming LLM reply via real brain worker + os-stored key   | smoke   | DONE (verified: gemini-2.5-flash, marker echo) |
| tts_function  | real Kokoro/cloud synth produces audio bytes (worker IPC path)   | smoke   | DONE (verified: 2.6s non-silent WAV + device playback) |
| stt_roundtrip | known speech -> real faster-whisper path -> transcript matches   | smoke   | DONE (verified: 100% match, large-v3 CUDA) |
| hotkeys       | native worker registers the real global hotkeys                  | smoke   | DONE (partial-with-running-Wisp -> SKIP) |
| gui_smoke     | real windows render offscreen (wraps run_personal_os_tests)      | smoke   | DONE (7 windows + screenshots) |
| flow_e2e      | simulated user: hotkey->intent->reply->spoken TTS + chat request | release | DONE (verified: real LLM + Kokoro speech) |
| macos_native  | ssl-race + macos_smoke crash harnesses (darwin only)             | release | DONE (wired; needs a run on the Mac) |
| install_stt   | fresh faster-whisper install via real installer + real inference | deep    | DONE (verified: 263 MB scratch install + CUDA verify) |
| install_tts   | fresh Kokoro install via real installer + real synth             | deep    | DONE (verified: 4.7 GB GPU install + CUDA synth) |

## Platform notes

- The lab must run on win32 / darwin / linux; checks declare platforms and
  self-SKIP with a reason otherwise (e.g. hotkeys on a headless Mac session —
  RegisterEventHotKey needs the console session, environmental not a bug).
- The lab code is now committed; `.artifacts/` and `reports/` stay gitignored.
  `.github/workflows/testlab.yml` runs it on ubuntu/macos runners with
  `--no-spend` (no keys in CI); token-spending checks stay on real machines.

## Open items

- Watch the first Testlab workflow runs on ubuntu/macos and fix platform
  quirks (espeak-ng availability for Kokoro, runner timings); a real-machine
  Mac run with keys/audio is still worth one pass per release.
- Possible later additions: live-voice (Gemini Live) session check, addon
  host boot check, staged-apply (restart_apply=True) install variant,
  dictation UI paste-back with --real-desktop.
- Findings so far:
  - STT_COMPUTE_TYPE=auto (hand-edited .env) bypasses the Blackwell cuBLAS
    self-heal in core/stt_device.py build_model - flagged as a background task.
  - FIXED 2026-07-08: app_boot caught the UI worker segfaulting on macOS under
    non-cocoa Qt platforms (offscreen CI/headless): keep_overlay_visible_across_apps
    handed a fake winId() to pyobjc. Guarded via _qt_platform_is_cocoa() in
    core/platform_utils.py + regression tests in tests/test_platform_macos.py.
