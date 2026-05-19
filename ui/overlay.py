"""
ui/overlay.py — Persistent doll overlay widget.

A small, always-on-top, click-through frameless window that lives in the
bottom-right corner. It shows the doll sprite and hosts the system tray icon.

States:
  idle      — static doll, sitting quietly
  listening — hotkey pressed; plays animate_listen()
  thinking  — LLM request in flight
  speaking  — TTS playing; plays animate_speak()
"""
from __future__ import annotations
import os
import glob
import urllib.parse
import config
from core import asset_server as _asset_server
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QSystemTrayIcon, QMenu
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal, QObject
from PyQt6.QtGui import QPixmap, QIcon, QAction, QColor
from doll.animation import DollAnimator

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings

    class _VRMView(QWebEngineView):
        """QWebEngineView that forwards click events to a registered handler."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self._click_handler = None

        def mousePressEvent(self, event):          # noqa: N802
            if self._click_handler:
                self._click_handler(event)
            super().mousePressEvent(event)

    _WEB_ENGINE_AVAILABLE = True
except ImportError:
    _VRMView = None  # type: ignore[assignment]
    _WEB_ENGINE_AVAILABLE = False


_ASSETS_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
_DOLL_ROOT   = os.path.join(os.path.dirname(os.path.dirname(__file__)), "doll")
ASSETS_DIR   = os.path.join(_ASSETS_ROOT, "doll")


class OverlaySignals(QObject):
    """Thread-safe signals for updating the overlay from worker threads."""
    set_state          = pyqtSignal(str)   # "idle" | "listening" | "thinking" | "speaking"
    set_mouth_amp      = pyqtSignal(float)  # 0.0–1.0 amplitude for lip sync
    show_text_popup    = pyqtSignal(str)   # full reply text
    show_intent_picker = pyqtSignal()      # show arrow-key intent chooser
    show_snip_overlay  = pyqtSignal()      # show full-screen region selector
    bubble_listening   = pyqtSignal()      # show mic/recording indicator
    bubble_thinking    = pyqtSignal()      # show animated dots
    bubble_start_reveal = pyqtSignal()     # start word-by-word reveal synced to audio
    bubble_schedule_words = pyqtSignal(list, list)  # (words, start_ms) from Cartesia timestamps
    bubble_chunk       = pyqtSignal(str)   # buffer additional streamed text chunk
    bubble_finish      = pyqtSignal()      # response done, start hide countdown
    bubble_clear       = pyqtSignal()      # hide immediately
    show_doll          = pyqtSignal()      # make doll visible
    hide_doll          = pyqtSignal()      # hide doll after short delay
    settings_applied   = pyqtSignal()      # settings were applied; re-register hotkeys etc.
    show_last_chat     = pyqtSignal()      # tray "Last chat" clicked


class DollOverlay(QMainWindow):
    """
    The persistent doll window. Always on top, no taskbar entry,
    no frame. Positioned at bottom-right corner.
    """

    @property
    def DOLL_SIZE(self):
        if getattr(self, '_use_vrm', False):
            return (config.VRM_WIDTH, config.VRM_HEIGHT)
        s = config.DOLL_SIZE
        return (s, s)

    def __init__(self, signals: OverlaySignals):
        super().__init__()
        self.signals = signals

        # Doll disabled — only the speech bubble is used.
        self._use_vrm = False
        self._no_doll = True

        self._build_window()
        self._build_tray()

        # Speech bubble
        from ui.bubble import SpeechBubble
        self._bubble = SpeechBubble()

        # Connect signals
        signals.set_state.connect(self._on_state_changed)
        signals.set_mouth_amp.connect(self._on_mouth_amp)
        signals.show_text_popup.connect(self._on_show_popup)
        signals.bubble_listening.connect(self._bubble.show_listening)
        signals.bubble_thinking.connect(self._bubble.start_thinking)
        signals.bubble_start_reveal.connect(self._bubble.start_word_reveal)
        signals.bubble_schedule_words.connect(self._bubble.schedule_words)
        signals.bubble_chunk.connect(self._bubble.append_chunk)
        signals.bubble_finish.connect(self._bubble.finish)
        signals.bubble_clear.connect(self._bubble.clear)
        signals.show_doll.connect(self._show_doll)
        signals.hide_doll.connect(self._hide_doll)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _build_window(self):
        # No doll — create a hidden zero-size window just to anchor the tray icon.
        if self._no_doll:
            self.setWindowFlags(Qt.WindowType.Tool)
            self.setFixedSize(0, 0)
            return

    def _build_tray(self):
        icon_path = os.path.join(ASSETS_DIR, "idle.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self._tray = QSystemTrayIcon(icon, self)
        menu = QMenu()

        if self._use_vrm:
            tuner_action = QAction("VRM Tuner…", self)
            tuner_action.triggered.connect(self._open_vrm_tuner)
            menu.addAction(tuner_action)
            menu.addSeparator()

        last_chat_action = QAction("Last chat", self)
        last_chat_action.triggered.connect(self.signals.show_last_chat.emit)
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self._open_settings)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(last_chat_action)
        menu.addSeparator()
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.show()

    # ------------------------------------------------------------------
    # VRM Tuner
    # ------------------------------------------------------------------

    def _open_vrm_tuner(self):
        from doll.vrm_tuner import TunerWindow

        def _on_saved(json_str: str) -> None:
            config_path = os.path.join(_DOLL_ROOT, "vrm_config.json")
            try:
                with open(config_path, "w", encoding="utf-8") as f:
                    f.write(json_str)
                print("[tuner] Saved vrm_config.json")
            except Exception as exc:
                print(f"[tuner] Save error: {exc}")
                return
            # Live-push new config to the overlay without restart
            self._webview.page().runJavaScript(
                f"if(window.applyConfig)applyConfig({json_str})"
            )

        if not hasattr(self, "_tuner_window") or not self._tuner_window.isVisible():
            self._tuner_window = TunerWindow(self._vrm_path, _on_saved,
                                             asset_port=getattr(self, "_asset_port", 0))
        self._tuner_window.show()
        self._tuner_window.raise_()

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _on_state_changed(self, state: str):
        if self._no_doll:
            return
        if self._use_vrm:
            self._webview.page().runJavaScript(f"if(window.setState)setState('{state}')")
        else:
            self._animator.play(state, self._update_pixmap)

    def _on_mouth_amp(self, amp: float):
        if self._use_vrm:
            self._webview.page().runJavaScript(f"if(window.setMouthAmp)window.setMouthAmp({amp:.3f})")

    def _update_pixmap(self, pixmap: QPixmap):
        if not self._use_vrm:
            self._label.setPixmap(pixmap)

    # ------------------------------------------------------------------
    # Popup
    # ------------------------------------------------------------------

    def _on_show_popup(self, text: str):
        from ui.popup import TextPopup
        popup = TextPopup(text, parent=None)
        popup.show()

    def _open_settings(self):
        from ui.settings import open_settings
        open_settings(parent=self, on_apply=self.signals.settings_applied.emit)

    # ------------------------------------------------------------------
    # Doll visibility
    # ------------------------------------------------------------------

    def _show_doll(self):
        pass  # doll disabled

    def _hide_doll(self):
        pass  # doll disabled

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def set_click_handler(self, handler):
        pass  # doll disabled

    def _on_click(self, event):
        pass
