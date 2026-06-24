"""Conversation-continuity history replay in the LLM client."""
from core.llm_clients import client


def test_sanitize_history_keeps_only_text_user_assistant_turns():
    """Verify sanitize history keeps only text user assistant turns behavior."""
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
    """Verify sanitize history handles none and empty behavior."""
    assert client._sanitize_history(None) == []
    assert client._sanitize_history([]) == []


def test_openai_messages_splice_history_between_system_and_current_turn():
    """Verify openai messages splice history between system and current turn behavior."""
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
    """Verify openai messages no history is system then user behavior."""
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
