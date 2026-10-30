"""BackgroundTasksTool — lista, cancela e inspecciona las delegaciones en background del agente.

Tercera superficie de la capacidad de la cola (``IBackgroundDelegationQueue``):
el LLM la usa como tool, y el operador por el gateway admin
(``POST /admin/tool/invoke`` / ``inaki tool background_tasks``) — misma lógica,
sin pasarelas por canal.

Nace de un caso real: un ``bg-N`` cuya entrega falló quedó "corriendo" 6 horas
y el agente no tenía forma de cerrarlo. Con ``cancel`` el agente corta una
delegación que ya no sirve, y descarta una ``delivery_failed`` recuperando el
resultado que el hijo había producido.

Con ``inspect`` el agente mira POR QUÉ terminó una delegación (o cómo va): estado,
tiempos, presupuesto, fallo clasificado y los últimos pasos del transcript del hijo.
Es de solo lectura.

Cada instancia está ligada a SU agente (``caller_agent_id``): solo ve, corta e inspecciona
las delegaciones que ese agente lanzó.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from inaki.kernel.ports.tool_port import ITool, ToolResult

if TYPE_CHECKING:
    from inaki.kernel.domain.background_task import (
        BackgroundTask,
        DelegationFailure,
        TaskInspection,
        TranscriptEntry,
        WorkdirState,
    )
    from inaki.kernel.ports.background_delegation_port import IBackgroundDelegationQueue

# Pasos del transcript que devuelve ``inspect`` por defecto y como máximo.
_LAST_N_DEFECTO = 10
_LAST_N_MAXIMO = 50
# Topes de lo que viaja al contexto del LLM. El store ya acota cada mensaje a 12450; acá se
# acota más porque un ``inspect`` puede traer 50 pasos y eso va entero al prompt del padre.
_CONTENIDO_MAX_CHARS = 1500
_PROMPT_MAX_CHARS = 500


def _acotar(texto: str, tope: int = _CONTENIDO_MAX_CHARS) -> str:
    """Cabeza 40 % + marcador + cola 60 % si ``texto`` excede ``tope`` (la cola es donde
    viven los tracebacks y el final de un output); si no, íntegro."""
    if len(texto) <= tope:
        return texto
    cabeza = tope * 40 // 100
    cola = tope - cabeza
    omitidos = len(texto) - cabeza - cola
    return f"{texto[:cabeza]}\n[… {omitidos} characters omitted …]\n{texto[-cola:]}"


def _prompt_acotado(prompt: str) -> str:
    """El prompt original, cortado en ``_PROMPT_MAX_CHARS`` con elipsis (la cabeza dice qué se pidió)."""
    if len(prompt) <= _PROMPT_MAX_CHARS:
        return prompt
    return prompt[: _PROMPT_MAX_CHARS - 1] + "…"


def _paso_a_dict(paso: "TranscriptEntry") -> dict:
    llamadas = None
    if paso.tool_calls:
        llamadas = []
        for tc in paso.tool_calls:
            funcion = tc.get("function") or {}
            argumentos = funcion.get("arguments", "")
            if not isinstance(argumentos, str):
                argumentos = json.dumps(argumentos, ensure_ascii=False)
            llamadas.append(
                {
                    "id": tc.get("id"),
                    "name": funcion.get("name"),
                    "arguments": _acotar(argumentos),
                }
            )
    return {
        "seq": paso.seq,
        "role": paso.role.value,
        "content": _acotar(paso.content),
        "tool_calls": llamadas,
        "tool_call_id": paso.tool_call_id,
        "truncated_from": paso.original_chars,
    }


def _fallo_a_dict(falla: "DelegationFailure") -> dict:
    en_vuelo = falla.in_flight_tool
    return {
        "kind": falla.kind,
        "detail": falla.detail,
        "last_step": falla.last_step,
        "in_flight_tool": (
            {
                "name": en_vuelo.name,
                "tool_call_id": en_vuelo.tool_call_id,
                "arguments_preview": en_vuelo.arguments_preview,
            }
            if en_vuelo is not None
            else None
        ),
        "not_started_tools": falla.not_started_tools,
    }


def _workdir_a_dict(w: "WorkdirState") -> dict:
    payload: dict = {
        "path": w.relative_path,
        "absolute_path": w.path,
        "exists": w.exists,
        "entries": [
            {"name": e.name, "type": e.kind, "size_bytes": e.size_bytes} for e in w.entries
        ],
        "entries_total": w.entries_total,
        "entries_omitted": max(0, w.entries_total - len(w.entries)),
    }
    if w.note:
        payload["note"] = w.note
    return payload


def _pausa_a_dict(insp: "TaskInspection") -> dict:
    """Campos de una task en ``awaiting_input``: la pregunta completa y cómo continuar.

    ``pending_question`` es ``None`` si el proceso no la tiene en memoria (la rehidratación
    no pudo recuperarla): se dice, no se inventa.
    """
    t = insp.task
    reanudable = not (t.transcript_incomplete or insp.transcript_incomplete)
    plantilla = _COMO_REANUDAR if reanudable else _NO_REANUDABLE
    return {
        "pending_question": t.question,
        "resumable": reanudable,
        "how_to_resume": plantilla.format(id=t.id),
    }


def _iso(valor: datetime | None) -> str | None:
    return valor.isoformat() if valor is not None else None


_COMO_REANUDAR = (
    'Resume it with delegate(resume="{id}", task=<your answer>) — do NOT start a new '
    'delegation — or abandon it with background_tasks(action=cancel, task_id="{id}").'
)
_NO_REANUDABLE = (
    "It cannot be resumed (its saved transcript is incomplete). If the answer is still "
    "needed, ask the user and delegate again including the answer; dismiss this one with "
    'background_tasks(action=cancel, task_id="{id}").'
)


def _hay_desenlace(task: "BackgroundTask") -> bool:
    return task.result_status is not None or task.result_parse_error


class BackgroundTasksTool(ITool):
    """Consulta, cancelación e inspección de las delegaciones async del agente."""

    name = "background_tasks"
    description = (
        "List, inspect or cancel your background delegations (the bg-N tasks launched with "
        "delegate wait=false). action=list returns each pending task with its status. "
        "action=inspect with a task_id shows why a task ended (or how it is going): status, "
        "timings, budget, failure details and the last steps of the child's transcript. "
        "action=cancel with a task_id stops a queued or running task, or dismisses a "
        "delivery_failed, interrupted or awaiting_input task (a delivery_failed one also "
        "returns the result it had produced). A task in awaiting_input is a child that "
        "paused with a question: answer it with delegate(resume=bg-N, task=your answer) "
        "instead of cancelling it."
    )
    routing_keywords = (
        "cancelar tarea en segundo plano, cancelar delegación, tarea colgada, "
        "cerrar bg, estado de la delegación, tareas en background, por qué falló la tarea, "
        "inspeccionar tarea; "
        "cancel background task, stop delegation, stuck task, delegation status, "
        "inspect background task, why did the task fail; "
        "annuler la tâche en arrière-plan, annuler la délégation, tâche bloquée"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "cancel", "inspect"],
                "description": (
                    "list: show your pending background tasks. cancel: stop or dismiss one. "
                    "inspect: show the details and transcript of one (also finished ones)."
                ),
            },
            "task_id": {
                "type": "string",
                "description": (
                    "The bg-N id of the task. Required for action=cancel and action=inspect."
                ),
            },
            "last_n": {
                "type": "integer",
                "description": (
                    "For action=inspect: how many of the most recent transcript steps to "
                    f"return (default {_LAST_N_DEFECTO}, max {_LAST_N_MAXIMO})."
                ),
            },
        },
        "required": ["action"],
    }

    def __init__(self, *, queue: "IBackgroundDelegationQueue", caller_agent_id: str) -> None:
        self._queue = queue
        self._caller_agent_id = caller_agent_id

    async def execute(  # type: ignore[override]
        self,
        action: str,
        task_id: str | None = None,
        last_n: int | str | None = None,
        **kwargs,
    ) -> ToolResult:
        if action == "list":
            return self._list()
        if action == "cancel":
            if not task_id:
                return self._error("task_id is required for action=cancel.")
            return await self._cancel(task_id)
        if action == "inspect":
            if not task_id:
                return self._error("task_id is required for action=inspect.")
            return await self._inspect(task_id, last_n)
        return self._error(f"Unknown action '{action}'. Valid actions: list, cancel, inspect.")

    def _list(self) -> ToolResult:
        tasks = self._queue.snapshot_inflight(self._caller_agent_id)
        payload = {
            "tasks": [t.model_dump(exclude_none=True) for t in tasks],
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if not tasks:
            payload["note"] = (
                "No pending background tasks for you. Finished tasks already delivered their "
                "[bg-N] result; use action=inspect with a task_id to review any recent one."
            )
        elif any(t.status == "awaiting_input" for t in tasks):
            payload["note"] = (
                "A task in awaiting_input paused with the question shown in question_preview "
                '(action=inspect shows it in full). Resume it with delegate(resume="bg-N", '
                "task=<your answer>) — do NOT start a new delegation — or abandon it with "
                "action=cancel. If resumable is false it can only be cancelled."
            )
        return self._ok(payload)

    async def _cancel(self, task_id: str) -> ToolResult:
        res = await self._queue.cancel(task_id, self._caller_agent_id)
        if res.outcome == "cancelled":
            return self._ok(
                {
                    "outcome": "cancelled",
                    "task_id": task_id,
                    "message": "The task was stopped. No [bg-N] result will arrive for it.",
                }
            )
        if res.outcome == "dismissed" and res.status == "awaiting_input":
            return self._ok(
                {
                    "outcome": "dismissed",
                    "task_id": task_id,
                    "message": (
                        "The child was waiting for your answer; the task is dismissed and "
                        "will not continue."
                    ),
                    "result": res.result,
                }
            )
        if res.outcome == "dismissed" and res.status == "interrupted":
            return self._ok(
                {
                    "outcome": "dismissed",
                    "task_id": task_id,
                    "message": (
                        "The task had been interrupted by a daemon restart and is now "
                        "dismissed. It will not deliver a result."
                    ),
                    "result": res.result,
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
        if res.outcome == "finished":
            return self._ok(
                {
                    "outcome": "finished",
                    "task_id": task_id,
                    "status": res.status,
                    "message": (
                        f"{task_id} had already finished ({res.status}); there is nothing to "
                        "cancel. Use action=inspect to review it."
                    ),
                }
            )
        if res.outcome == "delivering":
            return self._error(
                f"{task_id} has finished and its result is being delivered right now; "
                "it cannot be cancelled. Its [bg-N] message is about to arrive."
            )
        # not_found: la cola no la TIENE — no afirmar que nunca existió.
        return self._error(
            f"{task_id} is not among your background tasks. It may have already delivered "
            "its [bg-N] result, been cancelled or pruned. Use action=list to see what is "
            "pending."
        )

    async def _inspect(self, task_id: str, last_n: int | str | None) -> ToolResult:
        try:
            n = _LAST_N_DEFECTO if last_n is None else int(last_n)
        except (TypeError, ValueError):
            return self._error(f"last_n must be an integer between 1 and {_LAST_N_MAXIMO}.")
        n = max(1, min(n, _LAST_N_MAXIMO))
        insp = await self._queue.inspect(task_id, self._caller_agent_id, last_n=n)
        if insp is None:
            # "No la tengo" no es "no existe": la retención poda lo viejo, y una task ajena
            # responde igual que una inexistente (no se revela su existencia).
            return self._error(
                f"I no longer have {task_id}. Finished tasks are kept 14 days (last 20 per "
                "agent), so it may have been pruned — or it is not one of your tasks."
            )
        return self._ok(self._inspeccion_a_dict(insp))

    @staticmethod
    def _inspeccion_a_dict(insp: "TaskInspection") -> dict:
        t = insp.task
        payload: dict = {
            "task_id": t.id,
            "status": t.status,
            "target_agent_id": t.target_agent_id,
            "prompt": _prompt_acotado(t.prompt),
            "created_at": _iso(t.started_at),
            "run_started_at": _iso(t.run_started_at),
            "finished_at": _iso(t.finished_at),
            "budget": {"timeout_seconds": t.timeout_seconds, "max_iterations": t.max_iterations},
            "iterations_used": t.iterations_used,
            "outcome": (
                {"result_status": t.result_status, "result_parse_error": t.result_parse_error}
                if _hay_desenlace(t)
                else None
            ),
            "failure": _fallo_a_dict(t.failure) if t.failure is not None else None,
            "delivery_error": t.error,
            "workdir": _workdir_a_dict(insp.workdir) if insp.workdir is not None else None,
            "input_files": t.input_files,
            "persisted": insp.persisted,
            "messages_total": insp.messages_total,
            "messages_omitted": max(0, insp.messages_total - len(insp.messages)),
            "messages": [_paso_a_dict(m) for m in insp.messages],
        }
        if t.status == "awaiting_input":
            payload.update(_pausa_a_dict(insp))
        notas = []
        if not insp.persisted:
            notas.append(
                "This task has no durable transcript (background task transcript/durable "
                "state unavailable): only its current state is shown, without steps."
            )
        elif insp.transcript_incomplete:
            notas.append(
                "The durable transcript is incomplete: some writes failed, so steps after "
                "the last one shown may be missing."
            )
        if notas:
            payload["note"] = " ".join(notas)
        return payload

    def _ok(self, payload: dict) -> ToolResult:
        return ToolResult(
            tool_name=self.name, output=json.dumps(payload, ensure_ascii=False), success=True
        )

    def _error(self, message: str) -> ToolResult:
        return ToolResult(tool_name=self.name, output=message, success=False, error=message)
