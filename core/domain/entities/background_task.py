"""BackgroundTask y BackgroundTaskView — entidades del feature background-delegation.

BackgroundTask representa una delegación encolada en el
BackgroundDelegationQueueAdapter. Es mutable: el consumer del adapter transiciona
``status`` de ``"queued"`` a ``"running"`` en sitio. Tras dispatch, la task se
purga del dict del adapter (REQ-BGD-4).

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


class BackgroundTask(BaseModel):
    """Estado in-memory de una delegación async.

    Vive en el dict interno del BackgroundDelegationQueueAdapter mientras la
    delegación está en cola o corriendo. Se elimina al dispatchar el resultado.
    """

    id: str
    caller_agent_id: str
    target_agent_id: str
    prompt: str
    system_prompt: str | None
    channel: str
    chat_id: str
    started_at: datetime
    status: Literal["queued", "running"]


class BackgroundTaskView(BaseModel):
    """DTO read-only para `IBackgroundDelegationQueue.snapshot_inflight`.

    El campo ``prompt_preview`` está garantizado ≤80 chars cuando se construye
    via ``from_task`` (que aplica el truncado con elipsis).
    """

    id: str
    target_agent_id: str
    prompt_preview: str
    elapsed_seconds: int
    status: Literal["queued", "running"]

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
        )
