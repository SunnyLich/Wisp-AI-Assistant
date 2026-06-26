"""Tests for test core paths env."""

import importlib
import sys


def test_wisp_repo_root_env_overrides_writable_paths(tmp_path, monkeypatch):
    """Verify wisp repo root env overrides writable paths behavior."""
    import core.system.paths as paths

    monkeypatch.setenv("WISP_REPO_ROOT", str(tmp_path))
    try:
        reloaded = importlib.reload(paths)
        assert reloaded.REPO_ROOT == tmp_path
        assert reloaded.MEMORY_DIR == tmp_path / "memory"
        assert reloaded.MODEL_TOOLS_DIR == tmp_path / "model_tools"
        assert tmp_path.exists()
    finally:
        monkeypatch.delenv("WISP_REPO_ROOT", raising=False)
        importlib.reload(paths)


def test_frozen_addons_dir_prefers_writable_executable_sibling(tmp_path, monkeypatch):
    """Packaged portable builds expose an addons folder next to the executable."""
    import core.system.paths as paths

    exe_dir = tmp_path / "portable" / "Wisp"
    exe_dir.mkdir(parents=True)
    exe_path = exe_dir / ("Wisp.exe" if sys.platform == "win32" else "Wisp")
    exe_path.write_text("", encoding="utf-8")
    meipass = exe_dir / "_internal"
    meipass.mkdir()

    with monkeypatch.context() as mp:
        mp.delenv("WISP_REPO_ROOT", raising=False)
        mp.delenv("WISP_ADDONS_DIR", raising=False)
        mp.setattr(sys, "frozen", True, raising=False)
        mp.setattr(sys, "_MEIPASS", str(meipass), raising=False)
        mp.setattr(sys, "executable", str(exe_path))
        reloaded = importlib.reload(paths)

        assert reloaded.ADDONS_DIR == exe_dir / "addons"
        assert reloaded.ADDONS_DIR.is_dir()

    importlib.reload(paths)


def test_wisp_addons_dir_env_overrides_default_addons_path(tmp_path, monkeypatch):
    """An explicit addon directory override is honored and created."""
    import core.system.paths as paths

    addons_dir = tmp_path / "custom-addons"
    with monkeypatch.context() as mp:
        mp.setenv("WISP_ADDONS_DIR", str(addons_dir))
        reloaded = importlib.reload(paths)

        assert reloaded.ADDONS_DIR == addons_dir
        assert addons_dir.is_dir()

    importlib.reload(paths)
