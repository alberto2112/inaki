"""ManualRunResult — resultado de una ejecución manual on-demand de una tarea.

Lo produce ``SchedulerService.run_task_now`` cuando el usuario dispara una tarea
fuera de su agenda (``inaki scheduler run <id>``). A diferencia de una corrida
programada, NO altera ``status`` / ``next_run`` / ``executions_remaining``: es un
disparo de prueba NO destructivo. ``success`` distingue "el trigger se ejecutó OK"
de "el trigger falló"; la tarea inexistente NO se modela acá (eso es un error de
cliente, ``TaskNotFoundError``).
"""

from __future__ import annotations

from pydantic import BaseModel


class ManualRunResult(BaseModel, frozen=True):
    """Resultado inmutable de un disparo manual de una tarea.

    Atributos:
        task_id: ID de la tarea disparada.
        success: True si el trigger se ejecutó sin excepción.
        output: Salida del trigger (None para triggers que no producen texto,
            p. ej. ``channel_send``).
        error: Mensaje de error si ``success`` es False; None en caso contrario.
    """

    task_id: int
    success: bool
    output: str | None = None
    error: str | None = None
