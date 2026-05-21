"""
ui/agent_task_mockup.py - Mockup for starting a scoped agent task.

This module is intentionally self-contained.  It defines the tray-menu action
and a dialog for collecting everything an autonomous agent runner would need,
without wiring it into the current app runtime yet.

Future tray integration in ui/overlay.py would look like:

    from ui.agent_task_mockup import make_agent_task_action
    menu.addAction(make_agent_task_action(self, parent=self))

The important design point is that ``scope_folder`` is validated and resolved
before a task spec is emitted.  A real runner should use this resolved path as
its filesystem sandbox root, not merely include it in the prompt.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from ui.window_utils import fit_window_to_screen


TaskSubmitCallback = Callable[["AgentTaskSpec"], None]
_agent_run_windows: list["AgentRunWindow"] = []


@dataclass(frozen=True)
class AgentTaskSpec:
    """Serializable mock contract between the tray GUI and a future runner."""

    title: str
    objective: str
    scope_folder: str
    sandbox_mode: str
    approval_policy: str
    model: str
    reasoning_effort: str
    max_runtime_minutes: int
    max_turns: int
    allow_shell: bool
    allow_network: bool
    allow_git: bool
    allow_file_create: bool
    allow_file_edit: bool
    allow_file_delete: bool
    allowed_file_globs: list[str] = field(default_factory=list)
    blocked_file_globs: list[str] = field(default_factory=list)
    required_context: str = ""
    completion_criteria: str = ""
    report_format: str = "Summary + changed files + verification"


def resolve_scope_folder(raw_folder: str) -> Path:
    """
    Resolve and validate the folder that a future agent may manipulate.

    This is the hard boundary candidate for the runner.  Any file operation in
    the eventual implementation should be checked with ``is_inside_scope``.
    """
    folder = Path(raw_folder).expanduser().resolve()
    if not folder.exists():
        raise ValueError("Scope folder does not exist.")
    if not folder.is_dir():
        raise ValueError("Scope must be a folder, not a file.")
    return folder


def is_inside_scope(path: str | Path, scope_folder: str | Path) -> bool:
    """Return True only when ``path`` resolves inside ``scope_folder``."""
    scope = Path(scope_folder).expanduser().resolve()
    candidate = Path(path).expanduser().resolve()
    return candidate == scope or scope in candidate.parents


def make_agent_task_action(
    owner: QWidget,
    parent: QWidget | None = None,
    on_submit: TaskSubmitCallback | None = None,
) -> QAction:
    """
    Create the tray QAction for "Start agent task...".

    ``owner`` should normally be the overlay object so the QAction lifetime is
    tied to the app.  ``on_submit`` is where a future runner would be invoked.
    """
    action = QAction("Start agent task...", owner)
    action.triggered.connect(lambda: open_agent_task_dialog(parent or owner, on_submit))
    return action


def open_agent_task_dialog(
    parent: QWidget | None = None,
    on_submit: TaskSubmitCallback | None = None,
) -> AgentTaskSpec | None:
    """
    Show the mock dialog and return the accepted task spec.

    If ``on_submit`` is supplied, it is called after validation.  Without a real
    agent runner, the dialog shows a confirmation containing the resolved spec.
    """
    dialog = AgentTaskDialog(parent=parent, on_submit=on_submit)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.task_spec
    return None


class AgentTaskDialog(QDialog):
    """Mock GUI for collecting a complete, sandboxed agent task request."""

    def __init__(
        self,
        parent: QWidget | None = None,
        on_submit: TaskSubmitCallback | None = None,
    ):
        super().__init__(parent)
        self._on_submit = on_submit
        self.task_spec: AgentTaskSpec | None = None

        self.setWindowTitle("Start Agent Task")
        self.setMinimumSize(560, 420)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        self._build_ui()
        self._load_defaults()
        self._fit_to_screen()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        intro = QLabel(
            "Create a scoped autonomous task. The selected folder is the future "
            "filesystem boundary for reads and writes."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #777;")
        root.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        content_layout.addWidget(self._task_group())
        content_layout.addWidget(self._scope_group())
        content_layout.addWidget(self._permissions_group())
        content_layout.addWidget(self._runtime_group())
        content_layout.addWidget(self._output_group())
        content_layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)
        root.addWidget(self._buttons())

    def _task_group(self) -> QGroupBox:
        box = QGroupBox("Task")
        form = QFormLayout(box)
        form.setSpacing(10)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Example: Add tray launch action mockup")

        self.objective_edit = QTextEdit()
        self.objective_edit.setMinimumHeight(120)
        self.objective_edit.setPlaceholderText(
            "Describe the task, expected behavior, constraints, and what the "
            "agent should produce."
        )

        self.required_context_edit = QTextEdit()
        self.required_context_edit.setMinimumHeight(76)
        self.required_context_edit.setPlaceholderText(
            "Relevant files, APIs, user preferences, credentials policy, or "
            "anything the agent should know before it starts."
        )

        form.addRow("Title", self.title_edit)
        form.addRow("Objective", self.objective_edit)
        form.addRow("Context", self.required_context_edit)
        return box

    def _scope_group(self) -> QGroupBox:
        box = QGroupBox("Filesystem Scope")
        layout = QVBoxLayout(box)
        layout.setSpacing(10)

        row = QHBoxLayout()
        self.scope_edit = QLineEdit()
        self.scope_edit.setPlaceholderText("Folder the agent is allowed to manipulate")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._choose_scope)
        row.addWidget(self.scope_edit, stretch=1)
        row.addWidget(browse)
        layout.addLayout(row)

        form = QFormLayout()
        self.sandbox_combo = QComboBox()
        self.sandbox_combo.addItems(
            [
                "workspace-write: scope folder only",
                "read-only: inspect only",
                "approval-required: ask before every write",
            ]
        )

        self.allowed_globs_edit = QLineEdit()
        self.allowed_globs_edit.setPlaceholderText("Optional, comma-separated: *.py, ui/*.py")
        self.blocked_globs_edit = QLineEdit()
        self.blocked_globs_edit.setPlaceholderText("Optional, comma-separated: .env, private/*")

        form.addRow("Sandbox", self.sandbox_combo)
        form.addRow("Allow globs", self.allowed_globs_edit)
        form.addRow("Block globs", self.blocked_globs_edit)
        layout.addLayout(form)

        note = QLabel(
            "Runner contract: resolve the scope folder first, then reject any "
            "file operation whose resolved path is outside that folder."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #777; font-size: 9pt;")
        layout.addWidget(note)
        return box

    def _permissions_group(self) -> QGroupBox:
        box = QGroupBox("Capabilities")
        layout = QVBoxLayout(box)

        row_one = QHBoxLayout()
        self.allow_shell = QCheckBox("Shell")
        self.allow_network = QCheckBox("Network")
        self.allow_git = QCheckBox("Git")
        row_one.addWidget(self.allow_shell)
        row_one.addWidget(self.allow_network)
        row_one.addWidget(self.allow_git)
        row_one.addStretch()

        row_two = QHBoxLayout()
        self.allow_create = QCheckBox("Create files")
        self.allow_edit = QCheckBox("Edit files")
        self.allow_delete = QCheckBox("Delete files")
        row_two.addWidget(self.allow_create)
        row_two.addWidget(self.allow_edit)
        row_two.addWidget(self.allow_delete)
        row_two.addStretch()

        layout.addLayout(row_one)
        layout.addLayout(row_two)
        return box

    def _runtime_group(self) -> QGroupBox:
        box = QGroupBox("Runtime")
        form = QFormLayout(box)
        form.setSpacing(10)

        self.model_combo = QComboBox()
        self.model_combo.addItems(["gpt-5.3-codex", "gpt-5.4", "gpt-5.4-mini"])

        self.reasoning_combo = QComboBox()
        self.reasoning_combo.addItems(["medium", "high", "low", "xhigh"])

        self.approval_combo = QComboBox()
        self.approval_combo.addItems(
            [
                "ask before escalation",
                "never escalate",
                "auto-approve safe reads",
            ]
        )

        self.runtime_minutes = QSpinBox()
        self.runtime_minutes.setRange(1, 480)
        self.runtime_minutes.setSuffix(" min")

        self.max_turns = QSpinBox()
        self.max_turns.setRange(1, 200)

        form.addRow("Model", self.model_combo)
        form.addRow("Reasoning", self.reasoning_combo)
        form.addRow("Approvals", self.approval_combo)
        form.addRow("Time limit", self.runtime_minutes)
        form.addRow("Turn limit", self.max_turns)
        return box

    def _output_group(self) -> QGroupBox:
        box = QGroupBox("Completion")
        form = QFormLayout(box)

        self.completion_edit = QTextEdit()
        self.completion_edit.setMinimumHeight(76)
        self.completion_edit.setPlaceholderText(
            "How the agent knows it is done: tests pass, files changed, PR opened, "
            "summary produced, etc."
        )

        self.report_combo = QComboBox()
        self.report_combo.addItems(
            [
                "Summary + changed files + verification",
                "Patch only",
                "Detailed implementation report",
                "Ask before final changes",
            ]
        )

        form.addRow("Done when", self.completion_edit)
        form.addRow("Report", self.report_combo)
        return box

    def _buttons(self) -> QWidget:
        frame = QFrame()
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)

        self.preview_btn = QPushButton("Preview Spec")
        self.preview_btn.clicked.connect(self._preview_spec)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        start = QPushButton("Start Task")
        start.setDefault(True)
        start.clicked.connect(self._accept)

        row.addWidget(self.preview_btn)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(start)
        return frame

    def _load_defaults(self) -> None:
        self.scope_edit.setText(str(Path.cwd()))
        self.allow_shell.setChecked(True)
        self.allow_network.setChecked(False)
        self.allow_git.setChecked(True)
        self.allow_create.setChecked(True)
        self.allow_edit.setChecked(True)
        self.allow_delete.setChecked(False)
        self.runtime_minutes.setValue(60)
        self.max_turns.setValue(30)
        self.blocked_globs_edit.setText(".env, private/*, .git/*")

    def _fit_to_screen(self) -> None:
        screen = QApplication.primaryScreen()
        available_h = screen.availableGeometry().height() if screen is not None else 680
        fit_window_to_screen(
            self,
            preferred_width=680,
            preferred_height=min(640, max(460, available_h - 80)),
        )

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._fit_to_screen()

    # ------------------------------------------------------------------ Actions

    def _choose_scope(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Agent Scope Folder",
            self.scope_edit.text() or str(Path.cwd()),
        )
        if folder:
            self.scope_edit.setText(folder)

    def _preview_spec(self) -> None:
        try:
            spec = self._collect_spec()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Agent Task", str(exc))
            return
        QMessageBox.information(self, "Agent Task Spec", self._format_spec(spec))

    def _accept(self) -> None:
        try:
            spec = self._collect_spec()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Agent Task", str(exc))
            return

        self.task_spec = spec
        if self._on_submit is not None:
            self._on_submit(spec)
        else:
            window = AgentRunWindow(spec, parent=self.parentWidget())
            _agent_run_windows.append(window)
            window.destroyed.connect(lambda _obj=None, w=window: _agent_run_windows.remove(w) if w in _agent_run_windows else None)
            window.show()
        self.accept()

    # ------------------------------------------------------------------ Spec

    def _collect_spec(self) -> AgentTaskSpec:
        title = self.title_edit.text().strip()
        objective = self.objective_edit.toPlainText().strip()
        if not title:
            raise ValueError("Add a task title.")
        if not objective:
            raise ValueError("Describe the task objective.")

        scope = resolve_scope_folder(self.scope_edit.text().strip())
        return AgentTaskSpec(
            title=title,
            objective=objective,
            scope_folder=str(scope),
            sandbox_mode=self.sandbox_combo.currentText(),
            approval_policy=self.approval_combo.currentText(),
            model=self.model_combo.currentText(),
            reasoning_effort=self.reasoning_combo.currentText(),
            max_runtime_minutes=self.runtime_minutes.value(),
            max_turns=self.max_turns.value(),
            allow_shell=self.allow_shell.isChecked(),
            allow_network=self.allow_network.isChecked(),
            allow_git=self.allow_git.isChecked(),
            allow_file_create=self.allow_create.isChecked(),
            allow_file_edit=self.allow_edit.isChecked(),
            allow_file_delete=self.allow_delete.isChecked(),
            allowed_file_globs=self._split_globs(self.allowed_globs_edit.text()),
            blocked_file_globs=self._split_globs(self.blocked_globs_edit.text()),
            required_context=self.required_context_edit.toPlainText().strip(),
            completion_criteria=self.completion_edit.toPlainText().strip(),
            report_format=self.report_combo.currentText(),
        )

    @staticmethod
    def _split_globs(raw: str) -> list[str]:
        return [part.strip() for part in raw.split(",") if part.strip()]

    @staticmethod
    def _format_spec(spec: AgentTaskSpec) -> str:
        lines: list[str] = []
        for key, value in asdict(spec).items():
            if isinstance(value, list):
                value = ", ".join(value) if value else "(none)"
            lines.append(f"{key}: {value}")
        return "\n".join(lines)


class AgentRunWindow(QDialog):
    """Small live log window for a background agent run."""

    log_line = pyqtSignal(str)
    finished = pyqtSignal(str)

    def __init__(self, spec: AgentTaskSpec, parent: QWidget | None = None):
        super().__init__(parent)
        self._spec = spec
        self._thread = None
        self.setWindowTitle(f"Agent Task - {spec.title}")
        self.setMinimumSize(620, 420)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel(f"<b>{spec.title}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(title)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.log_view, stretch=1)

        row = QHBoxLayout()
        self.status_lbl = QLabel("Starting...")
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        row.addWidget(self.status_lbl)
        row.addStretch()
        row.addWidget(close_btn)
        root.addLayout(row)

        self.log_line.connect(self._append_log)
        self.finished.connect(self._on_finished)
        fit_window_to_screen(self, preferred_width=700, preferred_height=500)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._thread is None:
            self._start_runner()

    def _start_runner(self) -> None:
        from core.agent_runner import AgentTaskRunner

        runner = AgentTaskRunner()

        def run_and_finish():
            run_dir = runner.run(self._spec, self.log_line.emit)
            self.finished.emit(str(run_dir))

        import threading

        self._thread = threading.Thread(target=run_and_finish, daemon=True)
        self._thread.start()

    def _append_log(self, line: str) -> None:
        self.log_view.append(line)

    def _on_finished(self, run_dir: str) -> None:
        self.status_lbl.setText(f"Finished. Log: {run_dir}")
