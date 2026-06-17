"""Provider and fallback routing helpers for LLM clients."""
from __future__ import annotations

import config
from core import secret_store


GOOGLE_OPENAI_BASE_URL  = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEEPSEEK_BASE_URL       = "https://api.deepseek.com"
OPENROUTER_BASE_URL     = "https://openrouter.ai/api/v1"
MISTRAL_BASE_URL        = "https://api.mistral.ai/v1"
XAI_BASE_URL            = "https://api.x.ai/v1"
TOGETHER_BASE_URL       = "https://api.together.ai/v1"
CEREBRAS_BASE_URL       = "https://api.cerebras.ai/v1"
OLLAMA_BASE_URL         = "http://localhost:11434/v1"


def api_key_for(provider: str) -> str:
    """Handle api key for for LLM clients routes."""
    p = provider.lower()
    if p == "groq":
        return config.GROQ_API_KEY
    if p == "openai":
        return config.OPENAI_API_KEY
    if p == "anthropic":
        return config.ANTHROPIC_API_KEY
    if p == "google":
        return config.GOOGLE_API_KEY
    if p == "chatgpt":
        return "chatgpt-oauth"
    if p == "copilot":
        return "copilot-token"
    if p == "custom":
        return config.CUSTOM_API_KEY
    if p == "deepseek":
        return config.DEEPSEEK_API_KEY
    if p == "openrouter":
        return config.OPENROUTER_API_KEY
    if p == "mistral":
        return config.MISTRAL_API_KEY
    if p == "xai":
        return config.XAI_API_KEY
    if p == "together":
        return config.TOGETHER_API_KEY
    if p == "cerebras":
        return config.CEREBRAS_API_KEY
    if p == "ollama":
        return "ollama-local"   # no real key required
    return ""


def credential_source_for_provider(provider: str) -> str:
    """Handle credential source for provider for LLM clients routes."""
    p = provider.lower()
    if p == "groq":
        return secret_store.secret_source("GROQ_API_KEY")
    if p == "openai":
        return secret_store.secret_source("OPENAI_API_KEY")
    if p == "anthropic":
        return secret_store.secret_source("ANTHROPIC_API_KEY")
    if p == "google":
        return secret_store.secret_source("GOOGLE_API_KEY")
    if p == "chatgpt":
        return "chatgpt-oauth"
    if p == "copilot":
        return "copilot-keychain"
    if p == "custom":
        return secret_store.secret_source("CUSTOM_API_KEY")
    if p == "deepseek":
        return secret_store.secret_source("DEEPSEEK_API_KEY")
    if p == "openrouter":
        return secret_store.secret_source("OPENROUTER_API_KEY")
    if p == "mistral":
        return secret_store.secret_source("MISTRAL_API_KEY")
    if p == "xai":
        return secret_store.secret_source("XAI_API_KEY")
    if p == "together":
        return secret_store.secret_source("TOGETHER_API_KEY")
    if p == "cerebras":
        return secret_store.secret_source("CEREBRAS_API_KEY")
    if p == "ollama":
        return "local"
    return "none"


def normalize_model_for_provider(provider: str, model: str) -> str:
    """Normalize a model id before it is sent to a provider.

    Google's OpenAI-compatible ``/models`` endpoint lists ids with a ``models/``
    resource prefix (e.g. ``models/gemini-2.5-flash``), but its GenerateContent
    layer re-adds that prefix and rejects an already-prefixed name with
    ``GenerateContentRequest.model: unexpected model name format``. Strip it so
    both freshly-fetched and previously-saved Google model names work.
    """
    model = (model or "").strip()
    if provider and provider.lower() == "google" and model.startswith("models/"):
        return model[len("models/"):]
    return model


def parse_model_fallbacks(raw: str) -> list[tuple[str, str]]:
    """Parse provider:model fallback lines from settings."""
    routes: list[tuple[str, str]] = []
    for part in raw.replace(";", "\n").splitlines():
        item = part.strip()
        if not item or item.startswith("#") or ":" not in item:
            continue
        provider, model = item.split(":", 1)
        provider = provider.strip().lower()
        model = model.strip()
        if provider and model:
            routes.append((provider, model))
    return routes


def route_candidates(provider: str, model: str, fallback_raw: str) -> list[tuple[str, str]]:
    """Handle route candidates for LLM clients routes."""
    routes: list[tuple[str, str]] = []
    if provider and model:
        routes.append((provider.lower(), model))
    for candidate in parse_model_fallbacks(fallback_raw):
        if candidate not in routes:
            routes.append(candidate)
    return routes
