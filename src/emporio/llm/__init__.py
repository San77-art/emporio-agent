"""LLM provider factory."""

from __future__ import annotations

import os

from ..config import Settings
from .base import LLMProvider, LLMResponse

__all__ = ["LLMProvider", "LLMResponse", "create_provider"]


def create_provider(settings: Settings) -> LLMProvider:
    if settings.provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in (see README).")
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings.model, settings.temperature)
    if settings.provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set (any non-empty value works for local servers such as Ollama).")
        from .openai_provider import OpenAICompatProvider

        return OpenAICompatProvider(settings.model, settings.temperature)
    raise ValueError(f"Unknown provider {settings.provider!r}")  # pragma: no cover
