"""Tests para el helper de autenticación admin extraído a deps.py.

Cubre tarea 4.1 (TEST):
  - Sin X-Admin-Key → HTTPException 401
  - X-Admin-Key incorrecta → HTTPException 401
  - Sin auth_key configurada en el server → HTTPException 403
  - X-Admin-Key correcta → no levanta excepción (retorna None)
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from fastapi import HTTPException

from adapters.inbound.rest.admin.routers.deps import check_admin_auth


def _mock_request(header: str | None = None, auth_key: str | None = "clave-secreta") -> MagicMock:
    """Crea un request mock con el header y la auth_key configurados."""
    request = MagicMock()
    request.app.state.admin_auth_key = auth_key
    if header is None:
        request.headers.get.return_value = None
    else:
        request.headers.get.return_value = header
    return request


def test_rechaza_request_sin_header() -> None:
    """Sin X-Admin-Key → HTTPException 401."""
    request = _mock_request(header=None)
    with pytest.raises(HTTPException) as exc_info:
        check_admin_auth(request)
    assert exc_info.value.status_code == 401


def test_rechaza_header_incorrecto() -> None:
    """Header incorrecto → HTTPException 401."""
    request = _mock_request(header="clave-incorrecta")
    with pytest.raises(HTTPException) as exc_info:
        check_admin_auth(request)
    assert exc_info.value.status_code == 401


def test_sin_auth_key_configurada_devuelve_403() -> None:
    """Sin auth_key configurada en server (None) → HTTPException 403 (fail-closed)."""
    request = _mock_request(header="cualquier-cosa", auth_key=None)
    with pytest.raises(HTTPException) as exc_info:
        check_admin_auth(request)
    assert exc_info.value.status_code == 403


def test_acepta_header_correcto() -> None:
    """Header correcto → no levanta excepción, retorna None."""
    request = _mock_request(header="clave-secreta", auth_key="clave-secreta")
    resultado = check_admin_auth(request)
    assert resultado is None
