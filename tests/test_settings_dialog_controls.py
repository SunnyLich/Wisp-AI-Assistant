import os
import sys

import pytest


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_combo_ignores_wheel_when_popup_closed():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import _NoScrollCombo

    class FakeWheelEvent:
        def __init__(self) -> None:
            self.ignored = False

        def ignore(self) -> None:
            self.ignored = True

    app = QApplication.instance() or QApplication(sys.argv)
    combo = _NoScrollCombo()
    event = FakeWheelEvent()
    try:
        combo.addItems(["one", "two"])
        combo.setFocus()

        combo.wheelEvent(event)

        assert event.ignored is True
    finally:
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_memory_panel_loads_on_background_thread(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    import ui.settings_panel.dialog as dialog_module
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    layout = QVBoxLayout(host)
    started: list[dict] = []

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            started.append({"target": target, "name": name, "daemon": daemon, "started": False})

        def start(self) -> None:
            started[-1]["started"] = True

    monkeypatch.setattr(dialog_module.threading, "Thread", FakeThread)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._memory_browser_cv = layout
    dialog._memory_loading = True

    try:
        SettingsDialog._load_memory_panel(dialog)

        assert started
        assert started[0]["name"] == "wisp-memory-settings-load"
        assert started[0]["daemon"] is True
        assert started[0]["started"] is True
    finally:
        host.deleteLater()
        app.processEvents()
