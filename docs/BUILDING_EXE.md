# Building The Windows EXE

This project now includes a PyInstaller build path.

From PowerShell in the project root:

```powershell
.\tools\build_exe.ps1 -Clean
```

The script uses the project `.venv` by default. If `.venv` does not exist, it
asks before creating it. It also asks before installing or updating dependencies.

To create/use `.venv` and install/update dependencies without prompting:

```powershell
.\tools\build_exe.ps1 -Clean -Yes
```

The built app lands at:

```text
dist\Wisp\Wisp.exe
```

For a smaller/faster build without local Whisper STT, vector-memory embeddings,
document readers, Copilot SDK, or TTS provider SDKs:

```powershell
.\tools\build_exe.ps1 -Clean -Lite
```

or double-click/run:

```bat
.\tools\build_exe_light.bat
```

The light build lands at:

```text
dist\WispLite\WispLite.exe
```

Use `-SkipInstall` if dependencies are already installed:

```powershell
.\tools\build_exe.ps1 -Clean -SkipInstall
```

Use `-UseGlobalPython` only if you intentionally want to build outside the
project virtual environment.

Notes:

- API keys are not bundled. Users should enter them in Settings so they are saved to the OS keychain.
- `.env.example` is bundled as a template, but your local `.env` is not included.
- If packaging fails on a missing optional dependency, install it into `.venv` and rerun the script.
