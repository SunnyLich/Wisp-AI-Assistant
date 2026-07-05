# Building The Windows EXE

This project includes a PyInstaller build path for the supervisor runtime
(`runtime.supervisor.app`).

From PowerShell in the project root:

```powershell
.\tools\build_exe.ps1 -Clean
```

The script uses a dedicated `.venv-build` environment by default. If
`.venv-build` does not exist, it creates it automatically. The script first
looks for a local Python matching `.python-version`; if none is available, it
installs/uses `uv` to provision that Python for `.venv-build`, seeding `pip` in
the new environment. If an existing `.venv-build` is missing `pip`, the script
bootstraps it with `ensurepip` before installing dependencies. It then checks
dependencies and installs anything that is missing or out of date — no prompts.
Already-satisfied packages are skipped. Builds must use Python `3.12`, matching
`.python-version`; the script stops with a clear message if the selected build
environment or global Python does not match that Python minor line.

Keep the normal `.venv` for development and tests. The separate build
environment keeps local experiments, optional GPU packages, and developer tools
out of portable release bundles unless you intentionally opt into them.

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
build virtual environment. Use `-UseDevVenv` on Windows, or `--use-dev-venv` on
Linux/macOS, only when you intentionally want PyInstaller to build from the
developer `.venv`.

Notes:

- API keys are not bundled. Users should enter them in Settings so they are saved to the OS keychain.
- `.env.example` is bundled as a template, but your local `.env` is not included.
- The MCP Bridge and UI Lab addons are bundled when present in the checkout and
  seeded into the writable `addons` folder on first launch. Existing addon
  folders are left untouched so user addon configuration, such as MCP Bridge
  `servers.json`, is preserved.
- Portable builds create an `addons` folder next to `Wisp.exe` when that folder
  is writable. Drop addon folders there, or use **Addon Manager > Install
  archive/folder**. If the executable lives in a read-only install location,
  Wisp falls back to the user data addon folder shown by **Open addons folder**.
- The packaged executable starts the same supervisor worker runtime as the
  launchers.
- Packaged no-console runs keep runtime logs under `build_logs/`; the latest
  folder is written to `build_logs/latest_wisp_runtime.txt`. From the tray menu,
  open `Runtime Status` to see worker pids, running/stopped state, and recent
  worker stderr without launching from a terminal.
- Runtime package installs in packaged builds require `uv`. This includes addon
  dependency environments and Settings > Voice installs for optional speech
  packages such as STT/faster-whisper, Kokoro, or ElevenLabs. The Windows build script stages
  `uv.exe` into `tools\uv.exe` before PyInstaller runs, installing `uv` into the
  build Python first if needed, and PyInstaller bundles it with Wisp. If you
  build without the script, place `uv.exe` at `bin\uv.exe` or `tools\uv.exe`
  before running PyInstaller.
- If packaging fails on a missing required dependency, rerun without
  `-SkipInstall` so the build script can install it into `.venv-build`.
- On Windows, if the repo path is long enough to trip the OS path limit during
  `elevenlabs` install, the build script skips that optional package instead of
  failing the whole build. The packaged app still builds. Users who select the
  ElevenLabs TTS provider will see an in-app warning and can install ElevenLabs
  from Settings > Voice into Wisp's user-writable optional packages folder.

## Cross-Platform Portable Builds

Tagged releases are built by `.github/workflows/build.yml`.

Create a release tag that matches the current `pyproject.toml` version:

```powershell
git tag v0.7.2
git push origin v0.7.2
```

The workflow builds:

- Windows: `Wisp-<tag>-windows-x64.zip`
- macOS: `Wisp-<tag>-macos-<arch>.zip`
- Linux: `Wisp-<tag>-linux-x64.tar.gz`

After all platform jobs finish, the workflow creates or updates a draft GitHub
Release and uploads `wisp-release-manifest.json` plus `SHA256SUMS.txt`. The
Settings update button uses the manifest to find the newest build for the
current platform, verify the SHA256 hash, download the matching artifact, and
then apply it through a small helper process when the user chooses **Apply
update**. Users can compare downloaded archives against `SHA256SUMS.txt` from
the release page before unpacking them.

Manual platform build entry points:

```powershell
.\tools\build_exe.ps1 -Clean
```

```bash
./tools/build_exe.sh --clean --yes
./tools/build_macos_app.sh --clean --yes
```

On Linux desktops, starting `tools/build_exe.sh` from a file manager
(double-click) opens a terminal window automatically so build progress stays
visible, and the window waits for Enter before closing so the result stays
readable. Terminal launches behave as before.

For the closest local match to GitHub release artifacts, use the same commands
the workflow uses:

```powershell
.\tools\build_exe.ps1 -Clean -Yes
```

```bash
./tools/build_exe.sh --clean --yes
./tools/build_macos_app.sh --clean --yes
```

The release workflow intentionally delegates to these scripts instead of
duplicating dependency installation or PyInstaller commands in YAML. The normal
CI workflow includes `.github/workflows/build.yml`, `.python-version`, and these
build scripts in its path filters, and the test suite asserts that the release
workflow still calls the local scripts. If someone changes the GitHub build path
without updating the shared scripts, CI should fail before a release artifact is
cut.
