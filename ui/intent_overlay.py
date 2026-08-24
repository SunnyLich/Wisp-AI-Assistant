"""
ui/intent_overlay.py — Compact intent picker shown on Ctrl+Q.

Small floating widget centred on screen — no background dim.
Rows are built dynamically from config.INTENT_ROWS plus a fixed
Custom Prompt row (config.HOTKEY_CUSTOM_PROMPT_KEY).
Press the matching key to pick, Escape to cancel.
"""
from __future__ import annotations

import os
import re
import sys

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QPlainTextEdit, QToolTip, QWidget

import config
from core.prompt_i18n import localize_intent_if_default
from core.system.paths import ASSETS_DIR
from ui.i18n import current_language, t
from ui.shared.theme import show_tooltip_text

_IS_WIN = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"
_IS_LINUX = sys.platform.startswith("linux")
_DEBUG_KEYS = os.environ.get("OPENWAND_INTENT_KEY_DEBUG", "0").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def _configured_overlay_timeout_ms(value: object) -> int:
    """Return a safe intent-picker timeout for malformed runtime config."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return _AUTO_CLOSE_MS


def _key_name(key: int) -> str:
    """Handle key name for UI intent overlay."""
    try:
        return Qt.Key(key).name
    except Exception:
        return str(key)


def _safe_text_desc(text: str) -> str:
    """Handle safe text desc for UI intent overlay."""
    if text == "":
        return "empty"
    if text == " ":
        return "space"
    if text.isspace():
        return "whitespace:" + ",".join(str(ord(ch)) for ch in text)
    return f"printable-len:{len(text)}"


def _event_type_name(event) -> str:
    """Handle event type name for UI intent overlay."""
    try:
        return event.type().name
    except Exception:
        return str(event.type())


def _linux_qt_keyboard_grabs_enabled() -> bool:
    """Return whether the Linux picker should use Qt's native keyboard grab."""
    raw = os.environ.get("OPENWAND_LINUX_QT_KEYBOARD_GRAB")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return not bool(getattr(sys, "frozen", False))


def _restore_foreground_window(window_id: int) -> None:
    """Return keyboard focus to the app that owned the hotkey-time context."""
    target = int(window_id or 0)
    if not target:
        return
    try:
        from core.platform_utils import set_foreground_window

        set_foreground_window(target)
    except Exception:
        # Focus restoration is best-effort; cancellation must always finish.
        return


def _build_rows(
    caller_idx: int = 0,
    provider_suggestions: list[dict] | None = None,
) -> list[dict]:
    """Build app-aware suggestions plus the caller's normal overlay rows."""
    from core.action_files.store import configured_caller_rows

    callers = configured_caller_rows(config)
    caller = callers[caller_idx] if caller_idx < len(callers) else {}
    if bool(caller.get("paste_back")):
        provider_suggestions = []
    provider_active = bool(provider_suggestions)
    configured_mode = "answer" if provider_active else "legacy"
    rows = []
    used_keys: set[str] = set()
    for intent_idx, r in enumerate(caller.get("intents", [])):
        if not bool(r.get("enabled", True)):
            continue
        display_intent = localize_intent_if_default(
            caller_idx,
            intent_idx,
            r,
            current_language(),
        )
        key = str(r.get("key") or "").upper()
        if key:
            used_keys.add(key)
        action_file = r.get("action_file") if isinstance(r.get("action_file"), dict) else {}
        routing = (
            {
                "mode": "file",
                "source": "configured",
                "action_name": str(action_file.get("name") or ""),
                "caller_folder": str(caller.get("folder") or ""),
            }
            if bool(action_file.get("has_code"))
            else {"mode": configured_mode, "source": "configured"}
        )
        rows.append({
            "glyph":     key if key else "?",
            "label":     display_intent.get("label", r.get("label", "")),
            "hint":      display_intent.get("hint", r.get("hint", "")),
            "prompt":    r["prompt"],
            "is_custom": False,
            "routing": routing,
            "access": list(action_file.get("access") or []),
            "access_colour": str(action_file.get("colour") or ""),
        })
    for r in _addon_intent_rows(caller_idx, used_keys):
        rows.append(r)
    custom_key = str(caller.get("custom_key", "s") or "").strip()
    custom_label = str(caller.get("custom_label") or "").strip() or t("Custom prompt")
    custom_mode = "legacy" if bool(caller.get("paste_back")) else "answer"
    rows.append({
        "glyph":     custom_key.upper(),
        "label":     custom_label,
        "hint":      t("Ask anything"),
        "prompt":    "",
        "is_custom": True,
        "routing": {"mode": custom_mode, "source": "custom"},
    })
    used_keys.update(str(row.get("glyph") or "").upper() for row in rows)
    return _provider_intent_rows(provider_suggestions or [], used_keys) + rows


def _provider_intent_rows(items: list[dict], used_keys: set[str]) -> list[dict]:
    """Normalize provider suggestions and allocate non-conflicting shortcuts."""
    rows: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("show_in_picker", True) is False:
            continue
        suggestion_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        mode = str(item.get("mode") or "answer").strip().lower()
        capability_type = str(item.get("capability_type") or "").strip()
        planning_tool = str(item.get("planning_tool") or "").strip()
        available = bool(item.get("available", True))
        unavailable_reason = str(item.get("unavailable_reason") or "").strip()
        if not suggestion_id or not label or mode not in {"action", "answer", "file"}:
            continue
        if mode == "action" and (not capability_type or not planning_tool):
            continue
        key = str(item.get("preferred_key") or "").strip().upper()
        if available:
            if not key or key in used_keys:
                key = _choose_addon_intent_key(label, used_keys)
            used_keys.add(key)
        else:
            key = "·"
        routing = {
            "mode": mode,
            "source": "provider",
            "suggestion_id": suggestion_id,
            "capability_type": capability_type,
            "planning_tool": planning_tool,
        }
        if mode == "file":
            routing["action_name"] = suggestion_id
        row = {
            "glyph": key,
            "label": t(label),
            "hint": t(unavailable_reason or str(item.get("hint") or "").strip()),
            "prompt": prompt,
            "is_custom": False,
            "appearance": "app_action",
            "routing": routing,
            "access": list(item.get("access") or []),
            "access_colour": str(item.get("access_colour") or ""),
        }
        if not available:
            row["available"] = False
        rows.append(row)
    return rows


def _addon_intent_rows(caller_idx: int, used_keys: set[str]) -> list[dict]:
    """Handle addon intent rows for UI intent overlay."""
    try:
        from core.addon_manager import get_manager

        manager = get_manager()
        intents = manager.get_intents(caller_idx) if hasattr(manager, "get_intents") else []
    except Exception:
        return []
    rows: list[dict] = []
    for item in intents:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()
        label = str(item.get("label") or "").strip()
        callback = bool(item.get("callback"))
        if (not prompt and not callback) or not label:
            continue
        key = str(item.get("key") or "").strip().upper()
        if not key or key in used_keys:
            key = _choose_addon_intent_key(label, used_keys)
        used_keys.add(key)
        rows.append({
            "glyph": key,
            "label": label,
            "hint": str(item.get("hint") or f"Addon: {item.get('addon_id', '')}").strip(),
            "prompt": prompt,
            "is_custom": False,
            "routing": {
                "mode": "addon",
                "source": "addon",
                "addon_id": str(item.get("addon_id") or ""),
                "action_id": str(item.get("id") or ""),
                "callback": callback,
            },
            "access": list(item.get("access") or []),
            "access_colour": str(item.get("access_colour") or ""),
        })
    return rows


def _choose_addon_intent_key(label: str, used_keys: set[str]) -> str:
    """Handle choose addon intent key for UI intent overlay."""
    for char in label.upper():
        if char.isalnum() and char not in used_keys:
            return char
    for char in "ZXCVBNM123456789":
        if char not in used_keys:
            return char
    return "?"


# ── Layout constants ────────────────────────────────────────────────────────
_W             = 560
_ROW_H         = 37
_PAD_V         = 0
_PAD_H         = 20
_RADIUS        = 0
_ROW_RADIUS    = 0
_BADGE_W       = 12       # compatibility: 5a uses a key column, not badges
_BADGE_H       = 0
_BADGE_R       = 0
_BADGE_X       = _PAD_H
_TEXT_X        = _PAD_H + 24
_AUTO_CLOSE_MS = 60000
_INPUT_EXTRA   = 0        # the one-line input is part of the normal picker height
_INPUT_MIN_H   = 36
_INPUT_MAX_H   = 118      # fallback when usable screen geometry is unavailable
_SCREEN_MARGIN = 24
_CONV_H        = 77
_CONV_TOP      = 36
_CTX_H         = 130
_CTX_GAP       = 18
_CTX_CHIP_H    = 22
_CTX_CHIP_W    = 196
_CTX_TOP       = 49
_CTX_ROW_GAP   = 3
_CTX_KEY_W     = 10
_CTX_KEY_GAP   = 8
_CTX_EST_GAP   = 20
_ROWS_TOP      = 16
_ROW_KEY_W     = 12
_ROW_KEY_GAP   = 12
_ROW_LABEL_W   = 150
_ROW_LABEL_GAP = 12
_INPUT_TOP     = 14
_INPUT_BOTTOM  = 18
_INPUT_BAR_W   = 2
_INPUT_KEY_W   = 10
_INPUT_KEY_GAP = 9
_CTX_PREVIEW_TOP = 6
_CTX_PREVIEW_LINE_H = 22
_CTX_PREVIEW_MAX = 3
_CTX_PREVIEW_MAX_LINES = 2
_CTX_PREVIEW_REMOVE_W = 16
_PANEL_TAB_TOP = 8
_PANEL_TAB_H = 27
_PANEL_TAB_GAP = 18
_TOOLS_TOP = 43
_TOOLS_ROW_H = 36
_TOOLS_COLUMN_GAP = 18

# ── Palette ─────────────────────────────────────────────────────────────────
_BG             = QColor("#16181b")
_SURFACE        = QColor("#1c1f23")
_SURFACE_RAISED = QColor("#22262b")
_BORDER         = QColor("#30353b")
_ROW_HL         = QColor("#1c1f23")
_BADGE_BG       = QColor("#22262b")
_BADGE_HL       = QColor("#22262b")
_KEY_COLOR      = QColor("#d8a145")
_LABEL          = QColor("#e9e6e0")
_HINT           = QColor("#7e7c78")
_HINT_ESC       = QColor("#6e6c68")
_SEP            = QColor("#262a2f")
_CTX_OFF        = QColor("#33373d")
_CTX_ON         = QColor("#d8a145")
_CTX_AUTO       = QColor("#d8a145")
_CTX_TEXT       = QColor("#e9e6e0")
_CTX_SUB        = QColor("#b8b4ac")
_TEXT_DIM       = QColor("#8b8a86")
_TEXT_FAINT     = QColor("#6e6c68")
_WARN           = QColor("#c4553d")
_NEW_PROJECT_SENTINEL = "__new_project__"

_BITTER_READY = False


def _ensure_bitter_fonts() -> None:
    """Register the bundled Bitter faces once, retaining a Georgia fallback."""
    global _BITTER_READY
    if _BITTER_READY:
        return
    _BITTER_READY = True
    for name in ("Bitter-Regular.ttf", "Bitter-SemiBold.ttf", "Bitter-Italic.ttf"):
        path = ASSETS_DIR / "fonts" / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))


def _design_font(
    family: str,
    px: float,
    weight: QFont.Weight = QFont.Weight.Normal,
    *,
    italic: bool = False,
    tracking: float = 0.0,
) -> QFont:
    """Return a 96-dpi design font expressed in Qt point units."""
    font = QFont(family)
    font.setPointSizeF(px * 0.75)
    font.setWeight(weight)
    font.setItalic(italic)
    if tracking:
        font.setLetterSpacing(
            QFont.SpacingType.PercentageSpacing,
            100.0 + tracking * 100.0,
        )
    return font


def _serif_font(
    px: float,
    weight: QFont.Weight = QFont.Weight.Normal,
    *,
    italic: bool = False,
    tracking: float = 0.0,
) -> QFont:
    _ensure_bitter_fonts()
    families = set(QFontDatabase.families())
    family = "Bitter" if "Bitter" in families else "Bitter Pro" if "Bitter Pro" in families else "Georgia"
    return _design_font(
        family,
        px,
        weight,
        italic=italic,
        tracking=tracking,
    )


