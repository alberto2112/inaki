"""``logs`` y ``log_get``: el historial de ejecución, con y sin truncación."""

from __future__ import annotations

from typing import Any

from inaki.kernel.ports.tool_port import ToolResult
from inaki.scheduler.tools._context import error, ok
from inaki.scheduler.tools._params import parsear_entero
from inaki.scheduler.tools.operations._base import Operacion
from inaki.shared.errors import SchedulerError

# Truncación del output/error en `logs` — protege el contexto del LLM cuando el
# historial arrastra outputs grandes. `log_get` NO trunca: es la puerta de "dame
# el detalle completo de este log".
LOG_OUTPUT_TRUNCATION = 1000

# Cap duro sobre `limit` en `logs`. Vive en la tool, no en el repo: es un límite
# de presentación (contexto del LLM), no de persistencia.
MAX_LOGS_LIMIT = 50


class ListarLogs(Operacion):
    """Sin ``task_id`` lista los últimos ``limit`` logs de todas las tareas."""

    async def ejecutar(self, params: dict[str, Any]) -> ToolResult:
        task_id: int | None = None
        if params.get("task_id") is not None:
            parsed = parsear_entero(params, "task_id")
            if isinstance(parsed, ToolResult):
                return parsed
            task_id = parsed
        limit_raw = params.get("limit", 10)
        try:
            limit = int(limit_raw) if limit_raw is not None else 10
        except (TypeError, ValueError):
            return error(f"Invalid 'limit': '{limit_raw}'. Must be an integer.")
        limit = max(1, min(limit, MAX_LOGS_LIMIT))
        offset_raw = params.get("offset", 0)
        try:
            offset = int(offset_raw) if offset_raw is not None else 0
        except (TypeError, ValueError):
            return error(f"Invalid 'offset': '{offset_raw}'. Must be an integer.")
        offset = max(0, offset)
        status_filter = params.get("status_filter")
        if status_filter is not None:
            status_filter = str(status_filter).strip().lower() or None

        try:
            logs = await self._ctx.uc.list_logs(task_id, limit, offset, status_filter)
        except SchedulerError as exc:
            return error(str(exc))
        entries: list[dict[str, Any]] = []
        for log in logs:
            output_full = log.output or ""
            error_full = log.error or ""
            entries.append(
                {
                    "log_id": log.id,
                    "task_id": log.task_id,
                    "started_at": log.started_at.isoformat(),
                    "finished_at": log.finished_at.isoformat() if log.finished_at else None,
                    "status": log.status,
                    "attempt_output": output_full[:LOG_OUTPUT_TRUNCATION]
                    if log.output is not None
                    else None,
                    "attempt_error": error_full[:LOG_OUTPUT_TRUNCATION]
                    if log.error is not None
                    else None,
                    "output_truncated": len(output_full) > LOG_OUTPUT_TRUNCATION,
                    "error_truncated": len(error_full) > LOG_OUTPUT_TRUNCATION,
                }
            )
        return ok({"task_id": task_id, "total_returned": len(entries), "logs": entries})


class ObtenerLog(Operacion):
    """Un log completo. "No encontrado" es dato (``found: false``), no error."""

    async def ejecutar(self, params: dict[str, Any]) -> ToolResult:
        log_id = parsear_entero(params, "log_id")
        if isinstance(log_id, ToolResult):
            return log_id
        try:
            log = await self._ctx.uc.get_log(log_id)
        except SchedulerError as exc:
            return error(str(exc))
        if log is None:
            return ok({"found": False, "log_id": log_id})
        return ok({"found": True, "log": log.model_dump(mode="json")})
