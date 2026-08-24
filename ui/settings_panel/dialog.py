"""
ui/settings.py - Settings dialog.

A plain GUI for editing all user-configurable values.
Reads from and writes to the .env file.
Launch via tray icon → Settings, or call open_settings().
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QMimeData, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QDrag,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QIcon,
    QPainter,
    QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import ui.settings_panel.env as settings_env
from core import secret_store, settings_profiles
from core.custom_connections import (
    connection_id as _custom_connection_id,
)
from core.custom_connections import (
    env_keys as _custom_connection_env_keys,
)
from core.custom_connections import (
    env_values as _custom_connection_env_values,
)
from core.custom_connections import (
    is_custom as _is_custom_route,
)
from core.custom_connections import (
    load_connections as _load_custom_connections,
)
from core.custom_connections import (
    route_id as _custom_route_id,
)
from core.custom_connections import (
    secret_name as _custom_secret_name,
)
from core.system.env_utils import (
    format_tool_modes,
    normalize_file_access_mode,
    normalize_screenshot_mode,
    parse_tool_modes,
)
from core.system.paths import ASSETS_DIR
from ui.i18n import (
    COMBO_I18N_SOURCE_ROLE,
    LANGUAGE_OPTIONS,
    localize_widget_tree,
    t,
)
from ui.i18n import (
    current_language as current_app_language,
)
from ui.optional_install_dialog import OptionalInstallDialog
from ui.settings_panel import context_controls
from ui.settings_panel.helpers import (
    NoScrollCombo as _NoScrollCombo,
)
from ui.settings_panel.helpers import (
    WarningHeaderLabel as _WarningHeaderLabel,
)
from ui.settings_panel.helpers import (
    expanding_form_layout as _expanding_form_layout,
)
from ui.settings_panel.helpers import (
    parse_fallback_rows,
)
from ui.settings_panel.hotkey_capture import HotkeyCaptureEdit
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen

ENV_PATH = settings_env.ENV_PATH
_settings_log = logging.getLogger("openwand.settings")
_settings_dialog: SettingsDialog | None = None
_settings_open_pending = False
_SETUP_CHECK_STATUS_LABELS = {
    "pass": "PASS",
    "ok": "OK",
    "warn": "WARN",
    "fail": "FAIL",
}
_TTS_TIMESTAMPLESS_PROVIDERS = {
    "elevenlabs",
    "openai",
    "openai_compatible",
    "gpt_sovits",
    "kokoro",
}
_TTS_TIMING_NOTICE = (
    "This provider does not send real word timestamps. The bubble uses normal reveal speed "
    "instead of audio-synced word highlighting."
)
_AUTH_STATUS_TIMEOUT_MS = 7000
_SETTINGS_NAV_ITEM_HEIGHT = 33
_SETTINGS_NAV_DIRTY_ROLE = int(Qt.ItemDataRole.UserRole) + 41
_THEME_REPAINT_KEYS = frozenset({
    "THEME_MODE",
    "DARK_MODE",  # legacy compatibility setting
    "THEME_DARK_BG",
    "THEME_DARK_SURFACE",
    "THEME_DARK_TEXT",
    "THEME_DARK_ACCENT",
    "THEME_LIGHT_BG",
    "THEME_LIGHT_SURFACE",
    "THEME_LIGHT_TEXT",
    "THEME_LIGHT_ACCENT",
})


def _settings_change_requires_theme_repaint(changed_keys: set[str]) -> bool:
    """Return whether changed settings feed the shared Qt palette or stylesheet."""
    return not _THEME_REPAINT_KEYS.isdisjoint(changed_keys)


def _settings_font(px: float, *, semibold: bool = False, mono: bool = False) -> QFont:
    """Return a design font using CSS-pixel sizing at the standard 96 DPI."""
    families = set(QFontDatabase.families())
    if mono:
        family = next(
            (
                name
                for name in ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono")
                if name in families
            ),
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family(),
        )
    else:
        family = _settings_ui_family()
    font = QFont(family)
    font.setPointSizeF(px * 0.75)
    font.setWeight(QFont.Weight.DemiBold if semibold else QFont.Weight.Normal)
    return font


def _settings_ui_family() -> str:
    """Return the clean platform UI face used throughout Settings."""
    families = set(QFontDatabase.families())
    language = current_app_language().strip().lower()
    language_families: tuple[str, ...] = ()
    if language in {"zh-hant", "zh-tw", "zh-hk"}:
        language_families = ("Microsoft JhengHei UI", "Microsoft JhengHei")
    elif language.startswith("zh"):
        language_families = ("Microsoft YaHei UI", "Microsoft YaHei")
    return next(
        (
            name
            for name in (
                *language_families,
                "Segoe UI Variable Text",
                "Segoe UI",
                "SF Pro Text",
                "Inter",
                "Noto Sans",
                "DejaVu Sans",
            )
            if name in families
        ),
        QApplication.font().family(),
    )


def _compact_ui_font(px: float, *, semibold: bool = False) -> QFont:
    """Return a tightly hinted sans-serif font for dense controls and status rows."""
    family = _settings_ui_family()
    font = QFont(family)
    font.setPixelSize(round(px))
    font.setWeight(QFont.Weight.DemiBold if semibold else QFont.Weight.Normal)
    font.setKerning(True)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


class _FlexibleWrapLabel(QLabel):
    """A wrapped label whose row cannot collapse below its preferred text height."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        # QLabel's narrow minimumSizeHint can win over its wrapped size hint in
        # a resizable QScrollArea. Fallback CJK fonts can then paint an extra
        # line outside the allocated row. Preserve the larger content height.
        wrapped_height = QLabel.heightForWidth(self, width)
        preferred_height = QLabel.sizeHint(self).height()
        return max(wrapped_height, preferred_height)


class _SettingsNavigationDelegate(QStyledItemDelegate):
    """Paint the 9b navigation rows and their four-pixel dirty marker."""

    def __init__(self, parent: QWidget | None = None, *, dark: bool = True) -> None:
        super().__init__(parent)
        self._dark = dark

    def set_dark(self, dark: bool) -> None:
        self._dark = dark

    def sizeHint(self, option, index):  # noqa: N802 - Qt override
        hint = super().sizeHint(option, index)
        hint.setHeight(_SETTINGS_NAV_ITEM_HEIGHT)
        return hint

    def paint(self, painter: QPainter, option, index) -> None:
        from ui.shared.theme import theme_colors

        c = theme_colors(self._dark)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if self._dark:
            well, hover, text, dim, accent, selected_text = (
                "#0e1013",
                "#1c1f23",
                "#e9e6e0",
                "#8b8a86",
                "#d8a145",
                "#d8a145",
            )
        else:
            well, hover, text, dim, accent, selected_text = (
                c["accent_fill"],
                c["button_hover"],
                c["text"],
                c["text_dim"],
                c["accent"],
                c["text"],
            )

        painter.save()
        rect = option.rect.adjusted(0, 0, 0, -1)
        if selected:
            painter.fillRect(rect, QColor(well))
        elif hovered:
            painter.fillRect(rect, QColor(hover))

        font = _settings_font(14, semibold=selected)
        painter.setFont(font)
        painter.setPen(QColor(selected_text if selected else text if hovered else dim))
        dirty = bool(index.data(_SETTINGS_NAV_DIRTY_ROLE))
        right_gutter = 25 if dirty else 11
        text_rect = rect.adjusted(11, 0, -right_gutter, 0)
        label = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        clipped = QFontMetrics(font).elidedText(
            label,
            Qt.TextElideMode.ElideRight,
            max(0, text_rect.width()),
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            clipped,
        )
        if dirty:
            marker = option.rect.adjusted(option.rect.width() - 15, 16, -11, -16)
            marker.setWidth(4)
            marker.setHeight(4)
            marker.moveTop(option.rect.center().y() - 2)
            painter.fillRect(marker, QColor(accent))
        painter.restore()

_SETTINGS_PAGE_META: tuple[tuple[str, str], ...] = (
    ("App", "General"),
    ("Connections", "Connections"),
    ("LLM", "Model routing"),
    ("TTS / Voice", "Voice & audio"),
    ("Keybinds", "Shortcuts"),
    ("Prompts", "Prompts & context"),
    ("Advanced", "Advanced"),
    ("About", "About"),
)


# Instance attributes created by the page builders that run after the window is
# shown. Any read of one of these before its page exists forces the remaining
# pages to be built, so callers never see a half-constructed dialog. Kept
# explicit rather than "any missing attribute" so that the many
# ``getattr(self, "_optional", default)`` probes elsewhere stay cheap.
_DEFERRED_PAGE_ATTRS = frozenset({
    "_active_model_route", "_active_voice_feature", "_api_key_rows_container",
    "_api_key_rows_layout", "_caller_blocks", "_callers_container", "_callers_vlayout",
    "_cgpt_login_btn", "_cgpt_logout_btn", "_chatgpt_status_lbl", "_connections_empty_lbl",
    "_connections_expanded", "_connections_filter", "_connections_page",
    "_connections_search", "_connections_show_more_btn",
    "_custom_base_url_label", "_custom_endpoint_combo",
    "_elevenlabs_install_btn", "_elevenlabs_install_status_lbl", "_expanded_shortcut_detail",
    "_github_login_btn", "_github_logout_btn", "_github_status_lbl", "_global_shortcut_rows",
    "_kokoro_assets_btn", "_kokoro_assets_mode", "_kokoro_assets_update_revision",
    "_kokoro_install_btn", "_kokoro_install_status_lbl", "_live_voice_install_btn",
    "_live_voice_install_status_lbl", "_live_voice_key_note_lbl", "_live_voice_model_row",
    "_live_voice_voice_row", "_llm_test_status_lbl", "_memory_test_status_lbl",
    "_model_route_add_buttons", "_model_route_button_group", "_model_route_buttons",
    "_model_route_cards", "_shortcut_rows", "_shortcut_search", "_shortcut_sections",
    "_snip_block", "_stt_active_lbl", "_stt_download_btn", "_stt_install_status_lbl",
    "_system_prompt_tabs", "_tts_provider_details", "_tts_provider_groups", "_tts_test_row",
    "_tts_test_status_lbl", "_tts_timing_notice_lbl", "_tts_volume_value_lbl",
    "_vision_test_status_lbl", "_voice_block", "_voice_feature_button_group",
    "_voice_feature_buttons", "_voice_feature_cards", "_voice_playback_card",
})


class _DeferredPageRows(list):
    """Row list whose contents only exist once its page has been built.

    ``_api_key_rows`` and friends are created in ``__init__`` and filled by a page
    builder, so an early read sees an empty list rather than a missing attribute
    and cannot go through ``SettingsDialog.__getattr__``. Reading one finishes the
    outstanding build first, which keeps the deferral invisible to callers.
    """

    def __init__(self, dialog: SettingsDialog) -> None:
        """Initialize the row list bound to its dialog."""
        super().__init__()
        self._dialog = dialog

    def _materialize(self) -> None:
        """Finish building the outstanding pages, if any are still pending."""
        dialog = self._dialog
        if dialog is not None:
            dialog._build_deferred_pages()

    def __getitem__(self, index):
        """Return a row, building the pending pages that would create it."""
        self._materialize()
        return list.__getitem__(self, index)

    def __len__(self) -> int:
        """Report the real row count rather than a pre-build zero."""
        self._materialize()
        return list.__len__(self)

    def __iter__(self):
        """Iterate real rows rather than a pre-build empty list."""
        self._materialize()
        return list.__iter__(self)

    def __bool__(self) -> bool:
        """Truthiness must reflect the built page, not the pending one."""
        self._materialize()
        return list.__len__(self) > 0


class _DeferredPageRowGroups(dict):
    """Per-section row lists that materialize their page on first read."""

    def __init__(self, dialog: SettingsDialog, sections) -> None:
        """Initialize one deferred row list per section key."""
        super().__init__({key: _DeferredPageRows(dialog) for key in sections})
        self._dialog = dialog

    def __getitem__(self, key):
        """Return a section's rows, building its page first when pending."""
        dialog = self._dialog
        if dialog is not None:
            dialog._build_deferred_pages()
        return dict.__getitem__(self, key)


class _DeferredPageFields(dict):
    """Settings field map that materializes not-yet-built pages on demand.

    Only the General page exists when the window first appears. Reading a field
    that lives on a later page -- from a test, a save, or any code that has no
    reason to care about build order -- transparently finishes building the rest
    instead of raising, so deferral stays invisible to every existing caller.
    """

    def __init__(self, dialog: SettingsDialog) -> None:
        """Initialize the field map bound to its dialog."""
        super().__init__()
        self._dialog = dialog

    def _materialize(self) -> None:
        """Finish building the outstanding pages, if any are still pending."""
        dialog = self._dialog
        if dialog is not None:
            dialog._build_deferred_pages()

    def __missing__(self, key):
        """Build the outstanding pages, then retry the lookup once."""
        dialog = self._dialog
        if dialog is not None and dialog._build_deferred_pages():
            return dict.__getitem__(self, key)
        raise KeyError(key)

    def __contains__(self, key) -> bool:
        """Membership must reflect every page, not just the ones built so far."""
        if dict.__contains__(self, key):
            return True
        self._materialize()
        return dict.__contains__(self, key)

    def get(self, key, default=None):
        """Mirror __missing__: a key absent only because its page is pending."""
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        self._materialize()
        return dict.get(self, key, default)

    def __iter__(self):
        """Iterating the field map must never expose a half-built dialog."""
        self._materialize()
        return dict.__iter__(self)

    def __len__(self) -> int:
        """Report the full field count, building pending pages if needed."""
        self._materialize()
        return dict.__len__(self)

    def keys(self):
        """Return every field key, including those on not-yet-built pages."""
        self._materialize()
        return dict.keys(self)

    def values(self):
        """Return every field widget, including those on not-yet-built pages."""
        self._materialize()
        return dict.values(self)

    def items(self):
        """Return every field pair, including those on not-yet-built pages."""
        self._materialize()
        return dict.items(self)


def _disconnect_clicked_handlers(button: QPushButton) -> None:
    """Disconnect a button's clicked handlers without warning when none exist."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r'libpyside: Failed to disconnect .* from signal "clicked\(\)".',
            category=RuntimeWarning,
        )
        try:
            button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass


# Sentinel data value for the "Custom / enter manually…" model combo entry.
_CUSTOM_MODEL_SENTINEL = "__custom__"
_CUSTOM_MODEL_LABEL = "Custom / enter manually…"
_CONNECTION_PROVIDER_IDS: tuple[str, ...] = (
    "groq", "openai", "anthropic", "google", "deepseek", "openrouter",
    "mistral", "xai", "together", "cerebras", "zai", "nvidia",
    "sambanova", "github_models", "huggingface", "chutes", "vercel",
    "fireworks", "cohere", "ai21", "nebius", "custom", "copilot",
)
# Fixed width of the leading drag-handle/priority column beside each model row.
_MODEL_PRIORITY_COL_W = 72
_MODEL_ROUTE_ROW_MIME = "application/x-openwand-model-route-row"
_SECRET_MASK_PLACEHOLDER = "●" * 16


class _FolderListEditor(QWidget):
    """A path list backed by the operating system's native folder chooser."""

    valueChanged = Signal()
    _MAX_VISIBLE_ROWS = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._list = QListWidget()
        self._list.setObjectName("settingsModelFileFolderList")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._list.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        layout.addWidget(self._list)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        self._add_button = QPushButton(t("Browse..."))
        self._add_button.setObjectName("settingsModelFileFolderAdd")
        self._remove_button = QPushButton(t("Remove"))
        self._remove_button.setObjectName("settingsModelFileFolderRemove")
        self._remove_button.setEnabled(False)
        controls.addWidget(self._add_button)
        controls.addWidget(self._remove_button)
        controls.addStretch()
        layout.addLayout(controls)

        self._add_button.clicked.connect(self._choose_folder)
        self._remove_button.clicked.connect(self._remove_selected)
        self._list.itemSelectionChanged.connect(self._sync_remove_button)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._sync_list_height()

    def _sync_list_height(self) -> None:
        """Show one compact row, then grow with entries up to a useful cap."""
        import shiboken6

        if not shiboken6.isValid(self) or not shiboken6.isValid(self._list):
            return
        visible_rows = max(1, min(self._list.count(), self._MAX_VISIBLE_ROWS))
        row_height = self._list.fontMetrics().height() + 10
        if self._list.count():
            measured_height = self._list.sizeHintForRow(0)
            if measured_height > 0:
                row_height = max(row_height, measured_height)
        frame_height = self._list.frameWidth() * 2
        content_width = self._list.sizeHintForColumn(0) if self._list.count() else 0
        viewport_width = max(0, self._list.viewport().width())
        needs_horizontal_scroll = (
            self._list.horizontalScrollBar().maximum() > 0
            or content_width > viewport_width
        )
        scrollbar_height = (
            self._list.style().pixelMetric(
                QStyle.PixelMetric.PM_ScrollBarExtent,
                None,
                self._list,
            )
            if needs_horizontal_scroll
            else 0
        )
        self._list.setFixedHeight(
            (row_height * visible_rows) + frame_height + scrollbar_height
        )
        self.updateGeometry()

    def resizeEvent(self, event) -> None:
        """Recalculate overflow after the settings page assigns its final width."""
        super().resizeEvent(event)
        QTimer.singleShot(0, self._sync_list_height)

    @staticmethod
    def _normalized_path_key(path: str) -> str:
        return os.path.normcase(os.path.normpath(str(path or "").strip()))

    @staticmethod
    def _split_value(value: str) -> list[str]:
        paths: list[str] = []
        for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            for path in line.split(os.pathsep):
                path = path.strip()
                if path:
                    paths.append(path)
        return paths

    def paths(self) -> list[str]:
        return [self._list.item(index).text() for index in range(self._list.count())]

    def text(self) -> str:
        return "\n".join(self.paths())

    def setText(self, value: str) -> None:
        previous = self.text()
        self._list.clear()
        seen: set[str] = set()
        for path in self._split_value(value):
            key = self._normalized_path_key(path)
            if key in seen:
                continue
            seen.add(key)
            self._list.addItem(path)
        self._sync_remove_button()
        self._sync_list_height()
        if self.text() != previous:
            self.valueChanged.emit()

    def add_folder(self, path: str) -> bool:
        path = str(path or "").strip()
        if not path:
            return False
        key = self._normalized_path_key(path)
        if any(self._normalized_path_key(item) == key for item in self.paths()):
            return False
        self._list.addItem(path)
        self._list.setCurrentRow(self._list.count() - 1)
        self._sync_list_height()
        self.valueChanged.emit()
        return True

    def _choose_folder(self) -> None:
        selected = self._list.currentItem()
        start = selected.text() if selected is not None else ""
        if not start or not Path(start).is_dir():
            start = next((path for path in self.paths() if Path(path).is_dir()), str(Path.home()))
        folder = QFileDialog.getExistingDirectory(
            self,
            t("Folders the model may use"),
            start,
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder:
            self.add_folder(folder)

    def _remove_selected(self) -> None:
        rows = sorted({self._list.row(item) for item in self._list.selectedItems()}, reverse=True)
        if not rows and self._list.currentRow() >= 0:
            rows = [self._list.currentRow()]
        if not rows:
            return
        for row in rows:
            self._list.takeItem(row)
        self._sync_remove_button()
        self._sync_list_height()
        self.valueChanged.emit()

    def _sync_remove_button(self) -> None:
        self._remove_button.setEnabled(bool(self._list.selectedItems()))


class _SecretLineEdit(QLineEdit):
    """Password field that can show a stored secret as password dots."""

    def __init__(self) -> None:
        super().__init__()
        self._stored_secret_placeholder = False

    def setStoredSecretPlaceholder(self, stored: bool) -> None:
        self._stored_secret_placeholder = bool(stored)
        self.setProperty("storedSecret", self._stored_secret_placeholder)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        stored_placeholder = (
            self._stored_secret_placeholder
            and not self.text()
            and bool(self.placeholderText())
        )
        placeholder = ""
        if stored_placeholder:
            placeholder = self.placeholderText()
            super().setPlaceholderText("")
        try:
            super().paintEvent(event)
        finally:
            if stored_placeholder:
                super().setPlaceholderText(placeholder)
        if not stored_placeholder:
            return

        painter = QPainter(self)
        font = QFont(self.font())
        size = font.pointSizeF()
        if size > 0:
            font.setPointSizeF(size + 4)
        painter.setFont(font)
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        rect = self.rect().adjusted(12, 0, -10, 0)
        mask = painter.fontMetrics().elidedText(
            _SECRET_MASK_PLACEHOLDER,
            Qt.TextElideMode.ElideRight,
            max(0, rect.width()),
        )
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            mask,
        )


class _ModelRouteDragHandle(QLabel):
    """Small handle that starts a model-route row drag."""

    def __init__(self, row_widget: QWidget) -> None:
        super().__init__("⠿")
        self.row_widget = row_widget
        self._press_pos = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFixedWidth(24)
        self.setToolTip(t("Drag to change priority"))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not (event.buttons() & Qt.MouseButton.LeftButton) or self._press_pos is None:
            super().mouseMoveEvent(event)
            return
        distance = (event.position().toPoint() - self._press_pos).manhattanLength()
        if distance < QApplication.startDragDistance():
            return
        mime = QMimeData()
        mime.setData(_MODEL_ROUTE_ROW_MIME, b"row")
        drag = QDrag(self)
        drag.setMimeData(mime)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        drag.exec(Qt.DropAction.MoveAction)
        self._press_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._press_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)


class _ModelRouteRowsWidget(QWidget):
    """Drop target for reordering model-route rows."""

    rowDropped = Signal(object, int)

    def __init__(self) -> None:
        super().__init__()
        self._drop_index: int | None = None
        self.setAcceptDrops(True)

    def _is_model_row_drag(self, event) -> bool:
        return event.mimeData().hasFormat(_MODEL_ROUTE_ROW_MIME)

    def _insertion_index(self, y: int) -> int:
        layout = self.layout()
        if layout is None:
            return 0
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is not None and y < widget.geometry().center().y():
                return index
        return layout.count()

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._is_model_row_drag(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._is_model_row_drag(event):
            return
        self._drop_index = self._insertion_index(event.position().toPoint().y())
        self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._drop_index = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        source = event.source()
        row_widget = getattr(source, "row_widget", None)
        drop_index = self._insertion_index(event.position().toPoint().y())
        self._drop_index = None
        self.update()
        if self._is_model_row_drag(event) and isinstance(row_widget, QWidget):
            self.rowDropped.emit(row_widget, drop_index)
            event.acceptProposedAction()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        if self._drop_index is None:
            return
        layout = self.layout()
        if layout is None or layout.count() == 0:
            y = 1
        elif self._drop_index <= 0:
            y = layout.itemAt(0).widget().geometry().top()
        elif self._drop_index >= layout.count():
            y = layout.itemAt(layout.count() - 1).widget().geometry().bottom()
        else:
            y = layout.itemAt(self._drop_index).widget().geometry().top()
        painter = QPainter(self)
        painter.fillRect(
            0,
            max(0, y - 1),
            self.width(),
            3,
            self.palette().color(QPalette.ColorRole.Highlight),
        )

_ASSISTANT_LANGUAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("System default", ""),
    ("Match user language", "match_user"),
    ("English", "English"),
    ("Chinese", "Chinese"),
    ("Chinese (Traditional)", "Chinese (Traditional)"),
    ("Spanish", "Spanish"),
    ("French", "French"),
    ("German", "German"),
    ("Japanese", "Japanese"),
    ("Korean", "Korean"),
    ("Portuguese", "Portuguese"),
    ("Hindi", "Hindi"),
)

_STT_LANGUAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Auto-detect", ""),
    ("English", "en"),
    ("Chinese (Mandarin)", "zh"),
    ("Cantonese", "yue"),
    ("Spanish", "es"),
    ("French", "fr"),
    ("German", "de"),
    ("Japanese", "ja"),
    ("Korean", "ko"),
    ("Portuguese", "pt"),
    ("Hindi", "hi"),
    ("Italian", "it"),
    ("Russian", "ru"),
    ("Arabic", "ar"),
)

_STT_MODEL_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("tiny", "tiny", "Whisper model option: tiny"),
    ("base", "base", "Whisper model option: base"),
    ("small", "small", "Whisper model option: small"),
    ("medium", "medium", "Whisper model option: medium"),
    ("large-v3", "large-v3", "Whisper model option: large-v3"),
)

_STT_COMPUTE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("int8", "int8"),
    ("int8_float16", "int8_float16"),
    ("float16", "float16"),
    ("float32", "float32"),
)

_STT_BEAM_OPTIONS: tuple[tuple[str, str], ...] = (
    ("1 (fastest)", "1"),
    ("3", "3"),
    ("5 (recommended)", "5"),
    ("8 (most accurate)", "8"),
)

_STT_DEVICE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Auto (GPU if available)", "auto"),
    ("CPU", "cpu"),
    ("GPU (CUDA)", "cuda"),
)


def _local_speech_device_options() -> tuple[tuple[str, str], ...]:
    """Return device choices that are valid for local speech installs."""
    if sys.platform == "darwin":
        return (
            ("Auto (CPU)", "auto"),
            ("CPU", "cpu"),
        )
    return _STT_DEVICE_OPTIONS


_DICTATE_MODE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Paste raw transcript", "raw"),
    ("Light LLM cleanup", "llm"),
)

_BUILTIN_CALLER_LABELS = frozenset({"General", "Rewrite & Paste"})

_LIVE_VOICE_MODEL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("gemini-3.1-flash-live-preview", "gemini-3.1-flash-live-preview"),
    ("gemini-2.5-flash-native-audio-preview-12-2025", "gemini-2.5-flash-native-audio-preview-12-2025"),
)

_LIVE_VOICE_PROVIDER_OPTIONS: tuple[str, ...] = ("google",)

_LIVE_VOICE_PROVIDER_MODELS: dict[str, list[str]] = {
    provider: [value for _label, value in _LIVE_VOICE_MODEL_OPTIONS]
    for provider in _LIVE_VOICE_PROVIDER_OPTIONS
}

_LIVE_VOICE_VOICE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Model default", ""),
    ("Puck", "Puck"),
    ("Kore", "Kore"),
    ("Charon", "Charon"),
    ("Aoede", "Aoede"),
)

_CHAT_REASONING_EFFORT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Provider default", ""),
    ("Minimal", "minimal"),
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
)

_SETTINGS_PRESET_KEY = "OPENWAND_SETTINGS_PRESET"
_PRESET_ENV_PREFIX = "OPENWAND_PRESET_"

_PRESET_LABELS: dict[str, str] = {
    "low_setup": "Low setup",
}

_PRESET_SLUGS: dict[str, str] = {
    label.lower(): slug for slug, label in _PRESET_LABELS.items()
}

_PRESET_DESCRIPTIONS: dict[str, str] = {
    "low_setup": "Use ChatGPT OAuth for every model route, with minimal context and no API keys.",
}

_PRESET_DEFAULTS: dict[str, dict[str, str]] = {
    "low_setup": {
        "LLM_PROVIDER": "chatgpt",
        "LLM_MODEL": "gpt-5.5",
        "LLM_FALLBACKS": "",
        "VISION_LLM_PROVIDER": "chatgpt",
        "VISION_LLM_MODEL": "gpt-5.5",
        "VISION_LLM_FALLBACKS": "",
        "MEMORY_LLM_PROVIDER": "chatgpt",
        "MEMORY_LLM_MODEL": "gpt-5.5",
        "MEMORY_LLM_FALLBACKS": "",
        "STT_MODEL": "base",
        "STT_BEAM_SIZE": "1",
        "MEMORY_TOP_K": "2",
        "CONTEXT_BROWSER_MAX_CHARS": "3000",
        "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": "4000",
        "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": "4000",
    },
}

_PRESET_CONTEXT_DEFAULTS: dict[str, dict[str, str]] = {
    "low_setup": {
        "documents": "off", "browser": "off", "github": "off",
        "memory": "on", "screenshot": "off", "clear_tools": "true",
    },
}


class _ModelFetchSignals(QObject):
    """Marshals a background model-list fetch result back to the Qt main thread.

    done(models: list, error: str) — error is "" on success.
    """
    done = Signal(object, str)


class _SttPreloadSignals(QObject):
    """Marshals a background STT model preload result back to the Qt main thread.

    done(backend_info: object | None, error: str) — error is "" on success.
    """
    done = Signal(object, str)


class _UpdateSignals(QObject):
    """Marshals update check/download results back to the Qt main thread."""
    done = Signal(object, str)


class _TtsInstallStatusSignals(QObject):
    """Marshals optional TTS package status results back to the Qt main thread."""
    done = Signal(int, object)


def _read_env() -> dict[str, str]:
    """Read env."""
    old_path = settings_env.ENV_PATH
    settings_env.ENV_PATH = ENV_PATH
    try:
        return settings_env.read_settings_env()
    finally:
        settings_env.ENV_PATH = old_path


def _format_env_value(value: str) -> str:
    """Format env value."""
    return settings_env.format_settings_env_value(value)


def _write_env(vals: dict[str, str], remove_keys: set[str] | None = None):
    """Write env."""
    old_path = settings_env.ENV_PATH
    settings_env.ENV_PATH = ENV_PATH
    try:
        settings_env.write_settings_env(vals, remove_keys=remove_keys)
    finally:
        settings_env.ENV_PATH = old_path


def _normalize_extra_tool_payloads(raw_tools) -> list[dict[str, str]]:
    """Normalize live addon/tool payloads for the settings tool picker."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_tools or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or name)
        else:
            name = str(item or "").strip()
            description = name
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "description": description})
    return out


class SettingsDialog(QDialog):
    """Qt dialog for settings dialog."""
    def __init__(self, parent=None, on_apply=None, on_setup_check=None, extra_tools=None):
        """Initialize the settings dialog instance."""
        super().__init__(parent)
        self._on_apply = on_apply  # callable() fired after a successful apply
        self._on_setup_check = on_setup_check
        self._extra_tools = _normalize_extra_tool_payloads(extra_tools)
        self._disposing = False
        self.setWindowTitle("Settings")
        self.setMinimumSize(980, 720)
        self.setModal(False)
        enable_standard_window_controls(self)
        self.setFont(_settings_font(14))
        self._env = _read_env()
        self._fields: dict[
            str,
            QLineEdit | QComboBox | QCheckBox | QTextEdit | QSlider | _FolderListEditor,
        ] = (
            _DeferredPageFields(self)
        )
        # Pages after the first are built once the window is on screen. Until then
        # _pending_page_builds holds the outstanding steps and _values_loaded says
        # whether the full _load_values pass has run.
        self._pending_page_builds: list = []
        self._page_hosts: list[QWidget] = []
        self._building_deferred_pages = False
        self._deferred_build_scheduled = False
        self._values_loaded = False
        # Theme color templates: {"light": {bg,surface,text,accent}, "dark": {...}}.
        # The four App-tab swatches edit whichever mode is selected in the Theme
        # combo; switching modes swaps these without losing the other mode's edits.
        self._theme_templates: dict[str, dict[str, str]] = {}
        self._theme_shown_mode: str = ""
        self._theme_syncing: bool = False
        self._api_key_rows: list[dict] = _DeferredPageRows(self)
        self._removed_connection_providers: set[str] = set()
        self._removed_custom_connection_ids: set[str] = set()
        self._model_section_rows: dict[str, list[dict]] = _DeferredPageRowGroups(
            self, ("LLM", "VISION_LLM", "MEMORY_LLM")
        )
        self._model_section_layouts: dict[str, QVBoxLayout] = {}
        self._model_route_rows_containers: dict[str, _ModelRouteRowsWidget] = {}
        self._warning_headers: dict[str, QLabel] = {}
        self._warning_header_base_texts: dict[str, str] = {}
        self._chat_elaborate_prompt_label: QLabel | None = None
        self._fallback_rows: dict = {}
        self._loading_values = False
        self._saving_settings = False
        self._dirty_refresh_scheduled = False
        self._search_index_refresh_scheduled = False
        self._dirty_baseline: dict[str, str] = {}
        self._dirty_keys: set[str] = set()
        self._tab_base_names: list[str] = []
        self._tab_dirty_names: set[str] = set()
        self._tab_search_text: dict[str, str] = {}
        self._settings_search_visibility: dict[QWidget, bool] = {}
        self._active_preset_slug = self._env.get(_SETTINGS_PRESET_KEY, "")
        self._pending_active_profile = ""
        self._pending_test_results: list[tuple[str, int, bool, str]] = []
        self._pending_test_results_lock = threading.Lock()
        self._pending_test_progress: list[tuple[str, int, str]] = []
        self._pending_test_progress_lock = threading.Lock()
        self._pending_status_results: list[tuple[int, str, object, str]] = []
        self._pending_status_results_lock = threading.Lock()
        self._pending_status_attrs: set[str] = set()
        self._running_test_tokens: set[tuple[str, int]] = set()
        self._latest_test_token: dict[str, int] = {}
        self._last_save_warnings: list[str] = []
        self._open_warning_boxes: list[QMessageBox] = []
        self._status_refresh_token = 0
        self._status_refresh_running = False
        self._harness_auth_poll_target: bool | None = None
        self._harness_auth_poll_ticks = 0
        self._harness_auth_poll_provider = ""
        self._update_check_result = None
        self._update_download_path = None
        self._update_mode = "check"
        self._update_running = False
        self._update_signal_carriers: list[_UpdateSignals] = []
        self._app_tab_index = -1
        self._tts_tab_index = -1
        self._tts_install_status_checked = False
        self._tts_install_status_running = False
        self._tts_install_status_token = 0
        self._tts_install_status_result: dict[str, object] | None = None
        self._tts_install_status_signal_carriers: list[_TtsInstallStatusSignals] = []
        self._tabs = None
        self._settings_nav: QListWidget | None = None
        self._page_title_lbl: QLabel | None = None
        self._test_result_timer = QTimer(self)
        self._test_result_timer.setInterval(100)
        self._test_result_timer.timeout.connect(self._drain_test_results)
        self._status_result_timer = QTimer(self)
        self._status_result_timer.setInterval(100)
        self._status_result_timer.timeout.connect(self._drain_status_results)
        self._warning_header_uppercase_keys: set[str] = set()
        self.finished.connect(self._dispose_after_finished)
        self._build_ui()
        # Only the first page is ready at this point. Populate it so the window can
        # be shown immediately with correct values; the remaining pages and the full
        # _load_values pass run from showEvent, once the user can see something.
        self._active_preset_slug = self._env.get(_SETTINGS_PRESET_KEY, "")
        self._loading_values = True
        try:
            self._load_general_page_values()
        finally:
            self._loading_values = False
        localize_widget_tree(self)
        self._refresh_tab_labels()
        fit_window_to_screen(self, preferred_width=980, preferred_height=720)

    def _page_widget(self, index: int) -> QWidget | None:
        """Return the real page at ``index``.

        Each tab holds a permanent host widget so that deferring page
        construction cannot shift tab indices; the page itself is its only child.
        """
        self._build_deferred_pages()
        hosts = self.__dict__.get("_page_hosts") or []
        if not 0 <= index < len(hosts):
            return None
        layout = hosts[index].layout()
        item = layout.itemAt(0) if layout is not None else None
        return item.widget() if item is not None else None

    def _set_page_widget(self, internal_name: str, page: QWidget) -> None:
        """Drop a freshly built page into its permanent host slot."""
        try:
            index = [meta[0] for meta in _SETTINGS_PAGE_META].index(internal_name)
        except ValueError:
            return
        host = self._page_hosts[index]
        layout = host.layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        layout.addWidget(page)
        self._polish_page_layout(page)

    def _polish_page_layout(self, page: QWidget) -> None:
        """Apply 9b spacing and section hierarchy to an existing settings page."""
        if isinstance(page, QScrollArea):
            scroll_body = page.widget()
            if scroll_body is not None and scroll_body.layout() is not None:
                scroll_body.layout().setContentsMargins(0, 0, 0, 12)
        forms = page.findChildren(QFormLayout)
        for form in forms:
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(6)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            for row in range(form.rowCount()):
                label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                label = label_item.widget() if label_item is not None else None
                if label is None or field_item is None:
                    continue
                label.setFixedWidth(170)
                label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        headers = [
            label
            for label in page.findChildren(QLabel)
            if label.objectName() in {"sectionHeader", "areaHeader"}
        ]
        for index, header in enumerate(headers):
            header.setProperty("firstSection", index == 0)
            header.style().unpolish(header)
            header.style().polish(header)

    def __getattr__(self, name: str):
        """Materialize deferred pages when one of their widgets is read early.

        Only consulted when normal attribute lookup fails, and only for the known
        page-owned names, so this cannot turn an ordinary typo or an optional
        ``getattr(self, ..., default)`` probe into a page build.
        """
        if name in _DEFERRED_PAGE_ATTRS and self.__dict__.get("_pending_page_builds"):
            if self._build_deferred_pages():
                try:
                    return self.__dict__[name]
                except KeyError:
                    pass
        raise AttributeError(name)

    def _build_deferred_pages(self) -> bool:
        """Build the remaining pages and load their values. True if work was done.

        Deliberately one atomic step rather than one page per event loop turn: a
        page that existed but had not been loaded yet would be interactive, and the
        later ``_load_values`` pass would silently revert whatever the user typed
        into it. Callers reach this either from ``showEvent`` -- just after the
        first page is on screen -- or from the first read of a deferred field.
        """
        if self.__dict__.get("_building_deferred_pages"):
            return False
        if getattr(self, "_disposing", False):
            # Closed before the build ran; loading values into a dying dialog would
            # only risk touching widgets Qt is already tearing down.
            return False
        pending = self.__dict__.get("_pending_page_builds") or []
        if not pending:
            return False
        debug_enabled = bool(os.environ.get("OPENWAND_UI_DEBUG_METHODS"))
        started_at = time.monotonic()
        phase_at = started_at

        def debug(message: str) -> None:
            """Expose cold-build boundaries to real-worker acceptance logs."""
            nonlocal phase_at
            if not debug_enabled:
                return
            now = time.monotonic()
            print(
                f"[settings debug] deferred build: {message} "
                f"(+{now - phase_at:.1f}s, total {now - started_at:.1f}s)",
                file=sys.stderr,
                flush=True,
            )
            phase_at = now

        page_labels = (
            "LLM and Connections",
            "TTS / Voice",
            "Keybinds",
            "Prompts",
            "Advanced",
            "About",
        )
        self._building_deferred_pages = True
        try:
            debug("starting")
            while pending:
                page_index = len(page_labels) - len(pending)
                page_label = page_labels[page_index] if 0 <= page_index < len(page_labels) else "unknown page"
                debug(f"building {page_label}")
                pending.pop(0)()
                debug(f"built {page_label}")

            self._values_loaded = True
            debug("loading values")
            legacy_preset = self._preset_slug(self._active_preset_slug)
            # The General page was loaded during __init__ and may already carry
            # edits, so this pass covers only the pages built just now.
            self._load_values(include_general=False)
            debug("loaded values")
            if (
                legacy_preset
                and not settings_profiles.profile_path(ENV_PATH, legacy_preset).is_file()
            ):
                # Stage the one-time conversion from the old preset overlay to a
                # normal profile. Nothing touches disk until Save changes.
                self._apply_preset(legacy_preset)
        finally:
            self._building_deferred_pages = False
        debug("localizing widgets")
        localize_widget_tree(self)
        debug("localized widgets")
        self._refresh_tab_labels()
        self._refresh_search_index()
        self._apply_dialog_theme()
        # Targets sign-in labels on the Connections page, so it has to wait until
        # that page exists.
        self._schedule_open_status_refresh()
        debug("complete")
        return True

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        """Keep Escape from dismissing the entire non-modal Settings window."""
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)

    def _active_child_workflow(self) -> str:
        """Return the child workflow that makes discarding Settings unsafe."""
        for installer in list(getattr(self, "_optional_install_dialogs", []) or []):
            try:
                if getattr(installer, "exit_code", None) is None:
                    return t("an installer")
            except RuntimeError:
                continue
        wizard = getattr(self, "_profile_setup_wizard", None)
        if wizard is not None:
            try:
                if wizard.isVisible():
                    return t("the profile setup wizard")
            except RuntimeError:
                pass
        return ""

    def reject(self) -> None:
        """Discard pending edits unless a child workflow is still active."""
        child = self._active_child_workflow()
        if child:
            QMessageBox.information(
                self,
                t("Finish the open task first"),
                t("Settings cannot close while {child} is active.").format(child=child),
            )
            return
        # Unsaved controls are intentionally disposable. Do not reconstruct
        # widgets from the baseline here: that can fail on a stale dynamic row,
        # and no runtime/persistent state is changed until Save changes runs.
        super().reject()

    def _on_settings_tab_changed(self, index: int) -> None:
        """Run page-specific deferred checks after the user opens that page."""
        if index != self._app_tab_index:
            # The user navigated off the first page before the background build
            # reached it; finish now so they never land on an empty page.
            self._build_deferred_pages()
        nav = getattr(self, "_settings_nav", None)
        if isinstance(nav, QListWidget) and nav.currentRow() != index:
            nav.blockSignals(True)
            nav.setCurrentRow(index)
            nav.blockSignals(False)
        if 0 <= index < len(_SETTINGS_PAGE_META):
            _internal, title = _SETTINGS_PAGE_META[index]
            if isinstance(self._page_title_lbl, QLabel):
                self._page_title_lbl.setText(t(title))
        self._refresh_current_install_status(force_tts=False)

    def select_page(self, internal_name: str) -> bool:
        """Select a settings page by its stable internal name."""
        try:
            index = [meta[0] for meta in _SETTINGS_PAGE_META].index(str(internal_name))
        except ValueError:
            return False
        if not isinstance(self._tabs, QTabWidget):
            return False
        self._tabs.setCurrentIndex(index)
        return True

    def _refresh_current_install_status(self, *, force_tts: bool) -> None:
        """Refresh install state for the visible Settings page."""
        if getattr(self, "_disposing", False):
            return
        tabs = getattr(self, "_tabs", None)
        if not isinstance(tabs, QTabWidget):
            return
        index = tabs.currentIndex()
        if index == getattr(self, "_app_tab_index", -1):
            if hasattr(self, "_privacy_model_status_lbl"):
                self._refresh_privacy_model_status()
            return
        if index != getattr(self, "_tts_tab_index", -1):
            return

        self._refresh_stt_active_backend()
        if force_tts and not self._tts_install_status_running:
            self._tts_install_status_checked = False
            self._tts_install_status_result = None
        self._refresh_tts_optional_install_status()

    def _save_api_keys_to_keychain(self) -> bool:
        """Persist every typed API key to the OS keychain, one at a time.

        Each key is written and verified independently: a failure on one key is
        logged and collected for the user, but never blocks the other keys or the
        rest of the settings save. Successfully stored keys have their input field
        cleared and switched to a "stored" placeholder. Returns True only when all
        typed keys were saved.
        """
        failures: list[str] = []

        def _store(key_name: str, value: str, label: str) -> bool:
            """Handle store for settings dialog."""
            try:
                secret_store.set_secret(key_name, value)
                _settings_log.info("Saved %s (%s) to OS keychain", key_name, label)
                return True
            except Exception as exc:  # noqa: BLE001 — reported to the user + log
                _settings_log.error("Could not save %s (%s) to OS keychain: %s", key_name, label, exc)
                failures.append(f"{label}: {exc}")
                return False

        try:
            secret_store.migrate_env_secrets(self._env)
        except Exception as exc:  # noqa: BLE001 — non-fatal: just means no .env keys to migrate
            _settings_log.warning("Secret migration from .env skipped: %s", exc)

        # Removing a connection is staged like every other Settings edit.  Only
        # clear its shared credential on Save, and only when no row for that
        # provider remains.  This keeps Cancel non-destructive and handles a
        # provider being removed then re-added before saving.
        for provider in list(self._removed_connection_providers):
            if any(_get(row["provider"]).strip() == provider for row in self._api_key_rows):
                self._removed_connection_providers.discard(provider)
                continue
            try:
                if provider == "copilot":
                    from core.auth import copilot_auth

                    copilot_auth.clear_token()
                else:
                    key_name = _PROVIDER_KEY_NAMES.get(provider)
                    if key_name:
                        secret_store.delete_secret(key_name)
                self._removed_connection_providers.discard(provider)
            except Exception as exc:  # noqa: BLE001 - reported with storage failures
                label = _PROVIDER_LABELS.get(provider, provider)
                _settings_log.error("Could not clear %s connection credential: %s", label, exc)
                failures.append(f"{label}: {exc}")

        for connection_id in list(self._removed_custom_connection_ids):
            if any(
                _get(row["provider"]).strip() == "custom"
                and str(row.get("connection_id") or "legacy") == connection_id
                for row in self._api_key_rows
            ):
                self._removed_custom_connection_ids.discard(connection_id)
                continue
            try:
                secret_store.delete_secret(_custom_secret_name(connection_id))
                self._removed_custom_connection_ids.discard(connection_id)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"Custom ({connection_id}): {exc}")

        # LLM provider keys from the API key table
        for row in self._api_key_rows:
            provider = _get(row["provider"]).strip()
            value = row["key"].text().strip()
            if not value:
                continue
            label = _PROVIDER_LABELS.get(provider, provider)
            if provider == "copilot":
                try:
                    from core.auth import copilot_auth
                    copilot_auth.save_token(value)
                    _settings_log.info("Saved GitHub Copilot token to OS keychain")
                    row["key"].clear()
                    self._set_secret_placeholder(row["key"], "stored in keychain", stored=True)
                    continue
                except Exception as exc:  # noqa: BLE001 - reported with other keychain failures
                    _settings_log.error("Could not save GitHub Copilot token to OS keychain: %s", exc)
                    failures.append(f"{label}: {exc}")
                    continue
            key_name = (
                _custom_secret_name(str(row.get("connection_id") or "legacy"))
                if provider == "custom"
                else _PROVIDER_KEY_NAMES.get(provider)
            )
            if not key_name:
                continue
            if _store(key_name, value, label):
                row["key"].clear()
                self._set_secret_placeholder(row["key"], "stored in keychain", stored=True)

        # TTS and custom keys still live in self._fields
        for name, label in [
            ("CARTESIA_API_KEY",   "Cartesia"),
            ("ELEVENLABS_API_KEY", "ElevenLabs"),
            ("TTS_CUSTOM_API_KEY", "Custom TTS endpoint"),
            ("CLOUDFLARE_API_TOKEN", "Cloudflare Workers AI"),
            ("CUSTOM_API_KEY",     "Custom provider"),
        ]:
            if name not in self._fields:
                continue
            value = _get(self._fields[name]).strip()
            if not value:
                continue
            if _store(name, value, label):
                self._fields[name].clear()  # type: ignore[attr-defined]
                self._set_secret_placeholder(
                    self._fields[name],  # type: ignore[arg-type]
                    t("{label} key stored in OS keychain").format(label=label),
                    stored=True,
                )

        if failures:
            QMessageBox.warning(
                self,
                t("Some API keys were not saved"),
                t("These keys could not be written to the OS keychain and were NOT stored:")
                + "\n\n"
                + "\n".join(f"• {item}" for item in failures)
                + "\n\n"
                + t("Your other settings were still saved. See the log for details, "
                    "then try saving the affected keys again."),
            )
            return False
        return True

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def changeEvent(self, event):               # noqa: N802
        """Cancel any active hotkey recording when the window is deactivated."""
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.WindowDeactivate:
            for w in self.findChildren(HotkeyCaptureEdit):
                if w._recording:
                    w._cancel()
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowActivate:
            QTimer.singleShot(0, lambda: self._refresh_current_install_status(force_tts=True))

    def showEvent(self, event):                 # noqa: N802
        """Show event."""
        super().showEvent(event)
        fit_window_to_screen(self, preferred_width=980, preferred_height=720)
        self._refresh_current_install_status(force_tts=True)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        """Build the remaining pages once the first page has actually been drawn.

        Deliberately keyed off the first paint rather than showEvent: a zero-delay
        timer started from showEvent still runs ahead of the initial frame, so the
        user would keep staring at nothing for the whole build.
        """
        super().paintEvent(event)
        if self._pending_page_builds and not self._deferred_build_scheduled:
            self._deferred_build_scheduled = True
            QTimer.singleShot(0, self._build_deferred_pages_from_timer)

    def _build_deferred_pages_from_timer(self) -> None:
        """Run the background build without letting errors escape into Qt.

        This runs from a timer rather than a call stack we control, so an
        exception here would surface inside an unrelated event handler.
        """
        try:
            self._build_deferred_pages()
        except Exception:  # noqa: BLE001 - logged; the dialog stays usable
            _settings_log.exception("Could not finish building the Settings pages")

    def hideEvent(self, event):                 # noqa: N802
        """Hide event."""
        self._cancel_async_ui_updates()
        super().hideEvent(event)

    def closeEvent(self, event):                # noqa: N802
        """Close event."""
        self._cancel_async_ui_updates()
        super().closeEvent(event)

    def _dispose_after_finished(self, _result: int) -> None:
        """Handle dispose after finished for settings dialog."""
        self._disposing = True
        self._cancel_async_ui_updates()
        _clear_settings_dialog(self)
        self.deleteLater()

    @staticmethod
    def _dialog_style(dark: bool) -> str:
        """Build the dialog stylesheet from the active mode's template colours."""
        from ui.shared.theme import theme_colors
        c = theme_colors(dark)
        ui_family = _settings_ui_family().replace('"', "")
        if dark:
            bg = "#16181b"
            well = "#0e1013"
            hover = "#141619"
            raised = "#22262b"
            rule = "#262a2f"
            text = "#e9e6e0"
            label = "#b8b4ac"
            dim = "#8b8a86"
            disabled = "#5f574f"
            accent = "#d8a145"
            on_accent = "#16181b"
        else:
            bg = c["bg"]
            well = c["well"]
            hover = c["button_hover"]
            raised = c["raised"]
            rule = c["rule"]
            text = c["text"]
            label = c["label"]
            dim = c["text_dim"]
            disabled = c["disabled"]
            accent = c["accent"]
            on_accent = c["on_accent"]
        selected_bg = well if dark else c["accent_fill"]
        selected_text = accent if dark else text
        primary_fill = accent if dark else c["accent_fill"]
        primary_hover = c["accent_hover"] if dark else c["accent_fill_hover"]
        return f"""
        QDialog, QWidget#openwandWindowContent {{
            background: {bg};
        }}
        QWidget {{
            color: {text};
            font-family: "{ui_family}";
            font-size: 14px;
        }}
        QLabel#oauthProviderName,
        QLabel#oauthLoginStatus,
        QLabel#oauthFeaturePurpose,
        QLabel#oauthInlineMessage,
        QPushButton#chatgptOAuthSignIn,
        QPushButton#chatgptOAuthSignOut,
        QPushButton#githubOAuthSignIn,
        QPushButton#githubOAuthSignOut {{
            font-family: "{ui_family}";
            letter-spacing: 0px;
        }}
        QTabWidget#settingsTabs {{
            background: {bg}; border: none;
        }}
        QTabWidget#settingsTabs::pane {{
            border: none; background: {bg};
        }}
        QTabWidget#settingsTabs::tab-bar {{
            background: {bg}; alignment: left;
        }}
        QTabWidget#settingsTabs > QWidget {{
            background: {bg};
        }}
        QTabBar#settingsTabBar {{
            background: {bg}; background-color: {bg}; border: none;
        }}
        QTabBar#settingsTabBar::tab {{
            color: {dim}; padding: 7px 20px; border: none;
            border-radius: 0px; font-size: 14px; background: transparent;
        }}
        QTabBar#settingsTabBar::tab:selected {{
            background: {selected_bg}; color: {selected_text}; font-weight: 600;
        }}
        QTabBar#settingsTabBar::tab:hover:!selected {{ background: {hover}; color: {text}; }}
        QTabBar#settingsTabBar::scroller {{
            background: {bg}; width: 0px;
        }}
        QTabBar#settingsTabBar QToolButton {{
            background: {bg}; border: none;
        }}
        QFrame#settingsSidebar {{
            background: transparent; border: none; border-radius: 0px;
        }}
        QListWidget#settingsNavigation {{
            background: transparent; border: none; outline: none; padding: 0px;
        }}
        QListWidget#settingsNavigation::item {{
            border: none; border-radius: 0px; padding: 0px; margin: 0px;
        }}
        QListWidget#settingsNavigation::item:selected {{
            background: {selected_bg}; color: {selected_text};
        }}
        QListWidget#settingsNavigation::item:hover:!selected {{
            background: {hover}; color: {text};
        }}
        QLabel#settingsWindowTitle {{
            color: {text}; font-size: 20px; font-weight: 600;
        }}
        QLabel#settingsPageTitle {{
            color: {text}; font-size: 26px; font-weight: 600;
        }}
        QLabel#settingsSearchStatus {{
            color: {dim}; font-size: 12px;
        }}
        QLabel#settingsProfileLabel {{
            color: {dim}; font-family: "Cascadia Mono", "Consolas", monospace;
            font-size: 9px; font-weight: 600; letter-spacing: 1.1px;
        }}
        QPushButton#settingsSegmentButton {{
            border: 1px solid {rule}; color: {dim};
            background: {well}; padding: 7px 14px;
        }}
        QPushButton#settingsSegmentButton:checked {{
            border-color: {rule}; color: {accent};
            background: {well}; font-weight: 600;
        }}
        QPushButton[primary="true"] {{
            color: {on_accent}; background: {primary_fill};
            border-color: {primary_fill}; font-weight: 600;
        }}
        QPushButton[primary="true"]:hover {{ background: {primary_hover}; }}
        QFrame#card {{
            background: transparent; border: none; border-radius: 0px;
        }}
        QWidget#sectionHeadingRow {{ background: transparent; }}
        QFrame#sectionRule {{
            background: {rule}; border: none; min-height: 1px; max-height: 1px;
        }}
        QLabel#sectionHeader {{
            color: {dim}; font-family: "Cascadia Mono", "Consolas", monospace;
            font-size: 9px; font-weight: 600; letter-spacing: 1.2px; padding: 0px;
        }}
        QLabel#sectionHeader[firstSection="true"] {{
            color: {accent};
        }}
        QLabel#areaHeader {{
            color: {dim}; font-family: "Cascadia Mono", "Consolas", monospace;
            font-size: 9px; font-weight: 600; letter-spacing: 1.2px; padding: 0px;
        }}
        QLabel#areaHeader[firstSection="true"] {{ color: {accent}; }}
        QFrame#areaAccentLine {{
            background: {rule}; border: none; border-radius: 0px;
            min-height: 1px; max-height: 1px;
        }}
        QScrollArea {{
            background: {bg}; border: none;
        }}
        QScrollArea > QWidget {{
            background: {bg};
        }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QLabel {{ color: {text}; }}
        QLabel[description="true"] {{ color: {dim}; font-size: 13px; }}
        QLineEdit, QComboBox, QTextEdit, QPlainTextEdit {{
            background: {well}; border: 1px solid {well}; border-radius: 0px;
            padding: 6px 10px; font-size: 14px; color: {text}; min-height: 18px;
        }}
        QLineEdit:hover, QComboBox:hover, QTextEdit:hover, QPlainTextEdit:hover {{
            background: {hover};
        }}
        QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
            border-color: {accent};
        }}
        QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
            color: {disabled}; background: {well};
        }}
        QLineEdit#settingsSearch {{ min-width: 250px; max-width: 250px; }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox::down-arrow {{ width: 8px; height: 5px; }}
        QComboBox QAbstractItemView {{
            background: {well}; color: {text}; border: 1px solid {rule};
            selection-background-color: {hover}; selection-color: {accent}; outline: 0;
        }}
        QPushButton {{
            border: 1px solid {well}; color: {accent}; border-radius: 0px;
            padding: 7px 16px; background: {well}; font-size: 13px; font-weight: 600;
        }}
        QPushButton#settingsProfilesButton {{
            color: {text}; padding: 6px 10px; text-align: left; font-weight: 400;
        }}
        QPushButton#settingsSetupCheckButton {{
            color: {accent}; padding: 7px 11px; text-align: left; font-weight: 600;
        }}
        QPushButton:hover {{ background: {hover}; border-color: {hover}; }}
        QPushButton:pressed {{ background: {raised}; border-color: {raised}; }}
        QPushButton:disabled {{ color: {disabled}; background: {well}; border-color: {well}; }}
        QPushButton:flat {{ border: none; color: {accent}; background: transparent; }}
        QPushButton:flat:hover {{ color: {c["accent_hover"]}; background: transparent; }}
        QCheckBox {{ color: {label}; spacing: 8px; }}
        QCheckBox::indicator {{
            width: 14px; height: 14px; border-radius: 0px;
            border: 1px solid {rule}; background: {well};
        }}
        QCheckBox::indicator:checked {{
            background: {primary_fill}; border-color: {primary_fill};
        }}
        QLineEdit[dirty="true"], QComboBox[dirty="true"], QTextEdit[dirty="true"] {{
            border-color: {accent};
        }}
        QFrame#settingsFooter {{
            background: {bg}; border: none; border-top: 1px solid {rule};
        }}
        QPushButton#settingsResetPageButton, QPushButton#settingsCancelButton {{
            color: {label}; padding: 7px 14px;
        }}
        QPushButton#settingsCancelButton {{ padding-left: 18px; padding-right: 18px; }}
        QPushButton#settingsResetAllButton {{
            color: #c4553d; padding: 7px 14px;
        }}
        QPushButton#settingsApplyButton {{
            color: {on_accent}; background: {primary_fill}; border-color: {primary_fill};
            padding: 8px 20px; font-weight: 600;
        }}
        QPushButton#settingsApplyButton:hover {{ background: {primary_hover}; }}
        QMenu {{
            background: {well}; color: {text}; border: 1px solid {rule}; padding: 4px 0px;
        }}
        QMenu::item {{ padding: 6px 18px; }}
        QMenu::item:selected {{ background: {hover}; color: {accent}; }}
        QSlider::groove:horizontal {{ height: 2px; background: {rule}; }}
        QSlider::handle:horizontal {{
            width: 12px; margin: -5px 0px; border-radius: 0px; background: {accent};
        }}
        QScrollBar:vertical, QScrollBar:horizontal {{
            background: {bg}; border: none;
        }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background: {c["scroll_handle"]}; border-radius: 0px;
            min-height: 24px; min-width: 24px;
        }}
        QScrollBar::add-page, QScrollBar::sub-page {{
            background: {bg};
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
    """

    def _apply_dialog_theme(self):
        """Apply dialog theme."""
        from ui.shared.theme import is_dark_mode
        dark = is_dark_mode()
        self.setFont(_settings_font(14))
        self.setStyleSheet(self._dialog_style(dark))
        nav = getattr(self, "_settings_nav", None)
        if isinstance(nav, QListWidget) and isinstance(nav.itemDelegate(), _SettingsNavigationDelegate):
            nav.itemDelegate().set_dark(dark)
            nav.viewport().update()

    def _build_ui(self):
        """Build ui."""
        self._apply_dialog_theme()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body_widget = QWidget()
        body = QHBoxLayout()
        body_widget.setLayout(body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("settingsSidebar")
        sidebar.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(22, 20, 16, 16)
        sidebar_layout.setSpacing(0)
        window_title = QLabel(t("Settings"))
        window_title.setObjectName("settingsWindowTitle")
        sidebar_layout.addWidget(window_title)
        sidebar_layout.addSpacing(16)
        nav = QListWidget()
        nav.setObjectName("settingsNavigation")
        nav.setFrameShape(QFrame.Shape.NoFrame)
        nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav.setSpacing(1)
        nav.setUniformItemSizes(True)
        nav.setMouseTracking(True)
        from ui.shared.theme import is_dark_mode
        nav.setItemDelegate(_SettingsNavigationDelegate(nav, dark=is_dark_mode()))
        self._settings_nav = nav
        sidebar_layout.addWidget(nav, 1)

        sidebar_layout.addSpacing(12)
        profile_label = QLabel(t("Profile"))
        profile_label.setObjectName("settingsProfileLabel")
        profile_font = _settings_font(8.5, mono=True)
        profile_font.setCapitalization(QFont.Capitalization.AllUppercase)
        profile_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112.0)
        profile_label.setFont(profile_font)
        sidebar_layout.addWidget(profile_label)
        sidebar_layout.addSpacing(6)
        profile_btn = QPushButton()
        self._profiles_btn = profile_btn
        profile_btn.setObjectName("settingsProfilesButton")
        profile_btn.setToolTip(t("Load or create a profile for common OpenWand setups. Review changes before saving."))
        profile_btn.setMenu(self._build_profiles_menu(profile_btn))
        self._refresh_profile_button_text()
        sidebar_layout.addWidget(profile_btn)
        sidebar_layout.addSpacing(7)
        setup_btn = QPushButton(t("Run setup check"))
        setup_btn.setObjectName("settingsSetupCheckButton")
        setup_btn.setToolTip(t("Check provider, speech, hotkey, and privacy readiness."))
        setup_btn.clicked.connect(self._run_setup_check)
        sidebar_layout.addWidget(setup_btn)
        body.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(26, 20, 24, 0)
        content_layout.setSpacing(0)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(18)
        header_copy = QVBoxLayout()
        header_copy.setContentsMargins(0, 0, 0, 0)
        header_copy.setSpacing(3)
        self._page_title_lbl = QLabel()
        self._page_title_lbl.setObjectName("settingsPageTitle")
        header_copy.addWidget(self._page_title_lbl)
        header_row.addLayout(header_copy, 1)
        self._settings_search = QLineEdit()
        self._settings_search.setObjectName("settingsSearch")
        self._settings_search.setPlaceholderText(t("Search all settings..."))
        self._settings_search.setClearButtonEnabled(True)
        self._settings_search.setFixedWidth(250)
        self._settings_search.setFixedHeight(32)
        self._settings_search.textChanged.connect(self._apply_settings_search)
        header_row.addWidget(self._settings_search, 0, Qt.AlignmentFlag.AlignTop)
        content_layout.addLayout(header_row)

        self._search_status_lbl = QLabel()
        self._search_status_lbl.setObjectName("settingsSearchStatus")
        self._search_status_lbl.hide()
        content_layout.addWidget(self._search_status_lbl)
        content_layout.addSpacing(10)

        tabs = QTabWidget()
        tabs.setObjectName("settingsTabs")
        tabs.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tabs.tabBar().setObjectName("settingsTabBar")
        tabs.tabBar().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tabs.tabBar().setDrawBase(False)
        tabs.tabBar().setExpanding(True)
        tabs.setElideMode(Qt.TextElideMode.ElideNone)

        # Every page gets a permanent host widget so tab indices never move. Only
        # the first page's contents are built now; the rest are filled in after the
        # window is visible, which is what keeps opening Settings from stalling.
        self._page_hosts = []
        for internal_name in [meta[0] for meta in _SETTINGS_PAGE_META]:
            host = QWidget()
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(0, 0, 0, 0)
            host_layout.setSpacing(0)
            self._page_hosts.append(host)
            index = tabs.addTab(host, internal_name)
            if internal_name == "App":
                self._app_tab_index = index
            if internal_name == "TTS / Voice":
                self._tts_tab_index = index

        self._set_page_widget("App", self._tab_app())

        def _build_llm_pages() -> None:
            """Build the LLM page, which also produces the Connections page."""
            llm_page = self._tab_llm()
            self._set_page_widget("Connections", self._connections_page)
            self._set_page_widget("LLM", llm_page)

        self._pending_page_builds = [
            _build_llm_pages,
            lambda: self._set_page_widget("TTS / Voice", self._tab_tts()),
            lambda: self._set_page_widget("Keybinds", self._tab_keybinds()),
            lambda: self._set_page_widget("Prompts", self._tab_prompt()),
            lambda: self._set_page_widget("Advanced", self._tab_advanced()),
            lambda: self._set_page_widget("About", self._tab_about()),
        ]
        tabs.currentChanged.connect(self._on_settings_tab_changed)
        self._tabs = tabs
        self._tab_base_names = [tabs.tabText(i) for i in range(tabs.count())]
        tabs.tabBar().hide()
        content_layout.addWidget(tabs, 1)
        body.addWidget(content, 1)
        root.addWidget(body_widget, 1)

        for _internal, title in _SETTINGS_PAGE_META:
            item = QListWidgetItem(t(title))
            item.setSizeHint(QSize(0, _SETTINGS_NAV_ITEM_HEIGHT))
            nav.addItem(item)
        nav.currentRowChanged.connect(tabs.setCurrentIndex)
        nav.setCurrentRow(0)
        self._on_settings_tab_changed(0)

        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet("color: #80c080; font-size: 9pt;")
        footer = QFrame()
        footer.setObjectName("settingsFooter")
        btn_row = QHBoxLayout(footer)
        btn_row.setContentsMargins(24, 12, 24, 12)
        btn_row.setSpacing(10)
        reset_page_btn = QPushButton(t("Reset Page…"))
        reset_page_btn.setObjectName("settingsResetPageButton")
        reset_page_btn.setToolTip(t("Reset only the currently selected settings page to defaults"))
        reset_page_btn.clicked.connect(self._reset_current_page)
        btn_row.addWidget(reset_page_btn)
        reset_btn = QPushButton(t("Reset All…"))
        reset_btn.setObjectName("settingsResetAllButton")
        reset_btn.setToolTip(t("Delete all API keys from the OS keychain and reset every setting to defaults"))
        reset_btn.clicked.connect(self._reset_all)
        btn_row.addWidget(reset_btn)
        btn_row.addWidget(self._status_lbl)
        btn_row.addStretch()
        cancel_btn = QPushButton(t("Cancel"))
        cancel_btn.setObjectName("settingsCancelButton")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(t("Save changes"))
        save_btn.setObjectName("settingsApplyButton")
        save_btn.setProperty("primary", True)
        save_btn.setDefault(True)
        save_btn.setEnabled(False)
        save_btn.clicked.connect(self._confirm)
        self._apply_btn = save_btn
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        root.addWidget(footer)

        # Reapply after every navigation item exists. On Windows, applying the
        # parent stylesheet only before constructing the QListWidget can leave
        # unselected items without their stylesheet padding until the next theme
        # change, making the sidebar rows appear compressed on first open.
        self._apply_dialog_theme()

    def _build_profiles_menu(self, parent: QWidget) -> QMenu:
        """Build profiles menu."""
        menu = QMenu(parent)

        def add_action(label: str) -> QAction:
            # Construct the action explicitly. PySide's addAction(text)
            # convenience overload can return a stale wrapper after a prior
            # Settings menu is destroyed and the dialog is opened again.
            action = QAction(label, menu)
            menu.addAction(action)
            return action

        for slug, name in _PRESET_LABELS.items():
            action = add_action(t(name))
            action.setToolTip(t(_PRESET_DESCRIPTIONS[slug]))
            action.triggered.connect(lambda _checked=False, preset=slug: self._apply_preset(preset))
        saved_profiles = self._saved_custom_profile_entries()
        if saved_profiles:
            menu.addSeparator()
            for index, profile_id, _label in saved_profiles:
                action = add_action(
                    self._custom_profile_display_label(profile_id, _label, saved_profiles)
                )
                action.setToolTip(t("Load this custom profile into Settings."))
                action.triggered.connect(
                    lambda _checked=False, slot=index: self._apply_saved_profile(slot)
                )
            menu.addSeparator()
            rename_action = add_action(t("Rename profile..."))
            rename_action.setToolTip(t("Change the display name for a saved custom profile."))
            rename_action.triggered.connect(self._rename_custom_profile)
            delete_action = add_action(t("Delete profile..."))
            delete_action.setToolTip(t("Delete a saved custom profile."))
            delete_action.triggered.connect(self._delete_custom_profile)
        menu.addSeparator()
        create_action = add_action(t("Create custom profile..."))
        create_action.setToolTip(t("Save the current model, context, and budget settings as a reusable profile."))
        create_action.triggered.connect(self._create_custom_profile)
        return menu

    def _refresh_profiles_menu(self) -> None:
        """Refresh the Profiles menu after custom profile changes."""
        btn = getattr(self, "_profiles_btn", None)
        if isinstance(btn, QPushButton):
            old_menu = btn.menu()
            btn.setMenu(self._build_profiles_menu(btn))
            self._refresh_profile_button_text()
            if old_menu is not None:
                old_menu.deleteLater()

    def _selected_profile_id(self) -> str:
        """Return the profile currently selected for Settings/runtime use."""
        pending = str(getattr(self, "_pending_active_profile", "") or "").strip()
        if pending:
            return pending
        legacy_preset = self._preset_slug(self._env.get(_SETTINGS_PRESET_KEY, ""))
        if legacy_preset:
            return legacy_preset
        return str(
            self._env.get("SETTINGS_PROFILE", "")
            or self._env.get("ACTIVE_PROFILE", "")
            or "default"
        ).strip()

    def _selected_custom_profile_entry(self) -> tuple[int, str, str] | None:
        """Return the selected custom profile slot, id, and display label."""
        selected_id = self._selected_profile_id()
        for entry in self._saved_custom_profile_entries():
            if entry[1] == selected_id:
                return entry
        return None

    @staticmethod
    def _custom_profile_display_label(
        profile_id: str,
        label: str,
        entries: list[tuple[int, str, str]],
    ) -> str:
        """Disambiguate profiles only when their user-visible names collide."""
        duplicate = sum(1 for _index, _id, other_label in entries if other_label == label) > 1
        return f"{label} ({profile_id})" if duplicate else label

    def _refresh_profile_button_text(self) -> None:
        """Show the selected profile name on the closed profile selector."""
        btn = getattr(self, "_profiles_btn", None)
        if not isinstance(btn, QPushButton):
            return
        profile_id = self._selected_profile_id()
        preset_slug = self._preset_slug(getattr(self, "_active_preset_slug", ""))
        selected = self._selected_custom_profile_entry()
        if profile_id in _PRESET_LABELS:
            label = t(_PRESET_LABELS[profile_id])
        elif preset_slug and profile_id == "default":
            label = t(_PRESET_LABELS[preset_slug])
        elif selected is not None:
            label = self._custom_profile_display_label(
                selected[1], selected[2], self._saved_custom_profile_entries()
            )
        else:
            label = (
                t("Default")
                if profile_id == "default"
                else profile_id.replace("-", " ").title()
            )
        btn.setText(label)

    def _saved_custom_profile_entries(self) -> list[tuple[int, str, str]]:
        """Return custom PROFILE_N entries saved in the env file."""
        entries: list[tuple[int, str, str]] = []
        try:
            count = int(str(self._env.get("PROFILE_COUNT", "0") or "0"))
        except ValueError:
            count = 0
        for index in range(1, count + 1):
            profile_id = str(self._env.get(f"PROFILE_{index}_ID", "") or "").strip()
            if not profile_id:
                continue
            label = str(self._env.get(f"PROFILE_{index}_LABEL", "") or profile_id).strip()
            entries.append((index, profile_id, label))
        return entries

    def _choose_saved_profile_slot(self, title: str, prompt: str) -> tuple[int, str, str] | None:
        """Return a saved custom profile chosen by the user."""
        entries = self._saved_custom_profile_entries()
        if not entries:
            return None
        if len(entries) == 1:
            return entries[0]
        labels = [label for _slot, _profile_id, label in entries]
        duplicates = {label for label in labels if labels.count(label) > 1}
        choices = [
            f"{label} ({profile_id})" if label in duplicates else label
            for _slot, profile_id, label in entries
        ]
        choice, accepted = QInputDialog.getItem(
            self,
            title,
            prompt,
            choices,
            0,
            False,
        )
        if not accepted:
            return None
        try:
            return entries[choices.index(choice)]
        except ValueError:
            return None

    def _profile_env_slot_values(self, slot: int) -> dict[str, str]:
        """Return all PROFILE_N_* values for an env slot without the slot prefix."""
        prefix = f"PROFILE_{slot}_"
        return {
            key[len(prefix):]: value
            for key, value in self._env.items()
            if key.startswith(prefix)
        }

    def _rewrite_custom_profiles(self, profiles: list[dict[str, str]]) -> None:
        """Persist custom profiles compactly as PROFILE_1..PROFILE_N."""
        remove_keys = {
            key
            for key in self._env
            if re.match(r"^PROFILE_\d+_", key)
        }
        remove_keys.add("PROFILE_COUNT")
        vals: dict[str, str] = {"PROFILE_COUNT": str(len(profiles))}
        for index, profile in enumerate(profiles, start=1):
            vals.update({f"PROFILE_{index}_{key}": value for key, value in profile.items()})
        _write_env(vals, remove_keys=remove_keys)
        for key in remove_keys:
            self._env.pop(key, None)
        self._env.update(vals)

    @staticmethod
    def _profile_id(raw: str, default: str = "custom") -> str:
        """Normalize user-entered profile names to config profile ids."""
        text = str(raw or default or "custom").strip().lower()
        text = re.sub(r"[^a-z0-9_-]+", "-", text).strip("-")
        return text or default

    def _unique_custom_profile_id(self, label: str) -> str:
        """Return a profile id that does not shadow an existing built-in/custom profile."""
        base = self._profile_id(label)
        existing = {
            "default", "fast", "balanced", "deep", "private", "coding",
            *_PRESET_LABELS,
            *(profile_id for _idx, profile_id, _label in self._saved_custom_profile_entries()),
        }
        if base not in existing:
            return base
        suffix = 2
        while f"{base}-{suffix}" in existing:
            suffix += 1
        return f"{base}-{suffix}"

    def _model_section_values(self, sk: str) -> tuple[str, str, str]:
        """Return provider, primary model, and fallback rows for a model section."""
        rows = self._model_section_rows.get(sk, [])
        if not rows:
            return "", "", ""
        primary = rows[0]
        provider = str(primary["api_key_combo"].currentData() or "")
        model = self._model_value(primary)
        fallbacks = "\n".join(
            f"{r['api_key_combo'].currentData() or ''}:{self._model_value(r)}"
            for r in rows[1:]
            if (r["api_key_combo"].currentData() or "") and self._model_value(r)
        )
        return provider, model, fallbacks

    def _current_profile_values(self, label: str, profile_id: str) -> dict[str, str]:
        """Snapshot current settings into config.py's PROFILE_N_* contract."""
        import config as cfg

        llm_p, llm_m, llm_f = self._model_section_values("LLM")
        vis_p, vis_m, vis_f = self._model_section_values("VISION_LLM")
        mem_p, mem_m, mem_f = self._model_section_values("MEMORY_LLM")
        vb = getattr(self, "_voice_block", {})
        values = {
            "ID": profile_id,
            "LABEL": label,
            "LLM_PROVIDER": llm_p,
            "LLM_MODEL": llm_m,
            "LLM_FALLBACKS": llm_f,
            "VISION_LLM_PROVIDER": vis_p,
            "VISION_LLM_MODEL": vis_m,
            "VISION_LLM_FALLBACKS": vis_f,
            "MEMORY_LLM_PROVIDER": mem_p,
            "MEMORY_LLM_MODEL": mem_m,
            "MEMORY_LLM_FALLBACKS": mem_f,
            "CONTEXT_BROWSER_MAX_CHARS": _get(self._fields["CONTEXT_BROWSER_MAX_CHARS"]),
            "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"]),
            "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"]),
            "TOOL_TURN_MAX_CALLS": str(getattr(cfg, "TOOL_TURN_MAX_CALLS", 25)),
            "TOOL_TURN_MAX_RESULT_CHARS": str(getattr(cfg, "TOOL_TURN_MAX_RESULT_CHARS", 120000)),
            "TOOL_TURN_MAX_TOTAL_CHARS": str(getattr(cfg, "TOOL_TURN_MAX_TOTAL_CHARS", 300000)),
        }
        if vb:
            values.update(
                {
                    "CONTEXT_DOCUMENTS_MODE": str(vb["context_documents_mode"].currentData()),
                    "CONTEXT_BROWSER_MODE": str(vb["context_browser_mode"].currentData()),
                    "CONTEXT_GITHUB_MODE": str(vb["context_github_mode"].currentData()),
                    "CONTEXT_MEMORY_MODE": str(vb["context_memory_mode"].currentData()),
                    "CONTEXT_SCREENSHOT": str(vb["context_screenshot"].currentData()),
                    "FILE_ACCESS": str(vb["file_access"].currentData()),
                }
            )
        return values

    def _create_custom_profile(self) -> None:
        """Create a reusable custom profile from the current settings."""
        label, accepted = QInputDialog.getText(
            self,
            t("Create custom profile"),
            t("Profile name"),
        )
        label = str(label or "").strip()
        if not accepted or not label:
            return
        profile_id = self._unique_custom_profile_id(label)
        current_count = len(self._saved_custom_profile_entries())
        try:
            env_count = int(str(self._env.get("PROFILE_COUNT", "0") or "0"))
        except ValueError:
            env_count = current_count
        slot = max(env_count, current_count) + 1
        profile_values = self._current_profile_values(label, profile_id)
        vals = {
            "PROFILE_COUNT": str(slot),
        }
        vals.update({f"PROFILE_{slot}_{key}": value for key, value in profile_values.items()})
        try:
            _write_env(vals)
        except Exception as exc:  # noqa: BLE001 - leave the profile list unchanged
            QMessageBox.warning(
                self,
                t("Create profile failed"),
                t("Could not create {profile}: {error}").format(profile=label, error=exc),
            )
            return
        self._env.update(vals)
        self._active_preset_slug = ""
        self._pending_active_profile = profile_id
        self._refresh_profiles_menu()
        self._schedule_dirty_refresh()
        self._status_lbl.setText(
            t("{profile} profile created. Review changes, then Save changes to use it.").format(profile=label)
        )

    def _rename_custom_profile(self) -> None:
        """Rename a saved custom profile display label."""
        chosen = self._choose_saved_profile_slot(t("Rename profile"), t("Choose profile"))
        if chosen is None:
            return
        slot, profile_id, old_label = chosen
        label, accepted = QInputDialog.getText(
            self,
            t("Rename profile"),
            t("Profile name"),
            QLineEdit.EchoMode.Normal,
            old_label,
        )
        label = str(label or "").strip()
        if not accepted or not label or label == old_label:
            return
        duplicate = next(
            (
                saved_label
                for saved_slot, _saved_id, saved_label in self._saved_custom_profile_entries()
                if saved_slot != slot and saved_label.casefold() == label.casefold()
            ),
            "",
        )
        if duplicate:
            QMessageBox.warning(
                self,
                t("Rename profile failed"),
                t("A profile named {profile} already exists.").format(profile=label),
            )
            return
        vals = {f"PROFILE_{slot}_LABEL": label}
        try:
            _write_env(vals)
            if settings_profiles.profile_path(ENV_PATH, profile_id).is_file():
                settings_profiles.rename_profile(ENV_PATH, profile_id, label)
        except Exception as exc:  # noqa: BLE001 - preserve the old profile label
            QMessageBox.warning(
                self,
                t("Rename profile failed"),
                t("Could not rename {profile}: {error}").format(profile=old_label, error=exc),
            )
            return
        self._env.update(vals)
        self._refresh_profiles_menu()
        self._schedule_dirty_refresh()
        self._status_lbl.setText(
            t("{profile} profile renamed.").format(profile=label)
        )

    def _delete_custom_profile(self) -> None:
        """Delete a saved custom profile."""
        chosen = self._choose_saved_profile_slot(t("Delete profile"), t("Choose profile"))
        if chosen is None:
            return
        slot, profile_id, label = chosen
        answer = QMessageBox.question(
            self,
            t("Delete profile"),
            t("Delete {profile} profile?").format(profile=label),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        profiles = [
            self._profile_env_slot_values(index)
            for index, _pid, _label in self._saved_custom_profile_entries()
            if index != slot
        ]
        remove_keys = {
            key
            for key in self._env
            if re.match(r"^PROFILE_\d+_", key)
        }
        remove_keys.add("PROFILE_COUNT")
        if str(self._env.get("ACTIVE_PROFILE", "") or "") == profile_id:
            remove_keys.add("ACTIVE_PROFILE")
        if str(self._env.get("SETTINGS_PROFILE", "") or "") == profile_id:
            remove_keys.add("SETTINGS_PROFILE")
        vals: dict[str, str] = {"PROFILE_COUNT": str(len(profiles))}
        for index, profile in enumerate(profiles, start=1):
            vals.update({f"PROFILE_{index}_{key}": value for key, value in profile.items()})
        try:
            _write_env(vals, remove_keys=remove_keys - set(vals))
            settings_profiles.delete_profile(ENV_PATH, profile_id)
        except Exception as exc:  # noqa: BLE001 - keep the selected profile intact
            QMessageBox.warning(
                self,
                t("Delete profile failed"),
                t("Could not delete {profile}: {error}").format(profile=label, error=exc),
            )
            return
        for key in remove_keys:
            self._env.pop(key, None)
        self._env.update(vals)
        if str(getattr(self, "_pending_active_profile", "") or "") == profile_id:
            self._pending_active_profile = ""
        self._refresh_profiles_menu()
        self._schedule_dirty_refresh()
        self._status_lbl.setText(
            t("{profile} profile deleted.").format(profile=label)
        )

    def _profile_values_from_env_slot(self, slot: int) -> dict[str, str]:
        """Return UI-applicable values from a PROFILE_N env slot."""
        prefix = f"PROFILE_{slot}_"
        profile_values = {
            key[len(prefix):]: value
            for key, value in self._env.items()
            if key.startswith(prefix)
        }
        profile_id = str(profile_values.get("ID") or "").strip()
        if profile_id:
            isolated = settings_profiles.read_profile(ENV_PATH, profile_id)
            if isolated:
                profile_values.update(isolated)
        mapping = {
            "LLM_PROVIDER": "LLM_PROVIDER",
            "LLM_MODEL": "LLM_MODEL",
            "LLM_FALLBACKS": "LLM_FALLBACKS",
            "VISION_LLM_PROVIDER": "VISION_LLM_PROVIDER",
            "VISION_LLM_MODEL": "VISION_LLM_MODEL",
            "VISION_LLM_FALLBACKS": "VISION_LLM_FALLBACKS",
            "MEMORY_LLM_PROVIDER": "MEMORY_LLM_PROVIDER",
            "MEMORY_LLM_MODEL": "MEMORY_LLM_MODEL",
            "MEMORY_LLM_FALLBACKS": "MEMORY_LLM_FALLBACKS",
            "CONTEXT_BROWSER_MAX_CHARS": "CONTEXT_BROWSER_MAX_CHARS",
            "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS",
            "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": "CONTEXT_TOOL_DOCUMENT_MAX_CHARS",
        }
        return {env_key: profile_values[source_key] for source_key, env_key in mapping.items() if source_key in profile_values}

    def _apply_saved_profile(self, slot: int) -> None:
        """Load a custom profile into the settings UI for review."""
        values = self._profile_values_from_env_slot(slot)
        if not values:
            return
        profile_id = str(self._env.get(f"PROFILE_{slot}_ID", "") or "").strip()
        label = str(self._env.get(f"PROFILE_{slot}_LABEL", "") or profile_id).strip()
        isolated = settings_profiles.read_profile(ENV_PATH, profile_id)
        if isolated:
            self._load_isolated_profile(profile_id, label, isolated)
            return
        self._active_preset_slug = ""
        self._apply_env_values_to_ui(values)
        self._set_context_modes(
            documents=self._env.get(f"PROFILE_{slot}_CONTEXT_DOCUMENTS_MODE"),
            browser=self._env.get(f"PROFILE_{slot}_CONTEXT_BROWSER_MODE"),
            github=self._env.get(f"PROFILE_{slot}_CONTEXT_GITHUB_MODE"),
            memory=self._env.get(f"PROFILE_{slot}_CONTEXT_MEMORY_MODE"),
            screenshot=self._env.get(f"PROFILE_{slot}_CONTEXT_SCREENSHOT"),
            file_access=self._env.get(f"PROFILE_{slot}_FILE_ACCESS"),
        )
        self._pending_active_profile = profile_id
        self._refresh_profile_button_text()
        self._refresh_stt_active_backend()
        self._schedule_warning_marker_refresh()
        self._schedule_dirty_refresh()
        self._status_lbl.clear()

    def _load_isolated_profile(
        self,
        profile_id: str,
        label: str,
        values: dict[str, str],
    ) -> None:
        """Load a complete profile file while preserving the pre-switch baseline."""
        previous_baseline = dict(getattr(self, "_dirty_baseline", {}))
        merged = dict(_read_env())
        merged.update({str(key): str(value) for key, value in values.items()})
        merged["ACTIVE_PROFILE"] = profile_id
        merged["SETTINGS_PROFILE"] = profile_id
        merged.pop(_SETTINGS_PRESET_KEY, None)
        self._env = merged
        self._active_preset_slug = ""
        self._load_values()
        self._pending_active_profile = profile_id
        self._dirty_baseline = previous_baseline
        self._refresh_profile_button_text()
        self._refresh_stt_active_backend()
        self._schedule_warning_marker_refresh()
        self._refresh_dirty_state()
        self._status_lbl.setToolTip("")

    def _run_setup_check(self) -> None:
        """Run lightweight setup checks and show a reusable report."""
        if callable(self._on_setup_check):
            self._on_setup_check()
            return
        try:
            from core.setup_check import run_setup_check

            rows = run_setup_check()
        except Exception as exc:  # noqa: BLE001 - setup check must not crash settings
            rows = [
                {
                    "name": t("Setup check"),
                    "status": "fail",
                    "message": f"{type(exc).__name__}: {exc}",
                    "recommendation": t("Recommendation: close Settings, reopen it, and try again."),
                }
            ]
        lines = []
        for row in rows:
            status = t(_SETUP_CHECK_STATUS_LABELS.get(str(row.get("status") or "warn").lower(), "WARN"))
            name = _translate_status_message(str(row.get("name") or "Check"))
            message = _translate_status_message(str(row.get("message") or ""))
            recommendation = _translate_status_message(str(row.get("recommendation") or ""))
            block = f"{status} - {name}\n{message}"
            if recommendation:
                block += f"\n{recommendation}"
            lines.append(block)
        QMessageBox.information(self, t("Setup check"), "\n\n".join(lines))

    @staticmethod
    def _preset_slug(raw: str) -> str:
        """Handle preset slug for settings dialog."""
        value = str(raw or "").strip()
        return value if value in _PRESET_LABELS else _PRESET_SLUGS.get(value.lower(), "")

    @staticmethod
    def _preset_override_key(slug: str, key: str) -> str:
        """Handle preset override key for settings dialog."""
        safe_slug = slug.upper().replace("-", "_")
        return f"{_PRESET_ENV_PREFIX}{safe_slug}_{key}"

    @staticmethod
    def _key_from_preset_override(slug: str, env_key: str) -> str:
        """Handle key from preset override for settings dialog."""
        prefix = SettingsDialog._preset_override_key(slug, "")
        return env_key[len(prefix):] if env_key.startswith(prefix) else ""

    def _preset_saved_values(self, slug: str) -> dict[str, str]:
        """Handle preset saved values for settings dialog."""
        values: dict[str, str] = {}
        prefix = self._preset_override_key(slug, "")
        for env_key, value in self._env.items():
            if env_key.startswith(prefix):
                key = env_key[len(prefix):]
                if key:
                    values[key] = value
        return values

    def _preset_effective_values(self, slug: str) -> dict[str, str]:
        """Handle preset effective values for settings dialog."""
        values = dict(_PRESET_DEFAULTS.get(slug, {}))
        values.update(self._preset_saved_values(slug))
        return values

    def _preset_page_keys(self, slug: str, page: str, env: dict[str, str]) -> set[str]:
        """Handle preset page keys for settings dialog."""
        keys = set(self._reset_env_keys_for_page(page, env))
        prefix = self._preset_override_key(slug, "")
        for env_key in env:
            if env_key.startswith(prefix):
                key = env_key[len(prefix):]
                if key and self._page_for_dirty_key(key) == page:
                    keys.add(key)
        return keys

    def _preset_override_keys_for_keys(self, slug: str, keys: set[str], env: dict[str, str]) -> set[str]:
        """Handle preset override keys for keys for settings dialog."""
        prefix = self._preset_override_key(slug, "")
        remove = {self._preset_override_key(slug, key) for key in keys}
        remove.update(env_key for env_key in env if env_key.startswith(prefix) and env_key[len(prefix):] in keys)
        return remove

    def _current_tab_name(self) -> str:
        """Handle current tab name for settings dialog."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return ""
        idx = tabs.currentIndex()
        if 0 <= idx < len(getattr(self, "_tab_base_names", [])):
            return self._tab_base_names[idx]
        return tabs.tabText(idx).replace("*", "").strip()

    def _field_page_map(self) -> dict[str, str]:
        """Handle field page map for settings dialog."""
        mapping: dict[str, str] = {}
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return mapping
        for key, widget in self._fields.items():
            for idx, page in enumerate(self._tab_base_names):
                tab_widget = tabs.widget(idx)
                if tab_widget is widget or tab_widget.isAncestorOf(widget):
                    mapping[key] = page
                    break
        return mapping

    def _page_for_dirty_key(self, key: str) -> str:
        """Handle page for dirty key for settings dialog."""
        if key.startswith("CALLER_") or key.startswith("VOICE_") or key.startswith("SNIP_") or key in {
            "HOTKEY_VOICE", "HOTKEY_DICTATE", "DICTATE_MODE",
            "HOTKEY_ADD_CONTEXT", "HOTKEY_CLEAR_CONTEXT", "HOTKEY_SNIP",
            "HOTKEY_READ_SELECTION_ALOUD", "HOTKEY_VOICE_LIVE",
            "INTENT_CONTEXT_TOGGLE_KEYS", "INTENT_OVERLAY_TIMEOUT_MS",
        }:
            return "Keybinds"
        if key.startswith("API_KEY_ROW"):
            return "Connections"
        if key.startswith("LLM_") or key.startswith("VISION_LLM_") or key.startswith("MEMORY_LLM_"):
            return "LLM"
        if key in {"ACTIVE_PROFILE", "SETTINGS_PROFILE"}:
            return "App"
        if key == "_PROFILE_STORAGE_MODE":
            return "App"
        if key.startswith("THEME_") or key.startswith("BUBBLE_") or key.startswith("TTS_PLAYBACK"):
            return self._field_page_map().get(key, "App")
        return self._field_page_map().get(key, "")

    @staticmethod
    def _caller_label_value(blk: dict) -> str:
        """Return a built-in caller's stable source label or a custom label."""
        editor = blk["label"]
        value = _get(editor)
        source = str(editor.property("builtinCallerLabel") or "")
        if source and value.strip() == t(source):
            return source
        return value

    def _validate_caller_mutations(self) -> bool:
        """Reject incomplete, duplicate, invalid, or stale shortcut/action edits."""
        labels: set[str] = set()
        try:
            for blk in self._caller_blocks:
                label = self._caller_label_value(blk).strip()
                if not label:
                    raise ValueError(t("Every intent shortcut needs a name."))
                if any(ord(char) < 32 for char in label):
                    raise ValueError(t("Intent shortcut names cannot contain control characters."))
                normalized_label = label.casefold()
                if normalized_label in labels:
                    raise ValueError(t("Intent shortcut names must be unique."))
                labels.add(normalized_label)

                keys: set[str] = set()
                custom_key = _get(blk["custom_key"]).strip().casefold()
                if custom_key:
                    if len(custom_key) != 1 or not custom_key.isprintable():
                        raise ValueError(t("Action keys must be one printable character."))
                    keys.add(custom_key)
                for row in blk["intent_rows"]:
                    key = _get(row["key"]).strip().casefold()
                    action_label = _get(row["label"]).strip()
                    prompt = _get(row["prompt"]).strip()
                    if not key or not action_label or not prompt:
                        raise ValueError(t("Every action choice needs a key, label, and prompt."))
                    if len(key) != 1 or not key.isprintable():
                        raise ValueError(t("Action keys must be one printable character."))
                    if key in keys:
                        raise ValueError(t("Action keys must be unique within each intent shortcut."))
                    if any(ord(char) < 32 and char not in "\n\t" for char in prompt):
                        raise ValueError(t("Action prompt data is invalid."))
                    keys.add(key)
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, t("Invalid intent shortcut"), str(exc))
            return False
        return True

    def _snapshot_settings(self) -> dict[str, str]:
        """Handle snapshot settings for settings dialog."""
        snapshot: dict[str, str] = {}
        snapshot["ACTIVE_PROFILE"] = self._selected_profile_id()
        snapshot["_PROFILE_STORAGE_MODE"] = (
            "legacy-preset"
            if self._preset_slug(getattr(self, "_active_preset_slug", ""))
            else "profile-file"
        )

        for key, widget in self._fields.items():
            if key.endswith("_API_KEY"):
                snapshot[key] = _get(widget).strip()
            elif isinstance(widget, QCheckBox):
                snapshot[key] = str(widget.isChecked())
            elif key == "LIVE_VOICE_MODEL":
                snapshot[key] = self._live_voice_model_value()
            elif key == "LIVE_VOICE_VOICE_NAME":
                snapshot[key] = self._live_voice_voice_value()
            elif isinstance(widget, (QLineEdit, QComboBox, QTextEdit, _FolderListEditor)):
                snapshot[key] = _get(widget)

        for idx, row in enumerate(getattr(self, "_api_key_rows", []), 1):
            snapshot[f"API_KEY_ROW_{idx}_PROVIDER"] = _get(row["provider"])
            snapshot[f"API_KEY_ROW_{idx}_ALIAS"] = row["alias"].text()
            snapshot[f"API_KEY_ROW_{idx}_KEY"] = row["key"].text()
            if _get(row["provider"]) == "custom":
                snapshot[f"API_KEY_ROW_{idx}_CONNECTION_ID"] = str(row.get("connection_id") or "")
                snapshot[f"API_KEY_ROW_{idx}_BASE_URL"] = row["custom_address"].text()
        snapshot["API_KEY_ROW_COUNT"] = str(len(getattr(self, "_api_key_rows", [])))

        for section, rows in getattr(self, "_model_section_rows", {}).items():
            snapshot[f"{section}_ROW_COUNT"] = str(len(rows))
            for idx, row in enumerate(rows, 1):
                snapshot[f"{section}_{idx}_PROVIDER"] = str(row["api_key_combo"].currentData() or "")
                snapshot[f"{section}_{idx}_MODEL"] = self._model_value(row)

        snapshot["CALLER_COUNT"] = str(len(getattr(self, "_caller_blocks", [])))
        for idx, blk in enumerate(getattr(self, "_caller_blocks", []), 1):
            prefix = f"CALLER_{idx}"
            snapshot[f"{prefix}_HOTKEY"] = _get(blk["hotkey"])
            snapshot[f"{prefix}_HOTKEY_2"] = _get(blk["hotkey_2"])
            snapshot[f"{prefix}_ENABLED"] = str(blk["enabled"].isChecked())
            snapshot[f"{prefix}_LABEL"] = self._caller_label_value(blk)
            snapshot[f"{prefix}_PASTE_BACK"] = str(blk["paste_back"].isChecked())
            snapshot[f"{prefix}_CUSTOM_KEY"] = _get(blk["custom_key"])
            snapshot[f"{prefix}_CUSTOM_LABEL"] = _get(blk["custom_label"])
            snapshot[f"{prefix}_CONTEXT_AMBIENT"] = str(blk["context_ambient"].isChecked())
            snapshot[f"{prefix}_CONTEXT_CLIPBOARD"] = str(blk["context_clipboard"].isChecked())
            for name in (
                "context_documents_mode", "context_browser_mode",
                "context_github_mode", "context_memory_mode", "context_screenshot",
                "file_access",
            ):
                snapshot[f"{prefix}_{name.upper()}"] = str(blk[name].currentData())
            snapshot[f"{prefix}_TOOLS"] = format_tool_modes(blk.get("tool_overrides") or {})
            snapshot[f"{prefix}_INTENT_COUNT"] = str(len(blk["intent_rows"]))
            for row_idx, row in enumerate(blk["intent_rows"], 1):
                row_prefix = f"{prefix}_INTENT_{row_idx}"
                snapshot[f"{row_prefix}_KEY"] = _get(row["key"])
                snapshot[f"{row_prefix}_LABEL"] = _get(row["label"])
                snapshot[f"{row_prefix}_PROMPT"] = _get(row["prompt"])

        if hasattr(self, "_voice_block"):
            vb = self._voice_block
            snapshot["VOICE_CONTEXT_AMBIENT"] = str(vb["context_ambient"].isChecked())
            snapshot["VOICE_CONTEXT_CLIPBOARD"] = str(vb["context_clipboard"].isChecked())
            for name in (
                "context_documents_mode", "context_browser_mode",
                "context_github_mode", "context_memory_mode", "context_screenshot",
                "file_access",
            ):
                snapshot[f"VOICE_{name.upper()}"] = str(vb[name].currentData())
            snapshot["VOICE_TOOLS"] = format_tool_modes(vb.get("tool_overrides") or {})

        if hasattr(self, "_snip_block"):
            snapshot.update(self._snip_context_values())

        return snapshot

    def _snip_context_values(self) -> dict[str, str]:
        """Return persisted values for the region-snip context block."""
        sb = self._snip_block
        documents_mode = str(sb["context_documents_mode"].currentData())
        browser_mode = str(sb["context_browser_mode"].currentData())
        github_mode = str(sb["context_github_mode"].currentData())
        memory_mode = str(sb["context_memory_mode"].currentData())
        return {
            "SNIP_CONTEXT_AMBIENT": str(sb["context_ambient"].isChecked()),
            "SNIP_CONTEXT_CLIPBOARD": str(sb["context_clipboard"].isChecked()),
            "SNIP_CONTEXT_DOCUMENTS_MODE": documents_mode,
            "SNIP_CONTEXT_BROWSER_MODE": browser_mode,
            "SNIP_CONTEXT_GITHUB_MODE": github_mode,
            "SNIP_CONTEXT_MEMORY_MODE": memory_mode,
            "SNIP_CONTEXT_SCREENSHOT": "off",
            "SNIP_FILE_ACCESS": str(sb["file_access"].currentData()),
            "SNIP_TOOLS": format_tool_modes(sb.get("tool_overrides") or {}),
            "SNIP_CONTEXT_DOCUMENTS": str(documents_mode == "auto"),
            "SNIP_CONTEXT_TOOLS": str(
                any(mode == "model" for mode in (documents_mode, browser_mode, github_mode, memory_mode))
            ),
        }

    def _reset_dirty_baseline(self) -> None:
        """Reset dirty baseline."""
        self._dirty_refresh_scheduled = False
        self._dirty_baseline = self._snapshot_settings()
        self._refresh_dirty_state()

    def _schedule_dirty_refresh(self) -> None:
        """Schedule dirty refresh."""
        if (
            getattr(self, "_loading_values", False)
            or getattr(self, "_saving_settings", False)
            or getattr(self, "_disposing", False)
        ):
            return
        if not hasattr(self, "_dirty_baseline"):
            return
        if getattr(self, "_dirty_refresh_scheduled", False):
            return
        self._dirty_refresh_scheduled = True
        QTimer.singleShot(0, self._refresh_dirty_state)

    def _wire_change_tracking(self, root: QWidget | None = None) -> None:
        """Handle wire change tracking for settings dialog."""
        root = root or self
        widgets = [root]
        widgets.extend(root.findChildren(QWidget))
        for widget in widgets:
            if widget.property("_openwand_dirty_connected"):
                continue
            if isinstance(widget, _FolderListEditor):
                widget.valueChanged.connect(self._schedule_dirty_refresh)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(lambda _text="", self=self: self._schedule_dirty_refresh())
            elif isinstance(widget, QTextEdit):
                widget.textChanged.connect(self._schedule_dirty_refresh)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(lambda _idx=0, self=self: self._schedule_dirty_refresh())
                widget.currentTextChanged.connect(lambda _text="", self=self: self._schedule_dirty_refresh())
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(lambda _checked=False, self=self: self._schedule_dirty_refresh())
            widget.setProperty("_openwand_dirty_connected", True)

    def _refresh_dirty_state(self) -> None:
        """Refresh dirty state."""
        self._dirty_refresh_scheduled = False
        if not self._values_loaded:
            # Pages are still being built, so a snapshot would be missing fields and
            # read as spurious edits. The pass at the end of _load_values covers it.
            return
        current = self._snapshot_settings()
        baseline = getattr(self, "_dirty_baseline", {})
        keys = {key for key, value in current.items() if baseline.get(key) != value}
        keys.update(key for key in baseline if current.get(key) != baseline.get(key))
        self._dirty_keys = keys

        for key, widget in self._fields.items():
            is_dirty = key in keys
            if widget.property("dirty") != is_dirty:
                widget.setProperty("dirty", is_dirty)
                widget.style().unpolish(widget)
                widget.style().polish(widget)

        dirty_pages = {
            page for page in (self._page_for_dirty_key(key) for key in keys) if page
        }
        self._tab_dirty_names = dirty_pages
        self._refresh_tab_labels()

        apply_btn = getattr(self, "_apply_btn", None)
        if apply_btn is not None:
            apply_btn.setEnabled(bool(keys))

        if keys:
            display_by_internal = {
                internal: t(title) for internal, title in _SETTINGS_PAGE_META
            }
            visible_pages = [
                display_by_internal.get(page, t(page))
                for page in self._tab_base_names
                if page in dirty_pages
            ]
            self._status_lbl.setText(
                t("Unsaved changes: {pages}").format(pages=", ".join(visible_pages))
            )
        elif self._status_lbl.text().startswith(t("Unsaved changes")):
            self._status_lbl.setText("")

    def _refresh_tab_labels(self) -> None:
        """Refresh tab labels."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return
        for idx, base in enumerate(self._tab_base_names):
            suffix = " *" if base in getattr(self, "_tab_dirty_names", set()) else ""
            tabs.setTabText(idx, t(base) + suffix)
            nav = getattr(self, "_settings_nav", None)
            if isinstance(nav, QListWidget) and idx < nav.count():
                title = _SETTINGS_PAGE_META[idx][1] if idx < len(_SETTINGS_PAGE_META) else base
                nav.item(idx).setText(t(title))
                nav.item(idx).setData(
                    _SETTINGS_NAV_DIRTY_ROLE,
                    base in getattr(self, "_tab_dirty_names", set()),
                )
        nav = getattr(self, "_settings_nav", None)
        if isinstance(nav, QListWidget):
            nav.viewport().update()

    def _schedule_search_index_refresh(self) -> None:
        """Coalesce many index rebuilds into one pass on the next event loop turn.

        Rebuilding the index walks every widget on every page, so the dynamic row
        helpers calling it directly made opening Settings rebuild it more than a
        dozen times. Callers that only need the index eventually should use this.
        """
        if getattr(self, "_disposing", False):
            return
        if getattr(self, "_search_index_refresh_scheduled", False):
            return
        self._search_index_refresh_scheduled = True
        QTimer.singleShot(0, self._refresh_search_index)

    def _refresh_search_index(self) -> None:
        """Refresh search index."""
        self._search_index_refresh_scheduled = False
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return

        field_pages = self._field_page_map()
        per_page_fields: dict[str, list[str]] = {}
        for key, page in field_pages.items():
            per_page_fields.setdefault(page, []).append(key)
        text_by_page: dict[str, str] = {}
        for idx, page in enumerate(self._tab_base_names):
            tab_widget = tabs.widget(idx)
            meta = _SETTINGS_PAGE_META[idx] if idx < len(_SETTINGS_PAGE_META) else (page, page)
            parts = [
                page,
                *per_page_fields.get(page, []),
                *(str(part) for part in meta),
                *(t(str(part)) for part in meta),
            ]
            for widget in tab_widget.findChildren(QWidget):
                parts.extend(self._settings_search_widget_parts(widget))
            text_by_page[page] = " ".join(part for part in parts if part).lower()
        self._tab_search_text = text_by_page
        self._apply_settings_search()

    @staticmethod
    def _settings_search_original_text(widget: QWidget, prop_name: str) -> str:
        """Return text saved before runtime localization changed a widget property."""
        try:
            return str(widget.property(f"_openwand_i18n_{prop_name}") or "")
        except Exception:
            return ""

    def _settings_search_widget_parts(self, widget: QWidget) -> list[str]:
        """Return user-facing and stable search terms for one settings widget."""
        original = self._settings_search_original_text
        parts = [widget.objectName(), widget.accessibleName()]
        if isinstance(widget, QLabel):
            parts.extend([widget.text(), original(widget, "text"), widget.toolTip()])
        elif isinstance(widget, QLineEdit):
            parts.extend([
                widget.placeholderText(), original(widget, "placeholder"),
                widget.toolTip(), original(widget, "tooltip"),
                widget.text(),
            ])
        elif isinstance(widget, QTextEdit):
            parts.extend([
                widget.placeholderText(), original(widget, "placeholder"),
                widget.toolTip(), original(widget, "tooltip"),
            ])
        elif isinstance(widget, QComboBox):
            parts.extend([widget.toolTip(), original(widget, "tooltip")])
            parts.extend(widget.itemText(i) for i in range(widget.count()))
        elif isinstance(widget, (QCheckBox, QPushButton)):
            parts.extend([
                widget.text(), original(widget, "text"),
                widget.toolTip(), original(widget, "tooltip"),
            ])
        else:
            parts.extend([widget.toolTip(), original(widget, "tooltip")])
        return [str(part) for part in parts if part]

    @staticmethod
    def _settings_search_layout_widgets(item) -> list[QWidget]:
        """Return the top-level widgets represented by one layout item."""
        widget = item.widget()
        if widget is not None:
            return [widget]
        layout = item.layout()
        if layout is None:
            return []
        widgets: list[QWidget] = []
        for idx in range(layout.count()):
            widgets.extend(SettingsDialog._settings_search_layout_widgets(layout.itemAt(idx)))
        return widgets

    def _settings_search_unit_text(self, widgets: list[QWidget]) -> str:
        """Build searchable text for a complete setting row or card block."""
        parts: list[str] = []
        field_items = list(self._fields.items())
        for root in widgets:
            parts.extend(self._settings_search_widget_parts(root))
            for child in root.findChildren(QWidget):
                parts.extend(self._settings_search_widget_parts(child))
            parts.extend(
                key
                for key, field in field_items
                if root is field or root.isAncestorOf(field)
            )
        return " ".join(parts).lower()

    def _settings_search_card_units(self, card: QFrame) -> list[tuple[list[QWidget], str]]:
        """Return independently filterable rows/blocks inside one settings card."""
        layout = card.layout()
        if layout is None:
            return []
        units: list[tuple[list[QWidget], str]] = []
        for idx in range(layout.count()):
            item = layout.itemAt(idx)
            widget = item.widget()
            if widget is not None and widget.objectName() in {"sectionHeader", "sectionHeadingRow"}:
                continue
            form = widget.layout() if widget is not None else None
            if isinstance(form, QFormLayout):
                for row in range(form.rowCount()):
                    row_widgets: list[QWidget] = []
                    for role in (
                        QFormLayout.ItemRole.LabelRole,
                        QFormLayout.ItemRole.FieldRole,
                        QFormLayout.ItemRole.SpanningRole,
                    ):
                        row_item = form.itemAt(row, role)
                        if row_item is not None:
                            row_widgets.extend(self._settings_search_layout_widgets(row_item))
                    row_widgets = list(dict.fromkeys(row_widgets))
                    if row_widgets:
                        units.append((row_widgets, self._settings_search_unit_text(row_widgets)))
                continue
            widgets = self._settings_search_layout_widgets(item)
            if widgets:
                units.append((widgets, self._settings_search_unit_text(widgets)))
        return units

    def _settings_search_hide(self, widget: QWidget) -> None:
        """Hide a search result unit while remembering its non-search state."""
        if widget not in self._settings_search_visibility:
            self._settings_search_visibility[widget] = widget.isHidden()
        widget.hide()

    def _restore_settings_search_visibility(self) -> None:
        """Restore only visibility changes made by the global settings search."""
        for widget, was_hidden in self._settings_search_visibility.items():
            try:
                widget.setHidden(was_hidden)
            except RuntimeError:
                pass
        self._settings_search_visibility.clear()

    @staticmethod
    def _settings_search_card_title(card: QFrame) -> str:
        """Return the card's own header text, excluding nested controls."""
        return " ".join(
            label.text()
            for label in card.findChildren(QLabel)
            if label.objectName() == "sectionHeader"
        ).lower()

    def _filter_settings_page(self, idx: int, page: str, query: str) -> None:
        """Hide unrelated cards and form rows within one matching settings page."""
        tabs = self._tabs
        tab_widget = tabs.widget(idx)
        meta = _SETTINGS_PAGE_META[idx] if idx < len(_SETTINGS_PAGE_META) else (page, page)
        page_title_text = " ".join(
            [*(str(part) for part in meta), *(t(str(part)) for part in meta)]
        ).lower()
        if query in page_title_text:
            return

        cards = [
            card
            for card in tab_widget.findChildren(QFrame)
            if card.objectName() == "card"
        ]
        groups = tab_widget.findChildren(QWidget, "settingsAreaGroup")
        matched_groups = {
            group
            for group in groups
            if query in " ".join(
                label.text()
                for label in group.findChildren(QLabel)
                if label.objectName() == "areaHeader"
            ).lower()
        }

        for card in cards:
            if query in self._settings_search_card_title(card):
                continue
            if any(group.isAncestorOf(card) for group in matched_groups):
                continue
            units = self._settings_search_card_units(card)
            matching_units = [unit for unit in units if query in unit[1]]
            if not matching_units:
                self._settings_search_hide(card)
                continue
            matching_widgets = {
                widget
                for widgets, _text in matching_units
                for widget in widgets
            }
            for widgets, _text in units:
                if any(widget in matching_widgets for widget in widgets):
                    continue
                for widget in widgets:
                    self._settings_search_hide(widget)

        for group in groups:
            if group in matched_groups:
                continue
            group_cards = [card for card in cards if group.isAncestorOf(card)]
            if group_cards and all(card.isHidden() for card in group_cards):
                self._settings_search_hide(group)

    def _apply_settings_search(self) -> None:
        """Apply settings search."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return
        search_box = getattr(self, "_settings_search", None)
        if search_box is not None and search_box.text().strip():
            # Searching spans every page, so none of them may still be pending.
            self._build_deferred_pages()
        self._restore_settings_search_visibility()
        search = getattr(self, "_settings_search", None)
        query = search.text().strip().lower() if search is not None else ""
        visible: list[int] = []
        for idx, page in enumerate(self._tab_base_names):
            match = not query or query in self._tab_search_text.get(page, page.lower())
            tabs.setTabVisible(idx, match)
            nav = getattr(self, "_settings_nav", None)
            if isinstance(nav, QListWidget) and idx < nav.count():
                nav.item(idx).setHidden(not match)
            if match:
                visible.append(idx)
                if query:
                    self._filter_settings_page(idx, page, query)
        if query and visible and not tabs.isTabVisible(tabs.currentIndex()):
            tabs.setCurrentIndex(visible[0])
        if query:
            count = len(visible)
            self._search_status_lbl.setText(
                t("No matching settings.") if count == 0 else t("{count} matching pages.").format(count=count)
            )
            self._search_status_lbl.show()
        else:
            self._search_status_lbl.hide()

    def _set_value_for_env_key(self, key: str, value: str) -> bool:
        """Set value for env key."""
        if key == "LIVE_VOICE_MODEL":
            provider = _get(self._fields["LIVE_VOICE_PROVIDER"]).strip() or "google"
            self._fill_live_voice_model_combo(provider, value)
            return True
        if key == "LIVE_VOICE_VOICE_NAME":
            self._fill_live_voice_voice_combo(value)
            return True
        if key in self._fields:
            widget = self._fields[key]
            if isinstance(widget, QCheckBox):
                widget.setChecked(str(value).strip().lower() == "true")
            elif isinstance(widget, (QLineEdit, QComboBox, QTextEdit)):
                _set(widget, value)
            return True

        if key == "VOICE_CONTEXT_AMBIENT" and hasattr(self, "_voice_block"):
            self._voice_block["context_ambient"].setChecked(str(value).strip().lower() == "true")
            return True
        if key == "VOICE_CONTEXT_CLIPBOARD" and hasattr(self, "_voice_block"):
            self._voice_block["context_clipboard"].setChecked(str(value).strip().lower() == "true")
            return True
        voice_mode_keys = {
            "VOICE_CONTEXT_DOCUMENTS_MODE": "context_documents_mode",
            "VOICE_CONTEXT_BROWSER_MODE": "context_browser_mode",
            "VOICE_CONTEXT_GITHUB_MODE": "context_github_mode",
            "VOICE_CONTEXT_MEMORY_MODE": "context_memory_mode",
            "VOICE_CONTEXT_SCREENSHOT": "context_screenshot",
            "VOICE_FILE_ACCESS": "file_access",
        }
        if key in voice_mode_keys and hasattr(self, "_voice_block"):
            _set(self._voice_block[voice_mode_keys[key]], value)
            return True
        if key == "VOICE_TOOLS" and hasattr(self, "_voice_block"):
            self._voice_block["tool_overrides"] = parse_tool_modes(value)
            return True

        if not key.startswith("CALLER_"):
            return False
        parts = key.split("_", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            return False
        idx = int(parts[1]) - 1
        if idx < 0 or idx >= len(getattr(self, "_caller_blocks", [])):
            return False
        blk = self._caller_blocks[idx]
        caller_key = parts[2]
        simple_widgets = {
            "HOTKEY": "hotkey",
            "LABEL": "label",
            "CUSTOM_KEY": "custom_key",
            "CUSTOM_LABEL": "custom_label",
        }
        if caller_key in simple_widgets:
            _set(blk[simple_widgets[caller_key]], value)
            return True
        if caller_key == "PASTE_BACK":
            paste_back = str(value).strip().lower() == "true"
            blk["paste_back" if paste_back else "show_only"].setChecked(True)
            return True
        if caller_key == "CONTEXT_AMBIENT":
            blk["context_ambient"].setChecked(str(value).strip().lower() == "true")
            return True
        if caller_key == "CONTEXT_CLIPBOARD":
            blk["context_clipboard"].setChecked(str(value).strip().lower() == "true")
            return True
        mode_keys = {
            "CONTEXT_DOCUMENTS_MODE": "context_documents_mode",
            "CONTEXT_BROWSER_MODE": "context_browser_mode",
            "CONTEXT_GITHUB_MODE": "context_github_mode",
            "CONTEXT_MEMORY_MODE": "context_memory_mode",
            "CONTEXT_SCREENSHOT": "context_screenshot",
            "FILE_ACCESS": "file_access",
        }
        if caller_key in mode_keys:
            _set(blk[mode_keys[caller_key]], value)
            return True
        if caller_key == "TOOLS":
            blk["tool_overrides"] = parse_tool_modes(value)
            return True
        return False

    def _apply_env_values_to_ui(self, values: dict[str, str]) -> None:
        """Apply env values to ui."""
        for section, provider_key, model_key, fallbacks_key in (
            ("LLM", "LLM_PROVIDER", "LLM_MODEL", "LLM_FALLBACKS"),
            ("VISION_LLM", "VISION_LLM_PROVIDER", "VISION_LLM_MODEL", "VISION_LLM_FALLBACKS"),
            ("MEMORY_LLM", "MEMORY_LLM_PROVIDER", "MEMORY_LLM_MODEL", "MEMORY_LLM_FALLBACKS"),
        ):
            if not any(key in values for key in (provider_key, model_key, fallbacks_key)):
                continue
            rows = getattr(self, "_model_section_rows", {}).get(section, [])
            current_provider = str(rows[0]["api_key_combo"].currentData() or "") if rows else ""
            current_model = self._model_value(rows[0]) if rows else ""
            for row in list(rows):
                self._remove_model_section_row(section, row)
            self._add_model_section_row(
                section,
                values.get(provider_key, current_provider),
                values.get(model_key, current_model),
            )
            for provider, model in _parse_fallback_rows(values.get(fallbacks_key, "")):
                self._add_model_section_row(section, provider, model)

        for key, value in values.items():
            if key in {
                _SETTINGS_PRESET_KEY, "CALLER_COUNT",
                "LLM_PROVIDER", "LLM_MODEL", "LLM_FALLBACKS",
                "VISION_LLM_PROVIDER", "VISION_LLM_MODEL", "VISION_LLM_FALLBACKS",
                "MEMORY_LLM_PROVIDER", "MEMORY_LLM_MODEL", "MEMORY_LLM_FALLBACKS",
            }:
                continue
            self._set_value_for_env_key(key, value)

    def _preset_values_to_persist(self, vals: dict[str, str]) -> dict[str, str]:
        """Handle preset values to persist for settings dialog."""
        slug = self._preset_slug(getattr(self, "_active_preset_slug", ""))
        if not slug:
            return {}
        preset_vals = {_SETTINGS_PRESET_KEY: slug}
        for key, value in vals.items():
            if key in secret_store.API_KEY_NAMES or key.endswith("_API_KEY"):
                continue
            preset_vals[self._preset_override_key(slug, key)] = str(value)
        return preset_vals

    def _set_context_modes(self, *, documents: str | None = None, browser: str | None = None,
                           github: str | None = None, memory: str | None = None,
                           screenshot: str | None = None, file_access: str | None = None,
                           clear_tools: bool = False) -> None:
        """Set context modes."""
        blocks = list(getattr(self, "_caller_blocks", []))
        if hasattr(self, "_voice_block"):
            blocks.append(self._voice_block)
        for blk in blocks:
            updates = {
                "context_documents_mode": documents,
                "context_browser_mode": browser,
                "context_github_mode": github,
                "context_memory_mode": memory,
                "context_screenshot": screenshot,
                "file_access": file_access,
            }
            for key, value in updates.items():
                if value is not None:
                    _set(blk[key], value)
            if clear_tools:
                blk["tool_overrides"] = {}

    def _apply_preset(self, preset: str) -> None:
        """Load a built-in file-backed profile into Settings."""
        preset_key = self._preset_slug(preset)
        if not preset_key:
            return
        self._build_deferred_pages()
        isolated = settings_profiles.read_profile(ENV_PATH, preset_key)
        if isolated:
            self._load_isolated_profile(preset_key, t(_PRESET_LABELS[preset_key]), isolated)
            return
        self._active_preset_slug = ""
        self._pending_active_profile = preset_key
        values = dict(_PRESET_DEFAULTS.get(preset_key, {}))
        self._apply_env_values_to_ui(values)
        self._rebuild_stt_languages()
        context_defaults = _PRESET_CONTEXT_DEFAULTS.get(preset_key, {})
        self._set_context_modes(
            documents=context_defaults.get("documents"),
            browser=context_defaults.get("browser"),
            github=context_defaults.get("github"),
            memory=context_defaults.get("memory"),
            screenshot=context_defaults.get("screenshot"),
            clear_tools=context_defaults.get("clear_tools", "").lower() == "true",
        )
        self._refresh_stt_active_backend()
        self._refresh_profile_button_text()
        self._schedule_warning_marker_refresh()
        self._schedule_dirty_refresh()
        self._status_lbl.clear()
        self._status_lbl.setToolTip("")

    def _tab_llm(self) -> QWidget:
        """Build separate connection and model-routing pages over one config model."""
        connections_scroll = QScrollArea()
        connections_scroll.setWidgetResizable(True)
        connections_scroll.setFrameShape(QFrame.Shape.NoFrame)
        connections_w = QWidget()
        connections_outer = QVBoxLayout(connections_w)
        connections_outer.setContentsMargins(12, 12, 12, 12)
        connections_outer.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)
        credentials_group, credentials_layout = self._area_group("Provider credentials")

        # ── OAUTH LOGINS ─────────────────────────────────────────────────
        auth_card, auth_cv = self._card("OAuth logins")
        auth_cv.setSpacing(6)

        auth_cv.addWidget(
            self._oauth_provider_row(
                key="chatgpt",
                icon_asset="provider-icons/chatgpt.svg",
                title=t("ChatGPT OAuth"),
                purpose=t("Allows you to use ChatGPT subscription models in Model settings."),
                sign_in=self._chatgpt_login_browser,
                sign_out=self._chatgpt_logout,
            )
        )

        auth_cv.addWidget(
            self._oauth_provider_row(
                key="github",
                icon_asset="provider-icons/github-invertocat.svg",
                title=t("GitHub OAuth"),
                purpose=t(
                    "Allows authenticated GitHub tools to give the model repository metadata and "
                    "issues/PRs as context, and lets you use Copilot models when your account has "
                    "Copilot access."
                ),
                sign_in=self._github_login_device,
                sign_out=self._github_logout,
            )
        )

        self._fields["GITHUB_CLIENT_ID"] = QLineEdit()
        self._fields["GITHUB_CLIENT_ID"].setPlaceholderText("Developer OAuth app client ID override")
        self._fields["GITHUB_OAUTH_SCOPES"] = QLineEdit()
        self._fields["GITHUB_OAUTH_SCOPES"].setPlaceholderText("e.g. repo read:user user:email")
        credentials_layout.addWidget(auth_card)

        # ── PROVIDER CONNECTIONS card ─────────────────────────────────────
        api_keys_card, api_keys_cv = self._card("Provider connections")
        connection_tools = QWidget()
        connection_tools_h = QHBoxLayout(connection_tools)
        connection_tools_h.setContentsMargins(0, 0, 0, 0)
        connection_tools_h.setSpacing(8)
        self._connections_search = QLineEdit()
        self._connections_search.setObjectName("settingsConnectionsSearch")
        self._connections_search.setPlaceholderText(t("Search providers or aliases..."))
        self._connections_search.setClearButtonEnabled(True)
        self._connections_filter = _NoScrollCombo()
        self._connections_filter.addItem(t("All connections"), "all")
        self._connections_filter.addItem(t("Cloud providers"), "cloud")
        self._connections_filter.addItem(t("Local and custom"), "local")
        connection_tools_h.addWidget(self._connections_search, 1)
        connection_tools_h.addWidget(self._connections_filter, 0)
        api_keys_cv.addWidget(connection_tools)

        col_hdr_w = QWidget()
        col_hdr_h = QHBoxLayout(col_hdr_w)
        col_hdr_h.setContentsMargins(0, 0, 0, 0)
        col_hdr_h.setSpacing(8)
        for txt, stretch in [("Provider", 2), ("Alias", 2), ("API Key", 3)]:
            lbl = QLabel(f"<small><b>{t(txt)}</b></small>")
            col_hdr_h.addWidget(lbl, stretch)
        col_hdr_h.addSpacing(32)
        api_keys_cv.addWidget(col_hdr_w)

        self._api_key_rows_container = QWidget()
        self._api_key_rows_layout = QVBoxLayout(self._api_key_rows_container)
        self._api_key_rows_layout.setSpacing(4)
        self._api_key_rows_layout.setContentsMargins(0, 0, 0, 0)
        api_keys_cv.addWidget(self._api_key_rows_container)

        self._connections_empty_lbl = QLabel(t("No matching connections."))
        self._connections_empty_lbl.setStyleSheet("color: palette(placeholder-text);")
        self._connections_empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._connections_empty_lbl.hide()
        api_keys_cv.addWidget(self._connections_empty_lbl)

        add_key_btn = QPushButton(t("+ Add connection"))
        self._connections_show_more_btn = QPushButton()
        self._connections_show_more_btn.setFlat(True)
        self._connections_show_more_btn.hide()
        self._connections_expanded = False
        akw = QHBoxLayout()
        akw.setContentsMargins(0, 0, 0, 0)
        akw.addWidget(add_key_btn)
        akw.addWidget(self._connections_show_more_btn)
        akw.addStretch()
        api_keys_cv.addLayout(akw)
        add_key_btn.clicked.connect(self._add_inline_connection_row)
        self._connections_show_more_btn.clicked.connect(self._toggle_connections_expanded)
        self._connections_search.textChanged.connect(self._refresh_connection_rows_filter)
        self._connections_filter.currentIndexChanged.connect(self._refresh_connection_rows_filter)

        credentials_layout.addWidget(api_keys_card)

        # ── CUSTOM PROVIDER card ──────────────────────────────────────────
        self._fields["CUSTOM_BASE_URL"] = QLineEdit()
        self._fields["CUSTOM_BASE_URL"].setPlaceholderText("https://api.example.com/v1")
        self._fields["CUSTOM_API_KEY"] = self._password()
        self._set_secret_placeholder(
            self._fields["CUSTOM_API_KEY"],  # type: ignore[arg-type]
            "Stored in OS keychain",
            stored=False,
        )

        custom_card, custom_cv = self._card("Custom provider")
        custom_note = QLabel(
            f"<small>{t('Choose a known OpenAI-compatible endpoint, or select Custom endpoint to enter an address. Select Custom in a model row below to use it.')}</small>"
        )
        custom_note.setWordWrap(True)
        custom_cv.addWidget(custom_note)

        self._custom_endpoint_combo = _NoScrollCombo()
        self._custom_endpoint_combo.setObjectName("settingsCustomEndpointSelector")
        self._custom_endpoint_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._custom_endpoint_combo.setMinimumContentsLength(38)
        self._custom_endpoint_combo.view().setTextElideMode(Qt.TextElideMode.ElideMiddle)
        for name, url, _model_hint, _api_key_hint in self._CUSTOM_ENDPOINTS:
            display = f"{name} [{url}]"
            self._custom_endpoint_combo.addItem(display, url)
            self._custom_endpoint_combo.setItemData(
                self._custom_endpoint_combo.count() - 1,
                display,
                Qt.ItemDataRole.ToolTipRole,
            )
        self._custom_endpoint_combo.addItem(t("Custom endpoint"), "")
        self._custom_endpoint_combo.setItemData(
            self._custom_endpoint_combo.count() - 1,
            t("Enter any OpenAI-compatible API address."),
            Qt.ItemDataRole.ToolTipRole,
        )
        self._custom_endpoint_combo.currentIndexChanged.connect(
            self._custom_endpoint_changed
        )
        self._custom_base_url_label = QLabel(t("Custom address"))

        custom_f_w = QWidget()
        custom_f = _expanding_form_layout(custom_f_w)
        custom_f.setContentsMargins(0, 0, 0, 0)
        custom_f.setSpacing(8)
        custom_f.addRow(t("Endpoint"), self._custom_endpoint_combo)
        custom_f.addRow(self._custom_base_url_label, self._fields["CUSTOM_BASE_URL"])
        custom_f.addRow(t("API key"), self._fields["CUSTOM_API_KEY"])
        custom_cv.addWidget(custom_f_w)
        self._set_custom_url_field_visible(False)

        # Kept as hidden legacy controls so old CUSTOM_BASE_URL/CUSTOM_API_KEY
        # settings can be migrated into the first connection row. Custom
        # endpoints are now edited where they belong: inside each connection.
        custom_card.hide()
        credentials_layout.addWidget(custom_card)
        connections_outer.addWidget(credentials_group)
        connections_outer.addStretch()
        connections_scroll.setWidget(connections_w)
        self._connections_page = connections_scroll

        model_group, model_layout = self._area_group("Model routing")

        # ── MODEL SECTIONS ─────────────────────────────────────────────────
        section_configs = [
            ("LLM",        "Chat model",  "llm_test",      self._test_primary_llm_connection),
            ("VISION_LLM", "Image model", "vision_test",   self._test_vision_connection),
            ("MEMORY_LLM", "Memory model","memory_test",   self._test_memory_connection),
        ]

        route_switcher = QWidget()
        route_switcher_h = QHBoxLayout(route_switcher)
        route_switcher_h.setContentsMargins(0, 0, 0, 2)
        route_switcher_h.setSpacing(6)
        self._model_route_button_group = QButtonGroup(route_switcher)
        self._model_route_button_group.setExclusive(True)
        self._model_route_buttons: dict[str, QPushButton] = {}
        self._model_route_add_buttons: dict[str, QPushButton] = {}
        self._model_route_cards: dict[str, QWidget] = {}
        for section_key, section_title, _test_attr, _test_fn in section_configs:
            button = QPushButton(t(section_title))
            button.setObjectName("settingsSegmentButton")
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.clicked.connect(
                lambda _checked=False, sk=section_key: self._show_model_route(sk)
            )
            self._model_route_button_group.addButton(button)
            self._model_route_buttons[section_key] = button
            route_switcher_h.addWidget(button, 1)
        model_layout.addWidget(route_switcher)

        for section_key, section_title, test_attr, test_fn in section_configs:
            card, cv = self._card("")
            self._model_route_cards[section_key] = card

            # header: title (left) + "Apply to all" button (right)
            hdr_w = QWidget()
            hdr_h = QHBoxLayout(hdr_w)
            hdr_h.setContentsMargins(0, 0, 0, 4)
            hdr_h.setSpacing(0)
            title_lbl = _WarningHeaderLabel(section_title.upper())
            title_lbl.setObjectName("sectionHeader")
            self._register_warning_header(section_key, title_lbl, base_text=section_title, uppercase=True)
            apply_btn = QPushButton(t("Apply to all"))
            apply_btn.setToolTip(
                "Copy this section's provider/model rows into the other model sections."
            )
            apply_btn.clicked.connect(
                lambda checked, sk=section_key: self._apply_model_section_to_all(sk)
            )
            hdr_h.addWidget(title_lbl)
            hdr_h.addStretch()
            hdr_h.addWidget(apply_btn)
            cv.addWidget(hdr_w)

            # column headers
            mch_w = QWidget()
            mch_h = QHBoxLayout(mch_w)
            mch_h.setContentsMargins(0, 0, 0, 0)
            mch_h.setSpacing(8)
            priority_tip = t(
                "The first row is the primary model; lower rows are fallbacks tried in priority order."
            )
            lp = QLabel(f"<small><b>{t('Priority')}  ⓘ</b></small>")
            lp.setFixedWidth(_MODEL_PRIORITY_COL_W)
            lp.setObjectName("settingsInfoLabel")
            lp.setToolTip(priority_tip)
            lp.setCursor(Qt.CursorShape.WhatsThisCursor)
            lp.setAccessibleName(t("Priority"))
            lp.setAccessibleDescription(priority_tip)
            lp.setProperty("_openwand_info_source", "Priority")
            lp.setProperty("_openwand_info_small_bold", True)
            lp.setProperty(
                "_openwand_i18n_tooltip",
                "The first row is the primary model; lower rows are fallbacks tried in priority order.",
            )
            lk = QLabel(f"<small><b>{t('Provider')}</b></small>")
            lm = QLabel(f"<small><b>{t('Model')}</b></small>")
            mch_h.addWidget(lp)
            mch_h.addWidget(lk, 2)
            mch_h.addWidget(lm, 3)
            mch_h.addSpacing(32)
            cv.addWidget(mch_w)

            # rows container
            rows_container = _ModelRouteRowsWidget()
            rows_layout = QVBoxLayout(rows_container)
            rows_layout.setSpacing(4)
            rows_layout.setContentsMargins(0, 0, 0, 0)
            rows_container.rowDropped.connect(
                lambda row_widget, drop_index, sk=section_key: self._reorder_model_section_row(
                    sk, row_widget, drop_index
                )
            )
            cv.addWidget(rows_container)
            self._model_section_layouts[section_key] = rows_layout
            self._model_route_rows_containers[section_key] = rows_container

            # Test status + button. It is placed after Add fallback below so
            # the route-building actions read in order before route testing.
            test_lbl = QLabel()
            test_lbl.setWordWrap(True)
            setattr(self, f"_{test_attr}_status_lbl", test_lbl)
            test_row_w = QWidget()
            tr_h = QHBoxLayout(test_row_w)
            tr_h.setContentsMargins(0, 4, 0, 0)
            tr_h.setSpacing(8)
            test_btn = QPushButton(t(f"Test {section_title}"))
            test_btn.clicked.connect(test_fn)
            tr_h.addWidget(test_btn)
            tr_h.addWidget(test_lbl, 1)

            add_row_btn = QPushButton(t("+ Add fallback"))
            self._model_route_add_buttons[section_key] = add_row_btn
            arw = QHBoxLayout()
            arw.setContentsMargins(0, 0, 0, 0)
            arw.addWidget(add_row_btn)
            arw.addStretch()
            cv.addLayout(arw)
            cv.addWidget(test_row_w)
            add_row_btn.clicked.connect(
                lambda _checked=False, sk=section_key: self._add_inline_model_section_row(sk)
            )
            model_layout.addWidget(card)

        self._show_model_route("LLM")

        advanced_group, advanced_layout = self._collapsible_group("Advanced settings")
        chat_trace_tip = (
            "Show temporary progress lines in Chat that identify the tool loop and tool calls. "
            "Useful for testing; leave off for normal use."
        )
        self._fields["CHAT_TOOL_TRACE_UI"] = QCheckBox(t("Show chat tool-loop trace"))
        chat_trace_row = _checkbox_with_info(
            self._fields["CHAT_TOOL_TRACE_UI"], chat_trace_tip, "CHAT_TOOL_TRACE_UI"
        )
        planned_chunking_tip = (
            "Experimental. For longer text-only overlay questions, OpenWand first creates a private "
            "outline, then writes the answer as 2-4 ordered sections. The outline is never shown; "
            "each section appears as it is generated and the sections are joined into one final reply. "
            "This can make long answers more organized, but it uses one planning request plus one request "
            "per section, so it can be slower and use more model tokens. It only runs when the combined "
            "prompt and context reaches the minimum length. Requests using tools, files, images, or "
            "conversation history keep the normal reply path."
        )
        self._fields["OPENWAND_PLANNED_CHUNKING"] = QCheckBox(t("Use planned chunked replies"))
        planned_chunking_row = _checkbox_with_info(
            self._fields["OPENWAND_PLANNED_CHUNKING"],
            planned_chunking_tip,
            "OPENWAND_PLANNED_CHUNKING",
        )
        self._fields["OPENWAND_PLANNED_CHUNKING_CHUNKS"] = QLineEdit()
        self._fields["OPENWAND_PLANNED_CHUNKING_CHUNKS"].setPlaceholderText("e.g. 3")
        self._fields["OPENWAND_PLANNED_CHUNKING_CHUNKS"].setToolTip(
            t("How many ordered answer sections to generate, from 2 to 4. More sections use more model requests and may take longer.")
        )
        self._fields["OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS"] = QLineEdit()
        self._fields["OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS"].setPlaceholderText("e.g. 80")
        self._fields["OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS"].setToolTip(
            t("Minimum combined prompt and context length required to use planned replies. Shorter requests use the normal single reply path.")
        )
        reasoning_combo = _NoScrollCombo()
        reasoning_combo.setToolTip(
            t(
                "OpenAI Responses reasoning effort for chat. Provider default sends no explicit reasoning field; "
                "unsupported routes automatically retry without it."
            )
        )
        for label, value in _CHAT_REASONING_EFFORT_OPTIONS:
            reasoning_combo.addItem(t(label), value)
        self._fields["CHAT_REASONING_EFFORT"] = reasoning_combo
        auto_elaborate_tip = (
            "When enabled, opening Chat after a short overlay reply asks the model for a fuller explanation."
        )
        self._fields["CHAT_AUTO_ELABORATE"] = QCheckBox(t("Auto-elaborate when opening chat"))
        auto_elaborate_row = _checkbox_with_info(
            self._fields["CHAT_AUTO_ELABORATE"],
            auto_elaborate_tip,
            "CHAT_AUTO_ELABORATE",
        )
        self._fields["CHAT_ELABORATE_PROMPT"] = QLineEdit()
        self._fields["CHAT_ELABORATE_PROMPT"].setPlaceholderText(t("e.g. Please elaborate on that."))
        self._chat_elaborate_prompt_label = _tooltip_label(
            "Elaborate prompt",
            "Prompt used when Auto-elaborate asks the model to expand the latest short response.",
        )
        advanced_form_w = QWidget()
        advanced_form = _expanding_form_layout(advanced_form_w)
        advanced_form.setSpacing(8)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.addRow(
            _tooltip_label("Planned reply chunks", "How many ordered answer sections to generate, from 2 to 4. More sections use more model requests and may take longer."),
            self._fields["OPENWAND_PLANNED_CHUNKING_CHUNKS"],
        )
        advanced_form.addRow(
            _tooltip_label("Planned reply min chars", "Minimum combined prompt and context length required to use planned replies. Shorter requests use the normal single reply path."),
            self._fields["OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS"],
        )
        advanced_form.addRow(
            _tooltip_label(
                "Reasoning effort",
                "OpenAI Responses reasoning effort for chat. Unsupported models are retried without this field.",
            ),
            self._fields["CHAT_REASONING_EFFORT"],
        )
        advanced_form.addRow(self._chat_elaborate_prompt_label, self._fields["CHAT_ELABORATE_PROMPT"])
        # Keep optional switches together after the value fields so the form
        # reads top-to-bottom as configuration first, behavior toggles second.
        advanced_form.addRow("", chat_trace_row)
        advanced_form.addRow("", planned_chunking_row)
        advanced_form.addRow("", auto_elaborate_row)
        self._fields["CHAT_AUTO_ELABORATE"].toggled.connect(  # type: ignore[attr-defined]
            self._update_chat_elaborate_prompt_visibility
        )
        self._update_chat_elaborate_prompt_visibility()
        advanced_layout.addWidget(advanced_form_w)
        model_layout.addWidget(advanced_group)
        outer.addWidget(model_group)

        outer.addStretch()
        scroll.setWidget(w)
        return scroll

    # ---- Connection and API key row helpers ----

    def _add_inline_connection_row(self) -> None:
        """Insert a compact provider connection row without opening a dialog."""
        self._connections_expanded = True
        row = self._add_api_key_row()
        row["provider"].setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _toggle_connections_expanded(self) -> None:
        self._connections_expanded = not bool(getattr(self, "_connections_expanded", False))
        self._refresh_connection_rows_filter()

    def _refresh_connection_rows_filter(self, *_args) -> None:
        """Filter and paginate provider rows without changing the saved list."""
        search = getattr(self, "_connections_search", None)
        filter_combo = getattr(self, "_connections_filter", None)
        query = search.text().strip().lower() if isinstance(search, QLineEdit) else ""
        mode = str(filter_combo.currentData() or "all") if isinstance(filter_combo, QComboBox) else "all"
        expanded = bool(getattr(self, "_connections_expanded", False))
        matched = 0
        visible = 0
        local_providers = {"ollama", "custom"}
        for row in getattr(self, "_api_key_rows", []):
            provider = _get(row["provider"]).strip()
            alias = row["alias"].text().strip()
            label = t(_PROVIDER_LABELS.get(provider, provider))
            matches_text = not query or query in f"{provider} {label} {alias}".lower()
            matches_mode = (
                mode == "all"
                or (mode == "local" and provider in local_providers)
                or (mode == "cloud" and provider not in local_providers)
            )
            matches = matches_text and matches_mode
            show = matches and (expanded or bool(query) or matched < 6)
            row["widget"].setVisible(show)
            if matches:
                matched += 1
            if show:
                visible += 1

        empty = getattr(self, "_connections_empty_lbl", None)
        if isinstance(empty, QLabel):
            empty.setVisible(matched == 0)
        more = getattr(self, "_connections_show_more_btn", None)
        if isinstance(more, QPushButton):
            can_paginate = not query and matched > 6
            more.setVisible(can_paginate)
            more.setText(
                t("Show fewer")
                if expanded
                else t("Show {count} more").format(count=max(0, matched - visible))
            )

    def _add_api_key_row(
        self,
        provider: str = "",
        alias: str = "",
        stored: bool = False,
        *,
        connection_id: str = "",
        base_url: str = "",
    ) -> dict:
        """Add api key row."""
        row_w = QWidget()
        row_layout = QVBoxLayout(row_w)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(5)

        header_w = QWidget()
        h = QHBoxLayout(header_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        provider_combo = _NoScrollCombo()
        provider_combo.setObjectName("settingsConnectionProvider")
        provider_combo.addItem(t("Choose a provider"), "")
        for provider_id in _CONNECTION_PROVIDER_IDS:
            provider_combo.addItem(
                t(_PROVIDER_LABELS.get(provider_id, provider_id)),
                provider_id,
            )
        provider_index = provider_combo.findData(provider)
        provider_combo.setCurrentIndex(max(0, provider_index))
        provider_combo.setMinimumWidth(120)

        alias_edit = QLineEdit(alias)
        alias_edit.setPlaceholderText(t("alias (optional)"))
        alias_edit.setMinimumWidth(80)

        key_edit = self._password()

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(40)
        remove_btn.setStyleSheet("QPushButton { padding: 5px 4px; }")

        h.addWidget(provider_combo, 2)
        h.addWidget(alias_edit, 2)
        h.addWidget(key_edit, 3)
        h.addWidget(remove_btn)

        details_w = QWidget()
        details_w.setObjectName("settingsCustomConnectionDetails")
        details_form = _expanding_form_layout(details_w)
        details_form.setContentsMargins(28, 2, 40, 5)
        details_form.setSpacing(6)
        endpoint_combo = _NoScrollCombo()
        endpoint_combo.setObjectName("settingsCustomConnectionEndpoint")
        for name, url, _model_hint, _api_key_hint in self._CUSTOM_ENDPOINTS:
            display = f"{name} [{url}]"
            endpoint_combo.addItem(display, url)
            endpoint_combo.setItemData(
                endpoint_combo.count() - 1,
                display,
                Qt.ItemDataRole.ToolTipRole,
            )
        endpoint_combo.addItem(t("Custom endpoint"), "")
        custom_address = QLineEdit(base_url)
        custom_address.setObjectName("settingsCustomConnectionAddress")
        custom_address.setPlaceholderText("https://api.example.com/v1")
        custom_address_label = QLabel(t("Custom address"))
        details_form.addRow(t("Endpoint"), endpoint_combo)
        details_form.addRow(custom_address_label, custom_address)

        row_layout.addWidget(header_w)
        row_layout.addWidget(details_w)

        if provider == "custom" and not connection_id:
            connection_id = self._new_custom_connection_id()

        row_info: dict = {
            "widget":   row_w,
            "provider": provider_combo,
            "alias":    alias_edit,
            "key":      key_edit,
            "last_provider": _get(provider_combo).strip(),
            "connection_id": connection_id,
            "custom_details": details_w,
            "endpoint_combo": endpoint_combo,
            "custom_address": custom_address,
            "custom_address_label": custom_address_label,
        }
        self._sync_custom_connection_row(row_info)
        self._sync_api_key_row_placeholder(row_info, stored=stored)

        remove_btn.clicked.connect(lambda: self._remove_api_key_row(row_info))
        provider_combo.currentIndexChanged.connect(
            lambda _: self._on_api_key_row_provider_changed(row_info)
        )
        provider_combo.currentIndexChanged.connect(lambda _: self._sync_api_key_row_placeholder(row_info))
        provider_combo.currentIndexChanged.connect(lambda _: self._refresh_model_api_key_combos())
        provider_combo.currentIndexChanged.connect(self._refresh_connection_rows_filter)
        alias_edit.textChanged.connect(lambda _: self._refresh_model_api_key_combos())
        alias_edit.textChanged.connect(self._refresh_connection_rows_filter)
        endpoint_combo.currentIndexChanged.connect(
            lambda index: self._custom_connection_endpoint_changed(row_info, index)
        )
        custom_address.textChanged.connect(lambda _: self._refresh_model_api_key_combos())

        self._api_key_rows_layout.addWidget(row_w)
        self._api_key_rows.append(row_info)
        self._removed_connection_providers.discard(provider)
        if connection_id:
            self._removed_custom_connection_ids.discard(connection_id)
        self._refresh_model_api_key_combos()
        self._wire_change_tracking(row_w)
        self._schedule_search_index_refresh()
        self._refresh_connection_rows_filter()
        self._schedule_dirty_refresh()
        return row_info

    def _new_custom_connection_id(self) -> str:
        used = {
            str(row.get("connection_id") or "")
            for row in getattr(self, "_api_key_rows", [])
            if _get(row.get("provider")).strip() == "custom"
        }
        index = 1
        while f"custom-{index}" in used:
            index += 1
        return f"custom-{index}"

    def _sync_custom_connection_row(self, row_info: dict) -> None:
        """Show and initialize endpoint controls for one Custom connection."""
        is_custom = _get(row_info["provider"]).strip() == "custom"
        details = row_info["custom_details"]
        details.setVisible(is_custom)
        if not is_custom:
            return
        if not row_info.get("connection_id"):
            row_info["connection_id"] = self._new_custom_connection_id()
        combo = row_info["endpoint_combo"]
        address = row_info["custom_address"]
        base_url = address.text().strip()
        index = combo.findData(base_url) if base_url else combo.count() - 1
        if index < 0:
            index = combo.count() - 1
        combo.blockSignals(True)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        combo.setToolTip(combo.itemText(index))
        custom = not bool(combo.itemData(index))
        row_info["custom_address_label"].setVisible(custom)
        address.setVisible(custom)

    def _custom_connection_endpoint_changed(self, row_info: dict, index: int) -> None:
        combo = row_info["endpoint_combo"]
        address = row_info["custom_address"]
        url = str(combo.itemData(index) or "").strip()
        combo.setToolTip(combo.itemText(index))
        if url:
            address.setText(url)
            row_info["custom_address_label"].hide()
            address.hide()
        else:
            preset_urls = {endpoint[1] for endpoint in self._CUSTOM_ENDPOINTS}
            if address.text().strip() in preset_urls:
                address.clear()
            row_info["custom_address_label"].show()
            address.show()
            address.setFocus()
        self._refresh_model_api_key_combos()
        self._schedule_dirty_refresh()

    def _on_api_key_row_provider_changed(self, row_info: dict) -> None:
        """Stage removal of a provider replaced through a connection row."""

        previous = str(row_info.get("last_provider") or "").strip()
        provider = _get(row_info.get("provider")).strip()
        row_info["last_provider"] = provider
        if provider == "custom" and not row_info.get("connection_id"):
            row_info["connection_id"] = self._new_custom_connection_id()
        self._sync_custom_connection_row(row_info)
        if self._loading_values or previous == provider:
            return
        if previous == "custom" and row_info.get("connection_id"):
            self._removed_custom_connection_ids.add(str(row_info["connection_id"]))
        if previous and not any(
            row is not row_info and _get(row["provider"]).strip() == previous
            for row in self._api_key_rows
        ):
            self._removed_connection_providers.add(previous)
        if provider:
            self._removed_connection_providers.discard(provider)
        if provider == "custom" and row_info.get("connection_id"):
            self._removed_custom_connection_ids.discard(str(row_info["connection_id"]))

    def _sync_api_key_row_placeholder(self, row_info: dict, *, stored: bool = False) -> None:
        """Refresh provider-specific API key placeholder text."""
        provider = _get(row_info["provider"])
        key_edit = row_info["key"]
        if provider == "copilot":
            text = "stored in keychain" if stored else "github_pat_… (not saved to .env)"
        elif provider == "ollama":
            text = "not required"
            stored = False
        elif provider == "custom":
            text = "stored in keychain" if stored else "custom endpoint API key"
        else:
            text = "stored in keychain" if stored else "enter API key"
        self._set_secret_placeholder(key_edit, text, stored=stored)

    def _remove_api_key_row(self, row_info: dict) -> None:
        """Remove api key row."""
        provider = _get(row_info.get("provider")).strip()
        connection_id = str(row_info.get("connection_id") or "")
        if row_info in self._api_key_rows:
            self._api_key_rows.remove(row_info)
        if provider == "custom" and connection_id and not self._loading_values:
            self._removed_custom_connection_ids.add(connection_id)
        if (
            provider
            and not self._loading_values
            and not any(_get(row["provider"]).strip() == provider for row in self._api_key_rows)
        ):
            self._removed_connection_providers.add(provider)
        row_info["widget"].deleteLater()
        self._refresh_model_api_key_combos()
        self._refresh_connection_rows_filter()
        self._schedule_search_index_refresh()
        self._schedule_dirty_refresh()

    def _get_api_key_display_options(self) -> list[tuple[str, str]]:
        """Return api key display options."""
        options: list[tuple[str, str]] = []
        for row in getattr(self, "_api_key_rows", []):
            provider = _get(row["provider"])
            if not provider:
                # A newly inserted row is intentionally incomplete until the
                # user chooses a provider. Do not expose that draft row to the
                # model-routing selectors.
                continue
            alias = row["alias"].text().strip()
            label = t(_PROVIDER_LABELS.get(provider, provider))
            route_provider = provider
            if provider == "custom":
                route_provider = _custom_route_id(str(row.get("connection_id") or "legacy"))
                endpoint = row.get("custom_address")
                endpoint_text = endpoint.text().strip() if isinstance(endpoint, QLineEdit) else ""
                display = alias or endpoint_text or label
                display = f"{label} ({display})" if display != label else label
            else:
                display = f"{label} ({alias})" if alias else label
            options.append((display, route_provider))
        # Local/OAuth/keychain providers are always available regardless of API
        # key rows. In particular, Ollama must not require the user to create a
        # fake credential before it appears in Model routing.
        if not any(provider == "ollama" for _display, provider in options):
            options.append((t(_PROVIDER_LABELS["ollama"]), "ollama"))
        options.append((t(_PROVIDER_LABELS.get("chatgpt", "ChatGPT Plus/Pro (OAuth subscription)")), "chatgpt"))
        if not any(provider == "copilot" for _display, provider in options):
            options.append((t(_PROVIDER_LABELS.get("copilot", "GitHub Copilot") + " (keychain token)"), "copilot"))
        return options

    def _credential_availability(self) -> dict[str, tuple[bool, str]]:
        """Map OAuth/keychain providers to (available, hint-when-unavailable).

        Routes that authenticate via sign-in (chatgpt, copilot) are only usable
        once signed in. Copilot also accepts the GitHub OAuth login as a fallback
        token, so signing into GitHub enables it. Anything not listed is treated
        as always available. On any lookup error we fail open (available) so a
        keychain hiccup never hides a provider the user has configured.
        """
        avail: dict[str, tuple[bool, str]] = {}
        try:
            from core.auth import chatgpt as chatgpt_auth
            avail["chatgpt"] = (bool(chatgpt_auth.get_tokens()), t("sign in first"))
        except Exception:  # noqa: BLE001 — fail open
            avail["chatgpt"] = (True, "")
        try:
            from core.auth import copilot_auth
            avail["copilot"] = (
                copilot_auth.has_effective_token(),
                t("sign in to GitHub or add a Copilot token first"),
            )
        except Exception:  # noqa: BLE001 — fail open
            avail["copilot"] = (True, "")
        return avail

    def _fill_credential_combo(self, combo, current: str | None = None) -> None:
        """Populate a model-route credential combo, disabling un-signed-in routes.

        Unavailable entries (e.g. ChatGPT/Copilot when not signed in) stay in the
        list but are greyed out with a hint, so a route already pointed at one
        still displays its value while the inline route warning explains the fix.
        """
        options = self._get_api_key_display_options()
        avail = self._credential_availability()
        combo.blockSignals(True)
        combo.clear()
        for display, provider in options:
            enabled, hint = avail.get(provider, (True, ""))
            label = display if enabled else (f"{display} — {hint}" if hint else display)
            combo.addItem(label, provider)
            if not enabled:
                item = combo.model().item(combo.count() - 1)
                if item is not None:
                    item.setEnabled(False)
                    if hint:
                        item.setToolTip(hint)
        target = (current or "").strip()
        idx = combo.findData(target) if target else -1
        if target == "custom" and idx < 0:
            idx = combo.findData(_custom_route_id("legacy"))
        if target and idx < 0:
            # The configured route points at a provider with no saved credential
            # yet. Keep showing the configured provider without the obsolete
            # "add an API key below" hint: credentials now live on the separate
            # Connections page, and local providers may not need a key at all.
            provider_kind = "custom" if _is_custom_route(target) else target
            label = t(_PROVIDER_LABELS.get(provider_kind, target))
            combo.addItem(label, target)
            idx = combo.count() - 1
        elif not target:
            # No provider chosen (e.g. an Image/Memory model inheriting the Chat
            # model). Offer a neutral entry so the combo doesn't default onto a
            # disabled OAuth provider and raise a spurious credential warning.
            idx = combo.findData("")
            if idx < 0:
                combo.insertItem(0, t("Not set"), "")
                idx = 0
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _refresh_model_api_key_combos(self) -> None:
        """Refresh model api key combos."""
        for section_rows in self._model_section_rows.values():
            for row in section_rows:
                combo = row["api_key_combo"]
                self._fill_credential_combo(combo, combo.currentData())
        live_row = getattr(self, "_live_voice_model_row", None)
        if isinstance(live_row, dict):
            combo = live_row.get("api_key_combo")
            if isinstance(combo, QComboBox):
                self._fill_credential_combo(combo, combo.currentData())

    # ---- Model route catalog and row helpers ----

    def _show_model_route(self, section_key: str) -> None:
        """Show one purpose at a time so every route can grow independently."""
        cards = getattr(self, "_model_route_cards", {})
        for key, card in cards.items():
            card.setVisible(key == section_key)
        buttons = getattr(self, "_model_route_buttons", {})
        for key, button in buttons.items():
            button.setChecked(key == section_key)
        self._active_model_route = section_key

    def _add_inline_model_section_row(self, section_key: str) -> None:
        """Insert an editable route row without interrupting the settings flow."""
        row = self._add_model_section_row(section_key)
        row["api_key_combo"].setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _add_model_section_row(
        self,
        section_key: str,
        provider: str = "",
        model: str = "",
    ) -> dict:
        """Add model section row."""
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        priority_cell = QWidget()
        priority_cell.setFixedWidth(_MODEL_PRIORITY_COL_W)
        priority_h = QHBoxLayout(priority_cell)
        priority_h.setContentsMargins(0, 0, 0, 0)
        priority_h.setSpacing(2)
        drag_handle = _ModelRouteDragHandle(row_w)
        priority_lbl = QLabel()
        priority_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        priority_h.addWidget(drag_handle)
        priority_h.addWidget(priority_lbl, 1)

        api_key_combo = _NoScrollCombo()
        api_key_combo.setMinimumWidth(140)
        self._fill_credential_combo(api_key_combo, provider or None)

        # Model cell: a non-editable combo (curated list + a "Custom / enter
        # manually…" sentinel) stacked over a hidden line edit that appears only
        # when the sentinel is chosen.
        model_container = QWidget()
        mc_v = QVBoxLayout(model_container)
        mc_v.setContentsMargins(0, 0, 0, 0)
        mc_v.setSpacing(2)
        model_combo = _NoScrollCombo()
        model_combo.setMinimumWidth(140)
        model_edit = QLineEdit()
        model_edit.hide()
        mc_v.addWidget(model_combo)
        mc_v.addWidget(model_edit)

        refresh_btn = QPushButton("↻")
        refresh_btn.setFixedWidth(34)
        refresh_btn.setStyleSheet("QPushButton { padding: 5px 4px; }")
        refresh_btn.setToolTip("Fetch the latest model names from the provider")

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(40)
        remove_btn.setStyleSheet("QPushButton { padding: 5px 4px; }")

        h.addWidget(priority_cell)
        h.addWidget(api_key_combo, 2)
        h.addWidget(model_container, 3)
        h.addWidget(refresh_btn)
        h.addWidget(remove_btn)

        row_info: dict = {
            "widget":        row_w,
            "drag_handle":   drag_handle,
            "priority_lbl":  priority_lbl,
            "api_key_combo": api_key_combo,
            "model_combo":   model_combo,
            "model_edit":    model_edit,
            "refresh_btn":   refresh_btn,
        }

        # Populate with the curated list for this provider. Ollama has no
        # built-in guesses: while its live scan runs, preserve only a model the
        # user had already saved.
        # For a blank "+ Add row" the combo still defaults to the first provider,
        # so fall back to its current selection rather than leaving the list empty.
        effective_provider = provider or (api_key_combo.currentData() or "")
        provider_kind = "custom" if _is_custom_route(effective_provider) else effective_provider
        initial_models = list(_PROVIDER_MODELS.get(provider_kind, []))
        if provider_kind == "ollama" and (model or "").strip():
            initial_models = [(model or "").strip()]
        self._fill_model_combo(row_info, initial_models, provider_kind, model)

        model_combo.currentIndexChanged.connect(
            lambda _: self._on_model_combo_changed(row_info)
        )

        def _on_key_change():
            """Handle key change events."""
            p = api_key_combo.currentData() or ""
            provider_kind = "custom" if _is_custom_route(p) else p
            # A model from the previous provider is not an Ollama model. Leave
            # Ollama empty until its installed-model scan completes.
            selected = "" if p == "ollama" else self._model_value(row_info)
            row_info["_fetch_token"] = int(row_info.get("_fetch_token", 0)) + 1
            refresh_btn.setEnabled(True)
            refresh_btn.setText("↻")
            refresh_btn.setToolTip(t("Fetch the latest model names from the provider"))
            self._fill_model_combo(
                row_info, _PROVIDER_MODELS.get(provider_kind, []), provider_kind, selected
            )
            self._schedule_warning_marker_refresh()
            if p == "ollama":
                self._refresh_models_for_row(row_info, automatic=True)

        api_key_combo.currentIndexChanged.connect(lambda _: _on_key_change())
        model_combo.currentIndexChanged.connect(lambda _: self._schedule_warning_marker_refresh())
        model_edit.textChanged.connect(lambda _: self._schedule_warning_marker_refresh())
        refresh_btn.clicked.connect(lambda: self._refresh_models_for_row(row_info))
        remove_btn.clicked.connect(
            lambda: self._remove_model_section_row(section_key, row_info)
        )

        self._model_section_layouts[section_key].addWidget(row_w)
        self._model_section_rows[section_key].append(row_info)
        self._relabel_section_priorities(section_key)
        self._wire_change_tracking(row_w)
        self._schedule_search_index_refresh()
        self._schedule_dirty_refresh()
        if effective_provider == "ollama":
            # Let construction/loading finish before starting the local probe.
            QTimer.singleShot(
                0,
                lambda ri=row_info: self._refresh_models_for_row(ri, automatic=True),
            )
        return row_info

    def _reorder_model_section_row(
        self,
        section_key: str,
        row_widget: QWidget,
        drop_index: int,
    ) -> None:
        """Move a dropped row and keep its saved priority order in sync."""
        rows = self._model_section_rows.get(section_key, [])
        row_info = next((row for row in rows if row.get("widget") is row_widget), None)
        if row_info is None:
            return
        old_index = rows.index(row_info)
        target_index = max(0, min(int(drop_index), len(rows)))
        if target_index > old_index:
            target_index -= 1
        if target_index == old_index:
            return

        rows.pop(old_index)
        rows.insert(target_index, row_info)
        layout = self._model_section_layouts[section_key]
        layout.removeWidget(row_widget)
        layout.insertWidget(target_index, row_widget)
        self._relabel_section_priorities(section_key)
        self._schedule_dirty_refresh()

    def _relabel_section_priorities(self, section_key: str) -> None:
        """Number each model row by priority — row 1 is primary, the rest fallbacks."""
        rows = self._model_section_rows.get(section_key, [])
        for idx, row in enumerate(rows):
            lbl = row.get("priority_lbl")
            if lbl is None:
                continue
            rank = idx + 1
            if idx == 0:
                lbl.setText(f"<b>{rank}</b>")
                lbl.setToolTip(t("Priority {n} — primary model").format(n=rank))
            else:
                lbl.setText(str(rank))
                lbl.setToolTip(t("Priority {n} — fallback (tried if the rows above fail)").format(n=rank))

    def _fill_model_combo(
        self, row_info: dict, models: list, provider: str, selected: str
    ) -> None:
        """Repopulate a model combo, without manual/default entries for Ollama."""
        combo = row_info["model_combo"]
        edit = row_info["model_edit"]
        combo.blockSignals(True)
        combo.clear()
        for m in models:
            combo.addItem(m, m)
        allow_custom = provider != "ollama"
        if allow_custom:
            combo.addItem(_CUSTOM_MODEL_LABEL, _CUSTOM_MODEL_SENTINEL)
        selected = (selected or "").strip()
        if selected and selected in models:
            combo.setCurrentIndex(combo.findData(selected))
            edit.clear()
            edit.hide()
        elif selected and allow_custom:
            combo.setCurrentIndex(combo.findData(_CUSTOM_MODEL_SENTINEL))
            edit.setText(selected)
            edit.show()
        else:
            combo.setCurrentIndex(-1)
            edit.hide()
        combo.setPlaceholderText(
            t("Finding installed Ollama models…") if provider == "ollama" else ""
        )
        edit.setPlaceholderText(_model_hint(provider) if provider else "model name")
        completer = QCompleter(models, edit)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        edit.setCompleter(completer)
        combo.blockSignals(False)

    def _on_model_combo_changed(self, row_info: dict) -> None:
        """Handle model combo changed events."""
        combo = row_info["model_combo"]
        edit = row_info["model_edit"]
        if combo.currentData() == _CUSTOM_MODEL_SENTINEL:
            edit.show()
            edit.setFocus()
        else:
            edit.hide()

    def _model_value(self, row_info: dict) -> str:
        """Effective model string for a section row: the line-edit text when the
        Custom sentinel is selected, otherwise the chosen combo item."""
        combo = row_info["model_combo"]
        if combo.currentData() == _CUSTOM_MODEL_SENTINEL:
            return row_info["model_edit"].text().strip()
        return (combo.currentData() or "").strip()

    def _refresh_models_for_row(self, row_info: dict, *, automatic: bool = False) -> None:
        """Fetch live model names for the row's provider on a background thread."""
        if getattr(self, "_disposing", False):
            return
        try:
            provider = (row_info["api_key_combo"].currentData() or "").strip()
        except RuntimeError:
            # The row/dialog can be closed while a zero-delay automatic fetch is
            # still queued in Qt's event loop.
            return
        refresh_btn = row_info["refresh_btn"]
        if not provider:
            refresh_btn.setToolTip(t("Pick a provider first"))
            return
        if automatic and provider != "ollama":
            return
        api_key = self._effective_secret_value_from_provider(provider)
        base_url = self._custom_base_url_for_provider(provider) if _is_custom_route(provider) else ""

        # A quick provider change can leave an earlier request in flight. The
        # token prevents an old response from filling the new provider's combo.
        fetch_token = int(row_info.get("_fetch_token", 0)) + 1
        row_info["_fetch_token"] = fetch_token
        refresh_btn.setEnabled(False)
        refresh_btn.setText("…")
        refresh_btn.setToolTip(
            t("Finding installed Ollama models…")
            if provider == "ollama"
            else t("Fetching model names…")
        )

        carrier = _ModelFetchSignals()
        carrier.done.connect(
            lambda models, err, ri=row_info, p=provider, token=fetch_token: self._on_models_fetched(
                ri, models, err, provider=p, fetch_token=token
            )
        )
        row_info["_fetch_carrier"] = carrier  # keep alive until fetch completes

        def _worker():
            """Handle worker for settings dialog."""
            from core.llm_clients import client as llm
            models, error = llm.safe_list_models(provider, api_key=api_key, base_url=base_url)
            carrier.done.emit(models, error)

        threading.Thread(target=_worker, daemon=True, name="model-list-fetch").start()

    def _on_models_fetched(
        self,
        row_info: dict,
        models,
        err: str,
        *,
        provider: str | None = None,
        fetch_token: int | None = None,
    ) -> None:
        """Handle models fetched events."""
        if getattr(self, "_disposing", False):
            return
        requested_provider = (provider or "").strip()
        try:
            current_provider = (row_info["api_key_combo"].currentData() or "").strip()
        except RuntimeError:
            # Background results may arrive after the owning Qt row was removed.
            return
        if fetch_token is not None and fetch_token != row_info.get("_fetch_token"):
            return
        refresh_btn = row_info["refresh_btn"]
        if requested_provider and requested_provider != current_provider:
            # Some internal combo refreshes intentionally block change signals.
            # If that happened without starting a newer request, restore the
            # control while discarding this now-irrelevant result.
            refresh_btn.setEnabled(True)
            refresh_btn.setText("↻")
            refresh_btn.setToolTip(t("Fetch the latest model names from the provider"))
            row_info.pop("_fetch_carrier", None)
            return
        refresh_btn.setEnabled(True)
        refresh_btn.setText("↻")
        row_info.pop("_fetch_carrier", None)
        if err or not models:
            refresh_btn.setToolTip(
                t("Couldn't reach Ollama. Start Ollama, then try again. ({error})").format(error=err)
                if current_provider == "ollama" and err
                else t("Couldn't fetch — showing built-ins ({error})").format(error=err)
                if err
                else t("Provider returned no models — showing built-ins")
            )
            return
        # Live list only; preserve the row's current selection.
        self._fill_model_combo(
            row_info, list(models), current_provider, self._model_value(row_info)
        )
        refresh_btn.setToolTip(t("Live: {count} models").format(count=len(models)))

    def _remove_model_section_row(self, section_key: str, row_info: dict) -> None:
        """Remove model section row."""
        rows = self._model_section_rows[section_key]
        if row_info in rows:
            rows.remove(row_info)
        row_info["widget"].deleteLater()
        self._relabel_section_priorities(section_key)
        self._schedule_search_index_refresh()
        self._schedule_dirty_refresh()

    def _apply_model_section_to_all(self, source_key: str) -> None:
        """Apply model section to all."""
        source_rows = self._model_section_rows[source_key]
        for sk in list(self._model_section_rows):
            if sk == source_key:
                continue
            for row in list(self._model_section_rows[sk]):
                self._remove_model_section_row(sk, row)
            for row in source_rows:
                provider = row["api_key_combo"].currentData() or ""
                model = self._model_value(row)
                self._add_model_section_row(sk, provider, model)

    def _effective_secret_value_from_provider(self, provider: str) -> str:
        """Handle effective secret value from provider for settings dialog."""
        if _is_custom_route(provider):
            connection_id = _custom_connection_id(provider)
            for row in self._api_key_rows:
                if (
                    _get(row["provider"]) == "custom"
                    and str(row.get("connection_id") or "legacy") == connection_id
                ):
                    typed = row["key"].text().strip()
                    if typed:
                        return typed
                    break
            return secret_store.get_keychain_secret(_custom_secret_name(connection_id)) or ""
        if provider == "copilot":
            for row in self._api_key_rows:
                if _get(row["provider"]) == provider:
                    typed = row["key"].text().strip()
                    if typed:
                        return typed
            try:
                from core.auth import copilot_auth
                return copilot_auth.get_token() or ""
            except Exception:
                return ""
        key_name = _PROVIDER_KEY_NAMES.get(provider, "")
        if not key_name:
            return ""
        for row in self._api_key_rows:
            if _get(row["provider"]) == provider:
                typed = row["key"].text().strip()
                if typed:
                    return typed
        return secret_store.get_keychain_secret(key_name) or ""

    def _custom_base_url_for_provider(self, provider: str) -> str:
        connection_id = _custom_connection_id(provider)
        for row in self._api_key_rows:
            if (
                _get(row["provider"]) == "custom"
                and str(row.get("connection_id") or "legacy") == connection_id
            ):
                return row["custom_address"].text().strip()
        if connection_id == "legacy":
            return _get(self._fields.get("CUSTOM_BASE_URL")).strip()
        return ""

    # ---- Custom provider helpers ----

    _CUSTOM_ENDPOINTS: list[tuple[str, str, str, str]] = [
        ("DeepSeek",     "https://api.deepseek.com/v1",          "deepseek-v4-flash", ""),
        ("OpenRouter",   "https://openrouter.ai/api/v1",         "openai/gpt-4o", ""),
        ("Mistral",      "https://api.mistral.ai/v1",            "mistral-large-latest", ""),
        ("xAI (Grok)",   "https://api.x.ai/v1",                  "grok-4.5", ""),
        ("Together AI",  "https://api.together.xyz/v1",          "meta-llama/Llama-3-70b-chat-hf", ""),
        ("Cerebras",     "https://api.cerebras.ai/v1",           "llama-3.3-70b", ""),
        ("Z.AI / GLM",   "https://api.z.ai/api/paas/v4",         "glm-4.7-flash", ""),
        ("NVIDIA",       "https://integrate.api.nvidia.com/v1",  "meta/llama-3.3-70b-instruct", ""),
        ("SambaNova",    "https://api.sambanova.ai/v1",          "Meta-Llama-3.1-8B-Instruct", ""),
        ("GitHub Models", "https://models.github.ai/inference",  "openai/gpt-4.1-mini", ""),
        ("Hugging Face", "https://router.huggingface.co/v1",     "meta-llama/Llama-3.1-8B-Instruct", ""),
        ("Chutes",       "https://llm.chutes.ai/v1",             "deepseek-ai/DeepSeek-V3-0324", ""),
        ("Vercel AI Gateway", "https://ai-gateway.vercel.sh/v1", "openai/gpt-4o-mini", ""),
        ("Fireworks",    "https://api.fireworks.ai/inference/v1", "accounts/fireworks/models/llama-v3p1-8b-instruct", ""),
        ("Cohere",       "https://api.cohere.ai/compatibility/v1", "command-r-plus", ""),
        ("AI21",         "https://api.ai21.com/studio/v1",       "jamba-large", ""),
        ("Nebius",       "https://api.studio.nebius.com/v1",     "meta-llama/Meta-Llama-3.1-8B-Instruct", ""),
        ("Cloudflare Workers AI", "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1", "@cf/meta/llama-3.1-8b-instruct", ""),
        ("Baseten",      "https://model-{model_id}.api.baseten.co/environments/production/sync/v1", "model-name", ""),
        ("LM Studio (local)", "http://localhost:1234/v1",        "local-model", ""),
    ]

    def _set_custom_url_field_visible(self, visible: bool) -> None:
        """Show the address editor only for the Custom endpoint choice."""
        label = getattr(self, "_custom_base_url_label", None)
        field = self._fields.get("CUSTOM_BASE_URL")
        if isinstance(label, QLabel):
            label.setVisible(bool(visible))
        if isinstance(field, QLineEdit):
            field.setVisible(bool(visible))

    def _custom_endpoint_changed(self, index: int) -> None:
        """Apply a preset URL or reveal the custom-address editor."""
        combo = self._custom_endpoint_combo
        url = str(combo.itemData(index) or "").strip()
        combo.setToolTip(combo.itemText(index))
        if not url:
            current = self._fields["CUSTOM_BASE_URL"].text().strip()
            preset_urls = {endpoint[1] for endpoint in self._CUSTOM_ENDPOINTS}
            if current in preset_urls:
                self._fields["CUSTOM_BASE_URL"].clear()
            self._set_custom_url_field_visible(True)
            self._fields["CUSTOM_BASE_URL"].setFocus()
            return
        self._set_custom_url_field_visible(False)
        for _name, preset_url, model_hint, api_key_hint in self._CUSTOM_ENDPOINTS:
            if preset_url == url:
                self._apply_custom_preset(
                    preset_url,
                    model_hint,
                    api_key_hint,
                    sync_selector=False,
                )
                return

    def _sync_custom_endpoint_selection(self) -> None:
        """Match a loaded URL to a preset, otherwise select Custom endpoint."""
        combo = self._custom_endpoint_combo
        base_url = self._fields["CUSTOM_BASE_URL"].text().strip()
        index = combo.findData(base_url) if base_url else combo.count() - 1
        if index < 0:
            index = combo.count() - 1
        combo.blockSignals(True)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        combo.setToolTip(combo.itemText(index))
        self._set_custom_url_field_visible(not bool(combo.itemData(index)))
        if base_url:
            for _name, preset_url, model_hint, _api_key_hint in self._CUSTOM_ENDPOINTS:
                if preset_url == base_url:
                    self._set_custom_model_hint(model_hint)
                    break

    def _set_custom_model_hint(self, model_hint: str) -> None:
        """Apply a selected endpoint's example model to Custom model rows."""
        if not model_hint:
            return
        for section_rows in self._model_section_rows.values():
            for row in section_rows:
                if _is_custom_route(row["api_key_combo"].currentData() or ""):
                    row["model_edit"].setPlaceholderText(f"e.g. {model_hint}")

    def _apply_custom_preset(
        self,
        base_url: str,
        model_hint: str,
        api_key_hint: str = "",
        *,
        sync_selector: bool = True,
    ) -> None:
        """Apply custom preset."""
        self._fields["CUSTOM_BASE_URL"].setText(base_url)
        if sync_selector and hasattr(self, "_custom_endpoint_combo"):
            index = self._custom_endpoint_combo.findData(base_url)
            if index >= 0:
                self._custom_endpoint_combo.blockSignals(True)
                self._custom_endpoint_combo.setCurrentIndex(index)
                self._custom_endpoint_combo.blockSignals(False)
                self._custom_endpoint_combo.setToolTip(
                    self._custom_endpoint_combo.itemText(index)
                )
                self._set_custom_url_field_visible(False)
        if api_key_hint:
            self._fields["CUSTOM_API_KEY"].setText(api_key_hint)
        self._set_custom_model_hint(model_hint)

    def _refresh_chatgpt_status(self) -> None:
        """Refresh chatgpt status."""
        try:
            from core.auth import chatgpt as chatgpt_auth

            verified, _account = chatgpt_auth.validate_login(timeout_seconds=5.0)
            if verified:
                self._set_oauth_status("chatgpt", t("Logged in"), signed_in=True)
                self._chatgpt_status_lbl.setToolTip("")
            else:
                self._set_oauth_status("chatgpt", t("Not logged in"), signed_in=False)
                self._chatgpt_status_lbl.setToolTip("")
        except Exception as exc:
            self._set_oauth_status(
                "chatgpt",
                t("Status unavailable"),
                signed_in=None,
                message=_translate_status_message(f"Error reading status: {exc}"),
            )

    def _chatgpt_login_browser(self) -> None:
        """Handle chatgpt login browser for settings dialog."""
        from core.auth import chatgpt as chatgpt_auth
        self._set_oauth_status(
            "chatgpt",
            t("Opening browser… waiting for callback"),
            signed_in=None,
            busy=True,
        )
        self._start_auth_poll()

        def on_success(_tokens):
            """Handle success events."""
            # This callback runs only after this browser flow has persisted its
            # OpenWand credential. A shared Codex login may already exist, so
            # observing just any token is not proof that this flow completed.
            self._auth_poll_succeeded = True

        def on_error(msg):
            """Handle error events."""
            self._auth_poll_error = msg  # picked up by poll tick

        chatgpt_auth.start_browser_login(on_success, on_error)

    def _start_auth_poll(self) -> None:
        """Start a 1-second main-thread timer for this browser OAuth flow."""
        self._auth_poll_error: str | None = None
        self._auth_poll_succeeded = False
        self._auth_poll_ticks = 0
        self._auth_poll_timer = QTimer(self)
        self._auth_poll_timer.setInterval(1000)
        self._auth_poll_timer.timeout.connect(self._auth_poll_tick)
        self._auth_poll_timer.start()

    def _auth_poll_tick(self) -> None:
        # Check if the background thread stored a message
        """Handle auth poll tick for settings dialog."""
        if self._auth_poll_error is not None:
            msg = self._auth_poll_error
            self._auth_poll_error = None  # clear so we don't re-trigger
            self._auth_poll_timer.stop()
            self._set_oauth_status(
                "chatgpt",
                t("Status unavailable"),
                signed_in=None,
                message=_translate_status_message(f"Error: {msg}"),
            )
            return
        if self._auth_poll_succeeded:
            self._auth_poll_timer.stop()
            self._schedule_open_status_refresh()
            self._refresh_model_api_key_combos()
            return
        # Timeout after 5 minutes
        self._auth_poll_ticks += 1
        if self._auth_poll_ticks >= 300:
            self._auth_poll_timer.stop()
            self._set_oauth_status(
                "chatgpt",
                t("Not logged in"),
                signed_in=False,
                message=t("Timed out waiting for login"),
            )

    def _chatgpt_logout(self) -> None:
        """Handle chatgpt logout for settings dialog."""
        try:
            from core.auth import chatgpt as chatgpt_auth
            chatgpt_auth.clear_tokens()
        except Exception:
            pass
        self._refresh_chatgpt_status()
        self._refresh_model_api_key_combos()

    def _refresh_github_status(self) -> None:
        """Refresh github status."""
        try:
            from core.auth import github as github_auth

            tokens = github_auth.get_tokens() or {}
            verified, login = github_auth.validate_login(timeout_seconds=5.0)
            if verified:
                scopes = tokens.get("scope") or ""
                label = "Logged in" + (f" as {login}" if login else "")
                self._set_oauth_status(
                    "github",
                    _translate_status_message(label),
                    signed_in=True,
                )
                self._github_status_lbl.setToolTip(
                    t("Scopes: ") + scopes if scopes else ""
                )
            else:
                self._set_oauth_status("github", t("Not logged in"), signed_in=False)
                self._github_status_lbl.setToolTip("")
        except Exception as exc:
            self._set_oauth_status(
                "github",
                t("Status unavailable"),
                signed_in=None,
                message=_translate_status_message(f"Error reading status: {exc}"),
            )

    def _github_login_device(self) -> None:
        """Handle github login device for settings dialog."""
        import webbrowser

        import config as cfg
        from core.auth import github as github_auth

        override_client_id = _get(self._fields["GITHUB_CLIENT_ID"]).strip()
        cfg.GITHUB_CLIENT_ID = override_client_id or getattr(cfg, "GITHUB_DEFAULT_CLIENT_ID", "")
        cfg.GITHUB_OAUTH_SCOPES = _get(self._fields["GITHUB_OAUTH_SCOPES"]).strip()
        if not github_auth.has_configured_client_id():
            self._set_oauth_status(
                "github",
                t("Not logged in"),
                signed_in=False,
                message=t("This build does not include a GitHub OAuth app client ID yet."),
            )
            return

        self._set_oauth_status(
            "github",
            t("Starting GitHub device auth..."),
            signed_in=None,
            busy=True,
        )
        self._start_github_auth_poll()

        def on_code(url, user_code):
            """Handle code events."""
            self._github_auth_poll_message = f"__device_code__{url}\n{user_code}"
            try:
                webbrowser.open(url)
            except Exception:
                pass

        def on_success(_tokens):
            """Handle success events."""
            pass

        def on_error(msg):
            """Handle error events."""
            self._github_auth_poll_message = msg

        github_auth.start_device_login(on_code, on_success, on_error)

    def _start_github_auth_poll(self) -> None:
        """Start github auth poll."""
        self._github_auth_poll_message: str | None = None
        self._github_auth_poll_ticks = 0
        self._github_auth_poll_timer = QTimer(self)
        self._github_auth_poll_timer.setInterval(1000)
        self._github_auth_poll_timer.timeout.connect(self._github_auth_poll_tick)
        self._github_auth_poll_timer.start()

    def _github_auth_poll_tick(self) -> None:
        """Handle github auth poll tick for settings dialog."""
        if self._github_auth_poll_message is not None:
            msg = self._github_auth_poll_message
            self._github_auth_poll_message = None
            if msg.startswith("__device_code__"):
                body = msg[len("__device_code__"):]
                url, _, code = body.partition("\n")
                self._set_oauth_status(
                    "github",
                    t("Starting GitHub device auth..."),
                    signed_in=None,
                    message=f"{t('Go to:')} {url}   {t('Enter code:')} {code}",
                    message_kind="info",
                    busy=True,
                )
                return
            self._github_auth_poll_timer.stop()
            self._set_oauth_status(
                "github",
                t("Status unavailable"),
                signed_in=None,
                message=_translate_status_message(f"Error: {msg}"),
            )
            return
        try:
            from core.auth import github as github_auth
            if github_auth.get_tokens():
                self._github_auth_poll_timer.stop()
                self._schedule_open_status_refresh()
                self._refresh_model_api_key_combos()
                return
        except Exception:
            pass
        self._github_auth_poll_ticks += 1
        if self._github_auth_poll_ticks >= 900:
            self._github_auth_poll_timer.stop()
            self._set_oauth_status(
                "github",
                t("Not logged in"),
                signed_in=False,
                message=t("Timed out waiting for GitHub login"),
            )

    def _github_logout(self) -> None:
        """Handle github logout for settings dialog."""
        try:
            from core.auth import github as github_auth
            github_auth.clear_tokens()
        except Exception:
            pass
        self._refresh_github_status()
        self._refresh_model_api_key_combos()

    def _tab_tts(self) -> QWidget:
        """Handle tab TTS for settings dialog."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)
        self._voice_feature_cards: dict[str, QWidget] = {}

        # ── PROVIDER card ─────────────────────────────────────────────────
        # Provider selection and the selected provider's voice/authentication
        # fields belong to one feature, so keep them in a single card.
        provider_card, provider_cv = self._card("TTS")
        self._fields["TTS_PROVIDER"] = self._combo(
            ["cartesia", "elevenlabs", "openai", "openai_compatible", "gpt_sovits", "kokoro", "none"]
        )
        tts_provider_tip = "Choose which service speaks assistant replies. None disables generated voice output."
        self._fields["TTS_PROVIDER"].currentIndexChanged.connect(
            lambda *_: self._update_tts_provider_fields()
        )
        self._fields["TTS_SPEAK_REPLIES"] = QCheckBox(
            t("Read assistant replies aloud automatically")
        )
        tts_speak_replies_tip = (
            "When off, configured voices are still available for read-selection-aloud and Test TTS."
        )
        self._fields["TTS_SPEAK_REPLIES"].setToolTip(t(tts_speak_replies_tip))
        pf_w = QWidget()
        pf = _expanding_form_layout(pf_w)
        pf.setContentsMargins(0, 0, 0, 0)
        pf.setSpacing(8)
        pf.addRow(_tooltip_label("Provider", tts_provider_tip), self._fields["TTS_PROVIDER"])
        pf.addRow(self._fields["TTS_SPEAK_REPLIES"])
        self._tts_timing_notice_lbl = QLabel(t(_TTS_TIMING_NOTICE))
        self._tts_timing_notice_lbl.setObjectName("ttsTimingNotice")
        self._tts_timing_notice_lbl.setWordWrap(True)
        self._tts_timing_notice_lbl.setStyleSheet("color: #d8932a;")
        pf.addRow("", self._tts_timing_notice_lbl)
        provider_cv.addWidget(pf_w)
        outer.addWidget(provider_card)
        self._voice_feature_cards["tts"] = provider_card

        # ── SPEECH-TO-TEXT card ──────────────────────────────────────────
        stt_card, stt_cv = self._card("Speech to Text")
        stt_note_text = (
            "Larger models improve Mandarin/Cantonese speech accuracy but use more disk and CPU."
        )
        stt_note = QLabel(
            f"<small>{t(stt_note_text)}</small>"
        )
        stt_note.setWordWrap(True)
        stt_cv.addWidget(stt_note)

        stt_first_use = QLabel(
            "<small>"
            + t(
                "First use downloads the model (about 150 MB for base) and needs "
                "internet once. Use the button below to download it ahead of time."
            )
            + "</small>"
        )
        stt_first_use.setWordWrap(True)
        stt_cv.addWidget(stt_first_use)

        stt_provider = _NoScrollCombo()
        stt_provider.addItem(t("None"), "none")
        stt_provider.addItem(t("Local — faster-whisper"), "local")
        stt_provider.addItem(t("Cloudflare — Whisper Large v3 Turbo"), "cloudflare")
        stt_provider.setCurrentIndex(stt_provider.findData("local"))
        stt_provider_tip = (
            "Disabled does not record or transcribe speech. Local stays on this computer. "
            "Cloudflare uploads captured speech audio to Workers AI."
        )
        self._fields["STT_PROVIDER"] = stt_provider

        stt_model = _NoScrollCombo()
        stt_model.setProperty("allow_custom_saved_value", True)
        for label, model, translation_key in _STT_MODEL_OPTIONS:
            stt_model.addItem(label, model)
            stt_model.setItemData(stt_model.count() - 1, translation_key, COMBO_I18N_SOURCE_ROLE)
        stt_model_tip = (
            "Local faster-whisper model. small is a good first upgrade for Chinese; "
            "medium/large-v3 are heavier."
        )
        self._fields["STT_MODEL"] = stt_model

        stt_compute = _NoScrollCombo()
        for label, value in _STT_COMPUTE_OPTIONS:
            stt_compute.addItem(label, value)
        stt_compute_tip = "Whisper compute precision. Keep int8 for CPU unless you know you need another mode."
        self._fields["STT_COMPUTE_TYPE"] = stt_compute

        stt_language = _NoScrollCombo()
        for label, value in _STT_LANGUAGE_OPTIONS:
            stt_language.addItem(label, value)
        stt_language_tip = (
            "Recognition language for hold-to-talk. Auto-detect is useful if you switch languages often."
        )
        self._fields["STT_LANGUAGE"] = stt_language
        # Cantonese (yue) only exists in large-v3; rebuild the language list
        # whenever the model changes so it can't be paired with a model that
        # can't decode it.
        stt_model.currentIndexChanged.connect(lambda *_: self._rebuild_stt_languages())

        stt_beam = _NoScrollCombo()
        for label, value in _STT_BEAM_OPTIONS:
            stt_beam.addItem(label, value)
        stt_beam_tip = (
            "Decoding beam width. 5 (Whisper's default) is noticeably more accurate than greedy (1) "
            "for a small speed cost; raise it for tricky audio."
        )
        self._fields["STT_BEAM_SIZE"] = stt_beam

        stt_device = _NoScrollCombo()
        for label, value in _local_speech_device_options():
            stt_device.addItem(label, value)
        stt_device_tip = (
            "Where Whisper runs. macOS uses CPU for local STT in this release."
            if sys.platform == "darwin"
            else (
                "Where Whisper runs. GPU (CUDA) is much faster, especially for large-v3, but needs an "
                "NVIDIA GPU. Auto installs CPU and GPU runtime support, uses the GPU when present, and "
                "falls back to CPU."
            )
        )
        self._fields["STT_DEVICE"] = stt_device

        cloudflare_account = QLineEdit()
        cloudflare_account.setPlaceholderText(t("Cloudflare Account ID"))
        cloudflare_account.setToolTip(
            t("Find this in the Cloudflare Workers AI dashboard under Use REST API.")
        )
        self._fields["STT_CLOUDFLARE_ACCOUNT_ID"] = cloudflare_account
        cloudflare_token = self._password()
        cloudflare_token.setToolTip(
            t("Use a dedicated token with Workers AI Read and Edit permissions.")
        )
        self._fields["CLOUDFLARE_API_TOKEN"] = cloudflare_token
        self._set_secret_placeholder(cloudflare_token, "not configured", stored=False)
        cloudflare_fallback = QCheckBox(t("Use local Whisper if Cloudflare is unavailable"))
        cloudflare_fallback.setToolTip(
            t("Falls back only when local STT is installed; otherwise the Cloudflare error is shown.")
        )
        self._fields["STT_CLOUDFLARE_FALLBACK_LOCAL"] = cloudflare_fallback
        cloudflare_note = QLabel(
            "<small>"
            + t(
                "Cloudflare mode sends each speech clip to Workers AI after OpenWand's local silence check. "
                "It needs internet access and does not provide live streaming transcription."
            )
            + "</small>"
        )
        cloudflare_note.setWordWrap(True)

        stt_fw = QWidget()
        stt_f = _expanding_form_layout(stt_fw)
        stt_f.setContentsMargins(0, 0, 0, 0)
        stt_f.setSpacing(8)
        stt_f.addRow(_tooltip_label("Provider", stt_provider_tip), self._fields["STT_PROVIDER"])
        stt_model_label = _tooltip_label("Whisper model", stt_model_tip)
        stt_device_label = _tooltip_label("Device", stt_device_tip)
        stt_compute_label = _tooltip_label("Compute type", stt_compute_tip)
        stt_language_label = _tooltip_label("Speech language", stt_language_tip)
        stt_beam_label = _tooltip_label("Beam size", stt_beam_tip)
        cloudflare_account_label = _tooltip_label(
            "Cloudflare Account ID",
            "Account identifier from the Workers AI dashboard; this is not your email or zone ID.",
        )
        cloudflare_token_label = _tooltip_label(
            "Workers AI API token",
            "Stored in your operating system keychain and never written to the profile file.",
        )
        stt_f.addRow(stt_model_label, self._fields["STT_MODEL"])
        stt_f.addRow(stt_device_label, self._fields["STT_DEVICE"])
        stt_f.addRow(stt_compute_label, self._fields["STT_COMPUTE_TYPE"])
        stt_f.addRow(stt_language_label, self._fields["STT_LANGUAGE"])
        stt_f.addRow(stt_beam_label, self._fields["STT_BEAM_SIZE"])
        stt_f.addRow(cloudflare_account_label, self._fields["STT_CLOUDFLARE_ACCOUNT_ID"])
        stt_f.addRow(cloudflare_token_label, self._fields["CLOUDFLARE_API_TOKEN"])
        stt_f.addRow("", self._fields["STT_CLOUDFLARE_FALLBACK_LOCAL"])
        stt_f.addRow("", cloudflare_note)
        stt_cv.addWidget(stt_fw)
        self._stt_local_widgets = [
            stt_note,
            stt_first_use,
            stt_model_label,
            self._fields["STT_MODEL"],
            stt_device_label,
            self._fields["STT_DEVICE"],
            stt_compute_label,
            self._fields["STT_COMPUTE_TYPE"],
        ]
        self._stt_cloud_widgets = [
            cloudflare_account_label,
            self._fields["STT_CLOUDFLARE_ACCOUNT_ID"],
            cloudflare_token_label,
            self._fields["CLOUDFLARE_API_TOKEN"],
            self._fields["STT_CLOUDFLARE_FALLBACK_LOCAL"],
            cloudflare_note,
        ]
        for widget in self._stt_cloud_widgets:
            widget.setVisible(False)
        stt_provider.currentIndexChanged.connect(lambda *_: self._update_stt_provider_fields())

        stt_hint = QLabel(
            "<small>"
            + t(
                "Hold the voice hotkey while you speak, then release. Hold for at "
                "least half a second and speak clearly; very short or silent taps "
                "are skipped."
            )
            + "</small>"
        )
        stt_hint.setWordWrap(True)
        stt_cv.addWidget(stt_hint)
        self._stt_enabled_widgets = [
            stt_language_label,
            self._fields["STT_LANGUAGE"],
            stt_beam_label,
            self._fields["STT_BEAM_SIZE"],
            stt_hint,
        ]

        # Backend readout. Avoid importing core.stt while building Settings:
        # that pulls in NumPy/faster-whisper and can freeze the Qt UI thread.
        # If STT is already loaded in this process, the label below can still
        # show the live backend; otherwise it shows the configured request.
        self._stt_active_lbl = QLabel()
        self._stt_active_lbl.setWordWrap(True)
        self._stt_install_status_lbl = self._stt_active_lbl
        self._stt_download_btn = QPushButton(t("Install STT"))
        self._stt_download_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._stt_download_btn.setToolTip(
            t(
                "Install or repair faster-whisper, then download and load the speech "
                "model so the first hold-to-talk does not stall. The first download "
                "needs an internet connection."
            )
        )
        self._stt_download_btn.clicked.connect(self._preload_stt_model)
        stt_cv.addWidget(self._status_action_row(
            self._stt_active_lbl,
            self._stt_download_btn,
        ))
        # Resolving the active backend scans every installed distribution, which
        # costs a quarter second of disk I/O. Leave the label blank during page
        # construction: opening this page runs _refresh_current_install_status,
        # which fills it before the page paints. Probing here would block the
        # Settings window from appearing at all.
        outer.addWidget(stt_card)
        self._voice_feature_cards["stt"] = stt_card

        # ── PROVIDER SETTINGS card ────────────────────────────────────────
        # Only the rows for the selected provider are shown (toggled by
        # _update_tts_provider_fields), so each provider gets the space it needs
        # for its key / voice / model without cluttering the others.
        provider_details = QWidget()
        keys_cv = QVBoxLayout(provider_details)
        keys_cv.setContentsMargins(0, 0, 0, 0)
        keys_cv.setSpacing(10)
        tts_key_note = QLabel(
            f"<small>{t('API keys are saved to the OS keychain. Leave blank to keep the stored key.')}</small>"
        )
        tts_key_note.setWordWrap(True)
        keys_cv.addWidget(tts_key_note)

        # Fields shared/created up front so save + load can always reach them.
        self._fields["CARTESIA_API_KEY"] = self._password()
        self._set_secret_placeholder(
            self._fields["CARTESIA_API_KEY"],  # type: ignore[arg-type]
            "Stored in OS keychain",
            stored=False,
        )
        self._fields["CARTESIA_VOICE_ID"] = QLineEdit()
        self._fields["CARTESIA_VOICE_ID"].setPlaceholderText("e.g. a0e99841-438c-4a64-b679-ae501e7d6091")
        cartesia_voice_tip = "The Cartesia voice identifier to use for speech. Copy it from your Cartesia voices page."
        self._fields["ELEVENLABS_API_KEY"] = self._password()
        self._set_secret_placeholder(
            self._fields["ELEVENLABS_API_KEY"],  # type: ignore[arg-type]
            "Stored in OS keychain",
            stored=False,
        )
        self._fields["ELEVENLABS_VOICE_ID"] = QLineEdit()
        self._fields["ELEVENLABS_VOICE_ID"].setPlaceholderText(t("blank = account default voice"))
        eleven_voice_tip = "Leave blank for the account default voice, or paste a specific ElevenLabs voice ID."
        self._fields["ELEVENLABS_MODEL"] = QLineEdit()
        self._fields["ELEVENLABS_MODEL"].setPlaceholderText("e.g. eleven_turbo_v2_5")
        eleven_model_tip = "ElevenLabs speech model name. Use the provider default unless you need a specific model."
        self._fields["OPENAI_TTS_VOICE"] = QLineEdit()
        self._fields["OPENAI_TTS_VOICE"].setPlaceholderText("alloy, echo, fable, onyx, nova, shimmer…")
        openai_voice_tip = "OpenAI voice name for spoken replies."
        self._fields["OPENAI_TTS_MODEL"] = QLineEdit()
        self._fields["OPENAI_TTS_MODEL"].setPlaceholderText("e.g. gpt-4o-mini-tts or tts-1")
        openai_model_tip = "OpenAI text-to-speech model. Newer models sound better; tts-1 is a fast fallback."
        self._fields["TTS_CUSTOM_BASE_URL"] = QLineEdit()
        self._fields["TTS_CUSTOM_BASE_URL"].setPlaceholderText("e.g. http://localhost:8880/v1")
        custom_tts_base_tip = "Base URL for an OpenAI-compatible speech server, ending at the API root such as /v1."
        self._fields["TTS_CUSTOM_API_KEY"] = self._password()
        self._set_secret_placeholder(
            self._fields["TTS_CUSTOM_API_KEY"],  # type: ignore[arg-type]
            "Stored in OS keychain (blank if not needed)",
            stored=False,
        )
        self._fields["TTS_CUSTOM_VOICE"] = QLineEdit()
        self._fields["TTS_CUSTOM_VOICE"].setPlaceholderText(t("server-specific voice name"))
        custom_tts_voice_tip = "Voice name or ID expected by your custom speech server."
        self._fields["TTS_CUSTOM_MODEL"] = QLineEdit()
        self._fields["TTS_CUSTOM_MODEL"].setPlaceholderText(t("server-specific model name"))
        custom_tts_model_tip = "Model name expected by your custom speech server."
        self._fields["TTS_CUSTOM_SAMPLE_RATE"] = QLineEdit()
        self._fields["TTS_CUSTOM_SAMPLE_RATE"].setPlaceholderText("e.g. 24000")
        custom_tts_rate_tip = "Output sample rate in Hz. Match the rate your speech server returns, commonly 24000."
        self._fields["GPT_SOVITS_URL"] = QLineEdit()
        self._fields["GPT_SOVITS_URL"].setPlaceholderText("http://127.0.0.1:9880")
        gsv_url_tip = "Local GPT-SoVITS API URL. Start api_v2.py in GPT-SoVITS, then use its host and port here."
        self._fields["GPT_SOVITS_REF_AUDIO_PATH"] = QLineEdit()
        self._fields["GPT_SOVITS_REF_AUDIO_PATH"].setPlaceholderText(r"C:\voices\ref.wav")
        gsv_ref_tip = "Path to a clean reference WAV on the machine running GPT-SoVITS."
        self._fields["GPT_SOVITS_PROMPT_TEXT"] = QLineEdit()
        self._fields["GPT_SOVITS_PROMPT_TEXT"].setPlaceholderText(
            t("Exact words spoken in the reference audio")
        )
        gsv_prompt_tip = "Exact transcript of the reference audio. Leave blank only if the GPT-SoVITS model can infer without it."
        self._fields["GPT_SOVITS_PROMPT_LANG"] = QLineEdit()
        self._fields["GPT_SOVITS_PROMPT_LANG"].setPlaceholderText("en")
        gsv_prompt_lang_tip = "Language code for the reference audio transcript, such as en, zh, ja, ko, or yue."
        self._fields["GPT_SOVITS_TEXT_LANG"] = QLineEdit()
        self._fields["GPT_SOVITS_TEXT_LANG"].setPlaceholderText("en")
        gsv_text_lang_tip = "Language code for assistant replies sent to GPT-SoVITS."
        self._fields["GPT_SOVITS_SAMPLE_RATE"] = QLineEdit()
        self._fields["GPT_SOVITS_SAMPLE_RATE"].setPlaceholderText("32000")
        gsv_rate_tip = "Playback sample rate OpenWand should use. The adapter resamples GPT-SoVITS output to this rate."
        self._fields["KOKORO_VOICE"] = QLineEdit()
        self._fields["KOKORO_VOICE"].setPlaceholderText("af_heart")
        kokoro_voice_tip = "Built-in Kokoro voice name, such as af_heart, af_bella, af_sky, am_adam, or am_michael."
        self._fields["KOKORO_LANG_CODE"] = QLineEdit()
        self._fields["KOKORO_LANG_CODE"].setPlaceholderText("a")
        kokoro_lang_tip = "Kokoro language code. Use a for American English, b for British English, e for Spanish, f for French."
        kokoro_device = _NoScrollCombo()
        for label, value in _local_speech_device_options():
            kokoro_device.addItem(label, value)
        kokoro_device_tip = (
            "Where local Kokoro TTS runs. macOS uses CPU for Kokoro in this release."
            if sys.platform == "darwin"
            else (
                "Where local Kokoro TTS runs. Auto installs CUDA-enabled Torch, which supports both GPU "
                "and CPU execution, then uses CUDA when available and falls back to CPU."
            )
        )
        self._fields["KOKORO_DEVICE"] = kokoro_device
        kokoro_device.currentIndexChanged.connect(lambda _idx: self._handle_kokoro_device_changed())
        self._fields["KOKORO_SPEED"] = QLineEdit()
        self._fields["KOKORO_SPEED"].setPlaceholderText("1.0")
        kokoro_speed_tip = "Speech speed multiplier. 1.0 is normal."
        self._fields["KOKORO_SAMPLE_RATE"] = QLineEdit()
        self._fields["KOKORO_SAMPLE_RATE"].setPlaceholderText("24000")
        kokoro_rate_tip = "Kokoro's normal output is 24000 Hz. Keep this unless you have a reason to resample."
        self._fields["TTS_VOLUME"] = QSlider(Qt.Orientation.Horizontal)
        self._fields["TTS_VOLUME"].setRange(0, 150)
        self._fields["TTS_VOLUME"].setSingleStep(5)
        self._fields["TTS_VOLUME"].setPageStep(10)
        self._fields["TTS_VOLUME"].setTickPosition(QSlider.TickPosition.TicksBelow)
        self._fields["TTS_VOLUME"].setTickInterval(25)
        tts_volume_tip = "Playback volume for generated speech. 100% is normal."
        self._tts_volume_value_lbl = QLabel("100%")
        self._tts_volume_value_lbl.setMinimumWidth(44)

        def _update_volume_label(value: int) -> None:
            """Update the visible TTS volume percentage."""
            self._tts_volume_value_lbl.setText(f"{int(value)}%")

        self._fields["TTS_VOLUME"].valueChanged.connect(_update_volume_label)
        self._fields["TTS_READ_ALOUD_MIN_WORDS"] = QLineEdit()
        self._fields["TTS_READ_ALOUD_MIN_WORDS"].setPlaceholderText("50")
        tts_chunk_min_tip = "Minimum words before read-aloud TTS may split at punctuation."
        self._fields["TTS_READ_ALOUD_MAX_WORDS"] = QLineEdit()
        self._fields["TTS_READ_ALOUD_MAX_WORDS"].setPlaceholderText("110")
        tts_chunk_max_tip = "Maximum words per read-aloud TTS chunk before OpenWand splits anyway."
        self._fields["STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS"] = QLineEdit()
        self._fields["STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS"].setPlaceholderText("15.0")
        stt_first_chunk_tip = "Recording length before background transcription starts."
        self._fields["STT_BACKGROUND_CHUNK_STEP_SECONDS"] = QLineEdit()
        self._fields["STT_BACKGROUND_CHUNK_STEP_SECONDS"].setPlaceholderText("10.0")
        stt_chunk_step_tip = "Seconds between background STT chunks after the first one."
        self._fields["STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS"] = QLineEdit()
        self._fields["STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS"].setPlaceholderText("4.5")
        stt_live_delay_tip = "How far background STT stays behind the live microphone edge."
        self._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"] = QLineEdit()
        self._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"].setPlaceholderText("1.0")
        stt_overlap_tip = "Audio overlap between STT chunks, used to avoid losing boundary words."

        # Cartesia group
        cartesia_w = QWidget()
        cf = _expanding_form_layout(cartesia_w)
        cf.setContentsMargins(0, 0, 0, 0)
        cf.setSpacing(8)
        cf.addRow(_link_label("Cartesia API key", "https://play.cartesia.ai/keys"), self._fields["CARTESIA_API_KEY"])
        cf.addRow(_tooltip_label("Cartesia Voice ID", cartesia_voice_tip), self._fields["CARTESIA_VOICE_ID"])

        # ElevenLabs group
        eleven_w = QWidget()
        ef = _expanding_form_layout(eleven_w)
        ef.setContentsMargins(0, 0, 0, 0)
        ef.setSpacing(8)
        eleven_note = QLabel(
            f"<small>{t('ElevenLabs is downloaded only when you install it, keeping release downloads smaller. Source checkouts may use the SDK from their Python environment.')}</small>"
        )
        eleven_note.setWordWrap(True)
        self._elevenlabs_install_btn = QPushButton(t("Install ElevenLabs"))
        self._elevenlabs_install_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._elevenlabs_install_btn.clicked.connect(self._install_elevenlabs)
        self._elevenlabs_install_status_lbl = QLabel()
        self._elevenlabs_install_status_lbl.setWordWrap(True)
        ef.addRow(eleven_note)
        ef.addRow(self._status_action_row(
            self._elevenlabs_install_status_lbl,
            self._elevenlabs_install_btn,
        ))
        ef.addRow(_link_label("ElevenLabs API key", "https://elevenlabs.io/app/settings/api-keys"), self._fields["ELEVENLABS_API_KEY"])
        ef.addRow(_tooltip_label("ElevenLabs Voice ID", eleven_voice_tip), self._fields["ELEVENLABS_VOICE_ID"])
        ef.addRow(_tooltip_label("ElevenLabs Model", eleven_model_tip), self._fields["ELEVENLABS_MODEL"])

        # OpenAI group (reuses the OpenAI key from Connections)
        openai_w = QWidget()
        of = _expanding_form_layout(openai_w)
        of.setContentsMargins(0, 0, 0, 0)
        of.setSpacing(8)
        openai_note = QLabel(
            f"<small>{t('Uses your OpenAI API key from Connections.')}</small>"
        )
        openai_note.setWordWrap(True)
        of.addRow(openai_note)
        of.addRow(_tooltip_label("OpenAI Voice", openai_voice_tip), self._fields["OPENAI_TTS_VOICE"])
        of.addRow(_tooltip_label("OpenAI Model", openai_model_tip), self._fields["OPENAI_TTS_MODEL"])

        # OpenAI-compatible (custom endpoint) group
        custom_w = QWidget()
        cuf = _expanding_form_layout(custom_w)
        cuf.setContentsMargins(0, 0, 0, 0)
        cuf.setSpacing(8)
        custom_note = QLabel(
            f"<small>{t('Any server with an OpenAI-style /audio/speech endpoint that can return PCM (self-hosted Kokoro/LocalAI, Groq, …).')}</small>"
        )
        custom_note.setWordWrap(True)
        cuf.addRow(custom_note)
        cuf.addRow(_tooltip_label("Base URL", custom_tts_base_tip), self._fields["TTS_CUSTOM_BASE_URL"])
        cuf.addRow(t("API key"), self._fields["TTS_CUSTOM_API_KEY"])
        cuf.addRow(_tooltip_label("Voice", custom_tts_voice_tip), self._fields["TTS_CUSTOM_VOICE"])
        cuf.addRow(_tooltip_label("Model", custom_tts_model_tip), self._fields["TTS_CUSTOM_MODEL"])
        cuf.addRow(
            _tooltip_label("Output sample rate (Hz)", custom_tts_rate_tip),
            self._fields["TTS_CUSTOM_SAMPLE_RATE"],
        )

        # GPT-SoVITS local API group
        gsv_w = QWidget()
        gsvf = _expanding_form_layout(gsv_w)
        gsvf.setContentsMargins(0, 0, 0, 0)
        gsvf.setSpacing(8)
        gsv_note = QLabel(
            f"<small>{t('Runs through a local GPT-SoVITS api_v2.py server. No cloud key is needed.')}</small>"
        )
        gsv_note.setWordWrap(True)
        gsvf.addRow(gsv_note)
        gsvf.addRow(_tooltip_label("API URL", gsv_url_tip), self._fields["GPT_SOVITS_URL"])
        gsvf.addRow(_tooltip_label("Reference audio", gsv_ref_tip), self._fields["GPT_SOVITS_REF_AUDIO_PATH"])
        gsvf.addRow(_tooltip_label("Reference transcript", gsv_prompt_tip), self._fields["GPT_SOVITS_PROMPT_TEXT"])
        gsvf.addRow(_tooltip_label("Reference language", gsv_prompt_lang_tip), self._fields["GPT_SOVITS_PROMPT_LANG"])
        gsvf.addRow(_tooltip_label("Reply language", gsv_text_lang_tip), self._fields["GPT_SOVITS_TEXT_LANG"])
        gsvf.addRow(_tooltip_label("Playback sample rate (Hz)", gsv_rate_tip), self._fields["GPT_SOVITS_SAMPLE_RATE"])

        # Kokoro local library group
        kokoro_w = QWidget()
        kf = _expanding_form_layout(kokoro_w)
        kf.setContentsMargins(0, 0, 0, 0)
        kf.setSpacing(8)
        kokoro_note = QLabel(
            f"<small>{t('Runs Kokoro directly in OpenWand. No server, API key, reference audio, or voice clone is needed.')}</small>"
        )
        kokoro_note.setWordWrap(True)
        self._kokoro_install_btn = QPushButton(t("Install Kokoro"))
        self._kokoro_install_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._kokoro_install_btn.clicked.connect(self._install_kokoro)
        self._kokoro_install_status_lbl = QLabel()
        self._kokoro_install_status_lbl.setWordWrap(True)
        self._kokoro_assets_btn = QPushButton()
        self._kokoro_assets_btn.clicked.connect(self._kokoro_assets_action)
        self._kokoro_assets_btn.setVisible(False)
        self._kokoro_assets_mode = ""
        self._kokoro_assets_update_revision = ""
        kf.addRow(kokoro_note)
        kf.addRow(self._status_action_row(
            self._kokoro_install_status_lbl,
            self._kokoro_install_btn,
        ))
        kf.addRow(QLabel(), self._kokoro_assets_btn)
        kf.addRow(_tooltip_label("Voice", kokoro_voice_tip), self._fields["KOKORO_VOICE"])
        kf.addRow(_tooltip_label("Language code", kokoro_lang_tip), self._fields["KOKORO_LANG_CODE"])
        kf.addRow(_tooltip_label("Device", kokoro_device_tip), self._fields["KOKORO_DEVICE"])
        kf.addRow(_tooltip_label("Speed", kokoro_speed_tip), self._fields["KOKORO_SPEED"])
        kf.addRow(_tooltip_label("Sample rate (Hz)", kokoro_rate_tip), self._fields["KOKORO_SAMPLE_RATE"])

        for gw in (cartesia_w, eleven_w, openai_w, custom_w, gsv_w, kokoro_w):
            keys_cv.addWidget(gw)
        self._tts_provider_groups = {
            "cartesia": cartesia_w,
            "elevenlabs": eleven_w,
            "openai": openai_w,
            "openai_compatible": custom_w,
            "gpt_sovits": gsv_w,
            "kokoro": kokoro_w,
        }
        provider_cv.addWidget(provider_details)
        self._tts_provider_details = provider_details

        # ── PLAYBACK card ────────────────────────────────────────────────
        playback_card, playback_cv = self._card("Playback")
        playback_w = QWidget()
        playback_f = _expanding_form_layout(playback_w)
        playback_f.setContentsMargins(0, 0, 0, 0)
        playback_f.setSpacing(8)
        volume_row = QWidget()
        volume_h = QHBoxLayout(volume_row)
        volume_h.setContentsMargins(0, 0, 0, 0)
        volume_h.setSpacing(8)
        volume_h.addWidget(self._fields["TTS_VOLUME"], 1)
        volume_h.addWidget(self._tts_volume_value_lbl, 0)
        playback_f.addRow(_tooltip_label("Volume", tts_volume_tip), volume_row)
        playback_cv.addWidget(playback_w)
        # Volume affects every speech mode, so keep it first on the page.
        self._voice_playback_card = playback_card
        outer.insertWidget(0, playback_card)
        feature_switcher = QWidget()
        feature_switcher_h = QHBoxLayout(feature_switcher)
        feature_switcher_h.setContentsMargins(0, 0, 0, 0)
        feature_switcher_h.setSpacing(6)
        self._voice_feature_button_group = QButtonGroup(feature_switcher)
        self._voice_feature_button_group.setExclusive(True)
        self._voice_feature_buttons: dict[str, QPushButton] = {}
        for key, label in (
            ("tts", "Text to speech"),
            ("stt", "Speech to text"),
            ("live", "Live conversation"),
        ):
            button = QPushButton(t(label))
            button.setObjectName("settingsSegmentButton")
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, mode=key: self._show_voice_feature(mode))
            self._voice_feature_button_group.addButton(button)
            self._voice_feature_buttons[key] = button
            feature_switcher_h.addWidget(button, 1)
        outer.insertWidget(1, feature_switcher)

        # ── LIVE VOICE CONVERSATION card ─────────────────────────────────
        live_card, live_cv = self._card("Live voice conversation")
        self._live_voice_key_note_lbl = QLabel()
        self._live_voice_key_note_lbl.setWordWrap(True)
        live_cv.addWidget(self._live_voice_key_note_lbl)
        self._live_voice_install_btn = QPushButton(t("Install live voice"))
        self._live_voice_install_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._live_voice_install_btn.clicked.connect(self._install_live_voice)
        self._live_voice_install_status_lbl = QLabel()
        self._live_voice_install_status_lbl.setWordWrap(True)

        live_provider = _NoScrollCombo()
        live_provider.addItem(t("None"), "none")
        live_provider.addItem(t(_PROVIDER_LABELS["google"]), "google")
        live_provider.setCurrentIndex(live_provider.findData("google"))
        live_provider.setMinimumWidth(140)
        live_provider_tip = (
            "Disabled does not start a live microphone session. Gemini Live "
            "uses the Google API key from Connections."
        )
        self._fields["LIVE_VOICE_PROVIDER"] = live_provider

        live_model_container = QWidget()
        live_model_layout = QHBoxLayout(live_model_container)
        live_model_layout.setContentsMargins(0, 0, 0, 0)
        live_model_layout.setSpacing(6)
        live_model_stack = QWidget()
        live_model_stack_layout = QVBoxLayout(live_model_stack)
        live_model_stack_layout.setContentsMargins(0, 0, 0, 0)
        live_model_stack_layout.setSpacing(2)
        live_model = _NoScrollCombo()
        live_model_edit = QLineEdit()
        live_model_edit.hide()
        live_model_stack_layout.addWidget(live_model)
        live_model_stack_layout.addWidget(live_model_edit)
        live_model_refresh = QPushButton("↻")
        live_model_refresh.setFixedWidth(34)
        live_model_refresh.setStyleSheet("QPushButton { padding: 5px 4px; }")
        live_model_refresh.setToolTip("Fetch the latest model names from the provider")
        live_model_layout.addWidget(live_model_stack, 1)
        live_model_layout.addWidget(live_model_refresh)
        live_model_tip = (
            "Pick a common live model, or choose Custom / enter manually to "
            "type the exact model name."
        )
        self._fields["LIVE_VOICE_MODEL"] = live_model
        self._live_voice_model_row = {
            "api_key_combo": live_provider,
            "model_combo": live_model,
            "model_edit": live_model_edit,
            "refresh_btn": live_model_refresh,
        }
        self._fill_live_voice_model_combo("google", "")
        live_model.currentIndexChanged.connect(
            lambda _: self._on_model_combo_changed(self._live_voice_model_row)
        )

        def _on_live_voice_provider_change() -> None:
            provider = _get(live_provider).strip() or "google"
            if provider != "none":
                self._fill_live_voice_model_combo(provider, self._live_voice_model_value())
            self._update_live_voice_provider_fields()

        live_provider.currentIndexChanged.connect(lambda _: _on_live_voice_provider_change())
        live_model.currentIndexChanged.connect(lambda _: self._schedule_dirty_refresh())
        live_model_edit.textChanged.connect(lambda _: self._schedule_dirty_refresh())
        live_model_refresh.clicked.connect(lambda: self._refresh_models_for_row(self._live_voice_model_row))
        live_voice_container = QWidget()
        live_voice_layout = QVBoxLayout(live_voice_container)
        live_voice_layout.setContentsMargins(0, 0, 0, 0)
        live_voice_layout.setSpacing(2)
        live_voice_name = _NoScrollCombo()
        live_voice_name_edit = QLineEdit()
        live_voice_name_edit.hide()
        live_voice_layout.addWidget(live_voice_name)
        live_voice_layout.addWidget(live_voice_name_edit)
        live_voice_name_tip = (
            "Pick a prebuilt Gemini voice, use the model default, or choose "
            "Custom / enter manually to type an exact voice name."
        )
        self._fields["LIVE_VOICE_VOICE_NAME"] = live_voice_name
        self._live_voice_voice_row = {
            "model_combo": live_voice_name,
            "model_edit": live_voice_name_edit,
        }
        self._fill_live_voice_voice_combo("")
        live_voice_name.currentIndexChanged.connect(
            lambda _: self._on_model_combo_changed(self._live_voice_voice_row)
        )
        live_voice_name.currentIndexChanged.connect(lambda _: self._schedule_dirty_refresh())
        live_voice_name_edit.textChanged.connect(lambda _: self._schedule_dirty_refresh())
        self._fields["LIVE_VOICE_HALF_DUPLEX"] = QCheckBox(
            t("Pause mic while OpenWand talks (for speakers; disables barge-in)")
        )
        live_half_duplex_tip = (
            "Turn this on when OpenWand plays through speakers, so it does not "
            "hear and interrupt itself. With headphones, leave it off to talk "
            "over OpenWand naturally."
        )

        live_fw = QWidget()
        lvf = _expanding_form_layout(live_fw)
        lvf.setContentsMargins(0, 0, 0, 0)
        lvf.setSpacing(8)
        live_install_row = self._status_action_row(
            self._live_voice_install_status_lbl,
            self._live_voice_install_btn,
        )
        live_provider_label = _tooltip_label("Conversation provider", live_provider_tip)
        live_model_label = _tooltip_label("Conversation model", live_model_tip)
        live_voice_label = _tooltip_label("Conversation voice", live_voice_name_tip)
        live_speaker_label = _tooltip_label("Speaker mode", live_half_duplex_tip)
        lvf.addRow(live_install_row)
        lvf.addRow(live_provider_label, live_provider)
        lvf.addRow(live_model_label, live_model_container)
        lvf.addRow(live_voice_label, live_voice_container)
        lvf.addRow(
            live_speaker_label,
            self._fields["LIVE_VOICE_HALF_DUPLEX"],
        )
        self._live_voice_enabled_widgets = [
            live_install_row,
            live_model_label,
            live_model_container,
            live_voice_label,
            live_voice_container,
            live_speaker_label,
            self._fields["LIVE_VOICE_HALF_DUPLEX"],
        ]
        live_cv.addWidget(live_fw)
        outer.addWidget(live_card)
        self._voice_feature_cards["live"] = live_card
        self._refresh_live_voice_install_status()
        self._refresh_live_voice_key_note()

        advanced_group, advanced_layout = self._collapsible_group("Advanced settings")
        advanced_form_w = QWidget()
        advanced_form = _expanding_form_layout(advanced_form_w)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.setSpacing(8)
        advanced_form.addRow(
            _tooltip_label("Read-aloud min words", tts_chunk_min_tip),
            self._fields["TTS_READ_ALOUD_MIN_WORDS"],
        )
        advanced_form.addRow(
            _tooltip_label("Read-aloud max words", tts_chunk_max_tip),
            self._fields["TTS_READ_ALOUD_MAX_WORDS"],
        )
        advanced_form.addRow(
            _tooltip_label("STT first chunk trigger (s)", stt_first_chunk_tip),
            self._fields["STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS"],
        )
        advanced_form.addRow(
            _tooltip_label("STT chunk cadence (s)", stt_chunk_step_tip),
            self._fields["STT_BACKGROUND_CHUNK_STEP_SECONDS"],
        )
        advanced_form.addRow(
            _tooltip_label("STT live-edge delay (s)", stt_live_delay_tip),
            self._fields["STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS"],
        )
        advanced_form.addRow(
            _tooltip_label("STT overlap (s)", stt_overlap_tip),
            self._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"],
        )
        advanced_layout.addWidget(advanced_form_w)
        outer.addWidget(advanced_group)

        # Keep the TTS test with the feature it validates.
        self._tts_test_status_lbl = QLabel()
        self._tts_test_status_lbl.setWordWrap(True)
        self._tts_test_row = self._button_row(("Test TTS", self._test_tts_connection))
        provider_cv.addWidget(self._tts_test_row)
        provider_cv.addWidget(self._tts_test_status_lbl)

        self._update_tts_provider_fields()
        self._set_status_label(
            self._elevenlabs_install_status_lbl,
            None,
            "",
        )
        self._set_status_label(
            self._kokoro_install_status_lbl,
            None,
            "",
        )
        self._show_voice_feature("tts")
        outer.addStretch()
        scroll.setWidget(w)
        return scroll

    def _show_voice_feature(self, mode: str) -> None:
        """Show one speech feature editor while keeping shared playback visible."""
        cards = getattr(self, "_voice_feature_cards", {})
        for key, card in cards.items():
            card.setVisible(key == mode)
        buttons = getattr(self, "_voice_feature_buttons", {})
        for key, button in buttons.items():
            button.setChecked(key == mode)
        self._active_voice_feature = mode

    def _update_tts_provider_fields(self) -> None:
        """Show only the selected TTS provider's settings rows."""
        groups = getattr(self, "_tts_provider_groups", None)
        if not groups:
            return
        provider = _get(self._fields["TTS_PROVIDER"]).strip().lower()
        for name, widget in groups.items():
            widget.setVisible(name == provider)
        details = getattr(self, "_tts_provider_details", None)
        if details is not None:
            details.setVisible(provider != "none")
        enabled = provider != "none"
        speak_replies = self._fields.get("TTS_SPEAK_REPLIES")
        if speak_replies is not None:
            speak_replies.setVisible(enabled)
        test_row = getattr(self, "_tts_test_row", None)
        if test_row is not None:
            test_row.setVisible(enabled)
        test_status = getattr(self, "_tts_test_status_lbl", None)
        if test_status is not None:
            test_status.setVisible(enabled)
        notice = getattr(self, "_tts_timing_notice_lbl", None)
        if isinstance(notice, QLabel):
            notice.setVisible(provider in _TTS_TIMESTAMPLESS_PROVIDERS)
            notice.setText(t(_TTS_TIMING_NOTICE))
        if provider in {"kokoro", "elevenlabs"} and self._tts_page_is_current():
            self._refresh_tts_optional_install_status()

    def _update_stt_provider_fields(self) -> None:
        """Switch STT controls between disabled, local, and Cloudflare modes."""
        provider = _get(getattr(self, "_fields", {}).get("STT_PROVIDER")).strip().lower() or "local"
        is_disabled = provider == "none"
        is_cloudflare = provider == "cloudflare"
        for widget in getattr(self, "_stt_local_widgets", []):
            widget.setVisible(provider == "local")
        for widget in getattr(self, "_stt_cloud_widgets", []):
            widget.setVisible(is_cloudflare)
        for widget in getattr(self, "_stt_enabled_widgets", []):
            widget.setVisible(not is_disabled)
        self._rebuild_stt_languages()

        button = getattr(self, "_stt_download_btn", None)
        if isinstance(button, QPushButton):
            button.setVisible(not is_disabled)
            if is_disabled:
                pass
            elif is_cloudflare:
                self._connect_button_action(button, self._test_cloudflare_stt_connection)
                button.setText(t("Test Cloudflare STT"))
                button.setToolTip(t("Send a half-second silent clip to verify Workers AI access."))
            else:
                self._connect_button_action(button, self._preload_stt_model)
                button.setText(t("Install STT"))
                button.setToolTip(
                    t(
                        "Install or repair faster-whisper, then download and load the speech "
                        "model so the first hold-to-talk does not stall."
                    )
                )
        if getattr(self, "_stt_active_lbl", None) is not None:
            self._refresh_stt_active_backend()

    def _test_cloudflare_stt_connection(self) -> None:
        """Verify the currently entered/stored Cloudflare Workers AI credentials."""
        from core import cloudflare_stt

        account_id = _get(self._fields["STT_CLOUDFLARE_ACCOUNT_ID"]).strip()
        api_token = self._effective_secret_value("CLOUDFLARE_API_TOKEN")
        import config as cfg

        self._start_async_test(
            "cloudflare_stt_test",
            self._stt_active_lbl,
            lambda: cloudflare_stt.test_connection(
                account_id=account_id,
                api_token=api_token,
                model=str(
                    getattr(cfg, "STT_CLOUDFLARE_MODEL", "@cf/openai/whisper-large-v3-turbo")
                ),
                timeout_seconds=min(
                    30.0,
                    float(getattr(cfg, "STT_CLOUDFLARE_TIMEOUT_SECONDS", 90.0)),
                ),
            ),
        )

    def _tts_page_is_current(self) -> bool:
        """Return whether the Settings dialog is currently showing TTS / Voice."""
        tabs = getattr(self, "_tabs", None)
        if not isinstance(tabs, QTabWidget):
            return False
        return tabs.currentIndex() == getattr(self, "_tts_tab_index", -1)

    def _handle_kokoro_device_changed(self) -> None:
        """Update Kokoro install UI after device changes without repeating checks."""
        if not self._tts_page_is_current():
            return
        if not self._tts_install_status_checked:
            self._refresh_tts_optional_install_status()
            return
        result = getattr(self, "_tts_install_status_result", None)
        if isinstance(result, dict):
            selected = _get(self._fields["KOKORO_DEVICE"]).strip().lower()
            torch_status = result.get("kokoro_torch_status") if isinstance(result.get("kokoro_torch_status"), dict) else {}
            needs_cuda_status = selected == "cuda" or (selected == "auto" and bool(result.get("system_cuda_available")))
            if needs_cuda_status and bool(torch_status.get("fast")):
                self._tts_install_status_checked = False
                self._refresh_tts_optional_install_status()
                return
        self._apply_cached_kokoro_install_status()

    @staticmethod
    def _optional_package_installed(module_name: str) -> bool:
        """Return True when an optional package is importable."""
        try:
            from core import optional_deps

            return optional_deps.is_importable(module_name)
        except Exception:
            try:
                importlib.invalidate_caches()
                return importlib.util.find_spec(module_name) is not None
            except Exception:
                return False

    def _elevenlabs_installed(self) -> bool:
        """Return True when the optional ElevenLabs package is importable."""
        try:
            from core import optional_deps

            return bool(optional_deps.optional_package_runtime_status("elevenlabs").get("valid"))
        except Exception:
            pass
        return self._optional_package_installed("elevenlabs")

    def _kokoro_installed(self) -> bool:
        """Return True when Kokoro and its English G2P model are importable."""
        return all(
            self._optional_package_installed(module_name)
            for module_name in ("kokoro", "en_core_web_sm")
        )

    def _kokoro_install_mode(self) -> str:
        """Return the CPU/GPU install mode implied by the selected Kokoro device."""
        try:
            from core import optional_deps

            return optional_deps.kokoro_install_mode_for_device(_get(self._fields["KOKORO_DEVICE"]))
        except Exception:
            return "cpu"

    def _kokoro_torch_status(self) -> dict[str, object]:
        """Return Torch status for the Kokoro optional package layer."""
        try:
            from core import optional_deps

            return optional_deps.kokoro_torch_status_subprocess()
        except Exception as exc:  # noqa: BLE001
            return {
                "installed": False,
                "version": "",
                "cuda_version": "",
                "cuda_available": False,
                "device": "",
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _kokoro_torch_status_fast(self) -> dict[str, object]:
        """Return Torch package metadata without importing or verifying Torch."""
        try:
            from core import optional_deps

            return optional_deps.kokoro_torch_status_fast()
        except Exception as exc:  # noqa: BLE001
            return {
                "installed": False,
                "version": "",
                "cuda_version": "",
                "cuda_available": False,
                "device": "",
                "error": f"{type(exc).__name__}: {exc}",
                "fast": True,
                "valid": False,
            }

    def _kokoro_install_snapshot(self) -> dict[str, object]:
        """Return a non-blocking Kokoro install snapshot for UI decisions."""
        installed = self._kokoro_installed()
        selected_device = _get(self._fields["KOKORO_DEVICE"]).strip() or "auto"
        install_status: dict[str, object] = {}
        try:
            from core import optional_deps

            mode = self._kokoro_install_mode()
            install_status = optional_deps.current_optional_install_status(
                "Kokoro",
                "kokoro",
                device="cuda" if mode == "gpu" else "cpu",
            )
            if not install_status:
                stt_device = _get(self._fields["STT_DEVICE"]).strip() or "auto"
                install_status = optional_deps.current_local_speech_install_status(
                    kokoro_device="cuda" if mode == "gpu" else "cpu",
                    stt_device=stt_device,
                )
        except Exception:
            install_status = {}
        result = getattr(self, "_tts_install_status_result", None)
        mode = self._kokoro_install_mode()
        if isinstance(result, dict) and result.get("ok"):
            torch_status = result.get("kokoro_torch_status") if isinstance(result.get("kokoro_torch_status"), dict) else {}
            install_status = result.get("kokoro_install_status") if isinstance(result.get("kokoro_install_status"), dict) else install_status
            system_has_cuda = bool(result.get("system_cuda_available"))
            installed = bool(result.get("kokoro_installed"))
        else:
            try:
                spec_device = "cuda" if mode == "gpu" else "cpu"
                spec_status = optional_deps.optional_package_spec_status("kokoro", device=spec_device)
                installed = bool(spec_status.get("valid"))
            except Exception:
                pass
            torch_status = self._kokoro_torch_status_fast() if installed else {}
            system_has_cuda = False
        needs_gpu = self._kokoro_needs_gpu_install_from_status(
            installed=installed,
            selected_device=selected_device,
            torch_status=torch_status,
            system_cuda_available=system_has_cuda,
        )
        needs_repair = bool(torch_status and (torch_status.get("error") or torch_status.get("valid") is False))
        return {
            "installed": installed,
            "selected_device": selected_device,
            "mode": mode,
            "install_status": install_status,
            "torch_status": torch_status,
            "needs_gpu": needs_gpu,
            "needs_repair": needs_repair,
            "system_cuda_available": system_has_cuda,
        }

    def _kokoro_needs_gpu_install(self) -> bool:
        """Return whether the selected Kokoro device needs CUDA Torch support."""
        return bool(self._kokoro_install_snapshot().get("needs_gpu"))

    @staticmethod
    def _kokoro_needs_gpu_install_from_status(
        *,
        installed: bool,
        selected_device: str,
        torch_status: dict[str, object],
        system_cuda_available: bool,
    ) -> bool:
        """Return whether selected Kokoro settings need a CUDA Torch install."""
        selected = selected_device.strip().lower()
        if not installed or selected == "cpu" or bool(torch_status.get("cuda_available")):
            return False
        if bool(torch_status.get("timed_out")):
            return False
        if torch_status and (torch_status.get("error") or torch_status.get("valid") is False):
            if selected == "cuda":
                return True
            if selected == "auto":
                return system_cuda_available
            return False
        if bool(torch_status.get("fast")) and bool(torch_status.get("installed")):
            if selected == "cuda":
                return True
            if selected == "auto":
                return system_cuda_available
            return False
        if selected == "cuda":
            return True
        if selected == "auto":
            return system_cuda_available
        return False

    def _refresh_tts_optional_install_status(self) -> None:
        """Check optional TTS package status in the background for the Voice page."""
        if getattr(self, "_disposing", False):
            return
        if self._tts_install_status_running:
            return
        if self._tts_install_status_checked:
            return
        label_pairs = (
            ("_stt_install_status_lbl", "Checking STT install status..."),
            ("_elevenlabs_install_status_lbl", "Checking ElevenLabs install status..."),
            ("_kokoro_install_status_lbl", "Checking Kokoro install status..."),
        )
        for attr, message in label_pairs:
            label = getattr(self, attr, None)
            if isinstance(label, QLabel):
                self._set_status_label(label, None, message)
        for attr in ("_stt_download_btn", "_elevenlabs_install_btn", "_kokoro_install_btn"):
            button = getattr(self, attr, None)
            if isinstance(button, QPushButton):
                button.setEnabled(False)
        assets_button = getattr(self, "_kokoro_assets_btn", None)
        if isinstance(assets_button, QPushButton):
            assets_button.setVisible(False)

        self._tts_install_status_running = True
        self._tts_install_status_token += 1
        self._tts_install_status_result = None
        token = self._tts_install_status_token
        selected_device = _get(self._fields["KOKORO_DEVICE"]).strip() or "auto"
        kokoro_voice = _get(self._fields["KOKORO_VOICE"]).strip() or "af_heart"
        stt_model = _get(self._fields["STT_MODEL"]).strip() or "base"
        stt_device = _get(self._fields["STT_DEVICE"]).strip() or "auto"
        stt_compute = _get(self._fields["STT_COMPUTE_TYPE"]).strip() or "int8"
        # Do not parent the carrier to the dialog. The probe can legitimately
        # outlive a window that the user closes immediately; parenting it to
        # ``self`` destroys its C++ signal source while the worker still owns
        # the Python wrapper, making the final emit escape the thread as
        # ``RuntimeError: Signal source has been deleted``.
        carrier = _TtsInstallStatusSignals()
        carrier.done.connect(self._finish_tts_optional_install_status)
        self._tts_install_status_signal_carriers.append(carrier)

        def _worker() -> None:
            try:
                from core import optional_deps

                stt_spec_status = optional_deps.optional_package_runtime_status("stt", device=stt_device)
                stt_package_installed = bool(stt_spec_status.get("valid"))
                stt_runtime_status = (
                    optional_deps.stt_runtime_import_status_subprocess()
                    if stt_package_installed
                    else {}
                )
                elevenlabs_spec_status = optional_deps.optional_package_runtime_status("elevenlabs")
                elevenlabs_package_installed = bool(elevenlabs_spec_status.get("valid"))
                elevenlabs_runtime_status = (
                    optional_deps.elevenlabs_runtime_import_status_subprocess()
                    if elevenlabs_package_installed
                    else {}
                )
                elevenlabs_installed = bool(
                    elevenlabs_package_installed
                    and elevenlabs_runtime_status.get("valid") is True
                )
                system_has_cuda = optional_deps.system_cuda_available()
                mode = optional_deps.kokoro_install_mode_for_device(selected_device)
                elevenlabs_install_status = optional_deps.current_optional_install_status(
                    "ElevenLabs",
                    "elevenlabs",
                )
                kokoro_install_status = optional_deps.current_optional_install_status(
                    "Kokoro",
                    "kokoro",
                    device="cuda" if mode == "gpu" else "cpu",
                )
                if not kokoro_install_status:
                    kokoro_install_status = optional_deps.current_local_speech_install_status(
                        kokoro_device="cuda" if mode == "gpu" else "cpu",
                        stt_device=stt_device,
                    )
                kokoro_spec_status = optional_deps.optional_package_spec_status(
                    "kokoro",
                    device="cuda" if mode == "gpu" else "cpu",
                )
                kokoro_candidate = bool(kokoro_spec_status.get("installed"))
                kokoro_runtime_status = (
                    optional_deps.kokoro_runtime_import_status_subprocess()
                    if kokoro_candidate
                    else {}
                )
                kokoro_installed = bool(
                    kokoro_spec_status.get("valid")
                    or kokoro_runtime_status.get("valid") is True
                )
                needs_cuda_status = mode == "gpu"
                torch_status = (
                    optional_deps.kokoro_torch_status_subprocess()
                    if kokoro_installed and needs_cuda_status
                    else optional_deps.kokoro_torch_status_fast() if kokoro_installed else {}
                )
                needs_gpu = self._kokoro_needs_gpu_install_from_status(
                    installed=kokoro_installed,
                    selected_device=selected_device,
                    torch_status=torch_status,
                    system_cuda_available=system_has_cuda,
                )
                kokoro_assets: dict[str, object] = {}
                if kokoro_installed:
                    try:
                        from core import tts_assets

                        assets_status = tts_assets.verify(
                            tts_assets.KOKORO,
                            voices=tts_assets.parse_voices(kokoro_voice),
                        )
                        kokoro_assets = {
                            "state": assets_status.state,
                            "problems": list(assets_status.problems),
                            "missing_voices": list(assets_status.missing_voices),
                        }
                        if assets_status.state == "ok":
                            kokoro_assets["update_revision"] = tts_assets.check_update(tts_assets.KOKORO) or ""
                    except Exception:  # noqa: BLE001 - asset status is best-effort
                        kokoro_assets = {}
                result: dict[str, object] = {
                    "ok": True,
                    "stt_model": stt_model,
                    "stt_device": stt_device,
                    "stt_compute": stt_compute,
                    "stt_package_installed": stt_package_installed,
                    "stt_spec_status": stt_spec_status,
                    "stt_runtime_status": stt_runtime_status,
                    "elevenlabs_installed": elevenlabs_installed,
                    "elevenlabs_package_installed": elevenlabs_package_installed,
                    "elevenlabs_runtime_status": elevenlabs_runtime_status,
                    "elevenlabs_install_status": elevenlabs_install_status,
                    "kokoro_installed": kokoro_installed,
                    "kokoro_runtime_status": kokoro_runtime_status,
                    "kokoro_mode": mode,
                    "kokoro_install_status": kokoro_install_status,
                    "kokoro_torch_status": torch_status,
                    "kokoro_needs_gpu": needs_gpu,
                    "kokoro_assets": kokoro_assets,
                    "system_cuda_available": system_has_cuda,
                }
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            try:
                carrier.done.emit(token, result)
            except RuntimeError:
                # Qt disconnects a deleted receiver automatically. Keep the
                # background status probe from becoming an application-level
                # thread failure if shutdown races the final queued signal.
                _settings_log.debug(
                    "Discarded optional TTS status after Settings closed",
                    exc_info=True,
                )

        threading.Thread(target=_worker, daemon=True, name="settings-tts-install-status").start()

    def _finish_tts_optional_install_status(self, token: int, result: object) -> None:
        """Apply background optional TTS package status to the Voice page."""
        if token != getattr(self, "_tts_install_status_token", 0) or getattr(self, "_disposing", False):
            return
        self._tts_install_status_running = False
        self._tts_install_status_checked = True
        from ui.runtime_log_bridge import log_event

        if not isinstance(result, dict) or not result.get("ok"):
            self._tts_install_status_result = None
            message = f"Install status check failed: {result.get('error') if isinstance(result, dict) else result}"
            log_event("installer", "error", message)
            for attr in ("_stt_install_status_lbl", "_elevenlabs_install_status_lbl", "_kokoro_install_status_lbl"):
                label = getattr(self, attr, None)
                if isinstance(label, QLabel):
                    self._set_test_status(label, False, message)
            for attr in ("_stt_download_btn", "_elevenlabs_install_btn", "_kokoro_install_btn"):
                button = getattr(self, attr, None)
                if isinstance(button, QPushButton):
                    button.setEnabled(True)
            return
        # Mirror the Voice-page detection line into the runtime event log:
        # staged installs that failed while OpenWand was closed used to be visible
        # only in this Settings label.
        summary = (
            f"Install status: STT {'installed' if result.get('stt_package_installed') else 'not installed'}; "
            f"ElevenLabs {'installed' if result.get('elevenlabs_installed') else 'not installed'}; "
            f"Kokoro {'installed (' + str(result.get('kokoro_mode') or 'cpu') + ')' if result.get('kokoro_installed') else 'not installed'}."
        )
        log_event("installer", "info", summary)
        for status_key, package_name in (
            ("elevenlabs_install_status", "ElevenLabs"),
            ("kokoro_install_status", "Kokoro"),
        ):
            raw_status = result.get(status_key)
            failed_message = _failed_optional_install_message(raw_status if isinstance(raw_status, dict) else None)
            if failed_message:
                log_event("installer", "error", f"{package_name} last install failed: {failed_message}")
        self._tts_install_status_result = dict(result)
        self._apply_stt_runtime_status(result)
        self._apply_elevenlabs_install_status(
            bool(result.get("elevenlabs_installed")),
            install_status=result.get("elevenlabs_install_status") if isinstance(result.get("elevenlabs_install_status"), dict) else {},
            runtime_status=result.get("elevenlabs_runtime_status") if isinstance(result.get("elevenlabs_runtime_status"), dict) else {},
        )
        self._apply_cached_kokoro_install_status()

    def _apply_stt_runtime_status(self, result: dict[str, object]) -> None:
        """Apply the background STT package + import probe to the Voice page."""
        label = getattr(self, "_stt_install_status_lbl", None)
        button = getattr(self, "_stt_download_btn", None)
        if not isinstance(label, QLabel) or not isinstance(button, QPushButton):
            return
        provider = _get(getattr(self, "_fields", {}).get("STT_PROVIDER")).strip().lower() or "local"
        if provider == "none":
            label.clear()
            label.setVisible(False)
            button.setVisible(False)
            return
        label.setVisible(True)
        self._connect_button_action(button, self._preload_stt_model)
        button.setVisible(True)
        button.setEnabled(True)
        package_installed = bool(result.get("stt_package_installed"))
        runtime = result.get("stt_runtime_status") if isinstance(result.get("stt_runtime_status"), dict) else {}
        model = str(result.get("stt_model") or "base")
        device = str(result.get("stt_device") or "auto")
        compute = str(result.get("stt_compute") or "int8")
        summary = f"{model} · {device} / {compute}"
        if not package_installed:
            button.setText(t("Install STT"))
            self._set_test_status(
                label,
                "warn",
                "STT package is not installed. Click Install STT to install and verify it.",
            )
            return
        runtime_error = str(runtime.get("error") or "").strip()
        if runtime_error or runtime.get("valid") is False:
            button.setText(t("Reinstall STT"))
            self._set_test_status(
                label,
                "warn",
                f"STT package files are installed, but faster-whisper cannot load: "
                f"{runtime_error or 'runtime import verification failed.'}",
            )
            return
        button.setText(t("Reinstall STT"))
        self._set_test_status(
            label,
            True,
            f"STT package and runtime verified. Configured backend: {summary}; model loads on first use.",
        )

    def _apply_cached_kokoro_install_status(self) -> bool:
        """Reapply Kokoro install controls from the one Voice-page status check."""
        result = getattr(self, "_tts_install_status_result", None)
        if not isinstance(result, dict) or not result.get("ok"):
            return False
        selected_device = _get(self._fields["KOKORO_DEVICE"]).strip() or "auto"
        selected = selected_device.strip().lower()
        system_has_cuda = bool(result.get("system_cuda_available"))
        try:
            from core import optional_deps

            mode = optional_deps.kokoro_install_mode_for_device(selected_device)
        except Exception:
            mode = "gpu" if selected == "cuda" else "cpu"
        torch_status = result.get("kokoro_torch_status") if isinstance(result.get("kokoro_torch_status"), dict) else {}
        install_status = result.get("kokoro_install_status") if isinstance(result.get("kokoro_install_status"), dict) else {}
        installed = bool(result.get("kokoro_installed"))
        needs_gpu = self._kokoro_needs_gpu_install_from_status(
            installed=installed,
            selected_device=selected_device,
            torch_status=torch_status,
            system_cuda_available=system_has_cuda,
        )
        self._apply_kokoro_install_status(
            installed=installed,
            mode=mode,
            install_status=install_status,
            torch_status=torch_status,
            needs_gpu=needs_gpu,
            assets=result.get("kokoro_assets") if isinstance(result.get("kokoro_assets"), dict) else None,
            runtime_status=result.get("kokoro_runtime_status") if isinstance(result.get("kokoro_runtime_status"), dict) else {},
        )
        return True

    def _connect_button_action(self, button: QPushButton, callback: Callable[[], None]) -> None:
        """Replace a button's click handler after restart/apply state changes."""
        _disconnect_clicked_handlers(button)
        button.clicked.connect(callback)

    def _restart_for_staged_apply(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _apply_restart_apply_status(
        self,
        label: QLabel,
        button: QPushButton,
        *,
        display_name: str,
        install_status: dict[str, object] | None,
    ) -> bool:
        """Show durable staged-apply state instead of flattening it to uninstalled."""
        if not isinstance(install_status, dict) or not install_status.get("restart_apply"):
            return False
        button.setEnabled(True)
        button.setText(t("Restart app now"))
        self._connect_button_action(button, self._restart_for_staged_apply)
        message = str(install_status.get("message") or "").strip()
        if not message:
            message = f"{display_name} packages are staged. Click Restart app now to close OpenWand and apply them."
        self._set_test_status(label, "warn", _translate_status_message(message))
        return True

    def _apply_elevenlabs_install_status(
        self,
        installed: bool,
        *,
        install_status: dict[str, object] | None = None,
        runtime_status: dict[str, object] | None = None,
    ) -> None:
        """Apply ElevenLabs install state to the settings controls."""
        label = getattr(self, "_elevenlabs_install_status_lbl", None)
        button = getattr(self, "_elevenlabs_install_btn", None)
        if not isinstance(label, QLabel) or not isinstance(button, QPushButton):
            return
        if self._apply_restart_apply_status(
            label,
            button,
            display_name="ElevenLabs",
            install_status=install_status,
        ):
            return
        self._connect_button_action(button, self._install_elevenlabs)
        failed_install_message = _failed_optional_install_message(install_status)
        if failed_install_message:
            button.setEnabled(True)
            button.setText(t("Install ElevenLabs"))
            self._set_test_status(label, "warn", failed_install_message)
            return
        runtime_error = str((runtime_status or {}).get("error") or "").strip()
        if runtime_status and (runtime_error or runtime_status.get("valid") is False):
            button.setEnabled(True)
            button.setText(t("Reinstall ElevenLabs"))
            detail = runtime_error or "SDK import verification failed."
            self._set_test_status(
                label,
                "warn",
                f"ElevenLabs package files are installed, but the SDK cannot load: {detail}",
            )
            return
        if installed:
            button.setEnabled(True)
            button.setText(t("Reinstall ElevenLabs"))
            self._set_test_status(label, True, "ElevenLabs is installed.")
        else:
            button.setEnabled(True)
            button.setText(t("Install ElevenLabs"))
            self._set_test_status(
                label,
                "warn",
                "ElevenLabs is not installed. Release builds keep it optional to reduce download size; install it here.",
            )

    def _refresh_elevenlabs_install_status(self) -> None:
        """Refresh ElevenLabs install button and status copy."""
        install_status: dict[str, object] = {}
        try:
            from core import optional_deps

            install_status = optional_deps.current_optional_install_status(
                "ElevenLabs",
                "elevenlabs",
            )
        except Exception:
            install_status = {}
        self._apply_elevenlabs_install_status(self._elevenlabs_installed(), install_status=install_status)
        self._tts_install_status_checked = True
        self._tts_install_status_result = None

    def _live_voice_installed(self) -> bool:
        """Return True when the optional google-genai package is importable."""
        try:
            from core import optional_deps

            return bool(optional_deps.optional_package_spec_status("live_voice").get("valid"))
        except Exception:
            pass
        try:
            from core import live_voice

            return live_voice.genai_available()
        except Exception:
            return False

    def _refresh_live_voice_install_status(self) -> None:
        """Refresh the live voice install button and status copy."""
        label = getattr(self, "_live_voice_install_status_lbl", None)
        button = getattr(self, "_live_voice_install_btn", None)
        if not isinstance(label, QLabel) or not isinstance(button, QPushButton):
            return
        provider = _get(getattr(self, "_fields", {}).get("LIVE_VOICE_PROVIDER")).strip() or "google"
        if provider == "none":
            button.setVisible(False)
            label.clear()
            return
        button.setVisible(True)
        install_status: dict[str, object] = {}
        try:
            from core import optional_deps

            install_status = _read_optional_install_status("Live voice", optional_deps.OPTIONAL_PACKAGES_DIR)
        except Exception:
            install_status = {}
        if self._apply_restart_apply_status(
            label,
            button,
            display_name="Live voice",
            install_status=install_status,
        ):
            return
        self._connect_button_action(button, self._install_live_voice)
        failed_install_message = _failed_optional_install_message(install_status)
        if failed_install_message:
            button.setText(t("Install live voice"))
            self._set_test_status(label, "warn", failed_install_message)
            return
        if self._live_voice_installed():
            button.setText(t("Reinstall live voice"))
            self._set_test_status(label, True, "Live voice support is installed.")
        else:
            button.setText(t("Install live voice"))
            self._set_test_status(
                label,
                "warn",
                "Live voice support is not installed. Install it to enable hands-free conversations.",
            )

    def _refresh_live_voice_key_note(self) -> None:
        """Show whether the selected live voice provider has the key it needs."""
        label = getattr(self, "_live_voice_key_note_lbl", None)
        if not isinstance(label, QLabel):
            return
        import config as cfg

        provider = _get(self._fields.get("LIVE_VOICE_PROVIDER")).strip() or "google"
        if provider == "none":
            label.clear()
            label.setVisible(False)
        elif provider != "google":
            label.setVisible(True)
            label.setText(
                "<small>"
                + t("Live voice currently supports Gemini Live through the Google provider.")
                + "</small>"
            )
            label.setStyleSheet("color: #d8932a;")
        elif str(getattr(cfg, "GOOGLE_API_KEY", "") or "").strip():
            label.setVisible(True)
            label.setText(f"<small>{t('Uses the Google API key from Connections.')}</small>")
            label.setStyleSheet("")
        else:
            label.setVisible(True)
            label.setText(
                "<small>"
                + t("Live voice needs a Google API key. Add one in Connections first.")
                + "</small>"
            )
            label.setStyleSheet("color: #d8932a;")

    def _update_live_voice_provider_fields(self) -> None:
        """Show live-session controls only when a provider is enabled."""
        provider = _get(getattr(self, "_fields", {}).get("LIVE_VOICE_PROVIDER")).strip() or "google"
        enabled = provider != "none"
        for widget in getattr(self, "_live_voice_enabled_widgets", []):
            widget.setVisible(enabled)
        self._refresh_live_voice_install_status()
        self._refresh_live_voice_key_note()

    def _fill_live_voice_model_combo(self, provider: str, selected: str) -> None:
        """Populate the live voice model picker with built-ins plus Custom."""
        row = getattr(self, "_live_voice_model_row", None)
        if not isinstance(row, dict):
            return
        models = _LIVE_VOICE_PROVIDER_MODELS.get(provider, [])
        if not models:
            models = _PROVIDER_MODELS.get(provider, [])
        self._fill_model_combo(row, list(models), provider, selected)

    def _live_voice_model_value(self) -> str:
        """Return the effective live voice model, including custom text."""
        row = getattr(self, "_live_voice_model_row", None)
        if isinstance(row, dict):
            return self._model_value(row)
        field = self._fields.get("LIVE_VOICE_MODEL")
        return _get(field).strip() if field is not None else ""

    def _fill_live_voice_voice_combo(self, selected: str) -> None:
        """Populate the live voice picker with built-ins plus Custom."""
        row = getattr(self, "_live_voice_voice_row", None)
        if not isinstance(row, dict):
            return
        combo = row["model_combo"]
        edit = row["model_edit"]
        options = list(_LIVE_VOICE_VOICE_OPTIONS)
        values = [value for _label, value in options]

        combo.blockSignals(True)
        combo.clear()
        for label, value in options:
            combo.addItem(label, value)
        combo.addItem(_CUSTOM_MODEL_LABEL, _CUSTOM_MODEL_SENTINEL)

        selected = (selected or "").strip()
        if selected in values:
            combo.setCurrentIndex(combo.findData(selected))
            edit.clear()
            edit.hide()
        elif selected:
            combo.setCurrentIndex(combo.findData(_CUSTOM_MODEL_SENTINEL))
            edit.setText(selected)
            edit.show()
        else:
            combo.setCurrentIndex(combo.findData(""))
            edit.clear()
            edit.hide()

        edit.setPlaceholderText(t("voice name"))
        completer = QCompleter([value for value in values if value], edit)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        edit.setCompleter(completer)
        combo.blockSignals(False)

    def _live_voice_voice_value(self) -> str:
        """Return the effective live voice name, including custom text."""
        row = getattr(self, "_live_voice_voice_row", None)
        if isinstance(row, dict):
            return self._model_value(row)
        field = self._fields.get("LIVE_VOICE_VOICE_NAME")
        return _get(field).strip() if field is not None else ""

    def _apply_kokoro_install_status(
        self,
        *,
        installed: bool,
        mode: str,
        torch_status: dict[str, object],
        needs_gpu: bool,
        install_status: dict[str, object] | None = None,
        assets: dict[str, object] | None = None,
        runtime_status: dict[str, object] | None = None,
    ) -> None:
        """Apply Kokoro install state to the settings controls."""
        label = getattr(self, "_kokoro_install_status_lbl", None)
        button = getattr(self, "_kokoro_install_btn", None)
        if not isinstance(label, QLabel) or not isinstance(button, QPushButton):
            return
        if self._apply_restart_apply_status(
            label,
            button,
            display_name="Kokoro",
            install_status=install_status,
        ):
            return
        self._connect_button_action(button, self._install_kokoro)
        self._apply_kokoro_assets_status(installed=installed, assets=assets)
        failed_install_message = _failed_optional_install_message(install_status)
        if failed_install_message:
            button.setEnabled(True)
            button.setText(t("Install Kokoro GPU support") if mode == "gpu" else t("Install Kokoro"))
            self._set_test_status(label, "warn", failed_install_message)
        elif installed and runtime_status and (
            runtime_status.get("error") or runtime_status.get("valid") is False
        ):
            button.setEnabled(True)
            button.setText(t("Reinstall Kokoro"))
            detail = str(runtime_status.get("error") or "runtime import verification failed")
            self._set_test_status(
                label,
                "warn",
                f"Kokoro package files are installed, but the runtime cannot load: {detail}",
            )
        elif installed and torch_status and torch_status.get("timed_out"):
            button.setEnabled(True)
            button.setText(t("Reinstall Kokoro"))
            self._set_test_status(
                label,
                "warn",
                "Kokoro is installed, but Torch/GPU verification is still starting. Click Test TTS to verify the local voice.",
            )
        elif installed and torch_status and (torch_status.get("error") or torch_status.get("valid") is False):
            button.setEnabled(True)
            button.setText(t("Install Kokoro GPU support") if mode == "gpu" else t("Install Kokoro"))
            self._set_test_status(label, "warn", "Kokoro install is incomplete. Reinstall Kokoro.")
        elif installed and needs_gpu:
            button.setEnabled(True)
            button.setText(t("Install Kokoro GPU support"))
            self._set_test_status(label, "warn", "Kokoro GPU support is not installed.")
        elif installed and isinstance(assets, dict) and assets.get("state") in ("damaged", "not_installed"):
            button.setEnabled(True)
            button.setText(t("Reinstall Kokoro"))
            problems = "; ".join(str(item) for item in assets.get("problems") or [])
            self._set_test_status(
                label,
                "warn",
                f"Kokoro voice model files are missing or damaged ({problems}). Click Repair voice files to redownload them.",
            )
        elif installed:
            button.setEnabled(True)
            button.setText(t("Reinstall Kokoro"))
            missing_voices = [str(name) for name in (assets or {}).get("missing_voices") or []]
            update_revision = str((assets or {}).get("update_revision") or "")
            if missing_voices:
                self._set_test_status(
                    label,
                    "warn",
                    f"Kokoro is installed, but voice(s) {', '.join(missing_voices)} are not downloaded yet. "
                    "Click Test TTS to download and try them.",
                )
            elif update_revision:
                self._set_test_status(
                    label,
                    True,
                    "Kokoro is installed. A voice model update is available; click Update voice model to fetch it.",
                )
            elif bool(torch_status.get("fast")):
                self._set_test_status(label, True, "Kokoro is installed.")
            elif bool(torch_status.get("cuda_available")):
                device = str(torch_status.get("device") or "CUDA device")
                self._set_test_status(label, True, f"Kokoro is installed with GPU support ({device}).")
            else:
                self._set_test_status(label, True, "Kokoro is installed with CPU support.")
        else:
            button.setEnabled(True)
            button.setText(t("Install Kokoro GPU support") if mode == "gpu" else t("Install Kokoro"))
            if mode == "gpu":
                self._set_test_status(
                    label,
                    "warn",
                    "Kokoro is not installed. The selected device will install GPU support and may download several GB.",
                )
            else:
                self._set_test_status(label, "warn", "Kokoro is not installed.")

    def _apply_kokoro_assets_status(self, *, installed: bool, assets: dict[str, object] | None) -> None:
        """Show the voice-asset repair/update button when the local check asks for it."""
        button = getattr(self, "_kokoro_assets_btn", None)
        if not isinstance(button, QPushButton):
            return
        self._kokoro_assets_mode = ""
        self._kokoro_assets_update_revision = ""
        if not installed or not isinstance(assets, dict):
            button.setVisible(False)
            return
        state = str(assets.get("state") or "")
        update_revision = str(assets.get("update_revision") or "")
        if state in ("damaged", "not_installed"):
            self._kokoro_assets_mode = "repair"
            button.setText(t("Repair voice files"))
            button.setEnabled(True)
            button.setVisible(True)
        elif update_revision:
            self._kokoro_assets_mode = "update"
            self._kokoro_assets_update_revision = update_revision
            button.setText(t("Update voice model"))
            button.setEnabled(True)
            button.setVisible(True)
        else:
            button.setVisible(False)

    def _kokoro_assets_action(self) -> None:
        """Repair damaged Kokoro voice files or apply a model update (user-initiated download)."""
        mode = getattr(self, "_kokoro_assets_mode", "")
        if mode not in ("repair", "update"):
            return
        voice = _get(self._fields["KOKORO_VOICE"]).strip() or "af_heart"
        update_revision = getattr(self, "_kokoro_assets_update_revision", "")
        if mode == "repair":
            title = t("Repair voice files")
            message = t(
                "OpenWand will redownload Kokoro's damaged or missing voice model files "
                "(up to about 330 MB).\n\nContinue?"
            )
        else:
            title = t("Update voice model")
            message = t(
                "OpenWand will download the updated Kokoro voice model (about 330 MB) and switch to it "
                "only after the download is verified. The current voice keeps working if the update fails.\n\n"
                "Continue?"
            )
        answer = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        button = getattr(self, "_kokoro_assets_btn", None)
        if isinstance(button, QPushButton):
            button.setEnabled(False)

        def _run() -> tuple[bool, str]:
            from core import tts, tts_assets

            if mode == "repair":
                tts.prepare_kokoro_assets(voice=voice)
                return True, "Kokoro voice files repaired."
            tts_assets.apply_update(
                tts_assets.KOKORO,
                update_revision,
                voices=tts_assets.parse_voices(voice),
            )
            tts.reset_connections()
            return True, "Kokoro voice model updated."

        self._tts_install_status_checked = False
        self._start_async_test(
            "kokoro_assets",
            self._kokoro_install_status_lbl,
            _run,
            pending_message="Downloading voice model files...",
        )

    def _refresh_kokoro_install_status(self) -> None:
        """Refresh Kokoro install button and status copy."""
        snapshot = self._kokoro_install_snapshot()
        self._apply_kokoro_install_status(
            installed=bool(snapshot.get("installed")),
            mode=str(snapshot.get("mode") or "cpu"),
            install_status=snapshot.get("install_status") if isinstance(snapshot.get("install_status"), dict) else {},
            torch_status=snapshot.get("torch_status") if isinstance(snapshot.get("torch_status"), dict) else {},
            needs_gpu=bool(snapshot.get("needs_gpu")),
        )
        self._tts_install_status_checked = True
        self._tts_install_status_result = None

    def _install_kokoro(self) -> None:
        """Confirm and install optional Kokoro dependencies into OpenWand's Python."""
        from core import optional_deps

        snapshot = self._kokoro_install_snapshot()
        installed = bool(snapshot.get("installed"))
        needs_gpu = bool(snapshot.get("needs_gpu"))
        needs_repair = bool(snapshot.get("needs_repair"))
        voice = _get(self._fields["KOKORO_VOICE"]).strip() or "af_heart"
        lang_code = _get(self._fields["KOKORO_LANG_CODE"]).strip() or "a"
        device = _get(self._fields["KOKORO_DEVICE"]).strip() or "auto"
        mode = str(snapshot.get("mode") or "cpu")
        install_device = "cuda" if mode == "gpu" else "cpu"
        pre_install_packages = optional_deps.kokoro_torch_install_packages(install_device)
        packages = optional_deps.kokoro_install_packages(install_device)
        remove_artifacts = optional_deps.kokoro_remove_artifacts()
        reinstall = bool(installed)
        if installed and needs_gpu and not needs_repair:
            reinstall = False
        base_package_label = f"{optional_deps.KOKORO_PACKAGE}, {optional_deps.SOUNDFILE_PACKAGE}"
        package_label = (
            t("CUDA-enabled Torch")
            if installed and needs_gpu and not needs_repair
            else f"{base_package_label}, {t('CUDA-enabled Torch')}, {t('English speech model')}"
            if mode == "gpu"
            else f"{base_package_label}, {t('English speech model')}"
        )
        storage_note = t(
            "The GPU install downloads several GB and can temporarily require at least 15 GB free for the uv cache, extraction, "
            "and OpenWand's staging folder. It requires an NVIDIA GPU and compatible driver. "
        ) if mode == "gpu" else ""
        action_note = t(
            "OpenWand will upgrade Kokoro's optional package layer with GPU support.\n\n"
            if needs_gpu
            else "OpenWand will reinstall Kokoro in its user-writable optional packages folder.\n\n"
            if reinstall
            else "OpenWand will install Kokoro into its user-writable optional packages folder.\n\n"
        )
        speed = _get(self._fields["KOKORO_SPEED"]).strip() or "1.0"
        sample_rate = _get(self._fields["KOKORO_SAMPLE_RATE"]).strip() or "24000"
        volume = _get(self._fields["TTS_VOLUME"]).strip() or "1.0"
        message = t(
            "{action_note}"
            "Packages: {package_label}\n"
            "Estimated storage: up to about 2 GB for CPU, or several GB for GPU if speech dependencies are missing. "
            "{storage_note}"
            "First use may also download the Kokoro model cache.\n\n"
            "Current Kokoro settings:\n"
            "Voice: {voice}\n"
            "Language code: {lang_code}\n"
            "Device: {device}\n"
            "Speed: {speed}\n"
            "Sample rate: {sample_rate} Hz\n"
            "Volume: {volume}\n\n"
            "Kokoro may also need eSpeak NG installed separately if Test TTS reports a phoneme/espeak error "
            "(Windows: install eSpeak NG; macOS: brew install espeak-ng; Linux: apt install espeak-ng).\n\n"
            "Continue?"
        ).format(
            action_note=action_note,
            package_label=package_label,
            storage_note=storage_note,
            voice=voice,
            lang_code=lang_code,
            device=device,
            speed=speed,
            sample_rate=sample_rate,
            volume=volume,
        )
        answer = QMessageBox.question(
            self,
            t(
                "Install Kokoro GPU support"
                if installed and needs_gpu and not needs_repair
                else "Reinstall Kokoro"
                if reinstall
                else "Install Kokoro"
            ),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._select_installed_tts_provider("kokoro")

        self._install_optional_tts_package(
            test_key="kokoro_install",
            display_name="Kokoro",
            packages=packages,
            pre_install_packages=pre_install_packages,
            remove_artifacts=remove_artifacts,
            button_attr="_kokoro_install_btn",
            status_attr="_kokoro_install_status_lbl",
            success_message=(
                "Kokoro GPU support installed and local voice is ready."
                if mode == "gpu"
                else "Kokoro reinstalled and local voice is ready."
                if reinstall
                else "Kokoro installed and local voice is ready."
            ),
            thread_name="kokoro-install",
            reinstall=reinstall,
            external_plan_extra={
                "post_install": "kokoro_prepare",
                "kokoro_voice": voice,
                # Auto installs the CUDA-capable wheel as well, but lack of a
                # usable GPU is not an error because CPU fallback is expected.
                "kokoro_require_gpu": device.strip().lower() == "cuda",
                "kokoro_install_device": install_device,
                "pre_install_packages": pre_install_packages,
                "settings_updates": {
                    "TTS_PROVIDER": "kokoro",
                    "OPENWAND_TTS_PREFERENCE": "local",
                    "KOKORO_VOICE": voice,
                    "KOKORO_LANG_CODE": lang_code,
                    "KOKORO_DEVICE": device,
                },
            },
            post_install=lambda progress, write_log: self._prepare_kokoro_after_install(
                voice=voice,
                require_gpu=device.strip().lower() == "cuda",
                progress=progress,
                write_log=write_log,
            ),
        )

    @staticmethod
    def _prepare_kokoro_after_install(
        *,
        voice: str,
        require_gpu: bool = False,
        progress: Callable[[str], None],
        write_log: Callable[[str], None],
    ) -> tuple[bool, str]:
        """Download Kokoro runtime assets after pip install succeeds."""
        try:
            from core import optional_deps, tts

            progress("Installing Kokoro: preparing local voice assets for 0s.")
            write_log("[kokoro install] Preparing Kokoro model and voice assets.")
            paths = tts.prepare_kokoro_assets(voice=voice)
            for name, path in sorted(paths.items()):
                write_log(f"[kokoro install] Prepared {name}: {path}")
            runtime_status = optional_deps.kokoro_runtime_import_status_subprocess()
            if runtime_status.get("error") or runtime_status.get("valid") is False:
                detail = str(runtime_status.get("error") or "Kokoro runtime import failed.")
                return False, f"Kokoro installed, but runtime verification failed: {detail}"
            torch_status = optional_deps.kokoro_torch_status_subprocess()
            if torch_status.get("error") or torch_status.get("valid") is False:
                detail = str(torch_status.get("error") or "Torch verification failed.")
                return False, f"Kokoro installed, but Torch verification failed: {detail}"
            if require_gpu and not torch_status.get("cuda_available"):
                detail = optional_deps.kokoro_cuda_failure_detail(torch_status)
                write_log(f"[kokoro install] CUDA Torch verification failed: {detail}")
                return False, f"Kokoro installed, but CUDA Torch verification failed: {detail}"
            if require_gpu:
                return True, "Kokoro GPU support installed and local voice is ready."
            return True, "Kokoro installed and local voice is ready."
        except Exception as exc:  # noqa: BLE001
            write_log(f"[kokoro install] Voice preparation failed: {type(exc).__name__}: {exc}")
            return (
                False,
                "Kokoro package installed, but voice asset preparation failed: "
                f"{exc}. Connect to the internet and click Test TTS once to finish setup.",
            )

    def _install_elevenlabs(self) -> None:
        """Confirm and install optional ElevenLabs dependencies into OpenWand's Python."""
        from core import optional_deps

        installed = self._elevenlabs_installed()
        message_source = (
            "OpenWand will reinstall ElevenLabs support in its user-writable optional packages folder.\n\n"
            "Package: {package}\n\n"
            "Release builds do not bundle ElevenLabs, keeping downloads smaller. "
            "The install may need internet access and will survive OpenWand updates.\n\n"
            "Continue?"
            if installed
            else (
                "OpenWand will install ElevenLabs support into its user-writable optional packages folder.\n\n"
                "Package: {package}\n\n"
                "Release builds do not bundle ElevenLabs, keeping downloads smaller. "
                "The install may need internet access and will survive OpenWand updates.\n\n"
                "Continue?"
            )
        )
        message = t(message_source).format(package=optional_deps.ELEVENLABS_PACKAGE)
        answer = QMessageBox.question(
            self,
            t("Reinstall ElevenLabs" if installed else "Install ElevenLabs"),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._select_installed_tts_provider("elevenlabs")

        self._install_optional_tts_package(
            test_key="elevenlabs_install",
            display_name="ElevenLabs",
            packages=list(optional_deps.ELEVENLABS_INSTALL_PACKAGES),
            button_attr="_elevenlabs_install_btn",
            status_attr="_elevenlabs_install_status_lbl",
            success_message=(
                "ElevenLabs reinstalled. Add your API key, then click Test TTS."
                if installed
                else "ElevenLabs installed. Add your API key, then click Test TTS."
            ),
            thread_name="elevenlabs-install",
            reinstall=installed,
            external_plan_extra={
                "settings_updates": {
                    "TTS_PROVIDER": "elevenlabs",
                    "OPENWAND_TTS_PREFERENCE": "cloud",
                },
            },
        )

    def _select_installed_tts_provider(self, provider: str) -> None:
        """Select the provider whose installer the user just confirmed."""
        fields = getattr(self, "_fields", {})
        widget = fields.get("TTS_PROVIDER") if isinstance(fields, dict) else None
        if widget is None:
            return
        _set(widget, provider)
        self._update_tts_provider_fields()

    def _install_live_voice(self) -> None:
        """Confirm and install the optional google-genai package for live voice."""
        from core import optional_deps

        installed = self._live_voice_installed()
        message_source = (
            "OpenWand will reinstall live voice support (google-genai) in its user-writable optional packages folder.\n\n"
            "Package: {package}\n\n"
            "The install may need internet access and will survive OpenWand rebuilds.\n\n"
            "Continue?"
            if installed
            else (
                "OpenWand will install live voice support (google-genai) into its user-writable optional packages folder.\n\n"
                "Package: {package}\n\n"
                "The install may need internet access and will survive OpenWand rebuilds.\n\n"
                "Continue?"
            )
        )
        message = t(message_source).format(package=optional_deps.GOOGLE_GENAI_PACKAGE)
        answer = QMessageBox.question(
            self,
            t("Reinstall live voice" if installed else "Install live voice"),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._install_optional_tts_package(
            test_key="live_voice_install",
            display_name="Live voice",
            packages=[optional_deps.GOOGLE_GENAI_PACKAGE],
            button_attr="_live_voice_install_btn",
            status_attr="_live_voice_install_status_lbl",
            success_message="Live voice installed. Press the toggle hotkey to start a conversation.",
            thread_name="live-voice-install",
            # google-genai itself is never imported by a running OpenWand, but its
            # dependencies (charset_normalizer, pydantic, ...) overlap with
            # already-loaded optional packages, so Windows still needs the
            # staged restart-apply path to replace locked .pyd files.
            reinstall=installed,
        )

    def _try_launch_external_optional_tts_install(
        self,
        *,
        test_key: str,
        display_name: str,
        packages: list[str],
        pre_install_packages: list[str] | None = None,
        remove_artifacts: list[str] | None = None,
        button_attr: str,
        status_attr: str,
        external_plan_extra: dict[str, object] | None = None,
        reinstall: bool = False,
    ) -> bool:
        """Launch an optional speech install in a OpenWand-owned installer window."""
        if os.environ.get("OPENWAND_OPTIONAL_INSTALL_INLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
            return False
        status = getattr(self, status_attr, None)
        button = getattr(self, button_attr, None)
        if not isinstance(status, QLabel):
            return False
        try:

            command, root, log_path, status_path = _optional_install_plan_command(
                display_name=display_name,
                packages=packages,
                pre_install_packages=pre_install_packages,
                remove_artifacts=remove_artifacts,
                reinstall=reinstall,
                external_plan_extra=external_plan_extra,
            )
            _write_optional_install_status(
                status_path,
                ok=None,
                message=f"{display_name} install is running.",
            )
            dialog = OptionalInstallDialog(
                title=t("OpenWand {display_name} installer").format(display_name=display_name),
                subtitle=t("Installing {display_name} into OpenWand's optional packages folder.").format(
                    display_name=display_name
                ),
                command=command,
                cwd=root,
                log_path=log_path,
                status_path=status_path,
                env=_optional_install_env(),
                mirror_output_to_log=False,
                parent=self,
                auto_start=True,
            )
        except Exception as exc:  # noqa: BLE001
            self._set_test_status(status, "warn", f"Installer window could not be opened; continuing inside Settings: {exc}")
            return False

        dialogs = getattr(self, "_optional_install_dialogs", None)
        if not isinstance(dialogs, list):
            dialogs = []
            self._optional_install_dialogs = dialogs
        dialogs.append(dialog)
        dialog.install_finished.connect(
            lambda code, _dialog=dialog: self._finish_optional_install_dialog(
                test_key=test_key,
                display_name=display_name,
                button_attr=button_attr,
                status_attr=status_attr,
                exit_code=int(code),
                dialog=_dialog,
            )
        )
        dialog.destroyed.connect(lambda _obj=None, _dialog=dialog: self._forget_optional_install_dialog(_dialog))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        from ui.runtime_log_bridge import log_event

        log_event("installer", "info", f"{display_name} install started (installer window).")

        if isinstance(button, QPushButton):
            button.setEnabled(False)
            button.setText(t(f"Installing {display_name}..."))
        self._set_test_pending(
            status,
            "Installer opened in a OpenWand installer window. Progress and errors will appear there.",
        )
        return True

    def _forget_optional_install_dialog(self, dialog: OptionalInstallDialog) -> None:
        """Drop a finished optional installer dialog reference."""
        dialogs = getattr(self, "_optional_install_dialogs", None)
        if isinstance(dialogs, list) and dialog in dialogs:
            dialogs.remove(dialog)

    def _finish_optional_install_dialog(
        self,
        *,
        test_key: str,
        display_name: str,
        button_attr: str,
        status_attr: str,
        exit_code: int,
        dialog: OptionalInstallDialog,
    ) -> None:
        """Refresh Settings after a OpenWand-owned optional installer exits."""
        button = getattr(self, button_attr, None)
        status = getattr(self, status_attr, None)
        if isinstance(button, QPushButton):
            button.setEnabled(True)
            if test_key == "stt_install":
                button.setText(t("Install STT"))
            elif test_key == "kokoro_install":
                button.setText(t("Install Kokoro"))
            elif test_key == "elevenlabs_install":
                button.setText(t("Install ElevenLabs"))
            elif test_key == "live_voice_install":
                button.setText(t("Install live voice"))
        if isinstance(status, QLabel):
            message = ""
            ok: bool | str = exit_code == 0
            install_status: dict[str, object] = {}
            try:
                from core import optional_deps

                install_status = _read_optional_install_status(display_name, optional_deps.OPTIONAL_PACKAGES_DIR)
                if install_status:
                    if install_status.get("ok") is None:
                        ok = exit_code == 0
                    else:
                        ok = bool(install_status.get("ok"))
                    message = str(install_status.get("message") or "")
            except Exception:
                message = ""
            from ui.runtime_log_bridge import log_event

            if install_status.get("restart_apply"):
                if isinstance(button, QPushButton):
                    button.setEnabled(True)
                    button.setText(t("Restart app now"))
                    _disconnect_clicked_handlers(button)
                    button.clicked.connect(lambda _checked=False: QApplication.instance().quit() if QApplication.instance() else None)
                self._set_test_pending(status, f"{display_name} packages are staged. Click Restart app now to close OpenWand and apply them.")
                log_event("installer", "info", f"{display_name} packages are staged; restart OpenWand to apply them.")
                return
            if not message:
                message = (
                    f"{display_name} installed successfully."
                    if exit_code == 0
                    else f"{display_name} install failed with exit code {exit_code}."
                )
            log_event("installer", "info" if ok else "error", message)
            self._set_test_status(status, ok, _translate_status_message(message))

        if test_key == "kokoro_install":
            self._tts_install_status_checked = False
            self._tts_install_status_result = None
            self._refresh_tts_optional_install_status()
        elif test_key == "elevenlabs_install":
            self._tts_install_status_checked = False
            self._tts_install_status_result = None
            self._refresh_tts_optional_install_status()
        elif test_key == "live_voice_install":
            self._refresh_live_voice_install_status()
        elif test_key == "stt_install":
            self._refresh_stt_active_backend()
        if getattr(dialog, "exit_code", None) == 0:
            self._forget_optional_install_dialog(dialog)

    def _install_optional_tts_package(
        self,
        *,
        test_key: str,
        display_name: str,
        packages: list[str],
        pre_install_packages: list[str] | None = None,
        remove_artifacts: list[str] | None = None,
        button_attr: str,
        status_attr: str,
        success_message: str,
        thread_name: str,
        external_plan_extra: dict[str, object] | None = None,
        post_install_progress_detail: str | None = None,
        reinstall: bool = False,
        post_install: Callable[[Callable[[str], None], Callable[[str], None]], tuple[bool, str]] | None = None,
    ) -> None:
        """Install optional TTS packages via the staged installer.

        Both the installer-window path and the inline fallback stage the
        packages and apply them on the next restart, so the live package
        folder is never modified while OpenWand runs. ``post_install`` and
        ``post_install_progress_detail`` are unused here: verification runs
        in the apply helper via the plan's ``post_install`` field.
        """
        if self._try_launch_external_optional_tts_install(
            test_key=test_key,
            display_name=display_name,
            packages=packages,
            pre_install_packages=pre_install_packages,
            remove_artifacts=remove_artifacts,
            button_attr=button_attr,
            status_attr=status_attr,
            external_plan_extra=external_plan_extra,
            reinstall=reinstall,
        ):
            return
        button = getattr(self, button_attr, None)
        if isinstance(button, QPushButton):
            button.setEnabled(False)
        status = getattr(self, status_attr, None)
        if not isinstance(status, QLabel):
            return

        token = self._latest_test_token.get(test_key, 0) + 1
        self._latest_test_token[test_key] = token
        self._running_test_tokens.add((test_key, token))
        self._set_test_pending(status, f"Installing {display_name}: starting installer.")
        from ui.runtime_log_bridge import log_event

        log_event("installer", "info", f"{display_name} install started (inline).")

        def _progress(message: str) -> None:
            self._queue_test_progress(test_key, token, message)

        def _runner() -> tuple[bool, str]:
            """Install optional packages with pip into OpenWand's user package folder."""
            from core import optional_deps

            log_path = _optional_install_log_path(display_name, optional_deps.OPTIONAL_PACKAGES_DIR)
            status_path = _optional_install_status_path(display_name, optional_deps.OPTIONAL_PACKAGES_DIR)
            log_prefix = f"[{display_name.lower()} install]"
            tail: list[str] = []
            last_progress = f"Installing {display_name}: starting installer."
            started_at = time.monotonic()
            last_output_at = {"value": started_at}
            stop_heartbeat = threading.Event()
            no_output_timeout = _optional_install_no_output_timeout_seconds()
            stopped_reason = {"value": ""}
            current_process: dict[str, subprocess.Popen | None] = {"value": None}

            def _write_log(line: str) -> None:
                try:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(line.rstrip() + "\n")
                except Exception:
                    pass

            _write_optional_install_status(
                status_path,
                ok=None,
                message=f"{display_name} install is running.",
            )

            if not packages and not pre_install_packages:
                return False, f"No packages selected for {display_name} install."
            # This fallback runs the same staged installer as the installer
            # window: pip writes into a staging folder and the live package
            # dir is only touched after OpenWand exits, so a locked DLL cannot
            # corrupt a working install. The staged installer owns artifact
            # cleanup, logging, and post-install verification.
            try:
                install_command, install_root, _log_path, _status_path = _optional_install_plan_command(
                    display_name=display_name,
                    packages=packages,
                    pre_install_packages=pre_install_packages,
                    remove_artifacts=remove_artifacts,
                    reinstall=reinstall,
                    external_plan_extra=external_plan_extra,
                )
            except Exception as exc:
                message = f"{display_name} install failed: {exc}"
                _write_optional_install_status(status_path, ok=False, message=message)
                return False, message

            def _heartbeat() -> None:
                while not stop_heartbeat.wait(20.0):
                    elapsed = int(time.monotonic() - started_at)
                    quiet = int(time.monotonic() - last_output_at["value"])
                    _progress(_optional_install_elapsed_text(display_name, elapsed, quiet))
                    process = current_process["value"]
                    if no_output_timeout > 0 and process is not None and quiet >= no_output_timeout and process.poll() is None:
                        reason = (
                            f"Installer produced no output for {_format_duration(quiet)} "
                            f"after: {tail[-1] if tail else 'starting installer'}"
                        )
                        stopped_reason["value"] = reason
                        _write_log(f"{log_prefix} {reason}; stopping installer.")
                        _progress(f"Installing {display_name}: stalled; stopping installer.")
                        try:
                            process.terminate()
                            process.wait(timeout=5)
                        except Exception:
                            try:
                                process.kill()
                            except Exception:
                                pass
                        return

            commands = [("running the staged installer", install_command)]

            threading.Thread(target=_heartbeat, daemon=True, name=f"{display_name.lower()}-install-heartbeat").start()
            returncode = 0
            try:
                for phase, command in commands:
                    _progress(f"Installing {display_name}: {phase}.")
                    print(f"{log_prefix} Running: {' '.join(command)}", flush=True)
                    # debug: the print above already reaches stderr (and the
                    # runtime event log) — an info record would duplicate it.
                    _settings_log.debug("Installing %s with command: %s", display_name, command)
                    _write_log(f"{log_prefix} Running: {' '.join(command)}")
                    try:
                        process = subprocess.Popen(
                            command,
                            cwd=str(install_root),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            bufsize=1,
                            env=_optional_install_env(),
                            **optional_deps.subprocess_no_window_kwargs(),
                        )
                    except Exception as exc:
                        print(f"{log_prefix} Failed to start installer: {exc}", flush=True)
                        _settings_log.exception("%s install could not start installer", display_name)
                        _write_log(f"{log_prefix} Failed to start installer: {exc}")
                        message = f"{display_name} install failed: {exc}"
                        _write_optional_install_status(status_path, ok=False, message=message)
                        return False, message
                    assert process.stdout is not None
                    current_process["value"] = process
                    print(f"{log_prefix} installer started with pid {process.pid}", flush=True)
                    _write_log(f"{log_prefix} installer started with pid {process.pid}")
                    for raw_line in process.stdout:
                        line = raw_line.strip()
                        if not line:
                            continue
                        last_output_at["value"] = time.monotonic()
                        # The staged installer already writes its own lines to
                        # the shared log file; only mirror them to the console.
                        print(f"{log_prefix} {line}", flush=True)
                        _settings_log.debug("%s install: %s", display_name, line)
                        tail.append(line)
                        tail = tail[-30:]
                        progress_message = _optional_install_progress_text(line, display_name)
                        if progress_message != last_progress:
                            _progress(progress_message)
                            last_progress = progress_message
                    returncode = process.wait()
                    current_process["value"] = None
                    if returncode != 0:
                        break
            finally:
                current_process["value"] = None
                stop_heartbeat.set()
            if returncode == 0:
                importlib.invalidate_caches()
                optional_deps.add_optional_packages_to_path()
                # The staged installer wrote the durable status: usually
                # "packages are staged, restart to apply". Verification runs
                # in the apply helper after OpenWand exits, not in this process.
                install_status = _read_optional_install_status(display_name, optional_deps.OPTIONAL_PACKAGES_DIR)
                message = str(install_status.get("message") or "").strip() or success_message
                if install_status.get("ok") is False:
                    return False, message
                print(f"{log_prefix} {message}", flush=True)
                _progress(message)
                return True, message
            detail = _optional_install_failure_detail(tail)
            if stopped_reason["value"]:
                detail = stopped_reason["value"]
            _write_log(f"{log_prefix} Failed with exit code {returncode}: {detail}")
            _progress(f"{display_name} install failed: {detail}")
            message = f"{display_name} install failed: {detail}"
            _write_optional_install_status(status_path, ok=False, message=message)
            return False, message

        def _worker() -> None:
            try:
                ok, result_message = _runner()
                failure_detail = ""
            except Exception as exc:
                ok, result_message = False, f"{display_name} install failed: {exc}"
                failure_detail = traceback.format_exc()
            from ui.runtime_log_bridge import log_event

            log_event("installer", "info" if ok else "error", result_message, detail=failure_detail)
            with self._pending_test_results_lock:
                self._pending_test_results.append((test_key, token, ok, result_message))

        threading.Thread(target=_worker, daemon=True, name=thread_name).start()
        if not self._test_result_timer.isActive():
            self._test_result_timer.start()

    def _tab_prompt(self) -> QWidget:
        """Handle tab prompt for settings dialog."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        # ── SYSTEM PROMPT card ────────────────────────────────────────────
        prompt_card, prompt_cv = self._card("System Prompt")
        prompt_tabs = QTabWidget()
        prompt_tabs.setObjectName("systemPromptModeTabs")

        def add_prompt_tab(
            label: str,
            field_key: str,
            placeholder: str = "",
        ) -> None:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(8, 10, 8, 8)
            layout.setSpacing(8)
            editor = QTextEdit()
            editor.setMinimumHeight(260)
            editor.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )
            if placeholder:
                # QTextEdit renders this as non-editable palette placeholder text;
                # it is never returned by toPlainText() or persisted to .env.
                editor.setPlaceholderText(t(placeholder))
            self._fields[field_key] = editor
            layout.addWidget(editor, stretch=1)
            prompt_tabs.addTab(page, t(label))

        add_prompt_tab(
            "OpenWand",
            "SYSTEM_PROMPT_UTILITY",
        )
        add_prompt_tab(
            "ChatGPT",
            "OPENWAND_CODEX_SYSTEM_PROMPT",
            placeholder="Optional instructions for ChatGPT conversations. Leave blank to use ChatGPT's native instructions.",
        )
        add_prompt_tab(
            "Claude",
            "OPENWAND_CLAUDE_SYSTEM_PROMPT",
            placeholder="Optional instructions for Claude conversations. Leave blank to use Claude's native instructions.",
        )
        self._system_prompt_tabs = prompt_tabs
        prompt_cv.addWidget(prompt_tabs, stretch=1)
        outer.addWidget(prompt_card, stretch=1)

        scroll.setWidget(w)
        return scroll

    def _tab_keybinds(self) -> QWidget:
        """Build categorized shortcut rows with inline per-action editors."""
        from PySide6.QtWidgets import QScrollArea

        container = QWidget()
        outer_layout = QVBoxLayout(container)
        outer_layout.setSpacing(12)
        outer_layout.setContentsMargins(12, 12, 12, 12)

        self._shortcut_rows: list[dict] = []
        self._shortcut_sections: list[dict] = []
        self._expanded_shortcut_detail: QWidget | None = None

        search = QLineEdit()
        search.setObjectName("shortcutSearch")
        search.setPlaceholderText(t("Find a shortcut…"))
        search.setClearButtonEnabled(True)
        search.textChanged.connect(self._filter_shortcut_rows)
        self._shortcut_search = search
        outer_layout.addWidget(search)

        # Global shortcuts: intent-overlay callers and region snipping.
        global_card, global_rows = self._create_shortcut_section(
            "Global shortcuts",
            add_label="Add intent shortcut",
            add_callback=lambda: self._add_caller_block(),
        )
        self._global_shortcut_rows = global_rows

        self._callers_container = QWidget()
        self._callers_vlayout = QVBoxLayout(self._callers_container)
        self._callers_vlayout.setSpacing(0)
        self._callers_vlayout.setContentsMargins(0, 0, 0, 0)
        global_rows.addWidget(self._callers_container)
        self._caller_blocks: list[dict] = []
        self._build_snip_block(global_rows)

        timeout_row = QWidget()
        timeout_form = _expanding_form_layout(timeout_row)
        timeout_form.setContentsMargins(8, 8, 8, 0)
        self._fields["INTENT_OVERLAY_TIMEOUT_MS"] = QLineEdit()
        self._fields["INTENT_OVERLAY_TIMEOUT_MS"].setPlaceholderText("60000")
        intent_timeout_tip = (
            "How long the intent overlay stays open before closing itself. "
            "Use 0 to keep it open until you choose or cancel."
        )
        timeout_form.addRow(
            _tooltip_label("Intent overlay timeout (ms)", intent_timeout_tip),
            self._fields["INTENT_OVERLAY_TIMEOUT_MS"],
        )
        self._fields["INTENT_CONTEXT_TOGGLE_KEYS"] = QLineEdit()
        self._fields["INTENT_CONTEXT_TOGGLE_KEYS"].setPlaceholderText("12345678")
        self._fields["INTENT_CONTEXT_TOGGLE_KEYS"].hide()
        global_rows.addWidget(timeout_row)
        outer_layout.addWidget(global_card)

        # Voice shortcuts.
        voice_card, voice_rows = self._create_shortcut_section(
            "Voice shortcuts",
        )

        voice_detail, voice_cv = self._shortcut_detail()
        voice_note_text = (
            "Hold the key, speak, release to transcribe and ask. "
            "Context and tools below apply to voice queries, just like a caller hotkey."
        )
        voice_note = QLabel(
            f"<small>{t(voice_note_text)}</small>"
        )
        voice_note.setWordWrap(True)
        voice_cv.addWidget(voice_note)
        self._fields["VOICE_REVIEW_TRANSCRIPT"] = QCheckBox(
            t("Review transcript and context before asking")
        )
        self._fields["VOICE_REVIEW_TRANSCRIPT"].setToolTip(
            t("After F9 transcription, open the intent overlay with the transcript in the custom prompt field.")
        )
        voice_cv.addWidget(self._fields["VOICE_REVIEW_TRANSCRIPT"])
        voice_tools_btn = QPushButton(t("Allowed tools…"))
        voice_tools_btn.setToolTip(t("Choose which installed/addon tools voice queries may use"))
        voice_tools_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        voice_cv.addWidget(voice_tools_btn, 0, Qt.AlignmentFlag.AlignLeft)

        voice_context_row, voice_controls = self._build_context_controls()
        voice_cv.addWidget(voice_context_row)
        self._voice_block: dict = {
            **voice_controls,
            "tool_overrides": {},
            "detail": voice_detail,
        }
        voice_tools_btn.clicked.connect(
            lambda: self._open_tool_access_dialog(self._voice_block, "Voice")
        )
        voice_hotkey = HotkeyCaptureEdit()
        voice_hotkey_2 = HotkeyCaptureEdit()
        voice_enabled = QCheckBox()
        self._fields["HOTKEY_VOICE"] = voice_hotkey
        self._fields["HOTKEY_VOICE_2"] = voice_hotkey_2
        self._fields["HOTKEY_VOICE_ENABLED"] = voice_enabled
        self._add_shortcut_row(
            voice_rows,
            "Hold to ask by voice",
            "Hold while speaking, then release to transcribe and ask.",
            voice_hotkey,
            voice_hotkey_2,
            voice_enabled,
            voice_detail,
        )

        dictate_detail, dictate_cv = self._shortcut_detail()
        dictate_note_text = (
            "Hold the key, speak, release — the transcript is typed straight into "
            "whatever text field has focus (no assistant). Leave the hotkey empty to disable."
        )
        dictate_note = QLabel(
            f"<small>{t(dictate_note_text)}</small>"
        )
        dictate_note.setWordWrap(True)
        dictate_cv.addWidget(dictate_note)

        dictate_mode = _NoScrollCombo()
        for label, value in _DICTATE_MODE_OPTIONS:
            dictate_mode.addItem(t(label), value)
        dictate_mode_tip = t(
            "Raw pastes exactly what Whisper heard (fast, fully local). LLM cleanup runs the "
            "transcript through your configured model for punctuation/filler cleanup before pasting."
        )
        self._fields["DICTATE_MODE"] = dictate_mode
        dictate_form = QWidget()
        dictate_f = _expanding_form_layout(dictate_form)
        dictate_f.setContentsMargins(0, 0, 0, 0)
        dictate_f.setSpacing(8)
        dictate_f.addRow(_tooltip_label("Dictated text", dictate_mode_tip), self._fields["DICTATE_MODE"])
        dictate_cv.addWidget(dictate_form)
        dictate_hotkey = HotkeyCaptureEdit()
        dictate_hotkey_2 = HotkeyCaptureEdit()
        dictate_enabled = QCheckBox()
        self._fields["HOTKEY_DICTATE"] = dictate_hotkey
        self._fields["HOTKEY_DICTATE_2"] = dictate_hotkey_2
        self._fields["HOTKEY_DICTATE_ENABLED"] = dictate_enabled
        self._add_shortcut_row(
            voice_rows,
            "Hold to dictate",
            "Type speech into the focused field without asking the assistant.",
            dictate_hotkey,
            dictate_hotkey_2,
            dictate_enabled,
            dictate_detail,
        )
        self._kb_special_row(
            voice_rows, "HOTKEY_VOICE_LIVE", "Toggle live conversation",
            "Start or stop a live voice conversation.",
        )
        self._kb_special_row(
            voice_rows, "HOTKEY_READ_SELECTION_ALOUD", "Read selection aloud",
            "Speak selected text with the configured text-to-speech provider.",
        )
        outer_layout.addWidget(voice_card)

        # Context shortcuts.
        context_card, context_rows = self._create_shortcut_section(
            "Context shortcuts",
        )
        self._kb_special_row(
            context_rows, "HOTKEY_ADD_CONTEXT", "Add selection as context",
            "Append selected text to the context buffer.",
        )
        self._kb_special_row(
            context_rows, "HOTKEY_CLEAR_CONTEXT", "Clear context",
            "Remove every item from the context buffer.",
        )
        outer_layout.addWidget(context_card)
        outer_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    @staticmethod
    def _configure_shortcut_grid(grid: QGridLayout) -> None:
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setColumnMinimumWidth(0, 28)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, 104)
        grid.setColumnMinimumWidth(3, 104)
        # Reserve the full Details-button width even on rows without details.
        # Otherwise the flexible Action column grows on those rows and pushes
        # both shortcut editors into different horizontal positions.
        grid.setColumnMinimumWidth(4, 104)
        # Keep deletion in its own narrow final column. Non-removable rows use
        # a same-size ghost so every shortcut row remains vertically aligned.
        grid.setColumnMinimumWidth(5, 36)

    def _create_shortcut_section(
        self,
        title: str,
        *,
        add_label: str = "",
        add_callback: Callable[[], None] | None = None,
    ) -> tuple[QFrame, QVBoxLayout]:
        card, layout = self._card("")
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        heading = QWidget()
        heading.setMinimumWidth(0)
        heading.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        heading_layout = QVBoxLayout(heading)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(2)
        title_label = _WarningHeaderLabel(t(title))
        title_label.setObjectName("shortcutSectionTitle")
        self._register_warning_header(title, title_label, uppercase=False)
        heading_layout.addWidget(title_label)
        header_layout.addWidget(heading, 1)
        if add_label and add_callback:
            add_button = QPushButton(t(add_label))
            add_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            add_button.clicked.connect(add_callback)
            header_layout.addWidget(add_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(header)

        columns = QWidget()
        columns_grid = QGridLayout(columns)
        columns_grid.setContentsMargins(8, 2, 8, 2)
        self._configure_shortcut_grid(columns_grid)
        for column, label in enumerate(("On", "Action", "Shortcut 1", "Shortcut 2", "Details")):
            cell = QLabel(t(label))
            cell.setStyleSheet("color: palette(placeholder-text);")
            columns_grid.addWidget(cell, 0, column)
        remove_header = QWidget()
        remove_header.setFixedWidth(36)
        remove_header.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        columns_grid.addWidget(remove_header, 0, 5)
        layout.addWidget(columns)

        rows = QWidget()
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(0)
        layout.addWidget(rows)
        section = {"widget": card, "rows": rows_layout, "entries": []}
        self._shortcut_sections.append(section)
        return card, rows_layout

    def _shortcut_detail(self) -> tuple[QFrame, QVBoxLayout]:
        detail = QFrame()
        detail.setObjectName("shortcutInlineEditor")
        detail.setFrameShape(QFrame.Shape.StyledPanel)
        detail.setStyleSheet(
            "QFrame#shortcutInlineEditor { border: 1px solid palette(mid); "
            "border-radius: 6px; background: palette(base); }"
        )
        layout = QVBoxLayout(detail)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        return detail, layout

    def _add_shortcut_row(
        self,
        rows_layout: QVBoxLayout,
        title: str,
        description: str,
        primary: HotkeyCaptureEdit,
        secondary: HotkeyCaptureEdit,
        enabled: QCheckBox,
        detail: QWidget | None = None,
        *,
        section_rows_layout: QVBoxLayout | None = None,
        remove_callback: Callable[[], None] | None = None,
    ) -> QFrame:
        wrapper = QFrame()
        wrapper.setObjectName("shortcutRowWrapper")
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(4)

        row = QFrame()
        row.setObjectName("shortcutRow")
        row.setStyleSheet("QFrame#shortcutRow { border-top: 1px solid palette(mid); }")
        grid = QGridLayout(row)
        grid.setContentsMargins(8, 8, 8, 8)
        self._configure_shortcut_grid(grid)
        enabled.setToolTip(t("Enable this shortcut"))
        grid.addWidget(enabled, 0, 0, Qt.AlignmentFlag.AlignTop)

        action = QWidget()
        action.setMinimumWidth(0)
        action.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        action_layout = QVBoxLayout(action)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(2)
        action_title = QLabel(t(title))
        action_description = QLabel(t(description))
        action_description.setWordWrap(True)
        for label in (action_title, action_description):
            label.setMinimumWidth(0)
            label.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
        action_description.setStyleSheet("color: palette(placeholder-text);")
        action_layout.addWidget(action_title)
        action_layout.addWidget(action_description)
        grid.addWidget(action, 0, 1)

        for column, edit in ((2, primary), (3, secondary)):
            edit.setPlaceholderText(t("Assign shortcut"))
            edit.setFixedWidth(104)
            edit.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            grid.addWidget(edit, 0, column, Qt.AlignmentFlag.AlignTop)

        customize: QPushButton | None = None
        if detail is not None:
            detail.setVisible(False)
            customize = QPushButton(t("Customize"))
            customize.setStyleSheet(
                "QPushButton { padding-left: 8px; padding-right: 8px; }"
            )
            customize.setFixedWidth(104)
            customize.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            customize.clicked.connect(
                lambda _checked=False, panel=detail, button=customize: self._toggle_shortcut_detail(panel, button)
            )
            grid.addWidget(customize, 0, 4, Qt.AlignmentFlag.AlignTop)
            wrapper_layout.addWidget(row)
            wrapper_layout.addWidget(detail)
        else:
            details_ghost = QWidget()
            details_ghost.setObjectName("shortcutDetailsGhost")
            details_ghost.setFixedWidth(104)
            details_ghost.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
            )
            details_ghost.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
            grid.addWidget(details_ghost, 0, 4, Qt.AlignmentFlag.AlignTop)
            wrapper_layout.addWidget(row)

        if remove_callback is not None:
            remove_button = QPushButton("×")
            remove_button.setObjectName("shortcutRemoveButton")
            remove_button.setAccessibleName(t("Remove intent shortcut"))
            remove_button.setToolTip(t("Remove intent shortcut"))
            remove_button.setFixedSize(36, 32)
            remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_button.setStyleSheet(
                "QPushButton { color: #e06464; background: transparent; border: none; "
                "font-size: 18pt; font-weight: 700; padding: 0; }"
                "QPushButton:hover { color: #ff7b7b; background: #3b2929; "
                "border-radius: 6px; }"
                "QPushButton:pressed { color: #c94f4f; }"
            )
            remove_button.clicked.connect(remove_callback)
            grid.addWidget(remove_button, 0, 5, Qt.AlignmentFlag.AlignTop)
        else:
            remove_ghost = QWidget()
            remove_ghost.setObjectName("shortcutRemoveGhost")
            remove_ghost.setFixedWidth(36)
            remove_ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            grid.addWidget(remove_ghost, 0, 5, Qt.AlignmentFlag.AlignTop)

        def _set_enabled(checked: bool) -> None:
            primary.setEnabled(checked)
            secondary.setEnabled(checked)

        enabled.toggled.connect(_set_enabled)
        _set_enabled(enabled.isChecked())
        rows_layout.addWidget(wrapper)
        entry = {
            "widget": wrapper,
            "title": title,
            "description": description,
            "detail": detail,
            "title_label": action_title,
        }
        if not hasattr(self, "_shortcut_rows"):
            self._shortcut_rows = []
        self._shortcut_rows.append(entry)
        owner_layout = section_rows_layout or rows_layout
        for section in getattr(self, "_shortcut_sections", []):
            if section["rows"] is owner_layout:
                section["entries"].append(entry)
                break
        return wrapper

    def _toggle_shortcut_detail(self, detail: QWidget, button: QPushButton) -> None:
        opening = not detail.isVisible()
        previous = getattr(self, "_expanded_shortcut_detail", None)
        if previous is not None and previous is not detail:
            previous.setVisible(False)
            previous_button = previous.property("shortcutToggleButton")
            if isinstance(previous_button, QPushButton):
                previous_button.setText(t("Customize"))
        detail.setProperty("shortcutToggleButton", button)
        detail.setVisible(opening)
        button.setText(t("Close") if opening else t("Customize"))
        self._expanded_shortcut_detail = detail if opening else None

    def _filter_shortcut_rows(self, text: str) -> None:
        query = text.strip().lower()
        for entry in getattr(self, "_shortcut_rows", []):
            haystack = f"{t(entry['title'])} {t(entry['description'])}".lower()
            entry["widget"].setVisible(not query or query in haystack)
        for section in getattr(self, "_shortcut_sections", []):
            section["widget"].setVisible(
                any(not entry["widget"].isHidden() for entry in section["entries"])
            )

    def _kb_special_row(
        self,
        rows_layout: QVBoxLayout,
        key: str,
        label_text: str,
        description: str,
    ) -> tuple[HotkeyCaptureEdit, HotkeyCaptureEdit, QCheckBox]:
        primary = HotkeyCaptureEdit()
        secondary = HotkeyCaptureEdit()
        enabled = QCheckBox()
        self._fields[key] = primary
        self._fields[f"{key}_2"] = secondary
        self._fields[f"{key}_ENABLED"] = enabled
        self._add_shortcut_row(
            rows_layout, label_text, description, primary, secondary, enabled
        )
        return primary, secondary, enabled

    def _build_snip_block(self, rows_layout: QVBoxLayout) -> QFrame:
        """Build the snip row and its inline context/tool editor."""
        detail, outer = self._shortcut_detail()
        tools_btn = QPushButton(t("Allowed tools…"))
        tools_btn.setToolTip(t("Choose which installed/addon tools snip queries may use"))
        tools_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        outer.addWidget(tools_btn, 0, Qt.AlignmentFlag.AlignLeft)

        context_row, context_controls = self._build_context_controls(
            context_screenshot="off",
            screenshot_enabled=False,
        )
        outer.addWidget(context_row)

        self._fields["SNIP_CONTEXT_AMBIENT"] = context_controls["context_ambient"]
        self._fields["SNIP_CONTEXT_CLIPBOARD"] = context_controls["context_clipboard"]
        self._fields["SNIP_CONTEXT_DOCUMENTS_MODE"] = context_controls["context_documents_mode"]
        self._fields["SNIP_CONTEXT_BROWSER_MODE"] = context_controls["context_browser_mode"]
        self._fields["SNIP_CONTEXT_GITHUB_MODE"] = context_controls["context_github_mode"]
        self._fields["SNIP_CONTEXT_MEMORY_MODE"] = context_controls["context_memory_mode"]
        self._fields["SNIP_FILE_ACCESS"] = context_controls["file_access"]
        self._snip_block: dict = {**context_controls, "tool_overrides": {}}
        tools_btn.clicked.connect(
            lambda: self._open_tool_access_dialog(self._snip_block, "Snip screen region")
        )
        primary = HotkeyCaptureEdit()
        secondary = HotkeyCaptureEdit()
        enabled = QCheckBox()
        self._fields["HOTKEY_SNIP"] = primary
        self._fields["HOTKEY_SNIP_2"] = secondary
        self._fields["HOTKEY_SNIP_ENABLED"] = enabled
        self._snip_block.update(
            {
                "hotkey": primary,
                "hotkey_2": secondary,
                "enabled": enabled,
                "detail": detail,
            }
        )
        return self._add_shortcut_row(
            rows_layout,
            "Snip screen region",
            "Select a screen region, attach it, and open the intent overlay.",
            primary,
            secondary,
            enabled,
            detail,
        )

    def _intent_context_keys(self) -> str:
        """Return the configured context toggle keys, padded for all sources."""
        try:
            raw = _get(self._fields.get("INTENT_CONTEXT_TOGGLE_KEYS"))  # type: ignore[arg-type]
        except Exception:
            raw = ""
        raw = raw or getattr(self, "_env", {}).get("INTENT_CONTEXT_TOGGLE_KEYS", "12345678")
        keys: list[str] = []
        for ch in str(raw or "") + "12345678":
            if ch.isspace() or ch in keys:
                continue
            keys.append(ch)
            if len(keys) >= 8:
                break
        return "".join(keys)

    def _build_context_controls(
        self,
        *,
        context_ambient: bool = False,
        context_clipboard: bool = False,
        context_documents_mode: str = "off",
        context_browser_mode: str = "off",
        context_github_mode: str = "off",
        context_memory_mode: str = "off",
        context_screenshot: str = "off",
        file_access: str = "off",
        screenshot_enabled: bool = True,
    ) -> tuple[QWidget, dict]:
        """Build the shared per-hotkey context grid (used by callers and voice).

        Returns (row_widget, controls) where controls holds the ambient checkbox
        and the five mode combos keyed exactly like the caller block dict.
        """
        return context_controls.build_context_controls(
            intent_context_keys=self._intent_context_keys(),
            on_changed=self._schedule_warning_marker_refresh,
            context_ambient=context_ambient,
            context_clipboard=context_clipboard,
            context_documents_mode=context_documents_mode,
            context_browser_mode=context_browser_mode,
            context_github_mode=context_github_mode,
            context_memory_mode=context_memory_mode,
            context_screenshot=context_screenshot,
            file_access=file_access,
            screenshot_enabled=screenshot_enabled,
        )

    def _open_tool_access_dialog(self, blk: dict, method_label: str) -> None:
        """Open the per-method Allowed Tools dialog and store the result on blk."""
        from ui.settings_panel.tool_access import ToolAccessDialog

        def _mode_data(combo) -> str:
            """Handle mode data for settings dialog."""
            return str(combo.currentData() or "off")

        governed_modes = {
            "Open docs": _mode_data(blk["context_documents_mode"]),
            "Browser/Web": _mode_data(blk["context_browser_mode"]),
            "Git/GitHub": _mode_data(blk["context_github_mode"]),
            "Memory": _mode_data(blk["context_memory_mode"]),
            "Screenshot": _mode_data(blk["context_screenshot"]),
            "Files": _mode_data(blk["file_access"]),
        }
        dlg = ToolAccessDialog(
            self,
            method_label=method_label or "this hotkey",
            overrides=dict(blk.get("tool_overrides") or {}),
            governed_modes=governed_modes,
            extra_tools=self._extra_tools,
        )
        if dlg.exec():
            blk["tool_overrides"] = dlg.selected_overrides()
            self._schedule_warning_marker_refresh()
            self._schedule_dirty_refresh()

    def _add_caller_block(
        self,
        folder: str = "",
        hotkey: str = "",
        hotkey_2: str = "",
        enabled: bool = True,
        label: str = "",
        paste_back: bool = False,
        custom_key: str = "s",
        custom_label: str = "",
        space_starts_new_chat: bool = True,
        context_selection_enabled: bool = True,
        context_ambient: bool = False,
        context_clipboard: bool = False,
        context_documents: bool = False,
        context_tools: bool = False,
        context_documents_mode: str | None = None,
        context_browser_mode: str = "off",
        context_github_mode: str = "off",
        context_memory_mode: str = "off",
        context_screenshot: str = "off",
        file_access: str = "off",
        tools: dict[str, str] | None = None,
        intents: list[dict] | None = None,
        display_language: str | None = None,
    ) -> None:
        """Add an intent shortcut row with its editor directly underneath."""
        detail, outer = self._shortcut_detail()

        hotkey_edit = HotkeyCaptureEdit()
        if hotkey:
            hotkey_edit.setText(hotkey)
        hotkey_edit_2 = HotkeyCaptureEdit()
        if hotkey_2:
            hotkey_edit_2.setText(hotkey_2)
        enabled_check = QCheckBox()
        enabled_check.setChecked(enabled)

        identity = QWidget()
        identity_form = _expanding_form_layout(identity)
        identity_form.setContentsMargins(0, 0, 0, 0)
        source_label = label.strip()
        display_label = (
            t(source_label) if source_label in _BUILTIN_CALLER_LABELS else label
        )
        label_edit = QLineEdit(display_label)
        if source_label in _BUILTIN_CALLER_LABELS:
            label_edit.setProperty("builtinCallerLabel", source_label)
        label_edit.setPlaceholderText(t("Intent shortcut name"))
        identity_form.addRow(
            _tooltip_label(
                "Name",
                "Short name shown for this intent shortcut in settings and tool access dialogs.",
            ),
            label_edit,
        )
        outer.addWidget(identity)

        result_label = QLabel(f"<small><b>{t('Result behavior')}</b></small>")
        outer.addWidget(result_label)
        result_selector = QWidget()
        result_layout = QHBoxLayout(result_selector)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(6)
        result_group = QButtonGroup(result_selector)
        result_group.setExclusive(True)

        show_only_btn = QPushButton(t("Show answer in OpenWand"))
        show_only_btn.setObjectName("settingsSegmentButton")
        show_only_btn.setCheckable(True)
        show_only_btn.setToolTip(
            t("Show the final answer in OpenWand without changing the selected text.")
        )
        paste_cb = QPushButton(t("Rewrite selected text"))
        paste_cb.setObjectName("settingsSegmentButton")
        paste_cb.setCheckable(True)
        paste_cb.setToolTip(
            t("Replace the selected text in the focused application with the final answer.")
        )
        result_group.addButton(show_only_btn)
        result_group.addButton(paste_cb)
        show_only_btn.setChecked(not paste_back)
        paste_cb.setChecked(paste_back)
        for button in (show_only_btn, paste_cb):
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.toggled.connect(self._schedule_dirty_refresh)
            result_layout.addWidget(button, 1)
        outer.addWidget(result_selector)

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        tools_btn = QPushButton(t("Allowed tools…"))
        tools_btn.setToolTip(t("Choose which installed/addon tools this hotkey may use"))
        actions_layout.addWidget(tools_btn)
        actions_layout.addStretch()
        outer.addWidget(actions)

        docs_mode = context_documents_mode or ("auto" if context_documents else ("model" if context_tools else "off"))
        context_row, context_controls = self._build_context_controls(
            context_ambient=context_ambient,
            context_clipboard=context_clipboard,
            context_documents_mode=docs_mode,
            context_browser_mode=context_browser_mode,
            context_github_mode=context_github_mode,
            context_memory_mode=context_memory_mode,
            context_screenshot=context_screenshot,
            file_access=file_access,
        )
        outer.addWidget(context_row)

        # Intent rows column header
        from PySide6.QtWidgets import QSizePolicy as SP
        int_hdr = QWidget()
        int_hdr_h = QHBoxLayout(int_hdr)
        int_hdr_h.setContentsMargins(0, 2, 0, 0)
        int_hdr_h.setSpacing(6)
        for txt, w in [("On", 24), ("Key", 40), ("Label", 130), ("Prompt", 0)]:
            if txt == "Prompt":
                prompt_tip = t(
                    "Instruction sent with this intent. The user's selected text or context is added separately."
                )
                lbl = QLabel(f"<small><b>{t(txt)}  ⓘ</b></small>")
                lbl.setObjectName("settingsInfoLabel")
                lbl.setToolTip(prompt_tip)
                lbl.setCursor(Qt.CursorShape.WhatsThisCursor)
                lbl.setAccessibleName(t(txt))
                lbl.setAccessibleDescription(prompt_tip)
                lbl.setProperty("_openwand_info_source", txt)
                lbl.setProperty("_openwand_info_small_bold", True)
                lbl.setProperty(
                    "_openwand_i18n_tooltip",
                    "Instruction sent with this intent. The user's selected text or context is added separately.",
                )
            else:
                lbl = QLabel(f"<small><b>{t(txt)}</b></small>")
            if w:
                lbl.setFixedWidth(w)
            else:
                lbl.setSizePolicy(SP.Policy.Expanding, SP.Policy.Preferred)
            int_hdr_h.addWidget(lbl)
        int_hdr_h.addSpacing(32)
        outer.addWidget(int_hdr)

        # Intent rows container
        intents_container = QWidget()
        intents_vlayout = QVBoxLayout(intents_container)
        intents_vlayout.setSpacing(2)
        intents_vlayout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(intents_container)

        blk: dict = {
            "folder": folder,
            "space_starts_new_chat": space_starts_new_chat,
            "context_selection_enabled": context_selection_enabled,
            "hotkey":         hotkey_edit,
            "hotkey_2":       hotkey_edit_2,
            "enabled":        enabled_check,
            "detail":         detail,
            "label":          label_edit,
            "paste_back":     paste_cb,
            "show_only":      show_only_btn,
            "result_behavior_group": result_group,
            "context_ambient": context_controls["context_ambient"],
            "context_clipboard": context_controls["context_clipboard"],
            "context_documents": context_controls["context_documents_mode"],
            "context_documents_mode": context_controls["context_documents_mode"],
            "context_browser_mode": context_controls["context_browser_mode"],
            "context_github_mode": context_controls["context_github_mode"],
            "context_memory_mode": context_controls["context_memory_mode"],
            "context_screenshot": context_controls["context_screenshot"],
            "file_access": context_controls["file_access"],
            "tool_overrides": dict(tools or {}),
            "intents_layout": intents_vlayout,
            "intent_rows":    [],
            "original_action_names": {
                str(item.get("action_file", {}).get("name") or "")
                for item in (intents or [])
                if isinstance(item, dict)
                and isinstance(item.get("action_file"), dict)
                and str(item["action_file"].get("name") or "")
            },
        }
        tools_btn.clicked.connect(
            lambda: self._open_tool_access_dialog(
                blk, _get(blk["label"]).strip() or _get(blk["hotkey"]).strip() or "Caller"
            )
        )

        from core.prompt_i18n import localize_intent_if_default

        caller_idx = len(self._caller_blocks)
        ui_language = display_language or current_app_language()
        for intent_idx, r in enumerate(intents or []):
            display_intent = localize_intent_if_default(
                caller_idx,
                intent_idx,
                r,
                ui_language,
            )
            self._add_caller_intent_row(
                blk,
                display_intent.get("key", ""),
                display_intent.get("label", ""),
                display_intent.get("prompt", ""),
                action_file=(
                    display_intent.get("action_file")
                    if isinstance(display_intent.get("action_file"), dict)
                    else None
                ),
            )
        self._add_caller_custom_prompt_row(blk, custom_key=custom_key, custom_label=custom_label)

        # Add-row button
        add_row_btn = QPushButton(t("Add choice"))
        add_row_btn.clicked.connect(lambda: self._add_caller_intent_row(blk))
        add_wrap = QHBoxLayout()
        add_wrap.setContentsMargins(0, 2, 0, 0)
        add_wrap.addWidget(add_row_btn)
        add_wrap.addStretch()
        outer.addLayout(add_wrap)

        display_label = label_edit.text().strip() or t("New intent shortcut")
        wrapper = self._add_shortcut_row(
            self._callers_vlayout,
            display_label,
            "Open the intent overlay for selected text or the active project.",
            hotkey_edit,
            hotkey_edit_2,
            enabled_check,
            detail,
            section_rows_layout=getattr(
                self, "_global_shortcut_rows", self._callers_vlayout
            ),
            remove_callback=lambda: self._delete_caller_block(blk),
        )
        blk["widget"] = wrapper
        title_label = self._shortcut_rows[-1]["title_label"]
        blk["title_label"] = title_label
        label_edit.textChanged.connect(
            lambda text, target=title_label: target.setText(
                text.strip() or t("New intent shortcut")
            )
        )
        self._caller_blocks.append(blk)
        self._wire_change_tracking(wrapper)
        self._schedule_search_index_refresh()
        search = getattr(self, "_shortcut_search", None)
        if isinstance(search, QLineEdit):
            self._filter_shortcut_rows(search.text())
        self._schedule_warning_marker_refresh()
        self._schedule_dirty_refresh()

    def _add_caller_intent_row(
        self,
        blk: dict,
        key: str = "",
        label: str = "",
        prompt: str = "",
        action_file: dict | None = None,
    ) -> None:
        """Append one intent row to a caller block."""
        from PySide6.QtWidgets import QSizePolicy
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 1, 0, 1)
        h.setSpacing(6)

        metadata = dict(action_file or {})
        enabled_check = QCheckBox()
        enabled_check.setChecked(bool(metadata.get("enabled", True)))
        enabled_check.setToolTip(t("Show this action in the intent picker"))
        h.addWidget(enabled_check, 0, Qt.AlignmentFlag.AlignTop)

        key_edit = QLineEdit(key)
        key_edit.setFixedWidth(40)
        key_edit.setPlaceholderText("w")
        h.addWidget(key_edit, 0, Qt.AlignmentFlag.AlignTop)

        label_edit = QLineEdit(label)
        label_edit.setFixedWidth(130)
        label_edit.setPlaceholderText(t("Label"))
        h.addWidget(label_edit, 0, Qt.AlignmentFlag.AlignTop)

        prompt_edit = QTextEdit()
        prompt_edit.setAcceptRichText(False)
        prompt_edit.setTabChangesFocus(True)
        prompt_edit.setPlainText(prompt)
        prompt_edit.setPlaceholderText(t("Prompt sent to LLM..."))
        prompt_edit.setMinimumHeight(56)
        prompt_edit.setMaximumHeight(88)
        prompt_edit.setMinimumWidth(80)
        prompt_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        h.addWidget(prompt_edit)

        row_info: dict = {
            "widget": row_w,
            "key": key_edit,
            "label": label_edit,
            "prompt": prompt_edit,
            "enabled": enabled_check,
            "action_file": metadata,
            "original_label": label,
            "original_prompt": prompt,
        }

        if bool(metadata.get("has_code")):
            label_edit.setReadOnly(True)
            prompt_edit.setReadOnly(True)
            open_btn = QPushButton(t("Open file"))
            open_btn.setToolTip(t("Open this code-backed action in your default editor"))
            action_path = str(metadata.get("path") or metadata.get("script_path") or "")
            open_btn.clicked.connect(
                lambda _checked=False, path=action_path: (
                    QDesktopServices.openUrl(QUrl.fromLocalFile(path)) if path else None
                )
            )
            h.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignTop)
            row_info["open_file"] = open_btn

        access = [
            str(item).strip().title()
            for item in metadata.get("access") or []
            if str(item).strip()
        ]
        if access:
            colour = {
                "green": "#42b883",
                "amber": "#d9a441",
                "red": "#e06464",
            }.get(str(metadata.get("colour") or "green"), "#42b883")
            access_label = QLabel(", ".join(access))
            access_label.setMaximumWidth(85)
            access_label.setWordWrap(True)
            access_label.setToolTip(
                t("Access declared by this action file; this is a label, not a sandbox")
            )
            access_label.setStyleSheet(
                f"QLabel {{ color: {colour}; border: 1px solid {colour}; "
                "border-radius: 6px; padding: 2px 6px; }}"
            )
            h.addWidget(access_label, 0, Qt.AlignmentFlag.AlignTop)
            row_info["access_label"] = access_label

        del_btn = QPushButton("X")
        del_btn.setFixedWidth(40)
        del_btn.setStyleSheet("QPushButton { padding: 5px 4px; }")
        del_btn.clicked.connect(lambda: self._delete_caller_intent_row(blk, row_info))
        h.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignTop)

        blk["intents_layout"].addWidget(row_w)
        blk["intent_rows"].append(row_info)
        self._wire_change_tracking(row_w)
        self._schedule_search_index_refresh()
        self._schedule_dirty_refresh()

    def _add_caller_custom_prompt_row(
        self,
        blk: dict,
        *,
        custom_key: str = "s",
        custom_label: str = "",
    ) -> None:
        """Append the fixed custom prompt row beside the configured intent rows."""
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 1, 0, 1)
        h.setSpacing(6)

        h.addSpacing(24)
        key_edit = QLineEdit(custom_key)
        key_edit.setFixedWidth(40)
        key_edit.setPlaceholderText("s")
        h.addWidget(key_edit)

        label_edit = QLineEdit(custom_label)
        label_edit.setFixedWidth(130)
        label_edit.setPlaceholderText(t("Custom prompt"))
        h.addWidget(label_edit)

        prompt_edit = QLineEdit(t("Custom prompt"))
        prompt_edit.setEnabled(False)
        prompt_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        h.addWidget(prompt_edit)

        h.addSpacing(40)
        blk["intents_layout"].addWidget(row_w)
        blk["custom_key"] = key_edit
        blk["custom_label"] = label_edit
        blk["custom_prompt"] = prompt_edit

    def _delete_caller_intent_row(self, blk: dict, row_info: dict) -> None:
        """Delete caller intent row."""
        if row_info in blk["intent_rows"]:
            blk["intent_rows"].remove(row_info)
        row_info["widget"].deleteLater()
        self._schedule_search_index_refresh()
        self._schedule_dirty_refresh()

    def _delete_caller_block(self, blk: dict) -> None:
        """Delete caller block."""
        if blk in self._caller_blocks:
            self._caller_blocks.remove(blk)
        widget = blk.get("widget")
        self._shortcut_rows = [
            entry for entry in getattr(self, "_shortcut_rows", [])
            if entry.get("widget") is not widget
        ]
        for section in getattr(self, "_shortcut_sections", []):
            section["entries"] = [
                entry for entry in section["entries"]
                if entry.get("widget") is not widget
            ]
        blk["widget"].deleteLater()
        search = getattr(self, "_shortcut_search", None)
        if isinstance(search, QLineEdit):
            self._filter_shortcut_rows(search.text())
        self._schedule_search_index_refresh()
        self._schedule_dirty_refresh()

    def _tab_memory(self) -> QWidget:
        """Memory tab: LTM configuration only."""
        from PySide6.QtWidgets import QScrollArea

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        w = QWidget()
        root = QVBoxLayout(w)
        root.setSpacing(12)
        root.setContentsMargins(12, 12, 12, 12)

        root.addWidget(self._memory_settings_card())
        root.addStretch()
        scroll.setWidget(w)
        return scroll

    def _memory_settings_card(self) -> QWidget:
        """Build the long-term memory settings card."""
        cfg_card, cfg_cv = self._card("Memory Settings")
        fw = QWidget()
        f = _expanding_form_layout(fw)
        f.setSpacing(8)
        f.setContentsMargins(0, 0, 0, 0)

        mem_auto = QCheckBox("Extract long-term facts automatically")
        mem_auto.setToolTip("Off by default to avoid clutter. Explicit remember/note commands still save facts.")
        self._fields["MEMORY_AUTO_CONSOLIDATE"] = mem_auto
        f.addRow("", mem_auto)

        mem_topk = QLineEdit(self._env.get("MEMORY_TOP_K", "3"))
        mem_topk.setPlaceholderText("number of facts to retrieve per query")
        mem_topk_tip = "Maximum number of stored facts to add to each model request. Higher values add more context."
        self._fields["MEMORY_TOP_K"] = mem_topk
        f.addRow(_tooltip_label("Retrieval top-k:", mem_topk_tip), mem_topk)

        cfg_cv.addWidget(fw)
        return cfg_card

    def _selected_harness_provider(self) -> str:
        """Return the selected external conversation harness, if any."""
        fields = getattr(self, "_fields", {})
        field = fields.get("CHAT_EXECUTION_MODE")
        if field is None:
            return ""
        provider = str(field.currentData() or "").strip().lower()  # type: ignore[attr-defined]
        return provider if provider in {"codex", "claude"} else ""

    def _refresh_harness_login_status(self) -> None:
        """Refresh the selected harness login alongside other auth states."""
        if not self._selected_harness_provider():
            return
        self._schedule_open_status_refresh()

    def _harness_login(self) -> None:
        """Open the selected harness's interactive CLI login flow."""
        provider = self._selected_harness_provider()
        if not provider:
            return
        try:
            from core.harness_clients.auth import harness_login_command, harness_login_environment

            command = harness_login_command(provider)
            environment = harness_login_environment(provider)
        except Exception as exc:  # noqa: BLE001 - shown directly in Settings
            self._set_status_label(self._harness_auth_status_lbl, False, str(exc))
            return
        display_name = "ChatGPT" if provider == "codex" else "Claude Agent"
        launched = _launch_terminal_command(
            command,
            cwd=Path.cwd(),
            title=f"{display_name} sign-in",
            failure_label=f"{display_name} sign-in",
            environment=environment,
        )
        if not launched:
            self._set_status_label(
                self._harness_auth_status_lbl,
                False,
                "Could not open a terminal for sign-in.",
            )
            return
        self._set_status_label(
            self._harness_auth_status_lbl,
            None,
            "Complete sign-in in the terminal or browser. OpenWand will refresh automatically.",
        )
        self._start_harness_auth_poll(target=True)

    def _harness_logout(self) -> None:
        """Sign out of the selected harness without blocking the Settings UI."""
        provider = self._selected_harness_provider()
        if not provider:
            return
        self._cancel_status_refresh()
        self._status_refresh_token += 1
        token = self._status_refresh_token
        self._status_refresh_running = True
        self._pending_status_attrs = {"_harness_auth_status_lbl"}
        self._set_status_label(self._harness_auth_status_lbl, None, "Signing out...")
        self._harness_login_btn.setEnabled(False)
        self._harness_logout_btn.setEnabled(False)
        if not self._status_result_timer.isActive():
            self._status_result_timer.start()
        QTimer.singleShot(_AUTH_STATUS_TIMEOUT_MS, lambda tok=token: self._expire_status_refresh(tok))

        def _worker() -> None:
            from core.harness_clients.auth import logout_harness

            status = logout_harness(provider)
            ok = (
                True if status.available and status.logged_in is True
                else None if status.available and status.logged_in is False
                else False
            )
            self._queue_status_result(token, "_harness_auth_status_lbl", ok, status.message)

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"settings-{provider}-logout",
        ).start()

    def _start_harness_auth_poll(self, *, target: bool) -> None:
        """Poll until a CLI login/logout flow reaches its expected state."""
        self._harness_auth_poll_target = bool(target)
        self._harness_auth_poll_ticks = 0
        self._harness_auth_poll_provider = self._selected_harness_provider()
        timer = getattr(self, "_harness_auth_poll_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(2000)
            timer.timeout.connect(self._harness_auth_poll_tick)
            self._harness_auth_poll_timer = timer
        timer.start()

    def _harness_auth_poll_tick(self) -> None:
        """Start another non-blocking login status read while auth is pending."""
        provider = self._selected_harness_provider()
        if not provider or provider != self._harness_auth_poll_provider:
            self._harness_auth_poll_timer.stop()
            self._harness_auth_poll_target = None
            self._harness_auth_poll_provider = ""
            return
        self._harness_auth_poll_ticks += 1
        if self._harness_auth_poll_ticks >= 150:
            self._harness_auth_poll_timer.stop()
            self._harness_auth_poll_target = None
            self._harness_auth_poll_provider = ""
            self._set_status_label(
                self._harness_auth_status_lbl,
                False,
                "Timed out waiting for agent sign-in. Use Refresh to check again.",
            )
            return
        if not self._status_refresh_running:
            self._schedule_open_status_refresh()

    def _tab_app(self) -> QWidget:
        """Handle tab app for settings dialog."""
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer_w = QWidget()
        outer = QVBoxLayout(outer_w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        execution_card, execution_cv = self._card("Run conversations with")
        execution_cv.addWidget(_desc_label(
            "",
            "Choose the agent behind OpenWand conversations. OpenWand uses the configured model route; "
            "ChatGPT and Claude run their full local agent harness and stream progress back into OpenWand.",
        ))
        execution_mode = _NoScrollCombo()
        execution_mode.addItem(t("OpenWand"), "openwand")
        execution_mode.addItem(t("ChatGPT"), "codex")
        execution_mode.addItem(t("Claude Agent"), "claude")
        execution_mode.setToolTip(t(
            "OpenWand always keeps a local chat copy. Agent-managed conversations also keep a resumable ChatGPT or Claude session."
        ))
        self._fields["CHAT_EXECUTION_MODE"] = execution_mode
        conversation_owner = _NoScrollCombo()
        conversation_owner.addItem(t("OpenWand"), "openwand")
        conversation_owner.addItem(t("Selected agent"), "agent")
        conversation_owner.setToolTip(t(
            "OpenWand-managed conversations send the full OpenWand history with each request. "
            "Agent-managed conversations transfer once, then resume the ChatGPT or Claude session."
        ))
        self._fields["CHAT_CONVERSATION_OWNER"] = conversation_owner

        owner_sync_state = {"provider": str(execution_mode.currentData() or "openwand")}

        def sync_owner_agent_label(*, select_default: bool = True) -> None:
            selected = str(execution_mode.currentData() or "openwand")
            previous = owner_sync_state["provider"]
            label = {
                "codex": t("ChatGPT"),
                "claude": t("Claude Agent"),
            }.get(selected, t("Selected agent"))
            conversation_owner.setItemText(1, label)
            conversation_owner.setEnabled(selected in {"codex", "claude"})
            if selected == "openwand":
                conversation_owner.setCurrentIndex(conversation_owner.findData("openwand"))
            elif select_default and previous == "openwand":
                conversation_owner.setCurrentIndex(conversation_owner.findData("agent"))
            owner_sync_state["provider"] = selected
            poll_timer = getattr(self, "_harness_auth_poll_timer", None)
            poll_provider = getattr(self, "_harness_auth_poll_provider", "")
            if poll_timer is not None and poll_provider and poll_provider != selected:
                poll_timer.stop()
                self._harness_auth_poll_target = None
                self._harness_auth_poll_provider = ""
            auth_widget = getattr(self, "_harness_auth_widget", None)
            if isinstance(auth_widget, QWidget):
                auth_widget.setVisible(selected in {"codex", "claude"})
            title_label = getattr(self, "_harness_auth_title_lbl", None)
            if isinstance(title_label, QLabel):
                title_label.setText(
                    t("ChatGPT login") if selected == "codex" else t("Claude Agent login")
                )
            if (
                selected in {"codex", "claude"}
                and hasattr(self, "_status_result_timer")
                and not getattr(self, "_loading_values", False)
            ):
                self._schedule_open_status_refresh()

        execution_mode.currentIndexChanged.connect(lambda _index: sync_owner_agent_label())
        sync_owner_agent_label(select_default=False)
        execution_form_w = QWidget()
        execution_form = _expanding_form_layout(execution_form_w)
        execution_form.setContentsMargins(0, 0, 0, 0)
        execution_form.addRow(t("Agent"), execution_mode)
        execution_form.addRow(t("Conversation goes to"), conversation_owner)
        execution_cv.addWidget(execution_form_w)

        self._harness_auth_widget = QWidget()
        harness_auth_layout = QVBoxLayout(self._harness_auth_widget)
        harness_auth_layout.setContentsMargins(0, 4, 0, 0)
        harness_auth_layout.setSpacing(6)
        harness_auth_layout.addWidget(_sep(visible=True))
        self._harness_auth_title_lbl = QLabel()
        self._harness_auth_title_lbl.setObjectName("settingsHarnessLoginTitle")
        self._harness_auth_title_lbl.setStyleSheet("font-weight: 600;")
        harness_auth_layout.addWidget(self._harness_auth_title_lbl)
        harness_auth_layout.addWidget(_desc_label(
            "",
            "OpenWand keeps ChatGPT agent sessions and login state in an isolated OpenWand profile, "
            "so they do not appear in your personal Codex history. Sign-in opens its terminal and browser flow.",
        ))
        self._harness_auth_status_lbl = QLabel()
        self._harness_auth_status_lbl.setObjectName("settingsHarnessLoginStatus")
        self._harness_auth_status_lbl.setWordWrap(True)
        self._set_status_label(self._harness_auth_status_lbl, None, "Checking status...")
        harness_auth_layout.addWidget(self._harness_auth_status_lbl)
        harness_auth_row = self._button_row(
            ("Sign in", self._harness_login),
            ("Sign out", self._harness_logout),
            ("Refresh", self._refresh_harness_login_status),
        )
        harness_buttons = harness_auth_row.findChildren(QPushButton)
        self._harness_login_btn = harness_buttons[0]
        self._harness_logout_btn = harness_buttons[1]
        self._harness_auth_refresh_btn = harness_buttons[2]
        self._harness_login_btn.setObjectName("settingsHarnessLoginButton")
        self._harness_logout_btn.setObjectName("settingsHarnessLogoutButton")
        self._harness_auth_refresh_btn.setObjectName("settingsHarnessLoginRefreshButton")
        harness_auth_layout.addWidget(harness_auth_row)
        execution_cv.addWidget(self._harness_auth_widget)
        sync_owner_agent_label()
        outer.addWidget(execution_card)

        profile_card, profile_cv = self._card("Profile setup")
        profile_setup_btn = QPushButton(t("Run profile setup"))
        profile_setup_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        profile_setup_btn.setToolTip(t("Open the guided first-time setup without resetting your other settings."))
        profile_setup_btn.clicked.connect(self._open_profile_setup)
        profile_cv.addWidget(profile_setup_btn, 0, Qt.AlignmentFlag.AlignLeft)

        card, cv = self._card("App Settings")
        fw = QWidget()
        f = _expanding_form_layout(fw)
        f.setSpacing(10)
        f.setContentsMargins(0, 0, 0, 0)

        theme_combo = _NoScrollCombo()
        theme_combo.addItem(t("System default"), "system")
        theme_combo.addItem(t("Light"), "light")
        theme_combo.addItem(t("Dark"), "dark")
        self._fields["THEME_MODE"] = theme_combo
        theme_tip = "System follows your OS theme. Light and Dark use OpenWand's saved color templates."
        self._fields["ICON_AUTO_HIDE"] = QCheckBox(t("Auto-hide icon (only visible when active)"))
        self._fields["ICON_AUTO_HIDE"].setToolTip(
            "Hide the floating icon when OpenWand is idle, then show it again while listening or responding."
        )
        self._fields["START_ON_LOGIN"] = QCheckBox(t("Start OpenWand when you sign in"))
        self._fields["START_ON_LOGIN"].setToolTip(
            t("Launch OpenWand automatically after you sign in to this computer.")
        )
        self._fields["CHAT_OPEN_ON_PROMPT"] = QCheckBox(
            t("Open Chat automatically when I submit a prompt")
        )
        self._fields["CHAT_OPEN_ON_PROMPT"].setToolTip(
            t("Open and focus the full Chat window as soon as an overlay prompt is submitted.")
        )
        self._fields["CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE"] = QCheckBox(
            t("Hide the floating reply bubble when Chat opens automatically")
        )
        self._fields["CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE"].setToolTip(
            t("Stream the reply only in Chat for automatically opened prompts. Other bubble notices remain available.")
        )
        self._fields["CHAT_OPEN_ON_PROMPT"].toggled.connect(
            self._update_chat_open_on_prompt_options
        )
        self._fields["CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY"] = QCheckBox(
            t("Use caller context defaults only for new conversations")
        )
        self._fields["CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY"].setToolTip(
            t(
                "When continuing a conversation, start context sources Off. Conversation history "
                "is still included, and you can turn sources on for any prompt."
            )
        )
        app_language = _NoScrollCombo()
        for label, value in LANGUAGE_OPTIONS:
            app_language.addItem(t(label), value)
        app_language_tip = "Language used for the app's menus, dialogs, and controls."
        self._fields["APP_LANGUAGE"] = app_language
        assistant_language = _NoScrollCombo()
        for label, value in _ASSISTANT_LANGUAGE_OPTIONS:
            assistant_language.addItem(t(label), value)
        assistant_language_tip = "Preferred response language. System default leaves the prompt unchanged."
        self._fields["ASSISTANT_LANGUAGE"] = assistant_language
        assistant_language.currentIndexChanged.connect(self._on_assistant_language_changed)

        self._fields["ICON_SIZE"] = QLineEdit()
        self._fields["ICON_SIZE"].setPlaceholderText(t("e.g. 60"))
        icon_size_tip = "Floating icon diameter in pixels."
        self._fields["BUBBLE_WIDTH"] = QLineEdit()
        self._fields["BUBBLE_WIDTH"].setPlaceholderText(t("e.g. 340"))
        bubble_width_tip = "Maximum width of the floating response bubble in pixels."
        self._fields["BUBBLE_LINES"] = QLineEdit()
        self._fields["BUBBLE_LINES"].setPlaceholderText(t("e.g. 4"))
        bubble_lines_tip = "How many lines of response text the bubble shows before scrolling."
        self._fields["BUBBLE_FONT_SIZE"] = QLineEdit()
        self._fields["BUBBLE_FONT_SIZE"].setPlaceholderText(t("e.g. 10"))
        bubble_font_size_tip = "Point size for response text inside the floating bubble."
        self._fields["BUBBLE_SCROLL_ENABLED"] = QCheckBox(t("Wheel-scroll text bubble"))
        self._fields["BUBBLE_SCROLL_ENABLED"].setToolTip(
            t("Let the mouse wheel scroll the bubble text while the pointer is over it.")
        )
        self._fields["BUBBLE_SCROLL_SNAP_ENABLED"] = QCheckBox(t("Snap bubble scroll back while speaking"))
        self._fields["BUBBLE_SCROLL_SNAP_ENABLED"].setToolTip(
            t("After manual scrolling, return to the current highlighted word if speech is still active.")
        )
        _bg_row      = self._color_field("THEME_BG",      "e.g. #16181b", alpha=False)
        _surface_row = self._color_field("THEME_SURFACE", "e.g. #1c1f23", alpha=False)
        _text_row    = self._color_field("THEME_TEXT",    "e.g. #e9e6e0", alpha=False)
        _accent_row  = self._color_field("THEME_ACCENT",  "e.g. #d8a145", alpha=False)
        _bubble_color_row      = self._color_field("BUBBLE_COLOR",          "e.g. #1c1c24dc")
        _bubble_text_color_row = self._color_field("BUBBLE_TEXT_COLOR",     "e.g. #e6e6e6")
        _read_word_color_row   = self._color_field("BUBBLE_READ_WORD_COLOR", "e.g. #d8a145")

        theme_color_tip = (
            "Colors for the theme selected above. Light and Dark each keep their\n"
            "own set — switching Theme swaps these to that mode's colors.\n"
            "Cards, borders and buttons are shaded automatically from these four."
        )
        # Repaint the swatches/values whenever the user switches Theme mode, so the
        # four pickers always show the template for the currently selected mode.
        theme_combo.currentIndexChanged.connect(self._on_theme_mode_changed)

        f.addRow(_tooltip_label("Theme", theme_tip), self._fields["THEME_MODE"])
        f.addRow(_tooltip_label("Background color", theme_color_tip), _bg_row)
        f.addRow(_tooltip_label("Surface color", theme_color_tip), _surface_row)
        f.addRow(_tooltip_label("Text color", theme_color_tip), _text_row)
        f.addRow(_tooltip_label("Accent color", theme_color_tip), _accent_row)
        f.addRow(_tooltip_label("App language", app_language_tip), self._fields["APP_LANGUAGE"])
        f.addRow(_tooltip_label("Assistant language", assistant_language_tip), self._fields["ASSISTANT_LANGUAGE"])
        f.addRow(_sep(), _sep())
        f.addRow(_tooltip_label("Icon size (px)", icon_size_tip), self._fields["ICON_SIZE"])
        f.addRow(_tooltip_label("Text bubble width (px)", bubble_width_tip), self._fields["BUBBLE_WIDTH"])
        f.addRow(_tooltip_label("Text bubble lines", bubble_lines_tip), self._fields["BUBBLE_LINES"])
        f.addRow(_tooltip_label("Text bubble font size (pt)", bubble_font_size_tip), self._fields["BUBBLE_FONT_SIZE"])
        f.addRow("", self._fields["BUBBLE_SCROLL_ENABLED"])
        f.addRow("", self._fields["BUBBLE_SCROLL_SNAP_ENABLED"])
        f.addRow(t("Text bubble color"), _bubble_color_row)
        f.addRow(t("Text bubble text color"), _bubble_text_color_row)
        f.addRow(t("Read word color"), _read_word_color_row)
        f.addRow("", self._fields["ICON_AUTO_HIDE"])
        f.addRow("", self._fields["START_ON_LOGIN"])
        f.addRow("", self._fields["CHAT_OPEN_ON_PROMPT"])
        f.addRow("", self._fields["CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE"])
        f.addRow("", self._fields["CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY"])
        self._update_chat_open_on_prompt_options()
        cv.addWidget(fw)
        outer.addWidget(card)

        updates_card, updates_cv = self._card("Updates")
        from core import updater

        if not hasattr(self, "_update_running"):
            self._update_running = False
        if not hasattr(self, "_update_signal_carriers"):
            self._update_signal_carriers = []
        repo_checkout = updater.is_repo_checkout()
        self._update_repo_checkout = repo_checkout
        self._update_current_lbl = QLabel(
            t("Current version: {version}").format(version=updater.current_version())
        )
        status_text = "Repo checkout: ready to pull origin/main." if repo_checkout else "Ready to check for updates."
        self._update_status_lbl = _FlexibleWrapLabel(t(status_text))
        self._update_status_lbl.setObjectName("settingsUpdateStatusLabel")
        self._update_status_lbl.setStyleSheet("color: palette(placeholder-text);")
        self._update_mode = "repo" if repo_checkout else "check"
        self._update_btn = QPushButton(t("Pull latest") if repo_checkout else t("Check for updates"))
        self._update_btn.setObjectName("settingsUpdateButton")
        tooltip = (
            "Fast-forward this repo checkout from origin/main."
            if repo_checkout
            else "Check GitHub Releases for a newer OpenWand build."
        )
        self._update_btn.setToolTip(t(tooltip))
        self._update_btn.clicked.connect(self._on_update_button)
        updates_row = QHBoxLayout()
        updates_row.addWidget(self._update_current_lbl)
        updates_row.addStretch()
        updates_row.addWidget(self._update_btn)
        updates_cv.addLayout(updates_row)
        updates_cv.addWidget(self._update_status_lbl)
        self._about_updates_card = updates_card

        crash_card, crash_cv = self._card("Crash report")
        crash_cv.addWidget(_desc_label(
            "",
            "Create a redacted diagnostic bundle to attach to a bug report. OpenWand includes bounded log tails "
            "and system details, but does not collect chat, memory, settings, environment, or keychain files. "
            "Review the archive before sharing because logs can still contain user-provided text.",
        ))
        self._crash_report_status_lbl = _FlexibleWrapLabel(t("No crash report created yet."))
        self._crash_report_status_lbl.setObjectName("settingsCrashReportStatus")
        self._crash_report_status_lbl.setStyleSheet("color: palette(placeholder-text);")
        self._crash_report_btn = QPushButton(t("Create crash report…"))
        self._crash_report_btn.setObjectName("settingsCrashReportButton")
        self._crash_report_btn.clicked.connect(self._create_crash_report)
        crash_cv.addWidget(self._crash_report_status_lbl)
        crash_cv.addWidget(self._crash_report_btn)
        self._about_crash_card = crash_card

        uninstall_card, uninstall_cv = self._card("Uninstall")
        uninstall_cv.addWidget(_desc_label(
            "",
            t("Permanently remove OpenWand, its data, and OpenWand-owned local AI models from this computer."),
        ))
        self._uninstall_btn = QPushButton(t("Uninstall OpenWand"))
        self._uninstall_btn.setObjectName("settingsUninstallButton")
        self._uninstall_btn.clicked.connect(self._uninstall_openwand)
        uninstall_cv.addWidget(self._uninstall_btn)
        self._about_uninstall_card = uninstall_card

        privacy_card, privacy_cv = self._card("Text privacy before model requests")
        privacy_cv.addWidget(_desc_label(
            "",
            t("Choose whether OpenWand hides sensitive text before sending it to a model. "
              "Built-in rules need no download. Advanced adds an optional local AI detector."),
        ))
        privacy_mode = _NoScrollCombo()
        privacy_mode.addItem(t("Off — send original text"), "off")
        privacy_mode.addItem(t("Built-in rules — no download"), "builtin")
        privacy_mode.addItem(t("Advanced — built-in rules plus local AI"), "advanced")
        privacy_mode_help = _tooltip_label(
            "Protection level",
            "This controls text filtering, not which model provider you use. Off sends original "
            "text. Built-in replaces the enabled categories below with placeholders. Advanced "
            "also uses the optional local AI model to find context-sensitive private text. "
            "Screenshots and images are not inspected or redacted.",
        )
        privacy_mode_help.setProperty("privacyHelpKey", "mode")
        privacy_general_tooltip = privacy_mode_help.toolTip()
        privacy_mode.setToolTip(privacy_general_tooltip)
        privacy_mode.setProperty("generalToolTip", privacy_general_tooltip)
        advanced_index = privacy_mode.findData("advanced")
        privacy_mode.setItemData(
            advanced_index,
            t(
                "Advanced privacy loads a local 2.8 GB AI model into memory and warms it in the "
                "background when OpenWand starts. Warm-up may take tens of seconds on CPU. If you send "
                "a request before it finishes, that request waits; later requests are faster. The "
                "privacy model never uploads your text."
            ),
            Qt.ItemDataRole.ToolTipRole,
        )
        privacy_mode.currentIndexChanged.connect(self._on_privacy_mode_changed)
        self._fields["PRIVACY_MODE"] = privacy_mode
        privacy_form = QFormLayout()
        privacy_form.setContentsMargins(0, 0, 0, 0)
        privacy_form.setHorizontalSpacing(14)
        privacy_form.setVerticalSpacing(8)
        privacy_form.addRow(privacy_mode_help, privacy_mode)
        self._fields["PRIVACY_REVIEW_BEFORE_SEND"] = QCheckBox(
            t("Ask me before sending when private text is found")
        )
        review_help = _tooltip_label(
            "Confirmation",
            "When private text is found, pause before the model request and show the redacted "
            "preview. You can send the redacted text, send the original text for this request, "
            "or cancel. Turn this off to redact and send automatically.",
        )
        review_help.setProperty("privacyHelpKey", "review")
        self._fields["PRIVACY_REVIEW_BEFORE_SEND"].setToolTip(review_help.toolTip())
        privacy_form.addRow(review_help, self._fields["PRIVACY_REVIEW_BEFORE_SEND"])

        self._fields["PROMPT_INJECTION_PROTECTION"] = QCheckBox(
            t("Detect possible prompt injections in captured text")
        )
        injection_help = _tooltip_label(
            "Prompt injection detection",
            "Checks captured app, document, selection, and clipboard text for simple attempts "
            "to override model instructions. It looks for words such as Ignore or Override "
            "followed by an instruction-like word within the next five words. Your typed prompt "
            "is not scanned.",
        )
        injection_help.setProperty("privacyHelpKey", "prompt-injection")
        self._fields["PROMPT_INJECTION_PROTECTION"].setToolTip(injection_help.toolTip())
        privacy_form.addRow(injection_help, self._fields["PROMPT_INJECTION_PROTECTION"])

        self._fields["PROMPT_INJECTION_WARN"] = QCheckBox(
            t("Warn me before sending when a possible injection is found")
        )
        injection_warning_help = _tooltip_label(
            "Injection warning",
            "Pause before the model request and show the matching captured text. You can "
            "continue with the request or cancel it. Turn this off to detect silently.",
        )
        injection_warning_help.setProperty("privacyHelpKey", "prompt-injection-warning")
        self._fields["PROMPT_INJECTION_WARN"].setToolTip(injection_warning_help.toolTip())
        privacy_form.addRow(injection_warning_help, self._fields["PROMPT_INJECTION_WARN"])
        self._fields["PROMPT_INJECTION_PROTECTION"].toggled.connect(
            self._fields["PROMPT_INJECTION_WARN"].setEnabled
        )
        privacy_cv.addLayout(privacy_form)

        category_header = QLabel(t("Built-in categories to hide"))
        category_header.setObjectName("sectionHeader")
        privacy_cv.addWidget(category_header)
        privacy_cv.addWidget(_desc_label(
            "",
            t("These switches control the rule-based filter. They apply to prompt text, captured "
              "document or app text, memory, and chat history. They do not inspect images."),
        ))
        category_grid = QGridLayout()
        category_grid.setContentsMargins(0, 0, 0, 0)
        category_grid.setHorizontalSpacing(20)
        category_grid.setVerticalSpacing(6)
        category_options = (
            ("PRIVACY_HIDE_SECRETS", "Secrets and credentials", "Passwords, API keys, access tokens, URL credentials, and private keys"),
            ("PRIVACY_HIDE_CONTACT_DETAILS", "Contact details", "Email addresses and phone numbers"),
            ("PRIVACY_HIDE_FINANCIAL_DETAILS", "Financial information", "Payment cards, IBANs, and labeled account or routing numbers"),
            ("PRIVACY_HIDE_GOVERNMENT_IDS", "Government IDs", "US Social Security numbers, passports, and driver’s licence numbers"),
            ("PRIVACY_HIDE_URLS", "Web addresses", "HTTP and HTTPS URLs; credentials inside URLs are always part of Secrets and credentials"),
        )
        for index, (key, label, help_text) in enumerate(category_options):
            checkbox = QCheckBox(t(label))
            checkbox.setToolTip(t(help_text))
            checkbox.setProperty("privacyCategory", key)
            self._fields[key] = checkbox
            category_grid.addWidget(checkbox, index // 2, index % 2)
        privacy_cv.addLayout(category_grid)

        privacy_help_row = QHBoxLayout()
        privacy_help_row.setContentsMargins(0, 0, 0, 0)
        privacy_help_row.setSpacing(24)
        protected_help = _tooltip_label(
            "Where filtering applies",
            "OpenWand checks text in your prompt, captured app or document context, memory, and "
            "chat history. The category switches above show exactly what the built-in rules hide. "
            "Screenshots and other images are not inspected.",
        )
        protected_help.setProperty("privacyHelpKey", "protected-content")
        advanced_help = _tooltip_label(
            "Advanced model requirements",
            "Optional local download: about 2.8 GB plus its runtime. It loads into memory and "
            "warms in the background when OpenWand starts; CPU warm-up may take tens of seconds "
            "and an early request waits. Detection stays on this computer, but the resulting "
            "request is still sent to the model provider you selected.",
        )
        advanced_help.setProperty("privacyHelpKey", "advanced-model")
        privacy_help_row.addWidget(protected_help)
        privacy_help_row.addWidget(advanced_help)
        privacy_help_row.addStretch()
        privacy_cv.addLayout(privacy_help_row)
        self._privacy_model_status_lbl = QLabel()
        self._privacy_model_status_lbl.setWordWrap(True)
        self._privacy_model_status_lbl.setStyleSheet("color: palette(placeholder-text);")
        privacy_cv.addWidget(self._privacy_model_status_lbl)
        privacy_actions = QHBoxLayout()
        self._privacy_model_install_btn = QPushButton(t("Install advanced privacy model"))
        self._privacy_model_install_btn.setToolTip(
            t("Downloads OpenAI Privacy Filter (about 2.8 GB) and its local runtime. Nothing is uploaded.")
        )
        self._privacy_model_install_btn.clicked.connect(self._install_privacy_model)
        self._privacy_model_remove_btn = QPushButton(t("Remove privacy model"))
        self._privacy_model_remove_btn.clicked.connect(self._remove_privacy_model)
        privacy_actions.addWidget(self._privacy_model_install_btn)
        privacy_actions.addWidget(self._privacy_model_remove_btn)
        privacy_actions.addStretch()
        privacy_cv.addLayout(privacy_actions)
        outer.addWidget(privacy_card)
        self._refresh_privacy_model_status()

        # Profile setup changes personal data, so keep it at the end of General
        # instead of interrupting the everyday application controls near the top.
        outer.addWidget(profile_card)

        outer.addStretch()
        scroll.setWidget(outer_w)
        return scroll

    def _update_chat_open_on_prompt_options(self, checked: bool | None = None) -> None:
        """Show the bubble choice only when prompt-driven Chat opening is enabled."""
        parent = self._fields.get("CHAT_OPEN_ON_PROMPT")
        child = self._fields.get("CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE")
        if checked is None and hasattr(parent, "isChecked"):
            checked = bool(parent.isChecked())
        if child is not None:
            child.setVisible(bool(checked))
            child.setEnabled(bool(checked))

    def _refresh_privacy_model_status(self) -> None:
        """Refresh the optional privacy-model controls without loading the model."""
        try:
            from core.privacy_model import model_status

            status = model_status()
        except Exception as exc:  # noqa: BLE001
            status = {"valid": False, "error": f"{type(exc).__name__}: {exc}"}
        valid = bool(status.get("valid"))
        if valid:
            text = t("Advanced privacy model is installed and ready.")
        elif status.get("model_downloaded") and not status.get("runtime_ready"):
            text = t("Privacy model files are present, but the local runtime needs repair.")
        else:
            text = t("Optional AI privacy model is not installed. Built-in protection is still available.")
        if status.get("error"):
            text = t("Privacy model status could not be checked: {error}").format(error=status["error"])
        self._privacy_model_status_lbl.setText(text)
        self._privacy_model_install_btn.setText(
            t("Repair advanced privacy model") if status.get("model_downloaded") else t("Install advanced privacy model")
        )
        self._privacy_model_remove_btn.setVisible(bool(status.get("model_downloaded") or valid))
        self._privacy_model_ready = valid

    def _on_privacy_mode_changed(self, _index: int = -1) -> None:
        """Keep privacy modes exclusive and reject an unavailable advanced model."""
        combo = self._fields.get("PRIVACY_MODE")
        if not isinstance(combo, QComboBox):
            return
        mode = str(combo.currentData() or "builtin")
        item_tooltip = combo.itemData(combo.currentIndex(), Qt.ItemDataRole.ToolTipRole)
        combo.setToolTip(str(item_tooltip or combo.property("generalToolTip") or ""))
        review = self._fields.get("PRIVACY_REVIEW_BEFORE_SEND")
        if isinstance(review, QCheckBox):
            review.setEnabled(mode != "off")
        for key in (
            "PRIVACY_HIDE_SECRETS",
            "PRIVACY_HIDE_CONTACT_DETAILS",
            "PRIVACY_HIDE_FINANCIAL_DETAILS",
            "PRIVACY_HIDE_GOVERNMENT_IDS",
            "PRIVACY_HIDE_URLS",
        ):
            category = self._fields.get(key)
            if isinstance(category, QCheckBox):
                category.setEnabled(mode != "off")
        if mode != "advanced" or bool(getattr(self, "_privacy_model_ready", False)):
            return
        fallback = combo.findData("builtin")
        combo.blockSignals(True)
        combo.setCurrentIndex(fallback if fallback >= 0 else 0)
        combo.blockSignals(False)
        combo.setToolTip(str(combo.property("generalToolTip") or ""))
        if isinstance(review, QCheckBox):
            review.setEnabled(True)
        QMessageBox.information(
            self,
            t("Advanced privacy model required"),
            t("Install and verify the optional privacy model before selecting Advanced privacy model."),
        )

    def _privacy_model_install_paths(self) -> tuple[Path, Path]:
        from core.privacy_model import model_dir

        root = model_dir().parent / "installers"
        root.mkdir(parents=True, exist_ok=True)
        return root / "privacy-model.log", root / "privacy-model.status.json"

    def _install_privacy_model(self) -> None:
        """Launch the official model download in OpenWand's installer window."""
        answer = QMessageBox.question(
            self,
            t("Download and install advanced privacy model?"),
            t(
                "OpenWand will download the official OpenAI Privacy Filter (about 2.8 GB) and install "
                "its dedicated local runtime. The model runs only on this computer. Continue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            log_path, status_path = self._privacy_model_install_paths()
            command = [
                sys.executable,
                "-m",
                "runtime.workers.privacy_model_installer",
                "--status-path",
                str(status_path),
            ]
            env = _privacy_model_install_env()
            env.setdefault("PYTHONUTF8", "1")
            env.setdefault("PYTHONIOENCODING", "utf-8")
            root = (
                Path(sys.executable).resolve().parent
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parents[2]
            )
            dialog = OptionalInstallDialog(
                title=t("Advanced privacy model installer"),
                subtitle=t(
                    "Downloads the official OpenAI Privacy Filter (about 2.8 GB) and a local runtime. "
                    "Detection stays on this computer."
                ),
                command=command,
                cwd=root,
                log_path=log_path,
                status_path=status_path,
                env=env,
                parent=self,
                auto_start=True,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self,
                t("Could not start privacy installer"),
                t("Could not start the privacy-model installer: {error}").format(error=exc),
            )
            return
        dialogs = getattr(self, "_optional_install_dialogs", None)
        if not isinstance(dialogs, list):
            dialogs = []
            self._optional_install_dialogs = dialogs
        dialogs.append(dialog)
        dialog.install_finished.connect(
            lambda code, _dialog=dialog: self._finish_privacy_model_install(
                exit_code=int(code),
                dialog=_dialog,
            )
        )
        dialog.destroyed.connect(lambda _obj=None, _dialog=dialog: self._forget_optional_install_dialog(_dialog))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _finish_privacy_model_install(
        self,
        *,
        exit_code: int,
        dialog: OptionalInstallDialog,
    ) -> None:
        """Refresh privacy controls immediately after the installer exits."""
        self._refresh_privacy_model_status()
        QTimer.singleShot(0, self._refresh_privacy_model_status)
        if exit_code == 0:
            self._forget_optional_install_dialog(dialog)

    def _remove_privacy_model(self) -> None:
        """Remove the optional model after explicit confirmation."""
        answer = QMessageBox.question(
            self,
            t("Remove advanced privacy model?"),
            t("Remove the downloaded privacy model and its local runtime? Built-in protection will remain active."),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            from core.privacy_model import remove_model

            remove_model()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self,
                t("Could not remove privacy model"),
                t("Could not remove the privacy model: {error}").format(error=exc),
            )
            return
        combo = self._fields.get("PRIVACY_MODE")
        if isinstance(combo, QComboBox) and combo.currentData() == "advanced":
            combo.setCurrentIndex(combo.findData("builtin"))
        self._refresh_privacy_model_status()

    def _tab_about(self) -> QWidget:
        """Build the version, update, and removal page."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer_w = QWidget()
        outer = QVBoxLayout(outer_w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        outer.addWidget(self._about_updates_card)
        outer.addWidget(self._about_crash_card)
        outer.addWidget(self._about_uninstall_card)
        outer.addStretch()
        scroll.setWidget(outer_w)
        return scroll

    def _create_crash_report(self) -> None:
        """Create a redacted diagnostic bundle and reveal it to the user."""
        from core.crash_report import create_crash_report
        from core.system.file_browser import reveal_path

        self._crash_report_btn.setEnabled(False)
        self._crash_report_status_lbl.setText(t("Creating crash report…"))
        QApplication.processEvents()
        try:
            path = create_crash_report()
            self._crash_report_status_lbl.setText(t("Crash report created: {name}").format(name=path.name))
            try:
                reveal_path(path)
            except Exception as exc:  # noqa: BLE001 - the report still exists if Explorer/Finder fails
                _settings_log.warning("Could not reveal crash report %s: %s", path, exc)
            QMessageBox.information(
                self,
                t("Crash report created"),
                t("The redacted report was saved here:")
                + f"\n\n{path}\n\n"
                + t("Review the ZIP before attaching it to a bug report."),
            )
        except Exception as exc:  # noqa: BLE001 - user-facing diagnostic action
            _settings_log.exception("Crash report creation failed")
            self._crash_report_status_lbl.setText(t("Crash report could not be created."))
            QMessageBox.warning(self, t("Crash report failed"), str(exc))
        finally:
            self._crash_report_btn.setEnabled(True)

    def _uninstall_openwand(self) -> None:
        """Confirm and launch the detached, self-removing OpenWand uninstaller."""
        from ui.uninstall_dialog import run_uninstall_dialog

        run_uninstall_dialog(self)

    def _open_profile_setup(self) -> None:
        """Open the guided profile wizard from Settings → App."""
        if self._dirty_keys:
            QMessageBox.information(
                self,
                t("Save settings first"),
                t("Save or discard your pending Settings changes before running profile setup."),
            )
            return
        wizard = getattr(self, "_profile_setup_wizard", None)
        if wizard is not None and wizard.isVisible():
            wizard.raise_()
            wizard.activateWindow()
            return

        from ui.onboarding import OnboardingWizard

        wizard = OnboardingWizard(parent=None, on_complete=self._profile_setup_completed)
        self._profile_setup_wizard = wizard
        wizard.finished.connect(
            lambda _result, w=wizard: setattr(self, "_profile_setup_wizard", None)
            if getattr(self, "_profile_setup_wizard", None) is w else None
        )
        wizard.show()
        wizard.raise_()
        wizard.activateWindow()

    def _profile_setup_completed(self, _open_chat: bool) -> None:
        """Reload settings written by the standalone profile wizard."""
        old_env = dict(self._env)
        new_env = _read_env()
        changed_keys = sorted(
            key for key in set(old_env) | set(new_env)
            if old_env.get(key) != new_env.get(key)
        )
        try:
            if self._on_apply:
                self._on_apply({"changed_keys": changed_keys, "source": "profile_setup"})
            else:
                import config
                from ui.shared.theme import apply_app_theme

                config.reload()
                apply_app_theme()
            self._env = new_env
            self._load_values()
            self._reset_dirty_baseline()
            self._refresh_search_index()
        except Exception as exc:  # noqa: BLE001 - show a recoverable failure to the user
            _settings_log.exception("Profile setup reload failed")
            QMessageBox.warning(self, t("Profile setup"), str(exc))

    def _set_update_status(self, message: str, state: str | None = None, **format_args: object) -> None:
        """Update the Settings updater status label."""
        label = getattr(self, "_update_status_lbl", None)
        if label is None:
            return
        text = t(message)
        if format_args:
            text = text.format(**format_args)
        label.setText(text)
        if state == "ok":
            label.setStyleSheet("color: #80c080;")
        elif state == "error":
            label.setStyleSheet("color: #c04040;")
        elif state == "warn":
            label.setStyleSheet("color: #c0a040;")
        else:
            label.setStyleSheet("color: palette(placeholder-text);")

    def _on_update_button(self) -> None:
        """Handle the Settings update button based on its current mode."""
        mode = getattr(self, "_update_mode", "check")
        if mode == "download":
            self._download_available_update()
        elif mode == "apply":
            self._apply_downloaded_update()
        elif mode == "repo":
            self._pull_repo_update()
        else:
            self._check_for_updates()

    def _remember_update_carrier(self, carrier: _UpdateSignals) -> None:
        self._update_signal_carriers.append(carrier)

    def _forget_update_carrier(self, carrier: _UpdateSignals) -> None:
        try:
            self._update_signal_carriers.remove(carrier)
        except ValueError:
            pass

    def _check_for_updates(self) -> None:
        """Check the release manifest without blocking the Settings dialog."""
        if self._update_running:
            return
        self._update_running = True
        self._update_mode = "check"
        self._update_check_result = None
        self._update_download_path = None
        self._update_btn.setEnabled(False)
        self._update_btn.setText(t("Checking..."))
        self._set_update_status("Checking for updates...")

        carrier = _UpdateSignals()
        self._remember_update_carrier(carrier)
        carrier.done.connect(lambda result, error, c=carrier: self._finish_update_check(c, result, error))

        def _worker() -> None:
            try:
                from core import updater

                carrier.done.emit(updater.check_for_updates(), "")
            except Exception as exc:  # noqa: BLE001 - update checks should be visible, not fatal
                carrier.done.emit(None, str(exc))

        threading.Thread(target=_worker, daemon=True, name="openwand-update-check").start()

    def _pull_repo_update(self) -> None:
        """Fast-forward a source checkout without blocking the Settings dialog."""
        if self._update_running:
            return
        self._update_running = True
        self._update_mode = "repo"
        self._update_btn.setEnabled(False)
        self._update_btn.setText(t("Pulling..."))
        self._set_update_status("Pulling latest from origin/main...")

        carrier = _UpdateSignals()
        self._remember_update_carrier(carrier)
        carrier.done.connect(lambda result, error, c=carrier: self._finish_repo_update(c, result, error))

        def _worker() -> None:
            try:
                from core import updater

                carrier.done.emit(updater.apply_repo_update(), "")
            except Exception as exc:  # noqa: BLE001 - repo update errors should stay in Settings
                carrier.done.emit(None, str(exc))

        threading.Thread(target=_worker, daemon=True, name="openwand-repo-update").start()

    def _finish_repo_update(self, carrier: _UpdateSignals, result: object, error: str) -> None:
        """Apply a repo update result on the Qt thread."""
        self._forget_update_carrier(carrier)
        self._update_running = False
        self._update_mode = "repo"
        self._update_btn.setEnabled(True)
        self._update_btn.setText(t("Pull latest"))
        if error:
            self._set_update_status("Repo update failed: {error}", "error", error=error)
            return
        if bool(getattr(result, "updated", False)):
            self._set_update_status("Repo updated. Restart OpenWand to use the latest code.", "ok")
        else:
            self._set_update_status("Repo is already up to date.", "ok")

    def _finish_update_check(self, carrier: _UpdateSignals, result: object, error: str) -> None:
        """Apply an update-check result on the Qt thread."""
        self._forget_update_carrier(carrier)
        self._update_running = False
        self._update_btn.setEnabled(True)
        if error:
            self._update_mode = "check"
            self._update_btn.setText(t("Check for updates"))
            self._set_update_status("Update check failed: {error}", "error", error=error)
            return

        update_available = bool(getattr(result, "update_available", False))
        asset = getattr(result, "asset", None)
        latest_version = str(getattr(result, "latest_version", "") or "")
        if update_available and asset is not None:
            self._update_check_result = result
            self._update_mode = "download"
            self._update_btn.setText(t("Download update"))
            self._set_update_status("Version {version} is available.", "ok", version=latest_version)
            return

        from core import updater

        if updater.is_newer_version(latest_version, updater.current_version()) and asset is None:
            platform_key = updater.normalized_platform_key()
            self._update_mode = "check"
            self._update_btn.setText(t("Check for updates"))
            self._set_update_status(
                "Version {version} is available, but no {platform} build was published.",
                "warn",
                version=latest_version,
                platform=platform_key,
            )
            return

        self._update_mode = "check"
        self._update_btn.setText(t("Check for updates"))
        self._set_update_status("OpenWand is up to date.", "ok")

    def _download_available_update(self) -> None:
        """Download the selected update artifact."""
        if self._update_running:
            return
        result = self._update_check_result
        asset = getattr(result, "asset", None)
        if asset is None:
            self._update_mode = "check"
            self._update_btn.setText(t("Check for updates"))
            self._set_update_status("No update is ready to download.", "warn")
            return

        self._update_running = True
        self._update_btn.setEnabled(False)
        self._update_btn.setText(t("Downloading..."))
        self._set_update_status("Downloading update...")

        carrier = _UpdateSignals()
        self._remember_update_carrier(carrier)
        carrier.done.connect(lambda path, error, c=carrier: self._finish_update_download(c, path, error))

        def _worker() -> None:
            try:
                from core import updater

                carrier.done.emit(str(updater.download_update(asset)), "")
            except Exception as exc:  # noqa: BLE001 - update downloads should be visible, not fatal
                carrier.done.emit("", str(exc))

        threading.Thread(target=_worker, daemon=True, name="openwand-update-download").start()

    def _finish_update_download(self, carrier: _UpdateSignals, path: object, error: str) -> None:
        """Apply an update-download result on the Qt thread."""
        self._forget_update_carrier(carrier)
        self._update_running = False
        self._update_btn.setEnabled(True)
        if error:
            self._update_mode = "download" if self._update_check_result is not None else "check"
            self._update_btn.setText(t("Download update") if self._update_mode == "download" else t("Check for updates"))
            self._set_update_status("Update download failed: {error}", "error", error=error)
            return

        from pathlib import Path

        self._update_download_path = Path(str(path))
        self._update_mode = "apply"
        self._update_btn.setText(t("Apply update"))
        self._update_btn.setToolTip(t("Apply the downloaded update and restart OpenWand."))
        self._set_update_status("Update downloaded. Apply it when you are ready to restart OpenWand.", "ok")

    def _apply_downloaded_update(self) -> None:
        """Apply the downloaded update via a helper process and quit OpenWand."""
        path = self._update_download_path
        if path is None or not path.exists():
            self._update_mode = "check"
            self._update_btn.setText(t("Check for updates"))
            self._set_update_status("Downloaded update file is no longer available.", "error")
            return

        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setWindowTitle(t("Apply update"))
        confirm.setText(t("Apply the downloaded update now?"))
        confirm.setInformativeText(t("OpenWand will close, install the update, and restart."))
        confirm.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        confirm.button(QMessageBox.StandardButton.Yes).setText(t("Apply and restart"))
        confirm.button(QMessageBox.StandardButton.Cancel).setText(t("Not now"))
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            self._set_update_status("Update downloaded. Apply it when you are ready to restart OpenWand.", "ok")
            return

        try:
            from core import updater

            updater.apply_update(path)
            self._update_mode = "apply"
            self._update_btn.setEnabled(False)
            self._update_btn.setText(t("Applying..."))
            self._set_update_status(
                "Applying update. OpenWand will close now; installing and reopening can take a few minutes.",
                "ok",
            )
            app = QApplication.instance()
            if app is not None:
                QTimer.singleShot(1500, app.quit)
        except Exception as exc:  # noqa: BLE001 - surface launcher issues in Settings
            self._set_update_status("Could not apply update: {error}", "error", error=exc)

    def _update_chat_elaborate_prompt_visibility(self, checked: bool | None = None) -> None:
        """Show the elaborate prompt field only when auto-elaborate is enabled."""
        field = self._fields.get("CHAT_ELABORATE_PROMPT")
        checkbox = self._fields.get("CHAT_AUTO_ELABORATE")
        if checked is None and hasattr(checkbox, "isChecked"):
            checked = bool(checkbox.isChecked())  # type: ignore[attr-defined]
        visible = bool(checked)
        if self._chat_elaborate_prompt_label is not None:
            self._chat_elaborate_prompt_label.setVisible(visible)
        if field is not None:
            field.setVisible(visible)

    def _on_assistant_language_changed(self, *_args) -> None:
        """Refresh model-facing built-in prompts when the assistant language changes."""
        if getattr(self, "_loading_values", False):
            return
        import config as cfg

        assistant_language = _get(self._fields["ASSISTANT_LANGUAGE"])
        chat_field = self._fields.get("CHAT_ELABORATE_PROMPT")
        if chat_field is not None:
            _set(
                chat_field,
                cfg.localize_chat_elaborate_prompt_if_default(
                    _get(chat_field),
                    assistant_language,
                ),
            )

        system_field = self._fields.get("SYSTEM_PROMPT_UTILITY")
        if system_field is not None:
            _set(
                system_field,
                cfg.localize_system_prompt_utility_if_default(
                    _get(system_field),
                    assistant_language,
                ),
            )

        ui_language = _get(self._fields["APP_LANGUAGE"]) or current_app_language()
        for i, blk in enumerate(getattr(self, "_caller_blocks", [])):
            for j, row in enumerate(blk.get("intent_rows", [])):
                intent = cfg.localize_intent_if_default(
                    i,
                    j,
                    {
                        "key": _get(row["key"]),
                        "label": _get(row["label"]),
                        "prompt": _get(row["prompt"]),
                    },
                    ui_language,
                )
                row["key"].setText(str(intent.get("key", "")))
                row["label"].setText(str(intent.get("label", "")))
                _set(row["prompt"], str(intent.get("prompt", "")))

    def _tab_advanced(self) -> QWidget:
        """Handle tab advanced for settings dialog."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        outer_w = QWidget()
        outer = QVBoxLayout(outer_w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        context_card, context_cv = self._card("Context limits")
        context_fw = QWidget()
        context_f = _expanding_form_layout(context_fw)
        context_f.setSpacing(8)
        context_f.setContentsMargins(0, 0, 0, 0)
        self._fields["CONTEXT_BROWSER_MAX_CHARS"] = QLineEdit()
        self._fields["CONTEXT_BROWSER_MAX_CHARS"].setPlaceholderText("e.g. 8000")
        browser_chars_tip = "Maximum characters OpenWand reads from a browser page when browser context is on."
        self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"] = QLineEdit()
        self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"].setPlaceholderText("e.g. 12000")
        ambient_doc_chars_tip = "Maximum characters read automatically from open documents before the model answers."
        self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"] = QLineEdit()
        self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"].setPlaceholderText("e.g. 12000")
        tool_doc_chars_tip = "Maximum characters returned when the model chooses to fetch document text with a tool."
        context_f.addRow(_tooltip_label("Browser fetch chars", browser_chars_tip), self._fields["CONTEXT_BROWSER_MAX_CHARS"])
        context_f.addRow(
            _tooltip_label("Auto document fetch chars", ambient_doc_chars_tip),
            self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"],
        )
        context_f.addRow(
            _tooltip_label("Tool document fetch chars", tool_doc_chars_tip),
            self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"],
        )
        context_cv.addWidget(context_fw)
        outer.addWidget(context_card)

        local_file_card, local_file_cv = self._card("Model file access")
        local_file_note = QLabel(
            t(
                "These folders are the only places the model can list or read files. "
                "Each keybind chooses whether local files are off, read-only, ask-before-write, or automatic."
            )
        )
        local_file_note.setWordWrap(True)
        local_file_cv.addWidget(local_file_note)
        local_file_fw = QWidget()
        local_file_f = _expanding_form_layout(local_file_fw)
        local_file_f.setSpacing(8)
        local_file_f.setContentsMargins(0, 0, 0, 0)
        self._fields["TOOL_FILE_ROOTS"] = _FolderListEditor()
        file_roots_tip = "Folders that file tools are allowed to inspect. Paths outside this list are refused."
        self._fields["TOOL_FILE_BLOCKED_GLOBS"] = QTextEdit()
        self._fields["TOOL_FILE_BLOCKED_GLOBS"].setPlaceholderText(
            t("Add one blocking rule per line. Hover over ⓘ for examples.")
        )
        blocked_globs_tip = (
            "Block matching files even when they are inside an allowed folder. Add one rule per line. "
            "Use * to match part of a file name and ** to match folders at any depth.\n\n"
            "Examples:\n"
            ".env* — blocks .env and .env.local\n"
            "**/secrets/** — blocks every secrets folder\n"
            "*.pem — blocks PEM key files"
        )
        self._fields["TOOL_FILE_BLOCKED_GLOBS"].setFixedHeight(92)
        local_file_f.addRow(_tooltip_label("Folders the model may use", file_roots_tip), self._fields["TOOL_FILE_ROOTS"])
        local_file_f.addRow(
            _tooltip_label("Files and folders to block", blocked_globs_tip, show_info=True),
            self._fields["TOOL_FILE_BLOCKED_GLOBS"],
        )
        local_file_cv.addWidget(local_file_fw)
        outer.addWidget(local_file_card)

        outer.addWidget(self._memory_settings_card())

        memory_card, memory_cv = self._card("Memory tuning")
        memory_fw = QWidget()
        memory_f = _expanding_form_layout(memory_fw)
        memory_f.setSpacing(8)
        memory_f.setContentsMargins(0, 0, 0, 0)
        self._fields["MEMORY_CONSOLIDATION_INTERVAL"] = QLineEdit()
        self._fields["MEMORY_CONSOLIDATION_INTERVAL"].setPlaceholderText("minutes between consolidations")
        consolidation_tip = "How often OpenWand compresses recent conversation into longer-term memory."
        self._fields["MEMORY_STM_TOKEN_BUDGET"] = QLineEdit()
        self._fields["MEMORY_STM_TOKEN_BUDGET"].setPlaceholderText("tokens before STM compression kicks in")
        stm_budget_tip = "Approximate short-term memory size before recent conversation is summarized."
        memory_f.addRow(
            _tooltip_label("Consolidation interval (min)", consolidation_tip),
            self._fields["MEMORY_CONSOLIDATION_INTERVAL"],
        )
        memory_f.addRow(_tooltip_label("STM token budget", stm_budget_tip), self._fields["MEMORY_STM_TOKEN_BUDGET"])
        memory_cv.addWidget(memory_fw)
        outer.addWidget(memory_card)

        timing_card, timing_cv = self._card("Speech and bubble timing")
        timing_fw = QWidget()
        timing_f = _expanding_form_layout(timing_fw)
        timing_f.setSpacing(8)
        timing_f.setContentsMargins(0, 0, 0, 0)
        self._fields["BUBBLE_REVEAL_WPM"] = QLineEdit()
        self._fields["BUBBLE_REVEAL_WPM"].setPlaceholderText("e.g. 170")
        reveal_wpm_tip = "Words per minute used for the normal bubble reveal, including TTS providers without word timestamps."
        self._fields["BUBBLE_HOLD_REVEAL_WPM"] = QLineEdit()
        self._fields["BUBBLE_HOLD_REVEAL_WPM"].setPlaceholderText("e.g. 480")
        hold_reveal_wpm_tip = "Words per minute used when showing text without generated speech."
        self._fields["BUBBLE_HIDE_DELAY_S"] = QLineEdit()
        self._fields["BUBBLE_HIDE_DELAY_S"].setPlaceholderText("e.g. 3.5")
        bubble_hide_tip = "How long the text bubble stays on screen after the last word, in seconds."
        self._fields["BUBBLE_SCROLL_SNAP_DELAY_S"] = QLineEdit()
        self._fields["BUBBLE_SCROLL_SNAP_DELAY_S"].setPlaceholderText("e.g. 2.5")
        bubble_snap_tip = "How long to wait after wheel scrolling before returning to the highlighted word."
        self._fields["TTS_PLAYBACK_RATE"] = QLineEdit()
        self._fields["TTS_PLAYBACK_RATE"].setPlaceholderText("e.g. 1.0")
        tts_rate_tip = "Speech playback multiplier. 1.0 is normal speed; larger values speak faster."
        self._fields["TTS_HOLD_PLAYBACK_RATE"] = QLineEdit()
        self._fields["TTS_HOLD_PLAYBACK_RATE"].setPlaceholderText("e.g. 1.35")
        tts_hold_rate_tip = "Playback multiplier for hold-to-talk replies, where a faster response can feel more immediate."
        timing_f.addRow(_tooltip_label("Text bubble speed (WPM)", reveal_wpm_tip), self._fields["BUBBLE_REVEAL_WPM"])
        timing_f.addRow(
            _tooltip_label("Text bubble hold speed (WPM)", hold_reveal_wpm_tip),
            self._fields["BUBBLE_HOLD_REVEAL_WPM"],
        )
        timing_f.addRow(_tooltip_label("Text bubble display time (s)", bubble_hide_tip), self._fields["BUBBLE_HIDE_DELAY_S"])
        timing_f.addRow(
            _tooltip_label("Bubble scroll snap delay (s)", bubble_snap_tip),
            self._fields["BUBBLE_SCROLL_SNAP_DELAY_S"],
        )
        timing_f.addRow(_tooltip_label("TTS speed", tts_rate_tip), self._fields["TTS_PLAYBACK_RATE"])
        timing_f.addRow(_tooltip_label("TTS hold speed", tts_hold_rate_tip), self._fields["TTS_HOLD_PLAYBACK_RATE"])
        timing_cv.addWidget(timing_fw)
        outer.addWidget(timing_card)

        outer.addStretch()
        scroll.setWidget(outer_w)
        return scroll

    # ---- Theme template (light/dark color swatches) ----

    _THEME_ROLES = ("bg", "surface", "text", "accent")
    _THEME_FIELD_KEYS = {
        "bg": "THEME_BG", "surface": "THEME_SURFACE",
        "text": "THEME_TEXT", "accent": "THEME_ACCENT",
    }

    def _theme_edit_mode(self) -> str:
        """Which template the four swatches edit, given the Theme selection."""
        data = self._fields["THEME_MODE"].currentData()  # type: ignore[attr-defined]
        if data in ("light", "dark"):
            return data
        from ui.shared.theme import is_dark_mode  # "system" → whatever is active now
        return "dark" if is_dark_mode() else "light"

    def _flush_visible_theme_fields(self) -> None:
        """Copy the four swatch values back into the template they belong to."""
        mode = self._theme_shown_mode
        if not mode:
            return
        self._theme_templates[mode] = {
            role: _get(self._fields[self._THEME_FIELD_KEYS[role]]).strip()
            for role in self._THEME_ROLES
        }

    def _show_theme_template(self, mode: str) -> None:
        """Load *mode*'s template into the four swatches."""
        tpl = self._theme_templates.get(mode, {})
        self._theme_syncing = True
        try:
            for role in self._THEME_ROLES:
                _set(self._fields[self._THEME_FIELD_KEYS[role]], tpl.get(role, ""))
        finally:
            self._theme_syncing = False
        self._theme_shown_mode = mode

    def _on_theme_mode_changed(self) -> None:
        """Handle theme mode changed events."""
        if self._theme_syncing or not self._theme_templates:
            return
        new_mode = self._theme_edit_mode()
        if new_mode == self._theme_shown_mode:
            return
        self._flush_visible_theme_fields()
        self._show_theme_template(new_mode)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _model_combo(self, provider: str = "") -> QComboBox:
        """Handle model combo for settings dialog."""
        models = _PROVIDER_MODELS.get(provider, [])
        cb = _NoScrollCombo()
        cb.setEditable(True)
        cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        cb.addItems(models)
        cb.setCurrentIndex(-1)
        completer = QCompleter(models, cb)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        cb.setCompleter(completer)
        return cb

    def _combo(self, options: list[str], current: str = "") -> QComboBox:
        """Handle combo for settings dialog."""
        cb = _NoScrollCombo()
        for opt in options:
            cb.addItem(_PROVIDER_LABELS.get(opt, opt) if opt else "", opt)
        if current is not None:
            idx = cb.findData(current)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        return cb

    def _color_field(self, field_key: str, placeholder: str, *, alpha: bool = True) -> QWidget:
        """QLineEdit + color-swatch button that opens QColorDialog.

        Stores ``#RRGGBBAA`` when *alpha* is True (text-bubble colours), or plain
        opaque ``#RRGGBB`` when False (theme colours, where alpha is meaningless).
        """
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog

        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setProperty("hexField", True)
        edit.setFont(_settings_font(13.5, mono=True))
        self._fields[field_key] = edit

        swatch = QPushButton()
        swatch.setFixedSize(28, 28)
        swatch.setToolTip("Pick color")

        def _parse(text: str) -> QColor:
            """Parse the settings dialog workflow."""
            s = text.strip()
            if s.startswith("#") and len(s) == 9:
                try:
                    return QColor(int(s[1:3],16), int(s[3:5],16), int(s[5:7],16), int(s[7:9],16))
                except ValueError:
                    pass
            c = QColor(s)
            return c if c.isValid() else QColor()

        def _fmt(c: QColor) -> str:
            """Handle fmt for settings dialog."""
            if alpha:
                return f"#{c.red():02x}{c.green():02x}{c.blue():02x}{c.alpha():02x}"
            return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"

        def _update_swatch(text=""):
            """Update swatch."""
            from ui.shared.theme import theme_colors

            border = theme_colors()["border"]
            c = _parse(edit.text())
            if c.isValid():
                swatch.setStyleSheet(
                    f"QPushButton {{ background: #{c.alpha():02x}{c.red():02x}{c.green():02x}{c.blue():02x};"
                    f" border: 1px solid {border}; border-radius: 0px; padding: 0px; }}"
                )
            else:
                swatch.setStyleSheet(
                    f"QPushButton {{ background: transparent; border: 1px solid {border}; "
                    "border-radius: 0px; padding: 0px; }"
                )

        def _pick():
            """Handle pick for settings dialog."""
            c = _parse(edit.text())
            if not c.isValid():
                c = QColor(255, 255, 255, 255)
            options = (
                QColorDialog.ColorDialogOption.ShowAlphaChannel
                if alpha else QColorDialog.ColorDialogOption(0)
            )
            chosen = QColorDialog.getColor(c, self, "Pick color", options)
            if chosen.isValid():
                edit.setText(_fmt(chosen))

        edit.textChanged.connect(_update_swatch)
        swatch.clicked.connect(_pick)
        _update_swatch()

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(edit)
        h.addWidget(swatch)
        return row

    def _button_row(self, *buttons: tuple[str, object]) -> QWidget:
        """Handle button row for settings dialog."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for text, handler in buttons:
            btn = QPushButton(t(text))
            btn.clicked.connect(handler)
            layout.addWidget(btn)
        layout.addStretch()
        return row

    def _oauth_provider_row(
        self,
        *,
        key: str,
        icon_asset: str,
        title: str,
        purpose: str,
        sign_in: Callable[[], None],
        sign_out: Callable[[], None],
    ) -> QWidget:
        """Build one compact OAuth row with status, purpose, and inline details."""
        from ui.shared.theme import theme_colors

        colors = theme_colors()
        row = QWidget()
        row.setObjectName(f"{key}OAuthRow")
        outer = QVBoxLayout(row)
        outer.setContentsMargins(0, 5, 0, 7)
        outer.setSpacing(3)

        top = QWidget(row)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        icon = QLabel(top)
        icon.setObjectName("oauthProviderIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(30, 30)
        icon.setStyleSheet(
            f"background:#ffffff; border:1px solid {colors['border']}; border-radius:8px;"
        )
        icon_path = ASSETS_DIR / icon_asset
        provider_icon = QIcon(str(icon_path))
        if provider_icon.isNull():
            icon.setText("?")
        else:
            icon.setPixmap(provider_icon.pixmap(QSize(20, 20)))
        icon.setProperty("providerIconAsset", str(icon_asset))
        icon.setAccessibleName(title)
        top_layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        name = QLabel(title, top)
        name.setObjectName("oauthProviderName")
        name.setFont(_compact_ui_font(13, semibold=True))
        name.setMinimumWidth(126)
        top_layout.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)

        status = QLabel(top)
        status.setObjectName("oauthLoginStatus")
        status.setProperty("oauthProviderKey", key)
        status.setFont(_compact_ui_font(13))
        status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        status.setWordWrap(False)
        top_layout.addWidget(status, 1, Qt.AlignmentFlag.AlignVCenter)

        login_button = QPushButton(t("Sign in"), top)
        login_button.setObjectName(f"{key}OAuthSignIn")
        login_button.setFont(_compact_ui_font(13, semibold=True))
        login_button.clicked.connect(sign_in)
        logout_button = QPushButton(t("Sign out"), top)
        logout_button.setObjectName(f"{key}OAuthSignOut")
        logout_button.setFont(_compact_ui_font(13, semibold=True))
        logout_button.clicked.connect(sign_out)
        top_layout.addWidget(login_button, 0, Qt.AlignmentFlag.AlignVCenter)
        top_layout.addWidget(logout_button, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(top)

        purpose_label = QLabel(purpose, row)
        purpose_label.setObjectName("oauthFeaturePurpose")
        purpose_label.setWordWrap(True)
        purpose_label.setFont(_compact_ui_font(12))
        purpose_label.setStyleSheet(
            f"color: {colors['text_dim']};"
        )
        purpose_label.setContentsMargins(38, 0, 0, 0)
        outer.addWidget(purpose_label)

        message = QLabel(row)
        message.setObjectName("oauthInlineMessage")
        message.setWordWrap(True)
        message.setFont(_compact_ui_font(12))
        message.setContentsMargins(38, 0, 0, 0)
        message.hide()
        outer.addWidget(message)

        setattr(self, f"_{key}_icon_lbl", icon)
        setattr(self, f"_{key}_purpose_lbl", purpose_label)
        setattr(self, f"_{key}_status_lbl", status)
        setattr(self, f"_{key}_message_lbl", message)
        if key == "chatgpt":
            self._cgpt_login_btn = login_button
            self._cgpt_logout_btn = logout_button
        elif key == "github":
            self._github_login_btn = login_button
            self._github_logout_btn = logout_button
        self._set_oauth_status(key, t("Checking status..."), signed_in=None, busy=True)
        return row

    def _set_oauth_status(
        self,
        key: str,
        status_text: str,
        *,
        signed_in: bool | None,
        message: str = "",
        message_kind: str = "error",
        busy: bool = False,
    ) -> None:
        """Keep OAuth status concise and place errors/instructions below the row."""
        from ui.shared.theme import theme_colors

        colors = theme_colors()
        status = getattr(self, f"_{key}_status_lbl", None)
        detail = getattr(self, f"_{key}_message_lbl", None)
        login_button = getattr(self, "_cgpt_login_btn" if key == "chatgpt" else "_github_login_btn", None)
        logout_button = getattr(self, "_cgpt_logout_btn" if key == "chatgpt" else "_github_logout_btn", None)
        if isinstance(status, QLabel):
            status.setText(str(status_text or ""))
            status.setStyleSheet(
                "color: #80c080; font-weight: 600;"
                if signed_in is True
                else f"color: {colors['text_dim']};"
            )
        if isinstance(detail, QLabel):
            clean_message = str(message or "").strip()
            detail.setText(clean_message)
            detail.setStyleSheet(
                "color: #c04040;"
                if message_kind == "error"
                else "color: palette(highlight);"
            )
            detail.setVisible(bool(clean_message))
        if isinstance(login_button, QPushButton):
            login_button.setEnabled(not busy and signed_in is not True)
        if isinstance(logout_button, QPushButton):
            logout_button.setEnabled(not busy and signed_in is not False)

    @staticmethod
    def _status_action_row(status: QLabel, button: QPushButton) -> QWidget:
        """Return the shared compact status/action layout used by speech cards."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        status.setWordWrap(True)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(status, 1)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        """Return an unboxed 9b section with a small-caps heading and rule."""
        card = QFrame()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        # Cards live inside resizable scroll pages. Without a minimum-size
        # constraint Qt may squeeze a card below the height required by wrapped
        # translated labels, causing the last lines to overlap the next row.
        cv.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        cv.setContentsMargins(0, 8, 0, 8)
        cv.setSpacing(10)
        if title:
            heading_row = QWidget()
            heading_row.setObjectName("sectionHeadingRow")
            heading_layout = QHBoxLayout(heading_row)
            heading_layout.setContentsMargins(0, 0, 0, 0)
            heading_layout.setSpacing(10)
            hdr = _WarningHeaderLabel(t(title).upper())
            hdr.setObjectName("sectionHeader")
            rule = QFrame()
            rule.setObjectName("sectionRule")
            rule.setFixedHeight(1)
            heading_layout.addWidget(hdr, 0, Qt.AlignmentFlag.AlignVCenter)
            heading_layout.addWidget(rule, 1, Qt.AlignmentFlag.AlignVCenter)
            cv.addWidget(heading_row)
            self._register_warning_header(title, hdr, uppercase=True)
        return card, cv

    def _collapsible_group(self, title: str, *, checked: bool = False) -> tuple[QWidget, QVBoxLayout]:
        """Return a button-expanded settings body."""
        group = QWidget()
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        toggle = QPushButton(t(title))
        toggle.setCheckable(True)
        toggle.setChecked(checked)
        toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        body = QWidget(group)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 4, 0, 0)
        body_layout.setSpacing(8)
        layout.addWidget(toggle)
        layout.addWidget(body)
        body.setVisible(checked)
        toggle.toggled.connect(body.setVisible)
        return group, body_layout

    def _register_warning_header(
        self,
        key: str,
        label: QLabel,
        *,
        base_text: str | None = None,
        uppercase: bool = False,
    ) -> None:
        """Handle register warning header for settings dialog."""
        if not hasattr(self, "_warning_headers"):
            self._warning_headers = {}
            self._warning_header_base_texts = {}
        if not hasattr(self, "_warning_header_uppercase_keys"):
            self._warning_header_uppercase_keys = set()
        self._warning_headers[key] = label
        self._warning_header_base_texts[key] = base_text or key
        if uppercase:
            self._warning_header_uppercase_keys.add(key)
        else:
            self._warning_header_uppercase_keys.discard(key)

    def _warning_header_text(self, key: str) -> str:
        """Handle warning header text for settings dialog."""
        base = self._warning_header_base_texts.get(key, key)
        text = t(base)
        if key in getattr(self, "_warning_header_uppercase_keys", set()):
            return text.upper()
        return text

    def _set_warning_markers(self, warnings_by_target: dict[str, list[str]]) -> None:
        """Set warning markers."""
        if not hasattr(self, "_warning_headers"):
            return
        for key, label in self._warning_headers.items():
            base = self._warning_header_text(key)
            target_warnings = warnings_by_target.get(key, [])
            if target_warnings:
                label.setText(f"⚠ {base}")
                label.setToolTip("\n\n".join(t(warning) for warning in target_warnings))
            else:
                label.setText(base)
                label.setToolTip("")

    def _warning_values_from_current_ui(self) -> dict[str, str]:
        """Handle warning values from current ui for settings dialog."""
        def _section_vals(sk: str) -> tuple[str, str]:
            """Handle section vals for settings dialog."""
            rows = getattr(self, "_model_section_rows", {}).get(sk, [])
            if not rows:
                return "", ""
            primary = rows[0]
            return (
                str(primary["api_key_combo"].currentData() or ""),
                self._model_value(primary),
            )

        llm_p, llm_m = _section_vals("LLM")
        vis_p, vis_m = _section_vals("VISION_LLM")
        return {
            "LLM_PROVIDER": llm_p,
            "LLM_MODEL": llm_m,
            "VISION_LLM_PROVIDER": vis_p,
            "VISION_LLM_MODEL": vis_m,
        }

    def _refresh_capability_warning_markers(self) -> None:
        """Refresh capability warning markers."""
        _warnings, warnings_by_target = self._capability_warnings_for_values(
            self._warning_values_from_current_ui()
        )
        self._set_warning_markers(warnings_by_target)

    def _schedule_warning_marker_refresh(self) -> None:
        """Schedule warning marker refresh."""
        if getattr(self, "_disposing", False):
            return
        QTimer.singleShot(0, self._refresh_capability_warning_markers)

    def _area_group(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        """Return an unboxed section group using the 9b heading treatment."""
        w = QWidget()
        w.setObjectName("settingsAreaGroup")
        content_v = QVBoxLayout(w)
        content_v.setContentsMargins(0, 8, 0, 0)
        content_v.setSpacing(7)
        heading = QWidget()
        heading_layout = QHBoxLayout(heading)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(10)
        title_lbl = _WarningHeaderLabel(t(title).upper())
        title_lbl.setObjectName("areaHeader")
        self._register_warning_header(title, title_lbl, uppercase=True)
        rule = QFrame()
        rule.setObjectName("areaAccentLine")
        rule.setFixedHeight(1)
        heading_layout.addWidget(title_lbl)
        heading_layout.addWidget(rule, 1, Qt.AlignmentFlag.AlignVCenter)
        content_v.addWidget(heading)

        body_v = QVBoxLayout()
        body_v.setContentsMargins(0, 0, 0, 0)
        body_v.setSpacing(12)
        content_v.addLayout(body_v)
        return w, body_v

    def _provider_model_row(
        self,
        provider_field: QComboBox,
        model_field: QComboBox,
        test_label: QLabel,
        test_slot,
        fallback_key: str,
        fallback_prefix: str,
        fallback_providers: list[str] | None = None,
        test_btn_label: str = "Test",
    ) -> QWidget:
        """Provider | Model side-by-side + test row + fallbacks, returns a widget."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        pm = QWidget()
        pmh = QHBoxLayout(pm)
        pmh.setContentsMargins(0, 0, 0, 0)
        pmh.setSpacing(12)

        pc = QWidget()
        pvl = QVBoxLayout(pc)
        pvl.setContentsMargins(0, 0, 0, 0)
        pvl.setSpacing(4)
        pvl.addWidget(QLabel("Provider"))
        pvl.addWidget(provider_field)

        mc = QWidget()
        mvl = QVBoxLayout(mc)
        mvl.setContentsMargins(0, 0, 0, 0)
        mvl.setSpacing(4)
        mvl.addWidget(QLabel("Model"))
        mvl.addWidget(model_field)

        pmh.addWidget(pc)
        pmh.addWidget(mc)
        v.addWidget(pm)

        test_label.setWordWrap(True)
        tr = QWidget()
        trh = QHBoxLayout(tr)
        trh.setContentsMargins(0, 0, 0, 0)
        trh.setSpacing(10)
        trh.addWidget(self._button_row((test_btn_label, test_slot)))
        trh.addWidget(test_label, 1)
        v.addWidget(tr)

        fb_w = QWidget()
        fb_f = _expanding_form_layout(fb_w)
        fb_f.setContentsMargins(0, 4, 0, 0)
        fb_f.setSpacing(8)
        self._add_fallback_section(fb_f, fallback_key, fallback_prefix, providers=fallback_providers)
        v.addWidget(fb_w)

        return w

    def _set_secret_placeholder(self, edit: QLineEdit, text: str, *, stored: bool) -> None:
        """Set placeholder text and render stored secrets as masked dots."""
        edit.setPlaceholderText(t(text))
        if isinstance(edit, _SecretLineEdit):
            edit.setStoredSecretPlaceholder(stored)

    def _password(self) -> QLineEdit:
        """Handle password for settings dialog."""
        le = _SecretLineEdit()
        le.setEchoMode(QLineEdit.EchoMode.Password)
        return le

    def _add_fallback_section(
        self,
        form: QFormLayout,
        key: str,
        label_prefix: str,
        providers: list[str] | None = None,
    ) -> None:
        """Add fallback section."""
        add_btn = QPushButton(t("+ Add fallback"))
        add_wrap = QHBoxLayout()
        add_wrap.setContentsMargins(0, 0, 0, 0)
        add_wrap.addWidget(add_btn)
        add_wrap.addStretch()
        add_widget = QWidget()
        add_widget.setLayout(add_wrap)

        self._fields[key] = add_widget
        self._fallback_rows[key] = []
        self._fallback_rows[f"{key}__form"] = form  # type: ignore[index]
        self._fallback_rows[f"{key}__prefix"] = label_prefix  # type: ignore[index]
        self._fallback_rows[f"{key}__providers"] = providers or ["groq", "openai", "anthropic", "google", "chatgpt", "copilot"]  # type: ignore[index]
        self._fallback_rows[f"{key}__add_widget"] = add_widget  # type: ignore[index]
        add_btn.clicked.connect(lambda: self._add_fallback_row(key, providers=providers))
        form.addRow("", add_widget)

    def _add_fallback_row(
        self,
        key: str,
        provider: str = "",
        model: str = "",
        providers: list[str] | None = None,
    ) -> None:
        """Add fallback row."""
        provider_options = providers or self._fallback_rows.get(f"{key}__providers", ["groq", "openai", "anthropic", "google", "chatgpt", "copilot"])  # type: ignore[arg-type]
        provider_combo = self._combo(provider_options, provider)
        model_combo = self._model_combo(provider)
        if model:
            model_combo.setCurrentText(model)
        else:
            model_combo.lineEdit().setPlaceholderText(t("model"))
        provider_combo.currentIndexChanged.connect(
            lambda _: _refresh_model_combo(model_combo, _get(provider_combo))
        )
        remove_btn = QPushButton("Remove")
        model_row = QWidget()
        model_h = QHBoxLayout(model_row)
        model_h.setContentsMargins(0, 0, 0, 0)
        model_h.setSpacing(8)
        model_h.addWidget(model_combo)
        model_h.addWidget(remove_btn)

        provider_label = QLabel()
        model_label = QLabel()
        row_info = {
            "provider_label": provider_label,
            "provider": provider_combo,
            "model_label": model_label,
            "model_row": model_row,
            "model": model_combo,
        }
        remove_btn.clicked.connect(lambda: self._remove_fallback_row(key, row_info))
        form = self._fallback_rows[f"{key}__form"]  # type: ignore[index]
        add_widget = self._fallback_rows[f"{key}__add_widget"]  # type: ignore[index]
        insert_at, _role = form.getWidgetPosition(add_widget)
        form.insertRow(insert_at, provider_label, provider_combo)
        form.insertRow(insert_at + 1, model_label, model_row)
        self._fallback_rows[key].append(row_info)
        self._renumber_fallback_rows(key)
        self._wire_change_tracking(model_row)
        self._schedule_search_index_refresh()
        self._schedule_dirty_refresh()

    def _remove_fallback_row(self, key: str, row_info: dict) -> None:
        """Remove fallback row."""
        if row_info in self._fallback_rows[key]:
            self._fallback_rows[key].remove(row_info)
        form = self._fallback_rows[f"{key}__form"]  # type: ignore[index]
        form.removeRow(row_info["provider_label"])
        form.removeRow(row_info["model_label"])
        self._renumber_fallback_rows(key)
        self._schedule_search_index_refresh()
        self._schedule_dirty_refresh()

    def _renumber_fallback_rows(self, key: str) -> None:
        """Handle renumber fallback rows for settings dialog."""
        prefix = self._fallback_rows[f"{key}__prefix"]  # type: ignore[index]
        for idx, row in enumerate(self._fallback_rows[key], 1):
            row["provider_label"].setText(
                t("{prefix} provider {index}").format(prefix=prefix, index=idx)
            )
            row["model_label"].setText(
                t("{prefix} model {index}").format(prefix=prefix, index=idx)
            )

    def _set_fallback_rows(self, key: str, raw: str) -> None:
        """Set fallback rows."""
        for row in list(self._fallback_rows[key]):
            self._remove_fallback_row(key, row)
        for provider, model in _parse_fallback_rows(raw):
            self._add_fallback_row(key, provider, model)

    def _get_fallback_rows(self, key: str) -> str:
        """Return fallback rows."""
        parts = []
        for row in self._fallback_rows[key]:
            provider = _get(row["provider"]).strip()
            model = _get(row["model"]).strip()
            if provider and model:
                parts.append(f"{provider}:{model}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def _secret_configured_fast(self, name: str) -> bool:
        """Handle secret configured fast for settings dialog."""
        return bool((self._env.get(name) or "").strip()) or secret_store.has_secret(name)

    def _load_general_page_values(self) -> None:
        """Populate the General page, which is the only page built before showing.

        Every field touched here belongs to ``_tab_app``. Keeping it separate lets
        the window open with its first page already correct while the remaining
        pages and their values are built in the background -- see
        ``_build_deferred_pages``. ``_load_values`` calls this first, so the two
        never disagree.
        """
        import config as cfg

        _set(
            self._fields["CHAT_EXECUTION_MODE"],
            self._env.get("CHAT_EXECUTION_MODE", getattr(cfg, "CHAT_EXECUTION_MODE", "openwand")),
        )
        _set(
            self._fields["CHAT_CONVERSATION_OWNER"],
            self._env.get(
                "CHAT_CONVERSATION_OWNER",
                getattr(cfg, "CHAT_CONVERSATION_OWNER", "openwand"),
            ),
        )
        if self._fields["CHAT_EXECUTION_MODE"].currentData() == "openwand":  # type: ignore[attr-defined]
            _set(self._fields["CHAT_CONVERSATION_OWNER"], "openwand")

        # Read ICON_AUTO_HIDE, falling back to the legacy DOLL_AUTO_HIDE key.
        auto_hide = self._env.get(
            "ICON_AUTO_HIDE",
            self._env.get("DOLL_AUTO_HIDE", str(cfg.ICON_AUTO_HIDE)),
        ).lower() == "true"
        theme_mode = self._env.get("THEME_MODE", getattr(cfg, "THEME_MODE", "dark"))
        combo = self._fields["THEME_MODE"]
        idx = combo.findData(theme_mode)  # type: ignore[attr-defined]
        combo.setCurrentIndex(idx if idx >= 0 else 0)  # type: ignore[attr-defined]
        # Load both theme templates from env/config, then show the one for the
        # currently selected mode in the four swatches.
        self._theme_templates = {}
        for mode in ("light", "dark"):
            self._theme_templates[mode] = {
                role: self._env.get(
                    f"THEME_{mode.upper()}_{role.upper()}",
                    getattr(cfg, f"THEME_{mode.upper()}_{role.upper()}", ""),
                )
                for role in self._THEME_ROLES
            }
        self._theme_shown_mode = ""
        self._show_theme_template(self._theme_edit_mode())
        self._fields["ICON_AUTO_HIDE"].setChecked(auto_hide)  # type: ignore
        self._fields["START_ON_LOGIN"].setChecked(  # type: ignore
            self._env.get("START_ON_LOGIN", str(getattr(cfg, "START_ON_LOGIN", False))).lower()
            == "true"
        )
        self._fields["CHAT_OPEN_ON_PROMPT"].setChecked(
            self._env.get(
                "CHAT_OPEN_ON_PROMPT",
                str(getattr(cfg, "CHAT_OPEN_ON_PROMPT", False)),
            ).lower()
            == "true"
        )
        self._fields["CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE"].setChecked(
            self._env.get(
                "CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE",
                str(getattr(cfg, "CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE", False)),
            ).lower()
            == "true"
        )
        self._update_chat_open_on_prompt_options()
        self._fields["CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY"].setChecked(
            self._env.get(
                "CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY",
                str(getattr(cfg, "CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY", False)),
            ).lower()
            == "true"
        )
        privacy_mode = self._env.get("PRIVACY_MODE", "").strip().lower()
        if privacy_mode not in {"off", "builtin", "advanced"}:
            legacy_enabled = self._env.get(
                "TRUST_PRIVACY_MODE",
                str(getattr(cfg, "TRUST_PRIVACY_MODE", True)),
            ).lower() == "true"
            legacy_advanced = self._env.get(
                "PRIVACY_AI_ENABLED",
                str(getattr(cfg, "PRIVACY_AI_ENABLED", False)),
            ).lower() == "true"
            privacy_mode = (
                "advanced" if legacy_enabled and legacy_advanced
                else "builtin" if legacy_enabled
                else "off"
            )
        if privacy_mode == "advanced" and not bool(getattr(self, "_privacy_model_ready", False)):
            privacy_mode = "builtin"
        privacy_combo = self._fields["PRIVACY_MODE"]
        privacy_combo.blockSignals(True)  # type: ignore[attr-defined]
        privacy_combo.setCurrentIndex(max(0, privacy_combo.findData(privacy_mode)))  # type: ignore[attr-defined]
        privacy_combo.blockSignals(False)  # type: ignore[attr-defined]
        self._fields["PRIVACY_REVIEW_BEFORE_SEND"].setChecked(
            self._env.get(
                "PRIVACY_REVIEW_BEFORE_SEND",
                str(getattr(cfg, "PRIVACY_REVIEW_BEFORE_SEND", True)),
            ).lower() == "true"
        )
        injection_protection = self._env.get(
            "PROMPT_INJECTION_PROTECTION",
            str(getattr(cfg, "PROMPT_INJECTION_PROTECTION", True)),
        ).lower() == "true"
        self._fields["PROMPT_INJECTION_PROTECTION"].setChecked(injection_protection)
        self._fields["PROMPT_INJECTION_WARN"].setChecked(
            self._env.get(
                "PROMPT_INJECTION_WARN",
                str(getattr(cfg, "PROMPT_INJECTION_WARN", True)),
            ).lower() == "true"
        )
        self._fields["PROMPT_INJECTION_WARN"].setEnabled(injection_protection)
        for key in (
            "PRIVACY_HIDE_SECRETS",
            "PRIVACY_HIDE_CONTACT_DETAILS",
            "PRIVACY_HIDE_FINANCIAL_DETAILS",
            "PRIVACY_HIDE_GOVERNMENT_IDS",
            "PRIVACY_HIDE_URLS",
        ):
            self._fields[key].setChecked(
                self._env.get(key, str(getattr(cfg, key, True))).lower() == "true"
            )
        self._on_privacy_mode_changed()

        _set(
            self._fields["APP_LANGUAGE"],
            self._env.get("APP_LANGUAGE", getattr(cfg, "APP_LANGUAGE", "")),
        )
        _set(
            self._fields["ASSISTANT_LANGUAGE"],
            self._env.get("ASSISTANT_LANGUAGE", getattr(cfg, "ASSISTANT_LANGUAGE", "")),
        )

        _set(self._fields["ICON_SIZE"],    self._env.get("ICON_SIZE", self._env.get("DOLL_SIZE", str(cfg.ICON_SIZE))))
        _set(self._fields["BUBBLE_WIDTH"], self._env.get("BUBBLE_WIDTH", str(cfg.BUBBLE_WIDTH)))
        _set(self._fields["BUBBLE_LINES"], self._env.get("BUBBLE_LINES", str(cfg.BUBBLE_LINES)))
        _set(self._fields["BUBBLE_FONT_SIZE"], self._env.get("BUBBLE_FONT_SIZE", str(cfg.BUBBLE_FONT_SIZE)))
        self._fields["BUBBLE_SCROLL_ENABLED"].setChecked(  # type: ignore
            self._env.get(
                "BUBBLE_SCROLL_ENABLED",
                str(getattr(cfg, "BUBBLE_SCROLL_ENABLED", True)),
            ).lower()
            == "true"
        )
        self._fields["BUBBLE_SCROLL_SNAP_ENABLED"].setChecked(  # type: ignore
            self._env.get(
                "BUBBLE_SCROLL_SNAP_ENABLED",
                str(getattr(cfg, "BUBBLE_SCROLL_SNAP_ENABLED", True)),
            ).lower()
            == "true"
        )
        _set(self._fields["BUBBLE_COLOR"], self._env.get("BUBBLE_COLOR", cfg.BUBBLE_COLOR))
        _set(self._fields["BUBBLE_TEXT_COLOR"], self._env.get("BUBBLE_TEXT_COLOR", cfg.BUBBLE_TEXT_COLOR))
        _set(self._fields["BUBBLE_READ_WORD_COLOR"], self._env.get("BUBBLE_READ_WORD_COLOR", cfg.BUBBLE_READ_WORD_COLOR))

    def _load_values(self, *, include_general: bool = True):
        """Load values."""
        import config as cfg
        self._loading_values = True
        self._active_preset_slug = self._env.get(_SETTINGS_PRESET_KEY, "")
        if include_general:
            self._load_general_page_values()
        self._pending_active_profile = ""

        # ── API key rows ──────────────────────────────────────────────────
        for row in list(self._api_key_rows):
            self._remove_api_key_row(row)
        self._removed_connection_providers.clear()
        self._removed_custom_connection_ids.clear()

        _LLM_KEY_MAP = [
            ("groq",       "GROQ_API_KEY"),
            ("openai",     "OPENAI_API_KEY"),
            ("anthropic",  "ANTHROPIC_API_KEY"),
            ("google",     "GOOGLE_API_KEY"),
            ("deepseek",   "DEEPSEEK_API_KEY"),
            ("openrouter", "OPENROUTER_API_KEY"),
            ("mistral",    "MISTRAL_API_KEY"),
            ("xai",        "XAI_API_KEY"),
            ("together",   "TOGETHER_API_KEY"),
            ("cerebras",   "CEREBRAS_API_KEY"),
            ("zai",        "ZAI_API_KEY"),
            ("nvidia",     "NVIDIA_API_KEY"),
            ("sambanova",  "SAMBANOVA_API_KEY"),
            ("github_models", "GITHUB_MODELS_API_KEY"),
            ("huggingface", "HUGGINGFACE_API_KEY"),
            ("chutes",     "CHUTES_API_KEY"),
            ("vercel",     "VERCEL_API_KEY"),
            ("fireworks",  "FIREWORKS_API_KEY"),
            ("cohere",     "COHERE_API_KEY"),
            ("ai21",       "AI21_API_KEY"),
            ("nebius",     "NEBIUS_API_KEY"),
        ]
        for provider, key_name in _LLM_KEY_MAP:
            if self._secret_configured_fast(key_name):
                alias_key = f"OPENWAND_CONNECTION_ALIAS_{provider.upper()}"
                self._add_api_key_row(
                    provider=provider,
                    alias=self._env.get(alias_key, ""),
                    stored=True,
                )
        try:
            from core.auth import copilot_auth
            stored, _message = copilot_auth.token_status()
            if stored:
                self._add_api_key_row(
                    provider="copilot",
                    alias=self._env.get("OPENWAND_CONNECTION_ALIAS_COPILOT", ""),
                    stored=True,
                )
        except Exception:
            pass
        for connection in _load_custom_connections(self._env):
            connection_id = connection["id"]
            self._add_api_key_row(
                provider="custom",
                alias=connection.get("alias", ""),
                stored=self._secret_configured_fast(_custom_secret_name(connection_id)),
                connection_id=connection_id,
                base_url=connection.get("base_url", ""),
            )
        # No default placeholder row — the list stays empty until the user adds
        # a key via "+ Add API Key". Avoids a spurious "Groq" row on every open.

        # ── Model sections ────────────────────────────────────────────────
        selected_profile = self._selected_custom_profile_entry()
        selected_profile_values = (
            self._profile_values_from_env_slot(selected_profile[0])
            if selected_profile is not None
            else {}
        )

        def _load_section(sk, penv, menv, fenv, pdef, mdef, fdef=""):
            """Load section."""
            for r in list(self._model_section_rows[sk]):
                self._remove_model_section_row(sk, r)
            self._add_model_section_row(
                sk,
                selected_profile_values.get(penv, self._env.get(penv, pdef)),
                selected_profile_values.get(menv, self._env.get(menv, mdef)),
            )
            fallbacks = selected_profile_values.get(fenv, self._env.get(fenv, fdef))
            for p, m in _parse_fallback_rows(fallbacks):
                self._add_model_section_row(sk, p, m)

        _load_section("LLM",        "LLM_PROVIDER",        "LLM_MODEL",        "LLM_FALLBACKS",        cfg.LLM_PROVIDER,        cfg.LLM_MODEL,        cfg.LLM_FALLBACKS)
        _load_section("VISION_LLM", "VISION_LLM_PROVIDER", "VISION_LLM_MODEL", "VISION_LLM_FALLBACKS", cfg.VISION_LLM_PROVIDER, cfg.VISION_LLM_MODEL, cfg.VISION_LLM_FALLBACKS)
        _load_section("MEMORY_LLM", "MEMORY_LLM_PROVIDER", "MEMORY_LLM_MODEL", "MEMORY_LLM_FALLBACKS", cfg.MEMORY_LLM_PROVIDER, cfg.MEMORY_LLM_MODEL, cfg.MEMORY_LLM_FALLBACKS)
        self._fields["CHAT_TOOL_TRACE_UI"].setChecked(
            self._env.get(
                "CHAT_TOOL_TRACE_UI",
                str(getattr(cfg, "CHAT_TOOL_TRACE_UI", False)),
            ).strip().lower()
            in {"1", "true", "yes", "on"}
        )  # type: ignore[attr-defined]
        self._fields["OPENWAND_PLANNED_CHUNKING"].setChecked(
            self._env.get(
                "OPENWAND_PLANNED_CHUNKING",
                str(getattr(cfg, "PLANNED_CHUNKING", False)),
            ).strip().lower()
            in {"1", "true", "yes", "on"}
        )  # type: ignore[attr-defined]
        _set(
            self._fields["OPENWAND_PLANNED_CHUNKING_CHUNKS"],
            self._env.get(
                "OPENWAND_PLANNED_CHUNKING_CHUNKS",
                str(getattr(cfg, "PLANNED_CHUNKING_CHUNKS", 3)),
            ),
        )
        _set(
            self._fields["OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS"],
            self._env.get(
                "OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS",
                str(getattr(cfg, "PLANNED_CHUNKING_MIN_PROMPT_CHARS", 80)),
            ),
        )
        _set(
            self._fields["CHAT_REASONING_EFFORT"],
            self._env.get("CHAT_REASONING_EFFORT", getattr(cfg, "CHAT_REASONING_EFFORT", "high")),
        )

        # ── TTS / Custom keys (still in self._fields) ─────────────────────
        for name, _label in [
            ("CARTESIA_API_KEY",   "Cartesia"),
            ("ELEVENLABS_API_KEY", "ElevenLabs"),
            ("TTS_CUSTOM_API_KEY", "Custom TTS endpoint"),
            ("CLOUDFLARE_API_TOKEN", "Cloudflare Workers AI"),
            ("CUSTOM_API_KEY",     "Custom provider"),
        ]:
            if name not in self._fields:
                continue
            self._fields[name].clear()  # type: ignore[attr-defined]
            stored = self._secret_configured_fast(name)
            status = "stored in OS keychain" if stored else "not configured"
            self._set_secret_placeholder(
                self._fields[name],  # type: ignore[arg-type]
                status,
                stored=stored,
            )

        _set(self._fields["TTS_PROVIDER"], self._env.get("TTS_PROVIDER", cfg.TTS_PROVIDER))
        _set(
            self._fields["TTS_SPEAK_REPLIES"],
            self._env.get(
                "TTS_SPEAK_REPLIES",
                str(getattr(cfg, "TTS_SPEAK_REPLIES", False)),
            ),
        )
        _set(self._fields["CARTESIA_VOICE_ID"], self._env.get("CARTESIA_VOICE_ID", ""))
        _set(self._fields["ELEVENLABS_VOICE_ID"], self._env.get("ELEVENLABS_VOICE_ID", cfg.ELEVENLABS_VOICE_ID))
        _set(self._fields["ELEVENLABS_MODEL"], self._env.get("ELEVENLABS_MODEL", cfg.ELEVENLABS_MODEL))
        _set(self._fields["OPENAI_TTS_VOICE"], self._env.get("OPENAI_TTS_VOICE", cfg.OPENAI_TTS_VOICE))
        _set(self._fields["OPENAI_TTS_MODEL"], self._env.get("OPENAI_TTS_MODEL", cfg.OPENAI_TTS_MODEL))
        _set(self._fields["TTS_CUSTOM_BASE_URL"], self._env.get("TTS_CUSTOM_BASE_URL", cfg.TTS_CUSTOM_BASE_URL))
        _set(self._fields["TTS_CUSTOM_VOICE"], self._env.get("TTS_CUSTOM_VOICE", cfg.TTS_CUSTOM_VOICE))
        _set(self._fields["TTS_CUSTOM_MODEL"], self._env.get("TTS_CUSTOM_MODEL", cfg.TTS_CUSTOM_MODEL))
        _set(self._fields["TTS_CUSTOM_SAMPLE_RATE"], self._env.get("TTS_CUSTOM_SAMPLE_RATE", str(cfg.TTS_CUSTOM_SAMPLE_RATE)))
        _set(self._fields["GPT_SOVITS_URL"], self._env.get("GPT_SOVITS_URL", cfg.GPT_SOVITS_URL))
        _set(self._fields["GPT_SOVITS_REF_AUDIO_PATH"], self._env.get("GPT_SOVITS_REF_AUDIO_PATH", cfg.GPT_SOVITS_REF_AUDIO_PATH))
        _set(self._fields["GPT_SOVITS_PROMPT_TEXT"], self._env.get("GPT_SOVITS_PROMPT_TEXT", cfg.GPT_SOVITS_PROMPT_TEXT))
        _set(self._fields["GPT_SOVITS_PROMPT_LANG"], self._env.get("GPT_SOVITS_PROMPT_LANG", cfg.GPT_SOVITS_PROMPT_LANG))
        _set(self._fields["GPT_SOVITS_TEXT_LANG"], self._env.get("GPT_SOVITS_TEXT_LANG", cfg.GPT_SOVITS_TEXT_LANG))
        _set(self._fields["GPT_SOVITS_SAMPLE_RATE"], self._env.get("GPT_SOVITS_SAMPLE_RATE", str(cfg.GPT_SOVITS_SAMPLE_RATE)))
        _set(self._fields["KOKORO_VOICE"], self._env.get("KOKORO_VOICE", cfg.KOKORO_VOICE))
        _set(self._fields["KOKORO_LANG_CODE"], self._env.get("KOKORO_LANG_CODE", cfg.KOKORO_LANG_CODE))
        _set(self._fields["KOKORO_DEVICE"], self._env.get("KOKORO_DEVICE", getattr(cfg, "KOKORO_DEVICE", "auto")))
        _set(self._fields["KOKORO_SPEED"], self._env.get("KOKORO_SPEED", str(cfg.KOKORO_SPEED)))
        _set(self._fields["KOKORO_SAMPLE_RATE"], self._env.get("KOKORO_SAMPLE_RATE", str(cfg.KOKORO_SAMPLE_RATE)))
        live_voice_provider = self._env.get(
            "LIVE_VOICE_PROVIDER",
            getattr(cfg, "LIVE_VOICE_PROVIDER", "google"),
        )
        live_voice_model = self._env.get(
            "LIVE_VOICE_MODEL",
            getattr(cfg, "LIVE_VOICE_MODEL", "gemini-3.1-flash-live-preview"),
        )
        live_voice_provider_combo = self._fields["LIVE_VOICE_PROVIDER"]
        if isinstance(live_voice_provider_combo, QComboBox):
            _set(live_voice_provider_combo, live_voice_provider)
        selected_live_voice_provider = _get(self._fields["LIVE_VOICE_PROVIDER"]).strip() or "google"
        if selected_live_voice_provider != "none":
            self._fill_live_voice_model_combo(selected_live_voice_provider, live_voice_model)
        self._fill_live_voice_voice_combo(
            self._env.get("LIVE_VOICE_VOICE_NAME", getattr(cfg, "LIVE_VOICE_VOICE_NAME", ""))
        )
        _set(
            self._fields["LIVE_VOICE_HALF_DUPLEX"],
            self._env.get("LIVE_VOICE_HALF_DUPLEX", str(getattr(cfg, "LIVE_VOICE_HALF_DUPLEX", False))),
        )
        self._update_live_voice_provider_fields()
        _set(self._fields["TTS_VOLUME"], self._env.get("TTS_VOLUME", str(getattr(cfg, "TTS_VOLUME", 1.0))))
        _set(
            self._fields["TTS_READ_ALOUD_MIN_WORDS"],
            self._env.get("TTS_READ_ALOUD_MIN_WORDS", str(getattr(cfg, "TTS_READ_ALOUD_MIN_WORDS", 50))),
        )
        _set(
            self._fields["TTS_READ_ALOUD_MAX_WORDS"],
            self._env.get("TTS_READ_ALOUD_MAX_WORDS", str(getattr(cfg, "TTS_READ_ALOUD_MAX_WORDS", 110))),
        )
        self._update_tts_provider_fields()
        _set(
            self._fields["STT_PROVIDER"],
            self._env.get("STT_PROVIDER", getattr(cfg, "STT_PROVIDER", "local")),
        )
        _set(self._fields["STT_MODEL"], self._env.get("STT_MODEL", cfg.STT_MODEL))
        self._rebuild_stt_languages()  # drop yue if the loaded model isn't large-v3
        _set(self._fields["STT_COMPUTE_TYPE"], self._env.get("STT_COMPUTE_TYPE", cfg.STT_COMPUTE_TYPE))
        _set(self._fields["STT_LANGUAGE"], self._env.get("STT_LANGUAGE", cfg.STT_LANGUAGE))
        _set(self._fields["STT_BEAM_SIZE"], self._env.get("STT_BEAM_SIZE", str(cfg.STT_BEAM_SIZE)))
        _set(self._fields["STT_DEVICE"], self._env.get("STT_DEVICE", cfg.STT_DEVICE))
        _set(
            self._fields["STT_CLOUDFLARE_ACCOUNT_ID"],
            self._env.get(
                "STT_CLOUDFLARE_ACCOUNT_ID",
                getattr(cfg, "STT_CLOUDFLARE_ACCOUNT_ID", ""),
            ),
        )
        _set(
            self._fields["STT_CLOUDFLARE_FALLBACK_LOCAL"],
            self._env.get(
                "STT_CLOUDFLARE_FALLBACK_LOCAL",
                str(getattr(cfg, "STT_CLOUDFLARE_FALLBACK_LOCAL", True)),
            ),
        )
        self._update_stt_provider_fields()
        _set(
            self._fields["STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS"],
            self._env.get(
                "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS",
                str(getattr(cfg, "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS", 15.0)),
            ),
        )
        _set(
            self._fields["STT_BACKGROUND_CHUNK_STEP_SECONDS"],
            self._env.get(
                "STT_BACKGROUND_CHUNK_STEP_SECONDS",
                str(getattr(cfg, "STT_BACKGROUND_CHUNK_STEP_SECONDS", 10.0)),
            ),
        )
        _set(
            self._fields["STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS"],
            self._env.get(
                "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS",
                str(getattr(cfg, "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS", 4.5)),
            ),
        )
        _set(
            self._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"],
            self._env.get(
                "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS",
                str(getattr(cfg, "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS", 1.0)),
            ),
        )
        for hotkey_name in (
            "HOTKEY_ADD_CONTEXT",
            "HOTKEY_CLEAR_CONTEXT",
            "HOTKEY_SNIP",
            "HOTKEY_READ_SELECTION_ALOUD",
            "HOTKEY_VOICE_LIVE",
        ):
            _set(
                self._fields[hotkey_name],
                self._env.get(hotkey_name, getattr(cfg, hotkey_name, "")),
            )
            _set(
                self._fields[f"{hotkey_name}_2"],
                self._env.get(f"{hotkey_name}_2", getattr(cfg, f"{hotkey_name}_2", "")),
            )
            _set(
                self._fields[f"{hotkey_name}_ENABLED"],
                self._env.get(
                    f"{hotkey_name}_ENABLED",
                    str(getattr(cfg, f"{hotkey_name}_ENABLED", True)),
                ),
            )
        _set(self._fields["INTENT_CONTEXT_TOGGLE_KEYS"], self._env.get(
            "INTENT_CONTEXT_TOGGLE_KEYS",
            getattr(cfg, "INTENT_CONTEXT_TOGGLE_KEYS", "12345678"),
        ))
        _set(self._fields["INTENT_OVERLAY_TIMEOUT_MS"], self._env.get(
            "INTENT_OVERLAY_TIMEOUT_MS",
            str(getattr(cfg, "INTENT_OVERLAY_TIMEOUT_MS", 60000)),
        ))
        sc = dict(getattr(cfg, "SNIP_CALLER", {}) or {})
        sb = self._snip_block
        legacy_snip_documents = self._env.get(
            "SNIP_CONTEXT_DOCUMENTS",
            str(sc.get("context_documents", getattr(cfg, "SNIP_CONTEXT_DOCUMENTS", False))),
        ).lower() == "true"
        legacy_snip_tools = self._env.get(
            "SNIP_CONTEXT_TOOLS",
            str(sc.get("context_tools", getattr(cfg, "SNIP_CONTEXT_TOOLS", False))),
        ).lower() == "true"
        snip_documents_mode = self._env.get(
            "SNIP_CONTEXT_DOCUMENTS_MODE",
            sc.get("context_documents_mode")
            or ("auto" if legacy_snip_documents else ("model" if legacy_snip_tools else "off")),
        )
        sb["context_ambient"].setChecked(
            self._env.get("SNIP_CONTEXT_AMBIENT", str(sc.get("context_ambient", False))).lower() == "true"
        )
        sb["context_clipboard"].setChecked(
            self._env.get("SNIP_CONTEXT_CLIPBOARD", str(sc.get("context_clipboard", False))).lower() == "true"
        )
        _set(sb["context_documents_mode"], snip_documents_mode)
        _set(
            sb["context_browser_mode"],
            self._env.get(
                "SNIP_CONTEXT_BROWSER_MODE",
                "model" if legacy_snip_tools else (sc.get("context_browser_mode") or "off"),
            ),
        )
        _set(
            sb["context_github_mode"],
            self._env.get(
                "SNIP_CONTEXT_GITHUB_MODE",
                "model" if legacy_snip_tools else (sc.get("context_github_mode") or "off"),
            ),
        )
        _set(
            sb["context_memory_mode"],
            self._env.get("SNIP_CONTEXT_MEMORY_MODE", sc.get("context_memory_mode") or "on"),
        )
        _set(sb["context_screenshot"], "off")
        _set(
            sb["file_access"],
            normalize_file_access_mode(self._env.get("SNIP_FILE_ACCESS", sc.get("file_access", "off"))),
        )
        snip_tools_env = self._env.get("SNIP_TOOLS")
        sb["tool_overrides"] = (
            parse_tool_modes(snip_tools_env)
            if snip_tools_env is not None
            else dict(sc.get("tools") or {})
        )
        _set(self._fields["CUSTOM_BASE_URL"],      self._env.get("CUSTOM_BASE_URL",      cfg.CUSTOM_BASE_URL))
        self._sync_custom_endpoint_selection()
        _set(self._fields["GITHUB_CLIENT_ID"],     self._env.get("GITHUB_CLIENT_ID",     cfg.GITHUB_CLIENT_ID))
        _set(self._fields["GITHUB_OAUTH_SCOPES"],  self._env.get("GITHUB_OAUTH_SCOPES",  cfg.GITHUB_OAUTH_SCOPES))
        for key, default in (
            ("CONTEXT_BROWSER_MAX_CHARS", cfg.CONTEXT_BROWSER_MAX_CHARS),
            ("CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS", cfg.CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS),
            ("CONTEXT_TOOL_DOCUMENT_MAX_CHARS", cfg.CONTEXT_TOOL_DOCUMENT_MAX_CHARS),
        ):
            value = selected_profile_values.get(key, self._env.get(key, str(default)))
            _set(self._fields[key], value)
        _set(self._fields["TOOL_FILE_ROOTS"], self._env.get("TOOL_FILE_ROOTS", "\n".join(getattr(cfg, "TOOL_FILE_ROOTS", []))))
        _set(
            self._fields["TOOL_FILE_BLOCKED_GLOBS"],
            self._env.get(
                "TOOL_FILE_BLOCKED_GLOBS",
                "\n".join(getattr(cfg, "TOOL_FILE_BLOCKED_GLOBS", [])),
            ),
        )

        self._fields["MEMORY_AUTO_CONSOLIDATE"].setChecked(
            self._env.get("MEMORY_AUTO_CONSOLIDATE", str(cfg.MEMORY_AUTO_CONSOLIDATE)).lower() == "true"
        )  # type: ignore
        _set(self._fields["MEMORY_CONSOLIDATION_INTERVAL"], self._env.get("MEMORY_CONSOLIDATION_INTERVAL", str(cfg.MEMORY_CONSOLIDATION_INTERVAL)))
        _set(self._fields["MEMORY_TOP_K"],           self._env.get("MEMORY_TOP_K",           str(cfg.MEMORY_TOP_K)))
        _set(self._fields["MEMORY_STM_TOKEN_BUDGET"], self._env.get("MEMORY_STM_TOKEN_BUDGET", str(cfg.MEMORY_STM_TOKEN_BUDGET)))

        # Build caller blocks from the live action-file tree. Legacy CALLER_*
        # environment values remain readable by old releases but are ignored here.
        caller_widgets = [
            blk.get("widget")
            for blk in self._caller_blocks
            if blk.get("widget") is not None
        ]
        self._shortcut_rows = [
            entry
            for entry in getattr(self, "_shortcut_rows", [])
            if all(entry.get("widget") is not widget for widget in caller_widgets)
        ]
        for section in getattr(self, "_shortcut_sections", []):
            section["entries"] = [
                entry
                for entry in section["entries"]
                if all(entry.get("widget") is not widget for widget in caller_widgets)
            ]
        for widget in caller_widgets:
            widget.deleteLater()
        self._caller_blocks.clear()

        caller_count = len(cfg.CALLER_ROWS)
        for i in range(caller_count):
            cr = cfg.CALLER_ROWS[i] if i < len(cfg.CALLER_ROWS) else {}
            intent_count = len(cr.get("intents", []))
            intents = []
            for j in range(intent_count):
                di = cr["intents"][j] if j < len(cr.get("intents", [])) else {}
                intent = dict(di)
                intents.append(cfg.localize_intent_if_default(
                    i,
                    j,
                    intent,
                    self._env.get("ASSISTANT_LANGUAGE", getattr(cfg, "ASSISTANT_LANGUAGE", "")),
                ))
            documents_mode = cr.get("context_documents_mode") or "off"
            browser_mode = cr.get("context_browser_mode") or "off"
            github_mode = cr.get("context_github_mode") or "off"
            memory_mode = cr.get("context_memory_mode") or "off"
            file_access = cr.get("file_access") or "off"
            caller_tools = dict(cr.get("tools") or {})
            self._add_caller_block(
                folder = str(cr.get("folder") or ""),
                hotkey = str(cr.get("hotkey") or ""),
                hotkey_2 = str(cr.get("hotkey_2") or ""),
                enabled = bool(cr.get("enabled", True)),
                label = str(cr.get("label") or ""),
                paste_back = bool(cr.get("paste_back", False)),
                custom_key = str(cr.get("custom_key") or "s"),
                custom_label = str(cr.get("custom_label") or ""),
                space_starts_new_chat = bool(cr.get("space_starts_new_chat", True)),
                context_selection_enabled = bool(cr.get("_context_selection_enabled", True)),
                context_ambient = bool(cr.get("context_ambient", False)),
                context_clipboard = bool(cr.get("context_clipboard", False)),
                context_documents = documents_mode == "auto",
                context_tools = any(mode == "model" for mode in (documents_mode, browser_mode, github_mode, memory_mode)),
                context_documents_mode = documents_mode,
                context_browser_mode = browser_mode,
                context_github_mode = github_mode,
                context_memory_mode = memory_mode,
                context_screenshot = normalize_screenshot_mode(cr.get("context_screenshot", "off")),
                file_access = normalize_file_access_mode(file_access),
                tools      = caller_tools,
                display_language = self._env.get(
                    "APP_LANGUAGE",
                    getattr(cfg, "APP_LANGUAGE", ""),
                ) or current_app_language(),
                intents    = intents,
            )

        # Voice (push-to-talk) block
        vc = dict(getattr(cfg, "VOICE_CALLER", {}) or {})
        _set(self._fields["HOTKEY_VOICE"], self._env.get("HOTKEY_VOICE", vc.get("hotkey", cfg.HOTKEY_VOICE)))
        _set(
            self._fields["HOTKEY_VOICE_2"],
            self._env.get("HOTKEY_VOICE_2", vc.get("hotkey_2", getattr(cfg, "HOTKEY_VOICE_2", ""))),
        )
        _set(
            self._fields["HOTKEY_VOICE_ENABLED"],
            self._env.get(
                "HOTKEY_VOICE_ENABLED",
                str(vc.get("enabled", getattr(cfg, "HOTKEY_VOICE_ENABLED", True))),
            ),
        )
        _set(
            self._fields["VOICE_REVIEW_TRANSCRIPT"],
            self._env.get(
                "VOICE_REVIEW_TRANSCRIPT",
                str(getattr(cfg, "VOICE_REVIEW_TRANSCRIPT", False)),
            ),
        )
        _set(self._fields["HOTKEY_DICTATE"], self._env.get("HOTKEY_DICTATE", cfg.HOTKEY_DICTATE))
        _set(
            self._fields["HOTKEY_DICTATE_2"],
            self._env.get("HOTKEY_DICTATE_2", getattr(cfg, "HOTKEY_DICTATE_2", "")),
        )
        _set(
            self._fields["HOTKEY_DICTATE_ENABLED"],
            self._env.get(
                "HOTKEY_DICTATE_ENABLED",
                str(getattr(cfg, "HOTKEY_DICTATE_ENABLED", True)),
            ),
        )
        _set(self._fields["DICTATE_MODE"], self._env.get("DICTATE_MODE", cfg.DICTATE_MODE))
        vb = self._voice_block
        vb["context_ambient"].setChecked(
            self._env.get("VOICE_CONTEXT_AMBIENT", str(vc.get("context_ambient", False))).lower() == "true"
        )
        vb["context_clipboard"].setChecked(
            self._env.get("VOICE_CONTEXT_CLIPBOARD", str(vc.get("context_clipboard", False))).lower() == "true"
        )
        _set(
            vb["context_documents_mode"],
            self._env.get("VOICE_CONTEXT_DOCUMENTS_MODE", vc.get("context_documents_mode") or "off"),
        )
        _set(
            vb["context_browser_mode"],
            self._env.get("VOICE_CONTEXT_BROWSER_MODE", vc.get("context_browser_mode") or "off"),
        )
        _set(
            vb["context_github_mode"],
            self._env.get("VOICE_CONTEXT_GITHUB_MODE", vc.get("context_github_mode") or "off"),
        )
        _set(
            vb["context_memory_mode"],
            self._env.get("VOICE_CONTEXT_MEMORY_MODE", vc.get("context_memory_mode") or "on"),
        )
        _set(
            vb["context_screenshot"],
            normalize_screenshot_mode(
                self._env.get("VOICE_CONTEXT_SCREENSHOT", vc.get("context_screenshot", "off"))
            ),
        )
        _set(
            vb["file_access"],
            normalize_file_access_mode(self._env.get("VOICE_FILE_ACCESS", vc.get("file_access", "off"))),
        )
        voice_tools_env = self._env.get("VOICE_TOOLS")
        vb["tool_overrides"] = (
            parse_tool_modes(voice_tools_env)
            if voice_tools_env is not None
            else dict(vc.get("tools") or {})
        )

        auto_elab = self._env.get("CHAT_AUTO_ELABORATE", str(cfg.CHAT_AUTO_ELABORATE)).lower() == "true"
        self._fields["CHAT_AUTO_ELABORATE"].setChecked(auto_elab)  # type: ignore
        assistant_language = self._env.get("ASSISTANT_LANGUAGE", getattr(cfg, "ASSISTANT_LANGUAGE", ""))
        default_elaborate_prompt = cfg.localize_chat_elaborate_prompt_if_default(
            self._env.get("CHAT_ELABORATE_PROMPT", getattr(cfg, "CHAT_ELABORATE_PROMPT", "")),
            assistant_language,
        )
        _set(self._fields["CHAT_ELABORATE_PROMPT"],
             default_elaborate_prompt)

        _set(self._fields["BUBBLE_REVEAL_WPM"], self._env.get("BUBBLE_REVEAL_WPM", str(cfg.BUBBLE_REVEAL_WPM)))
        _set(self._fields["BUBBLE_HOLD_REVEAL_WPM"], self._env.get("BUBBLE_HOLD_REVEAL_WPM", str(cfg.BUBBLE_HOLD_REVEAL_WPM)))
        _set(
            self._fields["BUBBLE_HIDE_DELAY_S"],
            _ms_to_seconds_str(
                self._env.get("BUBBLE_HIDE_DELAY_MS", str(cfg.BUBBLE_HIDE_DELAY_MS)),
                cfg.BUBBLE_HIDE_DELAY_MS,
            ),
        )
        _set(
            self._fields["BUBBLE_SCROLL_SNAP_DELAY_S"],
            _ms_to_seconds_str(
                self._env.get(
                    "BUBBLE_SCROLL_SNAP_DELAY_MS",
                    str(getattr(cfg, "BUBBLE_SCROLL_SNAP_DELAY_MS", 2500)),
                ),
                getattr(cfg, "BUBBLE_SCROLL_SNAP_DELAY_MS", 2500),
            ),
        )
        _set(self._fields["TTS_PLAYBACK_RATE"], self._env.get("TTS_PLAYBACK_RATE", str(cfg.TTS_PLAYBACK_RATE)))
        _set(self._fields["TTS_HOLD_PLAYBACK_RATE"], self._env.get("TTS_HOLD_PLAYBACK_RATE", str(cfg.TTS_HOLD_PLAYBACK_RATE)))

        util_val = cfg.localize_system_prompt_utility_if_default(
            self._env.get("SYSTEM_PROMPT_UTILITY", cfg.SYSTEM_PROMPT_UTILITY),
            assistant_language,
        )
        self._fields["SYSTEM_PROMPT_UTILITY"].setPlainText(util_val)  # type: ignore
        self._fields["OPENWAND_CODEX_SYSTEM_PROMPT"].setPlainText(  # type: ignore[attr-defined]
            self._env.get(
                "OPENWAND_CODEX_SYSTEM_PROMPT",
                getattr(cfg, "OPENWAND_CODEX_SYSTEM_PROMPT", ""),
            )
        )
        self._fields["OPENWAND_CLAUDE_SYSTEM_PROMPT"].setPlainText(  # type: ignore[attr-defined]
            self._env.get(
                "OPENWAND_CLAUDE_SYSTEM_PROMPT",
                getattr(cfg, "OPENWAND_CLAUDE_SYSTEM_PROMPT", ""),
            )
        )
        self._refresh_capability_warning_markers()
        self._refresh_profile_button_text()
        self._loading_values = False
        self._wire_change_tracking(self)
        self._refresh_search_index()
        self._reset_dirty_baseline()

    def _effective_secret_value(self, name: str) -> str:
        """Handle effective secret value for settings dialog."""
        typed = _get(self._fields[name]).strip()
        if typed:
            return typed
        import config as cfg

        return getattr(cfg, name, "")

    def _set_test_status(self, label: QLabel, ok, message: str) -> None:
        """Colour a test status label. ``ok`` is True (green), False (red), or the
        string "warn" (amber) for a partial pass (e.g. primary works, fallback failed)."""
        if ok == "warn":
            color = "#d8932a"
        elif ok:
            color = "#80c080"
        else:
            color = "#c04040"
        label.setText(_translate_status_message(message))
        label.setStyleSheet(f"color: {color};")

    def _set_test_pending(self, label: QLabel, message: str = "Testing...") -> None:
        """Set test pending."""
        label.setText(_translate_status_message(message))
        label.setStyleSheet("color: #c0c040;")

    def _queue_test_progress(self, test_key: str, token: int, message: str) -> None:
        """Queue an in-progress status update from a worker thread."""
        with self._pending_test_progress_lock:
            self._pending_test_progress.append((test_key, token, message))

    def _set_status_label(self, label: QLabel, ok, message: str) -> None:
        """Set status label."""
        oauth_key = str(label.property("oauthProviderKey") or "")
        if oauth_key in {"chatgpt", "github"}:
            translated = _translate_status_message(message)
            if ok is False:
                self._set_oauth_status(
                    oauth_key,
                    t("Status unavailable"),
                    signed_in=None,
                    message=translated,
                )
            elif ok is True:
                self._set_oauth_status(oauth_key, translated, signed_in=True)
            elif str(message or "").strip().casefold() == "not logged in":
                self._set_oauth_status(oauth_key, translated, signed_in=False)
            else:
                self._set_oauth_status(
                    oauth_key,
                    translated,
                    signed_in=None,
                    busy=True,
                )
            return
        if ok is None:
            color = "palette(placeholder-text)"
        elif ok:
            color = "#80c080"
        else:
            color = "#c04040"
        label.setText(_translate_status_message(message))
        label.setStyleSheet(f"color: {color};")

    def _queue_status_result(self, token: int, attr: str, ok, message: str) -> None:
        """Handle queue status result for settings dialog."""
        with self._pending_status_results_lock:
            self._pending_status_results.append((token, attr, ok, message))

    def _cancel_status_refresh(self) -> None:
        """Cancel status refresh."""
        self._status_refresh_token += 1
        self._status_refresh_running = False
        self._pending_status_attrs = set()
        with self._pending_status_results_lock:
            self._pending_status_results.clear()
        if self._status_result_timer.isActive():
            self._status_result_timer.stop()

    def _cancel_async_ui_updates(self) -> None:
        """Cancel async ui updates."""
        self._cancel_status_refresh()
        self._tts_install_status_token = int(getattr(self, "_tts_install_status_token", 0) or 0) + 1
        self._tts_install_status_running = False
        self._running_test_tokens.clear()
        self._latest_test_token.clear()
        with self._pending_test_results_lock:
            self._pending_test_results.clear()
        with self._pending_test_progress_lock:
            self._pending_test_progress.clear()
        if self._test_result_timer.isActive():
            self._test_result_timer.stop()
        for timer_name in (
            "_auth_poll_timer",
            "_github_auth_poll_timer",
            "_harness_auth_poll_timer",
        ):
            timer = getattr(self, timer_name, None)
            if timer is not None and timer.isActive():
                timer.stop()
        self._harness_auth_poll_target = None
        self._harness_auth_poll_provider = ""

    def _schedule_open_status_refresh(self) -> None:
        """Schedule open status refresh."""
        if getattr(self, "_disposing", False):
            return
        self._status_refresh_token += 1
        token = self._status_refresh_token
        self._status_refresh_running = True
        self._pending_status_attrs = {
            "_chatgpt_status_lbl",
            "_github_status_lbl",
        }
        harness_provider = self._selected_harness_provider()
        if harness_provider:
            self._pending_status_attrs.add("_harness_auth_status_lbl")
        for attr in self._pending_status_attrs:
            label = getattr(self, attr, None)
            if isinstance(label, QLabel):
                self._set_status_label(label, None, "Checking status...")
        if harness_provider:
            self._harness_login_btn.setEnabled(False)
            self._harness_logout_btn.setEnabled(False)
        if not self._status_result_timer.isActive():
            self._status_result_timer.start()
        QTimer.singleShot(_AUTH_STATUS_TIMEOUT_MS, lambda tok=token: self._expire_status_refresh(tok))

        def _chatgpt_worker() -> None:
            """Verify ChatGPT sign-in without blocking other providers."""
            try:
                from core.auth import chatgpt as chatgpt_auth

                verified, _account = chatgpt_auth.validate_login(timeout_seconds=5.0)
                if verified:
                    self._queue_status_result(token, "_chatgpt_status_lbl", True, "Logged in")
                else:
                    self._queue_status_result(token, "_chatgpt_status_lbl", None, "Not logged in")
            except Exception as exc:
                self._queue_status_result(token, "_chatgpt_status_lbl", False, f"Error reading status: {exc}")

        def _github_worker() -> None:
            """Verify GitHub sign-in without waiting on ChatGPT/Copilot."""
            try:
                from core.auth import github as github_auth

                verified, login = github_auth.validate_login(timeout_seconds=5.0)
                if verified:
                    label = "Logged in" + (f" as {login}" if login else "")
                    self._queue_status_result(token, "_github_status_lbl", True, label)
                else:
                    self._queue_status_result(token, "_github_status_lbl", None, "Not logged in")
            except Exception as exc:
                self._queue_status_result(token, "_github_status_lbl", False, f"Error reading status: {exc}")

        def _harness_worker() -> None:
            """Read the selected local agent CLI's login state."""
            try:
                from core.harness_clients.auth import harness_login_status

                status = harness_login_status(harness_provider)
                ok = (
                    True if status.available and status.logged_in is True
                    else None if status.available and status.logged_in is False
                    else False
                )
                self._queue_status_result(
                    token,
                    "_harness_auth_status_lbl",
                    ok,
                    status.message,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced in Settings
                self._queue_status_result(
                    token,
                    "_harness_auth_status_lbl",
                    False,
                    f"Error reading status: {exc}",
                )

        for name, worker in (
            ("settings-status-chatgpt", _chatgpt_worker),
            ("settings-status-github", _github_worker),
        ):
            threading.Thread(target=worker, daemon=True, name=name).start()
        if harness_provider:
            threading.Thread(
                target=_harness_worker,
                daemon=True,
                name=f"settings-status-{harness_provider}",
            ).start()

    def _apply_status_result(self, attr: str, ok, message: str) -> None:
        """Apply one queued auth result and update provider-specific controls."""
        if getattr(self, "_disposing", False):
            return
        label = getattr(self, attr, None)
        if isinstance(label, QLabel):
            try:
                self._set_status_label(label, ok, message)
            except RuntimeError:
                # A single-shot timeout can outlive a dialog deleted directly
                # by a host or test without emitting ``finished`` first.
                return
        if attr != "_harness_auth_status_lbl":
            return
        target = getattr(self, "_harness_auth_poll_target", None)
        if target is True and ok is None:
            self._set_status_label(
                self._harness_auth_status_lbl,
                None,
                "Waiting for agent sign-in to finish...",
            )
            self._harness_login_btn.setEnabled(False)
            self._harness_logout_btn.setEnabled(False)
            return
        self._harness_login_btn.setEnabled(ok is not False)
        self._harness_logout_btn.setEnabled(ok is True)
        reached_target = (target is True and ok is True) or (target is False and ok is None)
        if reached_target:
            timer = getattr(self, "_harness_auth_poll_timer", None)
            if timer is not None:
                timer.stop()
            self._harness_auth_poll_target = None
            self._harness_auth_poll_provider = ""

    def _expire_status_refresh(self, token: int) -> None:
        """Replace stuck open-time auth checks with an actionable status."""
        if token != self._status_refresh_token or not self._status_refresh_running:
            return
        with self._pending_status_results_lock:
            queued = [item for item in self._pending_status_results if item[0] == token]
            self._pending_status_results = [
                item for item in self._pending_status_results if item[0] != token
            ]
        for _token, attr, ok, message in queued:
            if attr not in self._pending_status_attrs:
                continue
            self._apply_status_result(attr, ok, message)
            self._pending_status_attrs.discard(attr)
        if not self._pending_status_attrs:
            self._status_refresh_running = False
            try:
                if self._status_result_timer.isActive():
                    self._status_result_timer.stop()
            except RuntimeError:
                pass
            return
        message = t("Status check timed out. Sign-in may still work; try again or restart OpenWand.")
        for attr in tuple(self._pending_status_attrs):
            self._apply_status_result(attr, False, message)
        self._pending_status_attrs = set()
        self._status_refresh_running = False
        try:
            if self._status_result_timer.isActive():
                self._status_result_timer.stop()
        except RuntimeError:
            pass

    def _drain_status_results(self) -> None:
        """Handle drain status results for settings dialog."""
        if getattr(self, "_disposing", False):
            self._cancel_status_refresh()
            return
        with self._pending_status_results_lock:
            pending = list(self._pending_status_results)
            self._pending_status_results.clear()
        for token, attr, ok, message in pending:
            if token != self._status_refresh_token:
                continue
            if attr not in self._pending_status_attrs:
                continue
            self._apply_status_result(attr, ok, message)
            self._pending_status_attrs.discard(attr)
        if not self._pending_status_attrs:
            self._status_refresh_running = False
        if not self._status_refresh_running and not pending:
            self._status_result_timer.stop()

    def _start_async_test(
        self,
        test_key: str,
        status_label: QLabel,
        runner,
        *,
        pending_message: str = "Testing...",
    ) -> None:
        """Start async test."""
        if getattr(self, "_disposing", False):
            return
        token = self._latest_test_token.get(test_key, 0) + 1
        self._latest_test_token[test_key] = token
        self._running_test_tokens.add((test_key, token))
        self._set_test_pending(status_label, pending_message)

        def _worker() -> None:
            """Handle worker for settings dialog."""
            try:
                ok, message = runner()
            except Exception as exc:
                ok, message = False, f"Test failed: {exc}"
            with self._pending_test_results_lock:
                self._pending_test_results.append((test_key, token, ok, message))

        threading.Thread(target=_worker, daemon=True).start()
        if not self._test_result_timer.isActive():
            self._test_result_timer.start()

    def _drain_test_results(self) -> None:
        """Handle drain test results for settings dialog."""
        if getattr(self, "_disposing", False):
            self._cancel_async_ui_updates()
            return
        with self._pending_test_progress_lock:
            progress = list(self._pending_test_progress)
            self._pending_test_progress.clear()
        with self._pending_test_results_lock:
            pending = list(self._pending_test_results)
            self._pending_test_results.clear()
        latest_progress: dict[tuple[str, int], str] = {}
        for test_key, token, message in progress:
            latest_progress[(test_key, token)] = message
        for (test_key, token), message in latest_progress.items():
            if self._latest_test_token.get(test_key) != token:
                continue
            label = getattr(self, f"_{test_key}_status_lbl", None)
            if isinstance(label, QLabel):
                self._set_test_pending(label, message)
        for test_key, token, ok, message in pending:
            self._running_test_tokens.discard((test_key, token))
            if self._latest_test_token.get(test_key) != token:
                continue
            label_attr = (
                "_kokoro_install_status_lbl"
                if test_key == "kokoro_assets"
                else f"_{test_key}_status_lbl"
            )
            label = getattr(self, label_attr, None)
            if isinstance(label, QLabel):
                self._set_test_status(label, ok, message)
            if test_key == "kokoro_install" and ok:
                self._refresh_kokoro_install_status()
            elif test_key == "kokoro_install":
                button = getattr(self, "_kokoro_install_btn", None)
                if isinstance(button, QPushButton):
                    button.setEnabled(True)
                self._refresh_kokoro_install_status()
            elif test_key == "elevenlabs_install" and ok:
                self._refresh_elevenlabs_install_status()
            elif test_key == "elevenlabs_install":
                button = getattr(self, "_elevenlabs_install_btn", None)
                if isinstance(button, QPushButton):
                    button.setEnabled(True)
            elif test_key == "kokoro_assets":
                button = getattr(self, "_kokoro_assets_btn", None)
                if isinstance(button, QPushButton):
                    button.setEnabled(True)
            elif test_key == "stt_install":
                button = getattr(self, "_stt_download_btn", None)
                if isinstance(button, QPushButton):
                    button.setEnabled(True)
                self._refresh_stt_active_backend()
        if not self._running_test_tokens and not pending and not progress:
            self._test_result_timer.stop()

    def _test_llm_route(self, *, section_key: str, route_name: str, status_label: QLabel, image: bool = False) -> None:
        """Verify llm route behavior."""
        from core.llm_clients import client as llm

        rows = self._model_section_rows.get(section_key, [])
        if not rows:
            self._set_test_status(status_label, False, "No model configured.")
            return
        # Collect the primary plus every fallback row up front — Qt widgets are
        # not safe to touch from the worker thread, so read them here.
        routes: list[tuple[str, str]] = []
        for row in rows:
            provider = (row["api_key_combo"].currentData() or "").strip().lower()
            model = self._model_value(row)
            if provider and model:
                routes.append((provider, model))
        if not routes:
            self._set_test_status(status_label, False, "No model configured.")
            return
        anthropic_api_key = self._effective_secret_value_from_provider("anthropic")
        compat_keys = {
            p: self._effective_secret_value_from_provider(p)
            for p in (
                {provider for provider, _model in routes if _is_custom_route(provider)}
                | set(_PROVIDER_KEY_NAMES)
            )
        }
        test_key = {
            "LLM": "llm_test",
            "VISION_LLM": "vision_test",
            "MEMORY_LLM": "memory_test",
        }[section_key]

        def _run_all_routes():
            # Each route is probed independently; a failure on one is reported
            # against that exact provider/model and never attributed to another.
            """Run all routes."""
            results: list[tuple[str, bool, str, str, str]] = []
            for idx, (provider, model) in enumerate(routes):
                label = "Primary" if idx == 0 else f"Fallback {idx}"
                ok, message = llm.test_route_connection(
                    provider,
                    model,
                    route_name,
                    image=image,
                    anthropic_api_key=anthropic_api_key,
                    custom_base_url=(
                        self._custom_base_url_for_provider(provider)
                        if _is_custom_route(provider)
                        else ""
                    ),
                    compat_keys=compat_keys,
                )
                results.append((label, ok, provider, model, message))

            lines: list[str] = []
            for label, ok, provider, model, message in results:
                detail = "OK" if ok else _short_test_error(message, route_name)
                lines.append(f"{'✓' if ok else '✗'} {label} — {provider} / {model}: {detail}")

            primary_ok = results[0][1]
            failed = [label for label, ok, *_ in results if not ok]
            if not failed:
                status: object = True
            elif primary_ok:
                # Only fallbacks failed — the model you'll actually use works.
                status = "warn"
                lines.append("")
                lines.append(
                    f"Primary works. {len(failed)} fallback(s) failed "
                    f"({', '.join(failed)}) — fix or remove just those rows."
                )
            else:
                status = False
            return status, "\n".join(lines)

        self._start_async_test(test_key, status_label, _run_all_routes)

    def _test_primary_llm_connection(self) -> None:
        """Verify primary llm connection behavior."""
        self._test_llm_route(
            section_key="LLM",
            route_name="LLM",
            status_label=self._llm_test_status_lbl,
        )

    def _test_vision_connection(self) -> None:
        """Verify vision connection behavior."""
        self._test_llm_route(
            section_key="VISION_LLM",
            route_name="VISION_LLM",
            status_label=self._vision_test_status_lbl,
            image=True,
        )

    def _test_memory_connection(self) -> None:
        """Verify memory connection behavior."""
        self._test_llm_route(
            section_key="MEMORY_LLM",
            route_name="MEMORY_LLM",
            status_label=self._memory_test_status_lbl,
        )

    def _test_tts_connection(self) -> None:
        """Verify tts connection behavior."""
        from core import tts

        provider = _get(self._fields["TTS_PROVIDER"]).strip().lower()
        cartesia_api_key = self._effective_secret_value("CARTESIA_API_KEY")
        cartesia_voice_id = _get(self._fields["CARTESIA_VOICE_ID"]).strip()
        elevenlabs_api_key = self._effective_secret_value("ELEVENLABS_API_KEY")
        elevenlabs_voice_id = _get(self._fields["ELEVENLABS_VOICE_ID"]).strip()
        elevenlabs_model = _get(self._fields["ELEVENLABS_MODEL"]).strip()
        # OpenAI TTS reuses the OpenAI key managed in the Models tab's key table.
        openai_api_key = self._effective_secret_value_from_provider("openai")
        openai_voice = _get(self._fields["OPENAI_TTS_VOICE"]).strip()
        openai_model = _get(self._fields["OPENAI_TTS_MODEL"]).strip()
        custom_base_url = _get(self._fields["TTS_CUSTOM_BASE_URL"]).strip()
        custom_api_key = self._effective_secret_value("TTS_CUSTOM_API_KEY")
        custom_voice = _get(self._fields["TTS_CUSTOM_VOICE"]).strip()
        custom_model = _get(self._fields["TTS_CUSTOM_MODEL"]).strip()
        gpt_sovits_url = _get(self._fields["GPT_SOVITS_URL"]).strip()
        gpt_sovits_ref_audio_path = _get(self._fields["GPT_SOVITS_REF_AUDIO_PATH"]).strip()
        gpt_sovits_prompt_text = _get(self._fields["GPT_SOVITS_PROMPT_TEXT"]).strip()
        gpt_sovits_prompt_lang = _get(self._fields["GPT_SOVITS_PROMPT_LANG"]).strip()
        gpt_sovits_text_lang = _get(self._fields["GPT_SOVITS_TEXT_LANG"]).strip()
        kokoro_voice = _get(self._fields["KOKORO_VOICE"]).strip()
        kokoro_lang_code = _get(self._fields["KOKORO_LANG_CODE"]).strip()
        kokoro_device = _get(self._fields["KOKORO_DEVICE"]).strip()
        self._start_async_test(
            "tts_test",
            self._tts_test_status_lbl,
            lambda: tts.test_connection(
                provider,
                cartesia_api_key=cartesia_api_key,
                cartesia_voice_id=cartesia_voice_id,
                elevenlabs_api_key=elevenlabs_api_key,
                elevenlabs_voice_id=elevenlabs_voice_id,
                elevenlabs_model=elevenlabs_model,
                openai_api_key=openai_api_key,
                openai_voice=openai_voice,
                openai_model=openai_model,
                custom_base_url=custom_base_url,
                custom_api_key=custom_api_key,
                custom_voice=custom_voice,
                custom_model=custom_model,
                gpt_sovits_url=gpt_sovits_url,
                gpt_sovits_ref_audio_path=gpt_sovits_ref_audio_path,
                gpt_sovits_prompt_text=gpt_sovits_prompt_text,
                gpt_sovits_prompt_lang=gpt_sovits_prompt_lang,
                gpt_sovits_text_lang=gpt_sovits_text_lang,
                kokoro_voice=kokoro_voice,
                kokoro_lang_code=kokoro_lang_code,
                kokoro_device=kokoro_device,
            ),
        )

    @staticmethod
    def _reset_stt_model_in_background() -> None:
        """Reset stt model in background."""
        if os.environ.get("OPENWAND_MACOS_PY_UI_HOST") == "1":
            # In the split worker app, the UI process must not import core.stt:
            # that pulls in NumPy/faster-whisper and can stall Qt while Python is
            # starting other threads. The supervisor's settings-applied flow
            # resets STT in the audio worker instead.
            return

        def _worker():
            """Handle worker for settings dialog."""
            try:
                from core import stt as _stt
                _stt.reset_model()
            except Exception as exc:  # noqa: BLE001 - settings save should never hang on STT reset
                _settings_log.warning("STT model reset skipped/failed: %s", exc)

        threading.Thread(target=_worker, daemon=True, name="settings-stt-reset").start()

    def _rebuild_stt_languages(self) -> None:
        """Cantonese (yue) is only supported by large-v3; omit it from the
        language list for smaller models so it can't be selected into a
        combination that produces nothing usable."""
        combo = self._fields.get("STT_LANGUAGE")
        model_combo = self._fields.get("STT_MODEL")
        if combo is None or model_combo is None:
            return
        provider = _get(self._fields.get("STT_PROVIDER")).strip().lower() or "local"
        supports_yue = provider == "cloudflare" or _get(model_combo) == "large-v3"
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for label, value in _STT_LANGUAGE_OPTIONS:
            if value == "yue" and not supports_yue:
                continue
            combo.addItem(label, value)
        idx = combo.findData(current)
        if idx < 0:
            idx = combo.findData("")  # fall back to auto-detect
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)

    def _refresh_stt_active_backend(self) -> None:
        """Update the STT backend label without importing the heavy STT stack."""
        if getattr(self, "_disposing", False):
            return
        lbl = getattr(self, "_stt_active_lbl", None)
        if lbl is None:
            return
        button = getattr(self, "_stt_download_btn", None)

        def _set_stt_action(installed: bool) -> None:
            if isinstance(button, QPushButton):
                self._connect_button_action(button, self._preload_stt_model)
                button.setText(t("Reinstall STT") if installed else t("Install STT"))

        import config as cfg

        env = getattr(self, "_env", {})
        provider = (
            _get(self._fields.get("STT_PROVIDER"))
            or env.get("STT_PROVIDER")
            or getattr(cfg, "STT_PROVIDER", "local")
        ).strip().lower()
        if provider == "none":
            lbl.clear()
            lbl.setVisible(False)
            if isinstance(button, QPushButton):
                button.setVisible(False)
            return
        lbl.setVisible(True)
        if isinstance(button, QPushButton):
            button.setVisible(True)
        if provider == "cloudflare":
            if isinstance(button, QPushButton):
                self._connect_button_action(button, self._test_cloudflare_stt_connection)
                button.setText(t("Test Cloudflare STT"))
            account_id = (
                _get(self._fields.get("STT_CLOUDFLARE_ACCOUNT_ID"))
                or env.get("STT_CLOUDFLARE_ACCOUNT_ID")
                or getattr(cfg, "STT_CLOUDFLARE_ACCOUNT_ID", "")
            ).strip()
            api_token = self._effective_secret_value("CLOUDFLARE_API_TOKEN")
            missing = []
            if not account_id:
                missing.append(t("Account ID"))
            if not api_token:
                missing.append(t("API token"))
            if missing:
                lbl.setText(
                    t("Cloudflare STT needs: {items}.").format(items=", ".join(missing))
                )
                lbl.setStyleSheet("color: #d8932a; font-size: 9pt;")
            else:
                lbl.setText(
                    t("Cloudflare Whisper Large v3 Turbo is configured. Click Test Cloudflare STT to verify access.")
                )
                lbl.setStyleSheet("color: #80c080; font-size: 9pt;")
            return
        model = _get(self._fields.get("STT_MODEL")) or env.get("STT_MODEL", cfg.STT_MODEL)
        device = _get(self._fields.get("STT_DEVICE")) or env.get("STT_DEVICE", cfg.STT_DEVICE)
        compute = _get(self._fields.get("STT_COMPUTE_TYPE")) or env.get(
            "STT_COMPUTE_TYPE",
            cfg.STT_COMPUTE_TYPE,
        )

        info = None
        loaded_stt = sys.modules.get("core.stt")
        if loaded_stt is not None:
            try:
                info = loaded_stt.active_backend()
            except Exception:
                info = None

        configured_summary = f"{model} · {device} / {compute}"
        if info is None:
            installed = False
            install_status: dict[str, object] = {}
            cuda_runtime_error = ""
            try:
                from core import optional_deps

                spec_status = optional_deps.optional_package_runtime_status("stt", device=device)
                installed = bool(spec_status.get("valid"))
                install_status = optional_deps.current_optional_install_status(
                    "STT",
                    "stt",
                    device=device,
                )
                if not install_status:
                    kokoro_requested = _get(self._fields.get("KOKORO_DEVICE")) or "auto"
                    kokoro_device = (
                        "cuda"
                        if optional_deps.kokoro_install_mode_for_device(kokoro_requested) == "gpu"
                        else "cpu"
                    )
                    install_status = optional_deps.current_local_speech_install_status(
                        kokoro_device=kokoro_device,
                        stt_device=device,
                    )
                if sys.platform == "win32" and device.strip().lower() == "cuda":
                    from core.stt_device import windows_cuda_runtime_status

                    cuda_status = windows_cuda_runtime_status()
                    if cuda_status.get("checked") and not cuda_status.get("valid"):
                        names = ", ".join(sorted(str(name) for name in dict(cuda_status.get("errors") or {})))
                        cuda_runtime_error = (
                            "STT packages are present, but the Windows CUDA runtime is incomplete or unloadable"
                            + (f": {names}." if names else ".")
                        )
            except Exception:
                installed = False
                install_status = {}

            if isinstance(button, QPushButton) and self._apply_restart_apply_status(
                lbl,
                button,
                display_name="STT",
                install_status=install_status,
            ):
                return
            failed_install_message = _failed_optional_install_message(install_status)
            if cuda_runtime_error:
                _set_stt_action(False)
                lbl.setText(_translate_status_message(cuda_runtime_error))
                lbl.setStyleSheet("color: #d8932a; font-size: 9pt;")
            elif failed_install_message:
                _set_stt_action(False)
                lbl.setText(_translate_status_message(failed_install_message))
                lbl.setStyleSheet("color: #d8932a; font-size: 9pt;")
            elif bool(install_status.get("ok")):
                _set_stt_action(True)
                message = str(install_status.get("message") or f"STT installed and model ready: {configured_summary}.")
                lbl.setText(_translate_status_message(message))
                lbl.setStyleSheet("color: #80c080; font-size: 9pt;")
            elif installed:
                _set_stt_action(True)
                lbl.setText(
                    t("STT package installed. Configured backend: {summary}; model loads on first use.").format(
                        summary=configured_summary
                    )
                )
                lbl.setStyleSheet("color: #80c080; font-size: 9pt;")
            else:
                _set_stt_action(False)
                lbl.setText(
                    t("STT package is not installed. Click Install STT to install and verify it.")
                )
                lbl.setStyleSheet("color: #d8932a; font-size: 9pt;")
            return

        _set_stt_action(True)
        summary = f"{info['model']} · {info['device']} / {info['compute']}"
        if info["degraded"]:
            lbl.setText(
                t("Active backend: {summary} — GPU not in use. Free the GPU and "
                  "restart to recover quality.").format(summary=summary)
            )
            lbl.setStyleSheet("color: #c0c040; font-size: 9pt;")
        else:
            lbl.setText(t("Active backend: {summary}").format(summary=summary))
            lbl.setStyleSheet("color: #80c080; font-size: 9pt;")

    def _preload_stt_model(self) -> None:
        """Install/repair faster-whisper and verify the selected model."""
        from core import optional_deps

        model = _get(self._fields["STT_MODEL"]).strip() or "base"
        device = _get(self._fields["STT_DEVICE"]).strip() or "auto"
        compute_type = _get(self._fields["STT_COMPUTE_TYPE"]).strip() or "int8"
        language = _get(self._fields["STT_LANGUAGE"]).strip()
        language_label = language or "auto"
        beam_size = _get(self._fields["STT_BEAM_SIZE"]).strip() or "5"

        message = t(
            "OpenWand will install or repair local speech-to-text support in its user-writable optional packages folder.\n\n"
            "Package: {package}\n"
            "Model: {model}\n"
            "Device: {device}\n"
            "Compute type: {compute_type}\n"
            "Speech language: {language}\n"
            "Beam size: {beam_size}\n\n"
            "Before installing, OpenWand will remove any previous STT package files from its optional packages folder so a broken build cannot be reused.\n\n"
            "The installer will then load the selected Whisper model in a separate process. "
            "The first model download needs internet access and may take a while.\n\n"
            "Continue?"
        ).format(
            package=optional_deps.STT_PACKAGE,
            model=model,
            device=device,
            compute_type=compute_type,
            language=language_label,
            beam_size=beam_size,
        )
        if sys.platform == "win32" and device.lower() in {"auto", "cuda"}:
            message += t(
                "\n\nAuto/GPU support also downloads NVIDIA CUDA runtime and cuBLAS packages "
                "(approximately 570 MB) so the released app does not depend on a separate CUDA Toolkit install."
            )
        answer = QMessageBox.question(
            self,
            t("Install STT"),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._install_optional_tts_package(
            test_key="stt_install",
            display_name="STT",
            packages=optional_deps.stt_install_packages(device),
            remove_artifacts=optional_deps.stt_remove_artifacts(),
            button_attr="_stt_download_btn",
            status_attr="_stt_active_lbl",
            success_message=f"STT installed and model ready: {model} on {device} ({compute_type}).",
            thread_name="stt-install",
            external_plan_extra={
                "post_install": "stt_prepare",
                "stt_model": model,
                "stt_device": device,
                "stt_compute_type": compute_type,
                "settings_updates": {
                    "OPENWAND_STT_PREFERENCE": "local",
                    "STT_PROVIDER": "local",
                    "STT_MODEL": model,
                    "STT_DEVICE": device,
                    "STT_COMPUTE_TYPE": compute_type,
                    "STT_LANGUAGE": language,
                    "STT_BEAM_SIZE": beam_size,
                },
            },
            post_install_progress_detail="downloading or loading Whisper model for {elapsed}",
            post_install=lambda progress, write_log: self._prepare_stt_after_install(
                model=model,
                device=device,
                compute_type=compute_type,
                progress=progress,
                write_log=write_log,
            ),
        )

    def _on_stt_model_preloaded(self, _info, err: str) -> None:
        """Apply an STT preload result on the Qt thread."""
        self._stt_preload_carrier = None
        btn = getattr(self, "_stt_download_btn", None)
        if btn is not None:
            btn.setEnabled(True)
            btn.setText(t("Install STT"))
        lbl = getattr(self, "_stt_active_lbl", None)
        if err:
            if lbl is not None:
                lbl.setText(
                    t(
                        "Could not load the speech model. Connect to the internet "
                        "for the first download, then try again."
                    )
                )
                lbl.setStyleSheet("color: #c0392b; font-size: 9pt;")
            return
        # core.stt is now imported, so the readout can show the live backend.
        self._refresh_stt_active_backend()

    @staticmethod
    def _prepare_stt_after_install(
        *,
        model: str,
        device: str,
        compute_type: str,
        progress: Callable[[str], None],
        write_log: Callable[[str], None],
    ) -> tuple[bool, str]:
        """Verify faster-whisper and load the selected model outside Settings."""
        try:
            from core import optional_deps

            progress(f"Installing STT: downloading or loading Whisper model {model}.")
            write_log(f"[stt install] Downloading or loading Whisper model {model} on {device} ({compute_type}).")

            def _stt_model_progress(elapsed_seconds: int) -> None:
                elapsed = _format_duration(elapsed_seconds)
                message = (
                    f"Installing STT: Whisper model {model} is still downloading or loading after {elapsed}. "
                    "The first download can be large."
                )
                progress(message)
                write_log(f"[stt install] {message}")

            status = optional_deps.stt_model_status_subprocess(
                model,
                device,
                compute_type,
                progress=_stt_model_progress,
            )
            for diagnostic in status.get("diagnostics") or []:
                write_log(f"[stt install] STT diagnostic: {diagnostic}")
            if status.get("error") or status.get("valid") is False:
                detail = str(status.get("error") or "STT model verification failed.")
                return False, f"STT package installed, but model download/load failed: {detail}"
            resolved = f"{status.get('model') or model} on {status.get('device') or device} ({status.get('compute') or compute_type})"
            effective_device = str(status.get("device") or device)
            effective_compute = str(status.get("compute") or compute_type)
            if effective_device != device or effective_compute != compute_type:
                return True, (
                    f"STT installed and model ready: {resolved}. Requested {device} ({compute_type}); "
                    f"runtime verification selected {effective_device} ({effective_compute})."
                )
            return True, f"STT installed and model ready: {resolved}."
        except Exception as exc:  # noqa: BLE001
            write_log(f"[stt install] Model verification failed: {type(exc).__name__}: {exc}")
            return False, f"STT package installed, but model download/load failed: {exc}"

    def _stt_fields_changed(self, old_env: dict[str, str]) -> bool:
        """Handle STT fields changed for settings dialog."""
        import config as cfg

        return any(
            _get(self._fields[key]) != old_env.get(key, str(getattr(cfg, key, "")))
            for key in (
                "STT_PROVIDER",
                "STT_MODEL",
                "STT_COMPUTE_TYPE",
                "STT_LANGUAGE",
                "STT_BEAM_SIZE",
                "STT_DEVICE",
                "STT_CLOUDFLARE_ACCOUNT_ID",
                "STT_CLOUDFLARE_FALLBACK_LOCAL",
            )
        )

    @staticmethod
    def _block_uses_live_tools(blk: dict) -> bool:
        """Handle block uses live tools for settings dialog."""
        uses_context_tool = any(
            str(blk[key].currentData()) == "model"  # type: ignore[attr-defined]
            for key in (
                "context_documents_mode",
                "context_browser_mode",
                "context_github_mode",
                "context_memory_mode",
            )
        )
        uses_override_tool = any(
            mode in {"on", "model"}
            for mode in (blk.get("tool_overrides") or {}).values()
        )
        file_access = blk.get("file_access")
        uses_file_tool = str(file_access.currentData() if file_access else "off") != "off"
        return uses_context_tool or uses_override_tool or uses_file_tool

    @staticmethod
    def _block_uses_screenshot(blk: dict, mode: str) -> bool:
        """Handle block uses screenshot for settings dialog."""
        return str(blk["context_screenshot"].currentData()) == mode  # type: ignore[attr-defined]

    def _capability_warnings_for_values(self, vals: dict[str, str]) -> tuple[list[str], dict[str, list[str]]]:
        """Handle capability warnings for values for settings dialog."""
        warnings: list[str] = []
        warnings_by_target: dict[str, list[str]] = {}

        def add_warning(targets: list[str], warning: str) -> None:
            """Add warning."""
            warnings.append(warning)
            for target in targets:
                warnings_by_target.setdefault(target, []).append(warning)

        try:
            from core.llm_clients.client import (
                screenshot_capability_warnings,
                subscription_auth_warnings,
                tool_capability_warnings,
            )

            vb = self._voice_block
            sb = getattr(self, "_snip_block", None)
            context_blocks = [*self._caller_blocks, vb]
            if sb:
                context_blocks.append(sb)
            all_blocks = context_blocks
            screenshot_modes = [
                str(blk["context_screenshot"].currentData())  # type: ignore[attr-defined]
                for blk in all_blocks
            ]
            for screenshot_mode, model_target in (
                ("auto", "VISION_LLM"),
                ("model", "LLM"),
            ):
                if screenshot_mode not in screenshot_modes:
                    continue
                screenshot_targets = {model_target}
                if any(
                    self._block_uses_screenshot(blk, screenshot_mode)
                    for blk in self._caller_blocks
                ):
                    screenshot_targets.add("Global shortcuts")
                if self._block_uses_screenshot(vb, screenshot_mode):
                    screenshot_targets.add("Voice shortcuts")
                if sb and self._block_uses_screenshot(sb, screenshot_mode):
                    screenshot_targets.add("Global shortcuts")
                for warning in screenshot_capability_warnings(
                    [screenshot_mode],
                    llm_provider=vals.get("LLM_PROVIDER", ""),
                    llm_model=vals.get("LLM_MODEL", ""),
                    vision_provider=vals.get("VISION_LLM_PROVIDER", ""),
                    vision_model=vals.get("VISION_LLM_MODEL", ""),
                ):
                    add_warning(sorted(screenshot_targets), warning)

            llm_provider = vals.get("LLM_PROVIDER", "")
            vision_provider = vals.get("VISION_LLM_PROVIDER", "")
            llm_auth_targets: list[str] = []
            if llm_provider.strip().lower() == "chatgpt":
                llm_auth_targets.extend(["Provider credentials", "Authentication", "LLM"])
            if llm_provider.strip().lower() == "copilot":
                llm_auth_targets.extend(["Provider credentials", "API Keys", "LLM"])
            vision_auth_targets: list[str] = []
            if vision_provider.strip().lower() == "chatgpt":
                vision_auth_targets.extend(
                    ["Provider credentials", "Authentication", "VISION_LLM"]
                )
            if vision_provider.strip().lower() == "copilot":
                vision_auth_targets.extend(["Provider credentials", "API Keys", "VISION_LLM"])
            for warning in subscription_auth_warnings(
                llm_provider=llm_provider,
            ):
                add_warning(list(dict.fromkeys(llm_auth_targets)) or ["Authentication"], warning)
            for warning in subscription_auth_warnings(
                vision_provider=vision_provider,
            ):
                add_warning(list(dict.fromkeys(vision_auth_targets)) or ["Authentication"], warning)

            caller_tools = any(self._block_uses_live_tools(blk) for blk in self._caller_blocks)
            voice_tools = self._block_uses_live_tools(vb)
            snip_tools = bool(sb) and self._block_uses_live_tools(sb)
            tool_warnings = tool_capability_warnings(
                caller_tools or voice_tools or snip_tools,
                llm_provider=llm_provider,
            )
            tool_targets = ["LLM"]
            if caller_tools:
                tool_targets.append("Global shortcuts")
            if voice_tools:
                tool_targets.append("Voice shortcuts")
            if snip_tools:
                tool_targets.append("Global shortcuts")
            for warning in tool_warnings:
                add_warning(tool_targets, warning)
        except Exception:
            return [], {}

        return warnings, warnings_by_target

    def _apply_settings(self) -> bool:
        """Save settings and apply changes live. Returns True on success."""
        apply_started = time.monotonic()
        old_env = dict(self._env)
        stt_changed = self._stt_fields_changed(old_env)
        cloudflare_token_changed = bool(
            _get(self._fields.get("CLOUDFLARE_API_TOKEN")).strip()
        )
        saved = False
        self._last_save_warnings = []
        self._saving_settings = True
        try:
            saved = self._do_save()
            if saved:
                import config
                from core import tts as _tts
                from core.llm_clients import client as _llm
                from ui.shared.theme import apply_app_theme
                new_env = _read_env()
                changed_key_set = {
                    key
                    for key in set(old_env) | set(new_env)
                    if old_env.get(key) != new_env.get(key)
                }
                if cloudflare_token_changed:
                    # Keychain-only changes never appear in the .env diff,
                    # but the long-lived audio worker still needs to reload.
                    changed_key_set.add("CLOUDFLARE_API_TOKEN")
                config.reload()
                _llm.reset_clients()
                _tts.reset_connections()
                if stt_changed:
                    self._reset_stt_model_in_background()
                theme_changed = _settings_change_requires_theme_repaint(changed_key_set)
                language_changed = "APP_LANGUAGE" in changed_key_set
                if theme_changed:
                    apply_app_theme()
                    self._apply_dialog_theme()
                if language_changed:
                    localize_widget_tree(self)
                self._refresh_tab_labels()
                self._on_settings_tab_changed(self._tabs.currentIndex())
                if self._on_apply:
                    changed_keys = sorted(changed_key_set)
                    apply_payload = {"changed_keys": changed_keys}
                    try:
                        self._on_apply(apply_payload)
                    except TypeError:
                        self._on_apply()
                    self._env = new_env
                else:
                    self._env = new_env
                self._active_preset_slug = self._env.get(_SETTINGS_PRESET_KEY, "")
                self._refresh_capability_warning_markers()
                self._refresh_search_index()
                self._reset_dirty_baseline()
        except Exception as exc:  # noqa: BLE001 - surface save/apply failures to the user
            _settings_log.exception("Settings apply failed")
            QMessageBox.warning(self, t("Save failed"), str(exc))
            saved = False
        finally:
            self._saving_settings = False
            elapsed = time.monotonic() - apply_started
            if elapsed >= 0.5:
                _settings_log.info("Settings save/apply completed in %.2fs", elapsed)
        if saved:
            self._show_save_warnings()
            return True
        self._refresh_dirty_state()
        return False

    def _show_save_warnings(self) -> None:
        """Show non-fatal save warnings without blocking Apply/Confirm."""
        warnings = list(getattr(self, "_last_save_warnings", []))
        self._last_save_warnings = []
        if not warnings:
            return
        translated_warnings = [t(warning) for warning in warnings]
        box = QMessageBox(self)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t("Heads up"))
        box.setText(t("Your settings were saved, but:") + "\n\n• " + "\n\n• ".join(translated_warnings))
        self._open_warning_boxes.append(box)

        def _forget_box(_result: int, *, message_box=box) -> None:
            if message_box in self._open_warning_boxes:
                self._open_warning_boxes.remove(message_box)

        box.finished.connect(_forget_box)
        box.open()

    def _apply(self):
        """Save settings and keep the dialog open."""
        self._apply_settings()

    def _confirm(self):
        """Save and apply changed settings while keeping the window open."""
        # Saving reads every field, so a page must never still be pending here.
        self._build_deferred_pages()
        self._refresh_dirty_state()
        if not self._dirty_keys:
            # Nothing changed since the dialog opened or the last save, so skip
            # the heavy live reload without dismissing Settings.
            return
        self._apply_settings()

    @staticmethod
    def _reset_env_keys_for_page(page_name: str, env: dict[str, str]) -> set[str]:
        """Reset env keys for page."""
        page = (page_name or "").strip()
        exact: dict[str, set[str]] = {
            "LLM": {
                "LLM_PROVIDER", "LLM_MODEL", "LLM_FALLBACKS",
                "VISION_LLM_PROVIDER", "VISION_LLM_MODEL", "VISION_LLM_FALLBACKS",
                "MEMORY_LLM_PROVIDER", "MEMORY_LLM_MODEL", "MEMORY_LLM_FALLBACKS",
                "TOOL_LLM_MODEL", "CHAT_TOOL_TRACE_UI", "CHAT_REASONING_EFFORT",
                "OPENWAND_PLANNED_CHUNKING", "OPENWAND_PLANNED_CHUNKING_CHUNKS", "OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS",
                "CHAT_AUTO_ELABORATE", "CHAT_ELABORATE_PROMPT",
            },
            "Connections": {
                "CUSTOM_BASE_URL",
                "GITHUB_CLIENT_ID", "GITHUB_OAUTH_SCOPES",
                "COPILOT_CLI_URL", "COPILOT_CLI_PATH",
            },
            "TTS / Voice": {
                "TTS_PROVIDER", "TTS_SPEAK_REPLIES", "CARTESIA_VOICE_ID",
                "ELEVENLABS_VOICE_ID", "ELEVENLABS_MODEL",
                "OPENAI_TTS_VOICE", "OPENAI_TTS_MODEL",
                "TTS_CUSTOM_BASE_URL", "TTS_CUSTOM_VOICE", "TTS_CUSTOM_MODEL", "TTS_CUSTOM_SAMPLE_RATE",
                "GPT_SOVITS_URL", "GPT_SOVITS_REF_AUDIO_PATH", "GPT_SOVITS_PROMPT_TEXT",
                "GPT_SOVITS_PROMPT_LANG", "GPT_SOVITS_TEXT_LANG", "GPT_SOVITS_SAMPLE_RATE",
                "KOKORO_VOICE", "KOKORO_LANG_CODE", "KOKORO_DEVICE", "KOKORO_SPEED", "KOKORO_SAMPLE_RATE",
                "LIVE_VOICE_PROVIDER", "LIVE_VOICE_MODEL", "LIVE_VOICE_VOICE_NAME", "LIVE_VOICE_HALF_DUPLEX",
                "TTS_VOLUME", "TTS_READ_ALOUD_MIN_WORDS", "TTS_READ_ALOUD_MAX_WORDS",
                "STT_PROVIDER", "STT_MODEL", "STT_COMPUTE_TYPE", "STT_LANGUAGE", "STT_BEAM_SIZE", "STT_DEVICE",
                "STT_CLOUDFLARE_ACCOUNT_ID", "STT_CLOUDFLARE_MODEL",
                "STT_CLOUDFLARE_TIMEOUT_SECONDS", "STT_CLOUDFLARE_FALLBACK_LOCAL",
                "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS", "STT_BACKGROUND_CHUNK_STEP_SECONDS",
                "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS", "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS",
            },
            "Prompts": {
                "SYSTEM_PROMPT_UTILITY",
                "OPENWAND_CODEX_SYSTEM_PROMPT",
                "OPENWAND_CLAUDE_SYSTEM_PROMPT",
            },
            "Keybinds": {
                "HOTKEY_ADD_CONTEXT", "HOTKEY_ADD_CONTEXT_2", "HOTKEY_ADD_CONTEXT_ENABLED",
                "HOTKEY_CLEAR_CONTEXT", "HOTKEY_CLEAR_CONTEXT_2", "HOTKEY_CLEAR_CONTEXT_ENABLED",
                "HOTKEY_SNIP", "HOTKEY_SNIP_2", "HOTKEY_SNIP_ENABLED",
                "HOTKEY_VOICE", "HOTKEY_VOICE_2", "HOTKEY_VOICE_ENABLED",
                "HOTKEY_READ_SELECTION_ALOUD", "HOTKEY_READ_SELECTION_ALOUD_2",
                "HOTKEY_READ_SELECTION_ALOUD_ENABLED",
                "HOTKEY_DICTATE", "HOTKEY_DICTATE_2", "HOTKEY_DICTATE_ENABLED", "DICTATE_MODE",
                "HOTKEY_VOICE_LIVE", "HOTKEY_VOICE_LIVE_2", "HOTKEY_VOICE_LIVE_ENABLED",
                "INTENT_CONTEXT_TOGGLE_KEYS",
                "INTENT_OVERLAY_TIMEOUT_MS",
                "SNIP_CONTEXT_AMBIENT", "SNIP_CONTEXT_CLIPBOARD", "SNIP_CONTEXT_DOCUMENTS",
                "SNIP_CONTEXT_DOCUMENTS_MODE", "SNIP_CONTEXT_BROWSER_MODE", "SNIP_CONTEXT_GITHUB_MODE",
                "SNIP_CONTEXT_MEMORY_MODE", "SNIP_CONTEXT_SCREENSHOT", "SNIP_CONTEXT_TOOLS",
                "SNIP_FILE_ACCESS", "SNIP_TOOLS",
                "CALLER_COUNT",
            },
            "App": {
                "CHAT_EXECUTION_MODE",
                "CHAT_CONVERSATION_OWNER",
                "THEME_MODE", "DARK_MODE", "PRIVACY_MODE", "TRUST_PRIVACY_MODE",
                "PRIVACY_REVIEW_BEFORE_SEND", "PRIVACY_AI_ENABLED",
                "PRIVACY_HIDE_SECRETS", "PRIVACY_HIDE_CONTACT_DETAILS",
                "PRIVACY_HIDE_FINANCIAL_DETAILS", "PRIVACY_HIDE_GOVERNMENT_IDS",
                "PRIVACY_HIDE_URLS", "PROMPT_INJECTION_PROTECTION", "PROMPT_INJECTION_WARN",
                "ICON_AUTO_HIDE", "DOLL_AUTO_HIDE", "START_ON_LOGIN",
                "CHAT_OPEN_ON_PROMPT", "CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE",
                "CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY",
                "THEME_DARK_BG", "THEME_DARK_SURFACE", "THEME_DARK_TEXT", "THEME_DARK_ACCENT",
                "THEME_LIGHT_BG", "THEME_LIGHT_SURFACE", "THEME_LIGHT_TEXT", "THEME_LIGHT_ACCENT",
                "APP_LANGUAGE", "ASSISTANT_LANGUAGE",
                "ICON_SIZE", "DOLL_SIZE", "ICON_BACKSTOP_MS", "DOLL_ICON_BACKSTOP_MS",
                "BUBBLE_WIDTH", "BUBBLE_LINES", "BUBBLE_FONT_SIZE",
                "BUBBLE_COLOR", "BUBBLE_TEXT_COLOR",
                "BUBBLE_READ_WORD_COLOR", "BUBBLE_SCROLL_ENABLED", "BUBBLE_SCROLL_SNAP_ENABLED",
            },
            "Advanced": {
                "CONTEXT_BROWSER_MAX_CHARS", "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS",
                "CONTEXT_TOOL_DOCUMENT_MAX_CHARS", "TOOL_PLUGIN_DIR", "TOOL_GIT_ROOT",
                "TOOL_FILE_ROOTS", "TOOL_FILE_MODE", "TOOL_FILE_BLOCKED_GLOBS",
                "MEMORY_AUTO_CONSOLIDATE", "MEMORY_TOP_K", "MEMORY_CONSOLIDATION_INTERVAL",
                "MEMORY_STM_TOKEN_BUDGET", "BUBBLE_HIDE_DELAY_MS", "BUBBLE_REVEAL_WPM",
                "BUBBLE_HOLD_REVEAL_WPM", "BUBBLE_SCROLL_SNAP_DELAY_MS", "TTS_PLAYBACK_RATE",
                "TTS_HOLD_PLAYBACK_RATE",
            },
        }
        keys = set(exact.get(page, set()))
        if page == "Connections":
            keys.update(key for key in env if key.startswith("OPENWAND_CONNECTION_ALIAS_"))
            keys.update(_custom_connection_env_keys(env))
        if page == "Keybinds":
            keys.update(
                key for key in env
                if key.startswith(("CALLER_", "VOICE_", "SNIP_", "HOTKEY_"))
            )
        return keys

    def _reload_after_page_reset(self, page_name: str = "") -> None:
        """Handle reload after page reset for settings dialog."""
        try:
            import config
            from core import tts as _tts
            from core.llm_clients import client as _llm
            from ui.shared.theme import apply_app_theme

            config.reload()
            _llm.reset_clients()
            _tts.reset_connections()
            apply_app_theme()
            self._apply_dialog_theme()
        except Exception as exc:  # noqa: BLE001 - reset already happened on disk
            _settings_log.error("Live reload after page reset failed: %s", exc)
        self._env = _read_env()
        self._active_preset_slug = self._env.get(_SETTINGS_PRESET_KEY, "")
        self._load_values()
        localize_widget_tree(self)
        self._refresh_tab_labels()
        self._refresh_search_index()
        if page_name == "TTS / Voice":
            self._reset_stt_model_in_background()
        if self._on_apply:
            self._on_apply()

    def _reset_current_page(self) -> None:
        """Reset current page."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return
        self._build_deferred_pages()
        page = self._current_tab_name()
        display_page = next(
            (title for internal, title in _SETTINGS_PAGE_META if internal == page),
            page,
        )
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle(t("Reset page?"))
        confirm.setText(t("Reset the {page} page to defaults?").format(page=t(display_page)))
        confirm.setInformativeText(
            t(
                "Only settings on this page will be reset. API keys, OAuth sign-ins, "
                "stored memory, conversations, addons, and settings on other pages "
                "will be left alone."
            )
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.No)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return

        try:
            current_env = _read_env()
            active_preset = self._preset_slug(getattr(self, "_active_preset_slug", ""))
            if active_preset:
                page_keys = self._preset_page_keys(active_preset, page, current_env)
                remove_keys = set(page_keys)
                remove_keys.update(
                    self._preset_override_keys_for_keys(active_preset, page_keys, current_env)
                )
                preset_values = {
                    key: value
                    for key, value in _PRESET_DEFAULTS.get(active_preset, {}).items()
                    if key in page_keys
                }
                if page == "Keybinds":
                    ctx = _PRESET_CONTEXT_DEFAULTS.get(active_preset, {})
                    caller_count = int(current_env.get("CALLER_COUNT", "0") or "0")
                    for idx in range(1, caller_count + 1):
                        if "documents" in ctx:
                            preset_values[f"CALLER_{idx}_CONTEXT_DOCUMENTS_MODE"] = ctx["documents"]
                        if "browser" in ctx:
                            preset_values[f"CALLER_{idx}_CONTEXT_BROWSER_MODE"] = ctx["browser"]
                        if "github" in ctx:
                            preset_values[f"CALLER_{idx}_CONTEXT_GITHUB_MODE"] = ctx["github"]
                        if "memory" in ctx:
                            preset_values[f"CALLER_{idx}_CONTEXT_MEMORY_MODE"] = ctx["memory"]
                        if "screenshot" in ctx:
                            preset_values[f"CALLER_{idx}_CONTEXT_SCREENSHOT"] = ctx["screenshot"]
                        if ctx.get("clear_tools", "").lower() == "true":
                            preset_values[f"CALLER_{idx}_TOOLS"] = ""
                    if "documents" in ctx:
                        preset_values["VOICE_CONTEXT_DOCUMENTS_MODE"] = ctx["documents"]
                    if "browser" in ctx:
                        preset_values["VOICE_CONTEXT_BROWSER_MODE"] = ctx["browser"]
                    if "github" in ctx:
                        preset_values["VOICE_CONTEXT_GITHUB_MODE"] = ctx["github"]
                    if "memory" in ctx:
                        preset_values["VOICE_CONTEXT_MEMORY_MODE"] = ctx["memory"]
                    if "screenshot" in ctx:
                        preset_values["VOICE_CONTEXT_SCREENSHOT"] = ctx["screenshot"]
                    if ctx.get("clear_tools", "").lower() == "true":
                        preset_values["VOICE_TOOLS"] = ""
                preset_values[_SETTINGS_PRESET_KEY] = active_preset
            else:
                remove_keys = self._reset_env_keys_for_page(page, current_env)
                preset_values = {}
            for key in remove_keys:
                os.environ.pop(key, None)
            _write_env(preset_values, remove_keys=remove_keys - set(preset_values))
            self._reload_after_page_reset(page)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, t("Reset page failed"), str(exc))
            return

        QMessageBox.information(
            self,
            t("Page reset"),
            t("{page} settings were reset to defaults.").format(page=page),
        )

    def _reset_all(self) -> None:
        """Factory reset: erase every setting and delete all API keys.

        Pops up a detailed warning the user must confirm, then deletes all known
        secrets from the OS keychain, wipes the .env file (and the matching values
        from this process), reloads config + live app, and refreshes the dialog.
        """
        self._build_deferred_pages()
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle(t("Reset all settings?"))
        confirm.setText(t("Reset OpenWand to its defaults? This cannot be undone."))
        confirm.setInformativeText(
            t(
                "This will permanently:\n"
                "• DELETE every API key from your OS keychain "
                "(Groq, OpenAI, Anthropic, Google, DeepSeek, OpenRouter, Mistral, "
                "xAI, Together, Cerebras, Z.AI, NVIDIA, SambaNova, GitHub Models, "
                "Hugging Face, Chutes, Vercel, Fireworks, Cohere, AI21, Nebius, "
                "Cartesia, ElevenLabs, Copilot, custom)\n"
                "• ERASE all saved settings (models, hotkeys, prompts, theme, "
                "callers, and everything else in your .env)\n"
                "• SIGN YOU OUT of all OAuth logins (ChatGPT, GitHub)\n\n"
                "You will need to re-enter your API keys, sign in again, and "
                "reconfigure the app afterwards.\n\n"
                "Continue?"
            )
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.No)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return

        _settings_log.warning("User requested full settings reset")

        # 1. Delete every known secret from the OS keychain, verifying removal.
        key_failures: list[str] = []
        dynamic_custom_secret_names = {
            _custom_secret_name(connection["id"])
            for connection in _load_custom_connections(self._env)
        } - set(secret_store.API_KEY_NAMES)
        for name in (*secret_store.API_KEY_NAMES, *sorted(dynamic_custom_secret_names)):
            try:
                secret_store.delete_secret(name)
                if secret_store.get_keychain_secret(name):
                    key_failures.append(name)
                    _settings_log.error("Key %s still present in keychain after delete", name)
                else:
                    _settings_log.info("Deleted %s from OS keychain", name)
            except Exception as exc:  # noqa: BLE001 — collected and surfaced
                key_failures.append(f"{name}: {exc}")
                _settings_log.error("Failed deleting %s from keychain: %s", name, exc)

        # 1b. Sign out of every OAuth provider.
        for label, module_path, func_name in (
            ("ChatGPT",        "core.auth.chatgpt",      "clear_tokens"),
            ("GitHub",         "core.auth.github",       "clear_tokens"),
            ("GitHub Copilot", "core.auth.copilot_auth", "clear_token"),
        ):
            try:
                import importlib
                getattr(importlib.import_module(module_path), func_name)()
                _settings_log.info("Signed out of %s", label)
            except Exception as exc:  # noqa: BLE001 — collected and surfaced
                key_failures.append(f"{label} sign-out: {exc}")
                _settings_log.error("Failed signing out of %s: %s", label, exc)

        # 2. Wipe the .env file and clear matching values from this process so the
        #    reset takes effect live (load_dotenv won't unset removed keys itself).
        try:
            for key in _read_env():
                os.environ.pop(key, None)
            if ENV_PATH.exists():
                ENV_PATH.unlink()
            _settings_log.info("Erased settings file %s", ENV_PATH)
        except OSError as exc:
            _settings_log.error("Could not erase %s: %s", ENV_PATH, exc)
            QMessageBox.warning(
                self, t("Reset error"),
                t("Could not erase the settings file:\n{error}").format(error=exc),
            )

        # 3. Reload config + live app and refresh the dialog to show defaults.
        try:
            import config
            from core import tts as _tts
            from core.llm_clients import client as _llm
            from ui.shared.theme import apply_app_theme
            config.reload()
            _llm.reset_clients()
            _tts.reset_connections()
            self._reset_stt_model_in_background()
            apply_app_theme()
            self._apply_dialog_theme()
        except Exception as exc:  # noqa: BLE001 — reset already happened on disk
            _settings_log.error("Live reload after reset failed: %s", exc)
        self._env = _read_env()
        self._active_preset_slug = ""
        self._load_values()
        localize_widget_tree(self)
        self._refresh_tab_labels()
        self._refresh_search_index()
        for refresh in (
            getattr(self, "_refresh_chatgpt_status", None),
            getattr(self, "_refresh_github_status", None),
        ):
            if callable(refresh):
                try:
                    refresh()
                except Exception:  # noqa: BLE001 — status labels are cosmetic
                    pass
        if self._on_apply:
            self._on_apply()

        if key_failures:
            QMessageBox.warning(
                self, t("Reset partly complete"),
                t("Settings were reset, but these items could not be fully cleared:")
                + "\n\n"
                + "\n".join(f"• {item}" for item in key_failures)
                + "\n\n"
                + t("You may need to remove them manually (e.g. from your system "
                    "credential store). See the log for details."),
            )
        else:
            QMessageBox.information(
                self, t("Reset complete"),
                t("All API keys were removed from the OS keychain, you were signed "
                  "out of all OAuth logins, and every setting was reset to defaults."),
            )

    def _save_action_file_callers(self) -> None:
        """Persist the caller editor into the live comment-preserving TOML tree."""
        from core.action_files.edit import save_callers
        from core.action_files.store import invalidate_live_catalog
        from core.system.paths import SHIPPED_CALLERS_DIR

        def source_mode(value: str) -> str:
            mode = str(value or "off").strip().casefold()
            if mode == "auto":
                return "on"
            return mode if mode in {"off", "on", "model"} else "off"

        callers: list[dict[str, Any]] = []
        for blk in self._caller_blocks:
            documents_mode = str(blk["context_documents_mode"].currentData() or "off")
            ambient = (
                "model" if documents_mode == "model"
                else "on" if documents_mode == "auto" or blk["context_ambient"].isChecked()
                else "off"
            )
            file_access = str(blk["file_access"].currentData() or "off")
            actions: list[dict[str, Any]] = []
            for row in blk["intent_rows"]:
                metadata = dict(row.get("action_file") or {})
                label = _get(row["label"])
                prompt = _get(row["prompt"])
                actions.append(
                    {
                        "name": str(metadata.get("name") or ""),
                        "key": _get(row["key"]),
                        "label": label,
                        "prompt": prompt,
                        "enabled": row["enabled"].isChecked(),
                        "hint": str(metadata.get("hint") or ""),
                        "access": list(metadata.get("access") or ["text"]),
                        "template": str(metadata.get("template") or ""),
                        "preserve_template": bool(metadata.get("template"))
                        and label == str(row.get("original_label") or "")
                        and prompt == str(row.get("original_prompt") or ""),
                    }
                )
            current_action_names = {
                str(item.get("name") or "") for item in actions if str(item.get("name") or "")
            }
            callers.append(
                {
                    "folder": str(blk.get("folder") or ""),
                    "hotkey": _get(blk["hotkey"]),
                    "hotkey_2": _get(blk["hotkey_2"]),
                    "enabled": blk["enabled"].isChecked(),
                    "label": self._caller_label_value(blk),
                    "paste_back": blk["paste_back"].isChecked(),
                    "custom_key": _get(blk["custom_key"]),
                    "custom_label": _get(blk["custom_label"]),
                    "space_starts_new_chat": bool(blk.get("space_starts_new_chat", True)),
                    "file_access": file_access,
                    "tools": dict(blk.get("tool_overrides") or {}),
                    "context": {
                        "ambient": ambient,
                        "browser": source_mode(str(blk["context_browser_mode"].currentData() or "off")),
                        "selection": (
                            "on" if bool(blk.get("context_selection_enabled", True)) else "off"
                        ),
                        "clipboard": "on" if blk["context_clipboard"].isChecked() else "off",
                        "screenshot": source_mode(str(blk["context_screenshot"].currentData() or "off")),
                        "github": source_mode(str(blk["context_github_mode"].currentData() or "off")),
                        "memory": source_mode(str(blk["context_memory_mode"].currentData() or "off")),
                        "files": "off" if file_access == "off" else "model" if file_access == "auto" else "on",
                    },
                    "actions": actions,
                    "removed_actions": sorted(
                        set(blk.get("original_action_names") or ()) - current_action_names
                    ),
                }
            )
        callers_root = Path(ENV_PATH).parent / "callers"
        if not callers_root.exists():
            shutil.copytree(SHIPPED_CALLERS_DIR, callers_root)
        save_callers(callers_root, callers)
        invalidate_live_catalog()

    def _do_save(self) -> bool:
        """Write .env. Returns True on success, False if validation failed.

        Keychain key storage is attempted first and reports its own per-key
        warnings, but a keychain failure does NOT abort the save: the rest of the
        settings are still written to .env so the user never loses everything just
        because one API key could not reach the OS keychain.
        """
        import config as cfg

        if not self._validate_caller_mutations():
            return False
        self._save_api_keys_to_keychain()

        def _section_vals(sk):
            """Handle section vals for settings dialog."""
            rows = self._model_section_rows.get(sk, [])
            if not rows:
                return "", "", ""
            primary = rows[0]
            provider = primary["api_key_combo"].currentData() or ""
            model = self._model_value(primary)
            fallbacks = "\n".join(
                f"{r['api_key_combo'].currentData() or ''}:{self._model_value(r)}"
                for r in rows[1:]
                if (r["api_key_combo"].currentData() or "") and self._model_value(r)
            )
            return provider, model, fallbacks

        llm_p, llm_m, llm_f = _section_vals("LLM")
        vis_p, vis_m, vis_f = _section_vals("VISION_LLM")
        mem_p, mem_m, mem_f = _section_vals("MEMORY_LLM")

        # Persist both theme templates (the four swatches edit only the selected
        # mode, so fold their current values back into that template first).
        self._flush_visible_theme_fields()
        theme_vals = {
            f"THEME_{mode.upper()}_{role.upper()}": self._theme_templates.get(mode, {}).get(role, "")
            for mode in ("light", "dark")
            for role in self._THEME_ROLES
        }
        assistant_language = _get(self._fields["ASSISTANT_LANGUAGE"])
        chat_elaborate_prompt = cfg.localize_chat_elaborate_prompt_if_default(
            _get(self._fields["CHAT_ELABORATE_PROMPT"]),
            assistant_language,
        )
        _set(self._fields["CHAT_ELABORATE_PROMPT"], chat_elaborate_prompt)
        system_prompt_utility = cfg.localize_system_prompt_utility_if_default(
            self._fields["SYSTEM_PROMPT_UTILITY"].toPlainText(),  # type: ignore[attr-defined]
            assistant_language,
        )
        _set(self._fields["SYSTEM_PROMPT_UTILITY"], system_prompt_utility)
        chatgpt_system_prompt = self._fields["OPENWAND_CODEX_SYSTEM_PROMPT"].toPlainText()  # type: ignore[attr-defined]
        claude_system_prompt = self._fields["OPENWAND_CLAUDE_SYSTEM_PROMPT"].toPlainText()  # type: ignore[attr-defined]
        privacy_mode = str(self._fields["PRIVACY_MODE"].currentData() or "builtin")  # type: ignore[attr-defined]

        vals = {
            "CHAT_EXECUTION_MODE": _get(self._fields["CHAT_EXECUTION_MODE"]),
            "CHAT_CONVERSATION_OWNER": _get(self._fields["CHAT_CONVERSATION_OWNER"]),
            "LLM_PROVIDER":      llm_p,
            "LLM_MODEL":         llm_m,
            "LLM_FALLBACKS":     llm_f,
            "VISION_LLM_PROVIDER": vis_p,
            "VISION_LLM_MODEL":    vis_m,
            "VISION_LLM_FALLBACKS": vis_f,
            "MEMORY_LLM_PROVIDER": mem_p,
            "MEMORY_LLM_MODEL":    mem_m,
            "MEMORY_LLM_FALLBACKS": mem_f,
            "CHAT_TOOL_TRACE_UI": str(self._fields["CHAT_TOOL_TRACE_UI"].isChecked()),  # type: ignore[attr-defined]
            "OPENWAND_PLANNED_CHUNKING": str(self._fields["OPENWAND_PLANNED_CHUNKING"].isChecked()),  # type: ignore[attr-defined]
            "OPENWAND_PLANNED_CHUNKING_CHUNKS": _get(self._fields["OPENWAND_PLANNED_CHUNKING_CHUNKS"]),
            "OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS": _get(self._fields["OPENWAND_PLANNED_CHUNKING_MIN_PROMPT_CHARS"]),
            "CHAT_REASONING_EFFORT": _get(self._fields["CHAT_REASONING_EFFORT"]),
            "TTS_PROVIDER":      _get(self._fields["TTS_PROVIDER"]),
            "TTS_SPEAK_REPLIES": _get(self._fields["TTS_SPEAK_REPLIES"]),
            "CARTESIA_VOICE_ID": _get(self._fields["CARTESIA_VOICE_ID"]),
            "ELEVENLABS_VOICE_ID": _get(self._fields["ELEVENLABS_VOICE_ID"]),
            "ELEVENLABS_MODEL":  _get(self._fields["ELEVENLABS_MODEL"]),
            "OPENAI_TTS_VOICE":  _get(self._fields["OPENAI_TTS_VOICE"]),
            "OPENAI_TTS_MODEL":  _get(self._fields["OPENAI_TTS_MODEL"]),
            "TTS_CUSTOM_BASE_URL": _get(self._fields["TTS_CUSTOM_BASE_URL"]),
            "TTS_CUSTOM_VOICE":  _get(self._fields["TTS_CUSTOM_VOICE"]),
            "TTS_CUSTOM_MODEL":  _get(self._fields["TTS_CUSTOM_MODEL"]),
            "TTS_CUSTOM_SAMPLE_RATE": _get(self._fields["TTS_CUSTOM_SAMPLE_RATE"]),
            "GPT_SOVITS_URL": _get(self._fields["GPT_SOVITS_URL"]),
            "GPT_SOVITS_REF_AUDIO_PATH": _get(self._fields["GPT_SOVITS_REF_AUDIO_PATH"]),
            "GPT_SOVITS_PROMPT_TEXT": _get(self._fields["GPT_SOVITS_PROMPT_TEXT"]),
            "GPT_SOVITS_PROMPT_LANG": _get(self._fields["GPT_SOVITS_PROMPT_LANG"]),
            "GPT_SOVITS_TEXT_LANG": _get(self._fields["GPT_SOVITS_TEXT_LANG"]),
            "GPT_SOVITS_SAMPLE_RATE": _get(self._fields["GPT_SOVITS_SAMPLE_RATE"]),
            "KOKORO_VOICE": _get(self._fields["KOKORO_VOICE"]),
            "KOKORO_LANG_CODE": _get(self._fields["KOKORO_LANG_CODE"]),
            "KOKORO_DEVICE": _get(self._fields["KOKORO_DEVICE"]),
            "KOKORO_SPEED": _get(self._fields["KOKORO_SPEED"]),
            "KOKORO_SAMPLE_RATE": _get(self._fields["KOKORO_SAMPLE_RATE"]),
            "TTS_VOLUME": _get(self._fields["TTS_VOLUME"]),
            "TTS_READ_ALOUD_MIN_WORDS": _get(self._fields["TTS_READ_ALOUD_MIN_WORDS"]),
            "TTS_READ_ALOUD_MAX_WORDS": _get(self._fields["TTS_READ_ALOUD_MAX_WORDS"]),
            "LIVE_VOICE_PROVIDER": _get(self._fields["LIVE_VOICE_PROVIDER"]),
            "LIVE_VOICE_MODEL": self._live_voice_model_value(),
            "LIVE_VOICE_VOICE_NAME": self._live_voice_voice_value(),
            "LIVE_VOICE_HALF_DUPLEX": _get(self._fields["LIVE_VOICE_HALF_DUPLEX"]),
            "STT_PROVIDER":      _get(self._fields["STT_PROVIDER"]),
            "STT_MODEL":         _get(self._fields["STT_MODEL"]),
            "STT_COMPUTE_TYPE":  _get(self._fields["STT_COMPUTE_TYPE"]),
            "STT_LANGUAGE":      _get(self._fields["STT_LANGUAGE"]),
            "STT_BEAM_SIZE":     _get(self._fields["STT_BEAM_SIZE"]),
            "STT_DEVICE":        _get(self._fields["STT_DEVICE"]),
            "STT_CLOUDFLARE_ACCOUNT_ID": _get(self._fields["STT_CLOUDFLARE_ACCOUNT_ID"]),
            "STT_CLOUDFLARE_FALLBACK_LOCAL": _get(
                self._fields["STT_CLOUDFLARE_FALLBACK_LOCAL"]
            ),
            "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS": _get(
                self._fields["STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS"]
            ),
            "STT_BACKGROUND_CHUNK_STEP_SECONDS": _get(
                self._fields["STT_BACKGROUND_CHUNK_STEP_SECONDS"]
            ),
            "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS": _get(
                self._fields["STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS"]
            ),
            "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS": _get(
                self._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"]
            ),
            "HOTKEY_ADD_CONTEXT":  _get(self._fields["HOTKEY_ADD_CONTEXT"]),
            "HOTKEY_ADD_CONTEXT_2": _get(self._fields["HOTKEY_ADD_CONTEXT_2"]),
            "HOTKEY_ADD_CONTEXT_ENABLED": _get(self._fields["HOTKEY_ADD_CONTEXT_ENABLED"]),
            "HOTKEY_CLEAR_CONTEXT": _get(self._fields["HOTKEY_CLEAR_CONTEXT"]),
            "HOTKEY_CLEAR_CONTEXT_2": _get(self._fields["HOTKEY_CLEAR_CONTEXT_2"]),
            "HOTKEY_CLEAR_CONTEXT_ENABLED": _get(self._fields["HOTKEY_CLEAR_CONTEXT_ENABLED"]),
            "HOTKEY_SNIP":         _get(self._fields["HOTKEY_SNIP"]),
            "HOTKEY_SNIP_2":       _get(self._fields["HOTKEY_SNIP_2"]),
            "HOTKEY_SNIP_ENABLED": _get(self._fields["HOTKEY_SNIP_ENABLED"]),
            "HOTKEY_READ_SELECTION_ALOUD": _get(self._fields["HOTKEY_READ_SELECTION_ALOUD"]),
            "HOTKEY_READ_SELECTION_ALOUD_2": _get(self._fields["HOTKEY_READ_SELECTION_ALOUD_2"]),
            "HOTKEY_READ_SELECTION_ALOUD_ENABLED": _get(self._fields["HOTKEY_READ_SELECTION_ALOUD_ENABLED"]),
            "HOTKEY_VOICE_LIVE":   _get(self._fields["HOTKEY_VOICE_LIVE"]),
            "HOTKEY_VOICE_LIVE_2": _get(self._fields["HOTKEY_VOICE_LIVE_2"]),
            "HOTKEY_VOICE_LIVE_ENABLED": _get(self._fields["HOTKEY_VOICE_LIVE_ENABLED"]),
            "INTENT_CONTEXT_TOGGLE_KEYS": _get(self._fields["INTENT_CONTEXT_TOGGLE_KEYS"]),
            "INTENT_OVERLAY_TIMEOUT_MS": _get(self._fields["INTENT_OVERLAY_TIMEOUT_MS"]),
            "CONTEXT_BROWSER_MAX_CHARS": _get(self._fields["CONTEXT_BROWSER_MAX_CHARS"]),
            "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"]),
            "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"]),
            "TOOL_FILE_ROOTS": _get(self._fields["TOOL_FILE_ROOTS"]),
            "TOOL_FILE_BLOCKED_GLOBS": _get(self._fields["TOOL_FILE_BLOCKED_GLOBS"]),
            "CUSTOM_BASE_URL":            _get(self._fields["CUSTOM_BASE_URL"]),
            "GITHUB_CLIENT_ID":          _get(self._fields["GITHUB_CLIENT_ID"]),
            "GITHUB_OAUTH_SCOPES":       _get(self._fields["GITHUB_OAUTH_SCOPES"]),
            "MEMORY_AUTO_CONSOLIDATE":   str(self._fields["MEMORY_AUTO_CONSOLIDATE"].isChecked()),  # type: ignore
            "MEMORY_CONSOLIDATION_INTERVAL": _get(self._fields["MEMORY_CONSOLIDATION_INTERVAL"]),
            "MEMORY_TOP_K":             _get(self._fields["MEMORY_TOP_K"]),
            "MEMORY_STM_TOKEN_BUDGET":  _get(self._fields["MEMORY_STM_TOKEN_BUDGET"]),
            "THEME_MODE":       self._fields["THEME_MODE"].currentData(),  # type: ignore[attr-defined]
            "PRIVACY_MODE": privacy_mode,
            "TRUST_PRIVACY_MODE": str(privacy_mode != "off"),
            "PRIVACY_REVIEW_BEFORE_SEND": str(self._fields["PRIVACY_REVIEW_BEFORE_SEND"].isChecked()),
            "PRIVACY_AI_ENABLED": str(privacy_mode == "advanced"),
            "PRIVACY_HIDE_SECRETS": str(self._fields["PRIVACY_HIDE_SECRETS"].isChecked()),
            "PRIVACY_HIDE_CONTACT_DETAILS": str(self._fields["PRIVACY_HIDE_CONTACT_DETAILS"].isChecked()),
            "PRIVACY_HIDE_FINANCIAL_DETAILS": str(self._fields["PRIVACY_HIDE_FINANCIAL_DETAILS"].isChecked()),
            "PRIVACY_HIDE_GOVERNMENT_IDS": str(self._fields["PRIVACY_HIDE_GOVERNMENT_IDS"].isChecked()),
            "PRIVACY_HIDE_URLS": str(self._fields["PRIVACY_HIDE_URLS"].isChecked()),
            "PROMPT_INJECTION_PROTECTION": str(
                self._fields["PROMPT_INJECTION_PROTECTION"].isChecked()
            ),
            "PROMPT_INJECTION_WARN": str(self._fields["PROMPT_INJECTION_WARN"].isChecked()),
            "ICON_AUTO_HIDE":    str(self._fields["ICON_AUTO_HIDE"].isChecked()),  # type: ignore
            "START_ON_LOGIN": str(self._fields["START_ON_LOGIN"].isChecked()),  # type: ignore
            "CHAT_OPEN_ON_PROMPT": str(self._fields["CHAT_OPEN_ON_PROMPT"].isChecked()),
            "CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE": str(
                self._fields["CHAT_OPEN_ON_PROMPT_HIDE_BUBBLE"].isChecked()
            ),
            "CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY": str(
                self._fields["CONTEXT_DEFAULTS_FIRST_PROMPT_ONLY"].isChecked()
            ),
            "CHAT_AUTO_ELABORATE": str(self._fields["CHAT_AUTO_ELABORATE"].isChecked()),  # type: ignore
            "CHAT_ELABORATE_PROMPT": chat_elaborate_prompt,
            "APP_LANGUAGE": _get(self._fields["APP_LANGUAGE"]),
            "ASSISTANT_LANGUAGE": assistant_language,
            "ICON_SIZE":    _get(self._fields["ICON_SIZE"]),
            "BUBBLE_WIDTH": _get(self._fields["BUBBLE_WIDTH"]),
            "BUBBLE_LINES": _get(self._fields["BUBBLE_LINES"]),
            "BUBBLE_FONT_SIZE": _get(self._fields["BUBBLE_FONT_SIZE"]),
            "BUBBLE_SCROLL_ENABLED": str(self._fields["BUBBLE_SCROLL_ENABLED"].isChecked()),  # type: ignore
            "BUBBLE_SCROLL_SNAP_ENABLED": str(self._fields["BUBBLE_SCROLL_SNAP_ENABLED"].isChecked()),  # type: ignore
            "BUBBLE_COLOR": _get(self._fields["BUBBLE_COLOR"]),
            "BUBBLE_TEXT_COLOR": _get(self._fields["BUBBLE_TEXT_COLOR"]),
            "BUBBLE_READ_WORD_COLOR": _get(self._fields["BUBBLE_READ_WORD_COLOR"]),
            "BUBBLE_REVEAL_WPM": _get(self._fields["BUBBLE_REVEAL_WPM"]),
            "BUBBLE_HOLD_REVEAL_WPM": _get(self._fields["BUBBLE_HOLD_REVEAL_WPM"]),
            "BUBBLE_HIDE_DELAY_MS": _seconds_str_to_ms(
                _get(self._fields["BUBBLE_HIDE_DELAY_S"]), 3500
            ),
            "BUBBLE_SCROLL_SNAP_DELAY_MS": _seconds_str_to_ms(
                _get(self._fields["BUBBLE_SCROLL_SNAP_DELAY_S"]), 2500
            ),
            "TTS_PLAYBACK_RATE": _get(self._fields["TTS_PLAYBACK_RATE"]),
            "TTS_HOLD_PLAYBACK_RATE": _get(self._fields["TTS_HOLD_PLAYBACK_RATE"]),
            "SYSTEM_PROMPT_UTILITY": system_prompt_utility,
            "OPENWAND_CODEX_SYSTEM_PROMPT": chatgpt_system_prompt,
            "OPENWAND_CLAUDE_SYSTEM_PROMPT": claude_system_prompt,
        }
        vals.update(self._snip_context_values())
        vals.update(theme_vals)
        custom_connections = [
            {
                "id": str(row.get("connection_id") or "legacy"),
                "alias": row["alias"].text().strip(),
                "base_url": row["custom_address"].text().strip(),
            }
            for row in self._api_key_rows
            if _get(row["provider"]).strip() == "custom"
        ]
        vals.update(_custom_connection_env_values(custom_connections))
        # Keep the old singleton URL synchronized with the first row so older
        # builds can still open a configuration saved by this version.
        vals["CUSTOM_BASE_URL"] = (
            custom_connections[0]["base_url"]
            if custom_connections
            else _get(self._fields["CUSTOM_BASE_URL"]).strip()
        )
        connection_alias_keys = {
            key for key in self._env if key.startswith("OPENWAND_CONNECTION_ALIAS_")
        }
        vals.update({
            f"OPENWAND_CONNECTION_ALIAS_{_get(row['provider']).strip().upper()}": row["alias"].text().strip()
            for row in self._api_key_rows
            if _get(row["provider"]).strip() not in {"", "custom"} and row["alias"].text().strip()
        })
        selected_profile_id = self._selected_profile_id()
        vals["ACTIVE_PROFILE"] = selected_profile_id
        vals["SETTINGS_PROFILE"] = selected_profile_id
        vb = self._voice_block
        vals.update({
            "HOTKEY_VOICE": _get(self._fields["HOTKEY_VOICE"]),
            "HOTKEY_VOICE_2": _get(self._fields["HOTKEY_VOICE_2"]),
            "HOTKEY_VOICE_ENABLED": _get(self._fields["HOTKEY_VOICE_ENABLED"]),
            "VOICE_REVIEW_TRANSCRIPT": _get(self._fields["VOICE_REVIEW_TRANSCRIPT"]),
            "HOTKEY_DICTATE": _get(self._fields["HOTKEY_DICTATE"]),
            "HOTKEY_DICTATE_2": _get(self._fields["HOTKEY_DICTATE_2"]),
            "HOTKEY_DICTATE_ENABLED": _get(self._fields["HOTKEY_DICTATE_ENABLED"]),
            "DICTATE_MODE": _get(self._fields["DICTATE_MODE"]),
            "VOICE_CONTEXT_AMBIENT": str(vb["context_ambient"].isChecked()),
            "VOICE_CONTEXT_CLIPBOARD": str(vb["context_clipboard"].isChecked()),
            "VOICE_CONTEXT_DOCUMENTS_MODE": str(vb["context_documents_mode"].currentData()),
            "VOICE_CONTEXT_BROWSER_MODE": str(vb["context_browser_mode"].currentData()),
            "VOICE_CONTEXT_GITHUB_MODE": str(vb["context_github_mode"].currentData()),
            "VOICE_CONTEXT_MEMORY_MODE": str(vb["context_memory_mode"].currentData()),
            "VOICE_CONTEXT_SCREENSHOT": str(vb["context_screenshot"].currentData()),
            "VOICE_FILE_ACCESS": str(vb["file_access"].currentData()),
            "VOICE_TOOLS": format_tool_modes(vb.get("tool_overrides") or {}),
        })
        # Key conflict check (caller hotkeys + special hotkeys)
        all_keys: list[str] = []
        for blk in self._caller_blocks:
            if blk["enabled"].isChecked():
                all_keys.extend(
                    _get(blk[name]).strip().lower()
                    for name in ("hotkey", "hotkey_2")
                )
        for key in (
            "HOTKEY_ADD_CONTEXT",
            "HOTKEY_CLEAR_CONTEXT",
            "HOTKEY_SNIP",
            "HOTKEY_READ_SELECTION_ALOUD",
            "HOTKEY_VOICE",
            "HOTKEY_DICTATE",
            "HOTKEY_VOICE_LIVE",
        ):
            enabled_widget = self._fields.get(f"{key}_ENABLED")
            if isinstance(enabled_widget, QCheckBox) and not enabled_widget.isChecked():
                continue
            all_keys.extend(
                _get(self._fields[name]).strip().lower()
                for name in (key, f"{key}_2")
            )
        non_empty = [k for k in all_keys if k]
        if len(non_empty) != len(set(non_empty)):
            QMessageBox.warning(self, t("Duplicate keys"),
                                t("Two or more bindings share the same key.\nPlease resolve conflicts before saving."))
            return False
        try:
            self._save_action_file_callers()
        except Exception as exc:  # noqa: BLE001 - Settings must not claim a partial save succeeded
            _settings_log.exception("could not save action files")
            QMessageBox.warning(
                self,
                t("Could not save action files"),
                str(exc) or type(exc).__name__,
            )
            return False
        # The Chat model is combined with the Main LLM, so purge any stale
        # CHAT_LLM_* keys a previous version may have written.
        selected_profile = self._selected_custom_profile_entry()
        if selected_profile is not None:
            slot, profile_id, label = selected_profile
            profile_values = self._current_profile_values(label, profile_id)
            vals.update({f"PROFILE_{slot}_{key}": value for key, value in profile_values.items()})
        # Profiles now have one isolated file each. Copy legacy records into
        # files before updating the selected profile, then store the complete
        # current Settings snapshot in that profile only.
        migration_baseline = {
            key: value
            for key, value in getattr(self, "_dirty_baseline", {}).items()
            if key in vals
        }
        settings_profiles.migrate_legacy_profiles(
            ENV_PATH,
            {**vals, **migration_baseline, **self._env},
        )
        selected_label = (
            t(_PRESET_LABELS[selected_profile_id])
            if selected_profile_id in _PRESET_LABELS
            else (
                selected_profile[2]
                if selected_profile is not None
                else selected_profile_id.replace("-", " ").title()
            )
        )
        settings_profiles.save_profile(
            ENV_PATH,
            selected_profile_id,
            selected_label,
            vals,
        )
        startup_error = ""
        if os.environ.get("OPENWAND_LAUNCH_SMOKE_DISABLE_AUTOSTART_SYNC") != "1":
            try:
                from core.system.autostart import sync_start_on_login

                sync_start_on_login(str(vals.get("START_ON_LOGIN", "")).lower() == "true")
            except Exception as exc:  # noqa: BLE001 - settings still save; startup can be retried.
                startup_error = str(exc) or type(exc).__name__
        remove_keys = {
            key
            for key in self._env
            if key == _SETTINGS_PRESET_KEY or key.startswith(_PRESET_ENV_PREFIX)
        }
        _write_env(
            vals,
            remove_keys=set(secret_store.API_KEY_NAMES)
            | connection_alias_keys
            | _custom_connection_env_keys(self._env)
            | {"CHAT_LLM_PROVIDER", "CHAT_LLM_MODEL", "CHAT_LLM_FALLBACKS", "TOOL_FILE_MODE"}
            | {key for key in self._env if key == "CALLER_COUNT" or key.startswith("CALLER_")}
            | remove_keys,
        )
        if startup_error:
            QMessageBox.warning(
                self,
                t("Could not update startup setting"),
                t(
                    "Your preference was saved, but OpenWand could not update the operating system startup entry:\n\n{error}"
                ).format(error=startup_error),
            )

        # Honor the user's choices, but warn if their model setup probably can't
        # serve them (screenshot → vision; tools → tool-calling provider).
        warnings, warnings_by_target = self._capability_warnings_for_values(vals)
        self._set_warning_markers(warnings_by_target)
        if warnings:
            self._last_save_warnings = list(warnings)
        return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

# Maps provider name → secret store key name (OpenAI-compat providers only)
_PROVIDER_KEY_NAMES: dict[str, str] = {
    "groq":       "GROQ_API_KEY",
    "openai":     "OPENAI_API_KEY",
    "anthropic":  "ANTHROPIC_API_KEY",
    "google":     "GOOGLE_API_KEY",
    "deepseek":   "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral":    "MISTRAL_API_KEY",
    "xai":        "XAI_API_KEY",
    "together":   "TOGETHER_API_KEY",
    "cerebras":   "CEREBRAS_API_KEY",
    "zai":        "ZAI_API_KEY",
    "nvidia":     "NVIDIA_API_KEY",
    "sambanova":  "SAMBANOVA_API_KEY",
    "github_models": "GITHUB_MODELS_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "chutes":     "CHUTES_API_KEY",
    "vercel":     "VERCEL_API_KEY",
    "fireworks":  "FIREWORKS_API_KEY",
    "cohere":     "COHERE_API_KEY",
    "ai21":       "AI21_API_KEY",
    "nebius":     "NEBIUS_API_KEY",
    "custom":     "CUSTOM_API_KEY",
    # ollama: no key
}

_MODEL_HINTS: dict[str, str] = {
    "groq":       "e.g. openai/gpt-oss-120b",
    "openai":     "e.g. gpt-5.6-sol",
    "anthropic":  "e.g. claude-sonnet-5",
    "google":     "e.g. gemini-3.6-flash",
    "chatgpt":    "gpt-5.6-sol  |  gpt-5.6-terra  |  gpt-5.6-luna  |  gpt-5.5  |  gpt-5.3-codex",
    "copilot":    "e.g. gpt-4.1",
    "deepseek":   "e.g. deepseek-v4-flash",
    "openrouter": "e.g. openai/gpt-5.6-sol",
    "mistral":    "e.g. mistral-medium-3-5",
    "xai":        "e.g. grok-4.5",
    "together":   "e.g. meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    "cerebras":   "e.g. llama-4-scout-17b-16e-instruct",
    "zai":        "e.g. glm-4.5-flash",
    "nvidia":     "e.g. meta/llama-3.3-70b-instruct",
    "sambanova":  "e.g. Meta-Llama-3.1-8B-Instruct",
    "github_models": "e.g. openai/gpt-5.4-mini",
    "huggingface": "e.g. meta-llama/Llama-3.3-70B-Instruct",
    "chutes":     "e.g. deepseek-ai/DeepSeek-V3-0324",
    "vercel":     "e.g. openai/gpt-5.4-mini",
    "fireworks":  "e.g. accounts/fireworks/models/llama-v3p1-8b-instruct",
    "cohere":     "e.g. command-a-plus-05-2026",
    "ai21":       "e.g. jamba-mini-2",
    "nebius":     "e.g. meta-llama/Meta-Llama-3.3-70B-Instruct",
    "ollama":     "e.g. llama3  (model pulled locally)",
    "custom":     "model name for your custom endpoint",
}

_PROVIDER_MODELS: dict[str, list[str]] = {
    "groq": [
        "groq/compound",
        "groq/compound-mini",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.6-27b",
    ],
    "openai": [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.5-pro",
        "gpt-5.4",
        "gpt-5.4-pro",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.3-codex",
        "gpt-4.1",
        "gpt-4.1-mini",
        "o3",
    ],
    "anthropic": [
        "claude-fable-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-haiku-4-5",
        "claude-opus-4-5",
        "claude-sonnet-4-5",
    ],
    "google": [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
        "gemini-pro-latest",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ],
    "chatgpt": [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.3-codex",
    ],
    "copilot": [
        "gpt-5.4-mini",
        "gpt-5.4",
        "gpt-4.1",
        "gpt-4o",
        "claude-sonnet-4-6",
        "gemini-2.5-pro",
    ],
    "deepseek": [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ],
    "openrouter": [
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-terra",
        "openai/gpt-5.6-luna",
        "openai/gpt-5.5",
        "openai/gpt-5.4-mini",
        "anthropic/claude-sonnet-4-6",
        "google/gemini-3.5-flash",
        "x-ai/grok-4.3",
        "meta-llama/llama-4-maverick",
        "deepseek/deepseek-r1",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "anthropic/claude-sonnet-4-5",
        "google/gemini-2.5-flash",
        "meta-llama/llama-3.3-70b-instruct",
        "deepseek/deepseek-chat",
        "mistralai/mistral-large",
    ],
    "mistral": [
        "mistral-medium-3-5",
        "mistral-small-2603",
        "mistral-large-latest",
        "magistral-medium-latest",
        "magistral-small-latest",
        "mistral-small-latest",
        "mistral-medium-latest",
        "codestral-latest",
        "devstral-small-latest",
        "ministral-8b-latest",
    ],
    "xai": [
        "grok-4.5",
        "grok-4.20-0309-reasoning",
        "grok-4.20-0309-non-reasoning",
        "grok-4.20-multi-agent-0309",
        "grok-4.3",
        "grok-build-0.1",
        "grok-4",
        "grok-4-latest",
        "grok-3",
        "grok-3-mini",
        "grok-2-latest",
    ],
    "together": [
        "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        "meta-llama/Llama-4-Scout-17B-16E-Instruct",
        "deepseek-ai/DeepSeek-R1",
        "Qwen/Qwen3-235B-A22B-fp8-tput",
        "meta-llama/Llama-3-70b-chat-hf",
        "meta-llama/Llama-3-8b-chat-hf",
        "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "Qwen/Qwen2.5-72B-Instruct-Turbo",
    ],
    "cerebras": [
        "llama-4-scout-17b-16e-instruct",
        "llama-3.3-70b",
        "llama3.1-8b",
        "qwen-3-32b",
    ],
    "zai": [
        "glm-4.5-flash",
        "glm-4.5",
        "glm-4.5-air",
        "glm-4.6",
        "glm-4.7-flash",
    ],
    "nvidia": [
        "meta/llama-4-maverick-17b-128e-instruct",
        "meta/llama-4-scout-17b-16e-instruct",
        "meta/llama-3.3-70b-instruct",
        "nvidia/llama-3.3-nemotron-super-49b-v1",
        "nvidia/llama-3.1-nemotron-70b-instruct",
        "mistralai/mixtral-8x7b-instruct-v0.1",
    ],
    "sambanova": [
        "Meta-Llama-4-Maverick-17B-128E-Instruct",
        "Meta-Llama-3.1-8B-Instruct",
        "Meta-Llama-3.1-70B-Instruct",
        "Meta-Llama-3.3-70B-Instruct",
    ],
    "github_models": [
        "openai/gpt-5.4-mini",
        "openai/gpt-5.4",
        "openai/gpt-4.1",
        "openai/gpt-4.1-mini",
        "openai/gpt-4o-mini",
        "anthropic/claude-sonnet-4-6",
        "meta/Llama-3.3-70B-Instruct",
    ],
    "huggingface": [
        "meta-llama/Llama-3.3-70B-Instruct",
        "Qwen/Qwen3-32B",
        "deepseek-ai/DeepSeek-R1",
        "meta-llama/Llama-3.1-8B-Instruct",
        "Qwen/Qwen2.5-72B-Instruct",
        "mistralai/Mistral-7B-Instruct-v0.3",
    ],
    "chutes": [
        "deepseek-ai/DeepSeek-R1",
        "deepseek-ai/DeepSeek-V3-0324",
        "Qwen/Qwen3-32B",
        "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        "meta-llama/Llama-3.3-70B-Instruct",
    ],
    "vercel": [
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-terra",
        "openai/gpt-5.6-luna",
        "openai/gpt-5.4-mini",
        "openai/gpt-5.4",
        "anthropic/claude-sonnet-4-6",
        "google/gemini-3.5-flash",
        "xai/grok-4.3",
        "openai/gpt-4o-mini",
        "anthropic/claude-3-5-haiku",
        "xai/grok-3-mini",
    ],
    "fireworks": [
        "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "accounts/fireworks/models/deepseek-r1",
        "accounts/fireworks/models/qwen3-235b-a22b",
        "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "accounts/fireworks/models/llama-v3p1-70b-instruct",
        "accounts/fireworks/models/mixtral-8x7b-instruct",
    ],
    "cohere": [
        "command-a-plus-05-2026",
        "command-a-03-2025",
        "command-r-plus",
        "command-r",
    ],
    "ai21": [
        "jamba-large",
        "jamba-mini",
        "jamba-large-1.7",
        "jamba-mini-2",
        "jamba-large-1.7-2025-07",
        "jamba-mini-2-2026-01",
    ],
    "nebius": [
        "meta-llama/Meta-Llama-3.3-70B-Instruct",
        "Qwen/Qwen3-235B-A22B",
        "deepseek-ai/DeepSeek-R1",
        "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "Qwen/Qwen2.5-72B-Instruct",
    ],
    # Ollama is discovered live. Guessed defaults are actively misleading
    # because they may not be installed on this computer.
    "ollama": [],
    "custom": [],
}

_PROVIDER_LABELS: dict[str, str] = {
    "groq":       "Groq",
    "openai":     "OpenAI API",
    "anthropic":  "Anthropic",
    "google":     "Google AI Studio",
    "chatgpt":    "ChatGPT Plus/Pro (OAuth subscription)",
    "copilot":    "GitHub Copilot",
    "deepseek":   "DeepSeek",
    "openrouter": "OpenRouter",
    "mistral":    "Mistral",
    "xai":        "xAI (Grok)",
    "together":   "Together AI",
    "cerebras":   "Cerebras",
    "zai":        "Z.AI / GLM",
    "nvidia":     "NVIDIA",
    "sambanova":  "SambaNova",
    "github_models": "GitHub Models",
    "huggingface": "Hugging Face",
    "chutes":     "Chutes",
    "vercel":     "Vercel AI Gateway",
    "fireworks":  "Fireworks",
    "cohere":     "Cohere",
    "ai21":       "AI21",
    "nebius":     "Nebius",
    "ollama":     "Ollama (local)",
    "custom":     "Custom (OpenAI-compatible)",
    "cartesia":   "Cartesia",
    "elevenlabs": "ElevenLabs",
    "openai_compatible": "OpenAI-compatible (custom)",
    "gpt_sovits": "GPT-SoVITS (local)",
    "kokoro": "Kokoro (local)",
    "none":       "None",
}


def _model_hint(provider: str) -> str:
    """Handle model hint for UI settings panel dialog."""
    return _MODEL_HINTS.get(provider.lower(), "model name")


def _refresh_model_combo(combo: QComboBox, provider: str) -> None:
    """Update a model combo's item list for the given provider without losing the current text."""
    current_text = combo.currentText()
    models = _PROVIDER_MODELS.get(provider, [])
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(models)
    combo.setCurrentText(current_text) if current_text else combo.setCurrentIndex(-1)
    completer = QCompleter(models, combo)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    combo.setCompleter(completer)
    combo.blockSignals(False)


def _sep(visible: bool = False) -> QFrame:
    """Handle sep for UI settings panel dialog."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(
        "color: #e5e5ea; margin: 2px 0;" if visible else "max-height: 0px; color: #000000; margin: 0;"
    )
    return line


def _set(widget, value: str):
    """Set *value* on a combo/line/text-edit widget, honoring custom-value rules."""
    if isinstance(widget, _FolderListEditor):
        widget.setText(value)
    elif isinstance(widget, QComboBox):
        if value == "auto" and widget.property("legacy_auto_means_on"):
            value = "on"
        idx = widget.findData(value)
        if idx >= 0:
            widget.setCurrentIndex(idx)
        else:
            idx = widget.findText(value)
            if idx >= 0:
                widget.setCurrentIndex(idx)
            elif widget.property("allow_custom_saved_value") and value:
                widget.addItem(value, value)
                widget.setCurrentIndex(widget.count() - 1)
            elif widget.isEditable():
                widget.setCurrentText(value)
    elif isinstance(widget, QLineEdit):
        widget.setText(value)
    elif isinstance(widget, QTextEdit):
        widget.setPlainText(value)
    elif isinstance(widget, QCheckBox):
        widget.setChecked(str(value).strip().lower() in {"1", "true", "yes", "on"})
    elif isinstance(widget, QSlider):
        try:
            if "." in str(value):
                widget.setValue(int(round(float(value) * 100)))
            else:
                raw = int(str(value).strip())
                widget.setValue(raw if raw > 2 else raw * 100)
        except Exception:
            widget.setValue(100)


def _get(widget) -> str:
    """Return the current text/data value of a combo/line/text-edit widget."""
    if isinstance(widget, _FolderListEditor):
        return widget.text()
    if isinstance(widget, QComboBox):
        data = widget.currentData()
        return data if data is not None else widget.currentText()
    elif isinstance(widget, QLineEdit):
        return widget.text()
    elif isinstance(widget, QTextEdit):
        return widget.toPlainText()
    elif isinstance(widget, QCheckBox):
        return str(widget.isChecked())
    elif isinstance(widget, QSlider):
        return f"{widget.value() / 100:.2f}".rstrip("0").rstrip(".")
    return ""


def _ms_to_seconds_str(ms_value, default_ms: int) -> str:
    """Render a millisecond env value as a compact seconds string (e.g. "3.5")."""
    try:
        ms = int(float(str(ms_value).strip()))
    except (TypeError, ValueError):
        ms = default_ms
    return f"{ms / 1000:g}"


def _seconds_str_to_ms(text, fallback_ms: int) -> str:
    """Parse a seconds field back to a millisecond env value (min 0.5s)."""
    try:
        return str(max(500, int(round(float(str(text).strip()) * 1000))))
    except (TypeError, ValueError):
        return str(fallback_ms)


def _desc_label(title: str, description: str) -> QLabel:
    """Handle desc label for UI settings panel dialog."""
    lbl = _FlexibleWrapLabel(t(description))
    # Translations can need substantially more lines than the English source.
    # A Preferred vertical policy lets QScrollArea pages compress a wrapped
    # QLabel below its size hint, clipping the last lines into the next row.
    # Minimum keeps the row content-sized while still allowing it to grow and
    # makes the containing page scroll when the translated content is taller.
    lbl.setProperty("description", True)
    lbl.setMaximumWidth(640)
    return lbl


def _tooltip_label(text: str, tooltip: str, *, show_info: bool = True) -> QLabel:
    """Build a translated form/grid label that owns a settings tooltip."""
    translated_text = t(text)
    translated_tooltip = t(tooltip)
    lbl = QLabel(f"{translated_text}  ⓘ" if show_info else translated_text)
    lbl.setToolTip(translated_tooltip)
    lbl.setProperty("_openwand_i18n_tooltip", tooltip)
    if show_info:
        lbl.setObjectName("settingsInfoLabel")
        lbl.setCursor(Qt.CursorShape.WhatsThisCursor)
        lbl.setAccessibleName(translated_text)
        lbl.setAccessibleDescription(translated_tooltip)
        lbl.setProperty("_openwand_info_source", text)
    else:
        lbl.setProperty("_openwand_i18n_text", text)
    return lbl


def _checkbox_with_info(checkbox: QCheckBox, tooltip: str, field_name: str) -> QWidget:
    """Place a checkbox beside an explicit hover-help icon."""
    translated_tooltip = t(tooltip)
    checkbox.setToolTip(translated_tooltip)

    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(7)
    layout.addWidget(checkbox)

    info = QLabel("ⓘ")
    info.setObjectName("settingsInfoLabel")
    info.setToolTip(translated_tooltip)
    info.setCursor(Qt.CursorShape.WhatsThisCursor)
    info.setAccessibleName(checkbox.text())
    info.setAccessibleDescription(translated_tooltip)
    info.setProperty("_openwand_i18n_tooltip", tooltip)
    info.setProperty("_openwand_info_for", field_name)
    layout.addWidget(info)
    layout.addStretch(1)
    return row


def _link_label(text: str, url: str) -> QLabel:
    """Handle link label for UI settings panel dialog."""
    lbl = QLabel(f'<a href="{url}">{t(text)}</a>')
    lbl.setOpenExternalLinks(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    lbl.setToolTip(url)
    return lbl


def _parse_fallback_rows(raw: str) -> list[tuple[str, str]]:
    """Parse fallback rows."""
    return parse_fallback_rows(raw)


def _short_test_error(message: str, route_name: str) -> str:
    """Trim the redundant '<route> test failed: ' prefix so each route line shows
    just the underlying reason (the provider/model is already on the line)."""
    prefix = f"{route_name} test failed: "
    text = message[len(prefix):] if message.startswith(prefix) else message
    return " ".join(text.split())


def _translate_status_value(value: str) -> str:
    """Translate known status value atoms while preserving provider/model names."""
    text = str(value or "")
    if text in {"None", "unavailable", "authorized", "denied", "not_determined", "restricted"}:
        return t(text)
    return text


def _translate_install_detail(detail: str) -> str:
    """Translate dynamic optional-installer detail text while preserving values."""
    text = str(detail or "")
    dynamic_patterns: tuple[tuple[str, str], ...] = (
        (
            r"^still running for (?P<elapsed>.+); no installer output for (?P<quiet>.+)$",
            "still running for {elapsed}; no installer output for {quiet}",
        ),
        (r"^still running for (?P<elapsed>.+)$", "still running for {elapsed}"),
        (
            r"^preparing local voice assets for (?P<elapsed>.+)$",
            "preparing local voice assets for {elapsed}",
        ),
        (
            r"^preparing local assets for (?P<elapsed>.+)$",
            "preparing local assets for {elapsed}",
        ),
        (
            r"^downloading or loading Whisper model for (?P<elapsed>.+)$",
            "downloading or loading Whisper model for {elapsed}",
        ),
    )
    for pattern, template in dynamic_patterns:
        match = re.match(pattern, text)
        if match:
            return t(template).format(**match.groupdict())
    return t(text)


def _kokoro_install_progress_text(line: str) -> str:
    """Return a short user-facing install phase for a raw pip output line."""
    return _optional_install_progress_text(line, "Kokoro")


def _optional_install_progress_text(line: str, display_name: str) -> str:
    """Return a short user-facing install phase for raw pip output."""
    lower = str(line or "").lower()
    if "requirement already satisfied" in lower:
        detail = "checking installed packages"
    elif (
        "collecting " in lower
        or "looking in indexes" in lower
        or "resolving" in lower
        or "resolved " in lower
    ):
        detail = "resolving packages"
    elif "downloading" in lower or "progress " in lower or "prepared " in lower:
        detail = "downloading packages"
    elif "installing collected packages" in lower or lower.startswith("installed "):
        detail = "installing packages"
    elif "successfully installed" in lower:
        detail = "finalizing"
    elif "removing previous" in lower:
        detail = "removing previous install"
    else:
        detail = "working - installer is still running"
    return f"Installing {display_name}: {detail}."


def _optional_install_elapsed_text(display_name: str, elapsed_seconds: int, quiet_seconds: int) -> str:
    """Return a heartbeat status while an optional dependency install is running."""
    elapsed = _format_duration(elapsed_seconds)
    if quiet_seconds >= 60:
        detail = f"still running for {elapsed}; no installer output for {_format_duration(quiet_seconds)}"
    else:
        detail = f"still running for {elapsed}"
    return f"Installing {display_name}: {detail}."


def _optional_install_log_path(display_name: str, optional_packages_dir: Path) -> Path:
    """Return the stable installer log path that survives app restarts."""
    from core import optional_deps

    # Preserve temporary-root injection used by tests and diagnostics.
    if Path(optional_packages_dir) != Path(optional_deps.OPTIONAL_PACKAGES_DIR):
        slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-") or "optional-package"
        return Path(optional_packages_dir).parent / "installers" / f"{slug}-install.log"
    return optional_deps.optional_install_log_path(display_name)


def _optional_install_app_language() -> str:
    """Return the app UI language to pass into detached installer helpers."""
    try:
        from ui import i18n

        return i18n.current_language()
    except Exception:
        try:
            import config as cfg

            return str(getattr(cfg, "APP_LANGUAGE", "") or "").strip()
        except Exception:
            return ""


def _optional_install_env() -> dict[str, str]:
    """Return the optional installer subprocess environment with UI language."""
    from core import optional_deps

    env = optional_deps.pip_install_env()
    language = _optional_install_app_language()
    if language:
        env["APP_LANGUAGE"] = language
    return env


def _privacy_model_install_env() -> dict[str, str]:
    """Return a quiet privacy-installer environment with per-user HF auth."""
    env = _optional_install_env()
    if not str(env.get("HF_TOKEN") or "").strip():
        try:
            from core import secret_store

            token = secret_store.get_secret("HUGGINGFACE_API_KEY").strip()
        except Exception:
            token = ""
        if token:
            env["HF_TOKEN"] = token

    # The model is public, so authentication is an optimization rather than a
    # requirement. OpenWand owns the progress UI and reports real failures itself.
    env["HF_HUB_VERBOSITY"] = "error"
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env


def _optional_install_plan_command(
    *,
    display_name: str,
    packages: list[str],
    pre_install_packages: list[str] | None = None,
    remove_artifacts: list[str] | None = None,
    reinstall: bool = False,
    external_plan_extra: dict[str, object] | None = None,
) -> tuple[list[str], Path, Path, Path]:
    """Write a staged installer plan and return its command, cwd, and log paths.

    Every optional package install runs through the staged installer with
    restart_apply, so pip never writes into the live package folder while OpenWand
    is running — a locked DLL can then no longer corrupt a working install.
    """
    from core import optional_deps, updater

    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        command = [sys.executable, "-m", "runtime.workers.optional_speech_installer"]
    else:
        root = Path(__file__).resolve().parents[2]
        script_path = root / "scripts" / "optional_tts_installer.py"
        if not script_path.exists():
            raise FileNotFoundError(f"installer script is missing: {script_path}")
        command = [sys.executable, str(script_path)]
    log_path = _optional_install_log_path(display_name, optional_deps.OPTIONAL_PACKAGES_DIR)
    status_path = _optional_install_status_path(display_name, optional_deps.OPTIONAL_PACKAGES_DIR)
    plan_path = log_path.with_suffix(".plan.json")
    app_language = _optional_install_app_language()
    plan = {
        "display_name": display_name,
        "packages": packages,
        "pre_install_packages": pre_install_packages or [],
        "remove_artifacts": remove_artifacts or [],
        "reinstall": bool(reinstall),
        "restart_apply": True,
        "wait_pid": updater.openwand_wait_pid(),
        "log_path": str(log_path),
        "status_path": str(status_path),
        "app_language": app_language,
        **(external_plan_extra or {}),
    }
    spec_key = str(plan.get("spec_key") or {
        "stt": "stt",
        "kokoro": "kokoro",
        "elevenlabs": "elevenlabs",
        "live voice": "live_voice",
    }.get(display_name.strip().lower(), ""))
    if spec_key:
        plan["spec_key"] = spec_key
        spec_device = (
            plan.get("kokoro_install_device")
            if spec_key == "kokoro"
            else plan.get("stt_device")
            if spec_key == "stt"
            else None
        )
        plan["install_contract"] = optional_deps.optional_package_contract(
            spec_key,
            device=str(spec_device) if spec_device is not None else None,
        )
    elif str(plan.get("post_install") or "") == "speech_prepare":
        plan["install_contract"] = optional_deps.local_speech_install_contract(
            kokoro_device=str(plan.get("kokoro_install_device") or "cuda"),
            stt_device=str(plan.get("stt_device") or "auto"),
        )
    plan["app_version"] = updater.current_version()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return [*command, "--plan", str(plan_path)], root, log_path, status_path


def _optional_install_status_path(display_name: str, optional_packages_dir: Path) -> Path:
    """Return the durable installer status path beside the installer log."""
    return _optional_install_log_path(display_name, optional_packages_dir).with_suffix(".status.json")


def _write_optional_install_status(path: Path, *, ok: bool | None, message: str) -> None:
    """Persist the latest optional installer result for future Settings opens."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ok": ok,
            "message": str(message or ""),
            "updated_at": time.time(),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _read_optional_install_status(display_name: str, optional_packages_dir: Path) -> dict[str, object]:
    """Read the last optional installer result if one exists."""
    from core import optional_deps

    if Path(optional_packages_dir) == Path(optional_deps.OPTIONAL_PACKAGES_DIR):
        return optional_deps.read_optional_install_status(display_name)
    path = _optional_install_status_path(display_name, optional_packages_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _failed_optional_install_message(status: dict[str, object] | None) -> str:
    """Return the persisted failure message from an installer status payload."""
    if not isinstance(status, dict) or status.get("ok") is not False:
        return ""
    return str(status.get("message") or "").strip()


def _optional_install_no_output_timeout_seconds() -> int:
    """Return how long an optional installer may be silent before OpenWand stops it.

    A value of 0 disables the watchdog. Python package installs can be silent
    for several minutes while resolving or downloading large wheels, so the
    default is to keep reporting elapsed time instead of killing the installer.
    """
    raw = os.environ.get("OPENWAND_OPTIONAL_INSTALL_NO_OUTPUT_TIMEOUT_SECONDS", "0")
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 0


def _format_duration(seconds: int) -> str:
    """Format a short elapsed duration."""
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _optional_install_failure_detail(lines: list[str]) -> str:
    """Return a compact failure detail from installer output."""
    for line in reversed(lines):
        text = _collapse_spaces(line)
        if not text:
            continue
        lower = text.lower()
        if lower.startswith("[notice]"):
            continue
        if len(text) > 500:
            text = text[-500:]
        return f"Last installer message: {text}"
    return "Installer exited with an error before reporting details."


def _collapse_spaces(text: str) -> str:
    """Collapse whitespace from subprocess output."""
    return " ".join(str(text or "").split())


def _cmd_control_escape(text: str) -> str:
    """Escape cmd.exe control characters in simple command literals."""
    return (
        str(text)
        .replace("^", "^^")
        .replace("&", "^&")
        .replace("|", "^|")
        .replace("<", "^<")
        .replace(">", "^>")
    )


def _launch_terminal_command(
    command: list[str],
    *,
    cwd: Path,
    title: str,
    failure_label: str = "OpenWand installer",
    environment: dict[str, str] | None = None,
) -> bool:
    """Launch a command in a user-visible terminal window."""
    try:
        if sys.platform == "win32":
            inner = subprocess.list2cmdline(command)
            quoted_cwd = subprocess.list2cmdline([str(cwd)])
            failure_prompt = (
                "if errorlevel 1 ("
                "set OPENWAND_INSTALL_EXIT=!errorlevel! & "
                "echo. & "
                f"echo {_cmd_control_escape(failure_label)} failed with exit code !OPENWAND_INSTALL_EXIT!. & "
                "echo This window stayed open so you can copy the error above. & "
                "echo Press any key to close this window. & "
                "pause > nul"
                ")"
            )
            cmdline = (
                f"title {_cmd_control_escape(title)} & chcp 65001 > nul & cd /d {quoted_cwd} & "
                f"{inner} & {failure_prompt}"
            )
            flags = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0) or 0)
            kwargs: dict[str, object] = {"cwd": str(cwd), "creationflags": flags}
            if environment:
                process_environment = os.environ.copy()
                process_environment.update(environment)
                kwargs["env"] = process_environment
            subprocess.Popen(["cmd.exe", "/V:ON", "/C", cmdline], **kwargs)
            return True
        if sys.platform == "darwin":
            command_text = _terminal_shell_command(
                command,
                cwd,
                title,
                failure_label=failure_label,
                environment=environment,
            )
            script = (
                f"set commandText to {json.dumps(command_text)}\n"
                "tell application \"Terminal\"\n"
                "  activate\n"
                "  set targetTab to do script commandText\n"
                "  repeat while busy of targetTab\n"
                "    delay 1\n"
                "  end repeat\n"
                "  try\n"
                "    close (window of targetTab) saving no\n"
                "  end try\n"
                "end tell"
            )
            subprocess.Popen(["/usr/bin/osascript", "-e", script], cwd=str(cwd))
            return True
        shell_cmd = _terminal_shell_command(
            command,
            cwd,
            title,
            failure_label=failure_label,
            environment=environment,
        )
        for executable, terminal_command in _linux_terminal_candidates(shell_cmd, cwd=cwd, title=title):
            if shutil.which(executable) and _popen_terminal(terminal_command, cwd, environment=environment):
                return True
    except Exception:
        return False
    return False


def _terminal_shell_command(
    command: list[str],
    cwd: Path,
    title: str,
    *,
    failure_label: str = "OpenWand installer",
    environment: dict[str, str] | None = None,
) -> str:
    """Return the shell body run inside a visible terminal."""
    title_cmd = f"printf '\\033]0;%s\\007' {shlex.quote(str(title))}"
    failure_message = shlex.quote(f"\n{failure_label} failed with exit code %s.\n")
    failure_prompt = (
        "status=$?; "
        "if [ $status -ne 0 ]; then "
        f"printf {failure_message} \"$status\"; "
        "printf 'This window stayed open so you can copy the error above.\\n'; "
        "printf 'Press Enter to close this window.\\n'; "
        "read -r _; "
        "fi; "
        "exit $status"
    )
    run_command = list(command)
    if environment:
        assignments = [f"{key}={value}" for key, value in sorted(environment.items())]
        run_command = ["env", *assignments, *run_command]
    return f"{title_cmd}; cd {shlex.quote(str(cwd))}; {shlex.join(run_command)}; {failure_prompt}"


def _linux_terminal_candidates(shell_cmd: str, *, cwd: Path, title: str) -> list[tuple[str, list[str]]]:
    """Return Linux terminal launcher candidates in preferred order."""
    ordered: list[str] = []
    terminal_env = os.environ.get("TERMINAL", "").strip()
    if terminal_env:
        ordered.append(terminal_env)
    desktop = " ".join(
        os.environ.get(key, "")
        for key in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "KDE_FULL_SESSION", "GNOME_DESKTOP_SESSION_ID")
    ).lower()
    if "kde" in desktop:
        ordered.append("konsole")
    if "gnome" in desktop:
        ordered.extend(["ptyxis", "kgx", "gnome-terminal"])
    ordered.extend([
        "x-terminal-emulator",
        "konsole",
        "gnome-terminal",
        "ptyxis",
        "kgx",
        "xfce4-terminal",
        "mate-terminal",
        "tilix",
        "terminator",
        "lxterminal",
        "kitty",
        "alacritty",
        "wezterm",
        "foot",
        "xterm",
        "uxterm",
        "urxvt",
        "rxvt",
    ])

    candidates: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for raw in ordered:
        parts = shlex.split(raw)
        if not parts:
            continue
        executable = parts[0]
        key = str(Path(executable).name).casefold()
        if key in seen:
            continue
        seen.add(key)
        candidates.append((executable, _linux_terminal_command(parts, shell_cmd, cwd=cwd, title=title)))
    return candidates


def _linux_terminal_command(parts: list[str], shell_cmd: str, *, cwd: Path, title: str) -> list[str]:
    """Build the command line for one Linux terminal emulator."""
    executable = Path(parts[0]).name
    prefix = list(parts)
    cwd_text = str(cwd)
    if executable == "konsole":
        return prefix + ["--workdir", cwd_text, "--title", title, "-e", "sh", "-lc", shell_cmd]
    if executable in {"gnome-terminal", "ptyxis", "kgx", "terminator", "mate-terminal"}:
        return prefix + ["--title", title, "--working-directory", cwd_text, "--", "sh", "-lc", shell_cmd]
    if executable == "xfce4-terminal":
        return prefix + ["--working-directory", cwd_text, "--title", title, "--command", f"sh -lc {shlex.quote(shell_cmd)}"]
    if executable == "lxterminal":
        return prefix + ["--working-directory", cwd_text, "--title", title, "-e", "sh", "-lc", shell_cmd]
    if executable == "tilix":
        return prefix + ["--working-directory", cwd_text, "--title", title, "-e", "sh", "-lc", shell_cmd]
    if executable == "kitty":
        return prefix + ["--directory", cwd_text, "--title", title, "sh", "-lc", shell_cmd]
    if executable == "alacritty":
        return prefix + ["--working-directory", cwd_text, "--title", title, "-e", "sh", "-lc", shell_cmd]
    if executable == "wezterm":
        return prefix + ["start", "--cwd", cwd_text, "--", "sh", "-lc", shell_cmd]
    if executable == "foot":
        return prefix + ["--working-directory", cwd_text, "--title", title, "sh", "-lc", shell_cmd]
    if executable in {"xterm", "uxterm", "urxvt", "rxvt"}:
        return prefix + ["-T", title, "-e", "sh", "-lc", shell_cmd]
    return prefix + ["-e", "sh", "-lc", shell_cmd]


def _popen_terminal(
    command: list[str],
    cwd: Path,
    *,
    environment: dict[str, str] | None = None,
) -> bool:
    """Start a terminal launcher, treating immediate non-zero exit as failure."""
    kwargs: dict[str, object] = {
        "cwd": str(cwd),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if environment:
        process_environment = os.environ.copy()
        process_environment.update(environment)
        kwargs["env"] = process_environment
    proc = subprocess.Popen(command, **kwargs)
    try:
        return proc.wait(timeout=0.35) == 0
    except subprocess.TimeoutExpired:
        return True


def _translate_status_message(message: str) -> str:
    """Handle translate status message for UI settings panel dialog."""
    text = str(message or "")
    if "\n" in text:
        return "\n".join(_translate_status_message(part) for part in text.splitlines())
    cuda_failure = re.fullmatch(
        r"Windows CUDA runtime is incomplete or unloadable(?:: (?P<files>.+))?",
        text,
    )
    if cuda_failure:
        files = cuda_failure.group("files")
        if files:
            return t("Windows CUDA runtime is incomplete or unloadable: {files}").format(
                files=files
            )
        return t("Windows CUDA runtime is incomplete or unloadable")
    route_result = re.match(
        r"^(?P<mark>[✓✗]) (?P<label>Primary|Fallback (?P<index>\d+)) — "
        r"(?P<provider>.+?) / (?P<model>.+?): (?P<detail>.+)$",
        text,
    )
    if route_result:
        groups = route_result.groupdict()
        label = (
            t("Primary")
            if groups["label"] == "Primary"
            else t("Fallback {index}").format(index=groups["index"])
        )
        detail = (
            t("Passed")
            if groups["detail"] == "OK"
            else _translate_status_message(groups["detail"])
        )
        return t("{mark} {label} — {provider} / {model}: {detail}").format(
            mark=groups["mark"],
            label=label,
            provider=groups["provider"],
            model=groups["model"],
            detail=detail,
        )
    fallback_summary = re.match(
        r"^Primary works\. (?P<count>\d+) fallback\(s\) failed "
        r"\((?P<fallbacks>.+)\) — fix or remove just those rows\.$",
        text,
    )
    if fallback_summary:
        fallback_labels = ", ".join(
            t("Fallback {index}").format(index=match.group("index"))
            if (match := re.fullmatch(r"Fallback (?P<index>\d+)", item.strip()))
            else item.strip()
            for item in fallback_summary.group("fallbacks").split(",")
        )
        return t(
            "Primary works. {count} fallback(s) failed ({fallbacks}) — "
            "fix or remove just those rows."
        ).format(
            count=fallback_summary.group("count"),
            fallbacks=fallback_labels,
        )
    dynamic_patterns: tuple[tuple[str, str], ...] = (
        (
            r"^(?P<route_name>Primary|Fallback \d+) route is missing provider or model\.$",
            "{route_name} route is missing provider or model.",
        ),
        (
            r"^(?P<route_name>Primary|Fallback \d+) route uses chatgpt but you are not logged in\.$",
            "{route_name} route uses chatgpt but you are not logged in.",
        ),
        (
            r"^(?P<route_name>Primary|Fallback \d+) route uses copilot but no Copilot token is stored and "
            r"you are not signed in to GitHub\.$",
            "{route_name} route uses copilot but no Copilot token is stored and you are not signed in to GitHub.",
        ),
        (
            r"^(?P<route_name>Primary|Fallback \d+) route uses 'custom' but CUSTOM_BASE_URL is not set\.$",
            "{route_name} route uses 'custom' but CUSTOM_BASE_URL is not set.",
        ),
        (
            r"^(?P<route_name>Primary|Fallback \d+) route uses (?P<provider>.+), but its API key is not configured\.$",
            "{route_name} route uses {provider}, but its API key is not configured.",
        ),
        (
            r"^(?P<route_name>Primary|Fallback \d+) custom base URL is invalid: (?P<value>.+)$",
            "{route_name} custom base URL is invalid: {value}",
        ),
        (
            r"^Model '(?P<model>.+)' is not supported by the ChatGPT Codex endpoint\. "
            r"Use one of: (?P<models>.+)$",
            "Model '{model}' is not supported by the ChatGPT Codex endpoint. Use one of: {models}",
        ),
        (r"^No API key configured for (?P<provider>.+)$", "No API key configured for {provider}"),
        (r"^Unknown provider: (?P<provider>.+)$", "Unknown provider: {provider}"),
        (r"^Unknown vision provider: (?P<provider>.+)$", "Unknown vision provider: {provider}"),
        (r"^Unknown LLM provider: (?P<provider>.+)$", "Unknown LLM provider: {provider}"),
        (r"^Logged in using (?P<method>.+)$", "Logged in using {method}"),
        (r"^LLM route configured: (?P<route>.+)\.$", "LLM route configured: {route}."),
        (r"^LLM route incomplete: (?P<route>.+)\.$", "LLM route incomplete: {route}."),
        (r"^TTS provider configured: (?P<provider>.+)\.$", "TTS provider configured: {provider}."),
        (r"^STT model configured: (?P<model>.+)\. faster-whisper is installed\.$", "STT model configured: {model}. faster-whisper is installed."),
        (r"^STT model configured: (?P<model>.+), but faster-whisper is not installed\.$", "STT model configured: {model}, but faster-whisper is not installed."),
        (r"^STT model configured: (?P<model>.+), but STT verification failed: (?P<error>.+)$", "STT model configured: {model}, but STT verification failed: {error}"),
        (r"^STT packages and runtime are verified for (?P<model>.+); the model loads on first use\.$", "STT packages and runtime are verified for {model}; the model loads on first use."),
        (r"^STT model configured: (?P<model>.+)\.$", "STT model configured: {model}."),
        (r"^(?P<count>\d+) hotkeys configured\.$", "{count} hotkeys configured."),
        (r"^Accessibility permission: (?P<value>.+)\.$", "Accessibility permission: {value}."),
        (r"^Screen recording permission: (?P<value>.+)\.$", "Screen recording permission: {value}."),
        (r"^Microphone permission: (?P<value>.+)\.$", "Microphone permission: {value}."),
        (r"^LLM test failed: (?P<message>.+)$", "LLM test failed: {message}"),
        (r"^Kokoro install failed: (?P<message>.+)$", "Kokoro install failed: {message}"),
        (r"^Kokoro installed, but runtime verification failed: (?P<message>.+)$", "Kokoro installed, but runtime verification failed: {message}"),
        (r"^Kokoro installed, but Torch verification failed: (?P<message>.+)$", "Kokoro installed, but Torch verification failed: {message}"),
        (r"^Kokoro installed, but CUDA Torch verification failed: (?P<message>.+)$", "Kokoro installed, but CUDA Torch verification failed: {message}"),
        (r"^Kokoro package installed, but voice asset preparation failed: (?P<message>.+)$", "Kokoro package installed, but voice asset preparation failed: {message}"),
        (r"^Kokoro package installed; (?P<detail>.+)\.$", "Kokoro package installed; {detail}."),
        (r"^Kokoro package install failed: (?P<message>.+)$", "Kokoro package install failed: {message}"),
        (r"^(?P<display_name>.+) package files match this OpenWand release\.$", "{display_name} package files match this OpenWand release."),
        (r"^(?P<display_name>.+) package files do not match this OpenWand release: (?P<message>.+)\.$", "{display_name} package files do not match this OpenWand release: {message}."),
        (r"^(?P<display_name>.+) packages are staged\. Click Restart app now to close OpenWand and apply them\.$", "{display_name} packages are staged. Click Restart app now to close OpenWand and apply them."),
        (r"^(?P<display_name>.+) packages are staged\. Click Restart app now to close OpenWand, replace locked files, verify the install, and reopen\.$", "{display_name} packages are staged. Click Restart app now to close OpenWand, replace locked files, verify the install, and reopen."),
        (r"^(?P<display_name>.+) packages stay staged and will be applied the next time OpenWand restarts\.$", "{display_name} packages stay staged and will be applied the next time OpenWand restarts."),
        (r"^ElevenLabs install failed: (?P<message>.+)$", "ElevenLabs install failed: {message}"),
        (r"^STT install failed: (?P<message>.+)$", "STT install failed: {message}"),
        (r"^STT package install failed: (?P<message>.+)$", "STT package install failed: {message}"),
        (r"^STT installed, but model verification failed: (?P<message>.+)$", "STT installed, but model verification failed: {message}"),
        (r"^STT package installed, but model download/load failed: (?P<message>.+)$", "STT package installed, but model download/load failed: {message}"),
        (r"^STT package installed; downloading or loading Whisper model (?P<model>.+)\.$", "STT package installed; downloading or loading Whisper model {model}."),
        (r"^STT installed and model ready: (?P<summary>.+)\.$", "STT installed and model ready: {summary}."),
        (r"^Installing STT: downloading or loading Whisper model (?P<model>.+)\.$", "Installing STT: downloading or loading Whisper model {model}."),
        (r"^Kokoro is installed with GPU support \((?P<device>.+)\)\.$", "Kokoro is installed with GPU support ({device})."),
        (r"^Installing Kokoro: (?P<detail>.+)\.$", "Installing Kokoro: {detail}."),
        (r"^Installing ElevenLabs: (?P<detail>.+)\.$", "Installing ElevenLabs: {detail}."),
        (r"^Installing STT: (?P<detail>.+)\.$", "Installing STT: {detail}."),
        (r"^LLM route uses (?P<provider>.+) but you are not logged in\.$", "LLM route uses {provider} but you are not logged in."),
        (
            r"^Could not reach (?P<provider>ChatGPT|GitHub) to verify sign-in: (?P<error>.+)$",
            "Could not reach {provider} to verify sign-in: {error}",
        ),
        (
            r"^(?P<provider>ChatGPT|GitHub) sign-in check failed \(HTTP (?P<status>\d+)\)\.$",
            "{provider} sign-in check failed (HTTP {status}).",
        ),
    )
    for pattern, template in dynamic_patterns:
        match = re.match(pattern, text)
        if match:
            groups = match.groupdict()
            if "message" in groups:
                groups["message"] = _translate_status_message(groups["message"])
            if "error" in groups:
                groups["error"] = _translate_status_message(groups["error"])
            if "value" in groups:
                groups["value"] = _translate_status_value(groups["value"])
            if "detail" in groups:
                groups["detail"] = _translate_install_detail(groups["detail"])
            if "display_name" in groups:
                groups["display_name"] = t(groups["display_name"])
            if "route_name" in groups:
                route_name = groups["route_name"]
                fallback = re.fullmatch(r"Fallback (?P<index>\d+)", route_name)
                groups["route_name"] = (
                    t("Primary")
                    if route_name == "Primary"
                    else t("Fallback {index}").format(index=fallback.group("index"))
                    if fallback
                    else route_name
                )
            return t(template).format(**groups)
    for prefix in (
        "Error reading status: ",
        "Keychain error: ",
        "Test failed: ",
        "Could not load tools: ",
        "Error: ",
        "Logged in • account ",
        "Logged in - account ",
        "Logged in as ",
        "Scopes: ",
    ):
        if text.startswith(prefix):
            translated_prefix = t("Logged in • account ") if prefix == "Logged in - account " else t(prefix)
            suffix = text[len(prefix):]
            if prefix in {"Error reading status: ", "Keychain error: ", "Test failed: ", "Error: "}:
                suffix = _translate_status_message(suffix)
            return translated_prefix + suffix
    return t(text)


def _dialog_is_usable(dialog: SettingsDialog | None) -> bool:
    """Handle dialog is usable for UI settings panel dialog."""
    if dialog is None or getattr(dialog, "_disposing", False):
        return False
    try:
        import shiboken6

        return bool(shiboken6.isValid(dialog))
    except Exception:
        try:
            dialog.objectName()
            return True
        except RuntimeError:
            return False


def _clear_settings_dialog(_obj=None) -> None:
    """Clear settings dialog."""
    global _settings_dialog
    if _obj is None or _settings_dialog is None or _obj is _settings_dialog:
        _settings_dialog = None


def _open_settings_now(
    parent=None,
    on_apply=None,
    on_setup_check=None,
    extra_tools=None,
    initial_page: str | None = None,
):
    """Open settings now."""
    global _settings_dialog, _settings_open_pending
    _settings_open_pending = False
    # Never parent the settings window to the floating icon overlay: that overlay
    # is a Qt.Tool/no-taskbar utility window, and attaching a normal settings
    # dialog to it is fragile across focus-heavy interactions such as hotkey
    # recording. Keep Settings top-level on every platform.
    dialog_parent = None
    if not _dialog_is_usable(_settings_dialog):
        _settings_dialog = None
    elif not _settings_dialog.isVisible():
        _settings_dialog._disposing = True
        _settings_dialog.deleteLater()
        _settings_dialog = None

    if _settings_dialog is None:
        _settings_dialog = SettingsDialog(
            dialog_parent,
            on_apply=on_apply,
            on_setup_check=on_setup_check,
            extra_tools=extra_tools,
        )
        _settings_dialog.destroyed.connect(_clear_settings_dialog)
    else:
        _settings_dialog._on_apply = on_apply
        _settings_dialog._on_setup_check = on_setup_check
        _settings_dialog._extra_tools = _normalize_extra_tool_payloads(extra_tools)

    if _settings_dialog.isMinimized():
        _settings_dialog.showNormal()
    _settings_dialog.show()
    if initial_page:
        _settings_dialog.select_page(initial_page)
    _settings_dialog.raise_()
    _settings_dialog.activateWindow()


def open_settings(
    parent=None,
    on_apply=None,
    on_setup_check=None,
    extra_tools=None,
    initial_page: str | None = None,
):
    """Open settings."""
    global _settings_open_pending
    if _settings_open_pending:
        return
    _settings_open_pending = True
    QTimer.singleShot(
        50,
        lambda: _open_settings_now(
            parent=parent,
            on_apply=on_apply,
            on_setup_check=on_setup_check,
            extra_tools=extra_tools,
            initial_page=initial_page,
        ),
    )
