"""Contrato de una operación de la tool ``scheduler``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from inaki.kernel.ports.outbound.tool_port import ToolResult
from inaki.scheduler.tools._context import Contexto


class Operacion(ABC):
    """Una operación = un objeto chico con su validación y su salida.

    Recibe el ``Contexto`` (colaboradores de la tool) al construirse y los
    ``params`` crudos del LLM al ejecutar. Devuelve siempre un ``ToolResult``:
    los errores esperables (validación, tarea inexistente, guardrails del use
    case) salen como ``success=False`` con mensaje accionable; lo inesperado lo
    captura la fachada.
    """

    def __init__(self, ctx: Contexto) -> None:
        self._ctx = ctx

    @abstractmethod
    async def ejecutar(self, params: dict[str, Any]) -> ToolResult: ...
