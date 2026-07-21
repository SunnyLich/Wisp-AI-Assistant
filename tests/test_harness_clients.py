"""Contract tests for Wisp's live Codex and Claude harness bridge."""
from __future__ import annotations

import asyncio
import io
import os
import sys
import types
from copy import deepcopy
from pathlib import Path

import pytest

from core.conversation_store.store import _clean_conversation
from core.harness_clients import codex
from core.harness_clients.base import HarnessEvent, HarnessResult, normalized_cwd
from core.harness_clients.claude import _run_async
from core.harness_clients.codex import CodexAppServerError, _Client, _start_turn


def _bare_codex_client(events: list[HarnessEvent], approval=True) -> _Client:
    client = object.__new__(_Client)
    client.on_event = events.append
    client.approval_callback = lambda _request: approval
    client._items = {}
    client._reply_parts = []
    client._attachments = []
    client._model_thinking_announced = False
    client.sent = []
    client.send = client.sent.append
    return client


def test_normalized_cwd_uses_parent_for_file(tmp_path: Path) -> None:
    file_path = tmp_path / "context.txt"
    file_path.write_text("context", encoding="utf-8")

    assert normalized_cwd(file_path) == tmp_path.resolve()


def test_codex_environment_isolates_state_without_mutating_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    personal_home = tmp_path / "personal-codex"
    wisp_home = tmp_path / "wisp-codex"
    monkeypatch.setenv("CODEX_HOME", str(personal_home))
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(personal_home / "sqlite"))
    monkeypatch.setenv("WISP_CODEX_HOME", str(wisp_home))

    environment = codex._codex_environment()

    assert environment["CODEX_HOME"] == str(wisp_home.resolve())
    assert environment["CODEX_SQLITE_HOME"] == str(wisp_home.resolve())
    assert os.environ["CODEX_HOME"] == str(personal_home)
    assert os.environ["CODEX_SQLITE_HOME"] == str(personal_home / "sqlite")
    assert (wisp_home / "config.toml").read_text(encoding="utf-8") == 'history.persistence = "none"\n'


def test_codex_app_server_receives_isolated_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launched: dict[str, object] = {}

    class Process:
        stdin = io.StringIO()
        stdout = io.StringIO()
        stderr = None

        def poll(self):
            return 0

    monkeypatch.setattr(codex, "_codex_executable", lambda: "codex-test")
    monkeypatch.setattr(codex, "_codex_environment", lambda: {"CODEX_HOME": "wisp-codex"})
    monkeypatch.setattr(
        codex.subprocess,
        "Popen",
        lambda command, **kwargs: launched.update(command=command, **kwargs) or Process(),
    )

    client = _Client(tmp_path, None, None)
    client.close()

    assert launched["command"] == ["codex-test", "app-server", "--listen", "stdio://"]
    assert launched["env"] == {"CODEX_HOME": "wisp-codex"}


def test_codex_streams_reasoning_and_reply_separately() -> None:
    events: list[HarnessEvent] = []
    client = _bare_codex_client(events)

    client.handle({"method": "item/reasoning/summaryTextDelta", "params": {"delta": "Checking files"}})
    client.handle({"method": "item/agentMessage/delta", "params": {"delta": "Fixed it."}})

    assert events == [
        HarnessEvent(kind="thought", text="Checking files"),
        HarnessEvent(kind="reply", text="Fixed it."),
    ]
    assert client._reply_parts == ["Fixed it."]


def test_codex_announces_model_thinking_when_the_user_message_starts() -> None:
    events: list[HarnessEvent] = []
    client = _bare_codex_client(events)

    client.handle({
        "method": "item/started",
        "params": {"item": {"id": "user-1", "type": "userMessage"}},
    })

    assert events == [HarnessEvent(kind="status", text="Model is thinking...")]
    assert client._model_thinking_announced is True


@pytest.mark.parametrize(("approved", "decision"), [(True, "accept"), (False, "decline")])
def test_codex_forwards_command_approval(approved: bool, decision: str) -> None:
    client = _bare_codex_client([], approval=approved)
    client._items["cmd-1"] = {"id": "cmd-1", "type": "commandExecution", "command": "pytest", "cwd": "/repo"}

    client.handle({
        "id": 17,
        "method": "item/commandExecution/requestApproval",
        "params": {"itemId": "cmd-1", "reason": "Run tests"},
    })

    assert client.sent == [{"id": 17, "result": {"decision": decision}}]


