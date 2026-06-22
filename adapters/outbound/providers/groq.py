"""Proveedor LLM via Groq API (compatible con OpenAI ``/chat/completions``)."""

from __future__ import annotations

from typing import ClassVar

from adapters.outbound.providers.openai_compatible import OpenAICompatibleProvider

PROVIDER_NAME = "groq"


class GroqProvider(OpenAICompatibleProvider):
    _provider_label: ClassVar[str] = "Groq"
    _default_base_url: ClassVar[str] = "https://api.groq.com/openai/v1"

    def _completion_params(self, *, stream: bool) -> dict:
        # Groq usa ``max_completion_tokens`` solo con reasoning; los modelos
        # clásicos esperan ``max_tokens``.
        token_key = "max_completion_tokens" if self._cfg.reasoning_effort else "max_tokens"
        params: dict = {
            "temperature": self._cfg.temperature,
            token_key: self._cfg.max_tokens,
        }
        if self._cfg.reasoning_effort:
            params["reasoning_effort"] = self._cfg.reasoning_effort
        return params
