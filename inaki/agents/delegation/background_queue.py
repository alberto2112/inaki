"""BackgroundDelegationQueueAdapter — implementación de ``IBackgroundDelegationQueue``
con estado durable.

Mantiene un dict de tasks in-flight + una cola FIFO consumida por un único
``asyncio.Task`` bajo un ``asyncio.Semaphore`` que limita la concurrencia. Al
terminar cada delegación, inyecta el resultado en el ``(channel, chat_id)``
original via ``ILLMDispatcher.dispatch`` con el marker ``[bg-N] ...``
(REQ-BGD-5, REQ-DG-11). La task se purga del dict SOLO si el dispatch tuvo
éxito (con reintentos); si no se pudo entregar, queda en ``delivery_failed``
con el resultado y el error guardados, visible en el snapshot, hasta que alguien
la cancela (``cancel`` la descarta devolviendo ese resultado).

FIX bg-stuck-task: antes una entrega fallida dejaba la task en ``running`` para
siempre — el hijo había terminado pero el padre la veía "trabajando" horas
después, sin forma de cerrarla, y el resultado vivía solo en una variable local
que se perdía. Ahora el estado es honesto, el resultado sobrevive y cada task
guarda su ``asyncio.Task`` para poder cortarla.

FIX bg-result-delivery: el dispatch corre un turno completo del agente padre,
pero su valor de retorno (la respuesta digerida del padre) se DESCARTABA — se
persistía en el historial y jamás llegaba al canal, así que el usuario esperaba
un anuncio que nunca veía. Ahora, cuando el scope original es un canal
conversacional vivo (lo decide el router: ``is_conversational``), la respuesta se entrega vía
``result_sender`` al mismo ``channel:chat_id``, la narración intermedia fluye
en vivo por un ``IIntermediateSink`` del sender (mismo patrón que ``agent_send``
en el scheduler), y el turno recibe ``skip_marker`` para que el LLM pueda optar
por silencio deliberado respondiendo ``__SKIP__``.

El dict es solo la proyección de lo vivo en este proceso; la verdad durable es el
store SQLite (``background_tasks.db``): estado, resultado, fallo y transcript
sobreviven a un restart (REQ-BGD-8, REQ-BGP-1..4). Un restart marca las vivas como
``interrupted`` y avisa al usuario (REQ-BGD-11). Sin store (``store=None``) o con el
store caído la cola degrada a memoria, con ids ``bg-m{N}`` y un ERROR que nombra la
capacidad perdida (REQ-BGP-8): ahí sí, un restart pierde lo que estaba en vuelo.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from inaki.agents.delegation.failure_report import (
    clasificar_fallo,
    fallo_unknown_target,
    render_aviso_interrupcion,
    render_fallo,
    tool_calls_sin_par,
    ultimo_paso,
)
from inaki.agents.delegation.ports import TranscriptNoReanudable
from inaki.agents.delegation.result_contract import (
    ResultadoRendido,
    con_footer,
    render_pregunta_no_reanudable,
    render_resultado,
)
from inaki.agents.delegation.workdirs import instruccion_para_el_hijo, ruta_relativa
from inaki.kernel._result_parser import parse_delegation_result
from inaki.kernel.domain.background_task import (
    BackgroundTask,
    BackgroundTaskStatus,
    BackgroundTaskView,
    CancelOutcome,
    DelegationFailure,
    EnqueuedTask,
    InputFilesRejected,
    ResumeOutcome,
    TaskInspection,
    WorkdirState,
)
from inaki.kernel.ports.delegation_transcript_port import (
    IDelegationTranscript,
    NullDelegationTranscript,
)
from inaki.shared.errors import AgentNotFoundError
from inaki.shared.message import Message, Role
from inaki.shared.skip_marker import SKIP_MARKER, is_skip_response

if TYPE_CHECKING:
    from inaki.agents.delegation.ports import IBackgroundTaskStore
    from inaki.agents.delegation.workdirs import GestorDeWorkdirs, WorkdirPreparado
    from inaki.kernel.ports.llm_dispatcher_port import ILLMDispatcher
    from inaki.kernel.ports.channel_port import IChannelSender
    from inaki.kernel.run_agent_one_shot import RunAgentOneShotUseCase

logger = logging.getLogger(__name__)

# Reintentos del dispatch del resultado al scope original. El dispatch corre un
# turno del LLM padre: los fallos suelen ser transitorios, así que reintentamos
# unas pocas veces con backoff lineal corto antes de dejar la task visible.
_DISPATCH_ATTEMPTS = 3
_DISPATCH_RETRY_DELAY = 0.5  # segundos; el delay efectivo es delay * intento

# Capacidad que se pierde cuando el store falla: el prefijo de TODO log de degradación, para
# que un operador pueda buscarlo (REQ-BGP-8).
_CAPACIDAD_PERDIDA = "background task transcript/durable state unavailable"

# Estados desde los que la task ya no vive en el dict de proceso (siguen en el store).
_TERMINALES = frozenset({"delivered", "cancelled", "dismissed"})

# Cuánto esperar, en ``stop``, a que los hijos cortados terminen de cancelarse.
_STOP_ESPERA_HIJOS = 5.0

# Pasos del transcript que se miran, al cerrar una task cortada, para decir qué estaba haciendo.
_PASOS_PARA_EL_AVISO = 20

_DETALLE_INTERRUPCION = {
    "stopped": "the daemon stopped (reload or shutdown)",
    "found_at_startup": "found unfinished at startup (crash or kill)",
}
_ENTREGA_CORTADA = "delivery interrupted by a daemon stop; it may or may not have reached you"

# Estados en los que el store da una task por terminada: a ``cancel`` le responde ``finished``.
# ``interrupted`` ya avisada también: no avanza sola y no queda pendiente de nada.
_ESTADOS_TERMINADOS = frozenset({"delivered", "cancelled", "dismissed", "interrupted"})


@dataclass(frozen=True)
class _Desenlace:
    """Lo que dejó el hijo: el mensaje para el padre y los campos que se persisten."""

    content: str
    result_status: str | None = None
    parse_error: bool = False
    failure: DelegationFailure | None = None
    iterations_used: int | None = None
    # Solo con ``pausa``: el hijo cerró con ``needs_input`` y la task se pausa en
    # ``awaiting_input`` (snapshot ya hecho) en vez de entregarse. ``content`` es entonces el
    # ``[bg-N] needs_input: ...`` que se le inyecta al padre.
    question: str | None = None
    pausa: bool = False
    # Solo con ``pausa``: el mensaje para el padre si la pausa no llega a persistirse y hay que
    # degradarla a una entrega NO reanudable (``_pausar``). Ya lleva el aviso de workdir.
    content_no_reanudable: str | None = None


@dataclass(frozen=True)
class _Reanudacion:
    """Lo que viaja en MEMORIA entre ``resume`` y el arranque del tramo 2 (REQ-RES-12).

    Si el proceso muere antes de que el tramo corra, la respuesta se pierde y la task queda
    ``interrupted`` con aviso: nunca vuelve sola a ``awaiting_input``.
    """

    respuesta: str
    # Recorder REABIERTO: trace precargado y ``seq`` en el último valor.
    recorder: IDelegationTranscript
    # Mensajes que ya tenía el transcript antes del tramo: el historial previo del hijo y el
    # ``desde_seq`` del snapshot si el tramo vuelve a pausar.
    n_previos: int


def _tarea_del_hijo(task: BackgroundTask) -> str:
    """Mensaje de tarea del hijo: el prompt original más, si hay workdir, dónde trabajar.

    La fila guarda el prompt ORIGINAL; la instrucción se agrega recién al ejecutar.
    """
    if task.workdir is None:
        return task.prompt
    return task.prompt + instruccion_para_el_hijo(task.workdir, task.input_files)


def _con_aviso_de_workdir(task: BackgroundTask, content: str) -> str:
    """Agrega al resultado dónde quedó lo que el hijo produjo (REQ-BGW-10, SHOULD)."""
    if task.workdir is None:
        return content
    return (
        f"{content}\n\nWorking directory: {ruta_relativa(task.workdir)} "
        "(deleted when this task is pruned)."
    )


def _fallo_interno(task: BackgroundTask, tipo: str) -> tuple[str, DelegationFailure]:
    """Reporte (para el padre) y fallo clasificado de un error INTERNO del ciclo de vida.

    No es un fallo del hijo sino de la maquinaria que lo rodea, pero al padre le importa lo
    mismo: la delegación no dio resultado. Si ``render_fallo`` también fallara, se cae a un
    texto fijo: el rescate no puede depender de lo mismo que puede haber reventado.
    """
    falla = DelegationFailure(
        kind="exception",
        detail=f"internal error in the delegation runner ({tipo})",
        iterations_used=task.iterations_used or 0,
    )
    try:
        return render_fallo(task, falla), falla
    except Exception:  # noqa: BLE001
        return (
            f"[{task.id}] failed (exception): internal error in the delegation runner "
            f"({tipo}). Use background_tasks(action=inspect, task_id={task.id}) for details.",
            falla,
        )


def _render_pregunta_reenviada(task: BackgroundTask) -> str:
    """Mensaje al padre con la pregunta de una ``awaiting_input`` que nunca le llegó (REQ-BGD-14).

    Reusa el ``[bg-N] needs_input: ...`` persistido (ya trae cómo continuar y el workdir) y
    le avisa que es un reenvío, para que no lo trate como una pregunta nueva.
    """
    base = task.result or f"[{task.id}] needs_input: {task.question or '(question unavailable)'}"
    return (
        f"{base}\n\n(Re-sent after a daemon restart: this question was still pending and "
        "the child is still waiting for the answer.)"
    )


def _sigue_en_la_pausa(task: BackgroundTask, pausada_en: datetime) -> bool:
    """¿La task sigue en la pausa que se marcó en ``pausada_en``?

    El tramo 1 queda vivo dentro del dispatch de su pregunta mientras el padre puede reanudar la
    task y el tramo 2 volver a pausar: el estado solo no distingue una pausa de otra, el
    ``finished_at`` (que cada pausa fija y cada resume limpia) sí.
    """
    return task.status == "awaiting_input" and task.finished_at == pausada_en


def _iteraciones_usadas(trace: list[Message]) -> int:
    """Iteraciones del tool loop = respuestas del LLM en el transcript."""
    return sum(1 for m in trace if m.role is Role.ASSISTANT)


class BackgroundDelegationQueueAdapter:
    """Cola durable (con degradación a memoria) + consumer asyncio para delegaciones async."""

    def __init__(
        self,
        *,
        dispatcher: "ILLMDispatcher",
        one_shot_resolver: Callable[[str, str], "RunAgentOneShotUseCase | None"],
        max_concurrent: int = 3,
        result_sender: "IChannelSender | None" = None,
        store: "IBackgroundTaskStore | None" = None,
        workdirs: "GestorDeWorkdirs | None" = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._one_shot_resolver = one_shot_resolver
        # Sin sender no hay entrega al canal (modo headless: tests / instancias
        # sin canales conversacionales). Producción siempre inyecta el router.
        self._result_sender = result_sender
        self._tasks: dict[str, BackgroundTask] = {}
        # asyncio.Task de cada delegación lanzada por el consumer: es lo que
        # ``cancel`` corta. Se limpia sola al terminar (done callback).
        self._handles: dict[str, asyncio.Task] = {}
        # Handles de tramos 1 que ``resume`` sacó de ``_handles`` mientras seguían dentro del
        # dispatch de la pregunta (el turno del padre que la procesa): ``cancel`` no debe
        # cortarlos, pero ``stop`` sí tiene que cortarlos y esperarlos.
        self._en_entrega: set[asyncio.Task] = set()
        # Ids con un ``resume`` en curso (reservados) y las respuestas esperando su tramo 2.
        self._reanudando: set[str] = set()
        self._reanudaciones: dict[str, _Reanudacion] = {}
        self._queue: asyncio.Queue[BackgroundTask] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._consumer_task: asyncio.Task | None = None
        # Store durable (fuente de verdad de toda task). ``None`` = modo en memoria: o no
        # se inyectó, o ``start()`` no pudo abrirlo (ya se logueó en ERROR). El dict
        # ``_tasks`` es la proyección de proceso de las vivas; toda transición pasa por
        # ``_transicionar`` y va siempre dict → store, nunca al revés.
        self._store = store
        # Tasks cuya persistencia se rompió: se saltean sus escrituras siguientes.
        self._sin_persistir: set[str] = set()
        # Directorios de trabajo por delegación (``delegation.grant_workdir``). ``None`` =
        # ningún caller los tiene. ``_en_creacion`` protege del barrido de arranque a un
        # directorio ya creado cuya fila todavía no existe.
        self._workdirs = workdirs
        self._en_creacion: set[str] = set()
        # Aviso único tras el arranque (``programar_avisos_post_arranque``) y los ids cuyo
        # transcript durable quedó cortado (seam para ``inspect``: el flag vive en el recorder,
        # que no sobrevive al hijo).
        self._aviso_task: asyncio.Task[None] | None = None
        self._transcripts_incompletos: set[str] = set()
        # Contador de los ids en memoria (``bg-m{N}``): sin store, o cuando falla.
        self._mem_counter: int = 0

    async def enqueue(
        self,
        *,
        caller_agent_id: str,
        target_agent_id: str,
        prompt: str,
        system_prompt: str | None,
        channel: str,
        chat_id: str,
        max_iterations: int,
        timeout_seconds: int,
        input_files: Sequence[str] = (),
    ) -> EnqueuedTask:
        """Registra una nueva delegación y devuelve su ``task_id`` (REQ-BGD-2).

        El id lo asigna el store (``bg-{rowid}``, durable). Si no hay store, o escribir
        la fila falla, la delegación sigue con un id en memoria (``bg-m{N}``): el disco
        es una dependencia externa, y no tener transcript no justifica perder el trabajo
        (REQ-BGP-8). El ``bg-m`` es honesto sobre lo que falta: ``inspect`` y el aviso
        tras un reinicio no cubren a esa task.
        """
        workdir, workdir_unavailable, copiados = await self._preparar_workdir(
            caller_agent_id, input_files
        )
        try:
            task = BackgroundTask(
                id="",
                caller_agent_id=caller_agent_id,
                target_agent_id=target_agent_id,
                prompt=prompt,
                system_prompt=system_prompt,
                channel=channel,
                chat_id=chat_id,
                max_iterations=max_iterations,
                timeout_seconds=timeout_seconds,
                started_at=datetime.now(timezone.utc),
                status="queued",
                workdir=workdir,
                input_files=copiados,
            )
            task.id = await self._registrar(task)
            self._tasks[task.id] = task
            self._queue.put_nowait(task)
        except BaseException:
            # Una cancelación en medio del registro dejaría un directorio sin task.
            if workdir is not None and self._workdirs is not None:
                self._workdirs.borrar("(enqueue abortado)", workdir)
            raise
        finally:
            if workdir is not None:
                self._en_creacion.discard(workdir)
        return EnqueuedTask(
            task_id=task.id,
            workdir=workdir,
            workdir_unavailable=workdir_unavailable,
            input_files=copiados,
        )

    async def _preparar_workdir(
        self, caller_agent_id: str, input_files: Sequence[str]
    ) -> tuple[str | None, bool, list[str]]:
        """Workdir (y su contenido inicial) de la task: ``(ruta, no_disponible, insumos)``.

        Con ``input_files`` el directorio nace CON los insumos copiados y cualquier
        problema aborta el encolado (``InputFilesRejected``): arrancar al hijo sin lo que
        se le prometió es peor que no arrancarlo. Sin insumos, un workdir que no se pudo
        crear degrada ruidosamente (ver ``_crear_workdir``).

        La copia es síncrona (hasta 50 MiB de E/S): se mueve a un hilo para no bloquear el
        event loop, que atiende a todos los canales.
        """
        gestor = self._workdirs
        if not input_files:
            ruta, no_disponible = await self._crear_workdir(caller_agent_id)
            return ruta, no_disponible, []
        if gestor is None or not gestor.tiene_workdir(caller_agent_id):
            raise InputFilesRejected("input_files_require_workdir")
        pedidos = list(input_files)

        def preparar_y_registrar() -> "WorkdirPreparado":
            # El registro va en el mismo hilo, pegado a la creación: la ventana en la que
            # el barrido de arranque podría ver un directorio sin fila se reduce al mínimo.
            preparado = gestor.preparar(caller_agent_id, pedidos)
            self._en_creacion.add(str(preparado.ruta))
            return preparado

        preparado = await asyncio.to_thread(preparar_y_registrar)
        return str(preparado.ruta), False, preparado.input_files

    async def _crear_workdir(self, caller_agent_id: str) -> tuple[str | None, bool]:
        """Crea el workdir del caller si lo tiene. Devuelve ``(ruta, no_disponible)``.

        Sin flag → ``(None, False)``. Si el flag está pero el directorio no se puede crear
        (``dwss`` no escribible...) la delegación se encola igual SIN workdir: degradación
        ruidosa (ERROR que nombra la capacidad perdida) y ``no_disponible=True`` para que
        el resultado ``queued`` lo diga en vez de fingir un directorio.

        La ruta queda en ``_en_creacion`` hasta que existe la fila que la referencia: el
        barrido de huérfanos no debe borrar un directorio recién creado.

        ``crear`` (mkdir + ``secrets``) es E/S síncrona: corre en un hilo, como ``preparar``,
        para no bloquear el event loop. El registro en ``_en_creacion`` va en el mismo hilo,
        pegado a la creación.
        """
        gestor = self._workdirs
        if gestor is None or not gestor.tiene_workdir(caller_agent_id):
            return None, False

        def crear_y_registrar() -> Path | None:
            creada = gestor.crear(caller_agent_id)
            if creada is not None:
                self._en_creacion.add(str(creada))
            return creada

        try:
            ruta = await asyncio.to_thread(crear_y_registrar)
        except OSError as exc:
            logger.error(
                "background task workdir unavailable: no se pudo crear el directorio de "
                "trabajo de '%s' (%s: %s); la delegación se encola SIN workdir",
                caller_agent_id,
                type(exc).__name__,
                exc,
            )
            return None, True
        if ruta is None:  # inalcanzable: tiene_workdir ya se verificó
            return None, False
        return str(ruta), False

    async def _registrar(self, task: BackgroundTask) -> str:
        """Id definitivo de ``task``: el durable del store o, degradando, uno en memoria."""
        if self._store is not None:
            try:
                return await self._store.create(task)
            except Exception as exc:  # noqa: BLE001 — el disco no puede matar la delegación
                id_memoria = self._id_en_memoria()
                self._sin_persistir.add(id_memoria)
                self._loguear_perdida(
                    id_memoria,
                    f"no se pudo registrar la delegación en el store ({type(exc).__name__}: "
                    f"{exc}); corre con id en memoria, sin transcript, sin inspect y sin aviso "
                    "tras un reinicio",
                )
                return id_memoria
        return self._id_en_memoria()

    def _id_en_memoria(self) -> str:
        self._mem_counter += 1
        return f"bg-m{self._mem_counter}"

    def snapshot_inflight(self, caller_agent_id: str) -> list[BackgroundTaskView]:
        """Devuelve las tasks vivas del caller (incluye ``delivery_failed``),
        ordenadas por start time."""
        now = datetime.now(timezone.utc)
        propias = [t for t in self._tasks.values() if t.caller_agent_id == caller_agent_id]
        propias.sort(key=lambda t: t.started_at)
        return [BackgroundTaskView.from_task(t, now=now) for t in propias]

    async def cancel(self, task_id: str, caller_agent_id: str) -> CancelOutcome:
        """Corta o descarta una task del caller (ver ``IBackgroundDelegationQueue``).

        La decisión y la mutación del dict ocurren ANTES del primer ``await`` (la
        persistencia): sin ``await`` de por medio no hay ventana para que el consumer la
        cambie de estado entre el chequeo y la acción, ni para cancelarla dos veces.

        Lo que no está en el dict ya no está vivo: se consulta el store para distinguir
        "ya terminó" (``finished``, con su estado) de "no la tengo" (``not_found``). Una
        task de OTRO caller es siempre ``not_found``, igual que una inexistente.
        """
        task = self._tasks.get(task_id)
        if task is None or task.caller_agent_id != caller_agent_id:
            return await self._cancel_de_terminada(task_id, caller_agent_id)
        if task.status == "delivering":
            return CancelOutcome(outcome="delivering", task_id=task_id)
        ahora = datetime.now(timezone.utc)
        if task.status == "awaiting_input":
            # Nada corre: la pregunta se abandona. Sin ``handle.cancel()``: el handle del tramo 1
            # puede seguir dentro del dispatch, o sea, ser el turno del padre que la procesa.
            resultado = task.result
            await self._transicionar(task, "dismissed", notified=True)
            logger.info("background-delegation %s: descartada (esperaba respuesta)", task_id)
            return CancelOutcome(
                outcome="dismissed", task_id=task_id, result=resultado, status="awaiting_input"
            )
        if task.status in ("delivery_failed", "interrupted"):
            # Nada corre: se descarta devolviendo lo que tuviera guardado, para que
            # quien la descarta no pierda el trabajo del hijo.
            previo = task.status
            resultado, error = task.result, task.error
            await self._transicionar(task, "dismissed", notified=True)
            logger.info("background-delegation %s: descartada (estaba %s)", task_id, previo)
            return CancelOutcome(
                outcome="dismissed", task_id=task_id, result=resultado, error=error, status=previo
            )
        # queued/running: si el consumer ya la lanzó, se corta su asyncio.Task; si todavía
        # está en la cola, ``_run_task`` la saltea al no encontrarla en ``_tasks``.
        previo = task.status
        handle = self._handles.pop(task_id, None)
        if handle is not None:
            handle.cancel()
        await self._transicionar(task, "cancelled", finished_at=ahora)
        logger.info("background-delegation %s: cancelada (estaba %s)", task_id, previo)
        return CancelOutcome(outcome="cancelled", task_id=task_id)

    async def _cancel_de_terminada(self, task_id: str, caller_agent_id: str) -> CancelOutcome:
        """``cancel`` de una task que no está viva: ``finished`` si el store la conoce como
        del caller y ya terminó, ``delivering`` si su entrega está por completarse, y
        ``not_found`` en cualquier otro caso (no existe, se podó, es de otro, o no hay store)."""
        no_encontrada = CancelOutcome(outcome="not_found", task_id=task_id)
        if self._store is None or task_id in self._sin_persistir or task_id.startswith("bg-m"):
            return no_encontrada
        try:
            fila = await self._store.get(task_id, caller_agent_id)
        except Exception as exc:  # noqa: BLE001 — cancel nunca lanza por el disco
            self._loguear_perdida(
                task_id,
                f"no se pudo consultar el store para cancelar ({type(exc).__name__}: {exc}); "
                "se responde que no la tiene",
            )
            return no_encontrada
        if fila is None:
            return no_encontrada
        if fila.status == "delivering":
            # Ya salió del dict (se entregó) pero el UPDATE final todavía no se aplicó.
            return CancelOutcome(outcome="delivering", task_id=task_id)
        if fila.status == "awaiting_input":
            # Solo está en el store (la rehidratación falló): se descarta igual.
            try:
                await self._store.update(task_id, status="dismissed", notified=True)
            except Exception as exc:  # noqa: BLE001 — cancel nunca lanza por el disco
                self._loguear_perdida(
                    task_id,
                    f"no se pudo descartar la pregunta pendiente ({type(exc).__name__}: {exc}); "
                    "se responde que no la tiene",
                )
                return no_encontrada
            return CancelOutcome(
                outcome="dismissed", task_id=task_id, result=fila.result, status="awaiting_input"
            )
        if fila.status in _ESTADOS_TERMINADOS:
            return CancelOutcome(outcome="finished", task_id=task_id, status=fila.status)
        return no_encontrada

    async def resume(
        self,
        task_id: str,
        caller_agent_id: str,
        answer: str,
        *,
        target_agent_id: str | None = None,
        allowed_targets: Collection[str] = (),
    ) -> ResumeOutcome:
        """Reanuda una task en ``awaiting_input`` con ``answer`` (ver ``IBackgroundDelegationQueue``).

        Decisión y reserva son SÍNCRONAS sobre el dict (sin ``await`` entre el chequeo y
        ``_reanudando.add``): dos resume simultáneos no pueden ganar los dos. La carga del
        transcript sí es un ``await``, así que después se re-chequea todo lo que pudo cambiar
        (un ``cancel``, un ``stop``). Solo ``resumed`` cambia el estado de la fila; la única otra
        mutación es la marca ``transcript_incomplete`` cuando la reapertura detecta un
        historial inconsistente.
        """
        task = self._tasks.get(task_id)
        if task is None or task.caller_agent_id != caller_agent_id:
            return await self._resume_de_no_viva(task_id, caller_agent_id)
        rechazo = self._rechazo_de_resume(task, target_agent_id, allowed_targets)
        if rechazo is not None:
            return rechazo
        assert self._store is not None  # _rechazo_de_resume lo garantiza
        self._reanudando.add(task_id)
        recorder: IDelegationTranscript | None = None
        inconsistente: TranscriptNoReanudable | None = None
        try:
            try:
                recorder = await self._store.reabrir_transcript(task_id)
            except TranscriptNoReanudable as exc:
                inconsistente = exc
            except Exception as exc:  # noqa: BLE001 — el disco no puede romper al padre
                self._loguear_perdida(
                    task_id,
                    f"no se pudo reabrir el transcript para reanudar ({type(exc).__name__}: "
                    f"{exc}); la task sigue esperando respuesta, se puede reintentar",
                )
                return ResumeOutcome(
                    outcome="unavailable",
                    task_id=task_id,
                    detail="the transcript store failed while resuming; it can be retried",
                )
            # Re-chequeo SINCRÓNICO: durante el await pudo llegar un cancel o un stop.
            if not self._sigue_esperando(task):
                return ResumeOutcome(outcome="not_awaiting", task_id=task_id, status=task.status)
            if inconsistente is not None:
                self._loguear_perdida(
                    task_id,
                    f"el transcript persistido no permite reanudar ({inconsistente.motivo}); la "
                    "task queda marcada transcript_incomplete y solo se puede cancelar",
                )
                await self._marcar_transcript_incompleto(task)
                # El motivo del store está en español y ya quedó en el log: al LLM le llega
                # un texto fijo en inglés (el ``detail`` entra tal cual en su tool result).
                return ResumeOutcome(
                    outcome="transcript_incomplete",
                    task_id=task_id,
                    detail="its saved transcript is inconsistent",
                )
            if not self._consumer_vivo():
                return self._resume_no_disponible(task_id)
            assert recorder is not None
            return await self._reencolar(task, recorder, answer)
        finally:
            self._reanudando.discard(task_id)

    async def _reencolar(
        self, task: BackgroundTask, recorder: IDelegationTranscript, answer: str
    ) -> ResumeOutcome:
        """``awaiting_input`` → ``queued`` con la respuesta lista para el tramo 2.

        Todo lo previo al primer ``await`` de ``_transicionar`` es síncrono: ningún otro resume,
        ``cancel`` ni ``stop`` ve un estado intermedio. El handle del tramo 1, si todavía está
        dentro del dispatch de la pregunta, pasa a ``_en_entrega``: así un ``cancel`` de la task
        re-encolada no corta el turno del padre, y el handle que registre el consumer para el
        tramo 2 no choca con él.
        """
        restantes = task.max_iterations - (task.iterations_used or 0)
        self._reanudaciones[task.id] = _Reanudacion(
            respuesta=answer, recorder=recorder, n_previos=len(recorder.trace)
        )
        task.question = None
        handle1 = self._handles.pop(task.id, None)
        if handle1 is not None:
            self._en_entrega.add(handle1)
        await self._transicionar(
            task,
            "queued",
            result=None,
            error=None,
            failure=None,
            result_status=None,
            result_parse_error=False,
            finished_at=None,
            notified=True,
        )
        if self._tasks.get(task.id) is not task or task.status != "queued":
            # Un cancel/stop se coló durante la escritura: el tramo 2 ya no corre.
            return ResumeOutcome(outcome="not_awaiting", task_id=task.id, status=task.status)
        self._queue.put_nowait(task)
        logger.info(
            "background-delegation %s: reanudada (le quedan %d iteraciones)", task.id, restantes
        )
        return ResumeOutcome(
            outcome="resumed",
            task_id=task.id,
            target_agent_id=task.target_agent_id,
            iterations_left=restantes,
        )

    def _sigue_esperando(self, task: BackgroundTask) -> bool:
        return self._tasks.get(task.id) is task and task.status == "awaiting_input"

    def _consumer_vivo(self) -> bool:
        return self._consumer_task is not None and not self._consumer_task.done()

    @staticmethod
    def _resume_no_disponible(task_id: str) -> ResumeOutcome:
        return ResumeOutcome(
            outcome="unavailable",
            task_id=task_id,
            detail="the background queue is not running; try again later",
        )

    def _rechazo_de_resume(
        self, task: BackgroundTask, target_agent_id: str | None, allowed_targets: Collection[str]
    ) -> ResumeOutcome | None:
        """Precondiciones SINCRÓNICAS de ``resume`` sobre una task viva del caller.

        ``None`` = se puede reservar. Ningún rechazo cambia el estado de la task. Los chequeos
        van de lo que el padre puede corregir a lo que no: estado, reserva ajena, transcript,
        presupuesto, y recién entonces target y consumer.
        """
        if task.status != "awaiting_input":
            return ResumeOutcome(outcome="not_awaiting", task_id=task.id, status=task.status)
        if task.id in self._reanudando:
            return ResumeOutcome(outcome="already_resuming", task_id=task.id)
        if self._es_transcript_incompleto(task):
            return ResumeOutcome(
                outcome="transcript_incomplete",
                task_id=task.id,
                detail="its saved transcript is incomplete",
            )
        if task.max_iterations - (task.iterations_used or 0) < 1:
            return ResumeOutcome(outcome="no_iterations_left", task_id=task.id)
        if target_agent_id is not None and target_agent_id != task.target_agent_id:
            return ResumeOutcome(
                outcome="target_mismatch", task_id=task.id, target_agent_id=task.target_agent_id
            )
        if allowed_targets and task.target_agent_id not in allowed_targets:
            return ResumeOutcome(
                outcome="target_not_allowed", task_id=task.id, target_agent_id=task.target_agent_id
            )
        try:
            resoluble = self._one_shot_resolver(task.caller_agent_id, task.target_agent_id)
        except Exception:  # noqa: BLE001 — una config rota es un target que no se resuelve
            resoluble = None
        if resoluble is None:
            return ResumeOutcome(
                outcome="unknown_target", task_id=task.id, target_agent_id=task.target_agent_id
            )
        if self._store is None or not self._consumer_vivo():
            return self._resume_no_disponible(task.id)
        return None

    async def _resume_de_no_viva(self, task_id: str, caller_agent_id: str) -> ResumeOutcome:
        """``resume`` de algo que no está en el dict: lo dice el store.

        ``not_found`` cubre inexistente, podada y de otro caller sin distinguirlas (el id es
        monotónico: una ausente por debajo del máximo se podó, y "no la tengo" es cierto en
        todos los casos). Una ``bg-m*`` nunca tuvo registro durable: se dice, sin afirmar que no
        existió.
        """
        if task_id.startswith("bg-m") or self._store is None or task_id in self._sin_persistir:
            return ResumeOutcome(
                outcome="not_found", task_id=task_id, detail="it has no durable record"
            )
        try:
            fila = await self._store.get(task_id, caller_agent_id)
        except Exception as exc:  # noqa: BLE001 — resume nunca lanza por el disco
            self._loguear_perdida(
                task_id,
                f"no se pudo consultar el store para reanudar ({type(exc).__name__}: {exc})",
            )
            return ResumeOutcome(
                outcome="unavailable",
                task_id=task_id,
                detail="the task store failed while resuming; it can be retried",
            )
        if fila is None:
            return ResumeOutcome(outcome="not_found", task_id=task_id)
        if fila.status != "awaiting_input":
            return ResumeOutcome(outcome="not_awaiting", task_id=task_id, status=fila.status)
        # Espera respuesta pero este proceso no la tiene cargada (la rehidratación falló):
        # reanudarla a ciegas saltearía las precondiciones que viven en el dict.
        return ResumeOutcome(
            outcome="unavailable",
            task_id=task_id,
            detail="it is waiting for an answer but is not loaded in this process; "
            "try again after a restart",
        )

    async def inspect(
        self, task_id: str, caller_agent_id: str, *, last_n: int
    ) -> TaskInspection | None:
        """Estado, desenlace y últimos ``last_n`` pasos de una task del caller (REQ-BGI-1).

        Es de solo lectura: no transiciona la task, no toca ``notified`` ni dispara dispatch
        (REQ-BGI-4). ``None`` es "no la tengo": no existe, se podó, es de OTRO caller (idéntico
        a inexistente, REQ-BGI-2) o es una ``bg-m*`` que ya no está viva.

        Una task viva se muestra desde el dict, que es la verdad del proceso (si el store se
        cayó, la fila está vieja); una terminada, desde el store. El transcript siempre sale
        del store: si no hay (``bg-m*``, store caído, lectura fallida) la inspección sale con
        ``persisted=False`` y sin pasos, para que quien la muestre lo diga.
        """
        viva = self._tasks.get(task_id)
        if viva is not None and viva.caller_agent_id != caller_agent_id:
            return None
        if self._store is None or task_id.startswith("bg-m"):
            return await self._inspeccion_sin_transcript(viva)
        try:
            fila = await self._store.get(task_id, caller_agent_id)
            pasos, total = (
                await self._store.messages(task_id, last_n=last_n) if fila is not None else ([], 0)
            )
        except Exception as exc:  # noqa: BLE001 — leer no puede romper al agente
            self._loguear_perdida(
                task_id,
                f"no se pudo leer el transcript para inspect ({type(exc).__name__}: {exc}); "
                "se muestra solo el estado que hay en memoria, sin pasos",
            )
            return await self._inspeccion_sin_transcript(viva)
        task = viva if viva is not None else fila
        if task is None:
            return None
        return TaskInspection(
            task=task.model_copy(deep=True),
            messages=pasos,
            messages_total=total,
            persisted=True,
            workdir=await self._describir_workdir(task),
            transcript_incomplete=self._es_transcript_incompleto(task),
        )

    async def _inspeccion_sin_transcript(
        self, viva: BackgroundTask | None
    ) -> TaskInspection | None:
        """Inspección de una task sin transcript durable: solo vale si sigue viva en memoria."""
        if viva is None:
            return None
        return TaskInspection(
            task=viva.model_copy(deep=True),
            messages=[],
            messages_total=0,
            persisted=False,
            workdir=await self._describir_workdir(viva),
        )

    def _es_transcript_incompleto(self, task: BackgroundTask) -> bool:
        """¿Al transcript de ``task`` le faltan pasos? Lo dicen la fila (sobrevive a un
        reinicio), la memoria de este proceso y una persistencia rota (``_sin_persistir``)."""
        return (
            task.transcript_incomplete
            or task.id in self._transcripts_incompletos
            or task.id in self._sin_persistir
        )

    async def _describir_workdir(self, task: BackgroundTask) -> WorkdirState | None:
        """Estado del workdir de ``task`` para ``inspect`` (REQ-BGW-9); ``None`` si no tiene.

        Es de solo lectura: lista, nunca crea ni borra. Sin gestor (la config cambió entre
        arranques y la fila todavía trae workdir) no se lista el directorio —el gestor es
        quien aplica la compuerta sobre rutas leídas de disco— y la nota lo dice.
        """
        if task.workdir is None:
            return None
        if self._workdirs is None:
            return WorkdirState(
                path=task.workdir,
                relative_path=ruta_relativa(task.workdir),
                exists=await asyncio.to_thread(Path(task.workdir).is_dir),
                entries=[],
                entries_total=0,
                note="workdir listing unavailable: working directories are not configured",
            )
        return await asyncio.to_thread(self._workdirs.describir, task.workdir)

    async def start(self) -> None:
        """Abre el store, levanta lo que dejó el proceso anterior y lanza el consumer
        (REQ-BGD-1, REQ-BGD-10). Idempotente.

        El barrido de arranque convierte en ``interrupted`` lo que quedó ``queued``/
        ``running`` (el daemon murió sin ``stop()``: crash, SIGKILL) y en ``delivery_failed``
        lo que quedó ``delivering``. Después se poda (filas, workdirs, huérfanos) y recién
        entonces se rehidrata el dict con lo que sobrevivió. Cada paso está aislado: un store
        que falla a mitad loguea la capacidad perdida y el consumer arranca igual.
        """
        if self._consumer_task is not None and not self._consumer_task.done():
            return
        await self._abrir_store()
        await self._cerrar_huerfanas("found_at_startup")
        # FIX (design D8): primero se poda, DESPUÉS se rehidrata. Al revés, una task podada
        # (p. ej. una interrupted sin avisar de más de 14 días) volvía a entrar en el dict:
        # se avisaba y aparecía en el prompt aunque su fila y su workdir ya no existieran.
        await self._podar_y_barrer_workdirs()
        await self._rehidratar()
        self._consumer_task = asyncio.create_task(
            self._loop(), name="background-delegation-consumer"
        )

    async def stop(self) -> None:
        """Corta el consumer y las delegaciones lanzadas SIN dispatchar (REQ-BGD-8).

        Orden (importa): 1) el consumer deja de lanzar; 2) en el dict las ``queued``/
        ``running`` pasan a ``interrupted``, ANTES de cortar a los hijos; 3) se cortan
        los hijos y se espera a que terminen; 4) recién entonces se escribe el store
        (``_cerrar_huerfanas``); 5) se cierra. Escribir con hijos vivos es una carrera:
        uno que termina justo ahí escribiría ``delivering`` encima. Un SIGKILL entre 2 y 4
        lo levanta el barrido del próximo ``start()``.
        """
        if self._consumer_task is None:
            return
        await self._cortar_aviso()
        self._consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._consumer_task
        self._consumer_task = None
        self._marcar_interrumpidas_en_el_dict()
        # Las respuestas de los resume que no llegaron a correr se pierden con el proceso: la
        # task queda ``interrupted`` y el padre se entera (REQ-RES-12).
        self._reanudaciones.clear()
        handles = list({*self._handles.values(), *self._en_entrega})
        for handle in handles:
            handle.cancel()
        if handles:
            _, sin_terminar = await asyncio.wait(handles, timeout=_STOP_ESPERA_HIJOS)
            if sin_terminar:
                logger.warning(
                    "background-delegation: %d delegación(es) no terminaron de cortarse tras "
                    "%.0fs; se sigue con el cierre",
                    len(sin_terminar),
                    _STOP_ESPERA_HIJOS,
                )
        await self._cerrar_huerfanas("stopped")
        await self._cerrar_store()

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    async def _loop(self) -> None:
        """Consumer principal: dispara ``_run_task`` por cada item de la cola."""
        while True:
            task = await self._queue.get()
            if task.id not in self._tasks:
                continue  # cancelada mientras esperaba en la cola
            handle = asyncio.create_task(self._run_task(task), name=f"bg-delegation-{task.id}")
            self._handles[task.id] = handle
            handle.add_done_callback(partial(self._olvidar_handle, task.id))

    def _olvidar_handle(self, task_id: str, handle: asyncio.Task) -> None:
        """Olvida SOLO el handle que terminó: el del tramo 1 no puede borrar el del tramo 2."""
        if self._handles.get(task_id) is handle:
            del self._handles[task_id]
        self._en_entrega.discard(handle)

    # -- store ---------------------------------------------------------------

    async def _abrir_store(self) -> None:
        """Abre el store; si falla, loguea la capacidad perdida y sigue EN MEMORIA."""
        if self._store is None:
            return
        try:
            await self._store.open()
        except Exception as exc:  # noqa: BLE001 — el disco no puede tumbar el arranque
            logger.error(
                "%s: background_tasks.db no se pudo abrir (%s: %s): las delegaciones en "
                "background siguen EN MEMORIA, sin transcript, sin inspect y sin aviso tras "
                "un reinicio",
                _CAPACIDAD_PERDIDA,
                type(exc).__name__,
                exc,
            )
            self._store = None

    async def _cerrar_store(self) -> None:
        if self._store is None:
            return
        try:
            await self._store.close()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "%s: el store no cerró limpio (%s: %s); las escrituras en vuelo pueden "
                "haberse perdido",
                _CAPACIDAD_PERDIDA,
                type(exc).__name__,
                exc,
            )

    async def _podar_y_barrer_workdirs(self) -> None:
        """Poda las filas viejas y limpia los workdirs que quedaron sin fila (REQ-BGP-7,
        REQ-BGW-6/7/8).

        Orden: primero se borra la FILA (``store.prune``) y recién después el directorio, por
        la compuerta de ``GestorDeWorkdirs.borrar``. Un crash entre ambos deja un directorio
        sin fila (lo recoge el barrido siguiente), nunca una fila apuntando a nada. El
        directorio se borra en la ubicación que la fila guardó, aunque el workspace del
        caller haya cambiado; el barrido, en cambio, solo mira las raíces actuales.

        La poda de filas corre SIEMPRE que haya store: la retención no depende de que algún
        caller tenga workdir. Sin store (nunca inyectado o caído) no hay nada que podar y,
        con workdirs, tampoco se barre: no se sabe qué está referenciado. Cada paso está
        aislado: ninguno puede tumbar ``start()``.
        """
        if self._store is None:
            if self._workdirs is not None:
                logger.warning(
                    "background-delegation: no se barren workdirs huérfanos: sin store no se "
                    "sabe cuáles están referenciados"
                )
            return
        try:
            poda = await self._store.prune(datetime.now(timezone.utc))
        except Exception as exc:  # noqa: BLE001 — la limpieza no puede tumbar el arranque
            logger.error(
                "%s: la poda de tareas viejas falló (%s: %s); no se borró ninguna fila",
                _CAPACIDAD_PERDIDA,
                type(exc).__name__,
                exc,
            )
        else:
            for task_id, ruta in poda.workdirs:
                await self._borrar_workdir_podado(task_id, ruta)
        if self._workdirs is None:
            return
        try:
            referenciados = await self._store.workdirs_referenciados()
            await asyncio.to_thread(
                self._workdirs.barrer, referenciados, en_creacion=self._en_creacion
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "background-delegation: el barrido de workdirs huérfanos falló (%s: %s); "
                "pueden quedar directorios sin fila bajo dwss/",
                type(exc).__name__,
                exc,
            )

    async def _borrar_workdir_podado(self, task_id: str, ruta: str) -> None:
        """Borra el directorio de una fila ya podada. Sin gestor no se borra nada (ni se
        puede: la compuerta de borrado vive en él)."""
        if self._workdirs is None:
            logger.error(
                "background-delegation: el workdir '%s' de %s quedó sin borrar: no hay gestor "
                "de workdirs configurado",
                ruta,
                task_id,
            )
            return
        try:
            await asyncio.to_thread(self._workdirs.borrar, task_id, ruta)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "background-delegation: no se pudo borrar el workdir '%s' de %s (%s: %s)",
                ruta,
                task_id,
                type(exc).__name__,
                exc,
            )

    # -- aviso único tras el arranque -----------------------------------------

    def transcript_incompleto(self, task_id: str) -> bool:
        """``True`` si el transcript durable de la task quedó cortado por un fallo del disco.

        Solo cubre las corridas de ESTE proceso: el flag vive en el recorder del hijo, que no
        sobrevive a un reinicio. ``inspect`` lo usa para decir "faltan pasos" en vez de
        mostrar un transcript trunco como si fuera el completo.
        """
        return task_id in self._transcripts_incompletos

    def programar_avisos_post_arranque(self) -> None:
        """Programa el aviso ÚNICO de lo que el reinicio cortó y vuelve enseguida (REQ-BGD-11).

        No bloquea: el aviso dispara un turno completo del agente padre (llamada al LLM) y el
        arranque no puede esperarlo. Quién y cuándo lo llama (después de que los canales
        estén arriba) lo decide el composition root. Idempotente: mientras el aviso está en
        vuelo, otra llamada no hace nada.
        """
        if self._aviso_task is not None and not self._aviso_task.done():
            return
        self._aviso_task = asyncio.create_task(
            self._avisar_tras_arranque(), name="background-delegation-startup-notice"
        )

    async def _cortar_aviso(self) -> None:
        tarea, self._aviso_task = self._aviso_task, None
        if tarea is None or tarea.done():
            return
        tarea.cancel()
        with suppress(asyncio.CancelledError):
            await tarea

    async def _avisar_tras_arranque(self) -> None:
        """Un aviso por (caller, canal, chat): interrumpidas, entregas barridas y preguntas.

        Las interrumpidas y las ``delivery_failed`` que dejó el barrido de arranque tienen
        ``notified=False`` (una ``delivery_failed`` de runtime ya es visible en el prompt y
        no lo necesita). Las ``awaiting_input`` sin notificar (el daemon murió entre persistir
        la pausa y despachar la pregunta, o el dispatch falló) se re-despachan AGRUPADAS por
        conversación, igual que las interrumpidas. Cada conversación es independiente: el
        fallo de una no frena a las demás.
        """
        if self._store is None:
            return
        pendientes = [
            t
            for t in self._tasks.values()
            if not t.notified and t.status in ("interrupted", "delivery_failed", "awaiting_input")
        ]
        grupos: dict[tuple[str, str, str], list[BackgroundTask]] = {}
        for t in sorted(pendientes, key=lambda t: t.started_at):
            grupos.setdefault((t.caller_agent_id, t.channel, t.chat_id), []).append(t)
        for tasks in grupos.values():
            if self._sin_canal_al_que_avisar(tasks[0]):
                self._dejar_visibles_sin_avisar(tasks)
                continue
            await self._avisar_conversacion(tasks)

    def _sin_canal_al_que_avisar(self, task: BackgroundTask) -> bool:
        """``True`` si el router dice que el scope de ``task`` no es un canal conversacional.

        Sin ``result_sender`` no hay quién juzgue el canal (ensamblados sin canales): se
        conserva el comportamiento de avisar. Es el mismo criterio que usa el resultado de
        una delegación (``_conversational_target``).
        """
        return self._result_sender is not None and self._conversational_target(task) is None

    def _dejar_visibles_sin_avisar(self, tasks: list[BackgroundTask]) -> None:
        """Scope sin canal conversacional (CLI/REST, ``("", "")``, canal ya no vivo): no se
        despacha ningún turno de aviso.

        FIX (REQ-BGD-11 S4): ese turno caería en un historial que nadie lee y, peor, la task se
        marcaría ``notified`` y saldría del dict, o sea, el padre dejaría de verla. Se queda
        con ``notified=False`` en ``_tasks``: sigue en la sección in-flight del padre hasta
        que la descarte con ``background_tasks(action=cancel)`` o la retención la pode
        (``interrupted`` sin avisar, a los 14 días).
        """
        primera = tasks[0]
        logger.info(
            "background-delegation: sin canal conversacional al que avisar en el scope "
            "%r:%r de '%s'; no se despacha aviso. Siguen visibles en la sección in-flight "
            "del padre hasta que las descarte con cancel o la retención las pode: %s",
            primera.channel,
            primera.chat_id,
            primera.caller_agent_id,
            ",".join(t.id for t in tasks),
        )

    async def _avisar_conversacion(self, tasks: list[BackgroundTask]) -> None:
        interrumpidas = [t for t in tasks if t.status == "interrupted"]
        barridas = [t for t in tasks if t.status == "delivery_failed"]
        preguntas = [t for t in tasks if t.status == "awaiting_input"]
        partes = (
            [render_aviso_interrupcion(interrumpidas, barridas)]
            if (interrumpidas or barridas)
            else []
        )
        partes.extend(_render_pregunta_reenviada(t) for t in preguntas)
        aviso = "\n\n".join(partes)

        async def marcar() -> None:
            await self._marcar_avisadas(tasks)

        error = await self._dispatch_al_scope(tasks[0], aviso, tras_dispatch=marcar)
        if error is not None:
            logger.error(
                "background-delegation: no se pudo avisar a '%s' (%s:%s) de %d delegación(es) "
                "pendiente(s) tras el reinicio (%s: %s); siguen sin avisar y se reintenta en el "
                "próximo arranque",
                tasks[0].caller_agent_id,
                tasks[0].channel,
                tasks[0].chat_id,
                len(tasks),
                type(error).__name__,
                error,
            )

    async def _marcar_avisadas(self, tasks: list[BackgroundTask]) -> None:
        """Marca ``notified`` DESPUÉS de un dispatch exitoso. Nunca lanza.

        Las ``interrupted`` avisadas dejan de ser asunto pendiente y salen del dict; las
        ``delivery_failed`` se quedan: siguen siendo recuperables con ``cancel``.
        """
        for t in tasks:
            t.notified = True
            if t.status == "interrupted":
                self._tasks.pop(t.id, None)
        if self._store is None:
            return
        try:
            await self._store.mark_notified([t.id for t in tasks])
        except Exception as exc:  # noqa: BLE001
            self._loguear_perdida(
                ",".join(t.id for t in tasks),
                f"no se pudo marcar como avisadas ({type(exc).__name__}: {exc}); el próximo "
                "arranque volverá a avisar de las mismas delegaciones",
            )

    def _marcar_interrumpidas_en_el_dict(self) -> None:
        """``queued``/``running`` → ``interrupted`` SOLO en la proyección en memoria.

        Es la única mutación de estado fuera de ``_transicionar``, a propósito: el store lo
        escribe ``_cerrar_huerfanas`` cuando ya no hay hijos vivos (ver ``stop``).
        """
        for task in self._tasks.values():
            if task.status in ("queued", "running"):
                task.status = "interrupted"

    async def _cerrar_huerfanas(self, motivo: str) -> None:
        """Único camino que cierra en el store lo que un corte dejó a medias.

        Lo usan ``stop`` (``motivo="stopped"``, reload o apagado limpio) y ``start``
        (``"found_at_startup"``: el proceso anterior murió sin cerrar). ``queued``/
        ``running`` → ``interrupted`` con ``notified=False`` (hay que avisarle al padre);
        ``delivering`` → ``delivery_failed`` CONSERVANDO el resultado ya persistido (no se
        re-dispatcha sola: duplicaría el turno del padre; se recupera con ``cancel``), también
        sin avisar. Cada fila está aislada: una que falla no frena a las demás.
        """
        if self._store is None:
            return
        try:
            huerfanas = await self._store.list_by_status(["queued", "running", "delivering"])
        except Exception as exc:  # noqa: BLE001
            self._loguear_perdida(
                "barrido",
                f"el barrido de {motivo} falló al listar las tasks ({type(exc).__name__}: "
                f"{exc}); las delegaciones cortadas por el reinicio pueden quedar como "
                "queued/running en el store y no se le avisará al padre",
            )
            return
        ahora = datetime.now(timezone.utc)
        for fila in huerfanas:
            if fila.id in self._sin_persistir:
                # Ya se logueó que su persistencia se rompió: reintentar acá repite el ERROR
                # sin ganar nada. Si quedó mal en el disco, el próximo arranque la barre.
                logger.debug("%s: persistencia rota; el barrido de %s la saltea", fila.id, motivo)
                continue
            try:
                if fila.status == "delivering":
                    await self._store.update(
                        fila.id, status="delivery_failed", error=_ENTREGA_CORTADA, notified=False
                    )
                else:
                    falla = await self._falla_de_interrupcion(fila, motivo, ahora)
                    await self._store.update(
                        fila.id,
                        status="interrupted",
                        failure=falla,
                        iterations_used=falla.iterations_used,
                        finished_at=ahora,
                        notified=False,
                    )
            except Exception as exc:  # noqa: BLE001
                self._loguear_perdida(
                    fila.id,
                    f"el barrido de {motivo} no pudo cerrarla ({type(exc).__name__}: {exc}); "
                    "queda como estaba en el store y no se le avisará al padre",
                )

    async def _falla_de_interrupcion(
        self, fila: BackgroundTask, motivo: str, ahora: datetime
    ) -> DelegationFailure:
        """Fallo ``interrupted`` de una task cortada, con lo último que hizo el hijo.

        El último paso y la tool en vuelo salen de los últimos mensajes persistidos (el
        transcript es incremental, así que sobrevive a un SIGKILL). Una task que nunca
        arrancó (``queued``) no tiene nada de eso.
        """
        detalle = _DETALLE_INTERRUPCION[motivo]
        if fila.run_started_at is None:
            return DelegationFailure(kind="interrupted", detail=detalle)
        pasos, _ = await self._store.messages(fila.id, last_n=_PASOS_PARA_EL_AVISO)  # type: ignore[union-attr]
        trace = [
            Message(
                role=p.role, content=p.content, tool_calls=p.tool_calls, tool_call_id=p.tool_call_id
            )
            for p in pasos
        ]
        sin_par = tool_calls_sin_par(trace)
        return DelegationFailure(
            kind="interrupted",
            detail=detalle,
            iterations_used=_iteraciones_usadas(trace),
            elapsed_seconds=int((ahora - fila.run_started_at).total_seconds()),
            last_step=ultimo_paso(trace),
            in_flight_tool=sin_par.en_vuelo,
            not_started_tools=sin_par.no_iniciadas,
        )

    async def _rehidratar(self) -> None:
        """Vuelve a poner en el dict lo que sigue siendo asunto pendiente del padre.

        ``delivery_failed`` (su resultado solo vive acá hasta que se descarte) e
        ``interrupted`` sin avisar: el padre las ve en ``snapshot_inflight`` y las
        resuelve con ``cancel``. ``awaiting_input`` (no terminal, sobrevive al reinicio) vuelve
        con su pregunta, recuperada del transcript. Las demás terminales se consultan con
        ``inspect``.
        """
        if self._store is None:
            return
        try:
            filas = await self._store.list_by_status(
                ["delivery_failed", "interrupted", "awaiting_input"]
            )
        except Exception as exc:  # noqa: BLE001
            self._loguear_perdida(
                "rehidratación",
                f"no se pudieron releer las tasks pendientes ({type(exc).__name__}: {exc}); "
                "el padre no las verá en su lista hasta el próximo arranque",
            )
            return
        for fila in filas:
            if fila.status == "interrupted" and fila.notified:
                continue
            if fila.status == "awaiting_input":
                fila.question = await self._recuperar_pregunta(fila)
            self._tasks[fila.id] = fila

    async def _recuperar_pregunta(self, fila: BackgroundTask) -> str | None:
        """La pregunta de una ``awaiting_input`` rehidratada, del último mensaje del transcript.

        No hay columna (sin migración): el último mensaje es el cierre ``needs_input`` del
        hijo, completo gracias al snapshot. Si no se puede recuperar la task se rehidrata igual
        (sigue siendo reanudable) pero sin pregunta que mostrar en list/inspect: ERROR.
        """
        assert self._store is not None
        try:
            pasos, _ = await self._store.messages(fila.id, last_n=1)
            cierre = parse_delegation_result(pasos[-1].content) if pasos else None
        except Exception as exc:  # noqa: BLE001 — una pregunta ilegible no tumba el arranque
            self._loguear_perdida(
                fila.id,
                f"no se pudo leer la pregunta pendiente del transcript ({type(exc).__name__}: "
                f"{exc}); la task sigue esperando respuesta pero list/inspect no la mostrarán",
            )
            return None
        pregunta = ((cierre.question if cierre else None) or "").strip()
        if cierre is None or cierre.status != "needs_input" or not pregunta:
            self._loguear_perdida(
                fila.id,
                "el último mensaje del transcript no trae la pregunta pendiente; la task sigue "
                "esperando respuesta pero list/inspect no la mostrarán",
            )
            return None
        return pregunta

    def _loguear_perdida(self, task_id: str, que_se_pierde: str) -> None:
        """ERROR que nombra la capacidad perdida (REQ-BGP-8, decisión R1: ERROR y no WARNING)."""
        logger.error("%s: %s: %s", _CAPACIDAD_PERDIDA, task_id, que_se_pierde)

    async def _transicionar(
        self, task: BackgroundTask, estado: BackgroundTaskStatus, **campos: Any
    ) -> None:
        """ÚNICO mutador de una task viva: dict (sync) → store (idempotente).

        La mutación del dict ocurre antes de cualquier ``await``: ``cancel`` ve el estado
        viejo o el nuevo, nunca uno intermedio. Los estados terminales sacan la task del
        dict (siguen en el store, accesibles por ``inspect``). Si la escritura falla, la
        task pasa a ``_sin_persistir`` y la delegación sigue (REQ-BGP-8).
        """
        task.status = estado
        for nombre, valor in campos.items():
            setattr(task, nombre, valor)
        if estado in _TERMINALES:
            self._tasks.pop(task.id, None)
        if estado in _TERMINALES or estado == "interrupted":
            # Una respuesta que no llegó a correr no puede sobrevivir a la task.
            self._reanudaciones.pop(task.id, None)
        await self._persistir(task, status=estado, **campos)

    async def _persistir(self, task: BackgroundTask, **campos: Any) -> None:
        if self._store is None or task.id in self._sin_persistir:
            return
        try:
            await self._store.update(task.id, **campos)
        except Exception as exc:  # noqa: BLE001 — degradar ruidosamente, no morir
            self._sin_persistir.add(task.id)
            self._loguear_perdida(
                task.id,
                f"dejó de persistirse ({type(exc).__name__}: {exc}); inspect no mostrará su "
                "estado más allá de este punto y un reinicio no la avisará",
            )

    async def _run_task(self, task: BackgroundTask) -> None:
        """Ejecuta una delegación bajo el semáforo y dispatcha el resultado.

        Red de seguridad (clase ``bg-stuck-task``): NINGUNA excepción inesperada puede dejar
        la task en ``running``/``delivering`` para siempre. El ciclo de vida corre dentro de
        un ``except Exception`` exterior que delega en ``_rescatar_task_atascada``, que la
        deja en un estado honesto y nunca relanza. ``CancelledError`` no es ``Exception``:
        ``cancel``/``stop`` siguen cortando limpio y el ``async with`` libera el slot.
        """
        async with self._semaphore:
            if task.id not in self._tasks:
                return  # cancelada mientras esperaba el semáforo
            try:
                await self._ciclo_de_vida(task)
            except Exception as exc:  # noqa: BLE001 — la task no puede quedar atascada
                await self._rescatar_task_atascada(task, exc)

    async def _rescatar_task_atascada(self, task: BackgroundTask, exc: Exception) -> None:
        """Saca de ``running``/``delivering`` a una task cuyo ciclo de vida lanzó. Nunca lanza.

        El dominio no tiene un estado ``failed``: un fallo del hijo es un RESULTADO que se
        entrega al padre (``render_fallo``). Se sigue la misma convención, porque cualquier
        otra salida mentiría (``interrupted`` es "cortada por un reinicio"):

        - Sin resultado todavía (``queued``/``running``): se persiste un reporte de fallo
          interno como resultado y se intenta entregarlo; el padre se entera igual que de
          cualquier otro fallo.
        - Con el resultado ya persistido (``delivering``) o si la entrega del reporte también
          revienta: ``delivery_failed`` CONSERVANDO el resultado y con el error, recuperable
          con ``cancel``. No se re-dispatcha: la excepción pudo venir del dispatch mismo.
        """
        tipo = f"{type(exc).__name__}: {exc}" if str(exc) else f"{type(exc).__name__} (no message)"
        logger.error(
            "background-delegation %s: excepción inesperada en el ciclo de vida de la task "
            "(estado %s, %s); se la saca de ese estado para que no quede atascada",
            task.id,
            task.status,
            tipo,
            exc_info=exc,
        )
        error = f"internal error: {tipo}"
        try:
            if task.status in ("queued", "running"):
                contenido, falla = _fallo_interno(task, tipo)
                await self._transicionar(
                    task,
                    "delivering",
                    result=contenido,
                    finished_at=datetime.now(timezone.utc),
                    failure=falla,
                )
                ultimo_error = await self._dispatch_result(task, contenido)
                if ultimo_error is None:
                    await self._transicionar(task, "delivered")
                    return
                error = f"{type(ultimo_error).__name__}: {ultimo_error}"
        except Exception as exc2:  # noqa: BLE001 — el rescate no puede relanzar
            logger.error(
                "background-delegation %s: falló también el rescate de la task (%s: %s)",
                task.id,
                type(exc2).__name__,
                exc2,
                exc_info=exc2,
            )
            error = f"{error}; rescue failed: {type(exc2).__name__}: {exc2}"
        if task.status in ("delivering", "running", "queued"):
            with suppress(Exception):
                await self._transicionar(task, "delivery_failed", error=error)

    async def _ciclo_de_vida(self, task: BackgroundTask) -> None:
        """Cuerpo de ``_run_task``: ``running`` → hijo → ``delivering`` → dispatch → cierre.

        Orden de purga (FIX silent-death): la task se elimina de ``_tasks``
        SOLO si el dispatch del resultado tuvo éxito. Antes se purgaba en un
        ``finally`` previo al dispatch, así que un dispatch fallido borraba la
        task del snapshot y el ``[bg-N]`` nunca llegaba — el agente padre
        quedaba esperando un resultado que jamás aparecía y no podía siquiera
        ver que la delegación seguía pendiente.

        Si tras los reintentos el dispatch no se entrega, la task queda viva en
        ``_tasks`` como ``delivery_failed``, con el resultado del hijo y el
        error guardados: el agente ve que falló la ENTREGA (no que el hijo siga
        trabajando) y ``cancel`` le devuelve el resultado.

        Una cancelación (``cancel``) llega como ``CancelledError`` en cualquier
        ``await`` de la fase del hijo; ``except Exception`` no la atrapa, así que
        se propaga limpia y el ``async with`` libera el slot del semáforo.

        Si el hijo cerró con ``needs_input`` el ciclo no entrega: ``_pausar`` deja la task en
        ``awaiting_input`` y devuelve (REQ-RES-1/4). Si la pausa no llegó a persistirse,
        ``_pausar`` devuelve el desenlace NO reanudable y el ciclo lo entrega por el camino común.
        """
        await self._transicionar(task, "running", run_started_at=datetime.now(timezone.utc))
        desenlace = await self._ejecutar_hijo(task)
        if desenlace.pausa:
            degradado = await self._pausar(task, desenlace)
            if degradado is None:
                return
            desenlace = degradado

        # Sin await entre el fin del hijo y este cambio: ``cancel`` ve
        # ``running`` o ``delivering``, nunca un estado intermedio. El resultado se
        # persiste en la MISMA transición, ANTES del dispatch (REQ-BGP-4).
        await self._transicionar(
            task,
            "delivering",
            result=desenlace.content,
            finished_at=datetime.now(timezone.utc),
            result_status=desenlace.result_status,
            result_parse_error=desenlace.parse_error,
            failure=desenlace.failure,
            iterations_used=desenlace.iterations_used,
        )
        ultimo_error = await self._dispatch_result(task, desenlace.content)
        if ultimo_error is None:
            await self._transicionar(task, "delivered")
            return
        await self._transicionar(
            task, "delivery_failed", error=f"{type(ultimo_error).__name__}: {ultimo_error}"
        )
        logger.error(
            "background-delegation %s: no se pudo entregar el resultado tras %d "
            "intentos (último error: %s); la task queda en delivery_failed con el "
            "resultado guardado hasta que se cancele",
            task.id,
            _DISPATCH_ATTEMPTS,
            task.error,
            exc_info=ultimo_error,
        )

    async def _pausar(self, task: BackgroundTask, desenlace: "_Desenlace") -> "_Desenlace | None":
        """``running`` → ``awaiting_input`` y aviso al padre de la pregunta (REQ-RES-2, REQ-RES-4).

        El snapshot ya está hecho (``_desenlace_de_pausa``): acá solo se persiste el estado y
        recién entonces se despacha, así que un resume del padre nunca lee antes del snapshot.
        ``awaiting_input`` NO es terminal: la task sigue en el dict y no se purga.

        La pregunta se fija sincrónicamente ANTES de transicionar: ``question`` no es columna
        del store, así que no puede ir en los ``campos`` de ``_transicionar``. ``finished_at``
        es la marca de la pausa: la retención poda ``awaiting_input`` por
        ``COALESCE(finished_at, created_at)``, o sea, 14 días desde acá (el resume lo limpia).

        Todo lo posterior a la transición está aislado: el estado ya NO es ``running`` y
        ``_rescatar_task_atascada`` lo pisaría. Un dispatch fallido deja la task en
        ``awaiting_input`` (no ``delivery_failed``: ``cancel`` la descartaría) con ``error`` y
        ``notified=False``; el próximo arranque re-despacha la pregunta.

        Si la transición a ``awaiting_input`` no se pudo escribir (la task pasó a
        ``_sin_persistir``), la fila sigue ``running`` en disco: un reinicio perdería la pausa y
        ``resume`` la rechazaría. Prometerle al padre un ``delegate(resume=...)`` sería mentirle,
        así que NO se despacha nada acá: se devuelve el desenlace no reanudable (la misma
        degradación que un snapshot fallido) y el ciclo lo entrega por el camino común.
        Devuelve ``None`` cuando la pausa quedó hecha.
        """
        task.question = desenlace.question
        pausada_en = datetime.now(timezone.utc)
        await self._transicionar(
            task,
            "awaiting_input",
            result=desenlace.content,
            result_status="needs_input",
            finished_at=pausada_en,
            iterations_used=desenlace.iterations_used,
            error=None,
            notified=False,
        )
        if task.id in self._sin_persistir and _sigue_en_la_pausa(task, pausada_en):
            self._loguear_perdida(
                task.id,
                "no se pudo persistir la pausa (awaiting_input): la task NO es reanudable (un "
                "reinicio la perdería); se entrega la pregunta como resultado final",
            )
            assert desenlace.content_no_reanudable is not None  # lo arma _desenlace_de_pausa
            task.question = None
            return _Desenlace(
                content=desenlace.content_no_reanudable,
                result_status="needs_input",
                iterations_used=desenlace.iterations_used,
            )
        try:
            ultimo_error = await self._dispatch_al_scope(
                task,
                desenlace.content,
                tras_dispatch=partial(self._marcar_pregunta_notificada, task, pausada_en),
            )
            if ultimo_error is None:
                return None
            if not _sigue_en_la_pausa(task, pausada_en):
                logger.warning(
                    "background-delegation %s: el aviso de la pregunta falló (%s: %s) pero la "
                    "task ya no está en esa pausa (%s); no se toca",
                    task.id,
                    type(ultimo_error).__name__,
                    ultimo_error,
                    task.status,
                )
                return None
            task.error = f"{type(ultimo_error).__name__}: {ultimo_error}"
            await self._persistir(task, error=task.error)
            logger.error(
                "background-delegation %s: no se pudo avisar al padre de la pregunta del hijo "
                "tras %d intentos (último error: %s); la task sigue en awaiting_input sin "
                "notificar y se reintenta en el próximo arranque",
                task.id,
                _DISPATCH_ATTEMPTS,
                task.error,
                exc_info=ultimo_error,
            )
        except Exception as exc:  # noqa: BLE001 — nunca al rescate: ya no está running
            logger.error(
                "background-delegation %s: excepción al avisar de la pregunta del hijo "
                "(%s: %s); la task sigue en %s y se reintenta en el próximo arranque",
                task.id,
                type(exc).__name__,
                exc,
                task.status,
                exc_info=exc,
            )
        return None

    async def _marcar_pregunta_notificada(self, task: BackgroundTask, pausada_en: datetime) -> None:
        """Hook de ``_dispatch_al_scope`` tras el dispatch de la pregunta. Nunca lanza.

        Marca ``notified`` SOLO si la task sigue en LA pausa que este tramo avisó: si el padre
        la reanudó o descartó dentro del propio dispatch, el estado que quedó no es de este
        tramo y no se pisa (REQ-RES-11, R1) —ni siquiera si el tramo 2 ya volvió a
        ``awaiting_input`` con otra pregunta—.
        """
        if not _sigue_en_la_pausa(task, pausada_en):
            return
        task.notified = True
        await self._persistir(task, notified=True)

    async def _ejecutar_hijo(self, task: BackgroundTask) -> "_Desenlace":
        """Corre al hijo y devuelve lo que se le entrega al padre (éxito o fallo honesto).

        Resolver = construir la instancia EFÍMERA del hijo contra el CALLER (hereda su
        config vía inherit). Por eso recibe ambos ids: el caller define la herencia, el
        target la definición del sub-agente.

        El hijo corre con el footer de formato JSON (mismo contrato que ``delegate`` sync)
        y con un recorder que anota su transcript. Su respuesta se parsea con
        ``render_resultado``; un fallo se clasifica y se reporta con ``render_fallo``
        (nunca ``str(exc)`` crudo). ``CancelledError`` no es ``Exception``: la
        cancelación (``cancel``/``stop``) se propaga limpia.
        """
        reanudacion = self._reanudaciones.pop(task.id, None)
        recorder = reanudacion.recorder if reanudacion is not None else self._recorder_de(task)
        try:
            return await self._correr_hijo(
                task,
                recorder,
                desde_seq=reanudacion.n_previos if reanudacion is not None else 0,
                respuesta=reanudacion.respuesta if reanudacion is not None else None,
            )
        finally:
            if getattr(recorder, "incompleto", False):
                await self._marcar_transcript_incompleto(task)

    async def _marcar_transcript_incompleto(self, task: BackgroundTask) -> None:
        """Deja constancia de que al transcript de ``task`` le faltan pasos.

        Se anota en memoria (siempre, por si el store ya no escribe) y en la fila, para que
        sobreviva a un reinicio: sin eso ``inspect`` mostraría tras un restart un transcript
        trunco como si fuera el completo.
        """
        self._transcripts_incompletos.add(task.id)
        task.transcript_incomplete = True
        await self._persistir(task, transcript_incomplete=True)

    async def _correr_hijo(
        self,
        task: BackgroundTask,
        recorder: IDelegationTranscript,
        *,
        desde_seq: int = 0,
        respuesta: str | None = None,
    ) -> "_Desenlace":
        """Cuerpo de ``_ejecutar_hijo``: separado para que el flag de transcript incompleto
        se anote también si el hijo se corta (``CancelledError``).

        ``desde_seq`` es cuántos mensajes tiene el transcript ANTES de este tramo (0 en el
        primero): el snapshot de una pausa solo reescribe las filas del tramo actual.

        Con ``respuesta`` es el TRAMO 2 de una reanudación: el hijo recibe el historial ya
        persistido (``recorder.trace[:desde_seq]``, una copia tomada justo antes de ejecutar)
        más la respuesta como siguiente turno del usuario. El presupuesto es el de la FILA
        (inmutable) menos lo ya gastado —el tool loop solo cuenta las iteraciones del tramo
        nuevo—, y el timeout se renueva por tramo.
        """
        inicio = time.monotonic()
        try:
            one_shot = self._one_shot_resolver(task.caller_agent_id, task.target_agent_id)
            if one_shot is None:
                falla = fallo_unknown_target(task.target_agent_id)
                logger.warning(
                    "background-delegation %s: '%s' no es un sub-agente conocido",
                    task.id,
                    task.target_agent_id,
                )
                return _Desenlace(content=render_fallo(task, falla), failure=falla)
            system_prompt = con_footer(
                task.system_prompt, por_defecto=one_shot.system_prompt, puede_pausar=True
            )
            if respuesta is None:
                raw = await one_shot.execute(
                    task=_tarea_del_hijo(task),
                    system_prompt=system_prompt,
                    max_iterations=task.max_iterations,
                    timeout_seconds=task.timeout_seconds,
                    recorder=recorder,
                )
            else:
                raw = await one_shot.execute(
                    task=respuesta,
                    system_prompt=system_prompt,
                    max_iterations=task.max_iterations - (task.iterations_used or 0),
                    timeout_seconds=task.timeout_seconds,
                    recorder=recorder,
                    historial_previo=recorder.trace[:desde_seq],
                )
        except Exception as exc:  # noqa: BLE001
            falla = clasificar_fallo(exc, recorder.trace, task, time.monotonic() - inicio)
            logger.warning(
                "background-delegation %s falló (%s): %s", task.id, falla.kind, falla.detail
            )
            return _Desenlace(
                content=render_fallo(task, falla),
                failure=falla,
                iterations_used=falla.iterations_used,
            )
        rendido = render_resultado(
            task.id,
            raw,
            puede_pausar=True,
            workdir=ruta_relativa(task.workdir) if task.workdir else None,
        )
        if rendido.question is not None:
            return await self._desenlace_de_pausa(task, recorder, rendido, desde_seq=desde_seq)
        return _Desenlace(
            content=_con_aviso_de_workdir(task, rendido.content),
            result_status=rendido.result_status,
            parse_error=rendido.parse_error,
            iterations_used=_iteraciones_usadas(recorder.trace),
        )

    async def _desenlace_de_pausa(
        self,
        task: BackgroundTask,
        recorder: IDelegationTranscript,
        rendido: ResultadoRendido,
        *,
        desde_seq: int,
    ) -> "_Desenlace":
        """Decide si el ``needs_input`` del hijo se puede pausar de verdad (REQ-RES-2/3).

        Pausar es prometerle al padre una continuación, así que solo se pausa si hay con qué
        cumplirla: transcript durable y entero (snapshot hecho) y al menos una iteración de
        presupuesto. Si no, la pregunta se entrega igual pero como ``delivered`` y con un
        mensaje que dice la verdad (``render_pregunta_no_reanudable``), nunca prometiendo un
        ``delegate(resume=...)`` que fallaría. Cada causa deja un ERROR que nombra la
        capacidad perdida.

        Cualquier excepción del snapshot —también el ``ValueError`` de un trace desalineado
        con el disco— es un snapshot fallido: la task NO es reanudable y queda marcada
        ``transcript_incomplete``. Se mira primero ``recorder.incompleto`` para que el log
        nombre la causa real y no el síntoma.
        """
        usadas = _iteraciones_usadas(recorder.trace)
        motivo = self._motivo_sin_pausa(task, recorder, usadas)
        if motivo is None:
            assert self._store is not None  # _motivo_sin_pausa lo garantiza
            try:
                await self._store.snapshot_completo(task.id, recorder.trace, desde_seq=desde_seq)
            except Exception as exc:  # noqa: BLE001 — un snapshot fallido = no reanudable
                motivo = "its transcript could not be saved in full"
                self._loguear_perdida(
                    task.id,
                    f"el snapshot del transcript falló al pausar el needs_input "
                    f"({type(exc).__name__}: {exc}); la task NO es reanudable y se entrega la "
                    "pregunta como resultado final",
                )
                await self._marcar_transcript_incompleto(task)
        if motivo is not None:
            content = _con_aviso_de_workdir(
                task, render_pregunta_no_reanudable(task.id, rendido, motivo)
            )
            return _Desenlace(content=content, result_status="needs_input", iterations_used=usadas)
        return _Desenlace(
            content=rendido.content,
            result_status="needs_input",
            iterations_used=usadas,
            question=rendido.question,
            pausa=True,
            content_no_reanudable=_con_aviso_de_workdir(
                task,
                render_pregunta_no_reanudable(
                    task.id, rendido, "its paused state could not be saved"
                ),
            ),
        )

    def _motivo_sin_pausa(
        self, task: BackgroundTask, recorder: IDelegationTranscript, usadas: int
    ) -> str | None:
        """Por qué NO se puede pausar (texto para el padre), o ``None`` si se puede.

        Los chequeos previos al snapshot, en orden de causa: sin disco, transcript cortado, sin
        presupuesto. Loguea la causa; el snapshot en sí lo hace el caller.
        """
        if self._store is None or task.id.startswith("bg-m") or task.id in self._sin_persistir:
            self._loguear_perdida(
                task.id,
                "needs_input sin pausa: la delegación no tiene transcript durable, no se puede "
                "reanudar; se entrega la pregunta como resultado final",
            )
            return "it has no durable transcript to resume from"
        if getattr(recorder, "incompleto", False):
            self._loguear_perdida(
                task.id,
                "needs_input sin pausa: el transcript quedó incompleto por un fallo del disco, "
                "no se puede reanudar; se entrega la pregunta como resultado final",
            )
            return "its transcript is incomplete"
        if task.max_iterations - usadas < 1:
            logger.warning(
                "background-delegation %s: needs_input sin pausa: el hijo usó %d de %d "
                "iteraciones y no queda presupuesto para continuar con la respuesta",
                task.id,
                usadas,
                task.max_iterations,
            )
            return "no iterations left in its budget"
        return None

    def _recorder_de(self, task: BackgroundTask) -> IDelegationTranscript:
        """Recorder SQLite de la task, o uno nulo si no hay dónde persistir (``bg-m*``,
        store caído, task degradada). El nulo igual lleva el ``trace`` en memoria, que es
        de donde sale el reporte honesto de un fallo: no depende del disco."""
        if self._store is not None and task.id not in self._sin_persistir:
            try:
                return self._store.transcript(task.id)
            except ValueError:  # id no durable (bg-m*)
                pass
        return NullDelegationTranscript()

    async def _dispatch_result(self, task: BackgroundTask, content: str) -> Exception | None:
        """Entrega el resultado de ``task`` a su scope original (ver ``_dispatch_al_scope``)."""
        return await self._dispatch_al_scope(task, content)

    async def _dispatch_al_scope(
        self,
        task: BackgroundTask,
        content: str,
        *,
        tras_dispatch: Callable[[], Awaitable[None]] | None = None,
    ) -> Exception | None:
        """Inyecta ``content`` en el scope de ``task`` con reintentos y entrega la
        respuesta del padre al canal (FIX bg-result-delivery).

        Sirve tanto al resultado de una delegación como al aviso de interrupción: lo único
        que cambia es ``content`` y ``tras_dispatch``, un hook que corre cuando el dispatch
        ya tuvo éxito (el turno del padre quedó persistido) y ANTES de entregar al canal.
        Ahí se marca ``notified``: la entrega al canal es best-effort y no puede decidir si
        el padre se enteró.

        Un ``AgentNotFoundError`` corta sin reintentos: el agente no existe y esperar no lo
        crea.

        Devuelve ``None`` si algún intento tuvo éxito, o la excepción del último
        intento si todos fallaron. El dispatch corre un turno completo del agente padre (llamada
        al LLM), así que la mayoría de los fallos son transitorios (red, timeout
        del provider) — un puñado de reintentos con backoff corto los cubre sin
        sobreingeniar.

        El ``skip_marker`` le permite al padre optar por silencio deliberado
        (``__SKIP__``): en ese caso ``execute()`` ya descartó la persistencia y
        acá no se envía nada. El ``live_sink`` propaga la narración intermedia
        del turno (texto junto a tool_calls) al canal en vivo, igual que hace el
        scheduler en ``agent_send``.
        """
        target = self._conversational_target(task)
        live_sink = (
            self._result_sender.build_intermediate_sink(target, agent_id=task.caller_agent_id)
            if self._result_sender is not None and target is not None
            else None
        )
        ultimo_error: Exception | None = None
        for intento in range(1, _DISPATCH_ATTEMPTS + 1):
            try:
                response = await self._dispatcher.dispatch(
                    agent_id=task.caller_agent_id,
                    prompt=content,
                    intermediate_sink=live_sink,
                    channel=task.channel,
                    chat_id=task.chat_id,
                    skip_marker=SKIP_MARKER,
                    # FIX bg-delivery-sin-routing: ni el ``[bg-N]`` ni el aviso de
                    # interrupción son intención del usuario. Rutearlos embebía el
                    # resultado ENTERO (60.388 chars en bg-1001) contra el proveedor de
                    # embeddings: 400 por exceso de contexto y la entrega moría.
                    skip_routing=True,
                )
            except AgentNotFoundError as dispatch_exc:
                logger.warning(
                    "background-delegation %s: el agente '%s' no existe; no se reintenta",
                    task.id,
                    task.caller_agent_id,
                )
                return dispatch_exc
            except Exception as dispatch_exc:  # noqa: BLE001
                ultimo_error = dispatch_exc
                logger.warning(
                    "background-delegation %s: dispatch intento %d/%d falló: %s: %s",
                    task.id,
                    intento,
                    _DISPATCH_ATTEMPTS,
                    type(dispatch_exc).__name__,
                    dispatch_exc,
                )
                if intento < _DISPATCH_ATTEMPTS:
                    await asyncio.sleep(_DISPATCH_RETRY_DELAY * intento)
                continue
            if tras_dispatch is not None:
                await tras_dispatch()
            await self._deliver_response(task, target, response)
            return None
        return ultimo_error

    def _conversational_target(self, task: BackgroundTask) -> str | None:
        """Target ``channel:chat_id`` si el scope original es un canal
        conversacional vivo; ``None`` si no (CLI/REST sin canal, tests)."""
        if (
            self._result_sender is not None
            and task.chat_id
            and self._result_sender.is_conversational(task.channel, task.caller_agent_id)
        ):
            return f"{task.channel}:{task.chat_id}"
        return None

    async def _deliver_response(
        self, task: BackgroundTask, target: str | None, response: str
    ) -> None:
        """Entrega la respuesta del padre al canal original, best-effort.

        Un fallo acá NO marca el dispatch como fallido: reintentar re-correría
        el turno completo del LLM y duplicaría el historial. La respuesta ya
        está persistida — se loguea el error y el usuario la tiene en el
        historial aunque no le haya llegado el mensaje.
        """
        if self._result_sender is None:
            return
        if target is None:
            # No es un error del envío sino de a dónde: el usuario no se entera de
            # nada. Así se vio el bug del scope ("", "") — sin una línea en el log.
            logger.warning(
                "background-delegation %s: el scope %r:%r no es un canal conversacional "
                "de '%s' — la respuesta queda solo en ese historial, nadie la recibe",
                task.id,
                task.channel,
                task.chat_id,
                task.caller_agent_id,
            )
            return
        if not response.strip() or is_skip_response(response):
            logger.info(
                "background-delegation %s: el agente optó por silencio — no se envía nada a %s",
                task.id,
                target,
            )
            return
        try:
            # record_history=False: el turno del dispatch ya persistió la respuesta.
            await self._result_sender.send_message(
                target, response, agent_id=task.caller_agent_id, record_history=False
            )
        except Exception as send_exc:  # noqa: BLE001
            logger.error(
                "background-delegation %s: la respuesta quedó en el historial pero "
                "no pudo entregarse a %s: %s",
                task.id,
                target,
                send_exc,
            )