def _mono_font(px: float, *, tracking: float = 0.0) -> QFont:
    families = set(QFontDatabase.families())
    family = next(
        (
            name
            for name in ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono")
            if name in families
        ),
        QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family(),
    )
    return _design_font(family, px, tracking=tracking)


def _qcolor(value: str | None, fallback: QColor | str, alpha: int | None = None) -> QColor:
    """Parse a theme color with a fallback and optional alpha override."""
    color = QColor(str(value or ""))
    if not color.isValid():
        color = QColor(fallback)
    if alpha is not None:
        color.setAlpha(max(0, min(255, alpha)))
    return color


def _theme_palette() -> dict[str, QColor]:
    """Return the Graphite & amber palette, respecting user theme overrides."""
    dark = True
    try:
        from ui.shared.theme import is_dark_mode, theme_colors

        colors = theme_colors()
        dark = is_dark_mode()
    except Exception:
        colors = {}
    app_action = QColor("#e0b03e") if dark else _qcolor(colors.get("accent_fill"), "#d8a145")
    app_action_dim = QColor(app_action)
    app_action_dim.setAlpha(205)
    app_action_badge = QColor(app_action)
    app_action_badge.setAlpha(42)
    app_action_badge_hover = QColor(app_action)
    app_action_badge_hover.setAlpha(82)
    app_action_row_hover = QColor(app_action)
    app_action_row_hover.setAlpha(28)
    return {
        "bg": _qcolor(colors.get("bg"), _BG),
        "surface": _qcolor(colors.get("surface"), _SURFACE),
        "surface_raised": _qcolor(colors.get("raised"), _SURFACE_RAISED),
        "border": _qcolor(colors.get("border"), _BORDER),
        "row_hl": _qcolor(colors.get("surface"), _ROW_HL),
        "badge_bg": _qcolor(colors.get("surface"), _BADGE_BG),
        "badge_hl": _qcolor(colors.get("button_pressed"), _BADGE_HL),
        "key": _qcolor(colors.get("accent"), _KEY_COLOR),
        "label": _qcolor(colors.get("text"), _LABEL),
        "hint": _qcolor(colors.get("text_dim"), _HINT),
        "hint_esc": _qcolor(colors.get("disabled"), _HINT_ESC),
        "sep": _qcolor(colors.get("rule"), _SEP),
        "ctx_off": _qcolor(colors.get("border"), _CTX_OFF),
        "ctx_on": _qcolor(colors.get("accent"), _CTX_ON),
        "ctx_auto": _qcolor(None, _CTX_AUTO, 115),
        "ctx_text": _qcolor(colors.get("text"), _CTX_TEXT),
        "ctx_sub": _qcolor(colors.get("label"), _CTX_SUB),
        "text_dim": _qcolor(colors.get("text_dim"), _TEXT_DIM),
        "text_faint": _qcolor(colors.get("disabled"), _TEXT_FAINT),
        "selection_bg": _qcolor(colors.get("button_pressed"), "#101214"),
        "warn": _qcolor(colors.get("over_budget"), _WARN),
        "app_action": app_action,
        "app_action_dim": app_action_dim,
        "app_action_badge": app_action_badge,
        "app_action_badge_hover": app_action_badge_hover,
        "app_action_row_hover": app_action_row_hover,
    }


def _intent_label_color(row: dict, palette: dict[str, QColor], *, available: bool) -> QColor:
    """Return the label colour, highlighting app-aware actions without a banner."""
    if not available:
        return palette["text_faint"]
    if row.get("appearance") == "app_action":
        return palette["app_action"]
    return palette["label"]


def _input_line_stylesheet() -> str:
    """Return the square, amber-rail input styling from direction 5a."""
    try:
        from ui.shared.theme import theme_colors

        colors = theme_colors()
    except Exception:
        colors = {}
    surface = str(colors.get("surface") or _SURFACE.name())
    text = str(colors.get("text") or _LABEL.name())
    accent = str(colors.get("accent") or _KEY_COLOR.name())
    on_accent = str(colors.get("on_accent") or _BG.name())
    return (
        "QPlainTextEdit {"
        f"  background: {surface};"
        "  border: none;"
        "  border-radius: 0;"
        f"  color: {text};"
        # QPlainTextEdit starts its document at the top of its viewport.  Keep
        # the same total inset while placing the single line in the visual
        # centre of the 36 px prompt row instead of leaving it high.
        "  padding: 9px 0 0 0;"
        f"  selection-background-color: {accent};"
        f"  selection-color: {on_accent};"
        "}"
    )


