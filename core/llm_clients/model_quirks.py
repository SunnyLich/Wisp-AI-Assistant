"""Per-model capability quirks (image input, sampling, output-token field).

Pure, dependency-free predicates and request-kwarg shapers keyed off model
name substrings. Split out of core.llm_clients.client; re-exported there.
"""
from __future__ import annotations

# Substrings of model names known to accept image input. Best-effort only, used
# for a settings-time heads-up; unknown models are treated as text-only so we err
# toward warning. This never gates runtime behavior.
_VISION_MODEL_HINTS = (
    "gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-4-vision", "gpt-5", "chatgpt-4o",
    "o1", "o3", "o4",
    "claude", "sonnet", "opus", "haiku",
    "gemini",
    "pixtral", "mistral-small-3", "mistral-medium",
    "llama-3.2-11b", "llama-3.2-90b", "llama-4", "scout", "maverick",
    "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl",
    "internvl", "phi-3.5-vision", "phi-4-multimodal",
    "grok-2-vision", "grok-4",
    "vision", "-vl", "multimodal",
)


def _model_accepts_images(model: str) -> bool:
    """Handle model accepts images for LLM clients client."""
    m = (model or "").strip().lower()
    return any(hint in m for hint in _VISION_MODEL_HINTS)


# Model substrings whose providers reject any non-default sampling value
# (temperature / top_p): GPT-5 family + OpenAI o-series reasoning models, and the
# newest Claude models (Opus 4.7+, Fable), which removed those parameters. For
# these we omit temperature and let the model use its default.
_NO_CUSTOM_SAMPLING_HINTS = (
    "gpt-5", "o1", "o3", "o4",
    "opus-4-7", "opus-4-8", "fable",
)


def _model_rejects_custom_sampling(model: str) -> bool:
    """Handle model rejects custom sampling for LLM clients client."""
    m = (model or "").strip().lower()
    return any(hint in m for hint in _NO_CUSTOM_SAMPLING_HINTS)


def _apply_sampling(kwargs: dict, model: str, temperature: float | None) -> dict:
    """Add ``temperature`` only when the model accepts a custom one.

    Comply with each model's rules up front: GPT-5/o-series and the newest Claude
    models only accept their default sampling value and 400 on anything else, so
    we omit it for them. Models that do accept it keep the requested value. The
    reactive drop-and-retry on the OpenAI-compatible route remains the backstop
    for any model not covered by the hint list above.
    """
    if temperature is not None and not _model_rejects_custom_sampling(model):
        kwargs["temperature"] = temperature
    return kwargs


# OpenAI's GPT-5 family and o-series reasoning models reject ``max_tokens`` and
# require ``max_completion_tokens``. Only OpenAI serves these model names, so the
# substring match is effectively provider-scoped.
_MAX_COMPLETION_TOKENS_HINTS = ("gpt-5", "o1", "o3", "o4")


def _model_uses_max_completion_tokens(model: str) -> bool:
    """Handle model uses max completion tokens for LLM clients client."""
    m = (model or "").strip().lower()
    return any(hint in m for hint in _MAX_COMPLETION_TOKENS_HINTS)


def _apply_max_output(kwargs: dict, model: str, value) -> dict:
    """Set the output-token cap under the field name the model accepts.

    GPT-5 / o-series want ``max_completion_tokens``; everything else takes
    ``max_tokens``. Complying up front avoids a 400 + retry round-trip.
    """
    if value is None:
        return kwargs
    key = "max_completion_tokens" if _model_uses_max_completion_tokens(model) else "max_tokens"
    kwargs[key] = value
    return kwargs
