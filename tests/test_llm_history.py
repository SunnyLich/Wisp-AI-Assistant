"""Conversation-continuity history replay in the LLM client."""
import threading
import time
from types import SimpleNamespace

import config
from core.llm_clients import client


def test_sanitize_history_keeps_only_text_user_assistant_turns():
    raw = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello", "display_content": "<b>hello</b>"},
        {"role": "user", "content": "   "},          # empty -> dropped
        {"role": "system", "content": "ignore me"},   # wrong role -> dropped
        {"role": "user", "content": ["not", "a", "string"]},  # non-str -> dropped
    ]
    assert client._sanitize_history(raw) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_sanitize_history_handles_none_and_empty():
    assert client._sanitize_history(None) == []
    assert client._sanitize_history([]) == []


def test_openai_messages_splice_history_between_system_and_current_turn():
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
    ]
    msgs = client._build_openai_messages("now", None, "", "", history)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "first"
    assert msgs[2]["content"] == "reply"
    assert msgs[-1]["content"] == "now"


def test_openai_messages_no_history_is_system_then_user():
    msgs = client._build_openai_messages("solo", None, "", "")
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_openai_messages_attach_context_to_current_user_turn():
    """Verify dynamic context is user data, not system instructions."""
    msgs = client._build_openai_messages(
        "explain this",
        None,
        "[Selection]\nselected text",
        "[Session memory]\nremembered fact",
        system_prompt="SYSTEM RULES",
    )

    assert msgs[0]["content"] == "SYSTEM RULES"
    assert "selected text" not in msgs[0]["content"]
    assert "remembered fact" not in msgs[0]["content"]

    current = msgs[-1]["content"]
    assert current.startswith("<context>\n")
    assert "untrusted data" in current
    assert "<memory>\n[Session memory]\nremembered fact\n</memory>" in current
    assert "<captured_context>\n[Selection]\nselected text\n</captured_context>" in current
    assert current.endswith("<request>\nexplain this\n</request>")


def test_openai_vision_messages_attach_context_to_text_block():
    """Verify vision context stays in the user text block beside the image."""
    msgs = client._build_openai_messages(
        "what is this?",
        "image-b64",
        "[Browser/Web]\npage text",
        "",
        system_prompt="SYSTEM RULES",
    )

    assert msgs[0]["content"] == "SYSTEM RULES"
    content = msgs[-1]["content"]
    assert content[0]["type"] == "text"
    assert "<captured_context>\n[Browser/Web]\npage text\n</captured_context>" in content[0]["text"]
    assert content[0]["text"].endswith("<request>\nwhat is this?\n</request>")
    assert content[1]["type"] == "image_url"


def test_codex_text_uses_context_wrapper():
    """Verify single-text providers use the same context/request shape."""
    text = client._build_codex_text(
        "summarize",
        "[Clipboard]\nclip text",
        "[Session memory]\nremembered fact",
    )

    assert text.startswith("<context>\n")
    assert "<memory>\n[Session memory]\nremembered fact\n</memory>" in text
    assert "<captured_context>\n[Clipboard]\nclip text\n</captured_context>" in text
    assert text.endswith("<request>\nsummarize\n</request>")


def test_ollama_prefix_request_contains_only_static_openwand_content(monkeypatch):
    monkeypatch.setattr(config, "get_system_prompt", lambda: "OPENWAND SYSTEM")

    built = client._build_ollama_prefix_request(
        provider="ollama",
        model="local-test-model",
        allowed_tools=["web_search", "memory_search"],
        pinned_tools=[],
    )

    assert built is not None
    identity, request, metadata = built
    assert len(identity) == 64
    assert request["messages"] == [
        {"role": "system", "content": request["messages"][0]["content"]},
        {"role": "user", "content": "."},
    ]
    system = request["messages"][0]["content"]
    assert system.startswith("OPENWAND SYSTEM")
    assert "live tools available" in system
    assert "memory_search tool" in system
    assert {tool["function"]["name"] for tool in request["tools"]} == {
        "web_search",
        "memory_search",
    }
    assert metadata["tool_count"] == 2
    assert request["extra_body"] == {"keep_alive": "10m"}


def test_ollama_prefix_identity_rebuilds_only_for_static_inputs(monkeypatch):
    monkeypatch.setattr(config, "get_system_prompt", lambda: "SYSTEM ONE")

    def identity(**kwargs):
        built = client._build_ollama_prefix_request(
            provider="ollama",
            model=kwargs.pop("model", "model-a"),
            allowed_tools=kwargs.pop("allowed_tools", ["web_search"]),
            **kwargs,
        )
        assert built is not None
        return built[0]

    baseline = identity()
    assert identity() == baseline
    assert identity(model="model-b") != baseline
    assert identity(allowed_tools=["git_status"]) != baseline
    assert identity(browser_retrieval=True) != baseline
    assert identity(system_prompt="SYSTEM TWO") != baseline


def test_ollama_prefix_scheduler_prefills_once_and_reuses_ready_identity(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "LLM_MODEL", "local-test-model")
    monkeypatch.setattr(config, "get_system_prompt", lambda: "OPENWAND SYSTEM")
    completed = threading.Event()
    requests: list[dict] = []

    def create(**kwargs):
        requests.append(kwargs)
        completed.set()
        return SimpleNamespace(choices=[])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(client, "_dynamic_openai_client", lambda _provider: fake_client)
    client.invalidate_ollama_prefix_cache()
    try:
        first = client.schedule_ollama_prefix_prewarm(
            route_kind="query",
            allowed_tools=["web_search"],
        )
        assert first["scheduled"] is True
        assert completed.wait(timeout=2.0)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            second = client.schedule_ollama_prefix_prewarm(
                route_kind="query",
                allowed_tools=["web_search"],
            )
            if second.get("ready"):
                break
            time.sleep(0.01)
        assert second["ready"] is True
        assert second["cached"] is True
        assert len(requests) == 1
        assert [message["role"] for message in requests[0]["messages"]] == ["system", "user"]
    finally:
        client.invalidate_ollama_prefix_cache()
