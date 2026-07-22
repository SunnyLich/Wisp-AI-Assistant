"""Real user workflows for the multi-agent task setup, run, and history UI."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("PySide6", reason="PySide6 not installed") is None,
    reason="PySide6 not installed",
)


def _qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _button(widget, text: str):
    from PySide6.QtWidgets import QPushButton

    matches = [button for button in widget.findChildren(QPushButton) if button.text() == text]
    assert len(matches) == 1, f"expected one {text!r} button, found {len(matches)}"
    return matches[0]


def _agent_spec(scope: Path):
    from core.agent.task_spec import AgentRoleSpec, AgentTaskSpec

    return AgentTaskSpec(
        title="Workflow task",
        objective="Exercise the live agent controls.",
        scope_folder=str(scope),
        sandbox_mode="workspace-write: scope folder only",
        approval_policy="per-tool permissions",
        provider="openai",
        model="gpt-5.5",
        reasoning_effort="medium",
        max_runtime_minutes=5,
        max_turns=4,
        allow_shell=True,
        allow_network=False,
        allow_git=True,
        allow_file_create=True,
        allow_file_edit=True,
        allow_file_delete=False,
        agents=[
            AgentRoleSpec("Coordinator", "Coordinator", "same as task", "same as task", "Coordinate the run."),
            AgentRoleSpec("Builder", "Implementer", "same as task", "same as task", "Implement the change."),
            AgentRoleSpec("Reviewer", "Reviewer", "same as task", "same as task", "Review the change."),
        ],
    )


def test_agent_tray_setup_copy_preview_and_submit_real_workflow(tmp_path, monkeypatch):
    """Tray actions open real windows and visible setup controls emit the exact task spec."""
    app = _qapp()
    from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

    import config
    from ui.agent import task_window

    runs_root = tmp_path / "runs"
    scope = tmp_path / "scope"
    scope.mkdir()
    monkeypatch.setattr(task_window, "AGENT_RUNS_DIR", runs_root)
    monkeypatch.setattr(
        task_window.AgentTaskDialog,
        "_task_history_root",
        classmethod(lambda cls: runs_root),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: str(scope))
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai", raising=False)
    monkeypatch.setattr(config, "LLM_MODEL", "gpt-5.5", raising=False)
    monkeypatch.setattr(config, "LLM_FALLBACKS", "anthropic:claude-sonnet-4-5", raising=False)
    monkeypatch.setattr(
        task_window,
        "_capture_current_app_context",
        lambda: "Current application:\nTitle: Runtime notes\nRelevant content:\nUse the shared harness.",
    )
    task_window.AgentTaskDialog._last_task_spec = None
    task_window._agent_task_dialogs.clear()
    task_window._agent_history_windows.clear()
    owner = QWidget()
    widgets = [owner]
    submitted = []
    previews: list[str] = []
    try:
        task_action = task_window.make_agent_task_action(owner, on_submit=submitted.append)
        history_action = task_window.make_agent_history_action(owner)

        task_action.trigger()
        history_action.trigger()
        app.processEvents()
        app.processEvents()

        assert len(task_window._agent_task_dialogs) == 1
        assert len(task_window._agent_history_windows) == 1
        tray_dialog = task_window._agent_task_dialogs[-1]
        history_window = task_window._agent_history_windows[-1]
        widgets.extend([tray_dialog, history_window])
        assert tray_dialog.isVisible()
        assert history_window.isVisible()
        _button(tray_dialog, "Cancel").click()

        dialog = task_window.AgentTaskDialog(on_submit=submitted.append)
        widgets.append(dialog)
        monkeypatch.setattr(dialog, "_show_spec_preview", previews.append)
        dialog.title_edit.setText("Implement real workflow")
        dialog.objective_edit.setPlainText("Run the configured agents and verify the result.")
        dialog.required_context_edit.setPlainText("Use the existing runtime harness and preserve user files.")
        _button(dialog, "Copy current app context").click()
        _button(dialog, "Browse...").click()
        _button(dialog, "Advanced Settings").click()
        dialog.allowed_globs_edit.setText("*.py, tests/*.py")
        dialog.blocked_globs_edit.setText(".env, private/*")
        dialog.completion_edit.setPlainText("The focused workflow tests pass and artifacts are written.")
        dialog.parallel_briefing.setChecked(True)
        dialog.parallel_execution.setChecked(True)
        dialog.max_parallel_agents.setValue(2)

        # Exercise the visible app-model copy and fallback-row add/remove route.
        _button(dialog, "Copy from app").click()
        assert dialog.provider_combo.currentData() == "openai"
        assert dialog.model_edit.currentText() == "gpt-5.5"
        assert dialog._collect_fallbacks() == "anthropic:claude-sonnet-4-5"
        _button(dialog, "+ Add fallback model").click()
        added = dialog._fallback_rows[-1]
        added["provider"].setCurrentText("google")
        added["model"].setCurrentText("gemini-2.5-pro")

        _button(dialog, "Preview Spec").click()
        assert previews and "Implement real workflow" in previews[-1]
        assert "The focused workflow tests pass" in previews[-1]
        _button(dialog, "Start Task").click()

        assert len(submitted) == 1
        spec = submitted[0]
        assert spec.title == "Implement real workflow"
        assert spec.objective == "Run the configured agents and verify the result."
        assert spec.required_context == (
            "Use the existing runtime harness and preserve user files.\n\n"
            "Current application:\nTitle: Runtime notes\nRelevant content:\nUse the shared harness."
        )
        assert spec.scope_folder == str(scope.resolve())
        assert spec.allowed_file_globs == ["*.py", "tests/*.py"]
        assert spec.blocked_file_globs == [".env", "private/*"]
        assert spec.parallel_read_only_briefing is True
        assert spec.parallel_execution is True
        assert spec.max_parallel_agents == 2
        assert spec.model_fallbacks.splitlines() == [
            "anthropic:claude-sonnet-4-5",
            "google:gemini-2.5-pro",
        ]
        assert [agent.name for agent in spec.agents] == ["Coordinator", "Builder", "Reviewer"]
        from core.agent.runner import AgentTaskRunner

        prompt = AgentTaskRunner()._build_agent_prompt(spec, [])
        assert "Use the existing runtime harness" in prompt
        assert "Title: Runtime notes" in prompt
        assert "Use the shared harness" in prompt

        copied = task_window.AgentTaskDialog(on_submit=submitted.append)
        widgets.append(copied)
        _button(copied, "Copy from Last Task").click()
        assert copied.title_edit.text() == spec.title
        assert copied.objective_edit.toPlainText() == spec.objective
        assert copied.required_context_edit.toPlainText() == spec.required_context
        assert copied._collect_fallbacks() == spec.model_fallbacks
        assert copied.parallel_execution.isChecked()
    finally:
        task_window.AgentTaskDialog._last_task_spec = None
        for widget in reversed(widgets):
            widget.close()
            widget.deleteLater()
        app.processEvents()
        task_window._agent_task_dialogs.clear()
        task_window._agent_history_windows.clear()


def test_agent_current_app_context_uses_external_window_reader_and_budget(monkeypatch):
    """Current-app copy excludes Wisp, reuses document extraction/redaction, and stays bounded."""
    from core import context_fetcher
    from core.context_fetcher import WindowInfo
    from core.llm_clients import client
    from ui.agent import task_window

    calls = []
    monkeypatch.setattr(
        context_fetcher,
        "get_active_window_info",
        lambda *, exclude_pids: calls.append(exclude_pids) or WindowInfo(
            title="Design notes - Editor",
            process_name="editor.exe",
            pid=9001,
            url="https://example.test/design",
            hwnd=77,
        ),
    )
    monkeypatch.setattr(
        client,
        "read_active_document_for_context_with_debug",
        lambda *, active_window: ("token=abcdefghijklmnopqrstuvwxyz123456 " + "x" * 20_000, {"window": active_window}),
    )
    monkeypatch.setattr(
        context_fetcher,
        "fetch_browser_content_for_window",
        lambda **_kwargs: pytest.fail("document text should win over browser fallback"),
    )

    captured = task_window._capture_current_app_context()

    assert calls and os.getpid() in calls[0]
    assert "Title: Design notes - Editor" in captured
    assert "Application: editor.exe" in captured
    assert "URL: [URL]" in captured
    assert "Relevant content:" in captured
    assert "abcdefghijklmnopqrstuvwxyz123456" not in captured
    assert "[API_KEY]" in captured
    assert captured.endswith("[Current application context truncated at 12,000 characters]")
    assert len(captured) == task_window._AGENT_APP_CONTEXT_MAX_CHARS


@pytest.mark.parametrize(
    ("manual", "app_context", "copy_app", "expected", "absent"),
    [
        ("Manual constraint.", "Unused app context.", False, ("Manual constraint.",), ("Unused app context.",)),
        ("", "Current application:\nApp-only notes.", True, ("App-only notes.",), ("Manual constraint.",)),
        (
            "Manual constraint.",
            "Current application:\nCombined notes.",
            True,
            ("Manual constraint.", "Combined notes."),
            (),
        ),
    ],
)
def test_agent_manual_by_current_app_context_prompt_matrix(
    tmp_path,
    monkeypatch,
    manual,
    app_context,
    copy_app,
    expected,
    absent,
):
    """Manual-only, app-only, and combined context states reach the runner distinctly."""
    app = _qapp()
    from core.agent.runner import AgentTaskRunner
    from ui.agent import task_window

    monkeypatch.setattr(task_window, "_capture_current_app_context", lambda: app_context)
    dialog = task_window.AgentTaskDialog(on_submit=lambda _spec: None)
    try:
        dialog.title_edit.setText("Context matrix")
        dialog.objective_edit.setPlainText("Prove task context composition.")
        dialog.scope_edit.setText(str(tmp_path))
        dialog.required_context_edit.setPlainText(manual)
        if copy_app:
            _button(dialog, "Copy current app context").click()
        spec = dialog._collect_spec()
        prompt = AgentTaskRunner()._build_agent_prompt(spec, [])

        for value in expected:
            assert value in spec.required_context
            assert value in prompt
        for value in absent:
            assert value not in spec.required_context
            assert value not in prompt
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_agent_communication_map_visible_editing_workflow(monkeypatch):
    """The separate communication window edits agents, exchanges, pairs, and defaults."""
    app = _qapp()
    from PySide6.QtWidgets import QMessageBox

    from ui.agent import task_window

    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda _self: QMessageBox.StandardButton.Yes,
    )
    task_dialog = task_window.AgentTaskDialog(on_submit=lambda _spec: None)
    widgets = [task_dialog]
    try:
        _button(task_dialog, "Open Agents Communication Window").click()
        app.processEvents()
        window = task_dialog._communication_window
        assert window is not None and window.isVisible()
        widgets.append(window)
        assert window.window_agent_list.count() == 3

        _button(window, "Add Agent").click()
        assert window.window_agent_list.count() == 4
        window.window_agent_list.setCurrentRow(3)
        window.map_agent_name.setText("Test Model")
        window.map_agent_role.setCurrentText("Researcher")
        window.map_agent_provider.setCurrentText("anthropic")
        window.map_agent_model.setText("claude-sonnet-4-5")
        window.map_agent_responsibility.setPlainText("Inspect the runtime without changing files.")
        assert task_dialog._agent_specs[-1] == {
            "name": "Test Model",
            "role": "Researcher",
            "provider": "anthropic",
            "model": "claude-sonnet-4-5",
            "responsibility": "Inspect the runtime without changing files.",
        }

        before_communications = len(task_dialog._communication_specs)
        _button(window, "Add Communication").click()
        assert len(task_dialog._communication_specs) == before_communications + 1
        window.map_comm_from.setCurrentText("Test Model")
        window.map_comm_to.setCurrentText("Reviewer")
        window.map_comm_phase.setCurrentText("Review")
        window.map_comm_trigger.setText("After implementation")
        window.map_comm_message.setPlainText("Review every changed file and report blockers.")
        assert task_dialog._communication_specs[-1] == {
            "from_agent": "Test Model",
            "to_agent": "Reviewer",
            "phase": "Review",
            "trigger": "After implementation",
            "message": "Review every changed file and report blockers.",
        }

        _button(window, "Create Pair Exchanges").click()
        routes = {
            (item["from_agent"], item["to_agent"])
            for item in task_dialog._communication_specs
        }
        names = task_dialog._agent_names()
        assert all((source, target) in routes for source in names for target in names if source != target)

        count_before_remove = len(task_dialog._communication_specs)
        window.exchange_list.setCurrentRow(window.exchange_list.count() - 1)
        _button(window, "Remove Communication").click()
        assert len(task_dialog._communication_specs) == count_before_remove - 1
        _button(window, "Refresh").click()
        assert window.window_agent_list.count() == len(task_dialog._agent_specs)

        window.window_agent_list.setCurrentRow(window.window_agent_list.count() - 1)
        _button(window, "Remove Agent").click()
        assert "Test Model" not in task_dialog._agent_names()
        assert all(
            "Test Model" not in (item["from_agent"], item["to_agent"])
            for item in task_dialog._communication_specs
        )

        _button(window, "Reset to Default").click()
        assert task_dialog._agent_names() == ["Coordinator", "Builder", "Reviewer"]
        assert task_dialog._communication_specs == task_dialog._default_communication_specs()
    finally:
        for widget in reversed(widgets):
            widget.close()
            widget.deleteLater()
        app.processEvents()


def test_agent_run_visible_tabs_approval_controls_and_artifacts_workflow(tmp_path, monkeypatch):
    """Visible live-run controls drive the real control object and artifact viewers."""
    app = _qapp()
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QDialog

    from core.agent.runtime import AgentRunControl
    from ui.agent import task_window

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "task.json").write_text(json.dumps(asdict(_agent_spec(tmp_path))), encoding="utf-8")
    (run_dir / "run.log").write_text("run artifact", encoding="utf-8")
    (run_dir / "verbose.log").write_text("trace artifact", encoding="utf-8")
    (run_dir / "final.md").write_text("final artifact", encoding="utf-8")
    (run_dir / "diff.patch").write_text("+workflow change", encoding="utf-8")
    launched = []
    revealed = []
    monkeypatch.setattr(task_window, "launch_agent_run_window", lambda spec, **_kwargs: launched.append(spec))
    monkeypatch.setattr(
        task_window,
        "_reveal_local_folder",
        lambda _parent, _title, path: revealed.append(str(path)) or True,
    )

    def accept_nudge(dialog):
        dialog.nudge = {"to": "Builder", "message": "Please run the focused tests."}
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(task_window.AgentNudgeDialog, "exec", accept_nudge)
    spec = _agent_spec(tmp_path)
    window = task_window.AgentRunWindow(spec)
    control = AgentRunControl()
    window._control = control
    try:
        window._append_log("[12:00:00] agent turn 1/4: Builder")
        window._append_log("[12:00:01] Builder thought: I need to patch the workflow test.")
        window._append_log("[12:00:02] model response received in 1.5s via openai")
        window._append_log("[12:00:03] message: Builder -> Reviewer: Please review the patch")
        window._append_trace("live trace entry")

        labels = [window.tabs.tabText(index) for index in range(window.tabs.count())]
        assert labels == ["Meeting", "Live Log", "Model Trace", "Final Report"]
        window.tabs.setCurrentWidget(window.trace_view)
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents)
        assert "live trace entry" in window.trace_view.toPlainText()
        window._select_live_agent(1)
        assert "Builder" in window.agent_summary_view.toPlainText()
        assert "calls 1, average latency 1.5s" in window.agent_summary_view.toPlainText()
        assert "Builder -> Reviewer" in window.shared_board_view.toPlainText()
        assert "Please review the patch" in window.shared_board_view.toPlainText()

        # This is the exact callback used by a completed card drag/resize; reset is a real click.
        window._on_agent_geometry_change(1, 123.0, 77.0, 1.4)
        app.processEvents()
        assert window._agent_layout["Builder"]["scale"] == 1.4
        window.reset_layout_btn.click()
        assert window._agent_layout == {}

        for approved, button_text in ((True, "Approve"), (False, "Decline")):
            event = threading.Event()
            state = {"event": event, "approved": not approved}
            window._show_approval(
                {"action": "edit_file", "details": {"path": "tests/test_workflow.py"}},
                state,
            )
            assert window.approval_panel.isHidden() is False
            assert "tests/test_workflow.py" in window.approval_label.text()
            _button(window.approval_panel, button_text).click()
            assert event.is_set() and state["approved"] is approved

        window.pause_btn.click()
        assert control.is_pause_requested()
        assert window.pause_btn.text() == "Resume"
        window.pause_btn.click()
        assert not control.is_pause_requested()
        window.nudge_btn.click()
        assert control.drain_nudges() == [{
            "from": "User",
            "to": "Builder",
            "message": "Please run the focused tests.",
            "source": "manual_nudge",
        }]
        assert "User -> Builder" in window.shared_board_view.toPlainText()

        window._on_finished(str(run_dir))
        assert window.final_view.toPlainText() == "final artifact"
        assert window.trace_view.toPlainText() == "trace artifact"
        window.diff_btn.click()
        assert task_window._diff_windows
        diff_window = task_window._diff_windows[-1]
        assert "+workflow change" in diff_window.findChild(task_window.QTextEdit).toPlainText()
        window.open_result_btn.click()
        window.open_scope_btn.click()
        assert revealed == [str(run_dir), str(tmp_path)]
        window.retry_btn.click()
        window.continue_btn.click()
        assert launched[0].title == spec.title
        assert launched[1].title == f"Continue: {spec.title}"
        assert "final artifact" in launched[1].required_context

        cancel_window = task_window.AgentRunWindow(spec)
        cancel_window._control = AgentRunControl()
        try:
            cancel_window.cancel_btn.click()
            assert cancel_window._control.is_cancelled()
            assert cancel_window.status_lbl.text() == "Cancelling..."
            assert not cancel_window.cancel_btn.isEnabled()
        finally:
            cancel_window.close()
            cancel_window.deleteLater()
    finally:
        for diff_window in list(task_window._diff_windows):
            diff_window.close()
            diff_window.deleteLater()
        task_window._diff_windows.clear()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_agent_history_visible_refresh_tabs_folder_retry_and_continue_workflow(tmp_path, monkeypatch):
    """History refresh loads every artifact and its visible actions reopen usable specs."""
    app = _qapp()
    from PySide6.QtGui import QDesktopServices

    from ui.agent import task_window

    runs_root = tmp_path / "runs"
    run_dir = runs_root / "2026-07-21_120000-workflow"
    run_dir.mkdir(parents=True)
    spec = _agent_spec(tmp_path)
    task_payload = {
        "title": spec.title,
        "objective": spec.objective,
        "scope_folder": spec.scope_folder,
        "provider": spec.provider,
        "model": spec.model,
        "required_context": "original context",
    }
    (run_dir / "task.json").write_text(json.dumps(task_payload), encoding="utf-8")
    (run_dir / "final.md").write_text("historical final", encoding="utf-8")
    (run_dir / "run.log").write_text(
        "[12:00:00] historical run log\n[12:00:01] Builder tool call: patch_file",
        encoding="utf-8",
    )
    (run_dir / "verbose.log").write_text("historical trace", encoding="utf-8")
    (run_dir / "diff.patch").write_text("historical diff", encoding="utf-8")
    monkeypatch.setattr(task_window, "AGENT_RUNS_DIR", runs_root)
    opened = []
    launched = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    monkeypatch.setattr(task_window, "launch_agent_run_window", lambda spec, **_kwargs: launched.append(spec))

    window = task_window.AgentRunHistoryWindow()
    try:
        assert window.run_list.count() == 1
        assert "Workflow task" in window.run_list.currentItem().text()
        assert "historical final" in window.summary_view.toPlainText()
        assert "historical run log" in window.log_view.toPlainText()
        assert window.trace_view.toPlainText() == "historical trace"
        assert window.diff_view.toPlainText() == "historical diff"
        assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == [
            "Summary",
            "Run Log",
            "Model Trace",
            "Diff",
        ]

        second = runs_root / "2026-07-21_130000-newer"
        second.mkdir()
        (second / "task.json").write_text(json.dumps({**task_payload, "title": "Newer task"}), encoding="utf-8")
        (second / "final.md").write_text("newer final", encoding="utf-8")
        _button(window, "Refresh").click()
        assert window.run_list.count() == 2
        assert "Newer task" in window.run_list.item(0).text()

        window.run_list.setCurrentRow(1)
        _button(window, "Open Memory Folder").click()
        _button(window, "Retry").click()
        _button(window, "Continue").click()
        assert [Path(path) for path in opened] == [run_dir]
        assert launched[0].title == "Workflow task"
        assert launched[1].title == "Continue: Workflow task"
        assert "historical final" in launched[1].required_context
        assert "Builder tool call: patch_file" in launched[1].required_context
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
