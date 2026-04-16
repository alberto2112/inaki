"""Proveedor LLM via Ollama (API local compatible con OpenAI)."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator

import httpx

from adapters.outbound.providers.base import BaseLLMProvider
from core.domain.entities.message import Message
from core.domain.errors import LLMError
from core.domain.value_objects.llm_response import LLMResponse
from infrastructure.config import LLMConfig

PROVIDER_NAME = "ollama"

logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):

    def __init__(self, cfg: LLMConfig) -> None:
        self._cfg = cfg
        self._base_url = cfg.base_url or "http://localhost:11434"

    def _build_messages(self, messages: list[Message], system_prompt: str) -> list[dict]:
        """Construye el payload de mensajes para Ollama.

        Hereda la lógica del base (que ya soporta ASSISTANT+tool_calls y rol TOOL
        en formato OpenAI) y luego DESNORMALIZA ``tool_calls[].function.arguments``
        de string JSON a dict, porque Ollama nativo espera dict — no string.
        Sin esta conversión, el parser de Ollama tropieza con las llaves embebidas
        en el string y devuelve 400 ("Value looks like object, but can't find closing '}'").
        """
        result = super()._build_messages(messages, system_prompt)
        for msg in result:
            for tc in msg.get("tool_calls") or []:
                args = tc.get("function", {}).get("arguments")
                if isinstance(args, str):
                    try:
                        tc["function"]["arguments"] = json.loads(args)
                    except json.JSONDecodeError:
                        # Si no parsea, lo dejamos: Ollama devolverá un error claro.
                        pass
        return result

    @staticmethod
    def _normalize_tool_calls(raw_tool_calls: list[dict]) -> list[dict]:
        """Normaliza tool_calls de Ollama al formato OpenAI-compatible.

        Ollama nativo no devuelve ``id`` y entrega ``arguments`` como dict.
        El resto del sistema (tool loop, ``Message.tool_call_id``) asume formato
        OpenAI: ``id`` presente y ``arguments`` como string JSON.
        """
        normalized: list[dict] = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            normalized.append({
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": fn.get("name", ""),
                    "arguments": args,
                },
            })
        return normalized

    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        payload: dict = {
            "model": self._cfg.model,
            "messages": self._build_messages(messages, system_prompt),
            "stream": False,
            "options": {
                "temperature": self._cfg.temperature,
                "num_predict": self._cfg.max_tokens,
            },
        }
        if tools:
            payload["tools"] = tools

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            raise LLMError(
                f"Ollama HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama HTTP error: {exc}") from exc

        message = data.get("message", {})
        content = message.get("content") or ""
        tool_calls = self._normalize_tool_calls(message.get("tool_calls") or [])

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
            "stream": True,
            "options": {
                "temperature": self._cfg.temperature,
                "num_predict": self._cfg.max_tokens,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/api/chat",
                    json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                            if content := chunk.get("message", {}).get("content"):
                                yield content
                            if chunk.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama stream error: {exc}") from exc
