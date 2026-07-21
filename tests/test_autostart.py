from __future__ import annotations

import plistlib
import sys
import types

from core.system import autostart


def test_start_on_login_selects_the_real_source_or_packaged_entry(monkeypatch):
    """Autostart points at the same two launch entries proven by launcher acceptance."""
    monkeypatch.setattr(autostart, "_is_frozen", lambda: False)
    assert autostart._command() == [sys.executable, "-m", "runtime.supervisor.app"]

    monkeypatch.setattr(autostart, "_is_frozen", lambda: True)
    assert autostart._command() == [sys.executable]


def test_windows_start_on_login_writes_and_removes_run_value(monkeypatch):
    """Windows autostart registers the exact hidden source-launch command."""
    values = {}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake_winreg = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_SET_VALUE=1,
        REG_SZ=1,
        CreateKeyEx=lambda *_args: Key(),
        SetValueEx=lambda _key, name, _reserved, _kind, value: values.__setitem__(name, value),
        DeleteValue=lambda _key, name: values.pop(name),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(autostart, "_is_frozen", lambda: False)

    autostart.sync_start_on_login(True, platform="win32")

    command = values[autostart.APP_NAME]
    assert "powershell.exe" in command
    assert "-WindowStyle Hidden" in command
    assert "runtime.supervisor.app" in command

    autostart.sync_start_on_login(False, platform="win32")
    assert values == {}


def test_linux_start_on_login_writes_xdg_desktop_file(tmp_path, monkeypatch):
    """Linux autostart uses the per-user XDG autostart folder."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(autostart, "_command", lambda: ["python", "-m", "runtime.supervisor.app"])

    autostart.sync_start_on_login(True, platform="linux", home=tmp_path)

    path = tmp_path / ".config" / "autostart" / autostart.LINUX_DESKTOP_ID
    text = path.read_text(encoding="utf-8")
    assert "Name=Wisp" in text
    assert 'Exec="python" "-m" "runtime.supervisor.app"' in text
    assert "Icon=" in text
    assert "app.png" in text
    assert "StartupWMClass=Wisp" in text
    assert "Terminal=false" in text

    autostart.sync_start_on_login(False, platform="linux", home=tmp_path)

    assert not path.exists()


def test_macos_start_on_login_writes_launch_agent(tmp_path, monkeypatch):
    """macOS autostart uses a per-user LaunchAgent plist."""
    monkeypatch.setattr(autostart, "_command", lambda: ["python", "-m", "runtime.supervisor.app"])

    autostart.sync_start_on_login(True, platform="darwin", home=tmp_path)

    path = tmp_path / "Library" / "LaunchAgents" / f"{autostart.MACOS_LAUNCH_AGENT_ID}.plist"
    data = plistlib.loads(path.read_bytes())
    assert data["Label"] == autostart.MACOS_LAUNCH_AGENT_ID
    assert data["ProgramArguments"] == ["python", "-m", "runtime.supervisor.app"]
    assert data["RunAtLoad"] is True

    autostart.sync_start_on_login(False, platform="darwin", home=tmp_path)

    assert not path.exists()