def test_codex_uses_completed_agent_message_when_no_delta_arrived() -> None:
    events: list[HarnessEvent] = []
    client = _bare_codex_client(events)

    client.handle({
        "method": "item/completed",
        "params": {"item": {"id": "answer", "type": "agentMessage", "text": "Complete answer"}},
    })

    assert client._reply_parts == ["Complete answer"]
    assert events == [HarnessEvent(kind="reply", text="Complete answer")]


def test_codex_preserves_completed_image_generation_as_an_attachment(tmp_path: Path) -> None:
    """An image-only Codex turn must not disappear when it has no agent text."""
    events: list[HarnessEvent] = []
    client = _bare_codex_client(events)
    image_path = tmp_path / "generated.png"
    image_path.write_bytes(b"generated-image")

    client.handle({
        "method": "item/completed",
        "params": {
            "item": {
                "id": "image-1",
                "type": "imageGeneration",
                "status": "completed",
                "result": "data:image/png;base64,ignored-when-saved-path-exists",
                "savedPath": str(image_path),
                "revisedPrompt": "A small test image",
            }
        },
    })

    expected = {
        "kind": "image",
        "source": "codex_image_generation",
        "path": str(image_path),
        "name": "generated.png",
        "revised_prompt": "A small test image",
    }
    assert client._attachments == [expected]
    assert events == [
        HarnessEvent(kind="image", text="Image generated.", attachment=expected)
    ]
    assert client._reply_parts == []


def test_codex_preserves_inline_image_when_saved_path_is_unavailable() -> None:
    """The protocol's optional savedPath does not make inline image output vanish."""
    events: list[HarnessEvent] = []
    client = _bare_codex_client(events)
    data_url = "data:image/png;base64,aW1hZ2U="

    client.handle({
        "method": "item/completed",
        "params": {
            "item": {
                "id": "image-inline",
                "type": "imageGeneration",
                "status": "completed",
                "result": data_url,
                "savedPath": None,
            }
        },
    })

    expected = {
        "kind": "image",
        "source": "codex_image_generation",
        "data_url": data_url,
        "name": "generated-image.png",
    }
    assert client._attachments == [expected]
    assert events == [
        HarnessEvent(kind="image", text="Image generated.", attachment=expected)
    ]


def test_codex_turn_uses_installed_release_approval_policy(tmp_path: Path) -> None:
    """Released Codex builds accept on-request rather than unlessTrusted."""
    calls = []

    class Client:
        on_event = None

        def request(self, method, params):
            calls.append((method, deepcopy(params)))
            return {"turn": {"id": "turn-1"}}

    result = _start_turn(Client(), "thread-1", "hello", tmp_path)

    assert result == {"turn": {"id": "turn-1"}}
    assert calls[0][0] == "turn/start"
    assert calls[0][1]["approvalPolicy"] == "on-request"
    assert calls[0][1]["approvalsReviewer"] == "user"
    assert calls[0][1]["effort"] == "high"
    assert calls[0][1]["summary"] == "detailed"


def test_codex_turn_falls_back_for_newer_approval_enum(tmp_path: Path) -> None:
    """Future builds using unlessTrusted still work without breaking released builds."""
    calls = []
    events: list[HarnessEvent] = []

    class Client:
        on_event = events.append

        def request(self, method, params):
            calls.append((method, deepcopy(params)))
            if len(calls) == 1:
                raise CodexAppServerError(
                    "Invalid params: unknown variant 'on-request'; expected 'unlessTrusted'"
                )
            return {"turn": {"id": "turn-2"}}

    result = _start_turn(Client(), "thread-1", "hello", tmp_path)

    assert result == {"turn": {"id": "turn-2"}}
    assert [params["approvalPolicy"] for _method, params in calls] == [
        "on-request",
        "unlessTrusted",
    ]
    assert events == [
        HarnessEvent(kind="progress", text="Retrying with this ChatGPT version's approval policy…")
    ]


def test_codex_turn_falls_back_when_reasoning_controls_are_unsupported(tmp_path: Path) -> None:
    calls = []
    events: list[HarnessEvent] = []

    class Client:
        on_event = events.append

        def request(self, method, params):
            calls.append((method, deepcopy(params)))
            if len(calls) == 1:
                raise CodexAppServerError("Invalid params: unknown field `summary`")
            return {"turn": {"id": "turn-3"}}

    result = _start_turn(Client(), "thread-1", "hello", tmp_path)

    assert result == {"turn": {"id": "turn-3"}}
    assert calls[0][1]["summary"] == "detailed"
    assert "summary" not in calls[1][1]
    assert "effort" not in calls[1][1]
    assert events == [
        HarnessEvent(
            kind="progress",
            text="This ChatGPT version does not stream reasoning summaries.",
        )
    ]


