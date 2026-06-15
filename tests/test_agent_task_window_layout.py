from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    pytest.importorskip("PySide6", reason="PySide6 not installed") is None,
    reason="PySide6 not installed",
)


def _qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _form_layouts(widget):
    from PySide6.QtWidgets import QFormLayout, QWidget

    seen = set()
    found = []

    def visit_layout(layout):
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


def test_agent_task_forms_expand_across_platform_styles():
    _qapp()
    from PySide6.QtWidgets import QFormLayout
    from ui.agent.task_window import AgentTaskDialog

    dialog = AgentTaskDialog()
    try:
        dialog._toggle_advanced_settings()
        forms = _form_layouts(dialog)

        assert len(forms) >= 6
        assert {
            form.fieldGrowthPolicy()
            for form in forms
        } == {QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow}
    finally:
        dialog.deleteLater()


def test_agent_task_title_field_uses_extra_window_width():
    app = _qapp()
    from ui.agent.task_window import AgentTaskDialog

    dialog = AgentTaskDialog()
    try:
        dialog.show()
        dialog.resize(600, 520)
        app.processEvents()
        narrow_width = dialog.title_edit.width()

        dialog.resize(900, 520)
        app.processEvents()
        wide_width = dialog.title_edit.width()

        assert wide_width > narrow_width + 100
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_agent_communication_forms_expand_across_platform_styles():
    _qapp()
    from PySide6.QtWidgets import QFormLayout
    from ui.agent.task_window import (
        AgentCommunicationDialog,
        AgentCommunicationMapWindow,
        AgentNudgeDialog,
        AgentTaskDialog,
    )

    task_dialog = AgentTaskDialog()
    map_window = AgentCommunicationMapWindow(task_dialog)
    communication_dialog = AgentCommunicationDialog(["Planner", "Builder"])
    nudge_dialog = AgentNudgeDialog(["Planner", "Builder"])
    widgets = [task_dialog, map_window, communication_dialog, nudge_dialog]
    try:
        forms = [form for widget in widgets for form in _form_layouts(widget)]

        assert forms
        assert {
            form.fieldGrowthPolicy()
            for form in forms
        } == {QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow}
    finally:
        for widget in reversed(widgets):
            widget.close()
            widget.deleteLater()
