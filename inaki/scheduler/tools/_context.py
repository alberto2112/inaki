"""Lo que toda operación de la tool ``scheduler`` necesita para correr.

Las operaciones no reciben la tool: reciben este contexto (los colaboradores
inyectados en la construcción de la tool) y devuelven ``ToolResult``. Así cada
operación se prueba sola y ninguna sabe cómo se despacha.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.ports.outbound.tool_port import ToolResult
from inaki.shared.channel_context import ChannelContext

if TYPE_CHECKING:
    from inaki.scheduler.ports.use_case import IManualTaskRunner, ISchedulerUseCase

TOOL_NAME = "scheduler"


@dataclass(frozen=True)
class Contexto:
    """Colaboradores de la tool, compartidos por todas las operaciones.

    ``uc`` es el CRUD (``ISchedulerUseCase``); ``runner`` el motor de ejecución
    harness-global (``IManualTaskRunner``), segregado porque disparar un trigger
    necesita los puertos de dispatch, no solo el repo. ``agent_id`` es el agente
    que opera (se inyecta como ``created_by``, nunca viene del LLM) y
    ``get_channel_context`` resuelve la conversación en curso, si la hay.
    """

    uc: ISchedulerUseCase
    runner: IManualTaskRunner
    agent_id: str
    user_timezone: str
    get_channel_context: Callable[[], ChannelContext | None]


def error(message: str) -> ToolResult:
    return ToolResult(tool_name=TOOL_NAME, output=message, success=False, error=message)


def ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(tool_name=TOOL_NAME, output=json.dumps(payload), success=True)