def test_codex_turn_preserves_provider_default_effort(tmp_path: Path) -> None:
    calls = []

    class Client:
        on_event = None

        def request(self, method, params):
            calls.append((method, deepcopy(params)))
            return {"turn": {"id": "turn-default"}}

    _start_turn(Client(), "thread-1", "hello", tmp_path, reasoning_effort="")

    assert "effort" not in calls[0][1]
    assert calls[0][1]["summary"] == "detailed"


def test_codex_turn_applies_model_fast_and_read_only_controls(tmp_path: Path) -> None:
    calls = []

    class Client:
        on_event = None

        def request(self, method, params):
            calls.append((method, deepcopy(params)))
            return {"turn": {"id": "controlled-turn"}}

    _start_turn(
        Client(),
        "thread-1",
        "hello",
        tmp_path,
        model="gpt-5.6-sol",
        fast_mode=True,
        approval_mode="read_only",
    )

    params = calls[0][1]
    assert params["model"] == "gpt-5.6-sol"
    assert params["serviceTier"] == "priority"
    assert params["approvalPolicy"] == "never"
    assert params["sandboxPolicy"] == {"type": "readOnly"}
    assert "approvalsReviewer" not in params


def test_codex_turn_applies_full_access_controls(tmp_path: Path) -> None:
    calls = []

    class Client:
        on_event = None

        def request(self, method, params):
            calls.append((method, deepcopy(params)))
            return {"turn": {"id": "full-access-turn"}}

    _start_turn(
        Client(),
        "thread-1",
        "hello",
        tmp_path,
        approval_mode="full_access",
    )

    params = calls[0][1]
    assert params["approvalPolicy"] == "never"
    assert params["sandboxPolicy"] == {"type": "dangerFullAccess"}
    assert "approvalsReviewer" not in params


def test_claude_permission_modes_match_provider_controls() -> None:
    from core.harness_clients.claude import _permission_mode

    assert _permission_mode("ask") == "default"
    assert _permission_mode("auto_edits") == "acceptEdits"
    assert _permission_mode("full_access") == "bypassPermissions"
    assert _permission_mode("read_only") == "plan"


def test_codex_prewarm_and_repeated_turns_reuse_one_app_server(monkeypatch, tmp_path: Path) -> None:
    instances = []

    class Process:
        closed = False

        def poll(self):
            return 0 if self.closed else None

    class Client:
        def __init__(self, cwd, on_event, approval_callback):
            self.cwd = cwd
            self.on_event = on_event
            self.approval_callback = approval_callback
            self.process = Process()
            self.backend = "codex-test"
            self._items = {}
            self._reply_parts = []
            self.requests = []
            self.sent = []
            instances.append(self)

        def request(self, method, params):
            self.requests.append((method, deepcopy(params)))
            if method == "initialize":
                return {}
            if method == "thread/start":
                return {"thread": {"id": f"thread-{len(self.requests)}"}}
            if method == "thread/resume":
                return {"thread": {"id": params["threadId"]}}
            if method == "turn/start":
                return {"turn": {"id": "turn"}}
            raise AssertionError(method)

        def send(self, message):
            self.sent.append(message)

        def read(self):
            return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

        def handle(self, _message):
            return "completed"

        def close(self):
            self.process.closed = True

    monkeypatch.setattr(codex, "_Client", Client)
    monkeypatch.setattr(codex, "_PERSISTENT_CLIENT", None)
    monkeypatch.setattr("config.WISP_CODEX_SYSTEM_PROMPT", "ChatGPT-only rules.", raising=False)

    assert codex.prewarm_codex(tmp_path) == {
        "ready": True,
        "cached": False,
        "backend": "codex-test",
    }
    first_events: list[HarnessEvent] = []
    second_events: list[HarnessEvent] = []
    first = codex.run_codex("first", cwd=tmp_path, on_event=first_events.append)
    codex.run_codex(
        "second",
        session_id=first.session_id,
        cwd=tmp_path,
        on_event=second_events.append,
    )

    assert len(instances) == 1
    methods = [method for method, _params in instances[0].requests]
    assert methods.count("initialize") == 1
    assert methods.count("thread/start") == 1
    assert methods.count("thread/resume") == 1
    assert methods.count("turn/start") == 2
    expected_statuses = [
        HarnessEvent("status", "Opening conversation in ChatGPT..."),
        HarnessEvent("status", "Preparing ChatGPT turn..."),
        HarnessEvent("status", "Model is thinking..."),
    ]
    assert first_events == expected_statuses
    assert second_events == expected_statuses
    starts = [params for method, params in instances[0].requests if method == "thread/start"]
    assert starts == [{"cwd": str(tmp_path), "developerInstructions": "ChatGPT-only rules."}]
    resumes = [params for method, params in instances[0].requests if method == "thread/resume"]
    assert resumes == [{
        "threadId": first.session_id,
        "developerInstructions": "ChatGPT-only rules.",
    }]
    assert instances[0].process.closed is False

    codex.close_persistent_codex()
    assert instances[0].process.closed is True


