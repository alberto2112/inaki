"""
run_tool_loop — helper compartido para el loop de tool calls.

Delegation-agnostic: no sabe nada de depth, recursión ni delegación.
Solo ejecuta el loop LLM ↔ tools hasta obtener respuesta final o
alcanzar el límite de iteraciones.

Usado por:
- RunAgentUseCase (conversational)
- RunAgentOneShotUseCase (one-shot / delegation child)

Contrato con el provider:
- ``ILLMProvider.complete()`` devuelve ``LLMResponse`` con ``text_blocks``
  y ``tool_calls`` separados. Si ``tool_calls`` está vacío → la respuesta
  es final y retornamos el texto. Si hay tool_calls, ejecutamos las tools
  y seguimos iterando.
"""

from __future__ import annotations

import json
import logging

from core.domain.entities.message import Message, Role
from core.domain.errors import ToolLoopMaxIterationsError
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.intermediate_sink_port import (
    IIntermediateSink,
    NullIntermediateSink,
)
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.scope_registry_port import Scope
from core.ports.outbound.tool_port import IToolExecutor

logger = logging.getLogger(__name__)


async def _drain_new_user_messages(
    history_store: IHistoryStore | None,
    scope: Scope | None,
    initial_user_count: int,
    already_drained: int,
) -> list[Message]:
    """Devuelve mensajes ``role=user`` aparecidos en history.db tras iniciar el loop.

    Calcula la diferencia entre los user-messages que había en history al arrancar
    el turno (``initial_user_count``), los que ya drené en checkpoints previos
    (``already_drained``), y los que hay AHORA en history. La diferencia son los
    nuevos — los devuelve en orden (los últimos N user-messages de history).

    Si ``history_store`` o ``scope`` son ``None``, el loop corre en modo legacy
    (sin in-flight injection) — devuelve lista vacía sin tocar la DB.

    Es seguro contra el caso "history no creció": si por algún motivo
    ``fresh_count <= initial + already_drained``, devuelve ``[]``.
    """
    if history_store is None or scope is None:
        return []
    fresh = await history_store.load(*scope)
    fresh_user_count = sum(1 for m in fresh if m.role == Role.USER)
    new_count = fresh_user_count - initial_user_count - already_drained
    if new_count <= 0:
        return []
    # history.db es append-only durante el turno, así que los nuevos son los
    # últimos N mensajes role=user de la lista cargada.
    user_messages = [m for m in fresh if m.role == Role.USER]
    return user_messages[-new_count:]


