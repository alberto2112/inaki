"""
DelegateTool — delega una tarea a un agente hijo vía RunAgentOneShotUseCase.

Implementa ITool con name="delegate". Satisface:
- REQ-DG-2: allow-list check antes de resolver el registry.
- REQ-DG-3: registry lookup falla → unknown_agent.
- REQ-DG-4: child retorna texto → parse_delegation_result → DelegationResult.
- REQ-DG-5: parse failure → result_parse_error (manejado por el parser).
- REQ-DG-6 / REQ-DG-8: todos los fallos producen DelegationResult; NUNCA propaga.
- REQ-DG-9: recursión imposible por construcción — RunAgentOneShot ya filtra
             la tool "delegate" de los schemas del hijo.

IMPORTANTE — batch-3 decision: RunAgentOneShotUseCase es dueño del asyncio.wait_for.
DelegateTool NO envuelve en wait_for; solo captura asyncio.TimeoutError que el use
case propaga cuando se excede timeout_seconds.

Task 6.2: DelegateTool construye un effective_system_prompt que antepone el prompt base
(del caller o el default del agente hijo) y agrega RESULT_FORMAT_FOOTER (``result_contract``) al final.
El hijo SIEMPRE recibe las instrucciones de formato — sin esto, parse_delegation_result
devolvería result_parse_error en cada llamada.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import TYPE_CHECKING, Callable, Protocol

from inaki.agents.delegation.result_contract import con_footer
from inaki.agents.delegation.sqlite_task_store import RETENCION_DIAS, RETENCION_ULTIMAS
from inaki.agents.delegation.workdirs import ruta_relativa
from inaki.kernel.domain.background_task import (
    EnqueuedTask,
    InputFilesRejected,
    ResumeOutcome,
)
from inaki.kernel.domain.delegation_result import DelegationResult
from inaki.kernel.ports.tool_port import ITool, ToolResult
from inaki.kernel._result_parser import parse_delegation_result
from inaki.shared.errors import ToolLoopMaxIterationsError

if TYPE_CHECKING:
    from inaki.kernel.ports.background_delegation_port import IBackgroundDelegationQueue
    from inaki.kernel.run_agent_one_shot import RunAgentOneShotUseCase
    from inaki.shared.channel_context import TurnScope

logger = logging.getLogger(__name__)


# Interfaces estructurales que `delegate` necesita de los containers — declaradas
# acá (el consumidor define lo que requiere) para no acoplar el tool a
# `inaki.app.assembly`. El composition root las
# satisface por duck-typing; la dirección hexagonal queda intacta
# (un módulo NO importa el composition root).
class DelegationCaller(Protocol):
    """Container llamador: el scope de historial del turno en curso.

    El scope y no el ``ChannelContext``: un turno sin remitente (el que procesa
    un ``[bg-N]``, un ``agent_send``) corre con ``ctx=None`` pero pertenece a una
    conversación, y el resultado tiene que volver a ESA conversación.
    """

    def get_turn_scope(self) -> TurnScope | None: ...


def _normalizar_input_files(valor: list[str] | str | None) -> tuple[str, ...]:
    """``input_files`` tal como lo manda el LLM → tupla. ``[]``/``None`` = ausente.

    Un string suelto (el modelo olvida la lista) es un solo fichero: ``tuple("a.md")`` lo
    partiría en caracteres.
    """
    if not valor:
        return ()
    if isinstance(valor, str):
        return (valor,)
    return tuple(valor)


def _detalle_encolada(enqueued: EnqueuedTask) -> str:
    """Texto ``details`` del resultado ``queued`` (lo lee el LLM, en inglés).

    Sin workdir es solo el id. Con workdir le dice al padre dónde quedó, que el hijo
    escribe ahí, y que el directorio muere con el registro de la task. NUNCA lo invita a
    dejar ficheros ahí después de encolar: el hijo puede haber arrancado ya, y los insumos
    van por ``input_files``, que los copia ANTES de encolar.
    """
    if enqueued.workdir is None:
        if enqueued.workdir_unavailable:
            return (
                f"{enqueued.task_id}\nWorking directory: unavailable (it could not be "
                "created); the child will work in your workspace as usual."
            )
        return enqueued.task_id
    lineas = [
        enqueued.task_id,
        f"Working directory: `{ruta_relativa(enqueued.workdir)}` "
        f"(absolute: `{enqueued.workdir}`). The child was told to write its outputs there.",
    ]
    if enqueued.input_files:
        lineas.append(
            f"Input files copied there before it started: {', '.join(enqueued.input_files)}."
        )
    lineas.append(
        "The directory is deleted together with the task record (finished tasks are kept "
        f"{RETENCION_DIAS} days, last {RETENCION_ULTIMAS} per agent) — copy out anything "
        "you want to keep."
    )
    return "\n".join(lineas)


def _resultado_de_resume(outcome: ResumeOutcome) -> DelegationResult:
    """``ResumeOutcome`` → ``DelegationResult`` (lo lee el LLM, en inglés).

    ``resumed`` es ``success``; cualquier otro outcome es ``failed`` con ``reason`` = su
    nombre. Ningún texto afirma que la task "no existe": la cola responde igual para una
    podada, una inexistente y una ajena, y lo único honesto es "ya no la tengo".
    """
    tid = outcome.task_id
    if outcome.outcome == "resumed":
        detalle = f"{tid} continues with your answer"
        if outcome.iterations_left is not None:
            detalle += f" ({outcome.iterations_left} iterations left)"
        return DelegationResult(
            status="success",
            summary="Delegation resumed",
            details=(
                f"{detalle}. It runs in background: its result will arrive as a [bg-N] message "
                f"(this one is {tid}). Do not start a new delegation for it."
            ),
        )
    detalle_cola = f" ({outcome.detail})" if outcome.detail else ""
    objetivo = outcome.target_agent_id or "its agent"
    textos = {
        "not_found": (
            f"{tid} cannot be resumed: {outcome.detail}."
            if outcome.detail
            else f"I no longer have {tid}. It may have been pruned, or it is not one of your tasks."
        ),
        "not_awaiting": (
            f"{tid} is not waiting for an answer (status: {outcome.status}). Only a task "
            "that ended in needs_input can be resumed."
        ),
        "already_resuming": (
            f"{tid} is already being resumed by another call. Do not resume it again: its "
            "result will arrive as a [bg-N] message."
        ),
        "transcript_incomplete": (
            f"{tid} cannot be resumed; delegate again including the answer{detalle_cola}. "
            "You can dismiss it with background_tasks(action=cancel)."
        ),
        "no_iterations_left": (
            f"{tid} has no iterations left in its budget, so it cannot continue. Delegate "
            "again including the answer, or dismiss it with background_tasks(action=cancel)."
        ),
        "target_mismatch": (
            f"{tid} belongs to agent '{objetivo}', not to the agent_id you passed. Omit "
            "agent_id when you resume."
        ),
        "target_not_allowed": (
            f"{tid} belongs to agent '{objetivo}', which is not among your allowed delegation "
            "targets, so you cannot resume it."
        ),
        "unknown_target": (
            f"{tid} belongs to agent '{objetivo}', which is no longer available (the "
            "configuration changed). It cannot be resumed; dismiss it with "
            "background_tasks(action=cancel)."
        ),
        "unavailable": (
            f"{tid} cannot be resumed right now{detalle_cola or ''}. Nothing was changed: "
            "you may retry."
        ),
    }
    return DelegationResult(
        status="failed", summary=textos[outcome.outcome], reason=outcome.outcome
    )


class DelegateTool(ITool):
    """
    Tool que delega una tarea a otro agente (hijo) y retorna un resultado estructurado.

    El hijo se ejecuta vía RunAgentOneShotUseCase: no persiste historial de conversación,
    no lee digest, y no tiene acceso a la tool "delegate" en su schema (recursión imposible
    por construcción — REQ-DG-9). Un hijo en background puede pausar con UNA pregunta
    (``needs_input``) y se reanuda sobre la misma fila con ``delegate(resume="bg-N", task=...)``.
    """

    name = "delegate"
    # NOTE: keep this description as plain text — no markdown, no newlines.
    # Structured guidance on when to delegate lives in the agent-discovery
    # section injected by the assembler (_wire_delegation).
    description = (
        "Delegate a task to a specialized agent when the task requires expertise or tools you don't have, "
        "or involves multiple steps better handled end-to-end by another agent. "
        "Returns a structured result with status, summary, details, and reason. "
        "A background child that paused with a question (a [bg-N] needs_input message) "
        "continues when you call this tool again with resume set to its bg-N id and your "
        "answer in task."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": (
                    "ID of the target agent to delegate the task to. Required unless resume is set."
                ),
            },
            "task": {
                "type": "string",
                "description": (
                    "Description of the work to delegate to the target agent. "
                    "With resume, your ANSWER to the question the child asked."
                ),
            },
            "system_prompt": {
                "type": "string",
                "description": (
                    "Optional override for the child agent's system prompt. "
                    "When omitted, the child uses its own default system prompt."
                ),
            },
            "wait": {
                "type": "boolean",
                "description": (
                    "If false (default), runs in background: you immediately get a "
                    "task_id and the result is injected as a new message starting "
                    "with `[bg-N] ...` when ready. Use this for long research, "
                    "analysis, or any delegation expected to take more than a few "
                    "seconds. If true, blocks until the child finishes and returns "
                    "the result inline this same turn. Use ONLY when you need the "
                    "delegation result to make your next decision or tool call."
                ),
                "default": False,
            },
            "input_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional. Paths of files in YOUR workspace to copy into the child's "
                    "working directory before it starts, so it can read them from its first "
                    "step. Regular files only (max 20, 50 MiB total), copied flat by file "
                    "name (names must be unique). Only works in background mode (wait=false) "
                    "and when this agent is granted working directories; otherwise the call "
                    "fails."
                ),
            },
            "resume": {
                "type": "string",
                "description": (
                    "Optional. The bg-N id of a background task that ended in needs_input "
                    "(the child paused with one question). When set, task is your answer and "
                    "the SAME child continues where it stopped, in background: its result "
                    "arrives as a new [bg-N] message. Never start a new delegation for a "
                    "paused task. agent_id is not needed; wait=true, input_files and "
                    "system_prompt are not accepted together with resume."
                ),
            },
        },
        "required": ["task"],
    }

    def __init__(
        self,
        *,
        allowed_targets: list[str],
        build_child: Callable[[str], "RunAgentOneShotUseCase | None"],
        max_iterations_per_sub: int,
        timeout_seconds: int,
        caller_agent_id: str,
        caller_container: "DelegationCaller",
        queue: "IBackgroundDelegationQueue | None" = None,
    ) -> None:
        """
        Args:
            allowed_targets: Lista de agent_ids permitidos como destino. Si está vacía,
                             todos los agentes registrados son válidos. Proviene de
                             agent_config.delegation.allowed_targets del agente padre.
            build_child: Callable que recibe el agent_id de un sub-agente y construye una
                         instancia EFÍMERA one-shot resuelta contra el caller (hereda su
                         config vía `inherit`), o None si el id no es un sub-agente conocido.
                         Lo arma el ensamblador con `inaki.agents.wiring.build_ephemeral_child`
                         + el delta crudo del registry — una instancia nueva por delegación.
            max_iterations_per_sub: Límite de iteraciones para el loop del agente hijo.
                                    Proviene del ``delegation`` del agente que delega.
            timeout_seconds: Límite de tiempo en segundos para la ejecución del hijo.
                             Proviene del ``delegation`` del agente que delega.
        """
        self._allowed_targets = allowed_targets
        self._build_child = build_child
        self._max_iterations_per_sub = max_iterations_per_sub
        self._timeout_seconds = timeout_seconds
        # REQ-DG-10 / REQ-BGD-*: contexto del caller para el path async
        self._caller_agent_id = caller_agent_id
        self._caller_container = caller_container
        self._queue = queue

        # El schema de `agent_id` se especializa POR INSTANCIA con la allow-list
        # del caller: `enum` + lista en la description. Sin esto el LLM no tiene
        # forma de saber qué targets existen e inventa ids plausibles
        # ('research-analyst') que mueren en target_not_allowed — caso real que
        # hizo abandonar la delegación y re-hacer el trabajo a mano. El atributo
        # de instancia shadowea el de clase; con allow-list vacía (= todos los
        # targets válidos) el schema queda genérico como antes.
        if allowed_targets:
            schema = copy.deepcopy(type(self).parameters_schema)
            schema["properties"]["agent_id"]["enum"] = sorted(allowed_targets)
            schema["properties"]["agent_id"]["description"] = (
                "ID of the target agent to delegate the task to. "
                f"Must be one of: {', '.join(sorted(allowed_targets))}. "
                "Required unless resume is set."
            )
            self.parameters_schema = schema

    async def execute(  # type: ignore[override]
        self,
        agent_id: str | None = None,
        task: str = "",
        system_prompt: str | None = None,
        wait: bool = False,
        input_files: list[str] | str | None = None,
        resume: str | None = None,
        **kwargs,
    ) -> ToolResult:
        """Dispatcher: ``resume`` → reanudar; ``wait=True`` → sync path (legacy);
        ``wait=False`` → async queue.

        ``agent_id`` ya no es ``required`` en el schema (con ``resume`` no hace falta), así
        que se valida acá: sin ``resume`` y sin ``agent_id`` → ``agent_id_required``.

        Default es async (REQ-DG-10). El caller marca ``wait=True`` explícitamente
        cuando necesita el resultado de la delegación en este mismo turno (para
        tomar una decisión o llamar a otra tool con esa información).

        Para devs que modifiquen las dos ramas:

        - **Sync (``wait=True``)** — ``_execute_sync``: bloquea con
          ``asyncio.wait_for(timeout_seconds)`` sobre ``child_one_shot.execute``.
          Retorna un ``DelegationResult`` completo (status/summary/details/reason)
          parseado del bloque ```json``` del hijo. Aplica REQ-DG-2..9.

        - **Async (``wait=False``)** — ``_execute_async``: encola en la cola
          background y retorna inmediatamente con ``DelegationResult(status="success",
          summary="Delegation queued", details=task_id)``. El resultado real del
          hijo llega DESPUÉS, vía ``LLMDispatcherAdapter.dispatch`` con prefijo
          ``[bg-N] ...`` en el ``(channel, chat_id)`` original. Aplica REQ-DG-2,
          REQ-DG-10, REQ-DG-11, REQ-BGD-*.

        El path async requiere ``self._queue`` no-None. Si la cola no está
        wired (ej. tests parciales sin harness), retorna ``failed`` con
        ``reason="background_delegation_unavailable"`` — fail-fast en lugar de
        crashear.
        """
        insumos = _normalizar_input_files(input_files)
        if resume is not None and resume.strip():
            return await self._execute_resume(
                task_id=resume.strip(),
                respuesta=task,
                agent_id=agent_id,
                system_prompt=system_prompt,
                wait=wait,
                input_files=insumos,
            )
        if agent_id is None or not agent_id.strip():
            return self._build_tool_result(
                DelegationResult(
                    status="failed",
                    summary=(
                        "agent_id is required to start a delegation (it is only optional "
                        "when you resume a paused task with resume=bg-N)."
                    ),
                    reason="agent_id_required",
                )
            )
        if wait:
            if insumos:
                # El workdir solo existe en background (hay una fila que lo purgue): con
                # wait=true no hay dónde copiar. Se falla antes de tocar nada.
                return self._rechazo_de_insumos(
                    InputFilesRejected("input_files_require_background")
                )
            return await self._execute_sync(
                agent_id=agent_id, task=task, system_prompt=system_prompt
            )
        return await self._execute_async(
            agent_id=agent_id, task=task, system_prompt=system_prompt, input_files=insumos
        )

    async def _execute_sync(
        self,
        *,
        agent_id: str,
        task: str,
        system_prompt: str | None,
    ) -> ToolResult:
        """
        Delega `task` al agente `agent_id` y retorna un ToolResult.

        El campo `output` del ToolResult es siempre un JSON string serializado de
        DelegationResult (valid o error). NUNCA propaga excepciones — REQ-DG-8.

        Flow:
        1. Allow-list check (REQ-DG-2) → target_not_allowed
        2. Registry lookup (REQ-DG-3) → unknown_agent
        3. Construir effective_system_prompt (base + RESULT_FORMAT_FOOTER) — task 6.2
        4. Llamar al one-shot use case del hijo
        5. Capturar TimeoutError → timeout
        6. Capturar ToolLoopMaxIterationsError → max_iterations_exceeded
        7. Capturar Exception genérica → child_exception:<Type>
        8. On success: parse_delegation_result(raw)
        9. Serializar DelegationResult → ToolResult.output JSON
        """
        # -----------------------------------------------------------------------
        # 1. Allow-list check — REQ-DG-2
        # -----------------------------------------------------------------------
        if self._allowed_targets and agent_id not in self._allowed_targets:
            logger.warning(
                "DelegateTool: agente '%s' no está en la allow-list %s",
                agent_id,
                self._allowed_targets,
            )
            result = DelegationResult(
                status="failed",
                summary=(
                    f"Agent '{agent_id}' is not in the allowed delegation targets. "
                    f"Valid targets: {', '.join(sorted(self._allowed_targets))}. "
                    "Retry with one of those."
                ),
                reason="target_not_allowed",
            )
            return self._build_tool_result(result)

        # -----------------------------------------------------------------------
        # 2. Construir la instancia efímera del hijo contra el caller — REQ-DG-3
        #    build_child resuelve el sub-agente y lo construye heredando del caller
        #    (build_ephemeral_child). None → el id no es un sub-agente conocido. La
        #    construcción puede fallar si el delta del sub es inválido (config rota):
        #    se captura para preservar el never-raises (REQ-DG-8).
        # -----------------------------------------------------------------------
        try:
            child_one_shot = self._build_child(agent_id)
        except Exception as build_exc:  # noqa: BLE001
            exc_type = type(build_exc).__name__
            logger.warning(
                "DelegateTool: no se pudo construir el hijo efímero '%s': %s: %s",
                agent_id,
                exc_type,
                build_exc,
            )
            result = DelegationResult(
                status="failed",
                summary=f"Could not build ephemeral child '{agent_id}': {exc_type}.",
                details=str(build_exc),
                reason=f"child_build_error:{exc_type}",
            )
            return self._build_tool_result(result)

        if child_one_shot is None:
            logger.warning("DelegateTool: agente '%s' no es un sub-agente conocido", agent_id)
            result = DelegationResult(
                status="failed",
                summary=f"Agent '{agent_id}' is not registered in the application registry.",
                reason="unknown_agent",
            )
            return self._build_tool_result(result)

        # -----------------------------------------------------------------------
        # 3. Build effective system prompt — task 6.2
        #
        #    RESULT_FORMAT_FOOTER (result_contract) ALWAYS appended so the child knows to emit a
        #    trailing ```json``` block. Without it, parse_delegation_result returns
        #    result_parse_error on every call.
        #
        #    Strategy:
        #    - caller passed system_prompt → use it as base
        #    - caller passed None → fall back to the child's default system_prompt
        #      (child_one_shot.system_prompt — el prompt del sub-agente ya resuelto).
        #      Es una property que devuelve str, no puede romper el never-raises.
        # -----------------------------------------------------------------------
        effective_system_prompt = con_footer(
            system_prompt, por_defecto=child_one_shot.system_prompt
        )

        # -----------------------------------------------------------------------
        # 5–8. Call child + catch all failure modes (REQ-DG-6 / REQ-DG-8)
        # -----------------------------------------------------------------------
        try:
            raw = await child_one_shot.execute(
                task=task,
                system_prompt=effective_system_prompt,
                max_iterations=self._max_iterations_per_sub,
                timeout_seconds=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "DelegateTool: agente '%s' excedió timeout_seconds=%d",
                agent_id,
                self._timeout_seconds,
            )
            result = DelegationResult(
                status="failed",
                summary=f"Agent '{agent_id}' exceeded the configured timeout ({self._timeout_seconds}s).",
                reason="timeout",
            )
            return self._build_tool_result(result)
        except ToolLoopMaxIterationsError:
            logger.warning(
                "DelegateTool: agente '%s' alcanzó max_iterations=%d",
                agent_id,
                self._max_iterations_per_sub,
            )
            result = DelegationResult(
                status="failed",
                summary=(
                    f"Agent '{agent_id}' exceeded the maximum number of iterations "
                    f"({self._max_iterations_per_sub})."
                ),
                reason="max_iterations_exceeded",
            )
            return self._build_tool_result(result)
        except Exception as exc:  # noqa: BLE001
            exc_type = type(exc).__name__
            logger.exception("DelegateTool: agente '%s' lanzó %s", agent_id, exc_type)
            result = DelegationResult(
                status="failed",
                summary=f"Agent '{agent_id}' raised an unhandled exception: {exc_type}.",
                details=str(exc),
                reason=f"child_exception:{exc_type}",
            )
            return self._build_tool_result(result)

        # -----------------------------------------------------------------------
        # 7. Parse result (REQ-DG-4 / REQ-DG-5)
        #    parse_delegation_result NUNCA lanza — devuelve DelegationResult con
        #    reason="result_parse_error" si el texto no contiene un bloque ```json```.
        # -----------------------------------------------------------------------
        result = parse_delegation_result(raw)
        logger.debug("DelegateTool: agente '%s' completó con status='%s'", agent_id, result.status)
        return self._build_tool_result(result)

    # ---------------------------------------------------------------------------
    # Async path — REQ-DG-10 / REQ-BGD-*
    # ---------------------------------------------------------------------------

    async def _execute_async(
        self,
        *,
        agent_id: str,
        task: str,
        system_prompt: str | None,
        input_files: tuple[str, ...] = (),
    ) -> ToolResult:
        """Encola la delegación en background y retorna ``task_id`` inmediatamente.

        Flow:
        1. Allow-list check (REQ-DG-2) — fail-fast antes de tocar la cola.
        2. Resolver scope ``(channel, chat_id)`` via ``caller_container.get_turn_scope()``:
           el scope de historial del turno en curso, el mismo en el que se persiste.
           Sin turno activo (ej. el gateway admin) → fallback a ``("", "")``.
        3. ``queue.enqueue(...)`` — devuelve ``bg-N`` en <50ms (REQ-BGD-2).
        4. ToolResult con ``DelegationResult(status="success", summary="Delegation queued",
           details=task_id)`` — el LLM lee ``details`` para anunciarle al usuario el ``bg-N``.

        NUNCA propaga excepciones (REQ-DG-8): si ``enqueue`` falla por algún motivo,
        retorna un ToolResult ``failed`` con ``reason="enqueue_failed"``.
        """
        # 0. Si no hay queue wired, el feature de background-delegation está
        # desactivado a nivel app — fail-fast. El path sync (wait=True) sigue OK.
        if self._queue is None:
            result = DelegationResult(
                status="failed",
                summary="Background delegation is not available (queue not wired).",
                reason="background_delegation_unavailable",
            )
            return self._build_tool_result(result)

        # 1. Allow-list (REQ-DG-2)
        if self._allowed_targets and agent_id not in self._allowed_targets:
            logger.warning(
                "DelegateTool(async): agente '%s' no está en la allow-list %s",
                agent_id,
                self._allowed_targets,
            )
            result = DelegationResult(
                status="failed",
                summary=(
                    f"Agent '{agent_id}' is not in the allowed delegation targets. "
                    f"Valid targets: {', '.join(sorted(self._allowed_targets))}. "
                    "Retry with one of those."
                ),
                reason="target_not_allowed",
            )
            return self._build_tool_result(result)

        # 2. Resolver scope desde el turno activo del caller. NO desde el ctx: el
        # turno de un [bg-N] no tiene ctx, y una delegación relanzada desde ahí se
        # encolaba con ("", "") — su resultado acababa en un historial huérfano
        # (FIX bg-scope-del-turno).
        try:
            scope = self._caller_container.get_turn_scope()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "DelegateTool(async): get_turn_scope() falló: %s — fallback a ('', '')",
                exc,
            )
            scope = None

        channel, chat_id = scope if scope is not None else ("", "")

        # 3. Encolar (REQ-BGD-2)
        try:
            enqueued = await self._queue.enqueue(
                caller_agent_id=self._caller_agent_id,
                target_agent_id=agent_id,
                prompt=task,
                system_prompt=system_prompt,
                channel=channel,
                chat_id=chat_id,
                max_iterations=self._max_iterations_per_sub,
                timeout_seconds=self._timeout_seconds,
                input_files=input_files,
            )
        except InputFilesRejected as rechazo:
            logger.warning(
                "DelegateTool(async): input_files rechazados para '%s' (%s): %s",
                agent_id,
                rechazo.reason,
                rechazo,
            )
            return self._rechazo_de_insumos(rechazo)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "DelegateTool(async): queue.enqueue() falló para target '%s'", agent_id
            )
            result = DelegationResult(
                status="failed",
                summary=f"Could not queue delegation to '{agent_id}': {type(exc).__name__}.",
                details=str(exc),
                reason="enqueue_failed",
            )
            return self._build_tool_result(result)

        # 4. ToolResult "queued" — el LLM lee details para mencionar el bg-N al usuario
        result = DelegationResult(
            status="success",
            summary="Delegation queued",
            details=_detalle_encolada(enqueued),
        )
        logger.debug(
            "DelegateTool(async): delegación '%s' encolada como '%s' (caller=%s scope=%s:%s)",
            agent_id,
            enqueued.task_id,
            self._caller_agent_id,
            channel,
            chat_id,
        )
        return self._build_tool_result(result)

    # ---------------------------------------------------------------------------
    # Resume path — REQ-RES-5/6/7
    # ---------------------------------------------------------------------------

    async def _execute_resume(
        self,
        *,
        task_id: str,
        respuesta: str,
        agent_id: str | None,
        system_prompt: str | None,
        wait: bool,
        input_files: tuple[str, ...],
    ) -> ToolResult:
        """Reanuda la task ``task_id`` (en ``awaiting_input``) con ``respuesta``.

        Los parámetros que no tienen sentido al reanudar se rechazan ANTES de tocar la
        cola, con un ``reason`` estable y sin mutar nada (REQ-RES-6): la task sigue
        esperando su respuesta. Todo ``ResumeOutcome`` distinto de ``resumed`` es ``failed``
        con ``reason`` = el nombre del outcome. NUNCA propaga excepciones (REQ-DG-8).
        """
        if wait:
            return self._rechazo_de_resume(
                "resume_requires_background",
                "resume only works in background mode: do not set wait=true.",
            )
        if input_files:
            return self._rechazo_de_resume(
                "resume_rejects_input_files",
                "input_files cannot be combined with resume: the child keeps its own "
                "working directory.",
            )
        if system_prompt is not None:
            return self._rechazo_de_resume(
                "resume_rejects_system_prompt",
                "system_prompt cannot be combined with resume: the child keeps the prompt "
                "it started with.",
            )
        if not respuesta.strip():
            return self._rechazo_de_resume(
                "resume_answer_empty",
                "task must contain your answer to the child's question when you use resume.",
            )
        if self._queue is None:
            return self._build_tool_result(
                DelegationResult(
                    status="failed",
                    summary="Background delegation is not available (queue not wired).",
                    reason="background_delegation_unavailable",
                )
            )
        objetivo = agent_id.strip() if agent_id is not None and agent_id.strip() else None
        try:
            outcome = await self._queue.resume(
                task_id,
                self._caller_agent_id,
                respuesta,
                target_agent_id=objetivo,
                allowed_targets=self._allowed_targets,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("DelegateTool(resume): queue.resume() falló para '%s'", task_id)
            return self._build_tool_result(
                DelegationResult(
                    status="failed",
                    summary=f"Could not resume {task_id}: {type(exc).__name__}.",
                    details=str(exc),
                    reason="resume_failed",
                )
            )
        return self._build_tool_result(_resultado_de_resume(outcome))

    def _rechazo_de_resume(self, reason: str, summary: str) -> ToolResult:
        return self._build_tool_result(
            DelegationResult(status="failed", summary=summary, reason=reason)
        )

    # ---------------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------------

    def _rechazo_de_insumos(self, rechazo: InputFilesRejected) -> ToolResult:
        """``failed`` con el ``reason`` estable del rechazo; la ruta ofensora va en details."""
        return self._build_tool_result(
            DelegationResult(
                status="failed",
                summary=str(rechazo),
                details=rechazo.path,
                reason=rechazo.reason,
            )
        )

    def _build_tool_result(self, delegation_result: DelegationResult) -> ToolResult:
        """Serializa DelegationResult a JSON y lo envuelve en ToolResult."""
        output_json = delegation_result.model_dump_json()
        success = delegation_result.status == "success"
        return ToolResult(
            tool_name=self.name,
            output=output_json,
            success=success,
            error=delegation_result.reason if not success else None,
        )
