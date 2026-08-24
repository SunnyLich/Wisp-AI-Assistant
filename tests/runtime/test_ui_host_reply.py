from __future__ import annotations

import logging


def test_overlay_amplitude_is_clamped_and_emitted() -> None:
    from runtime.workers.ui_host import QtProtocolHost

    values: list[float] = []

    class Emitter:
        def emit(self, value: float) -> None:
            values.append(value)

    class Signals:
        set_mouth_amp = Emitter()

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._overlay_signals = Signals()
    host._ensure_overlay = lambda: object()  # type: ignore[method-assign]

    assert host._overlay_amplitude(4.2) == {"amplitude": 1.0}
    assert host._overlay_amplitude(-0.5) == {"amplitude": 0.0}
    assert values == [1.0, 0.0]


def test_suppressed_prompt_presentation_skips_only_reply_bubble() -> None:
    from runtime.workers.ui_host import QtProtocolHost

    class Bubble:
        def __init__(self) -> None:
            self.hidden = 0
            self.cleared = 0

        def hide(self) -> None:
            self.hidden += 1

        def clear(self) -> None:
            self.cleared += 1

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = Bubble()
    host._bubble = bubble
    host._ensure_bubble = lambda: bubble  # type: ignore[method-assign]

    assert host._reply_presentation(suppress_bubble=True)["suppress_bubble"] is True
    assert host._reply_reset()["suppressed"] is True
    assert bubble.hidden == 1
    assert bubble.cleared == 0
    assert host._reply_thinking()["suppressed"] is True
    assert host._reply_chunk(text="answer")["suppressed"] is True
    assert host._reply_image(attachments=[])["suppressed"] is True
    assert host._reply_done()["suppressed"] is True

    host._reply_presentation(suppress_bubble=False)
    assert host._reply_reset() == {"reset": True}
    assert bubble.cleared == 1


def test_begin_overlay_conversation_targets_the_persisted_chat() -> None:
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    host._persist_conversations = lambda: None  # type: ignore[method-assign]

    result = host._chat_begin_conversation(user="show me the answer")

    assert result["conversation_index"] == 0
    assert host._external_reply_stream["conversation_index"] == 0


def test_rewrite_anchor_refreshes_emit_one_summary_on_remove(caplog) -> None:
    from runtime.workers.ui_host import QtProtocolHost

    class Popup:
        state = "proposal"
        removed = False
        visible = True
        left = 400
        top = 300

        def update_selection_anchor(self, selection_rect, *, visible=True) -> None:
            self.visible = bool(visible)
            if selection_rect:
                self.left = int(selection_rect["left"]) + int(selection_rect["width"]) + 10
                self.top = int(selection_rect["top"]) - 12

        def isVisible(self) -> bool:
            return self.visible

        def x(self) -> int:
            return self.left

        def y(self) -> int:
            return self.top

        @staticmethod
        def width() -> int:
            return 390

        @staticmethod
        def height() -> int:
            return 240

        def remove(self) -> None:
            self.removed = True

    host = object.__new__(QtProtocolHost)
    popup = Popup()
    initial = {"left": 100, "top": 120, "width": 40, "height": 20}
    moved = {"left": 100, "top": 180, "width": 40, "height": 20}
    host._rewrite_annotations = {"anchor-1": popup}
    host._rewrite_anchor_stats = {
        "anchor-1": {
            "started_at": 1.0,
            "refreshes": 0,
            "visible_samples": 0,
            "hidden_samples": 0,
            "position_changes": 0,
            "visibility_changes": 0,
            "source_counts": {},
            "last_rect": host._rewrite_anchor_rect_key(initial),
            "last_visible": True,
        }
    }

    with caplog.at_level(logging.INFO, logger="openwand.ui_host"):
        host._rewrite_annotation_anchor("anchor-1", initial, True, "uia")
        host._rewrite_annotation_anchor("anchor-1", moved, True, "uia")
        host._rewrite_annotation_anchor("anchor-1", None, False, "uia")
        assert not [record for record in caplog.records if "rewrite anchor" in record.message]
        removed = host._rewrite_annotation_remove("anchor-1")

    summaries = [record.message for record in caplog.records if "rewrite anchor summary" in record.message]
    assert removed["removed"] is True
    assert popup.removed is True
    assert len(summaries) == 1
    assert "refreshes=3" in summaries[0]
    assert "visible=2 hidden=1" in summaries[0]
    assert "position_changes=1" in summaries[0]
    assert "visibility_changes=1" in summaries[0]
    assert "sources=uia:3" in summaries[0]
    assert "final_state=proposal" in summaries[0]


def test_wayland_desktop_uses_xwayland_for_positioned_overlay() -> None:
    """The floating overlay needs global coordinates, unlike native capture."""
    from runtime.workers import ui_host

    environment = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}

    assert ui_host._configure_linux_ui_platform(environment, platform="linux") == "xcb"
    assert environment["QT_QPA_PLATFORM"] == "xcb"


def test_ui_platform_respects_native_wayland_opt_in() -> None:
    """Users can retain the Qt Wayland backend explicitly."""
    from runtime.workers import ui_host

    environment = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "OPENWAND_UI_PLATFORM": "wayland"}

    assert ui_host._configure_linux_ui_platform(environment, platform="linux") == ""
    assert "QT_QPA_PLATFORM" not in environment


def test_context_source_labels_translate_without_touching_custom_labels(monkeypatch) -> None:
    """Verify built-in context badge labels are localized but user labels remain."""
    from runtime.workers import ui_host

    monkeypatch.setattr(ui_host, "t", lambda text: f"tx:{text}")

    assert ui_host._context_display_label("App") == "tx:App"
    assert ui_host._context_display_label("Browser/Web") == "tx:Browser/Web"
    assert ui_host._context_display_label("notes.txt") == "notes.txt"


def test_health_text_translates_nested_messages_and_values(monkeypatch) -> None:
    """Verify health text translation handles composed messages and value atoms."""
    from runtime.workers import ui_host

    translations = {
        "LLM test failed: {message}": "LLM \u6e2c\u8a66\u5931\u6557\uff1a{message}",
        "LLM route uses {provider} but you are not logged in.": "LLM \u8def\u7531\u4f7f\u7528 {provider}\uff0c\u4f46\u4f60\u5c1a\u672a\u767b\u5165\u3002",
        "Microphone permission: {value}.": "\u9ea5\u514b\u98a8\u6b0a\u9650\uff1a{value}\u3002",
        "unavailable": "\u7121\u6cd5\u4f7f\u7528",
        "Speech to text": "\u8a9e\u97f3\u8f49\u6587\u5b57",
        "STT model configured: {model}, but STT verification failed: {error}": "\u5df2\u8a2d\u5b9a STT \u6a21\u578b\uff1a{model}\uff0c\u4f46 STT \u9a57\u8b49\u5931\u6557\uff1a{error}",
        "STT packages and runtime are verified for {model}; the model loads on first use.": "\u5df2\u9a57\u8b49 {model} \u7684 STT \u5957\u4ef6\u8207\u57f7\u884c\u74b0\u5883\uff1b\u6a21\u578b\u6703\u5728\u9996\u6b21\u4f7f\u7528\u6642\u8f09\u5165\u3002",
        "Windows CUDA runtime is incomplete or unloadable: {files}": "Windows CUDA \u57f7\u884c\u74b0\u5883\u4e0d\u5b8c\u6574\u6216\u7121\u6cd5\u8f09\u5165\uff1a{files}",
        "Health issue: {name}: {message}": "\u5065\u5eb7\u72c0\u614b\u554f\u984c\uff1a{name}\uff1a{message}",
    }
    monkeypatch.setattr(ui_host, "t", lambda text: translations.get(text, text))

    assert ui_host._translate_health_text(
        "LLM test failed: LLM route uses chatgpt but you are not logged in."
    ) == "LLM \u6e2c\u8a66\u5931\u6557\uff1aLLM \u8def\u7531\u4f7f\u7528 chatgpt\uff0c\u4f46\u4f60\u5c1a\u672a\u767b\u5165\u3002"
    assert (
        ui_host._translate_health_text("Microphone permission: unavailable.")
        == "\u9ea5\u514b\u98a8\u6b0a\u9650\uff1a\u7121\u6cd5\u4f7f\u7528\u3002"
    )
    failure = (
        "STT model configured: base, but STT verification failed: "
        "Windows CUDA runtime is incomplete or unloadable: cublas64_12.dll"
    )
    assert ui_host._translate_health_text(failure) == (
        "\u5df2\u8a2d\u5b9a STT \u6a21\u578b\uff1abase\uff0c\u4f46 STT \u9a57\u8b49\u5931\u6557\uff1a"
        "Windows CUDA \u57f7\u884c\u74b0\u5883\u4e0d\u5b8c\u6574\u6216\u7121\u6cd5\u8f09\u5165\uff1acublas64_12.dll"
    )
    assert ui_host._translate_health_text(
        "STT packages and runtime are verified for base; the model loads on first use."
    ) == "\u5df2\u9a57\u8b49 base \u7684 STT \u5957\u4ef6\u8207\u57f7\u884c\u74b0\u5883\uff1b\u6a21\u578b\u6703\u5728\u9996\u6b21\u4f7f\u7528\u6642\u8f09\u5165\u3002"
    assert ui_host._translate_notice_text(f"Health issue: Speech to text: {failure}") == (
        "\u5065\u5eb7\u72c0\u614b\u554f\u984c\uff1a\u8a9e\u97f3\u8f49\u6587\u5b57\uff1a"
        "\u5df2\u8a2d\u5b9a STT \u6a21\u578b\uff1abase\uff0c\u4f46 STT \u9a57\u8b49\u5931\u6557\uff1a"
        "Windows CUDA \u57f7\u884c\u74b0\u5883\u4e0d\u5b8c\u6574\u6216\u7121\u6cd5\u8f09\u5165\uff1acublas64_12.dll"
    )


