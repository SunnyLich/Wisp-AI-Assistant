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
def test_settings_memory_tab_does_not_show_stored_facts():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._env = {}
    tab = SettingsDialog._tab_memory(dialog)

    try:
        labels = {label.text() for label in tab.findChildren(QLabel)}
        combined = "\n".join(labels)
        assert "Stored Facts" not in combined
        assert "Stored facts" not in combined
    finally:
        tab.deleteLater()
        app.processEvents()


def test_reset_page_key_mapping_is_scoped():
    from ui.settings_panel.dialog import SettingsDialog

    env = {
        "LLM_PROVIDER": "anthropic",
        "GROQ_API_KEY": "secret",
        "CALLER_COUNT": "3",
        "CALLER_1_HOTKEY": "ctrl+q",
        "CALLER_2_CONTEXT_MEMORY_MODE": "model",
        "BUBBLE_WIDTH": "420",
        "MEMORY_TOP_K": "7",
    }

    assert SettingsDialog._reset_env_keys_for_page("LLM", env) >= {"LLM_PROVIDER"}
    assert "GROQ_API_KEY" not in SettingsDialog._reset_env_keys_for_page("LLM", env)
    assert SettingsDialog._reset_env_keys_for_page("Keybinds", env) >= {
        "CALLER_COUNT",
        "CALLER_1_HOTKEY",
        "CALLER_2_CONTEXT_MEMORY_MODE",
        "HOTKEY_SNIP",
    }
    assert "BUBBLE_WIDTH" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "MEMORY_TOP_K" in SettingsDialog._reset_env_keys_for_page("Memory", env)
    assert SettingsDialog._reset_env_keys_for_page("Tools", env) == set()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_has_reset_page_button():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
        assert "Reset Page…" in button_texts
        assert "Reset All…" in button_texts
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_caller_memory_combo_uses_third_column_second_row():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QVBoxLayout, QWidget

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._caller_blocks = []
    dialog._callers_vlayout = QVBoxLayout(host)
    dialog._fields = {}

    try:
        SettingsDialog._add_caller_block(dialog, intents=[])
        frame = dialog._caller_blocks[0]["widget"]
        memory_pos = None
        for child in frame.findChildren(QWidget):
            layout = child.layout()
            if not isinstance(layout, QGridLayout):
                continue
            for idx in range(layout.count()):
                item = layout.itemAt(idx)
                widget = item.widget()
                if isinstance(widget, QLabel) and widget.text() == "Memory:":
                    memory_pos = layout.getItemPosition(idx)
                    break
            if memory_pos is not None:
                break

        assert memory_pos is not None
        assert memory_pos[:2] == (1, 4)
    finally:
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_refresh_runs_on_background_thread(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.memory_viewer as memory_viewer
    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)
    started: list[dict] = []

    class FakeManager:
        def get_all_facts(self):
            raise AssertionError("refresh should not run on the UI thread")

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            started.append({"target": target, "name": name, "daemon": daemon, "started": False})

        def start(self) -> None:
            started[-1]["started"] = True

    monkeypatch.setattr(memory_viewer.threading, "Thread", FakeThread)
    panel = MemoryPanel(FakeManager(), initial_facts=[])

    try:
        panel.refresh_facts()

        assert started
        assert started[0]["name"] == "wisp-memory-refresh"
        assert started[0]["daemon"] is True
        assert started[0]["started"] is True
    finally:
        panel.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_read_only_hides_mutation_controls():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)

    class FakeManager:
        def get_all_facts(self):
            return []

    panel = MemoryPanel(
        FakeManager(),
        initial_facts=[
            {"id": "fact-1", "text": "I prefer stable settings", "category": "general"}
        ],
        read_only=True,
    )

    try:
        assert not hasattr(panel, "_add_text")
        button_texts = {button.text() for button in panel.findChildren(QPushButton)}
        assert "Add" not in button_texts
        assert "X" not in button_texts
        assert "Refresh" in button_texts
    finally:
        panel.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_add_runs_on_background_thread(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.memory_viewer as memory_viewer
    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)
    started: list[dict] = []

    class FakeManager:
        def add_fact_manual(self, _text, _category):
            raise AssertionError("add should not run on the UI thread")

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            started.append({"target": target, "name": name, "daemon": daemon, "started": False})

        def start(self) -> None:
            started[-1]["started"] = True

    monkeypatch.setattr(memory_viewer.threading, "Thread", FakeThread)
    panel = MemoryPanel(FakeManager(), initial_facts=[])

    try:
        panel._add_text.setText("I prefer fast settings")
        panel._on_add_fact()

        assert panel._add_text.text() == ""
        assert started
        assert started[0]["name"] == "wisp-memory-add"
        assert started[0]["daemon"] is True
        assert started[0]["started"] is True
    finally:
        panel.deleteLater()
        app.processEvents()
