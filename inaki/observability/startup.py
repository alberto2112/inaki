"""Arranque observable: un evento estructurado por recurso que el wiring resuelve.

Generaliza lo que pedían ``broadcast-arranque-observable`` y ``config-falla-ruidoso``:
cada recurso que el composition root construye, saltea o no puede construir
deja UNA línea con el mismo formato, filtrable por ``event=startup.resource``.
"""

from __future__ import annotations

import logging
from typing import Literal

StartupStatus = Literal["ok", "skip", "error"]

_NIVEL: dict[StartupStatus, int] = {
    "ok": logging.INFO,
    "skip": logging.INFO,
    "error": logging.ERROR,
}


def startup_event(
    logger: logging.Logger,
    resource: str,
    *,
    status: StartupStatus,
    agent: str | None = None,
    reason: str | None = None,
    **fields: object,
) -> None:
    """Loguea ``[startup] <resource>`` con el detalle en campos ``extra``.

    Args:
        logger: logger del módulo que hace el wiring.
        resource: nombre del recurso (``broadcast``, ``telegram_bot``, ``scheduler``...).
        status: ``ok`` construido, ``skip`` no aplica por config, ``error`` falló.
        agent: id del agente dueño, si el recurso es per-agente.
        reason: por qué se salteó o falló (obligatorio moralmente para ``skip``/``error``).
        **fields: datos útiles del recurso (host, port, role...).
    """
    extra: dict[str, object] = {"event": "startup.resource", "resource": resource, "status": status}
    if agent is not None:
        extra["agent"] = agent
    if reason is not None:
        extra["reason"] = reason
    extra.update(fields)
    logger.log(_NIVEL[status], "[startup] %s", resource, extra=extra)
