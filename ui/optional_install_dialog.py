"""Qt subprocess installer window for optional runtime packages."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ui.i18n import t
from ui.shared.activity_spinner import ActivitySpinner
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
        status_path: Path | str | None = None,
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
        self._status_path = Path(status_path).expanduser() if status_path else None
        self._env = {str(key): str(value) for key, value in (env or {}).items()}
        self._mirror_output_to_log = bool(mirror_output_to_log)
        self._process: QProcess | None = None
        self._exit_code: int | None = None
        self._cancel_requested = False
        self._started = False
        self._progress_tail = ""
        self._progress_value: int | None = None
        self._started_at: float | None = None

        self.setWindowTitle(title)
        self.setModal(False)
        enable_standard_window_controls(self)
        self._build_ui(title, subtitle)
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(500)
        self._progress_timer.timeout.connect(self._refresh_progress_status)
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
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self._spinner = ActivitySpinner()
        status_row.addWidget(self._spinner)
        self._status = QLabel(t("Ready to start."))
        self._status.setObjectName("installerStatus")
        self._status.setWordWrap(True)
        status_row.addWidget(self._status, 1)
        self._progress_percent = QLabel("")
        self._progress_percent.setObjectName("installerPercent")
        self._progress_percent.setVisible(False)
        status_row.addWidget(self._progress_percent)
        self._elapsed = QLabel(t("Starting…"))
        self._elapsed.setObjectName("installerElapsed")
        status_row.addWidget(self._elapsed)
        status_layout.addLayout(status_row)
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
        self._restart_btn = QPushButton(t("Restart app now"))
        self._restart_btn.clicked.connect(self._restart_app_now)
        self._restart_btn.setVisible(False)
        buttons.addWidget(self._restart_btn)
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
            QLabel#installerPercent {{
                color: {c["accent"]};
                font-size: 14pt;
                font-weight: 700;
                min-width: 58px;
            }}
            QLabel#installerElapsed {{
                color: {c["text_dim"]};
                font-size: 9pt;
                min-width: 72px;
            }}
            QLabel#activitySpinner {{
                color: {c["accent"]};
                font-size: 16pt;
                font-weight: 700;
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
        """

    def _set_running(self, running: bool) -> None:
        if running:
            if self._started_at is None:
                self._started_at = time.monotonic()
            self._status.setText(t("Installer is running."))
            self._spinner.start()
            self._progress_timer.start()
            self._refresh_progress_status()
            self._cancel_btn.setEnabled(True)
            self._close_btn.setEnabled(False)
        else:
            self._progress_timer.stop()
            self._spinner.stop()
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
        self._update_percentage(text)
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

    def _update_percentage(self, text: str) -> None:
        """Show the latest explicit download/install percentage from process output."""
        if self._status_path is not None:
            # uv/pip percentages describe one wheel, not the complete install.
            # The staged installer status contains truthful overall phases.
            return
        combined = self._progress_tail + str(text)
        self._progress_tail = combined[-160:]
        percent_matches = list(re.finditer(r"(?<!\d)(100|\d{1,2})(?:\.\d+)?%", combined))
        if percent_matches:
            self._set_progress_percent(int(float(percent_matches[-1].group(0)[:-1])))
            return
        fraction_matches = list(re.finditer(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", combined))
        if fraction_matches:
            current, total = (int(value) for value in fraction_matches[-1].groups())
            if total > 0 and 0 <= current <= total:
                self._set_progress_percent(round((current / total) * 100))

    def _set_progress_percent(self, value: int | float) -> None:
        self._progress_value = max(0, min(100, round(float(value))))
        self._progress_percent.setText(f"{self._progress_value}%")
        self._progress_percent.setVisible(True)

    def _refresh_progress_status(self) -> None:
        """Poll durable phase progress and always show useful elapsed time."""
        if self._status_path is not None:
            try:
                data = json.loads(self._status_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if isinstance(data, dict):
                raw_percent = data.get("progress_percent")
                if isinstance(raw_percent, (int, float)):
                    self._set_progress_percent(raw_percent)
                message = str(data.get("message") or "").strip()
                if message:
                    self._status.setText(t(message))
        elapsed = 0 if self._started_at is None else max(0, int(time.monotonic() - self._started_at))
        minutes, seconds = divmod(elapsed, 60)
        text = f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"
        self._elapsed.setText(t("Elapsed {elapsed}").format(elapsed=text))

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
            self._spinner.stop("×")
            self._status.setText(t("Installer cancelled."))
            self._append_line(t("Installer cancelled."))
        elif code == 0:
            self._spinner.stop("✓")
            self._set_progress_percent(100)
            self._elapsed.setText("")
            self._status.setText(t("Installer completed successfully."))
            self._append_line(t("Installer completed successfully."))
        else:
            self._spinner.stop("×")
            message = self._persisted_failure_message() or t("Installer failed with exit code {code}.").format(code=code)
            self._status.setText(message)
            self._append_line(message)
        if code == 0 and self._restart_apply_ready():
            self._restart_btn.setVisible(True)
            self._restart_btn.setDefault(True)
        self.install_finished.emit(code)

    def _restart_apply_ready(self) -> bool:
        if self._status_path is None:
            return False
        try:
            data = json.loads(self._status_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return isinstance(data, dict) and bool(data.get("restart_apply"))

    @staticmethod
    def _restart_app_now() -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _persisted_failure_message(self) -> str:
        """Return the installer's durable, actionable failure when available."""
        if self._status_path is None:
            return ""
        try:
            data = json.loads(self._status_path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        if not isinstance(data, dict) or data.get("ok") is not False:
            return ""
        return t(str(data.get("message") or "").strip())

    def _finish_without_process(self, code: int, message: str) -> None:
        self._exit_code = int(code)
        self._set_running(False)
        self._spinner.stop("×")
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
