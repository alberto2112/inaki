"""
Admin REST server — instancia global del daemon.

Expone endpoints de administración (health, scheduler reload, inspect, consolidate)
en un puerto separado de los per-agent REST servers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI

from adapters.inbound.rest.admin.routers.admin import router
from adapters.inbound.rest.admin.routers.chat import router as chat_router

if TYPE_CHECKING:
    from infrastructure.container import AppContainer

logger = logging.getLogger(__name__)


def create_admin_app(
    app_container: "AppContainer",
    admin_auth_key: str | None,
) -> FastAPI:
    """Crea la instancia FastAPI del admin server."""
    app = FastAPI(
        title="Iñaki — Admin",
        description="Admin server para gestión del daemon",
        version="2.0.0",
    )

    app.state.app_container = app_container
    app.state.admin_auth_key = admin_auth_key

    app.include_router(router)
    app.include_router(chat_router, prefix="/admin/chat")

    return app
