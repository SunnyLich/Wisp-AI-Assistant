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
# Context tools — offered to Claude during hotkey-triggered queries.
# web_search is a built-in Anthropic server-side tool (no client code needed).
# fetch_browser_page is user-defined: we execute it and return the result.
# ------------------------------------------------------------------

_CONTEXT_TOOLS: list[dict] = [
    {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 2,
    },
    {
        "name": "fetch_browser_page",
        "description": (
            "Fetch and read the plain-text content of a web page. "
            "Use when the user asks about a specific URL or the current browser page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to fetch (must start with http:// or https://)",
                }
            },
            "required": ["url"],
        },
    },
]


def _execute_context_tool(name: str, inputs: dict) -> str:
    """Execute a user-defined context tool and return a plain-text result."""
    if name == "fetch_browser_page":
        from core.context_fetcher import fetch_browser_content_for_tool
        url = inputs.get("url", "")
        result = fetch_browser_content_for_tool(url)
        return result or f"Could not fetch content from {url!r}."
    return f"Unknown tool: {name!r}"

# ------------------------------------------------------------------
# Singleton clients — initialised once, reused across all requests
# ------------------------------------------------------------------
_openai_client = None
_anthropic_client = None
_chat_openai_client = None
_chat_anthropic_client = None
_vision_openai_client = None
_vision_anthropic_client = None


# ------------------------------------------------------------------
# Config sanity checks — raise early with actionable messages
# ------------------------------------------------------------------

def _api_key_for(provider: str) -> str:
    p = provider.lower()
    if p in ("groq",):
        return config.GROQ_API_KEY
    if p == "openai":
        return config.OPENAI_API_KEY
    if p == "anthropic":
        return config.ANTHROPIC_API_KEY
    return ""


def _check_llm_config() -> None:
    if not config.LLM_MODEL:
        raise ValueError(
            "LLM_MODEL is not set in .env. "
            "Add LLM_MODEL=<model> (e.g. llama3-8b-8192 for Groq)."
        )
    if not _api_key_for(config.LLM_PROVIDER):
        raise ValueError(
            f"API key for LLM_PROVIDER='{config.LLM_PROVIDER}' is not set in .env. "
            "Add the matching *_API_KEY variable."
        )


def _check_chat_llm_config() -> None:
    if not config.CHAT_LLM_MODEL:
        raise ValueError(
            "CHAT_LLM_MODEL is not set in .env. "
            "Add CHAT_LLM_MODEL=<model> or leave unset to inherit LLM_MODEL."
        )
    if not _api_key_for(config.CHAT_LLM_PROVIDER):
        raise ValueError(
            f"API key for CHAT_LLM_PROVIDER='{config.CHAT_LLM_PROVIDER}' is not set in .env. "
            "Add the matching *_API_KEY variable."
        )


def _check_vision_config() -> None:
    if not config.VISION_LLM_MODEL:
        raise ValueError(
            "VISION_LLM_MODEL is not set in .env. "
            "Screen-snip queries require a vision-capable model. "
            "Example: VISION_LLM_PROVIDER=anthropic  VISION_LLM_MODEL=claude-opus-4-5"
        )
    if not config.VISION_LLM_PROVIDER:
        raise ValueError(
            "VISION_LLM_PROVIDER is not set in .env. "
            "Add VISION_LLM_PROVIDER=anthropic (or openai) alongside VISION_LLM_MODEL."
        )
    if not _api_key_for(config.VISION_LLM_PROVIDER):
        raise ValueError(
            f"API key for VISION_LLM_PROVIDER='{config.VISION_LLM_PROVIDER}' is not set in .env. "
            "Add the matching *_API_KEY variable."
        )


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


def _get_vision_openai_client():
    global _vision_openai_client
    if _vision_openai_client is None:
        from openai import OpenAI
        if config.VISION_LLM_PROVIDER.lower() == "groq":
            _vision_openai_client = OpenAI(
                api_key=config.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
            )
        else:
            _vision_openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _vision_openai_client


def _get_vision_anthropic_client():
    global _vision_anthropic_client
    if _vision_anthropic_client is None:
        import anthropic
        _vision_anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _vision_anthropic_client


