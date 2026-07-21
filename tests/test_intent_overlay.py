"""Tests for test intent overlay."""

import os
import sys

import pytest


@pytest.fixture
def qapp():
    """Return a QApplication for intent overlay widget tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    yield app
    app.processEvents()


def _close_overlay_if_valid(overlay, app) -> None:
    """Close a Qt overlay unless WA_DeleteOnClose already destroyed it."""
    shiboken6 = pytest.importorskip("shiboken6", reason="shiboken6 not installed")
    if shiboken6.isValid(overlay):
        overlay.close()
        app.processEvents()


def test_custom_prompt_wraps_and_grows_vertically(qapp, monkeypatch):
    """Long freeform prompts wrap into additional visible editor lines."""
    from PySide6.QtCore import Qt

    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay.show()
        overlay._enter_custom_mode(drop_trigger_key=False)
        qapp.processEvents()
        initial_overlay_h = overlay.height()

        overlay._input_line.setText("wrapped prompt " * 80)
        qapp.processEvents()
        overlay._resize_prompt_input()

        assert overlay._input_line.height() > intent_overlay._INPUT_MIN_H
        assert overlay.height() > initial_overlay_h
        assert overlay._input_line.horizontalScrollBar().maximum() == 0
        assert overlay._input_line.height() <= overlay._prompt_input_max_height()
        assert (
            overlay._input_line.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_custom_prompt_single_line_has_vertical_room(qapp, monkeypatch):
    """The prompt editor leaves room for complete glyphs and descenders."""
    from PySide6.QtGui import QFontMetrics

    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay.show()
        overlay._enter_custom_mode(drop_trigger_key=False)
        overlay._input_line.setText("Summarize this. glyphs: gypq")
        qapp.processEvents()

        font_height = QFontMetrics(overlay._input_line.font()).lineSpacing()
        assert overlay._input_line.document().documentMargin() == 0
        assert overlay._input_line.viewport().height() >= font_height + 8
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_custom_prompt_scrolls_only_after_overlay_fills_screen(qapp, monkeypatch):
    """The editor uses available screen height before showing a scrollbar."""
    from PySide6.QtCore import QRect, Qt

    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._screen_geometry = QRect(0, 0, 800, 360)
        overlay.show()
        overlay._enter_custom_mode(drop_trigger_key=False)
        overlay._input_line.setText("very long wrapped prompt " * 300)
        qapp.processEvents()
        overlay._resize_prompt_input()

        assert overlay.height() <= 360 - intent_overlay._SCREEN_MARGIN
        assert overlay._input_line.height() == overlay._prompt_input_max_height()
        assert (
            overlay._input_line.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        assert overlay._input_line.verticalScrollBar().maximum() > 0
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


def test_custom_prompt_enter_submits_and_shift_enter_adds_line(qapp, monkeypatch):
    """Enter sends the prompt while Shift+Enter remains available for newlines."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    chosen: list[tuple[str, str]] = []
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    try:
        overlay.show()
        overlay._enter_custom_mode(drop_trigger_key=False)
        overlay._input_line.setText("first line")
        overlay._input_line.moveCursor(overlay._input_line.textCursor().MoveOperation.End)
        QTest.keyClick(
            overlay._input_line,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.ShiftModifier,
        )
        QTest.keyClicks(overlay._input_line, "second line")
        assert overlay._input_line.text() == "first line\nsecond line"

        QTest.keyClick(overlay._input_line, Qt.Key.Key_Return)
        qapp.processEvents()

        assert chosen == [("S", "first line\nsecond line")]
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


