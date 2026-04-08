from datetime import datetime
from enum import Enum
import uuid
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TaskType(str, Enum):
    CRON = "cron"
    ONCE = "once"


class ScheduledTask(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    name: str
    description: str
    task_type: TaskType
    schedule: str                     # cron expression o ISO datetime
    prompt: str                       # Prompt a ejecutar cuando dispare
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_run: datetime | None = None
    next_run: datetime | None = None
