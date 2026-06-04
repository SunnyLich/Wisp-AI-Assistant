# Dependency Locks

Wisp keeps `requirements.txt` as the human-edited dependency manifest. macOS
installs use `requirements-macos.lock`, an exact resolved lock for Python 3.12
on Apple Silicon.

The lock matters because Wisp crosses several native macOS boundaries:
PySide6/Qt Cocoa, PortAudio through `sounddevice`, PyObjC Quartz/AppKit,
PDFium through `liteparse`, Security-framework SSL setup through SDK clients,
and Torch/ONNX-style binary wheels through local speech/memory tooling. A fresh
unlocked reinstall can move any of those native wheels even when the app code
has not changed.

## Updating Dependencies

1. Edit `requirements.txt` when adding, removing, or intentionally upgrading a
   top-level dependency.
2. Regenerate the macOS lock on a Mac, or from another machine with `uv`:

   ```bash
   bash scripts/compile_macos_lock.sh
   ```

3. Run the macOS smoke/stress checks:

   ```bash
   python scripts/macos_smoke.py
   python scripts/macos_testbot.py ssl-race --iterations 20
   ```

4. Commit `requirements.txt` and `requirements-macos.lock` together.

The macOS CI workflow verifies that `requirements-macos.lock` can be regenerated
from `requirements.txt` without changes, then installs from the lock file.
