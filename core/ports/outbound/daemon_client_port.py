"""Port para comunicación con el daemon desde el CLI."""

from __future__ import annotations

from typing import Any, Protocol


class IDaemonClient(Protocol):
    """Interfaz para comunicarse con el daemon de Iñaki vía HTTP."""

    def health(self) -> bool:
        """Verifica si el daemon está corriendo. Retorna False si no responde."""
        ...

    def scheduler_reload(self) -> bool:
        """Pide al daemon que recargue la caché del scheduler. Retorna True si OK."""
        ...

    def inspect(self, agent_id: str, mensaje: str) -> dict[str, Any]:
        """Ejecuta inspect del pipeline RAG en el daemon."""
        ...

    def consolidate(self, agent_id: str | None = None) -> dict[str, Any]:
        """Ejecuta consolidación de memoria en el daemon."""
        ...
