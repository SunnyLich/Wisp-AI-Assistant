"""
core/llm.py — Cloud LLM client with streaming support.

Supports:
  - Groq (OpenAI-compatible, fast TTFT)
  - OpenAI
  - Anthropic Claude

Clients are module-level singletons so the TLS connection is reused across
calls, eliminating handshake overhead from every request.
"""
from __future__ import annotations
import config
from typing import Generator

# ------------------------------------------------------------------
# Singleton clients — initialised once, reused across all requests
# ------------------------------------------------------------------
_openai_client = None
_anthropic_client = None
_chat_openai_client = None
_chat_anthropic_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        if config.LLM_PROVIDER.lower() == "groq":
            _openai_client = OpenAI(
                api_key=config.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
            )
        else:
            _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _openai_client


def _get_chat_openai_client():
    """Returns the same singleton as _get_openai_client() when providers match."""
    if config.CHAT_LLM_PROVIDER.lower() == config.LLM_PROVIDER.lower():
        return _get_openai_client()
    global _chat_openai_client
    if _chat_openai_client is None:
        from openai import OpenAI
        if config.CHAT_LLM_PROVIDER.lower() == "groq":
            _chat_openai_client = OpenAI(
                api_key=config.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
            )
        else:
            _chat_openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _chat_openai_client


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


def _get_chat_anthropic_client():
    if config.CHAT_LLM_PROVIDER.lower() == config.LLM_PROVIDER.lower():
        return _get_anthropic_client()
    global _chat_anthropic_client
    if _chat_anthropic_client is None:
        import anthropic
        _chat_anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _chat_anthropic_client


def stream_response(
    user_message: str,
    image_base64: str | None = None,
) -> Generator[str, None, None]:
    """
    Stream a response from the configured LLM.

    Args:
        user_message: The user's query text.
        image_base64: Optional base64-encoded PNG for vision input.

    Yields:
        Text chunks as they arrive from the API.
    """
    provider = config.LLM_PROVIDER.lower()
    if provider in ("groq", "openai"):
        yield from _stream_openai_compat(user_message, image_base64)
    elif provider == "anthropic":
        yield from _stream_anthropic(user_message, image_base64)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


# ------------------------------------------------------------------
# OpenAI / Groq (OpenAI-compatible)
# ------------------------------------------------------------------

def _stream_openai_compat(
    user_message: str,
    image_base64: str | None,
) -> Generator[str, None, None]:
    client = _get_openai_client()
    messages = _build_openai_messages(user_message, image_base64)

    with client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=messages,
        stream=True,
        max_tokens=256,
        temperature=0.5,
    ) as stream:
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


def _build_openai_messages(user_message: str, image_base64: str | None) -> list:
    system = config.get_system_prompt()
    if image_base64:
        content = [
            {"type": "text", "text": user_message},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_base64}"},
            },
        ]
    else:
        content = user_message

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


# ------------------------------------------------------------------
# Anthropic Claude
# ------------------------------------------------------------------

def _stream_anthropic(
    user_message: str,
    image_base64: str | None,
) -> Generator[str, None, None]:
    client = _get_anthropic_client()
    system = config.get_system_prompt()

    if image_base64:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_base64,
                },
            },
            {"type": "text", "text": user_message},
        ]
    else:
        content = user_message

    with client.messages.stream(
        model=config.LLM_MODEL,
        max_tokens=256,
        system=system,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        for text in stream.text_stream:
            yield text


# ------------------------------------------------------------------
# Multi-turn (chat window)
# ------------------------------------------------------------------

def stream_response_with_history(messages: list) -> Generator[str, None, None]:
    """
    Stream a response given a pre-built messages list including history.
    Uses CHAT_LLM_PROVIDER / CHAT_LLM_MODEL (defaults to LLM_PROVIDER / LLM_MODEL).
    messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]
    """
    provider = config.CHAT_LLM_PROVIDER.lower()
    if provider in ("groq", "openai"):
        client = _get_chat_openai_client()
        with client.chat.completions.create(
            model=config.CHAT_LLM_MODEL,
            messages=messages,
            stream=True,
            max_tokens=1024,
            temperature=0.7,
        ) as stream:
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
    elif provider == "anthropic":
        client = _get_chat_anthropic_client()
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        turns = [m for m in messages if m["role"] != "system"]
        with client.messages.stream(
            model=config.CHAT_LLM_MODEL,
            max_tokens=1024,
            system=system,
            messages=turns,
        ) as stream:
            for text in stream.text_stream:
                yield text
    else:
        raise ValueError(f"Unknown chat LLM provider: {provider}")
