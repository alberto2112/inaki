"""BackgroundTask y BackgroundTaskView — entidades del feature background-delegation.

BackgroundTask representa una delegación encolada en el
BackgroundDelegationQueueAdapter. Es mutable: el consumer del adapter transiciona
``status`` en sitio (``queued`` → ``running`` → ``delivering`` → ``delivered``).
Las tasks VIVAS se proyectan en el dict del adapter; el estado durable (incluidas
las terminales, que se consultan con ``inspect``) vive en el store. Si la entrega
falla, queda en ``delivery_failed`` con el resultado y el error guardados hasta
que el agente (o el operador) la cancela.

BackgroundTaskView es el DTO read-only que expone ``snapshot_inflight(...)``.
Encapsula las reglas de:

- Truncado del prompt a ≤80 caracteres con elipsis Unicode ``"…"``.
- Cálculo de ``elapsed_seconds`` desde ``started_at`` hasta el momento del
  snapshot (int, truncado).

La factory ``BackgroundTaskView.from_task(task, now=...)`` es la única forma
documentada de construir un View — concentra esas reglas en un solo lugar para
que los callers (adapter + tests) no las repliquen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from inaki.shared.message import Role

_PROMPT_PREVIEW_MAX = 80
_QUESTION_PREVIEW_MAX = 120
_ELLIPSIS = "…"

# Ciclo de vida de una delegación async:
# - ``queued``: esperando un slot del semáforo.
# - ``running``: el hijo está trabajando.
# - ``delivering``: el hijo terminó y su resultado se le está entregando al
#   padre (corre un turno del padre). No se puede cancelar: cortaría ese turno
#   a mitad de camino.
# - ``delivery_failed``: el hijo terminó pero la entrega falló en todos los
#   reintentos (o el daemon murió entregándola). El resultado y el error quedan
#   guardados en la task; NUNCA se presenta como ``running`` (caso real: un bg-N
#   "corriendo" 6 horas después de que su hijo había terminado).
# - ``awaiting_input``: el hijo PAUSÓ con una pregunta (``needs_input``) y espera la
#   respuesta. NO es terminal: sobrevive a restart, ``/reload`` y ``stop()``, y se
#   reanuda sobre la misma fila con ``delegate(resume=...)`` (o se abandona con ``cancel``).
LiveBackgroundTaskStatus = Literal[
    "queued", "running", "delivering", "delivery_failed", "awaiting_input"
]

# Estados en los que la task ya no avanza sola:
# - ``delivered``: el resultado llegó al padre.
# - ``cancelled``: el padre (u operador) la cortó en cola o corriendo.
# - ``dismissed``: se descartó una ``delivery_failed`` recuperando su resultado.
# - ``interrupted``: un restart, ``/reload`` o ``stop()`` cortó al hijo. Sigue
#   pendiente de AVISO hasta que ``notified`` pase a verdadero o el padre la
#   descarte con ``cancel``.
FinishedBackgroundTaskStatus = Literal["delivered", "cancelled", "dismissed", "interrupted"]

# Los nueve estados persistidos (REQ-BGP-4). Se escriben completos y no como unión
# de los dos alias de arriba: ``Literal`` no se compone desde otros ``Literal``.
BackgroundTaskStatus = Literal[
    "queued",
    "running",
    "delivering",
    "delivery_failed",
    "delivered",
    "cancelled",
    "dismissed",
    "interrupted",
    "awaiting_input",
]

# Lo que puede verse en el prompt del padre (``BackgroundTaskView``): las vivas más
# ``interrupted`` mientras esperan su aviso. Las terminales NO viajan al prompt: se
# consultan con ``inspect`` (REQ-BGD-4 / REQ-BGD-7).
InFlightBackgroundTaskStatus = Literal[
    "queued", "running", "delivering", "delivery_failed", "interrupted", "awaiting_input"
]

# Desenlace de ``IBackgroundDelegationQueue.cancel``:
# - ``cancelled``: estaba ``queued``/``running`` y se cortó.
# - ``dismissed``: estaba ``delivery_failed``, ``interrupted`` sin avisar o
#   ``awaiting_input``; se descartó devolviendo lo que tuviera guardado (en
#   ``awaiting_input``, la pregunta que quedó sin respuesta).
# - ``finished``: ya había terminado (``delivered``/``cancelled``/``dismissed``/
#   ``interrupted`` ya avisada); no hay nada que cortar. ``CancelOutcome.status``
#   dice en qué estado quedó.
# - ``not_found``: no hay una task con ese id de ese caller (ya entregó, ya se
#   canceló, se podó, o es de otro agente). No afirma que nunca existió.
# - ``delivering``: su resultado se está entregando justo ahora; no se corta.
CancelOutcomeKind = Literal["cancelled", "dismissed", "finished", "not_found", "delivering"]

# Desenlace de ``IBackgroundDelegationQueue.resume`` (REQ-RES-7). Ninguno distinto de
# ``resumed`` cambia el ``status`` de la fila (la única mutación permitida es la marca
# ``transcript_incomplete`` cuando la reapertura detecta un transcript inconsistente):
# - ``resumed``: la task volvió a ``queued`` con la respuesta; el tramo corre solo.
# - ``not_found``: no hay una task con ese id de ese caller (podada, inexistente o de
#   otro agente: indistinguibles a propósito). No afirma que nunca existió.
# - ``not_awaiting``: la task existe pero no está esperando una respuesta (``status``
#   dice en cuál está).
# - ``already_resuming``: otro ``resume`` de la misma task ya reservó el turno.
# - ``transcript_incomplete``: el historial persistido no permite reanudar sin mentir.
# - ``no_iterations_left``: el presupuesto acumulado de la fila ya se agotó.
# - ``target_mismatch``: el ``agent_id`` que dio el caller no es el de la fila.
# - ``target_not_allowed``: el target de la fila ya no está en la allow-list del caller.
# - ``unknown_target``: el target de la fila ya no se resuelve (la config cambió).
# - ``unavailable``: el store o el consumer no están disponibles; reintentable.
ResumeOutcomeKind = Literal[
    "resumed",
    "not_found",
    "not_awaiting",
    "already_resuming",
    "transcript_incomplete",
    "no_iterations_left",
    "target_mismatch",
    "target_not_allowed",
    "unknown_target",
    "unavailable",
]

# Por qué falló una delegación (REQ-BGP-5). ``unknown_target`` e ``interrupted`` no
# son fallos del hijo: el primero nunca arrancó, el segundo lo cortó el daemon.
DelegationFailureKind = Literal[
    "timeout", "max_iterations", "exception", "unknown_target", "interrupted"
]


class InFlightToolCall(BaseModel):
    """Tool call que el hijo despachó y de la que no consta el resultado.

    Su efecto es DESCONOCIDO: pudo haberse ejecutado entera, a medias o nada. Por
    eso el reporte nunca la presenta como "no se ejecutó" (REQ-BGP-6).
    """

    tool_call_id: str
    name: str
    arguments_preview: str  # ≤200 chars


class DelegationFailure(BaseModel):
    """Fallo clasificado de una delegación (REQ-BGP-5).

    ``detail`` NUNCA está vacío: ``TimeoutError("")`` y ``RuntimeError("")`` son
    habituales y un reporte con el texto de error en blanco fue justo el bug que
    originó este cambio.
    """

    kind: DelegationFailureKind
    detail: str
    iterations_used: int = 0
    elapsed_seconds: int = 0
    last_step: str = ""
    in_flight_tool: InFlightToolCall | None = None
    # Tools del mismo batch que NO llegaron a despacharse (sí se sabe que no
    # corrieron: el loop es secuencial).
    not_started_tools: list[str] = []


class BackgroundTask(BaseModel):
    """Estado de una delegación async.

    Vive en el dict interno del BackgroundDelegationQueueAdapter mientras la
    delegación está viva (en cola, corriendo, entregándose o con la entrega
    fallida) y en el store durable durante toda su vida. Los campos nuevos de
    bg-delegation-transcript tienen todos default: construir una task como antes
    sigue siendo válido.
    """

    id: str
    caller_agent_id: str
    target_agent_id: str
    prompt: str
    system_prompt: str | None
    channel: str
    chat_id: str
    max_iterations: int
    timeout_seconds: int
    started_at: datetime
    status: BackgroundTaskStatus
    # Solo en ``delivery_failed``: el mensaje ``[bg-N] ...`` que no se pudo
    # entregar (el resultado del hijo) y el error del último intento.
    result: str | None = None
    error: str | None = None
    # Timeline y desenlace (bg-delegation-transcript).
    run_started_at: datetime | None = None
    finished_at: datetime | None = None
    iterations_used: int | None = None
    # ``status`` del bloque JSON que devolvió el hijo (``success``/``failed``).
    result_status: str | None = None
    # El hijo terminó pero sin el bloque JSON de resultado exigido por el footer.
    result_parse_error: bool = False
    failure: DelegationFailure | None = None
    # ¿El padre ya se enteró de este desenlace? Una task nueva no necesita aviso;
    # el barrido de arranque marca ``False`` a las interrumpidas.
    notified: bool = True
    # Directorio de trabajo propio (ruta ABSOLUTA) cuando el que delega tiene
    # ``delegation.grant_workdir``. Se fija al encolar y es inmutable; vive y muere con la
    # fila. ``None`` = sin workdir (flag apagado, ``wait=true`` o no se pudo crear).
    workdir: str | None = None
    # Basenames de los insumos copiados al workdir antes de encolar (``input_files``).
    input_files: list[str] = []
    # El transcript durable quedó cortado: una escritura falló mientras corría el hijo. Se
    # persiste (sobrevive a un reinicio) para que ``inspect`` nunca presente un transcript
    # trunco como si fuera el completo.
    transcript_incomplete: bool = False
    # Solo en ``awaiting_input``: la pregunta con la que pausó el hijo. Vive en MEMORIA y no
    # es columna del store (sin migración): se fija al pausar y se recupera en el arranque
    # parseando el último mensaje del transcript.
    question: str | None = None


class BackgroundTaskView(BaseModel):
    """DTO read-only para `IBackgroundDelegationQueue.snapshot_inflight`.

    El campo ``prompt_preview`` está garantizado ≤80 chars cuando se construye
    via ``from_task`` (que aplica el truncado con elipsis).
    """

    id: str
    target_agent_id: str
    prompt_preview: str
    elapsed_seconds: int
    status: InFlightBackgroundTaskStatus
    # Error del último intento de entrega (solo en ``delivery_failed``). El
    # resultado en sí NO viaja en el View: puede ser largo y el View se inyecta
    # en el system prompt de cada turno; se recupera con ``cancel``.
    error: str | None = None
    # Solo en ``awaiting_input``: la pregunta del hijo, truncada a ≤120 con elipsis (el
    # View se inyecta en el system prompt de cada turno). La completa se ve con ``inspect``.
    question_preview: str | None = None
    # Solo en ``awaiting_input``: ¿se puede reanudar? ``False`` si el transcript quedó
    # incompleto (solo queda ``cancel``). ``None`` en cualquier otro estado.
    resumable: bool | None = None

    @classmethod
    def from_task(cls, task: BackgroundTask, *, now: datetime) -> "BackgroundTaskView":
        """Construye un View truncando el prompt y calculando elapsed.

        Args:
            task: La task in-flight a representar.
            now: Timestamp del snapshot — el caller lo provee para mantener
                la función pura (sin ``datetime.now()`` interno).

        Returns:
            View con ``prompt_preview`` ≤80 chars y ``elapsed_seconds``
            truncado a int.
        """
        prompt = task.prompt
        if len(prompt) > _PROMPT_PREVIEW_MAX:
            preview = prompt[: _PROMPT_PREVIEW_MAX - 1] + _ELLIPSIS
        else:
            preview = prompt

        elapsed = int((now - task.started_at).total_seconds())

        en_pausa = task.status == "awaiting_input"
        question_preview: str | None = None
        if en_pausa and task.question is not None:
            question_preview = task.question
            if len(question_preview) > _QUESTION_PREVIEW_MAX:
                question_preview = question_preview[: _QUESTION_PREVIEW_MAX - 1] + _ELLIPSIS

        return cls(
            id=task.id,
            target_agent_id=task.target_agent_id,
            prompt_preview=preview,
            elapsed_seconds=elapsed,
            # Una task terminal (delivered/cancelled/dismissed) no entra al prompt del
            # padre: la validación de pydantic la rechaza, no hay filtro aparte que olvidar.
            status=task.status,  # type: ignore[arg-type]
            error=task.error,
            question_preview=question_preview,
            resumable=(not task.transcript_incomplete) if en_pausa else None,
        )


class CancelOutcome(BaseModel):
    """Resultado de ``IBackgroundDelegationQueue.cancel``.

    ``result`` y ``error`` solo vienen con ``dismissed``: son lo que la task en
    ``delivery_failed`` (o ``awaiting_input``, donde ``result`` es la pregunta sin
    contestar) tenía guardado, para que quien la descarta no pierda el trabajo del hijo. ``status`` solo viene con ``finished``: el estado terminal en
    el que la task ya estaba.
    """

    outcome: CancelOutcomeKind
    task_id: str
    result: str | None = None
    error: str | None = None
    status: BackgroundTaskStatus | None = None


class ResumeOutcome(BaseModel):
    """Resultado de ``IBackgroundDelegationQueue.resume`` (REQ-RES-7).

    ``status`` solo viene con ``not_awaiting`` (el estado en que la task sí está);
    ``target_agent_id`` con ``resumed`` y ``target_mismatch`` (el de la fila);
    ``iterations_left`` solo con ``resumed``; ``detail`` es una nota libre para el
    mensaje al LLM (p. ej. por qué no se puede reanudar).
    """

    outcome: ResumeOutcomeKind
    task_id: str
    status: BackgroundTaskStatus | None = None
    target_agent_id: str | None = None
    iterations_left: int | None = None
    detail: str | None = None


class TranscriptEntry(BaseModel):
    """Un mensaje del hijo tal como quedó persistido (REQ-BGP-2).

    Conserva el pareo crudo ``tool_calls`` / ``tool_call_id``: un ``assistant``
    con tool calls sin su ``tool`` posterior ES la evidencia de una tool en vuelo.
    ``original_chars`` solo viene si el contenido se recortó al cap (REQ-BGP-3).
    """

    seq: int
    role: Role
    content: str
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    original_chars: int | None = None
    created_at: datetime


class WorkdirEntry(BaseModel):
    """Una entrada del listado superficial de un workdir (REQ-BGW-9)."""

    name: str
    kind: Literal["file", "dir", "other"]
    # Solo los ficheros regulares tienen tamaño.
    size_bytes: int | None = None


class WorkdirState(BaseModel):
    """Estado de un workdir al momento de ``inspect`` (REQ-BGW-9).

    ``entries`` es una lista superficial y acotada; ``entries_total`` es cuántas había.
    Si el directorio ya no existe, ``exists=False`` y ``note`` lo dice: NUNCA se finge
    contenido.
    """

    path: str
    relative_path: str
    exists: bool
    entries: list[WorkdirEntry]
    entries_total: int
    note: str | None = None


class TaskInspection(BaseModel):
    """Lo que ``IBackgroundDelegationQueue.inspect`` devuelve de una task.

    ``persisted=False`` marca una task sin transcript durable (store caído o id
    ``bg-m*``): ``messages`` queda vacío y quien lo muestra debe decirlo.
    ``transcript_incomplete=True`` marca un transcript que SÍ existe pero al que le faltan
    pasos (una escritura falló a mitad): quien lo muestra debe decirlo también.
    """

    task: BackgroundTask
    messages: list[TranscriptEntry]
    messages_total: int
    persisted: bool
    workdir: WorkdirState | None = None
    transcript_incomplete: bool = False


class EnqueuedTask(BaseModel):
    """Lo que devuelve ``IBackgroundDelegationQueue.enqueue`` (antes, solo el id).

    ``workdir`` es la ruta absoluta del directorio de trabajo creado, si lo hubo.
    ``workdir_unavailable`` distingue "el caller tiene workdir pero no se pudo crear"
    (la delegación se encoló igual y el resultado ``queued`` lo dice) de "el caller no
    tiene workdir": nunca se finge un directorio que no existe.
    """

    task_id: str
    workdir: str | None = None
    workdir_unavailable: bool = False
    input_files: list[str] = []


# Motivos estables de rechazo de ``input_files`` (REQ-BGW-11..13). El tool los expone tal
# cual como ``reason`` del ``DelegationResult`` fallido.
InputFilesRejection = Literal[
    "input_files_require_background",
    "input_files_require_workdir",
    "input_file_outside_workspace",
    "input_file_not_found",
    "input_file_not_a_file",
    "input_file_name_collision",
    "input_files_too_many",
    "input_files_too_large",
    "input_files_workdir_unavailable",
    "input_file_copy_failed",
]

# Mensajes dirigidos al LLM (en inglés). ``{path}`` es la ruta ofensora (o el nombre en
# colisiones); los motivos con cifras (too_many / too_large) los arma quien los lanza.
_MENSAJES_RECHAZO: dict[str, str] = {
    "input_files_require_background": "input_files requires background mode (wait=false).",
    "input_files_require_workdir": (
        "input_files requires a working directory, and this agent is not granted one "
        "(delegation.grant_workdir)."
    ),
    "input_file_outside_workspace": "Input file is outside your workspace: {path}",
    "input_file_not_found": "Input file not found: {path}",
    "input_file_not_a_file": "Input file is not a regular file: {path}",
    "input_file_name_collision": "Two input files share the name '{path}'.",
    "input_files_too_many": "Too many input files.",
    "input_files_too_large": "Input files are too large.",
    "input_files_workdir_unavailable": (
        "Could not create the working directory for the input files."
    ),
    "input_file_copy_failed": "Could not copy input file: {path}",
}


class InputFilesRejected(Exception):
    """``enqueue`` rechazó los ``input_files``: NO se creó dir ni se encoló nada.

    ``reason`` es el código estable; ``path`` la ruta (o nombre) ofensora, si aplica. El
    mensaje va en inglés porque lo lee el LLM; si quien lanza tiene cifras que mostrar
    (cantidad, MiB) las pasa en ``message``.
    """

    def __init__(
        self,
        reason: InputFilesRejection,
        path: str | None = None,
        *,
        message: str | None = None,
    ) -> None:
        plantilla = _MENSAJES_RECHAZO.get(reason)
        if plantilla is None:
            raise ValueError(f"InputFilesRejected: reason desconocido {reason!r}")
        self.reason: InputFilesRejection = reason
        self.path = path
        super().__init__(message or plantilla.format(path=path if path is not None else "?"))