@pytest.mark.parametrize("clipboard_kind", ["image", "file"])
def test_custom_prompt_paste_attaches_non_text_clipboard_context(
    qapp,
    monkeypatch,
    tmp_path,
    clipboard_kind,
):
    """Ctrl+V attaches copied images/files without inserting paths in the prompt."""
    from PySide6.QtCore import QEvent, QMimeData, Qt, QUrl
    from PySide6.QtGui import QImage, QKeyEvent

    import config
    import ui.intent_overlay as intent_overlay

    mime = QMimeData()
    if clipboard_kind == "image":
        image = QImage(3, 2, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.magenta)
        mime.setImageData(image)
        mime.setText("https://example.test/source-image")
    else:
        path = tmp_path / "copied.txt"
        path.write_text("copied file body", encoding="utf-8")
        mime.setUrls([QUrl.fromLocalFile(str(path))])

    class Clipboard:
        @staticmethod
        def mimeData():
            return mime

    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay.QApplication, "clipboard", staticmethod(Clipboard))
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    pasted: list[list[tuple[str, str, str]]] = []
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.context_items_pasted.connect(lambda items: pasted.append(list(items)))
    try:
        overlay.show()
        overlay._enter_custom_mode(drop_trigger_key=False)
        overlay._input_line.setText("describe this")
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier,
            "v",
        )

        assert overlay.eventFilter(overlay._input_line, event) is True
        assert overlay._input_line.text() == "describe this"
        assert len(pasted) == 1
        assert pasted[0][0][2] == clipboard_kind.replace("file", "text")
        if clipboard_kind == "image":
            assert pasted[0][0][0] == "Pasted image"
        else:
            assert pasted[0][0][0] == "copied.txt"
            assert pasted[0][0][1] == "copied file body"
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_context_preview_entries_expand_item_sources(monkeypatch):
    """Verify one App chip can show multiple detected source previews."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[
            {
                "id": "ambient",
                "key": "1",
                "label": "App",
                "state": "on",
                "sources": [
                    {"label": "Notepad", "preview": "notepad body"},
                    {"label": "demo.py", "preview": "VS Code paragraph"},
                ],
            }
        ],
    )
    try:
        assert overlay._context_preview_entries() == [
            ("App 1: Notepad", "notepad body", "ambient", "Notepad"),
            ("App 2: demo.py", "VS Code paragraph", "ambient", "demo.py"),
        ]
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_prompt_input_grabs_keyboard_on_windows(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    grabs: list[QWidget] = []
    releases: list[QWidget] = []
    force_foreground_calls: list[bool] = []

    def grab_keyboard(self):
        grabs.append(self)

    def release_keyboard(self):
        releases.append(self)

    monkeypatch.setattr(intent_overlay, "_IS_WIN", True)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(
        intent_overlay.IntentOverlay,
        "_win_force_foreground",
        lambda self: force_foreground_calls.append(True),
    )
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._enter_custom_mode()

        assert overlay._input_line.isHidden() is False
        assert force_foreground_calls == [True]
        assert grabs == [overlay._input_line]
        assert overlay._input_grabbed_keyboard is True

        overlay._unhook()

        assert releases == [overlay._input_line]
        assert overlay._input_grabbed_keyboard is False
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_key_debug_avoids_prompt_text(monkeypatch, capsys):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_DEBUG_KEYS", True)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._custom_mode = True
        overlay._input_line.show()
        overlay._debug_key(
            "test",
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_A,
                Qt.KeyboardModifier.NoModifier,
                "a",
            ),
        )

        err = capsys.readouterr().err
        assert "[wisp-intent]" in err
        assert "text=printable-len:1" in err
        assert " a " not in err
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_auto_custom_prompt_keeps_first_typed_key(monkeypatch):
    """Verify blank custom key starts typing mode without dropping first input."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": ""}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._enter_auto_custom_mode()

        assert overlay._custom_mode is True
        assert overlay._input_line.isHidden() is False
        assert overlay._drop_next_keypress is False
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_prefilled_custom_prompt_keeps_context_keys(monkeypatch):
    """A prefilled voice prompt should not steal context toggle keys."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    chosen: list[tuple[str, str]] = []
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "memory", "key": "1", "label": "Memory", "state": "off"}],
        initial_custom_text="voice prompt",
        focus_overlay=True,
    )
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    try:
        overlay._enter_prefilled_custom_mode()

        assert overlay._prefilled_custom_mode is True
        assert overlay._custom_mode is False
        assert overlay._input_line.text() == "voice prompt"
        assert overlay._input_line.isHidden() is False

        overlay.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier, "1")
        )
        assert overlay.context_choices()[0]["state"] == "on"

        overlay.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        )
        assert chosen == [("S", "voice prompt")]
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_cancel_if_focus_leaves(monkeypatch):
    """Verify clicking away cancels a pending custom prompt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    cancelled: list[bool] = []
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.cancelled.connect(lambda: cancelled.append(True))
    try:
        overlay.show()
        overlay._enter_custom_mode()
        monkeypatch.setattr(
            intent_overlay.QApplication,
            "focusWidget",
            staticmethod(lambda: overlay._input_line),
        )

        overlay._cancel_if_focus_left()

        assert cancelled == []
        assert overlay._handled is False

        monkeypatch.setattr(
            intent_overlay.QApplication,
            "focusWidget",
            staticmethod(lambda: None),
        )

        overlay._cancel_if_focus_left()

        assert cancelled == [True]
        assert overlay._handled is True
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_close_emits_cancelled_once(qapp):
    """Verify lifecycle closes report cancellation to reset overlay state."""
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    cancelled: list[bool] = []
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.cancelled.connect(lambda: cancelled.append(True))
    try:
        overlay.show()
        overlay.close()

        assert cancelled == [True]
        assert overlay._handled is True

        assert overlay._cancel_if_unhandled() is False
        qapp.processEvents()

        assert cancelled == [True]
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_pending_selection_close_cancels_not_chosen(qapp, monkeypatch):
    """Verify a vanished picker cancels if the highlighted row was not emitted yet."""
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    callbacks = []
    chosen: list[tuple[str, str]] = []
    cancelled: list[bool] = []
    config.CALLER_ROWS[:] = [
        {
            "intents": [{"key": "w", "label": "What?", "prompt": "What is this?"}],
            "custom_key": "s",
        }
    ]
    monkeypatch.setattr(
        intent_overlay.QTimer,
        "singleShot",
        staticmethod(lambda _delay, callback: callbacks.append(callback)),
    )
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    overlay.cancelled.connect(lambda: cancelled.append(True))
    try:
        overlay._select(0)
        overlay.close()
        qapp.processEvents()

        assert cancelled == [True]
        assert chosen == []

        assert callbacks
        callbacks[0]()

        assert cancelled == [True]
        assert chosen == []
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, qapp)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_translates_default_custom_prompt_label(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    i18n.set_language(app=app)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s", "custom_label": ""}]
    try:
        row = intent_overlay._build_rows(0)[-1]

        assert row["label"] == i18n.t("Custom prompt")
        assert row["label"] != "Custom prompt"
    finally:
        config.CALLER_ROWS[:] = old_rows
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_translates_builtin_labels_but_preserves_runtime_prompt():
    """Built-in overlay copy follows the app language without changing model input."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay
    from core.prompt_i18n import caller_intent_template
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    old_language = getattr(config, "APP_LANGUAGE", "")
    english = caller_intent_template(0, 0, "English")
    traditional = caller_intent_template(0, 0, "zh-Hant")
    config.APP_LANGUAGE = "zh-Hant"
    i18n.set_language(app=app)
    config.CALLER_ROWS[:] = [{"intents": [english], "custom_key": "s", "custom_label": ""}]
    try:
        row = intent_overlay._build_rows(0)[0]

        assert row["label"] == traditional["label"]
        assert row["hint"] == traditional["hint"]
        assert row["prompt"] == english["prompt"]
    finally:
        config.CALLER_ROWS[:] = old_rows
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


def test_intent_overlay_preserves_custom_prompt_label():
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s", "custom_label": "Freeform"}]
    try:
        row = intent_overlay._build_rows(0)[-1]

        assert row["label"] == "Freeform"
    finally:
        config.CALLER_ROWS[:] = old_rows


def test_intent_overlay_context_palette_uses_theme_settings():
    """Verify context chip colors derive from the active settings theme."""
    import config
    import ui.intent_overlay as intent_overlay

    old_values = {
        "THEME_MODE": getattr(config, "THEME_MODE", "system"),
        "THEME_DARK_BG": getattr(config, "THEME_DARK_BG", "#1c1e26"),
        "THEME_DARK_SURFACE": getattr(config, "THEME_DARK_SURFACE", "#17181d"),
        "THEME_DARK_TEXT": getattr(config, "THEME_DARK_TEXT", "#e8e8f0"),
        "THEME_DARK_ACCENT": getattr(config, "THEME_DARK_ACCENT", "#8b87ff"),
    }
    try:
        config.THEME_MODE = "dark"
        config.THEME_DARK_BG = "#101820"
        config.THEME_DARK_SURFACE = "#203040"
        config.THEME_DARK_TEXT = "#f0ead6"
        config.THEME_DARK_ACCENT = "#ff3366"

        palette = intent_overlay._theme_palette()

        assert palette["ctx_on"].name().lower() == "#ff3366"
        assert palette["ctx_text"].name().lower() == "#f0ead6"
        assert palette["badge_bg"].name().lower() == "#203040"
        assert palette["bg"].name().lower() == "#101820"
    finally:
        for key, value in old_values.items():
            setattr(config, key, value)


def test_intent_overlay_custom_prompt_input_uses_theme_settings():
    """Verify custom prompt input does not stay hard-coded dark in light mode."""
    import config
    import ui.intent_overlay as intent_overlay

    old_values = {
        "THEME_MODE": getattr(config, "THEME_MODE", "system"),
        "THEME_LIGHT_BG": getattr(config, "THEME_LIGHT_BG", "#f2f2f7"),
        "THEME_LIGHT_SURFACE": getattr(config, "THEME_LIGHT_SURFACE", "#ffffff"),
        "THEME_LIGHT_TEXT": getattr(config, "THEME_LIGHT_TEXT", "#1c1c1e"),
        "THEME_LIGHT_ACCENT": getattr(config, "THEME_LIGHT_ACCENT", "#5856d6"),
    }
    try:
        config.THEME_MODE = "light"
        config.THEME_LIGHT_BG = "#eeeeee"
        config.THEME_LIGHT_SURFACE = "#fafafa"
        config.THEME_LIGHT_TEXT = "#111111"
        config.THEME_LIGHT_ACCENT = "#2255aa"

        style = intent_overlay._input_line_stylesheet().lower()

        assert "#fafafa" in style
        assert "#111111" in style
        assert "#2255aa" in style
        assert "#2a2a38" not in style
        assert "#eeeef8" not in style
    finally:
        for key, value in old_values.items():
            setattr(config, key, value)


def test_context_preview_text_is_redacted_and_trimmed(monkeypatch):
    """Verify context preview snippets are compact and privacy-safe."""
    import config
    from runtime.supervisor.flows import FlowController

    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True, raising=False)
    preview = FlowController._context_preview_text(
        "OpenAI key sk-" + ("a" * 24) + " should not be visible " + ("x" * 240),
        limit=80,
    )

    assert "[API_KEY]" in preview
    assert "sk-" not in preview
    assert len(preview) <= 80


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_fallback_context_tokens_are_unknown():
    """Verify fallback context chips do not pretend unknown estimates are zero."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    overlay = intent_overlay.IntentOverlay(context_items=None)
    try:
        choices = {item["id"]: item for item in overlay.context_choices()}
        assert choices["browser"]["tokens"] == "? tok"
        assert choices["screenshot"]["tokens"] == "? tok"
        assert choices["files"]["tokens"] == ""
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_fits_files_context_chip(qapp):
    """Verify all eight context chips fit and Files is hit-testable."""
    from PySide6.QtCore import QPoint

    from ui import intent_overlay
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(context_items=None)
    try:
        choices = overlay.context_choices()
        assert [item["id"] for item in choices] == [
            "ambient",
            "browser",
            "selection",
            "clipboard",
            "screenshot",
            "github",
            "memory",
            "files",
        ]
        assert overlay._context_chip_width() <= intent_overlay._CTX_CHIP_W
        top = intent_overlay._PAD_V + (
            intent_overlay._CONV_H if overlay._show_conversation_selector else 0
        ) + intent_overlay._CTX_TOP
        rects = overlay._context_chip_rects(top)
        assert len(rects) == 8
        assert rects[-1][0]["id"] == "files"
        assert rects[-1][1].right() <= intent_overlay._W - intent_overlay._PAD_H

        center = rects[-1][1].center()
        assert overlay._context_item_at(QPoint(center.x(), center.y()))["id"] == "files"
    finally:
        overlay.close()
        qapp.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_bottom_context_previews_resize(qapp):
    """Verify enabled context previews appear below intent rows and resize."""
    from ui import intent_overlay
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {"id": "ambient", "key": "1", "label": "App", "state": "on", "preview": "This is app context"},
            {"id": "browser", "key": "2", "label": "Browser/Web", "state": "on", "preview": "This is browser context"},
            {"id": "clipboard", "key": "4", "label": "Clipboard", "state": "off", "preview": "Hidden clipboard"},
        ]
    )
    try:
        assert overlay._context_preview_entries() == [
            ("App", "This is app context", "ambient", ""),
            ("Browser/Web", "This is browser context", "browser", ""),
        ]
        assert overlay._context_preview_height() == (
            intent_overlay._CTX_PREVIEW_TOP + intent_overlay._CTX_PREVIEW_LINE_H * 2
        )
        initial_h = overlay.height()

        overlay.update_context_items([
            {"id": "ambient", "key": "1", "label": "App", "state": "off", "preview": ""},
            {"id": "browser", "key": "2", "label": "Browser/Web", "state": "on", "preview": "Browser"},
            {"id": "selection", "key": "3", "label": "Selection", "state": "on", "preview": "Selection"},
            {"id": "clipboard", "key": "4", "label": "Clipboard", "state": "on", "preview": "Clipboard"},
            {"id": "memory", "key": "6", "label": "Memory", "state": "auto", "preview": "Memory"},
        ])

        assert overlay._context_preview_entries() == [
            ("Browser/Web", "Browser", "browser", ""),
            ("Selection", "Selection", "selection", ""),
            ("Clipboard", "Clipboard", "clipboard", ""),
        ]
        expanded_h = initial_h + intent_overlay._CTX_PREVIEW_LINE_H
        assert overlay.height() == expanded_h

        assert overlay._cycle_context_key("2") is True
        assert overlay._context_preview_entries() == [
            ("Selection", "Selection", "selection", ""),
            ("Clipboard", "Clipboard", "clipboard", ""),
            ("Memory", "Memory", "memory", ""),
        ]
        assert overlay.height() == expanded_h
    finally:
        overlay.close()
        qapp.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_cycles_context_chip(monkeypatch):
    """Verify numeric context chips cycle independently of intent rows."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "browser", "key": "2", "label": "Browser", "state": "on"}],
    )
    try:
        overlay.update_context_items(
            [{"id": "browser", "key": "2", "label": "Browser", "state": "auto", "tokens": "? tok"}]
        )
        assert overlay.context_choices()[0]["state"] == "auto"
        assert overlay.context_choices()[0]["touched"] is False

        assert overlay._cycle_context_key("2") is True
        assert overlay.context_choices()[0]["state"] == "off"
        assert overlay.context_choices()[0]["touched"] is True

        overlay.update_context_items(
            [{"id": "browser", "key": "2", "label": "Browser", "state": "auto", "tokens": "? tok"}]
        )
        assert overlay.context_choices()[0]["state"] == "off"
        assert overlay.context_choices()[0]["touched"] is True
        assert intent_overlay._context_chip_token_text(overlay.context_choices()[0]) == "? tok"

        overlay.show()
        app.processEvents()
        QTest.mouseClick(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=QPoint(
                intent_overlay._PAD_H + intent_overlay._CTX_CHIP_W // 2,
                intent_overlay._PAD_V
                + intent_overlay._CONV_H
                + intent_overlay._CTX_TOP
                + intent_overlay._CTX_CHIP_H // 2,
            ),
        )
        app.processEvents()
        assert overlay.context_choices()[0]["state"] == "on"

        assert overlay._cycle_context_key("2") is True
        assert overlay.context_choices()[0]["state"] == "off"
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_screenshot_chip_requests_snip_when_enabled_from_off(qapp):
    """Verify turning Screenshot on asks for a snip instead of silently capturing."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[{"id": "screenshot", "key": "5", "label": "Screenshot", "state": "off"}]
    )
    requested: list[bool] = []
    overlay.screenshot_snip_requested.connect(lambda: requested.append(True))
    try:
        assert overlay._cycle_context_key("5") is True
        qapp.processEvents()

        assert overlay.context_choices()[0]["state"] == "on"
        assert requested == [True]

        assert overlay._cycle_context_key("5") is True
        qapp.processEvents()

        assert overlay.context_choices()[0]["state"] == "off"
        assert requested == [True]
    finally:
        overlay.close()
        qapp.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_context_item_force_state_overrides_user_touched_choice(qapp):
    """Verify a cancelled snip can force Screenshot back off."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[{"id": "screenshot", "key": "5", "label": "Screenshot", "state": "off"}]
    )
    try:
        assert overlay._cycle_context_key("5") is True
        assert overlay.context_choices()[0]["state"] == "on"
        assert overlay.context_choices()[0]["touched"] is True

        overlay.update_context_items([
            {
                "id": "screenshot",
                "key": "5",
                "label": "Screenshot",
                "state": "off",
                "force_state": True,
            }
        ])

        assert overlay.context_choices()[0]["state"] == "off"
        assert overlay.context_choices()[0]["touched"] is False
    finally:
        overlay.close()
        qapp.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_conversation_choice_toggles_new_and_continue():
    """Verify the intent overlay exposes the selected conversation mode."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        conversation_options=[
            {"index": 1, "title": "Latest chat", "selected": True},
            {"index": 0, "title": "Older chat"},
        ],
    )
    try:
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}
        assert overlay.conversation_choice_touched() is False

        overlay._toggle_conversation_mode()
        assert overlay.conversation_choice() == {"mode": "new"}
        assert overlay.conversation_choice_touched() is True

        overlay._set_conversation_choice(0)
        assert overlay.conversation_choice() == {"mode": "continue", "index": 0}
        assert overlay.conversation_choice_touched() is True
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.parametrize("caller_idx", [0, 1])
def test_space_toggles_conversation_for_intent_and_action_overlays(qapp, caller_idx):
    """Space invokes the same new/continue toggle in either overlay."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        caller_idx=caller_idx,
        conversation_options=[{"index": 1, "title": "Previous chat"}],
    )
    try:
        assert overlay.conversation_choice() == {"mode": "new"}

        overlay.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Space,
                Qt.KeyboardModifier.NoModifier,
                " ",
            )
        )
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}

        overlay.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Space,
                Qt.KeyboardModifier.NoModifier,
                " ",
            )
        )
        assert overlay.conversation_choice() == {"mode": "new"}
        assert overlay.conversation_choice_touched() is True
    finally:
        _close_overlay_if_valid(overlay, qapp)


def test_windows_raw_space_toggle_is_not_repeated_by_qt(qapp):
    """A forwarded Windows Space press toggles only once if Qt also sees it."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        conversation_options=[{"index": 1, "title": "Previous chat"}],
    )
    try:
        assert "space" in overlay._raw_shortcut_names()

        overlay._on_raw_key("space")
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}

        overlay.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Space,
                Qt.KeyboardModifier.NoModifier,
                " ",
            )
        )
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}
    finally:
        _close_overlay_if_valid(overlay, qapp)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_conversation_row_is_split_mode_and_list(monkeypatch):
    """Verify left chat segment toggles mode and right segment opens history."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMenu

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    popups: list[list[object]] = []

    def fake_popup(self, _pos):
        """Capture menu entries without showing a native popup."""
        popups.append([action.data() for action in self.actions()])
        return None

    monkeypatch.setattr(QMenu, "popup", fake_popup)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        conversation_options=[
            {"index": 1, "title": "Latest chat"},
            {"index": 0, "title": "Older chat"},
        ],
    )
    try:
        overlay.show()
        app.processEvents()
        overlay.repaint()
        app.processEvents()

        assert overlay.conversation_choice() == {"mode": "new"}
        assert not overlay._conversation_mode_rect.isNull()
        assert not overlay._conversation_list_rect.isNull()

        QTest.mouseClick(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=overlay._conversation_list_rect.center(),
        )
        app.processEvents()
        assert popups == []
        assert overlay.conversation_choice() == {"mode": "new"}

        QTest.mouseClick(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=overlay._conversation_mode_rect.center(),
        )
        app.processEvents()
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}

        QTest.mouseClick(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=overlay._conversation_list_rect.center(),
        )
        app.processEvents()
        assert popups[-1] == [1, 0]
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_changes_restart_timeout_countdown(monkeypatch):
    """Verify context and conversation changes restart the overlay timeout."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    restarts: list[bool] = []
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "browser", "key": "2", "label": "Browser", "state": "off"}],
        conversation_options=[{"index": 1, "title": "Latest chat"}],
    )
    monkeypatch.setattr(overlay, "_restart_timer", lambda: restarts.append(True))
    try:
        assert overlay._cycle_context_key("2") is True
        overlay._toggle_conversation_mode()

        assert len(restarts) == 2
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_picker_timeout_failure_matrix_is_controlled(monkeypatch):
    """Invalid, zero, stale, and modal-owned timeout states stay controlled."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)

    # Invalid runtime config must not prevent the real picker from opening.
    monkeypatch.setattr(config, "INTENT_OVERLAY_TIMEOUT_MS", "not-a-duration")
    invalid = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        assert invalid._overlay_timeout_ms == intent_overlay._AUTO_CLOSE_MS
        assert invalid._timer.isActive()

        # A state transition must retire the old timer instead of allowing it
        # to cancel a custom prompt later.
        invalid._custom_mode = True
        invalid._restart_timer()
        assert not invalid._timer.isActive()
    finally:
        invalid.close()
        app.processEvents()

    # Zero is an intentional no-timeout mode and remains inactive across use.
    monkeypatch.setattr(config, "INTENT_OVERLAY_TIMEOUT_MS", 0)
    persistent = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        assert persistent._overlay_timeout_ms == 0
        assert not persistent._timer.isActive()
        persistent._note_interaction()
        assert not persistent._timer.isActive()
    finally:
        persistent.close()
        app.processEvents()

    # A modal taking focus cannot make timeout cancellation re-entrant or emit
    # cancellation more than once.
    monkeypatch.setattr(config, "INTENT_OVERLAY_TIMEOUT_MS", 50)
    modal_owned = intent_overlay.IntentOverlay(caller_idx=0)
    modal = QDialog()
    cancelled: list[bool] = []
    modal_owned.cancelled.connect(lambda: cancelled.append(True))
    try:
        modal.setModal(True)
        modal.show()
        app.processEvents()
        modal_owned._cancel()
        modal_owned._cancel()
        assert cancelled == [True]
    finally:
        modal.close()
        _close_overlay_if_valid(modal_owned, app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_defaults_to_new_when_history_has_no_active_selection():
    """Verify loaded history does not imply continuation on app start."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        conversation_options=[
            {"index": 1, "title": "Latest chat"},
            {"index": 0, "title": "Older chat"},
        ],
    )
    try:
        assert overlay.conversation_choice() == {"mode": "new"}

        overlay._toggle_conversation_mode()
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_project_choice_filters_conversations():
    """Verify project selection scopes the intent overlay chat list."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        project_options=[
            {"id": "general", "name": "General"},
            {"id": "proj-1", "name": "Personal OS"},
        ],
        active_project_id="proj-1",
        conversation_options=[
            {"index": 2, "title": "Project chat", "project_id": "proj-1", "selected": True},
            {"index": 1, "title": "General chat", "project_id": "general"},
        ],
    )
    try:
        assert overlay.project_choice() == {"mode": "existing", "project_id": "proj-1"}
        assert overlay.conversation_choice() == {"mode": "continue", "index": 2}
        assert [item["index"] for item in overlay._filtered_conversation_options()] == [2]

        overlay._set_project_choice("general")

        assert overlay.project_choice() == {"mode": "existing", "project_id": "general"}
        assert overlay.conversation_choice() == {"mode": "new"}
        assert [item["index"] for item in overlay._filtered_conversation_options()] == [1]
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_dedupes_raw_and_qt_context_key(monkeypatch):
    """Verify a Windows raw-hook context key is not immediately toggled again by Qt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "browser", "key": "2", "label": "Browser", "state": "on"}],
    )
    try:
        overlay._on_raw_key("2")

        assert overlay.context_choices()[0]["state"] == "off"
        assert overlay._is_duplicate_qt_context_key("2") is True
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_linux_uses_qt_keys_without_pynput(monkeypatch):
    """Verify Linux overlay-local shortcuts do not start a second native listener."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import builtins

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    original_import = builtins.__import__
    grabs: list[QWidget] = []
    releases: list[QWidget] = []

    def guarded_import(name, *args, **kwargs):
        """Fail if showing the overlay tries to import pynput."""
        if name == "pynput" or name.startswith("pynput."):
            raise AssertionError("Linux intent overlay should rely on Qt key events")
        return original_import(name, *args, **kwargs)

    def grab_keyboard(self):
        """Record Qt-local keyboard grabs."""
        grabs.append(self)

    def release_keyboard(self):
        """Record Qt-local keyboard releases."""
        releases.append(self)

    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "browser", "key": "2", "label": "Browser", "state": "on"}],
    )
    try:
        overlay.show()
        app.processEvents()

        assert overlay._kb_hook is None
        assert overlay.windowFlags() & Qt.WindowType.WindowType_Mask == Qt.WindowType.Window
        assert grabs == [overlay]
        assert overlay._overlay_grabbed_keyboard is True
        QTest.keyClick(overlay, Qt.Key.Key_2)
        app.processEvents()
        assert overlay.context_choices()[0]["state"] == "off"

        overlay._unhook()

        assert releases == [overlay]
        assert overlay._overlay_grabbed_keyboard is False
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_linux_visible_picker_accepts_letter_shortcut(monkeypatch):
    """Verify Linux visible intent picker handles letter shortcuts through Qt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    chosen: list[tuple[str, str]] = []

    def grab_keyboard(self):
        """Allow Qt-local keyboard grabs without touching the host desktop."""

    def release_keyboard(self):
        """Allow Qt-local keyboard releases without touching the host desktop."""

    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [
        {
            "intents": [
                {"key": "w", "label": "Write", "hint": "", "prompt": "write this"},
            ],
            "custom_key": "s",
        }
    ]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    try:
        overlay.show()
        app.processEvents()

        QTest.keyClick(overlay, Qt.Key.Key_W)
        QTest.qWait(120)
        app.processEvents()

        assert chosen == [("W", "write this")]
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_frozen_linux_avoids_keyboard_grabs(monkeypatch):
    """Verify frozen Linux builds do not use native Qt keyboard grabs."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    chosen: list[tuple[str, str]] = []
    grabs: list[QWidget] = []
    releases: list[QWidget] = []

    def grab_keyboard(self):
        """Record unexpected Qt-native keyboard grabs."""
        grabs.append(self)

    def release_keyboard(self):
        """Record unexpected Qt-native keyboard releases."""
        releases.append(self)

    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(intent_overlay, "_IS_LINUX", True)
    monkeypatch.setattr(intent_overlay.sys, "frozen", True, raising=False)
    monkeypatch.delenv("WISP_LINUX_QT_KEYBOARD_GRAB", raising=False)
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [
        {
            "intents": [
                {"key": "w", "label": "Write", "hint": "", "prompt": "write this"},
            ],
            "custom_key": "s",
        }
    ]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    try:
        overlay.show()
        app.processEvents()

        QTest.keyClick(overlay, Qt.Key.Key_W)
        QTest.qWait(120)
        app.processEvents()

        assert chosen == [("W", "write this")]
        assert grabs == []
        assert releases == []
    finally:
        config.CALLER_ROWS[:] = old_rows
        _close_overlay_if_valid(overlay, app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_frozen_linux_custom_prompt_avoids_keyboard_grabs(monkeypatch):
    """Verify frozen Linux custom prompt focus does not use native Qt grabs."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    grabs: list[QWidget] = []
    releases: list[QWidget] = []

    def grab_keyboard(self):
        """Record unexpected Qt-native keyboard grabs."""
        grabs.append(self)

    def release_keyboard(self):
        """Record unexpected Qt-native keyboard releases."""
        releases.append(self)

    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(intent_overlay, "_IS_LINUX", True)
    monkeypatch.setattr(intent_overlay.sys, "frozen", True, raising=False)
    monkeypatch.delenv("WISP_LINUX_QT_KEYBOARD_GRAB", raising=False)
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay.show()
        app.processEvents()

        QTest.keyClick(overlay, Qt.Key.Key_S)
        QTest.qWait(120)
        app.processEvents()

        assert overlay._custom_mode is True
        assert overlay._input_line.isHidden() is False
        assert overlay._input_grabbed_keyboard is False
        assert grabs == []
        assert releases == []
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_linux_moves_keyboard_grab_to_custom_input(monkeypatch):
    """Verify Linux custom prompt typing gets a Qt-local keyboard grab."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    grabs: list[QWidget] = []
    releases: list[QWidget] = []

    def grab_keyboard(self):
        """Record Qt-local keyboard grabs."""
        grabs.append(self)

    def release_keyboard(self):
        """Record Qt-local keyboard releases."""
        releases.append(self)

    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    monkeypatch.setattr(intent_overlay, "_IS_MAC", False)
    monkeypatch.setattr(QWidget, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QWidget, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._focus_overlay()
        overlay._enter_custom_mode()

        assert grabs == [overlay, overlay._input_line]
        assert releases == [overlay]
        assert overlay._overlay_grabbed_keyboard is False
        assert overlay._input_grabbed_keyboard is True

        overlay._unhook()

        assert releases == [overlay, overlay._input_line]
        assert overlay._input_grabbed_keyboard is False
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_apply_intent_context_choices_updates_caller_policy():
    """Verify overlay context choices become real per-prompt caller policy."""
    from runtime.supervisor.flows import FlowController

    caller = {
        "context_ambient": True,
        "context_documents_mode": "off",
        "context_browser_mode": "auto",
        "context_github_mode": "off",
        "context_memory_mode": "on",
        "context_screenshot": "auto",
        "file_access": "ask",
    }

    updated = FlowController._apply_intent_context_choices(
        caller,
        [
            {"id": "browser", "state": "off"},
            {"id": "selection", "state": "off"},
            {"id": "github", "state": "auto"},
            {"id": "memory", "state": "auto"},
            {"id": "files", "state": "off"},
            {"id": "ambient", "state": "on", "default_state": "off", "touched": True},
        ],
    )

    assert updated["context_documents_mode"] == "auto"
    assert updated["context_browser_mode"] == "off"
    assert updated["context_github_mode"] == "model"
    assert updated["_context_selection_enabled"] is False
    assert updated["context_memory_mode"] == "model"
    assert updated["file_access"] == "off"

    unchanged = FlowController._apply_intent_context_choices(
        caller,
        [{"id": "ambient", "state": "on", "default_state": "on"}],
    )
    assert unchanged["context_documents_mode"] == "off"


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_remove_buttons_remove_rows_and_disable_groups(qapp):
    """Verify per-row X removal drops sources and an emptied group turns off."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {
                "id": "ambient",
                "key": "1",
                "label": "App",
                "state": "on",
                "sources": [
                    {"label": "Doc A", "preview": "alpha"},
                    {"label": "Doc B", "preview": "beta"},
                ],
            },
            {"id": "clipboard", "key": "4", "label": "Clipboard", "state": "on", "preview": "Clip"},
        ]
    )
    removed: list[tuple[str, str]] = []
    overlay.context_source_removed.connect(
        lambda item_id, source_id: removed.append((item_id, source_id))
    )
    try:
        overlay._remove_context_entry("ambient", "Doc A")
        assert removed == [("ambient", "Doc A")]
        assert ("App 1: Doc B", "beta", "ambient", "Doc B") in overlay._context_preview_entries()
        choices = {c["id"]: c for c in overlay.context_choices()}
        assert choices["ambient"]["state"] == "on"

        overlay._remove_context_entry("ambient", "Doc B")
        assert removed == [("ambient", "Doc A"), ("ambient", "Doc B")]
        choices = {c["id"]: c for c in overlay.context_choices()}
        assert choices["ambient"]["state"] == "off"
        assert choices["ambient"]["touched"] is True

        overlay._remove_context_entry("clipboard", "")
        choices = {c["id"]: c for c in overlay.context_choices()}
        assert choices["clipboard"]["state"] == "off"
        assert choices["clipboard"]["touched"] is True
        assert overlay._context_preview_entries() == []
    finally:
        _close_overlay_if_valid(overlay, qapp)


