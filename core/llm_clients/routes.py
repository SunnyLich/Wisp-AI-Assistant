"""Provider and fallback routing helpers for LLM clients."""
from __future__ import annotations

import config
from core import secret_store
from core.ollama_manager import OLLAMA_BASE_URL as _OLLAMA_MANAGED_BASE_URL


GOOGLE_OPENAI_BASE_URL  = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEEPSEEK_BASE_URL       = "https://api.deepseek.com"
OPENROUTER_BASE_URL     = "https://openrouter.ai/api/v1"
MISTRAL_BASE_URL        = "https://api.mistral.ai/v1"
XAI_BASE_URL            = "https://api.x.ai/v1"
TOGETHER_BASE_URL       = "https://api.together.ai/v1"
CEREBRAS_BASE_URL       = "https://api.cerebras.ai/v1"
# Shared with core.ollama_manager (honors OLLAMA_HOST) so requests, the
# readiness probe, and the auto-started server always target one endpoint.
OLLAMA_BASE_URL         = _OLLAMA_MANAGED_BASE_URL
ZAI_BASE_URL            = "https://api.z.ai/api/paas/v4"
NVIDIA_BASE_URL         = "https://integrate.api.nvidia.com/v1"
SAMBANOVA_BASE_URL      = "https://api.sambanova.ai/v1"
GITHUB_MODELS_BASE_URL  = "https://models.github.ai/inference"
HUGGINGFACE_BASE_URL    = "https://router.huggingface.co/v1"
CHUTES_BASE_URL         = "https://llm.chutes.ai/v1"
VERCEL_BASE_URL         = "https://ai-gateway.vercel.sh/v1"
FIREWORKS_BASE_URL      = "https://api.fireworks.ai/inference/v1"
COHERE_BASE_URL         = "https://api.cohere.ai/compatibility/v1"
AI21_BASE_URL           = "https://api.ai21.com/studio/v1"
NEBIUS_BASE_URL         = "https://api.studio.nebius.com/v1"


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
    if p == "zai":
        return config.ZAI_API_KEY
    if p == "nvidia":
        return config.NVIDIA_API_KEY
    if p == "sambanova":
        return config.SAMBANOVA_API_KEY
    if p == "github_models":
        return config.GITHUB_MODELS_API_KEY
    if p == "huggingface":
        return config.HUGGINGFACE_API_KEY
    if p == "chutes":
        return config.CHUTES_API_KEY
    if p == "vercel":
        return config.VERCEL_API_KEY
    if p == "fireworks":
        return config.FIREWORKS_API_KEY
    if p == "cohere":
        return config.COHERE_API_KEY
    if p == "ai21":
        return config.AI21_API_KEY
    if p == "nebius":
        return config.NEBIUS_API_KEY
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
    if p == "zai":
        return secret_store.secret_source("ZAI_API_KEY")
    if p == "nvidia":
        return secret_store.secret_source("NVIDIA_API_KEY")
    if p == "sambanova":
        return secret_store.secret_source("SAMBANOVA_API_KEY")
    if p == "github_models":
        return secret_store.secret_source("GITHUB_MODELS_API_KEY")
    if p == "huggingface":
        return secret_store.secret_source("HUGGINGFACE_API_KEY")
    if p == "chutes":
        return secret_store.secret_source("CHUTES_API_KEY")
    if p == "vercel":
        return secret_store.secret_source("VERCEL_API_KEY")
    if p == "fireworks":
        return secret_store.secret_source("FIREWORKS_API_KEY")
    if p == "cohere":
        return secret_store.secret_source("COHERE_API_KEY")
    if p == "ai21":
        return secret_store.secret_source("AI21_API_KEY")
    if p == "nebius":
        return secret_store.secret_source("NEBIUS_API_KEY")
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
