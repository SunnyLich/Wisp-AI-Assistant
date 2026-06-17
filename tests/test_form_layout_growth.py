"""Tests for test form layout growth."""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    pytest.importorskip("PySide6", reason="PySide6 not installed") is None,
    reason="PySide6 not installed",
)


def _qapp():
    """Verify qapp behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _form_layouts(widget):
    """Verify form layouts behavior."""
    from PySide6.QtWidgets import QFormLayout, QWidget

    seen = set()
    found = []

    def visit_layout(layout):
        """Verify visit layout behavior."""
        if layout is None or id(layout) in seen:
            return
        seen.add(id(layout))
        if isinstance(layout, QFormLayout):
            found.append(layout)
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item is None:
                continue
            visit_layout(item.layout())
            child = item.widget()
            if child is not None:
                visit_layout(child.layout())

    visit_layout(widget.layout())
    for child in widget.findChildren(QWidget):
        visit_layout(child.layout())
    return found


def _assert_forms_expand(widgets):
    """Verify assert forms expand behavior."""
    from PySide6.QtWidgets import QFormLayout

    forms = [form for widget in widgets for form in _form_layouts(widget)]
    assert forms
    assert {
        form.fieldGrowthPolicy()
        for form in forms
    } == {QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow}


def test_settings_forms_expand_across_platform_styles():
    """Verify settings forms expand across platform styles behavior."""
    _qapp()
    from ui.settings_panel.dialog import SettingsDialog

    dialog = SettingsDialog()
    try:
        _assert_forms_expand([dialog])
    finally:
        dialog.close()
        dialog.deleteLater()


def test_addon_settings_forms_expand_across_platform_styles():
    """Verify addon settings forms expand across platform styles behavior."""
    _qapp()
    from ui.addon_manager import AddonSettingsDialog

    dialog = AddonSettingsDialog(
        manager=None,
        addon_id="demo",
        addon_name="Demo",
        settings=[
            {"key": "path", "label": "Path", "type": "text", "value": "C:/demo"},
            {"key": "mode", "label": "Mode", "type": "choice", "value": "fast", "options": ["fast", "safe"]},
        ],
    )
    try:
        _assert_forms_expand([dialog])
    finally:
        dialog.close()
        dialog.deleteLater()


def test_macos_ui_host_addon_settings_forms_expand_across_platform_styles():
    """Verify macos ui host plugin settings forms expand across platform styles behavior."""
    _qapp()
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host.emit = lambda *_args, **_kwargs: None
    widget = host._addon_settings_box(
        "demo",
        [
            {"key": "path", "label": "Path", "type": "text", "value": "/tmp/demo"},
            {"key": "enabled", "label": "Enabled", "type": "bool", "value": "true"},
        ],
    )
    try:
        assert widget is not None
        _assert_forms_expand([widget])
    finally:
        if widget is not None:
            widget.deleteLater()
