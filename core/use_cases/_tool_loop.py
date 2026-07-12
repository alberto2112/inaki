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

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from core.domain.entities.message import Message, Role
from core.domain.errors import ToolLoopMaxIterationsError
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.intermediate_sink_port import (
    IIntermediateSink,
    NullIntermediateSink,
)
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.scope_registry_port import IScopeRegistry, Scope
from core.ports.outbound.tool_port import IToolExecutor

logger = logging.getLogger(__name__)


# Instrucción de cierre tras un kill-switch (/stop). En INGLÉS por convención
# (system-prompts-language). Se appendea como mensaje user sintético SOLO en
# working_messages (jamás se persiste) para forzar una última respuesta SIN
# tools que le cuente al usuario dónde quedó el trabajo.
_CANCEL_WRAPUP_INSTRUCTION = (
    "The user has requested to STOP this task immediately (kill-switch). Do NOT do any "
    "further work. Reply with a brief summary: what was completed so far, what was left "
    "unfinished, and where any partial results live (file paths, task ids). Keep it short."
)

# Resultado sintético para las tool calls de un batch que quedaron sin ejecutar
# cuando el kill-switch cortó a mitad. Mantiene el pairing protocolar
# assistant+tool_calls ↔ tool results (sin esto, el provider tira 400).
_CANCELLED_TOOL_RESULT = "NOT EXECUTED — the user cancelled the task (kill-switch)."


# Tope de veces que un drain in-flight puede resetear el contador de iteraciones
# en un mismo turno. Sin tope, un usuario impaciente que manda N mensajes
# ("hola?", "seguís ahí?") mientras el turno corre le regala N × max_iterations
# de runway al loop → turnos de minutos que parecen colgados (reporte real:
# 6 mensajes → ~30 iteraciones → 8.5 min en una Pi). Tras este tope, los
# mensajes drenados se siguen incorporando pero el contador avanza normal, así
# el turno termina en tiempo acotado. Ver `in-flight-message-injection`.
_MAX_INFLIGHT_ITER_RESETS = 3


async def _drain_new_user_messages(
    history_store: IHistoryStore | None,
    scope: Scope | None,
    cursor: int | None,
) -> tuple[int | None, list[Message]]:
    """Devuelve mensajes ``role=user`` aparecidos en history.db tras el cursor.

    Cursor por rowid monotónico (``load_user_messages_since``): toda fila
    ``role=user`` con id > ``cursor`` es nueva, sin importar la ventana
    ``max_messages`` del store. El diseño anterior CONTABA users sobre
    ``load()`` (ventaneado): con la ventana llena, cada mensaje nuevo expulsa
    uno viejo del borde y el conteo puede no crecer — el drain quedaba ciego en
    conversaciones largas y un "para" del usuario jamás llegaba al LLM (bug
    real, 2026-07-12). También rompía con ``merge_chats`` (baseline sin scope
    vs drain scoped). El cursor elimina la clase entera de errores.

    Si ``history_store``, ``scope`` o ``cursor`` son ``None``, el loop corre en
    modo legacy (sin in-flight injection) — devuelve ``(cursor, [])`` sin tocar
    la DB.
    """
    if history_store is None or scope is None or cursor is None:
        return cursor, []
    agent_id, channel, chat_id = scope
    return await history_store.load_user_messages_since(
        agent_id, cursor, channel=channel, chat_id=chat_id
    )


