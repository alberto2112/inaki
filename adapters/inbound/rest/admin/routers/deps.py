"""Dependencias compartidas para los routers del admin REST server."""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from infrastructure.container import AgentContainer


def resolver_agente(request: Request, agent_id: str) -> "AgentContainer":
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


def check_admin_auth(request: Request) -> None:
    """Verifica X-Admin-Key contra la key configurada en el server.

    - auth_key es None → 403 (fail-closed: sin key configurada, no se permite acceso)
    - Header ausente o incorrecto → 401
    """
    auth_key: str | None = request.app.state.admin_auth_key
    if auth_key is None:
        raise HTTPException(
            status_code=403,
            detail="Admin auth_key no configurada. Agregala en global.secrets.yaml.",
        )
    provista = request.headers.get("X-Admin-Key")
    # compare_digest: comparación en tiempo constante — un `!=` corta en el
    # primer byte distinto y permite recuperar la key midiendo latencias.
    if not provista or not hmac.compare_digest(provista, auth_key):
        raise HTTPException(status_code=401, detail="X-Admin-Key inválida o ausente")
