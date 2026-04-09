from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TaskLog(BaseModel):
    id: int = 0
    task_id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str   # "success" | "failed" | "missed"
    output: str | None = None
    error: str | None = None