def test_notice_text_translates_known_bubble_messages(monkeypatch) -> None:
    """Verify system bubble notices translate known lines while preserving layout."""
    from runtime.workers import ui_host

    translations = {
        "Addon folder installed.": "\u5916\u639b\u8cc7\u6599\u593e\u5df2\u5b89\u88dd\u3002",
        "Recommendation: open Addon Manager, inspect the addon diagnostics, then repair or disable it.": "\u5efa\u8b70\uff1a\u958b\u555f\u5916\u639b\u7ba1\u7406\u5668\uff0c\u6aa2\u67e5\u5916\u639b\u8a3a\u65b7\u8cc7\u8a0a\uff0c\u7136\u5f8c\u4fee\u5fa9\u6216\u505c\u7528\u5b83\u3002",
        "Preparing local voice... {detail}": "\u6b63\u5728\u6e96\u5099\u672c\u6a5f\u8a9e\u97f3... {detail}",
        "Technical detail: ": "\u6280\u8853\u7d30\u7bc0\uff1a",
    }
    monkeypatch.setattr(ui_host, "t", lambda text: translations.get(text, text))

    assert ui_host._translate_notice_text(
        "Addon folder installed.\n\n"
        "Recommendation: open Addon Manager, inspect the addon diagnostics, then repair or disable it.\n"
        "Technical detail: addon.json missing"
    ) == (
        "\u5916\u639b\u8cc7\u6599\u593e\u5df2\u5b89\u88dd\u3002\n\n"
        "\u5efa\u8b70\uff1a\u958b\u555f\u5916\u639b\u7ba1\u7406\u5668\uff0c\u6aa2\u67e5\u5916\u639b\u8a3a\u65b7\u8cc7\u8a0a\uff0c\u7136\u5f8c\u4fee\u5fa9\u6216\u505c\u7528\u5b83\u3002\n"
        "\u6280\u8853\u7d30\u7bc0\uff1aaddon.json missing"
    )
    assert ui_host._translate_notice_text("Preparing local voice... for 5s") == (
        "\u6b63\u5728\u6e96\u5099\u672c\u6a5f\u8a9e\u97f3... for 5s"
    )


def test_speech_notice_translates_structure_but_preserves_runtime_detail(monkeypatch) -> None:
    """Translate speech timers and states without treating errors as catalog keys."""
    from runtime.workers import ui_host

    translations = {
        "Preparing speech services - {elapsed} elapsed.": "\u6b63\u5728\u6e96\u5099\u8a9e\u97f3\u670d\u52d9 - \u5df2\u7528\u6642 {elapsed}\u3002",
        "Speech warm-up failed.": "\u8a9e\u97f3\u670d\u52d9\u9810\u71b1\u5931\u6557\u3002",
        "STT (speech recognition)": "STT\uff08\u8a9e\u97f3\u8fa8\u8b58\uff09",
        "TTS (Kokoro local voice)": "TTS\uff08Kokoro \u672c\u6a5f\u8a9e\u97f3\uff09",
        "warming up ({elapsed})": "\u6b63\u5728\u9810\u71b1\uff08{elapsed}\uff09",
        "{minutes}m {seconds}s": "{minutes}\u5206 {seconds}\u79d2",
        "{seconds}s": "{seconds}\u79d2",
        "failed - {message}": "\u5931\u6557 - {message}",
        "{label}: {status}": "{label}\uff1a{status}",
    }
    requested: list[str] = []

    def translate(text: str) -> str:
        requested.append(text)
        return translations.get(text, text)

    monkeypatch.setattr(ui_host, "t", translate)

    assert ui_host._translate_notice_text(
        "Preparing speech services - 1m 05s elapsed.\n"
        "STT (speech recognition): warming up (12s)\n"
        "Speech warm-up failed.\n"
        "TTS (Kokoro local voice): failed - RuntimeError: cublas64_12.dll missing"
    ) == (
        "\u6b63\u5728\u6e96\u5099\u8a9e\u97f3\u670d\u52d9 - \u5df2\u7528\u6642 1\u5206 05\u79d2\u3002\n"
        "STT\uff08\u8a9e\u97f3\u8fa8\u8b58\uff09\uff1a\u6b63\u5728\u9810\u71b1\uff0812\u79d2\uff09\n"
        "\u8a9e\u97f3\u670d\u52d9\u9810\u71b1\u5931\u6557\u3002\n"
        "TTS\uff08Kokoro \u672c\u6a5f\u8a9e\u97f3\uff09\uff1a\u5931\u6557 - RuntimeError: cublas64_12.dll missing"
    )
    assert "RuntimeError: cublas64_12.dll missing" not in requested


def test_keyed_notice_updates_respect_user_dismissal() -> None:
    """Repeated warmup progress notices should not reopen after the user closes them."""
    from runtime.workers.ui_host import QtProtocolHost

    class Bubble:
        _thinking = False
        _transcript_preview = False
        _reply_chunk_count = 0
        _full_text = ""

        def __init__(self) -> None:
            self.visible = False
            self.notices = []

        def isVisible(self) -> bool:  # noqa: N802 - Qt-style API
            return self.visible

        def show_notice(self, text: str, timeout_ms: int = 12000) -> None:
            self.visible = True
            self.notices.append((text, timeout_ms))

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    first = host._reply_notice("Preparing local voice... for 0s", timeout_ms=0, key="audio-warmup")
    bubble.visible = False
    second = host._reply_notice("Preparing local voice... for 5s", timeout_ms=0, key="audio-warmup")

    assert first == {"shown": True, "text": "Preparing local voice... for 0s", "key": "audio-warmup"}
    assert second == {
        "shown": False,
        "text": "Preparing local voice... for 5s",
        "reason": "dismissed",
        "key": "audio-warmup",
    }
    assert bubble.notices == [("Preparing local voice... for 0s", 0)]


def test_rewrite_completion_exposes_working_undo_action(monkeypatch) -> None:
    """The completed response bubble emits the supervisor undo event when clicked."""
    from runtime.workers import ui_host
    from runtime.workers.ui_host import QtProtocolHost

    shown: list[dict] = []
    emitted: list[tuple[str, dict]] = []

    class Bubble:
        def show_notice(self, text: str, *, timeout_ms: int, actions: list) -> None:
            shown.append({"text": text, "timeout_ms": timeout_ms, "actions": actions})

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._ensure_bubble = lambda: Bubble()  # type: ignore[attr-defined]
    host.emit = lambda event, data=None, req_id=None: emitted.append((event, data or {}))  # type: ignore[method-assign]
    monkeypatch.setattr(ui_host, "t", lambda text: f"translated:{text}")

    result = host._reply_undo_ready("Fixed the grammar.", timeout_ms=30000)
    label, callback = shown[0]["actions"][0]
    callback()

    assert result == {"shown": True, "text": "translated:Fixed the grammar.", "action": "undo"}
    assert shown[0]["timeout_ms"] == 30000
    assert label == "translated:Undo"
    assert emitted == [("ui.rewrite.undo", {})]


