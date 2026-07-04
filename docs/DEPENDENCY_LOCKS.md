# Dependency Locks

Wisp keeps `requirements/requirements.txt` as the human-edited runtime dependency manifest.
Developer-only tools live in `requirements/requirements-dev.txt`, and packaging-only tools
live in `requirements/requirements-build.txt`. Installs use exact lock files:
`requirements/requirements-windows.lock`, `requirements/requirements-linux.lock`,
`requirements/requirements-macos.lock`, `requirements/requirements-dev.lock`, and
`requirements/requirements-build.lock`.

The lock matters because Wisp crosses several native macOS boundaries:
PySide6/Qt Cocoa, PortAudio through `sounddevice`, PyObjC Quartz/AppKit,
PDFium through `liteparse`, Security-framework SSL setup through SDK clients,
and Torch/ONNX-style binary wheels through local speech/memory tooling. A fresh
unlocked reinstall can move any of those native wheels even when the app code
has not changed.

## Updating Dependencies

1. Edit `requirements/requirements.txt` when adding, removing, or intentionally upgrading a
   runtime dependency. Edit `requirements/requirements-dev.txt` for local tooling such as
   pytest, Ruff, and MyPy. Edit `requirements/requirements-build.txt` for packaging tools
   such as PyInstaller.
2. Regenerate the lock files with `uv`:

   ```bash
   bash scripts/compile_dependency_locks.sh
   ```

   On Windows, the PowerShell equivalent is:

   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File scripts\compile_dependency_locks.ps1
   ```

   To refresh only one target, pass `windows`, `linux`, `macos`, `dev`, or
   `build`. The older `bash scripts/compile_macos_lock.sh` command remains as
   a macOS-only compatibility wrapper.

3. Run the platform smoke/stress checks you can access. For macOS:

   ```bash
   python scripts/macos_smoke.py
   python scripts/macos_testbot.py ssl-race --iterations 20
   ```

4. Commit the edited `.txt` manifest and its regenerated `.lock` files
   together. If tooling changed, also commit `pyproject.toml` when relevant.

CI installs from lock files. Lock verification compiles under the committed
locks as constraints so routine checks do not upgrade floating dependency
ranges, then compares the normalized package pins without `uv`'s explanatory
comments. Intentional refreshes happen through
`scripts/compile_dependency_locks.sh`.
