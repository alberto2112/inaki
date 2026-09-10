"""
Admin REST server — instancia global del daemon.

Expone endpoints de administración (health, scheduler reload, inspect, consolidate)
en un puerto separado de los per-agent REST servers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from inaki.channels.rest.routers.admin import router
from inaki.channels.rest.routers.chat import router as chat_router
from inaki.channels.rest.routers.config import router as config_router
from inaki.channels.rest.routers.tools import router as tools_router

if TYPE_CHECKING:
    from inaki.channels.rest.ports import AdminHarness

logger = logging.getLogger(__name__)


def create_admin_app(
    app_container: "AdminHarness",
    admin_auth_key: str | None,
    config_web: Any | None = None,
) -> FastAPI:
    """Crea la instancia FastAPI del admin server.

    ``config_web`` (un ``ConfigWeb`` de ``inaki.config.wiring``) enciende ``/admin/config``;
    sin él, esos endpoints responden 503."""
    app = FastAPI(
        title="Inaki — Admin",
        description="Admin server para gestión del daemon",
        version="2.0.0",
    )

    app.state.app_container = app_container
    app.state.admin_auth_key = admin_auth_key
    app.state.config_web = config_web

    app.include_router(router)
    app.include_router(chat_router, prefix="/admin/chat")
    app.include_router(tools_router)
    app.include_router(config_router)

    return app
