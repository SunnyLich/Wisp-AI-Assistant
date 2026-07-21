"""User-workflow tests for the Addon Manager dialog and its child windows.

Covers ui/addon_manager.py through the surfaces a user operates: the addon
card list, enable toggles, per-addon settings and log windows, dependency
approval/repair, archive/folder install, and the singleton opener. The core
addon manager and distribution installers are faked at the boundary; the Qt
widgets, layouts, and window chrome are real and run offscreen.
"""
from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import ui.addon_manager as addon_manager_ui
from ui.addon_manager import (
    AddonManagerDialog,
    AddonSettingsDialog,
    _runtime_action_label,
    _runtime_summary,
    open_addon_manager,
)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication(["wisp-addon-ui-tests"])


class FakeAddonManager:
    def __init__(self, addons: list[dict] | None = None, settings: dict[str, list] | None = None):
        self.addons = addons or []
        self.settings = settings or {}
        self.enabled_calls: list[tuple[str, bool]] = []
        self.setting_calls: list[tuple[str, str, object]] = []
        self.repair_calls: list[str] = []
        self.repair_result: dict = {"ready": True}
        self.repair_error: Exception | None = None
        self.load_all_calls = 0

    def summaries(self) -> list[dict]:
        return [dict(addon) for addon in self.addons]

    def set_enabled(self, addon_id: str, enabled: bool) -> bool:
        self.enabled_calls.append((addon_id, enabled))
        return True

    def get_settings(self, addon_id: str) -> list[dict]:
        return [dict(row) for row in self.settings.get(addon_id, [])]

    def set_setting(self, addon_id: str, key: str, value) -> None:
        self.setting_calls.append((addon_id, key, value))

    def repair_environment(self, addon_id: str) -> dict:
        self.repair_calls.append(addon_id)
        if self.repair_error is not None:
            raise self.repair_error
        return dict(self.repair_result)

    def load_all(self) -> None:
        self.load_all_calls += 1


class _FakeMessageBox:
    """Records QMessageBox traffic; question() answers from `answer`."""

    StandardButton = QMessageBox.StandardButton

    def __init__(self):
        self.questions: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.answer = QMessageBox.StandardButton.No

    def install(self, monkeypatch) -> None:
        recorder = self

        class _Box:
            StandardButton = QMessageBox.StandardButton

            @staticmethod
            def question(_parent, _title, text, *args, **kwargs):
                recorder.questions.append(text)
                return recorder.answer

            @staticmethod
            def information(_parent, _title, text, *args, **kwargs):
                recorder.infos.append(text)

            @staticmethod
            def warning(_parent, _title, text, *args, **kwargs):
                recorder.warnings.append(text)

        monkeypatch.setattr(addon_manager_ui, "QMessageBox", _Box)


def _addon(**overrides) -> dict:
    base = {
        "id": "demo.tools",
        "name": "Demo Tools",
        "enabled": True,
        "description": "Adds demo helpers.",
        "path": "C:/addons/demo",
        "hooks": ["on_query"],
        "tools": ["demo_lookup"],
        "permissions": {"clipboard": True, "network": False},
        "settings": [],
        "logs": "",
    }
    base.update(overrides)
    return base


@pytest.fixture
def fake_manager(monkeypatch) -> FakeAddonManager:
    import core.addon_manager as core_addon_manager

    manager = FakeAddonManager()
    monkeypatch.setattr(core_addon_manager, "get_manager", lambda: manager)
    return manager


def _dialog(fake_manager) -> AddonManagerDialog:
    return AddonManagerDialog()


def _all_text(widget: QWidget) -> str:
    return "\n".join(label.text() for label in widget.findChildren(QLabel))


def _button(widget: QWidget, text: str) -> QPushButton:
    matches = [b for b in widget.findChildren(QPushButton) if b.text() == text]
    assert matches, f"no button labeled {text!r}"
    return matches[0]


