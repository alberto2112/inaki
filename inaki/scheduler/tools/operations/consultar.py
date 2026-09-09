"""``list`` y ``get``: lectura de tareas (sin filtro de agente: el scheduler es global)."""

from __future__ import annotations

from typing import Any

from core.ports.outbound.tool_port import ToolResult
from inaki.scheduler.tools._context import error, ok
from inaki.scheduler.tools._params import TASK_KIND_TO_LLM, parsear_entero
from inaki.scheduler.tools.operations._base import Operacion
from inaki.shared.errors import SchedulerError, TaskNotFoundError


class Listar(Operacion):
    async def ejecutar(self, params: dict[str, Any]) -> ToolResult:
        tasks = await self._ctx.uc.list_tasks()
        items = [
            {
                "id": t.id,
                "name": t.name,
                "task_kind": TASK_KIND_TO_LLM.get(t.task_kind.value, t.task_kind.value),
                "status": t.status.value,
                "next_run_at": t.next_run.isoformat() if t.next_run else None,
                "trigger_type": t.trigger_type.value,
                "created_by": t.created_by,
            }
            for t in tasks
        ]
        return ok({"tasks": items, "total": len(items)})


class Obtener(Operacion):
    async def ejecutar(self, params: dict[str, Any]) -> ToolResult:
        task_id = parsear_entero(params, "task_id")
        if isinstance(task_id, ToolResult):
            return task_id
        try:
            task = await self._ctx.uc.get_task(task_id)
        except (TaskNotFoundError, SchedulerError) as exc:
            return error(str(exc))
        return ok(
            {
                "id": task.id,
                "name": task.name,
                "description": task.description,
                "task_kind": TASK_KIND_TO_LLM.get(task.task_kind.value, task.task_kind.value),
                "trigger_type": task.trigger_type.value,
                "trigger_payload": task.trigger_payload.model_dump(),
                "schedule": task.schedule,
                "status": task.status.value,
                "executions_remaining": task.executions_remaining,
                "created_by": task.created_by,
                "next_run_at": task.next_run.isoformat() if task.next_run else None,
                "last_run": task.last_run.isoformat() if task.last_run else None,
                "created_at": task.created_at.isoformat(),
            }
        )