def test_speech_status_notice_does_not_replace_active_reply() -> None:
    """Speech warmup/readiness notices must not overwrite model reply bubbles."""
    from runtime.workers.ui_host import QtProtocolHost

    class Bubble:
        _thinking = False
        _transcript_preview = False
        _reply_chunk_count = 1
        _full_text = "Actual model reply"

        def __init__(self) -> None:
            self.notices = []

        def isVisible(self) -> bool:  # noqa: N802 - Qt-style API
            return True

        def show_notice(self, text: str, timeout_ms: int = 12000) -> None:
            self.notices.append((text, timeout_ms))

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    result = host._reply_notice("Local voice is ready.", timeout_ms=6000)

    assert result == {"shown": False, "text": "Local voice is ready.", "reason": "active_reply"}
    assert bubble.notices == []


def test_speech_status_notice_does_not_replace_pending_transcript() -> None:
    """Warmup notices should not make the first model token append to status text."""
    from runtime.workers.ui_host import QtProtocolHost

    class Bubble:
        _thinking = False
        _transcript_preview = True
        _reply_chunk_count = 0
        _full_text = "Heard: summarize this"

        def __init__(self) -> None:
            self.notices = []

        def isVisible(self) -> bool:  # noqa: N802 - Qt-style API
            return True

        def show_notice(self, text: str, timeout_ms: int = 12000) -> None:
            self.notices.append((text, timeout_ms))

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    result = host._reply_notice("Warming up speech recognition...", timeout_ms=0)

    assert result["shown"] is False
    assert result["reason"] == "active_reply"
    assert bubble.notices == []


def test_speech_status_notice_does_not_replace_thinking_reply() -> None:
    """Warmup notices should not become the prefix for the first model token."""
    from runtime.workers.ui_host import QtProtocolHost

    class Bubble:
        _thinking = True
        _transcript_preview = False
        _reply_chunk_count = 0
        _full_text = ""

        def __init__(self) -> None:
            self.notices = []

        def isVisible(self) -> bool:  # noqa: N802 - Qt-style API
            return True

        def show_notice(self, text: str, timeout_ms: int = 12000) -> None:
            self.notices.append((text, timeout_ms))

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    result = host._reply_notice("STT/TTS is warming up", timeout_ms=0)

    assert result["shown"] is False
    assert result["reason"] == "active_reply"
    assert bubble.notices == []

    result = host._reply_notice("Preparing local voice... for 5s", timeout_ms=0)

    assert result["shown"] is False
    assert result["reason"] == "active_reply"
    assert bubble.notices == []


def test_speech_warmup_failure_notice_still_shows_during_reply() -> None:
    """Actual speech warmup failures remain visible instead of being suppressed."""
    from runtime.workers.ui_host import QtProtocolHost

    class Bubble:
        _thinking = False
        _transcript_preview = False
        _reply_chunk_count = 1
        _full_text = "Actual model reply"

        def __init__(self) -> None:
            self.notices = []

        def isVisible(self) -> bool:  # noqa: N802 - Qt-style API
            return True

        def show_notice(self, text: str, timeout_ms: int = 12000) -> None:
            self.notices.append((text, timeout_ms))

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    result = host._reply_notice("Local speech warmup failed: tts: missing model", timeout_ms=6000)

    assert result == {"shown": True, "text": "Local speech warmup failed: tts: missing model"}
    assert bubble.notices == [("Local speech warmup failed: tts: missing model", 6000)]


def test_transient_local_tts_warmup_notices_do_not_show_in_bubble() -> None:
    """Kokoro lock/import contention is transient and should stay out of the bubble."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)

    def fail_bubble():
        raise AssertionError("transient warmup notices should not create or use a bubble")

    host._ensure_bubble = fail_bubble  # type: ignore[attr-defined]

    messages = [
        "Local voice is still warming up. Try again when OpenWand says local speech is ready.",
        (
            "Local speech warmup failed: tts: error: RuntimeError: Kokoro is still warming up. "
            "Current stage: importing kokoro.KPipeline (17s). Try again when local speech is ready."
        ),
        (
            "[tts] Kokoro warmup failed: error: RuntimeError: Kokoro is still warming up. "
            "Current stage: importing kokoro.KPipeline (17s). Try again when local speech is ready."
        ),
    ]

    for message in messages:
        assert host._reply_notice(message, timeout_ms=6000) == {
            "shown": False,
            "text": message,
            "reason": "transient_local_tts_warmup",
        }


def test_memory_proxy_accepts_project_scope() -> None:
    """Verify UI memory proxy forwards project-scoped add/update payloads."""
    from runtime.workers.ui_host import MemoryProxy

    emitted = []
    proxy = MemoryProxy(lambda event, payload: emitted.append((event, payload)))

    proxy.add_fact_manual("ships on Fridays", project="proj-1")
    fact_id = proxy.get_all_facts()[0]["id"]
    proxy.update_fact(fact_id, "ships on Mondays", project="")

    assert emitted == [
        (
            "ui.memory.add",
            {"text": "ships on Fridays", "category": "project_context", "project": "proj-1"},
        ),
        (
            "ui.memory.update",
            {"id": fact_id, "text": "ships on Mondays", "category": "general", "project": ""},
        ),
    ]
    assert proxy.get_all_facts()[0]["category"] == "general"
    assert proxy.get_all_facts()[0]["project"] == ""


class _Bubble:
    """Capture reply chunks sent to the speech bubble."""

    def __init__(self) -> None:
        self.chunks: list[tuple[str, bool]] = []
        self.progress: list[str] = []
        self.labeled: list[tuple[str, str, int, bool]] = []
        self.images: list[str] = []

    def append_chunk(self, text: str, is_thought: bool = False, annotations=None) -> None:
        self.chunks.append((text, is_thought))

    def show_progress(self, text: str) -> None:
        self.progress.append(text)

    def show_image(self, image_base64: str) -> bool:
        self.images.append(image_base64)
        return True

    def show_labeled_text(
        self,
        label: str,
        text: str,
        *,
        timeout_ms: int = 0,
        cancel_on_close: bool = True,
    ) -> None:
        self.labeled.append((label, text, timeout_ms, cancel_on_close))


def test_reply_chunk_accepts_progress_metadata() -> None:
    """Progress chunks show as a transient status, not appended reply content."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = _Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    result = host._reply_chunk(text="Reading files...", is_progress=True)

    assert result == {"appended": len("Reading files..."), "is_progress": True}
    # Progress text must NOT be appended as reply content (would read
    # "Reading files... <answer>" in the bubble); it goes to show_progress so the
    # first real reply token replaces it.
    assert bubble.chunks == []
    assert bubble.progress == ["Reading files..."]


def test_action_progress_replaces_the_same_visible_status_line() -> None:
    """Ordered action stages use the bubble's replaceable progress surface."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = _Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    first = host._action_progress(text="Reading the saved file...", stage="reading", sequence=1)
    second = host._action_progress(
        text="Building the exact diff preview...",
        stage="preparing_preview",
        sequence=2,
    )

    assert bubble.chunks == []
    assert bubble.progress == ["Reading the saved file...", "Building the exact diff preview..."]
    assert first == {"shown": True, "stage": "reading", "sequence": 1, "terminal": False}
    assert second["stage"] == "preparing_preview"


def test_reply_chunk_keeps_provider_action_in_thought_transcript() -> None:
    """Provider actions are translated activity, not replaceable status text."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = _Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    result = host._reply_chunk(text="Claude started Read", is_progress=True, is_thought=True)

    assert result == {"appended": len("Claude started Read"), "is_progress": True}
    assert bubble.progress == []
    assert bubble.chunks == [("Claude started Read", True)]


def test_reply_image_loads_generated_attachment_for_speech_bubble(tmp_path) -> None:
    """Generated image paths are size-checked and forwarded to the bubble."""
    from runtime.workers.ui_host import QtProtocolHost

    image_path = tmp_path / "generated.png"
    image_path.write_bytes(b"small-image-payload")
    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = _Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    result = host._reply_image(
        attachments=[
            {
                "kind": "image",
                "source": "codex_image_generation",
                "path": str(image_path),
            }
        ]
    )

    assert result == {"shown": True}
    assert bubble.images == [__import__("base64").b64encode(image_path.read_bytes()).decode("ascii")]


