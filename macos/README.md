# Wisp macOS Native App

The macOS product direction is now the Swift/AppKit host in `Sources/Wisp`.
It should match the Windows Wisp workflow in look, behavior, configuration,
and feature coverage while moving crash-prone OS work out of the Python/Qt
process.

This `macos/` directory contains the native app and its Python brain sidecar:

```text
macos/
  brain/             Python sidecar for LLM, memory, TTS/STT, and agents
  Sources/Wisp/      Swift/AppKit product host
  Tests/WispTests/   Swift protocol tests
  ui_host/           temporary bridge for windows not yet ported to Swift
```

The Swift app owns the macOS overlay, tray, caller/intent picker, chat, memory,
core settings, plugin manager, snip overlay, native context, screen capture,
voice recording, and audio playback surfaces. The Python sidecar keeps using
the existing OS-agnostic backend modules until their visible windows are ported.

## Default Mac Launch

The shared Python/Qt launcher remains available as a fallback while the Swift UI
reaches full parity:

```bash
Start Wisp.command
```

That launcher creates or refreshes `.venv` from `requirements-macos.lock`, then
runs:

```bash
.venv/bin/python main.py
```

## Native Swift Launch

To run the Swift/AppKit app on a Mac:

```bash
bash scripts/macos_phase1_validate.sh --run
```

or double-click:

```bash
Start Wisp (Mac Native).command
```

This path validates the Python sidecar, Swift package, native menubar/overlay,
and the current Swift parity slice.
