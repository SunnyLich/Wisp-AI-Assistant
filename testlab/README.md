# Wisp Testlab

Automated replacements for the manual per-release checks: does STT/TTS
install, does every feature actually function, does anything crash. One
command per platform instead of an afternoon of hand-testing.

```powershell
# Windows (repo venv)
.\.venv\Scripts\python.exe testlab\run.py                 # release gate
.\.venv\Scripts\python.exe testlab\run.py --tier smoke    # quick pass (~3 min warm)
.\.venv\Scripts\python.exe testlab\run.py --tier deep     # + fresh install checks (GBs)
.\.venv\Scripts\python.exe testlab\run.py --list          # what would run here
.\.venv\Scripts\python.exe testlab\run.py --only flow_e2e # one check
```

```bash
# macOS / Linux
./.venv/bin/python testlab/run.py --tier release
```

Reports: `testlab/reports/<timestamp>/report.md` (+ `report.json` and one log
per check). Exit code is nonzero when anything failed. `--no-spend` skips the
two checks that use real LLM tokens (~4 small requests / a few hundred tokens
per run otherwise - trivially inside the AI Studio free tier).

The spending checks default to the app's configured route. To pin a lab-only
route (e.g. keep the lab on a free AI Studio model while the app uses a paid
provider), set `WISP_TESTLAB_LLM_PROVIDER` / `WISP_TESTLAB_LLM_MODEL`.

## What runs

| check         | proves                                                                | tier    |
|---------------|-----------------------------------------------------------------------|---------|
| app_boot      | 4 real workers boot + clean shutdown; the real app process boots headless and writes no crash logs | smoke |
| llm_query     | real streamed LLM reply through the real brain worker with your os-stored key | smoke |
| tts_function  | real synth via the real audio worker; WAV is non-silent; real device playback | smoke |
| stt_roundtrip | known speech -> the app's real whisper path -> transcript matches (no mic needed) | smoke |
| hotkeys       | the real native worker registers your global hotkeys                  | smoke   |
| gui_smoke     | settings/chat/intent/agent windows render offscreen with screenshots  | smoke   |
| flow_e2e      | simulated user: hotkey -> intent -> real LLM reply -> spoken via TTS; plus a chat request | release |
| macos_native  | macOS crash harnesses (ssl-race, macos smoke) - darwin only            | release |
| install_stt   | fresh faster-whisper install via the REAL installer into a scratch dir + CUDA/CPU inference | deep |
| install_tts   | fresh Kokoro install (GPU mode on CUDA machines, ~4.7 GB) + real synthesis | deep |

## Behavior notes

- **You will hear it speak.** tts_function and flow_e2e play short clips
  through the real output device - that is the point. `--no-play` /
  `--real-desktop` flags exist on the individual checks.
- Safe to run while Wisp is open: data writes go to scratch dirs
  (`WISP_REPO_ROOT` sandbox), the boot check sandboxes the single-instance
  lock (Windows/Linux), and hotkey/partial-registration cases turn into SKIPs
  with a note instead of false failures. Conclusive hotkey runs need Wisp
  closed.
- flow_e2e uses a scripted native worker by default so a background run never
  fires the synthetic copy (Ctrl+C) at whatever window you have focused. Run
  `checks/flow_e2e.py --real-desktop` attended for the fully-real variant.
- install_* checks never touch the real `python_packages` dir; they install
  into `testlab/.artifacts/` and delete it after (pass `--keep` to inspect).
  First run on a fresh machine downloads for real; pip/uv caches make reruns
  fast.
- stt_roundtrip pins language=en for its English reference clip (your STT
  language setting is reported, not overridden, in the real app).
- The lab code is committed (CI runs it); only `testlab/.artifacts/` and
  `testlab/reports/` stay gitignored, so run outputs and reply text never land
  in the repo.
- **CI**: `.github/workflows/testlab.yml` runs the lab on `ubuntu-latest` and
  `macos-latest` - on pushes touching `testlab/**` and via manual dispatch
  (tier selectable, default deep). CI uses `--no-spend` (no API keys on
  runners), so llm_query/flow_e2e run only on real machines; the deep tier's
  fresh CPU installs, boot, GUI, and macOS crash harnesses all run for real.

## Per-release routine

1. Windows: `.\.venv\Scripts\python.exe testlab\run.py` (add `--tier deep`
   when installer/dependency pins changed).
2. Mac + Linux: the Testlab GitHub workflow covers boot/GUI/native-crash/fresh
   installs on runners; for the token-spending checks (llm_query, flow_e2e)
   and real audio devices, run `./.venv/bin/python testlab/run.py` on the real
   boxes (git pull now brings the lab along).
3. Read `testlab/reports/<ts>/report.md` per machine (CI uploads it as an
   artifact); a PASS verdict means install + LLM + TTS + STT + boot + E2E flow
   all worked on real code paths.
