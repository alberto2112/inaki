"""
RunAgentOneShotUseCase — ejecución stateless de un agente para una sola tarea.

Usado internamente por DelegateTool (y cualquier consumidor futuro que necesite
una ejecución aislada sin side-effects sobre el estado persistido).

Contratos clave:
- REQ-OS-1: NO carga ni persiste historial. NO lee el memory digest.
- REQ-OS-2: Usa `system_prompt` del caller verbatim cuando no es None.
             Si es None, usa el system prompt por defecto del agente.
- REQ-OS-3: `asyncio.wait_for` con timeout_seconds; ToolLoopMaxIterationsError
             propagados al caller — ninguno se captura aquí.
- REQ-OS-4: Pasa `tool_registry.get_schemas()` completo al LLM — sin RAG.
- REQ-OS-5: Si `settings.allowed_tools` no es None, restringe el schema a ese subset
             (intersección con el registry recibido). Usado por el sub-agente efímero
             del flujo delegate para acotar qué tools del caller ve el hijo.
- REQ-BGP-2: con ``recorder`` el transcript del hijo se anota mensaje a mensaje
             (user inicial, assistant+tool_calls, tool results, respuesta final):
             ``recorder.trace`` es el espejo en memoria y ``recorder.record`` la
             escritura durable. Sin ``recorder`` la llamada al loop es la de siempre.
- REQ-RES-5: con ``historial_previo`` el hijo REANUDA un transcript pausado: ve lo que ya hizo
             más la respuesta nueva como último ``user``. Ese historial ya está en el
             ``recorder.trace`` reabierto, así que NO se vuelve a anotar ni a grabar.
- REQ-DG-9: Filtra la tool "delegate" de los schemas antes de pasarlos al loop
             (prevención de recursión por construcción). Filtra también
             "background_tasks": las delegaciones en background son del padre, y
             un hijo que la viera podría cancelarse a sí mismo o a sus hermanos.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence

from inaki.kernel.domain.agent_settings import OneShotSettings
from inaki.kernel.ports.delegation_transcript_port import IDelegationTranscript
from inaki.kernel.ports.llm_port import ILLMProvider
from inaki.kernel.ports.tool_port import IToolExecutor
from inaki.kernel.ports.turn_tracer_port import ITurnTracer, NullTurnTracer
from inaki.kernel._tool_loop import run_tool_loop
from inaki.shared.errors import ToolLoopMaxIterationsError
from inaki.shared.message import Message, Role

logger = logging.getLogger(__name__)

# Tools del caller que el hijo NUNCA ve (REQ-DG-9 + bg-stuck-task). `config` NO está acá a
# propósito: el hijo ve la tool y el kernel le rechaza `set` en la ejecución, porque nunca es
# turno de operador (`run_tool_loop`); esconderla entera le quitaría también la lectura.
_TOOLS_EXCLUIDAS_DEL_HIJO = frozenset({"delegate", "background_tasks"})


class RunAgentOneShotUseCase:
    """
    Ejecuta un agente de forma stateless para una única tarea.

    No carga ni escribe historial. No lee digest. No aplica RAG sobre tools.
    Excluye "delegate" y "background_tasks" del schema del hijo (REQ-DG-9).
    """

    def __init__(
        self,
        llm: ILLMProvider,
        tools: IToolExecutor,
        settings: OneShotSettings,
        tracer: ITurnTracer | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._cfg = settings
        self._tracer: ITurnTracer = tracer or NullTurnTracer()

    @property
    def system_prompt(self) -> str:
        """Prompt base del agente (sin footer ni sections extra).

        ``DelegateTool`` lo lee para construir el ``effective_system_prompt`` cuando el
        caller no pasa uno propio: el hijo efímero ya no es un container con
        ``agent_config``, así que el prompt default se expone acá.
        """
        return self._cfg.system_prompt

    async def execute(
        self,
        task: str,
        system_prompt: str | None,
        max_iterations: int,
        timeout_seconds: int,
        *,
        recorder: IDelegationTranscript | None = None,
        historial_previo: Sequence[Message] = (),
    ) -> str:
        """
        Ejecuta el agente sobre `task` y retorna la respuesta final del LLM.

        Args:
            task: Texto de la tarea a ejecutar (mensaje inicial del user).
            system_prompt: Prompt de sistema a usar. Si es None, usa el
                           system_prompt por defecto del agente (sin digest
                           ni sections extra — solo el base prompt).
            max_iterations: Límite de iteraciones del loop de tools.
                            Al superarse, propaga ToolLoopMaxIterationsError.
            timeout_seconds: Límite de tiempo en segundos. Al superarse,
                             propaga asyncio.TimeoutError.
            recorder: Transcript opcional de la delegación (REQ-BGP-2). Si se
                      pasa, recibe cada mensaje del hijo al generarse; ``record``
                      nunca lo mata al turno. Los errores de arriba se propagan
                      IGUAL: el recorder solo observa.
            historial_previo: Mensajes de un tramo anterior de ESTA misma delegación
                              (reanudación tras ``needs_input``). El LLM los ve antes de
                              ``task``; no se vuelven a anotar. Con ``recorder``, su
                              ``trace`` DEBE ser ese mismo historial (el recorder
                              reabierto lo trae precargado).

        Returns:
            Respuesta final en texto del LLM.

        Raises:
            asyncio.TimeoutError: Si la ejecución supera timeout_seconds.
            ToolLoopMaxIterationsError: Si el loop supera max_iterations.
            ValueError: Si ``historial_previo`` y ``recorder`` no están alineados.
        """
        # REQ-RES-5: fail-loud ANTES de tocar nada. Un recorder cuyo trace no es el
        # historial a rehidratar grabaría el tramo nuevo con seq repetidos o con huecos.
        if (
            historial_previo
            and recorder is not None
            and len(recorder.trace) != len(historial_previo)
        ):
            raise ValueError(
                "historial_previo no coincide con recorder.trace "
                f"({len(historial_previo)} mensajes contra {len(recorder.trace)}): el "
                "recorder debe ser el reabierto de esta misma delegación"
            )

        # REQ-OS-2: usa el system_prompt del caller, o el default del agente.
        effective_prompt = system_prompt if system_prompt is not None else self._cfg.system_prompt

        # REQ-OS-4: toolkit completo sin RAG.
        # REQ-DG-9: excluir "delegate" (recursión) y "background_tasks" (las
        #           delegaciones del padre no son asunto del hijo).
        # REQ-OS-5: si hay allow-list, recortar al subset declarado por el sub-agente
        #           (intersección con el registry — un nombre inexistente se ignora).
        all_schemas = self._tools.get_schemas()
        allowed = self._cfg.allowed_tools

        # ToolRegistry.get_schemas() devuelve {"type": "function", "function": {"name": ...}}.
        # El nombre vive en s["function"]["name"], no en s["name"].
        def _tool_name(schema: dict) -> str:
            return schema.get("function", {}).get("name", "")

        tool_schemas = [
            s
            for s in all_schemas
            if _tool_name(s) not in _TOOLS_EXCLUIDAS_DEL_HIJO
            and (allowed is None or _tool_name(s) in allowed)
        ]

        if len(tool_schemas) < len(all_schemas):
            logger.debug(
                "RunAgentOneShotUseCase: schema del hijo recortado a %d/%d tools "
                "(delegate/background_tasks excluidas; allow-list=%s)",
                len(tool_schemas),
                len(all_schemas),
                "todas" if allowed is None else sorted(allowed),
            )

        # REQ-OS-1: historial limpio — solo el mensaje de la tarea actual. Única excepción:
        # la reanudación de un hijo pausado (REQ-RES-5), que arrastra su propio tramo previo.
        messages = [*historial_previo, Message(role=Role.USER, content=task)]

        tool_trace: list[Message] | None = None
        persist_message: Callable[[Message], Awaitable[None]] | None = None
        if recorder is not None:
            persist_message = _grabar_seguro(recorder)
            # Solo el user nuevo es novedad: el historial previo ya está en el trace
            # reabierto y en las filas persistidas.
            await _anotar(recorder, persist_message, messages[-1])
            tool_trace = recorder.trace

        # REQ-OS-3: timeout + iteraciones.
        # asyncio.TimeoutError y ToolLoopMaxIterationsError se propagan al caller.
        try:
            response = await asyncio.wait_for(
                run_tool_loop(
                    llm=self._llm,
                    tools=self._tools,
                    messages=messages,
                    system_prompt=effective_prompt,
                    tool_schemas=tool_schemas,
                    max_iterations=max_iterations,
                    circuit_breaker_threshold=self._cfg.circuit_breaker_threshold,
                    agent_id=self._cfg.agent_id,
                    request_delay_seconds=self._cfg.request_delay_seconds,
                    tool_trace=tool_trace,
                    persist_message=persist_message,
                    tracer=self._tracer.bind(
                        agent_id=self._cfg.agent_id, turn_id=uuid.uuid4().hex[:12], mode="one_shot"
                    ),
                ),
                timeout=timeout_seconds,
            )
        except ToolLoopMaxIterationsError as exc:
            # Lo último que dijo el hijo es parte del rastro; el error sigue su camino.
            if persist_message is not None and exc.last_response.strip():
                assert recorder is not None
                await _anotar(
                    recorder,
                    persist_message,
                    Message(role=Role.ASSISTANT, content=exc.last_response),
                )
            raise

        if persist_message is not None:
            assert recorder is not None
            await _anotar(recorder, persist_message, Message(role=Role.ASSISTANT, content=response))
        return response


async def _anotar(
    recorder: IDelegationTranscript,
    grabar: Callable[[Message], Awaitable[None]],
    message: Message,
) -> None:
    """Agrega ``message`` al espejo en memoria y lo manda a grabar.

    El ``append`` va primero y es síncrono: aunque la escritura durable falle (o
    la cancele un timeout), el trace en memoria ya lo tiene. ``grabar`` es el
    wrapper seguro del turno (uno por ejecución, para avisar una sola vez).
    """
    recorder.trace.append(message)
    await grabar(message)


def _grabar_seguro(
    recorder: IDelegationTranscript,
) -> Callable[[Message], Awaitable[None]]:
    """Envuelve ``recorder.record`` para que un fallo nunca mate el turno del hijo.

    Defensa en profundidad: el contrato del port ya dice que ``record`` no lanza,
    pero un recorder roto no puede costarle el resultado al padre. Avisa UNA vez
    por turno (un disco lleno fallaría en cada mensaje y llenaría el log).
    ``CancelledError`` no es ``Exception``: el timeout sigue cortando limpio.
    """
    avisado = False

    async def _grabar(message: Message) -> None:
        nonlocal avisado
        try:
            await recorder.record(message)
        except Exception as exc:  # noqa: BLE001
            if not avisado:
                avisado = True
                logger.warning(
                    "RunAgentOneShotUseCase: el recorder falló (%s: %s); el transcript durable "
                    "de esta delegación queda incompleto, el turno continúa",
                    type(exc).__name__,
                    exc,
                )

    return _grabar
