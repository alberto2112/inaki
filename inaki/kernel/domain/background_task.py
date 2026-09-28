"""BackgroundTask y BackgroundTaskView — entidades del feature background-delegation.

BackgroundTask representa una delegación encolada en el
BackgroundDelegationQueueAdapter. Es mutable: el consumer del adapter transiciona
``status`` en sitio (``queued`` → ``running`` → ``delivering``). Tras un
dispatch exitoso, la task se purga del dict del adapter (REQ-BGD-4); si la
entrega falla, queda en ``delivery_failed`` con el resultado y el error
guardados hasta que el agente (o el operador) la cancela.

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

_PROMPT_PREVIEW_MAX = 80
_ELLIPSIS = "…"

# Ciclo de vida de una delegación async:
# - ``queued``: esperando un slot del semáforo.
# - ``running``: el hijo está trabajando.
# - ``delivering``: el hijo terminó y su resultado se le está entregando al
#   padre (corre un turno del padre). No se puede cancelar: cortaría ese turno
#   a mitad de camino.
# - ``delivery_failed``: el hijo terminó pero la entrega falló en todos los
#   reintentos. El resultado y el error quedan guardados en la task; NUNCA se
#   presenta como ``running`` (caso real: un bg-N "corriendo" 6 horas después de
#   que su hijo había terminado).
BackgroundTaskStatus = Literal["queued", "running", "delivering", "delivery_failed"]

# Desenlace de ``IBackgroundDelegationQueue.cancel``:
# - ``cancelled``: estaba ``queued``/``running`` y se cortó.
# - ``dismissed``: estaba ``delivery_failed``; se descartó devolviendo su resultado.
# - ``not_found``: no hay una task con ese id de ese caller EN MEMORIA (ya
#   entregó, ya se canceló, o el daemon reinició). No afirma que nunca existió.
# - ``delivering``: su resultado se está entregando justo ahora; no se corta.
CancelOutcomeKind = Literal["cancelled", "dismissed", "not_found", "delivering"]


class BackgroundTask(BaseModel):
    """Estado in-memory de una delegación async.

    Vive en el dict interno del BackgroundDelegationQueueAdapter mientras la
    delegación está en cola, corriendo o entregándose, y también si la entrega
    falló. Se elimina al dispatchar el resultado con éxito o al cancelarla.
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


class BackgroundTaskView(BaseModel):
    """DTO read-only para `IBackgroundDelegationQueue.snapshot_inflight`.

    El campo ``prompt_preview`` está garantizado ≤80 chars cuando se construye
    via ``from_task`` (que aplica el truncado con elipsis).
    """

    id: str
    target_agent_id: str
    prompt_preview: str
    elapsed_seconds: int
    status: BackgroundTaskStatus
    # Error del último intento de entrega (solo en ``delivery_failed``). El
    # resultado en sí NO viaja en el View: puede ser largo y el View se inyecta
    # en el system prompt de cada turno; se recupera con ``cancel``.
    error: str | None = None

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

        return cls(
            id=task.id,
            target_agent_id=task.target_agent_id,
            prompt_preview=preview,
            elapsed_seconds=elapsed,
            status=task.status,
            error=task.error,
        )


class CancelOutcome(BaseModel):
    """Resultado de ``IBackgroundDelegationQueue.cancel``.

    ``result`` y ``error`` solo vienen con ``dismissed``: son lo que la task en
    ``delivery_failed`` tenía guardado, para que quien la descarta no pierda el
    trabajo del hijo.
    """

    outcome: CancelOutcomeKind
    task_id: str
    result: str | None = None
    error: str | None = None
