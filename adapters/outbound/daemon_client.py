"""
Adapter HTTP para comunicación con el daemon de Iñaki.

Usa httpx sync (sin event loop) — pensado para uso desde el CLI de Typer.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.domain.errors import (
    DaemonAuthError,
    DaemonClientError,
    DaemonNotRunningError,
    DaemonTimeoutError,
    UnknownAgentError,
)
from core.domain.value_objects.chat_turn_result import ChatTurnResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0
_LONG_TIMEOUT = 30.0
_CHAT_TIMEOUT = 300.0


class DaemonClient:
    """Cliente HTTP sync para comunicarse con el admin server del daemon."""

    def __init__(
        self,
        admin_base_url: str,
        auth_key: str | None,
        chat_timeout: float = _CHAT_TIMEOUT,
    ) -> None:
        self._base_url = admin_base_url.rstrip("/")
        self._auth_key = auth_key
        self._chat_timeout = chat_timeout

    def _headers(self) -> dict[str, str]:
        if self._auth_key:
            return {"X-Admin-Key": self._auth_key}
        return {}

    # ------------------------------------------------------------------
    # health — no levanta excepciones, solo True/False
    # ------------------------------------------------------------------

    def health(self) -> bool:
        try:
            resp = httpx.get(f"{self._base_url}/health", timeout=_DEFAULT_TIMEOUT)
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    # ------------------------------------------------------------------
    # scheduler_reload — silencioso ante fallos de conexión
    # ------------------------------------------------------------------

    def scheduler_reload(self) -> bool:
        try:
            resp = httpx.post(
                f"{self._base_url}/scheduler/reload",
                headers=self._headers(),
                timeout=_DEFAULT_TIMEOUT,
            )
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    # ------------------------------------------------------------------
    # inspect
    # ------------------------------------------------------------------

    def inspect(self, agent_id: str, mensaje: str) -> dict[str, Any]:
        return self._post(
            "/inspect",
            json={"agent_id": agent_id, "mensaje": mensaje},
            timeout=_DEFAULT_TIMEOUT,
            error_map=self._CHAT_ERROR_MAP,
            agent_id=agent_id,
        )

    # ------------------------------------------------------------------
    # consolidate
    # ------------------------------------------------------------------

    def consolidate(self, agent_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if agent_id:
            body["agent_id"] = agent_id
        return self._post("/consolidate", json=body, timeout=_LONG_TIMEOUT)

    # ------------------------------------------------------------------
    # chat_turn — turno de conversación con el agente (Design §B2)
    # ------------------------------------------------------------------

    def chat_turn(self, agent_id: str, session_id: str, mensaje: str) -> ChatTurnResult:
        """Envía un turno de chat al daemon y retorna el resultado completo.

        Incluye la respuesta final y los bloques intermedios emitidos durante
        el turno (texto que acompaña tool_calls). El campo ``intermediates``
        puede estar ausente en daemons antiguos — lo tratamos como lista vacía.

        Raises:
            DaemonNotRunningError: si el daemon no es alcanzable.
            DaemonTimeoutError: si la respuesta supera el timeout configurado.
            UnknownAgentError: si agent_id no existe en el daemon (HTTP 404).
            DaemonAuthError: si la autenticación falla (HTTP 401/403).
            DaemonClientError: para otros errores HTTP del daemon.
        """
        data = self._post(
            "/admin/chat/turn",
            json={"agent_id": agent_id, "session_id": session_id, "message": mensaje},
            timeout=self._chat_timeout,
            error_map=self._CHAT_ERROR_MAP,
            agent_id=agent_id,
        )
        return ChatTurnResult(
            reply=data["reply"],
            intermediates=list(data.get("intermediates") or []),
        )

    # ------------------------------------------------------------------
    # chat_history — historial de mensajes del agente (Design §B2)
    # ------------------------------------------------------------------

    def chat_history(self, agent_id: str) -> list[dict[str, str]]:
        """Obtiene el historial de mensajes del agente desde el daemon.

        Retorna lista de dicts con claves 'role' y 'content'.

        Raises:
            DaemonNotRunningError: si el daemon no es alcanzable.
            UnknownAgentError: si agent_id no existe en el daemon (HTTP 404).
            DaemonAuthError: si la autenticación falla (HTTP 401/403).
            DaemonClientError: para otros errores HTTP del daemon.
        """
        data = self._get(
            "/admin/chat/history",
            params={"agent_id": agent_id},
            error_map=self._CHAT_ERROR_MAP,
            agent_id=agent_id,
        )
        return data["messages"]

    # ------------------------------------------------------------------
    # list_agents — lista agentes registrados en el daemon (Correction 2)
    # ------------------------------------------------------------------

    def list_agents(self) -> list[str]:
        """Lista los agentes registrados en el daemon.

        Raises:
            DaemonNotRunningError: si el daemon no es alcanzable.
            DaemonAuthError: si la autenticación falla (HTTP 401/403).
            DaemonClientError: para otros errores HTTP del daemon.
        """
        data = self._get(
            "/admin/agents",
            params=None,
            error_map=self._CHAT_ERROR_MAP,
            agent_id="__list_agents__",
        )
        return data.get("agents", [])

    # ------------------------------------------------------------------
    # chat_clear — limpia el historial del agente (Design §B2)
    # ------------------------------------------------------------------

    def chat_clear(self, agent_id: str) -> None:
        """Limpia el historial del agente en el daemon.

        Raises:
            DaemonNotRunningError: si el daemon no es alcanzable.
            UnknownAgentError: si agent_id no existe en el daemon (HTTP 404).
            DaemonAuthError: si la autenticación falla (HTTP 401/403).
            DaemonClientError: para otros errores HTTP del daemon.
        """
        self._delete(
            "/admin/chat/history",
            params={"agent_id": agent_id},
            error_map=self._CHAT_ERROR_MAP,
            agent_id=agent_id,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _map_error(
        self,
        status_code: int,
        detail: str,
        error_map: dict[int, type[Exception]] | None = None,
        agent_id: str = "",
    ) -> None:
        """Mapea errores HTTP a excepciones de dominio.

        Si `error_map` es None (comportamiento legacy), levanta DaemonClientError genérico.
        Si `error_map` está presente, busca el status_code en el dict y levanta la excepción
        correspondiente. Fallback: DaemonClientError genérico si el code no está en el mapa.

        `agent_id` se usa solo para construir UnknownAgentError (si aparece en el mapa).
        """
        if error_map is None:
            raise DaemonClientError(status_code=status_code, detail=detail)

        exc_cls = error_map.get(status_code)
        if exc_cls is None:
            raise DaemonClientError(status_code=status_code, detail=detail)

        # UnknownAgentError requiere agent_id como argumento posicional
        if exc_cls is UnknownAgentError:
            raise UnknownAgentError(agent_id)
        # DaemonAuthError necesita el status_code real (401 o 403)
        if exc_cls is DaemonAuthError:
            raise DaemonAuthError(status_code=status_code)
        raise exc_cls()

    # Mapa de errores estándar para endpoints de chat
    _CHAT_ERROR_MAP: dict[int, type[Exception]] = {
        404: UnknownAgentError,
        401: DaemonAuthError,
        403: DaemonAuthError,
    }

    def _post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        error_map: dict[int, type[Exception]] | None = None,
        agent_id: str = "",
    ) -> dict[str, Any]:
        """Helper POST unificado.

        Sin `error_map` (default): comportamiento legacy — levanta DaemonClientError genérico.
        Con `error_map`: usa el mapa para errores HTTP (ver _map_error).
        """
        try:
            resp = httpx.post(
                f"{self._base_url}{path}",
                json=json,
                headers=self._headers(),
                timeout=timeout,
            )
        except httpx.ConnectError:
            raise DaemonNotRunningError()
        except httpx.TimeoutException:
            raise DaemonTimeoutError()

        if resp.status_code >= 400:
            self._map_error(resp.status_code, resp.text, error_map=error_map, agent_id=agent_id)

        return resp.json()

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = _LONG_TIMEOUT,
        error_map: dict[int, type[Exception]] | None = None,
        agent_id: str = "",
    ) -> dict[str, Any]:
        """Helper GET unificado para el admin server."""
        try:
            resp = httpx.get(
                f"{self._base_url}{path}",
                params=params,
                headers=self._headers(),
                timeout=timeout,
            )
        except httpx.ConnectError:
            raise DaemonNotRunningError()
        except httpx.TimeoutException:
            raise DaemonTimeoutError()

        if resp.status_code >= 400:
            self._map_error(resp.status_code, resp.text, error_map=error_map, agent_id=agent_id)

        return resp.json()

    def _delete(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = _LONG_TIMEOUT,
        error_map: dict[int, type[Exception]] | None = None,
        agent_id: str = "",
    ) -> None:
        """Helper DELETE unificado para el admin server."""
        try:
            resp = httpx.delete(
                f"{self._base_url}{path}",
                params=params,
                headers=self._headers(),
                timeout=timeout,
            )
        except httpx.ConnectError:
            raise DaemonNotRunningError()
        except httpx.TimeoutException:
            raise DaemonTimeoutError()

        if resp.status_code >= 400:
            self._map_error(resp.status_code, resp.text, error_map=error_map, agent_id=agent_id)
