"""Router de la REST API — endpoints por instancia de agente."""
# TODO: implementar handler de channel_send para REST (set_channel_context + dispatch handler)

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from adapters.inbound.rest.schemas import (
    AgentInfo,
    ChatRequest,
    ChatResponse,
    ConsolidateResponse,
    HistoryResponse,
)
from infrastructure.container import AgentContainer

logger = logging.getLogger(__name__)

router = APIRouter()


def get_container(request: Request) -> AgentContainer:
    """Obtiene el AgentContainer inyectado en el state de la app."""
    return request.app.state.container


@router.get("/info", response_model=AgentInfo)
async def get_info(container: AgentContainer = Depends(get_container)) -> AgentInfo:
    info = container.run_agent.get_agent_info()
    return AgentInfo(id=info.id, name=info.name, description=info.description)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    container: AgentContainer = Depends(get_container),
) -> ChatResponse:
    info = container.run_agent.get_agent_info()
    try:
        response = await container.run_agent.execute(body.message)
    except Exception as exc:
        logger.exception("Error en /chat para agente '%s'", info.id)
        raise HTTPException(status_code=500, detail=str(exc))
    return ChatResponse(agent_id=info.id, agent_name=info.name, response=response)


@router.post("/consolidate", response_model=ConsolidateResponse)
async def consolidate(
    container: AgentContainer = Depends(get_container),
) -> ConsolidateResponse:
    try:
        result = await container.consolidate_memory.execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return ConsolidateResponse(result=result)


@router.get("/history", response_model=HistoryResponse)
async def get_history(
    container: AgentContainer = Depends(get_container),
) -> HistoryResponse:
    info = container.run_agent.get_agent_info()
    messages = await container.run_agent.get_history()
    return HistoryResponse(
        agent_id=info.id,
        messages=[{"role": m.role.value, "content": m.content} for m in messages],
    )


@router.delete("/history")
async def delete_history(
    container: AgentContainer = Depends(get_container),
) -> dict:
    await container.run_agent.clear_history()
    return {"status": "ok", "message": "Historial eliminado"}
