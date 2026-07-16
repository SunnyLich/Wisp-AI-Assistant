from __future__ import annotations

import os
import sys

import pytest


@pytest.mark.skipif(
    pytest.importorskip("PySide6", reason="PySide6 not installed") is None,
    reason="PySide6 not installed",
)
def test_review_sheet_offers_full_redacted_or_cancel(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QTextEdit

    from runtime.workers.ui_host import QtProtocolHost

    app = QApplication.instance() or QApplication(sys.argv)
    captured: dict[str, object] = {}

    def choose_full(dialog: QDialog) -> int:
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        captured["buttons"] = set(buttons)
        captured["size"] = (dialog.width(), dialog.height())
        captured["labels"] = [label.text() for label in dialog.findChildren(QLabel)]
        summary = dialog.findChild(QTextEdit, "privacyReviewSummary")
        preview = dialog.findChild(QTextEdit, "privacyReviewPreview")
        captured["summary"] = summary.toPlainText() if summary is not None else ""
        captured["preview"] = preview.toPlainText() if preview is not None else ""
        buttons["Send full message"].click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", choose_full)

    result = QtProtocolHost._privacy_review_request(
        object(),
        approval_id="privacy-ui",
        items=[
            {
                "category": "person",
                "source": "prompt",
                "preview": f"Person {index}",
                "replacement": f"[PERSON_{index}]",
            }
            for index in range(1, 26)
        ],
        scrubbed_preview=f"{'x' * 20_000} [PERSON_25]",
        count=25,
        ai_enabled=True,
    )

    assert {"Send redacted", "Send full message", "Cancel send"} <= captured["buttons"]
    assert captured["size"] == (760, 620)
    assert "Detection: advanced local AI model and built-in patterns" in captured["labels"]
    assert "Redacted request that the model will receive:" in captured["labels"]
    assert "[PERSON_1]" in captured["summary"]
    assert "[PERSON_25]" in captured["summary"]
    assert len(captured["preview"]) > 20_000
    assert captured["preview"].endswith("[PERSON_25]")
    assert all("cloud model" not in label.lower() for label in captured["labels"])
    assert result == {"approval_id": "privacy-ui", "approved": True, "decision": "full"}
    app.processEvents()


@pytest.mark.skipif(
    pytest.importorskip("PySide6", reason="PySide6 not installed") is None,
    reason="PySide6 not installed",
)
def test_privacy_report_uses_large_resizable_window(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from types import SimpleNamespace

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QTextEdit

    from runtime.workers.ui_host import QtProtocolHost

    app = QApplication.instance() or QApplication(sys.argv)
    captured: dict[str, object] = {}

    monkeypatch.setattr(QTimer, "singleShot", lambda _delay, callback: callback())

    def capture_open(dialog: QDialog) -> None:
        captured["minimum"] = (dialog.minimumWidth(), dialog.minimumHeight())
        captured["size"] = (dialog.width(), dialog.height())
        captured["labels"] = [label.text() for label in dialog.findChildren(QLabel)]
        details = dialog.findChild(QTextEdit, "privacyReportDetails")
        captured["details"] = details.toPlainText() if details is not None else ""

    monkeypatch.setattr(QDialog, "open", capture_open)
    host = SimpleNamespace(_status_dialogs=[])

    result = QtProtocolHost._privacy_report(
        host,
        report={
            "count": 1,
            "items": [
                {
                    "category": "person",
                    "source": "prompt",
                    "preview": "Jo...ga",
                }
            ],
        },
    )

    assert captured["minimum"] == (720, 480)
    assert captured["size"][0] >= 720
    assert captured["size"][1] >= 480
    assert "Privacy redaction report" in captured["labels"]
    assert "1 item(s) detected and censored." in captured["labels"]
    assert "Person - Prompt:" in captured["details"]
    assert len(host._status_dialogs) == 1
    assert result == {"queued": True, "count": 1}
    app.processEvents()
