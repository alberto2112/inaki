from __future__ import annotations

from pydantic import BaseModel


class DelegationResult(BaseModel):
    status: str
    summary: str
    details: str | None = None
    reason: str | None = None
