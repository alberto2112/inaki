"""Proveedor LLM via OpenRouter API (compatible con OpenAI ``/chat/completions``)."""

from __future__ import annotations

from typing import ClassVar

from adapters.outbound.providers.base import ResolvedLLMConfig
from adapters.outbound.providers.openai_compatible import OpenAICompatibleProvider

PROVIDER_NAME = "openrouter"


class OpenRouterProvider(OpenAICompatibleProvider):
    _provider_label: ClassVar[str] = "OpenRouter"
    _default_base_url: ClassVar[str] = "https://openrouter.ai/api/v1"

    def _build_headers(self, cfg: ResolvedLLMConfig) -> dict[str, str]:
        # OpenRouter usa ``HTTP-Referer`` para atribución de la app.
        return {**super()._build_headers(cfg), "HTTP-Referer": "https://github.com/inaki"}

    def _completion_params(self, *, stream: bool) -> dict:
        return {
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.max_tokens,
        }
