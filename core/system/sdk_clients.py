"""SDK client constructors with macOS-safe HTTP defaults."""
from __future__ import annotations

import os

from core.system import macos_safety

_proxy_guard_installed = False


def disable_env_proxy_lookup() -> bool:
    """Avoid macOS SystemConfiguration proxy lookup inside worker threads."""
    return (
        macos_safety.safe_mode_enabled()
        and os.environ.get("WISP_MACOS_TRUST_ENV_PROXIES") != "1"
    )


def install_proxy_guard() -> None:
    """Keep urllib proxy lookup from entering macOS SystemConfiguration."""
    global _proxy_guard_installed
    if _proxy_guard_installed or not disable_env_proxy_lookup():
        return
    import urllib.request

    urllib.request.getproxies = urllib.request.getproxies_environment
    _proxy_guard_installed = True


def httpx_client(**kwargs):
    import httpx

    install_proxy_guard()
    if disable_env_proxy_lookup():
        kwargs.setdefault("trust_env", False)
    return httpx.Client(**kwargs)


def openai_client(**kwargs):
    from openai import OpenAI

    install_proxy_guard()
    if disable_env_proxy_lookup() and "http_client" not in kwargs:
        kwargs["http_client"] = httpx_client()
    return OpenAI(**kwargs)


def anthropic_client(**kwargs):
    import anthropic

    install_proxy_guard()
    if disable_env_proxy_lookup() and "http_client" not in kwargs:
        kwargs["http_client"] = httpx_client()
    return anthropic.Anthropic(**kwargs)
