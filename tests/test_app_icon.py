from __future__ import annotations

from ui.shared import app_icon


class _FakeApp:
    def __init__(self) -> None:
        self.application_name = ""
        self.display_name = ""
        self.desktop_file_name = ""

    def setApplicationName(self, value: str) -> None:
        self.application_name = value

    def setApplicationDisplayName(self, value: str) -> None:
        self.display_name = value

    def setDesktopFileName(self, value: str) -> None:
        self.desktop_file_name = value


def test_app_icon_path_prefers_native_platform_assets() -> None:
    assert app_icon.app_icon_path("win32").name == "app.ico"
    assert app_icon.app_icon_path("darwin").name == "app.icns"
    assert app_icon.app_icon_path("linux").name == "app.png"


def test_install_app_icon_sets_application_metadata_without_qt(monkeypatch) -> None:
    fake = _FakeApp()
    monkeypatch.setattr(app_icon, "app_icon_path", lambda platform=None: None)

    assert app_icon.install_app_icon(fake, platform="linux") is None

    assert fake.application_name == "Wisp"
    assert fake.display_name == "Wisp"
    assert fake.desktop_file_name == "wisp"


def test_windows_app_user_model_id_is_noop_off_windows() -> None:
    assert app_icon.set_windows_app_user_model_id(platform="linux") is False


def test_linux_desktop_entry_written_and_stable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    path = app_icon.ensure_linux_desktop_entry(platform="linux")

    assert path == tmp_path / "applications" / "wisp.desktop"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("[Desktop Entry]\n")
    assert "Name=Wisp\n" in text
    assert "Exec=" in text
    assert "StartupWMClass=wisp\n" in text

    first_write = path.stat().st_mtime_ns
    assert app_icon.ensure_linux_desktop_entry(platform="linux") == path
    assert path.stat().st_mtime_ns == first_write


def test_linux_desktop_entry_noop_off_linux(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert app_icon.ensure_linux_desktop_entry(platform="win32") is None
    assert not (tmp_path / "applications").exists()