def test_reply_labeled_text_keeps_label_out_of_reply_content() -> None:
    """Addons and built-ins can show UI labels without making them reply text."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = _Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    result = host._reply_labeled_text(
        label="Tool",
        text="Indexing files",
        timeout_ms=2500,
        cancel_on_close=False,
    )

    assert result == {
        "shown": True,
        "label": "Tool",
        "text": "Indexing files",
        "label_excluded_from_reply": True,
    }
    assert bubble.labeled == [("Tool", "Indexing files", 2500, False)]
    assert bubble.chunks == []


def _install_fake_pyside(monkeypatch, *, top_level_widgets):
    """Fake the PySide6 pieces the shutdown path imports lazily."""
    import sys
    from types import SimpleNamespace

    def single_shot(interval, callback):
        assert interval == 0
        callback()

    qtcore = SimpleNamespace(QTimer=SimpleNamespace(singleShot=single_shot))
    qtwidgets = SimpleNamespace(
        QApplication=SimpleNamespace(topLevelWidgets=lambda: list(top_level_widgets))
    )
    monkeypatch.setitem(sys.modules, "PySide6", SimpleNamespace(QtCore=qtcore, QtWidgets=qtwidgets))
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qtcore)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qtwidgets)


def _fake_window(events, name):
    """Record close/deleteLater calls for a stand-in top-level widget."""
    from types import SimpleNamespace

    return SimpleNamespace(
        close=lambda: events.append((name, "close")),
        deleteLater=lambda: events.append((name, "deleteLater")),
    )


def test_debug_tray_trigger_queues_action_until_after_dispatch(monkeypatch) -> None:
    """A terminating tray action must not run inside its request-response dispatch."""
    import sys
    from types import SimpleNamespace

    from runtime.workers.ui_host import QtProtocolHost

    queued = []
    triggered = []
    qtcore = SimpleNamespace(
        QTimer=SimpleNamespace(singleShot=lambda interval, callback: queued.append((interval, callback)))
    )
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qtcore)
    monkeypatch.setenv("OPENWAND_UI_DEBUG_METHODS", "1")
    action = SimpleNamespace(text=lambda: "Quit", trigger=lambda: triggered.append("Quit"))
    overlay = SimpleNamespace(_tray_menu=SimpleNamespace(actions=lambda: [action]))
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._ensure_overlay = lambda: overlay  # type: ignore[method-assign]

    result = host._dispatch("ui.debug.tray.trigger", {"label": "Quit"})

    assert result == {"triggered": True, "label": "Quit"}
    assert triggered == []
    assert len(queued) == 1 and queued[0][0] == 0
    queued[0][1]()
    assert triggered == ["Quit"]


def test_native_workspace_endpoint_accepts_only_authenticated_loopback() -> None:
    """The custom window rejects remote, file, and unauthenticated endpoints."""
    import pytest

    from ui.virtual_workspace_window import _validated_endpoint

    token = "t" * 32
    assert _validated_endpoint(f"http://127.0.0.1:8765/?token={token}") == (
        "http://127.0.0.1:8765",
        token,
    )
    with pytest.raises(ValueError, match="loopback"):
        _validated_endpoint(f"https://example.com/?token={token}")
    with pytest.raises(ValueError, match="loopback"):
        _validated_endpoint("http://127.0.0.1:8765/")


def test_model_opened_workspace_window_is_native_reusable_and_nonactivating(tmp_path) -> None:
    """The real UI host opens and reuses the native viewer without taking focus."""
    import sys

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from addons.virtual_workspace.workspace import WorkspaceController
    from runtime.workers.ui_host import QtProtocolHost

    app = QApplication.instance() or QApplication(sys.argv)
    workspace = WorkspaceController()
    workspace.configure(tmp_path / "workspace-data")
    workspace.start()
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._virtual_workspace_window = None

    try:
        opened = host._show_virtual_workspace(workspace.viewer_url, activate=False)
        app.processEvents()
        window = host._virtual_workspace_window
        assert opened == {"shown": True, "reused": False, "native": True}
        assert window is not None and window.isVisible()
        assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        reused = host._show_virtual_workspace(workspace.viewer_url, activate=False)
        assert reused == {"shown": True, "reused": True, "native": True}
        assert host._virtual_workspace_window is window
    finally:
        if host._virtual_workspace_window is not None:
            host._virtual_workspace_window.close()
        workspace.stop()
        app.processEvents()


def test_virtual_workspace_task_start_uses_a_locked_down_scoped_agent_spec(monkeypatch) -> None:  # noqa: ANN001
    """The embedded task composer starts a scoped file task, not a host computer-use run."""
    import config
    from runtime.workers.ui_host import QtProtocolHost

    emitted = []
    host = QtProtocolHost.__new__(QtProtocolHost)
    host.emit = lambda event, payload: emitted.append((event, payload))  # type: ignore[method-assign]

    monkeypatch.setattr(config, "CHAT_REASONING_EFFORT", "high")
    host._start_virtual_workspace_task("Create notes/readme.txt", "C:/isolated/session/files")

    assert emitted[0][0] == "ui.agent.run_requested"
    spec = emitted[0][1]["spec"]
    assert spec["scope_folder"] == "C:/isolated/session/files"
    assert spec["allow_file_create"] is True
    assert spec["allow_file_edit"] is True
    assert spec["allow_shell"] is False
    assert spec["allow_network"] is False
    assert spec["allow_file_delete"] is False
    assert spec["reasoning_effort"] == "high"
    assert spec["max_turns"] == 12
    assert spec["full_turn_max_tokens"] == 4096
    assert spec["delta_turn_max_tokens"] == 3072
    assert spec["pause_holds_terminal_final"] is False
    assert spec["finish_on_successful_tools"] is True
    assert spec["max_tool_calls_per_turn"] == 0
    assert spec["parallel_execution"] is False
    assert spec["max_parallel_agents"] == 1
    assert host._virtual_workspace_agent_active is True


def test_virtual_workspace_parallelizes_only_explicit_independent_file_tasks() -> None:
    """Distinct named file checklist items receive independent visible workers."""
    from runtime.workers.ui_host import QtProtocolHost

    emitted = []
    host = QtProtocolHost.__new__(QtProtocolHost)
    host.emit = lambda event, payload: emitted.append((event, payload))  # type: ignore[method-assign]

    host._start_virtual_workspace_task(
        "1. Create alpha.md with a heading\n"
        "2. Create beta.csv with two rows\n"
        "3. Create gamma.svg with a blue circle",
        "C:/isolated/session/files",
    )

    spec = emitted[0][1]["spec"]
    assert spec["parallel_execution"] is True
    assert spec["max_parallel_agents"] == 3
    assert [agent["name"] for agent in spec["agents"]] == [
        "Coordinator",
        "Worker 1",
        "Worker 2",
        "Worker 3",
    ]
    assert "alpha.md" in spec["agents"][1]["responsibility"]
    assert "beta.csv" in spec["agents"][2]["responsibility"]
    assert "gamma.svg" in spec["agents"][3]["responsibility"]


def test_virtual_workspace_keeps_dependent_file_tasks_sequential() -> None:
    """Dependency wording disables worker fan-out."""
    from runtime.workers.ui_host import QtProtocolHost

    emitted = []
    host = QtProtocolHost.__new__(QtProtocolHost)
    host.emit = lambda event, payload: emitted.append((event, payload))  # type: ignore[method-assign]

    host._start_virtual_workspace_task(
        "1. Create data.csv with two rows\n"
        "2. Create report.md after data.csv is complete",
        "C:/isolated/session/files",
    )

    spec = emitted[0][1]["spec"]
    assert spec["parallel_execution"] is False
    assert spec["agents"] == []
    assert spec["max_parallel_agents"] == 1


def test_virtual_workspace_receives_only_its_own_agent_progress() -> None:
    from runtime.workers.ui_host import QtProtocolHost

    class Workspace:
        def __init__(self) -> None:
            self.logs = []
            self.traces = []
            self.done = []

        def isVisible(self) -> bool:
            return True

        def append_agent_event(self, params) -> None:  # noqa: ANN001
            self.logs.append(params)

        def append_agent_trace(self, params) -> bool:  # noqa: ANN001
            self.traces.append(params)
            return True

        def finish_agent_task(self, params) -> None:  # noqa: ANN001
            self.done.append(params)

    workspace = Workspace()
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._virtual_workspace_window = workspace
    host._virtual_workspace_agent_active = True
    host._agent_run_dialog = None

    assert host._agent_log(line="inventory complete")["accepted"] is True
    assert host._agent_trace(entry='{"workspace_progress": {}}')["accepted"] is True
    assert host._agent_done(final="done")["accepted"] is True
    assert workspace.logs == [{"line": "inventory complete"}]
    assert workspace.traces == [{"entry": '{"workspace_progress": {}}'}]
    assert workspace.done == [{"final": "done"}]
    assert host._virtual_workspace_agent_active is False


def test_ui_shutdown_message_defers_quit_and_leaves_stdin_open(monkeypatch) -> None:
    """Verify __shutdown__ tears down windows once, then quits via the loop."""
    import json
    from types import SimpleNamespace

    from runtime.workers.ui_host import QtProtocolHost

    window_events = []
    quit_calls = []
    _install_fake_pyside(
        monkeypatch,
        top_level_widgets=[_fake_window(window_events, "overlay"), _fake_window(window_events, "chat")],
    )
    host = QtProtocolHost.__new__(QtProtocolHost)
    stopped = []
    watchdog_stopped = []
    responses = []
    host._closing = False
    host._pump = SimpleNamespace(stop=lambda: stopped.append(True))
    host._watchdog = SimpleNamespace(stop=lambda: watchdog_stopped.append(True))
    host._app = SimpleNamespace(quit=lambda: quit_calls.append(True))
    host._respond = lambda req_id, ok, **kwargs: responses.append((req_id, ok, kwargs))  # type: ignore[method-assign]

    host._handle_line(json.dumps({"id": 7, "method": "__shutdown__", "params": {}}).encode("utf-8"))

    assert responses == [(7, True, {"result": None})]
    assert host._closing is True
    assert stopped == [True]
    assert watchdog_stopped == [True]
    assert window_events == [
        ("overlay", "close"),
        ("overlay", "deleteLater"),
        ("chat", "close"),
        ("chat", "deleteLater"),
    ]
    assert quit_calls == [True]


def test_ui_about_to_quit_emits_user_quit_once_and_leaves_stdin_open(monkeypatch) -> None:
    """Verify user-requested Qt quit tells the supervisor not to restart UI."""
    from types import SimpleNamespace

    from runtime.workers.ui_host import QtProtocolHost

    window_events = []
    _install_fake_pyside(
        monkeypatch,
        top_level_widgets=[_fake_window(window_events, "overlay")],
    )
    emitted = []
    stopped = []
    watchdog_stopped = []
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._closing = False
    host._pump = SimpleNamespace(stop=lambda: stopped.append(True))
    host._watchdog = SimpleNamespace(stop=lambda: watchdog_stopped.append(True))
    host.emit = lambda event, data=None, req_id=None: emitted.append((event, data, req_id))  # type: ignore[method-assign]

    host._on_about_to_quit()
    host._on_about_to_quit()

    assert host._closing is True
    assert emitted == [("ui.quit_requested", {"reason": "qt_about_to_quit"}, None)]
    assert stopped == [True]
    assert watchdog_stopped == [True]
    assert window_events == [("overlay", "close"), ("overlay", "deleteLater")]


def test_bubble_highlight_does_not_mutate_chat_window() -> None:
    """Verify TTS bubble highlight leaves selectable chat transcript alone."""
    from types import SimpleNamespace

    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    chat_updates = []
    emitted = []
    host._chat = SimpleNamespace(
        update_live_highlight=lambda *args: chat_updates.append(args)
    )
    host.emit = lambda event, payload: emitted.append((event, payload))  # type: ignore[method-assign]

    host._bubble_highlight("done", 1, False)

    assert chat_updates == []
    assert emitted == [
        ("ui.bubble.highlight", {"text": "done", "revealed_count": 1, "finished": False})
    ]


def test_chat_add_conversation_stamps_metadata() -> None:
    """Verify hotkey-created conversations carry display-only timestamps."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    persisted = []
    host._persist_conversations = lambda: persisted.append(True)  # type: ignore[attr-defined]

    result = host._chat_add_conversation(user="hi", assistant="hello")

    assert result == {"count": 1, "continued": False}
    assert persisted == [True]
    conv = host._all_conversations[0]
    assert conv["created_at"]
    assert conv["updated_at"] == conv["created_at"]
    assert conv["messages"][0]["created_at"] == conv["created_at"]
    assert conv["messages"][1]["created_at"] == conv["created_at"]


