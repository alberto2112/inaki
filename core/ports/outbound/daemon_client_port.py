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

    def chat_turn(self, agent_id: str, session_id: str, mensaje: str) -> str:
        """Envía un turno de chat al daemon y retorna la respuesta del agente.

        Raises:
            DaemonNotRunningError: si el daemon no es alcanzable.
            DaemonTimeoutError: si la respuesta supera el timeout configurado.
            UnknownAgentError: si agent_id no existe en el daemon (HTTP 404).
            DaemonAuthError: si la autenticación falla (HTTP 401/403).
            DaemonClientError: para otros errores HTTP del daemon.
        """
        ...

    def chat_history(self, agent_id: str) -> list[dict[str, str]]:
        """Obtiene el historial de mensajes del agente desde el daemon.

        Retorna lista de dicts con claves 'role' y 'content'.

        Raises:
            DaemonNotRunningError: si el daemon no es alcanzable.
            UnknownAgentError: si agent_id no existe en el daemon (HTTP 404).
            DaemonAuthError: si la autenticación falla (HTTP 401/403).
            DaemonClientError: para otros errores HTTP del daemon.
        """
        ...

    def chat_clear(self, agent_id: str) -> None:
        """Limpia el historial del agente en el daemon.

        Raises:
            DaemonNotRunningError: si el daemon no es alcanzable.
            UnknownAgentError: si agent_id no existe en el daemon (HTTP 404).
            DaemonAuthError: si la autenticación falla (HTTP 401/403).
            DaemonClientError: para otros errores HTTP del daemon.
        """
        ...

    def list_agents(self) -> list[str]:
        """Lista los agentes registrados en el daemon.

        Retorna lista de IDs de agentes disponibles.

        Raises:
            DaemonNotRunningError: si el daemon no es alcanzable.
            DaemonAuthError: si la autenticación falla (HTTP 401/403).
            DaemonClientError: para otros errores HTTP del daemon.
        """
        ...
