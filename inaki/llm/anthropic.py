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
persistencia y la re-inyección esperan) y Anthropic-shaped hacia la API.

Sin SDK ``anthropic`` — httpx puro, como el resto de providers del repo.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator

import httpx

from inaki.llm.base import BaseLLMProvider, ResolvedLLMConfig
from inaki.kernel.domain.llm_response import LLMResponse
from inaki.shared.errors import ConfigError, LLMError
from inaki.shared.message import Message, Role

PROVIDER_NAME = "anthropic"

logger = logging.getLogger(__name__)

# Versión del API de Anthropic. Header obligatorio ``anthropic-version``.
_ANTHROPIC_VERSION = "2023-06-01"

# ---------------------------------------------------------------------------
# Parámetros de muestreo y thinking — por qué el payload es como es
#
# ``temperature`` NO se manda. Desde Opus 4.7 (y en Sonnet 5, Opus 5, Opus 5.5,
# Fable) la API devuelve 400 ante cualquier ``temperature``/``top_p``/``top_k``
# distinto del default, y con thinking activo exige 1.0 en todos los modelos.
# Detectar la familia por el nombre del modelo para saber cuándo se puede es un
# mapeo que caduca con cada lanzamiento; se prefiere perder el ajuste fino en
# los modelos viejos que romper los nuevos.
#
# El thinking es ADAPTIVE (``{"type": "adaptive"}``): ``budget_tokens`` da 400
# en los modelos actuales. La profundidad se regula con ``output_config.effort``,
# que sale de ``reasoning_effort`` tal cual. ``display: "summarized"`` porque el
# default de los modelos nuevos es ``"omitted"`` (bloques con texto vacío).
#
# Nunca se manda ``{"type": "disabled"}``: en Opus 5.5 y Fable da 400. Ojo que
# en Sonnet 5 / Opus 5 / Opus 5.5 omitir ``thinking`` NO apaga el razonamiento:
# corre adaptive igual. Por eso los bloques ``thinking`` se preservan SIEMPRE,
# no solo cuando el operador lo pidió (ver abajo).
#
# Thinking dentro del tool loop: Anthropic exige que el turno ``assistant`` con
# ``tool_use`` vuelva con SUS bloques ``thinking`` originales (con la
# ``signature``) mientras el ``tool_result`` está pendiente, sin modificarlos.
# El adapter guarda el contenido crudo del assistant en
# ``LLMResponse.provider_content``; el tool loop lo copia al ``Message`` (campo
# opaco y transitorio, nunca persistido) y acá se re-envía literal. Los turnos
# de historial cargados de la DB no lo traen: para turnos cerrados la API acepta
# que falten los bloques thinking.
#
# Preserved thinking (Opus 5.5 / Fable 5.1, obligatorio en cuentas creadas desde
# el 2026-08-31): un bloque thinking reenviado solo vale si ``system``, ``tools``
# y los mensajes previos son byte-idénticos a cuando se produjo; si no → 400. El
# tool loop de Iñaki puede AGRANDAR el set de tools a mitad de turno (page-in,
# re-routing in-flight). Por eso el blob guarda una huella de ``system`` +
# ``tools``: si la request actual no coincide, se reenvía el assistant SIN sus
# bloques thinking (text y tool_use intactos) — el camino que la propia API
# documenta como recuperación, a costa del razonamiento de esa iteración.
# ---------------------------------------------------------------------------

_EFFORTS_VALIDOS = ("low", "medium", "high", "xhigh", "max")

# Una request con bloques tool_use/tool_result en el historial tiene que declarar
# ``tools``. Cuando el caller no pasa tools (wrap-up del kill-switch, fallback
# tras max_iterations, turno sin tools ruteadas con rastro de tools en el
# historial), esos bloques se aplanan a texto.
_TOOL_RESULT_VACIO = "(sin salida)"

# Placeholder cuando la ventana de historial arranca en un turno assistant: la
# API exige que el primer mensaje sea ``user``.
_INICIO_CONVERSACION = "(continuación de una conversación anterior)"


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


def _sanitizar_bloque(block: dict) -> dict | None:
    """Reduce un bloque de la respuesta a lo que la API acepta de vuelta.

    Los bloques conocidos se recortan a sus campos de request (la respuesta trae
    campos como ``citations: null`` que no hace falta reenviar). Los desconocidos
    se preservan tal cual: el contrato es devolver el assistant SIN cambios. Un
    ``text`` vacío se descarta (la API rechaza bloques de texto vacíos).
    """
    btype = block.get("type")
    if btype == "text":
        if not block.get("text"):
            return None
        out = {"type": "text", "text": block["text"]}
        if block.get("citations"):
            out["citations"] = block["citations"]
        return out
    if btype == "thinking":
        return {
            "type": "thinking",
            "thinking": block.get("thinking", ""),
            "signature": block.get("signature", ""),
        }
    if btype == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block.get("data", "")}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": block.get("id", ""),
            "name": block.get("name", ""),
            "input": block.get("input", {}),
        }
    return block


