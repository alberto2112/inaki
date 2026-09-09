"""SchedulerTool — expone el scheduler al LLM como una tool multi-operación.

Operations:
  - create   : crea una nueva tarea programada
  - list     : lista todas las tareas (sin filtro de agente)
  - get      : obtiene una tarea por ID (detalle completo con trigger_payload)
  - update   : modifica campos mutables de una tarea existente
  - delete   : elimina una tarea (builtin tasks protegidas)
  - enable   : habilita una tarea (re-arma FAILED/MISSED)
  - disable  : deshabilita una tarea sin borrarla (pausa)
  - run      : dispara una tarea AHORA, fuera de su agenda — NO destructivo
  - logs     : lista logs de ejecución (una tarea o global si se omite task_id)
  - log_get  : obtiene un log por ID con output/error completos (sin truncación)

Esta clase es la FACHADA: el contrato ``ITool`` (nombre, descripción, schema) y
el despacho por tabla (``operations.OPERACIONES``). Cada operación es un objeto
chico en ``operations/`` con su validación; lo que ``create`` y ``update``
comparten (schedule contra el kind efectivo, destino de ``channel_send``, alias
``self`` de ``agent_send``, payload contra el tipo efectivo) vive UNA vez en
``_params.py``, así la paridad entre las dos no depende de que alguien se acuerde.

El status runtime (pending/running/...) NO es mutable desde el LLM: es estado
interno del loop. La intención "quiero/no quiero que corra" se expresa con
enable/disable; re-armar una task se logra editando schedule/trigger_payload
(el use case resetea el runtime automáticamente en edits invalidantes).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from inaki.kernel.ports.outbound.tool_port import ITool, ToolResult
from inaki.scheduler.domain.task import USER_TASK_ID_START
from inaki.scheduler.tools._context import Contexto, error
from inaki.scheduler.tools._params import ALLOWED_TRIGGER_TYPES
from inaki.scheduler.tools.operations import OPERACIONES, Operacion
from inaki.scheduler.tools.operations.logs import MAX_LOGS_LIMIT
from inaki.shared.channel_context import ChannelContext

if TYPE_CHECKING:
    from inaki.scheduler.ports.use_case import IManualTaskRunner, ISchedulerUseCase

logger = logging.getLogger(__name__)


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
        "Set reminders, alarms, and recurring or one-time scheduled tasks. "
        "Use this whenever the user wants something to happen later or on a repeating basis: "
        "a reminder ('remind me tomorrow at 9'), a recurring action ('every Monday', 'every day at 8am'), "
        "a one-time future action ('in one hour', 'next Friday'), or an alert/notification at a given time. "
        "Also use it to review or change existing scheduled tasks. "
        "Operations: create, list, get, update, delete, enable, disable, run, logs, log_get. "
        "Use 'create' to schedule a future action (one_shot or recurring). "
        "REQUIRED for 'create': name, task_kind, trigger_type, trigger_payload, schedule. "
        "trigger_payload is ALWAYS required for 'create' — it is the object that describes "
        "WHAT the task will do (the text to send, the command to run, the task for the agent). "
        "Without trigger_payload the create call WILL fail. "
        "Use 'list' to see all active tasks. "
        "Use 'get' to retrieve full detail (including trigger_payload) for a specific task. "
        "Use 'update' to modify mutable fields on a task: name, description, schedule, "
        "trigger_type, trigger_payload, task_kind, executions_remaining. Two of them are "
        "COUPLED and must travel together in the same call: changing trigger_type requires "
        "trigger_payload, and changing task_kind requires schedule. "
        "Use 'delete' to remove a non-builtin task permanently. "
        "Use 'enable' to turn a task on (also re-arms a failed/missed task). "
        "Use 'disable' to pause a task without deleting it. "
        "Use 'run' to fire a task RIGHT NOW, off-schedule, as a one-off — use it when the "
        "user asks to run/execute an existing task immediately (e.g. 'run task 107 now'). "
        "It is NON-destructive: it does NOT touch the task's status, next_run or "
        "executions_remaining, so a recurring task keeps its schedule and does not "
        "consume an execution. Requires task_id. "
        "Use 'logs' to list execution logs (newest first, paginated, outputs truncated to 1000 chars). "
        "Omit task_id to list the latest logs across all tasks; include task_id to filter by task. "
        "Use 'log_get' to fetch a single log by id with the FULL untruncated output/error. "
        f"Builtin tasks (id < {USER_TASK_ID_START}) cannot be modified or deleted."
    )
    # Disparadores multilingües solo para el embedding del semantic routing.
    routing_keywords = (
        "recordame, recordarme, acordate de avisarme, avisame, agendá, agenda, programá una tarea, "
        "poné un recordatorio, alarma, despertador, recordatorio, todos los días, todas las semanas, "
        "cada lunes, cada día, en una hora, en diez minutos, mañana a las, esta noche, la semana que viene, "
        "tarea recurrente, tarea programada, recurrente, periódico. "
        "corré la tarea ahora, ejecutá la tarea ahora, corré la tarea ya, disparar la tarea, "
        "correr la tarea manualmente, forzar la ejecución, ejecutar ahora, probá la tarea. "
        "remind me, set a reminder, set an alarm, schedule, scheduled task, recurring task, every day, "
        "every Monday, every week, in one hour, in ten minutes, tomorrow at, tonight, next week, later, "
        "wake me up, notify me at, alert me, cron job, periodic task. "
        "run the task now, run task now, execute the task now, trigger the task, fire the task now, "
        "run it manually, force run, test the task. "
        "rappelle-moi, mets un rappel, planifie, tâche récurrente, tous les jours, chaque lundi, "
        "dans une heure, demain à, rappel, alarme, réveille-moi, plus tard. "
        "exécute la tâche maintenant, lance la tâche maintenant, déclencher la tâche."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(OPERACIONES),
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
                    "'recurring' runs on a cron schedule. "
                    "On 'update' it CAN be changed, but only together with 'schedule' "
                    "in the same call — the old schedule is in the wrong format for the "
                    "new kind (recurring needs cron, one_shot needs a datetime)."
                ),
            },
            "trigger_type": {
                "type": "string",
                "enum": sorted(ALLOWED_TRIGGER_TYPES),
                "description": (
                    "Kind of action to execute when the task fires. "
                    "On 'update' it CAN be changed, but only together with "
                    "'trigger_payload' in the same call — each type has its own "
                    "payload shape, so the existing payload is not valid for a new type."
                ),
            },
            "trigger_payload": {
                "type": "object",
                "description": (
                    "REQUIRED for 'create' and 'update'. Action-specific payload — "
                    "MUST be a JSON object (not a string, not omitted). "
                    "Shape depends on trigger_type: "
                    'channel_send → {"text": "mensaje"}: por default va a la conversación '
                    'actual. Para enviar a OTRO chat, incluí "target" como routing key '
                    '"canal:id" (ej. "telegram:-1001582404077"). Para que el envío quede '
                    "en el historial de OTRO agente (publicar EN SU NOMBRE), incluí "
                    '"agent_id" con su id (ej. "anacleto"). '
                    'agent_send → {"task": "lo que el agente debe hacer"} '
                    "(agent_id se resuelve a 'self' automáticamente; "
                    "incluilo explícito solo para delegar a otro agente). "
                    'shell_exec → {"command": "comando a ejecutar"} '
                    "(working_dir, env_vars, timeout son opcionales). "
                    "Ejemplo completo para agent_send recurrente: "
                    'trigger_payload={"task": "resumir la jornada"}.'
                ),
            },
            "schedule": {
                "type": "string",
                "description": (
                    "When to run the task. Three formats: "
                    "(1) Relative offset: '+Xd', '+Xh', '+Xm', or combinations like '+2d3h30m' — "
                    "converted to an absolute UTC datetime from now. "
                    "(2) ISO 8601 absolute datetime: '2026-04-12T14:00:00-03:00' or "
                    "'2026-04-12T14:00:00Z'. "
                    "If you OMIT the timezone offset (e.g. '2026-04-12T14:00:00'), the time is "
                    "interpreted in the USER'S configured timezone — prefer this form when the "
                    "user says a local time. "
                    "(3) For recurring tasks, a cron expression (e.g. '0 8 * * *'). Cron "
                    "expressions are evaluated in the USER'S configured timezone (with DST), "
                    "so '0 8 * * *' means 08:00 user-local every day. "
                    "Relative offsets (+) are NOT valid for recurring tasks."
                ),
            },
            "executions_remaining": {
                "type": "integer",
                "description": (
                    "For recurring tasks: number of executions before auto-disable. "
                    "Null means infinite."
                ),
            },
            # --- get / update / delete / enable / disable / logs ---
            "task_id": {
                "type": "integer",
                "description": (
                    "Task ID (required for get, update, delete, enable, disable, run). "
                    "For 'logs': optional — omit to list the latest logs across all tasks."
                ),
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
                    f"Máximo de entradas a devolver en 'logs'. Default 10, cap duro {MAX_LOGS_LIMIT}."
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
        manual_runner: IManualTaskRunner,
        agent_id: str,
        user_timezone: str,
        get_channel_context: Callable[[], ChannelContext | None],
    ) -> None:
        self._uc = schedule_task_uc
        # Motor de ejecución (harness-global) — corre una tarea on-demand sin tocar
        # su agenda. Segregado de `_uc` (CRUD) porque disparar un trigger necesita
        # los puertos de dispatch, no solo el repo. Ver IManualTaskRunner.
        self._runner = manual_runner
        self._agent_id = agent_id
        self._user_timezone = user_timezone
        self._get_channel_context = get_channel_context
        ctx = Contexto(
            uc=schedule_task_uc,
            runner=manual_runner,
            agent_id=agent_id,
            user_timezone=user_timezone,
            get_channel_context=get_channel_context,
        )
        self._operaciones: dict[str, Operacion] = {
            nombre: clase(ctx) for nombre, clase in OPERACIONES.items()
        }

    async def execute(self, **kwargs: Any) -> ToolResult:  # type: ignore[override]
        operation = str(kwargs.get("operation") or "").strip().lower()
        operacion = self._operaciones.get(operation)
        if operacion is None:
            return error(
                f"Unknown operation '{operation}'. Valid operations: {', '.join(OPERACIONES)}."
            )
        try:
            return await operacion.ejecutar(kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("SchedulerTool unexpected error (operation=%s)", operation)
            return error(f"Internal error: {exc}")
