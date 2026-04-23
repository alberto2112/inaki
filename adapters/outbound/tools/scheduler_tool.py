"""SchedulerTool — expone el scheduler al LLM como una tool multi-operación.

Operations:
  - create   : crea una nueva tarea programada
  - list     : lista todas las tareas (sin filtro de agente)
  - get      : obtiene una tarea por ID (detalle completo con trigger_payload)
  - update   : modifica campos mutables de una tarea existente
  - delete   : elimina una tarea (builtin tasks protegidas)
  - logs     : lista logs de ejecución de una tarea (con paginación y truncación)
  - log_get  : obtiene un log por ID con output/error completos (sin truncación)

REQs satisfechos: REQ-ST-1, REQ-ST-2, REQ-ST-3, REQ-ST-4, REQ-ST-5, REQ-ST-6,
                  REQ-ST-8, REQ-ST-10
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, cast

from pydantic import BaseModel

from core.domain.entities.task import (
    AgentSendPayload,
    ChannelSendPayload,
    ScheduledTask,
    ShellExecPayload,
    TaskKind,
    TaskStatus,
    TriggerPayload,
    TriggerType,
)
from core.domain.errors import (
    BuiltinTaskProtectedError,
    SchedulerError,
    TaskNotFoundError,
    TooManyActiveTasksError,
)
from core.domain.utils.time_parser import parse_schedule
from core.domain.value_objects.channel_context import ChannelContext
from core.ports.outbound.tool_port import ITool, ToolResult

if TYPE_CHECKING:
    from core.ports.inbound.scheduler_port import ISchedulerUseCase

logger = logging.getLogger(__name__)

# Trigger types exposed to the LLM (consolidate_memory is system-only)
_ALLOWED_TRIGGER_TYPES = {"channel_send", "agent_send", "shell_exec"}

_TRIGGER_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "channel_send": ChannelSendPayload,
    "agent_send": AgentSendPayload,
    "shell_exec": ShellExecPayload,
}

_VALID_OPERATIONS = ("create", "list", "get", "update", "delete", "logs", "log_get")


def _coerce_to_dict(value: Any) -> Any:
    """Intenta parsear `value` como dict si el LLM lo envió como JSON string."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return value

# Truncación del output/error en la operación `logs` — protege el contexto
# del LLM cuando el historial arrastra outputs grandes. `log_get` NO trunca:
# es la puerta de "dame el detalle completo de este log".
_LOG_OUTPUT_TRUNCATION = 1000

# Cap duro sobre `limit` en `logs` — evita que el LLM pida 1000 entradas y
# sature el contexto. Vive en el tool, no en el repo, porque es un límite
# de presentación (LLM-context), no de persistencia.
_MAX_LOGS_LIMIT = 50

# Map domain TaskKind values to LLM-friendly names and back
_TASK_KIND_TO_LLM = {
    "oneshot": "one_shot",
    "recurrent": "recurring",
}
_LLM_TO_TASK_KIND = {v: k for k, v in _TASK_KIND_TO_LLM.items()}

# Fields the LLM is allowed to update on an existing task
_MUTABLE_FIELDS = frozenset(
    {"name", "description", "schedule", "trigger_payload", "executions_remaining", "status"}
)


