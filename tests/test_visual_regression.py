"""Screenshot regression coverage for Wisp's critical Qt surfaces."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows reference renders")

_BASELINES = Path(__file__).with_name("visual_baselines") / "windows"
_UPDATE = os.environ.get("WISP_UPDATE_VISUAL_BASELINES") == "1"
_SURFACES = ("onboarding", "settings", "chat", "agent-task", "speech-bubble")
_CAPTURE_FLAG = "--wisp-visual-capture"


def _process_events_bounded(app, milliseconds: int = 25) -> None:
    """Drain Qt work without waiting forever on a continuously active timer."""
    from PySide6.QtCore import QEventLoop

    app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, milliseconds)


def _settle(app, widget) -> None:
    widget.show()
    for _ in range(4):
        _process_events_bounded(app)
    widget.repaint()
    _process_events_bounded(app)


def _capture(widget):
    from PySide6.QtGui import QImage

    return widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)


def _dispose_capture_widget(app, widget) -> None:
    """Delete a captured widget without invoking dialog accept/reject actions."""
    from PySide6.QtCore import QCoreApplication, QEvent

    # QDialog.close() maps to reject(). Onboarding's real reject path restores
    # the app language and theme globally, which is correct for users but wrong
    # for a screenshot fixture and can rebroadcast style events to every widget
    # created earlier in the suite.
    widget.hide()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
    _process_events_bounded(app)


def _compare_images(actual, expected) -> tuple[float, float]:
    """Return mean RGB error and fraction of materially changed pixels."""
    from PySide6.QtCore import Qt

    sample_w, sample_h = 240, 160
    actual = actual.scaled(
        sample_w,
        sample_h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    expected = expected.scaled(
        sample_w,
        sample_h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    total_error = 0
    changed = 0
    for y in range(sample_h):
        for x in range(sample_w):
            left = actual.pixelColor(x, y)
            right = expected.pixelColor(x, y)
            deltas = (
                abs(left.red() - right.red()),
                abs(left.green() - right.green()),
                abs(left.blue() - right.blue()),
            )
            total_error += sum(deltas)
            if max(deltas) > 24:
                changed += 1
    pixels = sample_w * sample_h
    return total_error / (pixels * 3), changed / pixels


def _make_surface(name: str):
    import config

    config.APP_LANGUAGE = "en"
    config.THEME_MODE = "light"
    config.DARK_MODE = False

    if name == "onboarding":
        from ui.onboarding import OnboardingWizard

        widget = OnboardingWizard()
        widget.setFixedSize(620, 460)
        return widget

    if name == "settings":
        from ui.settings_panel import dialog as settings_dialog

        settings_dialog._read_env = lambda: {}
        settings_dialog.SettingsDialog._schedule_open_status_refresh = lambda _self: None
        widget = settings_dialog.SettingsDialog()
        widget._settings_nav.setCurrentRow(7)
        widget.setFixedSize(980, 760)
        return widget

    if name == "chat":
        from ui.chat_window import ChatWindow

        conversations = [
            {
                "title": "Launch checklist",
                "context": "",
                "messages": [
                    {"role": "user", "content": "Can you review the launch checklist?"},
                    {
                        "role": "assistant",
                        "content": "Yes — secrets, permissions, recovery, and visual checks are all covered.",
                    },
                ],
            }
        ]
        widget = ChatWindow(
            conversations,
            lambda _messages: iter(()),
            projects=[{"id": "general", "name": "General"}, {"id": "launch", "name": "Launch"}],
            active_project_id="launch",
        )
        widget.setFixedSize(1000, 700)
        return widget

    if name == "agent-task":
        from ui.agent.task_window import AgentTaskDialog

        widget = AgentTaskDialog()
        widget.title_edit.setText("Audit release readiness")
        widget.objective_edit.setPlainText("Review the desktop app and report actionable launch risks.")
        widget.scope_edit.setText(r"C:\demo\wisp-project")
        widget.setFixedSize(1280, 760)
        return widget

    if name == "speech-bubble":
        config.BUBBLE_WIDTH = 360
        config.BUBBLE_LINES = 4
        config.BUBBLE_FONT_SIZE = 10
        config.BUBBLE_COLOR = "#1c1c24dc"
        config.BUBBLE_TEXT_COLOR = "#e6e6e6"
        from ui.bubble import SpeechBubble

        widget = SpeechBubble()
        widget.show_notice(
            "Crash report ready. Review the redacted ZIP before sharing it.",
            timeout_ms=0,
            severity="success",
        )
        return widget

    raise AssertionError(f"Unknown visual surface: {name}")


def _render_surface_to_path(surface: str, output_path: Path) -> None:
    """Render one surface in a fresh QApplication process."""
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["wisp-visual-capture"])
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setPalette(app.style().standardPalette())
    widget = _make_surface(surface)
    try:
        _settle(app, widget)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        assert _capture(widget).save(str(output_path), "PNG")
    finally:
        _dispose_capture_widget(app, widget)


def _capture_in_subprocess(surface: str, output_path: Path) -> None:
    """Capture outside pytest's shared QApplication and fail with child diagnostics."""
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "QT_SCALE_FACTOR": "1",
            "QT_FONT_DPI": "96",
            "PYTHONPATH": os.pathsep.join(filter(None, (str(repo_root), env.get("PYTHONPATH", "")))),
        }
    )
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), _CAPTURE_FLAG, surface, str(output_path)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert output_path.is_file(), f"Visual capture did not create {output_path}"


@pytest.mark.parametrize("surface", _SURFACES)
def test_critical_surface_matches_baseline(surface, tmp_path):
    from PySide6.QtGui import QImage

    actual_path = tmp_path / f"{surface}-actual.png"
    _capture_in_subprocess(surface, actual_path)
    actual = QImage(str(actual_path)).convertToFormat(QImage.Format.Format_RGBA8888)
    assert not actual.isNull(), f"Could not read {actual_path}"
    baseline_path = _BASELINES / f"{surface}.png"
    if _UPDATE:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(actual_path, baseline_path)
        return

    assert baseline_path.exists(), (
        f"Missing visual baseline {baseline_path}. Set WISP_UPDATE_VISUAL_BASELINES=1 on Windows to create it."
    )
    expected = QImage(str(baseline_path)).convertToFormat(QImage.Format.Format_RGBA8888)
    assert not expected.isNull(), f"Could not read {baseline_path}"
    assert actual.size() == expected.size(), (
        f"{surface} size changed from {expected.width()}x{expected.height()} "
        f"to {actual.width()}x{actual.height()}"
    )
    mean_error, changed_fraction = _compare_images(actual, expected)
    if mean_error > 3.0 or changed_fraction > 0.08:
        pytest.fail(
            f"{surface} differs from its baseline: mean RGB error={mean_error:.2f}, "
            f"changed pixels={changed_fraction:.1%}; actual saved to {actual_path}"
        )


def test_visual_event_drain_is_bounded_with_busy_timer():
    """A zero-interval timer left by another Qt test must not stall this module."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["wisp-visual-event-budget"])
    ticks: list[bool] = []
    timer = QTimer()
    timer.setInterval(0)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    started = time.monotonic()
    try:
        _process_events_bounded(app, 25)
    finally:
        timer.stop()

    assert ticks
    assert time.monotonic() - started < 0.5


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == _CAPTURE_FLAG:
    _render_surface_to_path(sys.argv[2], Path(sys.argv[3]))
