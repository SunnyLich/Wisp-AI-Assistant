"""Real UI-to-runtime acceptance for the floating agent-provider controls."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import types
from copy import deepcopy
from pathlib import Path

import pytest
from dotenv import dotenv_values


pytestmark = [
    pytest.mark.workflow,
    pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed"),
]

_CONFIG_SUFFIXES = (
    "MODEL",
    "WORKSPACE",
    "FAST_MODE",
    "APPROVAL_MODE",
    "REASONING_EFFORT",
    "REASONING_SUMMARY",
)


def _provider_choices() -> list[pytest.ParameterSet]:
    """Return every value the production provider dialog offers."""

    from ui.harness_controls import _CLAUDE_MODELS, _CODEX_MODELS

    cases: list[pytest.ParameterSet] = []
    for provider, models in (("codex", _CODEX_MODELS), ("claude", _CLAUDE_MODELS)):
        choices: dict[str, tuple[object, ...]] = {
            "model": ("", *models),
            "workspace": ("auto", "explicit"),
            "fast": (False, True),
            "effort": (
                "",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
                *(("ultra",) if provider == "codex" else ()),
            ),
            "reasoning": (
                ("detailed", "concise", "none")
                if provider == "codex"
                else ("summarized", "none")
            ),
            "approval": ("ask", "auto_edits", "full_access", "read_only"),
        }
        for control, values in choices.items():
            for value in values:
                label = "default" if value == "" else str(value).lower().replace("_", "-")
                cases.append(pytest.param(provider, control, value, id=f"{provider}-{control}-{label}"))
    return cases


def _config_key(provider: str, control: str) -> str:
    suffix = {
        "model": "MODEL",
        "workspace": "WORKSPACE",
        "fast": "FAST_MODE",
        "effort": "REASONING_EFFORT",
        "reasoning": "REASONING_SUMMARY",
        "approval": "APPROVAL_MODE",
    }[control]
    return f"WISP_{'CLAUDE' if provider == 'claude' else 'CODEX'}_{suffix}"


def _prepare_isolated_provider_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
) -> tuple[object, object, Path]:
    """Point the real Save/reload path at an isolated .env file."""

    import config
    import ui.harness_controls as controls

    prefix = f"WISP_{'CLAUDE' if provider == 'claude' else 'CODEX'}"
    for suffix in _CONFIG_SUFFIXES:
        monkeypatch.delenv(f"{prefix}_{suffix}", raising=False)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(config, "_ENV_FILE", env_file)
    monkeypatch.setattr(config, "_LOADED_DOTENV_KEYS", set())
    monkeypatch.setattr(controls, "REPO_ROOT", tmp_path)
    return config, controls, env_file


@pytest.mark.parametrize(
    ("provider", "efforts", "summaries"),
    (
        (
            "codex",
            ("", "low", "medium", "high", "xhigh", "max", "ultra"),
            ("detailed", "concise", "none"),
        ),
        (
            "claude",
            ("", "low", "medium", "high", "xhigh", "max"),
            ("summarized", "none"),
        ),
    ),
)
def test_provider_dialog_exposes_exact_capability_matrix(
    provider: str,
    efforts: tuple[str, ...],
    summaries: tuple[str, ...],
) -> None:
    """The selected provider changes only the model/reasoning choices it supports."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.harness_controls import HarnessControlsDialog, _CLAUDE_MODELS, _CODEX_MODELS

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = HarnessControlsDialog(provider)
    try:
        assert tuple(dialog.model.itemData(index) or "" for index in range(dialog.model.count())) == (
            "",
            *(_CLAUDE_MODELS if provider == "claude" else _CODEX_MODELS),
        )
        assert tuple(dialog.effort.itemData(index) or "" for index in range(dialog.effort.count())) == efforts
        assert tuple(
            dialog.reasoning.itemData(index) or "" for index in range(dialog.reasoning.count())
        ) == summaries
        assert tuple(
            dialog.approval.itemData(index) or "" for index in range(dialog.approval.count())
        ) == ("ask", "auto_edits", "full_access", "read_only")
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.usefixtures("isolated_default_profile")
@pytest.mark.parametrize(("provider", "control", "value"), _provider_choices())
def test_every_provider_control_choice_persists_through_real_save(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    control: str,
    value: object,
) -> None:
    """Every offered control value survives the actual Save button and config reload."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton

    config, controls, env_file = _prepare_isolated_provider_env(monkeypatch, tmp_path, provider)
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = controls.HarnessControlsDialog(provider)
    applied: list[list[str]] = []
    visible_when_applied: list[bool] = []
    dialog.applied.connect(
        lambda keys: (applied.append(list(keys)), visible_when_applied.append(dialog.isVisible()))
    )
    dialog.show()
    app.processEvents()

    expected: object = value
    try:
        if control == "model":
            index = dialog.model.findData(str(value))
            assert index >= 0
            dialog.model.setCurrentIndex(index)
        elif control == "workspace":
            project = tmp_path / "selected-project"
            project.mkdir()
            buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
            if value == "explicit":
                monkeypatch.setattr(
                    controls.QFileDialog,
                    "getExistingDirectory",
                    lambda *_args, **_kwargs: str(project),
                )
                buttons[controls.t("Browse...")].click()
                expected = str(project)
            else:
                dialog.workspace.setText(str(project))
                buttons[controls.t("Auto")].click()
                expected = ""
        elif control == "fast":
            dialog.fast.setChecked(not bool(value))
            dialog.fast.click()
        elif control == "effort":
            index = dialog.effort.findData(str(value))
            assert index >= 0
            dialog.effort.setCurrentIndex(index)
        elif control == "reasoning":
            index = dialog.reasoning.findData(str(value))
            assert index >= 0
            dialog.reasoning.setCurrentIndex(index)
        elif control == "approval":
            index = dialog.approval.findData(str(value))
            assert index >= 0
            dialog.approval.setCurrentIndex(index)
        else:  # pragma: no cover - the case table is closed above
            raise AssertionError(control)

        box = dialog.findChild(QDialogButtonBox)
        assert box is not None
        box.button(QDialogButtonBox.StandardButton.Save).click()
        app.processEvents()

        key = _config_key(provider, control)
        stored = dotenv_values(env_file)
        serialized = "true" if expected is True else "false" if expected is False else str(expected)
        assert stored[key] == serialized
        live_value = getattr(config, key)
        assert live_value is bool(expected) if control == "fast" else live_value == expected
        expected_keys = sorted(
            f"WISP_{'CLAUDE' if provider == 'claude' else 'CODEX'}_{suffix}"
            for suffix in _CONFIG_SUFFIXES
        )
        assert applied == [expected_keys]
        assert visible_when_applied == [False]
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.usefixtures("isolated_default_profile")
@pytest.mark.parametrize("provider", ("codex", "claude"))
def test_floating_provider_badge_opens_and_saves_real_controls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
) -> None:
    """The visible provider badge reaches the same dialog Save/reload/signal path."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialogButtonBox

    config, _controls, env_file = _prepare_isolated_provider_env(monkeypatch, tmp_path, provider)
    from ui.overlay import IconOverlay, OverlaySignals

    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(IconOverlay, "_pin_overlay_windows", lambda _self: None)
    monkeypatch.setattr(config, "CHAT_EXECUTION_MODE", provider)
    signals = OverlaySignals()
    applied: list[dict[str, object]] = []
    signals.settings_applied.connect(applied.append)
    overlay = IconOverlay(signals)
    try:
        overlay.show()
        overlay._icon_label.show()
        overlay._refresh_provider_badge()
        app.processEvents()
        assert overlay._provider_badge.isVisible()
        overlay._provider_badge.click()
        for _ in range(4):
            app.processEvents()
        dialog = overlay._harness_controls_dialog
        assert dialog.isVisible()
        assert dialog.provider == provider
        model = "claude-sonnet-5" if provider == "claude" else "gpt-5.6-sol"
        dialog.model.setCurrentIndex(dialog.model.findData(model))
        box = dialog.findChild(QDialogButtonBox)
        assert box is not None
        box.button(QDialogButtonBox.StandardButton.Save).click()
        app.processEvents()

        key = _config_key(provider, "model")
        assert dotenv_values(env_file)[key] == model
        assert getattr(config, key) == model
        assert applied == [{
            "changed_keys": sorted(
                f"WISP_{'CLAUDE' if provider == 'claude' else 'CODEX'}_{suffix}"
                for suffix in _CONFIG_SUFFIXES
            ),
            "source": "harness_controls",
        }]
    finally:
        if hasattr(overlay, "_harness_controls_dialog"):
            overlay._harness_controls_dialog.close()
        overlay._bubble.clear()
        overlay._provider_badge.close()
        overlay._icon_label.close()
        overlay.close()
        app.processEvents()


