from __future__ import annotations


def test_wayland_desktop_uses_xwayland_for_positioned_overlay() -> None:
    """The floating overlay needs global coordinates, unlike native capture."""
    from runtime.workers import ui_host

    environment = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}

    assert ui_host._configure_linux_ui_platform(environment, platform="linux") == "xcb"
    assert environment["QT_QPA_PLATFORM"] == "xcb"


def test_ui_platform_respects_native_wayland_opt_in() -> None:
    """Users can retain the Qt Wayland backend explicitly."""
    from runtime.workers import ui_host

    environment = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "WISP_UI_PLATFORM": "wayland"}

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
    }
    monkeypatch.setattr(ui_host, "t", lambda text: translations.get(text, text))

    assert ui_host._translate_health_text(
        "LLM test failed: LLM route uses chatgpt but you are not logged in."
    ) == "LLM \u6e2c\u8a66\u5931\u6557\uff1aLLM \u8def\u7531\u4f7f\u7528 chatgpt\uff0c\u4f46\u4f60\u5c1a\u672a\u767b\u5165\u3002"
    assert (
        ui_host._translate_health_text("Microphone permission: unavailable.")
        == "\u9ea5\u514b\u98a8\u6b0a\u9650\uff1a\u7121\u6cd5\u4f7f\u7528\u3002"
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
        "Local voice is still warming up. Try again when Wisp says local speech is ready.",
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


def test_agent_owned_chat_is_mirrored_into_wisp_history_with_live_activity() -> None:
    """A remote-owned turn must still leave a complete local Wisp transcript."""
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
    monkeypatch.delenv("WISP_MACOS_UI_QUARTZ_SNIP_APP_REGION", raising=False)
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


def test_wisp_owned_harness_reply_clears_provider_continuation() -> None:
    """Switching continuity to Wisp must not later resume a stale agent session."""
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
            "conversation_owner": "wisp",
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
    assert len(persisted) == 2


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
    """Codex-owned pickers must not offer native Wisp or Claude history."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._conversation_scope_key = "codex"
    host._active_conversation_idx = 0
    host._all_conversations = [
        {
            "messages": [{"role": "user", "content": "native"}],
            "project_id": "general",
            "conversation_scope": "wisp",
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
    """A route switch starts a new record instead of appending to native Wisp."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._conversation_scope_key = "codex"
    host._active_conversation_idx = 0
    host._active_project_id = "general"
    host._all_conversations = [
        {
            "messages": [{"role": "user", "content": "native"}],
            "project_id": "general",
            "conversation_scope": "wisp",
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
