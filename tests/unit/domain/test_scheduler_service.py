"""Unit tests for SchedulerService."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time

from core.domain.entities.task import (
    AgentSendPayload,
    ChannelSendPayload,
    ConsolidateMemoryPayload,
    ScheduledTask,
    TaskKind,
    TaskStatus,
    TriggerType,
    WebhookPayload,
)
from core.domain.entities.task_log import TaskLog
from core.domain.errors import TaskNotFoundError
from core.domain.services.scheduler_service import SchedulerService
from core.domain.value_objects.dispatch_result import DispatchResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_dispatch() -> MagicMock:
    dispatch = MagicMock()
    dispatch.channel_sender = AsyncMock()
    dispatch.llm_dispatcher = AsyncMock()
    dispatch.consolidator = AsyncMock()
    dispatch.consolidator.consolidate_all = AsyncMock(return_value="ok")
    dispatch.http_caller = AsyncMock()
    dispatch.http_caller.call = AsyncMock(return_value="webhook response")
    return dispatch


def _make_task(
    task_id: int = 100,
    task_kind: TaskKind = TaskKind.ONESHOT,
    next_run: datetime | None = None,
    executions_remaining: int | None = None,
) -> ScheduledTask:
    return ScheduledTask(
        id=task_id,
        name="test",
        task_kind=task_kind,
        trigger_type=TriggerType.CONSOLIDATE_MEMORY,
        trigger_payload=ConsolidateMemoryPayload(),
        schedule="0 3 * * *",
        next_run=next_run,
        executions_remaining=executions_remaining,
        status=TaskStatus.PENDING,
    )


@pytest.fixture()
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.list_due_pending.return_value = []
    repo.list_running.return_value = []
    repo.get_next_due.return_value = None
    return repo


@pytest.fixture()
def service(mock_repo: AsyncMock) -> SchedulerService:
    return SchedulerService(
        repo=mock_repo,
        dispatch=_make_dispatch(),
        max_retries=1,
        output_truncation_size=65536,
        retry_backoff_seconds=0,  # sin sleeps en unit tests
    )


# ---------------------------------------------------------------------------
# _recover_on_startup
# ---------------------------------------------------------------------------


@freeze_time("2025-06-01 12:00:00")
async def test_recover_marks_missed_oneshot_as_missed(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    task = _make_task(
        task_id=100,
        task_kind=TaskKind.ONESHOT,
        next_run=datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    mock_repo.list_due_pending.return_value = [task]

    await service._recover_on_startup()

    mock_repo.update_status.assert_awaited_once_with(100, TaskStatus.MISSED)
    mock_repo.save_log.assert_awaited_once()
    log_arg: TaskLog = mock_repo.save_log.call_args[0][0]
    assert log_arg.status == "missed"
    assert log_arg.task_id == 100


@freeze_time("2025-06-01 12:00:00")
async def test_recover_recomputes_recurrent_next_run_sin_tocar_last_run(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    task = _make_task(
        task_id=101,
        task_kind=TaskKind.RECURRENT,
        next_run=datetime(2025, 6, 1, 3, 0, 0, tzinfo=timezone.utc),
    )
    task = task.model_copy(update={"schedule": "0 3 * * *"})
    mock_repo.list_due_pending.return_value = [task]

    await service._recover_on_startup()

    mock_repo.update_after_execution.assert_awaited_once()
    call_kwargs = mock_repo.update_after_execution.call_args.kwargs
    # next_run should be future (tomorrow 03:00 UTC)
    assert call_kwargs["next_run"] > datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    # No hubo ejecución real → last_run no se escribe
    assert call_kwargs["last_run"] is None
    # La ocurrencia salteada deja rastro en task_logs
    log_arg: TaskLog = mock_repo.save_log.call_args[0][0]
    assert log_arg.status == "missed"
    assert log_arg.task_id == 101


@freeze_time("2025-06-01 12:00:00")
async def test_recover_oneshot_atrapado_en_running_pasa_a_failed(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    """Un oneshot RUNNING al arrancar (daemon murió a mitad) → FAILED + log."""
    task = _make_task(task_id=102, task_kind=TaskKind.ONESHOT)
    task = task.model_copy(update={"status": TaskStatus.RUNNING})
    mock_repo.list_running.return_value = [task]

    await service._recover_on_startup()

    mock_repo.update_status.assert_awaited_once_with(102, TaskStatus.FAILED)
    log_arg: TaskLog = mock_repo.save_log.call_args[0][0]
    assert log_arg.status == "failed"
    assert "restarted" in (log_arg.error or "")


@freeze_time("2025-06-01 12:00:00")
async def test_recover_recurrente_atrapada_en_running_avanza_al_proximo_slot(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    """Una recurrente RUNNING al arrancar → vuelve a pending con next_run futuro."""
    task = _make_task(task_id=103, task_kind=TaskKind.RECURRENT)
    task = task.model_copy(update={"status": TaskStatus.RUNNING})
    mock_repo.list_running.return_value = [task]

    await service._recover_on_startup()

    mock_repo.update_after_execution.assert_awaited_once()
    call_kwargs = mock_repo.update_after_execution.call_args.kwargs
    assert call_kwargs["next_run"] > datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert call_kwargs["retry_count"] == 0
    assert call_kwargs["last_run"] is None


# ---------------------------------------------------------------------------
# _execute_task retries
# ---------------------------------------------------------------------------


async def test_execute_task_retries_max_retries_then_failed(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    task = _make_task()
    # Make _dispatch_trigger raise every time
    service._dispatch_trigger = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    await service._execute_task(task)

    # max_retries=1 means 2 attempts total (attempt 0 + attempt 1)
    assert service._dispatch_trigger.call_count == 2  # type: ignore[attr-defined]
    # retry_count is persisted after each failure and on final FAILED status
    calls = mock_repo.update_status.call_args_list
    statuses = [(c.args[1], c.kwargs.get("retry_count")) for c in calls]
    assert (TaskStatus.RUNNING, None) in statuses
    assert (TaskStatus.FAILED, 2) in statuses


async def test_execute_task_on_success_calls_finalize(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    task = _make_task()
    service._dispatch_trigger = AsyncMock(return_value=("output", None))  # type: ignore[method-assign]
    service._finalize_task = AsyncMock()  # type: ignore[method-assign]

    await service._execute_task(task)

    service._finalize_task.assert_awaited_once()  # type: ignore[attr-defined]
    args = service._finalize_task.await_args.args  # type: ignore[attr-defined, union-attr]
    assert args[0] is task
    assert args[1] == "output"
    assert args[2] is None
    assert isinstance(args[3], datetime)  # run_started_at real


# ---------------------------------------------------------------------------
# _finalize_task oneshot → COMPLETED
# ---------------------------------------------------------------------------


async def test_finalize_oneshot_sets_completed(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    task = _make_task(task_kind=TaskKind.ONESHOT)
    await service._finalize_task(task, None)
    mock_repo.update_status.assert_awaited_once_with(task.id, TaskStatus.COMPLETED)


# ---------------------------------------------------------------------------
# Timezone: el recompute post-ejecución usa la tz del usuario, no UTC
# ---------------------------------------------------------------------------


@freeze_time("2025-07-01 04:00:00")  # = 06:00 en Madrid (CEST, UTC+2)
async def test_finalize_recurrent_recomputa_cron_en_timezone_usuario(
    mock_repo: AsyncMock,
) -> None:
    """Regresión del bug de doble ejecución: una task `0 6 * * *` con
    user_timezone=Europe/Madrid que corre a las 06:00 locales (04:00 UTC)
    debe reprogramarse para MAÑANA 06:00 locales (04:00 UTC) — NO para hoy
    06:00 UTC (08:00 locales), que era el drift que causaba dos ejecuciones
    separadas por 2h."""
    service = SchedulerService(
        repo=mock_repo,
        dispatch=_make_dispatch(),
        user_timezone="Europe/Madrid",
        retry_backoff_seconds=0,
    )
    task = _make_task(task_id=110, task_kind=TaskKind.RECURRENT)
    task = task.model_copy(update={"schedule": "0 6 * * *"})

    await service._finalize_task(task, None)

    call_kwargs = mock_repo.update_after_execution.call_args.kwargs
    assert call_kwargs["next_run"] == datetime(2025, 7, 2, 4, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Recurrente que agota retries → avanza al próximo slot, no muere
# ---------------------------------------------------------------------------


@freeze_time("2025-06-01 12:00:00")
async def test_execute_recurrent_fallida_avanza_sin_morir(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    task = _make_task(task_id=120, task_kind=TaskKind.RECURRENT)
    service._dispatch_trigger = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    await service._execute_task(task)

    # Nunca pasa a FAILED — la recurrencia sobrevive
    statuses = [c.args[1] for c in mock_repo.update_status.call_args_list]
    assert TaskStatus.FAILED not in statuses
    # Avanza al próximo slot con retry_count reseteado y sin tocar last_run
    call_kwargs = mock_repo.update_after_execution.call_args.kwargs
    assert call_kwargs["next_run"] > datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert call_kwargs["retry_count"] == 0
    assert call_kwargs["last_run"] is None
    # Los intentos fallidos quedaron logueados
    failed_logs = [
        c.args[0] for c in mock_repo.save_log.call_args_list if c.args[0].status == "failed"
    ]
    assert len(failed_logs) == 2  # max_retries=1 → 2 intentos


# ---------------------------------------------------------------------------
# invalidate sets wake event
# ---------------------------------------------------------------------------


def test_invalidate_sets_wake_event(service: SchedulerService) -> None:
    assert not service._wake.is_set()
    service.invalidate()
    assert service._wake.is_set()


# ---------------------------------------------------------------------------
# _dispatch_trigger webhook
# ---------------------------------------------------------------------------


def _make_webhook_task(url: str = "https://example.com/hook") -> ScheduledTask:
    return ScheduledTask(
        id=200,
        name="webhook-test",
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.WEBHOOK,
        trigger_payload=WebhookPayload(url=url),
        schedule="2025-12-01T10:00:00+00:00",
        status=TaskStatus.PENDING,
    )


async def test_dispatch_webhook_calls_http_caller(
    service: SchedulerService,
) -> None:
    """_dispatch_trigger calls http_caller.call() with the correct WebhookPayload."""
    task = _make_webhook_task()
    result = await service._dispatch_trigger(task)

    service._dispatch.http_caller.call.assert_awaited_once_with(task.trigger_payload)  # type: ignore[attr-defined]
    # _dispatch_trigger ahora devuelve (output, metadata)
    assert result == ("webhook response", None)


async def test_dispatch_webhook_return_value_propagated_to_finalize(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    """Return value from http_caller.call() reaches _finalize_task as output."""
    task = _make_webhook_task()
    service._finalize_task = AsyncMock()  # type: ignore[method-assign]

    await service._execute_task(task)

    service._finalize_task.assert_awaited_once()  # type: ignore[attr-defined]
    args = service._finalize_task.await_args.args  # type: ignore[attr-defined, union-attr]
    assert args[0] is task
    assert args[1] == "webhook response"


async def test_dispatch_webhook_failure_propagates_as_execute_failure(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    """RuntimeError from http_caller leads to FAILED status after retries."""
    task = _make_webhook_task()
    service._dispatch.http_caller.call = AsyncMock(side_effect=RuntimeError("500 error"))  # type: ignore[method-assign]

    await service._execute_task(task)

    calls = mock_repo.update_status.call_args_list
    final_statuses = [c.args[1] for c in calls]
    assert TaskStatus.FAILED in final_statuses


# ---------------------------------------------------------------------------
# Metadata propagation: DispatchResult → TaskLog.metadata  (tarea 4.7)
# ---------------------------------------------------------------------------


def _make_channel_task() -> ScheduledTask:
    return ScheduledTask(
        id=300,
        name="channel-test",
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.CHANNEL_SEND,
        trigger_payload=ChannelSendPayload(target="cli:local", text="hola"),
        schedule="2025-12-01T10:00:00+00:00",
        status=TaskStatus.PENDING,
    )


async def test_channel_send_propaga_metadata_a_tasklog(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    """Tras un channel_send, el TaskLog persistido por _finalize_task debe
    contener metadata {original_target, resolved_target}."""
    task = _make_channel_task()
    service._dispatch.channel_sender.send_message = AsyncMock(  # type: ignore[method-assign]
        return_value=DispatchResult(
            original_target="cli:local",
            resolved_target="file:///tmp/inaki-schedule-output.log",
        )
    )

    await service._execute_task(task)

    # Buscar la llamada save_log con status="success"
    success_logs = [
        c.args[0]
        for c in mock_repo.save_log.call_args_list
        if isinstance(c.args[0], TaskLog) and c.args[0].status == "success"
    ]
    assert len(success_logs) == 1
    log = success_logs[0]
    assert log.metadata == {
        "original_target": "cli:local",
        "resolved_target": "file:///tmp/inaki-schedule-output.log",
    }


async def test_dispatch_trigger_channel_send_devuelve_metadata(
    service: SchedulerService,
) -> None:
    task = _make_channel_task()
    service._dispatch.channel_sender.send_message = AsyncMock(  # type: ignore[method-assign]
        return_value=DispatchResult(original_target="cli:local", resolved_target="null:")
    )

    output, metadata = await service._dispatch_trigger(task)

    assert output is None
    assert metadata == {"original_target": "cli:local", "resolved_target": "null:"}


async def test_dispatch_trigger_consolidate_devuelve_metadata_none(
    service: SchedulerService,
) -> None:
    """Payloads que no son channel_send no tienen metadata de routing."""
    task = _make_task()  # ConsolidateMemoryPayload

    output, metadata = await service._dispatch_trigger(task)

    assert output == "ok"
    assert metadata is None


# ---------------------------------------------------------------------------
# AgentSendPayload — sink intermedio via output_channel
# ---------------------------------------------------------------------------


def _make_agent_task(output_channel: str | None) -> ScheduledTask:
    return ScheduledTask(
        id=400,
        name="agent-test",
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.AGENT_SEND,
        trigger_payload=AgentSendPayload(
            agent_id="dev",
            task="haceme un resumen",
            tools_override=None,
            output_channel=output_channel,
        ),
        schedule="2025-12-01T10:00:00+00:00",
        status=TaskStatus.PENDING,
    )


async def test_agent_send_con_output_channel_pasa_sink_construido_al_dispatcher(
    service: SchedulerService,
) -> None:
    """Con output_channel: ChannelRouter.build_intermediate_sink se invoca y el
    sink resultante llega al llm_dispatcher como ``intermediate_sink=``."""
    task = _make_agent_task(output_channel="telegram:7")
    sentinel_sink = object()
    service._dispatch.channel_sender.build_intermediate_sink = MagicMock(return_value=sentinel_sink)  # type: ignore[method-assign]
    service._dispatch.channel_sender.send_message = AsyncMock(  # type: ignore[method-assign]
        return_value=DispatchResult(original_target="telegram:7", resolved_target="telegram:7")
    )
    service._dispatch.llm_dispatcher.dispatch = AsyncMock(return_value="reply final")  # type: ignore[method-assign]

    await service._dispatch_trigger(task)

    service._dispatch.channel_sender.build_intermediate_sink.assert_called_once_with("telegram:7")
    service._dispatch.llm_dispatcher.dispatch.assert_awaited_once()
    call_kwargs = service._dispatch.llm_dispatcher.dispatch.await_args.kwargs  # type: ignore[union-attr]
    assert call_kwargs["intermediate_sink"] is sentinel_sink


async def test_agent_send_sin_output_channel_no_construye_sink(
    service: SchedulerService,
) -> None:
    """Sin output_channel: no hay sink — el dispatcher recibe None."""
    task = _make_agent_task(output_channel=None)
    service._dispatch.channel_sender.build_intermediate_sink = MagicMock()  # type: ignore[method-assign]
    service._dispatch.llm_dispatcher.dispatch = AsyncMock(return_value="reply")  # type: ignore[method-assign]

    await service._dispatch_trigger(task)

    service._dispatch.channel_sender.build_intermediate_sink.assert_not_called()
    call_kwargs = service._dispatch.llm_dispatcher.dispatch.await_args.kwargs  # type: ignore[union-attr]
    assert call_kwargs["intermediate_sink"] is None


# ---------------------------------------------------------------------------
# run_task_now — disparo manual on-demand, NO destructivo
# ---------------------------------------------------------------------------


async def test_run_task_now_task_inexistente_levanta_not_found(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    mock_repo.get_task.return_value = None

    with pytest.raises(TaskNotFoundError):
        await service.run_task_now(999)


async def test_run_task_now_no_toca_estado_de_scheduling_oneshot(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    """LA garantía clave: un oneshot disparado a mano NO pasa a COMPLETED ni se
    consume — status / next_run / executions_remaining quedan intactos."""
    task = _make_task(task_id=500, task_kind=TaskKind.ONESHOT)
    mock_repo.get_task.return_value = task

    result = await service.run_task_now(500)

    assert result.success is True
    assert result.output == "ok"  # ConsolidateMemoryPayload → consolidate_all
    # NADA que mute la agenda de la tarea
    mock_repo.update_status.assert_not_awaited()
    mock_repo.update_after_execution.assert_not_awaited()
    mock_repo.update_enabled.assert_not_awaited()
    mock_repo.save_task.assert_not_awaited()


async def test_run_task_now_no_toca_estado_de_scheduling_recurrente(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    """Una recurrente disparada a mano NO avanza next_run ni decrementa
    executions_remaining."""
    task = _make_task(task_id=501, task_kind=TaskKind.RECURRENT, executions_remaining=3)
    mock_repo.get_task.return_value = task

    await service.run_task_now(501)

    mock_repo.update_after_execution.assert_not_awaited()
    mock_repo.update_status.assert_not_awaited()


async def test_run_task_now_loguea_marcador_manual(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    """La corrida manual deja un TaskLog con metadata {'trigger': 'manual'}."""
    task = _make_channel_task()
    mock_repo.get_task.return_value = task
    service._dispatch.channel_sender.send_message = AsyncMock(  # type: ignore[method-assign]
        return_value=DispatchResult(original_target="cli:local", resolved_target="null:")
    )

    await service.run_task_now(task.id)

    mock_repo.save_log.assert_awaited_once()
    log: TaskLog = mock_repo.save_log.call_args[0][0]
    assert log.status == "success"
    assert log.task_id == task.id
    # Mergea la metadata del dispatch con el marcador de origen manual
    assert log.metadata == {
        "original_target": "cli:local",
        "resolved_target": "null:",
        "trigger": "manual",
    }


async def test_run_task_now_falla_un_solo_intento_sin_reintentos(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    """A diferencia de _execute_task, el run manual NO reintenta: un solo
    dispatch, y el fallo se reporta en el resultado (no rompe la agenda)."""
    task = _make_task(task_id=502, task_kind=TaskKind.ONESHOT)
    mock_repo.get_task.return_value = task
    service._dispatch_trigger = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    result = await service.run_task_now(502)

    assert result.success is False
    assert result.error == "boom"
    service._dispatch_trigger.assert_awaited_once()  # type: ignore[attr-defined]
    # Falló pero NO marca FAILED ni toca la agenda
    mock_repo.update_status.assert_not_awaited()
    mock_repo.update_after_execution.assert_not_awaited()
    # Deja el log de fallo marcado como manual
    log: TaskLog = mock_repo.save_log.call_args[0][0]
    assert log.status == "failed"
    assert log.metadata == {"trigger": "manual"}


async def test_run_task_now_respeta_log_disabled(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    """Con log_enabled=False no se persiste TaskLog (igual que el loop normal)."""
    task = _make_task(task_id=503, task_kind=TaskKind.ONESHOT).model_copy(
        update={"log_enabled": False}
    )
    mock_repo.get_task.return_value = task

    result = await service.run_task_now(503)

    assert result.success is True
    mock_repo.save_log.assert_not_awaited()
