"""Qt subprocess installer window for optional runtime packages."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ui.i18n import t
from ui.shared.theme import is_dark_mode, theme_colors
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen


class OptionalInstallDialog(QDialog):
    """Run an installer subprocess while streaming output inside Wisp."""

    install_finished = Signal(int)

    def __init__(
        self,
        *,
        title: str,
        command: list[str],
        cwd: Path | str | None = None,
        log_path: Path | str | None = None,
        env: dict[str, str] | None = None,
        mirror_output_to_log: bool = True,
        subtitle: str = "",
        auto_start: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._command = [str(part) for part in command]
        self._cwd = Path(cwd).expanduser() if cwd else Path.cwd()
        self._log_path = Path(log_path).expanduser() if log_path else None
        self._env = {str(key): str(value) for key, value in (env or {}).items()}
        self._mirror_output_to_log = bool(mirror_output_to_log)
        self._process: QProcess | None = None
        self._exit_code: int | None = None
        self._cancel_requested = False
        self._started = False

        self.setWindowTitle(title)
        self.setModal(False)
        enable_standard_window_controls(self)
        self._build_ui(title, subtitle)
        fit_window_to_screen(self, preferred_width=760, preferred_height=520)
        if auto_start:
            QTimer.singleShot(0, self.start)

    @property
    def exit_code(self) -> int | None:
        """Return the subprocess exit code after completion."""
        return self._exit_code

    def log_text(self) -> str:
        """Return the visible installer log text."""
        return self._log.toPlainText()

    def start(self) -> None:
        """Start the installer subprocess."""
        if self._started:
            return
        self._started = True
        if not self._command:
            self._finish_without_process(1, t("Installer command is empty."))
            return

        self._append_line(t("Starting installer..."))
        self._append_line(" ".join(self._command))
        self._set_running(True)

        process = QProcess(self)
        process.setProgram(self._command[0])
        process.setArguments(self._command[1:])
        process.setWorkingDirectory(str(self._cwd))
        process.setProcessEnvironment(self._process_environment())
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._drain_output)
        process.errorOccurred.connect(self._handle_process_error)
        process.finished.connect(self._handle_finished)
        self._process = process
        process.start()
        if not process.waitForStarted(3000):
            error = process.errorString() or t("Installer process could not be started.")
            self._finish_without_process(1, error)

    def cancel(self) -> None:
        """Request installer cancellation."""
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        self._cancel_requested = True
        self._status.setText(t("Cancelling installer..."))
        self._append_line(t("Cancelling installer..."))
        process.terminate()
        QTimer.singleShot(3000, self._kill_if_still_running)

    def _build_ui(self, title: str, subtitle: str) -> None:
        self.setStyleSheet(self._dialog_style())
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        header = QLabel(title)
        header.setObjectName("installerTitle")
        header.setWordWrap(True)
        root.addWidget(header)

        self._subtitle = QLabel(subtitle or t("Wisp is preparing the optional install."))
        self._subtitle.setObjectName("installerSubtitle")
        self._subtitle.setWordWrap(True)
        root.addWidget(self._subtitle)

        status_card = QFrame()
        status_card.setObjectName("installerCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(12, 12, 12, 12)
        status_layout.setSpacing(8)
        self._status = QLabel(t("Ready to start."))
        self._status.setObjectName("installerStatus")
        self._status.setWordWrap(True)
        status_layout.addWidget(self._status)
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        status_layout.addWidget(self._progress)
        root.addWidget(status_card)

        self._log = QPlainTextEdit()
        self._log.setObjectName("installerLog")
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._log.setMinimumHeight(260)
        root.addWidget(self._log, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self._copy_btn = QPushButton(t("Copy log"))
        self._copy_btn.clicked.connect(self._copy_log)
        buttons.addWidget(self._copy_btn)
        self._open_log_btn = QPushButton(t("Open log folder"))
        self._open_log_btn.clicked.connect(self._open_log_folder)
        self._open_log_btn.setEnabled(self._log_path is not None)
        buttons.addWidget(self._open_log_btn)
        self._cancel_btn = QPushButton(t("Cancel"))
        self._cancel_btn.clicked.connect(self.cancel)
        buttons.addWidget(self._cancel_btn)
        self._close_btn = QPushButton(t("Close"))
        self._close_btn.clicked.connect(self.close)
        self._close_btn.setEnabled(False)
        buttons.addWidget(self._close_btn)
        root.addLayout(buttons)

    def _dialog_style(self) -> str:
        c = theme_colors(is_dark_mode())
        return f"""
            QDialog {{
                background: {c["bg"]};
                color: {c["text"]};
            }}
            QLabel#installerTitle {{
                color: {c["text"]};
                font-size: 15pt;
                font-weight: 700;
                letter-spacing: 0px;
            }}
            QLabel#installerSubtitle {{
                color: {c["text_dim"]};
                font-size: 9pt;
            }}
            QLabel#installerStatus {{
                color: {c["text"]};
                font-weight: 600;
            }}
            QFrame#installerCard {{
                background: {c["card"]};
                border: 1px solid {c["border"]};
                border-radius: 8px;
            }}
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
            QPushButton:disabled {{
                border-color: {c["border"]};
                color: {c["text_dim"]};
            }}
            QProgressBar {{
                background: {c["surface"]};
                border: 1px solid {c["border"]};
                border-radius: 6px;
                min-height: 10px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background: {c["accent"]};
                border-radius: 5px;
            }}
        """

    def _set_running(self, running: bool) -> None:
        if running:
            self._status.setText(t("Installer is running."))
            self._progress.setRange(0, 0)
            self._cancel_btn.setEnabled(True)
            self._close_btn.setEnabled(False)
        else:
            self._progress.setRange(0, 1)
            self._progress.setValue(1)
            self._cancel_btn.setEnabled(False)
            self._close_btn.setEnabled(True)

    def _process_environment(self) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        for key, value in self._env.items():
            env.insert(key, value)
        return env

    def _drain_output(self) -> None:
        process = self._process
        if process is None:
            return
        data = bytes(process.readAllStandardOutput())
        if data:
            self._append_text(data.decode("utf-8", errors="replace"))

    def _append_line(self, text: str) -> None:
        self._append_text(str(text).rstrip("\n") + "\n")

    def _append_text(self, text: str) -> None:
        if not text:
            return
        self._log.moveCursor(QTextCursor.MoveOperation.End)
        self._log.insertPlainText(text)
        self._log.moveCursor(QTextCursor.MoveOperation.End)
        if self._mirror_output_to_log and self._log_path is not None:
            try:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._log_path.open("a", encoding="utf-8") as handle:
                    handle.write(text)
            except OSError:
                pass

    def _handle_process_error(self, _error: QProcess.ProcessError) -> None:
        process = self._process
        message = process.errorString() if process is not None else t("Installer process failed.")
        self._append_line(message)

    def _handle_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._drain_output()
        code = int(exit_code)
        if self._cancel_requested and code == 0:
            code = 1
        self._exit_code = code
        self._set_running(False)
        if self._cancel_requested:
            self._status.setText(t("Installer cancelled."))
            self._append_line(t("Installer cancelled."))
        elif code == 0:
            self._status.setText(t("Installer completed successfully."))
            self._append_line(t("Installer completed successfully."))
        else:
            self._status.setText(t("Installer failed with exit code {code}.").format(code=code))
            self._append_line(t("Installer failed with exit code {code}.").format(code=code))
        self.install_finished.emit(code)

    def _finish_without_process(self, code: int, message: str) -> None:
        self._exit_code = int(code)
        self._set_running(False)
        self._status.setText(message)
        self._append_line(message)
        self.install_finished.emit(int(code))

    def _kill_if_still_running(self) -> None:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _copy_log(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.log_text())

    def _open_log_folder(self) -> None:
        if self._log_path is None:
            return
        folder = self._log_path.parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def closeEvent(self, event) -> None:  # noqa: N802
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            event.ignore()
            self._status.setText(t("Cancel the installer before closing this window."))
            return
        super().closeEvent(event)


def optional_install_mock_command(*, mode: str = "success", lines: int = 6, delay: float = 0.05) -> list[str]:
    """Return a harmless command that exercises the installer dialog process path."""
    import sys

    return [
        sys.executable,
        "-m",
        "runtime.workers.mock_optional_install",
        "--mode",
        mode,
        "--lines",
        str(lines),
        "--delay",
        str(delay),
    ]
