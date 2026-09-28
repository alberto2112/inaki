"""BackgroundTasksTool — lista y cancela las delegaciones en background del agente.

Tercera superficie de la capacidad de la cola (``IBackgroundDelegationQueue``):
el LLM la usa como tool, y el operador por el gateway admin
(``POST /admin/tool/invoke`` / ``inaki tool background_tasks``) — misma lógica,
sin pasarelas por canal.

Nace de un caso real: un ``bg-N`` cuya entrega falló quedó "corriendo" 6 horas
y el agente no tenía forma de cerrarlo. Con ``cancel`` el agente corta una
delegación que ya no sirve, y descarta una ``delivery_failed`` recuperando el
resultado que el hijo había producido.

Cada instancia está ligada a SU agente (``caller_agent_id``): solo ve y corta
las delegaciones que ese agente lanzó.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from inaki.kernel.ports.tool_port import ITool, ToolResult

if TYPE_CHECKING:
    from inaki.kernel.ports.background_delegation_port import IBackgroundDelegationQueue


class BackgroundTasksTool(ITool):
    """Consulta y cancelación de las delegaciones async del agente."""

    name = "background_tasks"
    description = (
        "List or cancel your background delegations (the bg-N tasks launched with "
        "delegate wait=false). action=list returns each task with its status. "
        "action=cancel with a task_id stops a queued or running task, or dismisses a "
        "delivery_failed task and returns the result it had produced."
    )
    routing_keywords = (
        "cancelar tarea en segundo plano, cancelar delegación, tarea colgada, "
        "cerrar bg, estado de la delegación, tareas en background; "
        "cancel background task, stop delegation, stuck task, delegation status; "
        "annuler la tâche en arrière-plan, annuler la délégation, tâche bloquée"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "cancel"],
                "description": "list: show your background tasks. cancel: stop or dismiss one.",
            },
            "task_id": {
                "type": "string",
                "description": "The bg-N id of the task to cancel. Required for action=cancel.",
            },
        },
        "required": ["action"],
    }

    def __init__(self, *, queue: "IBackgroundDelegationQueue", caller_agent_id: str) -> None:
        self._queue = queue
        self._caller_agent_id = caller_agent_id

    async def execute(  # type: ignore[override]
        self, action: str, task_id: str | None = None, **kwargs
    ) -> ToolResult:
        if action == "list":
            return self._list()
        if action == "cancel":
            if not task_id:
                return self._error("task_id is required for action=cancel.")
            return self._cancel(task_id)
        return self._error(f"Unknown action '{action}'. Valid actions: list, cancel.")

    def _list(self) -> ToolResult:
        tasks = self._queue.snapshot_inflight(self._caller_agent_id)
        payload = {
            "tasks": [t.model_dump(exclude_none=True) for t in tasks],
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if not tasks:
            payload["note"] = (
                "No background tasks in memory for you. Finished tasks already delivered "
                "their [bg-N] result; tasks are also lost if the daemon restarted."
            )
        return self._ok(payload)

    def _cancel(self, task_id: str) -> ToolResult:
        res = self._queue.cancel(task_id, self._caller_agent_id)
        if res.outcome == "cancelled":
            return self._ok(
                {
                    "outcome": "cancelled",
                    "task_id": task_id,
                    "message": "The task was stopped. No [bg-N] result will arrive for it.",
                }
            )
        if res.outcome == "dismissed":
            return self._ok(
                {
                    "outcome": "dismissed",
                    "task_id": task_id,
                    "message": (
                        "The child agent had finished, but its result could not be delivered "
                        "to you. It is included here — this is the only copy."
                    ),
                    "delivery_error": res.error,
                    "result": res.result,
                }
            )
        if res.outcome == "delivering":
            return self._error(
                f"{task_id} has finished and its result is being delivered right now; "
                "it cannot be cancelled. Its [bg-N] message is about to arrive."
            )
        # not_found: la cola no la TIENE — no afirmar que nunca existió.
        return self._error(
            f"{task_id} is not among your background tasks in memory. It may have already "
            "delivered its [bg-N] result, been cancelled, or been lost in a daemon restart "
            "(tasks are in-memory). Use action=list to see what is pending."
        )

    def _ok(self, payload: dict) -> ToolResult:
        return ToolResult(
            tool_name=self.name, output=json.dumps(payload, ensure_ascii=False), success=True
        )

    def _error(self, message: str) -> ToolResult:
        return ToolResult(tool_name=self.name, output=message, success=False, error=message)
