"""Proveedor LLM via Groq API (compatible con OpenAI)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from adapters.outbound.providers.base import BaseLLMProvider
from core.domain.entities.message import Message, Role
from core.domain.errors import LLMError
from infrastructure.config import LLMConfig

PROVIDER_NAME = "groq"

logger = logging.getLogger(__name__)


class GroqProvider(BaseLLMProvider):

    def __init__(self, cfg: LLMConfig) -> None:
        if not cfg.api_key:
            raise LLMError("Groq requiere api_key en llm.api_key")
        self._cfg = cfg
        self._base_url = cfg.base_url or "https://api.groq.com/openai/v1"
        self._headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }

    def _build_messages(self, messages: list[Message], system_prompt: str) -> list[dict]:
        result = [{"role": "system", "content": system_prompt}]
        for m in messages:
            if m.role in (Role.USER, Role.ASSISTANT):
                result.append({"role": m.role.value, "content": m.content})
        return result

    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> str:
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
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"Groq HTTP error: {exc}") from exc

        choice = data["choices"][0]
        message = choice["message"]
        logger.debug(
            "Groq raw message keys=%s, tool_calls=%s, content_preview=%.200s",
            list(message.keys()),
            bool(message.get("tool_calls")),
            message.get("content", "")[:200],
        )

        if message.get("tool_calls"):
            return json.dumps({"tool_calls": message["tool_calls"]})

        return message.get("content", "")

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
            async with httpx.AsyncClient(timeout=60) as client:
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
            raise LLMError(f"Groq stream error: {exc}") from exc
