"""Tests for test agent task window layout."""

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
    """Verify the title field is configured to consume available form width."""
    _qapp()
    from PySide6.QtWidgets import QSizePolicy

    from ui.agent.task_window import AgentTaskDialog

    dialog = AgentTaskDialog()
    try:
        assert dialog.title_edit.sizePolicy().horizontalPolicy() in {
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.MinimumExpanding,
        }
        form = dialog.title_edit.parentWidget().layout()
        assert form.fieldGrowthPolicy().name == "AllNonFixedFieldsGrow"
    finally:
        dialog.close()
        dialog.deleteLater()


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


def test_agent_communication_map_uses_one_way_arrows_until_reverse_exists(monkeypatch):
    """Verify directed communications are not always rendered bidirectionally."""
    _qapp()
    from ui.agent.task_window import AgentCommunicationMapWindow, AgentTaskDialog

    task_dialog = AgentTaskDialog()
    map_window = AgentCommunicationMapWindow(task_dialog)
    task_dialog._agent_specs = [
        {"name": "Coordinator", "role": "Coordinator", "provider": "", "model": "", "responsibility": ""},
        {"name": "Builder", "role": "Implementer", "provider": "", "model": "", "responsibility": ""},
    ]
    task_dialog._communication_specs = [
        {
            "from_agent": "Coordinator",
            "to_agent": "Builder",
            "phase": "Planning",
            "trigger": "",
            "message": "",
        }
    ]
    calls: list[bool] = []

    def capture_arrow(*_args, bidirectional: bool = False):
        calls.append(bidirectional)

    monkeypatch.setattr(map_window, "_draw_directed_arrow", capture_arrow)
    try:
        map_window.refresh()

        assert calls == [False]

        calls.clear()
        task_dialog._communication_specs.append(
            {
                "from_agent": "Builder",
                "to_agent": "Coordinator",
                "phase": "Review",
                "trigger": "",
                "message": "",
            }
        )
        map_window.refresh()

        assert calls == [True, True]
    finally:
        map_window.close()
        map_window.deleteLater()
        task_dialog.close()
        task_dialog.deleteLater()


def test_agent_meeting_view_wheel_zoom_persists_across_refit():
    """Verify the meeting graphics view zooms with the mouse wheel."""
    _qapp()
    from PySide6.QtWidgets import QGraphicsScene

    from ui.agent.task_window import _FitGraphicsView

    class _Delta:
        def __init__(self, value: int) -> None:
            self._value = value

        def y(self) -> int:
            return self._value

    class _Wheel:
        def __init__(self, value: int) -> None:
            self._value = value
            self.accepted = False

        def angleDelta(self) -> _Delta:
            return _Delta(self._value)

        def accept(self) -> None:
            self.accepted = True

    scene = QGraphicsScene()
    scene.setSceneRect(0, 0, 1080, 560)
    view = _FitGraphicsView(scene)
    try:
        view.resize(460, 280)
        view.fit_scene()
        fitted_scale = view.transform().m11()
        zoom_in = _Wheel(120)

        view.wheelEvent(zoom_in)
        zoomed_scale = view.transform().m11()
        view.fit_scene()

        assert zoom_in.accepted
        assert zoomed_scale > fitted_scale
        assert view.transform().m11() == pytest.approx(zoomed_scale)
    finally:
        view.close()
        view.deleteLater()


