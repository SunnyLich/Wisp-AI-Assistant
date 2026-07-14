"""Small text-based activity spinner for lightweight installer windows."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel


class ActivitySpinner(QLabel):
    """Animate a compact circular glyph without an indeterminate progress bar."""

    _FRAMES = ("◴", "◷", "◶", "◵")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._advance)
        self.setObjectName("activitySpinner")
        self.setFixedWidth(22)
        self.setText("○")

    def start(self) -> None:
        """Start or resume the circular activity animation."""
        self._frame = 0
        self.setText(self._FRAMES[0])
        if not self._timer.isActive():
            self._timer.start()

    def stop(self, symbol: str = "○") -> None:
        """Stop animation and display a stable completion-state symbol."""
        self._timer.stop()
        self.setText(symbol)

    def is_active(self) -> bool:
        """Return whether the animation timer is running."""
        return self._timer.isActive()

    def _advance(self) -> None:
        self._frame = (self._frame + 1) % len(self._FRAMES)
        self.setText(self._FRAMES[self._frame])