class _ExpandingPromptEdit(QPlainTextEdit):
    """A wrapping prompt editor with the small QLineEdit API used here."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # QPlainTextEdit adds a hidden 4 px document margin on every side.
        # Combined with the stylesheet padding, that consumed the one-line
        # editor's entire vertical budget and clipped glyph bottoms/descenders.
        self.document().setDocumentMargin(0.0)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The owning overlay enables this only after it has used all available
        # vertical screen space.  Keeping it off while the editor can grow also
        # avoids a one-line scrollbar caused by the document/frame padding.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTabChangesFocus(True)

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:  # noqa: N802 - QLineEdit compatibility
        self.setPlainText(text)

    def selectedText(self) -> str:  # noqa: N802 - QLineEdit compatibility
        return self.textCursor().selectedText()

    def cursorPosition(self) -> int:  # noqa: N802 - QLineEdit compatibility
        return self.textCursor().position()


def _context_toggle_keys() -> str:
    """Return eight unique overlay-local context toggle keys."""
    raw = str(getattr(config, "INTENT_CONTEXT_TOGGLE_KEYS", "12345678") or "12345678")
    keys: list[str] = []
    for ch in raw + "12345678":
        if ch.isspace() or ch in keys:
            continue
        keys.append(ch)
        if len(keys) >= 8:
            break
    return "".join(keys)


def _context_chip_token_text(item: dict) -> str:
    """Return token text that should be painted for a context chip."""
    return str(item.get("tokens") or "")


def _context_token_count(item: dict) -> int | None:
    """Return the exact estimate, falling back to older rounded UI labels."""
    exact = item.get("token_count")
    if isinstance(exact, int) and not isinstance(exact, bool) and exact >= 0:
        return exact
    label = _context_chip_token_text(item).strip().lower().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(k)?", label)
    if match is None or "?" in label:
        return None
    value = float(match.group(1))
    if match.group(2):
        value *= 1000
    return max(0, int(round(value)))


def _context_token_display(item: dict) -> str:
    """Return the compact, unit-free per-source count used by direction 5a."""
    count = _context_token_count(item)
    return f"{count:,}" if count is not None else "?"


def _default_context_items() -> list[dict]:
    """Fallback context chips for callers that do not provide live metadata."""
    keys = _context_toggle_keys()
    labels = [
        ("ambient", t("App")),
        ("browser", t("Browser/Web")),
        ("selection", t("Selection")),
        ("clipboard", t("Clipboard")),
        ("screenshot", t("Screenshot")),
        ("github", t("Git/GitHub")),
        ("memory", t("Memory")),
        ("files", t("Files")),
    ]
    return [
        {
            "id": source,
            "key": keys[idx],
            "label": label,
            "state": "off",
            "default_state": "off",
            "touched": False,
            "tokens": "" if source == "files" else "? tok",
            "warning": "",
        }
        for idx, (source, label) in enumerate(labels)
    ]


def _normalize_tool_snapshot(snapshot: dict | None) -> dict:
    """Return a safe, interactive capability snapshot from the execution layer."""
    raw = snapshot if isinstance(snapshot, dict) else {}
    items: list[dict] = []
    for value in raw.get("items") or []:
        if not isinstance(value, dict):
            continue
        name = " ".join(str(value.get("name") or "").split()).strip()
        if not name:
            continue
        status = str(value.get("status") or "unavailable").strip().lower()
        if status not in {"ready", "ask", "unavailable"}:
            status = "unavailable"
        try:
            count = max(1, int(value.get("count") or 1))
        except (TypeError, ValueError, OverflowError):
            count = 1
        tools = [
            str(name).strip()
            for name in (value.get("tools") or [])
            if str(name).strip()
        ]
        toggleable = (
            bool(value.get("toggleable"))
            and status != "unavailable"
            and bool(tools)
        )
        items.append({
            "id": str(value.get("id") or name).strip(),
            "name": name,
            "status": status,
            "count": count,
            "description": " ".join(str(value.get("description") or "").split()),
            "tools": tools,
            "enabled": bool(value.get("enabled", status != "unavailable")) and status != "unavailable",
            "toggleable": toggleable,
        })
    try:
        count = max(0, int(raw.get("count") or 0))
    except (TypeError, ValueError, OverflowError):
        count = sum(item["count"] for item in items if item["status"] != "unavailable")
    return {
        "execution": " ".join(str(raw.get("execution") or "").split()),
        "count": count,
        "items": items,
        "note": " ".join(str(raw.get("note") or "").split()),
    }


class IntentOverlay(QWidget):
    """Model intent overlay."""
    intent_chosen = Signal(str, str)
    cancelled     = Signal()
    screenshot_snip_requested = Signal()
    selection_capture_requested = Signal(str)
    context_items_pasted = Signal(object)
    context_source_removed = Signal(str, str)
    context_source_reenabled = Signal(str)
    _raw_key      = Signal(str)

    def __init__(
        self,
        caller_idx: int = 0,
        target_hwnd: int = 0,
        context_items: list[dict] | None = None,
        conversation_options: list[dict] | None = None,
        project_options: list[dict] | None = None,
        active_project_id: str | None = None,
        conversation_namespace_label: str = "",
        initial_custom_text: str = "",
        focus_overlay: bool = False,
        defer_focus: bool = False,
        action_provider: dict | None = None,
        tool_snapshot: dict | None = None,
        parent=None,
    ):
        """Initialize the intent overlay instance."""
        super().__init__(parent)
        _ensure_bitter_fonts()
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        if _IS_WIN:
            # Popup gives click-outside-to-dismiss on Windows, where a raw hook
            # forwards suppressed overlay command keys. On Linux/macOS the
            # overlay must itself become a real key window, so keep those as
            # normal frameless top-level windows and dismiss via Esc/focus-out.
            flags |= Qt.WindowType.Popup
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._focus_deferred = bool(defer_focus)
        if self._focus_deferred:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setEnabled(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._caller_idx = int(caller_idx)
        from core.action_files.store import configured_caller_rows

        caller_rows = configured_caller_rows(config)
        caller_settings = (
            caller_rows[self._caller_idx] if self._caller_idx < len(caller_rows) else {}
        )
        self._space_starts_new_chat = bool(caller_settings.get("space_starts_new_chat", True))
        self._action_provider_enabled = not bool(caller_settings.get("paste_back"))
        self._action_provider = (
            dict(action_provider or {}) if self._action_provider_enabled else {}
        )
        provider_suggestions = self._action_provider.get("suggested_intents")
        self._rows = _build_rows(
            caller_idx,
            provider_suggestions if isinstance(provider_suggestions, list) else [],
        )
        self._selected_intent_routing: dict = dict(
            next(
                (row.get("routing") or {} for row in self._rows if row.get("is_custom")),
                {"mode": "answer", "source": "custom"},
            )
        )
        self._context_items = []
        self._new_conversation_context_defaults: dict[str, str] = {}
        for item in context_items or _default_context_items():
            next_item = dict(item)
            next_item.setdefault("default_state", next_item.get("state", "off"))
            next_item.setdefault("touched", False)
            self._context_items.append(next_item)
            item_id = str(next_item.get("id") or "")
            if item_id:
                self._new_conversation_context_defaults[item_id] = str(
                    next_item.get("default_state") or next_item.get("state") or "off"
                ).lower()
        self._tool_snapshot = _normalize_tool_snapshot(tool_snapshot)
        self._tool_items = list(self._tool_snapshot["items"])
        self._tool_row_rects: list[tuple[QRect, int]] = []
        self._active_panel = "context"
        self._context_tab_rect = QRect()
        self._tools_tab_rect = QRect()
        self._project_options = self._normalize_project_options(project_options or [])
        self._project_id = active_project_id or self._default_project_id()
        if not any(item.get("id") == self._project_id for item in self._project_options):
            self._project_id = self._default_project_id()
        self._new_project_name = ""
        self._project_dialog_open = False
        self._conversation_options = self._normalize_conversation_options(conversation_options or [])
        self._conversation_namespace_label = " ".join(
            str(conversation_namespace_label or "").split()
        ).strip()
        selected = next(
            (item for item in self._filtered_conversation_options() if item.get("selected")),
            None,
        )
        self._conversation_mode = "continue" if selected is not None else "new"
        self._conversation_index = int(selected["index"]) if selected is not None else None
        self._conversation_choice_touched = False
        self._apply_context_defaults_for_conversation_mode()
        self._project_rect = QRect()
        self._conversation_mode_rect = QRect()
        self._conversation_list_rect = QRect()
        self._project_menu: QMenu | None = None
        self._conversation_menu: QMenu | None = None
        self._warning_rects: list[tuple[QRect, str]] = []
        self._ctx_remove_rects: list[tuple[QRect, str, str]] = []
        self._last_warning_idx: int | None = None
        self._auto_custom_mode = self._custom_row_index_without_key()
        h = self._base_height()
        self._normal_h = h
        self.setFixedSize(_W, h)
        self._layout_conversation_selector(_PAD_V)
        self._target_hwnd = target_hwnd
        self._screen_geometry = self._resolve_screen_geometry()

        self._move_to_screen_center(h)

        self._hovered: int | None = None
        self._row_rects: list[QRect] = []
        self._handled = False
        self._suppress_hide_cancel = False
        self._selection_pending_idx: int | None = None
        self._custom_mode = False
        self._prefilled_custom_mode = False
        self._prompt_input_h = _INPUT_MIN_H
        self._prompt_resize_pending = False
        self._initial_custom_text = str(initial_custom_text or "").strip()
        self._focus_overlay_requested = bool(focus_overlay)
        self._interaction_started = False
        self._was_activated = False   # macOS: dismiss on focus-out once activated
        self._kb_hook = None
        self._overlay_grabbed_keyboard = False
        self._input_grabbed_keyboard = False
        self._drop_next_keypress = False
        self._last_raw_context_key = ""
        self._last_raw_context_at = 0.0
        self._raw_key.connect(self._on_raw_key)

        self._input_line = _ExpandingPromptEdit(self)
        self._input_line.installEventFilter(self)
        self._input_line.setPlaceholderText(t("Type your prompt"))
        self._input_line.setFont(_serif_font(14.5))
        input_palette = self._input_line.palette()
        input_palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(_HINT))
        input_palette.setColor(QPalette.ColorRole.Text, QColor(_LABEL))
        self._input_line.setPalette(input_palette)
        self._input_line.setStyleSheet(_input_line_stylesheet())
        self._input_line.hide()
        self._input_line.textChanged.connect(self._schedule_prompt_input_resize)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._cancel)
        self._overlay_timeout_ms = _configured_overlay_timeout_ms(
            getattr(config, "INTENT_OVERLAY_TIMEOUT_MS", _AUTO_CLOSE_MS)
        )
        self._restart_timer()

    def _restart_timer(self) -> None:
        """Restart the auto-close countdown after a user interaction."""
        if self._custom_mode or self._handled or self._overlay_timeout_ms <= 0:
            # A timer may already be active when the picker enters custom mode,
            # completes, or receives a live timeout change. Stop it so a stale
            # timeout cannot cancel the newer state.
            self._timer.stop()
            return
        self._timer.start(self._overlay_timeout_ms)

    def _note_interaction(self) -> None:
        """Record active use of the overlay so it does not close mid-change."""
        self._restart_timer()

    def _debug(self, message: str) -> None:
        """Handle debug for intent overlay."""
        if not _DEBUG_KEYS:
            return
        focus = QApplication.focusWidget()
        focus_name = type(focus).__name__ if focus is not None else "None"
        try:
            selection_len = len(self._input_line.selectedText())
            cursor = self._input_line.cursorPosition()
            input_focus = self._input_line.hasFocus()
            input_visible = not self._input_line.isHidden()
        except Exception:
            selection_len = -1
            cursor = -1
            input_focus = False
            input_visible = False
        print(
            "[openwand-intent] "
            f"{message} "
            f"custom={self._custom_mode} drop_next={self._drop_next_keypress} "
            f"input_focus={input_focus} input_visible={input_visible} "
            f"cursor={cursor} selection_len={selection_len} "
            f"focus={focus_name}",
            file=sys.stderr,
            flush=True,
        )

    def _debug_key(self, source: str, event) -> None:
        """Handle debug key for intent overlay."""
        if not _DEBUG_KEYS:
            return
        self._debug(
            f"{source} key={_key_name(int(event.key()))} "
            f"type={_event_type_name(event)} "
            f"text={_safe_text_desc(event.text())} "
            f"mods={int(event.modifiers().value)} accepted={event.isAccepted()}"
        )

    def _resolve_screen_geometry(self) -> QRect:
        """Handle resolve screen geometry for intent overlay."""
        app = QApplication.instance()
        if self._target_hwnd:
            if sys.platform == "win32":
                try:
                    import ctypes
                    import ctypes.wintypes
                    rect = ctypes.wintypes.RECT()
                    if ctypes.windll.user32.GetWindowRect(self._target_hwnd, ctypes.byref(rect)):
                        center = QPoint(
                            (rect.left + rect.right) // 2,
                            (rect.top + rect.bottom) // 2,
                        )
                        screen = app.screenAt(center) if app is not None else None
                        if screen is not None:
                            return screen.availableGeometry()
                except Exception:
                    pass
            else:
                from PySide6.QtGui import QCursor
                cursor_pos = QCursor.pos()
                screen = app.screenAt(cursor_pos) if app is not None else None
                if screen is not None:
                    return screen.availableGeometry()
        primary = QApplication.primaryScreen()
        return (
            primary.availableGeometry()
            if primary is not None
            else QRect(0, 0, _W, self._normal_h + _INPUT_EXTRA + _INPUT_MAX_H)
        )

    def _move_to_screen_center(self, height: int) -> None:
        """Handle move to screen center for intent overlay."""
        screen = self._screen_geometry
        self.move(
            screen.x() + (screen.width() - _W) // 2,
            screen.y() + (screen.height() - height) // 2,
        )

    def context_choices(self) -> list[dict]:
        """Return the current per-prompt context source states."""
        return [dict(item) for item in self._context_items]

    def tool_snapshot(self) -> dict:
        """Return the current capability snapshot represented by the Tools tab."""
        return {
            **self._tool_snapshot,
            "items": [dict(item) for item in self._tool_items],
        }

    def tool_choices(self) -> list[dict]:
        """Return the per-prompt tool switch decisions."""
        return [
            {
                "id": str(item.get("id") or ""),
                "tools": list(item.get("tools") or []),
                "enabled": bool(item.get("enabled")),
                "toggleable": bool(item.get("toggleable")),
            }
            for item in self._tool_items
        ]

    def update_action_provider(self, action_provider: dict | None = None) -> None:
        """Load app-specific actions after deferred hotkey context capture."""
        if self._handled:
            return
        self._action_provider = (
            dict(action_provider or {}) if self._action_provider_enabled else {}
        )
        provider_suggestions = self._action_provider.get("suggested_intents")
        self._rows = _build_rows(
            self._caller_idx,
            provider_suggestions if isinstance(provider_suggestions, list) else [],
        )
        self._auto_custom_mode = self._custom_row_index_without_key()
        height = self._base_height()
        self._normal_h = height
        self.setFixedSize(_W, height)
        self._layout_conversation_selector(_PAD_V)
        self._move_to_screen_center(height)
        self.update()

    def current_custom_text(self) -> str:
        """Return the currently typed custom prompt text, if any."""
        return self._input_line.text().strip()

    def selected_intent_routing(self) -> dict:
        """Return stable routing metadata for the row that was submitted."""
        routing = dict(self._selected_intent_routing)
        if routing.get("source") == "provider":
            routing["provider_id"] = str(self._action_provider.get("id") or "")
            routing["app"] = str(self._action_provider.get("app") or "")
        return routing

    def _paste_clipboard_context(self) -> bool:
        """Attach clipboard files/images instead of inserting path-like text."""
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData() if clipboard is not None else None
        if mime is None or not (mime.hasUrls() or mime.hasImage()):
            return False
        from ui.drop_zone import process_drop_mime

        items = process_drop_mime(mime)
        if not items:
            return False
        self.context_items_pasted.emit(items)
        self._note_interaction()
        return True

    def _base_height(self) -> int:
        """Return the picker height for the current rows and context previews."""
        conversation_h = _CONV_H if self._show_conversation_selector else 0
        context_h = self._panel_controls_height()
        intent_count = sum(not bool(row.get("is_custom")) for row in self._rows)
        return (
            conversation_h
            + context_h
            + _ROWS_TOP
            + _ROW_H * intent_count
            + self._context_preview_height()
            + _INPUT_TOP
            + _INPUT_MIN_H
            + _INPUT_BOTTOM
        )

    def _context_controls_height(self) -> int:
        """Return the source-grid height including its body top inset."""
        rows = max(1, (len(self._context_items) + 1) // 2)
        grid_h = rows * _CTX_CHIP_H + max(0, rows - 1) * _CTX_ROW_GAP
        return max(_CTX_H, _CTX_TOP + max(57, grid_h))

    def _tools_controls_height(self) -> int:
        """Return the tab-and-inventory height for the tools view."""
        rows = max(1, (len(self._tool_items) + 1) // 2)
        return max(_CTX_H, _TOOLS_TOP + rows * _TOOLS_ROW_H + 8)

    def _panel_controls_height(self) -> int:
        """Return the height of the active Context/Tools sibling view."""
        if self._active_panel == "tools":
            return self._tools_controls_height()
        return self._context_controls_height()

    def _set_active_panel(self, panel: str) -> bool:
        """Switch between the Context and per-prompt Tools views."""
        normalized = "tools" if str(panel or "").lower() == "tools" else "context"
        if normalized == self._active_panel:
            return False
        self._active_panel = normalized
        self._warning_rects = []
        self._ctx_remove_rects = []
        self._note_interaction()
        self._resize_for_context_preview()
        self.update()
        return True

    def _tools_shortcut_available(self) -> bool:
        """Reserve T only when it does not shadow an existing picker command."""
        used = {
            str(row.get("glyph") or "").strip().lower()
            for row in self._rows
        }
        used.update(
            str(item.get("key") or "").strip().lower()
            for item in self._context_items
        )
        return "t" not in used

    def _prompt_input_rect(self, height: int | None = None) -> QRect:
        """Return the amber-rail prompt surface at the foot of the picker."""
        input_h = int(height if height is not None else _INPUT_MIN_H)
        return QRect(
            _PAD_H,
            self._normal_h - _INPUT_BOTTOM - _INPUT_MIN_H,
            _W - _PAD_H * 2,
            input_h,
        )

    def _prompt_editor_rect(self, height: int | None = None) -> QRect:
        """Inset the editor after the painted shortcut key and prompt rail."""
        surface = self._prompt_input_rect(height)
        left = surface.x() + 12 + _INPUT_KEY_W + _INPUT_KEY_GAP
        return QRect(left, surface.y(), max(1, surface.right() - left - 11), surface.height())

    @staticmethod
    def _normalize_conversation_options(options: list[dict]) -> list[dict]:
        """Return compact history options for the overlay selector."""
        normalized: list[dict] = []
        for raw in options or []:
            if not isinstance(raw, dict):
                continue
            try:
                idx = int(raw.get("index"))
            except (TypeError, ValueError):
                continue
            title = " ".join(str(raw.get("title") or "").split()).strip()
            if not title:
                title = t("Conversation")
            subtitle = " ".join(str(raw.get("subtitle") or "").split()).strip()
            normalized.append(
                {
                    "index": idx,
                    "title": title,
                    "subtitle": subtitle,
                    "selected": bool(raw.get("selected")),
                    "project_id": str(raw.get("project_id") or "general"),
                }
            )
        return normalized

    @staticmethod
    def _normalize_project_options(options: list[dict]) -> list[dict]:
        """Return compact project options for the overlay selector."""
        normalized: list[dict] = []
        seen: set[str] = set()
        for raw in options or []:
            if not isinstance(raw, dict):
                continue
            project_id = str(raw.get("id") or "").strip()
            if not project_id or project_id in seen:
                continue
            name = " ".join(str(raw.get("name") or "").split()).strip() or t("Project")
            normalized.append({"id": project_id, "name": name})
            seen.add(project_id)
        if not normalized:
            normalized.append({"id": "general", "name": t("General")})
        return normalized

    def _default_project_id(self) -> str:
        return str(self._project_options[0].get("id") or "general") if self._project_options else "general"

    @property
    def _show_conversation_selector(self) -> bool:
        return True

    def conversation_choice(self) -> dict:
        """Return the selected chat continuation mode for this prompt."""
        if self._conversation_mode == "continue" and self._conversation_index is not None:
            return {"mode": "continue", "index": int(self._conversation_index)}
        return {"mode": "new"}

    def conversation_choice_touched(self) -> bool:
        """Return whether the user changed the chat target in this overlay."""
        return bool(self._conversation_choice_touched)

    def _apply_context_defaults_for_conversation_mode(self, *, restore_new: bool = False) -> None:
        """Apply caller defaults to new chats and optional all-off defaults to continuations."""
        first_prompt_only = bool(
            getattr(config, "CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY", False)
        )
        if not first_prompt_only:
            return
        continuing = self._conversation_mode == "continue" and self._conversation_index is not None
        if not continuing and not restore_new:
            return
        for item in self._context_items:
            if item.get("locked") or item.get("touched"):
                continue
            item_id = str(item.get("id") or "")
            caller_default = self._new_conversation_context_defaults.get(
                item_id,
                str(item.get("default_state") or item.get("state") or "off").lower(),
            )
            state = "off" if first_prompt_only and continuing else caller_default
            item["state"] = state
            item["default_state"] = state

    def project_choice(self) -> dict:
        """Return the selected project for this prompt."""
        if self._project_id == _NEW_PROJECT_SENTINEL:
            return {"mode": "new_project", "name": self._new_project_name}
        return {"mode": "existing", "project_id": self._project_id}

    def _selected_project_option(self) -> dict | None:
        for option in self._project_options:
            if str(option.get("id") or "") == str(self._project_id):
                return option
        return None

    def _selected_project_name(self) -> str:
        if self._project_id == _NEW_PROJECT_SENTINEL:
            return self._new_project_name or t("New project")
        option = self._selected_project_option()
        return str(option.get("name") or t("Project")) if option else t("Project")

    def _filtered_conversation_options(self) -> list[dict]:
        project_id = str(self._project_id or "")
        return [
            option
            for option in self._conversation_options
            if not project_id or str(option.get("project_id") or "") == project_id
        ]

    def _selected_conversation_option(self) -> dict | None:
        if self._conversation_index is None:
            return None
        for option in self._filtered_conversation_options():
            if int(option.get("index", -1)) == int(self._conversation_index):
                return option
        return None

    def _selected_conversation_title(self) -> str:
        option = self._selected_conversation_option()
        if option is None:
            return t("Latest conversation") if self._filtered_conversation_options() else t("No history yet")
        return str(option.get("title") or t("Conversation"))

    def update_context_items(self, items: list[dict]) -> None:
        """Refresh context chip metadata while preserving user-toggled states."""
        if not items:
            return
        current_by_id = {str(item.get("id") or ""): item for item in self._context_items}
        refreshed: list[dict] = []
        for item in items:
            next_item = dict(item)
            next_item.setdefault("default_state", next_item.get("state", "off"))
            next_item.setdefault("touched", False)
            current = current_by_id.get(str(next_item.get("id") or ""))
            if next_item.pop("force_state", False):
                refreshed.append(next_item)
                continue
            if current is not None:
                touched = bool(current.get("touched", False))
                if touched:
                    next_item["state"] = current.get("state", next_item.get("state", "off"))
                    next_item["default_state"] = current.get(
                        "default_state",
                        next_item.get("default_state", next_item.get("state", "off")),
                    )
                    next_item["touched"] = True
                elif (
                    str(next_item.get("id") or "") in {"selection", "screenshot"}
                    and str(current.get("state") or "").lower() == "on"
                    and str(next_item.get("state") or "").lower() == "off"
                    and not bool(next_item.get("available", True))
                ):
                    next_item["state"] = "on"
            refreshed.append(next_item)
        self._context_items = refreshed
        self._apply_context_defaults_for_conversation_mode()
        self._warning_rects = []
        self._resize_for_context_preview()
        self.update()

    def _resize_for_context_preview(self) -> None:
        """Resize the picker when live context previews appear or disappear."""
        next_h = self._base_height()
        if next_h == self._normal_h:
            return
        self._normal_h = next_h
        if self._prompt_input_visible():
            next_h += self._prompt_input_extra()
        self.setFixedSize(_W, next_h)
        if self._prompt_input_visible():
            self._input_line.setGeometry(self._prompt_editor_rect(self._prompt_input_h))
        if hasattr(self, "_screen_geometry"):
            self._move_to_screen_center(next_h)

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        """Paint event."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = _theme_palette()

        # Direction 5a is deliberately a single, square graphite plane.
        p.fillRect(self.rect(), QBrush(palette["bg"]))

        label_font = _serif_font(15.5)
        hint_font = _serif_font(12.5, italic=True)
        key_font = _mono_font(11)
        ctx_label_font = _serif_font(13.5)
        ctx_state_font = _mono_font(9.5)
        ctx_token_font = _mono_font(10)

        y = 0
        self._warning_rects = []
        self._row_rects = [QRect() for _row in self._rows]
        if self._show_conversation_selector:
            self._paint_conversation_selector(p, y, _serif_font(13.5), _serif_font(13.5), palette)
            y += _CONV_H
        self._paint_panel_tabs(p, y, palette)
        if self._active_panel == "context":
            self._paint_context_items(p, y, ctx_label_font, ctx_state_font, ctx_token_font, palette)
        else:
            self._paint_tool_items(p, y, palette)
        y += self._panel_controls_height()
        y += _ROWS_TOP
        for i, row in enumerate(self._rows):
            if row.get("is_custom"):
                continue
            row_rect = QRect(_PAD_H, y, _W - _PAD_H * 2, _ROW_H)
            self._row_rects[i] = row_rect
            available = bool(row.get("available", True))
            hovered = available and i == self._hovered

            if self._selection_pending_idx == i:
                p.fillRect(row_rect, QBrush(palette["surface_raised"]))
            elif hovered:
                p.fillRect(row_rect, QBrush(palette["row_hl"]))

            p.setPen(QPen(palette["sep"], 1))
            p.drawLine(row_rect.left(), row_rect.top(), row_rect.right(), row_rect.top())

            p.setFont(key_font)
            key_color = palette["key"] if available else palette["text_faint"]
            p.setPen(QPen(key_color))
            p.drawText(
                _PAD_H,
                y,
                _ROW_KEY_W,
                _ROW_H,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                str(row.get("glyph") or ""),
            )

            label_x = _PAD_H + _ROW_KEY_W + _ROW_KEY_GAP
            p.setFont(label_font)
            p.setPen(QPen(_intent_label_color(row, palette, available=available)))
            p.drawText(
                label_x,
                y,
                _ROW_LABEL_W,
                _ROW_H,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                str(row.get("label") or ""),
            )

            subtitle = str(row.get("hint") or row.get("prompt") or "")
            if subtitle:
                p.setFont(hint_font)
                p.setPen(QPen(palette["hint"] if available else palette["text_faint"]))
                hint_x = label_x + _ROW_LABEL_W + _ROW_LABEL_GAP
                hint_w = _W - _PAD_H - hint_x
                elided = QFontMetrics(hint_font).elidedText(
                    subtitle, Qt.TextElideMode.ElideRight, hint_w
                )
                p.drawText(
                    hint_x,
                    y,
                    hint_w,
                    _ROW_H,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    elided,
                )

            y += _ROW_H

        preview_h = self._context_preview_height()
        if preview_h:
            self._paint_context_preview(p, y, hint_font, ctx_token_font, palette)
            y += preview_h

        # The custom prompt is always visible as the fourth path through the picker.
        input_rect = self._prompt_input_rect(self._prompt_input_h)
        p.fillRect(input_rect, QBrush(palette["surface"]))
        p.fillRect(
            QRect(input_rect.x(), input_rect.y(), _INPUT_BAR_W, input_rect.height()),
            QBrush(palette["key"]),
        )
        custom_idx = next(
            (idx for idx, row in enumerate(self._rows) if row.get("is_custom")),
            None,
        )
        if custom_idx is not None:
            self._row_rects[custom_idx] = input_rect
            custom_key = str(self._rows[custom_idx].get("glyph") or "S")
        else:
            custom_key = "S"
        key_x = input_rect.x() + 12
        p.setFont(_mono_font(10))
        p.setPen(QPen(palette["key"]))
        p.drawText(
            key_x,
            input_rect.y(),
            _INPUT_KEY_W,
            input_rect.height(),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            custom_key,
        )
        if not self._prompt_input_visible():
            text_x = key_x + _INPUT_KEY_W + _INPUT_KEY_GAP
            p.setFont(_serif_font(14.5))
            p.setPen(QPen(palette["hint"]))
            placeholder = t("Type your prompt")
            p.drawText(
                text_x,
                input_rect.y(),
                input_rect.right() - text_x - 10,
                input_rect.height(),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                placeholder,
            )

        p.end()

    def _paint_conversation_selector(
        self,
        p: QPainter,
        y: int,
        label_font: QFont,
        value_font: QFont,
        palette: dict[str, QColor],
    ) -> None:
        """Paint the project and chat selector row."""
        self._layout_conversation_selector(y)
        p.fillRect(QRect(0, y, _W, _CONV_H), QBrush(palette["surface"]))

        p.setFont(_serif_font(14, QFont.Weight.DemiBold, tracking=0.05))
        p.setPen(QPen(palette["label"]))
        p.drawText(
            14,
            y + 10,
            _W - 28,
            17,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "OpenWand",
        )

        project_rect = self._project_rect
        mode_rect = self._conversation_mode_rect
        chat_rect = self._conversation_list_rect
        p.fillRect(project_rect, QBrush(palette["surface_raised"]))
        p.setPen(QPen(palette["border"], 1))
        p.drawRect(project_rect.adjusted(0, 0, -1, -1))

        p.fillRect(mode_rect, QBrush(palette["key"]))

        chat_bg = palette["surface_raised"] if self._conversation_mode == "continue" else QColor("#1a1d21")
        p.fillRect(chat_rect, QBrush(chat_bg))
        p.setPen(QPen(palette["border"], 1))
        p.drawRect(chat_rect.adjusted(0, 0, -1, -1))

        # Project label and value are separate runs, matching the reference's hierarchy.
        p.setFont(label_font)
        project_label = t("Project")
        label_x = project_rect.x() + 10
        p.setPen(QPen(palette["text_dim"]))
        p.setFont(label_font)
        p.drawText(
            label_x,
            project_rect.y(),
            QFontMetrics(label_font).horizontalAdvance(project_label),
            project_rect.height(),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            project_label,
        )
        value_x = label_x + QFontMetrics(label_font).horizontalAdvance(project_label) + 8
        arrow_w = 14
        value_w = max(20, project_rect.right() - 10 - arrow_w - value_x)
        project_value = QFontMetrics(label_font).elidedText(
            self._selected_project_name(),
            Qt.TextElideMode.ElideRight,
            value_w,
        )
        p.setPen(QPen(palette["label"]))
        p.drawText(
            value_x,
            project_rect.y(),
            value_w,
            project_rect.height(),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            project_value,
        )
        p.setPen(QPen(palette["text_dim"]))
        p.setFont(_mono_font(8))
        p.drawText(
            project_rect.right() - 18,
            project_rect.y(),
            12,
            project_rect.height(),
            Qt.AlignmentFlag.AlignCenter,
            "▼",
        )

        mode_value = t("Continue") if self._conversation_mode == "continue" else t("New chat")
        p.setFont(_serif_font(13.5, QFont.Weight.DemiBold))
        p.setPen(QPen(palette["bg"]))
        p.drawText(
            mode_rect,
            Qt.AlignmentFlag.AlignCenter,
            mode_value,
        )

        chat_value = (
            self._selected_conversation_title()
            if self._conversation_mode == "continue"
            else t("Choose conversation")
        )
        p.setFont(value_font)
        chat_color = palette["label"] if self._conversation_mode == "continue" else palette["text_faint"]
        p.setPen(QPen(chat_color))
        chat_value = QFontMetrics(value_font).elidedText(
            chat_value,
            Qt.TextElideMode.ElideRight,
            max(20, chat_rect.width() - 40),
        )
        p.drawText(
            chat_rect.adjusted(10, 0, -30, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            chat_value,
        )
        p.setPen(QPen(palette["text_dim"] if self._conversation_mode == "continue" else palette["text_faint"]))
        p.setFont(_mono_font(8))
        p.drawText(
            chat_rect.right() - 20,
            chat_rect.y(),
            12,
            chat_rect.height(),
            Qt.AlignmentFlag.AlignCenter,
            "▼",
        )

    def _layout_conversation_selector(self, y: int) -> None:
        """Update selector hit rects independently of paint delivery."""
        if not self._show_conversation_selector:
            self._project_rect = QRect()
            self._conversation_mode_rect = QRect()
            self._conversation_list_rect = QRect()
            return
        top = y + _CONV_TOP
        available = _W - 28
        project_w = available - 96 - 212 - 12
        self._project_rect = QRect(14, top, project_w, 29)
        self._conversation_mode_rect = QRect(self._project_rect.right() + 7, top, 96, 29)
        self._conversation_list_rect = QRect(self._conversation_mode_rect.right() + 7, top, 212, 29)

    def _paint_panel_tabs(
        self,
        p: QPainter,
        y: int,
        palette: dict[str, QColor],
    ) -> None:
        """Paint Context and Tools as sibling views above their shared body."""
        top = y + _PANEL_TAB_TOP
        font = _serif_font(10.5, QFont.Weight.DemiBold)
        context_text = f"{t('Context')} · {len(self._context_items)}"
        tools_text = f"{t('Tools')} · {int(self._tool_snapshot.get('count') or 0)}"
        context_w = QFontMetrics(font).horizontalAdvance(context_text) + 4
        tools_w = QFontMetrics(font).horizontalAdvance(tools_text) + 4
        self._context_tab_rect = QRect(_PAD_H, top, context_w, _PANEL_TAB_H)
        self._tools_tab_rect = QRect(
            self._context_tab_rect.right() + _PANEL_TAB_GAP,
            top,
            tools_w,
            _PANEL_TAB_H,
        )
        note = str(self._tool_snapshot.get("note") or "").strip()
        if note:
            self._warning_rects.append((self._tools_tab_rect, note))
        p.setFont(font)
        for name, text, rect in (
            ("context", context_text, self._context_tab_rect),
            ("tools", tools_text, self._tools_tab_rect),
        ):
            selected = self._active_panel == name
            p.setPen(QPen(palette["key"] if selected else palette["text_dim"]))
            p.drawText(
                rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text,
            )
            if selected:
                p.fillRect(
                    QRect(rect.x(), rect.bottom() - 1, rect.width(), 2),
                    QBrush(palette["key"]),
                )
        execution = str(self._tool_snapshot.get("execution") or "").upper()
        if execution:
            right = _W - _PAD_H
            left = self._tools_tab_rect.right() + 12
            p.setFont(_mono_font(7.5, tracking=0.08))
            p.setPen(QPen(palette["text_dim"]))
            execution = QFontMetrics(p.font()).elidedText(
                execution,
                Qt.TextElideMode.ElideLeft,
                max(0, right - left),
            )
            p.drawText(
                left,
                top,
                max(0, right - left),
                _PANEL_TAB_H,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                execution,
            )
        p.setPen(QPen(palette["sep"], 1))
        p.drawLine(_PAD_H, top + _PANEL_TAB_H, _W - _PAD_H, top + _PANEL_TAB_H)

    def _paint_tool_items(
        self,
        p: QPainter,
        y: int,
        palette: dict[str, QColor],
    ) -> None:
        """Paint per-prompt tool switches in two columns."""
        top = y + _TOOLS_TOP
        self._tool_row_rects = []
        content_w = _W - _PAD_H * 2
        column_w = (content_w - _TOOLS_COLUMN_GAP) // 2
        if not self._tool_items:
            p.setFont(_serif_font(11.5, italic=True))
            p.setPen(QPen(palette["text_dim"]))
            p.drawText(
                _PAD_H + 10,
                top,
                content_w - 20,
                _TOOLS_ROW_H,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                t("No tools are offered for this prompt."),
            )
            return

        label_font = _serif_font(11.5, QFont.Weight.DemiBold)
        status_font = _mono_font(7.3, tracking=0.07)
        for index, item in enumerate(self._tool_items):
            column = index % 2
            row = index // 2
            rect = QRect(
                _PAD_H + column * (column_w + _TOOLS_COLUMN_GAP),
                top + row * _TOOLS_ROW_H,
                column_w,
                _TOOLS_ROW_H,
            )
            self._tool_row_rects.append((QRect(rect), index))
            p.setPen(QPen(palette["sep"], 1))
            p.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
            status = str(item.get("status") or "unavailable")
            enabled = bool(item.get("enabled"))
            toggleable = bool(item.get("toggleable"))
            status_text = (
                t("UNAVAILABLE")
                if status == "unavailable"
                else (
                    t("LOCKED")
                    if not toggleable
                    else (
                        t("OFF")
                        if not enabled
                        else (t("ASK") if status == "ask" else t("ON"))
                    )
                )
            )
            status_color = (
                palette["text_faint"]
                if status == "unavailable"
                else (palette["key"] if enabled else palette["text_dim"])
            )
            p.setFont(status_font)
            status_w = QFontMetrics(status_font).horizontalAdvance(status_text)
            switch_w = 28 if toggleable else 0
            switch_gap = 8 if toggleable else 0
            p.setPen(QPen(status_color))
            p.drawText(
                rect.right() - status_w - switch_w - switch_gap,
                rect.y(),
                status_w,
                rect.height(),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                status_text,
            )
            if toggleable:
                switch_rect = QRect(rect.right() - switch_w, rect.center().y() - 7, switch_w, 14)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(palette["key"] if enabled else palette["ctx_off"]))
                p.drawRoundedRect(switch_rect, 7, 7)
                knob_x = switch_rect.right() - 11 if enabled else switch_rect.x() + 3
                p.setBrush(QBrush(palette["bg"] if enabled else palette["text_dim"]))
                p.drawEllipse(QRect(knob_x, switch_rect.y() + 3, 8, 8))
            else:
                dot_rect = QRect(rect.x() + 9, rect.center().y() - 3, 6, 6)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(palette["key"] if status != "unavailable" else palette["ctx_off"]))
                p.drawEllipse(dot_rect)
            count = int(item.get("count") or 1)
            label = str(item.get("name") or "")
            if count > 1:
                label = f"{label} · {count}"
            label_x = rect.x() + 9 if toggleable else rect.x() + 23
            label_w = max(8, rect.right() - status_w - switch_w - switch_gap - 12 - label_x)
            p.setFont(label_font)
            p.setPen(
                QPen(
                    palette["label"]
                    if status != "unavailable" and enabled
                    else palette["text_faint"]
                )
            )
            p.drawText(
                label_x,
                rect.y(),
                label_w,
                rect.height(),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                QFontMetrics(label_font).elidedText(
                    label,
                    Qt.TextElideMode.ElideRight,
                    label_w,
                ),
            )
            description = str(item.get("description") or "").strip()
            if description:
                self._warning_rects.append((rect, description))

    def _tool_item_at(self, pos: QPoint) -> int | None:
        """Return the toggleable tool row under a point."""
        if self._active_panel != "tools":
            return None
        for rect, index in self._tool_row_rects:
            if rect.contains(pos) and bool(self._tool_items[index].get("toggleable")):
                return index
        return None

    def _toggle_tool_at(self, pos: QPoint) -> bool:
        """Toggle one enforceable tool group for this prompt."""
        index = self._tool_item_at(pos)
        if index is None:
            return False
        item = self._tool_items[index]
        item["enabled"] = not bool(item.get("enabled"))
        self._tool_snapshot["count"] = sum(
            int(candidate.get("count") or 1)
            for candidate in self._tool_items
            if bool(candidate.get("enabled"))
            and str(candidate.get("status") or "") != "unavailable"
        )
        self._note_interaction()
        self.update()
        return True

    def _paint_context_items(
        self,
        p: QPainter,
        y: int,
        label_font: QFont,
        state_font: QFont,
        token_font: QFont,
        palette: dict[str, QColor] | None = None,
    ) -> None:
        """Paint the per-prompt context controls."""
        palette = palette or _theme_palette()
        top = y + _CTX_TOP
        enabled = [
            item
            for item in self._context_items
            if str(item.get("state") or "off").lower() != "off"
        ]
        total = sum(_context_token_count(item) or 0 for item in enabled)
        try:
            budget = max(1, int(getattr(config, "INTENT_CONTEXT_TOKEN_BUDGET", 8000) or 8000))
        except (TypeError, ValueError, OverflowError):
            budget = 8000
        over_budget = total > budget
        offender_id = ""
        running = 0
        if over_budget:
            for item in enabled:
                running += _context_token_count(item) or 0
                if running > budget:
                    offender_id = str(item.get("id") or "")
                    break

        for item, rect in self._context_chip_rects(top):
            state = str(item.get("state") or "off").lower()
            selected = state != "off"
            offending = bool(offender_id) and str(item.get("id") or "") == offender_id
            key_color = palette["warn"] if offending else (
                palette["key"] if selected else palette["text_faint"]
            )
            name_color = palette["warn"] if offending else (
                palette["key"] if selected else palette["text_faint"]
            )
            if selected:
                p.fillRect(rect, QBrush(palette["selection_bg"]))

            key = str(item.get("key") or "")
            key_x = rect.x() + 10
            if key:
                p.setFont(state_font)
                p.setPen(QPen(key_color))
                p.drawText(
                    key_x,
                    rect.y(),
                    _CTX_KEY_W,
                    rect.height(),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    key,
                )

            warning = "" if state == "off" else str(item.get("warning") or "").strip()
            if warning:
                self._warning_rects.append((rect, warning))

            label = {
                "Browser/Web": "Browser",
                "Screenshot": "Screen",
                "Git/GitHub": "Git",
            }.get(str(item.get("label") or ""), str(item.get("label") or ""))
            text_x = key_x + _CTX_KEY_W + _CTX_KEY_GAP
            token_text = _context_token_display(item) if selected else ""
            token_w = QFontMetrics(token_font).horizontalAdvance(token_text) if token_text else 0
            label_w = max(10, rect.right() - 8 - text_x - token_w - (8 if token_text else 0))
            p.setFont(_serif_font(13.5, QFont.Weight.DemiBold) if selected else label_font)
            p.setPen(QPen(name_color))
            label = QFontMetrics(label_font).elidedText(
                label, Qt.TextElideMode.ElideRight, label_w
            )
            p.drawText(
                text_x,
                rect.y(),
                label_w,
                rect.height(),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )

            if token_text:
                p.setFont(token_font)
                p.setPen(QPen(name_color))
                p.drawText(
                    rect.right() - 8 - token_w,
                    rect.y(),
                    token_w,
                    rect.height(),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    token_text,
                )

        estimate_right = _W - _PAD_H
        estimate_w = self._context_estimate_width()
        estimate_x = estimate_right - estimate_w
        p.setFont(_mono_font(8.5, tracking=0.12))
        p.setPen(QPen(palette["text_dim"]))
        p.drawText(
            estimate_x,
            top,
            estimate_w,
            13,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "EST. TOKENS",
        )
        p.setFont(_serif_font(30, QFont.Weight.DemiBold))
        p.setPen(QPen(palette["warn"] if over_budget else palette["key"]))
        p.drawText(
            estimate_x,
            top + 12,
            estimate_w,
            33,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"≈{total:,}",
        )
        p.setFont(_mono_font(9, tracking=0.12))
        p.setPen(QPen(palette["text_dim"]))
        p.drawText(
            estimate_x,
            top + 44,
            estimate_w,
            13,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"OF {budget:,}",
        )

    def _context_preview_entries(self) -> list[tuple[str, str, str, str]]:
        """Return (label, preview, item id, source id) rows for enabled context."""
        entries: list[tuple[str, str, str, str]] = []
        preview_items = sorted(
            self._context_items,
            key=lambda item: 0 if str(item.get("id") or "") == "attachments" else 1,
        )
        for item in preview_items:
            if len(entries) >= _CTX_PREVIEW_MAX:
                break
            state = str(item.get("state") or "off").lower()
            if state == "off":
                continue
            item_id = str(item.get("id") or "")
            sources = item.get("sources")
            if isinstance(sources, list) and sources:
                base_label = " ".join(str(item.get("label") or t("Context")).split())
                added_source = False
                for source in sources:
                    if len(entries) >= _CTX_PREVIEW_MAX:
                        break
                    if not isinstance(source, dict):
                        continue
                    preview = " ".join(str(source.get("preview") or "").split())
                    if not preview:
                        continue
                    source_label = " ".join(str(source.get("label") or "").split())
                    app_label = " ".join(str(source.get("app") or "").split())
                    source_id = str(source.get("id") or source_label)
                    if app_label and source_label and source_label.casefold() not in app_label.casefold():
                        label = f"{app_label}: {source_label}"
                    else:
                        label = app_label or source_label or base_label
                    entries.append((label, preview, item_id, source_id))
                    added_source = True
                if added_source:
                    continue
            preview = " ".join(str(item.get("preview") or "").split())
            if not preview:
                continue
            label = " ".join(str(item.get("label") or t("Context")).split())
            entries.append((label, preview, item_id, ""))
        return entries

    @staticmethod
    def _preview_value_font() -> QFont:
        """Return the font used for bottom context preview text."""
        return QFont("Segoe UI", 7)

    @staticmethod
    def _preview_wrap_lines(fm: QFontMetrics, text: str, width: int) -> list[str]:
        """Split a preview into at most two painted lines (second one elided)."""
        text = " ".join(str(text or "").split())
        if not text or width <= 0 or fm.horizontalAdvance(text) <= width:
            return [text]
        cut = len(text)
        for idx in range(1, len(text) + 1):
            if fm.horizontalAdvance(text[:idx]) > width:
                cut = max(1, idx - 1)
                break
        space = text.rfind(" ", 0, cut + 1)
        if space > 0:
            cut = space
        first = text[:cut].rstrip()
        rest = text[cut:].strip()
        if not rest:
            return [first]
        return [first, fm.elidedText(rest, Qt.TextElideMode.ElideRight, width)]

    def _context_preview_layout(self) -> list[tuple[str, list[str], str, str]]:
        """Return (label, preview lines, item id, source id) for each bottom row."""
        fm = QFontMetrics(self._preview_value_font())
        preview_x = _PAD_H + _CTX_PREVIEW_REMOVE_W + 18 + 98 + 12
        preview_w = _W - _PAD_H - preview_x
        rows: list[tuple[str, list[str], str, str]] = []
        for label, preview, item_id, source_id in self._context_preview_entries():
            lines = self._preview_wrap_lines(fm, preview, preview_w)[:_CTX_PREVIEW_MAX_LINES]
            rows.append((label, lines, item_id, source_id))
        return rows

    def _context_preview_height(self) -> int:
        """Return the extra height needed for bottom context preview lines."""
        if self._active_panel != "context":
            return 0
        rows = self._context_preview_layout()
        if not rows:
            return 0
        total_lines = sum(max(1, len(lines)) for _label, lines, _iid, _sid in rows)
        return _CTX_PREVIEW_TOP + _CTX_PREVIEW_LINE_H * total_lines

    def _paint_context_preview(
        self,
        p: QPainter,
        y: int,
        label_font: QFont,
        value_font: QFont,
        palette: dict[str, QColor],
    ) -> None:
        """Paint short previews of enabled context at the bottom of the picker."""
        rows = self._context_preview_layout()
        self._ctx_remove_rects = []
        if not rows:
            return
        line_y = y + _CTX_PREVIEW_TOP
        number_w = 18
        label_w = 98
        preview_x = _PAD_H + _CTX_PREVIEW_REMOVE_W + number_w + label_w + 12
        preview_w = _W - _PAD_H - preview_x
        remove_font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        for idx, (label, lines, item_id, source_id) in enumerate(rows, start=1):
            row_h = _CTX_PREVIEW_LINE_H * max(1, len(lines))
            remove_rect = QRect(
                _PAD_H,
                line_y + (_CTX_PREVIEW_LINE_H - 14) // 2,
                14,
                14,
            )
            self._ctx_remove_rects.append((remove_rect, item_id, source_id))
            p.setFont(remove_font)
            p.setPen(QPen(palette["ctx_sub"]))
            p.drawText(remove_rect, Qt.AlignmentFlag.AlignCenter, "✕")
            p.setFont(label_font)
            p.setPen(QPen(palette["ctx_sub"]))
            p.drawText(
                _PAD_H + _CTX_PREVIEW_REMOVE_W,
                line_y,
                number_w,
                _CTX_PREVIEW_LINE_H,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                f"{idx}.",
            )
            label_text = QFontMetrics(label_font).elidedText(
                label,
                Qt.TextElideMode.ElideRight,
                label_w,
            )
            p.drawText(
                _PAD_H + _CTX_PREVIEW_REMOVE_W + number_w,
                line_y,
                label_w,
                _CTX_PREVIEW_LINE_H,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label_text,
            )
            p.setFont(value_font)
            p.setPen(QPen(palette["hint"]))
            for line_idx, line in enumerate(lines):
                p.drawText(
                    preview_x,
                    line_y + line_idx * _CTX_PREVIEW_LINE_H,
                    preview_w,
                    _CTX_PREVIEW_LINE_H,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    line,
                )
            line_y += row_h

    def _cycle_context_key(self, name: str) -> bool:
        """Cycle a context source when its numeric overlay key is pressed."""
        for item in self._context_items:
            if name.lower() != str(item.get("key") or "").lower():
                continue
            self._cycle_context_item(item)
            return True
        return False

    def _cycle_context_item(self, item: dict) -> None:
        """Cycle one context source through its explicit prompt states."""
        if item.get("locked"):
            return
        self._active_panel = "context"
        item_id = str(item.get("id") or "")
        state = str(item.get("state") or "off").lower()
        if state == "auto":
            item["state"] = "off"
        elif state == "off":
            item["state"] = "on"
        else:
            item["state"] = "off"
        item["touched"] = True
        self._note_interaction()
        self._resize_for_context_preview()
        self.update()
        if item_id in {"ambient", "browser"} and state == "off" and item["state"] != "off":
            QTimer.singleShot(0, lambda: self.context_source_reenabled.emit(item_id))
        if item_id == "screenshot" and state == "off" and item["state"] == "on":
            QTimer.singleShot(0, self.screenshot_snip_requested.emit)
        if (
            item_id == "selection"
            and state == "off"
            and item["state"] == "on"
            and not item.get("stale")
            and item.get("capture_on_enable", True)
        ):
            # A stale chip already carries an earlier selection supervisor-side;
            # plain re-enable attaches it without a new interactive capture.
            QTimer.singleShot(0, lambda: self.selection_capture_requested.emit(self.current_custom_text()))

    def _remove_button_at(self, pos: QPoint) -> tuple[str, str] | None:
        """Return (item id, source id) for the preview-row X under a point."""
        for rect, item_id, source_id in self._ctx_remove_rects:
            if rect.adjusted(-3, -3, 3, 3).contains(pos):
                return item_id, source_id
        return None

    def _remove_context_entry(self, item_id: str, source_id: str) -> None:
        """Remove one bottom-list context row; an emptied group switches off."""
        for item in self._context_items:
            if str(item.get("id") or "") != item_id:
                continue
            if source_id:
                sources = [s for s in (item.get("sources") or []) if isinstance(s, dict)]
                kept = [
                    s for s in sources
                    if str(
                        s.get("id")
                        or " ".join(str(s.get("label") or "").split())
                    ) != source_id
                ]
                item["sources"] = kept
                if not kept:
                    item["state"] = "off"
                    item["touched"] = True
                # The supervisor drops the matching document block from the
                # prompt as well - the preview list is not just cosmetic.
                self.context_source_removed.emit(item_id, source_id)
            else:
                item["state"] = "off"
                item["touched"] = True
            self._note_interaction()
            self._resize_for_context_preview()
            self.update()
            return

    def _context_item_at(self, pos: QPoint) -> dict | None:
        """Return the context chip under a mouse position."""
        if self._active_panel != "context" or not self._context_items:
            return None
        top = _PAD_V + (_CONV_H if self._show_conversation_selector else 0) + _CTX_TOP
        for item, rect in self._context_chip_rects(top):
            if rect.contains(pos):
                return item
        return None

    def _context_chip_width(self) -> int:
        """Return one of the two source-column widths beside the estimate."""
        sources_w = _W - _PAD_H * 2 - _CTX_EST_GAP - self._context_estimate_width()
        return max(44, min(_CTX_CHIP_W, (sources_w - _CTX_GAP) // 2))

    @staticmethod
    def _context_estimate_width() -> int:
        """Reserve the measured width of the widest estimate line."""
        label_w = QFontMetrics(_mono_font(8.5, tracking=0.12)).horizontalAdvance("EST. TOKENS")
        number_w = QFontMetrics(_serif_font(30, QFont.Weight.DemiBold)).horizontalAdvance("≈8,000")
        budget_w = QFontMetrics(_mono_font(9, tracking=0.12)).horizontalAdvance("OF 8,000")
        return max(92, label_w, number_w, budget_w)

    def _context_chip_rects(self, top: int) -> list[tuple[dict, QRect]]:
        """Return context chip hit/paint rects."""
        chip_w = self._context_chip_width()
        rects: list[tuple[dict, QRect]] = []
        for idx, item in enumerate(self._context_items):
            column = idx % 2
            row = idx // 2
            x = _PAD_H + column * (chip_w + _CTX_GAP)
            y = top + row * (_CTX_CHIP_H + _CTX_ROW_GAP)
            rects.append((item, QRect(x, y, chip_w, _CTX_CHIP_H)))
        return rects

    def _toggle_conversation_mode(self) -> bool:
        """Swap between continuing the selected chat and starting fresh."""
        self._conversation_choice_touched = True
        if self._conversation_mode == "continue":
            self._conversation_mode = "new"
        elif self._filtered_conversation_options():
            self._conversation_mode = "continue"
            if self._conversation_index is None:
                self._conversation_index = int(self._filtered_conversation_options()[0]["index"])
        else:
            self._conversation_mode = "new"
        self._apply_context_defaults_for_conversation_mode(restore_new=True)
        self._note_interaction()
        self.update()
        return True

    def _menu_style(self) -> str:
        p = _theme_palette()
        return (
            f"QMenu {{ background: {p['surface'].name()}; color: {p['label'].name()}; "
            f"border: 1px solid {p['border'].name()}; }}"
            "QMenu::item { padding: 7px 18px; }"
            f"QMenu::item:selected {{ background: {p['surface_raised'].name()}; color: {p['key'].name()}; }}"
        )

    def _show_project_menu(self) -> None:
        """Open the project selector menu."""
        self._note_interaction()
        self._timer.stop()
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_style())
        current = str(self._project_id or "")
        for option in self._project_options:
            project_id = str(option.get("id") or "")
            name = str(option.get("name") or t("Project"))
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(project_id == current)
            action.triggered.connect(
                lambda _checked=False, project_id=project_id: self._set_project_choice(project_id)
            )
        menu.addSeparator()
        menu.addAction(t("+ New project..."), self._create_project_interactive)
        self._project_menu = menu
        def _menu_closed() -> None:
            self._project_menu = None
            self._restart_timer()
        menu.aboutToHide.connect(_menu_closed)
        menu.popup(self.mapToGlobal(self._project_rect.bottomLeft()))

    def _set_project_choice(self, project_id: str) -> None:
        self._project_id = str(project_id or self._default_project_id())
        self._new_project_name = ""
        self._conversation_mode = "new"
        self._conversation_index = None
        self._apply_context_defaults_for_conversation_mode(restore_new=True)
        self._note_interaction()
        self.update()

    def _create_project_interactive(self) -> None:
        self._project_dialog_open = True
        try:
            name, ok = QInputDialog.getText(self, t("New project"), t("Project name:"))
        finally:
            self._project_dialog_open = False
        name = " ".join(str(name or "").split()).strip()
        if not ok or not name:
            return
        self._project_id = _NEW_PROJECT_SENTINEL
        self._new_project_name = name
        self._conversation_mode = "new"
        self._conversation_index = None
        self._apply_context_defaults_for_conversation_mode(restore_new=True)
        self._note_interaction()
        self.update()

    def _show_conversation_menu(self) -> None:
        """Open the chat history selector menu."""
        self._note_interaction()
        self._timer.stop()
        options = self._filtered_conversation_options()
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_style())
        current = self._conversation_index
        for option in options:
            idx = int(option["index"])
            title = str(option.get("title") or t("Conversation"))
            subtitle = str(option.get("subtitle") or "")
            label = title if not subtitle else f"{title}  ·  {subtitle}"
            action = menu.addAction(label)
            action.setData(idx)
            action.setCheckable(True)
            action.setChecked(idx == current and self._conversation_mode == "continue")
            action.triggered.connect(lambda _checked=False, idx=idx: self._set_conversation_choice(idx))
        self._conversation_menu = menu
        def _menu_closed() -> None:
            self._conversation_menu = None
            self._restart_timer()
        menu.aboutToHide.connect(_menu_closed)
        menu.popup(self.mapToGlobal(self._conversation_list_rect.bottomLeft()))

    def _set_conversation_new(self) -> None:
        self._conversation_choice_touched = True
        self._conversation_mode = "new"
        self._conversation_index = None
        self._apply_context_defaults_for_conversation_mode(restore_new=True)
        self._note_interaction()
        self.update()

    def _set_conversation_choice(self, idx: int) -> None:
        self._conversation_choice_touched = True
        self._conversation_mode = "continue"
        self._conversation_index = int(idx)
        self._apply_context_defaults_for_conversation_mode(restore_new=True)
        self._note_interaction()
        self.update()

    def _handle_conversation_click(self, pos: QPoint) -> bool:
        """Handle clicks in the continuation selector."""
        if not self._show_conversation_selector:
            return False
        if self._project_rect.contains(pos):
            self._show_project_menu()
            return True
        if self._conversation_mode_rect.contains(pos):
            self._toggle_conversation_mode()
            return True
        if self._conversation_list_rect.contains(pos):
            if self._conversation_mode == "continue" and self._filtered_conversation_options():
                self._show_conversation_menu()
                self.update()
            else:
                self._note_interaction()
            return True
        return False

    def _panel_tab_at(self, pos: QPoint) -> str:
        """Return the sibling panel under a mouse position."""
        if self._context_tab_rect.contains(pos):
            return "context"
        if self._tools_tab_rect.contains(pos):
            return "tools"
        return ""

    def _cycle_context_at(self, pos: QPoint) -> bool:
        """Cycle a context chip at a mouse position."""
        item = self._context_item_at(pos)
        if item is None:
            return False
        self._cycle_context_item(item)
        return True

    def _mark_raw_context_key(self, name: str) -> None:
        """Remember a raw-hook context key so Qt does not toggle it twice."""
        import time

        self._last_raw_context_key = str(name or "").lower()
        self._last_raw_context_at = time.monotonic()

    def _is_duplicate_qt_context_key(self, name: str) -> bool:
        """Return whether this Qt key press was already handled by the raw hook."""
        import time

        key = str(name or "").lower()
        return (
            bool(key)
            and key == self._last_raw_context_key
            and time.monotonic() - self._last_raw_context_at < 0.18
        )

    def _context_warning_at(self, pos: QPoint) -> tuple[int, str] | None:
        """Return the warning tooltip at a mouse position, if any."""
        for idx, (rect, text) in enumerate(self._warning_rects):
            if rect.contains(pos):
                return idx, text
        return None

    def mouseMoveEvent(self, event):  # noqa: N802
        """Show context warning reasons, and highlight the hovered intent row."""
        pos = event.position().toPoint()
        if not self._custom_mode and self._selection_pending_idx is None:
            row_idx = self._row_at(pos)
            if row_idx != self._hovered:
                self._hovered = row_idx
                self.setCursor(
                    Qt.CursorShape.PointingHandCursor
                    if (
                        row_idx is not None
                        or self._remove_button_at(pos) is not None
                        or bool(self._panel_tab_at(pos))
                        or self._tool_item_at(pos) is not None
                    )
                    else Qt.CursorShape.ArrowCursor
                )
                self.update()
            elif row_idx is None:
                self.setCursor(
                    Qt.CursorShape.PointingHandCursor
                    if (
                        self._remove_button_at(pos) is not None
                        or bool(self._panel_tab_at(pos))
                        or self._tool_item_at(pos) is not None
                    )
                    else Qt.CursorShape.ArrowCursor
                )
        found = self._context_warning_at(pos)
        if found is None:
            if self._last_warning_idx is not None:
                QToolTip.hideText()
                self._last_warning_idx = None
            super().mouseMoveEvent(event)
            return
        idx, text = found
        if idx != self._last_warning_idx:
            show_tooltip_text(event.globalPosition().toPoint(), text, self)
            self._last_warning_idx = idx
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        """Toggle context chips, or select an intent row, when clicked."""
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if self._handle_conversation_click(pos):
                event.accept()
                return
            panel = self._panel_tab_at(pos)
            if panel:
                self._set_active_panel(panel)
                event.accept()
                return
            if self._toggle_tool_at(pos):
                event.accept()
                return
            removed = self._remove_button_at(pos)
            if removed is not None:
                self._remove_context_entry(*removed)
                event.accept()
                return
            if self._cycle_context_at(pos):
                event.accept()
                return
            # Clicking a row is equivalent to pressing its WASD/shortcut key.
            if not self._custom_mode:
                idx = self._row_at(pos)
                if idx is not None:
                    self._select(idx, drop_trigger_key=False)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def _row_at(self, pos) -> int | None:
        """Return the intent row index under a point, or None."""
        for idx, rect in enumerate(self._row_rects):
            if rect.contains(pos):
                return idx
        return None

    # ── Key input ─────────────────────────────────────────────────────────

    def _select(self, idx: int, *, drop_trigger_key: bool = True):
        """Handle select for intent overlay."""
        if self._handled or self._selection_pending_idx is not None:
            return
        if not self._rows[idx].get("available", True):
            return
        if self._rows[idx]["is_custom"]:
            self._hovered = idx
            self._debug(f"select-custom idx={idx}")
            self._unhook()
            QTimer.singleShot(
                0,
                lambda: self._enter_custom_mode(drop_trigger_key=drop_trigger_key),
            )
            return
        self._selection_pending_idx = idx
        self._hovered = idx
        self.update()
        QTimer.singleShot(80, lambda: self._fire(idx))

    def _custom_row_index_without_key(self) -> int | None:
        """Return the custom row index when it has no configured shortcut."""
        for idx, row in enumerate(self._rows):
            if row["is_custom"] and not row["glyph"]:
                return idx
        return None

    def _enter_auto_custom_mode(self) -> None:
        """Open the custom prompt directly when no custom shortcut is configured."""
        if self._handled or self._custom_mode or self._auto_custom_mode is None:
            return
        self._hovered = self._auto_custom_mode
        self._debug(f"auto-custom idx={self._auto_custom_mode}")
        self._unhook()
        self._enter_custom_mode(drop_trigger_key=False)

    def _enter_custom_mode(self, *, drop_trigger_key: bool = True):
        """Handle enter custom mode for intent overlay."""
        self._custom_mode = True
        self._prefilled_custom_mode = False
        self._debug("enter-custom-before")
        self._timer.stop()
        self._prompt_input_h = _INPUT_MIN_H
        new_h = self._normal_h + self._prompt_input_extra()
        self.setFixedSize(_W, new_h)
        self._move_to_screen_center(new_h)
        self._input_line.setGeometry(self._prompt_editor_rect(self._prompt_input_h))
        self._input_line.show()
        self._focus_custom_input()
        self.update()
        self._drop_next_keypress = drop_trigger_key
        self._debug("enter-custom-after")
        for delay_ms in (25, 75, 150):
            QTimer.singleShot(delay_ms, self._focus_custom_input)

    def _enter_prefilled_custom_mode(self) -> None:
        """Show a custom prompt value while keeping picker shortcut focus."""
        if self._handled or self._custom_mode or self._prefilled_custom_mode:
            return
        if not self._initial_custom_text:
            return
        self._prefilled_custom_mode = True
        self._prompt_input_h = _INPUT_MIN_H
        new_h = self._normal_h + self._prompt_input_extra()
        self.setFixedSize(_W, new_h)
        self._move_to_screen_center(new_h)
        self._input_line.setGeometry(self._prompt_editor_rect(self._prompt_input_h))
        self._input_line.setText(self._initial_custom_text)
        self._input_line.show()
        self._schedule_prompt_input_resize()
        self.update()
        self._focus_overlay()

    def _prompt_input_visible(self) -> bool:
        """Return whether the custom prompt input is visible below the rows."""
        return bool(self._custom_mode or self._prefilled_custom_mode)

    def _prompt_input_extra(self) -> int:
        """Return total space reserved below the rows for the prompt editor."""
        return _INPUT_EXTRA + max(0, self._prompt_input_h - _INPUT_MIN_H)

    def _prompt_input_max_height(self) -> int:
        """Return the tallest editor that keeps the overlay on the usable screen."""
        screen_h = self._screen_geometry.height()
        if screen_h <= 0:
            return _INPUT_MAX_H
        max_overlay_h = max(0, screen_h - _SCREEN_MARGIN)
        available_h = (
            max_overlay_h
            - self._normal_h
            - _INPUT_EXTRA
            + _INPUT_MIN_H
        )
        return max(_INPUT_MIN_H, available_h)

    def _schedule_prompt_input_resize(self) -> None:
        """Coalesce document relayouts before measuring wrapped prompt lines."""
        if self._prompt_resize_pending:
            return
        self._prompt_resize_pending = True
        QTimer.singleShot(0, self._resize_prompt_input)

    def _resize_prompt_input(self) -> None:
        """Grow with wrapped lines until the overlay reaches the usable screen."""
        self._prompt_resize_pending = False
        if not self._prompt_input_visible() or self._input_line.isHidden():
            return
        # QPlainTextDocumentLayout can report a single-line height until the
        # offscreen Linux platform plugin paints the editor. Measure wrapping
        # explicitly so synchronous resizes behave the same on every platform.
        # The viewport can temporarily retain QPlainTextEdit's 100 px default
        # width before an offscreen backend propagates setGeometry(). The
        # editor geometry is authoritative. QTextLayout measures the glyph
        # area directly, so its line width should use that stable geometry.
        text_width = max(1, self._input_line.width())
        wrap_option = QTextOption()
        wrap_option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document_h = 0.0
        font = self._input_line.font()
        line_height = float(QFontMetrics(font).lineSpacing())
        for paragraph in self._input_line.toPlainText().split("\n"):
            layout = QTextLayout(paragraph or " ", font)
            layout.setTextOption(wrap_option)
            layout.beginLayout()
            paragraph_h = 0.0
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(text_width)
                paragraph_h += line.height()
            layout.endLayout()
            document_h += max(line_height, paragraph_h)
        document_h = int(document_h + 0.999)
        required_h = max(_INPUT_MIN_H, document_h + 12)
        max_input_h = self._prompt_input_max_height()
        desired_h = min(max_input_h, required_h)
        scrollbar_policy = (
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if required_h > max_input_h
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        if self._input_line.verticalScrollBarPolicy() != scrollbar_policy:
            self._input_line.setVerticalScrollBarPolicy(scrollbar_policy)
        if desired_h == self._prompt_input_h:
            return
        self._prompt_input_h = desired_h
        total_h = self._normal_h + self._prompt_input_extra()
        self.setFixedSize(_W, total_h)
        self._input_line.setGeometry(self._prompt_editor_rect(self._prompt_input_h))
        self._move_to_screen_center(total_h)
        self.update()

    def _focus_custom_input(self) -> None:
        """Focus custom input."""
        if not self._custom_mode or self._input_line.isHidden():
            return
        if self._input_line.hasFocus() and (_IS_MAC or self._input_grabbed_keyboard):
            return
        self._debug("focus-custom-before")
        self._release_overlay_keyboard()
        self.raise_()
        if _IS_WIN:
            self._win_force_foreground()
        self.activateWindow()
        self._input_line.setFocus(Qt.FocusReason.OtherFocusReason)
        if self._qt_keyboard_grabs_enabled() and not self._input_grabbed_keyboard:
            try:
                self._input_line.grabKeyboard()
                self._input_grabbed_keyboard = True
            except Exception:
                pass
        self._debug("focus-custom-after")

    def _focus_overlay(self) -> None:
        """Give the picker itself keyboard focus when not typing a custom prompt."""
        if self._custom_mode:
            return
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._grab_overlay_keyboard()
        self._debug("focus-overlay")

    def _grab_overlay_keyboard(self) -> None:
        """Route overlay-local shortcut keys to this Qt popup on Linux."""
        if (
            _IS_MAC
            or _IS_WIN
            or self._custom_mode
            or self._overlay_grabbed_keyboard
            or not self._qt_keyboard_grabs_enabled()
        ):
            return
        try:
            self.grabKeyboard()
            self._overlay_grabbed_keyboard = True
        except Exception:
            pass

    def _qt_keyboard_grabs_enabled(self) -> bool:
        """Return whether this platform should use Qt native keyboard grabs."""
        if _IS_MAC:
            return False
        if _IS_WIN:
            return True
        if _IS_LINUX:
            return _linux_qt_keyboard_grabs_enabled()
        return True

    def _release_overlay_keyboard(self) -> None:
        """Release a Qt keyboard grab owned by the picker shell."""
        if not self._overlay_grabbed_keyboard:
            return
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        self._overlay_grabbed_keyboard = False

    def _release_input_keyboard(self) -> None:
        """Release a Qt keyboard grab owned by the custom prompt input."""
        if not self._input_grabbed_keyboard:
            return
        try:
            self._input_line.releaseKeyboard()
        except Exception:
            pass
        self._input_grabbed_keyboard = False

    def changeEvent(self, event):
        """Handle change event for intent overlay."""
        from PySide6.QtCore import QEvent
        # Once we've gained activation, cancel when the window loses it (user
        # clicked another app/window). macOS needs this because it cannot use
        # Popup here; Windows/Linux need it when custom prompt mode grabs keys.
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self._was_activated = True
            elif self._was_activated and not self._handled:
                QTimer.singleShot(0, self._cancel_if_focus_left)
        super().changeEvent(event)

    def eventFilter(self, obj, event):
        """Handle event filter for intent overlay."""
        from PySide6.QtCore import QEvent
        input_line = getattr(self, "_input_line", None)
        if obj is input_line and event.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(0, self._cancel_if_focus_left)
        if obj is input_line and event.type() in {
            QEvent.Type.KeyPress,
            QEvent.Type.ShortcutOverride,
        }:
            self._debug_key("input-filter-key", event)
            if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()
                return True
            if event.matches(QKeySequence.StandardKey.Paste) and self._paste_clipboard_context():
                event.accept()
                return True
            if event.key() == Qt.Key.Key_Escape:
                self._cancel(restore_target_focus=True)
                return True
            if (
                event.type() == QEvent.Type.KeyPress
                and event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
                and not bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            ):
                self._fire_custom()
                return True
            if self._drop_next_keypress and event.type() == QEvent.Type.KeyPress:
                self._debug_key("input-filter-before-drop", event)
                custom_key = next((r["glyph"].lower() for r in self._rows if r["is_custom"]), "")
                if event.text().lower() == custom_key:
                    self._drop_next_keypress = False
                    self._debug_key("input-filter-drop-trigger", event)
                    return True  # consume the triggering key so it never reaches the field
                else:
                    self._drop_next_keypress = False  # not the trigger key — let it through
                    self._debug_key("input-filter-pass-first-key", event)
        return super().eventFilter(obj, event)

    def _cancel_if_focus_left(self) -> None:
        """Cancel when focus has moved outside the overlay."""
        if self._handled or not self.isVisible():
            return
        if self._conversation_menu is not None and self._conversation_menu.isVisible():
            return
        if self._project_menu is not None and self._project_menu.isVisible():
            return
        if self._project_dialog_open:
            return
        focus = QApplication.focusWidget()
        if focus is not None and (focus is self or self.isAncestorOf(focus)):
            return
        self._debug("focus-left-cancel")
        self._cancel()

    def _fire_custom(self):
        """Handle fire custom for intent overlay."""
        text = self._input_line.text().strip()
        if not text:
            return
        self._handled = True
        self._unhook()
        self._timer.stop()
        custom_row = next(r for r in self._rows if r["is_custom"])
        self._selected_intent_routing = dict(custom_row.get("routing") or {})
        self.intent_chosen.emit(custom_row["glyph"], text)
        self.close()

    def _on_raw_key(self, name: str):
        """Handle raw key events."""
        if getattr(self, "_closed", False) or self._handled or self._custom_mode:
            return
        if name in ('escape', 'esc'):
            self._cancel(restore_target_focus=True)
            return
        if name.lower() in {"space", "spacebar"}:
            if self._space_starts_new_chat:
                self._toggle_conversation_mode()
            self._mark_raw_context_key("space")
            return
        if name.lower() == "t" and self._tools_shortcut_available():
            self._set_active_panel("tools")
            self._mark_raw_context_key("t")
            return
        if self._cycle_context_key(name):
            self._mark_raw_context_key(name)
            return
        for i, row in enumerate(self._rows):
            if name.lower() == row['glyph'].lower():
                self._select(i, drop_trigger_key=False)
                return

    def keyPressEvent(self, event):
        """Handle key press event for intent overlay."""
        self._debug_key("overlay-keypress", event)
        if event.matches(QKeySequence.StandardKey.Paste) and self._paste_clipboard_context():
            event.accept()
            return
        if self._custom_mode:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Space:
            if self._space_starts_new_chat and not self._is_duplicate_qt_context_key("space"):
                self._toggle_conversation_mode()
            event.accept()
            return
        if self._prefilled_custom_mode and event.key() in {
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        }:
            self._fire_custom()
            event.accept()
            return
        key_map: dict[Qt.Key, int] = {}
        for i, row in enumerate(self._rows):
            qt_key = getattr(Qt.Key, f"Key_{row['glyph']}", None)
            if qt_key is not None:
                key_map[qt_key] = i
        text = (event.text() or "").strip()
        if text and self._is_duplicate_qt_context_key(text):
            event.accept()
            return
        if text.lower() == "t" and self._tools_shortcut_available():
            self._set_active_panel("tools")
            event.accept()
            return
        if text and self._cycle_context_key(text):
            event.accept()
            return
        idx = key_map.get(event.key())
        if idx is not None:
            self._select(idx)
            event.accept()
            return
        elif event.key() == Qt.Key.Key_Escape:
            self._cancel(restore_target_focus=True)
            event.accept()
            return
        super().keyPressEvent(event)

    def _raw_shortcut_names(self) -> list[str]:
        """Return overlay-local keys that should be captured by the raw hook."""
        keys: list[str] = ["escape", "space"]
        if self._tools_shortcut_available():
            keys.append("t")
        for item in self._context_items:
            key = str(item.get("key") or "").strip().lower()
            if key and key not in keys:
                keys.append(key)
        for row in self._rows:
            key = str(row.get("glyph") or "").strip().lower()
            if key and key != "?" and key not in keys:
                keys.append(key)
        return keys

    def _win_force_foreground(self) -> None:
        """Force the overlay to the foreground on Windows, past the foreground lock.

        The hotkey is received by the native worker, but THIS (UI) process shows
        the window — so Windows denies plain SetForegroundWindow/activateWindow and
        the overlay never gets keyboard focus (you'd have to click it before WASD
        works). Briefly attaching to the current foreground thread's input queue
        lifts that restriction for the duration of the call. Best-effort; never
        raises.
        """
        try:
            import ctypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = int(self.winId())
            fg = user32.GetForegroundWindow()
            if not hwnd or fg == hwnd:
                return
            target_tid = user32.GetWindowThreadProcessId(fg, None)
            our_tid = kernel32.GetCurrentThreadId()
            attached = bool(user32.AttachThreadInput(target_tid, our_tid, True))
            try:
                user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.SetActiveWindow(hwnd)
                user32.SetFocus(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(target_tid, our_tid, False)
        except Exception:
            pass

    def activate_after_context(self) -> None:
        """Enable and focus a picker shown early for hotkey-time context capture."""
        if self._handled:
            return
        if self._focus_deferred:
            self._focus_deferred = False
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
            self.setEnabled(True)
        if self.isHidden():
            self.show()
            self.raise_()
        self._start_interaction()

    def _start_interaction(self) -> None:
        """Install key routing and focus the picker exactly once."""
        if self._interaction_started or self._handled:
            return
        self._interaction_started = True
        if _IS_WIN:
            self._win_force_foreground()
        else:
            self.activateWindow()
            self._focus_overlay()
        if _IS_WIN:
            import keyboard  # type: ignore

            def _on_key_event(e):
                """Forward suppressed Windows key-down events to the Qt thread."""
                if (
                    not e
                    or getattr(e, "event_type", None) != "down"
                    or not getattr(e, "name", None)
                    or self._closed
                ):
                    return
                self._raw_key.emit(e.name)

            # Capture only overlay command keys. A suppress-all hook can swallow
            # modifier key-up events from the hotkey that opened the picker,
            # leaving Windows with a stuck modifier after the overlay closes.
            hooks = []
            for name in self._raw_shortcut_names():
                try:
                    hooks.append(keyboard.on_press_key(name, _on_key_event, suppress=True))
                except Exception:
                    pass
            self._kb_hook = hooks
        else:
            # Keep overlay-local keys in Qt on Unix-like desktops. A second
            # pynput listener inside the UI process is fragile in frozen Linux
            # builds, especially across X11/Wayland focus changes while the
            # intent popup is being clicked or resized.
            self._kb_hook = None
            self.setFocus(Qt.FocusReason.PopupFocusReason)
            self._grab_overlay_keyboard()
        if self._initial_custom_text:
            QTimer.singleShot(0, self._enter_prefilled_custom_mode)
            if _IS_WIN or self._focus_overlay_requested:
                for delay_ms in (25, 75, 150):
                    QTimer.singleShot(delay_ms, self._focus_overlay)
        elif self._auto_custom_mode is not None:
            QTimer.singleShot(0, self._enter_auto_custom_mode)
        elif _IS_WIN:
            for delay_ms in (25, 75, 150):
                QTimer.singleShot(delay_ms, self._focus_overlay)
        elif not _IS_MAC:
            for delay_ms in (25, 75, 150):
                QTimer.singleShot(delay_ms, self._focus_overlay)

    def showEvent(self, event):
        """Show event."""
        super().showEvent(event)
        self.raise_()
        self._closed = False
        self._debug("show-deferred" if self._focus_deferred else "show")
        if not self._focus_deferred:
            self._start_interaction()

    # ── Cleanup / fire ────────────────────────────────────────────────────

    def _unhook(self):
        """Handle unhook for intent overlay."""
        self._closed = True
        if self._kb_hook is not None:
            try:
                if _IS_WIN:
                    import keyboard  # type: ignore
                    hooks = self._kb_hook if isinstance(self._kb_hook, list) else [self._kb_hook]
                    for hook in hooks:
                        keyboard.unhook(hook)
                else:
                    self._kb_hook.stop()
            except Exception:
                pass
            self._kb_hook = None
        self._release_overlay_keyboard()
        self._release_input_keyboard()

    def closeEvent(self, event):
        """Close event."""
        # Qt may try to close a disabled Popup shown without activation. During
        # deferred context capture that is a platform lifecycle event, not a
        # user dismissal. Internally superseded pickers are already marked
        # handled by close_without_cancel(), so those still close normally.
        if self._focus_deferred and not self._handled:
            event.ignore()
            return
        self._cancel_if_unhandled()
        super().closeEvent(event)

    def hideEvent(self, event):
        """Treat unexpected hides as cancellation so the icon returns idle."""
        # A non-activating disabled Popup can be hidden by Qt before the native
        # context capture completes (notably under the offscreen Windows
        # backend). It is intentionally inert at this point, so the hide cannot
        # represent a user cancellation. Activation re-shows it once capture is
        # complete.
        if self._focus_deferred:
            super().hideEvent(event)
            return
        if self._suppress_hide_cancel:
            self._suppress_hide_cancel = False
            super().hideEvent(event)
            return
        self._cancel_if_unhandled()
        super().hideEvent(event)

    def hide_without_cancel(self) -> None:
        """Hide temporarily for an interactive capture without cancelling."""
        self._suppress_hide_cancel = True
        self.hide()

    def close_without_cancel(self) -> None:
        """Close an internally superseded picker without emitting cancelled."""
        self._handled = True
        self.close()

    def _cancel_if_unhandled(self) -> bool:
        """Emit cancellation for lifecycle closes that bypass _cancel()."""
        if self._handled:
            self._unhook()
            return False
        self._selection_pending_idx = None
        self._handled = True
        self._unhook()
        self._timer.stop()
        self.cancelled.emit()
        return True

    def _fire(self, idx: int):
        """Handle fire for intent overlay."""
        if self._handled or self._selection_pending_idx != idx:
            return
        self._selection_pending_idx = None
        self._handled = True
        self._unhook()
        self._timer.stop()
        row = self._rows[idx]
        self._selected_intent_routing = dict(row.get("routing") or {})
        self.intent_chosen.emit(row["glyph"], row["prompt"])
        self.close()

    def _cancel(self, *, restore_target_focus: bool = False):
        """Cancel the intent overlay workflow."""
        if self._handled:
            return
        target_id = self._target_hwnd if restore_target_focus else 0
        self._cancel_if_unhandled()
        self.close()
        if target_id:
            _restore_foreground_window(target_id)
