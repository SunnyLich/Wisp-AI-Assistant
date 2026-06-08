"""wisp-ui worker: the only process allowed to own PySide6 widgets."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

from macos_py.bootstrap import configure_paths
from macos_py.boundaries import boundary_status
from macos_py import VERSION, protocol


def _protect_stdout():
    real_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real_out


class QtProtocolHost:
    def __init__(self, app, out) -> None:
        from PySide6.QtCore import QTimer

        self._app = app
        self._out = out
        self._write_lock = threading.Lock()
        self._lines: "queue.Queue[bytes | None]" = queue.Queue()
        self._closing = False

        self._overlay_signals = None
        self._overlay = None
        self._intent = None
        self._bubble = None
        self._chat = None
        self._memory = None
        self._memory_viewer = None
        self._all_conversations: list[dict] = []

        self._pump = QTimer()
        self._pump.setInterval(20)
        self._pump.timeout.connect(self._drain)
        self._pump.start()

        self._reader = threading.Thread(target=self._read_loop, name="wisp-ui-stdin", daemon=True)
        self._reader.start()

    def _send(self, obj: dict[str, Any]) -> None:
        with self._write_lock:
            protocol.write_message(self._out, obj)

    def emit(self, event: str, data: Any = None, req_id: Any = None) -> None:
        self._send(protocol.make_event(event, data=data, req_id=req_id))

    def _respond(self, req_id: Any, ok: bool, *, result: Any = None, error: str | None = None) -> None:
        self._send(protocol.make_response(req_id, ok, result=result, error=error))

    def _read_loop(self) -> None:
        stream = sys.stdin.buffer
        while True:
            line = stream.readline()
            if not line:
                self._lines.put(None)
                return
            self._lines.put(line)

    def _drain(self) -> None:
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                return
            if line is None:
                self._closing = True
                self._pump.stop()
                self._app.quit()
                return
            if line.strip():
                self._handle_line(line)

    def _handle_line(self, raw: bytes) -> None:
        req_id = None
        try:
            msg = json.loads(raw.decode("utf-8"))
            req_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "__shutdown__":
                self._respond(req_id, True, result=None)
                self._closing = True
                self._pump.stop()
                self._app.quit()
                return
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            result = self._dispatch(str(method), params)
            self._respond(req_id, True, result=result)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._respond(req_id, False, error=f"{type(exc).__name__}: {exc}")

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method in {"ping", "ui.ping"}:
            return {
                "pong": True,
                "pid": os.getpid(),
                "role": "ui",
                "version": VERSION,
                "repo_root": str(configure_paths()),
                "boundary": boundary_status("ui"),
            }
        if method == "boundary.status":
            return boundary_status("ui")
        if method == "ui.reload_config":
            return self._reload_config()
        if method == "ui.show_overlay":
            return self._show_overlay()
        if method == "ui.show_intent":
            return self._show_intent(**params)
        if method == "ui.reply.reset":
            return self._reply_reset()
        if method == "ui.reply.chunk":
            return self._reply_chunk(**params)
        if method == "ui.reply.done":
            return self._reply_done()
        if method == "ui.show_chat":
            return self._show_chat(force_new=bool(params.get("new", False)))
        if method == "ui.show_settings":
            return self._show_settings()
        if method == "ui.show_memory":
            return self._show_memory()
        if method == "ui.show_plugins":
            return self._show_plugins()
        if method == "ui.show_agent_task":
            return self._show_agent_task()
        if method == "ui.show_agent_history":
            return self._show_agent_history()
        raise ValueError(f"unknown method: {method}")

    def _reload_config(self) -> dict[str, Any]:
        import config

        config.reload()
        try:
            from core.llm_clients import client as llm

            llm.reset_clients()
        except Exception:
            traceback.print_exc()
        try:
            from core import tts

            tts.reset_connections()
        except Exception:
            traceback.print_exc()
        return {"ok": True}

    def _ensure_overlay(self):
        if self._overlay is None:
            from ui.overlay import IconOverlay, OverlaySignals

            self._overlay_signals = OverlaySignals()
            self._overlay = IconOverlay(self._overlay_signals)
        return self._overlay

    def _ensure_bubble(self):
        if self._bubble is None:
            from ui.bubble import SpeechBubble

            self._bubble = SpeechBubble()
        return self._bubble

    def _show_overlay(self) -> dict[str, Any]:
        overlay = self._ensure_overlay()
        overlay.show()
        overlay.raise_()
        return {"shown": True}

    def _show_intent(self, caller_idx: int = 0, target_hwnd: int = 0) -> dict[str, Any]:
        from ui.intent_overlay import IntentOverlay

        if self._intent is not None:
            self._intent.close()
            self._intent = None
        self._intent = IntentOverlay(caller_idx=caller_idx, target_hwnd=target_hwnd)
        self._intent.intent_chosen.connect(
            lambda intent, custom: self.emit(
                "ui.intent.chosen",
                {"caller_idx": caller_idx, "intent": intent, "custom": custom},
            )
        )
        self._intent.cancelled.connect(lambda: self.emit("ui.intent.cancelled", {"caller_idx": caller_idx}))
        self._intent.destroyed.connect(lambda: setattr(self, "_intent", None))
        self._intent.show()
        self._intent.raise_()
        self._intent.activateWindow()
        return {"shown": True, "caller_idx": caller_idx}

    def _reply_reset(self) -> dict[str, Any]:
        bubble = self._ensure_bubble()
        bubble.clear()
        return {"reset": True}

    def _reply_chunk(self, text: str = "", is_thought: bool = False) -> dict[str, Any]:
        bubble = self._ensure_bubble()
        bubble.append_chunk(text, is_thought=is_thought)
        return {"appended": len(text or "")}

    def _reply_done(self) -> dict[str, Any]:
        bubble = self._ensure_bubble()
        bubble.finish()
        return {"done": True}

    def _memory_manager(self):
        if self._memory is None:
            from core.memory_store import store as memory_module

            self._memory = memory_module.get_manager()
        return self._memory

    def _make_chat_send_fn(self):
        memory = self._memory_manager()

        def send_with_memory(messages: list):
            from core.llm_clients import client as llm

            last_user = next(
                (m["content"] for m in reversed(messages) if m.get("role") == "user"),
                "",
            )
            mem_ctx = memory.retrieve_relevant(last_user) if last_user else ""
            return llm.stream_response_with_history(messages, memory_context=mem_ctx)

        return send_with_memory

    def _show_chat(self, force_new: bool = False) -> dict[str, Any]:
        from ui.chat_window import ChatWindow

        if self._chat is not None:
            if force_new:
                self._chat.start_new_conversation()
            self._chat.raise_()
            self._chat.activateWindow()
            return {"shown": True, "reused": True}
        start_new = force_new or not self._all_conversations
        self._chat = ChatWindow(
            conversations=self._all_conversations,
            send_fn=self._make_chat_send_fn(),
            start_new=start_new,
        )
        self._chat.destroyed.connect(lambda: setattr(self, "_chat", None))
        self._chat.show()
        self._chat.raise_()
        self._chat.activateWindow()
        return {"shown": True, "reused": False}

    def _show_settings(self) -> dict[str, Any]:
        from ui.settings import open_settings

        open_settings(parent=None, on_apply=lambda: self._reload_config())
        return {"shown": True}

    def _show_memory(self) -> dict[str, Any]:
        from ui.memory_viewer import MemoryViewer

        if self._memory_viewer is not None and self._memory_viewer.isVisible():
            self._memory_viewer.raise_()
            self._memory_viewer.activateWindow()
            return {"shown": True, "reused": True}
        self._memory_viewer = MemoryViewer(self._memory_manager(), parent=None)
        self._memory_viewer.destroyed.connect(lambda: setattr(self, "_memory_viewer", None))
        self._memory_viewer.show()
        self._memory_viewer.raise_()
        self._memory_viewer.activateWindow()
        return {"shown": True, "reused": False}

    def _show_plugins(self) -> dict[str, Any]:
        from ui.plugin_manager import open_plugin_manager

        open_plugin_manager(parent=None)
        return {"shown": True}

    def _show_agent_task(self) -> dict[str, Any]:
        from ui.agent.task_window import open_agent_task_dialog

        open_agent_task_dialog(parent=None)
        return {"shown": True}

    def _show_agent_history(self) -> dict[str, Any]:
        from ui.agent.task_window import open_agent_history

        open_agent_history(parent=None)
        return {"shown": True}


def main() -> int:
    root = configure_paths()
    os.chdir(root)
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.screen=false")
    os.environ.setdefault("WISP_MACOS_PY_UI_HOST", "1")
    real_out = _protect_stdout()

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Wisp Python UI")
    app.setApplicationDisplayName("Wisp")
    app.setQuitOnLastWindowClosed(False)

    icon_path = Path(root) / "assets" / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    try:
        from ui.shared.theme import apply_app_theme

        apply_app_theme(app)
    except Exception:
        traceback.print_exc()

    host = QtProtocolHost(app, real_out)
    app._wisp_macos_py_ui_host = host
    host.emit("ui.ready", {"repo": str(root)})
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