_BLOQUES_THINKING = ("thinking", "redacted_thinking")


def _huella_prefijo(system_prompt: str, tools: list[dict]) -> str:
    """Huella de la parte del prefijo que el tool loop puede cambiar a mitad de
    turno (``system`` y ``tools``), para saber si un thinking guardado sigue
    siendo reenviable."""
    crudo = json.dumps([system_prompt, tools], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        if not cfg.api_key:
            raise LLMError("Anthropic requiere api_key en providers.anthropic.api_key")
        effort = (cfg.reasoning_effort or "").strip().lower()
        if effort and effort not in _EFFORTS_VALIDOS:
            raise ConfigError(
                f"llm.reasoning_effort={cfg.reasoning_effort!r} no es válido para el "
                f"provider anthropic. Valores aceptados: {', '.join(_EFFORTS_VALIDOS)} "
                "(o null para no mandarlo)."
            )
        self._cfg = cfg
        self._effort = effort or None
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
    def _build_anthropic_messages(
        messages: list[Message], *, con_tools: bool, huella: str | None = None
    ) -> list[dict]:
        """Traduce los Message del dominio a la lista ``messages`` de Anthropic.

        El ``system`` NO va acá (es un param top-level del payload).

        ``con_tools=False`` aplana los bloques tool_use/tool_result a texto: la
        API rechaza una request con esos bloques si no declara ``tools``.

        ``huella`` es la de ``system``+``tools`` de ESTA request: un assistant
        guardado con otra huella se reenvía sin sus bloques thinking.
        """
        result: list[dict] = []
        for m in messages:
            if m.role in (Role.TOOL, Role.TOOL_RESULT):
                contenido = m.content or _TOOL_RESULT_VACIO
                if not con_tools:
                    texto = f"[resultado de tool {m.tool_call_id or '?'}]\n{contenido}"
                    result.append({"role": "user", "content": [{"type": "text", "text": texto}]})
                    continue
                block = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id or "",
                    "content": contenido,
                }
                if _last_is_tool_result(result):
                    result[-1]["content"].append(block)
                else:
                    result.append({"role": "user", "content": [block]})
            elif m.role == Role.ASSISTANT:
                guardado = (m.provider_content or {}).get("content")
                if con_tools and m.tool_calls and guardado:
                    # Turno del tool loop en vuelo: se devuelve LITERAL (thinking
                    # firmado incluido), como exige la API.
                    bloques = list(guardado)
                    if (m.provider_content or {}).get("huella") != huella:
                        bloques = [b for b in bloques if b.get("type") not in _BLOQUES_THINKING]
                        logger.info(
                            "Anthropic: cambiaron system/tools a mitad de turno; "
                            "reenvío el assistant sin sus bloques thinking"
                        )
                    result.append({"role": "assistant", "content": bloques})
                    continue
                blocks: list[dict] = []
                if m.content.strip():
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls or []:
                    fn = tc.get("function", {})
                    nombre = fn.get("name", "")
                    argumentos = _parse_tool_arguments(fn.get("arguments"))
                    if con_tools:
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc.get("id", ""),
                                "name": nombre,
                                "input": argumentos,
                            }
                        )
                    else:
                        texto = (
                            f"[llamada a tool {tc.get('id', '?')}] {nombre}"
                            f"({json.dumps(argumentos, ensure_ascii=False)})"
                        )
                        blocks.append({"type": "text", "text": texto})
                # Un assistant sin nada que decir no aporta y un bloque de texto
                # vacío es un 400: se omite.
                if blocks:
                    result.append({"role": "assistant", "content": blocks})
            else:
                # USER (y cualquier SYSTEM que se cuele: el real va como param aparte).
                if m.content.strip():
                    result.append(
                        {"role": "user", "content": [{"type": "text", "text": m.content}]}
                    )
        if result and result[0]["role"] != "user":
            result.insert(
                0, {"role": "user", "content": [{"type": "text", "text": _INICIO_CONVERSACION}]}
            )
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

    def _build_payload(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None,
    ) -> dict:
        """Arma el payload de ``/messages`` (ver bloque de diseño arriba).

        ``max_tokens`` es obligatorio en Anthropic y, con thinking, cubre también
        los tokens de razonamiento.
        """
        converted_tools = self._convert_tools(tools) if tools else []
        payload: dict = {
            "model": self._cfg.model,
            "max_tokens": self._cfg.max_tokens,
            "messages": self._build_anthropic_messages(
                messages,
                con_tools=bool(converted_tools),
                huella=_huella_prefijo(system_prompt, converted_tools),
            ),
        }
        if system_prompt:
            payload["system"] = system_prompt
        if self.thinking_active:
            payload["thinking"] = {"type": "adaptive", "display": "summarized"}
        if self._effort:
            payload["output_config"] = {"effort": self._effort}
        if converted_tools:
            payload["tools"] = converted_tools
        return payload

    # -- Traducción Anthropic → dominio ------------------------------------

    @staticmethod
    def _parse_content(
        content: list[dict], huella: str = ""
    ) -> tuple[list[str], list[dict], str | None, dict | None]:
        """Parsea el array ``content`` de la respuesta a (text_blocks, tool_calls,
        thinking, provider_content).

        Los tool_use se re-serializan al formato OpenAI-shaped que el tool loop
        espera (``arguments`` como JSON string). ``provider_content`` es el
        blob opaco ``{"content": [...saneado...], "huella": ...}`` para re-enviar
        literal; solo se arma cuando hay tool_use, que es el único caso en que el
        tool loop lo conserva.
        """
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
            # redacted_thinking y futuros tipos: no aportan al dominio, pero
            # viajan en provider_content.
        thinking = "\n".join(thinking_parts) if thinking_parts else None
        provider_content: dict | None = None
        if tool_calls:
            provider_content = {
                "content": [b for blk in content if (b := _sanitizar_bloque(blk)) is not None],
                "huella": huella,
            }
        return text_blocks, tool_calls, thinking, provider_content

    def _check_stop_reason(
        self, data: dict, text_blocks: list[str], tool_calls: list[dict]
    ) -> None:
        """Hace ruidosos los cortes que de otro modo llegarían como respuesta muda
        o truncada. ``end_turn``/``tool_use``/``stop_sequence`` pasan sin más."""
        stop_reason = data.get("stop_reason")
        if stop_reason == "refusal":
            details = data.get("stop_details") or {}
            raise LLMError(
                f"Anthropic rechazó la solicitud (stop_reason=refusal, "
                f"categoría={details.get('category')!r}, modelo={self._cfg.model})"
            )
        if stop_reason == "model_context_window_exceeded":
            raise LLMError(
                f"Anthropic: la conversación excede la ventana de contexto de {self._cfg.model}"
            )
        if stop_reason == "max_tokens":
            if tool_calls:
                # El último tool_use puede venir con el input truncado: ejecutarlo
                # sería actuar con argumentos que el modelo no terminó de escribir.
                raise LLMError(
                    f"Anthropic cortó la respuesta por max_tokens={self._cfg.max_tokens} "
                    "en mitad de un tool_use. Subí llm.max_tokens (con thinking activo, "
                    "el razonamiento consume el mismo presupuesto)."
                )
            logger.warning(
                "Anthropic cortó la respuesta por max_tokens=%d (texto truncado, %d bloques). "
                "Subí llm.max_tokens si pasa seguido.",
                self._cfg.max_tokens,
                len(text_blocks),
            )

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
        huella = _huella_prefijo(payload.get("system", ""), payload.get("tools", []))
        text_blocks, tool_calls, thinking, provider_content = self._parse_content(content, huella)
        self._check_stop_reason(data, text_blocks, tool_calls)
        logger.info(
            "%s", self._format_response_log("Anthropic", "\n".join(text_blocks), tool_calls)
        )
        return LLMResponse(
            text_blocks=text_blocks,
            tool_calls=tool_calls,
            thinking=thinking,
            provider_content=provider_content,
            raw=json.dumps(data, ensure_ascii=False),
        )

    async def stream(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        """Stream SSE de ``/messages``. Emite solo los ``text_delta``; el thinking
        corre igual (mismo payload que ``complete`` sin tools) pero no se muestra."""
        payload = self._build_payload(messages, system_prompt, None)
        payload["stream"] = True
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
                        etype = event.get("type")
                        if etype == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                if text := delta.get("text"):
                                    yield text
                        elif etype == "message_delta":
                            stop_reason = event.get("delta", {}).get("stop_reason")
                            if stop_reason == "refusal":
                                raise LLMError(
                                    "Anthropic rechazó la solicitud durante el stream "
                                    f"(stop_reason=refusal, modelo={self._cfg.model})"
                                )
                            if stop_reason == "max_tokens":
                                logger.warning(
                                    "Anthropic cortó el stream por max_tokens=%d",
                                    self._cfg.max_tokens,
                                )
                        elif etype == "error":
                            error = event.get("error", {})
                            raise LLMError(
                                f"Anthropic stream error ({error.get('type')}): "
                                f"{error.get('message')}"
                            )
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
