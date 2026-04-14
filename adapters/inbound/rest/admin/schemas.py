"""Schemas del admin REST server."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"


class SchedulerReloadResponse(BaseModel):
    reloaded: bool = True


class InspectRequest(BaseModel):
    agent_id: str
    mensaje: str


class ConsolidateRequest(BaseModel):
    agent_id: str | None = None


class ConsolidateResponse(BaseModel):
    resultado: str
