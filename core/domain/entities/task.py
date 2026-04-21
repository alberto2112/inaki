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
    WEBHOOK = "webhook"


class TaskStatus(str, Enum):
    """
    Runtime execution state of a task. Orthogonal to `ScheduledTask.enabled`,
    which expresses user intent (quiero que corra / no quiero). The scheduler
    loop filters by `enabled=1 AND status='pending'` — ambos se necesitan
    para que una tarea sea candidata a correr.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    MISSED = "missed"


# ---------------------------------------------------------------------------
# Trigger payloads — discriminated union
# ---------------------------------------------------------------------------


class ChannelSendPayload(BaseModel):
    type: Literal["channel_send"] = "channel_send"
    target: str
    text: str
    user_id: str | None = None


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


class WebhookPayload(BaseModel):
    type: Literal["webhook"] = "webhook"
    url: str
    method: str = "POST"
    headers: dict[str, str] = {}
    body: str | None = None
    timeout: int = 30
    success_codes: list[int] = [200, 201, 202, 204]


TriggerPayload = Annotated[
    ChannelSendPayload
    | AgentSendPayload
    | ShellExecPayload
    | ConsolidateMemoryPayload
    | WebhookPayload,
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
    schedule: str  # cron expr if recurrent, ISO datetime if oneshot
    enabled: bool = True
    executions_remaining: int | None = None  # recurrent only: None=infinite, N=countdown
    status: TaskStatus = TaskStatus.PENDING
    created_by: str = ""  # agent_id that created the task; "" = CLI/unknown origin
    retry_count: int = 0
    log_enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_run: datetime | None = None
    next_run: datetime | None = None
