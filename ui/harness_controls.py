"""Compact live controls for the ChatGPT and Claude harnesses."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import config
from core.system.env_utils import write_env_file
from core.system.paths import REPO_ROOT
from ui.i18n import localize_widget_tree, t
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen

_CODEX_MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex-spark",
)
_CLAUDE_MODELS = ("opus", "sonnet", "haiku")


def _select_data(combo: QComboBox, value: str) -> None:
    index = combo.findData(str(value or ""))
    if index >= 0:
        combo.setCurrentIndex(index)
    elif value:
        combo.setEditText(value)


class _NoRepeatComboBox(QComboBox):
    """Keep the current value in the field without repeating it in the open menu."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hidden_popup_row = -1

    def showPopup(self) -> None:  # noqa: N802 - Qt API
        self._restore_hidden_row()
        if self.count() > 1 and self.currentIndex() >= 0:
            self._hidden_popup_row = self.currentIndex()
            self.view().setRowHidden(self._hidden_popup_row, True)
        super().showPopup()

    def hidePopup(self) -> None:  # noqa: N802 - Qt API
        super().hidePopup()
        self._restore_hidden_row()

    def _restore_hidden_row(self) -> None:
        if self._hidden_popup_row >= 0:
            self.view().setRowHidden(self._hidden_popup_row, False)
            self._hidden_popup_row = -1