def _codex_runtime_choices() -> list[pytest.ParameterSet]:
    from ui.harness_controls import _CODEX_MODELS

    values = {
        "model": ("", *_CODEX_MODELS),
        "fast": (False, True),
        "effort": ("", "low", "medium", "high", "xhigh", "max", "ultra"),
        "reasoning": ("detailed", "concise", "none"),
        "approval": ("ask", "auto_edits", "full_access", "read_only"),
    }
    return [
        pytest.param(control, value, id=f"{control}-{'default' if value == '' else value}")
        for control, choices in values.items()
        for value in choices
    ]


@pytest.mark.parametrize(("control", "value"), _codex_runtime_choices())
def test_every_codex_control_choice_reaches_real_turn_request(
    tmp_path: Path,
    control: str,
    value: object,
) -> None:
    """Every ChatGPT control state maps to the production turn/start payload."""

    from core.harness_clients import codex

    calls: list[tuple[str, dict[str, object]]] = []

    class Client:
        on_event = None

        def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
            calls.append((method, deepcopy(params)))
            return {"turn": {"id": "turn-acceptance"}}

    options: dict[str, object] = {
        "model": "",
        "fast_mode": False,
        "reasoning_effort": "high",
        "reasoning_summary": "detailed",
        "approval_mode": "ask",
    }
    options[
        {
            "model": "model",
            "fast": "fast_mode",
            "effort": "reasoning_effort",
            "reasoning": "reasoning_summary",
            "approval": "approval_mode",
        }[control]
    ] = value
    codex._start_turn(Client(), "thread-1", "hello", tmp_path, **options)
    params = calls[0][1]

    if control == "model":
        assert params.get("model") == value if value else "model" not in params
    elif control == "fast":
        assert (params.get("serviceTier") == "priority") is bool(value)
    elif control == "effort":
        assert params.get("effort") == value if value else "effort" not in params
    elif control == "reasoning":
        assert params["summary"] == value
    else:
        expected = {
            "ask": (codex._APPROVAL_POLICIES[0], "workspaceWrite", True),
            "auto_edits": ("never", "workspaceWrite", False),
            "full_access": ("never", "dangerFullAccess", False),
            "read_only": ("never", "readOnly", False),
        }[str(value)]
        assert params["approvalPolicy"] == expected[0]
        assert params["sandboxPolicy"]["type"] == expected[1]
        assert ("approvalsReviewer" in params) is expected[2]


