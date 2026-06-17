"""Tests for test core paths env."""

import importlib


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