def test_dialog_lists_addon_metadata_on_cards(qapp, fake_manager):
    fake_manager.addons = [
        _addon(),
        _addon(
            id="heavy.addon",
            name="Heavy Addon",
            enabled=False,
            error="boom line one\nfinal error line",
            runtime={
                "tier": "2",
                "ready": False,
                "packages": ["numpy", "pillow"],
                "error": "env missing",
            },
        ),
    ]
    dialog = _dialog(fake_manager)
    try:
        text = _all_text(dialog)
        assert "Demo Tools" in text
        assert "Adds demo helpers." in text
        assert "Hooks: on_query" in text
        assert "Model tools: demo_lookup" in text
        assert "Permissions: clipboard, network" in text
        assert "Dependency env: needs install" in text
        assert "Packages: numpy, pillow" in text
        assert "env missing" in text
        # Only the last line of a multi-line addon error is shown.
        assert "final error line" in text
        assert "boom line one" not in text
        # Dependency button only exists for the tier-2 addon.
        assert len([b for b in dialog.findChildren(QPushButton) if b.text() == "Install env"]) == 1
        enable_boxes = dialog.findChildren(QCheckBox)
        assert [box.isChecked() for box in enable_boxes] == [True, False]
    finally:
        dialog.close()


def test_dialog_empty_state_when_manager_unavailable(qapp, monkeypatch):
    import core.addon_manager as core_addon_manager

    def _unavailable():
        raise RuntimeError("manager not started")

    monkeypatch.setattr(core_addon_manager, "get_manager", _unavailable)
    dialog = AddonManagerDialog()
    try:
        assert "No addons loaded" in _all_text(dialog)
    finally:
        dialog.close()


def test_enable_toggle_reaches_manager(qapp, fake_manager):
    fake_manager.addons = [_addon()]
    dialog = _dialog(fake_manager)
    try:
        box = dialog.findChildren(QCheckBox)[0]
        box.setChecked(False)
        box.setChecked(True)
        assert fake_manager.enabled_calls == [("demo.tools", False), ("demo.tools", True)]
    finally:
        dialog.close()


@pytest.mark.parametrize(
    ("runtime", "action", "summary"),
    [
        ({"needs_approval": True}, "Approve env", "Dependency env: needs approval"),
        ({"ready": True}, "Repair env", "Dependency env: ready"),
        ({"ready": False}, "Install env", "Dependency env: needs install"),
    ],
)
def test_runtime_action_and_summary_labels(runtime, action, summary):
    assert _runtime_action_label(runtime) == action
    assert _runtime_summary(runtime) == summary


def test_settings_window_widgets_save_through_manager(qapp, fake_manager):
    fake_manager.addons = [_addon()]
    fake_manager.settings["demo.tools"] = [
        {"key": "notify", "label": "Notify", "type": "bool", "value": "true"},
        {"key": "mode", "label": "Mode", "type": "choice", "value": "b", "options": ["a", "b", "c"]},
        {"key": "prefix", "label": "Prefix", "type": "text", "value": "wisp"},
        {"key": "limit", "label": "Limit", "type": "number", "value": 5, "help": "Max rows"},
    ]
    dialog = _dialog(fake_manager)
    try:
        dialog._open_settings_window("demo.tools", "Demo Tools", [])
        settings_dialog = dialog._settings_dialogs["demo.tools"]

        box = settings_dialog.findChildren(QCheckBox)[0]
        assert box.isChecked() is True
        box.setChecked(False)

        combo = settings_dialog.findChildren(QComboBox)[0]
        assert combo.currentText() == "b"
        combo.setCurrentText("c")

        prefix_edit, limit_edit = settings_dialog.findChildren(QLineEdit)
        assert prefix_edit.text() == "wisp"
        assert limit_edit.text() == "5"
        assert limit_edit.placeholderText() == "number"
        assert limit_edit.toolTip() == "Max rows"
        prefix_edit.setText("wisp2")
        prefix_edit.editingFinished.emit()

        assert fake_manager.setting_calls == [
            ("demo.tools", "notify", "false"),
            ("demo.tools", "mode", "c"),
            ("demo.tools", "prefix", "wisp2"),
        ]
    finally:
        dialog.close()


def test_settings_window_reuses_visible_dialog_and_reloads(qapp, fake_manager):
    fake_manager.addons = [_addon()]
    fake_manager.settings["demo.tools"] = [
        {"key": "prefix", "label": "Prefix", "type": "text", "value": "one"}
    ]
    dialog = _dialog(fake_manager)
    try:
        dialog._open_settings_window("demo.tools", "Demo Tools", [])
        first = dialog._settings_dialogs["demo.tools"]
        assert first.findChildren(QLineEdit)[0].text() == "one"

        fake_manager.settings["demo.tools"] = [
            {"key": "prefix", "label": "Prefix", "type": "text", "value": "two"}
        ]
        dialog._open_settings_window("demo.tools", "Demo Tools", [])
        assert dialog._settings_dialogs["demo.tools"] is first
        # Replaced widgets die via deleteLater; flush them before counting.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert [edit.text() for edit in first.findChildren(QLineEdit)] == ["two"]
    finally:
        dialog.close()


