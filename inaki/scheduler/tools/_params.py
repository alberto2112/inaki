"""Validación y resolución de parámetros compartida por las operaciones.

Acá vive UNA vez lo que ``create`` y ``update`` necesitan por igual: parsear el
``schedule`` contra el kind EFECTIVO de la tarea, resolver el destino de un
``channel_send`` y el alias ``self`` de un ``agent_send``, y validar el
``trigger_payload`` contra el tipo EFECTIVO. La paridad entre las dos
operaciones (nota ``scheduler-trigger-type-mutable``) deja de depender de que
alguien se acuerde de copiar un cambio: no hay dos copias.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, cast

from pydantic import BaseModel

from inaki.kernel.ports.outbound.tool_port import ToolResult
from inaki.scheduler.domain.task import (
    AgentSendPayload,
    ChannelSendPayload,
    ScheduledTask,
    ShellExecPayload,
    TriggerPayload,
)
from inaki.scheduler.domain.time_parser import parse_schedule
from inaki.scheduler.tools._context import Contexto, error

logger = logging.getLogger(__name__)

# Trigger types expuestos al LLM (consolidate_memory y reconcile_memory son del sistema).
ALLOWED_TRIGGER_TYPES = frozenset({"channel_send", "agent_send", "shell_exec"})

TRIGGER_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "channel_send": ChannelSendPayload,
    "agent_send": AgentSendPayload,
    "shell_exec": ShellExecPayload,
}

# Ejemplos de trigger_payload por tipo — van en el mensaje de error para que el
# LLM pueda auto-corregirse en el retry dentro del tool loop.
PAYLOAD_EXAMPLE_BY_TRIGGER: dict[str, str] = {
    "channel_send": '{"text": "mensaje"}',
    "agent_send": '{"task": "descripción de lo que el agente debe hacer"}',
    "shell_exec": '{"command": "comando a ejecutar"}',
}

# Margen de gracia para schedules one-shot "en el pasado": un reloj levemente
# desfasado o un parse al filo del minuto no deben rechazar la creación.
PAST_SCHEDULE_GRACE_SECONDS = 60

# TaskKind del dominio ↔ nombre que ve el LLM.
TASK_KIND_TO_LLM = {"oneshot": "one_shot", "recurrent": "recurring"}
LLM_TO_TASK_KIND = {v: k for k, v in TASK_KIND_TO_LLM.items()}


def coerce_to_dict(value: Any) -> Any:
    """Intenta parsear ``value`` como dict si el LLM lo envió como JSON string."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return value


def parsear_entero(params: dict[str, Any], nombre: str) -> int | ToolResult:
    """``task_id`` / ``log_id``: obligatorio y entero. Devuelve el error listo si no."""
    raw = params.get(nombre)
    if raw is None:
        return error(f"Missing required parameter '{nombre}'.")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return error(f"Invalid '{nombre}': '{raw}'. Must be an integer.")


def parsear_schedule(
    schedule_raw: str, *, es_recurrente: bool, user_timezone: str
) -> str | ToolResult:
    """Normaliza el ``schedule`` según el kind EFECTIVO de la tarea.

    - Recurrente: expresión cron cruda (croniter la evalúa en el repo); un
      offset relativo (``+2h``) es error.
    - One-shot: relativo (``+Xd``, ``+Xh``, ``+Xm``) o ISO 8601, normalizado a
      UTC ISO. Un instante en el pasado (con gracia) es error accionable: casi
      siempre es un typo de fecha o un "hoy a las 9" cuando ya son las 10.
    """
    if schedule_raw.startswith("+"):
        if es_recurrente:
            return error("Recurring tasks require a cron expression, not a relative time offset.")
        try:
            return parse_schedule(schedule_raw, user_timezone).isoformat()
        except ValueError as exc:
            return error(
                f"Invalid relative schedule '{schedule_raw}'. "
                f"Use format: +Xd, +Xh, +Xm or combinations (e.g. +2d3h30m). "
                f"Detail: {exc}"
            )
    if es_recurrente:
        return schedule_raw
    try:
        dt = parse_schedule(schedule_raw, user_timezone)
    except ValueError as exc:
        return error(str(exc))
    if (datetime.now(timezone.utc) - dt).total_seconds() > PAST_SCHEDULE_GRACE_SECONDS:
        return error(
            f"Schedule '{schedule_raw}' resolves to {dt.isoformat()}, which is in the past. "
            f"One-shot tasks must be scheduled in the future — adjust the date/time "
            f"(e.g. tomorrow) and retry."
        )
    return dt.isoformat()