async def run_tool_loop(
    *,
    llm: ILLMProvider,
    tools: IToolExecutor,
    messages: list[Message],
    system_prompt: str,
    tool_schemas: list[dict],
    max_iterations: int,
    circuit_breaker_threshold: int,
    agent_id: str,
    intermediate_sink: IIntermediateSink | None = None,
    thinking_indicator: bool = False,
    history_store: IHistoryStore | None = None,
    scope: Scope | None = None,
    initial_db_user_count: int | None = None,
) -> str:
    """
    Ejecuta el loop LLM + tool-dispatch hasta obtener respuesta final o
    alcanzar `max_iterations`.

    Args:
        llm: Proveedor LLM (ILLMProvider).
        tools: Ejecutor de tools (IToolExecutor).
        messages: Historial de mensajes de entrada (no se muta el original).
        system_prompt: Prompt de sistema a pasar al LLM.
        tool_schemas: Schemas de tools disponibles para el LLM.
        max_iterations: Límite de iteraciones del loop.
        circuit_breaker_threshold: Número de fallos de una tool antes de abrir el circuit breaker.
        agent_id: ID del agente (solo para logging).
        history_store: Si se provee junto con ``scope``, el loop drena mensajes
            ``role=user`` aparecidos en history.db entre iteraciones (feature
            ``in-flight-message-injection``). Cuando hay drain no-vacío, el
            contador de iteraciones se resetea a 0. Default ``None`` →
            comportamiento legacy (loop ciego al historial externo).
        scope: Tupla ``(agent_id, channel, chat_id)`` del turno. Requerido junto
            con ``history_store`` para activar la drainage. Default ``None``.
        initial_db_user_count: Cantidad de mensajes ``role=user`` que YA están
            en history.db al inicio del turno. Si se provee, se usa como baseline
            del drain en vez de contar desde ``messages``. Esto es necesario
            cuando ``messages`` viene coalesced (``_coalesce_consecutive_same_role``)
            y su conteo NO refleja la realidad de la DB — sin este parámetro, el
            drain re-introduce mensajes que ya están dentro del bloque coalesced,
            produciendo duplicación visible al LLM. Default ``None`` →
            fallback al conteo desde ``messages`` (correcto cuando no hay coalesce).

    Returns:
        El texto de respuesta final del LLM (sin tool calls).

    Raises:
        ToolLoopMaxIterationsError: Si se alcanzan `max_iterations` sin obtener
            respuesta final. El atributo `.last_response` contiene el último texto
            del LLM en ese momento.
    """
    sink: IIntermediateSink = intermediate_sink or NullIntermediateSink()
    working_messages = list(messages)
    failure_counts: dict[str, int] = {}
    tripped: set[str] = set()
    last_text: str = ""

    # Baseline para detectar mensajes role=user que aparezcan en history.db
    # mientras este loop está corriendo (in-flight-message-injection). Si el
    # caller no pasó history_store/scope, estas variables existen pero el
    # helper _drain_new_user_messages no las usa (devuelve [] inmediato).
    #
    # `initial_db_user_count` permite al caller pasar el conteo real de la DB
    # cuando `messages` está coalesced (modo history-derived). Fallback al
    # conteo desde messages para callers legacy (run_agent_one_shot).
    initial_user_count = (
        initial_db_user_count
        if initial_db_user_count is not None
        else sum(1 for m in messages if m.role == Role.USER)
    )
    already_drained = 0

    # Indicador "Thinking..." una sola vez por turno cuando el provider activa
    # thinking mode y el operador lo habilitó via ``channels.thinking_indicator``.
    # Es feedback efímero para el canal — no persiste en DB, no se broadcastea.
    # Si el sink es Null (CLI sin streaming) no se ve.
    if llm.thinking_active and thinking_indicator:
        await sink.emit("Thinking...")

    iteration = 0
    while iteration < max_iterations:
        # Checkpoint A — drenar antes de llamar al LLM. Cualquier mensaje
        # role=user que el inbound adapter haya persistido en history mientras
        # estábamos ejecutando tools en la iteración previa se incorpora ahora
        # a working_messages y el LLM lo ve en la próxima llamada.
        drained = await _drain_new_user_messages(
            history_store, scope, initial_user_count, already_drained
        )
        if drained:
            working_messages.extend(drained)
            already_drained += len(drained)
            logger.info(
                "[in-flight] drain checkpoint=A count=%d agent_id=%s iter_reset_from=%d",
                len(drained),
                agent_id,
                iteration,
            )
            iteration = 0

        response = await llm.complete(
            working_messages,
            system_prompt,
            tools=tool_schemas if tool_schemas else None,
        )
        last_text = response.text

        if not response.tool_calls:
            return response.text

        # Iteración con tool calls. El assistant puede haber emitido texto
        # narrando lo que va a hacer ("ok, voy a buscar esto...") junto
        # con los tool_calls en la MISMA respuesta. Ese texto se empuja al
        # sink inbound ANTES de ejecutar las tools para que el usuario vea
        # progreso en vivo, y se preserva también en el mensaje assistant
        # del contexto para que el propio LLM lo vea en la siguiente
        # iteración.
        for block in response.text_blocks:
            if block.strip():
                await sink.emit(block)
        working_messages.append(
            Message(
                role=Role.ASSISTANT,
                content=response.text,
                tool_calls=response.tool_calls,
                thinking=response.thinking,
            )
        )

        for tc in response.tool_calls:
            tc_id = tc.get("id", "")
            tool_name = tc.get("function", {}).get("name", "")
            args_raw = tc.get("function", {}).get("arguments", "{}")
            try:
                kwargs = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                kwargs = {}

            if tool_name in tripped:
                logger.warning("Circuit breaker abierto para '%s' — llamada bloqueada", tool_name)
                working_messages.append(
                    Message(
                        role=Role.TOOL,
                        content=(
                            f"CIRCUIT OPEN — esta tool ya falló "
                            f"{circuit_breaker_threshold} vez/veces en este turno. "
                            "NO la vuelvas a llamar. Respondé al usuario con lo que "
                            "sabés, o pedile ayuda para resolver el bloqueo."
                        ),
                        tool_call_id=tc_id,
                    )
                )
                continue

            result = await tools.execute(tool_name, **kwargs)
            working_messages.append(
                Message(
                    role=Role.TOOL,
                    content=result.output,
                    tool_call_id=tc_id,
                )
            )
            logger.info(
                "Tool '%s' ejecutada: success=%s, kwargs=%.200s, output=%.200s",
                tool_name,
                result.success,
                str(kwargs),
                result.output or "",
            )

            if result.success:
                failure_counts[tool_name] = 0
            elif not result.retryable:
                failure_counts[tool_name] = failure_counts.get(tool_name, 0) + 1
                if failure_counts[tool_name] >= circuit_breaker_threshold:
                    tripped.add(tool_name)
                    logger.warning(
                        "Circuit breaker DISPARADO para '%s' tras %d fallos no-retryable",
                        tool_name,
                        failure_counts[tool_name],
                    )

        # Checkpoint B — drenar después de que TODO el batch de tool_calls esté
        # en working_messages. Si entre el LLM call y aquí el usuario mandó un
        # mensaje nuevo, lo vemos ahora y la próxima iteración del while lo
        # incorpora a la siguiente llamada al LLM.
        #
        # IMPORTANTE: nunca drenamos en medio del for-tc (entre tool calls
        # individuales). Eso violaría el contrato de los providers: la API de
        # OpenAI/DeepSeek requiere que todos los tool_result de un mismo
        # assistant(tool_calls) lleguen juntos antes del próximo mensaje.
        drained = await _drain_new_user_messages(
            history_store, scope, initial_user_count, already_drained
        )
        if drained:
            working_messages.extend(drained)
            already_drained += len(drained)
            logger.info(
                "[in-flight] drain checkpoint=B count=%d agent_id=%s iter_reset_from=%d",
                len(drained),
                agent_id,
                iteration,
            )
            iteration = 0
            continue  # no incrementar: ya reseteamos, arrancamos iteración 1

        iteration += 1

    logger.warning("Máximo de iteraciones de tool calls alcanzado para '%s'", agent_id)

    # Recuperación: si la última iteración fue tool-only (sin texto), hacemos
    # una llamada final SIN tools para forzar al LLM a producir una explicación
    # textual de la situación. Esto evita que el caller reciba un string vacío
    # (que por ejemplo en Telegram dispara "Message text is empty") y le da al
    # usuario información accionable sobre por qué se agotó el loop.
    if not last_text.strip():
        try:
            fallback = await llm.complete(
                working_messages,
                system_prompt,
                tools=None,
            )
            last_text = fallback.text
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Fallback LLM call tras max_iterations falló para '%s': %s",
                agent_id,
                exc,
            )

    raise ToolLoopMaxIterationsError(last_response=last_text)
