"""``run``: dispara una tarea AHORA, fuera de su agenda — NO destructivo."""

from __future__ import annotations

from typing import Any

from core.ports.outbound.tool_port import ToolResult
from inaki.scheduler.tools._context import error, ok
from inaki.scheduler.tools._params import parsear_entero
from inaki.scheduler.tools.operations._base import Operacion
from inaki.shared.errors import SchedulerError, TaskNotFoundError


class Correr(Operacion):
    """Delega en ``IManualTaskRunner.run_task_now`` (el motor de ejecución).

    Corre el trigger UNA vez sin la máquina de reintentos y sin tocar
    ``status``/``next_run``/``executions_remaining``: un recurrente conserva su
    agenda y NO consume una ejecución; un oneshot NO pasa a COMPLETED. Sin
    protección de builtins: correr no muta la tarea.

    El fallo del trigger (un shell que sale con código != 0) NO es un error de
    la tool: se devuelve como dato (``trigger_success=false`` + ``error``) para
    que el LLM decida — mismo criterio que ``log_get`` con "no encontrado". Solo
    es error de la tool que la tarea no exista o que el ``task_id`` sea inválido.
    """

    async def ejecutar(self, params: dict[str, Any]) -> ToolResult:
        task_id = parsear_entero(params, "task_id")
        if isinstance(task_id, ToolResult):
            return task_id
        try:
            result = await self._ctx.runner.run_task_now(task_id)
        except (TaskNotFoundError, SchedulerError) as exc:
            return error(str(exc))
        return ok(
            {
                "ran": True,
                "task_id": result.task_id,
                "trigger_success": result.success,
                "output": result.output,
                "error": result.error,
            }
        )
