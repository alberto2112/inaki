"""Router de chat admin — endpoints de conversación turn-based via REST.

Expone cuatro endpoints bajo /admin/chat/*:
  POST   /admin/chat/turn     — envía un turno de conversación
  POST   /admin/chat/task     — oneshot ephemeral (carga historial, no persiste)
  GET    /admin/chat/history  — obtiene el historial de un agente
  DELETE /admin/chat/history  — limpia el historial de un agente

Todos requieren X-Admin-Key (via check_admin_auth).
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from adapters.inbound.rest.admin.routers.deps import check_admin_auth
from adapters.inbound.rest.admin.schemas import (
    ChatTurnRequest,
    ChatTurnResponse,
    HistoryMessage,
    HistoryResponse,
    TaskTurnRequest,
    TaskTurnResponse,
)
from adapters.outbound.intermediate_sinks.buffering import BufferingIntermediateSink
from core.domain.value_objects.channel_context import ChannelContext

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolver_agente(request: Request, agent_id: str) -> object:
    """Resuelve el AgentContainer para el agent_id dado o levanta 404."""
    app_container = request.app.state.app_container
    if agent_id not in app_container.agents:
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Agente '{agent_id}' no encontrado",
                "error_code": "agent_not_found",
                "disponibles": list(app_container.agents.keys()),
            },
        )
    return app_container.agents[agent_id]


# ---------------------------------------------------------------------------
# POST /admin/chat/turn
# ---------------------------------------------------------------------------


@router.post(
    "/turn",
    response_model=ChatTurnResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def chat_turn(body: ChatTurnRequest, request: Request) -> ChatTurnResponse:
    """Envía un turno de conversación al agente y retorna la respuesta del asistente.

    Flujo (Design §A3):
      1. Validar auth (via Depends)
      2. Resolver AgentContainer o 404
      3. Construir ChannelContext("cli", session_id)
      4. set_channel_context → execute → set_channel_context(None) via try/finally
      5. Retornar ChatTurnResponse
    """
    t0 = time.monotonic()
    logger.info(
        "chat_turn agent=%s session=%s msg_len=%d",
        body.agent_id,
        body.session_id,
        len(body.message),
    )

    agent_container = _resolver_agente(request, body.agent_id)
    ctx = ChannelContext(channel_type="cli", user_id=body.session_id)
    sink = BufferingIntermediateSink()

    try:
        agent_container.set_channel_context(ctx)
        reply = await agent_container.run_agent.execute(body.message, intermediate_sink=sink)
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "chat_turn error agent=%s session=%s duration_ms=%d",
            body.agent_id,
            body.session_id,
            duration_ms,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": str(exc),
                "error_code": "internal_error",
            },
        ) from exc
    finally:
        agent_container.set_channel_context(None)

    duration_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "chat_turn done agent=%s session=%s duration_ms=%d reply_len=%d",
        body.agent_id,
        body.session_id,
        duration_ms,
        len(reply),
    )

    return ChatTurnResponse(
        reply=reply,
        agent_id=body.agent_id,
        session_id=body.session_id,
        intermediates=sink.messages,
    )


# ---------------------------------------------------------------------------
# POST /admin/chat/task
# ---------------------------------------------------------------------------


@router.post(
    "/task",
    response_model=TaskTurnResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def chat_task(body: TaskTurnRequest, request: Request) -> TaskTurnResponse:
    """Ejecuta una tarea oneshot: carga historial para contexto pero NO persiste el turno.

    Equivalente a chat/turn con ephemeral=True — el agente ve el historial
    previo pero el turno no queda registrado ni actualiza el estado sticky.
    """
    t0 = time.monotonic()
    logger.info("chat_task agent=%s msg_len=%d", body.agent_id, len(body.message))

    agent_container = _resolver_agente(request, body.agent_id)
    sink = BufferingIntermediateSink()

    try:
        reply = await agent_container.run_agent.execute(
            body.message, intermediate_sink=sink, ephemeral=True
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "chat_task error agent=%s duration_ms=%d",
            body.agent_id,
            duration_ms,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), "error_code": "internal_error"},
        ) from exc

    duration_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "chat_task done agent=%s duration_ms=%d reply_len=%d",
        body.agent_id,
        duration_ms,
        len(reply),
    )

    return TaskTurnResponse(reply=reply, agent_id=body.agent_id, intermediates=sink.messages)


# ---------------------------------------------------------------------------
# GET /admin/chat/history
# ---------------------------------------------------------------------------


@router.get(
    "/history",
    response_model=HistoryResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def get_history(agent_id: str, request: Request) -> HistoryResponse:
    """Retorna el historial activo del agente en orden cronológico.

    Flujo (Design §A4):
      1. Validar auth (via Depends)
      2. Resolver AgentContainer o 404
      3. Llamar run_agent.get_history()
      4. Mapear a list[HistoryMessage]
      5. Retornar HistoryResponse
    """
    t0 = time.monotonic()
    logger.info("get_history agent=%s", agent_id)

    agent_container = _resolver_agente(request, agent_id)
    mensajes = await agent_container.run_agent.get_history()

    duration_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "get_history done agent=%s duration_ms=%d count=%d",
        agent_id,
        duration_ms,
        len(mensajes),
    )

    return HistoryResponse(
        agent_id=agent_id,
        messages=[
            HistoryMessage(role=msg.role.value, content=msg.content, timestamp=msg.timestamp)
            for msg in mensajes
        ],
    )


# ---------------------------------------------------------------------------
# DELETE /admin/chat/history
# ---------------------------------------------------------------------------


@router.delete(
    "/history",
    status_code=204,
    dependencies=[Depends(check_admin_auth)],
)
async def clear_history(agent_id: str, request: Request) -> Response:
    """Limpia el historial activo del agente (afecta a todos los canales).

    Flujo (Design §A5):
      1. Validar auth (via Depends)
      2. Resolver AgentContainer o 404
      3. Llamar run_agent.clear_history()
      4. Retornar 204 No Content
    """
    t0 = time.monotonic()
    logger.info("clear_history agent=%s", agent_id)

    agent_container = _resolver_agente(request, agent_id)
    await agent_container.run_agent.clear_history()

    duration_ms = int((time.monotonic() - t0) * 1000)
    logger.info("clear_history done agent=%s duration_ms=%d", agent_id, duration_ms)

    return Response(status_code=204)
