"""wisp-ui worker: the only process allowed to own PySide6 widgets."""

from __future__ import annotations

import json
import itertools
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


class MemoryProxy:
    """Small UI-side cache that forwards memory mutations to the supervisor."""

    def __init__(self, emit_fn) -> None:
        self._emit = emit_fn
        self._facts: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    def replace_facts(self, facts: list[dict[str, Any]] | None) -> None:
        self._facts = [dict(f) for f in (facts or [])]

    def get_all_facts(self) -> list[dict[str, Any]]:
        return [dict(f) for f in self._facts]

    def add_fact_manual(self, text: str, category: str = "general") -> None:
        fact = {
            "id": f"pending-{next(self._ids)}",
            "text": text,
            "category": category or "general",
            "source": "manual",
        }
        self._facts.append(fact)
        self._emit("ui.memory.add", {"text": text, "category": category})

    def update_fact(self, fact_id: str, text: str, category: str | None = None) -> None:
        for fact in self._facts:
            if str(fact.get("id")) == str(fact_id):
                fact["text"] = text
                if category is not None:
                    fact["category"] = category
                break
        self._emit("ui.memory.update", {"id": fact_id, "text": text, "category": category})

    def delete_fact(self, fact_id: str) -> None:
        self._facts = [f for f in self._facts if str(f.get("id")) != str(fact_id)]
        self._emit("ui.memory.delete", {"id": fact_id})


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
        self._snip = None
        self._bubble = None
        self._chat = None
        self._memory = None
        self._memory_viewer = None
        self._plugins_dialog = None
        self._all_conversations: list[dict] = []
        self._chat_request_ids = itertools.count(1)
        self._chat_streams: dict[str, "queue.Queue[tuple[str, Any]]"] = {}
        self._chat_streams_lock = threading.Lock()

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
        if method == "ui.show_snip":
            return self._show_snip()
        if method == "ui.overlay.state":
            return self._overlay_state(**params)
        if method == "ui.reply.reset":
            return self._reply_reset()
        if method == "ui.reply.thinking":
            return self._reply_thinking()
        if method == "ui.reply.listening":
            return self._reply_listening()
        if method == "ui.reply.start_reveal":
            return self._reply_start_reveal()
        if method == "ui.reply.notice":
            return self._reply_notice(**params)
        if method == "ui.reply.chunk":
            return self._reply_chunk(**params)
        if method == "ui.reply.done":
            return self._reply_done()
        if method == "ui.context.clear":
            return self._context_clear()
        if method == "ui.context.summary":
            return self._context_summary(**params)
        if method == "ui.chat.chunk":
            return self._chat_chunk(**params)
        if method == "ui.chat.done":
            return self._chat_done(**params)
        if method == "ui.chat.error":
            return self._chat_error(**params)
        if method == "ui.chat.add_conversation":
            return self._chat_add_conversation(**params)
        if method == "ui.chat.ingest":
            return self._chat_ingest()
        if method == "ui.show_chat":
            return self._show_chat(force_new=bool(params.get("new", False)))
        if method == "ui.show_settings":
            return self._show_settings()
        if method == "ui.show_memory":
            return self._show_memory(**params)
        if method == "ui.show_plugins":
            return self._show_plugins(**params)
        if method == "ui.show_agent_task":
            return self._show_agent_task()
        if method == "ui.show_agent_history":
            return self._show_agent_history()
        if method == "ui.agent.notify_approval":
            return self._agent_notify_approval(**params)
        if method in {"ui.agent.log", "ui.agent.trace", "ui.agent.done", "ui.agent.approval.request"}:
            return {"accepted": True}
        raise ValueError(f"unknown method: {method}")

    def _reload_config(self) -> dict[str, Any]:
        import config

        config.reload()
        try:
            if self._overlay is not None:
                self._overlay.apply_settings()
        except Exception:
            traceback.print_exc()
        try:
            from ui.shared.theme import apply_app_theme

            apply_app_theme(self._app)
        except Exception:
            traceback.print_exc()
        return {"ok": True}

    def _ensure_overlay(self):
        if self._overlay is None:
            from ui.overlay import IconOverlay, OverlaySignals

            self._overlay_signals = OverlaySignals()
            self._overlay = IconOverlay(self._overlay_signals)
            # Keep audio ownership out of the UI process. IconOverlay's default
            # speed callback reaches into core.audio; replace it with a protocol
            # event so the supervisor/audio worker can decide what to do.
            try:
                self._overlay._bubble.set_speed_callback(
                    lambda enabled: self.emit("ui.bubble.speed", {"enabled": bool(enabled)})
                )
            except Exception:
                traceback.print_exc()
            self._overlay_signals.summon_caller.connect(
                lambda idx: self.emit("ui.summon_caller", {"caller_idx": int(idx)})
            )
            self._overlay_signals.show_snip_overlay.connect(
                lambda: self.emit("ui.request_snip", {})
            )
            self._overlay_signals.show_new_chat.connect(lambda: self._show_chat(force_new=True))
            self._overlay_signals.show_last_chat.connect(lambda: self._show_chat(force_new=False))
            self._overlay_signals.show_memory_viewer.connect(
                lambda: self.emit("ui.memory.open_requested", {})
            )
            self._overlay_signals.show_plugin_manager.connect(
                lambda: self.emit("ui.plugins.open_requested", {})
            )
            self._overlay_signals.show_agent_task.connect(
                lambda: self.emit("ui.agent.task_requested", {})
            )
            self._overlay_signals.show_agent_history.connect(
                lambda: self.emit("ui.agent.history_requested", {})
            )
            self._overlay_signals.context_items_dropped.connect(self._context_items_dropped)
            self._overlay_signals.remove_dropped_item.connect(
                lambda idx: self.emit("ui.context.remove", {"index": int(idx)})
            )
            self._overlay_signals.bubble_highlight.connect(self._bubble_highlight)
            self._overlay_signals.settings_applied.connect(self._settings_applied)
        return self._overlay

    def _settings_applied(self) -> None:
        self._reload_config()
        self.emit("ui.settings.applied", {})

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

    def _show_snip(self) -> dict[str, Any]:
        from ui.snip_overlay import SnipOverlay

        if self._snip is not None:
            return {"shown": True, "reused": True}
        self._snip = SnipOverlay()
        self._snip.region_selected.connect(lambda region: self.emit("ui.snip.region", region))
        self._snip.cancelled.connect(lambda: self.emit("ui.snip.cancelled", {}))
        self._snip.destroyed.connect(lambda: setattr(self, "_snip", None))
        self._snip.show()
        return {"shown": True, "reused": False}

    def _overlay_state(self, state: str = "idle") -> dict[str, Any]:
        overlay = self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.set_state.emit(state)
        if state != "idle":
            overlay.show()
            overlay.raise_()
        return {"state": state}

    def _reply_reset(self) -> dict[str, Any]:
        bubble = self._ensure_bubble()
        bubble.clear()
        return {"reset": True}

    def _reply_thinking(self) -> dict[str, Any]:
        self._ensure_bubble().start_thinking()
        return {"thinking": True}

    def _reply_listening(self) -> dict[str, Any]:
        self._ensure_bubble().show_listening()
        return {"listening": True}

    def _reply_start_reveal(self) -> dict[str, Any]:
        self._ensure_bubble().start_word_reveal()
        return {"started": True}

    def _reply_notice(self, text: str = "", timeout_ms: int = 12000) -> dict[str, Any]:
        self._ensure_bubble().show_notice(text, timeout_ms=timeout_ms)
        return {"shown": True, "text": text}

    def _reply_chunk(self, text: str = "", is_thought: bool = False) -> dict[str, Any]:
        bubble = self._ensure_bubble()
        bubble.append_chunk(text, is_thought=is_thought)
        return {"appended": len(text or "")}

    def _reply_done(self) -> dict[str, Any]:
        bubble = self._ensure_bubble()
        bubble.finish()
        return {"done": True}

    @staticmethod
    def _context_item_payload(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return {
                "name": str(item.get("name") or item.get("label") or "Context"),
                "content": item.get("content", ""),
                "type": str(item.get("type") or item.get("item_type") or "text"),
            }
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            return {"name": str(item[0]), "content": item[1], "type": str(item[2])}
        return {"name": "Context", "content": str(item), "type": "text"}

    def _context_items_dropped(self, items: Any) -> None:
        payload = [self._context_item_payload(item) for item in (items or [])]
        self.emit("ui.context.dropped", {"items": payload})

    def _context_clear(self) -> dict[str, Any]:
        overlay = self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.drop_context_cleared.emit()
        return {"cleared": True, "overlay": bool(overlay)}

    def _context_summary(self, items: list | None = None) -> dict[str, Any]:
        self._ensure_overlay()
        if self._overlay_signals is not None:
            pairs = [
                (str(item.get("label") or item.get("name") or "Context"), str(item.get("type") or "text"))
                for item in (items or [])
                if isinstance(item, dict)
            ]
            self._overlay_signals.show_context_summary.emit(pairs)
        return {"shown": len(items or [])}

    def _bubble_highlight(self, text: str, revealed_count: int, finished: bool) -> None:
        if self._chat is not None:
            self._chat.update_live_highlight(text, int(revealed_count), bool(finished))
        self.emit(
            "ui.bubble.highlight",
            {"text": text, "revealed_count": int(revealed_count), "finished": bool(finished)},
        )

    def _memory_manager(self):
        if self._memory is None:
            self._memory = MemoryProxy(self.emit)
        return self._memory

    def _make_chat_send_fn(self):
        def send_with_memory(messages: list):
            request_id = f"chat-{next(self._chat_request_ids)}"
            stream: "queue.Queue[tuple[str, Any]]" = queue.Queue()
            with self._chat_streams_lock:
                self._chat_streams[request_id] = stream
            self.emit("ui.chat.request", {"request_id": request_id, "messages": messages})
            try:
                while True:
                    kind, payload = stream.get()
                    if kind == "chunk":
                        yield str(payload or "")
                    elif kind == "done":
                        return
                    elif kind == "error":
                        raise RuntimeError(str(payload or "chat failed"))
            finally:
                with self._chat_streams_lock:
                    self._chat_streams.pop(request_id, None)

        return send_with_memory

    def _chat_stream(self, request_id: str):
        with self._chat_streams_lock:
            return self._chat_streams.get(str(request_id))

    def _chat_chunk(self, request_id: str = "", text: str = "") -> dict[str, Any]:
        stream = self._chat_stream(request_id)
        if stream is not None:
            stream.put(("chunk", text))
        return {"queued": stream is not None}

    def _chat_done(self, request_id: str = "") -> dict[str, Any]:
        stream = self._chat_stream(request_id)
        if stream is not None:
            stream.put(("done", None))
        return {"queued": stream is not None}

    def _chat_error(self, request_id: str = "", error: str = "") -> dict[str, Any]:
        stream = self._chat_stream(request_id)
        if stream is not None:
            stream.put(("error", error))
        return {"queued": stream is not None}

    def _chat_add_conversation(
        self,
        user: str = "",
        assistant: str = "",
        context: str = "",
        image_base64: str | None = None,
    ) -> dict[str, Any]:
        user_msg: dict[str, Any] = {"role": "user", "content": user}
        if image_base64:
            user_msg["image_base64"] = image_base64
        self._all_conversations.append(
            {
                "messages": [
                    user_msg,
                    {"role": "assistant", "content": assistant},
                ],
                "context": context or "",
            }
        )
        if self._chat is not None:
            self._chat.ingest_new_conversations()
        return {"count": len(self._all_conversations)}

    def _chat_ingest(self) -> dict[str, Any]:
        if self._chat is not None:
            self._chat.ingest_new_conversations()
            return {"ingested": True}
        return {"ingested": False}

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

        open_settings(parent=None, on_apply=self._settings_applied)
        return {"shown": True}

    def _show_memory(self, facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        from ui.memory_viewer import MemoryViewer

        manager = self._memory_manager()
        if hasattr(manager, "replace_facts"):
            manager.replace_facts(facts or [])
        if self._memory_viewer is not None and self._memory_viewer.isVisible():
            self._memory_viewer.raise_()
            self._memory_viewer.activateWindow()
            return {"shown": True, "reused": True}
        self._memory_viewer = MemoryViewer(manager, parent=None)
        self._memory_viewer.destroyed.connect(lambda: setattr(self, "_memory_viewer", None))
        self._memory_viewer.show()
        self._memory_viewer.raise_()
        self._memory_viewer.activateWindow()
        return {"shown": True, "reused": False}

    def _show_plugins(
        self,
        plugins: list[dict[str, Any]] | None = None,
        plugins_dir: str = "",
    ) -> dict[str, Any]:
        from PySide6.QtCore import Qt, QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import (
            QDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QScrollArea,
            QSizePolicy,
            QVBoxLayout,
            QWidget,
        )

        if self._plugins_dialog is not None and self._plugins_dialog.isVisible():
            self._plugins_dialog.raise_()
            self._plugins_dialog.activateWindow()
            return {"shown": True, "reused": True}

        dialog = QDialog()
        dialog.setWindowTitle("Plugin Manager")
        dialog.setModal(False)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("Plugins")
        title.setStyleSheet("font-size: 15pt; font-weight: 700;")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        plugin_rows = plugins or []
        if not plugin_rows:
            empty = QLabel("No plugins found.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("opacity: 0.55; font-size: 10pt;")
            inner_layout.addWidget(empty)
        else:
            for plugin in plugin_rows:
                inner_layout.addWidget(self._plugin_card(plugin))

        inner_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        if plugins_dir:
            open_btn = QPushButton("Open plugins folder")
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(plugins_dir)))
            footer.addWidget(open_btn)
        footer.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        dialog.resize(520, 420)
        self._plugins_dialog = dialog
        self._plugins_dialog.destroyed.connect(lambda: setattr(self, "_plugins_dialog", None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return {"shown": True, "reused": False}

    def _plugin_card(self, plugin: dict[str, Any]):
        from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout

        card = QFrame()
        card.setObjectName("pluginCard")
        card.setStyleSheet(
            "QFrame#pluginCard { border: 1px solid rgba(128,128,128,0.25); "
            "border-radius: 8px; padding: 2px; }"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        name_row = QHBoxLayout()
        name = str(plugin.get("name") or "Plugin")
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-size: 11pt; font-weight: 600;")
        name_row.addWidget(name_lbl)
        name_row.addStretch()
        status = QLabel(str(plugin.get("status") or "unknown"))
        status.setStyleSheet("font-size: 8pt; opacity: 0.55;")
        name_row.addWidget(status)
        layout.addLayout(name_row)

        path = str(plugin.get("path") or "")
        if path:
            path_lbl = QLabel(path)
            path_lbl.setStyleSheet("font-size: 8pt; opacity: 0.45;")
            layout.addWidget(path_lbl)

        hooks = plugin.get("hooks") or []
        tools = plugin.get("tools") or []
        details = []
        if hooks:
            details.append("Hooks: " + ", ".join(str(h) for h in hooks))
        if tools:
            details.append("Tools: " + ", ".join(str(t) for t in tools))
        if details:
            detail_lbl = QLabel("\n".join(details))
            detail_lbl.setWordWrap(True)
            detail_lbl.setStyleSheet("font-size: 8pt; opacity: 0.65;")
            layout.addWidget(detail_lbl)

        actions = plugin.get("tray_actions") or []
        if actions:
            action_row = QHBoxLayout()
            action_row.setSpacing(6)
            for label in actions:
                text = str(label)
                btn = QPushButton(text)
                btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
                btn.clicked.connect(
                    lambda _checked=False, plugin_name=name, action_label=text: self.emit(
                        "ui.plugins.run_action",
                        {"plugin_name": plugin_name, "label": action_label},
                    )
                )
                action_row.addWidget(btn)
            action_row.addStretch()
            layout.addLayout(action_row)

        error = str(plugin.get("error") or "")
        if error:
            error_lbl = QLabel(error)
            error_lbl.setWordWrap(True)
            error_lbl.setStyleSheet("font-size: 8pt; color: #b42318;")
            layout.addWidget(error_lbl)
        return card

    def _show_agent_task(self) -> dict[str, Any]:
        from ui.agent.task_window import open_agent_task_dialog

        open_agent_task_dialog(parent=None)
        return {"shown": True}

    def _show_agent_history(self) -> dict[str, Any]:
        from ui.agent.task_window import open_agent_history

        open_agent_history(parent=None)
        return {"shown": True}

    def _agent_notify_approval(
        self,
        text: str = "",
        resolved: bool = False,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        overlay = self._ensure_overlay()
        notify = getattr(overlay, "notify_agent_approval", None)
        if callable(notify):
            notify(text or "Agent approval requested.", resolved=bool(resolved))
        return {"shown": bool(callable(notify)), "data": data or {}}


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
