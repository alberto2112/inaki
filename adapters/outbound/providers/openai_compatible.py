"""Base compartida para proveedores que hablan el dialecto OpenAI ``/chat/completions``.

``openai``, ``groq``, ``openrouter`` y ``deepseek`` difieren únicamente en:
  - la URL por defecto y, a veces, headers extra (OpenRouter manda ``HTTP-Referer``);
  - los parámetros de *sampling* del payload (qué clave de ``max_tokens``, si va
    ``temperature``, ``reasoning_effort``, ``thinking``…).

Todo lo demás —ensamblado del payload, request HTTP, parseo del response, stream
SSE y manejo de errores— es idéntico y vive UNA sola vez acá (patrón **Template
Method**). Cada provider concreto solo declara ``_provider_label`` /
``_default_base_url`` y rellena ``_completion_params``; si necesita headers
extra, override ``_build_headers``.

Los providers con contrato propio NO heredan de acá —no comparten este dialecto—:
cuelgan directo de ``BaseLLMProvider`` (Anthropic ``/messages``, OpenAI Responses
``/responses``, Ollama ``/api/chat``). Meterlos bajo esta base los obligaría a
pisar casi todo: sería herencia que miente.

Sin SDK ``openai`` — httpx puro, como el resto de providers del repo.
"""

from __future__ import annotations

import json
import logging
from abc import abstractmethod
from collections.abc import AsyncIterator
from typing import ClassVar

import httpx

from adapters.outbound.providers.base import BaseLLMProvider, ResolvedLLMConfig
from core.domain.entities.message import Message
from core.domain.errors import LLMError
from core.domain.value_objects.llm_response import LLMResponse

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(BaseLLMProvider):
    """Template Method para la familia OpenAI ``/chat/completions``.

    Hooks que el provider concreto define:
      - ``_provider_label`` (ClassVar): nombre para logs y mensajes de error.
      - ``_default_base_url`` (ClassVar): endpoint si no hay override en config.
      - ``_completion_params``: parámetros de sampling propios del provider.
      - ``_build_headers`` (opcional): headers extra (default: Bearer + JSON).
    """

    # Los hijos concretos DEBEN asignar ambos. Son ClassVar (constantes de clase),
    # no estado de instancia.
    _provider_label: ClassVar[str]
    _default_base_url: ClassVar[str]

    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        if self.REQUIRES_CREDENTIALS and not cfg.api_key:
            raise LLMError(
                f"{self._provider_label} requiere api_key en providers.{cfg.provider}.api_key"
            )
        self._cfg = cfg
        self._base_url = cfg.base_url or self._default_base_url
        self._headers = self._build_headers(cfg)

    # -- Hooks de personalización ------------------------------------------

    def _build_headers(self, cfg: ResolvedLLMConfig) -> dict[str, str]:
        """Headers HTTP. Default OpenAI-compatible: Bearer + JSON.

        Override para sumar headers propios (ej. OpenRouter ``HTTP-Referer``).
        """
        return {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }

    @abstractmethod
    def _completion_params(self, *, stream: bool) -> dict:
        """Parámetros de sampling propios del provider (``temperature``, la clave
        de ``max_tokens``, ``reasoning_effort``, ``thinking``…).

        ``model``, ``messages``, ``tools`` y ``stream`` los agrega
        ``_build_payload`` de forma uniforme — acá va SOLO lo que varía entre
        providers. ``stream`` permite ajustar el sampling según el modo (p. ej.
        DeepSeek desactiva thinking al streamear).
        """
        ...

    # -- Payload + red (comunes a toda la familia) -------------------------

    def _build_payload(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
        *,
        stream: bool = False,
    ) -> dict:
        """Ensambla el payload de ``/chat/completions``: la parte común
        (model/messages/tools/stream) + los ``_completion_params`` del provider."""
        payload: dict = {
            "model": self._cfg.model,
            "messages": self._build_messages(messages, system_prompt),
            **self._completion_params(stream=stream),
        }
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    @property
    def _chat_url(self) -> str:
        return f"{self._base_url}/chat/completions"

    async def _request(self, payload: dict) -> dict:
        """POST a ``/chat/completions``; devuelve el JSON crudo. Errores → ``LLMError``.

        El mensaje de error incluye el tipo de excepción y el timeout: muchas
        ``httpx.HTTPError`` (ReadTimeout, ConnectTimeout, RemoteProtocolError)
        tienen ``__str__`` vacío y, sin esto, el operador no puede diagnosticar.
        """
        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout_seconds) as client:
                resp = await client.post(self._chat_url, headers=self._headers, json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            raise LLMError(
                f"{self._provider_label} HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            detail = str(exc) or repr(exc)
            raise LLMError(
                f"{self._provider_label} HTTP error ({type(exc).__name__}, "
                f"timeout={self._cfg.timeout_seconds}s): {detail}"
            ) from exc

    # -- API pública (ILLMProvider) ----------------------------------------

    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        data = await self._request(self._build_payload(messages, system_prompt, tools))
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        logger.info("%s", self._format_response_log(self._provider_label, content, tool_calls))
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
        payload = self._build_payload(messages, system_prompt, stream=True)
        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout_seconds) as client:
                async with client.stream(
                    "POST", self._chat_url, headers=self._headers, json=payload
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
            raise LLMError(
                f"{self._provider_label} HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            detail = str(exc) or repr(exc)
            raise LLMError(
                f"{self._provider_label} stream error ({type(exc).__name__}, "
                f"timeout={self._cfg.timeout_seconds}s): {detail}"
            ) from exc
