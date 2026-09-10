"""``delete``, ``enable`` y ``disable``: el ciclo de vida que el LLM sí controla.

``enable`` además re-arma tasks FAILED/MISSED (el use case resetea el runtime);
``disable`` pausa sin tocar el estado runtime; ``delete`` respeta la protección
de las tareas builtin.
"""

from __future__ import annotations

from typing import Any, ClassVar

from inaki.kernel.ports.tool_port import ToolResult
from inaki.scheduler.tools._context import error, ok
from inaki.scheduler.tools._params import parsear_entero
from inaki.scheduler.tools.operations._base import Operacion
from inaki.shared.errors import (
    BuiltinTaskProtectedError,
    SchedulerError,
    TaskNotFoundError,
)


class Borrar(Operacion):
    async def ejecutar(self, params: dict[str, Any]) -> ToolResult:
        task_id = parsear_entero(params, "task_id")
        if isinstance(task_id, ToolResult):
            return task_id
        try:
            await self._ctx.uc.delete_task(task_id)
        except (BuiltinTaskProtectedError, TaskNotFoundError, SchedulerError) as exc:
            return error(str(exc))
        return ok({"deleted": True, "task_id": task_id})


class _CambiarHabilitado(Operacion):
    habilitar: ClassVar[bool]

    async def ejecutar(self, params: dict[str, Any]) -> ToolResult:
        task_id = parsear_entero(params, "task_id")
        if isinstance(task_id, ToolResult):
            return task_id
        try:
            if self.habilitar:
                await self._ctx.uc.enable_task(task_id)
            else:
                await self._ctx.uc.disable_task(task_id)
            task = await self._ctx.uc.get_task(task_id)
        except (TaskNotFoundError, SchedulerError) as exc:
            return error(str(exc))
        return ok(
            {
                "task_id": task.id,
                "name": task.name,
                "enabled": task.enabled,
                "task_status": task.status.value,
                "next_run_at": task.next_run.isoformat() if task.next_run else None,
            }
        )


class Habilitar(_CambiarHabilitado):
    habilitar = True


class Deshabilitar(_CambiarHabilitado):
    habilitar = False