def test_agent_run_window_shows_prominent_finished_banner(tmp_path):
    """Verify completed auto-agent runs are visually obvious."""
    app = _qapp()

    from core.agent.task_spec import AgentTaskSpec
    from ui.agent.task_window import AgentRunWindow

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "final.md").write_text("All done.", encoding="utf-8")
    notices: list[tuple[str, bool]] = []
    spec = AgentTaskSpec(
        title="Finish demo",
        objective="Show completion clearly.",
        scope_folder=str(tmp_path),
        sandbox_mode="workspace-write: scope folder only",
        approval_policy="ask before escalation",
        provider="openai",
        model="gpt-5.5",
        reasoning_effort="medium",
        max_runtime_minutes=5,
        max_turns=3,
        allow_shell=True,
        allow_network=False,
        allow_git=False,
        allow_file_create=True,
        allow_file_edit=True,
        allow_file_delete=False,
    )
    window = AgentRunWindow(
        spec,
        approval_notice_callback=lambda text, resolved: notices.append((text, resolved)),
    )
    try:
        window._on_finished(str(run_dir))

        assert window.completion_banner.isHidden() is False
        assert window.completion_banner_title.text() == "Agent Task Finished"
        assert "Final report is ready" in window.completion_banner_detail.text()
        assert window.tabs.currentWidget() is window.final_view
        assert window.final_view.toPlainText() == "All done."
        assert notices and notices[-1][1] is True
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_agent_communication_i18n_preserves_internal_values():
    app = _qapp()

    import config
    from ui import i18n
    from ui.agent.task_window import AgentCommunicationDialog, AgentCommunicationMapWindow, AgentTaskDialog

    old_language = getattr(config, "APP_LANGUAGE", "")
    old_assistant_language = getattr(config, "ASSISTANT_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    config.ASSISTANT_LANGUAGE = "English"
    i18n.set_language(app=app)
    task_dialog = AgentTaskDialog()
    map_window = AgentCommunicationMapWindow(task_dialog)
    exchange_dialog = AgentCommunicationDialog(["Coordinator", "Builder"])
    widgets = [task_dialog, map_window, exchange_dialog]
    try:
        map_window.refresh()

        role_index = map_window.map_agent_role.findData("Coordinator")
        phase_index = map_window.map_comm_phase.findData("Planning")
        from_index = map_window.map_comm_from.findData("Coordinator")

        assert role_index >= 0
        assert phase_index >= 0
        assert from_index >= 0
        assert map_window.map_agent_role.itemText(role_index) == "\u5354\u8abf\u54e1"
        assert map_window.map_comm_phase.itemText(phase_index) == "\u898f\u5283"
        assert map_window.map_comm_from.itemText(from_index) == "\u5354\u8abf\u54e1"
        assert "Coordinator" not in {
            map_window.map_agent_role.itemText(i)
            for i in range(map_window.map_agent_role.count())
        }
        assert "Planning" not in {
            map_window.map_comm_phase.itemText(i)
            for i in range(map_window.map_comm_phase.count())
        }

        map_window.map_comm_from.setCurrentIndex(from_index)
        map_window.map_comm_to.setCurrentIndex(map_window.map_comm_to.findData("Builder"))
        map_window.map_comm_phase.setCurrentIndex(phase_index)
        map_window._save_exchange_form()

        assert task_dialog._communication_specs[0]["from_agent"] == "Coordinator"
        assert task_dialog._communication_specs[0]["to_agent"] == "Builder"
        assert task_dialog._communication_specs[0]["phase"] == "Planning"
        assert exchange_dialog.from_combo.itemText(
            exchange_dialog.from_combo.findData("Coordinator")
        ) == "\u5354\u8abf\u54e1"
        assert exchange_dialog.phase_combo.itemText(
            exchange_dialog.phase_combo.findData("Planning")
        ) == "\u898f\u5283"
    finally:
        config.APP_LANGUAGE = old_language
        config.ASSISTANT_LANGUAGE = old_assistant_language
        i18n.set_language(app=app)
        for widget in reversed(widgets):
            widget.close()
            widget.deleteLater()
        app.processEvents()


def test_agent_task_defaults_follow_assistant_language(monkeypatch):
    """Verify auto-agent preset fields use the assistant/model language."""
    app = _qapp()

    import config
    from ui.agent.task_window import AgentTaskDialog

    old_language = getattr(config, "ASSISTANT_LANGUAGE", "")
    monkeypatch.setattr(config, "ASSISTANT_LANGUAGE", "Chinese (Traditional)", raising=False)
    dialog = AgentTaskDialog()
    try:
        assert dialog._agent_specs[0]["name"] == "\u5354\u8abf\u54e1"
        assert dialog._agent_specs[1]["name"] == "\u5efa\u69cb\u8005"
        assert dialog._agent_specs[1]["role"] == "\u5be6\u4f5c\u8005"
        assert "\u7a0b\u5f0f\u78bc" in dialog._agent_specs[1]["responsibility"]
        assert dialog._communication_specs[0]["from_agent"] == "\u5354\u8abf\u54e1"
        assert dialog._communication_specs[0]["to_agent"] == "\u5efa\u69cb\u8005"
        assert dialog._communication_specs[0]["phase"] == "\u898f\u5283"
    finally:
        monkeypatch.setattr(config, "ASSISTANT_LANGUAGE", old_language, raising=False)
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_agent_task_preview_localizes_nested_agent_spec(monkeypatch):
    """Verify the task preview uses localized labels and readable nested text."""
    app = _qapp()

    import config
    from core.agent.task_spec import AgentCommunicationSpec, AgentRoleSpec, AgentTaskSpec
    from ui import i18n
    from ui.agent.task_window import AgentTaskDialog

    old_language = getattr(config, "APP_LANGUAGE", "")
    old_assistant_language = getattr(config, "ASSISTANT_LANGUAGE", "")
    monkeypatch.setattr(config, "APP_LANGUAGE", "zh-Hant", raising=False)
    monkeypatch.setattr(config, "ASSISTANT_LANGUAGE", "Chinese (Traditional)", raising=False)
    i18n.set_language(app=app)
    try:
        spec = AgentTaskSpec(
            title="Maze",
            objective="Create a maze game.",
            scope_folder=str(os.getcwd()),
            sandbox_mode="workspace-write: scope folder only",
            approval_policy="ask before escalation",
            provider="openai",
            model="gpt-5.5",
            reasoning_effort="medium",
            max_runtime_minutes=60,
            max_turns=60,
            allow_shell=True,
            allow_network=True,
            allow_git=True,
            allow_file_create=True,
            allow_file_edit=True,
            allow_file_delete=True,
            required_context="The maze was completed.",
            completion_criteria="Create tests.",
            agents=[
                AgentRoleSpec(
                    name="\u5354\u8abf\u54e1",
                    role="\u5354\u8abf\u54e1",
                    provider="same as task",
                    model="same as task",
                    responsibility="\u62c6\u5206\u4efb\u52d9\u3001\u5206\u914d\u5de5\u4f5c\u3001\u5408\u4f75\u6c7a\u7b56\u3001\u5354\u8abf\u885d\u7a81\u3002",
                )
            ],
            communications=[
                AgentCommunicationSpec(
                    from_agent="\u5354\u8abf\u54e1",
                    to_agent="\u5efa\u69cb\u8005",
                    phase="\u898f\u5283",
                    trigger="\u4efb\u52d9\u958b\u59cb\u6642",
                    message="\u8acb\u5148\u6aa2\u67e5\u76ee\u6a19\u3002",
                )
            ],
        )

        formatted = AgentTaskDialog._format_spec(spec)

        assert "\\u" not in formatted
        assert "title:" not in formatted
        assert "agents:" not in formatted
        assert '"name"' not in formatted
        assert "\u5354\u8abf\u54e1" in formatted
        assert "\u62c6\u5206\u4efb\u52d9" in formatted
        assert f"{i18n.t('Title')}:" in formatted
        assert f"{i18n.t('Agents')}:" in formatted
    finally:
        monkeypatch.setattr(config, "APP_LANGUAGE", old_language, raising=False)
        monkeypatch.setattr(config, "ASSISTANT_LANGUAGE", old_assistant_language, raising=False)
        i18n.set_language(app=app)


def test_agent_task_preview_dialog_uses_scrollable_text_view(monkeypatch):
    """Verify long task previews are shown in a scrollable text view."""
    app = _qapp()

    from PySide6.QtWidgets import QDialog, QTextEdit

    from ui.agent.task_window import AgentTaskDialog

    captured = {}

    def fake_exec(dialog):
        captured["dialog"] = dialog
        return 0

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    task_dialog = AgentTaskDialog()
    try:
        text = "\n".join(f"line {idx}" for idx in range(200))
        task_dialog._show_spec_preview(text)

        preview = captured["dialog"]
        viewer = preview.findChild(QTextEdit)

        assert viewer is not None
        assert viewer.isReadOnly()
        assert viewer.toPlainText() == text
        assert viewer.document().blockCount() == 200
        assert preview.minimumWidth() >= 720
        assert preview.minimumHeight() >= 520
    finally:
        if captured.get("dialog") is not None:
            captured["dialog"].close()
            captured["dialog"].deleteLater()
        task_dialog.close()
        task_dialog.deleteLater()
        app.processEvents()


def test_agent_task_combos_ignore_passive_wheel_events():
    """Verify auto-agent combo boxes reuse the settings passive-wheel guard."""
    app = _qapp()

    from PySide6.QtWidgets import QComboBox

    from ui.agent.task_window import (
        AgentCommunicationDialog,
        AgentCommunicationMapWindow,
        AgentNudgeDialog,
        AgentTaskDialog,
    )

    class FakeWheelEvent:
        """Tiny event object for the closed-popup wheel path."""

        def __init__(self) -> None:
            """Initialize fake event."""
            self.ignored = False

        def ignore(self) -> None:
            """Record ignored wheel events."""
            self.ignored = True

    task_dialog = AgentTaskDialog()
    map_window = AgentCommunicationMapWindow(task_dialog)
    exchange_dialog = AgentCommunicationDialog(["Coordinator", "Builder"])
    nudge_dialog = AgentNudgeDialog(["Coordinator"])
    widgets = [task_dialog, map_window, exchange_dialog, nudge_dialog]
    try:
        for widget in widgets:
            combos = widget.findChildren(QComboBox)
            assert combos
            for combo in combos:
                event = FakeWheelEvent()
                combo.wheelEvent(event)
                assert event.ignored is True
    finally:
        for widget in reversed(widgets):
            widget.close()
            widget.deleteLater()
        app.processEvents()


def test_agent_run_folder_actions_use_controlled_reveal_helper(tmp_path, monkeypatch):
    """Both user-facing folder actions route through the guarded OS boundary."""
    app = _qapp()

    from core.agent.task_spec import AgentTaskSpec
    from ui.agent import task_window

    spec = AgentTaskSpec(
        title="Folder actions",
        objective="Open folders safely.",
        scope_folder=str(tmp_path / "scope"),
        sandbox_mode="workspace-write: scope folder only",
        approval_policy="ask before escalation",
        provider="openai",
        model="gpt-5.5",
        reasoning_effort="medium",
        max_runtime_minutes=5,
        max_turns=3,
        allow_shell=False,
        allow_network=False,
        allow_git=False,
        allow_file_create=False,
        allow_file_edit=False,
        allow_file_delete=False,
    )
    window = task_window.AgentRunWindow(spec)
    run_dir = tmp_path / "run"
    window._run_dir = str(run_dir)
    calls: list[str] = []
    monkeypatch.setattr(
        task_window,
        "_reveal_local_folder",
        lambda _parent, _title, path: calls.append(str(path)) or True,
    )
    try:
        window._open_result_folder()
        window._open_scope_folder()

        assert calls == [str(run_dir), spec.scope_folder]
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    ("failure_mode", "expected"),
    [
        ("missing", "no longer exists"),
        ("denied", "access denied"),
        ("unavailable", "file manager unavailable"),
        ("unsupported", "could not reveal"),
    ],
)
def test_agent_folder_reveal_failures_are_controlled(
    tmp_path, monkeypatch, failure_mode, expected
):
    """Missing paths, permission errors, and desktop backend failures stay controlled."""
    _qapp()

    from PySide6.QtWidgets import QWidget

    from ui.agent import task_window

    notices: list[str] = []
    monkeypatch.setattr(
        task_window.QMessageBox,
        "warning",
        lambda _parent, _title, message: notices.append(str(message)),
    )
    if failure_mode == "missing":
        monkeypatch.setattr(task_window.Path, "exists", lambda _path: False)
    elif failure_mode == "denied":
        monkeypatch.setattr(
            task_window.Path,
            "exists",
            lambda _path: (_ for _ in ()).throw(PermissionError("access denied")),
        )
    elif failure_mode == "unavailable":
        monkeypatch.setattr(task_window.Path, "exists", lambda _path: True)
        monkeypatch.setattr(
            task_window.QDesktopServices,
            "openUrl",
            lambda _url: (_ for _ in ()).throw(OSError("file manager unavailable")),
        )
    else:
        monkeypatch.setattr(task_window.Path, "exists", lambda _path: True)
        monkeypatch.setattr(task_window.QDesktopServices, "openUrl", lambda _url: False)

    parent = QWidget()
    try:
        assert task_window._reveal_local_folder(parent, "Open Folder", tmp_path) is False
        assert notices and expected in notices[-1].lower()
    finally:
        parent.close()
        parent.deleteLater()
