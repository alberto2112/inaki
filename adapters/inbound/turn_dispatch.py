"""Routing in-flight-message-injection compartido entre adapters inbound.

El patrón "scope ocupado → persistir + ACK / scope libre → ejecutar turno"
estaba copy-pasted en Telegram (`bot.py`), REST per-agente (`agents.py`) y
admin REST (`chat.py`), con textos de ACK divergentes. Este módulo lo
centraliza: la política vive en un solo lugar y los adapters solo aportan
el closure `execute` con sus kwargs específicos (ctx, sink, skip_marker...).

El handler de fotos de Telegram NO usa este helper a propósito: adquiere el
slot ANTES del procesamiento pesado (reconocimiento facial + descripción de
escena) y decide el camino al final, porque su `_run_pipeline` anidado con
``user_input=None`` depende de que el slot ya esté tomado. Comparte solo la
constante ``INFLIGHT_ACK``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core.ports.outbound.scope_registry_port import IScopeRegistry, Scope
from core.use_cases.run_agent import RunAgentUseCase

# ACK único para todos los canales — antes cada adapter tenía su propio texto
# y divergían silenciosamente. Si algún día un canal necesita otro tono, que
# lo derive de su `InboundTurnResult.executed`, no de un string propio.
INFLIGHT_ACK = "📝 Lo incorporo a lo que estoy haciendo, dame un momento."


@dataclass(frozen=True)
class InboundTurnResult:
    """Resultado del routing de un turno inbound.

    ``executed=True`` → el turno corrió y ``reply`` es la respuesta del agente.
    ``executed=False`` → el scope estaba ocupado: el mensaje quedó persistido
    en history para que el loop activo lo drene, y ``reply`` es el ACK.
    """

    reply: str
    executed: bool


async def dispatch_inbound_turn(
    *,
    scope_registry: IScopeRegistry,
    run_agent: RunAgentUseCase,
    scope: Scope,
    message: str,
    execute: Callable[[], Awaitable[str]],
) -> InboundTurnResult:
    """Ejecuta un turno inbound respetando la política in-flight-message-injection.

    Si el scope está libre lo marca busy, corre ``execute()`` y libera el slot
    en ``finally`` (aunque el turno lance excepción — la excepción se propaga
    al caller, que ya tiene su propio manejo de errores por canal).

    Si el scope está ocupado por otro turno, persiste ``message`` en history
    vía ``record_user_message`` — el tool loop activo lo drenará entre
    iteraciones — y devuelve el ACK sin ejecutar nada.

    Args:
        scope_registry: Registry de scopes busy/idle del proceso.
        run_agent: Use case del agente dueño del scope.
        scope: Tupla ``(agent_id, channel, chat_id)`` del turno.
        message: Texto del usuario, ya formateado por el adapter.
        execute: Closure sin argumentos que corre el turno completo — el
            adapter cierra sobre sus kwargs específicos (ctx, sink, etc.).
    """
    _, channel, chat_id = scope
    if await scope_registry.try_mark_busy(scope):
        try:
            reply = await execute()
        finally:
            await scope_registry.mark_idle(scope)
        return InboundTurnResult(reply=reply, executed=True)

    await run_agent.record_user_message(message, channel, chat_id)
    return InboundTurnResult(reply=INFLIGHT_ACK, executed=False)