def _claude_runtime_choices() -> list[pytest.ParameterSet]:
    from ui.harness_controls import _CLAUDE_MODELS

    values = {
        "model": ("", *_CLAUDE_MODELS),
        "fast": (False, True),
        "effort": ("", "low", "medium", "high", "xhigh", "max"),
        "reasoning": ("summarized", "none"),
        "approval": ("ask", "auto_edits", "full_access", "read_only"),
    }
    return [
        pytest.param(control, value, id=f"{control}-{'default' if value == '' else value}")
        for control, choices in values.items()
        for value in choices
    ]


@pytest.mark.parametrize(("control", "value"), _claude_runtime_choices())
def test_every_claude_control_choice_reaches_real_sdk_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    control: str,
    value: object,
) -> None:
    """Every Claude control state maps to the production SDK options object."""

    import config
    from core.harness_clients import claude

    captured: list[dict[str, object]] = []

    class ClaudeAgentOptions:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            captured.append(kwargs)

    class PermissionResultAllow:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class PermissionResultDeny(PermissionResultAllow):
        pass

    async def query(**_kwargs: object):
        yield type(
            "ResultMessage",
            (),
            {"is_error": False, "result": "done", "session_id": "claude-acceptance"},
        )()

    sdk = types.ModuleType("claude_agent_sdk")
    sdk.ClaudeAgentOptions = ClaudeAgentOptions
    sdk.query = query
    sdk_types = types.ModuleType("claude_agent_sdk.types")
    sdk_types.PermissionResultAllow = PermissionResultAllow
    sdk_types.PermissionResultDeny = PermissionResultDeny
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk.types", sdk_types)
    monkeypatch.setattr(claude, "_claude_executable", lambda **_kwargs: "")

    settings: dict[str, object] = {
        "WISP_CLAUDE_MODEL": "",
        "WISP_CLAUDE_FAST_MODE": False,
        "WISP_CLAUDE_REASONING_EFFORT": "high",
        "WISP_CLAUDE_REASONING_SUMMARY": "summarized",
        "WISP_CLAUDE_APPROVAL_MODE": "ask",
        "WISP_CLAUDE_SYSTEM_PROMPT": "",
    }
    settings[
        {
            "model": "WISP_CLAUDE_MODEL",
            "fast": "WISP_CLAUDE_FAST_MODE",
            "effort": "WISP_CLAUDE_REASONING_EFFORT",
            "reasoning": "WISP_CLAUDE_REASONING_SUMMARY",
            "approval": "WISP_CLAUDE_APPROVAL_MODE",
        }[control]
    ] = value
    for key, configured in settings.items():
        monkeypatch.setattr(config, key, configured, raising=False)

    result = asyncio.run(
        claude._run_async(
            "hello",
            session_id="",
            cwd=tmp_path,
            on_event=None,
            approval_callback=lambda _request: True,
        )
    )
    assert result.text == "done"
    options = captured[0]
    if control == "model":
        assert options.get("model") == value if value else "model" not in options
    elif control == "fast":
        assert json.loads(str(options["settings"])) == {"fastMode": value}
    elif control == "effort":
        assert options.get("effort") == value if value else "effort" not in options
    elif control == "reasoning":
        expected = (
            {"type": "disabled"}
            if value == "none"
            else {"type": "adaptive", "display": "summarized"}
        )
        assert options["thinking"] == expected
    else:
        assert options["permission_mode"] == {
            "ask": "default",
            "auto_edits": "acceptEdits",
            "full_access": "bypassPermissions",
            "read_only": "plan",
        }[str(value)]


