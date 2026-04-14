"""
Adapter HTTP para comunicación con el daemon de Iñaki.

Usa httpx sync (sin event loop) — pensado para uso desde el CLI de Typer.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.domain.errors import (
    DaemonClientError,
    DaemonNotRunningError,
    DaemonTimeoutError,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0
_LONG_TIMEOUT = 30.0


class DaemonClient:
    """Cliente HTTP sync para comunicarse con el admin server del daemon."""

    def __init__(self, admin_base_url: str, auth_key: str | None) -> None:
        self._base_url = admin_base_url.rstrip("/")
        self._auth_key = auth_key

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
    # Helpers
    # ------------------------------------------------------------------

    def _post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
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
            raise DaemonClientError(status_code=resp.status_code, detail=resp.text)

        return resp.json()
