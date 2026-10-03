"""Pluggable LLM providers. Pick one with LLM_PROVIDER (gemini | groq | ollama); LLM_FALLBACKS lists backups
that take over when it runs out of tokens (see fallback.py)."""

import httpx

from app.core.config import Settings, get_settings
from worker.llm.base import LLMError, LLMOutputError, LLMProvider, LLMQuotaError, LLMRateLimitError, Usage
from worker.llm.fallback import FallbackProvider, OnSwitch
from worker.llm.gemini import GeminiProvider
from worker.llm.groq import GroqProvider
from worker.llm.ollama import OllamaProvider

PROVIDERS = ("gemini", "groq", "ollama")


def get_provider(
    settings: Settings | None = None, *, http: httpx.AsyncClient | None = None, on_switch: OnSwitch | None = None
) -> LLMProvider:
    """LLM_PROVIDER, wrapped in a FallbackProvider when LLM_FALLBACKS names usable backups."""
    settings = settings or get_settings()
    primary = make_provider(settings.llm_provider, settings, http=http)
    backups = [
        make_provider(name, settings, http=http)
        for name in dict.fromkeys(n.strip().lower() for n in settings.llm_fallbacks.split(",") if n.strip())
        if name != primary.name and has_credentials(name, settings)
    ]
    return FallbackProvider([primary, *backups], on_switch=on_switch) if backups else primary


def has_credentials(name: str, settings: Settings) -> bool:
    return {"gemini": bool(settings.gemini_api_key), "groq": bool(settings.groq_api_key)}.get(name, name in PROVIDERS)


def make_provider(name: str, settings: Settings, *, http: httpx.AsyncClient | None = None) -> LLMProvider:
    name = name.strip().lower()
    options = {"http": http, "timeout": settings.llm_timeout_seconds, "max_retries": settings.llm_max_retries}
    hosted = options | {"min_interval": settings.llm_min_interval_seconds}
    if name == "gemini":
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model, **hosted)
    if name == "groq":
        return GroqProvider(settings.groq_api_key, settings.groq_model, **hosted)
    if name == "ollama":
        return OllamaProvider(settings.ollama_url, settings.ollama_model, **options)  # local: no rate limit
    raise LLMError(f"Unknown LLM provider {name!r}; use one of {', '.join(PROVIDERS)}")


__all__ = ["PROVIDERS", "FallbackProvider", "LLMError", "LLMOutputError", "LLMProvider", "LLMQuotaError",
           "LLMRateLimitError", "Usage", "get_provider", "make_provider"]