def test_chat_add_conversation_persists_image_only_assistant(tmp_path) -> None:
    """A generated image is an assistant message even when final text is empty."""
    from runtime.workers.ui_host import QtProtocolHost

    image_path = tmp_path / "generated.png"
    image_path.write_bytes(b"generated-image")
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    persisted = []
    host._persist_conversations = lambda: persisted.append(True)  # type: ignore[attr-defined]

    result = host._chat_add_conversation(
        user="Generate a test image",
        assistant="",
        assistant_attachments=[
            {
                "kind": "image",
                "source": "codex_image_generation",
                "path": str(image_path),
                "name": "generated.png",
            }
        ],
    )

    assert result == {"count": 1, "continued": False}
    assert persisted == [True]
    messages = host._all_conversations[0]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["content"] == ""
    assert messages[1]["attachments"][0]["kind"] == "image"
    assert messages[1]["attachments"][0]["source"] == "codex_image_generation"
    assert messages[1]["attachments"][0]["path"] == str(image_path)


def test_agent_owned_chat_is_mirrored_into_openwand_history_with_live_activity() -> None:
    """A remote-owned turn must still leave a complete local OpenWand transcript."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    persisted = []
    host._persist_conversations = lambda: persisted.append(True)  # type: ignore[attr-defined]

    host._chat_add_conversation(
        user="inspect the project",
        assistant="Finished",
        display_segments=[
            {"text": "Inspecting\nRunning: rg\n", "is_thought": True},
            {"text": "Finished", "is_thought": False},
        ],
        harness={
            "provider": "codex",
            "session_id": "thread-1",
            "cwd": "/repo",
            "conversation_owner": "agent",
        },
    )

    conv = host._all_conversations[0]
    assert [message["content"] for message in conv["messages"]] == ["inspect the project", "Finished"]
    assert conv["messages"][1]["display_segments"][0]["is_thought"] is True
    assert conv["messages"][1]["display_content"] == (
        "<thought>Inspecting\nRunning: rg\n</thought>Finished"
    )
    assert conv["harness_sessions"]["codex"]["session_id"] == "thread-1"
    assert persisted == [True]


def test_macos_snip_app_region_avoids_ui_quartz_by_default(monkeypatch) -> None:
    """The UI worker should not import Quartz just to preselect Snip's App mode."""
    import builtins

    from runtime.workers import ui_host
    from runtime.workers.ui_host import QtProtocolHost

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        """Fail if the default macOS path imports Quartz."""
        if name == "Quartz" or name.startswith("Quartz."):
            raise AssertionError("UI worker should not import Quartz by default")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(ui_host.sys, "platform", "darwin")
    monkeypatch.delenv("OPENWAND_MACOS_UI_QUARTZ_SNIP_APP_REGION", raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    host = QtProtocolHost.__new__(QtProtocolHost)

    assert host._mac_snip_app_region() is None


def test_chat_add_conversation_selects_new_chat_when_window_is_open() -> None:
    """Verify externally created chats become visible in an open chat window."""
    from types import SimpleNamespace

    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._persist_conversations = lambda: None  # type: ignore[attr-defined]
    ingest_calls = []
    host._chat = SimpleNamespace(
        isVisible=lambda: True,
        ingest_new_conversations=lambda **kwargs: ingest_calls.append(kwargs)
    )

    result = host._chat_add_conversation(user="hi", assistant="hello")

    assert result == {"count": 1, "continued": False}
    assert ingest_calls == [{"select_new": True}]


def test_chat_add_conversation_does_not_touch_hidden_chat_window() -> None:
    """Verify hotkey chats persist without surfacing a hidden chat widget."""
    from types import SimpleNamespace

    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._persist_conversations = lambda: None  # type: ignore[attr-defined]
    ingest_calls = []
    host._chat = SimpleNamespace(
        isVisible=lambda: False,
        ingest_new_conversations=lambda **kwargs: ingest_calls.append(kwargs),
    )

    result = host._chat_add_conversation(user="hi", assistant="hello")

    assert result == {"count": 1, "continued": False}
    assert ingest_calls == []


def test_chat_add_conversation_persists_file_context() -> None:
    """Verify hotkey-created conversations store file tool metadata."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    host._persist_conversations = lambda: None  # type: ignore[attr-defined]
    file_context = [
        {
            "tool": "create_file",
            "path": r"C:\repo\model_files\hello_world.py",
            "relative_path": "hello_world.py",
            "root": r"C:\repo\model_files",
            "ok": True,
            "message": "Created hello_world.py.",
        }
    ]

    host._chat_add_conversation(user="create", assistant="done", file_context=file_context)

    assert host._all_conversations[0]["file_context"] == file_context


def test_chat_add_conversation_persists_text_annotations() -> None:
    """Verify addon text annotations are stored with chat messages."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    host._persist_conversations = lambda: None  # type: ignore[attr-defined]
    user_annotations = [{"start": 0, "end": 4, "tag": "u"}]
    assistant_annotations = [{"start": 0, "end": 4, "tag": "mark"}]

    host._chat_add_conversation(
        user="test",
        assistant="done",
        user_annotations=user_annotations,
        assistant_annotations=assistant_annotations,
    )

    messages = host._all_conversations[0]["messages"]
    assert messages[0]["annotations"] == user_annotations
    assert messages[1]["annotations"] == assistant_annotations


def test_openwand_owned_harness_reply_clears_provider_continuation() -> None:
    """Switching continuity to OpenWand must not later resume a stale agent session."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._active_project_id = "general"
    host._all_conversations = [{
        "messages": [{"role": "user", "content": "old"}],
        "harness_sessions": {
            "codex": {"provider": "codex", "session_id": "thread-old", "cwd": "/repo"}
        },
    }]
    host._chat = None
    host._persist_conversations = lambda: None  # type: ignore[attr-defined]

    host._chat_add_conversation(
        user="new",
        assistant="answer",
        harness={
            "provider": "codex",
            "session_id": "",
            "cwd": "/repo",
            "conversation_owner": "openwand",
            "clear_session": True,
        },
    )

    assert "harness_sessions" not in host._all_conversations[0]


def test_chat_begin_conversation_persists_user_then_final_appends_assistant() -> None:
    """Verify overlay prompts are recoverable before the assistant reply lands."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    persisted = []
    host._persist_conversations = lambda: persisted.append(True)  # type: ignore[attr-defined]

    begin = host._chat_begin_conversation(user="edit notes", context="ctx", context_policy={"context_memory_mode": "on"})
    idx = begin["conversation_index"]

    assert begin["started"] is True
    assert idx == 0
    assert [message["role"] for message in host._all_conversations[0]["messages"]] == ["user"]
    assert host._all_conversations[0]["messages"][0]["content"] == "edit notes"
    assert host._all_conversations[0]["messages"][0]["context"] == "ctx"

    host._chat_add_conversation(
        user="edit notes",
        assistant="done",
        append_user=False,
        conversation_index=idx,
        tool_context={"allowed_tools": ["edit_file"], "pinned_tools": [], "file_access_mode": "ask"},
    )

    messages = host._all_conversations[0]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "done"
    assert host._external_reply_stream is None
    assert len(persisted) == 2


def test_overlay_reply_chunks_restore_when_chat_opens_mid_reply() -> None:
    """Chunks produced before Chat exists must reconstruct the ongoing answer."""
    from runtime.workers.ui_host import QtProtocolHost

    class Chat:
        def __init__(self) -> None:
            self.calls = []

        def begin_external_reply_stream(self, idx: int) -> None:
            self.calls.append(("begin", idx))

        def external_reply_chunk(self, idx: int, chunk: dict) -> None:
            self.calls.append(("chunk", idx, dict(chunk)))

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._all_conversations = [{"messages": [{"role": "user", "content": "please sum this up"}]}]
    host._chat = None
    host._chat_streams = {}
    import threading

    host._chat_streams_lock = threading.Lock()
    host._external_reply_stream = {
        "conversation_index": 0,
        "chunks": [],
        "attached_chat_id": None,
        "done": False,
    }

    assert host._chat_chunk(conversation_index=0, text="Working", is_progress=True)["queued"] is True
    assert host._chat_chunk(conversation_index=0, text="Summary text")["queued"] is True

    chat = Chat()
    host._chat = chat
    assert host._restore_external_reply_stream() is True
    assert chat.calls == [
        ("begin", 0),
        (
            "chunk",
            0,
            {"text": "Working", "is_progress": True, "is_thought": False, "local_work": {}},
        ),
        (
            "chunk",
            0,
            {"text": "Summary text", "is_progress": False, "is_thought": False, "local_work": {}},
        ),
    ]

    assert host._chat_chunk(conversation_index=0, text=" continues")["queued"] is True
    assert chat.calls[-1] == (
        "chunk",
        0,
        {"text": " continues", "is_progress": False, "is_thought": False, "local_work": {}},
    )


def test_chat_chunk_preserves_structured_harness_activity() -> None:
    """The UI stream carries agent data without mixing it into assistant prose."""
    import queue
    import threading

    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    stream: queue.Queue = queue.Queue()
    host._chat_streams = {"chat-1": stream}
    host._chat_streams_lock = threading.Lock()
    activity = {"type": "subagent", "agent_id": "child", "prompt": "Inspect tests"}

    assert host._chat_chunk(request_id="chat-1", harness_activity=activity)["queued"] is True

    kind, payload = stream.get_nowait()
    assert kind == "chunk"
    assert payload["text"] == ""
    assert payload["harness_activity"] == activity


def test_chat_request_reuses_active_conversation_tool_context() -> None:
    """Verify chat sends stored tool policy when continuing a conversation."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._all_conversations = [
        {
            "messages": [{"role": "user", "content": "hi"}],
            "tool_context": {
                "allowed_tools": ["read_file", "edit_file"],
                "pinned_tools": ["read_file", "edit_file"],
                "file_access_mode": "ask",
            },
        }
    ]
    host._chat_request_ids = iter([1])
    host._chat_streams = {}
    import threading

    host._chat_streams_lock = threading.Lock()
    emitted = []

    def emit(event, payload):
        emitted.append((event, payload))
        request_id = payload["request_id"]
        host._chat_done(request_id=request_id, text="ok", tool_context=payload["tool_context"])

    host.emit = emit  # type: ignore[method-assign]

    result = list(host._make_chat_send_fn()([{"role": "user", "content": "continue"}]))

    assert emitted[0][0] == "ui.chat.request"
    assert emitted[0][1]["tool_context"]["file_access_mode"] == "ask"
    assert emitted[0][1]["tool_context"]["allowed_tools"] == ["read_file", "edit_file"]
    assert result == [
        {
            "type": "metadata",
            "file_context": [],
            "tool_context": emitted[0][1]["tool_context"],
            "context_snippets": [],
            "annotations": [],
            "user_annotations": [],
        },
        {"type": "final", "text": "ok"},
    ]


def test_chat_send_fn_forwards_annotation_metadata() -> None:
    """Verify chat stream metadata carries addon text annotations."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._all_conversations = []
    host._chat_request_ids = iter([1])
    host._chat_streams = {}
    import threading

    host._chat_streams_lock = threading.Lock()
    assistant_annotations = [{"start": 0, "end": 2, "tag": "mark"}]
    user_annotations = [{"start": 0, "end": 4, "tag": "u"}]

    def emit(event, payload):
        request_id = payload["request_id"]
        host._chat_done(
            request_id=request_id,
            text="ok",
            annotations=assistant_annotations,
            user_annotations=user_annotations,
        )

    host.emit = emit  # type: ignore[method-assign]

    result = list(host._make_chat_send_fn()([{"role": "user", "content": "test"}]))

    assert result[0]["type"] == "metadata"
    assert result[0]["annotations"] == assistant_annotations
    assert result[0]["user_annotations"] == user_annotations
    assert result[-1] == {"type": "final", "text": "ok"}


def test_selecting_chat_shows_overlay_continuation_notice() -> None:
    """Verify chat selection reflects the target conversation in the bubble."""
    from types import SimpleNamespace

    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._all_conversations = [
        {"messages": [{"role": "user", "content": "old chat"}]},
        {"messages": [{"role": "user", "content": "new chat"}]},
    ]
    host._chat = SimpleNamespace(_streaming=False)
    notices = []
    host._ensure_bubble = lambda: SimpleNamespace(show_notice=lambda text, timeout_ms=0: notices.append((text, timeout_ms)))  # type: ignore[attr-defined]

    host._set_active_conversation(1)

    assert host._active_conversation_idx == 1
    assert notices == [("Continuing: new chat", 2500)]


def test_reply_notice_forwards_warning_severity_when_supported() -> None:
    """Tagged notices should reach the bubble with their warning severity."""
    from runtime.workers.ui_host import QtProtocolHost

    class Bubble:
        def __init__(self) -> None:
            self.calls = []

        def isVisible(self) -> bool:  # noqa: N802 - Qt-style fake
            return True

        def show_notice(self, text: str, timeout_ms: int = 12000, severity: str = "") -> None:
            self.calls.append({"text": text, "timeout_ms": timeout_ms, "severity": severity})

    bubble = Bubble()
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._ensure_bubble = lambda: bubble  # type: ignore[method-assign]
    host._active_notice_key = ""

    result = host._reply_notice("Global hotkeys did not start.", severity="warning")

    assert result == {"shown": True, "text": "Global hotkeys did not start."}
    assert bubble.calls == [
        {"text": "Global hotkeys did not start.", "timeout_ms": 12000, "severity": "warning"}
    ]


def test_intent_conversation_options_start_new_until_chat_is_active() -> None:
    """Verify loaded history is listed but not continued by default on app start."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._all_conversations = [
        {"messages": [{"role": "user", "content": "old chat"}], "project_id": "general"},
        {"messages": [{"role": "user", "content": "latest chat"}], "project_id": "proj-1"},
    ]

    options = host._intent_conversation_options()

    assert [option["index"] for option in options[:2]] == [1, 0]
    assert options[0]["project_id"] == "proj-1"
    assert not any(option["selected"] for option in options)

    host._active_conversation_idx = 0
    selected_options = host._intent_conversation_options()

    assert [option for option in selected_options if option["selected"]][0]["index"] == 0


def test_intent_conversation_options_are_isolated_by_provider_scope() -> None:
    """Codex-owned pickers must not offer native OpenWand or Claude history."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._conversation_scope_key = "codex"
    host._active_conversation_idx = 0
    host._all_conversations = [
        {
            "messages": [{"role": "user", "content": "native"}],
            "project_id": "general",
            "conversation_scope": "openwand",
        },
        {
            "messages": [{"role": "user", "content": "codex"}],
            "project_id": "codex-project",
            "conversation_scope": "codex",
        },
        {
            "messages": [{"role": "user", "content": "claude"}],
            "project_id": "claude-project",
            "conversation_scope": "claude",
        },
    ]

    options = host._intent_conversation_options()

    assert [option["index"] for option in options] == [1]
    assert options[0]["selected"] is False


def test_chat_add_conversation_does_not_cross_provider_scope() -> None:
    """A route switch starts a new record instead of appending to native OpenWand."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._conversation_scope_key = "codex"
    host._active_conversation_idx = 0
    host._active_project_id = "general"
    host._all_conversations = [
        {
            "messages": [{"role": "user", "content": "native"}],
            "project_id": "general",
            "conversation_scope": "openwand",
        }
    ]
    host._chat = None
    host._persist_conversations = lambda: None  # type: ignore[attr-defined]

    host._chat_add_conversation(user="agent question", assistant="agent answer")

    assert [message["content"] for message in host._all_conversations[0]["messages"]] == ["native"]
    assert host._all_conversations[1]["conversation_scope"] == "codex"
    assert host._active_conversation_idx == 1


def test_apply_intent_conversation_choice_preserves_new_selection() -> None:
    """Verify a canceled picker can retarget future prompts to a new chat."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._all_conversations = [
        {"messages": [{"role": "user", "content": "existing chat"}]},
    ]

    result = host._apply_intent_conversation_choice({"mode": "new"})

    assert result == {"mode": "new"}
    assert host._active_conversation_idx is None


