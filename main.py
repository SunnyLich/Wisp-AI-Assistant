"""
main.py — Entry point for the AI Assistant Overlay.

Flow:
  Ctrl+E → IntentOverlay appears (WASD picker)
  WASD key → capture input → LLM stream → TTS stream → audio out
  Click doll → show full reply popup
"""
import sys
import threading
from PyQt6.QtWidgets import QApplication
import config
from core.hotkeys import HotkeyListener
from core import capture, llm, audio
from core import tts as tts_module
from ui.overlay import DollOverlay, OverlaySignals
from ui.intent_overlay import IntentOverlay
from ui.chat_window import ChatWindow


class App:
    def __init__(self):
        self._qt = QApplication(sys.argv)
        self._signals = OverlaySignals()
        self._overlay = DollOverlay(self._signals)
        self._hotkeys = HotkeyListener(
            on_invoke=self._on_hotkey,
            on_add_context=self._on_add_context,
            on_clear_context=self._on_clear_context,
        )
        self._context_buffer: list[str] = []   # accumulated via Alt+Q
        self._last_reply: str = ""
        self._intent_picker: IntentOverlay | None = None
        self._chat_window: ChatWindow | None = None
        self._conversation_history: list = []   # [{"role": ..., "content": ...}]
        self._overlay_hwnd: int = 0          # cached after first show
        self._pending_capture: tuple | None = None  # (selected_text, screenshot_b64)

        # Wire signals
        self._signals.show_intent_picker.connect(self._show_intent_picker)
        self._overlay.set_click_handler(self._on_doll_click)

        # Pre-warm connections in background
        threading.Thread(target=self._prewarm, daemon=True).start()

    def run(self):
        if not config.DOLL_AUTO_HIDE:
            self._overlay.show()
        self._overlay_hwnd = int(self._overlay.winId())  # safe: Qt main thread
        self._hotkeys.start()
        print("[main] AI Assistant Overlay running. Press Ctrl+Q to invoke.")
        sys.exit(self._qt.exec())

    # ------------------------------------------------------------------
    # Pre-warm
    # ------------------------------------------------------------------

    def _prewarm(self):
        tts_module.prewarm()
        print("[main] Connections pre-warmed.")

    # ------------------------------------------------------------------
    # Hotkey → show intent picker (runs in keyboard listener thread)
    # ------------------------------------------------------------------

    def _on_add_context(self):
        """Alt+Q — capture selected text and append to context buffer."""
        text = capture.get_selected_text()
        if text:
            self._context_buffer.append(text)
            print(f"[main] Context buffer: {len(self._context_buffer)} item(s) queued.")

    def _on_clear_context(self):
        """Alt+W — clear the context buffer."""
        self._context_buffer.clear()
        print("[main] Context buffer cleared.")

    def _on_hotkey(self):
        # 1. Capture FIRST — original app still has focus, so Ctrl+C works.
        selected = capture.get_selected_text()
        screenshot_b64 = None
        if not selected:
            img = capture.get_screen_snippet()
            screenshot_b64 = capture.image_to_base64(img)
        self._pending_capture = (selected, screenshot_b64)

        # 2. Now steal foreground using the pre-cached HWND (thread-safe).
        if self._overlay_hwnd:
            try:
                import ctypes
                ctypes.windll.user32.SetForegroundWindow(self._overlay_hwnd)
            except Exception:
                pass

        audio.play_filler()
        if config.DOLL_AUTO_HIDE:
            self._signals.show_doll.emit()
        self._signals.set_state.emit("listening")
        self._signals.show_intent_picker.emit()

    # ------------------------------------------------------------------
    # Intent picker (runs on Qt main thread via signal)
    # ------------------------------------------------------------------

    def _show_intent_picker(self):
        if self._intent_picker is not None:
            return  # already showing

        self._intent_picker = IntentOverlay()
        self._intent_picker.intent_chosen.connect(self._on_intent_chosen)
        self._intent_picker.cancelled.connect(self._on_intent_cancelled)
        self._intent_picker.show()

    def _on_intent_chosen(self, direction: str, prompt: str):
        self._intent_picker = None
        capture_data = self._pending_capture
        self._pending_capture = None
        threading.Thread(
            target=self._query_and_speak,
            args=(prompt, capture_data),
            daemon=True,
        ).start()

    def _on_intent_cancelled(self):
        self._intent_picker = None
        self._signals.set_state.emit("idle")
        if config.DOLL_AUTO_HIDE:
            self._signals.hide_doll.emit()

    # ------------------------------------------------------------------
    # LLM + TTS pipeline (worker thread)
    # ------------------------------------------------------------------

    def _query_and_speak(self, intent_prompt: str, capture_data: tuple | None):
        import queue

        # Use pre-captured input from hotkey time (original app still had focus then).
        if capture_data:
            selected, screenshot_b64 = capture_data
        else:
            selected = capture.get_selected_text()
            screenshot_b64 = None
            if not selected:
                img = capture.get_screen_snippet()
                screenshot_b64 = capture.image_to_base64(img)

        # Build final message, incorporating any buffered context items
        context_items = self._context_buffer.copy()
        self._context_buffer.clear()

        all_contexts = context_items + ([selected] if selected else [])
        if len(all_contexts) > 1:
            labelled = "\n\n".join(
                f"Context {i + 1}:\n{ctx}" for i, ctx in enumerate(all_contexts)
            )
            user_message = f"{intent_prompt}\n\n{labelled}"
        elif all_contexts:
            user_message = f"{intent_prompt}\n\n{all_contexts[0]}"
        else:
            user_message = intent_prompt

        self._signals.set_state.emit("thinking")
        self._signals.bubble_thinking.emit()

        # Reset conversation for a fresh query (new hotkey invocation)
        self._conversation_history = []
        full_text = ""
        llm_chunk_q: queue.Queue[str | None] = queue.Queue()

        def llm_producer():
            nonlocal full_text
            try:
                for chunk in llm.stream_response(user_message, screenshot_b64):
                    full_text += chunk
                    llm_chunk_q.put(chunk)
                    self._signals.bubble_chunk.emit(chunk)   # buffered by bubble in reveal mode
            finally:
                llm_chunk_q.put(None)
            # Populate history as soon as the LLM is done (before TTS finishes)
            # so the chat window always has full context when opened.
            self._conversation_history = [
                {"role": "user",      "content": user_message},
                {"role": "assistant", "content": full_text},
            ]

        def llm_chunk_iter():
            while True:
                chunk = llm_chunk_q.get()
                if chunk is None:
                    return
                yield chunk

        def on_audio_start():
            # Called when first PCM chunk hits the speaker — start word reveal.
            self._signals.bubble_start_reveal.emit()

        def on_word_timestamps(words, start_ms):
            # Real word timings from Cartesia — drives precise bubble sync.
            self._signals.bubble_schedule_words.emit(words, start_ms)

        def tts_consumer():
            self._signals.set_state.emit("speaking")
            audio.play_tts_stream_from_chunks(
                llm_chunk_iter(),
                on_done=lambda: (
                    setattr(self, "_last_reply", full_text),
                    self._signals.set_state.emit("idle"),
                    self._signals.bubble_finish.emit(),
                    self._signals.hide_doll.emit() if config.DOLL_AUTO_HIDE else None,
                ),
                on_audio_start=on_audio_start,
                on_word_timestamps=on_word_timestamps,
            )

        threading.Thread(target=llm_producer, daemon=True).start()
        threading.Thread(target=tts_consumer, daemon=True).start()

    # ------------------------------------------------------------------
    # Doll click → show popup
    # ------------------------------------------------------------------

    def _on_doll_click(self, event):
        auto_msg = config.CHAT_ELABORATE_PROMPT if config.CHAT_AUTO_ELABORATE and self._conversation_history else None
        if self._chat_window is not None:
            self._chat_window.raise_()
            self._chat_window.activateWindow()
            return
        self._chat_window = ChatWindow(
            history=self._conversation_history,
            send_fn=llm.stream_response_with_history,
            auto_message=auto_msg,
        )
        self._chat_window.destroyed.connect(lambda: (
            setattr(self, "_chat_window", None),
            self._qt.quit(),
        ))
        self._chat_window.show()


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
