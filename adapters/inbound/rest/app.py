"""
REST API FastAPI — una instancia por agente.

Cada agente con canal 'rest' definido levanta su propia instancia FastAPI en su propio puerto.
Auth: header X-API-Key verificado contra channels.rest.auth_key.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from adapters.inbound.rest.routers.agents import router
from infrastructure.config import AgentConfig
from infrastructure.container import AgentContainer

logger = logging.getLogger(__name__)


def _auth_middleware(auth_key: str | None):
    """Middleware factory para autenticación via X-API-Key."""

    async def middleware(request: Request, call_next):
        if auth_key:
            provided = request.headers.get("X-API-Key")
            if provided != auth_key:
                raise HTTPException(status_code=401, detail="X-API-Key inválida o ausente")
        return await call_next(request)

    return middleware


def create_agent_app(agent_cfg: AgentConfig, container: AgentContainer) -> FastAPI:
    """Crea una instancia FastAPI configurada para un agente específico."""
    rest_cfg = agent_cfg.channels.get("rest", {})
    auth_key = rest_cfg.get("auth_key")

    app = FastAPI(
        title=f"Iñaki — {agent_cfg.name}",
        description=agent_cfg.description,
        version="2.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if auth_key:
        from starlette.middleware.base import BaseHTTPMiddleware

        app.add_middleware(BaseHTTPMiddleware, dispatch=_auth_middleware(auth_key))

    # Inyectar el container en el state de la app
    app.state.container = container

    app.include_router(router)

    return app
