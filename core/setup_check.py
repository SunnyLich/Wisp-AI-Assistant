"""Lightweight setup checks safe to run from the Settings UI."""
from __future__ import annotations

from typing import Any

from core.error_recommendations import recommendation_for


def _status(ok: bool, warning: bool = False) -> str:
    if ok:
        return "pass"
    return "warn" if warning else "fail"


def _secret_for_provider(config: Any, provider: str) -> str:
    mapping = {
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "xai": "XAI_API_KEY",
        "together": "TOGETHER_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
        "zai": "ZAI_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "sambanova": "SAMBANOVA_API_KEY",
        "github_models": "GITHUB_MODELS_API_KEY",
        "huggingface": "HUGGINGFACE_API_KEY",
        "chutes": "CHUTES_API_KEY",
        "vercel": "VERCEL_API_KEY",
        "fireworks": "FIREWORKS_API_KEY",
        "cohere": "COHERE_API_KEY",
        "ai21": "AI21_API_KEY",
        "nebius": "NEBIUS_API_KEY",
        "custom": "CUSTOM_API_KEY",
    }
    key_name = mapping.get(provider.lower(), "")
    return str(getattr(config, key_name, "") or "") if key_name else ""


def run_setup_check() -> list[dict[str, str]]:
    """Return setup check rows without importing audio, STT, or provider SDKs."""
    import config

    config.reload()
    rows: list[dict[str, str]] = []

    provider = str(getattr(config, "LLM_PROVIDER", "") or "").strip()
    model = str(getattr(config, "LLM_MODEL", "") or "").strip()
    llm_ok = bool(provider and model and (_secret_for_provider(config, provider) or provider in {"copilot", "chatgpt", "ollama"}))
    llm_message = (
        f"LLM route configured: {provider}/{model}."
        if llm_ok
        else f"LLM route incomplete: {provider or 'missing provider'}/{model or 'missing model'}."
    )
    rows.append(
        {
            "name": "LLM provider",
            "status": _status(llm_ok),
            "message": llm_message,
            "recommendation": "" if llm_ok else recommendation_for("missing API key"),
        }
    )

    tts_provider = str(getattr(config, "TTS_PROVIDER", "none") or "none").strip().lower()
    tts_ok = tts_provider == "none"
    tts_recommendation = ""
    if tts_provider == "cartesia":
        tts_ok = bool(getattr(config, "CARTESIA_API_KEY", ""))
    elif tts_provider == "elevenlabs":
        has_key = bool(getattr(config, "ELEVENLABS_API_KEY", ""))
        try:
            from core import optional_deps

            has_package = optional_deps.is_importable("elevenlabs")
        except Exception:
            has_package = False
        tts_ok = has_key and has_package
        if has_key and not has_package:
            tts_recommendation = (
                "Recommendation: ElevenLabs support is not installed. Open Settings > Voice and click "
                "Install ElevenLabs, or rebuild from a shorter path."
            )
    elif tts_provider == "openai":
        tts_ok = bool(getattr(config, "OPENAI_API_KEY", ""))
    elif tts_provider == "openai_compatible":
        tts_ok = bool(getattr(config, "TTS_CUSTOM_BASE_URL", ""))
    elif tts_provider == "gpt_sovits":
        tts_ok = bool(
            getattr(config, "GPT_SOVITS_URL", "")
            and getattr(config, "GPT_SOVITS_REF_AUDIO_PATH", "")
        )
    elif tts_provider == "kokoro":
        tts_ok = bool(getattr(config, "KOKORO_VOICE", ""))
    rows.append(
        {
            "name": "TTS",
            "status": _status(tts_ok, warning=tts_provider == "none"),
            "message": "TTS is off." if tts_provider == "none" else f"TTS provider configured: {tts_provider}.",
            "recommendation": "" if tts_ok else (tts_recommendation or recommendation_for("tts no audio")),
        }
    )

    stt_model = str(getattr(config, "STT_MODEL", "") or "").strip()
    stt_package_ok = False
    stt_import_error = ""
    stt_recommendation = ""
    if stt_model:
        try:
            from core import optional_deps

            stt_status = optional_deps.stt_runtime_import_status_subprocess()
            stt_package_ok = bool(stt_status.get("installed") and stt_status.get("valid"))
            stt_import_error = str(stt_status.get("error") or "").strip()
        except Exception:
            stt_package_ok = False
            stt_import_error = ""
        if not stt_package_ok:
            stt_recommendation = (
                "Recommendation: STT support is not working. Open Settings > Voice and click "
                "Install STT."
            )
    rows.append(
        {
            "name": "Speech to text",
            "status": "pass" if not stt_model else _status(stt_package_ok),
            "message": (
                f"STT model configured: {stt_model}. faster-whisper is installed."
                if stt_model and stt_package_ok
                else f"STT model configured: {stt_model}, but faster-whisper failed to import: {stt_import_error}"
                if stt_model and stt_import_error
                else f"STT model configured: {stt_model}, but faster-whisper is not installed."
                if stt_model
                else "STT is not configured; voice and dictation can stay off."
            ),
            "recommendation": (
                ""
                if not stt_model or stt_package_ok
                else stt_recommendation
            ),
        }
    )

    hotkeys = [
        str(getattr(config, "HOTKEY_SNIP", "") or ""),
        str(getattr(config, "HOTKEY_VOICE", "") or ""),
        str(getattr(config, "HOTKEY_ADD_CONTEXT", "") or ""),
    ]
    caller_rows = getattr(config, "CALLER_ROWS", []) or []
    hotkeys.extend(str(row.get("hotkey") or "") for row in caller_rows if isinstance(row, dict))
    enabled_hotkeys = [key for key in hotkeys if key.strip()]
    rows.append(
        {
            "name": "Hotkeys",
            "status": _status(bool(enabled_hotkeys)),
            "message": f"{len(enabled_hotkeys)} hotkeys configured.",
            "recommendation": "" if enabled_hotkeys else recommendation_for("hotkey conflict"),
        }
    )
    return rows
