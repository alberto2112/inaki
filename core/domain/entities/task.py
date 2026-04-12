from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class TaskKind(str, Enum):
    RECURRENT = "recurrent"
    ONESHOT = "oneshot"


class TriggerType(str, Enum):
    CHANNEL_SEND = "channel_send"
    AGENT_SEND = "agent_send"
    SHELL_EXEC = "shell_exec"
    CONSOLIDATE_MEMORY = "consolidate_memory"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    MISSED = "missed"
    DISABLED = "disabled"


# ---------------------------------------------------------------------------
# Trigger payloads — discriminated union
# ---------------------------------------------------------------------------

class ChannelSendPayload(BaseModel):
    type: Literal["channel_send"] = "channel_send"
    channel_id: str
    text: str


class AgentSendPayload(BaseModel):
    type: Literal["agent_send"] = "agent_send"
    agent_id: str
    task: str
    system: str | None = None
    tools_override: list[dict] | None = None
    output_channel: str | None = None


class ShellExecPayload(BaseModel):
    type: Literal["shell_exec"] = "shell_exec"
    command: str
    working_dir: str | None = None
    env_vars: dict[str, str] = {}
    timeout: int | None = None


class ConsolidateMemoryPayload(BaseModel):
    """
    Triggers the global memory consolidation across all enabled agents.

    No fields — the consolidator reads the agent registry at runtime and
    iterates every agent whose `memory.enabled` flag is true.
    """
    type: Literal["consolidate_memory"] = "consolidate_memory"


TriggerPayload = Annotated[
    ChannelSendPayload | AgentSendPayload | ShellExecPayload | ConsolidateMemoryPayload,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# ScheduledTask entity
# ---------------------------------------------------------------------------

class ScheduledTask(BaseModel):
    id: int = 0  # 0 = unassigned; repo assigns real id on save
    name: str
    description: str = ""
    task_kind: TaskKind
    trigger_type: TriggerType
    trigger_payload: TriggerPayload
    schedule: str                        # cron expr if recurrent, ISO datetime if oneshot
    enabled: bool = True
    executions_remaining: int | None = None  # recurrent only: None=infinite, N=countdown
    status: TaskStatus = TaskStatus.PENDING
    created_by: str = ""  # agent_id that created the task; "" = CLI/unknown origin
    retry_count: int = 0
    log_enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_run: datetime | None = None
    next_run: datetime | None = None