def test_intent_overlay_stale_selection_toggle_skips_interactive_capture(qapp):
    """Verify enabling a stale Selection chip does not start a new capture."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {
                "id": "selection",
                "key": "3",
                "label": "Selection",
                "available": True,
                "state": "off",
                "stale": True,
                "tokens": "~12 tok",
                "preview": "earlier words",
            }
        ]
    )
    captures: list[str] = []
    overlay.selection_capture_requested.connect(captures.append)
    try:
        assert overlay._cycle_context_key("3") is True
        qapp.processEvents()
        selection = overlay.context_choices()[0]
        assert selection["state"] == "on"
        assert captures == []
        assert ("Selection", "earlier words", "selection", "") in overlay._context_preview_entries()
    finally:
        _close_overlay_if_valid(overlay, qapp)


def test_intent_overlay_selection_toggle_can_disable_interactive_capture(qapp):
    """Verify Linux-style Selection chips toggle without requesting a new selection."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {
                "id": "selection",
                "key": "3",
                "label": "Selection",
                "available": True,
                "state": "off",
                "capture_on_enable": False,
                "tokens": "~12 tok",
                "preview": "last selected words",
            }
        ]
    )
    captures: list[str] = []
    overlay.selection_capture_requested.connect(captures.append)
    try:
        assert overlay._cycle_context_key("3") is True
        qapp.processEvents()
        selection = overlay.context_choices()[0]
        assert selection["state"] == "on"
        assert captures == []
        assert ("Selection", "last selected words", "selection", "") in overlay._context_preview_entries()
    finally:
        _close_overlay_if_valid(overlay, qapp)


