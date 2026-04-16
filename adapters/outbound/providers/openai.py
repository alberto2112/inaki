"""Proveedor LLM via OpenAI API."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from adapters.outbound.providers.base import BaseLLMProvider
from core.domain.entities.message import Message
from core.domain.errors import LLMError
from core.domain.value_objects.llm_response import LLMResponse
from infrastructure.config import LLMConfig

PROVIDER_NAME = "openai"

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):

    def __init__(self, cfg: LLMConfig) -> None:
        if not cfg.api_key:
            raise LLMError("OpenAI requiere api_key en llm.api_key")
        self._cfg = cfg
        self._base_url = cfg.base_url or "https://api.openai.com/v1"
        self._headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }

    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        payload: dict = {
            "model": self._cfg.model,
            "messages": self._build_messages(messages, system_prompt),
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenAI HTTP error: {exc}") from exc

        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        logger.info("%s", self._format_response_log("OpenAI", content, tool_calls))

        return LLMResponse(
            text_blocks=[content] if content else [],
            tool_calls=tool_calls,
            raw=json.dumps(message, ensure_ascii=False),
        )

    async def stream(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self._cfg.model,
            "messages": self._build_messages(messages, system_prompt),
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.max_tokens,
            "stream": True,
        }
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=self._headers,
                    json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0]["delta"]
                            if content := delta.get("content"):
                                yield content
                        except (json.JSONDecodeError, KeyError):
                            continue
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenAI stream error: {exc}") from exc
