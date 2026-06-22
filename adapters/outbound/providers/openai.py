"""Proveedor LLM via OpenAI API (``/chat/completions``)."""

from __future__ import annotations

from typing import ClassVar

from adapters.outbound.providers.openai_compatible import OpenAICompatibleProvider

PROVIDER_NAME = "openai"


class OpenAIProvider(OpenAICompatibleProvider):
    _provider_label: ClassVar[str] = "OpenAI"
    _default_base_url: ClassVar[str] = "https://api.openai.com/v1"

    def _completion_params(self, *, stream: bool) -> dict:
        # OpenAI espera ``max_completion_tokens`` (no ``max_tokens``).
        return {
            "temperature": self._cfg.temperature,
            "max_completion_tokens": self._cfg.max_tokens,
        }