@pytest.mark.parametrize("provider", ("codex", "claude"))
@pytest.mark.parametrize("workspace_state", ("auto", "valid", "missing"))
def test_provider_workspace_selection_runtime_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    workspace_state: str,
) -> None:
    """Only a valid explicit project overrides the automatic conversation workspace."""

    import config
    from runtime.supervisor.flows import _configured_harness_workspace

    selected = tmp_path / "selected"
    selected.mkdir()
    raw = {"auto": "", "valid": str(selected), "missing": str(tmp_path / "missing")}[workspace_state]
    key = "WISP_CLAUDE_WORKSPACE" if provider == "claude" else "WISP_CODEX_WORKSPACE"
    monkeypatch.setattr(config, key, raw, raising=False)

    result = _configured_harness_workspace(provider)
    assert result == (str(selected.resolve()) if workspace_state == "valid" else "")


@pytest.mark.parametrize("provider", ("codex", "claude"))
@pytest.mark.parametrize("source", ("provider_session", "file_context", "attachment", "fallback"))
def test_automatic_provider_workspace_source_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    source: str,
) -> None:
    """Automatic mode resolves every production conversation workspace source."""

    import config
    from runtime.workers.ui_host import QtProtocolHost

    codex_project = tmp_path / "codex-project"
    claude_project = tmp_path / "claude-project"
    context_project = tmp_path / "context-project"
    attachment_project = tmp_path / "attachment-project"
    for directory in (codex_project, claude_project, context_project, attachment_project):
        directory.mkdir()
    context_file = context_project / "context.txt"
    attachment_file = attachment_project / "attachment.txt"
    context_file.write_text("context", encoding="utf-8")
    attachment_file.write_text("attachment", encoding="utf-8")
    monkeypatch.setattr(config, "CHAT_EXECUTION_MODE", provider, raising=False)

    conversation: dict[str, object] = {}
    if source == "provider_session":
        conversation["harness_sessions"] = {
            "codex": {"cwd": str(codex_project)},
            "claude": {"cwd": str(claude_project)},
        }
        expected = codex_project if provider == "codex" else claude_project
    elif source == "file_context":
        conversation["file_context"] = [{"path": str(context_file)}]
        expected = context_project
    elif source == "attachment":
        conversation["messages"] = [{
            "role": "user",
            "content": "inspect",
            "attachments": [{"path": str(attachment_file), "source": "external_path"}],
        }]
        expected = attachment_project
    else:
        expected = Path.cwd()

    assert QtProtocolHost._conversation_harness_cwd(conversation) == str(expected.resolve())