def stream_response(
    user_message: str,
    image_base64: str | None = None,
    ambient_context: str = "",
    use_tools: bool = False,
) -> Generator[str, None, None]:
    """
    Stream a response from the configured LLM.

    When image_base64 is provided, uses VISION_LLM_PROVIDER/MODEL.
    Otherwise uses LLM_PROVIDER/MODEL.

    Args:
        user_message:     The user's query text.
        image_base64:     Optional base64-encoded PNG for vision input.
        ambient_context:  Plain-text context block prepended to the system
                          prompt (active window, clipboard, focused element).
        use_tools:        If True and provider is Anthropic, expose
                          web_search + fetch_browser_page tools so Claude can
                          pull extra context when it decides to.  Ignored for
                          Groq/OpenAI providers and vision calls.

    Yields:
        Text chunks as they arrive from the API.
    """
    if image_base64:
        _check_vision_config()
        provider = config.VISION_LLM_PROVIDER.lower()
        model    = config.VISION_LLM_MODEL
        if provider in ("groq", "openai"):
            yield from _stream_openai_compat(user_message, image_base64, model, _get_vision_openai_client())
        elif provider == "anthropic":
            yield from _stream_anthropic(user_message, image_base64, model, _get_vision_anthropic_client())
        else:
            raise ValueError(f"Unknown VISION_LLM_PROVIDER: {provider}")
    else:
        _check_llm_config()
        provider = config.LLM_PROVIDER.lower()
        if provider in ("groq", "openai"):
            yield from _stream_openai_compat(user_message, None, config.LLM_MODEL, _get_openai_client(), ambient_context)
        elif provider == "anthropic":
            yield from _stream_anthropic(user_message, None, config.LLM_MODEL, _get_anthropic_client(), ambient_context, use_tools)
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {provider}")


# ------------------------------------------------------------------
# OpenAI / Groq (OpenAI-compatible)
# ------------------------------------------------------------------

def _stream_openai_compat(
    user_message: str,
    image_base64: str | None,
    model: str,
    client,
    ambient_context: str = "",
) -> Generator[str, None, None]:
    messages = _build_openai_messages(user_message, image_base64, ambient_context)

    with client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        max_tokens=256,
        temperature=0.5,
    ) as stream:
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


def _build_openai_messages(user_message: str, image_base64: str | None, ambient_context: str = "") -> list:
    system = config.get_system_prompt()
    if ambient_context:
        system += f"\n\n---\n{ambient_context}"
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
    model: str,
    client,
    ambient_context: str = "",
    use_tools: bool = False,
) -> Generator[str, None, None]:
    system = config.get_system_prompt()
    if ambient_context:
        system += f"\n\n---\n{ambient_context}"

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

    # --- No tools: original streaming path (lowest latency) ---
    if not use_tools:
        with client.messages.stream(
            model=model,
            max_tokens=256,
            system=system,
            messages=[{"role": "user", "content": content}],
        ) as stream:
            for text in stream.text_stream:
                yield text
        return

    # --- Tool-enabled path: non-streaming loop, then yield final text ---
    # Claude decides whether to call tools; common case is no calls at all.
    messages: list[dict] = [{"role": "user", "content": content}]
    for _round in range(4):   # at most 3 tool rounds + 1 final answer
        response = client.messages.create(
            model=model,
            max_tokens=512,
            system=system,
            messages=messages,
            tools=_CONTEXT_TOOLS,
        )

        if response.stop_reason in ("end_turn", "max_tokens"):
            for block in response.content:
                if getattr(block, "type", "") == "text":
                    text = getattr(block, "text", "")
                    if text:
                        yield text
            return

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if getattr(block, "type", "") == "tool_use":
                    result = _execute_context_tool(block.name, block.input or {})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop reason (e.g. server-side tool already resolved)
            for block in response.content:
                if getattr(block, "type", "") == "text":
                    text = getattr(block, "text", "")
                    if text:
                        yield text
            return


# ------------------------------------------------------------------
# Multi-turn (chat window)
# ------------------------------------------------------------------

def stream_response_with_history(messages: list) -> Generator[str, None, None]:
    """
    Stream a response given a pre-built messages list including history.
    Uses CHAT_LLM_PROVIDER / CHAT_LLM_MODEL (defaults to LLM_PROVIDER / LLM_MODEL).
    messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]
    """
    _check_chat_llm_config()
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
