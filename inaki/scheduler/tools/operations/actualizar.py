"""``update``: campos mutables, con los pares acoplados validados contra lo EFECTIVO.

``trigger_type`` solo cambia junto a su ``trigger_payload`` (la forma del payload
es específica del tipo) y ``task_kind`` solo junto a su ``schedule`` (cron ↔
datetime). Reenviar el valor actual es un no-op, no un error. El status runtime
NO es mutable: la intención on/off se expresa con ``enable``/``disable``.
"""

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
    parsear_entero,
    parsear_schedule,
    resolver_trigger_payload,
)
from inaki.scheduler.tools.operations._base import Operacion
from inaki.shared.errors import (
    BuiltinTaskProtectedError,
    SchedulerError,
    TaskNotFoundError,
)

logger = logging.getLogger(__name__)

# Campos que el LLM puede tocar. El status runtime NO está: setear 'running' a
# mano brickea la task (el loop solo levanta 'pending').
MUTABLE_FIELDS = frozenset(
    {
        "name",
        "description",
        "schedule",
        "trigger_type",
        "trigger_payload",
        "task_kind",
        "executions_remaining",
    }
)

# Campos que no se pueden cambiar solos: mover uno sin el otro deja la task
# incoherente (el dominio lo rechaza) o con un schedule del formato equivocado.
COUPLED_FIELDS: dict[str, str] = {
    "trigger_type": "trigger_payload",
    "task_kind": "schedule",
}

_CAMPOS_QUE_NECESITAN_LA_TASK = ("schedule", "trigger_payload", "trigger_type", "task_kind")


class Actualizar(Operacion):
    async def ejecutar(self, params: dict[str, Any]) -> ToolResult:
        task_id = parsear_entero(params, "task_id")
        if isinstance(task_id, ToolResult):
            return task_id

        updates: dict[str, Any] = {}
        if "name" in params:
            updates["name"] = str(params["name"])
        if "description" in params:
            updates["description"] = str(params["description"])
        if "executions_remaining" in params:
            updates["executions_remaining"] = params["executions_remaining"]

        payload_raw: dict[str, Any] | None = None
        if "trigger_payload" in params:
            coerced = coerce_to_dict(params["trigger_payload"])
            if not isinstance(coerced, dict):
                logger.error(
                    "scheduler.update: trigger_payload inválido — type=%s, repr=%r",
                    type(params["trigger_payload"]).__name__,
                    params["trigger_payload"],
                )
                return error("'trigger_payload' must be an object.")
            payload_raw = coerced

        # Las ramas acopladas necesitan la task existente: para saber si el valor
        # recibido CAMBIA algo y para validar contra los valores EFECTIVOS (los
        # nuevos si vienen en esta llamada, los viejos si no).
        existing: ScheduledTask | None = None
        if any(k in params for k in _CAMPOS_QUE_NECESITAN_LA_TASK):
            try:
                existing = await self._ctx.uc.get_task(task_id)
            except (TaskNotFoundError, SchedulerError) as exc:
                return error(str(exc))

        trigger_type = self._trigger_type_efectivo(params, existing, payload_raw, updates)
        if isinstance(trigger_type, ToolResult):
            return trigger_type
        task_kind = self._task_kind_efectivo(params, existing, updates)
        if isinstance(task_kind, ToolResult):
            return task_kind

        if "schedule" in params:
            schedule = parsear_schedule(
                str(params["schedule"]).strip(),
                es_recurrente=task_kind == TaskKind.RECURRENT,
                user_timezone=self._ctx.user_timezone,
            )
            if isinstance(schedule, ToolResult):
                return schedule
            updates["schedule"] = schedule

        if payload_raw is not None:
            assert existing is not None  # garantizado por el fetch de arriba
            if trigger_type not in ALLOWED_TRIGGER_TYPES:
                return error(
                    f"Cannot update trigger_payload for system trigger type '{trigger_type}'."
                )
            payload = resolver_trigger_payload(
                self._ctx, trigger_type, payload_raw, existente=existing
            )
            if isinstance(payload, ToolResult):
                return payload
            updates["trigger_payload"] = payload

        if not updates:
            return error(
                f"No mutable fields provided. Mutable fields: {', '.join(sorted(MUTABLE_FIELDS))}."
            )
        try:
            updated = await self._ctx.uc.update_task(task_id, **updates)
        except (BuiltinTaskProtectedError, TaskNotFoundError, SchedulerError) as exc:
            return error(str(exc))
        return ok(echo_task(updated, op="updated"))

    @staticmethod
    def _trigger_type_efectivo(
        params: dict[str, Any],
        existing: ScheduledTask | None,
        payload_raw: dict[str, Any] | None,
        updates: dict[str, Any],
    ) -> str | ToolResult:
        """El tipo contra el que se valida el payload: el pedido si cambia, el actual si no."""
        efectivo = existing.trigger_type.value if existing is not None else ""
        if "trigger_type" not in params:
            return efectivo
        assert existing is not None  # garantizado por el fetch del caller
        requested = str(params["trigger_type"] or "").strip().lower()
        if requested not in ALLOWED_TRIGGER_TYPES:
            return error(
                f"Invalid 'trigger_type': '{requested}'. "
                f"Must be one of: {', '.join(sorted(ALLOWED_TRIGGER_TYPES))}."
            )
        if requested == existing.trigger_type.value:
            return efectivo
        if payload_raw is None:
            example = PAYLOAD_EXAMPLE_BY_TRIGGER.get(requested, '{"text": "..."}')
            return error(
                f"Changing 'trigger_type' to '{requested}' requires sending "
                f"'{COUPLED_FIELDS['trigger_type']}' in the SAME call: the payload "
                f"shape is type-specific, so the current one is not valid for the "
                f"new type. Retry with trigger_type='{requested}' and "
                f"trigger_payload={example}."
            )
        updates["trigger_type"] = TriggerType(requested)
        return requested

    @staticmethod
    def _task_kind_efectivo(
        params: dict[str, Any], existing: ScheduledTask | None, updates: dict[str, Any]
    ) -> TaskKind | None | ToolResult:
        """El kind contra el que se interpreta el schedule: el pedido si cambia, el actual si no."""
        efectivo = existing.task_kind if existing is not None else None
        if "task_kind" not in params:
            return efectivo
        assert existing is not None  # garantizado por el fetch del caller
        requested = str(params["task_kind"] or "").strip().lower()
        if requested not in LLM_TO_TASK_KIND:
            return error(f"Invalid 'task_kind': '{requested}'. Must be 'one_shot' or 'recurring'.")
        domain_kind = TaskKind(LLM_TO_TASK_KIND[requested])
        if domain_kind == existing.task_kind:
            return efectivo
        if "schedule" not in params:
            expected = (
                "a cron expression (e.g. '0 8 * * *')"
                if domain_kind == TaskKind.RECURRENT
                else "a datetime ('+2h' or ISO 8601)"
            )
            return error(
                f"Changing 'task_kind' to '{requested}' requires sending "
                f"'{COUPLED_FIELDS['task_kind']}' in the SAME call: the current "
                f"schedule is in the format of the OLD kind. Retry with "
                f"task_kind='{requested}' and schedule set to {expected}."
            )
        updates["task_kind"] = domain_kind
        return domain_kind
