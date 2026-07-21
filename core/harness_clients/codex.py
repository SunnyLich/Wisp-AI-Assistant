"""Codex app-server JSONL adapter."""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any, TextIO

from core.harness_clients.base import (
    ApprovalCallback,
    EventCallback,
    HarnessEvent,
    HarnessResult,
    approval_allowed,
    emit,
    normalized_cwd,
)


class CodexAppServerError(RuntimeError):
    """Raised when the local Codex app-server cannot complete a turn."""


_APPROVAL_POLICIES = ("on-request", "unlessTrusted")
_REASONING_SUMMARY = "detailed"
_PERSISTENT_CLIENT: _Client | None = None
_PERSISTENT_CLIENT_LOCK = threading.RLock()
_ISOLATED_CONFIG = 'history.persistence = "none"\n'


def _codex_executable() -> str:
    configured = os.getenv("WISP_CODEX_CLI", "").strip()
    executable = configured or shutil.which("codex") or ""
    if not executable:
        raise CodexAppServerError(
            "ChatGPT is unavailable. Install the Codex CLI or set WISP_CODEX_CLI to its executable."
        )
    return executable


def _isolated_codex_home() -> Path:
    """Return Wisp's private Codex state root, creating it when needed."""
    configured = os.getenv("WISP_CODEX_HOME", "").strip()
    if configured:
        home = Path(configured).expanduser()
    else:
        from core.system.paths import USER_DATA_DIR

        home = USER_DATA_DIR / "codex"
    home = home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    config_path = home / "config.toml"
    if not config_path.exists():
        config_path.write_text(_ISOLATED_CONFIG, encoding="utf-8")
    return home


def _codex_environment_overrides() -> dict[str, str]:
    """Return state-location overrides shared by every Wisp Codex command."""
    home = str(_isolated_codex_home())
    return {
        "CODEX_HOME": home,
        "CODEX_SQLITE_HOME": home,
    }


def _codex_environment() -> dict[str, str]:
    """Build a child environment without changing Wisp or the user's shell."""
    environment = os.environ.copy()
    environment.update(_codex_environment_overrides())
    return environment


def _approval_request(params: dict[str, Any], method: str, item: dict[str, Any]) -> dict[str, Any]:
    reason = str(params.get("reason") or "ChatGPT needs permission to continue.")
    if method == "item/commandExecution/requestApproval":
        command = params.get("command") or item.get("command") or "command"
        return {
            "action": "run command",
            "path": str(params.get("cwd") or item.get("cwd") or ""),
            "details": {"command": command, "reason": reason},
            "diff": str(command),
        }
    changes = item.get("changes") if isinstance(item.get("changes"), list) else []
    paths = [str(change.get("path") or "") for change in changes if isinstance(change, dict)]
    diffs = [str(change.get("diff") or "") for change in changes if isinstance(change, dict)]
    return {
        "action": "apply ChatGPT file changes",
        "path": ", ".join(path for path in paths if path),
        "details": {"reason": reason},
        "diff": "\n".join(diff for diff in diffs if diff),
    }


