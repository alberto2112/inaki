from __future__ import annotations

from datetime import datetime, timezone

from core.domain.entities.task import (
    CliCommandPayload,
    ScheduledTask,
    TaskKind,
    TaskStatus,
    TriggerType,
)

BUILTIN_CONSOLIDATE_MEMORY = ScheduledTask(
    id=1,
    name="consolidate_memory",
    description="Nightly memory consolidation",
    task_kind=TaskKind.RECURRENT,
    trigger_type=TriggerType.CLI_COMMAND,
    trigger_payload=CliCommandPayload(args=["--consolidate"], timeout=600),
    schedule="0 3 * * *",
    executions_remaining=None,
)
