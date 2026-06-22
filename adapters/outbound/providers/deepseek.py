"""Proveedor LLM via DeepSeek API (compatible con OpenAI ``/chat/completions``).

Hereda de ``OpenAICompatibleProvider`` (payload, red, stream y manejo de errores
compartidos) y aporta SOLO lo propio de DeepSeek: el sampling thinking-aware
(``_completion_params``) y el workaround DSML, que requiere override de
``complete``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from adapters.outbound.providers.openai_compatible import OpenAICompatibleProvider
from core.domain.entities.message import Message
from core.domain.value_objects.llm_response import LLMResponse

PROVIDER_NAME = "deepseek"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workaround DSML — específico de DeepSeek (NO promover a core)
#
# Bug conocido del modelo (reportado por la comunidad): con thinking activo,
# DeepSeek a veces serializa las tool calls como markup DSML dentro de
# ``content`` en vez de usar el array ``tool_calls`` nativo. Ejemplo:
#
#   <｜｜DSML｜｜tool_calls>
#   <｜｜DSML｜｜invoke name="exchange_mail">
#   <｜｜DSML｜｜parameter name="operation" string="true">list_inbox</｜｜DSML｜｜parameter>
#   <｜｜DSML｜｜parameter name="limit" string="false">5</｜｜DSML｜｜parameter>
#   </｜｜DSML｜｜invoke>
#   </｜｜DSML｜｜tool_calls>
#
# El adapter lo normaliza al contrato del dominio (``LLMResponse`` con
# ``tool_calls`` poblado) — el core nunca ve esta basura. Es trabajo del
# adapter traducir el I/O roto del provider.
# ---------------------------------------------------------------------------

_DSML_RETRY_DELAY_SECONDS = 3.0
"""Respiro antes del único retry, cuando el DSML no fue parseable."""

# El marcador real usa FULLWIDTH VERTICAL LINE (U+FF5C ｜), no el pipe ASCII.
# Las regex no dependen de la cantidad exacta de pipes (matchean ``invoke``/
# ``parameter`` y cualquier tag que contenga ``DSML``), así que toleran drift
# de formato. La heurística de detección busca el substring "DSML" a secas:
# robusta y casi imposible de falso-positivo en este dominio (un assistant
# doméstico no escribe "DSML" en prosa).
_INVOKE_RE = re.compile(r'invoke\s+name="([^"]+)"\s*>(.*?)</[^>]*?invoke>', re.DOTALL)
_PARAM_RE = re.compile(
    r'parameter\s+name="([^"]+)"(?:\s+string="(true|false)")?[^>]*?>(.*?)</[^>]*?parameter>',
    re.DOTALL,
)
_DSML_WRAPPER_RE = re.compile(
    r"<[^>]*?DSML[^>]*?tool_calls>.*?</[^>]*?DSML[^>]*?tool_calls>", re.DOTALL
)
_DSML_TAG_RE = re.compile(r"<[^>]*?DSML[^>]*?>")
# Tag DSML colgante, sin ``>`` de cierre: el modelo lo truncó por max_tokens.
# Va hasta el final del texto (un tag sin cerrar implica que no hay más nada).
_DSML_DANGLING_RE = re.compile(r"<[^>]*?DSML.*$", re.DOTALL)


def _has_dsml(content: str) -> bool:
    """Heurística barata: ¿el content trae markup DSML?"""
    return "DSML" in content


def _coerce_param(value: str, is_string: str | None) -> object:
    """Convierte el valor textual de un parámetro DSML a su tipo nativo.

    ``string="true"`` → el modelo marcó el valor como string: se preserva tal cual.
    ``string="false"`` o ausente → se intenta coerce a bool/null/int/float/JSON,
    replicando el tipo que el modelo quiso expresar (ej. ``limit=5`` como int).
    Si ninguna coerción aplica, se devuelve el string original.
    """
    if is_string == "true":
        return value
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value


def _parse_dsml_tool_calls(content: str) -> list[dict]:
    """Parsea markup DSML a tool_calls en formato OpenAI-compatible.

    Devuelve la lista de tool_calls (``id`` + ``function.name`` +
    ``function.arguments`` como **JSON string**, que es lo que el tool loop y la
    re-serialización a la API esperan). Lista vacía si el markup no es parseable
    — el caller decide el fallback.
    """
    calls: list[dict] = []
    for idx, invoke in enumerate(_INVOKE_RE.finditer(content)):
        name = invoke.group(1)
        body = invoke.group(2)
        args: dict[str, object] = {}
        for param in _PARAM_RE.finditer(body):
            args[param.group(1)] = _coerce_param(param.group(3).strip(), param.group(2))
        calls.append(
            {
                "id": f"call_dsml_{idx}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        )
    return calls


def _strip_dsml(content: str) -> str:
    """Elimina el markup DSML dejando solo texto legible (último recurso).

    Se usa cuando el parseo falló Y el retry tampoco produjo tool_calls: al menos
    el usuario ve texto sano en vez del markup crudo. Quita primero el bloque
    ``tool_calls`` completo (well-formed) y después cualquier tag DSML suelto.
    """
    cleaned = _DSML_WRAPPER_RE.sub("", content)
    cleaned = _DSML_TAG_RE.sub("", cleaned)
    cleaned = _DSML_DANGLING_RE.sub("", cleaned)
    return cleaned.strip()


class DeepSeekProvider(OpenAICompatibleProvider):
    _provider_label = "DeepSeek"
    _default_base_url = "https://api.deepseek.com/v1"

    @property
    def thinking_active(self) -> bool:
        return self._cfg.thinking_active

    def _completion_params(self, *, stream: bool) -> dict:
        """Sampling thinking-aware.

        Con ``thinking`` activo (y fuera de stream), DeepSeek rechaza
        ``temperature``, ``top_p``, ``presence_penalty`` y ``frequency_penalty``
        → solo mandamos ``thinking: enabled`` + ``reasoning_effort``. En stream el
        thinking se desactiva siempre (el stream interactivo es para chat rápido).
        """
        params: dict = {"max_tokens": self._cfg.max_tokens}
        if self._cfg.thinking_active and not stream:
            params["thinking"] = {"type": "enabled"}
            params["reasoning_effort"] = self._cfg.reasoning_effort
        else:
            params["temperature"] = self._cfg.temperature
            params["thinking"] = {"type": "disabled"}
        return params

    async def _request_message(self, payload: dict) -> dict:
        """``message`` del choice 0. Reusa el ``_request`` (red + manejo de errores)
        de ``OpenAICompatibleProvider`` — lo comparten ``complete`` y el retry DSML."""
        data = await self._request(payload)
        return data["choices"][0]["message"]

    async def _recover_dsml(
        self, content: str, message: dict, payload: dict
    ) -> tuple[str, list[dict], dict]:
        """Recupera tool calls que DeepSeek emitió como markup DSML en ``content``.

        Estrategia (ver bloque de workaround arriba):
          1. Parsear el DSML que ya tenemos → si sale, tool_calls limpios SIN
             re-llamar al modelo (determinista, costo cero).
          2. Si el parseo falla → 1 retry tras ``_DSML_RETRY_DELAY_SECONDS``.
          3. Si el retry sigue roto → stripear el DSML y devolver texto legible.

        Devuelve ``(content, tool_calls, message)`` ya normalizados.
        """
        recovered = _parse_dsml_tool_calls(content)
        if recovered:
            logger.warning(
                "DeepSeek emitió %d tool call(s) como DSML; recuperadas por parseo",
                len(recovered),
            )
            # Conservar la narración que rodea al bloque (ej. "Voy a buscar..."):
            # va a text_blocks y el tool loop la emite antes de ejecutar las tools.
            return _strip_dsml(content), recovered, message

        # Parseo falló (markup malformado o drift de formato) → 1 retry.
        logger.warning(
            "DSML detectado pero no parseable; reintentando una vez en %.1fs",
            _DSML_RETRY_DELAY_SECONDS,
        )
        await asyncio.sleep(_DSML_RETRY_DELAY_SECONDS)
        retry = await self._request_message(payload)
        retry_content = retry.get("content") or ""
        retry_tool_calls = retry.get("tool_calls") or []

        if retry_tool_calls:
            return retry_content, retry_tool_calls, retry

        if _has_dsml(retry_content):
            recovered_retry = _parse_dsml_tool_calls(retry_content)
            if recovered_retry:
                logger.warning(
                    "Retry volvió a emitir DSML; recuperadas %d por parseo",
                    len(recovered_retry),
                )
                return _strip_dsml(retry_content), recovered_retry, retry
            logger.error("DSML persiste tras retry; devolviendo content stripeado")
            return _strip_dsml(retry_content), [], retry

        # Retry limpio (texto normal, sin DSML ni tool_calls).
        return retry_content, retry_tool_calls, retry

    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        payload = self._build_payload(messages, system_prompt, tools)
        message = await self._request_message(payload)
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []

        # Workaround DSML: si el modelo no usó el array nativo pero escupió markup
        # DSML en el texto, lo normalizamos (parse → retry → strip). Ver bloque
        # de comentario arriba. Solo aplica a DeepSeek — no es feature de core.
        if not tool_calls and _has_dsml(content):
            content, tool_calls, message = await self._recover_dsml(content, message, payload)

        reasoning = message.get("reasoning_content") or None
        logger.info("%s", self._format_response_log("DeepSeek", content, tool_calls))

        return LLMResponse(
            text_blocks=[content] if content else [],
            tool_calls=tool_calls,
            thinking=reasoning,
            raw=json.dumps(message, ensure_ascii=False),
        )
