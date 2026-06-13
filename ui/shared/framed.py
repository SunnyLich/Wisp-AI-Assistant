"""Custom, palette-themed window chrome.

Native OS title bars cannot render the app's colour palette and look different on
every OS (GTK on Linux, NSWindow on macOS, DWM on Windows). To make the *whole*
window follow the light/dark template, app windows are made frameless and we
paint our own title bar + window buttons. Everything is styled with Qt
``palette()`` roles so it re-themes automatically when ``apply_app_theme()``
swaps the application palette.

The single entry point is :func:`install_window_chrome`; ``window_utils``'s
``enable_standard_window_controls`` simply forwards to it, so every existing
top-level window gets the new chrome for free.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QObject, QEvent
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QSizePolicy,
)

_TITLE_BAR_HEIGHT = 38
_BTN_W = 44
_BTN_H = 28
_RESIZE_MARGIN = 6

# Styled on the title bar itself (not the app/window stylesheet) so it is
# isolated from each window's own stylesheet and wins via selector specificity.
# All colours are palette roles mapped in ui.shared.theme.apply_app_theme, so a
# theme change refreshes the chrome automatically.
_TITLE_BAR_QSS = """
#wispTitleBar {
    background: palette(window);
    border-bottom: 1px solid palette(mid);
}
#wispTitleText {
    color: palette(window-text);
    font-size: 10pt;
    font-weight: 600;
    background: transparent;
}
QPushButton[winbtn] {
    background: transparent;
    border: none;
    border-radius: 6px;
    color: palette(window-text);
    font-size: 11pt;
    margin: 0px 1px;
}
QPushButton[winbtn]:hover { background: rgba(127, 127, 127, 0.20); }
QPushButton[winbtn]:pressed { background: rgba(127, 127, 127, 0.32); }
QPushButton[winbtn="close"]:hover { background: #e81123; }
QPushButton[winbtn="close"]:pressed { background: #c50f1f; }
"""


class _WinButton(QPushButton):
    """A window-control button whose glyph is painted (font-independent) so it
    looks identical on every OS. Background/hover still come from the stylesheet."""

    def __init__(self, kind: str, parent: QWidget):
        super().__init__(parent)
        self._kind = kind
        self.setProperty("winbtn", kind)
        self.setFixedSize(_BTN_W, _BTN_H)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)  # stylesheet background / hover / pressed
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._kind == "close" and self.underMouse():
            colour = QColor("#ffffff")
        else:
            colour = self.palette().color(QPalette.ColorRole.WindowText)
        p.setPen(QPen(colour, 1.2))
        r = self.rect()
        cx, cy, s = r.center().x(), r.center().y(), 5
        if self._kind == "min":
            p.drawLine(cx - s, cy, cx + s, cy)
        elif self._kind == "max":
            p.drawRect(cx - s, cy - s, 2 * s, 2 * s)
        else:  # close
            p.drawLine(cx - s, cy - s, cx + s, cy + s)
            p.drawLine(cx - s, cy + s, cx + s, cy - s)
        p.end()


class _TitleBar(QWidget):
    """Draggable title bar with minimise / maximise / close buttons."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        self.setObjectName("wispTitleBar")
        self.setFixedHeight(_TITLE_BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(_TITLE_BAR_QSS)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(0)

        self._title = QLabel(window.windowTitle(), self)
        self._title.setObjectName("wispTitleText")
        lay.addWidget(self._title)
        lay.addStretch(1)

        self._min_btn = _WinButton("min", self)
        self._max_btn = _WinButton("max", self)
        self._close_btn = _WinButton("close", self)
        for b in (self._min_btn, self._max_btn, self._close_btn):
            lay.addWidget(b)

        self._min_btn.clicked.connect(window.showMinimized)
        self._max_btn.clicked.connect(self._toggle_max)
        self._close_btn.clicked.connect(window.close)
        window.windowTitleChanged.connect(self._title.setText)

    def _toggle_max(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _ResizeGrip(QWidget):
    """Invisible hit-area along a window edge/corner that drives a WM resize."""

    def __init__(self, window: QWidget, edges: Qt.Edge, cursor: Qt.CursorShape):
        super().__init__(window)
        self._window = window
        self._edges = edges
        self.setCursor(cursor)
        # Counter the global ``QWidget { background-color: ... }`` rule so the
        # grip stays see-through and only acts as a mouse hit-area.
        self.setStyleSheet("background: transparent;")

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self._window.isMaximized():
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemResize(self._edges)
                event.accept()
                return
        super().mousePressEvent(event)


class _WindowChrome(QObject):
    """Installs frameless custom chrome on a top-level window.

    Frameless-ness is set immediately (before first show); the title bar is
    grafted in on the first Polish/Show event, once the window's content layout
    exists. The existing layout is re-hosted under ``[title bar | content]``.
    """

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        self._title_bar: _TitleBar | None = None
        self._grips: list[_ResizeGrip] = []
        self._installed = False
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        window.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._window:
            et = event.type()
            if not self._installed and et in (
                QEvent.Type.Polish, QEvent.Type.Show
            ):
                self._install()
            elif self._installed and et == QEvent.Type.Resize:
                self._reposition_grips()
        return False

    def _install(self) -> None:
        w = self._window
        old_layout = w.layout()
        if old_layout is None:
            return  # content not built yet — retry on the next event
        self._installed = True

        content = QWidget(w)
        content.setObjectName("wispWindowContent")
        content.setLayout(old_layout)  # re-host the window's real content

        self._title_bar = _TitleBar(w)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._title_bar)
        outer.addWidget(content, 1)
        w.setLayout(outer)

        self._make_grips()
        self._reposition_grips()

    def _make_grips(self) -> None:
        w = self._window
        E = Qt.Edge
        C = Qt.CursorShape
        specs = [
            (E.TopEdge, C.SizeVerCursor),
            (E.BottomEdge, C.SizeVerCursor),
            (E.LeftEdge, C.SizeHorCursor),
            (E.RightEdge, C.SizeHorCursor),
            (E.TopEdge | E.LeftEdge, C.SizeFDiagCursor),
            (E.BottomEdge | E.RightEdge, C.SizeFDiagCursor),
            (E.TopEdge | E.RightEdge, C.SizeBDiagCursor),
            (E.BottomEdge | E.LeftEdge, C.SizeBDiagCursor),
        ]
        for edges, cursor in specs:
            self._grips.append(_ResizeGrip(w, edges, cursor))

    def _reposition_grips(self) -> None:
        if not self._grips:
            return
        w = self._window
        width = w.width()
        height = w.height()
        m = _RESIZE_MARGIN
        # Order matches _make_grips: top, bottom, left, right, tl, br, tr, bl
        rects = [
            (m, 0, width - 2 * m, m),
            (m, height - m, width - 2 * m, m),
            (0, m, m, height - 2 * m),
            (width - m, m, m, height - 2 * m),
            (0, 0, m, m),
            (width - m, height - m, m, m),
            (width - m, 0, m, m),
            (0, height - m, m, m),
        ]
        for grip, (x, y, gw, gh) in zip(self._grips, rects):
            grip.setGeometry(x, y, max(0, gw), max(0, gh))
            grip.raise_()


def install_window_chrome(window: QWidget) -> None:
    """Make *window* a frameless, palette-themed window with a custom title bar.

    Safe to call once per top-level window, in its ``__init__`` (before it is
    shown). The chrome attaches itself to the window's lifetime.
    """
    window.setWindowFlag(Qt.WindowType.Window, True)
    _WindowChrome(window)
