"""
ui/snip_overlay.py — Full-screen region selector (Ctrl+Alt+Q).

Covers the entire virtual desktop (all monitors) with a semi-dark overlay.
The user clicks and drags to select a region; the widget emits
region_selected with an mss-compatible region dict on release,
or cancelled on Escape / zero-size drag.
"""
from __future__ import annotations
import sys
import time
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtCore import Qt, Signal, QRect, QPoint, QTimer
from PySide6.QtGui import QPainter, QColor, QFont, QCursor, QPen

from ui.i18n import t

_DIM_ALPHA = 110   # overlay darkness 0-255
_TOOLBAR_W = 360
_TOOLBAR_H = 38
_TOOLBAR_TOP = 18
_TOOL_GAP = 6


class SnipOverlay(QWidget):
    """Model snip overlay."""
    region_selected = Signal(dict)   # mss-format: {left, top, width, height}
    cancelled       = Signal()

    def __init__(self, parent=None, app_region: dict | None = None):
        """Initialize the snip overlay instance."""
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

        # Cover the full virtual desktop (all monitors combined)
        vg = QRect()
        for screen in QApplication.screens():
            vg = vg.united(screen.geometry())
        self.setGeometry(vg)
        self._virtual_origin = vg.topLeft()

        self._t_created = time.monotonic()
        self._closed = False
        self._first_paint_logged = False
        self._origin: QPoint | None = None
        self._mode = "area"
        self._app_region = self._normalize_region(app_region)
        self._toolbar_rect = QRect()
        self._mode_rects: list[tuple[str, QRect]] = []
        # Current drag rectangle, drawn directly in paintEvent. We deliberately
        # avoid QRubberBand here: on macOS it is backed by its own native window,
        # and a child window on a translucent Qt.Tool overlay aborts Cocoa
        # (SIGABRT) the moment the selector is shown.
        self._sel_rect: QRect | None = None

    # ------------------------------------------------------------------
    # Paint — dim the whole screen; rubber band provides the selection box
    # ------------------------------------------------------------------

    def paintEvent(self, _event):
        """Paint event."""
        if not self._first_paint_logged:
            self._first_paint_logged = True
            print(
                f"[snip.timing] first paint {time.monotonic() - self._t_created:.2f}s "
                "after build (macOS compositing the overlay)",
                file=sys.stderr,
                flush=True,
            )
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, _DIM_ALPHA))

        # Selection box: punch a brighter, outlined hole where the user is
        # dragging (replaces the old QRubberBand child widget).
        if self._sel_rect is not None and not self._sel_rect.isNull():
            p.fillRect(self._sel_rect, QColor(0, 0, 0, 0))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(self._sel_rect, QColor(255, 255, 255, 20))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(77, 163, 255, 230), 1))
            p.drawRect(self._sel_rect.adjusted(0, 0, -1, -1))

        font = QFont("Segoe UI", 11)
        p.setFont(font)
        p.setPen(QColor(220, 220, 220, 200))
        p.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            f"  {t('Click and drag to select a region  -  ESC to cancel')}  ",
        )
        self._paint_toolbar(p)
        p.end()

    def _paint_toolbar(self, p: QPainter) -> None:
        """Paint the lightweight capture-mode chooser."""
        toolbar_x = (self.width() - _TOOLBAR_W) // 2
        self._toolbar_rect = QRect(toolbar_x, _TOOLBAR_TOP, _TOOLBAR_W, _TOOLBAR_H)
        p.fillRect(self._toolbar_rect, QColor(24, 26, 32, 232))
        p.setPen(QPen(QColor(255, 255, 255, 38), 1))
        p.drawRect(self._toolbar_rect.adjusted(0, 0, -1, -1))

        labels = [("area", t("Rectangle"))]
        if self._app_region:
            labels.append(("app", t("App")))
        labels.append(("full", t("Full screen")))
        x = self._toolbar_rect.x() + _TOOL_GAP
        y = self._toolbar_rect.y() + _TOOL_GAP
        w = (self._toolbar_rect.width() - _TOOL_GAP * (len(labels) + 1)) // len(labels)
        h = self._toolbar_rect.height() - _TOOL_GAP * 2
        self._mode_rects = []
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        for mode, label in labels:
            rect = QRect(x, y, w, h)
            self._mode_rects.append((mode, rect))
            active = self._mode == mode
            p.fillRect(rect, QColor(77, 163, 255, 185 if active else 44))
            p.setPen(QPen(QColor(168, 213, 255, 230 if active else 120), 1))
            p.drawRect(rect.adjusted(0, 0, -1, -1))
            p.setPen(QColor(245, 248, 255, 240 if active else 190))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            x += w + _TOOL_GAP

    def _toolbar_mode_at(self, pos: QPoint) -> str | None:
        """Return the clicked toolbar mode, if any."""
        for mode, rect in self._mode_rects:
            if rect.contains(pos):
                return mode
        return None

    def _select_fullscreen(self) -> None:
        """Capture the full virtual desktop."""
        self._unhook()
        self.region_selected.emit(self._region_for(self.rect()))
        self.close()

    def _select_app(self) -> None:
        """Capture the preselected app/window bounds."""
        if not self._app_region:
            return
        self._unhook()
        self.region_selected.emit(dict(self._app_region))
        self.close()

    @staticmethod
    def _normalize_region(region: dict | None) -> dict | None:
        """Return a capture region if it has useful dimensions."""
        if not isinstance(region, dict):
            return None
        try:
            left = int(round(float(region.get("left") or 0)))
            top = int(round(float(region.get("top") or 0)))
            width = int(round(float(region.get("width") or 0)))
            height = int(round(float(region.get("height") or 0)))
        except Exception:
            return None
        if width <= 4 or height <= 4:
            return None
        return {"left": left, "top": top, "width": width, "height": height}

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        """Handle mouse press event for snip overlay."""
        if event.button() == Qt.MouseButton.LeftButton:
            mode = self._toolbar_mode_at(event.pos())
            if mode is not None:
                self._mode = mode
                self.update()
                if mode == "app":
                    QTimer.singleShot(0, self._select_app)
                    return
                if mode == "full":
                    QTimer.singleShot(0, self._select_fullscreen)
                return
            if self._toolbar_rect.contains(event.pos()):
                return
            if self._mode == "full":
                self._select_fullscreen()
                return
            self._origin = event.pos()
            self._sel_rect = QRect(self._origin, self._origin)
            self.update()

    def mouseMoveEvent(self, event):
        """Handle mouse move event for snip overlay."""
        if self._origin is not None:
            self._sel_rect = QRect(self._origin, event.pos()).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        """Handle mouse release event for snip overlay."""
        if event.button() == Qt.MouseButton.LeftButton and self._origin is not None:
            rect = QRect(self._origin, event.pos()).normalized()
            self._sel_rect = None
            self._unhook()
            if rect.width() > 4 and rect.height() > 4:
                self.region_selected.emit(self._region_for(rect))
            else:
                self.cancelled.emit()
            self.close()

    def _region_for(self, rect: QRect) -> dict:
        """Translate the widget-local selection into an mss-style region.

        Qt reports mouse coordinates in device-independent (logical) pixels, but
        mss.grab on Windows/Linux captures in physical pixels. On a scaled
        display (e.g. 150%) feeding logical coordinates straight to mss grabs a
        region shifted up and to the left, with the error growing further from
        the top-left corner. Scale by the screen's device-pixel ratio so the
        grab lands exactly where the user dragged. macOS' screencapture -R takes
        logical points, so that path keeps the unscaled coordinates.
        """
        # Widget-local coords -> absolute logical screen coords.
        g_left = rect.x() + self._virtual_origin.x()
        g_top  = rect.y() + self._virtual_origin.y()
        width  = rect.width()
        height = rect.height()

        if sys.platform == "darwin":
            return {"left": g_left, "top": g_top, "width": width, "height": height}

        screen = QApplication.screenAt(QPoint(g_left, g_top)) or self.screen()
        dpr = screen.devicePixelRatio() if screen is not None else 1.0
        return {
            "left":   round(g_left * dpr),
            "top":    round(g_top * dpr),
            "width":  round(width * dpr),
            "height": round(height * dpr),
        }

    # ------------------------------------------------------------------
    # Key input
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        """Handle key press event for snip overlay."""
        if event.key() == Qt.Key.Key_Escape:
            self._unhook()
            self.cancelled.emit()
            self.close()

    # ------------------------------------------------------------------
    # Focus / keyboard grab
    # ------------------------------------------------------------------

    def showEvent(self, event):
        """Show event."""
        super().showEvent(event)
        self.focus_for_capture()
        # Defer the grabs: on Windows, grabKeyboard()/grabMouse() are more reliable after the
        # window is truly in the foreground.  A zero-delay timer fires after the
        # current event-loop iteration, by which time the OS has processed Show.
        QTimer.singleShot(0, self.focus_for_capture)
        QTimer.singleShot(75, self.focus_for_capture)

    def focus_for_capture(self) -> None:
        """Make the snip overlay the immediate keyboard and mouse target."""
        if self._closed or not self.isVisible():
            return
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        try:
            self.grabKeyboard()
        except Exception:
            pass
        try:
            self.grabMouse(QCursor(Qt.CursorShape.CrossCursor))
        except Exception:
            pass

    def _unhook(self):
        """Handle unhook for snip overlay."""
        self._closed = True
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        try:
            self.releaseMouse()
        except Exception:
            pass

    def closeEvent(self, event):  # noqa: N802
        """Release native grabs before Qt destroys the overlay."""
        self._unhook()
        super().closeEvent(event)
