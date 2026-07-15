"""Headless macOS smoke test.

Run on a real macOS machine (or the GitHub Actions macos runner) to confirm the
app's platform-sensitive modules import and the real pyobjc-backed window helpers
run without raising. Requires no Accessibility/Screen-Recording permissions and no
visible display — it only checks that nothing crashes and return types are sane.

    QT_QPA_PLATFORM=offscreen python scripts/macos_smoke.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Run from the repo root so `core`, `ui`, `config` import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    """Support command-line helper for scripts macos smoke for main."""
    if sys.platform != "darwin":
        print(f"SKIP: not macOS (sys.platform={sys.platform!r})")
        return 0

    import core.platform_utils as pu

    assert pu.IS_MAC, "IS_MAC should be True on darwin"
    assert pu.COPY_COMBO == "cmd+c", pu.COPY_COMBO
    assert pu.PASTE_COMBO == "cmd+v", pu.PASTE_COMBO

    # Real CoreGraphics / AppKit calls. On a headless runner with no granted
    # permissions these must still return safe values rather than raising.
    wid = pu.get_foreground_window()
    assert isinstance(wid, int), type(wid)
    assert isinstance(pu.list_visible_windows(), list)
    assert isinstance(pu.get_window_title(wid), str)
    assert isinstance(pu.get_window_pid(wid), int)
    pu.set_foreground_window(0)  # no-op path must not raise

    # Import-time check for the rest of the platform-sensitive surface.
    import config  # noqa: F401
    import core.capture  # noqa: F401
    import core.context_fetcher as cf
    assert cf._config_dir().endswith(os.path.join("Library", "Application Support")), cf._config_dir()
    import core.hotkeys  # noqa: F401
    import ui.intent_overlay  # noqa: F401
    import ui.overlay  # noqa: F401

    print("macOS smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
