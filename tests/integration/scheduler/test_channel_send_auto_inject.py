"""Tests de integración: inyección automática de canal en channel_send.

Verifica el flujo completo:
  1. SchedulerTool.execute(operation="create", trigger_type="channel_send", ...)
  2. El target se inyecta automáticamente desde el ChannelContext
  3. Cuando el LLM envía user_id explícito, el target se reconstruye con ese user_id
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.outbound.scheduler.sqlite_scheduler_repo import SQLiteSchedulerRepo
from adapters.outbound.tools.scheduler_tool import SchedulerTool
from core.domain.entities.task import ChannelSendPayload
from core.domain.value_objects.channel_context import ChannelContext
from core.use_cases.schedule_task import ScheduleTaskUseCase


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def repo(tmp_path: Path) -> SQLiteSchedulerRepo:
    r = SQLiteSchedulerRepo(str(tmp_path / "sched.db"))
    await r.ensure_schema()
    return r


@pytest.fixture()
def uc(repo: SQLiteSchedulerRepo) -> ScheduleTaskUseCase:
    return ScheduleTaskUseCase(repo=repo, on_mutation=lambda: None)


def _make_tool(
    uc: ScheduleTaskUseCase,
    context: ChannelContext | None,
) -> SchedulerTool:
    return SchedulerTool(
        schedule_task_uc=uc,
        agent_id="test-agent",
        user_timezone="America/Argentina/Buenos_Aires",
        get_channel_context=lambda: context,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_channel_send_inyecta_target_desde_contexto(
    uc: ScheduleTaskUseCase,
    repo: SQLiteSchedulerRepo,
) -> None:
    """Sin user_id del LLM → target viene del routing_key del contexto."""
    contexto = ChannelContext(channel_type="telegram", user_id="987654")
    tool = _make_tool(uc, contexto)

    resultado = await tool.execute(
        operation="create",
        name="recordatorio",
        task_kind="one_shot",
        trigger_type="channel_send",
        schedule="+1h",
        trigger_payload={"text": "Hola mundo"},
    )

    assert resultado.success, f"La tool falló: {resultado.error}"
    datos = json.loads(resultado.output)
    task_id = datos["id"]

    tarea = await repo.get_task(task_id)
    assert tarea is not None
    assert isinstance(tarea.trigger_payload, ChannelSendPayload)
    assert tarea.trigger_payload.target == "telegram:987654"
    assert tarea.trigger_payload.text == "Hola mundo"


async def test_channel_send_user_id_explicito_reemplaza_contexto(
    uc: ScheduleTaskUseCase,
    repo: SQLiteSchedulerRepo,
) -> None:
    """Con user_id del LLM → target usa ese user_id pero mantiene el channel_type del contexto."""
    contexto = ChannelContext(channel_type="telegram", user_id="987654")
    tool = _make_tool(uc, contexto)

    resultado = await tool.execute(
        operation="create",
        name="recordatorio-otro-usuario",
        task_kind="one_shot",
        trigger_type="channel_send",
        schedule="+1h",
        trigger_payload={"text": "Mensaje para otro", "user_id": "111222"},
    )

    assert resultado.success, f"La tool falló: {resultado.error}"
    datos = json.loads(resultado.output)
    task_id = datos["id"]

    tarea = await repo.get_task(task_id)
    assert tarea is not None
    assert isinstance(tarea.trigger_payload, ChannelSendPayload)
    assert tarea.trigger_payload.target == "telegram:111222"
    assert tarea.trigger_payload.user_id == "111222"
    assert tarea.trigger_payload.text == "Mensaje para otro"


async def test_channel_send_sin_contexto_retorna_error(
    uc: ScheduleTaskUseCase,
) -> None:
    """Sin contexto de canal disponible → la tool retorna error descriptivo."""
    tool = _make_tool(uc, context=None)

    resultado = await tool.execute(
        operation="create",
        name="tarea-sin-contexto",
        task_kind="one_shot",
        trigger_type="channel_send",
        schedule="+1h",
        trigger_payload={"text": "Este debería fallar"},
    )

    assert not resultado.success
    assert "No hay contexto de canal" in (resultado.error or "")


async def test_channel_send_descarta_target_del_llm(
    uc: ScheduleTaskUseCase,
    repo: SQLiteSchedulerRepo,
) -> None:
    """Si el LLM envía 'target' directamente, debe ser descartado y reemplazado por el contexto."""
    contexto = ChannelContext(channel_type="telegram", user_id="987654")
    tool = _make_tool(uc, contexto)

    resultado = await tool.execute(
        operation="create",
        name="tarea-target-ignorado",
        task_kind="one_shot",
        trigger_type="channel_send",
        schedule="+1h",
        trigger_payload={"text": "Prueba", "target": "cli:local"},
    )

    assert resultado.success, f"La tool falló: {resultado.error}"
    datos = json.loads(resultado.output)
    task_id = datos["id"]

    tarea = await repo.get_task(task_id)
    assert tarea is not None
    assert isinstance(tarea.trigger_payload, ChannelSendPayload)
    # El target del LLM debe haber sido ignorado; se usa el del contexto
    assert tarea.trigger_payload.target == "telegram:987654"
