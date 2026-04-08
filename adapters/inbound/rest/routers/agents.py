"""Router de la REST API — endpoints por instancia de agente."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

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
    cfg = container.run_agent._cfg
    return AgentInfo(id=cfg.id, name=cfg.name, description=cfg.description)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    container: AgentContainer = Depends(get_container),
) -> ChatResponse:
    cfg = container.run_agent._cfg
    try:
        response = await container.run_agent.execute(body.message)
    except Exception as exc:
        logger.exception("Error en /chat para agente '%s'", cfg.id)
        raise HTTPException(status_code=500, detail=str(exc))
    return ChatResponse(agent_id=cfg.id, agent_name=cfg.name, response=response)


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    container: AgentContainer = Depends(get_container),
) -> StreamingResponse:
    """
    Chat con streaming SSE. Devuelve tokens en tiempo real.
    Formato: `data: <token>\n\n`
    """
    cfg = container.run_agent._cfg

    async def event_generator():
        try:
            # Construir context para streaming
            query_vec = await container.run_agent._embedder.embed_query(body.message)
            history = await container.run_agent._history.load(cfg.id)
            memories = await container.run_agent._memory.search(query_vec)
            skills = await container.run_agent._skills.retrieve(query_vec)

            from core.domain.value_objects.agent_context import AgentContext
            from core.domain.entities.message import Message, Role

            context = AgentContext(agent_id=cfg.id, memories=memories, skills=skills)
            system_prompt = context.build_system_prompt(cfg.system_prompt)

            user_msg = Message(role=Role.USER, content=body.message)
            messages = history + [user_msg]

            full_response = []
            async for token in container.run_agent._llm.stream(messages, system_prompt):
                full_response.append(token)
                yield f"data: {token}\n\n"

            # Persistir en historial
            await container.run_agent._history.append(cfg.id, user_msg)
            await container.run_agent._history.append(
                cfg.id, Message(role=Role.ASSISTANT, content="".join(full_response))
            )
            yield "data: [DONE]\n\n"
        except Exception as exc:
            logger.exception("Error en /chat/stream para '%s'", cfg.id)
            yield f"data: [ERROR] {exc}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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
    cfg = container.run_agent._cfg
    messages = await container.run_agent._history.load(cfg.id)
    return HistoryResponse(
        agent_id=cfg.id,
        messages=[{"role": m.role.value, "content": m.content} for m in messages],
    )


@router.delete("/history")
async def delete_history(
    container: AgentContainer = Depends(get_container),
) -> dict:
    cfg = container.run_agent._cfg
    await container.run_agent._history.clear(cfg.id)
    return {"status": "ok", "message": "Historial eliminado"}
