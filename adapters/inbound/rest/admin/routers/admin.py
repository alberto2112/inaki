"""Router del admin REST server — endpoints globales del daemon."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from adapters.inbound.rest.admin.schemas import (
    ConsolidateRequest,
    ConsolidateResponse,
    HealthResponse,
    InspectRequest,
    SchedulerReloadResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


def _check_admin_auth(request: Request) -> None:
    """Verifica X-Admin-Key contra la key configurada.

    - auth_key is None → 403 (fail-closed: sin key configurada, no se permite acceso)
    - Header ausente o incorrecto → 401
    """
    auth_key: str | None = request.app.state.admin_auth_key
    if auth_key is None:
        raise HTTPException(
            status_code=403,
            detail="Admin auth_key no configurada. Agregala en global.secrets.yaml.",
        )
    provided = request.headers.get("X-Admin-Key")
    if not provided or provided != auth_key:
        raise HTTPException(status_code=401, detail="X-Admin-Key inválida o ausente")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.post(
    "/scheduler/reload",
    response_model=SchedulerReloadResponse,
    dependencies=[Depends(_check_admin_auth)],
)
async def scheduler_reload(request: Request) -> SchedulerReloadResponse:
    scheduler_service = request.app.state.app_container.scheduler_service
    await scheduler_service.invalidate()
    return SchedulerReloadResponse()


@router.post("/inspect", dependencies=[Depends(_check_admin_auth)])
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
    return resultado


@router.post(
    "/consolidate",
    response_model=ConsolidateResponse,
    dependencies=[Depends(_check_admin_auth)],
)
async def consolidate_endpoint(
    request: Request,
    body: ConsolidateRequest | None = None,
) -> ConsolidateResponse:
    app_container = request.app.state.app_container
    resultado = await app_container.consolidate_all_agents.execute()
    return ConsolidateResponse(resultado=resultado)
