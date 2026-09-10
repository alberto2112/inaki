"""``create``: una tarea nueva, con ``created_by`` inyectado (nunca del LLM)."""

from __future__ import annotations

import logging
from typing import Any

from inaki.kernel.ports.tool_port import ToolResult
from inaki.scheduler.domain.task import ScheduledTask, TaskKind, TriggerType
from inaki.scheduler.tools._context import error, ok
from inaki.scheduler.tools._params import (
    ALLOWED_TRIGGER_TYPES,
    LLM_TO_TASK_KIND,
    PAYLOAD_EXAMPLE_BY_TRIGGER,
    coerce_to_dict,
    echo_task,
    parsear_schedule,
    resolver_trigger_payload,
)
from inaki.scheduler.tools.operations._base import Operacion
from inaki.shared.errors import SchedulerError, TooManyActiveTasksError

logger = logging.getLogger(__name__)


class Crear(Operacion):
    async def ejecutar(self, params: dict[str, Any]) -> ToolResult:
        name = str(params.get("name") or "").strip()
        if not name:
            return error("Missing required parameter 'name'.")
        task_kind_raw = str(params.get("task_kind") or "").strip().lower()
        if task_kind_raw not in LLM_TO_TASK_KIND:
            return error(
                f"Invalid 'task_kind': '{task_kind_raw}'. Must be 'one_shot' or 'recurring'."
            )
        trigger_type = str(params.get("trigger_type") or "").strip().lower()
        if trigger_type not in ALLOWED_TRIGGER_TYPES:
            return error(
                f"Invalid 'trigger_type': '{trigger_type}'. "
                f"Must be one of: {', '.join(sorted(ALLOWED_TRIGGER_TYPES))}."
            )
        schedule_raw = str(params.get("schedule") or "").strip()
        if not schedule_raw:
            return error("Missing required parameter 'schedule'.")

        payload_original = params.get("trigger_payload")
        payload_raw = coerce_to_dict(payload_original)
        if not isinstance(payload_raw, dict):
            logger.error(
                "scheduler.create: trigger_payload inválido — type=%s, repr=%r, keys_recibidas=%s",
                type(payload_original).__name__,
                payload_original,
                list(params.keys()),
            )
            example = PAYLOAD_EXAMPLE_BY_TRIGGER.get(trigger_type, '{"text": "..."}')
            return error(
                f"'trigger_payload' is REQUIRED for create and must be a JSON object. "
                f"For trigger_type='{trigger_type}' use: trigger_payload={example}. "
                f"Retry the call including the trigger_payload field."
            )

        es_recurrente = task_kind_raw == "recurring"
        schedule = parsear_schedule(
            schedule_raw, es_recurrente=es_recurrente, user_timezone=self._ctx.user_timezone
        )
        if isinstance(schedule, ToolResult):
            return schedule

        payload = resolver_trigger_payload(self._ctx, trigger_type, payload_raw, existente=None)
        if isinstance(payload, ToolResult):
            return payload

        # next_run lo resuelve el repo desde schedule: ONESHOT → ISO 8601; RECURRENT → cron.
        task = ScheduledTask(
            name=name,
            description=str(params.get("description") or ""),
            task_kind=TaskKind(LLM_TO_TASK_KIND[task_kind_raw]),
            trigger_type=TriggerType(trigger_type),
            trigger_payload=payload,
            schedule=schedule,
            executions_remaining=params.get("executions_remaining"),
            created_by=self._ctx.agent_id,
        )
        try:
            created = await self._ctx.uc.create_task(task)
        except (TooManyActiveTasksError, SchedulerError) as exc:
            return error(str(exc))
        return ok(echo_task(created, op="created"))