def test_intent_overlay_previews_wrap_to_two_lines(qapp):
    """Verify long context previews paint on two lines and short ones on one."""
    from PySide6.QtGui import QFontMetrics

    from ui import intent_overlay
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {
                "id": "clipboard",
                "key": "4",
                "label": "Clipboard",
                "state": "on",
                "preview": "word " * 60,
            },
        ]
    )
    try:
        rows = overlay._context_preview_layout()
        assert len(rows) == 1
        assert len(rows[0][1]) == 2
        assert overlay._context_preview_height() == (
            intent_overlay._CTX_PREVIEW_TOP + intent_overlay._CTX_PREVIEW_LINE_H * 2
        )
        fm = QFontMetrics(overlay._preview_value_font())
        assert overlay._preview_wrap_lines(fm, "tiny", 400) == ["tiny"]
        two = overlay._preview_wrap_lines(fm, "alpha beta " * 40, 120)
        assert len(two) == 2
        assert two[0]
    finally:
        _close_overlay_if_valid(overlay, qapp)


def test_selection_context_chip_can_start_capture_when_empty(qapp):
    """Verify empty Selection metadata does not block the capture chip."""
    from ui.intent_overlay import IntentOverlay

    overlay = IntentOverlay(
        context_items=[
            {
                "id": "selection",
                "key": "3",
                "label": "Selection",
                "available": True,
                "state": "on",
                "tokens": "~12 tok",
            }
        ]
    )
    try:
        overlay.update_context_items([
            {
                "id": "selection",
                "key": "3",
                "label": "Selection",
                "available": False,
                "state": "off",
                "tokens": "",
            }
        ])
        selection = overlay.context_choices()[0]
        assert selection["state"] == "on"
        assert selection["touched"] is False
        assert selection["tokens"] == ""

        assert overlay._cycle_context_key("3") is True
        selection = overlay.context_choices()[0]
        assert selection["state"] == "off"
        assert selection["touched"] is True
        assert overlay._cycle_context_key("3") is True
        selection = overlay.context_choices()[0]
        assert selection["state"] == "on"
        assert selection["touched"] is True
    finally:
        overlay.close()
        qapp.processEvents()
