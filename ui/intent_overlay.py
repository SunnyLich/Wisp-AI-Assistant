"""
ui/intent_overlay.py — Compact intent picker shown on Ctrl+Q.

Small floating widget centred on screen — no background dim.
Rows are built dynamically from config.INTENT_ROWS plus a fixed
Custom Prompt row (config.HOTKEY_CUSTOM_PROMPT_KEY).
Press the matching key to pick, Escape to cancel.
"""
from __future__ import annotations
from PyQt6.QtWidgets import QWidget, QApplication, QLineEdit
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QBrush
import config



def _build_rows() -> list[dict]:
    """Build the full list of overlay rows from config at show-time."""
    rows = []
    for r in config.INTENT_ROWS:
        rows.append({
            "glyph":     r["key"].upper() if r["key"] else "?",
            "label":     r["label"],
            "prompt":    r["prompt"],
            "is_custom": False,
        })
    # Custom prompt row always appended last
    rows.append({
        "glyph":     config.HOTKEY_CUSTOM_PROMPT_KEY.upper(),
        "label":     "Custom prompt",
        "prompt":    "",
        "is_custom": True,
    })
    return rows


_W             = 230
_ROW_H         = 36
_PADDING       = 14
_RADIUS        = 10
_AUTO_CLOSE_MS = 5000
_INPUT_EXTRA   = 54   # extra height when custom-input mode is active

_BG     = QColor(28, 28, 36, 230)
_ROW_HL = QColor(255, 255, 255, 22)
_ARROW  = QColor(160, 160, 255, 220)
_LABEL  = QColor(230, 230, 230, 230)
_HINT   = QColor(120, 120, 140, 180)


class IntentOverlay(QWidget):
    intent_chosen = pyqtSignal(str, str)   # (direction_key, prompt)
    cancelled     = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Popup   # auto-focuses on Windows; closes on click-outside
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._rows = _build_rows()
        n_rows = len(self._rows)
        h = _PADDING * 2 + _ROW_H * n_rows + 28   # 28px for ESC hint
        self._normal_h = h
        self.setFixedSize(_W, h)

        screen = QApplication.primaryScreen().geometry()
        self.move(
            screen.x() + (screen.width()  - _W) // 2,
            screen.y() + (screen.height() - h)  // 2,
        )

        self._hovered: int | None = None
        self._handled = False
        self._custom_mode = False

        # Inline text input (shown only in custom mode)
        self._input_line = QLineEdit(self)
        self._input_line.setPlaceholderText("Type your prompt, press Enter...")
        self._input_line.setStyleSheet(
            "QLineEdit {"
            "  background: rgba(255,255,255,12);"
            "  border: 1px solid rgba(255,255,255,55);"
            "  border-radius: 5px;"
            "  color: #e6e6e6;"
            "  padding: 4px 8px;"
            "  font-size: 10pt;"
            "}"
        )
        self._input_line.hide()
        self._input_line.returnPressed.connect(self._fire_custom)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._cancel)
        self._timer.start(_AUTO_CLOSE_MS)

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        p.setBrush(QBrush(_BG))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, _W, self.height(), _RADIUS, _RADIUS)

        key_font   = QFont("Segoe UI", 13, QFont.Weight.Bold)
        label_font = QFont("Segoe UI", 10)
        hint_font  = QFont("Segoe UI", 8)

        y = _PADDING
        for i, row in enumerate(self._rows):
            if i == self._hovered:
                p.setBrush(QBrush(_ROW_HL))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(6, y, _W - 12, _ROW_H, 6, 6)

            p.setFont(key_font)
            p.setPen(QPen(_ARROW))
            p.drawText(12, y, 28, _ROW_H, Qt.AlignmentFlag.AlignCenter, row["glyph"])

            p.setFont(label_font)
            p.setPen(QPen(_LABEL))
            p.drawText(46, y, _W - 52, _ROW_H,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       row["label"])

            y += _ROW_H

        p.setFont(hint_font)
        p.setPen(QPen(_HINT))
        hint_y = y + (_INPUT_EXTRA if self._custom_mode else 4)
        p.drawText(0, hint_y, _W, 20, Qt.AlignmentFlag.AlignCenter, "ESC to cancel")

        p.end()

    # ------------------------------------------------------------------
    # Key input
    # ------------------------------------------------------------------

    def _select(self, idx: int):
        if self._handled:
            return
        if self._rows[idx]["is_custom"]:
            self._hovered = idx
            self._unhook()  # release grabKeyboard; Qt handles typing in QLineEdit
            QTimer.singleShot(0, self._enter_custom_mode)
            return
        self._handled = True
        self._hovered = idx
        self.update()
        QTimer.singleShot(80, lambda: self._fire(idx))

    def _enter_custom_mode(self):
        self._custom_mode = True
        new_h = self._normal_h + _INPUT_EXTRA
        screen = QApplication.primaryScreen().geometry()
        self.setFixedSize(_W, new_h)
        self.move(
            screen.x() + (screen.width()  - _W) // 2,
            screen.y() + (screen.height() - new_h) // 2,
        )
        self._input_line.setGeometry(10, self._normal_h - 24, _W - 20, 32)
        self._input_line.show()
        self._input_line.setFocus()
        self.update()

    def _fire_custom(self):
        text = self._input_line.text().strip()
        if not text:
            return
        self._handled = True
        self._unhook()
        self._timer.stop()
        self.intent_chosen.emit(config.HOTKEY_CUSTOM_PROMPT_KEY, text)
        self.close()

    def keyPressEvent(self, event):
        key_map: dict[Qt.Key, int] = {}
        for i, row in enumerate(self._rows):
            qt_key = getattr(Qt.Key, f"Key_{row['glyph']}", None)
            if qt_key is not None:
                key_map[qt_key] = i

        idx = key_map.get(event.key())
        if idx is not None:
            self._select(idx)
        elif event.key() == Qt.Key.Key_Escape:
            self._cancel()

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.grabKeyboard()

    # ------------------------------------------------------------------
    # Cleanup / fire
    # ------------------------------------------------------------------

    def _unhook(self):
        try:
            self.releaseKeyboard()
        except Exception:
            pass

    def _fire(self, idx: int):
        self._unhook()
        self._timer.stop()
        row = self._rows[idx]
        self.intent_chosen.emit(row["glyph"], row["prompt"])
        self.close()

    def _cancel(self):
        if self._handled:
            return
        self._handled = True
        self._unhook()
        self._timer.stop()
        self.cancelled.emit()
        self.close()
