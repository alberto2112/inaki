"""Dependencias compartidas para los routers del admin REST server."""

from __future__ import annotations

from fastapi import HTTPException, Request


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
    if not provista or provista != auth_key:
        raise HTTPException(status_code=401, detail="X-Admin-Key inválida o ausente")
