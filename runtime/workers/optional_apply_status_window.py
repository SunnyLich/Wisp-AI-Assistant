"""Standalone Qt status window for restart-time optional package applies."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ui.i18n import set_language
from ui.i18n import t
from ui.shared.theme import is_dark_mode, theme_colors
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen


def _translate_detail(detail: str) -> str:
    return t(str(detail or ""))


def _translate_status_message(message: str) -> str:
    text = str(message or "")
    patterns: tuple[tuple[str, str], ...] = (
        (r"^(?P<display_name>.+) staged install is waiting for Wisp to close\.$", "{display_name} staged install is waiting for Wisp to close."),
        (r"^(?P<display_name>.+) staged install is applying package files\.$", "{display_name} staged install is applying package files."),
        (r"^(?P<display_name>.+) staged install is verifying package files\.$", "{display_name} staged install is verifying package files."),
        (r"^(?P<display_name>.+) staged install failed: (?P<message>.+)$", "{display_name} staged install failed: {message}"),
        (r"^(?P<display_name>.+) packages are staged\. Click Restart app now to close Wisp and apply them\.$", "{display_name} packages are staged. Click Restart app now to close Wisp and apply them."),
        (r"^(?P<display_name>.+) packages are staged\. Click Restart app now to close Wisp, replace locked files, verify the install, and reopen\.$", "{display_name} packages are staged. Click Restart app now to close Wisp, replace locked files, verify the install, and reopen."),
        (r"^(?P<display_name>.+) packages stay staged and will be applied the next time Wisp restarts\.$", "{display_name} packages stay staged and will be applied the next time Wisp restarts."),
        (r"^(?P<display_name>.+) installed and model ready: (?P<summary>.+)\.$", "{display_name} installed and model ready: {summary}."),
        (r"^(?P<display_name>.+) package files match this Wisp release\.$", "{display_name} package files match this Wisp release."),
        (r"^(?P<display_name>.+) package files do not match this Wisp release: (?P<message>.+)\.$", "{display_name} package files do not match this Wisp release: {message}."),
        (r"^STT package installed; downloading or loading Whisper model (?P<model>.+)\.$", "STT package installed; downloading or loading Whisper model {model}."),
        (r"^STT package installed, but model download/load failed: (?P<message>.+)$", "STT package installed, but model download/load failed: {message}"),
        (r"^Kokoro package installed; (?P<detail>.+)\.$", "Kokoro package installed; {detail}."),
        (r"^Kokoro package installed, but voice asset preparation failed: (?P<message>.+)$", "Kokoro package installed, but voice asset preparation failed: {message}"),
    )
    for pattern, template in patterns:
        match = re.match(pattern, text)
        if match:
            groups = match.groupdict()
            if "detail" in groups:
                groups["detail"] = _translate_detail(groups["detail"])
            if "display_name" in groups:
                groups["display_name"] = t(groups["display_name"])
            return t(template).format(**groups)
    return t(text)


class ApplyStatusWindow(QDialog):
    """Poll installer status/log files while Wisp is closed."""

    def __init__(self, *, display_name: str, status_path: Path, log_path: Path | None = None) -> None:
        super().__init__()
        self._display_name = display_name
        self._status_path = status_path
        self._log_path = log_path
        self._last_log_text = ""
        self._finished = False
        self.setWindowTitle(t("Wisp {display_name} apply").format(display_name=display_name))
        enable_standard_window_controls(self)
        self._build_ui()
        fit_window_to_screen(self, preferred_width=720, preferred_height=440)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _build_ui(self) -> None:
        c = theme_colors(is_dark_mode())
        self.setStyleSheet(
            f"""
            QDialog {{ background: {c["bg"]}; color: {c["text"]}; }}
            QLabel#title {{ color: {c["text"]}; font-size: 14pt; font-weight: 700; }}
            QLabel#subtitle {{ color: {c["text_dim"]}; font-size: 9pt; }}
            QPlainTextEdit {{
                background: {c["surface"]};
                border: 1px solid {c["border"]};
                border-radius: 8px;
                color: {c["text"]};
                font-family: Consolas, "SF Mono", "DejaVu Sans Mono", monospace;
                font-size: 9pt;
                padding: 8px;
            }}
            QPushButton {{
                border: 1.5px solid {c["accent"]};
                color: {c["accent"]};
                border-radius: 8px;
                padding: 6px 14px;
                background: transparent;
            }}
            QPushButton:hover {{ background: {c["accent_soft"]}; }}
            QPushButton:disabled {{ border-color: {c["border"]}; color: {c["text_dim"]}; }}
            QProgressBar {{
                background: {c["surface"]};
                border: 1px solid {c["border"]};
                border-radius: 6px;
                min-height: 10px;
            }}
            QProgressBar::chunk {{ background: {c["accent"]}; border-radius: 5px; }}
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)

        title = QLabel(t("Applying {display_name}").format(display_name=self._display_name))
        title.setObjectName("title")
        title.setWordWrap(True)
        root.addWidget(title)

        self._status = QLabel(t("Preparing to apply optional speech packages."))
        self._status.setObjectName("subtitle")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        root.addWidget(self._progress)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(220)
        root.addWidget(self._log, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._open_log_btn = QPushButton(t("Open log folder"))
        self._open_log_btn.clicked.connect(self._open_log_folder)
        self._open_log_btn.setEnabled(self._log_path is not None)
        buttons.addWidget(self._open_log_btn)
        self._close_btn = QPushButton(t("Close"))
        self._close_btn.clicked.connect(self.close)
        self._close_btn.setEnabled(False)
        buttons.addWidget(self._close_btn)
        root.addLayout(buttons)

    def _read_status(self) -> dict[str, object]:
        try:
            data = json.loads(self._status_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _refresh_log(self) -> None:
        if self._log_path is None:
            return
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        if text == self._last_log_text:
            return
        self._last_log_text = text
        lines = text.splitlines()[-160:]
        self._log.setPlainText("\n".join(lines))
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._log.setTextCursor(cursor)

    def _refresh(self) -> None:
        self._refresh_log()
        status = self._read_status()
        message = str(status.get("message") or "").strip()
        if message:
            self._status.setText(_translate_status_message(message))
        ok = status.get("ok")
        if ok is True:
            self._finish(t("{display_name} applied successfully.").format(display_name=self._display_name), auto_close=True)
        elif ok is False and not status.get("restart_apply"):
            self._finish(message or t("{display_name} apply failed.").format(display_name=self._display_name), auto_close=False)

    def _finish(self, message: str, *, auto_close: bool) -> None:
        if self._finished:
            return
        self._finished = True
        self._status.setText(message)
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._close_btn.setEnabled(True)
        if auto_close:
            QTimer.singleShot(2500, self.close)

    def _open_log_folder(self) -> None:
        if self._log_path is None:
            return
        folder = self._log_path.expanduser().resolve().parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--log-path", default="")
    parser.add_argument("--language", default="")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    set_language(args.language or None, app=app)
    window = ApplyStatusWindow(
        display_name=args.display_name,
        status_path=Path(args.status_path).expanduser(),
        log_path=Path(args.log_path).expanduser() if args.log_path else None,
    )
    window.show()
    window.raise_()
    window.activateWindow()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