def test_settings_window_without_settings_shows_empty_state(qapp, fake_manager):
    fake_manager.addons = [_addon()]
    dialog = _dialog(fake_manager)
    try:
        dialog._open_settings_window("demo.tools", "Demo Tools", [])
        settings_dialog = dialog._settings_dialogs["demo.tools"]
        assert "does not expose settings" in _all_text(settings_dialog)
    finally:
        dialog.close()


def test_settings_box_skips_rows_without_keys(qapp, fake_manager):
    fake_manager.addons = [_addon()]
    dialog = _dialog(fake_manager)
    try:
        dialog._open_settings_window("demo.tools", "Demo Tools", [])
        settings_dialog = dialog._settings_dialogs["demo.tools"]
        assert settings_dialog._settings_box([{"key": "  ", "label": "Blank"}]) is None
    finally:
        dialog.close()


def test_log_window_prefers_latest_manager_logs_and_reloads(qapp, fake_manager):
    fake_manager.addons = [_addon(logs="fresh line")]
    dialog = _dialog(fake_manager)
    try:
        dialog._open_log_window("demo.tools", "Demo Tools", "stale line")
        log_dialog = dialog._log_dialogs["demo.tools"]
        assert log_dialog.findChildren(QTextEdit)[0].toPlainText() == "fresh line"

        fake_manager.addons = [_addon(logs="")]
        dialog._open_log_window("demo.tools", "Demo Tools", "stale line")
        assert dialog._log_dialogs["demo.tools"] is log_dialog
        assert log_dialog.findChildren(QTextEdit)[0].toPlainText() == "No log output yet."
    finally:
        dialog.close()


def test_repair_environment_requires_approval(qapp, fake_manager, monkeypatch):
    fake_manager.addons = [_addon()]
    dialog = _dialog(fake_manager)
    boxes = _FakeMessageBox()
    boxes.install(monkeypatch)
    runtime = {
        "tier": "2",
        "ready": False,
        "packages": ["numpy"],
        "python_requirement": ">=3.12",
        "env_path": "C:/envs/demo",
    }
    try:
        boxes.answer = QMessageBox.StandardButton.No
        dialog._repair_environment("demo.tools", "Demo Tools", runtime)
        assert fake_manager.repair_calls == []
        prompt = boxes.questions[0]
        assert "numpy" in prompt
        assert ">=3.12" in prompt
        assert "C:/envs/demo" in prompt

        boxes.answer = QMessageBox.StandardButton.Yes
        fake_manager.repair_result = {"ready": True}
        dialog._repair_environment("demo.tools", "Demo Tools", runtime)
        assert fake_manager.repair_calls == ["demo.tools"]
        assert boxes.infos == ["Dependency environment is ready."]

        fake_manager.repair_result = {"ready": False, "error": "pip exploded"}
        dialog._repair_environment("demo.tools", "Demo Tools", runtime)
        assert boxes.warnings[-1] == "pip exploded"

        fake_manager.repair_error = RuntimeError("no network")
        dialog._repair_environment("demo.tools", "Demo Tools", runtime)
        assert boxes.warnings[-1] == "no network"
    finally:
        dialog.close()


def test_install_archive_reloads_and_shows_new_addon(qapp, fake_manager, monkeypatch, tmp_path):
    import core.addon_distribution as addon_distribution

    fake_manager.addons = [_addon()]
    dialog = _dialog(fake_manager)
    boxes = _FakeMessageBox()
    boxes.install(monkeypatch)
    archive = tmp_path / "extra.wisp"
    archive.write_bytes(b"zip-bytes")
    installed: list = []

    def _fake_install(path, replace=False):
        installed.append((path, replace))
        fake_manager.addons.append(_addon(id="extra.addon", name="Extra Addon"))
        return {"id": "extra.addon"}

    monkeypatch.setattr(addon_distribution, "install_addon_archive", _fake_install)
    monkeypatch.setattr(
        addon_manager_ui,
        "QFileDialog",
        type("_Files", (), {"getOpenFileName": staticmethod(lambda *a, **k: (str(archive), "filter"))}),
    )
    try:
        dialog._install_archive()
        assert installed == [(archive, False)]
        assert fake_manager.load_all_calls == 1
        assert "extra.addon" in boxes.infos[0]
        assert "Extra Addon" in _all_text(dialog)
    finally:
        dialog.close()


