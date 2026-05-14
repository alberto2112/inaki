"""Proveedor LLM via DeepSeek API (compatible con OpenAI)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from adapters.outbound.providers.base import BaseLLMProvider
from core.domain.entities.message import Message
from core.domain.errors import LLMError
from core.domain.value_objects.llm_response import LLMResponse
from infrastructure.config import ResolvedLLMConfig

PROVIDER_NAME = "deepseek"

logger = logging.getLogger(__name__)


class DeepSeekProvider(BaseLLMProvider):
    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        if not cfg.api_key:
            raise LLMError("DeepSeek requiere api_key en providers.deepseek.api_key")
        self._cfg = cfg
        self._base_url = cfg.base_url or "https://api.deepseek.com/v1"
        self._headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }

    @property
    def thinking_active(self) -> bool:
        return self._cfg.thinking_active

    def _build_payload(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None,
    ) -> dict:
        """Arma el payload de chat/completions con thinking-aware sampling.

        Cuando ``thinking_active``, DeepSeek rechaza ``temperature``, ``top_p``,
        ``presence_penalty`` y ``frequency_penalty`` — los omitimos.
        """
        payload: dict = {
            "model": self._cfg.model,
            "messages": self._build_messages(messages, system_prompt),
            "max_tokens": self._cfg.max_tokens,
        }
        if self._cfg.thinking_active:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = self._cfg.reasoning_effort
        else:
            payload["temperature"] = self._cfg.temperature
            payload["thinking"] = {"type": "disabled"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        payload = self._build_payload(messages, system_prompt, tools)

        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout_seconds) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            raise LLMError(f"DeepSeek HTTP {exc.response.status_code}: {body}") from exc
        except httpx.HTTPError as exc:
            # Muchas excepciones de httpx (ReadTimeout, ConnectTimeout, RemoteProtocolError)
            # tienen __str__ vacío. Incluimos el tipo para que el mensaje sea
            # accionable en logs y en el canal del usuario.
            detail = str(exc) or repr(exc)
            raise LLMError(
                f"DeepSeek HTTP error ({type(exc).__name__}, timeout={self._cfg.timeout_seconds}s): {detail}"
            ) from exc

        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        reasoning = message.get("reasoning_content") or None
        logger.info("%s", self._format_response_log("DeepSeek", content, tool_calls))

        return LLMResponse(
            text_blocks=[content] if content else [],
            tool_calls=tool_calls,
            thinking=reasoning,
            raw=json.dumps(message, ensure_ascii=False),
        )

    async def stream(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        payload: dict = {
            "model": self._cfg.model,
            "messages": self._build_messages(messages, system_prompt),
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.max_tokens,
            "stream": True,
            "thinking": {"type": "disabled"},
        }
        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout_seconds) as client:
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
        except httpx.HTTPStatusError as exc:
            await exc.response.aread()
            body = exc.response.text[:500]
            raise LLMError(f"DeepSeek HTTP {exc.response.status_code}: {body}") from exc
        except httpx.HTTPError as exc:
            detail = str(exc) or repr(exc)
            raise LLMError(
                f"DeepSeek stream error ({type(exc).__name__}, timeout={self._cfg.timeout_seconds}s): {detail}"
            ) from exc
