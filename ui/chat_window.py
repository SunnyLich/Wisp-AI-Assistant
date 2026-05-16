"""
ui/chat_window.py — Multi-turn chat window.

Opened by clicking the doll. Shows conversation history and allows
follow-up messages. If CHAT_AUTO_ELABORATE is enabled, automatically
sends an elaboration prompt when first opened.

Send message: Enter (Shift+Enter for newline).
"""
from __future__ import annotations
import threading
import config
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QTextEdit, QPushButton, QFrame, QApplication,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QFont

_W = 420
_H = 520
_BG       = "#1c1c24"
_TITLE_BG = "#16161f"
_USER_BG  = "#3a3a5c"
_AI_BG    = "#26263a"
_BORDER   = "rgba(255,255,255,20)"
_TEXT     = "#e6e6e6"
_HINT     = "#888888"
_ACCENT   = "#a0a0ff"


class _StreamSignals(QObject):
    chunk     = pyqtSignal(str)
    finished  = pyqtSignal()


class ChatWindow(QWidget):
    def __init__(self, history: list, send_fn, auto_message: str | None = None):
        """
        Args:
            history:      Existing conversation as list of
                          {"role": "user"|"assistant", "content": str}.
            send_fn:      Callable(messages: list) -> Generator[str] — streams
                          a reply given the full messages list.
            auto_message: If set, automatically sent when the window opens.
        """
        super().__init__()
        self._history = list(history)
        self._send_fn = send_fn
        self._streaming = False
        self._current_ai_label: QLabel | None = None
        self._current_ai_text = ""

        self._signals = _StreamSignals()
        self._signals.chunk.connect(self._on_chunk)
        self._signals.finished.connect(self._on_finished)

        self.setWindowTitle("Chat")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setStyleSheet(f"background: {_BG}; color: {_TEXT};")
        self.setMinimumSize(_W, _H)
        self.resize(_W, _H)

        self._build_ui()
        self._populate_history()
        self._center_on_screen()

        if auto_message:
            QTimer.singleShot(120, lambda: self._send(auto_message))

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._make_title_bar())
        root.addWidget(self._make_scroll_area(), stretch=1)
        root.addWidget(self._make_input_area())

    def _make_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(38)
        bar.setStyleSheet(f"background: {_TITLE_BG}; border-bottom: 1px solid {_BORDER};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 0, 8, 0)

        title = QLabel("Chat")
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {_ACCENT}; background: transparent;")

        close = QPushButton("✕")
        close.setFixedSize(26, 26)
        close.setStyleSheet(
            "QPushButton { background: transparent; color: #888; border: none; font-size: 13px; }"
            "QPushButton:hover { color: #e66; }"
        )
        close.clicked.connect(self.close)

        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(close)
        return bar

    def _make_scroll_area(self) -> QScrollArea:
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"background: {_BG};")

        self._msg_container = QWidget()
        self._msg_container.setStyleSheet(f"background: {_BG};")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(14, 14, 14, 14)
        self._msg_layout.setSpacing(10)
        self._msg_layout.addStretch()

        self._scroll.setWidget(self._msg_container)
        return self._scroll

    def _make_input_area(self) -> QWidget:
        frame = QWidget()
        frame.setStyleSheet(f"background: {_TITLE_BG}; border-top: 1px solid {_BORDER};")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        self._input = QTextEdit()
        self._input.setFixedHeight(62)
        self._input.setPlaceholderText("Message… (Enter to send, Shift+Enter for newline)")
        self._input.setStyleSheet(
            f"QTextEdit {{"
            f"  background: rgba(255,255,255,8);"
            f"  border: 1px solid {_BORDER};"
            f"  border-radius: 6px;"
            f"  color: {_TEXT};"
            f"  padding: 6px 8px;"
            f"  font-size: 10pt;"
            f"}}"
        )
        self._input.installEventFilter(self)

        self._send_btn = QPushButton("Send")
        self._send_btn.setFixedSize(64, 46)
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: #1c1c24; border: none;"
            f"  border-radius: 6px; font-size: 10pt; font-weight: bold; }}"
            f"QPushButton:hover {{ background: #b8b8ff; }}"
            f"QPushButton:disabled {{ background: #444; color: #666; }}"
        )
        self._send_btn.clicked.connect(self._on_send_clicked)

        layout.addWidget(self._input)
        layout.addWidget(self._send_btn)
        return frame

    # ------------------------------------------------------------------
    # Message bubbles
    # ------------------------------------------------------------------

    def _populate_history(self):
        for msg in self._history:
            self._add_bubble(msg["content"], msg["role"])

    def _add_bubble(self, text: str, role: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bg = _USER_BG if role == "user" else _AI_BG
        role_color = _HINT
        lbl.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {_TEXT}; border-radius: 8px;"
            f"  padding: 8px 11px; font-size: 10pt; }}"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Role label above bubble
        role_lbl = QLabel("You" if role == "user" else "Assistant")
        role_lbl.setStyleSheet(f"color: {role_color}; background: transparent; font-size: 8pt;")

        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(2)
        wl.addWidget(role_lbl)
        wl.addWidget(lbl)

        # Insert before the trailing stretch
        idx = self._msg_layout.count() - 1
        self._msg_layout.insertWidget(idx, wrapper)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return lbl

    def _scroll_to_bottom(self):
        self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def _on_send_clicked(self):
        text = self._input.toPlainText().strip()
        if text and not self._streaming:
            self._input.clear()
            self._send(text)

    def _send(self, text: str):
        if self._streaming:
            return
        self._streaming = True
        self._send_btn.setEnabled(False)

        self._add_bubble(text, "user")
        self._history.append({"role": "user", "content": text})

        # Streaming placeholder
        self._current_ai_text = ""
        self._current_ai_label = self._add_bubble("…", "assistant")

        messages = (
            [{"role": "system", "content": config.get_system_prompt()}]
            + self._history
        )

        def _stream():
            try:
                for chunk in self._send_fn(messages):
                    self._signals.chunk.emit(chunk)
            finally:
                self._signals.finished.emit()

        threading.Thread(target=_stream, daemon=True).start()

    def _on_chunk(self, chunk: str):
        self._current_ai_text += chunk
        if self._current_ai_label:
            self._current_ai_label.setText(self._current_ai_text)
        self._scroll_to_bottom()

    def _on_finished(self):
        if self._current_ai_text:
            self._history.append({"role": "assistant", "content": self._current_ai_text})
        self._current_ai_label = None
        self._current_ai_text = ""
        self._streaming = False
        self._send_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Keyboard: Enter to send, Shift+Enter for newline
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
                self._on_send_clicked()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _center_on_screen(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.x() + (screen.width()  - self.width())  // 2,
            screen.y() + (screen.height() - self.height()) // 2,
        )