def test_install_archive_cancel_and_failure(qapp, fake_manager, monkeypatch, tmp_path):
    import core.addon_distribution as addon_distribution

    fake_manager.addons = [_addon()]
    dialog = _dialog(fake_manager)
    boxes = _FakeMessageBox()
    boxes.install(monkeypatch)
    calls: list = []
    monkeypatch.setattr(
        addon_distribution,
        "install_addon_archive",
        lambda path, replace=False: calls.append(path) or (_ for _ in ()).throw(ValueError("bad zip")),
    )
    files = {"result": ("", "")}
    monkeypatch.setattr(
        addon_manager_ui,
        "QFileDialog",
        type("_Files", (), {"getOpenFileName": staticmethod(lambda *a, **k: files["result"])}),
    )
    try:
        dialog._install_archive()
        assert calls == []
        assert boxes.warnings == []

        files["result"] = (str(tmp_path / "broken.zip"), "filter")
        dialog._install_archive()
        assert boxes.warnings == ["bad zip"]
    finally:
        dialog.close()


def test_addon_archive_install_failure_matrix_is_in_band(qapp, fake_manager, monkeypatch, tmp_path):
    """Archive install surfaces download/storage/dependency faults without escaping."""
    import core.addon_distribution as addon_distribution

    fake_manager.addons = [_addon()]
    dialog = _dialog(fake_manager)
    boxes = _FakeMessageBox()
    boxes.install(monkeypatch)
    archive = tmp_path / "addon.wisp"
    archive.write_bytes(b"fixture")
    monkeypatch.setattr(
        addon_manager_ui,
        "QFileDialog",
        type(
            "_Files",
            (),
            {"getOpenFileName": staticmethod(lambda *a, **k: (str(archive), "filter"))},
        ),
    )
    faults = (
        ConnectionError("Network access is unavailable."),
        RuntimeError("The package source is unavailable."),
        OSError("Available disk space is insufficient."),
        PermissionError("Filesystem permission is insufficient."),
        RuntimeError("Dependency versions conflict."),
    )
    try:
        for fault in faults:
            monkeypatch.setattr(
                addon_distribution,
                "install_addon_archive",
                lambda *_args, fault=fault, **_kwargs: (_ for _ in ()).throw(fault),
            )
            dialog._install_archive()
            assert boxes.warnings[-1] == str(fault)
    finally:
        dialog.close()


def test_install_folder_flow(qapp, fake_manager, monkeypatch, tmp_path):
    import core.addon_distribution as addon_distribution

    fake_manager.addons = [_addon()]
    dialog = _dialog(fake_manager)
    boxes = _FakeMessageBox()
    boxes.install(monkeypatch)
    folder = tmp_path / "my-addon"
    folder.mkdir()
    installed: list = []

    def _fake_install(path, replace=False):
        installed.append((path, replace))
        return {"id": "folder.addon"}

    monkeypatch.setattr(addon_distribution, "install_addon_folder", _fake_install)
    monkeypatch.setattr(
        addon_manager_ui,
        "QFileDialog",
        type("_Files", (), {"getExistingDirectory": staticmethod(lambda *a, **k: str(folder))}),
    )
    try:
        dialog._install_folder()
        assert installed == [(folder, False)]
        assert fake_manager.load_all_calls == 1
        assert "folder.addon" in boxes.infos[0]
    finally:
        dialog.close()


def test_addon_folder_install_failure_matrix_is_in_band(qapp, fake_manager, monkeypatch, tmp_path):
    """Folder install cancellation and installer faults stay in the add-on UI."""
    import core.addon_distribution as addon_distribution

    fake_manager.addons = [_addon()]
    dialog = _dialog(fake_manager)
    boxes = _FakeMessageBox()
    boxes.install(monkeypatch)
    selected = {"path": ""}
    monkeypatch.setattr(
        addon_manager_ui,
        "QFileDialog",
        type(
            "_Files",
            (),
            {"getExistingDirectory": staticmethod(lambda *a, **k: selected["path"])},
        ),
    )
    calls = []

    try:
        monkeypatch.setattr(
            addon_distribution,
            "install_addon_folder",
            lambda *_args, **_kwargs: calls.append("install"),
        )
        dialog._install_folder()
        assert calls == []

        selected["path"] = str(tmp_path / "addon")
        faults = (
            ConnectionError("Network access is unavailable."),
            RuntimeError("The package source is unavailable."),
            OSError("Available disk space is insufficient."),
            PermissionError("Filesystem permission is insufficient."),
            RuntimeError("Verification fails."),
            RuntimeError("Dependency versions conflict."),
        )
        for fault in faults:
            monkeypatch.setattr(
                addon_distribution,
                "install_addon_folder",
                lambda *_args, fault=fault, **_kwargs: (_ for _ in ()).throw(fault),
            )
            dialog._install_folder()
            assert boxes.warnings[-1] == str(fault)
    finally:
        dialog.close()