async def _cancel_requested(
    scope_registry: IScopeRegistry | None,
    scope: Scope | None,
) -> bool:
    """``True`` si hay un kill-switch pendiente para el scope del turno."""
    if scope_registry is None or scope is None:
        return False
    return await scope_registry.is_cancel_requested(scope)


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
    request_delay_seconds: float = 0.0,
    history_store: IHistoryStore | None = None,
    scope: Scope | None = None,
    history_cursor: int | None = None,
    tool_trace: list[Message] | None = None,
    page_in_schemas: list[dict] | None = None,
    scope_registry: IScopeRegistry | None = None,
    persist_message: Callable[[Message], Awaitable[None]] | None = None,
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
        request_delay_seconds: Espera (segundos) antes de cada ``llm.complete()``
            EXCEPTO la primera del turno. Espacia las llamadas encadenadas al
            provider para no saturar su rate limiter cuando el modelo hace varias
            tool calls seguidas. Default ``0.0`` → sin throttle (comportamiento
            legacy). El valor de producción llega desde ``config.llm.request_delay_seconds``.
        history_store: Si se provee junto con ``scope`` y ``history_cursor``, el
            loop drena mensajes ``role=user`` aparecidos en history.db entre
            iteraciones (feature ``in-flight-message-injection``). Cuando hay
            drain no-vacío, el contador de iteraciones se resetea a 0. Default
            ``None`` → comportamiento legacy (loop ciego al historial externo).
        scope: Tupla ``(agent_id, channel, chat_id)`` del turno. Requerido junto
            con ``history_store`` para activar la drainage. Default ``None``.
        history_cursor: Rowid de la última fila de history.db que el turno YA
            tiene en su contexto (típicamente el id del user message recién
            persistido). El drain devuelve solo filas ``role=user`` con id
            mayor — inmune a la ventana ``max_messages`` y al coalesce (el
            diseño anterior contaba users sobre ``load()`` ventaneado y quedaba
            ciego con la ventana llena). Default ``None`` → el loop bootstrapea
            el cursor con ``last_row_id`` del scope al arrancar (drainage sigue
            activa si hay ``history_store`` + ``scope``).
        tool_trace: Acumulador opcional (feature persist-tool-calls). Si se provee
            una lista, el loop le appendea —en orden— cada mensaje ``assistant``
            con tool_calls y cada mensaje ``tool`` (resultado o circuit-open) que
            genera durante el turno. El caller (``RunAgentUseCase``) es dueño de la
            lista y la persiste tras el loop, así que queda completa aun si el turno
            corta por ``ToolLoopMaxIterationsError`` (mismo patrón que
            ``RecordingIntermediateSink``). Default ``None`` → no se acumula nada
            (subagentes one-shot y modo legacy). NO incluye los mensajes ``user``
            drenados in-flight (esos ya están persistidos por el inbound adapter).
        page_in_schemas: Catálogo COMPLETO de schemas para el page-in de tools
            (feature ``tool-page-in``). Si el LLM llama una tool que no está en
            ``tool_schemas`` (el set visible que dejó el semantic routing) pero
            SÍ existe en este catálogo, su schema se agrega al set visible para
            las iteraciones siguientes — como un fallo de página que se resuelve
            trayendo la página a memoria. La ejecución en sí ya funcionaba (el
            executor conoce todas las tools registradas); lo que faltaba era que
            el LLM viera el contrato de argumentos en las llamadas posteriores.
            Default ``None`` → deshabilitado. Los subagentes one-shot NO lo
            pasan a propósito: su set visible está acotado por ``tools.allowed``
            (REQ-OS-5) y la exclusión de ``delegate`` (REQ-DG-9) — el page-in
            los dejaría escapar del sandbox.
        scope_registry: Registry de scopes para el kill-switch (feature
            ``turn-kill-switch``). Si se provee junto con ``scope``, el loop
            consulta ``is_cancel_requested(scope)`` en el checkpoint A y antes
            de CADA tool del batch: ante una cancelación (comando ``/stop``),
            las tools restantes del batch reciben un resultado sintético (el
            pairing protocolar se preserva), el loop corta, y una última
            llamada SIN tools le pide al LLM un resumen de dónde quedó el
            trabajo. La cancelación es MECÁNICA — no depende de que el LLM
            interprete nada. Default ``None`` → sin kill-switch (one-shot,
            legacy).
        persist_message: Callback de persistencia INCREMENTAL (feature
            ``incremental-persist``). Si se provee, el loop lo invoca con cada
            mensaje del rastro (assistant+tool_calls, tool results, circuit-open
            y results sintéticos de cancelación) EN el momento en que se genera
            — el historial refleja el turno en vivo y un crash del daemon no
            pierde el trabajo ya narrado. Solo lo pasa ``RunAgentUseCase`` en
            turnos que NO pueden terminar en ``__SKIP__`` (``skip_marker is
            None``, decidido al inicio); los turnos skip-capaces siguen con el
            batch post-loop del caller. Mutuamente excluyente en la práctica
            con ``tool_trace`` (el caller pasa uno u otro), aunque el loop
            tolera ambos. Default ``None`` → sin persistencia incremental.

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

    # tool-page-in: copia propia del set visible (no mutar la lista del caller)
    # + índice del catálogo completo para resolver fallos de página. Con
    # ``page_in_schemas=None`` el feature queda inerte (one-shot, legacy).
    # Los lookups usan .get() porque el loop no impone la forma OpenAI del
    # schema — callers de test pasan dicts sueltos.
    tool_schemas = list(tool_schemas)
    visible_names = {sch.get("function", {}).get("name", "") for sch in tool_schemas}
    page_in_by_name: dict[str, dict] | None = (
        {name: sch for sch in page_in_schemas if (name := sch.get("function", {}).get("name", ""))}
        if page_in_schemas is not None
        else None
    )

    # Cursor de drainage (in-flight-message-injection): toda fila role=user de
    # history.db con id > cursor se inyecta al loop en los checkpoints. El
    # caller idealmente lo pasa apuntando a la última fila que el turno ya
    # tiene en contexto (el id del user_msg recién persistido — baseline
    # exacto); si no, se bootstrapea acá con MAX(id) del scope: nada anterior
    # al arranque del loop es "nuevo".
    cursor = history_cursor
    if cursor is None and history_store is not None and scope is not None:
        cursor = await history_store.last_row_id(scope[0], channel=scope[1], chat_id=scope[2])
    # Cuántas veces ya reseteamos el contador por un drain in-flight. Acotado por
    # _MAX_INFLIGHT_ITER_RESETS para que el turno no viva indefinidamente.
    inflight_resets = 0

    # Kill-switch (/stop): cuando pasa a True, el loop corta y salta al wrap-up
    # (una última llamada SIN tools para resumir dónde quedó el trabajo).
    cancelled = False

    # Indicador "Thinking..." una sola vez por turno cuando el provider activa
    # thinking mode y el operador lo habilitó via ``channels.thinking_indicator``.
    # Es feedback efímero para el canal — no persiste en DB, no se broadcastea.
    # Si el sink es Null (CLI sin streaming) no se ve.
    if llm.thinking_active and thinking_indicator:
        await sink.emit("Thinking...")

    # Throttle del provider: una vez que hicimos al menos una llamada en este
    # turno, espaciamos las siguientes ``request_delay_seconds`` para no saturar
    # el rate limiter cuando el modelo encadena tool calls. La PRIMERA llamada no
    # se demora. El flag es independiente del contador de iteraciones (que se
    # resetea con el drain in-flight): lo que importa es si ya pegamos al provider.
    made_llm_call = False

    iteration = 0
    while iteration < max_iterations:
        # Checkpoint A — drenar antes de llamar al LLM. Cualquier mensaje
        # role=user que el inbound adapter haya persistido en history mientras
        # estábamos ejecutando tools en la iteración previa se incorpora ahora
        # a working_messages y el LLM lo ve en la próxima llamada.
        cursor, drained = await _drain_new_user_messages(history_store, scope, cursor)
        if drained:
            working_messages.extend(drained)
            if inflight_resets < _MAX_INFLIGHT_ITER_RESETS:
                logger.info(
                    "[in-flight] drain checkpoint=A count=%d agent_id=%s iter_reset_from=%d",
                    len(drained),
                    agent_id,
                    iteration,
                )
                iteration = 0
                inflight_resets += 1
            else:
                logger.warning(
                    "[in-flight] drain checkpoint=A count=%d agent_id=%s — reset cap "
                    "(%d) alcanzado, NO reseteo el contador para acotar el turno",
                    len(drained),
                    agent_id,
                    _MAX_INFLIGHT_ITER_RESETS,
                )

        # Kill-switch — checkpoint A: si hay un /stop pendiente, no gastamos ni
        # una llamada más al LLM en seguir trabajando; salimos directo al
        # wrap-up (working_messages termina en tool results completos o en
        # user, así que la llamada de cierre es protocolarmente válida).
        if await _cancel_requested(scope_registry, scope):
            logger.info("[kill-switch] cancel detectado (checkpoint A) agent=%s", agent_id)
            cancelled = True
            break

        if made_llm_call and request_delay_seconds > 0:
            await asyncio.sleep(request_delay_seconds)

        response = await llm.complete(
            working_messages,
            system_prompt,
            tools=tool_schemas if tool_schemas else None,
        )
        made_llm_call = True
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
        assistant_msg = Message(
            role=Role.ASSISTANT,
            content=response.text,
            tool_calls=response.tool_calls,
            thinking=response.thinking,
        )
        working_messages.append(assistant_msg)
        if tool_trace is not None:
            tool_trace.append(assistant_msg)
        if persist_message is not None:
            await persist_message(assistant_msg)

        for tc in response.tool_calls:
            tc_id = tc.get("id", "")
            tool_name = tc.get("function", {}).get("name", "")
            args_raw = tc.get("function", {}).get("arguments", "{}")
            try:
                kwargs = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                kwargs = {}

            # Kill-switch — chequeo por tool: un /stop a mitad de un batch de
            # 3 búsquedas corta ANTES de la próxima ejecución. Las tools que
            # quedan sin ejecutar reciben un resultado sintético para preservar
            # el pairing protocolar (todo tool_call_id del assistant DEBE tener
            # su tool result, o el provider tira 400 en la llamada de cierre).
            if not cancelled and await _cancel_requested(scope_registry, scope):
                cancelled = True
                logger.info(
                    "[kill-switch] cancel detectado mid-batch (tool '%s' y siguientes "
                    "no se ejecutan) agent=%s",
                    tool_name,
                    agent_id,
                )
            if cancelled:
                cancel_msg = Message(
                    role=Role.TOOL,
                    content=_CANCELLED_TOOL_RESULT,
                    tool_call_id=tc_id,
                )
                working_messages.append(cancel_msg)
                if tool_trace is not None:
                    tool_trace.append(cancel_msg)
                if persist_message is not None:
                    await persist_message(cancel_msg)
                continue

            if tool_name in tripped:
                logger.warning("Circuit breaker abierto para '%s' — llamada bloqueada", tool_name)
                circuit_msg = Message(
                    role=Role.TOOL,
                    content=(
                        f"CIRCUIT OPEN — esta tool ya falló "
                        f"{circuit_breaker_threshold} vez/veces en este turno. "
                        "NO la vuelvas a llamar. Respondé al usuario con lo que "
                        "sabés, o pedile ayuda para resolver el bloqueo."
                    ),
                    tool_call_id=tc_id,
                )
                working_messages.append(circuit_msg)
                if tool_trace is not None:
                    tool_trace.append(circuit_msg)
                if persist_message is not None:
                    await persist_message(circuit_msg)
                continue

            # tool-page-in: el LLM llamó una tool que existe en el registry pero
            # el routing no la había hecho visible (caso real: el LLM decide
            # delegar a mitad del turno). La ejecución de abajo funciona igual
            # (el executor conoce todas las tools); acá agregamos su schema al
            # set visible para que las próximas llamadas al LLM vean el contrato
            # de argumentos completo.
            if (
                page_in_by_name is not None
                and tool_name not in visible_names
                and tool_name in page_in_by_name
            ):
                tool_schemas.append(page_in_by_name[tool_name])
                visible_names.add(tool_name)
                logger.info(
                    "[page-in] tool '%s' agregada al set visible (agent=%s)",
                    tool_name,
                    agent_id,
                )

            result = await tools.execute(tool_name, **kwargs)
            result_msg = Message(
                role=Role.TOOL,
                content=result.output,
                tool_call_id=tc_id,
            )
            working_messages.append(result_msg)
            if tool_trace is not None:
                tool_trace.append(result_msg)
            if persist_message is not None:
                await persist_message(result_msg)
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

        # Kill-switch — el batch quedó completo (con resultados sintéticos si
        # cortó a mitad): salir al wrap-up sin drenar ni iterar más.
        if cancelled:
            break

        # Checkpoint B — drenar después de que TODO el batch de tool_calls esté
        # en working_messages. Si entre el LLM call y aquí el usuario mandó un
        # mensaje nuevo, lo vemos ahora y la próxima iteración del while lo
        # incorpora a la siguiente llamada al LLM.
        #
        # IMPORTANTE: nunca drenamos en medio del for-tc (entre tool calls
        # individuales). Eso violaría el contrato de los providers: la API de
        # OpenAI/DeepSeek requiere que todos los tool_result de un mismo
        # assistant(tool_calls) lleguen juntos antes del próximo mensaje.
        cursor, drained = await _drain_new_user_messages(history_store, scope, cursor)
        if drained:
            working_messages.extend(drained)
            if inflight_resets < _MAX_INFLIGHT_ITER_RESETS:
                logger.info(
                    "[in-flight] drain checkpoint=B count=%d agent_id=%s iter_reset_from=%d",
                    len(drained),
                    agent_id,
                    iteration,
                )
                iteration = 0
                inflight_resets += 1
                continue  # no incrementar: ya reseteamos, arrancamos iteración 1
            logger.warning(
                "[in-flight] drain checkpoint=B count=%d agent_id=%s — reset cap "
                "(%d) alcanzado, dejo avanzar el contador para acotar el turno",
                len(drained),
                agent_id,
                _MAX_INFLIGHT_ITER_RESETS,
            )

        iteration += 1

    # -----------------------------------------------------------------------
    # Wrap-up del kill-switch: el turno fue cancelado por el usuario (/stop).
    # Una última llamada SIN tools con una instrucción de cierre sintética
    # (solo en working_messages, jamás persistida) para que el LLM resuma qué
    # completó, qué quedó a medias y dónde están los resultados parciales.
    # Best-effort: si el provider falla, devolvemos un cierre fijo — el turno
    # termina igual, que es lo que el usuario pidió.
    # -----------------------------------------------------------------------
    if cancelled:
        working_messages.append(Message(role=Role.USER, content=_CANCEL_WRAPUP_INSTRUCTION))
        try:
            if made_llm_call and request_delay_seconds > 0:
                await asyncio.sleep(request_delay_seconds)
            wrapup = await llm.complete(working_messages, system_prompt, tools=None)
            if wrapup.text.strip():
                return wrapup.text
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[kill-switch] wrap-up LLM call falló para '%s': %s — cierro con texto fijo",
                agent_id,
                exc,
            )
        return "🛑 Tarea detenida a pedido del usuario."

    logger.warning("Máximo de iteraciones de tool calls alcanzado para '%s'", agent_id)

    # Recuperación: si la última iteración fue tool-only (sin texto), hacemos
    # una llamada final SIN tools para forzar al LLM a producir una explicación
    # textual de la situación. Esto evita que el caller reciba un string vacío
    # (que por ejemplo en Telegram dispara "Message text is empty") y le da al
    # usuario información accionable sobre por qué se agotó el loop.
    if not last_text.strip():
        try:
            # Misma cortesía con el rate limiter: esta llamada sigue inmediata
            # a la última del loop, así que la espaciamos también.
            if request_delay_seconds > 0:
                await asyncio.sleep(request_delay_seconds)
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
