#!/usr/bin/env python3
"""macOS Qt UI host for reusable Wisp product windows.

Swift owns the fragile macOS-native surfaces: tray, hotkeys, overlay, capture,
audio, Accessibility prompts. This process only opens normal Qt windows that are
already shared with the Windows app: Settings, Chat, Memory, Plugin Manager, and
Agent task views.

Protocol: newline-delimited JSON on stdin/stdout.

    {"method": "ui.show_settings", "params": {}}
    {"event": "ui.ok", "method": "ui.show_settings"}

The host intentionally does not import main.py. Importing the full app would
start a second tray/overlay/hotkey/audio stack and reintroduce the Cocoa crash
surface that the Swift shell is replacing.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import traceback
from pathlib import Path


def _resolve_repo_root() -> Path:
    env_root = os.environ.get("WISP_REPO_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


REPO_ROOT = _resolve_repo_root()
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.screen=false")
os.environ.setdefault("WISP_QT_UI_HOST", "1")


def _setup_logging() -> logging.Logger:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    log_dir = os.environ.get("WISP_RUN_LOG_DIR")
    if log_dir:
        try:
            path = Path(log_dir).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(path / "qt-ui-host.log", encoding="utf-8"))
        except Exception:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    return logging.getLogger("wisp.qt_ui_host")


log = _setup_logging()


def _write_event(event: str, **payload) -> None:
    msg = {"event": event, **payload}
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _short_trace() -> str:
    return "".join(traceback.format_exc(limit=8))


def _reload_runtime() -> None:
    """Reload config and cached clients after Settings writes .env."""
    import config

    config.reload()
    try:
        from core.llm_clients import client as llm

        llm.reset_clients()
    except Exception:
        log.exception("Could not reset LLM clients after settings apply")
    try:
        from core import tts

        tts.reset_connections()
    except Exception:
        log.exception("Could not reset TTS clients after settings apply")


class QtUIHost:
    def __init__(self, app):
        from PySide6.QtCore import QTimer

        self._app = app
        self._closing = False

        self._memory = None
        self._memory_viewer = None
        self._chat_window = None
        self._all_conversations: list[dict] = []

        # A blocking read on stdin can't run on the Qt main thread, and
        # QSocketNotifier on a pipe fd is POSIX-only (it never fires on Windows).
        # So a daemon thread does the blocking line reads and hands each complete
        # line to a main-thread QTimer, which keeps all UI work on the main thread
        # (see the macOS native-on-main-thread rule). Mirrors the brain host's
        # portable read loop, so the protocol is testable on any OS.
        self._lines: "queue.Queue[bytes | None]" = queue.Queue()
        self._pump = QTimer()
        self._pump.setInterval(20)
        self._pump.timeout.connect(self._drain)
        self._pump.start()

        self._reader = threading.Thread(
            target=self._read_loop, name="wisp-ui-stdin", daemon=True
        )
        self._reader.start()

    def _read_loop(self) -> None:
        stream = sys.stdin.buffer
        try:
            while True:
                line = stream.readline()
                if not line:  # EOF: parent closed the pipe
                    self._lines.put(None)
                    return
                self._lines.put(line)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("stdin reader thread failed")
            _write_event("ui.error", error=str(exc), traceback=_short_trace())
            self._lines.put(None)

    def _drain(self) -> None:
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                return
            if line is None:
                log.info("stdin closed; quitting Qt UI host")
                self._closing = True
                self._pump.stop()
                self._app.quit()
                return
            if line.strip():
                self._handle_line(line)

    def _handle_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8"))
            method = msg.get("method")
            params = msg.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            self._dispatch(method, params)
            _write_event("ui.ok", method=method)
        except Exception as exc:
            log.exception("command failed: %r", line)
            _write_event("ui.error", error=str(exc), traceback=_short_trace())

    def _dispatch(self, method: str, params: dict) -> None:
        log.info("command: %s", method)
        if method == "__shutdown__":
            self._closing = True
            self._pump.stop()
            self._app.quit()
            return
        if method == "ui.show_settings":
            self._show_settings()
            return
        if method == "ui.show_chat":
            self._show_chat(force_new=bool(params.get("new", False)))
            return
        if method == "ui.show_memory":
            self._show_memory()
            return
        if method == "ui.show_plugin_manager":
            self._show_plugin_manager()
            return
        if method == "ui.show_agent_task":
            self._show_agent_task()
            return
        if method == "ui.show_agent_history":
            self._show_agent_history()
            return
        if method == "ui.reload_config":
            _reload_runtime()
            return
        raise ValueError(f"unknown method: {method}")

    def _settings_applied(self) -> None:
        _reload_runtime()
        log.info("settings applied and runtime reloaded")

    def _show_settings(self) -> None:
        from ui.settings import open_settings

        open_settings(parent=None, on_apply=self._settings_applied)

    def _show_plugin_manager(self) -> None:
        from ui.plugin_manager import open_plugin_manager

        open_plugin_manager(parent=None)

    def _memory_manager(self):
        if self._memory is None:
            from core.memory_store import store as memory_module

            self._memory = memory_module.get_manager()
        return self._memory

    def _show_memory(self) -> None:
        from ui.memory_viewer import MemoryViewer

        if self._memory_viewer is not None and self._memory_viewer.isVisible():
            self._memory_viewer.raise_()
            self._memory_viewer.activateWindow()
            return
        self._memory_viewer = MemoryViewer(self._memory_manager(), parent=None)
        self._memory_viewer.destroyed.connect(lambda: setattr(self, "_memory_viewer", None))
        self._memory_viewer.show()
        self._memory_viewer.raise_()
        self._memory_viewer.activateWindow()

    def _make_chat_send_fn(self):
        memory = self._memory_manager()

        def send_with_memory(messages: list):
            # Mirrors main.py._make_memory_send_fn. Config is refreshed via the
            # ui.reload_config path after Settings applies, not per chat turn.
            from core.llm_clients import client as llm

            last_user = next(
                (m["content"] for m in reversed(messages) if m.get("role") == "user"),
                "",
            )
            mem_ctx = memory.retrieve_relevant(last_user) if last_user else ""
            return llm.stream_response_with_history(messages, memory_context=mem_ctx)

        return send_with_memory

    def _show_chat(self, force_new: bool = False) -> None:
        from ui.chat_window import ChatWindow

        if self._chat_window is not None:
            if force_new:
                self._chat_window.start_new_conversation()
            self._chat_window.raise_()
            self._chat_window.activateWindow()
            return

        start_new = force_new or not self._all_conversations
        self._chat_window = ChatWindow(
            conversations=self._all_conversations,
            send_fn=self._make_chat_send_fn(),
            start_new=start_new,
        )
        self._chat_window.destroyed.connect(lambda: setattr(self, "_chat_window", None))
        self._chat_window.show()
        self._chat_window.raise_()
        self._chat_window.activateWindow()

    def _show_agent_task(self) -> None:
        from ui.agent.task_window import open_agent_task_dialog

        open_agent_task_dialog(parent=None)

    def _show_agent_history(self) -> None:
        from ui.agent.task_window import open_agent_history

        open_agent_history(parent=None)


def main() -> int:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Wisp Qt UI")
    app.setApplicationDisplayName("Wisp")
    app.setQuitOnLastWindowClosed(False)

    icon_path = REPO_ROOT / "assets" / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    try:
        from ui.shared.theme import apply_app_theme

        apply_app_theme(app)
    except Exception:
        log.exception("Could not apply Qt app theme")

    host = QtUIHost(app)
    app._wisp_qt_ui_host = host  # keep the stdin listener alive for app.exec()
    log.info("Qt UI host ready; repo=%s", REPO_ROOT)
    _write_event("ui.ready", repo=str(REPO_ROOT))
    exit_code = app.exec()

    # Hard-exit once the event loop returns. The daemon stdin reader can still be
    # blocked in readline(), and letting the interpreter finalize around a thread
    # stuck in a C syscall segfaults on teardown (seen as 0xC0000005 on Windows).
    # Output is flushed per event already, so there is nothing to lose.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code if isinstance(exit_code, int) else 0)


if __name__ == "__main__":
    main()