class _Client:
    def __init__(
        self,
        cwd: Path,
        on_event: EventCallback | None,
        approval_callback: ApprovalCallback | None,
    ) -> None:
        self.cwd = cwd
        self.on_event = on_event
        self.approval_callback = approval_callback
        self._next_id = 1
        self._items: dict[str, dict[str, Any]] = {}
        self._reply_parts: list[str] = []
        self._attachments: list[dict[str, Any]] = []
        self._model_thinking_announced = False
        self._stderr: deque[str] = deque(maxlen=30)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        executable = _codex_executable()
        try:
            self.process = subprocess.Popen(
                [executable, "app-server", "--listen", "stdio://"],
                cwd=str(cwd),
                env=_codex_environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise CodexAppServerError(f"Could not start ChatGPT's local agent: {exc}") from exc
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.stdin: TextIO = self.process.stdin
        self.stdout: TextIO = self.process.stdout
        self.backend = str(Path(executable).resolve())
        if self.process.stderr is not None:
            threading.Thread(target=self._drain_stderr, args=(self.process.stderr,), daemon=True).start()

    def _drain_stderr(self, stream: TextIO) -> None:
        for line in stream:
            value = line.strip()
            if value:
                self._stderr.append(value)

    def close(self) -> None:
        try:
            self.stdin.close()
        except OSError:
            pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def send(self, message: dict[str, Any]) -> None:
        self.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self.send({"method": method, "id": request_id, "params": params})
        while True:
            message = self.read()
            if message.get("id") == request_id:
                if message.get("error"):
                    error = message.get("error") or {}
                    raise CodexAppServerError(str(error.get("message") or error))
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            self.handle(message)

    def read(self) -> dict[str, Any]:
        line = self.stdout.readline()
        if not line:
            detail = "; ".join(self._stderr)
            suffix = f": {detail}" if detail else ""
            raise CodexAppServerError(f"ChatGPT's local agent closed unexpectedly{suffix}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def handle(self, message: dict[str, Any]) -> str:
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if message.get("id") is not None and method:
            self._handle_server_request(message["id"], method, params)
            return ""
        if method == "item/started":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            item_id = str(item.get("id") or "")
            if item_id:
                self._items[item_id] = item
            item_type = str(item.get("type") or "")
            if item_type == "userMessage":
                self._model_thinking_announced = True
                emit(self.on_event, "status", "Model is thinking...")
            elif item_type == "commandExecution":
                emit(self.on_event, "progress", f"Running: {item.get('command') or 'command'}")
            elif item_type == "fileChange":
                emit(self.on_event, "progress", "Preparing file changes…")
            elif item_type not in {"agentMessage", "reasoning", "plan", "userMessage"}:
                detail = (
                    item.get("query")
                    or item.get("name")
                    or item.get("tool")
                    or item.get("path")
                    or ""
                )
                suffix = f": {detail}" if detail else ""
                emit(self.on_event, "progress", f"ChatGPT started {item_type or 'action'}{suffix}")
        elif method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            item_type = str(item.get("type") or "")
            if item_type == "agentMessage" and not self._reply_parts:
                text = str(item.get("text") or "")
                if text:
                    self._reply_parts.append(text)
                    emit(self.on_event, "reply", text)
            elif item_type == "imageGeneration":
                saved_path = str(item.get("savedPath") or "").strip()
                result = str(item.get("result") or "").strip()
                if not saved_path and result and not result.startswith("data:"):
                    candidate = Path(result).expanduser()
                    if candidate.is_absolute():
                        saved_path = str(candidate)
                attachment: dict[str, Any] | None = None
                if saved_path:
                    attachment = {
                        "kind": "image",
                        "source": "codex_image_generation",
                        "path": saved_path,
                        "name": Path(saved_path).name or "generated-image.png",
                    }
                elif result.startswith("data:image/"):
                    attachment = {
                        "kind": "image",
                        "source": "codex_image_generation",
                        "data_url": result,
                        "name": "generated-image.png",
                    }
                if attachment is not None:
                    revised_prompt = str(item.get("revisedPrompt") or "").strip()
                    if revised_prompt:
                        attachment["revised_prompt"] = revised_prompt
                    self._attachments.append(attachment)
                    if self.on_event is not None:
                        self.on_event(
                            HarnessEvent(
                                kind="image",
                                text="Image generated.",
                                attachment=dict(attachment),
                            )
                        )
                else:
                    emit(self.on_event, "progress", "ChatGPT imageGeneration: completed")
            elif item_type in {"commandExecution", "fileChange"}:
                status = str(item.get("status") or "completed")
                emit(self.on_event, "progress", f"ChatGPT {item_type}: {status}")
            elif item_type not in {"agentMessage", "reasoning", "plan", "userMessage"}:
                status = str(item.get("status") or "completed")
                emit(self.on_event, "progress", f"ChatGPT {item_type or 'action'}: {status}")
        elif method == "item/agentMessage/delta":
            delta = str(params.get("delta") or "")
            if delta:
                self._reply_parts.append(delta)
                emit(self.on_event, "reply", delta)
        elif method in {
            "item/reasoning/summaryTextDelta",
            "item/reasoning/textDelta",
            "item/plan/delta",
        }:
            emit(self.on_event, "thought", params.get("delta"))
        elif method == "warning":
            emit(self.on_event, "progress", params.get("message"))
        elif method == "error":
            error = params.get("error") if isinstance(params.get("error"), dict) else params
            raise CodexAppServerError(str(error.get("message") if isinstance(error, dict) else error))
        elif method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            status = str(turn.get("status") or "completed")
            if status == "failed":
                error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
                raise CodexAppServerError(str(error.get("message") or "ChatGPT turn failed"))
            return status
        return ""

    def _handle_server_request(self, request_id: object, method: str, params: dict[str, Any]) -> None:
        item_id = str(params.get("itemId") or "")
        item = self._items.get(item_id, {})
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            allowed = False
            if self.approval_callback is not None:
                allowed = approval_allowed(self.approval_callback(_approval_request(params, method, item)))
            self.send({"id": request_id, "result": {"decision": "accept" if allowed else "decline"}})
            return
        if method == "item/permissions/requestApproval":
            allowed = False
            if self.approval_callback is not None:
                allowed = approval_allowed(self.approval_callback({
                    "action": "grant ChatGPT permissions",
                    "path": str(params.get("cwd") or ""),
                    "details": {"reason": params.get("reason"), "permissions": params.get("permissions")},
                }))
            result = {"permissions": params.get("permissions") or {}} if allowed else {"permissions": {}}
            self.send({"id": request_id, "result": result})
            return
        self.send({"id": request_id, "error": {"code": -32601, "message": f"Unsupported request: {method}"}})


def _client_alive(client: _Client | None) -> bool:
    """Return whether the cached app-server process can accept another turn."""
    if client is None:
        return False
    try:
        return client.process.poll() is None
    except Exception:
        return False


def _initialize_client(
    workdir: Path,
    on_event: EventCallback | None,
    approval_callback: ApprovalCallback | None,
) -> _Client:
    """Start and initialize one reusable Codex app-server."""
    client = _Client(workdir, on_event, approval_callback)
    try:
        client.request("initialize", {
            "clientInfo": {"name": "wisp", "title": "Wisp", "version": "0.10.2"},
            "capabilities": {"experimentalApi": True},
        })
        client.send({"method": "initialized", "params": {}})
        return client
    except Exception:
        client.close()
        raise


def _persistent_client(
    workdir: Path,
    on_event: EventCallback | None,
    approval_callback: ApprovalCallback | None,
) -> tuple[_Client, bool]:
    """Return the Wisp-lifetime app-server, creating it when necessary.

    The caller must hold ``_PERSISTENT_CLIENT_LOCK`` for the complete turn;
    ``_Client`` owns one JSONL stream and cannot safely interleave reads from
    concurrent requests.
    """
    global _PERSISTENT_CLIENT
    if _client_alive(_PERSISTENT_CLIENT):
        assert _PERSISTENT_CLIENT is not None
        _PERSISTENT_CLIENT.on_event = on_event
        _PERSISTENT_CLIENT.approval_callback = approval_callback
        return _PERSISTENT_CLIENT, False
    if _PERSISTENT_CLIENT is not None:
        _PERSISTENT_CLIENT.close()
    _PERSISTENT_CLIENT = _initialize_client(workdir, on_event, approval_callback)
    return _PERSISTENT_CLIENT, True


def close_persistent_codex() -> None:
    """Stop the reusable app-server during brain-worker shutdown or tests."""
    global _PERSISTENT_CLIENT
    with _PERSISTENT_CLIENT_LOCK:
        client, _PERSISTENT_CLIENT = _PERSISTENT_CLIENT, None
        if client is not None:
            client.close()


def prewarm_codex(cwd: str | Path | None = None) -> dict[str, Any]:
    """Start Codex's reusable app-server before the first user prompt."""
    workdir = normalized_cwd(cwd)
    with _PERSISTENT_CLIENT_LOCK:
        client, created = _persistent_client(workdir, None, None)
        return {
            "ready": True,
            "cached": not created,
            "backend": client.backend,
        }


atexit.register(close_persistent_codex)


def _approval_policy_rejected(exc: CodexAppServerError) -> bool:
    """Return whether app-server rejected a version-specific approval enum."""
    message = str(exc).lower()
    names_policy = any(value.lower() in message for value in _APPROVAL_POLICIES)
    describes_enum_error = any(
        marker in message
        for marker in ("approval", "unknown variant", "invalid value", "allowed set")
    )
    return names_policy and describes_enum_error


def _reasoning_options_rejected(exc: CodexAppServerError) -> bool:
    """Return whether an older app-server rejected reasoning controls."""
    message = str(exc).lower()
    return any(name in message for name in ("effort", "summary", "reasoning")) and any(
        marker in message
        for marker in ("unknown field", "unknown variant", "invalid params", "invalid value")
    )


def _configured_reasoning_effort() -> str:
    """Read Wisp's live reasoning setting without coupling config import order."""
    try:
        import config

        value = getattr(config, "WISP_CODEX_REASONING_EFFORT", "high")
    except (ImportError, AttributeError):
        value = os.getenv("CHAT_REASONING_EFFORT", "high")
    return str(value or "").strip().lower()


def _request_turn(client: _Client, params: dict[str, Any]) -> dict[str, Any]:
    """Retry without reasoning controls when an older app-server lacks them."""
    try:
        return client.request("turn/start", params)
    except CodexAppServerError as exc:
        if not _reasoning_options_rejected(exc):
            raise
        params.pop("effort", None)
        params.pop("summary", None)
        emit(client.on_event, "progress", "This ChatGPT version does not stream reasoning summaries.")
        return client.request("turn/start", params)


def _start_turn(
    client: _Client,
    thread_id: str,
    prompt: str,
    workdir: Path,
    *,
    reasoning_effort: str = "high",
    reasoning_summary: str = _REASONING_SUMMARY,
    model: str = "",
    fast_mode: bool = False,
    approval_mode: str = "ask",
) -> dict[str, Any]:
    """Start a turn with live summaries and cross-version approval support."""
    sandbox_policy: dict[str, Any]
    if approval_mode == "read_only":
        sandbox_policy = {"type": "readOnly"}
    elif approval_mode == "full_access":
        sandbox_policy = {"type": "dangerFullAccess"}
    else:
        sandbox_policy = {
            "type": "workspaceWrite",
            "writableRoots": [str(workdir)],
            "networkAccess": False,
        }
    params: dict[str, Any] = {
        "threadId": thread_id,
        "input": [{"type": "text", "text": str(prompt)}],
        "cwd": str(workdir),
        "summary": str(reasoning_summary or _REASONING_SUMMARY),
        "approvalPolicy": (
            "never"
            if approval_mode in {"auto_edits", "full_access", "read_only"}
            else _APPROVAL_POLICIES[0]
        ),
        "sandboxPolicy": sandbox_policy,
    }
    if approval_mode == "ask":
        params["approvalsReviewer"] = "user"
    if str(reasoning_effort or "").strip():
        params["effort"] = str(reasoning_effort).strip().lower()
    if str(model or "").strip():
        params["model"] = str(model).strip()
    if fast_mode:
        params["serviceTier"] = "priority"
    try:
        return _request_turn(client, params)
    except CodexAppServerError as exc:
        if not _approval_policy_rejected(exc):
            raise
        params["approvalPolicy"] = _APPROVAL_POLICIES[1]
        emit(client.on_event, "progress", "Retrying with this ChatGPT version's approval policy…")
        return _request_turn(client, params)


def run_codex(
    prompt: str,
    *,
    session_id: str = "",
    cwd: str | Path | None = None,
    on_event: EventCallback | None = None,
    approval_callback: ApprovalCallback | None = None,
) -> HarnessResult:
    """Run one prompt through a local Codex app-server process."""
    workdir = normalized_cwd(cwd)
    try:
        import config

        model = str(getattr(config, "WISP_CODEX_MODEL", "") or "")
        fast_mode = bool(getattr(config, "WISP_CODEX_FAST_MODE", False))
        approval_mode = str(getattr(config, "WISP_CODEX_APPROVAL_MODE", "ask") or "ask")
        reasoning_summary = str(
            getattr(config, "WISP_CODEX_REASONING_SUMMARY", _REASONING_SUMMARY)
            or _REASONING_SUMMARY
        )
        system_prompt = str(getattr(config, "WISP_CODEX_SYSTEM_PROMPT", "") or "")
    except (ImportError, AttributeError):
        model = os.getenv("WISP_CODEX_MODEL", "")
        fast_mode = os.getenv("WISP_CODEX_FAST_MODE", "").lower() in {"1", "true", "yes", "on"}
        approval_mode = os.getenv("WISP_CODEX_APPROVAL_MODE", "ask")
        reasoning_summary = os.getenv("WISP_CODEX_REASONING_SUMMARY", _REASONING_SUMMARY)
        system_prompt = os.getenv("WISP_CODEX_SYSTEM_PROMPT", "")
    with _PERSISTENT_CLIENT_LOCK:
        client: _Client | None = None
        try:
            client, _created = _persistent_client(workdir, on_event, approval_callback)
            client._items.clear()
            client._reply_parts.clear()
            if not hasattr(client, "_attachments"):
                client._attachments = []
            client._attachments.clear()
            client._model_thinking_announced = False
            emit(on_event, "status", "Opening conversation in ChatGPT...")
            thread_id = str(session_id or "").strip()
            if thread_id:
                try:
                    resumed = client.request(
                        "thread/resume",
                        {
                            "threadId": thread_id,
                            "developerInstructions": system_prompt,
                        },
                    )
                    thread = resumed.get("thread") if isinstance(resumed.get("thread"), dict) else {}
                    thread_id = str(thread.get("id") or thread_id)
                except CodexAppServerError:
                    thread_id = ""
            if not thread_id:
                started = client.request(
                    "thread/start",
                    {
                        "cwd": str(workdir),
                        "developerInstructions": system_prompt,
                    },
                )
                thread = started.get("thread") if isinstance(started.get("thread"), dict) else {}
                thread_id = str(thread.get("id") or "")
            if not thread_id:
                raise CodexAppServerError("ChatGPT's local agent did not return a conversation id")
            emit(on_event, "status", "Preparing ChatGPT turn...")
            _start_turn(
                client,
                thread_id,
                prompt,
                workdir,
                reasoning_effort=_configured_reasoning_effort(),
                reasoning_summary=reasoning_summary,
                model=model,
                fast_mode=fast_mode,
                approval_mode=approval_mode,
            )
            if not getattr(client, "_model_thinking_announced", False):
                emit(on_event, "status", "Model is thinking...")
            while True:
                status = client.handle(client.read())
                if status in {"completed", "interrupted"}:
                    break
            return HarnessResult(
                provider="codex",
                text="".join(client._reply_parts),
                session_id=thread_id,
                cwd=str(workdir),
                backend=client.backend,
                attachments=tuple(dict(item) for item in client._attachments),
            )
        except Exception:
            # A protocol or turn failure can leave unread JSONL messages behind.
            # Discard the process so the next turn gets a clean app-server.
            close_persistent_codex()
            raise