class HarnessControlsDialog(QDialog):
    """Provider-specific controls that apply to the next Wisp turn."""

    applied = Signal(object)

    def __init__(self, provider: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.provider = "claude" if str(provider).lower() == "claude" else "codex"
        provider_name = "Claude" if self.provider == "claude" else "ChatGPT"
        self.provider_name = provider_name
        self.setWindowTitle(t("{provider} controls").format(provider=provider_name))
        self.setMinimumWidth(390)
        enable_standard_window_controls(self)

        outer = QVBoxLayout(self)
        title = QLabel(t("Live {provider} controls").format(provider=provider_name))
        title.setStyleSheet("font-size: 14pt; font-weight: 700;")
        outer.addWidget(title)

        note = QLabel(
            t(
                "Wisp shows every thought, plan, tool action, approval, and reply event "
                "the provider exposes. Private hidden chain-of-thought is not available."
            )
        )
        note.setWordWrap(True)
        outer.addWidget(note)

        form = QFormLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItem(t("Provider default"), "")
        for model in (_CLAUDE_MODELS if self.provider == "claude" else _CODEX_MODELS):
            self.model.addItem(model, model)
        model_value = getattr(
            config,
            "WISP_CLAUDE_MODEL" if self.provider == "claude" else "WISP_CODEX_MODEL",
            "",
        )
        _select_data(self.model, str(model_value or ""))
        form.addRow(t("Model"), self.model)

        workspace_row = QWidget()
        workspace_layout = QHBoxLayout(workspace_row)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(6)
        self.workspace = QLineEdit()
        self.workspace.setReadOnly(True)
        self.workspace.setPlaceholderText(t("Auto"))
        self.workspace.setToolTip(t("Folder the agent is allowed to manipulate"))
        workspace_value = getattr(
            config,
            "WISP_CLAUDE_WORKSPACE" if self.provider == "claude" else "WISP_CODEX_WORKSPACE",
            "",
        )
        self.workspace.setText(str(workspace_value or ""))
        browse_workspace = QPushButton(t("Browse..."))
        browse_workspace.clicked.connect(self._choose_workspace)
        auto_workspace = QPushButton(t("Auto"))
        auto_workspace.clicked.connect(self.workspace.clear)
        workspace_layout.addWidget(self.workspace, 1)
        workspace_layout.addWidget(browse_workspace)
        workspace_layout.addWidget(auto_workspace)
        form.addRow(t("Project"), workspace_row)

        self.fast = QCheckBox(t("Fast mode (uses more quota or may cost more)"))
        self.fast.setChecked(
            bool(
                getattr(
                    config,
                    "WISP_CLAUDE_FAST_MODE" if self.provider == "claude" else "WISP_CODEX_FAST_MODE",
                    False,
                )
            )
        )
        form.addRow(t("Speed"), self.fast)

        self.effort = QComboBox()
        self.effort.addItem(t("Provider default"), "")
        effort_values = ("low", "medium", "high", "xhigh", "max")
        if self.provider == "codex":
            effort_values += ("ultra",)
        for value in effort_values:
            self.effort.addItem(t(value.title()), value)
        effort_value = getattr(
            config,
            "WISP_CLAUDE_REASONING_EFFORT"
            if self.provider == "claude"
            else "WISP_CODEX_REASONING_EFFORT",
            "high",
        )
        _select_data(self.effort, str(effort_value or ""))
        form.addRow(t("Reasoning effort"), self.effort)

        self.reasoning = QComboBox()
        if self.provider == "codex":
            self.reasoning.addItem(t("Detailed summaries"), "detailed")
            self.reasoning.addItem(t("Concise summaries"), "concise")
        else:
            self.reasoning.addItem(t("Provider summaries"), "summarized")
        self.reasoning.addItem(t("Off"), "none")
        summary_value = getattr(
            config,
            "WISP_CLAUDE_REASONING_SUMMARY"
            if self.provider == "claude"
            else "WISP_CODEX_REASONING_SUMMARY",
            "summarized" if self.provider == "claude" else "detailed",
        )
        _select_data(self.reasoning, str(summary_value or ""))
        form.addRow(t("Visible reasoning"), self.reasoning)

        self.approval = _NoRepeatComboBox()
        self.approval.addItem(t("Require approval"), "ask")
        self.approval.addItem(t("Allow within project"), "auto_edits")
        self.approval.addItem(t("Full access"), "full_access")
        self.approval.addItem(t("Plan only (read-only)"), "read_only")
        approval_value = getattr(
            config,
            "WISP_CLAUDE_APPROVAL_MODE"
            if self.provider == "claude"
            else "WISP_CODEX_APPROVAL_MODE",
            "ask",
        )
        _select_data(self.approval, str(approval_value or "ask"))
        self.approval.currentIndexChanged.connect(self._refresh_permission_help)
        form.addRow(t("Permission mode"), self.approval)
        self.approval_help = QLabel()
        self.approval_help.setWordWrap(True)
        self.approval_help.setStyleSheet("color: palette(mid); font-size: 9pt;")
        form.addRow("", self.approval_help)
        self._refresh_permission_help()
        outer.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        localize_widget_tree(self)
        fit_window_to_screen(self)

    def _model_value(self) -> str:
        index = self.model.currentIndex()
        if index >= 0 and self.model.currentText() == self.model.itemText(index):
            return str(self.model.itemData(index) or "").strip()
        return self.model.currentText().strip()

    def _refresh_permission_help(self) -> None:
        mode = str(self.approval.currentData() or "ask")
        if mode == "ask":
            source = (
                "Ask before file changes, commands, or other protected actions."
                if self.provider == "claude"
                else "Work within the project automatically; ask before network or outside access."
            )
        elif mode == "auto_edits":
            source = (
                "Allow file changes within the selected project folder. Other actions may still require approval."
                if self.provider == "claude"
                else "Allow actions within the selected project folder. Network and outside access stay blocked."
            )
        elif mode == "full_access":
            source = "Allow unrestricted access to files and the network without asking."
        else:
            source = "Read files and make a plan without changing anything."
        self.approval_help.setText(t(source))
        self.approval_help.setStyleSheet(
            "color: #ff8a3d; font-size: 9pt; font-weight: 600;"
            if mode == "full_access"
            else "color: palette(mid); font-size: 9pt;"
        )

    def _choose_workspace(self) -> None:
        start = self.workspace.text().strip() or str(REPO_ROOT)
        selected = QFileDialog.getExistingDirectory(
            self,
            t("Choose workspace folder for {provider}").format(provider=self.provider_name),
            start,
        )
        if selected:
            self.workspace.setText(selected)

    def _save(self) -> None:
        prefix = "WISP_CLAUDE" if self.provider == "claude" else "WISP_CODEX"
        values = {
            f"{prefix}_MODEL": self._model_value(),
            f"{prefix}_WORKSPACE": self.workspace.text().strip(),
            f"{prefix}_FAST_MODE": "true" if self.fast.isChecked() else "false",
            f"{prefix}_APPROVAL_MODE": str(self.approval.currentData() or "ask"),
            f"{prefix}_REASONING_EFFORT": str(self.effort.currentData() or ""),
            f"{prefix}_REASONING_SUMMARY": str(self.reasoning.currentData() or "none"),
        }
        write_env_file(REPO_ROOT / ".env", values)
        config.reload()
        # Close before notifying the rest of Wisp.  The applied signal is
        # synchronous and its receivers reload the UI and worker settings; if
        # that work runs while this dialog is still visible, the controls can
        # appear to snap back even though the new values were saved correctly.
        self.accept()
        self.applied.emit(sorted(values))
