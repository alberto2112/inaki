"""Router del admin REST server — endpoints globales del daemon."""

from __future__ import annotations

import dataclasses
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from adapters.inbound.rest.admin.routers.deps import check_admin_auth, resolver_agente
from adapters.inbound.rest.admin.schemas import (
    AgentInfoResponse,
    AgentsResponse,
    ConsolidateRequest,
    ConsolidateResponse,
    DaemonReloadResponse,
    HealthResponse,
    InspectRequest,
    SchedulerReloadResponse,
    SchedulerRunRequest,
    SchedulerRunResponse,
)
from core.domain.errors import TaskNotFoundError

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


@router.get(
    "/admin/agent/info",
    response_model=AgentInfoResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def agent_info(agent_id: str, request: Request) -> AgentInfoResponse:
    """Metadata de un agente (id, name, description).

    Porteado del ``GET /info`` de la superficie REST per-agente eliminada.
    """
    agent_container = resolver_agente(request, agent_id)
    info = agent_container.run_agent.get_agent_info()
    return AgentInfoResponse(id=info.id, name=info.name, description=info.description)


@router.post(
    "/scheduler/reload",
    response_model=SchedulerReloadResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def scheduler_reload(request: Request) -> SchedulerReloadResponse:
    scheduler_service = request.app.state.app_container.scheduler_service
    await scheduler_service.invalidate()
    return SchedulerReloadResponse()


@router.post(
    "/scheduler/run",
    response_model=SchedulerRunResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def scheduler_run(body: SchedulerRunRequest, request: Request) -> SchedulerRunResponse:
    """Dispara una tarea on-demand, fuera de su agenda (NO destructivo).

    Pensado para testear: ejecuta el trigger una vez sin tocar el estado de
    scheduling (status/next_run/executions_remaining). 404 si la tarea no existe;
    ``success=False`` en el body si el trigger ejecutó pero falló.
    """
    scheduler_service = request.app.state.app_container.scheduler_service
    try:
        result = await scheduler_service.run_task_now(body.task_id)
    except TaskNotFoundError:
        raise HTTPException(status_code=404, detail=f"Task {body.task_id} not found")
    return SchedulerRunResponse(
        task_id=result.task_id,
        success=result.success,
        output=result.output,
        error=result.error,
    )


@router.post(
    "/admin/reload",
    response_model=DaemonReloadResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def daemon_reload(request: Request) -> DaemonReloadResponse:
    """Reinicia el daemon in-place: cierra todos los canales, recarga config y vuelve a levantar.

    Endpoint asíncrono — devuelve 200 inmediatamente y el reload ocurre en background.
    El cliente HTTP que llama (CLI, Telegram bot) puede perder la conexión cuando el
    admin server se reinicie; eso es esperado.
    """
    reloader = request.app.state.app_container.reloader
    reloader.request_reload()
    logger.info("Reload solicitado vía POST /admin/reload")
    return DaemonReloadResponse()


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
    """Consolida la memoria de un agente o de todos.

    Con ``agent_id`` en el body consolida SOLO ese agente (404 si no existe,
    503 si tiene ``memory.enabled=false``). Sin body o sin ``agent_id``,
    consolida todos los agentes (comportamiento original).
    """
    if body is not None and body.agent_id is not None:
        agent_container = resolver_agente(request, body.agent_id)
        if agent_container.consolidate_memory is None:
            # memory.enabled=false es una config esperada, no un error interno
            # → 503 (Service Unavailable), igual que la superficie per-agente.
            raise HTTPException(
                status_code=503,
                detail=(
                    f"El agente '{body.agent_id}' no tiene 'memory.enabled=true' "
                    "en su config — consolidate no está disponible."
                ),
            )
        resultado = await agent_container.consolidate_memory.execute()
        return ConsolidateResponse(resultado=resultado)

    app_container = request.app.state.app_container
    resultado = await app_container.consolidate_all_agents.execute()
    return ConsolidateResponse(resultado=resultado)
