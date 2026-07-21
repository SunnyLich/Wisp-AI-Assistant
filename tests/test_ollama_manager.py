"""Tests for starting an already-installed Ollama server on demand."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core import ollama_manager


def test_ensure_ollama_running_leaves_ready_server_alone(monkeypatch):
    monkeypatch.setattr(ollama_manager, "ollama_is_running", lambda base_url=None: True)
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

    monkeypatch.setattr(ollama_manager, "ollama_is_running", lambda base_url=None: next(states))
    monkeypatch.setattr(ollama_manager, "find_ollama_executable", lambda: executable)
    monkeypatch.setattr(ollama_manager, "_start_ollama", lambda path: started.append(path))
    monkeypatch.setattr(ollama_manager.time, "sleep", lambda _seconds: None)

    assert ollama_manager.ensure_ollama_running(timeout_seconds=1) is True
    assert started == [executable]


def test_ensure_ollama_running_explains_when_not_installed(monkeypatch):
    monkeypatch.setattr(ollama_manager, "ollama_is_running", lambda base_url=None: False)
    monkeypatch.setattr(ollama_manager, "find_ollama_executable", lambda: None)

    with pytest.raises(RuntimeError, match="could not find an installed Ollama"):
        ollama_manager.ensure_ollama_running()


def test_ollama_runtime_failure_matrix_is_controlled(monkeypatch, tmp_path):
    """Server, endpoint, model, RAM, and VRAM faults all return bounded diagnostics."""
    executable = tmp_path / "ollama"
    executable.touch()
    monkeypatch.setattr(ollama_manager, "ollama_is_running", lambda base_url=None: False)
    monkeypatch.setattr(ollama_manager, "find_ollama_executable", lambda: executable)
    monkeypatch.setattr(ollama_manager.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ollama_manager, "_start_ollama", lambda _path: None)

    with pytest.raises(RuntimeError, match="did not become ready"):
        ollama_manager.ensure_ollama_running(timeout_seconds=0)

    monkeypatch.setattr(
        ollama_manager,
        "_start_ollama",
        lambda _path: (_ for _ in ()).throw(OSError("server cannot be started")),
    )
    with pytest.raises(RuntimeError, match="could not start Ollama"):
        ollama_manager.ensure_ollama_running()

    for url in ("http://wrong-host.invalid:11434/v1", "http://192.0.2.1:65500/v1"):
        with pytest.raises(RuntimeError, match="only auto-starts a local"):
            ollama_manager.ensure_ollama_running(base_url=url)

    from core.llm_clients import client

    probe_failures = (
        "requested Ollama model is not installed",
        "available RAM is insufficient for the model",
        "available VRAM is insufficient for the model",
    )
    for failure in probe_failures:
        with monkeypatch.context() as scoped:
            scoped.setattr(client, "_check_route_config", lambda *_args: None)
            scoped.setattr(
                client,
                "_probe_openai_compat_route",
                lambda *_args, failure=failure, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError(failure)
                ),
            )
            ok, message = client.test_route_connection("ollama", "local-model", "LLM")
        assert ok is False
        assert failure in message


def test_ensure_ollama_running_does_not_start_a_server_for_remote_urls(monkeypatch):
    monkeypatch.setattr(ollama_manager, "ollama_is_running", lambda base_url=None: False)
    monkeypatch.setattr(
        ollama_manager,
        "_start_ollama",
        lambda _executable: pytest.fail("a local server must not be started for a remote URL"),
    )

    with pytest.raises(RuntimeError, match="only auto-starts a local"):
        ollama_manager.ensure_ollama_running(base_url="http://192.168.1.20:11434/v1")


def test_resolve_ollama_base_url_honors_ollama_host(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ollama_manager.resolve_ollama_base_url() == "http://localhost:11434/v1"

    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11500")
    assert ollama_manager.resolve_ollama_base_url() == "http://127.0.0.1:11500/v1"

    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0")
    assert ollama_manager.resolve_ollama_base_url() == "http://127.0.0.1:11434/v1"

    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.lan:8080/")
    assert ollama_manager.resolve_ollama_base_url() == "https://ollama.lan:8080/v1"


def test_probe_url_matches_the_request_base_url():
    assert (
        ollama_manager._api_probe_url("http://127.0.0.1:11500/v1")
        == "http://127.0.0.1:11500/api/tags"
    )
    assert ollama_manager._api_probe_url(None) == ollama_manager.OLLAMA_BASE_URL.replace(
        "/v1", "/api/tags"
    )


def test_find_ollama_executable_uses_configured_path(monkeypatch, tmp_path):
    executable = tmp_path / "ollama-custom.exe"
    executable.touch()
    monkeypatch.setenv("OLLAMA_BIN", str(executable))

    assert ollama_manager.find_ollama_executable() == executable


def test_llm_client_starts_ollama_before_listing_models(monkeypatch):
    from core.llm_clients import client

    started: list[dict] = []
    model_client = SimpleNamespace(
        models=SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id="local-model")]))
    )
    monkeypatch.setattr(client, "_ensure_ollama_running", lambda **kwargs: started.append(kwargs))
    monkeypatch.setattr(client.sdk_clients, "openai_client", lambda **_kwargs: model_client)

    assert client.list_models("ollama") == ["local-model"]
    assert started == [{"base_url": None}]

    started.clear()
    assert client.list_models("ollama", base_url="http://127.0.0.1:11500/v1") == ["local-model"]
    assert started == [{"base_url": "http://127.0.0.1:11500/v1"}]


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
        # The request client and the readiness probe share one resolved endpoint.
        assert built[0]["base_url"] == client._OLLAMA_BASE_URL
        assert built[0]["base_url"] == ollama_manager.OLLAMA_BASE_URL
    finally:
        client._dynamic_openai_clients.pop("ollama", None)
