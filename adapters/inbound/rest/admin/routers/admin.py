"""Router del admin REST server — endpoints globales del daemon."""

from __future__ import annotations

import dataclasses
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from adapters.inbound.rest.admin.routers.deps import check_admin_auth
from adapters.inbound.rest.admin.schemas import (
    AgentsResponse,
    ConsolidateRequest,
    ConsolidateResponse,
    HealthResponse,
    InspectRequest,
    SchedulerReloadResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get(
    "/admin/agents",
    response_model=AgentsResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def list_agents(request: Request) -> AgentsResponse:
    """Lista los agentes registrados en el daemon.

    Retorna la lista de IDs de agentes disponibles para chat y otras operaciones.
    """
    app_container = request.app.state.app_container
    return AgentsResponse(agents=list(app_container.agents.keys()))


@router.post(
    "/scheduler/reload",
    response_model=SchedulerReloadResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def scheduler_reload(request: Request) -> SchedulerReloadResponse:
    scheduler_service = request.app.state.app_container.scheduler_service
    await scheduler_service.invalidate()
    return SchedulerReloadResponse()


@router.post("/inspect", dependencies=[Depends(check_admin_auth)])
async def inspect_endpoint(body: InspectRequest, request: Request) -> dict:
    app_container = request.app.state.app_container
    agents = app_container.agents
    if body.agent_id not in agents:
        raise HTTPException(
            status_code=404,
            detail=f"Agente '{body.agent_id}' no encontrado. Disponibles: {list(agents.keys())}",
        )
    agent_container = agents[body.agent_id]
    resultado = await agent_container.run_agent.inspect(body.mensaje)
    return dataclasses.asdict(resultado)


@router.post(
    "/consolidate",
    response_model=ConsolidateResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def consolidate_endpoint(
    request: Request,
    body: ConsolidateRequest | None = None,
) -> ConsolidateResponse:
    app_container = request.app.state.app_container
    resultado = await app_container.consolidate_all_agents.execute()
    return ConsolidateResponse(resultado=resultado)
