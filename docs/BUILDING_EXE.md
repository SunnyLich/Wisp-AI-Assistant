# Building The Windows EXE

This project includes a PyInstaller build path for the supervisor runtime
(`runtime.supervisor.app`).

From PowerShell in the project root:

```powershell
.\tools\build_exe.ps1 -Clean
```

The script uses the project `.venv` by default. If `.venv` does not exist, it
creates it automatically, then checks dependencies and installs anything that is
missing or out of date — no prompts. Already-satisfied packages are skipped.
Builds must use Python `3.12`, matching `.python-version`; the script stops
with a clear message if the selected `.venv` or global Python does not match
that Python minor line.

(`-Yes` is still accepted for backward compatibility but no longer does anything,
since auto-install is now the default.)

The built app lands at:

```text
dist\Wisp\Wisp.exe
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
- The packaged executable starts the same supervisor worker runtime as the
  launchers.
- Addon dependency environments in packaged builds require `uv`. Place `uv.exe`
  at `bin\uv.exe` or `tools\uv.exe` before building and PyInstaller will bundle
  it into `dist\Wisp\bin\uv.exe`.
- If packaging fails on a missing optional dependency, install it into `.venv` and rerun the script.
- On Windows, if the repo path is long enough to trip the OS path limit during `elevenlabs` install, the build script now skips that optional package instead of failing the whole build. The packaged app will still build, but the ElevenLabs TTS provider will be unavailable unless you enable Windows long paths and reinstall dependencies.

## Cross-Platform Release Builds

Tagged releases are built by `.github/workflows/build.yml`.

Create a release tag that matches `pyproject.toml`:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

The workflow builds:

- Windows: `Wisp-<tag>-windows-x64.zip`
- macOS: `Wisp-<tag>-macos-<arch>.zip`
- Linux: `Wisp-<tag>-linux-x64.tar.gz`

After all platform jobs finish, the workflow creates or updates a draft GitHub
Release and uploads `wisp-release-manifest.json`. The Settings update button
uses that manifest to find the newest build for the current platform, verify
the SHA256 hash, and open the downloaded installer/archive.

Manual platform build entry points:

```powershell
.\tools\build_exe.ps1 -Clean
```

```bash
./tools/build_exe.sh --clean --yes
./tools/build_macos_app.sh --clean --yes
```
