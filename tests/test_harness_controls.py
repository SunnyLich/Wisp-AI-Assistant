"""Tests for the clickable Codex/Claude controls popup."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("language", "alternate", "send_alternate", "project_permission", "full_access"),
    [
        ("zh", "替代方案", "发送替代方案", "允许在项目内操作", "完全访问权限"),
        ("zh-Hant", "替代方案", "送出替代方案", "允許在專案內操作", "完整存取權"),
        ("es", "Opción alternativa", "Enviar opción alternativa", "Permitir dentro del proyecto", "Acceso completo"),
        ("fr", "Autre option", "Envoyer l’autre option", "Autoriser dans le projet", "Accès complet"),
    ],
)
def test_harness_permission_and_alternate_option_translations(
    language: str,
    alternate: str,
    send_alternate: str,
    project_permission: str,
    full_access: str,
) -> None:
    """The revised controls are present in every shipped Qt catalog."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QTranslator
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    translator = QTranslator()
    catalog = Path(__file__).parents[1] / "ui" / "locales" / "qt" / f"wisp_{language}.qm"
    assert translator.load(str(catalog))
    app.installTranslator(translator)
    try:
        assert QCoreApplication.translate("Wisp", "Alternate option") == alternate
        assert QCoreApplication.translate("Wisp", "Send alternate option") == send_alternate
        assert (
            QCoreApplication.translate("Wisp", "Allow within project")
            == project_permission
        )
        assert QCoreApplication.translate("Wisp", "Full access") == full_access
    finally:
        app.removeTranslator(translator)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_codex_controls_save_provider_specific_values(monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.harness_controls as controls

    app = QApplication.instance() or QApplication(sys.argv)
    writes = []
    reloads = []
    applied_while_visible = []
    monkeypatch.setattr(controls, "write_env_file", lambda path, values: writes.append((path, values)))
    monkeypatch.setattr(controls.config, "reload", lambda: reloads.append(True))
    dialog = controls.HarnessControlsDialog("codex")
    dialog.show()
    app.processEvents()
    dialog.applied.connect(lambda _keys: applied_while_visible.append(dialog.isVisible()))

    try:
        assert [dialog.approval.itemText(index) for index in range(dialog.approval.count())] == [
            "Require approval",
            "Allow within project",
            "Full access",
            "Plan only (read-only)",
        ]
        dialog.approval.setCurrentIndex(dialog.approval.findData("full_access"))
        assert dialog.approval_help.text() == (
            "Allow unrestricted access to files and the network without asking."
        )
        assert "#ff8a3d" in dialog.approval_help.styleSheet()
        selected_row = dialog.approval.currentIndex()
        dialog.approval.showPopup()
        assert dialog.approval.view().isRowHidden(selected_row)
        dialog.approval.hidePopup()
        assert not dialog.approval.view().isRowHidden(selected_row)
        dialog.model.setEditText("gpt-test")
        dialog.workspace.setText("C:/work/project")
        dialog.fast.setChecked(True)
        dialog.approval.setCurrentIndex(dialog.approval.findData("auto_edits"))
        dialog.reasoning.setCurrentIndex(dialog.reasoning.findData("detailed"))
        dialog._save()

        values = writes[0][1]
        assert values["WISP_CODEX_MODEL"] == "gpt-test"
        assert values["WISP_CODEX_WORKSPACE"] == "C:/work/project"
        assert values["WISP_CODEX_FAST_MODE"] == "true"
        assert values["WISP_CODEX_APPROVAL_MODE"] == "auto_edits"
        assert values["WISP_CODEX_REASONING_SUMMARY"] == "detailed"
        assert reloads == [True]
        assert applied_while_visible == [False]
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_harness_controls_choose_or_clear_workspace(monkeypatch, tmp_path) -> None:
    """Codex and Claude share the same explicit-project control."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.harness_controls as controls

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(
        controls.QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(tmp_path),
    )
    dialog = controls.HarnessControlsDialog("claude")

    try:
        dialog._choose_workspace()
        assert dialog.workspace.text() == str(tmp_path)
        dialog.workspace.clear()
        assert dialog.workspace.text() == ""
        assert dialog.workspace.placeholderText() == controls.t("Auto")
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_claude_controls_offer_full_model_ids() -> None:
    """Claude's picker exposes concrete versions instead of only family aliases."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.harness_controls import HarnessControlsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = HarnessControlsDialog("claude")

    try:
        model_values = {
            str(dialog.model.itemData(index) or "")
            for index in range(dialog.model.count())
        }
        assert {
            "claude-fable-5",
            "claude-sonnet-5",
            "claude-opus-4-8",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
        } <= model_values

        sonnet_5_index = dialog.model.findData("claude-sonnet-5")
        assert sonnet_5_index >= 0
        dialog.model.setCurrentIndex(sonnet_5_index)
        assert dialog.model.currentText() == "claude-sonnet-5"
        assert dialog._model_value() == "claude-sonnet-5"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    ("language", "labels"),
    [
        ("es", ("Bajo", "Medio", "Alto", "Guardar")),
        ("fr", ("Faible", "Moyen", "Élevé", "Enregistrer")),
        ("zh", ("低", "中", "高", "保存")),
        ("zh-Hant", ("低", "中", "高", "儲存")),
    ],
)
def test_harness_effort_and_save_labels_are_translated(language: str, labels: tuple[str, ...]) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialogButtonBox

    import config
    from ui import i18n
    from ui.harness_controls import HarnessControlsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = language
    i18n.set_language(language, app=app)
    dialog = HarnessControlsDialog("codex")

    try:
        effort_text = {dialog.effort.itemText(index) for index in range(dialog.effort.count())}
        assert set(labels[:3]).issubset(effort_text)
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons is not None
        assert buttons.button(QDialogButtonBox.StandardButton.Save).text() == labels[3]
    finally:
        dialog.deleteLater()
        config.APP_LANGUAGE = old_language
        i18n.set_language(old_language or None, app=app)
        app.processEvents()


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("es", ("Iniciando ChatGPT...", "Abriendo la conversación en ChatGPT...", "Preparando el turno de ChatGPT...", "El modelo está pensando...")),
        ("fr", ("Démarrage de ChatGPT...", "Ouverture de la conversation dans ChatGPT...", "Préparation du tour ChatGPT...", "Le modèle réfléchit...")),
        ("zh", ("正在启动 ChatGPT...", "正在 ChatGPT 中打开对话...", "正在准备 ChatGPT 回合...", "模型正在思考...")),
        ("zh-Hant", ("正在啟動 ChatGPT...", "正在 ChatGPT 中開啟對話...", "正在準備 ChatGPT 回合...", "模型正在思考...")),
    ],
)
def test_codex_live_phases_are_translated(language: str, expected: tuple[str, ...]) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = language
    i18n.set_language(language, app=app)

    try:
        sources = (
            "Starting ChatGPT...",
            "Opening conversation in ChatGPT...",
            "Preparing ChatGPT turn...",
            "Model is thinking...",
        )
        assert tuple(i18n.t(source) for source in sources) == expected
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(old_language or None, app=app)
        app.processEvents()
