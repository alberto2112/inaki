"""Proveedor LLM via OpenAI Responses API (`/v1/responses`).

A diferencia de ``/v1/chat/completions``, la Responses API:
- Usa ``input`` (estructurado) en lugar de ``messages``.
- Usa ``instructions`` separado del input para el system prompt.
- Tools tienen forma plana (sin nesting bajo ``function``).
- Response devuelve ``output`` (array de items tipados) en lugar de ``choices``.

Soporta los modelos modernos de OpenAI que solo viven en este endpoint:
``codex-*``, ``o1-*``, ``o3-*``, ``gpt-5-*``. Sin streaming — devuelve respuesta
completa en una sola llamada.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from adapters.outbound.providers.base import BaseLLMProvider
from core.domain.entities.message import Message, Role
from core.domain.errors import LLMError
from core.domain.value_objects.llm_response import LLMResponse
from infrastructure.config import ResolvedLLMConfig

PROVIDER_NAME = "openai_responses"

logger = logging.getLogger(__name__)


class OpenAIResponsesProvider(BaseLLMProvider):
    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        if not cfg.api_key:
            raise LLMError(
                "openai_responses requiere api_key en providers.openai_responses.api_key"
            )
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
            "instructions": system_prompt,
            "input": self._build_input(messages),
            "max_output_tokens": self._cfg.max_tokens,
        }
        # Modelos de razonamiento (codex, o1, o3, gpt-5 reasoning) NO aceptan
        # temperature — la API devuelve 400. Si reasoning_effort está seteado,
        # asumimos que es uno de esos modelos y omitimos temperature.
        if self._cfg.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self._cfg.reasoning_effort}
            logger.debug(
                "openai_responses: temperature omitida (reasoning_effort=%s)",
                self._cfg.reasoning_effort,
            )
        elif self._cfg.temperature is not None:
            payload["temperature"] = self._cfg.temperature
        if tools:
            payload["tools"] = self._convert_tools(tools)
            payload["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self._base_url}/responses",
                    headers=self._headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            raise LLMError(
                f"OpenAI Responses HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenAI Responses HTTP error: {exc}") from exc

        output = data.get("output", [])
        text_blocks, tool_calls = self._parse_output(output)
        logger.info(
            "%s",
            self._format_response_log(
                "OpenAI Responses",
                "\n".join(text_blocks),
                tool_calls,
            ),
        )

        return LLMResponse(
            text_blocks=text_blocks,
            tool_calls=tool_calls,
            raw=json.dumps(output, ensure_ascii=False),
        )

    async def stream(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        # Responses API soporta SSE pero no se usa: los sub-agentes se llaman
        # one-shot via complete(). Si en algún momento se quiere stream, hay
        # que parsear el SSE de "/v1/responses?stream=true" (events: response.*).
        raise LLMError("openai_responses.stream() no está implementado")
        if False:  # pragma: no cover  — satisface el tipo AsyncIterator
            yield ""

    @staticmethod
    def _build_input(messages: list[Message]) -> list[dict]:
        """Mapea historial interno → array `input` de la Responses API.

        - USER / ASSISTANT (texto)  → ``{type: message, role, content: [...]}``
        - ASSISTANT con tool_calls  → texto (si hay) + N items ``function_call``
        - TOOL                       → ``function_call_output``
        """
        result: list[dict] = []
        for m in messages:
            if m.role == Role.ASSISTANT and m.tool_calls:
                if m.content:
                    result.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": m.content}],
                        }
                    )
                for tc in m.tool_calls:
                    fn = tc.get("function", {})
                    result.append(
                        {
                            "type": "function_call",
                            "call_id": tc.get("id", ""),
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", ""),
                        }
                    )
            elif m.role == Role.TOOL:
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": m.tool_call_id or "",
                        "output": m.content,
                    }
                )
            elif m.role == Role.USER:
                result.append(
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": m.content}],
                    }
                )
            elif m.role == Role.ASSISTANT:
                result.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": m.content}],
                    }
                )
        return result

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        """Convierte tool schemas chat-completions → responses (forma plana).

        Chat completions:  ``{type: function, function: {name, description, parameters}}``
        Responses:         ``{type: function, name, description, parameters}``
        """
        converted: list[dict] = []
        for t in tools:
            if t.get("type") != "function":
                converted.append(t)
                continue
            fn = t.get("function", {})
            converted.append(
                {
                    "type": "function",
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                }
            )
        return converted

    @staticmethod
    def _parse_output(output: list[dict]) -> tuple[list[str], list[dict]]:
        """Parsea el array ``output`` de la Responses API.

        Items relevantes:
          - ``{type: message, ...}``       → text block(s)
          - ``{type: function_call, ...}`` → tool call (re-armado al formato chat)

        Otros tipos (reasoning, web_search_call, etc.) se ignoran — no aplican
        al tool loop actual.
        """
        text_blocks: list[str] = []
        tool_calls: list[dict] = []
        for item in output:
            item_type = item.get("type")
            if item_type == "message":
                for block in item.get("content", []):
                    if block.get("type") in ("output_text", "text"):
                        text = block.get("text", "")
                        if text:
                            text_blocks.append(text)
            elif item_type == "function_call":
                # Re-armar al formato chat-completions que espera el tool loop.
                tool_calls.append(
                    {
                        "id": item.get("call_id") or item.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": item.get("name", ""),
                            "arguments": item.get("arguments", ""),
                        },
                    }
                )
        return text_blocks, tool_calls
