"""Tests for starting an already-installed Ollama server on demand."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core import ollama_manager


def test_ensure_ollama_running_leaves_ready_server_alone(monkeypatch):
    monkeypatch.setattr(ollama_manager, "ollama_is_running", lambda: True)
    monkeypatch.setattr(
        ollama_manager,
        "_start_ollama",
        lambda _executable: pytest.fail("a running Ollama server must not be started again"),
    )

    assert ollama_manager.ensure_ollama_running() is False


def test_ensure_ollama_running_starts_installed_server_and_waits(monkeypatch, tmp_path):
    executable = tmp_path / "ollama.exe"
    executable.touch()
    states = iter((False, False, True))
    started: list[Path] = []

    monkeypatch.setattr(ollama_manager, "ollama_is_running", lambda: next(states))
    monkeypatch.setattr(ollama_manager, "find_ollama_executable", lambda: executable)
    monkeypatch.setattr(ollama_manager, "_start_ollama", lambda path: started.append(path))
    monkeypatch.setattr(ollama_manager.time, "sleep", lambda _seconds: None)

    assert ollama_manager.ensure_ollama_running(timeout_seconds=1) is True
    assert started == [executable]


def test_ensure_ollama_running_explains_when_not_installed(monkeypatch):
    monkeypatch.setattr(ollama_manager, "ollama_is_running", lambda: False)
    monkeypatch.setattr(ollama_manager, "find_ollama_executable", lambda: None)

    with pytest.raises(RuntimeError, match="could not find an installed Ollama"):
        ollama_manager.ensure_ollama_running()


def test_find_ollama_executable_uses_configured_path(monkeypatch, tmp_path):
    executable = tmp_path / "ollama-custom.exe"
    executable.touch()
    monkeypatch.setenv("OLLAMA_BIN", str(executable))

    assert ollama_manager.find_ollama_executable() == executable


def test_llm_client_starts_ollama_before_listing_models(monkeypatch):
    from core.llm_clients import client

    started: list[bool] = []
    model_client = SimpleNamespace(
        models=SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id="local-model")]))
    )
    monkeypatch.setattr(client, "_ensure_ollama_running", lambda: started.append(True))
    monkeypatch.setattr(client.sdk_clients, "openai_client", lambda **_kwargs: model_client)

    assert client.list_models("ollama") == ["local-model"]
    assert started == [True]


def test_llm_client_starts_ollama_before_building_request_client(monkeypatch):
    from core.llm_clients import client

    started: list[bool] = []
    built: list[dict] = []
    sentinel = object()
    client._dynamic_openai_clients.pop("ollama", None)
    monkeypatch.setattr(client, "_ensure_ollama_running", lambda: started.append(True))
    monkeypatch.setattr(
        client.sdk_clients,
        "openai_client",
        lambda **kwargs: built.append(kwargs) or sentinel,
    )

    try:
        assert client._dynamic_openai_client("ollama") is sentinel
        assert started == [True]
        assert built[0]["base_url"] == "http://localhost:11434/v1"
    finally:
        client._dynamic_openai_clients.pop("ollama", None)
