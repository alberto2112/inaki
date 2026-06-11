"""Router de la REST API — endpoints por instancia de agente."""

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
from adapters.inbound.turn_dispatch import dispatch_inbound_turn
from core.domain.value_objects.channel_context import ChannelContext
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

    channel = body.channel if body.channel is not None else ""
    chat_id = body.chat_id if body.chat_id is not None else ""
    channel_type = body.channel if body.channel is not None else "rest"

    ctx = ChannelContext(
        channel_type=channel_type,
        user_id="anonymous",
        chat_id=chat_id or None,
    )

    scope = (info.id, channel, chat_id)
    try:
        result = await dispatch_inbound_turn(
            scope_registry=container.scope_registry,
            run_agent=container.run_agent,
            scope=scope,
            message=body.message,
            execute=lambda: container.run_agent.execute(
                body.message, ctx=ctx, channel=channel, chat_id=chat_id
            ),
        )
    except Exception as exc:
        logger.exception("Error en /chat para agente '%s'", info.id)
        raise HTTPException(status_code=500, detail=str(exc))
    return ChatResponse(agent_id=info.id, agent_name=info.name, response=result.reply)


@router.post("/consolidate", response_model=ConsolidateResponse)
async def consolidate(
    container: AgentContainer = Depends(get_container),
) -> ConsolidateResponse:
    if container.consolidate_memory is None:
        # consolidate_memory es None cuando memory.enabled=false en el agent
        # config. Devolvemos 503 (Service Unavailable) en vez de 500 porque
        # es una config esperada, no un error interno.
        raise HTTPException(
            status_code=503,
            detail=(
                "El agente no tiene 'memory.enabled=true' en su config — "
                "consolidate no está disponible."
            ),
        )
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
