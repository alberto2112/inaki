"""Los helpers compartidos de la tool ``scheduler``: el punto ÚNICO de paridad.

Antes ``_create`` y ``_update`` tenían cada uno su copia del parseo del schedule
y de la resolución del payload (nota ``scheduler-trigger-type-mutable``: la
paridad dependía de acordarse). Ahora hay una sola función por regla; estos
tests fijan esas reglas donde viven, y la tabla de despacho garantiza que la
fachada no conoce operaciones que no existan.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from core.ports.outbound.tool_port import ToolResult
from inaki.scheduler.domain.task import (
    AgentSendPayload,
    ChannelSendPayload,
    ScheduledTask,
    TaskKind,
    TriggerType,
)
from inaki.scheduler.tools._context import Contexto
from inaki.scheduler.tools._params import parsear_schedule, resolver_trigger_payload
from inaki.scheduler.tools.operations import OPERACIONES
from inaki.scheduler.tools.scheduler_tool import SchedulerTool
from inaki.shared.channel_context import ChannelContext


def _ctx(context: ChannelContext | None) -> Contexto:
    return Contexto(
        uc=MagicMock(),
        runner=MagicMock(),
        agent_id="agente",
        user_timezone="UTC",
        get_channel_context=lambda: context,
    )


def _tarea_channel_send(target: str) -> ScheduledTask:
    return ScheduledTask(
        name="t",
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.CHANNEL_SEND,
        trigger_payload=ChannelSendPayload(text="hola", target=target),
        schedule=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        created_by="agente",
    )


# --- schedule contra el kind EFECTIVO --------------------------------------


def test_recurrente_pasa_el_cron_crudo_y_rechaza_el_offset() -> None:
    assert parsear_schedule("0 8 * * *", es_recurrente=True, user_timezone="UTC") == "0 8 * * *"
    err = parsear_schedule("+2h", es_recurrente=True, user_timezone="UTC")
    assert isinstance(err, ToolResult) and "cron expression" in err.output


def test_oneshot_normaliza_relativo_e_iso_y_rechaza_el_pasado() -> None:
    relativo = parsear_schedule("+2h", es_recurrente=False, user_timezone="UTC")
    assert isinstance(relativo, str) and datetime.fromisoformat(relativo) > datetime.now(
        timezone.utc
    )
    futuro = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert parsear_schedule(futuro, es_recurrente=False, user_timezone="UTC") == futuro
    pasado = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    err = parsear_schedule(pasado, es_recurrente=False, user_timezone="UTC")
    assert isinstance(err, ToolResult) and "in the past" in err.output


# --- destino de channel_send y alias de agent_send, iguales en create y update ---


def test_channel_send_sin_destino_cae_a_la_conversacion_en_create() -> None:
    ctx = _ctx(ChannelContext(channel_type="telegram", user_id="42"))
    payload = resolver_trigger_payload(ctx, "channel_send", {"text": "hola"}, existente=None)
    assert isinstance(payload, ChannelSendPayload) and payload.target == "telegram:42"


def test_channel_send_sin_destino_conserva_el_target_de_la_tarea_en_update() -> None:
    ctx = _ctx(ChannelContext(channel_type="telegram", user_id="42"))
    payload = resolver_trigger_payload(
        ctx,
        "channel_send",
        {"text": "hola"},
        existente=_tarea_channel_send("telegram:-100"),
    )
    assert isinstance(payload, ChannelSendPayload) and payload.target == "telegram:-100"


def test_channel_send_sin_destino_ni_conversacion_es_error_accionable() -> None:
    err = resolver_trigger_payload(_ctx(None), "channel_send", {"text": "hola"}, existente=None)
    assert isinstance(err, ToolResult) and "'target' explícito" in err.output


def _tarea_agent_send(output_channel: str | None) -> ScheduledTask:
    return ScheduledTask(
        name="t",
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.AGENT_SEND,
        trigger_payload=AgentSendPayload(agent_id="otro", task="x", output_channel=output_channel),
        schedule=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        created_by="agente",
    )


def test_agent_send_resuelve_self_y_hereda_el_canal_activo_en_create_y_update() -> None:
    """La misma regla que el ``target`` de ``channel_send``: sin dato, la conversación."""
    ctx = _ctx(ChannelContext(channel_type="telegram", user_id="42"))
    en_create = resolver_trigger_payload(
        ctx, "agent_send", {"task": "x", "agent_id": "self"}, existente=None
    )
    en_update = resolver_trigger_payload(
        ctx, "agent_send", {"task": "x"}, existente=_tarea_agent_send(None)
    )
    assert isinstance(en_create, AgentSendPayload) and isinstance(en_update, AgentSendPayload)
    assert en_create.agent_id == "agente" and en_update.agent_id == "agente"
    assert en_create.output_channel == "telegram:42"
    assert en_update.output_channel == "telegram:42"


def test_agent_send_en_update_conserva_el_output_channel_de_la_tarea() -> None:
    ctx = _ctx(ChannelContext(channel_type="telegram", user_id="42"))
    payload = resolver_trigger_payload(
        ctx, "agent_send", {"task": "y"}, existente=_tarea_agent_send("telegram:-100")
    )
    assert isinstance(payload, AgentSendPayload) and payload.output_channel == "telegram:-100"


# --- la tabla de despacho ES el contrato ------------------------------------


def test_el_schema_expone_exactamente_las_operaciones_de_la_tabla() -> None:
    enum = SchedulerTool.parameters_schema["properties"]["operation"]["enum"]
    assert enum == list(OPERACIONES)
    assert set(OPERACIONES) == {
        "create",
        "list",
        "get",
        "update",
        "delete",
        "enable",
        "disable",
        "run",
        "logs",
        "log_get",
    }