def test_cancelled_intent_only_applies_touched_conversation_choice() -> None:
    """Verify plain cancel keeps the active chat but explicit picker changes stick."""
    from runtime.workers.ui_host import QtProtocolHost

    class FakeOverlay:
        def __init__(self, touched: bool, choice: dict):
            self._touched = touched
            self._choice = choice

        def conversation_choice_touched(self) -> bool:
            return self._touched

        def conversation_choice(self) -> dict:
            return self._choice

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._all_conversations = [
        {"messages": [{"role": "user", "content": "existing chat"}]},
        {"messages": [{"role": "user", "content": "latest chat"}]},
    ]

    host._apply_cancelled_intent_conversation_choice(FakeOverlay(False, {"mode": "new"}))
    assert host._active_conversation_idx == 0

    host._apply_cancelled_intent_conversation_choice(FakeOverlay(True, {"mode": "new"}))
    assert host._active_conversation_idx is None

    host._apply_cancelled_intent_conversation_choice(FakeOverlay(True, {"mode": "continue", "index": 1}))
    assert host._active_conversation_idx == 1


def test_apply_intent_project_choice_sets_active_or_creates_project(monkeypatch) -> None:
    """Verify intent overlay project choice updates the active project."""
    from core.conversation_store import store as conversation_store
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    applied = []
    host._active_project_id = "general"
    host._apply_memory_project = lambda: applied.append(host._active_project_id)  # type: ignore[method-assign]
    monkeypatch.setattr(
        conversation_store,
        "load_projects",
        lambda: [
            {"id": "general", "name": "General"},
            {"id": "proj-1", "name": "Personal OS"},
        ],
    )
    monkeypatch.setattr(
        conversation_store,
        "add_project",
        lambda name: {"id": "proj-new", "name": name},
    )

    existing = host._apply_intent_project_choice({"mode": "existing", "project_id": "proj-1"})

    assert existing == {"mode": "existing", "project_id": "proj-1"}
    assert host._active_project_id == "proj-1"

    created = host._apply_intent_project_choice({"mode": "new_project", "name": "New Work"})

    assert created == {"mode": "existing", "project_id": "proj-new"}
    assert host._active_project_id == "proj-new"
    assert applied[-2:] == ["proj-1", "proj-new"]