def test_harness_result_keeps_provider_session_and_workspace(tmp_path: Path) -> None:
    result = HarnessResult(provider="claude", text="done", session_id="session-1", cwd=str(tmp_path))

    assert result.session_id == "session-1"
    assert result.provider == "claude"


def test_claude_streams_thinking_and_reply_without_repeating_final_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    received_prompts = []
    received_options = []

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            received_options.append(kwargs)

    class PermissionResultAllow:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class PermissionResultDeny:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def message(class_name: str, **attrs):
        return type(class_name, (), attrs)()

    async def query(**kwargs):
        assert not isinstance(kwargs["prompt"], str)
        received_prompts.extend([item async for item in kwargs["prompt"]])
        yield message("SystemMessage", subtype="init", data={"session_id": "claude-session"})
        yield message(
            "StreamEvent",
            event={
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "id": "tool-1", "name": "Read"},
            },
        )
        yield message(
            "StreamEvent",
            event={"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "Inspecting"}},
        )
        yield message(
            "StreamEvent",
            event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Finished"}},
        )
        yield message(
            "AssistantMessage",
            content=[
                message("ToolUseBlock", id="tool-1", name="Read", input={"file_path": "notes.txt"}),
                message("ThinkingBlock", thinking="Inspecting"),
                message("TextBlock", text="Finished"),
            ],
        )
        yield message(
            "ResultMessage",
            is_error=False,
            result="Finished",
            session_id="claude-session",
        )

    sdk = types.ModuleType("claude_agent_sdk")
    sdk.ClaudeAgentOptions = ClaudeAgentOptions
    sdk.query = query
    sdk_types = types.ModuleType("claude_agent_sdk.types")
    sdk_types.PermissionResultAllow = PermissionResultAllow
    sdk_types.PermissionResultDeny = PermissionResultDeny
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk.types", sdk_types)
    monkeypatch.setattr("config.WISP_CLAUDE_MODEL", "claude-sonnet-5", raising=False)
    monkeypatch.setattr("config.WISP_CLAUDE_SYSTEM_PROMPT", "Claude-only rules.", raising=False)
    monkeypatch.setattr("config.WISP_CLAUDE_APPROVAL_MODE", "ask", raising=False)

    events: list[HarnessEvent] = []
    result = asyncio.run(_run_async(
        "hello",
        session_id="",
        cwd=tmp_path,
        on_event=events.append,
        approval_callback=lambda _request: True,
    ))

    assert events == [
        HarnessEvent(kind="progress", text="Claude started Read"),
        HarnessEvent(kind="thought", text="Inspecting"),
        HarnessEvent(kind="reply", text="Finished"),
        HarnessEvent(kind="progress", text="Claude action: notes.txt"),
    ]
    assert result.text == "Finished"
    assert result.session_id == "claude-session"
    assert result.backend.startswith("claude-agent-sdk")
    assert received_prompts == [{
        "type": "user",
        "message": {"role": "user", "content": "hello"},
        "parent_tool_use_id": None,
    }]
    assert received_options[0]["include_hook_events"] is True
    assert received_options[0]["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert received_options[0]["permission_mode"] == "default"
    assert received_options[0]["model"] == "claude-sonnet-5"
    assert received_options[0]["system_prompt"] == "Claude-only rules."


def test_conversation_persists_independent_harness_sessions(tmp_path: Path) -> None:
    cleaned = _clean_conversation({
        "messages": [{"role": "user", "content": "hello"}],
        "harness_sessions": {
            "codex": {"session_id": "thread-1", "cwd": str(tmp_path)},
            "claude": {"session_id": "session-2", "cwd": str(tmp_path)},
            "unknown": {"session_id": "ignored"},
        },
    })

    assert set(cleaned["harness_sessions"]) == {"codex", "claude"}
    assert cleaned["harness_sessions"]["codex"]["provider"] == "codex"
