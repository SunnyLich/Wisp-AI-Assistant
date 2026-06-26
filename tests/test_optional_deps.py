"""Tests for runtime optional dependency installation helpers."""

from __future__ import annotations

import sys


def test_optional_deps_dev_install_uses_current_python(monkeypatch, tmp_path):
    """Repo/dev launches should keep using the active Python's pip."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps.sys, "frozen", False, raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", sys.executable)

    command = optional_deps.pip_install_command(["elevenlabs>=1.0.0"])

    assert command[:4] == [sys.executable, "-m", "pip", "install"]
    assert "--progress-bar=raw" in command
    assert command[command.index("--target") + 1] == str(target)
    assert command[-1] == "elevenlabs>=1.0.0"


def test_optional_deps_frozen_install_uses_bundled_uv(monkeypatch, tmp_path):
    """Packaged launches must not run Wisp.exe as `python -m pip`."""
    from core import optional_deps

    suffix = ".exe" if sys.platform == "win32" else ""
    bundle = tmp_path / "_internal"
    uv = bundle / "bin" / f"uv{suffix}"
    uv.parent.mkdir(parents=True)
    uv.write_text("", encoding="utf-8")
    target = tmp_path / "data" / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps, "REPO_ROOT", tmp_path / "data")
    monkeypatch.setattr(optional_deps.sys, "frozen", True, raising=False)
    monkeypatch.setattr(optional_deps.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", str(tmp_path / "Wisp.exe"))
    monkeypatch.setattr(optional_deps.shutil, "which", lambda _name: "")

    command = optional_deps.pip_install_command(["kokoro>=0.9.4", "soundfile"])

    assert command[:3] == [str(uv), "pip", "install"]
    assert "-m" not in command
    assert "--python-version" in command
    assert command[command.index("--target") + 1] == str(target)
    assert command[-2:] == ["kokoro>=0.9.4", "soundfile"]


def test_optional_deps_frozen_install_explains_missing_uv(monkeypatch, tmp_path):
    """A packaged build without bundled uv should fail clearly before starting pip."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps, "REPO_ROOT", tmp_path / "data")
    monkeypatch.setattr(optional_deps.sys, "frozen", True, raising=False)
    monkeypatch.setattr(optional_deps.sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", str(tmp_path / "Wisp.exe"))
    monkeypatch.setattr(optional_deps.shutil, "which", lambda _name: "")

    try:
        optional_deps.pip_install_command(["kokoro>=0.9.4"])
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected missing uv to raise")

    assert "uv was not bundled" in message
    assert "rebuild Wisp" in message
