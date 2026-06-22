"""Proveedor LLM nativo de Anthropic (Claude) — endpoint ``/v1/messages``.

A diferencia de OpenAI/DeepSeek/Groq (todos OpenAI-shaped), Anthropic tiene un
contrato propio que este adapter traduce en AMBOS sentidos:

  Dominio (OpenAI-shaped)            Anthropic (Messages API)
  --------------------------------   ------------------------------------------
  system como Message role=system    ``system`` param top-level (string)
  roles user/assistant/tool          SOLO user/assistant; tool_result va dentro
                                      de un mensaje ``user`` como content block
  tool_calls: [{id, function:{...}}] content block ``{type: tool_use, id, name,
                                      input}`` dentro del assistant
  tool result: role=tool +           content block ``{type: tool_result,
  tool_call_id                        tool_use_id, content}`` dentro de un user
  tools: {type:function,             ``{name, description, input_schema}``
  function:{name,desc,parameters}}

El adapter habla OpenAI-shaped hacia el dominio (lo que el tool loop, la
persistencia y la re-inyección esperan) y Anthropic-shaped hacia la API. Es el
mismo rol que cumple el workaround DSML de DeepSeek: traducir el I/O del
provider al contrato del dominio sin filtrar la basura al core.

Sin SDK ``anthropic`` — httpx puro, como el resto de providers del repo.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from adapters.outbound.providers.base import BaseLLMProvider, ResolvedLLMConfig
from core.domain.entities.message import Message, Role
from core.domain.errors import LLMError
from core.domain.value_objects.llm_response import LLMResponse

PROVIDER_NAME = "anthropic"

logger = logging.getLogger(__name__)

# Versión del API de Anthropic. Header obligatorio ``anthropic-version``.
_ANTHROPIC_VERSION = "2023-06-01"

# ---------------------------------------------------------------------------
# Extended thinking — por qué se desactiva cuando hay tools (decisión de diseño)
#
# Anthropic exige que, con thinking activo, los turnos ``assistant`` que llevan
# ``tool_use`` devuelvan SU bloque ``thinking`` ORIGINAL con la ``signature``
# (un token que emite la API). Si no se reenvía → error 400.
#
# El dominio modela ``Message.thinking`` como un string SIN signature, a
# propósito (el resto del sistema lo trata como transitorio y descartable; ver
# llm_response.py). Persistir la signature obligaría a meter un campo
# Anthropic-específico en el ``Message`` del core → rompe la pureza hexagonal.
#
# Salida correcta SIN contaminar el core: habilitar thinking SOLO cuando NO hay
# tools. En turnos sin tool_use, Anthropic permite descartar los bloques
# thinking del historial (no requieren signature), así que el chat puro y las
# llamadas internas (extractor/reconciliador de memoria) sí razonan. En el tool
# loop se usa sampling estándar. Si en el futuro se quiere thinking durante tool
# use, el camino es agregar un campo transitorio ``thinking_signature`` al
# Message — NO se hace en v1.
# ---------------------------------------------------------------------------

# reasoning_effort → budget_tokens del thinking. Los valores que activan
# thinking salen de ``ResolvedLLMConfig.thinking_active`` (None/""/"low" → off).
_THINKING_BUDGET_BY_EFFORT: dict[str, int] = {
    "medium": 4096,
    "high": 8192,
    "max": 16384,
}
_DEFAULT_THINKING_BUDGET = 8192
_MIN_THINKING_BUDGET = 1024  # mínimo que exige Anthropic
_THINKING_RESPONSE_MARGIN = 512  # tokens reservados para la respuesta tras el thinking


def _parse_tool_arguments(raw: object) -> dict:
    """Convierte los ``arguments`` de una tool_call (JSON string OpenAI-shaped)
    al dict ``input`` que espera Anthropic. Tolera dict ya parseado y vacíos."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _last_is_tool_result(messages: list[dict]) -> bool:
    """¿El último mensaje acumulado es un ``user`` de tool_results?

    Anthropic exige que múltiples tool_result (cuando el assistant pidió varias
    tools en un turno) vayan AGRUPADOS en un único mensaje ``user``. Esto detecta
    ese caso para appendear el block en vez de abrir un mensaje nuevo.
    """
    if not messages:
        return False
    last = messages[-1]
    content = last.get("content")
    return (
        last.get("role") == "user"
        and isinstance(content, list)
        and bool(content)
        and content[0].get("type") == "tool_result"
    )


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        if not cfg.api_key:
            raise LLMError("Anthropic requiere api_key en providers.anthropic.api_key")
        self._cfg = cfg
        self._base_url = cfg.base_url or "https://api.anthropic.com/v1"
        self._headers = {
            "x-api-key": cfg.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    @property
    def thinking_active(self) -> bool:
        return self._cfg.thinking_active

    # -- Traducción dominio → Anthropic ------------------------------------

    @staticmethod
    def _build_anthropic_messages(messages: list[Message]) -> list[dict]:
        """Traduce los Message del dominio a la lista ``messages`` de Anthropic.

        El ``system`` NO va acá (es un param top-level del payload). Los bloques
        thinking NO se reconstruyen: no tenemos signature y, en turnos sin tool
        use, Anthropic permite descartarlos (ver bloque de diseño arriba).
        """
        result: list[dict] = []
        for m in messages:
            if m.role in (Role.TOOL, Role.TOOL_RESULT):
                block = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id or "",
                    "content": m.content,
                }
                if _last_is_tool_result(result):
                    result[-1]["content"].append(block)
                else:
                    result.append({"role": "user", "content": [block]})
            elif m.role == Role.ASSISTANT:
                blocks: list[dict] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls or []:
                    fn = tc.get("function", {})
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": fn.get("name", ""),
                            "input": _parse_tool_arguments(fn.get("arguments")),
                        }
                    )
                # Anthropic no acepta content vacío: garantizamos al menos un block.
                if not blocks:
                    blocks.append({"type": "text", "text": ""})
                result.append({"role": "assistant", "content": blocks})
            else:
                # USER (y cualquier SYSTEM que se cuele: el real va como param aparte).
                result.append({"role": "user", "content": [{"type": "text", "text": m.content}]})
        return result

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        """Tools OpenAI-shaped → formato Anthropic (``input_schema`` plano)."""
        converted: list[dict] = []
        for t in tools:
            if t.get("type") != "function":
                continue
            fn = t.get("function", {})
            converted.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
                }
            )
        return converted

    def _thinking_budget(self) -> int:
        """budget_tokens del extended thinking, derivado de reasoning_effort y
        capeado a los límites de Anthropic (``1024 <= budget < max_tokens``)."""
        effort = (self._cfg.reasoning_effort or "").strip().lower()
        target = _THINKING_BUDGET_BY_EFFORT.get(effort, _DEFAULT_THINKING_BUDGET)
        ceiling = self._cfg.max_tokens - _THINKING_RESPONSE_MARGIN
        return max(_MIN_THINKING_BUDGET, min(target, ceiling))

    def _build_payload(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None,
    ) -> dict:
        """Arma el payload de ``/messages``.

        ``max_tokens`` es obligatorio en Anthropic. El thinking se activa solo
        cuando NO hay tools (ver bloque de diseño); con thinking, Anthropic exige
        ``temperature=1``.
        """
        payload: dict = {
            "model": self._cfg.model,
            "max_tokens": self._cfg.max_tokens,
            "messages": self._build_anthropic_messages(messages),
        }
        if system_prompt:
            payload["system"] = system_prompt

        if self.thinking_active and not tools:
            payload["thinking"] = {"type": "enabled", "budget_tokens": self._thinking_budget()}
            payload["temperature"] = 1.0  # Anthropic exige temp=1 con extended thinking
        else:
            payload["temperature"] = self._cfg.temperature

        if tools:
            payload["tools"] = self._convert_tools(tools)
        return payload

    # -- Traducción Anthropic → dominio ------------------------------------

    @staticmethod
    def _parse_content(content: list[dict]) -> tuple[list[str], list[dict], str | None]:
        """Parsea el array ``content`` de la respuesta a (text_blocks, tool_calls,
        thinking). Los tool_use se re-serializan al formato OpenAI-shaped que el
        tool loop espera (``arguments`` como JSON string)."""
        text_blocks: list[str] = []
        tool_calls: list[dict] = []
        thinking_parts: list[str] = []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                if text := block.get("text"):
                    text_blocks.append(text)
            elif btype == "thinking":
                if t := block.get("thinking"):
                    thinking_parts.append(t)
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    }
                )
            # redacted_thinking y futuros tipos → se ignoran silenciosamente.
        thinking = "\n".join(thinking_parts) if thinking_parts else None
        return text_blocks, tool_calls, thinking

    async def _request(self, payload: dict) -> dict:
        """POST a ``/messages``; devuelve el JSON crudo. Envuelve errores en LLMError."""
        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout_seconds) as client:
                resp = await client.post(
                    f"{self._base_url}/messages",
                    headers=self._headers,
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            raise LLMError(f"Anthropic HTTP {exc.response.status_code}: {body}") from exc
        except httpx.HTTPError as exc:
            detail = str(exc) or repr(exc)
            raise LLMError(
                f"Anthropic HTTP error ({type(exc).__name__}, "
                f"timeout={self._cfg.timeout_seconds}s): {detail}"
            ) from exc

    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        payload = self._build_payload(messages, system_prompt, tools)
        data = await self._request(payload)
        content = data.get("content") or []
        text_blocks, tool_calls, thinking = self._parse_content(content)
        logger.info(
            "%s", self._format_response_log("Anthropic", "\n".join(text_blocks), tool_calls)
        )
        return LLMResponse(
            text_blocks=text_blocks,
            tool_calls=tool_calls,
            thinking=thinking,
            raw=json.dumps(data, ensure_ascii=False),
        )

    async def stream(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        """Stream SSE de ``/messages``. Emite solo los ``text_delta`` (sin
        extended thinking; el stream interactivo es para chat rápido)."""
        payload: dict = {
            "model": self._cfg.model,
            "max_tokens": self._cfg.max_tokens,
            "temperature": self._cfg.temperature,
            "messages": self._build_anthropic_messages(messages),
            "stream": True,
        }
        if system_prompt:
            payload["system"] = system_prompt
        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout_seconds) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/messages",
                    headers=self._headers,
                    json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if not data_str:
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                if text := delta.get("text"):
                                    yield text
        except httpx.HTTPStatusError as exc:
            await exc.response.aread()
            body = exc.response.text[:500]
            raise LLMError(f"Anthropic HTTP {exc.response.status_code}: {body}") from exc
        except httpx.HTTPError as exc:
            detail = str(exc) or repr(exc)
            raise LLMError(
                f"Anthropic stream error ({type(exc).__name__}, "
                f"timeout={self._cfg.timeout_seconds}s): {detail}"
            ) from exc