def test_addon_dependency_repair_failure_matrix_is_in_band(qapp, fake_manager, monkeypatch):
    """Dependency repair approval and every provision fault remain controlled."""
    fake_manager.addons = [_addon()]
    dialog = _dialog(fake_manager)
    boxes = _FakeMessageBox()
    boxes.install(monkeypatch)
    runtime = {
        "tier": "2",
        "ready": False,
        "packages": ["example-package"],
        "python_requirement": ">=3.12",
        "env_path": "C:/envs/demo",
    }
    try:
        boxes.answer = QMessageBox.StandardButton.No
        dialog._repair_environment("demo.tools", "Demo Tools", runtime)
        assert fake_manager.repair_calls == []

        boxes.answer = QMessageBox.StandardButton.Yes
        faults = (
            ConnectionError("Network access is unavailable."),
            RuntimeError("The package source is unavailable."),
            OSError("Available disk space is insufficient."),
            PermissionError("Filesystem permission is insufficient."),
            RuntimeError("Verification fails."),
            RuntimeError("Dependency versions conflict."),
        )
        for fault in faults:
            fake_manager.repair_error = fault
            dialog._repair_environment("demo.tools", "Demo Tools", runtime)
            assert boxes.warnings[-1] == str(fault)
    finally:
        dialog.close()


def test_open_addons_folder_uses_shared_reveal_boundary(qapp, monkeypatch, tmp_path):
    import core.system.paths as core_paths
    from core.system import file_browser

    addons_dir = tmp_path / "addons-home"
    monkeypatch.setattr(core_paths, "ADDONS_DIR", addons_dir)
    revealed = []
    monkeypatch.setattr(file_browser, "reveal_path", revealed.append)

    dialog = AddonManagerDialog()
    try:
        _button(dialog, "Open addons folder").click()
        qapp.processEvents()

        assert addons_dir.is_dir()
        assert revealed == [addons_dir]
    finally:
        dialog.close()


def test_clear_layout_empties_nested_layouts(qapp):
    holder = QWidget()
    outer = QVBoxLayout(holder)
    inner = QVBoxLayout()
    inner.addWidget(QLabel("inner label"))
    outer.addLayout(inner)
    outer.addWidget(QLabel("outer label"))

    AddonSettingsDialog._clear_layout(outer)

    assert outer.count() == 0
    AddonSettingsDialog._clear_layout(None)  # must not raise
    holder.deleteLater()


def test_open_addon_manager_reuses_visible_singleton(qapp, fake_manager, monkeypatch):
    monkeypatch.setattr(addon_manager_ui, "_dialog_instance", None)
    monkeypatch.setattr(sys, "platform", "win32")
    parent = QWidget()
    try:
        open_addon_manager(parent)
        first = addon_manager_ui._dialog_instance
        assert first is not None
        assert first.parent() is None  # non-Linux never parents to the overlay
        open_addon_manager(parent)
        assert addon_manager_ui._dialog_instance is first
    finally:
        if addon_manager_ui._dialog_instance is not None:
            addon_manager_ui._dialog_instance.close()
        parent.deleteLater()


def test_open_addon_manager_keeps_parent_on_linux(qapp, fake_manager, monkeypatch):
    monkeypatch.setattr(addon_manager_ui, "_dialog_instance", None)
    monkeypatch.setattr(sys, "platform", "linux")
    parent = QWidget()
    try:
        open_addon_manager(parent)
        assert addon_manager_ui._dialog_instance.parent() is parent
    finally:
        if addon_manager_ui._dialog_instance is not None:
            addon_manager_ui._dialog_instance.close()
        parent.deleteLater()