class SchedulerTool(ITool):
    """
    Tool que expone el scheduler al LLM.

    El schedule acepta dos formatos:
      - Relativo: "+2h", "+1d30m" → convertido internamente a datetime UTC absoluto
      - ISO 8601: "2026-04-12T14:00:00-03:00" → pasado directamente al use case
      - Para tareas recurrentes el schedule es una expresión cron (no admite "+")

    El campo created_by se inyecta desde agent_id en construcción —
    nunca es aceptado desde los kwargs del LLM.
    """

    name = "scheduler"
    description = (
        "Manage scheduled tasks. Operations: create, list, get, update, delete, logs, log_get. "
        "Use 'create' to schedule a future action (one_shot or recurring). "
        "Use 'list' to see all active tasks. "
        "Use 'get' to retrieve full detail (including trigger_payload) for a specific task. "
        "Use 'update' to modify mutable fields on a task. "
        "Use 'delete' to remove a non-builtin task permanently. "
        "Use 'logs' to list execution logs of a task (newest first, paginated, outputs truncated to 1000 chars). "
        "Use 'log_get' to fetch a single log by id with the FULL untruncated output/error. "
        "Builtin tasks (id < 100) cannot be modified or deleted."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(_VALID_OPERATIONS),
                "description": "Operation to perform.",
            },
            # --- create / update fields ---
            "name": {
                "type": "string",
                "description": "Human-readable name for the task (required for create).",
            },
            "description": {
                "type": "string",
                "description": "Optional description of the task purpose.",
            },
            "task_kind": {
                "type": "string",
                "enum": ["one_shot", "recurring"],
                "description": (
                    "Task type. 'one_shot' runs once at a specific time; "
                    "'recurring' runs on a cron schedule."
                ),
            },
            "trigger_type": {
                "type": "string",
                "enum": sorted(_ALLOWED_TRIGGER_TYPES),
                "description": "Kind of action to execute when the task fires.",
            },
            "trigger_payload": {
                "type": "object",
                "description": (
                    "Action-specific payload. "
                    'For \'channel_send\': {"text": "...", "user_id": "...(opcional)"}. '
                    "El canal de destino se inyecta automáticamente del contexto de conversación — "
                    "NO incluir 'channel_id' ni 'target'. "
                    'For \'agent_send\': {"agent_id": "...", "task": "..."}. '
                    "agent_id acepta 'self' (o omitirlo) para referirse al agente actual; "
                    "usá un id explícito solo si querés delegar a otro agente. "
                    'For \'shell_exec\': {"command": "...", "working_dir": null, '
                    '"env_vars": {}, "timeout": null}.'
                ),
            },
            "schedule": {
                "type": "string",
                "description": (
                    "When to run the task. Two formats supported: "
                    "(1) Relative offset: '+Xd', '+Xh', '+Xm', or combinations like '+2d3h30m' — "
                    "converted to an absolute UTC datetime from now. "
                    "(2) ISO 8601 absolute datetime: '2026-04-12T14:00:00-03:00' or "
                    "'2026-04-12T14:00:00Z'. "
                    "For recurring tasks, use a cron expression (e.g. '0 8 * * *') — "
                    "relative offsets (+) are NOT valid for recurring tasks."
                ),
            },
            "executions_remaining": {
                "type": "integer",
                "description": (
                    "For recurring tasks: number of executions before auto-disable. "
                    "Null means infinite."
                ),
            },
            "status": {
                "type": "string",
                "enum": ["pending", "running", "completed", "failed", "missed"],
                "description": "Task status (update only).",
            },
            # --- get / update / delete / logs ---
            "task_id": {
                "type": "integer",
                "description": "Task ID (required for get, update, delete, logs).",
            },
            # --- log_get ---
            "log_id": {
                "type": "integer",
                "description": "Log entry ID (required for log_get).",
            },
            # --- logs ---
            "limit": {
                "type": "integer",
                "description": (
                    f"Máximo de entradas a devolver en 'logs'. Default 10, cap duro {_MAX_LOGS_LIMIT}."
                ),
            },
            "offset": {
                "type": "integer",
                "description": "Desplazamiento de paginación para 'logs'. Default 0.",
            },
            "status_filter": {
                "type": "string",
                "enum": ["success", "failed", "missed"],
                "description": ("Filtra 'logs' por status. Omitir para ver todos."),
            },
        },
        "required": ["operation"],
    }

    def __init__(
        self,
        *,
        schedule_task_uc: ISchedulerUseCase,
        agent_id: str,
        user_timezone: str,
        get_channel_context: Callable[[], ChannelContext | None],
    ) -> None:
        self._uc = schedule_task_uc
        self._agent_id = agent_id
        self._user_timezone = user_timezone
        self._get_channel_context = get_channel_context

    async def execute(self, **kwargs: Any) -> ToolResult:  # type: ignore[override]
        operation = str(kwargs.get("operation") or "").strip().lower()
        try:
            if operation == "create":
                return await self._create(kwargs)
            if operation == "list":
                return await self._list()
            if operation == "get":
                return await self._get(kwargs)
            if operation == "update":
                return await self._update(kwargs)
            if operation == "delete":
                return await self._delete(kwargs)
            if operation == "logs":
                return await self._logs(kwargs)
            if operation == "log_get":
                return await self._log_get(kwargs)
            return self._error(
                f"Unknown operation '{operation}'. "
                f"Valid operations: {', '.join(_VALID_OPERATIONS)}."
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("SchedulerTool unexpected error (operation=%s)", operation)
            return self._error(f"Internal error: {exc}")

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    async def _create(self, params: dict[str, Any]) -> ToolResult:
        # --- Required fields ---
        name = str(params.get("name") or "").strip()
        if not name:
            return self._error("Missing required parameter 'name'.")

        task_kind_raw = str(params.get("task_kind") or "").strip().lower()
        if task_kind_raw not in _LLM_TO_TASK_KIND:
            return self._error(
                f"Invalid 'task_kind': '{task_kind_raw}'. Must be 'one_shot' or 'recurring'."
            )

        trigger_type_raw = str(params.get("trigger_type") or "").strip().lower()
        if trigger_type_raw not in _ALLOWED_TRIGGER_TYPES:
            return self._error(
                f"Invalid 'trigger_type': '{trigger_type_raw}'. "
                f"Must be one of: {', '.join(sorted(_ALLOWED_TRIGGER_TYPES))}."
            )

        schedule_raw = str(params.get("schedule") or "").strip()
        if not schedule_raw:
            return self._error("Missing required parameter 'schedule'.")

        trigger_payload_raw_original = params.get("trigger_payload")
        trigger_payload_raw = _coerce_to_dict(trigger_payload_raw_original)
        if not isinstance(trigger_payload_raw, dict):
            logger.error(
                "scheduler.create: trigger_payload inválido — type=%s, repr=%r, keys_recibidas=%s",
                type(trigger_payload_raw_original).__name__,
                trigger_payload_raw_original,
                list(params.keys()),
            )
            return self._error("Missing or invalid 'trigger_payload'. Must be an object.")

        # --- Validate recurring + relative guard ---
        if task_kind_raw == "recurring" and schedule_raw.startswith("+"):
            return self._error(
                "Recurring tasks require a cron expression, not a relative time offset."
            )

        # --- Parse schedule ---
        parsed_schedule = schedule_raw
        is_recurring = task_kind_raw == "recurring"
        if not is_recurring:
            # Only parse for one_shot tasks; cron expressions are passed as-is
            if schedule_raw.startswith("+"):
                try:
                    dt = parse_schedule(schedule_raw, self._user_timezone)
                    parsed_schedule = dt.isoformat()
                except ValueError as exc:
                    return self._error(
                        f"Invalid relative schedule '{schedule_raw}'. "
                        f"Use format: +Xd, +Xh, +Xm or combinations (e.g. +2d3h30m). "
                        f"Detail: {exc}"
                    )
            else:
                # ISO 8601 — parsear y normalizar a UTC (naive → user_timezone → UTC)
                try:
                    dt = parse_schedule(schedule_raw, self._user_timezone)
                    parsed_schedule = dt.isoformat()
                except ValueError as exc:
                    return self._error(str(exc))

        # --- Inyección de contexto de canal para channel_send ---
        if trigger_type_raw == "channel_send":
            context = self._get_channel_context()
            if context is None:
                return self._error(
                    "No hay contexto de canal disponible. "
                    "channel_send solo funciona en conversaciones interactivas."
                )
            # Descartar silenciosamente 'target' que el LLM pueda haber enviado
            trigger_payload_raw.pop("target", None)
            # Determinar target: si LLM envió user_id, reconstruir con channel_type del contexto
            llm_user_id = trigger_payload_raw.pop("user_id", None)
            if llm_user_id is not None:
                trigger_payload_raw["target"] = f"{context.channel_type}:{llm_user_id}"
                trigger_payload_raw["user_id"] = llm_user_id
            else:
                trigger_payload_raw["target"] = context.routing_key

        # --- Resolución de 'self' para agent_send ---
        # Si el LLM no especifica agent_id o usa el alias 'self', apuntamos al
        # agente que está creando la tarea. Delegar a otro agente requiere un
        # id explícito distinto.
        # Si output_channel no fue especificado, heredar el canal activo para que
        # el resultado se difunda donde se creó la tarea.
        if trigger_type_raw == "agent_send":
            raw_agent_id = trigger_payload_raw.get("agent_id")
            if raw_agent_id is None or str(raw_agent_id).strip().lower() == "self":
                trigger_payload_raw["agent_id"] = self._agent_id
            if trigger_payload_raw.get("output_channel") is None:
                ctx = self._get_channel_context()
                if ctx is not None:
                    trigger_payload_raw["output_channel"] = ctx.routing_key

        # --- Validate trigger payload ---
        payload_model_cls = _TRIGGER_PAYLOAD_MODELS[trigger_type_raw]
        try:
            trigger_payload_raw["type"] = trigger_type_raw
            trigger_payload_obj = cast(
                TriggerPayload, payload_model_cls.model_validate(trigger_payload_raw)
            )
        except Exception as exc:  # noqa: BLE001
            return self._error(f"Invalid trigger_payload for '{trigger_type_raw}': {exc}")

        # --- Map LLM-friendly name to domain enum ---
        task_kind = TaskKind(_LLM_TO_TASK_KIND[task_kind_raw])
        trigger_type = TriggerType(trigger_type_raw)

        # --- Build entity ---
        # next_run lo resuelve el repo desde schedule (ver SQLiteSchedulerRepo._resolve_next_run).
        # El contrato es: ONESHOT → schedule es ISO 8601; RECURRENT → schedule es cron.
        task = ScheduledTask(
            name=name,
            description=str(params.get("description") or ""),
            task_kind=task_kind,
            trigger_type=trigger_type,
            trigger_payload=trigger_payload_obj,
            schedule=parsed_schedule,
            executions_remaining=params.get("executions_remaining"),
            created_by=self._agent_id,  # always injected — never from LLM kwargs
        )

        # --- Call use case ---
        try:
            created = await self._uc.create_task(task)
        except TooManyActiveTasksError as exc:
            return self._error(str(exc))
        except SchedulerError as exc:
            return self._error(str(exc))

        return ToolResult(
            tool_name=self.name,
            output=json.dumps(self._echo_task(created, op="created")),
            success=True,
        )

    async def _list(self) -> ToolResult:
        try:
            tasks = await self._uc.list_tasks()
        except Exception as exc:  # noqa: BLE001
            return self._error(f"Internal error: {exc}")

        task_items = [
            {
                "id": t.id,
                "name": t.name,
                "task_kind": _TASK_KIND_TO_LLM.get(t.task_kind.value, t.task_kind.value),
                "status": t.status.value,
                "next_run_at": t.next_run.isoformat() if t.next_run else None,
                "trigger_type": t.trigger_type.value,
                "created_by": t.created_by,
            }
            for t in tasks
        ]

        return ToolResult(
            tool_name=self.name,
            output=json.dumps({"tasks": task_items, "total": len(task_items)}),
            success=True,
        )

    async def _get(self, params: dict[str, Any]) -> ToolResult:
        task_id = params.get("task_id")
        if task_id is None:
            return self._error("Missing required parameter 'task_id'.")
        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            return self._error(f"Invalid 'task_id': '{task_id}'. Must be an integer.")

        try:
            task = await self._uc.get_task(task_id)
        except TaskNotFoundError as exc:
            return self._error(str(exc))
        except SchedulerError as exc:
            return self._error(str(exc))

        payload_dict = task.trigger_payload.model_dump()

        return ToolResult(
            tool_name=self.name,
            output=json.dumps(
                {
                    "id": task.id,
                    "name": task.name,
                    "description": task.description,
                    "task_kind": _TASK_KIND_TO_LLM.get(task.task_kind.value, task.task_kind.value),
                    "trigger_type": task.trigger_type.value,
                    "trigger_payload": payload_dict,
                    "schedule": task.schedule,
                    "status": task.status.value,
                    "executions_remaining": task.executions_remaining,
                    "created_by": task.created_by,
                    "next_run_at": task.next_run.isoformat() if task.next_run else None,
                    "last_run": task.last_run.isoformat() if task.last_run else None,
                    "created_at": task.created_at.isoformat(),
                }
            ),
            success=True,
        )

    async def _update(self, params: dict[str, Any]) -> ToolResult:
        task_id = params.get("task_id")
        if task_id is None:
            return self._error("Missing required parameter 'task_id'.")
        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            return self._error(f"Invalid 'task_id': '{task_id}'. Must be an integer.")

        # Collect mutable fields — silently drop immutable ones
        updates: dict[str, Any] = {}

        if "name" in params:
            updates["name"] = str(params["name"])

        if "description" in params:
            updates["description"] = str(params["description"])

        if "executions_remaining" in params:
            updates["executions_remaining"] = params["executions_remaining"]

        if "status" in params:
            status_raw = str(params["status"]).strip().lower()
            try:
                updates["status"] = TaskStatus(status_raw)
            except ValueError:
                return self._error(
                    f"Invalid 'status': '{status_raw}'. "
                    f"Must be one of: {', '.join(s.value for s in TaskStatus)}."
                )

        if "schedule" in params:
            schedule_raw = str(params["schedule"]).strip()
            if schedule_raw.startswith("+"):
                try:
                    dt = parse_schedule(schedule_raw, self._user_timezone)
                    updates["schedule"] = dt.isoformat()
                except ValueError as exc:
                    return self._error(
                        f"Invalid relative schedule '{schedule_raw}'. "
                        f"Use format: +Xd, +Xh, +Xm or combinations. "
                        f"Detail: {exc}"
                    )
            else:
                try:
                    dt = parse_schedule(schedule_raw, self._user_timezone)
                    updates["schedule"] = dt.isoformat()
                except ValueError as exc:
                    return self._error(str(exc))

        if "trigger_payload" in params:
            payload_raw_original = params["trigger_payload"]
            payload_raw = _coerce_to_dict(payload_raw_original)
            if not isinstance(payload_raw, dict):
                logger.error(
                    "scheduler.update: trigger_payload inválido — type=%s, repr=%r",
                    type(payload_raw_original).__name__,
                    payload_raw_original,
                )
                return self._error("'trigger_payload' must be an object.")
            # Need trigger_type to validate — fetch existing task first
            # (handled below when we call update_task)
            updates["_trigger_payload_raw"] = payload_raw

        if not {k for k in updates if not k.startswith("_")}:
            if "_trigger_payload_raw" not in updates:
                return self._error(
                    "No mutable fields provided. "
                    f"Mutable fields: {', '.join(sorted(_MUTABLE_FIELDS))}."
                )

        # If trigger_payload update requested, resolve it now
        if "_trigger_payload_raw" in updates:
            payload_raw = updates.pop("_trigger_payload_raw")
            # Get current task to know the trigger_type
            try:
                existing = await self._uc.get_task(task_id)
            except TaskNotFoundError as exc:
                return self._error(str(exc))
            except SchedulerError as exc:
                return self._error(str(exc))

            trigger_type_str = existing.trigger_type.value
            if trigger_type_str not in _ALLOWED_TRIGGER_TYPES:
                return self._error(
                    f"Cannot update trigger_payload for system trigger type '{trigger_type_str}'."
                )
            # Inyección de contexto de canal para channel_send (misma lógica que _create)
            if trigger_type_str == "channel_send":
                context = self._get_channel_context()
                if context is None:
                    return self._error(
                        "No hay contexto de canal disponible. "
                        "channel_send solo funciona en conversaciones interactivas."
                    )
                payload_raw.pop("target", None)
                llm_user_id = payload_raw.pop("user_id", None)
                if llm_user_id is not None:
                    payload_raw["target"] = f"{context.channel_type}:{llm_user_id}"
                    payload_raw["user_id"] = llm_user_id
                else:
                    payload_raw["target"] = existing.trigger_payload.target

            # Resolución de 'self' para agent_send (misma lógica que _create)
            if trigger_type_str == "agent_send":
                raw_agent_id = payload_raw.get("agent_id")
                if raw_agent_id is None or str(raw_agent_id).strip().lower() == "self":
                    payload_raw["agent_id"] = self._agent_id

            payload_model_cls = _TRIGGER_PAYLOAD_MODELS[trigger_type_str]
            try:
                payload_raw["type"] = trigger_type_str
                updates["trigger_payload"] = cast(
                    TriggerPayload, payload_model_cls.model_validate(payload_raw)
                )
            except Exception as exc:  # noqa: BLE001
                return self._error(f"Invalid trigger_payload for '{trigger_type_str}': {exc}")

        try:
            updated = await self._uc.update_task(task_id, **updates)
        except BuiltinTaskProtectedError as exc:
            return self._error(str(exc))
        except TaskNotFoundError as exc:
            return self._error(str(exc))
        except SchedulerError as exc:
            return self._error(str(exc))

        return ToolResult(
            tool_name=self.name,
            output=json.dumps(self._echo_task(updated, op="updated")),
            success=True,
        )

    async def _delete(self, params: dict[str, Any]) -> ToolResult:
        task_id = params.get("task_id")
        if task_id is None:
            return self._error("Missing required parameter 'task_id'.")
        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            return self._error(f"Invalid 'task_id': '{task_id}'. Must be an integer.")

        try:
            await self._uc.delete_task(task_id)
        except BuiltinTaskProtectedError as exc:
            return self._error(str(exc))
        except TaskNotFoundError as exc:
            return self._error(str(exc))
        except SchedulerError as exc:
            return self._error(str(exc))

        return ToolResult(
            tool_name=self.name,
            output=json.dumps({"deleted": True, "task_id": task_id}),
            success=True,
        )

    async def _logs(self, params: dict[str, Any]) -> ToolResult:
        """
        Lista logs de ejecución de una tarea, con truncación por entrada.

        - Cap `limit` a `_MAX_LOGS_LIMIT` (50) — protege el contexto del LLM.
        - Trunca `output`/`error` a `_LOG_OUTPUT_TRUNCATION` (1000) por entrada
          y setea flags explícitas (`output_truncated` / `error_truncated`).
          Cuando el LLM necesita el detalle completo, llama `log_get`.
        """
        task_id = params.get("task_id")
        if task_id is None:
            return self._error("Missing required parameter 'task_id'.")
        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            return self._error(f"Invalid 'task_id': '{task_id}'. Must be an integer.")

        limit_raw = params.get("limit", 10)
        try:
            limit = int(limit_raw) if limit_raw is not None else 10
        except (TypeError, ValueError):
            return self._error(f"Invalid 'limit': '{limit_raw}'. Must be an integer.")
        if limit > _MAX_LOGS_LIMIT:
            limit = _MAX_LOGS_LIMIT
        if limit < 1:
            limit = 1

        offset_raw = params.get("offset", 0)
        try:
            offset = int(offset_raw) if offset_raw is not None else 0
        except (TypeError, ValueError):
            return self._error(f"Invalid 'offset': '{offset_raw}'. Must be an integer.")
        if offset < 0:
            offset = 0

        status_filter = params.get("status_filter")
        if status_filter is not None:
            status_filter = str(status_filter).strip().lower()
            if status_filter == "":
                status_filter = None

        try:
            logs = await self._uc.list_logs(task_id, limit, offset, status_filter)
        except SchedulerError as exc:
            return self._error(str(exc))

        entries: list[dict[str, Any]] = []
        for log in logs:
            output_full = log.output or ""
            error_full = log.error or ""
            output_truncated = len(output_full) > _LOG_OUTPUT_TRUNCATION
            error_truncated = len(error_full) > _LOG_OUTPUT_TRUNCATION
            entries.append(
                {
                    "log_id": log.id,
                    "started_at": log.started_at.isoformat(),
                    "finished_at": log.finished_at.isoformat() if log.finished_at else None,
                    "status": log.status,
                    "attempt_output": output_full[:_LOG_OUTPUT_TRUNCATION]
                    if log.output is not None
                    else None,
                    "attempt_error": error_full[:_LOG_OUTPUT_TRUNCATION]
                    if log.error is not None
                    else None,
                    "output_truncated": output_truncated,
                    "error_truncated": error_truncated,
                }
            )

        return ToolResult(
            tool_name=self.name,
            output=json.dumps(
                {
                    "task_id": task_id,
                    "total_returned": len(entries),
                    "logs": entries,
                }
            ),
            success=True,
        )

    async def _log_get(self, params: dict[str, Any]) -> ToolResult:
        """
        Devuelve un log por id con output/error completos (sin truncación).

        No lanza excepción si no existe — mapea a `{"found": false, "log_id": N}`
        para que el LLM trate "no encontrado" como dato, no como error.
        """
        log_id_raw = params.get("log_id")
        if log_id_raw is None:
            return self._error("Missing required parameter 'log_id'.")
        try:
            log_id = int(log_id_raw)
        except (TypeError, ValueError):
            return self._error(f"Invalid 'log_id': '{log_id_raw}'. Must be an integer.")

        try:
            log = await self._uc.get_log(log_id)
        except SchedulerError as exc:
            return self._error(str(exc))

        if log is None:
            return ToolResult(
                tool_name=self.name,
                output=json.dumps({"found": False, "log_id": log_id}),
                success=True,
            )

        return ToolResult(
            tool_name=self.name,
            output=json.dumps({"found": True, "log": log.model_dump(mode="json")}),
            success=True,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _echo_task(self, task: ScheduledTask, *, op: str) -> dict[str, Any]:
        """
        Echo autoconfirmable de una task tras create/update.

        Devuelve un objeto con:
          - flag booleano explícito (`created=True` o `updated=True`) análogo al
            `deleted=True` de `_delete`;
          - campos autoritativos persistidos (schedule, next_run_at, task_status).

        El LLM necesita estos campos para saber sin ambigüedad que la operación
        tomó y con qué valores finales — en particular `next_run_at` (recomputado
        por el repo vía `_resolve_next_run`), `task_status` (estado runtime, que
        puede haber sido reseteado a pending en edits invalidantes; ver
        `ScheduleTaskUseCase.update_task`) y `enabled` (la intención declarada
        del usuario; `enabled=False` significa que NO va a correr aunque el
        status diga pending).
        """
        return {
            f"{op}": True,
            "id": task.id,
            "name": task.name,
            "task_kind": _TASK_KIND_TO_LLM.get(task.task_kind.value, task.task_kind.value),
            "trigger_type": task.trigger_type.value,
            "schedule": task.schedule,
            "next_run_at": task.next_run.isoformat() if task.next_run else None,
            "task_status": task.status.value,
            "enabled": task.enabled,
        }

    def _error(self, message: str) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            output=message,
            success=False,
            error=message,
        )