def test_chat_stream_preserves_structured_thought_chunks() -> None:
    """Verify chat stream yields thought metadata instead of flattening it."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._all_conversations = []
    host._chat_request_ids = iter([1])
    host._chat_streams = {}
    import threading

    host._chat_streams_lock = threading.Lock()

    def emit(_event, payload):
        request_id = payload["request_id"]
        host._chat_chunk(request_id=request_id, text="Thinking first.", is_thought=True)
        host._chat_chunk(request_id=request_id, text="Answer.")
        host._chat_done(request_id=request_id, text="Answer.")

    host.emit = emit  # type: ignore[method-assign]

    result = list(host._make_chat_send_fn()([{"role": "user", "content": "hi"}]))

    assert result == [
        {"type": "chunk", "text": "Thinking first.", "is_thought": True},
        "Answer.",
    ]


def test_chat_stream_preserves_progress_without_flattening_into_answer() -> None:
    """Verify chat progress chunks stay display-only and do not pollute answer text."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._all_conversations = []
    host._chat_request_ids = iter([1])
    host._chat_streams = {}
    import threading

    host._chat_streams_lock = threading.Lock()

    def emit(_event, payload):
        request_id = payload["request_id"]
        host._chat_chunk(request_id=request_id, text="Tool loop: unified Responses.", is_progress=True)
        host._chat_chunk(request_id=request_id, text="Answer.")
        host._chat_done(request_id=request_id, text="Answer.")

    host.emit = emit  # type: ignore[method-assign]

    result = list(host._make_chat_send_fn()([{"role": "user", "content": "hi"}]))

    assert result == [
        {"type": "chunk", "text": "Tool loop: unified Responses.", "is_progress": True},
        "Answer.",
    ]


