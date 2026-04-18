"""E2E integration test: task con canal inbound → FileSink → TaskLog metadata.

Cubre el happy path completo del cambio channel-fallback-routing:
  1. Agendamos una task con target ``cli:local`` (canal sin gateway outbound).
  2. Ejecutamos el dispatch via SchedulerService + ChannelRouter real.
  3. El router cae en cascada al hardcoded FileSink (redirigido a tmp_path).
  4. El mensaje se escribe al archivo.
  5. SQLiteSchedulerRepo persiste TaskLog.metadata con (original, resolved).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import pytest

from adapters.outbound.scheduler.dispatch_adapters import (
    ChannelRouter,
    ConsolidationDispatchAdapter,
    HttpCallerAdapter,
    LLMDispatcherAdapter,
    SchedulerDispatchPorts,
)
from adapters.outbound.scheduler.sqlite_scheduler_repo import SQLiteSchedulerRepo
from adapters.outbound.sinks.sink_factory import SinkFactory
from core.domain.entities.task import (
    ChannelSendPayload,
    ScheduledTask,
    TaskKind,
    TaskStatus,
    TriggerType,
)
from core.domain.services.scheduler_service import SchedulerService
from infrastructure.config import ChannelFallbackConfig, SchedulerConfig


@pytest.fixture()
async def repo(tmp_path: Path) -> SQLiteSchedulerRepo:
    r = SQLiteSchedulerRepo(str(tmp_path / "sched.db"))
    await r.ensure_schema()
    return r


async def test_channel_send_cli_cae_en_hardcoded_file_y_persiste_metadata(
    tmp_path: Path,
    repo: SQLiteSchedulerRepo,
) -> None:
    # --- Arrange: router real con hardcoded redirigido a tmp_path ---
    destino_hardcoded = tmp_path / "hardcoded.log"
    factory = SinkFactory(get_telegram_bot=lambda: None)
    router = ChannelRouter(
        native_sinks={},  # Telegram no registrado: cli cae a cascada
        fallback_config=ChannelFallbackConfig(),  # sin default ni overrides
        sink_factory=factory.from_target,
        hardcoded_fallback=f"file://{destino_hardcoded}",
    )
    dispatch = SchedulerDispatchPorts(
        channel_sender=router,
        llm_dispatcher=LLMDispatcherAdapter({}),
        consolidator=ConsolidationDispatchAdapter(None),  # type: ignore[arg-type]
        http_caller=HttpCallerAdapter(),
    )
    config = SchedulerConfig(db_filename=str(tmp_path / "sched.db"))
    service = SchedulerService(repo=repo, dispatch=dispatch, config=config)

    task = await repo.save_task(
        ScheduledTask(
            name="e2e-cli-fallback",
            task_kind=TaskKind.ONESHOT,
            trigger_type=TriggerType.CHANNEL_SEND,
            trigger_payload=ChannelSendPayload(target="cli:local", text="recordatorio"),
            schedule="2025-12-01T10:00:00+00:00",
            next_run=datetime.now(timezone.utc),
            status=TaskStatus.PENDING,
        )
    )

    # --- Act ---
    await service._execute_task(task)

    # --- Assert: archivo escrito ---
    assert destino_hardcoded.exists()
    assert "recordatorio" in destino_hardcoded.read_text()

    # --- Assert: TaskLog persistido con metadata ---
    async with aiosqlite.connect(str(tmp_path / "sched.db")) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall(
            "SELECT status, metadata FROM task_logs WHERE task_id = ? AND status = 'success'",
            (task.id,),
        )
    assert len(rows) == 1
    metadata = json.loads(rows[0]["metadata"])
    assert metadata == {
        "original_target": "cli:local",
        "resolved_target": f"file://{destino_hardcoded}",
    }
