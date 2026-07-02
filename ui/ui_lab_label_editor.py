"""Small UI Lab label editor dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from addons.ui_lab import labels


def edit_label(match: str, parent=None) -> bool:
    """Open the UI Lab label editor for selected text."""
    selected = str(match or "").strip()
    if not selected:
        return False
    existing = labels.find_rule(selected)
    dialog = _LabelEditorDialog(selected, existing, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    labels.upsert_rule(selected, tooltip=dialog.tooltip_text(), style=dialog.style_text())
    return True


def delete_label(match: str, parent=None) -> bool:
    """Delete the UI Lab label for selected text."""
    del parent
    return labels.delete_rule(match)


class _LabelEditorDialog(QDialog):
    """Editor for one selected word/phrase label."""

    def __init__(self, selected: str, existing: dict[str, str] | None, parent=None):
        """Initialize the label editor."""
        super().__init__(parent)
        self.setWindowTitle("UI Lab label")
        self.setModal(True)
        self.setMinimumWidth(360)
        self._selected = selected

        style = existing.get("style", "") if existing else labels.DEFAULT_STYLE
        tooltip = existing.get("tooltip", "") if existing else ""

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        selected_lbl = QLabel(selected)
        selected_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(selected_lbl)

        form = QFormLayout()
        self._tooltip = QLineEdit(tooltip)
        self._tooltip.setPlaceholderText("Tooltip shown on hover")
        form.addRow("Popup text", self._tooltip)
        root.addLayout(form)

        style_row = QHBoxLayout()
        style_row.setSpacing(6)
        self._bold = _tool_button("B", "Bold")
        self._italic = _tool_button("I", "Italic")
        self._underline = _tool_button("U", "Underline")
        self._strike = _tool_button("S", "Strikethrough")
        for btn in (self._bold, self._italic, self._underline, self._strike):
            style_row.addWidget(btn)
        style_row.addStretch(1)
        root.addLayout(style_row)

        color_form = QFormLayout()
        self._text_color = QLineEdit()
        self._text_color.setPlaceholderText("#ffffff")
        self._text_color_btn = _color_button("Pick text color")
        self._text_color_btn.clicked.connect(
            lambda: self._pick_color(self._text_color, self._text_color_btn, "Pick text color")
        )
        self._highlight_color = QLineEdit()
        self._highlight_color.setPlaceholderText("#ffd166")
        self._highlight_color_btn = _color_button("Pick highlight color")
        self._highlight_color_btn.clicked.connect(
            lambda: self._pick_color(
                self._highlight_color,
                self._highlight_color_btn,
                "Pick highlight color",
            )
        )
        self._text_color.textChanged.connect(lambda: _sync_color_button(self._text_color, self._text_color_btn))
        self._highlight_color.textChanged.connect(
            lambda: _sync_color_button(self._highlight_color, self._highlight_color_btn)
        )
        color_form.addRow("Text color", _color_row(self._text_color, self._text_color_btn))
        color_form.addRow("Highlight color", _color_row(self._highlight_color, self._highlight_color_btn))
        root.addLayout(color_form)

        self._load_style(style)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def tooltip_text(self) -> str:
        """Return entered tooltip text."""
        return self._tooltip.text().strip()

    def style_text(self) -> str:
        """Return CSS style text from editor controls."""
        styles: list[str] = []
        if self._bold.isChecked():
            styles.append("font-weight:700")
        if self._italic.isChecked():
            styles.append("font-style:italic")
        decorations: list[str] = []
        if self._underline.isChecked():
            decorations.append("underline")
        if self._strike.isChecked():
            decorations.append("line-through")
        if decorations:
            styles.append("text-decoration:" + " ".join(decorations))
        text_color = self._text_color.text().strip()
        if text_color:
            styles.append("color:" + text_color)
        highlight_color = self._highlight_color.text().strip()
        if highlight_color:
            styles.append("background-color:" + highlight_color)
        return "; ".join(styles)

    def _load_style(self, style: str) -> None:
        """Populate controls from a CSS style string."""
        declarations = _parse_style(style)
        self._bold.setChecked(declarations.get("font-weight", "") in {"700", "bold"})
        self._italic.setChecked(declarations.get("font-style", "") == "italic")
        decoration = declarations.get("text-decoration", "")
        self._underline.setChecked("underline" in decoration)
        self._strike.setChecked("line-through" in decoration)
        self._text_color.setText(declarations.get("color", ""))
        self._highlight_color.setText(declarations.get("background-color", ""))
        _sync_color_button(self._text_color, self._text_color_btn)
        _sync_color_button(self._highlight_color, self._highlight_color_btn)

    def _pick_color(self, field: QLineEdit, button: QToolButton, title: str) -> None:
        """Open a color picker and write the selected hex color."""
        current = QColor(field.text().strip())
        if not current.isValid():
            current = QColor("#ffffff")
        chosen = QColorDialog.getColor(current, self, title)
        if not chosen.isValid():
            return
        field.setText(chosen.name(QColor.NameFormat.HexRgb))
        _sync_color_button(field, button)


def _tool_button(text: str, tooltip: str) -> QToolButton:
    btn = QToolButton()
    btn.setText(text)
    btn.setToolTip(tooltip)
    btn.setCheckable(True)
    btn.setFixedSize(30, 28)
    if text in {"B", "S"}:
        btn.setStyleSheet("font-weight: 700;")
    elif text == "I":
        btn.setStyleSheet("font-style: italic;")
    elif text == "U":
        btn.setStyleSheet("text-decoration: underline;")
    return btn


def _color_button(tooltip: str) -> QToolButton:
    btn = QToolButton()
    btn.setText("...")
    btn.setToolTip(tooltip)
    btn.setFixedSize(34, 28)
    _sync_color_button(None, btn)
    return btn


def _color_row(field: QLineEdit, button: QToolButton) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(field, 1)
    layout.addWidget(button)
    return row


def _sync_color_button(field: QLineEdit | None, button: QToolButton) -> None:
    raw = field.text().strip() if field is not None else ""
    color = QColor(raw)
    if color.isValid():
        button.setStyleSheet(
            "QToolButton {"
            f"background-color: {color.name(QColor.NameFormat.HexRgb)};"
            "border: 1px solid #6f6f80;"
            "border-radius: 4px;"
            "}"
        )
    else:
        button.setStyleSheet("QToolButton { border: 1px solid #6f6f80; border-radius: 4px; }")


def _parse_style(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in str(style or "").split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        out[key.strip().lower()] = value.strip()
    return out