def test_chat_stream_preserves_local_work_monitor_events() -> None:
    """Live file activity stays structured until the chat UI creates its link."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._all_conversations = []
    host._chat_request_ids = iter([1])
    host._chat_streams = {}
    import threading

    host._chat_streams_lock = threading.Lock()
    local_work = {
        "tool": "read_file",
        "path": "C:/repo/notes.txt",
        "relative_path": "notes.txt",
        "phase": "started",
    }

    def emit(_event, payload):
        request_id = payload["request_id"]
        host._chat_chunk(request_id=request_id, local_work=local_work)
        host._chat_chunk(request_id=request_id, text="Answer.")
        host._chat_done(request_id=request_id, text="Answer.")

    host.emit = emit  # type: ignore[method-assign]

    result = list(host._make_chat_send_fn()([{"role": "user", "content": "hi"}]))

    assert result == [
        {"type": "chunk", "text": "", "local_work": local_work},
        "Answer.",
    ]


def test_live_file_approval_shows_chat_and_bubble() -> None:
    """Verify live file approvals render in chat and bubble together."""
    from runtime.workers.ui_host import QtProtocolHost

    class Chat:
        def __init__(self) -> None:
            self.requests: list[dict] = []

        def isVisible(self) -> bool:
            return True

        def request_live_file_approval(self, request: dict) -> dict:
            self.requests.append(request)
            return {"shown": True}

    class Overlay:
        def __init__(self) -> None:
            self.notices: list[str] = []

        def notify_agent_approval(self, text: str, **kwargs) -> dict:
            self.notices.append(text)
            kwargs["on_approve"]()
            return {"shown": True, "actionable": True}

    chat = Chat()
    overlay = Overlay()
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._chat = chat
    host._show_chat = lambda force_new=False: {"shown": True}  # type: ignore[method-assign]
    host._ensure_overlay = lambda: overlay  # type: ignore[method-assign]

    result = host._live_file_approval_request(
        approval_id="file-1",
        action="edit_file",
        path="note.txt",
        details={"old_chars": 4, "new_chars": 8, "diff": "--- a/note.txt\n+++ b/note.txt\n-old\n+new text"},
    )

    assert result == {"approved": True, "feedback": "", "surface": "bubble"}
    assert len(chat.requests) == 1
    assert chat.requests[0]["approval_id"] == "file-1"
    assert overlay.notices
    assert "Why:" in overlay.notices[0]
    assert "Target:" in overlay.notices[0]
    assert "Diff: +1 -1 lines" in overlay.notices[0]


def test_live_file_approval_uses_bubble_when_chat_is_not_visible() -> None:
    """Verify live file approvals fall back to actionable bubble buttons."""
    from runtime.workers.ui_host import QtProtocolHost

    class Chat:
        def isVisible(self) -> bool:
            return False

    class Overlay:
        def __init__(self) -> None:
            self.notices: list[str] = []

        def notify_agent_approval(self, text: str, **kwargs) -> dict:
            self.notices.append(text)
            kwargs["on_approve"]()
            return {"shown": True, "actionable": True}

    overlay = Overlay()
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._chat = Chat()
    host._show_chat = lambda force_new=False: {"shown": False}  # type: ignore[method-assign]
    host._ensure_overlay = lambda: overlay  # type: ignore[method-assign]

    result = host._live_file_approval_request(approval_id="file-1", action="edit_file", path="note.txt")

    assert result == {"approved": True, "feedback": "", "surface": "bubble"}
    assert overlay.notices
    assert "edit_file" in overlay.notices[0]
    assert "note.txt" in overlay.notices[0]


def test_live_file_approval_can_be_resolved_from_chat_while_bubble_is_shown() -> None:
    """Verify the chat approval panel can resolve a request also shown in the bubble."""
    from runtime.workers.ui_host import QtProtocolHost

    class Chat:
        def __init__(self) -> None:
            self.callback = None
            self.resolver = None

        def isVisible(self) -> bool:
            return True

        def request_live_file_approval(self, request: dict) -> dict:
            self.callback = request.get("_on_decision")
            register = request.get("_register_resolver")
            if callable(register):
                register(lambda *_args: None)
            return {"shown": True}

    class Overlay:
        def __init__(self, chat: Chat) -> None:
            self.chat = chat
            self.notices: list[str] = []

        def notify_agent_approval(self, text: str, **_kwargs) -> dict:
            self.notices.append(text)
            self.chat.callback({"approved": False, "feedback": "Use a smaller patch.", "shown": True})
            return {"shown": True, "actionable": True}

    chat = Chat()
    overlay = Overlay(chat)
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._chat = chat
    host._ensure_overlay = lambda: overlay  # type: ignore[method-assign]
    host._show_chat = lambda force_new=False: {"shown": True}  # type: ignore[method-assign]
    host._ensure_bubble = lambda: type("Bubble", (), {"start_thinking": lambda self: None})()  # type: ignore[method-assign]

    result = host._live_file_approval_request(approval_id="file-1", action="edit_file", path="note.txt")

    assert result == {
        "approved": False,
        "feedback": "Use a smaller patch.",
        "surface": "chat",
    }
    assert overlay.notices


def test_agent_approval_bubble_notice_does_not_timeout() -> None:
    """Verify unresolved approval bubble notices stay actionable indefinitely."""
    import pytest

    pytest.importorskip("PySide6")
    from ui.overlay import IconOverlay

    class Timer:
        def __init__(self) -> None:
            self.interval = None
            self.starts = 0
            self.stops = 0

        def stop(self) -> None:
            self.stops += 1

        def setInterval(self, value: int) -> None:  # noqa: N802 - Qt-style fake
            self.interval = value

        def start(self) -> None:
            self.starts += 1

    class Icon:
        def show(self) -> None:
            pass

        def raise_(self) -> None:
            pass

        def isVisible(self) -> bool:  # noqa: N802 - Qt-style fake
            return True

    class Bubble:
        def __init__(self) -> None:
            self.notice = None

        def show_notice(self, text: str, *, timeout_ms: int, actions: list) -> None:
            self.notice = {"text": text, "timeout_ms": timeout_ms, "actions": actions}

    bubble = Bubble()
    overlay = IconOverlay.__new__(IconOverlay)
    overlay._bubble = bubble
    timer = Timer()
    overlay._icon_hide_timer = timer
    overlay._icon_label = Icon()
    # _run_bubble_after_icon shows the notice inline only when the icon is
    # already up; satisfy its gating state so the action runs synchronously.
    overlay._icon_ready_for_bubble = True
    overlay._pending_bubble_actions = []
    overlay._pending_bubble_flush_scheduled = False
    overlay._show_icon = lambda: None  # type: ignore[method-assign]
    overlay._position_bubble_next_to_icon = lambda: None  # type: ignore[method-assign]
    overlay._set_icon_pixmap = lambda _name: None  # type: ignore[method-assign]
    overlay._icon_backstop_ms = lambda: 4000  # type: ignore[method-assign]

    result = overlay.notify_agent_approval(
        "Permission needed.",
        on_approve=lambda: None,
        on_feedback=lambda: None,
        on_decline=lambda: None,
    )

    assert result == {"shown": True, "actionable": True}
    assert bubble.notice["timeout_ms"] == 0
    assert [label for label, _callback in bubble.notice["actions"]] == ["Approve", "Alternate option", "Decline"]
    assert timer.starts == 0
    assert timer.stops >= 1


def test_active_history_includes_context_and_attachment_refs() -> None:
    """Verify selected conversation replay includes ambient context and refs."""
    from runtime.workers.ui_host import QtProtocolHost

    attachment = {
        "id": "att_1",
        "kind": "image",
        "source": "external_path",
        "path": r"C:\Users\TestUser\Downloads\shot.png",
        "name": "shot.png",
        "mime": "image/png",
    }
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._active_project_id = "general"
    host._all_conversations = [
        {
            "project_id": "general",
            "context": "Original ambient context",
            "messages": [
                {"role": "user", "content": "what is this?", "attachments": [attachment]},
                {"role": "assistant", "content": "a screenshot"},
            ],
        }
    ]

    history = host._chat_active_history()

    assert history["context"] == "Original ambient context"
    assert history["history"][0]["attachments"] == [attachment]
    assert "image_base64" not in history["history"][0]