def resolver_trigger_payload(
    ctx: Contexto,
    trigger_type: str,
    payload_raw: dict[str, Any],
    *,
    existente: ScheduledTask | None,
) -> TriggerPayload | ToolResult:
    """Completa y valida un ``trigger_payload`` contra ``trigger_type`` (el EFECTIVO).

    ``existente`` es la tarea previa en ``update`` (``None`` en ``create``). La
    MISMA regla para los dos destinos: un ``channel_send`` sin ``target`` y un
    ``agent_send`` sin ``output_channel`` conservan el de la tarea si YA era de
    ese tipo y lo tenía; si no, caen a la conversación actual. Muta ``payload_raw``.
    """
    if trigger_type == "channel_send":
        target, err = _resolver_target_channel_send(ctx, payload_raw)
        if err is not None:
            return err
        if target is None:
            previo = existente.trigger_payload if existente is not None else None
            if isinstance(previo, ChannelSendPayload):
                target = previo.target
            else:
                context = ctx.get_channel_context()
                if context is None:
                    return error(
                        "No hay contexto de canal disponible. channel_send necesita una "
                        "conversación interactiva o un 'target' explícito "
                        "(ej. 'telegram:-1001582404077')."
                    )
                target = context.routing_key
        payload_raw["target"] = target

    if trigger_type == "agent_send":
        # Sin agent_id o con el alias 'self' → el agente que opera. Delegar a
        # otro agente requiere un id explícito distinto.
        raw_agent_id = payload_raw.get("agent_id")
        if raw_agent_id is None or str(raw_agent_id).strip().lower() == "self":
            payload_raw["agent_id"] = ctx.agent_id
        if payload_raw.get("output_channel") is None:
            previo = existente.trigger_payload if existente is not None else None
            if isinstance(previo, AgentSendPayload) and previo.output_channel:
                payload_raw["output_channel"] = previo.output_channel
            else:
                context = ctx.get_channel_context()
                if context is not None:
                    payload_raw["output_channel"] = context.routing_key

    modelo = TRIGGER_PAYLOAD_MODELS[trigger_type]
    try:
        payload_raw["type"] = trigger_type
        return cast(TriggerPayload, modelo.model_validate(payload_raw))
    except Exception as exc:  # noqa: BLE001
        return error(f"Invalid trigger_payload for '{trigger_type}': {exc}")


def _resolver_target_channel_send(
    ctx: Contexto, payload_raw: dict[str, Any]
) -> tuple[str | None, ToolResult | None]:
    """Destino de un ``channel_send`` a partir del payload del LLM.

    Prioridad: ``target`` explícito (``"canal:id"``) → ``user_id`` (otro usuario
    del mismo canal que la conversación) → nada (``(None, None)``: el caller
    decide el default). Quita del payload las claves ya consumidas.
    """
    explicit = payload_raw.pop("target", None)
    if isinstance(explicit, str) and explicit.strip():
        candidate = explicit.strip()
        prefix, sep, dest = candidate.partition(":")
        if not (sep and prefix and dest):
            return None, error(
                f"Invalid 'target': '{candidate}'. Use the form 'channel:id' "
                f"(e.g. 'telegram:-1001582404077'). Omit 'target' to send to the "
                f"current conversation."
            )
        # El target explícito gana sobre user_id (destino completo y sin ambigüedad).
        payload_raw.pop("user_id", None)
        return candidate, None
    llm_user_id = payload_raw.pop("user_id", None)
    if llm_user_id is not None:
        context = ctx.get_channel_context()
        if context is None:
            return None, error(
                "No hay contexto de canal para resolver 'user_id'. Usá un 'target' "
                "explícito (ej. 'telegram:-1001582404077')."
            )
        payload_raw["user_id"] = str(llm_user_id)
        return f"{context.channel_type}:{llm_user_id}", None
    return None, None


def echo_task(task: ScheduledTask, *, op: str) -> dict[str, Any]:
    """Echo autoconfirmable tras create/update: flag explícito + valores persistidos.

    El LLM necesita saber sin ambigüedad que la operación tomó y con qué valores
    finales: ``next_run_at`` (recomputado por el repo), ``task_status`` (puede
    haberse reseteado a pending en un edit invalidante) y ``enabled`` (la
    intención declarada: ``False`` no corre aunque el status diga pending).
    """
    return {
        f"{op}": True,
        "id": task.id,
        "name": task.name,
        "task_kind": TASK_KIND_TO_LLM.get(task.task_kind.value, task.task_kind.value),
        "trigger_type": task.trigger_type.value,
        "schedule": task.schedule,
        "next_run_at": task.next_run.isoformat() if task.next_run else None,
        "task_status": task.status.value,
        "enabled": task.enabled,
    }
